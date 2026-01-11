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
          <h3>➕ Создать джобу</h3>
          <div class="muted">Поиск фьючерса → выбор стратегии → сохранить джобу (появится в списке выше)</div>
          <input id="futQuery" placeholder="Поиск фьючерса: тикер или название (например, Si, BR, Gazp)" />
          <div class="btn-group">
            <button onclick="searchFutures()">🔎 Найти</button>
          </div>
          <div class="muted" style="margin-top:0.5rem;">Результаты:</div>
          <select id="futSelect" style="width:100%; padding:0.75rem; background: var(--bg-primary); border: 1px solid var(--border); border-radius: 6px; color: var(--text-primary);">
            <option value="">— выберите фьючерс —</option>
          </select>
          <input id="figiInput" placeholder="FIGI (можно вставить вручную)" />
          <div class="muted" style="margin-top:0.5rem;">Стратегия:</div>
          <select id="strategySelect" style="width:100%; padding:0.75rem; background: var(--bg-primary); border: 1px solid var(--border); border-radius: 6px; color: var(--text-primary);">
            <option value="donchian_atr">donchian_atr</option>
            <option value="ema_atr">ema_atr</option>
            <option value="trend_breakout_atr">trend_breakout_atr</option>
            <option value="intraday_vwap_momentum">intraday_vwap_momentum</option>
            <option value="intraday_bollinger_rsi">intraday_bollinger_rsi</option>
          </select>
          <div class="muted" style="margin-top:0.75rem;">Описание стратегии:</div>
          <div id="strategyDesc" class="muted" style="white-space: pre-wrap; line-height: 1.45; background: rgba(15, 23, 42, 0.6); border: 1px solid var(--border); border-radius: 8px; padding: 0.75rem;">
            —
          </div>
          <div class="muted" style="margin-top:0.5rem;">Параметры (JSON):</div>
          <textarea id="paramsInput" style="width:100%; min-height:120px; padding:0.75rem; background: var(--bg-primary); border: 1px solid var(--border); border-radius: 6px; color: var(--text-primary); font-family: 'Monaco','Menlo',monospace; font-size: 0.85rem;">{"breakout_lookback":20,"exit_lookback":10,"atr_period":14,"atr_stop_mult":"3","base_target_qty":1}</textarea>
          <div class="muted" style="margin-top:0.75rem;">Лимиты (безопасность):</div>
          <div class="status-grid" style="grid-template-columns: 1fr 1fr;">
            <div class="status-item">
              <span class="status-label">Max позиция (контрактов)</span>
              <input id="maxPosInput" type="number" min="1" step="1" value="10" style="width:100%; margin-top:0.35rem;" />
            </div>
            <div class="status-item">
              <span class="status-label">Max ордер (контрактов)</span>
              <input id="maxOrderInput" type="number" min="1" step="1" value="10" style="width:100%; margin-top:0.35rem;" />
            </div>
          </div>
          <div class="btn-group">
            <button class="success" onclick="createJob()">💾 Сохранить джобу</button>
          </div>
          <div class="muted">После сохранения джоба появится в «Торговые джобы». Запуск — кнопкой «Старт».</div>
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
          <h3>🧪 Dry‑run (бэктест на 90 дней)</h3>
          <div class="muted">Прогон стратегии на исторических данных без реальной торговли. Требуется токен для загрузки свечей.</div>
          <div class="row" style="grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 1rem;">
            <div>
              <div class="muted">Джоба</div>
              <select id="btJobSelect" style="width:100%; padding:0.75rem; background: var(--bg-primary); border: 1px solid var(--border); border-radius: 6px; color: var(--text-primary);">
                <option value="">— выберите джобу —</option>
              </select>
            </div>
            <div>
              <div class="muted">Стартовый капитал (RUB)</div>
              <input id="btEquity" placeholder="например 1000000" value="1000000" />
            </div>
            <div>
              <div class="muted">Горизонт (дней)</div>
              <input id="btDays" placeholder="90" value="90" />
            </div>
            <div style="display:flex; align-items:end;">
              <button class="success" onclick="runBacktest()">▶ Запустить dry‑run</button>
            </div>
          </div>
          <div class="muted" id="btStatus" style="margin-top:0.75rem;">—</div>
          <div class="table-container" style="margin-top:0.75rem;">
            <table id="btSummaryTbl">
              <thead><tr><th>Стратегия</th><th>FIGI</th><th>Капитал старт</th><th>Капитал финал</th><th>PnL</th><th>Max DD</th><th>Сделок</th><th>Winrate</th></tr></thead>
              <tbody></tbody>
            </table>
          </div>
          <div class="table-container" style="margin-top:0.75rem;">
            <table id="btTradesTbl">
              <thead><tr><th>Время</th><th>Сторона</th><th>Кол-во</th><th>Цена</th><th>P&L</th><th>Комиссия</th></tr></thead>
              <tbody></tbody>
            </table>
          </div>
          <div class="muted" style="margin-top:0.75rem;">График цены и точки сделок</div>
          <div class="table-container" style="margin-top:0.5rem; padding: 0.75rem;">
            <div style="display:flex; justify-content: space-between; align-items:center; gap: 0.5rem; margin-bottom: 0.5rem;">
              <div class="muted">Колесо мыши: zoom • Перетаскивание: пан</div>
              <div style="display:flex; gap: 0.5rem; align-items:center;">
                <button id="btZoomOut" class="warning" style="padding: 0.45rem 0.7rem;">−</button>
                <button id="btZoomReset" style="padding: 0.45rem 0.7rem;">Сброс</button>
                <button id="btZoomIn" class="success" style="padding: 0.45rem 0.7rem;">+</button>
              </div>
            </div>
            <canvas id="btPriceChart" height="260" style="width:100%; display:block;"></canvas>
          </div>
        </div>
      </div>

      <div class="row">
        <div class="card fade-in" style="grid-column: 1 / -1;">
          <h3>📥 Выгрузка свечей (для локального дебага)</h3>
          <div class="muted">Выберите стратегию и параметры — выгрузка соберёт нужные таймфреймы (и warmup) именно под неё.</div>
          <div class="row" style="grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 1rem; margin-top: 0.75rem;">
            <div>
              <div class="muted">Джоба (быстрый выбор FIGI)</div>
              <select id="exportJobSelect" style="width:100%; padding:0.75rem; background: var(--bg-primary); border: 1px solid var(--border); border-radius: 6px; color: var(--text-primary);">
                <option value="">— выберите джобу —</option>
              </select>
            </div>
            <div>
              <div class="muted">FIGI</div>
              <input id="exportFigi" placeholder="например FXXXXX..." />
            </div>
            <div>
              <div class="muted">Стратегия</div>
              <select id="exportStrategy" style="width:100%; padding:0.75rem; background: var(--bg-primary); border: 1px solid var(--border); border-radius: 6px; color: var(--text-primary);">
                <option value="donchian_atr">donchian_atr</option>
                <option value="ema_atr">ema_atr</option>
                <option value="trend_breakout_atr">trend_breakout_atr</option>
                <option value="intraday_vwap_momentum">intraday_vwap_momentum</option>
                <option value="intraday_bollinger_rsi">intraday_bollinger_rsi</option>
              </select>
            </div>
            <div>
              <div class="muted">Горизонт (дней)</div>
              <input id="exportDays" placeholder="например 30" value="30" />
            </div>
            <div>
              <div class="muted">Формат</div>
              <select id="exportFormat" style="width:100%; padding:0.75rem; background: var(--bg-primary); border: 1px solid var(--border); border-radius: 6px; color: var(--text-primary);">
                <option value="csv">CSV</option>
                <option value="jsonl">JSONL</option>
              </select>
            </div>
            <div style="display:flex; align-items:end;">
              <button class="success" onclick="downloadCandlesForStrategy()">⬇ Скачать свечи</button>
            </div>
          </div>
          <div class="muted" style="margin-top:0.5rem;">Параметры стратегии (JSON):</div>
          <textarea id="exportParamsInput" style="width:100%; min-height:120px; padding:0.75rem; background: var(--bg-primary); border: 1px solid var(--border); border-radius: 6px; color: var(--text-primary); font-family: 'Monaco','Menlo',monospace; font-size: 0.85rem;">{}</textarea>
          <div class="muted" id="exportStatus" style="margin-top:0.75rem;">—</div>
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
              const actions = document.createElement('div');
              actions.style.display = 'flex';
              actions.style.gap = '0.5rem';
              actions.style.flexWrap = 'wrap';
              actions.appendChild(btn);

              if (j.can_delete) {
                const del = document.createElement('button');
                del.className = 'danger';
                del.textContent = '🗑 Удалить';
                del.onclick = async () => {
                  const ok = window.confirm('Удалить джобу? Она исчезнет из списка и из state.db.');
                  if (!ok) return;
                  const res = await jpost('/api/jobs/delete', { job_id: j.job_id });
                  if (res && res.ok === false) {
                    alert(res.error || 'Не удалось удалить джобу');
                    return;
                  }
                  await refresh();
                };
                if (j.status === 'running') {
                  del.disabled = true;
                  del.style.opacity = '0.5';
                  del.style.cursor = 'not-allowed';
                  del.title = 'Сначала остановите джобу';
                }
                actions.appendChild(del);
              }

              tr.children[5].appendChild(actions);
              tb.appendChild(tr);
            }
          }

          // Backtest job selector
          const btSel = document.getElementById('btJobSelect');
          if (btSel) {
            const cur = btSel.value;
            btSel.innerHTML = '<option value=\"\">— выберите джобу —</option>';
            for (const j of (jobs.items || [])) {
              const opt = document.createElement('option');
              opt.value = j.job_id;
              opt.textContent = `${j.strategy} • ${j.figi}`;
              btSel.appendChild(opt);
            }
            if (cur) btSel.value = cur;
          }

          // Export candles job selector
          const exSel = document.getElementById('exportJobSelect');
          if (exSel) {
            const cur = exSel.value;
            exSel.innerHTML = '<option value=\"\">— выберите джобу —</option>';
            for (const j of (jobs.items || [])) {
              const opt = document.createElement('option');
              opt.value = j.job_id;
              opt.textContent = `${j.strategy} • ${j.figi}`;
              exSel.appendChild(opt);
            }
            if (cur) exSel.value = cur;
            exSel.onchange = () => {
              const jid = (exSel.value || '').trim();
              if (!jid) return;
              const found = (jobs.items || []).find(x => x.job_id === jid);
              if (found && found.figi) {
                document.getElementById('exportFigi').value = found.figi;
              }
              if (found && found.strategy) {
                const sel = document.getElementById('exportStrategy');
                if (sel) sel.value = found.strategy;
                const ep = document.getElementById('exportParamsInput');
                if (ep) {
                  try { ep.value = JSON.stringify(defaultParams(found.strategy)); }
                  catch { ep.value = '{}'; }
                }
              }
            };
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

      async function runBacktest() {
        const job_id = (document.getElementById('btJobSelect').value || '').trim();
        const initial_equity = (document.getElementById('btEquity').value || '').trim();
        const days = parseInt((document.getElementById('btDays').value || '90').trim(), 10);
        if (!job_id) { alert('Выберите джобу'); return; }
        if (!initial_equity) { alert('Введите стартовый капитал'); return; }
        const st = document.getElementById('btStatus');
        if (st) st.textContent = '⏳ Выполняю dry‑run...';
        const res = await jpost('/api/backtest/run', { job_id, initial_equity, days });
        if (!res || res.ok === false) {
          if (st) st.textContent = 'Ошибка: ' + (res.error || 'неизвестно');
          alert(res.error || 'Не удалось выполнить dry‑run');
          return;
        }
        if (st) st.textContent = '✅ Готово';

        // Summary table
        const sumTb = document.querySelector('#btSummaryTbl tbody');
        if (sumTb) {
          sumTb.innerHTML = '';
          const tr = document.createElement('tr');
          const pnl = (res.summary && res.summary.total_pnl) ? formatMoney(res.summary.total_pnl) : '';
          const mdd = (res.summary && res.summary.max_drawdown) ? formatMoney(res.summary.max_drawdown) : '';
          const trades = (res.summary && res.summary.trades !== undefined) ? res.summary.trades : '';
          const winrate = (res.summary && res.summary.winrate !== undefined) ? (Math.round(res.summary.winrate * 10000)/100).toFixed(2) + '%' : '';
          tr.innerHTML = `<td>${res.strategy}</td><td><code>${res.figi}</code></td><td>${formatMoney(res.initial_equity)}</td><td>${formatMoney(res.final_equity)}</td><td>${pnl}</td><td>${mdd}</td><td>${trades}</td><td>${winrate}</td>`;
          sumTb.appendChild(tr);
        }

        // Trades
        fillTable('btTradesTbl', res.trades || [], ['ts','side','qty','price','pnl','commission'], {
          ts: formatDate,
          side: v => (String(v).toLowerCase().includes('buy') ? badge('BUY','success') : badge('SELL','danger')),
          price: v => v ? formatMoney(v) : '',
          pnl: v => {
            if (v === null || v === undefined || v === '') return '';
            const n = parseFloat(v);
            const s = formatMoney(v);
            if (isNaN(n)) return s;
            if (n > 0) return `<span style="color: var(--success); font-weight: 600;">${s}</span>`;
            if (n < 0) return `<span style="color: var(--danger); font-weight: 600;">${s}</span>`;
            return s;
          },
          commission: v => v ? formatMoney(v) : '',
        });

        // Price chart + trades overlay
        try {
          drawBacktestPriceChart(res.price_series || [], res.trades || []);
        } catch (e) {
          console.warn('Chart draw failed', e);
        }
      }

      async function downloadCandlesForStrategy() {
        const figi = (document.getElementById('exportFigi').value || '').trim();
        const strategy = (document.getElementById('exportStrategy').value || '').trim();
        const paramsText = (document.getElementById('exportParamsInput').value || '').trim();
        const days = parseInt((document.getElementById('exportDays').value || '30').trim(), 10);
        const fmt = (document.getElementById('exportFormat').value || 'csv').trim();
        if (!figi) { alert('FIGI обязателен'); return; }
        if (!strategy) { alert('Стратегия обязательна'); return; }
        if (!days || days <= 0) { alert('Горизонт (дней) должен быть > 0'); return; }
        let params = {};
        try { params = paramsText ? JSON.parse(paramsText) : {}; }
        catch (e) { alert('Параметры должны быть валидным JSON'); return; }

        // Persist
        state.set('export_strategy', strategy);
        state.set('export_params', params);
        state.set('export_days', days);
        state.set('export_format', fmt);

        const st = document.getElementById('exportStatus');
        if (st) st.textContent = '⏳ Загружаю свечи...';

        const status = await jget('/api/status');
        if (!status || !status.token_set) {
          if (st) st.textContent = 'Ошибка: токен не задан';
          alert('Сначала установите токен (карточка "Доступ").');
          return;
        }

        try {
          const r = await fetch('/api/candles/export/strategy', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ figi, strategy, params, days, format: fmt })
          });
          if (!r.ok) {
            const j = await r.json().catch(() => null);
            const msg = (j && (j.error || j.message)) ? (j.error || j.message) : `HTTP ${r.status}`;
            if (st) st.textContent = 'Ошибка: ' + msg;
            alert(msg);
            return;
          }
          const blob = await r.blob();
          let filename = `candles_${strategy}_${figi}_${days}d.zip`;
          const cd = r.headers.get('Content-Disposition') || '';
          const m = cd.match(/filename=\"?([^\";]+)\"?/i);
          if (m && m[1]) filename = m[1];
          const a = document.createElement('a');
          const objUrl = URL.createObjectURL(blob);
          a.href = objUrl;
          a.download = filename;
          document.body.appendChild(a);
          a.click();
          a.remove();
          URL.revokeObjectURL(objUrl);
          if (st) st.textContent = `✅ Готово: ${filename}`;
        } catch (e) {
          console.error('downloadCandlesForStrategy failed', e);
          if (st) st.textContent = 'Ошибка: не удалось скачать файл';
          alert('Не удалось скачать файл (см. консоль).');
        }
      }

      // Backtest chart view state (zoom/pan) – index window.
      const btChartState = { start: 0, end: 0, n: 0 };
      function btClamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }
      function btEnsureWindow(n) {
        if (!n || n < 2) return;
        if (btChartState.n !== n || btChartState.end <= btChartState.start) {
          btChartState.n = n;
          btChartState.start = 0;
          btChartState.end = n - 1;
        }
      }
      function btSetZoom(n, factor, anchorFrac) {
        btEnsureWindow(n);
        const minWin = Math.min(80, n - 1); // don't zoom too far in
        const maxWin = n - 1;
        const curWin = btChartState.end - btChartState.start;
        const newWin = btClamp(Math.round(curWin * factor), minWin, maxWin);
        const a = btClamp(anchorFrac ?? 0.5, 0, 1);
        const anchorIdx = btChartState.start + Math.round(curWin * a);
        let newStart = anchorIdx - Math.round(newWin * a);
        let newEnd = newStart + newWin;
        if (newStart < 0) { newStart = 0; newEnd = newWin; }
        if (newEnd > n - 1) { newEnd = n - 1; newStart = (n - 1) - newWin; }
        btChartState.start = btClamp(newStart, 0, n - 2);
        btChartState.end = btClamp(newEnd, btChartState.start + 1, n - 1);
      }
      function btPan(n, deltaFrac) {
        btEnsureWindow(n);
        const win = btChartState.end - btChartState.start;
        const shift = Math.round(win * deltaFrac);
        let ns = btChartState.start + shift;
        let ne = btChartState.end + shift;
        if (ns < 0) { ns = 0; ne = win; }
        if (ne > n - 1) { ne = n - 1; ns = (n - 1) - win; }
        btChartState.start = btClamp(ns, 0, n - 2);
        btChartState.end = btClamp(ne, btChartState.start + 1, n - 1);
      }
      function btReset(n) {
        btChartState.n = n;
        btChartState.start = 0;
        btChartState.end = Math.max(1, n - 1);
      }

      function drawBacktestPriceChart(priceSeries, trades) {
        const canvas = document.getElementById('btPriceChart');
        if (!canvas) return;
        const ctx = canvas.getContext('2d');
        if (!ctx) return;

        // HiDPI
        const cssW = canvas.clientWidth || 800;
        const cssH = canvas.clientHeight || 260;
        const dpr = window.devicePixelRatio || 1;
        canvas.width = Math.floor(cssW * dpr);
        canvas.height = Math.floor(cssH * dpr);
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

        const w = cssW, h = cssH;
        ctx.clearRect(0, 0, w, h);

        if (!priceSeries || priceSeries.length < 2) {
          ctx.fillStyle = 'rgba(148, 163, 184, 0.9)';
          ctx.font = '14px system-ui';
          ctx.fillText('Нет данных для графика', 12, 20);
          return;
        }

        const padL = 46, padR = 10, padT = 10, padB = 28;
        const iw = w - padL - padR;
        const ih = h - padT - padB;

        // Parse
        const xsAll = [];
        const ysAll = [];
        for (const p of priceSeries) {
          const t = (p.ts || p.time || p.datetime || '').toString();
          const y = parseFloat(p.close);
          if (!t || !isFinite(y)) continue;
          xsAll.push(t);
          ysAll.push(y);
        }
        if (ysAll.length < 2) return;

        // Bind controls once per page
        if (!canvas._btBound) {
          canvas._btBound = true;
          // Wheel zoom
          canvas.addEventListener('wheel', (ev) => {
            ev.preventDefault();
            const rect = canvas.getBoundingClientRect();
            const x = (ev.clientX - rect.left);
            const frac = btClamp((x - padL) / iw, 0, 1);
            const factor = ev.deltaY < 0 ? 0.8 : 1.25;
            btSetZoom(xsAll.length, factor, frac);
            drawBacktestPriceChart(canvas._btSeries || [], canvas._btTrades || []);
          }, { passive: false });

          // Drag pan
          let dragging = false;
          let lastX = 0;
          canvas.addEventListener('mousedown', (ev) => { dragging = true; lastX = ev.clientX; });
          window.addEventListener('mouseup', () => { dragging = false; });
          window.addEventListener('mousemove', (ev) => {
            if (!dragging) return;
            const dx = ev.clientX - lastX;
            lastX = ev.clientX;
            btPan(xsAll.length, -dx / (iw || 1));
            drawBacktestPriceChart(canvas._btSeries || [], canvas._btTrades || []);
          });

          // Buttons
          const zIn = document.getElementById('btZoomIn');
          const zOut = document.getElementById('btZoomOut');
          const zReset = document.getElementById('btZoomReset');
          if (zIn) zIn.onclick = () => { btSetZoom(xsAll.length, 0.8, 0.5); drawBacktestPriceChart(canvas._btSeries || [], canvas._btTrades || []); };
          if (zOut) zOut.onclick = () => { btSetZoom(xsAll.length, 1.25, 0.5); drawBacktestPriceChart(canvas._btSeries || [], canvas._btTrades || []); };
          if (zReset) zReset.onclick = () => { btReset(xsAll.length); drawBacktestPriceChart(canvas._btSeries || [], canvas._btTrades || []); };
        }

        // Persist latest data for interactive redraws
        canvas._btSeries = priceSeries;
        canvas._btTrades = trades;

        btEnsureWindow(xsAll.length);
        const s0 = btClamp(btChartState.start, 0, xsAll.length - 2);
        const e0 = btClamp(btChartState.end, s0 + 1, xsAll.length - 1);
        const xs = xsAll.slice(s0, e0 + 1);
        const ys = ysAll.slice(s0, e0 + 1);

        let ymin = Math.min(...ys);
        let ymax = Math.max(...ys);
        if (ymax === ymin) { ymax += 1; ymin -= 1; }
        const ypad = (ymax - ymin) * 0.04;
        ymax += ypad; ymin -= ypad;

        const xAt = (i) => padL + (i / (ys.length - 1)) * iw;
        const yAt = (y) => padT + (1 - (y - ymin) / (ymax - ymin)) * ih;

        // Grid
        ctx.strokeStyle = 'rgba(148, 163, 184, 0.10)';
        ctx.lineWidth = 1;
        for (let k = 0; k <= 4; k++) {
          const yy = padT + (k / 4) * ih;
          ctx.beginPath();
          ctx.moveTo(padL, yy);
          ctx.lineTo(padL + iw, yy);
          ctx.stroke();
        }

        // Y axis labels
        ctx.fillStyle = 'rgba(148, 163, 184, 0.9)';
        ctx.font = '12px system-ui';
        for (let k = 0; k <= 4; k++) {
          const yv = ymin + (1 - k / 4) * (ymax - ymin);
          const yy = padT + (k / 4) * ih;
          ctx.fillText(yv.toFixed(0), 8, yy + 4);
        }

        // Price line
        ctx.strokeStyle = 'rgba(56, 189, 248, 0.95)'; // cyan
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.moveTo(xAt(0), yAt(ys[0]));
        for (let i = 1; i < ys.length; i++) {
          ctx.lineTo(xAt(i), yAt(ys[i]));
        }
        ctx.stroke();

        // Map trades by ts to index
        const indexByTs = new Map();
        for (let i = 0; i < xs.length; i++) indexByTs.set(xs[i], i);

        function drawMarker(i, y, side) {
          const x = xAt(i);
          const yy = yAt(y);
          const size = 6;
          if (side === 'buy') {
            ctx.fillStyle = 'rgba(34, 197, 94, 0.95)';
            ctx.beginPath();
            ctx.moveTo(x, yy - size);
            ctx.lineTo(x - size, yy + size);
            ctx.lineTo(x + size, yy + size);
            ctx.closePath();
            ctx.fill();
          } else {
            ctx.fillStyle = 'rgba(239, 68, 68, 0.95)';
            ctx.beginPath();
            ctx.moveTo(x, yy + size);
            ctx.lineTo(x - size, yy - size);
            ctx.lineTo(x + size, yy - size);
            ctx.closePath();
            ctx.fill();
          }
        }

        for (const t of trades || []) {
          const ts = (t.ts || '').toString();
          const side = (t.side || '').toString().toLowerCase().includes('buy') ? 'buy' : 'sell';
          let idx = indexByTs.get(ts);
          if (idx === undefined) {
            // best-effort: try to match by minute string
            const key = ts.replace('Z','').slice(0,16);
            for (let i = 0; i < xs.length; i++) {
              const k = xs[i].replace('Z','').slice(0,16);
              if (k === key) { idx = i; break; }
            }
          }
          if (idx === undefined) continue;
          drawMarker(idx, ys[idx], side);
        }

        // X labels (start/end)
        ctx.fillStyle = 'rgba(148, 163, 184, 0.9)';
        ctx.font = '12px system-ui';
        ctx.fillText(formatDate(xs[0]), padL, h - 10);
        const endTxt = formatDate(xs[xs.length - 1]);
        const tw = ctx.measureText(endTxt).width;
        ctx.fillText(endTxt, padL + iw - tw, h - 10);
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

      function defaultParams(strategy) {
        if (strategy === 'ema_atr') {
          return {"ema_fast":20,"ema_slow":50,"atr_period":14,"atr_stop_mult":"3","cooldown_days":5,"base_target_qty":1};
        }
        if (strategy === 'trend_breakout_atr') {
          return {"timeframe":"1h","breakout_lookback":20,"exit_lookback":10,"atr_period":14,"atr_stop_mult":"2.0","atr_tp_mult":"3.0","atr_trail_mult":"1.5","trend_ema_fast":20,"trend_ema_slow":50,"risk_per_trade_pct":0.5,"volume_window":20,"min_volume_ratio":"1.2","trade_sessions":[["10:00","18:45"]],"exit_before_close_minutes":30,"days_before_expiry_to_roll":5};
        }
        if (strategy === 'intraday_vwap_momentum') {
          // Default preset: active but not too noisy.
          // If you want more trades/day: set require_fast_slope=false and min_volume_ratio=1.0..1.1
          return {"timeframe":"1min","vwap_window":60,"ema_fast":10,"ema_slow":30,"require_fast_slope":false,"entry_mode":"cross_or_retest","retest_lookback":10,"require_retest_breakout":true,"trend_timeframe":"1h","trend_ema_fast":20,"trend_ema_slow":50,"atr_period":14,"atr_sl_mult":"1.5","atr_tp_mult":"3.0","atr_trail_mult":"1.0","risk_per_trade_pct":0.25,"volume_window":30,"min_volume_ratio":"1.1","cooldown_bars":5,"exit_before_session_end_minutes":10,"exit_confirm_bars":2,"exit_on_vwap_cross":true,"trade_sessions":[["10:15","12:30"],["14:00","16:30"]]};
        }
        if (strategy === 'intraday_bollinger_rsi') {
          return {"timeframe":"5min","bollinger_period":12,"bollinger_std_mult":"2.0","rsi_period":15,"rsi_overbought":"69","rsi_oversold":"31","atr_period":19,"sl_atr_mult":"1.6","tp_atr_mult":"1.1","risk_per_trade_pct":0.3,"volume_window":24,"min_volume_ratio":"1.1","trade_sessions":[["10:15","17:30"]],"cooldown_bars":1};
        }
        // donchian_atr
        return {"breakout_lookback":20,"exit_lookback":10,"atr_period":14,"atr_stop_mult":"3","base_target_qty":1};
      }

      function strategyDescription(strategy) {
        if (strategy === 'ema_atr') {
          return [
            'EMA + ATR (D1, позиционная)',
            '',
            'Идея: торговать по тренду на дневках.',
            'Сигнал формируется по закрытию дня.',
            '',
            'Лонг-режим:',
            '- Close > EMA(slow) и EMA(fast) > EMA(slow) → цель: держать/встать в лонг (target_qty = +base_target_qty)',
            '',
            'Шорт-режим:',
            '- Close < EMA(slow) и EMA(fast) < EMA(slow) → цель: держать/встать в шорт (target_qty = -base_target_qty)',
            '',
            'Выход:',
            '- Если bias пропал/сменился → цель: выйти в 0 (target_qty = 0)',
            '',
            'ATR:',
            '- ATR считается для оценки волатильности и передаётся в RiskGate (может масштабировать размер).',
            '',
            'Параметры:',
            '- ema_fast / ema_slow: периоды EMA',
            '- atr_period: период ATR',
            '- atr_stop_mult: множитель ATR (для стоп-логики/сайзинга; стоп может быть реализован на уровне раннера)',
            '- cooldown_days: пауза после выхода (для уменьшения “пилы”)',
            '- base_target_qty: базовая целевая позиция (в контрактах)',
          ].join('\\n');
        }
        if (strategy === 'trend_breakout_atr') {
          return [
            'Trend Breakout + Donchian + ATR (1h/4h + D1 EMA fast/slow + объём/сессии + трейлинг)',
            '',
            'Идея: торговать пробои канала Дончиана на 1H/4H, но входить только по направлению долгосрочного тренда (D1).',
            'Тренд: EMA_fast и EMA_slow на D1. Лонг только если Close(D1) > EMA_slow и EMA_fast > EMA_slow; шорт — зеркально.',
            '',
            'Данные:',
            '- Базовый ТФ: 1h или 4h (4h агрегируется из 1h свечей).',
            '- Тренд: дневные свечи (D1) для EMA fast/slow.',
            '',
            'Вход:',
            '- Лонг: Close > DonchianUpper(breakout_lookback), тренд вверх, фильтр по объёму и торговым часам.',
            '- Шорт: Close < DonchianLower(breakout_lookback), тренд вниз, фильтр по объёму и торговым часам.',
            '',
            'Размер позиции:',
            '- Риск на сделку: risk_per_trade_pct% от equity.',
            '- Кол-во ≈ risk_budget / (ATR × atr_stop_mult × price_multiplier × lot). (price_multiplier берётся из спецификации фьючерса, best-effort)',
            '',
            'Выход:',
            '- Канал выхода (exit_lookback): выход при пробое противоположной границы.',
            '- Стоп-лосс/тейк-профит по ATR (контроль по закрытию свечи):',
            '  SL = entry ± atr_stop_mult × ATR, TP = entry ± atr_tp_mult × ATR.',
            '- Трейлинг-стоп по ATR: стоп подтягивается за ценой на atr_trail_mult × ATR.',
            '- За N минут до конца сессии (exit_before_close_minutes) — выход в 0.',
            '',
            'Параметры:',
            '- timeframe: "1h" или "4h"',
            '- breakout_lookback: окно Дончиана для входа',
            '- exit_lookback: окно Дончиана для выхода',
            '- trend_ema_fast / trend_ema_slow: EMA на дневках для фильтра тренда',
            '- atr_period: период ATR',
            '- atr_stop_mult / atr_tp_mult / atr_trail_mult: множители SL/TP/трейлинга',
            '- volume_window / min_volume_ratio: фильтр объёма',
            '- trade_sessions: торговые окна (MSK), например [["10:00","18:45"]]',
            '- exit_before_close_minutes: выйти за N минут до конца сессии',
          ].join('\\n');
        }
        if (strategy === 'intraday_vwap_momentum') {
          return [
            'Intraday VWAP Momentum (1m/5m, 10–20 сделок/день)',
            '',
            'Идея: ловить импульсы по направлению локального тренда.',
            'Фильтры: VWAP + EMA(fast/slow) + объём + торговая сессия (MSK) + старший тренд (1h/4h EMA).',
            '',
            'Лонг (вход только из 0):',
            '- Close пересекает VWAP снизу вверх',
            '- Close > VWAP и EMA_fast > EMA_slow, EMA_fast растёт',
            '- объём текущей свечи >= min_volume_ratio × avg(volume_window)',
            '',
            'Шорт (вход только из 0): зеркально.',
            '',
            'Выход:',
            '- SL/TP (контроль по закрытию свечи, уровни выставляет раннер)',
            '- опционально: трейлинг-стоп по ATR (atr_trail_mult)',
            '- ранний выход: EMA_fast пересекла EMA_slow в обратную сторону',
            '- выход за exit_before_session_end_minutes до конца торгового окна',
            '',
            'Сайзинг:',
            '- RiskGate рассчитывает кол-во контрактов по risk_per_trade_pct и стоп-риску на 1 контракт (best-effort по спецификации фьючерса).',
            '',
            'Параметры:',
            '- timeframe: "1min" или "5min"',
            '- vwap_window: окно VWAP (в барах, предпочтительно для 5min) или vwap_period (в минутах, legacy)',
            '- ema_fast / ema_slow',
            '- trend_timeframe ("1h"/"4h") + trend_ema_fast / trend_ema_slow: фильтр старшего тренда',
            '- sl_points / tp_points (в тиках, если тик известен; иначе в “ценовых пунктах”)',
            '- atr_sl_mult / atr_tp_mult (альтернатива фиксированным SL/TP)',
            '- atr_trail_mult: трейлинг стоп по ATR (опционально)',
            '- risk_per_trade_pct',
            '- volume_window / min_volume_ratio',
            '- trade_sessions (MSK), cooldown_bars',
          ].join('\\n');
        }
        if (strategy === 'intraday_bollinger_rsi') {
          return [
            'Intraday Bollinger–RSI Reversion (5m, mean reversion)',
            '',
            'Идея: брать частые короткие сделки на возврате цены к среднему (SMA).',
            'Вход: экстремум по полосам Боллинджера + RSI (перекупленность/перепроданность).',
            'Фильтры: торговые окна (MSK) + повышенный объём + cooldown после сделки.',
            '',
            'Лонг (вход только из 0): Close < нижней полосы и RSI < rsi_oversold.',
            'Шорт (вход только из 0): Close > верхней полосы и RSI > rsi_overbought.',
            '',
            'Выход:',
            '- Возврат к средней: Close пересёк SMA(mid) в обратную сторону',
            '- SL/TP: раннер выставляет уровни по ATR (sl_atr_mult / tp_atr_mult), контроль по close',
            '- Закрытие позиции за 5 минут до конца торгового окна',
            '',
            'Сайзинг:',
            '- RiskGate рассчитывает кол-во контрактов по risk_per_trade_pct и стоп-риску на 1 контракт (ATR × sl_atr_mult × spec).',
            '',
            'Параметры:',
            '- timeframe: "5min" или "1min"',
            '- bollinger_period / bollinger_std_mult',
            '- rsi_period / rsi_overbought / rsi_oversold',
            '- atr_period / sl_atr_mult / tp_atr_mult',
            '- risk_per_trade_pct',
            '- volume_window / min_volume_ratio',
            '- trade_sessions (MSK), cooldown_bars',
          ].join('\\n');
        }
        // donchian_atr
        return [
          'Donchian breakout + ATR (D1, позиционная)',
          '',
          'Идея: входить по пробою канала Дончиана на дневках.',
          'Сигнал формируется по закрытию дня.',
          '',
          'Вход:',
          '- Если Close > DonchianHigh(breakout_lookback) → цель: лонг (target_qty = +base_target_qty)',
          '- Если Close < DonchianLow(breakout_lookback) → цель: шорт (target_qty = -base_target_qty)',
          '',
          'Выход:',
          '- Для лонга: если Close < DonchianLow(exit_lookback) → цель: 0',
          '- Для шорта: если Close > DonchianHigh(exit_lookback) → цель: 0',
          '',
          'ATR:',
          '- ATR считается и прикладывается к сигналу для RiskGate (позиционирование/лимиты).',
          '',
          'Параметры:',
          '- breakout_lookback: окно для канала входа',
          '- exit_lookback: окно для канала выхода',
          '- atr_period: период ATR',
          '- atr_stop_mult: множитель ATR (для стоп-логики/сайзинга)',
          '- base_target_qty: базовая целевая позиция (в контрактах)',
        ].join('\\n');
      }

      async function searchFutures() {
        const q = (document.getElementById('futQuery').value || '').trim();
        if (!q) { alert('Введите запрос для поиска'); return; }
        const res = await jget('/api/broker/futures/search?query=' + encodeURIComponent(q));
        if (res && res.error) { alert(res.error); return; }
        const sel = document.getElementById('futSelect');
        sel.innerHTML = '<option value=\"\">— выберите фьючерс —</option>';
        for (const it of (res.items || [])) {
          const figi = it.figi || '';
          const label = `${it.ticker || ''} • ${it.name || ''} • ${figi}`.trim();
          const opt = document.createElement('option');
          opt.value = figi;
          opt.textContent = label;
          sel.appendChild(opt);
        }
        sel.onchange = () => {
          const v = sel.value || '';
          if (v) document.getElementById('figiInput').value = v;
        };
      }

      function syncParamsTemplate() {
        const s = document.getElementById('strategySelect').value;
        document.getElementById('paramsInput').value = JSON.stringify(defaultParams(s));
        const desc = document.getElementById('strategyDesc');
        if (desc) desc.textContent = strategyDescription(s);
      }
      document.addEventListener('DOMContentLoaded', () => {
        const s = document.getElementById('strategySelect');
        if (s) s.onchange = syncParamsTemplate;
        // initial render
        syncParamsTemplate();

        // Restore export settings
        const es = document.getElementById('exportStrategy');
        const ep = document.getElementById('exportParamsInput');
        if (es) {
          es.value = state.get('export_strategy', 'donchian_atr');
          es.onchange = () => {
            // if user didn't type custom params yet, refresh template on strategy change
            try {
              if (ep && (!ep.value || ep.value.trim() === '' || ep.value.trim() === '{}' )) {
                ep.value = JSON.stringify(defaultParams(es.value || 'donchian_atr'));
              }
            } catch {}
            state.set('export_strategy', es.value);
          };
        }
        if (ep) {
          const saved = state.get('export_params', null);
          if (saved) {
            try { ep.value = JSON.stringify(saved); } catch {}
          }
          if (!ep.value || ep.value.trim() === '') {
            try { ep.value = JSON.stringify(defaultParams((es && es.value) ? es.value : 'donchian_atr')); } catch { ep.value = '{}'; }
          }
          ep.onchange = () => {
            try { state.set('export_params', JSON.parse(ep.value || '{}')); }
            catch {}
          };
        }
        const ed = document.getElementById('exportDays');
        if (ed) {
          const v = state.get('export_days', 30);
          ed.value = (v === null || v === undefined) ? '30' : String(v);
          ed.onchange = () => state.set('export_days', parseInt((ed.value || '30'), 10) || 30);
        }
        const ef = document.getElementById('exportFormat');
        if (ef) {
          ef.value = state.get('export_format', 'csv');
          ef.onchange = () => state.set('export_format', ef.value);
        }
      });

      async function createJob() {
        const figi = (document.getElementById('figiInput').value || '').trim();
        const strategy = (document.getElementById('strategySelect').value || '').trim();
        const paramsText = (document.getElementById('paramsInput').value || '').trim();
        const max_position_qty = parseInt((document.getElementById('maxPosInput').value || '10'), 10);
        const max_order_qty = parseInt((document.getElementById('maxOrderInput').value || '10'), 10);
        if (!figi) { alert('FIGI обязателен'); return; }
        if (!max_position_qty || max_position_qty <= 0) { alert('Max позиция должна быть > 0'); return; }
        if (!max_order_qty || max_order_qty <= 0) { alert('Max ордер должен быть > 0'); return; }
        if (max_order_qty > max_position_qty) { alert('Max ордер не должен быть больше Max позиции'); return; }
        let params = {};
        try { params = paramsText ? JSON.parse(paramsText) : {}; }
        catch (e) { alert('Параметры должны быть валидным JSON'); return; }
        const res = await jpost('/api/jobs/create', { figi, strategy, params, max_position_qty, max_order_qty });
        if (res && res.ok === false) { alert(res.error || 'Не удалось создать джобу'); return; }
        alert('Джоба сохранена: ' + (res.job_id || ''));
        await refresh();
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
            self._job_defs[jid] = {
                "figi": inst.figi,
                "strategy": inst.strategy.name,
                "params": dict(inst.strategy.parameters),
                "instrument_config": inst,
                "source": "config",
            }

        # Load UI-managed jobs from DB (persisted)
        try:
            for j in self.store.list_ui_jobs():
                jid = j["job_id"]
                self._job_defs[jid] = {
                    "figi": j["figi"],
                    "strategy": StrategyName(j["strategy"]),
                    "params": j.get("strategy_params") or {},
                    "max_position_qty": j.get("max_position_qty") or 10,
                    "max_order_qty": j.get("max_order_qty") or (j.get("max_position_qty") or 10),
                    "source": "ui",
                }
        except Exception:  # noqa: BLE001
            pass

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
            src = str(meta.get("source") or ("config" if meta.get("instrument_config") is not None else "ui"))
            items.append(
                {
                    "job_id": jid,
                    "figi": figi,
                    "strategy": strat,
                    "status": status,
                    "mode": mode,
                    "can_delete": src == "ui",
                    "source": src,
                }
            )
        return _json_response({"items": items})

    async def handle_job_create(self, request: web.Request) -> web.Response:
        """
        Create/update a UI-managed job.
        body: { figi, strategy, params, max_position_qty?, max_order_qty? }
        """
        try:
            body = await request.json()
            figi = str((body.get("figi") or "")).strip()
            strategy = str((body.get("strategy") or "")).strip()
            params = body.get("params") or {}
            max_position_qty = body.get("max_position_qty")
            max_order_qty = body.get("max_order_qty")
            if not figi:
                return _json_response({"ok": False, "error": "FIGI обязателен"}, status=400)
            if strategy not in {s.value for s in StrategyName}:
                return _json_response({"ok": False, "error": "Неизвестная стратегия"}, status=400)
            try:
                max_position_qty = int(max_position_qty) if max_position_qty is not None else 10
                max_order_qty = int(max_order_qty) if max_order_qty is not None else max_position_qty
            except Exception:  # noqa: BLE001
                return _json_response({"ok": False, "error": "Лимиты должны быть целыми числами"}, status=400)
            if max_position_qty <= 0 or max_order_qty <= 0:
                return _json_response({"ok": False, "error": "Лимиты должны быть > 0"}, status=400)
            if max_order_qty > max_position_qty:
                return _json_response({"ok": False, "error": "max_order_qty не должен быть больше max_position_qty"}, status=400)
            jid = f"{figi}|{strategy}"

            # persist
            self.store.set_flag(key="trading_enabled", value=self.store.get_flag(key="trading_enabled", default="1"))
            self.store.upsert_ui_job(
                job_id=jid,
                figi=figi,
                strategy_name=strategy,
                strategy_params=dict(params),
                max_position_qty=max_position_qty,
                max_order_qty=max_order_qty,
            )

            # register in memory
            self._job_defs[jid] = {
                "figi": figi,
                "strategy": StrategyName(strategy),
                "params": dict(params),
                "max_position_qty": max_position_qty,
                "max_order_qty": max_order_qty,
                "source": "ui",
            }
            return _json_response({"ok": True, "job_id": jid})
        except Exception:  # noqa: BLE001
            logger.exception("handle_job_create failed")
            return _json_response({"ok": False, "error": "Не удалось создать джобу"}, status=500)

    async def handle_job_delete(self, request: web.Request) -> web.Response:
        body = await request.json()
        jid = str(body.get("job_id") or "")
        if not jid:
            return _json_response({"ok": False, "error": "job_id обязателен"}, status=400)
        meta = self._job_defs.get(jid) or {}
        src = str(meta.get("source") or ("config" if meta.get("instrument_config") is not None else "ui"))
        if src != "ui":
            return _json_response({"ok": False, "error": "Эту джобу нельзя удалить (она из instruments_config.json)."}, status=403)
        t = self._jobs.get(jid)
        if t is not None and not t.done():
            return _json_response({"ok": False, "error": "Сначала остановите джобу"}, status=409)
        try:
            self.store.delete_ui_job(job_id=jid)
        except Exception:  # noqa: BLE001
            pass
        self._job_defs.pop(jid, None)
        return _json_response({"ok": True})

    async def handle_futures_search(self, request: web.Request) -> web.Response:
        """
        Search futures by query (ticker/name). Best-effort; depends on SDK support.
        """
        q = (request.query.get("query") or "").strip()
        if not q:
            return _json_response({"items": []})
        if not broker_client.credentials_set():
            return _json_response({"error": "Сначала установите токен"}, status=400)
        try:
            # SDK search response shape varies; we normalize to {figi, ticker, name, class_code}
            resp = await broker_client.find_instrument(query=q)
            found = getattr(resp, "instruments", None) or getattr(resp, "instruments_found", None) or getattr(resp, "found_instruments", None) or []
            items = []
            for inst in found:
                # best-effort filter: keep only futures-like instruments if field exists
                kind = getattr(inst, "instrument_kind", None) or getattr(inst, "kind", None)
                if kind is not None:
                    s = str(kind).lower()
                    if "futures" not in s:
                        continue
                items.append(
                    {
                        "figi": getattr(inst, "figi", None),
                        "ticker": getattr(inst, "ticker", None),
                        "name": getattr(inst, "name", None),
                        "class_code": getattr(inst, "class_code", None),
                    }
                )
            return _json_response({"items": items[:50]})
        except Exception as e:  # noqa: BLE001
            logger.exception("futures search failed")
            return _json_response({"error": str(e)}, status=500)

    async def handle_candles_export(self, request: web.Request) -> web.Response:
        """
        Download candles for local debugging.

        GET /api/candles/export?figi=...&timeframe=D1|4h|1h|5min|1min&days=30&format=csv|jsonl
        """
        figi = (request.query.get("figi") or "").strip()
        timeframe_raw = (request.query.get("timeframe") or "D1").strip()
        fmt = (request.query.get("format") or "csv").strip().lower()
        try:
            days = int(request.query.get("days") or 30)
        except Exception:  # noqa: BLE001
            days = -1

        if not figi:
            return _json_response({"ok": False, "error": "figi обязателен"}, status=400)
        if days <= 0 or days > 3650:
            return _json_response({"ok": False, "error": "days должно быть в диапазоне 1..3650"}, status=400)
        if fmt not in {"csv", "jsonl", "ndjson"}:
            return _json_response({"ok": False, "error": "format должен быть csv или jsonl"}, status=400)
        if not broker_client.credentials_set():
            return _json_response({"ok": False, "error": "Сначала установите токен"}, status=400)

        import asyncio
        import csv
        import io
        from datetime import timedelta

        from core.data.candles import CandleRepository
        from t_tech.invest import CandleInterval

        # Normalize timeframe
        tf = timeframe_raw.lower().strip()
        if tf in {"d1", "1d", "day", "daily"}:
            tf_norm = "D1"
        elif tf in {"4h", "h4"}:
            tf_norm = "4h"
        elif tf in {"1h", "h1", "hour"}:
            tf_norm = "1h"
        elif tf in {"5min", "m5", "5m"}:
            tf_norm = "5min"
        elif tf in {"1min", "m1", "1m"}:
            tf_norm = "1min"
        else:
            return _json_response({"ok": False, "error": "timeframe должен быть D1/4h/1h/5min/1min"}, status=400)

        # Guardrails: small timeframes can be huge
        if tf_norm == "1min" and days > 10:
            return _json_response({"ok": False, "error": "timeframe=1min слишком тяжёлый: уменьшите days до 10"}, status=400)
        if tf_norm == "5min" and days > 45:
            return _json_response({"ok": False, "error": "timeframe=5min слишком тяжёлый: уменьшите days до 45"}, status=400)

        await broker_client.ainit()
        repo = CandleRepository(broker=broker_client)
        to_ts = datetime.now(timezone.utc)
        # For expired futures it's useful to return "last N days ending at last available candle",
        # so we fetch a small padding window and slice by last candle timestamp (only for heavier frames).
        pad_days = 30 if tf_norm in {"D1", "1h", "4h"} else 0
        from_ts_req = to_ts - timedelta(days=(days + pad_days))

        try:
            if tf_norm == "D1":
                candles_all = await asyncio.wait_for(
                    repo.fetch_range(
                        figi=figi,
                        from_ts=from_ts_req,
                        to_ts=to_ts,
                        interval=CandleInterval.CANDLE_INTERVAL_DAY,
                    ),
                    timeout=120,
                )
            else:
                candles_all = await asyncio.wait_for(
                    repo.fetch_intraday_range(figi=figi, from_ts=from_ts_req, to_ts=to_ts, timeframe=tf_norm),
                    timeout=180,
                )
        except asyncio.TimeoutError:
            return _json_response({"ok": False, "error": "Таймаут при загрузке свечей. Попробуйте уменьшить days."}, status=504)

        if not candles_all:
            return _json_response(
                {
                    "ok": False,
                    "error": "Свечи не найдены. Проверьте FIGI и доступность истории.",
                    "debug": {
                        "figi": figi,
                        "timeframe": tf_norm,
                        "from_ts": from_ts_req.isoformat(),
                        "to_ts": to_ts.isoformat(),
                    },
                },
                status=404,
            )

        # Slice last N days ending at last candle timestamp (best-effort).
        candles = candles_all
        try:
            end_ts = candles_all[-1].time
            cutoff = end_ts - timedelta(days=days)
            candles = [c for c in candles_all if c.time >= cutoff]
            if not candles:
                candles = candles_all
        except Exception:  # noqa: BLE001
            candles = candles_all

        ts_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        ext = "jsonl" if fmt in {"jsonl", "ndjson"} else "csv"
        filename = f"candles_{figi}_{tf_norm}_{days}d_{ts_stamp}.{ext}"
        headers = {"Content-Disposition": f'attachment; filename="{filename}"'}

        if fmt in {"jsonl", "ndjson"}:
            lines = []
            for c in candles:
                lines.append(
                    json.dumps(
                        {
                            "figi": c.figi,
                            "ts": c.time.isoformat(),
                            "open": str(c.open),
                            "high": str(c.high),
                            "low": str(c.low),
                            "close": str(c.close),
                            "volume": int(c.volume),
                        },
                        ensure_ascii=False,
                    )
                )
            data = "\n".join(lines) + "\n"
            return web.Response(text=data, content_type="application/x-ndjson", headers=headers)

        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["ts", "open", "high", "low", "close", "volume"])
        for c in candles:
            w.writerow([c.time.isoformat(), str(c.open), str(c.high), str(c.low), str(c.close), int(c.volume)])
        return web.Response(text=buf.getvalue(), content_type="text/csv", headers=headers)

    async def handle_candles_export_strategy(self, request: web.Request) -> web.Response:
        """
        Export candles specifically required by a strategy (may include multiple timeframes).

        POST /api/candles/export/strategy
        body: { figi: str, strategy: str, params: object, days: int, format: "csv"|"jsonl" }

        Always returns a ZIP:
        - candles_*.csv|jsonl
        - meta.json
        """
        body = await request.json()
        figi = str(body.get("figi") or "").strip()
        strategy = str(body.get("strategy") or "").strip()
        params = body.get("params") or {}
        fmt = str(body.get("format") or "csv").strip().lower()
        try:
            days = int(body.get("days") or 30)
        except Exception:  # noqa: BLE001
            days = -1

        if not figi:
            return _json_response({"ok": False, "error": "figi обязателен"}, status=400)
        if not strategy:
            return _json_response({"ok": False, "error": "strategy обязателен"}, status=400)
        if not isinstance(params, dict):
            return _json_response({"ok": False, "error": "params должен быть объектом (JSON)"}, status=400)
        if days <= 0 or days > 3650:
            return _json_response({"ok": False, "error": "days должно быть в диапазоне 1..3650"}, status=400)
        if fmt not in {"csv", "jsonl", "ndjson"}:
            return _json_response({"ok": False, "error": "format должен быть csv или jsonl"}, status=400)
        if not broker_client.credentials_set():
            return _json_response({"ok": False, "error": "Сначала установите токен"}, status=400)

        import asyncio
        import csv
        import io
        import math
        import zipfile
        from datetime import timedelta

        from core.data.candles import CandleRepository
        from t_tech.invest import CandleInterval

        def _int(v, default: int) -> int:
            try:
                return int(v)
            except Exception:  # noqa: BLE001
                return int(default)

        def _tf_norm(s: str) -> str:
            t = str(s or "").lower().strip()
            if t in {"d1", "1d", "day", "daily"}:
                return "D1"
            if t in {"4h", "h4"}:
                return "4h"
            if t in {"1h", "h1", "hour"}:
                return "1h"
            if t in {"5min", "m5", "5m"}:
                return "5min"
            if t in {"1min", "m1", "1m"}:
                return "1min"
            raise ValueError("timeframe должен быть D1/4h/1h/5min/1min")

        def _bar_minutes(tf: str) -> int:
            if tf == "D1":
                return 24 * 60
            if tf == "4h":
                return 4 * 60
            if tf == "1h":
                return 60
            if tf == "5min":
                return 5
            if tf == "1min":
                return 1
            raise ValueError("unsupported timeframe")

        def _warmup_days_from_bars(tf: str, warmup_bars: int) -> int:
            # Convert bars -> calendar days (rough but safe).
            bm = _bar_minutes(tf)
            return max(2, int(math.ceil((max(0, int(warmup_bars)) * bm) / (24 * 60))) + 1)

        def _guard_days(tf: str, requested_days: int) -> Optional[str]:
            if tf == "1min" and requested_days > 10:
                return "timeframe=1min слишком тяжёлый: уменьшите days до 10"
            if tf == "5min" and requested_days > 45:
                return "timeframe=5min слишком тяжёлый: уменьшите days до 45"
            return None

        async def _fetch(tf: str, total_days: int) -> list:
            to_ts = datetime.now(timezone.utc)
            # padding for expired futures (best-effort)
            pad_days = 30 if tf in {"D1", "1h", "4h"} else 0
            from_ts_req = to_ts - timedelta(days=(total_days + pad_days))
            if tf == "D1":
                candles_all = await asyncio.wait_for(
                    repo.fetch_range(
                        figi=figi,
                        from_ts=from_ts_req,
                        to_ts=to_ts,
                        interval=CandleInterval.CANDLE_INTERVAL_DAY,
                    ),
                    timeout=120,
                )
            else:
                candles_all = await asyncio.wait_for(
                    repo.fetch_intraday_range(figi=figi, from_ts=from_ts_req, to_ts=to_ts, timeframe=tf),
                    timeout=180,
                )
            if not candles_all:
                return []
            # end at last candle
            end_ts = candles_all[-1].time
            cutoff = end_ts - timedelta(days=total_days)
            sliced = [c for c in candles_all if c.time >= cutoff]
            return sliced or candles_all

        def _candles_to_csv(candles: list) -> str:
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(["ts", "open", "high", "low", "close", "volume"])
            for c in candles:
                w.writerow([c.time.isoformat(), str(c.open), str(c.high), str(c.low), str(c.close), int(c.volume)])
            return buf.getvalue()

        def _candles_to_jsonl(candles: list) -> str:
            lines = []
            for c in candles:
                lines.append(
                    json.dumps(
                        {
                            "figi": c.figi,
                            "ts": c.time.isoformat(),
                            "open": str(c.open),
                            "high": str(c.high),
                            "low": str(c.low),
                            "close": str(c.close),
                            "volume": int(c.volume),
                        },
                        ensure_ascii=False,
                    )
                )
            return "\n".join(lines) + "\n"

        # Decide required series by strategy
        s = strategy.strip()
        series: list[tuple[str, str, int]] = []  # (label, tf, total_days)

        try:
            if s in {"donchian_atr"}:
                warm = max(_int(params.get("breakout_lookback", 20), 20), _int(params.get("exit_lookback", 10), 10), _int(params.get("atr_period", 14), 14)) + 5
                total = days + warm
                series.append(("candles_D1", "D1", total))
            elif s in {"ema_atr"}:
                warm = max(_int(params.get("ema_fast", 20), 20), _int(params.get("ema_slow", 50), 50), _int(params.get("atr_period", 14), 14)) + 5
                total = days + warm
                series.append(("candles_D1", "D1", total))
            elif s in {"trend_breakout_atr"}:
                tf = _tf_norm(params.get("timeframe", "1h"))
                if tf in {"1min", "5min"}:
                    return _json_response({"ok": False, "error": "Для trend_breakout_atr поддерживаются только timeframe 1h/4h."}, status=400)
                w_tf = max(
                    _int(params.get("breakout_lookback", 20), 20),
                    _int(params.get("exit_lookback", 10), 10),
                    _int(params.get("atr_period", 14), 14),
                    _int(params.get("volume_window", 20), 20),
                ) + 10
                total_tf = days + _warmup_days_from_bars(tf, w_tf)
                d1_w = max(_int(params.get("trend_ema_fast", 20), 20), _int(params.get("trend_ema_slow", 50), 50)) + 10
                total_d1 = days + d1_w
                series.append((f"candles_{tf}", tf, total_tf))
                series.append(("candles_D1", "D1", total_d1))
            elif s in {"intraday_vwap_momentum"}:
                tf = _tf_norm(params.get("timeframe", "1min"))
                if tf not in {"1min", "5min"}:
                    return _json_response({"ok": False, "error": "Для intraday_vwap_momentum timeframe должен быть 1min или 5min."}, status=400)
                guard = _guard_days(tf, days)
                if guard:
                    return _json_response({"ok": False, "error": guard}, status=400)
                # vwap_bars: use vwap_window (bars) if provided, else vwap_period(minutes)/bar_minutes
                vwap_window = params.get("vwap_window", None)
                if vwap_window is not None:
                    vwap_bars = max(1, _int(vwap_window, 1))
                else:
                    vwap_period_min = max(1, _int(params.get("vwap_period", 30), 30))
                    vwap_bars = int(math.ceil(vwap_period_min / max(1, (_bar_minutes(tf)))))
                w_tf = max(
                    vwap_bars,
                    _int(params.get("ema_fast", 5), 5),
                    _int(params.get("ema_slow", 20), 20),
                    _int(params.get("atr_period", 14), 14),
                    _int(params.get("volume_window", 20), 20),
                ) + 10
                total_tf = days + _warmup_days_from_bars(tf, w_tf)
                series.append((f"candles_{tf}", tf, total_tf))

                tr_tf_raw = params.get("trend_timeframe", None)
                if tr_tf_raw:
                    tr_tf = _tf_norm(tr_tf_raw)
                    if tr_tf not in {"1h", "4h"}:
                        return _json_response({"ok": False, "error": "trend_timeframe должен быть 1h/4h или null."}, status=400)
                    w_tr = max(_int(params.get("trend_ema_fast", 20), 20), _int(params.get("trend_ema_slow", 50), 50)) + 10
                    total_tr = days + _warmup_days_from_bars(tr_tf, w_tr)
                    series.append((f"candles_trend_{tr_tf}", tr_tf, total_tr))
            elif s in {"intraday_bollinger_rsi"}:
                tf = _tf_norm(params.get("timeframe", "5min"))
                if tf not in {"1min", "5min"}:
                    return _json_response({"ok": False, "error": "Для intraday_bollinger_rsi timeframe должен быть 1min или 5min."}, status=400)
                guard = _guard_days(tf, days)
                if guard:
                    return _json_response({"ok": False, "error": guard}, status=400)
                w_tf = max(
                    _int(params.get("bollinger_period", 20), 20),
                    _int(params.get("rsi_period", 14), 14),
                    _int(params.get("atr_period", 14), 14),
                    _int(params.get("volume_window", 30), 30),
                ) + 10
                total_tf = days + _warmup_days_from_bars(tf, w_tf)
                series.append((f"candles_{tf}", tf, total_tf))
            else:
                return _json_response({"ok": False, "error": f"Неизвестная стратегия: {s}"}, status=400)
        except ValueError as e:
            return _json_response({"ok": False, "error": str(e)}, status=400)

        await broker_client.ainit()
        repo = CandleRepository(broker=broker_client)

        # If a single series is required, return it directly (more convenient).
        ts_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        ext = "jsonl" if fmt in {"jsonl", "ndjson"} else "csv"
        if len(series) == 1:
            label, tf, total = series[0]
            try:
                data = await _fetch(tf, int(total))
            except asyncio.TimeoutError:
                return _json_response({"ok": False, "error": "Таймаут при загрузке свечей. Попробуйте уменьшить days."}, status=504)
            if not data:
                return _json_response(
                    {
                        "ok": False,
                        "error": "Свечи не найдены. Проверьте FIGI и доступность истории.",
                        "debug": {"figi": figi, "timeframe": tf, "total_days": int(total)},
                    },
                    status=404,
                )
            content = _candles_to_jsonl(data) if fmt in {"jsonl", "ndjson"} else _candles_to_csv(data)
            filename = f"{label}_{s}_{figi}_{days}d_{ts_stamp}.{ext}"
            headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
            if fmt in {"jsonl", "ndjson"}:
                return web.Response(text=content, content_type="application/x-ndjson", headers=headers)
            return web.Response(text=content, content_type="text/csv", headers=headers)

        # Fetch and build zip (multi-series strategies)
        zip_name = f"candles_{s}_{figi}_{days}d_{ts_stamp}.zip"

        meta = {
            "figi": figi,
            "strategy": s,
            "days": days,
            "format": ("jsonl" if fmt in {"jsonl", "ndjson"} else "csv"),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "params": params,
            "files": [],
        }

        zbuf = io.BytesIO()
        try:
            with zipfile.ZipFile(zbuf, mode="w", compression=zipfile.ZIP_DEFLATED) as z:
                for label, tf, total in series:
                    data = await _fetch(tf, int(total))
                    if not data:
                        return _json_response(
                            {
                                "ok": False,
                                "error": "Свечи не найдены. Проверьте FIGI и доступность истории.",
                                "debug": {"figi": figi, "timeframe": tf, "total_days": int(total)},
                            },
                            status=404,
                        )
                    content = _candles_to_jsonl(data) if fmt in {"jsonl", "ndjson"} else _candles_to_csv(data)
                    fname = f"{label}.{ext}"
                    z.writestr(fname, content.encode("utf-8"))
                    meta["files"].append(
                        {
                            "name": fname,
                            "timeframe": tf,
                            "total_days": int(total),
                            "candles": len(data),
                            "first_ts": data[0].time.isoformat() if data else None,
                            "last_ts": data[-1].time.isoformat() if data else None,
                        }
                    )
                z.writestr("meta.json", json.dumps(meta, ensure_ascii=False, indent=2).encode("utf-8"))
        except asyncio.TimeoutError:
            return _json_response({"ok": False, "error": "Таймаут при загрузке свечей. Попробуйте уменьшить days."}, status=504)

        headers = {"Content-Disposition": f'attachment; filename="{zip_name}"'}
        return web.Response(body=zbuf.getvalue(), content_type="application/zip", headers=headers)

    async def handle_backtest_run(self, request: web.Request) -> web.Response:
        """
        Dry-run (backtest) on historical data, no real trading.
        body: { job_id, initial_equity, days (default 90) }
        """
        body = await request.json()
        jid = str(body.get("job_id") or "")
        days = int(body.get("days") or 90)
        initial_equity = str(body.get("initial_equity") or "1000000")
        if not jid:
            return _json_response({"ok": False, "error": "job_id обязателен"}, status=400)
        if days <= 0 or days > 365:
            return _json_response({"ok": False, "error": "days должно быть в диапазоне 1..365"}, status=400)
        if not broker_client.credentials_set():
            return _json_response({"ok": False, "error": "Сначала установите токен"}, status=400)
        if jid not in self._job_defs:
            return _json_response({"ok": False, "error": "Неизвестная джоба"}, status=404)

        meta = self._job_defs[jid]
        figi = meta["figi"]
        strat: StrategyName = meta["strategy"]
        params = dict(meta.get("params") or {})

        from datetime import timedelta
        from decimal import Decimal

        import asyncio

        from core.backtest.engine import BacktestConfig, run_backtest_target_qty
        from core.backtest.futures import futures_spec_from_instrument
        from core.backtest.trend_breakout_atr import TrendBreakoutParams, run_backtest_trend_breakout_atr
        from core.data.candles import CandleRepository
        from reports.stats import summarize
        from t_tech.invest import CandleInterval

        # Ensure broker client is initialized (mode does not matter for history)
        await broker_client.ainit()
        repo = CandleRepository(broker=broker_client)

        # Fetch instrument spec (best-effort) to make futures PnL/size realistic.
        inst = None
        try:
            try:
                from t_tech.invest.grpc.instruments_pb2 import INSTRUMENT_ID_TYPE_FIGI

                id_type = INSTRUMENT_ID_TYPE_FIGI
            except Exception:  # noqa: BLE001
                id_type = 1
            resp = await broker_client.get_instrument(id_type=id_type, id=figi)
            inst = getattr(resp, "instrument", None)
        except Exception:  # noqa: BLE001
            inst = None
        f_spec = futures_spec_from_instrument(inst)

        to_ts = datetime.now(timezone.utc)
        from_ts = to_ts - timedelta(days=days + 30)  # padding for indicators
        logger.info(
            "backtest_run jid=%s figi=%s strat=%s days=%s from_ts=%s to_ts=%s params=%s",
            jid,
            figi,
            strat.value,
            days,
            from_ts.isoformat(),
            to_ts.isoformat(),
            params,
        )

        bt_cfg = BacktestConfig(
            initial_equity=Decimal(initial_equity),
            price_slippage_bps=Decimal(str(instruments_config.global_execution.price_slippage_bps)),
            commission_bps=Decimal(str(instruments_config.global_execution.commission_bps)),
            futures_price_multiplier=f_spec.price_multiplier,
        )

        if strat.value in {"donchian_atr", "ema_atr"}:
            # D1 candles only
            candles_all = await repo.fetch_range(
                figi=figi,
                from_ts=from_ts,
                to_ts=to_ts,
                interval=CandleInterval.CANDLE_INTERVAL_DAY,
            )
            if not candles_all:
                logger.warning(
                    "backtest_run no candles returned figi=%s interval=D1 from_ts=%s to_ts=%s",
                    figi,
                    from_ts.isoformat(),
                    to_ts.isoformat(),
                )
                return _json_response(
                    {
                        "ok": False,
                        "error": "Не удалось загрузить свечи (D1). Проверь FIGI/токен или что по инструменту есть история.",
                        "debug": {"interval": "D1", "from_ts": from_ts.isoformat(), "to_ts": to_ts.isoformat()},
                    },
                    status=500,
                )

            # Use last available candle as end of period (important for expired futures).
            end_ts = candles_all[-1].time
            cutoff = end_ts - timedelta(days=days)
            candles = [c for c in candles_all if c.time >= cutoff]
            if not candles:
                candles = candles_all[-min(len(candles_all), days) :]
            logger.info(
                "backtest_run candles_loaded interval=D1 total=%s used=%s first=%s last=%s",
                len(candles_all),
                len(candles),
                candles[0].time.isoformat() if candles else None,
                candles[-1].time.isoformat() if candles else None,
            )

            if strat.value == "donchian_atr":
                from app.strategies.positional.donchian_atr import DonchianATRStrategy, DonchianAtrConfig

                st = DonchianATRStrategy(figi=figi, config=DonchianAtrConfig(**params))
                sig_fn = lambda w, pos: st.generate_signal(candles=w, current_position_qty=pos, strategy_name=strat.value)
            else:
                from app.strategies.positional.ema_atr import EmaAtrTrendStrategy, EmaAtrConfig

                st = EmaAtrTrendStrategy(figi=figi, config=EmaAtrConfig(**params))
                sig_fn = lambda w, pos: st.generate_signal(candles=w, current_position_qty=pos, in_cooldown=False, strategy_name=strat.value)

            res = run_backtest_target_qty(figi=figi, strategy_name=strat.value, candles=candles, signal_fn=sig_fn, cfg=bt_cfg)
            summ = summarize(res.trades, res.equity)
            return _json_response(
                {
                    "ok": True,
                    "strategy": strat.value,
                    "figi": figi,
                    "days": days,
                    "initial_equity": str(bt_cfg.initial_equity),
                    "final_equity": str(res.equity[-1].equity if res.equity else bt_cfg.initial_equity),
                    "price_series": [{"ts": c.time.isoformat(), "close": str(c.close)} for c in candles[-1500:]],
                    "instrument_spec": {
                        "price_multiplier": str(f_spec.price_multiplier),
                        "currency": f_spec.currency,
                        "lot": f_spec.lot,
                        "min_price_increment": None if f_spec.min_price_increment is None else str(f_spec.min_price_increment),
                        "min_price_increment_amount": None
                        if f_spec.min_price_increment_amount is None
                        else str(f_spec.min_price_increment_amount),
                        "source": f_spec.source,
                    },
                    "summary": {
                        "trades": summ.trades,
                        "winrate": summ.winrate,
                        "total_pnl": str(summ.total_pnl),
                        "max_drawdown": str(summ.max_drawdown),
                    },
                    "trades": [t.__dict__ for t in res.trades[-200:]],
                    "equity": [p.__dict__ for p in res.equity[-500:]],
                }
            )

        if strat.value == "trend_breakout_atr":
            tf = str(params.get("timeframe") or "1h")
            # Intraday + D1
            # Guardrails: big intraday ranges can take a long time.
            if tf.lower().strip() in {"1min", "5min"}:
                return _json_response(
                    {
                        "ok": False,
                        "error": "Для trend_breakout_atr поддерживаются только timeframe 1h/4h (а не 1min/5min).",
                    },
                    status=400,
                )
            candles_tf_all = await asyncio.wait_for(
                repo.fetch_intraday_range(figi=figi, from_ts=from_ts, to_ts=to_ts, timeframe=tf),
                timeout=90,
            )
            if not candles_tf_all:
                logger.warning(
                    "backtest_run no candles returned figi=%s interval=%s from_ts=%s to_ts=%s",
                    figi,
                    tf,
                    from_ts.isoformat(),
                    to_ts.isoformat(),
                )
                return _json_response(
                    {
                        "ok": False,
                        "error": "Не удалось загрузить свечи (интрадей). Возможно, диапазон слишком большой или по инструменту нет истории.",
                        "debug": {"interval": tf, "from_ts": from_ts.isoformat(), "to_ts": to_ts.isoformat()},
                    },
                    status=500,
                )
            end_tf = candles_tf_all[-1].time
            cutoff_tf = end_tf - timedelta(days=days)
            candles_tf = [c for c in candles_tf_all if c.time >= cutoff_tf]
            if not candles_tf:
                candles_tf = candles_tf_all[-min(len(candles_tf_all), 24 * days) :]

            # D1 padding for EMA fast/slow
            ema_slow = int(params.get("trend_ema_slow", 50))
            d1_from = to_ts - timedelta(days=days + ema_slow + 90)
            candles_d1 = await asyncio.wait_for(
                repo.fetch_range(figi=figi, from_ts=d1_from, to_ts=to_ts, interval=CandleInterval.CANDLE_INTERVAL_DAY),
                timeout=60,
            )
            if not candles_d1:
                logger.warning(
                    "backtest_run no candles returned figi=%s interval=D1 from_ts=%s to_ts=%s",
                    figi,
                    d1_from.isoformat(),
                    to_ts.isoformat(),
                )
                return _json_response(
                    {
                        "ok": False,
                        "error": "Не удалось загрузить свечи (D1 для тренда). Проверь FIGI/токен или историю инструмента.",
                        "debug": {"interval": "D1", "from_ts": d1_from.isoformat(), "to_ts": to_ts.isoformat()},
                    },
                    status=500,
                )
            logger.info(
                "backtest_run candles_loaded interval=%s tf_total=%s tf_used=%s d1=%s tf_first=%s tf_last=%s",
                tf,
                len(candles_tf_all),
                len(candles_tf),
                len(candles_d1),
                candles_tf[0].time.isoformat() if candles_tf else None,
                candles_tf[-1].time.isoformat() if candles_tf else None,
            )

            p = TrendBreakoutParams(
                timeframe=tf,
                breakout_lookback=int(params.get("breakout_lookback", 20)),
                exit_lookback=int(params.get("exit_lookback", 10)),
                trend_ema_fast=int(params.get("trend_ema_fast", 20)),
                trend_ema_slow=int(params.get("trend_ema_slow", 50)),
                atr_period=int(params.get("atr_period", 14)),
                atr_stop_mult=Decimal(str(params.get("atr_stop_mult", "2"))),
                atr_tp_mult=Decimal(str(params.get("atr_tp_mult", "3"))),
                atr_trail_mult=Decimal(str(params.get("atr_trail_mult", "1.5"))),
                risk_per_trade_pct=Decimal(str(params.get("risk_per_trade_pct", "0.5"))),
                volume_window=int(params.get("volume_window", 20)),
                min_volume_ratio=Decimal(str(params.get("min_volume_ratio", "1.0"))),
                trade_sessions=tuple(tuple(x) for x in (params.get("trade_sessions") or (("10:00","18:45"),))),
                exit_before_close_minutes=int(params.get("exit_before_close_minutes", 0)),
            )
            # Limits: for UI jobs use stored meta values; for config jobs fall back to instrument_config.
            inst_cfg = meta.get("instrument_config")
            max_pos = int(meta.get("max_position_qty") or getattr(inst_cfg, "max_position_qty", None) or 10)
            max_order = int(meta.get("max_order_qty") or getattr(inst_cfg, "max_order_qty", None) or max_pos)
            trades, equity = run_backtest_trend_breakout_atr(
                figi=figi,
                candles_tf=candles_tf,
                candles_d1=candles_d1,
                params=p,
                cfg=bt_cfg,
                max_position_qty=max_pos,
                max_order_qty=max_order,
            )
            summ = summarize(trades, equity)
            return _json_response(
                {
                    "ok": True,
                    "strategy": strat.value,
                    "figi": figi,
                    "days": days,
                    "initial_equity": str(bt_cfg.initial_equity),
                    "final_equity": str(equity[-1].equity if equity else bt_cfg.initial_equity),
                    "price_series": [{"ts": c.time.isoformat(), "close": str(c.close)} for c in candles_tf[-2000:]],
                    "instrument_spec": {
                        "price_multiplier": str(f_spec.price_multiplier),
                        "currency": f_spec.currency,
                        "lot": f_spec.lot,
                        "min_price_increment": None if f_spec.min_price_increment is None else str(f_spec.min_price_increment),
                        "min_price_increment_amount": None
                        if f_spec.min_price_increment_amount is None
                        else str(f_spec.min_price_increment_amount),
                        "source": f_spec.source,
                        "max_position_qty": max_pos,
                        "max_order_qty": max_order,
                    },
                    "summary": {
                        "trades": summ.trades,
                        "winrate": summ.winrate,
                        "total_pnl": str(summ.total_pnl),
                        "max_drawdown": str(summ.max_drawdown),
                    },
                    "trades": [t.__dict__ for t in trades[-200:]],
                    "equity": [p.__dict__ for p in equity[-2000:]],
                }
            )

        if strat.value == "intraday_vwap_momentum":
            # Intraday only (1min/5min). Note: large day ranges may be heavy; keep user-provided days but
            # rely on broker/SDK to enforce limits. UI can retry with fewer days if needed.
            tf = str(params.get("timeframe") or "1min")
            tf_norm = tf.lower().strip()
            # Hard guardrails to prevent "hang" on huge 1-minute ranges.
            if tf_norm == "1min" and days > 10:
                return _json_response(
                    {
                        "ok": False,
                        "error": "timeframe=1min слишком тяжёлый для long-run. Уменьшите days до 10 (или выберите 5min).",
                    },
                    status=400,
                )
            if tf_norm == "5min" and days > 45:
                return _json_response(
                    {
                        "ok": False,
                        "error": "timeframe=5min слишком тяжёлый для long-run. Уменьшите days до 45.",
                    },
                    status=400,
                )
            candles_tf_all = await asyncio.wait_for(
                repo.fetch_intraday_range(figi=figi, from_ts=from_ts, to_ts=to_ts, timeframe=tf),
                timeout=120,
            )
            if not candles_tf_all:
                logger.warning(
                    "backtest_run no candles returned figi=%s interval=%s from_ts=%s to_ts=%s",
                    figi,
                    tf,
                    from_ts.isoformat(),
                    to_ts.isoformat(),
                )
                return _json_response(
                    {
                        "ok": False,
                        "error": "Не удалось загрузить свечи (интрадей). Попробуйте уменьшить days или переключить timeframe на 5min.",
                        "debug": {"interval": tf, "from_ts": from_ts.isoformat(), "to_ts": to_ts.isoformat()},
                    },
                    status=500,
                )
            end_tf = candles_tf_all[-1].time
            cutoff_tf = end_tf - timedelta(days=days)
            candles = [c for c in candles_tf_all if c.time >= cutoff_tf]
            if not candles:
                candles = candles_tf_all[-min(len(candles_tf_all), 2000) :]

            from app.strategies.intraday.vwap_momentum import VwapMomentumConfig, VwapMomentumStrategy, _to_decimal

            p = dict(params or {})
            cfg0 = VwapMomentumConfig()
            vm_cfg = VwapMomentumConfig(
                timeframe=str(p.get("timeframe", cfg0.timeframe)),
                vwap_period=int(p.get("vwap_period", cfg0.vwap_period)),
                vwap_window=(int(p["vwap_window"]) if p.get("vwap_window") is not None else cfg0.vwap_window),
                ema_fast=int(p.get("ema_fast", cfg0.ema_fast)),
                ema_slow=int(p.get("ema_slow", cfg0.ema_slow)),
                trend_timeframe=(str(p["trend_timeframe"]) if p.get("trend_timeframe") is not None else cfg0.trend_timeframe),
                trend_ema_fast=int(p.get("trend_ema_fast", cfg0.trend_ema_fast)),
                trend_ema_slow=int(p.get("trend_ema_slow", cfg0.trend_ema_slow)),
                atr_period=int(p.get("atr_period", cfg0.atr_period)),
                sl_points=_to_decimal(p.get("sl_points", cfg0.sl_points)),
                tp_points=_to_decimal(p.get("tp_points", cfg0.tp_points)),
                atr_sl_mult=_to_decimal(p.get("atr_sl_mult", cfg0.atr_sl_mult)),
                atr_tp_mult=_to_decimal(p.get("atr_tp_mult", cfg0.atr_tp_mult)),
                atr_trail_mult=_to_decimal(p.get("atr_trail_mult", cfg0.atr_trail_mult)),
                risk_per_trade_pct=_to_decimal(p.get("risk_per_trade_pct", cfg0.risk_per_trade_pct))
                or cfg0.risk_per_trade_pct,
                volume_window=int(p.get("volume_window", cfg0.volume_window)),
                min_volume_ratio=_to_decimal(p.get("min_volume_ratio", cfg0.min_volume_ratio)) or cfg0.min_volume_ratio,
                trade_sessions=tuple(tuple(x) for x in (p.get("trade_sessions") or cfg0.trade_sessions)),
                cooldown_bars=int(p.get("cooldown_bars", cfg0.cooldown_bars)),
                exit_before_session_end_minutes=int(
                    p.get("exit_before_session_end_minutes", cfg0.exit_before_session_end_minutes)
                ),
            )
            st = VwapMomentumStrategy(figi=figi, config=vm_cfg)

            # Higher timeframe trend filter (optional): compute trend_dir for each bar timestamp.
            trend_ts: list[datetime] = []
            trend_close: list[Decimal] = []
            trend_ef: list[Decimal] = []
            trend_es: list[Decimal] = []
            trend_j = 0  # pointer used in sig_fn (monotonic in backtest loop)

            tf_trend = str(vm_cfg.trend_timeframe or "").lower().strip()
            if tf_trend in {"1h", "4h"}:
                # Padding for EMA calculation
                hours_per_bar = 1 if tf_trend == "1h" else 4
                pad_hours = max(48, (int(vm_cfg.trend_ema_slow) + 10) * hours_per_bar)
                trend_from = from_ts - timedelta(hours=pad_hours)
                try:
                    candles_trend_all = await asyncio.wait_for(
                        repo.fetch_intraday_range(figi=figi, from_ts=trend_from, to_ts=to_ts, timeframe=tf_trend),
                        timeout=60,
                    )
                except Exception:  # noqa: BLE001
                    candles_trend_all = []

                if candles_trend_all:
                    trend_ts = [c.time for c in candles_trend_all]
                    trend_close = [c.close for c in candles_trend_all]
                    try:
                        from app.strategies.positional.indicators import ema

                        trend_ef = ema(trend_close, int(vm_cfg.trend_ema_fast))
                        trend_es = ema(trend_close, int(vm_cfg.trend_ema_slow))
                    except Exception:  # noqa: BLE001
                        trend_ef = []
                        trend_es = []

            def _trend_dir_for_ts(ts: datetime) -> int:
                nonlocal trend_j
                if not trend_ts or not trend_close or not trend_ef or not trend_es:
                    return 0
                # Advance pointer while we have trend bars <= ts
                while (trend_j + 1) < len(trend_ts) and trend_ts[trend_j + 1] <= ts:
                    trend_j += 1
                if trend_j < 0 or trend_j >= len(trend_ts):
                    return 0
                if trend_j >= len(trend_close) or trend_j >= len(trend_ef) or trend_j >= len(trend_es):
                    return 0
                c = trend_close[trend_j]
                ef = trend_ef[trend_j]
                es = trend_es[trend_j]
                if c > es and ef > es:
                    return 1
                if c < es and ef < es:
                    return -1
                return 0

            # Risk-based sizing for dry-run:
            # Strategy emits target_qty = +/-1 as direction; here we scale it to contracts using:
            # qty = floor((risk_per_trade_pct * initial_equity) / stop_risk_per_contract_rub)
            # stop_risk_per_contract_rub is derived from:
            # - sl_points * tick_size (if available) * price_multiplier * lot
            # - OR ATR * atr_sl_mult * price_multiplier * lot
            def _norm_risk_pct(v: Decimal) -> Decimal:
                return v / Decimal("100") if v >= Decimal("0.1") else v

            # Limits: for UI jobs use stored meta values; for config jobs fall back to instrument_config.
            inst_cfg = meta.get("instrument_config")
            max_pos = int(meta.get("max_position_qty") or getattr(inst_cfg, "max_position_qty", None) or 10)

            risk_frac = _norm_risk_pct(_to_decimal(p.get("risk_per_trade_pct", vm_cfg.risk_per_trade_pct)) or vm_cfg.risk_per_trade_pct)
            risk_budget = bt_cfg.initial_equity * risk_frac
            tick_size = f_spec.min_price_increment  # Decimal or None
            pm = f_spec.price_multiplier
            lot = Decimal(int(f_spec.lot or 1))
            last_sizing: dict[str, str] = {}

            def _stop_risk_per_contract_rub(window) -> Optional[Decimal]:
                # Fixed stop in points (interpreted as ticks if tick_size is known)
                if vm_cfg.sl_points is not None and vm_cfg.sl_points > 0:
                    dist = (vm_cfg.sl_points * tick_size) if (tick_size is not None and tick_size > 0) else vm_cfg.sl_points
                    return dist * pm * lot
                # ATR-based stop
                if (
                    vm_cfg.atr_sl_mult is not None
                    and vm_cfg.atr_sl_mult > 0
                    and len(window) >= int(vm_cfg.atr_period) + 2
                ):
                    from app.strategies.positional.indicators import atr

                    a = atr(window, int(vm_cfg.atr_period))
                    if a is None or a <= 0:
                        return None
                    dist = a * vm_cfg.atr_sl_mult
                    return dist * pm * lot
                return None

            # Close-based SL/TP/trailing simulation (same idea as runner):
            entry_price: Optional[Decimal] = None
            stop_price: Optional[Decimal] = None
            tp_price: Optional[Decimal] = None
            peak_price: Optional[Decimal] = None
            trough_price: Optional[Decimal] = None

            def _set_levels(*, entry: Decimal, atr_value: Optional[Decimal], direction: int) -> None:
                nonlocal entry_price, stop_price, tp_price, peak_price, trough_price
                if direction == 0:
                    return
                stop_dist = None
                tp_dist = None

                # stop distance in price units
                if vm_cfg.sl_points is not None and vm_cfg.sl_points > 0:
                    stop_dist = (vm_cfg.sl_points * tick_size) if (tick_size is not None and tick_size > 0) else vm_cfg.sl_points
                elif vm_cfg.atr_sl_mult is not None and vm_cfg.atr_sl_mult > 0 and atr_value is not None and atr_value > 0:
                    stop_dist = atr_value * vm_cfg.atr_sl_mult

                # tp distance in price units
                if vm_cfg.tp_points is not None and vm_cfg.tp_points > 0:
                    tp_dist = (vm_cfg.tp_points * tick_size) if (tick_size is not None and tick_size > 0) else vm_cfg.tp_points
                elif vm_cfg.atr_tp_mult is not None and vm_cfg.atr_tp_mult > 0 and atr_value is not None and atr_value > 0:
                    tp_dist = atr_value * vm_cfg.atr_tp_mult

                if stop_dist is None or tp_dist is None:
                    return

                entry_price = entry
                if direction > 0:
                    stop_price = entry - stop_dist
                    tp_price = entry + tp_dist
                    peak_price = entry
                    trough_price = None
                else:
                    stop_price = entry + stop_dist
                    tp_price = entry - tp_dist
                    trough_price = entry
                    peak_price = None

            def _clear_levels() -> None:
                nonlocal entry_price, stop_price, tp_price, peak_price, trough_price
                entry_price = None
                stop_price = None
                tp_price = None
                peak_price = None
                trough_price = None

            def _update_trailing(*, direction: int, close: Decimal, atr_value: Optional[Decimal]) -> None:
                nonlocal stop_price, peak_price, trough_price
                if direction == 0:
                    return
                if vm_cfg.atr_trail_mult is None or vm_cfg.atr_trail_mult <= 0:
                    return
                if atr_value is None or atr_value <= 0:
                    return
                dist = atr_value * vm_cfg.atr_trail_mult
                if direction > 0:
                    peak_price = close if peak_price is None else max(peak_price, close)
                    trail = peak_price - dist
                    if stop_price is None or trail > stop_price:
                        stop_price = trail
                else:
                    trough_price = close if trough_price is None else min(trough_price, close)
                    trail = trough_price + dist
                    if stop_price is None or trail < stop_price:
                        stop_price = trail

            def sig_fn(w, pos):
                # 0) If in position, update trailing + exit by SL/TP (close-based)
                if w:
                    last = w[-1]
                else:
                    return None

                if pos != 0:
                    direction = 1 if pos > 0 else -1
                    # ATR for trailing (same period as strategy)
                    try:
                        from app.strategies.positional.indicators import atr

                        a_now = atr(w, int(vm_cfg.atr_period))
                    except Exception:  # noqa: BLE001
                        a_now = None

                    try:
                        _update_trailing(direction=direction, close=last.close, atr_value=a_now)
                    except Exception:  # noqa: BLE001
                        pass

                    # SL/TP check
                    if stop_price is not None and tp_price is not None:
                        if direction > 0:
                            if last.close <= stop_price or last.close >= tp_price:
                                _clear_levels()
                                from core.models.entities import Signal as CoreSignal, SignalType as CoreSignalType

                                return CoreSignal(
                                    strategy_name=strat.value,
                                    figi=last.figi,
                                    ts=last.time,
                                    signal_type=CoreSignalType.TARGET_QTY,
                                    target_qty=0,
                                    reason="exit: SL/TP hit (close-based)",
                                )
                        else:
                            if last.close >= stop_price or last.close <= tp_price:
                                _clear_levels()
                                from core.models.entities import Signal as CoreSignal, SignalType as CoreSignalType

                                return CoreSignal(
                                    strategy_name=strat.value,
                                    figi=last.figi,
                                    ts=last.time,
                                    signal_type=CoreSignalType.TARGET_QTY,
                                    target_qty=0,
                                    reason="exit: SL/TP hit (close-based)",
                                )

                td = _trend_dir_for_ts(w[-1].time) if w else 0
                sig = st.generate_signal(
                    candles=w,
                    current_position_qty=pos,
                    trend_direction=td,
                    strategy_name=strat.value,
                )
                if sig is None:
                    return None

                # Only size entries; exits keep 0
                if int(getattr(sig, "target_qty", 0)) == 0:
                    _clear_levels()

                if pos == 0 and sig.target_qty != 0 and abs(int(sig.target_qty)) == 1:
                    rs = _stop_risk_per_contract_rub(w)
                    if rs is not None and rs > 0 and risk_budget > 0:
                        qty_raw = int((risk_budget / rs).to_integral_value(rounding="ROUND_FLOOR"))
                        qty_raw = max(1, qty_raw)
                        qty = min(qty_raw, max_pos) if max_pos > 0 else qty_raw
                        last_sizing.clear()
                        last_sizing.update(
                            {
                                "risk_pct": str(risk_frac),
                                "risk_budget": str(risk_budget),
                                "stop_risk_per_contract_rub": str(rs),
                                "qty_raw": str(qty_raw),
                                "qty_capped": str(qty),
                                "max_position_qty": str(max_pos),
                                "sl_points": str(vm_cfg.sl_points) if vm_cfg.sl_points is not None else "",
                                "atr_sl_mult": str(vm_cfg.atr_sl_mult) if vm_cfg.atr_sl_mult is not None else "",
                                "tick_size": str(tick_size) if tick_size is not None else "",
                                "price_multiplier": str(pm),
                                "lot": str(lot),
                            }
                        )
                        # Set SL/TP levels at entry (using ATR from strategy if available)
                        try:
                            _set_levels(entry=last.close, atr_value=getattr(sig, "atr", None), direction=(1 if sig.target_qty > 0 else -1))
                        except Exception:  # noqa: BLE001
                            pass
                        from core.models.entities import Signal as CoreSignal

                        return CoreSignal(
                            strategy_name=sig.strategy_name,
                            figi=sig.figi,
                            ts=sig.ts,
                            signal_type=sig.signal_type,
                            target_qty=(qty if sig.target_qty > 0 else -qty),
                            reason=f"{sig.reason} (sized qty={qty})",
                            atr=sig.atr,
                        )
                return sig

            # Quick diagnostics: how many raw entry signals exist if we ignore position state (pos=0 always)
            raw_entries = 0
            diag = {
                "bars_total": len(candles),
                "bars_warmup_ok": 0,
                "bars_in_session": 0,
                "bars_volume_ok": 0,
                "bars_trend_bull": 0,
                "bars_trend_bear": 0,
                "vwap_cross_up": 0,
                "vwap_cross_down": 0,
                "bias_long": 0,
                "bias_short": 0,
                "fast_up": 0,
                "fast_down": 0,
                "entries_long": 0,
                "entries_short": 0,
            }
            try:
                from zoneinfo import ZoneInfo

                from app.strategies.indicators import average_volume, vwap_close
                from app.strategies.positional.indicators import ema

                msk = ZoneInfo("Europe/Moscow")

                def _in_sessions(ts: datetime) -> bool:
                    try:
                        t = ts.astimezone(msk).time()
                    except Exception:
                        t = ts.time()
                    for a, b in vm_cfg.trade_sessions:
                        try:
                            sh, sm = a.split(":")
                            eh, em = b.split(":")
                            start_t = datetime(2000, 1, 1, int(sh), int(sm)).time()
                            end_t = datetime(2000, 1, 1, int(eh), int(em)).time()
                        except Exception:
                            continue
                        if start_t <= t <= end_t:
                            return True
                    return False

                vwap_bars = int(vm_cfg.vwap_window) if vm_cfg.vwap_window is not None else None
                if vwap_bars is None:
                    # derive from minutes period (legacy)
                    bm = 1 if tf_norm == "1min" else 5
                    vwap_bars = max(1, int((int(vm_cfg.vwap_period) + bm - 1) // bm))

                closes_all = [c.close for c in candles]
                ef_all = ema(closes_all, int(vm_cfg.ema_fast))
                es_all = ema(closes_all, int(vm_cfg.ema_slow))
                vwap_all = vwap_close(candles, int(vwap_bars))

                for i in range(len(candles)):
                    w = candles[: i + 1]
                    td = _trend_dir_for_ts(w[-1].time) if w else 0
                    if td == 1:
                        diag["bars_trend_bull"] += 1
                    elif td == -1:
                        diag["bars_trend_bear"] += 1

                    # Warmup check (same as strategy needs + cross)
                    if i < 2:
                        continue
                    vwap_now = vwap_all[i] if i < len(vwap_all) else None
                    vwap_prev = vwap_all[i - 1] if (i - 1) < len(vwap_all) else None
                    if vwap_now is None or vwap_prev is None:
                        continue
                    if i >= len(ef_all) or i >= len(es_all) or i < 1:
                        continue
                    diag["bars_warmup_ok"] += 1

                    c = candles[i]
                    pprev = candles[i - 1]

                    in_sess = _in_sessions(c.time)
                    if in_sess:
                        diag["bars_in_session"] += 1

                    # volume filter
                    vol_ok = True
                    if vm_cfg.min_volume_ratio is not None and vm_cfg.min_volume_ratio > 0:
                        avg_v = average_volume(w, int(vm_cfg.volume_window), include_last=False)
                        vol_ok = bool(avg_v is not None and avg_v > 0 and (Decimal(int(c.volume)) / avg_v) >= Decimal(str(vm_cfg.min_volume_ratio)))
                    if vol_ok:
                        diag["bars_volume_ok"] += 1

                    fast_now, fast_prev = ef_all[i], ef_all[i - 1]
                    slow_now = es_all[i]
                    if fast_now > fast_prev:
                        diag["fast_up"] += 1
                    if fast_now < fast_prev:
                        diag["fast_down"] += 1

                    cross_up = (pprev.close < vwap_prev) and (c.close > vwap_now)
                    cross_down = (pprev.close > vwap_prev) and (c.close < vwap_now)
                    if cross_up:
                        diag["vwap_cross_up"] += 1
                    if cross_down:
                        diag["vwap_cross_down"] += 1

                    long_bias = (c.close > vwap_now) and (fast_now > slow_now)
                    short_bias = (c.close < vwap_now) and (fast_now < slow_now)
                    if long_bias:
                        diag["bias_long"] += 1
                    if short_bias:
                        diag["bias_short"] += 1

                    s0 = st.generate_signal(
                        candles=w,
                        current_position_qty=0,
                        trend_direction=td,
                        strategy_name=strat.value,
                    )
                    if s0 is not None and int(getattr(s0, "target_qty", 0)) != 0:
                        raw_entries += 1
                        if int(getattr(s0, "target_qty", 0)) > 0:
                            diag["entries_long"] += 1
                        else:
                            diag["entries_short"] += 1
            except Exception:  # noqa: BLE001
                raw_entries = -1
                diag = {"error": "diag_failed"}

            res = run_backtest_target_qty(figi=figi, strategy_name=strat.value, candles=candles, signal_fn=sig_fn, cfg=bt_cfg)
            summ = summarize(res.trades, res.equity)
            return _json_response(
                {
                    "ok": True,
                    "strategy": strat.value,
                    "figi": figi,
                    "days": days,
                    "initial_equity": str(bt_cfg.initial_equity),
                    "final_equity": str(res.equity[-1].equity if res.equity else bt_cfg.initial_equity),
                    "price_series": [{"ts": c.time.isoformat(), "close": str(c.close)} for c in candles[-2000:]],
                    "instrument_spec": {
                        "price_multiplier": str(f_spec.price_multiplier),
                        "currency": f_spec.currency,
                        "lot": f_spec.lot,
                        "min_price_increment": None if f_spec.min_price_increment is None else str(f_spec.min_price_increment),
                        "min_price_increment_amount": None
                        if f_spec.min_price_increment_amount is None
                        else str(f_spec.min_price_increment_amount),
                        "source": f_spec.source,
                        "max_position_qty": max_pos,
                    },
                    "summary": {
                        "trades": summ.trades,
                        "winrate": summ.winrate,
                        "total_pnl": str(summ.total_pnl),
                        "max_drawdown": str(summ.max_drawdown),
                    },
                    "debug": {
                        "timeframe": tf_norm,
                        "trend_timeframe": tf_trend if tf_trend in {"1h", "4h"} else None,
                        "trend_candles": len(trend_ts) if trend_ts else 0,
                        "raw_entry_signals_pos0": raw_entries,
                        "filters": diag,
                        "last_sizing": last_sizing,
                    },
                    "trades": [t.__dict__ for t in res.trades[-200:]],
                    "equity": [p.__dict__ for p in res.equity[-2000:]],
                }
            )

        if strat.value == "intraday_bollinger_rsi":
            # Intraday only (1min/5min). Note: large day ranges may be heavy; keep user-provided days but
            # rely on broker/SDK to enforce limits. UI can retry with fewer days if needed.
            tf = str(params.get("timeframe") or "5min")
            tf_norm = tf.lower().strip()
            # Hard guardrails to prevent "hang" on huge 1-minute ranges.
            if tf_norm == "1min" and days > 10:
                return _json_response(
                    {
                        "ok": False,
                        "error": "timeframe=1min слишком тяжёлый для long-run. Уменьшите days до 10 (или выберите 5min).",
                    },
                    status=400,
                )
            if tf_norm == "5min" and days > 45:
                return _json_response(
                    {
                        "ok": False,
                        "error": "timeframe=5min слишком тяжёлый для long-run. Уменьшите days до 45.",
                    },
                    status=400,
                )

            candles_tf_all = await asyncio.wait_for(
                repo.fetch_intraday_range(figi=figi, from_ts=from_ts, to_ts=to_ts, timeframe=tf),
                timeout=120,
            )
            if not candles_tf_all:
                logger.warning(
                    "backtest_run no candles returned figi=%s interval=%s from_ts=%s to_ts=%s",
                    figi,
                    tf,
                    from_ts.isoformat(),
                    to_ts.isoformat(),
                )
                return _json_response(
                    {
                        "ok": False,
                        "error": "Не удалось загрузить свечи (интрадей). Попробуйте уменьшить days или переключить timeframe на 5min.",
                        "debug": {"interval": tf, "from_ts": from_ts.isoformat(), "to_ts": to_ts.isoformat()},
                    },
                    status=500,
                )
            end_tf = candles_tf_all[-1].time
            cutoff_tf = end_tf - timedelta(days=days)
            candles = [c for c in candles_tf_all if c.time >= cutoff_tf]
            if not candles:
                candles = candles_tf_all[-min(len(candles_tf_all), 2000) :]

            from app.strategies.intraday.bollinger_rsi import BollingerRsiConfig, BollingerRsiStrategy, _to_decimal

            p = dict(params or {})
            cfg0 = BollingerRsiConfig()
            br_cfg = BollingerRsiConfig(
                timeframe=str(p.get("timeframe", cfg0.timeframe)),
                bollinger_period=int(p.get("bollinger_period", cfg0.bollinger_period)),
                bollinger_std_mult=_to_decimal(p.get("bollinger_std_mult", cfg0.bollinger_std_mult)) or cfg0.bollinger_std_mult,
                rsi_period=int(p.get("rsi_period", cfg0.rsi_period)),
                rsi_overbought=_to_decimal(p.get("rsi_overbought", cfg0.rsi_overbought)) or cfg0.rsi_overbought,
                rsi_oversold=_to_decimal(p.get("rsi_oversold", cfg0.rsi_oversold)) or cfg0.rsi_oversold,
                atr_period=int(p.get("atr_period", cfg0.atr_period)),
                sl_atr_mult=_to_decimal(p.get("sl_atr_mult", cfg0.sl_atr_mult)) or cfg0.sl_atr_mult,
                tp_atr_mult=_to_decimal(p.get("tp_atr_mult", cfg0.tp_atr_mult)) or cfg0.tp_atr_mult,
                risk_per_trade_pct=_to_decimal(p.get("risk_per_trade_pct", cfg0.risk_per_trade_pct)) or cfg0.risk_per_trade_pct,
                volume_window=int(p.get("volume_window", cfg0.volume_window)),
                min_volume_ratio=_to_decimal(p.get("min_volume_ratio", cfg0.min_volume_ratio)) or cfg0.min_volume_ratio,
                trade_sessions=tuple(tuple(x) for x in (p.get("trade_sessions") or cfg0.trade_sessions)),
                cooldown_bars=int(p.get("cooldown_bars", cfg0.cooldown_bars)),
            )
            st = BollingerRsiStrategy(figi=figi, config=br_cfg)

            # Risk-based sizing for dry-run (same approach as intraday_vwap_momentum UI backtest):
            def _norm_risk_pct(v: Decimal) -> Decimal:
                return v / Decimal("100") if v >= Decimal("0.1") else v

            inst_cfg = meta.get("instrument_config")
            max_pos = int(meta.get("max_position_qty") or getattr(inst_cfg, "max_position_qty", None) or 10)

            risk_frac = _norm_risk_pct(_to_decimal(p.get("risk_per_trade_pct", br_cfg.risk_per_trade_pct)) or br_cfg.risk_per_trade_pct)
            risk_budget = bt_cfg.initial_equity * risk_frac
            pm = f_spec.price_multiplier
            lot = Decimal(int(f_spec.lot or 1))

            # Close-based SL/TP simulation (ATR multiples) like runner does.
            entry_price: Optional[Decimal] = None
            stop_price: Optional[Decimal] = None
            tp_price: Optional[Decimal] = None

            def _set_levels(*, entry: Decimal, atr_value: Optional[Decimal], direction: int) -> None:
                nonlocal entry_price, stop_price, tp_price
                if direction == 0:
                    return
                if atr_value is None or atr_value <= 0:
                    return
                if br_cfg.sl_atr_mult is None or br_cfg.sl_atr_mult <= 0:
                    return
                if br_cfg.tp_atr_mult is None or br_cfg.tp_atr_mult <= 0:
                    return
                sl_dist = atr_value * br_cfg.sl_atr_mult
                tp_dist = atr_value * br_cfg.tp_atr_mult
                entry_price = entry
                stop_price = (entry - sl_dist) if direction > 0 else (entry + sl_dist)
                tp_price = (entry + tp_dist) if direction > 0 else (entry - tp_dist)

            def _clear_levels() -> None:
                nonlocal entry_price, stop_price, tp_price
                entry_price = None
                stop_price = None
                tp_price = None

            def sig_fn(w, pos):
                nonlocal entry_price, stop_price, tp_price
                if not w:
                    return None
                last = w[-1]

                # 1) Exit by SL/TP levels (close-based)
                if pos != 0 and stop_price is not None and tp_price is not None:
                    if pos > 0 and (last.close <= stop_price or last.close >= tp_price):
                        _clear_levels()
                        try:
                            st.apply_exit_cooldown(last.time)
                        except Exception:  # noqa: BLE001
                            pass
                        from core.models.entities import Signal as CoreSignal, SignalType as CoreSignalType

                        return CoreSignal(
                            strategy_name=strat.value,
                            figi=figi,
                            ts=last.time,
                            signal_type=CoreSignalType.TARGET_QTY,
                            target_qty=0,
                            reason="exit: SL/TP hit (close-based)",
                        )
                    if pos < 0 and (last.close >= stop_price or last.close <= tp_price):
                        _clear_levels()
                        try:
                            st.apply_exit_cooldown(last.time)
                        except Exception:  # noqa: BLE001
                            pass
                        from core.models.entities import Signal as CoreSignal, SignalType as CoreSignalType

                        return CoreSignal(
                            strategy_name=strat.value,
                            figi=figi,
                            ts=last.time,
                            signal_type=CoreSignalType.TARGET_QTY,
                            target_qty=0,
                            reason="exit: SL/TP hit (close-based)",
                        )

                # 2) Strategy signal (entries/mean exit/session exit)
                sig = st.generate_signal(candles=w, current_position_qty=pos, strategy_name=strat.value)
                if sig is None:
                    return None

                # 3) Size entries (direction-only -> absolute qty)
                if pos == 0 and sig.target_qty != 0 and abs(int(sig.target_qty)) == 1:
                    a = getattr(sig, "atr", None)
                    if a is None or a <= 0:
                        return None
                    # risk per 1 contract (RUB): ATR * sl_atr_mult * price_multiplier * lot
                    rs = (a * br_cfg.sl_atr_mult) * pm * lot
                    if rs is None or rs <= 0 or risk_budget <= 0:
                        return None
                    qty = int((risk_budget / rs).to_integral_value(rounding="ROUND_FLOOR"))
                    if qty <= 0:
                        return None
                    qty = min(qty, max_pos) if max_pos > 0 else qty
                    direction = 1 if int(sig.target_qty) > 0 else -1
                    _set_levels(entry=last.close, atr_value=a, direction=direction)

                    from core.models.entities import Signal as CoreSignal

                    return CoreSignal(
                        strategy_name=sig.strategy_name,
                        figi=sig.figi,
                        ts=sig.ts,
                        signal_type=sig.signal_type,
                        target_qty=(qty if direction > 0 else -qty),
                        reason=f"{sig.reason} (sized qty={qty})",
                        atr=a,
                    )

                # If we exit (target=0) - clear levels.
                if sig.target_qty == 0:
                    _clear_levels()

                return sig

            # Quick diagnostics: how many raw entry signals exist if we ignore position state (pos=0 always)
            raw_entries = 0
            try:
                for i in range(len(candles)):
                    w = candles[: i + 1]
                    s0 = st.generate_signal(candles=w, current_position_qty=0, strategy_name=strat.value)
                    if s0 is not None and int(getattr(s0, "target_qty", 0)) != 0:
                        raw_entries += 1
            except Exception:  # noqa: BLE001
                raw_entries = -1

            res = run_backtest_target_qty(figi=figi, strategy_name=strat.value, candles=candles, signal_fn=sig_fn, cfg=bt_cfg)
            summ = summarize(res.trades, res.equity)
            return _json_response(
                {
                    "ok": True,
                    "strategy": strat.value,
                    "figi": figi,
                    "days": days,
                    "initial_equity": str(bt_cfg.initial_equity),
                    "final_equity": str(res.equity[-1].equity if res.equity else bt_cfg.initial_equity),
                    "price_series": [{"ts": c.time.isoformat(), "close": str(c.close)} for c in candles[-2000:]],
                    "instrument_spec": {
                        "price_multiplier": str(f_spec.price_multiplier),
                        "currency": f_spec.currency,
                        "lot": f_spec.lot,
                        "min_price_increment": None if f_spec.min_price_increment is None else str(f_spec.min_price_increment),
                        "min_price_increment_amount": None
                        if f_spec.min_price_increment_amount is None
                        else str(f_spec.min_price_increment_amount),
                        "source": f_spec.source,
                        "max_position_qty": max_pos,
                    },
                    "summary": {
                        "trades": summ.trades,
                        "winrate": summ.winrate,
                        "total_pnl": str(summ.total_pnl),
                        "max_drawdown": str(summ.max_drawdown),
                    },
                    "debug": {
                        "timeframe": tf_norm,
                        "raw_entry_signals_pos0": raw_entries,
                    },
                    "trades": [t.__dict__ for t in res.trades[-200:]],
                    "equity": [p.__dict__ for p in res.equity[-2000:]],
                }
            )

        return _json_response({"ok": False, "error": "Стратегия не поддерживает dry-run"}, status=400)

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
            inst_cfg = meta.get("instrument_config")
            if inst_cfg is None:
                # UI-created job: construct minimal InstrumentConfig
                from app.instruments_config.models import InstrumentConfig, RolloverConfig, StrategyConfig

                inst_cfg = InstrumentConfig(
                    figi=figi,
                    instrument_type="futures",
                    allow_short=True,
                    allow_margin=False,
                    max_position_qty=int(meta.get("max_position_qty") or 10),
                    max_order_qty=int(meta.get("max_order_qty") or (meta.get("max_position_qty") or 10)),
                    rollover=RolloverConfig(enabled=False),
                    strategy=StrategyConfig(name=strat, parameters=dict(meta.get("params") or {})),
                )
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
    app.router.add_post("/api/jobs/create", srv.handle_job_create)
    app.router.add_post("/api/jobs/delete", srv.handle_job_delete)
    app.router.add_post("/api/jobs/start", srv.handle_job_start)
    app.router.add_post("/api/jobs/stop", srv.handle_job_stop)
    app.router.add_get("/api/jobs/decisions", srv.handle_job_decisions)
    app.router.add_get("/api/broker/futures/search", srv.handle_futures_search)
    app.router.add_get("/api/candles/export", srv.handle_candles_export)
    app.router.add_post("/api/candles/export/strategy", srv.handle_candles_export_strategy)
    app.router.add_post("/api/backtest/run", srv.handle_backtest_run)
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

