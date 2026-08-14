#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regression checks for compact KLD FORMAT_V2 evening posts."""
from __future__ import annotations

import importlib
import os
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import format_v2  # noqa: E402
from format_v2 import build_evening_format_v2  # noqa: E402

pendulum_stub = types.ModuleType("pendulum")
pendulum_stub.DateTime = object
sys.modules.setdefault("pendulum", pendulum_stub)

telegram_stub = types.ModuleType("telegram")
telegram_stub.Bot = object
telegram_stub.constants = types.SimpleNamespace(ParseMode=types.SimpleNamespace(HTML="HTML"))
sys.modules.setdefault("telegram", telegram_stub)

os.environ.setdefault("TELEGRAM_TOKEN_KLG", "test-token")
os.environ.setdefault("CHANNEL_ID_KLG", "test-channel")

import safe_test_post  # noqa: E402
from safe_test_post import (  # noqa: E402
    _apply_format_v2_safe_postprocess,
    _has_confirmed_storm_word,
)
import post_kld  # noqa: E402
from post_kld import _extract_storm_warning  # noqa: E402
import weather_text  # noqa: E402
import kld_informative_cover  # noqa: E402


NORMAL_EVENING = """<b>🌅 Калининградская область: погода на завтра (20.06.2026)</b>
✨ VayboMeter завтра: 8.1/10 — хороший день; берег свежее, восток теплее.
Погода: 🏙️ Калининград — 21/13 °C • облачно • 💨 4.0 м/с • 🔹 1014 гПа.
🌊 <b>Морские города</b>
Светлогорск: 18/13 °C • облачно • 🌊 6 м/с
Зеленоградск: 19/13 °C • облачно • 🌊 5 м/с
Балтийск: 17/12 °C • облачно • 🌊 7 м/с • 0.3 м
———
🌡 <b>Тёплые города</b>
• Черняховск: 24/12 °C • облачно
• Гусев: 23/11 °C • облачно
• Советск: 22/11 °C • облачно
• Полесск: 21/10 °C • облачно
❄️ <b>Холодные города</b>
• Балтийск: 17/12 °C • облачно
• Светлогорск: 18/13 °C • облачно
• Зеленоградск: 19/13 °C • облачно
• Янтарный: 19/12 °C • облачно
🌅 Рассвет завтра: 04:08
🌇 Закат завтра: 21:33
📻 <b>Астрособытия</b>
🌙 🌒 Растущий серп (34%) • ♎ Весы
💚 В плюсе: баланс, прогулки, договорённости.
🌙 В этот период лучше не перегружать вечер.
⚫️ VoC 12:00–13:20 — без новых стартов.
🌍 Сейсмика 24ч: M2.3, 5 км от Калининграда, глубина 8 км, 12:30.
#Калининград #погода #здоровье #море
"""


RAIN_EVENING = """<b>🌅 Калининградская область: погода на завтра (20.06.2026)</b>
✨ VayboMeter завтра: 5.8/10 — с оговорками; дождь, порывы у моря.
⚠️ <b>Штормовое предупреждение</b>: местами дождь и порывы до 16 м/с.
Погода: 🏙️ Калининград — 20/17 °C • 🌧 дождь • 💨 8 м/с • порывы до 16 м/с.
🌊 <b>Морские города</b>
Балтийск: 20/17 °C • 🌧 дождь • 💨 9 м/с • порывы до 16 м/с • 🌊 12 • 0.9 м
🧜‍♂️ Отлично: SUP (W/None) • гидрокостюм 5/4 мм
Зеленоградск: 14/9 °C • 🌧 дождь • 💨 8 м/с • порывы до 15 м/с • 🌊 12 • 0.8 м
🧜‍♂️ Отлично: SUP (W/None) • гидрокостюм шорти 2 мм
———
🌡 <b>Тёплые города</b>
• Черняховск: 17/10 °C • 🌧 дождь
❄️ <b>Холодные города</b>
• Светлогорск: 13/9 °C • 🌧 дождь
🌇 Закат завтра: 21:33
📻 <b>Астрособытия</b>
🌙 🌒 Растущий серп (34%) • ♎ Весы
💚 В плюсе: спокойный темп.
#Калининград #погода #здоровье #море
"""


WARM_UNCERTAIN_EVENING = """<b>🌅 Калининградская область: погода на завтра (20.06.2026)</b>
✨ VayboMeter завтра: 6.6/10 — рабочий день; местами осадки.
Погода: 🏙️ Калининград — 26/18 °C • 🌦 местами дождь • 💨 5 м/с.
🌊 <b>Морские города</b>
Зеленоградск: 24/18 °C • 🌦 местами дождь
———
🌡 <b>Тёплые города</b>
• Черняховск: 26/17 °C • 🌦 местами дождь
🌅 Рассвет завтра: 04:08
🌇 Закат завтра: 21:33
📻 <b>Астрособытия</b>
🌙 🌒 Растущий серп (34%) • ♎ Весы
💚 В плюсе: спокойный темп.
#Калининград #погода #здоровье #море
"""


