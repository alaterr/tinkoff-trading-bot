from __future__ import annotations

from typing import Any

from app.strategies.intraday.bollinger_rsi import BollingerRsiConfig, _to_decimal as _br_to_decimal
from app.strategies.intraday.vwap_momentum import VwapMomentumConfig, _to_decimal as _vm_to_decimal


def parse_bollinger_rsi_config(strategy_params: dict[str, Any] | None) -> BollingerRsiConfig:
    """
    Parse user-provided strategy_params (from instruments_config or UI) into BollingerRsiConfig.
    Keep parsing permissive: config json / UI may provide ints/floats/strings.
    """
    p = dict(strategy_params or {})
    cfg0 = BollingerRsiConfig()
    return BollingerRsiConfig(
        timeframe=str(p.get("timeframe", cfg0.timeframe)),
        bollinger_period=int(p.get("bollinger_period", cfg0.bollinger_period)),
        bollinger_std_mult=_br_to_decimal(p.get("bollinger_std_mult", cfg0.bollinger_std_mult)) or cfg0.bollinger_std_mult,
        rsi_period=int(p.get("rsi_period", cfg0.rsi_period)),
        rsi_overbought=_br_to_decimal(p.get("rsi_overbought", cfg0.rsi_overbought)) or cfg0.rsi_overbought,
        rsi_oversold=_br_to_decimal(p.get("rsi_oversold", cfg0.rsi_oversold)) or cfg0.rsi_oversold,
        atr_period=int(p.get("atr_period", cfg0.atr_period)),
        sl_atr_mult=_br_to_decimal(p.get("sl_atr_mult", cfg0.sl_atr_mult)) or cfg0.sl_atr_mult,
        tp_atr_mult=_br_to_decimal(p.get("tp_atr_mult", cfg0.tp_atr_mult)) or cfg0.tp_atr_mult,
        risk_per_trade_pct=_br_to_decimal(p.get("risk_per_trade_pct", cfg0.risk_per_trade_pct)) or cfg0.risk_per_trade_pct,
        volume_window=int(p.get("volume_window", cfg0.volume_window)),
        min_volume_ratio=_br_to_decimal(p.get("min_volume_ratio", cfg0.min_volume_ratio)) or cfg0.min_volume_ratio,
        trade_sessions=tuple(tuple(x) for x in (p.get("trade_sessions") or cfg0.trade_sessions)),
        cooldown_bars=int(p.get("cooldown_bars", cfg0.cooldown_bars)),
    )


def parse_vwap_momentum_config(strategy_params: dict[str, Any] | None) -> VwapMomentumConfig:
    """
    Parse user-provided strategy_params (from instruments_config or UI) into VwapMomentumConfig.
    Keep parsing permissive: config json / UI may provide ints/floats/strings.
    """
    p = dict(strategy_params or {})
    cfg0 = VwapMomentumConfig()
    return VwapMomentumConfig(
        timeframe=str(p.get("timeframe", cfg0.timeframe)),
        vwap_period=int(p.get("vwap_period", cfg0.vwap_period)),
        vwap_window=(int(p["vwap_window"]) if p.get("vwap_window") is not None else cfg0.vwap_window),
        ema_fast=int(p.get("ema_fast", cfg0.ema_fast)),
        ema_slow=int(p.get("ema_slow", cfg0.ema_slow)),
        require_fast_slope=bool(p.get("require_fast_slope", cfg0.require_fast_slope)),
        entry_mode=str(p.get("entry_mode", cfg0.entry_mode)),
        retest_lookback=int(p.get("retest_lookback", cfg0.retest_lookback)),
        require_retest_breakout=bool(p.get("require_retest_breakout", cfg0.require_retest_breakout)),
        trend_timeframe=(str(p["trend_timeframe"]) if p.get("trend_timeframe") is not None else cfg0.trend_timeframe),
        trend_ema_fast=int(p.get("trend_ema_fast", cfg0.trend_ema_fast)),
        trend_ema_slow=int(p.get("trend_ema_slow", cfg0.trend_ema_slow)),
        atr_period=int(p.get("atr_period", cfg0.atr_period)),
        sl_points=_vm_to_decimal(p.get("sl_points", cfg0.sl_points)),
        tp_points=_vm_to_decimal(p.get("tp_points", cfg0.tp_points)),
        atr_sl_mult=_vm_to_decimal(p.get("atr_sl_mult", cfg0.atr_sl_mult)),
        atr_tp_mult=_vm_to_decimal(p.get("atr_tp_mult", cfg0.atr_tp_mult)),
        atr_trail_mult=_vm_to_decimal(p.get("atr_trail_mult", cfg0.atr_trail_mult)),
        risk_per_trade_pct=_vm_to_decimal(p.get("risk_per_trade_pct", cfg0.risk_per_trade_pct)) or cfg0.risk_per_trade_pct,
        volume_window=int(p.get("volume_window", cfg0.volume_window)),
        min_volume_ratio=_vm_to_decimal(p.get("min_volume_ratio", cfg0.min_volume_ratio)) or cfg0.min_volume_ratio,
        trade_sessions=tuple(tuple(x) for x in (p.get("trade_sessions") or cfg0.trade_sessions)),
        cooldown_bars=int(p.get("cooldown_bars", cfg0.cooldown_bars)),
        exit_before_session_end_minutes=int(p.get("exit_before_session_end_minutes", cfg0.exit_before_session_end_minutes)),
        exit_confirm_bars=int(p.get("exit_confirm_bars", cfg0.exit_confirm_bars)),
        exit_on_vwap_cross=bool(p.get("exit_on_vwap_cross", cfg0.exit_on_vwap_cross)),
        max_hold_bars=int(p.get("max_hold_bars", cfg0.max_hold_bars)),
        atr_regime_period=int(p.get("atr_regime_period", cfg0.atr_regime_period)),
        atr_regime_min_ratio=_vm_to_decimal(p.get("atr_regime_min_ratio", cfg0.atr_regime_min_ratio))
        or cfg0.atr_regime_min_ratio,
        atr_regime_max_ratio=_vm_to_decimal(p.get("atr_regime_max_ratio", cfg0.atr_regime_max_ratio))
        or cfg0.atr_regime_max_ratio,
        min_impulse_atr_mult=_vm_to_decimal(p.get("min_impulse_atr_mult", cfg0.min_impulse_atr_mult))
        or cfg0.min_impulse_atr_mult,
        min_vwap_dist_atr_mult=_vm_to_decimal(p.get("min_vwap_dist_atr_mult", cfg0.min_vwap_dist_atr_mult))
        or cfg0.min_vwap_dist_atr_mult,
        retest_touch_atr_mult=_vm_to_decimal(p.get("retest_touch_atr_mult", cfg0.retest_touch_atr_mult))
        or cfg0.retest_touch_atr_mult,
    )

