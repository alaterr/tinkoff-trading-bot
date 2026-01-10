from __future__ import annotations

import asyncio
import json
import os
import logging
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

from aiohttp import web

from app.client import client as broker_client
from app.instruments_config.parser import instruments_config
from app.settings import settings
from app.strategies.models import StrategyName
from app.strategies.strategy_fabric import resolve_strategy
from storage.state_store import StateStore

logger = logging.getLogger(__name__)


def _json_response(data: Any, status: int = 200) -> web.Response:
    return web.json_response(data, status=status, dumps=lambda o: json.dumps(o, default=str))


INDEX_HTML = """<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Semantix AI Trading</title>
    <style>
      :root {
        --bg-primary: #0f172a;
        --bg-secondary: #1e293b;
        --bg-card: #1e293b;
        --bg-hover: #334155;
        --text-primary: #f1f5f9;
        --text-secondary: #94a3b8;
        --text-muted: #64748b;
        --accent: #3b82f6;
        --accent-hover: #2563eb;
        --success: #10b981;
        --warning: #f59e0b;
        --danger: #ef4444;
        --border: #334155;
        --shadow: rgba(0, 0, 0, 0.3);
        --gradient: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
      }
      * { box-sizing: border-box; margin: 0; padding: 0; }
      body {
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
        background: var(--bg-primary);
        color: var(--text-primary);
        line-height: 1.6;
        padding: 0;
        margin: 0;
        min-height: 100vh;
      }
      .header {
        background: var(--bg-secondary);
        border-bottom: 1px solid var(--border);
        padding: 1rem 2rem;
        position: sticky;
        top: 0;
        z-index: 100;
        box-shadow: 0 4px 6px var(--shadow);
        backdrop-filter: blur(10px);
      }
      .header h1 {
        font-size: 1.5rem;
        font-weight: 700;
        background: var(--gradient);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        background-clip: text;
        margin-bottom: 0.25rem;
      }
      .header .subtitle {
        color: var(--text-muted);
        font-size: 0.875rem;
      }
      .container {
        max-width: 1600px;
        margin: 0 auto;
        padding: 2rem;
      }
      .row {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
        gap: 1.5rem;
        margin-bottom: 1.5rem;
      }
      .card {
        background: var(--bg-card);
        border: 1px solid var(--border);
        border-radius: 12px;
        padding: 1.5rem;
        transition: all 0.3s ease;
        box-shadow: 0 4px 6px var(--shadow);
      }
      .card:hover {
        transform: translateY(-2px);
        box-shadow: 0 8px 12px var(--shadow);
        border-color: var(--accent);
      }
      .card h3 {
        font-size: 1.125rem;
        font-weight: 600;
        margin-bottom: 1rem;
        color: var(--text-primary);
        display: flex;
        align-items: center;
        gap: 0.5rem;
      }
      .card h3::before {
        content: '▸';
        color: var(--accent);
        font-size: 0.875rem;
      }
      .muted {
        color: var(--text-muted);
        font-size: 0.875rem;
        margin-bottom: 1rem;
      }
      .status-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
        gap: 1rem;
        margin-top: 1rem;
      }
      .status-item {
        display: flex;
        flex-direction: column;
        gap: 0.25rem;
      }
      .status-label {
        font-size: 0.75rem;
        color: var(--text-muted);
        text-transform: uppercase;
        letter-spacing: 0.05em;
      }
      .status-value {
        font-size: 1rem;
        font-weight: 600;
        color: var(--text-primary);
      }
      .status-value.active { color: var(--success); }
      .status-value.inactive { color: var(--text-muted); }
      .status-value.warning { color: var(--warning); }
      .status-value.danger { color: var(--danger); }
      code {
        background: rgba(59, 130, 246, 0.15);
        color: var(--accent);
        padding: 0.25rem 0.5rem;
        border-radius: 4px;
        font-size: 0.875rem;
        font-family: 'Monaco', 'Menlo', monospace;
      }
      input {
        width: 100%;
        padding: 0.75rem;
        background: var(--bg-primary);
        border: 1px solid var(--border);
        border-radius: 6px;
        color: var(--text-primary);
        font-size: 0.875rem;
        transition: all 0.2s ease;
        margin-top: 0.5rem;
      }
      input:focus {
        outline: none;
        border-color: var(--accent);
        box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.1);
      }
      input::placeholder {
        color: var(--text-muted);
      }
      button {
        padding: 0.625rem 1.25rem;
        background: var(--accent);
        color: white;
        border: none;
        border-radius: 6px;
        font-size: 0.875rem;
        font-weight: 500;
        cursor: pointer;
        transition: all 0.2s ease;
        margin: 0.25rem;
      }
      button:hover {
        background: var(--accent-hover);
        transform: translateY(-1px);
        box-shadow: 0 4px 8px rgba(59, 130, 246, 0.3);
      }
      button:active {
        transform: translateY(0);
      }
      button.danger {
        background: var(--danger);
      }
      button.danger:hover {
        background: #dc2626;
      }
      button.success {
        background: var(--success);
      }
      button.success:hover {
        background: #059669;
      }
      button.warning {
        background: var(--warning);
      }
      button.warning:hover {
        background: #d97706;
      }
      .btn-group {
        display: flex;
        flex-wrap: wrap;
        gap: 0.5rem;
        margin-top: 1rem;
      }
      table {
        width: 100%;
        border-collapse: collapse;
        margin-top: 1rem;
        font-size: 0.875rem;
      }
      thead {
        position: sticky;
        top: 0;
        z-index: 10;
      }
      th {
        background: var(--bg-secondary);
        color: var(--text-secondary);
        font-weight: 600;
        text-transform: uppercase;
        font-size: 0.75rem;
        letter-spacing: 0.05em;
        padding: 0.75rem;
        text-align: left;
        border-bottom: 2px solid var(--border);
        position: sticky;
        top: 0;
        backdrop-filter: blur(10px);
      }
      td {
        padding: 0.75rem;
        border-bottom: 1px solid var(--border);
        color: var(--text-primary);
      }
      tbody tr {
        transition: background 0.2s ease;
      }
      tbody tr:hover {
        background: var(--bg-hover);
      }
      .table-container {
        max-height: 500px;
        overflow-y: auto;
        border-radius: 8px;
        margin-top: 1rem;
      }
      .table-container::-webkit-scrollbar {
        width: 8px;
      }
      .table-container::-webkit-scrollbar-track {
        background: var(--bg-secondary);
      }
      .table-container::-webkit-scrollbar-thumb {
        background: var(--border);
        border-radius: 4px;
      }
      .table-container::-webkit-scrollbar-thumb:hover {
        background: var(--text-muted);
      }
      .badge {
        display: inline-block;
        padding: 0.25rem 0.5rem;
        border-radius: 4px;
        font-size: 0.75rem;
        font-weight: 600;
        text-transform: uppercase;
      }
      .badge.success {
        background: rgba(16, 185, 129, 0.2);
        color: var(--success);
      }
      .badge.danger {
        background: rgba(239, 68, 68, 0.2);
        color: var(--danger);
      }
      .badge.warning {
        background: rgba(245, 158, 11, 0.2);
        color: var(--warning);
      }
      .badge.info {
        background: rgba(59, 130, 246, 0.2);
        color: var(--accent);
      }
      .pulse {
        animation: pulse 2s cubic-bezier(0.4, 0, 0.6, 1) infinite;
      }
      @keyframes pulse {
        0%, 100% { opacity: 1; }
        50% { opacity: 0.5; }
      }
      .fade-in {
        animation: fadeIn 0.3s ease-in;
      }
      @keyframes fadeIn {
        from { opacity: 0; transform: translateY(10px); }
        to { opacity: 1; transform: translateY(0); }
      }
      @media (max-width: 768px) {
        .container { padding: 1rem; }
        .row { grid-template-columns: 1fr; }
        .header { padding: 1rem; }
      }
    </style>
  </head>
  <body>
    <div class="header">
      <h1>Semantix AI Trading</h1>
      <div class="subtitle">Мониторинг и ручное управление • Без авторизации (MVP)</div>
    </div>
    <div class="container">
      <div class="row">
        <div class="card fade-in">
          <h3>🔐 Доступ</h3>
          <div class="muted">Токен хранится только в памяти (не сохраняется)</div>
          <div class="status-grid">
            <div class="status-item">
              <span class="status-label">Токен</span>
              <span class="status-value" id="tokenSet">?</span>
            </div>
          </div>
          <input id="tokenInput" placeholder="API-токен брокера" type="password" />
          <input id="accountInput" placeholder="ACCOUNT_ID (опционально)" />
          <div class="btn-group">
            <button class="success" onclick="setCredentials()">Установить токен</button>
          </div>
        </div>
        <div class="card fade-in">
          <h3>⚙️ Управление</h3>
          <div class="status-grid">
            <div class="status-item">
              <span class="status-label">Торговля</span>
              <span class="status-value" id="tradingEnabled">?</span>
            </div>
            <div class="status-item">
              <span class="status-label">Стоп-кран</span>
              <span class="status-value" id="killSwitchStatus">?</span>
            </div>
          </div>
          <div class="btn-group">
            <button class="success" onclick="resume()">▶ Возобновить</button>
            <button class="warning" onclick="pause()">⏸ Пауза</button>
            <button onclick="setCooldown()">⏱ Кулдаун 5 мин</button>
            <button class="danger" onclick="toggleKillSwitch()">🛑 Стоп-кран</button>
            <button onclick="refreshOpenOrders()">🔄 Обновить ордера</button>
            <button onclick="reconcile()">🔁 Сверка</button>
          </div>
        </div>
        <div class="card fade-in">
          <h3>📊 Статус</h3>
          <div class="status-grid">
            <div class="status-item">
              <span class="status-label">Песочница</span>
              <span class="status-value" id="sandbox">?</span>
            </div>
            <div class="status-item">
              <span class="status-label">Аккаунт</span>
              <span class="status-value" id="account">?</span>
            </div>
            <div class="status-item">
              <span class="status-label">Время (UTC)</span>
              <span class="status-value" id="now">?</span>
            </div>
          </div>
        </div>
        <div class="card fade-in">
          <h3>💰 Данные брокера (live)</h3>
          <div class="muted">Данные портфеля в реальном времени</div>
          <div class="status-grid">
            <div class="status-item">
              <span class="status-label">Портфель</span>
              <span class="status-value" id="liveTotal">?</span>
            </div>
            <div class="status-item">
              <span class="status-label">Деньги</span>
              <span class="status-value" id="liveCash">?</span>
            </div>
          </div>
        </div>
      </div>

      <div class="row">
        <div class="card fade-in" style="grid-column: 1 / -1;">
          <h3>🎯 Торговые джобы</h3>
          <div class="muted">Торговля не начнётся, пока вы не нажмёте <b>Старт</b>. Выберите режим: <code>sandbox</code> или <code>real</code>.</div>
          <div class="table-container">
            <table id="jobsTbl">
              <thead><tr><th>Job ID</th><th>FIGI</th><th>Стратегия</th><th>Статус</th><th>Режим</th><th>Действие</th></tr></thead>
              <tbody></tbody>
            </table>
          </div>
        </div>
      </div>

      <div class="row">
        <div class="card fade-in" style="grid-column: 1 / -1;">
          <h3>🔎 Последнее решение (прозрачность)</h3>
          <div class="muted">Показывает, что джоба увидела на последней D1 свече: индикаторы → сигнал → RiskGate → план ордера</div>
          <div class="table-container">
            <table id="decisionsTbl">
              <thead><tr><th>Job ID</th><th>Свеча (UTC)</th><th>Close</th><th>Позиция</th><th>Цель</th><th>Δ</th><th>Риск</th><th>Причина</th><th>Ордер</th></tr></thead>
              <tbody></tbody>
            </table>
          </div>
        </div>
      </div>

      <div class="row">
        <div class="card fade-in" style="grid-column: 1 / -1;">
          <h3>📈 Стратегии</h3>
          <div class="muted">Сконфигурированные стратегии и время последней обработки</div>
          <div class="table-container">
            <table id="strategiesTbl">
              <thead><tr><th>FIGI</th><th>Стратегия</th><th>Тип</th><th>Последняя обработка</th></tr></thead>
              <tbody></tbody>
            </table>
          </div>
        </div>
      </div>

      <div class="row">
        <div class="card fade-in" style="grid-column: 1 / -1;">
          <h3>💼 Открытые позиции</h3>
          <div class="table-container">
            <table id="positionsTbl">
              <thead><tr><th>FIGI</th><th>Количество</th><th>Средняя цена</th><th>Обновлено</th></tr></thead>
              <tbody></tbody>
            </table>
          </div>
        </div>
      </div>

      <div class="row">
        <div class="card fade-in" style="grid-column: 1 / -1;">
          <h3>📋 Ордера (последние)</h3>
          <div class="muted">Последние ордера из state.db</div>
          <div class="table-container">
            <table id="ordersTbl">
              <thead><tr><th>ID ордера</th><th>FIGI</th><th>Сторона</th><th>Кол-во</th><th>Статус</th><th>Создан</th></tr></thead>
              <tbody></tbody>
            </table>
          </div>
        </div>
      </div>

      <div class="row">
        <div class="card fade-in" style="grid-column: 1 / -1;">
          <h3>✅ Исполнения (последние)</h3>
          <div class="muted">Исполненные сделки</div>
          <div class="table-container">
            <table id="fillsTbl">
              <thead><tr><th>Client Order ID</th><th>Broker Order ID</th><th>FIGI</th><th>Сторона</th><th>Кол-во</th><th>Цена</th><th>Время</th></tr></thead>
              <tbody></tbody>
            </table>
          </div>
        </div>
      </div>

      <div class="row">
        <div class="card fade-in" style="grid-column: 1 / -1;">
          <h3>🌐 Открытые ордера брокера (live)</h3>
          <div class="muted">Текущие открытые ордера из API брокера</div>
          <div class="table-container">
            <table id="brokerOrdersTbl">
              <thead><tr><th>ID ордера</th><th>FIGI</th><th>Направление</th><th>Лоты</th><th>Статус</th></tr></thead>
              <tbody></tbody>
            </table>
          </div>
        </div>
      </div>
    </div>

    <script>
      // State persistence
      const state = {
        get: (key, def) => {
          try { return JSON.parse(localStorage.getItem(key) || JSON.stringify(def)); }
          catch { return def; }
        },
        set: (key, val) => {
          try { localStorage.setItem(key, JSON.stringify(val)); }
          catch {}
        }
      };

      async function jget(url){
        const r = await fetch(url);
        const j = await r.json();
        if (!r.ok) j._http_status = r.status;
        return j;
      }
      async function jpost(url, body){
        const r = await fetch(url, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body||{}) });
        const j = await r.json();
        if (!r.ok) j._http_status = r.status;
        return j;
      }

      function formatDate(s) {
        if (!s) return '';
        try {
          const d = new Date(s);
          return d.toLocaleString('en-US', { hour12: false });
        } catch { return s; }
      }

      function formatMoney(v) {
        if (!v) return '';
        try {
          const n = parseFloat(v);
          return new Intl.NumberFormat('ru-RU', { style: 'currency', currency: 'RUB', minimumFractionDigits: 2 }).format(n);
        } catch { return v; }
      }

      function badge(text, type) {
        return `<span class="badge ${type}">${text}</span>`;
      }

      function fillTable(tblId, rows, cols, formatters = {}) {
        const tb = document.querySelector(`#${tblId} tbody`);
        if (!tb) return;
        tb.innerHTML = '';
        for (const row of rows) {
          const tr = document.createElement('tr');
          tr.className = 'fade-in';
          for (const c of cols) {
            const td = document.createElement('td');
            let val = row[c];
            if (val === null || val === undefined) val = '';
            if (formatters[c]) val = formatters[c](val, row);
            td.innerHTML = val;
            tr.appendChild(td);
          }
          tb.appendChild(tr);
        }
      }

      async function refresh() {
        try {
          const st = await jget('/api/status');
          const tokenSet = !!st.token_set;
          
          // Status updates with badges
          document.getElementById('sandbox').innerHTML = st.sandbox ? badge('Да', 'success') : badge('Нет', 'warning');
          document.getElementById('account').textContent = st.account_id || 'Не задан';
          document.getElementById('now').textContent = formatDate(st.now);
          document.getElementById('tradingEnabled').innerHTML = st.trading_enabled ? badge('Включена', 'success') : badge('Остановлена', 'danger');
          document.getElementById('tokenSet').innerHTML = st.token_set ? badge('Установлен', 'success') : badge('Не задан', 'danger');
          document.getElementById('killSwitchStatus').innerHTML = st.kill_switch_active ? badge('АКТИВЕН', 'danger') : badge('Не активен', 'success');

          // Jobs table
          const jobs = await jget('/api/jobs');
          const tb = document.querySelector('#jobsTbl tbody');
          if (tb) {
            tb.innerHTML = '';
            for (const j of jobs.items) {
              const tr = document.createElement('tr');
              tr.className = 'fade-in';
              const statusBadge = j.status === 'running' ? badge('Работает', 'success') : badge('Остановлена', 'danger');
              const modeBadge = j.mode ? badge(j.mode.toUpperCase(), j.mode === 'real' ? 'danger' : 'info') : badge('—', 'warning');
              tr.innerHTML = `<td><code>${j.job_id}</code></td><td><code>${j.figi}</code></td><td>${j.strategy}</td><td>${statusBadge}</td><td></td><td></td>`;

              // Mode selector (used when starting)
              const modeSel = document.createElement('select');
              modeSel.style.width = '100%';
              modeSel.style.padding = '0.6rem';
              modeSel.style.background = 'var(--bg-primary)';
              modeSel.style.border = '1px solid var(--border)';
              modeSel.style.borderRadius = '6px';
              modeSel.style.color = 'var(--text-primary)';
              modeSel.innerHTML = `
                <option value="sandbox">sandbox</option>
                <option value="real">real</option>
              `;
              // Persist last chosen mode globally
              const lastMode = state.get('job_mode', 'sandbox');
              modeSel.value = lastMode;
              modeSel.onchange = () => state.set('job_mode', modeSel.value);

              // If job is already running, show badge instead of selector
              if (j.status === 'running') {
                tr.children[4].innerHTML = modeBadge;
              } else {
                tr.children[4].appendChild(modeSel);
              }

              const btn = document.createElement('button');
              btn.className = j.status === 'running' ? 'danger' : 'success';
              btn.textContent = j.status === 'running' ? '⏹ Стоп' : '▶ Старт';
              if (j.status !== 'running' && !tokenSet) {
                btn.disabled = true;
                btn.style.opacity = '0.5';
                btn.style.cursor = 'not-allowed';
                btn.title = 'Сначала установите токен';
                modeSel.disabled = true;
                modeSel.style.opacity = '0.6';
              }
              btn.onclick = async () => {
                if (j.status === 'running') {
                  const res = await jpost('/api/jobs/stop', { job_id: j.job_id });
                  if (res && res.ok === false) alert(res.error || 'Не удалось остановить джобу');
                } else {
                  if (!tokenSet) {
                    alert('Сначала установите токен (карточка "Доступ").');
                    return;
                  }
                  const mode = modeSel.value || state.get('job_mode', 'sandbox');
                  if (mode === 'real') {
                    const ok = window.confirm('Выбран REAL режим. Будут выставляться реальные ордера. Продолжить?');
                    if (!ok) return;
                  }
                  const res = await jpost('/api/jobs/start', { job_id: j.job_id, sandbox: mode === 'sandbox', confirm: mode === 'real' });
                  if (res && res.ok === false) {
                    alert(res.error || 'Не удалось запустить джобу');
                    return;
                  }
                }
                await refresh();
              };
              tr.children[5].appendChild(btn);
              tb.appendChild(tr);
            }
          }

          // Strategies
          const strategies = await jget('/api/strategies');
          fillTable('strategiesTbl', strategies.items, ['figi', 'strategy', 'instrument_type', 'last_processed'], {
            figi: v => `<code>${v}</code>`,
            last_processed: v => v ? formatDate(v) : badge('Никогда', 'warning')
          });

          // Positions
          const positions = await jget('/api/positions');
          fillTable('positionsTbl', positions.items, ['figi', 'qty', 'avg_price', 'updated_at'], {
            figi: v => `<code>${v}</code>`,
            qty: v => v > 0 ? `<span style="color: var(--success)">+${v}</span>` : `<span style="color: var(--danger)">${v}</span>`,
            avg_price: v => v ? formatMoney(v) : '',
            updated_at: formatDate
          });

          // Orders
          const orders = await jget('/api/orders?limit=50');
          fillTable('ordersTbl', orders.items, ['client_order_id', 'figi', 'side', 'requested_qty', 'status', 'created_at'], {
            client_order_id: v => `<code style="font-size: 0.75rem">${v.substring(0, 16)}...</code>`,
            figi: v => `<code>${v}</code>`,
            side: v => v === 'buy' ? badge('BUY', 'success') : badge('SELL', 'danger'),
            status: v => {
              const s = String(v).toLowerCase();
              if (s.includes('fill')) return badge('Исполнен', 'success');
              if (s.includes('cancel') || s.includes('reject')) return badge('Ошибка', 'danger');
              return badge('В процессе', 'warning');
            },
            created_at: formatDate
          });

          // Fills
          const fills = await jget('/api/fills?limit=50');
          fillTable('fillsTbl', fills.items, ['client_order_id', 'broker_order_id', 'figi', 'side', 'qty', 'price', 'ts'], {
            client_order_id: v => `<code style="font-size: 0.75rem">${v.substring(0, 16)}...</code>`,
            broker_order_id: v => `<code style="font-size: 0.75rem">${v}</code>`,
            figi: v => `<code>${v}</code>`,
            side: v => v === 'buy' ? badge('BUY', 'success') : badge('SELL', 'danger'),
            price: formatMoney,
            ts: formatDate
          });

          // Broker open orders
          const brokerOrders = await jget('/api/broker/open-orders');
          fillTable('brokerOrdersTbl', brokerOrders.items || [], ['order_id', 'figi', 'direction', 'lots_requested', 'execution_report_status'], {
            order_id: v => `<code style="font-size: 0.75rem">${v}</code>`,
            figi: v => `<code>${v}</code>`,
            direction: v => {
              const d = String(v).toLowerCase();
              return d.includes('buy') ? badge('BUY', 'success') : badge('SELL', 'danger');
            },
            execution_report_status: v => {
              const s = String(v).toLowerCase();
              if (s.includes('fill')) return badge('Исполнен', 'success');
              if (s.includes('cancel') || s.includes('reject')) return badge('Ошибка', 'danger');
              return badge('В процессе', 'warning');
            }
          });

          // Live portfolio
          const live = await jget('/api/broker/portfolio');
          document.getElementById('liveTotal').textContent = live.total_amount_portfolio ? formatMoney(live.total_amount_portfolio) : 'N/A';
          document.getElementById('liveCash').textContent = live.total_amount_currencies ? formatMoney(live.total_amount_currencies) : 'N/A';

          // Decisions (transparency)
          const dec = await jget('/api/jobs/decisions');
          const decItems = (dec && dec.items) ? dec.items : [];
          fillTable('decisionsTbl', decItems, ['job_id','candle_time','close','current_position_qty','target_qty','delta_qty','risk','reason','order'], {
            job_id: v => `<code style="font-size: 0.75rem">${v}</code>`,
            candle_time: v => v ? formatDate(v) : '',
            close: v => v ? String(v) : '',
            current_position_qty: v => String(v ?? ''),
            target_qty: v => (v === null || v === undefined) ? '' : String(v),
            delta_qty: v => (v === null || v === undefined) ? '' : String(v),
            risk: (v, row) => {
              const ok = row.risk_allowed;
              return ok ? badge('OK', 'success') : badge('STOP', 'danger');
            },
            reason: v => v ? String(v) : '',
            order: v => v ? `<code style="font-size: 0.75rem">${v}</code>` : ''
          });
        } catch (err) {
          console.error('Refresh error:', err);
        }
      }

      async function pause() {
        await jpost('/api/control/pause');
        await refresh();
      }
      async function resume() {
        await jpost('/api/control/resume');
        await refresh();
      }
      async function setCooldown() {
        await jpost('/api/control/cooldown', { seconds: 300 });
        await refresh();
      }
      async function toggleKillSwitch() {
        await jpost('/api/control/kill-switch-toggle');
        await refresh();
      }
      async function refreshOpenOrders() {
        await jpost('/api/control/refresh-open-orders');
        await refresh();
      }
      async function reconcile() {
        await jpost('/api/control/reconcile');
        await refresh();
      }
      async function setCredentials() {
        const token = document.getElementById('tokenInput').value;
        const account_id = document.getElementById('accountInput').value;
        if (!token) {
          alert('Токен обязателен');
          return;
        }
        try {
          await jpost('/api/credentials', { token, account_id });
          document.getElementById('tokenInput').value = '';
          document.getElementById('accountInput').value = '';
          state.set('last_account_id', account_id);
          await refresh();
        } catch (err) {
          alert('Не удалось сохранить доступ: ' + (err.message || 'Неизвестная ошибка'));
        }
      }

      // Restore saved account_id
      const savedAccount = state.get('last_account_id', '');
      if (savedAccount) {
        document.getElementById('accountInput').value = savedAccount;
      }

      // Initial load
      refresh();
      
      // Auto-refresh with saved interval (default 5s)
      const refreshInterval = state.get('refresh_interval', 5000);
      setInterval(refresh, refreshInterval);
      
      // Save scroll position
      let scrollPositions = state.get('scroll_positions', {});
      window.addEventListener('scroll', () => {
        scrollPositions[window.location.pathname] = window.scrollY;
        state.set('scroll_positions', scrollPositions);
      });
      
      // Restore scroll position
      const savedScroll = scrollPositions[window.location.pathname];
      if (savedScroll) {
        window.scrollTo(0, savedScroll);
      }
    </script>
  </body>
</html>
"""


