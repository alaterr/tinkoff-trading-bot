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
from app.strategies.positional.indicators import atr, donchian_high, donchian_low, ema
from core.data.candles import CandleRepository
from core.futures.rollover import should_rollover
from core.models.entities import OrderIntent, Signal, SignalType
from core.oms.order_manager import OrderManager
from core.risk.gate import RiskGate
from core.utils.time import moscow_tz, start_of_day, start_of_week
from storage.state_store import StateStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TrendBreakoutATRConfig:
    timeframe: str = "1h"  # "1h" | "4h"
    breakout_lookback: int = 20
    trend_lookback: int = 50  # EMA on D1
    atr_period: int = 14
    atr_stop_mult: Decimal = Decimal("2")
    atr_tp_mult: Decimal = Decimal("3")
    risk_per_trade_pct: Decimal = Decimal("0.005")  # 0.5% default
    exit_before_close_minutes: int = 0

    # Optional override for rollover decision (instrument.rollover is still the source of truth)
    days_before_expiry_to_roll: Optional[int] = None


def _to_decimal(v) -> Optional[Decimal]:
    if v is None:
        return None
    if isinstance(v, Decimal):
        return v
    try:
        return Decimal(str(v))
    except Exception:  # noqa: BLE001
        return None


def _norm_risk_pct(v: Decimal) -> Decimal:
    """
    Accept both "0.005" (0.5%) and "0.5" (0.5%) styles.
    If v > 1, interpret as percent and divide by 100.
    """
    if v > 1:
        return v / Decimal("100")
    return v