STORM_REAL_EVENING = """<b>🌅 Калининградская область: погода на завтра (03.07.2026)</b>
✨ VayboMeter завтра: 5.7/10 — с оговорками; предупреждение, осадки, сильные порывы.
⚠️ Предупреждение
⚠️ Штормовое: порывы до 19 м/с
Погода: 🏙️ Калининград — 21/16 °C • облачно • 💨 6.9 м/с • порывы до 19 м/с • давл. 1014 гПа.
🌊 <b>Морские города</b>
Балтийск: 21/16 °C • облачно • 💨 6.9 м/с • порывы до 19 м/с • 🌊 21 • волна 1.3 м
🏄 Отлично: Сёрф (западный ветер, вдоль берега)
🧜‍♂️ Отлично: SUP (W/None) • короткий гидрокостюм 2 мм
Зеленоградск: 21/16 °C • облачно • 💨 6.9 м/с • порывы до 19 м/с • 🌊 23 • волна 2.0 м
———
🌡 <b>Тёплые города</b>
• Черняховск: 22/16 °C • местами дождь
❄️ <b>Холодные города</b>
• Балтийск: 21/16 °C • облачно
🌇 Закат завтра: 21:33
📻 <b>Астрособытия</b>
🌙 Убывающая Луна, ♐ (92%)
✨ 92% освещённости — эмоции ярче обычного.
⚠️ Общий фон: неблагоприятный день.
💚 В плюсе: планы, обучение.
⚫ VoC: 17:15–00:00.
#Калининград #погода #здоровье #море
"""


MODERATE_SURF_EVENING = """<b>🌅 Калининградская область: погода на завтра (03.07.2026)</b>
✨ VayboMeter завтра: 7.2/10 — рабочий день; волна у моря.
Погода: 🏙️ Калининград — 22/16 °C • облачно • 💨 4 м/с • порывы до 7 м/с.
🌊 <b>Морские города</b>
Балтийск: 21/16 °C • облачно • 💨 5 м/с • порывы до 7 м/с • 🌊 21 • волна 1.1 м
🏄 Отлично: Сёрф (волна 1.1 м)
———
🌇 Закат завтра: 21:33
📻 <b>Астрособытия</b>
🌙 Растущая Луна, ♐ (72%)
💚 В плюсе: планы.
#Калининград #погода #здоровье #море
"""


NONSTORM_WARNING_EVENING = """<b>🌅 Калининградская область: погода на завтра (03.07.2026)</b>
✨ VayboMeter завтра: 6.8/10 — с оговорками; ветер у моря.
⚠️ Предупреждение: высокий УФ.
Погода: 🏙️ Калининград — 24/16 °C • ясно • 💨 6 м/с • порывы до 10 м/с.
🌊 <b>Морские города</b>
Балтийск: 22/16 °C • ясно • 💨 6 м/с • порывы до 10 м/с • 🌊 21 • волна 0.4 м
———
🌇 Закат завтра: 21:33
📻 <b>Астрособытия</b>
🌙 Растущая Луна, ♐ (72%)
⚠️ Общий фон: неблагоприятный день.
💚 В плюсе: планы.
#Калининград #погода #здоровье #море
"""


RAIN_WARNING_NO_STORM_EVENING = """<b>🌅 Калининградская область: погода на завтра (03.07.2026)</b>
✨ VayboMeter завтра: 6.4/10 — с оговорками; местами дождь.
⚠️ Предупреждение: местами дождь.
Погода: 🏙️ Калининград — 22/16 °C • 🌦 местами дождь • 💨 5 м/с • порывы до 9 м/с.
🌊 <b>Морские города</b>
Балтийск: 21/16 °C • облачно • 💨 5 м/с • порывы до 9 м/с • 🌊 21 • волна 0.5 м
———
🌇 Закат завтра: 21:33
📻 <b>Астрособытия</b>
🌙 Растущая Луна, ♐ (72%)
💚 В плюсе: планы.
#Калининград #погода #здоровье #море
"""


GUST19_NO_STORM_WORD_EVENING = """<b>🌅 Калининградская область: погода на завтра (03.07.2026)</b>
✨ VayboMeter завтра: 5.9/10 — с оговорками; сильные порывы.
⚠️ Предупреждение: порывы до 19 м/с.
Погода: 🏙️ Калининград — 21/16 °C • облачно • 💨 6.9 м/с • порывы до 19 м/с.
🌊 <b>Морские города</b>
Балтийск: 21/16 °C • облачно • 💨 6.9 м/с • порывы до 19 м/с • 🌊 21 • волна 1.3 м
🏄 Отлично: Сёрф (волна 1.3 м)
———
🌇 Закат завтра: 21:33
📻 <b>Астрособытия</b>
🌙 Убывающая Луна, ♐ (92%)
💚 В плюсе: планы.
#Калининград #погода #здоровье #море
"""


LOW_GUST_STORM_WORD_EVENING = """<b>🌅 Калининградская область: погода на завтра (03.07.2026)</b>
✨ VayboMeter завтра: 7.0/10 — рабочий день; риск шторма невысок.
⚠️ Предупреждение: риск шторма невысок, порывы до 9 м/с.
Погода: 🏙️ Калининград — 22/16 °C • облачно • 💨 5 м/с • порывы до 9 м/с.
🌊 <b>Морские города</b>
Балтийск: 21/16 °C • облачно • 💨 5 м/с • порывы до 9 м/с • 🌊 21 • волна 0.4 м
———
🌇 Закат завтра: 21:33
📻 <b>Астрособытия</b>
🌙 Растущая Луна, ♐ (72%)
💚 В плюсе: планы.
#Калининград #погода #здоровье #море
"""


