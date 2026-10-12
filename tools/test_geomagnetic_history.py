"""Isolated offline tests for geomagnetic_history.py. No third-party deps.

These tests are independent of the existing KLD regression suite; they do not
claim to establish end-to-end workflow, NOAA transport or Telegram behavior.
"""

from __future__ import annotations

import ast
import builtins
import os
import pathlib
import socket
import sys
from datetime import datetime, timedelta, timezone

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from geomagnetic_history import (  # noqa: E402
    OBSERVED_SOURCE,
    incident_candidates,
    kp_g_level,
    parse_observed_3h_history,
    rolling_24h_peak,
)

UTC = timezone.utc
T = datetime(2026, 10, 12, 12, tzinfo=UTC)
NETWORK_ATTEMPTS: list[str] = []


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch):
    """Always block egress; any attempt immediately fails this test."""
    def deny(*_args, **_kwargs):
        NETWORK_ATTEMPTS.append("blocked")
        raise AssertionError("Offline test attempted a network connection")
    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket.socket, "connect_ex", deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    yield


def tag(start: datetime, *, use_z: bool = False) -> str:
    assert start.utcoffset() == timedelta(0)
    return start.strftime("%Y-%m-%dT%H:%M:%S") + ("Z" if use_z else "")


def row(start: datetime, kp: object, **metadata: object) -> dict[str, object]:
    return {"time_tag": tag(start), "Kp": kp, **metadata}


def parse(rows: object, as_of: datetime = T):
    return parse_observed_3h_history(rows, as_of_utc=as_of, source=OBSERVED_SOURCE)


def complete_rows(end: datetime = T, *, values: list[float] | None = None):
    values = values or [0, 1, 2, 3, 4, 5, 6, 7]
    return [row(end - timedelta(hours=24 - i * 3), value) for i, value in enumerate(values)]


@pytest.mark.parametrize("value,level", [
    (0.0, None), (4.99, None), (5.0, "G1"), (5.99, "G1"),
    (6.0, "G2"), (6.67, "G2"), (6.999, "G2"), (7.0, "G3"),
    (7.99, "G3"), (8.0, "G4"), (8.67, "G4"), (8.999, "G4"),
    (9.0, "G5"),
])
def test_exact_g_thresholds(value, level):
    assert kp_g_level(value) == level


@pytest.mark.parametrize("value", [None, False, True, -0.1, 9.01, "garbage", float("nan"), float("inf"), -float("inf"), [], {}])
def test_invalid_severity_is_never_classified(value):
    assert kp_g_level(value) is None


def test_fractional_raw_kp_preserved_without_rounding():
    parsed = parse([row(T - timedelta(hours=3), 6.67)])
    assert parsed.bars[0].kp_raw == 6.67
    assert parsed.bars[0].g_level == "G2"
    peak = rolling_24h_peak(parsed)
    assert peak.peak_kp_raw == 6.67
    assert peak.peak_g_level == "G2"


def test_true_zero_is_valid_observation_not_missing():
    observed = parse([row(T - timedelta(hours=3), 0.0)])
    assert observed.bars[0].kp_raw == 0.0
    assert observed.bars[0].g_level is None
    peak = rolling_24h_peak(observed)
    assert peak.coverage == "PARTIAL" and peak.peak_kp_raw == 0.0


def test_complete_window_eight_bars_with_utc_midnight():
    observed = parse(complete_rows())
    peak = rolling_24h_peak(observed)
    assert peak.coverage == "COMPLETE"
    assert peak.observed_count == peak.expected_count == 8
    assert peak.missing_interval_starts_utc == ()
    assert peak.peak_kp_raw == 7
    assert peak.peak_g_level == "G3"
    assert peak.peak_interval_start_utc == T - timedelta(hours=3)
    assert peak.window_start_utc == T - timedelta(hours=24)


def test_exact_window_lower_bound_excludes_older_completed_bar():
    rows = complete_rows() + [row(T - timedelta(hours=27), 9.0)]
    observed = parse(rows)
    assert len(observed.bars) == 9
    peak = rolling_24h_peak(observed)
    assert peak.coverage == "COMPLETE"
    assert peak.peak_kp_raw == 7.0


def test_current_open_bar_is_not_in_peak_or_incident():
    observed = parse(complete_rows(values=[0.0] * 8) + [row(T, 9.0)])
    assert rolling_24h_peak(observed).peak_kp_raw == 0
    assert incident_candidates(observed) == ()


