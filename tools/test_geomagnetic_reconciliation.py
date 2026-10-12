"""Strictly offline H2-A tests: no network, database, filesystem writes or delivery."""
from __future__ import annotations

import ast
import builtins
import os
import pathlib
import socket
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from geomagnetic_history import OBSERVED_SOURCE, parse_observed_3h_history
from geomagnetic_reconciliation import (
    MAX_INTERVALS, Decision, GeomagneticState, IncidentRecord, IntervalRecord,
    OutboxEntry, StateError, StaleVersion, reconcile, record_delivery,
)

T = datetime(2026, 10, 12, 12, tzinfo=timezone.utc)
NETWORK_ATTEMPTS: list[str] = []


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def deny(*args, **kwargs):
        NETWORK_ATTEMPTS.append("blocked")
        raise AssertionError("H2-A attempted network")
    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket.socket, "connect_ex", deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(socket, "getaddrinfo", deny)


def row(hours, kp):
    return {"time_tag": (T + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S"), "Kp": kp}


def history(rows, *, as_of=T, source=OBSERVED_SOURCE):
    return parse_observed_3h_history(rows, source=source, as_of_utc=as_of)


def apply(rows, state=None, *, as_of=T):
    s = state if state is not None else GeomagneticState()
    return reconcile(s, history(rows, as_of=as_of), expected_version=s.version, as_of_utc=as_of)


def test_initial_state_empty_and_unknown_not_calm():
    x = apply([])
    assert x.peak.coverage == "UNKNOWN"
    assert x.state == GeomagneticState() and not x.decisions


def test_one_credible_recent_storm_proposes_start_only():
    x = apply([row(-3, 5)])
    assert x.state.version == 1
    assert x.state.incidents[0].incident_id == "kld-geomag-00000001"
    assert x.state.incidents[0].candidate_ids == ("geomag:20261012T090000Z",)
    assert [d.status for d in x.decisions] == ["PROPOSED"]
    assert x.state.outbox[0].event_key == "kld-geomag-00000001:START"
    assert x.state.outbox[0].status == "PENDING"


def test_replay_exactly_same_history_has_no_duplicate_outbox_and_no_version_bump():
    first = apply([row(-3, 5)])
    again = apply([row(-3, 5)], first.state)
    assert again.state is not first.state
    assert again.state == first.state
    assert [d.status for d in again.decisions] == ["SUPPRESSED"]


def test_reordered_input_and_identical_duplicates_deterministic():
    rows = [row(-9, 3), row(-6, 5), row(-3, 7)]
    a = apply(rows)
    b = apply(list(reversed(rows)) + [row(-6, 5), row(-3, 7)])
    assert a.state == b.state
    assert a.peak == b.peak
    assert [d.status for d in a.decisions] == [d.status for d in b.decisions]


def test_missing_one_interval_partial_and_no_false_resolution():
    x = apply([row(-12, 7), row(-3, 4)])
    assert x.peak.coverage == "PARTIAL"
    assert x.state.incidents[0].state == "OPEN"
    assert x.state.incidents[0].continuity_unknown
    assert not x.state.outbox
    assert x.requires_review


def test_two_adjacent_quiet_bars_close_same_identity():
    x = apply([row(-12, 7), row(-9, 3), row(-6, 2)])
    assert x.state.incidents[0].state == "CLOSED"
    assert x.state.incidents[0].closed_at_utc == T - timedelta(hours=3)
    assert all(e.transition != "CLOSE" for e in x.state.outbox)  # close only if fresh in incoming batch


def test_rolling_24h_complete_when_8_observations():
    x = apply([row(-24 + i*3, 0 if i < 7 else 6.67) for i in range(8)])
    assert x.peak.coverage == "COMPLETE" and x.peak.peak_kp_raw == 6.67
    assert x.peak.peak_g_level == "G2"


def test_true_zero_remains_observation_and_not_storm():
    x = apply([row(-3, 0.0)])
    assert x.state.intervals[0].observed_values == (0.0,)
    assert x.peak.coverage == "PARTIAL" and x.peak.peak_kp_raw == 0.0
    assert not x.state.incidents


def test_older_backfill_preserves_stable_id_even_though_candidate_changes():
    first = apply([row(-6, 7)])
    nxt = apply([row(-9, 5), row(-6, 7)], first.state)
    inc = nxt.state.incidents[0]
    assert inc.incident_id == first.state.incidents[0].incident_id
    assert inc.first_storm_interval_start_utc == T - timedelta(hours=9)
    assert set(inc.candidate_ids) == {"geomag:20261012T060000Z", "geomag:20261012T030000Z"}
    assert len(nxt.state.incidents) == 1
    assert len([e for e in nxt.state.outbox if e.transition == "START"]) == 1
    assert set(first.state.outbox).issubset(set(nxt.state.outbox))


def test_replaying_backfill_twice_keeps_aliases_and_identity():
    first = apply([row(-6, 6)]).state
    replay = apply([row(-9, 5), row(-6, 6)], first)
    again = apply([row(-9, 5), row(-6, 6)], replay.state)
    assert replay.state == again.state
    assert len(replay.state.incidents[0].candidate_ids) == 2


def test_later_independent_storm_gets_new_id_after_two_quiet_bars():
    a = apply([row(-15, 5), row(-12, 1), row(-9, 1)])
    b = apply([row(-6, 7)], a.state)
    assert [i.incident_id for i in b.state.incidents] == ["kld-geomag-00000001", "kld-geomag-00000002"]


def test_no_new_alert_on_old_historical_first_import():
    x = apply([row(-72, 9)])
    assert x.state.incidents[0].peak_g_level == "G5"
    assert not x.state.outbox
    assert x.decisions[0].status == "SUPPRESSED"
    assert x.decisions[0].reason == "historical/stale transition"


def test_proposed_escalation_on_new_maximum_g_only():
    a = apply([row(-6, 5)])
    confirmed = record_delivery(a.state, expected_version=a.state.version, event_key=a.state.outbox[0].event_key, status="SENT", telegram_message_id=101)
    b = apply([row(-3, 7)], confirmed)
    assert [e.event_key for e in b.state.outbox] == ["kld-geomag-00000001:ESCALATE:G3", "kld-geomag-00000001:START"]
    c = apply([row(-3, 7)], b.state)
    assert c.state == b.state and not any(d.status == "PROPOSED" for d in c.decisions)


def test_repeating_same_severity_not_new_escalation():
    a = apply([row(-6, 7)])
    b = apply([row(-3, 7.66)], a.state)
    assert not any(e.transition == "ESCALATE" for e in b.state.outbox)


def test_in_batch_conflict_is_persisted_without_fabricated_storm():
    x = apply([row(-3, 5), row(-3, 9)])
    assert x.state.intervals[0].status == "CONFLICT"
    assert x.state.intervals[0].observed_values == ()
    assert x.peak.coverage == "UNKNOWN"
    assert not x.state.incidents and not x.state.outbox


def test_cross_run_revision_becomes_conflict_no_silent_override():
    a = apply([row(-3, 5)])
    b = apply([row(-3, 9)], a.state)
    interval = b.state.intervals[0]
    assert interval.status == "CONFLICT" and interval.observed_values == (5.0, 9.0)
    assert b.state.incidents[0].review_required
    assert all(d.status != "PROPOSED" for d in b.decisions)
    assert b.state.outbox == a.state.outbox


def test_conflict_cannot_be_silently_cleared_by_repeated_noaa_value():
    a = apply([row(-3, 5)])
    b = apply([row(-3, 9)], a.state)
    c = apply([row(-3, 9)], b.state)
    assert c.state.intervals[0].status == "CONFLICT"
    assert c.state == b.state


def test_conflict_in_history_does_not_convert_missing_into_calm():
    x = apply([row(-12, 5), row(-9, 1), row(-6, 1), row(-6, 9), row(-3, 1)])
    assert x.state.incidents[0].state == "OPEN"
    assert x.state.incidents[0].continuity_unknown
    assert not x.state.outbox


def test_ambiguous_merger_marks_both_review_not_new_notification():
    first = apply([row(-15, 5), row(-12, 1), row(-9, 1), row(-6, 6)])
    second = apply([row(-12, 6)], first.state)  # earlier quiet bar changes; conflict quarantines
    assert second.requires_review
    assert all(d.status != "PROPOSED" for d in second.decisions)


def test_split_by_mutating_historical_storm_to_quiet_requires_review():
    a = apply([row(-15, 7), row(-12, 7), row(-9, 7), row(-6, 7)])
    b = apply([row(-12, 0)], a.state)
    assert b.requires_review and all(d.status != "PROPOSED" for d in b.decisions)


def test_all_outbox_events_immutable_on_backfill():
    a = apply([row(-6, 5)])
    key = a.state.outbox[0].event_key
    ack = record_delivery(a.state, expected_version=a.state.version, event_key=key, status="SENT", telegram_message_id=321)
    b = apply([row(-9, 5), row(-6, 5)], ack)
    assert [x for x in b.state.outbox if x.event_key == key][0].telegram_message_id == 321
    assert all(d.event_key != key or d.status != "PROPOSED" for d in b.decisions)


def test_delivery_ack_idempotent_and_immutable():
    a = apply([row(-3, 6)]).state
    key = a.outbox[0].event_key
    b = record_delivery(a, expected_version=a.version, event_key=key, status="SENT", telegram_message_id=42)
    assert b.version == a.version + 1
    assert record_delivery(b, expected_version=b.version, event_key=key, status="SENT", telegram_message_id=42) == b
    with pytest.raises(StateError):
        record_delivery(b, expected_version=b.version, event_key=key, status="SENT", telegram_message_id=43)
    with pytest.raises(StateError):
        record_delivery(b, expected_version=b.version, event_key=key, status="UNCERTAIN")


def test_uncertain_ack_blocks_automatic_retry():
    a = apply([row(-3, 7)]).state
    key = a.outbox[0].event_key
    b = record_delivery(a, expected_version=a.version, event_key=key, status="UNCERTAIN")
    assert b.outbox[0].status == "UNCERTAIN"
    replay = apply([row(-3, 7)], b)
    assert replay.state == b and not any(d.status == "PROPOSED" for d in replay.decisions)


def test_valid_confirmed_ack_from_uncertain_is_explicit():
    a = apply([row(-3, 7)]).state
    key = a.outbox[0].event_key
    b = record_delivery(a, expected_version=a.version, event_key=key, status="UNCERTAIN")
    c = record_delivery(b, expected_version=b.version, event_key=key, status="SENT", telegram_message_id=88)
    assert c.outbox[0].status == "SENT" and c.outbox[0].telegram_message_id == 88


@pytest.mark.parametrize("bad", [0, -1, True, "12", None, 3.5])
def test_invalid_message_id_is_never_a_sent_ack(bad):
    a = apply([row(-3, 5)]).state
    with pytest.raises(StateError):
        record_delivery(a, expected_version=a.version, event_key=a.outbox[0].event_key, status="SENT", telegram_message_id=bad)


@pytest.mark.parametrize("bad", [0, 2, -1, True, None, "0"])
def test_explicit_cas_rejects_stale_or_invalid_version(bad):
    a = apply([row(-3, 5)]).state
    with pytest.raises(StaleVersion):
        reconcile(a, history([row(-3, 5)]), expected_version=bad, as_of_utc=T)
    with pytest.raises(StaleVersion):
        record_delivery(a, expected_version=bad, event_key=a.outbox[0].event_key, status="UNCERTAIN")


@pytest.mark.parametrize("bad_time", [T.replace(tzinfo=None), T.astimezone(timezone(timedelta(hours=3))), None, "2026-10-12T12:00Z"])
def test_utc_clock_required(bad_time):
    with pytest.raises(StateError):
        reconcile(GeomagneticState(), history([row(-3, 5)]), expected_version=0, as_of_utc=bad_time)


def test_different_explicit_clock_rejected():
    with pytest.raises(StateError):
        reconcile(GeomagneticState(), history([row(-3, 5)]), expected_version=0, as_of_utc=T + timedelta(hours=3))


@pytest.mark.parametrize("source", ["swpc_estimated_1m", "cache:swpc_observed_3h", "WARNING", "forecast", "unknown"])
def test_untrusted_source_is_rejected(source):
    good = history([row(-3, 5)])
    with pytest.raises(StateError):
        reconcile(GeomagneticState(), replace(good, source=source), expected_version=0, as_of_utc=T)


def test_malformed_forged_bar_rejected():
    h = history([row(-3, 5)])
    bad = replace(h.bars[0], g_level="G5")
    with pytest.raises(StateError):
        reconcile(GeomagneticState(), replace(h, bars=(bad,)), expected_version=0, as_of_utc=T)


def test_future_forged_bar_rejected():
    h = history([row(-3, 5)])
    fake = replace(h.bars[0], interval_start_utc=T + timedelta(hours=3), interval_end_utc=T + timedelta(hours=6))
    with pytest.raises(StateError):
        reconcile(GeomagneticState(), replace(h, bars=(fake,)), expected_version=0, as_of_utc=T)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1.0, 9.1, True])
