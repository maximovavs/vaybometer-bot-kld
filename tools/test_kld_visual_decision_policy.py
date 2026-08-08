#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline regressions for KLD visual decision policy v3."""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kld_image_first import _visual_decision  # noqa: E402
from kld_visual_policy import (  # noqa: E402
    KLD_VISUAL_POLICY_VERSION,
    WEATHER_SCENE_ROUTES,
    apply_weather_scene_route,
    finalize_kld_provider_prompt,
    scene_policy_rejection,
    seasonal_guard_label,
    weather_route_key,
)


def _metadata(**overrides):
    data = {
        "forecast_date": "2026-08-08",
        "target_date": "2026-08-08",
        "post_type": "morning",
        "prompt_version": "v6+" + KLD_VISUAL_POLICY_VERSION,
        "scene_family": "curonian_spit_dunes",
        "scene_text": "Curonian Spit dunes",
        "composition": "wide diagonal shoreline composition",
        "weather_scenario": "cloudy",
        "wind_gust_category": "calm_to_breezy",
        "visibility_condition": "clear",
        "variation_attempt": "0",
    }
    data.update(overrides)
    return data


def fog_routes_to_low_visibility_geography() -> None:
    metadata = _metadata(visibility_condition="mist")
    routed = apply_weather_scene_route(metadata)
    assert routed["scene_route"] == "fog_visibility"
    assert routed["scene_family"] in WEATHER_SCENE_ROUTES["fog_visibility"]
    assert metadata["scene_family"] == routed["scene_family"]


def rain_routes_to_wet_or_sheltered_geography() -> None:
    metadata = _metadata(weather_scenario="rain", scene_family="yantarny_wide_beach")
    routed = apply_weather_scene_route(metadata)
    assert routed["scene_route"] == "rain"
    assert routed["scene_family"] in WEATHER_SCENE_ROUTES["rain"]


def strong_wind_routes_to_exposed_structured_coast() -> None:
    metadata = _metadata(
        weather_scenario="cloudy",
        wind_gust_category="gust_10_14",
        scene_family="quiet_lagoon_coast",
    )
    routed = apply_weather_scene_route(metadata)
    assert routed["scene_route"] == "strong_wind"
    assert routed["scene_family"] in WEATHER_SCENE_ROUTES["strong_wind"]


def calm_route_keeps_non_beach_options_available() -> None:
    metadata = _metadata(
        weather_scenario="cloudy",
        wind_gust_category="calm_to_breezy",
        scene_family="pine_forest_sea_path",
    )
    routed = apply_weather_scene_route(metadata)
    assert routed["scene_route"] == "calm"
    assert routed["scene_family"] == "pine_forest_sea_path"
    assert WEATHER_SCENE_ROUTES["calm"][:4] == (
        "kaliningrad_urban_coastal_view",
        "pine_forest_sea_path",
        "quiet_lagoon_coast",
        "zelenogradsk_promenade",
    )


def final_prompt_records_weather_route_and_routed_scene() -> None:
    metadata = _metadata(
        weather_scenario="rain",
        scene_family="yantarny_wide_beach",
    )
    prompt = "\n".join(
        [
            "Create a photorealistic KLD weather scene.",
            "Base scene: Baltic coast near Kaliningrad, dunes, pines, promenade, sea horizon.",
            "Controlled composition: legacy beach lock.",
            "Text restrictions: no text.",
        ]
    )
    finalized = finalize_kld_provider_prompt(prompt, metadata=metadata, date_key="2026-08-08")
    assert "Weather scene route: rain." in finalized
    assert metadata["scene_route"] == "rain"
    assert f"dominant scene family {metadata['scene_family']}" in finalized
    assert "Base scene:" not in finalized
    assert "Controlled composition:" not in finalized


def last_four_compositions_are_hard_blocked() -> None:
    history = [
        {"date": "2026-08-04", "scene_family": "quiet_lagoon_coast", "composition": "c1"},
        {"date": "2026-08-05", "scene_family": "zelenogradsk_promenade", "composition": "c2"},
        {"date": "2026-08-06", "scene_family": "pine_forest_sea_path", "composition": "c3"},
        {"date": "2026-08-07", "scene_family": "baltiysk_breakwater", "composition": "c4"},
    ]
    reason, match = scene_policy_rejection(
        history,
        scene_family="kaliningrad_urban_coastal_view",
        composition="c2",
    )
    assert reason == "composition_cooldown"
    assert match and match["composition"] == "c2"


def fifth_previous_composition_is_allowed() -> None:
    history = [
        {"date": "2026-08-03", "scene_family": "svetlogorsk_cliff_coast", "composition": "old"},
        {"date": "2026-08-04", "scene_family": "quiet_lagoon_coast", "composition": "c1"},
        {"date": "2026-08-05", "scene_family": "zelenogradsk_promenade", "composition": "c2"},
        {"date": "2026-08-06", "scene_family": "pine_forest_sea_path", "composition": "c3"},
        {"date": "2026-08-07", "scene_family": "baltiysk_breakwater", "composition": "c4"},
    ]
    reason, _ = scene_policy_rejection(
        history,
        scene_family="kaliningrad_urban_coastal_view",
        composition="old",
    )
    assert reason == ""


def visual_decision_is_single_readable_summary() -> None:
    result = {
        "backend": "stable_horde",
        "selected_scene_family": "baltiysk_breakwater",
        "selected_composition": "breakwater perspective line",
        "dedup_reason": "accepted",
        "dedup_distance": 21,
        "fallback_reason": "provider_failure",
        "visual_policy_version": KLD_VISUAL_POLICY_VERSION,
        "telegram_image_sent": True,
    }
    prompt_metadata = {
        "visual_policy_version": KLD_VISUAL_POLICY_VERSION,
        "metadata": {
            "forecast_date": "2026-08-08",
            "target_date": "2026-08-08",
            "weather_scenario": "cloudy",
            "visibility_condition": "clear",
            "wind_gust_category": "gust_10_14",
            "scene_route": "strong_wind",
        },
    }
    decision = _visual_decision(result, prompt_metadata, mode="morning")
    assert decision["backend"] == "stable_horde"
    assert decision["weather_main"] == "cloudy"
    assert decision["scene_route"] == "strong_wind"
    assert decision["seasonal_guard"] == "summer_green"
    assert decision["scene_family"] == "baltiysk_breakwater"
    assert decision["scene_macro_family"] == "breakwater_harbour"
    assert decision["composition"] == "breakwater perspective line"
    assert decision["content_guard"] == {"valid": True, "reason": "accepted"}
    assert decision["dedup"] == {"reason": "accepted", "distance": 21}
    assert decision["published"] is True


def season_label_tracks_target_date() -> None:
    assert seasonal_guard_label("2026-06-01") == "summer_green"
    assert seasonal_guard_label("2026-08-31") == "summer_green"
    assert seasonal_guard_label("2026-09-01") == "seasonal_default"


TESTS = [
    fog_routes_to_low_visibility_geography,
    rain_routes_to_wet_or_sheltered_geography,
    strong_wind_routes_to_exposed_structured_coast,
    calm_route_keeps_non_beach_options_available,
    final_prompt_records_weather_route_and_routed_scene,
    last_four_compositions_are_hard_blocked,
    fifth_previous_composition_is_allowed,
    visual_decision_is_single_readable_summary,
    season_label_tracks_target_date,
]


def main() -> None:
    for test in TESTS:
        test()
        print(f"PASS: {test.__name__}")
    print(f"OK: {len(TESTS)} KLD visual decision policy checks passed")


if __name__ == "__main__":
    main()
