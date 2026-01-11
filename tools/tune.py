from __future__ import annotations

import argparse
import json
import math
import random
import zipfile
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Ensure repo root is on sys.path (so `core`, `app` imports work when running as a script).
import sys

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from core.backtest.engine import BacktestConfig, run_backtest_target_qty
from core.models.entities import Candle
from reports.stats import summarize


def _to_dt(s: str) -> datetime:
    s = str(s).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _to_decimal(v) -> Optional[Decimal]:
    if v is None:
        return None
    if isinstance(v, Decimal):
        return v
    try:
        return Decimal(str(v))
    except Exception:  # noqa: BLE001
        return None


def _load_candles_jsonl(text: str, *, figi_fallback: str) -> List[Candle]:
    out: List[Candle] = []
    for line in text.splitlines():
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


def _load_candles_csv(text: str, *, figi_fallback: str) -> List[Candle]:
    # Minimal CSV parser (header expected): ts,open,high,low,close,volume (optionally figi)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return []
    header = [h.strip() for h in lines[0].split(",")]
    idx = {name: i for i, name in enumerate(header)}

    def _get(parts: List[str], key: str) -> str:
        i = idx.get(key)
        return parts[i].strip() if i is not None and i < len(parts) else ""

    out: List[Candle] = []
    for ln in lines[1:]:
        parts = ln.split(",")
        ts = _get(parts, "ts") or _get(parts, "time")
        if not ts:
            continue
        figi = _get(parts, "figi") or figi_fallback
        out.append(
            Candle(
                figi=str(figi),
                time=_to_dt(ts),
                open=Decimal(_get(parts, "open")),
                high=Decimal(_get(parts, "high")),
                low=Decimal(_get(parts, "low")),
                close=Decimal(_get(parts, "close")),
                volume=int(Decimal(_get(parts, "volume") or "0")),
            )
        )
    out.sort(key=lambda c: c.time)
    return out


