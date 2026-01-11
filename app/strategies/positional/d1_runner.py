from __future__ import annotations

import asyncio
import logging
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

from t_tech.invest import AioRequestError, OrderExecutionReportStatus

from app.client import client as broker_client
from app.instruments_config.models import GlobalExecutionConfig, GlobalRiskConfig, InstrumentConfig
from app.settings import settings
from app.strategies.base import BaseStrategy
from app.strategies.positional.indicators import atr, donchian_high, donchian_low, ema
from core.data.candles import CandleRepository
from core.futures.rollover import should_rollover
from core.models.entities import OrderIntent
from core.oms.order_manager import OrderManager
from core.risk.gate import RiskGate
from core.utils.time import moscow_tz, start_of_day, start_of_week
from storage.state_store import StateStore

from app.strategies.positional.donchian_atr import DonchianATRStrategy
from app.strategies.positional.ema_atr import EmaAtrTrendStrategy
from app.strategies.positional.params import parse_donchian_atr_config, parse_ema_atr_config

logger = logging.getLogger(__name__)

FINAL_STATUSES = {
    OrderExecutionReportStatus.EXECUTION_REPORT_STATUS_CANCELLED,
    OrderExecutionReportStatus.EXECUTION_REPORT_STATUS_REJECTED,
    OrderExecutionReportStatus.EXECUTION_REPORT_STATUS_FILL,
}


def _mv_to_decimal(mv) -> Decimal:
    return Decimal(mv.units) + (Decimal(mv.nano) / Decimal(1_000_000_000))


def _get_equity_rub(portfolio) -> Optional[Decimal]:
    # Best effort: depends on SDK response shape; fallback to None
    mv = getattr(portfolio, "total_amount_portfolio", None)
    if mv is None:
        mv = getattr(portfolio, "total_amount_currencies", None)
    if mv is None:
        return None
    try:
        return _mv_to_decimal(mv)
    except Exception:  # noqa: BLE001
        return None


def _pos_qty_from_position(pos) -> int:
    q = getattr(pos, "quantity", None)
    if q is None:
        return 0
    try:
        # quotation-like: units + nano
        return int(Decimal(q.units) + (Decimal(q.nano) / Decimal(1_000_000_000)))
    except Exception:  # noqa: BLE001
        try:
            return int(q)
        except Exception:  # noqa: BLE001
            return 0


@dataclass(frozen=True)
class D1RunnerConfig:
    poll_seconds: int = 300  # check for new closed D1 candle every 5 min