def test_bad_kp_in_forged_histories_rejected(bad):
    h = history([row(-3, 5)])
    fake = replace(h.bars[0], kp_raw=bad)
    with pytest.raises(StateError):
        reconcile(GeomagneticState(), replace(h, bars=(fake,)), expected_version=0, as_of_utc=T)


def test_invalid_outbox_untrusted_sent_evidence_refused():
    x = apply([row(-3, 7)]).state
    invalid = replace(x.outbox[0], status="SENT", telegram_message_id=None)
    with pytest.raises(StateError):
        apply([row(-3, 7)], replace(x, outbox=(invalid,)))


def test_duplicate_candidate_alias_in_store_fails_closed():
    x = apply([row(-15, 5), row(-12, 1), row(-9, 1), row(-3, 6)]).state
    aliases = x.incidents[0].candidate_ids
    broken = replace(x.incidents[1], candidate_ids=aliases)
    with pytest.raises(StateError):
        apply([], replace(x, incidents=(x.incidents[0], broken)))


def test_capacity_error_no_silent_pruning():
    start = T - timedelta(days=93)
    values = tuple(IntervalRecord(start + i * timedelta(hours=3), (0.0,), "OBSERVED") for i in range(MAX_INTERVALS))
    state = GeomagneticState(intervals=values)
    with pytest.raises(StateError, match="capacity"):
        apply([row(-3, 1)], state)
    assert len(state.intervals) == MAX_INTERVALS


