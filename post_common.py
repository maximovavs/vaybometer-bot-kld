#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
post_common.py  •  Общая логика формирования текстового сообщения для ботов.

Содержит:
  - Блок погоды (темп. воздуха, облачность, ветер, давление с реальным трендом).
  - Рейтинг городов (дн./ночь, расшифровка WMO-кода, возможность вывести 5 самых тёплых/холодных).
  - Качество воздуха и пыльца.
  - Шумановский резонанс (с красно/фиолет/зелёной индикацией).
  - Блок «Астрособытия» (включает фазу, советы и VoC).
  - Общие рекомендации и завершающий факт.
  - Отправка результата в Telegram.

Чтобы использовать:
  из post_klg.py (или post_cyprus.py) вызываем:
      text = build_msg_common(
          location_name="Калининградская область",
          sea_label="Балтийское море",
          cities_for_rating=cities_dict,
          use_wmo=True
      )
      send_message_common(text)
"""

from __future__ import annotations
import os
import asyncio
import json
import logging
from pathlib import Path
from typing import Dict, Any, Tuple, Optional, List

import pendulum
import requests
from requests.exceptions import RequestException
from telegram import Bot, constants, error as tg_err

from weather import get_weather, fetch_tomorrow_temps
from air     import get_air, get_sst, get_kp
from pollen  import get_pollen
from schumann import get_schumann
from astro    import astro_events
from gpt      import gpt_blurb
from lunar    import get_day_lunar_info

# ─────────── Локализация и константы ────────────────────────────────────────────
TZ       = pendulum.timezone("Asia/Nicosia")
TODAY    = pendulum.now(TZ).date()
TOMORROW = TODAY.add(days=1)

TOKEN_ENV     = "TELEGRAM_TOKEN"
CHANNEL_ENV   = "CHANNEL_ID"

# Open-Meteo WMO-коды → краткое описание (расшифровка)
WMO_DESC: Dict[int, str] = {
    0: "ясно", 1: "част. облач.", 2: "облачно", 3: "пасмурно",
    45: "туман", 48: "изморозь", 51: "слаб. морось",
    61: "дождь", 71: "снег", 95: "гроза",
    # можно добавить остальные коды при необходимости
}
def code_desc(code: int) -> str:
    """Возвращает текстовое описание WMO-кода погоды."""
    return WMO_DESC.get(code, "—")

def pressure_arrow(hourly: Dict[str, Any]) -> str:
    """Сравниваем давление на начало и конец суток, возвращаем стрелку."""
    pr = hourly.get("surface_pressure", [])
    if len(pr) < 2:
        return "→"
    delta = pr[-1] - pr[0]
    if delta > 1.0:
        return "↑"
    if delta < -1.0:
        return "↓"
    return "→"

def schumann_line(sch: Dict[str, Any]) -> str:
    """
    Формирует строку с результатом для шумана:
      🟢 нормальная (около 7.8 Гц)
      🔴 ниже нормы (< 7.6)
      🟣 выше нормы (> 8.1)
    """
    if sch.get("freq") is None:
        return "🎵 Шуман: н/д"
    f   = sch["freq"]
    amp = sch["amp"]
    # Выбираем эмодзи по частоте
    if f < 7.6:
        emoji = "🔴"
    elif f > 8.1:
        emoji = "🟣"
    else:
        emoji = "🟢"
    return f"{emoji} Шуман: {f:.2f} Гц / {amp:.1f} pT {sch['trend']}"

def get_schumann_with_fallback() -> Dict[str, Any]:
    """
    Пытаемся взять актуальный шуман, если нет — подгружаем из кэша.
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
                freqs = [p["freq"] for p in pts]
                if len(freqs) >= 2:
                    avg   = sum(freqs[:-1]) / (len(freqs)-1)
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

