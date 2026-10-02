#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline regression for KLD Last Quarter civil-day semantics."""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("GEN_SKIP_SHORT", "1")

import gen_lunar_calendar as lunar  # noqa: E402
from format_v2 import _moon_line  # noqa: E402
from image_prompt_kld import build_kld_evening_prompt  # noqa: E402
from visual_context_kld import build_visual_context  # noqa: E402


def _jd(day: dt.date) -> float:
    return lunar.swe.julday(day.year, day.month, day.day, 0.0)


def _calendar_days() -> dict:
    payload = json.loads((ROOT / "lunar_calendar.json").read_text("utf-8"))
    return payload.get("days", payload)


def main() -> None:
    oct2 = dt.date(2026, 10, 2)
    oct3 = dt.date(2026, 10, 3)
    oct4 = dt.date(2026, 10, 4)

    name2, illum2, _sign2 = lunar.compute_phase_for_local_date(_jd(oct2), oct2)
    assert name2 == "Убывающая Луна", name2
    assert illum2 == 68, illum2
    assert not lunar.last_quarter_occurs_on_local_date(oct2)

    name3, illum3, _sign3 = lunar.compute_phase_for_local_date(_jd(oct3), oct3)
    assert name3 == "Последняя четверть", name3
    assert illum3 == 56, illum3
    assert lunar.last_quarter_occurs_on_local_date(oct3)

    name4, illum4, _sign4 = lunar.compute_phase_for_local_date(_jd(oct4), oct4)
    assert name4 == "Убывающий серп", name4
    assert illum4 == 45, illum4
    assert not lunar.last_quarter_occurs_on_local_date(oct4)

    days = _calendar_days()
    assert days["2026-10-02"]["phase_name"] == "Убывающая Луна"
    assert days["2026-10-02"]["percent"] == 68
    assert days["2026-10-03"]["phase_name"] == "Последняя четверть"
    assert days["2026-10-03"]["percent"] == 56
    assert days["2026-10-04"]["phase_name"] == "Убывающий серп"

    raw_moon = "🌙 🌖 Убывающая Луна , ♊ (68%)"
    format_v2_moon = _moon_line(raw_moon)
    assert format_v2_moon == "🌖 Убывающая Луна в ♊ — 68% освещённости."
    assert "Последняя четверть" not in format_v2_moon

    message = "\n".join(
        [
            "02.10.2026",
            "🌊 Морские города",
            "Балтийск: 13/8 °C • 🌥 облачно • 💨 4 м/с • порывы до 8 м/с • 🌊 14°C • волна 0.5 м",
            "Зеленоградск: 13/8 °C • 🌥 облачно • 🌊 14°C",
            format_v2_moon,
        ]
    )
    ctx = build_visual_context(message, post_type="evening")
    assert ctx.moon_phase == "waning_gibbous", ctx.moon_phase

    prompt, _style_name = build_kld_evening_prompt(
        oct2,
        marine_mood="",
        inland_mood="",
        final_format_v2_message=message,
        post_type="evening",
    )
    assert "Lunar cue: one realistic waning gibbous Moon" in prompt
    assert "68% illuminated" in prompt
    assert "last quarter half moon" not in prompt.lower()
    assert "last-quarter Moon" not in prompt

    print("OK: KLD Last Quarter event-boundary regression passed")


if __name__ == "__main__":
    main()
