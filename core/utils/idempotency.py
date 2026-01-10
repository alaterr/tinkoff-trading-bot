from __future__ import annotations

import hashlib
from datetime import datetime, timezone


def _ts_key(ts: datetime) -> str:
    """
    Stable timestamp key.
    We store/derive idempotency IDs off candle-close timestamps; use UTC seconds.
    """
    if ts.tzinfo is None:
        # Assume UTC if naive (caller should pass tz-aware, but be resilient).
        ts = ts.replace(tzinfo=timezone.utc)
    ts_utc = ts.astimezone(timezone.utc)
    return ts_utc.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def make_client_order_id(
    *,
    strategy_name: str,
    figi: str,
    side: str,
    signal_ts: datetime,
    intended_qty: int,
    prefix: str = "tib",
) -> str:
    """
    Deterministic idempotency key for OMS orders.

    Inputs must uniquely define the trading intent at a given candle close.
    """
    payload = f"{strategy_name}|{figi}|{side}|{_ts_key(signal_ts)}|{int(intended_qty)}"
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()  # deterministic, compact
    # Keep reasonably short (Tinkoff client_order_id supports strings, but avoid huge ids)
    return f"{prefix}_{strategy_name}_{digest[:16]}"

