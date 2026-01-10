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
    exit_lookback: int
    trend_ema_fast: int
    trend_ema_slow: int
    atr_period: int
    atr_stop_mult: Decimal
    atr_tp_mult: Decimal
    atr_trail_mult: Decimal
    risk_per_trade_pct: Decimal  # can be 0.5 meaning 0.5%
    volume_window: int = 20
    min_volume_ratio: Decimal = Decimal("1.0")
    trade_sessions: tuple[tuple[str, str], ...] = (("10:00", "18:45"),)  # MSK
    exit_before_close_minutes: int = 0


def _to_dt(s: str) -> datetime:
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _norm_risk_pct(v: Decimal) -> Decimal:
    return v / Decimal("100") if v >= Decimal("0.1") else v


def _trend_dir_for_ts_v2(
    *,
    d1_ts: List[datetime],
    d1_closes: List[Decimal],
    ema_fast: List[Decimal],
    ema_slow: List[Decimal],
    ts: datetime,
) -> int:
    """
    Map intraday ts to last available D1 EMA filter:
    bull if close>ema_slow and ema_fast>ema_slow, bear if close<ema_slow and ema_fast<ema_slow.
    """
    if len(ema_fast) == 0 or len(ema_slow) == 0:
        return 0
    idx = None
    for i in range(len(d1_ts)):
        if d1_ts[i] <= ts:
            idx = i
        else:
            break
    if idx is None or idx >= len(d1_closes) or idx >= len(ema_fast) or idx >= len(ema_slow):
        return 0
    c = d1_closes[idx]
    ef = ema_fast[idx]
    es = ema_slow[idx]
    if c > es and ef > es:
        return 1
    if c < es and ef < es:
        return -1
    return 0


def _in_trade_session(ts: datetime, sessions: tuple[tuple[str, str], ...]) -> bool:
    try:
        from zoneinfo import ZoneInfo

        msk = ZoneInfo("Europe/Moscow")
        t = ts.astimezone(msk).time()
    except Exception:
        t = ts.time()
    for a, b in sessions:
        try:
            sh, sm = a.split(":")
            eh, em = b.split(":")
            start_t = datetime(2000, 1, 1, int(sh), int(sm)).time()
            end_t = datetime(2000, 1, 1, int(eh), int(em)).time()
        except Exception:
            continue
        if start_t <= t <= end_t:
            return True
    return False


