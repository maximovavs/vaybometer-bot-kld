#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
post_kld.py  •  Запуск «Kaliningrad daily post» для Telegram-канала.

Задача:
1) Считываем TELEGRAM_TOKEN_KLG и CHANNEL_ID_KLG из окружения
2) Задаём списки городов (морские и прочие)
3) Вызываем main_common из post_common.py, который формирует и отправляет сообщение
"""

import os
import sys
import asyncio
import logging
import pendulum
from telegram import Bot

from post_common import main_common

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ────────────────────────────── Secrets / Env ────────────────────────────────

TOKEN_KLG      = os.getenv("TELEGRAM_TOKEN_KLG", "")
CHANNEL_ID_KLG = os.getenv("CHANNEL_ID_KLG", "")

if not TOKEN_KLG or not CHANNEL_ID_KLG:
    logging.error("Не заданы TELEGRAM_TOKEN_KLG и/или CHANNEL_ID_KLG")
    sys.exit(1)

try:
    CHAT_ID_KLG = int(CHANNEL_ID_KLG)
except ValueError:
    logging.error("CHANNEL_ID_KLG должен быть числом (например, -1001234567890)")
    sys.exit(1)

# ───────────────────────────── Параметры региона ────────────────────────────

SEA_LABEL   = "Морские города (топ-5)"
OTHER_LABEL = "Список не-морских городов (тёплые/холодные)"

# Часовой пояс — Калининград
TZ = "Europe/Kaliningrad"  # можно и pendulum.timezone("Europe/Kaliningrad")

# Топ-5 «морских» городов Калининградской области
SEA_CITIES_ORDERED = [
    ("Балтийск",     (54.649, 20.055)),
    ("Янтарный",     (54.912, 19.887)),
    ("Зеленоградск", (54.959, 20.478)),
    ("Пионерский",   (54.930, 19.825)),
    ("Светлогорск",  (54.952, 20.160)),
]

# «Не-морские» города (из них в посте берутся топ-3 тёплых/холодных)
OTHER_CITIES_ALL = [
    ("Гурьевск",        (54.658, 20.581)),
    ("Светлый",         (54.836, 19.767)),
    ("Советск",         (54.507, 21.347)),
    ("Черняховск",      (54.630, 21.811)),
    ("Гусев",           (54.590, 22.205)),
    ("Неман",           (55.030, 21.877)),
    ("Мамоново",        (54.657, 19.933)),
    ("Полесск",         (54.809, 21.010)),
    ("Багратионовск",   (54.368, 20.632)),
    ("Ладушкин",        (54.872, 19.706)),
    ("Правдинск",       (54.669, 21.330)),
    ("Славск",          (54.765, 21.644)),
    ("Озёрск",          (54.717, 20.282)),
    ("Нестеров",        (54.620, 21.647)),
    ("Краснознаменск",  (54.730, 21.104)),
    ("Гвардейск",       (54.655, 21.078)),
]

# ───────────────────────────────── Main ─────────────────────────────────────

async def main_kld() -> None:
    """Инициализация бота и запуск общей логики для Калининградской области."""
    bot = Bot(token=TOKEN_KLG)
    await main_common(
        bot=bot,
        chat_id=CHAT_ID_KLG,
        region_name="Калининградская область",
        sea_label=SEA_LABEL,
        sea_cities=SEA_CITIES_ORDERED,
        other_label=OTHER_LABEL,
        other_cities=OTHER_CITIES_ALL,
        tz=TZ,  # можно передать строку TZ — внутри приведётся к pendulum.timezone
    )

if __name__ == "__main__":
    asyncio.run(main_kld())
```0