# ─────────── Основная функция сборки сообщения ─────────────────────────────────
def build_msg_common(
    location_name: str,
    sea_label:      str,
    cities_for_rating: Dict[str, Tuple[float, float]],
    use_wmo:        bool = True
) -> str:
    """
    Формирует текст сообщения:
    1) Заголовок с датой и местом (для завтра).
    2) Температура моря (sea_label).
    3) Прогноз (Limassol/Cyprus) или (Kaliningrad). Если нужно, для главного города можно отдельно.
    4) Рейтинг городов: 5 городов (дн/ночь, при наличии use_wmo – WMO).
    5) Качество воздуха, пыльца.
    6) Геомагнитка + Шуман.
    7) Астрособытия.
    8) Вывод и рекомендации.
    9) Завершающий факт.
    """
    P: list[str] = []
    # ----- 1. Заголовок -----
    P.append(f"<b>🌅 Добрый вечер! {location_name}: погода на завтра ({TOMORROW.format('DD.MM.YYYY')})</b>")

    # ----- 2. Температура моря -----
    if (sst := get_sst()) is not None:
        P.append(f"🌊 Темп. {sea_label}: {sst:.1f} °C")

    # ----- 3. Основной прогноз (первый город из словаря) -----
    #    Берём самый северо-восточный город из списка для расчёта «главного» прогноза (опционально).
    main_city = next(iter(cities_for_rating))
    lat, lon  = cities_for_rating[main_city]
    # Сутки (завтрашний день)
    day_max, night_min = fetch_tomorrow_temps(lat, lon, tz=TZ.name)
    w = get_weather(lat, lon) or {}
    cur = w.get("current", {})

    avg_temp = (day_max + night_min) / 2 if (day_max is not None and night_min is not None) else cur.get("temperature", 0.0)
    wind_kmh = cur.get("windspeed",    cur.get("wind_speed", 0.0))
    wind_deg = cur.get("winddirection", cur.get("wind_deg", 0.0))
    press    = cur.get("pressure",    w.get("hourly", {}).get("surface_pressure", [0])[0])
    clouds   = cur.get("clouds",      w.get("hourly", {}).get("cloud_cover", [0])[0])

    from utils import compass, clouds_word  # используем ваш модуль util
    P.append(
        f"🌡️ Ср. темп: {avg_temp:.0f} °C • {clouds_word(clouds)} "
        f"• 💨 {wind_kmh:.1f} км/ч ({compass(wind_deg)}) "
        f"• 💧 {press:.0f} гПа {pressure_arrow(w.get('hourly', {}))}"
    )
    P.append("———")

    # ----- 4. Рейтинг городов -----
    temps: Dict[str, Tuple[float, float, int]] = {}
    for city, (la, lo) in cities_for_rating.items():
        d, n = fetch_tomorrow_temps(la, lo, tz=TZ.name)
        if d is None:
            continue
        wcodes = get_weather(la, lo) or {}
        # Поле daily.weathercode: [0] – сегодня, [1] – завтра
        code_tmr = None
        if use_wmo:
            code_tmr = wcodes.get("daily", {}).get("weathercode", [])
            code_tmr = code_tmr[1] if len(code_tmr) >= 2 else 0
        temps[city] = (d, n or d, code_tmr or 0)

    if temps:
        P.append(f"🎖️ <b>Рейтинг городов (дн./ночь, {'погода' if use_wmo else 'только темп'})</b>")
        medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
        # сортируем по днём (убывание), при равенстве можно вторично по ночи
        sorted_list = sorted(temps.items(), key=lambda kv: kv[1][0], reverse=True)
        for i, (city, (d, n, code)) in enumerate(sorted_list[:5]):
            wmo_text = f", {code_desc(code)}" if use_wmo else ""
            P.append(f"{medals[i]} {city}: {d:.1f}/{n:.1f} °C{wmo_text}")
        P.append("———")

    # ----- 5. Качество воздуха и пыльца -----
    from utils import AIR_EMOJI, pm_color
    air = get_air() or {}
    lvl = air.get("lvl", "н/д")
    P.append("🏙️ <b>Качество воздуха</b>")
    P.append(f"{AIR_EMOJI.get(lvl,'⚪')} {lvl} (AQI {air.get('aqi','н/д')}) | "
             f"PM₂.₅: {pm_color(air.get('pm25'))} | PM₁₀: {pm_color(air.get('pm10'))}")

    if (pollen := get_pollen()):
        P.append("🌿 <b>Пыльца</b>")
        P.append(f"Деревья: {pollen['tree']} | Травы: {pollen['grass']} | "
                 f"Сорняки: {pollen['weed']} — риск {pollen['risk']}")
    P.append("———")

    # ----- 6. Геомагнитка и Шуман -----
    kp, kp_state = get_kp()
    from utils import kp_emoji
    if kp is not None:
        P.append(f"{kp_emoji(kp)} Геомагнитка: Kp={kp:.1f} ({kp_state})")
    else:
        P.append("🧲 Геомагнитка: н/д")

    P.append(schumann_line(get_schumann_with_fallback()))
    P.append("———")

    # ----- 7. Астрособытия -----
    P.append("🌌 <b>Астрособытия</b>")
    for line in astro_events():
        P.append(line)
    # Кроме того, можем добавить VoC на текущий день (если нужно, но astro_events уже его выводит)
    P.append("———")

    # ----- 8. GPT-вывод и рекомендации -----
    summary, tips = gpt_blurb("погода")
    P.append(f"📜 <b>Вывод</b>\n{summary}")
    P.append("———")
    P.append("✅ <b>Рекомендации</b>")
    for t in tips:
        P.append(f"• {t}")
    P.append("———")

    # ----- 9. Завершающий факт -----
    from utils import get_fact
    P.append(f"📚 {get_fact(TOMORROW)}")

    return "\n".join(P)


async def send_message_common(text: str) -> None:
    """
    Отправляет сформированную строку в Telegram.
    Ожидает, что переменные окружения установлены:
      - TELEGRAM_TOKEN
      - CHANNEL_ID
    """
    token   = os.getenv(TOKEN_ENV, "")
    chat_id = os.getenv(CHANNEL_ENV, "")
    if not token or not chat_id:
        logging.error("Отсутствует TELEGRAM_TOKEN или CHANNEL_ID")
        return

    bot = Bot(token=token)
    try:
        await bot.send_message(
            chat_id=int(chat_id),
            text=text,
            parse_mode=constants.ParseMode.HTML,
            disable_web_page_preview=True
        )
        logging.info("Message sent ✓")
    except tg_err.TelegramError as e:
        logging.error("Telegram error: %s", e)
        raise

# Если захотите протестировать локально:
if __name__ == "__main__":
    # Пример вызова для проверки (поля задаём, но send не производим)
    txt = build_msg_common(
        location_name="Локейшн тест",
        sea_label="Тестовое море",
        cities_for_rating={"Город1": (0.0, 0.0)},
        use_wmo=False
    )
    print(txt)