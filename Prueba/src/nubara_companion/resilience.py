from __future__ import annotations

import random
from collections.abc import Iterator

from .clock import Clock


def backoff_delays(waits: int, base: float, rng: random.Random | None = None) -> Iterator[float]:
    """Exponential backoff with full jitter: U(0, base * 2^i) for each wait."""
    rng = rng or random.Random()
    for i in range(max(waits, 0)):
        yield rng.uniform(0, base * (2**i))


class TokenBucket:
    """Classic token bucket (course: rate limiting). O(1) per call."""

    def __init__(self, capacity: int, refill_per_second: float, clock: Clock) -> None:
        self._capacity = float(capacity)
        self._rate = refill_per_second
        self._clock = clock
        self._tokens = float(capacity)
        self._last = clock.now()

    def try_take(self) -> bool:
        now = self._clock.now()
        elapsed = (now - self._last).total_seconds()
        self._last = now
        self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
        if self._tokens >= 1:
            self._tokens -= 1
            return True
        return False
