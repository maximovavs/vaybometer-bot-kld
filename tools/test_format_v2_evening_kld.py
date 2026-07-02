#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regression checks for compact KLD FORMAT_V2 evening posts."""
from __future__ import annotations

import os
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

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

from safe_test_post import _apply_format_v2_safe_postprocess  # noqa: E402
from post_kld import _extract_storm_warning  # noqa: E402


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
    assert "🎯 Уверенность: по температуре спокойно; ветер/осадки лучше проверить утром." in text
    assert "температура высокая" not in text
    assert "🎯 <b>Уверенность прогноза</b>" not in text
    legacy = text.replace(
        "🎯 Уверенность: по температуре спокойно; ветер/осадки лучше проверить утром.",
        "🎯 Уверенность: температура высокая; ветер/осадки лучше проверить утром.",
    )
    polished = _apply_format_v2_safe_postprocess(legacy, "", "", "evening")
    assert "🎯 Уверенность: по температуре спокойно; ветер/осадки лучше проверить утром." in polished
    assert "температура высокая" not in polished


def kld_evening_warm_uncertain_confidence_is_not_high() -> None:
    text = build_evening_format_v2("Калининградская область", WARM_UNCERTAIN_EVENING)
    assert "температура высокая" not in text
    assert "🎯 Уверенность: температура тёплая; ветер/осадки лучше проверить утром." in text
    assert text.splitlines()[-1] == "#Калининград #погода #здоровье #море"
    assert "🌒 Растущий серп" in text


def kld_evening_sup_guidance_is_common_block() -> None:
    text = build_evening_format_v2("Калининградская область", RAIN_EVENING)
    assert text.count("SUP: на открытой воде не рекомендован") == 1
    assert "🏄 Сёрф: только опытным; волна 0.8–0.9 м, но порывы сильные" in text
    assert "проверить конкретный спот и предупреждения перед выходом" in text
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
    assert "штормовые порывы и локальные осадки" in score_line
    assert "🧭 Главное завтра: штормовые порывы; у воды и на открытых участках особенно осторожно." in text
    assert text.count("Штормовое предупреждение") == 1
    assert "⚠️ <b>Предупреждение</b>" not in text
    assert "⚠️ Предупреждение\n" not in text
    assert "Отлично" not in text
    assert "SUP: на открытой воде не рекомендован" in text
    assert "Сёрф: только опытным" in text
    assert "проверить конкретный спот" in text
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
    assert "⚠️ Штормовое предупреждение: порывы до 19 м/с." in text
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
