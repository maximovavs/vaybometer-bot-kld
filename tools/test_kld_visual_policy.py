#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline regression checks for shared KLD visual policy."""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from image_prompt_kld_morning import build_kld_morning_prompt  # noqa: E402
from kld_visual_policy import (  # noqa: E402
    PROVIDER_NEGATIVE_GUARD,
    SUMMER_VEGETATION_CUE,
    apply_scene_composition_policy,
    apply_summer_vegetation_guard,
    build_stable_horde_prompt_parts,
    build_scene_contract,
    scene_composition_compatible,
    scene_macro_family,
    scene_policy_rejection,
)


def kld_policy_august_is_green() -> None:
    result = apply_summer_vegetation_guard("base", "2026-08-01")
    assert SUMMER_VEGETATION_CUE in result
    assert "visibly natural green" in result


def kld_policy_autumn_does_not_force_summer_green() -> None:
    result = apply_summer_vegetation_guard("base", "2026-09-01")
    assert result == "base"


def kld_policy_scene_contract_is_authoritative_not_beach_locked() -> None:
    contract = build_scene_contract(
        {
            "scene_family": "pine_forest_sea_path",
            "scene_text": "pine forest sea path opening toward the Baltic shore",
            "composition": "pine-framed side composition",
        }
    )
    assert "pine_forest_sea_path" in contract
    assert "scene family is authoritative" in contract
    assert "beach, dunes, pines and sea filling the whole image" not in contract


def kld_policy_macro_families_are_stable() -> None:
    assert scene_macro_family("curonian_spit_dunes") == "open_beach_dunes"
    assert scene_macro_family("yantarny_wide_beach") == "open_beach_dunes"
    assert scene_macro_family("zelenogradsk_promenade") == "promenade_urban"
    assert scene_macro_family("pine_forest_sea_path") == "forest_road"


def kld_policy_recent_scene_is_hard_blocked() -> None:
    history = [
        {"scene_family": "quiet_lagoon_coast"},
        {"scene_family": "zelenogradsk_promenade"},
        {"scene_family": "pine_forest_sea_path"},
    ]
    reason, matched = scene_policy_rejection(
        history,
        scene_family="pine_forest_sea_path",
        scene_cooldown=3,
    )
    assert reason == "scene_cooldown"
    assert matched and matched["scene_family"] == "pine_forest_sea_path"


def kld_policy_open_beach_is_limited_to_two_of_five() -> None:
    history = [
        {"scene_family": "zelenogradsk_promenade"},
        {"scene_family": "curonian_spit_dunes"},
        {"scene_family": "pine_forest_sea_path"},
        {"scene_family": "yantarny_wide_beach"},
        {"scene_family": "quiet_lagoon_coast"},
    ]
    reason, _ = scene_policy_rejection(history, scene_family="stormy_open_baltic")
    assert reason == "scene_macro_cooldown"


def kld_policy_non_beach_scene_survives_beach_quota() -> None:
    history = [
        {"scene_family": "curonian_spit_dunes"},
        {"scene_family": "zelenogradsk_promenade"},
        {"scene_family": "yantarny_wide_beach"},
        {"scene_family": "quiet_lagoon_coast"},
    ]
    reason, _ = scene_policy_rejection(history, scene_family="baltiysk_breakwater")
    assert reason == ""


def kld_policy_august_morning_prompt_has_no_legacy_beach_lock() -> None:
    message = (
        "🌅 Калининград сегодня (01.08.2026)\n"
        "🏙 Калининград — 21/16 °C • 🌥 пасм\n"
        "💨 Ветер: 3 м/с, порывы до 6 м/с\n"
    )
    prompt, _ = build_kld_morning_prompt(message, post_type="morning")
    assert "beach, dunes, pines and sea filling the whole image" not in prompt
    assert "Baltic coast near Kaliningrad in daylight, dunes, pines, promenade, sea horizon" not in prompt
    assert "dunes, pines, promenade, or Baltic sea horizon" not in prompt
    assert SUMMER_VEGETATION_CUE in prompt
    assert PROVIDER_NEGATIVE_GUARD in prompt
    assert "Controlled scene:" in prompt
    assert "Controlled composition:" not in prompt


def curonian_spit_cannot_receive_breakwater_objects() -> None:
    metadata = {
        "scene_family": "curonian_spit_dunes",
        "composition": "breakwater perspective line",
        "variation_attempt": "0",
    }
    normalized = apply_scene_composition_policy(metadata)
    assert normalized["composition"] != "breakwater perspective line"
    assert scene_composition_compatible(
        normalized["scene_family"], normalized["composition"]
    )
    assert scene_composition_compatible(
        "baltiysk_breakwater", "breakwater perspective line"
    )


def stable_horde_prompt_is_short_kld_first_and_negative_separate() -> None:
    positive, negative = build_stable_horde_prompt_parts(
        {
            "target_date": "2026-08-09",
            "post_type": "morning",
            "scene_family": "curonian_spit_dunes",
            "scene_text": "Curonian Spit dunes with marram grass and open Baltic water",
            "composition": "foreground dune grass with open water behind",
            "weather_scenario": "cloudy",
            "visibility_condition": "clear",
            "wind_gust_category": "gust_7_9",
            "lunar_phase": "waning crescent",
        }
    )
    assert positive.startswith("Kaliningrad region, Baltic Sea coast, August summer.")
    assert "lush fresh natural green" in positive
    assert "Open Baltic water" in positive
    assert len(positive.split()) < 100
    assert "dry yellow living grass" not in positive
    assert "dry yellow living grass" in negative
    assert "breakwater" in negative
    assert "screenshot" in negative


TESTS = [
    kld_policy_august_is_green,
    kld_policy_autumn_does_not_force_summer_green,
    kld_policy_scene_contract_is_authoritative_not_beach_locked,
    kld_policy_macro_families_are_stable,
    kld_policy_recent_scene_is_hard_blocked,
    kld_policy_open_beach_is_limited_to_two_of_five,
    kld_policy_non_beach_scene_survives_beach_quota,
    kld_policy_august_morning_prompt_has_no_legacy_beach_lock,
    curonian_spit_cannot_receive_breakwater_objects,
    stable_horde_prompt_is_short_kld_first_and_negative_separate,
]


def main() -> None:
    for test in TESTS:
        test()
        print(f"PASS: {test.__name__}")
    print(f"OK: {len(TESTS)} KLD visual policy checks passed")


if __name__ == "__main__":
    main()
