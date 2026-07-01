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
import re
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


def _assert_no_trigger_word(case_name: str, prompt: str, trigger: str) -> None:
    if re.search(rf"\b{re.escape(trigger)}\b", prompt, flags=re.IGNORECASE):
        raise AssertionError(f"{case_name}: prompt must not contain trigger word {trigger!r}")


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
        "Text restrictions: No visible text anywhere, no tiny white text at the bottom, no pseudo-caption, no text, no captions, no labels, no UI, no logos, no watermarks, no watermark, no logo, no artist signature, no signature, no letters, no artist mark, no brand marks, absolutely no letters or numbers anywhere.",
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
    for needle in [
        "no watermark",
        "no signature",
        "no text",
        "no letters",
        "no artist mark",
        "no logo",
    ]:
        _assert_contains(name, prompt, needle)

    print(f"PASS {name}")


def run_first_quarter_moon_guard_case() -> None:
    name = "first_quarter_61_percent_moon_guard"
    message = "\n".join(
        [
            "22.06.2026",
            "🌊 Морские города",
            "Светлогорск: 20/15 °C • ☀️ ясно • 🌊 15 • 0.2 м",
            "Зеленоградск: 20/15 °C • ☀️ ясно • 🌊 15",
            "🌙 Первая четверть, 61%",
        ]
    )
    prompt, style_name = build_kld_evening_prompt(
        dt.date(2026, 6, 22),
        marine_mood="",
        inland_mood="",
        final_format_v2_message=message,
        post_type="evening",
    )

    _assert_equal(name, "style_name", style_name, "format_v2_scene_cues")
    for needle in [
        "a small non-dominant waxing half-to-gibbous Moon with the right side illuminated, not a full moon",
        "small",
        "non-dominant",
        "not a full moon",
        "no full moon unless the actual phase is full moon",
        "no oversized moon",
        "no dominant focal moon",
        "no large bright round moon",
        "no oversized round moon for quarter or crescent phases",
    ]:
        _assert_contains(name, prompt, needle)
    positive_lines = "\n".join(
        line
        for line in prompt.splitlines()
        if not line.strip().startswith(("Must avoid:", "Evening visual avoid:"))
    ).lower()
    for forbidden in (
        "large full moon",
        "round full moon",
        "large bright round moon",
        "oversized moon",
        "dominant focal moon",
    ):
        _assert_not_contains(name, positive_lines, forbidden)

    print(f"PASS {name}")


def run_not_quite_full_moon_guard_case() -> None:
    name = "full_phase_95_percent_renders_gibbous"
    message = "\n".join(
        [
            "22.06.2026",
            "🌊 Морские города",
            "Светлогорск: 20/15 °C • ☀️ ясно • 🌊 15 • 0.2 м",
            "Зеленоградск: 20/15 °C • ☀️ ясно • 🌊 15",
            "🌙 Луна: полнолуние, 95%",
        ]
    )
    prompt, style_name = build_kld_evening_prompt(
        dt.date(2026, 6, 22),
        marine_mood="",
        inland_mood="",
        final_format_v2_message=message,
        post_type="evening",
    )

    _assert_equal(name, "style_name", style_name, "format_v2_scene_cues")
    for needle in [
        "a realistic gibbous Moon, 90-96 percent illuminated, visibly not a perfect full moon",
        "no perfect full moon when illumination is below 97 percent",
        "no oversized full circular moon",
        "no fantasy supermoon",
    ]:
        _assert_contains(name, prompt, needle)
    positive_lines = "\n".join(
        line
        for line in prompt.splitlines()
        if not line.strip().startswith(("Must avoid:", "Evening visual avoid:"))
    ).lower()
    for forbidden in ("bright full moon", "fantasy supermoon", "oversized full circular moon"):
        _assert_not_contains(name, positive_lines, forbidden)

    print(f"PASS {name}")


