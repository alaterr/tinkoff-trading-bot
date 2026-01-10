from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Callable, List, Optional

from core.models.entities import Candle, Signal
from reports.stats import EquityPoint, Trade

from core.backtest.futures import apply_futures_fill, futures_equity


@dataclass(frozen=True)
class BacktestConfig:
    initial_equity: Decimal = Decimal("1000000")
    price_slippage_bps: Decimal = Decimal("0")  # 10 bps = 0.10%
    commission_bps: Decimal = Decimal("0")  # applied on notional (abs(qty*price))
    # Futures backtest: currency amount per +1.0 price move for 1 contract.
    # If None -> use spot-like accounting (legacy).
    futures_price_multiplier: Optional[Decimal] = None


@dataclass(frozen=True)
class BacktestResult:
    trades: List[Trade]
    equity: List[EquityPoint]


def _apply_slippage(price: Decimal, *, side: str, slippage_bps: Decimal) -> Decimal:
    if slippage_bps <= 0:
        return price
    mult = slippage_bps / Decimal("10000")
    if side == "buy":
        return price * (Decimal("1") + mult)
    return price * (Decimal("1") - mult)


def run_backtest_target_qty(
    *,
    figi: str,
    strategy_name: str,
    candles: List[Candle],
    signal_fn: Callable[[List[Candle], int], Optional[Signal]],
    cfg: BacktestConfig,
) -> BacktestResult:
    """
    Deterministic backtest for strategies returning Signal(target_qty).
    - Trades are executed at candle close with fixed slippage/commission.
    - Equity is marked to market at close.
    """
    if not candles:
        return BacktestResult(trades=[], equity=[])

    cash = cfg.initial_equity
    pos = 0
    avg_price: Optional[Decimal] = None
    trades: List[Trade] = []
    equity: List[EquityPoint] = []

    for i in range(len(candles)):
        window = candles[: i + 1]
        c = candles[i]

        sig = signal_fn(window, pos)
        if sig is not None:
            target = int(sig.target_qty)
            delta = target - pos
            if delta != 0:
                side = "buy" if delta > 0 else "sell"
                qty = abs(delta)
                fill_price = _apply_slippage(c.close, side=side, slippage_bps=cfg.price_slippage_bps)
                if cfg.futures_price_multiplier is not None:
                    pos, avg_price, cash, commission = apply_futures_fill(
                        pos=pos,
                        avg_price=avg_price,
                        cash=cash,
                        side=side,
                        qty=qty,
                        price=fill_price,
                        price_multiplier=cfg.futures_price_multiplier,
                        commission_bps=cfg.commission_bps,
                    )
                else:
                    notional = fill_price * Decimal(qty)
                    commission = (cfg.commission_bps / Decimal("10000")) * abs(notional)
                    # Cashflow: buy consumes cash; sell increases cash
                    if side == "buy":
                        cash -= notional + commission
                    else:
                        cash += notional - commission
                    pos = target

                trades.append(
                    Trade(
                        ts=c.time.isoformat(),
                        figi=figi,
                        strategy=strategy_name,
                        side=side,
                        qty=qty,
                        price=fill_price,
                        commission=commission,
                    )
                )

        # Mark-to-market
        if cfg.futures_price_multiplier is not None:
            mtm = futures_equity(
                cash=cash,
                pos=pos,
                avg_price=avg_price,
                price=c.close,
                price_multiplier=cfg.futures_price_multiplier,
            )
        else:
            mtm = cash + (Decimal(pos) * c.close)
        equity.append(EquityPoint(ts=c.time.isoformat(), equity=mtm))

    return BacktestResult(trades=trades, equity=equity)

