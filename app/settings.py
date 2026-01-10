import logging
from typing import Optional

from pydantic import BaseSettings


class Settings(BaseSettings):
    app_name: str = "qwertyo1"
    # Token may be provided via env (.env) or set at runtime via UI (job mode).
    token: Optional[str] = None
    account_id: Optional[str] = None
    sandbox: bool = True
    # Safety latch: real trading mode requires explicit confirmation flag.
    # This must remain False by default.
    i_know_what_i_am_doing: bool = False
    log_level = logging.DEBUG
    tinkoff_library_log_level = logging.INFO
    use_candle_history_cache = True

    class Config:
        env_file = ".env"


settings = Settings()
