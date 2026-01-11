from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import List, Optional, Sequence, Tuple

from zoneinfo import ZoneInfo

from app.strategies.indicators import average_volume, vwap_close
from app.strategies.positional.indicators import atr, ema
from core.models.entities import Candle, Signal, SignalType


@dataclass(frozen=True)
class VwapMomentumConfig:
    timeframe: str = "1min"  # "1min" | "5min"
    # VWAP window:
    # - vwap_period: minutes (backward-compatible, default)
    # - vwap_window: bars (preferred for 5m setup). If set, it overrides vwap_period.
    vwap_period: int = 30  # minutes
    vwap_window: Optional[int] = None  # bars
    ema_fast: int = 5
    ema_slow: int = 20
    # Higher timeframe trend filter (optional)
    trend_timeframe: Optional[str] = None  # "1h" | "4h" | None
    trend_ema_fast: int = 20
    trend_ema_slow: int = 50
    atr_period: int = 14
    # fixed SL/TP in "points" (interpreted as ticks by runner)
    sl_points: Optional[Decimal] = Decimal("50")
    tp_points: Optional[Decimal] = Decimal("100")
    # ATR-based SL/TP alternative
    atr_sl_mult: Optional[Decimal] = None
    atr_tp_mult: Optional[Decimal] = None
    # ATR trailing stop (optional). If set > 0, runner will trail stop by atr_trail_mult * ATR.
    atr_trail_mult: Optional[Decimal] = None
    # risk per trade (0.3 => 0.3% or 0.003 => 0.3%)
    risk_per_trade_pct: Decimal = Decimal("0.3")
    volume_window: int = 20
    min_volume_ratio: Decimal = Decimal("1.5")
    trade_sessions: Tuple[Tuple[str, str], ...] = (("10:00", "17:00"),)  # MSK
    cooldown_bars: int = 3
    exit_before_session_end_minutes: int = 5


def _to_decimal(v) -> Optional[Decimal]:
    if v is None:
        return None
    if isinstance(v, Decimal):
        return v
    try:
        return Decimal(str(v))
    except Exception:  # noqa: BLE001
        return None