GUST14_NO_STORM_WORD_EVENING = """<b>🌅 Калининградская область: погода на завтра (22.07.2026)</b>
✨ VayboMeter завтра: 6.9/10 — рабочий день; порывистый ветер у моря.
Погода: 🏙️ Калининград — 23/17 °C • облачно • 💨 8 м/с • порывы до 14 м/с.
🌊 <b>Морские города</b>
Балтийск: 22/17 °C • облачно • 💨 8 м/с • порывы до 14 м/с • 🌊 19 • волна 1.0 м
———
🌇 Закат завтра: 21:20
📻 <b>Астрособытия</b>
🌙 Растущая Луна, ♐ (72%)
💚 В плюсе: планы.
#Калининград #погода #здоровье #море
"""


def _gust_no_storm_word_evening(gust_ms: int) -> str:
    return f"""<b>🌅 Калининградская область: погода на завтра (22.07.2026)</b>
✨ VayboMeter завтра: 6.9/10 — рабочий день; порывистый ветер у моря.
Погода: 🏙️ Калининград — 23/17 °C • облачно • 💨 8 м/с • порывы до {gust_ms} м/с.
🌊 <b>Морские города</b>
Балтийск: 22/17 °C • облачно • 💨 8 м/с • порывы до {gust_ms} м/с • 🌊 19 • волна 1.0 м
———
🌇 Закат завтра: 21:20
📻 <b>Астрособытия</b>
🌙 Растущая Луна, ♐ (72%)
💚 В плюсе: планы.
#Калининград #погода #здоровье #море
"""


FULL_ASTRO_EVENING = """<b>🌅 Калининградская область: погода на завтра (03.07.2026)</b>
✨ VayboMeter завтра: 7.1/10 — рабочий день; у моря ветер.
Погода: 🏙️ Калининград — 22/16 °C • облачно • 💨 4 м/с • порывы до 8 м/с.
🌊 <b>Морские города</b>
Балтийск: 21/16 °C • облачно • 💨 4 м/с • порывы до 8 м/с • 🌊 21 • волна 0.4 м
———
🌅 Рассвет завтра: 04:09
🌇 Закат завтра: 21:33
📻 <b>Астрособытия</b>
🌙 Убывающая Луна, ♐ (92%)
✨ 92% освещённости — эмоции ярче обычного.
⚠️ Общий фон: неблагоприятный день.
💚 В плюсе: планы, обучение.
⚫ VoC: 17:15–00:00.
🌙 В этот период лучше не перегружать вечер.
#Калининград #погода #здоровье #море
"""


def kld_evening_normal_no_generic_confidence() -> None:
    text = build_evening_format_v2("Калининградская область", NORMAL_EVENING)
    assert "🎯 <b>Уверенность прогноза</b>" not in text
    assert "🎯 Уверенность:" not in text


def kld_evening_normal_no_sea_correction() -> None:
    text = build_evening_format_v2("Калининградская область", NORMAL_EVENING)
    assert "🌊 <b>Морская поправка</b>" not in text


def kld_evening_no_old_conclusion_or_recommendations() -> None:
    text = build_evening_format_v2("Калининградская область", NORMAL_EVENING)
    assert "📌 <b>Вывод</b>" not in text
    assert "✅ <b>Рекомендации</b>" not in text


def kld_evening_has_one_final_plan() -> None:
    text = build_evening_format_v2("Калининградская область", NORMAL_EVENING)
    assert text.count("✅ План завтра:") == 1


def kld_evening_preserves_city_and_marine_lines() -> None:
    text = build_evening_format_v2("Калининградская область", NORMAL_EVENING)
    assert "🏙 Калининград — 21/13 °C" in text
    assert "🌊 <b>Морские города</b>" in text
    assert "Светлогорск: 18/13 °C" in text
    assert "Балтийск: 17/12 °C" in text


def kld_evening_preserves_compact_astro() -> None:
    text = build_evening_format_v2("Калининградская область", NORMAL_EVENING)
    lines = text.splitlines()
    start = lines.index("🌇 <b>Солнце, Луна и ритм завтрашнего дня</b>")
    block = [line for line in lines[start:start + 6] if line.strip()]
    assert "🌅 Рассвет завтра: 04:08" in block
    assert "🌇 Закат завтра: 21:33" in block
    assert "🌒 Растущий серп в ♎ Весы — 34% освещённости." in block
    assert "⚫️ VoC 12:00–13:20 — без новых стартов." in block
    assert len(block) <= 6


def kld_evening_preserves_quake_line() -> None:
    text = build_evening_format_v2("Калининградская область", NORMAL_EVENING)
    assert "🌍 Сейсмика 24ч:" in text


def kld_evening_uncertain_has_short_confidence_line() -> None:
    text = build_evening_format_v2("Калининградская область", RAIN_EVENING)
    assert "🎯 Уверенность: по температуре спокойно; осадки и условия у воды лучше проверить утром." in text
    assert "температура высокая" not in text
    assert "🎯 <b>Уверенность прогноза</b>" not in text
    legacy = text.replace(
        "🎯 Уверенность: по температуре спокойно; осадки и условия у воды лучше проверить утром.",
        "🎯 Уверенность: температура высокая; ветер/осадки лучше проверить утром.",
    )
    polished = _apply_format_v2_safe_postprocess(legacy, "", "", "evening")
    assert "🎯 Уверенность: по температуре спокойно; осадки и условия у воды лучше проверить утром." in polished
    assert "температура высокая" not in polished


