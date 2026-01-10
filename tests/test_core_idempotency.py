from __future__ import annotations

from datetime import datetime, timezone

from core.utils.idempotency import make_client_order_id


def test_client_order_id_is_deterministic():
    ts = datetime(2026, 1, 10, 23, 59, 59, tzinfo=timezone.utc)
    a = make_client_order_id(
        strategy_name="donchian_atr",
        figi="FIGI1",
        side="buy",
        signal_ts=ts,
        intended_qty=3,
    )
    b = make_client_order_id(
        strategy_name="donchian_atr",
        figi="FIGI1",
        side="buy",
        signal_ts=ts,
        intended_qty=3,
    )
    assert a == b


def test_client_order_id_changes_with_inputs():
    ts = datetime(2026, 1, 10, 23, 59, 59, tzinfo=timezone.utc)
    base = make_client_order_id(
        strategy_name="donchian_atr",
        figi="FIGI1",
        side="buy",
        signal_ts=ts,
        intended_qty=3,
    )
    diff_qty = make_client_order_id(
        strategy_name="donchian_atr",
        figi="FIGI1",
        side="buy",
        signal_ts=ts,
        intended_qty=4,
    )
    diff_side = make_client_order_id(
        strategy_name="donchian_atr",
        figi="FIGI1",
        side="sell",
        signal_ts=ts,
        intended_qty=3,
    )
    assert base != diff_qty
    assert base != diff_side

