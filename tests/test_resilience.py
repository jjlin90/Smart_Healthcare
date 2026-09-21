import asyncio

import pytest

from backend.resilience import AsyncCircuitBreaker, CircuitOpenError


@pytest.mark.asyncio
async def test_circuit_breaker_opens_and_recovers():
    breaker = AsyncCircuitBreaker("test", failure_threshold=2, recovery_seconds=0)

    async def fail():
        raise RuntimeError("downstream failed")

    for _ in range(2):
        with pytest.raises(RuntimeError):
            await breaker.call(fail)
    assert breaker.opened_at is not None

    async def succeed():
        return "ok"

    assert await breaker.call(succeed) == "ok"
    assert breaker.opened_at is None


@pytest.mark.asyncio
async def test_open_circuit_rejects_before_recovery_window():
    breaker = AsyncCircuitBreaker("test", failure_threshold=1, recovery_seconds=60)

    async def fail():
        raise RuntimeError("downstream failed")

    with pytest.raises(RuntimeError):
        await breaker.call(fail)
    with pytest.raises(CircuitOpenError):
        await breaker.call(fail)


@pytest.mark.asyncio
async def test_cancelled_half_open_probe_releases_probe_lease():
    breaker = AsyncCircuitBreaker("cancel", failure_threshold=1, recovery_seconds=0)

    async def fail():
        raise RuntimeError("downstream failed")

    with pytest.raises(RuntimeError):
        await breaker.call(fail)

    async def cancelled():
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await breaker.call(cancelled)
    assert breaker._half_open_probe is False

    async def succeed():
        return "recovered"

    assert await breaker.call(succeed) == "recovered"
