#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
post_common.py — VayboMeter (Калининград).

Режимы:
- POST_MODE=morning  → компактный «утренний» макет (по заданному шаблону).
- POST_MODE=evening  → подробный «вечерний» макет (как раньше).

Также см. ENV-переключатели ниже.
"""

from __future__ import annotations

import os
import re
import json
import html
import logging
import math
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional, Union

import pendulum
from telegram import Bot, constants

from utils        import compass, get_fact, AIR_EMOJI, pm_color, kp_emoji, kmh_to_ms, smoke_index
from weather      import get_weather
from air          import get_air, get_sst, get_kp, get_solar_wind
from pollen       import get_pollen
from radiation    import get_radiation
from gpt          import gpt_blurb, gpt_complete  # микро-LLM для «Астрособытий»

# (опц.) морская волна из Open-Meteo Marine
try:
    import requests  # type: ignore
except Exception:
    requests = None  # type: ignore

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ────────────────────────── ENV flags ──────────────────────────
def _env_on(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return str(val).strip().lower() in ("1", "true", "yes", "on")

POST_MODE    = (os.getenv("POST_MODE") or "").strip().lower()  # morning/evening
DAY_OFFSET   = int(os.getenv("DAY_OFFSET", "1" if POST_MODE == "evening" else "0") or 0)
ASTRO_OFFSET = int(os.getenv("ASTRO_OFFSET", str(DAY_OFFSET)) or DAY_OFFSET)

SHOW_AIR      = _env_on("SHOW_AIR", True if POST_MODE != "evening" else False)
SHOW_SPACE    = _env_on("SHOW_SPACE", True if POST_MODE != "evening" else False)
SHOW_SCHUMANN = _env_on("SHOW_SCHUMANN", True if POST_MODE != "evening" else False)

DEBUG_WATER = _env_on("DEBUG_WATER", False)
DISABLE_SCHUMANN = (os.getenv("DISABLE_SCHUMANN", "").strip().lower() in ("1","true","yes","on")) or (not SHOW_SCHUMANN)

NBSP = "\u00A0"

# ────────────────────────── базовые константы ──────────────────────────
KLD_LAT, KLD_LON = 54.710426, 20.452214
CPM_TO_USVH = float(os.getenv("CPM_TO_USVH", "0.000571"))

CACHE_DIR = Path(".cache"); CACHE_DIR.mkdir(exist_ok=True, parents=True)
USE_DAILY_LLM = os.getenv("DISABLE_LLM_DAILY", "").strip().lower() not in ("1", "true", "yes", "on")

# ────────────────────────── LLM safety ──────────────────────────
DISABLE_LLM_TIPS = os.getenv("DISABLE_LLM_TIPS", "").strip().lower() in ("1", "true", "yes", "on")
ASTRO_LLM_TEMP = float(os.getenv("ASTRO_LLM_TEMP", "0.2"))

SAFE_TIPS_FALLBACKS = {
    "здоровый день": ["🚶 20–30 мин прогулки до полудня.", "🥤 Вода + лёгкий завтрак.", "🧘 5 минут растяжки вечером."],
    "плохая погода": ["🧥 Слои + непромокаемая куртка.", "🌧 Планы под крышу при ливнях.", "🚗 Заложите время на дорогу."],
    "магнитные бури": ["🧘 Меньше перегрузок, больше отдыха.", "💧 Больше воды, магний/калий в рационе.", "😴 Режим сна, меньше экранов вечером."],
    "плохой воздух": ["😮‍💨 Сократите время на улице.", "🪟 Проветривайте по ситуации.", "🏃 Тренируйтесь в помещении."],
    "волны Шумана": ["🍵 Тёплые напитки, спокойный темп.", "🧘 Небольшие перерывы в работе.", "😴 Ранний сон."],
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
KITE_WIND_MIN        = float(os.getenv("KITE_WIND_MIN",        "6"))
KITE_WIND_GOOD_MIN   = float(os.getenv("KITE_WIND_GOOD_MIN",   "7"))
KITE_WIND_GOOD_MAX   = float(os.getenv("KITE_WIND_GOOD_MAX",   "12"))
KITE_WIND_STRONG_MAX = float(os.getenv("KITE_WIND_STRONG_MAX", "18"))
KITE_GUST_RATIO_BAD  = float(os.getenv("KITE_GUST_RATIO_BAD",  "1.5"))
KITE_WAVE_WARN       = float(os.getenv("KITE_WAVE_WARN",       "2.5"))

SUP_WIND_GOOD_MAX     = float(os.getenv("SUP_WIND_GOOD_MAX",     "4"))
SUP_WAVE_GOOD_MAX     = float(os.getenv("SUP_WAVE_GOOD_MAX",     "0.6"))
OFFSHORE_SUP_WIND_MIN = float(os.getenv("OFFSHORE_SUP_WIND_MIN", "5"))

SURF_WAVE_GOOD_MIN   = float(os.getenv("SURF_WAVE_GOOD_MIN",   "0.9"))
SURF_WAVE_GOOD_MAX   = float(os.getenv("SURF_WAVE_GOOD_MAX",   "2.5"))
SURF_WIND_MAX        = float(os.getenv("SURF_WIND_MAX",        "10"))

# Wetsuit thresholds (°C)
WSUIT_NONE   = float(os.getenv("WSUIT_NONE",   "22"))
WSUIT_SHORTY = float(os.getenv("WSUIT_SHORTY", "20"))
WSUIT_32     = float(os.getenv("WSUIT_32",     "17"))
WSUIT_43     = float(os.getenv("WSUIT_43",     "14"))
WSUIT_54     = float(os.getenv("WSUIT_54",     "12"))
WSUIT_65     = float(os.getenv("WSUIT_65",     "10"))

# ────────────────────────── споты и береговая линия ──────────────────────────
SHORE_PROFILE: Dict[str, float] = {
    "Kaliningrad": 270.0,
    "Zelenogradsk": 285.0, "Svetlogorsk": 300.0, "Pionersky": 300.0, "Yantarny": 300.0,
    "Baltiysk": 270.0, "Primorsk": 265.0,
}

SPOT_SHORE_PROFILE: Dict[str, float] = {
    "Zelenogradsk": 285.0, "Svetlogorsk": 300.0, "Pionersky": 300.0, "Yantarny": 300.0,
    "Baltiysk (Spit)": 270.0, "Baltiysk (North beach)": 280.0,
    "Primorsk": 265.0, "Donskoye": 300.0,
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
    key = _norm_key(name); real = _SPOT_INDEX.get(key)
    if real: return real, SPOT_SHORE_PROFILE[real]
    return None

def _shore_face_for_city(city: str) -> Tuple[Optional[float], Optional[str]]:
    face_env = _parse_deg(os.getenv(f"SHORE_FACE_{_env_city_key(city)}"))
    if face_env is not None: return face_env, f"ENV:SHORE_FACE_{_env_city_key(city)}"
    spot_env = os.getenv(f"SPOT_{_env_city_key(city)}")
    sp = _spot_from_env(spot_env) if spot_env else None
    if not sp: sp = _spot_from_env(os.getenv("ACTIVE_SPOT"))
    if sp:
        label, deg = sp; return deg, label
    if city in SHORE_PROFILE: return SHORE_PROFILE[city], city
    return None, None

# ───────────── утилиты времени/погодных массивов ─────────────
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

def _daily_times(wm: Dict[str, Any]) -> List[pendulum.Date]:
    daily = wm.get("daily") or {}
    times = daily.get("time") or []
    out: List[pendulum.Date] = []
    for t in times:
        try:
            dt = pendulum.parse(str(t)); out.append(dt.date())
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

# ——— универсальные «на день offset» функции ———
def pick_header_metrics_for_offset(wm: Dict[str, Any], tz: pendulum.Timezone, offset_days: int) -> Tuple[Optional[float], Optional[int], Optional[int], str]:
    hourly = wm.get("hourly") or {}
    times = _hourly_times(wm)
    target_date = pendulum.now(tz).add(days=offset_days).date()

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
    return pick_header_metrics_for_offset(wm, tz, 1)

def _indices_for_day_offset(wm: Dict[str, Any], tz: pendulum.Timezone, offset_days: int) -> List[int]:
    times = _hourly_times(wm)
    target_date = pendulum.now(tz).add(days=offset_days).date()
    idxs: List[int] = []
    for i, dt in enumerate(times):
        try:
            if dt.in_tz(tz).date() == target_date: idxs.append(i)
        except Exception: pass
    return idxs

def storm_flags_for_offset(wm: Dict[str, Any], tz: pendulum.Timezone, offset_days: int) -> Dict[str, Any]:
    hourly = wm.get("hourly") or {}
    idxs = _indices_for_day_offset(wm, tz, offset_days)
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

    return {
        "max_speed_ms": max_speed_ms, "max_gust_ms": max_gust_ms,
        "heavy_rain": heavy_rain, "thunder": thunder,
        "warning": bool(reasons),
        "warning_text": "⚠️ <b>Штормовое предупреждение</b>: " + ", ".join(reasons) if reasons else ""
    }

def storm_flags_for_tomorrow(wm: Dict[str, Any], tz: pendulum.Timezone) -> Dict[str, Any]:
    return storm_flags_for_offset(wm, tz, 1)

# ───────────── дополнительные помощники ─────────────
def _fmt_delta(x: Any) -> str:
    try:
        v = float(x)
    except Exception:
        return "0.00"
    sign = "−" if v < 0 else ""
    return f"{sign}{abs(v):.2f}"

def _load_fx_rates(date_local: pendulum.DateTime, tz: pendulum.Timezone) -> Dict[str, Any]:
    try:
        import importlib
        fx = importlib.import_module("fx")
        return fx.get_rates(date=date_local, tz=tz) or {}  # type: ignore[attr-defined]
    except Exception as e:
        logging.warning("FX: failed %s", e)
        return {}

def _fx_line(date_local: pendulum.DateTime, tz: pendulum.Timezone) -> Optional[str]:
    r = _load_fx_rates(date_local, tz)
    if not r: return None
    def tok(code: str) -> Tuple[str,str]:
        it = r.get(code) or {}
        val = it.get("value"); dlt = it.get("delta")
        try: v=f"{float(val):.2f}"
        except Exception: v="н/д"
        return v, _fmt_delta(dlt)
    usd, usd_d = tok("USD"); eur, eur_d = tok("EUR"); cny, cny_d = tok("CNY")
    return f"💱 Курсы (утро): USD {usd} ₽ ({usd_d}) • EUR {eur} ₽ ({eur_d}) • CNY {cny} ₽ ({cny_d})"

def _sunset_for_offset(wm: Dict[str, Any], tz_name: str, offset_days: int) -> Optional[str]:
    daily = (wm or {}).get("daily") or {}
    times = daily.get("time") or []
    sunsets = daily.get("sunset") or []
    tz = pendulum.timezone(tz_name)
    target = pendulum.today(tz).add(days=offset_days).date()
    idx = None
    try:
        idx = [pendulum.parse(t).date() for t in times].index(target)
    except Exception:
        return None
    if idx is not None and idx < len(sunsets):
        try:
            return pendulum.parse(str(sunsets[idx])).in_tz(tz).format("HH:mm")
        except Exception:
            return None
    return None

def _fetch_temps_for_offset(lat: float, lon: float, tz_name: str, offset_days: int) -> Tuple[Optional[float], Optional[float], Optional[int]]:
    wm = get_weather(lat, lon) or {}
    daily = wm.get("daily") or {}
    times = _daily_times(wm)
    tz = pendulum.timezone(tz_name)
    target_date = pendulum.today(tz).add(days=offset_days).date()
    try:
        idx = times.index(target_date)
    except ValueError:
        return None, None, None
    tmax_arr = daily.get("temperature_2m_max") or []
    tmin_arr = daily.get("temperature_2m_min") or []
    wc_arr   = daily.get("weathercode") or []
    def _safe(arr, i):
        if i is None or i < 0 or i >= len(arr): return None
        try: return float(arr[i])
        except Exception: return None
    tmax = _safe(tmax_arr, idx); tmin = _safe(tmin_arr, idx)
    wc = None
    if idx is not None and idx < len(wc_arr):
        try: wc = int(wc_arr[idx])
        except Exception: wc = None
    return tmax, tmin, wc

def _select_warm_cold(other_cities, tz_name: str, offset_days: int) -> Tuple[Optional[Tuple[str,int,int]], Optional[Tuple[str,int,int]]]:
    pool: List[Tuple[str,int,int]] = []
    for city, (la, lo) in other_cities:
        d, n, _ = _fetch_temps_for_offset(la, lo, tz_name, offset_days)
        if d is None: continue
        pool.append((city, int(round(d)), int(round(n if n is not None else d))))
    if not pool: return None, None
    warm = max(pool, key=lambda x: x[1]); cold = min(pool, key=lambda x: x[1])
    return warm, cold

def _wetsuit_hint(sst: Optional[float]) -> Optional[str]:
    if not isinstance(sst, (int, float)): return None
    t = float(sst)
    if t >= WSUIT_NONE:   return None
    if t >= WSUIT_SHORTY: return "гидрокостюм шорти 2 мм"
    if t >= WSUIT_32:     return "гидрокостюм 3/2 мм"
    if t >= WSUIT_43:     return "гидрокостюм 4/3 мм (боты)"
    if t >= WSUIT_54:     return "гидрокостюм 5/4 мм (боты, перчатки)"
    if t >= WSUIT_65:     return "гидрокостюм 5/4 мм + капюшон (боты, перчатки)"
    return "гидрокостюм 6/5 мм + капюшон (боты, перчатки)"

def _avg_sst(sea_cities) -> Optional[float]:
    vals=[]
    for _, (la,lo) in sea_cities:
        try:
            v=get_sst(la,lo)
            if isinstance(v,(int,float)): vals.append(float(v))
        except Exception: pass
    if not vals: return None
    return sum(vals)/len(vals)

def _uvi_for_day(wm: Dict[str, Any], tz_name: str, offset_days: int) -> Optional[float]:
    daily = (wm or {}).get("daily") or {}
    times = daily.get("time") or []
    uvs = daily.get("uv_index_max") or daily.get("uv_index") or []
    if not times or not uvs: return None
    tz = pendulum.timezone(tz_name)
    target = pendulum.today(tz).add(days=offset_days).date()
    try:
        idx = [pendulum.parse(t).date() for t in times].index(target)
    except Exception:
        return None
    if idx < len(uvs):
        try:
            return float(uvs[idx])
        except Exception:
            return None
    return None

def _uvi_label(u: float) -> str:
    if u < 3: return "низкий"
    if u < 6: return "умеренный"
    if u < 8: return "высокий"
    if u < 11: return "оч. высокий"
    return "экстремальный"

# ───────────── Safecast ─────────────
def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        if not path.exists(): return None
        data = json.loads(path.read_text("utf-8"))
        return data if isinstance(data, dict) else None
    except Exception as e:
        logging.warning("JSON read error from %s: %s", path, e)
        return None

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

def safecast_line_always() -> str:
    sc = load_safecast()

    em, lbl = "⚪", "н/д"
    pm25_s, pm10_s = "—", "—"
    cpm_s, usvh_s  = "—", "—"
    risk = "н/д"

    if sc:
        pm25, pm10 = sc.get("pm25"), sc.get("pm10")
        em, lbl = safecast_pm_level(pm25, pm10)
        if isinstance(pm25,(int,float)): pm25_s=f"{pm25:.0f}"
        if isinstance(pm10,(int,float)): pm10_s=f"{pm10:.0f}"
        cpm  = sc.get("cpm")
        usvh = sc.get("radiation_usvh")
        if not isinstance(usvh,(int,float)) and isinstance(cpm,(int,float)):
            usvh = float(cpm)*CPM_TO_USVH
        if isinstance(cpm,(int,float)): cpm_s=f"{cpm:.0f}"
        if isinstance(usvh,(int,float)):
            usvh_s=f"{usvh:.3f}"
            _, risk_lbl = safecast_usvh_risk(float(usvh))
            risk = risk_lbl
        else:
            risk = lbl

    return f"🧪 Safecast: {em} {lbl} · PM₂.₅ {pm25_s} | PM₁₀ {pm10_s} · {cpm_s} CPM ≈ {usvh_s} μSv/h — {risk}"

def radiation_line(lat: float, lon: float) -> Optional[str]:
    data = get_radiation(lat, lon) or {}
    dose = data.get("dose")
    if isinstance(dose,(int,float)):
        em,lbl = official_usvh_risk(float(dose))
        return f"{em} Радиация: {dose:.3f} μSv/h ({lbl})"
    return None

# ───────────── астроблок (нужен только для VoC в компактном) ─────────────
def load_calendar(path: str = "lunar_calendar.json") -> dict:
    try: data = json.loads(Path(path).read_text("utf-8"))
    except Exception: return {}
    if isinstance(data, dict) and isinstance(data.get("days"), dict): return data["days"]
    return data if isinstance(data, dict) else {}

def _parse_voc_dt(s: str, tz: pendulum.Timezone):
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

# ───────────── компактный УТРОМ ─────────────
def build_message_morning_compact(region_name: str,
                                  sea_label: str, sea_cities,
                                  other_label: str, other_cities,
                                  tz: Union[pendulum.Timezone, str]) -> str:
    tz_obj = _as_tz(tz); tz_name = tz_obj.name
    base = pendulum.today(tz_obj).add(days=DAY_OFFSET)

    P: List[str] = []
    P.append(f"<b>🌅 {region_name}: погода на сегодня ({base.format('DD.MM.YYYY')})</b>")

    wm_klg = get_weather(KLD_LAT, KLD_LON) or {}
    storm  = storm_flags_for_offset(wm_klg, tz_obj, DAY_OFFSET)
    t_day_max, t_night_min, wc = _fetch_temps_for_offset(KLD_LAT, KLD_LON, tz_name, DAY_OFFSET)
    desc = code_desc(wc) or "н/д"

    wind_ms, wind_dir_deg, press_val, press_trend = pick_header_metrics_for_offset(wm_klg, tz_obj, DAY_OFFSET)
    wind_txt = f"💨 {wind_ms:.1f} м/с ({compass(wind_dir_deg)})" if isinstance(wind_ms,(int,float)) and wind_dir_deg is not None else "💨 н/д"
    if isinstance(storm.get("max_gust_ms"),(int,float)):
        wind_txt += f" • порывы — {int(round(storm['max_gust_ms']))}"
    press_txt = f"{press_val} гПа {press_trend}" if isinstance(press_val,int) else "н/д"

    tday_i   = int(round(t_day_max))   if isinstance(t_day_max,(int,float)) else None
    tnight_i = int(round(t_night_min)) if isinstance(t_night_min,(int,float)) else None
    kal_temp = f"{tday_i}/{tnight_i}{NBSP}°C" if (tday_i is not None and tnight_i is not None) else "н/д"

    P.append(f"Доброе утро 🏙️ Калининград — {kal_temp} • {desc} • {wind_txt} • 🔹 {press_txt}.")
    # Погреться/остыть
    warm, cold = _select_warm_cold(other_cities, tz_name, DAY_OFFSET)
    warm_s = f"{warm[0]} {warm[1]}/{warm[2]} °C" if warm else "н/д"
    cold_s = f"{cold[0]} {cold[1]}/{cold[2]} °C" if cold else "н/д"

    sst_avg = _avg_sst(sea_cities)
    suit = _wetsuit_hint(sst_avg)
    suit_s = suit if suit else "н/д"

    P.append(f"Погреться: {warm_s}; остыть: {cold_s}. Море: {suit_s}.")
    P.append("")

    # Закат
    sunset = _sunset_for_offset(wm_klg, tz_name, DAY_OFFSET) or "н/д"
    P.append(f"🌇 Закат: {sunset}")

    # FX
    fx_line = _fx_line(base, tz_obj)
    if fx_line: P.append(fx_line)

    # Воздух
    air = get_air(KLD_LAT, KLD_LON) or {}
    lvl = air.get("lvl", "н/д"); em = AIR_EMOJI.get(lvl, "⚪")
    aqi = air.get("aqi", "н/д")
    pm25i = air.get("pm25"); pm10i = air.get("pm10")
    pm25s = f"{int(round(float(pm25i)))}" if isinstance(pm25i,(int,float)) else "—"
    pm10s = f"{int(round(float(pm10i)))}" if isinstance(pm10i,(int,float)) else "—"
    P.append(f"🏭 Воздух: {em} {lvl} (AQI {aqi}) • PM₂.₅ {pm25s} / PM₁₀ {pm10s}")

    # Космопогода
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
            age_txt = (f", 🕓 {age_min // 60}ч назад") if age_min > 180 else (f", {age_min} мин назад" if age_min >= 0 else "")
        except Exception: age_txt = ""
    kp_part = f"{kp_emoji(kp)} Kp={kp:.1f} ({ks}{age_txt})" if isinstance(kp,(int,float)) else "🧲 Геомагнитка: н/д"

    sw = get_solar_wind() or {}
    v = sw.get("speed_kms"); n = sw.get("density"); sw_note = sw.get("status", "н/д")
    sw_bits=[]
    if isinstance(v,(int,float)): sw_bits.append(f"v {v:.0f} км/с")
    if isinstance(n,(int,float)): sw_bits.append(f"n {n:.1f} см⁻³")
    sw_part = "🌬️ SW: " + (", ".join(sw_bits) if sw_bits else "н/д") + f" — {sw_note}"
    if isinstance(kp,(int,float)):
        P.append(f"🧲 Геомагнитка: {kp_part} • {sw_part}")
    else:
        P.append(f"🧲 Геомагнитка: н/д • {sw_part}")

    # УФ — только если ≥ 3
    uvi = _uvi_for_day(wm_klg, tz_name, DAY_OFFSET)
    if isinstance(uvi,(int,float)) and uvi >= 3:
        P.append(f"☀️ УФ: {uvi:.1f} — {_uvi_label(float(uvi))} • SPF 30+ и головной убор")

    # Официальная радиация (если есть)
    rl = radiation_line(KLD_LAT, KLD_LON)
    if rl: P.append(rl)

    # Safecast — всегда
    P.append(safecast_line_always())

    # Итого
    storm_short = "шторм" if storm.get("warning") else "без шторма"
    air_short = "🟢" if str(lvl).lower() in ("хороший","низкий") else "🟡" if str(lvl).lower() in ("умеренный",) else "🟠/🔴"
    kp_short = (str(ks) if isinstance(ks,str) else "н/д")
    P.append("")
    P.append(f"🔎 Итого: воздух {air_short} • {storm_short} • Kp {kp_short}")

    # Рекомендации
    theme = ("плохая погода" if storm.get("warning") else
             ("магнитные бури" if (isinstance(kp,(int,float)) and kp >= 5) else
              ("плохой воздух" if (isinstance(pm25i,(int,float)) and float(pm25i)>35) else "здоровый день")))
    tips = safe_tips(theme)
    if tips:
        P.append(f"✅ Сегодня: {tips[0]}; {tips[1]}; {tips[2]}")

    # VoC — только если впереди
    cal = load_calendar("lunar_calendar.json")
    rec = (cal or {}).get(base.format("YYYY-MM-DD"), {}) if isinstance(cal, dict) else {}
    voc = voc_interval_for_date(rec, tz_local="Asia/Nicosia")
    if voc:
        t1, t2 = voc
        now_nic = pendulum.now("Asia/Nicosia")
        if t1 > now_nic:  # только если ещё впереди
            P.append(f"⚫️ VoC сегодня: {t1.format('HH:mm')}–{t2.format('HH:mm')}")

    # Праздник/факт дня
    P.append("")
    P.append(f"📚 {get_fact(base, region_name)}")
    P.append("#Калининград #погода #здоровье #сегодня #море")
    return "\n".join(P)

# ───────────── подробный (вечерний) — прежний ─────────────
def build_message_full(region_name: str,
                       sea_label: str, sea_cities,
                       other_label: str, other_cities,
                       tz: Union[pendulum.Timezone, str]) -> str:
    # (это — прежний «длинный» конструктор, укороченная версия под evening)
    tz_obj = _as_tz(tz); tz_name = tz_obj.name
    P: List[str] = []
    base = pendulum.today(tz_obj).add(days=DAY_OFFSET)
    hdr_when = "на завтра" if DAY_OFFSET == 1 else "на сегодня"
    P.append(f"<b>🌅 {region_name}: погода {hdr_when} ({base.format('DD.MM.YYYY')})</b>")

    wm_klg = get_weather(KLD_LAT, KLD_LON) or {}
    storm  = storm_flags_for_offset(wm_klg, tz_obj, DAY_OFFSET)
    t_day_max, t_night_min, wc = _fetch_temps_for_offset(KLD_LAT, KLD_LON, tz_name, DAY_OFFSET)
    wind_ms, wind_dir_deg, press_val, press_trend = pick_header_metrics_for_offset(wm_klg, tz_obj, DAY_OFFSET)

    wind_part = (f"💨 {wind_ms:.1f} м/с ({compass(wind_dir_deg)})" if isinstance(wind_ms,(int,float)) and wind_dir_deg is not None
                 else (f"💨 {wind_ms:.1f} м/с" if isinstance(wind_ms,(int,float)) else "💨 н/д"))
    if isinstance(storm.get("max_gust_ms"),(int,float)):
        wind_part += f" • порывы — {int(round(storm['max_gust_ms']))}"
    press_part = f"{press_val} гПа {press_trend}" if isinstance(press_val,int) else "н/д"
    desc = code_desc(wc)
    tday_i = int(round(t_day_max)) if isinstance(t_day_max,(int,float)) else None
    tnight_i = int(round(t_night_min)) if isinstance(t_night_min,(int,float)) else None
    kal_temp = f"{tday_i}/{tnight_i}{NBSP}°C" if (tday_i is not None and tnight_i is not None) else "н/д"
    P.append(" • ".join([x for x in [
        f"🏙️ Калининград: дн/ночь {kal_temp}", desc or None, wind_part, f"🔹 {press_part}"] if x]))
    P.append("———")

    if storm.get("warning"):
        P.append(storm["warning_text"])
        P.append("———")

    # (остальной подробный блок опущен для краткости: морские/тёплые/холодные и т.д.)
    # Воздух, Safecast, пыльца
    if SHOW_AIR:
        P.append("🏭 <b>Качество воздуха</b>")
        air = get_air(KLD_LAT, KLD_LON) or {}
        lvl = air.get("lvl", "н/д")
        P.append(f"{AIR_EMOJI.get(lvl,'⚪')} {lvl} (AQI {air.get('aqi','н/д')}) | PM₂.₅: {pm_color(air.get('pm25'))} | PM₁₀: {pm_color(air.get('pm10'))}")
        P.append(safecast_line_always())
        P.append("———")

    if SHOW_SPACE:
        kp_tuple = get_kp() or (None, "н/д", None, "n/d")
        try: kp, ks, kp_ts, kp_src = kp_tuple
        except Exception:
            kp = kp_tuple[0] if isinstance(kp_tuple,(list,tuple)) and len(kp_tuple)>0 else None
            ks = kp_tuple[1] if isinstance(kp_tuple,(list,tuple)) and len(kp_tuple)>1 else "н/д"
        if isinstance(kp,(int,float)):
            P.append(f"{kp_emoji(kp)} Геомагнитка: Kp={kp:.1f} ({ks})")
        else:
            P.append("🧲 Геомагнитка: н/д")
        sw = get_solar_wind() or {}
        bz, bt, v, n = sw.get("bz"), sw.get("bt"), sw.get("speed_kms"), sw.get("density")
        parts=[]
        if isinstance(bz,(int,float)): parts.append(f"Bz {bz:.1f} nT")
        if isinstance(bt,(int,float)): parts.append(f"Bt {bt:.1f} nT")
        if isinstance(v,(int,float)):  parts.append(f"v {v:.0f} км/с")
        if isinstance(n,(int,float)):  parts.append(f"n {n:.1f} см⁻³")
        if parts: P.append("🌬️ Солнечный ветер: " + ", ".join(parts) + f" — {sw.get('status','н/д')}")
        P.append("———")

    # Вывод + рекомендации + факт/праздник
    P.append("📜 <b>Вывод</b>")
    P.append("Планируйте дела по погоде; детальный evening-макет может быть расширен по желанию.")
    P.append("———")
    P.append("✅ <b>Рекомендации</b>")
    for t in safe_tips("здоровый день"):
        P.append(t)
    P.append("———")
    P.append(f"📚 {get_fact(base, region_name)}")
    return "\n".join(P)

# ───────────── публичный билдер ─────────────
def build_message(region_name: str,
                  sea_label: str, sea_cities,
                  other_label: str, other_cities,
                  tz: Union[pendulum.Timezone, str]) -> str:
    if POST_MODE == "morning":
        return build_message_morning_compact(region_name, sea_label, sea_cities, other_label, other_cities, tz)
    return build_message_full(region_name, sea_label, sea_cities, other_label, other_cities, tz)

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
    "safecast_line_always",
    "pick_tomorrow_header_metrics",
    "storm_flags_for_tomorrow",
    "pick_header_metrics_for_offset",
    "storm_flags_for_offset",
]
