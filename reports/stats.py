from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, List, Optional


@dataclass(frozen=True)
class EquityPoint:
    ts: str
    equity: Decimal


@dataclass(frozen=True)
class Trade:
    ts: str
    figi: str
    strategy: str
    side: str
    qty: int
    price: Decimal
    commission: Decimal
    # Per-fill realized P&L net of commission (best-effort).
    # - For opening trades it is typically -commission.
    # - For closing/reducing it includes realized move minus commission.
    pnl: Decimal = Decimal("0")


@dataclass(frozen=True)
class BacktestSummary:
    trades: int
    winrate: float
    total_pnl: Decimal
    max_drawdown: Decimal


def max_drawdown(equity: List[Decimal]) -> Decimal:
    if not equity:
        return Decimal("0")
    peak = equity[0]
    mdd = Decimal("0")
    for x in equity:
        if x > peak:
            peak = x
        dd = peak - x
        if dd > mdd:
            mdd = dd
    return mdd


def summarize(trades: List[Trade], equity_curve: List[EquityPoint]) -> BacktestSummary:
    eq = [p.equity for p in equity_curve]
    total_pnl = (eq[-1] - eq[0]) if len(eq) >= 2 else Decimal("0")

    # Winrate: by round-trip (from non-zero position back to flat).
    # We use per-fill Trade.pnl (best-effort) and segment by position reaching 0.
    pos = 0
    trip_pnl = Decimal("0")
    trips: List[Decimal] = []
    for t in trades:
        prev_pos = pos
        d = int(t.qty) if str(t.side).lower().startswith("buy") else -int(t.qty)
        pos += d
        trip_pnl += (t.pnl or Decimal("0"))
        if prev_pos != 0 and pos == 0:
            trips.append(trip_pnl)
            trip_pnl = Decimal("0")
    wins = sum(1 for p in trips if p > 0)
    winrate = float(wins / len(trips)) if trips else 0.0

    return BacktestSummary(
        trades=len(trades),
        winrate=winrate,
        total_pnl=total_pnl,
        max_drawdown=max_drawdown(eq),
    )

