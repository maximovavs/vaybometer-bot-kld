#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
post_common.py  • общий «движок» для ежедневного поста.

Содержит:
  - Погода (Open-Meteo)
  - Рейтинг городов (дн./ночь, WMO-коды)
  - Качество воздуха и пыльца
  - Шуман с цветным индикатором
  - Астрособытия (фаза, VOC, три совета, next_event)
  - GPT-блок (вывод + три совета)
  - Случайный факт
  - Правильное склонение слова «погода»
  - CTA: «А вы уже решили, как проведёте вечер? 🌆»
"""

from __future__ import annotations
import os
import asyncio
import logging
import json
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

import requests
import pendulum
from requests.exceptions import RequestException
from telegram import Bot, constants, error as tg_err

from utils import (
    compass, clouds_word, get_fact,
    AIR_EMOJI, pm_color, kp_emoji
)
from weather import get_weather, fetch_tomorrow_temps
from air     import get_air, get_sst, get_kp
from pollen  import get_pollen
from schumann import get_schumann
from astro   import astro_events
from gpt     import gpt_blurb

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ───────────────── Constants ─────────────────────────────────────
TZ       = pendulum.timezone("Asia/Nicosia")
TODAY    = pendulum.now(TZ).date()
TOMORROW = TODAY.add(days=1)

# Если скрипт используется для другого региона, то СОБИРАЙТЕ СВОИ CITIES в post_klg.py
CITIES: Dict[str, Tuple[float, float]] = {
    "Limassol": (34.707, 33.022),
    "Larnaca" : (34.916, 33.624),
    "Nicosia" : (35.170, 33.360),
    "Pafos"   : (34.776, 32.424),
    "Troodos" : (34.916, 32.823),
}

POLL_QUESTION = "Как сегодня ваше самочувствие? 🤔"
POLL_OPTIONS  = [
    "🔥 Полон(а) энергии",
    "🙂 Нормально",
    "😴 Слегка вялый(ая)",
    "🤒 Всё плохо",
]

# ───────────────── WMO Weather Interpretation Codes ───────────────
WMO_DESC = {
    0:  "ясно",
    1:  "част. облач.",
    2:  "облачно",
    3:  "пасмурно",
    45: "туман",
    48: "изморозь",
    51: "слаб. морось",
    61: "дождь",
    71: "снег",
    95: "гроза",
    # добавить по желанию
}

def code_desc(code: int) -> str:
    return WMO_DESC.get(code, "—")


def pressure_arrow(hourly: Dict[str, Any]) -> str:
    """
    Определяет тренд давления по данным hourly Open-Meteo.
    Если на конец суток атмосферное давление выше начала > +1 → "↑",
    ниже < -1 → "↓", иначе "→".
    """
    pr = hourly.get("surface_pressure", [])
    if len(pr) < 2:
        return "→"
    delta = pr[-1] - pr[0]
    if delta > 1.0:
        return "↑"
    if delta < -1.0:
        return "↓"
    return "→"


# ───────────────── Schumann display ─────────────────────────────────
def schumann_line(sch: Dict[str, Any]) -> str:
    """
    Форматирует строку Шумана с цветовым индикатором:
      🔴  f < 7.6
      🟢  7.6 ≤ f ≤ 8.1
      🟣  f > 8.1
    и добавляет тренд (↑/↓/→).
    """
    if sch.get("freq") is None:
        return "🎵 Шуман: н/д"
    f   = sch["freq"]
    amp = sch["amp"]
    trend = sch.get("trend", "→")
    if f < 7.6:
        emoji = "🔴"
    elif f > 8.1:
        emoji = "🟣"
    else:
        emoji = "🟢"
    return f"{emoji} Шуман: {f:.2f} Гц / {amp:.1f} pT {trend}"


def get_schumann_with_fallback() -> Dict[str, Any]:
    """
    Пытаемся взять данные из канала в реальном времени (get_schumann()).
    Если не удалось, берем из локального кэша schumann_hourly.json
    и вычисляем тренд по последним 24 часам.
    """
    sch = get_schumann()
    if sch.get("freq") is not None:
        sch["cached"] = False
        return sch

    cache_path = Path(__file__).parent / "schumann_hourly.json"
    if cache_path.exists():
        try:
            arr = json.loads(cache_path.read_text())
            if arr:
                last = arr[-1]
                pts  = arr[-24:]
                freqs = [p["freq"] for p in pts if "freq" in p]
                if len(freqs) >= 2:
                    avg   = sum(freqs[:-1]) / max(1, len(freqs)-1)
                    delta = freqs[-1] - avg
                    trend = "↑" if delta >= 0.1 else "↓" if delta <= -0.1 else "→"
                else:
                    trend = "→"
                return {
                    "freq":  round(last["freq"], 2),
                    "amp":   round(last["amp"], 1),
                    "trend": trend,
                    "cached": True,
                    "high":  False,
                }
        except Exception as e:
            logging.warning("Schumann cache parse error: %s", e)

    return sch


# ───────────────── Main message builder ─────────────────────────────────
def build_msg_common(
    region_name: str,
    cities: Dict[str, Tuple[float, float]],
    sea_label: str = "моря",
    sea_coords: Tuple[float, float] | None = None,
) -> str:
    """
    Общая функция для построения ежедневного сообщения.
    Параметры:
      region_name  — название региона ("Кипре", "Калининградской области" и т. д.)
      cities       — словарь {«Имя города»: (lat, lon)}
      sea_label    — слово для «водной поверхности» ("моря", "море", "озера" и т. п.)
      sea_coords   — координаты (lat, lon) для прогноза температуры воды
                      (если None, пропускаем строку «Темп. моря»).
    """
    P: List[str] = []

    # 1) Заголовок
    P.append(f"<b>🌅 Добрый вечер! Погода на завтра ({TOMORROW.format('DD.MM.YYYY')})</b>")

    # 2) Температура «моря»
    if sea_coords:
        sst = get_sst(*sea_coords)
        if sst is not None:
            P.append(f"🌊 Темп. {sea_label}: {sst:.1f} °C")

    # 3) Основной город (берем первый из cities)
    #    (например: Limassol для Кипра, Kaliningrad для Калининграда)
    main_city = list(cities.keys())[0]
    lat_main, lon_main = cities[main_city]
    # Получаем завтрашние min/max + текущую погоду из Open-Meteo
    day_max, night_min = fetch_tomorrow_temps(lat_main, lon_main, tz=TZ.name)
    w = get_weather(lat_main, lon_main) or {}
    cur = w.get("current", {}) or w.get("current_weather", {})

    if day_max is not None and night_min is not None:
        avg_temp = (day_max + night_min) / 2
    else:
        avg_temp = cur.get("temperature", 0)

    wind_kmh  = cur.get("windspeed") or cur.get("wind_speed") or 0
    wind_deg  = cur.get("winddirection") or cur.get("wind_deg") or 0
    press     = cur.get("pressure") or w.get("hourly", {}).get("surface_pressure", [1013])[0]
    clouds_pct = cur.get("clouds") or w.get("hourly", {}).get("cloud_cover", [0])[0]

    arrow = pressure_arrow(w.get("hourly", {}))
    P.append(
        f"🌡️ Ср. темп: {avg_temp:.0f} °C • {clouds_word(clouds_pct)} "
        f"• 💨 {wind_kmh:.1f} км/ч ({compass(wind_deg)}) "
        f"• 💧 {press:.0f} гПа {arrow}"
    )
    P.append("———")

    # 4) Рейтинг городов (дн./ночь, WMO-код)
    temps: Dict[str, Tuple[float, float, int]] = {}
    for city, (la, lo) in cities.items():
        dd, nn = fetch_tomorrow_temps(la, lo, tz=TZ.name)
        if dd is None:
            continue
        wcodes = get_weather(la, lo) or {}
        # берем daily.weathercode: [сегодня, завтра], поэтому [1]
        code_tmr = wcodes.get("daily", {}).get("weathercode", [])[1] if wcodes else 0
        temps[city] = (dd, nn or dd, code_tmr)

    if temps:
        P.append(f"🎖️ <b>Рейтинг городов ({region_name}, дн./ночь, погода)</b>")
        medals = ["🥇","🥈","🥉","4️⃣","5️⃣"]
        # сортируем по дн. температуре (убыв.), берем топ-5
        sorted_list = sorted(temps.items(), key=lambda kv: kv[1][0], reverse=True)[:5]
        for i, (city, (dd, nn, code)) in enumerate(sorted_list):
            P.append(f"{medals[i]} {city}: {dd:.1f}/{nn:.1f} °C, {code_desc(code)}")
        P.append("———")

    # 5) Качество воздуха и пыльца
    air = get_air() or {}
    lvl = air.get("lvl", "н/д")
    P.append("🏙️ <b>Качество воздуха</b>")
    P.append(
        f"{AIR_EMOJI.get(lvl,'⚪')} {lvl} (AQI {air.get('aqi','н/д')}) | "
        f"PM₂.₅: {pm_color(air.get('pm25'))} | PM₁₀: {pm_color(air.get('pm10'))}"
    )
    if (pollen := get_pollen()):
        P.append("🌿 <b>Пыльца</b>")
        P.append(
            f"Деревья: {pollen['tree']} | "
            f"Травы: {pollen['grass']} | "
            f"Сорняки: {pollen['weed']} — риск {pollen['risk']}"
        )
    P.append("———")

    # 6) Геомагнитка и Шуман
    kp, kp_state = get_kp()
    if kp is not None:
        P.append(f"{kp_emoji(kp)} Геомагнитка: Kp={kp:.1f} ({kp_state})")
    else:
        P.append("🧲 Геомагнитка: н/д")

    P.append(schumann_line(get_schumann_with_fallback()))
    P.append("———")

    # 7) Астрособытия (фаза Луны, VOC, три совета, next_event)
    P.append("🌌 <b>Астрособытия</b>")
    for line in astro_events():
        P.append(line)
    P.append("———")

    # 8) GPT-блок («Вывод» + «Рекомендации»)
    culprit = "погода"
    summary, tips = gpt_blurb(culprit)
    # правильно склоняем слово «погода» в зависимости от региона/языка
    P.append(f"📜 <b>Вывод</b>\n{summary}")
    P.append("———")
    P.append("✅ <b>Рекомендации</b>")
    for tip in tips:
        P.append(f"• {tip}")
    P.append("———")

    # 9) Случайный факт
    P.append(f"📚 {get_fact(TOMORROW)}")

    # 10) CTA
    P.append("———")
    P.append("А вы уже решили, как проведёте вечер? 🌆")

    return "\n".join(P)


async def send_common_post(
    bot: Bot,
    region_name: str,
    cities: Dict[str, Tuple[float, float]],
    sea_label: str = "моря",
    sea_coords: Tuple[float, float] | None = None,
) -> None:
    """
    Отправляет сформированное сообщение через API Telegram.
    """
    text = build_msg_common(region_name, cities, sea_label, sea_coords)
    try:
        await bot.send_message(
            chat_id=CHAT_ID,
            text=text,
            parse_mode=constants.ParseMode.HTML,
            disable_web_page_preview=True,
        )
        logging.info("Message sent ✓")
    except tg_err.TelegramError as e:
        logging.error("Telegram error: %s", e)
        raise


async def send_poll_if_friday(bot: Bot) -> None:
    """
    Присылает опрос в пятницу.
    """
    if pendulum.now(TZ).weekday() == 4:
        try:
            await bot.send_poll(
                CHAT_ID,
                question=POLL_QUESTION,
                options=POLL_OPTIONS,
                is_anonymous=False,
                allows_multiple_answers=False
            )
        except tg_err.TelegramError as e:
            logging.warning("Poll send error: %s", e)


async def main_common(
    region_name: str,
    cities: Dict[str, Tuple[float, float]],
    sea_label: str = "моря",
    sea_coords: Tuple[float, float] | None = None,
) -> None:
    bot = Bot(token=os.getenv("TELEGRAM_TOKEN") or "")
    await send_common_post(bot, region_name, cities, sea_label, sea_coords)
    await send_poll_if_friday(bot)


if __name__ == "__main__":
    # Пример \"локального\" запуска:
    # Для Кипра:
    asyncio.run(main_common(
        region_name="Кипре",
        cities=CITIES,
        sea_label="моря",
        sea_coords=(34.707, 33.022),
    ))