class VwapMomentumStrategy:
    """
    Intraday VWAP Momentum (signal generator):
    - Entry (flat only): VWAP cross + EMA trend filter + volume filter + session filter
    - Exit: EMA fast crosses EMA slow in the opposite direction OR session end close
    - Cooldown: blocks entries for N bars after exit

    Position sizing is handled by RiskGate/runner using risk hints attached to Signal.
    """

    def __init__(self, *, figi: str, config: VwapMomentumConfig):
        self.figi = figi
        self.cfg = config
        self._cooldown_left: int = 0
        self._last_bar_ts: Optional[datetime] = None
        self._msk = ZoneInfo("Europe/Moscow")

    def _bar_minutes(self) -> int:
        tf = str(self.cfg.timeframe).lower().strip()
        if tf == "1min":
            return 1
        if tf == "5min":
            return 5
        raise ValueError("timeframe must be '1min' or '5min'")

    def _vwap_bars(self) -> int:
        if self.cfg.vwap_window is not None:
            return max(1, int(self.cfg.vwap_window))
        bm = self._bar_minutes()
        return max(1, int(math.ceil(int(self.cfg.vwap_period) / bm)))

    @staticmethod
    def trend_dir_from_candles(*, candles: List[Candle], ema_fast_p: int, ema_slow_p: int) -> int:
        """
        +1 bull, -1 bear, 0 unknown.
        Bull if close > EMA_slow and EMA_fast > EMA_slow; bear if close < EMA_slow and EMA_fast < EMA_slow.
        """
        if not candles:
            return 0
        need = max(int(ema_fast_p), int(ema_slow_p)) + 2
        if len(candles) < need:
            return 0
        closes = [c.close for c in candles]
        ef = ema(closes, int(ema_fast_p))
        es = ema(closes, int(ema_slow_p))
        if not ef or not es:
            return 0
        last_close = closes[-1]
        if last_close > es[-1] and ef[-1] > es[-1]:
            return 1
        if last_close < es[-1] and ef[-1] < es[-1]:
            return -1
        return 0

    def _in_trade_session(self, ts: datetime) -> bool:
        try:
            t = ts.astimezone(self._msk).time()
        except Exception:
            t = ts.time()
        for a, b in self.cfg.trade_sessions:
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

    def _minutes_to_session_end(self, ts: datetime) -> Optional[int]:
        try:
            local = ts.astimezone(self._msk)
        except Exception:
            return None
        t = local.time()
        for a, b in self.cfg.trade_sessions:
            try:
                eh, em = b.split(":")
                end_dt = datetime(local.year, local.month, local.day, int(eh), int(em), tzinfo=self._msk)
                sh, sm = a.split(":")
                start_dt = datetime(local.year, local.month, local.day, int(sh), int(sm), tzinfo=self._msk)
            except Exception:
                continue
            if start_dt.time() <= t <= end_dt.time():
                return int((end_dt - local).total_seconds() // 60)
        return None

    def _on_new_bar(self, ts: datetime) -> None:
        if self._last_bar_ts is None or ts > self._last_bar_ts:
            if self._cooldown_left > 0:
                self._cooldown_left -= 1
            self._last_bar_ts = ts

    def generate_signal(
        self,
        *,
        candles: List[Candle],
        current_position_qty: int,
        trend_direction: int = 0,
        strategy_name: str = "intraday_vwap_momentum",
    ) -> Optional[Signal]:
        if not candles:
            return None

        last = candles[-1]
        self._on_new_bar(last.time)

        # Time-based forced exit near session end
        mins_to_end = self._minutes_to_session_end(last.time)
        if (
            current_position_qty != 0
            and self.cfg.exit_before_session_end_minutes > 0
            and mins_to_end is not None
            and mins_to_end <= int(self.cfg.exit_before_session_end_minutes)
        ):
            self._cooldown_left = max(0, int(self.cfg.cooldown_bars))
            return Signal(
                strategy_name=strategy_name,
                figi=self.figi,
                ts=last.time,
                signal_type=SignalType.TARGET_QTY,
                target_qty=0,
                reason="exit: before session end",
                risk_per_trade_pct=_to_decimal(self.cfg.risk_per_trade_pct),
                sl_points=_to_decimal(self.cfg.sl_points),
                tp_points=_to_decimal(self.cfg.tp_points),
            )

        # Need enough history for indicators & cross checks
        need = max(self._vwap_bars(), int(self.cfg.ema_slow), int(self.cfg.atr_period), int(self.cfg.volume_window)) + 2
        if len(candles) < need:
            return None

        # Cooldown blocks entries only when flat
        if current_position_qty == 0 and self._cooldown_left > 0:
            return None

        if not self._in_trade_session(last.time):
            return None

        closes = [c.close for c in candles]
        efast = ema(closes, int(self.cfg.ema_fast))
        eslow = ema(closes, int(self.cfg.ema_slow))
        if len(efast) < 2 or len(eslow) < 2:
            return None

        fast_now, fast_prev = efast[-1], efast[-2]
        slow_now, slow_prev = eslow[-1], eslow[-2]

        vwap_vals = vwap_close(candles, self._vwap_bars())
        vwap_now = vwap_vals[-1]
        vwap_prev = vwap_vals[-2] if len(vwap_vals) >= 2 else None
        if vwap_now is None or vwap_prev is None:
            return None

        prev = candles[-2]
        # ATR is attached as a hint (price units) for optional ATR-based stop calculations in runner.
        a = atr(candles, int(self.cfg.atr_period))

        # Volume filter (current vs avg of previous window)
        if self.cfg.min_volume_ratio is not None and self.cfg.min_volume_ratio > 0:
            avg_v = average_volume(candles, int(self.cfg.volume_window), include_last=False)
            if avg_v is None or avg_v <= 0:
                return None
            ratio = (Decimal(int(last.volume)) / avg_v) if avg_v > 0 else Decimal("0")
            if ratio < Decimal(str(self.cfg.min_volume_ratio)):
                return None

        long_bias = (last.close > vwap_now) and (fast_now > slow_now)
        short_bias = (last.close < vwap_now) and (fast_now < slow_now)

        # EMA-cross early exit
        if current_position_qty > 0 and (fast_now < slow_now):
            self._cooldown_left = max(0, int(self.cfg.cooldown_bars))
            return Signal(
                strategy_name=strategy_name,
                figi=self.figi,
                ts=last.time,
                signal_type=SignalType.TARGET_QTY,
                target_qty=0,
                reason="exit: ema_fast crossed below ema_slow",
                atr=a,
                risk_per_trade_pct=_to_decimal(self.cfg.risk_per_trade_pct),
                sl_points=_to_decimal(self.cfg.sl_points),
                tp_points=_to_decimal(self.cfg.tp_points),
            )
        if current_position_qty < 0 and (fast_now > slow_now):
            self._cooldown_left = max(0, int(self.cfg.cooldown_bars))
            return Signal(
                strategy_name=strategy_name,
                figi=self.figi,
                ts=last.time,
                signal_type=SignalType.TARGET_QTY,
                target_qty=0,
                reason="exit: ema_fast crossed above ema_slow",
                atr=a,
                risk_per_trade_pct=_to_decimal(self.cfg.risk_per_trade_pct),
                sl_points=_to_decimal(self.cfg.sl_points),
                tp_points=_to_decimal(self.cfg.tp_points),
            )

        # Entry (flat only)
        if current_position_qty == 0:
            # VWAP cross conditions
            cross_up = (prev.close < vwap_prev) and (last.close > vwap_now)
            cross_down = (prev.close > vwap_prev) and (last.close < vwap_now)

            fast_up = fast_now > fast_prev
            fast_down = fast_now < fast_prev

            if long_bias and cross_up and fast_up and (trend_direction in (0, 1)):
                return Signal(
                    strategy_name=strategy_name,
                    figi=self.figi,
                    ts=last.time,
                    signal_type=SignalType.TARGET_QTY,
                    target_qty=1,  # direction; RiskGate may size into actual qty using risk_stop
                    reason="entry: VWAP cross up + EMA trend up",
                    atr=a,
                    risk_per_trade_pct=_to_decimal(self.cfg.risk_per_trade_pct),
                    sl_points=_to_decimal(self.cfg.sl_points),
                    tp_points=_to_decimal(self.cfg.tp_points),
                )
            if short_bias and cross_down and fast_down and (trend_direction in (0, -1)):
                return Signal(
                    strategy_name=strategy_name,
                    figi=self.figi,
                    ts=last.time,
                    signal_type=SignalType.TARGET_QTY,
                    target_qty=-1,  # direction; RiskGate may size into actual qty using risk_stop
                    reason="entry: VWAP cross down + EMA trend down",
                    atr=a,
                    risk_per_trade_pct=_to_decimal(self.cfg.risk_per_trade_pct),
                    sl_points=_to_decimal(self.cfg.sl_points),
                    tp_points=_to_decimal(self.cfg.tp_points),
                )

        return None

