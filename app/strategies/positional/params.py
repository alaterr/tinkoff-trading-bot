from __future__ import annotations

from decimal import Decimal
from typing import Any, Optional

from app.strategies.positional.donchian_atr import DonchianAtrConfig
from app.strategies.positional.ema_atr import EmaAtrConfig


def _to_decimal(v) -> Optional[Decimal]:
    if v is None:
        return None
    if isinstance(v, Decimal):
        return v
    try:
        return Decimal(str(v))
    except Exception:  # noqa: BLE001
        return None


def parse_donchian_atr_config(strategy_params: dict[str, Any] | None) -> DonchianAtrConfig:
    """
    Parse user-provided strategy_params (from instruments_config or UI) into DonchianAtrConfig.
    Keep parsing permissive: config json / UI may provide ints/floats/strings.
    """
    p = dict(strategy_params or {})
    cfg0 = DonchianAtrConfig()
    return DonchianAtrConfig(
        breakout_lookback=int(p.get("breakout_lookback", cfg0.breakout_lookback)),
        exit_lookback=int(p.get("exit_lookback", cfg0.exit_lookback)),
        atr_period=int(p.get("atr_period", cfg0.atr_period)),
        atr_stop_mult=_to_decimal(p.get("atr_stop_mult", cfg0.atr_stop_mult)) or cfg0.atr_stop_mult,
        base_target_qty=int(p.get("base_target_qty", cfg0.base_target_qty)),
    )


def parse_ema_atr_config(strategy_params: dict[str, Any] | None) -> EmaAtrConfig:
    """
    Parse user-provided strategy_params (from instruments_config or UI) into EmaAtrConfig.
    Keep parsing permissive: config json / UI may provide ints/floats/strings.
    """
    p = dict(strategy_params or {})
    cfg0 = EmaAtrConfig()
    return EmaAtrConfig(
        ema_fast=int(p.get("ema_fast", cfg0.ema_fast)),
        ema_slow=int(p.get("ema_slow", cfg0.ema_slow)),
        atr_period=int(p.get("atr_period", cfg0.atr_period)),
        atr_stop_mult=_to_decimal(p.get("atr_stop_mult", cfg0.atr_stop_mult)) or cfg0.atr_stop_mult,
        cooldown_days=int(p.get("cooldown_days", cfg0.cooldown_days)),
        base_target_qty=int(p.get("base_target_qty", cfg0.base_target_qty)),
    )

