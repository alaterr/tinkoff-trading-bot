from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional

from app.client import client as broker_client
from app.instruments_config.models import GlobalExecutionConfig, GlobalRiskConfig, InstrumentConfig
from app.settings import settings
from app.strategies.base import BaseStrategy
from app.strategies.intraday.vwap_momentum import VwapMomentumConfig, VwapMomentumStrategy, _to_decimal
from core.data.candles import CandleRepository
from core.futures.rollover import should_rollover
from core.models.entities import OrderIntent, Signal, SignalType
from core.oms.order_manager import OrderManager
from core.risk.gate import RiskGate
from core.utils.time import moscow_tz, start_of_day, start_of_week
from storage.state_store import StateStore

logger = logging.getLogger(__name__)


def _mv_to_decimal(mv) -> Optional[Decimal]:
    try:
        return Decimal(mv.units) + (Decimal(mv.nano) / Decimal(1_000_000_000))
    except Exception:  # noqa: BLE001
        return None


def _norm_risk_pct(v: Decimal) -> Decimal:
    # Accept both 0.003 (0.3%) and 0.3 (0.3%) styles.
    return v / Decimal("100") if v >= Decimal("0.1") else v


@dataclass(frozen=True)
class VwapMomentumRunnerConfig:
    poll_seconds: int = 10


