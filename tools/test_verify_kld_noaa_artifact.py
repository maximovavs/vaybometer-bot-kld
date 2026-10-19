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

# R10: a completed delivery receipt skips preview (and hence NOAA history).
# A skip is accepted only if the independently validated receipt and both
# publisher markers agree; all other paths still require verified NOAA evidence.

def _r10_complete_delivery(tmp_path, *, image=True, text=True, chat="-1001234567890"):
    from kld_image_first import kld_delivery_path
    delivery = tmp_path / "receipts"
    delivery.mkdir()
    date = "2026-10-19"
    receipt = {
        "schema_version": 1, "target_date": date, "post_type": "morning",
        "chat_type": "production", "chat_id": chat,
        "image_delivered": image, "text_delivered": text,
        "telegram_image_message_id": 531 if image else None,
        "image_sent_at_utc": "2026-10-19T06:00:00Z" if image else None,
        "telegram_text_message_ids": [532] if text else [],
        "text_sent_at_utc": "2026-10-19T06:00:02Z" if text else None,
        "updated_at_utc": "2026-10-19T06:00:02Z",
    }
    kld_delivery_path(date, "morning", delivery_dir=delivery).write_text(json.dumps(receipt))
    return delivery, chat, date


def _r10_cli(monkeypatch, capsys, tmp_path, *, result=None, metadata=None,
             chat="-1001234567890", receipt_dir=None, evidence_dir=None):
    from tools.verify_kld_noaa_artifact import main
    result_path = tmp_path / "image_result.json"
    metadata_path = tmp_path / "image_prompt_metadata.json"
    if result is not None:
        result_path.write_text(json.dumps(result))
    if metadata is not None:
        metadata_path.write_text(json.dumps(metadata))
    monkeypatch.setenv("GITHUB_RUN_ID", RUN_ID)
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    monkeypatch.setenv("GITHUB_SHA", SHA)
    root = evidence_dir if evidence_dir is not None else tmp_path / "evidence"
    monkeypatch.setattr("sys.argv", [
        "verify_kld_noaa_artifact.py", "seal", "--dir", str(root),
        "--publication-result", str(result_path),
        "--prompt-metadata", str(metadata_path),
        "--delivery-dir", str(receipt_dir if receipt_dir is not None else tmp_path / "receipts"),
        "--production-chat-id", chat,
    ])
    capsys.readouterr()  # Discard the publisher's diagnostic skip message.
    status = main()
    output = capsys.readouterr()
    return status, output.out, output.err


def _r10_skip_output(monkeypatch, tmp_path, *, image=True, text=True):
    from kld_image_first import run_image_first_publication
    delivery, chat, day = _r10_complete_delivery(tmp_path, image=image, text=text)
    if not (image and text):
        return delivery, chat, None, None
    def no_actions(*args, **kwargs):
        raise AssertionError("receipt-skip must never build preview, call provider or send Telegram")
    result = run_image_first_publication(
        mode="morning", preview_cmd=["forbidden-preview"], image_cmd=["forbidden-send"],
        send_text=no_actions, run_process=no_actions,
        result_path=tmp_path / "image_result.json",
        prompt_metadata_path=tmp_path / "image_prompt_metadata.json",
        production=True, production_chat_id=chat, target_date=day,
        delivery_dir=delivery,
    )
    assert result["result"] == "skipped_delivery_receipt_complete"
    return delivery, chat, result, json.loads((tmp_path / "image_prompt_metadata.json").read_text())


def test_r10_complete_receipt_skips_noaa_capture_and_does_not_resend(monkeypatch, tmp_path, capsys):
    delivery, chat, result, metadata = _r10_skip_output(monkeypatch, tmp_path)
    status, stdout, stderr = _r10_cli(monkeypatch, capsys, tmp_path,
        result=result, metadata=metadata, chat=chat, receipt_dir=delivery)
    assert status == 0 and not stderr
    assert stdout.strip() == "KLD_NOAA_EVIDENCE_INTEGRITY=SKIPPED_COMPLETE_DELIVERY_RECEIPT"
    assert not (tmp_path / "evidence").exists()  # No fabricated/raw NOAA bytes or manifest.


def test_r10_normal_with_real_capture_seals(monkeypatch, tmp_path, capsys):
    root = tmp_path / "evidence"
    produce(monkeypatch, root)
    status, stdout, stderr = _r10_cli(monkeypatch, capsys, tmp_path,
        result={"result": "succeeded", "mode": "morning"}, evidence_dir=root)
    assert status == 0 and not stderr
    assert "KLD_NOAA_EVIDENCE_INTEGRITY=PASS manifest_sha256=" in stdout
    assert (root / MANIFEST).is_file()


@pytest.mark.parametrize("result", [None, {"result": "not_attempted"},
    {"result": "failed_nonfatal"}, {"result": "preview_failed"},
    {"result": "skipped_delivery_receipt_complete", "mode": "morning"}])
