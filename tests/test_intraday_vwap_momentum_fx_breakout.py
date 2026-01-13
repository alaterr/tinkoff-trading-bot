from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from app.strategies.intraday.vwap_momentum import VwapMomentumConfig, VwapMomentumStrategy
from app.strategies.intraday.params import parse_vwap_momentum_config
from core.models.entities import Candle


def _c(
    ts: str,
    *,
    o: str,
    h: str,
    l: str,
    c: str,
    v: int = 100,
    figi: str = "FUTSI1225000",
) -> Candle:
    return Candle(
        figi=figi,
        time=datetime.fromisoformat(ts).replace(tzinfo=timezone.utc),
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(l),
        close=Decimal(c),
        volume=int(v),
    )


def test_fx_breakout_long_entry_requires_vwap_and_donchian_breakout():
    cfg = VwapMomentumConfig(
        timeframe="5min",
        vwap_window=3,
        breakout_lookback=3,
        trend_timeframe="1h",
        trend_ema_fast=20,
        trend_ema_slow=50,
        atr_period=3,
        atr_sl_mult=Decimal("2.0"),
        atr_tp_mult=Decimal("4.0"),
        atr_trail_mult=Decimal("3.0"),
        volume_window=3,
        min_volume_ratio=Decimal("1.0"),
        trade_sessions=(("10:15", "17:00"),),
        exit_before_session_end_minutes=15,
        # make sure legacy filters don't interfere
        require_fast_slope=False,
        entry_mode="cross_or_retest",
    )
    st = VwapMomentumStrategy(figi="FUTSI1225000", config=cfg)

    # 4 candles: last closes above previous highs and above VWAP (window=3)
    candles = [
        _c("2026-01-10T07:15:00", o="100", h="101", l="99", c="100", v=100),
        _c("2026-01-10T07:20:00", o="100", h="101", l="99", c="100", v=100),
        _c("2026-01-10T07:25:00", o="100", h="101", l="99", c="100", v=100),
        _c("2026-01-10T07:30:00", o="101", h="103", l="100", c="103", v=200),
    ]

    sig = st.generate_signal(candles=candles, current_position_qty=0, trend_direction=1)
    assert sig is not None
    assert sig.target_qty == 1


def test_fx_breakout_respects_trend_filter():
    cfg = VwapMomentumConfig(
        timeframe="5min",
        vwap_window=3,
        breakout_lookback=3,
        trend_timeframe="1h",
        trend_ema_fast=20,
        trend_ema_slow=50,
        atr_period=3,
        atr_sl_mult=Decimal("2.0"),
        atr_tp_mult=Decimal("4.0"),
        atr_trail_mult=Decimal("3.0"),
        volume_window=3,
        min_volume_ratio=Decimal("1.0"),
        trade_sessions=(("10:15", "17:00"),),
        exit_before_session_end_minutes=15,
        require_fast_slope=False,
    )
    st = VwapMomentumStrategy(figi="FUTSI1225000", config=cfg)
    candles = [
        _c("2026-01-10T07:15:00", o="100", h="101", l="99", c="100", v=100),
        _c("2026-01-10T07:20:00", o="100", h="101", l="99", c="100", v=100),
        _c("2026-01-10T07:25:00", o="100", h="101", l="99", c="100", v=100),
        _c("2026-01-10T07:30:00", o="101", h="103", l="100", c="103", v=200),
    ]
    # Bear trend -> long must be blocked
    sig = st.generate_signal(candles=candles, current_position_qty=0, trend_direction=-1)
    assert sig is None


def test_trend_reversal_exit():
    cfg = VwapMomentumConfig(
        timeframe="5min",
        vwap_window=3,
        breakout_lookback=3,
        trend_timeframe="1h",
        trend_ema_fast=20,
        trend_ema_slow=50,
        atr_period=3,
        atr_sl_mult=Decimal("2.0"),
        atr_tp_mult=Decimal("4.0"),
        atr_trail_mult=Decimal("3.0"),
        volume_window=3,
        min_volume_ratio=Decimal("1.0"),
        trade_sessions=(("10:15", "17:00"),),
        exit_before_session_end_minutes=15,
        require_fast_slope=False,
    )
    st = VwapMomentumStrategy(figi="FUTSI1225000", config=cfg)
    candles = [
        _c("2026-01-10T07:15:00", o="100", h="101", l="99", c="100", v=100),
        _c("2026-01-10T07:20:00", o="100", h="101", l="99", c="100", v=100),
        _c("2026-01-10T07:25:00", o="100", h="101", l="99", c="100", v=100),
        _c("2026-01-10T07:30:00", o="101", h="102", l="100", c="101", v=200),
    ]
    sig = st.generate_signal(candles=candles, current_position_qty=1, trend_direction=-1)
    assert sig is not None
    assert sig.target_qty == 0
    assert "trend reversal" in sig.reason


def test_parse_aliases_from_spec():
    cfg = parse_vwap_momentum_config(
        {
            "timeframe": "5min",
            "vwap_period": 30,  # interpreted as bars via vwap_window alias for 5min
            "breakout_lookback": 10,
            "ema_fast_1h": 20,
            "ema_slow_1h": 50,
            "atr_stop_mult": "2.0",
            "atr_tp_mult": "4.0",
            "atr_trail_mult": "3.0",
            "exit_before_close_minutes": 15,
        }
    )
    assert cfg.vwap_window == 30
    assert cfg.breakout_lookback == 10
    assert cfg.trend_ema_fast == 20
    assert cfg.trend_ema_slow == 50
    assert str(cfg.atr_sl_mult) == "2.0"
    assert str(cfg.atr_tp_mult) == "4.0"
    assert str(cfg.atr_trail_mult) == "3.0"
    assert cfg.exit_before_session_end_minutes == 15

