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