def test_r10_missing_noaa_normal_failure_and_forged_skip_fail_closed(monkeypatch,tmp_path,capsys,result):
    status, stdout, stderr = _r10_cli(monkeypatch, capsys, tmp_path, result=result)
    assert status == 1 and not stdout
    assert stderr.strip() == "KLD_NOAA_EVIDENCE_INTEGRITY=STOP"
    assert not (tmp_path / "evidence").exists()


@pytest.mark.parametrize("image,text",[(True,False),(False,True)])
def test_r10_incomplete_receipt_requires_snapshot(monkeypatch,tmp_path,capsys,image,text):
    delivery,chat,_,_= _r10_skip_output(monkeypatch,tmp_path,image=image,text=text)
    status, stdout, stderr = _r10_cli(monkeypatch, capsys, tmp_path,
        result={"result": "not_attempted", "mode": "morning"}, chat=chat, receipt_dir=delivery)
    assert status == 1 and not stdout and "STOP" in stderr


@pytest.mark.parametrize("variation", ["wrong_chat", "wrong_date", "bad_receipt", "bad_result_id",
                                         "missing_metadata", "metadata_wrong_mode", "non_skipped_flag",
                                         "invalid_root_content"])
def test_r10_skip_needs_independent_receipt_and_intact_metadata(monkeypatch,tmp_path,capsys,variation):
    delivery, chat, result, metadata = _r10_skip_output(monkeypatch,tmp_path)
    root = tmp_path / "evidence"
    if variation == "wrong_chat":
        chat = "-1009876543210"
    if variation == "wrong_date":
        metadata["target_date"] = "2026-10-18"
    if variation == "bad_receipt":
        (delivery / "2026-10-19-morning.json").write_text("{}")
    if variation == "bad_result_id":
        result["telegram_image_message_id"] = 90001
    if variation == "missing_metadata":
        metadata = None
        (tmp_path / "image_prompt_metadata.json").unlink()
    if variation == "metadata_wrong_mode":
        metadata["mode"] = "evening"
    if variation == "non_skipped_flag":
        result["text_send_skipped_receipt"] = False
    if variation == "invalid_root_content":
        root.mkdir()
        (root / "unverified.json").write_text("{}")
    status, stdout, stderr = _r10_cli(monkeypatch,capsys,tmp_path,
        result=result,metadata=metadata,chat=chat,receipt_dir=delivery,evidence_dir=root)
    assert status == 1 and not stdout and stderr.strip() == "KLD_NOAA_EVIDENCE_INTEGRITY=STOP"
    assert not (root / MANIFEST).exists()


def test_r10_verify_mode_rejects_receipt_skip_options(monkeypatch,tmp_path,capsys):
    from tools.verify_kld_noaa_artifact import main
    monkeypatch.setattr("sys.argv", ["verify_kld_noaa_artifact.py", "verify",
        "--dir",str(tmp_path),"--publication-result","image_result.json"])
    assert main()==1
    assert capsys.readouterr().err.strip() == "KLD_NOAA_EVIDENCE_INTEGRITY=STOP"



@pytest.mark.parametrize("missing", [
    "--publication-result", "--prompt-metadata", "--delivery-dir", "--production-chat-id",
])
def test_r11_partial_skip_arguments_stop_even_with_valid_evidence(monkeypatch, tmp_path, capsys, missing):
    """A partial skip gate must not silently fall back to successful normal seal."""
    from tools.verify_kld_noaa_artifact import main
    root = tmp_path / "evidence"
    produce(monkeypatch, root)  # Valid evidence makes a weakened fallback pass.
    args = ["verify_kld_noaa_artifact.py", "seal", "--dir", str(root)]
    for flag, value in (
        ("--publication-result", str(tmp_path / "image_result.json")),
        ("--prompt-metadata", str(tmp_path / "image_prompt_metadata.json")),
        ("--delivery-dir", str(tmp_path / "receipts")),
        ("--production-chat-id", "-1001234567890"),
    ):
        if flag != missing:
            args.extend([flag, value])
    monkeypatch.setattr("sys.argv", args)
    assert main() == 1
    output = capsys.readouterr()
    assert output.out == "" and output.err.strip() == "KLD_NOAA_EVIDENCE_INTEGRITY=STOP"
    assert not (root / MANIFEST).exists()

def test_r10_workflow_preserves_seal_and_upload_order():
    workflow = (Path(__file__).resolve().parents[1]/".github/workflows/daily_post_klg.yml").read_text()
    morning = workflow.split("  morning:",1)[1].split("  evening:",1)[0]
    assert morning.index("Seal KLD NOAA evidence") < morning.index("Upload KLD morning publication diagnostics")
    for arg in ("--publication-result image_result.json", "--prompt-metadata image_prompt_metadata.json",
                '--delivery-dir "$KLD_DELIVERY_DIR"', '--production-chat-id "$CHANNEL_ID_KLG"'):
        assert arg in morning
    assert "KLD_GEOMAG_24H_PEAK_ENABLED" in morning
    assert "vars.KLD_NOAA_EVIDENCE_ENABLED == '1'" in morning
    assert "steps.kld_noaa_evidence_check.outcome == 'success'" in morning
    assert morning.count("retention-days: 14") == 1
