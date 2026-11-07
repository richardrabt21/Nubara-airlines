## ADDED Requirements

### Requirement: Source reconciliation
The system SHALL reconcile operational events by source trust and recency, discard events from degraded or stale sources, and log every discard and conflict.

#### Scenario: Contradicting legacy event
- **WHEN** a `LEGACY_MIRROR` event says `ON_TIME` while `OPS_CORE` reports a 160-minute delay and `LEGACY_MIRROR` is degraded
- **THEN** the legacy event is discarded, the delay is used, and the conflict is recorded as resolved

#### Scenario: Unresolvable contradiction
- **WHEN** a non-degraded higher-ranked source says `ON_TIME` against a lower-ranked delay
- **THEN** the system marks facts as unvalidated, escalates to a human and sends no passenger message

### Requirement: Connection impact
The system SHALL propagate delays through the itinerary and flag connections whose remaining time is below the minimum connection time.

#### Scenario: Missed connection
- **WHEN** S1 arrives 160 minutes late and S2 departs 70 minutes after S1's scheduled arrival
- **THEN** the connection is reported as missed with slack `70 -> -90` minutes

### Requirement: Policy evaluation
The system SHALL evaluate only a policy version that is effective and not superseded, with fail-closed three-valued logic.

#### Scenario: Superseded policy
- **WHEN** the retrieved policy version appears in `superseded_versions`
- **THEN** no entitlement is granted and the case is escalated

#### Scenario: Unknown fact
- **WHEN** a rule needs a fact that is unknown and the rest of the rule is true
- **THEN** the rule is reported as unevaluable and never grants an entitlement

### Requirement: Option eligibility
The system SHALL offer an option only if it is fresh, backed by a validated policy reference and entitlement, has seats for the whole party, fits the route and time, has sufficient confidence and carries no fare difference.

#### Scenario: Not enough seats
- **WHEN** an option has 1 seat and the party has 2 passengers
- **THEN** the option is rejected with `insufficient_seats:have_1_need_2`

#### Scenario: Refund on a non-refundable fare
- **WHEN** a `REFUND` option is returned for a non-refundable fare
- **THEN** the option is rejected

### Requirement: Decision gate
The system SHALL return exactly one decision among `INFORM_ONLY`, `PROPOSE_OPTIONS`, `ESCALATE_HUMAN` and `DEGRADED_SAFE`, with machine-readable reasons, and SHALL NOT change a booking without passenger confirmation.

#### Scenario: Party includes a minor
- **WHEN** the party includes a minor and rule R5 requires human approval
- **THEN** the decision is `ESCALATE_HUMAN`, a human case is created and no rebooking is requested

#### Scenario: Required source down
- **WHEN** the events source is unavailable
- **THEN** the decision is `DEGRADED_SAFE`, a human case is created and no passenger message is sent

### Requirement: Safe writes
The system SHALL execute writes idempotently and SHALL treat indeterminate results as unknown rather than as success or failure.

#### Scenario: Timeout after commit
- **WHEN** the rebooking call times out but the request was stored
- **THEN** a retry with the same idempotency key receives a duplicate response and is reconciled as success

#### Scenario: Persistent timeout
- **WHEN** all retries time out
- **THEN** the outcome is `INDETERMINATE` and a human case is created

### Requirement: Validated passenger communication
The system SHALL send only messages whose facts are traceable to sources, that include the policy reference and that contain no personal data or promises about money.

#### Scenario: Hallucinating composer
- **WHEN** the optional AI composer returns a delay that is not in the facts
- **THEN** the output is rejected and the deterministic template is used

### Requirement: Privacy
The system SHALL NOT include names, contact data or document numbers in responses, cases, logs or messages.

#### Scenario: Escalation evidence
- **WHEN** a human case is created for a booking
- **THEN** its evidence contains passenger ids only
