#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
post_common.py — VayboMeter (Калининград).

Ключевые моменты:
• Режим "morning" — компактный макет как в Кипре (закат, AQI, космопогода, итоги, советы).
• Kp как в Кипре/мировом: последний закрытый 3-часовой бар SWPC (Estimated Kp),
  формат «Кр 3.0 (умеренно, 🕓 5ч назад)». Если данных >9ч — считаем н/д.
• УФ: берём daily uv_index_max от Open-Meteo (или почасовой максимум), строка выводится только при UVI ≥ 3.
• Курсы с жёстким символом рубля \u20BD.
• Однострочные рекомендации: три пункта через «; ».
• Safecast: печатаем только если есть данные (нет — ничего не выводим).
• Давление в шапке: «🔹 {value} гПа {стрелка}».

ENV-переключатели:
  POST_MODE      ∈ {"morning","evening"} (косметика заголовка/макета)
  DAY_OFFSET     ∈ {"0","1",...}   — целевой день (0=сегодня, 1=завтра)
  ASTRO_OFFSET   ∈ {"0","1",...}   — для астроблока (по умолчанию = DAY_OFFSET)
  SHOW_AIR       ∈ {"0","1"}       — печатать воздух/пыльцу/офиц.радиацию
  SHOW_SPACE     ∈ {"0","1"}       — печатать космопогоду
  SHOW_SCHUMANN  ∈ {"0","1"}       — печатать Шумана (не в компактном утре)

В проекте требуются модули:
  utils, weather, air, pollen, radiation, gpt (+ опционально schumann, fx)
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

# utils / внешние источники
from utils        import compass, get_fact, AIR_EMOJI, pm_color, kmh_to_ms, smoke_index
from weather      import get_weather
from air          import get_air, get_sst, get_kp, get_solar_wind
from pollen       import get_pollen
from radiation    import get_radiation
from gpt          import gpt_blurb, gpt_complete  # микро-LLM (рекомендации/астро)

# (опц.) HTTP для marine (волна)
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
RUBLE = "\u20BD"  # жёстко используем символ рубля

# ────────────────────────── базовые константы ──────────────────────────
KLD_LAT, KLD_LON = 54.710426, 20.452214
CPM_TO_USVH = float(os.getenv("CPM_TO_USVH", "0.000571"))

CACHE_DIR = Path(".cache"); CACHE_DIR.mkdir(exist_ok=True, parents=True)
USE_DAILY_LLM = os.getenv("DISABLE_LLM_DAILY", "").strip().lower() not in ("1", "true", "yes", "on")

# ────────────────────────── LLM safety ──────────────────────────
DISABLE_LLM_TIPS = os.getenv("DISABLE_LLM_TIPS", "").strip().lower() in ("1", "true", "yes", "on")
ASTRO_LLM_TEMP = float(os.getenv("ASTRO_LLM_TEMP", "0.2"))

