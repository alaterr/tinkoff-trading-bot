from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, root_validator, validator

from app.strategies.models import StrategyName


class StrategyConfig(BaseModel):
    name: StrategyName
    parameters: Dict[str, Any]


class GlobalRiskConfig(BaseModel):
    # Position / trade limits
    max_positions_total: int = 10
    max_trades_per_day: int = 10
    max_trades_per_week: int = 50

    # Loss limits
    max_daily_loss_rub: float = 0.0
    max_weekly_loss_rub: float = 0.0

    # Budgeting / safety
    risk_per_trade_pct: float = 0.01
    cooldown_seconds_after_error: int = 60
    kill_switch_file: str = "kill.switch"

    @validator("max_positions_total", "max_trades_per_day", "max_trades_per_week")
    def _positive_ints(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("must be > 0")
        return v

    @validator("max_daily_loss_rub", "max_weekly_loss_rub")
    def _non_negative_money(cls, v: float) -> float:
        if v < 0:
            raise ValueError("must be >= 0")
        return v

    @validator("risk_per_trade_pct")
    def _risk_pct(cls, v: float) -> float:
        if not (0 < v <= 1):
            raise ValueError("must be in (0, 1]")
        return v

    @validator("cooldown_seconds_after_error")
    def _cooldown(cls, v: int) -> int:
        if v < 0:
            raise ValueError("must be >= 0")
        return v


class GlobalExecutionConfig(BaseModel):
    order_type: Literal["market"] = "market"
    trade_on_close: bool = True
    trade_only_market_hours: bool = True
    timezone: str = "Europe/Moscow"
    price_slippage_bps: float = 0.0

    @validator("price_slippage_bps")
    def _slippage_bps(cls, v: float) -> float:
        if v < 0:
            raise ValueError("must be >= 0")
        if v > 500:
            raise ValueError("too large (bps), sanity check failed")
        return v


class RolloverConfig(BaseModel):
    enabled: bool = False
    days_before_expiry_to_roll: int = 5
    roll_mode: Literal["close_and_open"] = "close_and_open"
    # MVP: explicit mapping to the next contract
    next_contract_figi: Optional[str] = None

    @validator("days_before_expiry_to_roll")
    def _days(cls, v: int) -> int:
        if v < 0:
            raise ValueError("must be >= 0")
        if v > 60:
            raise ValueError("too large, sanity check failed")
        return v


class InstrumentConfig(BaseModel):
    figi: str
    strategy: StrategyConfig
    # Extended positional trading settings (backward compatible)
    instrument_type: Optional[Literal["futures", "share", "bond", "etf", "currency"]] = None
    allow_short: Optional[bool] = None
    allow_margin: Optional[bool] = None
    max_position_qty: Optional[int] = None
    max_order_qty: Optional[int] = None
    rollover: RolloverConfig = Field(default_factory=RolloverConfig)

    @validator("max_position_qty", "max_order_qty")
    def _qty_limits(cls, v: Optional[int]) -> Optional[int]:
        if v is None:
            return v
        if v <= 0:
            raise ValueError("must be > 0")
        return v

    @root_validator
    def _validate_futures_flags(cls, values: Dict[str, Any]) -> Dict[str, Any]:
        instrument_type = values.get("instrument_type")
        allow_short = values.get("allow_short")
        allow_margin = values.get("allow_margin")
        rollover: RolloverConfig = values.get("rollover") or RolloverConfig()

        if instrument_type == "futures":
            # Defaults per requirements
            if allow_margin is None:
                values["allow_margin"] = False
            if allow_short is None:
                values["allow_short"] = True
            if rollover.enabled and not rollover.next_contract_figi:
                raise ValueError(
                    "rollover.enabled=true requires rollover.next_contract_figi (MVP mapping)"
                )
        else:
            # allow_short only permitted for futures
            if allow_short is True:
                raise ValueError("allow_short=true is only supported for instrument_type='futures'")
            # allow_margin should be disabled by default if explicitly set
            if allow_margin is True:
                raise ValueError("allow_margin=true is not supported in this bot (safety default)")

        return values


class InstrumentsConfig(BaseModel):
    instruments: List[InstrumentConfig]
    global_risk: GlobalRiskConfig = Field(default_factory=GlobalRiskConfig)
    global_execution: GlobalExecutionConfig = Field(default_factory=GlobalExecutionConfig)
