#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regression checks for green KLD summer vegetation in morning prompts."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from image_prompt_kld_morning import (  # noqa: E402
    _SUMMER_VEGETATION_CUE,
    _apply_summer_vegetation_guard,
    build_kld_morning_prompt,
)


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
    assert _SUMMER_VEGETATION_CUE in prompt
    assert "coastal grass and dune vegetation are visibly natural green" in prompt
    assert "pale beige tones belong to sand, not to living vegetation" in prompt


def summer_guard_is_idempotent() -> None:
    once = _apply_summer_vegetation_guard("base", "2026-08-01")
    twice = _apply_summer_vegetation_guard(once, "2026-08-01")
    assert once == twice
    assert twice.count(_SUMMER_VEGETATION_CUE) == 1


def non_summer_date_is_unchanged() -> None:
    original = "base prompt"
    assert _apply_summer_vegetation_guard(original, "2026-11-01") == original


def malformed_date_is_unchanged() -> None:
    original = "base prompt"
    assert _apply_summer_vegetation_guard(original, "undated") == original


def main() -> None:
    checks = (
        august_production_like_prompt_requires_green_vegetation,
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
