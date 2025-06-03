#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
weather.py
~~~~~~~~~~

1) OpenWeather One Call (v3 → v2) — если есть OWM_KEY
2) Open-Meteo (start_date / end_date = сегодня+завтра)
3) Фоллбэк — Open-Meteo «current_weather»

Дополнительно:
• fetch_tomorrow_temps(lat, lon, tz="UTC")  →  (t_max, t_min)
  Быстрый запрос только завтрашних max/min — для post_common.py
"""

from __future__ import annotations
import os
import pendulum
import logging
from typing import Any, Dict, Optional, Tuple

from utils import _get  # HTTP-обёртка из utils.py

OWM_KEY = os.getenv("OWM_KEY")            # ключ OpenWeather, может быть None
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

# ──────────────────────────────────────────────────────────────────────────────

def fetch_tomorrow_temps(
    lat: float,
    lon: float,
    tz: str | None = "UTC"
) -> Tuple[Optional[float], Optional[float]]:
    """
    Возвращает (t_max, t_min) на завтра одним недорогим запросом к Open-Meteo.
    Если сервис недоступен или данные невалидны — оба значения = None.
    """
    tomorrow = pendulum.today().add(days=1).to_date_string()  # 'YYYY-MM-DD'
    try:
        j = _get(
            OPEN_METEO_URL,
            latitude=lat,
            longitude=lon,
            timezone=tz or "UTC",
            daily="temperature_2m_max,temperature_2m_min",
            start_date=tomorrow,
            end_date=tomorrow,
        )
    except Exception as e:
        logging.warning("fetch_tomorrow_temps — HTTP error: %s", e)
        return None, None

    if not j or "daily" not in j:
        logging.warning("fetch_tomorrow_temps — нет поля 'daily' в ответе")
        return None, None

    try:
        d = j["daily"]
        t_max_list = d.get("temperature_2m_max", [])
        t_min_list = d.get("temperature_2m_min", [])
        if not t_max_list or not t_min_list:
            logging.warning("fetch_tomorrow_temps — списки max/min пустые")
            return None, None

        t_max = t_max_list[0]
        t_min = t_min_list[0]
        return float(t_max), float(t_min)
    except Exception as e:
        logging.warning("fetch_tomorrow_temps — парсинг ответа: %s", e)
        return None, None

# ──────────────────────────────────────────────────────────────────────────────

def _openweather(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    """
    Получение прогноза через OpenWeather One Call (v3 или v2).
    Возвращает структуру с ключами "current", "hourly", "daily", "strong_wind", "fog_alert",
    или None при ошибке.
    """
    if not OWM_KEY:
        return None

    for version in ("3.0", "2.5"):
        try:
            ow = _get(
                f"https://api.openweathermap.org/data/{version}/onecall",
                lat=lat,
                lon=lon,
                appid=OWM_KEY,
                units="metric",
                exclude="minutely,hourly,alerts",
            )
        except Exception as e:
            logging.warning("_openweather — HTTP error (v%s): %s", version, e)
            continue

        if not ow or "current" not in ow:
            logging.debug("_openweather — нет 'current' (v%s)", version)
            continue

        try:
            cur = ow["current"]
            # unify current
            unified_current = {
                "temperature":    cur.get("temp"),
                "pressure":       cur.get("pressure"),
                "clouds":         cur.get("clouds", 0),
                # wind_speed в OWM в м/с → км/ч
                "windspeed":      (cur.get("wind_speed", 0) * 3.6) if cur.get("wind_speed") is not None else 0.0,
                "winddirection":  cur.get("wind_deg", 0),
                # weathercode ← id из weather[0]
                "weathercode":    (cur.get("weather", [{}])[0].get("id", 0))
            }

            # hourly.surface_pressure и пр. заполняем списком из current для единообразия:
            unified_hourly = {
                "surface_pressure":    [cur.get("pressure", 0)],
                "cloud_cover":         [cur.get("clouds", 0)],
                "weathercode":         [unified_current["weathercode"]],
                "wind_speed_10m":      [unified_current["windspeed"]],
                "wind_direction_10m":  [unified_current["winddirection"]],
            }

            # daily строим как «дублирование» current, чтобы обеспечить минимум 2 элемента:
            temp_val = cur.get("temp")
            weather_val = unified_current["weathercode"]
            unified_daily = {
                "temperature_2m_max": [temp_val, temp_val],
                "temperature_2m_min": [temp_val, temp_val],
                "weathercode":        [weather_val, weather_val],
            }

            ow["current"] = unified_current
            ow["hourly"] = unified_hourly
            ow["daily"] = unified_daily
            ow["strong_wind"] = (unified_current["windspeed"] > 30.0)
            ow["fog_alert"] = False  # явного флага нет

            return ow
        except Exception as e:
            logging.warning("_openweather — ошибка обработки данных: %s", e)
            continue

    return None

# ──────────────────────────────────────────────────────────────────────────────

def _openmeteo(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    """
    Запрос прогноза через Open-Meteo (полные daily + hourly + current).
    Возвращает структуру:
      {
        "current": { … },
        "hourly": { "surface_pressure": [...], "cloud_cover": [...], … },
        "daily": { "temperature_2m_max": [...], "temperature_2m_min": [...], "weathercode": [...] },
        "strong_wind": bool,
        "fog_alert": bool
      }
    или None при ошибке/отсутствии полей.
    """
    today = pendulum.today().to_date_string()
    tomorrow = pendulum.today().add(days=1).to_date_string()

    try:
        om = _get(
            OPEN_METEO_URL,
            latitude=lat,
            longitude=lon,
            timezone="UTC",
            start_date=today,
            end_date=tomorrow,
            current_weather="true",
            daily="temperature_2m_max,temperature_2m_min,weathercode",
            hourly="surface_pressure,cloud_cover,weathercode,wind_speed_10m,wind_direction_10m",
        )
    except Exception as e:
        logging.warning("_openmeteo — HTTP error: %s", e)
        return None

    if not om or "current_weather" not in om or "daily" not in om or "hourly" not in om:
        logging.debug("_openmeteo — отсутствуют нужные ключи")
        return None

    try:
        cw = om["current_weather"]
        cur_pressure = om["hourly"].get("surface_pressure", [None])[0]
        cur_clouds = om["hourly"].get("cloud_cover", [None])[0]

        unified_current = {
            "temperature":    cw.get("temperature"),
            "pressure":       (cur_pressure if cur_pressure is not None else 0),
            "clouds":         (cur_clouds if cur_clouds is not None else 0),
            "windspeed":      cw.get("windspeed"),
            "winddirection":  cw.get("winddirection"),
            "weathercode":    cw.get("weathercode"),
        }

        om["current"] = unified_current
        om["strong_wind"] = (
            unified_current["windspeed"] is not None and unified_current["windspeed"] > 30.0
        )
        om["fog_alert"] = (
            (om["daily"].get("weathercode", [0])[0] in (45, 48))
        )

        return om
    except Exception as e:
        logging.warning("_openmeteo — ошибка обработки данных: %s", e)
        return None

# ──────────────────────────────────────────────────────────────────────────────

def _openmeteo_current_only(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    """
    Фоллбэк: только текущая погода через Open-Meteo.
    Возвращает структуру того же формата (daily и hourly дублируют current).
    """
    try:
        om = _get(
            OPEN_METEO_URL,
            latitude=lat,
            longitude=lon,
            timezone="UTC",
            current_weather="true",
        )
    except Exception as e:
        logging.warning("_openmeteo_current_only — HTTP error: %s", e)
        return None

    if not om or "current_weather" not in om:
        logging.debug("_openmeteo_current_only — нет 'current_weather'")
        return None

    try:
        cw = om["current_weather"]
        unified_current = {
            "temperature":    cw.get("temperature"),
            "pressure":       cw.get("pressure", 1013),
            "clouds":         cw.get("clouds", 0),
            "windspeed":      cw.get("windspeed", 0),
            "winddirection":  cw.get("winddirection", 0),
            "weathercode":    cw.get("weathercode", 0),
        }
        om["current"] = unified_current

        # Дублируем текущие данные для daily и hourly
        om["daily"] = {
            "temperature_2m_max": [cw.get("temperature"), cw.get("temperature")],
            "temperature_2m_min": [cw.get("temperature"), cw.get("temperature")],
            "weathercode":        [cw.get("weathercode"), cw.get("weathercode")],
        }
        om["hourly"] = {
            "surface_pressure":    [unified_current["pressure"]],
            "cloud_cover":         [unified_current["clouds"]],
            "weathercode":         [unified_current["weathercode"]],
            "wind_speed_10m":      [unified_current["windspeed"]],
            "wind_direction_10m":  [unified_current["winddirection"]],
        }

        om["strong_wind"] = (unified_current["windspeed"] > 30.0)
        om["fog_alert"] = (unified_current["weathercode"] in (45, 48))
        return om
    except Exception as e:
        logging.warning("_openmeteo_current_only — ошибка формирования данных: %s", e)
        return None

# ──────────────────────────────────────────────────────────────────────────────

def get_weather(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    """
    Возвращает единый словарь прогноза, стараясь:
      1) _openweather
      2) _openmeteo
      3) _openmeteo_current_only
    или None, если ни один источник не дал правильного результата.
    """
    for fn in (_openweather, _openmeteo, _openmeteo_current_only):
        try:
            data = fn(lat, lon)
        except Exception as e:
            logging.warning("get_weather — исключение в %s: %s", fn.__name__, e)
            data = None

        if data:
            return data

    return None