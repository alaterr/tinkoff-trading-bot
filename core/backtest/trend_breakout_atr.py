from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Optional

from app.strategies.positional.indicators import atr, donchian_high, donchian_low, ema
from core.backtest.engine import BacktestConfig, _apply_slippage
from core.backtest.futures import apply_futures_fill, futures_equity
from core.models.entities import Candle
from reports.stats import EquityPoint, Trade


@dataclass(frozen=True)
class TrendBreakoutParams:
    timeframe: str  # "1h" | "4h" (informational in backtest; candles passed already match)
    breakout_lookback: int
    trend_lookback: int
    atr_period: int
    atr_stop_mult: Decimal
    atr_tp_mult: Decimal
    risk_per_trade_pct: Decimal  # can be "0.5" meaning 0.5%


def _to_dt(s: str) -> datetime:
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _norm_risk_pct(v: Decimal) -> Decimal:
    return v / Decimal("100") if v > 1 else v


def _trend_dir_for_ts(*, d1_ts: List[datetime], ema_vals: List[Decimal], ts: datetime) -> int:
    """
    Map intraday ts to last available D1 EMA slope.
    """
    if len(ema_vals) < 2:
        return 0
    idx = None
    for i in range(len(d1_ts)):
        if d1_ts[i] <= ts:
            idx = i
        else:
            break
    if idx is None or idx < 1 or idx >= len(ema_vals):
        return 0
    if ema_vals[idx] > ema_vals[idx - 1]:
        return 1
    if ema_vals[idx] < ema_vals[idx - 1]:
        return -1
    return 0


