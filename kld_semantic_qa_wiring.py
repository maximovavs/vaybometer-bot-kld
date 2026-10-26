"""Non-activating, opt-in semantic QA injection selection for KLD.

This module does not read environment flags or contact providers.  The
production CLI may request a selector only behind an OFF-by-default flag.
If source facts/evaluator are not ready it returns a *non-None* fail-closed
injection, so enabled mode can never quietly bypass semantic QA.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from kld_semantic_qa_facts import FactsUnavailable, build_verified_facts
from kld_semantic_qa_evaluator import fail_closed_observer, wrap_observer


@dataclass(frozen=True)
class Injection:
    evaluator: Callable[[bytes, Mapping[str, Any]], Any] | None
    facts: Mapping[str, Any] | None
    status: str
    reason: str


def select_semantic_injection(
    *, enabled: bool, source_snapshot: Mapping[str, Any] | None,
    target_date: str, post_type: str, publication_target_date: str,
    observer: Callable[[bytes, Mapping[str, Any]], Any] | None = None,
) -> Injection:
    if not enabled:
        return Injection(None, None, "disabled", "flag_off")
    # Core v1 validator requires target/date/post fields to reach the observer.
    # These are never treated as evidence of weather correctness.
    fallback_facts = {
        "target_date": target_date,
        "post_type": post_type,
        "weather_target_date": target_date,
        "visibility_target_date": target_date,
        "lunar_target_date": target_date,
        "visibility_condition": "unknown",
        "forecast_window": "unknown",
    }
    try:
        facts = build_verified_facts(
            source_snapshot or {},
            target_date=target_date, post_type=post_type,
            publication_target_date=publication_target_date,
        )
    except FactsUnavailable as exc:
        return Injection(fail_closed_observer, fallback_facts, "fail_closed", exc.reason)
    if observer is None:
        return Injection(fail_closed_observer, facts, "fail_closed", "evaluator_unavailable")
    return Injection(wrap_observer(observer), facts, "ready_offline_injection", "")


def select_offline_collector_injection(
    *,
    enabled: bool,
    collector_response: Mapping[str, Any] | None,
    collector_run_id: str,
    expected_run_id: str,
    expected_raw_sha256: str,
    target_date: str,
    post_type: str,
    publication_target_date: str,
    image_observer: Callable[[bytes], Any] | None,
    now_utc: Any = None,
) -> Injection:
    """Collector-to-observer seam; real CLI uses this only behind verified HMAC.

    The expected run id and digest MUST come from a trusted run-local context,
    never unsigned response fields. R5-E's consumer verifies an ephemeral
    producer HMAC first. This does not establish external provider identity.
    """
    if not enabled:
        return Injection(None, None, "disabled", "flag_off")
    from hmac import compare_digest
    from kld_semantic_qa_source import build_trusted_snapshot
    from kld_semantic_qa_observer import wrap_image_only_observer

    def refuse(reason: str) -> Injection:
        return Injection(fail_closed_observer, None, "fail_closed", reason)

    if (not isinstance(collector_run_id, str) or not isinstance(expected_run_id, str)
            or not 0 < len(expected_run_id) <= 128
            or not compare_digest(collector_run_id, expected_run_id)):
        return refuse("collector_run_mismatch")
    if (not isinstance(expected_raw_sha256, str) or len(expected_raw_sha256) != 64
            or any(ch not in "0123456789abcdef" for ch in expected_raw_sha256)):
        return refuse("collector_digest_unavailable")
    if not isinstance(collector_response, Mapping):
        return refuse("collector_source_unavailable")
    envelope = collector_response.get("_kld_r5_capture")
    if (not isinstance(envelope, Mapping) or
            not isinstance(envelope.get("raw_payload_sha256"), str) or
            not compare_digest(envelope["raw_payload_sha256"], expected_raw_sha256)):
        return refuse("collector_digest_mismatch")
    try:
        snapshot = build_trusted_snapshot(
            collector_response, target_date=target_date,
            post_type=post_type, publication_target_date=publication_target_date,
            now_utc=now_utc,
        )
    except (FactsUnavailable, TypeError, ValueError, KeyError) as exc:
        return refuse(exc.reason if isinstance(exc, FactsUnavailable) else "collector_source_invalid")
    if image_observer is None or not callable(image_observer):
        return select_semantic_injection(
            enabled=True, source_snapshot=snapshot,
            target_date=target_date, post_type=post_type,
            publication_target_date=publication_target_date, observer=None,
        )
    return select_semantic_injection(
        enabled=True, source_snapshot=snapshot,
        target_date=target_date, post_type=post_type,
        publication_target_date=publication_target_date,
        observer=wrap_image_only_observer(image_observer),
    )
