"""KLD R4 artifact packing, replay and isolation; 100% offline."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from geomagnetic_noaa_transport import (
    NOAA_OBSERVED_3H_URL, NOAAResponse, load_observed_3h_history,
)
from tools.verify_kld_noaa_artifact import (
    ArtifactIntegrityError, MANIFEST, seal, verify,
)

UTC = timezone.utc
AT = datetime(2026, 10, 19, 9, 50, tzinfo=UTC)
RUN_ID = "39232459035"
SHA = "a" * 40
NETWORK = []


@pytest.fixture(autouse=True)
def prohibit_network(monkeypatch):
    def deny(*args, **kwargs):
        NETWORK.append("attempted")
        raise AssertionError("Network is forbidden during KLD R4 tests")
    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket.socket, "connect_ex", deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    yield
    assert not NETWORK


def rows(values, *, as_of=AT):
    return [{"time_tag": (as_of.replace(hour=9, minute=0, second=0) - timedelta(hours=24 - i * 3))
             .strftime("%Y-%m-%dT%H:%M:%S"), "Kp": k,
             "a_running": "2", "station_count": 8} for i, k in enumerate(values)]


def produce(monkeypatch, root, values=None, *, at=AT, payload=None, env_secrets=True):
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("KLD_NOAA_EVIDENCE_DIR", str(root))
    monkeypatch.setenv("KLD_GEOMAG_24H_PEAK_ENABLED", "1")
    monkeypatch.setenv("GITHUB_RUN_ID", RUN_ID)
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    monkeypatch.setenv("GITHUB_SHA", SHA)
    if env_secrets:
        monkeypatch.setenv("TELEGRAM_TOKEN_KLG", "SECRETTOKEN_should_not_appear")
        monkeypatch.setenv("OPENAI_API_KEY", "SECRETKEY_should_not_appear")
        monkeypatch.setenv("GH_TOKEN", "SECRETGH_should_not_appear")
    obj = rows(values if values is not None else [0.33] * 8, as_of=at) if payload is None else payload
    body = json.dumps(obj).encode("utf-8")
    calls = []

    def fake_transport(url, *, timeout_s, max_bytes):
        assert url == NOAA_OBSERVED_3H_URL
        calls.append(url)
        return NOAAResponse(200, url, body)

    result = load_observed_3h_history(clock=lambda: at, transport=fake_transport)
    assert calls == [NOAA_OBSERVED_3H_URL]
    return result


def test_complete_capture_manifest_repeat_verify_and_separate_current(monkeypatch, tmp_path, capsys):
    produce(monkeypatch, tmp_path, [0, 1, 1.33, 1.67, 0.33, 0, 1, 0.33])
    digest = seal(tmp_path, run_id=RUN_ID, attempt=1, head_sha=SHA)
    assert len(digest) == 64
    contents = (tmp_path / MANIFEST).read_bytes()
    data = json.loads(contents)
    assert digest == hashlib.sha256(contents).hexdigest()
    assert data["snapshot_count"] == 1
    assert data["files"][0]["coverage"] == "COMPLETE"
    assert data["files"][0]["observed_count"] == 8
    assert data["files"][0]["peak_kp_raw"] == 1.67
    assert data["current_kp_attested"] is False
    assert verify(tmp_path, expected_manifest_sha256=digest) == digest
    assert seal(tmp_path, run_id=RUN_ID, attempt=1, head_sha=SHA) == digest
    assert (tmp_path / MANIFEST).read_bytes() == contents
    for secret in ("SECRETTOKEN_should_not_appear", "SECRETKEY_should_not_appear", "SECRETGH_should_not_appear"):
        assert all(secret not in p.read_text() for p in tmp_path.iterdir())
        assert secret not in capsys.readouterr().out


def test_partial_and_unknown_stay_unfabricated(monkeypatch, tmp_path):
    produce(monkeypatch, tmp_path / "partial", payload=rows([2, 5, 3, 0, 0, 0, 0, 0])[:7])
    a = seal(tmp_path / "partial", run_id=RUN_ID, attempt=1, head_sha=SHA)
    assert verify(tmp_path / "partial", expected_manifest_sha256=a) == a
    assert json.loads((tmp_path / "partial" / MANIFEST).read_text())["files"][0]["coverage"] == "PARTIAL"
    produce(monkeypatch, tmp_path / "unknown", payload=[])
    b = seal(tmp_path / "unknown", run_id=RUN_ID, attempt=1, head_sha=SHA)
    row = json.loads((tmp_path / "unknown" / MANIFEST).read_text())["files"][0]
    assert row["coverage"] == "UNKNOWN" and row["observed_count"] == 0 and row["peak_kp_raw"] is None
    assert verify(tmp_path / "unknown", expected_manifest_sha256=b) == b


def test_conflict_remains_partial(monkeypatch, tmp_path):
    payload = rows([0, 1, 2, 3, 4, 5, 6, 7])
    payload.append({**payload[-1], "Kp": 9})
    produce(monkeypatch, tmp_path, payload=payload)
    digest = seal(tmp_path, run_id=RUN_ID, attempt=1, head_sha=SHA)
    assert verify(tmp_path, expected_manifest_sha256=digest) == digest
    snapshot = next(p for p in tmp_path.iterdir() if p.name != MANIFEST)
    data = json.loads(snapshot.read_text())
    assert data["calculation"]["coverage"] == "PARTIAL"
    assert data["calculation"]["intervals"][-1]["status"] == "CONFLICT"
    assert data["calculation"]["peak_kp_raw"] == 6


def test_manifest_bound_to_run_attempt_and_commit(monkeypatch, tmp_path):
    produce(monkeypatch, tmp_path)
    for kw in ({"run_id": "39232459036", "attempt": 1, "head_sha": SHA},
               {"run_id": RUN_ID, "attempt": 2, "head_sha": SHA},
               {"run_id": RUN_ID, "attempt": 1, "head_sha": "b" * 40}):
        with pytest.raises(ArtifactIntegrityError):
            seal(tmp_path, **kw)
    assert not (tmp_path / MANIFEST).exists()


def test_damaged_snapshot_blocks_packaging_without_manifest(monkeypatch, tmp_path):
    produce(monkeypatch, tmp_path)
    file = next(tmp_path.iterdir())
    original = json.loads(file.read_text())
    original["calculation"]["coverage"] = "COMPLETE" if original["calculation"]["coverage"] != "COMPLETE" else "UNKNOWN"
    file.chmod(0o644)
    file.write_text(json.dumps(original))
    with pytest.raises(Exception):
        seal(tmp_path, run_id=RUN_ID, attempt=1, head_sha=SHA)
    assert not (tmp_path / MANIFEST).exists()


def test_artifact_recheck_detects_any_snapshot_tamper(monkeypatch, tmp_path):
    produce(monkeypatch, tmp_path)
    digest = seal(tmp_path, run_id=RUN_ID, attempt=1, head_sha=SHA)
    snapshot = next(p for p in tmp_path.iterdir() if p.name != MANIFEST)
    snapshot.chmod(0o644)
    snapshot.write_bytes(snapshot.read_bytes() + b"\n")
    with pytest.raises(Exception):
        verify(tmp_path, expected_manifest_sha256=digest)


def test_manifest_tamper_rejected_by_separate_log_hash(monkeypatch, tmp_path):
    produce(monkeypatch, tmp_path)
    digest = seal(tmp_path, run_id=RUN_ID, attempt=1, head_sha=SHA)
    man = tmp_path / MANIFEST
    man.chmod(0o644)
    man.write_bytes(man.read_bytes() + b" ")
    with pytest.raises(ArtifactIntegrityError, match="digest mismatch"):
        verify(tmp_path, expected_manifest_sha256=digest)


def test_repack_different_source_same_asof_rejected(monkeypatch, tmp_path):
    produce(monkeypatch, tmp_path)
    digest = seal(tmp_path, run_id=RUN_ID, attempt=1, head_sha=SHA)
    with pytest.raises(Exception):
        produce(monkeypatch, tmp_path, [1] * 8)
    assert verify(tmp_path, expected_manifest_sha256=digest) == digest


def test_multiple_snapshots_from_same_run_are_supported(monkeypatch, tmp_path):
    produce(monkeypatch, tmp_path, [1] * 8)
    produce(monkeypatch, tmp_path, [2] * 8, at=AT + timedelta(seconds=1))
    digest = seal(tmp_path, run_id=RUN_ID, attempt=1, head_sha=SHA)
    assert json.loads((tmp_path / MANIFEST).read_text())["snapshot_count"] == 2
    assert verify(tmp_path, expected_manifest_sha256=digest) == digest


@pytest.mark.parametrize("extra", ["extra.tmp", "bad.json", "noaa.secret", "kld-noaa-observed3h-run11-attempt1-20261019T095000000000Z.json"])
def test_unexpected_files_fail_closed(monkeypatch, tmp_path, extra):
    produce(monkeypatch, tmp_path)
    (tmp_path / extra).write_text("SECRETTOKEN_should_not_appear")
    with pytest.raises(ArtifactIntegrityError):
        seal(tmp_path, run_id=RUN_ID, attempt=1, head_sha=SHA)


def test_symlink_is_not_archived(monkeypatch, tmp_path):
    produce(monkeypatch, tmp_path)
    outside = tmp_path.parent / "outside_secret"
    outside.write_text("secret")
    (tmp_path / "bad-link").symlink_to(outside)
    with pytest.raises(ArtifactIntegrityError):
        seal(tmp_path, run_id=RUN_ID, attempt=1, head_sha=SHA)


def test_empty_optin_dir_is_hard_error(tmp_path):
    with pytest.raises(ArtifactIntegrityError, match="missing/excessive"):
        seal(tmp_path, run_id=RUN_ID, attempt=1, head_sha=SHA)


def test_ancillary_field_cannot_contain_secret_even_if_r3_accepts(monkeypatch, tmp_path):
    payload = rows([1] * 8)
    payload[0]["a_running"] = "TELEGRAM_TOKEN_KLG=SHOULD_NOT_LEAK"
    produce(monkeypatch, tmp_path, payload=payload)
    with pytest.raises(ArtifactIntegrityError, match="unexpected NOAA ancillary"):
        seal(tmp_path, run_id=RUN_ID, attempt=1, head_sha=SHA)


def test_cli_uses_hash_only_and_no_secrets(monkeypatch, tmp_path, capsys):
    from tools.verify_kld_noaa_artifact import main
    produce(monkeypatch, tmp_path)
    monkeypatch.setattr("sys.argv", ["verify_kld_noaa_artifact.py", "seal", "--dir", str(tmp_path)])
    assert main() == 0
    out = capsys.readouterr()
    assert "KLD_NOAA_EVIDENCE_INTEGRITY=PASS" in out.out
    assert "SECRETTOKEN" not in out.out and "SECRETKEY" not in out.out
    monkeypatch.setattr("sys.argv", ["verify_kld_noaa_artifact.py", "verify", "--dir", str(tmp_path),
                                        "--expected-manifest-sha256", "0" * 64])
    assert main() == 1
    out = capsys.readouterr()
    assert out.err.strip() == "KLD_NOAA_EVIDENCE_INTEGRITY=STOP"


def test_no_persist_without_optin(monkeypatch, tmp_path):
    monkeypatch.delenv("KLD_NOAA_EVIDENCE_DIR", raising=False)
    body = json.dumps(rows([1] * 8)).encode()
    def fake(url, *, timeout_s, max_bytes):
        return NOAAResponse(200, url, body)
    load_observed_3h_history(clock=lambda: AT, transport=fake)
    assert list(tmp_path.iterdir()) == []


def test_renamed_snapshot_time_is_rejected(monkeypatch, tmp_path):
    produce(monkeypatch, tmp_path)
    file = next(tmp_path.iterdir())
    new = tmp_path / file.name.replace("20261019T095000", "20261019T095001")
    file.rename(new)
    with pytest.raises(ArtifactIntegrityError, match="file name does not match"):
        seal(tmp_path, run_id=RUN_ID, attempt=1, head_sha=SHA)


def test_workflow_integration_scope_and_flag_are_exact():
    """Test the candidate hunk document, not a claim to a full live checkout."""
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github/workflows/daily_post_klg.yml").read_text()
    morning = workflow.split("  morning:", 1)[1].split("  evening:", 1)[0]
    assert workflow.count("KLD_NOAA_EVIDENCE_DIR:") == 1
    assert "KLD_NOAA_EVIDENCE_DIR: ${{ vars.KLD_NOAA_EVIDENCE_ENABLED == '1' && 'outputs/kld_noaa_evidence' || '' }}" in workflow
    assert workflow.count("KLD_GEOMAG_24H_PEAK_ENABLED") == 2
    assert "'outputs/kld_noaa_evidence' || ''" in workflow
    assert morning.count("retention-days: 14") == 1
    assert "include-hidden-files: true" not in workflow
    assert morning.count("KLD_NOAA_EVIDENCE_DIR") == 3
    assert "steps.kld_noaa_evidence_check.outcome == 'success'" in workflow
    assert "steps.kld_noaa_evidence_check.outcome == 'success' && 'outputs/kld_noaa_evidence' || ''" in workflow
    assert "KLD_DIAGNOSTICS_ARTIFACT_DIGEST=" in workflow
    assert "Record KLD diagnostics artifact digest" in workflow
    assert "--dir \"$KLD_NOAA_EVIDENCE_DIR\"" in workflow
    # The snippet must remain scoped to morning and leave the other jobs alone.
    env_fragment = workflow.split("      KLD_GEOMAG_24H_PEAK_ENABLED:", 1)[1].split("    steps:", 1)[0]
    assert env_fragment.count("      KLD_NOAA_EVIDENCE_DIR:") == 1
    assert "KLD_NOAA_EVIDENCE_DIR:" not in workflow.split("  evening:", 1)[1]
    assert "if: always()" in workflow
    assert "retention-days: 30" in workflow   # Existing delivery artifact unchanged.
    assert "# and a write-once manifest before any NOAA evidence may be uploaded." in workflow
