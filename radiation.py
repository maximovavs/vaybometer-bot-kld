import json, math, requests, logging
from typing import Optional, Dict

# ────────── вспомогательные ──────────
def _closest_station(lat:float, lon:float, stations):
    def dist2(p):
        return (p["lat"]-lat)**2 + (p["lon"]-lon)**2
    return min(stations, key=dist2) if stations else None

# ────────── основные функции ──────────
def _eurdep(lat:float, lon:float) -> Optional[float]:
    """Берём ближайшую станцию EURDEP за последний час."""
    try:
        url = ("https://eurdep.jrc.ec.europa.eu/eurdep/msc/"
               f"observations?format=json&maxage=3h")
        data = requests.get(url, timeout=8).json()
        stations = [
            {"lat":s["latitude"], "lon":s["longitude"],
             "dose":s["lastvalue"]}                       # μSv/h
            for s in data.get("features", [])
            if s.get("lastvalue") is not None
        ]
        st = _closest_station(lat, lon, stations)
        return st["dose"] if st else None
    except Exception as e:
        logging.warning("EURDEP error: %s", e)
        return None

def _openradiation(lat:float, lon:float)->Optional[float]:
    """Ищем последнюю пользовательскую точку в 0.5° радиусе."""
    try:
        rng = 0.5
        url = ("https://api.openradiation.net/measurements"
               f"?lte[lat]={lat+rng}&gte[lat]={lat-rng}"
               f"&lte[lon]={lon+rng}&gte[lon]={lon-rng}"
               "&limit=1&sort=-created_at")
        js = requests.get(url, timeout=8).json()
        if js and js[0].get("sievert"):
            # value уже в μSv/h
            return float(js[0]["sievert"])
    except Exception as e:
        logging.warning("openradiation error: %s", e)
    return None

# ────────── публичная функция ──────────
def get_radiation(lat:float, lon:float) -> Optional[Dict[str,float]]:
    """
    Возвращает словарь {'dose': μSv/h, 'src': 'eurdep'|'openrad'} либо None
    """
    for fn, tag in ((_eurdep,"eurdep"), (_openradiation,"openrad")):
        dose = fn(lat, lon)
        if dose:
            return {"dose": dose, "src": tag}
    return None
