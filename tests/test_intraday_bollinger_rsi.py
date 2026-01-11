from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.strategies.intraday.bollinger_rsi import BollingerRsiConfig, BollingerRsiStrategy
from app.strategies.positional.indicators import rsi, sma, std
from core.backtest.engine import BacktestConfig, run_backtest_target_qty
from core.models.entities import Candle


def _candles_5m(*, figi: str, closes: list[Decimal], volumes: list[int], start_ts: datetime) -> list[Candle]:
    assert len(closes) == len(volumes)
    out: list[Candle] = []
    ts = start_ts
    for c, v in zip(closes, volumes):
        out.append(
            Candle(
                figi=figi,
                time=ts,
                open=c,
                high=c,
                low=c,
                close=c,
                volume=v,
            )
        )
        ts = ts + timedelta(minutes=5)
    return out


def test_sma_std_basic():
    xs = [Decimal("1"), Decimal("2"), Decimal("3"), Decimal("4")]
    assert sma(xs, 2) == Decimal("3")  # (3+4)/2
    # last 2 values: [3,4], mean=3.5, var=((0.5^2+0.5^2)/2)=0.25, std=0.5
    assert std(xs, 2) == Decimal("0.5")


def test_rsi_extremes():
    up = [Decimal("1"), Decimal("2"), Decimal("3")]
    down = [Decimal("3"), Decimal("2"), Decimal("1")]
    assert rsi(up, 2) == Decimal("100")
    assert rsi(down, 2) == Decimal("0")


def test_bollinger_rsi_entry_long_signal():
    figi = "F1"
    start = datetime(2026, 1, 2, 7, 0, tzinfo=timezone.utc)  # 10:00 MSK
    candles = _candles_5m(
        figi=figi,
        closes=[Decimal("100"), Decimal("100"), Decimal("100"), Decimal("100"), Decimal("90")],
        volumes=[10, 10, 10, 10, 20],
        start_ts=start,
    )
    cfg = BollingerRsiConfig(
        timeframe="5min",
        bollinger_period=3,
        bollinger_std_mult=Decimal("1"),
        rsi_period=2,
        rsi_oversold=Decimal("30"),
        rsi_overbought=Decimal("70"),
        atr_period=2,
        sl_atr_mult=Decimal("1"),
        tp_atr_mult=Decimal("2"),
        risk_per_trade_pct=Decimal("0.3"),
        volume_window=2,
        min_volume_ratio=Decimal("1.5"),
        trade_sessions=(("10:15", "17:30"),),
        cooldown_bars=3,
    )
    st = BollingerRsiStrategy(figi=figi, config=cfg)
    sig = st.generate_signal(candles=candles, current_position_qty=0, strategy_name="intraday_bollinger_rsi")
    assert sig is not None
    assert sig.target_qty == 1
    assert sig.atr is not None and sig.atr > 0


def test_bollinger_rsi_exit_on_midline_cross():
    figi = "F1"
    start = datetime(2026, 1, 2, 7, 0, tzinfo=timezone.utc)  # 10:00 MSK
    candles = _candles_5m(
        figi=figi,
        closes=[Decimal("100"), Decimal("100"), Decimal("90"), Decimal("90"), Decimal("97")],
        volumes=[10, 10, 10, 10, 10],
        start_ts=start,
    )
    cfg = BollingerRsiConfig(
        timeframe="5min",
        bollinger_period=3,
        bollinger_std_mult=Decimal("2"),
        rsi_period=2,
        atr_period=2,
        volume_window=2,
        min_volume_ratio=Decimal("999"),  # should not affect exits
        trade_sessions=(("10:15", "17:30"),),
        cooldown_bars=3,
    )
    st = BollingerRsiStrategy(figi=figi, config=cfg)
    sig = st.generate_signal(candles=candles, current_position_qty=1, strategy_name="intraday_bollinger_rsi")
    assert sig is not None
    assert sig.target_qty == 0
    assert "mean reversion" in sig.reason


def test_backtest_runs_deterministically_on_synthetic_series():
    figi = "F1"
    start = datetime(2026, 1, 2, 7, 0, tzinfo=timezone.utc)  # 10:00 MSK
    closes = [
        Decimal("100"),
        Decimal("100"),
        Decimal("100"),
        Decimal("100"),
        Decimal("90"),  # long entry trigger
        Decimal("92"),
        Decimal("95"),
        Decimal("98"),  # cross midline -> exit
        Decimal("110"),  # short entry trigger (overbought) may happen with small params
        Decimal("105"),
        Decimal("100"),
    ]
    vols = [10] * (len(closes) - 1) + [100]
    candles = _candles_5m(figi=figi, closes=closes, volumes=vols, start_ts=start)
    cfg = BollingerRsiConfig(
        timeframe="5min",
        bollinger_period=3,
        bollinger_std_mult=Decimal("1"),
        rsi_period=2,
        rsi_overbought=Decimal("60"),
        rsi_oversold=Decimal("40"),
        atr_period=2,
        risk_per_trade_pct=Decimal("0.3"),
        volume_window=2,
        min_volume_ratio=Decimal("1.0"),
        trade_sessions=(("10:15", "17:30"),),
        cooldown_bars=1,
    )
    st = BollingerRsiStrategy(figi=figi, config=cfg)
    sig_fn = lambda w, pos: st.generate_signal(candles=w, current_position_qty=pos, strategy_name="intraday_bollinger_rsi")
    bt_cfg = BacktestConfig(initial_equity=Decimal("100000"), price_slippage_bps=Decimal("0"), commission_bps=Decimal("0"))
    r1 = run_backtest_target_qty(figi=figi, strategy_name="intraday_bollinger_rsi", candles=candles, signal_fn=sig_fn, cfg=bt_cfg)
    # Re-run with a fresh strategy instance
    st2 = BollingerRsiStrategy(figi=figi, config=cfg)
    sig_fn2 = lambda w, pos: st2.generate_signal(candles=w, current_position_qty=pos, strategy_name="intraday_bollinger_rsi")
    r2 = run_backtest_target_qty(figi=figi, strategy_name="intraday_bollinger_rsi", candles=candles, signal_fn=sig_fn2, cfg=bt_cfg)
    assert len(r1.trades) == len(r2.trades)
    assert (r1.equity[-1].equity if r1.equity else bt_cfg.initial_equity) == (
        r2.equity[-1].equity if r2.equity else bt_cfg.initial_equity
    )

