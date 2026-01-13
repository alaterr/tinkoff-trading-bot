from __future__ import annotations

from decimal import Decimal
from typing import Optional, Tuple

from app.strategies.intraday.bollinger_rsi import BollingerRsiConfig
from app.strategies.intraday.vwap_momentum import VwapMomentumConfig


def norm_risk_pct(v: Decimal) -> Decimal:
    """
    Accept both 0.003 (0.3%) and 0.3 (0.3%) styles.
    """
    return v / Decimal("100") if v >= Decimal("0.1") else v


def risk_stop_rub_bollinger(
    *,
    cfg: BollingerRsiConfig,
    atr_value: Optional[Decimal],
    price_multiplier: Decimal,
    lot: Decimal,
) -> Optional[Decimal]:
    """
    Per-contract stop risk in RUB for BollingerRsi (ATR multiples).
    """
    if atr_value is None or atr_value <= 0:
        return None
    if cfg.sl_atr_mult is None or cfg.sl_atr_mult <= 0:
        return None
    return (atr_value * cfg.sl_atr_mult) * price_multiplier * lot


def vwap_stop_dist_price(
    *,
    cfg: VwapMomentumConfig,
    atr_value: Optional[Decimal],
    tick_size: Optional[Decimal],
) -> Optional[Decimal]:
    """
    Stop distance in PRICE units for VwapMomentum:
    - fixed points (interpreted as ticks if tick_size is known),
    - OR ATR-based stop.
    """
    if cfg.sl_points is not None and cfg.sl_points > 0:
        if tick_size is not None and tick_size > 0:
            return cfg.sl_points * tick_size
        return cfg.sl_points
    if cfg.atr_sl_mult is not None and cfg.atr_sl_mult > 0 and atr_value is not None and atr_value > 0:
        return atr_value * cfg.atr_sl_mult
    return None


def vwap_tp_dist_price(
    *,
    cfg: VwapMomentumConfig,
    atr_value: Optional[Decimal],
    tick_size: Optional[Decimal],
) -> Optional[Decimal]:
    """
    TP distance in PRICE units for VwapMomentum:
    - fixed points (interpreted as ticks if tick_size is known),
    - OR ATR-based TP.
    """
    if cfg.tp_points is not None and cfg.tp_points > 0:
        if tick_size is not None and tick_size > 0:
            return cfg.tp_points * tick_size
        return cfg.tp_points
    if cfg.atr_tp_mult is not None and cfg.atr_tp_mult > 0 and atr_value is not None and atr_value > 0:
        return atr_value * cfg.atr_tp_mult
    return None


def risk_stop_rub_vwap(
    *,
    cfg: VwapMomentumConfig,
    atr_value: Optional[Decimal],
    tick_size: Optional[Decimal],
    price_multiplier: Decimal,
    lot: Decimal,
) -> Optional[Decimal]:
    """
    Per-contract stop risk in RUB (best-effort) for VwapMomentum:
    - fixed points: sl_points * tick_size * price_multiplier * lot
    - ATR-based: (atr * atr_sl_mult) * price_multiplier * lot
    """
    dist = vwap_stop_dist_price(cfg=cfg, atr_value=atr_value, tick_size=tick_size)
    if dist is None or dist <= 0:
        return None
    return dist * price_multiplier * lot


def levels_bollinger(
    *,
    cfg: BollingerRsiConfig,
    entry_price: Decimal,
    atr_value: Optional[Decimal],
    direction: int,
) -> Optional[Tuple[Decimal, Decimal]]:
    """
    Return (stop_price, tp_price) for BollingerRsi using ATR multiples.
    direction: +1 long, -1 short
    """
    if direction == 0:
        return None
    if atr_value is None or atr_value <= 0:
        return None
    if cfg.sl_atr_mult is None or cfg.sl_atr_mult <= 0:
        return None
    if cfg.tp_atr_mult is None or cfg.tp_atr_mult <= 0:
        return None
    sl_dist = atr_value * cfg.sl_atr_mult
    tp_dist = atr_value * cfg.tp_atr_mult
    stop = (entry_price - sl_dist) if direction > 0 else (entry_price + sl_dist)
    tp = (entry_price + tp_dist) if direction > 0 else (entry_price - tp_dist)
    return stop, tp


def levels_vwap(
    *,
    cfg: VwapMomentumConfig,
    entry_price: Decimal,
    atr_value: Optional[Decimal],
    tick_size: Optional[Decimal],
    direction: int,
) -> Optional[Tuple[Decimal, Decimal]]:
    """
    Return (stop_price, tp_price) for VwapMomentum (points or ATR mults).
    direction: +1 long, -1 short
    """
    if direction == 0:
        return None
    stop_dist = vwap_stop_dist_price(cfg=cfg, atr_value=atr_value, tick_size=tick_size)
    tp_dist = vwap_tp_dist_price(cfg=cfg, atr_value=atr_value, tick_size=tick_size)
    if stop_dist is None or tp_dist is None or stop_dist <= 0 or tp_dist <= 0:
        return None
    stop = entry_price - stop_dist if direction > 0 else entry_price + stop_dist
    tp = entry_price + tp_dist if direction > 0 else entry_price - tp_dist
    return stop, tp


def update_vwap_trailing_stop(
    *,
    direction: int,
    entry_price: Optional[Decimal],
    close: Decimal,
    atr_value: Optional[Decimal],
    atr_trail_mult: Optional[Decimal],
    activate_profit_mult: Optional[Decimal] = None,
    stop_price: Optional[Decimal],
    peak_price: Optional[Decimal],
    trough_price: Optional[Decimal],
) -> tuple[Optional[Decimal], Optional[Decimal], Optional[Decimal]]:
    """
    ATR trailing stop update (close-based, deterministic):
    - long: peak = max(peak, close); trail = peak - atr_trail_mult*ATR; stop = max(stop, trail)
    - short: trough = min(trough, close); trail = trough + atr_trail_mult*ATR; stop = min(stop, trail)
    Returns (new_stop_price, new_peak_price, new_trough_price)
    """
    if direction == 0:
        return stop_price, peak_price, trough_price
    if atr_trail_mult is None or atr_trail_mult <= 0:
        return stop_price, peak_price, trough_price
    if atr_value is None or atr_value <= 0:
        return stop_price, peak_price, trough_price

    dist = atr_value * atr_trail_mult
    # Activation: only start moving stop once trade has at least `activate_profit_mult` * ATR unrealized profit.
    # If activate_profit_mult is None -> use atr_trail_mult (FX spec: start trailing after profit == trail distance).
    act = activate_profit_mult
    if act is None:
        act = atr_trail_mult
    if act is not None and act > 0 and entry_price is not None:
        need_profit = atr_value * act
        profit = (close - entry_price) if direction > 0 else (entry_price - close)
        if profit < need_profit:
            # Still update peak/trough bookkeeping, but do not tighten stop yet.
            if direction > 0:
                peak = close if peak_price is None else max(peak_price, close)
                return stop_price, peak, None
            trough = close if trough_price is None else min(trough_price, close)
            return stop_price, None, trough
    if direction > 0:
        peak = close if peak_price is None else max(peak_price, close)
        trail = peak - dist
        new_stop = trail if (stop_price is None or trail > stop_price) else stop_price
        return new_stop, peak, None
    trough = close if trough_price is None else min(trough_price, close)
    trail = trough + dist
    new_stop = trail if (stop_price is None or trail < stop_price) else stop_price
    return new_stop, None, trough

