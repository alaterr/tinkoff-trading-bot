from __future__ import annotations

from decimal import Decimal
from typing import Any, Optional, TYPE_CHECKING

from app.strategies.intraday.risk import norm_risk_pct

if TYPE_CHECKING:
    from app.strategies.trend_breakout_atr import TrendBreakoutATRConfig
    from core.backtest.trend_breakout_atr import TrendBreakoutParams


def _to_decimal(v) -> Optional[Decimal]:
    if v is None:
        return None
    if isinstance(v, Decimal):
        return v
    try:
        return Decimal(str(v))
    except Exception:  # noqa: BLE001
        return None


def parse_trend_breakout_atr_config(strategy_params: dict[str, Any] | None) -> "TrendBreakoutATRConfig":
    """
    Parse user-provided strategy_params into TrendBreakoutATRConfig (live runner).
    Keep parsing permissive: config json / UI may provide ints/floats/strings.
    """
    from app.strategies.trend_breakout_atr import TrendBreakoutATRConfig

    p = dict(strategy_params or {})
    cfg0 = TrendBreakoutATRConfig()
    return TrendBreakoutATRConfig(
        timeframe=str(p.get("timeframe", cfg0.timeframe)),
        breakout_lookback=int(p.get("breakout_lookback", cfg0.breakout_lookback)),
        exit_lookback=int(p.get("exit_lookback", cfg0.exit_lookback)),
        trend_ema_fast=int(p.get("trend_ema_fast", cfg0.trend_ema_fast)),
        trend_ema_slow=int(p.get("trend_ema_slow", cfg0.trend_ema_slow)),
        atr_period=int(p.get("atr_period", cfg0.atr_period)),
        atr_stop_mult=_to_decimal(p.get("atr_stop_mult", cfg0.atr_stop_mult)) or cfg0.atr_stop_mult,
        atr_tp_mult=_to_decimal(p.get("atr_tp_mult", cfg0.atr_tp_mult)) or cfg0.atr_tp_mult,
        atr_trail_mult=_to_decimal(p.get("atr_trail_mult", cfg0.atr_trail_mult)) or cfg0.atr_trail_mult,
        risk_per_trade_pct=_to_decimal(p.get("risk_per_trade_pct", cfg0.risk_per_trade_pct)) or cfg0.risk_per_trade_pct,
        exit_before_close_minutes=int(p.get("exit_before_close_minutes", cfg0.exit_before_close_minutes)),
        volume_window=int(p.get("volume_window", cfg0.volume_window)),
        min_volume_ratio=_to_decimal(p.get("min_volume_ratio", cfg0.min_volume_ratio)) or cfg0.min_volume_ratio,
        trade_sessions=tuple(tuple(x) for x in (p.get("trade_sessions") or cfg0.trade_sessions)),
        days_before_expiry_to_roll=(int(p["days_before_expiry_to_roll"]) if "days_before_expiry_to_roll" in p else None),
    )


def parse_trend_breakout_params(*, timeframe: str, strategy_params: dict[str, Any] | None) -> "TrendBreakoutParams":
    """
    Parse user-provided strategy_params into TrendBreakoutParams (core backtest).

    Important: TrendBreakoutParams.risk_per_trade_pct is "percent-like" (0.5 means 0.5%),
    but we accept both 0.5 and 0.005 styles and normalize to fraction.
    """
    from core.backtest.trend_breakout_atr import TrendBreakoutParams

    p = dict(strategy_params or {})
    # Keep defaults aligned with core/backtest defaults.
    risk_raw = _to_decimal(p.get("risk_per_trade_pct", Decimal("0.5"))) or Decimal("0.5")
    risk_frac = norm_risk_pct(risk_raw)
    return TrendBreakoutParams(
        timeframe=str(timeframe),
        breakout_lookback=int(p.get("breakout_lookback", 20)),
        exit_lookback=int(p.get("exit_lookback", 10)),
        trend_ema_fast=int(p.get("trend_ema_fast", 20)),
        trend_ema_slow=int(p.get("trend_ema_slow", 50)),
        atr_period=int(p.get("atr_period", 14)),
        atr_stop_mult=_to_decimal(p.get("atr_stop_mult", "2")) or Decimal("2"),
        atr_tp_mult=_to_decimal(p.get("atr_tp_mult", "3")) or Decimal("3"),
        atr_trail_mult=_to_decimal(p.get("atr_trail_mult", "1.5")) or Decimal("1.5"),
        risk_per_trade_pct=risk_frac,
        volume_window=int(p.get("volume_window", 20)),
        min_volume_ratio=_to_decimal(p.get("min_volume_ratio", "1.0")) or Decimal("1.0"),
        trade_sessions=tuple(tuple(x) for x in (p.get("trade_sessions") or (("10:00", "18:45"),))),
        exit_before_close_minutes=int(p.get("exit_before_close_minutes", 0)),
    )

