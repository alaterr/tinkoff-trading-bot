from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from core.models.entities import Fill, Order, Position


def _dt_to_str(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _str_to_dt(s: str) -> datetime:
    # Accept our Z format
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


class StateStore:
    """
    Minimal SQLite-backed state store for OMS / risk / D1 processing.

    Tables:
    - orders: 1 row per client_order_id
    - fills: multiple rows per broker execution
    - positions: latest known position per figi
    - last_processed: last processed candle-close per (strategy, figi)
    """

    def __init__(self, db_path: str = "state.db"):
        self.db_path = Path(db_path)
        self._conn: Optional[sqlite3.Connection] = None

    def connect(self) -> None:
        self._conn = sqlite3.connect(self.db_path.as_posix())
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA foreign_keys=ON;")
        self._create_schema()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _create_schema(self) -> None:
        assert self._conn is not None
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS orders (
                client_order_id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                figi TEXT NOT NULL,
                side TEXT NOT NULL,
                order_type TEXT NOT NULL,
                requested_qty INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                broker_order_id TEXT,
                status TEXT NOT NULL,
                extra_json TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS fills (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_order_id TEXT NOT NULL,
                broker_order_id TEXT NOT NULL,
                figi TEXT NOT NULL,
                side TEXT NOT NULL,
                qty INTEGER NOT NULL,
                price TEXT NOT NULL,
                ts TEXT NOT NULL,
                UNIQUE(broker_order_id, ts, qty, price),
                FOREIGN KEY(client_order_id) REFERENCES orders(client_order_id)
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS positions (
                figi TEXT PRIMARY KEY,
                qty INTEGER NOT NULL,
                avg_price TEXT,
                updated_at TEXT
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS last_processed (
                strategy_name TEXT NOT NULL,
                figi TEXT NOT NULL,
                candle_close_ts TEXT NOT NULL,
                PRIMARY KEY(strategy_name, figi)
            )
            """
        )

        # Risk accounting / run control
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trade_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                strategy_name TEXT NOT NULL,
                figi TEXT NOT NULL,
                client_order_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                UNIQUE(event_type, client_order_id)
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS equity_baselines (
                period_key TEXT PRIMARY KEY,
                ts TEXT NOT NULL,
                equity_rub TEXT NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cooldown (
                key TEXT PRIMARY KEY,
                cooldown_until_ts TEXT NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS control_flags (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        self._conn.commit()

    def list_open_orders(self) -> list[str]:
        """
        Return client_order_id for orders that are not in a terminal status.
        Status values are broker-specific strings; we treat anything containing
        'FILL', 'CANCEL', 'REJECT' as terminal (best-effort).
        """
        assert self._conn is not None
        rows = self._conn.execute("SELECT client_order_id, status FROM orders").fetchall()
        out: list[str] = []
        for oid, st in rows:
            s = (st or "").upper()
            if ("FILL" in s) or ("CANCEL" in s) or ("REJECT" in s):
                continue
            out.append(str(oid))
        return out

    def upsert_order(self, order: Order, *, extra: Optional[dict[str, Any]] = None) -> None:
        assert self._conn is not None
        extra_json = json.dumps(extra or {}, ensure_ascii=False, sort_keys=True)
        self._conn.execute(
            """
            INSERT INTO orders (
                client_order_id, account_id, figi, side, order_type, requested_qty, created_at,
                broker_order_id, status, extra_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(client_order_id) DO UPDATE SET
                broker_order_id=COALESCE(excluded.broker_order_id, orders.broker_order_id),
                status=excluded.status,
                extra_json=excluded.extra_json
            """,
            (
                order.client_order_id,
                order.account_id,
                order.figi,
                order.side.value,
                order.order_type.value,
                int(order.requested_qty),
                _dt_to_str(order.created_at),
                order.broker_order_id,
                order.status,
                extra_json,
            ),
        )
        self._conn.commit()

    def add_trade_event(
        self,
        *,
        ts: datetime,
        strategy_name: str,
        figi: str,
        client_order_id: str,
        event_type: str = "order_sent",
    ) -> None:
        assert self._conn is not None
        self._conn.execute(
            """
            INSERT OR IGNORE INTO trade_events (ts, strategy_name, figi, client_order_id, event_type)
            VALUES (?, ?, ?, ?, ?)
            """,
            (_dt_to_str(ts), strategy_name, figi, client_order_id, event_type),
        )
        self._conn.commit()

    def count_trade_events_since(self, *, since_ts: datetime, event_type: str = "order_sent") -> int:
        assert self._conn is not None
        row = self._conn.execute(
            """
            SELECT COUNT(*) FROM trade_events
            WHERE event_type=? AND ts>=?
            """,
            (event_type, _dt_to_str(since_ts)),
        ).fetchone()
        return int(row[0]) if row else 0

    def get_or_set_equity_baseline(self, *, period_key: str, ts: datetime, equity_rub) -> str:
        """
        Returns baseline equity for the period. If not present, sets it to provided equity.
        Stored as string to avoid float drift.
        """
        assert self._conn is not None
        row = self._conn.execute(
            "SELECT equity_rub FROM equity_baselines WHERE period_key=?",
            (period_key,),
        ).fetchone()
        if row is not None:
            return str(row[0])

        self._conn.execute(
            "INSERT INTO equity_baselines(period_key, ts, equity_rub) VALUES (?, ?, ?)",
            (period_key, _dt_to_str(ts), str(equity_rub)),
        )
        self._conn.commit()
        return str(equity_rub)

    def get_cooldown_until(self, *, key: str = "global") -> Optional[datetime]:
        assert self._conn is not None
        row = self._conn.execute(
            "SELECT cooldown_until_ts FROM cooldown WHERE key=?",
            (key,),
        ).fetchone()
        if row is None:
            return None
        return _str_to_dt(row[0])

    def set_cooldown_until(self, *, key: str = "global", cooldown_until: datetime) -> None:
        assert self._conn is not None
        self._conn.execute(
            """
            INSERT INTO cooldown(key, cooldown_until_ts) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET cooldown_until_ts=excluded.cooldown_until_ts
            """,
            (key, _dt_to_str(cooldown_until)),
        )
        self._conn.commit()

    def get_flag(self, *, key: str, default: str) -> str:
        assert self._conn is not None
        row = self._conn.execute(
            "SELECT value FROM control_flags WHERE key=?",
            (key,),
        ).fetchone()
        if row is None:
            return default
        return str(row[0])

    def set_flag(self, *, key: str, value: str) -> None:
        assert self._conn is not None
        self._conn.execute(
            """
            INSERT INTO control_flags(key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (key, value),
        )
        self._conn.commit()

    def get_order(self, client_order_id: str) -> Optional[Order]:
        assert self._conn is not None
        row = self._conn.execute(
            """
            SELECT client_order_id, account_id, figi, side, order_type, requested_qty, created_at,
                   broker_order_id, status
            FROM orders WHERE client_order_id=?
            """,
            (client_order_id,),
        ).fetchone()
        if row is None:
            return None
        client_order_id, account_id, figi, side, order_type, requested_qty, created_at, broker_id, status = row
        from core.models.entities import OrderType, Side

        return Order(
            client_order_id=client_order_id,
            account_id=account_id,
            figi=figi,
            side=Side(side),
            order_type=OrderType(order_type),
            requested_qty=int(requested_qty),
            created_at=_str_to_dt(created_at),
            broker_order_id=broker_id,
            status=status,
        )

    def add_fill(self, fill: Fill) -> None:
        assert self._conn is not None
        self._conn.execute(
            """
            INSERT OR IGNORE INTO fills (
                client_order_id, broker_order_id, figi, side, qty, price, ts
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fill.client_order_id,
                fill.broker_order_id,
                fill.figi,
                fill.side.value,
                int(fill.qty),
                str(fill.price),
                _dt_to_str(fill.ts),
            ),
        )
        self._conn.commit()

    def upsert_position(self, position: Position) -> None:
        assert self._conn is not None
        self._conn.execute(
            """
            INSERT INTO positions (figi, qty, avg_price, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(figi) DO UPDATE SET
                qty=excluded.qty,
                avg_price=excluded.avg_price,
                updated_at=excluded.updated_at
            """,
            (
                position.figi,
                int(position.qty),
                None if position.avg_price is None else str(position.avg_price),
                None if position.updated_at is None else _dt_to_str(position.updated_at),
            ),
        )
        self._conn.commit()

    def get_position(self, figi: str) -> Optional[Position]:
        assert self._conn is not None
        row = self._conn.execute(
            "SELECT figi, qty, avg_price, updated_at FROM positions WHERE figi=?",
            (figi,),
        ).fetchone()
        if row is None:
            return None
        figi, qty, avg_price, updated_at = row
        from decimal import Decimal

        return Position(
            figi=figi,
            qty=int(qty),
            avg_price=None if avg_price is None else Decimal(avg_price),
            updated_at=None if updated_at is None else _str_to_dt(updated_at),
        )

    def set_last_processed_candle_close(
        self, *, strategy_name: str, figi: str, candle_close_ts: datetime
    ) -> None:
        assert self._conn is not None
        self._conn.execute(
            """
            INSERT INTO last_processed (strategy_name, figi, candle_close_ts)
            VALUES (?, ?, ?)
            ON CONFLICT(strategy_name, figi) DO UPDATE SET
                candle_close_ts=excluded.candle_close_ts
            """,
            (strategy_name, figi, _dt_to_str(candle_close_ts)),
        )
        self._conn.commit()

    def get_last_processed_candle_close(
        self, *, strategy_name: str, figi: str
    ) -> Optional[datetime]:
        assert self._conn is not None
        row = self._conn.execute(
            """
            SELECT candle_close_ts FROM last_processed
            WHERE strategy_name=? AND figi=?
            """,
            (strategy_name, figi),
        ).fetchone()
        if row is None:
            return None
        (s,) = row
        return _str_to_dt(s)

