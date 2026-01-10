from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

from aiohttp import web

from app.client import client as broker_client
from app.instruments_config.parser import instruments_config
from app.settings import settings
from storage.state_store import StateStore


def _json_response(data: Any, status: int = 200) -> web.Response:
    return web.json_response(data, status=status, dumps=lambda o: json.dumps(o, default=str))


INDEX_HTML = """<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Tinkoff Trading Bot UI</title>
    <style>
      body { font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Arial; margin: 16px; }
      .row { display: flex; gap: 12px; flex-wrap: wrap; }
      .card { border: 1px solid #ddd; border-radius: 8px; padding: 12px; min-width: 320px; }
      table { border-collapse: collapse; width: 100%; }
      th, td { border-bottom: 1px solid #eee; padding: 6px 8px; text-align: left; font-size: 13px; }
      th { background: #fafafa; position: sticky; top: 0; }
      .muted { color: #666; font-size: 12px; }
      button { padding: 6px 10px; }
      code { background: #f6f8fa; padding: 2px 4px; border-radius: 4px; }
    </style>
  </head>
  <body>
    <h2>Tinkoff Trading Bot UI</h2>
    <div class="muted">No authentication (MVP). Do not expose publicly.</div>
    <div class="row">
      <div class="card">
        <h3>Controls</h3>
        <div>Trading enabled: <code id="tradingEnabled">?</code></div>
        <div style="margin-top: 8px;">
          <button onclick="pause()">Pause</button>
          <button onclick="resume()">Resume</button>
          <button onclick="setCooldown()">Cooldown 5m</button>
        </div>
        <div class="muted" style="margin-top: 8px;">Kill-switch file is also supported by RiskGate (if configured).</div>
      </div>
      <div class="card">
        <h3>Status</h3>
        <div>Sandbox: <code id="sandbox">?</code></div>
        <div>Account: <code id="account">?</code></div>
        <div>Now (UTC): <code id="now">?</code></div>
      </div>
    </div>

    <div class="row" style="margin-top: 12px;">
      <div class="card" style="flex: 1;">
        <h3>Strategies (configured)</h3>
        <div class="muted">From instruments_config.json + last_processed from state.db</div>
        <table id="strategiesTbl">
          <thead><tr><th>figi</th><th>strategy</th><th>instrument_type</th><th>last_processed</th></tr></thead>
          <tbody></tbody>
        </table>
      </div>
    </div>

    <div class="row" style="margin-top: 12px;">
      <div class="card" style="flex: 1;">
        <h3>Open positions</h3>
        <table id="positionsTbl">
          <thead><tr><th>figi</th><th>qty</th><th>avg_price</th><th>updated_at</th></tr></thead>
          <tbody></tbody>
        </table>
      </div>
    </div>

    <div class="row" style="margin-top: 12px;">
      <div class="card" style="flex: 1;">
        <h3>Orders (latest)</h3>
        <table id="ordersTbl">
          <thead><tr><th>client_order_id</th><th>figi</th><th>side</th><th>qty</th><th>status</th><th>created_at</th></tr></thead>
          <tbody></tbody>
        </table>
      </div>
    </div>

    <div class="row" style="margin-top: 12px;">
      <div class="card" style="flex: 1;">
        <h3>Fills (latest)</h3>
        <table id="fillsTbl">
          <thead><tr><th>client_order_id</th><th>broker_order_id</th><th>figi</th><th>side</th><th>qty</th><th>price</th><th>ts</th></tr></thead>
          <tbody></tbody>
        </table>
      </div>
    </div>

    <script>
      async function jget(url){ const r=await fetch(url); return await r.json(); }
      async function jpost(url, body){ const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})}); return await r.json(); }

      function fillTable(tblId, rows, cols){
        const tb=document.querySelector(`#${tblId} tbody`);
        tb.innerHTML='';
        for(const row of rows){
          const tr=document.createElement('tr');
          for(const c of cols){
            const td=document.createElement('td');
            td.textContent = (row[c]===null||row[c]===undefined) ? '' : row[c];
            tr.appendChild(td);
          }
          tb.appendChild(tr);
        }
      }

      async function refresh(){
        const st = await jget('/api/status');
        document.getElementById('sandbox').textContent = st.sandbox;
        document.getElementById('account').textContent = st.account_id || '';
        document.getElementById('now').textContent = st.now;
        document.getElementById('tradingEnabled').textContent = st.trading_enabled ? 'true' : 'false';

        const strategies = await jget('/api/strategies');
        fillTable('strategiesTbl', strategies.items, ['figi','strategy','instrument_type','last_processed']);

        const positions = await jget('/api/positions');
        fillTable('positionsTbl', positions.items, ['figi','qty','avg_price','updated_at']);

        const orders = await jget('/api/orders?limit=50');
        fillTable('ordersTbl', orders.items, ['client_order_id','figi','side','requested_qty','status','created_at']);

        const fills = await jget('/api/fills?limit=50');
        fillTable('fillsTbl', fills.items, ['client_order_id','broker_order_id','figi','side','qty','price','ts']);
      }

      async function pause(){ await jpost('/api/control/pause'); await refresh(); }
      async function resume(){ await jpost('/api/control/resume'); await refresh(); }
      async function setCooldown(){ await jpost('/api/control/cooldown', {seconds: 300}); await refresh(); }

      refresh();
      setInterval(refresh, 5000);
    </script>
  </body>
</html>
"""


