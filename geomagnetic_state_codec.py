"""H2-B1 offline, strict, canonical codec for the H2-A geomagnetic state.

No filesystem, network or actual Git operations. A SHA-256 checksum guards against
accidental corruption; it is NOT proof of trusted NOAA origin or writer identity.
Storage is not authoritative merely because this document passes validation.
"""
from __future__ import annotations

from dataclasses import fields
from datetime import datetime, timezone
import hashlib
import json
import math
import re
from typing import Any

from geomagnetic_reconciliation import (
    GeomagneticState, IncidentRecord, IntervalRecord, OutboxEntry, StateError,
    _validate_state,
)

STORAGE_FORMAT_VERSION = 1
MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024
REGION = "kld"
ENVIRONMENTS = frozenset({"production", "test"})
SHA256 = "sha256"
_INCIDENT_ID = re.compile(r"kld-geomag-(\d{8})\Z")
_ALIAS = re.compile(r"geomag:(\d{8}T\d{6}Z)\Z")
_EVENT_TYPE = frozenset({"START", "ESCALATE", "CLOSE"})


class CodecError(StateError):
    """No recoverable storage snapshot may be assumed on decoding failure."""


def _keys(value: Any, expected: set[str], context: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise CodecError(f"invalid {context} fields")
    return value


def _real(value: object) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 9:
        raise CodecError("Kp must be an unrounded finite number in [0,9]")
    return float(value)


def _stamp(dt: datetime) -> str:
    if not isinstance(dt, datetime) or dt.tzinfo is None or dt.utcoffset() != timezone.utc.utcoffset(dt):
        raise CodecError("timestamps must be explicitly UTC-aware")
    dt = dt.astimezone(timezone.utc)
    return dt.isoformat(timespec="microseconds" if dt.microsecond else "seconds").replace("+00:00", "Z")


def _parse_stamp(text: object) -> datetime:
    if not isinstance(text, str) or not text.endswith("Z"):
        raise CodecError("non-canonical UTC timestamp")
    try:
        dt = datetime.fromisoformat(text[:-1] + "+00:00")
    except ValueError as exc:
        raise CodecError("malformed UTC timestamp") from exc
    if _stamp(dt) != text:
        raise CodecError("non-canonical UTC timestamp")
    return dt


def _optional_stamp(dt: datetime | None) -> str | None:
    return None if dt is None else _stamp(dt)


def _parse_optional_stamp(v: Any) -> datetime | None:
    return None if v is None else _parse_stamp(v)


def _json(obj: object) -> bytes:
    try:
        return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                          allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, OverflowError) as exc:
        raise CodecError("non-canonical JSON value") from exc


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CodecError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> None:
    raise CodecError("non-finite JSON number")


def _validate_identity(state: GeomagneticState) -> None:
    """Storage-specific semantic checks beyond the existing H2-A reducer guards."""
    try:
        _validate_state(state)
    except (ValueError, TypeError, AttributeError) as exc:
        raise CodecError("invalid H2-A state invariant") from exc
    if tuple(sorted(state.intervals, key=lambda x: x.interval_start_utc)) != state.intervals:
        raise CodecError("non-canonical interval ordering")
    if tuple(sorted(state.incidents, key=lambda x: x.incident_id)) != state.incidents:
        raise CodecError("non-canonical incident ordering")
    if tuple(sorted(state.outbox, key=lambda x: x.event_key)) != state.outbox:
        raise CodecError("non-canonical outbox ordering")
    seen = set()
    max_id = 0
    for incident in state.incidents:
        match = _INCIDENT_ID.fullmatch(incident.incident_id)
        if match is None:
            raise CodecError("invalid stable incident ID")
        max_id = max(max_id, int(match.group(1)))
        if (not isinstance(incident.review_required, bool) or not isinstance(incident.continuity_unknown, bool)
                or incident.state not in ("OPEN", "CLOSED") or not isinstance(incident.candidate_ids, tuple)
                or tuple(sorted(incident.candidate_ids)) != incident.candidate_ids):
            raise CodecError("invalid incident flags or alias ordering")
        for alias in incident.candidate_ids:
            if not isinstance(alias, str) or _ALIAS.fullmatch(alias) is None:
                raise CodecError("invalid incident alias")
        if tuple(sorted(incident.storm_intervals_utc)) != incident.storm_intervals_utc:
            raise CodecError("storm interval order invalid")
        _parse_stamp(_stamp(incident.first_storm_interval_start_utc))
        _parse_stamp(_stamp(incident.last_storm_interval_end_utc))
        for t in incident.storm_intervals_utc:
            _parse_stamp(_stamp(t))
        if incident.state == "CLOSED" and incident.closed_at_utc is None:
            raise CodecError("CLOSED incident missing close time")
        if incident.state == "OPEN" and incident.closed_at_utc is not None:
            raise CodecError("OPEN incident has close time")
        if incident.closed_at_utc is not None:
            _parse_stamp(_stamp(incident.closed_at_utc))
        if incident.first_storm_interval_start_utc > incident.last_storm_interval_end_utc:
            raise CodecError("invalid incident time span")
        seen.add(incident.incident_id)
    if max_id >= state.next_incident_seq:
        raise CodecError("next incident sequence may reuse prior identity")
    for row in state.intervals:
        _parse_stamp(_stamp(row.interval_start_utc))
        if not isinstance(row.observed_values, tuple):
            raise CodecError("invalid interval tuple")
        for kp in row.observed_values:
            _real(kp)
    for event in state.outbox:
        if event.incident_id not in seen or event.transition not in _EVENT_TYPE:
            raise CodecError("event refers to missing incident")
        if event.transition == "CLOSE":
            if event.g_level is not None:
                raise CodecError("CLOSE cannot carry G level")
        elif event.g_level not in {"G1", "G2", "G3", "G4", "G5"}:
            raise CodecError("missing event G level")
        expected = f"{event.incident_id}:{event.transition}"
        if event.transition == "ESCALATE":
            expected += f":{event.g_level}"
        if event.event_key != expected:
            raise CodecError("outbox key mismatch")


