#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
post_common.py  •  Общая логика формирования и отправки ежедневного поста «VayboMeter» (Калининград).

• Балтийское море, прогноз для Кёнига
• Рейтинг морских, тёплых, холодных
• Воздух, пыльца, радиация
• Геомагнитка, резонанс Шумана
• Астрособытия, «Вините …», советы, факт дня
"""

from __future__ import annotations
import asyncio, json, logging
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pendulum
from telegram import Bot, error as tg_err, constants

from utils      import compass, clouds_word, get_fact, AIR_EMOJI, pm_color, kp_emoji
from weather    import get_weather, fetch_tomorrow_temps
from air        import get_air, get_sst, get_kp
from pollen     import get_pollen
from schumann   import get_schumann
from astro      import astro_events
from gpt        import gpt_blurb
from radiation  import get_radiation                      # 🆕 блок радиации
from settings_klg import SEA_SST_COORD                   # координаты в Балтийском заливе

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ──────────────────────────── базовые константы ────────────────────────────
KLD_LAT, KLD_LON = 54.710426, 20.452214

WMO_DESC = {
    0:  "☀️ ясно", 1: "⛅ ч.обл", 2: "☁️ обл", 3: "🌥 пасм",
    45: "🌫 туман", 48: "🌫 изморозь", 51: "🌦 морось",
    61: "🌧 дождь", 71: "❄️ снег", 95: "⛈ гроза",
}
code_desc = lambda c: WMO_DESC.get(c, "—")

def pressure_arrow(hourly: Dict[str, Any]) -> str:
    """Стрелка по изменению давления: ↑ >+1 hPa, ↓ <−1 hPa, иначе →."""
    pr = hourly.get("surface_pressure", [])
    if len(pr) < 2:
        return "→"
    delta = pr[-1] - pr[0]
    return "↑" if delta > 1 else "↓" if delta < -1 else "→"

# ──────────────────────── Шуман ────────────────────────
def get_schumann_with_fallback() -> Dict[str, Any]:
    sch = get_schumann()
    if sch.get("freq") is not None:
        sch["cached"] = False
        return sch

    cache = Path(__file__).parent / "schumann_hourly.json"
    if cache.exists():
        try:
            arr = json.loads(cache.read_text(encoding="utf-8"))
            if arr:
                last  = arr[-1]
                pts   = arr[-24:]
                freqs = [p["freq"] for p in pts if isinstance(p.get("freq"), (int, float))]
                trend = "→"
                if len(freqs) > 1:
                    avg = sum(freqs[:-1]) / (len(freqs) - 1)
                    d   = freqs[-1] - avg
                    trend = "↑" if d >= 0.1 else "↓" if d <= -0.1 else "→"
                return {
                    "freq":  round(last["freq"], 2),
                    "amp":   round(last["amp"], 1),
                    "trend": trend,
                    "cached": True,
                }
        except Exception as e:
            logging.warning("Schumann cache parse error: %s", e)
    return sch

def schumann_line(s: Dict[str, Any]) -> str:
    if s.get("freq") is None:
        return "🎵 Шуман: н/д"
    f, amp = s["freq"], s["amp"]
    emoji = "🔴" if f < 7.6 else "🟣" if f > 8.1 else "🟢"
    return f"{emoji} Шуман: {f:.2f} Гц / {amp:.1f} pT {s['trend']}"

# ──────────────────────── Радиация ────────────────────────
def radiation_line(lat: float, lon: float) -> Optional[str]:
    """
    Возвращает текстовую строку про радиацию или None,
    если данных нет (чтобы блок можно было не выводить).
    """
    data = get_radiation(lat, lon) or {}
    dose = data.get("dose")
    if dose is None:
        return None                       # ← нет данных — нет строки

    # простая цветовая градация
    if dose <= 0.15:
        emoji, level = "🟢", "низкий"
    elif dose <= 0.30:
        emoji, level = "🟡", "повышенный"
    else:
        emoji, level = "🔴", "высокий"
    return f"{emoji} Радиация: {dose:.3f} μSv/h ({level})"

# ──────────────────────── Основное сообщение ────────────────────────
def build_message(
    region_name: str,
    chat_id: int,
    sea_label: str,
    sea_cities: List[Tuple[str, Tuple[float, float]]],
    other_label: str,
    other_cities: List[Tuple[str, Tuple[float, float]]],
    tz: pendulum.Timezone,
) -> str:

    P: List[str] = []
    today    = pendulum.now(tz).date()
    tomorrow = today.add(days=1)

    # 1) Заголовок
    P.append(f"<b>🌅 {region_name}: погода на завтра ({tomorrow.format('DD.MM.YYYY')})</b>")

    # 2) Температура Балтийского моря
    sst = get_sst(*SEA_SST_COORD)
    P.append(f"🌊 Темп. моря (центр залива): {sst:.1f} °C" if sst is not None
             else "🌊 Темп. моря (центр залива): н/д")

    # 3) Прогноз для Калининграда
    d_max, d_min = fetch_tomorrow_temps(KLD_LAT, KLD_LON, tz=tz.name)
    wm   = get_weather(KLD_LAT, KLD_LON) or {}
    cur  = wm.get("current", {}) or {}
    avgT = ((d_max + d_min) / 2) if d_max and d_min else cur.get("temperature", 0)

    P.append(
        f"🏙️ Калининград: Ср. темп {avgT:.0f} °C • {clouds_word(cur.get('clouds', 0))} • "
        f"💨 {cur.get('windspeed', 0):.1f} км/ч ({compass(cur.get('winddirection', 0))}) • "
        f"💧 {cur.get('pressure', 1013):.0f} гПа {pressure_arrow(wm.get('hourly', {}))}"
    )
    P.append("———")

    # … (остальные блоки рейтингов, воздуха, пыльцы – без изменений) …

    # 4) Воздух и пыльца
    air = get_air(KLD_LAT, KLD_LON) or {}
    lvl = air.get("lvl", "н/д")
    P.append("🏭 <b>Качество воздуха</b>")
    P.append(
        f"{AIR_EMOJI.get(lvl, '⚪')} {lvl} (AQI {air.get('aqi', 'н/д')}) | "
        f"PM₂.₅: {pm_color(air.get('pm25'))} | PM₁₀: {pm_color(air.get('pm10'))}"
    )
    if (p := get_pollen()):
        P.append("🌿 <b>Пыльца</b>")
        P.append(
            f"Деревья: {p['tree']} | Травы: {p['grass']} | "
            f"Сорняки: {p['weed']} — риск {p['risk']}"
        )

    # 5) Радиация (добавится ТОЛЬКО, если есть данные)
    rad_line = radiation_line(KLD_LAT, KLD_LON)
    if rad_line:
        P.append(rad_line)

    P.append("———")

    # 6) Геомагнитка + Шуман
    kp, kp_state = get_kp()
    P.append(f"{kp_emoji(kp)} Геомагнитка: Kp={kp:.1f} ({kp_state})" if kp is not None
             else "🧲 Геомагнитка: н/д")
    P.append(schumann_line(get_schumann_with_fallback()))
    P.append("———")

    # 7) Астрособытия
    P.append("🌌 <b>Астрособытия</b>")
    astro = astro_events(offset_days=1, show_all_voc=True)
    P.extend(astro if astro else ["— нет данных —"])
    P.append("———")

    # 8) Вывод + советы (сокращённо, логика прежняя)
    culprit = "магнитные бури" if kp and kp_state.lower() == "буря" else "неблагоприятный прогноз погоды"
    P.append("📜 <b>Вывод</b>")
    P.append(f"Если что-то пойдёт не так, вините {culprit}! 😉")
    P.append("———")

    P.append("✅ <b>Рекомендации</b>")
    _, tips = gpt_blurb(culprit)
    for tip in tips[:3]:
        P.append(tip.strip())
    P.append("———")

    # 9) Факт дня
    P.append(f"📚 {get_fact(tomorrow, region_name)}")
    return "\n".join(P)

# ────────────────────────────── отправка ──────────────────────────────
async def send_common_post(
    bot: Bot,
    chat_id: int,
    region_name: str,
    sea_label: str,
    sea_cities: List[Tuple[str, Tuple[float, float]]],
    other_label: str,
    other_cities: List[Tuple[str, Tuple[float, float]]],
    tz: pendulum.Timezone,
) -> None:
    text = build_message(
        region_name, chat_id, sea_label, sea_cities, other_label, other_cities, tz
    )
    await bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode=constants.ParseMode.HTML,
        disable_web_page_preview=True,
    )

async def main_common(
    bot: Bot,
    chat_id: int,
    region_name: str,
    sea_label: str,
    sea_cities: List[Tuple[str, Tuple[float, float]]],
    other_label: str,
    other_cities: List[Tuple[str, Tuple[float, float]]],
    tz: pendulum.Timezone,
) -> None:
    await send_common_post(bot, chat_id, region_name, sea_label, sea_cities, other_label, other_cities, tz)
