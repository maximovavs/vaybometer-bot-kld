"""Strict supplementary KLD weather, storm, and photographed-window QA.

An injected independent observer returns a v1-compatible schema plus v2
observation fields. The existing kld_visual_semantic_qa module retains final
control over Moon, visibility, SHA-256, dates and upstream veto.
No network provider or production evaluator is constructed here.
"""
from __future__ import annotations

from hashlib import sha256
from typing import Any, Callable, Mapping

from kld_semantic_qa_facts import FactsUnavailable

_VALID_WINDOWS = frozenset({"current_morning", "tomorrow_morning"})
_VALID_WIND = frozenset({"calm", "windy", "strong", "unclear"})
_VALID_SEA = frozenset({"calm", "choppy", "rough", "unclear"})
_VALID_IMAGE_WINDOW = frozenset({"current_morning", "tomorrow_morning", "unknown"})


def _fail(raw: Mapping[str, Any], decision: str, reason: str) -> dict[str, Any]:
    result = dict(raw)
    result["decision"] = decision
    result["reason_codes"] = [reason]
    return result


def apply_weather_veto(raw: Any, facts: Mapping[str, Any], *, image: bytes) -> Any:
    """Only downgrade a claimed ACCEPT. Never upgrade an evaluator verdict."""
    if not isinstance(raw, Mapping) or raw.get("decision") != "ACCEPT":
        return raw
    # Let the unchanged source validator reject a bad hash or bad schema.
    if raw.get("raw_image_sha256") != sha256(image).hexdigest():
        return raw
    if facts.get("facts_version") != "kld_visual_semantic_facts_v2":
        return _fail(raw, "UNKNOWN", "facts_missing")
    if facts.get("target_date") != facts.get("publication_target_date"):
        return _fail(raw, "UNKNOWN", "publication_target_mismatch")
    obs = raw.get("observations")
    if not isinstance(obs, Mapping):
        return raw
    expected_window = facts.get("weather_window")
    image_only = raw.get("observation_contract") == "kld_image_only_observer_v1"
    # Image-only observers CANNOT know the date/time an illustration depicts.
    # R5 verified facts select the forecast window; the observer supplies only
    # visible weather. Keep the original R4 pictured-window policy unchanged.
    if image_only:
        if (expected_window not in _VALID_WINDOWS
                or facts.get("forecast_window") != expected_window
                or not isinstance(facts.get("source_provenance"), Mapping)
                or set(facts["source_provenance"]) != {"weather", "visibility", "lunar"}):
            return _fail(raw, "UNKNOWN", "image_window_unknown")
        if any(k in obs for k in ("pictured_weather_window", "pictured_visibility_window",
                                   "image_date", "image_timestamp")):
            return _fail(raw, "UNKNOWN", "observer_claimed_image_time")
    pictured_weather = expected_window if image_only else obs.get("pictured_weather_window")
    if expected_window not in _VALID_WINDOWS or pictured_weather not in _VALID_IMAGE_WINDOW:
        return _fail(raw, "UNKNOWN", "image_window_unknown")
    if pictured_weather == "unknown":
        return _fail(raw, "UNKNOWN", "image_window_unknown")
    if pictured_weather != expected_window:
        return _fail(raw, "UNKNOWN", "weather_window_mismatch")
    visibility_condition = facts.get("visibility_condition")
    if visibility_condition not in {"clear", "mist", "fog", "dense_fog", "reduced_visibility", "dust_haze", "mixed_visibility"}:
        return _fail(raw, "UNKNOWN", "visibility_source_unavailable")
    pictured_visibility = expected_window if image_only else obs.get("pictured_visibility_window")
    if pictured_visibility not in _VALID_IMAGE_WINDOW or pictured_visibility == "unknown":
        return _fail(raw, "UNKNOWN", "image_window_unknown")
    if pictured_visibility != facts.get("forecast_window"):
        return _fail(raw, "UNKNOWN", "visibility_window_mismatch")
    weather = facts.get("weather_main")
    pictured = obs.get("weather_appearance")
    if weather not in {"clear", "partly_cloudy", "cloudy", "drizzle", "rain", "fog", "snow", "storm"}:
        return _fail(raw, "UNKNOWN", "weather_source_unavailable")
    # Reasonable ambiguity is UNKNOWN, not optimism.
    if pictured in {None, "other", "ambiguous"} and weather != "storm":
        return _fail(raw, "UNKNOWN", "weather_visual_unknown")
    if weather == "clear" and pictured in {"rain", "drizzle", "snow", "storm"}:
        return _fail(raw, "REJECT", "invented_precipitation")
    if weather == "clear" and pictured == "cloudy":
        return _fail(raw, "REJECT", "unexpected_overcast")
    if visibility_condition == "clear" and obs.get("visibility_appearance") in {"mist", "dense_fog"}:
        return _fail(raw, "REJECT", "invented_reduced_visibility")
    if weather in {"rain", "drizzle"} and pictured in {"clear", "snow"}:
        return _fail(raw, "REJECT", "forecast_rain_not_shown")
    if weather in {"rain", "drizzle"} and pictured == "cloudy":
        return _fail(raw, "UNKNOWN", "precipitation_visual_unknown")
    if weather == "snow" and pictured in {"clear", "rain", "drizzle"}:
        return _fail(raw, "REJECT", "forecast_snow_not_shown")
    if image_only and weather == "snow" and pictured in {"cloudy", "fog", "other"}:
        return _fail(raw, "UNKNOWN", "snow_visual_unknown")
    if image_only and weather == "clear" and pictured == "fog":
        return _fail(raw, "REJECT", "invented_reduced_visibility")
    if weather == "storm":
        if facts.get("storm_signal") not in {"confirmed_gust", "confirmed_warning", "confirmed_weather"}:
            return _fail(raw, "UNKNOWN", "storm_source_unavailable")
        wind = obs.get("wind_appearance")
        sea = obs.get("sea_appearance")
        if wind not in _VALID_WIND or sea not in _VALID_SEA or "unclear" in (wind, sea):
            return _fail(raw, "UNKNOWN", "storm_visual_unknown")
        if wind == "calm" and sea == "calm":
            return _fail(raw, "REJECT", "storm_conditions_not_shown")
        # Wind-driven storm need not show lightning or rainfall.
        if wind not in {"strong", "windy"} and sea != "rough":
            return _fail(raw, "UNKNOWN", "storm_visual_unknown")
    return dict(raw)


def wrap_observer(
    observer: Callable[[bytes, Mapping[str, Any]], Any],
) -> Callable[[bytes, Mapping[str, Any]], Any]:
    """Wrap only an explicitly injected observer; never connect a real model."""
    def evaluate(image: bytes, facts: Mapping[str, Any]) -> Any:
        raw = observer(image, facts)
        return apply_weather_veto(raw, facts, image=image)
    # Preserve the process-isolation contract through the R4 weather veto.
    if getattr(observer, "_kld_killable_image_observer", False):
        evaluate._kld_killable_image_observer = True
    return evaluate


def fail_closed_observer(_image: bytes, _facts: Mapping[str, Any]) -> dict[str, Any]:
    """Do not silently allow AI imagery if the opt-in adapter is unavailable."""
    raise FactsUnavailable("evaluator_unavailable")


def make_offline_fake(decide: Callable[[bytes, Mapping[str, Any]], Any]) -> Callable[[bytes, Mapping[str, Any]], Any]:
    """Tests only. The fake must never be selected by production CLI flags."""
    return wrap_observer(decide)
