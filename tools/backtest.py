from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import List, Optional

from app.client import client as broker_client
from app.instruments_config.parser import get_instruments
from core.backtest.engine import BacktestConfig, run_backtest_target_qty
from core.data.candles import CandleRepository
from core.models.entities import Candle
from reports.stats import summarize
from reports.trade_log import write_equity_csv, write_summary_json, write_trades_csv
from app.strategies.positional.donchian_atr import DonchianATRStrategy, DonchianAtrConfig
from app.strategies.positional.ema_atr import EmaAtrTrendStrategy, EmaAtrConfig
from app.strategies.intraday.vwap_momentum import VwapMomentumConfig, VwapMomentumStrategy, _to_decimal
from app.strategies.intraday.bollinger_rsi import BollingerRsiConfig, BollingerRsiStrategy


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
    args = ap.parse_args()

    cfg = get_instruments(args.config)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Init broker client for candle download (optional if you already have cache)
    import asyncio

    async def _run():
        candles_from_file: Optional[List[Candle]] = None
        if args.candles_file:
            if not args.figi:
                raise SystemExit("--figi обязателен при использовании --candles-file")
            candles_from_file = _load_candles_file(Path(args.candles_file), figi_fallback=str(args.figi))
        else:
            await broker_client.ainit()
            repo = CandleRepository(broker=broker_client)

        for inst in cfg.instruments:
            if args.figi and inst.figi != args.figi:
                continue

            sname = inst.strategy.name.value
            if sname not in {"donchian_atr", "ema_atr", "intraday_vwap_momentum", "intraday_bollinger_rsi"}:
                continue

            # Fetch candles (uses sdk history cache if enabled in app settings)
            # For deterministic runs, user should freeze a time interval by --to.
            to = _parse_dt(args.to_dt) if args.to_dt else datetime.now(timezone.utc)
            if candles_from_file is not None:
                candles = list(candles_from_file)
                # Optional time window filter
                if args.from_dt:
                    from_ts = _parse_dt(args.from_dt)
                    candles = [c for c in candles if c.time >= from_ts]
                if args.to_dt:
                    candles = [c for c in candles if c.time <= to]
            else:
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

