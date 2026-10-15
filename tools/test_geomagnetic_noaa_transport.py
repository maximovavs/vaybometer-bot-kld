"""H1 contract tests: pure fixtures, injected transport/clock, zero egress."""
from __future__ import annotations

import ast
import runpy
import json
import os
import pathlib
import socket
import sys
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from geomagnetic_history import incident_candidates, rolling_24h_peak
from geomagnetic_noaa_transport import (
    MAX_RESPONSE_BYTES, NOAA_OBSERVED_3H_URL, NOAAResponse, NoaaTransportError,
    load_observed_3h_history,
)

T = datetime(2026, 10, 12, 12, tzinfo=timezone.utc)
NETWORK_ATTEMPTS: list[str] = []


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def deny(*_args, **_kwargs):
        NETWORK_ATTEMPTS.append("attempt")
        raise AssertionError("offline H1 test attempted network")
    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket.socket, "connect_ex", deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(socket, "getaddrinfo", deny)


def row(hours: int, kp: object, **meta):
    start = T + timedelta(hours=hours)
    return {"time_tag": start.strftime("%Y-%m-%dT%H:%M:%S"), "Kp": kp, **meta}


def fake(rows=None, **response_changes):
    requested = []
    response = dict(status=200, final_url=NOAA_OBSERVED_3H_URL,
                    body=json.dumps(rows if rows is not None else [row(-3, 6.67)]).encode(),
                    content_type="application/json", redirect_count=0)
    response.update(response_changes)
    def transport(url, *, timeout_s, max_bytes):
        requested.append((url, timeout_s, max_bytes))
        return NOAAResponse(**response)
    return transport, requested


def load(rows=None, *, clock=lambda: T, **changes):
    transport, requests = fake(rows, **changes)
    return load_observed_3h_history(clock=clock, transport=transport), requests


def test_exact_pinned_noaa_url_no_other_endpoints():
    history, reqs = load()
    assert reqs == [("https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json", 10.0, MAX_RESPONSE_BYTES)]
    assert history.source == "swpc_observed_3h"
    assert history.bars[0].kp_raw == 6.67


def test_no_rounding_no_utc_conversion():
    hist, _ = load([row(-3, "6,67"), row(-6, 5.999)])
    assert [b.kp_raw for b in hist.bars] == [5.999, 6.67]
    assert hist.bars[1].interval_start_utc == T - timedelta(hours=3)
    assert hist.bars[1].interval_end_utc == T


def test_complete_24h_and_midnight():
    h, _ = load([row(-24 + i * 3, i) for i in range(8)])
    peak = rolling_24h_peak(h)
    assert peak.coverage == "COMPLETE" and peak.observed_count == 8
    assert peak.peak_kp_raw == 7


def test_missing_interval_is_partial_not_zero():
    h, _ = load([row(-24 + i * 3, i) for i in range(8) if i != 3])
    peak = rolling_24h_peak(h)
    assert peak.coverage == "PARTIAL" and peak.observed_count == 7
    assert peak.missing_interval_starts_utc == (T - timedelta(hours=15),)


def test_empty_valid_noaa_array_is_unknown_not_calm():
    h, _ = load([])
    peak = rolling_24h_peak(h)
    assert peak.coverage == "UNKNOWN" and peak.peak_kp_raw is None


def test_unchanged_a1_conflict_semantics():
    h, _ = load([row(-3, 5), row(-3, 9), row(-6, 6)])
    assert h.conflicting_interval_starts_utc == (T - timedelta(hours=3),)
    assert rolling_24h_peak(h).coverage == "PARTIAL"
    assert len(h.bars) == 1


def test_identical_duplicates_not_false_conflicts():
    h, _ = load([row(-3, 6), row(-3, 6), row(-3, 6)])
    assert h.duplicate_count == 2 and len(h.bars) == 1


def test_backfill_preserves_reconstructed_candidate_identity_semantics():
    later = [row(-9, 7)]
    old, _ = load(later)
    new, _ = load([row(-12, 5)] + later)
    assert incident_candidates(old)[0].candidate_id != incident_candidates(new)[0].candidate_id
    assert [t.kind for t in incident_candidates(new)[0].transitions] == ["START", "ESCALATE"]


def test_unsorted_product_is_equivalent():
    rows = [row(-9, 6.67), row(-6, 0), row(-3, 7.0)]
    a, _ = load(rows)
    b, _ = load(list(reversed(rows)))
    assert a == b


