#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
post_common.py — Kaliningrad morning/evening post builder.

Правки по макету:
• Safecast — одна строка и печатается ТОЛЬКО если есть свежие данные.
• УФ-индекс — показываем, если >= 3 (нужен uv_index_max в weather.py).
• Курс: символ рубля принудительно «₽».
• «Сегодня» — советы в одну строку через «;».
• Давление в шапке: «🔹 {давление} гПа {стрелка}». Возраст Kp с «🕓».
"""

from __future__ import annotations

import os
import re
import json
import html
import math
import logging
from typing import Any, Dict, List, Tuple, Optional, Union
from pathlib import Path

import pendulum
from telegram import Bot, constants

# ─── project deps ───
from utils   import compass, get_fact, AIR_EMOJI, pm_color, kp_emoji, kmh_to_ms, smoke_index
from weather import get_weather
from air     import get_air, get_sst, get_kp, get_solar_wind
from pollen  import get_pollen
from radiation import get_radiation

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ─────────────────────────────────────────────────────────────────────────────
# ENV flags
def _env_on(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")

POST_MODE    = (os.getenv("POST_MODE") or "").strip().lower()  # "morning" / "evening"
DAY_OFFSET   = int(os.getenv("DAY_OFFSET", "1" if POST_MODE == "evening" else "0") or 0)

SHOW_AIR      = _env_on("SHOW_AIR",      POST_MODE != "evening")
SHOW_SPACE    = _env_on("SHOW_SPACE",    POST_MODE != "evening")
SHOW_SCHUMANN = _env_on("SHOW_SCHUMANN", POST_MODE != "evening")  # (в этом файле не используем)

# ─────────────────────────────────────────────────────────────────────────────
# Basics / constants
KLD_LAT, KLD_LON = 54.710426, 20.452214
NBSP = "\u00A0"
RUB  = "\u20BD"
CPM_TO_USVH = float(os.getenv("CPM_TO_USVH", "0.000571"))

CACHE_DIR = Path(".cache"); CACHE_DIR.mkdir(parents=True, exist_ok=True)

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
def _pick(d: Dict[str, Any], *keys, default=None):
    for k in keys:
        if k in d:
            return d[k]
    return default

def _as_tz(tz: Union[str, pendulum.Timezone]) -> pendulum.Timezone:
    return pendulum.timezone(tz) if isinstance(tz, str) else tz

def _hourly_times(wm: Dict[str, Any]) -> List[pendulum.DateTime]:
    hourly = wm.get("hourly") or {}
    times = hourly.get("time") or hourly.get("time_local") or hourly.get("timestamp") or []
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

WMO_DESC = {
    0:"☀️ ясно",1:"⛅ ч.обл",2:"☁️ обл",3:"🌥 пасм",
    45:"🌫 туман",48:"🌫 изморозь",51:"🌦 морось",61:"🌧 дождь",
    71:"❄️ снег",95:"⛈ гроза"
}
def code_desc(c: Any) -> Optional[str]:
    try:
        return WMO_DESC.get(int(c))
    except Exception:
        return None

def _nearest_index_for_day(times: List[pendulum.DateTime], date_obj: pendulum.Date,
                           prefer_hour: int, tz: pendulum.Timezone) -> Optional[int]:
    if not times:
        return None
    target = pendulum.datetime(date_obj.year, date_obj.month, date_obj.day, prefer_hour, 0, tz=tz)
    best_i, best_diff = None, None
    for i, dt in enumerate(times):
        try:
            dt_local = dt.in_tz(tz)
        except Exception:
            dt_local = dt
        if dt_local.date() != date_obj:
            continue
        diff = abs((dt_local - target).total_seconds())
        if best_diff is None or diff < best_diff:
            best_i, best_diff = i, diff
    return best_i

def kmh_arr_to_ms(vals: List[Any]) -> List[float]:
    out=[]
    for v in vals:
        try:
            out.append(kmh_to_ms(float(v)))
        except Exception:
            pass
    return out

# ─────────────────────────────────────────────────────────────────────────────
# Metrics for target day
def pick_header_metrics_for_offset(wm: Dict[str, Any], tz: pendulum.Timezone, offset_days: int
    ) -> Tuple[Optional[float], Optional[int], Optional[int], str]:
    """Ветер/направление/давление (≈полдень), тренд давления."""
    hourly = wm.get("hourly") or {}
    times = _hourly_times(wm)
    target_date = pendulum.today(tz).add(days=offset_days).date()

    spd_arr = _pick(hourly, "windspeed_10m", "wind_speed_10m", default=[]) or []
    dir_arr = _pick(hourly, "winddirection_10m", "wind_direction_10m", default=[]) or []
    prs_arr = hourly.get("surface_pressure", []) or hourly.get("pressure", []) or []

    idx_noon = _nearest_index_for_day(times, target_date, 12, tz) if times else None
    idx_morn = _nearest_index_for_day(times, target_date, 6, tz) if times else None

    wind_ms = None; wind_dir = None; press_val = None; trend = "→"

    if idx_noon is not None:
        try: spd = float(spd_arr[idx_noon]) if idx_noon < len(spd_arr) else None
        except Exception: spd = None
        try: wdir = float(dir_arr[idx_noon]) if idx_noon < len(dir_arr) else None
        except Exception: wdir = None
        try: p_noon = float(prs_arr[idx_noon]) if idx_noon < len(prs_arr) else None
        except Exception: p_noon = None
        try:
            p_morn = float(prs_arr[idx_morn]) if (idx_morn is not None and idx_morn < len(prs_arr)) else None
        except Exception:
            p_morn = None

        wind_ms = kmh_to_ms(spd) if isinstance(spd, (int, float)) else None
        wind_dir = int(round(wdir)) if isinstance(wdir, (int, float)) else None
        press_val = int(round(p_noon)) if isinstance(p_noon, (int, float)) else None
        if isinstance(p_noon, (int, float)) and isinstance(p_morn, (int, float)):
            d = p_noon - p_morn
            trend = "↑" if d >= 0.3 else "↓" if d <= -0.3 else "→"

    # fallback от current
    if wind_ms is None or wind_dir is None or press_val is None:
        cur = (wm.get("current") or wm.get("current_weather") or {}) or {}
        if wind_ms is None:
            spd = _pick(cur, "windspeed_10m", "windspeed", "wind_speed_10m")
            wind_ms = kmh_to_ms(spd) if isinstance(spd, (int, float)) else None
        if wind_dir is None:
            wdir = _pick(cur, "winddirection_10m", "winddirection", "wind_direction_10m")
            wind_dir = int(round(float(wdir))) if isinstance(wdir, (int, float)) else None
        if press_val is None:
            p = _pick(cur, "surface_pressure", "pressure")
            press_val = int(round(float(p))) if isinstance(p, (int, float)) else None
    return wind_ms, wind_dir, press_val, trend

def storm_flags_for_offset(wm: Dict[str, Any], tz: pendulum.Timezone, offset_days: int) -> Dict[str, Any]:
    hourly = wm.get("hourly") or {}
    times = _hourly_times(wm)
    if not times:
        return {"warning": False}

    target = pendulum.today(tz).add(days=offset_days).date()
    idxs = [i for i, t in enumerate(times) if t.in_tz(tz).date() == target]

    def _vals(arr):
        out=[]
        for i in idxs:
            if i < len(arr):
                try:
                    out.append(float(arr[i]))
                except Exception:
                    pass
        return out

    wind_kmh = _pick(hourly, "windspeed_10m", "wind_speed_10m", default=[]) or []
    gust_kmh = _pick(hourly, "windgusts_10m", "wind_gusts_10m", default=[]) or []
    rain = hourly.get("rain", []) or []
    tprob = hourly.get("thunderstorm_probability", []) or []

    max_speed_ms = max(kmh_arr_to_ms(_vals(wind_kmh)), default=None) if idxs else None
    max_gust_ms  = max(kmh_arr_to_ms(_vals(gust_kmh)), default=None) if idxs else None
    heavy_rain   = (max(_vals(rain), default=0) >= 8.0) if idxs else False
    thunder      = (max(_vals(tprob), default=0) >= 60) if idxs else False

    reasons=[]
    if isinstance(max_speed_ms,(int,float)) and max_speed_ms >= 13: reasons.append(f"ветер до {max_speed_ms:.0f} м/с")
    if isinstance(max_gust_ms,(int,float)) and max_gust_ms >= 17: reasons.append(f"порывы до {max_gust_ms:.0f} м/с")
    if heavy_rain: reasons.append("сильный дождь")
    if thunder: reasons.append("гроза")

    return {
        "warning": bool(reasons),
        "warning_text": "⚠️ <b>Штормовое предупреждение</b>: " + ", ".join(reasons) if reasons else "",
        "max_gust_ms": max_gust_ms,
    }

def _fetch_temps_for_offset(lat: float, lon: float, tz_name: str, offset_days: int
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
        if i is None or i < 0 or i >= len(arr): return None
        try: return float(arr[i])
        except Exception: return None

    tmax = _num(daily.get("temperature_2m_max") or [], idx)
    tmin = _num(daily.get("temperature_2m_min") or [], idx)
    wc = None
    wc_arr = daily.get("weathercode") or []
    if idx is not None and idx < len(wc_arr):
        try: wc = int(wc_arr[idx])
        except Exception: wc = None
    return tmax, tmin, wc

# ─────────────────────────────────────────────────────────────────────────────
# Safecast
def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        if not path.exists(): return None
        return json.loads(path.read_text("utf-8"))
    except Exception:
        return None

def load_safecast() -> Optional[Dict[str, Any]]:
    paths: List[Path] = []
    if os.getenv("SAFECAST_FILE"):
        paths.append(Path(os.getenv("SAFECAST_FILE")))
    here = Path(__file__).parent
    paths += [here / "data" / "safecast_kaliningrad.json"]
    sc: Optional[Dict[str, Any]] = None
    for p in paths:
        sc = _read_json(p)
        if sc: break
    if not sc: return None
    ts = sc.get("ts")
    try:
        now_ts = pendulum.now("UTC").int_timestamp
        if not isinstance(ts,(int,float)) or (now_ts - int(ts) > 24*3600):
            return None
    except Exception:
        return None
    return sc

def safecast_usvh_risk(x: float) -> tuple[str, str]:
    if x <= 0.15: return "🟢", "низкий"
    if x <= 0.30: return "🟡", "умеренный"
    return "🔵", "выше нормы"

def safecast_pm_level(pm25: Optional[float], pm10: Optional[float]) -> Tuple[str, str]:
    def l25(x: float) -> int: return 0 if x<=15 else 1 if x<=35 else 2 if x<=55 else 3
    def l10(x: float) -> int: return 0 if x<=30 else 1 if x<=50 else 2 if x<=100 else 3
    worst = -1
    if isinstance(pm25,(int,float)): worst=max(worst,l25(float(pm25)))
    if isinstance(pm10,(int,float)): worst=max(worst,l10(float(pm10)))
    if worst<0: return "⚪","н/д"
    return (["🟢","🟡","🟠","🔴"][worst],
            ["низкий","умеренный","высокий","очень высокий"][worst])

def safecast_one_line() -> Optional[str]:
    """Вернёт строку Safecast или None, если данных нет/старые."""
    sc = load_safecast()
    if not sc:
        return None
    pm25, pm10 = sc.get("pm25"), sc.get("pm10")
    cpm  = sc.get("cpm")
    usvh = sc.get("radiation_usvh")
    if not isinstance(usvh,(int,float)) and isinstance(cpm,(int,float)):
        usvh = float(cpm) * CPM_TO_USVH

    # минимум одно поле должно быть «живым»
    if not any(isinstance(x,(int,float)) for x in (pm25, pm10, cpm, usvh)):
        return None

    em_air, lbl_air = safecast_pm_level(pm25, pm10)
    pm25_txt = f"{pm25:.0f}" if isinstance(pm25,(int,float)) else "—"
    pm10_txt = f"{pm10:.0f}" if isinstance(pm10,(int,float)) else "—"
    cpm_txt  = f"{cpm:.0f}"  if isinstance(cpm,(int,float))  else "—"
    if isinstance(usvh,(int,float)):
        em_rad, lbl_rad = safecast_usvh_risk(float(usvh))
        usvh_txt = f"{float(usvh):.3f}"
    else:
        em_rad, lbl_rad, usvh_txt = "⚪", "н/д", "—"

    return f"🧪 Safecast: {em_air} {lbl_air} · PM₂.₅ {pm25_txt} | PM₁₀ {pm10_txt} · {cpm_txt} CPM ≈ {usvh_txt} μSv/h — {em_rad} {lbl_rad}"

def radiation_line(lat: float, lon: float) -> Optional[str]:
    data = get_radiation(lat, lon) or {}
    dose = data.get("dose")
    if isinstance(dose,(int,float)):
        if dose <= 0.15: em, lbl = "🟢","низкий"
        elif dose <= 0.30: em, lbl = "🟡","повышенный"
        else: em, lbl = "🔴","высокий"
        return f"{em} Радиация: {dose:.3f} μSv/h ({lbl})"
    return None

# ─────────────────────────────────────────────────────────────────────────────
# UVI
def uvi_for_offset(wm: Dict[str, Any], tz: pendulum.Timezone, offset_days: int) -> Optional[float]:
    daily = wm.get("daily") or {}
    times = _daily_times(wm)
    target = pendulum.today(tz).add(days=offset_days).date()
    try:
        idx = times.index(target)
    except ValueError:
        return None
    arr = daily.get("uv_index_max") or daily.get("uv_index_clear_sky_max") or []
    if idx < len(arr):
        try:
            return float(arr[idx])
        except Exception:
            return None
    return None

# ─────────────────────────────────────────────────────────────────────────────
# FX — простая строка с курсами (утро)
def _fmt_delta(x: Any) -> str:
    try:
        v = float(x)
    except Exception:
        return "0.00"
    sign = "−" if v < 0 else ""
    return f"{sign}{abs(v):.2f}"

def fx_line(date_local: pendulum.DateTime, tz: pendulum.Timezone) -> Optional[str]:
    try:
        import importlib
        fx = importlib.import_module("fx")
        rates = fx.get_rates(date=date_local, tz=tz) or {}
        def tok(code: str) -> Tuple[Optional[float], Optional[Any]]:
            r = rates.get(code) or {}
            return r.get("value"), r.get("delta")
        u, ud = tok("USD"); e, ed = tok("EUR"); c, cd = tok("CNY")
        if not any(isinstance(x,(int,float)) for x in (u,e,c)):
            return None
        us = f"{float(u):.2f}" if isinstance(u,(int,float)) else "н/д"
        es = f"{float(e):.2f}" if isinstance(e,(int,float)) else "н/д"
        cs = f"{float(c):.2f}" if isinstance(c,(int,float)) else "н/д"
        return f"💱 Курсы (утро): USD {us} {RUB} ({_fmt_delta(ud)}) • EUR {es} {RUB} ({_fmt_delta(ed)}) • CNY {cs} {RUB} ({_fmt_delta(cd)})"
    except Exception:
        return None

# ─────────────────────────────────────────────────────────────────────────────
# Tips
SAFE_TIPS = [
    "Лёгкая растяжка перед сном",
    "5-минутная дыхательная пауза",
    "Заварите чай с травами",
]
def safe_tips(theme: str) -> List[str]:
    # можно расширить; для стабильности вернём fallback
    return SAFE_TIPS

# ─────────────────────────────────────────────────────────────────────────────
# Morning compact message (по макету)
def build_message_morning_compact(
    region_name: str,
    sea_label: str, sea_cities,
    other_label: str, other_cities,
    tz: Union[str, pendulum.Timezone],
) -> str:
    tz_obj = _as_tz(tz)
    tz_name = tz_obj.name
    date_local = pendulum.today(tz_obj)

    P: List[str] = []
    P.append(f"<b>🌅 {region_name}: погода на сегодня ({date_local.format('DD.MM.YYYY')})</b>")

    # Калининград — основные метрики
    wm_klg = get_weather(KLD_LAT, KLD_LON) or {}
    t_day_max, t_night_min, wc = _fetch_temps_for_offset(KLD_LAT, KLD_LON, tz_name, 0)
    d_i = int(round(t_day_max)) if isinstance(t_day_max,(int,float)) else None
    n_i = int(round(t_night_min)) if isinstance(t_night_min,(int,float)) else None
    kal_temp = f"{d_i}/{n_i}{NBSP}°C" if (d_i is not None and n_i is not None) else "н/д"

    wind_ms, wind_dir, press_val, press_trend = pick_header_metrics_for_offset(wm_klg, tz_obj, 0)
    wind_part = f"💨 {wind_ms:.1f} м/с ({compass(wind_dir)})" if isinstance(wind_ms,(int,float)) and wind_dir is not None \
                else (f"💨 {wind_ms:.1f} м/с" if isinstance(wind_ms,(int,float)) else "💨 н/д")
    storm = storm_flags_for_offset(wm_klg, tz_obj, 0)
    gust = storm.get("max_gust_ms")
    if isinstance(gust,(int,float)):
        wind_part += f" • порывы — {int(round(gust))}"
    press_part = f"{press_val} гПа {press_trend}" if isinstance(press_val,int) else "н/д"

    desc = code_desc(wc) or "—"
    P.append(
        "Доброе утро 🏙️ Калининград — "
        f"{kal_temp} • {desc} • {wind_part} • 🔹 {press_part}."
    )

    # «Погреться / остыть»
    temp_map: Dict[str, Tuple[float, float]] = {}
    for city, (la, lo) in other_cities:
        tmax, tmin, _ = _fetch_temps_for_offset(la, lo, tz_name, 0)
        if tmax is None: continue
        temp_map[city] = (tmax, tmin or tmax)
    warm_city, cold_city = None, None
    if temp_map:
        warm_city = max(temp_map.items(), key=lambda kv: kv[1][0])
        cold_city = min(temp_map.items(), key=lambda kv: kv[1][0])

    warm_str = (f"{warm_city[0]} {int(round(warm_city[1][0]))}/{int(round(warm_city[1][1]))}{NBSP}°C"
                if warm_city else "н/д")
    cold_str = (f"{cold_city[0]} {int(round(cold_city[1][0]))}/{int(round(cold_city[1][1]))}{NBSP}°C"
                if cold_city else "н/д")

    # wetsuit hint по первой морской точке
    suit_hint = None
    try:
        if sea_cities:
            la, lo = sea_cities[0][1]
            sst = get_sst(la, lo)
            if isinstance(sst,(int,float)):
                t=float(sst)
                if   t>=22: suit_hint=None
                elif t>=20: suit_hint="гидрокостюм шорти 2 мм"
                elif t>=17: suit_hint="гидрокостюм 3/2 мм"
                elif t>=14: suit_hint="гидрокостюм 4/3 мм (боты)"
                elif t>=12: suit_hint="гидрокостюм 5/4 мм (боты, перчатки)"
                elif t>=10: suit_hint="гидрокостюм 5/4 мм + капюшон"
                else:       suit_hint="гидрокостюм 6/5 мм + капюшон"
    except Exception:
        pass
    suit_txt = f"Море: {suit_hint}." if suit_hint else "Море: по ощущениям — прохладно."

    P.append(f"Погреться: {warm_str}; остыть: {cold_str}. {suit_txt}")

    # Закат
    sunset = None
    try:
        daily = wm_klg.get("daily") or {}
        sarr = daily.get("sunset") or []
        if sarr:
            sunset = pendulum.parse(sarr[0]).in_tz(tz_obj).format("HH:mm")
    except Exception:
        pass
    P.append(f"\n🌇 Закат: {sunset or 'н/д'}")

    # FX
    fx_txt = fx_line(date_local, tz_obj)
    if fx_txt:
        P.append(fx_txt)

    # Air + optional Safecast
    if SHOW_AIR:
        air = get_air(KLD_LAT, KLD_LON) or {}
        lvl = air.get("lvl","н/д")
        aqi = air.get("aqi","н/д")
        pm25 = air.get("pm25"); pm10 = air.get("pm10")
        pm25i = f"{pm25:.0f}" if isinstance(pm25,(int,float)) else "—"
        pm10i = f"{pm10:.0f}" if isinstance(pm10,(int,float)) else "—"
        P.append(f"🏭 Воздух: {AIR_EMOJI.get(lvl,'⚪')} {lvl} (AQI {aqi}) • PM₂.₅ {pm25i} / PM₁₀ {pm10i}")

        sc_line = safecast_one_line()
        if sc_line:
            P.append(sc_line)

        # дым/смог, если есть
        em_sm, lbl_sm = smoke_index(pm25, pm10)
        if lbl_sm and str(lbl_sm).strip().lower() not in ("низкое", "низкий", "нет", "н/д"):
            P.append(f"🔥 Задымление: {em_sm} {lbl_sm}")

        # официоз радиации — если есть
        rl = radiation_line(KLD_LAT, KLD_LON)
        if rl:
            P.append(rl)

        # Пыльца (если возвращается)
        p = get_pollen() or None
        if p:
            P.append(f"🌿 Пыльца: Деревья {p.get('tree','0.0')} | Травы {p.get('grass','0.0')} | Сорняки {p.get('weed','0.0')} — риск {p.get('risk','н/д')}")

    # UVI (если >=3)
    uvi = uvi_for_offset(wm_klg, tz_obj, 0)
    if isinstance(uvi,(int,float)) and uvi >= 3:
        P.append(f"☀️ УФ: {uvi:.0f} — высокий • SPF 30+ и головной убор")

    # Space weather
    if SHOW_SPACE:
        kp_tuple = get_kp() or (None, "н/д", None, "n/d")
        try:
            kp, ks, kp_ts, _ = kp_tuple
        except Exception:
            kp = kp_tuple[0]; ks = kp_tuple[1]; kp_ts = None
        age_txt = ""
        try:
            if isinstance(kp_ts,int):
                dt_min = int((pendulum.now('UTC').int_timestamp - kp_ts)/60)
                if dt_min > 180: age_txt = f" (🕓 {dt_min//60}ч назад)"
                elif dt_min >= 0: age_txt = f" (🕓 {dt_min} мин назад)"
        except Exception:
            age_txt = ""
        if isinstance(kp,(int,float)):
            P.append(f"{kp_emoji(kp)} Геомагнитка: Kp={kp:.1f} ({ks}){age_txt}")
        else:
            P.append("🧲 Геомагнитка: н/д")

        sw = get_solar_wind() or {}
        bz, bt, v, n = sw.get("bz"), sw.get("bt"), sw.get("speed_kms"), sw.get("density")
        status = sw.get("status", "н/д")
        parts=[]
        if isinstance(v,(int,float)):  parts.append(f"v {v:.0f} км/с")
        if isinstance(n,(int,float)):  parts.append(f"n {n:.1f} см⁻³")
        P.append("🌬️ SW: " + ", ".join(parts) + f" — {status}")

    # Итого
    storm_short = "без шторма" if not storm.get("warning") else "шторм"
    air_short = "🟢"  # упрощённо: если AQI нет — считаем норм
    if SHOW_AIR:
        air = get_air(KLD_LAT, KLD_LON) or {}
        try:
            aqi = float(air.get("aqi"))
            air_short = "🟡" if aqi >= 100 else "🟢"
        except Exception:
            pass
    kp_short = "буря" if (SHOW_SPACE and isinstance(kp,(int,float)) and kp >= 7) else \
               "активно" if (SHOW_SPACE and isinstance(kp,(int,float)) and kp >= 5) else "спокойно"

    P.append("\n🔎 Итого: воздух " + air_short + f" • {storm_short} • Kp {kp_short}")

    # Сегодня — советы в одну строку
    tips = safe_tips("здоровый день")
    if tips:
        P.append("✅ Сегодня: " + "; ".join(tips[:3]))

    # Финал: праздник/факт дня
    P.append(f"\n📚 {get_fact(date_local, region_name)}")
    P.append("#Калининград #погода #здоровье #сегодня #море")
    return "\n".join(P)

# ─────────────────────────────────────────────────────────────────────────────
# Router (поддержка evening – пока возвращаем тот же компакт)
def build_message(
    region_name: str,
    sea_label: str, sea_cities,
    other_label: str, other_cities,
    tz: Union[str, pendulum.Timezone],
) -> str:
    if (POST_MODE or "morning") == "morning" or DAY_OFFSET == 0:
        return build_message_morning_compact(region_name, sea_label, sea_cities, other_label, other_cities, tz)
    # на случай вечернего — используем тот же макет со сдвигом дня
    return build_message_morning_compact(region_name, sea_label, sea_cities, other_label, other_cities, tz)

# ─────────────────────────────────────────────────────────────────────────────
# Send / main
async def send_common_post(
    bot: Bot,
    chat_id: int,
    region_name: str,
    sea_label: str,
    sea_cities,
    other_label: str,
    other_cities,
    tz: Union[str, pendulum.Timezone],
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
    tz: Union[str, pendulum.Timezone],
) -> None:
    await send_common_post(bot, chat_id, region_name, sea_label, sea_cities, other_label, other_cities, tz)

__all__ = [
    "build_message",
    "send_common_post",
    "main_common",
    "pick_header_metrics_for_offset",
    "storm_flags_for_offset",
]
