#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regression checks for KLD morning FORMAT_V2 utility blocks."""
from __future__ import annotations

import os
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from format_v2 import build_evening_format_v2, build_morning_format_v2  # noqa: E402

pendulum_stub = types.ModuleType("pendulum")
pendulum_stub.DateTime = object
sys.modules.setdefault("pendulum", pendulum_stub)

telegram_stub = types.ModuleType("telegram")
telegram_stub.Bot = object
telegram_stub.constants = types.SimpleNamespace(ParseMode=types.SimpleNamespace(HTML="HTML"))
sys.modules.setdefault("telegram", telegram_stub)

from post_safety import sanitize_post_text  # noqa: E402
from safe_test_post import (  # noqa: E402
    _inject_morning_best_window,
    _inject_morning_score,
    _inject_morning_smart_plan,
    _inject_sensor_line,
)


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

HOT_MORNING_FIXTURE = """<b>🌅 Калининградская область: погода на сегодня (28.06.2026)</b>
✨ VayboMeter: 7.2/10 — хорошо для прогулок.
Погода: 🏙️ Калининград — 38/26 °C • ясно • 💨 6 м/с • порывы до 10 м/с • 🔷 1015 гПа ↓.
🌡 <b>Тёплые города</b>
Балтийск: 31/22 °C • ясно • 💨 7 м/с
Гвардейск: 39/21 °C • ясно
Неман: 35/17 °C • ясно
🕘 Лучшее окно: позднее утро и время ближе к закату.
🕘 Лучшее окно: вечером, когда будет свежее.
💱 Курсы (утро): USD 94.12 ₽ ↑0.35 • EUR 101.43 ₽ ↑0.27 • CNY 12.90 ₽ →0.00
☀️ УФ: 7 — высокий
🏭 Воздух: 🟡 умеренный (AQI 58) • PM₂.₅ 12 / PM₁₀ 24 • 🌿 пыльца: умеренная
🌊 Балтика: вода 18 °C • волна 0.4 м.
🌇 Закат сегодня: 21:34
📻 <b>Астрособытия</b>
🌙 🟡 Полнолуние, ♐ (96%)
💚 В плюсе: планы, обучение.
⚫ VoC: 00:00–00:00.
🧪 Радиационный фон: высокий по частному датчику.
🧪 Safecast: 0.22 мкЗв/ч — выше обычного по датчику.
🧲 Космопогода: Kp 0.3 (спокойно), v 420 км/с.
✅ План: прогулка днём, вечером взять лёгкий слой.
#Калининград #погода #здоровье #сегодня #море
"""


def _astro_lines(text: str) -> list[str]:
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("🌇 <b>Солнце, Луна и ритм"))
    return [line for line in lines[start:start + 6] if line.strip()]


def kld_morning_astro_block_has_sun_and_rhythm_title() -> None:
    text = build_morning_format_v2("Калининградская область", LEGACY_FIXTURE)
    assert "🌇 <b>Солнце, Луна и ритм дня</b>" in text
    assert "🌒 Растущий серп в ♍ Дева — 30% освещённости." in text
    assert "✅ Астроритм:" not in text
    assert "💚 В плюсе:" in text


def kld_morning_astro_block_has_sunset_if_available() -> None:
    text = build_morning_format_v2("Калининградская область", LEGACY_FIXTURE)
    assert "🌇 Закат сегодня: 21:32" in _astro_lines(text)


def kld_evening_astro_block_has_tomorrow_wording() -> None:
    text = build_evening_format_v2("Калининградская область", EVENING_FIXTURE)
    assert "🌇 <b>Солнце, Луна и ритм завтрашнего дня</b>" in text
    assert "🌇 Закат завтра: 21:33" in text


def kld_astro_block_stays_compact() -> None:
    for text in (
        build_morning_format_v2("Калининградская область", LEGACY_FIXTURE),
        build_evening_format_v2("Калининградская область", EVENING_FIXTURE),
    ):
        assert len(_astro_lines(text)) <= 6


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
    assert "🧪 Частный датчик: спокойно." in text
    assert "мкЗв" not in text


def kld_morning_keeps_fx_uv_air_plan() -> None:
    text = build_morning_format_v2("Калининградская область", LEGACY_FIXTURE)
    for marker in ("💱 Курсы", "☀️ УФ", "🏭 Воздух", "✅ План:"):
        assert marker in text
    assert "💱 Курсы: USD 90.00 ₽ ↑1.43 · EUR 98.00 ₽ ↓0.22 · CNY 12.00 ₽ →0.00" in text


