from __future__ import annotations

from decimal import Decimal
from decimal import getcontext
from typing import List, Optional

from core.models.entities import Candle


def sma(values: List[Decimal], period: int) -> Optional[Decimal]:
    """
    Simple moving average of the last `period` values.
    Returns None if there is not enough data.
    """
    if period <= 0:
        raise ValueError("period must be > 0")
    if len(values) < period:
        return None
    w = values[-period:]
    return sum(w) / Decimal(period)


def std(values: List[Decimal], period: int) -> Optional[Decimal]:
    """
    Population standard deviation of the last `period` values.
    Returns None if there is not enough data.
    """
    if period <= 0:
        raise ValueError("period must be > 0")
    if len(values) < period:
        return None
    w = values[-period:]
    mean = sum(w) / Decimal(period)
    var = sum((x - mean) * (x - mean) for x in w) / Decimal(period)
    # Decimal.sqrt uses current context precision
    return var.sqrt(getcontext())


def rsi(values: List[Decimal], period: int) -> Optional[Decimal]:
    """
    RSI (0..100) using simple averages over the last `period` deltas (no Wilder smoothing).
    Returns None if there is not enough data.
    """
    if period <= 0:
        raise ValueError("period must be > 0")
    if len(values) < period + 1:
        return None
    w = values[-(period + 1) :]
    gains = Decimal("0")
    losses = Decimal("0")
    for i in range(1, len(w)):
        d = w[i] - w[i - 1]
        if d > 0:
            gains += d
        elif d < 0:
            losses += (-d)
    avg_gain = gains / Decimal(period)
    avg_loss = losses / Decimal(period)
    if avg_loss == 0 and avg_gain == 0:
        return Decimal("50")
    if avg_loss == 0:
        return Decimal("100")
    if avg_gain == 0:
        return Decimal("0")
    rs = avg_gain / avg_loss
    return Decimal("100") - (Decimal("100") / (Decimal("1") + rs))


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

