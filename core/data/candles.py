from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Iterable, List, Optional

from t_tech.invest import CandleInterval, HistoricCandle

from app.client import TinkoffClient
from core.models.entities import Candle


def _q_to_decimal(q) -> Decimal:
    # Works for tinkoff.invest.Quotation / MoneyValue-like
    return Decimal(q.units) + (Decimal(q.nano) / Decimal(1_000_000_000))


def _hc_to_candle(figi: str, hc: HistoricCandle) -> Candle:
    return Candle(
        figi=figi,
        time=hc.time,
        open=_q_to_decimal(hc.open),
        high=_q_to_decimal(hc.high),
        low=_q_to_decimal(hc.low),
        close=_q_to_decimal(hc.close),
        volume=int(hc.volume),
    )


def _is_closed_d1(candle_close_ts: datetime, *, now_ts: datetime) -> bool:
    """
    For D1: consider candle closed if its close timestamp is strictly < now (with safety buffer).
    """
    if candle_close_ts.tzinfo is None:
        candle_close_ts = candle_close_ts.replace(tzinfo=timezone.utc)
    if now_ts.tzinfo is None:
        now_ts = now_ts.replace(tzinfo=timezone.utc)
    # Buffer to avoid edge cases around close timestamp
    return candle_close_ts <= (now_ts - timedelta(seconds=5))


@dataclass(frozen=True)
class D1FetchResult:
    candles: List[Candle]
    last_closed_ts: Optional[datetime]


class CandleRepository:
    """
    Fetches and normalizes candles. MVP uses broker historical candles endpoint.
    """

    def __init__(self, broker: TinkoffClient):
        self._broker = broker

    async def fetch_last_d1(
        self,
        *,
        figi: str,
        n: int,
        to: Optional[datetime] = None,
        gap_fill_days: int = 10,
    ) -> D1FetchResult:
        """
        Fetch last N daily candles, ensuring we return only *closed* candles.
        If broker returns gaps, we expand lookback a bit.
        """
        if n <= 0:
            return D1FetchResult(candles=[], last_closed_ts=None)

        now_ts = to or datetime.now(timezone.utc)
        # Request more than N to survive weekends/holidays + gaps
        lookback_days = max(30, n + gap_fill_days)
        from_ts = now_ts - timedelta(days=lookback_days)

        raw: List[HistoricCandle] = []
        async for hc in self._broker.get_all_candles(
            figi=figi,
            from_=from_ts,
            to=now_ts,
            interval=CandleInterval.CANDLE_INTERVAL_DAY,
        ):
            raw.append(hc)

        normalized: List[Candle] = []
        for hc in raw:
            c = _hc_to_candle(figi, hc)
            if _is_closed_d1(c.time, now_ts=now_ts):
                normalized.append(c)

        normalized.sort(key=lambda c: c.time)
        if not normalized:
            return D1FetchResult(candles=[], last_closed_ts=None)

        # Return last N closed candles (or fewer if not enough history).
        out = normalized[-n:]
        return D1FetchResult(candles=out, last_closed_ts=out[-1].time)

