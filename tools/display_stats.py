from __future__ import annotations

import json
import sqlite3
from pathlib import Path


def _display_backtest_reports() -> bool:
    out_dir = Path("backtest_reports")
    if not out_dir.exists():
        return False
    summaries = sorted(out_dir.glob("*.summary.json"))
    if not summaries:
        return False
    for p in summaries:
        data = json.loads(p.read_text(encoding="utf-8"))
        print(
            f"{p.name}: trades={data.get('trades')} pnl={data.get('total_pnl')} "
            f"mdd={data.get('max_drawdown')} winrate={data.get('winrate')}"
        )
    return True


def _display_legacy_stats_db() -> None:
    if not Path("stats.db").exists():
        print("No stats found (backtest_reports/ or stats.db missing).")
        return
    with sqlite3.connect("stats.db") as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM orders")
        for row in cursor.fetchall():
            print(row)


if __name__ == "__main__":
    if not _display_backtest_reports():
        _display_legacy_stats_db()
