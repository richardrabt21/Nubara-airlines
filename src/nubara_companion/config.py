from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    # Business thresholds
    min_connection_minutes: int = 45
    event_max_age_minutes: int = 30
    min_actionable_delay_minutes: int = 30
    top_k_options: int = 2
    # Source trust: higher wins when sources disagree
    source_priority: dict[str, int] = field(
        default_factory=lambda: {"OPS_CORE": 3, "AIRPORT_FEED": 2, "LEGACY_MIRROR": 1}
    )
    # Resilience
    retrieval_timeout_seconds: float = 2.0
    read_attempts: int = 2
    write_attempts: int = 3
    backoff_base_seconds: float = 0.2
    # Notification rate limiting (token bucket)
    notify_bucket_capacity: int = 5
    notify_refill_per_second: float = 1.0
    # Airport -> country, used to decide "international"
    airport_country: dict[str, str] = field(
        default_factory=lambda: {"SCL": "CL", "LIM": "PE", "MEX": "MX", "BOG": "CO", "EZE": "AR"}
    )
