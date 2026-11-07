# Project Context

## Purpose
Disruption Companion for Nubara Airlines: given a booking, explain what happened to the
trip, what it implies, and what the passenger may be offered — without exposing unverified
information and without executing high-impact actions on its own.

## Tech Stack
- Python 3.12, FastAPI, Pydantic v2, pytest
- Docker (multi-stage: `runtime`, `test`) + docker-compose
- OpenSpec for spec-driven changes (`openspec/`)

## Conventions
- Hexagonal layout: `core/` pure functions, `adapters/` I/O, `service.py` orchestration.
- Everything in English (code, specs, docs).
- Decisions are deterministic and auditable; AI is optional and limited to message phrasing.
- Privacy: no names, contacts or documents in logs, cases or messages.

## Domain Context
Six platform tools: `get_trip_context`, `get_operational_events`, `get_eligible_options`,
`get_policy_rules` (reads) and `create_rebooking_request`, `create_human_case`,
`notify_passenger` (writes). Sources can be stale, degraded or contradictory.

## Important Constraints
- Wrong recommendations cost money and trust; booking changes and compensation are high-impact.
- First version operable by a small team; latency and cost per interaction matter.
