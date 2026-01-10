from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import List, Optional

from core.models.entities import Candle, Signal, SignalType
from app.strategies.positional.indicators import atr, donchian_high, donchian_low


@dataclass(frozen=True)
class DonchianAtrConfig:
    breakout_lookback: int = 20
    exit_lookback: int = 10
    atr_period: int = 14
    atr_stop_mult: Decimal = Decimal("3")
    # target position direction: +/-1 contracts (RiskGate can scale up)
    base_target_qty: int = 1


class DonchianATRStrategy:
    """
    D1 positional:
    - Entry: close breaks above/below Donchian channel over breakout_lookback
    - Exit: opposite channel break over exit_lookback OR ATR stop (tracked externally in v2)

    MVP: produces target_qty = +/- base_target_qty or 0.
    ATR is attached to Signal for RiskGate sizing.
    """

    def __init__(self, *, figi: str, config: DonchianAtrConfig):
        self.figi = figi
        self.cfg = config

    def generate_signal(
        self,
        *,
        candles: List[Candle],
        current_position_qty: int,
        strategy_name: str = "donchian_atr",
    ) -> Optional[Signal]:
        if len(candles) < max(self.cfg.breakout_lookback, self.cfg.exit_lookback) + 1:
            return None

        last = candles[-1]
        prev = candles[:-1]

        hi = donchian_high(prev, self.cfg.breakout_lookback)
        lo = donchian_low(prev, self.cfg.breakout_lookback)
        if hi is None or lo is None:
            return None

        exit_hi = donchian_high(prev, self.cfg.exit_lookback)
        exit_lo = donchian_low(prev, self.cfg.exit_lookback)
        a = atr(candles, self.cfg.atr_period)

        # Exit first: if in position and reverse breakout on exit channel -> flat
        if current_position_qty > 0 and exit_lo is not None and last.close < exit_lo:
            return Signal(
                strategy_name=strategy_name,
                figi=self.figi,
                ts=last.time,
                signal_type=SignalType.TARGET_QTY,
                target_qty=0,
                reason="exit: close < donchian_low(exit_lookback)",
                atr=a,
            )
        if current_position_qty < 0 and exit_hi is not None and last.close > exit_hi:
            return Signal(
                strategy_name=strategy_name,
                figi=self.figi,
                ts=last.time,
                signal_type=SignalType.TARGET_QTY,
                target_qty=0,
                reason="exit: close > donchian_high(exit_lookback)",
                atr=a,
            )

        # Entry: breakout
        if last.close > hi:
            return Signal(
                strategy_name=strategy_name,
                figi=self.figi,
                ts=last.time,
                signal_type=SignalType.TARGET_QTY,
                target_qty=abs(self.cfg.base_target_qty),
                reason="entry: close > donchian_high(breakout_lookback)",
                atr=a,
            )
        if last.close < lo:
            return Signal(
                strategy_name=strategy_name,
                figi=self.figi,
                ts=last.time,
                signal_type=SignalType.TARGET_QTY,
                target_qty=-abs(self.cfg.base_target_qty),
                reason="entry: close < donchian_low(breakout_lookback)",
                atr=a,
            )

        return None

