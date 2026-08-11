#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline regression for the current KLD evening controlled-variety contract.

This test is intentionally side-effect free: it does not fetch weather data,
call image providers, send Telegram messages, or require secrets.
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from image_prompt_kld import (  # noqa: E402
    KLD_SCENE_FAMILIES,
    _scene_catalog,
    build_kld_evening_prompt,
)


PINE_SCENE = "pine_forest_sea_path"
MESSAGE = "\n".join(
    [
        "🌊 Морские города",
        "Светлогорск: 20/15 °C • 🌥 пасм • 🌊 15 • 0.2 м",
        "Зеленоградск: 20/15 °C • 🌥 пасм • 🌊 15",
        "💨 Ветер: 3–5 м/с, порывы до 7 м/с",
        "🧜‍♂️ SUP: только для опытных и короткой сессии • гидрокостюм 4/3 мм (боты)",
        "🌙 Луна: растущий серп",
    ]
)


def _build(date_value: dt.date) -> tuple[str, str]:
    return build_kld_evening_prompt(
        date_value,
        marine_mood="",
        inland_mood="",
        final_format_v2_message=MESSAGE,
        post_type="evening",
    )


def _selected_scene(prompt: str) -> str:
    marker = "dominant Baltic scene family: "
    for line in prompt.splitlines():
        if marker in line:
            return line.split(marker, 1)[1].split(";", 1)[0].strip()
    raise AssertionError("controlled-variety prompt must expose the selected scene family")


def provider_scene_catalog_excludes_unverifiable_pine() -> None:
    for weather in ("rain", "drizzle", "cloudy", "clear"):
        if PINE_SCENE in _scene_catalog(weather):
            raise AssertionError(f"{PINE_SCENE} must not be provider-selectable for {weather}")

    if PINE_SCENE not in KLD_SCENE_FAMILIES:
        raise AssertionError("historical KLD scene catalog compatibility must retain the pine scene")

    review_prompt, _ = _build(dt.date(2026, 8, 8))
    if _selected_scene(review_prompt) == PINE_SCENE:
        raise AssertionError("cloudy 2026-08-08 evening must not select the pine scene")


def main() -> None:
    prompt_a1, style_a1 = _build(dt.date(2026, 6, 19))
    prompt_a2, style_a2 = _build(dt.date(2026, 6, 19))
    prompt_b, style_b = _build(dt.date(2026, 6, 20))

    if prompt_a1 != prompt_a2 or style_a1 != style_a2:
        raise AssertionError("same-date evening prompt/style must be deterministic")
    if prompt_a1 == prompt_b:
        raise AssertionError("different evening dates should select different controlled variety")

    required = (
        "cloudy Baltic weather",
        "Controlled composition:",
        "dominant Baltic scene family:",
        "composition:",
        "photorealistic Baltic coastal photography",
        "Text restrictions: No visible text anywhere",
    )
    for needle in required:
        if needle not in prompt_a1:
            raise AssertionError(f"evening prompt must contain {needle!r}")

    if not style_a1.startswith("format_v2_scene_cues_v6_"):
        raise AssertionError(f"unexpected evening style name: {style_a1!r}")
    if style_a1 == style_b:
        raise AssertionError("different evening dates should produce a different style/cache identity")

    provider_scene_catalog_excludes_unverifiable_pine()

    print("PASS evening_provider_catalog_excludes_pine")
    print("PASS evening_controlled_variety")


if __name__ == "__main__":
    main()
