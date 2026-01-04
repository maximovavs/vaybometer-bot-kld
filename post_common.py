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

Вечерний пост (legacy) + генерация картинки для KLD.

Ключевая логика дат:
  - утро: данные "на сегодня" (дата публикации поста)
  - вечер: данные "на завтра" (дата публикации поста + 1 день)

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

import pendulum
from telegram import Bot, constants

from utils   import compass, get_fact
from weather import get_weather
from air     import get_air, get_sst, get_kp, get_solar_wind
from pollen  import get_pollen
from radiation import get_radiation


# Задымление (опционально): модуль может отсутствовать.
try:
    from smoke import get_smoke  # type: ignore
except Exception:
    get_smoke = None  # type: ignore

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

POST_MODE    = (os.getenv("POST_MODE") or "evening").strip().lower()
DAY_OFFSET   = int(os.getenv("DAY_OFFSET", "0" if POST_MODE == "morning" else "1"))
ASTRO_OFFSET = int(os.getenv("ASTRO_OFFSET", str(DAY_OFFSET)))

SHOW_AIR      = _env_on("SHOW_AIR",      POST_MODE != "evening")
SHOW_SPACE    = _env_on("SHOW_SPACE",    POST_MODE != "evening")
SHOW_SCHUMANN = _env_on("SHOW_SCHUMANN", POST_MODE != "evening")

DEBUG_WATER = os.getenv("DEBUG_WATER", "").strip().lower() in ("1", "true", "yes", "on")
DISABLE_SCHUMANN = os.getenv("DISABLE_SCHUMANN", "").strip().lower() in ("1", "true", "yes", "on")

# LLM-параметры
USE_DAILY_LLM    = os.getenv("DISABLE_LLM_DAILY", "").strip().lower() not in ("1", "true", "yes", "on")
ASTRO_LLM_TEMP   = float(os.getenv("ASTRO_LLM_TEMP", "0.7"))

DEFAULT_TZ_NAME = (os.getenv("TZ") or "Europe/Kaliningrad").strip() or "Europe/Kaliningrad"

# ────────────────────────── базовые константы ──────────────────────────
NBSP = "\u00A0"
RUB  = "\u20BD"

KLD_LAT, KLD_LON = 54.710426, 20.452214
CACHE_DIR = Path(".cache")
CACHE_DIR.mkdir(exist_ok=True, parents=True)

# шторм-пороги (м/с, мм/ч, %)
STORM_GUST_MS        = float(os.getenv("STORM_GUST_MS", "15"))
ALERT_GUST_MS        = float(os.getenv("ALERT_GUST_MS", "20"))
ALERT_RAIN_MM_H      = float(os.getenv("ALERT_RAIN_MM_H", "10"))
ALERT_TSTORM_PROB_PC = float(os.getenv("ALERT_TSTORM_PROB_PC", "70"))

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

def _daily_value_for_date(
    wm: Dict[str, Any],
    field: str,
    date_obj: pendulum.Date,
) -> Optional[Any]:
    """Берёт daily[field] по индексу, соответствующему date_obj, если возможно."""
    daily = wm.get("daily") or {}
    dates = _daily_times(wm)
    if not dates:
        return None
    try:
        idx = dates.index(date_obj)
    except ValueError:
        return None
    arr = daily.get(field) or []
    if isinstance(arr, list) and idx < len(arr):
        return arr[idx]
    return None

def _daily_hhmm_for_date(
    wm: Dict[str, Any],
    field: str,
    date_obj: pendulum.Date,
    tz_obj: pendulum.Timezone,
) -> Optional[str]:
    val = _daily_value_for_date(wm, field, date_obj)
    if not val:
        return None
    try:
        return pendulum.parse(str(val)).in_tz(tz_obj).format("HH:mm")
    except Exception:
        try:
            return pendulum.parse(str(val)).format("HH:mm")
        except Exception:
            return None

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
    times = daily.get("time") or []
    out: List[pendulum.Date] = []
    for t in times:
        try:
            out.append(pendulum.parse(str(t)).date())
        except Exception:
            pass
    return out

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
    """Алиас для совместимости с продакшн-кодом (берёт +1 день)."""
    return pick_header_metrics_for_offset(wm, tz, 1)

# ────────────────────────── daily temps helpers ──────────────────────────
def _fetch_temps_for_offset(
    lat: float, lon: float, tz_name: str, offset_days: int
) -> Tuple[Optional[float], Optional[float], Optional[int]]:
    wm = get_weather(lat, lon) or {}
    daily = wm.get("daily") or {}
    times = _daily_times(wm)
    tz = pendulum.timezone(tz_name)
    target = pendulum.today(tz).add(days=offset_days).date()
    try:
        idx = times.index(target)
    except ValueError:
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
        wc = int((daily.get("weathercode") or [None])[idx])
    except Exception:
        wc = None
    return tmax, tmin, wc