def kld_evening_warm_uncertain_confidence_is_not_high() -> None:
    text = build_evening_format_v2("Калининградская область", WARM_UNCERTAIN_EVENING)
    assert "температура высокая" not in text
    assert "🎯 Уверенность: температура тёплая; осадки и условия у воды лучше проверить утром." in text
    assert text.splitlines()[-1] == "#Калининград #погода #здоровье #море"
    assert "🌒 Растущий серп" in text


def kld_evening_sup_guidance_is_common_block() -> None:
    text = build_evening_format_v2("Калининградская область", RAIN_EVENING)
    assert text.count("SUP: на открытой воде не рекомендован") == 1
    assert "🏄 Сёрф: только опытным; волна 0.8–0.9 м, но выход только после проверки конкретного спота и предупреждений." in text
    assert "после фактической проверки условий" in text
    assert "🧜‍♂️ Отлично: SUP" not in text
    assert "Отлично:" not in text
    assert "шорти 2 мм" not in text
    assert "Балтийск: 20/17 °C • 🌧 дождь • 💨 9 м/с • порывы до 16 м/с • 🌊 12°C • волна 0.9 м" in text
    assert "Зеленоградск: 14/9 °C • 🌧 дождь • 💨 8 м/с • порывы до 15 м/с • 🌊 12°C • волна 0.8 м" in text


def kld_evening_title_is_compact() -> None:
    text = build_evening_format_v2("Калининградская область", NORMAL_EVENING)
    assert text.splitlines()[0] == "<b>🌅 Калининградская область завтра (20.06.2026)</b>"


HEAT_GUST_EVENING = """<b>🌅 Калининградская область: погода на завтра (20.06.2026)</b>
✨ VayboMeter завтра: 9.0/10 — отлично для прогулок.
Погода: 🏙️ Калининград — 39/23 °C • ясно • 💨 6 м/с • порывы до 12 м/с • 💧 1014 гПа ↓.
🌊 <b>Морские города</b>
Балтийск: 31/21 °C • ясно • 💨 7 м/с • порывы до 12 м/с • 🌊 18 • 0.5 м
Зеленоградск: 33/22 °C • ясно • 💨 6 м/с • порывы до 11 м/с • 🌊 19 • 0.4 м
———
🌡 <b>Тёплые города</b>
• Черняховск: 39/24 °C • ясно
• Гусев: 38/23 °C • ясно
❄️ <b>Холодные города</b>
• Неман: 35/17 °C • ясно
• Балтийск: 31/21 °C • ясно
🌅 Рассвет завтра: 04:08
🌇 Закат завтра: 21:33
📻 <b>Астрособытия</b>
🌙 🟡 Полнолуние, ♐ (96%)
💚 В плюсе: планы, обучение.
#Калининград #погода #здоровье #море
⚠️ Предупреждение: жара и порывы у моря требуют осторожности.
"""


def kld_evening_heat_gust_polish() -> None:
    text = build_evening_format_v2("Калининградская область", HEAT_GUST_EVENING)
    lines = text.splitlines()
    tag_index = lines.index("#Калининград #погода #здоровье #море")
    assert tag_index == len(lines) - 1
    assert not any(line.startswith("⚠️") for line in lines[tag_index + 1:])
    assert "отлично" not in text.lower()
    assert "жарко; у моря порывы" in text or "днём жара, у воды — ветер" in text
    assert "✅ План завтра: основные дела утром/вечером" in text
    assert "🌙 🟡" not in text
    assert "🌕 Почти полная Луна в ♐ — 96% освещённости." in text
    assert "❄️ <b>Холодные города</b>" not in text
    assert "❄️ <b>Самые прохладные ночи</b>" in text
    assert "Неман: 35/17 °C" in text
    assert "💧 1014 гПа" not in text
    assert "давл. 1014 гПа" in text


def kld_evening_storm_sport_voc_and_background_polish() -> None:
    text = build_evening_format_v2("Калининградская область", STORM_REAL_EVENING)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    score_line = next(line for line in lines if line.startswith("✨ VayboMeter"))
    assert "5.7/10" in score_line
    assert "предупреждение" not in score_line.lower()
    assert "неустойчивый день: локальные осадки и штормовые порывы" in score_line
    assert "с оговорками" not in score_line
    assert "🧭 Главное завтра: неустойчивое погодное окно; береговые планы лучше держать гибкими." in text
    assert "⚠️ Главный нюанс: на открытом берегу и пирсах порывы до 19 м/с." in text
    assert text.count("Штормовое предупреждение") == 0
    assert "⚠️ <b>Предупреждение</b>" not in text
    assert "⚠️ Предупреждение\n" not in text
    assert "Отлично" not in text
    assert "SUP: на открытой воде не рекомендован" in text
    assert "Сёрф: только опытным" in text
    assert "проверки конкретного спота" in text
    assert "короткий гидрокостюм 2 мм" not in text
    assert "⚫️ VoC: 03.07 17:15–04.07 00:00." in text
    assert "⚠️ Общий фон: неблагоприятный день." in text
    assert lines[-1] == "#Калининград #погода #здоровье #море"


def kld_evening_moderate_surf_keeps_cautious_positive() -> None:
    text = build_evening_format_v2("Калининградская область", MODERATE_SURF_EVENING)
    assert "Отлично: Сёрф" not in text
    assert "🏄 Сёрф: есть рабочие окна по волне; проверить конкретный спот." in text
    assert "SUP: на открытой воде не рекомендован" not in text
    assert text.splitlines()[-1] == "#Калининград #погода #здоровье #море"