SAFE_TIPS_FALLBACKS = {
    "здоровый день": [
        "вода и завтрак", "20-мин прогулка до полудня", "короткая растяжка вечером"
    ],
    "плохая погода": [
        "тёплые слои и непромокаемая куртка", "дела под крышу", "заложите время на дорогу"
    ],
    "магнитные бури": [
        "уменьшите перегрузки", "больше воды и магний", "ранний сон и меньше экранов"
    ],
    "плохой воздух": [
        "сократите время на улице", "проветривание по ситуации", "тренировки в помещении"
    ],
    "волны Шумана": [
        "спокойный темп дня", "лёгкая еда/тёплый чай", "ранний сон"
    ],
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
            t = _sanitize_line(t, max_len=120)
            if not t or _looks_gibberish(t): continue
            # оставляем короткие фразы без финальной точки — лучше смотрятся в Telegram
            out.append(t.rstrip("."))
        if out: return out
    except Exception as e:
        logging.warning("LLM tips failed: %s", e)
    return SAFE_TIPS_FALLBACKS.get(k, SAFE_TIPS_FALLBACKS["здоровый день"])

# ────────────────────────── пороги гидрокостюмов ──────────────────────────
WSUIT_NONE   = float(os.getenv("WSUIT_NONE",   "22"))
WSUIT_SHORTY = float(os.getenv("WSUIT_SHORTY", "20"))
WSUIT_32     = float(os.getenv("WSUIT_32",     "17"))
WSUIT_43     = float(os.getenv("WSUIT_43",     "14"))
WSUIT_54     = float(os.getenv("WSUIT_54",     "12"))
WSUIT_65     = float(os.getenv("WSUIT_65",     "10"))

# ────────────────────────── вспомогалки ──────────────────────────
def _as_tz(tz: Union[pendulum.Timezone, str]) -> pendulum.Timezone:
    return pendulum.timezone(tz) if isinstance(tz, str) else tz

WMO_DESC = {
    0:"☀️ ясно",1:"⛅ ч.обл",2:"☁️ обл",3:"🌥 пасм",45:"🌫 туман",48:"🌫 изморозь",
    51:"🌦 морось",61:"🌧 дождь",71:"❄️ снег",95:"⛈ гроза"
}
def code_desc(c: Any) -> Optional[str]:
    try: return WMO_DESC.get(int(c))
    except Exception: return None

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
            dt = pendulum.parse(str(t))
            out.append(dt.date())
        except Exception:
            continue
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

def _pick(d: Dict[str, Any], *keys, default=None):
    for k in keys:
        if k in d: return d[k]
    return default

# ────────────────────────── хедер Кёнига ──────────────────────────
def pick_header_metrics_for_offset(wm: Dict[str, Any], tz: pendulum.Timezone, offset_days: int) -> Tuple[Optional[float], Optional[int], Optional[int], str]:
    """Ветер/направление/давление для дня с указанным смещением (≈полдень)."""
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

def _circular_mean_deg(deg_list: List[float]) -> Optional[float]:
    if not deg_list: return None
    x = sum(math.cos(math.radians(d)) for d in deg_list)
    y = sum(math.sin(math.radians(d)) for d in deg_list)
    if x == 0 and y == 0: return None
    ang = math.degrees(math.atan2(y, x))
    return (ang + 360.0) % 360.0

# ────────────────────────── шторм-флаги ──────────────────────────
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

# ────────────────────────── УФ-индекс (из weather) ──────────────────────────
def _uv_from_weather(wm: Dict[str, Any], tz: pendulum.Timezone, offset_days: int = 0) -> Dict[str, Any]:
    """Возвращает {'uvi_now','uvi_max','label','source'}; печатаем только если uvi>=3."""
    hourly = wm.get("hourly") or {}
    daily  = wm.get("daily")  or {}
    times  = hourly.get("time") or []
    uvi_arr = hourly.get("uv_index") or hourly.get("uv_index_clear_sky") or []
    date_str = pendulum.today(tz).add(days=offset_days).to_date_string()

    def uvi_label(v: Optional[float]) -> str:
        if not isinstance(v,(int,float)): return "н/д"
        x = float(v)
        if x < 3:  return "низкий"
        if x < 6:  return "умеренный"
        if x < 8:  return "высокий"
        if x < 11: return "очень высокий"
        return "экстремальный"

    # текущее (берём ближайший час сегодняшнего дня)
    uvi_now = None
    try:
        now_hh = pendulum.now(tz).format("HH")
        for t, v in zip(times, uvi_arr):
            if not t or not t.startswith(date_str): continue
            if t[11:13] == now_hh and isinstance(v, (int, float)):
                uvi_now = float(v); break
    except Exception:
        uvi_now = None

    # максимум за день
    uvi_max = None
    try:
        if daily.get("uv_index_max"):
            uvi_max = float((daily["uv_index_max"] or [None])[0])
        else:
            day_vals = [float(v) for t, v in zip(times, uvi_arr) if t and t.startswith(date_str) and isinstance(v,(int,float))]
            if day_vals:
                uvi_max = max(day_vals)
    except Exception:
        pass

    return {
        "uvi": uvi_now,
        "uvi_max": uvi_max,
        "label": uvi_label(uvi_now if uvi_now is not None else uvi_max),
        "source": "daily" if (daily.get("uv_index_max")) else ("hourly" if uvi_arr else None),
    }

def uv_line_if_needed(wm: Dict[str, Any], tz: pendulum.Timezone, offset_days: int = 0) -> Optional[str]:
    u = _uv_from_weather(wm, tz, offset_days)
    uvi = u.get("uvi") if isinstance(u.get("uvi"), (int, float)) else u.get("uvi_max")
    try:
        val = float(uvi) if uvi is not None else None
    except Exception:
        val = None
    if val is None or val < 3:
        return None
    # краткая рекомендация
    label = u.get("label") or "высокий"
    return f"☀️ УФ: {val:.0f} — {label} • SPF 30+ и головной убор"

# ────────────────────────── «кипрская» логика Kp ──────────────────────────
def _kp_cyprus_like() -> Tuple[Optional[float], str, Optional[int]]:
    """
    Возвращает (kp, status, age_minutes) как в кипрском/мировом посте:
    последний закрытый 3ч бар SWPC. Если старше 9 часов — н/д.
    """
    try:
        # совместимость: у get_kp может не быть аргументов
        try:
            kp_tuple = get_kp(source="global")  # если поддерживается
        except TypeError:
            kp_tuple = get_kp()
    except Exception:
        return None, "н/д", None

    # ожидаем (kp, status, ts, src), но распакуем бережно
    kp = kp_tuple[0] if isinstance(kp_tuple, (list, tuple)) and len(kp_tuple) > 0 else None
    status = kp_tuple[1] if isinstance(kp_tuple, (list, tuple)) and len(kp_tuple) > 1 else "н/д"
    ts = kp_tuple[2] if isinstance(kp_tuple, (list, tuple)) and len(kp_tuple) > 2 else None

    age_min = None
    try:
        if isinstance(ts, int):
            age_min = int((pendulum.now("UTC").int_timestamp - ts) / 60)
            if age_min > 9 * 60:   # старше 9ч — считаем н/д
                return None, "н/д", None
    except Exception:
        pass
    try:
        if isinstance(kp, (int, float)):
            kp = float(kp)
            if kp < 0 or kp > 9:
                kp = max(0.0, min(9.0, kp))
    except Exception:
        kp = None
    return kp, status, age_min

# ────────────────────────── вывод / итоги ──────────────────────────
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

# ────────────────────────── вода / гидрик (краткая подсказка) ─────────────────
def _wetsuit_hint(sst: Optional[float]) -> Optional[str]:
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

# ────────────────────────── Safecast и радиация ──────────────────────────
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
        if parts: lines.append(f"🧪 Safecast: {em} {lbl} · " + " | ".join(parts))
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

# ────────────────────────── вечерний/утренний рендереры ─────────────────────
def _sunset_from_weather(wm: Dict[str, Any], tz: pendulum.Timezone, offset_days: int = 0) -> Optional[str]:
    daily = wm.get("daily") or {}
    times = daily.get("time") or []
    sunset = daily.get("sunset") or []
    try:
        target = pendulum.today(tz).add(days=offset_days).date()
        for t, s in zip(times, sunset):
            if not t or not s: continue
            if pendulum.parse(str(t)).date() == target:
                return pendulum.parse(str(s)).in_tz(tz).format("HH:mm")
    except Exception:
        return None
    return None

def _top_warm_cold(other_cities, tz_name: str) -> Tuple[Optional[Tuple[str,int,int]], Optional[Tuple[str,int,int]]]:
    temps: List[Tuple[str,int,int]] = []
    for city, (la, lo) in other_cities:
        wm = get_weather(la, lo) or {}
        daily = wm.get("daily") or {}
        times = _daily_times(wm)
        try:
            idx = times.index(pendulum.today(tz_name).date())
        except ValueError:
            idx = None
        if idx is None: continue
        tmax_a = daily.get("temperature_2m_max") or []
        tmin_a = daily.get("temperature_2m_min") or []
        try:
            tmax = int(round(float(tmax_a[idx])))
            tmin = int(round(float(tmin_a[idx] if idx<len(tmin_a) else tmax)))
            temps.append((city, tmax, tmin))
        except Exception:
            continue
    if not temps: return None, None
    warm = max(temps, key=lambda x: x[1])
    cold = min(temps, key=lambda x: x[1])
    return warm, cold

def _wetsuit_today_hint() -> Optional[str]:
    try:
        sst = get_sst(KLD_LAT, KLD_LON)
        return _wetsuit_hint(float(sst)) if isinstance(sst,(int,float)) else None
    except Exception:
        return None

def _fx_line_morning(tz: pendulum.Timezone) -> Optional[str]:
    """Курсы (утро): USD/EUR/CNY, символ рубля — \u20BD. Показываем, если fx.get_rates вернулось."""
    try:
        import importlib
        fx = importlib.import_module("fx")
        base_date = pendulum.today(tz)
        rates = fx.get_rates(date=base_date, tz=tz)  # type: ignore[attr-defined]
    except Exception as e:
        logging.info("FX not available: %s", e)
        return None
    if not isinstance(rates, dict): return None

    def token(code: str, name: str) -> Optional[str]:
        r = rates.get(code) or {}
        v = r.get("value"); d = r.get("delta")
        try:
            vs = f"{float(v):.2f}"
            ds = f"{float(d):.2f}" if d is not None else "0.00"
            # знак минуса — узкий (−)
            if ds.startswith("-"):
                ds = "−" + ds[1:]
            return f"{name} {vs} {RUBLE} ({ds})"
        except Exception:
            return None

    parts = [token("USD","USD"), token("EUR","EUR"), token("CNY","CNY")]
    parts = [p for p in parts if p]
    if not parts:
        return None
    return "💱 Курсы (утро): " + " • ".join(parts)

def _air_line(air: Dict[str, Any]) -> str:
    lvl = air.get("lvl", "н/д")
    return f"🏭 Воздух: {AIR_EMOJI.get(lvl,'⚪')} {lvl} (AQI {air.get('aqi','н/д')}) • PM₂.₅ {air.get('pm25','н/д')} / PM₁₀ {air.get('pm10','н/д')}"

def _space_lines_cyprus_like(tz: pendulum.Timezone) -> List[str]:
    kp_val, kp_status, age_min = _kp_cyprus_like()
    age_txt = ""
    if isinstance(age_min, int):
        age_txt = f", 🕓 {age_min // 60}ч назад" if age_min > 180 else f", 🕓 {age_min} мин назад"
    kp_chunk = f"Кр {kp_val:.1f} ({kp_status}{age_txt})" if isinstance(kp_val,(int,float)) else "Кр н/д"

    sw = get_solar_wind() or {}
    bz, bt, v, n = sw.get("bz"), sw.get("bt"), sw.get("speed_kms"), sw.get("density")
    wind_status = sw.get("status", "н/д")
    parts=[]
    if isinstance(v,(int,float)):  parts.append(f"v {v:.0f} км/с")
    if isinstance(n,(int,float)):  parts.append(f"n {n:.1f} см⁻³")
    sw_chunk = (" • 🌬️ " + ", ".join(parts) + f" — {wind_status}") if parts else ""
    return [f"🧲 Космопогода: {kp_chunk}{sw_chunk}"]

def _itogo_line(air: Dict[str, Any], storm: Dict[str, Any]) -> str:
    # воздух — только смайлик по уровню
    lvl = air.get("lvl", "н/д")
    air_short = "🟢" if lvl in ("хороший","низкий") else ("🟡" if lvl in ("умеренный",) else ("🟠" if lvl in ("высокий",) else "⚪"))
    storm_short = "без шторма" if not storm.get("warning") else "шторм"
    kp_val, kp_status, _ = _kp_cyprus_like()
    kp_short = kp_status if isinstance(kp_val,(int,float)) else "н/д"
    return f"🔎 Итого: воздух {air_short} • {storm_short} • Кр {kp_short}"

def _tips_one_liner(theme: str) -> str:
    tips = safe_tips(theme)
    return "✅ Сегодня: " + "; ".join(tips[:3])

# ────────────────────────── Компактный утренний макет ───────────────────────
def build_message_morning_compact(
    region_name: str,
    sea_label: str, sea_cities,
    other_label: str, other_cities,
    tz: Union[pendulum.Timezone, str],
) -> str:
    tz_obj = _as_tz(tz)
    tz_name = tz_obj.name
    base = pendulum.today(tz_obj).add(days=DAY_OFFSET)

    P: List[str] = []
    hdr_when = "на сегодня" if DAY_OFFSET == 0 else "на завтра"
    P.append(f"🟧 <b>{region_name}: погода {hdr_when} ({base.format('DD.MM.YYYY')})</b>")

    # — шапка: Калининград
    wm_klg = get_weather(KLD_LAT, KLD_LON) or {}
    t_day_max = t_night_min = wc = None
    # дневные температуры
    daily = wm_klg.get("daily") or {}
    times = _daily_times(wm_klg)
    try:
        idx = times.index(base.date())
    except ValueError:
        idx = None
    if idx is not None:
        try: t_day_max = float((daily.get("temperature_2m_max") or [None])[idx])
        except Exception: pass
        try: t_night_min = float((daily.get("temperature_2m_min") or [None])[idx])
        except Exception: pass
        try: wc = int((daily.get("weathercode") or [None])[idx])
        except Exception: pass

    wind_ms, wind_dir_deg, press_val, press_trend = pick_header_metrics_for_offset(wm_klg, tz_obj, DAY_OFFSET)
    gust = storm_flags_for_offset(wm_klg, tz_obj, DAY_OFFSET).get("max_gust_ms")
    desc = code_desc(wc)

    tday_i   = int(round(t_day_max))   if isinstance(t_day_max,(int,float)) else None
    tnight_i = int(round(t_night_min)) if isinstance(t_night_min,(int,float)) else None
    kal_temp = f"{tday_i}/{tnight_i}{NBSP}°C" if (tday_i is not None and tnight_i is not None) else "н/д"
    wind_part = (
        f"💨 {wind_ms:.1f} м/с ({compass(wind_dir_deg)})" if isinstance(wind_ms,(int,float)) and wind_dir_deg is not None
        else (f"💨 {wind_ms:.1f} м/с" if isinstance(wind_ms,(int,float)) else "💨 н/д")
    )
    if isinstance(gust,(int,float)):
        wind_part += f" • порывы — {int(round(gust))}"
    press_part = f"🔹 {press_val} гПа {press_trend}" if isinstance(press_val,int) else "🔹 н/д"

    header = f"Доброе утро 🏙️ Калининград — {kal_temp} • {desc or 'н/д'} • {wind_part} • {press_part}."
    P.append(header)

    # Погреться/остыть, гидрик
    warm, cold = _top_warm_cold(other_cities, tz_name)
    if warm or cold or _wetsuit_today_hint():
        parts=[]
        if warm: parts.append(f"Погреться: {warm[0]} {warm[1]}/{warm[2]}{NBSP}°C")
        if cold: parts.append(f"остыть: {cold[0]} {cold[1]}/{cold[2]}{NBSP}°C")
        ws = _wetsuit_today_hint()
        if ws: parts.append(f"Море: {ws}")
        if parts: P.append("; ".join(parts) + ".")

    P.append("—")

    # Нижний «кипрский» блок
    sunset = _sunset_from_weather(wm_klg, tz_obj, DAY_OFFSET) or "н/д"
    P.append(f"🏙️ Закат: {sunset}")

    # Курсы (утро) — если есть
    fx_line = _fx_line_morning(tz_obj)
    if fx_line:
        P.append(fx_line)

    # Воздух, Safecast, пыльца, радиация
    air = get_air(KLD_LAT, KLD_LON) if SHOW_AIR else {}
    if SHOW_AIR and air:
        P.append(_air_line(air))
        sc_lines = safecast_block_lines()
        if sc_lines:
            P.extend(sc_lines)
        if (p := get_pollen()):
            P.append(f"🌿 Пыльца: деревья {p['tree']} • травы {p['grass']} • сорняки {p['weed']} — риск {p['risk']}")
        if (rl := radiation_line(KLD_LAT, KLD_LON)):
            P.append(rl)

    # УФ (если ≥3)
    uvi_line = uv_line_if_needed(wm_klg, tz_obj, DAY_OFFSET)
    if uvi_line:
        P.append(uvi_line)

    # Космопогода (Кр, SW) — как в Кипре
    if SHOW_SPACE:
        P.extend(_space_lines_cyprus_like(tz_obj))

    # Итоги
    storm = storm_flags_for_offset(wm_klg, tz_obj, DAY_OFFSET)
    P.append(_itogo_line(air or {}, storm))

    # Советы — одной строкой
    theme = (
        "плохая погода" if storm.get("warning") else
        ("магнитные бури" if (_kp_cyprus_like()[0] and _kp_cyprus_like()[0] >= 5) else
         ("плохой воздух" if (_is_air_bad(air or {})[0]) else "здоровый день"))
    )
    P.append(_tips_one_liner(theme))

    P.append("—")
    P.append(f"📚 {get_fact(base, region_name)}")
    return "\n".join(P)

# ────────────────────────── Полный вечерний (как раньше) ────────────────────
def build_message(
    region_name: str,
    sea_label: str, sea_cities,
    other_label: str, other_cities,
    tz: Union[pendulum.Timezone, str]
) -> str:
    # Для утреннего — компактный «кипрский» макет
    if POST_MODE == "morning" or DAY_OFFSET == 0:
        return build_message_morning_compact(region_name, sea_label, sea_cities, other_label, other_cities, tz)

    # Иначе — оставляем стандартный расширенный (вечерний анонс)
    tz_obj = _as_tz(tz)
    tz_name = tz_obj.name

    P: List[str] = []
    base = pendulum.today(tz_obj).add(days=DAY_OFFSET)
    hdr_when = "на завтра"
    P.append(f"<b>🌅 {region_name}: погода {hdr_when} ({base.format('DD.MM.YYYY')})</b>")

    # Калининград (шапка)
    wm_klg = get_weather(KLD_LAT, KLD_LON) or {}
    storm  = storm_flags_for_offset(wm_klg, tz_obj, DAY_OFFSET)

    t_day_max = t_night_min = wc = None
    daily = wm_klg.get("daily") or {}
    times = _daily_times(wm_klg)
    try:
        idx = times.index(base.date())
    except ValueError:
        idx = None
    if idx is not None:
        try: t_day_max = float((daily.get("temperature_2m_max") or [None])[idx])
        except Exception: pass
        try: t_night_min = float((daily.get("temperature_2m_min") or [None])[idx])
        except Exception: pass
        try: wc = int((daily.get("weathercode") or [None])[idx])
        except Exception: pass

    wind_ms, wind_dir_deg, press_val, press_trend = pick_header_metrics_for_offset(wm_klg, tz_obj, DAY_OFFSET)
    gust = storm.get("max_gust_ms")
    desc = code_desc(wc)

    tday_i   = int(round(t_day_max))   if isinstance(t_day_max,(int,float)) else None
    tnight_i = int(round(t_night_min)) if isinstance(t_night_min,(int,float)) else None
    kal_temp = f"{tday_i}/{tnight_i}{NBSP}°C" if (tday_i is not None and tnight_i is not None) else "н/д"

    wind_part = (
        f"💨 {wind_ms:.1f} м/с ({compass(wind_dir_deg)})" if isinstance(wind_ms,(int,float)) and wind_dir_deg is not None
        else (f"💨 {wind_ms:.1f} м/с" if isinstance(wind_ms,(int,float)) else "💨 н/д")
    )
    if isinstance(gust,(int,float)):
        wind_part += f" • порывы — {int(round(gust))}"
    press_part = f"{press_val} гПа {press_trend}" if isinstance(press_val,int) else "н/д"

    kal_parts = [
        f"🏙️ Калининград: дн/ночь {kal_temp}",
        desc or None,
        wind_part,
        f"🔹 {press_part}",
    ]
    P.append(" • ".join([x for x in kal_parts if x]))
    P.append("———")

    if storm.get("warning"):
        P.append(storm["warning_text"])
        P.append("———")

    # Морские города
    temps_sea: Dict[str, Tuple[float, float, int, float | None]] = {}
    sea_lookup: Dict[str, Tuple[float, float]] = {}
    for city, (la, lo) in sea_cities:
        sea_lookup[city] = (la, lo)
        wm = get_weather(la, lo) or {}
        daily = wm.get("daily") or {}
        times = _daily_times(wm)
        try:
            idx = times.index(base.date())
        except ValueError:
            idx = None
        if idx is None: continue
        try: tmax = float((daily.get("temperature_2m_max") or [None])[idx])
        except Exception: tmax = None
        try: tmin = float((daily.get("temperature_2m_min") or [None])[idx])
        except Exception: tmin = None
        try: wcx = int((daily.get("weathercode") or [None])[idx])
        except Exception: wcx = 0
        temps_sea[city] = (tmax or 0, tmin or (tmax or 0), wcx, get_sst(la, lo))

    if temps_sea:
        P.append(f"🌊 <b>{sea_label}</b>")
        medals = ["🥵","😊","🙄","😮‍💨"]
        for i, (city, (d, n, wcx, sst_c)) in enumerate(sorted(temps_sea.items(), key=lambda kv: kv[1][0], reverse=True)[:5]):
            d_i, n_i = int(round(d)), int(round(n))
            medal = medals[i] if i < len(medals) else "•"
            line = f"{medal} {city}: {d_i}/{n_i}{NBSP}°C"
            descx = code_desc(wcx)
            if descx: line += f" {descx}"
            if sst_c is not None: line += f" 🌊 {int(round(sst_c))}{NBSP}°C"
            P.append(line)
        P.append("———")

    # Континентальные тёплые/холодные
    temps_oth: Dict[str, Tuple[float, float, int]] = {}
    for city, (la, lo) in other_cities:
        wm = get_weather(la, lo) or {}
        daily = wm.get("daily") or {}
        times = _daily_times(wm)
        try:
            idx = times.index(base.date())
        except ValueError:
            idx = None
        if idx is None: continue
        try: tmax = float((daily.get("temperature_2m_max") or [None])[idx])
        except Exception: tmax = None
        try: tmin = float((daily.get("temperature_2m_min") or [None])[idx])
        except Exception: tmin = None
        try: wcx = int((daily.get("weathercode") or [None])[idx])
        except Exception: wcx = 0
        if tmax is not None:
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

    # Уточняющие блоки (вечер)
    if SHOW_AIR:
        P.append("🏭 <b>Качество воздуха</b>")
        air = get_air(KLD_LAT, KLD_LON) or {}
        lvl = air.get("lvl", "н/д")
        P.append(f"{AIR_EMOJI.get(lvl,'⚪')} {lvl} (AQI {air.get('aqi','н/д')}) | PM₂.₅: {pm_color(air.get('pm25'))} | PM₁₀: {pm_color(air.get('pm10'))}")
        sc = safecast_block_lines()
        if sc: P.extend(sc)
        if (p := get_pollen()):
            P.append("🌿 <b>Пыльца</b>")
            P.append(f"Деревья: {p['tree']} | Травы: {p['grass']} | Сорняки: {p['weed']} — риск {p['risk']}")
        if (rl := radiation_line(KLD_LAT, KLD_LON)):
            P.append(rl)
        P.append("———")
    else:
        air = {}

    if SHOW_SPACE:
        kp_val, kp_status, age_min = _kp_cyprus_like()
        age_txt = ""
        if isinstance(age_min,int):
            age_txt = f", 🕓 {age_min // 60}ч назад" if age_min > 180 else f", 🕓 {age_min} мин назад"
        if isinstance(kp_val,(int,float)):
            P.append(f"🧲 Геомагнитка: Кр {kp_val:.1f} ({kp_status}{age_txt})")
        else:
            P.append("🧲 Геомагнитка: Кр н/д")

        sw = get_solar_wind() or {}
        bz, bt, v, n = sw.get("bz"), sw.get("bt"), sw.get("speed_kms"), sw.get("density")
        wind_status = sw.get("status", "н/д")
        parts=[]
        if isinstance(bz,(int,float)): parts.append(f"Bz {bz:.1f} nT")
        if isinstance(bt,(int,float)): parts.append(f"Bt {bt:.1f} nT")
        if isinstance(v,(int,float)):  parts.append(f"v {v:.0f} км/с")
        if isinstance(n,(int,float)):  parts.append(f"n {n:.1f} см⁻³")
        if parts: P.append("🌬️ Солнечный ветер: " + ", ".join(parts) + f" — {wind_status}")
        P.append("———")

    # Рекомендации
    P.append("✅ <b>Рекомендации</b>")
    theme = (
        "плохая погода" if storm.get("warning") else
        ("магнитные бури" if (SHOW_SPACE and _kp_cyprus_like()[0] and _kp_cyprus_like()[0] >= 5) else
         ("плохой воздух" if (SHOW_AIR and _is_air_bad(air)[0]) else "здоровый день"))
    )
    for t in safe_tips(theme):
        P.append(t)

    P.append("———")
    P.append(f"📚 {get_fact(base, region_name)}")
    return "\n".join(P)

# ────────────────────────── отправка ──────────────────────────
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
    "pick_header_metrics_for_offset",
    "storm_flags_for_offset",
]
