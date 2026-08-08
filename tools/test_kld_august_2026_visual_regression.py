#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regression fixtures derived from the KLD channel export for 01-08 Aug 2026.

The fixtures intentionally assert policy invariants rather than reproducing old
provider images. They preserve the real weather categories/gust regimes that
exposed the repetitive beach visual problem.
"""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kld_visual_policy import (  # noqa: E402
    WEATHER_SCENE_ROUTES,
    apply_weather_scene_route,
    scene_macro_family,
    scene_policy_rejection,
    seasonal_guard_label,
    weather_route_key,
)


AUGUST_CASES = (
    {
        "date": "2026-08-01",
        "weather_scenario": "rain",
        "wind_gust_category": "calm_to_breezy",
        "visibility_condition": "clear",
        "expected_route": "rain",
    },
    {
        "date": "2026-08-02",
        "weather_scenario": "drizzle",
        "wind_gust_category": "gust_7_9",
        "visibility_condition": "clear",
        "expected_route": "rain",
    },
    {
        "date": "2026-08-03",
        "weather_scenario": "cloudy",
        "wind_gust_category": "calm_to_breezy",
        "visibility_condition": "clear",
        "expected_route": "calm",
    },
    {
        "date": "2026-08-04",
        "weather_scenario": "cloudy",
        "wind_gust_category": "gust_7_9",
        "visibility_condition": "clear",
        "expected_route": "calm",
    },
    {
        "date": "2026-08-05",
        "weather_scenario": "cloudy",
        "wind_gust_category": "gust_7_9",
        "visibility_condition": "clear",
        "expected_route": "calm",
    },
    {
        "date": "2026-08-06",
        "weather_scenario": "rain",
        "wind_gust_category": "calm_to_breezy",
        "visibility_condition": "clear",
        "expected_route": "rain",
    },
    {
        "date": "2026-08-07",
        "weather_scenario": "cloudy",
        "wind_gust_category": "gust_10_14",
        "visibility_condition": "clear",
        "expected_route": "strong_wind",
    },
    {
        "date": "2026-08-08",
        "weather_scenario": "cloudy",
        "wind_gust_category": "gust_10_14",
        "visibility_condition": "clear",
        "expected_route": "strong_wind",
    },
)


def _metadata(case, attempt: int = 0):
    return {
        "forecast_date": case["date"],
        "target_date": case["date"],
        "post_type": "morning",
        "scene_family": "curonian_spit_dunes",
        "scene_text": "Curonian Spit dunes",
        "composition": "wide diagonal shoreline composition",
        "weather_scenario": case["weather_scenario"],
        "wind_gust_category": case["wind_gust_category"],
        "visibility_condition": case["visibility_condition"],
        "variation_attempt": str(attempt),
    }


def all_august_cases_use_summer_green() -> None:
    for case in AUGUST_CASES:
        assert seasonal_guard_label(case["date"]) == "summer_green", case


def august_weather_routes_match_real_regimes() -> None:
    for case in AUGUST_CASES:
        metadata = _metadata(case)
        assert weather_route_key(metadata) == case["expected_route"], case
        routed = apply_weather_scene_route(metadata)
        assert routed["scene_route"] == case["expected_route"], case
        assert routed["scene_family"] in WEATHER_SCENE_ROUTES[case["expected_route"]], case


def calm_days_are_not_forced_to_open_beach() -> None:
    calm_route = WEATHER_SCENE_ROUTES["calm"]
    assert scene_macro_family(calm_route[0]) == "promenade_urban"
    assert scene_macro_family(calm_route[1]) == "forest_road"
    assert scene_macro_family(calm_route[2]) == "lagoon"
    assert scene_macro_family(calm_route[3]) == "promenade_urban"


def strong_wind_days_prioritize_breakwater_cliff_overlook() -> None:
    route = WEATHER_SCENE_ROUTES["strong_wind"]
    assert route[:3] == (
        "baltiysk_breakwater",
        "svetlogorsk_cliff_coast",
        "elevated_baltic_overlook",
    )


def two_open_beaches_in_five_block_another_open_beach() -> None:
    history = [
        {"date": "2026-08-03", "scene_family": "curonian_spit_dunes", "composition": "c1"},
        {"date": "2026-08-04", "scene_family": "pine_forest_sea_path", "composition": "c2"},
        {"date": "2026-08-05", "scene_family": "yantarny_wide_beach", "composition": "c3"},
        {"date": "2026-08-06", "scene_family": "zelenogradsk_promenade", "composition": "c4"},
        {"date": "2026-08-07", "scene_family": "quiet_lagoon_coast", "composition": "c5"},
    ]
    reason, _ = scene_policy_rejection(
        history,
        scene_family="stormy_open_baltic",
        composition="fresh-composition",
    )
    assert reason == "scene_macro_cooldown"


def previous_three_scenes_and_four_compositions_are_hard() -> None:
    history = [
        {"date": "2026-08-04", "scene_family": "curonian_spit_dunes", "composition": "c1"},
        {"date": "2026-08-05", "scene_family": "pine_forest_sea_path", "composition": "c2"},
        {"date": "2026-08-06", "scene_family": "zelenogradsk_promenade", "composition": "c3"},
        {"date": "2026-08-07", "scene_family": "quiet_lagoon_coast", "composition": "c4"},
    ]
    scene_reason, _ = scene_policy_rejection(
        history,
        scene_family="quiet_lagoon_coast",
        composition="fresh",
    )
    composition_reason, _ = scene_policy_rejection(
        history,
        scene_family="baltiysk_breakwater",
        composition="c1",
    )
    assert scene_reason == "scene_cooldown"
    assert composition_reason == "composition_cooldown"


TESTS = [
    all_august_cases_use_summer_green,
    august_weather_routes_match_real_regimes,
    calm_days_are_not_forced_to_open_beach,
    strong_wind_days_prioritize_breakwater_cliff_overlook,
    two_open_beaches_in_five_block_another_open_beach,
    previous_three_scenes_and_four_compositions_are_hard,
]


def main() -> None:
    for test in TESTS:
        test()
        print(f"PASS: {test.__name__}")
    print(f"OK: {len(TESTS)} KLD August 2026 visual regressions passed")


if __name__ == "__main__":
    main()
