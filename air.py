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
• get_sst(lat, lon) — текущая температура поверхности моря (SST) для любых lat, lon
• get_kp() — текущий индекс Kp с retry, кешем и двумя endpoint’ами
"""

from __future__ import annotations
import os
import logging
import time
import json
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, Union

from utils import _get  # утилита для HTTP-запросов (_get_retry внутри)

# ────────── Константы ───────────────────────────────────────────────
# API AQI: IQAir использует координаты пользователя,
# но для нашего бота мы передаём lat и lon непосредственно
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


# ────────── Утилиты для AQI ────────────────────────────────────────────
def _aqi_level(aqi: Union[int, float, str, None]) -> str:
    """
    Переводит числовой AQI в текстовую категорию:
      ≤50   → "хороший"
      ≤100  → "умеренный"
      ≤150  → "вредный"
      ≤200  → "оч. вредный"
      >200  → "опасный"
      None/"н/д" → "н/д"
    """
    if aqi in (None, "н/д"):
        return "н/д"
    try:
        aqi_val = float(aqi)
    except (TypeError, ValueError):
        return "н/д"
    if aqi_val <= 50:
        return "хороший"
    if aqi_val <= 100:
        return "умеренный"
    if aqi_val <= 150:
        return "вредный"
    if aqi_val <= 200:
        return "оч. вредный"
    return "опасный"


def _kp_state(kp: float) -> str:
    """
    Переводит значение Kp в состояние:
      < 3 → "спокойно"
      < 5 → "неспокойно"
      ≥ 5 → "буря"
    """
    if kp < 3.0:
        return "спокойно"
    if kp < 5.0:
        return "неспокойно"
    return "буря"


# ────────── Источники качества воздуха ──────────────────────────────
def _src_iqair(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    """
    Запрашивает качество воздуха через IQAir API для указанных lat, lon.
    Возвращает словарь {"aqi": float, "pm25": float, "pm10": float} или None при ошибке.
    """
    if not AIR_KEY:
        return None
    try:
        resp = _get(
            "https://api.airvisual.com/v2/nearest_city",
            lat=lat,
            lon=lon,
            key=AIR_KEY
        )
    except Exception as e:
        logging.warning("IQAir request error: %s", e)
        return None

    if not resp or "data" not in resp:
        return None

    try:
        pollution = resp["data"]["current"]["pollution"]
        aqi_val = pollution.get("aqius", None)
        pm25_val = pollution.get("p2", None)
        pm10_val = pollution.get("p1", None)

        # Приводим к нужным типам
        aqi_float = float(aqi_val) if aqi_val is not None else None
        pm25_float = float(pm25_val) if pm25_val is not None else None
        pm10_float = float(pm10_val) if pm10_val is not None else None

        return {"aqi": aqi_float, "pm25": pm25_float, "pm10": pm10_float}
    except Exception as e:
        logging.warning("IQAir parse error: %s", e)
        return None


def _src_openmeteo(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    """
    Запрашивает качество воздуха через Open-Meteo Air-Quality API для указанных lat, lon.
    Возвращает {"aqi": float|"н/д", "pm25": float|None, "pm10": float|None} или None при ошибке.
    """
    try:
        resp = _get(
            "https://air-quality-api.open-meteo.com/v1/air-quality",
            latitude=lat,
            longitude=lon,
            hourly="pm10,pm2_5,us_aqi",
            timezone="UTC"
        )
    except Exception as e:
        logging.warning("Open-Meteo AQ request error: %s", e)
        return None

    if not resp or "hourly" not in resp:
        return None

    try:
        hourly = resp["hourly"]
        aqi_raw = hourly.get("us_aqi", [])
        pm25_raw = hourly.get("pm2_5", [])
        pm10_raw = hourly.get("pm10", [])

        # Берём первый элемент, если он есть, иначе "н/д"/None
        aqi_val = aqi_raw[0] if isinstance(aqi_raw, list) and aqi_raw else None
        pm25_val = pm25_raw[0] if isinstance(pm25_raw, list) and pm25_raw else None
        pm10_val = pm10_raw[0] if isinstance(pm10_raw, list) and pm10_raw else None

        # Нормализуем
        aqi_norm: Union[float, str]
        if isinstance(aqi_val, (int, float)) and aqi_val >= 0:
            aqi_norm = float(aqi_val)
        else:
            aqi_norm = "н/д"

        pm25_norm = float(pm25_val) if isinstance(pm25_val, (int, float)) and pm25_val >= 0 else None
        pm10_norm = float(pm10_val) if isinstance(pm10_val, (int, float)) and pm10_val >= 0 else None

        return {"aqi": aqi_norm, "pm25": pm25_norm, "pm10": pm10_norm}
    except Exception as e:
        logging.warning("Open-Meteo AQ parse error: %s", e)
        return None


def merge_air_sources(
    src1: Optional[Dict[str, Any]],
    src2: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    """
    Объединяет результаты двух источников AQI (приоритет src1 → src2).
    Возвращает словарь {"lvl": str, "aqi": float|"н/д", "pm25": float|None, "pm10": float|None}.
    Если оба источника не дали валидного значения для ключа, остаётся "н/д"/None.
    """
    base: Dict[str, Union[str, float, None]] = {
        "aqi": "н/д",
        "pm25": None,
        "pm10": None
    }

    # Ключи, которые надо смержить
    for key in ("aqi", "pm25", "pm10"):
        v1 = src1.get(key) if src1 else None
        if v1 not in (None, "н/д"):
            base[key] = v1
        else:
            v2 = src2.get(key) if src2 else None
            if v2 not in (None, "н/д"):
                base[key] = v2
            # иначе оставляем базовое значение

    # Добавляем категорию уровня AQI
    base["lvl"] = _aqi_level(base["aqi"])  # type: ignore

    return base  # type: ignore


def get_air(lat: float, lon: float) -> Dict[str, Any]:
    """
    Возвращает объединённые данные качества воздуха по координатам:
      {"lvl": str, "aqi": float|"н/д", "pm25": float|None, "pm10": float|None}
    Никогда не бросает исключение; при ошибках API возвращает дефолтные значения.
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


