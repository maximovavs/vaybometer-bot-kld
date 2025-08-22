#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
air.py
~~~~~~

• Источники качества воздуха:
  1) IQAir / nearest_city  (API key: AIRVISUAL_KEY)
  2) Open‑Meteo Air‑Quality (без ключа)

• merge_air_sources() — объединяет словари с приоритетом IQAir → Open‑Meteo
• get_air(lat, lon)      — {'lvl','aqi','pm25','pm10','src','src_emoji','src_icon'}
• get_sst(lat, lon)      — Sea Surface Temperature (по ближайшему часу)
• get_kp()               — индекс Kp (последний замер) с кешем (TTL 6 ч)

Особенности:
- Open‑Meteo: берём значения по ближайшему прошедшему часу (UTC).
- SST: то же правило ближайшего часа.
- Kp: парсим ПОСЛЕДНЕЕ значение из обоих эндпоинтов SWPC; кэш валиден 6 часов.
- NEW: Возвращаем источник AQI:
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

__all__ = ("get_air", "get_sst", "get_kp")

# ───────────────────────── Константы / кеш ─────────────────────────

AIR_KEY = os.getenv("AIRVISUAL_KEY")

CACHE_DIR = Path.home() / ".cache" / "vaybometer"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
KP_CACHE = CACHE_DIR / "kp.json"

KP_URLS = [
    "https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json",
    "https://services.swpc.noaa.gov/json/planetary_k_index_1m.json",
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
        logging.warning("Open‑Meteo AQ request error: %s", e)
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
        logging.warning("Open‑Meteo AQ parse error: %s", e)
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

# ───────────────────────── Kp + кеш (TTL 6 ч) ──────────────────────

def _load_kp_cache() -> Tuple[Optional[float], Optional[int]]:
    try:
        data = json.loads(KP_CACHE.read_text(encoding="utf-8"))
        return data.get("kp"), data.get("ts")
    except Exception:
        return None, None

def _save_kp_cache(kp: float) -> None:
    try:
        KP_CACHE.write_text(json.dumps({"kp": kp, "ts": int(time.time())}, ensure_ascii=False))
    except Exception as e:
        logging.warning("Kp cache write error: %s", e)

def _fetch_kp_data(url: str, attempts: int = 3, backoff: float = 2.0) -> Optional[Any]:
    for i in range(attempts):
        data = _get(url)
        if data:
            return data
        time.sleep(backoff ** i)
    return None

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

def get_kp() -> Tuple[Optional[float], str]:
    for url in KP_URLS:
        data = _fetch_kp_data(url)
        logging.info("Kp fetch from %s -> %s", url, bool(data))
        if not data:
            continue
        try:
            if isinstance(data, list) and data:
                kp_value = _parse_kp_from_table(data) if isinstance(data[0], list) else _parse_kp_from_dicts(data)
            else:
                kp_value = None
            if kp_value is None:
                raise ValueError("no parsable kp in response")
            _save_kp_cache(kp_value)
            return kp_value, _kp_state(kp_value)
        except Exception as e:
            logging.warning("Kp parse error %s: %s", url, e)

    cached_kp, ts = _load_kp_cache()
    if cached_kp is not None and ts:
        age = int(time.time()) - int(ts)
        if age <= 6 * 60 * 60:
            logging.info("Using cached Kp=%s age=%ss", cached_kp, age)
            return cached_kp, _kp_state(cached_kp)

    return None, "н/д"

# ───────────────────────── CLI ─────────────────────────────────────

if __name__ == "__main__":
    from pprint import pprint
    print("=== Пример get_air (Калининград) ===")
    pprint(get_air(54.710426, 20.452214))
    print("\n=== Пример get_sst (Калининград) ===")
    print(get_sst(54.710426, 20.452214))
    print("\n=== Пример get_kp ===")
    print(get_kp())