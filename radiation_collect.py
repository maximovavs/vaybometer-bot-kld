#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Запускать кроном/Workflow раз в час.
Добавляет свежий замер γ-фона в radiation_hourly.json
Формат одной записи:
{
  "ts": 1717935600,        # Unix-время замера
  "lat": 54.71,
  "lon": 20.45,
  "val": 0.11              # μSv/h  (None, если н/д)
}
"""
import json, time, logging, pathlib, sys
from typing import Optional
from radiation import _try_radmon, _try_eurdep

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
CACHE = pathlib.Path(__file__).parent / "radiation_hourly.json"
CACHE.touch(exist_ok=True)

def get_gamma(lat: float, lon: float) -> Optional[float]:
    """два источника подряд, None если оба молчат"""
    return _try_radmon(lat, lon) or _try_eurdep(lat, lon)

def append_point(lat: float, lon: float) -> None:
    arr = []
    try:
        txt = CACHE.read_text() or "[]"
        arr = json.loads(txt)
    except Exception:
        logging.warning("cant parse cache – overwrite")

    val = get_gamma(lat, lon)
    arr.append({"ts": int(time.time()), "lat": lat, "lon": lon, "val": val})
    # храним максимум 1000 последних точек
    CACHE.write_text(json.dumps(arr[-1000:], ensure_ascii=False, indent=2))
    logging.info("new point: %s μSv/h", val)

if __name__ == "__main__":
    # при желании перечислите несколько станций
    append_point(54.71, 20.45)   # Калининград
    append_point(34.70, 33.02)   # Лимассол
