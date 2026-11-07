# Nubara Disruption Companion — Engineering README

Backend vertical slice that, for a booking, explains **what happened**, **what it implies** and
**what the passenger may be offered**, escalating to a person when it must. No UI.

## 1. Problem understood and expected outcome
Passengers of disrupted flights call the contact center because information is scattered and
partly wrong. The slice combines booking, operational events, policies and options into one
explainable decision, **never exposing unverified information** and **never changing a booking
without passenger confirmation**. Expected outcome for the exam sample (`ABC123`, SCL→LIM→MEX,
S1 delayed 160 min, party with a 7-year-old): the passenger is told about the delay and the
missed connection, a human case is opened (SLA 45 min) with a recommended option, and nothing
is modified.

## 2. How to run
```bash
docker compose --profile test run --rm tests      # test suite in a container
docker compose up --build api                     # API on :8000 (clock pinned to the sample date)
curl -X POST localhost:8000/v1/disruptions/ABC123/assess
curl -X POST localhost:8000/v1/disruptions/ABC456/confirm -H 'content-type: application/json' -d '{"option_id":"O1"}'
```
Without Docker: `pip install -e ".[dev]" && pytest -q && python scripts/demo.py`.
Mock bookings: `ABC123` (minor → escalation), `ABC456` (adults only → options proposed), anything else → 404.
`assess` accepts `{"dry_run": true}` to evaluate without side effects.

## 3. Architecture and algorithms
Pipeline in `service.py`; pure logic in `core/`, I/O behind `ports.py` (hexagonal).

1. **Retrieve** — trip, then events, then policy ∥ options (thread pool, timeout, bounded retries, contract validation with Pydantic).
2. **Reconcile** — hash map keyed `(segment, type)` keeping best `(source priority, recorded_at)`; degraded/stale events discarded. O(E).
3. **Impact** — delay propagation along the segment chain, connection re-check (min 45 min). O(S log S).
4. **Policy** — rules as data, grammar-based, three-valued logic, fail-closed. O(R).
5. **Options** — filter (freshness, policy ref, entitlement, seats ≥ party, route/time, confidence, refundability), rank with `heapq.nsmallest`. O(n log k).
6. **Decision gate** — pure function → `INFORM_ONLY | PROPOSE_OPTIONS | ESCALATE_HUMAN | DEGRADED_SAFE`.
7. **Actions** — idempotent writes (key = SHA-256 of booking, option, policy version, event ids), retry with jittered backoff, token-bucket for notifications, dedupe for cases/notifications.
8. **Message** — template (or optional AI phrasing) → deterministic validator → `notify_passenger`.

## 4. Assumptions, decisions and discarded alternatives
| Open question | Decision |
|---|---|
| What is a "relevant modification"? | Delay ≥ 30 min or a missed connection. A gate change alone is shown but not acted on. |
| What is shown to the passenger? | Only validated facts (flight, delay, new time, missed connection, eligible options) with policy reference and data timestamp. No names, contacts, IDs, confidence scores, rejected options or internal reasons. |
| What is automatic? | Reads, human-case creation and the informational message. Rebooking only after passenger confirmation; refunds and cash compensation never. |
| When to communicate? | Only when facts are validated (no unresolved conflict, valid policy, evaluable rules). Otherwise silence + human case, because `notify_passenger` must not receive unvalidated content. |
| When a person steps in? | Minor in the party (R5), compensation requested (R4), unresolved/contradictory sources, invalid or superseded policy, unevaluable rule, no eligible option, fare difference, indeterminate write. |
| Contradicting sources? | `OPS_CORE > AIRPORT_FEED > LEGACY_MIRROR`; degraded sources discarded; equal-or-higher contradicting source → human. |

Traps in the sample data that the design handles: LEGACY_MIRROR says `ON_TIME` against an
OPS_CORE delay; 160 min is below R1 (≥180) but the missed connection triggers R3; O2 has 1 seat
for 2 passengers and a 180,000 CLP fare difference; O3 is a refund on a non-refundable fare
with an unvalidated policy; options expire after 120 s (re-fetched); the fare's change penalty
is missing (irrelevant under REBOOK_NO_FEE, so never quoted).
Discarded: DP/backtracking (no overlapping subproblems), LLM-based decisions, auto-rebooking,
Kafka/outbox infrastructure (an in-memory deferred outbox is enough for v1).

## 5. Tests and validations
29 tests (`tests/`): the base case end-to-end; adults-only proposal; unresolved conflict;
source down; superseded policy; stale options; idempotent repeats; 503 after commit; persistent
503; confirm refused; rate limit; retries; PII absence; rule grammar (incl. injection attempt);
hallucinating/crashing AI composer; token bucket; API contract (404/422/409/200).
Validated with `pytest`: **29 passed**. Docker image build and `openspec validate` are pending
until Docker/Node are available on the author's machine (see `tasks.md` 4.5).

## 6. Known limitations and next steps
- Mocks only; real tools may differ in error shapes.
- In-memory dedupe/outbox are lost on restart → use Redis/DB and a real queue (Kafka/SQS) with DLQ.
- Times are treated as comparable local times (no timezone model); one min-connection time for all airports.
- Only the `DELAY` scenario; cancellations/diversions need their own policies.
- Metrics (decision mix, escalation rate, stale-option rate) and tracing are not wired.
- Notification of rebooking confirmation to the passenger is not implemented.

## 7. Layout
`openspec/` (project, proposal, design, tasks, spec) · `src/nubara_companion/` (`core/`, `adapters/`, `service.py`, `api.py`) · `tests/` · `scripts/demo.py` · `Dockerfile`, `docker-compose.yml` · `AI_USAGE_NOTE.md`