# ────────── Sea Surface Temperature ─────────────────────────────────
def get_sst(lat: float, lon: float) -> Optional[float]:
    """
    Запрашивает температуру поверхности моря (SST) по переданным координатам (lat, lon).
    Использует Open-Meteo Marine API:
      https://marine-api.open-meteo.com/v1/marine
    При ошибке возвращает None.
    """
    try:
        resp = _get(
            "https://marine-api.open-meteo.com/v1/marine",
            latitude=lat,
            longitude=lon,
            hourly="sea_surface_temperature",
            timezone="UTC"
        )
    except Exception as e:
        logging.warning("Marine SST request error: %s", e)
        return None

    if not resp or "hourly" not in resp:
        return None

    try:
        arr = resp["hourly"].get("sea_surface_temperature", [])
        val = arr[0] if arr else None
        return float(val) if isinstance(val, (int, float)) else None
    except Exception as e:
        logging.warning("Marine SST parse error: %s", e)
        return None

# ────────── Kp-индекс с retry и кешем ──────────────────────────────
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
    """
    Пытается получить данные Kp (список) с указанного URL с retry/backoff.
    """
    for i in range(attempts):
        data = _get(url)
        if data:
            return data
        time.sleep(backoff ** i)
    return None


def get_kp() -> Tuple[Optional[float], str]:
    """
    Возвращает кортеж (kp_value: float | None, state: str).
    Порядок:
      1) Запрос к каждому URL в KP_URLS с retry
      2) Парсинг raw_val; при успехе — кеширование и возврат
      3) Если оба запроса неудачны → попытка взять из кеша
      4) Иначе → (None, "н/д")
    """
    for url in KP_URLS:
        data = _fetch_kp_data(url)
        logging.info("Kp fetch from %s -> %s", url, bool(data))
        if not data:
            continue
        try:
            raw_val: Any = None
            # Сценарии:
            #  - Суточный формат: список списков [[...], [...], ...]
            #  - Минутный формат: список словарей [{...}, {...}, ...]
            if isinstance(data, list) and data:
                first = data[0]
                if isinstance(first, list) and len(data) > 1:
                    # Суточный формат: берем вторую строку (data[1]), последний столбец
                    entry = data[1]
                    raw_val = entry[-1]
                elif isinstance(first, dict):
                    # Минутный формат: берем первый элемент
                    entry = first
                    raw_val = entry.get("kp_index") or entry.get("estimated_kp") or entry.get("kp")

            if raw_val is None:
                raise ValueError("raw Kp not found")

            # Приводим к float
            raw_str = str(raw_val).rstrip("Z").replace(",", ".")
            kp_value = float(raw_str)
            _save_kp_cache(kp_value)
            return kp_value, _kp_state(kp_value)

        except Exception as e:
            logging.warning("Kp parse error %s: %s", url, e)

    # Фоллбэк: берём из кеша
    cached_kp, ts = _load_kp_cache()
    if cached_kp is not None:
        logging.info("Using cached Kp=%s ts=%s", cached_kp, ts)
        return cached_kp, _kp_state(cached_kp)

    return None, "н/д"


# ────────── CLI-тестирование ────────────────────────────────────────
if __name__ == "__main__":
    from pprint import pprint

    print("=== Пример get_air на Калининграде ===")
    pprint(get_air(54.710426, 20.452214))

    print("\n=== Пример get_sst на Балтийском море (Калининград) ===")
    print(get_sst(54.710426, 20.452214))

    print("\n=== Пример get_kp ===")
    print(get_kp())