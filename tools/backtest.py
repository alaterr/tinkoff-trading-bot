from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import List, Optional

from app.client import client as broker_client
from app.instruments_config.parser import get_instruments
from core.backtest.engine import BacktestConfig, run_backtest_target_qty
from core.data.candles import CandleRepository
from reports.stats import summarize
from reports.trade_log import write_equity_csv, write_summary_json, write_trades_csv
from app.strategies.positional.donchian_atr import DonchianATRStrategy, DonchianAtrConfig
from app.strategies.positional.ema_atr import EmaAtrTrendStrategy, EmaAtrConfig


def _parse_dt(s: str) -> datetime:
    # ISO8601 date or datetime
    if len(s) == 10:
        return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="instruments_config.json")
    ap.add_argument("--out", default="backtest_reports")
    ap.add_argument("--figi", default=None)
    ap.add_argument("--from", dest="from_dt", default=None)
    ap.add_argument("--to", dest="to_dt", default=None)
    ap.add_argument("--initial-equity", default="1000000")
    args = ap.parse_args()

    cfg = get_instruments(args.config)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Init broker client for candle download (optional if you already have cache)
    import asyncio

    async def _run():
        await broker_client.ainit()
        repo = CandleRepository(broker=broker_client)

        for inst in cfg.instruments:
            if args.figi and inst.figi != args.figi:
                continue

            sname = inst.strategy.name.value
            if sname not in {"donchian_atr", "ema_atr"}:
                continue

            # Fetch candles (uses sdk history cache if enabled in app settings)
            # For deterministic runs, user should freeze a time interval by --to.
            to = _parse_dt(args.to_dt) if args.to_dt else datetime.now(timezone.utc)
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
                st = EmaAtrTrendStrategy(figi=inst.figi, config=EmaAtrConfig(**inst.strategy.parameters))
                sig_fn = lambda w, pos: st.generate_signal(
                    candles=w, current_position_qty=pos, in_cooldown=False, strategy_name=sname
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

