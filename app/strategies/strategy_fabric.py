from typing import Dict

from app.strategies.interval.IntervalStrategy import IntervalStrategy
from app.strategies.positional.d1_runner import D1PositionalStrategyRunner
from app.strategies.trend_breakout_atr import TrendBreakoutATRStrategy
from app.strategies.base import BaseStrategy
from app.strategies.errors import UnsupportedStrategyError
from app.strategies.models import StrategyName

strategies: Dict[StrategyName, BaseStrategy.__class__] = {
    StrategyName.INTERVAL: IntervalStrategy,
    StrategyName.DONCHIAN_ATR: D1PositionalStrategyRunner,
    StrategyName.EMA_ATR: D1PositionalStrategyRunner,
    StrategyName.TREND_BREAKOUT_ATR: TrendBreakoutATRStrategy,
}


def resolve_strategy(strategy_name: StrategyName, figi: str, *args, **kwargs) -> BaseStrategy:
    """
    Creates strategy instance by strategy name. Passes all arguments to strategy constructor.

    :param strategy_name: the name of strategy. See :class:`app.strategies.models.StrategyName`
    :param figi: the figi of the instrument strategy applied to
    :return: strategy instance. See :class:`app.strategies.base.BaseStrategy`
    :raises: :class:`app.strategies.errors.UnsupportedStrategyError` if the name is not supported
    """
    if strategy_name not in strategies:
        raise UnsupportedStrategyError(strategy_name)
    cls = strategies[strategy_name]
    # Non-legacy runners need an explicit string strategy name (e.g. "donchian_atr").
    # Keep IntervalStrategy backward-compatible by not injecting extra kwargs there.
    if cls is not IntervalStrategy:
        kwargs.setdefault("strategy_name", strategy_name.value)
    return cls(figi=figi, *args, **kwargs)
