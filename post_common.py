#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
post_common.py — Kaliningrad (VayboMeter).

Утренний пост (compact):
  🌇 Закат • 💱 Курсы (утро)
  🏭 AQI … • PM… • 🌿 пыльца
  🧲 Космопогода: Kp (статус, 🕓 …) • 🌬️ SW v, n — …
  ⚠️ Штормовое предупреждение (если порывы/ливни/гроза сильные)
  🔎 Итого … • ✅ Сегодня: советы

Вечерний пост:
  - Вечером блоки воздуха/космопогоды/Шумана скрыты (кроме рекомендаций).
  - Вечером Kp не выводится (и не участвует в выборе рекомендаций).

ENV:
  POST_MODE (morning/evening), DAY_OFFSET, ASTRO_OFFSET,
  SHOW_AIR, SHOW_SPACE, SHOW_SCHUMANN.
"""

from __future__ import annotations

import os
import re
import json
import html
import math
import logging
import datetime as dt
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional, Union
import urllib.request
import urllib.error
import random
import time

import pendulum
from telegram import Bot, constants

from utils   import compass, get_fact
from weather import get_sunrise_sunset, get_visibility_weather, get_weather
from air     import get_air, get_sst, get_kp, get_solar_wind
from pollen  import get_pollen
from radiation import get_radiation
from earthquakes import build_kld_quake_line, get_recent_earthquakes_kld
from visibility_context import (
    KldVisibilityContext,
    build_kld_visibility_line,
    get_kld_visibility_context,
    visibility_diagnostics,
    visibility_payload_has_morning_window,
)

try:
    from gpt import gpt_blurb, gpt_complete  # type: ignore
except Exception:
    gpt_blurb = None      # type: ignore
    gpt_complete = None   # type: ignore

try:
    import requests  # type: ignore
except Exception:
    requests = None  # type: ignore

# Картинки для KLD
try:
    # основной вариант — как в кипрском боте
    from world_en.imagegen import generate_astro_image  # type: ignore
except Exception:
    try:
        # запасной вариант — локальный модуль
        from imagegen import generate_astro_image  # type: ignore
    except Exception:
        generate_astro_image = None  # type: ignore

try:
    from image_prompt_kld import build_kld_evening_prompt  # type: ignore
except Exception:
    build_kld_evening_prompt = None  # type: ignore

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ────────────────────────── ENV flags ──────────────────────────
def _env_on(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")


POST_MODE = (os.getenv("POST_MODE") or "evening").strip().lower()

# ВАЖНО: day/astro offsets должны следовать режиму, если ENV не задан явно.
_DAY_OFFSET_ENV = os.getenv("DAY_OFFSET")
_ASTRO_OFFSET_ENV = os.getenv("ASTRO_OFFSET")

def _default_day_offset(mode: str) -> int:
    return 0 if (mode or "").strip().lower() == "morning" else 1

def _int_env(v: Optional[str]) -> Optional[int]:
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    try:
        return int(s)
    except Exception:
        return None

DAY_OFFSET = _int_env(_DAY_OFFSET_ENV)
if DAY_OFFSET is None:
    DAY_OFFSET = _default_day_offset(POST_MODE)

ASTRO_OFFSET = _int_env(_ASTRO_OFFSET_ENV)
if ASTRO_OFFSET is None:
    ASTRO_OFFSET = int(DAY_OFFSET)

# По умолчанию (если ENV не задан) — включено.
# Вечером блоки всё равно скрыты логикой build_message_legacy_evening (кроме рекомендаций),
# а утром — показываются.
SHOW_AIR      = _env_on("SHOW_AIR",      True)
SHOW_SPACE    = _env_on("SHOW_SPACE",    True)
SHOW_SCHUMANN = _env_on("SHOW_SCHUMANN", True)

DEBUG_WATER = os.getenv("DEBUG_WATER", "").strip().lower() in ("1", "true", "yes", "on")
DISABLE_SCHUMANN = os.getenv("DISABLE_SCHUMANN", "").strip().lower() in ("1", "true", "yes", "on")

# Пороги
STORM_GUST_MS        = float(os.getenv("STORM_GUST_MS", "15"))
ALERT_GUST_MS        = float(os.getenv("ALERT_GUST_MS", "20"))
ALERT_RAIN_MM_H      = float(os.getenv("ALERT_RAIN_MM_H", "10"))
ALERT_TSTORM_PROB_PC = float(os.getenv("ALERT_TSTORM_PROB_PC", "70"))

# UVI: показываем только когда реально нужны меры (по умолчанию с "высокого")
UVI_WARN_FROM = float(os.getenv("UVI_WARN_FROM", "6"))

# LLM-параметры
USE_DAILY_LLM    = os.getenv("DISABLE_LLM_DAILY", "").strip().lower() not in ("1", "true", "yes", "on")
ASTRO_LLM_TEMP   = float(os.getenv("ASTRO_LLM_TEMP", "0.7"))

# ────────────────────────── базовые константы ──────────────────────────
NBSP = "\u00A0"
RUB  = "\u20BD"

KLD_LAT, KLD_LON = 54.710426, 20.452214
CACHE_DIR = Path(".cache")
CACHE_DIR.mkdir(exist_ok=True, parents=True)

KLD_LAT_DEFAULT = float(os.getenv("KLD_LAT", "54.71"))
KLD_LON_DEFAULT = float(os.getenv("KLD_LON", "20.51"))

# ────────────────────────── ENV TUNABLES (водные активности) ──────────────────────────
KITE_WIND_MIN        = float(os.getenv("KITE_WIND_MIN",        "6"))
KITE_WIND_GOOD_MIN   = float(os.getenv("KITE_WIND_GOOD_MIN",   "7"))
KITE_WIND_GOOD_MAX   = float(os.getenv("KITE_WIND_GOOD_MAX",   "12"))
KITE_WIND_STRONG_MAX = float(os.getenv("KITE_WIND_STRONG_MAX", "18"))
KITE_GUST_RATIO_BAD  = float(os.getenv("KITE_GUST_RATIO_BAD",  "1.5"))
KITE_WAVE_WARN       = float(os.getenv("KITE_WAVE_WARN",       "2.5"))

SUP_WIND_GOOD_MAX     = float(os.getenv("SUP_WIND_GOOD_MAX",     "4"))
SUP_WIND_OK_MAX       = float(os.getenv("SUP_WIND_OK_MAX",       "6"))
SUP_WIND_EDGE_MAX     = float(os.getenv("SUP_WIND_EDGE_MAX",     "8"))
SUP_WAVE_GOOD_MAX     = float(os.getenv("SUP_WAVE_GOOD_MAX",     "0.6"))
SUP_WAVE_OK_MAX       = float(os.getenv("SUP_WAVE_OK_MAX",       "0.8"))
SUP_WAVE_BAD_MIN      = float(os.getenv("SUP_WAVE_BAD_MIN",      "1.5"))
OFFSHORE_SUP_WIND_MIN = float(os.getenv("OFFSHORE_SUP_WIND_MIN", "5"))

SURF_WAVE_GOOD_MIN   = float(os.getenv("SURF_WAVE_GOOD_MIN",   "0.9"))
SURF_WAVE_GOOD_MAX   = float(os.getenv("SURF_WAVE_GOOD_MAX",   "2.5"))
SURF_WIND_MAX        = float(os.getenv("SURF_WIND_MAX",        "10"))

WSUIT_NONE   = float(os.getenv("WSUIT_NONE",   "22"))
WSUIT_SHORTY = float(os.getenv("WSUIT_SHORTY", "20"))
WSUIT_32     = float(os.getenv("WSUIT_32",     "17"))
WSUIT_43     = float(os.getenv("WSUIT_43",     "14"))
WSUIT_54     = float(os.getenv("WSUIT_54",     "12"))
WSUIT_65     = float(os.getenv("WSUIT_65",     "10"))

# ────────────────────────── споты и профиль береговой линии ──────────────────────────
SHORE_PROFILE: Dict[str, float] = {
    "Kaliningrad": 270.0,
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
    if not val:
        return None
    try:
        return float(str(val).strip())
    except Exception:
        return None

def _env_city_key(city: str) -> str:
    return city.upper().replace(" ", "_")

def _spot_from_env(name: Optional[str]) -> Optional[Tuple[str, float]]:
    if not name:
        return None
    key = _norm_key(name)
    real = _SPOT_INDEX.get(key)
    if real:
        return real, SPOT_SHORE_PROFILE[real]
    return None

def _shore_face_for_city(city: str) -> Tuple[Optional[float], Optional[str]]:
    face_env = _parse_deg(os.getenv(f"SHORE_FACE_{_env_city_key(city)}"))
    if face_env is not None:
        return face_env, f"ENV:SHORE_FACE_{_env_city_key(city)}"
    spot_env = os.getenv(f"SPOT_{_env_city_key(city)}")
    sp = _spot_from_env(spot_env) if spot_env else None
    if not sp:
        sp = _spot_from_env(os.getenv("ACTIVE_SPOT"))
    if sp:
        label, deg = sp
        return deg, label
    if city in SHORE_PROFILE:
        return SHORE_PROFILE[city], city
    return None, None

# ────────────────────────── WMO → эмодзи/текст ──────────────────────────
WMO_DESC = {
    0: "☀️ ясно", 1: "⛅ ч.обл", 2: "☁️ обл", 3: "🌥 пасм",
    45: "🌫 туман", 48: "🌫 изморозь",
    51: "🌦 морось", 61: "🌧 дождь", 71: "❄️ снег", 95: "⛈ гроза",
}

def code_desc(c: Any) -> Optional[str]:
    try:
        return WMO_DESC.get(int(c))
    except Exception:
        return None

# ────────────────────────── утилиты ──────────────────────────
def _fmt_delta(x: Any) -> str:
    try:
        v = float(x)
    except Exception:
        return "0.00"
    sign = "−" if v < 0 else ""
    return f"{sign}{abs(v):.2f}"

def aqi_risk_ru(aqi: Any) -> str:
    try:
        v = float(aqi)
    except Exception:
        return "н/д"
    if v <= 50:
        return "низкий"
    if v <= 100:
        return "умеренный"
    if v <= 150:
        return "высокий"
    return "очень высокий"

def kmh_to_ms(kmh: Optional[float]) -> Optional[float]:
    """Конвертирует км/ч в м/с."""
    if not isinstance(kmh, (int, float)):
        return None
    return float(kmh) / 3.6

def _pick(d: Dict[str, Any], *keys, default=None):
    """Универсальный getter для словарей."""
    for k in keys:
        if k in d:
            return d[k]
    return default

def _sanitize_line(text: str, max_len: int = 120) -> str:
    text = (text or "").strip()
    if len(text) > max_len:
        text = text[:max_len].rstrip(" ,.;:-") + "…"
    return text

def _looks_gibberish(s: str) -> bool:
    if not s:
        return True
    letters = sum(ch.isalpha() for ch in s)
    if letters < max(3, int(len(s) * 0.15)):
        return True
    return False

# ────────────── ЕДИНЫЙ ИСТОЧНИК Kp: SWPC closed 3-hour bar ──────────────
def _kp_status_by_value(kp: Optional[float]) -> str:
    if not isinstance(kp, (int, float)):
        return "н/д"
    k = float(kp)
    if k >= 6.0:
        return "буря"
    if k >= 5.0:
        return "повышенная"
    return "умеренно"

def _kp_from_swpc_http() -> Tuple[Optional[float], Optional[int], str]:
    url = "https://services.swpc.noaa.gov/json/planetary_k_index.json"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if not data:
            return None, None, "swpc/http-empty"
        last = data[-1]
        kp = float(last.get("kp_index"))
        t  = str(last.get("time_tag"))
        dt_utc = pendulum.parse(t, tz="UTC")
        age_min = int((pendulum.now("UTC") - dt_utc).in_minutes())
        return kp, age_min, "swpc/http"
    except Exception as e:
        logging.warning("SWPC HTTP Kp failed: %s", e)
        return None, None, "swpc/http-fail"

def _kp_global_swpc() -> Tuple[Optional[float], str, Optional[int], str]:
    kp, age, src = _kp_from_swpc_http()
    if isinstance(kp, (int, float)):
        if isinstance(age, int) and age > 6 * 60:
            logging.warning("Kp SWPC stale (%s min, src=%s)", age, src)
        else:
            k = max(0.0, min(9.0, float(kp)))
            status = _kp_status_by_value(k)
            logging.info("Kp SWPC used: %.1f, age=%s min, src=%s", k, age, src)
            return k, status, age, src or "swpc/http"

    tup = None
    src2 = "kp:nodata"

    for arg in ("swpc_closed", "global", "swpc"):
        try:
            tup = get_kp(source=arg)  # type: ignore[arg-type]
            src2 = f"air.{arg}"
            break
        except TypeError:
            try:
                tup = get_kp(arg)  # type: ignore[misc]
                src2 = f"air.{arg}"
                break
            except Exception:
                tup = None
        except Exception:
            tup = None

    if tup is None:
        try:
            tup = get_kp()
            src2 = "air.default"
        except Exception:
            logging.warning("Kp fallback via air.get_kp() failed")
            return None, "н/д", None, "kp:nodata"

    kp_val = None
    ts = None
    if isinstance(tup, (list, tuple)):
        if len(tup) > 0 and isinstance(tup[0], (int, float)):
            kp_val = float(tup[0])
        if len(tup) > 2 and isinstance(tup[2], (int, float)):
            ts = int(tup[2])

    age_min: Optional[int] = None
    if ts is not None:
        try:
            age_min = int((pendulum.now("UTC").int_timestamp - ts) / 60)
        except Exception:
            age_min = None

    if isinstance(age_min, int) and age_min > 24 * 60:
        logging.warning("Kp fallback stale (%s min, src=%s)", age_min, src2)
        return None, "н/д", age_min, f"{src2}-stale"

    if not isinstance(kp_val, (int, float)):
        return None, "н/д", age_min, src2

    k = max(0.0, min(9.0, float(kp_val)))
    status = _kp_status_by_value(k)
    logging.info("Kp fallback used: %.1f, age=%s min, src=%s", k, age_min, src2)
    return k, status, age_min, src2

# ────────────────────────── Open-Meteo helpers ──────────────────────────
def _hourly_times(wm: Dict[str, Any]) -> List[pendulum.DateTime]:
    hourly = wm.get("hourly") or {}
    times = hourly.get("time") or hourly.get("time_local") or []
    out: List[pendulum.DateTime] = []
    for t in times:
        try:
            out.append(pendulum.parse(str(t)))
        except Exception:
            pass
    return out

def _daily_times(wm: Dict[str, Any]) -> List[pendulum.Date]:
    daily = wm.get("daily") or {}
    times = daily.get("time") or daily.get("time_local") or []
    out: List[pendulum.Date] = []
    for t in times:
        try:
            out.append(pendulum.parse(str(t)).date())
        except Exception:
            pass
    return out

def _daily_index_for_offset(
    wm: Dict[str, Any],
    tz: pendulum.Timezone,
    offset_days: int,
) -> Optional[int]:
    times = _daily_times(wm)
    if not times:
        return None
    target = pendulum.today(tz).add(days=offset_days).date()
    try:
        return times.index(target)
    except ValueError:
        tstr = target.to_date_string()
        for i, d in enumerate(times):
            try:
                if getattr(d, "to_date_string", None) and d.to_date_string() == tstr:
                    return i
            except Exception:
                continue
    return None

def _nearest_index_for_day(
    times: List[pendulum.DateTime],
    date_obj: pendulum.Date,
    prefer_hour: int,
    tz: pendulum.Timezone,
) -> Optional[int]:
    if not times:
        return None
    target = pendulum.datetime(
        date_obj.year, date_obj.month, date_obj.day, prefer_hour, 0, tz=tz
    )
    best_i, best_diff = None, None
    for i, dt_ in enumerate(times):
        try:
            dl = dt_.in_tz(tz)
        except Exception:
            dl = dt_
        if dl.date() != date_obj:
            continue
        diff = abs((dl - target).total_seconds())
        if best_diff is None or diff < best_diff:
            best_i, best_diff = i, diff
    return best_i

def _circular_mean_deg(deg_list: List[float]) -> Optional[float]:
    if not deg_list:
        return None
    x = sum(math.cos(math.radians(d)) for d in deg_list)
    y = sum(math.sin(math.radians(d)) for d in deg_list)
    if x == 0 and y == 0:
        return None
    ang = math.degrees(math.atan2(y, x))
    return (ang + 360.0) % 360.0

def pick_header_metrics_for_offset(
    wm: Dict[str, Any], tz: pendulum.Timezone, offset_days: int
) -> Tuple[Optional[float], Optional[int], Optional[int], str]:
    hourly = wm.get("hourly") or {}
    times  = _hourly_times(wm)
    tgt    = pendulum.now(tz).add(days=offset_days).date()
    idx_noon = _nearest_index_for_day(times, tgt, 12, tz)
    idx_morn = _nearest_index_for_day(times, tgt, 6, tz)

    spd_kmh = hourly.get("wind_speed_10m") or hourly.get("windspeed_10m") or []
    dir_deg = hourly.get("wind_direction_10m") or hourly.get("winddirection_10m") or []
    prs     = hourly.get("surface_pressure") or []

    wind_ms = None
    wind_dir = None
    press_val = None
    trend = "→"
    try:
        if idx_noon is not None:
            if idx_noon < len(spd_kmh):
                wind_ms = float(spd_kmh[idx_noon]) / 3.6
            if idx_noon < len(dir_deg):
                wind_dir = int(round(float(dir_deg[idx_noon])))
            if idx_noon < len(prs):
                press_val = int(round(float(prs[idx_noon])))
            if idx_morn is not None and idx_morn < len(prs) and idx_noon < len(prs):
                diff = float(prs[idx_noon]) - float(prs[idx_morn])
                trend = "↑" if diff >= 0.3 else "↓" if diff <= -0.3 else "→"
    except Exception:
        pass
    return wind_ms, wind_dir, press_val, trend

def pick_tomorrow_header_metrics(
    wm: Dict[str, Any], tz: pendulum.Timezone
) -> Tuple[Optional[float], Optional[int], Optional[int], str]:
    hourly = wm.get("hourly") or {}
    times = _hourly_times(wm)
    tomorrow = pendulum.now(tz).add(days=1).date()

    spd_arr = _pick(
        hourly,
        "windspeed_10m",
        "windspeed",
        "wind_speed_10m",
        "wind_speed",
        default=[],
    )
    dir_arr = _pick(
        hourly,
        "winddirection_10m",
        "winddirection",
        "wind_dir_10m",
        "wind_dir",
        default=[],
    )
    prs_arr = hourly.get("surface_pressure", []) or hourly.get("pressure", [])

    if times:
        idx_noon = _nearest_index_for_day(times, tomorrow, prefer_hour=12, tz=tz)
        idx_morn = _nearest_index_for_day(times, tomorrow, prefer_hour=6, tz=tz)
    else:
        idx_noon = idx_morn = None

    wind_ms = None
    wind_dir = None
    press_val = None
    trend = "→"

    if idx_noon is not None:
        try:
            spd = float(spd_arr[idx_noon]) if idx_noon < len(spd_arr) else None
        except Exception:
            spd = None
        try:
            wdir = float(dir_arr[idx_noon]) if idx_noon < len(dir_arr) else None
        except Exception:
            wdir = None
        try:
            p_noon = float(prs_arr[idx_noon]) if idx_noon < len(prs_arr) else None
        except Exception:
            p_noon = None
        try:
            p_morn = float(prs_arr[idx_morn]) if (
                idx_morn is not None and idx_morn < len(prs_arr)
            ) else None
        except Exception:
            p_morn = None

        wind_ms  = kmh_to_ms(spd) if isinstance(spd,  (int, float)) else None
        wind_dir = int(round(wdir)) if isinstance(wdir, (int, float)) else None
        press_val = int(round(p_noon)) if isinstance(p_noon, (int, float)) else None
        if isinstance(p_noon, (int, float)) and isinstance(p_morn, (int, float)):
            diff = p_noon - p_morn
            trend = "↑" if diff >= 0.3 else "↓" if diff <= -0.3 else "→"

    if wind_ms is None and times:
        idxs = [i for i, t in enumerate(times) if t.in_tz(tz).date() == tomorrow]
        if idxs:
            try:
                speeds = [float(spd_arr[i]) for i in idxs if i < len(spd_arr)]
            except Exception:
                speeds = []
            try:
                dirs = [float(dir_arr[i]) for i in idxs if i < len(dir_arr)]
            except Exception:
                dirs = []
            try:
                prs = [float(prs_arr[i]) for i in idxs if i < len(prs_arr)]
            except Exception:
                prs = []
            if speeds:
                wind_ms = kmh_to_ms(sum(speeds) / len(speeds))
            mean_dir = _circular_mean_deg(dirs)
            wind_dir = int(round(mean_dir)) if mean_dir is not None else wind_dir
            if prs:
                press_val = int(round(sum(prs) / len(prs)))

    if wind_ms is None or wind_dir is None or press_val is None:
        cur = (wm.get("current") or wm.get("current_weather") or {})
        if wind_ms is None:
            spd = _pick(
                cur,
                "windspeed_10m",
                "windspeed",
                "wind_speed_10m",
                "wind_speed",
            )
            wind_ms = kmh_to_ms(spd) if isinstance(spd, (int, float)) else wind_ms
        if wind_dir is None:
            wdir = _pick(
                cur,
                "winddirection_10m",
                "winddirection",
                "wind_dir_10m",
                "wind_dir",
            )
            if isinstance(wdir, (int, float)):
                wind_dir = int(round(float(wdir)))
        if press_val is None:
            pcur = _pick(cur, "surface_pressure", "pressure")
            if isinstance(pcur, (int, float)):
                press_val = int(round(float(pcur)))
    return wind_ms, wind_dir, press_val, trend

def _fetch_temps_for_offset(
    lat: float, lon: float, tz_name: str, offset_days: int
) -> Tuple[Optional[float], Optional[float], Optional[int]]:
    wm = get_weather(lat, lon) or {}
    return _temps_for_offset_from_weather(wm, pendulum.timezone(tz_name), offset_days)


def _temps_for_offset_from_weather(
    wm: Dict[str, Any], tz: pendulum.Timezone, offset_days: int
) -> Tuple[Optional[float], Optional[float], Optional[int]]:
    daily = wm.get("daily") or {}

    idx = _daily_index_for_offset(wm, tz, offset_days)
    if idx is None:
        return None, None, None

    def _num(arr, i):
        try:
            v = arr[i]
            return float(v) if v is not None else None
        except Exception:
            return None

    tmax = _num(daily.get("temperature_2m_max", []), idx)
    tmin = _num(daily.get("temperature_2m_min", []), idx)
    wc   = None
    try:
        arr_wc = daily.get("weathercode") or []
        if isinstance(arr_wc, list) and idx < len(arr_wc):
            wc = int(arr_wc[idx]) if arr_wc[idx] is not None else None
    except Exception:
        wc = None
    return tmax, tmin, wc


def _daily_wind_kmh_for_offset(
    wm: Dict[str, Any], tz: pendulum.Timezone, offset_days: int, *, gust: bool = False
) -> Optional[float]:
    daily = wm.get("daily") or {}
    idx = _daily_index_for_offset(wm, tz, offset_days)
    if idx is None:
        return None
    keys = (
        ("wind_gusts_10m_max", "windgusts_10m_max", "wind_gust_max", "windgust_max")
        if gust
        else ("wind_speed_10m_max", "windspeed_10m_max", "wind_speed_max", "windspeed_max")
    )
    for key in keys:
        arr = daily.get(key) or []
        try:
            value = arr[idx]
            if value is not None:
                return float(value)
        except Exception:
            continue
    return None


def _weather_core_for_offset(wm: Dict[str, Any], tz: pendulum.Timezone, offset_days: int) -> bool:
    t_day, t_night, _ = _temps_for_offset_from_weather(wm, tz, offset_days)
    wind_ms, _wind_dir, _press, _trend = pick_header_metrics_for_offset(wm, tz, offset_days)
    if wind_ms is None:
        wind_ms = kmh_to_ms(_daily_wind_kmh_for_offset(wm, tz, offset_days))
    return isinstance(t_day, (int, float)) and isinstance(t_night, (int, float)) and isinstance(wind_ms, (int, float))


def _get_weather_with_retry(
    lat: float,
    lon: float,
    *,
    source_label: str,
    validator=None,
    attempts: int = 3,
    backoff_s: float = 0.6,
) -> Dict[str, Any]:
    last: Dict[str, Any] = {}
    total = max(1, int(attempts))
    for attempt in range(1, total + 1):
        try:
            wm = get_weather(lat, lon) or {}
        except Exception as exc:
            wm = {}
            logging.warning("%s: weather source failed on attempt %s/%s: %s", source_label, attempt, total, exc)
        last = wm
        if wm and (validator is None or validator(wm)):
            if attempt > 1:
                logging.info("%s: weather source recovered on attempt %s/%s", source_label, attempt, total)
            return wm
        logging.warning("%s: weather source incomplete on attempt %s/%s", source_label, attempt, total)
        if attempt < total:
            time.sleep(max(0.0, float(backoff_s)))
    return last or {}


def _kld_visibility_for_post(
    weather_data: Dict[str, Any],
    air_data: Dict[str, Any],
    *,
    post_type: str,
    target_date: str,
    tz_name: str,
) -> tuple[KldVisibilityContext, Optional[str]]:
    payload: Dict[str, Any] = weather_data if isinstance(weather_data, dict) else {}
    if not visibility_payload_has_morning_window(payload, target_date=target_date, tz=tz_name):
        try:
            dedicated = get_visibility_weather(
                KLD_LAT,
                KLD_LON,
                tz=tz_name,
                target_date=target_date,
            ) or {}
        except Exception as exc:
            dedicated = {}
            logging.info("KLD visibility source unavailable: %s", exc)
        if dedicated:
            if post_type.startswith("morn") and not isinstance(dedicated.get("current"), dict):
                current = payload.get("current")
                if isinstance(current, dict):
                    dedicated["current"] = current
            payload = dedicated

    context = get_kld_visibility_context(
        payload,
        post_type=post_type,
        target_date=target_date,
        tz=tz_name,
        air_data=air_data,
        location_label="Калининград",
    )
    line = build_kld_visibility_line(context, post_type=post_type)
    aqi = air_data.get("aqi") if isinstance(air_data, dict) else None
    air_penalty = 0.8 if isinstance(aqi, (int, float)) and aqi > 80 else 0.0
    logging.info(
        "KLD visibility diagnostics: %s",
        json.dumps(
            visibility_diagnostics(
                context,
                air_penalty=air_penalty,
                fog_text_added=bool(line),
                fog_visual_rule=context.condition != "clear",
            ),
            ensure_ascii=False,
            sort_keys=True,
        ),
    )
    return context, line

def day_night_stats(lat: float, lon: float, tz: str = "UTC") -> Dict[str, Optional[float]]:
    wm = get_weather(lat, lon) or {}
    daily = wm.get("daily") or {}
    tz_obj = pendulum.timezone(tz)

    idx = _daily_index_for_offset(wm, tz_obj, 1)
    if idx is None:
        return {}

    def _num(arr, i):
        try:
            return float(arr[i]) if i < len(arr) and arr[i] is not None else None
        except Exception:
            return None

    return {
        "t_day_max": _num(daily.get("temperature_2m_max", []), idx),
        "t_night_min": _num(daily.get("temperature_2m_min", []), idx),
        "rh_min": _num(daily.get("relative_humidity_2m_min", []), idx),
        "rh_max": _num(daily.get("relative_humidity_2m_max", []), idx),
    }

def fetch_tomorrow_temps(
    lat: float, lon: float, tz: str = "UTC"
) -> Tuple[Optional[float], Optional[float]]:
    tmax, tmin, _ = _fetch_temps_for_offset(lat, lon, tz, 1)
    return tmax, tmin

# === шторм-флаги ==================
def _tomorrow_hourly_indices(wm: Dict[str, Any], tz: pendulum.Timezone) -> List[int]:
    times = _hourly_times(wm)
    tom = pendulum.now(tz).add(days=1).date()
    idxs: List[int] = []
    for i, dt_ in enumerate(times):
        try:
            if dt_.in_tz(tz).date() == tom:
                idxs.append(i)
        except Exception:
            pass
    return idxs

def storm_flags_for_tomorrow(wm: Dict[str, Any], tz: pendulum.Timezone) -> Dict[str, Any]:
    hourly = wm.get("hourly") or {}
    idxs = _tomorrow_hourly_indices(wm, tz)
    if not idxs:
        return {"warning": False}

    def _arr(*names, default=None):
        v = _pick(hourly, *names, default=default)
        return v if isinstance(v, list) else []

    def _vals(arr):
        out = []
        for i in idxs:
            if i < len(arr):
                try:
                    out.append(float(arr[i]))
                except Exception:
                    pass
        return out

    speeds_kmh = _vals(_arr("windspeed_10m", "windspeed", "wind_speed_10m", "wind_speed", default=[]))
    gusts_kmh  = _vals(_arr("windgusts_10m", "wind_gusts_10m", "wind_gusts", default=[]))
    rain_mm_h  = _vals(_arr("rain", default=[]))
    tprob      = _vals(_arr("thunderstorm_probability", default=[]))

    max_speed_ms = kmh_to_ms(max(speeds_kmh)) if speeds_kmh else None
    max_gust_ms  = kmh_to_ms(max(gusts_kmh))  if gusts_kmh  else None
    heavy_rain   = (max(rain_mm_h) >= 8.0) if rain_mm_h else False
    thunder      = (max(tprob) >= 60) if tprob else False

    reasons = []
    if isinstance(max_speed_ms, (int, float)) and max_speed_ms >= 13:
        reasons.append(f"ветер до {max_speed_ms:.0f} м/с")
    if isinstance(max_gust_ms, (int, float)) and max_gust_ms >= 17:
        reasons.append(f"порывы до {max_gust_ms:.0f} м/с")
    if heavy_rain:
        reasons.append("сильный дождь")
    if thunder:
        reasons.append("гроза")

    return {
        "max_speed_ms": max_speed_ms,
        "max_gust_ms": max_gust_ms,
        "heavy_rain": heavy_rain,
        "thunder": thunder,
        "warning": bool(reasons),
        "warning_text": "⚠️ <b>Штормовое</b>: " + ", ".join(reasons) if reasons else "",
    }

def _is_air_bad(air: Dict[str, Any]) -> Tuple[bool, str, str]:
    try:
        aqi = float(air.get("aqi")) if air.get("aqi") is not None else None
    except Exception:
        aqi = None
    pm25 = air.get("pm25")
    pm10 = air.get("pm10")
    worst_label = "умеренный"
    reason_parts: List[str] = []
    bad = False

    def _num(v):
        try:
            return float(v)
        except Exception:
            return None

    p25 = _num(pm25)
    p10 = _num(pm10)
    if aqi is not None and aqi >= 100:
        bad = True
        if aqi >= 150:
            worst_label = "высокий"
        reason_parts.append(f"AQI {aqi:.0f}")
    if p25 is not None and p25 > 35:
        bad = True
        if p25 > 55:
            worst_label = "высокий"
        reason_parts.append(f"PM₂.₅ {p25:.0f}")
    if p10 is not None and p10 > 50:
        bad = True
        if p10 > 100:
            worst_label = "высокий"
        reason_parts.append(f"PM₁₀ {p10:.0f}")
    reason = ", ".join(reason_parts) if reason_parts else "показатели в норме"
    return bad, worst_label, reason

# ────────────────────────── Советы ──────────────────────────
SAFE_TIPS_FALLBACKS = {
    "здоровый день": [
        "🚶 30–40 мин лёгкой активности.",
        "🥤 Пейте воду и делайте короткие паузы.",
        "😴 Спланируйте 7–9 часов сна.",
    ],
    "плохая погода": [
        "🧥 Тёплые слои и непромокаемая куртка.",
        "🌧 Перенесите дела под крышу; больше пауз.",
        "🚗 Заложите время на дорогу.",
    ],
    "магнитные бури": [
        "🧘 Уменьшите перегрузки, больше отдыха.",
        "💧 Больше воды и магний/калий в рационе.",
        "😴 Режим сна, меньше экранов вечером.",
    ],
    "плохой воздух": [
        "😮‍💨 Сократите время на улице и проветривания.",
        "🪟 Используйте фильтры/проветривание по ситуации.",
        "🏃 Тренировки — в помещении.",
    ],
    "волны Шумана": [
        "🧘 Спокойный темп дня, без авралов.",
        "🍵 Лёгкая еда, тёплые напитки.",
        "😴 Лёгкая прогулка и ранний сон.",
    ],
}

def safe_tips(theme: str) -> List[str]:
    k = (theme or "здоровый день").strip().lower()
    if gpt_blurb:
        try:
            _, tips = gpt_blurb(k)
            tips = [str(x).strip() for x in (tips or []) if x]
            if tips:
                return tips[:3]
        except Exception as e:
            logging.warning("LLM tips failed: %s", e)
    return SAFE_TIPS_FALLBACKS.get(k, SAFE_TIPS_FALLBACKS["здоровый день"])

# ────────────────────────── Шуман ──────────────────────────
def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        if not path.exists():
            return None
        return json.loads(path.read_text("utf-8"))
    except Exception:
        return None

def _schu_freq_status(freq: Optional[float]) -> tuple[str, str]:
    if not isinstance(freq, (int, float)):
        return "🟡 колебания", "yellow"
    f = float(freq)
    if 7.4 <= f <= 8.4:
        return ("🟢 в норме", "green") if (7.7 <= f <= 8.1) else ("🟡 колебания", "yellow")
    return "🔴 сильное отклонение", "red"

def get_schumann_with_fallback() -> Dict[str, Any]:
    try:
        import schumann  # type: ignore
        if hasattr(schumann, "get_schumann"):
            payload = schumann.get_schumann() or {}
            return {
                "freq": payload.get("freq"),
                "status": payload.get("status") or _schu_freq_status(payload.get("freq"))[0],
                "status_code": payload.get("status_code") or _schu_freq_status(payload.get("freq"))[1],
            }
    except Exception:
        pass
    here = Path(__file__).parent
    js = _read_json(here / "data" / "schumann_hourly.json") or {}
    st, code = _schu_freq_status(js.get("freq"))
    return {"freq": js.get("freq"), "status": st, "status_code": code}

def schumann_line(s: Dict[str, Any]) -> Optional[str]:
    if (s or {}).get("status_code") == "green":
        return None
    f = s.get("freq")
    fstr = f"{f:.2f} Гц" if isinstance(f, (int, float)) else "н/д"
    return f"{s.get('status', 'н/д')} • Шуман: {fstr}"

# ────────────────────────── Safecast/радиация ──────────────────────────
CPM_TO_USVH = float(os.getenv("CPM_TO_USVH", "0.000571"))

def load_safecast() -> Optional[Dict[str, Any]]:
    paths: List[Path] = []
    if os.getenv("SAFECAST_FILE"):
        paths.append(Path(os.getenv("SAFECAST_FILE")))
    here = Path(__file__).parent
    paths.append(here / "data" / "safecast_kaliningrad.json")
    for p in paths:
        sc = _read_json(p)
        if not sc:
            continue
        ts = sc.get("ts")
        if not isinstance(ts, (int, float)):
            continue
        now_ts = pendulum.now("UTC").int_timestamp
        if now_ts - int(ts) <= 24 * 3600:
            return sc
    return None

def _pm_level(pm25: Optional[float], pm10: Optional[float]) -> Tuple[str, str]:
    def l25(x: float) -> int:
        return 0 if x <= 15 else 1 if x <= 35 else 2 if x <= 55 else 3
    def l10(x: float) -> int:
        return 0 if x <= 30 else 1 if x <= 50 else 2 if x <= 100 else 3
    worst = -1
    if isinstance(pm25, (int, float)):
        worst = max(worst, l25(float(pm25)))
    if isinstance(pm10, (int, float)):
        worst = max(worst, l10(float(pm10)))
    if worst < 0:
        return "⚪", "н/д"
    return (
        ["🟢", "🟡", "🟠", "🔴"][worst],
        ["низкий", "умеренный", "высокий", "очень высокий"][worst],
    )

def _rad_risk(usvh: float) -> Tuple[str, str]:
    if usvh <= 0.15:
        return "🟢", "низкий"
    if usvh <= 0.30:
        return "🟡", "повышенный"
    return "🔴", "высокий"

def safecast_summary_line() -> Optional[str]:
    sc = load_safecast()
    if not sc:
        return None
    pm25, pm10 = sc.get("pm25"), sc.get("pm10")
    cpm, usvh  = sc.get("cpm"), sc.get("radiation_usvh")
    if not isinstance(usvh, (int, float)) and isinstance(cpm, (int, float)):
        usvh = float(cpm) * CPM_TO_USVH
    baseline = sc.get("baseline_usvh") or sc.get("radiation_baseline_usvh") or sc.get("usual_usvh")
    if not isinstance(usvh, (int, float)) or not isinstance(baseline, (int, float)):
        logging.info("Safecast KLD omitted: missing radiation value or baseline")
        return None
    ts = sc.get("ts")
    age_min = None
    if isinstance(ts, (int, float)):
        age_min = int((pendulum.now("UTC").int_timestamp - int(ts)) / 60)
    if age_min is None or age_min < 0 or age_min > 24 * 60:
        logging.info("Safecast KLD omitted: stale or missing timestamp age_min=%s", age_min)
        return None
    parts: List[str] = []
    em, lbl = _pm_level(pm25, pm10)
    pm_parts = []
    if isinstance(pm25, (int, float)):
        pm_parts.append(f"PM₂.₅ {pm25:.0f}")
    if isinstance(pm10, (int, float)):
        pm_parts.append(f"PM₁₀ {pm10:.0f}")
    if pm_parts:
        parts.append(f"{em} {lbl} · " + " | ".join(pm_parts))
    r_em, r_lbl = _rad_risk(float(usvh))
    delta = float(usvh) - float(baseline)
    if delta >= 0.02:
        interp = f"немного выше локального фона, {r_em} {r_lbl}"
    elif delta <= -0.02:
        interp = f"ниже локального фона, {r_em} {r_lbl}"
    else:
        interp = f"около локального фона, {r_em} {r_lbl}"
    age_txt = f"{age_min} мин назад" if age_min < 180 else f"{age_min // 60}ч назад"
    parts.append(
        f"{float(usvh):.2f} μSv/h, обычно {float(baseline):.2f} — {interp}; замер {age_txt}"
    )
    if not parts:
        return None
    return "🧪 Safecast: " + " · ".join(parts)

def radiation_line(lat: float, lon: float) -> Optional[str]:
    data = get_radiation(lat, lon) or {}
    dose = data.get("dose")
    if isinstance(dose, (int, float)):
        em, lbl = _rad_risk(float(dose))
        return f"{em} Радиация: {float(dose):.3f} μSv/h — {lbl}"
    return None

# ────────────────────────── UVI ──────────────────────────
def _kld_quake_line_24h() -> Optional[str]:
    if os.getenv("KLD_QUAKES_24H", "").strip().lower() not in ("1", "true", "yes", "on"):
        return None
    show_calm = os.getenv("KLD_QUAKE_SHOW_CALM", "0").strip().lower() in ("1", "true", "yes", "on")
    try:
        hours = int(float(os.getenv("KLD_QUAKE_HOURS", "24")))
    except Exception:
        hours = 24
    try:
        radius_km = float(os.getenv("KLD_QUAKE_RADIUS_KM", "500"))
    except Exception:
        radius_km = 500.0
    try:
        min_mag = float(os.getenv("KLD_QUAKE_MIN_MAG", "0.9"))
    except Exception:
        min_mag = 0.9
    try:
        events = get_recent_earthquakes_kld(hours=hours, radius_km=radius_km, min_mag=min_mag)
        return build_kld_quake_line(
            events,
            tz=os.getenv("TZ", "Europe/Kaliningrad"),
            show_calm=show_calm,
            publish_empty=show_calm,
            publish_source_failure=False,
        )
    except Exception:
        logging.warning(
            "KLD seismic monitoring failed before formatting: hours=%s radius_km=%s min_mag=%.1f",
            hours,
            radius_km,
            min_mag,
            exc_info=True,
        )
        return build_kld_quake_line(
            None,
            tz=os.getenv("TZ", "Europe/Kaliningrad"),
            show_calm=show_calm,
            publish_empty=False,
            publish_source_failure=False,
        )


def uvi_label(x: float) -> str:
    if x < 3:
        return "низкий"
    if x < 6:
        return "умеренный"
    if x < 8:
        return "высокий"
    if x < 11:
        return "очень высокий"
    return "экстремальный"

def uvi_advice(uvi: float) -> str:
    try:
        u = float(uvi)
    except Exception:
        return ""
    if u >= 11:
        return "Экстремально: тень 11–16, закрытая одежда, SPF 50+"
    if u >= 8:
        return "Очень высокий: SPF 50, тень 11–16, очки/кепка"
    if u >= 6:
        return "Высокий: SPF 30–50, очки/кепка, тень в полдень"
    return ""

def uvi_for_offset(
    wm: Dict[str, Any], tz: pendulum.Timezone, offset_days: int
) -> Dict[str, Optional[float | str]]:
    daily = wm.get("daily") or {}
    hourly = wm.get("hourly") or {}
    date_obj = pendulum.today(tz).add(days=offset_days).date()
    times = hourly.get("time") or hourly.get("time_local") or []
    uvi_arr = hourly.get("uv_index") or hourly.get("uv_index_clear_sky") or []
    uvi_now = None
    try:
        if times and uvi_arr:
            uvi_now = float(uvi_arr[0]) if isinstance(uvi_arr[0], (int, float)) else None
    except Exception:
        uvi_now = None

    uvi_max = None
    try:
        dts = _daily_times(wm)
        if dts and date_obj in dts:
            idx = dts.index(date_obj)
            uvi_max = float((daily.get("uv_index_max") or [None])[idx])  # type: ignore
    except Exception:
        pass
    if uvi_max is None and times and uvi_arr:
        vals = []
        for t, v in zip(times, uvi_arr):
            if t and str(t).startswith(date_obj.to_date_string()) and isinstance(v, (int, float)):
                vals.append(float(v))
        if vals:
            uvi_max = max(vals)
    return {"uvi": uvi_now, "uvi_max": uvi_max}

# ────────────────────────── гидрик по SST ──────────────────────────
def wetsuit_hint_by_sst(sst: Optional[float]) -> Optional[str]:
    if not isinstance(sst, (int, float)):
        return None
    t = float(sst)
    if t >= WSUIT_NONE:
        return None
    if t >= WSUIT_SHORTY:
        return "короткий гидрокостюм 2 мм"
    if t >= WSUIT_32:
        return "гидрокостюм 3/2 мм"
    if t >= WSUIT_43:
        return "гидрокостюм 4/3 мм (боты)"
    if t >= WSUIT_54:
        return "гидрокостюм 5/4 мм (боты, перчатки)"
    if t >= WSUIT_65:
        return "гидрокостюм 5/4 мм + капюшон (боты, перчатки)"
    return "гидрокостюм 6/5 мм + капюшон (боты, перчатки)"

# ────────────────────────── FX (утро) ──────────────────────────
def fx_morning_line(date_local: pendulum.DateTime, tz: pendulum.Timezone) -> Optional[str]:
    try:
        import importlib
        fx = importlib.import_module("fx")
        rates = fx.get_rates(date=date_local, tz=tz) or {}  # type: ignore[attr-defined]
    except Exception as e:
        logging.info("FX morning: нет fx.get_rates: %s", e)
        return None

    def token(code: str, name: str) -> str:
        r = rates.get(code) or {}
        val = r.get("value")
        dlt = r.get("delta")
        try:
            vs = f"{float(val):.2f}"
        except Exception:
            vs = "н/д"
        try:
            delta = float(dlt)
        except Exception:
            delta = 0.0
        if delta > 0:
            ds = f"↑{abs(delta):.2f}"
        elif delta < 0:
            ds = f"↓{abs(delta):.2f}"
        else:
            ds = "→0.00"
        return f"{name} {vs} {RUB} {ds}"

    return "💱 Курсы (утро): " + " • ".join(
        [token("USD", "USD"), token("EUR", "EUR"), token("CNY", "CNY")]
    )

# ────────────────────────── «шторм/итого» ──────────────────────────
def _day_indices(wm: Dict[str, Any], tz: pendulum.Timezone, offset: int) -> List[int]:
    times = _hourly_times(wm)
    date_obj = pendulum.today(tz).add(days=offset).date()
    idxs = []
    for i, dt_ in enumerate(times):
        try:
            if dt_.in_tz(tz).date() == date_obj:
                idxs.append(i)
        except Exception:
            pass
    return idxs

def _vals(arr, idxs):
    out = []
    for i in idxs:
        if i < len(arr) and arr[i] is not None:
            try:
                out.append(float(arr[i]))
            except Exception:
                pass
    return out

def storm_short_text(wm: Dict[str, Any], tz: pendulum.Timezone) -> str:
    hourly = wm.get("hourly") or {}
    idxs = _day_indices(wm, tz, DAY_OFFSET)
    if not idxs:
        return "без шторма"
    gusts = _vals(hourly.get("wind_gusts_10m") or hourly.get("windgusts_10m") or [], idxs)
    rain  = _vals(hourly.get("rain") or [], idxs)
    thp   = _vals(hourly.get("thunderstorm_probability") or [], idxs)
    if (
        (max(gusts, default=0) / 3.6 >= STORM_GUST_MS)
        or (max(rain, default=0) >= ALERT_RAIN_MM_H)
        or (max(thp, default=0) >= ALERT_TSTORM_PROB_PC)
    ):
        return "шторм"
    return "без шторма"

def storm_alert_line(wm: Dict[str, Any], tz: pendulum.Timezone) -> Optional[str]:
    hourly = wm.get("hourly") or {}
    idxs = _day_indices(wm, tz, DAY_OFFSET)
    if not idxs:
        return None
    gust_kmh = _vals(hourly.get("wind_gusts_10m") or hourly.get("windgusts_10m") or [], idxs)
    rain     = _vals(hourly.get("rain") or [], idxs)
    thp      = _vals(hourly.get("thunderstorm_probability") or [], idxs)
    g_max = max(gust_kmh, default=0) / 3.6
    r_max = max(rain, default=0)
    t_max = max(thp, default=0)
    parts = []
    if g_max >= ALERT_GUST_MS:
        parts.append(f"ветер: порывы до {int(round(g_max))} м/с")
    if r_max >= ALERT_RAIN_MM_H:
        parts.append(f"дождь до {int(round(r_max))} мм/ч")
    if t_max >= ALERT_TSTORM_PROB_PC:
        parts.append(f"гроза до {int(round(t_max))}%")
    if parts:
        return "⚠️ Штормовое предупреждение: " + "; ".join(parts)
    return None

# ────────────────────────── водные активности ──────────────────────────
def _deg_diff(a: float, b: float) -> float:
    return abs((a - b + 180) % 360 - 180)

def _cardinal(deg: Optional[float]) -> Optional[str]:
    if deg is None:
        return None
    dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    idx = int((deg + 22.5) // 45) % 8
    return dirs[idx]

def _shore_class(city: str, wind_from_deg: Optional[float]) -> Tuple[Optional[str], Optional[str]]:
    if wind_from_deg is None:
        return None, None
    face_deg, src_label = _shore_face_for_city(city)
    if face_deg is None:
        return None, src_label
    diff = _deg_diff(wind_from_deg, face_deg)
    if diff <= 45:
        return "onshore", src_label
    if diff >= 135:
        return "offshore", src_label
    return "cross", src_label

def _fetch_wave_for_tomorrow(
    lat: float,
    lon: float,
    tz_obj: pendulum.Timezone,
    prefer_hour: int = 12,
) -> Tuple[Optional[float], Optional[float]]:
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
        idx = _nearest_index_for_day(
            times,
            pendulum.now(tz_obj).add(days=1).date(),
            prefer_hour,
            tz_obj,
        )
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

def _water_highlights(
    city: str,
    la: float,
    lo: float,
    tz_obj: pendulum.Timezone,
    sst_hint: Optional[float] = None,
) -> Optional[str]:
    wm = get_weather(la, lo) or {}
    wind_ms, wind_dir, _, _ = pick_tomorrow_header_metrics(wm, tz_obj)
    wave_h, _ = _fetch_wave_for_tomorrow(la, lo, tz_obj)

    def _gust_at_noon(wm_: Dict[str, Any], tz_: pendulum.Timezone) -> Optional[float]:
        hourly = wm_.get("hourly") or {}
        times = _hourly_times(wm_)
        idx = _nearest_index_for_day(
            times,
            pendulum.now(tz_).add(days=1).date(),
            12,
            tz_,
        )
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
    if kite_good:
        goods.append("Кайт/Винг/Винд")
    if sup_good:
        goods.append("SUP")
    if surf_good:
        goods.append("Сёрф")

    if not goods:
        if DEBUG_WATER:
            logging.info(
                "WATER[%s]: no good. wind=%s dir=%s wave_h=%s gust=%s shore=%s",
                city,
                wind_val,
                wind_dir,
                wave_h,
                gust_val,
                shore,
            )
        return None

    sst = sst_hint if isinstance(sst_hint, (int, float)) else get_sst(la, lo)
    suit_txt = wetsuit_hint_by_sst(sst)
    suit_part = f" • {suit_txt}" if suit_txt else ""

    dir_part = f" ({card}/{shore})" if card or shore else ""
    spot_part = (
        f" @{shore_src}"
        if shore_src and shore_src not in (city, f"ENV:SHORE_FACE_{_env_city_key(city)}")
        else ""
    )
    env_mark = " (ENV)" if shore_src and str(shore_src).startswith("ENV:") else ""

    return "🧜‍♂️ Отлично: " + "; ".join(goods) + spot_part + env_mark + dir_part + suit_part

# ───────────── Астроблок ─────────────
ZODIAC = {
    "Овен": "♈", "Телец": "♉", "Близнецы": "♊", "Рак": "♋",
    "Лев": "♌", "Дева": "♍", "Весы": "♎", "Скорпион": "♏",
    "Стрелец": "♐", "Козерог": "♑", "Водолей": "♒", "Рыбы": "♓"
}

def zsym(s: str) -> str:
    for name, sym in ZODIAC.items():
        s = s.replace(name, sym)
    return s

def load_calendar(path: str = "lunar_calendar.json") -> dict:
    here = Path(__file__).parent
    candidates = [
        Path(path),
        here / path,
        here / "data" / path,
    ]
    for p in candidates:
        try:
            if not p.exists():
                continue
            data = json.loads(p.read_text("utf-8"))
            if isinstance(data, dict) and isinstance(data.get("days"), dict):
                return data["days"]
            if isinstance(data, dict):
                return data
        except Exception as e:
            logging.warning("load_calendar: failed to read %s: %s", p, e)
    return {}

def _parse_voc_dt(s: str, tz: pendulum.tz.timezone.Timezone):
    if not s:
        return None
    try:
        return pendulum.parse(s).in_tz(tz)
    except Exception:
        pass
    try:
        dmy, hm = s.split()
        d, m = map(int, dmy.split("."))
        hh, mm = map(int, hm.split(":"))
        year = pendulum.today(tz).year
        return pendulum.datetime(year, m, d, hh, mm, tz=tz)
    except Exception:
        return None

def voc_interval_for_date(rec: dict, tz_local: str = "Asia/Nicosia"):
    if not isinstance(rec, dict):
        return None
    voc = (rec.get("void_of_course") or rec.get("voc") or rec.get("void") or {})
    if not isinstance(voc, dict):
        return None
    s = voc.get("start") or voc.get("from") or voc.get("start_time")
    e = voc.get("end")   or voc.get("to")   or voc.get("end_time")
    if not s or not e:
        return None
    tz = pendulum.timezone(tz_local)
    t1 = _parse_voc_dt(s, tz)
    t2 = _parse_voc_dt(e, tz)
    if not t1 or not t2:
        return None
    return (t1, t2)

def lunar_advice_for_date(cal: dict, date_obj) -> List[str]:
    key = date_obj.to_date_string() if hasattr(date_obj, "to_date_string") else str(date_obj)
    rec = (cal or {}).get(key, {}) or {}
    adv = rec.get("advice")
    if isinstance(adv, str):
        items = [adv]
    elif isinstance(adv, list):
        items = adv
    elif adv is None:
        items = []
    else:
        items = [str(adv)]
    return [str(x).strip() for x in items if str(x).strip()][:3]

def _astro_llm_bullets(date_key: str, phase_name: str, percent: int | None, sign: str | None, voc_text: str | None) -> list[str]:
    cache_path = CACHE_DIR / f"astro_{date_key}.txt"

    def _looks_like_date_only(s: str) -> bool:
        return bool(re.match(r"^\s*(?:✨\s*)?\d{1,2}\s+[А-Яа-яёЁ]+\s*(?:20\d{0,2})?\s*$", s.strip()))

    def _strip_leading_date_prefix(s: str) -> str:
        return re.sub(
            r"^\s*(?:✨\s*)?\d{1,2}\s+[А-Яа-яёЁ]+\s*(?:20\d{2})?\s*[:—–-]\s*",
            "",
            s,
        )

    def _clean_line(s: str) -> str:
        s = _sanitize_line(s)
        if not s:
            return ""
        s = _strip_leading_date_prefix(s).strip()
        if not s or _looks_like_date_only(s):
            return ""
        s = re.sub(r"^\s*[-•–—]+\s*", "", s).strip()
        if re.search(r"\b20\d?$", s):
            return ""
        return s

    def _accept(lines: list[str]) -> list[str]:
        cleaned: list[str] = []
        for ln in lines:
            ln = _clean_line(ln)
            if not ln:
                continue
            if _looks_gibberish(ln):
                continue
            cleaned.append(ln)
        if len(cleaned) >= 2 and any(len(x) >= 18 for x in cleaned):
            return cleaned[:3]
        return []

    if cache_path.exists():
        try:
            cached = cache_path.read_text(encoding="utf-8").splitlines()
            ok = _accept(cached)
            if ok:
                return ok
        except Exception:
            pass

    if not (USE_DAILY_LLM and gpt_complete):
        return []

    system = (
        "Ты пишешь короткий блок 'Астрособытия' для Telegram. "
        "Нужно 2–3 лаконичных пункта (каждый с нового ряда), "
        "в нейтральном практичном тоне. Без длинных вступлений, без нумерации. "
        "Можно начинать пункты с одного эмодзи (✨/🌙/⚡️)."
    )

    prompt = (
        f"Фаза Луны: {phase_name or 'н/д'}\n"
        f"Освещённость: {(str(percent) + '%') if isinstance(percent,int) and percent else 'н/д'}\n"
        f"Знак Луны: {sign or 'н/д'}\n"
        f"VoC: {voc_text or 'н/д'}\n\n"
        "Сформулируй 2–3 коротких пункта с практическим смыслом на день: "
        "настроение/энергия, дела и фокус, чего избегать. "
        "Не вставляй дату в пункты."
    )

    try:
        resp = gpt_complete(prompt=prompt, system=system, temperature=0.6, max_tokens=220)
    except Exception:
        resp = None

    if not resp:
        return []

    raw_lines = str(resp).splitlines()
    ok = _accept(raw_lines)

    if not ok:
        blob = _sanitize_line(str(resp))
        parts = re.split(r"[•\n]+", blob)
        ok = _accept(parts)

    if not ok:
        return []

    with_emoji: list[str] = []
    for ln in ok:
        ln2 = ln.strip()
        if not re.match(r"^[\u2600-\u27BF\U0001F300-\U0001FAFF]", ln2):
            ln2 = "✨ " + ln2
        with_emoji.append(ln2)

    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text("\n".join(with_emoji), encoding="utf-8")
    except Exception:
        pass

    return with_emoji

def _astro_markers_from_rec(rec: dict) -> list[str]:
    if not isinstance(rec, dict):
        return []

    def _truthy(v) -> bool:
        if isinstance(v, bool):
            return v
        if isinstance(v, (int, float)):
            return v > 0
        if isinstance(v, str):
            s = v.strip().lower()
            return s in {"1","+","yes","true","ok","good","best","favorable","да","хорошо","рекомендуется","✅"}
        if isinstance(v, list):
            return len(v) > 0
        return False

    markers: list[str] = []

    if any(_truthy(rec.get(k)) for k in ("shopping","purchases","buying","money")):
        markers.append("🛍️ покупки")
    if any(_truthy(rec.get(k)) for k in ("travel","journey","trip")):
        markers.append("✈️ путешествия")
    if any(_truthy(rec.get(k)) for k in ("haircut","beauty","spa")):
        markers.append("💇 стрижка/уход")

    gf = rec.get("good_for") or rec.get("favorable_for") or rec.get("goodFor")
    if isinstance(gf, str):
        gf_list = [x.strip().lower() for x in re.split(r"[,;/]+", gf) if x.strip()]
    elif isinstance(gf, list):
        gf_list = [str(x).strip().lower() for x in gf if str(x).strip()]
    else:
        gf_list = []

    if gf_list:
        if any(x in gf_list for x in ("shopping","purchases","buy","покупки")) and "🛍️ покупки" not in markers:
            markers.append("🛍️ покупки")
        if any(x in gf_list for x in ("travel","trip","journey","путешествия","дорога")) and "✈️ путешествия" not in markers:
            markers.append("✈️ путешествия")

    return markers


def _astro_favorable_lines(rec: dict, date_local) -> list[str]:
    """Return short lines about general/day activities based on lunar_calendar.json lists.

    Supports schema:
      rec["favorable_days"][category]["favorable"|"unfavorable"] = [day_of_month...]
    and tolerant to minor spelling variants.
    """
    try:
        day = int(getattr(date_local, "day", None) or 0)
    except Exception:
        day = 0
    if day <= 0:
        return []

    def _pick_dict(*keys):
        for k in keys:
            v = rec.get(k)
            if isinstance(v, dict):
                return v
        return {}

    fav_root = _pick_dict("favorable_days", "favourable_days") or _pick_dict("unfavorable_days", "unfavourable_days")
    if not isinstance(fav_root, dict) or not fav_root:
        return []

    def _state(category: str):
        node = fav_root.get(category)
        if not isinstance(node, dict):
            return None
        fav = node.get("favorable") or node.get("favourable") or []
        unf = node.get("unfavorable") or node.get("unfavourable") or []
        # Safer: if a day is in both lists, treat as unfavorable.
        try:
            if day in unf or str(day) in unf:
                return False
            if day in fav or str(day) in fav:
                return True
        except Exception:
            return None
        return None

    out: list[str] = []

    s_general = _state("general")
    if s_general is False:
        out.append("⛔️ В целом: неблагоприятный день.")
    elif s_general is True:
        out.append("✅ В целом: благоприятный день.")

    priorities = [
        ("haircut", "💇 Стрижка"),
        ("travel", "✈️ Поездки"),
        ("shopping", "🛍️ Покупки"),
    ]
    for cat, label in priorities:
        s = _state(cat)
        # Keep it useful but not gloomy: show haircut both ways; other categories only when favorable.
        if cat == "haircut":
            if s is True:
                out.append(f"{label}: ✅ благоприятно.")
            elif s is False:
                out.append(f"{label}: ⛔ лучше перенести.")
        else:
            if s is True:
                out.append(f"{label}: ✅ благоприятно.")

    # Keep section compact
    return out[:3]

def build_astro_section(astro_date=None, tz_obj=None, *, date_local=None, tz_local: str = "Asia/Nicosia") -> str:
    if date_local is None and astro_date is not None:
        date_local = astro_date
    if tz_obj is not None and getattr(tz_obj, "name", None) and (not tz_local or tz_local == "Asia/Nicosia"):
        tz_local = tz_obj.name

    try:
        tz = pendulum.timezone(tz_local)
    except Exception:
        tz = pendulum.timezone("UTC")

    if date_local is None:
        date_local = pendulum.today(tz)
    else:
        try:
            if hasattr(date_local, "in_tz"):
                date_local = date_local.in_tz(tz)
        except Exception:
            pass

    date_key = date_local.format("YYYY-MM-DD")
    cal = load_calendar()
    rec = cal.get(date_key, {}) if isinstance(cal, dict) else {}

    phase_name = str(rec.get("phase") or rec.get("phase_name") or "").strip()
    try:
        percent = int(rec.get("illumination") or rec.get("illumination_percent") or rec.get("percent") or 0) or None
    except Exception:
        percent = None

    sign = str(rec.get("sign") or rec.get("zodiac") or rec.get("moon_sign") or "").strip() or None
    voc_interval = voc_interval_for_date(rec, tz_local=tz_local)
    voc_text: Optional[str] = None
    voc_line: Optional[str] = None
    if voc_interval:
        try:
            v1, v2 = voc_interval
            voc_text = f"{v1.format('HH:mm')}–{v2.format('HH:mm')}"
            voc_line = f"⚫️ VoC: {voc_text}."
        except Exception:
            voc_text = None
            voc_line = None

    favorable_lines = _astro_favorable_lines(rec, date_local)
    marker_items = _astro_markers_from_rec(rec)
    marker_line = ""
    if (not favorable_lines) and marker_items:
        marker_line = "✅ " + " • ".join(marker_items)

    phase_clean = zsym(phase_name).strip() if phase_name else ""
    sign_sym = zsym(sign).strip() if sign else ""
    pct_txt = f"{percent}%" if isinstance(percent, int) and percent else ""

    parts = []
    if phase_clean:
        s = phase_clean
        if pct_txt and pct_txt not in s:
            s = f"{s} ({pct_txt})"
        parts.append(f"🌙 {s}")
    elif pct_txt:
        parts.append(f"🌙 Освещённость: {pct_txt}")

    if sign_sym and (sign_sym not in phase_clean):
        parts.append(sign_sym)

    moon_line = " • ".join([p for p in parts if p]).strip()

    bullets = _astro_llm_bullets(date_key, phase_name, percent, sign, voc_text)

    if not bullets:
        adv = lunar_advice_for_date(cal, date_key)
        if adv:
            bullets = [f"• {x}" for x in adv[:3] if x.strip()]

    if not bullets:
        bullets = [
            "• День подходит для спокойного планирования и аккуратных решений.",
            "• Избегайте спешки и перегруза новостями.",
        ]

    lines = ["📻 <b>Астрособытия</b>"]
    if moon_line:
        lines.append(zsym(moon_line))
    if favorable_lines:
        for ln in favorable_lines:
            lines.append(zsym(ln))
    if marker_line:
        lines.append(zsym(marker_line))

    for b in bullets[:3]:
        b = str(b).strip()
        if not b:
            continue
        if not b.startswith(("•", "✨", "🌙", "⚡️")):
            b = "• " + b
        lines.append(zsym(b))

    if voc_line:
        lines.append(voc_line)

    return "\n".join(lines)

# ────────────────────────── Morning (compact) ──────────────────────────
class KldMorningMessage(str):
    """Visible morning text plus current-run regional temperatures for FORMAT_V2."""

    def __new__(
        cls,
        text: str,
        *,
        regional_city_temperatures: list[tuple[str, float, float | None]] | None = None,
        visibility_context: KldVisibilityContext | None = None,
    ):
        obj = str.__new__(cls, text)
        obj.regional_city_temperatures = tuple(regional_city_temperatures or ())
        obj.visibility_context = visibility_context
        return obj


def _collect_morning_region_temperatures(
    sea_cities,
    other_cities,
    tz_obj: pendulum.Timezone,
    *,
    kaliningrad_high: float | None,
    kaliningrad_low: float | None,
) -> list[tuple[str, float, float | None]]:
    rows: list[tuple[str, float, float | None]] = []
    if isinstance(kaliningrad_high, (int, float)):
        low = float(kaliningrad_low) if isinstance(kaliningrad_low, (int, float)) else None
        rows.append(("Калининград", float(kaliningrad_high), low))

    seen = {"калининград"}
    for city, coords in list(sea_cities or []) + list(other_cities or []):
        city_name = str(city or "").strip()
        key = city_name.casefold()
        if not city_name or key in seen:
            continue
        seen.add(key)
        try:
            lat, lon = coords
            wm = _get_weather_with_retry(
                float(lat),
                float(lon),
                source_label=f"KLD morning regional weather: {city_name}",
                validator=lambda data: all(
                    isinstance(value, (int, float))
                    for value in _temps_for_offset_from_weather(data, tz_obj, DAY_OFFSET)[:2]
                ),
                attempts=2,
                backoff_s=0.2,
            )
            high, low, _code = _temps_for_offset_from_weather(wm, tz_obj, DAY_OFFSET)
        except Exception as exc:
            logging.warning("KLD morning regional weather unavailable for %s: %s", city_name, exc)
            continue
        if not isinstance(high, (int, float)):
            continue
        rows.append(
            (
                city_name,
                float(high),
                float(low) if isinstance(low, (int, float)) else None,
            )
        )

    logging.info("KLD morning regional temperatures collected for %d cities", len(rows))
    return rows


def build_message_morning_compact(
    region_name: str,
    sea_label: str,
    sea_cities,
    other_label: str,
    other_cities,
    tz: Union[pendulum.Timezone, str],
) -> str:
    tz_obj = pendulum.timezone(tz) if isinstance(tz, str) else tz
    date_local = pendulum.today(tz_obj)

    header = f"<b>🌅 {region_name}: погода на сегодня ({date_local.format('DD.MM.YYYY')})</b>"
    fact_text = get_fact(date_local, region_name)
    fact_text = fact_text.strip()
    fact_line = f"🌾 Доброе утро! {fact_text}" if fact_text else "🌾 Доброе утро!"

    wm_klg = _get_weather_with_retry(
        KLD_LAT,
        KLD_LON,
        source_label="KLD morning Kaliningrad weather",
        validator=lambda wm: _weather_core_for_offset(wm, tz_obj, DAY_OFFSET),
    )
    t_day, t_night, wcode = _temps_for_offset_from_weather(wm_klg, tz_obj, DAY_OFFSET)
    if t_day is None or t_night is None:
        t_day, t_night, wcode = _fetch_temps_for_offset(KLD_LAT, KLD_LON, tz_obj.name, DAY_OFFSET)
    wind_ms, wind_dir_deg, press_val, press_trend = pick_header_metrics_for_offset(wm_klg, tz_obj, DAY_OFFSET)
    if wind_ms is None:
        wind_ms = kmh_to_ms(_daily_wind_kmh_for_offset(wm_klg, tz_obj, DAY_OFFSET))

    gust = None
    try:
        times = _hourly_times(wm_klg)
        hourly = wm_klg.get("hourly") or {}
        idx_noon = _nearest_index_for_day(times, date_local.add(days=DAY_OFFSET).date(), 12, tz_obj)
        arr = hourly.get("wind_gusts_10m") or hourly.get("windgusts_10m") or []
        if idx_noon is not None and idx_noon < len(arr):
            gust = float(arr[idx_noon]) / 3.6
    except Exception:
        pass
    if gust is None:
        gust = kmh_to_ms(_daily_wind_kmh_for_offset(wm_klg, tz_obj, DAY_OFFSET, gust=True))

    regional_city_temperatures = []
    if _env_on("FORMAT_V2", False):
        regional_city_temperatures = _collect_morning_region_temperatures(
            sea_cities,
            other_cities,
            tz_obj,
            kaliningrad_high=t_day,
            kaliningrad_low=t_night,
        )

    desc = code_desc(wcode) or "—"
    tday_i   = int(round(t_day))   if isinstance(t_day, (int, float)) else None
    tnight_i = int(round(t_night)) if isinstance(t_night, (int, float)) else None
    temp_txt = f"{tday_i}/{tnight_i}{NBSP}°C" if (tday_i is not None and tnight_i is not None) else "н/д"
    if isinstance(wind_ms, (int, float)) and wind_dir_deg is not None:
        wind_txt = f"💨 {wind_ms:.1f} м/с ({compass(wind_dir_deg)})"
    elif isinstance(wind_ms, (int, float)):
        wind_txt = f"💨 {wind_ms:.1f} м/с"
    else:
        wind_txt = "💨 н/д"
    if isinstance(gust, (int, float)):
        wind_txt += f" • порывы — {int(round(gust))}"
    press_txt = f"🔹 {press_val} гПа {press_trend}" if isinstance(press_val, int) else "🔹 н/д"
    kal_line = f"Погода: 🏙️ Калининград — {temp_txt} • {desc} • {wind_txt} • {press_txt}."

    # Курсы
    fx_line = fx_morning_line(pendulum.now(tz_obj), tz_obj)

    # Закат сегодня
    sunset_line = None
    try:
        _sunrise, sunset = get_sunrise_sunset(
            KLD_LAT,
            KLD_LON,
            tz_obj.name,
            DAY_OFFSET,
        )
        if sunset:
            sunset_line = f"🌇 Закат сегодня: {sunset}"
        else:
            logging.info("KLD morning: время заката недоступно")
    except Exception as e:
        logging.info("KLD morning: не удалось получить время заката: %s", e)

    # Воздух
    air = get_air(KLD_LAT, KLD_LON) or {}
    try:
        aqi = air.get("aqi")
        aqi_i = int(round(float(aqi))) if isinstance(aqi, (int, float)) else "н/д"
    except Exception:
        aqi_i = "н/д"

    def _int_or_nd(x):
        try:
            return str(int(round(float(x))))
        except Exception:
            return "н/д"

    pm25_int = _int_or_nd(air.get("pm25"))
    pm10_int = _int_or_nd(air.get("pm10"))
    pollen = get_pollen() or {}
    pollen_risk = str(pollen.get("risk")).strip() if pollen.get("risk") else ""

    air_risk = aqi_risk_ru(aqi)
    air_emoji_main = (
        "🟠" if air_risk in ("высокий", "очень высокий")
        else ("🟡" if air_risk == "умеренный" else "🟢")
    )

    air_line = f"🏭 Воздух: {air_emoji_main} {air_risk} (AQI {aqi_i}) • PM₂.₅ {pm25_int} / PM₁₀ {pm10_int}"
    if pollen_risk:
        air_line += f" • 🌿 пыльца: {pollen_risk}"

    visibility_context, visibility_line = _kld_visibility_for_post(
        wm_klg,
        air,
        post_type="morning",
        target_date=date_local.add(days=DAY_OFFSET).to_date_string(),
        tz_name=tz_obj.name,
    )

    # УФ — только если есть смысл
    uvi_info = uvi_for_offset(wm_klg, tz_obj, DAY_OFFSET)
    uvi_line = None
    try:
        uvi_val = None
        if isinstance(uvi_info.get("uvi_max"), (int, float)):
            uvi_val = float(uvi_info["uvi_max"])
        elif isinstance(uvi_info.get("uvi"), (int, float)):
            uvi_val = float(uvi_info["uvi"])
        if isinstance(uvi_val, (int, float)) and uvi_val >= UVI_WARN_FROM:
            adv = uvi_advice(uvi_val)
            if adv:
                uvi_line = f"☀️ УФ: {uvi_val:.0f} — {uvi_label(uvi_val)} • {adv}"
    except Exception:
        pass

    # Космопогода (утром показываем по умолчанию)
    kp_val, kp_status, kp_age_min, _kp_src = _kp_global_swpc()
    age_txt = ""
    if isinstance(kp_age_min, int):
        age_txt = f", 🕓 {kp_age_min // 60}ч назад" if kp_age_min > 180 else f", 🕓 {kp_age_min} мин назад"
    kp_chunk = f"Кр {kp_val:.1f} ({kp_status}{age_txt})" if isinstance(kp_val, (int, float)) else "Кр н/д"

    sw = get_solar_wind() or {}
    v = sw.get("speed_kms")
    n = sw.get("density")
    vtxt = f"v {float(v):.0f} км/с" if isinstance(v, (int, float)) else None
    ntxt = f"n {float(n):.1f} см⁻³" if isinstance(n, (int, float)) else None
    parts = [p for p in (vtxt, ntxt) if p]
    sw_chunk = (" • 🌬️ " + ", ".join(parts) + f" — {sw.get('status', 'н/д')}") if parts else ""
    space_line = "🧲 Космопогода: " + kp_chunk + (sw_chunk or "")

    storm_line_alert = storm_alert_line(wm_klg, tz_obj)

    sc_line = safecast_summary_line()
    official_rad = radiation_line(KLD_LAT, KLD_LON)

    schu_line = schumann_line(get_schumann_with_fallback()) if (SHOW_SCHUMANN and not DISABLE_SCHUMANN) else None

    storm_short = storm_short_text(wm_klg, tz_obj)

    # (1) Утро: Kp в «Итого» — как факт (… • Кр …), без статуса/аналитики.
    kp_fact = f"Кр {kp_val:.1f}" if isinstance(kp_val, (int, float)) else "Кр н/д"
    itogo = f"🔎 Итого: воздух {air_emoji_main} • {storm_short} • {kp_fact}"

    theme = (
        "магнитные бури"
        if (isinstance(kp_val, (int, float)) and kp_val >= 5)
        else ("плохой воздух" if air_risk in ("высокий", "очень высокий") else "здоровый день")
    )
    today_line = "✅ Сегодня: " + "; ".join(safe_tips(theme)) + "."

    P: List[str] = [
        header,
        fact_line,
        kal_line,
        "",
        "———",
    ]
    if fx_line:
        P.append(fx_line)
        P.append("———")
    if SHOW_AIR:
        P.append(air_line)
        if uvi_line:
            P.append(uvi_line)
    if visibility_line:
        P.append(visibility_line)
    if sunset_line:
        P.append(sunset_line)
    P.append(build_astro_section(date_local=date_local, tz_local=tz_obj.name))
    if SHOW_SPACE:
        P.append(space_line)
    if storm_line_alert:
        P.append(storm_line_alert)
    sc_block_parts = [x for x in (sc_line, official_rad) if x]
    if sc_block_parts:
        P.append(" • ".join(sc_block_parts))
    if schu_line:
        P.append(schu_line)
    quake_line = _kld_quake_line_24h()
    if quake_line:
        P.append(quake_line)

    P.append("")
    P.append(itogo)
    P.append(today_line)
    P.append("")
    P.append("#Калининград #погода #здоровье #сегодня #море")
    return KldMorningMessage(
        "\n".join(P),
        regional_city_temperatures=regional_city_temperatures,
        visibility_context=visibility_context,
    )

# ────────────────────────── Evening (legacy) ──────────────────────────
def build_message_legacy_evening(
    region_name: str,
    sea_label: str,
    sea_cities,
    other_label: str,
    other_cities,
    tz: Union[pendulum.Timezone, str],
) -> str:
    tz_obj = pendulum.timezone(tz) if isinstance(tz, str) else tz
    tz_name = tz_obj.name

    date_weather = pendulum.today(tz_obj).add(days=DAY_OFFSET)

    header = f"<b>🌅 {region_name}: погода на завтра ({date_weather.format('DD.MM.YYYY')})</b>"
    P: List[str] = [header]

    wm_main = get_weather(KLD_LAT, KLD_LON) or {}

    stats = day_night_stats(KLD_LAT, KLD_LON, tz=tz_name)
    t_day_max = stats.get("t_day_max")
    t_night_min = stats.get("t_night_min")
    rh_min = stats.get("rh_min")
    rh_max = stats.get("rh_max")

    wcode = None
    try:
        didx = _daily_index_for_offset(wm_main, tz_obj, 1)
        wcarr = (wm_main.get("daily") or {}).get("weathercode") or []
        if isinstance(didx, int) and isinstance(wcarr, list) and didx < len(wcarr):
            wcode = int(wcarr[didx]) if wcarr[didx] is not None else None
    except Exception:
        wcode = None

    wind_ms, wind_dir_deg, press_val, press_trend = pick_tomorrow_header_metrics(wm_main, tz_obj)

    storm = storm_flags_for_tomorrow(wm_main, tz_obj)
    gust = storm.get("max_gust_ms")

    desc = code_desc(wcode) or "—"

    temp_txt = (
        f"{t_day_max:.0f}/{t_night_min:.0f}{NBSP}°C"
        if (t_day_max is not None and t_night_min is not None)
        else "н/д"
    )

    if isinstance(wind_ms, (int, float)) and wind_dir_deg is not None:
        wind_txt = f"💨 {wind_ms:.1f} м/с ({compass(wind_dir_deg)})"
    elif isinstance(wind_ms, (int, float)):
        wind_txt = f"💨 {wind_ms:.1f} м/с"
    else:
        wind_txt = "💨 н/д"

    if isinstance(gust, (int, float)):
        wind_txt += f" • порывы до {gust:.0f}"

    rh_txt = ""
    if isinstance(rh_min, (int, float)) and isinstance(rh_max, (int, float)):
        rh_txt = f" • 💧 RH {rh_min:.0f}–{rh_max:.0f}%"

    press_txt = f" • 🔹 {press_val} гПа {press_trend}" if isinstance(press_val, int) else ""

    kal_line = f"🏙️ Калининград: дн/ночь {temp_txt} • {desc} • {wind_txt}{rh_txt}{press_txt}"
    P.append(kal_line)
    P.append("———")

    if storm.get("warning"):
        P.append(storm["warning_text"])
        P.append("———")

    # Море/города (без изменений в логике сортировки — оставлено как было)
    temps_sea: Dict[str, Tuple[float, float, int, float | None]] = {}
    sea_lookup: Dict[str, Tuple[float, float]] = {}

    for city, (la, lo) in (sea_cities or []):
        sea_lookup[city] = (la, lo)
        tmax, tmin = fetch_tomorrow_temps(la, lo, tz=tz_name)
        if tmax is None:
            continue

        wcx = 0
        try:
            wmx = get_weather(la, lo) or {}
            didx = _daily_index_for_offset(wmx, tz_obj, 1)
            arr = (wmx.get("daily") or {}).get("weathercode") or []
            if isinstance(didx, int) and isinstance(arr, list) and didx < len(arr) and arr[didx] is not None:
                wcx = int(arr[didx])
        except Exception:
            wcx = 0

        sst_c = get_sst(la, lo)
        temps_sea[city] = (tmax, tmin or tmax, wcx, sst_c)

    if temps_sea:
        P.append(f"🌊 <b>{sea_label}</b>")
        medals = ["🥵", "😊", "🙄", "😮‍💨", "🥶"]

        for i, (city, (d, n, wcx, sst_c)) in enumerate(
            sorted(temps_sea.items(), key=lambda kv: kv[1][0], reverse=True)[:5]
        ):
            line = f"{medals[i]} {city}: {d:.0f}/{n:.0f}{NBSP}°C"
            descx = code_desc(wcx)
            if descx:
                line += f" • {descx}"
            if sst_c is not None:
                line += f" • 🌊 {sst_c:.0f}"

            try:
                la, lo = sea_lookup[city]
                wave_h, _wave_t = _fetch_wave_for_tomorrow(la, lo, tz_obj)
                if isinstance(wave_h, (int, float)):
                    line += f" • {wave_h:.1f} м"
            except Exception as e:
                if DEBUG_WATER:
                    logging.warning("Wave fetch failed for %s: %s", city, e)

            P.append(line)

            try:
                la, lo = sea_lookup[city]
                hl = _water_highlights(city, la, lo, tz_obj, sst_c)
                if hl:
                    P.append(f"   {hl}")
            except Exception as e:
                if DEBUG_WATER:
                    logging.exception("water_highlights failed for %s: %s", city, e)

        P.append("———")

    temps_oth: Dict[str, Tuple[float, float, int]] = {}
    for city, (la, lo) in (other_cities or []):
        tmax, tmin = fetch_tomorrow_temps(la, lo, tz=tz_name)
        if tmax is None:
            continue

        wcx = 0
        try:
            wmx = get_weather(la, lo) or {}
            didx = _daily_index_for_offset(wmx, tz_obj, 1)
            arr = (wmx.get("daily") or {}).get("weathercode") or []
            if isinstance(didx, int) and isinstance(arr, list) and didx < len(arr) and arr[didx] is not None:
                wcx = int(arr[didx])
        except Exception:
            wcx = 0

        temps_oth[city] = (tmax, tmin or tmax, wcx)

    if temps_oth:
        P.append("🔥 <b>Тёплые города, °C (топ-3)</b>")
        for city, (d, n, wcx) in sorted(temps_oth.items(), key=lambda kv: kv[1][0], reverse=True)[:3]:
            descx = code_desc(wcx)
            P.append(f"   • {city}: {d:.0f}/{n:.0f}{NBSP}°C" + (f" • {descx}" if descx else ""))

        P.append("❄️ <b>Холодные города, °C (топ-3)</b>")
        for city, (d, n, wcx) in sorted(temps_oth.items(), key=lambda kv: kv[1][0])[:3]:
            descx = code_desc(wcx)
            P.append(f"   • {city}: {d:.0f}/{n:.0f}{NBSP}°C" + (f" • {descx}" if descx else ""))

        P.append("———")

    # Закат и астрособытия завтрашнего дня
    try:
        _sunrise, sunset = get_sunrise_sunset(
            KLD_LAT,
            KLD_LON,
            tz_name,
            DAY_OFFSET,
        )
        if sunset:
            P.append(f"🌇 Закат завтра: {sunset}")
        else:
            logging.info("KLD evening: время заката недоступно")
    except Exception as e:
        logging.info("KLD evening: не удалось получить время заката: %s", e)
    date_for_astro = pendulum.today(tz_obj).add(days=ASTRO_OFFSET)
    P.append(build_astro_section(date_local=date_for_astro, tz_local=tz_name))
    P.append("———")

    # (2) Вечером блоки воздуха/космопогоды/Шумана скрыты — остаются только рекомендации.
    air = get_air(KLD_LAT, KLD_LON) or {}
    schu_state = {} if (DISABLE_SCHUMANN) else get_schumann_with_fallback()
    _visibility_context, visibility_line = _kld_visibility_for_post(
        wm_main,
        air,
        post_type="evening",
        target_date=date_weather.to_date_string(),
        tz_name=tz_name,
    )
    if visibility_line:
        P.append(visibility_line)
        P.append("———")
    quake_line = _kld_quake_line_24h()
    if quake_line:
        P.append(quake_line)
        P.append("———")

    P.append("✅ <b>Рекомендации</b>")

    air_bad, _air_label, _air_reason = _is_air_bad(air)
    storm_main = bool(storm.get("warning"))
    schu_main = (schu_state or {}).get("status_code") == "red"

    # (3) Вечером Kp не используется и не выводится.
    if storm_main:
        theme = "плохая погода"
    elif air_bad:
        theme = "плохой воздух"
    elif schu_main:
        theme = "волны Шумана"
    else:
        theme = "здоровый день"

    for tip in safe_tips(theme):
        P.append(tip)

    P.append("———")
    P.append("#Калининград #погода #здоровье #море")
    return "\n".join(P)

# ────────────────────────── Внешний интерфейс ──────────────────────────
def build_message(
    region_name: str,
    sea_label: str,
    sea_cities,
    other_label: str,
    other_cities,
    tz: Union[pendulum.Timezone, str],
    mode: Optional[str] = None,
) -> str:
    effective_mode = (mode or POST_MODE or "evening").strip().lower()

    global DAY_OFFSET, ASTRO_OFFSET
    if _int_env(_DAY_OFFSET_ENV) is None:
        DAY_OFFSET = _default_day_offset(effective_mode)
    if _int_env(_ASTRO_OFFSET_ENV) is None:
        ASTRO_OFFSET = int(DAY_OFFSET)

    if effective_mode == "morning":
        return build_message_morning_compact(
            region_name, sea_label, sea_cities, other_label, other_cities, tz
        )
    return build_message_legacy_evening(
        region_name, sea_label, sea_cities, other_label, other_cities, tz
    )

# ────────────────────────── Mood для KLD-картинки ──────────────────────────
def _pick_ref_coords(
    pairs: list[tuple[str, tuple[float, float]]],
    default: tuple[float, float],
) -> tuple[float, float]:
    pairs = list(pairs or [])
    if pairs:
        return pairs[0][1]
    return default

def _iter_city_pairs(cities: Any) -> list[tuple[str, tuple[float, float]]]:
    out: list[tuple[str, tuple[float, float]]] = []
    try:
        for item in cities or []:
            try:
                name, coords = item
                if not coords:
                    continue
                la, lo = coords
                out.append((str(name), (float(la), float(lo))))
            except Exception:
                continue
    except Exception:
        pass
    return out

def _build_kld_image_moods_for_evening(
    tz_obj: pendulum.Timezone,
    sea_pairs: list[tuple[str, tuple[float, float]]],
    other_pairs: list[tuple[str, tuple[float, float]]],
) -> tuple[str, str, str]:
    la_sea, lo_sea = _pick_ref_coords(sea_pairs, (KLD_LAT_DEFAULT, KLD_LON_DEFAULT))
    la_inland, lo_inland = _pick_ref_coords(other_pairs, (KLD_LAT_DEFAULT, KLD_LON_DEFAULT))

    marine_mood = "cool Baltic seaside evening with long sandy beaches and fresh wind from the sea"
    inland_mood = "quieter inland forests, lakes and the city of Kaliningrad with grounded, slower energy"

    try:
        wm_sea = get_weather(la_sea, lo_sea) or {}
    except Exception:
        wm_sea = {}

    try:
        storm_sea = storm_flags_for_tomorrow(wm_sea, tz_obj)
    except Exception:
        storm_sea = {"warning": False}

    if storm_sea.get("warning"):
        marine_variants = [
            "stormy Baltic evening with strong onshore wind, high waves and dramatic clouds over the sea",
            "very windy Baltic coastline, restless waves, blowing sand and low heavy clouds above the water",
            "rough Baltic sea with powerful gusts, whitecaps and wild sky — more for watching from shelter than walking on the pier",
        ]
    else:
        marine_variants = [
            "mild Baltic evening with noticeable but pleasant wind, fresh air and soft, steady waves along the long beaches",
            "cool-but-comfortable seaside evening, good for a long walk along the promenade with a hood or light jacket",
            "fresh, slightly harsh seaside evening — good for a short walk and hot tea afterwards",
        ]
    marine_mood = random.choice(marine_variants)

    inland_variants = [
        "typical mixed northern inland evening: cool but calmer than the sea, more about forests, courtyards and quiet streets",
        "balanced inland mood with fresher air than in summer, softer wind than at the coast and a slower, grounded rhythm",
    ]
    inland_mood = random.choice(inland_variants)

    astro_mood_en = (
        "calm, grounded northern sky energy supporting rest, reflection and simple practical planning for tomorrow"
        if not storm_sea.get("warning")
        else "more intense, restless sky mood that favours flexibility, backing up plans and gentle self-care after a long day"
    )

    return marine_mood, inland_mood, astro_mood_en

# ────────────────────────── Общий send + картинка ──────────────────────────
def _as_tz(tz: Union[pendulum.Timezone, str]) -> pendulum.Timezone:
    if isinstance(tz, pendulum.Timezone):
        return tz
    try:
        return pendulum.timezone(str(tz))
    except Exception:
        return pendulum.timezone("Europe/Kaliningrad")

async def send_common_post(
    bot: Bot,
    chat_id: int,
    region_name: str,
    sea_label: str,
    sea_cities,
    other_label: str,
    other_cities,
    tz,
    mode: Optional[str] = None,
) -> None:
    msg = build_message(
        region_name=region_name,
        sea_label=sea_label,
        sea_cities=sea_cities,
        other_label=other_label,
        other_cities=other_cities,
        tz=tz,
        mode=mode,
    )

    try:
        effective_mode = (mode or os.getenv("POST_MODE") or os.getenv("MODE") or "evening").lower()
    except Exception:
        effective_mode = "evening"

    kld_img_env = os.getenv("KLD_IMG_ENABLED", "1")
    enable_img = kld_img_env.strip().lower() not in ("0", "false", "no", "off")

    img_path: Optional[str] = None

    if (
        enable_img
        and effective_mode.startswith("evening")
        and generate_astro_image is not None
        and build_kld_evening_prompt is not None
    ):
        try:
            tz_obj = _as_tz(tz)
            sea_pairs = _iter_city_pairs(sea_cities)
            other_pairs = _iter_city_pairs(other_cities)
            marine_mood, inland_mood, astro_mood_en = _build_kld_image_moods_for_evening(
                tz_obj=tz_obj,
                sea_pairs=sea_pairs,
                other_pairs=other_pairs,
            )
            today = pendulum.today(tz_obj).add(days=DAY_OFFSET).date()
            prompt, style_name = build_kld_evening_prompt(
                date=today,
                marine_mood=marine_mood,
                inland_mood=inland_mood,
                astro_mood_en=astro_mood_en,
            )

            img_dir = Path("kld_images")
            img_dir.mkdir(parents=True, exist_ok=True)

            safe_style = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(style_name) if style_name else "default")
            img_file = img_dir / f"kld_evening_{today.isoformat()}_{safe_style}.jpg"

            img_path = generate_astro_image(prompt, str(img_file))  # type: ignore[call-arg]
        except Exception:
            img_path = None

    if img_path and Path(img_path).exists():
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=msg,
                parse_mode=constants.ParseMode.HTML,
                disable_web_page_preview=True,
            )
        except Exception:
            pass
        try:
            caption = (os.getenv("KLD_IMG_CAPTION") or "Визуальный вайб завтрашнего вечера над Балтикой🌊").strip()
            if len(caption) > 900:
                caption = caption[:900].rstrip() + "…"
            with open(img_path, "rb") as f:
                await bot.send_photo(
                    chat_id=chat_id,
                    photo=f,
                    caption=caption,
                    parse_mode=constants.ParseMode.HTML,
                )
            return
        except Exception:
            return

    await bot.send_message(
        chat_id=chat_id,
        text=msg,
        parse_mode=constants.ParseMode.HTML,
        disable_web_page_preview=True,
    )

async def main_common(
    bot: Bot,
    chat_id: int,
    region_name: str,
    sea_label: str,
    sea_cities,
    other_label: str,
    other_cities,
    tz,
    mode: Optional[str] = None,
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
        mode=mode,
    )

__all__ = [
    "build_message",
    "send_common_post",
    "main_common",
    "schumann_line",
    "get_schumann_with_fallback",
    "pick_header_metrics_for_offset",
    "pick_tomorrow_header_metrics",
    "radiation_line",
]
