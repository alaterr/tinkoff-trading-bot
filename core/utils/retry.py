from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional, TypeVar


T = TypeVar("T")


@dataclass(frozen=True)
class Backoff:
    base_seconds: float = 0.5
    factor: float = 2.0
    max_seconds: float = 30.0
    jitter: float = 0.1  # +-10%

    def delay(self, attempt: int) -> float:
        raw = min(self.max_seconds, self.base_seconds * (self.factor ** max(0, attempt - 1)))
        if self.jitter <= 0:
            return raw
        j = raw * self.jitter
        return max(0.0, raw + random.uniform(-j, j))


async def retry_async(
    fn: Callable[[], Awaitable[T]],
    *,
    attempts: int = 5,
    backoff: Backoff = Backoff(),
    is_retryable: Optional[Callable[[BaseException], bool]] = None,
) -> T:
    last_exc: Optional[BaseException] = None
    for attempt in range(1, attempts + 1):
        try:
            return await fn()
        except BaseException as e:  # noqa: BLE001 - we re-raise at end
            last_exc = e
            if is_retryable is not None and not is_retryable(e):
                raise
            if attempt >= attempts:
                break
            await asyncio.sleep(backoff.delay(attempt))
    assert last_exc is not None
    raise last_exc