def kld_evening_generic_warning_is_not_storm() -> None:
    text = build_evening_format_v2("Калининградская область", NONSTORM_WARNING_EVENING)
    assert "штормовые порывы" not in text
    assert "Штормовое предупреждение" not in text
    assert "🧭 Главное завтра: у воды главный фактор — ветер и порывы." in text
    assert _extract_storm_warning(NONSTORM_WARNING_EVENING) is None
    assert _extract_storm_warning("⚠️ Нюанс: у воды ветер ощущается сильнее\nПогода: 22/16 °C • порывы до 10 м/с") is None


def kld_evening_rain_warning_is_not_storm() -> None:
    text = build_evening_format_v2("Калининградская область", RAIN_WARNING_NO_STORM_EVENING)
    assert "штормовые порывы" not in text
    assert "Штормовое предупреждение" not in text
    assert "🧭 Главное завтра: влажный и ветреный день, особенно заметный на побережье." in text
    assert _extract_storm_warning(RAIN_WARNING_NO_STORM_EVENING) is None


def kld_evening_safe_postprocess_does_not_promote_wind_nuance_to_storm() -> None:
    # Regression: safe_test_post._finalize_kld_evening_safe_text promoted any
    # "⚠️"-prefixed line containing the word "порыв" to "⚠️ Штормовое
    # предупреждение: ...", which fired on ordinary wind/rain nuance lines
    # (e.g. "на побережье ощущение меняют порывы") at 7-9 м/с — this was the
    # actual source of the storm badge appearing on weak-wind days in
    # production, upstream of build_evening_format_v2 itself.
    text = build_evening_format_v2("Калининградская область", RAIN_WARNING_NO_STORM_EVENING)
    assert "⚠️ Нюанс: у моря дождь и порывы ощущаются резче, чем в городе." in text
    polished = _apply_format_v2_safe_postprocess(text, "", "", "evening")
    assert "Штормовое предупреждение" not in polished
    assert "⚠️ Нюанс: у моря дождь и порывы ощущаются резче, чем в городе." in polished


def kld_has_confirmed_storm_word_ignores_negation_from_a_different_line() -> None:
    # Regression: _has_confirmed_storm_word used to check the whole text at once
    # (word present AND no negation anywhere), so a negation in one line ("риск
    # шторма в другой части области невысок") could cancel a real confirmation
    # in a different line ("Штормовое предупреждение: порывы до 18 м/с.").
    text = (
        "Штормовое предупреждение: порывы до 18 м/с.\n"
        "Риск шторма в другой части области невысок."
    )
    assert _has_confirmed_storm_word(text) is True


def kld_has_confirmed_storm_word_all_lines_negated_is_false() -> None:
    text = "Риск шторма невысок.\nШторма не будет."
    assert _has_confirmed_storm_word(text) is False


def kld_has_confirmed_storm_word_confirmed_line_order_independent() -> None:
    text = (
        "Шторма не будет.\n"
        "Штормовое предупреждение: порывы до 18 м/с."
    )
    assert _has_confirmed_storm_word(text) is True


def kld_storm_negation_handles_modifiers_and_clauses() -> None:
    # Regression: storm negation required the negation/risk-assessment to sit
    # immediately after "шторм" (no filler words) and only worked at line
    # granularity, so hedged phrasings with modifiers ("Шторма точно не
    # будет.") or a negation and a confirmation sharing one line via ", но"
    # were misclassified. weather_text.has_confirmed_storm_word is the single
    # shared contract used by format_v2.py, safe_test_post.py and post_kld.py.
    cases = [
        ("Риск шторма в другой части области невысок.", False),
        ("Шторма точно не будет.", False),
        ("Шторм, скорее всего, не ожидается.", False),
        ("Шторма не будет утром. Вечером ожидается шторм.", True),
        ("Риск шторма невысок, но штормовое предупреждение действует у моря.", True),
        ("Штормовое предупреждение отменено.", False),
    ]
    for text, expected in cases:
        assert weather_text.has_confirmed_storm_word(text) is expected, (text, expected)


def kld_storm_uncertainty_is_not_confirmation() -> None:
    # A hedged storm mention (possibility/probability/persisting-risk/
    # check-later/not-ruled-out) must not count as a confirmed storm.
    for text in (
        "Шторм возможен.",
        "Возможен шторм.",
        "Вероятность шторма 30%.",
        "Риск шторма сохраняется.",
        "Шторм следует уточнить утром.",
        "Шторм не исключён.",
    ):
        assert weather_text.clause_has_confirmed_storm(text) is False, text


def kld_storm_cancellation_is_scoped_to_the_warning() -> None:
    # Only the *warning* being cancelled denies a storm. The storm being the
    # actor of a cancellation ("Шторм отменил...", "Из-за шторма отменены...")
    # still confirms a storm. Bare "отмен\w*"/"исключ\w*" must not be treated
    # as fact negation.
    negated = (
        "Штормовое предупреждение отменено.",
        "Штормовое предупреждение снято.",
        "Штормовое предупреждение не действует.",
    )
    confirmed = (
        "Шторм отменил паромные рейсы.",
        "Из-за шторма отменены рейсы.",
    )
    for text in negated:
        assert weather_text.clause_has_confirmed_storm(text) is False, text
    for text in confirmed:
        assert weather_text.clause_has_confirmed_storm(text) is True, text