def test_mid_interval_window_uses_exact_eight_latest_completed_bars():
    as_of = T - timedelta(hours=1, minutes=45)  # 10:15 UTC
    latest_end = T - timedelta(hours=3)  # 09:00 UTC
    observed = parse(complete_rows(end=latest_end), as_of=as_of)
    peak = rolling_24h_peak(observed)
    assert peak.coverage == "COMPLETE"
    assert peak.window_start_utc == as_of - timedelta(hours=24)
    assert peak.peak_interval_start_utc == latest_end - timedelta(hours=3)


def test_missing_one_interval_is_partial_not_falsely_complete():
    rows = complete_rows()
    observed = parse(rows[:4] + rows[5:])
    peak = rolling_24h_peak(observed)
    assert peak.coverage == "PARTIAL"
    assert peak.observed_count == 7
    assert peak.missing_interval_starts_utc == (T - timedelta(hours=12),)


def test_no_observed_bars_is_unknown_not_calm():
    peak = rolling_24h_peak(parse([]))
    assert peak.coverage == "UNKNOWN"
    assert peak.peak_kp_raw is None
    assert peak.peak_g_level is None
    assert len(peak.missing_interval_starts_utc) == 8


def test_stale_historical_bars_do_not_become_current_peak():
    observed = parse([row(T - timedelta(hours=48), 9)])
    peak = rolling_24h_peak(observed)
    assert peak.coverage == "UNKNOWN" and peak.peak_kp_raw is None
    assert len(observed.bars) == 1  # retained for honest historic reconstruction


def test_latest_equal_peaks_have_deterministic_later_interval():
    rows = complete_rows(values=[0, 7, 0, 7, 0, 0, 0, 0])
    observed = parse(list(reversed(rows)))
    assert rolling_24h_peak(observed).peak_interval_start_utc == T - timedelta(hours=15)


def test_unsorted_rows_are_sorted_deterministically():
    rows = complete_rows()
    straight = parse(rows)
    reversed_rows = parse(list(reversed(rows)))
    assert straight == reversed_rows
    assert incident_candidates(straight) == incident_candidates(reversed_rows)


def test_identical_duplicates_collapsed_without_changing_peak():
    rows = complete_rows()
    parsed = parse(rows + [rows[-1], rows[-1]])
    assert parsed.duplicate_count == 2
    assert len(parsed.bars) == 8
    assert rolling_24h_peak(parsed).coverage == "COMPLETE"


def test_conflicting_same_timestamp_is_excluded_as_missing_even_at_g5():
    rows = complete_rows()
    start = T - timedelta(hours=3)
    observed = parse(rows + [row(start, 9)])
    assert observed.conflicting_interval_starts_utc == (start,)
    peak = rolling_24h_peak(observed)
    assert peak.coverage == "PARTIAL"
    assert peak.peak_kp_raw == 6
    assert peak.conflicting_interval_starts_utc == (start,)
    assert start in peak.missing_interval_starts_utc
    assert parse(list(reversed(rows + [row(start, 9)]))).conflicting_interval_starts_utc == (start,)


def test_non_utc_time_tag_offsets_and_non_aligned_timestamps_rejected():
    invalid = [
        {"time_tag": "2026-10-12T09:00:00+03:00", "Kp": 9},
        {"time_tag": "2026-10-12T09:15:00Z", "Kp": 9},
        {"time_tag": "2026-10-12T09:00:01Z", "Kp": 9},
        {"time_tag": "2026-10-12T09:00:00.000001Z", "Kp": 9},
        {"time_tag": "nonsense", "Kp": 9},
        {"time_tag": None, "Kp": 9},
        {"time_tag": 1234, "Kp": 9},
        {"time_tag": "2026-02-30T09:00:00Z", "Kp": 9},
    ]
    parsed = parse(invalid)
    assert parsed.bars == ()
    assert parsed.rejection_counts()["invalid_time_or_alignment"] == len(invalid)


def test_valid_explicit_utc_formats_and_legacy_named_columns():
    current = parse([{"time_tag": "2026-10-12T09:00:00Z", "Kp": "6,67"}])
    assert current.bars[0].kp_raw == 6.67
    assert parse([{"time_tag": "2026-10-12T09:00:00+00:00", "Kp": "0"}]).bars[0].kp_raw == 0
    legacy = parse([["station_count", "Kp", "time_tag"], [99, "7", "2026-10-12 09:00:00"]])
    assert legacy.bars[0].kp_raw == 7.0
    assert legacy.bars[0].interval_start_utc == T - timedelta(hours=3)


