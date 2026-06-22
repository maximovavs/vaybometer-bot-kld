#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Synthetic visual checks for KLD FORMAT_V2 image prompt pipeline.

This script is intentionally side-effect free:
- does not fetch weather/marine data;
- does not call LLM;
- does not send Telegram messages;
- does not generate images.

Run locally or in GitHub Actions:
    python tools/test_visual_kld.py
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from image_prompt_kld import build_kld_evening_prompt  # noqa: E402
from image_prompt_kld_morning import build_kld_morning_prompt  # noqa: E402
from visual_context_kld import build_visual_context  # noqa: E402
from visual_rules import apply_visual_rules, build_prompt_from_cues  # noqa: E402


BASE_SPORT_LINE = "🧜‍♂️ SUP: только для опытных и короткой сессии • гидрокостюм 4/3 мм (боты)"
BASE_WIND_LINE = "💨 Ветер: 3–5 м/с, порывы до 7 м/с"
BASE_MOON_LINE = "🌙 Луна: растущий серп"


CASES: list[dict[str, Any]] = [
    {
        "name": "coastal_cloudy_ignores_inland_rain",
        "message": "\n".join(
            [
                "🌊 Морские города",
                "Светлогорск: 30/16 °C • 🌥 пасм • 🌊 15 • 0.2 м",
                "Зеленоградск: 29/13 °C • 🌥 пасм • 🌊 16",
                "Балтийск: 29/15 °C • 🌥 пасм • 🌊 18 • 0.1 м",
                "Янтарный: 23/16 °C • 🌊 15 • 0.3 м",
                "Пионерский: 22/16 °C • 🌦 морось • 🌊 15",
                "Мамоново: 29/16 °C • 🌧 дождь",
                BASE_WIND_LINE,
                BASE_SPORT_LINE,
                BASE_MOON_LINE,
            ]
        ),
        "expected": {
            "ctx.weather_main": "cloudy",
            "ctx.evidence.weather_source_used": "coastal",
            "cues.activity_visual": "small distant standing paddleboarder with paddle only, no sail",
            "cues.activity_scale": "distant",
            "cues.moon_visual": "small thin waxing crescent moon, subtle not dominant",
        },
        "prompt_must_contain": [
            "small thin waxing crescent moon, not a full circular moon",
            "crescent is subtle and secondary behind or between clouds",
            "small distant standing paddleboarder with paddle",
            "flat paddleboard silhouette, not a boat",
            "sailboat",
            "visible sail or mast",
            "full moon",
            "round moon",
            "moon reflection path on water",
        ],
        "prompt_must_not_contain": [
            "visible rain streaks",
            "wet surfaces and darker rainy clouds",
        ],
    },
    {
        "name": "coastal_drizzle_sup_tiny_hint",
        "message": "\n".join(
            [
                "🌊 Морские города",
                "Светлогорск: 20/16 °C • 🌦 морось • 🌊 15 • 0.2 м",
                "Зеленоградск: 20/15 °C • 🌦 морось • 🌊 15",
                "Балтийск: 20/15 °C • 🌦 морось • 🌊 15 • 0.3 м",
                "Янтарный: 20/16 °C • 🌥 пасм • 🌊 15 • 0.2 м",
                "Пионерский: 20/16 °C • 🌦 морось • 🌊 15",
                BASE_WIND_LINE,
                BASE_SPORT_LINE,
                BASE_MOON_LINE,
            ]
        ),
        "expected": {
            "ctx.weather_main": "drizzle",
            "ctx.evidence.weather_source_used": "coastal",
            "cues.activity_visual": "tiny distant standing paddleboard silhouette only, no sail",
            "cues.activity_scale": "tiny_hint",
            "cues.overall_mood": "soft cautious Baltic mood",
        },
        "prompt_must_contain": [
            "humid wet Baltic air",
            "subtle drizzle texture",
            "tiny distant standing paddleboard silhouette only, no sail",
            "standing human silhouette with a paddle on a flat board, no sail",
        ],
    },
    {
        "name": "coastal_rain_removes_sup",
        "message": "\n".join(
            [
                "🌊 Морские города",
                "Светлогорск: 18/15 °C • 🌧 дождь • 🌊 15 • 0.4 м",
                "Зеленоградск: 18/14 °C • 🌧 дождь • 🌊 15",
                "Балтийск: 17/14 °C • 🌧 дождь • 🌊 15 • 0.5 м",
                "Янтарный: 18/15 °C • 🌥 пасм • 🌊 15 • 0.4 м",
                "Пионерский: 18/15 °C • 🌧 дождь • 🌊 15",
                BASE_WIND_LINE,
                BASE_SPORT_LINE,
                BASE_MOON_LINE,
            ]
        ),
        "expected": {
            "ctx.weather_main": "rain",
            "ctx.evidence.weather_source_used": "coastal",
            "cues.activity_visual": "no visible paddleboarder",
            "cues.activity_scale": "none",
            "cues.overall_mood": "mixed, cautious comfort",
        },
        "prompt_must_contain": [
            "visible rain streaks",
            "wet surfaces",
            "steady visible rain streaks across the frame",
            "dense diagonal raindrop streaks",
            "rain clearly readable in foreground and midground",
            "wet grey Baltic atmosphere",
            "dark low rain clouds",
            "wet shoreline or wet promenade foreground",
            "overcast sky with no bright sun breaks",
            "empty coast, no leisure mood",
            "peaceful postcard beach mood",
            "bright clearing in the sky",
            "beautiful bright beach mood",
            "large sunlit opening in the clouds",
            "dry sand foreground",
            "visible SUP rider in rain",
            "relaxed SUP holiday mood",
        ],
    },
    {
        "name": "coastal_storm_removes_sup",
        "message": "\n".join(
            [
                "🌊 Морские города",
                "Светлогорск: 17/14 °C • ⛈ гроза • 🌊 15 • 1.0 м",
                "Зеленоградск: 17/14 °C • 🌥 пасм • 🌊 15",
                "Балтийск: 17/14 °C • 🌥 пасм • 🌊 15 • 0.9 м",
                BASE_WIND_LINE,
                BASE_SPORT_LINE,
                BASE_MOON_LINE,
            ]
        ),
        "expected": {
            "ctx.weather_main": "storm",
            "ctx.evidence.weather_source_used": "coastal",
            "cues.activity_visual": "no visible paddleboarder",
            "cues.activity_scale": "none",
            "cues.overall_mood": "visibly cautious day",
        },
        "prompt_must_contain": [
            "dramatic clouds",
            "rough sea",
            "storm-warning Baltic atmosphere",
            "rougher choppy sea with white foam",
            "strong wind visible in dune grass and pines",
            "dark dramatic cloud shelf",
            "sea spray or wind-driven rain",
            "unsafe coastal conditions feeling",
            "peaceful beach mood",
            "calm sea surface",
            "sunny breaks that make the scene feel pleasant",
            "choppy water with irregular wave texture",
            "white foam near shoreline",
            "wind force visible on the sea surface",
            "visible SUP rider in rain",
            "relaxed SUP holiday mood",
        ],
    },
    {
        "name": "new_moon_avoids_visible_moon",
        "message": "\n".join(
            [
                "🌊 Морские города",
                "Светлогорск: 20/15 °C • 🌥 пасм • 🌊 15 • 0.2 м",
                "Зеленоградск: 20/15 °C • 🌥 пасм • 🌊 15",
                BASE_WIND_LINE,
                "🌙 Луна: новолуние",
            ]
        ),
        "expected": {
            "ctx.moon_phase": "new",
            "cues.moon_visual": "no visible moon / moonless sky",
        },
        "prompt_must_contain": [
            "moonless dark sky if evening or night",
            "visible moon",
            "crescent moon",
            "full moon",
            "moon reflection on water",
        ],
    },
    {
        "name": "full_moon_evening_reflection",
        "message": "\n".join(
            [
                "🌊 Морские города",
                "Светлогорск: 20/15 °C • 🌥 пасм • 🌊 15 • 0.2 м",
                "Зеленоградск: 20/15 °C • 🌥 пасм • 🌊 15",
                BASE_WIND_LINE,
                "🌙 Луна: полнолуние",
            ]
        ),
        "expected": {
            "ctx.moon_phase": "full",
            "cues.moon_visual": "bright full moon",
        },
        "prompt_must_contain": [
            "bright full moon",
            "moon reflection on Baltic water if water is visible",
        ],
    },
]


