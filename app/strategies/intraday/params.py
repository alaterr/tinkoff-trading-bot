from __future__ import annotations

from decimal import Decimal
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
    # Aliases for FX intraday spec (backward compatible):
    # - ema_fast_1h/ema_slow_1h -> trend_ema_fast/trend_ema_slow
    # - atr_stop_mult -> atr_sl_mult
    # - atr_tp_mult -> atr_tp_mult (same name)
    # - exit_before_close_minutes -> exit_before_session_end_minutes
    # - vwap_period for 5min configs often means "bars" -> map to vwap_window if vwap_window is not set
    if p.get("trend_ema_fast") is None and p.get("ema_fast_1h") is not None:
        p["trend_ema_fast"] = p.get("ema_fast_1h")
    if p.get("trend_ema_slow") is None and p.get("ema_slow_1h") is not None:
        p["trend_ema_slow"] = p.get("ema_slow_1h")
    if p.get("atr_sl_mult") is None and p.get("atr_stop_mult") is not None:
        p["atr_sl_mult"] = p.get("atr_stop_mult")
    if p.get("exit_before_session_end_minutes") is None and p.get("exit_before_close_minutes") is not None:
        p["exit_before_session_end_minutes"] = p.get("exit_before_close_minutes")
    tf0 = str(p.get("timeframe", cfg0.timeframe)).lower().strip()
    if tf0 == "5min" and p.get("vwap_window") is None and p.get("vwap_period") is not None:
        # In 5m configs, vwap_period is commonly provided as "bars". Prefer explicit vwap_window, but accept this alias.
        try:
            p["vwap_window"] = int(p.get("vwap_period"))
        except Exception:
            pass

    cfg = VwapMomentumConfig(
        timeframe=str(p.get("timeframe", cfg0.timeframe)),
        vwap_period=int(p.get("vwap_period", cfg0.vwap_period)),
        vwap_window=(int(p["vwap_window"]) if p.get("vwap_window") is not None else cfg0.vwap_window),
        ema_fast=int(p.get("ema_fast", cfg0.ema_fast)),
        ema_slow=int(p.get("ema_slow", cfg0.ema_slow)),
        require_fast_slope=bool(p.get("require_fast_slope", cfg0.require_fast_slope)),
        entry_mode=str(p.get("entry_mode", cfg0.entry_mode)),
        retest_lookback=int(p.get("retest_lookback", cfg0.retest_lookback)),
        require_retest_breakout=bool(p.get("require_retest_breakout", cfg0.require_retest_breakout)),
        require_cross_breakout=bool(p.get("require_cross_breakout", cfg0.require_cross_breakout)),
        trend_timeframe=(str(p["trend_timeframe"]) if p.get("trend_timeframe") is not None else cfg0.trend_timeframe),
        trend_ema_fast=int(p.get("trend_ema_fast", cfg0.trend_ema_fast)),
        trend_ema_slow=int(p.get("trend_ema_slow", cfg0.trend_ema_slow)),
        atr_period=int(p.get("atr_period", cfg0.atr_period)),
        breakout_lookback=int(p.get("breakout_lookback", cfg0.breakout_lookback)),
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
        loss_streak_pause_after=int(p.get("loss_streak_pause_after", cfg0.loss_streak_pause_after)),
        loss_streak_cooldown_bars=int(p.get("loss_streak_cooldown_bars", cfg0.loss_streak_cooldown_bars)),
        exit_before_session_end_minutes=int(p.get("exit_before_session_end_minutes", cfg0.exit_before_session_end_minutes)),
        exit_confirm_bars=int(p.get("exit_confirm_bars", cfg0.exit_confirm_bars)),
        exit_on_vwap_cross=bool(p.get("exit_on_vwap_cross", cfg0.exit_on_vwap_cross)),
        max_hold_bars=int(p.get("max_hold_bars", cfg0.max_hold_bars)),
        atr_regime_period=int(p.get("atr_regime_period", cfg0.atr_regime_period)),
        atr_regime_min_ratio=_vm_to_decimal(p.get("atr_regime_min_ratio", cfg0.atr_regime_min_ratio))
        or cfg0.atr_regime_min_ratio,
        atr_regime_max_ratio=_vm_to_decimal(p.get("atr_regime_max_ratio", cfg0.atr_regime_max_ratio))
        or cfg0.atr_regime_max_ratio,
        adx_period=int(p.get("adx_period", cfg0.adx_period)),
        adx_min=_vm_to_decimal(p.get("adx_min", cfg0.adx_min)) or cfg0.adx_min,
        bb_period=int(p.get("bb_period", cfg0.bb_period)),
        bb_std_mult=_vm_to_decimal(p.get("bb_std_mult", cfg0.bb_std_mult)) or cfg0.bb_std_mult,
        bb_width_lookback=int(p.get("bb_width_lookback", cfg0.bb_width_lookback)),
        bb_width_mult=_vm_to_decimal(p.get("bb_width_mult", cfg0.bb_width_mult)) or cfg0.bb_width_mult,
        min_impulse_atr_mult=_vm_to_decimal(p.get("min_impulse_atr_mult", cfg0.min_impulse_atr_mult))
        or cfg0.min_impulse_atr_mult,
        min_vwap_dist_atr_mult=_vm_to_decimal(p.get("min_vwap_dist_atr_mult", cfg0.min_vwap_dist_atr_mult))
        or cfg0.min_vwap_dist_atr_mult,
        retest_touch_atr_mult=_vm_to_decimal(p.get("retest_touch_atr_mult", cfg0.retest_touch_atr_mult))
        or cfg0.retest_touch_atr_mult,
    )

    # Validation / safety checks (raise early to avoid silent misconfiguration)
    tf = str(cfg.timeframe).lower().strip()
    if tf not in {"1min", "5min"}:
        raise ValueError("vwap_momentum.timeframe must be '1min' or '5min'")
    if int(cfg.breakout_lookback) < 0:
        raise ValueError("breakout_lookback must be >= 0")
    if int(cfg.breakout_lookback) > 0 and int(cfg.breakout_lookback) < 2:
        raise ValueError("breakout_lookback too small; must be >= 2")
    if cfg.trend_timeframe is not None:
        ttf = str(cfg.trend_timeframe).lower().strip()
        if ttf not in {"1h", "4h"}:
            raise ValueError("trend_timeframe must be one of: '1h', '4h', or null")
    if int(cfg.trend_ema_fast) >= int(cfg.trend_ema_slow):
        raise ValueError("trend_ema_fast must be < trend_ema_slow (higher TF trend filter)")
    if int(cfg.atr_period) <= 0:
        raise ValueError("atr_period must be > 0")
    # ATR SL/TP sanity if used
    if cfg.atr_sl_mult is not None and cfg.atr_sl_mult > 0 and cfg.atr_sl_mult < Decimal("1"):
        raise ValueError("atr_sl_mult must be >= 1.0")
    if cfg.atr_tp_mult is not None and cfg.atr_tp_mult > 0 and cfg.atr_tp_mult < Decimal("1"):
        raise ValueError("atr_tp_mult must be >= 1.0")
    if cfg.atr_trail_mult is not None and cfg.atr_trail_mult > 0 and cfg.atr_trail_mult < Decimal("0.1"):
        raise ValueError("atr_trail_mult too small; sanity check failed")
    if int(cfg.exit_before_session_end_minutes) < 0:
        raise ValueError("exit_before_session_end_minutes must be >= 0")
    if int(cfg.loss_streak_pause_after) < 0:
        raise ValueError("loss_streak_pause_after must be >= 0")
    if int(cfg.loss_streak_cooldown_bars) < 0:
        raise ValueError("loss_streak_cooldown_bars must be >= 0")

    return cfg

