"""Stage 2: reconcile operational events from sources that may disagree.

Single pass, O(E): hash map keyed by (segment, event type) keeping the event with
the best (source priority, recorded_at). Degraded and stale events are discarded
and logged. A STATUS=ON_TIME that outranks a DELAY cannot be resolved safely.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta

from ..models import OperationalEvent


@dataclass(frozen=True)
class SegmentState:
    segment_id: str
    delay_minutes: int = 0
    new_departure: time | None = None
    gate: str | None = None
    status: str | None = None
    event_ids: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()


@dataclass
class Reconciliation:
    segments: dict[str, SegmentState] = field(default_factory=dict)
    discarded: list[dict] = field(default_factory=list)
    conflicts: list[dict] = field(default_factory=list)
    unresolved: bool = False


def _rank(e: OperationalEvent, priority: dict[str, int]) -> tuple[int, datetime]:
    return (priority.get(e.source, 0), e.recorded_at)


def reconcile(
    events: list[OperationalEvent],
    degraded_sources: list[str],
    now: datetime,
    priority: dict[str, int],
    max_age: timedelta,
) -> Reconciliation:
    degraded = set(degraded_sources)
    out = Reconciliation()
    best: dict[tuple[str, str], OperationalEvent] = {}
    dropped: list[OperationalEvent] = []

    for e in events:
        if e.source in degraded:
            out.discarded.append({"event_id": e.event_id, "source": e.source, "reason": "degraded_source"})
            dropped.append(e)
            continue
        if now - e.recorded_at > max_age:
            out.discarded.append({"event_id": e.event_id, "source": e.source, "reason": "stale"})
            dropped.append(e)
            continue
        key = (e.segment_id, e.type)
        cur = best.get(key)
        if cur is None or _rank(e, priority) > _rank(cur, priority):
            best[key] = e

    # Cross-type check: STATUS ON_TIME vs DELAY on the same segment.
    for seg in {s for s, _ in best}:
        delay = best.get((seg, "DELAY"))
        status = best.get((seg, "STATUS"))
        if delay and (delay.delay_minutes or 0) > 0 and status and status.status == "ON_TIME":
            if _rank(status, priority) > _rank(delay, priority):
                out.unresolved = True
                out.conflicts.append({"segment_id": seg, "kind": "status_vs_delay", "resolved": False,
                                      "events": [delay.event_id, status.event_id]})
            else:
                out.conflicts.append({"segment_id": seg, "kind": "status_vs_delay", "resolved": True,
                                      "resolution": "higher_priority_source_wins",
                                      "events": [delay.event_id, status.event_id]})
    for e in dropped:
        d = best.get((e.segment_id, "DELAY"))
        if e.type == "STATUS" and e.status == "ON_TIME" and d and (d.delay_minutes or 0) > 0:
            out.conflicts.append({"segment_id": e.segment_id, "kind": "status_vs_delay", "resolved": True,
                                  "resolution": "contradicting_event_discarded",
                                  "events": [d.event_id, e.event_id]})

    for seg in sorted({s for s, _ in best}):
        d, g, st = best.get((seg, "DELAY")), best.get((seg, "GATE_CHANGE")), best.get((seg, "STATUS"))
        used = [x for x in (d, g, st) if x]
        out.segments[seg] = SegmentState(
            segment_id=seg,
            delay_minutes=(d.delay_minutes or 0) if d else 0,
            new_departure=d.new_departure if d else None,
            gate=g.gate if g else None,
            status=st.status if st else None,
            event_ids=tuple(x.event_id for x in used),
            sources=tuple(sorted({x.source for x in used})),
        )
    return out
