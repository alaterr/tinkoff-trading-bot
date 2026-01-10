from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"


class SignalType(str, Enum):
    # Target position (in contracts/lots). For MVP we model as absolute qty.
    TARGET_QTY = "target_qty"


@dataclass(frozen=True)
class Candle:
    figi: str
    time: datetime  # candle close timestamp
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int


@dataclass(frozen=True)
class Signal:
    strategy_name: str
    figi: str
    ts: datetime  # signal timestamp (aligned to D1 close)
    signal_type: SignalType
    target_qty: int
    reason: str = ""
    # Strategy may attach ATR-based risk hint for sizing in RiskGate.
    atr: Optional[Decimal] = None


@dataclass(frozen=True)
class OrderIntent:
    strategy_name: str
    figi: str
    side: Side
    intended_qty: int
    ts: datetime  # idempotency anchor (aligned to D1 close)


@dataclass(frozen=True)
class Order:
    client_order_id: str
    account_id: str
    figi: str
    side: Side
    order_type: OrderType
    requested_qty: int
    created_at: datetime
    broker_order_id: Optional[str] = None
    status: str = "new"  # broker-specific status normalized later


@dataclass(frozen=True)
class Fill:
    client_order_id: str
    broker_order_id: str
    figi: str
    side: Side
    qty: int
    price: Decimal
    ts: datetime


@dataclass(frozen=True)
class Position:
    figi: str
    qty: int
    avg_price: Optional[Decimal] = None
    updated_at: Optional[datetime] = None

