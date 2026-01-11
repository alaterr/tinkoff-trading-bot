from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import List, Optional, Tuple

from zoneinfo import ZoneInfo

from app.strategies.indicators import average_volume
from app.strategies.positional.indicators import atr, rsi, sma, std
from core.models.entities import Candle, Signal, SignalType


@dataclass(frozen=True)
class BollingerRsiConfig:
    timeframe: str = "5min"  # "5min" | "1min"

    bollinger_period: int = 20
    bollinger_std_mult: Decimal = Decimal("2.0")

    rsi_period: int = 14
    rsi_overbought: Decimal = Decimal("70")
    rsi_oversold: Decimal = Decimal("30")

    atr_period: int = 14
    sl_atr_mult: Decimal = Decimal("1.0")
    tp_atr_mult: Decimal = Decimal("2.0")

    # risk per trade (0.3 => 0.3% or 0.003 => 0.3%)
    risk_per_trade_pct: Decimal = Decimal("0.3")

    volume_window: int = 30
    min_volume_ratio: Decimal = Decimal("1.5")

    # trade sessions in MSK, list of [start, end]
    trade_sessions: Tuple[Tuple[str, str], ...] = (("10:15", "17:30"),)
    cooldown_bars: int = 3


def _to_decimal(v) -> Optional[Decimal]:
    if v is None:
        return None
    if isinstance(v, Decimal):
        return v
    try:
        return Decimal(str(v))
    except Exception:  # noqa: BLE001
        return None


class BollingerRsiStrategy:
    """
    Intraday Bollinger–RSI Reversion (signal generator):
    - Entry (flat only): close beyond Bollinger band + RSI extreme + volume filter + session filter
    - Exit: mean reversion to SMA midline OR session end close
    - Stop/TP: handled by runner using ATR multiples from config
    - Cooldown: blocks entries for N bars after any exit
    """

    def __init__(self, *, figi: str, config: BollingerRsiConfig):
        self.figi = figi
        self.cfg = config
        self._cooldown_left: int = 0
        self._last_bar_ts: Optional[datetime] = None
        self._msk = ZoneInfo("Europe/Moscow")

    def _on_new_bar(self, ts: datetime) -> None:
        if self._last_bar_ts is None or ts > self._last_bar_ts:
            if self._cooldown_left > 0:
                self._cooldown_left -= 1
            self._last_bar_ts = ts

    def apply_exit_cooldown(self, ts: datetime) -> None:
        # Runner may exit by SL/TP; this keeps cooldown consistent.
        self._on_new_bar(ts)
        self._cooldown_left = max(0, int(self.cfg.cooldown_bars))

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

    def generate_signal(
        self,
        *,
        candles: List[Candle],
        current_position_qty: int,
        strategy_name: str = "intraday_bollinger_rsi",
    ) -> Optional[Signal]:
        if not candles:
            return None

        last = candles[-1]
        self._on_new_bar(last.time)

        # Forced exit before session end (spec: 5 minutes)
        mins_to_end = self._minutes_to_session_end(last.time)
        if current_position_qty != 0 and mins_to_end is not None and mins_to_end <= 5:
            self._cooldown_left = max(0, int(self.cfg.cooldown_bars))
            return Signal(
                strategy_name=strategy_name,
                figi=self.figi,
                ts=last.time,
                signal_type=SignalType.TARGET_QTY,
                target_qty=0,
                reason="exit: before session end",
                risk_per_trade_pct=_to_decimal(self.cfg.risk_per_trade_pct),
            )

        # Need enough history for indicators & cross checks
        need = max(
            int(self.cfg.bollinger_period),
            int(self.cfg.rsi_period),
            int(self.cfg.atr_period),
            int(self.cfg.volume_window),
        ) + 2
        if len(candles) < need:
            return None

        prev = candles[-2]
        closes = [c.close for c in candles]

        mid_now = sma(closes, int(self.cfg.bollinger_period))
        mid_prev = sma(closes[:-1], int(self.cfg.bollinger_period))
        sd_now = std(closes, int(self.cfg.bollinger_period))

        if mid_now is None or mid_prev is None or sd_now is None:
            return None

        # Exit by mean reversion to midline (close crosses midline) - should work even outside entry filters.
        a = atr(candles, int(self.cfg.atr_period))
        if current_position_qty > 0:
            # long exits when crossing from below to at/above midline
            if prev.close < mid_prev and last.close >= mid_now:
                self._cooldown_left = max(0, int(self.cfg.cooldown_bars))
                return Signal(
                    strategy_name=strategy_name,
                    figi=self.figi,
                    ts=last.time,
                    signal_type=SignalType.TARGET_QTY,
                    target_qty=0,
                    reason="exit: mean reversion to SMA(mid)",
                    atr=a,
                    risk_per_trade_pct=_to_decimal(self.cfg.risk_per_trade_pct),
                )
            return None

        if current_position_qty < 0:
            # short exits when crossing from above to at/below midline
            if prev.close > mid_prev and last.close <= mid_now:
                self._cooldown_left = max(0, int(self.cfg.cooldown_bars))
                return Signal(
                    strategy_name=strategy_name,
                    figi=self.figi,
                    ts=last.time,
                    signal_type=SignalType.TARGET_QTY,
                    target_qty=0,
                    reason="exit: mean reversion to SMA(mid)",
                    atr=a,
                    risk_per_trade_pct=_to_decimal(self.cfg.risk_per_trade_pct),
                )
            return None

        # Entry-only filters
        if not self._in_trade_session(last.time):
            return None

        mult = _to_decimal(self.cfg.bollinger_std_mult) or Decimal("2")
        upper = mid_now + (mult * sd_now)
        lower = mid_now - (mult * sd_now)

        r = rsi(closes, int(self.cfg.rsi_period))
        if r is None:
            return None

        # Volume filter (current vs avg of previous window)
        if self.cfg.min_volume_ratio is not None and self.cfg.min_volume_ratio > 0:
            avg_v = average_volume(candles, int(self.cfg.volume_window), include_last=False)
            if avg_v is None or avg_v <= 0:
                return None
            ratio = (Decimal(int(last.volume)) / avg_v) if avg_v > 0 else Decimal("0")
            if ratio < Decimal(str(self.cfg.min_volume_ratio)):
                return None

        # Cooldown blocks entries only when flat
        if self._cooldown_left > 0:
            return None

        # Entry (flat only): extreme band + RSI extreme
        ro = _to_decimal(self.cfg.rsi_overbought) or Decimal("70")
        rs = _to_decimal(self.cfg.rsi_oversold) or Decimal("30")

        if last.close < lower and r < rs:
            return Signal(
                strategy_name=strategy_name,
                figi=self.figi,
                ts=last.time,
                signal_type=SignalType.TARGET_QTY,
                target_qty=1,  # direction only; RiskGate/runner sizes using risk_stop
                reason="entry: close < lower_bollinger and RSI oversold",
                atr=a,
                risk_per_trade_pct=_to_decimal(self.cfg.risk_per_trade_pct),
            )

        if last.close > upper and r > ro:
            return Signal(
                strategy_name=strategy_name,
                figi=self.figi,
                ts=last.time,
                signal_type=SignalType.TARGET_QTY,
                target_qty=-1,  # direction only; RiskGate/runner sizes using risk_stop
                reason="entry: close > upper_bollinger and RSI overbought",
                atr=a,
                risk_per_trade_pct=_to_decimal(self.cfg.risk_per_trade_pct),
            )

        return None

