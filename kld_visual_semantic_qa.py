#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Injection-only semantic QA verdict normalisation for KLD AI images.

Inactive in production: nothing constructs an evaluator, and
``execute_image_delivery`` only calls this when a caller injects one.
The evaluator is an untrusted dependency.  Only a well-formed,
hash-bound, temporally aligned ACCEPT may let an already guard-accepted
candidate continue.  Every other outcome is REJECT or UNKNOWN, and the
caller treats both as a hard candidate rejection.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Callable, Mapping

SCHEMA = "kld_independent_semantic_qa_v1"
DECISIONS = ("ACCEPT", "REJECT", "UNKNOWN")
DEFAULT_TIMEOUT_S = 20.0
_OBSERVATIONS = {
    "moon_visibility": {"not_visible", "visible", "unclear"},
    "moon_shape": {"crescent", "quarter", "gibbous", "full_disk", "unclear", "not_applicable"},
    "dominant_moon": {"yes", "no", "unclear"},
    "visibility_appearance": {"normal", "mist", "dense_fog", "ambiguous"},
    "weather_appearance": {"cloudy", "rain", "clear", "other", "ambiguous"},
}
_CONFLICT_CODES = {"moon_phase_conflict", "invented_dense_fog", "other_weather_conflict"}
# Only apparent shape can be checked by the current evaluator schema.
# Waxing/waning orientation and illumination remain outside this contract.
_PHASE_SHAPES = {
    "new": frozenset(),
    "waxing_crescent": frozenset({"crescent"}),
    "first_quarter": frozenset({"quarter"}),
    "waxing_gibbous": frozenset({"gibbous"}),
    "full": frozenset({"full_disk"}),
    "waning_gibbous": frozenset({"gibbous"}),
    "last_quarter": frozenset({"quarter"}),
    "waning_crescent": frozenset({"crescent"}),
}


@dataclass(frozen=True)
class SemanticQaVerdict:
    decision: str
    reason: str
    image_sha256: str
    evaluator_version: str = ""

    @property
    def accepted(self) -> bool:
        return self.decision == "ACCEPT"

    def as_payload(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "reason": self.reason,
            "image_sha256": self.image_sha256,
            "evaluator_version": self.evaluator_version,
        }


def _call_with_timeout(evaluator: Callable[..., Any], image: bytes, facts: Mapping[str, Any], timeout_s: float) -> Any:
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="kld-semantic-qa")
    try:
        return pool.submit(evaluator, image, dict(facts)).result(timeout=timeout_s)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def evaluate_semantic_qa(
    evaluator: Callable[[bytes, Mapping[str, Any]], Any],
    image: bytes | None,
    facts: Mapping[str, Any],
    *,
    expected_target_date: str,
    expected_post_type: str,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> SemanticQaVerdict:
    if not isinstance(image, bytes) or not image:
        return SemanticQaVerdict("UNKNOWN", "image_unreadable", "")
    digest = sha256(image).hexdigest()

    def verdict(decision: str, reason: str, version: str = "") -> SemanticQaVerdict:
        return SemanticQaVerdict(decision, reason, digest, version)

    # Temporal alignment: facts must describe the exact post being illustrated.
    if not isinstance(facts, Mapping):
        return verdict("UNKNOWN", "facts_missing")
    if str(facts.get("target_date") or "") != str(expected_target_date or "") or not expected_target_date:
        return verdict("UNKNOWN", "temporal_mismatch")
    if str(facts.get("post_type") or "") != str(expected_post_type or ""):
        return verdict("UNKNOWN", "temporal_mismatch")
    for key in ("weather_target_date", "visibility_target_date", "lunar_target_date"):
        if key in facts and str(facts.get(key) or "") != str(expected_target_date):
            return verdict("UNKNOWN", "temporal_mismatch")
    try:
        raw = _call_with_timeout(evaluator, image, facts, timeout_s)
    except (FutureTimeout, TimeoutError):
        return verdict("UNKNOWN", "qa_timeout")
    except Exception:
        return verdict("UNKNOWN", "qa_unavailable")
    if not isinstance(raw, Mapping):
        return verdict("UNKNOWN", "schema_error")
    version = raw.get("model_or_evaluator_version")
    if raw.get("qa_schema_version") != SCHEMA or not isinstance(version, str) or not version:
        return verdict("UNKNOWN", "schema_error")
    if raw.get("raw_image_sha256") != digest:
        return verdict("UNKNOWN", "image_hash_mismatch", version)
    decision = raw.get("decision")
    if decision not in DECISIONS:
        return verdict("UNKNOWN", "invalid_decision", version)
    observations = raw.get("observations")
    if not isinstance(observations, Mapping):
        return verdict("UNKNOWN", "missing_observations", version)
    for key, allowed in _OBSERVATIONS.items():
        value = observations.get(key)
        if not isinstance(value, str) or value not in allowed:
            return verdict("UNKNOWN", "invalid_observations", version)
    codes = raw.get("reason_codes")
    if not isinstance(codes, list) or any(not isinstance(code, str) for code in codes):
        return verdict("UNKNOWN", "invalid_reason_codes", version)
    if decision == "ACCEPT":
        if _CONFLICT_CODES.intersection(codes):
            return verdict("UNKNOWN", "contradictory_verdict", version)
        if any(value in {"unclear", "ambiguous"} for value in observations.values()):
            return verdict("UNKNOWN", "insufficient_visual_evidence", version)
        moon_visibility = observations["moon_visibility"]
        moon_shape = observations["moon_shape"]
        dominant_moon = observations["dominant_moon"]
        if moon_visibility == "not_visible" and (
            moon_shape != "not_applicable" or dominant_moon == "yes"
        ):
            return verdict("UNKNOWN", "contradictory_observations", version)
        if moon_visibility == "visible" and moon_shape == "not_applicable":
            return verdict("UNKNOWN", "contradictory_observations", version)
        lunar = str(facts.get("lunar_phase") or "")
        if moon_visibility == "visible":
            # Unknown or missing source phase cannot substantiate any visible moon.
            if lunar not in _PHASE_SHAPES:
                return verdict("UNKNOWN", "lunar_phase_unknown", version)
            if moon_shape not in _PHASE_SHAPES[lunar]:
                return verdict("REJECT", "moon_phase_conflict", version)
            if expected_post_type == "morning" and dominant_moon == "yes":
                return verdict("REJECT", "morning_dominant_moon", version)
        if str(facts.get("visibility_condition") or "") == "clear" and observations["visibility_appearance"] == "dense_fog":
            return verdict("REJECT", "invented_dense_fog", version)
    return verdict(decision, codes[0] if codes else "observed_consistent", version)


__all__ = ["SCHEMA", "SemanticQaVerdict", "evaluate_semantic_qa", "DEFAULT_TIMEOUT_S"]