def validate_transition(old: GeomagneticState, new: GeomagneticState) -> bool:
    """Reject irreversible identity/receipt loss. Return True only for a change."""
    _validate_identity(old)
    _validate_identity(new)
    if old == new:
        return False
    if new.version != old.version + 1 or new.next_incident_seq < old.next_incident_seq:
        raise CodecError("state revision/counter regression")
    old_events = {x.event_key: x for x in old.outbox}
    new_events = {x.event_key: x for x in new.outbox}
    for key, event in old_events.items():
        future = new_events.get(key)
        if future is None or (future.incident_id, future.transition, future.g_level) != (
                event.incident_id, event.transition, event.g_level):
            raise CodecError("outbox identity lost or changed")
        if event.status == "SENT" and future != event:
            raise CodecError("immutable SENT evidence lost")
        if event.status == "UNCERTAIN" and future.status not in ("UNCERTAIN", "SENT"):
            raise CodecError("UNCERTAIN outcome cannot become retryable")
        if event.status == "PENDING" and future.status not in ("PENDING", "UNCERTAIN", "SENT"):
            raise CodecError("invalid outbox transition")
    previous = {x.incident_id: x for x in old.incidents}
    updated = {x.incident_id: x for x in new.incidents}
    for key, value in previous.items():
        now = updated.get(key)
        if now is None or not set(value.candidate_ids).issubset(now.candidate_ids) or not set(value.storm_intervals_utc).issubset(now.storm_intervals_utc):
            raise CodecError("incident identity/aliases lost")
    # Conflict is sticky and interval history cannot disappear silently (pruning is a separate stage).
    new_intervals = {x.interval_start_utc: x for x in new.intervals}
    for before in old.intervals:
        after = new_intervals.get(before.interval_start_utc)
        if after is None:
            raise CodecError("interval lost without retention authorization")
        if before.status == "CONFLICT" and (after.status != "CONFLICT" or not set(before.observed_values).issubset(after.observed_values)):
            raise CodecError("interval conflict cannot be silently erased")
        if before.status == "OBSERVED" and (after.status == "OBSERVED" and after.observed_values != before.observed_values or
                                             after.status == "CONFLICT" and before.observed_values[0] not in after.observed_values):
            raise CodecError("verified observation changed without conflict")
    return True


def _state_payload(state: GeomagneticState) -> dict[str, Any]:
    _validate_identity(state)
    return {
        "schema_version": state.schema_version,
        "version": state.version,
        "next_incident_seq": state.next_incident_seq,
        "intervals": [{"interval_start_utc": _stamp(x.interval_start_utc), "observed_values": list(x.observed_values),
                       "status": x.status, "source": x.source} for x in state.intervals],
        "incidents": [{"incident_id": x.incident_id, "candidate_ids": list(x.candidate_ids),
                       "storm_intervals_utc": [_stamp(t) for t in x.storm_intervals_utc],
                       "first_storm_interval_start_utc": _stamp(x.first_storm_interval_start_utc),
                       "last_storm_interval_end_utc": _stamp(x.last_storm_interval_end_utc),
                       "peak_kp_raw": x.peak_kp_raw, "peak_g_level": x.peak_g_level,
                       "state": x.state, "closed_at_utc": _optional_stamp(x.closed_at_utc),
                       "continuity_unknown": x.continuity_unknown,
                       "review_required": x.review_required} for x in state.incidents],
        "outbox": [{"event_key": x.event_key, "incident_id": x.incident_id,
                    "transition": x.transition, "g_level": x.g_level,
                    "status": x.status, "telegram_message_id": x.telegram_message_id} for x in state.outbox],
    }


def encode_state(state: GeomagneticState, *, region: str = REGION, environment: str) -> bytes:
    if region != REGION or environment not in ENVIRONMENTS:
        raise CodecError("invalid region/environment namespace")
    payload = {"storage_format_version": STORAGE_FORMAT_VERSION, "region": region,
               "environment": environment, "state": _state_payload(state)}
    digest = hashlib.sha256(_json(payload)).hexdigest()
    document = {"payload": payload, "integrity": {"algorithm": SHA256, "digest": digest}}
    encoded = _json(document)
    if len(encoded) > MAX_SNAPSHOT_BYTES:
        raise CodecError("oversized snapshot")
    return encoded


