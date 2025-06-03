#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
post_common.py  •  Общая логика формирования и отправки ежедневного поста «VayboMeter».

Экспортируемая функция:
    main_common(bot, chat_id, region_name, sea_label, sea_cities, other_label, other_cities, tz)

Параметры:
    bot            – экземпляр telegram.Bot
    chat_id        – integer, ID чата/канала
    region_name    – строка, название региона («Калининградская область»)
    sea_label      – строка, заголовок для «морских» городов
    sea_cities     – список кортежей (название, (широта, долгота)) морских городов
    other_label    – строка, заголовок для «не-морских» городов
    other_cities   – список кортежей (название, (широта, долгота)) остальных городов
    tz             – pendulum.Timezone, часовой пояс региона

Используемые модули:
— weather.get_weather, weather.fetch_tomorrow_temps
— air.get_air, air.get_sst, air.get_kp
— pollen.get_pollen
— schumann.get_schumann
— astro.astro_events
— lunar.get_day_lunar_info
— gpt.gpt_blurb
— utils.get_fact, utils.compass, utils.clouds_word, utils.AIR_EMOJI, utils.pm_color, utils.kp_emoji
"""

from __future__ import annotations
import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pendulum
from telegram import Bot, error as tg_err, constants

from utils      import compass, clouds_word, get_fact, AIR_EMOJI, pm_color, kp_emoji
from weather    import get_weather, fetch_tomorrow_temps
from air        import get_air, get_sst, get_kp
from pollen     import get_pollen
from schumann   import get_schumann
from astro      import astro_events
from lunar      import get_day_lunar_info
from gpt        import gpt_blurb

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ────────────────────────── Constants ────────────────────────────────────

KLD_LAT = 54.710426
KLD_LON = 20.452214


# ─────────────────────── Helper Functions ───────────────────────────────

def schumann_line(sch: Dict[str, Any]) -> str:
    if sch.get("freq") is None:
        return "🎵 Шуман: н/д"
    f = sch["freq"]
    amp = sch["amp"]
    if f < 7.6:
        icon = "🔴"
    elif f > 8.1:
        icon = "🟣"
    else:
        icon = "🟢"
    return f"{icon} Шуман: {f:.2f} Гц / {amp:.1f} pT {sch['trend']}"

def code_desc(code: int) -> str:
    WMO_DESC = {
        0:  "☀️ ясно",
        1:  "⛅ част. облач.",
        2:  "☁️ облачно",
        3:  "🌥 пасмурно",
        45: "🌫 туман",
        48: "🌫 изморозь",
        51: "🌦 слаб. морось",
        61: "🌧 дождь",
        71: "❄️ снег",
        95: "⛈ гроза",
    }
    return WMO_DESC.get(code, "—")

def pressure_arrow(hourly: Dict[str, Any]) -> str:
    pr = hourly.get("surface_pressure", [])
    if len(pr) < 2:
        return "→"
    delta = pr[-1] - pr[0]
    if delta > 1.0:
        return "↑"
    if delta < -1.0:
        return "↓"
    return "→"


# ────────────────────────── Core Builder ─────────────────────────────────

def build_message(
    region_name: str,
    chat_id: int,
    sea_label: str,
    sea_cities: List[Tuple[str, Tuple[float, float]]],
    other_label: str,
    other_cities: List[Tuple[str, Tuple[float, float]]],
    tz: pendulum.Timezone
) -> str:
    P: List[str] = []
    TODAY = pendulum.now(tz).date()
    TOMORROW = TODAY.add(days=1)

    # — 1) Заголовок —
    P.append(f"<b>🌅 {region_name}: погода на завтра ({TOMORROW.format('DD.MM.YYYY')})</b>")
    P.append("")  # отступ

    # — 2) Температура Балтики —
    sea_lat, sea_lon = sea_cities[0][1]
    sst_main = get_sst(sea_lat, sea_lon)
    if sst_main is not None:
        P.append(f"🌊 <b>Балтика:</b> {sst_main:.1f} °C")
    else:
        P.append("🌊 <b>Балтика:</b> н/д")
    P.append("")  # разделитель

    # — 3) Время восхода/заката (Солнце + Луна) —
    # Предполагается, что get_weather возвращает поля sunrise/sunset, moonrise/moonset в daily
    w_kld = get_weather(KLD_LAT, KLD_LON) or {}
    daily = w_kld.get("daily", {})
    sunrise = daily.get("sunrise", [])
    sunset = daily.get("sunset", [])
    moonrise = daily.get("moonrise", [])
    moonset = daily.get("moonset", [])
    sr = sunrise[1] if len(sunrise) > 1 else "—"
    ss = sunset[1] if len(sunset) > 1 else "—"
    mr = moonrise[1] if len(moonrise) > 1 else "—"
    ms = moonset[1] if len(moonset) > 1 else "—"
    P.append(f"🌇 <b>Солнце:</b> ☀️ {sr}   |   🌇 {ss}")
    P.append(f"🌙 <b>Луна:</b> 🌙 {mr}   |   🌗 {ms}")
    P.append("")  # разделитель

    # — 4) Калининград (ощущаемая температура) —
    d_max, d_min = fetch_tomorrow_temps(KLD_LAT, KLD_LON, tz=tz.name)
    w_cur = w_kld.get("current", {}) or {}
    feels = w_cur.get("feels_like")
    temp_main = feels if feels is not None else w_cur.get("temperature", 0)
    clouds = w_cur.get("clouds", 0)
    wind_kmh = w_cur.get("windspeed", 0.0)
    wind_deg = w_cur.get("winddirection", 0.0)
    press = w_cur.get("pressure", 1013)

    P.append("🏙️ <b>Калининград</b>")
    P.append(f"   🌡️ Ощущается как: {temp_main:.0f} °C   •   {clouds_word(clouds)}")
    P.append(f"   💨 Ветер: {wind_kmh:.1f} км/ч ({compass(wind_deg)})   •   💧 {press:.0f} гПа {pressure_arrow(w_kld.get('hourly', {}))}")
    uv = daily.get("uv_index_max", [None, None])[1]
    if uv is not None:
        P.append(f"   🌞 UV-индекс (макс): {uv}")
    P.append("")  # разделитель

    # — 5) Морские города (топ-5) —
    temps_sea: Dict[str, Tuple[float, float, int, Optional[float]]] = {}
    for city, (la, lo) in sea_cities:
        tday, tnight = fetch_tomorrow_temps(la, lo, tz=tz.name)
        if tday is None:
            continue
        wct = get_weather(la, lo) or {}
        dct = wct.get("daily", {}).get("weathercode", [])
        code = dct[1] if len(dct) > 1 else 0
        sst_city = get_sst(la, lo)
        temps_sea[city] = (tday, tnight or tday, code, sst_city)

    if temps_sea:
        P.append(f"🎖️ <b>{sea_label}</b>")
        medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
        top5 = sorted(temps_sea.items(), key=lambda kv: kv[1][0], reverse=True)[:5]
        for i, (city, (td, tn, code, sst_c)) in enumerate(top5):
            desc = code_desc(code)
            if sst_c is not None:
                P.append(f"   {medals[i]} {city}: {td:.1f}/{tn:.1f} °C, {desc}, 🌊 {sst_c:.1f} °C")
            else:
                P.append(f"   {medals[i]} {city}: {td:.1f}/{tn:.1f} °C, {desc}")
        P.append("")  # разделитель

    # — 6) Тёплые / Холодные города (топ-3/3) —
    temps_other: Dict[str, Tuple[float, float]] = {}
    for city, (la, lo) in other_cities:
        td, tn = fetch_tomorrow_temps(la, lo, tz=tz.name)
        if td is None:
            continue
        temps_other[city] = (td, tn or td)

    if temps_other:
        P.append(f"🔥 <b>Тёплые города</b>   |   ❄️ <b>Холодные города</b>")
        warm = sorted(temps_other.items(), key=lambda kv: kv[1][0], reverse=True)[:3]
        cold = sorted(temps_other.items(), key=lambda kv: kv[1][0])[:3]
        for i in range(3):
            left = f"   • {