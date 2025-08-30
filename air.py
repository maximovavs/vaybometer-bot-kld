#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
air.py
~~~~~~

• Источники качества воздуха:
  1) IQAir / nearest_city  (API key: AIRVISUAL_KEY)
  2) Open-Meteo Air-Quality (без ключа)

• merge_air_sources() — объединяет словари с приоритетом IQAir → Open-Mетео
• get_air(lat, lon)      — {'lvl','aqi','pm25','pm10','src','src_emoji','src_icon'}
• get_sst(lat, lon)      — Sea Surface Temperature (по ближайшему часу)
• get_kp()               — индекс Kp (последний замер) с кешем (возвращает и «свежесть»)
• get_solar_wind()       — Bz, Bt, скорость и плотность солнечного ветра + «статус»

Особенности:
- Open-Meteo: берём значения по ближайшему прошедшему часу (UTC).
- SST: то же правило ближайшего часа.
- Kp: парсим ПОСЛЕДНЕЕ значение из обоих эндпоинтов SWPC; кэш валиден 3 ч (раньше было 6).
- Возвращаем источник AQI:
    'src' ∈ {'iqair','openmeteo','n/d'},
    'src_emoji' ∈ {'📡','🛰','⚪'},
    'src_icon'  ∈ {'📡 IQAir','🛰 OM','⚪ н/д'}.
