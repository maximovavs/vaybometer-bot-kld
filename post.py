#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
post.py — вечерний пост VayboMeter-бота (обновлён 2025-06-01).

• Астрособытия: фаза отдельной строкой + 3 совета без нумерации.
• VoC выводится в том же блоке (если ≥ 15 мин).
• Исправлено «вините погода» → «вините погоду».
• В рекомендациях всегда минимум три пункта.
"""

from __future__ import annotations

import os, asyncio, json, logging, re
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional

import pendulum, requests
from requests.exceptions import RequestException
from telegram import Bot, error as tg_err

# ─── внутренние модули ─────────────────────────────────────────
from utils   import compass, clouds_word, get_fact, AIR_EMOJI, pm_color, kp_emoji
from weather import get_weather, fetch_tomorrow_temps
from air     import get_air, get_sst, get_kp
from pollen  import get_pollen
from schumann import get_schumann
from astro   import astro_events
from gpt     import gpt_blurb
from lunar   import get_day_lunar_info

# ─── константы ────────────────────────────────────────────────
TZ          = pendulum.timezone("Asia/Nicosia")
TODAY       = pendulum.now(TZ).date()
TOMORROW    = TODAY.add(days=1)

TOKEN       = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID     = int(os.getenv("CHANNEL_ID", 0))

CITIES = {
    "Limassol": (34.707, 33.022),
    "Larnaca" : (34.916, 33.624),
    "Nicosia" : (35.170, 33.360),
    "Pafos"   : (34.776, 32.424),
    "Troodos" : (34.916, 32.823),
}

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ─── helpers ──────────────────────────────────────────────────
WMO_DESC = {
    0: "ясно", 1: "част. облач.", 2: "облачно", 3: "пасмурно",
    45: "туман", 48: "изморозь", 51: "морось", 61: "дождь",
    71: "снег", 95: "гроза",
}
def code_desc(code: int) -> str: return WMO_DESC.get(code, "—")

def pressure_arrow(hourly: Dict[str, Any]) -> str:
    pr = hourly.get("surface_pressure", [])
    if len(pr) < 2: return "→"
    d = pr[-1] - pr[0]
    return "↑" if d > 1 else "↓" if d < -1 else "→"

def schumann_line(s: Dict[str, Any]) -> str:
    if s.get("freq") is None:
        return "🎵 Шуман: н/д"
    f = s["freq"]; amp = s["amp"]
    emoji = "🔴" if f < 7.6 else "🟣" if f > 8.1 else "🟢"
    return f"{emoji} Шуман: {f:.2f} Гц / {amp:.1f} pT {s['trend']}"

def get_schumann_with_fallback() -> Dict[str, Any]:
    sch = get_schumann()
    if sch.get("freq") is not None:
        sch["trend"] = "→"
        return sch
    # минимальный кэш-бэкап
    fp = Path(__file__).parent / "schumann_hourly.json"
    if not fp.exists(): return sch
    arr = json.loads(fp.read_text())
    last = arr[-1]
    return {"freq": round(last["freq"],2), "amp": round(last["amp"],1), "trend":"→"}

# ─── основное сообщение ───────────────────────────────────────
def build_msg() -> str:
    P: List[str] = []
    P.append(f"<b>🌅 Добрый вечер! Погода на завтра ({TOMORROW.format('DD.MM.YYYY')})</b>")

    if (sst := get_sst()) is not None:
        P.append(f"🌊 Темп. моря: {sst:.1f} °C")

    # Limassol summary
    lat, lon = CITIES["Limassol"]
    dmax, nmin = fetch_tomorrow_temps(lat, lon, tz=TZ.name)
    w = get_weather(lat, lon) or {}
    cur = w.get("current", {})
    avg_t = (dmax + nmin)/2 if dmax and nmin else cur.get("temperature", 0)
    wind_kmh = cur.get("windspeed", 0); wind_deg = cur.get("winddirection",0)
    clouds = cur.get("clouds", 0); press = cur.get("pressure", 1013)
    P.append(
        f"🌡️ Ср. темп: {avg_t:.0f} °C • {clouds_word(clouds)} "
        f"• 💨 {wind_kmh:.1f} км/ч ({compass(wind_deg)}) "
        f"• 💧 {press:.0f} гПа {pressure_arrow(w.get('hourly',{}))}"
    )
    P.append("———")

    # рейтинг городов
    temps: Dict[str, Tuple[float,float,int]] = {}
    for c,(la,lo) in CITIES.items():
        t_hi, t_lo = fetch_tomorrow_temps(la,lo,tz=TZ.name)
        if t_hi is None: continue
        code = (get_weather(la,lo) or {}).get("daily",{}).get("weathercode",[0,0])[1]
        temps[c] = (t_hi, t_lo or t_hi, code)
    if temps:
        P.append("🎖️ <b>Рейтинг городов (дн./ночь, погода)</b>")
        medals = ["🥇","🥈","🥉","4️⃣","5️⃣"]
        for i,(city,(hi,lo,code)) in enumerate(
            sorted(temps.items(), key=lambda kv: kv[1][0], reverse=True)[:5]):
            P.append(f"{medals[i]} {city}: {hi:.1f}/{lo:.1f} °C, {code_desc(code)}")
        P.append("———")

    # воздух и пыльца
    air = get_air() or {}; lvl = air.get("lvl","н/д")
    P.append("🏙️ <b>Качество воздуха</b>")
    P.append(f"{AIR_EMOJI.get(lvl,'⚪')} {lvl} (AQI {air.get('aqi','н/д')}) | "
             f"PM₂.₅: {pm_color(air.get('pm25'))} | PM₁₀: {pm_color(air.get('pm10'))}")
    if (pol := get_pollen()):
        P.append("🌿 <b>Пыльца</b>")
        P.append(f"Деревья: {pol['tree']} | Травы: {pol['grass']} | "
                 f"Сорняки: {pol['weed']} — риск {pol['risk']}")
    P.append("———")

    # space-weather
    kp, state = get_kp()
    P.append(f"{kp_emoji(kp)} Геомагнитка: Kp={kp:.1f} ({state})" if kp else "🧲 Геомагнитка: н/д")
    P.append(schumann_line(get_schumann_with_fallback()))
    P.append("———")

    # астрособытия
    P.append("🌌 <b>Астрособытия</b>")
    for ln in astro_events():
        P.append(ln)
    if len(P[-1]) != 3:  # если astro_events что-то вернул
        P.append("———")

    # GPT-вывод
    summary, tips = gpt_blurb("погода")
    summary = summary.replace("вините погода", "вините погоду")
    P.append(f"📜 <b>Вывод</b>\n{summary}")
    P.append("———")

    P.append("✅ <b>Рекомендации</b>")
    while len(tips) < 3: tips.append("Наслаждайтесь вечером и берите тепло 😊")
    for t in tips[:3]:
        P.append(f"• {t}")
    P.append("———")
    P.append(f"📚 {get_fact(TOMORROW)}")
    return "\n".join(P)

# ─── telegram I/O ──────────────────────────────────────────────
async def main() -> None:
    bot = Bot(token=TOKEN)
    try:
        await bot.send_message(CHAT_ID, build_msg(),
                               parse_mode="HTML", disable_web_page_preview=True)
        logging.info("Message sent ✓")
    except tg_err.TelegramError as e:
        logging.error("Telegram error: %s", e)

if __name__ == "__main__":
    asyncio.run(main())
