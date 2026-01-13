from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from app.instruments_config.models import GlobalRiskConfig, InstrumentConfig
from core.models.entities import OrderIntent, Side, Signal
from core.utils.time import moscow_tz


@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    reason: str
    intent: Optional[OrderIntent] = None


def _file_exists(path: str) -> bool:
    try:
        return os.path.exists(path)
    except Exception:  # noqa: BLE001
        return False


class RiskGate:
    """
    MVP RiskGate:
    - kill switch file
    - per-instrument qty caps
    - basic max_positions_total (based on current positions count)
    - ATR sizing helper (risk_per_trade_pct * equity / atr) if ATR provided

    Strict margin blocking is enforced by config validation and later by portfolio checks (v2).
    """

    def __init__(self, *, global_risk: GlobalRiskConfig, kill_switch_path: Optional[str] = None):
        self._global = global_risk
        self._kill_switch = kill_switch_path or global_risk.kill_switch_file

    def check(
        self,
        *,
        signal: Signal,
        instrument: InstrumentConfig,
        current_position_qty: int,
        open_positions_total: int,
        trades_today: Optional[int] = None,
        trades_week: Optional[int] = None,
        daily_loss_rub: Optional[Decimal] = None,
        weekly_loss_rub: Optional[Decimal] = None,
        equity_rub: Optional[Decimal] = None,
        risk_per_trade_pct_override: Optional[Decimal] = None,
        now_ts: Optional[datetime] = None,
    ) -> RiskDecision:
        if _file_exists(self._kill_switch):
            return RiskDecision(allowed=False, reason=f"kill-switch file present: {self._kill_switch}")

        inst_risk = getattr(instrument, "risk", None)
        max_trades_per_day = (
            int(getattr(inst_risk, "max_trades_per_day", None))
            if getattr(inst_risk, "max_trades_per_day", None) is not None
            else int(self._global.max_trades_per_day)
        )
        max_trades_per_week = (
            int(getattr(inst_risk, "max_trades_per_week", None))
            if getattr(inst_risk, "max_trades_per_week", None) is not None
            else int(self._global.max_trades_per_week)
        )
        max_daily_loss_rub = (
            Decimal(str(getattr(inst_risk, "max_daily_loss_rub", None)))
            if getattr(inst_risk, "max_daily_loss_rub", None) is not None
            else Decimal(str(self._global.max_daily_loss_rub))
        )
        max_weekly_loss_rub = (
            Decimal(str(getattr(inst_risk, "max_weekly_loss_rub", None)))
            if getattr(inst_risk, "max_weekly_loss_rub", None) is not None
            else Decimal(str(self._global.max_weekly_loss_rub))
        )
        max_daily_loss_pct = (
            Decimal(str(getattr(inst_risk, "max_daily_loss_pct", None)))
            if getattr(inst_risk, "max_daily_loss_pct", None) is not None
            else Decimal(str(getattr(self._global, "max_daily_loss_pct", 0.0) or 0.0))
        )
        max_weekly_loss_pct = (
            Decimal(str(getattr(inst_risk, "max_weekly_loss_pct", None)))
            if getattr(inst_risk, "max_weekly_loss_pct", None) is not None
            else Decimal(str(getattr(self._global, "max_weekly_loss_pct", 0.0) or 0.0))
        )

        if trades_today is not None and trades_today >= max_trades_per_day:
            return RiskDecision(
                allowed=False,
                reason=f"max_trades_per_day reached ({max_trades_per_day})",
            )
        if trades_week is not None and trades_week >= max_trades_per_week:
            return RiskDecision(
                allowed=False,
                reason=f"max_trades_per_week reached ({max_trades_per_week})",
            )

        # Daily loss limits:
        # - absolute RUB: max_daily_loss_rub
        # - percent-of-equity (baseline): max_daily_loss_pct
        if daily_loss_rub is not None:
            if max_daily_loss_rub > 0 and daily_loss_rub >= max_daily_loss_rub:
                return RiskDecision(allowed=False, reason=f"max_daily_loss_rub reached ({max_daily_loss_rub})")
            if (
                max_daily_loss_rub <= 0
                and max_daily_loss_pct is not None
                and max_daily_loss_pct > 0
                and equity_rub is not None
            ):
                # Normalize percent-like values: 2.0 -> 2%
                pct = max_daily_loss_pct / Decimal("100") if max_daily_loss_pct >= Decimal("0.1") else max_daily_loss_pct
                # Baseline equity for the day ~ current equity + drawdown so far
                baseline = equity_rub + daily_loss_rub
                limit = baseline * pct
                if limit > 0 and daily_loss_rub >= limit:
                    return RiskDecision(allowed=False, reason=f"max_daily_loss_pct reached ({max_daily_loss_pct})")
        if max_weekly_loss_rub > 0 and weekly_loss_rub is not None:
            if weekly_loss_rub >= max_weekly_loss_rub:
                return RiskDecision(
                    allowed=False,
                    reason=f"max_weekly_loss_rub reached ({max_weekly_loss_rub})",
                )
        # Weekly percent limit (only if *_rub is not set)
        if (
            max_weekly_loss_rub <= 0
            and max_weekly_loss_pct is not None
            and max_weekly_loss_pct > 0
            and weekly_loss_rub is not None
            and equity_rub is not None
        ):
            pct = max_weekly_loss_pct / Decimal("100") if max_weekly_loss_pct >= Decimal("0.1") else max_weekly_loss_pct
            baseline = equity_rub + weekly_loss_rub
            limit = baseline * pct
            if limit > 0 and weekly_loss_rub >= limit:
                return RiskDecision(allowed=False, reason=f"max_weekly_loss_pct reached ({max_weekly_loss_pct})")

        if open_positions_total >= self._global.max_positions_total and current_position_qty == 0:
            return RiskDecision(
                allowed=False,
                reason=f"max_positions_total reached ({self._global.max_positions_total})",
            )

        # Determine desired target and convert to order intent delta (MVP: target_qty absolute).
        # Intraday sizing mode: if signal carries risk_stop + risk_per_trade_pct and uses target_qty as direction (+/-1),
        # compute actual target_qty from equity and per-contract risk.
        target_qty = int(signal.target_qty)
        if (
            target_qty != 0
            and abs(target_qty) == 1
            and signal.risk_stop is not None
            and equity_rub is not None
            and current_position_qty == 0
        ):
            rs = signal.risk_stop
            if rs <= 0:
                return RiskDecision(allowed=False, reason="risk_stop must be > 0 for risk sizing")

            rp = risk_per_trade_pct_override
            if rp is None:
                rp = signal.risk_per_trade_pct
            if rp is None:
                rp = (
                    Decimal(str(getattr(inst_risk, "risk_per_trade_pct", None)))
                    if getattr(inst_risk, "risk_per_trade_pct", None) is not None
                    else Decimal(str(self._global.risk_per_trade_pct))
                )
            # Normalize percent-like values: 0.5 -> 0.5%
            if rp > 0 and rp >= Decimal("0.1"):
                rp = rp / Decimal("100")
            risk_budget = equity_rub * rp
            max_qty_by_risk = int((risk_budget / rs).to_integral_value(rounding="ROUND_FLOOR"))
            if max_qty_by_risk <= 0:
                return RiskDecision(allowed=False, reason="risk budget too small for provided risk_stop")
            target_qty = (1 if target_qty > 0 else -1) * max_qty_by_risk

        delta = target_qty - int(current_position_qty)
        if delta == 0:
            return RiskDecision(allowed=False, reason="no-op: target equals current position")

        side = Side.BUY if delta > 0 else Side.SELL
        qty = abs(delta)

        # Per-instrument hard caps
        if instrument.max_position_qty is not None and abs(target_qty) > instrument.max_position_qty:
            # Cap rather than block (safe default, especially for risk-sized intraday strategies)
            target_qty = (1 if target_qty > 0 else -1) * int(instrument.max_position_qty)
            delta = target_qty - int(current_position_qty)
            if delta == 0:
                return RiskDecision(allowed=False, reason="no-op after max_position_qty cap")
            side = Side.BUY if delta > 0 else Side.SELL
            qty = abs(delta)
        if instrument.max_order_qty is not None and qty > instrument.max_order_qty:
            qty = instrument.max_order_qty

        # ATR sizing (optional): if ATR & equity are provided, cap qty by risk budget / atr.
        if signal.risk_stop is None and signal.atr is not None and equity_rub is not None:
            atr = signal.atr
            if atr > 0:
                rp = risk_per_trade_pct_override
                if rp is None:
                    # allow per-instrument override too
                    rp = (
                        Decimal(str(getattr(inst_risk, "risk_per_trade_pct", None)))
                        if getattr(inst_risk, "risk_per_trade_pct", None) is not None
                        else Decimal(str(self._global.risk_per_trade_pct))
                    )
                # Normalize percent-like values: 0.5 -> 0.5%
                if rp > 0 and rp >= Decimal("0.1"):
                    rp = rp / Decimal("100")
                risk_budget = equity_rub * rp
                max_qty_by_risk = int((risk_budget / atr).to_integral_value(rounding="ROUND_FLOOR"))
                if max_qty_by_risk <= 0:
                    return RiskDecision(allowed=False, reason="risk budget too small for ATR sizing")
                qty = min(qty, max_qty_by_risk)

        if qty <= 0:
            return RiskDecision(allowed=False, reason="computed order qty <= 0")

        # Idempotency anchor:
        # - for D1 strategies signal.ts already changes daily
        # - for intraday strategies we need per-bar idempotency, so we keep original ts
        _ = moscow_tz()  # ensure tzdata is available; keep behavior consistent
        anchor_ts = signal.ts

        return RiskDecision(
            allowed=True,
            reason="allowed",
            intent=OrderIntent(
                strategy_name=signal.strategy_name,
                figi=signal.figi,
                side=side,
                intended_qty=qty,
                ts=anchor_ts,
            ),
        )