def kld_morning_fx_cleaner_is_idempotent() -> None:
    fixture = LEGACY_FIXTURE.replace(
        "💱 Курсы (утро): USD 90.00 ₽ (1.43) • EUR 98.00 ₽ (-0.22) • CNY 12.00 ₽ (0.00)",
        "💱 Курсы (утро): USD 90.00 ₽ ↑1.43 • EUR 98.00 ₽ ↓0.22 • CNY 12.00 ₽ →0.00",
    )
    text = build_morning_format_v2("Калининградская область", fixture)
    assert "USD 90.00 ₽ ↑1.43 · EUR 98.00 ₽ ↓0.22 · CNY 12.00 ₽ →0.00" in text
    assert "→0.00 ↓0.22" not in text
    assert "→0.00 ↑1.43" not in text


def kld_morning_safecast_above_observation_is_soft() -> None:
    fixture = LEGACY_FIXTURE.replace(
        "🧪 Safecast: 0.12 мкЗв/ч — фон спокойный.",
        "🧪 Safecast: 0.22 мкЗв/ч — выше обычного по датчику.",
    )
    text = build_morning_format_v2("Калининградская область", fixture)
    wanted = "🧪 Частный датчик: выше обычной точки наблюдения; смотрим динамику."
    assert wanted in text
    assert text.count("🧪") == 1


def kld_morning_has_only_one_plan() -> None:
    text = build_morning_format_v2("Калининградская область", LEGACY_FIXTURE)
    assert text.count("✅ План:") == 1


def kld_morning_astro_block_has_moon_and_plus() -> None:
    text = build_morning_format_v2("Калининградская область", LEGACY_FIXTURE)
    astro = "\n".join(_astro_lines(text))
    assert "🌒 Растущий серп в ♍ Дева — 30% освещённости." in astro
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


def kld_morning_hot_day_uses_cyprus_style_skeleton() -> None:
    text = build_morning_format_v2("Калининградская область", HOT_MORNING_FIXTURE)
    lines = text.splitlines()
    assert lines[-1] == "#Калининград #погода #здоровье #сегодня #море"
    vaybo_lines = [line for line in lines if line.startswith("✨ VayboMeter")]
    window_lines = [line for line in lines if line.startswith("🕘 Лучшее окно")]
    assert vaybo_lines == ["✨ VayboMeter: 7.2/10 — с оговорками; жара и высокий УФ."]
    assert "7.2/10" in vaybo_lines[0]
    assert "🌡 По области: теплее всего — Гвардейск (39°), прохладнее — Балтийск (31°), диапазон 31–39°." in lines
    assert "🌡 По области: жарко; у Балтики обычно свежее" not in text
    assert window_lines == ["🕘 Лучшее окно: до 11:00 и после 18:30; днём — тень."]
    assert "🌡 Ощущается: жарко; на солнце высокая нагрузка." in lines[:5]
    assert "⚠️ Главный нюанс: жара и УФ важнее формальной облачности." in text
    assert "давл. 1015 гПа ↓" in text
    assert "🔷 1015 гПа" not in text
    assert "💧 1015 гПа" not in text
    assert text.count("🧪") == 1
    assert "🧪 Частный датчик: выше обычной точки наблюдения; смотрим динамику." in text
    assert "🧲 Космопогода: спокойно, Kp 0.3." in text
    assert "v 420 км/с" not in text
    assert "🌊 Балтика: вода 18°C; волна 0.4 м; у открытой воды ветер заметнее." in text
    assert "Safecast:" not in text
    assert "Радиационный фон: высокий" not in text
    assert "VoC: 00:00–00:00" not in text
    assert "✅ План: дела и прогулка утром/вечером; днём — вода, тень, SPF и короткие выходы." in text
    assert "лёгкий слой" not in text

    uv_i = lines.index("☀️ УФ 7 — высокий")
    air_i = next(i for i, line in enumerate(lines) if line.startswith("🏭 Воздух:"))
    sensor_i = next(i for i, line in enumerate(lines) if line.startswith("🧪"))
    kp_i = next(i for i, line in enumerate(lines) if line.startswith("🧲 Космопогода:"))
    baltic_i = next(i for i, line in enumerate(lines) if line.startswith("🌊 Балтика:"))
    fx_i = next(i for i, line in enumerate(lines) if line.startswith("💱 Курсы:"))
    astro_i = next(i for i, line in enumerate(lines) if line.startswith("🌇 <b>Солнце, Луна"))
    weather_i = next(i for i, line in enumerate(lines) if line.startswith("🏙 Калининград"))
    feels_i = lines.index("🌡 Ощущается: жарко; на солнце высокая нагрузка.")
    window_i = lines.index("🕘 Лучшее окно: до 11:00 и после 18:30; днём — тень.")
    context_i = lines.index("🌡 По области: теплее всего — Гвардейск (39°), прохладнее — Балтийск (31°), диапазон 31–39°.")
    assert context_i < weather_i < feels_i < window_i < uv_i < air_i < sensor_i < kp_i < baltic_i < fx_i < astro_i
    assert fx_i > weather_i + 1
    assert "🌿 пыльца: умеренная" in text


