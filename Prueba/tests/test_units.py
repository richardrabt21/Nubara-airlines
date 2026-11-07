"""Unit tests for the pure building blocks."""
import pytest
from fastapi.testclient import TestClient

from nubara_companion.api import create_app
from nubara_companion.core.messaging import LLMComposer, MessageFacts, TemplateComposer, compose_validated, validate_message
from nubara_companion.core.policy import eval_condition
from nubara_companion.models import Passenger
from nubara_companion.resilience import TokenBucket, backoff_delays

PAX = [Passenger(passenger_id="P1", name="Maria Silva", type="ADULT", email="m@x.com")]
FACTS = MessageFacts(
    decision="ESCALATE_HUMAN", policy_ref="POL-DELAY-2026-03", as_of="2026-09-15T11:02:35Z",
    delayed=[{"flight": "NB500", "route": "SCL→LIM", "date": "2026-09-15", "delay": "160",
              "new_departure": "17:10"}])


# ----------------------------------------------------------- policy engine ---
@pytest.mark.parametrize("cond,facts,expected", [
    ("delay_minutes >= 180", {"delay_minutes": 160}, False),
    ("delay_minutes >= 180", {"delay_minutes": 180}, True),
    ("missed_connection == true", {"missed_connection": True}, True),
    ("delay_minutes >= 240 AND international", {"delay_minutes": 100, "international": None}, False),  # False AND ? = False
    ("delay_minutes >= 240 AND international", {"delay_minutes": 300, "international": None}, None),   # True AND ? = unknown
    ("group_includes_minor == true", {}, None),
])
def test_three_valued_rule_evaluation(cond, facts, expected):
    assert eval_condition(cond, facts) is expected


def test_malformed_rule_is_rejected_not_evaluated():
    from nubara_companion.core.policy import RuleSyntaxError
    with pytest.raises(RuleSyntaxError):
        eval_condition("__import__('os').system('x')", {})


# ------------------------------------------------------- message validation --
def test_template_message_passes_validation():
    body = TemplateComposer().compose(FACTS)
    assert validate_message(body, FACTS, PAX) == []


def test_hallucinating_llm_falls_back_to_template():
    bad = LLMComposer(lambda f: f"Your flight is delayed 999 minutes, refund guaranteed. {f['policy_ref']}")
    body, composer, rejected = compose_validated([bad], FACTS, PAX)

    assert composer == "template"
    assert "unverified_numeric_content" in rejected[0] and "forbidden_promise:refund" in rejected[0]
    assert "160" in body


def test_crashing_llm_falls_back_to_template():
    def boom(_):
        raise TimeoutError

    _, composer, rejected = compose_validated([LLMComposer(boom)], FACTS, PAX)
    assert composer == "template" and rejected[0].startswith("llm:error")


def test_pii_in_message_is_detected():
    issues = validate_message("Hi Maria Silva, see POL-DELAY-2026-03", FACTS, PAX)
    assert any(i.startswith("pii_leak") for i in issues)


# -------------------------------------------------------------- resilience ---
def test_token_bucket_refills_over_time(clock):
    bucket = TokenBucket(2, 1.0, clock)
    assert [bucket.try_take() for _ in range(3)] == [True, True, False]
    clock.advance(1.5)
    assert bucket.try_take() is True


def test_backoff_is_bounded_and_exponential():
    import random
    delays = list(backoff_delays(3, 0.2, random.Random(0)))
    assert len(delays) == 3 and all(d <= 0.2 * 2**i for i, d in enumerate(delays))


# --------------------------------------------------------------------- API ---
def test_api_contract(service):
    client = TestClient(create_app(service))
    assert client.get("/health").json() == {"status": "ok"}
    assert client.post("/v1/disruptions/ABC123/assess", json={"dry_run": True}).json()["decision"] == "ESCALATE_HUMAN"
    assert client.post("/v1/disruptions/ZZZ000/assess").status_code == 404
    assert client.post("/v1/disruptions/bad-id/assess").status_code == 422
    assert client.post("/v1/disruptions/ABC123/confirm", json={"option_id": "O1"}).status_code == 409
    assert client.post("/v1/disruptions/ABC456/confirm", json={"option_id": "O1"}).status_code == 200
