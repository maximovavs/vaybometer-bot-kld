"""Run-local authenticated collector handoff for opt-in KLD Semantic Visual QA.

No provider is instantiated.  This is an integrity/authenticity boundary between
*our* preview and image subprocesses, not authentication of Open-Meteo itself.
Only the trusted parent creates a fresh in-memory run secret; it is never
persisted with the collector data. Missing/invalid context always fails closed.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from hmac import compare_digest, new as hmac_new
import json
import os
from pathlib import Path
import secrets
import tempfile
from typing import Any, Callable, Mapping

SCHEMA = "kld_semantic_qa_run_handoff_v1"
RUN_ID_ENV = "KLD_SEMANTIC_QA_RUN_ID"
RUN_KEY_ENV = "KLD_SEMANTIC_QA_RUN_KEY"
MAX_HANDOFF_BYTES = 2 * 1024 * 1024


class RunHandoffUnavailable(ValueError):
    """Non-sensitive reason suitable for fail-closed reporting."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _context(environment: Mapping[str, str]) -> tuple[str, bytes]:
    run_id = environment.get(RUN_ID_ENV, "")
    key_hex = environment.get(RUN_KEY_ENV, "")
    if not isinstance(run_id, str) or len(run_id) != 32 or any(c not in "0123456789abcdef" for c in run_id):
        raise RunHandoffUnavailable("run_context_unavailable")
    if not isinstance(key_hex, str) or len(key_hex) != 64 or any(c not in "0123456789abcdef" for c in key_hex):
        raise RunHandoffUnavailable("run_context_unavailable")
    return run_id, bytes.fromhex(key_hex)


def prepare_run_context(environment: dict[str, str]) -> str:
    """Parent-only action, called solely when the external flag is explicitly ON."""
    if RUN_ID_ENV in environment or RUN_KEY_ENV in environment:
        raise RunHandoffUnavailable("run_context_already_present")
    run_id = secrets.token_hex(16)
    environment[RUN_ID_ENV] = run_id
    environment[RUN_KEY_ENV] = secrets.token_hex(32)
    return run_id


def _validate_type_date(post_type: str, target_date: str) -> None:
    from datetime import date
    if post_type not in {"morning", "evening"}:
        raise RunHandoffUnavailable("post_type_invalid")
    try:
        if date.fromisoformat(target_date).isoformat() != target_date:
            raise ValueError("non-canonical date")
    except (TypeError, ValueError):
        raise RunHandoffUnavailable("target_date_invalid") from None


def write_run_handoff(
    path_value: str, response: Mapping[str, Any] | None, *,
    post_type: str, target_date: str, publication_target_date: str,
    environment: Mapping[str, str] | None = None,
) -> str:
    """Producer ONLY. Validate true collector capture before signing its bytes.

    Return the authenticated raw-payload digest. Do not print credentials.
    """
    env = environment if environment is not None else os.environ
    run_id, key = _context(env)
    _validate_type_date(post_type, target_date)
    if target_date != publication_target_date:
        raise RunHandoffUnavailable("publication_target_mismatch")
    if not isinstance(response, Mapping):
        raise RunHandoffUnavailable("collector_source_unavailable")
    # This invokes full R5 source verification (WMO, fresh timestamp, lunar,
    # window, geography, canonical collector hash), no new network call.
    from kld_semantic_qa_source import build_trusted_snapshot
    snapshot = build_trusted_snapshot(response, target_date=target_date,
                                      post_type=post_type,
                                      publication_target_date=publication_target_date)
    digest = snapshot["provenance"]["raw_payload_sha256"]
    source = dict(response)
    content = {"schema": SCHEMA, "run_id": run_id, "post_type": post_type,
               "target_date": target_date, "raw_sha256": digest,
               "collector_response": source}
    signature = hmac_new(key, _canonical(content), "sha256").hexdigest()
    data = _canonical({**content, "hmac_sha256": signature}) + b"\n"
    if len(data) > MAX_HANDOFF_BYTES:
        raise RunHandoffUnavailable("handoff_oversized")
    path = Path(path_value)
    if not str(path_value).strip():
        raise RunHandoffUnavailable("handoff_path_unavailable")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp: str | None = None
    try:
        fd, tmp = tempfile.mkstemp(prefix=".kld_qa_", suffix=".tmp", dir=path.parent)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if tmp is not None:
            Path(tmp).unlink(missing_ok=True)
    return digest


