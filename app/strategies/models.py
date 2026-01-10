from enum import Enum


class StrategyName(Enum):
    INTERVAL = "interval"
    DONCHIAN_ATR = "donchian_atr"
    EMA_ATR = "ema_atr"
    TREND_BREAKOUT_ATR = "trend_breakout_atr"
    INTRADAY_VWAP_MOMENTUM = "intraday_vwap_momentum"
