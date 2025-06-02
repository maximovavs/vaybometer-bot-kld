#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
post_klg.py  •  Калининградский ежедневный пост VayboMeter-бота.

Задача:
1. Считываем из окружения TELEGRAM_TOKEN_KLG и CHANNEL_ID_KLG
2. Определяем параметры региона (название, города, «морской» список, «не- морской» список)
3. Запускаем общую функцию для формирования и отправки сообщения (из post_common.py)
"""

import os
import asyncio
import logging
import pendulum
from telegram import Bot, error as tg_err

from post_common import main_common

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ─────────── Constants ─────────────────────────────────────────
# Читаем из Secrets репозитория GitHub
TOKEN_KLG = os.getenv("TELEGRAM_TOKEN_KLG", "")
CHANNEL_ID_KLG = os.getenv("CHANNEL_ID_KLG", "")
if not TOKEN_KLG or not CHANNEL_ID_KLG:
    logging.error("Не заданы TELEGRAM_TOKEN_KLG и/или CHANNEL_ID_KLG")
    exit(1)

# конвертим CHAT_ID к int
try:
    CHAT_ID_KLG = int(CHANNEL_ID_KLG)
except ValueError:
    logging.error("CHANNEL_ID_KLG должен быть числом (например, -1001234567890)")
    exit(1)

# Часовой пояс — Калининград (UTC+2 или UTC+3 в зависимости от DST)
TZ = pendulum.timezone("Europe/Kaliningrad")

# Список «морских» городов Калиниградской области (топ-5)
SEA_CITIES_ORDERED = [
    ("Балтийск",   (54.649, 20.055)),  # координаты примера
    ("Янтарный",   (54.912, 19.887)),
    ("Зеленоградск",(54.959, 20.478)),
    ("Пионерский",(54.930, 19.825)),
    ("Светлогорск",(54.952, 20.160)),
]

# Список «не-морских» городов (выбираем 3 самых тёплых и 3 самых холодных)
# Будем динамически выбирать внутри post_common.py
OTHER_CITIES_ALL = [
    ("Гурьевск",    (54.658, 20.581)),
    ("Светлый",     (54.836, 19.767)),
    ("Советск",     (54.507, 21.347)),
    ("Черняховск",  (54.630, 21.811)),
    ("Гусев",       (54.590, 22.205)),
    ("Неман",       (55.030, 21.877)),
    ("Мамоново",    (54.657, 19.933)),
    ("Полесск",     (54.809, 21.010)),
    ("Багратионовск",(54.368, 20.632)),
    ("Ладушкин",    (54.872, 19.706)),
    ("Правдинск",   (54.669, 21.330)),
    ("Славск",      (54.765, 21.644)),
    ("Озёрск",      (54.717, 20.282)),
    ("Нестеров",    (54.620, 21.647)),
    ("Краснознаменск",(54.730, 21.104)),
    ("Гвардейск",   (54.655, 21.078)),
]

# «Морской» ярлык и «не-морской» ярлык:
SEA_LABEL     = "Морские города (топ-5)"
OTHER_LABEL   = "Список не-морских городов (тёплые/холодные)"

# ─────────── Main ───────────────────────────────────────────────
async def main_klg() -> None:
    """
    Инициализация бота и запуск общей логики для Калининградской области.
    main_common внутри берёт все входные параметры и формирует/отправляет сообщение.
    """
    bot = Bot(token=TOKEN_KLG)

    # Передаём в main_common:
    # - бот
    # - chat_id (целое число)
    # - региональное название (для заголовков)
    # - список морских городов и их координаты
    # - список всех остальных городов (для фильтрации тёплых/холодных)
    await main_common(
        bot=bot,
        chat_id=CHAT_ID_KLG,
        region_name="Калининградская область",
        sea_label=SEA_LABEL,
        sea_cities=SEA_CITIES_ORDERED,
        other_label=OTHER_LABEL,
        other_cities=OTHER_CITIES_ALL,
        tz=TZ
    )

if __name__ == "__main__":
    asyncio.run(main_klg())