def test_named_header_table_semantics():
    h, _ = load([["station_count", "Kp", "time_tag", "a_running"],
                 [11, "6.67", "2026-10-12 09:00:00", 22]])
    assert h.bars[0].kp_raw == 6.67


@pytest.mark.parametrize("changes", [
    {"status": 301}, {"status": 302}, {"status": 404}, {"status": 429},
    {"status": 500}, {"status": 503}, {"status": True},
    {"final_url": "http://services.swpc.noaa.gov/products/noaa-planetary-k-index.json"},
    {"final_url": "https://services.swpc.noaa.gov/products/planetary_k_index_1m.json"},
    {"final_url": "https://attacker.invalid/products/noaa-planetary-k-index.json"},
    {"final_url": NOAA_OBSERVED_3H_URL + "?other=1"},
    {"redirect_count": 1}, {"redirect_count": True},
    {"content_type": "text/html"}, {"content_type": None},
    {"body": b""}, {"body": b"x" * (MAX_RESPONSE_BYTES + 1)},
    {"body": "[]"},
])
def test_untrusted_http_rejected(changes):
    with pytest.raises(NoaaTransportError):
        load(**changes)


@pytest.mark.parametrize("raw", [
    b"{", b"null", b"{}", b'"foo"', b"0", b"true", b"[null]",
    b"[{\"time_tag\":\"2026-10-12T09:00:00\",\"Kp\":7,\"Kp\":0}]",
    b"[{\"time_tag\":\"2026-10-12T09:00:00\",\"Kp\":NaN}]",
    b"\xff\xfe",
])
def test_malformed_json_or_schema_fails_closed(raw):
    with pytest.raises(NoaaTransportError):
        load(body=raw)


@pytest.mark.parametrize("invalid_row", [
    {"Kp": 7}, {"time_tag": "2026-10-12T09:00:00"},
    {"time_tag": "2026-10-12T09:00:00", "Kp": 5, "unexpected": "x"},
    {"time_tag": "2026-10-12T09:00:00", "Kp": 5, "source": "swpc_estimated_1m"},
    {"time_tag": "2026-10-12T09:00:00", "Kp": 5, "source": "cache:swpc_observed_3h"},
    {"time_tag": "2026-10-12T09:00:00", "Kp": 5, "product": "planetary_k_index_1m.json"},
    {"time_tag": "2026-10-12T09:00:00", "Kp": 5, "type": "FORECAST"},
    {"time_tag": "2026-10-12T09:00:00", "Kp": 5, "kind": "estimated"},
    {"time_tag": "2026-10-12T09:00:00", "Kp": 5, "status": "WARNING"},
    {"time_tag": "2026-10-12T09:00:00", "Kp": 5, "status": ["observed"]},
    {"time_tag": "2026-10-12T09:00:00", "Kp": float("nan")},
    {"time_tag": "2026-10-12T09:00:00", "Kp": 10},
    {"time_tag": "2026-10-12T09:00:00", "Kp": True},
    {"time_tag": "2026-10-12T09:00:00", "Kp": {}},
    {"time_tag": "2026-10-12T09:30:00", "Kp": 7},
    {"time_tag": "2026-10-12T10:00:00", "Kp": 7},
    {"time_tag": "2026-10-12T09:00:01Z", "Kp": 7},
    {"time_tag": "2026-10-12T09:00:00+03:00", "Kp": 7},
    {"time_tag": "2026-02-30T09:00:00", "Kp": 7},
])
def test_one_bad_row_rejects_entire_transport_batch(invalid_row):
    with pytest.raises(NoaaTransportError):
        load([row(-6, 6.67), invalid_row])


@pytest.mark.parametrize("table", [
    [["time_tag", "Kp", "kind"], ["2026-10-12T09:00:00", 6, "forecast"]],
    [["Kp", "Kp", "time_tag"], [6, 7, "2026-10-12T09:00:00"]],
    [["time_tag", "kp_index"], ["2026-10-12T09:00:00", 6]],
    [["Kp"], [6]],
    [["time_tag", "Kp"], ["2026-10-12T09:00:00"]],
    [["time_tag", "Kp"], {"time_tag": "2026-10-12T09:00:00", "Kp": 6}],
])
def test_wrong_product_table_or_malformed_rows_rejected(table):
    with pytest.raises(NoaaTransportError):
        load(table)


