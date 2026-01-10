from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from tinkoff.invest.grpc.orders_pb2 import ORDER_DIRECTION_BUY, ORDER_DIRECTION_SELL, ORDER_TYPE_MARKET

from app.client import TinkoffClient
from core.models.entities import Fill, Order, OrderIntent, OrderType, Side
from core.utils.idempotency import make_client_order_id
from storage.state_store import StateStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PlaceOrderResult:
    order: Order
    created_new: bool


class OrderManager:
    """
    Minimal OMS:
    - deterministic client_order_id
    - persist-before-send (best effort) + persist-after-send
    - dedupe on restart: if client_order_id exists in state -> do nothing

    v1 MVP only supports market orders.
    """

    def __init__(self, *, broker: TinkoffClient, store: StateStore, account_id: str):
        self._broker = broker
        self._store = store
        self._account_id = account_id

    @staticmethod
    def _map_side(side: Side) -> int:
        return ORDER_DIRECTION_BUY if side == Side.BUY else ORDER_DIRECTION_SELL

    async def place_market_order(self, intent: OrderIntent) -> PlaceOrderResult:
        client_order_id = make_client_order_id(
            strategy_name=intent.strategy_name,
            figi=intent.figi,
            side=intent.side.value,
            signal_ts=intent.ts,
            intended_qty=intent.intended_qty,
        )

        existing = self._store.get_order(client_order_id)
        if existing is not None:
            return PlaceOrderResult(order=existing, created_new=False)

        created_at = datetime.now(timezone.utc)
        order = Order(
            client_order_id=client_order_id,
            account_id=self._account_id,
            figi=intent.figi,
            side=intent.side,
            order_type=OrderType.MARKET,
            requested_qty=int(intent.intended_qty),
            created_at=created_at,
            broker_order_id=None,
            status="pending_send",
        )

        # Persist intent before sending to protect against crashes between decision and send.
        self._store.upsert_order(
            order,
            extra={
                "strategy_name": intent.strategy_name,
                "idempotency_ts": intent.ts.isoformat(),
            },
        )

        resp = await self._broker.post_order(
            order_id=client_order_id,  # stable id, not random uuid
            figi=intent.figi,
            direction=self._map_side(intent.side),
            quantity=int(intent.intended_qty),
            order_type=ORDER_TYPE_MARKET,
            account_id=self._account_id,
        )

        sent = Order(
            client_order_id=client_order_id,
            account_id=self._account_id,
            figi=intent.figi,
            side=intent.side,
            order_type=OrderType.MARKET,
            requested_qty=int(intent.intended_qty),
            created_at=created_at,
            broker_order_id=getattr(resp, "order_id", None),
            status="sent",
        )
        self._store.upsert_order(sent)
        return PlaceOrderResult(order=sent, created_new=True)

    async def refresh_order_state(self, client_order_id: str) -> Optional[Order]:
        """
        Poll broker for the latest order state and persist it.
        """
        order = self._store.get_order(client_order_id)
        if order is None:
            return None
        if order.broker_order_id is None:
            # For Tinkoff, order_id is used to query state
            broker_id = order.client_order_id
        else:
            broker_id = order.broker_order_id

        st = await self._broker.get_order_state(account_id=self._account_id, order_id=broker_id)
        updated = Order(
            client_order_id=order.client_order_id,
            account_id=order.account_id,
            figi=order.figi,
            side=order.side,
            order_type=order.order_type,
            requested_qty=order.requested_qty,
            created_at=order.created_at,
            broker_order_id=broker_id,
            status=str(st.execution_report_status),
        )
        self._store.upsert_order(updated)

        # v1: we don't parse partial fills in detail (Tinkoff has lots_executed + price fields).
        # We still record a synthetic fill on full fill where possible.
        try:
            lots_exec = int(getattr(st, "lots_executed", 0))
            if lots_exec > 0:
                price_mv = getattr(st, "executed_order_price", None)
                # Fallback: average_position_price etc are not here; keep 0 if unknown.
                px = Decimal("0")
                if price_mv is not None:
                    px = Decimal(price_mv.units) + (Decimal(price_mv.nano) / Decimal(1_000_000_000))
                self._store.add_fill(
                    Fill(
                        client_order_id=order.client_order_id,
                        broker_order_id=broker_id,
                        figi=order.figi,
                        side=order.side,
                        qty=lots_exec,
                        price=px,
                        ts=getattr(st, "order_date", datetime.now(timezone.utc)),
                    )
                )
        except Exception as e:  # noqa: BLE001
            logger.debug("Failed to parse fill for %s: %s", client_order_id, e)

        return updated

