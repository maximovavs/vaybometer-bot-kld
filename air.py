#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
air.py
~~~~~~

• Источники качества воздуха:
  1) IQAir / nearest_city  (API key: AIRVISUAL_KEY)
  2) Open‑Meteo Air‑Quality (без ключа)

• merge_air_sources() — объединяет словари с приоритетом IQAir → Open‑Meteo
• get_air(lat, lon)      — {'lvl','aqi','pm25','pm10'}
• get_sst(lat, lon)      — Sea Surface Temperature (по ближайшему часу)
• get_kp()               — индекс Kp с retry + кешем

Изменения vs. базовая версия:
- Берём значения Open‑Meteo **по ближайшему прошедшему часу (UTC)**, а не первый элемент массива.
- SST считаем так же — по ближайшему часу.
- Аккуратный merge источников и нормализация значений.
"""

from __future__ import annotations
import os
import time
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Union, List

from utils import _get  # HTTP-обёртка (_get_retry внутри)

# ───────────────────────── Константы / кеш ─────────────────────────

AIR_KEY = os.getenv("AIRVISUAL_KEY")

CACHE_DIR = Path.home() / ".cache" / "vaybometer"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
KP_CACHE = CACHE_DIR / "kp.json"

KP_URLS = [
    # Суточный planetary K-index
    "https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json",
    # Моментальный (1 мин) K-index
    "https://services.swpc.noaa.gov/json/planetary_k_index_1m.json",
]

# ───────────────────────── Утилиты AQI/Kp ──────────────────────────

def _aqi_level(aqi: Union[int, float, str, None]) -> str:
    """
    Числовой AQI → текстовая категория (наша локальная шкала).
    """
    if aqi in (None, "н/д"):
        return "н/д"
    try:
        v = float(aqi)
    except (TypeError, ValueError):
        return "н/д"
    if v <= 50:
        return "хороший"
    if v <= 100:
        return "умеренный"
    if v <= 150:
        return "вредный"
    if v <= 200:
        return "оч. вредный"
    return "опасный"


def _kp_state(kp: float) -> str:
    if kp < 3.0:
        return "спокойно"
    if kp < 5.0:
        return "неспокойно"
    return "буря"


def _pick_nearest_hour(arr_time: List[str], arr_val: List[Any]) -> Optional[float]:
    """
    Возвращает значение из массивов Open‑Meteo, соответствующее ближайшему
    прошедшему часу относительно текущего UTC. Если подходящего индекса нет —
    берём нулевой элемент. Некорректные значения → None.

    Ожидаемый формат времени: 'YYYY-MM-DDTHH:00'.
    """
    if not arr_time or not arr_val or len(arr_time) != len(arr_val):
        return None
    try:
        now_iso = time.strftime("%Y-%m-%dT%H:00", time.gmtime())
        idxs = [i for i, t in enumerate(arr_time) if isinstance(t, str) and t <= now_iso]
        idx = max(idxs) if idxs else 0
        v = arr_val[idx]
        return float(v) if isinstance(v, (int, float)) else None
    except Exception:
        return None

# ───────────────────────── Источники AQI ───────────────────────────

def _src_iqair(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    """
    IQAir nearest_city: {'aqi','pm25','pm10'} (в AQI US, PM — µg/m³).
    """
    if not AIR_KEY:
        return None
    try:
        resp = _get(
            "https://api.airvisual.com/v2/nearest_city",
            lat=lat,
            lon=lon,
            key=AIR_KEY,
        )
    except Exception as e:
        logging.warning("IQAir request error: %s", e)
        return None
    if not resp or "data" not in resp:
        return None
    try:
        pol = resp["data"]["current"]["pollution"]
        aqi_val = pol.get("aqius")
        pm25_val = pol.get("p2")
        pm10_val = pol.get("p1")
        return {
            "aqi": float(aqi_val) if aqi_val is not None else None,
            "pm25": float(pm25_val) if pm25_val is not None else None,
            "pm10": float(pm10_val) if pm10_val is not None else None,
            "src": "iqair",
        }
    except Exception as e:
        logging.warning("IQAir parse error: %s", e)
        return None


def _src_openmeteo(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    """
    Open‑Meteo Air‑Quality: {'aqi','pm25','pm10'} (us_aqi, pm2_5, pm10).
    Берём значения за ближайший прошедший час.
    """
    try:
        resp = _get(
            "https://air-quality-api.open-meteo.com/v1/air-quality",
            latitude=lat,
            longitude=lon,
            hourly="pm10,pm2_5,us_aqi",
            timezone="UTC",
        )
    except Exception as e:
        logging.warning("Open‑Meteo AQ request error: %s", e)
        return None
    if not resp or "hourly" not in resp:
        return None

    try:
        h = resp["hourly"]
        times = h.get("time", []) or []
        aqi_val = _pick_nearest_hour(times, h.get("us_aqi", []) or [])
        pm25_val = _pick_nearest_hour(times, h.get("pm2_5", []) or [])
        pm10_val = _pick_nearest_hour(times, h.get("pm10", []) or [])

        aqi_norm: Union[float, str] = float(aqi_val) if isinstance(aqi_val, (int, float)) and aqi_val >= 0 else "н/д"
        pm25_norm = float(pm25_val) if isinstance(pm25_val, (int, float)) and pm25_val >= 0 else None
        pm10_norm = float(pm10_val) if isinstance(pm10_val, (int, float)) and pm10_val >= 0 else None

        return {"aqi": aqi_norm, "pm25": pm25_norm, "pm10": pm10_norm, "src": "openmeteo"}
    except Exception as e:
        logging.warning("Open‑Meteo AQ parse error: %s", e)
        return None

# ───────────────────────── Merge AQI ───────────────────────────────

def merge_air_sources(src1: Optional[Dict[str, Any]], src2: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Соединяет данные двух источников AQI (приоритет src1 → src2).
    Возвращает {'lvl','aqi','pm25','pm10'}.
    """
    base: Dict[str, Union[str, float, None]] = {"aqi": "н/д", "pm25": None, "pm10": None}
    for key in ("aqi", "pm25", "pm10"):
        v1 = src1.get(key) if src1 else None
        v2 = src2.get(key) if src2 else None
        base[key] = v1 if v1 not in (None, "н/д") else (v2 if v2 not in (None, "н/д") else base[key])
    base["lvl"] = _aqi_level(base["aqi"])  # type: ignore
    return base  # type: ignore

