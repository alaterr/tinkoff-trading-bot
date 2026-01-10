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

    # Winrate: count positive realized PnL per *round-trip* trade is complex.
    # MVP: count profitable days where equity increased (stable + deterministic).
    wins = 0
    for i in range(1, len(eq)):
        if eq[i] > eq[i - 1]:
            wins += 1
    winrate = float(wins / (len(eq) - 1)) if len(eq) >= 2 else 0.0

    return BacktestSummary(
        trades=len(trades),
        winrate=winrate,
        total_pnl=total_pnl,
        max_drawdown=max_drawdown(eq),
    )

