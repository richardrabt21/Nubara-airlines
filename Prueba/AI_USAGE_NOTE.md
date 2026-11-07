# AI Usage & Decision Note

## AI tools used and for what
- **Claude Code (AI-assisted development):** analysis of the statement, architecture proposal, code, tests and docs, produced under my direction in a plan → implement → test loop.
- **Runtime AI:** none by default. `LLMComposer` is an optional hook for message phrasing; it is **off** unless a generator is injected and it never sees personal data.

## Outputs accepted, rejected, modified
- Accepted: pipeline decomposition, three-valued rule engine, heap-based top-k, idempotency-key design, hexagonal layout.
- Modified: the first plan promised to "communicate uncertainty" to the passenger; I changed it to *no message* when facts are unvalidated, because `notify_passenger` must not receive unvalidated content. On a 503 from rebooking I changed "never retry" to "retry with the **same** idempotency key, then escalate", which is safe and resolves committed-but-timed-out calls.
- Fixed after running tests: a Pydantic field named `date` shadowing the `date` type; the upstream `status` overwriting our own result status.
- Rejected: letting an LLM decide eligibility or compose the final text unchecked; automatic rebooking.

## How I validated the result
29 automated tests over the exam's sample payloads and injected failures (all passing), a demo script, manual review of the generated passenger messages, and a PII scan of responses, cases and notifications. Not yet validated: Docker build and `openspec validate` (tools not installed at the time).

## Decisions I made personally
Thresholds (45 min connection, 30 min actionable delay, 30 min event age), source trust order, escalating every party with a minor, never offering options with a fare difference, silence over uncertain messages, and keeping AI out of decisions.

## What must never go to a model
Names, e-mail, phone, document numbers, loyalty tier, free-form booking data, internal case evidence, and any decision about money, rebooking or compensation. Only sanitized `MessageFacts` (flight numbers, times, delay, option ids, policy reference) may be sent.

## Step classification

| STEP | CLASSIFICATION | REASON | CONTROL / FALLBACK |
|---|---|---|---|
| Retrieve trip/events/policy/options | deterministic | Plain reads with known contracts | Timeouts, bounded retries, Pydantic validation; failure → `DEGRADED_SAFE` |
| Reconcile contradictory events | deterministic | Must be reproducible and auditable | Trust order; discarded events logged; unresolved → human |
| Impact analysis (delay, missed connection) | deterministic | Arithmetic over times | Consistency check of `new_departure`; inconsistency → human |
| Policy evaluation | deterministic | Versioned rules, legal/cost impact | Fail-closed on unknown facts, invalid or superseded policy |
| Option filtering and ranking | deterministic | Hard constraints (seats, TTL, entitlement) | Reasons kept per option; stale → re-fetch, else reject |
| Decision gate | deterministic | Accountability of who acts | Pure function with unit tests; default is escalate |
| Rebooking / compensation / refund approval | human | High-impact, money, minors | Passenger confirmation; human case with evidence and SLA |
| Rebooking request execution | deterministic | Idempotent write | Same-key retry, 409 reconciliation, `INDETERMINATE` → human |
| Passenger message phrasing | AI-assisted (optional) | Tone and clarity benefit from a model | Sanitized facts only; validator (facts, policy ref, PII, promises); template fallback |
| Message validation | deterministic | Last barrier before the passenger | Failing text is never sent |
| Case summary for the agent | AI-assisted (optional, not implemented) | Could shorten reading time | Structured evidence already attached; human reads source data |