"""

from __future__ import annotations
import os
import time
import json
import math
import logging
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Union, List

from utils import _get  # HTTP-обёртка (_get_retry внутри)

__all__ = ("get_air", "get_sst", "get_kp", "get_solar_wind")

# ───────────────────────── Константы / кеш ─────────────────────────

AIR_KEY = os.getenv("AIRVISUAL_KEY")

CACHE_DIR = Path.home() / ".cache" / "vaybometer"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
KP_CACHE = CACHE_DIR / "kp.json"

KP_URLS = [
    "https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json",
    "https://services.swpc.noaa.gov/json/planetary_k_index_1m.json",
]

# DSCOVR (L1): SWPC прокси с усреднением по минутам
SW_URLS = [
    "https://services.swpc.noaa.gov/products/solar-wind/mag-1-day.json",   # Bt/Bz
    "https://services.swpc.noaa.gov/products/solar-wind/plasma-1-day.json" # v/n
]

SRC_EMOJI = {"iqair": "📡", "openmeteo": "🛰", "n/d": "⚪"}
SRC_ICON  = {"iqair": "📡 IQAir", "openmeteo": "🛰 OM", "n/d": "⚪ н/д"}

# ───────────────────────── Утилиты AQI/Kp ──────────────────────────

def _aqi_level(aqi: Union[int, float, str, None]) -> str:
    if aqi in (None, "н/д"):
        return "н/д"
    try:
        v = float(aqi)
    except (TypeError, ValueError):
        return "н/д"
    if v <= 50: return "хороший"
    if v <= 100: return "умеренный"
    if v <= 150: return "вредный"
    if v <= 200: return "оч. вредный"
    return "опасный"

def _kp_state(kp: float) -> str:
    if kp < 3.0: return "спокойно"
    if kp < 5.0: return "неспокойно"
    return "буря"

def _pick_nearest_hour(arr_time: List[str], arr_val: List[Any]) -> Optional[float]:
    if not arr_time or not arr_val or len(arr_time) != len(arr_val):
        return None
    try:
        now_iso = time.strftime("%Y-%m-%dT%H:00", time.gmtime())
        idxs = [i for i, t in enumerate(arr_time) if isinstance(t, str) and t <= now_iso]
        idx = max(idxs) if idxs else 0
        v = arr_val[idx]
        if not isinstance(v, (int, float)):
            return None
        v = float(v)
        return v if (math.isfinite(v) and v >= 0) else None
    except Exception:
        return None

# ───────────────────────── Источники AQI ───────────────────────────

def _src_iqair(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    if not AIR_KEY:
        return None
    try:
        resp = _get(
            "https://api.airvisual.com/v2/nearest_city",
            lat=lat, lon=lon, key=AIR_KEY,
        )
    except Exception as e:
        logging.warning("IQAir request error: %s", e)
        return None
    if not resp or "data" not in resp:
        return None
    try:
        pol = resp["data"]["current"].get("pollution", {}) or {}
        aqi_val  = pol.get("aqius")
        pm25_val = pol.get("p2")
        pm10_val = pol.get("p1")
        return {
            "aqi":  float(aqi_val)  if isinstance(aqi_val,  (int, float)) else None,
            "pm25": float(pm25_val) if isinstance(pm25_val, (int, float)) else None,
            "pm10": float(pm10_val) if isinstance(pm10_val, (int, float)) else None,
            "src": "iqair",
        }
    except Exception as e:
        logging.warning("IQAir parse error: %s", e)
        return None

def _src_openmeteo(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    try:
        resp = _get(
            "https://air-quality-api.open-meteo.com/v1/air-quality",
            latitude=lat, longitude=lon,
            hourly="pm10,pm2_5,us_aqi", timezone="UTC",
        )
    except Exception as e:
        logging.warning("Open-Meteo AQ request error: %s", e)
        return None
    if not resp or "hourly" not in resp:
        return None
    try:
        h = resp["hourly"]
        times = h.get("time", []) or []
        aqi_val  = _pick_nearest_hour(times, h.get("us_aqi", []) or [])
        pm25_val = _pick_nearest_hour(times, h.get("pm2_5", []) or [])
        pm10_val = _pick_nearest_hour(times, h.get("pm10", [])  or [])
        aqi_norm: Union[float, str] = float(aqi_val)  if isinstance(aqi_val,  (int, float)) and math.isfinite(aqi_val)  and aqi_val  >= 0 else "н/д"
        pm25_norm = float(pm25_val) if isinstance(pm25_val, (int, float)) and math.isfinite(pm25_val) and pm25_val >= 0 else None
        pm10_norm = float(pm10_val) if isinstance(pm10_val, (int, float)) and math.isfinite(pm10_val) and pm10_val >= 0 else None
        return {"aqi": aqi_norm, "pm25": pm25_norm, "pm10": pm10_norm, "src": "openmeteo"}
    except Exception as e:
        logging.warning("Open-Mетео AQ parse error: %s", e)
        return None

# ───────────────────────── Merge AQI ───────────────────────────────

def merge_air_sources(src1: Optional[Dict[str, Any]], src2: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Соединяет данные двух источников AQI (приоритет src1 → src2).
    Возвращает {'lvl','aqi','pm25','pm10','src','src_emoji','src_icon'}.
    """
    aqi_val: Union[float, str, None] = "н/д"
    src_tag: str = "n/d"

    # AQI источник
    for s in (src1, src2):
        if not s:
            continue
        v = s.get("aqi")
        if isinstance(v, (int, float)) and math.isfinite(v) and v >= 0:
            aqi_val = float(v)
            src_tag = s.get("src") or src_tag
            break

    # PM first-non-null
    pm25 = None
    pm10 = None
    for s in (src1, src2):
        if not s:
            continue
        if pm25 is None and isinstance(s.get("pm25"), (int, float)) and math.isfinite(s["pm25"]):
            pm25 = float(s["pm25"])
        if pm10 is None and isinstance(s.get("pm10"), (int, float)) and math.isfinite(s["pm10"]):
            pm10 = float(s["pm10"])

    lvl = _aqi_level(aqi_val)
    src_emoji = SRC_EMOJI.get(src_tag, SRC_EMOJI["n/d"])
    src_icon  = SRC_ICON.get(src_tag,  SRC_ICON["n/d"])

    return {
        "lvl": lvl,
        "aqi": aqi_val,
        "pm25": pm25,
        "pm10": pm10,
        "src": src_tag,
        "src_emoji": src_emoji,
        "src_icon": src_icon,
    }

def get_air(lat: float, lon: float) -> Dict[str, Any]:
    try:
        src1 = _src_iqair(lat, lon)
    except Exception:
        src1 = None
    try:
        src2 = _src_openmeteo(lat, lon)
    except Exception:
        src2 = None
    return merge_air_sources(src1, src2)

# ───────────────────────── SST (по ближайшему часу) ─────────────────

