"""Stage 7: side effects. Every write is idempotent and retried safely.

- Rebooking: deterministic idempotency key; 409 => already created (success);
  503 (indeterminate) => retry with the SAME key, then INDETERMINATE -> human case.
- Human case / notification: de-duplicated locally by a content key because their
  contracts take no idempotency key (outbox-style at-least-once with dedupe).
- Notifications pass through a token bucket; excess goes to a deferred outbox.
"""
from __future__ import annotations

import hashlib
import logging
import random
import time
from collections.abc import Callable
from typing import Any

from ..clock import Clock
from ..config import Settings
from ..ports import SystemsPort
from ..resilience import TokenBucket, backoff_delays

log = logging.getLogger("nubara.actions")


def make_key(prefix: str, *parts: object) -> str:
    raw = "|".join(str(p) for p in parts)
    return f"{prefix}-{hashlib.sha256(raw.encode()).hexdigest()[:24]}"


class ActionExecutor:
    def __init__(
        self,
        systems: SystemsPort,
        settings: Settings,
        clock: Clock,
        sleep: Callable[[float], None] = time.sleep,
        rng: random.Random | None = None,
    ) -> None:
        self._systems = systems
        self._s = settings
        self._sleep = sleep
        self._rng = rng
        self._bucket = TokenBucket(settings.notify_bucket_capacity, settings.notify_refill_per_second, clock)
        self.cases: dict[str, dict[str, Any]] = {}
        self.sent: dict[str, dict[str, Any]] = {}
        self.outbox: list[dict[str, Any]] = []

    def _attempts(self) -> list[float]:
        return [0.0, *backoff_delays(self._s.write_attempts - 1, self._s.backoff_base_seconds, self._rng)]

    def open_case(self, key: str, reason: str, evidence: dict[str, Any]) -> dict[str, Any]:
        if key in self.cases:
            return {**self.cases[key], "deduplicated": True}
        last: tuple[int, dict] = (0, {})
        for delay in self._attempts():
            if delay:
                self._sleep(delay)
            try:
                last = self._systems.create_human_case(reason, {**evidence, "dedupe_key": key})
            except Exception as exc:  # network-level failure
                log.warning("create_human_case raised %s", type(exc).__name__)
                last = (0, {"error": type(exc).__name__})
                continue
            if last[0] == 201:
                self.cases[key] = {"status": "CREATED", **last[1]}
                return dict(self.cases[key])
            if last[0] < 500:
                break
        return {"status": "FAILED", "http_status": last[0], "detail": last[1]}

    def notify(self, key: str, message: dict[str, Any]) -> dict[str, Any]:
        if key in self.sent:
            return {**self.sent[key], "deduplicated": True}
        if not self._bucket.try_take():
            self.outbox.append(message)
            return {"status": "DEFERRED", "detail": "rate_limited"}
        last: tuple[int, dict] = (0, {})
        for delay in self._attempts():
            if delay:
                self._sleep(delay)
            try:
                last = self._systems.notify_passenger(message)
            except Exception as exc:
                log.warning("notify_passenger raised %s", type(exc).__name__)
                last = (0, {"error": type(exc).__name__})
                continue
            if last[0] == 202:
                self.sent[key] = {"status": last[1].get("status", "QUEUED"), **last[1]}
                return dict(self.sent[key])
            if last[0] < 500:  # 422 etc.: retrying the same content cannot help
                break
        return {"status": "REJECTED" if last[0] == 422 else "FAILED",
                "http_status": last[0], "detail": last[1]}

    def rebook(self, key: str, option: dict[str, Any]) -> dict[str, Any]:
        last: tuple[int, dict] = (0, {})
        for delay in self._attempts():
            if delay:
                self._sleep(delay)
            try:
                last = self._systems.create_rebooking_request(option, key)
            except Exception as exc:
                last = (0, {"error": type(exc).__name__})
                continue
            status, body = last
            if status == 201:
                return {**body, "request_status": body.get("status"), "status": "CREATED", "idempotency_key": key}
            if status == 409:  # same key already processed: reconcile as success
                return {**body, "status": "DUPLICATE_RECONCILED", "idempotency_key": key}
            if status < 500:
                return {"status": "FAILED", "idempotency_key": key, "http_status": status, "detail": body}
        return {"status": "INDETERMINATE", "idempotency_key": key, "http_status": last[0],
                "detail": last[1]}
