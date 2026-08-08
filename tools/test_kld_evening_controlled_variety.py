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

from image_prompt_kld import build_kld_evening_prompt  # noqa: E402


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

    print("PASS evening_controlled_variety")


if __name__ == "__main__":
    main()
