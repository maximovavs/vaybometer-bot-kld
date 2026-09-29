#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regression checks for green KLD summer vegetation in morning prompts."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from image_prompt_kld_morning import build_kld_morning_prompt  # noqa: E402
from kld_visual_policy import (  # noqa: E402
    SUMMER_ANTI_WINTER_CUE,
    SUMMER_ANTI_WINTER_TERMS,
    SUMMER_VEGETATION_CUE,
    apply_summer_vegetation_guard,
    build_stable_horde_prompt_parts,
    finalize_kld_provider_prompt,
)


def _provider_metadata(target_date: str) -> dict[str, str]:
    return {
        "forecast_date": target_date,
        "target_date": target_date,
        "post_type": "evening",
        "scene_family": "zelenogradsk_promenade",
        "scene_text": "Zelenogradsk seaside promenade with Baltic horizon and realistic coastal railings",
        "composition": "promenade railing foreground",
        "weather_scenario": "cloudy",
        "visibility_condition": "clear",
        "wind_gust_category": "gust_10_14",
        "lunar_phase": "waxing_crescent",
        "variation_attempt": "12",
    }


def august_production_like_prompt_requires_green_vegetation() -> None:
    message = "\n".join(
        [
            "🌅 Калининград сегодня (01.08.2026)",
            "🌊 Морские города",
            "Светлогорск: 20/16 °C • 🌥 пасм • 🌊 18",
            "Зеленоградск: 21/16 °C • 🌥 пасм • 🌊 18",
            "💨 Ветер: 2.5 м/с, порывы до 6 м/с",
            "🌫 Видимость: хорошая",
        ]
    )
    prompt, style_name = build_kld_morning_prompt(message)
    assert style_name.startswith("format_v2_scene_cues_morning_")
    assert SUMMER_VEGETATION_CUE in prompt
    assert "coastal grass and dune vegetation are visibly natural green" in prompt
    assert "pale beige and warm straw tones belong to sand" in prompt
    assert "not to living summer vegetation" in prompt


def august_finalized_provider_prompt_blocks_winter_semantics() -> None:
    base_prompt = "\n".join(
        [
            "Palette: cool Baltic grey-blue, muted sand, pine green, restrained northern light.",
            "Must show: recognizable Baltic/Kaliningrad atmosphere; Baltic sea horizon.",
            "Text restrictions: no text, no labels.",
        ]
    )
    prompt = finalize_kld_provider_prompt(
        base_prompt,
        metadata=_provider_metadata("2026-08-17"),
        date_key="2026-08-17",
    )
    assert SUMMER_VEGETATION_CUE in prompt
    assert SUMMER_ANTI_WINTER_CUE in prompt
    assert "fresh natural summer greens" in prompt
    for term in SUMMER_ANTI_WINTER_TERMS:
        assert f"no {term}" in prompt, term


def august_stable_horde_negative_blocks_winter_semantics() -> None:
    positive, negative = build_stable_horde_prompt_parts(
        _provider_metadata("2026-08-17")
    )
    assert "August summer" in positive
    assert "lush fresh natural green" in positive
    for term in SUMMER_ANTI_WINTER_TERMS:
        assert term in negative, (term, negative)


def non_summer_provider_contract_does_not_force_anti_winter() -> None:
    base_prompt = "\n".join(
        [
            "Palette: cool Baltic grey-blue, muted sand, pine green, restrained northern light.",
            "Text restrictions: no text, no labels.",
        ]
    )
    prompt = finalize_kld_provider_prompt(
        base_prompt,
        metadata=_provider_metadata("2026-11-17"),
        date_key="2026-11-17",
    )
    _positive, negative = build_stable_horde_prompt_parts(
        _provider_metadata("2026-11-17")
    )
    assert SUMMER_VEGETATION_CUE not in prompt
    assert SUMMER_ANTI_WINTER_CUE not in prompt
    for term in SUMMER_ANTI_WINTER_TERMS:
        assert term not in negative, (term, negative)


def summer_guard_is_idempotent() -> None:
    once = apply_summer_vegetation_guard("base", "2026-08-01")
    twice = apply_summer_vegetation_guard(once, "2026-08-01")
    assert once == twice
    assert twice.count(SUMMER_VEGETATION_CUE) == 1


def non_summer_date_is_unchanged() -> None:
    original = "base prompt"
    assert apply_summer_vegetation_guard(original, "2026-11-01") == original


def malformed_date_is_unchanged() -> None:
    original = "base prompt"
    assert apply_summer_vegetation_guard(original, "undated") == original


def main() -> None:
    checks = (
        august_production_like_prompt_requires_green_vegetation,
        august_finalized_provider_prompt_blocks_winter_semantics,
        august_stable_horde_negative_blocks_winter_semantics,
        non_summer_provider_contract_does_not_force_anti_winter,
        summer_guard_is_idempotent,
        non_summer_date_is_unchanged,
        malformed_date_is_unchanged,
    )
    for check in checks:
        check()
        print(f"PASS: {check.__name__}")
    print(f"OK: {len(checks)} KLD summer vegetation prompt checks passed")


if __name__ == "__main__":
    main()