@pytest.mark.parametrize("payload", [None, {}, "no", [{"Kp": 7}], [["bad", "header"], [1, 7]], [["time_tag", "time_tag", "Kp"]], [7], 123])
def test_invalid_payload_fails_closed(payload):
    parsed = parse(payload)
    assert parsed.bars == ()
    assert rolling_24h_peak(parsed).coverage == "UNKNOWN"


@pytest.mark.parametrize("bad", ["NaN", "Infinity", True, -1, 9.01, object(), {}, "", None])
def test_invalid_kp_rejected(bad):
    history = parse([row(T - timedelta(hours=3), bad)])
    assert history.bars == ()
    assert history.rejection_counts()["invalid_kp"] == 1


def test_future_beyond_skew_rejected_and_unclosed_never_used():
    as_of = datetime(2026, 10, 12, 11, 55, tzinfo=UTC)
    near_future = parse([row(T, 9.0)], as_of=as_of)
    assert len(near_future.bars) == 1
    assert rolling_24h_peak(near_future).coverage == "UNKNOWN"
    far_future = parse([row(T + timedelta(hours=3), 9)], as_of=as_of)
    assert far_future.bars == ()
    assert far_future.rejection_counts()["future"] == 1


def test_estimated_watch_warning_forecast_never_observed():
    start = T - timedelta(hours=3)
    rows = [
        row(start, 7, source="swpc_estimated_1m"),
        row(start, 7, type="WATCH"),
        row(start, 7, kind="forecast"),
        row(start, 7, status="WARNING"),
        row(start, 7, product="alerts.json"),
    ]
    parsed = parse(rows)
    assert parsed.bars == ()
    assert parsed.rejection_counts()["invalid_provenance"] == 5
    for source in ("swpc_estimated_1m", "WATCH", "WARNING", "forecast", "cache:swpc_observed_3h"):
        with pytest.raises(ValueError):
            parse_observed_3h_history([row(start, 7)], as_of_utc=T, source=source)


def test_no_explicit_trusted_source_is_rejected():
    with pytest.raises(TypeError):
        parse_observed_3h_history([row(T - timedelta(hours=3), 7)], as_of_utc=T)


@pytest.mark.parametrize("as_of", [T.replace(tzinfo=None), "2026-10-12T12:00:00Z", 0, datetime(2026, 10, 12, 15, tzinfo=timezone(timedelta(hours=3)))])
def test_as_of_must_be_explicit_utc_datetime(as_of):
    with pytest.raises(ValueError):
        parse_observed_3h_history([], as_of_utc=as_of, source=OBSERVED_SOURCE)


def test_observed_source_not_mutable_and_results_are_immutable():
    h = parse([row(T - timedelta(hours=3), 7)])
    assert h.source == OBSERVED_SOURCE
    counts = h.rejection_counts()
    counts["fake"] = 9
    assert "fake" not in h.rejection_counts()
    with pytest.raises(AttributeError):
        h.bars = ()


def test_synthetic_oct10_g3_fixture_not_claiming_an_actual_noaa_interval():
    # Synthetic 10 Oct calendar case; the actual G3 interval was NOT verified.
    synthetic_as_of = datetime(2026, 10, 10, 15, tzinfo=UTC)
    synthetic = parse([row(synthetic_as_of - timedelta(hours=3), 7)], as_of=synthetic_as_of)
    peak = rolling_24h_peak(synthetic)
    assert peak.peak_kp_raw == 7
    assert peak.peak_g_level == "G3"
    assert peak.coverage == "PARTIAL"
    assert len(incident_candidates(synthetic)) == 1


def test_incident_start_repeated_storm_and_escalation_only_on_new_max_g():
    start = T - timedelta(hours=18)
    values = [5.0, 7.0, 7.0, 6.0, 8.0, 8.67]
    bars = [row(start + timedelta(hours=3*i), k) for i,k in enumerate(values)]
    incidents = incident_candidates(parse(list(reversed(bars))))
    assert len(incidents) == 1
    inc = incidents[0]
    assert inc.state == "OPEN"
    assert inc.peak_kp_raw == 8.67
    assert inc.peak_g_level == "G4"
    assert [x.kind for x in inc.transitions] == ["START", "ESCALATE", "ESCALATE"]
    assert [x.g_level for x in inc.transitions] == ["G1", "G3", "G4"]
    assert inc.candidate_id == "geomag:" + start.strftime("%Y%m%dT%H%M%SZ")


