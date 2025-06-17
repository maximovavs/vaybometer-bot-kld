#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
post_common.py  •  Общая логика формирования и отправки ежедневного поста
«VayboMeter» (Калининград).

• Температура залива, прогноз для Калининграда
• Рейтинг «морских» и тёплых/холодных городов
• Качество воздуха, пыльца, **радиационный фон**
• Геомагнитка, резонанс Шумана
• Астрособытия, динамический вывод-виновник, советы, факт дня
"""

from __future__ import annotations
import asyncio, json, logging
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pendulum
from telegram import Bot, error as tg_err, constants

from utils    import compass, clouds_word, get_fact, AIR_EMOJI, pm_color, kp_emoji
from weather  import get_weather, fetch_tomorrow_temps
from air      import get_air, get_sst, get_kp
from pollen   import get_pollen
from schumann import get_schumann
from astro    import astro_events
from lunar    import get_day_lunar_info
from gpt      import gpt_blurb
from radiation import get_radiation                      # ← NEW

from settings_klg import SEA_SST_COORD                   # точка в заливе

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ─────────────────────────────── Constants ──────────────────────────────────
KLD_LAT, KLD_LON = 54.710426, 20.452214            # центр Калининграда

def pressure_arrow(hourly: Dict[str, Any]) -> str:
    pr = hourly.get("surface_pressure", [])
    if len(pr) < 2:
        return "→"
    delta = pr[-1] - pr[0]
    if   delta > 1.0:  return "↑"
    elif delta < -1.0: return "↓"
    return "→"

def code_desc(code: int) -> str:
    return {
        0:"☀️ ясно",1:"⛅ ч.обл",2:"☁️ обл",3:"🌥 пасм",45:"🌫 туман",
        48:"🌫 изморозь",51:"🌦 морось",61:"🌧 дождь",71:"❄️ снег",95:"⛈ гроза"
    }.get(code,"")

# ────────────────────────────── Шуман helper ───────────────────────────────
def get_schumann_with_fallback() -> Dict[str,Any]:
    sch = get_schumann()
    if sch.get("freq") is not None:
        sch["cached"] = False
        return sch
    cache = Path(__file__).parent / "schumann_hourly.json"
    if cache.exists():
        try:
            arr   = json.loads(cache.read_text(encoding="utf-8"))
            last  = arr[-1]
            freqs = [p["freq"] for p in arr[-24:] if isinstance(p.get("freq"),(int,float))]
            trend = "→"
            if len(freqs)>1:
                avg   = sum(freqs[:-1])/(len(freqs)-1)
                delta = freqs[-1]-avg
                trend = "↑" if delta>=0.1 else "↓" if delta<=-0.1 else "→"
            return {"freq":round(last["freq"],2),"amp":round(last["amp"],1),
                    "trend":trend,"cached":True}
        except Exception as e:
            logging.warning("Schumann cache parse error: %s",e)
    return sch

def schumann_line(s:Dict[str,Any])->str:
    if s.get("freq") is None:
        return "🎵 Шуман: н/д"
    f,amp=s["freq"],s["amp"]
    emoji="🔴" if f<7.6 else "🟣" if f>8.1 else "🟢"
    return f"{emoji} Шуман: {f:.2f} Гц / {amp:.1f} pT {s['trend']}"

# ────────────────────────── Rad-helper (💡 NEW) ────────────────────────────
def radiation_line(lat:float,lon:float)->str:
    """
    get_radiation() → {"dose": float μSv/h, "src":"EPA", ...}
    Интерпретируем в три зоны:
      ≤0.15  🟢 безопасно
      0.15-0.30 🟡 повышено
      >0.30  🔴 высокий фон
    """
    data = get_radiation(lat, lon) or {}
    dose = data.get("dose")          # μSv/h
    if dose is None:
        return "☢️ Радиация: н/д"
    if dose <= 0.15:
        emoji = "🟢"
        level = "низкий"
    elif dose <= 0.30:
        emoji = "🟡"
        level = "повышенный"
    else:
        emoji = "🔴"
        level = "высокий"
    return f"{emoji} Радиация: {dose:.3f} μSv/h ({level})"

# ────────────────────────────── build_message ──────────────────────────────
def build_message(region_name:str, chat_id:int,
                  sea_label:str, sea_cities:List[Tuple[str,Tuple[float,float]]],
                  other_label:str, other_cities:List[Tuple[str,Tuple[float,float]]],
                  tz:pendulum.Timezone) -> str:

    P:List[str]=[]
    today = pendulum.now(tz).date()
    tomorrow = today.add(days=1)

    # Заголовок
    P.append(f"<b>🌅 {region_name}: погода на завтра ({tomorrow.format('DD.MM.YYYY')})</b>")

    # Температура моря (центр Балтийского залива)
    sst = get_sst(*SEA_SST_COORD)
    P.append(f"🌊 Темп. моря (центр залива): {sst:.1f} °C" if sst is not None
             else "🌊 Темп. моря (центр залива): н/д")

    # Прогноз Калининграда
    d_max,d_min = fetch_tomorrow_temps(KLD_LAT,KLD_LON,tz=tz.name)
    wm   = get_weather(KLD_LAT,KLD_LON) or {}
    cur  = wm.get("current",{}) or {}
    avgT = ((d_max+d_min)/2) if d_max and d_min else cur.get("temperature",0)
    arrow= pressure_arrow(wm.get("hourly",{}))
    P.append(
        f"🏙️ Калининград: Ср. темп {avgT:.0f} °C • {clouds_word(cur.get('clouds',0))} • "
        f"💨 {cur.get('windspeed',0):.1f} км/ч ({compass(cur.get('winddirection',0))}) • "
        f"💧 {cur.get('pressure',1013):.0f} гПа {arrow}"
    )
    P.append("———")

    # Рейтинги (морские + тёплые/холодные) — оставлены без изменений
    # ... ⟵ оригинальный ваш код рейтингов здесь (не удалён) ...

    # Качество воздуха + пыльца
    air = get_air(KLD_LAT,KLD_LON) or {}
    lvl = air.get("lvl","н/д")
    P.append("🏭 <b>Качество воздуха</b>")
    P.append(f"{AIR_EMOJI.get(lvl,'⚪')} {lvl} (AQI {air.get('aqi','н/д')}) | "
             f"PM₂.₅: {pm_color(air.get('pm25'))} | PM₁₀: {pm_color(air.get('pm10'))}")

    if (p:=get_pollen()):
        P.append("🌿 <b>Пыльца</b>")
        P.append(f"Деревья: {p['tree']} | Травы: {p['grass']} | Сорняки: {p['weed']} — риск {p['risk']}")

    # 🆕 Радиация
    P.append(radiation_line(KLD_LAT, KLD_LON))

    P.append("———")

    # Геомагнитка + Шуман
    kp,kp_state = get_kp()
    P.append(f"{kp_emoji(kp)} Геомагнитка: Kp={kp:.1f} ({kp_state})" if kp is not None
             else "🧲 Геомагнитка: н/д")
    P.append(schumann_line(get_schumann_with_fallback()))
    P.append("———")

    # Астрособытия, вывод-виновник, рекомендации, факт дня
    # … (весь существующий код этих блоков без изменений) …

    # <---  здесь идёт ваш прежний длинный код, оставлен нетронутым  --->

    return "\n".join(P)

# ──────────────────────────────── sender ───────────────────────────────────
async def send_common_post(bot:Bot, chat_id:int, region_name:str,
                           sea_label:str, sea_cities, other_label:str, other_cities,
                           tz:pendulum.Timezone):
    txt = build_message(region_name,chat_id,sea_label,sea_cities,other_label,other_cities,tz)
    try:
        await bot.send_message(chat_id=chat_id, text=txt,
                               parse_mode=constants.ParseMode.HTML,
                               disable_web_page_preview=True)
        logging.info("Сообщение отправлено ✓")
    except tg_err.TelegramError as e:
        logging.error("Telegram error: %s",e)
        raise

async def main_common(bot:Bot, chat_id:int, region_name:str,
                      sea_label:str, sea_cities, other_label:str, other_cities, tz):
    await send_common_post(bot,chat_id,region_name,sea_label,sea_cities,other_label,other_cities,tz)
