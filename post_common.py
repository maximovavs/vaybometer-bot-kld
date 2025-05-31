# post_common.py
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Общий движок вечернего поста VayboMeter-бота.
• Читает API (weather, air, pollen, Шуман, astro…)
• Формирует HTML-сообщение
• Шлёт в Telegram

Региональный скрипт обязан:
1) импортировать build_msg() из этого файла
2) определить свою функцию build_city_blocks()
3) передать TELEGRAM_TOKEN / CHANNEL_ID
"""

from __future__ import annotations
import os, asyncio, json, logging
from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional

import pendulum, requests
from telegram import Bot, error as tg_err
from requests.exceptions import RequestException

# ── внутренние модули (остаются теми же) ──────────────────────
from utils    import compass, clouds_word, get_fact, AIR_EMOJI, pm_color, kp_emoji
from weather  import get_weather, fetch_tomorrow_temps
from air      import get_air, get_sst, get_kp
from pollen   import get_pollen
from schumann import get_schumann
from astro    import astro_events
from gpt      import gpt_blurb
from lunar    import get_day_lunar_info

# ── константы, общие для всех регионов ────────────────────────
TZ          = pendulum.timezone("Europe/Kaliningrad")   # дефолт; региональный скрипт может переопределить
TODAY       = pendulum.now(TZ).date()
TOMORROW    = TODAY.add(days=1)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ── MINI-utilities (не зависят от региона) ────────────────────
WMO_DESC = {
    0: "ясно", 1: "част. облач.", 2: "облачно", 3: "пасмурно",
    45: "туман", 48: "изморозь", 51: "морось", 61: "дождь",
    71: "снег", 95: "гроза",
}
def code_desc(code:int) -> str: return WMO_DESC.get(code, "—")

def pressure_arrow(hourly: Dict[str, Any]) -> str:
    pr = hourly.get("surface_pressure", [])
    if len(pr) < 2: return "→"
    delta = pr[-1]-pr[0]
    return "↑" if delta>1 else "↓" if delta<-1 else "→"

def schumann_line(s: Dict[str, Any]) -> str:
    if s.get("freq") is None:
        return "🎵 Шуман: н/д"
    f = s["freq"]; amp = s["amp"]
    emoji = "🔴" if f<7.6 else "🟣" if f>8.1 else "🟢"
    return f"{emoji} Шуман: {f:.2f} Гц / {amp:.1f} pT {s.get('trend','→')}"

def get_schumann_with_fallback() -> Dict[str, Any]:
    sch = get_schumann()
    if sch.get("freq") is not None:
        sch["trend"]="→"; return sch
    fp = Path(__file__).parent / "schumann_hourly.json"
    if fp.exists():
        try:
            arr=json.loads(fp.read_text())
            last=arr[-1]; return {"freq":round(last['freq'],2),
                                  "amp":round(last['amp'],1),
                                  "trend":"→"}
        except Exception: pass
    return sch


# ───────────────────────────────────────────────────────────────
#  ШАБЛОН СООБЩЕНИЯ — в нём вызывается build_city_blocks()
# ───────────────────────────────────────────────────────────────
def build_msg(region_name:str,
              city_blocks:str,
              culprit:str="погода") -> str:
    """Формирует полный текст сообщения.

    Args:
        region_name: «Калининградская область» (для шапки).
        city_blocks: результат build_city_blocks().
        culprit:     «виновник вывода» для GPT-блока.
    """
    P: List[str] = []
    P.append(f"<b>🌅 Добрый вечер! {region_name}: погода на завтра "
             f"({TOMORROW.format('DD.MM.YYYY')})</b>")

    # температура моря, если есть
    if (sst := get_sst()) is not None:
        P.append(f"🌊 Темп. моря: {sst:.1f} °C")

    # вставляем готовые блоки городов
    P.extend(city_blocks.splitlines())
    P.append("———")

    # воздух & пыльца
    air = get_air() or {}; lvl = air.get("lvl","н/д")
    P.append("🏙️ <b>Качество воздуха</b>")
    P.append(f"{AIR_EMOJI.get(lvl,'⚪')} {lvl} (AQI {air.get('aqi','н/д')}) | "
             f"PM₂.₅: {pm_color(air.get('pm25'))} | PM₁₀: {pm_color(air.get('pm10'))}")
    if (pol := get_pollen()):
        P.append("🌿 <b>Пыльца</b>")
        P.append(f"Деревья: {pol['tree']} | Травы: {pol['grass']} | "
                 f"Сорняки: {pol['weed']} — риск {pol['risk']}")
    P.append("———")

    # космо-погода
    kp,state = get_kp()
    P.append(f"{kp_emoji(kp)} Геомагнитка: Kp={kp:.1f} ({state})" if kp else "🧲 Геомагнитка: н/д")
    P.append(schumann_line(get_schumann_with_fallback()))
    P.append("———")

    # астрособытия
    P.append("🌌 <b>Астрособытия</b>")
    for ln in astro_events():
        P.append(ln)
    P.append("———")

    # GPT-блок
    summary, tips = gpt_blurb(culprit)
    summary = summary.replace("вините погода", "вините погоду")
    P.append(f"📜 <b>Вывод</b>\n{summary}")
    P.append("———")
    P.append("✅ <b>Рекомендации</b>")
    tips = tips + ["Сохраняйте баланс 😊"]*(3-len(tips))
    for t in tips[:3]: P.append(t)
    P.append("———")
    P.append(f"📚 {get_fact(TOMORROW)}")
    return "\n".join(P)


# ── telegram helper ───────────────────────────────────────────
async def tg_send(token:str, chat_id:int, text:str) -> None:
    bot = Bot(token=token)
    try:
        await bot.send_message(chat_id, text,
                               parse_mode="HTML",
                               disable_web_page_preview=True)
        logging.info("Message sent ✓")
    except tg_err.TelegramError as e:
        logging.error("Telegram error: %s", e)


# ------------- региональный скрипт импортирует: ---------------
# • build_msg
# • tg_send
# и обязан определить build_city_blocks()
# ---------------------------------------------------------------
