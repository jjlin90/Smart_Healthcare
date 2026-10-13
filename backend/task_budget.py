"""One signed wall-clock deadline shared across the request's services."""
import math
import time
from typing import Any

from backend.service_auth import request_context


def remaining_seconds(limit: float, context: dict[str, Any] | None = None) -> float:
    """Legacy calls use the stage limit; delegated calls cannot reset the clock.

    Servers must synchronize their clocks. Expiry prevents new work and bounds
    local waits, but does not prove a previously submitted HIS write was undone.
    """
    scope = context if context is not None else request_context.get() or {}
    deadline = scope.get("deadline_at")
    if deadline is None:
        return limit
    if type(deadline) not in (int, float) or not math.isfinite(deadline):
        raise ValueError("Invalid task deadline")
    remaining = deadline - time.time()
    if remaining <= 0:
        raise TimeoutError("Task budget exhausted")
    return min(limit, remaining)
