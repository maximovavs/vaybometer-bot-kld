#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
post_klg.py – вечерний прогноз для Калининградской области (канал @vaybometer_39reg)

Основные изменения:
• Температура Балтийского моря (коорд. Балтийска)
• Часовой пояс Europe/Kaliningrad
• Вывод категорий в «Астрособытиях» (шопинг, стрижки и др.)
• Русские формулировки и корректные эмодзи погоды
"""

from __future__ import annotations
import os, asyncio, json, logging
from pathlib import Path
from typing import Dict, Any, Tuple, Optional

import pendulum, requests
from requests.exceptions import RequestException
from telegram import Bot, error as tg_err

# ─────────── внутренние модули (из общего репо) ────────────
from utils   import compass, clouds_word, get_fact, AIR_EMOJI, pm_color, kp_emoji
from weather import get_weather, fetch_tomorrow_temps          # уже написаны
from air     import get_air, get_kp
from pollen  import get_pollen
from schumann import get_schumann
from astro   import astro_events
from gpt     import gpt_blurb

# ─────────── Константы ─────────────────────────────────────
TZ        = pendulum.timezone("Europe/Kaliningrad")
TODAY     = pendulum.now(TZ).date()
TOMORROW  = TODAY.add(days=1)

TOKEN   = os.getenv("TELEGRAM_TOKEN_KLG", "")
CHAT_ID = int(os.getenv("CHANNEL_ID_KLG", 0))

# Крупные населённые пункты
MARINE = {                         # города у моря
    "Балтийск"     : (54.65, 19.90),
    "Янтарный"     : (54.88, 19.94),
    "Зеленоградск" : (54.96, 20.48),
    "Пионерский"   : (54.95, 20.22),
    "Светлогорск"  : (54.94, 20.15),
}

INLAND = {                         # без выхода к морю
    "Черняховск" : (54.64, 21.82),
    "Калининград": (54.71, 20.51),
    "Озёрск"     : (54.41, 22.03),
    "Правдинск"  : (54.44, 21.01),
    "Неман"      : (55.04, 22.03),
    "Краснознаменск": (54.94, 22.50),
    # … список можно расширять
}

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ─────────── Baltic SST ────────────────────────────────────
def get_baltic_sst() -> Optional[float]:
    """Средняя температура Балтийского моря на завтра (по Baltiysk)."""
    lat, lon = MARINE["Балтийск"]
    date_str = TOMORROW.to_date_string()
    url = "https://marine-api.open-meteo.com/v1/marine"
    params = {
        "latitude":  lat,
        "longitude": lon,
        "daily": "sea_surface_temperature_max,sea_surface_temperature_min",
        "start_date": date_str,
        "end_date":   date_str,
        "timezone":   "UTC",
    }
    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        daily = r.json().get("daily", {})
        tmax  = daily.get("sea_surface_temperature_max", [None])[0]
        tmin  = daily.get("sea_surface_temperature_min", [None])[0]
        if tmax is not None and tmin is not None:
            return round((tmax + tmin) / 2, 1)
    except RequestException as e:
        logging.warning("Baltic SST error: %s", e)
    return None

# ─────────── helpers ───────────────────────────────────────
WMO_DESC = {0:"ясно",1:"част. облач.",2:"облачно",3:"пасмурно",
            45:"туман",48:"изморозь",51:"морось",61:"дождь",80:"ливень"}

def code_icon(code: int) -> str:
    if code >= 61:   # дождевые коды
        return "🌧"
    if code in (0,1):
        return "☀️"
    if code in (2,3):
        return "☁️"
    return "🌫"

def pressure_arrow(hourly: Dict[str, Any]) -> str:
    p = hourly.get("surface_pressure", [])
    if len(p) < 2:
        return "→"
    delta = p[-1] - p[0]
    return "↑" if delta > 1 else "↓" if delta < -1 else "→"

# ─────────── основное сообщение ────────────────────────────
def build_msg() -> str:
    P: list[str] = []
    P.append(f"<b>🏖 Добрый вечер! Калининградская область: погода на завтра ({TOMORROW.format('DD.MM.YYYY')})</b>")

    if (sst := get_baltic_sst()) is not None:
        P.append(f"🌊 Темп. моря: {sst:.1f} °C")

    # --- рейтинг городов ------------------------------------
    def city_row(city:str, lat:float, lon:float) -> Tuple[str,float,float,int]:
        d, n = fetch_tomorrow_temps(lat, lon, tz=TZ.name)
        w = get_weather(lat, lon) or {}
        code = w.get("daily", {}).get("weathercode", [0,0])[1]  # прогноз на завтра
        return city, (d or 0), (n or d or 0), code

    marine_rows  = [city_row(c,*MARINE[c])  for c in MARINE]
    inland_rows  = [city_row(c,*INLAND[c])  for c in INLAND]

    marine_sorted = sorted(marine_rows, key=lambda r: r[1], reverse=True)[:5]
    warmest       = sorted(inland_rows, key=lambda r: r[1], reverse=True)[:3]
    coldest       = sorted(inland_rows, key=lambda r: r[1])[:3]

    P.append("🌅 <b>Морские города (топ-5)</b>")
    for c,tmax,tmin,code in marine_sorted:
        P.append(f"{code_icon(code)} {c}: {tmax:.1f}/{tmin:.1f} °C, {WMO_DESC.get(code,'—')}")

    P.append("🔥 <b>Тёплые города</b>")
    for c,tmax,tmin,code in warmest:
        P.append(f"{code_icon(code)} {c}: {tmax:.1f} °C, {WMO_DESC.get(code,'—')}")

    P.append("❄️ <b>Холодные города</b>")
    for c,tmax,tmin,code in coldest:
        P.append(f"{code_icon(code)} {c}: {tmin:.1f} °C, {WMO_DESC.get(code,'—')}")

    P.append("———")

    # --- качество воздуха / пыльца --------------------------
    air = get_air() or {}
    lvl = air.get("lvl","н/д")
    P.append("🏙️ <b>Качество воздуха</b>")
    P.append(f"{AIR_EMOJI.get(lvl,'⚪')} {lvl} (AQI {air.get('aqi','н/д')}) | "
             f"PM₂.₅: {pm_color(air.get('pm25'))} | PM₁₀: {pm_color(air.get('pm10'))}")

    if (pollen := get_pollen()):
        P.append("🌿 <b>Пыльца</b>")
        P.append(f"Деревья: {pollen['tree']} | Травы: {pollen['grass']} | "
                 f"Сорняки: {pollen['weed']} — риск {pollen['risk']}")
    P.append("———")

    # --- space weather --------------------------------------
    kp, kp_state = get_kp()
    P.append(f"{kp_emoji(kp)} Геомагнитка: Kp={kp:.1f} ({kp_state})" if kp else "🧲 Геомагнитка: н/д")
    P.append(schumann_line(get_schumann()))
    P.append("———")

    # --- astro ----------------------------------------------
    P.append("🌌 <b>Астрособытия</b>")
    for line in astro_events(tz=TZ):      # модифицированный astro_events принимает TZ
        P.append(line)
    P.append("———")

    # --- GPT вывод ------------------------------------------
    summary, tips = gpt_blurb("погода")
    P.append(f"📜 <b>Вывод</b>\n{summary}")
    if tips:
        P.append("———")
        P.append("✅ <b>Рекомендации</b>")
        for t in tips[:3]:
            P.append(f"• {t}")
    P.append("———")
    P.append(f"📚 {get_fact(TOMORROW)}")

    return "\n".join(P)

# ─────────── Schumann helper with emoji colour ─────────────
def schumann_line(sch: Dict[str, Any]) -> str:
    if sch.get("freq") is None:
        return "🎵 Шуман: н/д"
    f = sch["freq"]; amp = sch["amp"]; trend = sch["trend"]
    if f < 7.6:      emo = "🔴"
    elif f > 8.1:    emo = "🟣"
    else:            emo = "🟢"
    return f"{emo} Шуман: {f:.2f} Гц / {amp:.1f} pT {trend}"

# ─────────── Telegram I/O ───────────────────────────────────
async def main() -> None:
    bot = Bot(token=TOKEN)
    try:
        await bot.send_message(
            CHAT_ID, build_msg(),
            parse_mode="HTML", disable_web_page_preview=True
        )
        logging.info("Kaliningrad message sent ✓")
    except tg_err.TelegramError as e:
        logging.error("Telegram error: %s", e)
        raise

if __name__ == "__main__":
    asyncio.run(main())
