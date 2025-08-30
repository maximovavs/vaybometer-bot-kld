#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
air.py
~~~~~~

• Источники качества воздуха:
  1) IQAir / nearest_city  (API key: AIRVISUAL_KEY)
  2) Open-Meteo Air-Quality (без ключа)

• get_air(lat, lon)       — {'lvl','aqi','pm25','pm10','src','src_emoji','src_icon'}
• get_sst(lat, lon)       — Sea Surface Temperature (по ближайшему часу)
• get_kp()                — совместимая обёртка: (kp, 'спокойно/неспокойно/буря')
• get_geomag()            — расширенно: {'kp','state','ts','age_min','src'}
• get_solar_wind()        — {'bz','bt','speed','density','state','ts','age_min','src','window'}

Особенности:
- Open-Meteо: берём значения по ближайшему прошедшему часу (UTC).
- SST: то же правило ближайшего часа.
- Kp: берём POSLEDNEE значение из двух эндпоинтов SWPC; кэш 6 ч.
- Solar wind (DSCOVR): берём последние 5 записей (≈5 мин) для medians.
  Данные старше 20 мин считаем невалидными и не возвращаем.
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
    "get_air", "get_sst",
    "get_kp", "get_geomag",
    "get_solar_wind",
)

# ───────────────────────── Константы / кеш ─────────────────────────

AIR_KEY = os.getenv("AIRVISUAL_KEY")

CACHE_DIR = Path.home() / ".cache" / "vaybometer"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
KP_CACHE = CACHE_DIR / "kp.json"

# SWPC / NOAA
KP_URLS = [
    "https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json",
    "https://services.swpc.noaa.gov/json/planetary_k_index_1m.json",
]
SW_MAG = "https://services.swpc.noaa.gov/products/solar-wind/mag-1-day.json"
SW_PLA = "https://services.swpc.noaa.gov/products/solar-wind/plasma-1-day.json"

SRC_EMOJI = {"iqair": "📡", "openmeteo": "🛰", "n/d": "⚪"}
SRC_ICON  = {"iqair": "📡 IQAir", "openmeteo": "🛰 OM", "n/d": "⚪ н/д"}

# ───────────────────────── Утилиты общие ───────────────────────────

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

def _iso_to_epoch(s: str) -> Optional[int]:
    """Парсим ISO 'YYYY-MM-DDTHH:MM:SSZ' → epoch (UTC)."""
    try:
        if not isinstance(s, str):
            return None
        s = s.strip().replace(" ", "T")
        if s.endswith("Z"):
            s = s[:-1]
        # допускаем без секунд
        parts = s.split("T")
        y, m, d = parts[0].split("-")
        hh, mm, *rest = parts[1].split(":")
        ss = rest[0] if rest else "00"
        tm = time.strptime(f"{y}-{m}-{d} {hh}:{mm}:{ss}", "%Y-%m-%d %H:%M:%S")
        return int(time.mktime(tm) - time.timezone)  # к UTC
    except Exception:
        return None

def _minutes_ago(ts_iso: str) -> Optional[int]:
    ep = _iso_to_epoch(ts_iso)
    if ep is None:
        return None
    return max(0, int((time.time() - ep) / 60))

def _median(xs: List[float]) -> Optional[float]:
    arr = [float(x) for x in xs if isinstance(x, (int, float)) and math.isfinite(x)]
    if not arr:
        return None
    arr.sort()
    n = len(arr)
    mid = n // 2
    return arr[mid] if n % 2 == 1 else 0.5 * (arr[mid - 1] + arr[mid])

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

