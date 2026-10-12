"""Pure, offline NOAA SWPC observed-3h Kp history and incident candidates.

No network, Telegram, persistence, system clock or filesystem interaction.
The caller MUST supply an authenticated/trusted observed-3h product provenance;
JSON content by itself cannot prove it originated from NOAA. The `time_tag`
field represents the START of a UTC three-hour interval, not the end.

This module intentionally does not implement user-facing notifications or an
exactly-once event ledger. Incident IDs are deterministic *candidates* and can
change when earlier observations are backfilled; publication needs persistent
identity reconciliation and an independently reviewed delivery protocol.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Mapping


OBSERVED_SOURCE = "swpc_observed_3h"
THREE_HOURS = timedelta(hours=3)
WINDOW_24H = timedelta(hours=24)
FUTURE_SKEW = timedelta(minutes=10)
EXPECTED_BARS = 8
Coverage = Literal["COMPLETE", "PARTIAL", "UNKNOWN"]
IncidentState = Literal["OPEN", "CLOSED"]
TransitionKind = Literal["START", "ESCALATE", "CLOSE"]

# Valid NOAA UTC representations. A naive time_tag is UTC by the existing KLD
# source contract. Explicit non-UTC offsets are not accepted by this history
# engine, rather than silently converting a third-party local timestamp.
_NOAA_UTC_TAG = re.compile(
    r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]00:00)?$"
)


@dataclass(frozen=True, slots=True)
class ObservedBar:
    interval_start_utc: datetime
    interval_end_utc: datetime
    kp_raw: float
    g_level: str | None
    source: str = OBSERVED_SOURCE


@dataclass(frozen=True, slots=True)
class ParsedHistory:
    as_of_utc: datetime
    bars: tuple[ObservedBar, ...]
    rejected: tuple[tuple[str, int], ...]
    duplicate_count: int
    conflicting_interval_starts_utc: tuple[datetime, ...]
    source: str = OBSERVED_SOURCE

    def rejection_counts(self) -> dict[str, int]:
        """A new dictionary, so callers cannot mutate internal result state."""
        return dict(self.rejected)


@dataclass(frozen=True, slots=True)
class Rolling24hPeak:
    as_of_utc: datetime
    window_start_utc: datetime
    coverage: Coverage
    observed_count: int
    expected_count: int
    missing_interval_starts_utc: tuple[datetime, ...]
    conflicting_interval_starts_utc: tuple[datetime, ...]
    peak_kp_raw: float | None
    peak_g_level: str | None
    peak_interval_start_utc: datetime | None
    source: str = OBSERVED_SOURCE


@dataclass(frozen=True, slots=True)
class IncidentTransition:
    kind: TransitionKind
    interval_start_utc: datetime
    kp_raw: float
    g_level: str | None


@dataclass(frozen=True, slots=True)
class IncidentCandidate:
    candidate_id: str
    first_storm_interval_start_utc: datetime
    last_storm_interval_end_utc: datetime
    peak_kp_raw: float
    peak_g_level: str
    state: IncidentState
    closed_at_utc: datetime | None
    continuity_unknown: bool
    transitions: tuple[IncidentTransition, ...]
    source: str = OBSERVED_SOURCE


def _utc_as_of(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("as_of_utc must be a UTC-aware datetime")
    return value.astimezone(timezone.utc)


def _finite_kp(raw: Any) -> float | None:
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = float(raw.strip().replace(",", ".")) if isinstance(raw, str) else float(raw)
    except (ValueError, TypeError, OverflowError, AttributeError):
        return None
    if not math.isfinite(value) or not 0.0 <= value <= 9.0:
        return None
    return value


def kp_g_level(raw: Any) -> str | None:
    """NOAA G1–G5 threshold from the unrounded observed Kp, else None."""
    kp = _finite_kp(raw)
    if kp is None or kp < 5.0:
        return None
    if kp >= 9.0:
        return "G5"
    if kp >= 8.0:
        return "G4"
    if kp >= 7.0:
        return "G3"
    if kp >= 6.0:
        return "G2"
    return "G1"


def _parse_utc_tag(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not _NOAA_UTC_TAG.fullmatch(raw.strip()):
        return None
    cleaned = raw.strip().replace(" ", "T")
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(cleaned)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    elif parsed.utcoffset() != timedelta(0):
        return None
    parsed = parsed.astimezone(timezone.utc)
    if parsed.hour % 3 or parsed.minute or parsed.second or parsed.microsecond:
        return None
    return parsed


def _row_provenance_ok(row: Mapping[str, Any]) -> bool:
    # Unlabelled rows are accepted ONLY because the caller has asserted a
    # trusted observed-3h endpoint through the mandatory `source` argument.
    checks = {
        "source": {OBSERVED_SOURCE},
        "kind": {"observed_3h"},
        "type": {"observed", "observed_3h"},
        "product": {"noaa-planetary-k-index", "noaa-planetary-k-index.json"},
        "status": {"observed"},
    }
    return all(
        field not in row or (isinstance(row[field], str) and row[field] in valid)
        for field, valid in checks.items()
    )


def _raw_rows(payload: Any) -> list[tuple[Any, Any, bool]] | None:
    """Current NOAA dict list; legacy table via named header, never last column."""
    if not isinstance(payload, list):
        return None
    if not payload:
        return []
    if isinstance(payload[0], dict):
        result: list[tuple[Any, Any, bool]] = []
        for row in payload:
            if not isinstance(row, dict):
                result.append((None, None, False))
            else:
                result.append((row.get("time_tag"), row.get("Kp"), _row_provenance_ok(row)))
        return result
    if isinstance(payload[0], list):
        header = payload[0]
        if not all(isinstance(s, str) for s in header):
            return None
        names = [s.strip() for s in header]
        if "time_tag" not in names:
            return None
        kp_field = next((name for name in ("Kp", "kp_index") if name in names), None)
        if kp_field is None or len(names) != len(set(names)):
            return None
        ti, ki = names.index("time_tag"), names.index(kp_field)
        return [
            (row[ti], row[ki], True)
            if isinstance(row, list) and len(row) > max(ti, ki)
            else (None, None, False)
            for row in payload[1:]
        ]
    return None


def parse_observed_3h_history(
    payload: Any, *, as_of_utc: datetime, source: str
) -> ParsedHistory:
    """Parse injected trusted NOAA observed-3h payload without reading a clock.

    A row is not discarded merely because it is older than today's 24h
    window: a historical incident may require older observations. Staleness
    for the 24-hour peak is enforced by `rolling_24h_peak`.

    A disagreement on the same time_tag invalidates that WHOLE interval;
    choosing a convenient value would fabricate severity. Exact repeats
    are collapsed. All results are deterministic under input reordering.
    """
    as_of = _utc_as_of(as_of_utc)
    if source != OBSERVED_SOURCE:
        raise ValueError("Only trusted swpc_observed_3h payloads are accepted")
    rows = _raw_rows(payload)
    counts: dict[str, int] = {}

    def reject(reason: str) -> None:
        counts[reason] = counts.get(reason, 0) + 1

    if rows is None:
        reject("invalid_payload")
        rows = []

    values: dict[datetime, set[float]] = {}
    duplicates = 0
    for tag, raw_kp, allowed in rows:
        if not allowed:
            reject("invalid_provenance")
            continue
        start = _parse_utc_tag(tag)
        if start is None:
            reject("invalid_time_or_alignment")
            continue
        if start > as_of + FUTURE_SKEW:
            reject("future")
            continue
        kp = _finite_kp(raw_kp)
        if kp is None:
            reject("invalid_kp")
            continue
        seen = values.setdefault(start, set())
        if kp in seen:
            duplicates += 1
        seen.add(kp)

    conflicts = tuple(sorted(start for start, seen in values.items() if len(seen) != 1))
    bars = tuple(
        ObservedBar(start, start + THREE_HOURS, next(iter(vals)), kp_g_level(next(iter(vals))))
        for start, vals in sorted(values.items())
        if len(vals) == 1
    )
    return ParsedHistory(
        as_of_utc=as_of,
        bars=bars,
        rejected=tuple(sorted(counts.items())),
        duplicate_count=duplicates,
        conflicting_interval_starts_utc=conflicts,
    )


def rolling_24h_peak(history: ParsedHistory) -> Rolling24hPeak:
    """Max Kp over exactly eight latest completed UTC 3-hour intervals.

    Inclusion rule: `as_of_utc - 24h < interval_end <= as_of_utc`.
    COMPLETE means exactly all eight expected *observed* slots exist.
    PARTIAL reports a maximum of observed slots only and never claims to
    know the maximum of missing intervals. UNKNOWN has no eligible bars.
    """
    as_of = _utc_as_of(history.as_of_utc)
    floor_hour = (as_of.hour // 3) * 3
    last_end = as_of.replace(hour=floor_hour, minute=0, second=0, microsecond=0)
    expected = tuple(last_end - THREE_HOURS * i - THREE_HOURS for i in range(EXPECTED_BARS - 1, -1, -1))
    window_start = as_of - WINDOW_24H
    accepted = {bar.interval_start_utc: bar for bar in history.bars
                if window_start < bar.interval_end_utc <= as_of}
    included = tuple(accepted[k] for k in expected if k in accepted)
    missing = tuple(k for k in expected if k not in accepted)
    conflicts = tuple(k for k in history.conflicting_interval_starts_utc if k in missing)
    coverage: Coverage = "COMPLETE" if not missing else "PARTIAL" if included else "UNKNOWN"
    peak = max(included, key=lambda bar: (bar.kp_raw, bar.interval_start_utc)) if included else None
    return Rolling24hPeak(
        as_of_utc=as_of,
        window_start_utc=window_start,
        coverage=coverage,
        observed_count=len(included),
        expected_count=EXPECTED_BARS,
        missing_interval_starts_utc=missing,
        conflicting_interval_starts_utc=conflicts,
        peak_kp_raw=peak.kp_raw if peak else None,
        peak_g_level=peak.g_level if peak else None,
        peak_interval_start_utc=peak.interval_start_utc if peak else None,
    )


def incident_candidates(history: ParsedHistory) -> tuple[IncidentCandidate, ...]:
    """Reconstruct candidate incidents from completed observed bars only.

    First observed Kp>=5 starts an incident. Higher NEW maximum G produces
    an escalation; equal/lower/repeated G does not. Two CONSECUTIVE recorded
    below-G1 bars close it. A missing 3h slot breaks calm streak and marks
    continuity UNKNOWN, never implying calm or a new incident. These are
    observations, NOT evidence that the event is ongoing at evaluation time.
    """
    as_of = _utc_as_of(history.as_of_utc)
    bars = [b for b in history.bars if b.interval_end_utc <= as_of]
    results: list[IncidentCandidate] = []
    active: dict[str, Any] | None = None
    previous_end: datetime | None = None
    calm_streak = 0

    def close(state: IncidentState, closed_at: datetime | None) -> None:
        assert active is not None
        results.append(IncidentCandidate(
            candidate_id=active["id"],
            first_storm_interval_start_utc=active["first"],
            last_storm_interval_end_utc=active["last"],
            peak_kp_raw=active["peak"],
            peak_g_level=active["peak_g"],
            state=state,
            closed_at_utc=closed_at,
            continuity_unknown=active["uncertain"],
            transitions=tuple(active["transitions"]),
        ))

    for bar in bars:
        if active is not None and previous_end is not None and bar.interval_start_utc != previous_end:
            active["uncertain"] = True
            calm_streak = 0
        if bar.g_level is not None:
            if active is None:
                start = bar.interval_start_utc
                active = {
                    "id": "geomag:" + start.strftime("%Y%m%dT%H%M%SZ"),
                    "first": start,
                    "last": bar.interval_end_utc,
                    "peak": bar.kp_raw,
                    "peak_g": bar.g_level,
                    "uncertain": False,
                    "transitions": [IncidentTransition("START", start, bar.kp_raw, bar.g_level)],
                }
            else:
                active["last"] = bar.interval_end_utc
                if bar.kp_raw > active["peak"]:
                    if int(bar.g_level[1]) > int(active["peak_g"][1]):
                        active["transitions"].append(
                            IncidentTransition("ESCALATE", bar.interval_start_utc, bar.kp_raw, bar.g_level)
                        )
                    active["peak"] = bar.kp_raw
                    active["peak_g"] = bar.g_level
            calm_streak = 0
        elif active is not None:
            calm_streak += 1
            if calm_streak >= 2:
                active["transitions"].append(
                    IncidentTransition("CLOSE", bar.interval_start_utc, bar.kp_raw, None)
                )
                close("CLOSED", bar.interval_end_utc)
                active = None
                calm_streak = 0
        previous_end = bar.interval_end_utc
    if active is not None:
        close("OPEN", None)
    return tuple(results)


__all__ = (
    "OBSERVED_SOURCE", "ObservedBar", "ParsedHistory", "Rolling24hPeak",
    "IncidentTransition", "IncidentCandidate", "parse_observed_3h_history",
    "kp_g_level", "rolling_24h_peak", "incident_candidates",
)
