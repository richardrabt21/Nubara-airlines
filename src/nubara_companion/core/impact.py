"""Stage 3: what does the disruption imply for the itinerary?

Linear scan over the segment chain, O(S log S) including the sort. The delay of
each segment shifts its own times; each connection is re-checked against the
minimum connection time. Local clock times are used as given (documented
assumption: the platform provides comparable local times).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..models import Segment, TripContext
from .reconcile import Reconciliation


@dataclass(frozen=True)
class SegmentImpact:
    segment_id: str
    flight_number: str
    origin: str
    destination: str
    scheduled_departure: datetime
    scheduled_arrival: datetime
    delay_minutes: int
    new_departure: datetime
    new_arrival: datetime
    gate: str | None


@dataclass(frozen=True)
class ConnectionImpact:
    from_segment: str
    to_segment: str
    airport: str
    slack_before_minutes: int
    slack_after_minutes: int
    missed: bool


@dataclass
class Impact:
    segments: list[SegmentImpact] = field(default_factory=list)
    connections: list[ConnectionImpact] = field(default_factory=list)
    max_delay_minutes: int = 0
    missed_connection: bool = False
    international: bool | None = None
    group_includes_minor: bool = False
    issues: list[str] = field(default_factory=list)


def _times(seg: Segment) -> tuple[datetime, datetime]:
    dep = datetime.combine(seg.date, seg.scheduled_departure)
    arr = datetime.combine(seg.date, seg.scheduled_arrival)
    if arr <= dep:  # overnight arrival
        arr += timedelta(days=1)
    return dep, arr


def _minutes(delta: timedelta) -> int:
    return int(delta.total_seconds() // 60)


def analyze(
    trip: TripContext,
    recon: Reconciliation,
    min_connection_minutes: int,
    airport_country: dict[str, str],
) -> Impact:
    impact = Impact()
    for seg in sorted(trip.segments, key=lambda s: _times(s)[0]):
        dep, arr = _times(seg)
        state = recon.segments.get(seg.segment_id)
        delay = state.delay_minutes if state else 0
        if state and state.new_departure is not None:
            declared = datetime.combine(seg.date, state.new_departure)
            if abs(_minutes(declared - dep) - delay) > 1:
                impact.issues.append(f"inconsistent_new_departure:{seg.segment_id}")
        impact.segments.append(SegmentImpact(
            segment_id=seg.segment_id, flight_number=seg.flight_number, origin=seg.origin,
            destination=seg.destination, scheduled_departure=dep, scheduled_arrival=arr,
            delay_minutes=delay, new_departure=dep + timedelta(minutes=delay),
            new_arrival=arr + timedelta(minutes=delay), gate=state.gate if state else None))

    for prev, nxt in zip(impact.segments, impact.segments[1:]):
        before = _minutes(nxt.scheduled_departure - prev.scheduled_arrival)
        after = _minutes(nxt.new_departure - prev.new_arrival)
        impact.connections.append(ConnectionImpact(
            from_segment=prev.segment_id, to_segment=nxt.segment_id, airport=prev.destination,
            slack_before_minutes=before, slack_after_minutes=after,
            missed=after < min_connection_minutes))

    impact.max_delay_minutes = max((s.delay_minutes for s in impact.segments), default=0)
    impact.missed_connection = any(c.missed for c in impact.connections)
    impact.group_includes_minor = any(p.is_minor for p in trip.passengers)

    airports = {a for s in impact.segments for a in (s.origin, s.destination)}
    if airports <= airport_country.keys():
        impact.international = len({airport_country[a] for a in airports}) > 1
    else:
        impact.international = None  # unknown -> rules using it become unevaluable
    return impact