def _minutes_to_session_end(ts: datetime, sessions: tuple[tuple[str, str], ...]) -> Optional[int]:
    try:
        from zoneinfo import ZoneInfo

        msk = ZoneInfo("Europe/Moscow")
        local = ts.astimezone(msk)
    except Exception:
        return None
    t = local.time()
    for a, b in sessions:
        try:
            eh, em = b.split(":")
            end_dt = datetime(local.year, local.month, local.day, int(eh), int(em), tzinfo=local.tzinfo)
            sh, sm = a.split(":")
            start_dt = datetime(local.year, local.month, local.day, int(sh), int(sm), tzinfo=local.tzinfo)
        except Exception:
            continue
        if start_dt.time() <= t <= end_dt.time():
            return int((end_dt - local).total_seconds() // 60)
    return None


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

    # Precompute daily EMA fast/slow (aligned to D1 candle timestamps)
    d1_closes = [c.close for c in candles_d1]
    d1_ts = [c.time for c in candles_d1]
    d1_ema_fast = ema(d1_closes, params.trend_ema_fast) if d1_closes else []
    d1_ema_slow = ema(d1_closes, params.trend_ema_slow) if d1_closes else []

    cash = cfg.initial_equity
    pos = 0
    avg_price: Optional[Decimal] = None
    entry: Optional[Decimal] = None
    stop: Optional[Decimal] = None
    tp: Optional[Decimal] = None
    peak: Optional[Decimal] = None
    trough: Optional[Decimal] = None

    trades: List[Trade] = []
    equity: List[EquityPoint] = []

    risk_frac = _norm_risk_pct(params.risk_per_trade_pct)
    pm = cfg.futures_price_multiplier if cfg.futures_price_multiplier is not None else Decimal("1")

    for i in range(len(candles_tf)):
        window = candles_tf[: i + 1]
        c = candles_tf[i]
        a = atr(window, params.atr_period)

        # update mark-to-market first (for sizing)
        mtm = futures_equity(cash=cash, pos=pos, avg_price=avg_price, price=c.close, price_multiplier=pm)

        # SL/TP check (close-based)
        if pos != 0 and entry is not None:
            exit_now = False
            exit_reason = ""

            # Trailing stop update
            if params.atr_trail_mult > 0 and a is not None and a > 0:
                if pos > 0:
                    peak = c.close if peak is None else max(peak, c.close)
                    trail = peak - (params.atr_trail_mult * a)
                    stop0 = entry - (params.atr_stop_mult * a)
                    stop = max(stop0, trail) if stop is not None else max(stop0, trail)
                else:
                    trough = c.close if trough is None else min(trough, c.close)
                    trail = trough + (params.atr_trail_mult * a)
                    stop0 = entry + (params.atr_stop_mult * a)
                    stop = min(stop0, trail) if stop is not None else min(stop0, trail)

            # Exit before session end
            mins_to_end = _minutes_to_session_end(c.time, params.trade_sessions)
            if params.exit_before_close_minutes > 0 and mins_to_end is not None and mins_to_end <= params.exit_before_close_minutes:
                exit_now, exit_reason = True, "SESSION_END"
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
                peak = trough = None
                mtm = futures_equity(cash=cash, pos=pos, avg_price=avg_price, price=c.close, price_multiplier=pm)

        # Indicator values
        if len(window) < max(params.breakout_lookback, params.atr_period) + 2:
            equity.append(EquityPoint(ts=c.time.isoformat(), equity=mtm))
            continue

        prev = window[:-1]
        upper = donchian_high(prev, params.breakout_lookback)
        lower = donchian_low(prev, params.breakout_lookback)

        trend_dir = _trend_dir_for_ts_v2(
            d1_ts=d1_ts,
            d1_closes=d1_closes,
            ema_fast=d1_ema_fast,
            ema_slow=d1_ema_slow,
            ts=c.time,
        )
        target = pos

        exit_high = donchian_high(prev, params.exit_lookback) if params.exit_lookback > 0 else None
        exit_low = donchian_low(prev, params.exit_lookback) if params.exit_lookback > 0 else None

        # Channel exit
        if pos > 0 and exit_low is not None and c.close < exit_low:
            target = 0
        elif pos < 0 and exit_high is not None and c.close > exit_high:
            target = 0

        # Entry (flat only)
        if (
            pos == 0
            and upper is not None
            and lower is not None
            and a is not None
            and trend_dir != 0
            and _in_trade_session(c.time, params.trade_sessions)
        ):
            # Volume filter
            if params.min_volume_ratio > 0 and params.volume_window > 0 and len(prev) >= params.volume_window:
                vw = prev[-params.volume_window :]
                avg_v = sum(int(x.volume) for x in vw) / float(len(vw)) if vw else 0.0
                if avg_v > 0:
                    vr = Decimal(str(int(c.volume) / avg_v))
                    if vr < params.min_volume_ratio:
                        pass
                    else:
                        # Size
                        if risk_frac > 0 and a > 0 and params.atr_stop_mult > 0:
                            risk_budget = mtm * risk_frac
                            per_contract_risk = (a * params.atr_stop_mult) * pm
                            qty = (
                                int((risk_budget / per_contract_risk).to_integral_value(rounding="ROUND_FLOOR"))
                                if per_contract_risk > 0
                                else 0
                            )
                            if qty <= 0:
                                qty = 1
                        else:
                            qty = 1

                        if c.close > upper and trend_dir > 0:
                            target = qty
                        elif c.close < lower and trend_dir < 0:
                            target = -qty
                else:
                    pass
            else:
                # If volume filter disabled or insufficient history, we allow entries only if disabled.
                if params.min_volume_ratio <= 0:
                    # Size
                    if risk_frac > 0 and a > 0 and params.atr_stop_mult > 0:
                        risk_budget = mtm * risk_frac
                        per_contract_risk = (a * params.atr_stop_mult) * pm
                        qty = (
                            int((risk_budget / per_contract_risk).to_integral_value(rounding="ROUND_FLOOR"))
                            if per_contract_risk > 0
                            else 0
                        )
                        if qty <= 0:
                            qty = 1
                    else:
                        qty = 1
                    if c.close > upper and trend_dir > 0:
                        target = qty
                    elif c.close < lower and trend_dir < 0:
                        target = -qty
            # Size
            if risk_frac > 0 and a > 0 and params.atr_stop_mult > 0:
                risk_budget = mtm * risk_frac
                stop_dist = a * params.atr_stop_mult
                per_contract_risk = stop_dist * pm
                qty = (
                    int((risk_budget / per_contract_risk).to_integral_value(rounding="ROUND_FLOOR"))
                    if per_contract_risk > 0
                    else 0
                )
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
            fill_price = _apply_slippage(c.close, side=("buy" if delta > 0 else "sell"), slippage_bps=cfg.price_slippage_bps)
            reversing = (pos != 0 and target != 0 and ((pos > 0 and target < 0) or (pos < 0 and target > 0)))
            steps = []
            if reversing:
                close_side = "sell" if pos > 0 else "buy"
                open_side = "buy" if target > 0 else "sell"
                steps = [(close_side, abs(pos)), (open_side, abs(target))]
            else:
                side = "buy" if delta > 0 else "sell"
                steps = [(side, abs(delta))]

            for side, qty in steps:
                if qty <= 0:
                    continue
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

            pos = target

            # Set levels when entering/reversing into non-zero position
            if pos != 0 and a is not None:
                entry = c.close
                if pos > 0:
                    stop = entry - (params.atr_stop_mult * a)
                    tp = entry + (params.atr_tp_mult * a)
                    peak = entry
                    trough = None
                else:
                    stop = entry + (params.atr_stop_mult * a)
                    tp = entry - (params.atr_tp_mult * a)
                    trough = entry
                    peak = None
            else:
                entry = stop = tp = None
                peak = trough = None

        mtm = futures_equity(cash=cash, pos=pos, avg_price=avg_price, price=c.close, price_multiplier=pm)
        equity.append(EquityPoint(ts=c.time.isoformat(), equity=mtm))

    return trades, equity

