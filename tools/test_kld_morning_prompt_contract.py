#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Current scene-neutral morning prompt regressions for KLD.

This replaces the superseded morning block inside tools/test_visual_kld.py,
which still expected the pre-hardening beach+dunes+pines prompt contract.
The checks here assert current weather fidelity, scene authority and provider
safety without forcing a particular coastal geography.
"""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from image_prompt_kld_morning import build_kld_morning_prompt  # noqa: E402
from visual_context_kld import build_visual_context  # noqa: E402
from visual_rules import apply_visual_rules  # noqa: E402


CASES = (
    {
        "name": "morning_cloudy_scene_neutral",
        "message": "\n".join(
            [
                "<b>🌅 Калининград сегодня (04.08.2026)</b>",
                "🌊 Морские города",
                "Светлогорск: 17/12 °C • 🌥 пасм • 🌊 14 • 0.2 м",
                "Зеленоградск: 17/12 °C • 🌥 пасм • 🌊 14",
            ]
        ),
        "weather": "cloudy",
        "must_contain": (
            "cloudy Baltic weather",
            "Morning overcast scene:",
        ),
        "must_not_contain": (),
    },
    {
        "name": "morning_rain_scene_neutral_no_moon",
        "message": "\n".join(
            [
                "<b>🌅 Калининград сегодня (04.08.2026)</b>",
                "🌊 Морские города",
                "Светлогорск: 15/11 °C • 🌧 дождь • 🌊 14 • 0.4 м",
                "Зеленоградск: 15/11 °C • 🌧 дождь • 🌊 14",
                "🌙 Луна: полнолуние",
            ]
        ),
        "weather": "rain",
        "must_contain": (
            "rainy Baltic weather",
            "Morning rain adherence:",
            "wet or damp surfaces appropriate to the selected scene",
            "muted northern grey-blue palette",
            "realistic rain-dark cloud cover",
        ),
        "must_not_contain": (
            "bright full moon",
            "moon reflection on Baltic water",
            "clear daylight sky",
            "golden-hour",
            "bright sunny",
            "dry sand foreground",
        ),
    },
    {
        "name": "morning_rain_gusts_keep_weather_without_beach_lock",
        "message": "\n".join(
            [
                "<b>🌅 Калининград сегодня (04.08.2026)</b>",
                "✨ VayboMeter: 6.4/10 — нормальный, с поправками; дождь и порывы снижают комфорт.",
                "🏙 Калининград — 20/13 °C • 🌧 дождь • 💨 4.8 м/с • порывы до 10 м/с.",
                "⚠️ Главный нюанс: у воды порывы ощущаются сильнее, чем в городе.",
                "✅ План: дождевик и закрытая обувь; у моря выбирать защищённый маршрут.",
            ]
        ),
        "weather": "rain",
        "must_contain": (
            "rainy Baltic weather",
            "Morning rain adherence:",
            "subdued practical wet-weather mood",
        ),
        "must_not_contain": (
            "clear daylight sky",
            "golden-hour",
            "golden sunny",
            "bright sunny",
            "dry sand foreground",
        ),
    },
    {
        "name": "morning_clear_no_evening_mood",
        "message": "\n".join(
            [
                "<b>🌅 Калининград сегодня (04.08.2026)</b>",
                "🌊 Морские города",
                "Светлогорск: 21/13 °C • ☀️ ясно • 🌊 15 • 0.1 м",
                "Зеленоградск: 21/13 °C • ☀️ ясно • 🌊 15",
            ]
        ),
        "weather": "clear",
        "must_contain": ("clear Baltic weather",),
        "must_not_contain": ("Morning rain adherence:",),
    },
)

COMMON_MUST_CONTAIN = (
    "soft low-angle morning light",
    "practical weather-for-the-day mood",
    "Final image: one clean unmarked photorealistic Kaliningrad-region scene in natural Baltic daylight",
    "the selected scene family is authoritative and fills the frame",
    "Scene identity adherence: the selected scene family is authoritative",
    "Provider safety: no map, no satellite imagery, no cartographic view, no navigation screen",
)

LEGACY_BEACH_LOCKS = (
    "Final image: clean unmarked natural Baltic landscape only",
    "open sky, sea, dunes, pines, clouds and daylight",
    "dunes, pines, promenade, or Baltic sea horizon",
    "Base scene: Baltic coast near Kaliningrad in daylight, dunes, pines, promenade, sea horizon",
)

FORBIDDEN_POSITIVE_CUES = (
    "Moon cue:",
    "bright full moon",
    "northern night sky",
    "sunset light",
    "mystical evening atmosphere",
    "night atmosphere",
    "sunset colors",
)


def _contains(name: str, prompt: str, needle: str) -> None:
    if needle not in prompt:
        raise AssertionError(f"{name}: prompt must contain {needle!r}")


def _not_contains(name: str, prompt: str, needle: str) -> None:
    if needle in prompt:
        raise AssertionError(f"{name}: prompt must not contain {needle!r}")


def run_case(case) -> None:
    name = case["name"]
    ctx = build_visual_context(case["message"], post_type="morning")
    cues = apply_visual_rules(ctx)
    prompt, style_name = build_kld_morning_prompt(case["message"])

    assert ctx.post_type == "morning", (name, ctx.post_type)
    assert ctx.weather_main == case["weather"], (name, ctx.weather_main)
    assert cues.light_style == "soft low-angle morning light", (name, cues.light_style)
    assert style_name.startswith("format_v2_scene_cues_morning_"), (name, style_name)

    for needle in COMMON_MUST_CONTAIN + tuple(case["must_contain"]):
        _contains(name, prompt, needle)
    for needle in tuple(case.get("must_not_contain") or ()) + FORBIDDEN_POSITIVE_CUES + LEGACY_BEACH_LOCKS:
        _not_contains(name, prompt, needle)

    # Negative provider-safety wording is intentionally present. The contract
    # forbids those outputs at provider boundary; it is not a positive UI/text cue.
    assert "no screenshot" in prompt
    assert "no UI chrome" in prompt
    assert "no text overlay" in prompt

    print(f"PASS: {name}")


def main() -> None:
    for case in CASES:
        run_case(case)
    print(f"OK: {len(CASES)} current KLD morning prompt regressions passed")


if __name__ == "__main__":
    main()
