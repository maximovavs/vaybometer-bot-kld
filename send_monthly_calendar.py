#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Отправка месячного лунного поста-резюме в Telegram-канал.

• читает lunar_calendar.json
• формирует красивый HTML-текст
• фильтрует Void-of-Course короче MIN_VOC_MINUTES
"""

import os, json, asyncio, html
from pathlib import Path
from typing import Dict, Any, List

import pendulum
from telegram import Bot, constants

# ── настройки ──────────────────────────────────────────────────────────────
TZ                = pendulum.timezone("Asia/Nicosia")
CAL_FILE          = "lunar_calendar.json"
MIN_VOC_MINUTES   = 15
MOON_EMOJI        = "🌙"

TOKEN   = os.getenv("TELEGRAM_TOKEN_KLD", "")
CHAT_ID = os.getenv("CHANNEL_ID_KLD",  "")
if not TOKEN or not CHAT_ID:
    raise RuntimeError("TELEGRAM_TOKEN / CHANNEL_ID не заданы")

# ── helpers ────────────────────────────────────────────────────────────────
def _parse_dt(s: str, year: int):
    try:
        return pendulum.parse(s).in_tz(TZ)
    except Exception:
        dmy, hm  = s.split()
        day, mon = map(int, dmy.split("."))
        hh, mm   = map(int, hm.split(":"))
        return pendulum.datetime(year, mon, day, hh, mm, tz=TZ)

def build_phase_blocks(data: Dict[str, Any]) -> str:
    zodiac_order = ["Овен","Телец","Близнецы","Рак","Лев","Дева",
                    "Весы","Скорпион","Стрелец","Козерог","Водолей","Рыбы"]

    days  = sorted(data.keys())
    lines = []
    i = 0
    while i < len(days):
        start = days[i]
        rec   = data[start]
        name  = rec["phase_name"]
        emoji = rec["phase"].split()[0]
        signs = {rec["sign"]}

        j = i
        while j+1 < len(days) and data[days[j+1]]["phase_name"] == name:
            j += 1
            signs.add(data[days[j]]["sign"])

        d1  = pendulum.parse(start).format("D")
        d2  = pendulum.parse(days[j]).format("D MMM", locale="ru")
        span = f"{d1}–{d2}" if i != j else d2
        signs_str = ", ".join(sorted(signs, key=zodiac_order.index))
        desc = html.escape(rec.get("long_desc", "").strip())

        lines.append(f"<b>{emoji} {span}</b> <i>({signs_str})</i>\n<i>{desc}</i>\n")
        i = j + 1
    return "\n".join(lines)

def build_fav_blocks(rec: Dict[str, Any]) -> str:
    fav = rec["favorable_days"]
    g = fav["general"]
    def fmt(cat): return ", ".join(map(str, fav[cat]["favorable"]))
    parts = [
        f"✅ <b>Благоприятные:</b> {', '.join(map(str, g['favorable']))}",
        f"❌ <b>Неблагоприятные:</b> {', '.join(map(str, g['unfavorable']))}",
        f"✂️ <b>Стрижка:</b> {fmt('haircut')}",
        f"✈️ <b>Путешевствия:</b> {fmt('travel')}",
        f"🛍️ <b>Покупки:</b> {fmt('shopping')}",
        f"❤️ <b>Здоровье:</b> {fmt('health')}",
    ]
    return "\n".join(parts)

def build_voc_list(data: Dict[str, Any], year: int) -> str:
    items: List[str] = []
    for d in sorted(data):
        rec = data[d]["void_of_course"]
        if not rec or not rec["start"] or not rec["end"]:
            continue
        t1 = _parse_dt(rec["start"], year)
        t2 = _parse_dt(rec["end"],   year)
        if (t2 - t1).in_minutes() < MIN_VOC_MINUTES:
            continue
        items.append(f"{t1.format('DD.MM HH:mm')}  →  {t2.format('DD.MM HH:mm')}")
    if not items:
        return ""
    return "<b>⚫️ Void-of-Course:</b>\n" + "\n".join(items)

def build_message(data: Dict[str, Any]) -> str:
    first_day = pendulum.parse(sorted(data.keys())[0])
    header = f"{MOON_EMOJI} <b>Лунный календарь {first_day.format('MMMM YYYY', locale='ru').upper()}</b>\n"

    phases = build_phase_blocks(data)
    fav    = build_fav_blocks(next(iter(data.values())))
    voc    = build_voc_list(data, first_day.year)

    return "\n".join([
        header, phases, fav, "", voc,
        "\n<i>Void-of-Course — период, когда Луна завершила все аспекты в знаке и не вошла в следующий; энергия рассеяна, новые начинания лучше отложить.</i>"
    ])

# ── main ──────────────────────────────────────────────────────────────────
async def main():
    data = json.loads(Path(CAL_FILE).read_text("utf-8"))
    text = build_message(data)

    bot = Bot(TOKEN)
    await bot.send_message(CHAT_ID, text, parse_mode=constants.ParseMode.HTML)

if __name__ == "__main__":
    asyncio.run(main())