def run_full_moon_evening_moonlit_guard_case() -> None:
    name = "full_moon_evening_blue_hour_moonlit_guard"
    message = "\n".join(
        [
            "22.06.2026",
            "🌊 Морские города",
            "Светлогорск: 20/15 °C • 🌥 пасм • 🌊 15 • 0.2 м",
            "Зеленоградск: 20/15 °C • 🌥 пасм • 🌊 15",
            "🌕 Полнолуние в ♑ — 100% освещённости.",
        ]
    )
    prompt, style_name = build_kld_evening_prompt(
        dt.date(2026, 6, 22),
        marine_mood="",
        inland_mood="",
        final_format_v2_message=message,
        post_type="evening",
    )

    _assert_equal(name, "style_name", style_name, "format_v2_scene_cues")
    for needle in [
        "Evening moonlit cue: blue-hour Baltic coast",
        "soft evening twilight",
        "visible realistic full moon",
        "cool moonlit sea",
        "residual pale horizon glow",
        "right side of frame",
        "realistic moon scale and natural moon position",
        "Evening visual avoid: no bright daytime look",
        "no morning look",
        "no sun-dominant scene",
        "no bright golden sunset",
        "no oversized moon",
        "no fantasy planet",
        "no fantasy supermoon",
        "No visible text anywhere",
        "no tiny white text at the bottom",
        "no pseudo-caption",
        "no watermark",
        "no artist signature",
        "no logo",
        "no brand marks",
        "Evening direction cue: right-side horizon glow",
    ]:
        _assert_contains(name, prompt, needle)

    print(f"PASS {name}")


def run_waning_gibbous_moon_guard_case() -> None:
    name = "waning_gibbous_94_percent_guard"
    message = "\n".join(
        [
            "22.06.2026",
            "🌊 Морские города",
            "Светлогорск: 20/15 °C • ☀️ ясно • 🌊 15 • 0.2 м",
            "Зеленоградск: 20/15 °C • ☀️ ясно • 🌊 15",
            "🌙 Убывающая Луна, 94%",
        ]
    )
    prompt, style_name = build_kld_evening_prompt(
        dt.date(2026, 6, 22),
        marine_mood="",
        inland_mood="",
        final_format_v2_message=message,
        post_type="evening",
    )

    _assert_equal(name, "style_name", style_name, "format_v2_scene_cues")
    _assert_contains(
        name,
        prompt,
        "a realistic waning gibbous Moon, 90-96 percent illuminated, visibly not a perfect full moon",
    )
    _assert_contains(name, prompt, "no perfect full moon when illumination is below 97 percent")
    _assert_not_contains(
        name,
        "\n".join(line for line in prompt.splitlines() if not line.strip().startswith("Must avoid:")),
        "a realistic waxing gibbous Moon, 90-96 percent illuminated",
    )

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
        "left-side morning light",
        "sun from the left side of frame",
        "Final image: clean unmarked natural Baltic landscape only",
        "open sky, sea, dunes, pines, clouds and daylight",
        "pure scenic painting without graphic overlay elements",
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
        for forbidden in (
            "text",
            "caption",
            "label",
            "logo",
            "watermark",
            "number",
            "numbers",
            "ui",
            "letter",
            "word",
            "writing",
            "title",
            "headline",
            "typography",
            "moon",
            "lunar",
            "night",
            "evening",
            "sunset",
        ):
            _assert_no_trigger_word(case["name"], prompt, forbidden)

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
        "Text restrictions: No visible text anywhere, no tiny white text at the bottom, no pseudo-caption, no text, no captions, no labels, no UI, no logos, no watermarks, no watermark, no logo, no artist signature, no signature, no letters, no artist mark, no brand marks, absolutely no letters or numbers anywhere.",
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
    _assert_contains(name, morning_a1, "Final image: clean unmarked natural Baltic landscape only")
    for forbidden in (
        "text",
        "caption",
        "label",
        "logo",
        "watermark",
        "number",
        "numbers",
        "ui",
        "letter",
        "word",
        "writing",
        "title",
        "headline",
        "typography",
        "night",
        "sunset",
        "evening",
        "moon",
        "lunar",
        "crescent",
    ):
        _assert_no_trigger_word(name, morning_a1, forbidden)

    print(f"PASS {name}")


def main() -> None:
    for case in CASES:
        run_case(case)
    run_image_prompt_bridge_case()
    run_first_quarter_moon_guard_case()
    run_not_quite_full_moon_guard_case()
    run_full_moon_evening_moonlit_guard_case()
    run_waning_gibbous_moon_guard_case()
    run_morning_cases()
    run_controlled_variety_cases()
    print(f"OK: {len(CASES) + 9} KLD synthetic visual checks passed")


if __name__ == "__main__":
    main()