@pytest.mark.parametrize("clock", [lambda: T.replace(tzinfo=None),
                                   lambda: T.astimezone(timezone(timedelta(hours=3))),
                                   lambda: "2026-10-12T12:00Z", lambda: None,
                                   lambda: (_ for _ in ()).throw(RuntimeError("bad clock"))])
def test_invalid_clock_rejected_before_transport(clock):
    transport, calls = fake()
    with pytest.raises(NoaaTransportError):
        load_observed_3h_history(clock=clock, transport=transport)
    assert calls == []


def test_clock_called_once_and_fixed_for_parser():
    calls = []
    def clock():
        calls.append(1)
        return T
    h, _ = load([row(-3, 6)], clock=clock)
    assert calls == [1] and h.as_of_utc == T


@pytest.mark.parametrize("error", [TimeoutError("NOAA timeout"), OSError("DNS down"),
                                   HTTPError(NOAA_OBSERVED_3H_URL, 503, "unavailable", {}, None)])
def test_simulated_noaa_failures_do_not_become_observations(error):
    def broken(_url, **_kwargs):
        raise error
    with pytest.raises(NoaaTransportError, match="HTTPS request failed"):
        load_observed_3h_history(clock=lambda: T, transport=broken)


def test_untrusted_response_shape_rejected():
    with pytest.raises(NoaaTransportError):
        load_observed_3h_history(clock=lambda: T, transport=lambda *_args, **_kwargs: {"status": 200})


def test_no_calls_on_import_no_filesystem_network_or_telegram_in_module(monkeypatch):
    def forbidden(*_a, **_kw):
        raise AssertionError("side effect on import")
    with monkeypatch.context() as m:
        m.setattr(socket, "create_connection", forbidden)
        m.setattr(os, "system", forbidden)
        runpy.run_path(str(ROOT / "geomagnetic_noaa_transport.py"))
    ast_tree = ast.parse((ROOT / "geomagnetic_noaa_transport.py").read_text())
    imports = [v.module for v in ast.walk(ast_tree) if isinstance(v, ast.ImportFrom)]
    imports += [a.name for v in ast.walk(ast_tree) if isinstance(v, ast.Import) for a in v.names]
    assert "air" not in imports and "post_kld" not in imports and "post_common" not in imports
    assert "telegram" not in imports and "requests" not in imports
    assert 'if __name__ == "__main__"' not in (ROOT / "geomagnetic_noaa_transport.py").read_text()


def test_default_transport_redirect_policy_is_fail_closed(monkeypatch):
    """Offline request stub exercises real urllib opener, never opens socket."""
    import geomagnetic_noaa_transport as t
    from urllib import request
    records = []
    def stub_build_opener(*handlers):
        records.extend(handlers)
        class FakeOpener:
            def open(self, req, timeout):
                assert req.full_url == NOAA_OBSERVED_3H_URL and timeout == 10.0
                assert req.get_method() == "GET"
                raise HTTPError(req.full_url, 302, "Moved", {"Location": "https://evil.invalid"}, None)
        return FakeOpener()
    monkeypatch.setattr(request, "build_opener", stub_build_opener)
    with pytest.raises(t.NoaaTransportError, match="HTTPS request failed"):
        t.load_observed_3h_history(clock=lambda: T)
    assert len(records) == 1
    assert records[0].redirect_request(None, None, 302, "Moved", {}, "https://evil.invalid", "https://evil.invalid") is None


