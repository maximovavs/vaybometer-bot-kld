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

Внутри будут вызваны:
— get_weather, fetch_tomorrow_temps (из weather.py)
— get_air, get_sst, get_kp (из air.py)
— get_pollen (из pollen.py)
— get_schumann_with_fallback (локальная функция)
— astro_events (из astro.py)
— get_day_lunar_info (из lunar.py)
— gpt_blurb (из gpt.py)
— get_fact (из utils.py)
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


# ─────────────────────────────── Constants ────────────────────────────────────

# Примерные координаты Калининграда (центр города)
KLD_LAT = 54.710426
KLD_LON = 20.452214


# ───────────────────────────── Schumann Data ──────────────────────────────────

def get_schumann_with_fallback() -> Dict[str, Any]:
    """
    Пытаемся получить текущие значения Шумана.
    Если их нет, читаем последние 24 часа из schumann_hourly.json и
    вычисляем тренд (+/−/→). Возвращаем структуру:
        {
          "freq": float или None,
          "amp":  float или None,
          "trend": "↑"/"↓"/"→",
          "high": bool,
          "cached": bool
        }
    """
    sch = get_schumann()
    if sch.get("freq") is not None:
        sch["cached"] = False
        return sch

    cache_path = Path(__file__).parent / "schumann_hourly.json"
    if cache_path.exists():
        try:
            arr = json.loads(cache_path.read_text(encoding="utf-8"))
            if arr:
                last = arr[-1]
                pts  = arr[-24:]
                freqs = [p["freq"] for p in pts if p.get("freq") is not None]
                if len(freqs) >= 2:
                    avg   = sum(freqs[:-1]) / (len(freqs)-1)
                    delta = freqs[-1] - avg
                    trend = "↑" if delta >= 0.1 else "↓" if delta <= -0.1 else "→"
                else:
                    trend = "→"
                return {
                    "freq":   round(last.get("freq", 0.0), 2),
                    "amp":    round(last.get("amp", 0.0), 1),
                    "trend":  trend,
                    "high":   (last.get("freq", 0.0) > 8.1 or last.get("amp", 0.0) > 100.0),
                    "cached": True,
                }
        except Exception as e:
            logging.warning("Schumann cache parse error: %s", e)

    # если и это не прокатило, вернём оригинал (возможно пустой)
    return sch


def schumann_line(sch: Dict[str, Any]) -> str:
    """
    Формирует строку «Шуман» с цветовым индикатором:
    🔴 если freq < 7.6 Hz
    🟢 если 7.6 ≤ freq ≤ 8.1
    🟣 если freq > 8.1
    Добавляет амплитуду и тренд.
    """
    if sch.get("freq") is None:
        return "🎵 Шуман: н/д"

    f   = sch["freq"]
    amp = sch["amp"]
    if f < 7.6:
        emoji = "🔴"
    elif f > 8.1:
        emoji = "🟣"
    else:
        emoji = "🟢"
    return f"{emoji} Шуман: {f:.2f} Гц / {amp:.1f} pT {sch['trend']}"


# ───────────────────────── Helpers ──────────────────────────────────────────

def code_desc(code: int) -> str:
    """
    WMO Weather Interpretation Codes → краткое описание на русском + эмодзи.
    """
    WMO_DESC = {
        0:  "☀️ ясно",
        1:  "⛅ малооблач.",
        2:  "☁️ облачно",
        3:  "🌥 пасмурно",
        45: "🌫 туман",
        48: "🌫 изморозь",
        51: "🌦 морось",
        61: "🌧 дождь",
        71: "❄️ снег",
        95: "⛈ гроза",
    }
    return WMO_DESC.get(code, "—")


# ─────────────────────────── Core Builder ──────────────────────────────────