def run_backtest_trend_breakout_atr(
    *,
    figi: str,
    candles_tf: List[Candle],
    candles_d1: List[Candle],
    params: TrendBreakoutParams,
    cfg: BacktestConfig,
    max_position_qty: Optional[int] = None,
    max_order_qty: Optional[int] = None,
) -> tuple[List[Trade], List[EquityPoint]]:
    """
    Backtest:
    - Executes at candle close
    - SL/TP evaluated on candle close (conservative / deterministic)
    - Position sizing uses equity and ATR (best-effort, lot multiplier = 1)
    """
    if not candles_tf:
        return [], []

    # Precompute daily EMA (aligned to D1 candle timestamps)
    d1_closes = [c.close for c in candles_d1]
    d1_ts = [c.time for c in candles_d1]
    d1_ema = ema(d1_closes, params.trend_lookback) if d1_closes else []

    cash = cfg.initial_equity
    pos = 0
    avg_price: Optional[Decimal] = None
    entry: Optional[Decimal] = None
    stop: Optional[Decimal] = None
    tp: Optional[Decimal] = None

    trades: List[Trade] = []
    equity: List[EquityPoint] = []

    risk_frac = _norm_risk_pct(params.risk_per_trade_pct)
    pm = cfg.futures_price_multiplier if cfg.futures_price_multiplier is not None else Decimal("1")

    for i in range(len(candles_tf)):
        window = candles_tf[: i + 1]
        c = candles_tf[i]

        # update mark-to-market first (for sizing)
        mtm = futures_equity(cash=cash, pos=pos, avg_price=avg_price, price=c.close, price_multiplier=pm)

        # SL/TP check (close-based)
        if pos != 0 and entry is not None:
            exit_now = False
            exit_reason = ""
            if pos > 0:
                if stop is not None and c.close <= stop:
                    exit_now, exit_reason = True, "SL"
                elif tp is not None and c.close >= tp:
                    exit_now, exit_reason = True, "TP"
            else:
                if stop is not None and c.close >= stop:
                    exit_now, exit_reason = True, "SL"
                elif tp is not None and c.close <= tp:
                    exit_now, exit_reason = True, "TP"

            if exit_now:
                side = "sell" if pos > 0 else "buy"
                qty = abs(pos)
                fill_price = _apply_slippage(c.close, side=side, slippage_bps=cfg.price_slippage_bps)
                cash_before = cash
                pos, avg_price, cash, commission = apply_futures_fill(
                    pos=pos,
                    avg_price=avg_price,
                    cash=cash,
                    side=side,
                    qty=qty,
                    price=fill_price,
                    price_multiplier=pm,
                    commission_bps=cfg.commission_bps,
                )
                trades.append(
                    Trade(
                        ts=c.time.isoformat(),
                        figi=figi,
                        strategy="trend_breakout_atr",
                        side=side,
                        qty=qty,
                        price=fill_price,
                        commission=commission,
                        pnl=cash - cash_before,
                    )
                )
                entry = stop = tp = None
                mtm = futures_equity(cash=cash, pos=pos, avg_price=avg_price, price=c.close, price_multiplier=pm)

        # Indicator values
        if len(window) < max(params.breakout_lookback, params.atr_period) + 2:
            equity.append(EquityPoint(ts=c.time.isoformat(), equity=mtm))
            continue

        prev = window[:-1]
        upper = donchian_high(prev, params.breakout_lookback)
        lower = donchian_low(prev, params.breakout_lookback)
        a = atr(window, params.atr_period)

        trend_dir = _trend_dir_for_ts(d1_ts=d1_ts, ema_vals=d1_ema, ts=c.time)
        target = pos

        if upper is not None and lower is not None and a is not None and trend_dir != 0:
            # Size
            if risk_frac > 0 and a > 0 and params.atr_stop_mult > 0:
                risk_budget = mtm * risk_frac
                stop_dist = a * params.atr_stop_mult
                per_contract_risk = stop_dist * pm
                qty = int((risk_budget / per_contract_risk).to_integral_value(rounding="ROUND_FLOOR")) if per_contract_risk > 0 else 0
                if qty <= 0:
                    qty = 1
            else:
                qty = 1

            # Entry
            if c.close > upper and trend_dir > 0:
                target = qty
            elif c.close < lower and trend_dir < 0:
                target = -qty

            # Reverse
            if pos > 0 and c.close < lower and trend_dir < 0:
                target = -qty
            elif pos < 0 and c.close > upper and trend_dir > 0:
                target = qty

        if max_position_qty is not None and max_position_qty > 0:
            if target > max_position_qty:
                target = max_position_qty
            elif target < -max_position_qty:
                target = -max_position_qty

        # Execute position change
        delta = target - pos
        if max_order_qty is not None and max_order_qty > 0 and abs(delta) > max_order_qty:
            delta = max_order_qty if delta > 0 else -max_order_qty
            target = pos + delta
        if delta != 0:
            side = "buy" if delta > 0 else "sell"
            qty = abs(delta)
            fill_price = _apply_slippage(c.close, side=side, slippage_bps=cfg.price_slippage_bps)
            cash_before = cash
            pos, avg_price, cash, commission = apply_futures_fill(
                pos=pos,
                avg_price=avg_price,
                cash=cash,
                side=side,
                qty=qty,
                price=fill_price,
                price_multiplier=pm,
                commission_bps=cfg.commission_bps,
            )
            trades.append(
                Trade(
                    ts=c.time.isoformat(),
                    figi=figi,
                    strategy="trend_breakout_atr",
                    side=side,
                    qty=qty,
                    price=fill_price,
                    commission=commission,
                    pnl=cash - cash_before,
                )
            )

            # Set levels when entering/reversing into non-zero position
            if pos != 0 and a is not None:
                entry = c.close
                if pos > 0:
                    stop = entry - (params.atr_stop_mult * a)
                    tp = entry + (params.atr_tp_mult * a)
                else:
                    stop = entry + (params.atr_stop_mult * a)
                    tp = entry - (params.atr_tp_mult * a)
            else:
                entry = stop = tp = None

        mtm = futures_equity(cash=cash, pos=pos, avg_price=avg_price, price=c.close, price_multiplier=pm)
        equity.append(EquityPoint(ts=c.time.isoformat(), equity=mtm))

    return trades, equity

