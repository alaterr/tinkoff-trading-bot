from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Optional

from app.instruments_config.models import InstrumentConfig


@dataclass(frozen=True)
class RolloverDecision:
    should_roll: bool
    reason: str
    current_figi: str
    next_figi: Optional[str] = None


def should_rollover(
    *,
    instrument: InstrumentConfig,
    expiry_date: Optional[date],
    now_ts: Optional[datetime] = None,
) -> RolloverDecision:
    """
    Decide whether we should roll a futures contract.

    MVP:
    - requires config rollover.enabled=true and rollover.next_contract_figi
    - uses provided expiry_date (from broker metadata later); if missing -> do not roll
    """
    if instrument.instrument_type != "futures":
        return RolloverDecision(
            should_roll=False, reason="not a futures instrument", current_figi=instrument.figi
        )

    if not instrument.rollover.enabled:
        return RolloverDecision(
            should_roll=False, reason="rollover disabled", current_figi=instrument.figi
        )

    if not instrument.rollover.next_contract_figi:
        return RolloverDecision(
            should_roll=False,
            reason="missing next_contract_figi",
            current_figi=instrument.figi,
        )

    if expiry_date is None:
        return RolloverDecision(
            should_roll=False,
            reason="missing expiry_date (broker metadata not available yet)",
            current_figi=instrument.figi,
            next_figi=instrument.rollover.next_contract_figi,
        )

    now_ts = now_ts or datetime.now(timezone.utc)
    today = now_ts.date()
    days_left = (expiry_date - today).days
    if days_left <= instrument.rollover.days_before_expiry_to_roll:
        return RolloverDecision(
            should_roll=True,
            reason=f"expiry in {days_left}d <= {instrument.rollover.days_before_expiry_to_roll}d",
            current_figi=instrument.figi,
            next_figi=instrument.rollover.next_contract_figi,
        )

    return RolloverDecision(
        should_roll=False,
        reason=f"expiry in {days_left}d > {instrument.rollover.days_before_expiry_to_roll}d",
        current_figi=instrument.figi,
        next_figi=instrument.rollover.next_contract_figi,
    )