def day_night_stats(lat: float, lon: float, tz: str = "UTC", offset_days: int = 1) -> Dict[str, Optional[float]]:
    """Возвращает статистику дня/ночи для offset_days (по умолчанию: завтра)."""
    wm = get_weather(lat, lon) or {}
    daily = wm.get("daily") or {}
    times = _daily_times(wm)
    tz_obj = pendulum.timezone(tz)
    target = pendulum.today(tz_obj).add(days=offset_days).date()

    try:
        idx = times.index(target)
    except ValueError:
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
    """Возвращает (tmax, tmin) для завтра."""
    tmax, tmin, _ = _fetch_temps_for_offset(lat, lon, tz, 1)
    return tmax, tmin

# === шторм-флаги ==================
def _hourly_indices_for_offset(wm: Dict[str, Any], tz: pendulum.Timezone, offset_days: int) -> List[int]:
    times = _hourly_times(wm)
    tgt = pendulum.now(tz).add(days=offset_days).date()
    idxs: List[int] = []
    for i, dt_ in enumerate(times):
        try:
            if dt_.in_tz(tz).date() == tgt:
                idxs.append(i)
        except Exception:
            pass
    return idxs

def storm_flags_for_tomorrow(wm: Dict[str, Any], tz: pendulum.Timezone, offset_days: int = 1) -> Dict[str, Any]:
    hourly = wm.get("hourly") or {}
    idxs = _hourly_indices_for_offset(wm, tz, offset_days)
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
        "warning_text": "⚠️ <b>Штормовое предупреждение</b>: " + ", ".join(reasons) if reasons else "",
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

def build_conclusion(
    kp: Any,
    kp_status: str,
    air: Dict[str, Any],
    storm: Dict[str, Any],
    schu: Dict[str, Any],
) -> List[str]:
    """Сводка «главное и забота о себе» — БЕЗ рекомендаций про магнитные бури."""
    lines: List[str] = []

    storm_main = bool(storm.get("warning"))
    air_bad, air_label, air_reason = _is_air_bad(air)
    schu_main = (schu or {}).get("status_code") == "red"

    gust = storm.get("max_gust_ms")

    storm_text = None
    if storm_main:
        parts = []
        if isinstance(gust, (int, float)):
            parts.append(f"порывы до {gust:.0f} м/с")
        if storm.get("heavy_rain"):
            parts.append("ливни")
        if storm.get("thunder"):
            parts.append("гроза")
        storm_text = "штормовая погода: " + (
            ", ".join(parts) if parts else "возможны неблагоприятные условия"
        )

    air_text = f"качество воздуха: {air_label} ({air_reason})" if air_bad else None
    kp_text = None  # kp вообще не используем
    schu_text = "сильные колебания Шумана (⚠️)" if schu_main else None

    # --- основной фактор (БЕЗ магнитных бурь) ---
    if storm_main:
        lines.append(
            f"Основной фактор — {storm_text}. Планируйте дела с учётом погоды."
        )
    elif air_bad:
        lines.append(
            f"Основной фактор — {air_text}. Сократите время на улице и проветривание по ситуации."
        )
    elif schu_main:
        lines.append(
            "Основной фактор — волны Шумана: отмечаются сильные отклонения. Берегите режим и нагрузку."
        )
    else:
        lines.append(
            "Серьёзных факторов риска не видно — ориентируйтесь на текущую погоду и личные планы."
        )

    # --- второстепенные факторы (тоже без kp) ---
    secondary: List[str] = []
    for tag, txt in (("storm", storm_text), ("air", air_text), ("schu", schu_text)):
        if not txt:
            continue
        if tag == "storm" and storm_main:
            continue
        if tag == "air" and air_bad:
            continue
        if tag == "schu" and schu_main:
            continue
        secondary.append(txt)

    if secondary:
        lines.append("Также обратите внимание: " + "; ".join(secondary[:2]) + ".")

    return lines


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
    parts: List[str] = []
    em, lbl = _pm_level(pm25, pm10)
    pm_parts = []
    if isinstance(pm25, (int, float)):
        pm_parts.append(f"PM₂.₅ {pm25:.0f}")
    if isinstance(pm10, (int, float)):
        pm_parts.append(f"PM₁₀ {pm10:.0f}")
    if pm_parts:
        parts.append(f"{em} {lbl} · " + " | ".join(pm_parts))
    if isinstance(usvh, (int, float)):
        r_em, r_lbl = _rad_risk(float(usvh))
        if isinstance(cpm, (int, float)):
            parts.append(f"{int(round(cpm))} CPM ≈ {float(usvh):.3f} μSv/h — {r_em} {r_lbl}")
        else:
            parts.append(f"≈ {float(usvh):.3f} μSv/h — {r_em} {r_lbl}")
    elif isinstance(cpm, (int, float)):
        parts.append(f"{int(round(cpm))} CPM")
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


