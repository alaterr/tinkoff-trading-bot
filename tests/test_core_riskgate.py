from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from app.instruments_config.models import GlobalRiskConfig, InstrumentConfig, StrategyConfig
from app.strategies.models import StrategyName
from core.models.entities import Signal, SignalType
from core.risk.gate import RiskGate


def _instrument(**kwargs) -> InstrumentConfig:
    return InstrumentConfig(
        figi="FIGI1",
        strategy=StrategyConfig(name=StrategyName.INTERVAL, parameters={}),
        **kwargs,
    )


def test_kill_switch_blocks(tmp_path):
    kill = tmp_path / "kill.switch"
    kill.write_text("1")
    rg = RiskGate(global_risk=GlobalRiskConfig(kill_switch_file=str(kill)))
    sig = Signal(
        strategy_name="donchian_atr",
        figi="FIGI1",
        ts=datetime(2026, 1, 10, 21, 0, tzinfo=timezone.utc),
        signal_type=SignalType.TARGET_QTY,
        target_qty=1,
    )
    d = rg.check(
        signal=sig,
        instrument=_instrument(instrument_type="futures", max_order_qty=10),
        current_position_qty=0,
        open_positions_total=0,
        equity_rub=Decimal("100000"),
    )
    assert d.allowed is False
    assert "kill-switch" in d.reason


def test_max_positions_total_blocks_new_entries():
    rg = RiskGate(global_risk=GlobalRiskConfig(max_positions_total=1))
    sig = Signal(
        strategy_name="donchian_atr",
        figi="FIGI1",
        ts=datetime(2026, 1, 10, 21, 0, tzinfo=timezone.utc),
        signal_type=SignalType.TARGET_QTY,
        target_qty=1,
    )
    d = rg.check(
        signal=sig,
        instrument=_instrument(instrument_type="futures", max_order_qty=10),
        current_position_qty=0,
        open_positions_total=1,
        equity_rub=Decimal("100000"),
    )
    assert d.allowed is False
    assert "max_positions_total" in d.reason


def test_atr_sizing_caps_qty():
    rg = RiskGate(global_risk=GlobalRiskConfig(risk_per_trade_pct=0.01))
    sig = Signal(
        strategy_name="donchian_atr",
        figi="FIGI1",
        ts=datetime(2026, 1, 10, 21, 0, tzinfo=timezone.utc),
        signal_type=SignalType.TARGET_QTY,
        target_qty=100,  # big target
        atr=Decimal("1000"),
    )
    # equity=100k, risk=1% => 1k risk budget; ATR=1000 => max 1 contract
    d = rg.check(
        signal=sig,
        instrument=_instrument(instrument_type="futures", max_order_qty=1000),
        current_position_qty=0,
        open_positions_total=0,
        equity_rub=Decimal("100000"),
    )
    assert d.allowed is True
    assert d.intent is not None
    assert d.intent.intended_qty == 1