def kld_morning_adds_baltic_fallback_when_sea_missing() -> None:
    fixture = HOT_MORNING_FIXTURE.replace("🌊 Балтика: вода 18 °C • волна 0.4 м.\n", "")
    text = build_morning_format_v2("Калининградская область", fixture)
    assert "🌊 Балтика: у воды свежее, но ветер заметнее; прогулки — по защищённым променадам." in text


def kld_morning_region_context_fallback_when_only_kaliningrad() -> None:
    fixture = HOT_MORNING_FIXTURE.replace(
        "🌡 <b>Тёплые города</b>\nБалтийск: 31/22 °C • ясно • 💨 7 м/с\nГвардейск: 39/21 °C • ясно\nНеман: 35/17 °C • ясно\n",
        "",
    )
    text = build_morning_format_v2("Калининградская область", fixture)
    assert "🌡 По области: жарко; у Балтики обычно свежее, но ветер заметнее." in text


def kld_morning_postprocess_does_not_reintroduce_duplicates() -> None:
    env_names = (
        "FORMAT_V2",
        "MORNING_BEST_WINDOW",
        "MORNING_VAYBOMETER_SCORE",
        "MORNING_SMART_PLAN",
        "FORMAT_V2_SENSOR_LINE",
    )
    old_env = {name: os.environ.get(name) for name in env_names}
    try:
        for name in env_names:
            os.environ[name] = "1"
        text = build_morning_format_v2("Калининградская область", HOT_MORNING_FIXTURE)
        text = _inject_morning_best_window(text, "morning")
        text = _inject_morning_score(text, "morning")
        text = _inject_sensor_line(text, HOT_MORNING_FIXTURE)
        text = _inject_morning_smart_plan(text, "morning")
        text = sanitize_post_text(text).text
    finally:
        for name, value in old_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    lines = text.splitlines()
    assert text.count("VayboMeter") == 1
    assert text.count("Лучшее окно") == 1
    assert text.count("🧪") == 1
    assert "Safecast:" not in text
    assert "Радиационный фон: высокий" not in text
    assert "лёгкий слой" not in text
    assert lines[-1] == "#Калининград #погода #здоровье #сегодня #море"

    sensor_i = next(i for i, line in enumerate(lines) if line.startswith("🧪"))
    kp_i = next(i for i, line in enumerate(lines) if line.startswith("🧲 Космопогода:"))
    baltic_i = next(i for i, line in enumerate(lines) if line.startswith("🌊 Балтика:"))
    fx_i = next(i for i, line in enumerate(lines) if line.startswith("💱 Курсы:"))
    astro_i = next(i for i, line in enumerate(lines) if line.startswith("🌇 <b>Солнце, Луна"))
    plan_i = next(i for i, line in enumerate(lines) if line.startswith("✅ План:"))
    assert sensor_i < kp_i < baltic_i < fx_i
    assert astro_i < plan_i
    plan = lines[plan_i]
    for word in ("вода", "тень", "SPF"):
        assert word in plan


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
        kld_morning_hot_day_uses_cyprus_style_skeleton,
        kld_morning_adds_baltic_fallback_when_sea_missing,
        kld_morning_region_context_fallback_when_only_kaliningrad,
        kld_morning_postprocess_does_not_reintroduce_duplicates,
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
