#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
post_common.py — общая логика для ежедневного “вечернего” поста.

В этом модуле:
 - функции для получения и форматирования погоды
 - функции для качества воздуха, пыльцы, геомагнитки, Шумана
 - функцию astro_events (без параметра tz) — блок “Астрособытия”
 - функцию build_msg_common(...) — которая на вход получает:
     • словарь CITIES (название → (lat, lon))
     • get_sea_temperature(lat, lon) → float (темп. морской воды)
     • читаемые WMO-коды
     • нужные ключи-секреты
   и возвращает готовую к отправке HTML-строку.
"""

from __future__ import annotations
import os
import json
import asyncio
import logging
from pathlib import Path
from typing import Optional, Tuple, Dict, Any, List

import pendulum
import requests
from requests.exceptions import RequestException
from telegram import Bot, error as tg_err

# ── Внутренние импорты ──────────────────────────────────────────
from utils import compass, clouds_word, get_fact, AIR_EMOJI, pm_color, kp_emoji
from weather import get_weather, fetch_tomorrow_temps, code_desc
from air     import get_air, get_sst, get_kp
from pollen  import get_pollen
from schumann import get_schumann
from astro    import astro_events
from gpt      import gpt_blurb
from lunar    import get_day_lunar_info

# ─── Логирование ─────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ─── Константы ────────────────────────────────────────────────────
TZ       = pendulum.timezone("Europe/Kaliningrad")  # универсальная зона, но обычно регионы сами подставят
TODAY    = pendulum.now(TZ).date()
TOMORROW = TODAY.add(days=1)


def get_schumann_with_fallback() -> Dict[str, Any]:
    """
    Возвращает последние данные по резонансу Шумана.
    Если свежих данных нет, пытается взять их из локального cache-файла schumann_hourly.json.
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
                if len(pts) >= 2:
                    freqs = [p["freq"] for p in pts[:-1]]
                    avg   = sum(freqs) / len(freqs)
                    delta = pts[-1]["freq"] - avg
                    trend = "↑" if delta >= 0.1 else "↓" if delta <= -0.1 else "→"
                else:
                    trend = "→"
                return {
                    "freq": round(last["freq"], 2),
                    "amp":  round(last["amp"], 1),
                    "trend": trend,
                    "cached": True,
                    # “high” признак, что Шуман сильно отклонился
                    "high": last["freq"] > 8.1 or last["freq"] < 7.6,
                }
        except Exception as e:
            logging.warning("Schumann cache parse error: %s", e)

    return sch