def build_message(
    region_name: str,
    chat_id: int,
    sea_label: str,
    sea_cities: List[Tuple[str, Tuple[float, float]]],
    other_label: str,
    other_cities: List[Tuple[str, Tuple[float, float]]],
    tz: pendulum.Timezone
) -> str:
    """
    Шаги:
      1) Заголовок
      2) Температура Балтийского моря (get_sst над sea_cities[0])
      3) Восход/закат Солнца и Луны
      4) «🏙️ Калининград» (ощущается как, облачность, ветер, давление, UV)
      5) Морские города (топ-5) с SST
      6) Тёплые / Холодные города
      7) Качество воздуха + Пыльца
      8) Геомагнитка + Шуман
      9) Астрособытия (offset_days=1, show_all_voc=True)
     10) Вывод & Рекомендации (GPT)
     11) Факт дня
    """
    P: List[str] = []
    TODAY = pendulum.now(tz).date()
    TOMORROW = TODAY.add(days=1)

    # ─── 1) Заголовок ──────────────────────────────────────────────────────────
    P.append(f"<b>🌅 {region_name}: погода на завтра ({TOMORROW.format('DD.MM.YYYY')})</b>")
    P.append("")  # пустая строка после заголовка

    # ─── 2) Температура Балтийского моря ─────────────────────────────────────
    sea_lat, sea_lon = sea_cities[0][1]
    sst_main = get_sst(sea_lat, sea_lon)
    if sst_main is not None:
        P.append(f"🌊 <b>Балтика:</b> {sst_main:.1f} °C")
    else:
        P.append("🌊 <b>Балтика:</b> н/д")
    P.append("")

    # ─── 3) Восход/закат Солнца и Луны ────────────────────────────────────────
    w_main = get_weather(KLD_LAT, KLD_LON) or {}
    daily = w_main.get("daily", {})
    # Забираем второй элемент (завтрашний) или «—»
    sr = daily.get("sunrise", [None, "—"])[1]
    ss = daily.get("sunset",  [None, "—"])[1]
    mr = daily.get("moonrise",[None, "—"])[1]
    ms = daily.get("moonset", [None, "—"])[1]

    P.append(f"🌇 Солнце: ☀️ {sr}   |   🌇 {ss}")
    P.append(f"🌙 Луна: 🌙 {mr}   |   🌗 {ms}")
    P.append("")

    # ─── 4) «🏙️ Калининград» ───────────────────────────────────────────────────
    # Получаем «ощущается как» и другие данные
    day_max, night_min = fetch_tomorrow_temps(KLD_LAT, KLD_LON, tz=tz.name)
    w = w_main.get("current", {}) or {}
    feels = w.get("feels_like")
    if day_max is not None and night_min is not None:
        # Пример: мы не показываем ср. темп, а именно «ощущается как»
        feels_text = f"{feels:.0f} °C" if feels is not None else "—"
        clouds = w.get("clouds", 0)
        wind_kmh  = w.get("windspeed", 0.0)
        wind_deg  = w.get("winddirection", 0.0)
        press     = w.get("pressure", 1013)
        arrow     = pressure_arrow(w_main.get("hourly", {}))
        uv_max    = daily.get("uv_index_max", [None, "—"])[1]

        P.append("🏙️ <b>Калининград</b>")
        P.append(f"   🌡️ Ощущается как: {feels_text}   •   {clouds_word(clouds)}")
        P.append(f"   💨 Ветер: {wind_kmh:.1f} км/ч ({compass(wind_deg)})   •   💧 {press:.0f} гПа {arrow}")
        P.append(f"   🌞 UV-индекс (макс): {uv_max}")
    else:
        # Если нет «feels», отображаем хотя бы среднюю
        avg_temp = (day_max + night_min) / 2 if (day_max is not None and night_min is not None) else w.get("temperature", 0)
        clouds = w.get("clouds", 0)
        wind_kmh  = w.get("windspeed", 0.0)
        wind_deg  = w.get("winddirection", 0.0)
        press     = w.get("pressure", 1013)
        arrow     = pressure_arrow(w_main.get("hourly", {}))
        uv_max    = daily.get("uv_index_max", [None, "—"])[1]

        P.append("🏙️ <b>Калининград</b>")
        P.append(f"   🌡️ Темп.: {avg_temp:.0f} °C   •   {clouds_word(clouds)}")
        P.append(f"   💨 Ветер: {wind_kmh:.1f} км/ч ({compass(wind_deg)})   •   💧 {press:.0f} гПа {arrow}")
        P.append(f"   🌞 UV-индекс (макс): {uv_max}")
    P.append("")

    # ─── 5) Морские города (топ-5) ────────────────────────────────────────────
    temps_sea: Dict[str, Tuple[float, float, int, Optional[float]]] = {}
    for city, (la, lo) in sea_cities:
        d, n = fetch_tomorrow_temps(la, lo, tz=tz.name)
        if d is None:
            continue
        wcodes = get_weather(la, lo) or {}
        daily_codes = wcodes.get("daily", {}).get("weathercode", [])
        code_tmr = daily_codes[1] if len(daily_codes) > 1 else 0
        sst_city = get_sst(la, lo)
        temps_sea[city] = (d, n or d, code_tmr or 0, sst_city)

    if temps_sea:
        P.append(f"🎖️ <b>{sea_label}</b>")
        medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
        sorted_sea = sorted(
            temps_sea.items(),
            key=lambda kv: kv[1][0],
            reverse=True
        )[:5]
        for i, (city, (tday, tnight, wcode, sst_city)) in enumerate(sorted_sea):
            desc = code_desc(wcode)
            if sst_city is not None:
                P.append(
                    f"   {medals[i]} {city}: {tday:.1f}/{tnight:.1f} °C, {desc}, 🌊 {sst_city:.1f} °C"
                )
            else:
                P.append(f"   {medals[i]} {city}: {tday:.1f}/{tnight:.1f} °C, {desc}")
    P.append("")

    # ─── 6) Тёплые / Холодные города ───────────────────────────────────────────
    temps_other: Dict[str, Tuple[float, float]] = {}
    for city, (la, lo) in other_cities:
        d, n = fetch_tomorrow_temps(la, lo, tz=tz.name)
        if d is None:
            continue
        temps_other[city] = (d, n or d)

    if temps_other:
        # Тёплые
        P.append(f"🔥 <b>Тёплые города</b>   |   ❄️ <b>Холодные города</b>")
        top_warm = sorted(temps_other.items(), key=lambda kv: kv[1][0], reverse=True)[:3]
        top_cold = sorted(temps_other.items(), key=lambda kv: kv[1][0])[:3]
        for i in range(max(len(top_warm), len(top_cold))):
            left = right = ""
            if i < len(top_warm):
                city_w, (dw, nw) = top_warm[i]
                left = f"   • {city_w} {dw:.1f}/{nw:.1f} °C"
            if i < len(top_cold):
                city_c, (dc, nc) = top_cold[i]
                right = f"   • {city_c} {dc:.1f}/{nc:.1f} °C"
            P.append(f"{left:<35}{right}")
    P.append("")

    # ─── 7) Качество воздуха + Пыльца ───────────────────────────────────────────
    air = get_air(KLD_LAT, KLD_LON) or {}
    lvl = air.get("lvl", "н/д")
    P.append("🏭 <b>Качество воздуха</b>")
    P.append(
        f"   {AIR_EMOJI.get(lvl, '⚪')} {lvl} (AQI {air.get('aqi', 'н/д')})   •   "
        f"PM₂.₅: {pm_color(air.get('pm25'))}   •   PM₁₀: {pm_color(air.get('pm10'))}"
    )
    if (pollen := get_pollen()):
        P.append("")
        P.append("🌿 <b>Пыльца</b>")
        P.append(
            f"   Деревья: {pollen['tree']}   |   Травы: {pollen['grass']}   |   Сорняки: {pollen['weed']}   — риск {pollen['risk']}"
        )
    P.append("")

    # ─── 8) Геомагнитка + Шуман ────────────────────────────────────────────────
    kp, kp_state = get_kp()
    if kp is not None:
        P.append(f"🧲 Геомагнитка: Kp={kp:.1f} ({kp_state})   🔴" if kp >= 5 else
                 f"🧲 Геомагнитка: Kp={kp:.1f} ({kp_state})   🟢" if kp < 3 else
                 f"🧲 Геомагнитка: Kp={kp:.1f} ({kp_state})   🟡")
    else:
        P.append("🧲 Геомагнитка: н/д")
    P.append(f"🔬 {schumann_line(get_schumann_with_fallback())}")
    P.append("")

    # ─── 9) Астрособытия ─────────────────────────────────────────────────────────
    P.append("🌌 <b>Астрособытия</b>")
    astro_lines = astro_events(offset_days=1, show_all_voc=True, tz=tz)
    if astro_lines:
        for line in astro_lines:
            # Вложение для советов: если строка начинается с «•», добавляем два пробела
            if line.startswith("•"):
                P.append(f"   {line}")
            else:
                P.append(f"   {line}")
    else:
        P.append("   — нет данных —")
    P.append("")

    # ─── 10) Вывод & Рекомендации ───────────────────────────────────────────────
    summary, tips = gpt_blurb("погода")
    summary = summary.replace("вините погода", "вините погоду")
    P.append("📜 <b>Вывод</b>")
    P.append(f"   {summary}")
    P.append("")
    P.append("✅ <b>Рекомендации</b>")
    for t in tips:
        P.append(f"   • {t}")
    P.append("")

    # ─── 11) Факт дня ───────────────────────────────────────────────────────────
    fact = get_fact(TOMORROW, region_name)
    P.append(f"📚 <b>Факт дня:</b> {fact}")

    return "\n".join(P)


