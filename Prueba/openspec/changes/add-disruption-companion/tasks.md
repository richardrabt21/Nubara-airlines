# Tasks

## 1. Foundations
- [x] 1.1 Pydantic contracts for the four read tools
- [x] 1.2 Mock systems with failure injection (409, 503 indeterminate, 422, degraded sources)
- [x] 1.3 Clock, settings, privacy helpers, resilience helpers (backoff, token bucket)

## 2. Core pipeline
- [x] 2.1 Event reconciliation (trust ranking, degraded/stale discard, conflict detection)
- [x] 2.2 Impact analysis (delay propagation, missed connection, international, minor)
- [x] 2.3 Policy engine (grammar, three-valued logic, validity checks)
- [x] 2.4 Option filtering and top-k ranking
- [x] 2.5 Decision gate

## 3. Actions and messaging
- [x] 3.1 Idempotent rebooking with reconciliation and human fallback
- [x] 3.2 Case/notification dedupe, retries, rate limiting
- [x] 3.3 Message composers (template, optional LLM) and deterministic validator

## 4. Delivery
- [x] 4.1 FastAPI endpoints `assess` and `confirm`
- [x] 4.2 Tests (pipeline, units, API)
- [x] 4.3 Dockerfile (runtime/test targets) and docker-compose
- [x] 4.4 README and AI usage note
- [ ] 4.5 Run `openspec validate add-disruption-companion --strict` and `docker compose --profile test run --rm tests` (needs Node and Docker)
- [ ] 4.6 `openspec archive add-disruption-companion --yes` after validation
