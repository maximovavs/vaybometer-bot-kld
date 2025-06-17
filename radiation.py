#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
radiation.py  ·  γ-доза (µSv/h) поблизости указанной точки.

• get_radiation(lat, lon)  →  dict | None
    {'uSv_h': float, 'src': 'radmon'|'eurdep', 'station': str, 'dist_km': float}

Алгоритм:
 1) Radmon JSON (общественная сеть бытовых датчиков)
 2) EURDEP (официальные станции ЕС, включая Кипр)
 3) если ничего близко или свежее не найдено → None
"""
from __future__ import annotations
import math, time, json, logging
from pathlib import Path
from typing import Any, Dict, Optional

from utils import _get   # тот же helper, что уже есть в проекте

LOG = logging.getLogger(__name__)
CACHE = Path.home() / ".cache" / "vaybometer"
CACHE.mkdir(parents=True, exist_ok=True)
RADMON_CACHE = CACHE / "radmon.json"
EURDEP_CACHE = CACHE / "eurdep.json"

# ────────────────────────── гео-утилиты ──────────────────────────
def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    φ1, φ2 = math.radians(lat1), math.radians(lat2)
    dφ     = math.radians(lat2 - lat1)
    dλ     = math.radians(lon2 - lon1)
    a = math.sin(dφ/2)**2 + math.cos(φ1)*math.cos(φ2)*math.sin(dλ/2)**2
    return 2*R*math.asin(math.sqrt(a))

# ────────────────────────── Radmon ───────────────────────────────
_RADMON_URL = "https://radmon.org/radmon.php?getcurrjson&time=5"  # последние 5 ч

def _load_radmon() -> list[dict]:
    try:
        # кеш 1 час
        if RADMON_CACHE.exists() and time.time() - RADMON_CACHE.stat().st_mtime < 3600:
            return json.loads(RADMON_CACHE.read_text())
        data = _get(_RADMON_URL, response_type="json")
        if isinstance(data, list):
            RADMON_CACHE.write_text(json.dumps(data))
            return data
    except Exception as e:
        LOG.info("Radmon fetch error: %s", e)
    return []

def _nearest_radmon(lat: float, lon: float) -> Optional[dict]:
    best, best_d = None, 1e9
    for st in _load_radmon():
        try:
            slat, slon = float(st["Latitude"]), float(st["Longitude"])
            dist = _haversine(lat, lon, slat, slon)
            if dist < best_d:
                best, best_d = st, dist
        except Exception:
            continue
    if best and best_d <= 400:          # не дальше 400 км
        when = float(best.get("UnixTime", 0))
        if time.time() - when < 5400:    # не старше 90 мин
            return {
                "uSv_h": float(best["CPM"]) * 0.0057,  # ≈ перевод CPM->µSv/h (Geiger β/γ)
                "src": "radmon",
                "station": best.get("ID","?"),
                "dist_km": round(best_d, 1),
            }
    return None

# ────────────────────────── EURDEP ───────────────────────────────
_EURDEP_LIST = "https://remap.jrc.ec.europa.eu/eurdep/feeds/currentlevels.json"

def _load_eurdep() -> list[dict]:
    try:
        if EURDEP_CACHE.exists() and time.time() - EURDEP_CACHE.stat().st_mtime < 1800:
            return json.loads(EURDEP_CACHE.read_text())
        data = _get(_EURDEP_LIST, response_type="json")
        if isinstance(data, list):
            EURDEP_CACHE.write_text(json.dumps(data))
            return data
    except Exception as e:
        LOG.info("EURDEP fetch error: %s", e)
    return []

def _nearest_eurdep(lat: float, lon: float) -> Optional[dict]:
    best, best_d = None, 1e9
    for st in _load_eurdep():
        try:
            slat, slon = float(st["lat"]), float(st["lon"])
            dist = _haversine(lat, lon, slat, slon)
            if dist < best_d:
                best, best_d = st, dist
        except Exception:
            continue
    if best and best_d <= 500:
        val = best.get("gdr")
        if isinstance(val, (int, float)) and val > 0:
            return {
                "uSv_h": val / 1000.0,   # нГр/ч  →  µЗв/ч
                "src": "eurdep",
                "station": best.get("id","?"),
                "dist_km": round(best_d, 1),
            }
    return None

# ────────────────────────── Public API ───────────────────────────
def get_radiation(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    """
    Вернёт ближайшую валидную γ-дозу или None.
    Приоритет: Radmon → EURDEP.
    """
    for fn in (_nearest_radmon, _nearest_eurdep):
        res = fn(lat, lon)
        if res:
            return res
    return None

# ────────────────────────── CLI-тест ─────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    tests = {
        "Калининград": (54.71, 20.45),
        "Лимассол":    (34.70, 33.02),
    }
    for city,(la,lo) in tests.items():
        print(f"{city}: {get_radiation(la, lo)}")
