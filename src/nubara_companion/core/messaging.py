"""Stage 8: passenger message. Composition may be AI-assisted (optional), but the
validator is always deterministic and the template is the guaranteed fallback.

A composer only ever sees `MessageFacts` (no PII). Whatever it returns must pass
`validate_message`: policy reference present, every number/flight/time traceable to
a fact, no PII, no promises about money.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Protocol

from ..models import Passenger
from ..privacy import find_pii

FORBIDDEN_WORDS = ("refund", "compensation", "cash", "guarantee")
MAX_LENGTH = 900


@dataclass(frozen=True)
class MessageFacts:
    decision: str
    policy_ref: str
    as_of: str
    delayed: list[dict[str, str]] = field(default_factory=list)
    missed: list[dict[str, str]] = field(default_factory=list)
    options: list[dict[str, str]] = field(default_factory=list)

    def allowed_strings(self) -> set[str]:
        values = {self.policy_ref, self.as_of}
        for group in (self.delayed, self.missed, self.options):
            for item in group:
                values.update(v for v in item.values() if v)
        return values

    def to_dict(self) -> dict:
        return {"decision": self.decision, "policy_ref": self.policy_ref, "as_of": self.as_of,
                "delayed": self.delayed, "missed": self.missed, "options": self.options}


class MessageComposer(Protocol):
    name: str

    def compose(self, facts: MessageFacts) -> str: ...


class TemplateComposer:
    """Deterministic, always-available composer."""

    name = "template"

    def compose(self, facts: MessageFacts) -> str:
        parts = ["Dear traveler,"]
        for d in facts.delayed:
            parts.append(
                f"your flight {d['flight']} ({d['route']}) on {d['date']} is delayed by "
                f"{d['delay']} minutes; the new departure time is {d['new_departure']}.")
        for m in facts.missed:
            parts.append(
                f"With this delay you will not be able to make your connection {m['flight']} "
                f"({m['route']}, scheduled {m['departure']}).")
        if facts.decision == "PROPOSE_OPTIONS" and facts.options:
            parts.append("You can choose one of these alternatives at no extra fare:")
            for o in facts.options:
                parts.append(f"Option {o['option_id']}: {o['flight']} ({o['route']}) on "
                             f"{o['date']} at {o['departure']}.")
            parts.append("Reply with the option you prefer to confirm; nothing changes until you do.")
        elif facts.decision == "ESCALATE_HUMAN":
            parts.append("A customer care agent will review your booking with priority and "
                         "contact you. No changes have been made to your booking.")
        parts.append(f"Information as of {facts.as_of}. Reference: {facts.policy_ref}.")
        return " ".join(parts)


class LLMComposer:
    """Optional AI-assisted phrasing. Disabled unless a `generate` callable is
    injected (e.g. a Gemini client). It receives only sanitized facts."""

    name = "llm"

    def __init__(self, generate: Callable[[dict], str]) -> None:
        self._generate = generate

    def compose(self, facts: MessageFacts) -> str:
        return self._generate(facts.to_dict())


def validate_message(body: str, facts: MessageFacts, passengers: Iterable[Passenger]) -> list[str]:
    issues: list[str] = []
    if not body.strip():
        return ["empty_body"]
    if len(body) > MAX_LENGTH:
        issues.append("too_long")
    if facts.policy_ref not in body:
        issues.append("missing_policy_ref")
    residual = body
    for s in sorted(facts.allowed_strings(), key=len, reverse=True):
        residual = residual.replace(s, "")
    if re.search(r"\d", residual):
        issues.append("unverified_numeric_content")
    leaks = find_pii(body, passengers)
    if leaks:
        issues.append("pii_leak:" + ",".join(leaks))
    lowered = body.lower()
    issues += [f"forbidden_promise:{w}" for w in FORBIDDEN_WORDS if w in lowered]
    return issues


def compose_validated(
    composers: list[MessageComposer], facts: MessageFacts, passengers: list[Passenger]
) -> tuple[str, str, list[str]]:
    """Try composers in order; return (body, composer_name, rejected_attempts)."""
    rejected: list[str] = []
    for c in [*composers, TemplateComposer()]:
        try:
            body = c.compose(facts)
        except Exception as exc:  # a failing model must never block the flow
            rejected.append(f"{c.name}:error:{type(exc).__name__}")
            continue
        issues = validate_message(body, facts, passengers)
        if not issues:
            return body, c.name, rejected
        rejected.append(f"{c.name}:{'|'.join(issues)}")
    raise RuntimeError("no composer produced a valid message")  # template bug: fail loudly
