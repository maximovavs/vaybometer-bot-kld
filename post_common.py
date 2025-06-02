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
import logging
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

    cache_path = __import__("pathlib").Path(__file__).parent / "schumann_hourly.json"
    if cache_path.exists():
        try:
            arr = __import__("json").loads(cache_path.read_text(encoding="utf-8"))
            if arr:
                last = arr[-1]
                pts  = arr[-24:]
                freqs = [p["freq"] for p in pts if p.get("freq") is not None]
                if len(freqs) >= 2:
                    avg   = sum(freqs[:-1]) / (len(freqs) - 1)
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
    """
    Сравниваем давление на начало и конец суток → возвращаем стрелочку:
    ↑ если изменение > +1 hPa, ↓ если < −1, иначе →
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
      2) Рейтинг «морских» городов (погода + SST)
      3) Прогноз для «главного города» (Калининград)
      4) Рейтинг «теплых / холодных» городов (не-морские)
      5) Качество воздуха + пыльца
      6) Геомагнитка + Шуман
      7) Астрособытия (offset_days=1, show_all_voc=True)
      8) GPT-блок «Вывод» & «Рекомендации» (замена «вините погода» → «вините погоду»)
      9) Факт (get_fact(TOMORROW, region_name))
    """
    P: List[str] = []
    TODAY = pendulum.now(tz).date()
    TOMORROW = TODAY.add(days=1)

    # 1) Заголовок
    header = f"<b>🌅 {region_name}: погода на завтра ({TOMORROW.format('DD.MM.YYYY')})</b>"
    P.append(header)

    # 2) Рейтинг «морских» городов (завтрашняя tday/tnight + описание + SST)
    temps_sea_per_city: Dict[str, Tuple[float, float, int, Optional[float]]] = {}
    for city, (lat, lon) in sea_cities:
        # 2.1 Получаем завтрашние t_max, t_min
        tday, tnight = fetch_tomorrow_temps(lat, lon, tz=tz.name)
        if tday is None:
            # если не удалось получить прогноз — пропускаем город
            continue

        # 2.2 Выясняем код погоды на завтра
        wdata = get_weather(lat, lon)
        if wdata:
            wc_list = wdata.get("daily", {}).get("weathercode", [])
            if len(wc_list) > 1:
                wcode = wc_list[1]
            elif wc_list:
                wcode = wc_list[0]
            else:
                wcode = 0
        else:
            wcode = 0

        # 2.3 Получаем SST “у берега” для этого города
        try:
            sst_city = get_sst(lat, lon)
        except Exception as e:
            logging.warning("Ошибка get_sst для %s: %s", city, e)
            sst_city = None

        temps_sea_per_city[city] = (tday, tnight or tday, wcode, sst_city)

    if temps_sea_per_city:
        P.append(f"🎖️ <b>{sea_label}</b>")
        medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
        # сортируем по дневной температуре, убывание
        sorted_sea = sorted(
            temps_sea_per_city.items(),
            key=lambda kv: kv[1][0],
            reverse=True
        )[:5]

        for i, (city, (tday, tnight, wcode, sst_city)) in enumerate(sorted_sea):
            desc = code_desc(wcode)
            if sst_city is not None:
                P.append(
                    f"{medals[i]} {city}: {tday:.1f}/{tnight:.1f} °C, {desc}, море: {sst_city:.1f} °C"
                )
            else:
                P.append(
                    f"{medals[i]} {city}: {tday:.1f}/{tnight:.1f} °C, {desc}, море: н/д"
                )
        P.append("———")

    # 3) Прогноз для «главного города» (Калининград)
    main_city_name, (m_lat, m_lon) = "Калининград", (KLD_LAT, KLD_LON)

    day_max, night_min = fetch_tomorrow_temps(m_lat, m_lon, tz=tz.name)
    w_main = get_weather(m_lat, m_lon) or {}
    cur = w_main.get("current", {})

    if day_max is not None and night_min is not None:
        avg_temp = (day_max + night_min) / 2
    else:
        avg_temp = cur.get("temperature", 0)

    wind_kmh   = cur.get("windspeed", 0.0)
    wind_deg   = cur.get("winddirection", 0.0)
    press_main = cur.get("pressure", 1013)
    clouds_main= cur.get("clouds", 0)

    P.append(
        f"🏙️ {main_city_name}: Ср. темп: {avg_temp:.0f} °C • {clouds_word(clouds_main)} "
        f"• 💨 {wind_kmh:.1f} км/ч ({compass(wind_deg)}) "
        f"• 💧 {press_main:.0f} гПа {pressure_arrow(w_main.get('hourly', {}))}"
    )
    P.append("———")

    # 4) Рейтинг «теплых / холодных» (не-морские города)
    temps_other: Dict[str, Tuple[float, float]] = {}
    for city, (lat, lon) in other_cities:
        tmax2, tmin2 = fetch_tomorrow_temps(lat, lon, tz=tz.name)
        if tmax2 is None:
            continue
        temps_other[city] = (tmax2, tmin2 or tmax2)

    if temps_other:
        P.append(f"🔥 <b>Тёплые города</b>")
        top_warm = sorted(temps_other.items(), key=lambda kv: kv[1][0], reverse=True)[:3]
        for city, (d, n) in top_warm:
            P.append(f"   • {city}: {d:.1f}/{n:.1f} °C")

        P.append(f"❄️ <b>Холодные города</b>")
        top_cold = sorted(temps_other.items(), key=lambda kv: kv[1][0])[:3]
        for city, (d, n) in top_cold:
            P.append(f"   • {city}: {d:.1f}/{n:.1f} °C")
        P.append("———")

    # 5) Качество воздуха + пыльца
    air = get_air() or {}
    lvl = air.get("lvl", "н/д")
    P.append("🏭 <b>Качество воздуха</b>")
    P.append(
        f"{AIR_EMOJI.get(lvl, '⚪')} {lvl} (AQI {air.get('aqi', 'н/д')}) | "
        f"PM₂.₅: {pm_color(air.get('pm25'))} | PM₁₀: {pm_color(air.get('pm10'))}"
    )
    if (pollen := get_pollen()):
        P.append("🌿 <b>Пыльца</b>")
        P.append(
            f"Деревья: {pollen['tree']} | Травы: {pollen['grass']} | "
            f"Сорняки: {pollen['weed']} — риск {pollen['risk']}"
        )
    P.append("———")

    # 6) Геомагнитка + Шуман
    kp, kp_state = get_kp()
    if kp is not None:
        P.append(f"{kp_emoji(kp)} Геомагнитка: Kp={kp:.1f} ({kp_state})")
    else:
        P.append("🧲 Геомагнитка: н/д")

    P.append(schumann_line(get_schumann_with_fallback()))
    P.append("———")

    # 7) Астрособытия (offset_days=1: завтрашний день, show_all_voc=True → показываем VoC даже <15 мин)
    P.append("🌌 <b>Астрособытия</b>")
    astro_lines = astro_events(offset_days=1, show_all_voc=True)
    if astro_lines:
        P.extend(astro_lines)
    else:
        P.append("— нет данных —")
    P.append("———")

    # 8) GPT-блок: «Вывод» и «Рекомендации»
    summary, tips = gpt_blurb("погода")
    # Исправляем «вините погода» → «вините погоду»
    summary = summary.replace("вините погода", "вините погоду")

    P.append(f"📜 <b>Вывод</b>\n{summary}")
    P.append("———")
    P.append("✅ <b>Рекомендации</b>")
    for t in tips:
        P.append(f"• {t}")
    P.append("———")

    # 9) Факт (учитываем регион)
    P.append(f"📚 {get_fact(TOMORROW, region_name)}")

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