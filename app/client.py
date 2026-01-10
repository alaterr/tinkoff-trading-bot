from pathlib import Path
from typing import Optional

from t_tech.invest import (
    AsyncClient,
    Client,
    PostOrderResponse,
    GetLastPricesResponse,
    OrderState,
    GetTradingStatusResponse,
    InstrumentResponse,
)
from t_tech.invest.async_services import AsyncServices, MarketDataService
from t_tech.invest.caching.market_data_cache.cache_settings import MarketDataCacheSettings
from t_tech.invest.services import MarketDataCache, Services

from app.settings import settings


class TinkoffClient:
    """
    Wrapper for tinkoff.invest.AsyncClient.
    Takes responsibility for choosing correct function to call basing on sandbox mode flag.
    """

    def __init__(self, token: str, sandbox: bool = False):
        self.token = token
        self.sandbox = sandbox
        self.client: Optional[AsyncServices] = None
        self.sync_client: Optional[Services] = None
        self.market_data_cache: Optional[MarketDataCache] = None

    async def ainit(self):
        self.client = await AsyncClient(token=self.token, app_name=settings.app_name).__aenter__()
        if settings.use_candle_history_cache:
            self.sync_client = Client(token=self.token, app_name=settings.app_name).__enter__()
            self.market_data_cache = MarketDataCache(
                settings=MarketDataCacheSettings(base_cache_dir=Path("market_data_cache")),
                services=self.sync_client,
            )

    async def aclose(self):
        """
        Best-effort shutdown of underlying SDK clients.
        """
        try:
            if self.client is not None:
                await self.client.__aexit__(None, None, None)
        except Exception:
            pass
        try:
            if self.sync_client is not None:
                self.sync_client.__exit__(None, None, None)
        except Exception:
            pass
        self.client = None
        self.sync_client = None
        self.market_data_cache = None

    async def get_orders(self, **kwargs):
        if self.sandbox:
            return await self.client.sandbox.get_sandbox_orders(**kwargs)
        return await self.client.orders.get_orders(**kwargs)

    async def get_portfolio(self, **kwargs):
        if self.sandbox:
            return await self.client.sandbox.get_sandbox_portfolio(**kwargs)
        return await self.client.operations.get_portfolio(**kwargs)

    async def get_accounts(self):
        if self.sandbox:
            return await self.client.sandbox.get_sandbox_accounts()
        return await self.client.users.get_accounts()

    async def get_all_candles(self, **kwargs):
        if settings.use_candle_history_cache:
            for candle in self.market_data_cache.get_all_candles(**kwargs):
                yield candle
        else:
            async for candle in self.client.get_all_candles(**kwargs):
                yield candle

    async def get_last_prices(self, **kwargs) -> GetLastPricesResponse:
        return await self.client.market_data.get_last_prices(**kwargs)

    async def post_order(self, **kwargs) -> PostOrderResponse:
        if self.sandbox:
            return await self.client.sandbox.post_sandbox_order(**kwargs)
        return await self.client.orders.post_order(**kwargs)

    async def get_order_state(self, **kwargs) -> OrderState:
        if self.sandbox:
            return await self.client.sandbox.get_sandbox_order_state(**kwargs)
        return await self.client.orders.get_order_state(**kwargs)

    async def get_trading_status(self, **kwargs) -> GetTradingStatusResponse:
        return await self.client.market_data.get_trading_status(**kwargs)

    async def get_instrument(self, **kwargs) -> InstrumentResponse:
        return await self.client.instruments.get_instrument_by(**kwargs)


class RuntimeClient:
    """
    Lazy / runtime-configurable client.
    - Allows starting the app without TOKEN in env (UI job mode).
    - Keeps backward compatibility: existing code imports `client` and calls async methods.
    """

    def __init__(self):
        self._inner: Optional[TinkoffClient] = None
        self._token: Optional[str] = settings.token
        self._sandbox: bool = settings.sandbox

    def credentials_set(self) -> bool:
        return bool(self._token)

    async def set_credentials(self, *, token: str, sandbox: bool) -> None:
        # Swap client instance (best-effort close previous)
        self._token = token
        self._sandbox = sandbox
        if self._inner is not None:
            await self._inner.aclose()
        self._inner = TinkoffClient(token=token, sandbox=sandbox)

    def _ensure(self) -> TinkoffClient:
        if self._inner is None:
            if not self._token:
                raise RuntimeError("TOKEN is not set. Provide it via .env or UI.")
            self._inner = TinkoffClient(token=self._token, sandbox=self._sandbox)
        return self._inner

    async def ainit(self):
        await self._ensure().ainit()

    async def aclose(self):
        if self._inner is not None:
            await self._inner.aclose()
        self._inner = None

    # Delegate API
    async def get_orders(self, **kwargs):
        return await self._ensure().get_orders(**kwargs)

    async def get_portfolio(self, **kwargs):
        return await self._ensure().get_portfolio(**kwargs)

    async def get_accounts(self):
        return await self._ensure().get_accounts()

    async def get_all_candles(self, **kwargs):
        async for c in self._ensure().get_all_candles(**kwargs):
            yield c

    async def get_last_prices(self, **kwargs) -> GetLastPricesResponse:
        return await self._ensure().get_last_prices(**kwargs)

    async def post_order(self, **kwargs) -> PostOrderResponse:
        return await self._ensure().post_order(**kwargs)

    async def get_order_state(self, **kwargs) -> OrderState:
        return await self._ensure().get_order_state(**kwargs)

    async def get_trading_status(self, **kwargs) -> GetTradingStatusResponse:
        return await self._ensure().get_trading_status(**kwargs)

    async def get_instrument(self, **kwargs) -> InstrumentResponse:
        return await self._ensure().get_instrument(**kwargs)


client = RuntimeClient()