def _get_path(obj: Any, path: str) -> Any:
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur[part]
        else:
            cur = getattr(cur, part)
    return cur


def _assert_equal(case_name: str, label: str, actual: Any, expected: Any) -> None:
    if actual != expected:
        raise AssertionError(f"{case_name}: {label} expected {expected!r}, got {actual!r}")


def _assert_contains(case_name: str, prompt: str, needle: str) -> None:
    if needle not in prompt:
        raise AssertionError(f"{case_name}: prompt must contain {needle!r}")


def _assert_not_contains(case_name: str, prompt: str, needle: str) -> None:
    if needle in prompt:
        raise AssertionError(f"{case_name}: prompt must not contain {needle!r}")


def run_case(case: dict[str, Any]) -> None:
    name = case["name"]
    ctx = build_visual_context(case["message"], post_type="evening")
    cues = apply_visual_rules(ctx)
    prompt = build_prompt_from_cues(cues)

    env = {"ctx": ctx, "cues": cues}
    for path, expected in case.get("expected", {}).items():
        root_name, rest = path.split(".", 1)
        actual = _get_path(env[root_name], rest)
        _assert_equal(name, path, actual, expected)

    evidence = ctx.evidence or {}
    temp_pairs = evidence.get("temp_pairs", [])
    for pair in temp_pairs:
        if not (
            isinstance(pair, (list, tuple))
            and len(pair) == 2
            and isinstance(pair[0], (int, float))
            and isinstance(pair[1], (int, float))
        ):
            raise AssertionError(f"{name}: temp_pairs must contain numeric pairs only, got {pair!r}")

    ignored = "\n".join(evidence.get("ignored_temp_like_lines", []))
    if "гидрокостюм" in case["message"] and "гидрокостюм" not in ignored:
        raise AssertionError(f"{name}: wetsuit temperature-like lines must be ignored")

    for needle in case.get("prompt_must_contain", []):
        _assert_contains(name, prompt, needle)
    for needle in case.get("prompt_must_not_contain", []):
        _assert_not_contains(name, prompt, needle)

    print(f"PASS {name}")


