from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import List, Optional

from core.backtest.engine import BacktestConfig, run_backtest_target_qty
from core.models.entities import Candle, Signal, SignalType
from reports.stats import summarize
from reports.trade_log import write_equity_csv, write_summary_json, write_trades_csv
from app.strategies.positional.donchian_atr import DonchianATRStrategy, DonchianAtrConfig
from app.strategies.positional.ema_atr import EmaAtrTrendStrategy, EmaAtrConfig
from app.strategies.intraday.vwap_momentum import VwapMomentumStrategy, _to_decimal
from app.strategies.intraday.bollinger_rsi import BollingerRsiConfig, BollingerRsiStrategy

from app.strategies.intraday.params import parse_bollinger_rsi_config, parse_vwap_momentum_config
from app.strategies.intraday.risk import levels_vwap, update_vwap_trailing_stop
from app.strategies.positional.indicators import atr as atr_ind


def _parse_dt(s: str) -> datetime:
    # ISO8601 date or datetime
    if len(s) == 10:
        return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _load_candles_file(path: Path, *, figi_fallback: str) -> List[Candle]:
    """
    Load candles from file exported by UI:
    - CSV: header ts,open,high,low,close,volume (optionally figi)
    - JSONL: each line has ts/open/high/low/close/volume (optionally figi)
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(str(p))
    ext = p.suffix.lower()
    out: List[Candle] = []

    if ext == ".csv":
        with p.open("r", encoding="utf-8") as f:
            r = csv.DictReader(f)
            for row in r:
                ts = (row.get("ts") or row.get("time") or "").strip()
                if not ts:
                    continue
                figi = (row.get("figi") or figi_fallback).strip()
                out.append(
                    Candle(
                        figi=figi,
                        time=_parse_dt(ts),
                        open=Decimal(str(row.get("open"))),
                        high=Decimal(str(row.get("high"))),
                        low=Decimal(str(row.get("low"))),
                        close=Decimal(str(row.get("close"))),
                        volume=int(Decimal(str(row.get("volume") or "0"))),
                    )
                )
    elif ext in {".jsonl", ".ndjson"}:
        with p.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                ts = str(row.get("ts") or row.get("time") or "").strip()
                if not ts:
                    continue
                figi = str(row.get("figi") or figi_fallback).strip()
                out.append(
                    Candle(
                        figi=figi,
                        time=_parse_dt(ts),
                        open=Decimal(str(row.get("open"))),
                        high=Decimal(str(row.get("high"))),
                        low=Decimal(str(row.get("low"))),
                        close=Decimal(str(row.get("close"))),
                        volume=int(row.get("volume") or 0),
                    )
                )
    else:
        raise ValueError("Unsupported candles file extension. Use .csv or .jsonl/.ndjson")

    out.sort(key=lambda c: c.time)
    return out


def _infer_timeframe_minutes(candles: List[Candle]) -> Optional[int]:
    """
    Best-effort: infer bar duration (1 or 5) from first N deltas.
    Returns None if cannot infer.
    """
    if len(candles) < 3:
        return None
    ds: List[int] = []
    # use up to first 50 deltas to reduce noise from gaps
    for i in range(1, min(len(candles), 51)):
        dt = candles[i].time - candles[i - 1].time
        m = int(round(dt.total_seconds() / 60))
        if m > 0:
            ds.append(m)
    if not ds:
        return None
    # mode-like: prefer 1 or 5 if present
    ones = ds.count(1)
    fives = ds.count(5)
    if ones >= fives and ones > 0:
        return 1
    if fives > 0:
        return 5
    # fallback: return smallest positive delta
    return min(ds)


def _aggregate_candles(candles: List[Candle], *, minutes: int) -> List[Candle]:
    """
    Aggregate smaller timeframe candles into `minutes` buckets (close-time aligned, UTC).
    - bucket key is floor(ts_epoch_minutes / minutes)
    - candle.time is close timestamp of the last candle in bucket
    Deterministic, best-effort for offline backtests.
    """
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


def _maybe_load_meta_params(candles_file: Path) -> Optional[dict]:
    """
    If candles were exported into datasets/_unzipped/<FIGI>/, we typically have a meta.json nearby.
    We'll auto-load meta["params"] as default params when --params-json is not provided.
    """
    try:
        meta_path = Path(candles_file).resolve().parent / "meta.json"
        if not meta_path.exists():
            return None
        with meta_path.open("r", encoding="utf-8") as f:
            meta = json.load(f)
        params = meta.get("params")
        return dict(params) if isinstance(params, dict) else None
    except Exception:  # noqa: BLE001
        return None


def _maybe_load_trend_candles(candles_file: Path, *, figi_fallback: str) -> Optional[List[Candle]]:
    """
    Best-effort load of higher timeframe candles colocated with intraday candles.
    Expected filename: candles_trend_1h.jsonl
    """
    try:
        p = Path(candles_file).resolve().parent / "candles_trend_1h.jsonl"
        if not p.exists():
            return None
        return _load_candles_file(p, figi_fallback=figi_fallback)
    except Exception:  # noqa: BLE001
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="instruments_config.json")
    ap.add_argument("--out", default="backtest_reports")
    ap.add_argument("--figi", default=None)
    ap.add_argument("--from", dest="from_dt", default=None)
    ap.add_argument("--to", dest="to_dt", default=None)
    ap.add_argument("--initial-equity", default="1000000")
    ap.add_argument(
        "--candles-file",
        default=None,
        help="Путь к файлу свечей (CSV/JSONL), выгруженному из UI. Если задан — свечи берём из файла, без API.",
    )
    ap.add_argument(
        "--strategy",
        default=None,
        help="Имя стратегии (например intraday_vwap_momentum). Если не задано при --candles-file, попробуем угадать по имени файла.",
    )
    ap.add_argument(
        "--params-json",
        default=None,
        help="JSON со стратегическими параметрами (перекрывает параметры из instruments_config.json). Удобно для офлайн-прогона.",
    )
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Init broker client for candle download (optional if you already have cache)
    import asyncio

    async def _run():
        candles_from_file: Optional[List[Candle]] = None
        candles_trend_from_file: Optional[List[Candle]] = None
        params_override: Optional[dict] = None
        if args.params_json:
            try:
                params_override = json.loads(args.params_json)
            except Exception as e:  # noqa: BLE001
                raise SystemExit(f"--params-json должен быть валидным JSON: {e}")

        if args.candles_file:
            if not args.figi:
                raise SystemExit("--figi обязателен при использовании --candles-file")
            candles_from_file = _load_candles_file(Path(args.candles_file), figi_fallback=str(args.figi))
            candles_trend_from_file = _maybe_load_trend_candles(Path(args.candles_file), figi_fallback=str(args.figi))
            if params_override is None:
                params_override = _maybe_load_meta_params(Path(args.candles_file))
        else:
            # Heavy imports only when broker download is needed.
            from app.client import client as broker_client
            from core.data.candles import CandleRepository

            await broker_client.ainit()
            repo = CandleRepository(broker=broker_client)

        # Resolve strategy+params:
        # - If candles-file mode and --strategy provided: use that.
        # - If candles-file mode and --strategy not provided: infer from filename.
        # - Otherwise: load instruments_config.json (requires pydantic installed).
        if candles_from_file is not None:
            if not args.strategy:
                name = str(args.candles_file).lower()
                if "intraday_vwap_momentum" in name:
                    sname = "intraday_vwap_momentum"
                elif "intraday_bollinger_rsi" in name:
                    sname = "intraday_bollinger_rsi"
                else:
                    raise SystemExit("--strategy обязателен (не удалось угадать по имени файла)")
            else:
                sname = str(args.strategy).strip()

            candles = list(candles_from_file)
            to = _parse_dt(args.to_dt) if args.to_dt else (candles[-1].time if candles else datetime.now(timezone.utc))
            if args.from_dt:
                from_ts = _parse_dt(args.from_dt)
                candles = [c for c in candles if c.time >= from_ts]
            if args.to_dt:
                candles = [c for c in candles if c.time <= to]
            if not candles:
                raise SystemExit("Нет свечей после фильтрации диапазона (--from/--to)")

            # Parameters: override -> defaults
            if sname == "intraday_vwap_momentum":
                p = dict(params_override or {})
                vm_cfg = parse_vwap_momentum_config(p)

                # If strategy expects 5m bars but input is 1m, aggregate deterministically.
                want_tf = str(vm_cfg.timeframe).lower().strip()
                inferred = _infer_timeframe_minutes(candles)
                if want_tf == "5min" and inferred == 1:
                    candles = _aggregate_candles(candles, minutes=5)
                st = VwapMomentumStrategy(figi=str(args.figi), config=vm_cfg)

                # Offline mode supports deterministic SL/TP + ATR trailing similar to runner (close-based)
                # and (best-effort) higher-timeframe trend filter if candles_trend_1h.jsonl exists.
                stop_price = None
                tp_price = None
                peak_price = None
                trough_price = None
                entry_price = None

                trend_candles = list(candles_trend_from_file or [])
                trend_candles.sort(key=lambda x: x.time)
                trend_lookback = max(int(vm_cfg.trend_ema_fast), int(vm_cfg.trend_ema_slow)) + 10

                def _trend_dir(ts: datetime) -> int:
                    tf = (vm_cfg.trend_timeframe or "").lower().strip()
                    if tf not in {"1h", "4h"}:
                        return 0
                    if not trend_candles:
                        return 0
                    eligible = [c for c in trend_candles if c.time <= ts]
                    if not eligible:
                        return 0
                    eligible = eligible[-trend_lookback:]
                    return VwapMomentumStrategy.trend_dir_from_candles(
                        candles=eligible,
                        ema_fast_p=int(vm_cfg.trend_ema_fast),
                        ema_slow_p=int(vm_cfg.trend_ema_slow),
                    )

                def _hit_levels(*, pos_qty: int, close: Decimal) -> bool:
                    nonlocal stop_price, tp_price
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

                def _clear_levels() -> None:
                    nonlocal stop_price, tp_price, peak_price, trough_price, entry_price
                    stop_price = None
                    tp_price = None
                    peak_price = None
                    trough_price = None
                    entry_price = None

                def _set_levels(*, entry_price: Decimal, atr_value: Optional[Decimal], direction: int) -> None:
                    nonlocal stop_price, tp_price, peak_price, trough_price
                    lv = levels_vwap(cfg=vm_cfg, entry_price=entry_price, atr_value=atr_value, tick_size=None, direction=direction)
                    if lv is None:
                        _clear_levels()
                        return
                    stop_price, tp_price = lv
                    if direction > 0:
                        peak_price = entry_price
                        trough_price = None
                    else:
                        trough_price = entry_price
                        peak_price = None

                def sig_fn(w: List[Candle], pos: int):
                    nonlocal stop_price, tp_price, peak_price, trough_price, entry_price
                    last = w[-1]
                    a = atr_ind(w, int(vm_cfg.atr_period))

                    # Always update trailing stop while in position (runner bugfix parity).
                    if pos != 0:
                        direction = 1 if pos > 0 else -1
                        stop_price, peak_price, trough_price = update_vwap_trailing_stop(
                            direction=direction,
                            entry_price=entry_price,
                            close=last.close,
                            atr_value=a,
                            atr_trail_mult=vm_cfg.atr_trail_mult,
                            activate_profit_mult=(vm_cfg.atr_trail_mult if int(vm_cfg.breakout_lookback) > 0 else Decimal("0")),
                            stop_price=stop_price,
                            peak_price=peak_price,
                            trough_price=trough_price,
                        )

                    # 1) Close-based SL/TP exit
                    if _hit_levels(pos_qty=pos, close=last.close):
                        _clear_levels()
                        return Signal(
                            strategy_name=sname,
                            figi=str(args.figi),
                            ts=last.time,
                            signal_type=SignalType.TARGET_QTY,
                            target_qty=0,
                            reason="exit: SL/TP hit (close-based)",
                            atr=a,
                            risk_per_trade_pct=_to_decimal(vm_cfg.risk_per_trade_pct),
                            sl_points=_to_decimal(vm_cfg.sl_points),
                            tp_points=_to_decimal(vm_cfg.tp_points),
                        )

                    # 2) Strategy decision (entry/exit)
                    sig = st.generate_signal(
                        candles=w,
                        current_position_qty=pos,
                        trend_direction=_trend_dir(last.time),
                        strategy_name=sname,
                    )
                    if sig is None:
                        return None

                    # Keep levels in sync with target changes (entry/exit/reversal)
                    if int(sig.target_qty) == 0:
                        _clear_levels()
                        return sig

                    tgt = int(sig.target_qty)
                    if pos == 0:
                        entry_price = last.close
                        _set_levels(entry_price=entry_price, atr_value=a, direction=(1 if tgt > 0 else -1))
                        return sig

                    # reversal: clear old and set new
                    if (pos > 0 and tgt < 0) or (pos < 0 and tgt > 0):
                        _clear_levels()
                        entry_price = last.close
                        _set_levels(entry_price=entry_price, atr_value=a, direction=(1 if tgt > 0 else -1))
                        return sig

                    # resize in same direction (rare here) -> keep existing levels
                    return sig
            elif sname == "intraday_bollinger_rsi":
                p = dict(params_override or {})
                cfg0 = BollingerRsiConfig()
                br_cfg = BollingerRsiConfig(
                    timeframe=str(p.get("timeframe", cfg0.timeframe)),
                    bollinger_period=int(p.get("bollinger_period", cfg0.bollinger_period)),
                    bollinger_std_mult=_to_decimal(p.get("bollinger_std_mult", cfg0.bollinger_std_mult))
                    or cfg0.bollinger_std_mult,
                    rsi_period=int(p.get("rsi_period", cfg0.rsi_period)),
                    rsi_overbought=_to_decimal(p.get("rsi_overbought", cfg0.rsi_overbought)) or cfg0.rsi_overbought,
                    rsi_oversold=_to_decimal(p.get("rsi_oversold", cfg0.rsi_oversold)) or cfg0.rsi_oversold,
                    atr_period=int(p.get("atr_period", cfg0.atr_period)),
                    sl_atr_mult=_to_decimal(p.get("sl_atr_mult", cfg0.sl_atr_mult)) or cfg0.sl_atr_mult,
                    tp_atr_mult=_to_decimal(p.get("tp_atr_mult", cfg0.tp_atr_mult)) or cfg0.tp_atr_mult,
                    risk_per_trade_pct=_to_decimal(p.get("risk_per_trade_pct", cfg0.risk_per_trade_pct))
                    or cfg0.risk_per_trade_pct,
                    volume_window=int(p.get("volume_window", cfg0.volume_window)),
                    min_volume_ratio=_to_decimal(p.get("min_volume_ratio", cfg0.min_volume_ratio))
                    or cfg0.min_volume_ratio,
                    trade_sessions=tuple(tuple(x) for x in (p.get("trade_sessions") or cfg0.trade_sessions)),
                    cooldown_bars=int(p.get("cooldown_bars", cfg0.cooldown_bars)),
                )
                st = BollingerRsiStrategy(figi=str(args.figi), config=br_cfg)
                sig_fn = lambda w, pos: st.generate_signal(candles=w, current_position_qty=pos, strategy_name=sname)
            else:
                raise SystemExit(f"Стратегия '{sname}' пока не поддерживается в офлайн-режиме tools/backtest.py")

            bt_cfg = BacktestConfig(
                initial_equity=Decimal(str(args.initial_equity)),
            )
            result = run_backtest_target_qty(
                figi=str(args.figi),
                strategy_name=sname,
                candles=candles,
                signal_fn=sig_fn,
                cfg=bt_cfg,
            )
            summary = summarize(result.trades, result.equity)
            prefix = out_dir / f"{sname}_{args.figi}_offline"
            write_trades_csv(prefix.with_suffix(".trades.csv"), result.trades)
            write_equity_csv(prefix.with_suffix(".equity.csv"), result.equity)
            write_summary_json(prefix.with_suffix(".summary.json"), summary)
            print(
                f"{sname} {args.figi}: trades={summary.trades} pnl={summary.total_pnl} "
                f"mdd={summary.max_drawdown} winrate={summary.winrate:.2%}"
            )
            return

        # Online/config mode below (requires pydantic installed)
        from app.instruments_config.parser import get_instruments

        cfg = get_instruments(args.config)

        for inst in cfg.instruments:
            if args.figi and inst.figi != args.figi:
                continue

            sname = inst.strategy.name.value
            if sname not in {"donchian_atr", "ema_atr", "intraday_vwap_momentum", "intraday_bollinger_rsi"}:
                continue

            # Fetch candles (uses sdk history cache if enabled in app settings)
            # For deterministic runs, user should freeze a time interval by --to.
            to = _parse_dt(args.to_dt) if args.to_dt else datetime.now(timezone.utc)
            if sname in {"intraday_vwap_momentum", "intraday_bollinger_rsi"}:
                tf = str(inst.strategy.parameters.get("timeframe", "1min"))
                if args.from_dt:
                    from_ts = _parse_dt(args.from_dt)
                    candles = await repo.fetch_intraday_range(figi=inst.figi, from_ts=from_ts, to_ts=to, timeframe=tf)
                else:
                    # best-effort: last ~2-5 trading days depending on tf
                    candles_n = 3000 if tf.lower().strip() == "1min" else 1200
                    res = await repo.fetch_last_intraday(figi=inst.figi, n=candles_n, timeframe=tf, to=to)
                    candles = res.candles
            else:
                candles_n = 400  # enough for D1 indicators
                res = await repo.fetch_last_d1(figi=inst.figi, n=candles_n, to=to)
                candles = res.candles
            if not candles:
                continue

            # Build strategy signal function
            if sname == "donchian_atr":
                st = DonchianATRStrategy(figi=inst.figi, config=DonchianAtrConfig(**inst.strategy.parameters))
                sig_fn = lambda w, pos: st.generate_signal(candles=w, current_position_qty=pos, strategy_name=sname)
            else:
                if sname == "ema_atr":
                    st = EmaAtrTrendStrategy(figi=inst.figi, config=EmaAtrConfig(**inst.strategy.parameters))
                    sig_fn = lambda w, pos: st.generate_signal(
                        candles=w, current_position_qty=pos, in_cooldown=False, strategy_name=sname
                    )
                else:
                    if sname == "intraday_vwap_momentum":
                        p = dict(inst.strategy.parameters or {})
                        cfg0 = VwapMomentumConfig()
                        vm_cfg = VwapMomentumConfig(
                            timeframe=str(p.get("timeframe", cfg0.timeframe)),
                            vwap_period=int(p.get("vwap_period", cfg0.vwap_period)),
                            ema_fast=int(p.get("ema_fast", cfg0.ema_fast)),
                            ema_slow=int(p.get("ema_slow", cfg0.ema_slow)),
                            atr_period=int(p.get("atr_period", cfg0.atr_period)),
                            sl_points=_to_decimal(p.get("sl_points", cfg0.sl_points)),
                            tp_points=_to_decimal(p.get("tp_points", cfg0.tp_points)),
                            atr_sl_mult=_to_decimal(p.get("atr_sl_mult", cfg0.atr_sl_mult)),
                            atr_tp_mult=_to_decimal(p.get("atr_tp_mult", cfg0.atr_tp_mult)),
                            risk_per_trade_pct=_to_decimal(p.get("risk_per_trade_pct", cfg0.risk_per_trade_pct))
                            or cfg0.risk_per_trade_pct,
                            volume_window=int(p.get("volume_window", cfg0.volume_window)),
                            min_volume_ratio=_to_decimal(p.get("min_volume_ratio", cfg0.min_volume_ratio))
                            or cfg0.min_volume_ratio,
                            trade_sessions=tuple(tuple(x) for x in (p.get("trade_sessions") or cfg0.trade_sessions)),
                            cooldown_bars=int(p.get("cooldown_bars", cfg0.cooldown_bars)),
                            exit_before_session_end_minutes=int(
                                p.get("exit_before_session_end_minutes", cfg0.exit_before_session_end_minutes)
                            ),
                        )
                        st = VwapMomentumStrategy(figi=inst.figi, config=vm_cfg)
                        sig_fn = lambda w, pos: st.generate_signal(
                            candles=w, current_position_qty=pos, strategy_name=sname
                        )
                    else:
                        p = dict(inst.strategy.parameters or {})
                        cfg0 = BollingerRsiConfig()
                        br_cfg = BollingerRsiConfig(
                            timeframe=str(p.get("timeframe", cfg0.timeframe)),
                            bollinger_period=int(p.get("bollinger_period", cfg0.bollinger_period)),
                            bollinger_std_mult=_to_decimal(p.get("bollinger_std_mult", cfg0.bollinger_std_mult))
                            or cfg0.bollinger_std_mult,
                            rsi_period=int(p.get("rsi_period", cfg0.rsi_period)),
                            rsi_overbought=_to_decimal(p.get("rsi_overbought", cfg0.rsi_overbought))
                            or cfg0.rsi_overbought,
                            rsi_oversold=_to_decimal(p.get("rsi_oversold", cfg0.rsi_oversold)) or cfg0.rsi_oversold,
                            atr_period=int(p.get("atr_period", cfg0.atr_period)),
                            sl_atr_mult=_to_decimal(p.get("sl_atr_mult", cfg0.sl_atr_mult)) or cfg0.sl_atr_mult,
                            tp_atr_mult=_to_decimal(p.get("tp_atr_mult", cfg0.tp_atr_mult)) or cfg0.tp_atr_mult,
                            risk_per_trade_pct=_to_decimal(p.get("risk_per_trade_pct", cfg0.risk_per_trade_pct))
                            or cfg0.risk_per_trade_pct,
                            volume_window=int(p.get("volume_window", cfg0.volume_window)),
                            min_volume_ratio=_to_decimal(p.get("min_volume_ratio", cfg0.min_volume_ratio))
                            or cfg0.min_volume_ratio,
                            trade_sessions=tuple(tuple(x) for x in (p.get("trade_sessions") or cfg0.trade_sessions)),
                            cooldown_bars=int(p.get("cooldown_bars", cfg0.cooldown_bars)),
                        )
                        st = BollingerRsiStrategy(figi=inst.figi, config=br_cfg)
                        sig_fn = lambda w, pos: st.generate_signal(
                            candles=w, current_position_qty=pos, strategy_name=sname
                        )

            bt_cfg = BacktestConfig(
                initial_equity=Decimal(str(args.initial_equity)),
                price_slippage_bps=Decimal(str(cfg.global_execution.price_slippage_bps)),
                commission_bps=Decimal(str(cfg.global_execution.commission_bps)),
            )
            result = run_backtest_target_qty(
                figi=inst.figi,
                strategy_name=sname,
                candles=candles,
                signal_fn=sig_fn,
                cfg=bt_cfg,
            )
            summary = summarize(result.trades, result.equity)

            prefix = out_dir / f"{sname}_{inst.figi}"
            write_trades_csv(prefix.with_suffix(".trades.csv"), result.trades)
            write_equity_csv(prefix.with_suffix(".equity.csv"), result.equity)
            write_summary_json(prefix.with_suffix(".summary.json"), summary)

            print(
                f"{sname} {inst.figi}: trades={summary.trades} pnl={summary.total_pnl} "
                f"mdd={summary.max_drawdown} winrate={summary.winrate:.2%}"
            )

    asyncio.run(_run())


if __name__ == "__main__":
    main()

