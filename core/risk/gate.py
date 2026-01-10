from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from app.instruments_config.models import GlobalRiskConfig, InstrumentConfig
from core.models.entities import OrderIntent, Side, Signal
from core.utils.time import floor_to_day_close, moscow_tz


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
        equity_rub: Optional[Decimal] = None,
        now_ts: Optional[datetime] = None,
    ) -> RiskDecision:
        if _file_exists(self._kill_switch):
            return RiskDecision(allowed=False, reason=f"kill-switch file present: {self._kill_switch}")

        if open_positions_total >= self._global.max_positions_total and current_position_qty == 0:
            return RiskDecision(
                allowed=False,
                reason=f"max_positions_total reached ({self._global.max_positions_total})",
            )

        # Determine desired target and convert to order intent delta (MVP: target_qty absolute).
        target_qty = int(signal.target_qty)
        delta = target_qty - int(current_position_qty)
        if delta == 0:
            return RiskDecision(allowed=False, reason="no-op: target equals current position")

        side = Side.BUY if delta > 0 else Side.SELL
        qty = abs(delta)

        # Per-instrument hard caps
        if instrument.max_position_qty is not None and abs(target_qty) > instrument.max_position_qty:
            return RiskDecision(
                allowed=False,
                reason=f"target_qty exceeds max_position_qty ({instrument.max_position_qty})",
            )
        if instrument.max_order_qty is not None and qty > instrument.max_order_qty:
            qty = instrument.max_order_qty

        # ATR sizing (optional): if ATR & equity are provided, cap qty by risk budget / atr.
        if signal.atr is not None and equity_rub is not None:
            atr = signal.atr
            if atr > 0:
                risk_budget = equity_rub * Decimal(str(self._global.risk_per_trade_pct))
                max_qty_by_risk = int((risk_budget / atr).to_integral_value(rounding="ROUND_FLOOR"))
                if max_qty_by_risk <= 0:
                    return RiskDecision(allowed=False, reason="risk budget too small for ATR sizing")
                qty = min(qty, max_qty_by_risk)

        if qty <= 0:
            return RiskDecision(allowed=False, reason="computed order qty <= 0")

        tz = moscow_tz()
        anchor_ts = floor_to_day_close(signal.ts, tz)

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

