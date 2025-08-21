#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
post_common.py — VayboMeter (Калининград).

• Море, прогноз Кёнига (день/ночь, м/с, RH min–max, давление)
• Рейтинги городов (d/n, код погоды словами + 🌊)
• Air (+ 🔥 Задымление, если не низкое), пыльца, радиация
• Kp, Шуман
• Астрособытия (знак как ♈ … ♓)
• «Вините …», рекомендации, факт дня
"""

from __future__ import annotations
import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional, Union

import pendulum
from telegram import Bot, constants

from utils import (
    compass, clouds_word, get_fact, AIR_EMOJI, pm_color, kp_emoji,
    kmh_to_ms, smoke_index, pressure_trend, _get,
)
from weather     import get_weather, fetch_tomorrow_temps, day_night_stats
import air as airmod                       # может быть старая версия — ниже обернём
from pollen      import get_pollen
from schumann    import get_schumann
from astro       import astro_events
from gpt         import gpt_blurb
from radiation   import get_radiation
from settings_klg import SEA_SST_COORD     # точка в заливе

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ────────────────────────── константы ──────────────────────────
KLD_LAT, KLD_LON = 54.710426, 20.452214

# Мэппинг WMO-кодов в короткие текст+эмодзи
WMO_DESC = {
    0:"☀️ ясно", 1:"⛅ ч.обл", 2:"☁️ обл", 3:"🌥 пасм",
    45:"🌫 туман", 48:"🌫 изморозь", 51:"🌦 морось",
    61:"🌧 дождь", 71:"❄️ снег", 95:"⛈ гроза",
}
def code_desc(c: Any) -> Optional[str]:
    return WMO_DESC.get(int(c)) if isinstance(c, (int, float)) and int(c) in WMO_DESC else None

# ───────────── безопасные обёртки над air.* ─────────────
def _nearest_hour_val(times: List[str], vals: List[Any]) -> Optional[float]:
    if not times or not vals or len(times) != len(vals):
        return None
    try:
        import time as _t
        now_iso = _t.strftime("%Y-%m-%dT%H:00", _t.gmtime())
        idxs = [i for i, t in enumerate(times) if isinstance(t, str) and t <= now_iso]
        idx = max(idxs) if idxs else 0
        v = vals[idx]
        return float(v) if isinstance(v, (int, float)) else None
    except Exception:
        return None

def _get_sst_safe(lat: float, lon: float) -> Optional[float]:
    try:
        if hasattr(airmod, "get_sst"):
            return airmod.get_sst(lat, lon)  # type: ignore[attr-defined]
    except Exception as e:
        logging.warning("air.get_sst error: %s", e)
    try:
        j = _get(
            "https://marine-api.open-meteo.com/v1/marine",
            latitude=lat, longitude=lon,
            hourly="sea_surface_temperature", timezone="UTC",
        )
        if not j or "hourly" not in j:
            return None
        h = j["hourly"]
        return _nearest_hour_val(h.get("time", []) or [], h.get("sea_surface_temperature", []) or [])
    except Exception as e:
        logging.warning("local SST fallback error: %s", e)
        return None

def _get_air_fallback_openmeteo(lat: float, lon: float) -> Dict[str, Any]:
    try:
        j = _get(
            "https://air-quality-api.open-meteo.com/v1/air-quality",
            latitude=lat, longitude=lon,
            hourly="pm10,pm2_5,us_aqi", timezone="UTC",
        )
        if not j or "hourly" not in j:
            return {}
        h = j["hourly"]
        t = h.get("time", []) or []
        aqi  = _nearest_hour_val(t, h.get("us_aqi", []) or [])
        pm25 = _nearest_hour_val(t, h.get("pm2_5", []) or [])
        pm10 = _nearest_hour_val(t, h.get("pm10", []) or [])
        aqi_norm: Union[float,str] = float(aqi) if isinstance(aqi,(int,float)) and aqi >= 0 else "н/д"
        return {
            "lvl": ("н/д" if aqi_norm == "н/д" else ("хороший" if aqi_norm <= 50 else "умеренный" if aqi_norm <= 100 else "вредный" if aqi_norm <= 150 else "оч. вредный" if aqi_norm <= 200 else "опасный")),
            "aqi": aqi_norm,
            "pm25": float(pm25) if isinstance(pm25,(int,float)) and pm25 >= 0 else None,
            "pm10": float(pm10) if isinstance(pm10,(int,float)) and pm10 >= 0 else None,
        }
    except Exception as e:
        logging.warning("Open-Meteo AQ fallback error: %s", e)
        return {}

def _air_is_empty(d: Dict[str, Any]) -> bool:
    return not d or (str(d.get("aqi","н/д")) == "н/д" and d.get("pm25") in (None,"н/д") and d.get("pm10") in (None,"н/д"))

def _get_air_safe(lat: float, lon: float) -> Dict[str, Any]:
    res: Dict[str, Any] = {}
    try:
        if hasattr(airmod, "get_air"):
            res = airmod.get_air(lat, lon)  # type: ignore[attr-defined]
    except Exception as e:
        logging.warning("air.get_air error: %s", e)
    if _air_is_empty(res):
        res = _get_air_fallback_openmeteo(lat, lon)
    if _air_is_empty(res):  # совсем нет данных — заполним дефолтом
        res = {"lvl": "н/д", "aqi": "н/д", "pm25": None, "pm10": None}
    return res

def _parse_kp_from_table(data: Any) -> Optional[float]:
    if not isinstance(data, list) or not data or not isinstance(data[0], list):
        return None
    for row in reversed(data[1:]):
        try:
            return float(str(row[-1]).rstrip("Z").replace(",", "."))
        except Exception:
            continue
    return None

def _parse_kp_from_dicts(data: Any) -> Optional[float]:
    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        return None
    for item in reversed(data):
        raw = item.get("kp_index") or item.get("estimated_kp") or item.get("kp")
        if raw is None:
            continue
        try:
            return float(str(raw).rstrip("Z").replace(",", "."))
        except Exception:
            continue
    return None

def _get_kp_fallback() -> Tuple[Optional[float], str]:
    urls = [
        "https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json",
        "https://services.swpc.noaa.gov/json/planetary_k_index_1m.json",
    ]
    for u in urls:
        try:
            d = _get(u)
            if not d: 
                continue
            kp = _parse_kp_from_table(d) if isinstance(d, list) and d and isinstance(d[0], list) else _parse_kp_from_dicts(d)
            if kp is not None:
                state = "спокойно" if kp < 3 else ("неспокойно" if kp < 5 else "буря")
                return kp, state
        except Exception as e:
            logging.warning("Kp fallback error: %s", e)
            continue
    return None, "н/д"

def _get_kp_safe() -> Tuple[Optional[float], str]:
    try:
        if hasattr(airmod, "get_kp"):
            return airmod.get_kp()  # type: ignore[attr-defined]
    except Exception as e:
        logging.warning("air.get_kp error: %s", e)
    return _get_kp_fallback()

# ───────────── Шуман ─────────────
def get_schumann_with_fallback() -> Dict[str, Any]:
    sch = get_schumann()
    if sch.get("freq") is not None:
        sch["cached"] = False
        return sch
    cache = Path(__file__).parent / "schumann_hourly.json"
    if cache.exists():
        try:
            arr = json.loads(cache.read_text("utf-8"))
            if arr:
                last = arr[-1]; pts = arr[-24:]
                freqs = [p.get("freq") for p in pts if isinstance(p.get("freq"), (int, float))]
                trend = "→"
                if len(freqs) > 1:
                    avg = sum(freqs[:-1]) / (len(freqs) - 1)
                    d = freqs[-1] - avg
                    trend = "↑" if d >= .1 else "↓" if d <= -.1 else "→"
                return {
                    "freq": round(last.get("freq", 0.0), 2),
                    "amp": round(last.get("amp", 0.0), 1),
                    "trend": trend,
                    "cached": True
                }
        except Exception as e:
            logging.warning("Schumann cache err: %s", e)
    return sch

def schumann_line(s: Dict[str, Any]) -> str:
    if s.get("freq") is None:
        return "🎵 Шуман: н/д"
    f, amp = s["freq"], s["amp"]
    e = "🔴" if f < 7.6 else "🟣" if f > 8.1 else "🟢"
    return f"{e} Шуман: {f:.2f} Гц / {amp:.1f} pT {s.get('trend','')}"

# ───────────── Радиация ─────────────
def radiation_line(lat: float, lon: float) -> str | None:
    data = get_radiation(lat, lon) or {}
    dose = data.get("dose")
    if dose is None:
        return None
    if dose <= 0.15:
        emoji, lvl = "🟢", "низкий"
    elif dose <= 0.30:
        emoji, lvl = "🟡", "повышенный"
    else:
        emoji, lvl = "🔴", "высокий"
    return f"{emoji} Радиация: {dose:.3f} μSv/h ({lvl})"

# ───────────── Зодиаки → символы ─────────────
ZODIAC = {
    "Овен":"♈","Телец":"♉","Близнецы":"♊","Рак":"♋","Лев":"♌",
    "Дева":"♍","Весы":"♎","Скорпион":"♏","Стрелец":"♐",
    "Козерог":"♑","Водолей":"♒","Рыбы":"♓",
}
def zsym(s: str) -> str:
    for name, sym in ZODIAC.items():
        s = s.replace(name, sym)
    return s

# ───────────── сообщение ─────────────
def build_message(region_name: str, chat_id: int,
                  sea_label: str, sea_cities, other_label: str,
                  other_cities, tz: pendulum.Timezone) -> str:

    P: List[str] = []
    today = pendulum.now(tz).date()
    tom   = today.add(days=1)

    # Заголовок
    P.append(f"<b>🌅 {region_name}: погода на завтра ({tom.format('DD.MM.YYYY')})</b>")

    # Море (средняя SST в точке)
    sst = _get_sst_safe(*SEA_SST_COORD)
    P.append(f"🌊 Темп. моря (центр залива): {sst:.1f} °C" if sst is not None
             else "🌊 Темп. моря (центр залива): н/д")

    # Калининград — день/ночь, код словами (если надёжен), ветер м/с, RH min–max, давление
    stats = day_night_stats(KLD_LAT, KLD_LON, tz=tz.name)
    wm    = get_weather(KLD_LAT, KLD_LON) or {}
    cur   = wm.get("current", {}) or {}
    wcarr = (wm.get("daily", {}) or {}).get("weathercode", [])
    wc    = wcarr[1] if isinstance(wcarr, list) and len(wcarr) > 1 else cur.get("weathercode")
    wind_ms = kmh_to_ms(cur.get("windspeed"))
    rh_min = stats.get("rh_min"); rh_max = stats.get("rh_max")
    t_day_max = stats.get("t_day_max"); t_night_min = stats.get("t_night_min")

    # давление
    pressure_val = cur.get("pressure")
    if pressure_val is None:
        hp = (wm.get("hourly", {}) or {}).get("surface_pressure", [])
        if isinstance(hp, list) and hp:
            pressure_val = hp[-1]
    press_part = f"{int(round(pressure_val))} гПа {pressure_trend(wm)}" if isinstance(pressure_val, (int, float)) else "н/д"

    desc = code_desc(wc)
    kal_parts = [
        f"🏙️ Калининград: дн/ночь {t_day_max:.0f}/{t_night_min:.0f} °C" if (t_day_max is not None and t_night_min is not None)
        else "🏙️ Калининград: дн/ночь н/д",
        desc or None,
        f"💨 {wind_ms:.1f} м/с ({compass(cur.get('winddirection', 0))})" if wind_ms is not None else f"💨 н/д ({compass(cur.get('winddirection', 0))})",
        (f"💧 RH {rh_min:.0f}–{rh_max:.0f}%" if rh_min is not None and rh_max is not None else None),
        f"🔹 {press_part}",
    ]
    P.append(" • ".join([x for x in kal_parts if x]))
    P.append("———")

    # Морские города (топ‑5)
    temps_sea: Dict[str, Tuple[float, float, int, float | None]] = {}
    for city, (la, lo) in sea_cities:
        tmax, tmin = fetch_tomorrow_temps(la, lo, tz=tz.name)
        if tmax is None:
            continue
        wcx = (get_weather(la, lo) or {}).get("daily", {}).get("weathercode", [])
        wcx = wcx[1] if isinstance(wcx, list) and len(wcx) > 1 else 0
        temps_sea[city] = (tmax, tmin or tmax, wcx, _get_sst_safe(la, lo))
    if temps_sea:
        P.append(f"🎖️ <b>{sea_label}</b>")
        medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
        for i, (city, (d, n, wcx, sst_c)) in enumerate(sorted(temps_sea.items(),
                                                              key=lambda kv: kv[1][0], reverse=True)[:5]):
            line = f"{medals[i]} {city}: {d:.1f}/{n:.1f}"
            descx = code_desc(wcx)
            if descx:
                line += f", {descx}"
            if sst_c is not None:
                line += f" 🌊 {sst_c:.1f}"
            P.append(line)
        P.append("———")

    # Тёплые/холодные (топ‑3 / топ‑3)
    temps_oth: Dict[str, Tuple[float, float, int]] = {}
    for city, (la, lo) in other_cities:
        tmax, tmin = fetch_tomorrow_temps(la, lo, tz=tz.name)
        if tmax is None:
            continue
        wcx = (get_weather(la, lo) or {}).get("daily", {}).get("weathercode", [])
        wcx = wcx[1] if isinstance(wcx, list) and len(wcx) > 1 else 0
        temps_oth[city] = (tmax, tmin or tmax, wcx)
    if temps_oth:
        P.append("🔥 <b>Тёплые города, °C</b>")
        for city, (d, n, wcx) in sorted(temps_oth.items(), key=lambda kv: kv[1][0], reverse=True)[:3]:
            descx = code_desc(wcx)
            P.append(f"   • {city}: {d:.1f}/{n:.1f}" + (f" {descx}" if descx else ""))
        P.append("❄️ <b>Холодные города, °C</b>")
        for city, (d, n, wcx) in sorted(temps_oth.items(), key=lambda kv: kv[1][0])[:3]:
            descx = code_desc(wcx)
            P.append(f"   • {city}: {d:.1f}/{n:.1f}" + (f" {descx}" if descx else ""))
        P.append("———")

    # Air + пыльца + радиация
    air = _get_air_safe(KLD_LAT, KLD_LON) or {}
    lvl = air.get("lvl", "н/д")
    P.append("🏭 <b>Качество воздуха</b>")
    P.append(f"{AIR_EMOJI.get(lvl,'⚪')} {lvl} (AQI {air.get('aqi','н/д')}) | "
             f"PM₂.₅: {pm_color(air.get('pm25'))} | PM₁₀: {pm_color(air.get('pm10'))}")
    em, lbl = smoke_index(air.get("pm25"), air.get("pm10"))
    if lbl != "низкое":
        P.append(f"🔥 Задымление: {em} {lbl}")
    if (p := get_pollen()):
        P.append("🌿 <b>Пыльца</b>")
        P.append(f"Деревья: {p['tree']} | Травы: {p['grass']} | Сорняки: {p['weed']} — риск {p['risk']}")
    if (rl := radiation_line(KLD_LAT, KLD_LON)):
        P.append(rl)
    P.append("———")

    # Kp + Шуман
    kp, ks = _get_kp_safe()
    P.append(f"{kp_emoji(kp)} Геомагнитка: Kp={kp:.1f} ({ks})" if kp is not None else "🧲 Геомагнитка: н/д")
    P.append(schumann_line(get_schumann_with_fallback()))
    P.append("———")

    # Астрособытия
    P.append("🌌 <b>Астрособытия</b>")
    astro = astro_events(offset_days=1, show_all_voc=True)
    if astro:
        P.extend([zsym(line) for line in astro])
    else:
        P.append("— нет данных —")
    P.append("———")

    # Вывод + советы
    culprit = "магнитные бури" if kp is not None and ks and ks.lower() == "буря" else "неблагоприятный прогноз погоды"
    P.append("📜 <b>Вывод</b>")
    P.append(f"Если что-то пойдёт не так, вините {culprit}! 😉")
    P.append("———")
    P.append("✅ <b>Рекомендации</b>")
    try:
        _, tips = gpt_blurb(culprit)
        for t in tips[:3]:
            P.append(t.strip())
    except Exception:
        P.append("— больше воды, меньше стресса, нормальный сон")

    P.append("———")
    P.append(f"📚 {get_fact(tom, region_name)}")
    return "\n".join(P)

# ───────────── отправка ─────────────
async def send_common_post(bot: Bot, chat_id: int, region_name: str,
                           sea_label: str, sea_cities, other_label: str,
                           other_cities, tz):
    msg = build_message(region_name, chat_id, sea_label, sea_cities,
                        other_label, other_cities, tz)
    await bot.send_message(chat_id=chat_id, text=msg,
                           parse_mode=constants.ParseMode.HTML,
                           disable_web_page_preview=True)

async def main_common(bot: Bot, chat_id: int, region_name: str,
                      sea_label: str, sea_cities, other_label: str,
                      other_cities, tz):
    await send_common_post(bot, chat_id, region_name, sea_label,
                           sea_cities, other_label, other_cities, tz)