def get_air(lat: float, lon: float) -> Dict[str, Any]:
    """
    Обёртка: достаёт из IQAir и Open‑Meteo и мёржит результаты.
    Никогда не бросает исключение — при ошибках вернёт дефолтные поля.
    """
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
    """
    Температура поверхности моря (SST), по ближайшему прошедшему часу (UTC).
    API: https://marine-api.open-meteo.com/v1/marine
    """
    try:
        resp = _get(
            "https://marine-api.open-meteo.com/v1/marine",
            latitude=lat,
            longitude=lon,
            hourly="sea_surface_temperature",
            timezone="UTC",
        )
    except Exception as e:
        logging.warning("Marine SST request error: %s", e)
        return None
    if not resp or "hourly" not in resp:
        return None

    try:
        h = resp["hourly"]
        times = h.get("time", []) or []
        vals = h.get("sea_surface_temperature", []) or []
        v = _pick_nearest_hour(times, vals)
        return float(v) if isinstance(v, (int, float)) else None
    except Exception as e:
        logging.warning("Marine SST parse error: %s", e)
        return None

# ───────────────────────── Kp + кеш ────────────────────────────────

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

def get_kp() -> Tuple[Optional[float], str]:
    """
    Возвращает (kp_value, state). Пытается пройти по списку URL,
    распарсить значение, сохранить в кеш. Если сеть/парсинг падают —
    берёт из кеша. Иначе → (None, "н/д").
    """
    for url in KP_URLS:
        data = _fetch_kp_data(url)
        logging.info("Kp fetch from %s -> %s", url, bool(data))
        if not data:
            continue
        try:
            raw_val: Any = None
            if isinstance(data, list) and data:
                first = data[0]
                if isinstance(first, list) and len(data) > 1:
                    # Суточная таблица — берём вторую строку
                    entry = data[1]
                    raw_val = entry[-1]
                elif isinstance(first, dict):
                    # Минутные словари
                    entry = first
                    raw_val = entry.get("kp_index") or entry.get("estimated_kp") or entry.get("kp")
            if raw_val is None:
                raise ValueError("raw Kp not found")
            kp_value = float(str(raw_val).rstrip("Z").replace(",", "."))
            _save_kp_cache(kp_value)
            return kp_value, _kp_state(kp_value)
        except Exception as e:
            logging.warning("Kp parse error %s: %s", url, e)

    cached_kp, ts = _load_kp_cache()
    if cached_kp is not None:
        logging.info("Using cached Kp=%s ts=%s", cached_kp, ts)
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