def _list(v: Any, label: str) -> list[Any]:
    if not isinstance(v, list):
        raise CodecError(f"invalid {label} array")
    return v


def _strict_int(v: Any, label: str, *, allow_none: bool = False) -> int | None:
    if allow_none and v is None:
        return None
    if type(v) is not int:
        raise CodecError(f"invalid {label} integer")
    return v


def _decode_payload(payload: dict[str, Any]) -> GeomagneticState:
    raw = _keys(payload, {"schema_version", "version", "next_incident_seq", "intervals", "incidents", "outbox"}, "state")
    intervals = []
    for row in _list(raw["intervals"], "intervals"):
        x = _keys(row, {"interval_start_utc", "observed_values", "status", "source"}, "interval")
        intervals.append(IntervalRecord(_parse_stamp(x["interval_start_utc"]),
                                        tuple(_real(v) for v in _list(x["observed_values"], "Kp")),
                                        x["status"], x["source"]))
    incidents = []
    for row in _list(raw["incidents"], "incidents"):
        x = _keys(row, {f.name for f in fields(IncidentRecord)}, "incident")
        if type(x["continuity_unknown"]) is not bool or type(x["review_required"]) is not bool:
            raise CodecError("invalid incident boolean")
        incidents.append(IncidentRecord(
            incident_id=x["incident_id"], candidate_ids=tuple(_list(x["candidate_ids"], "aliases")),
            storm_intervals_utc=tuple(_parse_stamp(t) for t in _list(x["storm_intervals_utc"], "storm slots")),
            first_storm_interval_start_utc=_parse_stamp(x["first_storm_interval_start_utc"]),
            last_storm_interval_end_utc=_parse_stamp(x["last_storm_interval_end_utc"]),
            peak_kp_raw=_real(x["peak_kp_raw"]), peak_g_level=x["peak_g_level"],
            state=x["state"], closed_at_utc=_parse_optional_stamp(x["closed_at_utc"]),
            continuity_unknown=x["continuity_unknown"], review_required=x["review_required"]))
    outbox = []
    for row in _list(raw["outbox"], "outbox"):
        x = _keys(row, {f.name for f in fields(OutboxEntry)}, "outbox")
        outbox.append(OutboxEntry(x["event_key"], x["incident_id"], x["transition"], x["g_level"],
                                  x["status"], _strict_int(x["telegram_message_id"], "message ID", allow_none=True)))
    state = GeomagneticState(
        schema_version=_strict_int(raw["schema_version"], "schema"),
        version=_strict_int(raw["version"], "version"),
        next_incident_seq=_strict_int(raw["next_incident_seq"], "incident sequence"),
        intervals=tuple(intervals), incidents=tuple(incidents), outbox=tuple(outbox))
    _validate_identity(state)
    return state


def decode_state(data: bytes, *, expected_region: str = REGION, expected_environment: str) -> GeomagneticState:
    if expected_region != REGION or expected_environment not in ENVIRONMENTS:
        raise CodecError("invalid expected namespace")
    if not isinstance(data, bytes) or not data or len(data) > MAX_SNAPSHOT_BYTES:
        raise CodecError("missing or oversized snapshot")
    try:
        doc = json.loads(data.decode("utf-8"), object_pairs_hook=_unique_object,
                         parse_constant=_reject_nonfinite)
    except (UnicodeError, ValueError, RecursionError, OverflowError) as exc:
        raise CodecError("unreadable JSON") from exc
    _keys(doc, {"payload", "integrity"}, "document")
    payload = _keys(doc["payload"], {"storage_format_version", "region", "environment", "state"}, "payload")
    identity = _keys(doc["integrity"], {"algorithm", "digest"}, "integrity")
    if type(payload["storage_format_version"]) is not int or payload["storage_format_version"] != STORAGE_FORMAT_VERSION:
        raise CodecError("unsupported storage schema")
    if payload["region"] != expected_region or payload["environment"] != expected_environment:
        raise CodecError("snapshot namespace mismatch")
    if identity["algorithm"] != SHA256 or not isinstance(identity["digest"], str) or re.fullmatch(r"[0-9a-f]{64}", identity["digest"]) is None:
        raise CodecError("invalid checksum algorithm or digest")
    if hashlib.sha256(_json(payload)).hexdigest() != identity["digest"]:
        raise CodecError("snapshot integrity mismatch")
    state = _decode_payload(payload["state"])
    if encode_state(state, region=expected_region, environment=expected_environment) != data:
        raise CodecError("non-canonical serialized snapshot")
    return state


__all__ = (
    "STORAGE_FORMAT_VERSION", "MAX_SNAPSHOT_BYTES", "REGION", "ENVIRONMENTS", "CodecError",
    "encode_state", "decode_state", "validate_transition",
)
