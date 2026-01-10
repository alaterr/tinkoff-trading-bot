from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, Tuple


def quotation_to_decimal(q) -> Optional[Decimal]:
    """
    Works for Quotation-like objects (units/nano) and returns Decimal.
    Returns None if value cannot be parsed.
    """
    if q is None:
        return None
    try:
        return Decimal(q.units) + (Decimal(q.nano) / Decimal(1_000_000_000))
    except Exception:  # noqa: BLE001
        try:
            return Decimal(str(q))
        except Exception:  # noqa: BLE001
            return None


def money_to_decimal(m) -> Optional[Decimal]:
    """
    Works for MoneyValue-like objects (units/nano) and returns Decimal.
    Returns None if value cannot be parsed.
    """
    return quotation_to_decimal(m)


@dataclass(frozen=True)
class FuturesSpec:
    """
    Minimal futures contract spec for backtesting PnL in currency.

    price_multiplier:
      currency amount per +1.0 price move for 1 contract.
      Example: if tick is 0.01 and tick value is 1 RUB, then multiplier=100 RUB per +1.0 move.
    """

    price_multiplier: Decimal = Decimal("1")
    currency: Optional[str] = None
    lot: int = 1
    min_price_increment: Optional[Decimal] = None
    min_price_increment_amount: Optional[Decimal] = None
    source: str = "default"


def futures_spec_from_instrument(inst) -> FuturesSpec:
    """
    Best-effort extraction from SDK instrument object.
    Expected (for futures):
    - min_price_increment (Quotation)
    - min_price_increment_amount (MoneyValue) == tick value in currency
    - lot (int)
    - currency (str)
    """
    if inst is None:
        return FuturesSpec()

    mpi = quotation_to_decimal(getattr(inst, "min_price_increment", None))
    mpia = money_to_decimal(getattr(inst, "min_price_increment_amount", None))
    lot = int(getattr(inst, "lot", 1) or 1)
    currency = getattr(inst, "currency", None)

    # price_multiplier = tick_value / tick_size
    mult = None
    if mpi is not None and mpia is not None and mpi > 0:
        mult = (mpia / mpi)
        # Some SDKs might define tick value per 1 lot already; we keep lot for debug only.

    return FuturesSpec(
        price_multiplier=mult if mult is not None else Decimal("1"),
        currency=str(currency) if currency else None,
        lot=lot,
        min_price_increment=mpi,
        min_price_increment_amount=mpia,
        source="instrument",
    )


def apply_futures_fill(
    *,
    pos: int,
    avg_price: Optional[Decimal],
    cash: Decimal,
    side: str,
    qty: int,
    price: Decimal,
    price_multiplier: Decimal,
    commission_bps: Decimal,
) -> Tuple[int, Optional[Decimal], Decimal, Decimal]:
    """
    Futures accounting model (simplified, deterministic):
    - No notional cashflow (only PnL + commission affect cash).
    - cash represents: initial_equity + realized_pnl - commissions_total
    - equity = cash + unrealized_pnl
    """
    if qty <= 0:
        return pos, avg_price, cash, Decimal("0")

    delta = qty if side == "buy" else -qty

    notional_for_commission = abs(price * Decimal(qty) * price_multiplier)
    commission = (commission_bps / Decimal("10000")) * notional_for_commission
    cash -= commission

    if pos == 0 or avg_price is None:
        new_pos = delta
        return new_pos, price if new_pos != 0 else None, cash, commission

    # Same direction: update weighted average
    if (pos > 0 and delta > 0) or (pos < 0 and delta < 0):
        new_pos = pos + delta
        if new_pos == 0:
            return 0, None, cash, commission
        w1 = Decimal(abs(pos))
        w2 = Decimal(abs(delta))
        new_avg = ((w1 * avg_price) + (w2 * price)) / Decimal(abs(new_pos))
        return new_pos, new_avg, cash, commission

    # Opposite direction: reduce/close/reverse
    closing = min(abs(pos), abs(delta))
    sign = Decimal("1") if pos > 0 else Decimal("-1")
    realized = (price - avg_price) * Decimal(closing) * price_multiplier * sign
    cash += realized

    remaining = abs(delta) - closing
    new_pos = pos + delta
    if remaining == 0:
        # either reduced or fully closed
        if new_pos == 0:
            return 0, None, cash, commission
        return new_pos, avg_price, cash, commission

    # Reversed: open remaining at this fill price
    new_pos = remaining if delta > 0 else -remaining
    return new_pos, price, cash, commission


def futures_equity(*, cash: Decimal, pos: int, avg_price: Optional[Decimal], price: Decimal, price_multiplier: Decimal) -> Decimal:
    if pos == 0 or avg_price is None:
        return cash
    # works for both long and short because pos is signed
    return cash + ((price - avg_price) * Decimal(pos) * price_multiplier)

