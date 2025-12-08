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

Вечерний пост (legacy) сохранён.

ENV:
  POST_MODE (morning/evening), DAY_OFFSET, ASTRO_OFFSET,
  SHOW_AIR, SHOW_SPACE, SHOW_SCHUMANN.
"""

from __future__ import annotations

import os
import re
import json
import html
import logging
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional, Union
import urllib.request
import urllib.error

import pendulum
from telegram import Bot, constants

from utils   import compass, get_fact
from weather import get_weather
from air     import get_air, get_sst, get_kp, get_solar_wind
from pollen  import get_pollen
from radiation import get_radiation

try:
    from gpt import gpt_blurb  # type: ignore
except Exception:
    gpt_blurb = None  # type: ignore

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ────────────────────────── ENV flags ──────────────────────────
def _env_on(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None: return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")

POST_MODE    = (os.getenv("POST_MODE") or "evening").strip().lower()
DAY_OFFSET   = int(os.getenv("DAY_OFFSET", "0" if POST_MODE == "morning" else "1"))
ASTRO_OFFSET = int(os.getenv("ASTRO_OFFSET", str(DAY_OFFSET)))

SHOW_AIR      = _env_on("SHOW_AIR",      POST_MODE != "evening")
SHOW_SPACE    = _env_on("SHOW_SPACE",    POST_MODE != "evening")
SHOW_SCHUMANN = _env_on("SHOW_SCHUMANN", POST_MODE != "evening")

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


# ────────────────────────── базовые константы ──────────────────────────
NBSP = "\u00A0"
RUB  = "\u20BD"

KLD_LAT, KLD_LON = 54.710426, 20.452214
CACHE_DIR = Path(".cache"); CACHE_DIR.mkdir(exist_ok=True, parents=True)

# шторм-пороги (м/с, мм/ч, %)
STORM_GUST_MS        = float(os.getenv("STORM_GUST_MS", "15"))  # для «итого: шторм»
ALERT_GUST_MS        = float(os.getenv("ALERT_GUST_MS", "20"))  # для «⚠️ предупреждение»
ALERT_RAIN_MM_H      = float(os.getenv("ALERT_RAIN_MM_H", "10"))
ALERT_TSTORM_PROB_PC = float(os.getenv("ALERT_TSTORM_PROB_PC", "70"))

# ────────────────────────── WMO → эмодзи/текст ──────────────────────────
WMO_DESC = {0:"☀️ ясно",1:"⛅ ч.обл",2:"☁️ обл",3:"🌥 пасм",45:"🌫 туман",48:"🌫 изморозь",
            51:"🌦 морось",61:"🌧 дождь",71:"❄️ снег",95:"⛈ гроза"}
def code_desc(c: Any) -> Optional[str]:
    try: return WMO_DESC.get(int(c))
    except Exception: return None

# ────────────────────────── утилиты ──────────────────────────
def _fmt_delta(x: Any) -> str:
    try:
        v = float(x)
    except Exception:
        return "0.00"
    sign = "−" if v < 0 else ""
    return f"{sign}{abs(v):.2f}"

def aqi_risk_ru(aqi: Any) -> str:
    try: v = float(aqi)
    except Exception: return "н/д"
    if v <= 50:  return "низкий"
    if v <= 100: return "умеренный"
    if v <= 150: return "высокий"
    return "очень высокий"

# ────────────── ЕДИНЫЙ ИСТОЧНИК Kp: SWPC closed 3-hour bar ──────────────
def _kp_status_by_value(kp: Optional[float]) -> str:
    if not isinstance(kp, (int, float)): return "н/д"
    k = float(kp)
    if k >= 6.0: return "буря"
    if k >= 5.0: return "повышенная"
    return "умеренно"

def _kp_from_swpc_http() -> Tuple[Optional[float], Optional[int], str]:
    """
    Прямая загрузка закрытых 3-часовых баров с SWPC.
    Возвращает (kp, age_min, src).
    """
    url = "https://services.swpc.noaa.gov/json/planetary_k_index.json"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if not data: return None, None, "swpc/http-empty"
        last = data[-1]
        # поля: 'kp_index' (float), 'time_tag' (ISO)
        kp = float(last.get("kp_index"))
        t  = str(last.get("time_tag"))
        dt = pendulum.parse(t, tz="UTC")
        age_min = int((pendulum.now("UTC") - dt).in_minutes())
        return kp, age_min, "swpc/http"
    except Exception as e:
        logging.warning("SWPC HTTP Kp failed: %s", e)
        return None, None, "swpc/http-fail"

def _kp_global_swpc() -> Tuple[Optional[float], str, Optional[int], str]:
    """
    Возвращает (kp, status, age_minutes, src).

    Приоритет:
      1) прямой HTTP на SWPC (planetary_k_index.json),
      2) fallback через air.get_kp(...),
      3) если вообще ничего — (None, "н/д", None, "kp:nodata").

    Для SWPC считаем «старым» бар > 6 часов.
    Для fallback через air.get_kp допускаем возраст до 24 часов,
    чтобы не было вечного «Кр н/д», но при этом отсекать совсем древние значения.
    """
    # ── 1. Прямой HTTP к SWPC ─────────────────────────────────────────────
    kp, age, src = _kp_from_swpc_http()
    if isinstance(kp, (int, float)):
        if isinstance(age, int) and age > 6 * 60:
            logging.warning("Kp SWPC stale (%s min, src=%s)", age, src)
        else:
            k = max(0.0, min(9.0, float(kp)))
            status = _kp_status_by_value(k)
            logging.info("Kp SWPC used: %.1f, age=%s min, src=%s", k, age, src)
            return k, status, age, src or "swpc/http"

    # ── 2. Fallback через air.get_kp(...) ─────────────────────────────────
    tup = None
    src2 = "kp:nodata"

    # сначала пробуем явно глобальные источники
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

    # если не получилось — совсем дефолтный get_kp()
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

    # для fallback считаем приемлемым до 24 часов
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
        try: out.append(pendulum.parse(str(t)))
        except Exception: pass
    return out

def _daily_times(wm: Dict[str, Any]) -> List[pendulum.Date]:
    daily = wm.get("daily") or {}
    times = daily.get("time") or []
    out: List[pendulum.Date] = []
    for t in times:
        try: out.append(pendulum.parse(str(t)).date())
        except Exception: pass
    return out

def _nearest_index_for_day(times: List[pendulum.DateTime],
                           date_obj: pendulum.Date,
                           prefer_hour: int,
                           tz: pendulum.Timezone) -> Optional[int]:
    if not times: return None
    target = pendulum.datetime(date_obj.year, date_obj.month, date_obj.day, prefer_hour, 0, tz=tz)
    best_i, best_diff = None, None
    for i, dt in enumerate(times):
        try: dl = dt.in_tz(tz)
        except Exception: dl = dt
        if dl.date() != date_obj: continue
        diff = abs((dl - target).total_seconds())
        if best_diff is None or diff < best_diff:
            best_i, best_diff = i, diff
    return best_i

def pick_header_metrics_for_offset(wm: Dict[str, Any], tz: pendulum.Timezone, offset_days: int
) -> Tuple[Optional[float], Optional[int], Optional[int], str]:
    hourly = wm.get("hourly") or {}
    times  = _hourly_times(wm)
    tgt    = pendulum.now(tz).add(days=offset_days).date()
    idx_noon = _nearest_index_for_day(times, tgt, 12, tz)
    idx_morn = _nearest_index_for_day(times, tgt, 6, tz)

    spd_kmh = hourly.get("wind_speed_10m") or hourly.get("windspeed_10m") or []
    dir_deg = hourly.get("wind_direction_10m") or hourly.get("winddirection_10m") or []
    prs     = hourly.get("surface_pressure") or []

    wind_ms = None; wind_dir = None; press_val = None; trend = "→"
    try:
        if idx_noon is not None:
            if idx_noon < len(spd_kmh): wind_ms = float(spd_kmh[idx_noon]) / 3.6
            if idx_noon < len(dir_deg): wind_dir = int(round(float(dir_deg[idx_noon])))
            if idx_noon < len(prs):     press_val = int(round(float(prs[idx_noon])))
            if idx_morn is not None and idx_morn < len(prs) and idx_noon < len(prs):
                diff = float(prs[idx_noon]) - float(prs[idx_morn])
                trend = "↑" if diff >= 0.3 else "↓" if diff <= -0.3 else "→"
    except Exception:
        pass
    return wind_ms, wind_dir, press_val, trend

def _fetch_temps_for_offset(lat: float, lon: float, tz_name: str, offset_days: int
) -> Tuple[Optional[float], Optional[float], Optional[int]]:
    wm = get_weather(lat, lon) or {}
    daily = wm.get("daily") or {}
    times = _daily_times(wm)
    tz = pendulum.timezone(tz_name)
    target = pendulum.today(tz).add(days=offset_days).date()
    try: idx = times.index(target)
    except ValueError: return None, None, None

    def _num(arr, i):
        try:
            v = arr[i]
            return float(v) if v is not None else None
        except Exception:
            return None
    tmax = _num(daily.get("temperature_2m_max", []), idx)
    tmin = _num(daily.get("temperature_2m_min", []), idx)
    wc   = None
    try: wc = int((daily.get("weathercode") or [None])[idx])
    except Exception: wc = None
    return tmax, tmin, wc

# ────────────────────────── Шуман ──────────────────────────
def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        if not path.exists(): return None
        return json.loads(path.read_text("utf-8"))
    except Exception:
        return None

def _schu_freq_status(freq: Optional[float]) -> tuple[str, str]:
    if not isinstance(freq, (int, float)): return "🟡 колебания", "yellow"
    f = float(freq)
    if 7.4 <= f <= 8.4:
        return ("🟢 в норме", "green") if (7.7 <= f <= 8.1) else ("🟡 колебания", "yellow")
    return "🔴 сильное отклонение", "red"

def get_schumann_with_fallback() -> Dict[str, Any]:
    try:
        import schumann  # type: ignore
        if hasattr(schumann, "get_schumann"):
            payload = schumann.get_schumann() or {}
            return {"freq": payload.get("freq"),
                    "status": payload.get("status") or _schu_freq_status(payload.get("freq"))[0],
                    "status_code": payload.get("status_code") or _schu_freq_status(payload.get("freq"))[1]}
    except Exception:
        pass
    here = Path(__file__).parent
    js = _read_json(here / "data" / "schumann_hourly.json") or {}
    st, code = _schu_freq_status(js.get("freq"))
    return {"freq": js.get("freq"), "status": st, "status_code": code}

def schumann_line(s: Dict[str, Any]) -> Optional[str]:
    if (s or {}).get("status_code") == "green": return None
    f = s.get("freq")
    fstr = f"{f:.2f} Гц" if isinstance(f,(int,float)) else "н/д"
    return f"{s.get('status','н/д')} • Шуман: {fstr}"

# ────────────────────────── Safecast/радиация ──────────────────────────
CPM_TO_USVH = float(os.getenv("CPM_TO_USVH", "0.000571"))

def load_safecast() -> Optional[Dict[str, Any]]:
    paths: List[Path] = []
    if os.getenv("SAFECAST_FILE"): paths.append(Path(os.getenv("SAFECAST_FILE")))
    here = Path(__file__).parent
    paths.append(here / "data" / "safecast_kaliningrad.json")
    for p in paths:
        sc = _read_json(p)
        if not sc: continue
        ts = sc.get("ts")
        if not isinstance(ts,(int,float)): continue
        now_ts = pendulum.now("UTC").int_timestamp
        if now_ts - int(ts) <= 24*3600: return sc
    return None

def _pm_level(pm25: Optional[float], pm10: Optional[float]) -> Tuple[str, str]:
    def l25(x: float) -> int: return 0 if x<=15 else 1 if x<=35 else 2 if x<=55 else 3
    def l10(x: float) -> int: return 0 if x<=30 else 1 if x<=50 else 2 if x<=100 else 3
    worst = -1
    if isinstance(pm25,(int,float)): worst = max(worst, l25(float(pm25)))
    if isinstance(pm10,(int,float)): worst = max(worst, l10(float(pm10)))
    if worst < 0: return "⚪","н/д"
    return (["🟢","🟡","🟠","🔴"][worst],
            ["низкий","умеренный","высокий","очень высокий"][worst])

def _rad_risk(usvh: float) -> Tuple[str, str]:
    if usvh <= 0.15: return "🟢", "низкий"
    if usvh <= 0.30: return "🟡", "повышенный"
    return "🔴", "высокий"

def safecast_summary_line() -> Optional[str]:
    sc = load_safecast()
    if not sc: return None
    pm25, pm10 = sc.get("pm25"), sc.get("pm10")
    cpm, usvh  = sc.get("cpm"), sc.get("radiation_usvh")
    if not isinstance(usvh,(int,float)) and isinstance(cpm,(int,float)):
        usvh = float(cpm) * CPM_TO_USVH
    parts: List[str] = []
    em,lbl = _pm_level(pm25, pm10)
    pm_parts=[]
    if isinstance(pm25,(int,float)): pm_parts.append(f"PM₂.₅ {pm25:.0f}")
    if isinstance(pm10,(int,float)): pm_parts.append(f"PM₁₀ {pm10:.0f}")
    if pm_parts: parts.append(f"{em} {lbl} · " + " | ".join(pm_parts))
    if isinstance(usvh,(int,float)):
        r_em,r_lbl = _rad_risk(float(usvh))
        if isinstance(cpm,(int,float)):
            parts.append(f"{int(round(cpm))} CPM ≈ {float(usvh):.3f} μSv/h — {r_em} {r_lbl}")
        else:
            parts.append(f"≈ {float(usvh):.3f} μSv/h — {r_em} {r_lbl}")
    elif isinstance(cpm,(int,float)):
        parts.append(f"{int(round(cpm))} CPM")
    if not parts: return None
    return "🧪 Safecast: " + " · ".join(parts)

def radiation_line(lat: float, lon: float) -> Optional[str]:
    data = get_radiation(lat, lon) or {}
    dose = data.get("dose")
    if isinstance(dose,(int,float)):
        em,lbl = _rad_risk(float(dose))
        return f"{em} Радиация: {float(dose):.3f} μSv/h — {lbl}"
    return None

# ────────────────────────── UVI ──────────────────────────
def uvi_label(x: float) -> str:
    if x < 3:  return "низкий"
    if x < 6:  return "умеренный"
    if x < 8:  return "высокий"
    if x < 11: return "очень высокий"
    return "экстремальный"

def uvi_for_offset(wm: Dict[str, Any], tz: pendulum.Timezone, offset_days: int) -> Dict[str, Optional[float | str]]:
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
        vals=[]
        for t, v in zip(times, uvi_arr):
            if t and str(t).startswith(date_obj.to_date_string()) and isinstance(v,(int,float)):
                vals.append(float(v))
        if vals: uvi_max = max(vals)
    return {"uvi": uvi_now, "uvi_max": uvi_max}

# ────────────────────────── гидрик по SST ──────────────────────────
WSUIT_NONE   = float(os.getenv("WSUIT_NONE",   "22"))
WSUIT_SHORTY = float(os.getenv("WSUIT_SHORTY", "20"))
WSUIT_32     = float(os.getenv("WSUIT_32",     "17"))
WSUIT_43     = float(os.getenv("WSUIT_43",     "14"))
WSUIT_54     = float(os.getenv("WSUIT_54",     "12"))
WSUIT_65     = float(os.getenv("WSUIT_65",     "10"))

def wetsuit_hint_by_sst(sst: Optional[float]) -> Optional[str]:
    if not isinstance(sst,(int,float)): return None
    t=float(sst)
    if t >= WSUIT_NONE:   return None
    if t >= WSUIT_SHORTY: return "гидрокостюм шорти 2 мм"
    if t >= WSUIT_32:     return "гидрокостюм 3/2 мм"
    if t >= WSUIT_43:     return "гидрокостюм 4/3 мм (боты)"
    if t >= WSUIT_54:     return "гидрокостюм 5/4 мм (боты, перчатки)"
    if t >= WSUIT_65:     return "гидрокостюм 5/4 мм + капюшон (боты, перчатки)"
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
        val = r.get("value"); dlt = r.get("delta")
        try: vs = f"{float(val):.2f}"
        except Exception: vs = "н/д"
        return f"{name} {vs} {RUB} ({_fmt_delta(dlt)})"

    return "💱 Курсы (утро): " + " • ".join([token("USD", "USD"), token("EUR", "EUR"), token("CNY", "CNY")])

# ────────────────────────── «шторм/итого» ──────────────────────────
def _day_indices(wm: Dict[str, Any], tz: pendulum.Timezone, offset: int) -> List[int]:
    times = _hourly_times(wm)
    date_obj = pendulum.today(tz).add(days=offset).date()
    idxs=[]
    for i, dt in enumerate(times):
        try:
            if dt.in_tz(tz).date() == date_obj: idxs.append(i)
        except Exception: pass
    return idxs

def _vals(arr, idxs):
    out=[]
    for i in idxs:
        if i < len(arr) and arr[i] is not None:
            try: out.append(float(arr[i]))
            except Exception: pass
    return out

def storm_short_text(wm: Dict[str, Any], tz: pendulum.Timezone) -> str:
    hourly = wm.get("hourly") or {}
    idxs = _day_indices(wm, tz, DAY_OFFSET)
    if not idxs: return "без шторма"
    gusts = _vals(hourly.get("wind_gusts_10m") or hourly.get("windgusts_10m") or [], idxs)
    rain  = _vals(hourly.get("rain") or [], idxs)
    thp   = _vals(hourly.get("thunderstorm_probability") or [], idxs)
    if (max(gusts, default=0)/3.6 >= STORM_GUST_MS) or (max(rain, default=0) >= ALERT_RAIN_MM_H) or (max(thp, default=0) >= ALERT_TSTORM_PROB_PC):
        return "шторм"
    return "без шторма"

def storm_alert_line(wm: Dict[str, Any], tz: pendulum.Timezone) -> Optional[str]:
    hourly = wm.get("hourly") or {}
    idxs = _day_indices(wm, tz, DAY_OFFSET)
    if not idxs: return None
    gust_kmh = _vals(hourly.get("wind_gusts_10m") or hourly.get("windgusts_10m") or [], idxs)
    rain     = _vals(hourly.get("rain") or [], idxs)
    thp      = _vals(hourly.get("thunderstorm_probability") or [], idxs)
    g_max = max(gust_kmh, default=0)/3.6
    r_max = max(rain, default=0)
    t_max = max(thp, default=0)
    parts=[]
    if g_max >= ALERT_GUST_MS: parts.append(f"ветер: порывы до {int(round(g_max))} м/с")
    if r_max >= ALERT_RAIN_MM_H: parts.append(f"дождь до {int(round(r_max))} мм/ч")
    if t_max >= ALERT_TSTORM_PROB_PC: parts.append(f"гроза до {int(round(t_max))}%")
    if parts:
        return "⚠️ Штормовое предупреждение: " + "; ".join(parts)
    return None

# ────────────────────────── Вечерний вывод и рекомендации ──────────────────────────

def evening_conclusion_line(kp_val: Any,
                            kp_status: str,
                            storm_short: str,
                            air_risk: str) -> str:
    """
    Краткий вывод по дню: что главнее — магнитка, воздух или погода.
    """
    try:
        kv = float(kp_val) if isinstance(kp_val, (int, float)) else None
    except Exception:
        kv = None

    if isinstance(kv, float) and kv >= 5.0:
        return (f"Основной фактор — магнитная активность: Кр≈{kv:.1f} ({kp_status}). "
                "Возможна чувствительность у метеозависимых — береги нервную систему.")
    if storm_short == "шторм":
        return ("Основной фактор — погода: возможен сильный ветер или осадки, "
                "самочувствие и техника могут реагировать сильнее обычного.")
    if air_risk in ("высокий", "очень высокий"):
        return ("Основной фактор — качество воздуха: лучше меньше времени проводить "
                "рядом с оживлёнными трассами и промзонами.")

    return "Баланс факторов спокойный: можно планировать обычные дела и мягкий вечерний отдых."


def evening_tips(kp_val: Any,
                 kp_status: str,
                 storm_short: str,
                 air_risk: str) -> List[str]:
    """
    2–3 рекомендации под вечер — без молочки, глютена и специфичных продуктов.
    """
    try:
        kv = float(kp_val) if isinstance(kp_val, (int, float)) else None
    except Exception:
        kv = None

    if isinstance(kv, float) and kv >= 5.0:
        theme = "magnetic"
    elif air_risk in ("высокий", "очень высокий"):
        theme = "air"
    elif storm_short == "шторм":
        theme = "storm"
    else:
        theme = "normal"

    if theme == "magnetic":
        return [
            "Тёплый напиток с магнием или электролитами, меньше кофеина вечером.",
            "5–10 минут мягкой растяжки или йоги — без перегрузок.",
            "Ложитесь до 23:00, экраны приглушить за 1 час до сна.",
        ]
    if theme == "air":
        return [
            "Гуляй ближе к паркам и воде, подальше от больших дорог.",
            "Пей воду небольшими порциями в течение дня.",
            "Проветривай дом короткими проветриваниями по самочувствию.",
        ]
    if theme == "storm":
        return [
            "Планируй дела с запасом по времени, не гони себя.",
            "Подготовь удобную обувь, дождевик/зонт и запасной слой одежды.",
            "Вечером тёплый душ и спокойный фильм вместо новостей.",
        ]
    # спокойный день
    return [
        "Дыши глубже, улыбайся шире, живи ярче.",
        "Ложись спать до 23:00 — 7–8 часов сна поддержат гормональный баланс.",
        "Добавь больше овощей и зелени в течение дня, минимум сахара.",
    ]


def holiday_or_fact(date_obj: pendulum.DateTime, region_name: str) -> str:
    return f"📚 {get_fact(date_obj, region_name)}"

# ────────────────────────── Астроданные (лунный календарь) ──────────────────────────

def _load_lunar_calendar_json() -> Optional[Dict[str, Any]]:
    """
    Ищем файл с астроданными в нескольких стандартных местах:
    - ./lunar_calendar.json
    - ./data/lunar_calendar.json
    - ./data/astro_daily.json

    Формат ожидается таким, как в примере:
    {
      "days": {
        "2025-12-01": { ... }
      }
    }
    """
    here = Path(__file__).parent
    candidates = [
        here / "lunar_calendar.json",
        here / "data" / "lunar_calendar.json",
        here / "data" / "astro_daily.json",
    ]
    for p in candidates:
        try:
            if p.exists():
                return json.loads(p.read_text("utf-8"))
        except Exception:
            logging.warning("Не удалось прочитать %s", p, exc_info=True)
    return None


def astro_block_for_offset(offset_days: int,
                           tz: Union[pendulum.Timezone, str]) -> List[str]:
    """
    Собирает блок:
      🌌 Астрособытия
      🌕 Фаза – длинное описание
      ⏳ VoC ... — без новых стартов.
      ✅ Благоприятно для: ...
      ИЛИ
      ⚠️ Неблагоприятный день.
    """

    tz_obj = pendulum.timezone(tz) if isinstance(tz, str) else tz
    cal = _load_lunar_calendar_json()
    if not cal:
        return []

    days = cal.get("days") or {}
    date_obj = pendulum.today(tz_obj).add(days=offset_days)
    key = date_obj.to_date_string()  # 'YYYY-MM-DD'
    day = days.get(key)
    if not isinstance(day, dict):
        return []

    lines: List[str] = ["🌌 Астрособытия"]

    # Фаза + длинное описание
    phase = day.get("phase")
    long_desc = day.get("long_desc")
    if phase or long_desc:
        text = (str(phase) if phase else "").strip()
        if long_desc:
            if text:
                text += " – "
            text += str(long_desc).strip()
        if text:
            lines.append(text)

    # VoC
    voc = day.get("void_of_course") or {}
    vs, ve = voc.get("start"), voc.get("end")
    if vs and ve:
        lines.append(f"⏳ VoC {vs}–{ve} — без новых стартов.")

    # Благоприятные / неблагоприятные дни
    fav_days = day.get("favorable_days") or {}
    dom = date_obj.day

    cat_meta = {
        "general": ("✨", "общие дела"),
        "shopping": ("💰", "покупки"),
        "travel": ("✈️", "поездки"),
        "haircut": ("💇‍♀️", "стрижки"),
        "health": ("💊", "здоровье"),
    }

    fav_tokens: List[str] = []
    for cat_key, (emo, label) in cat_meta.items():
        cfg = fav_days.get(cat_key) or {}
        arr = cfg.get("favorable") or []
        try:
            if dom in [int(x) for x in arr]:
                fav_tokens.append(f"{emo} {label}")
        except Exception:
            continue

    if fav_tokens:
        lines.append("✅ Благоприятно для: " + ", ".join(fav_tokens) + ".")
    else:
        gen_cfg = fav_days.get("general") or {}
        arr = gen_cfg.get("unfavorable") or []
        try:
            if dom in [int(x) for x in arr]:
                lines.append("⚠️ Неблагоприятный день.")
        except Exception:
            pass

    return lines

# ────────────────────────── Morning (compact) ──────────────────────────
def build_message_morning_compact(region_name: str,
                                  sea_label: str, sea_cities,
                                  other_label: str, other_cities,
                                  tz: Union[pendulum.Timezone, str]) -> str:
    tz_obj = pendulum.timezone(tz) if isinstance(tz, str) else tz
    date_local = pendulum.today(tz_obj)

    # Заголовок как на Кипре + отдельный факт дня
    header = f"<b>🌅 {region_name}: погода на сегодня ({date_local.format('DD.MM.YYYY')})</b>"
    fact_text = get_fact(date_local, region_name)
    fact_text = fact_text.strip()
    fact_line = f"🌾 Доброе утро! {fact_text}" if fact_text else "🌾 Доброе утро!"

    wm_klg = get_weather(KLD_LAT, KLD_LON) or {}
    t_day, t_night, wcode = _fetch_temps_for_offset(KLD_LAT, KLD_LON, tz_obj.name, DAY_OFFSET)
    wind_ms, wind_dir_deg, press_val, press_trend = pick_header_metrics_for_offset(wm_klg, tz_obj, DAY_OFFSET)

    # порывы к полудню для шапки (информативный токен)
    gust = None
    try:
        times = _hourly_times(wm_klg); hourly = wm_klg.get("hourly") or {}
        idx_noon = _nearest_index_for_day(times, date_local.add(days=DAY_OFFSET).date(), 12, tz_obj)
        arr = hourly.get("wind_gusts_10m") or hourly.get("windgusts_10m") or []
        if idx_noon is not None and idx_noon < len(arr):
            gust = float(arr[idx_noon]) / 3.6
    except Exception:
        pass

    desc = code_desc(wcode) or "—"
    tday_i   = int(round(t_day))   if isinstance(t_day,(int,float)) else None
    tnight_i = int(round(t_night)) if isinstance(t_night,(int,float)) else None
    temp_txt = f"{tday_i}/{tnight_i}{NBSP}°C" if (tday_i is not None and tnight_i is not None) else "н/д"
    wind_txt = (f"💨 {wind_ms:.1f} м/с ({compass(wind_dir_deg)})" if isinstance(wind_ms,(int,float)) and wind_dir_deg is not None
                else (f"💨 {wind_ms:.1f} м/с" if isinstance(wind_ms,(int,float)) else "💨 н/д"))
    if isinstance(gust,(int,float)):
        wind_txt += f" • порывы — {int(round(gust))}"
    press_txt = f"🔹 {press_val} гПа {press_trend}" if isinstance(press_val,int) else "🔹 н/д"
    kal_line = f"Погода: 🏙️ Калининград — {temp_txt} • {desc} • {wind_txt} • {press_txt}."

    # Погреться/остыть + море
    tz_name = tz_obj.name
    warm_city, warm_vals = None, None
    cold_city, cold_vals = None, None
    for city, (la, lo) in other_cities:
        tmax, tmin, _ = _fetch_temps_for_offset(la, lo, tz_name, DAY_OFFSET)
        if tmax is None: continue
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
            if isinstance(s,(int,float)): sst_hint = s; break
        except Exception:
            pass
    suit = wetsuit_hint_by_sst(sst_hint)
    sea_txt = f"Море: {suit}." if suit else "Море: н/д."

    # Закат
    sunset = None
    try:
        daily = wm_klg.get("daily") or {}
        ss = (daily.get("sunset") or [None])[0]
        if ss:
            sunset = pendulum.parse(ss).in_tz(tz_obj).format("HH:mm")
    except Exception:
        pass
    sunset_line = f"🌇 Закат сегодня: {sunset}" if sunset else "🌇 Закат: н/д"

    # Курсы (утро)
    fx_line = fx_morning_line(pendulum.now(tz_obj), tz_obj)

    # Воздух
    air = get_air(KLD_LAT, KLD_LON) or {}
    try:
        aqi = air.get("aqi")
        aqi_i = int(round(float(aqi))) if isinstance(aqi,(int,float)) else "н/д"
    except Exception:
        aqi_i = "н/д"

    def _int_or_nd(x):
        try: return str(int(round(float(x))))
        except Exception: return "н/д"

    pm25_int = _int_or_nd(air.get("pm25"))
    pm10_int = _int_or_nd(air.get("pm10"))
    pollen = get_pollen() or {}
    pollen_risk = str(pollen.get("risk")).strip() if pollen.get("risk") else ""

    # здесь сразу считаем уровень воздуха и эмодзи,
    # чтобы переиспользовать в "Итого"
    air_risk = aqi_risk_ru(aqi)
    air_emoji_main = "🟠" if air_risk in ("высокий", "очень высокий") else ("🟡" if air_risk == "умеренный" else "🟢")

    air_line = f"🏭 Воздух: {air_emoji_main} {air_risk} (AQI {aqi_i}) • PM₂.₅ {pm25_int} / PM₁₀ {pm10_int}"
    if pollen_risk:
        air_line += f" • 🌿 пыльца: {pollen_risk}"

    # UVI (если ≥3)
    uvi_info = uvi_for_offset(wm_klg, tz_obj, DAY_OFFSET)
    uvi_line = None
    try:
        uvi_val = None
        if isinstance(uvi_info.get("uvi"), (int, float)):
            uvi_val = float(uvi_info["uvi"])
        elif isinstance(uvi_info.get("uvi_max"), (int, float)):
            uvi_val = float(uvi_info["uvi_max"])
        if isinstance(uvi_val,(int,float)) and uvi_val >= 3:
            uvi_line = f"☀️ УФ: {uvi_val:.0f} — {uvi_label(uvi_val)} • SPF 30+ и головной убор"
    except Exception:
        pass

    # Космопогода
    kp_val, kp_status, kp_age_min, kp_src = _kp_global_swpc()
    age_txt = ""
    if isinstance(kp_age_min, int):
        age_txt = f", 🕓 {kp_age_min // 60}ч назад" if kp_age_min > 180 else f", 🕓 {kp_age_min} мин назад"
    kp_chunk = f"Кр {kp_val:.1f} ({kp_status}{age_txt})" if isinstance(kp_val, (int, float)) else "Кр н/д"

    sw = get_solar_wind() or {}
    v = sw.get("speed_kms"); n = sw.get("density")
    vtxt = f"v {float(v):.0f} км/с" if isinstance(v, (int, float)) else None
    ntxt = f"n {float(n):.1f} см⁻³" if isinstance(n, (int, float)) else None
    parts = [p for p in (vtxt, ntxt) if p]
    sw_chunk = (" • 🌬️ " + ", ".join(parts) + f" — {sw.get('status','н/д')}") if parts else ""
    space_line = "🧲 Космопогода: " + kp_chunk + (sw_chunk or "")

    # Шторм-предупреждение
    storm_line_alert = storm_alert_line(wm_klg, tz_obj)

    # Safecast/радиация
    sc_line = safecast_summary_line()
    official_rad = radiation_line(KLD_LAT, KLD_LON)

    # Шуман (если включён)
    schu_line = schumann_line(get_schumann_with_fallback()) if SHOW_SCHUMANN else None

    # Итог
    storm_short = storm_short_text(wm_klg, tz_obj)
    kp_short = kp_status if isinstance(kp_val, (int, float)) else "н/д"
    air_emoji = air_emoji_main
    itogo = f"🔎 Итого: воздух {air_emoji} • {storm_short} • Кр {kp_short}"

    # Советы
    def safe_tips(theme: str) -> List[str]:
        base = {
            "здоровый день": ["вода и завтрак", "20-мин прогулка до полудня", "короткая растяжка вечером"],
            "магнитные бури": ["лёгкая растяжка перед сном", "5-мин дыхательная пауза", "чаёк с травами"],
            "плохой воздух": ["уменьшите время на улице", "проветривание по ситуации", "тренировка — в помещении"],
        }
        if gpt_blurb:
            try:
                _, tips = gpt_blurb(theme)  # type: ignore
                tips = [str(x).strip() for x in (tips or []) if x]
                if tips:
                    return tips[:3]
            except Exception:
                pass
        return base.get(theme, base["здоровый день"])

    theme = "магнитные бури" if (isinstance(kp_val, (int, float)) and kp_val >= 5) \
            else ("плохой воздух" if air_risk in ("высокий", "очень высокий") else "здоровый день")
    today_line = "✅ Сегодня: " + "; ".join(safe_tips(theme)) + "."

    # Сборка
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
    P.append(air_line)
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
def build_message_legacy_evening(region_name: str,
                                 sea_label: str, sea_cities,
                                 other_label: str, other_cities,
                                 tz: Union[pendulum.Timezone, str]) -> str:
    """
    Вечерний пост с детальной информацией о завтрашнем дне.
    Формат: Калининград → Морские города (с волнами и спортом) → 
    Тёплые/холодные → Астро → Главное и забота о себе
    """
    tz_obj = pendulum.timezone(tz) if isinstance(tz, str) else tz
    tz_name = tz_obj.name
    date_local = pendulum.today(tz_obj).add(days=DAY_OFFSET)

    header = f"<b>🌅 {region_name}: погода на завтра ({date_local.format('DD.MM.YYYY')})</b>"

    P: List[str] = [header]

    # ==================== КАЛИНИНГРАД (шапка) ====================
    wm_main = get_weather(KLD_LAT, KLD_LON) or {}
    
    # Используем day_night_stats для полной статистики
    stats = day_night_stats(KLD_LAT, KLD_LON, tz=tz_name)
    t_day_max = stats.get("t_day_max")
    t_night_min = stats.get("t_night_min")
    rh_min = stats.get("rh_min")
    rh_max = stats.get("rh_max")
    
    # Код погоды из daily
    wcarr = (wm_main.get("daily", {}) or {}).get("weathercode", [])
    wcode = wcarr[1] if isinstance(wcarr, list) and len(wcarr) > 1 else None
    
    # Ветер и давление через обновлённую функцию
    wind_ms, wind_dir_deg, press_val, press_trend = pick_tomorrow_header_metrics(wm_main, tz_obj)

    # Проверка штормовых условий
    storm = storm_flags_for_tomorrow(wm_main, tz_obj)
    gust = storm.get("max_gust_ms")

    # Формируем описание погоды
    desc = code_desc(wcode) or "—"
    
    # Температуры
    temp_txt = (
        f"{t_day_max:.0f}/{t_night_min:.0f}{NBSP}°C"
        if (t_day_max is not None and t_night_min is not None)
        else "н/д"
    )

    # Ветер с порывами
    if isinstance(wind_ms, (int, float)) and wind_dir_deg is not None:
        wind_txt = f"💨 {wind_ms:.1f} м/с ({compass(wind_dir_deg)})"
    elif isinstance(wind_ms, (int, float)):
        wind_txt = f"💨 {wind_ms:.1f} м/с"
    else:
        wind_txt = "💨 н/д"

    if isinstance(gust, (int, float)):
        wind_txt += f" • порывы до {gust:.0f}"

    # Влажность
    rh_txt = ""
    if isinstance(rh_min, (int, float)) and isinstance(rh_max, (int, float)):
        rh_txt = f" • 💧 RH {rh_min:.0f}–{rh_max:.0f}%"

    # Давление
    press_txt = f" • 🔹 {press_val} гПа {press_trend}" if isinstance(press_val, int) else ""

    kal_line = (
        f"🏙️ Калининград: дн/ночь {temp_txt} • {desc} • {wind_txt}{rh_txt}{press_txt}"
    )

    P.append(kal_line)
    P.append("———")

    # Штормовое предупреждение (если есть)
    if storm.get("warning"):
        P.append(storm["warning_text"])
        P.append("———")

    # ==================== МОРСКИЕ ГОРОДА ====================
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
            # Основная строка города
            line = f"{medals[i]} {city}: {d:.0f}/{n:.0f}{NBSP}°C"
            descx = code_desc(wcx)
            if descx:
                line += f" • {descx}"
            if sst_c is not None:
                line += f" • 🌊 {sst_c:.0f}"
            
            # Волны (из Marine API)
            try:
                la, lo = sea_lookup[city]
                wave_h, wave_t = _fetch_wave_for_tomorrow(la, lo, tz_obj)
                if isinstance(wave_h, (int, float)):
                    line += f" • {wave_h:.1f} м"
            except Exception as e:
                if DEBUG_WATER:
                    logging.warning("Wave fetch failed for %s: %s", city, e)
            
            P.append(line)
            
            # Водные активности (только если условия good)
            try:
                la, lo = sea_lookup[city]
                hl = _water_highlights(city, la, lo, tz_obj, sst_c)
                if hl:
                    P.append(f"   {hl}")
            except Exception as e:
                if DEBUG_WATER:
                    logging.exception("water_highlights failed for %s: %s", city, e)

        P.append("———")

    # ==================== ТЁПЛЫЕ/ХОЛОДНЫЕ ГОРОДА ====================
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

    # ==================== АСТРОСОБЫТИЯ ====================
    # Используем ту же дату, что и для основного прогноза (завтра по местному времени)
    astro_section = build_astro_section(date_local=date_local, tz_local=tz_name)
    if astro_section:
        P.append(astro_section)
        P.append("———")

    # ==================== ВЫВОД ====================
    # Получаем данные для анализа
    kp_tuple = get_kp() or (None, "н/д", None, "n/d")
    try:
        kp, ks, kp_ts, kp_src = kp_tuple
    except Exception:
        kp = kp_tuple[0] if isinstance(kp_tuple, (list, tuple)) and len(kp_tuple) > 0 else None
        ks = kp_tuple[1] if isinstance(kp_tuple, (list, tuple)) and len(kp_tuple) > 1 else "н/д"
        kp_ts, kp_src = None, "n/d"

    air = get_air(KLD_LAT, KLD_LON) or {}
    schu_state = {} if DISABLE_SCHUMANN else get_schumann_with_fallback()

    P.append("📜 <b>Завтра: главное и забота о себе</b>")
    
    # Используем умную функцию вывода
    conclusion_lines = build_conclusion(kp, ks, air, storm, schu_state)
    P.extend(conclusion_lines)
    
    P.append("———")

    # ==================== РЕКОМЕНДАЦИИ ====================
    P.append("✅ <b>Рекомендации</b>")
    
    # Определяем тему для рекомендаций
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
    
    # Получаем безопасные рекомендации
    for tip in safe_tips(theme):
        P.append(tip)

    P.append("———")
    
    # Факт дня
    P.append(f"📚 {get_fact(date_local, region_name)}")
    P.append("")
    P.append("#Калининград #погода #здоровье #море")

    return "\n".join(P)


# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================
# (добавьте эти функции, если их нет в вашем коде)

def _fetch_wave_for_tomorrow(lat: float, lon: float, tz_obj: pendulum.Timezone,
                             prefer_hour: int = 12) -> Tuple[Optional[float], Optional[float]]:
    """Получает данные о волнах из Marine API Open-Meteo."""
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
        idx = _nearest_index_for_day(times, pendulum.now(tz_obj).add(days=1).date(), prefer_hour, tz_obj)
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
    sst_hint: Optional[float] = None
) -> Optional[str]:
    """
    Возвращает строку ТОЛЬКО если условия «good».
    Пример: 🧜‍♂️ Отлично: Кайт/Винг/Винд; SUP @Spot (SE/cross) • гидрокостюм 4/3 мм
    Если good-активностей нет — вернёт None (ничего не печатаем).
    """
    wm = get_weather(la, lo) or {}
    wind_ms, wind_dir, _, _ = pick_tomorrow_header_metrics(wm, tz_obj)
    wave_h, _ = _fetch_wave_for_tomorrow(la, lo, tz_obj)

    # порывы около полудня
    def _gust_at_noon(wm: Dict[str, Any], tz: pendulum.Timezone) -> Optional[float]:
        hourly = wm.get("hourly") or {}
        times = _hourly_times(wm)
        idx = _nearest_index_for_day(times, pendulum.now(tz).add(days=1).date(), 12, tz)
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

    # Критерии good для каждого вида спорта
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

    # Если good нет — не печатаем ничего
    if not goods:
        if DEBUG_WATER:
            logging.info("WATER[%s]: no good. wind=%s dir=%s wave_h=%s gust=%s shore=%s",
                         city, wind_val, wind_dir, wave_h, gust_val, shore)
        return None

    # Оформляем good с гидриком
    sst = sst_hint if isinstance(sst_hint, (int, float)) else get_sst(la, lo)
    suit_txt = _wetsuit_hint(sst)
    suit_part = f" • {suit_txt}" if suit_txt else ""

    dir_part = f" ({card}/{shore})" if card or shore else ""
    spot_part = f" @{shore_src}" if shore_src and shore_src not in (city, f"ENV:SHORE_FACE_{_env_city_key(city)}") else ""
    env_mark = " (ENV)" if shore_src and str(shore_src).startswith("ENV:") else ""

    return "🧜‍♂️ Отлично: " + "; ".join(goods) + spot_part + env_mark + dir_part + suit_part


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
# ────────────────────────── Внешний интерфейс ──────────────────────────
def build_message(region_name: str,
                  sea_label: str, sea_cities,
                  other_label: str, other_cities,
                  tz: Union[pendulum.Timezone, str]) -> str:
    if POST_MODE == "morning":
        return build_message_morning_compact(region_name, sea_label, sea_cities, other_label, other_cities, tz)
    return build_message_legacy_evening(region_name, sea_label, sea_cities, other_label, other_cities, tz)

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
    "pick_header_metrics_for_offset",
    "radiation_line",
]