def get_sst(lat: float, lon: float) -> Optional[float]:
    try:
        resp = _get(
            "https://marine-api.open-meteo.com/v1/marine",
            latitude=lat, longitude=lon,
            hourly="sea_surface_temperature", timezone="UTC",
        )
    except Exception as e:
        logging.warning("Marine SST request error: %s", e)
        return None
    if not resp or "hourly" not in resp:
        return None
    try:
        h = resp["hourly"]
        times = h.get("time", []) or []
        vals  = h.get("sea_surface_temperature", []) or []
        v = _pick_nearest_hour(times, vals)
        return float(v) if isinstance(v, (int, float)) else None
    except Exception as e:
        logging.warning("Marine SST parse error: %s", e)
        return None

# ───────────────────────── Kp + кеш (TTL 3 ч, с меткой времени) ──────────────────────

def _load_kp_cache() -> Tuple[Optional[float], Optional[int], Optional[str], Optional[int]]:
    try:
        data = json.loads(KP_CACHE.read_text(encoding="utf-8"))
        return data.get("kp"), data.get("ts"), data.get("src"), data.get("obs_ts")
    except Exception:
        return None, None, None, None

def _save_kp_cache(kp: float, src: str, obs_ts: Optional[int]) -> None:
    try:
        KP_CACHE.write_text(json.dumps(
            {"kp": kp, "ts": int(time.time()), "src": src, "obs_ts": int(obs_ts) if obs_ts else None},
            ensure_ascii=False))
    except Exception as e:
        logging.warning("Kp cache write error: %s", e)

def _fetch_kp_data(url: str, attempts: int = 3, backoff: float = 2.0) -> Optional[Any]:
    for i in range(attempts):
        data = _get(url)
        if data:
            return data
        time.sleep(backoff ** i)
    return None

def _parse_kp_from_table(data: Any) -> Tuple[Optional[float], Optional[int]]:
    # services.swpc.noaa.gov/products/noaa-planetary-k-index.json (таблица)
    if not isinstance(data, list) or not data or not isinstance(data[0], list):
        return None, None
    for row in reversed(data[1:]):
        try:
            kp_val = float(str(row[-1]).replace(",", "."))
            t = str(row[0]).replace(" ", "T")  # "YYYY-MM-DD HH:MM:SS"
            obs_ts = int(time.mktime(time.strptime(t, "%Y-%m-%dT%H:%M:%S")))
            return kp_val, obs_ts
        except Exception:
            continue
    return None, None

def _parse_kp_from_dicts(data: Any) -> Tuple[Optional[float], Optional[int]]:
    # services.swpc.noaa.gov/json/planetary_k_index_1m.json (массив словарей)
    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        return None, None
    for item in reversed(data):
        raw = item.get("kp_index") or item.get("estimated_kp") or item.get("kp")
        t   = item.get("time_tag") or item.get("time_tag_updated") or item.get("time_tag_estimated")
        if raw is None:
            continue
        try:
            kp_val = float(str(raw).replace(",", "."))
            obs_ts = None
            if isinstance(t, str):
                t2 = t.split(".")[0].rstrip("Z")
                obs_ts = int(time.mktime(time.strptime(t2, "%Y-%m-%dT%H:%M:%S")))
            return kp_val, obs_ts
        except Exception:
            continue
    return None, None

def get_kp() -> Tuple[Optional[float], str, Optional[int], str]:
    """
    Возвращает (kp, state, obs_ts, src)
      kp: float | None
      state: 'спокойно'|'неспокойно'|'буря'|'н/д'
      obs_ts: Unix-время наблюдения (UTC) или None
      src: 'table'|'dict'|'cache'|'n/d'
    """
    for url in KP_URLS:
        data = _fetch_kp_data(url)
        logging.info("Kp fetch from %s -> %s", url, bool(data))
        if not data:
            continue
        try:
            if isinstance(data, list) and data:
                if isinstance(data[0], list):
                    kp_value, obs_ts = _parse_kp_from_table(data)
                    src = "table"
                else:
                    kp_value, obs_ts = _parse_kp_from_dicts(data)
                    src = "dict"
            else:
                kp_value, obs_ts, src = None, None, "n/d"
            if kp_value is None:
                raise ValueError("no parsable kp in response")
            _save_kp_cache(kp_value, src, obs_ts)
            return kp_value, _kp_state(kp_value), obs_ts, src
        except Exception as e:
            logging.warning("Kp parse error %s: %s", url, e)

    cached_kp, ts, src, obs_ts = _load_kp_cache()
    if cached_kp is not None and ts:
        age = int(time.time()) - int(ts)
        if age <= 3 * 60 * 60:  # кэш не старше 3 часов
            logging.info("Using cached Kp=%s age=%ss", cached_kp, age)
            return cached_kp, _kp_state(cached_kp), obs_ts, "cache"

    return None, "н/д", None, "n/d"

