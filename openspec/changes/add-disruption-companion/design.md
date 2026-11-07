# Design

## Context
Six tools with different reliability contracts (see `project.md`). The statement leaves
open what counts as a modification, what to show, when to automate, when to escalate and how
to treat contradictory sources. This document records those decisions.

## Goals / Non-Goals
- Goals: correct and explainable decisions; safe failure modes; small-team operability.
- Non-goals: automatic booking changes, UI, ML ranking.

## Decisions

### D1. Deterministic core, AI only for phrasing
All eligibility, policy and escalation logic is code. An optional `LLMComposer` may rephrase
the message; its output is accepted only if `validate_message` passes, else the template is used.
- Alternative rejected: LLM decides entitlements. Non-reproducible, hard to audit, costly.

### D2. Source reconciliation by trust, then recency
`OPS_CORE (3) > AIRPORT_FEED (2) > LEGACY_MIRROR (1)`. Degraded and stale (>30 min) events are
discarded and logged. `STATUS=ON_TIME` outranking a `DELAY` is an unresolved conflict -> human.
Algorithm: one pass over events with a hash map keyed by `(segment, type)`; O(E).

### D3. Connection impact by linear propagation
Each segment shifts by its own delay; each connection is rechecked against the minimum
connection time (45 min, configurable). O(S log S). Local clock times are used as provided.

### D4. Rule engine with three-valued logic, fail-closed
Rules are data (`condition` strings), parsed by a small grammar, never `eval`. Missing facts
give UNKNOWN; `False AND UNKNOWN = False`, `True AND UNKNOWN = UNKNOWN`. Unknown rules never
grant anything and force escalation. A policy that is not yet effective or is superseded grants nothing.

### D5. Option filtering then top-k with a heap
Filters: freshness (TTL), validated `policy_ref`, entitlement, seats >= party size, route and
earliest feasible time, confidence, refundability. All reasons are kept. Ranking with
`heapq.nsmallest`, O(n log k). Fare difference > 0 -> needs human, never offered.

### D6. Decision gate (pure function)
| Condition | Decision |
|---|---|
| Events source unavailable / policy unavailable | `DEGRADED_SAFE` |
| No actionable disruption | `INFORM_ONLY` |
| Unresolved conflict, invalid policy, unevaluable rule, inconsistent data, any rule with human approval (minor, cash compensation), no eligible option, no adult contact | `ESCALATE_HUMAN` |
| Rebooking entitlement and at least one eligible option | `PROPOSE_OPTIONS` |

The system never creates a rebooking by itself: the passenger confirms, and confirmation
re-validates everything with fresh data.

### D7. Writes
- Rebooking: key = SHA-256(booking, option, policy version, event ids). 409 = success (reconcile).
  503 = indeterminate -> retry with the same key (safe), then `INDETERMINATE` + human case.
- Human case / notification: local dedupe key, bounded retries with jittered backoff.
- Notifications pass a token bucket; excess is deferred to an outbox.

### D8. Privacy
Cases carry ids only. Messages use no names or contacts (the notification service resolves the
recipient from `recipient_passenger_id`). Messages only contain facts traceable to sources.

## Risks / Trade-offs
- In-memory dedupe is lost on restart -> acceptable for v1; production needs Redis/DB.
- Escalating every party with a minor is conservative by policy (R5) and may raise human load.
- No notification is sent when facts are unvalidated: we prefer silence over a wrong message.

## Open Questions
- Should the minimum connection time vary by airport?
- Which team owns the `DISRUPTION_L2` queue SLA breaches?
