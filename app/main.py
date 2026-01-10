import asyncio
import logging
import os

from app.client import client
from app.instruments_config.parser import instruments_config
from app.settings import settings
from app.strategies.strategy_fabric import resolve_strategy
from app.ui.server import start_ui_server
from app.strategies.models import StrategyName

logging.basicConfig(
    level=settings.log_level,
    format="[%(levelname)-5s] %(asctime)-19s %(name)s:%(lineno)d: %(message)s",
)
logging.getLogger("tinkoff").setLevel(settings.tinkoff_library_log_level)


async def run():
    # Safety guard: never allow accidental real trading.
    if not settings.sandbox and not settings.i_know_what_i_am_doing:
        raise SystemExit(
            "Refusing to start with SANDBOX=false without explicit confirmation. "
            "Set I_KNOW_WHAT_I_AM_DOING=true to acknowledge real trading mode."
        )
    # Optional UI server (no auth). Enable via UI_ENABLED=true.
    ui_enabled = os.getenv("UI_ENABLED", "false").lower() in {"1", "true", "yes", "on"}
    if ui_enabled:
        from storage.state_store import StateStore

        store = StateStore(db_path=os.getenv("STATE_DB_PATH", "state.db"))
        store.connect()
        host = os.getenv("UI_HOST", "0.0.0.0")
        port = int(os.getenv("UI_PORT", "8000"))
        await start_ui_server(store=store, host=host, port=port)

    # Job mode default: do NOT start trading automatically.
    # Legacy autostart can be enabled explicitly.
    autostart = os.getenv("AUTO_START", "false").lower() in {"1", "true", "yes", "on"}
    if not autostart:
        # Keep process alive (UI + manual jobs).
        await asyncio.Future()

    # Legacy: start configured strategies immediately.
    await client.ainit()
    spawned_tasks = []
    for instrument_config in instruments_config.instruments:
        extra_kwargs = {}
        if instrument_config.strategy.name == StrategyName.INTERVAL:
            extra_kwargs = dict(instrument_config.strategy.parameters)
        strategy = resolve_strategy(
            strategy_name=instrument_config.strategy.name,
            figi=instrument_config.figi,
            instrument_config=instrument_config,
            global_risk=instruments_config.global_risk,
            global_execution=instruments_config.global_execution,
            strategy_params=instrument_config.strategy.parameters,
            **extra_kwargs,
        )
        spawned_tasks.append(asyncio.create_task(strategy.start()))
    await asyncio.wait(spawned_tasks)


if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(loop.create_task(run()))
