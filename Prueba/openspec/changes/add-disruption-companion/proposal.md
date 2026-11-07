# Change: Add Disruption Companion vertical slice

## Why
When a flight is disrupted, call-center contacts spike because passengers cannot tell what
happened or what their next step is. Information is spread across systems that may be stale,
degraded or contradictory, and acting on it wrongly (rebooking, compensation) is costly.

## What Changes
- Add an assessment pipeline: retrieve -> reconcile events -> impact -> policy -> options -> decision gate -> actions -> message.
- Add a decision gate with four outcomes: `INFORM_ONLY`, `PROPOSE_OPTIONS`, `ESCALATE_HUMAN`, `DEGRADED_SAFE`.
- Add passenger-confirmed rebooking with idempotency, reconciliation of indeterminate results, and human fallback.
- Add a validated passenger message with an optional AI-assisted composer and a deterministic fallback.
- Expose `POST /v1/disruptions/{booking_id}/assess` and `POST /v1/disruptions/{booking_id}/confirm`.

## Impact
- Affected specs: `disruption-assessment` (new)
- Affected code: `src/nubara_companion/**`, `tests/**`, Docker files
- Non-goals: UI, real integrations (mocks only), multi-scenario policies beyond `DELAY`.