def kld_first_line_contains_is_clause_aware_morning_and_evening() -> None:
    # Regression: format_v2._first_line_contains dropped the whole warning line
    # when a negation matched anywhere in it, so the confirmed second clause of
    # "Риск шторма невысок, но штормовое предупреждение действует у моря." was
    # lost. It must survive into both the morning and evening builds.
    warning = "⚠️ Риск шторма невысок, но штормовое предупреждение действует у моря."
    body = (
        "Погода: 🏙️ Калининград — 22/16 °C • облачно • 💨 6 м/с • порывы до 9 м/с.\n"
        "🌊 <b>Морские города</b>\n"
        "Балтийск: 21/16 °C • облачно • 💨 6 м/с • порывы до 9 м/с • 🌊 21 • волна 0.4 м\n"
        "#Калининград #погода #здоровье #море"
    )
    evening_source = (
        "<b>🌅 Калининградская область: погода на завтра (03.07.2026)</b>\n"
        "✨ VayboMeter завтра: 6.0/10 — с оговорками; ветер у моря.\n"
        f"{warning}\n{body}"
    )
    morning_source = (
        "<b>🌅 Калининградская область: погода на сегодня (03.07.2026)</b>\n"
        "✨ VayboMeter: 6.0/10 — с оговорками; ветер у моря.\n"
        f"{warning}\n{body}"
    )
    from format_v2 import _first_line_contains

    assert _first_line_contains(evening_source.splitlines(), "шторм") == warning
    assert _first_line_contains(morning_source.splitlines(), "шторм") == warning


def kld_storm_gust_ms_override_is_consistent_across_layers() -> None:
    # Regression: format_v2.py read STORM_GUST_MS from the environment, but
    # safe_test_post.py and post_kld.py still compared against a literal 15,
    # so overriding the threshold changed the text classification without
    # changing the image-overlay classification. All three now import
    # STORM_GUST_MS from weather_text.py.
    old_value = os.environ.get("STORM_GUST_MS")
    try:
        os.environ["STORM_GUST_MS"] = "16"
        importlib.reload(weather_text)
        importlib.reload(format_v2)
        importlib.reload(safe_test_post)
        importlib.reload(post_kld)
        assert weather_text.STORM_GUST_MS == 16.0

        gust15 = _gust_no_storm_word_evening(15)
        gust16 = _gust_no_storm_word_evening(16)

        text15 = format_v2.build_evening_format_v2("Калининградская область", gust15)
        polished15 = safe_test_post._apply_format_v2_safe_postprocess(text15, "", "", "evening")
        assert "Штормовое предупреждение" not in polished15
        assert post_kld._extract_storm_warning(gust15) is None

        text16 = format_v2.build_evening_format_v2("Калининградская область", gust16)
        assert "⚠️ Главный нюанс: на открытом берегу и пирсах порывы до 16 м/с." in text16
        assert post_kld._extract_storm_warning(gust16) is not None
    finally:
        if old_value is None:
            os.environ.pop("STORM_GUST_MS", None)
        else:
            os.environ["STORM_GUST_MS"] = old_value
        importlib.reload(weather_text)
        importlib.reload(format_v2)
        importlib.reload(safe_test_post)
        importlib.reload(post_kld)
        # After restoration, expect whatever the environment actually had: the
        # caller's own STORM_GUST_MS if one was set, otherwise the 15.0 default.
        expected_restored = float(old_value) if old_value is not None else 15.0
        assert weather_text.STORM_GUST_MS == expected_restored


def _wind_gust_evening(wind_ms, gust_ms=None) -> str:
    gust = f" • порывы до {gust_ms} м/с" if gust_ms is not None else ""
    return (
        "<b>🌅 Калининградская область: погода на завтра (03.07.2026)</b>\n"
        "✨ VayboMeter завтра: 6.0/10 — с оговорками; ветер у моря.\n"
        f"Погода: 🏙️ Калининград — 21/16 °C • облачно • 💨 {wind_ms} м/с{gust}.\n"
        "🌊 <b>Морские города</b>\n"
        f"Балтийск: 21/16 °C • облачно • 💨 {wind_ms} м/с{gust} • 🌊 21 • волна 0.4 м\n"
        "#Калининград #погода #здоровье #море\n"
    )


def _four_layer_storm_state(msg: str) -> dict:
    """Storm decision as seen by each of the four layers, for one message."""
    lines = msg.splitlines()
    fv = format_v2._evening_flags(lines, storm="")
    score_line = safe_test_post._kld_evening_score_line(msg)
    cover = kld_informative_cover.extract_kld_cover_facts(msg, post_type="evening")["weather"]
    return {
        "format_v2_storm_gust": fv["storm_gust"],
        "format_v2_storm": fv["storm"],
        "post_kld_overlay": post_kld._extract_storm_warning(msg) is not None,
        "safe_test_post_storm": "штормов" in score_line.lower(),
        "cover_storm_gust": cover["storm_gust"],
        "cover_storm_badge": cover["storm_badge"],
    }


