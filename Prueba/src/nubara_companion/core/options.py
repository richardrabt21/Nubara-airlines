"""Stage 5: filter candidate options against policy/capacity/time, then rank top-k.

Filtering is O(n); ranking uses heapq.nsmallest => O(n log k). All rejection
reasons are collected (no short-circuit) so a human can audit why.
"""
from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..models import OptionsDocument, RebookingOption, TripContext
from .impact import Impact
from .policy import Entitlement

CONFIDENCE_RANK = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}


@dataclass
class OptionEvaluation:
    offers: list[RebookingOption] = field(default_factory=list)
    needs_human: list[tuple[RebookingOption, list[str]]] = field(default_factory=list)
    rejected: list[tuple[RebookingOption, list[str]]] = field(default_factory=list)


def is_stale(doc: OptionsDocument, now: datetime) -> bool:
    return now > doc.meta.retrieved_at + timedelta(seconds=doc.meta.ttl_seconds)


def _departure(o: RebookingOption) -> datetime | None:
    return datetime.combine(o.date, o.departure) if o.date and o.departure else None


def evaluate_options(
    doc: OptionsDocument,
    trip: TripContext,
    impact: Impact,
    entitlements: list[Entitlement],
    validated_policy_versions: set[str],
    now: datetime,
    min_connection_minutes: int,
    top_k: int,
) -> OptionEvaluation:
    result = OptionEvaluation()
    n_pax = len(trip.passengers)
    rebook_entitled = any(e.entitlement == "REBOOK_NO_FEE" for e in entitlements)
    stale = is_stale(doc, now)

    # Reference segment: the first missed connection, else the most delayed segment.
    by_id = {s.segment_id: s for s in impact.segments}
    missed = next((c for c in impact.connections if c.missed), None)
    if missed:
        ref = by_id[missed.to_segment]
        earliest: datetime | None = by_id[missed.from_segment].new_arrival + timedelta(
            minutes=min_connection_minutes)
    else:
        ref = max(impact.segments, key=lambda s: s.delay_minutes)
        earliest = None
    final_destination = impact.segments[-1].destination

    for o in doc.options:
        reasons: list[str] = []
        if stale:
            reasons.append("options_stale")
        if o.policy_ref not in validated_policy_versions:
            reasons.append(f"policy_ref_not_validated:{o.policy_ref}")
        if o.type == "REBOOK":
            if not rebook_entitled:
                reasons.append("no_rebooking_entitlement")
            if o.seats_available is None or o.seats_available < n_pax:
                reasons.append(f"insufficient_seats:have_{o.seats_available}_need_{n_pax}")
            if o.origin != ref.origin or o.destination != final_destination:
                reasons.append("route_mismatch")
            dep = _departure(o)
            if dep is None:
                reasons.append("missing_departure")
            elif earliest and dep < earliest:
                reasons.append("departs_before_earliest_feasible_time")
        elif o.type == "REFUND":
            if not trip.fare_conditions.refundable:
                reasons.append("fare_not_refundable")
        else:
            reasons.append(f"unsupported_type:{o.type}")
        if CONFIDENCE_RANK.get(o.confidence, 0) < CONFIDENCE_RANK["MEDIUM"]:
            reasons.append("low_confidence")

        if reasons:
            result.rejected.append((o, reasons))
        elif o.fare_difference > 0:
            result.needs_human.append((o, ["fare_difference_requires_approval"]))
        else:
            result.offers.append(o)

    result.offers = heapq.nsmallest(
        top_k, result.offers,
        key=lambda o: (-CONFIDENCE_RANK[o.confidence], o.fare_difference,
                       _departure(o) or datetime.max, o.option_id))
    return result