# ───────────────────────── Солнечный ветер (DSCOVR @L1) ────────────

def _avg_recent(rows: List[List[Any]], value_idx: int, minutes: int = 15) -> Tuple[Optional[float], Optional[int]]:
    """
    Берём последние ≤ minutes минут значений и усредняем.
    Возвращает (avg, last_ts_utc).
    """
    if not rows or not isinstance(rows[0], list):
        return None, None
    # rows: [ ["time_tag","bx_gsm","by_gsm","bz_gsm","bt"], ... ]
    recent = []
    last_ts = None
    now = int(time.time())
    cutoff = now - minutes * 60
    for r in rows[1:]:
        try:
            t = str(r[0]).replace(" ", "T").split(".")[0]  # "YYYY-MM-DDTHH:MM:SS"
            ts = int(time.mktime(time.strptime(t, "%Y-%m-%dT%H:%M:%S")))
            if ts >= cutoff:
                v = r[value_idx]
                if isinstance(v, (int, float)) and math.isfinite(v):
                    recent.append(float(v))
                last_ts = ts
        except Exception:
            continue
    if not recent:
        return None, last_ts
    return sum(recent) / len(recent), last_ts

def get_solar_wind() -> Dict[str, Any]:
    """
    Возвращает dict:
      {'bz': float|None, 'bt': float|None, 'speed_kms': float|None, 'density': float|None,
       'status': 'спокойно'|'умеренно'|'напряжённо'|'буря-потенц', 'ts': int|None}
    """
    bz = bt = spd = den = None
    ts1 = ts2 = None

    try:
        mag = _get(SW_URLS[0])  # MAG: Bt/Bz
        if isinstance(mag, list) and mag:
            # header: ["time_tag","bx_gsm","by_gsm","bz_gsm","bt"]
            bz, ts1 = _avg_recent(mag, 3, minutes=15)
            bt, _   = _avg_recent(mag, 4, minutes=15)
    except Exception as e:
        logging.warning("Solar wind MAG error: %s", e)

    try:
        pls = _get(SW_URLS[1])  # Plasma: speed/density
        if isinstance(pls, list) and pls:
            # header: ["time_tag","density","speed","temperature"]
            den, ts2 = _avg_recent(pls, 1, minutes=15)
            spd, _   = _avg_recent(pls, 2, minutes=15)
    except Exception as e:
        logging.warning("Solar wind PLASMA error: %s", e)

    ts = ts1 or ts2

    # Простая эвристика статуса
    status = "н/д"
    if all(v is None for v in (bz, bt, spd, den)):
        status = "н/д"
    else:
        south = (bz is not None and bz <= -5.0) or (bz is not None and bz <= -3.0 and bt and bt >= 6.0)
        fast  = (spd is not None and spd >= 600)
        dense = (den is not None and den >= 10)
        if south and (fast or dense):
            status = "буря-потенц"
        elif south or fast or dense:
            status = "напряжённо"
        else:
            status = "спокойно"

    return {
        "bz": bz, "bt": bt, "speed_kms": spd, "density": den,
        "status": status, "ts": ts
    }

# ───────────────────────── CLI ─────────────────────────────────────

if __name__ == "__main__":
    from pprint import pprint
    print("=== Пример get_air (Калининград) ===")
    pprint(get_air(54.710426, 20.452214))
    print("\n=== Пример get_sst (Калининград) ===")
    print(get_sst(54.710426, 20.452214))
    print("\n=== Пример get_kp ===")
    print(get_kp())
    print("\n=== Пример get_solar_wind ===")
    pprint(get_solar_wind())