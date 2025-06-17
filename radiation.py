"""
radiation.py  •  get_radiation(lat, lon) → dict | None
Полёт: сначала «живые» источники → fallback на radiation_hourly.json
"""
from __future__ import annotations
import json, time, math, logging, pathlib
from typing import Dict, Any, Optional, Tuple

import requests

CACHE = pathlib.Path(__file__).parent / "radiation_hourly.json"
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ───────────────────────── live-источники ─────────────────────────
def _try_radmon(lat: float, lon: float) -> Optional[float]:
    try:
        j = requests.get("https://radmon.org/radmon.php?format=json").json()
        # ищем ближайший активный датчик <100 км, не старше 3 ч
        best, dmin = None, 1e9
        for p in j.get("users", []):
            dx = _haversine(lat, lon, p["lat"], p["lon"])
            if dx < 100 and (time.time() - p["last_seen"] < 3*3600):
                if dx < dmin:
                    best, dmin = p, dx
        if best:
            return float(best["cpm_avg"]) * 0.0065   # простая пересчётная конст.
    except Exception as e:
        logging.info("radmon err: %s", e)
    return None

def _try_eurdep(lat: float, lon: float) -> Optional[float]:
    try:
        j = requests.get(
            "https://eurdep.jrc.ec.europa.eu/eurdep/json/",
            timeout=10
        ).json()
        best, dmin = None, 1e9
        for p in j.get("measurements", []):
            dx = _haversine(lat, lon, p["lat"], p["lon"])
            if dx < 200 and (time.time() - p["utctime"] < 6*3600):
                if dx < dmin:
                    best, dmin = p, dx
        if best:
            return float(best["value"])
    except Exception as e:
        logging.info("eurdep err: %s", e)
    return None

def _haversine(lat1, lon1, lat2, lon2) -> float:
    R, dLat, dLon = 6371, math.radians(lat2-lat1), math.radians(lon2-lon1)
    a = math.sin(dLat/2)**2 + math.cos(math.radians(lat1))*math.cos(math.radians(lat2))*math.sin(dLon/2)**2
    return 2*R*math.asin(math.sqrt(a))

# ───────────────────────── API для постов ─────────────────────────
def get_radiation(lat: float, lon: float) -> Dict[str, Any] | None:
    """
    {'val': 0.11, 'trend': '↑', 'cached': False}
    """
    val_live = _try_radmon(lat, lon) or _try_eurdep(lat, lon)
    if val_live is not None:
        return {"val": round(val_live, 3), "trend": "→", "cached": False}

    # fallback на вчера-сегодня из radiation_hourly.json
    try:
        arr = json.loads(CACHE.read_text())
        pts = [p for p in arr if _haversine(lat, lon, p["lat"], p["lon"]) < 150]
        if len(pts) >= 2:
            last = pts[-1]["val"]
            prev = pts[-2]["val"]
            if None not in (last, prev):
                trend = "↑" if last-prev > 0.005 else "↓" if last-prev < -0.005 else "→"
                return {"val": round(last,3), "trend": trend, "cached": True}
    except Exception as e:
        logging.info("cache err: %s", e)
    return None
