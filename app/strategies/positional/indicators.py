from __future__ import annotations

from decimal import Decimal
from typing import List, Optional

from core.models.entities import Candle


def ema(values: List[Decimal], period: int) -> List[Decimal]:
    if period <= 0:
        raise ValueError("period must be > 0")
    if not values:
        return []
    alpha = Decimal("2") / (Decimal(period) + Decimal("1"))
    out: List[Decimal] = [values[0]]
    for v in values[1:]:
        out.append((alpha * v) + ((Decimal("1") - alpha) * out[-1]))
    return out


def true_range(curr: Candle, prev: Candle) -> Decimal:
    return max(
        curr.high - curr.low,
        abs(curr.high - prev.close),
        abs(curr.low - prev.close),
    )


def atr(candles: List[Candle], period: int) -> Optional[Decimal]:
    if period <= 0:
        raise ValueError("period must be > 0")
    if len(candles) < period + 1:
        return None
    trs: List[Decimal] = []
    for i in range(1, len(candles)):
        trs.append(true_range(candles[i], candles[i - 1]))
    window = trs[-period:]
    return sum(window) / Decimal(period)


def donchian_high(candles: List[Candle], lookback: int) -> Optional[Decimal]:
    if lookback <= 0:
        raise ValueError("lookback must be > 0")
    if len(candles) < lookback:
        return None
    window = candles[-lookback:]
    return max(c.high for c in window)


def donchian_low(candles: List[Candle], lookback: int) -> Optional[Decimal]:
    if lookback <= 0:
        raise ValueError("lookback must be > 0")
    if len(candles) < lookback:
        return None
    window = candles[-lookback:]
    return min(c.low for c in window)

