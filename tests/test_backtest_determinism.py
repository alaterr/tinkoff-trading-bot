from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from core.backtest.engine import BacktestConfig, run_backtest_target_qty
from core.models.entities import Candle, Signal, SignalType


def test_backtest_is_deterministic():
    candles = [
        Candle(
            figi="F",
            time=datetime(2026, 1, 1, tzinfo=timezone.utc),
            open=Decimal("10"),
            high=Decimal("11"),
            low=Decimal("9"),
            close=Decimal("10"),
            volume=1,
        ),
        Candle(
            figi="F",
            time=datetime(2026, 1, 2, tzinfo=timezone.utc),
            open=Decimal("10"),
            high=Decimal("12"),
            low=Decimal("10"),
            close=Decimal("11"),
            volume=1,
        ),
        Candle(
            figi="F",
            time=datetime(2026, 1, 3, tzinfo=timezone.utc),
            open=Decimal("11"),
            high=Decimal("12"),
            low=Decimal("10"),
            close=Decimal("10"),
            volume=1,
        ),
    ]

    def sig_fn(window, pos):
        # Enter long on day2, exit on day3
        last = window[-1]
        if last.time.date().isoformat() == "2026-01-02":
            return Signal(
                strategy_name="s",
                figi="F",
                ts=last.time,
                signal_type=SignalType.TARGET_QTY,
                target_qty=1,
            )
        if last.time.date().isoformat() == "2026-01-03":
            return Signal(
                strategy_name="s",
                figi="F",
                ts=last.time,
                signal_type=SignalType.TARGET_QTY,
                target_qty=0,
            )
        return None

    cfg = BacktestConfig(
        initial_equity=Decimal("1000"),
        price_slippage_bps=Decimal("10"),
        commission_bps=Decimal("5"),
    )
    r1 = run_backtest_target_qty(figi="F", strategy_name="s", candles=candles, signal_fn=sig_fn, cfg=cfg)
    r2 = run_backtest_target_qty(figi="F", strategy_name="s", candles=candles, signal_fn=sig_fn, cfg=cfg)

    assert r1.trades == r2.trades
    assert r1.equity == r2.equity

