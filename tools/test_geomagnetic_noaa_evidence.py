"""R3 immutable observed-3h evidence: pure offline tests, no HTTP/Telegram."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from geomagnetic_noaa_evidence import EvidenceError, verify_evidence
from geomagnetic_noaa_transport import (NOAA_OBSERVED_3H_URL, NOAAResponse,
                                        NoaaTransportError, load_observed_3h_history)
from geomagnetic_history import rolling_24h_peak

UTC = timezone.utc
AT = datetime(2026, 10, 19, 9, 50, tzinfo=UTC)
SHA = "a" * 40
NETWORK = []


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    for key in ("KLD_NOAA_EVIDENCE_DIR", "KLD_GEOMAG_24H_PEAK_ENABLED", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_SHA"):
        monkeypatch.delenv(key, raising=False)
    def denied(*args, **kwargs):
        NETWORK.append("network attempted")
        raise AssertionError("R3 must never access network")
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    assert not NETWORK


def rows(values):
    return [{"time_tag": (datetime(2026, 10, 18, 9, tzinfo=UTC) + timedelta(hours=i*3))
             .strftime("%Y-%m-%dT%H:%M:%S"), "Kp": value} for i, value in enumerate(values)]


def run(monkeypatch, tmp_path, payload, *, raw=None, as_of=AT, status=200,
        endpoint=NOAA_OBSERVED_3H_URL, enabled=True, attempts=None):
    if enabled:
        monkeypatch.setenv("KLD_NOAA_EVIDENCE_DIR", str(tmp_path))
        monkeypatch.setenv("KLD_GEOMAG_24H_PEAK_ENABLED", "1")
        monkeypatch.setenv("GITHUB_RUN_ID", "39232459035")
        monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
        monkeypatch.setenv("GITHUB_SHA", SHA)
    else:
        monkeypatch.delenv("KLD_NOAA_EVIDENCE_DIR", raising=False)
    body = raw if raw is not None else json.dumps(payload, separators=(",", ":")).encode("utf-8")
    calls = []
    def fake(url, *, timeout_s, max_bytes):
        calls.append((url, timeout_s, max_bytes))
        return NOAAResponse(status=status, final_url=endpoint, body=body)
    history = load_observed_3h_history(clock=lambda: as_of, transport=fake)
    if attempts is not None:
        attempts.extend(calls)
    assert len(calls) == 1 and calls[0][0] == NOAA_OBSERVED_3H_URL
    return history


def stored(tmp_path):
    files = list(tmp_path.glob("kld-noaa-observed3h-*.json"))
    assert len(files) == 1, files
    return files[0], verify_evidence(files[0])


def test_complete_8_8_exact_raw_sha_and_utc_peak(monkeypatch, tmp_path, caplog):
    caplog.set_level("INFO")
    payload = rows([0.0, 1.0, 1.33, 1.67, 0.33, 0.67, 1.0, 0.33])
    raw = json.dumps(payload, indent=3, ensure_ascii=False).encode()
    history = run(monkeypatch, tmp_path, payload, raw=raw)
    assert rolling_24h_peak(history).coverage == "COMPLETE"
    path, obj = stored(tmp_path)
    assert obj["raw"]["sha256"] == hashlib.sha256(raw).hexdigest()
    assert base64.b64decode(obj["raw"]["body_base64"]) == raw
    assert obj["calculation"]["observed_count"] == 8
    assert obj["calculation"]["coverage"] == "COMPLETE"
    assert obj["calculation"]["peak_kp_raw"] == 1.67
    assert obj["calculation"]["peak_g_level"] is None
    assert obj["calculation"]["peak_interval_start_utc"] == "2026-10-18T18:00:00Z"
    assert obj["calculation"]["intervals"][0]["start_utc"] == "2026-10-18T09:00:00Z"
    assert obj["calculation"]["intervals"][-1]["end_utc"] == "2026-10-19T09:00:00Z"
    assert obj["origin"]["github_run_id"] == "39232459035"
    assert obj["origin"]["github_head_sha"] == SHA
    assert obj["origin"]["as_of_utc"] == "2026-10-19T09:50:00Z"
    assert obj["current_kp"]["attested_by_this_snapshot"] is False
    assert path.stat().st_size > len(raw)
    assert "envelope_sha256=" + hashlib.sha256(path.read_bytes()).hexdigest() in caplog.text
    assert "raw_sha256=" + hashlib.sha256(raw).hexdigest() in caplog.text


def test_missing_slot_is_partial_and_never_zero_filled(monkeypatch, tmp_path):
    payload = rows([1.0, 2.0, 5.99, 6.67, 2, 1, 2, 0.33])
    del payload[3]
    run(monkeypatch, tmp_path, payload)
    _, obj = stored(tmp_path)
    assert obj["calculation"]["coverage"] == "PARTIAL"
    assert obj["calculation"]["observed_count"] == 7
    assert obj["calculation"]["intervals"][3]["status"] == "MISSING"
    assert obj["calculation"]["intervals"][3]["kp_raw"] is None
    assert obj["calculation"]["peak_kp_raw"] == 5.99
    assert obj["calculation"]["peak_g_level"] == "G1"


def test_empty_history_unknown_not_calm(monkeypatch, tmp_path):
    run(monkeypatch, tmp_path, [])
    _, obj = stored(tmp_path)
    assert obj["calculation"]["coverage"] == "UNKNOWN"
    assert obj["calculation"]["observed_count"] == 0
    assert obj["calculation"]["peak_kp_raw"] is None
    assert all(x["status"] == "MISSING" for x in obj["calculation"]["intervals"])


def test_conflict_same_timestamp_partial_no_convenient_choice(monkeypatch, tmp_path):
    payload = rows([0, 1, 2, 3, 4, 5, 6, 7])
    payload.append({**payload[-1], "Kp": 9})
    run(monkeypatch, tmp_path, payload)
    _, obj = stored(tmp_path)
    assert obj["calculation"]["coverage"] == "PARTIAL"
    assert obj["calculation"]["intervals"][-1]["status"] == "CONFLICT"
    assert obj["calculation"]["intervals"][-1]["kp_raw"] is None
    assert obj["calculation"]["peak_kp_raw"] == 6
    assert obj["calculation"]["peak_g_level"] == "G2"


def test_replayed_same_input_same_bytes_is_idempotent(monkeypatch, tmp_path):
    payload = rows([0.33] * 8)
    run(monkeypatch, tmp_path, payload)
    path, obj = stored(tmp_path)
    original = path.read_bytes()
    run(monkeypatch, tmp_path, payload)
    assert path.read_bytes() == original
    assert list(tmp_path.glob("*.json")) == [path]
    assert verify_evidence(path) == obj


def test_changed_payload_same_run_identity_is_rejected(monkeypatch, tmp_path):
    run(monkeypatch, tmp_path, rows([0.33] * 8))
    path, _ = stored(tmp_path)
    original = path.read_bytes()
    with pytest.raises(NoaaTransportError, match="evidence capture failed"):
        run(monkeypatch, tmp_path, rows([1.0] * 8))
    assert path.read_bytes() == original


@pytest.mark.parametrize("field", ["sha", "raw", "peak", "coverage", "run_id", "interval"])
def test_manual_tamper_is_detected(monkeypatch, tmp_path, field):
    run(monkeypatch, tmp_path, rows([0.33] * 8))
    path, data = stored(tmp_path)
    if field == "sha":
        data["raw"]["sha256"] = "0" * 64
    elif field == "raw":
        data["raw"]["body_base64"] = base64.b64encode(b"[]").decode()
    elif field == "peak":
        data["calculation"]["peak_kp_raw"] = 9
    elif field == "coverage":
        data["calculation"]["coverage"] = "COMPLETE_FABRICATED"
    elif field == "run_id":
        data["origin"]["github_run_id"] = "faked"
    elif field == "interval":
        data["calculation"]["intervals"][0]["kp_raw"] = 7.0
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(EvidenceError):
        verify_evidence(path)


@pytest.mark.parametrize("payload", [
    [{"time_tag":"2026-10-19T06:00:00","estimated_kp":7}],
    [{"time_tag":"2026-10-19T06:01:00","Kp":7}],
    [{"time_tag":"2026-10-19T06:00:00","Kp":7,"source":"swpc_estimated_1m"}],
    [{"time_tag":"2026-10-19T06:00:00","Kp":11}],
])
def test_untrusted_schema_has_no_evidence(monkeypatch, tmp_path, payload):
    with pytest.raises(NoaaTransportError):
        run(monkeypatch, tmp_path, payload)
    assert list(tmp_path.glob("*.json")) == []


def test_transport_rejects_untrusted_endpoint_with_no_evidence(monkeypatch, tmp_path):
    with pytest.raises(NoaaTransportError):
        run(monkeypatch, tmp_path, rows([0.33]*8),endpoint=NOAA_OBSERVED_3H_URL+"?redirected=1")
    assert list(tmp_path.glob("*.json")) == []


def test_off_by_default_and_missing_identity_fail_closed(monkeypatch, tmp_path):
    calls = []
    run(monkeypatch, tmp_path, rows([0.33] * 8), enabled=False, attempts=calls)
    assert len(calls) == 1 and list(tmp_path.glob("*.json")) == []
    monkeypatch.setenv("KLD_NOAA_EVIDENCE_DIR", str(tmp_path))
    monkeypatch.setenv("KLD_GEOMAG_24H_PEAK_ENABLED", "1")
    # No GITHUB_RUN_ID / SHA allowed only when capture is disabled.
    with pytest.raises(NoaaTransportError, match="evidence capture failed"):
        load_observed_3h_history(
            clock=lambda: AT, transport=lambda url, **kw: NOAAResponse(
                status=200, final_url=url, body=json.dumps(rows([0.33]*8)).encode()))
    assert list(tmp_path.glob("*.json")) == []


@pytest.mark.parametrize("peak_value", [None, "", "0", "true", "1 ", "TRUE", "2"])
def test_evidence_enabled_but_peak_not_exact_one_fails_closed(monkeypatch, tmp_path, peak_value):
    monkeypatch.setenv("KLD_NOAA_EVIDENCE_DIR", str(tmp_path))
    if peak_value is not None:
        monkeypatch.setenv("KLD_GEOMAG_24H_PEAK_ENABLED", peak_value)
    with pytest.raises(NoaaTransportError, match="evidence requires active 24h peak"):
        load_observed_3h_history(
            clock=lambda: AT, transport=lambda url, **kw: NOAAResponse(
                status=200, final_url=url, body=json.dumps(rows([0.33] * 8)).encode()))
    assert list(tmp_path.iterdir()) == []


def test_peak_enabled_but_evidence_off_does_not_write(monkeypatch, tmp_path):
    monkeypatch.setenv("KLD_GEOMAG_24H_PEAK_ENABLED", "1")
    run(monkeypatch, tmp_path, rows([0.33] * 8), enabled=False)
    assert list(tmp_path.iterdir()) == []


def test_both_flags_on_writes_replayable_snapshot(monkeypatch, tmp_path):
    run(monkeypatch, tmp_path, rows([0.33] * 8))
    _, saved = stored(tmp_path)
    assert saved["calculation"]["coverage"] == "COMPLETE"


def test_feature_flag_contract_does_not_weaken_existing_workflow_guard():
    path = ROOT / ".github" / "workflows" / "daily_post_klg.yml"
    workflow = path.read_text(encoding="utf-8")
    flag = "KLD_GEOMAG_24H_PEAK_ENABLED"
    prefix, jobs = workflow.split("\njobs:\n", 1)
    morning, rest = jobs.split("\n  evening:\n", 1)
    evening, noon_fx = rest.split("\n  noon_fx:\n", 1)
    assert morning.count(flag) == workflow.count(flag) == 2
    assert flag not in prefix + evening + noon_fx
    assert flag not in morning.split("\n    steps:\n", 1)[1]
    assert morning.count("vars.KLD_NOAA_EVIDENCE_ENABLED == '1'") == 1
    assert "KLD_NOAA_EVIDENCE_DIR" not in prefix + evening + noon_fx


def test_no_network_attempts():
    assert NETWORK == []
