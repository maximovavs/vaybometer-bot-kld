#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
post_klg.py  • ежедневный пост для Калининградской области.

Заменяем:
  region_name = "Калининградской области"
  Морские города  (топ-5): Балтийск, Янтарный, Зеленоградск, Пионерский, Светлогорск
  Неморские города: Гурьевск, Светлый, Советск, Черняховск, Гусев,
                    Неман, Мамоново, Полесск, Багратионовск, Ладушкин,
                    Правдинск, Славск, Озёрск, Нестеров, Краснознаменск, Гвардейск

  Показываем:
   • «Морские города (дн./ночь, погода)» → топ-5 самых тёплых.
   • «Тёплые города» → 3 самых тёплых среди «неморских».
   • «Холодные города» → 3 самых холодных среди «неморских».
   • Остальная логика ― см. в post_common.py
"""

import os
import asyncio
import logging
from typing import Dict, Tuple

from telegram import Bot

# Импортируем общий функционал:
from post_common import (
    main_common, CITIES  # CITIES здесь ― это словарь для Кипра, но мы можем переопределить
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ───────────────── Настройки region_name и списков городов ──────────────
REGION_NAME = "Калининградской области"

# 1) Морские города (координаты каждого вы можете уточнить по своим данным)
SEA_CITIES: Dict[str, Tuple[float, float]] = {
    "Балтийск":    (54.645, 19.912),
    "Янтарный":    (54.922, 19.902),
    "Зеленоградск":(54.964, 20.048),
    "Пионерский":  (54.901, 19.822),
    "Светлогорск": (54.962, 20.148),
}

# 2) Неморские города
NONSEA_CITIES: Dict[str, Tuple[float, float]] = {
    "Гурьевск":      (54.660, 20.497),
    "Светлый":       (54.908, 19.759),
    "Советск":       (54.323, 21.101),
    "Черняховск":    (54.630, 21.812),
    "Гусев":         (54.584, 21.855),
    "Неман":         (54.844, 22.683),
    "Мамоново":      (54.625, 19.892),
    "Полесск":       (54.891, 21.207),
    "Багратионовск": (54.368, 20.643),
    "Ладушкин":      (54.791, 19.855),
    "Правдинск":     (55.029, 21.811),
    "Славск":        (54.756, 21.563),
    "Озёрск":        (54.602, 21.546),
    "Нестеров":      (54.361, 22.723),
    "Краснознаменск":(54.885, 21.094),
    "Гвардейск":     (54.657, 21.082),
}

# Координаты Балтийского моря (берег около Калининграда)
SEA_COORDS: Tuple[float, float] = (54.708, 20.511)  # приближённые координаты Балтийского моря

# ───────────────── Основная точка входа ───────────────────────────────────
async def main_klg():
    bot_token = os.getenv("TELEGRAM_TOKEN_KLG") or ""
    chat_id   = int(os.getenv("CHANNEL_ID_KLG", "0") or "0")
    # Подменяем переменную CHAT_ID из post_common при вызове:
    os.environ["TELEGRAM_TOKEN"] = bot_token
    global CHAT_ID
    CHAT_ID = chat_id

    # 1) Сначала публикуем «Морские города» (топ-5 по температуре)
    #    → Для этого мы вызываем main_common с SEA_CITIES как единственным словарем
    await main_common(
        region_name=REGION_NAME + " (морские города)",  # заголовок уточняется
        cities=SEA_CITIES,
        sea_label="моря",  # т.к. это Балтийское море
        sea_coords=SEA_COORDS,
    )

    # 2) Далее публикуем «Неморские города» ― с тремя самыми теплыми и тремя холодными:
    #    Взять все температуры для NONSEA_CITIES, отсортировать по дневной температуре.
    #    Вывести топ-3 и bottom-3, подобно рейтингу.
    #    После этого выводим тот же блок «качество воздуха → Шуман → Астрособытия» и т. д.
    await asyncio.sleep(3)  # даём чуть паузы, чтобы не «спамить» telegram сразу двумя крупными сообщениями

    # Собираем словарь температур:
    temps_nonsea: Dict[str, Tuple[float, float, int]] = {}
    from weather import fetch_tomorrow_temps, get_weather

    for city, (la, lo) in NONSEA_CITIES.items():
        dd, nn = fetch_tomorrow_temps(la, lo, tz=TZ.name)
        if dd is None:
            continue
        wc = get_weather(la, lo) or {}
        code_tmr = wc.get("daily", {}).get("weathercode", [])[1] if wc else 0
        temps_nonsea[city] = (dd, nn or dd, code_tmr)

    # Если есть данные по NONSEA_CITIES:
    if temps_nonsea:
        # Сортируем по дневной температуре (убыв.), берем top3:
        sorted_cities = sorted(temps_nonsea.items(), key=lambda kv: kv[1][0], reverse=True)
        top3 = sorted_cities[:3]
        bottom3 = sorted_cities[-3:]

        text_lines: List[str] = []
        text_lines.append(f"🎇 <b>Тёплые города ({REGION_NAME}, топ-3)</b>")
        medals = ["🔥","🔥","🔥"]
        for i, (city, (dd, nn, code)) in enumerate(top3):
            text_lines.append(f"{medals[i]} {city}: {dd:.1f}/{nn:.1f} °C, {code_desc(code)}")

        text_lines.append("———")
        text_lines.append(f"❄️ <b>Холодные города ({REGION_NAME}, bottom-3)</b>")
        snowflakes = ["❄️","❄️","❄️"]
        for i, (city, (dd, nn, code)) in enumerate(bottom3):
            text_lines.append(f"{snowflakes[i]} {city}: {dd:.1f}/{nn:.1f} °C, {code_desc(code)}")

        # Отправляем этот блок:
        bot = Bot(token=bot_token)
        try:
            await bot.send_message(
                chat_id=chat_id,
                text="\n".join(text_lines),
                parse_mode=constants.ParseMode.HTML,
                disable_web_page_preview=True
            )
            logging.info("Non-sea cities block sent ✓")
        except tg_err.TelegramError as e:
            logging.error("Telegram error (non-sea): %s", e)

    # 3) Затем немного подождём и отправим остальной «общий» блок:
    await asyncio.sleep(3)
    # Вызываем общий блок (повторно) с «Калининградом» как одним из городов
    # (для того чтобы снова вывести «погоду в Калининграде» и использовать его как основную точку).
    # Чтобы не дублировать весь код, просто добавляем Калининград в верхний словарь:
    full_cities = {
        "Калининград": (54.710, 20.452),
        **NONSEA_CITIES
    }
    await main_common(
        region_name=REGION_NAME,
        cities=full_cities,
        sea_label="моря",
        sea_coords=SEA_COORDS,
    )


if __name__ == "__main__":
    asyncio.run(main_klg())
