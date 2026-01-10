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


def adx(candles: List[Candle], period: int) -> Optional[Decimal]:
    """
    Average Directional Index (ADX), Wilder's method.
    Returns the latest ADX value or None if insufficient history.
    """
    if period <= 0:
        raise ValueError("period must be > 0")
    # Needs at least period+1 candles for DM/TR, and period values for DX smoothing.
    if len(candles) < (2 * period) + 2:
        return None

    trs: List[Decimal] = []
    pdm: List[Decimal] = []
    ndm: List[Decimal] = []

    for i in range(1, len(candles)):
        cur = candles[i]
        prev = candles[i - 1]

        up_move = cur.high - prev.high
        down_move = prev.low - cur.low

        plus_dm = up_move if (up_move > 0 and up_move > down_move) else Decimal("0")
        minus_dm = down_move if (down_move > 0 and down_move > up_move) else Decimal("0")

        trs.append(true_range(cur, prev))
        pdm.append(plus_dm)
        ndm.append(minus_dm)

    # Wilder smoothing
    tr14 = sum(trs[:period])
    pdm14 = sum(pdm[:period])
    ndm14 = sum(ndm[:period])

    def _di(dm: Decimal, tr: Decimal) -> Decimal:
        if tr <= 0:
            return Decimal("0")
        return (Decimal("100") * dm) / tr

    dxs: List[Decimal] = []
    for i in range(period, len(trs)):
        if i > period:
            tr14 = tr14 - (tr14 / Decimal(period)) + trs[i]
            pdm14 = pdm14 - (pdm14 / Decimal(period)) + pdm[i]
            ndm14 = ndm14 - (ndm14 / Decimal(period)) + ndm[i]

        pdi = _di(pdm14, tr14)
        ndi = _di(ndm14, tr14)
        denom = pdi + ndi
        dx = (Decimal("100") * abs(pdi - ndi) / denom) if denom > 0 else Decimal("0")
        dxs.append(dx)

    if len(dxs) < period:
        return None

    adx_val = sum(dxs[:period]) / Decimal(period)
    for i in range(period, len(dxs)):
        adx_val = ((adx_val * (Decimal(period) - Decimal("1"))) + dxs[i]) / Decimal(period)
    return adx_val