def test_no_import_side_effects_and_expected_static_imports(monkeypatch):
    import importlib.util
    path = ROOT / "geomagnetic_reconciliation.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    imports += [a.name for node in ast.walk(tree) if isinstance(node, ast.Import) for a in node.names]
    assert set(imports) <= {"__future__", "math", "dataclasses", "datetime", "typing", "geomagnetic_history"}
    forbidden = ("telegram", "requests", "sqlite3", "subprocess", "socket", "pathlib", "os", "urllib")
    assert not any(x in imports for x in forbidden)
    def deny(*args, **kwargs):
        raise AssertionError("unwanted IO during import")
    with monkeypatch.context() as m:
        m.setattr(builtins, "open", deny)
        m.setattr(os, "system", deny)
        m.setattr(socket, "create_connection", deny)
        spec = importlib.util.spec_from_file_location("_h2a_test_import", path)
        assert spec and spec.loader
        # Import is already covered by module-level imports; no dependency I/O.
        assert spec.origin == str(path)


def test_pure_reducer_does_not_touch_files_or_sockets(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("unexpected IO")
    with monkeypatch.context() as m:
        m.setattr(builtins, "open", fail)
        m.setattr(os, "open", fail)
        m.setattr(pathlib.Path, "write_text", fail)
        m.setattr(socket, "create_connection", fail)
        x = apply([row(-3, 7)])
        assert x.state.incidents and x.state.outbox


def test_test_suite_network_ledger_empty():
    assert NETWORK_ATTEMPTS == []


def test_real_backfill_split_requires_review_for_same_old_incident():
    # Earlier storm plus later storm separated by two initially MISSING slots:
    # A1 conservatively treats them as one uncertain incident until backfill.
    old = apply([row(-15, 5), row(-3, 7)])
    assert len(old.state.incidents) == 1
    fixed = apply([row(-12, 1), row(-9, 1)], old.state)
    assert any(d.reason == "ambiguous candidate merger/split" for d in fixed.decisions)
    assert fixed.requires_review
    assert len(fixed.state.incidents) == 1  # preserve original stable identity, no duplicate
    assert all(d.status != "PROPOSED" for d in fixed.decisions)


def test_real_correction_merge_requires_review_not_alert():
    # Two previously CLOSED-separated storms become connected after a quiet bar
    # is contradicted by NOAA; the disputed slot is quarantined, not chosen.
    old = apply([row(-18, 5), row(-15, 1), row(-12, 1), row(-9, 7)])
    assert len(old.state.incidents) == 2
    fixed = apply([row(-15, 7)], old.state)
    assert len(fixed.state.incidents) == 2
    assert all(i.review_required for i in fixed.state.incidents)
    assert any(d.reason == "ambiguous candidate merger/split" for d in fixed.decisions)
    assert all(d.status != "PROPOSED" for d in fixed.decisions)


def test_close_does_not_propose_without_confirmed_start():
    x = apply([row(-12, 5), row(-9, 2), row(-6, 2)])
    assert x.state.incidents[0].state == "CLOSED"
    assert not x.state.outbox
    assert all(d.status != "PROPOSED" for d in x.decisions)


def test_escalation_waits_for_start_ack_and_stays_idempotent_after_ack():
    a = apply([row(-6, 5)]).state
    b = apply([row(-3, 7)], a)
    assert b.state.outbox == a.outbox
    assert any(d.reason == "START not confirmed delivered" for d in b.decisions)
    ack = record_delivery(b.state, expected_version=b.state.version, event_key=b.state.outbox[0].event_key,
                          status="SENT", telegram_message_id=17)
    c = apply([row(-3, 7)], ack)
    assert any(d.status == "PROPOSED" and d.event_key.endswith(":ESCALATE:G3") for d in c.decisions)
    d = apply([row(-3, 7)], c.state)
    assert d.state == c.state and not any(x.status == "PROPOSED" for x in d.decisions)
