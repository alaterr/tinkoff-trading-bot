from enum import Enum


class StrategyName(Enum):
    INTERVAL = "interval"
    DONCHIAN_ATR = "donchian_atr"
    EMA_ATR = "ema_atr"