def _peak_real_pendulum_rows(as_of_utc, kp_values):
    """NOAA-style observed 3h bars ending at the latest completed UTC slot."""
    last_end = as_of_utc.replace(
        hour=(as_of_utc.hour // 3) * 3, minute=0, second=0, microsecond=0,
    )
    return [
        {"time_tag": (last_end - timedelta(hours=24 - 3 * index)).strftime("%Y-%m-%dT%H:%M:%S"),
         "Kp": kp}
        for index, kp in enumerate(kp_values)
    ]


def test_kld_peak_real_pendulum_live_clock(monkeypatch):
    """Pendulum 3 object must become a stdlib aware UTC datetime, not UNKNOWN."""
    import pendulum
    import post_common
    import geomagnetic_noaa_transport as noaa

    pinned = pendulum.datetime(2026, 10, 12, 12, 45, tz="UTC")
    monkeypatch.setattr(pendulum, "now", lambda zone=None: pinned.in_tz(zone) if zone else pinned)
    transport, calls = fake(_peak_real_pendulum_rows(T, [1, 7.33] + [1] * 6))
    actual_loader = noaa.load_observed_3h_history

    def inspect_clock(*, clock, transport):
        now = clock()
        assert type(now) is datetime and now.tzinfo is not None
        assert now.utcoffset() == timedelta(0) and now == T.replace(minute=45)
        return actual_loader(clock=lambda: now, transport=transport)

    monkeypatch.setattr(noaa, "load_observed_3h_history", inspect_clock)
    result = post_common._kld_geomag_24h_peak_line(transport=transport)
    assert "7.33 (G3)" in result and "COMPLETE (8/8)" in result
    assert len(calls) == 1 and calls[0][0] == NOAA_OBSERVED_3H_URL


@pytest.mark.parametrize("source", ["WORK_DATE", "--date"])
def test_kld_peak_real_todaypatch_work_date_and_date_arg(source, monkeypatch):
    """Both WORK_DATE and --date follow the real runner's parse/in_tz path."""
    import pendulum
    import post_common
    from safe_test_post import _TodayPatch

    if source == "WORK_DATE":
        monkeypatch.setenv("WORK_DATE", "2026-10-10")
        date_arg = os.getenv("WORK_DATE", "")
    else:
        date_arg = "2026-10-10T14:00:00+02:00"
    base_date = pendulum.parse(date_arg).in_tz(pendulum.timezone("Europe/Kaliningrad"))
    as_of = datetime.fromtimestamp(base_date.timestamp(), timezone.utc)
    transport, calls = fake(_peak_real_pendulum_rows(as_of, [1, 7.33] + [1] * 6))
    with _TodayPatch(base_date):
        line = post_common._kld_geomag_24h_peak_line(transport=transport)
    assert "7.33 (G3)" in line and "COMPLETE (8/8)" in line
    assert len(calls) == 1


def test_kld_peak_real_todaypatch_rejects_wall_clock_observations():
    """A replayed historical post must not print a current G3 as historical."""
    import pendulum
    import post_common
    from safe_test_post import _TodayPatch

    real_now = datetime.now(timezone.utc)
    current_rows = _peak_real_pendulum_rows(real_now, [1, 7.33] + [1] * 6)
    transport, calls = fake(current_rows)
    historical = pendulum.datetime(2020, 10, 10, 14, tz="Europe/Kaliningrad")
    with _TodayPatch(historical):
        line = post_common._kld_geomag_24h_peak_line(transport=transport)
    assert "UNKNOWN" in line and "7.33" not in line and "спокойно" not in line
    assert len(calls) == 1


def test_kld_peak_explicit_stdlib_clock_overrides_todaypatch():
    import pendulum
    import post_common
    from safe_test_post import _TodayPatch

    transport, calls = fake(_peak_real_pendulum_rows(T, [1, 7.33] + [1] * 6))
    with _TodayPatch(pendulum.datetime(2020, 10, 10, 14, tz="Europe/Kaliningrad")):
        line = post_common._kld_geomag_24h_peak_line(clock=lambda: T, transport=transport)
    assert "7.33 (G3)" in line and "COMPLETE (8/8)" in line
    assert len(calls) == 1


def test_kld_peak_real_todaypatch_partial_and_noaa_failure():
    import pendulum
    import post_common
    from safe_test_post import _TodayPatch

    base = pendulum.datetime(2026, 10, 10, 14, tz="Europe/Kaliningrad")
    as_of = datetime.fromtimestamp(base.timestamp(), timezone.utc)
    partial, partial_calls = fake(_peak_real_pendulum_rows(as_of, [1, 6.67] + [1] * 5))

    def unavailable(_url, **_kwargs):
        raise TimeoutError("offline simulated NOAA outage")

    with _TodayPatch(base):
        partial_line = post_common._kld_geomag_24h_peak_line(transport=partial)
        error_line = post_common._kld_geomag_24h_peak_line(transport=unavailable)
    assert "6.67 (G2)" in partial_line and "PARTIAL (7/8)" in partial_line
    assert "максимум среди доступных" in partial_line
    assert "UNKNOWN" in error_line and "н/д" in error_line and "спокойно" not in error_line
    assert len(partial_calls) == 1


def test_network_attempt_ledger_empty():
    assert NETWORK_ATTEMPTS == []
