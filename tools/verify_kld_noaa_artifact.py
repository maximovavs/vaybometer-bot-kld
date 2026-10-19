#!/usr/bin/env python3
"""Offline KLD NOAA evidence artifact gate and independent replay verifier.

The producer (R3) is opt-in. This gate NEVER calls NOAA/GitHub/Telegram.
`seal` writes a write-once manifest of verified snapshot SHA-256 values and
prints a hash to anchor in the Actions log. `verify` independently checks a
previously extracted artifact against an OPTIONAL separately retained digest.

This is not a permanent/WORM archive; the diagnostic artifact's retention is
controlled by the existing workflow. This does not attest the separately fetched
current Kp, nor independently prove the historical TLS connection to NOAA.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# Permit normal `python tools/verify_...py ...` execution in the checkout.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from geomagnetic_noaa_evidence import EvidenceError, verify_evidence

MANIFEST = "kld-noaa-evidence-manifest.json"
SCHEMA = "kld-noaa-artifact-manifest-v1"
SNAPSHOT = re.compile(r"^kld-noaa-observed3h-run([1-9][0-9]{0,19})-attempt([1-9][0-9]{0,5})-(\d{8}T\d{12}Z)\.json$")
RUN_ID = re.compile(r"^[1-9][0-9]{0,19}$")
COMMIT_SHA = re.compile(r"^[a-f0-9]{40}$")
DIGEST = re.compile(r"^[a-f0-9]{64}$")
MAX_SNAPSHOTS = 12
MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024


class ArtifactIntegrityError(ValueError):
    """Never upload a candidate snapshot that cannot be independently verified."""


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _identity(run_id: str, attempt: int, head_sha: str) -> None:
    if (not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id)
            or type(attempt) is not int or attempt < 1 or attempt > 999999
            or not isinstance(head_sha, str) or not COMMIT_SHA.fullmatch(head_sha)):
        raise ArtifactIntegrityError("invalid GitHub run identity")


def _safe_root(root: Path) -> None:
    if root.is_symlink() or not root.is_dir():
        raise ArtifactIntegrityError("missing or unsafe evidence directory")


def _numeric_no_secret(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return False
    try:
        n = float(value)
    except (ValueError, TypeError, OverflowError):
        return False
    return math.isfinite(n) and 0 <= n <= 1_000_000


def _restricted_noaa_payload(snapshot: dict) -> None:
    """Prevent arbitrary NOAA ancillary fields from carrying credentials.

    The R3 parser pins schema field names, but its ancillary numerical columns
    accept any JSON type. Reject nonnumeric content here before artifact upload.
    Never redact original response bytes: a refusal is safer than altering evidence.
    """
    import base64
    raw = base64.b64decode(snapshot["raw"]["body_base64"], validate=True)
    payload = json.loads(raw)
    if not isinstance(payload, list):
        raise ArtifactIntegrityError("invalid NOAA payload")
    if payload and isinstance(payload[0], list):
        fields = payload[0]
        for row in payload[1:]:
            if not isinstance(row, list) or len(row) != len(fields):
                raise ArtifactIntegrityError("malformed NOAA row")
            for col in ("a_running", "station_count"):
                if col in fields and not _numeric_no_secret(row[fields.index(col)]):
                    raise ArtifactIntegrityError("unexpected NOAA ancillary content")
    else:
        for row in payload:
            if not isinstance(row, dict):
                raise ArtifactIntegrityError("malformed NOAA row")
            for col in ("a_running", "station_count"):
                if col in row and not _numeric_no_secret(row[col]):
                    raise ArtifactIntegrityError("unexpected NOAA ancillary content")


def _snapshots(root: Path, *, run_id: str, attempt: int, head_sha: str) -> list[dict]:
    _identity(run_id, attempt, head_sha)
    _safe_root(root)
    entries = sorted(root.iterdir(), key=lambda p: p.name)
    candidates = [p for p in entries if p.name != MANIFEST]
    if not 1 <= len(candidates) <= MAX_SNAPSHOTS:
        raise ArtifactIntegrityError("missing/excessive NOAA evidence snapshots")
    values = []
    for path in candidates:
        match = SNAPSHOT.fullmatch(path.name)
        if (not match or path.is_symlink() or not path.is_file()
                or match.group(1) != run_id or int(match.group(2)) != attempt):
            raise ArtifactIntegrityError("unexpected or unsafe NOAA evidence file")
        size = path.stat().st_size
        if not 0 < size <= MAX_SNAPSHOT_BYTES:
            raise ArtifactIntegrityError("NOAA evidence size outside limit")
        content = path.read_bytes()
        snapshot = verify_evidence(path)  # Recomputes exact NOAA bars/coverage.
        _restricted_noaa_payload(snapshot)
        origin = snapshot["origin"]
        try:
            stamp = datetime.fromisoformat(origin["as_of_utc"].replace("Z", "+00:00"))
            if stamp.utcoffset() != timezone.utc.utcoffset(stamp) or stamp.strftime("%Y%m%dT%H%M%S%fZ") != match.group(3):
                raise ArtifactIntegrityError("evidence file name does not match UTC timestamp")
        except ArtifactIntegrityError:
            raise
        except (ValueError, AttributeError, TypeError) as exc:
            raise ArtifactIntegrityError("invalid evidence timestamp") from exc
        if (origin["github_run_id"] != run_id
                or origin["github_run_attempt"] != attempt
                or origin["github_head_sha"] != head_sha
                or snapshot["current_kp"] != {
                    "attested_by_this_snapshot": False,
                    "reason": "separate NOAA request/cache path; no shared-payload claim",
                }):
            raise ArtifactIntegrityError("NOAA snapshot provenance does not match this run")
        values.append({
            "file": path.name, "sha256": _sha(content),
            "raw_sha256": snapshot["raw"]["sha256"],
            "as_of_utc": origin["as_of_utc"],
            "coverage": snapshot["calculation"]["coverage"],
            "observed_count": snapshot["calculation"]["observed_count"],
            "expected_count": snapshot["calculation"]["expected_count"],
            "peak_kp_raw": snapshot["calculation"]["peak_kp_raw"],
            "peak_interval_start_utc": snapshot["calculation"]["peak_interval_start_utc"],
        })
    return values


def _manifest_bytes(*, run_id: str, attempt: int, head_sha: str, files: list[dict]) -> bytes:
    record = {
        "schema": SCHEMA,
        "purpose": "kld-observed-3h-24h-peak-only",
        "github_run_id": run_id,
        "github_run_attempt": attempt,
        "github_head_sha": head_sha,
        "snapshot_count": len(files),
        "files": files,
        "current_kp_attested": False,
    }
    return (json.dumps(record, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def seal(root: Path, *, run_id: str, attempt: int, head_sha: str) -> str:
    """Verify snapshots, create canonical manifest O_EXCL; identical replay OK."""
    files = _snapshots(root, run_id=run_id, attempt=attempt, head_sha=head_sha)
    data = _manifest_bytes(run_id=run_id, attempt=attempt, head_sha=head_sha, files=files)
    target = root / MANIFEST
    if target.is_symlink():
        raise ArtifactIntegrityError("symlink manifest refused")
    try:
        flags = os.O_CREAT | os.O_WRONLY | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(target, flags, 0o444)
    except FileExistsError:
        if not target.is_file() or target.read_bytes() != data:
            raise ArtifactIntegrityError("manifest replay conflicts with original") from None
    else:
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            target.unlink(missing_ok=True)
            raise
    return _sha(data)


def verify(root: Path, *, expected_manifest_sha256: str) -> str:
    """Offline check of downloaded artifact, anchored to digest in Actions log."""
    _safe_root(root)
    target = root / MANIFEST
    if target.is_symlink() or not target.is_file():
        raise ArtifactIntegrityError("missing/unsafe manifest")
    data = target.read_bytes()
    actual_sha = _sha(data)
    if not DIGEST.fullmatch(expected_manifest_sha256) or actual_sha != expected_manifest_sha256:
        raise ArtifactIntegrityError("manifest digest mismatch with separate log")
    try:
        record = json.loads(data)
        run_id, attempt, head_sha = (record["github_run_id"],
                                     record["github_run_attempt"], record["github_head_sha"])
        _identity(run_id, attempt, head_sha)
        computed = _snapshots(root, run_id=run_id, attempt=attempt, head_sha=head_sha)
        if data != _manifest_bytes(run_id=run_id, attempt=attempt, head_sha=head_sha,
                                   files=computed):
            raise ArtifactIntegrityError("downloaded artifact content/manifest mismatch")
    except (KeyError, TypeError, ValueError, UnicodeError) as exc:
        raise ArtifactIntegrityError("invalid or altered evidence manifest") from exc
    return actual_sha


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("seal", "verify"))
    parser.add_argument("--dir", required=True)
    parser.add_argument("--expected-manifest-sha256", default="")
    args = parser.parse_args()
    try:
        if args.mode == "seal":
            if args.expected_manifest_sha256:
                raise ArtifactIntegrityError("digest argument invalid in seal mode")
            result = seal(Path(args.dir), run_id=os.environ.get("GITHUB_RUN_ID", ""),
                          attempt=int(os.environ.get("GITHUB_RUN_ATTEMPT", "0")),
                          head_sha=os.environ.get("GITHUB_SHA", ""))
        else:
            result = verify(Path(args.dir), expected_manifest_sha256=args.expected_manifest_sha256)
    except (ArtifactIntegrityError, EvidenceError, OSError, ValueError, TypeError) as exc:
        # No raw NOAA response, tokens, paths or env secrets appear in messages.
        print("KLD_NOAA_EVIDENCE_INTEGRITY=STOP", file=sys.stderr)
        return 1
    print(f"KLD_NOAA_EVIDENCE_INTEGRITY=PASS manifest_sha256={result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
