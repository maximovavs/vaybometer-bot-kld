#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regression checks for KLD morning FORMAT_V2 utility blocks."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from format_v2 import build_evening_format_v2, build_morning_format_v2  # noqa: E402


LEGACY_FIXTURE = """<b>🌅 Калининградская область: погода на сегодня (19.06.2026)</b>
Погода: 🏙️ Калининград — 22/14 °C • облачно • 💨 4.0 м/с • 🔹 1014 гПа.
💱 Курсы (утро): USD 90.00 ₽ • EUR 98.00 ₽ • CNY 12.00 ₽
☀️ УФ: 4 — умеренный
🏭 Воздух: 🟢 низкий (AQI 22) • PM₂.₅ 5 / PM₁₀ 10
🌇 Закат сегодня: 21:32
📻 <b>Астрособытия</b>
🌙 🌒 Растущий серп (30%) • ♍ Дева
• День подходит для спокойного планирования и аккуратных решений.
🧪 Safecast: 0.12 мкЗв/ч — фон спокойный.
✅ Сегодня: прогулка в удобное окно.
#Калининград #погода #здоровье #сегодня #море
"""

EVENING_FIXTURE = """<b>🌅 Калининградская область: погода на завтра (20.06.2026)</b>
Погода: 🏙️ Калининград — 21/13 °C • облачно • 💨 4.0 м/с • 🔹 1014 гПа.
🌊 <b>Морские города</b>
Светлогорск: 18/13 °C • облачно • 🌊 6 м/с
———
🌡 <b>Тёплые города</b>
Черняховск: 23/12 °C • облачно
🌇 Закат завтра: 21:33
📻 <b>Астрособытия</b>
🌙 🌒 Растущий серп (34%) • ♎ Весы
✅ В целом: благоприятный день.
• Подходит для спокойных договорённостей и планирования.
———
#Калининград #погода #здоровье #море
"""


def _astro_lines(text: str) -> list[str]:
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("🌅 <b>Солнце и ритм"))
    return [line for line in lines[start:start + 5] if line.strip()]


def kld_morning_astro_block_has_sun_and_rhythm_title() -> None:
    text = build_morning_format_v2("Калининградская область", LEGACY_FIXTURE)
    assert "🌅 <b>Солнце и ритм дня</b>" in text
    assert "🌙 🌒 Растущий серп, ♍ Дева (30%)" in text
    assert "✅ Астроритм:" in text
    assert "💚 В плюсе:" in text


def kld_morning_astro_block_has_sunset_if_available() -> None:
    text = build_morning_format_v2("Калининградская область", LEGACY_FIXTURE)
    assert "🌇 Закат сегодня: 21:32" in _astro_lines(text)


def kld_evening_astro_block_has_tomorrow_wording() -> None:
    text = build_evening_format_v2("Калининградская область", EVENING_FIXTURE)
    assert "🌅 <b>Солнце и ритм завтрашнего дня</b>" in text
    assert "🌇 Закат завтра: 21:33" in text


def kld_astro_block_stays_compact() -> None:
    for text in (
        build_morning_format_v2("Калининградская область", LEGACY_FIXTURE),
        build_evening_format_v2("Калининградская область", EVENING_FIXTURE),
    ):
        assert len(_astro_lines(text)) <= 5


def kld_daily_keeps_weather_blocks() -> None:
    morning = build_morning_format_v2("Калининградская область", LEGACY_FIXTURE)
    evening = build_evening_format_v2("Калининградская область", EVENING_FIXTURE)
    assert "🏙️ Калининград" in morning
    assert "💱 Курсы" in morning
    assert "🏙 <b>Калининград</b>" in evening
    assert "🌊 <b>Морские города</b>" in evening


def kld_morning_has_sunset() -> None:
    text = build_morning_format_v2("Калининградская область", LEGACY_FIXTURE)
    assert "🌇 Закат сегодня: 21:32" in text
    assert text.index("🏭 Воздух:") < text.index("🌇 Закат сегодня:") < text.index("✅ План:")


def kld_morning_keeps_safecast() -> None:
    os.environ["FORMAT_V2_SENSOR_LINE"] = "1"
    from safe_test_post import _inject_sensor_line

    text = build_morning_format_v2("Калининградская область", LEGACY_FIXTURE)
    text = _inject_sensor_line(text, LEGACY_FIXTURE)
    assert "Safecast" in text
    assert text.count("Safecast") == 1


def kld_morning_keeps_fx_uv_air_plan() -> None:
    text = build_morning_format_v2("Калининградская область", LEGACY_FIXTURE)
    for marker in ("💱 Курсы", "☀️ УФ", "🏭 Воздух", "✅ План:"):
        assert marker in text


def main() -> None:
    checks = (
        kld_morning_has_sunset,
        kld_morning_keeps_safecast,
        kld_morning_keeps_fx_uv_air_plan,
        kld_morning_astro_block_has_sun_and_rhythm_title,
        kld_morning_astro_block_has_sunset_if_available,
        kld_evening_astro_block_has_tomorrow_wording,
        kld_astro_block_stays_compact,
        kld_daily_keeps_weather_blocks,
    )
    for check in checks:
        check()
        print(f"PASS {check.__name__}")
    print(f"OK: {len(checks)} KLD morning FORMAT_V2 regression checks passed")


if __name__ == "__main__":
    main()
