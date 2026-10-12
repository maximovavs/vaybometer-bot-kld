"""H2-A pure, store-independent NOAA observed-3h reconciliation model.

NO I/O, clocks, persistence, provider or delivery calls. The caller MUST supply
an H1 transport-verified ParsedHistory and explicit UTC as_of. The string
'swpc_observed_3h' alone does not authenticate a network response; production
must enforce the H1 trust boundary before using this module.

`expected_version` is an optimistic concurrency contract, NOT a durable CAS.
An actual store must atomically check/version/write the returned state; do not
publish decisions before that commit. Outbox entries are *proposals*, never
authorization to send Telegram. Ambiguous network ACKs require manual review.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Literal

from geomagnetic_history import (
    OBSERVED_SOURCE, ParsedHistory, Rolling24hPeak, incident_candidates,
    kp_g_level, parse_observed_3h_history, rolling_24h_peak,
)

SCHEMA_VERSION = 1
THREE_HOURS = timedelta(hours=3)
MAX_INTERVALS = 8 * 90  # 90-day *capacity*, not a silent deletion policy
MAX_EVENT_LAG = timedelta(hours=6, minutes=30)
IntervalStatus = Literal["OBSERVED", "CONFLICT"]
OutboxStatus = Literal["PENDING", "SENT", "UNCERTAIN"]
DecisionStatus = Literal["PROPOSED", "SUPPRESSED", "REVIEW"]


class StateError(ValueError):
    """Invalid state, provenance, or ambiguous write is rejected, not repaired."""


class StaleVersion(StateError):
    """Expected store revision does not match actual revision."""


@dataclass(frozen=True, slots=True)
class IntervalRecord:
    interval_start_utc: datetime
    observed_values: tuple[float, ...]
    status: IntervalStatus
    source: str = OBSERVED_SOURCE


@dataclass(frozen=True, slots=True)
class IncidentRecord:
    incident_id: str
    candidate_ids: tuple[str, ...]
    storm_intervals_utc: tuple[datetime, ...]
    first_storm_interval_start_utc: datetime
    last_storm_interval_end_utc: datetime
    peak_kp_raw: float
    peak_g_level: str
    state: Literal["OPEN", "CLOSED"]
    closed_at_utc: datetime | None
    continuity_unknown: bool
    review_required: bool = False


@dataclass(frozen=True, slots=True)
class OutboxEntry:
    event_key: str
    incident_id: str
    transition: Literal["START", "ESCALATE", "CLOSE"]
    g_level: str | None
    status: OutboxStatus = "PENDING"
    telegram_message_id: int | None = None


@dataclass(frozen=True, slots=True)
class GeomagneticState:
    schema_version: int = SCHEMA_VERSION
    version: int = 0
    next_incident_seq: int = 1
    intervals: tuple[IntervalRecord, ...] = ()
    incidents: tuple[IncidentRecord, ...] = ()
    outbox: tuple[OutboxEntry, ...] = ()


@dataclass(frozen=True, slots=True)
class Decision:
    status: DecisionStatus
    event_key: str | None
    incident_id: str | None
    reason: str


@dataclass(frozen=True, slots=True)
class ReconcileResult:
    state: GeomagneticState
    peak: Rolling24hPeak
    decisions: tuple[Decision, ...]
    requires_review: bool


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise StateError("explicit aware UTC datetime required")
    return value.astimezone(timezone.utc)


def _start(value: datetime) -> datetime:
    dt = _utc(value)
    if dt.hour % 3 or dt.minute or dt.second or dt.microsecond:
        raise StateError("UTC three-hour interval alignment required")
    return dt


def _kp(value: object) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 9:
        raise StateError("invalid unrounded Kp")
    return float(value)


def _validate_state(state: GeomagneticState) -> None:
    if not isinstance(state, GeomagneticState) or state.schema_version != SCHEMA_VERSION:
        raise StateError("unsupported state schema")
    if type(state.version) is not int or state.version < 0 or type(state.next_incident_seq) is not int or state.next_incident_seq < 1:
        raise StateError("invalid revision or identity counter")
    if not isinstance(state.intervals, tuple) or len(state.intervals) > MAX_INTERVALS:
        raise StateError("interval capacity exceeded; explicit reviewed retention needed")
    seen_starts = set()
    for r in state.intervals:
        if not isinstance(r, IntervalRecord) or r.source != OBSERVED_SOURCE or r.status not in ("OBSERVED", "CONFLICT"):
            raise StateError("untrusted stored interval")
        dt = _start(r.interval_start_utc)
        if dt in seen_starts or not isinstance(r.observed_values, tuple):
            raise StateError("duplicate or malformed stored interval")
        seen_starts.add(dt)
        vs = tuple(sorted({_kp(v) for v in r.observed_values}))
        if vs != r.observed_values or (r.status == "OBSERVED" and len(vs) != 1) or (r.status == "CONFLICT" and len(vs) == 1):
            raise StateError("invalid interval value provenance/conflict")
    seen_ids, aliases = set(), set()
    for inc in state.incidents:
        if not isinstance(inc, IncidentRecord) or not inc.incident_id or inc.incident_id in seen_ids:
            raise StateError("duplicate/invalid incident ID")
        seen_ids.add(inc.incident_id)
        if not inc.storm_intervals_utc or len(inc.storm_intervals_utc) != len(set(inc.storm_intervals_utc)):
            raise StateError("invalid incident membership")
        for slot in inc.storm_intervals_utc:
            _start(slot)
        for a in inc.candidate_ids:
            if not a or a in aliases:
                raise StateError("ambiguous candidate alias")
            aliases.add(a)
        if _kp(inc.peak_kp_raw) < 5 or kp_g_level(inc.peak_kp_raw) != inc.peak_g_level:
            raise StateError("invalid incident peak")
    event_keys = set()
    for ev in state.outbox:
        if not isinstance(ev, OutboxEntry) or not ev.event_key or ev.event_key in event_keys or ev.incident_id not in seen_ids:
            raise StateError("invalid outbox identity")
        event_keys.add(ev.event_key)
        if ev.status not in ("PENDING", "SENT", "UNCERTAIN") or ev.transition not in ("START", "ESCALATE", "CLOSE"):
            raise StateError("invalid outbox status")
        if ev.status == "SENT":
            if type(ev.telegram_message_id) is not int or ev.telegram_message_id <= 0:
                raise StateError("sent event requires positive message id")
        elif ev.telegram_message_id is not None:
            raise StateError("unconfirmed event cannot hold message id")


def _validate_history(history: ParsedHistory, as_of: datetime) -> None:
    if not isinstance(history, ParsedHistory) or history.source != OBSERVED_SOURCE or history.as_of_utc != as_of:
        raise StateError("only H1 trusted observed-3h ParsedHistory with matching clock accepted")
    seen = set()
    for bar in history.bars:
        dt = _start(bar.interval_start_utc)
        if (bar.source != OBSERVED_SOURCE or bar.interval_end_utc != dt + THREE_HOURS
                or _kp(bar.kp_raw) != bar.kp_raw or bar.g_level != kp_g_level(bar.kp_raw)
                or dt > as_of + timedelta(minutes=10) or dt in seen):
            raise StateError("malformed or untrusted observed bar")
        seen.add(dt)
    for conflict in history.conflicting_interval_starts_utc:
        if _start(conflict) in seen or conflict > as_of + timedelta(minutes=10):
            raise StateError("invalid NOAA conflict provenance")
    if len(set(history.conflicting_interval_starts_utc)) != len(history.conflicting_interval_starts_utc):
        raise StateError("duplicated conflict slot")


def _aggregate_intervals(previous: tuple[IntervalRecord, ...], history: ParsedHistory) -> tuple[IntervalRecord, ...]:
    rows = {r.interval_start_utc: r for r in previous}
    for conflict in history.conflicting_interval_starts_utc:
        old = rows.get(conflict)
        rows[conflict] = IntervalRecord(conflict, old.observed_values if old else (), "CONFLICT")
    for bar in history.bars:
        old = rows.get(bar.interval_start_utc)
        vals = tuple(sorted(set((old.observed_values if old else ()) + (_kp(bar.kp_raw),))))
        status: IntervalStatus = "CONFLICT" if (old and old.status == "CONFLICT") or len(vals) > 1 else "OBSERVED"
        rows[bar.interval_start_utc] = IntervalRecord(bar.interval_start_utc, vals, status)
    if len(rows) > MAX_INTERVALS:
        raise StateError("bounded 90-day capacity exceeded; no silent pruning")
    return tuple(sorted(rows.values(), key=lambda r: r.interval_start_utc))


def _history_from_intervals(intervals: tuple[IntervalRecord, ...], as_of: datetime) -> ParsedHistory:
    rows = [{"time_tag": i.interval_start_utc.isoformat(), "Kp": i.observed_values[0]}
            for i in intervals if i.status == "OBSERVED"]
    return parse_observed_3h_history(rows, as_of_utc=as_of, source=OBSERVED_SOURCE)


def _event_key(inc_id: str, kind: str, g_level: str | None) -> str:
    return f"{inc_id}:{kind}" + (f":{g_level}" if kind == "ESCALATE" else "")


def reconcile(
    previous: GeomagneticState, history: ParsedHistory, *, expected_version: int,
    as_of_utc: datetime,
) -> ReconcileResult:
    """Deterministic pure candidate reconciliation; caller must commit via CAS.

    Ambiguous merges/splits and historical changes are REVIEW, never eligible.
    Returning PENDING outbox entries does not send or reserve anything durably.
    """
    as_of = _utc(as_of_utc)
    _validate_state(previous)
    if type(expected_version) is not int or expected_version != previous.version:
        raise StaleVersion("state revision mismatch")
    _validate_history(history, as_of)
    intervals = _aggregate_intervals(previous.intervals, history)
    combined = _history_from_intervals(intervals, as_of)
    peak = rolling_24h_peak(combined)
    candidates = incident_candidates(combined)
    incumbents = {i.incident_id: i for i in previous.incidents}
    previous_members = {i.incident_id: set(i.storm_intervals_utc) for i in previous.incidents}
    prev_aliases = {alias: i.incident_id for i in previous.incidents for alias in i.candidate_ids}
    storm_candidates = []
    for c in candidates:
        storm = frozenset(b.interval_start_utc for b in combined.bars
                          if c.first_storm_interval_start_utc <= b.interval_start_utc < c.last_storm_interval_end_utc
                          and b.g_level is not None)
        matches = {id for id, members in previous_members.items() if members.intersection(storm)}
        if c.candidate_id in prev_aliases:
            matches.add(prev_aliases[c.candidate_id])
        storm_candidates.append((c, storm, matches))
    reverse: dict[str, int] = {}
    for _, _, matches in storm_candidates:
        for ident in matches:
            reverse[ident] = reverse.get(ident, 0) + 1
    incidents = dict(incumbents)
    outbox = {e.event_key: e for e in previous.outbox}
    decisions: list[Decision] = []
    seq = previous.next_incident_seq
    matched_ids: set[str] = set()
    for c, storm, matches in storm_candidates:
        if len(matches) > 1 or any(reverse[k] > 1 for k in matches):
            for k in matches:
                incidents[k] = replace(incidents[k], review_required=True)
                matched_ids.add(k)
            decisions.append(Decision("REVIEW", None, None, "ambiguous candidate merger/split"))
            continue
        conflicted = any(i.status == "CONFLICT" and c.first_storm_interval_start_utc <= i.interval_start_utc < c.last_storm_interval_end_utc
                         for i in intervals)
        if matches:
            ident = next(iter(matches))
            original = incidents[ident]
            matched_ids.add(ident)
            aliases = tuple(sorted(set(original.candidate_ids) | {c.candidate_id}))
            storm_union = tuple(sorted(set(original.storm_intervals_utc) | storm))
        else:
            ident = f"kld-geomag-{seq:08d}"
            seq += 1
            original = None
            aliases = (c.candidate_id,)
            storm_union = tuple(sorted(storm))
        requires_review = bool(c.continuity_unknown or conflicted or (original and original.review_required))
        inc = IncidentRecord(
            incident_id=ident, candidate_ids=aliases, storm_intervals_utc=storm_union,
            first_storm_interval_start_utc=c.first_storm_interval_start_utc,
            last_storm_interval_end_utc=c.last_storm_interval_end_utc,
            peak_kp_raw=c.peak_kp_raw, peak_g_level=c.peak_g_level,
            state=c.state, closed_at_utc=c.closed_at_utc,
            continuity_unknown=c.continuity_unknown, review_required=requires_review,
        )
        incidents[ident] = inc
        if requires_review:
            decisions.append(Decision("REVIEW", None, ident, "continuity/conflict requires reconciliation"))
            continue
        for t in c.transitions:
            key = _event_key(ident, t.kind, t.g_level)
            if key in outbox:
                decisions.append(Decision("SUPPRESSED", key, ident, "existing outbox identity"))
                continue
            # Never announce escalation/closure of a storm for which START has
            # not been *confirmed* delivered; startup/historical replay must
            # not issue stand-alone closure or escalation notifications.
            if t.kind != "START":
                start_ack = outbox.get(_event_key(ident, "START", None))
                if start_ack is None or start_ack.status != "SENT":
                    decisions.append(Decision("SUPPRESSED", key, ident, "START not confirmed delivered"))
                    continue
            occurred = c.closed_at_utc if t.kind == "CLOSE" else t.interval_start_utc + THREE_HOURS
            if occurred is None or occurred > as_of or as_of - occurred > MAX_EVENT_LAG:
                decisions.append(Decision("SUPPRESSED", key, ident, "historical/stale transition"))
                continue
            if not history.bars or t.interval_start_utc not in {b.interval_start_utc for b in history.bars}:
                decisions.append(Decision("SUPPRESSED", key, ident, "no fresh verified transition observation"))
                continue
            outbox[key] = OutboxEntry(key, ident, t.kind, t.g_level)
            decisions.append(Decision("PROPOSED", key, ident, "offline outbox proposal only"))
    for ident, inc in list(incidents.items()):
        if ident not in matched_ids and ident in incumbents:
            incidents[ident] = replace(inc, review_required=True)
            decisions.append(Decision("REVIEW", None, ident, "previous incident no longer reconstructable"))
    result = replace(previous, next_incident_seq=seq, intervals=intervals,
                     incidents=tuple(sorted(incidents.values(), key=lambda i: i.incident_id)),
                     outbox=tuple(sorted(outbox.values(), key=lambda x: x.event_key)))
    _validate_state(result)
    if result != previous:
        result = replace(result, version=previous.version + 1)
    return ReconcileResult(result, peak, tuple(decisions), any(i.review_required for i in result.incidents))


def record_delivery(
    previous: GeomagneticState, *, expected_version: int, event_key: str,
    status: Literal["SENT", "UNCERTAIN"], telegram_message_id: int | None = None,
) -> GeomagneticState:
    """Pure receipt reducer: SENT requires trusted ACK; never resend UNCERTAIN.

    This does NOT send Telegram or prove a supplied acknowledgement is genuine.
    """
    _validate_state(previous)
    if type(expected_version) is not int or previous.version != expected_version:
        raise StaleVersion("state revision mismatch")
    existing = next((ev for ev in previous.outbox if ev.event_key == event_key), None)
    if existing is None or status not in ("SENT", "UNCERTAIN"):
        raise StateError("unknown event or invalid delivery status")
    if status == "SENT":
        if type(telegram_message_id) is not int or telegram_message_id <= 0:
            raise StateError("positive verified Telegram message ID required")
        if existing.status == "SENT":
            if existing.telegram_message_id != telegram_message_id:
                raise StateError("immutable sent receipt conflict")
            return previous
        replacement = replace(existing, status="SENT", telegram_message_id=telegram_message_id)
    else:
        if telegram_message_id is not None or existing.status == "SENT":
            raise StateError("cannot overwrite sent evidence")
        if existing.status == "UNCERTAIN":
            return previous
        replacement = replace(existing, status="UNCERTAIN")
    outbox = tuple(sorted((replacement if x.event_key == event_key else x for x in previous.outbox),
                          key=lambda x: x.event_key))
    updated = replace(previous, version=previous.version + 1, outbox=outbox)
    _validate_state(updated)
    return updated


__all__ = (
    "SCHEMA_VERSION", "MAX_INTERVALS", "MAX_EVENT_LAG", "StateError", "StaleVersion",
    "IntervalRecord", "IncidentRecord", "OutboxEntry", "GeomagneticState", "Decision",
    "ReconcileResult", "reconcile", "record_delivery",
)
