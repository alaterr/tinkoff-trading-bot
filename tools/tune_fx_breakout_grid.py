from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Callable, Iterable, List, Optional

from app.strategies.intraday.params import parse_vwap_momentum_config
from app.strategies.intraday.risk import levels_vwap, update_vwap_trailing_stop
from app.strategies.intraday.vwap_momentum import VwapMomentumStrategy
from app.strategies.positional.indicators import atr as atr_ind
from core.backtest.engine import BacktestConfig, run_backtest_target_qty
from core.models.entities import Candle, Signal, SignalType
from reports.stats import summarize


def _to_dt(s: str) -> datetime:
    s = str(s).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _load_jsonl(path: Path, *, figi_fallback: str) -> List[Candle]:
    out: List[Candle] = []
    raw = Path(path).read_text(encoding="utf-8")
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        ts = row.get("ts") or row.get("time")
        if not ts:
            continue
        out.append(
            Candle(
                figi=str(row.get("figi") or figi_fallback),
                time=_to_dt(ts),
                open=Decimal(str(row.get("open"))),
                high=Decimal(str(row.get("high"))),
                low=Decimal(str(row.get("low"))),
                close=Decimal(str(row.get("close"))),
                volume=int(row.get("volume") or 0),
            )
        )
    out.sort(key=lambda c: c.time)
    return out


def _infer_timeframe_minutes(candles: List[Candle]) -> Optional[int]:
    if len(candles) < 3:
        return None
    ds: List[int] = []
    for i in range(1, min(len(candles), 51)):
        dt = candles[i].time - candles[i - 1].time
        m = int(round(dt.total_seconds() / 60))
        if m > 0:
            ds.append(m)
    if not ds:
        return None
    ones = ds.count(1)
    fives = ds.count(5)
    if ones >= fives and ones > 0:
        return 1
    if fives > 0:
        return 5
    return min(ds)


