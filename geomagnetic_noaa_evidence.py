"""Write-once, offline-auditable evidence for one verified KLD NOAA 3h response.

Called only after geomagnetic_noaa_transport validates its pinned HTTPS response.
This records the *24h peak* source only: the independently fetched current Kp
is NOT attested by this snapshot. Local write-once is not permanent/WORM storage;
shipping and retaining the evidence artifact requires a separately approved stage.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from geomagnetic_history import OBSERVED_SOURCE, ParsedHistory, rolling_24h_peak

SCHEMA = "kld-noaa-observed-3h-evidence-v1"
RUN_ID = re.compile(r"^[1-9][0-9]{0,19}$")
SHA = re.compile(r"^[a-f0-9]{40}$")


class EvidenceError(ValueError):
    """Evidence missing, conflicting, malformed, or unsafe to persist."""


def _iso(t: datetime) -> str:
    if not isinstance(t, datetime) or t.tzinfo is None or t.utcoffset() != timedelta(0):
        raise EvidenceError("UTC-aware datetime required")
    return t.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _record(*, history: ParsedHistory, response: object, run_id: str,
            attempt: int, commit_sha: str) -> dict:
    # An untrusted caller must never use this to *assert* NOAA provenance.
    # Only the pinned HTTPS transport may invoke this after response validation.
    from geomagnetic_noaa_transport import NOAA_OBSERVED_3H_URL, NOAAResponse
    if (not isinstance(response, NOAAResponse)
            or type(response.status) is not int or response.status != 200
            or response.final_url != NOAA_OBSERVED_3H_URL
            or response.redirect_count != 0
            or not isinstance(response.content_type, str)
            or response.content_type.split(";", 1)[0].strip().lower() != "application/json"
            or not isinstance(response.body, bytes) or not response.body):
        raise EvidenceError("untrusted NOAA response")
    if (not isinstance(history, ParsedHistory) or history.source != OBSERVED_SOURCE
            or type(run_id) is not str or RUN_ID.fullmatch(run_id) is None
            or type(attempt) is not int or attempt <= 0
            or type(commit_sha) is not str or SHA.fullmatch(commit_sha) is None):
        raise EvidenceError("invalid history or GitHub identity")
    peak = rolling_24h_peak(history)
    accepted = {bar.interval_start_utc: bar for bar in history.bars}
    conflicts = set(history.conflicting_interval_starts_utc)
    expected_start = (
        peak.as_of_utc.replace(hour=peak.as_of_utc.hour // 3 * 3,
                               minute=0, second=0, microsecond=0)
        - timedelta(hours=24)
    )
    intervals = []
    for n in range(8):
        start = expected_start + timedelta(hours=n * 3)
        bar = accepted.get(start)
        status = "OBSERVED" if bar is not None else "CONFLICT" if start in conflicts else "MISSING"
        intervals.append({"start_utc": _iso(start), "end_utc": _iso(start + timedelta(hours=3)),
                          "status": status, "kp_raw": bar.kp_raw if bar else None,
                          "g_level": bar.g_level if bar else None})
    return {
        "schema": SCHEMA,
        "purpose": "kld-24h-peak-only",
        "origin": {
            "endpoint": NOAA_OBSERVED_3H_URL, "final_url": response.final_url,
            "http_status": response.status, "redirect_count": response.redirect_count,
            "content_type": response.content_type, "source": OBSERVED_SOURCE,
            "github_run_id": run_id, "github_run_attempt": attempt,
            "github_head_sha": commit_sha, "as_of_utc": _iso(history.as_of_utc),
        },
        "raw": {"sha256": hashlib.sha256(response.body).hexdigest(),
                "byte_length": len(response.body),
                "body_base64": base64.b64encode(response.body).decode("ascii")},
        "calculation": {
            "window_start_utc": _iso(peak.window_start_utc),
            "coverage": peak.coverage, "observed_count": peak.observed_count,
            "expected_count": peak.expected_count, "intervals": intervals,
            "peak_kp_raw": peak.peak_kp_raw,
            "peak_g_level": peak.peak_g_level,
            "peak_interval_start_utc": (
                _iso(peak.peak_interval_start_utc) if peak.peak_interval_start_utc else None
            ),
            "rejected": history.rejection_counts(),
            "identical_duplicates": history.duplicate_count,
        },
        "current_kp": {
            "attested_by_this_snapshot": False,
            "reason": "separate NOAA request/cache path; no shared-payload claim",
        },
    }


def write_verified_evidence(*, directory: str | os.PathLike[str],
                            history: ParsedHistory, response: object,
                            run_id: str, attempt: int, commit_sha: str) -> Path:
    """Atomically create one evidence envelope. Same bytes = idempotent replay;
    same identity with other bytes = STOP; never overwrite a previous record.
    """
    record = _record(history=history, response=response, run_id=run_id,
                     attempt=attempt, commit_sha=commit_sha)
    blob = (json.dumps(record, sort_keys=True, ensure_ascii=False,
                       separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
    root = Path(directory)
    if root.is_symlink():
        raise EvidenceError("symlink evidence directory refused")
    root.mkdir(parents=True, exist_ok=True)
    stamp = history.as_of_utc.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    target = root / f"kld-noaa-observed3h-run{run_id}-attempt{attempt}-{stamp}.json"
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(target, flags, 0o444)
    except FileExistsError:
        if target.is_file() and not target.is_symlink() and target.read_bytes() == blob:
            return target
        raise EvidenceError("immutable evidence conflict: existing bytes differ") from None
    try:
        with os.fdopen(fd, "wb") as fp:
            fp.write(blob)
            fp.flush()
            os.fsync(fp.fileno())
    except BaseException:
        # Do not publish an incomplete artifact as valid evidence.
        target.unlink(missing_ok=True)
        raise
    return target


def verify_evidence(path: str | os.PathLike[str]) -> dict:
    """Fail closed on tampering by checking raw hash and independently replaying
    the complete raw NOAA payload through the existing strict schema/parser.
    This verifies internal consistency, not historical TLS authenticity.
    """
    from geomagnetic_noaa_transport import NOAAResponse, _parse_unique_object, _refuse_nonfinite, _validate_schema
    from geomagnetic_history import parse_observed_3h_history
    try:
        data = json.loads(Path(path).read_bytes())
        origin, raw = data["origin"], data["raw"]
        body = base64.b64decode(raw["body_base64"], validate=True)
        if hashlib.sha256(body).hexdigest() != raw["sha256"] or len(body) != raw["byte_length"]:
            raise EvidenceError("raw NOAA SHA-256 mismatch")
        timestamp = datetime.fromisoformat(origin["as_of_utc"].replace("Z", "+00:00"))
        payload = json.loads(body.decode("utf-8"), object_pairs_hook=_parse_unique_object,
                             parse_constant=_refuse_nonfinite)
        _validate_schema(payload)
        history = parse_observed_3h_history(payload, as_of_utc=timestamp, source=OBSERVED_SOURCE)
        response = NOAAResponse(status=origin["http_status"], final_url=origin["final_url"],
                                content_type=origin["content_type"], redirect_count=origin["redirect_count"],
                                body=body)
        expected = _record(history=history, response=response,
                           run_id=origin["github_run_id"], attempt=origin["github_run_attempt"],
                           commit_sha=origin["github_head_sha"])
        if expected != data:
            raise EvidenceError("calculation/provenance metadata mismatch")
        return data
    except (OSError, ValueError, KeyError, TypeError, OverflowError) as exc:
        if isinstance(exc, EvidenceError):
            raise
        raise EvidenceError("invalid immutable NOAA evidence") from exc