def run_image_prompt_bridge_case() -> None:
    name = "image_prompt_kld_format_v2_bridge"
    message = "\n".join(
        [
            "🌊 Морские города",
            "Светлогорск: 20/15 °C • 🌥 пасм • 🌊 15 • 0.2 м",
            "Зеленоградск: 20/15 °C • 🌥 пасм • 🌊 15",
            BASE_WIND_LINE,
            BASE_SPORT_LINE,
            BASE_MOON_LINE,
        ]
    )
    prompt, style_name = build_kld_evening_prompt(
        dt.date(2026, 6, 19),
        marine_mood="legacy marine mood should be ignored when FORMAT_V2 message is provided",
        inland_mood="legacy inland mood should be ignored when FORMAT_V2 message is provided",
        final_format_v2_message=message,
        post_type="evening",
    )

    _assert_equal(name, "style_name", style_name, "format_v2_scene_cues")
    for needle in [
        "Create an atmospheric, information-driven weather illustration for VayboMeter Kaliningrad.",
        "Weather: cloudy Baltic weather.",
        "Sky cue: cloud-dominant evening sky; celestial details hidden by clouds.",
        "Activity cue: unoccupied shoreline and open Baltic water; scale: none.",
        "layered cloud cover as the main sky feature",
        "unoccupied shoreline",
        "open Baltic water with only natural wave texture",
        "Text restrictions: no text, no captions, no labels, no logos, no numbers, no UI, no watermarks.",
    ]:
        _assert_contains(name, prompt, needle)
    for needle in [
        "legacy marine mood should be ignored",
        "legacy inland mood should be ignored",
        "visible rain streaks",
        "full moon",
        "round moon",
        "moon reflection path",
        "sailboat",
        "boat",
        "yacht",
        "sail",
        "mast",
        "paddleboard",
        "SUP",
    ]:
        _assert_not_contains(name, prompt, needle)

    print(f"PASS {name}")