def kld_storm_gust_uses_gust_not_average_wind_across_all_layers() -> None:
    # Cross-layer contract: storm classification keys on the actual gust
    # ("порыв …"), never on average wind. format_v2._max_wind_ms previously
    # matched both, so "ветер 16 м/с, порывы до 14 м/с" was wrongly a storm in
    # format_v2 while the other three layers (real gust 14 < 15) said no.
    def _assert_all(state: dict, storm: bool) -> None:
        assert state["format_v2_storm_gust"] is storm, state
        assert state["post_kld_overlay"] is storm, state
        assert state["safe_test_post_storm"] is storm, state
        assert state["cover_storm_gust"] is storm, state
        assert state["cover_storm_badge"] is storm, state

    # A. Average wind 16, no "порывы": not a storm anywhere.
    _assert_all(_four_layer_storm_state(_wind_gust_evening(16)), storm=False)
    # B. Average wind 16, gust 14 (< default 15): not a storm in any layer,
    #    even though the average wind is high.
    _assert_all(_four_layer_storm_state(_wind_gust_evening(16, 14)), storm=False)
    # C. Average wind 8, gust 15 (== default 15): a storm in every layer.
    _assert_all(_four_layer_storm_state(_wind_gust_evening(8, 15)), storm=True)

    # B also keeps windy/water cues alive: a strong average wind still marks
    # the day windy and can make the water guidance strict — it just isn't a
    # storm. (max_wind comes through even when storm_gust is False.)
    windy_flags = format_v2._evening_flags(_wind_gust_evening(16, 14).splitlines(), storm="")
    assert windy_flags["wind"] is True
    assert windy_flags["max_wind"] == 16.0
    assert windy_flags["max_gust"] == 14.0
    assert windy_flags["storm_gust"] is False


def kld_storm_gust_threshold_override_uses_gust_across_all_layers() -> None:
    # D/E. Raise the threshold to 16 and reload the env-bound layers; the cover
    # reads the threshold live. 15 м/с gust is no longer a storm; 16 м/с is.
    old_value = os.environ.get("STORM_GUST_MS")
    try:
        os.environ["STORM_GUST_MS"] = "16"
        for mod in (weather_text, format_v2, safe_test_post, post_kld):
            importlib.reload(mod)
        assert weather_text.STORM_GUST_MS == 16.0

        # D. gust 15 with a high average wind 16: below the raised threshold,
        #    so no storm in any layer.
        d = _four_layer_storm_state(_wind_gust_evening(16, 15))
        assert d["format_v2_storm_gust"] is False, d
        assert d["post_kld_overlay"] is False, d
        assert d["safe_test_post_storm"] is False, d
        assert d["cover_storm_gust"] is False, d
        assert d["cover_storm_badge"] is False, d

        # E. gust 16, weak average wind 8: at the raised threshold, a storm.
        e = _four_layer_storm_state(_wind_gust_evening(8, 16))
        assert e["format_v2_storm_gust"] is True, e
        assert e["post_kld_overlay"] is True, e
        assert e["safe_test_post_storm"] is True, e
        assert e["cover_storm_gust"] is True, e
        assert e["cover_storm_badge"] is True, e
    finally:
        if old_value is None:
            os.environ.pop("STORM_GUST_MS", None)
        else:
            os.environ["STORM_GUST_MS"] = old_value
        for mod in (weather_text, format_v2, safe_test_post, post_kld):
            importlib.reload(mod)
        assert weather_text.STORM_GUST_MS == (float(old_value) if old_value is not None else 15.0)


def kld_evening_low_gust_hedged_storm_word_is_not_storm() -> None:
    # Regression: "риск шторма невысок" at 9 м/с previously badged the day as
    # storm because _has_explicit_storm_text / _extract_storm_warning only
    # excluded the literal phrase "без шторма".
    text = build_evening_format_v2("Калининградская область", LOW_GUST_STORM_WORD_EVENING)
    assert "Штормовое предупреждение" not in text
    assert "🧭 Главное завтра: неустойчивое погодное окно" not in text
    assert _extract_storm_warning(LOW_GUST_STORM_WORD_EVENING) is None


def kld_evening_gust14_below_threshold_is_not_storm_but_has_wind_nuance() -> None:
    # Regression: 21.07 -> 22.07 case from the production export — 14 м/с is the
    # strongest gust of the week (below STORM_GUST_MS=15) and the source text
    # never says "шторм", so it must not get the storm badge, but it also must
    # not be silently dropped to a plain forecast with no wind callout.
    text = build_evening_format_v2("Калининградская область", GUST14_NO_STORM_WORD_EVENING)
    assert "Штормовое предупреждение" not in text
    assert "🧭 Главное завтра: неустойчивое погодное окно" not in text
    assert "порывы" in text
    assert _extract_storm_warning(GUST14_NO_STORM_WORD_EVENING) is None


def kld_evening_astro_warning_does_not_trigger_image_storm() -> None:
    msg = "\n".join(
        [
            "⚠️ Общий фон: неблагоприятный день.",
            "⚠️ Нюанс: у воды ветер ощущается сильнее.",
            "Погода: Калининград — 22/16 °C • 💨 5 м/с • порывы до 10 м/с.",
        ]
    )
    assert _extract_storm_warning(msg) is None


def kld_evening_gust19_without_storm_word_is_storm() -> None:
    text = build_evening_format_v2("Калининградская область", GUST19_NO_STORM_WORD_EVENING)
    assert "штормовые порывы" in text
    assert "⚠️ Главный нюанс: на открытом берегу и пирсах порывы до 19 м/с." in text
    assert "Штормовое предупреждение" not in text
    assert "Сёрф: только опытным" in text
    assert _extract_storm_warning(GUST19_NO_STORM_WORD_EVENING) is not None