def _aggregate_candles(candles: List[Candle], *, minutes: int) -> List[Candle]:
    if minutes <= 1:
        return list(candles)
    if not candles:
        return []
    out: List[Candle] = []
    bucket: List[Candle] = []
    cur_key = None
    for c in candles:
        t = c.time
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        epoch_min = int(t.timestamp() // 60)
        key = epoch_min // int(minutes)
        if cur_key is None:
            cur_key = key
        if key != cur_key:
            if bucket:
                bucket.sort(key=lambda x: x.time)
                out.append(
                    Candle(
                        figi=bucket[-1].figi,
                        time=bucket[-1].time,
                        open=bucket[0].open,
                        high=max(x.high for x in bucket),
                        low=min(x.low for x in bucket),
                        close=bucket[-1].close,
                        volume=sum(int(x.volume) for x in bucket),
                    )
                )
            bucket = []
            cur_key = key
        bucket.append(c)
    if bucket:
        bucket.sort(key=lambda x: x.time)
        out.append(
            Candle(
                figi=bucket[-1].figi,
                time=bucket[-1].time,
                open=bucket[0].open,
                high=max(x.high for x in bucket),
                low=min(x.low for x in bucket),
                close=bucket[-1].close,
                volume=sum(int(x.volume) for x in bucket),
            )
        )
    return out


def _trend_dir_mapper(trend: List[Candle], *, ema_fast: int, ema_slow: int) -> Callable[[datetime], int]:
    """
    Match runner/offline behavior:
    compute EMA on a trailing window (reset EMA start each evaluation on last N candles).
    """
    ts_list = [c.time for c in trend]
    j = 0
    lookback = max(int(ema_fast), int(ema_slow)) + 10

    def _dir(ts: datetime) -> int:
        nonlocal j
        if not ts_list:
            return 0
        while (j + 1) < len(ts_list) and ts_list[j + 1] <= ts:
            j += 1
        eligible = trend[: j + 1]
        if not eligible:
            return 0
        eligible = eligible[-lookback:]
        return VwapMomentumStrategy.trend_dir_from_candles(
            candles=eligible,
            ema_fast_p=int(ema_fast),
            ema_slow_p=int(ema_slow),
        )

    return _dir


def _entries_per_day_utc(trades) -> int:
    """
    Approximate entries/day as count of 0->non0 position transitions per UTC date.
    """
    pos = 0
    per_day: dict[str, int] = {}
    for t in trades:
        ts = _to_dt(getattr(t, "ts"))
        day = ts.date().isoformat()
        d = int(t.qty) if str(t.side).lower().startswith("buy") else -int(t.qty)
        prev_pos = pos
        pos += d
        if prev_pos == 0 and pos != 0:
            per_day[day] = per_day.get(day, 0) + 1
    return max(per_day.values()) if per_day else 0


@dataclass(frozen=True)
class GridResult:
    breakout_lookback: int
    min_volume_ratio: Decimal
    atr_stop_mult: Decimal
    atr_tp_mult: Decimal
    atr_trail_mult: Decimal
    trades: int
    entries_per_day_max: int
    total_pnl: Decimal
    max_drawdown: Decimal
    winrate: float


def _run_one(
    *,
    figi: str,
    candles_1m: List[Candle],
    trend_1h: List[Candle],
    initial_equity: Decimal,
    breakout_lookback: int,
    min_volume_ratio: Decimal,
    atr_stop_mult: Decimal,
    atr_tp_mult: Decimal,
    atr_trail_mult: Decimal,
    max_entries_per_day: int,
) -> Optional[GridResult]:
    # FX breakout spec params (fixed except grid knobs)
    params = {
        "timeframe": "5min",
        "vwap_period": 30,  # bars for 5min (we map to vwap_window in parser)
        "breakout_lookback": int(breakout_lookback),
        "trend_timeframe": "1h",
        "ema_fast_1h": 20,
        "ema_slow_1h": 50,
        "atr_period": 14,
        "atr_stop_mult": str(atr_stop_mult),
        "atr_tp_mult": str(atr_tp_mult),
        "atr_trail_mult": str(atr_trail_mult),
        "risk_per_trade_pct": 0.4,
        "volume_window": 20,
        "min_volume_ratio": str(min_volume_ratio),
        "trade_sessions": [["10:15", "17:00"]],
        "exit_before_close_minutes": 15,
        "loss_streak_pause_after": 3,
        "loss_streak_cooldown_bars": 5,
        # deterministic/simple
        "require_fast_slope": False,
        "entry_mode": "cross_or_retest",
        "exit_on_vwap_cross": True,
        "exit_confirm_bars": 2,
    }
    cfg = parse_vwap_momentum_config(params)

    # Aggregate 1m -> 5m (deterministic)
    inferred = _infer_timeframe_minutes(candles_1m)
    candles = _aggregate_candles(candles_1m, minutes=5) if inferred == 1 else list(candles_1m)
    if not candles:
        return None

    st = VwapMomentumStrategy(figi=figi, config=cfg)
    trend_fn = _trend_dir_mapper(trend_1h, ema_fast=cfg.trend_ema_fast, ema_slow=cfg.trend_ema_slow)

    # In-memory levels (close-based) similar to runner/backtest
    stop_price: Optional[Decimal] = None
    tp_price: Optional[Decimal] = None
    peak_price: Optional[Decimal] = None
    trough_price: Optional[Decimal] = None
    entry_price: Optional[Decimal] = None

    def _clear_levels() -> None:
        nonlocal stop_price, tp_price, peak_price, trough_price, entry_price
        stop_price = None
        tp_price = None
        peak_price = None
        trough_price = None
        entry_price = None

    def _set_levels(*, entry: Decimal, atr_value: Optional[Decimal], direction: int) -> None:
        nonlocal stop_price, tp_price, peak_price, trough_price, entry_price
        lv = levels_vwap(cfg=cfg, entry_price=entry, atr_value=atr_value, tick_size=None, direction=direction)
        if lv is None:
            _clear_levels()
            return
        stop_price, tp_price = lv
        entry_price = entry
        if direction > 0:
            peak_price = entry
            trough_price = None
        else:
            trough_price = entry
            peak_price = None

    def _hit_levels(*, pos_qty: int, close: Decimal) -> bool:
        if pos_qty == 0:
            return False
        if stop_price is None and tp_price is None:
            return False
        if pos_qty > 0:
            if stop_price is not None and close <= stop_price:
                return True
            if tp_price is not None and close >= tp_price:
                return True
        else:
            if stop_price is not None and close >= stop_price:
                return True
            if tp_price is not None and close <= tp_price:
                return True
        return False

    def sig_fn(w: List[Candle], pos: int) -> Optional[Signal]:
        nonlocal stop_price, peak_price, trough_price
        last = w[-1]
        a = atr_ind(w, int(cfg.atr_period))
        td = trend_fn(last.time) if trend_fn else 0

        # trailing update always while in position (breakout activates after profit >= atr_trail_mult*ATR)
        if pos != 0:
            direction = 1 if pos > 0 else -1
            stop_price, peak_price, trough_price = update_vwap_trailing_stop(
                direction=direction,
                entry_price=entry_price,
                close=last.close,
                atr_value=a,
                atr_trail_mult=cfg.atr_trail_mult,
                activate_profit_mult=cfg.atr_trail_mult,
                stop_price=stop_price,
                peak_price=peak_price,
                trough_price=trough_price,
            )

        # close-based SL/TP exit
        if _hit_levels(pos_qty=pos, close=last.close):
            _clear_levels()
            return Signal(
                strategy_name="intraday_vwap_momentum",
                figi=figi,
                ts=last.time,
                signal_type=SignalType.TARGET_QTY,
                target_qty=0,
                reason="exit: SL/TP hit (close-based)",
                atr=a,
            )

        sig = st.generate_signal(
            candles=w,
            current_position_qty=pos,
            trend_direction=td,
            strategy_name="intraday_vwap_momentum",
        )
        if sig is None:
            return None

        tgt = int(sig.target_qty)
        if tgt == 0:
            _clear_levels()
            return sig
        if pos == 0:
            _set_levels(entry=last.close, atr_value=a, direction=(1 if tgt > 0 else -1))
        elif (pos > 0 and tgt < 0) or (pos < 0 and tgt > 0):
            _clear_levels()
            _set_levels(entry=last.close, atr_value=a, direction=(1 if tgt > 0 else -1))
        return sig

    bt_cfg = BacktestConfig(initial_equity=initial_equity)
    res = run_backtest_target_qty(figi=figi, strategy_name="intraday_vwap_momentum", candles=candles, signal_fn=sig_fn, cfg=bt_cfg)
    summ = summarize(res.trades, res.equity)
    epd = _entries_per_day_utc(res.trades)
    if max_entries_per_day > 0 and epd > max_entries_per_day:
        return None

    return GridResult(
        breakout_lookback=int(breakout_lookback),
        min_volume_ratio=Decimal(str(min_volume_ratio)),
        atr_stop_mult=Decimal(str(atr_stop_mult)),
        atr_tp_mult=Decimal(str(atr_tp_mult)),
        atr_trail_mult=Decimal(str(atr_trail_mult)),
        trades=int(getattr(summ, "trades", 0) or 0),
        entries_per_day_max=int(epd),
        total_pnl=Decimal(str(getattr(summ, "total_pnl", "0"))),
        max_drawdown=Decimal(str(getattr(summ, "max_drawdown", "0"))),
        winrate=float(getattr(summ, "winrate", 0.0) or 0.0),
    )


def _grid_int(xs: str) -> List[int]:
    return [int(x.strip()) for x in str(xs).split(",") if x.strip()]


def _grid_dec(xs: str) -> List[Decimal]:
    return [Decimal(x.strip()) for x in str(xs).split(",") if x.strip()]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--candles-file", required=True, help="Path to intraday candles (.jsonl), usually 1min")
    ap.add_argument("--trend-file", default=None, help="Path to trend candles 1h (.jsonl). Default: sibling candles_trend_1h.jsonl")
    ap.add_argument("--figi", required=True)
    ap.add_argument("--initial-equity", default="1000000")
    ap.add_argument("--out", default="backtest_reports_local/fx_breakout_grid.json")
    ap.add_argument("--max-entries-per-day", type=int, default=12)

    ap.add_argument("--breakout-lookback", default="5,10,15")
    ap.add_argument("--min-volume-ratio", default="1.0,1.2,1.5")
    ap.add_argument("--atr-stop-mult", default="1.5,2.0,2.5")
    ap.add_argument("--atr-tp-mult", default="3.0,4.0,5.0")
    ap.add_argument("--atr-trail-mult", default="2.0,3.0,4.0")
    args = ap.parse_args()

    candles_path = Path(args.candles_file)
    trend_path = Path(args.trend_file) if args.trend_file else candles_path.resolve().parent / "candles_trend_1h.jsonl"
    figi = str(args.figi)

    candles_1m = _load_jsonl(candles_path, figi_fallback=figi)
    trend_1h = _load_jsonl(trend_path, figi_fallback=figi) if trend_path.exists() else []
    if not candles_1m:
        raise SystemExit("No candles loaded")
    if not trend_1h:
        raise SystemExit(f"No trend candles loaded at {trend_path}")

    initial_equity = Decimal(str(args.initial_equity))
    max_epd = int(args.max_entries_per_day)

    lbs = _grid_int(args.breakout_lookback)
    vrs = _grid_dec(args.min_volume_ratio)
    sls = _grid_dec(args.atr_stop_mult)
    tps = _grid_dec(args.atr_tp_mult)
    trs = _grid_dec(args.atr_trail_mult)

    results: List[GridResult] = []
    total = len(lbs) * len(vrs) * len(sls) * len(tps) * len(trs)
    n = 0
    for lb in lbs:
        for vr in vrs:
            for sl in sls:
                for tp in tps:
                    for tr in trs:
                        n += 1
                        r = _run_one(
                            figi=figi,
                            candles_1m=candles_1m,
                            trend_1h=trend_1h,
                            initial_equity=initial_equity,
                            breakout_lookback=lb,
                            min_volume_ratio=vr,
                            atr_stop_mult=sl,
                            atr_tp_mult=tp,
                            atr_trail_mult=tr,
                            max_entries_per_day=max_epd,
                        )
                        if r is not None:
                            results.append(r)
                        if n % 50 == 0:
                            print(f"progress {n}/{total} kept={len(results)}")

    # Rank: primary total_pnl desc, secondary max_drawdown asc, then winrate desc
    results.sort(key=lambda x: (x.total_pnl, -x.max_drawdown, x.winrate), reverse=True)
    top = results[:15]

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "dataset": str(candles_path),
        "trend_file": str(trend_path),
        "figi": figi,
        "grid": {
            "breakout_lookback": lbs,
            "min_volume_ratio": [str(x) for x in vrs],
            "atr_stop_mult": [str(x) for x in sls],
            "atr_tp_mult": [str(x) for x in tps],
            "atr_trail_mult": [str(x) for x in trs],
            "max_entries_per_day": max_epd,
        },
        "kept": len(results),
        "top": [asdict(x) for x in top],
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    print("")
    print(f"KEPT: {len(results)} / {total}")
    print("TOP:")
    for i, r in enumerate(top, start=1):
        print(
            f"{i:02d}) pnl={r.total_pnl} mdd={r.max_drawdown} winrate={r.winrate:.2%} "
            f"epd={r.entries_per_day_max} trades={r.trades} "
            f"lb={r.breakout_lookback} vr={r.min_volume_ratio} sl={r.atr_stop_mult} tp={r.atr_tp_mult} tr={r.atr_trail_mult}"
        )


if __name__ == "__main__":
    main()