def smoke_line(lat: float, lon: float) -> Optional[str]:
    '''
    Универсальная строка про задымление (если в проекте есть модуль smoke.py).
    Ожидаемые поля (любые из): level/risk/status/text/aerosol_index/pm25/pm10/source.
    '''
    if get_smoke is None:
        return None
    try:
        d = get_smoke(lat, lon)  # type: ignore[misc]
    except Exception:
        return None
    if not isinstance(d, dict) or not d:
        return None

    def _s(v):
        return str(v).strip() if v is not None else ""

    level = _s(d.get("level") or d.get("risk") or d.get("status"))
    text_ = _s(d.get("text") or d.get("message") or d.get("desc"))
    ai = d.get("aerosol_index") or d.get("ai") or d.get("uvi_like_ai")
    pm25 = d.get("pm25")
    pm10 = d.get("pm10")
    src = _s(d.get("source") or d.get("src"))

    parts: list[str] = []
    if level:
        parts.append(level)
    if text_ and (not level or text_.lower() not in level.lower()):
        parts.append(text_)
    if isinstance(ai, (int, float)):
        parts.append(f"AI {float(ai):.1f}")
    try:
        if isinstance(pm25, (int, float)):
            parts.append(f"PM₂.₅ {float(pm25):.0f}")
        if isinstance(pm10, (int, float)):
            parts.append(f"PM₁₀ {float(pm10):.0f}")
    except Exception:
        pass
    if src:
        parts.append(src)

    if not parts:
        return None

    return "🔥 Задымление: " + " • ".join(parts[:4])


# ────────────────────────── UVI ──────────────────────────
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

def uvi_for_offset(
    wm: Dict[str, Any], tz: pendulum.Timezone, offset_days: int
) -> Dict[str, Optional[float | str]]:
    daily = wm.get("daily") or {}
    hourly = wm.get("hourly") or {}
    date_obj = pendulum.today(tz).add(days=offset_days).date()
    times = hourly.get("time") or []
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
        return "гидрокостюм шорти 2 мм"
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
        return f"{name} {vs} {RUB} ({_fmt_delta(dlt)})"

    return "💱 Курсы (утро): " + " • ".join(
        [token("USD", "USD"), token("EUR", "EUR"), token("CNY", "CNY")]
    )

# ────────────────────────── «шторм/итого» ──────────────────────────
def _day_indices(wm: Dict[str, Any], tz: pendulum.Timezone, offset: int) -> List[int]:
    return _hourly_indices_for_offset(wm, tz, offset)

def _vals(arr, idxs):
    out = []
    for i in idxs:
        if i < len(arr) and arr[i] is not None:
            try:
                out.append(float(arr[i]))
            except Exception:
                pass
    return out

def storm_short_text(wm: dict, tz_obj, offset_days: int | None = None) -> str:
    """Return a compact storm description for the requested day.

    offset_days=None means: use global DAY_OFFSET (legacy behavior).
    """
    off = DAY_OFFSET if offset_days is None else int(offset_days)
    d0, d1, i0, i1 = _day_indices(wm, tz_obj, offset_days=off)
    if i0 is None:
        return ""

    gust = _max_slice(wm.get("hourly", {}).get("wind_gusts_10m", []), i0, i1)
    precip = _sum_slice(wm.get("hourly", {}).get("precipitation", []), i0, i1)
    th = _any_slice(wm.get("hourly", {}).get("thunderstorm", []), i0, i1)

    if gust >= 18:
        return "сильные порывы ветра"
    if th:
        return "риск грозы"
    if precip >= 20:
        return "обильные осадки"
    if gust >= 14:
        return "порывистый ветер"
    if precip >= 10:
        return "дождь/снег"
    return ""

def storm_alert_line(wm: dict, tz_obj, offset_days: int | None = None) -> str:
    """Build a warning line if severe weather is expected for the requested day.

    offset_days=None means: use global DAY_OFFSET (legacy behavior).
    """
    off = DAY_OFFSET if offset_days is None else int(offset_days)
    d0, d1, i0, i1 = _day_indices(wm, tz_obj, offset_days=off)
    if i0 is None:
        return ""

    gust = _max_slice(wm.get("hourly", {}).get("wind_gusts_10m", []), i0, i1)
    precip = _sum_slice(wm.get("hourly", {}).get("precipitation", []), i0, i1)
    th = _any_slice(wm.get("hourly", {}).get("thunderstorm", []), i0, i1)

    reasons: list[str] = []
    if gust >= 18:
        reasons.append(f"порывы до {int(round(gust))} м/с")
    if precip >= 20:
        reasons.append(f"осадки ~{int(round(precip))} мм")
    if th:
        reasons.append("гроза")

    if not reasons:
        return ""
    return "⚠️ Штормовое предупреждение: " + ", ".join(reasons)

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
    """
    Ищем лунный календарь:
      - по относительному пути (рабочая директория),
      - рядом со скриптом,
      - в подкаталоге data/ рядом со скриптом.
    Структура:
      { "days": { "YYYY-MM-DD": {...} } } или { "YYYY-MM-DD": {...} }.
    """
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

