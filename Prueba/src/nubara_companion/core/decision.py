"""Stage 6: the decision gate. Pure function, no I/O: easy to test and audit."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .policy import Entitlement


class Decision(str, Enum):
    INFORM_ONLY = "INFORM_ONLY"          # explain; nothing to act on
    PROPOSE_OPTIONS = "PROPOSE_OPTIONS"  # passenger may confirm an offered option
    ESCALATE_HUMAN = "ESCALATE_HUMAN"    # a person must decide
    DEGRADED_SAFE = "DEGRADED_SAFE"      # required source unavailable: no claims, human case


@dataclass(frozen=True)
class GateInput:
    events_available: bool
    disruption: bool
    policy_available: bool = True
    conflicts_unresolved: bool = False
    policy_issues: list[str] = field(default_factory=list)
    unevaluable_rules: list[str] = field(default_factory=list)
    impact_issues: list[str] = field(default_factory=list)
    entitlements: list[Entitlement] = field(default_factory=list)
    offers_count: int = 0
    options_available: bool = True
    has_adult_contact: bool = True


@dataclass(frozen=True)
class GateResult:
    decision: Decision
    reasons: list[str]
    facts_validated: bool  # safe to communicate the facts to the passenger?


def decide(g: GateInput) -> GateResult:
    if not g.events_available:
        return GateResult(Decision.DEGRADED_SAFE, ["events_unavailable"], False)
    if not g.disruption:
        return GateResult(Decision.INFORM_ONLY, ["no_actionable_disruption"], False)
    if not g.policy_available:
        return GateResult(Decision.DEGRADED_SAFE, ["policy_unavailable"], False)

    reasons: list[str] = []
    if g.conflicts_unresolved:
        reasons.append("sources_conflict_unresolved")
    reasons += g.policy_issues
    reasons += [f"unevaluable_rule:{r}" for r in g.unevaluable_rules]
    reasons += g.impact_issues
    facts_validated = not reasons

    reasons += [f"human_approval_required:{e.rule_id}:{e.entitlement}"
                for e in g.entitlements if e.requires_human_approval]
    rebook_entitled = any(e.entitlement == "REBOOK_NO_FEE" for e in g.entitlements)
    if rebook_entitled and g.offers_count == 0:
        reasons.append("no_eligible_options" if g.options_available else "options_unavailable")
    if not g.has_adult_contact:
        reasons.append("no_adult_contact")

    if reasons:
        return GateResult(Decision.ESCALATE_HUMAN, reasons, facts_validated)
    if g.offers_count:
        return GateResult(Decision.PROPOSE_OPTIONS, ["rebooking_entitlement_with_eligible_options"], True)
    return GateResult(Decision.INFORM_ONLY, ["no_automatable_action"], True)
