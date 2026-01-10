from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import List, Optional

from core.models.entities import Candle, Signal, SignalType
from strategies.indicators import atr, ema


@dataclass(frozen=True)
class EmaAtrConfig:
    ema_fast: int = 20
    ema_slow: int = 50
    atr_period: int = 14
    atr_stop_mult: Decimal = Decimal("3")
    cooldown_days: int = 5
    base_target_qty: int = 1


class EmaAtrTrendStrategy:
    """
    D1 positional:
    - Long bias when close > EMA(slow) and EMA(fast) > EMA(slow)
    - Short bias when close < EMA(slow) and EMA(fast) < EMA(slow)
    - Exit on opposite bias; cooldown_days after exiting (tracked via state by runner/OMS layer)

    MVP: returns target_qty (+/- base_target_qty or 0) and includes ATR for RiskGate sizing.
    """

    def __init__(self, *, figi: str, config: EmaAtrConfig):
        self.figi = figi
        self.cfg = config

    def generate_signal(
        self,
        *,
        candles: List[Candle],
        current_position_qty: int,
        in_cooldown: bool,
        strategy_name: str = "ema_atr",
    ) -> Optional[Signal]:
        if in_cooldown:
            return None

        if len(candles) < max(self.cfg.ema_fast, self.cfg.ema_slow) + 1:
            return None

        closes = [c.close for c in candles]
        efast = ema(closes, self.cfg.ema_fast)
        eslow = ema(closes, self.cfg.ema_slow)
        last = candles[-1]
        a = atr(candles, self.cfg.atr_period)

        fast = efast[-1]
        slow = eslow[-1]

        long_bias = last.close > slow and fast > slow
        short_bias = last.close < slow and fast < slow

        # Exit on opposite bias (or loss of bias)
        if current_position_qty > 0 and not long_bias:
            return Signal(
                strategy_name=strategy_name,
                figi=self.figi,
                ts=last.time,
                signal_type=SignalType.TARGET_QTY,
                target_qty=0,
                reason="exit: long bias invalidated",
                atr=a,
            )
        if current_position_qty < 0 and not short_bias:
            return Signal(
                strategy_name=strategy_name,
                figi=self.figi,
                ts=last.time,
                signal_type=SignalType.TARGET_QTY,
                target_qty=0,
                reason="exit: short bias invalidated",
                atr=a,
            )

        if long_bias:
            return Signal(
                strategy_name=strategy_name,
                figi=self.figi,
                ts=last.time,
                signal_type=SignalType.TARGET_QTY,
                target_qty=abs(self.cfg.base_target_qty),
                reason="entry/hold: long bias",
                atr=a,
            )
        if short_bias:
            return Signal(
                strategy_name=strategy_name,
                figi=self.figi,
                ts=last.time,
                signal_type=SignalType.TARGET_QTY,
                target_qty=-abs(self.cfg.base_target_qty),
                reason="entry/hold: short bias",
                atr=a,
            )

        return None