class TrendBreakoutATRStrategy(BaseStrategy):
    """
    TrendBreakoutATRStrategy (positional):
    - Base timeframe: 1h or 4h (4h aggregated from 1h candles)
    - Trend filter: D1 EMA(trend_lookback) slope
    - Entry: Donchian breakout on base timeframe, only in trend direction
    - Exit: ATR-based SL/TP (evaluated on candle close) and reverse breakout
    - Rollover: uses existing futures rollover mechanism (instrument.rollover)
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
    ):
        self.figi = figi
        self.instrument_config = instrument_config
        self.global_risk = global_risk
        self.global_execution = global_execution
        self.strategy_name = strategy_name

        # parse config
        cfg = TrendBreakoutATRConfig()
        p = dict(strategy_params or {})
        if "timeframe" in p:
            cfg = dataclass_replace(cfg, timeframe=str(p["timeframe"]))  # type: ignore[arg-type]
        # manual parsing to keep permissive types
        self.cfg = TrendBreakoutATRConfig(
            timeframe=str(p.get("timeframe", cfg.timeframe)),
            breakout_lookback=int(p.get("breakout_lookback", cfg.breakout_lookback)),
            trend_lookback=int(p.get("trend_lookback", cfg.trend_lookback)),
            atr_period=int(p.get("atr_period", cfg.atr_period)),
            atr_stop_mult=_to_decimal(p.get("atr_stop_mult")) or cfg.atr_stop_mult,
            atr_tp_mult=_to_decimal(p.get("atr_tp_mult")) or cfg.atr_tp_mult,
            risk_per_trade_pct=_to_decimal(p.get("risk_per_trade_pct")) or cfg.risk_per_trade_pct,
            exit_before_close_minutes=int(p.get("exit_before_close_minutes", cfg.exit_before_close_minutes)),
            days_before_expiry_to_roll=(
                int(p["days_before_expiry_to_roll"]) if "days_before_expiry_to_roll" in p else None
            ),
        )

        self.store = StateStore(db_path="state.db")
        self.data = CandleRepository(broker=broker_client)
        self.risk = RiskGate(global_risk=global_risk, kill_switch_path=global_risk.kill_switch_file)
        self.oms: Optional[OrderManager] = None
        self.account_id: Optional[str] = settings.account_id

        # Loop cadence: check every minute; real decision only on new closed bar.
        self.poll_seconds = 60

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
                except Exception:
                    try:
                        return int(q)
                    except Exception:
                        return 0
        return 0

    def _equity_rub(self, portfolio) -> Optional[Decimal]:
        mv = getattr(portfolio, "total_amount_portfolio", None) or getattr(portfolio, "total_amount_currencies", None)
        if mv is None:
            return None
        try:
            return Decimal(mv.units) + (Decimal(mv.nano) / Decimal(1_000_000_000))
        except Exception:
            return None

    async def _rollover_if_needed(self, portfolio) -> bool:
        if self.instrument_config.instrument_type != "futures":
            return False
        pos_qty = self._get_position_qty(portfolio)
        if pos_qty == 0:
            return False

        # use existing rollover config (already has days_before_expiry_to_roll + next_contract_figi)
        expiry_dt = None
        try:
            # Prefer official enum if available; fallback to numeric.
            try:
                from t_tech.invest.grpc.instruments_pb2 import INSTRUMENT_ID_TYPE_FIGI

                id_type = INSTRUMENT_ID_TYPE_FIGI
            except Exception:
                id_type = 1
            resp = await broker_client.get_instrument(id_type=id_type, id=self.figi)
            inst = getattr(resp, "instrument", None)
            expiry_dt = getattr(inst, "expiration_date", None) if inst is not None else None
        except Exception:
            expiry_dt = None
        expiry_date = expiry_dt.date() if expiry_dt else None
        dec = should_rollover(instrument=self.instrument_config, expiry_date=expiry_date)
        if not dec.should_roll or not dec.next_figi:
            return False

        assert self.oms is not None and self.account_id is not None
        from core.models.entities import Side as SideEnum

        # Close old
        close_intent = OrderIntent(
            strategy_name="rollover",
            figi=self.figi,
            side=SideEnum.SELL if pos_qty > 0 else SideEnum.BUY,
            intended_qty=abs(pos_qty),
            ts=datetime.now(timezone.utc),
        )
        r1 = await self.oms.place_market_order(close_intent)

        # Open new
        open_intent = OrderIntent(
            strategy_name="rollover",
            figi=dec.next_figi,
            side=SideEnum.BUY if pos_qty > 0 else SideEnum.SELL,
            intended_qty=abs(pos_qty),
            ts=datetime.now(timezone.utc),
        )
        r2 = await self.oms.place_market_order(open_intent)

        logger.info("rollover %s -> %s qty=%s close=%s open=%s", self.figi, dec.next_figi, abs(pos_qty), r1.order.client_order_id, r2.order.client_order_id)
        return True

    def _trend_direction(self, d1_closes: list[Decimal]) -> int:
        """
        +1 uptrend, -1 downtrend, 0 unknown
        Uses EMA slope: ema[-1] > ema[-2]
        """
        if len(d1_closes) < self.cfg.trend_lookback + 2:
            return 0
        e = ema(d1_closes, self.cfg.trend_lookback)
        if len(e) < 2:
            return 0
        return 1 if e[-1] > e[-2] else -1 if e[-1] < e[-2] else 0

    def _position_size(self, *, equity: Optional[Decimal], atr_value: Optional[Decimal]) -> int:
        """
        Risk-based sizing (best-effort, contract multiplier unknown → lot_size=1).
        """
        if equity is None or atr_value is None or atr_value <= 0:
            return int(self.instrument_config.max_order_qty or 1)
        risk_frac = _norm_risk_pct(self.cfg.risk_per_trade_pct)
        if risk_frac <= 0:
            return int(self.instrument_config.max_order_qty or 1)
        risk_budget = equity * risk_frac
        stop_dist = atr_value * self.cfg.atr_stop_mult
        if stop_dist <= 0:
            return int(self.instrument_config.max_order_qty or 1)
        qty = int((risk_budget / stop_dist).to_integral_value(rounding="ROUND_FLOOR"))
        if qty <= 0:
            qty = 1
        if self.instrument_config.max_position_qty is not None:
            qty = min(qty, int(self.instrument_config.max_position_qty))
        if self.instrument_config.max_order_qty is not None:
            qty = min(qty, int(self.instrument_config.max_order_qty))
        return max(1, qty)

    def _maybe_exit_by_levels(self, *, current_qty: int, close: Decimal) -> Optional[int]:
        """
        Evaluate stored stop/tp levels (on candle close).
        Returns target_qty if should exit, else None.
        """
        if current_qty == 0:
            return None
        stop_s = self.store.get_kv(strategy_name=self.strategy_name, figi=self.figi, key="stop_price")
        tp_s = self.store.get_kv(strategy_name=self.strategy_name, figi=self.figi, key="tp_price")
        stop = _to_decimal(stop_s)
        tp = _to_decimal(tp_s)
        if current_qty > 0:
            if stop is not None and close <= stop:
                return 0
            if tp is not None and close >= tp:
                return 0
        else:
            if stop is not None and close >= stop:
                return 0
            if tp is not None and close <= tp:
                return 0
        return None

    def _set_levels(self, *, entry_price: Decimal, atr_value: Optional[Decimal], direction: int) -> None:
        if atr_value is None or atr_value <= 0:
            return
        stop = entry_price - (self.cfg.atr_stop_mult * atr_value) if direction > 0 else entry_price + (self.cfg.atr_stop_mult * atr_value)
        tp = entry_price + (self.cfg.atr_tp_mult * atr_value) if direction > 0 else entry_price - (self.cfg.atr_tp_mult * atr_value)
        self.store.set_kv(strategy_name=self.strategy_name, figi=self.figi, key="entry_price", value=str(entry_price))
        self.store.set_kv(strategy_name=self.strategy_name, figi=self.figi, key="stop_price", value=str(stop))
        self.store.set_kv(strategy_name=self.strategy_name, figi=self.figi, key="tp_price", value=str(tp))

    async def start(self):
        self.store.connect()
        aid = await self._ensure_account_id()
        if not aid:
            logger.error("account_id not resolved")
            return
        self.oms = OrderManager(broker=broker_client, store=self.store, account_id=aid)

        logger.info("start trend_breakout_atr figi=%s tf=%s", self.figi, self.cfg.timeframe)

        last_processed_s = self.store.get_kv(strategy_name=self.strategy_name, figi=self.figi, key="last_bar_ts")

        while True:
            try:
                # Global pause flag (UI control)
                if self.store.get_flag(key="trading_enabled", default="1") != "1":
                    await asyncio.sleep(5)
                    continue

                now_utc = datetime.now(timezone.utc)
                cd_until = self.store.get_cooldown_until()
                if cd_until is not None and now_utc < cd_until:
                    await asyncio.sleep(5)
                    continue

                portfolio = await self._get_portfolio()
                if await self._rollover_if_needed(portfolio):
                    await asyncio.sleep(self.poll_seconds)
                    continue

                # Intraday candles
                need = max(self.cfg.breakout_lookback, self.cfg.atr_period) + 5
                intr = await self.data.fetch_last_intraday(figi=self.figi, n=need, timeframe=self.cfg.timeframe)
                if not intr.candles or intr.last_closed_ts is None:
                    await asyncio.sleep(self.poll_seconds)
                    continue

                # Process only new bar
                if last_processed_s:
                    try:
                        if intr.last_closed_ts <= datetime.fromisoformat(last_processed_s.replace("Z", "+00:00")):
                            await asyncio.sleep(self.poll_seconds)
                            continue
                    except Exception:
                        pass

                # Daily candles for trend
                d1 = await self.data.fetch_last_d1(figi=self.figi, n=self.cfg.trend_lookback + 5)
                d1_closes = [c.close for c in d1.candles]
                trend_dir = self._trend_direction(d1_closes)  # +1 / -1 / 0

                # Indicators on base tf
                prev = intr.candles[:-1]
                last = intr.candles[-1]
                upper = donchian_high(prev, self.cfg.breakout_lookback)
                lower = donchian_low(prev, self.cfg.breakout_lookback)
                a = atr(intr.candles, self.cfg.atr_period)

                current_qty = self._get_position_qty(portfolio)
                equity = self._equity_rub(portfolio)

                # Exit by SL/TP (close-based)
                exit_target = self._maybe_exit_by_levels(current_qty=current_qty, close=last.close)
                if exit_target == 0 and current_qty != 0:
                    sig = Signal(
                        strategy_name=self.strategy_name,
                        figi=self.figi,
                        ts=last.time,
                        signal_type=SignalType.TARGET_QTY,
                        target_qty=0,
                        reason="выход по SL/TP",
                        atr=a,
                    )
                else:
                    sig = None

                # Entry / reverse logic
                if sig is None and upper is not None and lower is not None and trend_dir != 0:
                    qty = self._position_size(equity=equity, atr_value=a)
                    if last.close > upper and trend_dir > 0:
                        sig = Signal(
                            strategy_name=self.strategy_name,
                            figi=self.figi,
                            ts=last.time,
                            signal_type=SignalType.TARGET_QTY,
                            target_qty=qty,
                            reason="вход: пробой верхнего дончиана + тренд вверх",
                            atr=a,
                        )
                    elif last.close < lower and trend_dir < 0:
                        sig = Signal(
                            strategy_name=self.strategy_name,
                            figi=self.figi,
                            ts=last.time,
                            signal_type=SignalType.TARGET_QTY,
                            target_qty=-qty,
                            reason="вход: пробой нижнего дончиана + тренд вниз",
                            atr=a,
                        )
                    # Reverse on opposite breakout (if trend also flipped)
                    elif current_qty > 0 and last.close < lower and trend_dir < 0:
                        sig = Signal(
                            strategy_name=self.strategy_name,
                            figi=self.figi,
                            ts=last.time,
                            signal_type=SignalType.TARGET_QTY,
                            target_qty=-qty,
                            reason="переворот: пробой вниз + тренд вниз",
                            atr=a,
                        )
                    elif current_qty < 0 and last.close > upper and trend_dir > 0:
                        sig = Signal(
                            strategy_name=self.strategy_name,
                            figi=self.figi,
                            ts=last.time,
                            signal_type=SignalType.TARGET_QTY,
                            target_qty=qty,
                            reason="переворот: пробой вверх + тренд вверх",
                            atr=a,
                        )

                if sig is None:
                    # record transparency
                    self.store.set_job_decision(
                        job_id=self._job_id(),
                        payload={
                            "figi": self.figi,
                            "strategy": self.strategy_name,
                            "timeframe": self.cfg.timeframe,
                            "bar_ts": last.time.isoformat(),
                            "close": str(last.close),
                            "donchian_upper": None if upper is None else str(upper),
                            "donchian_lower": None if lower is None else str(lower),
                            "trend_dir": trend_dir,
                            "atr": None if a is None else str(a),
                            "equity": None if equity is None else str(equity),
                            "current_position_qty": current_qty,
                            "note": "нет сигнала",
                        },
                    )
                    last_processed_s = last.time.isoformat()
                    self.store.set_kv(strategy_name=self.strategy_name, figi=self.figi, key="last_bar_ts", value=last_processed_s)
                    await asyncio.sleep(self.poll_seconds)
                    continue

                # Risk + OMS (reuse existing global limits)
                open_total = 0
                for pos in getattr(portfolio, "positions", []):
                    q = getattr(pos, "quantity", None)
                    try:
                        qq = int(Decimal(q.units) + (Decimal(q.nano) / Decimal(1_000_000_000))) if q is not None else 0
                    except Exception:
                        qq = 0
                    if qq != 0:
                        open_total += 1

                tz = moscow_tz()
                now_local = now_utc.astimezone(tz)
                day_start = start_of_day(now_local, tz).astimezone(timezone.utc)
                week_start = start_of_week(now_local, tz).astimezone(timezone.utc)
                trades_today = self.store.count_trade_events_since(since_ts=day_start)
                trades_week = self.store.count_trade_events_since(since_ts=week_start)

                decision = self.risk.check(
                    signal=sig,
                    instrument=self.instrument_config,
                    current_position_qty=current_qty,
                    open_positions_total=open_total,
                    trades_today=trades_today,
                    trades_week=trades_week,
                    equity_rub=equity,
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
                    last_processed_s = last.time.isoformat()
                    self.store.set_kv(strategy_name=self.strategy_name, figi=self.figi, key="last_bar_ts", value=last_processed_s)
                    await asyncio.sleep(self.poll_seconds)
                    continue

                assert self.oms is not None
                placed = await self.oms.place_market_order(decision.intent)

                # Set SL/TP levels if we opened/reversed position (best-effort)
                if sig.target_qty != 0:
                    direction = 1 if sig.target_qty > 0 else -1
                    self._set_levels(entry_price=last.close, atr_value=a, direction=direction)
                else:
                    # exit: clear levels
                    self.store.delete_kv(strategy_name=self.strategy_name, figi=self.figi, key="entry_price")
                    self.store.delete_kv(strategy_name=self.strategy_name, figi=self.figi, key="stop_price")
                    self.store.delete_kv(strategy_name=self.strategy_name, figi=self.figi, key="tp_price")

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
                        "donchian_upper": None if upper is None else str(upper),
                        "donchian_lower": None if lower is None else str(lower),
                        "trend_dir": trend_dir,
                        "atr": None if a is None else str(a),
                        "equity": None if equity is None else str(equity),
                        "current_position_qty": current_qty,
                        "signal_target_qty": sig.target_qty,
                        "signal_reason": sig.reason,
                        "risk_allowed": True,
                        "order_client_id": placed.order.client_order_id,
                    },
                )

                last_processed_s = last.time.isoformat()
                self.store.set_kv(strategy_name=self.strategy_name, figi=self.figi, key="last_bar_ts", value=last_processed_s)
            except Exception:  # noqa: BLE001
                logger.exception("trend_breakout_atr loop error figi=%s", self.figi)
                self.store.set_cooldown_until(
                    cooldown_until=datetime.now(timezone.utc)
                    + timedelta(seconds=int(self.global_risk.cooldown_seconds_after_error))
                )

            await asyncio.sleep(self.poll_seconds)


def dataclass_replace(obj, **kwargs):
    # tiny helper (avoid importing dataclasses.replace for older style)
    d = obj.__dict__.copy()
    d.update(kwargs)
    return obj.__class__(**d)

