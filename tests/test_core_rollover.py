from __future__ import annotations

from datetime import date, datetime, timezone

from app.instruments_config.models import InstrumentConfig, RolloverConfig, StrategyConfig
from app.strategies.models import StrategyName
from core.futures.rollover import should_rollover


def _inst(**kwargs) -> InstrumentConfig:
    return InstrumentConfig(
        figi="CURR",
        strategy=StrategyConfig(name=StrategyName.INTERVAL, parameters={}),
        instrument_type="futures",
        rollover=RolloverConfig(enabled=True, days_before_expiry_to_roll=5, next_contract_figi="NEXT"),
        **kwargs,
    )


def test_rollover_triggers_inside_window():
    inst = _inst()
    now = datetime(2026, 1, 10, tzinfo=timezone.utc)
    expiry = date(2026, 1, 13)  # 3 days left
    d = should_rollover(instrument=inst, expiry_date=expiry, now_ts=now)
    assert d.should_roll is True
    assert d.next_figi == "NEXT"


def test_rollover_not_triggered_outside_window():
    inst = _inst()
    now = datetime(2026, 1, 10, tzinfo=timezone.utc)
    expiry = date(2026, 2, 10)  # far
    d = should_rollover(instrument=inst, expiry_date=expiry, now_ts=now)
    assert d.should_roll is False

