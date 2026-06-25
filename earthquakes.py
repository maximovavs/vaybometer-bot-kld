#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Kaliningrad/KLD 24h earthquake alert line via USGS Earthquake Catalog API."""
from __future__ import annotations

import math
import os
from typing import Any, Dict, List, Optional

import pendulum
import requests

USGS_EARTHQUAKE_QUERY_URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"

KLD_CENTER_LAT = 54.71
KLD_CENTER_LON = 20.51

KLD_CITY_COORDS = {
    "Калининград": (54.7104, 20.4522),
    "Светлогорск": (54.9439, 20.1514),
    "Зеленоградск": (54.9600, 20.4750),
    "Балтийск": (54.6544, 19.9094),
    "Черняховск": (54.6335, 21.8156),
}

KLD_CITY_GENITIVE = {
    "Калининград": "Калининграда",
    "Светлогорск": "Светлогорска",
    "Зеленоградск": "Зеленоградска",
    "Балтийск": "Балтийска",
    "Черняховск": "Черняховска",
}

REQUEST_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "10"))

__all__ = (
    "get_recent_earthquakes_kld",
    "build_kld_quake_line",
)


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def _nearest_city(lat: float, lon: float) -> tuple[Optional[str], Optional[float]]:
    best_name: Optional[str] = None
    best_dist: Optional[float] = None
    for name, (city_lat, city_lon) in KLD_CITY_COORDS.items():
        dist = _haversine_km(lat, lon, city_lat, city_lon)
        if best_dist is None or dist < best_dist:
            best_name = name
            best_dist = dist
    return best_name, best_dist


def _normalize_event(feature: Dict[str, Any], tz: str = "Europe/Kaliningrad") -> Optional[Dict[str, Any]]:
    try:
        props = feature.get("properties") or {}
        geom = feature.get("geometry") or {}
        coords = geom.get("coordinates") or []
        if len(coords) < 2:
            return None
        lon = float(coords[0])
        lat = float(coords[1])
        depth_km = float(coords[2]) if len(coords) > 2 and coords[2] is not None else None
        mag = float(props.get("mag"))
        ts_ms = int(props.get("time"))
        time_utc = pendulum.from_timestamp(ts_ms / 1000, tz="UTC")
        time_local = time_utc.in_timezone(tz)
        nearest_name, nearest_dist = _nearest_city(lat, lon)
        return {
            "mag": mag,
            "place": str(props.get("place") or ""),
            "time_utc": time_utc.to_iso8601_string(),
            "time_local": time_local.to_iso8601_string(),
            "depth_km": depth_km,
            "lat": lat,
            "lon": lon,
            "distance_km": float(nearest_dist) if nearest_dist is not None else None,
            "nearest_city": nearest_name,
            "url": str(props.get("url") or ""),
        }
    except Exception:
        return None


def get_recent_earthquakes_kld(
    hours: int = 24,
    radius_km: float = 500,
    min_mag: float = 2.0,
) -> Optional[List[Dict[str, Any]]]:
    """Return normalized USGS earthquake events near KLD, or None on source failure."""
    try:
        now = pendulum.now("UTC")
        start = now.subtract(hours=int(hours))
        params = {
            "format": "geojson",
            "starttime": start.to_iso8601_string(),
            "endtime": now.to_iso8601_string(),
            "latitude": KLD_CENTER_LAT,
            "longitude": KLD_CENTER_LON,
            "maxradiuskm": float(radius_km),
            "minmagnitude": float(min_mag),
            "eventtype": "earthquake",
            "orderby": "time",
        }
        resp = requests.get(USGS_EARTHQUAKE_QUERY_URL, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        payload = resp.json()
        features = payload.get("features")
        if not isinstance(features, list):
            return None
        events: List[Dict[str, Any]] = []
        for feature in features:
            if not isinstance(feature, dict):
                continue
            normalized = _normalize_event(feature)
            if normalized is not None:
                events.append(normalized)
        return sorted(events, key=lambda item: float(item.get("mag") or 0), reverse=True)
    except Exception:
        return None


def _format_mag(mag: Any) -> str:
    try:
        return f"M{float(mag):.1f}"
    except Exception:
        return "Mн/д"


def _format_local_time(event: Dict[str, Any], tz: str) -> str:
    try:
        return pendulum.parse(str(event.get("time_local"))).in_timezone(tz).format("HH:mm")
    except Exception:
        return ""


def _city_genitive(city: Any) -> str:
    name = str(city or "").strip()
    return KLD_CITY_GENITIVE.get(name, name or "Калининграда")


def build_kld_quake_line(
    events: Optional[List[Dict[str, Any]]],
    tz: str = "Europe/Kaliningrad",
    show_calm: bool = False,
) -> Optional[str]:
    """Build compact factual alert line; return None when quiet and show_calm is off."""
    if not events:
        if not show_calm:
            return None
        return "🌍 Сейсмика 24ч: спокойно — заметных землетрясений рядом не было."

    strongest = max(events, key=lambda item: float(item.get("mag") or 0))
    mag = float(strongest.get("mag") or 0)
    prefix = "⚠️ " if mag >= 4.0 else ""
    city = _city_genitive(strongest.get("nearest_city"))
    dist = strongest.get("distance_km")
    dist_txt = f"{int(round(float(dist)))} км" if isinstance(dist, (int, float)) else "рядом"
    depth = strongest.get("depth_km")
    depth_part = f", глубина {int(round(float(depth)))} км" if isinstance(depth, (int, float)) else ""
    time_txt = _format_local_time(strongest, tz)
    time_part = f", {time_txt}" if time_txt else ""
    return f"🌍 Сейсмика 24ч: {prefix}{_format_mag(mag)}, {dist_txt} от {city}{depth_part}{time_part}."
