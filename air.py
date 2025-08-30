#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
air.py
~~~~~~

• Источники качества воздуха:
  1) IQAir / nearest_city  (API key: AIRVISUAL_KEY)
  2) Open-Meteo Air-Quality (без ключа)

• get_air(lat, lon)      — {'lvl','aqi','pm25','pm10','src','src_emoji','src_icon'}
• get_sst(lat, lon)      — Sea Surface Temperature (по ближайшему часу)

• Геомагнитка:
  - get_kp()             — (kp, state)  — стабильный 3-часовой Kp (SWPC), кэш 3 ч (обратная совместимость)
  - get_geomag()         — детально: {'kp','state','ts','age_min','src','window'} — удобно для «5 мин назад»
  - get_solar_wind()     — {'bz','bt','v','n','ts','age_min','src'}  — реальный солнечный ветер (DSCOVR), кэш 10 мин

Замечания:
- Для Kp по умолчанию используем 3-часовой продукт SWPC (устойчивее и меньше «скачет»).
- «Nowcast» (1-минутный) используется только как резерв при недоступности 3-часового.
- Телеметрия солнечного ветра фильтруется: отбрасываем невалидные значения (|Bt|/|Bz|>100 нТл, v вне [200, 1000], n вне [0, 50]).
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

__all__ = (
    "get_air",
    "get_sst",
    "get_kp",
    "get_geomag",
    "get_solar_wind",
)

# ───────────────────────── Константы / кеш ─────────────────────────

AIR_KEY = os.getenv("AIRVISUAL_KEY")

CACHE_DIR = Path.home() / ".cache" / "vaybometer"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

KP_CACHE          = CACHE_DIR / "kp.json"           # стабильный 3-часовой
KP_NOWCAST_CACHE  = CACHE_DIR / "kp_nowcast.json"   # 1-минутный (резерв)
SW_CACHE          = CACHE_DIR / "solarwind.json"    # солнечный ветер

# SWPC продукты
KP_3H_URL     = "https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json"
KP_1M_URL     = "https://services.swpc.noaa.gov/json/planetary_k_index_1m.json"

# DSCOVR: магнитометр и плазма (1-сутки, ~1 мин дискрет)
SW_MAG_URL    = "https://services.swpc.noaa.gov/products/solar-wind/mag-1-day.json"
SW_PLASMA_URL = "https://services.swpc.noaa.gov/products/solar-wind/plasma-1-day.json"

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

def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None

def _save_json(path: Path, data: Dict[str, Any]) -> None:
    try:
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        logging.warning("Cache write error to %s: %s", path, e)

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

# ───────────────────────── Kp: стабильный + nowcast (кэш) ──────────

def _parse_kp_from_table(data: Any) -> Optional[Tuple[float, str]]:
    """
    JSON-таблица: первая строка — заголовки; последние значения — самые свежие.
    Возвращаем (kp, ts_iso).
    """
    if not isinstance(data, list) or len(data) < 2 or not isinstance(data[0], list):
        return None
    for row in reversed(data[1:]):
        try:
            # Формат: ["YYYY-mm-dd HH:MM:SS", ..., kp]
            ts = str(row[0])
            kp_val = float(str(row[-1]).replace(",", "."))
            if math.isfinite(kp_val):
                return kp_val, ts
        except Exception:
            continue
    return None

def _parse_kp_from_dicts(data: Any) -> Optional[Tuple[float, str]]:
    """
    Список словарей 1-минутного nowcast.
    Берём последнее валидное значение.
    """
    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        return None
    for item in reversed(data):
        raw = item.get("kp_index") or item.get("estimated_kp") or item.get("kp")
        ts  = item.get("time_tag") or item.get("time") or item.get("date")
        if raw is None:
            continue
        try:
            kp_val = float(str(raw).replace(",", "."))
            if math.isfinite(kp_val):
                return kp_val, str(ts) if ts else ""
        except Exception:
            continue
    return None

def _minutes_ago(ts_iso: str) -> Optional[int]:
    try:
        # Универсально: парсим первые 16 символов до минут
        # "YYYY-mm-dd HH:MM" или "YYYY-mm-ddTHH:MM"
        ts_iso = ts_iso.replace("T", " ")
        base = ts_iso[:16]
        t_struct = time.strptime(base, "%Y-%m-%d %H:%M")
        ts = int(time.mktime(t_struct))
        return max(0, int(time.time()) - ts) // 60
    except Exception:
        return None