def _load_dataset(path: Path) -> Tuple[Dict[str, List[Candle]], Dict[str, Any]]:
    """
    Returns:
      - series: name -> candles
      - meta: dict (may be empty)
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(str(p))

    meta: Dict[str, Any] = {}
    series: Dict[str, List[Candle]] = {}

    if p.suffix.lower() == ".zip":
        with zipfile.ZipFile(p, "r") as z:
            # meta.json is optional
            if "meta.json" in z.namelist():
                meta = json.loads(z.read("meta.json").decode("utf-8"))
            # fallback figi for older exports
            figi_fallback = str(meta.get("figi") or "")
            for name in z.namelist():
                if not name.startswith("candles_"):
                    continue
                if name.endswith("/"):
                    continue
                raw = z.read(name).decode("utf-8")
                if name.endswith(".jsonl") or name.endswith(".ndjson"):
                    series[name] = _load_candles_jsonl(raw, figi_fallback=figi_fallback)
                elif name.endswith(".csv"):
                    series[name] = _load_candles_csv(raw, figi_fallback=figi_fallback)
    else:
        # single file
        figi_fallback = ""
        raw = p.read_text(encoding="utf-8")
        if p.suffix.lower() in {".jsonl", ".ndjson"}:
            series[p.name] = _load_candles_jsonl(raw, figi_fallback=figi_fallback)
        elif p.suffix.lower() == ".csv":
            series[p.name] = _load_candles_csv(raw, figi_fallback=figi_fallback)
        else:
            raise ValueError("Unsupported dataset format. Use .csv/.jsonl or .zip")

    if not series:
        raise ValueError("No candles_* files found in dataset")
    return series, meta


def _guess_strategy_from_name(name: str) -> Optional[str]:
    n = name.lower()
    for s in [
        "intraday_bollinger_rsi",
        "intraday_vwap_momentum",
        "donchian_atr",
        "ema_atr",
        "trend_breakout_atr",
    ]:
        if s in n:
            return s
    return None


def _select_vwap_series(series: Dict[str, List[Candle]]) -> Tuple[List[Candle], Optional[List[Candle]], str]:
    """
    Returns: (main_candles, trend_candles, main_tf)
    """
    main_name = None
    for tf in ["1min", "5min"]:
        for k in series.keys():
            if f"candles_{tf}" in k and "trend" not in k:
                main_name = k
                break
        if main_name:
            break
    if not main_name:
        # fallback: any candles_* that's not trend
        for k in series.keys():
            if k.startswith("candles_") and "trend" not in k:
                main_name = k
                break
    if not main_name:
        raise ValueError("Can't find main intraday candles in dataset")

    main_tf = "5min" if "5min" in main_name else ("1min" if "1min" in main_name else "unknown")
    trend_name = None
    for tf in ["4h", "1h"]:
        for k in series.keys():
            if f"candles_trend_{tf}" in k:
                trend_name = k
                break
        if trend_name:
            break
    return series[main_name], (series[trend_name] if trend_name else None), main_tf


def _trend_dir_mapper(trend: List[Candle], *, ema_fast: int, ema_slow: int):
    """
    Returns function(ts)->{-1,0,1} mapped from trend candles.
    """
    from app.strategies.positional.indicators import ema

    closes = [c.close for c in trend]
    ts_list = [c.time for c in trend]
    ef = ema(closes, int(ema_fast))
    es = ema(closes, int(ema_slow))

    j = 0

    def _dir(ts: datetime) -> int:
        nonlocal j
        if not ts_list or not ef or not es:
            return 0
        while (j + 1) < len(ts_list) and ts_list[j + 1] <= ts:
            j += 1
        if j >= len(ts_list) or j >= len(closes) or j >= len(ef) or j >= len(es):
            return 0
        c = closes[j]
        if c > es[j] and ef[j] > es[j]:
            return 1
        if c < es[j] and ef[j] < es[j]:
            return -1
        return 0

    return _dir


def _eval_intraday_bollinger_rsi(candles: List[Candle], params: Dict[str, Any]) -> Dict[str, Any]:
    from app.strategies.intraday.bollinger_rsi import BollingerRsiConfig, BollingerRsiStrategy, _to_decimal as br_dec

    # Universal sessions: full day (do not tune per dataset).
    full_day_sessions = (("00:00", "23:59"),)

    cfg0 = BollingerRsiConfig()
    cfg = BollingerRsiConfig(
        timeframe=str(params.get("timeframe", cfg0.timeframe)),
        bollinger_period=int(params.get("bollinger_period", cfg0.bollinger_period)),
        bollinger_std_mult=br_dec(params.get("bollinger_std_mult", cfg0.bollinger_std_mult)) or cfg0.bollinger_std_mult,
        rsi_period=int(params.get("rsi_period", cfg0.rsi_period)),
        rsi_overbought=br_dec(params.get("rsi_overbought", cfg0.rsi_overbought)) or cfg0.rsi_overbought,
        rsi_oversold=br_dec(params.get("rsi_oversold", cfg0.rsi_oversold)) or cfg0.rsi_oversold,
        atr_period=int(params.get("atr_period", cfg0.atr_period)),
        sl_atr_mult=br_dec(params.get("sl_atr_mult", cfg0.sl_atr_mult)) or cfg0.sl_atr_mult,
        tp_atr_mult=br_dec(params.get("tp_atr_mult", cfg0.tp_atr_mult)) or cfg0.tp_atr_mult,
        risk_per_trade_pct=br_dec(params.get("risk_per_trade_pct", cfg0.risk_per_trade_pct)) or cfg0.risk_per_trade_pct,
        volume_window=int(params.get("volume_window", cfg0.volume_window)),
        min_volume_ratio=br_dec(params.get("min_volume_ratio", cfg0.min_volume_ratio)) or cfg0.min_volume_ratio,
        trade_sessions=full_day_sessions,
        cooldown_bars=int(params.get("cooldown_bars", cfg0.cooldown_bars)),
    )
    st = BollingerRsiStrategy(figi=candles[0].figi if candles else "", config=cfg)

    def sig_fn(w, pos):
        return st.generate_signal(candles=w, current_position_qty=pos, strategy_name="intraday_bollinger_rsi")

    bt_cfg = BacktestConfig(
        initial_equity=Decimal("1000000"),
        price_slippage_bps=Decimal("0"),
        commission_bps=Decimal("0"),
    )
    res = run_backtest_target_qty(figi=st.figi, strategy_name="intraday_bollinger_rsi", candles=candles, signal_fn=sig_fn, cfg=bt_cfg)
    summ = summarize(res.trades, res.equity)
    return {"cfg": asdict(cfg), "summary": summ, "trades": len(res.trades)}


def _eval_intraday_vwap_momentum(
    candles: List[Candle],
    *,
    trend_candles: Optional[List[Candle]],
    params: Dict[str, Any],
) -> Dict[str, Any]:
    from app.strategies.intraday.vwap_momentum import VwapMomentumConfig, VwapMomentumStrategy, _to_decimal as vm_dec

    # Universal sessions: full day (do not tune per dataset).
    full_day_sessions = (("00:00", "23:59"),)

    cfg0 = VwapMomentumConfig()
    cfg = VwapMomentumConfig(
        timeframe=str(params.get("timeframe", cfg0.timeframe)),
        vwap_period=int(params.get("vwap_period", cfg0.vwap_period)),
        vwap_window=(int(params["vwap_window"]) if params.get("vwap_window") is not None else cfg0.vwap_window),
        ema_fast=int(params.get("ema_fast", cfg0.ema_fast)),
        ema_slow=int(params.get("ema_slow", cfg0.ema_slow)),
        trend_timeframe=(str(params["trend_timeframe"]) if params.get("trend_timeframe") is not None else cfg0.trend_timeframe),
        trend_ema_fast=int(params.get("trend_ema_fast", cfg0.trend_ema_fast)),
        trend_ema_slow=int(params.get("trend_ema_slow", cfg0.trend_ema_slow)),
        atr_period=int(params.get("atr_period", cfg0.atr_period)),
        sl_points=vm_dec(params.get("sl_points", cfg0.sl_points)),
        tp_points=vm_dec(params.get("tp_points", cfg0.tp_points)),
        atr_sl_mult=vm_dec(params.get("atr_sl_mult", cfg0.atr_sl_mult)),
        atr_tp_mult=vm_dec(params.get("atr_tp_mult", cfg0.atr_tp_mult)),
        atr_trail_mult=vm_dec(params.get("atr_trail_mult", cfg0.atr_trail_mult)),
        risk_per_trade_pct=vm_dec(params.get("risk_per_trade_pct", cfg0.risk_per_trade_pct)) or cfg0.risk_per_trade_pct,
        volume_window=int(params.get("volume_window", cfg0.volume_window)),
        min_volume_ratio=vm_dec(params.get("min_volume_ratio", cfg0.min_volume_ratio)) or cfg0.min_volume_ratio,
        trade_sessions=full_day_sessions,
        cooldown_bars=int(params.get("cooldown_bars", cfg0.cooldown_bars)),
        exit_before_session_end_minutes=int(params.get("exit_before_session_end_minutes", cfg0.exit_before_session_end_minutes)),
    )
    st = VwapMomentumStrategy(figi=candles[0].figi if candles else "", config=cfg)

    trend_fn = None
    # Enable trend filter only when config asks for it (matches UI behavior).
    if trend_candles and str(cfg.trend_timeframe or "").lower().strip() in {"1h", "4h"}:
        trend_fn = _trend_dir_mapper(
            trend_candles,
            ema_fast=int(cfg.trend_ema_fast),
            ema_slow=int(cfg.trend_ema_slow),
        )

    def sig_fn(w, pos):
        td = 0
        if trend_fn and w:
            td = trend_fn(w[-1].time)
        return st.generate_signal(
            candles=w,
            current_position_qty=pos,
            trend_direction=td,
            strategy_name="intraday_vwap_momentum",
        )

    bt_cfg = BacktestConfig(
        initial_equity=Decimal("1000000"),
        price_slippage_bps=Decimal("0"),
        commission_bps=Decimal("0"),
    )
    res = run_backtest_target_qty(figi=st.figi, strategy_name="intraday_vwap_momentum", candles=candles, signal_fn=sig_fn, cfg=bt_cfg)
    summ = summarize(res.trades, res.equity)
    return {"cfg": asdict(cfg), "summary": summ, "trades": len(res.trades)}


def _rand_float(rng: random.Random, lo: float, hi: float, step: float) -> Decimal:
    if step <= 0:
        return Decimal(str(lo))
    n = int(round((hi - lo) / step))
    k = rng.randint(0, max(0, n))
    return Decimal(str(lo + k * step))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", required=True, help="Path to dataset file (.csv/.jsonl) or export zip")
    ap.add_argument("--strategy", default=None, help="Strategy name (optional; guessed from dataset)")
    ap.add_argument("--iters", type=int, default=400, help="Random search iterations")
    ap.add_argument("--min-trades", type=int, default=3, help="Minimum number of trades to accept config")
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()

    ds_path = Path(args.dataset)
    series, meta = _load_dataset(ds_path)

    strategy = args.strategy or str(meta.get("strategy") or "") or _guess_strategy_from_name(ds_path.name)
    if not strategy:
        raise SystemExit("Can't infer strategy. Pass --strategy explicitly.")

    rng = random.Random(int(args.seed))

    # Baseline params: meta.params if available, else defaults.
    base_params: Dict[str, Any] = {}
    if isinstance(meta.get("params"), dict):
        base_params = dict(meta["params"])

    # Resolve candles for strategy
    if strategy == "intraday_bollinger_rsi":
        # prefer 5min file when present
        chosen = None
        for k in series.keys():
            if "5min" in k and "bollinger" in k:
                chosen = k
                break
        if chosen is None:
            chosen = next(iter(series.keys()))
        candles = series[chosen]
        if not candles:
            raise SystemExit("No candles loaded")
        # If timeframe not set in params, infer from filename.
        if "timeframe" not in base_params:
            base_params["timeframe"] = "5min" if "5min" in chosen else "1min" if "1min" in chosen else "5min"

        def _eval(p: Dict[str, Any]) -> Dict[str, Any]:
            return _eval_intraday_bollinger_rsi(candles, p)

        def _sample() -> Dict[str, Any]:
            tf = str(base_params.get("timeframe") or "5min")
            return {
                "timeframe": tf,
                "bollinger_period": rng.randint(10, 40),
                "bollinger_std_mult": _rand_float(rng, 1.5, 3.0, 0.1),
                "rsi_period": rng.randint(7, 21),
                "rsi_overbought": _rand_float(rng, 60, 85, 1),
                "rsi_oversold": _rand_float(rng, 15, 40, 1),
                "atr_period": rng.randint(7, 21),
                "sl_atr_mult": _rand_float(rng, 0.5, 2.5, 0.1),
                "tp_atr_mult": _rand_float(rng, 0.5, 4.0, 0.1),
                "volume_window": rng.randint(10, 60),
                "min_volume_ratio": _rand_float(rng, 1.0, 2.5, 0.1),
                "cooldown_bars": rng.randint(0, 6),
                # keep sessions from base if provided
                **({"trade_sessions": base_params["trade_sessions"]} if "trade_sessions" in base_params else {}),
            }

    elif strategy == "intraday_vwap_momentum":
        main_candles, trend_candles, main_tf = _select_vwap_series(series)
        if not main_candles:
            raise SystemExit("No candles loaded")
        if "timeframe" not in base_params:
            base_params["timeframe"] = main_tf if main_tf in {"1min", "5min"} else "5min"
        # If export zip contains trend candles, allow trend_timeframe in search, else keep None.
        has_trend = trend_candles is not None and len(trend_candles) > 50

        def _eval(p: Dict[str, Any]) -> Dict[str, Any]:
            return _eval_intraday_vwap_momentum(main_candles, trend_candles=trend_candles, params=p)

        def _sample() -> Dict[str, Any]:
            tf = str(base_params.get("timeframe") or "1min")
            # for 5min it's often better to use vwap_window(bars)
            use_vwap_window = (tf == "5min") and (rng.random() < 0.7)
            vwap_window = rng.randint(3, 30) if use_vwap_window else None
            vwap_period = rng.randint(10, 120)  # minutes
            out: Dict[str, Any] = {
                "timeframe": tf,
                "ema_fast": rng.randint(3, 15),
                "ema_slow": rng.randint(10, 60),
                "atr_period": rng.randint(7, 21),
                "volume_window": rng.randint(10, 60),
                "min_volume_ratio": _rand_float(rng, 1.0, 2.5, 0.1),
                "cooldown_bars": rng.randint(0, 6),
                "exit_before_session_end_minutes": rng.randint(0, 15),
                # stops
                "sl_points": _to_decimal(rng.choice([None, 30, 50, 80, 120])),
                "tp_points": _to_decimal(rng.choice([None, 60, 100, 160, 240])),
                # keep sessions from base if provided
                **({"trade_sessions": base_params["trade_sessions"]} if "trade_sessions" in base_params else {}),
            }
            if vwap_window is not None:
                out["vwap_window"] = int(vwap_window)
            else:
                out["vwap_period"] = int(vwap_period)

            if has_trend and rng.random() < 0.6:
                out["trend_timeframe"] = "1h" if any("1h" in k for k in series.keys()) else "4h"
                out["trend_ema_fast"] = rng.randint(5, 30)
                out["trend_ema_slow"] = rng.randint(20, 100)
            else:
                out["trend_timeframe"] = None
            return out

    else:
        raise SystemExit(f"Strategy '{strategy}' tuning is not implemented in this tool yet.")

    def _metric(res: Dict[str, Any]) -> Tuple[float, int]:
        s = res["summary"]
        win = float(getattr(s, "winrate", 0.0) or 0.0)
        trades = int(getattr(s, "trades", 0) or 0)
        return win, trades

    # Baseline
    base_res = _eval(base_params)
    base_win, base_trades = _metric(base_res)
    print(f"DATASET: {ds_path}")
    print(f"STRATEGY: {strategy}")
    print(f"BASELINE: winrate={base_win:.2%} trades={base_trades}")

    best: List[Tuple[float, int, Dict[str, Any], Dict[str, Any]]] = []

    for _ in range(int(args.iters)):
        p = _sample()
        try:
            r = _eval(p)
        except Exception:
            continue
        win, tr = _metric(r)
        if tr < int(args.min_trades):
            continue
        best.append((win, tr, p, r))

    best.sort(key=lambda x: (x[0], x[1]), reverse=True)
    best = best[:10]

    print("")
    print("TOP CONFIGS:")
    for i, (win, tr, p, r) in enumerate(best, start=1):
        summ = r["summary"]
        pnl = getattr(summ, "total_pnl", None)
        mdd = getattr(summ, "max_drawdown", None)
        print(f"{i:02d}) winrate={win:.2%} trades={tr} pnl={pnl} mdd={mdd}")
        print(json.dumps(p, ensure_ascii=False, default=str))
        print("")


if __name__ == "__main__":
    main()

