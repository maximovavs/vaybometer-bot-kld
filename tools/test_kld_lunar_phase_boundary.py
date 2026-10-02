#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Focused regression for the KLD Last Quarter civil-date boundary."""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

import pendulum
import swisseph as swe

os.environ.setdefault("GEN_SKIP_SHORT", "1")
os.environ.setdefault("TELEGRAM_TOKEN_KLG", "test-token")
os.environ.setdefault("CHANNEL_ID_KLG", "test-channel")

import gen_lunar_calendar as lunar  # noqa: E402
from format_v2 import _moon_line  # noqa: E402
from image_prompt_kld import build_kld_evening_prompt  # noqa: E402
from visual_context_kld import build_visual_context  # noqa: E402


def main() -> None:
    swe.set_ephe_path(".")
    oct2 = pendulum.date(2026, 10, 2)
    oct3 = pendulum.date(2026, 10, 3)

    name, illumination, _sign = lunar.compute_phase(swe.julday(2026, 10, 2, 0.0))
    assert name == "Убывающая Луна", name
    assert 67 <= illumination <= 69, illumination
    assert lunar._last_quarter_event_on_kld_date(oct2) is False
    assert lunar._last_quarter_event_on_kld_date(oct3) is True

    payload = json.loads(Path("lunar_calendar.json").read_text("utf-8"))
    days = payload.get("days", payload)
    rec2 = days["2026-10-02"]
    rec3 = days["2026-10-03"]
    assert rec2["phase_name"] == "Убывающая Луна"
    assert rec2["percent"] == 68
    assert rec3["phase_name"] == "Последняя четверть"

    formatted_moon = _moon_line("🌙 🌖 Убывающая Луна , ♊ (68%)")
    assert formatted_moon == "🌖 Убывающая Луна в ♊ — 68% освещённости."
    assert "Последняя четверть" not in formatted_moon

    message = "\n".join(
        [
            "<b>🌅 Калининградская область завтра (02.10.2026)</b>",
            "🌊 <b>Морские города</b>",
            "Балтийск: 14/10 °C • 🌥 облачно • 💨 4 м/с • порывы до 8 м/с • 🌊 14°C • волна 0.4 м",
            "Зеленоградск: 14/9 °C • 🌥 облачно • 🌊 14°C",
            formatted_moon,
        ]
    )
    ctx = build_visual_context(message, post_type="evening")
    assert ctx.moon_phase == "waning_gibbous", ctx.moon_phase

    prompt, _style = build_kld_evening_prompt(
        dt.date(2026, 10, 1),
        marine_mood="",
        inland_mood="",
        final_format_v2_message=message,
        post_type="evening",
    )
    assert "waning gibbous Moon" in prompt
    assert "68% illuminated" in prompt
    assert "last quarter half moon" not in prompt.lower()
    assert "last-quarter Moon" not in prompt

    quarter_message = message.replace(
        formatted_moon,
        "🌗 Последняя четверть в ♋ — 50% освещённости.",
    )
    quarter_ctx = build_visual_context(quarter_message, post_type="evening")
    assert quarter_ctx.moon_phase == "last_quarter"
    quarter_prompt, _style = build_kld_evening_prompt(
        dt.date(2026, 10, 2),
        marine_mood="",
        inland_mood="",
        final_format_v2_message=quarter_message,
        post_type="evening",
    )
    assert "last-quarter Moon" in quarter_prompt
    assert "50% illuminated" in quarter_prompt

    print("OK: KLD Last Quarter event-boundary regressions passed")


if __name__ == "__main__":
    main()
