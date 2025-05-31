#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
weather.py
~~~~~~~~~~

1) OpenWeather One Call (v3 → v2) — если есть OWM_KEY  
2) Open-Meteo (start_date / end_date = сегодня+завтра)  
3) Фоллбэк — Open-Meteo «current_weather»

Дополнительно:
• fetch_tomorrow_temps(lat, lon, tz="UTC")  →  (t_max, t_min)
  Быстрый запрос только завтрашних max/min — им пользуется post.py
"""

from __future__ import annotations
import os, pendulum, logging
from typing import Any, Dict, Optional, Tuple

from utils import _get

OWM_KEY = os.getenv("OWM_KEY")            # может быть None
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

# ────────────────────────────────────────────────────────────
def fetch_tomorrow_temps(lat: float, lon: float,
                         tz: str | None = "UTC") -> Tuple[Optional[float], Optional[float]]:
    """
    Возвращает (t_max, t_min) на завтра одним недорогим запросом.
    Если сервис недоступен — оба значения = None.
    """
    tomorrow = pendulum.today().add(days=1).to_date_string()  # 'YYYY-MM-DD'
    j = _get(
        OPEN_METEO_URL,
        latitude=lat, longitude=lon,
        timezone=tz or "UTC",
        daily="temperature_2m_max,temperature_2m_min",
        start_date=tomorrow,
        end_date=tomorrow,
    )
    try:
        d = j["daily"]
        t_max = d["temperature_2m_max"][0]
        t_min = d["temperature_2m_min"][0]
        return t_max, t_min
    except Exception as e:
        logging.warning("fetch_tomorrow_temps: %s", e)
        return None, None

# -----------------------------------------------------------------
def _openweather(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    if not OWM_KEY:
        return None
    for ver in ("3.0", "2.5"):
        ow = _get(
            f"https://api.openweathermap.org/data/{ver}/onecall",
            lat=lat, lon=lon, appid=OWM_KEY, units="metric",
            exclude="minutely,hourly,alerts",
        )
        if not ow or "current" not in ow:
            continue

        cur = ow["current"]
        ow["current"] = {
            "temperature":   cur.get("temp"),
            "pressure":      cur.get("pressure"),
            "clouds":        cur.get("clouds"),
            "windspeed":     cur.get("wind_speed") * 3.6,
            "winddirection": cur.get("wind_deg"),
            "weathercode":   cur.get("weather", [{}])[0].get("id", 0),
        }
        ow["hourly"] = {
            "surface_pressure":    [cur.get("pressure")],
            "cloud_cover":         [cur.get("clouds")],
            "weathercode":         [ow["current"]["weathercode"]],
            "wind_speed_10m":      [ow["current"]["windspeed"]],
            "wind_direction_10m":  [ow["current"]["winddirection"]],
        }
        ow["daily"] = {
            "temperature_2m_max": [cur.get("temp"), cur.get("temp")],
            "temperature_2m_min": [cur.get("temp"), cur.get("temp")],
            "weathercode":        [ow["current"]["weathercode"],
                                   ow["current"]["weathercode"]],
        }
        ow["strong_wind"] = ow["current"]["windspeed"] > 30
        ow["fog_alert"]   = False
        return ow
    return None

# -----------------------------------------------------------------
def _openmeteo(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    today    = pendulum.today().to_date_string()
    tomorrow = pendulum.today().add(days=1).to_date_string()

    om = _get(
        OPEN_METEO_URL,
        latitude  = lat,
        longitude = lon,
        timezone  = "UTC",
        start_date= today,
        end_date  = tomorrow,
        current_weather="true",
        daily="temperature_2m_max,temperature_2m_min,weathercode",
        hourly="surface_pressure,cloud_cover,weathercode,"
               "wind_speed_10m,wind_direction_10m",
    )
    if not om or "current_weather" not in om or "daily" not in om:
        return None

    cur = om["current_weather"]
    cur["pressure"] = om["hourly"]["surface_pressure"][0]
    cur["clouds"]   = om["hourly"]["cloud_cover"][0]

    om["current"] = {
        "temperature":   cur["temperature"],
        "pressure":      cur["pressure"],
        "clouds":        cur["clouds"],
        "windspeed":     cur["windspeed"],
        "winddirection": cur["winddirection"],
        "weathercode":   cur["weathercode"],
    }
    om["strong_wind"] = cur["windspeed"] > 30
    om["fog_alert"]   = om["daily"]["weathercode"][0] in (45, 48)
    return om

# -----------------------------------------------------------------
def _openmeteo_current_only(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    om = _get(OPEN_METEO_URL, latitude=lat, longitude=lon,
              timezone="UTC", current_weather="true")
    if not om or "current_weather" not in om:
        return None
    cw = om["current_weather"]
    om["current"] = {
        "temperature":   cw["temperature"],
        "pressure":      cw.get("pressure", 1013),
        "clouds":        cw.get("clouds", 0),
        "windspeed":     cw.get("windspeed", 0),
        "winddirection": cw.get("winddirection", 0),
        "weathercode":   cw.get("weathercode", 0),
    }
    om["daily"] = {
        "temperature_2m_max": [cw["temperature"], cw["temperature"]],
        "temperature_2m_min": [cw["temperature"], cw["temperature"]],
        "weathercode":        [cw["weathercode"],  cw["weathercode"]],
    }
    om["hourly"] = {
        "surface_pressure":    [om["current"]["pressure"]],
        "cloud_cover":         [om["current"]["clouds"]],
        "weathercode":         [om["current"]["weathercode"]],
        "wind_speed_10m":      [om["current"]["windspeed"]],
        "wind_direction_10m":  [om["current"]["winddirection"]],
    }
    om["strong_wind"] = om["current"]["windspeed"] > 30
    om["fog_alert"]   = om["current"]["weathercode"] in (45, 48)
    return om

# -----------------------------------------------------------------
def get_weather(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    """
    Пытается вернуть прогноз из нескольких источников.
    Первый успешный ответ — сразу отдаётся.
    """
    for fn in (_openweather, _openmeteo, _openmeteo_current_only):
        data = fn(lat, lon)
        if data:
            return data
    return None