def merge_air_sources(src1: Optional[Dict[str, Any]], src2: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    aqi_val: Union[float, str, None] = "н/д"
    src_tag: str = "n/d"

    for s in (src1, src2):
        if not s:
            continue
        v = s.get("aqi")
        if isinstance(v, (int, float)) and math.isfinite(v) and v >= 0:
            aqi_val = float(v)
            src_tag = s.get("src") or src_tag
            break

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

# ───────────────────────── Геомагнитка (Kp) ────────────────────────

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

def _fetch_json(url: str, attempts: int = 3, backoff: float = 2.0) -> Optional[Any]:
    for i in range(attempts):
        try:
            data = _get(url)
            if data:
                return data
        except Exception:
            pass
        time.sleep(backoff ** i)
    return None

def _parse_kp_from_table(data: Any) -> Tuple[Optional[float], Optional[str]]:
    if not isinstance(data, list) or not data or not isinstance(data[0], list):
        return None, None
    # формат: первая строка — заголовок, далее данные; последний столбец — kp, первый — время
    for row in reversed(data[1:]):
        try:
            ts_iso = str(row[0])
            kp_val = float(str(row[-1]).replace(",", "."))
            return kp_val, ts_iso
        except Exception:
            continue
    return None, None

def _parse_kp_from_dicts(data: Any) -> Tuple[Optional[float], Optional[str]]:
    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        return None, None
    for item in reversed(data):
        raw = item.get("kp_index") or item.get("estimated_kp") or item.get("kp")
        ts_iso = item.get("time_tag") or item.get("time") or item.get("timestamp")
        if raw is None:
            continue
        try:
            kp_val = float(str(raw).replace(",", "."))
            return kp_val, ts_iso
        except Exception:
            continue
    return None, None

def get_geomag() -> Dict[str, Any]:
    """Расширенная геомагнитка: {'kp','state','ts','age_min','src'} с кешем."""
    for url in KP_URLS:
        data = _fetch_json(url)
        if not data:
            continue
        try:
            if isinstance(data, list) and data:
                if isinstance(data[0], list):
                    kp_value, ts_iso = _parse_kp_from_table(data)
                else:
                    kp_value, ts_iso = _parse_kp_from_dicts(data)
            else:
                kp_value, ts_iso = (None, None)

            if kp_value is None:
                raise ValueError("no parsable kp in response")

            _save_kp_cache(kp_value)
            age = _minutes_ago(ts_iso) if ts_iso else None
            return {
                "kp": kp_value,
                "state": _kp_state(kp_value),
                "ts": ts_iso or "",
                "age_min": age,
                "src": url,
            }
        except Exception as e:
            logging.warning("Kp parse error %s: %s", url, e)

    cached_kp, ts = _load_kp_cache()
    if cached_kp is not None:
        return {
            "kp": cached_kp,
            "state": _kp_state(cached_kp),
            "ts": "",
            "age_min": None,
            "src": "cache",
        }
    return {"kp": None, "state": "н/д", "ts": "", "age_min": None, "src": ""}

def get_kp() -> Tuple[Optional[float], str]:
    """Совместимая обёртка для старого кода."""
    g = get_geomag()
    return g.get("kp"), g.get("state", "н/д")

# ───────────────────────── Солнечный ветер (DSCOVR) ────────────────

def _parse_sw_table(url: str, want: Dict[str, str], window: int = 5) -> Dict[str, Any]:
    """
    Парсим SWPC 'json table' (первый ряд — заголовки).
    want: {'value_name':'column_key'} — пример: {'bz':'bz_gsm','bt':'bt'}
    Возвращает {'ts':ts_iso, <keys>:median, 'count':N}.
    """
    data = _fetch_json(url)
    if not isinstance(data, list) or len(data) < 2 or not isinstance(data[0], list):
        return {}
    header = [str(x).strip().lower() for x in data[0]]
    col = {h: i for i, h in enumerate(header)}

    def get_idx(key: str) -> Optional[int]:
        key = key.lower()
        return col.get(key)

    res_vals: Dict[str, List[float]] = {k: [] for k in want.keys()}
    ts_iso = None
    cnt = 0
    # берём последние window строк
    for row in reversed(data[1:1 + window]):
        if not isinstance(row, list):
            continue
        ts_iso = ts_iso or (str(row[get_idx("time_tag")]) if get_idx("time_tag") is not None else None)
        good = False
        for out_key, col_key in want.items():
            idx = get_idx(col_key)
            if idx is None or idx >= len(row):
                continue
            raw = row[idx]
            try:
                val = float(raw)
                # фильтр по "сентинелам"
                if not math.isfinite(val):
                    continue
                if abs(val) > 9e6:
                    continue
                res_vals[out_key].append(val)
                good = True
            except Exception:
                pass
        if good:
            cnt += 1

    result: Dict[str, Any] = {"ts": ts_iso or "", "count": cnt}
    for k, arr in res_vals.items():
        result[k] = _median(arr) if arr else None
    return result

def _classify_wind(bz: Optional[float], bt: Optional[float], v: Optional[float], n: Optional[float]) -> str:
    """Грубая эвристика состояния ветра."""
    if bz is None and bt is None and v is None and n is None:
        return "н/д"
    # Спокойно, если слабое поле и умеренная скорость/плотность
    if (bz is not None and bz > -2.0) and (bt is not None and bt < 10.0) \
       and (v is not None and v < 500.0) and (n is not None and n < 10.0):
        return "спокойно"
    # Возмущенно, если что-то из этого усилилось
    if (bz is not None and bz <= -5.0) or (bt is not None and bt >= 12.0) \
       or (v is not None and v >= 550.0) or (n is not None and n >= 15.0):
        return "возмущенно"
    return "неспокойно"

def get_solar_wind(window: int = 5, stale_minutes: int = 20) -> Optional[Dict[str, Any]]:
    """Возвращает усреднённый за ~последние window минут солнечный ветер (DSCOVR)."""
    # MAG: берем Bz, Bt
    mag = _parse_sw_table(SW_MAG, want={"bz": "bz_gsm", "bt": "bt"}, window=window)
    # PLASMA: берем скорость и плотность
    pla = _parse_sw_table(SW_PLA, want={"speed": "speed", "density": "density"}, window=window)

    # Вычислим «возраст» по последней доступной метке времени
    ts_iso = mag.get("ts") or pla.get("ts") or ""
    age = _minutes_ago(ts_iso) if ts_iso else None
    if age is None or age > stale_minutes:
        # данные неактуальны — не показываем строку
        return None

    bz = mag.get("bz"); bt = mag.get("bt")
    v  = pla.get("speed"); n = pla.get("density")
    state = _classify_wind(bz, bt, v, n)

    return {
        "bz": bz,            # нТ
        "bt": bt,            # нТ
        "speed": v,          # км/с
        "density": n,        # см^-3
        "state": state,
        "ts": ts_iso,
        "age_min": age,
        "src": "DSCOVR SWPC (mag/plasma 1-day)",
        "window": f"{window}m-median",
    }

# ───────────────────────── CLI ─────────────────────────────────────

if __name__ == "__main__":
    from pprint import pprint
    print("=== Пример get_air (Калининград) ===")
    pprint(get_air(54.710426, 20.452214))
    print("\n=== Пример get_sst (Калининград) ===")
    print(get_sst(54.710426, 20.452214))
    print("\n=== Пример get_geomag ===")
    pprint(get_geomag())
    print("\n=== Пример get_solar_wind ===")
    pprint(get_solar_wind())