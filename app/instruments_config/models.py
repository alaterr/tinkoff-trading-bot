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
    # Percent-of-equity loss limits (optional alternative to *_rub). Can be set like 0.02 (2%) or 2.0 (2%).
    max_daily_loss_pct: float = 0.0
    max_weekly_loss_pct: float = 0.0

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

    @validator("max_daily_loss_pct", "max_weekly_loss_pct")
    def _non_negative_pct(cls, v: float) -> float:
        if v < 0:
            raise ValueError("must be >= 0")
        if v > 100:
            raise ValueError("too large, sanity check failed")
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
    commission_bps: float = 0.0

    @validator("price_slippage_bps")
    def _slippage_bps(cls, v: float) -> float:
        if v < 0:
            raise ValueError("must be >= 0")
        if v > 500:
            raise ValueError("too large (bps), sanity check failed")
        return v

    @validator("commission_bps")
    def _commission_bps(cls, v: float) -> float:
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


class InstrumentRiskOverrideConfig(BaseModel):
    """
    Optional per-instrument risk overrides.
    Can be provided as `instrument.risk` in instruments_config.json (backward-compatible).
    """

    # Qty caps (aliases for top-level InstrumentConfig fields)
    max_position_qty: Optional[int] = None
    max_order_qty: Optional[int] = None

    # Risk limits
    max_trades_per_day: Optional[int] = None
    max_trades_per_week: Optional[int] = None
    max_daily_loss_rub: Optional[float] = None
    max_weekly_loss_rub: Optional[float] = None
    max_daily_loss_pct: Optional[float] = None
    max_weekly_loss_pct: Optional[float] = None

    # Optional per-instrument risk fraction override (0..1 or percent-like)
    risk_per_trade_pct: Optional[float] = None

    @validator("max_position_qty", "max_order_qty", "max_trades_per_day", "max_trades_per_week")
    def _pos_ints(cls, v: Optional[int]) -> Optional[int]:
        if v is None:
            return v
        if v <= 0:
            raise ValueError("must be > 0")
        return v

    @validator("max_daily_loss_rub", "max_weekly_loss_rub")
    def _non_negative_money_opt(cls, v: Optional[float]) -> Optional[float]:
        if v is None:
            return v
        if v < 0:
            raise ValueError("must be >= 0")
        return v

    @validator("max_daily_loss_pct", "max_weekly_loss_pct")
    def _non_negative_pct_opt(cls, v: Optional[float]) -> Optional[float]:
        if v is None:
            return v
        if v < 0:
            raise ValueError("must be >= 0")
        if v > 100:
            raise ValueError("too large, sanity check failed")
        return v

    @validator("risk_per_trade_pct")
    def _risk_pct_opt(cls, v: Optional[float]) -> Optional[float]:
        if v is None:
            return v
        # allow percent-like; clamp sanity only
        if v <= 0:
            raise ValueError("must be > 0")
        if v > 100:
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
    # Optional per-instrument overrides (e.g. {"risk": {"max_trades_per_day": 3, ...}})
    risk: Optional[InstrumentRiskOverrideConfig] = None
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
        risk: InstrumentRiskOverrideConfig = values.get("risk") or InstrumentRiskOverrideConfig()

        # Apply risk aliases to top-level qty limits if provided in instrument.risk
        if values.get("max_position_qty") is None and risk.max_position_qty is not None:
            values["max_position_qty"] = risk.max_position_qty
        if values.get("max_order_qty") is None and risk.max_order_qty is not None:
            values["max_order_qty"] = risk.max_order_qty

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