class UiServer:
    def __init__(self, *, store: StateStore):
        self.store = store
        self._cached_account_id: Optional[str] = None
        self._token_set: bool = broker_client.credentials_set()
        self._jobs: dict[str, asyncio.Task] = {}
        self._job_modes: dict[str, bool] = {}  # job_id -> sandbox bool

        # Build job registry from config (manual start).
        # key: figi|strategy
        self._job_defs: dict[str, dict[str, Any]] = {}
        for inst in instruments_config.instruments:
            jid = f"{inst.figi}|{inst.strategy.name.value}"
            self._job_defs[jid] = {"figi": inst.figi, "strategy": inst.strategy.name}

    async def _account_id(self) -> Optional[str]:
        if settings.account_id:
            return settings.account_id
        if self._cached_account_id:
            return self._cached_account_id
        try:
            resp = await broker_client.get_accounts()
            self._cached_account_id = resp.accounts[0].id if getattr(resp, "accounts", None) else None
            if self._cached_account_id:
                settings.account_id = self._cached_account_id
            return self._cached_account_id
        except Exception:  # noqa: BLE001
            return None

    async def handle_index(self, request: web.Request) -> web.Response:
        return web.Response(text=INDEX_HTML, content_type="text/html")

    def _kill_switch_path(self) -> Path:
        """Get kill-switch file path from config (defaults to /data/kill.switch)."""
        from app.instruments_config.parser import instruments_config as cfg

        ks_file = cfg.global_risk.kill_switch_file
        # If relative, assume it's under /data (PVC mount)
        if not Path(ks_file).is_absolute():
            return Path("/data") / ks_file
        return Path(ks_file)

    def _kill_switch_active(self) -> bool:
        """Check if kill-switch file exists."""
        try:
            return self._kill_switch_path().exists()
        except Exception:  # noqa: BLE001
            return False

    async def handle_status(self, request: web.Request) -> web.Response:
        trading_enabled = self.store.get_flag(key="trading_enabled", default="1") == "1"
        return _json_response(
            {
                "sandbox": settings.sandbox,
                "account_id": settings.account_id,
                "now": datetime.now(timezone.utc).isoformat(),
                "trading_enabled": trading_enabled,
                "token_set": broker_client.credentials_set(),
                "broker_mode": broker_client.current_mode(),
                "kill_switch_active": self._kill_switch_active(),
            }
        )

    async def handle_credentials(self, request: web.Request) -> web.Response:
        body = await request.json()
        token = (body.get("token") or "").strip()
        account_id = (body.get("account_id") or "").strip() or None
        if not token:
            return _json_response({"ok": False, "error": "token is required"}, status=400)

        # Update runtime credentials (memory only).
        # Initialize in SANDBOX mode by default for safety; job start can switch mode.
        logger.info("credentials set via UI (account_id=%s, sandbox_default=true)", account_id)
        await broker_client.set_credentials(token=token, sandbox=True)
        await broker_client.ainit()
        settings.token = token
        if account_id:
            settings.account_id = account_id
            self._cached_account_id = account_id
        else:
            # Best-effort: resolve account automatically (also creates sandbox account if needed)
            try:
                self._cached_account_id = await self._account_id()
            except Exception:  # noqa: BLE001
                pass
        # do not ever return token back
        return _json_response({"ok": True, "account_id": settings.account_id, "sandbox": settings.sandbox})

    async def handle_jobs(self, request: web.Request) -> web.Response:
        items = []
        for jid, meta in self._job_defs.items():
            t = self._jobs.get(jid)
            status = "running" if (t is not None and not t.done()) else "stopped"
            figi, strat = meta["figi"], meta["strategy"].value
            mode = None
            if status == "running":
                mode = "sandbox" if self._job_modes.get(jid, True) else "real"
            items.append({"job_id": jid, "figi": figi, "strategy": strat, "status": status, "mode": mode})
        return _json_response({"items": items})

    async def handle_job_decisions(self, request: web.Request) -> web.Response:
        """
        Last decision snapshot per job (written by runners into state.db).
        """
        try:
            rows = self.store.list_job_decisions()
            items = []
            for r in rows:
                job_id = r.get("job_id")
                payload = r.get("payload") or {}
                ind = payload.get("indicators") or {}
                sig = payload.get("signal") or {}
                plan = payload.get("plan") or {}
                order = plan.get("order") or {}
                side = order.get("side")
                qty = order.get("intended_qty")
                client_order_id = plan.get("client_order_id")
                order_str = ""
                if side or qty or client_order_id:
                    order_str = f"{side} {qty} ({client_order_id or '-'})"

                items.append(
                    {
                        "job_id": job_id,
                        "candle_time": ind.get("candle_time"),
                        "close": ind.get("close"),
                        "current_position_qty": payload.get("current_position_qty"),
                        "target_qty": plan.get("target_qty") if plan.get("target_qty") is not None else sig.get("target_qty"),
                        "delta_qty": plan.get("delta_qty"),
                        "risk_allowed": (payload.get("risk") or {}).get("allowed"),
                        "risk": "OK" if (payload.get("risk") or {}).get("allowed") else "STOP",
                        "reason": (payload.get("risk") or {}).get("reason") or sig.get("reason"),
                        "order": order_str,
                    }
                )
            return _json_response({"items": items})
        except Exception:  # noqa: BLE001
            logger.exception("handle_job_decisions failed")
            return _json_response({"items": []})

    async def handle_job_start(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
            jid = body.get("job_id")
            sandbox = bool(body.get("sandbox", True))
            confirm = bool(body.get("confirm", False))
            logger.info("job_start requested jid=%s sandbox=%s confirm=%s", jid, sandbox, confirm)

            if not settings.token:
                return _json_response({"ok": False, "error": "Сначала установите токен в «Доступ»."}, status=400)

            if jid not in self._job_defs:
                return _json_response({"ok": False, "error": "unknown job_id"}, status=404)
            if not broker_client.credentials_set():
                return _json_response({"ok": False, "error": "set token first"}, status=400)
            t = self._jobs.get(jid)
            if t is not None and not t.done():
                return _json_response({"ok": True, "status": "already_running"})

            # Safety: do not allow real trading unless explicitly enabled in env + confirmed in UI.
            if not sandbox:
                if not settings.i_know_what_i_am_doing:
                    return _json_response(
                        {
                            "ok": False,
                            "error": "Реальная торговля заблокирована. Установите I_KNOW_WHAT_I_AM_DOING=true в окружении.",
                        },
                        status=403,
                    )
                if not confirm:
                    return _json_response({"ok": False, "error": "Требуется подтверждение для real режима"}, status=400)

            # Disallow mixing sandbox/real jobs in one process (shared broker client).
            for running_jid, task in self._jobs.items():
                if task is not None and not task.done():
                    running_mode = self._job_modes.get(running_jid, True)
                    if running_mode != sandbox:
                        return _json_response(
                            {
                                "ok": False,
                                "error": "Нельзя одновременно запускать sandbox и real джобы в одном процессе.",
                            },
                            status=409,
                        )

            # Switch broker client mode for this job.
            await broker_client.set_credentials(token=settings.token, sandbox=sandbox)  # type: ignore[arg-type]
            await broker_client.ainit()

            meta = self._job_defs[jid]
            figi = meta["figi"]
            strat: StrategyName = meta["strategy"]

            # Create strategy instance with same args as legacy main (but no autostart).
            from app.instruments_config.parser import instruments_config as cfg

            inst_cfg = next(i for i in cfg.instruments if i.figi == figi and i.strategy.name == strat)
            extra_kwargs = {}
            if strat == StrategyName.INTERVAL:
                extra_kwargs = dict(inst_cfg.strategy.parameters)
            strategy = resolve_strategy(
                strategy_name=strat,
                figi=figi,
                instrument_config=inst_cfg,
                global_risk=cfg.global_risk,
                global_execution=cfg.global_execution,
                strategy_params=inst_cfg.strategy.parameters,
                **extra_kwargs,
            )

            async def _run_job():
                try:
                    logger.info("job_started jid=%s sandbox=%s figi=%s strategy=%s", jid, sandbox, figi, strat.value)
                    await strategy.start()
                except asyncio.CancelledError:
                    logger.info("job_cancelled jid=%s", jid)
                    raise
                except Exception:  # noqa: BLE001
                    logger.exception("job_crashed jid=%s", jid)
                    raise

            self._jobs[jid] = asyncio.create_task(_run_job())
            self._job_modes[jid] = sandbox
            return _json_response({"ok": True, "status": "started"})
        except Exception:  # noqa: BLE001
            logger.exception("handle_job_start failed")
            return _json_response({"ok": False, "error": "Внутренняя ошибка сервера. Смотрите логи контейнера."}, status=500)
        if jid not in self._job_defs:
            return _json_response({"ok": False, "error": "unknown job_id"}, status=404)
        if not broker_client.credentials_set():
            return _json_response({"ok": False, "error": "set token first"}, status=400)
        t = self._jobs.get(jid)
        if t is not None and not t.done():
            return _json_response({"ok": True, "status": "already_running"})

        # Safety: do not allow real trading unless explicitly enabled in env + confirmed in UI.
        if not sandbox:
            if not settings.i_know_what_i_am_doing:
                return _json_response(
                    {
                        "ok": False,
                        "error": "real trading is locked. Set I_KNOW_WHAT_I_AM_DOING=true in env to enable real mode.",
                    },
                    status=403,
                )
            if not confirm:
                return _json_response({"ok": False, "error": "confirmation required for real mode"}, status=400)

        # Disallow mixing sandbox/real jobs in one process (shared broker client).
        for running_jid, task in self._jobs.items():
            if task is not None and not task.done():
                running_mode = self._job_modes.get(running_jid, True)
                if running_mode != sandbox:
                    return _json_response(
                        {
                            "ok": False,
                            "error": "cannot run sandbox and real jobs одновременно в одном процессе",
                        },
                        status=409,
                    )

        # Switch broker client mode for this job.
        # (token is already stored inside broker_client from /api/credentials)
        await broker_client.set_credentials(token=settings.token or "", sandbox=sandbox)
        await broker_client.ainit()

        meta = self._job_defs[jid]
        figi = meta["figi"]
        strat: StrategyName = meta["strategy"]

        # Create strategy instance with same args as legacy main (but no autostart).
        from app.instruments_config.parser import instruments_config as cfg

        inst_cfg = next(i for i in cfg.instruments if i.figi == figi and i.strategy.name == strat)
        extra_kwargs = {}
        if strat == StrategyName.INTERVAL:
            extra_kwargs = dict(inst_cfg.strategy.parameters)
        strategy = resolve_strategy(
            strategy_name=strat,
            figi=figi,
            instrument_config=inst_cfg,
            global_risk=cfg.global_risk,
            global_execution=cfg.global_execution,
            strategy_params=inst_cfg.strategy.parameters,
            **extra_kwargs,
        )

        async def _run_job():
            await strategy.start()

        self._jobs[jid] = asyncio.create_task(_run_job())
        self._job_modes[jid] = sandbox
        return _json_response({"ok": True, "status": "started"})

    async def handle_job_stop(self, request: web.Request) -> web.Response:
        body = await request.json()
        jid = body.get("job_id")
        t = self._jobs.get(jid)
        if t is None or t.done():
            return _json_response({"ok": True, "status": "already_stopped"})
        t.cancel()
        return _json_response({"ok": True, "status": "stopping"})

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

    async def handle_broker_portfolio(self, request: web.Request) -> web.Response:
        aid = await self._account_id()
        if not aid:
            return _json_response(
                {
                    "error": "ACCOUNT_ID ещё не определён. Укажите ACCOUNT_ID в «Доступ» или используйте sandbox-аккаунт."
                },
                status=503,
            )
        try:
            p = await broker_client.get_portfolio(account_id=aid)
            # keep only a few stable fields
            return _json_response(
                {
                    "total_amount_portfolio": getattr(p, "total_amount_portfolio", None),
                    "total_amount_currencies": getattr(p, "total_amount_currencies", None),
                    "total_amount_shares": getattr(p, "total_amount_shares", None),
                    "total_amount_bonds": getattr(p, "total_amount_bonds", None),
                    "total_amount_etf": getattr(p, "total_amount_etf", None),
                    "total_amount_futures": getattr(p, "total_amount_futures", None),
                }
            )
        except Exception as e:  # noqa: BLE001
            return _json_response({"error": str(e)}, status=500)

    async def handle_broker_open_orders(self, request: web.Request) -> web.Response:
        aid = await self._account_id()
        if not aid:
            return _json_response(
                {
                    "error": "ACCOUNT_ID ещё не определён. Укажите ACCOUNT_ID в «Доступ» или используйте sandbox-аккаунт."
                },
                status=503,
            )
        try:
            resp = await broker_client.get_orders(account_id=aid)
            items = []
            for o in getattr(resp, "orders", []):
                items.append(
                    {
                        "order_id": getattr(o, "order_id", None),
                        "figi": getattr(o, "figi", None),
                        "direction": str(getattr(o, "direction", "")),
                        "lots_requested": getattr(o, "lots_requested", None),
                        "execution_report_status": str(getattr(o, "execution_report_status", "")),
                    }
                )
            return _json_response({"items": items})
        except Exception as e:  # noqa: BLE001
            return _json_response({"error": str(e)}, status=500)

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

    async def handle_kill_switch_toggle(self, request: web.Request) -> web.Response:
        """
        Toggle kill-switch file (create/delete) in /data (PVC).
        """
        ks_path = self._kill_switch_path()
        was_active = ks_path.exists()
        try:
            if was_active:
                ks_path.unlink()
                return _json_response({"ok": True, "action": "deleted", "kill_switch_active": False})
            else:
                ks_path.parent.mkdir(parents=True, exist_ok=True)
                ks_path.write_text("1")
                return _json_response({"ok": True, "action": "created", "kill_switch_active": True})
        except Exception as e:  # noqa: BLE001
            return _json_response({"ok": False, "error": str(e)}, status=500)

    async def handle_refresh_open_orders(self, request: web.Request) -> web.Response:
        """
        Refresh statuses of non-terminal OMS orders.
        """
        aid = await self._account_id()
        if not aid:
            return _json_response({"error": "account_id not resolved yet"}, status=503)
        from core.oms.order_manager import OrderManager

        oms = OrderManager(broker=broker_client, store=self.store, account_id=aid)
        updated = 0
        for oid in self.store.list_open_orders():
            try:
                await oms.refresh_order_state(oid)
                updated += 1
            except Exception:  # noqa: BLE001
                continue
        return _json_response({"ok": True, "updated": updated})

    async def handle_reconcile(self, request: web.Request) -> web.Response:
        """
        Snapshot current broker positions into store + refresh open orders.
        """
        aid = await self._account_id()
        if not aid:
            return _json_response({"error": "account_id not resolved yet"}, status=503)
        from core.models.entities import Position
        from core.oms.order_manager import OrderManager

        oms = OrderManager(broker=broker_client, store=self.store, account_id=aid)
        pos_upserts = 0
        try:
            portfolio = await broker_client.get_portfolio(account_id=aid)
            for pos in getattr(portfolio, "positions", []):
                figi = getattr(pos, "figi", None)
                if not figi:
                    continue
                qty = 0
                q = getattr(pos, "quantity", None)
                if q is not None:
                    try:
                        qty = int(Decimal(q.units) + (Decimal(q.nano) / Decimal(1_000_000_000)))
                    except Exception:  # noqa: BLE001
                        qty = 0
                if qty == 0:
                    continue
                self.store.upsert_position(
                    Position(figi=str(figi), qty=int(qty), updated_at=datetime.now(timezone.utc))
                )
                pos_upserts += 1
        except Exception:  # noqa: BLE001
            pass

        updated = 0
        for oid in self.store.list_open_orders():
            try:
                await oms.refresh_order_state(oid)
                updated += 1
            except Exception:  # noqa: BLE001
                continue
        return _json_response({"ok": True, "positions_upserted": pos_upserts, "orders_refreshed": updated})


async def start_ui_server(*, store: StateStore, host: str, port: int) -> web.AppRunner:
    srv = UiServer(store=store)

    @web.middleware
    async def error_middleware(request: web.Request, handler):
        try:
            return await handler(request)
        except web.HTTPException:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("unhandled UI API error: %s %s", request.method, request.path)
            return _json_response(
                {"ok": False, "error": "Внутренняя ошибка сервера. Смотрите логи контейнера."},
                status=500,
            )

    app = web.Application(middlewares=[error_middleware])
    app.router.add_get("/", srv.handle_index)
    app.router.add_get("/api/status", srv.handle_status)
    app.router.add_post("/api/credentials", srv.handle_credentials)
    app.router.add_get("/api/jobs", srv.handle_jobs)
    app.router.add_post("/api/jobs/start", srv.handle_job_start)
    app.router.add_post("/api/jobs/stop", srv.handle_job_stop)
    app.router.add_get("/api/jobs/decisions", srv.handle_job_decisions)
    app.router.add_get("/api/strategies", srv.handle_strategies)
    app.router.add_get("/api/positions", srv.handle_positions)
    app.router.add_get("/api/orders", srv.handle_orders)
    app.router.add_get("/api/fills", srv.handle_fills)
    app.router.add_get("/api/broker/portfolio", srv.handle_broker_portfolio)
    app.router.add_get("/api/broker/open-orders", srv.handle_broker_open_orders)
    app.router.add_post("/api/control/pause", srv.handle_pause)
    app.router.add_post("/api/control/resume", srv.handle_resume)
    app.router.add_post("/api/control/cooldown", srv.handle_cooldown)
    app.router.add_post("/api/control/kill-switch-toggle", srv.handle_kill_switch_toggle)
    app.router.add_post("/api/control/refresh-open-orders", srv.handle_refresh_open_orders)
    app.router.add_post("/api/control/reconcile", srv.handle_reconcile)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host=host, port=port)
    await site.start()
    return runner

