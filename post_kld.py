#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
post_klg.py — вечерний прогноз для Калининградской области.
Использует общую логику из post_common.py, дополняя специфические данные:
  • список морских городов (топ-5 Балтийск → Пионерский)
  • список “теплых” городов (выбирается по температуре из непоср. списка городов)
  • список “холодных” городов
  • вывод температуры Балтийского моря
  • проставляем реальные переменные окружения: TELEGRAM_TOKEN_KLG, CHANNEL_ID_KLG
"""

from __future__ import annotations
import os
import asyncio
import pendulum
import logging
from typing import Dict, Tuple

from telegram import Bot

from post_common import build_msg_common, send_message_common

# ─── Логирование ─────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ─── Константы (Калининградская область) ─────────────────────────
TZ_KLG    = pendulum.timezone("Europe/Kaliningrad")
TODAY_KLG = pendulum.now(TZ_KLG).date()
TOMORROW_KLG = TODAY_KLG.add(days=1)

# Словарь городов КЛГ: ключ "Kaliningrad" должен быть первым
CITIES_KLG: Dict[str, Tuple[float, float]] = {
    "Kaliningrad": (54.716, 20.511),  # Координаты центра Калининграда
    # Морские города (топ-5)
    "Baltiysk":    (54.644, 19.910),
    "Yantarny":    (54.885, 20.280),
    "Zelenogradsk":(54.952, 20.487),
    "Pionersky":   (54.969, 19.950),
    "Svetlogorsk": (54.952, 20.150),
    # Неморские города (для “теплых/холодных”)
    "Chern'yakhovsk": (54.631, 21.812),
    "Guryevsk":      (54.657, 20.512),
    "Svetly":        (54.704, 20.158),
    "Sovetsk":       (55.055, 21.883),
    "Chernyakhovsk": (54.631, 21.812),
    "Gusyev":        (54.585, 22.175),
    "Neman":         (54.661, 21.830),
    "Mamonovo":      (54.386, 19.264),
    "Polessk":       (54.867, 21.083),
    "Bagrationovsk": (54.633, 20.794),
    "Ladushkin":     (54.642, 19.914),
    "Pravdinsk":     (54.374, 21.318),
    "Slavsk":        (55.153, 22.651),
    "Ozërsk":        (54.334, 21.770),
    "Nesterov":      (54.419, 22.699),
    "Krasnoznamensk":(54.669, 22.151),
    "Gvardeysk":     (54.674, 21.000),
}

# “Балтийское море” берём по координатам Балтийск
SEA_LABEL = "Балтийское море"
SEA_COORDS = CITIES_KLG["Baltiysk"]

# Имя переменной окружения для токена (GitHub Secrets должны содержать TELEGRAM_TOKEN_KLG)
TELEGRAM_TOKEN_KEY = "TELEGRAM_TOKEN_KLG"
# Имя переменной окружения для ID чата (GitHub Secrets должны содержать CHANNEL_ID_KLG)
CHANNEL_ID_KEY = "CHANNEL_ID_KLG"


async def main() -> None:
    """
    Асинхронная точка входа:
    1) Формируем пост на завтра (TOMORROW_KLG) через build_msg_common
    2) Отправляем в Telegram через send_message_common
    """
    # Читаем environment-переменные:
    token = os.getenv(TELEGRAM_TOKEN_KEY, "")
    channel_id = os.getenv(CHANNEL_ID_KEY, "")

    if not token or not channel_id:
        logging.error("❌ Не задан TELEGRAM_TOKEN_KLG или CHANNEL_ID_KLG")
        return

    # Переопределяем переменные окружения, чтобы send_message_common знал, куда слать:
    os.environ["TELEGRAM_TOKEN"] = token
    os.environ["CHANNEL_ID"]   = channel_id

    # Строим готовый текст:
    msg = build_msg_common(
        CITIES=CITIES_KLG,
        sea_label=SEA_LABEL,
        sea_coords=SEA_COORDS,
        chat_date_tz=TOMORROW_KLG,
        TELEGRAM_TOKEN_KEY=TELEGRAM_TOKEN_KEY,
        CHANNEL_ID_KEY=CHANNEL_ID_KEY
    )

    # Отправка:
    bot = Bot(token=token)
    await send_message_common(bot, msg)


if __name__ == "__main__":
    asyncio.run(main())
