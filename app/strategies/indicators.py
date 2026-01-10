from __future__ import annotations

from decimal import Decimal
from typing import List, Optional

from core.models.entities import Candle


def vwap_close(candles: List[Candle], period: int) -> List[Optional[Decimal]]:
    """
    Rolling VWAP using close price:
      VWAP(t) = sum(close_i * volume_i) / sum(volume_i) over last `period` candles.

    Returns a list aligned to candles (same length), with None where insufficient data.
    """
    if period <= 0:
        raise ValueError("period must be > 0")
    if not candles:
        return []

    out: List[Optional[Decimal]] = []
    for i in range(len(candles)):
        if i + 1 < period:
            out.append(None)
            continue
        window = candles[i - period + 1 : i + 1]
        sum_v = 0
        sum_pv = Decimal("0")
        for c in window:
            v = int(getattr(c, "volume", 0) or 0)
            if v <= 0:
                continue
            sum_v += v
            sum_pv += (c.close * Decimal(v))
        out.append((sum_pv / Decimal(sum_v)) if sum_v > 0 else None)
    return out


def average_volume(candles: List[Candle], window: int, *, include_last: bool = False) -> Optional[Decimal]:
    """
    Average volume over last `window` candles.
    By default excludes the last candle (useful for volume filters on current candle).
    """
    if window <= 0:
        raise ValueError("window must be > 0")
    if not candles:
        return None

    xs = candles if include_last else candles[:-1]
    if len(xs) < window:
        return None
    w = xs[-window:]
    s = sum(int(getattr(c, "volume", 0) or 0) for c in w)
    return (Decimal(s) / Decimal(len(w))) if w else None

