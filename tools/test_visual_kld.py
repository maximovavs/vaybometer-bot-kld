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
            "cues.activity_visual": "small distant paddleboarder only",
            "cues.activity_scale": "distant",
        },
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
            "cues.activity_visual": "tiny distant paddleboard silhouette only",
            "cues.activity_scale": "tiny_hint",
            "cues.overall_mood": "soft cautious Baltic mood",
        },
        "prompt_must_contain": [
            "humid wet Baltic air",
            "subtle drizzle texture",
            "tiny distant paddleboard silhouette only",
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
    message = CASES[0]["message"]
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
        "Activity cue: small distant paddleboarder only; scale: distant.",
        "Text restrictions: no text, no captions, no labels, no logos, no numbers, no UI, no watermarks.",
    ]:
        _assert_contains(name, prompt, needle)
    for needle in [
        "legacy marine mood should be ignored",
        "legacy inland mood should be ignored",
        "visible rain streaks",
    ]:
        _assert_not_contains(name, prompt, needle)

    print(f"PASS {name}")


def main() -> None:
    for case in CASES:
        run_case(case)
    run_image_prompt_bridge_case()
    print(f"OK: {len(CASES) + 1} KLD synthetic visual checks passed")


if __name__ == "__main__":
    main()
