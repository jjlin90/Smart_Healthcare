"""Small async circuit breaker used around remote hospital and A2A services."""

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")


class CircuitOpenError(RuntimeError):
    pass


class AsyncCircuitBreaker:
    def __init__(self, name: str, failure_threshold: int = 5, recovery_seconds: float = 30.0) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_seconds = recovery_seconds
        self.failures = 0
        self.opened_at: float | None = None
        self._half_open_probe = False
        self._generation = 0
        self._lock = asyncio.Lock()

    async def call(self, operation: Callable[[], Awaitable[T]], *, is_failure: Callable[[Exception], bool] | None = None) -> T:
        async with self._lock:
            if self.opened_at is not None:
                elapsed = time.monotonic() - self.opened_at
                if elapsed < self.recovery_seconds:
                    raise CircuitOpenError(f"{self.name} 熔断中，请在 {self.recovery_seconds - elapsed:.1f} 秒后重试")
                if self._half_open_probe:
                    raise CircuitOpenError(f"{self.name} 正在半开探测")
                self._half_open_probe = True
            generation = self._generation
            is_probe = self._half_open_probe
        try:
            result = await operation()
        except asyncio.CancelledError:
            # Cancellation bypasses ``except Exception``. Release a half-open
            # probe lease before propagating it so recovery cannot deadlock.
            async with self._lock:
                if is_probe and generation == self._generation:
                    self._half_open_probe = False
            raise
        except Exception as exc:
            async with self._lock:
                if generation == self._generation:
                    if is_failure is not None and not is_failure(exc):
                        if is_probe:
                            self.opened_at = None
                            self.failures = 0
                            self._half_open_probe = False
                            self._generation += 1
                        raise
                    self.failures += 1
                    self._half_open_probe = False
                    if self.failures >= self.failure_threshold:
                        self.opened_at = time.monotonic()
                        self._generation += 1
            raise
        async with self._lock:
            # A slow call begun before opening must not close a newer circuit.
            if generation == self._generation:
                self.failures = 0
                self.opened_at = None
                self._half_open_probe = False
                if is_probe:
                    self._generation += 1
        return result


_breakers: dict[str, AsyncCircuitBreaker] = {}


def get_breaker(name: str) -> AsyncCircuitBreaker:
    if name not in _breakers:
        _breakers[name] = AsyncCircuitBreaker(name)
    return _breakers[name]
