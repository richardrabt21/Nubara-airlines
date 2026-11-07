"""End-to-end demo over the mock systems (no server needed).

    python scripts/demo.py
"""
import json
import random
from datetime import datetime, timezone

from nubara_companion.adapters.mock_systems import MockSystems
from nubara_companion.clock import FixedClock
from nubara_companion.service import DisruptionService

clock = FixedClock(datetime(2026, 9, 15, 11, 2, 35, tzinfo=timezone.utc))
svc = DisruptionService(MockSystems(clock), clock, sleep=lambda s: None, rng=random.Random(7))


def show(title: str, payload: dict) -> None:
    print(f"\n=== {title} ===")
    print(json.dumps(payload, indent=2, default=str))


r = svc.assess("ABC123")
show("ABC123 (party with a minor) -> decision", {
    "decision": r["decision"], "reasons": r["decision_reasons"], "what_happened": r["what_happened"],
    "what_it_implies": r["what_it_implies"], "offered": [o["option_id"] for o in r["options"]["offered"]],
    "rejected": r["options"]["rejected"], "actions": {k: (v and {x: v[x] for x in v if x != "body"})
                                                       for k, v in r["actions"].items()}})
print("\nPassenger message:\n", r["actions"]["notification"]["body"])

r = svc.assess("ABC456")
show("ABC456 (adults only) -> decision", {"decision": r["decision"], "offered": r["options"]["offered"]})
print("\nPassenger message:\n", r["actions"]["notification"]["body"])
show("ABC456 passenger confirms O1", svc.confirm_rebooking("ABC456", "O1"))
