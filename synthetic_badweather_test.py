#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Synthetic FORMAT_V2 bad-weather fixture for Kaliningrad safe tests.

This script is used only by the manual Safe Test workflow. It forces a mixed
sea/wind scenario so we can verify temperature emoji correction and cautious
water-sport wording without waiting for real weather to match the edge case.
"""
from __future__ import annotations

import os

from post_safety import sanitize_post_text, validation_summary
from safe_test_post import _apply_format_v2_test_polish


SYNTHETIC_V2 = """<b>🌅 Калининградская область завтра: синтетический bad-weather тест (19.06.2026)</b>

✨ VayboMeter завтра: 5.8/10 — с оговорками; сильные порывы, дождь, холодная вода.
🧭 <b>Главный сценарий</b>
Тестовый сценарий: побережье холодное, мокрое и ветреное; задача — не пропустить рискованные формулировки для воды.

⚠️ Главный нюанс: сильные порывы, дождь и холодная вода у берега.

🎯 <b>Уверенность прогноза</b>
🟡 Ветер у моря: тестовый сценарий — проверяем осторожную формулировку.
🟡 Осадки: тестовый сценарий — проверяем реакцию на дождь.
✅ Температура: тестовая — проверяем правильные эмодзи.

🏙 <b>Калининград</b>
🏙️ Калининград: дн/ночь 15/10 °C • 🌧 дождь • 💨 8 м/с (З) • порывы до 16 м/с • 🔹 1010 гПа ↓

🌊 <b>Морские города</b>
🥵 Балтийск: 15/10 °C • 🌧 дождь • 💨 9 м/с (З) • порывы до 16 м/с • 🌊 12 • 0.9 м
🏄 Отлично: Кайт/Винг/Винд (W/None) • гидрокостюм 5/4 мм
🥵 Янтарный: 14/9 °C • 🌧 дождь • 💨 8 м/с (З) • порывы до 15 м/с • 🌊 11 • 1.0 м
🧜‍♂️ Отлично: SUP (W/None) • гидрокостюм 5/4 мм
🥶 Пионерский: 16/11 °C • 🌧 дождь • 💨 7 м/с (З) • порывы до 14 м/с • 🌊 12 • 0.8 м
🏄 Отлично: Кайт/Винг/Винд (W/None) • гидрокостюм 5/4 мм

🌡 <b>Внутри области</b>
🔥 <b>Тёплые города, °C (топ-3)</b>
• Черняховск: 17/10 °C • 🌧 дождь
• Гусев: 16/9 °C • 🌧 дождь
• Гурьевск: 15/10 °C • 🌧 дождь
❄️ <b>Холодные города, °C (топ-3)</b>
• Светлый: 13/9 °C • 🌧 дождь
• Ладушкин: 14/9 °C • 🌧 дождь
• Полесск: 15/8 °C • 🌧 дождь

🌊 <b>Морская поправка</b>
При такой воде, волне и ветре любые водные активности только для подготовленных и после проверки фактических условий.

🌙 <b>Астроритм</b>
🌙 🌒 Растущий серп, ♌ (20%)

✅ <b>Рекомендации</b>
🧥 Ветровка и непромокаемый слой обязательны.
🚶 Маршруты короткие, лучше застройка и лесные участки вместо открытого берега.

📌 <b>Вывод</b>
Тест: день должен стать осторожным, без “отлично” для кайт/винг/винд и SUP.

#Калининград #погода #здоровье #море"""


def main() -> None:
    os.environ.setdefault("FORMAT_V2", "1")
    os.environ.setdefault("FORMAT_V2_POLISH", "1")
    os.environ.setdefault("FORMAT_V2_WINDSPORT_POLISH", "1")
    os.environ.setdefault("FORMAT_V2_COMPACT", "1")

    print("\n===== SYNTHETIC BAD WEATHER RAW BEGIN =====\n")
    print(SYNTHETIC_V2)
    print("\n===== SYNTHETIC BAD WEATHER RAW END =====\n")

    polished = _apply_format_v2_test_polish(SYNTHETIC_V2)
    final = sanitize_post_text(polished)

    print("\n===== SYNTHETIC BAD WEATHER SAFETY SUMMARY =====\n")
    print(validation_summary(final))
    print("\n===== SYNTHETIC BAD WEATHER MESSAGE BEGIN =====\n")
    print(final.text)
    print("\n===== SYNTHETIC BAD WEATHER MESSAGE END =====\n")


if __name__ == "__main__":
    main()