def _parse_voc_dt(s: str, tz: pendulum.Timezone):
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

def voc_interval_for_date(rec: dict, tz_local: str = DEFAULT_TZ_NAME):
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

def format_voc_for_post(start: pendulum.DateTime, end: pendulum.DateTime, label: str = "сегодня") -> str:
    if not start or not end:
        return ""
    return f"⚫️ VoC {label} {start.format('HH:mm')}–{end.format('HH:mm')}."

def lunar_advice_for_date(cal: dict, date_obj) -> List[str]:
    key = date_obj.to_date_string() if hasattr(date_obj, "to_date_string") else str(date_obj)
    rec = (cal or {}).get(key, {}) or {}
    adv = rec.get("advice")
    # advice может быть list[str] или строкой (или др. типом) — нормализуем к list[str]
    if isinstance(adv, str):
        items = [adv]
    elif isinstance(adv, list):
        items = adv
    elif adv is None:
        items = []
    else:
        items = [str(adv)]

    return [str(x).strip() for x in items if str(x).strip()][:3]

def _astro_markers_from_rec(rec: dict) -> list[str]:
    """Heuristically extract simple astro 'markers' from a calendar record.

    Different generators store these flags under different keys; sometimes they live only inside
    free-form text like 'advice'. We keep this intentionally permissive and non-breaking.
    """

    keys = [
        # explicit boolean-ish flags
        "good_day",
        "is_good",
        "favorable",
        "auspicious",
        "lucky",
        "unlucky",
        "bad_day",
        # domains
        "shopping",
        "shopping_day",
        "buy",
        "purchases",
        "travel",
        "travel_day",
        "trip",
        "journey",
        "beauty",
        "beauty_day",
        # free-form buckets
        "note",
        "notes",
        "comment",
        "comments",
        "markers",
        "tags",
        "summary",
        "recommendations",
        "advice",
        "advice_text",
    ]

    raw: list[str] = []
    for k in keys:
        v = rec.get(k)
        if not v:
            continue
        if isinstance(v, list):
            raw.extend([str(x) for x in v if x])
        else:
            raw.append(str(v))

    blob = " ".join(raw).lower()

    markers: list[str] = []

    # Favorable
    if any(w in blob for w in [
        "благоприят",
        "удачн",
        "хорош",
        "good",
        "favorable",
        "auspicious",
        "lucky",
    ]):
        markers.append("🟢 Благоприятный")

    # Shopping / purchases
    if any(w in blob for w in [
        "покуп",
        "шоп",
        "shopping",
        "buy",
        "purchase",
    ]):
        markers.append("🛍 Покупки")

    # Travel
    if any(w in blob for w in [
        "путеше",
        "поезд",
        "дорог",
        "travel",
        "trip",
        "journey",
    ]):
        markers.append("✈️ Путешествия")

    # De-dup while preserving order
    out: list[str] = []
    for m in markers:
        if m not in out:
            out.append(m)
    return out

def _astro_llm_bullets(date_str: str, sys_prompt: str, prompt: str) -> list[str]:
    """Ask the LLM for 2–3 short astro bullets and cache the result.

    If a cached file exists but contains fewer than 2 usable lines, we treat it as stale and
    regenerate. This prevents a "half-empty" astro block from sticking forever.
    """

    cache_path = Path(f".cache/astro_{date_str}.txt")

    def _parse(raw: str) -> list[str]:
        out: list[str] = []
        for x in (raw or "").splitlines():
            x = x.strip()
            if not x:
                continue
            # strip simple numbering / bullets
            x = re.sub(r"^[-•\*]+\s*", "", x)
            x = re.sub(r"^\d+[\).]\s*", "", x)
            x = _sanitize_line(x, max_len=140)
            if len(x) < 4:
                continue
            out.append(x)

        # keep unique, preserve order
        uniq: list[str] = []
        for x in out:
            if x not in uniq:
                uniq.append(x)
        return uniq[:3]

    if cache_path.exists():
        try:
            cached = _parse(cache_path.read_text("utf-8", errors="ignore"))
            if len(cached) >= 2:
                return cached
        except Exception:
            pass

    txt = ""
    try:
        txt = gpt_complete(prompt, system=sys_prompt, max_tokens=220)
    except Exception:
        txt = ""

    bullets = _parse(txt)

    # only cache if we got at least 2 useful lines
    if len(bullets) >= 2:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text("\n".join(bullets), "utf-8")

    return bullets

