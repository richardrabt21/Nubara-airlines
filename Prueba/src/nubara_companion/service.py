"""Orchestrates the 8-stage pipeline. All decisions are deterministic; the only
optional AI touchpoint is message phrasing (see core/messaging.py)."""
from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import timedelta
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from .clock import Clock, SystemClock
from .config import Settings
from .core import options as opt
from .core import policy as pol
from .core.actions import ActionExecutor, make_key
from .core.decision import Decision, GateInput, decide
from .core.impact import Impact, analyze
from .core.messaging import MessageComposer, MessageFacts, compose_validated
from .core.reconcile import reconcile
from .errors import BookingNotFound, ConfirmationRefused, ContractViolation, SourceUnavailable
from .models import EventsDocument, OptionsDocument, PolicyDocument, TripContext
from .ports import SystemsPort
from .privacy import PiiMaskingFilter
from .resilience import backoff_delays

log = logging.getLogger("nubara.service")
log.addFilter(PiiMaskingFilter())

M = TypeVar("M", bound=BaseModel)
DEFAULT_CHANNEL = "PUSH"


class DisruptionService:
    def __init__(
        self,
        systems: SystemsPort,
        clock: Clock | None = None,
        settings: Settings | None = None,
        composers: list[MessageComposer] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        rng: random.Random | None = None,
    ) -> None:
        self.systems = systems
        self.clock = clock or SystemClock()
        self.s = settings or Settings()
        self.composers = composers or []
        self._sleep = sleep
        self._rng = rng
        self.executor = ActionExecutor(systems, self.s, self.clock, sleep, rng)

    # ----------------------------------------------------------- retrieval --
    def _read(self, fn: Callable[[], dict], model: type[M]) -> M:
        """Read + validate with bounded retries. Raises SourceUnavailable /
        ContractViolation; BookingNotFound is never retried."""
        last: Exception | None = None
        for delay in [0.0, *backoff_delays(self.s.read_attempts - 1, self.s.backoff_base_seconds, self._rng)]:
            if delay:
                self._sleep(delay)
            try:
                return model.model_validate(fn())
            except BookingNotFound:
                raise
            except ValidationError as exc:
                last = ContractViolation(f"{model.__name__}: {len(exc.errors())} validation errors")
            except SourceUnavailable as exc:
                last = exc
        assert last is not None
        raise last

    # ------------------------------------------------------------ assess ----
    def assess(self, booking_id: str, *, compensation_requested: bool = False,
               dry_run: bool = False) -> dict[str, Any]:
        audit: list[dict[str, Any]] = []
        step = lambda name, **kw: audit.append({"step": name, **kw})  # noqa: E731
        now = self.clock.now()

        # 1. Retrieve -------------------------------------------------------
        trip = self._read(lambda: self.systems.get_trip_context(booking_id), TripContext)
        step("retrieve_trip", completeness=trip.meta.completeness, missing=trip.meta.missing_fields)
        segment_ids = [s.segment_id for s in trip.segments]

        events_doc: EventsDocument | None = None
        try:
            events_doc = self._read(lambda: self.systems.get_operational_events(segment_ids), EventsDocument)
        except (SourceUnavailable, ContractViolation) as exc:
            step("retrieve_events", ok=False, error=type(exc).__name__)

        # 2. Reconcile ------------------------------------------------------
        recon = reconcile(events_doc.events, events_doc.meta.sources_degraded, now, self.s.source_priority,
                          timedelta(minutes=self.s.event_max_age_minutes)) if events_doc else None
        if recon:
            step("reconcile_events", discarded=recon.discarded, conflicts=recon.conflicts,
                 unresolved=recon.unresolved)

        # 3. Impact ---------------------------------------------------------
        impact = analyze(trip, recon, self.s.min_connection_minutes, self.s.airport_country) if recon else Impact()
        disruption = bool(recon) and (impact.max_delay_minutes >= self.s.min_actionable_delay_minutes
                                      or impact.missed_connection)
        if recon:
            step("impact", max_delay=impact.max_delay_minutes, missed_connection=impact.missed_connection,
                 international=impact.international, issues=impact.issues)

        # 4-5. Policy + options (parallel; only when something actionable happened)
        policy_doc: PolicyDocument | None = None
        options_doc: OptionsDocument | None = None
        outcome = pol.PolicyOutcome()
        evaluation = opt.OptionEvaluation()
        if disruption:
            policy_doc, options_doc = self._fetch_policy_and_options(trip)
            step("retrieve_policy_options", policy_ok=policy_doc is not None, options_ok=options_doc is not None)
            if policy_doc:
                facts = {"delay_minutes": impact.max_delay_minutes, "missed_connection": impact.missed_connection,
                         "international": impact.international, "group_includes_minor": impact.group_includes_minor,
                         "compensation_requested": compensation_requested}
                outcome = pol.evaluate(policy_doc, facts, now.date())
                step("policy", version=policy_doc.version,
                     triggered=[e.rule_id for e in outcome.triggered],
                     unevaluable=outcome.unevaluable, issues=outcome.issues)
                if options_doc and opt.is_stale(options_doc, self.clock.now()):
                    step("options_stale_refetch")
                    try:  # cache-aside revalidation: one fresh read, else reject as stale
                        options_doc = self._read(lambda: self.systems.get_eligible_options(trip.model_dump(mode="json")),
                                                 OptionsDocument)
                    except (SourceUnavailable, ContractViolation):
                        options_doc = None
                if options_doc:
                    evaluation = opt.evaluate_options(
                        options_doc, trip, impact, outcome.triggered,
                        {policy_doc.version} if not outcome.issues else set(),
                        self.clock.now(), self.s.min_connection_minutes, self.s.top_k_options)
                    step("options", offered=[o.option_id for o in evaluation.offers],
                         needs_human=[o.option_id for o, _ in evaluation.needs_human],
                         rejected={o.option_id: r for o, r in evaluation.rejected})

        # 6. Decision gate --------------------------------------------------
        contact = next((p for p in trip.passengers if p.type.upper() == "ADULT"), None)
        gate = decide(GateInput(
            events_available=recon is not None, disruption=disruption,
            policy_available=policy_doc is not None if disruption else True,
            conflicts_unresolved=bool(recon and recon.unresolved), policy_issues=outcome.issues,
            unevaluable_rules=outcome.unevaluable, impact_issues=impact.issues,
            entitlements=outcome.triggered, offers_count=len(evaluation.offers),
            options_available=options_doc is not None, has_adult_contact=contact is not None))
        step("decision", decision=gate.decision.value, reasons=gate.reasons, facts_validated=gate.facts_validated)

        result = self._build_result(trip, recon, impact, outcome, evaluation, policy_doc, gate, events_doc, now)

        # 7-8. Actions (skipped on dry-run) ------------------------------------
        actions: dict[str, Any] = {"human_case": None, "notification": None}
        if not dry_run:
            if gate.decision in (Decision.ESCALATE_HUMAN, Decision.DEGRADED_SAFE):
                actions["human_case"] = self._open_case(trip, impact, outcome, evaluation, gate, result)
            if gate.facts_validated and disruption and policy_doc and contact:
                actions["notification"] = self._notify(trip, impact, evaluation, policy_doc, gate, contact.passenger_id,
                                                       result, now)
        result["actions"] = actions
        result["dry_run"] = dry_run
        result["audit"] = audit
        return result

    def _fetch_policy_and_options(self, trip: TripContext):
        with ThreadPoolExecutor(max_workers=2) as pool:
            f_policy = pool.submit(self._read, lambda: self.systems.get_policy_rules("DELAY"), PolicyDocument)
            f_opts = pool.submit(self._read, lambda: self.systems.get_eligible_options(trip.model_dump(mode="json")),
                                 OptionsDocument)
            out = []
            for fut in (f_policy, f_opts):
                try:
                    out.append(fut.result(timeout=self.s.retrieval_timeout_seconds))
                except (SourceUnavailable, ContractViolation, FutureTimeout):
                    out.append(None)
        return out[0], out[1]

    # ------------------------------------------------------------ results ---
    def _build_result(self, trip, recon, impact, outcome, evaluation, policy_doc, gate, events_doc, now):
        happened, implies = [], []
        for s in impact.segments:
            if s.delay_minutes > 0:
                happened.append(f"{s.flight_number} {s.origin}->{s.destination} is delayed {s.delay_minutes} min "
                                f"(new departure {s.new_departure:%H:%M}).")
            if s.gate:
                happened.append(f"{s.flight_number} gate changed to {s.gate}.")
        for c in impact.connections:
            if c.missed:
                implies.append(f"Connection {c.from_segment}->{c.to_segment} at {c.airport} is missed "
                               f"(slack {c.slack_before_minutes} -> {c.slack_after_minutes} min).")
        if impact.group_includes_minor:
            implies.append("The party includes a minor: priority handling needs human approval.")
        offered = [self._opt_dict(o) for o in evaluation.offers]
        return {
            "booking_id": trip.booking_id,
            "generated_at": now.isoformat(),
            "decision": gate.decision.value,
            "decision_reasons": gate.reasons,
            "what_happened": happened,
            "what_it_implies": implies,
            "impact": {
                "max_delay_minutes": impact.max_delay_minutes,
                "missed_connection": impact.missed_connection,
                "international": impact.international,
                "segments": [{"segment_id": s.segment_id, "flight": s.flight_number, "delay_minutes": s.delay_minutes,
                              "new_departure": s.new_departure.isoformat(), "new_arrival": s.new_arrival.isoformat()}
                             for s in impact.segments],
                "connections": [vars(c) for c in impact.connections],
            },
            "entitlements": [vars(e) for e in outcome.triggered],
            "options": {
                "offered": offered,
                "needs_human_review": [{**self._opt_dict(o), "reasons": r} for o, r in evaluation.needs_human],
                "rejected": [{"option_id": o.option_id, "reasons": r} for o, r in evaluation.rejected],
            },
            "data_quality": {
                "trip_completeness": trip.meta.completeness,
                "missing_fields": trip.meta.missing_fields,
                "sources_degraded": events_doc.meta.sources_degraded if events_doc else ["events_unavailable"],
                "discarded_events": recon.discarded if recon else [],
                "conflicts": recon.conflicts if recon else [],
                "policy_issues": outcome.issues,
                "unevaluable_rules": outcome.unevaluable,
            },
            "traceability": {
                "policy_version": policy_doc.version if policy_doc else None,
                "event_ids": sorted(i for st in (recon.segments.values() if recon else []) for i in st.event_ids),
            },
        }

    @staticmethod
    def _opt_dict(o) -> dict[str, Any]:
        return {"option_id": o.option_id, "type": o.type, "flight_number": o.flight_number, "origin": o.origin,
                "destination": o.destination, "date": o.date.isoformat() if o.date else None,
                "departure": o.departure.strftime("%H:%M") if o.departure else None,
                "fare_difference": o.fare_difference, "currency": o.currency, "confidence": o.confidence,
                "policy_ref": o.policy_ref}

    # ------------------------------------------------------------ actions ---
    def _open_case(self, trip, impact, outcome, evaluation, gate, result) -> dict[str, Any]:
        trace = result["traceability"]
        key = make_key("HC", trip.booking_id, sorted(gate.reasons), trace["event_ids"], trace["policy_version"])
        evidence = {  # PII-minimized: ids only, no names/contacts/documents
            "booking_id": trip.booking_id,
            "passenger_ids": [p.passenger_id for p in trip.passengers],
            "party_includes_minor": impact.group_includes_minor,
            "decision_reasons": gate.reasons,
            "impact": result["impact"],
            "entitlements": result["entitlements"],
            "recommended_option": (result["options"]["offered"] or [None])[0],
            "options_needing_review": result["options"]["needs_human_review"],
            "data_quality": result["data_quality"],
            "traceability": trace,
        }
        case = self.executor.open_case(key, ";".join(gate.reasons), evidence)
        log.info("case %s for booking %s", case.get("case_id", case["status"]), trip.booking_id)
        return case

    def _notify(self, trip, impact, evaluation, policy_doc, gate, recipient_id, result, now) -> dict[str, Any]:
        route = lambda a, b: f"{a}→{b}"  # noqa: E731
        by_id = {s.segment_id: s for s in impact.segments}
        facts = MessageFacts(
            decision=gate.decision.value, policy_ref=policy_doc.version, as_of=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            delayed=[{"flight": s.flight_number, "route": route(s.origin, s.destination),
                      "date": s.scheduled_departure.date().isoformat(), "delay": str(s.delay_minutes),
                      "new_departure": f"{s.new_departure:%H:%M}"} for s in impact.segments if s.delay_minutes > 0],
            missed=[{"flight": by_id[c.to_segment].flight_number,
                     "route": route(by_id[c.to_segment].origin, by_id[c.to_segment].destination),
                     "departure": f"{by_id[c.to_segment].scheduled_departure:%H:%M}"}
                    for c in impact.connections if c.missed],
            options=[{"option_id": o.option_id, "flight": o.flight_number or "", "route": route(o.origin, o.destination),
                      "date": o.date.isoformat() if o.date else "", "departure": o.departure.strftime("%H:%M")
                      if o.departure else ""} for o in evaluation.offers]
            if gate.decision == Decision.PROPOSE_OPTIONS else [])
        body, composer, rejected = compose_validated(self.composers, facts, trip.passengers)
        message = {"booking_id": trip.booking_id, "recipient_passenger_id": recipient_id,
                   "channel": DEFAULT_CHANNEL, "template_id": f"DISRUPTION_{gate.decision.value}",
                   "body": body, "policy_ref": policy_doc.version,
                   "fact_refs": result["traceability"]["event_ids"]}
        key = make_key("NT", trip.booking_id, body)
        outcome = self.executor.notify(key, message)
        outcome.update({"composer": composer, "rejected_compositions": rejected, "body": body})
        return outcome

    # ----------------------------------------------------------- confirm ----
    def confirm_rebooking(self, booking_id: str, option_id: str) -> dict[str, Any]:
        """Passenger confirmation: re-validates everything with fresh data first."""
        fresh = self.assess(booking_id, dry_run=True)
        if fresh["decision"] != Decision.PROPOSE_OPTIONS.value:
            raise ConfirmationRefused(f"decision_is_{fresh['decision']}")
        offered = {o["option_id"]: o for o in fresh["options"]["offered"]}
        if option_id not in offered:
            raise ConfirmationRefused("option_not_currently_offered")
        trace = fresh["traceability"]
        key = make_key("RBK", booking_id, option_id, trace["policy_version"], trace["event_ids"])
        outcome = self.executor.rebook(key, {"booking_id": booking_id, **offered[option_id]})
        response: dict[str, Any] = {"booking_id": booking_id, "option_id": option_id, "rebooking": outcome}
        if outcome["status"] in ("INDETERMINATE", "FAILED"):
            ckey = make_key("HC", booking_id, "rebooking", outcome["status"], key)
            response["human_case"] = self.executor.open_case(
                ckey, f"rebooking_{outcome['status'].lower()}",
                {"booking_id": booking_id, "option_id": option_id, "idempotency_key": key,
                 "detail": outcome.get("detail")})
        return response
