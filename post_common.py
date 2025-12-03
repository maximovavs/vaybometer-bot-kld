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

def holiday_or_fact(date_obj: pendulum.DateTime, region_name: str) -> str:
    return f"📚 {get_fact(date_obj, region_name)}"

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
# ────────────────────────── Evening (legacy) ──────────────────────────
def build_message_legacy_evening(region_name: str,
                                 sea_label: str, sea_cities,
                                 other_label: str, other_cities,
                                 tz: Union[pendulum.Timezone, str]) -> str:
    tz_obj = pendulum.timezone(tz) if isinstance(tz, str) else tz
    base = pendulum.today(tz_obj).add(days=1)
    P: List[str] = [f"<b>🌅 {region_name}: погода на завтра ({base.format('DD.MM.YYYY')})</b>"]
    wm_klg = get_weather(KLD_LAT, KLD_LON) or {}
    t_day, t_night, wcode = _fetch_temps_for_offset(KLD_LAT, KLD_LON, tz_obj.name, 1)
    wind_ms, wind_dir_deg, press_val, press_trend = pick_header_metrics_for_offset(wm_klg, tz_obj, 1)
    desc = code_desc(wcode) or "—"
    temp_txt = (f"{int(round(t_day))}/{int(round(t_night))}{NBSP}°C"
                if isinstance(t_day,(int,float)) and isinstance(t_night,(int,float)) else "н/д")
    wind_txt = (f"💨 {wind_ms:.1f} м/с ({compass(wind_dir_deg)})"
                if isinstance(wind_ms,(int,float)) and wind_dir_deg is not None else "💨 н/д")
    press_txt = f"🔹 {press_val} гПа {press_trend}" if isinstance(press_val,int) else "🔹 н/д"
    P.append(f"🏙️ Калининград: дн/ночь {temp_txt} • {desc} • {wind_txt} • {press_txt}")
    P.append("")
    P.append(holiday_or_fact(base, region_name))
    return "\n".join(P)

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
