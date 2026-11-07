"""Stage 4: deterministic policy engine.

Rules arrive as data (condition strings). They are parsed with a tiny grammar
(`clause (AND clause)*`) and evaluated with three-valued (Kleene) logic: a missing
fact yields UNKNOWN, and an unknown rule is fail-closed (escalated, never granted).
No `eval()` is ever used.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from ..models import PolicyDocument

_COMPARE = re.compile(r"^(\w+)\s*(>=|<=|==|>|<)\s*(\w+)$")
_BARE = re.compile(r"^\w+$")


class RuleSyntaxError(ValueError):
    pass


@dataclass(frozen=True)
class Entitlement:
    rule_id: str
    entitlement: str
    requires_human_approval: bool
    policy_version: str


@dataclass
class PolicyOutcome:
    triggered: list[Entitlement] = field(default_factory=list)
    unevaluable: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)


def _clause(text: str, facts: dict[str, object]) -> bool | None:
    text = text.strip()
    m = _COMPARE.match(text)
    if m:
        name, op, raw = m.groups()
        value = facts.get(name)
        if raw in ("true", "false"):
            rhs: object = raw == "true"
            if op not in ("==",):
                raise RuleSyntaxError(text)
        else:
            try:
                rhs = int(raw)
            except ValueError as exc:
                raise RuleSyntaxError(text) from exc
        if value is None:
            return None
        return {">=": value >= rhs, "<=": value <= rhs, "==": value == rhs,  # type: ignore[operator]
                ">": value > rhs, "<": value < rhs}[op]  # type: ignore[operator]
    if _BARE.match(text):
        value = facts.get(text)
        return None if value is None else bool(value)
    raise RuleSyntaxError(text)


def eval_condition(condition: str, facts: dict[str, object]) -> bool | None:
    results = [_clause(part, facts) for part in re.split(r"\s+AND\s+", condition.strip())]
    if any(r is False for r in results):
        return False
    if any(r is None for r in results):
        return None
    return True


def validate_policy(doc: PolicyDocument, today: date) -> list[str]:
    issues = []
    if doc.effective_from > today:
        issues.append(f"policy_not_yet_effective:{doc.version}")
    if doc.version in doc.meta.superseded_versions:
        issues.append(f"policy_superseded:{doc.version}")
    return issues


def evaluate(doc: PolicyDocument, facts: dict[str, object], today: date) -> PolicyOutcome:
    out = PolicyOutcome(issues=validate_policy(doc, today))
    if out.issues:  # never grant anything from an invalid policy version
        return out
    for rule in doc.rules:
        try:
            verdict = eval_condition(rule.condition, facts)
        except RuleSyntaxError:
            verdict = None
        if verdict is True:
            out.triggered.append(Entitlement(rule.rule_id, rule.entitlement,
                                             rule.requires_human_approval, doc.version))
        elif verdict is None:
            out.unevaluable.append(rule.rule_id)
    return out