async def send_common_post(
    bot: Bot,
    chat_id: int,
    region_name: str,
    sea_label: str,
    sea_cities: List[Tuple[str, Tuple[float, float]]],
    other_label: str,
    other_cities: List[Tuple[str, Tuple[float, float]]],
    tz: pendulum.Timezone
) -> None:
    """
    Отправка сформированного сообщения в Telegram.
    """
    text = build_message(
        region_name=region_name,
        chat_id=chat_id,
        sea_label=sea_label,
        sea_cities=sea_cities,
        other_label=other_label,
        other_cities=other_cities,
        tz=tz
    )
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=constants.ParseMode.HTML,
            disable_web_page_preview=True
        )
        logging.info("Сообщение отправлено ✓")
    except tg_err.TelegramError as e:
        logging.error("Telegram error: %s", e)
        raise


async def main_common(
    bot: Bot,
    chat_id: int,
    region_name: str,
    sea_label: str,
    sea_cities: List[Tuple[str, Tuple[float, float]]],
    other_label: str,
    other_cities: List[Tuple[str, Tuple[float, float]]],
    tz: pendulum.Timezone
) -> None:
    """
    Точка входа. Просто вызывает send_common_post.
    """
    await send_common_post(
        bot=bot,
        chat_id=chat_id,
        region_name=region_name,
        sea_label=sea_label,
        sea_cities=sea_cities,
        other_label=other_label,
        other_cities=other_cities,
        tz=tz
    )