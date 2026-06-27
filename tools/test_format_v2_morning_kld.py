#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regression checks for KLD morning FORMAT_V2 utility blocks."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from format_v2 import build_evening_format_v2, build_morning_format_v2  # noqa: E402


LEGACY_FIXTURE = """<b>🌅 Калининградская область: погода на сегодня (19.06.2026)</b>
✨ VayboMeter сегодня: 7.4/10 — нормальный день с морской поправкой.
🧭 Главный сценарий: мягко, облачно, у воды свежее.
Погода: 🏙️ Калининград — 22/14 °C • облачно • 💨 4.0 м/с • 🔹 1014 гПа.
🌡 Ощущается: комфортно в городе, свежее у воды.
🕘 Лучшее окно: 10:00–13:00.
⚠️ Главный нюанс: у моря ветер ощущается сильнее.
💱 Курсы (утро): USD 90.00 ₽ (1.43) • EUR 98.00 ₽ (-0.22) • CNY 12.00 ₽ (0.00)
☀️ УФ: 4 — умеренный
🏭 Воздух: 🟢 низкий (AQI 22) • PM₂.₅ 5 / PM₁₀ 10
🌍 Сейсмика 24ч: M2.3, 5 км от Калининграда, глубина 8 км, 12:30.
🌇 Закат сегодня: 21:32
📻 <b>Астрособытия</b>
🌙 🌒 Растущий серп (30%) • ♍ Дева
• День подходит для спокойного планирования и аккуратных решений.
💚 В плюсе: порядок, здоровье, аккуратность.
🌙 В этот период лучше закрывать мелкие дела без рывков.
🧲 Космопогода: Кр 2.0 (спокойно), v 529 км/с, n 0.5 см⁻³.
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
    assert "✅ Астроритм:" not in text
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
    assert "🏙 Калининград" in morning
    assert "💱 Курсы" in morning
    assert "🏙 Калининград" in evening
    assert "🌊 <b>Морские города</b>" in evening


def kld_morning_has_sunset() -> None:
    text = build_morning_format_v2("Калининградская область", LEGACY_FIXTURE)
    assert "🌇 Закат сегодня: 21:32" in text
    assert text.index("🏭 Воздух:") < text.index("🌇 Закат сегодня:") < text.index("✅ План:")


def kld_morning_keeps_safecast() -> None:
    text = build_morning_format_v2("Калининградская область", LEGACY_FIXTURE)
    assert "🧪 Фон по частному датчику: спокойно." in text
    assert "мкЗв" not in text


def kld_morning_keeps_fx_uv_air_plan() -> None:
    text = build_morning_format_v2("Калининградская область", LEGACY_FIXTURE)
    for marker in ("💱 Курсы", "☀️ УФ", "🏭 Воздух", "✅ План:"):
        assert marker in text
    assert "USD 90.00 ₽ ↑1.43" in text
    assert "EUR 98.00 ₽ ↓0.22" in text
    assert "CNY 12.00 ₽ →0.00" in text


def kld_morning_fx_cleaner_is_idempotent() -> None:
    fixture = LEGACY_FIXTURE.replace(
        "💱 Курсы (утро): USD 90.00 ₽ (1.43) • EUR 98.00 ₽ (-0.22) • CNY 12.00 ₽ (0.00)",
        "💱 Курсы (утро): USD 90.00 ₽ ↑1.43 • EUR 98.00 ₽ ↓0.22 • CNY 12.00 ₽ →0.00",
    )
    text = build_morning_format_v2("Калининградская область", fixture)
    assert "USD 90.00 ₽ ↑1.43 • EUR 98.00 ₽ ↓0.22 • CNY 12.00 ₽ →0.00" in text
    assert "→0.00 ↓0.22" not in text
    assert "→0.00 ↑1.43" not in text


def kld_morning_safecast_above_observation_is_soft() -> None:
    fixture = LEGACY_FIXTURE.replace(
        "🧪 Safecast: 0.12 мкЗв/ч — фон спокойный.",
        "🧪 Safecast: 0.22 мкЗв/ч — выше обычного по датчику.",
    )
    text = build_morning_format_v2("Калининградская область", fixture)
    wanted = "🧪 Фон по частному датчику: выше обычной точки наблюдения; смотрим динамику, не разовое значение."
    assert wanted in text
    assert text.count("🧪") == 1


def kld_morning_has_only_one_plan() -> None:
    text = build_morning_format_v2("Калининградская область", LEGACY_FIXTURE)
    assert text.count("✅ План:") == 1


def kld_morning_astro_block_has_moon_and_plus() -> None:
    text = build_morning_format_v2("Калининградская область", LEGACY_FIXTURE)
    astro = "\n".join(_astro_lines(text))
    assert "🌙 🌒 Растущий серп, ♍ Дева (30%)" in astro
    assert "💚 В плюсе:" in astro
    assert "🌙 В этот период" in astro


def kld_morning_preserves_quake_line() -> None:
    text = build_morning_format_v2("Калининградская область", LEGACY_FIXTURE)
    assert "🌍 Сейсмика 24ч:" in text


def kld_morning_cosmoweather_is_compact() -> None:
    text = build_morning_format_v2("Калининградская область", LEGACY_FIXTURE)
    assert "🧲 Космопогода: спокойно, Kp 2.0." in text
    assert "v 529 км/с" not in text
    assert "n 0.5 см" not in text


def main() -> None:
    checks = (
        kld_morning_has_sunset,
        kld_morning_keeps_safecast,
        kld_morning_keeps_fx_uv_air_plan,
        kld_morning_fx_cleaner_is_idempotent,
        kld_morning_safecast_above_observation_is_soft,
        kld_morning_has_only_one_plan,
        kld_morning_astro_block_has_sun_and_rhythm_title,
        kld_morning_astro_block_has_moon_and_plus,
        kld_morning_preserves_quake_line,
        kld_morning_cosmoweather_is_compact,
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
