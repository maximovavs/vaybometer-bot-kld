"""Strict, offline-only provenance adapter for KLD visual semantic QA.

No external reads. Values must be independently supplied by trusted, date-bound
collection code; FORMAT_V2 text, prompt metadata and LLM output are not sources.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Mapping

_ALLOWED_WEATHER = frozenset({"clear", "partly_cloudy", "cloudy", "drizzle", "rain", "fog", "snow", "storm"})
_ALLOWED_VISIBILITY = frozenset({"clear", "mist", "fog", "dense_fog", "reduced_visibility", "dust_haze", "mixed_visibility"})
_ALLOWED_LUNAR = frozenset({"new", "waxing_crescent", "first_quarter", "waxing_gibbous", "full", "waning_gibbous", "last_quarter", "waning_crescent"})
_ALLOWED_STORM = frozenset({"none", "confirmed_gust", "confirmed_warning", "confirmed_weather"})

@dataclass(frozen=True)
class FactsUnavailable(ValueError):
    reason: str
    def __str__(self) -> str:
        return self.reason


def _section(snapshot: Mapping[str, Any], name: str, *, target: str, window: str | None) -> Mapping[str, Any]:
    section = snapshot.get(name)
    if not isinstance(section, Mapping) or section.get("source_status") != "verified":
        raise FactsUnavailable(f"{name}_source_unavailable")
    if section.get("target_date") != target:
        raise FactsUnavailable(f"{name}_target_mismatch")
    # A genuine collection timestamp and source id are mandatory; a generated
    # prompt, a generic 'known' label, or a score cannot claim provenance.
    if not isinstance(section.get("source_id"), str) or not section["source_id"].strip():
        raise FactsUnavailable(f"{name}_source_unavailable")
    retrieved = section.get("retrieved_at_utc")
    if not isinstance(retrieved, str) or not retrieved.endswith("Z"):
        raise FactsUnavailable(f"{name}_source_unavailable")
    try:
        stamp = datetime.fromisoformat(retrieved.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        raise FactsUnavailable(f"{name}_source_unavailable") from None
    if stamp.tzinfo is None:
        raise FactsUnavailable(f"{name}_source_unavailable")
    if window is not None and section.get("window") != window:
        raise FactsUnavailable(f"{name}_window_mismatch")
    return section


def build_verified_facts(
    snapshot: Mapping[str, Any], *, target_date: str, post_type: str,
    publication_target_date: str, region: str = "kaliningrad"
) -> dict[str, Any]:
    """Create versioned facts; reject every missing/mismatched factual source.

    snapshot: {weather:{...}, visibility:{...}, lunar:{...}}, produced by
    upstream trusted collectors, never by an image evaluator.
    """
    if not isinstance(snapshot, Mapping):
        raise FactsUnavailable("facts_missing")
    if region != "kaliningrad" or post_type not in {"morning", "evening"}:
        raise FactsUnavailable("region_or_post_type_invalid")
    try:
        date.fromisoformat(target_date)
        date.fromisoformat(publication_target_date)
    except (TypeError, ValueError):
        raise FactsUnavailable("target_date_invalid") from None
    if target_date != publication_target_date:
        raise FactsUnavailable("publication_target_mismatch")
    window = "current_morning" if post_type == "morning" else "tomorrow_morning"
    weather = _section(snapshot, "weather", target=target_date, window=window)
    visibility = _section(snapshot, "visibility", target=target_date, window=window)
    lunar = _section(snapshot, "lunar", target=target_date, window=None)
    if weather.get("weather_main") not in _ALLOWED_WEATHER:
        raise FactsUnavailable("weather_source_unavailable")
    if visibility.get("visibility_condition") not in _ALLOWED_VISIBILITY:
        raise FactsUnavailable("visibility_source_unavailable")
    if lunar.get("lunar_phase") not in _ALLOWED_LUNAR:
        raise FactsUnavailable("lunar_source_unavailable")
    storm = weather.get("storm_signal")
    if storm not in _ALLOWED_STORM:
        raise FactsUnavailable("storm_source_unavailable")
    if weather["weather_main"] == "storm" and storm == "none":
        raise FactsUnavailable("storm_source_unavailable")
    if weather["weather_main"] != "storm" and storm != "none":
        raise FactsUnavailable("storm_weather_contradiction")
    return {
        "facts_version": "kld_visual_semantic_facts_v2",
        "region": region,
        "target_date": target_date,
        "post_type": post_type,
        "publication_target_date": publication_target_date,
        "weather_target_date": target_date,
        "visibility_target_date": target_date,
        "lunar_target_date": target_date,
        "weather_main": weather["weather_main"],
        "weather_window": window,
        "storm_signal": storm,
        "visibility_condition": visibility["visibility_condition"],
        "visibility_forecast_window": window if visibility["visibility_condition"] != "clear" else "none",
        "forecast_window": window,
        "lunar_phase": lunar["lunar_phase"],
        "source_provenance": {
            name: {"source_id": section["source_id"], "retrieved_at_utc": section["retrieved_at_utc"]}
            for name, section in (("weather", weather), ("visibility", visibility), ("lunar", lunar))
        },
    }