def kld_evening_fully_populated_astro_preserves_voc() -> None:
    text = build_evening_format_v2("Калининградская область", FULL_ASTRO_EVENING)
    lines = text.splitlines()
    start = lines.index("🌇 <b>Солнце, Луна и ритм завтрашнего дня</b>")
    block = [line for line in lines[start:start + 9] if line.strip()]
    assert "🌅 Рассвет завтра: 04:09" in block
    assert "🌇 Закат завтра: 21:33" in block
    assert "🌖 Убывающая Луна в ♐ — 92% освещённости." in block
    assert "✨ 92% освещённости — эмоции ярче обычного." in block
    assert "⚠️ Общий фон: неблагоприятный день." in block
    assert "💚 В плюсе: планы, обучение." in block
    assert "⚫️ VoC: 03.07 17:15–04.07 00:00." in block
    assert "🌙 В этот период лучше не перегружать вечер." not in block


def kld_evening_safe_postprocess_preserves_storm_astro() -> None:
    text = build_evening_format_v2("Калининградская область", STORM_REAL_EVENING)
    old = os.environ.get("FORMAT_V2_ASTRO_CLEANUP")
    try:
        os.environ["FORMAT_V2_ASTRO_CLEANUP"] = "1"
        polished = _apply_format_v2_safe_postprocess(text, "", "", "evening")
    finally:
        if old is None:
            os.environ.pop("FORMAT_V2_ASTRO_CLEANUP", None)
        else:
            os.environ["FORMAT_V2_ASTRO_CLEANUP"] = old

    assert "⚠️ Общий фон: неблагоприятный день." in polished
    assert "⚫️ VoC: 03.07 17:15–04.07 00:00." in polished
    assert "Отлично" not in polished
    assert "предупреждение, осадки" not in polished
    assert polished.splitlines()[-1] == "#Калининград #погода #здоровье #море"


def kld_evening_safe_postprocess_keeps_hashtags_final_and_dedupes_nuance() -> None:
    text = build_evening_format_v2("Калининградская область", NORMAL_EVENING)
    text += "\n⚠️ Главный нюанс: у воды ветер ощущается сильнее, чем в городе."
    old = os.environ.get("FORMAT_V2_MAIN_NUANCE")
    try:
        os.environ["FORMAT_V2_MAIN_NUANCE"] = "1"
        polished = _apply_format_v2_safe_postprocess(text, "", "", "evening")
    finally:
        if old is None:
            os.environ.pop("FORMAT_V2_MAIN_NUANCE", None)
        else:
            os.environ["FORMAT_V2_MAIN_NUANCE"] = old

    lines = [line.strip() for line in polished.splitlines() if line.strip()]
    tag_index = lines.index("#Калининград #погода #здоровье #море")
    assert tag_index == len(lines) - 1
    assert not lines[tag_index + 1:]
    nuance_lines = [line for line in lines if line.startswith(("⚠️ Нюанс:", "⚠️ Главный нюанс:"))]
    assert len(nuance_lines) <= 1
    assert not any(line.startswith("⚠️ Главный нюанс:") for line in lines[tag_index + 1:])


def main() -> None:
    checks = (
        kld_evening_normal_no_generic_confidence,
        kld_evening_normal_no_sea_correction,
        kld_evening_no_old_conclusion_or_recommendations,
        kld_evening_has_one_final_plan,
        kld_evening_preserves_city_and_marine_lines,
        kld_evening_preserves_compact_astro,
        kld_evening_preserves_quake_line,
        kld_evening_uncertain_has_short_confidence_line,
        kld_evening_warm_uncertain_confidence_is_not_high,
        kld_evening_sup_guidance_is_common_block,
        kld_evening_title_is_compact,
        kld_evening_heat_gust_polish,
        kld_evening_storm_sport_voc_and_background_polish,
        kld_evening_moderate_surf_keeps_cautious_positive,
        kld_evening_generic_warning_is_not_storm,
        kld_evening_rain_warning_is_not_storm,
        kld_evening_safe_postprocess_does_not_promote_wind_nuance_to_storm,
        kld_has_confirmed_storm_word_ignores_negation_from_a_different_line,
        kld_has_confirmed_storm_word_all_lines_negated_is_false,
        kld_has_confirmed_storm_word_confirmed_line_order_independent,
        kld_storm_negation_handles_modifiers_and_clauses,
        kld_storm_uncertainty_is_not_confirmation,
        kld_storm_cancellation_is_scoped_to_the_warning,
        kld_first_line_contains_is_clause_aware_morning_and_evening,
        kld_storm_gust_ms_override_is_consistent_across_layers,
        kld_storm_gust_uses_gust_not_average_wind_across_all_layers,
        kld_storm_gust_threshold_override_uses_gust_across_all_layers,
        kld_evening_low_gust_hedged_storm_word_is_not_storm,
        kld_evening_gust14_below_threshold_is_not_storm_but_has_wind_nuance,
        kld_evening_astro_warning_does_not_trigger_image_storm,
        kld_evening_gust19_without_storm_word_is_storm,
        kld_evening_fully_populated_astro_preserves_voc,
        kld_evening_safe_postprocess_preserves_storm_astro,
        kld_evening_safe_postprocess_keeps_hashtags_final_and_dedupes_nuance,
    )
    for check in checks:
        check()
        print(f"PASS {check.__name__}")
    print(f"OK: {len(checks)} KLD evening FORMAT_V2 checks passed")


if __name__ == "__main__":
    main()
