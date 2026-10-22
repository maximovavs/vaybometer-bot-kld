"""Offline-only independent image observation -> strict R4 semantic-QA schema.

The observer receives raw image bytes ONLY. It cannot access forecast facts,
location, dates, image prompts, source names or publication metadata. A JSON
observation is not an authenticated photo timestamp or provider statement.
No model, network or production evaluator is constructed in this module.
"""
from __future__ import annotations

from hashlib import sha256
import json
from typing import Any, Callable, Mapping

from kld_semantic_qa_evaluator import apply_weather_veto

# Keep the R4 versioned wire schema without importing the runtime validator.
SCHEMA = "kld_independent_semantic_qa_v1"

OBS_SCHEMA = "kld_image_only_observer_v1"
REQUIRED = {
    "moon_visibility": {"not_visible", "visible", "unclear"},
    "moon_shape": {"crescent", "quarter", "gibbous", "full_disk", "unclear", "not_applicable"},
    "dominant_moon": {"yes", "no", "unclear"},
    "visibility_appearance": {"normal", "mist", "dense_fog", "ambiguous"},
    "weather_appearance": {"clear", "cloudy", "drizzle", "rain", "snow", "fog", "storm", "other", "ambiguous"},
    "cloud_appearance": {"clear", "scattered", "overcast", "unclear"},
    "precipitation_appearance": {"none", "drizzle", "rain", "snow", "mixed", "unclear"},
    "wind_appearance": {"calm", "windy", "strong", "unclear"},
    "sea_appearance": {"calm", "choppy", "rough", "unclear"},
}
_ALLOWED_KEYS = {"observation_schema", "model_or_evaluator_version", "decision", "reason_codes", "observations"}


def _normalize(raw: Any, image: bytes) -> dict[str, Any]:
    # Reject facts/date/provenance/metadata injection, extra fields and unknown
    # enums. An observer may downgrade to REJECT/UNKNOWN, never override guards.
    if isinstance(raw, (bytes, str)):
        if len(raw) > 16_384:
            raise ValueError("oversized observer result")
        raw = json.loads(raw)
    if not isinstance(raw, Mapping) or set(raw) != _ALLOWED_KEYS:
        raise ValueError("invalid observer result keys")
    if raw["observation_schema"] != OBS_SCHEMA:
        raise ValueError("observer schema mismatch")
    version = raw["model_or_evaluator_version"]
    if not isinstance(version, str) or not 0 < len(version) <= 100:
        raise ValueError("observer version missing")
    decision = raw["decision"]
    if decision not in ("ACCEPT", "REJECT", "UNKNOWN"):
        raise ValueError("observer decision invalid")
    obs = raw["observations"]
    if not isinstance(obs, Mapping) or set(obs) != set(REQUIRED):
        raise ValueError("observer observation keys invalid")
    if any(not isinstance(obs[k], str) or obs[k] not in allowed
           for k, allowed in REQUIRED.items()):
        raise ValueError("observer observation value invalid")
    codes = raw["reason_codes"]
    if not isinstance(codes, list) or len(codes) > 8 or any(
            not isinstance(c, str) or not 0 < len(c) <= 96 for c in codes):
        raise ValueError("observer reason codes invalid")
    if decision == "ACCEPT":
        if codes or any(v in {"unclear", "ambiguous"} for v in obs.values()):
            raise ValueError("ambiguous ACCEPT")
        precipitation = obs["precipitation_appearance"]
        visible_weather = obs["weather_appearance"]
        matches = {"clear": {"none"}, "cloudy": {"none"},
                   "drizzle": {"drizzle"}, "rain": {"rain"},
                   "snow": {"snow"}, "fog": {"none"},
                   "storm": {"none", "drizzle", "rain", "snow", "mixed"},
                   "other": {"none", "mixed"}}
        if precipitation not in matches.get(visible_weather, set()):
            raise ValueError("contradictory weather observations")
        if (visible_weather == "clear" and obs["cloud_appearance"] == "overcast"):
            raise ValueError("contradictory clouds")
        if visible_weather == "fog" and obs["visibility_appearance"] == "normal":
            raise ValueError("contradictory fog visibility")
    # Intentionally exclude image_date, source/provenance, weather window,
    # location and facts from the image-only observation schema.
    return {
        "qa_schema_version": SCHEMA,
        "model_or_evaluator_version": version,
        "raw_image_sha256": sha256(image).hexdigest(),
        "observation_contract": OBS_SCHEMA,
        "decision": decision,
        "reason_codes": list(codes),
        "observations": dict(obs),
    }


def wrap_image_only_observer(observer: Callable[[bytes], Any]) -> Callable[[bytes, Mapping[str, Any]], Any]:
    """Pure local adapter. No provider creation and no source facts to observer."""
    def evaluate(image: bytes, _facts: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(image, bytes) or not image:
            raise ValueError("raw image unavailable")
        raw = _normalize(observer(image), image)
        return apply_weather_veto(raw, _facts, image=image)
    evaluate._kld_killable_image_observer = True
    return evaluate


__all__ = ["OBS_SCHEMA", "REQUIRED", "wrap_image_only_observer"]
