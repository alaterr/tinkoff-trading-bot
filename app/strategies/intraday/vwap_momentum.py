from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import List, Optional, Sequence, Tuple

from zoneinfo import ZoneInfo

from app.strategies.indicators import average_volume, vwap_close
from app.strategies.positional.indicators import adx_simple, atr, bollinger_band_width, ema, true_range
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
    # Optional additional entry filter: require EMA_fast slope (reduces noise, but can cut frequency a lot)
    require_fast_slope: bool = True
    # Entry mode:
    # - "cross": only VWAP cross (strict, fewer trades)
    # - "retest": VWAP retest/bounce (more trades)
    # - "cross_or_retest": either condition (recommended)
    entry_mode: str = "cross_or_retest"
    # For retest mode: look back N bars and require price was on the other side of VWAP recently.
    retest_lookback: int = 10
    # Optional: require a micro-breakout on retest (close > prev.high for long / close < prev.low for short)
    require_retest_breakout: bool = True
    # Retest strictness: require last.low/last.high to touch VWAP within ATR band.
    # Set to 0 to disable and use only lookback condition.
    retest_touch_atr_mult: Decimal = Decimal("0.15")
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
    # Exit tuning (to reduce noise):
    # - exit_confirm_bars: require EMA-fast/slow opposite condition for N consecutive bars before exit
    exit_confirm_bars: int = 2
    # - exit_on_vwap_cross: exit if price crosses VWAP against the position (often reduces drawdowns)
    exit_on_vwap_cross: bool = True
    # Time-stop: if position hasn't reached TP/SL and we held too long, exit (prevents bleed in chop)
    max_hold_bars: int = 30

    # Volatility regime filter (relative):
    # Compute short ATR (atr_period) and long ATR (atr_regime_period), require ratio in [min,max].
    # Helps avoid dead low-vol and chaotic high-vol regimes.
    atr_regime_period: int = 100
    atr_regime_min_ratio: Decimal = Decimal("0.6")
    atr_regime_max_ratio: Decimal = Decimal("1.8")

    # Trend regime filter (ADX):
    adx_period: int = 14
    adx_min: Decimal = Decimal("0")  # set to 18..25 to trade only in trend

    # Squeeze -> expansion filter (BB width):
    bb_period: int = 20
    bb_std_mult: Decimal = Decimal("2.0")
    bb_width_lookback: int = 20
    bb_width_mult: Decimal = Decimal("1.05")  # require width_now >= avg_width * mult

    # Quality filters to reduce losing trades:
    # Require entry candle impulse: TrueRange(last, prev) >= min_impulse_atr_mult * ATR
    min_impulse_atr_mult: Decimal = Decimal("0")
    # Require close be far enough from VWAP: abs(close - VWAP) >= min_vwap_dist_atr_mult * ATR
    min_vwap_dist_atr_mult: Decimal = Decimal("0")


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
        self._exit_streak: int = 0
        self._last_pos_sign: int = 0
        self._hold_bars: int = 0

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
        pos_sign = 1 if current_position_qty > 0 else (-1 if current_position_qty < 0 else 0)
        if pos_sign != self._last_pos_sign:
            self._exit_streak = 0
            self._last_pos_sign = pos_sign
            self._hold_bars = 0
        if pos_sign != 0:
            # Count bars in position (close-based)
            self._hold_bars += 1

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

        # Volatility regime filter (entries only): short ATR must be within a reasonable range vs long ATR.
        long_atr = atr(candles, int(self.cfg.atr_regime_period)) if int(self.cfg.atr_regime_period) > 0 else None
        atr_ratio_ok = True
        if a is not None and a > 0 and long_atr is not None and long_atr > 0:
            r = a / long_atr
            atr_ratio_ok = (r >= Decimal(str(self.cfg.atr_regime_min_ratio))) and (r <= Decimal(str(self.cfg.atr_regime_max_ratio)))

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

        # Exits (deterministic, close-based): VWAP cross against + EMA confirm bars
        if current_position_qty > 0:
            if int(self.cfg.max_hold_bars) > 0 and self._hold_bars >= int(self.cfg.max_hold_bars):
                self._cooldown_left = max(0, int(self.cfg.cooldown_bars))
                return Signal(
                    strategy_name=strategy_name,
                    figi=self.figi,
                    ts=last.time,
                    signal_type=SignalType.TARGET_QTY,
                    target_qty=0,
                    reason=f"exit: time-stop {self._hold_bars} bars",
                    atr=a,
                    risk_per_trade_pct=_to_decimal(self.cfg.risk_per_trade_pct),
                    sl_points=_to_decimal(self.cfg.sl_points),
                    tp_points=_to_decimal(self.cfg.tp_points),
                )
            if self.cfg.exit_on_vwap_cross and last.close < vwap_now:
                self._cooldown_left = max(0, int(self.cfg.cooldown_bars))
                return Signal(
                    strategy_name=strategy_name,
                    figi=self.figi,
                    ts=last.time,
                    signal_type=SignalType.TARGET_QTY,
                    target_qty=0,
                    reason="exit: close < VWAP",
                    atr=a,
                    risk_per_trade_pct=_to_decimal(self.cfg.risk_per_trade_pct),
                    sl_points=_to_decimal(self.cfg.sl_points),
                    tp_points=_to_decimal(self.cfg.tp_points),
                )
            if fast_now < slow_now:
                self._exit_streak += 1
            else:
                self._exit_streak = 0
            if self._exit_streak >= max(1, int(self.cfg.exit_confirm_bars)):
                self._cooldown_left = max(0, int(self.cfg.cooldown_bars))
                return Signal(
                    strategy_name=strategy_name,
                    figi=self.figi,
                    ts=last.time,
                    signal_type=SignalType.TARGET_QTY,
                    target_qty=0,
                    reason=f"exit: ema_fast < ema_slow for {self._exit_streak} bars",
                    atr=a,
                    risk_per_trade_pct=_to_decimal(self.cfg.risk_per_trade_pct),
                    sl_points=_to_decimal(self.cfg.sl_points),
                    tp_points=_to_decimal(self.cfg.tp_points),
                )
        if current_position_qty < 0:
            if int(self.cfg.max_hold_bars) > 0 and self._hold_bars >= int(self.cfg.max_hold_bars):
                self._cooldown_left = max(0, int(self.cfg.cooldown_bars))
                return Signal(
                    strategy_name=strategy_name,
                    figi=self.figi,
                    ts=last.time,
                    signal_type=SignalType.TARGET_QTY,
                    target_qty=0,
                    reason=f"exit: time-stop {self._hold_bars} bars",
                    atr=a,
                    risk_per_trade_pct=_to_decimal(self.cfg.risk_per_trade_pct),
                    sl_points=_to_decimal(self.cfg.sl_points),
                    tp_points=_to_decimal(self.cfg.tp_points),
                )
            if self.cfg.exit_on_vwap_cross and last.close > vwap_now:
                self._cooldown_left = max(0, int(self.cfg.cooldown_bars))
                return Signal(
                    strategy_name=strategy_name,
                    figi=self.figi,
                    ts=last.time,
                    signal_type=SignalType.TARGET_QTY,
                    target_qty=0,
                    reason="exit: close > VWAP",
                    atr=a,
                    risk_per_trade_pct=_to_decimal(self.cfg.risk_per_trade_pct),
                    sl_points=_to_decimal(self.cfg.sl_points),
                    tp_points=_to_decimal(self.cfg.tp_points),
                )
            if fast_now > slow_now:
                self._exit_streak += 1
            else:
                self._exit_streak = 0
            if self._exit_streak >= max(1, int(self.cfg.exit_confirm_bars)):
                self._cooldown_left = max(0, int(self.cfg.cooldown_bars))
                return Signal(
                    strategy_name=strategy_name,
                    figi=self.figi,
                    ts=last.time,
                    signal_type=SignalType.TARGET_QTY,
                    target_qty=0,
                    reason=f"exit: ema_fast > ema_slow for {self._exit_streak} bars",
                    atr=a,
                    risk_per_trade_pct=_to_decimal(self.cfg.risk_per_trade_pct),
                    sl_points=_to_decimal(self.cfg.sl_points),
                    tp_points=_to_decimal(self.cfg.tp_points),
                )

        # Entry (flat only)
        if current_position_qty == 0:
            # Require regime filter for entries (if long ATR available)
            if not atr_ratio_ok:
                return None

            # ADX filter (entries only)
            if self.cfg.adx_min is not None and Decimal(str(self.cfg.adx_min)) > 0:
                adx = adx_simple(candles, int(self.cfg.adx_period))
                if adx is None or adx < Decimal(str(self.cfg.adx_min)):
                    return None

            # Bollinger width expansion filter (entries only)
            if int(self.cfg.bb_width_lookback) > 0 and Decimal(str(self.cfg.bb_width_mult)) > 0:
                closes_all = [c.close for c in candles]
                width_now = bollinger_band_width(closes_all, int(self.cfg.bb_period), Decimal(str(self.cfg.bb_std_mult)))
                if width_now is None:
                    return None
                widths: List[Decimal] = []
                # compute a small trailing series (cost is small, periods are small)
                start = max(0, len(closes_all) - (int(self.cfg.bb_width_lookback) + int(self.cfg.bb_period) + 5))
                for k in range(start, len(closes_all)):
                    w = bollinger_band_width(closes_all[: k + 1], int(self.cfg.bb_period), Decimal(str(self.cfg.bb_std_mult)))
                    if w is not None:
                        widths.append(w)
                if len(widths) >= int(self.cfg.bb_width_lookback):
                    avg_w = sum(widths[-int(self.cfg.bb_width_lookback) :]) / Decimal(int(self.cfg.bb_width_lookback))
                    if avg_w > 0 and width_now < (avg_w * Decimal(str(self.cfg.bb_width_mult))):
                        return None
            # VWAP cross conditions
            cross_up = (prev.close < vwap_prev) and (last.close > vwap_now)
            cross_down = (prev.close > vwap_prev) and (last.close < vwap_now)

            fast_up = fast_now > fast_prev
            fast_down = fast_now < fast_prev

            slope_ok_long = (fast_up if self.cfg.require_fast_slope else True)
            slope_ok_short = (fast_down if self.cfg.require_fast_slope else True)

            mode = str(self.cfg.entry_mode or "cross_or_retest").lower().strip()
            if mode not in {"cross", "retest", "cross_or_retest"}:
                mode = "cross_or_retest"

            # Retest logic (more trades): recently on the other side of VWAP, now reclaimed.
            w = max(1, int(self.cfg.retest_lookback))
            recent = candles[-(w + 1) : -1] if len(candles) >= (w + 1) else candles[:-1]
            min_low = min((c.low for c in recent), default=last.low)
            max_high = max((c.high for c in recent), default=last.high)
            # Stricter retest: pullback touches VWAP (within ATR band) and closes back on the trend side.
            touch_band = Decimal("0")
            if a is not None and a > 0:
                touch_band = a * Decimal(str(self.cfg.retest_touch_atr_mult))
            touched_long = (last.low <= (vwap_now + touch_band)) and (last.close > vwap_now)
            touched_short = (last.high >= (vwap_now - touch_band)) and (last.close < vwap_now)

            retest_up = (min_low < vwap_now) and touched_long
            retest_down = (max_high > vwap_now) and touched_short
            if self.cfg.require_retest_breakout:
                retest_up = retest_up and (last.close > prev.high)
                retest_down = retest_down and (last.close < prev.low)

            allow_long = (trend_direction in (0, 1))
            allow_short = (trend_direction in (0, -1))

            long_entry = long_bias and slope_ok_long and allow_long and (
                (cross_up if mode in {"cross", "cross_or_retest"} else False)
                or (retest_up if mode in {"retest", "cross_or_retest"} else False)
            )
            short_entry = short_bias and slope_ok_short and allow_short and (
                (cross_down if mode in {"cross", "cross_or_retest"} else False)
                or (retest_down if mode in {"retest", "cross_or_retest"} else False)
            )

            # Extra quality filters (ATR-based), applied only for entries
            if (long_entry or short_entry) and a is not None and a > 0:
                try:
                    imp_mult = Decimal(str(self.cfg.min_impulse_atr_mult))
                except Exception:  # noqa: BLE001
                    imp_mult = Decimal("0")
                try:
                    dist_mult = Decimal(str(self.cfg.min_vwap_dist_atr_mult))
                except Exception:  # noqa: BLE001
                    dist_mult = Decimal("0")

                if imp_mult > 0:
                    tr = true_range(last, prev)
                    if tr < (a * imp_mult):
                        long_entry = False
                        short_entry = False
                if dist_mult > 0 and (long_entry or short_entry):
                    if abs(last.close - vwap_now) < (a * dist_mult):
                        long_entry = False
                        short_entry = False

            if long_entry:
                return Signal(
                    strategy_name=strategy_name,
                    figi=self.figi,
                    ts=last.time,
                    signal_type=SignalType.TARGET_QTY,
                    target_qty=1,  # direction; RiskGate may size into actual qty using risk_stop
                    reason=f"entry: {mode} long",
                    atr=a,
                    risk_per_trade_pct=_to_decimal(self.cfg.risk_per_trade_pct),
                    sl_points=_to_decimal(self.cfg.sl_points),
                    tp_points=_to_decimal(self.cfg.tp_points),
                )
            if short_entry:
                return Signal(
                    strategy_name=strategy_name,
                    figi=self.figi,
                    ts=last.time,
                    signal_type=SignalType.TARGET_QTY,
                    target_qty=-1,  # direction; RiskGate may size into actual qty using risk_stop
                    reason=f"entry: {mode} short",
                    atr=a,
                    risk_per_trade_pct=_to_decimal(self.cfg.risk_per_trade_pct),
                    sl_points=_to_decimal(self.cfg.sl_points),
                    tp_points=_to_decimal(self.cfg.tp_points),
                )

        return None