def get_geomag() -> Dict[str, Any]:
    """
    Детальная геомагнитка.
    Пытаемся получить стабильный 3-часовой Kp; при неудаче — 1-мин nowcast.
    Кэши: 3h Kp — 3 часа, nowcast — 15 минут.
    """
    # 1) стабильный 3h
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

    # 2) fallback: кэш 3h
    cached = _load_json(KP_CACHE)
    if isinstance(cached, dict) and "kp" in cached:
        age = cached.get("age_min")
        # если кэш не старше 3 часов — годится
        if isinstance(age, int) and age <= 180:
            return cached

    # 3) nowcast 1-мин
    try:
        data = _get(KP_1M_URL)
        if isinstance(data, list):
            parsed = _parse_kp_from_dicts(data)
            if parsed:
                kp_val, ts = parsed
                res = {
                    "kp": kp_val,
                    "state": _kp_state(kp_val),
                    "ts": ts,
                    "age_min": _minutes_ago(ts),
                    "src": "swpc_1m",
                    "window": "1m",
                }
                _save_json(KP_NOWCAST_CACHE, res)
                return res
    except Exception as e:
        logging.warning("Kp 1m request/parse error: %s", e)

    # 4) fallback: кэш nowcast (до 15 минут)
    cached = _load_json(KP_NOWCAST_CACHE)
    if isinstance(cached, dict) and "kp" in cached:
        age = cached.get("age_min")
        if isinstance(age, int) and age <= 15:
            return cached

    # ничего
    return {"kp": None, "state": "н/д", "ts": "", "age_min": None, "src": "n/d", "window": ""}

def get_kp() -> Tuple[Optional[float], str]:
    """
    Обратная совместимость: вернуть только (kp, state) — из get_geomag().
    """
    g = get_geomag()
    return g.get("kp"), g.get("state", "н/д")

# ───────────────────────── Солнечный ветер (DSCOVR) ────────────────

def _sanitize_sw_value(name: str, val: Any) -> Optional[float]:
    try:
        v = float(val)
    except Exception:
        return None
    if not math.isfinite(v):
        return None
    # отбрасываем мусор/заглушки
    if name in ("bt", "bz"):
        if abs(v) > 100:  # нТл
            return None
    elif name == "v":
        if v < 200 or v > 1000:  # км/с
            return None
    elif name == "n":
        if v < 0 or v > 50:  # см^-3
            return None
    return v

def get_solar_wind() -> Optional[Dict[str, Any]]:
    """
    Возвращает словарь {'bz','bt','v','n','ts','age_min','src'} или None.
    Кэш: 10 минут. Берём последнюю валидную минуту из обоих файлов.
    """
    # 1) кэш
    cached = _load_json(SW_CACHE)
    if isinstance(cached, dict):
        age = cached.get("age_min")
        if isinstance(age, int) and age <= 10:
            return cached

    # 2) сетевые данные
    try:
        mag = _get(SW_MAG_URL)
        plasma = _get(SW_PLASMA_URL)
        # оба — JSON-таблицы, первая строка — заголовки
        def last_valid_row(arr: Any) -> Optional[List[Any]]:
            if not isinstance(arr, list) or len(arr) < 2 or not isinstance(arr[0], list):
                return None
            for row in reversed(arr[1:]):
                if any(x in (None, "null", "") for x in row):
                    continue
                return row
            return None

        r_mag = last_valid_row(mag)
        r_pl  = last_valid_row(plasma)
        if not r_mag or not r_pl:
            raise ValueError("no valid rows")

        ts_mag = str(r_mag[0])
        ts_pl  = str(r_pl[0])
        ts = ts_mag or ts_pl

        # По документации SWPC (mag: [time, bt, bx, by, bz, ...], plasma: [time, speed, density, ...])
        bt = _sanitize_sw_value("bt", r_mag[1])
        bz = _sanitize_sw_value("bz", r_mag[4])
        v  = _sanitize_sw_value("v",  r_pl[1])
        n  = _sanitize_sw_value("n",  r_pl[2])

        if all(x is None for x in (bt, bz, v, n)):
            raise ValueError("all sanitized to None")

        res = {
            "bt": bt,
            "bz": bz,
            "v":  v,
            "n":  n,
            "ts": ts,
            "age_min": _minutes_ago(ts),
            "src": "swpc_dscovr",
        }
        _save_json(SW_CACHE, res)
        return res
    except Exception as e:
        logging.warning("Solar wind fetch/parse error: %s", e)

    # 3) fallback: даже просроченный кэш (до часа), лучше чем ничего
    if isinstance(cached, dict):
        age = cached.get("age_min")
        if isinstance(age, int) and age <= 60:
            return cached

    return None

# ───────────────────────── CLI ─────────────────────────────────────

if __name__ == "__main__":
    from pprint import pprint
    print("=== Пример get_air (Калининград) ===")
    pprint(get_air(54.710426, 20.452214))
    print("\n=== Пример get_sst (Калининград) ===")
    print(get_sst(54.710426, 20.452214))
    print("\n=== Пример get_kp / get_geomag ===")
    print(get_kp())
    pprint(get_geomag())
    print("\n=== Пример get_solar_wind ===")
    pprint(get_solar_wind())