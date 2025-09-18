#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
post.py — вечерний пост VayboMeter (Кипр), рендер «как в KLD»,
с использованием готовых надёжных помощников из post_common.py.
"""

from __future__ import annotations
import os, sys, asyncio, logging
from typing import Dict, Tuple, List, Optional

import pendulum
from telegram import Bot, error as tg_err

from utils import (
    compass, get_fact, AIR_EMOJI, pm_color, kp_emoji, smoke_index,
)
from weather import get_weather, fetch_tomorrow_temps, day_night_stats
from air import get_air, get_sst, get_kp, get_solar_wind
from pollen import get_pollen

# забираем «боевые» блоки прямо из KLD
from post_common import (
    pick_tomorrow_header_metrics,
    storm_flags_for_tomorrow,
    schumann_line,
    get_schumann_with_fallback,
    build_astro_section,
    radiation_line,            # офиц. радиация (если есть)
    safecast_block_lines,      # строки Safecast (PM/CPM→μSv/h)
    # и для умного вывода:
    _is_air_bad, build_conclusion,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ───────── Константы региона ─────────
TZ = pendulum.timezone("Asia/Nicosia")
TODAY = pendulum.today(TZ)
TOMORROW = TODAY.add(days=1)

# Точка «якоря» для шапки — Лимассол (как Калининград в KLD)
LIM_LAT, LIM_LON = 34.707, 33.022

CITIES: Dict[str, Tuple[float, float]] = {
    "Limassol":  (34.707, 33.022),
    "Nicosia":   (35.170, 33.360),
    "Pafos":     (34.776, 32.424),
    "Ayia Napa": (34.988, 34.012),
    "Troodos":   (34.916, 32.823),
    "Larnaca":   (34.916, 33.624),
}
COASTAL = {"Limassol", "Larnaca", "Pafos", "Ayia Napa"}

WMO_DESC = {
    0: "☀️ ясно", 1: "⛅ ч.обл", 2: "☁️ обл", 3: "🌥 пасм",
    45: "🌫 туман", 48: "🌫 изморозь", 51: "🌦 морось",
    61: "🌧 дождь", 71: "❄️ снег", 95: "⛈ гроза",
}
def code_desc(c: object) -> Optional[str]:
    try: return WMO_DESC.get(int(c))
    except Exception: return None

# ───────── Рейтинг городов (KLD-формат) ─────────
def build_cities_block() -> List[str]:
    """KLD-стиль: дн/ночь, краткое описание, 🌊 если есть."""
    tz_name = TZ.name
    temps: Dict[str, Tuple[float, float, int, Optional[float]]] = {}

    # собираем d/n + wmo + sst (мягко к таймаутам)
    for city, (la, lo) in CITIES.items():
        tmax, tmin = fetch_tomorrow_temps(la, lo, tz=tz_name)
        if tmax is None:
            # пропускаем только если нет вообще ничего (так в KLD)
            continue
        wc = (get_weather(la, lo) or {}).get("daily", {}).get("weathercode", [])
        wc = wc[1] if isinstance(wc, list) and len(wc) > 1 else 0
        sst = get_sst(la, lo) if city in COASTAL else None
        temps[city] = (tmax, tmin or tmax, wc, sst)

    if not temps:
        return ["🎖️ <b>Города (д./н. °C, погода, 🌊)</b>", "— н/д —"]

    medals = ["🥇","🥈","🥉","4️⃣","5️⃣","6️⃣"]
    lines = ["🎖️ <b>Города (д./н. °C, погода, 🌊)</b>"]
    for i, (city, (d, n, wc, sst)) in enumerate(
        sorted(temps.items(), key=lambda kv: kv[1][0], reverse=True)[:6]
    ):
        desc = code_desc(wc)
        line = f"{medals[i]} {city}: {d:.0f}/{n:.0f} °C"
        if desc: line += f" • {desc}"
        if sst is not None: line += f" • 🌊 {sst:.1f}"
        lines.append(line)
    return lines

# ───────── Шапка «как в KLD» для Лимассола ─────────
def build_header_line() -> str:
    tz_name = TZ.name
    stats = day_night_stats(LIM_LAT, LIM_LON, tz=tz_name)
    wm = get_weather(LIM_LAT, LIM_LON) or {}

    # штормовые цифры (макс. порывы завтра)
    storm = storm_flags_for_tomorrow(wm, TZ)
    gust = storm.get("max_gust_ms")

    wcarr = (wm.get("daily", {}) or {}).get("weathercode", [])
    wc = wcarr[1] if isinstance(wcarr, list) and len(wcarr) > 1 else None

    rh_min, rh_max = stats.get("rh_min"), stats.get("rh_max")
    t_day_max, t_night_min = stats.get("t_day_max"), stats.get("t_night_min")

    wind_ms, wind_dir_deg, press_val, press_trend = pick_tomorrow_header_metrics(wm, TZ)
    wind_part = (
        f"💨 {wind_ms:.1f} м/с ({compass(wind_dir_deg)})" if isinstance(wind_ms, (int, float)) and wind_dir_deg is not None
        else (f"💨 {wind_ms:.1f} м/с" if isinstance(wind_ms, (int, float)) else "💨 н/д")
    )
    if isinstance(gust, (int, float)):
        wind_part += f" порывы до {gust:.0f}"

    parts = [
        f"🏙️ Limassol: дн/ночь {t_day_max:.0f}/{t_night_min:.0f} °C" if (t_day_max is not None and t_night_min is not None) else "🏙️ Limassol: дн/ночь н/д",
        (code_desc(wc) or None),
        wind_part,
        (f"💧 RH {rh_min:.0f}–{rh_max:.0f}%" if rh_min is not None and rh_max is not None else None),
        (f"🔹 {press_val} гПа {press_trend}" if isinstance(press_val, int) else None),
    ]
    return " • ".join([x for x in parts if x])

# ───────── Сообщение целиком ─────────
def build_message() -> str:
    P: List[str] = []
    P.append(f"<b>🌅 Кипр: погода на завтра ({TOMORROW.strftime('%d.%m.%Y')})</b>")
    P.append("———")

    # Города (KLD-ранжирование)
    P.extend(build_cities_block())
    P.append("———")

    # Air (как в KLD) + Safecast + дымовой индекс
    P.append("🏭 <b>Качество воздуха</b>")
    air = get_air(LIM_LAT, LIM_LON) or {}
    lvl = air.get("lvl", "н/д")
    P.append(f"{AIR_EMOJI.get(lvl,'⚪')} {lvl} (AQI {air.get('aqi','н/д')}) | PM₂.₅: {pm_color(air.get('pm25'))} | PM₁₀: {pm_color(air.get('pm10'))}")
    # Safecast (если JSON есть)
    P.extend(safecast_block_lines())
    # дымовой индекс (печатаем, только если не «низкое/н/д»)
    em_sm, lbl_sm = smoke_index(air.get("pm25"), air.get("pm10"))
    if lbl_sm and str(lbl_sm).lower() not in ("низкое", "низкий", "нет", "н/д"):
        P.append(f"🔥 Задымление: {em_sm} {lbl_sm}")
    P.append("———")

    # Пыльца
    if (p := get_pollen()):
        P.append("🌿 <b>Пыльца</b>")
        P.append(f"Деревья: {p['tree']} | Травы: {p['grass']} | Сорняки: {p['weed']} — риск {p['risk']}")
        P.append("———")

    # Геомагнитка (со свежестью)
    kp_tuple = get_kp() or (None, "н/д", None, "n/d")
    try:
        kp, ks, kp_ts, _ = kp_tuple
    except Exception:
        kp = kp_tuple[0] if isinstance(kp_tuple, (list, tuple)) and len(kp_tuple) else None
        ks, kp_ts = "н/д", None

    age_txt = ""
    if isinstance(kp_ts, int) and kp_ts > 0:
        try:
            age_min = int((pendulum.now("UTC").int_timestamp - kp_ts) / 60)
            age_txt = f", 🕓 {age_min // 60}ч назад" if age_min > 180 else (f", {age_min} мин назад" if age_min >= 0 else "")
        except Exception:
            pass

    if isinstance(kp, (int, float)):
        P.append(f"{kp_emoji(kp)} Геомагнитка: Kp={kp:.1f} ({ks}{age_txt})")
    else:
        P.append("🧲 Геомагнитка: н/д")

    # Солнечный ветер
    sw = get_solar_wind() or {}
    bz = sw.get("bz"); bt = sw.get("bt"); v = sw.get("speed_kms"); n = sw.get("density")
    wind_status = sw.get("status", "н/д")
    parts = []
    if isinstance(bz, (int, float)): parts.append(f"Bz {bz:.1f} nT")
    if isinstance(bt, (int, float)): parts.append(f"Bt {bt:.1f} nT")
    if isinstance(v,  (int, float)): parts.append(f"v {v:.0f} км/с")
    if isinstance(n,  (int, float)): parts.append(f"n {n:.1f} см⁻³")
    if parts:
        P.append("🌬️ Солнечный ветер: " + ", ".join(parts) + f" — {wind_status}")
        if isinstance(kp, (int, float)) and kp >= 5 and isinstance(wind_status, str) and ("спокой" in wind_status.lower()):
            P.append("ℹ️ По ветру сейчас спокойно; Kp — глобальный индекс за 3 ч.")
    P.append("———")

    # Шуман (с фоллбэком)
    schu_state = get_schumann_with_fallback()
    P.append(schumann_line(schu_state))
    P.append("———")

    # Астрособытия (на завтра, Asia/Nicosia)
    P.append(build_astro_section(date_local=pendulum.today(TZ).add(days=1), tz_local=TZ.name))
    P.append("———")

    # Умный «Вывод»
    wm_anchor = get_weather(LIM_LAT, LIM_LON) or {}
    storm = storm_flags_for_tomorrow(wm_anchor, TZ)
    P.append("📜 <b>Вывод</b>")
    P.extend(build_conclusion(kp, ks, air, storm, schu_state))
    P.append("———")

    # Рекомендации
    from gpt import gpt_blurb
    try:
        theme = (
            "плохая погода" if storm.get("warning") else
            ("магнитные бури" if isinstance(kp, (int, float)) and kp >= 5 else
             ("плохой воздух" if _is_air_bad(air)[0] else
              ("волны Шумана" if (schu_state or {}).get("status_code") == "red" else
               "здоровый день")))
        )
        _, tips = gpt_blurb(theme)
        tips = [t.strip() for t in tips if t.strip()][:3]
        if tips: P.extend(tips)
        else:    P.append("— больше воды, меньше стресса, нормальный сон")
    except Exception:
        P.append("— больше воды, меньше стресса, нормальный сон")
    P.append("———")

    # Факт дня
    P.append(f"📚 {get_fact(TOMORROW, 'Кипр')}")
    return "\n".join(P)

# ───────── Отправка (дробим по 3600) ─────────
async def send_text(bot: Bot, chat_id: int, text: str) -> None:
    chunks: List[str] = []
    cur, cur_len = [], 0
    for line in text.split("\n"):
        if cur_len + len(line) + 1 > 3600 and cur:
            chunks.append("\n".join(cur)); cur, cur_len = [line], len(line) + 1
        else:
            cur.append(line); cur_len += len(line) + 1
    if cur: chunks.append("\n".join(cur))
    for i, part in enumerate(chunks):
        await bot.send_message(chat_id=chat_id, text=part, parse_mode="HTML", disable_web_page_preview=True)
        if i < len(chunks) - 1:
            await asyncio.sleep(0.4)

async def main() -> None:
    token = (os.getenv("TELEGRAM_TOKEN") or "").strip()
    chat_id_env = (os.getenv("CHANNEL_ID") or "").strip()
    try: chat_id = int(chat_id_env) if chat_id_env else 0
    except Exception: chat_id = 0
    if not token or chat_id == 0:
        logging.error("Не заданы TELEGRAM_TOKEN и/или CHANNEL_ID")
        raise SystemExit(1)

    txt = build_message()
    logging.info("Preview: %s", txt[:220].replace("\n", " | "))
    await send_text(Bot(token=token), chat_id, txt)

if __name__ == "__main__":
    asyncio.run(main())