class VwapMomentumRunner(BaseStrategy):
    """
    Broker-backed intraday runner for VwapMomentumStrategy:
    - fetch last closed 1m/5m candles
    - check SL/TP (close-based) stored in StateStore
    - generate entry/exit signal via pure strategy
    - RiskGate sizes entry based on signal.risk_stop + signal.risk_per_trade_pct
    - OMS places market orders (idempotent per bar timestamp)
    """

    def __init__(
        self,
        figi: str,
        *,
        instrument_config: InstrumentConfig,
        global_risk: GlobalRiskConfig,
        global_execution: GlobalExecutionConfig,
        strategy_name: str,
        strategy_params: dict[str, Any],
        runner_config: VwapMomentumRunnerConfig = VwapMomentumRunnerConfig(),
    ):
        self.figi = figi
        self.instrument_config = instrument_config
        self.global_risk = global_risk
        self.global_execution = global_execution
        self.strategy_name = strategy_name
        self.runner_config = runner_config

        # Permissive parsing (config json may provide ints/floats/strings)
        p = dict(strategy_params or {})
        cfg0 = VwapMomentumConfig()
        self.cfg = VwapMomentumConfig(
            timeframe=str(p.get("timeframe", cfg0.timeframe)),
            vwap_period=int(p.get("vwap_period", cfg0.vwap_period)),
            vwap_window=(int(p["vwap_window"]) if p.get("vwap_window") is not None else cfg0.vwap_window),
            ema_fast=int(p.get("ema_fast", cfg0.ema_fast)),
            ema_slow=int(p.get("ema_slow", cfg0.ema_slow)),
            require_fast_slope=bool(p.get("require_fast_slope", cfg0.require_fast_slope)),
            trend_timeframe=(str(p["trend_timeframe"]) if p.get("trend_timeframe") is not None else cfg0.trend_timeframe),
            trend_ema_fast=int(p.get("trend_ema_fast", cfg0.trend_ema_fast)),
            trend_ema_slow=int(p.get("trend_ema_slow", cfg0.trend_ema_slow)),
            atr_period=int(p.get("atr_period", cfg0.atr_period)),
            sl_points=_to_decimal(p.get("sl_points", cfg0.sl_points)),
            tp_points=_to_decimal(p.get("tp_points", cfg0.tp_points)),
            atr_sl_mult=_to_decimal(p.get("atr_sl_mult", cfg0.atr_sl_mult)),
            atr_tp_mult=_to_decimal(p.get("atr_tp_mult", cfg0.atr_tp_mult)),
            atr_trail_mult=_to_decimal(p.get("atr_trail_mult", cfg0.atr_trail_mult)),
            risk_per_trade_pct=_to_decimal(p.get("risk_per_trade_pct", cfg0.risk_per_trade_pct)) or cfg0.risk_per_trade_pct,
            volume_window=int(p.get("volume_window", cfg0.volume_window)),
            min_volume_ratio=_to_decimal(p.get("min_volume_ratio", cfg0.min_volume_ratio)) or cfg0.min_volume_ratio,
            trade_sessions=tuple(tuple(x) for x in (p.get("trade_sessions") or cfg0.trade_sessions)),
            cooldown_bars=int(p.get("cooldown_bars", cfg0.cooldown_bars)),
            exit_before_session_end_minutes=int(
                p.get("exit_before_session_end_minutes", cfg0.exit_before_session_end_minutes)
            ),
        )

        self.store = StateStore(db_path="state.db")
        self.data = CandleRepository(broker=broker_client)
        self.risk = RiskGate(global_risk=global_risk, kill_switch_path=global_risk.kill_switch_file)
        self.oms: Optional[OrderManager] = None
        self.account_id: Optional[str] = settings.account_id

        # Futures contract spec (best-effort)
        self._tick_size: Optional[Decimal] = None
        self._price_multiplier: Decimal = Decimal("1")  # currency per +1.0 price move per 1 contract
        self._lot_size: int = 1

        self.strategy = VwapMomentumStrategy(figi=figi, config=self.cfg)

        # Lookback for indicators
        if self.cfg.vwap_window is not None:
            vwap_bars = int(self.cfg.vwap_window)
        else:
            bm = 1 if self.cfg.timeframe.lower().strip() == "1min" else 5
            vwap_bars = max(1, int((int(self.cfg.vwap_period) + bm - 1) // bm))
        self.lookback = max(vwap_bars, self.cfg.ema_slow, self.cfg.atr_period, self.cfg.volume_window) + 10
        self._trend_lookback = max(self.cfg.trend_ema_fast, self.cfg.trend_ema_slow) + 10

    def _job_id(self) -> str:
        return f"{self.figi}|{self.strategy_name}"

    async def _ensure_account_id(self) -> Optional[str]:
        if self.account_id:
            return self.account_id
        try:
            self.account_id = (await broker_client.get_accounts()).accounts[0].id
            settings.account_id = self.account_id
            return self.account_id
        except Exception:  # noqa: BLE001
            return None

    async def _get_portfolio(self):
        assert self.account_id is not None
        return await broker_client.get_portfolio(account_id=self.account_id)

    def _get_position_qty(self, portfolio) -> int:
        for pos in getattr(portfolio, "positions", []):
            if getattr(pos, "figi", None) == self.figi:
                q = getattr(pos, "quantity", None)
                if q is None:
                    return 0
                try:
                    return int(Decimal(q.units) + (Decimal(q.nano) / Decimal(1_000_000_000)))
                except Exception:  # noqa: BLE001
                    try:
                        return int(q)
                    except Exception:  # noqa: BLE001
                        return 0
        return 0

    @staticmethod
    def _pos_qty_from_position(pos) -> int:
        q = getattr(pos, "quantity", None)
        if q is None:
            return 0
        try:
            return int(Decimal(q.units) + (Decimal(q.nano) / Decimal(1_000_000_000)))
        except Exception:  # noqa: BLE001
            try:
                return int(q)
            except Exception:  # noqa: BLE001
                return 0

    def _get_open_positions_total(self, portfolio) -> int:
        n = 0
        for pos in getattr(portfolio, "positions", []):
            if self._pos_qty_from_position(pos) != 0:
                n += 1
        return n

    def _equity_rub(self, portfolio) -> Optional[Decimal]:
        mv = getattr(portfolio, "total_amount_portfolio", None) or getattr(portfolio, "total_amount_currencies", None)
        return _mv_to_decimal(mv) if mv is not None else None

    async def _ensure_contract_spec(self) -> None:
        """
        Best-effort futures spec for:
        - tick_size (min_price_increment)
        - price_multiplier = tick_value / tick_size (currency per +1.0 price move)
        - lot_size
        """
        try:
            try:
                from t_tech.invest.grpc.instruments_pb2 import INSTRUMENT_ID_TYPE_FIGI

                id_type = INSTRUMENT_ID_TYPE_FIGI
            except Exception:  # noqa: BLE001
                id_type = 1
            resp = await broker_client.get_instrument(id_type=id_type, id=self.figi)
            inst = getattr(resp, "instrument", None)
            if inst is None:
                return
            self._lot_size = max(1, int(getattr(inst, "lot", 1) or 1))
            mpi = getattr(inst, "min_price_increment", None)
            mpia = getattr(inst, "min_price_increment_amount", None)
            if mpi is None or mpia is None:
                return
            tick = _mv_to_decimal(mpi)  # quotation-like
            tick_val = _mv_to_decimal(mpia)  # money-like
            if tick is not None and tick > 0:
                self._tick_size = tick
                if tick_val is not None and tick_val > 0:
                    self._price_multiplier = tick_val / tick
        except Exception:  # noqa: BLE001
            return

    async def _rollover_if_needed(self, portfolio) -> bool:
        if self.instrument_config.instrument_type != "futures":
            return False
        pos_qty = self._get_position_qty(portfolio)
        if pos_qty == 0:
            return False
        expiry_dt = None
        try:
            try:
                from t_tech.invest.grpc.instruments_pb2 import INSTRUMENT_ID_TYPE_FIGI

                id_type = INSTRUMENT_ID_TYPE_FIGI
            except Exception:  # noqa: BLE001
                id_type = 1
            resp = await broker_client.get_instrument(id_type=id_type, id=self.figi)
            inst = getattr(resp, "instrument", None)
            expiry_dt = getattr(inst, "expiration_date", None) if inst is not None else None
        except Exception:  # noqa: BLE001
            expiry_dt = None
        expiry_date = expiry_dt.date() if expiry_dt else None
        dec = should_rollover(instrument=self.instrument_config, expiry_date=expiry_date)
        if not dec.should_roll or not dec.next_figi:
            return False
        assert self.oms is not None
        from core.models.entities import Side as SideEnum

        close_intent = OrderIntent(
            strategy_name="rollover",
            figi=self.figi,
            side=SideEnum.SELL if pos_qty > 0 else SideEnum.BUY,
            intended_qty=abs(pos_qty),
            ts=datetime.now(timezone.utc),
        )
        await self.oms.place_market_order(close_intent)
        open_intent = OrderIntent(
            strategy_name="rollover",
            figi=dec.next_figi,
            side=SideEnum.BUY if pos_qty > 0 else SideEnum.SELL,
            intended_qty=abs(pos_qty),
            ts=datetime.now(timezone.utc),
        )
        await self.oms.place_market_order(open_intent)
        logger.info("rollover %s -> %s qty=%s", self.figi, dec.next_figi, abs(pos_qty))
        return True

    def _risk_stop_rub(self, *, atr_value: Optional[Decimal]) -> Optional[Decimal]:
        """
        Per-contract stop risk in RUB (best-effort):
        - fixed points: sl_points * tick_size * price_multiplier * lot
        - ATR-based: (atr * atr_sl_mult) * price_multiplier * lot
        """
        lot = Decimal(self._lot_size)
        pm = self._price_multiplier
        if self.cfg.sl_points is not None and self.cfg.sl_points > 0:
            if self._tick_size is not None and self._tick_size > 0:
                return (self.cfg.sl_points * self._tick_size) * pm * lot
            # fallback: treat points as price units
            return self.cfg.sl_points * pm * lot
        if self.cfg.atr_sl_mult is not None and self.cfg.atr_sl_mult > 0 and atr_value is not None and atr_value > 0:
            return (atr_value * self.cfg.atr_sl_mult) * pm * lot
        return None

    def _set_levels(self, *, entry_price: Decimal, atr_value: Optional[Decimal], direction: int) -> None:
        """
        Store absolute stop/tp prices (close-based evaluation).
        Points are interpreted as ticks if tick_size is available.
        """
        if direction == 0:
            return
        stop_dist = None
        tp_dist = None
        if self.cfg.sl_points is not None and self.cfg.sl_points > 0:
            if self._tick_size is not None and self._tick_size > 0:
                stop_dist = self.cfg.sl_points * self._tick_size
            else:
                stop_dist = self.cfg.sl_points
        elif self.cfg.atr_sl_mult is not None and self.cfg.atr_sl_mult > 0 and atr_value is not None and atr_value > 0:
            stop_dist = atr_value * self.cfg.atr_sl_mult

        if self.cfg.tp_points is not None and self.cfg.tp_points > 0:
            if self._tick_size is not None and self._tick_size > 0:
                tp_dist = self.cfg.tp_points * self._tick_size
            else:
                tp_dist = self.cfg.tp_points
        elif self.cfg.atr_tp_mult is not None and self.cfg.atr_tp_mult > 0 and atr_value is not None and atr_value > 0:
            tp_dist = atr_value * self.cfg.atr_tp_mult

        if stop_dist is None or tp_dist is None:
            return

        stop = entry_price - stop_dist if direction > 0 else entry_price + stop_dist
        tp = entry_price + tp_dist if direction > 0 else entry_price - tp_dist

        self.store.set_kv(strategy_name=self.strategy_name, figi=self.figi, key="entry_price", value=str(entry_price))
        self.store.set_kv(strategy_name=self.strategy_name, figi=self.figi, key="stop_price", value=str(stop))
        self.store.set_kv(strategy_name=self.strategy_name, figi=self.figi, key="tp_price", value=str(tp))
        if direction > 0:
            self.store.set_kv(strategy_name=self.strategy_name, figi=self.figi, key="peak_price", value=str(entry_price))
            self.store.delete_kv(strategy_name=self.strategy_name, figi=self.figi, key="trough_price")
        else:
            self.store.set_kv(strategy_name=self.strategy_name, figi=self.figi, key="trough_price", value=str(entry_price))
            self.store.delete_kv(strategy_name=self.strategy_name, figi=self.figi, key="peak_price")

    def _clear_levels(self) -> None:
        for k in ("entry_price", "stop_price", "tp_price", "peak_price", "trough_price"):
            self.store.delete_kv(strategy_name=self.strategy_name, figi=self.figi, key=k)

    def _update_trailing_stop(self, *, current_qty: int, last_close: Decimal, atr_value: Optional[Decimal]) -> None:
        """
        ATR trailing stop (close-based updates, deterministic):
        - long: peak = max(peak, close); trail = peak - atr_trail_mult*ATR; stop = max(stop, trail)
        - short: trough = min(trough, close); trail = trough + atr_trail_mult*ATR; stop = min(stop, trail)
        """
        if current_qty == 0:
            return
        if self.cfg.atr_trail_mult is None or self.cfg.atr_trail_mult <= 0:
            return
        if atr_value is None or atr_value <= 0:
            return

        stop_s = self.store.get_kv(strategy_name=self.strategy_name, figi=self.figi, key="stop_price")
        stop = _to_decimal(stop_s)
        dist = atr_value * self.cfg.atr_trail_mult
        if current_qty > 0:
            peak_s = self.store.get_kv(strategy_name=self.strategy_name, figi=self.figi, key="peak_price")
            peak = _to_decimal(peak_s) or last_close
            peak = max(peak, last_close)
            self.store.set_kv(strategy_name=self.strategy_name, figi=self.figi, key="peak_price", value=str(peak))
            trail = peak - dist
            if stop is None or trail > stop:
                self.store.set_kv(strategy_name=self.strategy_name, figi=self.figi, key="stop_price", value=str(trail))
        else:
            trough_s = self.store.get_kv(strategy_name=self.strategy_name, figi=self.figi, key="trough_price")
            trough = _to_decimal(trough_s) or last_close
            trough = min(trough, last_close)
            self.store.set_kv(strategy_name=self.strategy_name, figi=self.figi, key="trough_price", value=str(trough))
            trail = trough + dist
            if stop is None or trail < stop:
                self.store.set_kv(strategy_name=self.strategy_name, figi=self.figi, key="stop_price", value=str(trail))

    async def _get_trend_direction(self) -> int:
        """
        Optional higher timeframe trend filter (1h/4h EMA).
        Returns +1/-1/0.
        """
        tf = (self.cfg.trend_timeframe or "").lower().strip()
        if tf not in {"1h", "4h"}:
            return 0
        res = await self.data.fetch_last_intraday(figi=self.figi, n=self._trend_lookback, timeframe=tf)
        if not res.candles:
            return 0
        return VwapMomentumStrategy.trend_dir_from_candles(
            candles=res.candles,
            ema_fast_p=int(self.cfg.trend_ema_fast),
            ema_slow_p=int(self.cfg.trend_ema_slow),
        )
    def _maybe_exit_by_levels(self, *, current_qty: int, close: Decimal) -> bool:
        if current_qty == 0:
            return False
        stop_s = self.store.get_kv(strategy_name=self.strategy_name, figi=self.figi, key="stop_price")
        tp_s = self.store.get_kv(strategy_name=self.strategy_name, figi=self.figi, key="tp_price")
        stop = _to_decimal(stop_s)
        tp = _to_decimal(tp_s)
        if current_qty > 0:
            if stop is not None and close <= stop:
                return True
            if tp is not None and close >= tp:
                return True
        else:
            if stop is not None and close >= stop:
                return True
            if tp is not None and close <= tp:
                return True
        return False

    async def start(self):
        self.store.connect()
        aid = await self._ensure_account_id()
        if not aid:
            logger.error("account_id not resolved")
            return
        self.oms = OrderManager(broker=broker_client, store=self.store, account_id=aid)
        await self._ensure_contract_spec()

        last_processed_s = self.store.get_kv(strategy_name=self.strategy_name, figi=self.figi, key="last_bar_ts")
        logger.info("start vwap_momentum figi=%s tf=%s poll=%ss", self.figi, self.cfg.timeframe, self.runner_config.poll_seconds)

        while True:
            try:
                if self.store.get_flag(key="trading_enabled", default="1") != "1":
                    await asyncio.sleep(min(self.runner_config.poll_seconds, 5))
                    continue

                now_utc = datetime.now(timezone.utc)
                cd_until = self.store.get_cooldown_until()
                if cd_until is not None and now_utc < cd_until:
                    await asyncio.sleep(min(self.runner_config.poll_seconds, 5))
                    continue

                portfolio = await self._get_portfolio()
                if await self._rollover_if_needed(portfolio):
                    await asyncio.sleep(self.runner_config.poll_seconds)
                    continue

                res = await self.data.fetch_last_intraday(figi=self.figi, n=self.lookback, timeframe=self.cfg.timeframe)
                if not res.candles or res.last_closed_ts is None:
                    await asyncio.sleep(self.runner_config.poll_seconds)
                    continue

                # Process only new closed bar
                if last_processed_s:
                    try:
                        if res.last_closed_ts <= datetime.fromisoformat(last_processed_s.replace("Z", "+00:00")):
                            await asyncio.sleep(self.runner_config.poll_seconds)
                            continue
                    except Exception:  # noqa: BLE001
                        pass

                last = res.candles[-1]
                current_qty = self._get_position_qty(portfolio)
                open_total = self._get_open_positions_total(portfolio)
                equity = self._equity_rub(portfolio)

                # Risk accounting: trades/day+week and drawdown vs equity baselines
                tz = moscow_tz()
                now_local = now_utc.astimezone(tz)
                day_start = start_of_day(now_local, tz).astimezone(timezone.utc)
                week_start = start_of_week(now_local, tz).astimezone(timezone.utc)
                trades_today = self.store.count_trade_events_since(since_ts=day_start)
                trades_week = self.store.count_trade_events_since(since_ts=week_start)

                daily_loss = None
                weekly_loss = None
                if equity is not None:
                    day_key = f"day:{now_local.date().isoformat()}"
                    week_key = f"week:{week_start.astimezone(tz).date().isoformat()}"
                    base_day = Decimal(self.store.get_or_set_equity_baseline(period_key=day_key, ts=now_utc, equity_rub=equity))
                    base_week = Decimal(self.store.get_or_set_equity_baseline(period_key=week_key, ts=now_utc, equity_rub=equity))
                    daily_pnl = equity - base_day
                    weekly_pnl = equity - base_week
                    daily_loss = -daily_pnl if daily_pnl < 0 else Decimal("0")
                    weekly_loss = -weekly_pnl if weekly_pnl < 0 else Decimal("0")

                # 1) Exit by stored SL/TP levels (close-based)
                sig: Optional[Signal] = None
                # Trailing stop update (best-effort). We'll use ATR from strategy signal if available.
                if current_qty != 0 and self._maybe_exit_by_levels(current_qty=current_qty, close=last.close):
                    sig = Signal(
                        strategy_name=self.strategy_name,
                        figi=self.figi,
                        ts=last.time,
                        signal_type=SignalType.TARGET_QTY,
                        target_qty=0,
                        reason="exit: SL/TP hit (close-based)",
                        risk_per_trade_pct=_to_decimal(self.cfg.risk_per_trade_pct),
                        sl_points=_to_decimal(self.cfg.sl_points),
                        tp_points=_to_decimal(self.cfg.tp_points),
                    )

                # 2) Strategy signal (entry/ema exit/session exit)
                if sig is None:
                    trend_dir = await self._get_trend_direction()
                    sig = self.strategy.generate_signal(
                        candles=res.candles,
                        current_position_qty=current_qty,
                        trend_direction=trend_dir,
                        strategy_name=self.strategy_name,
                    )
                    # If in position, update trailing stop using ATR computed by strategy.
                    if current_qty != 0:
                        try:
                            self._update_trailing_stop(current_qty=current_qty, last_close=last.close, atr_value=getattr(sig, "atr", None))
                        except Exception:  # noqa: BLE001
                            pass
                        # If trailing moved stop beyond close, exit on this bar close (deterministic).
                        if self._maybe_exit_by_levels(current_qty=current_qty, close=last.close):
                            sig = Signal(
                                strategy_name=self.strategy_name,
                                figi=self.figi,
                                ts=last.time,
                                signal_type=SignalType.TARGET_QTY,
                                target_qty=0,
                                reason="exit: SL/TP hit after trailing update (close-based)",
                                risk_per_trade_pct=_to_decimal(self.cfg.risk_per_trade_pct),
                                sl_points=_to_decimal(self.cfg.sl_points),
                                tp_points=_to_decimal(self.cfg.tp_points),
                            )

                if sig is None:
                    self.store.set_kv(strategy_name=self.strategy_name, figi=self.figi, key="last_bar_ts", value=last.time.isoformat())
                    last_processed_s = last.time.isoformat()
                    await asyncio.sleep(self.runner_config.poll_seconds)
                    continue

                # Attach per-contract risk_stop for RiskGate sizing on entry signals
                risk_stop = None
                if sig.target_qty != 0 and abs(int(sig.target_qty)) == 1:
                    risk_stop = self._risk_stop_rub(atr_value=sig.atr)
                sig_risk = Signal(
                    strategy_name=sig.strategy_name,
                    figi=sig.figi,
                    ts=sig.ts,
                    signal_type=sig.signal_type,
                    target_qty=sig.target_qty,
                    reason=sig.reason,
                    atr=sig.atr,
                    risk_per_trade_pct=sig.risk_per_trade_pct,
                    risk_stop=risk_stop,
                    sl_points=sig.sl_points,
                    tp_points=sig.tp_points,
                )

                decision = self.risk.check(
                    signal=sig_risk,
                    instrument=self.instrument_config,
                    current_position_qty=current_qty,
                    open_positions_total=open_total,
                    trades_today=trades_today,
                    trades_week=trades_week,
                    daily_loss_rub=daily_loss,
                    weekly_loss_rub=weekly_loss,
                    equity_rub=equity,
                    # prefer signal.risk_per_trade_pct, fallback to instrument/global inside RiskGate
                    risk_per_trade_pct_override=_norm_risk_pct(self.cfg.risk_per_trade_pct),
                )
                if not decision.allowed or decision.intent is None:
                    self.store.set_job_decision(
                        job_id=self._job_id(),
                        payload={
                            "figi": self.figi,
                            "strategy": self.strategy_name,
                            "timeframe": self.cfg.timeframe,
                            "bar_ts": last.time.isoformat(),
                            "close": str(last.close),
                            "signal_target_qty": sig.target_qty,
                            "signal_reason": sig.reason,
                            "risk_allowed": False,
                            "risk_reason": decision.reason,
                        },
                    )
                    self.store.set_kv(strategy_name=self.strategy_name, figi=self.figi, key="last_bar_ts", value=last.time.isoformat())
                    last_processed_s = last.time.isoformat()
                    await asyncio.sleep(self.runner_config.poll_seconds)
                    continue

                assert self.oms is not None
                placed = await self.oms.place_market_order(decision.intent)

                # Levels management
                if sig.target_qty == 0:
                    self._clear_levels()
                else:
                    direction = 1 if sig.target_qty > 0 else -1
                    self._set_levels(entry_price=last.close, atr_value=sig.atr, direction=direction)

                if placed.created_new:
                    self.store.add_trade_event(
                        ts=now_utc,
                        strategy_name=self.strategy_name,
                        figi=self.figi,
                        client_order_id=placed.order.client_order_id,
                    )

                self.store.set_job_decision(
                    job_id=self._job_id(),
                    payload={
                        "figi": self.figi,
                        "strategy": self.strategy_name,
                        "timeframe": self.cfg.timeframe,
                        "bar_ts": last.time.isoformat(),
                        "close": str(last.close),
                        "current_position_qty": current_qty,
                        "signal_target_qty": sig.target_qty,
                        "signal_reason": sig.reason,
                        "risk_stop_rub": None if risk_stop is None else str(risk_stop),
                        "risk_allowed": True,
                        "order_client_id": placed.order.client_order_id,
                    },
                )

                self.store.set_kv(strategy_name=self.strategy_name, figi=self.figi, key="last_bar_ts", value=last.time.isoformat())
                last_processed_s = last.time.isoformat()
            except Exception as e:  # noqa: BLE001
                logger.exception("vwap_momentum loop error figi=%s err=%s", self.figi, e)
                self.store.set_cooldown_until(
                    cooldown_until=datetime.now(timezone.utc)
                    + timedelta(seconds=int(self.global_risk.cooldown_seconds_after_error))
                )

            await asyncio.sleep(self.runner_config.poll_seconds)