def schumann_line(sch: Dict[str, Any]) -> str:
    """
    Форматирует строку “Шуман” с цветовым индикатором:
      • f < 7.6  → 🔴 
      • 7.6 ≤ f ≤ 8.1 → 🟢
      • f > 8.1 → 🟣
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

    trend = sch.get("trend", "→")
    return f"{emoji} Шуман: {f:.2f} Гц / {amp:.1f} pT {trend}"


def build_msg_common(
    CITIES: Dict[str, Tuple[float, float]],
    sea_label: str,
    sea_coords: Tuple[float,float],
    chat_date_tz: pendulum.Date,
    TELEGRAM_TOKEN_KEY: str,
    CHANNEL_ID_KEY: str
) -> str:
    """
    Собирает и возвращает HTML-тело вечернего поста, применяемое в разных регионах.

    Параметры:
      • CITIES             — словарь вида {"ИмяГорода": (lat, lon), …}
      • sea_label          — имя “моря” (например, "Балтийское море" или "Средиземное море")
      • sea_coords         — координаты для получения температуры моря
      • chat_date_tz       — pendulum.Date в нужной часовoй зоне, за которую строим прогноз
      • TELEGRAM_TOKEN_KEY  — имя env-переменной с токеном бота в GitHub Secrets
      • CHANNEL_ID_KEY      — имя env-переменной с ID чата (канала) в GitHub Secrets

    Возвращает: длинную HTML-строку (unicode) для отправки через send_message.
    """

    P: List[str] = []

    # ─── 1. Заголовок ──────────────────────────────────────────────
    # Пример: “🌅 Добрый вечер! Погода на завтра (01.06.2025)”
    date_str = chat_date_tz.format("DD.MM.YYYY")
    P.append(f"<b>🌅 Добрый вечер! Погода на завтра ({date_str})</b>")

    # ─── 2. Температура моря ────────────────────────────────────────
    lat_sea, lon_sea = sea_coords
    try:
        sst = get_sst(lat_sea, lon_sea)  # ожидаем, что get_sst умеет принимать координаты
    except Exception:
        sst = None

    if sst is not None:
        P.append(f"🌊 Темп. моря ({sea_label}): {sst:.1f} °C")

    # ─── 3. Основной прогноз (с температурой, облаками, ветром, давлением) ─
    # Берём Limassol (или другой центральный город): но для Калининграда мы выберем “Калининград”.
    # Пусть ключевым городом в CITIES[0] будет именно “Калининград”, если вы так назвали словарь.
    main_city = list(CITIES.keys())[0]
    lat, lon = CITIES[main_city]

    # Получаем температурные максимумы/минимумы (Open-Meteo) на завтра:
    t_max, t_min = fetch_tomorrow_temps(lat, lon, tz=chat_date_tz.timezone_name)

    w = get_weather(lat, lon) or {}
    cur = w.get("current") or w.get("current_weather", {})

    if t_max is not None and t_min is not None:
        avg_temp = (t_max + t_min) / 2
    else:
        avg_temp = cur.get("temperature", 0.0)

    wind_kmh = cur.get("windspeed") or cur.get("wind_speed", 0.0)
    wind_deg = cur.get("winddirection") or cur.get("wind_deg", 0.0)
    press    = cur.get("pressure") or w.get("hourly", {}).get("surface_pressure", [0])[0]
    clouds_pct = cur.get("clouds") or w.get("hourly", {}).get("cloud_cover", [0])[0]

    # Форматируем строку:
    P.append(
        f"🌡️ Ср. темп: {avg_temp:.0f} °C • {clouds_word(clouds_pct)} "
        f"• 💨 {wind_kmh:.1f} км/ч ({compass(wind_deg)}) "
        f"• 💧 {press:.0f} гПа"
    )

    P.append("———")

    # ─── 4. Рейтинг городов (дн./ночь, WMO-код) ───────────────────────
    temps: Dict[str, Tuple[float,float,int]] = {}
    for city, (la, lo) in CITIES.items():
        d, n = fetch_tomorrow_temps(la, lo, tz=chat_date_tz.timezone_name)
        if d is None:
            continue  # если не удалось, пропускаем
        wcode = None
        wdata = get_weather(la, lo) or {}
        # Open-Meteo: daily.weathercode: [вчера, завтра, …]
        wcodes_list = wdata.get("daily", {}).get("weathercode", [])
        if len(wcodes_list) >= 2:
            wcode = wcodes_list[1]
        code_tmr = wcode if wcode is not None else 0
        temps[city] = (d, n or d, code_tmr)

    if temps:
        P.append("🎖️ <b>Рейтинг городов (дн./ночь, погода)</b>")
        medals = ["🥇","🥈","🥉","4️⃣","5️⃣"]
        # Сортируем по дневной температуре ↓
        sorted_cities = sorted(temps.items(), key=lambda kv: kv[1][0], reverse=True)
        for idx, (city, (d, n, code)) in enumerate(sorted_cities[:5]):
            desc = code_desc(code)
            P.append(f"{medals[idx]} {city}: {d:.1f}/{n:.1f} °C, {desc}")
        P.append("———")

    # ─── 5. Качество воздуха & пыльца ───────────────────────────────────
    air = get_air() or {}
    lvl = air.get("lvl", "н/д")
    P.append("🏙️ <b>Качество воздуха</b>")
    P.append(
        f"{AIR_EMOJI.get(lvl,'⚪')} {lvl} (AQI {air.get('aqi','н/д')}) | "
        f"PM₂.₅: {pm_color(air.get('pm25'))} | PM₁₀: {pm_color(air.get('pm10'))}"
    )
    pollen = get_pollen()
    if pollen:
        P.append("🌿 <b>Пыльца</b>")
        P.append(
            f"Деревья: {pollen['tree']} | Травы: {pollen['grass']} | "
            f"Сорняки: {pollen['weed']} — риск {pollen['risk']}"
        )

    P.append("———")

    # ─── 6. Геомагнитка + Шуман ────────────────────────────────────────
    kp, kp_state = get_kp()
    if kp is not None:
        P.append(f"{kp_emoji(kp)} Геомагнитка: Kp={kp:.1f} ({kp_state})")
    else:
        P.append("🧲 Геомагнитка: н/д")

    sch = get_schumann_with_fallback()
    P.append(schumann_line(sch))
    P.append("———")

    # ─── 7. Астрособытия ───────────────────────────────────────────────
    # Здесь просто вызываем astro_events() без tz-параметра
    P.append("🌌 <b>Астрособытия</b>")
    for line in astro_events():
        P.append(line)
    P.append("———")

    # ─── 8. GPT-вывод & рекомендации ───────────────────────────────────
    summary, tips = gpt_blurb("погода")
    P.append(f"📜 <b>Вывод</b>\n{summary}")
    P.append("———")
    P.append("✅ <b>Рекомендации</b>")
    for t in tips:
        P.append(f"• {t}")
    P.append("———")
    P.append(f"📚 {get_fact(TOMORROW)}")

    return "\n".join(P)


async def send_message_common(bot: Bot, text: str) -> None:
    """
    Универсальная обёртка для отправки сообщения в Telegram.
    """
    try:
        await bot.send_message(
            os.getenv("CHANNEL_ID"),  # Для универсальности, но region-specific файлы могут переопределять
            text,
            parse_mode="HTML",
            disable_web_page_preview=True
        )
        logging.info("Message sent ✓")
    except tg_err.TelegramError as e:
        logging.error("Telegram error: %s", e)
        raise


if __name__ == "__main__":
    # Простой тест (если запустить напрямую)
    from telegram import Bot as _Bot
    token = os.getenv("TELEGRAM_TOKEN")
    bot = _Bot(token=token)
    msg = build_msg_common(
        CITIES={"Kaliningrad": (54.71, 20.45)},  # пример
        sea_label="Балтийское море",
        sea_coords=(54.65, 19.94),  # Балтийск
        chat_date_tz=pendulum.now(TZ).date(),
        TELEGRAM_TOKEN_KEY="TELEGRAM_TOKEN",
        CHANNEL_ID_KEY="CHANNEL_ID"
    )
    print(msg[:500])