@dataclass(frozen=True)
class VerifiedRunCapture:
    run_id: str
    raw_sha256: str
    collector_response: Mapping[str, Any]


def read_run_handoff(
    path_value: str, *, post_type: str, target_date: str,
    environment: Mapping[str, str] | None = None,
) -> VerifiedRunCapture:
    """Consumer ONLY. HMAC must verify before any response data is trusted."""
    run_id, key = _context(environment if environment is not None else os.environ)
    _validate_type_date(post_type, target_date)
    path = Path(path_value)
    if not str(path_value).strip() or path.is_symlink():
        raise RunHandoffUnavailable("handoff_path_unavailable")
    try:
        if not path.is_file() or path.stat().st_size > MAX_HANDOFF_BYTES:
            raise RunHandoffUnavailable("handoff_unavailable")
        raw = path.read_bytes()
    except OSError:
        raise RunHandoffUnavailable("handoff_unavailable") from None
    if len(raw) > MAX_HANDOFF_BYTES:
        raise RunHandoffUnavailable("handoff_oversized")
    try:
        parsed = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeDecodeError, TypeError):
        raise RunHandoffUnavailable("handoff_invalid") from None
    fields = {"schema", "run_id", "post_type", "target_date", "raw_sha256",
              "collector_response", "hmac_sha256"}
    if not isinstance(parsed, dict) or set(parsed) != fields:
        raise RunHandoffUnavailable("handoff_schema_invalid")
    provided = parsed["hmac_sha256"]
    if not isinstance(provided, str) or len(provided) != 64:
        raise RunHandoffUnavailable("handoff_signature_invalid")
    unsigned = {k: v for k, v in parsed.items() if k != "hmac_sha256"}
    try:
        computed = hmac_new(key, _canonical(unsigned), "sha256").hexdigest()
    except (ValueError, TypeError):
        raise RunHandoffUnavailable("handoff_schema_invalid") from None
    if not compare_digest(provided, computed):
        raise RunHandoffUnavailable("handoff_signature_invalid")
    if (unsigned["schema"] != SCHEMA or unsigned["run_id"] != run_id
            or unsigned["target_date"] != target_date
            or unsigned["post_type"] != post_type):
        raise RunHandoffUnavailable("handoff_context_mismatch")
    response = unsigned["collector_response"]
    digest = unsigned["raw_sha256"]
    if (not isinstance(response, dict) or not isinstance(digest, str)
            or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest)):
        raise RunHandoffUnavailable("handoff_schema_invalid")
    # The signed descriptor is an independent expectation from the trusted
    # preview, not a free-form label from the unverified image-side payload.
    capture = response.get("_kld_r5_capture")
    if not isinstance(capture, Mapping) or not compare_digest(str(capture.get("raw_payload_sha256", "")), digest):
        raise RunHandoffUnavailable("collector_digest_mismatch")
    return VerifiedRunCapture(run_id, digest, response)


def select_run_injection(*, enabled: bool, source_file: str,
                         post_type: str, target_date: str,
                         image_observer: Callable[[bytes], Any] | None = None,
                         environment: Mapping[str, str] | None = None) -> Any:
    """No network/model instantiation. ON without any required input fails closed."""
    from kld_semantic_qa_wiring import (select_offline_collector_injection,
                                        select_semantic_injection)
    if not enabled:
        return select_semantic_injection(enabled=False, source_snapshot=None,
                                         target_date=target_date, post_type=post_type,
                                         publication_target_date=target_date, observer=None)
    try:
        verified = read_run_handoff(source_file, post_type=post_type,
                                    target_date=target_date, environment=environment)
    except RunHandoffUnavailable as exc:
        injection = select_semantic_injection(enabled=True, source_snapshot=None,
                                               target_date=target_date, post_type=post_type,
                                               publication_target_date=target_date, observer=None)
        # Retain original fail-closed evaluator; propagate a safe cause only.
        return type(injection)(injection.evaluator, injection.facts,
                               "fail_closed", str(exc))
    return select_offline_collector_injection(
        enabled=True, collector_response=verified.collector_response,
        collector_run_id=verified.run_id,
        expected_run_id=_context(environment if environment is not None else os.environ)[0],
        expected_raw_sha256=verified.raw_sha256,
        target_date=target_date, post_type=post_type,
        publication_target_date=target_date,
        image_observer=image_observer,
    )
