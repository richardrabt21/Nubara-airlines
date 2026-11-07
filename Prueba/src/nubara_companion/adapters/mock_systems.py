"""In-memory mock of the six platform tools, reproducing the sample payloads of the
exam (including its traps) and offering switches to inject failures.

Bookings:
  ABC123  exam sample: family with a minor (P2, age 7)  -> escalation path
  ABC456  same itinerary, adults only                    -> propose-options path
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta
from typing import Any

from ..clock import Clock
from ..errors import BookingNotFound, SourceUnavailable
from ..ports import Response


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


_SEGMENTS = [
    {"segment_id": "S1", "origin": "SCL", "destination": "LIM", "date": "2026-09-15",
     "scheduled_departure": "14:30", "scheduled_arrival": "17:55", "flight_number": "NB500"},
    {"segment_id": "S2", "origin": "LIM", "destination": "MEX", "date": "2026-09-15",
     "scheduled_departure": "19:05", "scheduled_arrival": "23:40", "flight_number": "NB2410"},
]
_ADULT = {"passenger_id": "P1", "name": "Maria Silva", "type": "ADULT",
          "email": "maria.silva@example.com", "phone": "+56 9 8765 4321",
          "document_id": "12.345.678-9", "loyalty_tier": "GOLD"}
_CHILD = {"passenger_id": "P2", "name": "Tomas Silva", "type": "CHILD", "age": 7}
_ADULT2 = {"passenger_id": "P2", "name": "Jorge Silva", "type": "ADULT", "age": 41,
           "email": "jorge.silva@example.com"}


class MockSystems:
    def __init__(self, clock: Clock, *, ttl_seconds: int = 120) -> None:
        self.clock = clock
        self.ttl_seconds = ttl_seconds
        # ---- failure injection --------------------------------------------
        self.fail_events = False
        self.fail_policy = False
        self.fail_options = False
        self.events_degraded_sources = ["LEGACY_MIRROR"]
        self.include_legacy_conflict = True
        self.policy_effective_from = "2026-03-01"
        self.policy_version = "POL-DELAY-2026-03"
        self.policy_superseded = ["POL-DELAY-2025-11"]
        self.rebooking_script: list[str] = []   # "ok" | "timeout_lost" | "timeout_committed"
        self.case_failures = 0
        self.notify_failures = 0
        # ---- observable state ----------------------------------------------
        self._rebookings: dict[str, str] = {}
        self.cases: list[dict[str, Any]] = []
        self.notifications: list[dict[str, Any]] = []
        self.calls: list[str] = []

    # ------------------------------------------------------------------ reads
    def get_trip_context(self, booking_id: str) -> dict[str, Any]:
        self.calls.append("get_trip_context")
        if booking_id not in {"ABC123", "ABC456"}:
            raise BookingNotFound(booking_id)
        now = self.clock.now()
        second = _CHILD if booking_id == "ABC123" else _ADULT2
        return {
            "booking_id": booking_id,
            "passengers": [copy.deepcopy(_ADULT), copy.deepcopy(second)],
            "segments": copy.deepcopy(_SEGMENTS),
            "fare_conditions": {"changeable": True, "refundable": False, "fare_family": "LIGHT"},
            "meta": {"retrieved_at": _iso(now - timedelta(seconds=4)), "completeness": "PARTIAL",
                     "missing_fields": ["fare_conditions.change_penalty_amount"]},
        }

    def get_operational_events(self, segment_ids: list[str]) -> dict[str, Any]:
        self.calls.append("get_operational_events")
        if self.fail_events:
            raise SourceUnavailable("events")
        now = self.clock.now()
        ago = lambda m: _iso(now - timedelta(minutes=m))  # noqa: E731
        events = [
            {"event_id": "E-1001", "segment_id": "S1", "type": "DELAY", "delay_minutes": 160,
             "new_departure": "17:10", "source": "OPS_CORE", "recorded_at": ago(14)},
            {"event_id": "E-1007", "segment_id": "S1", "type": "GATE_CHANGE", "gate": "24",
             "source": "AIRPORT_FEED", "recorded_at": ago(7)},
            {"event_id": "E-2003", "segment_id": "S2", "type": "STATUS", "status": "SCHEDULED",
             "source": "OPS_CORE", "recorded_at": ago(14)},
        ]
        if self.include_legacy_conflict:
            events.append({"event_id": "E-1012", "segment_id": "S1", "type": "STATUS",
                           "status": "ON_TIME", "source": "LEGACY_MIRROR", "recorded_at": ago(14)})
        return {
            "events": [e for e in events if e["segment_id"] in segment_ids],
            "meta": {"retrieved_at": _iso(now - timedelta(seconds=2)),
                     "sources_available": ["OPS_CORE", "AIRPORT_FEED", "LEGACY_MIRROR"],
                     "sources_degraded": list(self.events_degraded_sources)},
        }

    def get_eligible_options(self, trip_context: dict[str, Any]) -> dict[str, Any]:
        self.calls.append("get_eligible_options")
        if self.fail_options:
            raise SourceUnavailable("options")
        now = self.clock.now()
        return {
            "options": [
                {"option_id": "O1", "type": "REBOOK", "flight_number": "NB2412", "origin": "LIM",
                 "destination": "MEX", "date": "2026-09-16", "departure": "08:15",
                 "fare_difference": 0, "seats_available": 4, "policy_ref": "POL-DELAY-2026-03",
                 "confidence": "HIGH"},
                {"option_id": "O2", "type": "REBOOK", "flight_number": "NB2450", "origin": "LIM",
                 "destination": "MEX", "date": "2026-09-15", "departure": "23:40",
                 "fare_difference": 180000, "currency": "CLP", "seats_available": 1,
                 "policy_ref": "POL-DELAY-2026-03", "confidence": "MEDIUM"},
                {"option_id": "O3", "type": "REFUND", "policy_ref": "POL-REFUND-2026-01",
                 "confidence": "LOW"},
            ],
            "meta": {"retrieved_at": _iso(now), "ttl_seconds": self.ttl_seconds},
        }

    def get_policy_rules(self, scenario: str) -> dict[str, Any]:
        self.calls.append("get_policy_rules")
        if self.fail_policy or scenario != "DELAY":
            raise SourceUnavailable("policy")
        return {
            "scenario": "DELAY", "version": self.policy_version,
            "effective_from": self.policy_effective_from,
            "rules": [
                {"rule_id": "R1", "condition": "delay_minutes >= 180", "entitlement": "REBOOK_NO_FEE",
                 "requires_human_approval": False},
                {"rule_id": "R2", "condition": "delay_minutes >= 240 AND international",
                 "entitlement": "MEAL_VOUCHER", "requires_human_approval": False},
                {"rule_id": "R3", "condition": "missed_connection == true",
                 "entitlement": "REBOOK_NO_FEE", "requires_human_approval": False},
                {"rule_id": "R4", "condition": "compensation_requested == true",
                 "entitlement": "CASH_COMPENSATION", "requires_human_approval": True},
                {"rule_id": "R5", "condition": "group_includes_minor == true",
                 "entitlement": "PRIORITY_HANDLING", "requires_human_approval": True},
            ],
            "meta": {"retrieved_at": _iso(self.clock.now()),
                     "superseded_versions": list(self.policy_superseded)},
        }

    # ----------------------------------------------------------------- writes
    def create_rebooking_request(self, option: dict[str, Any], idempotency_key: str) -> Response:
        self.calls.append("create_rebooking_request")
        step = self.rebooking_script.pop(0) if self.rebooking_script else "ok"
        if step == "timeout_lost":
            return 503, {"error": "UPSTREAM_TIMEOUT",
                         "detail": "actual result is indeterminate"}
        if idempotency_key in self._rebookings:
            return 409, {"error": "DUPLICATE_REQUEST", "request_id": self._rebookings[idempotency_key]}
        request_id = f"RBK-{88231 + len(self._rebookings)}"
        self._rebookings[idempotency_key] = request_id
        if step == "timeout_committed":
            return 503, {"error": "UPSTREAM_TIMEOUT",
                         "detail": "actual result is indeterminate"}
        return 201, {"request_id": request_id, "status": "PENDING_CONFIRMATION",
                     "expires_at": _iso(self.clock.now() + timedelta(minutes=30))}

    def create_human_case(self, reason: str, evidence: dict[str, Any]) -> Response:
        self.calls.append("create_human_case")
        if self.case_failures > 0:
            self.case_failures -= 1
            return 503, {"error": "UNAVAILABLE"}
        case = {"case_id": f"HC-{4471 + len(self.cases)}", "queue": "DISRUPTION_L2",
                "sla_minutes": 45, "reason": reason, "evidence": evidence}
        self.cases.append(case)
        return 201, {k: case[k] for k in ("case_id", "queue", "sla_minutes")}

    def notify_passenger(self, message: dict[str, Any]) -> Response:
        self.calls.append("notify_passenger")
        if self.notify_failures > 0:
            self.notify_failures -= 1
            return 503, {"error": "UNAVAILABLE"}
        if not message.get("policy_ref") or not message.get("body"):
            return 422, {"error": "UNVALIDATED_CONTENT", "detail": "missing policy_ref"}
        self.notifications.append(message)
        return 202, {"notification_id": f"NT-{9912 + len(self.notifications) - 1}",
                     "channel": message.get("channel", "PUSH"), "status": "QUEUED"}
