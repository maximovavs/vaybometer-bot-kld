#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
post_common.py — VayboMeter (Калининград).

• Море, прогноз Кёнига (день/ночь, м/с, RH min–max, давление)
• Рейтинги городов (d/n, код погоды словами + 🌊)
• Air (IQAir/ваш источник) + Safecast (PM и CPM→μSv/h, мягкая шкала 🟢🟡🔵), пыльца
• Радиация из офиц. источника (строгая шкала 🟢🟡🔴)
• Геомагнитка: Kp со «свежестью» + Солнечный ветер (Bz/Bt/v/n + статус)
• Шуман (фоллбэк чтения JSON; либо прямой импорт schumann.get_schumann())
• Астрособытия (микро-LLM 2–3 строки + VoC, извлекаем из lunar_calendar.json)
• Умный «Вывод», рекомендации, факт дня

(+ Water highlights) Для морских городов — короткая строка «что сейчас ОТЛИЧНО»
для водных активностей (Кайт/Винг/Винд; SUP; Сёрф) + подсказка по гидрокостюму.
"""

from __future__ import annotations
import os
import re
import json
import html
import asyncio
import logging
import math
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional, Union

import pendulum
from telegram import Bot, constants

from utils        import compass, get_fact, AIR_EMOJI, pm_color, kp_emoji, kmh_to_ms, smoke_index
from weather      import get_weather, fetch_tomorrow_temps, day_night_stats
from air          import get_air, get_sst, get_kp, get_solar_wind
from pollen       import get_pollen
from radiation    import get_radiation
from gpt          import gpt_blurb, gpt_complete  # микро-LLM для «Астрособытий»

# (опц.) волна из Open-Meteo Marine
try:
    import requests  # type: ignore
except Exception:
    requests = None  # type: ignore

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

DEBUG_WATER = os.getenv("DEBUG_WATER", "").strip().lower() in ("1", "true", "yes", "on")
DISABLE_SCHUMANN = os.getenv("DISABLE_SCHUMANN", "").strip().lower() in ("1","true","yes","on")

# Неразрывный пробел для «27/18 °C»
NBSP = "\u00A0"

# Режим поста: evening (на завтра) / morning (на сегодня)
POST_MODE = (os.getenv("POST_MODE") or os.getenv("MODE") or "evening").strip().lower()
DAY_OFFSET = 0 if POST_MODE == "morning" else 1

# ────────────────────────── базовые константы ──────────────────────────
KLD_LAT, KLD_LON = 54.710426, 20.452214
CPM_TO_USVH = float(os.getenv("CPM_TO_USVH", "0.000571"))

CACHE_DIR = Path(".cache"); CACHE_DIR.mkdir(exist_ok=True, parents=True)
USE_DAILY_LLM = os.getenv("DISABLE_LLM_DAILY", "").strip().lower() not in ("1", "true", "yes", "on")

# ────────────────────────── LLM safety ──────────────────────────
DISABLE_LLM_TIPS = os.getenv("DISABLE_LLM_TIPS", "").strip().lower() in ("1", "true", "yes", "on")
ASTRO_LLM_TEMP = float(os.getenv("ASTRO_LLM_TEMP", "0.2"))

SAFE_TIPS_FALLBACKS = {
    "здоровый день": ["🚶 30–40 мин лёгкой активности.", "🥤 Пейте воду и делайте короткие паузы.", "😴 Спланируйте 7–9 часов сна."],
    "плохая погода": ["🧥 Тёплые слои и непромокаемая куртка.", "🌧 Перенесите дела под крышу; больше пауз.", "🚗 Заложите время на дорогу."],
    "магнитные бури": ["🧘 Уменьшите перегрузки, больше отдыха.", "💧 Больше воды и магний/калий в рационе.", "😴 Режим сна, меньше экранов вечером."],
    "плохой воздух": ["😮‍💨 Сократите время на улице и проветривания.", "🪟 Используйте фильтры/проветривание по ситуации.", "🏃 Тренировки — в помещении."],
    "волны Шумана": ["🧘 Спокойный темп дня, без авралов.", "🍵 Лёгкая еда, тёплые напитки.", "😴 Лёгкая прогулка и ранний сон."],
}

def _escape_html(s: str) -> str:
    return html.escape(str(s), quote=False)

def _sanitize_line(s: str, max_len: int = 140) -> str:
    s = " ".join(str(s).split())
    s = re.sub(r"(.)\1{3,}", r"\1\1\1", s)
    s = s[:max_len-1] + "…" if len(s) > max_len else s
    return _escape_html(s).strip()

def _looks_gibberish(s: str) -> bool:
    if re.search(r"(.)\1{5,}", s):  # «щщщщщ…»
        return True
    letters = re.findall(r"[A-Za-zА-Яа-яЁё]", s)
    return (len(set(letters)) <= 2 and len("".join(letters)) >= 10)

def safe_tips(theme: str) -> list[str]:
    k = (theme or "здоровый день").strip().lower()
    if DISABLE_LLM_TIPS:
        return SAFE_TIPS_FALLBACKS.get(k, SAFE_TIPS_FALLBACKS["здоровый день"])
    try:
        _, tips = gpt_blurb(k)
        out: list[str] = []
        for t in (tips or [])[:3]:
            t = _sanitize_line(t, max_len=140)
            if not t or _looks_gibberish(t): continue
            out.append(t)
        if out: return out
    except Exception as e:
        logging.warning("LLM tips failed: %s", e)
    return SAFE_TIPS_FALLBACKS.get(k, SAFE_TIPS_FALLBACKS["здоровый день"])

# ────────────────────────── ENV TUNABLES (водные активности) ──────────────────────────
# KITE — м/с
KITE_WIND_MIN        = float(os.getenv("KITE_WIND_MIN",        "6"))
KITE_WIND_GOOD_MIN   = float(os.getenv("KITE_WIND_GOOD_MIN",   "7"))
KITE_WIND_GOOD_MAX   = float(os.getenv("KITE_WIND_GOOD_MAX",   "12"))
KITE_WIND_STRONG_MAX = float(os.getenv("KITE_WIND_STRONG_MAX", "18"))
KITE_GUST_RATIO_BAD  = float(os.getenv("KITE_GUST_RATIO_BAD",  "1.5"))
KITE_WAVE_WARN       = float(os.getenv("KITE_WAVE_WARN",       "2.5"))

# SUP — м/с и м
SUP_WIND_GOOD_MAX     = float(os.getenv("SUP_WIND_GOOD_MAX",     "4"))
SUP_WIND_OK_MAX       = float(os.getenv("SUP_WIND_OK_MAX",       "6"))
SUP_WIND_EDGE_MAX     = float(os.getenv("SUP_WIND_EDGE_MAX",     "8"))
SUP_WAVE_GOOD_MAX     = float(os.getenv("SUP_WAVE_GOOD_MAX",     "0.6"))
SUP_WAVE_OK_MAX       = float(os.getenv("SUP_WAVE_OK_MAX",       "0.8"))
SUP_WAVE_BAD_MIN      = float(os.getenv("SUP_WAVE_BAD_MIN",      "1.5"))
OFFSHORE_SUP_WIND_MIN = float(os.getenv("OFFSHORE_SUP_WIND_MIN", "5"))

# SURF — волна (м) и ветер (м/с)
SURF_WAVE_GOOD_MIN   = float(os.getenv("SURF_WAVE_GOOD_MIN",   "0.9"))
SURF_WAVE_GOOD_MAX   = float(os.getenv("SURF_WAVE_GOOD_MAX",   "2.5"))
SURF_WIND_MAX        = float(os.getenv("SURF_WIND_MAX",        "10"))

# Wetsuit thresholds (°C)
WSUIT_NONE   = float(os.getenv("WSUIT_NONE",   "22"))  # ≥22 — можно без гидрика/лайкра
WSUIT_SHORTY = float(os.getenv("WSUIT_SHORTY", "20"))
WSUIT_32     = float(os.getenv("WSUIT_32",     "17"))
WSUIT_43     = float(os.getenv("WSUIT_43",     "14"))
WSUIT_54     = float(os.getenv("WSUIT_54",     "12"))
WSUIT_65     = float(os.getenv("WSUIT_65",     "10"))

# ────────────────────────── споты и профиль береговой линии ──────────────────────────
# face = направление «к морю» (куда смотрит берег). Для оншора ветер дует примерно с этого курса.
SHORE_PROFILE: Dict[str, float] = {
    "Kaliningrad": 270.0,   # для консистентности, хотя не морской
    "Zelenogradsk": 285.0,
    "Svetlogorsk":  300.0,
    "Pionersky":    300.0,
    "Yantarny":     300.0,
    "Baltiysk":     270.0,
    "Primorsk":     265.0,
}

SPOT_SHORE_PROFILE: Dict[str, float] = {
    "Zelenogradsk":           285.0,
    "Svetlogorsk":            300.0,
    "Pionersky":              300.0,
    "Yantarny":               300.0,
    "Baltiysk (Spit)":        270.0,
    "Baltiysk (North beach)": 280.0,
    "Primorsk":               265.0,
    "Donskoye":               300.0,
}

def _norm_key(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())

_SPOT_INDEX = {_norm_key(k): k for k in SPOT_SHORE_PROFILE.keys()}

def _parse_deg(val: Optional[str]) -> Optional[float]:
    if not val: return None
    try: return float(str(val).strip())
    except Exception: return None

def _env_city_key(city: str) -> str:
    return city.upper().replace(" ", "_")

def _spot_from_env(name: Optional[str]) -> Optional[Tuple[str, float]]:
    if not name: return None
    key = _norm_key(name)
    real = _SPOT_INDEX.get(key)
    if real: return real, SPOT_SHORE_PROFILE[real]
    return None

def _shore_face_for_city(city: str) -> Tuple[Optional[float], Optional[str]]:
    # 1) прямой override углом
    face_env = _parse_deg(os.getenv(f"SHORE_FACE_{_env_city_key(city)}"))
    if face_env is not None:
        return face_env, f"ENV:SHORE_FACE_{_env_city_key(city)}"
    # 2) спот для города
    spot_env = os.getenv(f"SPOT_{_env_city_key(city)}")
    sp = _spot_from_env(spot_env) if spot_env else None
    # 3) глобальный активный спот
    if not sp:
        sp = _spot_from_env(os.getenv("ACTIVE_SPOT"))
    if sp:
        label, deg = sp
        return deg, label
    # 4) дефолт
    if city in SHORE_PROFILE:
        return SHORE_PROFILE[city], city
    return None, None

# ───────────── утилиты общего кода ─────────────
def _as_tz(tz: Union[pendulum.Timezone, str]) -> pendulum.Timezone:
    return pendulum.timezone(tz) if isinstance(tz, str) else tz

WMO_DESC = {0:"☀️ ясно",1:"⛅ ч.обл",2:"☁️ обл",3:"🌥 пасм",45:"🌫 туман",48:"🌫 изморозь",51:"🌦 морось",61:"🌧 дождь",71:"❄️ снег",95:"⛈ гроза"}
def code_desc(c: Any) -> Optional[str]:
    try: return WMO_DESC.get(int(c))
    except Exception: return None

def _pick(d: Dict[str, Any], *keys, default=None):
    for k in keys:
        if k in d: return d[k]
    return default

def _hourly_times(wm: Dict[str, Any]) -> List[pendulum.DateTime]:
    hourly = wm.get("hourly") or {}
    times = hourly.get("time") or hourly.get("time_local") or hourly.get("timestamp") or []
    out: List[pendulum.DateTime] = []
    for t in times:
        try: out.append(pendulum.parse(str(t)))
        except Exception: continue
    return out

def _nearest_index_for_day(times: List[pendulum.DateTime], date_obj: pendulum.Date, prefer_hour: int, tz: pendulum.Timezone) -> Optional[int]:
    if not times: return None
    target = pendulum.datetime(date_obj.year, date_obj.month, date_obj.day, prefer_hour, 0, tz=tz)
    best_i, best_diff = None, None
    for i, dt in enumerate(times):
        try: dt_local = dt.in_tz(tz)
        except Exception: dt_local = dt
        if dt_local.date() != date_obj: continue
        diff = abs((dt_local - target).total_seconds())
        if best_diff is None or diff < best_diff:
            best_i, best_diff = i, diff
    return best_i

def _circular_mean_deg(deg_list: List[float]) -> Optional[float]:
    if not deg_list: return None
    x = sum(math.cos(math.radians(d)) for d in deg_list)
    y = sum(math.sin(math.radians(d)) for d in deg_list)
    if x == 0 and y == 0: return None
    ang = math.degrees(math.atan2(y, x))
    return (ang + 360.0) % 360.0

# === дата-утилиты/обобщения для «сегодня/завтра» ============================

def _date_for_offset(tz: pendulum.Timezone, offset_days: int) -> pendulum.Date:
    return pendulum.now(tz).add(days=offset_days).date()

def pick_header_metrics(wm: Dict[str, Any], tz: pendulum.Timezone, offset_days: int) -> Tuple[Optional[float], Optional[int], Optional[int], str]:
    """Обобщение: ветер/направление/давление на указанную дату (≈полдень)."""
    hourly = wm.get("hourly") or {}
    times = _hourly_times(wm)
    target_date = _date_for_offset(tz, offset_days)

    spd_arr = _pick(hourly, "windspeed_10m", "windspeed", "wind_speed_10m", "wind_speed", default=[])
    dir_arr = _pick(hourly, "winddirection_10m", "winddirection", "wind_dir_10m", "wind_dir", default=[])
    prs_arr = hourly.get("surface_pressure", []) or hourly.get("pressure", [])

    if times:
        idx_noon = _nearest_index_for_day(times, target_date, prefer_hour=12, tz=tz)
        idx_morn = _nearest_index_for_day(times, target_date, prefer_hour=6,  tz=tz)
    else:
        idx_noon = idx_morn = None

    wind_ms = None; wind_dir = None; press_val = None; trend = "→"

    if idx_noon is not None:
        try: spd  = float(spd_arr[idx_noon]) if idx_noon < len(spd_arr) else None
        except Exception: spd = None
        try: wdir = float(dir_arr[idx_noon]) if idx_noon < len(dir_arr) else None
        except Exception: wdir = None
        try: p_noon = float(prs_arr[idx_noon]) if idx_noon < len(prs_arr) else None
        except Exception: p_noon = None
        try: p_morn = float(prs_arr[idx_morn]) if (idx_morn is not None and idx_morn < len(prs_arr)) else None
        except Exception: p_morn = None

        wind_ms  = kmh_to_ms(spd) if isinstance(spd,  (int, float)) else None
        wind_dir = int(round(wdir)) if isinstance(wdir, (int, float)) else None
        press_val = int(round(p_noon)) if isinstance(p_noon, (int, float)) else None
        if isinstance(p_noon, (int, float)) and isinstance(p_morn, (int, float)):
            diff = p_noon - p_morn
            trend = "↑" if diff >= 0.3 else "↓" if diff <= -0.3 else "→"

    if wind_ms is None and times:
        idxs = [i for i, t in enumerate(times) if t.in_tz(tz).date() == target_date]
        if idxs:
            try: speeds = [float(spd_arr[i]) for i in idxs if i < len(spd_arr)]
            except Exception: speeds = []
            try: dirs   = [float(dir_arr[i]) for i in idxs if i < len(dir_arr)]
            except Exception: dirs = []
            try: prs    = [float(prs_arr[i]) for i in idxs if i < len(prs_arr)]
            except Exception: prs = []
            if speeds: wind_ms = kmh_to_ms(sum(speeds)/len(speeds))
            mean_dir = _circular_mean_deg(dirs)
            wind_dir = int(round(mean_dir)) if mean_dir is not None else wind_dir
            if prs: press_val = int(round(sum(prs)/len(prs)))

    if wind_ms is None or wind_dir is None or press_val is None:
        cur = (wm.get("current") or wm.get("current_weather") or {})
        if wind_ms is None:
            spd = _pick(cur, "windspeed_10m", "windspeed", "wind_speed_10m", "wind_speed")
            wind_ms = kmh_to_ms(spd) if isinstance(spd, (int, float)) else wind_ms
        if wind_dir is None:
            wdir = _pick(cur, "winddirection_10m", "winddirection", "wind_dir_10m", "wind_dir")
            if isinstance(wdir, (int, float)): wind_dir = int(round(float(wdir)))
        if press_val is None:
            pcur = _pick(cur, "surface_pressure", "pressure")
            if isinstance(pcur, (int, float)): press_val = int(round(float(pcur)))
    return wind_ms, wind_dir, press_val, trend

def pick_tomorrow_header_metrics(wm: Dict[str, Any], tz: pendulum.Timezone) -> Tuple[Optional[float], Optional[int], Optional[int], str]:
    """Оставлено для совместимости; теперь оборачивает pick_header_metrics(..., offset=1)."""
    return pick_header_metrics(wm, tz, 1)

def _hourly_indices_for_offset(wm: Dict[str, Any], tz: pendulum.Timezone, offset_days: int) -> List[int]:
    times = _hourly_times(wm)
    target = _date_for_offset(tz, offset_days)
    idxs: List[int] = []
    for i, dt in enumerate(times):
        try:
            if dt.in_tz(tz).date() == target: idxs.append(i)
        except Exception: pass
    return idxs

def storm_flags_for_date_offset(wm: Dict[str, Any], tz: pendulum.Timezone, offset_days: int) -> Dict[str, Any]:
    hourly = wm.get("hourly") or {}
    idxs = _hourly_indices_for_offset(wm, tz, offset_days)
    if not idxs: return {"warning": False}

    def _arr(*names, default=None):
        v = _pick(hourly, *names, default=default)
        return v if isinstance(v, list) else []

    def _vals(arr):
        out=[]
        for i in idxs:
            if i < len(arr):
                try: out.append(float(arr[i]))
                except Exception: pass
        return out

    speeds_kmh = _vals(_arr("windspeed_10m","windspeed","wind_speed_10m","wind_speed", default=[]))
    gusts_kmh  = _vals(_arr("windgusts_10m","wind_gusts_10m","wind_gusts", default=[]))
    rain_mm_h  = _vals(_arr("rain", default=[]))
    tprob      = _vals(_arr("thunderstorm_probability", default=[]))

    max_speed_ms = kmh_to_ms(max(speeds_kmh)) if speeds_kmh else None
    max_gust_ms  = kmh_to_ms(max(gusts_kmh))  if gusts_kmh  else None
    heavy_rain   = (max(rain_mm_h) >= 8.0) if rain_mm_h else False
    thunder      = (max(tprob) >= 60) if tprob else False

    reasons=[]
    if isinstance(max_speed_ms,(int,float)) and max_speed_ms >= 13: reasons.append(f"ветер до {max_speed_ms:.0f} м/с")
    if isinstance(max_gust_ms,(int,float)) and max_gust_ms >= 17: reasons.append(f"порывы до {max_gust_ms:.0f} м/с")
    if heavy_rain: reasons.append("сильный дождь")
    if thunder: reasons.append("гроза")

    return {"max_speed_ms": max_speed_ms, "max_gust_ms": max_gust_ms, "heavy_rain": heavy_rain,
            "thunder": thunder, "warning": bool(reasons),
            "warning_text": "⚠️ <b>Штормовое предупреждение</b>: " + ", ".join(reasons) if reasons else ""}

def storm_flags_for_tomorrow(wm: Dict[str, Any], tz: pendulum.Timezone) -> Dict[str, Any]:
    """Совместимость: вызывает storm_flags_for_date_offset(..., 1)."""
    return storm_flags_for_date_offset(wm, tz, 1)

# ───────────── Air → вывод ─────────────
def _is_air_bad(air: Dict[str, Any]) -> Tuple[bool, str, str]:
    try: aqi = float(air.get("aqi")) if air.get("aqi") is not None else None
    except Exception: aqi = None
    pm25 = air.get("pm25"); pm10 = air.get("pm10")
    worst_label="умеренный"; reason_parts=[]; bad=False
    def _num(v):
        try: return float(v)
        except Exception: return None
    p25=_num(pm25); p10=_num(pm10)
    if aqi is not None and aqi >= 100:
        bad=True; 
        if aqi>=150: worst_label="высокий"
        reason_parts.append(f"AQI {aqi:.0f}")
    if p25 is not None and p25>35:
        bad=True; 
        if p25>55: worst_label="высокий"
        reason_parts.append(f"PM₂.₅ {p25:.0f}")
    if p10 is not None and p10>50:
        bad=True; 
        if p10>100: worst_label="высокий"
        reason_parts.append(f"PM₁₀ {p10:.0f}")
    reason=", ".join(reason_parts) if reason_parts else "показатели в норме"
    return bad, worst_label, reason

def build_conclusion(kp: Any, kp_status: str, air: Dict[str, Any], storm: Dict[str, Any], schu: Dict[str, Any]) -> List[str]:
    lines: List[str] = []
    storm_main = bool(storm.get("warning"))
    air_bad, air_label, air_reason = _is_air_bad(air)
    kp_val = float(kp) if isinstance(kp,(int,float)) else None
    kp_main = bool(kp_val is not None and kp_val >= 5)
    schu_main = (schu or {}).get("status_code") == "red"
    gust = storm.get("max_gust_ms")
    storm_text=None
    if storm_main:
        parts=[]
        if isinstance(gust,(int,float)): parts.append(f"порывы до {gust:.0f} м/с")
        if storm.get("heavy_rain"): parts.append("ливни")
        if storm.get("thunder"): parts.append("гроза")
        storm_text="штормовая погода: " + (", ".join(parts) if parts else "возможны неблагоприятные условия")
    air_text = f"качество воздуха: {air_label} ({air_reason})" if air_bad else None
    kp_text  = f"магнитная активность: Kp≈{kp_val:.1f} ({kp_status})" if kp_main and kp_val is not None else None
    schu_text= "сильные колебания Шумана (⚠️)" if schu_main else None
    if storm_main: lines.append(f"Основной фактор — {storm_text}. Планируйте дела с учётом погоды.")
    elif air_bad:  lines.append(f"Основной фактор — {air_text}. Сократите время на улице и проветривание по ситуации.")
    elif kp_main:  lines.append(f"Основной фактор — {kp_text}. Возможна чувствительность у метеозависимых.")
    elif schu_main:lines.append("Основной фактор — волны Шумана: отмечаются сильные отклонения. Берегите режим и нагрузку.")
    else:          lines.append("Серьёзных факторов риска не видно — ориентируйтесь на текущую погоду и личные планы.")
    secondary=[]
    for tag,txt in (("storm",storm_text),("air",air_text),("kp",kp_text),("schu",schu_text)):
        if txt:
            if (tag=="storm" and storm_main) or (tag=="air" and air_bad) or (tag=="kp" and kp_main) or (tag=="schu" and schu_main):
                continue
            secondary.append(txt)
    if secondary: lines.append("Также обратите внимание: " + "; ".join(secondary[:2]) + ".")
    return lines

# ───────────── SST cache (минимальный фолбэк) ─────────────
def get_sst_cached(la: float, lo: float) -> Optional[float]:
    """Простой фолбэк: обёртка над get_sst; если нужно — заменим на кэш."""
    try:
        v = get_sst(la, lo)
        return float(v) if isinstance(v, (int, float)) else None
    except Exception:
        return None

# ───────────── водные активности: короткий «highlights» ─────────────
def _deg_diff(a: float, b: float) -> float:
    return abs((a - b + 180) % 360 - 180)

def _cardinal(deg: Optional[float]) -> Optional[str]:
    if deg is None: return None
    dirs = ["N","NE","E","SE","S","SW","W","NW"]
    idx = int((deg + 22.5) // 45) % 8
    return dirs[idx]

def _shore_class(city: str, wind_from_deg: Optional[float]) -> Tuple[Optional[str], Optional[str]]:
    """Возвращает (class, source_label). class ∈ {onshore,cross,offshore}."""
    if wind_from_deg is None: return None, None
    face_deg, src_label = _shore_face_for_city(city)
    if face_deg is None: return None, src_label
    diff = _deg_diff(wind_from_deg, face_deg)
    if diff <= 45:  return "onshore", src_label
    if diff >= 135: return "offshore", src_label
    return "cross", src_label

def _fetch_wave_for_day(lat: float, lon: float, tz_obj: pendulum.Timezone, offset_days: int,
                        prefer_hour: int = 12) -> Tuple[Optional[float], Optional[float]]:
    if not requests:
        return None, None
    try:
        url = "https://marine-api.open-meteo.com/v1/marine"
        params = {
            "latitude": lat,
            "longitude": lon,
            "hourly": "wave_height,wave_period",
            "timezone": tz_obj.name,
        }
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        j = r.json()
        hourly = j.get("hourly") or {}
        times = [pendulum.parse(t) for t in (hourly.get("time") or []) if t]
        idx = _nearest_index_for_day(times, _date_for_offset(tz_obj, offset_days), prefer_hour, tz_obj)
        if idx is None:
            return None, None
        h = hourly.get("wave_height") or []
        p = hourly.get("wave_period") or []
        w_h = float(h[idx]) if idx < len(h) and h[idx] is not None else None
        w_t = float(p[idx]) if idx < len(p) and p[idx] is not None else None
        return w_h, w_t
    except Exception as e:
        logging.warning("marine fetch failed: %s", e)
        return None, None

def _fetch_wave_for_tomorrow(lat: float, lon: float, tz_obj: pendulum.Timezone,
                             prefer_hour: int = 12) -> Tuple[Optional[float], Optional[float]]:
    # совместимость
    return _fetch_wave_for_day(lat, lon, tz_obj, offset_days=1, prefer_hour=prefer_hour)

def _wetsuit_hint(sst: Optional[float]) -> Optional[str]:
    """Подсказка по толщине гидрика по температуре воды (°C)."""
    if not isinstance(sst, (int, float)):
        return None
    t = float(sst)
    if t >= WSUIT_NONE:   return None
    if t >= WSUIT_SHORTY: return "гидрокостюм шорти 2 мм"
    if t >= WSUIT_32:     return "гидрокостюм 3/2 мм"
    if t >= WSUIT_43:     return "гидрокостюм 4/3 мм (боты)"
    if t >= WSUIT_54:     return "гидрокостюм 5/4 мм (боты, перчатки)"
    if t >= WSUIT_65:     return "гидрокостюм 5/4 мм + капюшон (боты, перчатки)"
    return "гидрокостюм 6/5 мм + капюшон (боты, перчатки)"

def _water_highlights(
    city: str,
    la: float,
    lo: float,
    tz_obj: pendulum.Timezone,
    sst_hint: Optional[float] = None,
    offset_days: int = DAY_OFFSET
) -> Optional[str]:
    """
    Возвращает строку ТОЛЬКО если условия «good».
    Пример: 🧜‍♂️ Отлично: Кайт/Винг/Винд; SUP @Spot (SE/cross) • гидрокостюм 4/3 мм
    Если good-активностей нет — вернёт None (ничего не печатаем).
    """
    wm = get_weather(la, lo) or {}

    # ветер/порывы/волна на нужный день
    wind_ms, wind_dir, _, _ = pick_header_metrics(wm, tz_obj, offset_days=offset_days)
    wave_h, _ = _fetch_wave_for_day(la, lo, tz_obj, offset_days=offset_days)

    def _gust_at_noon(wm: Dict[str, Any], tz: pendulum.Timezone) -> Optional[float]:
        hourly = wm.get("hourly") or {}
        times = _hourly_times(wm)
        idx = _nearest_index_for_day(times, _date_for_offset(tz, offset_days), 12, tz)
        arr = _pick(hourly, "windgusts_10m", "wind_gusts_10m", "wind_gusts", default=[])
        if idx is not None and idx < len(arr):
            try:
                return kmh_to_ms(float(arr[idx]))
            except Exception:
                return None
        return None

    gust = _gust_at_noon(wm, tz_obj)

    wind_val = float(wind_ms) if isinstance(wind_ms, (int, float)) else None
    gust_val = float(gust) if isinstance(gust, (int, float)) else None
    card = _cardinal(float(wind_dir)) if isinstance(wind_dir, (int, float)) else None
    shore, shore_src = _shore_class(city, float(wind_dir) if isinstance(wind_dir, (int, float)) else None)

    # — критерии good
    kite_good = False
    if wind_val is not None:
        if KITE_WIND_GOOD_MIN <= wind_val <= KITE_WIND_GOOD_MAX:
            kite_good = True
        if shore == "offshore":
            kite_good = False
        if gust_val and wind_val and (gust_val / max(wind_val, 0.1) > KITE_GUST_RATIO_BAD):
            kite_good = False
        if wave_h is not None and wave_h >= KITE_WAVE_WARN:
            kite_good = False

    sup_good = False
    if wind_val is not None:
        if (wind_val <= SUP_WIND_GOOD_MAX) and (wave_h is None or wave_h <= SUP_WAVE_GOOD_MAX):
            sup_good = True
        if shore == "offshore" and wind_val >= OFFSHORE_SUP_WIND_MIN:
            sup_good = False

    surf_good = False
    if wave_h is not None:
        if SURF_WAVE_GOOD_MIN <= wave_h <= SURF_WAVE_GOOD_MAX and (wind_val is None or wind_val <= SURF_WIND_MAX):
            surf_good = True

    goods: List[str] = []
    if kite_good: goods.append("Кайт/Винг/Винд")
    if sup_good:  goods.append("SUP")
    if surf_good: goods.append("Сёрф")

    if not goods:
        if DEBUG_WATER:
            logging.info("WATER[%s]: no good. wind=%s dir=%s wave_h=%s gust=%s shore=%s",
                         city, wind_val, wind_dir, wave_h, gust_val, shore)
        return None

    sst = sst_hint if isinstance(sst_hint, (int, float)) else get_sst_cached(la, lo)
    suit_txt  = _wetsuit_hint(sst)
    suit_part = f" • {suit_txt}" if suit_txt else ""

    dir_part  = f" ({card}/{shore})" if card or shore else ""
    spot_part = f" @{shore_src}" if shore_src and shore_src not in (city, f"ENV:SHORE_FACE_{_env_city_key(city)}") else ""
    env_mark  = " (ENV)" if shore_src and str(shore_src).startswith("ENV:") else ""

    # одна строка даже если несколько активностей
    return "🧜‍♂️ Отлично: " + "; ".join(goods) + spot_part + env_mark + dir_part + suit_part

# ───────────── вспомогательная выборка температур по offset ─────────────

def _fetch_temps_for_offset(lat: float, lon: float, tz_name: str, offset_days: int) -> Tuple[Optional[float], Optional[float]]:
    """Пытаемся достать tmax/tmin из daily на смещение offset_days; фоллбэк — fetch_tomorrow_temps для offset=1."""
    if offset_days == 1:
        # быстрый путь — как раньше
        try:
            tmax, tmin = fetch_tomorrow_temps(lat, lon, tz=tz_name)
            return tmax, tmin
        except Exception:
            pass
    wm = get_weather(lat, lon) or {}
    daily = wm.get("daily") or {}
    tmax_arr = daily.get("temperature_2m_max") or daily.get("temperature_max") or []
    tmin_arr = daily.get("temperature_2m_min") or daily.get("temperature_min") or []
    try:
        tmax = float(tmax_arr[offset_days]) if offset_days < len(tmax_arr) else None
    except Exception:
        tmax = None
    try:
        tmin = float(tmin_arr[offset_days]) if offset_days < len(tmin_arr) else None
    except Exception:
        tmin = None
    return tmax, tmin

# ───────────── сообщение ─────────────
def build_message(region_name: str,
                  sea_label: str, sea_cities,
                  other_label: str, other_cities,
                  tz: Union[pendulum.Timezone, str]) -> str:

    tz_obj = _as_tz(tz)
    tz_name = tz_obj.name

    P: List[str] = []
    base_date = pendulum.today(tz_obj).add(days=DAY_OFFSET)
    title_when = "на сегодня" if DAY_OFFSET == 0 else "на завтра"
    P.append(f"<b>🌅 {region_name}: погода {title_when} ({base_date.format('DD.MM.YYYY')})</b>")

    # Калининград (шапка)
    stats = day_night_stats(KLD_LAT, KLD_LON, tz=tz_name)  # для RH и дневн/ночн предела
    wm    = get_weather(KLD_LAT, KLD_LON) or {}
    storm = storm_flags_for_date_offset(wm, tz_obj, DAY_OFFSET)

    wcarr = (wm.get("daily", {}) or {}).get("weathercode", [])
    wc    = wcarr[DAY_OFFSET] if isinstance(wcarr, list) and len(wcarr) > DAY_OFFSET else None

    rh_min = stats.get("rh_min"); rh_max = stats.get("rh_max")
    # tmax/tmin на соответствующий день
    t_day_max, t_night_min = _fetch_temps_for_offset(KLD_LAT, KLD_LON, tz_name, DAY_OFFSET)

    wind_ms, wind_dir_deg, press_val, press_trend = pick_header_metrics(wm, tz_obj, DAY_OFFSET)
    wind_part = (
        f"💨 {wind_ms:.1f} м/с ({compass(wind_dir_deg)})" if isinstance(wind_ms, (int, float)) and wind_dir_deg is not None
        else (f"💨 {wind_ms:.1f} м/с" if isinstance(wind_ms, (int, float)) else "💨 н/д")
    )
    gust = storm.get("max_gust_ms")
    if isinstance(gust, (int, float)):
        # короткий формат порывов (без единиц)
        wind_part += f" • порывы — {int(round(gust))}"
    press_part = f"{press_val} гПа {press_trend}" if isinstance(press_val, int) else "н/д"
    desc = code_desc(wc)

    tday_i  = int(round(t_day_max)) if isinstance(t_day_max, (int, float)) else None
    tnight_i= int(round(t_night_min)) if isinstance(t_night_min, (int, float)) else None
    kal_temp = f"{tday_i}/{tnight_i}{NBSP}°C" if (tday_i is not None and tnight_i is not None) else "н/д"

    kal_parts = [
        f"🏙️ Калининград: дн/ночь {kal_temp}",
        desc or None,
        wind_part,
        (f"💧 RH {rh_min:.0f}–{rh_max:.0f}%" if rh_min is not None and rh_max is not None else None),
        f"🔹 {press_part}",
    ]
    P.append(" • ".join([x for x in kal_parts if x]))
    P.append("———")

    if storm.get("warning"):
        P.append(storm["warning_text"])
        P.append("———")

    # Морские города: d/n + код погоды + 🌊 + возможные водные подсказки
    temps_sea: Dict[str, Tuple[float, float, int, float | None]] = {}
    sea_lookup: Dict[str, Tuple[float, float]] = {}
    for city, (la, lo) in sea_cities:
        sea_lookup[city] = (la, lo)
        tmax, tmin = _fetch_temps_for_offset(la, lo, tz=tz_name, offset_days=DAY_OFFSET)
        if tmax is None:
            continue
        wcx = (get_weather(la, lo) or {}).get("daily", {}).get("weathercode", [])
        wcx = wcx[DAY_OFFSET] if isinstance(wcx, list) and len(wcx) > DAY_OFFSET else 0
        temps_sea[city] = (tmax, tmin or tmax, wcx, get_sst(la, lo))

    if temps_sea:
        P.append(f"🌊 <b>{sea_label}</b>")
        medals = ["🥵", "😊", "🙄", "😮‍💨"]  # медали только первым четырём
        for i, (city, (d, n, wcx, sst_c)) in enumerate(sorted(temps_sea.items(),
                                                              key=lambda kv: kv[1][0], reverse=True)[:5]):
            d_i, n_i = int(round(d)), int(round(n))
            medal = medals[i] if i < len(medals) else "•"
            line = f"{medal} {city}: {d_i}/{n_i}{NBSP}°C"
            descx = code_desc(wcx)
            if descx:
                line += f" {descx}"
            if sst_c is not None:
                line += f" 🌊 {int(round(sst_c))}{NBSP}°C"
            try:
                la, lo = sea_lookup[city]
                hl = _water_highlights(city, la, lo, tz_obj, sst_c, offset_days=DAY_OFFSET)
                if hl:
                    line += f"\n   {hl}"
            except Exception as e:
                if DEBUG_WATER:
                    logging.exception("water_highlights failed for %s: %s", city, e)
            P.append(line)
        P.append("———")

    # Континентальные: «тёплые/холодные» (сортируем по tmax)
    temps_oth: Dict[str, Tuple[float, float, int]] = {}
    for city, (la, lo) in other_cities:
        tmax, tmin = _fetch_temps_for_offset(la, lo, tz=tz_name, offset_days=DAY_OFFSET)
        if tmax is None:
            continue
        wcx = (get_weather(la, lo) or {}).get("daily", {}).get("weathercode", [])
        wcx = wcx[DAY_OFFSET] if isinstance(wcx, list) and len(wcx) > DAY_OFFSET else 0
        temps_oth[city] = (tmax, tmin or tmax, wcx)

    if temps_oth:
        P.append("🔥 <b>Тёплые города, °C</b>")
        for city, (d, n, wcx) in sorted(temps_oth.items(), key=lambda kv: kv[1][0], reverse=True)[:3]:
            d_i, n_i = int(round(d)), int(round(n))
            descx = code_desc(wcx)
            P.append(f"   • {city}: {d_i}/{n_i}{NBSP}°C" + (f" {descx}" if descx else ""))
        P.append("❄️ <b>Холодные города, °C</b>")
        for city, (d, n, wcx) in sorted(temps_oth.items(), key=lambda kv: kv[1][0])[:3]:
            d_i, n_i = int(round(d)), int(round(n))
            descx = code_desc(wcx)
            P.append(f"   • {city}: {d_i}/{n_i}{NBSP}°C" + (f" {descx}" if descx else ""))
        P.append("———")

    # Air + Safecast + пыльца + радиация
    P.append("🏭 <b>Качество воздуха</b>")
    air = get_air(KLD_LAT, KLD_LON) or {}
    lvl = air.get("lvl", "н/д")
    P.append(f"{AIR_EMOJI.get(lvl,'⚪')} {lvl} (AQI {air.get('aqi','н/д')}) | PM₂.₅: {pm_color(air.get('pm25'))} | PM₁₀: {pm_color(air.get('pm10'))}")
    P.extend(safecast_block_lines())
    em_sm, lbl_sm = smoke_index(air.get("pm25"), air.get("pm10"))
    if lbl_sm and str(lbl_sm).lower() not in ("низкое", "низкий", "нет", "н/д"):
        P.append(f"🔥 Задымление: {em_sm} {lbl_sm}")

    if (p := get_pollen()):
        P.append("🌿 <b>Пыльца</b>")
        P.append(f"Деревья: {p['tree']} | Травы: {p['grass']} | Сорняки: {p['weed']} — риск {p['risk']}")

    if (rl := radiation_line(KLD_LAT, KLD_LON)):
        P.append(rl)
    P.append("———")

    # Геомагнитка / Солн. ветер
    kp_tuple = get_kp() or (None, "н/д", None, "n/d")
    try: kp, ks, kp_ts, kp_src = kp_tuple
    except Exception:
        kp = kp_tuple[0] if isinstance(kp_tuple,(list,tuple)) and len(kp_tuple)>0 else None
        ks = kp_tuple[1] if isinstance(kp_tuple,(list,tuple)) and len(kp_tuple)>1 else "н/д"
        kp_ts, kp_src = None, "n/d"
    age_txt = ""
    if isinstance(kp_ts,int) and kp_ts>0:
        try:
            age_min = int((pendulum.now("UTC").int_timestamp - kp_ts) / 60)
            if age_min > 180: age_txt = f", 🕓 {age_min // 60}ч назад"
            elif age_min >= 0: age_txt = f", {age_min} мин назад"
        except Exception: age_txt = ""
    if isinstance(kp,(int,float)):
        P.append(f"{kp_emoji(kp)} Геомагнитка: Kp={kp:.1f} ({ks}{age_txt})")
    else:
        P.append("🧲 Геомагнитка: н/д")

    sw = get_solar_wind() or {}
    bz, bt, v, n = sw.get("bz"), sw.get("bt"), sw.get("speed_kms"), sw.get("density")
    wind_status = sw.get("status", "н/д")
    parts=[]
    if isinstance(bz,(int,float)): parts.append(f"Bz {bz:.1f} nT")
    if isinstance(bt,(int,float)): parts.append(f"Bt {bt:.1f} nT")
    if isinstance(v,(int,float)):  parts.append(f"v {v:.0f} км/с")
    if isinstance(n,(int,float)):  parts.append(f"n {n:.1f} см⁻³")
    if parts: P.append("🌬️ Солнечный ветер: " + ", ".join(parts) + f" — {wind_status}")
    try:
        if (isinstance(kp,(int,float)) and kp >= 5) and isinstance(wind_status,str) and ("спокой" in wind_status.lower()):
            P.append("ℹ️ По ветру сейчас спокойно; Kp — глобальный индекс за 3 ч.")
    except Exception: pass

    # Шуман (можно отключить переменной окружения)
    schu_state = {} if DISABLE_SCHUMANN else get_schumann_with_fallback()
    if not DISABLE_SCHUMANN:
        P.append(schumann_line(schu_state))
        P.append("———")

    # Астрособытия (для Asia/Nicosia; смещение по режиму)
    tz_nic = pendulum.timezone("Asia/Nicosia")
    date_for_astro = pendulum.today(tz_nic).add(days=DAY_OFFSET)
    P.append(build_astro_section(date_local=date_for_astro, tz_local="Asia/Nicosia"))
    P.append("———")

    # Вывод
    P.append("📜 <b>Вывод</b>")
    P.extend(build_conclusion(kp, ks, air, storm, schu_state))
    P.append("———")

    # Рекомендации (безопасные)
    P.append("✅ <b>Рекомендации</b>")
    theme = (
        "плохая погода" if storm.get("warning") else
        ("магнитные бури" if isinstance(kp,(int,float)) and kp >= 5 else
         ("плохой воздух" if _is_air_bad(air)[0] else
          ("волны Шумана" if (schu_state or {}).get("status_code") == "red" else
           "здоровый день"))))
    for t in safe_tips(theme):
        P.append(t)

    P.append("———")
    P.append(f"📚 {get_fact(base_date, region_name)}")
    return "\n".join(P)

# ───────────── Шуман (чтение/фоллбэк) ─────────────
def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        if not path.exists(): return None
        data = json.loads(path.read_text("utf-8"))
        return data if isinstance(data, dict) else None
    except Exception as e:
        logging.warning("Safecast read error from %s: %s", path, e)
        return None

def _read_schumann_history() -> List[Dict[str, Any]]:
    candidates: List[Path] = []
    env_path = os.getenv("SCHU_FILE")
    if env_path: candidates.append(Path(env_path))
    here = Path(__file__).parent
    candidates += [here / "schumann_hourly.json", here / "data" / "schumann_hourly.json", here.parent / "schumann_hourly.json"]
    for p in candidates:
        try:
            if p.exists():
                txt = p.read_text("utf-8").strip()
                data = json.loads(txt) if txt else []
                if isinstance(data, list): return data
        except Exception as e:
            logging.warning("Schumann history read error from %s: %s", p, e)
    return []

def _schumann_trend(values: List[float], delta: float = 0.1) -> str:
    if not values: return "→"
    tail = values[-24:] if len(values) > 24 else values
    if len(tail) < 2: return "→"
    avg_prev = sum(tail[:-1]) / (len(tail) - 1)
    d = tail[-1] - avg_prev
    return "↑" if d >= delta else "↓" if d <= -delta else "→"

def _freq_status(freq: Optional[float]) -> tuple[str, str]:
    if not isinstance(freq, (int, float)): return "🟡 колебания", "yellow"
    f = float(freq)
    if 7.4 <= f <= 8.4:
        return ("🟢 в норме", "green") if (7.7 <= f <= 8.1) else ("🟡 колебания", "yellow")
    return "🔴 сильное отклонение", "red"

def _trend_text(sym: str) -> str:
    return {"↑": "растёт", "↓": "снижается", "→": "стабильно"}.get(sym, "стабильно")

def _h7_text(h7_amp: Optional[float], h7_spike: Optional[bool]) -> str:
    if isinstance(h7_amp, (int, float)):
        return f"H7: {h7_amp:.1f} (⚡ всплеск)" if h7_spike else f"H7: {h7_amp:.1f} — спокойно"
    return "H7: — нет данных"

def _is_stale(ts: Any, max_age_sec: int = 7200) -> bool:
    if not isinstance(ts, (int, float)): return False
    try:
        now_ts = pendulum.now("UTC").int_timestamp
        return (now_ts - int(ts)) > max_age_sec
    except Exception:
        return False

def _gentle_interpretation(code: str) -> str:
    if code == "green":  return "Волны Шумана близки к норме — организм реагирует как на обычный день."
    if code == "yellow": return "Заметны колебания — возможна лёгкая чувствительность к погоде и настроению."
    return "Сильные отклонения — прислушивайтесь к самочувствию и снижайте перегрузки."

def get_schumann_with_fallback() -> Dict[str, Any]:
    try:
        import schumann
        if hasattr(schumann, "get_schumann"):
            payload = schumann.get_schumann() or {}
            cached = bool(payload.get("cached"))
            if not cached and isinstance(payload.get("ts"), (int, float)) and _is_stale(payload["ts"]):
                cached = True
            return {
                "freq": payload.get("freq"),
                "amp": payload.get("amp"),
                "trend": payload.get("trend", "→"),
                "trend_text": payload.get("trend_text") or _trend_text(payload.get("trend", "→")),
                "status": payload.get("status") or _freq_status(payload.get("freq"))[0],
                "status_code": payload.get("status_code") or _freq_status(payload.get("freq"))[1],
                "h7_text": payload.get("h7_text") or _h7_text(payload.get("h7_amp"), payload.get("h7_spike")),
                "h7_amp": payload.get("h7_amp"),
                "h7_spike": payload.get("h7_spike"),
                "interpretation": payload.get("interpretation") or _gentle_interpretation(
                    payload.get("status_code") or _freq_status(payload.get("freq"))[1]
                ),
                "cached": cached,
            }
    except Exception:
        pass

    arr = _read_schumann_history()
    if not arr:
        return {"freq": None, "amp": None, "trend": "→",
                "trend_text": "стабильно", "status": "🟡 колебания", "status_code": "yellow",
                "h7_text": _h7_text(None, None), "h7_amp": None, "h7_spike": None,
                "interpretation": _gentle_interpretation("yellow"), "cached": True}

    amps: List[float] = []; last: Optional[Dict[str, Any]] = None
    for rec in arr:
        if isinstance(rec, dict) and isinstance(rec.get("amp"), (int, float)):
            amps.append(float(rec["amp"]))
        last = rec

    trend = _schumann_trend(amps)
    freq = (last.get("freq") if last else None)
    amp  = (last.get("amp")  if last else None)
    h7_amp = (last.get("h7_amp") if last else None)
    h7_spike = (last.get("h7_spike") if last else None)
    src = ((last or {}).get("src") or "").lower()
    cached = (src == "cache") or _is_stale((last or {}).get("ts"))
    status, code = _freq_status(freq)
    return {
        "freq": freq if isinstance(freq, (int, float)) else None,
        "amp":  amp  if isinstance(amp,  (int, float)) else None,
        "trend": trend, "trend_text": _trend_text(trend),
        "status": status, "status_code": code,
        "h7_text": _h7_text(h7_amp, h7_spike),
        "h7_amp": h7_amp if isinstance(h7_amp, (int, float)) else None,
        "h7_spike": h7_spike if isinstance(h7_spike, bool) else None,
        "interpretation": _gentle_interpretation(code),
        "cached": cached,
    }

def schumann_line(s: Dict[str, Any]) -> str:
    freq = s.get("freq"); amp = s.get("amp")
    trend_text = s.get("trend_text") or _trend_text(s.get("trend", "→"))
    status_lbl = s.get("status") or _freq_status(freq)[0]
    h7line = s.get("h7_text") or _h7_text(s.get("h7_amp"), s.get("h7_spike"))
    interp = s.get("interpretation") or _gentle_interpretation(s.get("status_code") or _freq_status(freq)[1])
    stale = " ⏳ нет свежих чисел" if s.get("cached") else ""
    if not isinstance(freq, (int, float)) and not isinstance(amp, (int, float)):
        return f"{status_lbl}{stale} • тренд: {trend_text} • {h7line}\n{interp}"
    fstr = f"{freq:.2f}" if isinstance(freq, (int, float)) else "н/д"
    astr = f"{amp:.2f} pT" if isinstance(amp, (int, float)) else "н/д"
    return f"{status_lbl}{stale} • Шуман: {fstr} Гц / {astr} • тренд: {trend_text} • {h7line}\n{interp}"

# ───────────── Safecast ─────────────
def load_safecast() -> Optional[Dict[str, Any]]:
    paths: List[Path] = []
    if os.getenv("SAFECAST_FILE"):
        paths.append(Path(os.getenv("SAFECAST_FILE")))
    here = Path(__file__).parent
    paths.append(here / "data" / "safecast_kaliningrad.json")
    sc: Optional[Dict[str, Any]] = None
    for p in paths:
        sc = _read_json(p)
        if sc: break
    if not sc: return None
    ts = sc.get("ts")
    if not isinstance(ts, (int, float)): return None
    now_ts = pendulum.now("UTC").int_timestamp
    if now_ts - int(ts) > 24 * 3600: return None
    return sc

def safecast_usvh_risk(x: float) -> tuple[str, str]:
    if x <= 0.15: return "🟢", "низкий"
    if x <= 0.30: return "🟡", "умеренный"
    return "🔵", "выше нормы"

def official_usvh_risk(x: float) -> tuple[str, str]:
    if x <= 0.15: return "🟢", "низкий"
    if x <= 0.30: return "🟡", "повышенный"
    return "🔴", "высокий"

def safecast_pm_level(pm25: Optional[float], pm10: Optional[float]) -> Tuple[str, str]:
    def l25(x: float) -> int: return 0 if x<=15 else 1 if x<=35 else 2 if x<=55 else 3
    def l10(x: float) -> int: return 0 if x<=30 else 1 if x<=50 else 2 if x<=100 else 3
    worst = -1
    if isinstance(pm25,(int,float)): worst=max(worst,l25(float(pm25)))
    if isinstance(pm10,(int,float)): worst=max(worst,l10(float(pm10)))
    if worst<0: return "⚪","н/д"
    return (["🟢","🟡","🟠","🔴"][worst],
            ["низкий","умеренный","высокий","очень высокий"][worst])

def safecast_block_lines() -> List[str]:
    sc = load_safecast()
    if not sc: return []
    lines: List[str] = []
    pm25, pm10 = sc.get("pm25"), sc.get("pm10")
    if isinstance(pm25,(int,float)) or isinstance(pm10,(int,float)):
        em,lbl = safecast_pm_level(pm25,pm10)
        parts=[]
        if isinstance(pm25,(int,float)): parts.append(f"PM₂.₅ {pm25:.0f}")
        if isinstance(pm10,(int,float)): parts.append(f"PM₁₀ {pm10:.0f}")
        lines.append(f"🧪 Safecast: {em} {lbl} · " + " | ".join(parts))
    cpm = sc.get("cpm"); usvh = sc.get("radiation_usvh")
    if not isinstance(usvh,(int,float)) and isinstance(cpm,(int,float)):
        usvh = float(cpm) * CPM_TO_USVH
    if isinstance(usvh,(int,float)):
        em,lbl = safecast_usvh_risk(float(usvh))
        if isinstance(cpm,(int,float)):
            lines.append(f"📟 Радиация (Safecast): {cpm:.0f} CPM ≈ {usvh:.3f} μSv/h — {em} {lbl} (медиана 6 ч)")
        else:
            lines.append(f"📟 Радиация (Safecast): ≈ {usvh:.3f} μSv/h — {em} {lbl} (медиана 6 ч)")
    elif isinstance(cpm,(int,float)):
        lines.append(f"📟 Радиация (Safecast): {cpm:.0f} CPM (медиана 6 ч)")
    return lines

def radiation_line(lat: float, lon: float) -> Optional[str]:
    data = get_radiation(lat, lon) or {}
    dose = data.get("dose")
    if isinstance(dose,(int,float)):
        em,lbl = official_usvh_risk(float(dose))
        return f"{em} Радиация: {dose:.3f} μSv/h ({lbl})"
    return None

# ───────────── Астроблок ─────────────
ZODIAC = {"Овен":"♈","Телец":"♉","Близнецы":"♊","Рак":"♋","Лев":"♌","Дева":"♍","Весы":"♎","Скорпион":"♏","Стрелец":"♐","Козерог":"♑","Водолей":"♒","Рыбы":"♓"}
def zsym(s: str) -> str:
    for name,sym in ZODIAC.items(): s = s.replace(name, sym)
    return s

def load_calendar(path: str = "lunar_calendar.json") -> dict:
    try: data = json.loads(Path(path).read_text("utf-8"))
    except Exception: return {}
    if isinstance(data, dict) and isinstance(data.get("days"), dict): return data["days"]
    return data if isinstance(data, dict) else {}

def _parse_voc_dt(s: str, tz: pendulum.tz.timezone.Timezone):
    if not s: return None
    try: return pendulum.parse(s).in_tz(tz)
    except Exception: pass
    try:
        dmy, hm = s.split(); d,m = map(int,dmy.split(".")); hh,mm = map(int,hm.split(":"))
        year = pendulum.today(tz).year
        return pendulum.datetime(year, m, d, hh, mm, tz=tz)
    except Exception: return None

def voc_interval_for_date(rec: dict, tz_local: str = "Asia/Nicosia"):
    if not isinstance(rec, dict): return None
    voc = (rec.get("void_of_course") or rec.get("voc") or rec.get("void") or {})
    if not isinstance(voc, dict): return None
    s = voc.get("start") or voc.get("from") or voc.get("start_time")
    e = voc.get("end")   or voc.get("to")   or rec.get("end_time")
    if not s or not e: return None
    tz = pendulum.timezone(tz_local)
    t1 = _parse_voc_dt(s, tz); t2 = _parse_voc_dt(e, tz)
    if not t1 or not t2: return None
    return (t1, t2)

def format_voc_for_post(start: pendulum.DateTime, end: pendulum.DateTime, label: str = "сегодня") -> str:
    if not start or not end: return ""
    return f"⚫️ VoC {label} {start.format('HH:mm')}–{end.format('HH:mm')}."

def lunar_advice_for_date(cal: dict, date_obj) -> list[str]:
    key = date_obj.to_date_string() if hasattr(date_obj, "to_date_string") else str(date_obj)
    rec = (cal or {}).get(key, {}) or {}
    adv = rec.get("advice")
    return [str(x).strip() for x in adv][:3] if isinstance(adv, list) and adv else []

def _astro_llm_bullets(date_str: str, phase: str, percent: int, sign: str, voc_text: str) -> List[str]:
    cache_file = CACHE_DIR / f"astro_{date_str}.txt"
    if cache_file.exists():
        lines = [l.strip() for l in cache_file.read_text("utf-8").splitlines() if l.strip()]
        if lines: return lines[:3]
    if not USE_DAILY_LLM:
        return []
    system = ("Действуй как АстроЭксперт, ты лучше всех знаешь как энергии луны и звезд влияют на жизнь человека."
              "Ты делаешь очень короткую сводку астрособытий на указанную дату (2–3 строки). "
              "Пиши грамотно по-русски, без клише. Используй ТОЛЬКО данную информацию: "
              "фаза Луны, освещённость, знак Луны и интервал Void-of-Course. "
              "Не придумывай других планет и аспектов. Каждая строка начинается с эмодзи и содержит одну мысль.")
    prompt = (f"Дата: {date_str}. Фаза Луны: {phase or 'н/д'} ({percent}% освещённости). "
              f"Знак: {sign or 'н/д'}. VoC: {voc_text or 'нет'}.")
    try:
        txt = gpt_complete(prompt=prompt, system=system, temperature=ASTRO_LLM_TEMP, max_tokens=160)
        raw_lines = [l.strip() for l in (txt or "").splitlines() if l.strip()]
        safe: List[str] = []
        for l in raw_lines:
            l = _sanitize_line(l, max_len=120)
            if not l or _looks_gibberish(l): continue
            if not re.match(r"^\W", l):
                l = "• " + l
            safe.append(l)
        if safe:
            cache_file.write_text("\n".join(safe[:3]), "utf-8")
            return safe[:3]
    except Exception as e:
        logging.warning("Astro LLM failed: %s", e)
    return []

def build_astro_section(date_local: Optional[pendulum.Date] = None, tz_local: str = "Asia/Nicosia") -> str:
    tz = pendulum.timezone(tz_local)
    date_local = date_local or pendulum.today(tz)
    date_key = date_local.format("YYYY-MM-DD")
    cal = load_calendar("lunar_calendar.json")
    rec = cal.get(date_key, {}) if isinstance(cal, dict) else {}
    phase_raw = (rec.get("phase_name") or rec.get("phase") or "").strip()
    phase_name = re.sub(r"^[^\wА-Яа-яЁё]+", "", phase_raw).split(",")[0].strip()
    percent = rec.get("percent") or rec.get("illumination") or rec.get("illum") or 0
    try: percent = int(round(float(percent)))
    except Exception: percent = 0
    sign = rec.get("sign") or rec.get("zodiac") or ""
    voc_text = ""
    voc = voc_interval_for_date(rec, tz_local=tz_local)
    if voc:
        t1, t2 = voc; voc_text = f"{t1.format('HH:mm')}–{t2.format('HH:mm')}"
    bullets = _astro_llm_bullets(date_local.format("DD.MM.YYYY"), phase_name, int(percent or 0), sign, voc_text)
    if not bullets:
        adv = rec.get("advice") or []
        bullets = [f"• {a}" for a in adv[:3]] if adv else []
    if not bullets:
        base = f"🌙 Фаза: {phase_name}" if phase_name else "🌙 Лунный день в норме"
        prm  = f" ({percent}%)" if isinstance(percent, int) and percent else ""
        bullets = [base + prm, (f"♒ Знак: {sign}" if sign else "— знак Луны н/д")]
    lines = ["🌌 <b>Астрособытия</b>"]
    # Если LLM выключен — максимум 2 пункта, иначе до 3
    max_items = 3 if USE_DAILY_LLM else 2
    lines += [zsym(x) for x in bullets[:max_items]]
    llm_used = bool(bullets) and USE_DAILY_LLM
    if voc_text and not llm_used:
        lines.append(f"⚫️ VoC: {voc_text}")
    return "\n".join(lines)

# ───────────── отправка ─────────────
async def send_common_post(
    bot: Bot,
    chat_id: int,
    region_name: str,
    sea_label: str,
    sea_cities,
    other_label: str,
    other_cities,
    tz: Union[pendulum.Timezone, str],
) -> None:
    msg = build_message(region_name, sea_label, sea_cities, other_label, other_cities, tz)
    await bot.send_message(chat_id=chat_id, text=msg, parse_mode=constants.ParseMode.HTML, disable_web_page_preview=True)

async def main_common(
    bot: Bot,
    chat_id: int,
    region_name: str,
    sea_label: str,
    sea_cities,
    other_label: str,
    other_cities,
    tz: Union[pendulum.Timezone, str],
) -> None:
    await send_common_post(
        bot=bot,
        chat_id=chat_id,
        region_name=region_name,
        sea_label=sea_label,
        sea_cities=sea_cities,
        other_label=other_label,
        other_cities=other_cities,
        tz=tz,
    )

__all__ = [
    "build_message",
    "send_common_post",
    "main_common",
    "schumann_line",
    "get_schumann_with_fallback",
    "pick_tomorrow_header_metrics",
    "storm_flags_for_tomorrow",
]
