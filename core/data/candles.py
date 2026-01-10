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

    async def fetch_range(
        self,
        *,
        figi: str,
        from_ts: datetime,
        to_ts: datetime,
        interval: CandleInterval,
    ) -> List[Candle]:
        """
        Fetch candles in [from_ts, to_ts] and normalize to core Candle.
        Returns only candles with time <= to_ts - 5s (closed).
        """
        if from_ts.tzinfo is None:
            from_ts = from_ts.replace(tzinfo=timezone.utc)
        if to_ts.tzinfo is None:
            to_ts = to_ts.replace(tzinfo=timezone.utc)
        raw: List[HistoricCandle] = []
        async for hc in self._broker.get_all_candles(
            figi=figi,
            from_=from_ts,
            to=to_ts,
            interval=interval,
        ):
            raw.append(hc)
        out: List[Candle] = []
        for hc in raw:
            c = _hc_to_candle(figi, hc)
            if c.time <= (to_ts - timedelta(seconds=5)):
                out.append(c)
        out.sort(key=lambda c: c.time)
        return out

    async def fetch_intraday_range(
        self,
        *,
        figi: str,
        from_ts: datetime,
        to_ts: datetime,
        timeframe: str,
    ) -> List[Candle]:
        """
        Fetch intraday candles for timeframe:
        - 1h: direct
        - 4h: aggregate from 1h
        """
        tf = timeframe.lower().strip()
        if tf not in {"1h", "4h"}:
            raise ValueError("timeframe must be '1h' or '4h'")
        h1 = await self.fetch_range(
            figi=figi,
            from_ts=from_ts,
            to_ts=to_ts,
            interval=CandleInterval.CANDLE_INTERVAL_HOUR,
        )
        if tf == "1h":
            return h1

        # Aggregate to 4h buckets
        buckets: dict[datetime, list[Candle]] = {}
        for c in h1:
            t = c.time.astimezone(timezone.utc)
            bucket_start = t.replace(minute=0, second=0, microsecond=0)
            bucket_start = bucket_start.replace(hour=(bucket_start.hour // 4) * 4)
            buckets.setdefault(bucket_start, []).append(c)
        agg: List[Candle] = []
        for bucket_start in sorted(buckets.keys()):
            cs = buckets[bucket_start]
            if len(cs) < 4:
                continue
            cs.sort(key=lambda x: x.time)
            agg.append(
                Candle(
                    figi=figi,
                    time=cs[-1].time,
                    open=cs[0].open,
                    high=max(x.high for x in cs),
                    low=min(x.low for x in cs),
                    close=cs[-1].close,
                    volume=sum(int(x.volume) for x in cs),
                )
            )
        return agg

    async def fetch_last_intraday(
        self,
        *,
        figi: str,
        n: int,
        timeframe: str,
        to: Optional[datetime] = None,
        gap_fill_days: int = 10,
    ) -> D1FetchResult:
        """
        Fetch last N intraday candles (1h or 4h).
        For 4h, we aggregate from 1h candles (SDK support varies).
        Returns only closed candles.
        """
        if n <= 0:
            return D1FetchResult(candles=[], last_closed_ts=None)

        now_ts = to or datetime.now(timezone.utc)
        tf = timeframe.lower().strip()
        if tf not in {"1h", "4h"}:
            raise ValueError("timeframe must be '1h' or '4h'")

        # Request more than N to survive weekends/holidays + gaps
        hours_per_bar = 1 if tf == "1h" else 4
        lookback_hours = max(24 * 14, (n + gap_fill_days) * hours_per_bar + 24)
        from_ts = now_ts - timedelta(hours=lookback_hours)

        raw: List[HistoricCandle] = []
        async for hc in self._broker.get_all_candles(
            figi=figi,
            from_=from_ts,
            to=now_ts,
            interval=CandleInterval.CANDLE_INTERVAL_HOUR,
        ):
            raw.append(hc)

        normalized: List[Candle] = []
        for hc in raw:
            c = _hc_to_candle(figi, hc)
            # For intraday hour bars: consider closed if time < now (small buffer)
            if c.time <= (now_ts - timedelta(seconds=5)):
                normalized.append(c)

        normalized.sort(key=lambda c: c.time)
        if not normalized:
            return D1FetchResult(candles=[], last_closed_ts=None)

        if tf == "1h":
            out = normalized[-n:]
            return D1FetchResult(candles=out, last_closed_ts=out[-1].time)

        # Aggregate 1h -> 4h
        buckets: dict[datetime, list[Candle]] = {}
        for c in normalized:
            t = c.time.astimezone(timezone.utc)
            bucket_start = t.replace(minute=0, second=0, microsecond=0)
            bucket_start = bucket_start.replace(hour=(bucket_start.hour // 4) * 4)
            buckets.setdefault(bucket_start, []).append(c)

        agg: List[Candle] = []
        for bucket_start in sorted(buckets.keys()):
            cs = buckets[bucket_start]
            # Require full 4 hours (best-effort): 4 candles in the bucket.
            if len(cs) < 4:
                continue
            cs.sort(key=lambda x: x.time)
            o = cs[0].open
            h = max(x.high for x in cs)
            l = min(x.low for x in cs)
            cl = cs[-1].close
            v = sum(int(x.volume) for x in cs)
            agg.append(
                Candle(
                    figi=figi,
                    time=cs[-1].time,
                    open=o,
                    high=h,
                    low=l,
                    close=cl,
                    volume=v,
                )
            )

        if not agg:
            return D1FetchResult(candles=[], last_closed_ts=None)
        out = agg[-n:]
        return D1FetchResult(candles=out, last_closed_ts=out[-1].time)