def run_morning_cases() -> None:
    cases = [
        {
            "name": "morning_cloudy_daylight",
            "message": "\n".join(
                [
                    "🌊 Морские города",
                    "Светлогорск: 17/12 °C • 🌥 пасм • 🌊 14 • 0.2 м",
                    "Зеленоградск: 17/12 °C • 🌥 пасм • 🌊 14",
                ]
            ),
            "weather": "cloudy",
            "must_contain": ["cloudy Baltic weather"],
        },
        {
            "name": "morning_rain_no_moon",
            "message": "\n".join(
                [
                    "🌊 Морские города",
                    "Светлогорск: 15/11 °C • 🌧 дождь • 🌊 14 • 0.4 м",
                    "Зеленоградск: 15/11 °C • 🌧 дождь • 🌊 14",
                    "🌙 Луна: полнолуние",
                ]
            ),
            "weather": "rain",
            "must_contain": [
                "rainy Baltic weather",
                "steady visible rain streaks across the frame",
            ],
        },
        {
            "name": "morning_clear_no_evening_mood",
            "message": "\n".join(
                [
                    "🌊 Морские города",
                    "Светлогорск: 21/13 °C • ☀️ ясно • 🌊 15 • 0.1 м",
                    "Зеленоградск: 21/13 °C • ☀️ ясно • 🌊 15",
                ]
            ),
            "weather": "clear",
            "must_contain": ["clear Baltic weather"],
        },
    ]

    common_must_contain = [
        "soft low-angle morning light",
        "practical weather-for-the-day mood",
        "clear daylight sky",
        "fresh Baltic morning light",
        "Text restrictions: no text, no captions, no labels, no logos, no numbers, no UI, no watermarks.",
    ]
    forbidden_positive_cues = [
        "Moon cue:",
        "bright full moon",
        "moon reflection on Baltic water",
        "northern night sky",
        "sunset light",
        "mystical evening atmosphere",
        "night atmosphere",
        "sunset colors",
    ]

    for case in cases:
        ctx = build_visual_context(case["message"], post_type="morning")
        cues = apply_visual_rules(ctx)
        prompt, style_name = build_kld_morning_prompt(case["message"])

        _assert_equal(case["name"], "ctx.post_type", ctx.post_type, "morning")
        _assert_equal(case["name"], "ctx.weather_main", ctx.weather_main, case["weather"])
        _assert_equal(case["name"], "cues.light_style", cues.light_style, "soft low-angle morning light")
        _assert_equal(case["name"], "style_name", style_name, "format_v2_scene_cues_morning")

        for needle in common_must_contain + case["must_contain"]:
            _assert_contains(case["name"], prompt, needle)
        for needle in forbidden_positive_cues:
            _assert_not_contains(case["name"], prompt, needle)

        print(f"PASS {case['name']}")


def run_controlled_variety_cases() -> None:
    name = "controlled_visual_variety"
    message = CASES[0]["message"]

    prompt_a1, _ = build_kld_evening_prompt(
        dt.date(2026, 6, 19),
        marine_mood="",
        inland_mood="",
        final_format_v2_message=message,
        post_type="evening",
    )
    prompt_a2, _ = build_kld_evening_prompt(
        dt.date(2026, 6, 19),
        marine_mood="",
        inland_mood="",
        final_format_v2_message=message,
        post_type="evening",
    )
    prompt_b, _ = build_kld_evening_prompt(
        dt.date(2026, 6, 20),
        marine_mood="",
        inland_mood="",
        final_format_v2_message=message,
        post_type="evening",
    )
    _assert_equal(name, "same-date prompt stability", prompt_a1, prompt_a2)
    if prompt_a1 == prompt_b:
        raise AssertionError(f"{name}: different dates should select a different composition")
    _assert_contains(name, prompt_a1, "cloudy Baltic weather")
    _assert_contains(name, prompt_a1, "Controlled composition:")
    _assert_contains(
        name,
        prompt_a1,
        "Text restrictions: no text, no captions, no labels, no logos, no numbers, no UI, no watermarks.",
    )

    morning_message_a = "19.06.2026\n" + message
    morning_message_b = "20.06.2026\n" + message
    morning_a1, _ = build_kld_morning_prompt(morning_message_a)
    morning_a2, _ = build_kld_morning_prompt(morning_message_a)
    morning_b, _ = build_kld_morning_prompt(morning_message_b)
    _assert_equal(name, "same-date morning stability", morning_a1, morning_a2)
    if morning_a1 == morning_b:
        raise AssertionError(f"{name}: different morning dates should select a different composition")
    _assert_contains(name, morning_a1, "Controlled composition:")
    _assert_contains(name, morning_a1, "cloudy Baltic weather")
    for forbidden in ("night", "sunset", "evening", "moon", "lunar", "crescent"):
        _assert_not_contains(name, morning_a1.lower(), forbidden)

    print(f"PASS {name}")


def main() -> None:
    for case in CASES:
        run_case(case)
    run_image_prompt_bridge_case()
    run_morning_cases()
    run_controlled_variety_cases()
    print(f"OK: {len(CASES) + 5} KLD synthetic visual checks passed")


if __name__ == "__main__":
    main()
