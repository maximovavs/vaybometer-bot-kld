#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
radiation.py  •  γ-фоновая дозовая нагрузка (µSv/h)

Используются 3 независимых источника (по убыванию приоритета):
  1) EURDEP CSV-feed          — станции Европы, обновление ~1 ч
  2) OpenRadiation community  — краудсорсинг, обновление ~15 мин
  3) EURDEP Simple API        — REST-интерфейс с поиском по радиусу

get_radiation(lat, lon, max_km=300) → dict | None
    {'dose': 0.11, 'unit':'µSv/h', 'dist_km':12.3,
     'station':'LT-KLAIPEDA-042', 'src':'eurdep_api', 'ts':'2025-06-09T12:40Z'}
"""

from __future__ import annotations
import csv, json, math, time, logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional, Tuple, List
import requests

# ────────────────────────── helpers ──────────────────────────
def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    φ1, φ2 = math.radians(lat1), math.radians(lat2)
    dφ = math.radians(lat2-lat1)
    dλ = math.radians(lon2-lon1)
    a = math.sin(dφ/2)**2 + math.cos(φ1)*math.cos(φ2)*math.sin(dλ/2)**2
    return R*2*math.atan2(math.sqrt(a), math.sqrt(1-a))

# ────────────────────────── EURDEP CSV (как было) ──────────────────────────
_EURDEP_CSV_URL = (
    "https://remap.jrc.ec.europa.eu/Service/RadMeasurementService/"
    "RadMeasurements/exportLatestCSV"
)
_EURDEP_CACHE: Tuple[float, List[Dict[str, Any]]] = (0.0, [])  # (ts, rows)

def _load_eurdep_csv() -> List[Dict[str, Any]]:
    global _EURDEP_CACHE
    now = time.time()
    if now - _EURDEP_CACHE[0] < 3600:      # кеш 1 час
        return _EURDEP_CACHE[1]

    try:
        txt = requests.get(_EURDEP_CSV_URL, timeout=20).text
        rdr = csv.DictReader(txt.splitlines(), delimiter=';')
        rows = [r for r in rdr if r.get("TypeRadData") == "AIR_GAMMA_D_G"]
        _EURDEP_CACHE = (now, rows)
        return rows
    except Exception as e:
        logging.warning("EURDEP CSV load error: %s", e)
        return []

def _src_eurdep_csv(lat: float, lon: float, max_km: float) -> Optional[Dict[str, Any]]:
    best = None
    rows = _load_eurdep_csv()
    for r in rows:
        try:
            la, lo = float(r["StationLat"]), float(r["StationLon"])
            dist = _haversine_km(lat, lon, la, lo)
            if dist > max_km:
                continue
            val = float(r["Value"])/1000.0  # нЗв/ч → µSv/h
            ts  = datetime.strptime(r["EndDate"], "%d/%m/%Y %H:%M:%S").replace(tzinfo=timezone.utc)
            if (best is None) or (dist < best["dist_km"]):
                best = {"dose": val, "unit":"µSv/h", "dist_km":dist,
                        "station": r["StationID"], "ts": ts.isoformat(timespec="minutes")+"Z",
                        "src":"eurdep_csv"}
        except Exception:
            continue
    return best

# ────────────────────────── OpenRadiation ──────────────────────────
_OPENRAD_URL = "https://data.openradiation.net/api/v1/measurements/near"

def _src_openradiation(lat: float, lon: float, max_km: float) -> Optional[Dict[str, Any]]:
    try:
        j = requests.get(_OPENRAD_URL,
                         params={"lat":lat, "lon":lon, "km":max_km, "limit":1},
                         timeout=15).json()
        if not j:
            return None
        m = j[0]
        val = float(m["dose_rate"])/1000.0           # нЗв/ч → µSv/h
        ts  = datetime.fromisoformat(m["measurement_time"]).astimezone(timezone.utc)
        dist = _haversine_km(lat, lon, m["location"]["lat"], m["location"]["lon"])
        return {"dose": val, "unit":"µSv/h", "dist_km":dist,
                "station": m.get("detector_name","openradiation"), "ts": ts.isoformat(timespec="minutes")+"Z",
                "src":"openradiation"}
    except Exception as e:
        logging.debug("OpenRadiation error: %s", e)
        return None

# ────────────────────────── EURDEP Simple API (новый fallback) ──────────────────────────
_EURDEP_API = "https://remap.jrc.ec.europa.eu/simpleAPI/measurements"

def _src_eurdep_api(lat: float, lon: float, max_km: float) -> Optional[Dict[str, Any]]:
    try:
        j = requests.get(_EURDEP_API,
                         params={"lat":lat, "lon":lon, "radius":max_km,
                                 "format":"json", "latest": "true"},
                         timeout=20).json()
        if not j:
            return None
        m = j[0]           # самый близкий/последний
        val = float(m["value"])/1000.0               # нЗв/ч → µSv/h
        ts  = datetime.fromisoformat(m["endtime"]).astimezone(timezone.utc)
        dist = _haversine_km(lat, lon, m["latitude"], m["longitude"])
        return {"dose": val, "unit":"µSv/h", "dist_km":dist,
                "station": m["station"], "ts": ts.isoformat(timespec="minutes")+"Z",
                "src":"eurdep_api"}
    except Exception as e:
        logging.debug("EURDEP API error: %s", e)
        return None

# ────────────────────────── Public API facade ──────────────────────────
def get_radiation(lat: float, lon: float, max_km: float = 300.0) -> Optional[Dict[str, Any]]:
    """
    Ищет ближайшую станцию в пределах max_km.
    Приоритет: csv → openradiation → simpleAPI.
    Возвращает dict (см. docstring сверху) или None.
    """
    for fn in (_src_eurdep_csv, _src_openradiation, _src_eurdep_api):
        data = fn(lat, lon, max_km)
        if data:
            return data
    return None

# ────────────────────────── CLI quick-check ──────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    for city, (la, lo) in {
        "Калининград": (54.71, 20.45),
        "Лимассол":    (34.70, 33.02),
    }.items():
        print(f"\n— {city} —")
        print(get_radiation(la, lo) or "нет станции в радиусе 300 км")
