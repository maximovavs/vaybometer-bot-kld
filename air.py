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
• get_geomag()           — стабильный 3-часовой индекс Kp (+ метаданные, кэш)
• get_kp()               — обратная совместимость: (kp, state) из get_geomag()

Особенности:
- Open-Meteо: берём значения по ближайшему прошедшему часу (UTC).
- SST: то же правило ближайшего часа.
- Геомагнитка: используем ТОЛЬКО 3-часовой продукт SWPC (noaa-planetary-k-index.json),
  без 1-минутного nowcast, чтобы исключить «скачки».
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

__all__ = ("get_air", "get_sst", "get_geomag", "get_kp")

# ───────────────────────── Константы / кеш ─────────────────────────

AIR_KEY = os.getenv("AIRVISUAL_KEY")

CACHE_DIR = Path.home() / ".cache" / "vaybometer"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

KP_CACHE = CACHE_DIR / "kp.json"  # кэш со структурой get_geomag()
KP_3H_URL = "https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json"

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
    """Берём значение по ближайшему прошедшему часу (UTC)."""
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

def _minutes_ago(ts_iso: str) -> Optional[int]:
    """Сколько минут назад от текущего UTC был ts_iso (YYYY-MM-DDTHH:MM:SSZ / ...)."""
    try:
        # Нормализуем «YYYY-MM-DDTHH:MM:SSZ» → без 'Z'
        ts_iso = str(ts_iso).rstrip("Z")
        # Оставляем только до минут (чтобы не споткнуться о разные форматы)
        base = ts_iso[:16]  # YYYY-MM-DDTHH:MM
        tm = time.strptime(base, "%Y-%m-%dT%H:%M")
        ts = int(time.mktime(tm))  # локаль -> но мы сравниваем разницу, погрешность некритична
        return max(0, int(time.time()) - ts) // 60
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
        logging.warning("Open-Meteo AQ parse error: %s", e)
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

# ───────────────────────── Кэш JSON ────────────────────────────────

def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None

def _save_json(path: Path, data: Dict[str, Any]) -> None:
    try:
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        logging.warning("Cache write error %s: %s", path, e)

# ───────────────────────── Kp: ТОЛЬКО стабильный 3h ────────────────

def _parse_kp_from_table(data: Any) -> Optional[Tuple[float, str]]:
    """
    Разбираем таблицу из noaa-planetary-k-index.json:
      [ [header...],
        ["2025-08-30 09:00:00", "0.3"],
        ... ]
    Возвращаем (kp_float, ts_iso) по последней строке.
    """
    if not isinstance(data, list) or len(data) < 2:
        return None
    # Идём с конца, ищем первую пригодную строку
    for row in reversed(data[1:]):
        if not isinstance(row, list) or len(row) < 2:
            continue
        ts = str(row[0]).strip().replace(" ", "T")  # → YYYY-MM-DDTHH:MM:SS
        raw = row[-1]
        try:
            kp_val = float(str(raw).replace(",", "."))
            if math.isfinite(kp_val):
                return kp_val, ts
        except Exception:
            continue
    return None

def get_geomag() -> Dict[str, Any]:
    """
    Детальная геомагнитка из стабильного 3-часового продукта SWPC.
    Без nowcast. Если сети нет — используем кэш (до 6 часов).
    Возвращает:
      {'kp','state','ts','age_min','src':'swpc_3h','window':'3h'}
    """
    # Онлайн попытка
    try:
        data = _get(KP_3H_URL)
        if isinstance(data, list):
            parsed = _parse_kp_from_table(data)
            if parsed:
                kp_val, ts = parsed
                res = {
                    "kp": kp_val,
                    "state": _kp_state(kp_val),
                    "ts": ts,
                    "age_min": _minutes_ago(ts),
                    "src": "swpc_3h",
                    "window": "3h",
                }
                _save_json(KP_CACHE, res)
                return res
    except Exception as e:
        logging.warning("Kp 3h request/parse error: %s", e)

    # Фоллбэк — кэш (до 6 часов считаем приемлемым)
    cached = _load_json(KP_CACHE)
    if isinstance(cached, dict) and "kp" in cached:
        age = cached.get("age_min")
        # если age не сохранён, пересчитаем от ts
        if age is None and isinstance(cached.get("ts"), str):
            cached["age_min"] = _minutes_ago(cached["ts"])
            age = cached["age_min"]
        if isinstance(age, int) and age <= 360:
            return cached

    # Совсем нет данных
    return {"kp": None, "state": "н/д", "ts": "", "age_min": None, "src": "swpc_3h", "window": "3h"}

def get_kp() -> Tuple[Optional[float], str]:
    """
    Обратная совместимость для внешнего кода: (kp, state) из стабильного 3-часового Kp.
    """
    g = get_geomag()
    return g.get("kp"), g.get("state", "н/д")

# ───────────────────────── CLI ─────────────────────────────────────

if __name__ == "__main__":
    from pprint import pprint
    print("=== Пример get_air (Калининград) ===")
    pprint(get_air(54.710426, 20.452214))
    print("\n=== Пример get_sst (Калининград) ===")
    print(get_sst(54.710426, 20.452214))
    print("\n=== Пример get_geomag (Kp 3h) ===")
    pprint(get_geomag())
    print("\n=== Пример get_kp (compat) ===")
    print(get_kp())