def test_two_consecutive_quiet_intervals_close_then_new_storm_new_id():
    start = T - timedelta(hours=21)
    vals = [7.0, 3.0, 4.0, 0.0, 6.0, 3.0, 4.0]
    incidents = incident_candidates(parse([row(start + timedelta(hours=3*i), k) for i,k in enumerate(vals)]))
    assert len(incidents) == 2
    assert all(c.state == "CLOSED" for c in incidents)
    assert incidents[0].closed_at_utc == start + timedelta(hours=9)
    assert incidents[1].closed_at_utc == start + timedelta(hours=21)
    assert incidents[0].candidate_id != incidents[1].candidate_id
    assert all([e.kind for e in x.transitions][-1] == "CLOSE" for x in incidents)


def test_one_quiet_bar_then_storm_is_same_incident():
    start = T - timedelta(hours=15)
    vals = [7, 3, 8, 2, 7]
    res = incident_candidates(parse([row(start + timedelta(hours=3*i), k) for i,k in enumerate(vals)]))
    assert len(res) == 1
    assert res[0].state == "OPEN" and res[0].peak_g_level == "G4"


def test_missing_interval_not_calm_and_flags_uncertain_continuity():
    start = T - timedelta(hours=18)
    observed = parse([row(start, 7), row(start + timedelta(hours=6), 4), row(start + timedelta(hours=9), 8)])
    incidents = incident_candidates(observed)
    assert len(incidents) == 1
    assert incidents[0].state == "OPEN"
    assert incidents[0].continuity_unknown is True
    assert incidents[0].peak_g_level == "G4"
    assert [x.kind for x in incidents[0].transitions] == ["START", "ESCALATE"]


def test_two_nonconsecutive_quiet_bars_never_close():
    start = T - timedelta(hours=18)
    data = parse([row(start, 7), row(start + timedelta(hours=3), 4), row(start + timedelta(hours=9), 1)])
    incidents = incident_candidates(data)
    assert len(incidents) == 1
    assert incidents[0].state == "OPEN"
    assert incidents[0].continuity_unknown


def test_conflicted_bar_causes_unknown_continuity_not_false_resolution():
    start = T - timedelta(hours=12)
    observed = parse([row(start, 7), row(start + timedelta(hours=3), 4), row(start + timedelta(hours=3), 9), row(start + timedelta(hours=6), 4)])
    inc = incident_candidates(observed)[0]
    assert inc.continuity_unknown and inc.state == "OPEN"


def test_late_recovery_from_unsorted_missing_history_is_deterministic():
    start = T - timedelta(hours=18)
    recovered = [row(start, 7), row(start + timedelta(hours=3), 7), row(start + timedelta(hours=6), 8)]
    a = incident_candidates(parse(recovered))
    b = incident_candidates(parse(list(reversed(recovered))))
    assert a == b
    assert a[0].peak_g_level == "G4"


def test_candidate_identity_can_change_when_older_eligible_bar_backfilled():
    start = T - timedelta(hours=12)
    old = incident_candidates(parse([row(start + timedelta(hours=3), 7)]))[0]
    new = incident_candidates(parse([row(start, 5), row(start + timedelta(hours=3), 7)]))[0]
    assert old.candidate_id != new.candidate_id
    assert [t.kind for t in new.transitions] == ["START", "ESCALATE"]


def test_safely_refuse_bad_provenance_container_unhashable_fields():
    parsed = parse([row(T - timedelta(hours=3), 9, status=["WATCH"]), row(T - timedelta(hours=6), 8, source={"bad": True})])
    assert parsed.bars == ()
    assert parsed.rejection_counts() == {"invalid_provenance": 2}


def test_pure_engine_has_no_filesystem_network_or_subprocess_calls(monkeypatch):
    import geomagnetic_history as gh

    def deny(*_args, **_kwargs):
        raise AssertionError("production module performed filesystem/subprocess I/O")

    with monkeypatch.context() as m:
        m.setattr(builtins, "open", deny)
        m.setattr(os, "open", deny)
        m.setattr(pathlib.Path, "open", deny)
        m.setattr(pathlib.Path, "write_text", deny)
        rows = complete_rows()
        original = tuple((r["time_tag"], r["Kp"]) for r in rows)
        hist = gh.parse_observed_3h_history(rows, as_of_utc=T, source=OBSERVED_SOURCE)
        assert gh.rolling_24h_peak(hist).coverage == "COMPLETE"
        assert isinstance(gh.incident_candidates(hist), tuple)
        assert tuple((r["time_tag"], r["Kp"]) for r in rows) == original

    # Also check production-module imports statically, without any provider.
    source = (ROOT / "geomagnetic_history.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
    imports += [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
    assert set(imports) <= {"__future__", "math", "re", "dataclasses", "datetime", "typing"}


def test_all_tests_made_zero_network_attempts():
    assert NETWORK_ATTEMPTS == []