class UiServer:
    def __init__(self, *, store: StateStore):
        self.store = store

    async def handle_index(self, request: web.Request) -> web.Response:
        return web.Response(text=INDEX_HTML, content_type="text/html")

    async def handle_status(self, request: web.Request) -> web.Response:
        trading_enabled = self.store.get_flag(key="trading_enabled", default="1") == "1"
        return _json_response(
            {
                "sandbox": settings.sandbox,
                "account_id": settings.account_id,
                "now": datetime.now(timezone.utc).isoformat(),
                "trading_enabled": trading_enabled,
            }
        )

    async def handle_strategies(self, request: web.Request) -> web.Response:
        items = []
        for inst in instruments_config.instruments:
            lp = self.store.get_last_processed_candle_close(
                strategy_name=inst.strategy.name.value, figi=inst.figi
            )
            items.append(
                {
                    "figi": inst.figi,
                    "strategy": inst.strategy.name.value,
                    "instrument_type": getattr(inst, "instrument_type", None),
                    "last_processed": None if lp is None else lp.isoformat(),
                }
            )
        return _json_response({"items": items})

    async def handle_positions(self, request: web.Request) -> web.Response:
        # Prefer store snapshot; it's cheap and works without broker calls.
        rows = self.store._conn.execute(  # type: ignore[union-attr]
            "SELECT figi, qty, avg_price, updated_at FROM positions ORDER BY figi"
        ).fetchall()
        items = []
        for figi, qty, avg_price, updated_at in rows:
            items.append(
                {
                    "figi": figi,
                    "qty": int(qty),
                    "avg_price": avg_price,
                    "updated_at": updated_at,
                }
            )
        return _json_response({"items": items})

    async def handle_orders(self, request: web.Request) -> web.Response:
        limit = int(request.query.get("limit", "100"))
        rows = self.store._conn.execute(  # type: ignore[union-attr]
            """
            SELECT client_order_id, figi, side, requested_qty, status, created_at
            FROM orders
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        items = []
        for oid, figi, side, qty, status, created_at in rows:
            items.append(
                {
                    "client_order_id": oid,
                    "figi": figi,
                    "side": side,
                    "requested_qty": int(qty),
                    "status": status,
                    "created_at": created_at,
                }
            )
        return _json_response({"items": items})

    async def handle_fills(self, request: web.Request) -> web.Response:
        limit = int(request.query.get("limit", "100"))
        rows = self.store._conn.execute(  # type: ignore[union-attr]
            """
            SELECT client_order_id, broker_order_id, figi, side, qty, price, ts
            FROM fills
            ORDER BY ts DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        items = []
        for cid, bid, figi, side, qty, price, ts in rows:
            items.append(
                {
                    "client_order_id": cid,
                    "broker_order_id": bid,
                    "figi": figi,
                    "side": side,
                    "qty": int(qty),
                    "price": price,
                    "ts": ts,
                }
            )
        return _json_response({"items": items})

    async def handle_pause(self, request: web.Request) -> web.Response:
        self.store.set_flag(key="trading_enabled", value="0")
        return _json_response({"ok": True, "trading_enabled": False})

    async def handle_resume(self, request: web.Request) -> web.Response:
        self.store.set_flag(key="trading_enabled", value="1")
        return _json_response({"ok": True, "trading_enabled": True})

    async def handle_cooldown(self, request: web.Request) -> web.Response:
        body = await request.json()
        seconds = int(body.get("seconds", 60))
        from datetime import timedelta

        until = datetime.now(timezone.utc) + timedelta(seconds=seconds)
        self.store.set_cooldown_until(cooldown_until=until)
        return _json_response({"ok": True, "cooldown_until": until.isoformat()})


async def start_ui_server(*, store: StateStore, host: str, port: int) -> web.AppRunner:
    srv = UiServer(store=store)
    app = web.Application()
    app.router.add_get("/", srv.handle_index)
    app.router.add_get("/api/status", srv.handle_status)
    app.router.add_get("/api/strategies", srv.handle_strategies)
    app.router.add_get("/api/positions", srv.handle_positions)
    app.router.add_get("/api/orders", srv.handle_orders)
    app.router.add_get("/api/fills", srv.handle_fills)
    app.router.add_post("/api/control/pause", srv.handle_pause)
    app.router.add_post("/api/control/resume", srv.handle_resume)
    app.router.add_post("/api/control/cooldown", srv.handle_cooldown)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host=host, port=port)
    await site.start()
    return runner

