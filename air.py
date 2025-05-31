#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
air.py
~~~~~~

• Два источника качества воздуха:
  1) IQAir / nearest_city  (API key AIRVISUAL_KEY)
  2) Open-Meteo Air-Quality (без ключа)

• merge_air_sources() — объединяет словари, приоритет IQAir → Open-Meteo
• get_air() — возвращает dict {'lvl','aqi','pm25','pm10'}
• get_sst() — текущая температура поверхности моря (SST)
• get_kp() — текущий индекс Kp с retry, кешем и двумя проверенными endpoint’ами
"""
from __future__ import annotations
import os
import logging
import time
import json
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, Union

from utils import _get

# ────────── Константы ───────────────────────────────────────────────
LAT, LON = 34.707, 33.022  # Limassol
AIR_KEY = os.getenv("AIRVISUAL_KEY")

# Путь для кеша Kp
CACHE_DIR = Path.home() / ".cache" / "vaybometer"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
KP_CACHE = CACHE_DIR / "kp.json"

# Endpoint’ы для получения Kp
KP_URLS = [
    # Суточный planetary K-index
    "https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json",
    # Моментальный 1m K-index
    "https://services.swpc.noaa.gov/json/planetary_k_index_1m.json"
]

# ────────── Утилиты ─────────────────────────────────────────────────
def _aqi_level(aqi: float | int | str) -> str:
    if aqi in ("н/д", None):
        return "н/д"
    aqi = float(aqi)
    if aqi <= 50: return "хороший"
    if aqi <= 100: return "умеренный"
    if aqi <= 150: return "вредный"
    if aqi <= 200: return "оч. вредный"
    return "опасный"

def _kp_state(kp: float) -> str:
    if kp < 3: return "спокойно"
    if kp < 5: return "неспокойно"
    return "буря"

# ────────── Источники качества воздуха ──────────────────────────────
def _src_iqair() -> Optional[Dict[str, Any]]:
    if not AIR_KEY:
        return None
    j = _get("https://api.airvisual.com/v2/nearest_city", lat=LAT, lon=LON, key=AIR_KEY)
    if not j or "data" not in j:
        return None
    try:
        p = j["data"]["current"]["pollution"]
        return {"aqi": p.get("aqius", "н/д"), "pm25": p.get("p2"), "pm10": p.get("p1")}  # type: ignore
    except Exception as e:
        logging.warning("IQAir parse error: %s", e)
        return None

def _src_openmeteo() -> Optional[Dict[str, Any]]:
    j = _get(
        "https://air-quality-api.open-meteo.com/v1/air-quality",
        latitude=LAT, longitude=LON,
        hourly="pm10,pm2_5,us_aqi", timezone="UTC"
    )
    if not j or "hourly" not in j:
        return None
    try:
        h = j["hourly"]
        aqi = h["us_aqi"][0] if h["us_aqi"] else "н/д"
        pm25 = h["pm2_5"][0] if h["pm2_5"] else None
        pm10 = h["pm10"][0] if h["pm10"] else None
        # Приводим к норме
        aqi = aqi if isinstance(aqi, (int, float)) and aqi >= 0 else "н/д"
        pm25 = pm25 if isinstance(pm25, (int, float)) and pm25 >= 0 else None
        pm10 = pm10 if isinstance(pm10, (int, float)) and pm10 >= 0 else None
        return {"aqi": aqi, "pm25": pm25, "pm10": pm10}
    except Exception as e:
        logging.warning("Open-Meteo AQ parse error: %s", e)
        return None

def merge_air_sources(src1: Optional[Dict[str, Any]], src2: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    base: Dict[str, Union[str, float, None]] = {"aqi": "н/д", "pm25": None, "pm10": None}
    for k in ("aqi", "pm25", "pm10"):  # type: ignore
        v1 = src1.get(k) if src1 else None
        if v1 not in (None, "н/д"):
            base[k] = v1  # type: ignore
        else:
            v2 = src2.get(k) if src2 else None
            base[k] = v2 if v2 not in (None, "н/д") else base[k]
    base["lvl"] = _aqi_level(base["aqi"])  # type: ignore
    return base  # type: ignore

def get_air() -> Dict[str, Any]:
    return merge_air_sources(_src_iqair(), _src_openmeteo())

# ────────── Sea Surface Temperature ─────────────────────────────────
def get_sst() -> Optional[float]:
    j = _get(
        "https://api.open-meteo.com/v1/forecast",
        latitude=LAT, longitude=LON,
        hourly="soil_temperature_0cm", timezone="UTC",
        cell_selection="sea"
    )
    if not j or "hourly" not in j:
        return None
    try:
        arr = j["hourly"].get("soil_temperature_0cm", [])
        val = arr[0] if arr else None
        return float(val) if isinstance(val, (int, float)) else None
    except Exception as e:
        logging.warning("SST parse error: %s", e)
        return None

# ────────── Kp-индекс с retry и кешем ──────────────────────────────
def _load_kp_cache() -> Tuple[Optional[float], Optional[int]]:
    try:
        d = json.loads(KP_CACHE.read_text())
        return d.get("kp"), d.get("ts")
    except Exception:
        return None, None

def _save_kp_cache(kp: float) -> None:
    KP_CACHE.write_text(json.dumps({"kp": kp, "ts": int(time.time())}, ensure_ascii=False))

def _fetch_kp_data(url: str, attempts: int = 3, backoff: float = 2.0) -> Optional[Any]:
    for i in range(attempts):
        data = _get(url)
        if data:
            return data
        time.sleep(backoff ** i)
    return None

def get_kp() -> Tuple[Optional[float], str]:
    """
    Возвращает (kp_value, state)
    Пробует два endpoint’а с retry + backoff, парсит суточный или минутный Kp,
    кеширует последний успешный, использует кеш при падении обоих.
    """
    # 1) Попытка по порядку
    for url in KP_URLS:
        data = _fetch_kp_data(url)
        logging.info("Kp fetch from %s -> %s", url, bool(data))
        if not data:
            continue
        try:
            # Сначала обрабатываем лист
            raw_val = None
            if isinstance(data, list):
                # формат 1: [[...], [...]] suточный — вложенные списки
                if isinstance(data[0], list):
                    entry = data[1]
                    raw_val = entry[-1]
                # формат 2: [{...}, {...}] минутный — словари
                elif isinstance(data[0], dict):
                    entry = data[0]
                    raw_val = entry.get("kp_index") or entry.get("estimated_kp") or entry.get("kp")
            # Если не list — пропускаем
            if raw_val is None:
                raise ValueError("raw Kp not found")
            # Убираем возможные суффиксы, например 'Z'
            raw_str = str(raw_val).rstrip("Z").replace(",", ".")
            kp = float(raw_str)
            _save_kp_cache(kp)
            return kp, _kp_state(kp)
        except Exception as e:
            logging.warning("Kp parse error %s: %s", url, e)
    # 2) Фоллбэк: кеш
    kp_cached, ts = _load_kp_cache()
    if kp_cached is not None:
        logging.info("Using cached Kp=%s ts=%s", kp_cached, ts)
        return kp_cached, _kp_state(kp_cached)
    return None, "н/д"

# ────────── CLI-тестирование ────────────────────────────────────────
if __name__ == "__main__":
    from pprint import pprint
    print("Air:", end=" "); pprint(get_air())
    print("SST:", get_sst())
    print("Kp:", get_kp())
