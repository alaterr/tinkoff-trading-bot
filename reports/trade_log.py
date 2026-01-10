from __future__ import annotations

import csv
import json
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from typing import Iterable, List

from reports.stats import EquityPoint, Trade


def _dumps_decimal(o):
    if isinstance(o, Decimal):
        return str(o)
    raise TypeError(f"Object of type {type(o)} is not JSON serializable")


def write_trades_csv(path: str | Path, trades: List[Trade]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f,
            fieldnames=["ts", "strategy", "figi", "side", "qty", "price", "commission"],
        )
        w.writeheader()
        for t in trades:
            w.writerow(
                {
                    "ts": t.ts,
                    "strategy": t.strategy,
                    "figi": t.figi,
                    "side": t.side,
                    "qty": t.qty,
                    "price": str(t.price),
                    "commission": str(t.commission),
                }
            )


def write_equity_csv(path: str | Path, equity: List[EquityPoint]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["ts", "equity"])
        w.writeheader()
        for pt in equity:
            w.writerow({"ts": pt.ts, "equity": str(pt.equity)})


def write_summary_json(path: str | Path, summary) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        json.dump(asdict(summary), f, ensure_ascii=False, indent=2, default=_dumps_decimal)

