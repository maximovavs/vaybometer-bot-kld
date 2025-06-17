# radiation.py (фрагмент)
import requests, logging, math

_HEADERS = {"Accept": "application/vnd.geo+json"}  # 👈 новый заголовок

def _eurdep(lat: float, lon: float) -> float | None:
    try:
        url = ("https://eurdep.jrc.ec.europa.eu/eurdep/msc/"
               "observations?format=json&maxage=6h")
        data = requests.get(url, headers=_HEADERS, timeout=8).json()

        stations = [
            {"lat": f["latitude"], "lon": f["longitude"],
             "dose": f["lastvalue"]}
            for f in data.get("features", [])
            if f.get("lastvalue") is not None
        ]
        logging.info("EURDEP rows: %s", len(stations))   # 👈 смотреть в логи

        # радиус 150 км  (≈ 1.35° по широте)
        best, best_d2 = None, 1e9
        for s in stations:
            d2 = (s["lat"]-lat)**2 + (s["lon"]-lon)**2
            if d2 < best_d2 and d2 <= 1.35**2:
                best, best_d2 = s, d2
        return best["dose"] if best else None
    except Exception as e:
        logging.warning("EURDEP error: %s", e)
        return None
