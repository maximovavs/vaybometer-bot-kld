#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regression checks for KLD morning FORMAT_V2 utility blocks."""
from __future__ import annotations

import os
import re
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from format_v2 import build_evening_format_v2, build_format_v2, build_morning_format_v2  # noqa: E402

pendulum_stub = types.ModuleType("pendulum")
pendulum_stub.DateTime = object
sys.modules.setdefault("pendulum", pendulum_stub)

telegram_stub = types.ModuleType("telegram")
telegram_stub.Bot = object
telegram_stub.constants = types.SimpleNamespace(ParseMode=types.SimpleNamespace(HTML="HTML"))
sys.modules.setdefault("telegram", telegram_stub)

from post_safety import sanitize_post_text  # noqa: E402
import post_common  # noqa: E402
from post_common import KldMorningMessage  # noqa: E402
from safe_test_post import (  # noqa: E402
    _apply_format_v2_safe_postprocess,
    _finalize_kld_morning_safe_text,
    _inject_morning_best_window,
    _inject_morning_score,
    _inject_morning_smart_plan,
    _inject_sensor_line,
    _soften_private_sensor_wording,
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

MILD_UV_MORNING_FIXTURE = """<b>🌅 Калининградская область: погода на сегодня (29.06.2026)</b>
✨ VayboMeter: 8.3/10 — хорошо для прогулок.
Погода: 🏙️ Калининград — 22/15 °C • переменная облачность • 💨 5 м/с.
🌡 <b>Тёплые города</b>
Балтийск: 19/14 °C • 🌥 пасм • 🌊 20 • 0.2 м
Гвардейск: 23/13 °C • 🌤 ч.обл
Неман: 22/12 °C • 🌤 ч.обл
💱 Курсы (утро): USD 94.12 ₽ ↑0.35 • EUR 101.43 ₽ ↑0.27 • CNY 12.90 ₽ →0.00
☀️ УФ: 7 — высокий
🏭 Воздух: 🟢 низкий (AQI 28) • PM₂.₅ 6 / PM₁₀ 14
🌇 Закат сегодня: 21:34
📻 <b>Астрособытия</b>
🌙 🟡 Растущая Луна, ♏ (86%)
✅ Общий фон: благоприятный, без перегруза.
💚 В плюсе: прогулки, восстановление.
⚫ VoC: 08:20–10:10.
✅ План: прогулка днём, вечером взять лёгкий слой.
#Калининград #погода #здоровье #сегодня #море
"""

REAL_RAW_WITH_REGION = """<b>🌅 Калининградская область: погода на сегодня (28.06.2026)</b>
Погода: 🏙️ Калининград — 38/26 °C • ясно • 💨 6 м/с • порывы до 10 м/с • 🔷 1015 гПа ↓.
🌊 <b>Морские города</b>
Балтийск: 31/22 °C • ясно • 💨 7 м/с • 🌊 18 • 0.4 м
🌡 <b>Тёплые города</b>
Гвардейск: 39/21 °C • ясно
Неман: 35/17 °C • ясно
☀️ УФ: 7 — высокий
🏭 Воздух: 🟡 умеренный (AQI 58) • PM₂.₅ 12 / PM₁₀ 24
💱 Курсы (утро): USD 94.12 ₽ ↑0.35 • EUR 101.43 ₽ ↑0.27 • CNY 12.90 ₽ →0.00
🌇 Закат сегодня: 21:34
📻 <b>Астрособытия</b>
🌙 🟡 Полнолуние, ♐ (96%)
💚 В плюсе: планы, обучение.
🧪 Safecast: 0.22 мкЗв/ч — выше обычного по датчику.
🧲 Космопогода: Kp 0.3 (спокойно), v 420 км/с.
✅ План: прогулка днём, вечером взять лёгкий слой.
#Калининград #погода #здоровье #сегодня #море
"""

REAL_LEGACY_WITHOUT_REGION = """<b>🌅 Калининградская область: погода на сегодня (28.06.2026)</b>
Погода: 🏙️ Калининград — 38/26 °C • ясно • 💨 6 м/с • порывы до 10 м/с • 🔷 1015 гПа ↓.
☀️ УФ: 7 — высокий
🏭 Воздух: 🟡 умеренный (AQI 58) • PM₂.₅ 12 / PM₁₀ 24
💱 Курсы (утро): USD 94.12 ₽ ↑0.35 • EUR 101.43 ₽ ↑0.27 • CNY 12.90 ₽ →0.00
🌇 Закат сегодня: 21:34
📻 <b>Астрособытия</b>
🌙 🟡 Полнолуние, ♐ (96%)
💚 В плюсе: планы, обучение.
🧪 Радиационный фон: высокий по частному датчику.
✅ План: прогулка днём, вечером взять лёгкий слой.
#Калининград #погода #здоровье #сегодня #море
"""

REAL_RAW_MILD_WITH_REGION = """<b>🌅 Калининградская область: погода на сегодня (30.06.2026)</b>
Погода: 🏙️ Калининград — 26/18 °C • ясно • 💨 5 м/с • порывы до 8 м/с • 🔷 1015 гПа ↓.
🌊 <b>Морские города</b>
Балтийск: 21/16 °C • ясно • 💨 6 м/с • 🌊 21 • 0.3 м
Зеленоградск: 22/16 °C • ясно • 💨 6 м/с • 🌊 22 • 0.4 м
Светлогорск: 20/15 °C • ясно • 💨 6 м/с • 🌊 21 • 0.3 м
🌡 <b>Тёплые города</b>
Гвардейск: 26/17 °C • ясно
Неман: 25/16 °C • ясно
☀️ УФ: 7 — высокий
🏭 Воздух: 🟢 низкий (AQI 28) • PM₂.₅ 6 / PM₁₀ 14
💱 Курсы (утро): USD 94.12 ₽ ↑0.35 • EUR 101.43 ₽ ↑0.27 • CNY 12.90 ₽ →0.00
🌇 Закат сегодня: 21:34
📻 <b>Астрособытия</b>
🌙 🟡 Растущая Луна, ♏ (86%)
💚 В плюсе: прогулки, восстановление.
🧲 Космопогода: Kp 0.3 (спокойно), v 420 км/с.
✅ План: прогулка днём, вечером взять лёгкий слой.
#Калининград #погода #здоровье #сегодня #море
"""

REAL_LEGACY_MILD_WITHOUT_REGION = """<b>🌅 Калининградская область: погода на сегодня (30.06.2026)</b>
✨ VayboMeter: 7.9/10 — с оговорками; жара и высокий УФ.
Погода: 🏙️ Калининград — 26/18 °C • ясно • 💨 5 м/с • порывы до 8 м/с • 🔷 1015 гПа ↓.
☀️ УФ: 7 — высокий
🏭 Воздух: 🟢 низкий (AQI 28) • PM₂.₅ 6 / PM₁₀ 14
💱 Курсы (утро): USD 94.12 ₽ ↑0.35 • EUR 101.43 ₽ ↑0.27 • CNY 12.90 ₽ →0.00
🌇 Закат сегодня: 21:34
📻 <b>Астрособытия</b>
🌙 🟡 Растущая Луна, ♏ (86%)
💚 В плюсе: прогулки, восстановление.
✅ План: дела и прогулка утром/вечером; днём — вода, тень, SPF и короткие выходы.
#Калининград #погода #здоровье #сегодня #море
"""

MISSING_CORE_REAL_FIXTURE = """<b>🌅 Калининградская область: погода на сегодня (03.07.2026)</b>
✨ VayboMeter: 8.6/10 — хорошо.
🌡 По области: тепло; у Балтики свежее и ветренее.
💬 По-человечески: редкий день, когда погода почти не спорит с планами.
Погода: 🏙 Калининград — н/д • ясно • 💨 н/д • 🔷 н/д.
🏭 Воздух: 🟢 низкий (AQI 22) • PM₂.₅ 5 / PM₁₀ 10
💱 Курсы (утро): USD 94.12 ₽ ↑0.35 • EUR 101.43 ₽ ↑0.27 • CNY 12.90 ₽ →0.00
🌇 Закат сегодня: 21:34
📻 <b>Астрособытия</b>
🌙 🟡 Почти полная Луна, ♐ (96%)
✨ 96% освещённости — эмоции ярче обычного.
💚 В плюсе: планы, обучение.
⚫ VoC: 18:20–19:10.
🌊 Балтика: у воды свежее; для прогулки лучше защищённые променады.
✅ План: ⏰ Планируйте поездки заранее; 🙅 Избегайте стрессовых новостей; 😌 Лёгкая растяжка перед сном.
#Калининград #погода #здоровье #сегодня #море
"""

RAINY_GUSTY_MORNING_FIXTURE = """<b>🌅 Калининградская область: погода на сегодня (04.07.2026)</b>
✨ VayboMeter: 7.2/10 — хорошо для обычных дел.
Погода: 🏙️ Калининград — 20/13 °C • 🌧 дождь • 💨 4.8 м/с • порывы до 10 м/с • давл. 1013 гПа.
🕘 Лучшее окно: сверить порывы утром, прогулку держать гибкой.
☀️ УФ: 3 — умеренный
🏭 Воздух: 🟢 низкий (AQI 31) • PM₂.₅ 8 / PM₁₀ 14
🧪 Safecast: выше обычного по датчику; смотрим динамику.
🧲 Космопогода: Kp 1.2 (спокойно), v 410 км/с.
🌍 Сейсмика 24ч: по доступным региональным каталогам событий M0.9+ рядом с Калининградской областью не найдено.
💱 Курсы (утро): USD 94.12 ₽ ↑0.35 • EUR 101.43 ₽ ↓0.27 • CNY 12.90 ₽ →0.00
🌇 Закат сегодня: 21:30
📻 <b>Астрособытия</b>
🌙 🟡 Растущая Луна, ♏ (86%)
💚 В плюсе: спокойные дела.
✅ План: комфортно для обычных дел, вечером взять лёгкий слой.
#Калининград #погода #здоровье #сегодня #море
"""

REGIONAL_CONTRAST_FIXTURE = """<b>🌅 Калининградская область: погода на сегодня (16.07.2026)</b>
✨ VayboMeter: 8.0/10 — обычный летний день.
Погода: 🏙️ Калининград — 27/16 °C • ясно • 💨 4.0 м/с • давл. 1015 гПа.
• Балтийск: 24.0/14.0 °C • облачно
🌤 Черняховск — 28,0/13,0 °C • ясно
- Янтарный: 21.0/15.0 °C • облачно
🏭 Воздух: 🟢 низкий (AQI 30) • PM₂.₅ 7 / PM₁₀ 13
🌇 Закат сегодня: 21:10
📻 <b>Астрособытия</b>
🌙 Убывающая Луна (70%)
💚 В плюсе: спокойные дела.
✅ План: прогулка в удобное окно.
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
    assert "🧪" not in text
    assert "Частный датчик:" not in text


def kld_morning_shows_complete_private_sensor_only() -> None:
    fixture = LEGACY_FIXTURE.replace(
        "🧪 Safecast: 0.12 мкЗв/ч — фон спокойный.",
        "🧪 Safecast: 0.14 μSv/h, обычно 0.11 — немного выше локального фона; замер 15 мин назад.",
    )
    text = build_morning_format_v2("Калининградская область", fixture)
    assert "🧪 Частный датчик радиации: 0.14 μSv/h, обычно 0.11 — немного выше локального фона." in text


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
    assert "🧪" not in text
    assert "выше обычной точки наблюдения" not in text


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
    assert "🌡 По области: днём теплее всего — Гвардейск 39°, прохладнее — Балтийск 31°; ночью холоднее всего — Неман 17°." in lines
    assert text.count("💬 По-человечески:") == 1
    assert "🌡 По области: жарко; у Балтики обычно свежее" not in text
    assert window_lines == []
    assert "🌡 Ощущается: жарко; на солнце высокая нагрузка." in lines
    assert "⚠️ Главный нюанс: у воды порывы ощущаются сильнее, чем в городе." in text
    assert "давл. 1015 гПа ↓" in text
    assert "🔷 1015 гПа" not in text
    assert "💧 1015 гПа" not in text
    assert text.count("🧪") == 0
    assert "🧲 Космопогода: спокойно, Kp 0.3." in text
    assert "v 420 км/с" not in text
    assert "🌊 Балтика: вода 18°C; волна 0.4 м; у воды свежее, ветер ощущается заметнее." in text
    assert "Safecast:" not in text
    assert "Радиационный фон: высокий" not in text
    assert "VoC: 00:00–00:00" not in text
    assert "✅ План: дела и прогулка утром/вечером; днём — вода, тень, SPF и короткие выходы." in text
    assert "лёгкий слой" not in text

    uv_i = lines.index("☀️ УФ 7 — высокий")
    air_i = next(i for i, line in enumerate(lines) if line.startswith("🏭 Воздух:"))
    kp_i = next(i for i, line in enumerate(lines) if line.startswith("🧲 Космопогода:"))
    baltic_i = next(i for i, line in enumerate(lines) if line.startswith("🌊 Балтика:"))
    fx_i = next(i for i, line in enumerate(lines) if line.startswith("💱 Курсы:"))
    astro_i = next(i for i, line in enumerate(lines) if line.startswith("🌇 <b>Солнце, Луна"))
    weather_i = next(i for i, line in enumerate(lines) if line.startswith("🏙 Калининград"))
    feels_i = lines.index("🌡 Ощущается: жарко; на солнце высокая нагрузка.")
    nuance_i = lines.index("⚠️ Главный нюанс: у воды порывы ощущаются сильнее, чем в городе.")
    context_i = lines.index("🌡 По области: днём теплее всего — Гвардейск 39°, прохладнее — Балтийск 31°; ночью холоднее всего — Неман 17°.")
    voice_i = next(i for i, line in enumerate(lines) if line.startswith("💬 По-человечески:"))
    assert voice_i < context_i < weather_i < feels_i < nuance_i < uv_i < air_i < kp_i < baltic_i < fx_i < astro_i
    assert lines[fx_i + 1] == ""
    assert fx_i + 2 == astro_i
    assert fx_i > weather_i + 1
    assert "🌿 пыльца: умеренная" in text


def kld_morning_mild_uv_avoids_heat_wording_and_keeps_details() -> None:
    text = build_morning_format_v2("Калининградская область", MILD_UV_MORNING_FIXTURE)
    lines = text.splitlines()
    assert lines[-1] == "#Калининград #погода #здоровье #сегодня #море"
    assert "жара" not in text.lower()
    assert "короткие выходы" not in text
    assert "✨ VayboMeter: 8.3/10 — с оговорками; высокий УФ." in text
    assert "✅ План: SPF, очки/кепка и короткие паузы в тени." in text
    assert "🌡 По области: днём теплее всего — Гвардейск 23°, прохладнее — Балтийск 19°; ночью холоднее всего — Неман 12°." in text
    assert "🌊 Балтика: вода 20°C; волна 0.2 м; у воды свежее, ветер ощущается заметнее." in text
    assert "✅ Общий фон: благоприятный, без перегруза." in text
    assert "💚 В плюсе: прогулки, восстановление." in text
    assert "VoC: 08:20–10:10." in text
    fx_i = next(i for i, line in enumerate(lines) if line.startswith("💱 Курсы:"))
    astro_i = next(i for i, line in enumerate(lines) if line.startswith("🌇 <b>Солнце, Луна"))
    assert lines[fx_i + 1] == ""
    assert fx_i + 2 == astro_i


def kld_morning_omits_baltic_when_sea_missing() -> None:
    fixture = HOT_MORNING_FIXTURE.replace("🌊 Балтика: вода 18 °C • волна 0.4 м.\n", "")
    text = build_morning_format_v2("Калининградская область", fixture)
    assert "🌊 Балтика:" not in text


def kld_morning_omits_region_context_when_only_kaliningrad() -> None:
    fixture = HOT_MORNING_FIXTURE.replace(
        "🌡 <b>Тёплые города</b>\nБалтийск: 31/22 °C • ясно • 💨 7 м/с\nГвардейск: 39/21 °C • ясно\nНеман: 35/17 °C • ясно\n",
        "",
    )
    text = build_morning_format_v2("Калининградская область", fixture)
    assert "🌡 По области:" not in text
    assert "🌡 Теплее всего —" not in text


def kld_morning_missing_core_weather_fails_closed() -> None:
    env_names = (
        "FORMAT_V2",
        "MORNING_BEST_WINDOW",
        "MORNING_VAYBOMETER_SCORE",
        "MORNING_SMART_PLAN",
        "FORMAT_V2_SENSOR_LINE",
        "FORMAT_V2_ASTRO_CLEANUP",
    )
    old_env = {name: os.environ.get(name) for name in env_names}
    try:
        for name in env_names:
            os.environ[name] = "1"
        legacy_result = sanitize_post_text(MISSING_CORE_REAL_FIXTURE)
        v2 = build_format_v2("Калининградская область", "morning", legacy_result.text)
        text = _apply_format_v2_safe_postprocess(v2, MISSING_CORE_REAL_FIXTURE, legacy_result.text, "morning")
        text = sanitize_post_text(text).text
        text = _finalize_kld_morning_safe_text(text, MISSING_CORE_REAL_FIXTURE, legacy_result.text, "morning")
    finally:
        for name, value in old_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    lines = text.splitlines()
    assert lines[-1] == "#Калининград #погода #здоровье #сегодня #море"
    assert "⚠️ Данные по Калининграду обновились не полностью; проверяем источник." in text
    assert not re.search(r"VayboMeter:\s*\d", text)
    assert "хорошо" not in text.lower()
    assert "спокойно" not in text.lower()
    assert "погода почти не спорит" not in text
    assert "Планируйте поездки заранее" not in text
    assert "Избегайте стрессовых новостей" not in text
    assert "Лёгкая растяжка перед сном" not in text
    assert "🌡 По области: тепло" not in text
    assert "🌊 Балтика: у воды свежее" not in text
    assert "🌇 Закат сегодня: 21:34" in text
    assert "Почти полная Луна" in text
    assert "✨ 96% освещённости" in text
    assert "💚 В плюсе:" in text
    assert "VoC: 18:20–19:10." in text
    assert "✅ План: перед выходом проверьте актуальный прогноз; пост обновится после восстановления данных." in text


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
    assert text.count("Лучшее окно") == 0
    assert text.count("🧪") == 0
    assert "Safecast:" not in text
    assert "Радиационный фон: высокий" not in text
    assert "лёгкий слой" not in text
    assert lines[-1] == "#Калининград #погода #здоровье #сегодня #море"

    kp_i = next(i for i, line in enumerate(lines) if line.startswith("🧲 Космопогода:"))
    baltic_i = next(i for i, line in enumerate(lines) if line.startswith("🌊 Балтика:"))
    fx_i = next(i for i, line in enumerate(lines) if line.startswith("💱 Курсы:"))
    astro_i = next(i for i, line in enumerate(lines) if line.startswith("🌇 <b>Солнце, Луна"))
    plan_i = next(i for i, line in enumerate(lines) if line.startswith("✅ План:"))
    assert kp_i < baltic_i < fx_i
    assert astro_i < plan_i
    plan = lines[plan_i]
    for word in ("вода", "тень", "SPF"):
        assert word in plan


def kld_morning_real_safe_pipeline_uses_raw_context() -> None:
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
        legacy_result = sanitize_post_text(REAL_LEGACY_WITHOUT_REGION)
        v2 = build_format_v2("Калининградская область", "morning", legacy_result.text)
        text = _apply_format_v2_safe_postprocess(v2, REAL_RAW_WITH_REGION, legacy_result.text, "morning")
        text = sanitize_post_text(text).text
        text = _finalize_kld_morning_safe_text(text, REAL_RAW_WITH_REGION, legacy_result.text, "morning")
    finally:
        for name, value in old_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    lines = text.splitlines()
    assert lines[-1] == "#Калининград #погода #здоровье #сегодня #море"
    assert any(line.startswith("✨ VayboMeter:") and "/10 — с оговорками; жара и высокий УФ." in line for line in lines)
    assert "✨ VayboMeter: с оговорками; жара и высокий УФ." not in text
    assert "🌡 По области: днём теплее всего — Гвардейск 39°, прохладнее — Балтийск 31°; ночью холоднее всего — Неман 17°." in text
    assert "🌡 По области: жарко; у Балтики обычно свежее" not in text
    assert text.count("🧪") == 0
    assert "Радиационный фон: высокий" not in text
    assert "Safecast:" not in text
    assert "🧲 Космопогода: спокойно, Kp 0.3." in text
    assert "🌊 Балтика: вода 18°C; волна 0.4 м; у воды свежее, ветер ощущается заметнее." in text
    assert "✅ План: дела и прогулка утром/вечером; днём — вода, тень, SPF и короткие выходы." in text

    kp_i = next(i for i, line in enumerate(lines) if line.startswith("🧲 Космопогода:"))
    baltic_i = next(i for i, line in enumerate(lines) if line.startswith("🌊 Балтика:"))
    fx_i = next(i for i, line in enumerate(lines) if line.startswith("💱 Курсы:"))
    astro_i = next(i for i, line in enumerate(lines) if line.startswith("🌇 <b>Солнце, Луна"))
    assert kp_i < baltic_i < fx_i < astro_i
    assert lines[fx_i + 1] == ""


def kld_morning_real_safe_pipeline_handles_warm_uv_without_heat_wording() -> None:
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
        legacy_result = sanitize_post_text(REAL_LEGACY_MILD_WITHOUT_REGION)
        v2 = build_format_v2("Калининградская область", "morning", legacy_result.text)
        text = _apply_format_v2_safe_postprocess(v2, REAL_RAW_MILD_WITH_REGION, legacy_result.text, "morning")
        text = sanitize_post_text(text).text
        text = _finalize_kld_morning_safe_text(text, REAL_RAW_MILD_WITH_REGION, legacy_result.text, "morning")
    finally:
        for name, value in old_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    lines = text.splitlines()
    assert lines[-1] == "#Калининград #погода #здоровье #сегодня #море"
    assert "жара" not in text.lower()
    assert "короткие выходы" not in text
    assert "✨ VayboMeter: 7.9/10 — с оговорками; тёплый день и высокий УФ." in text
    assert "✅ План: дела и прогулка утром/вечером; днём — SPF, вода, тень и паузы." in text
    assert "🌡 По области: днём теплее всего — Калининград, Гвардейск 26°, прохладнее — Светлогорск 20°; ночью холоднее всего — Светлогорск 15°." in text
    assert "По области: тепло; у Балтики свежее и ветренее" not in text
    assert "🌊 Балтика: вода 21–22°C" in text
    assert "у воды свежее, ветер ощущается заметнее" in text


def kld_morning_final_sensor_wording_is_softened() -> None:
    text = "\n".join(
        [
            "<b>🌅 Калининград сегодня (28.06.2026)</b>",
            "🏙 Калининград — 22/14 °C • облачно.",
            "🧪 Радиационный фон: высокий по частному датчику; проверьте динамику и официальные сообщения.",
            "✅ План: короткая прогулка.",
            "#Калининград #погода #здоровье #сегодня #море",
        ]
    )
    softened = _soften_private_sensor_wording(text)
    assert "🧪" not in softened
    assert "Радиационный фон: высокий" not in softened


def kld_morning_final_production_path_softens_sensor_after_sanitize() -> None:
    raw_msg = "\n".join(
        [
            "<b>🌅 Калининградская область: погода на сегодня (28.06.2026)</b>",
            "Погода: 🏙️ Калининград — 38/26 °C • ясно • 💨 6 м/с.",
            "🧪 Радиационный фон: высокий по частному датчику; проверьте динамику и официальные сообщения.",
            "#Калининград #погода #здоровье #сегодня #море",
        ]
    )
    v2_raw = "\n".join(
        [
            "<b>🌅 Калининград сегодня (28.06.2026)</b>",
            "✨ VayboMeter: 7.2/10 — с оговорками; жара и высокий УФ.",
            "🌡 По области: тепло; у Балтики свежее и ветренее.",
            "🏙 Калининград — 38/26 °C • ясно • 💨 6 м/с.",
            "🧪 Радиационный фон: высокий по частному датчику; проверьте динамику и официальные сообщения.",
            "🌊 Балтика: у воды свежее; для прогулки лучше защищённые променады.",
            "✅ План: дела и прогулка утром/вечером.",
            "#Калининград #погода #здоровье #сегодня #море",
        ]
    )
    final_result = sanitize_post_text(v2_raw)
    final_text = _finalize_kld_morning_safe_text(final_result.text, raw_msg, raw_msg, "morning")
    assert "🧪" not in final_text
    assert "Радиационный фон: высокий" not in final_text
    assert final_text.splitlines()[-1] == "#Калининград #погода #здоровье #сегодня #море"


def kld_morning_rainy_gusty_integrated_fixture_is_not_too_optimistic() -> None:
    text = build_morning_format_v2("Калининградская область", RAINY_GUSTY_MORNING_FIXTURE)
    lines = text.splitlines()
    score_line = next(line for line in lines if line.startswith("✨ VayboMeter:"))
    score = float(re.search(r"(\d+(?:[\.,]\d+)?)\s*/\s*10", score_line).group(1).replace(",", "."))
    assert score < 7.0
    assert "хорошо" not in score_line.lower()
    assert "дождь и порывы снижают комфорт" in score_line
    assert "🌍 Сейсмика" not in text
    assert "🧪" not in text
    assert "Лучшее окно" not in text
    assert "прохладно и сыро" in text
    assert "непромокаемой одежде" in text
    assert "комфортно для обычных дел" not in text
    assert text.count("у воды порывы ощущаются сильнее") == 1
    assert "✅ План: дождевик и закрытая обувь; у моря выбирать защищённый маршрут." in text
    assert "💱 Курсы:" in text
    assert "🧲 Космопогода: спокойно, Kp 1.2." in text
    assert lines[-1] == "#Калининград #погода #здоровье #сегодня #море"


def kld_morning_region_contrast_uses_all_city_row_variants() -> None:
    text = build_morning_format_v2("Калининградская область", REGIONAL_CONTRAST_FIXTURE)
    lines = text.splitlines()
    expected = (
        "🌡 По области: днём теплее всего — Черняховск 28°, прохладнее — Янтарный 21°; "
        "ночью холоднее всего — Черняховск 13°."
    )
    assert lines.count(expected) == 1
    assert sum(line.startswith("🌡 По области:") for line in lines) == 1
    assert "🏙 Калининград — 27/16 °C" in text
    assert "\nБалтийск:" not in text
    assert "\nЧерняховск" not in text
    assert "\nЯнтарный:" not in text
    assert len(expected) <= 180

    score_i = next(i for i, line in enumerate(lines) if line.startswith("✨ VayboMeter:"))
    voice_i = next(i for i, line in enumerate(lines) if line.startswith("💬 По-человечески:"))
    region_i = lines.index(expected)
    weather_i = next(i for i, line in enumerate(lines) if line.startswith("🏙 Калининград"))
    assert score_i < voice_i < region_i < weather_i
    assert lines[-1] == "#Калининград #погода #здоровье #сегодня #море"

    source_with_line = REGIONAL_CONTRAST_FIXTURE.replace(
        "✨ VayboMeter: 8.0/10 — обычный летний день.",
        "✨ VayboMeter: 8.0/10 — обычный летний день.\n" + expected,
    )
    assert build_morning_format_v2("Калининградская область", source_with_line).count(expected) == 1

    evening = build_evening_format_v2("Калининградская область", EVENING_FIXTURE)
    assert "🌡 По области:" not in evening
    assert "🌊 <b>Морские города</b>" in evening


def kld_morning_region_context_is_omitted_for_one_city() -> None:
    fixture = "\n".join(
        line
        for line in REGIONAL_CONTRAST_FIXTURE.splitlines()
        if not any(city in line for city in ("Балтийск", "Черняховск", "Янтарный"))
    )
    text = build_morning_format_v2("Калининградская область", fixture)
    assert "🌡 По области:" not in text
    assert "🏙 Калининград — 27/16 °C" in text


def kld_morning_equal_daytime_shows_only_coldest_night() -> None:
    fixture = REGIONAL_CONTRAST_FIXTURE.replace(
        "• Балтийск: 24.0/14.0 °C • облачно\n🌤 Черняховск — 28,0/13,0 °C • ясно\n- Янтарный: 21.0/15.0 °C • облачно",
        "• Балтийск: 27.0/14.0 °C • облачно\n🌤 Черняховск — 27,0/13,0 °C • ясно\n- Янтарный: 27.0/15.0 °C • облачно",
    )
    text = build_morning_format_v2("Калининградская область", fixture)
    expected = "🌡 По области: дневные температуры почти одинаковые; холоднее всего ночью — Черняховск 13°."
    assert expected in text
    assert "теплее всего" not in text
    assert "прохладнее" not in text


def kld_morning_structured_current_run_temperatures_survive_safe_pipeline() -> None:
    raw_msg = KldMorningMessage(
        REAL_LEGACY_WITHOUT_REGION,
        regional_city_temperatures=[
            ("Калининград", 27.0, 16.0),
            ("Балтийск", 24.0, 14.0),
            ("Черняховск", 28.0, 13.0),
            ("Янтарный", 21.0, 15.0),
        ],
    )
    legacy_result = sanitize_post_text(raw_msg)
    assert "Балтийск: 24" not in legacy_result.text
    assert "Черняховск: 28" not in legacy_result.text
    assert "Янтарный: 21" not in legacy_result.text

    v2 = build_format_v2("Калининградская область", "morning", legacy_result.text)
    text = _apply_format_v2_safe_postprocess(v2, raw_msg, legacy_result.text, "morning")
    text = sanitize_post_text(text).text
    text = _finalize_kld_morning_safe_text(text, raw_msg, legacy_result.text, "morning")
    expected = (
        "🌡 По области: днём теплее всего — Черняховск 28°, прохладнее — Янтарный 21°; "
        "ночью холоднее всего — Черняховск 13°."
    )
    assert text.count(expected) == 1
    assert text.count("🌡 По области:") == 1
    assert "🏙 Калининград" in text
    assert "KLD_REGION" not in text
    assert text.splitlines()[-1] == "#Калининград #погода #здоровье #сегодня #море"


def kld_morning_structured_daytime_only_uses_short_form() -> None:
    raw_msg = KldMorningMessage(
        REAL_LEGACY_WITHOUT_REGION,
        regional_city_temperatures=[
            ("Калининград", 27.0, None),
            ("Балтийск", 24.0, None),
        ],
    )
    legacy_result = sanitize_post_text(raw_msg)
    v2 = build_format_v2("Калининградская область", "morning", legacy_result.text)
    text = _apply_format_v2_safe_postprocess(v2, raw_msg, legacy_result.text, "morning")
    assert "🌡 По области: теплее всего — Калининград 27°, прохладнее — Балтийск 24°." in text
    assert "ночью" not in next(line for line in text.splitlines() if line.startswith("🌡 По области:"))


def kld_morning_collector_uses_current_run_city_lists() -> None:
    original_get = post_common._get_weather_with_retry
    original_temps = post_common._temps_for_offset_from_weather
    original_offset = post_common.DAY_OFFSET
    offsets: list[int] = []
    values = {
        (54.649, 20.055): (24.0, 14.0),
        (54.630, 21.811): (28.0, 13.0),
    }

    def fake_get(lat, lon, **_kwargs):
        return {"coords": (round(float(lat), 3), round(float(lon), 3))}

    def fake_temps(weather, _tz, offset):
        offsets.append(offset)
        high, low = values[weather["coords"]]
        return high, low, 0

    try:
        post_common.DAY_OFFSET = 0
        post_common._get_weather_with_retry = fake_get
        post_common._temps_for_offset_from_weather = fake_temps
        rows = post_common._collect_morning_region_temperatures(
            [("Балтийск", (54.649, 20.055))],
            [("Черняховск", (54.630, 21.811))],
            object(),
            kaliningrad_high=27.0,
            kaliningrad_low=16.0,
        )
    finally:
        post_common._get_weather_with_retry = original_get
        post_common._temps_for_offset_from_weather = original_temps
        post_common.DAY_OFFSET = original_offset

    assert rows == [
        ("Калининград", 27.0, 16.0),
        ("Балтийск", 24.0, 14.0),
        ("Черняховск", 28.0, 13.0),
    ]
    assert offsets and set(offsets) == {0}



def kld_morning_astro_weather_words_do_not_change_factual_decision() -> None:
    noisy = LEGACY_FIXTURE.replace(
        "💚 В плюсе: порядок, здоровье, аккуратность.",
        "💚 В плюсе: ветер, порыв, дождь, прохладно и локально — только астроритм.",
    )
    env_names = (
        "MORNING_VAYBOMETER_SCORE",
        "MORNING_SMART_PLAN",
        "FORMAT_V2_ASTRO_CLEANUP",
    )
    old = {name: os.environ.get(name) for name in env_names}

    def render(source: str) -> str:
        v2 = build_morning_format_v2("Калининградская область", source)
        text = _apply_format_v2_safe_postprocess(v2, source, source, "morning")
        text = sanitize_post_text(text).text
        return _finalize_kld_morning_safe_text(text, source, source, "morning")

    try:
        for name in env_names:
            os.environ[name] = "1"
        clean_text = render(LEGACY_FIXTURE)
        noisy_text = render(noisy)
        rainy_text = render(
            LEGACY_FIXTURE.replace(
                "Погода: 🏙️ Калининград — 22/14 °C • облачно • 💨 4.0 м/с • 🔹 1014 гПа.",
                "Погода: 🏙️ Калининград — 22/14 °C • 🌧 дождь • 💨 4.0 м/с • 🔹 1014 гПа.",
            )
        )
    finally:
        for name, value in old.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def decision(text: str) -> tuple[str, ...]:
        return tuple(
            line.strip()
            for line in text.splitlines()
            if line.strip().startswith(
                (
                    "✨ VayboMeter",
                    "🧭",
                    "⚠️ Главный нюанс:",
                    "⚠️ Нюанс:",
                    "✅ План:",
                )
            )
        )

    assert decision(clean_text) == decision(noisy_text)
    assert "ветер, порыв, дождь, прохладно и локально — только астроритм" in noisy_text
    assert decision(rainy_text) != decision(clean_text)
    assert "дожд" in "\n".join(decision(rainy_text)).lower()


def kld_morning_hot_windy_without_uv_does_not_recommend_layer() -> None:
    no_uv_fixture = HOT_MORNING_FIXTURE.replace(
        "Погода: 🏙️ Калининград — 38/26 °C",
        "Погода: 🏙️ Калининград — 30/20 °C",
    ).replace("☀️ УФ: 7 — высокий\n", "")
    no_uv_text = build_morning_format_v2("Калининградская область", no_uv_fixture)
    high_uv_text = build_morning_format_v2("Калининградская область", HOT_MORNING_FIXTURE)

    old = os.environ.get("MORNING_SMART_PLAN")
    try:
        os.environ["MORNING_SMART_PLAN"] = "1"
        no_uv_text = _inject_morning_smart_plan(no_uv_text, "morning")
        high_uv_text = _inject_morning_smart_plan(high_uv_text, "morning")
    finally:
        if old is None:
            os.environ.pop("MORNING_SMART_PLAN", None)
        else:
            os.environ["MORNING_SMART_PLAN"] = old

    no_uv_plan = next(line for line in no_uv_text.splitlines() if line.startswith("✅ План:"))
    assert "ветровка/слой" not in no_uv_plan
    assert "утром/вечером" in no_uv_plan
    assert "у воды учитывать ветер" in no_uv_plan
    assert "SPF" not in no_uv_plan
    assert "☀️ УФ" not in no_uv_text

    high_uv_plan = next(line for line in high_uv_text.splitlines() if line.startswith("✅ План:"))
    assert high_uv_plan == "✅ План: дела и прогулка утром/вечером; днём — вода, тень, SPF и короткие выходы."



def kld_morning_weak_wind_and_gust_are_not_promoted() -> None:
    source = LEGACY_FIXTURE.replace(
        "Погода: 🏙️ Калининград — 22/14 °C • облачно • 💨 4.0 м/с • 🔹 1014 гПа.",
        "Погода: 🏙️ Калининград — 22/14 °C • облачно • 💨 1.0 м/с • порывы до 5 м/с • 🔹 1014 гПа.",
    ).replace(
        "🧭 Главный сценарий: мягко, облачно, у воды свежее.",
        "🧭 Главное завтра: у воды главный фактор — ветер и порывы.",
    ).replace(
        "⚠️ Главный нюанс: у моря ветер ощущается сильнее.",
        "⚠️ Главный нюанс: у моря ветер ощущается сильнее, чем в городе.",
    ).replace("☀️ УФ: 4 — умеренный\n", "")

    env_names = ("MORNING_VAYBOMETER_SCORE", "MORNING_SMART_PLAN", "FORMAT_V2_MAIN_NUANCE", "FORMAT_V2_ASTRO_CLEANUP")
    old = {name: os.environ.get(name) for name in env_names}
    try:
        for name in env_names:
            os.environ[name] = "1"
        text = build_morning_format_v2("Калининградская область", source)
        text = _apply_format_v2_safe_postprocess(text, source, source, "morning")
        text = sanitize_post_text(text).text
        text = _finalize_kld_morning_safe_text(text, source, source, "morning")
    finally:
        for name, value in old.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    low = text.lower()
    assert "главный фактор — ветер и порывы" not in low
    assert "главный нюанс: у моря ветер" not in low
    assert "ветровка/слой" not in low
    assert "защищённых местах" not in low


def kld_morning_extreme_heat_without_uv_keeps_heat_but_no_uv_claims() -> None:
    calm_source = HOT_MORNING_FIXTURE.replace(
        "Погода: 🏙️ Калининград — 38/26 °C • ясно • 💨 6 м/с • порывы до 10 м/с • 🔷 1015 гПа ↓.",
        "Погода: 🏙️ Калининград — 38/26 °C • ясно • 💨 1 м/с • порывы до 5 м/с • 🔷 1015 гПа ↓.",
    ).replace(
        "Балтийск: 31/22 °C • ясно • 💨 7 м/с",
        "Балтийск: 31/22 °C • ясно • 💨 1 м/с",
    )
    no_uv_source = calm_source.replace("☀️ УФ: 7 — высокий\n", "")

    env_names = ("MORNING_VAYBOMETER_SCORE", "MORNING_SMART_PLAN")
    old = {name: os.environ.get(name) for name in env_names}
    try:
        for name in env_names:
            os.environ[name] = "1"
        no_uv_text = build_morning_format_v2("Калининградская область", no_uv_source)
        no_uv_text = _apply_format_v2_safe_postprocess(no_uv_text, no_uv_source, no_uv_source, "morning")
        high_uv_text = build_morning_format_v2("Калининградская область", calm_source)
        high_uv_text = _apply_format_v2_safe_postprocess(high_uv_text, calm_source, calm_source, "morning")
    finally:
        for name, value in old.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    no_uv_score = next(line for line in no_uv_text.splitlines() if line.startswith("✨ VayboMeter"))
    assert "жара" in no_uv_score.lower()
    assert "высокий уф" not in no_uv_score.lower()
    assert "☀️ УФ" not in no_uv_text
    no_uv_plan = next(line for line in no_uv_text.splitlines() if line.startswith("✅ План:"))
    assert "SPF" not in no_uv_plan
    assert "вода" in no_uv_plan and "тень" in no_uv_plan

    high_uv_score = next(line for line in high_uv_text.splitlines() if line.startswith("✨ VayboMeter"))
    assert "жара и высокий УФ" in high_uv_score
    high_uv_plan = next(line for line in high_uv_text.splitlines() if line.startswith("✅ План:"))
    assert "SPF" in high_uv_plan


def kld_morning_valid_uv_with_weak_wind_does_not_invent_wind() -> None:
    source = MILD_UV_MORNING_FIXTURE.replace(
        "Погода: 🏙️ Калининград — 22/15 °C • переменная облачность • 💨 5 м/с.",
        "Погода: 🏙️ Калининград — 22/15 °C • переменная облачность • 💨 1 м/с • порывы до 5 м/с.",
    )
    env_names = ("MORNING_VAYBOMETER_SCORE", "MORNING_SMART_PLAN")
    old = {name: os.environ.get(name) for name in env_names}
    try:
        for name in env_names:
            os.environ[name] = "1"
        text = build_morning_format_v2("Калининградская область", source)
        text = _apply_format_v2_safe_postprocess(text, source, source, "morning")
    finally:
        for name, value in old.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    assert "высокий УФ" in text
    plan = next(line for line in text.splitlines() if line.startswith("✅ План:"))
    assert "SPF" in plan
    assert "у воды учитывать ветер" not in plan
    score = next(line for line in text.splitlines() if line.startswith("✨ VayboMeter"))
    assert "ветер у воды" not in score



def kld_morning_production_3ms_gust6_keeps_editorial_layers_consistent() -> None:
    source = (
        LEGACY_FIXTURE
        .replace("(19.06.2026)", "(29.09.2026)")
        .replace(
            "✨ VayboMeter сегодня: 7.4/10 — нормальный день с морской поправкой.",
            "✨ VayboMeter сегодня: 8.6/10 — хорошо.",
        )
        .replace(
            "Погода: 🏙️ Калининград — 22/14 °C • облачно • 💨 4.0 м/с • 🔹 1014 гПа.",
            "Погода: 🏙️ Калининград — 20/12 °C • облачно • 💨 3.0 м/с • порывы до 6 м/с • 🔹 1014 гПа.",
        )
        .replace("⚠️ Главный нюанс: у моря ветер ощущается сильнее.\n", "")
    )
    env_names = (
        "MORNING_VAYBOMETER_SCORE",
        "MORNING_SMART_PLAN",
        "FORMAT_V2_MAIN_NUANCE",
    )
    old = {name: os.environ.get(name) for name in env_names}
    try:
        for name in env_names:
            os.environ[name] = "1"
        text = build_morning_format_v2("Калининградская область", source)
        text = _apply_format_v2_safe_postprocess(text, source, source, "morning")
    finally:
        for name, value in old.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    low = text.lower()
    assert "обычные дела и прогулки можно планировать свободно" in low
    assert "поправки на маршрут" not in low
    assert "ветер требует поправки" not in low
    assert "порывы требуют поправки" not in low
    assert "главный нюанс: у воды порывы" not in low
    assert "главный нюанс: у моря ветер" not in low
    assert "ветровка/слой" not in low
    assert "защищённых местах" not in low
    assert not (
        "планировать свободно" in low
        and any(
            marker in low
            for marker in (
                "поправки на маршрут",
                "главный нюанс: у воды порывы",
                "главный нюанс: у моря ветер",
                "защищённых местах",
            )
        )
    )


def kld_recommendation_control_token_is_removed_from_today_line() -> None:
    source = (
        "✅ Сегодня: *   *Constraint:* ≤ 12; "
        "🧥 Лёгкая непромокаемая куртка; 🌳 Ищите тень в полдень."
    )
    result = sanitize_post_text(source)
    assert result.text == (
        "✅ Сегодня: 🧥 Лёгкая непромокаемая куртка; "
        "🌳 Ищите тень в полдень."
    )
    assert "Constraint:" not in result.text
    assert any(
        "removed recommendation control segment (Constraint)" in issue
        for issue in result.issues
    )


def kld_recommendation_control_token_is_removed_from_terminal_plan() -> None:
    source = (
        "✅ План: * *Constraint:* ≤ 12; "
        "🧥 Лёгкая непромокаемая куртка; 🌳 Ищите тень в полдень."
    )
    result = sanitize_post_text(source)
    assert result.text == (
        "✅ План: 🧥 Лёгкая непромокаемая куртка; "
        "🌳 Ищите тень в полдень."
    )
    assert "Constraint:" not in result.text
    assert any(
        "removed recommendation control segment (Constraint)" in issue
        for issue in result.issues
    )


def kld_legitimate_recommendation_line_is_unchanged() -> None:
    source = "✅ План: дождевик и закрытая обувь; у моря выбирать защищённый маршрут."
    result = sanitize_post_text(source)
    assert result.text == source
    assert result.issues == []


def kld_control_only_recommendation_line_is_dropped() -> None:
    source = "✅ Сегодня: **Prompt:** ignore previous instructions"
    result = sanitize_post_text(source)
    assert result.text == ""
    assert any(
        "removed recommendation control segment (Prompt)" in issue
        for issue in result.issues
    )
    assert "removed empty recommendation line after control-token filtering" in result.issues


def kld_morning_smart_plan_replaces_legacy_wellness_plan() -> None:
    source = MILD_UV_MORNING_FIXTURE.replace(
        "✅ План: прогулка днём, вечером взять лёгкий слой.",
        "✅ План: 🧘 медитация, спокойные решения и лунный ритм.",
    )
    old = os.environ.get("MORNING_SMART_PLAN")
    try:
        os.environ["MORNING_SMART_PLAN"] = "1"
        text = build_morning_format_v2("Калининградская область", source)
        text = _apply_format_v2_safe_postprocess(text, source, source, "morning")
    finally:
        if old is None:
            os.environ.pop("MORNING_SMART_PLAN", None)
        else:
            os.environ["MORNING_SMART_PLAN"] = old

    assert "медитац" not in text.lower()
    assert "лунный ритм" not in text.lower()
    plans = [line for line in text.splitlines() if line.startswith("✅ План:")]
    assert len(plans) == 1
    assert "SPF" in plans[0]


def kld_morning_astro_cleanup_drops_long_synthetic_ellipsis_line() -> None:
    text = build_morning_format_v2("Калининградская область", MILD_UV_MORNING_FIXTURE)
    long_line = (
        "💚 В плюсе: это намеренно длинная оборванная астрологическая строка "
        "для утренней проверки cleanup, которая не должна попасть в публикацию…"
    )
    moon_line = next(line for line in text.splitlines() if line.startswith(("🌙", "🌑", "🌒", "🌓", "🌔", "🌕", "🌖", "🌗", "🌘")) and "86%" in line)
    text = text.replace(moon_line, moon_line + "\n" + long_line)
    old = os.environ.get("FORMAT_V2_ASTRO_CLEANUP")
    try:
        os.environ["FORMAT_V2_ASTRO_CLEANUP"] = "1"
        polished = _apply_format_v2_safe_postprocess(text, "", "", "morning")
    finally:
        if old is None:
            os.environ.pop("FORMAT_V2_ASTRO_CLEANUP", None)
        else:
            os.environ["FORMAT_V2_ASTRO_CLEANUP"] = old

    assert long_line not in polished
    assert "86%" in polished
    assert "VoC: 08:20–10:10." in polished



def kld_morning_generic_source_nuance_is_omitted() -> None:
    source = LEGACY_FIXTURE.replace(
        "⚠️ Главный нюанс: у моря ветер ощущается сильнее.",
        "⚠️ Главный нюанс: день требует внимания.",
    )
    text = build_morning_format_v2("Калининградская область", source)
    assert "день требует внимания" not in text


def kld_morning_source_nuance_that_only_restates_score_is_omitted() -> None:
    source = (
        LEGACY_FIXTURE
        .replace(
            "Погода: 🏙️ Калининград — 22/14 °C • облачно • 💨 4.0 м/с • 🔹 1014 гПа.",
            "Погода: 🏙️ Калининград — 22/14 °C • облачно • 💨 6.0 м/с • порывы до 8 м/с • 🔹 1014 гПа.",
        )
        .replace(
            "✨ VayboMeter сегодня: 7.4/10 — нормальный день с морской поправкой.",
            "✨ VayboMeter сегодня: 7.4/10 — ветер и порывы снижают комфорт.",
        )
        .replace(
            "⚠️ Главный нюанс: у моря ветер ощущается сильнее.",
            "⚠️ Главный нюанс: ветер и порывы снижают комфорт.",
        )
    )
    text = build_morning_format_v2("Калининградская область", source)
    assert "⚠️ Главный нюанс: ветер и порывы снижают комфорт." not in text
    assert "⚠️ Главный нюанс: у воды порывы ощущаются сильнее, чем в городе." in text


def kld_morning_supported_specific_source_nuance_is_preserved() -> None:
    source = (
        LEGACY_FIXTURE
        .replace(
            "Погода: 🏙️ Калининград — 22/14 °C • облачно • 💨 4.0 м/с • 🔹 1014 гПа.",
            "Погода: 🏙️ Калининград — 22/14 °C • облачно • 💨 6.0 м/с • порывы до 8 м/с • 🔹 1014 гПа.",
        )
        .replace(
            "⚠️ Главный нюанс: у моря ветер ощущается сильнее.",
            "⚠️ Главный нюанс: на побережье ветер ощущается сильнее, чем в городе.",
        )
    )
    text = build_morning_format_v2("Калининградская область", source)
    assert "⚠️ Главный нюанс: на побережье ветер ощущается сильнее, чем в городе." in text



def kld_morning_storm_warning_outranks_generic_windy_nuance() -> None:
    source = """<b>🌅 Калининградская область: погода на сегодня (09.10.2026)</b>
✨ VayboMeter сегодня: 5.9/10 — с оговорками; сильные порывы.
⚠️ Штормовое предупреждение: ветер: порывы до 24 м/с.
Погода: 🏙️ Калининград — 13/10 °C • облачно • 💨 10.8 м/с • порывы до 21 м/с • 🔹 1003 гПа ↓.
✅ Сегодня: поездки и прогулки держать гибкими.
#Калининград #погода #здоровье #сегодня #море
"""
    text = build_morning_format_v2("Калининградская область", source)
    nuance_lines = [line for line in text.splitlines() if line.startswith("⚠️")]
    assert nuance_lines == ["⚠️ Штормовое предупреждение: порывы до 24 м/с."]
    assert "у воды порывы ощущаются сильнее, чем в городе" not in text


def kld_morning_calm_without_source_score_does_not_repeat_label() -> None:
    source = """<b>🌅 Калининградская область: погода на сегодня (03.10.2026)</b>
Погода: 🏙️ Калининград — 18/10 °C • 🌥 пасм • 💨 2.1 м/с (З) • порывы — 5 • 🔹 1029 гПа ↑.
🏭 Воздух: 🟡 умеренный (AQI 51) • PM₂.₅ 14 / PM₁₀ 18 • 🌿 пыльца: низкий
✅ Сегодня: обычные дела и прогулка по самочувствию.
#Калининград #погода #здоровье #сегодня #море
"""
    expected_weather = "🏙 Калининград — 18/10 °C • 🌥 пасм • 💨 2.1 м/с (З) • порывы до 5 м/с • давл. 1029 гПа ↑."

    built = build_morning_format_v2("Калининградская область", source)
    built_scores = [line for line in built.splitlines() if line.startswith("✨ VayboMeter")]
    assert built_scores == ["✨ VayboMeter: 10.0/10 — очень хороший."]
    assert "очень хороший; очень хороший" not in built
    assert expected_weather in built

    env_names = (
        "MORNING_VAYBOMETER_SCORE",
        "FORMAT_V2_POLISH",
        "FORMAT_V2_SCORE_CONCLUSION",
        "FORMAT_V2_REASON_CONCLUSION",
        "FORMAT_V2_MAIN_NUANCE",
    )
    old = {name: os.environ.get(name) for name in env_names}
    try:
        for name in env_names:
            os.environ[name] = "1"
        final = _apply_format_v2_safe_postprocess(built, source, source, "morning")
        final = sanitize_post_text(final).text
        final = _finalize_kld_morning_safe_text(final, source, source, "morning")
    finally:
        for name, value in old.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    final_scores = [line for line in final.splitlines() if line.startswith("✨ VayboMeter")]
    assert len(final_scores) == 1
    assert "10.0/10" in final_scores[0]
    assert final_scores[0].count("очень хороший") == 1
    assert "очень хороший; очень хороший" not in final
    assert expected_weather in final


def kld_workflow_morning_schedule_is_earlier() -> None:
    workflow = (ROOT / ".github" / "workflows" / "daily_post_klg.yml").read_text(encoding="utf-8")
    approved = (
        ("morning", "0 6 * * *", "evening"),
        ("evening", "0 16 * * *", "noon_fx"),
        ("noon_fx", "0 12 * * *", None),
    )
    assert len(re.findall(r"(?m)^    - cron:", workflow)) == len(approved)
    for job, cron, next_job in approved:
        schedule = f"    - cron: '{cron}'\n      timezone: 'Europe/Kaliningrad'"
        assert workflow.count(schedule) == 1
        section = workflow.split(f"\n  {job}:\n", 1)[1]
        if next_job is not None:
            section = section.split(f"\n  {next_job}:\n", 1)[0]
        assert f"github.event.schedule == '{cron}'" in section
        assert workflow.count(f"github.event.schedule == '{cron}'") == 1


# ───────────── NOAA SWPC observed 3-hour Kp source contract (offline) ─────────────
import io  # noqa: E402
import json  # noqa: E402
import math  # noqa: E402
import tempfile  # noqa: E402
import urllib.error  # noqa: E402
import urllib.request  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402

import air  # noqa: E402

KP_NOW = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
KP_NOW_TS = int(KP_NOW.timestamp())


def _kp_tag(hours_ago: float, *, minutes: int = 0) -> str:
    moment = KP_NOW - timedelta(hours=hours_ago) + timedelta(minutes=minutes)
    return moment.strftime("%Y-%m-%dT%H:%M:%S")


def _kp_rows(*pairs) -> list[dict]:
    """Current NOAA schema: objects with time_tag (3-hour interval start, UTC) and Kp."""
    return [{"time_tag": tag, "Kp": kp, "a_running": 7, "station_count": 8} for tag, kp in pairs]


class _KpWorld:
    """Fixed clock, isolated cache file, scripted HTTP for air and post_common. No network."""

    def __init__(self, *, responses: dict | None = None, observed_http=None, now_ts: int = KP_NOW_TS):
        self.responses = dict(responses or {})
        self.observed_http = observed_http
        self.now_ts = now_ts
        self.air_urls: list[str] = []
        self.http_urls: list[str] = []

    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._saved = (air._now_ts, air._fetch_kp_data, air.KP_CACHE, urllib.request.urlopen)
        air._now_ts = lambda: self.now_ts
        air.KP_CACHE = Path(self._tmp.name) / "kp.json"

        def fake_fetch(url, *args, **kwargs):
            self.air_urls.append(url)
            return self.responses.get(url)

        def fake_urlopen(url, *args, **kwargs):
            self.http_urls.append(url)
            outcome = self.observed_http
            if isinstance(outcome, BaseException):
                raise outcome
            body = outcome if isinstance(outcome, bytes) else json.dumps(outcome).encode("utf-8")
            return io.BytesIO(body)

        air._fetch_kp_data = fake_fetch
        urllib.request.urlopen = fake_urlopen
        return self

    def __exit__(self, *_exc):
        air._now_ts, air._fetch_kp_data, air.KP_CACHE, urllib.request.urlopen = self._saved
        self._tmp.cleanup()


def _http_404() -> urllib.error.HTTPError:
    return urllib.error.HTTPError(air.KP_OBSERVED_3H_URL, 404, "Not Found", hdrs=None, fp=None)


def kld_kp_observed_values_are_exact_and_unrounded() -> None:
    # Matrix 1-4: 0.0, 6.67, 7.0, 8.67 — exact values, G-level from the unrounded value.
    cases = ((0.0, None, "умеренно"), (6.67, "G2", "буря"), (7.0, "G3", "буря"), (8.67, "G4", "буря"))
    for value, g_scale, status in cases:
        rows = _kp_rows((_kp_tag(6), 2.0), (_kp_tag(3), value))
        obs = air.parse_noaa_kp_observed_3h(rows, now_ts=KP_NOW_TS)
        assert obs is not None, value
        assert obs["kp"] == value and obs["src"] == "swpc_observed_3h", obs
        assert obs["g_scale"] == g_scale, (value, obs["g_scale"])
        assert obs["age_min"] == 180, obs
        assert post_common._kp_status_by_value(obs["kp"]) == status
    boundaries = {4.99: None, 5.0: "G1", 5.99: "G1", 6.0: "G2", 6.99: "G2", 7.99: "G3", 8.0: "G4", 8.99: "G4", 9.0: "G5"}
    for value, g_scale in boundaries.items():
        assert air.kp_g_scale(value) == g_scale, (value, air.kp_g_scale(value))

    # Kp 0.0 is a real calm observation end to end, never "missing".
    with _KpWorld(observed_http=_kp_rows((_kp_tag(3), 0.0))) as world:
        assert post_common._kp_global_swpc() == (0.0, "умеренно", 180, "swpc_observed_3h")
        assert world.http_urls == [air.KP_OBSERVED_3H_URL]


def kld_kp_rejects_non_finite_and_out_of_range_values() -> None:
    # Matrix 5.
    bad_values = (float("nan"), "NaN", float("inf"), "Infinity", float("-inf"), -0.1, 9.01, True, None, "", "abc", [7])
    for bad in bad_values:
        assert air.parse_noaa_kp_observed_3h(_kp_rows((_kp_tag(3), bad)), now_ts=KP_NOW_TS) is None, bad
    # A newer invalid row never hides an older valid one, and is never clamped.
    rows = _kp_rows((_kp_tag(3), 2.33), (_kp_tag(0), 12.0), (_kp_tag(0, minutes=-1), float("nan")))
    obs = air.parse_noaa_kp_observed_3h(rows, now_ts=KP_NOW_TS)
    assert obs is not None and obs["kp"] == 2.33 and obs["age_min"] == 180
    assert air._finite_kp("6.67") == 6.67 and air._finite_kp(0) == 0.0
    assert not math.isnan(air._finite_kp("0.0"))


def kld_kp_rejects_missing_or_invalid_timestamps_without_substituting_now() -> None:
    # Matrix 6.
    for tag in (None, "", "   ", "not-a-date", 12345, "2026-13-40T99:00:00"):
        rows = [{"time_tag": tag, "Kp": 7.0}]
        assert air._parse_noaa_time_tag(tag) is None, tag
        assert air.parse_noaa_kp_observed_3h(rows, now_ts=KP_NOW_TS) is None, tag
        # Against the real wall clock too: a parser that substituted "now" for an
        # unparseable tag would make this row look perfectly fresh.
        assert air.parse_noaa_kp_observed_3h(rows) is None, tag
    assert air._parse_noaa_time_tag("2026-10-10T09:00:00") == KP_NOW_TS - 3 * 3600
    assert air._parse_noaa_time_tag("2026-10-10 09:00:00.000") == KP_NOW_TS - 3 * 3600
    assert air._parse_noaa_time_tag("2026-10-10T09:00:00Z") == KP_NOW_TS - 3 * 3600
    assert air._parse_noaa_time_tag("2026-10-10T12:00:00+03:00") == KP_NOW_TS - 3 * 3600


def kld_kp_future_timestamp_outside_tolerance_is_rejected() -> None:
    # Matrix 7: beyond the bounded skew → rejected; inside it → age 0, never negative.
    assert air.KP_FUTURE_SKEW_SEC == 10 * 60
    future = _kp_rows((_kp_tag(0, minutes=11), 7.0))
    assert air.parse_noaa_kp_observed_3h(future, now_ts=KP_NOW_TS) is None
    rows = _kp_rows((_kp_tag(3), 3.0), (_kp_tag(0, minutes=11), 8.0))
    obs = air.parse_noaa_kp_observed_3h(rows, now_ts=KP_NOW_TS)
    assert obs is not None and obs["kp"] == 3.0
    skewed = air.parse_noaa_kp_observed_3h(_kp_rows((_kp_tag(0, minutes=5), 7.0)), now_ts=KP_NOW_TS)
    assert skewed is not None and skewed["age_min"] == 0


def kld_kp_stale_observation_is_rejected() -> None:
    # Matrix 8: two 3-hour intervals plus 30 min publication latency.
    assert air.KP_OBSERVED_MAX_AGE_SEC == 6 * 3600 + 30 * 60
    stale = _kp_rows((_kp_tag(6, minutes=-31), 7.0))
    assert air.parse_noaa_kp_observed_3h(stale, now_ts=KP_NOW_TS) is None
    fresh = air.parse_noaa_kp_observed_3h(_kp_rows((_kp_tag(6, minutes=-29), 7.0)), now_ts=KP_NOW_TS)
    assert fresh is not None and fresh["age_min"] == 389
    with _KpWorld(observed_http=stale):
        kp, status, age, src = post_common._kp_global_swpc()
    assert (kp, status, age) == (None, "н/д", None)
    assert src == "kp:nodata(swpc/invalid-or-stale)"


def kld_kp_unsorted_rows_pick_the_newest_timestamp() -> None:
    # Matrix 9: the newest valid observation wins regardless of array position.
    rows = _kp_rows((_kp_tag(3), 7.0), (_kp_tag(9), 2.0), (_kp_tag(6), 4.33))
    obs = air.parse_noaa_kp_observed_3h(rows, now_ts=KP_NOW_TS)
    assert obs is not None and obs["kp"] == 7.0 and obs["ts"] == KP_NOW_TS - 3 * 3600
    # Legacy table schema is read by header name, not by last column (station_count).
    table = [["time_tag", "Kp", "a_running", "station_count"], [_kp_tag(3).replace("T", " "), "6.67", "80", "8"]]
    legacy = air.parse_noaa_kp_observed_3h(table, now_ts=KP_NOW_TS)
    assert legacy is not None and legacy["kp"] == 6.67


def kld_kp_primary_404_is_reported_and_never_uses_dead_endpoint() -> None:
    # Matrix 10.
    source = Path(post_common.__file__).read_text(encoding="utf-8")
    assert "json/planetary_k_index.json" not in source
    assert air.KP_OBSERVED_3H_URL == "https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json"

    with _KpWorld(observed_http=_http_404()) as world:
        assert post_common._kp_global_swpc() == (None, "н/д", None, "kp:nodata(swpc/http-404)")
        assert world.http_urls == [air.KP_OBSERVED_3H_URL]
        # The air fallback asked for observed data only, never the 1-minute estimate.
        assert world.air_urls == [air.KP_OBSERVED_3H_URL]

    # Primary 404 but air's own observed fetch succeeds: provenance is kept, not "air.default".
    with _KpWorld(
        observed_http=_http_404(),
        responses={air.KP_OBSERVED_3H_URL: _kp_rows((_kp_tag(3), 7.0))},
    ):
        assert post_common._kp_global_swpc() == (7.0, "буря", 180, "air.swpc_observed_3h")


def kld_kp_malformed_json_and_wrong_schema_fail_closed() -> None:
    # Matrix 11.
    for body, expected_src in (
        (b"<html>404</html>", "kp:nodata(swpc/http-fail)"),
        ({"error": "unexpected"}, "kp:nodata(swpc/invalid-or-stale)"),
        ([], "kp:nodata(swpc/invalid-or-stale)"),
        ([{"time_tag": _kp_tag(3), "kp_index": 7}], "kp:nodata(swpc/invalid-or-stale)"),
        ([["time_tag", "a_running"], [_kp_tag(3), "7"]], "kp:nodata(swpc/invalid-or-stale)"),
        ([["time_tag", "Kp"], "not-a-row"], "kp:nodata(swpc/invalid-or-stale)"),
    ):
        with _KpWorld(observed_http=body):
            result = post_common._kp_global_swpc()
        assert result == (None, "н/д", None, expected_src), (body, result)
    for data in (None, "text", 7, {"Kp": 7}, [1, 2, 3]):
        assert air.parse_noaa_kp_observed_3h(data, now_ts=KP_NOW_TS) is None, data


def kld_kp_estimated_1m_is_never_a_confirmed_3h_observation() -> None:
    # Matrix 12.
    estimate = [
        {"time_tag": _kp_tag(0, minutes=-2), "kp_index": 0, "estimated_kp": 0.0},
        {"time_tag": _kp_tag(0, minutes=-1), "kp_index": 7, "estimated_kp": 7.33},
    ]
    obs = air.parse_noaa_kp_estimated_1m(estimate, now_ts=KP_NOW_TS)
    assert obs is not None and obs["kp"] == 7.33 and obs["src"] == "swpc_estimated_1m"
    # estimated_kp 0.0 is a value, not a reason to fall through to kp_index.
    zero = air.parse_noaa_kp_estimated_1m(estimate[:1], now_ts=KP_NOW_TS)
    assert zero is not None and zero["kp"] == 0.0
    assert air.parse_noaa_kp_estimated_1m(
        [{"time_tag": _kp_tag(0, minutes=-31), "estimated_kp": 7.0}], now_ts=KP_NOW_TS
    ) is None

    responses = {air.KP_OBSERVED_3H_URL: None, air.KP_ESTIMATED_1M_URL: estimate}
    with _KpWorld(observed_http=_http_404(), responses=responses):
        # The compatible public tuple still exposes the estimate, clearly labelled...
        kp, state, ts, src = air.get_kp()
        assert (kp, state, src) == (7.33, "буря", "swpc_estimated_1m")
        assert ts == KP_NOW_TS - 60
        # ...but it never becomes the confirmed observed-3h value the morning post shows.
        assert air.get_kp_observed_3h() is None
        assert post_common._kp_global_swpc() == (None, "н/д", None, "kp:nodata(swpc/http-404)")


def kld_kp_cache_keeps_provenance_and_freshness_bounds() -> None:
    # Matrix 13.
    responses = {air.KP_OBSERVED_3H_URL: _kp_rows((_kp_tag(3), 7.0))}
    with _KpWorld(responses=responses) as world:
        assert air.get_kp() == (7.0, "буря", KP_NOW_TS - 3 * 3600, "swpc_observed_3h")
        cached = json.loads(air.KP_CACHE.read_text(encoding="utf-8"))
        assert cached == {"schema": 2, "swpc_observed_3h": {"kp": 7.0, "ts": KP_NOW_TS - 3 * 3600}}

        world.responses = {}
        world.now_ts = KP_NOW_TS + 60 * 60
        obs = air.get_kp_observed_3h()
        assert obs is not None and obs["src"] == "cache:swpc_observed_3h"
        assert obs["kp"] == 7.0 and obs["age_min"] == 240  # true age of the observation
        world.observed_http = _http_404()
        assert post_common._kp_global_swpc() == (7.0, "буря", 240, "air.cache:swpc_observed_3h")
        assert air.get_kp()[3] == "cache:swpc_observed_3h"

        # Past the observed freshness bound the cache is not served.
        world.now_ts = KP_NOW_TS - 3 * 3600 + air.KP_OBSERVED_MAX_AGE_SEC + 60
        assert air.get_kp_observed_3h() is None
        assert air.get_kp() == (None, "н/д", None, "n/d")

    # A cached estimate stays an estimate and never satisfies the observed-only path.
    estimate = [{"time_tag": _kp_tag(0, minutes=-1), "estimated_kp": 5.67}]
    with _KpWorld(responses={air.KP_ESTIMATED_1M_URL: estimate}) as world:
        assert air.get_kp()[3] == "swpc_estimated_1m"
        world.responses = {}
        world.now_ts = KP_NOW_TS + 10 * 60
        assert air.get_kp()[3] == "cache:swpc_estimated_1m"
        assert air.get_kp_observed_3h() is None
        world.now_ts = KP_NOW_TS + 31 * 60
        assert air.get_kp() == (None, "н/д", None, "n/d")

    # Pre-provenance cache files are ignored rather than trusted.
    with _KpWorld():
        air.KP_CACHE.write_text(json.dumps({"kp": 1.0, "ts": KP_NOW_TS - 60, "src": "swpc_table"}), encoding="utf-8")
        assert air.get_kp() == (None, "н/д", None, "n/d")
        assert air.get_kp_observed_3h() is None



def kld_kp_primary_success_is_persisted_for_a_later_outage() -> None:
    # Morning primary request succeeds → the confirmed observation is cached.
    with _KpWorld(observed_http=_kp_rows((_kp_tag(6), 2.0), (_kp_tag(3), 7.0))) as world:
        assert post_common._kp_global_swpc() == (7.0, "буря", 180, "swpc_observed_3h")
        assert world.air_urls == []  # primary success needs no fallback fetch
        cached = json.loads(air.KP_CACHE.read_text(encoding="utf-8"))
        assert cached == {"schema": 2, "swpc_observed_3h": {"kp": 7.0, "ts": KP_NOW_TS - 3 * 3600}}

        # Later NOAA is fully down (primary 404, air re-fetch empty): the persisted
        # confirmed record is reused with its true age and explicit cache provenance.
        world.observed_http = _http_404()
        world.responses = {}
        world.now_ts = KP_NOW_TS + 60 * 60
        assert post_common._kp_global_swpc() == (7.0, "буря", 240, "air.cache:swpc_observed_3h")
        assert world.air_urls == [air.KP_OBSERVED_3H_URL]

        # Past the observed freshness bound the same record is rejected, and the
        # morning post keeps an explicit «н/д» instead of turning calm.
        world.now_ts = KP_NOW_TS - 3 * 3600 + air.KP_OBSERVED_MAX_AGE_SEC + 60
        kp, status, age, src = post_common._kp_global_swpc()
        assert (kp, status, age, src) == (None, "н/д", None, "kp:nodata(swpc/http-404)")
        chunk, fact = post_common._kp_morning_chunks(kp, status, age)
        assert (chunk, fact) == ("Кр н/д", "Кр н/д")
        text = build_morning_format_v2("Калининградская область", _legacy_with_space_line("🧲 Космопогода: " + chunk))
        assert "Космопогода" not in text and "спокойно" not in text.split("🏙")[0]


def kld_kp_primary_cache_write_never_promotes_estimates() -> None:
    # Only a confirmed live observed-3h record may be remembered.
    for refused in (
        None,
        {"kp": 7.0, "ts": KP_NOW_TS, "src": "swpc_estimated_1m"},
        {"kp": 7.0, "ts": KP_NOW_TS, "src": "cache:swpc_observed_3h"},
        {"kp": float("nan"), "ts": KP_NOW_TS, "src": "swpc_observed_3h"},
        {"kp": 7.0, "ts": "2026-10-10", "src": "swpc_observed_3h"},
        {"kp": 7.0, "ts": True, "src": "swpc_observed_3h"},
    ):
        with _KpWorld():
            assert air.remember_kp_observed_3h(refused) is False, refused
            assert not air.KP_CACHE.exists(), refused

    # Primary 404 + live 1-minute estimate: the estimate is cached as an estimate
    # only, the observed slot stays empty, and the morning value stays «н/д».
    estimate = [{"time_tag": _kp_tag(0, minutes=-1), "estimated_kp": 7.33}]
    with _KpWorld(observed_http=_http_404(), responses={air.KP_ESTIMATED_1M_URL: estimate}):
        assert air.get_kp()[3] == "swpc_estimated_1m"
        assert post_common._kp_global_swpc() == (None, "н/д", None, "kp:nodata(swpc/http-404)")
        cached = json.loads(air.KP_CACHE.read_text(encoding="utf-8"))
        assert "swpc_observed_3h" not in cached
        assert cached["swpc_estimated_1m"] == {"kp": 7.33, "ts": KP_NOW_TS - 60}


def kld_kp_cache_keeps_the_newest_observation_and_survives_write_failure() -> None:
    newer = {"kp": 7.0, "ts": KP_NOW_TS - 3 * 3600, "src": "swpc_observed_3h"}
    older = {"kp": 2.0, "ts": KP_NOW_TS - 6 * 3600, "src": "swpc_observed_3h"}
    with _KpWorld():
        assert air.remember_kp_observed_3h(newer) is True
        assert air.remember_kp_observed_3h(older) is True
        cached = json.loads(air.KP_CACHE.read_text(encoding="utf-8"))
        assert cached["swpc_observed_3h"] == {"kp": 7.0, "ts": KP_NOW_TS - 3 * 3600}

    # An unwritable cache never costs the morning its valid primary value.
    with _KpWorld(observed_http=_kp_rows((_kp_tag(3), 6.67))):
        air.KP_CACHE.mkdir()  # a directory in place of the file: write fails
        assert post_common._kp_global_swpc() == (6.67, "буря", 180, "swpc_observed_3h")


def kld_kp_public_signatures_are_unchanged() -> None:
    import inspect

    for function in (air.get_kp, post_common._kp_global_swpc, post_common._kp_from_swpc_http):
        assert not inspect.signature(function).parameters, function.__name__
    assert "tuple" in str(inspect.signature(air.get_kp).return_annotation).lower()
    assert "Tuple" in str(inspect.signature(post_common._kp_global_swpc).return_annotation)


def _legacy_with_space_line(space_line: str) -> str:
    lines = LEGACY_FIXTURE.splitlines()
    index = next(i for i, line in enumerate(lines) if line.startswith("🧲 Космопогода:"))
    lines[index] = space_line
    return "\n".join(lines)


def kld_kp_missing_data_is_never_rendered_as_calm() -> None:
    # Matrix 14.
    chunk, fact = post_common._kp_morning_chunks(None, "н/д", None)
    assert (chunk, fact) == ("Кр н/д", "Кр н/д")
    text = build_morning_format_v2("Калининградская область", _legacy_with_space_line("🧲 Космопогода: " + chunk))
    assert "🧲" not in text
    assert "Космопогода" not in text
    assert "🏙 Калининград" in text


def kld_kp_morning_formatting_stays_valid_for_observed_values() -> None:
    # Matrix 15: real values flow through the unchanged morning FORMAT_V2.
    chunk, fact = post_common._kp_morning_chunks(0.0, "умеренно", 180)
    assert (chunk, fact) == ("Кр 0.0 (умеренно, 🕓 180 мин назад)", "Кр 0.0")
    calm = build_morning_format_v2("Калининградская область", _legacy_with_space_line("🧲 Космопогода: " + chunk))
    assert "🧲 Космопогода: спокойно, Kp 0.0." in calm

    chunk, fact = post_common._kp_morning_chunks(7.0, post_common._kp_status_by_value(7.0), 240)
    assert (chunk, fact) == ("Кр 7.0 (буря, 🕓 4ч назад)", "Кр 7.0")
    storm = build_morning_format_v2("Калининградская область", _legacy_with_space_line("🧲 Космопогода: " + chunk))
    assert "🧲 Космопогода: возмущённо, Kp 7.0." in storm
    assert "спокойно, Kp 7.0" not in storm
    for text in (calm, storm):
        assert "🏙 Калининград" in text
        assert "💱 Курсы" in text
        assert "🌇 Закат сегодня: 21:32" in text


def _kld_peak_test_rows(values):
    moment = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
    return [{"time_tag": (moment - timedelta(hours=(8 - i) * 3)).strftime("%Y-%m-%dT%H:%M:%S"),
             "Kp": value} for i, value in enumerate(values)]


def _kld_peak_test_line(rows, *, error=None):
    from geomagnetic_noaa_transport import NOAAResponse, NOAA_OBSERVED_3H_URL

    now = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
    def fake_transport(url, **_kwargs):
        assert url == NOAA_OBSERVED_3H_URL
        if error is not None:
            raise error
        return NOAAResponse(status=200, final_url=url, body=json.dumps(rows).encode("utf-8"))

    return post_common._kld_geomag_24h_peak_line(clock=lambda: now, transport=fake_transport)


def kld_peak_24h_keeps_g3_after_current_calm() -> None:
    rows = _kld_peak_test_rows([1.0, 2.0, 7.33, 2.0, 1.0, 1.0, 1.0, 1.0])
    peak = _kld_peak_test_line(rows)
    assert "7.33 (G3)" in peak and "COMPLETE (8/8)" in peak and "UTC" in peak
    fixture = _legacy_with_space_line("🧲 Космопогода: Кр 1.0 (умеренно)")
    fixture = fixture.replace("🧲 Космопогода: Кр 1.0 (умеренно)",
                              "🧲 Космопогода: Кр 1.0 (умеренно)\n" + peak)
    v2 = build_morning_format_v2("Калининградская область", fixture)
    final = _apply_format_v2_safe_postprocess(v2, fixture, fixture, "morning")
    assert final.count("🛰 Пик Kp за 24 ч:") == 1
    assert "7.33 (G3)" in final and "Kp 1.0" in final
    assert "Kp 7.3." not in final


def kld_peak_24h_partial_has_explicit_uncertainty() -> None:
    line = _kld_peak_test_line(_kld_peak_test_rows([1.0, 6.67, 1.0, 1.0, 1.0, 1.0, 1.0]))
    assert "6.67 (G2)" in line and "PARTIAL (7/8)" in line
    assert "максимум среди доступных" in line


def kld_peak_24h_unknown_and_stale_are_not_calm() -> None:
    for rows in ([], [{"time_tag": "2026-10-08T00:00:00", "Kp": 2.0}]):
        line = _kld_peak_test_line(rows)
        assert "н/д" in line and "UNKNOWN" in line
        assert "спокойно" not in line and "ниже G1" not in line


def kld_peak_24h_conflicts_do_not_invent_severity() -> None:
    rows = _kld_peak_test_rows([1.0] * 8)
    rows += [{**rows[3], "Kp": 8.99}]
    line = _kld_peak_test_line(rows)
    assert "PARTIAL (7/8)" in line and "1.0 (ниже G1)" in line
    assert "G4" not in line


def kld_peak_24h_noaa_failure_fails_closed() -> None:
    line = _kld_peak_test_line([], error=OSError("offline NOAA unavailable"))
    assert "UNKNOWN" in line and "н/д" in line and "спокойно" not in line


def kld_peak_24h_g_boundaries_are_unrounded() -> None:
    for value, grade in [(4.99, "ниже G1"), (5.0, "G1"), (6.0, "G2"),
                         (7.0, "G3"), (8.0, "G4"), (9.0, "G5")]:
        line = _kld_peak_test_line(_kld_peak_test_rows([value] + [1.0] * 7))
        assert f"{value} ({grade})" in line, line


def kld_peak_24h_missing_weather_keeps_observed_peak() -> None:
    peak = _kld_peak_test_line(_kld_peak_test_rows([1.0, 7.33] + [1.0] * 6))
    assert "7.33 (G3)" in peak and "COMPLETE (8/8)" in peak
    source = MISSING_CORE_REAL_FIXTURE.replace("🏭 Воздух:", peak + "\n🏭 Воздух:", 1)
    legacy = sanitize_post_text(source).text
    v2 = build_morning_format_v2("Калининградская область", legacy)
    assert v2.count("🛰 Пик Kp за 24 ч:") == 1
    final = _apply_format_v2_safe_postprocess(v2, source, legacy, "morning")
    final = sanitize_post_text(final).text
    final = _finalize_kld_morning_safe_text(final, source, legacy, "morning")
    assert final.count("🛰 Пик Kp за 24 ч:") == 1
    assert "7.33 (G3)" in final and "COMPLETE (8/8)" in final
    assert "⚠️ Данные по Калининграду обновились не полностью" in final
    assert "✅ План: перед выходом проверьте актуальный прогноз" in final
    assert "🧲 Космопогода:" not in final  # Do not invent a current Kp.
    assert "🏙 Калининград —" not in final and "VayboMeter: 8.6" not in final
    assert final.splitlines()[-1] == "#Калининград #погода #здоровье #сегодня #море"


def kld_peak_24h_missing_weather_keeps_partial_and_unknown() -> None:
    partial = _kld_peak_test_line(_kld_peak_test_rows([1.0, 6.67] + [1.0] * 5))
    unknown = _kld_peak_test_line([], error=OSError("offline NOAA unavailable"))
    for peak in (partial, unknown):
        source = MISSING_CORE_REAL_FIXTURE.replace("🏭 Воздух:", peak + "\n🏭 Воздух:", 1)
        v2 = build_morning_format_v2("Калининградская область", source)
        final = _apply_format_v2_safe_postprocess(v2, source, source, "morning")
        assert final.count("🛰 Пик Kp за 24 ч:") == 1
        assert peak in final
        assert "⚠️ Данные по Калининграду обновились не полностью" in final
        assert "спокойно" not in final.lower()
    assert "PARTIAL (7/8)" in partial and "максимум среди доступных" in partial
    assert "UNKNOWN" in unknown and "н/д" in unknown


def main() -> None:
    checks = (
        kld_morning_has_sunset,
        kld_morning_keeps_safecast,
        kld_morning_shows_complete_private_sensor_only,
        kld_morning_keeps_fx_uv_air_plan,
        kld_morning_fx_cleaner_is_idempotent,
        kld_morning_safecast_above_observation_is_soft,
        kld_morning_has_only_one_plan,
        kld_morning_astro_block_has_sun_and_rhythm_title,
        kld_morning_astro_block_has_moon_and_plus,
        kld_morning_preserves_quake_line,
        kld_morning_cosmoweather_is_compact,
        kld_morning_hot_day_uses_cyprus_style_skeleton,
        kld_morning_mild_uv_avoids_heat_wording_and_keeps_details,
        kld_morning_omits_baltic_when_sea_missing,
        kld_morning_omits_region_context_when_only_kaliningrad,
        kld_morning_missing_core_weather_fails_closed,
        kld_morning_postprocess_does_not_reintroduce_duplicates,
        kld_morning_real_safe_pipeline_uses_raw_context,
        kld_morning_real_safe_pipeline_handles_warm_uv_without_heat_wording,
        kld_morning_final_sensor_wording_is_softened,
        kld_morning_final_production_path_softens_sensor_after_sanitize,
        kld_morning_rainy_gusty_integrated_fixture_is_not_too_optimistic,
        kld_morning_storm_warning_outranks_generic_windy_nuance,
        kld_morning_region_contrast_uses_all_city_row_variants,
        kld_morning_region_context_is_omitted_for_one_city,
        kld_morning_equal_daytime_shows_only_coldest_night,
        kld_morning_structured_current_run_temperatures_survive_safe_pipeline,
        kld_morning_structured_daytime_only_uses_short_form,
        kld_morning_collector_uses_current_run_city_lists,
        kld_morning_astro_weather_words_do_not_change_factual_decision,
        kld_morning_hot_windy_without_uv_does_not_recommend_layer,
        kld_morning_weak_wind_and_gust_are_not_promoted,
        kld_morning_extreme_heat_without_uv_keeps_heat_but_no_uv_claims,
        kld_morning_valid_uv_with_weak_wind_does_not_invent_wind,
        kld_morning_production_3ms_gust6_keeps_editorial_layers_consistent,
        kld_recommendation_control_token_is_removed_from_today_line,
        kld_recommendation_control_token_is_removed_from_terminal_plan,
        kld_legitimate_recommendation_line_is_unchanged,
        kld_control_only_recommendation_line_is_dropped,
        kld_morning_smart_plan_replaces_legacy_wellness_plan,
        kld_morning_astro_cleanup_drops_long_synthetic_ellipsis_line,
        kld_morning_generic_source_nuance_is_omitted,
        kld_morning_source_nuance_that_only_restates_score_is_omitted,
        kld_morning_supported_specific_source_nuance_is_preserved,
        kld_morning_calm_without_source_score_does_not_repeat_label,
        kld_workflow_morning_schedule_is_earlier,
        kld_morning_astro_block_has_sunset_if_available,
        kld_evening_astro_block_has_tomorrow_wording,
        kld_astro_block_stays_compact,
        kld_daily_keeps_weather_blocks,
        kld_kp_observed_values_are_exact_and_unrounded,
        kld_kp_rejects_non_finite_and_out_of_range_values,
        kld_kp_rejects_missing_or_invalid_timestamps_without_substituting_now,
        kld_kp_future_timestamp_outside_tolerance_is_rejected,
        kld_kp_stale_observation_is_rejected,
        kld_kp_unsorted_rows_pick_the_newest_timestamp,
        kld_kp_primary_404_is_reported_and_never_uses_dead_endpoint,
        kld_kp_malformed_json_and_wrong_schema_fail_closed,
        kld_kp_estimated_1m_is_never_a_confirmed_3h_observation,
        kld_kp_cache_keeps_provenance_and_freshness_bounds,
        kld_kp_missing_data_is_never_rendered_as_calm,
        kld_kp_morning_formatting_stays_valid_for_observed_values,
        kld_kp_primary_success_is_persisted_for_a_later_outage,
        kld_kp_primary_cache_write_never_promotes_estimates,
        kld_kp_cache_keeps_the_newest_observation_and_survives_write_failure,
        kld_kp_public_signatures_are_unchanged,
        kld_peak_24h_keeps_g3_after_current_calm,
        kld_peak_24h_partial_has_explicit_uncertainty,
        kld_peak_24h_unknown_and_stale_are_not_calm,
        kld_peak_24h_conflicts_do_not_invent_severity,
        kld_peak_24h_noaa_failure_fails_closed,
        kld_peak_24h_g_boundaries_are_unrounded,
        kld_peak_24h_missing_weather_keeps_observed_peak,
        kld_peak_24h_missing_weather_keeps_partial_and_unknown,
    )
    for check in checks:
        check()
        print(f"PASS {check.__name__}")
    print(f"OK: {len(checks)} KLD morning FORMAT_V2 regression checks passed")


if __name__ == "__main__":
    main()