def build_astro_section(astro_date: pendulum.Date, tz_obj) -> str:
    """Astro section, based on lunar_calendar.json.

    Requirements:
      - Keep VoC line if present.
      - Preserve markers like: благоприятный день / покупки / путешествия.
      - Use LLM bullets when available, but always fall back to calendar data.
    """

    rec = find_day_record(load_calendar("lunar_calendar.json"), astro_date)
    if not rec:
        return ""

    phase_name = str(rec.get("phase") or "").strip()
    illum_pct = rec.get("illum_pct")
    sign = str(rec.get("sign") or "").strip()

    voc = rec.get("voc") or rec.get("void_of_course") or rec.get("voc_interval")
    voc_line = f"🕓 VoC: {voc}" if voc else ""

    markers = _astro_markers_from_rec(rec)
    marker_line = " • ".join(markers) if markers else ""

    # LLM bullets (optional)
    llm_bullets: list[str] = []
    if USE_DAILY_LLM:
        date_str = astro_date.to_date_string()
        sys_prompt = (
            "Ты — астрологический ассистент. Пиши кратко, без мистификаций, "
            "только практичные советы."
        )

        prompt = (
            f"Дата: {astro_date.format('DD.MM.YYYY')}\n"
            f"Фаза Луны: {phase_name or '—'}\n"
            f"Освещённость: {illum_pct if illum_pct is not None else '—'}%\n"
            f"Знак: {sign or '—'}\n"
            f"VoC: {voc or '—'}\n"
            f"Маркер(ы): {', '.join(markers) if markers else '—'}\n\n"
            "Сформируй ровно 2–3 строки (каждая — одно короткое предложение). "
            "Каждая строка начинается с подходящего эмодзи. "
            "Не используй слова 'возможно', 'может быть'. "
            "Не добавляй фактов, которых нет во входе."
        )

        llm_bullets = _astro_llm_bullets(date_str, sys_prompt, prompt)

    # Calendar advice (fallback and/or filler)
    advice_src = rec.get("advice") or rec.get("recommendations") or []
    advice_lines: list[str] = []
    if isinstance(advice_src, list):
        for a in advice_src:
            a = _sanitize_line(str(a), max_len=140)
            if a:
                advice_lines.append(a)
    elif isinstance(advice_src, str):
        a = _sanitize_line(advice_src, max_len=140)
        if a:
            advice_lines.append(a)

    # factual fallbacks if everything else is sparse
    factual_lines: list[str] = []
    if phase_name:
        if isinstance(illum_pct, (int, float)):
            factual_lines.append(f"🌙 {phase_name} ({int(round(float(illum_pct)))}%)")
        else:
            factual_lines.append(f"🌙 {phase_name}")
    if sign:
        factual_lines.append(f"Знак: {sign}")

    body: list[str] = []
    if marker_line:
        body.append(marker_line)

    # Prefer LLM bullets
    for x in llm_bullets:
        if x and x not in body:
            body.append(x)
        if len(body) >= 3:
            break

    # Fill with calendar advice
    if len(body) < 3:
        for x in advice_lines:
            if x and x not in body:
                body.append(x)
            if len(body) >= 3:
                break

    # Fill with factual lines (phase/sign)
    if len(body) < 3:
        for x in factual_lines:
            if x and x not in body:
                body.append(x)
            if len(body) >= 3:
                break

    lines: list[str] = ["📅 Астрособытия"]

    for l in body[:3]:
        l = (l or "").strip()
        if not l:
            continue
        # keep emoji-led lines as-is, but add a bullet for plain-text starts
        if not re.match(r"^\W", l):
            l = "• " + l
        lines.append(zsym(l))

    if voc_line:
        lines.append(voc_line)

    return "\n".join(lines)

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

    wm_klg = get_weather(KLD_LAT, KLD_LON) or {}
    t_day, t_night, wcode = _fetch_temps_for_offset(KLD_LAT, KLD_LON, tz_obj.name, 0)
    wind_ms, wind_dir_deg, press_val, press_trend = pick_header_metrics_for_offset(wm_klg, tz_obj, 0)

    gust = None
    try:
        times = _hourly_times(wm_klg)
        hourly = wm_klg.get("hourly") or {}
        idx_noon = _nearest_index_for_day(
            times,
            date_local.date(),
            12,
            tz_obj,
        )
        arr = hourly.get("wind_gusts_10m") or hourly.get("windgusts_10m") or []
        if idx_noon is not None and idx_noon < len(arr):
            gust = float(arr[idx_noon]) / 3.6
    except Exception:
        pass

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

    tz_name = tz_obj.name
    warm_city, warm_vals = None, None
    cold_city, cold_vals = None, None
    for city, (la, lo) in other_cities:
        tmax, tmin, _ = _fetch_temps_for_offset(la, lo, tz_name, 0)
        if tmax is None:
            continue
        if warm_vals is None or tmax > warm_vals[0]:
            warm_city, warm_vals = city, (tmax, tmin or tmax)
        if cold_vals is None or tmax < cold_vals[0]:
            cold_city, cold_vals = city, (tmax, tmin or tmax)
    warm_txt = f"{warm_city} {int(round(warm_vals[0]))}/{int(round(warm_vals[1]))}{NBSP}°C" if warm_city else "н/д"
    cold_txt = f"{cold_city} {int(round(cold_vals[0]))}/{int(round(cold_vals[1]))}{NBSP}°C" if cold_city else "н/д"
    sst_hint = None
    for _, (la, lo) in (sea_cities or []):
        try:
            s = get_sst(la, lo)
            if isinstance(s, (int, float)):
                sst_hint = s
                break
        except Exception:
            pass
    suit = wetsuit_hint_by_sst(sst_hint)
    sea_txt = f"Море: {suit}." if suit else "Море: н/д."

    sunset = _daily_hhmm_for_date(wm_klg, "sunset", date_local.date(), tz_obj)
    sunset_line = f"🌇 Закат сегодня: {sunset}" if sunset else "🌇 Закат: н/д"

    fx_line = fx_morning_line(pendulum.now(tz_obj), tz_obj)

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
        "🟠"
        if air_risk in ("высокий", "очень высокий")
        else ("🟡" if air_risk == "умеренный" else "🟢")
    )

    air_line = f"🏭 Воздух: {air_emoji_main} {air_risk} (AQI {aqi_i}) • PM₂.₅ {pm25_int} / PM₁₀ {pm10_int}"
    if pollen_risk:
        air_line += f" • 🌿 пыльца: {pollen_risk}"

    smoke = smoke_line(KLD_LAT, KLD_LON)

    uvi_info = uvi_for_offset(wm_klg, tz_obj, 0)
    uvi_line = None
    try:
        uvi_val = None
        if isinstance(uvi_info.get("uvi"), (int, float)):
            uvi_val = float(uvi_info["uvi"])
        elif isinstance(uvi_info.get("uvi_max"), (int, float)):
            uvi_val = float(uvi_info["uvi_max"])
        if isinstance(uvi_val, (int, float)) and uvi_val >= 3:
            uvi_line = f"☀️ УФ: {uvi_val:.0f} — {uvi_label(uvi_val)} • SPF 30+ и головной убор"
    except Exception:
        pass

    kp_val, kp_status, kp_age_min, kp_src = _kp_global_swpc()
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

    storm_line_alert = storm_alert_line(wm_klg, tz_obj, offset_days=0)

    sc_line = safecast_summary_line()
    official_rad = radiation_line(KLD_LAT, KLD_LON)

    schu_line = schumann_line(get_schumann_with_fallback()) if SHOW_SCHUMANN else None

    storm_short = storm_short_text(wm_klg, tz_obj, offset_days=0)
    kp_short = kp_status if isinstance(kp_val, (int, float)) else "н/д"
    air_emoji = air_emoji_main
    itogo = f"🔎 Итого: воздух {air_emoji} • {storm_short} • Кр {kp_short}"

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
        f"Погреться: {warm_txt}; остыть: {cold_txt}. {sea_txt}",
        "",
        sunset_line,
        "———",
    ]
    if fx_line:
        P.append(fx_line)
    P.append("———")
    if SHOW_AIR:
        P.append(air_line)

        if smoke:
            P.append(smoke)
    if uvi_line:
        P.append(uvi_line)
    if SHOW_SPACE:
        P.append(space_line)
    if storm_line_alert:
        P.append(storm_line_alert)
    sc_block_parts = [x for x in (sc_line, official_rad) if x]
    if sc_block_parts:
        P.append(" • ".join(sc_block_parts))
    if schu_line:
        P.append(schu_line)
    P.append("")
    P.append(itogo)
    P.append(today_line)
    P.append("")
    P.append("#Калининград #погода #здоровье #сегодня #море")
    return "\n".join(P)

