# post_klg.py
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Вечерний пост для Калининградской области."""

from __future__ import annotations
import os, asyncio, logging
from typing import Dict, Tuple, List

import pendulum
from post_common import (
    TZ, TOMORROW, build_msg, tg_send,
    get_weather, fetch_tomorrow_temps, code_desc
)

# ── СЕКРЕТЫ этого региона ─────────────────────────────────────
TOKEN   = os.getenv("TELEGRAM_TOKEN_KLG", "")
CHAT_ID = int(os.getenv("CHANNEL_ID_KLG", 0))

# ── ГОРОДА Калининградской области ────────────────────────────
SEA_CITIES = {
    "Балтийск"    :(54.651, 19.914),
    "Светлогорск" :(54.942, 20.151),
    "Пионерский"  :(54.950, 20.231),
    "Зеленоградск":(54.959, 20.476),
    "Янтарный"    :(54.878, 19.947),
}
INLAND_CITIES = {
    "Калининград" :(54.710, 20.510),
    "Гурьевск"    :(54.770, 20.602),
    "Светлый"     :(54.677, 20.134),
    "Советск"     :(55.078, 21.888),
    "Черняховск"  :(54.640, 21.818),
    "Гусев"       :(54.563, 22.196),
    "Неман"       :(55.031, 22.030),
    "Мамоново"    :(54.465, 19.937),
    "Полесск"     :(54.862, 21.100),
    "Багратионовск":(54.387, 20.643),
    "Ладушкин"    :(54.569, 20.172),
    "Правдинск"   :(54.443, 21.016),
    "Славск"      :(55.042, 21.674),
    "Озёрск"      :(54.404, 22.013),
    "Нестеров"    :(54.631, 22.567),
    "Краснознаменск":(54.946, 22.492),
    "Гвардейск"   :(54.653, 21.064),
}

# ── Сборка рейтингов ──────────────────────────────────────────
def _collect_temps(cities:Dict[str,Tuple[float,float]])->Dict[str,Tuple[float,float,int]]:
    out={}
    for name,(lat,lon) in cities.items():
        hi,lo = fetch_tomorrow_temps(lat,lon,tz=TZ.name)
        if hi is None: continue
        code = (get_weather(lat,lon) or {}).get("daily",{}).get("weathercode",[0,hi])[1]
        out[name]=(hi,lo or hi,code)
    return out

def build_city_blocks()->str:
    """Возвращает готовый фрагмент с двумя рейтингами."""
    sea  = _collect_temps(SEA_CITIES)
    land = _collect_temps(INLAND_CITIES)

    lines: List[str]=[]

    # рейтинги морских городов (топ-5 по дневной температуре)
    if sea:
        lines.append("🏖️ <b>Морские города (топ-5)</b>")
        medals = ["🥇","🥈","🥉","4️⃣","5️⃣"]
        for i,(name,(hi,lo,code)) in enumerate(
            sorted(sea.items(), key=lambda kv: kv[1][0], reverse=True)[:5]):
            lines.append(f"{medals[i]} {name}: {hi:.1f}/{lo:.1f} °C, {code_desc(code)}")
    # континентальные: 3 самых тёплых и 3 самых холодных
    if land:
        sorted_land = sorted(land.items(), key=lambda kv: kv[1][0], reverse=True)
        top3 = sorted_land[:3]; cold3 = sorted_land[-3:]
        lines.append("")
        lines.append("🏙️ <b>Тёплые города</b>")
        for name,(hi,lo,code) in top3:
            lines.append(f"🔥 {name}: {hi:.1f}/{lo:.1f} °C, {code_desc(code)}")
        lines.append("🌬️ <b>Холодные города</b>")
        for name,(hi,lo,code) in cold3:
            lines.append(f"❄️ {name}: {hi:.1f}/{lo:.1f} °C, {code_desc(code)}")
    return "\n".join(lines)


# ── MAIN runner ───────────────────────────────────────────────
async def main()->None:
    html = build_msg("Калининградская область", build_city_blocks(), culprit="погоду")
    await tg_send(TOKEN, CHAT_ID, html)

if __name__=="__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    asyncio.run(main())
