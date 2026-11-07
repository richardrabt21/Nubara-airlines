"""End-to-end behaviour of the vertical slice over the exam's sample data."""
import json

import pytest

from nubara_companion.config import Settings
from nubara_companion.errors import BookingNotFound, ConfirmationRefused


# ---------------------------------------------------------------- base case --
def test_sample_booking_with_minor_escalates_with_full_explanation(service, systems):
    r = service.assess("ABC123")

    assert r["decision"] == "ESCALATE_HUMAN"
    assert any(x.startswith("human_approval_required:R5") for x in r["decision_reasons"])
    # What happened / implies
    assert r["impact"]["max_delay_minutes"] == 160
    assert r["impact"]["missed_connection"] is True
    conn = r["impact"]["connections"][0]
    assert (conn["slack_before_minutes"], conn["slack_after_minutes"]) == (70, -90)
    # Policy: 160 min is below R1 (>=180), but the missed connection triggers R3; minor triggers R5
    assert {e["rule_id"] for e in r["entitlements"]} == {"R3", "R5"}
    # Contradicting LEGACY_MIRROR event is discarded and logged, not trusted
    assert r["data_quality"]["discarded_events"] == [
        {"event_id": "E-1012", "source": "LEGACY_MIRROR", "reason": "degraded_source"}]
    assert r["data_quality"]["conflicts"][0]["resolved"] is True
    # Options: O1 recommended; O2 lacks seats for 2 pax; O3 refund is invalid for a non-refundable fare
    assert [o["option_id"] for o in r["options"]["offered"]] == ["O1"]
    rejected = {o["option_id"]: o["reasons"] for o in r["options"]["rejected"]}
    assert "insufficient_seats:have_1_need_2" in rejected["O2"]
    assert {"fare_not_refundable", "low_confidence"} <= set(rejected["O3"])
    # Actions: a human case with SLA, a validated passenger message, and NO rebooking
    assert r["actions"]["human_case"]["status"] == "CREATED"
    assert r["actions"]["notification"]["status"] == "QUEUED"
    assert "create_rebooking_request" not in systems.calls


def test_adults_only_booking_proposes_options_without_acting(service, systems):
    r = service.assess("ABC456")

    assert r["decision"] == "PROPOSE_OPTIONS"
    assert r["actions"]["human_case"] is None
    body = systems.notifications[0]["body"]
    assert "Option O1" in body and "POL-DELAY-2026-03" in body
    assert "create_rebooking_request" not in systems.calls  # nothing changes until the passenger confirms


def test_unknown_booking_raises(service):
    with pytest.raises(BookingNotFound):
        service.assess("ZZZ000")


# ------------------------------------------------------- data-quality paths --
def test_unresolved_source_conflict_escalates_and_sends_no_claims(make_service, systems):
    systems.events_degraded_sources = []  # legacy feed now looks healthy...
    svc = make_service(Settings(source_priority={"OPS_CORE": 3, "AIRPORT_FEED": 2, "LEGACY_MIRROR": 5}))
    r = svc.assess("ABC456")  # ...and outranks OPS_CORE: genuinely contradictory

    assert r["decision"] == "ESCALATE_HUMAN"
    assert "sources_conflict_unresolved" in r["decision_reasons"]
    assert r["actions"]["notification"] is None  # unvalidated facts are never sent
    assert r["actions"]["human_case"]["status"] == "CREATED"


def test_events_source_down_is_degraded_safe(service, systems):
    systems.fail_events = True
    r = service.assess("ABC123")

    assert r["decision"] == "DEGRADED_SAFE"
    assert r["actions"]["notification"] is None
    assert r["actions"]["human_case"]["status"] == "CREATED"


def test_superseded_policy_grants_nothing(service, systems):
    systems.policy_version = "POL-DELAY-2025-11"  # listed as superseded
    r = service.assess("ABC456")

    assert r["entitlements"] == []
    assert r["decision"] == "ESCALATE_HUMAN"
    assert "policy_superseded:POL-DELAY-2025-11" in r["decision_reasons"]
    assert r["actions"]["notification"] is None


def test_stale_options_are_not_offered(systems, make_service):
    systems.ttl_seconds = -1  # always expired, even after the re-fetch
    r = make_service().assess("ABC456")

    assert r["options"]["offered"] == []
    assert "no_eligible_options" in r["decision_reasons"]
    assert all("options_stale" in o["reasons"] for o in r["options"]["rejected"])


# --------------------------------------------------------------- idempotency --
def test_repeated_assess_does_not_duplicate_case_or_notification(service, systems):
    service.assess("ABC123")
    second = service.assess("ABC123")

    assert len(systems.cases) == 1 and len(systems.notifications) == 1
    assert second["actions"]["human_case"]["deduplicated"] is True


def test_confirm_creates_request_and_is_idempotent(service):
    first = service.confirm_rebooking("ABC456", "O1")
    again = service.confirm_rebooking("ABC456", "O1")

    assert first["rebooking"]["status"] == "CREATED"
    assert again["rebooking"]["status"] == "DUPLICATE_RECONCILED"
    assert again["rebooking"]["request_id"] == first["rebooking"]["request_id"]


def test_timeout_after_commit_reconciles_via_same_key(service, systems):
    systems.rebooking_script = ["timeout_committed"]  # 503 although the request was stored
    out = service.confirm_rebooking("ABC456", "O1")

    assert out["rebooking"]["status"] == "DUPLICATE_RECONCILED"
    assert len(systems._rebookings) == 1  # exactly one request exists


def test_persistent_timeout_is_indeterminate_and_escalated(service, systems):
    systems.rebooking_script = ["timeout_lost"] * 3
    out = service.confirm_rebooking("ABC456", "O1")

    assert out["rebooking"]["status"] == "INDETERMINATE"
    assert out["human_case"]["status"] == "CREATED"


def test_confirm_refused_when_case_needs_a_human_or_option_not_offered(service):
    with pytest.raises(ConfirmationRefused):
        service.confirm_rebooking("ABC123", "O1")  # minor -> human approval
    with pytest.raises(ConfirmationRefused):
        service.confirm_rebooking("ABC456", "O2")  # not eligible


def test_notification_rate_limit_defers_excess(make_service, systems):
    svc = make_service(Settings(notify_bucket_capacity=1, notify_refill_per_second=0))
    svc.assess("ABC123")
    second = svc.assess("ABC456")

    assert second["actions"]["notification"]["status"] == "DEFERRED"
    assert len(svc.executor.outbox) == 1


def test_case_creation_retries_then_succeeds(service, systems):
    systems.case_failures = 2
    r = service.assess("ABC123")

    assert r["actions"]["human_case"]["status"] == "CREATED"


# ---------------------------------------------------------------- privacy ----
def test_no_pii_in_response_cases_or_notifications(service, systems):
    r = service.assess("ABC123")
    blob = json.dumps([r, systems.cases, systems.notifications])
    for secret in ("maria.silva@example.com", "+56 9 8765 4321", "12.345.678-9", "Maria", "Tomas"):
        assert secret not in blob
