#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regression checks for compact KLD FORMAT_V2 evening posts."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from format_v2 import build_evening_format_v2  # noqa: E402


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
🌍 Сейсмика 24ч: M2.3, 5 км от Калининграда, глубина 8 км, 12:30.
#Калининград #погода #здоровье #море
"""


RAIN_EVENING = """<b>🌅 Калининградская область: погода на завтра (20.06.2026)</b>
✨ VayboMeter завтра: 5.8/10 — с оговорками; дождь, порывы у моря.
⚠️ <b>Штормовое предупреждение</b>: местами дождь и порывы до 16 м/с.
Погода: 🏙️ Калининград — 15/10 °C • 🌧 дождь • 💨 8 м/с • порывы до 16 м/с.
🌊 <b>Морские города</b>
Балтийск: 15/10 °C • 🌧 дождь • 💨 9 м/с • порывы до 16 м/с • 🌊 12 • 0.9 м
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
    start = lines.index("🌅 <b>Солнце и ритм завтрашнего дня</b>")
    block = [line for line in lines[start:start + 5] if line.strip()]
    assert "🌅 Рассвет завтра: 04:08" in block
    assert "🌇 Закат завтра: 21:33" in block
    assert "🌙 🌒 Растущий серп, ♎ Весы (34%)" in block
    assert len(block) <= 5


def kld_evening_preserves_quake_line() -> None:
    text = build_evening_format_v2("Калининградская область", NORMAL_EVENING)
    assert "🌍 Сейсмика 24ч:" in text


def kld_evening_uncertain_has_short_confidence_line() -> None:
    text = build_evening_format_v2("Калининградская область", RAIN_EVENING)
    assert "🎯 Уверенность: температура высокая; ветер/осадки лучше проверить утром." in text
    assert "🎯 <b>Уверенность прогноза</b>" not in text


def kld_evening_sup_guidance_is_common_block() -> None:
    text = build_evening_format_v2("Калининградская область", RAIN_EVENING)
    assert text.count("SUP/вода") == 1
    assert "🏄 SUP/вода: только опытным, короткая сессия;" in text
    assert "Главный риск — порывы ветра." in text
    assert "🧜‍♂️ Отлично: SUP" not in text
    assert "шорти 2 мм" not in text


def kld_evening_title_is_compact() -> None:
    text = build_evening_format_v2("Калининградская область", NORMAL_EVENING)
    assert text.splitlines()[0] == "<b>🌅 Калининградская область завтра (20.06.2026)</b>"


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
        kld_evening_sup_guidance_is_common_block,
        kld_evening_title_is_compact,
    )
    for check in checks:
        check()
        print(f"PASS {check.__name__}")
    print(f"OK: {len(checks)} KLD evening FORMAT_V2 checks passed")


if __name__ == "__main__":
    main()