class D1PositionalStrategyRunner(BaseStrategy):
    """
    Broker-backed D1 runner:
    - fetch last D1 candles
    - generate signal via pure strategy
    - RiskGate -> OMS (idempotent)
    - persist last processed candle close
    - optional futures rollover execution (close+open) via OMS
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
        runner_config: D1RunnerConfig = D1RunnerConfig(),
    ):
        self.figi = figi
        self.instrument_config = instrument_config
        self.global_risk = global_risk
        self.global_execution = global_execution
        self.strategy_name = strategy_name
        self.strategy_params = strategy_params
        self.runner_config = runner_config

        self.account_id: Optional[str] = settings.account_id
        self.store = StateStore(db_path="state.db")
        self.data = CandleRepository(broker=broker_client)
        self.risk = RiskGate(global_risk=global_risk, kill_switch_path=global_risk.kill_switch_file)
        self.oms: Optional[OrderManager] = None

        # strategy instance (pure)
        if strategy_name == "donchian_atr":
            cfg = parse_donchian_atr_config(strategy_params)
            self.strategy = DonchianATRStrategy(figi=figi, config=cfg)
            self.lookback = max(cfg.breakout_lookback, cfg.exit_lookback) + cfg.atr_period + 5
        elif strategy_name == "ema_atr":
            cfg = parse_ema_atr_config(strategy_params)
            self.strategy = EmaAtrTrendStrategy(figi=figi, config=cfg)
            self.lookback = max(cfg.ema_fast, cfg.ema_slow) + cfg.atr_period + 5
        else:
            raise ValueError(f"Unsupported D1 runner strategy: {strategy_name}")

    @property
    def job_id(self) -> str:
        return f"{self.figi}|{self.strategy_name}"

    def _indicators_snapshot(self, candles) -> dict[str, Any]:
        """
        Best-effort indicator values for UI transparency.
        Uses the same inputs as signal logic (closed candles).
        """
        try:
            last = candles[-1]
        except Exception:
            return {}

        out: dict[str, Any] = {
            "candle_time": getattr(last, "time", None),
            "close": getattr(last, "close", None),
        }
        try:
            if self.strategy_name == "donchian_atr":
                prev = candles[:-1]
                bl = int(getattr(self.strategy, "cfg").breakout_lookback)  # type: ignore[attr-defined]
                el = int(getattr(self.strategy, "cfg").exit_lookback)  # type: ignore[attr-defined]
                ap = int(getattr(self.strategy, "cfg").atr_period)  # type: ignore[attr-defined]
                out.update(
                    {
                        "donchian_high": donchian_high(prev, bl),
                        "donchian_low": donchian_low(prev, bl),
                        "exit_high": donchian_high(prev, el),
                        "exit_low": donchian_low(prev, el),
                        "atr": atr(candles, ap),
                    }
                )
            elif self.strategy_name == "ema_atr":
                closes = [c.close for c in candles]
                ef = int(getattr(self.strategy, "cfg").ema_fast)  # type: ignore[attr-defined]
                es = int(getattr(self.strategy, "cfg").ema_slow)  # type: ignore[attr-defined]
                ap = int(getattr(self.strategy, "cfg").atr_period)  # type: ignore[attr-defined]
                efast = ema(closes, ef)
                eslow = ema(closes, es)
                out.update(
                    {
                        "ema_fast": efast[-1] if efast else None,
                        "ema_slow": eslow[-1] if eslow else None,
                        "atr": atr(candles, ap),
                    }
                )
        except Exception:  # noqa: BLE001
            # transparency must never break trading loop
            return out
        return out

    def _record_decision(
        self,
        *,
        candles,
        current_position_qty: int,
        in_cooldown: bool,
        signal,
        risk_allowed: bool,
        risk_reason: str,
        target_qty: Optional[int],
        delta_qty: Optional[int],
        order_intent: Optional[OrderIntent],
        client_order_id: Optional[str] = None,
    ) -> None:
        payload: dict[str, Any] = {
            "figi": self.figi,
            "strategy": self.strategy_name,
            "sandbox": bool(settings.sandbox),
            "in_cooldown": bool(in_cooldown),
            "current_position_qty": int(current_position_qty),
            "signal": None
            if signal is None
            else {
                "type": getattr(signal, "signal_type", None),
                "target_qty": getattr(signal, "target_qty", None),
                "reason": getattr(signal, "reason", None),
                "atr": getattr(signal, "atr", None),
                "ts": getattr(signal, "ts", None),
            },
            "indicators": self._indicators_snapshot(candles),
            "risk": {"allowed": bool(risk_allowed), "reason": str(risk_reason)},
            "plan": {
                "target_qty": target_qty,
                "delta_qty": delta_qty,
                "order": None
                if order_intent is None
                else {
                    "side": getattr(order_intent, "side", None),
                    "intended_qty": getattr(order_intent, "intended_qty", None),
                },
                "client_order_id": client_order_id,
            },
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            self.store.set_job_decision(job_id=self.job_id, payload=payload)
        except Exception:  # noqa: BLE001
            pass

    async def _ensure_account_id(self) -> Optional[str]:
        if self.account_id:
            return self.account_id
        try:
            self.account_id = (await broker_client.get_accounts()).accounts.pop().id
            return self.account_id
        except AioRequestError as e:
            logger.error("Failed to get account_id: %s", e)
            return None

    async def _get_portfolio(self):
        assert self.account_id is not None
        return await broker_client.get_portfolio(account_id=self.account_id)

    async def _get_position_qty(self, portfolio) -> int:
        for pos in getattr(portfolio, "positions", []):
            if getattr(pos, "figi", None) == self.figi:
                return _pos_qty_from_position(pos)
        return 0

    async def _get_open_positions_total(self, portfolio) -> int:
        n = 0
        for pos in getattr(portfolio, "positions", []):
            if _pos_qty_from_position(pos) != 0:
                n += 1
        return n

    async def _get_expiry_date(self) -> Optional[datetime]:
        try:
            # Prefer official enum if available; fallback to numeric.
            try:
                from t_tech.invest.grpc.instruments_pb2 import INSTRUMENT_ID_TYPE_FIGI

                id_type = INSTRUMENT_ID_TYPE_FIGI
            except Exception:  # noqa: BLE001
                id_type = 1
            resp = await broker_client.get_instrument(id_type=id_type, id=self.figi)
            inst = getattr(resp, "instrument", None)
            if inst is None:
                return None
            return getattr(inst, "expiration_date", None)
        except Exception:  # noqa: BLE001
            return None

    async def _wait_final(self, order_id: str, timeout_seconds: int = 120) -> None:
        assert self.account_id is not None
        start = datetime.now(timezone.utc)
        while (datetime.now(timezone.utc) - start).total_seconds() < timeout_seconds:
            st = await broker_client.get_order_state(account_id=self.account_id, order_id=order_id)
            if st.execution_report_status in FINAL_STATUSES:
                return
            await asyncio.sleep(3)

    async def _rollover_if_needed(self, portfolio) -> bool:
        """
        Returns True if rollover executed (so we should skip regular signal for this loop).
        """
        if self.instrument_config.instrument_type != "futures":
            return False

        pos_qty = await self._get_position_qty(portfolio)
        if pos_qty == 0:
            return False

        expiry_dt = await self._get_expiry_date()
        expiry_date = expiry_dt.date() if expiry_dt else None
        dec = should_rollover(instrument=self.instrument_config, expiry_date=expiry_date)
        if not dec.should_roll or not dec.next_figi:
            return False

        assert self.oms is not None

        # 1) Close current position
        # Convert side to Side enum for OMS
        from core.models.entities import Side as SideEnum

        close_intent = OrderIntent(
            strategy_name="rollover",
            figi=self.figi,
            side=SideEnum.SELL if pos_qty > 0 else SideEnum.BUY,
            intended_qty=abs(pos_qty),
            ts=datetime.now(timezone.utc),
        )
        r1 = await self.oms.place_market_order(close_intent)
        await self._wait_final(r1.order.client_order_id)

        # 2) Open equivalent in next contract
        open_intent = OrderIntent(
            strategy_name="rollover",
            figi=dec.next_figi,
            side=SideEnum.BUY if pos_qty > 0 else SideEnum.SELL,
            intended_qty=abs(pos_qty),
            ts=datetime.now(timezone.utc),
        )
        r2 = await self.oms.place_market_order(open_intent)
        await self._wait_final(r2.order.client_order_id)
        logger.info("Rollover executed %s -> %s qty=%s", self.figi, dec.next_figi, abs(pos_qty))
        return True

    async def start(self):
        self.store.connect()
        aid = await self._ensure_account_id()
        if not aid:
            return
        self.oms = OrderManager(broker=broker_client, store=self.store, account_id=aid)

        # Reconcile: snapshot current positions and refresh open orders
        try:
            portfolio = await self._get_portfolio()
            for pos in getattr(portfolio, "positions", []):
                figi = getattr(pos, "figi", None)
                if not figi:
                    continue
                qty = _pos_qty_from_position(pos)
                if qty == 0:
                    continue
                try:
                    from core.models.entities import Position

                    self.store.upsert_position(
                        Position(figi=str(figi), qty=int(qty), updated_at=datetime.now(timezone.utc))
                    )
                except Exception:  # noqa: BLE001
                    pass

            for oid in self.store.list_open_orders():
                try:
                    await self.oms.refresh_order_state(oid)
                except Exception:  # noqa: BLE001
                    continue
        except Exception:  # noqa: BLE001
            pass

        logger.info(
            "Starting D1 runner strategy=%s figi=%s poll=%ss",
            self.strategy_name,
            self.figi,
            self.runner_config.poll_seconds,
        )

        while True:
            try:
                # Global pause flag (UI control)
                if self.store.get_flag(key="trading_enabled", default="1") != "1":
                    await asyncio.sleep(min(self.runner_config.poll_seconds, 30))
                    continue

                # Cooldown after errors (global)
                now_utc = datetime.now(timezone.utc)
                cd_until = self.store.get_cooldown_until()
                if cd_until is not None and now_utc < cd_until:
                    await asyncio.sleep(min(self.runner_config.poll_seconds, 30))
                    continue

                portfolio = await self._get_portfolio()

                # Rollover has priority on futures
                if await self._rollover_if_needed(portfolio):
                    await asyncio.sleep(self.runner_config.poll_seconds)
                    continue

                res = await self.data.fetch_last_d1(figi=self.figi, n=self.lookback)
                if not res.candles or res.last_closed_ts is None:
                    await asyncio.sleep(self.runner_config.poll_seconds)
                    continue

                last_processed = self.store.get_last_processed_candle_close(
                    strategy_name=self.strategy_name, figi=self.figi
                )
                if last_processed is not None and res.last_closed_ts <= last_processed:
                    await asyncio.sleep(self.runner_config.poll_seconds)
                    continue

                current_qty = await self._get_position_qty(portfolio)
                open_total = await self._get_open_positions_total(portfolio)
                equity = _get_equity_rub(portfolio)

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
                    base_day = Decimal(
                        self.store.get_or_set_equity_baseline(
                            period_key=day_key, ts=now_utc, equity_rub=equity
                        )
                    )
                    base_week = Decimal(
                        self.store.get_or_set_equity_baseline(
                            period_key=week_key, ts=now_utc, equity_rub=equity
                        )
                    )
                    daily_pnl = equity - base_day
                    weekly_pnl = equity - base_week
                    daily_loss = -daily_pnl if daily_pnl < 0 else Decimal("0")
                    weekly_loss = -weekly_pnl if weekly_pnl < 0 else Decimal("0")

                # Generate signal
                in_cooldown = False
                if self.strategy_name == "ema_atr":
                    # cooldown handling v1: not implemented yet (needs state); assume not in cooldown
                    sig = self.strategy.generate_signal(
                        candles=res.candles,
                        current_position_qty=current_qty,
                        in_cooldown=in_cooldown,
                        strategy_name=self.strategy_name,
                    )
                else:
                    sig = self.strategy.generate_signal(
                        candles=res.candles,
                        current_position_qty=current_qty,
                        strategy_name=self.strategy_name,
                    )
                if sig is None:
                    # Transparency: record that no signal was generated for this candle
                    try:
                        self._record_decision(
                            candles=res.candles,
                            current_position_qty=current_qty,
                            in_cooldown=in_cooldown,
                            signal=None,
                            risk_allowed=False,
                            risk_reason="нет сигнала",
                            target_qty=None,
                            delta_qty=None,
                            order_intent=None,
                        )
                    except Exception:  # noqa: BLE001
                        pass
                    self.store.set_last_processed_candle_close(
                        strategy_name=self.strategy_name, figi=self.figi, candle_close_ts=res.last_closed_ts
                    )
                    await asyncio.sleep(self.runner_config.poll_seconds)
                    continue

                decision = self.risk.check(
                    signal=sig,
                    instrument=self.instrument_config,
                    current_position_qty=current_qty,
                    open_positions_total=open_total,
                    trades_today=trades_today,
                    trades_week=trades_week,
                    daily_loss_rub=daily_loss,
                    weekly_loss_rub=weekly_loss,
                    equity_rub=equity,
                    # For D1 strategies, keep using global risk_per_trade_pct (no override)
                )
                if not decision.allowed or decision.intent is None:
                    # Transparency: record block reason
                    try:
                        self._record_decision(
                            candles=res.candles,
                            current_position_qty=current_qty,
                            in_cooldown=in_cooldown,
                            signal=sig,
                            risk_allowed=False,
                            risk_reason=decision.reason,
                            target_qty=getattr(sig, "target_qty", None),
                            delta_qty=None,
                            order_intent=None,
                        )
                    except Exception:  # noqa: BLE001
                        pass
                    logger.info(
                        "RiskGate blocked strategy=%s figi=%s reason=%s",
                        self.strategy_name,
                        self.figi,
                        decision.reason,
                    )
                    self.store.set_last_processed_candle_close(
                        strategy_name=self.strategy_name, figi=self.figi, candle_close_ts=res.last_closed_ts
                    )
                    await asyncio.sleep(self.runner_config.poll_seconds)
                    continue

                # Transparency: record planned order before sending
                target_qty = getattr(sig, "target_qty", None)
                try:
                    delta_qty = int(target_qty) - int(current_qty) if target_qty is not None else None
                except Exception:  # noqa: BLE001
                    delta_qty = None
                try:
                    self._record_decision(
                        candles=res.candles,
                        current_position_qty=current_qty,
                        in_cooldown=in_cooldown,
                        signal=sig,
                        risk_allowed=True,
                        risk_reason=decision.reason,
                        target_qty=target_qty,
                        delta_qty=delta_qty,
                        order_intent=decision.intent,
                    )
                except Exception:  # noqa: BLE001
                    pass

                placed = await self.oms.place_market_order(decision.intent)
                logger.info(
                    "Order %s strategy=%s figi=%s side=%s qty=%s new=%s",
                    placed.order.client_order_id,
                    self.strategy_name,
                    self.figi,
                    decision.intent.side.value,
                    decision.intent.intended_qty,
                    placed.created_new,
                )
                # Transparency: record placed order id
                try:
                    self._record_decision(
                        candles=res.candles,
                        current_position_qty=current_qty,
                        in_cooldown=in_cooldown,
                        signal=sig,
                        risk_allowed=True,
                        risk_reason=decision.reason,
                        target_qty=target_qty,
                        delta_qty=delta_qty,
                        order_intent=decision.intent,
                        client_order_id=placed.order.client_order_id,
                    )
                except Exception:  # noqa: BLE001
                    pass
                if placed.created_new:
                    self.store.add_trade_event(
                        ts=now_utc,
                        strategy_name=self.strategy_name,
                        figi=self.figi,
                        client_order_id=placed.order.client_order_id,
                    )

                self.store.set_last_processed_candle_close(
                    strategy_name=self.strategy_name, figi=self.figi, candle_close_ts=res.last_closed_ts
                )
            except AioRequestError as e:
                logger.error("Broker error strategy=%s figi=%s err=%s", self.strategy_name, self.figi, e)
                self.store.set_cooldown_until(
                    cooldown_until=datetime.now(timezone.utc)
                    + timedelta(seconds=int(self.global_risk.cooldown_seconds_after_error))
                )
            except Exception as e:  # noqa: BLE001
                logger.exception("Unexpected error strategy=%s figi=%s err=%s", self.strategy_name, self.figi, e)
                self.store.set_cooldown_until(
                    cooldown_until=datetime.now(timezone.utc)
                    + timedelta(seconds=int(self.global_risk.cooldown_seconds_after_error))
                )

            await asyncio.sleep(self.runner_config.poll_seconds)