# ────────────────────────── Evening (подробный) ──────────────────────────
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

    date_weather = pendulum.today(tz_obj).add(days=1)  # вечер: всегда завтра
    header = f"<b>🌅 {region_name}: погода на завтра ({date_weather.format('DD.MM.YYYY')})</b>"

    P: List[str] = [header]

    wm_main = get_weather(KLD_LAT, KLD_LON) or {}

    sunrise = _daily_hhmm_for_date(wm_main, "sunrise", date_weather.date(), tz_obj)
    if sunrise:
        P.append(f"🌄 Рассвет завтра: {sunrise}")

    stats = day_night_stats(KLD_LAT, KLD_LON, tz=tz_name, offset_days=1)
    t_day_max = stats.get("t_day_max")
    t_night_min = stats.get("t_night_min")
    rh_min = stats.get("rh_min")
    rh_max = stats.get("rh_max")

    wcarr = (wm_main.get("daily", {}) or {}).get("weathercode", [])
    wcode = wcarr[1] if isinstance(wcarr, list) and len(wcarr) > 1 else None

    wind_ms, wind_dir_deg, press_val, press_trend = pick_tomorrow_header_metrics(wm_main, tz_obj)

    storm = storm_flags_for_tomorrow(wm_main, tz_obj, offset_days=1)
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

    kal_line = (
        f"🏙️ Калининград: дн/ночь {temp_txt} • {desc} • {wind_txt}{rh_txt}{press_txt}"
    )

    P.append(kal_line)
    P.append("———")

    if storm.get("warning"):
        P.append(storm["warning_text"])
        P.append("———")

    temps_sea: Dict[str, Tuple[float, float, int, float | None]] = {}
    sea_lookup: Dict[str, Tuple[float, float]] = {}

    for city, (la, lo) in (sea_cities or []):
        sea_lookup[city] = (la, lo)
        tmax, tmin = fetch_tomorrow_temps(la, lo, tz=tz_name)
        if tmax is None:
            continue
        wcx = (get_weather(la, lo) or {}).get("daily", {}).get("weathercode", [])
        wcx = wcx[1] if isinstance(wcx, list) and len(wcx) > 1 else 0
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
                wave_h, wave_t = _fetch_wave_for_tomorrow(la, lo, tz_obj)
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
        wcx = (get_weather(la, lo) or {}).get("daily", {}).get("weathercode", [])
        wcx = wcx[1] if isinstance(wcx, list) and len(wcx) > 1 else 0
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

    date_for_astro = pendulum.today(tz_obj).add(days=ASTRO_OFFSET)
    P.append(build_astro_section(date_local=date_for_astro, tz_local=tz_obj.name))
    P.append("———")

    kp_tuple = get_kp() or (None, "н/д", None, "n/d")
    try:
        kp, ks, kp_ts, kp_src = kp_tuple
    except Exception:
        kp = kp_tuple[0] if isinstance(kp_tuple, (list, tuple)) and len(kp_tuple) > 0 else None
        ks = kp_tuple[1] if isinstance(kp_tuple, (list, tuple)) and len(kp_tuple) > 1 else "н/д"
        kp_ts, kp_src = None, "n/d"

    air = get_air(KLD_LAT, KLD_LON) or {}
    schu_state = {} if DISABLE_SCHUMANN else get_schumann_with_fallback()

    sc_line = safecast_summary_line()
    official_rad = radiation_line(KLD_LAT, KLD_LON)
    smoke = smoke_line(KLD_LAT, KLD_LON)
    extra_parts = [x for x in (sc_line, official_rad, smoke) if x]
    if extra_parts:
        P.append(" • ".join(extra_parts))
        P.append("———")

    P.append("✅ <b>Рекомендации</b>")

    air_bad, air_label, air_reason = _is_air_bad(air)
    kp_val = float(kp) if isinstance(kp, (int, float)) else None
    kp_main = bool(kp_val is not None and kp_val >= 5)
    storm_main = bool(storm.get("warning"))
    schu_main = (schu_state or {}).get("status_code") == "red"

    if storm_main:
        theme = "плохая погода"
    elif kp_main:
        theme = "магнитные бури"
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
    if effective_mode == "morning":
        return build_message_morning_compact(
            region_name,
            sea_label,
            sea_cities,
            other_label,
            other_cities,
            tz,
        )
    return build_message_legacy_evening(
        region_name,
        sea_label,
        sea_cities,
        other_label,
        other_cities,
        tz,
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
        stats_sea = day_night_stats(la_sea, lo_sea, tz=tz_obj.name, offset_days=1) or {}
    except Exception:
        stats_sea = {}

    try:
        stats_inland = day_night_stats(la_inland, lo_inland, tz=tz_obj.name, offset_days=1) or {}
    except Exception:
        stats_inland = {}

    tmax_sea = stats_sea.get("t_day_max")
    tmin_sea = stats_sea.get("t_night_min")
    tmax_inland = stats_inland.get("t_day_max")
    tmin_inland = stats_inland.get("t_night_min")

    try:
        wm_sea = get_weather(la_sea, lo_sea) or {}
    except Exception:
        wm_sea = {}

    try:
        storm_sea = storm_flags_for_tomorrow(wm_sea, tz_obj, offset_days=1)
    except Exception:
        storm_sea = {"warning": False}

    if storm_sea.get("warning"):
        marine_variants = [
            "stormy Baltic evening with strong onshore wind, high waves and dramatic clouds over the sea",
            "very windy Baltic coastline, restless waves, blowing sand and low heavy clouds above the water",
            "rough Baltic sea with powerful gusts, whitecaps and wild sky — more for watching from shelter than walking on the pier",
        ]
    else:
        if isinstance(tmax_sea, (int, float)) and tmax_sea >= 22:
            marine_variants = [
                "rarely warm Baltic seaside evening with almost summer air, gentle waves and long golden light over the horizon",
                "unusually warm Baltic evening, people stay outside longer, the sea looks softer and friendlier than usual",
            ]
        elif isinstance(tmax_sea, (int, float)) and tmax_sea >= 17:
            marine_variants = [
                "mild Baltic evening with noticeable but pleasant wind, fresh air and soft, steady waves along the long beaches",
                "cool-but-comfortable seaside evening, good for a long walk along the promenade with a hood or light jacket",
            ]
        elif isinstance(tmax_sea, (int, float)) and tmax_sea >= 10:
            marine_variants = [
                "cool Baltic shoreline with brisk wind, choppy waves and a feeling of early autumn even if the calendar says otherwise",
                "fresh, slightly harsh seaside evening — good for a short walk and hot tea afterwards",
            ]
        else:
            marine_variants = [
                "cold Baltic evening with dark restless water, strong wind and air that bites your cheeks — better with a scarf and hood",
                "very chilly Baltic coastline, almost winter-like mood: rough sea, cold wind and a desire to warm hands on a mug of tea indoors",
            ]

    marine_mood = random.choice(marine_variants)

    if isinstance(tmin_inland, (int, float)) and tmin_inland <= -5:
        inland_variants = [
            "frosty inland night with crunchy snow, very clear air and glowing windows in quiet streets of Kaliningrad and small towns",
            "freezing cold evening inland, still air, frost on branches and bright moonlight over hidden lakes and forests",
        ]
    elif isinstance(tmin_inland, (int, float)) and tmin_inland <= 0:
        inland_variants = [
            "cold inland evening around zero with damp air, bare branches and glistening roads, the city lights reflecting in wet asphalt",
            "chilly, slightly wet inland mood, more about quick walks and then hot tea at home",
        ]
    elif isinstance(tmax_inland, (int, float)) and tmax_inland >= 20:
        inland_variants = [
            "warm inland evening with soft air, slow walks along rivers and lakes and a relaxed city rhythm",
            "rare warm night in Kaliningrad: open windows, slow conversations and air that still keeps some heat from the day",
        ]
    else:
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
        return pendulum.timezone(DEFAULT_TZ_NAME)

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

    logging.info(
        "KLD_IMG: send_common_post called, mode=%s, tz=%s, KLD_IMG_ENABLED=%s -> enable_img=%s",
        effective_mode,
        tz if isinstance(tz, str) else getattr(tz, "name", "obj"),
        kld_img_env,
        enable_img,
    )

    if len(msg) > 4090:
        logging.warning("Telegram message too long (%d), trimming to 4090 chars", len(msg))
        msg_to_send = msg[:4090] + "…"
    else:
        msg_to_send = msg

    await bot.send_message(
        chat_id=chat_id,
        text=msg_to_send,
        parse_mode=constants.ParseMode.HTML,
        disable_web_page_preview=True,
    )

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

            logging.info(
                "KLD_IMG: evening image, sea_pairs=%d, other_pairs=%d",
                len(sea_pairs),
                len(other_pairs),
            )

            marine_mood, inland_mood, astro_mood_en = _build_kld_image_moods_for_evening(
                tz_obj=tz_obj,
                sea_pairs=sea_pairs,
                other_pairs=other_pairs,
            )

            post_date = pendulum.today(tz_obj).add(days=1).date()
            post_date_d = dt.date(post_date.year, post_date.month, post_date.day)

            prompt, style_name = build_kld_evening_prompt(
                date=post_date_d,
                marine_mood=marine_mood,
                inland_mood=inland_mood,
                astro_mood_en=astro_mood_en,
            )

            logging.info(
                "KLD_IMG: built prompt, style=%s, date=%s, prompt_len=%d",
                style_name,
                post_date_d.isoformat(),
                len(prompt),
            )

            img_dir = Path("kld_images")
            img_dir.mkdir(parents=True, exist_ok=True)

            safe_style = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(style_name) if style_name else "default")
            img_file = img_dir / f"kld_evening_{post_date_d.isoformat()}_{safe_style}.jpg"

            logging.info("KLD_IMG: calling generate_astro_image -> %s", img_file)
            img_path = generate_astro_image(prompt, str(img_file))  # type: ignore[call-arg]
            logging.info(
                "KLD_IMG: generate_astro_image returned %r, exists=%s",
                img_path,
                bool(img_path and Path(img_path).exists()),
            )
        except Exception as exc:
            logging.exception("KLD_IMG: image generation failed: %s", exc)
            img_path = None
    else:
        logging.info(
            "KLD_IMG: skip image (enable_img=%s, effective_mode=%s, gen=%s, prompt_fn=%s)",
            enable_img,
            effective_mode,
            bool(generate_astro_image),
            bool(build_kld_evening_prompt),
        )

    if img_path and Path(img_path).exists():
        try:
            logging.info("KLD_IMG: sending photo %s", img_path)
            with open(img_path, "rb") as f:
                await bot.send_photo(
                    chat_id=chat_id,
                    photo=f,
                )
        except Exception as exc:
            logging.exception("KLD_IMG: sending photo failed: %s", exc)

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
