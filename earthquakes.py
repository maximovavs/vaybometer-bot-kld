#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Kaliningrad/KLD 24h earthquake line via regional and USGS FDSN catalogs."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
import os
from typing import Any, Dict, Iterable, List, Optional
from zoneinfo import ZoneInfo

import requests


SEISMICPORTAL_FDSN_URL = "https://www.seismicportal.eu/fdsnws/event/1/query"
USGS_EARTHQUAKE_QUERY_URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"

KLD_CENTER_LAT = 54.71
KLD_CENTER_LON = 20.51
DEFAULT_KLD_QUAKE_MIN_MAG = 0.9
DEFAULT_KLD_QUAKE_RADIUS_KM = 500.0
DEFAULT_KLD_QUAKE_HOURS = 24

KLD_CITY_COORDS = {
    "Калининград": (54.7104, 20.4522),
    "Светлогорск": (54.9439, 20.1514),
    "Зеленоградск": (54.9600, 20.4750),
    "Балтийск": (54.6544, 19.9094),
    "Черняховск": (54.6335, 21.8156),
    "Гусев": (54.5900, 22.2050),
    "Советск": (54.5070, 21.3470),
}

KLD_CITY_GENITIVE = {
    "Калининград": "Калининграда",
    "Светлогорск": "Светлогорска",
    "Зеленоградск": "Зеленоградска",
    "Балтийск": "Балтийска",
    "Черняховск": "Черняховска",
    "Гусев": "Гусева",
    "Советск": "Советска",
}

BAD_EVENT_TYPES = ("quarry", "blast", "explosion", "mine", "chemical", "nuclear", "sonic")
REQUEST_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "10"))


class KldQuakeEvents(list):
    def __init__(
        self,
        events: Iterable[Dict[str, Any]] = (),
        *,
        min_mag: float = DEFAULT_KLD_QUAKE_MIN_MAG,
        hours: int = DEFAULT_KLD_QUAKE_HOURS,
        radius_km: float = DEFAULT_KLD_QUAKE_RADIUS_KM,
        source_status: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> None:
        super().__init__(events)
        self.min_mag = float(min_mag)
        self.hours = int(hours)
        self.radius_km = float(radius_km)
        self.source_status = source_status or {}


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


def _parse_time(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(float(value) / (1000 if float(value) > 10_000_000_000 else 1), tz=timezone.utc)
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _event_type_text(props: Dict[str, Any]) -> str:
    for key in ("type", "eventtype", "event_type", "evtype"):
        value = props.get(key)
        if value:
            return str(value).lower()
    return ""


def _is_bad_event_type(props: Dict[str, Any]) -> bool:
    text = _event_type_text(props)
    return any(token in text for token in BAD_EVENT_TYPES)


def _status_rank(status: Any) -> int:
    text = str(status or "").lower()
    if any(word in text for word in ("reviewed", "manual", "confirmed", "final")):
        return 3
    if any(word in text for word in ("automatic", "prelim", "preliminary")):
        return 1
    return 2


def _city_genitive(city: Any) -> str:
    name = str(city or "").strip()
    return KLD_CITY_GENITIVE.get(name, name or "Калининграда")


def _normalize_common(
    *,
    source: str,
    source_event_id: str,
    mag: Any,
    place: str,
    time_value: Any,
    depth_km: Any,
    lat: Any,
    lon: Any,
    url: str,
    status: str = "",
    event_type: str = "earthquake",
    tz: str = "Europe/Kaliningrad",
) -> Optional[Dict[str, Any]]:
    try:
        mag_f = float(mag)
        lat_f = float(lat)
        lon_f = float(lon)
    except Exception:
        return None
    time_utc_dt = _parse_time(time_value)
    if time_utc_dt is None:
        return None
    try:
        depth = float(depth_km) if depth_km not in (None, "") else None
    except Exception:
        depth = None
    nearest_name, nearest_dist = _nearest_city(lat_f, lon_f)
    return {
        "source": source,
        "sources": [source],
        "source_event_id": str(source_event_id or ""),
        "mag": mag_f,
        "place": str(place or ""),
        "time_utc": time_utc_dt.isoformat().replace("+00:00", "Z"),
        "time_local": time_utc_dt.astimezone(ZoneInfo(tz)).isoformat(),
        "depth_km": depth,
        "lat": lat_f,
        "lon": lon_f,
        "distance_km": float(nearest_dist) if nearest_dist is not None else None,
        "distance_from_center_km": _haversine_km(KLD_CENTER_LAT, KLD_CENTER_LON, lat_f, lon_f),
        "nearest_city": nearest_name,
        "url": str(url or ""),
        "status": str(status or ""),
        "event_type": event_type or "earthquake",
    }


def _normalize_usgs_feature(feature: Dict[str, Any], tz: str = "Europe/Kaliningrad") -> Optional[Dict[str, Any]]:
    props = feature.get("properties") or {}
    if _is_bad_event_type(props):
        return None
    geom = feature.get("geometry") or {}
    coords = geom.get("coordinates") or []
    if len(coords) < 2:
        return None
    return _normalize_common(
        source="USGS",
        source_event_id=str(feature.get("id") or props.get("code") or ""),
        mag=props.get("mag"),
        place=str(props.get("place") or ""),
        time_value=props.get("time"),
        depth_km=coords[2] if len(coords) > 2 else None,
        lat=coords[1],
        lon=coords[0],
        url=str(props.get("url") or ""),
        status=str(props.get("status") or ""),
        event_type=_event_type_text(props) or "earthquake",
        tz=tz,
    )


def _normalize_regional_feature(feature: Dict[str, Any], tz: str = "Europe/Kaliningrad") -> Optional[Dict[str, Any]]:
    props = feature.get("properties") or {}
    if _is_bad_event_type(props):
        return None
    geom = feature.get("geometry") or {}
    coords = geom.get("coordinates") or []
    if len(coords) < 2:
        return None
    place = (
        props.get("flynn_region")
        or props.get("place")
        or props.get("region")
        or props.get("description")
        or ""
    )
    mag = props.get("mag") or props.get("magnitude") or props.get("mag_value")
    time_value = props.get("time") or props.get("datetime") or props.get("origin_time")
    depth = props.get("depth") or props.get("depth_km") or (coords[2] if len(coords) > 2 else None)
    status = props.get("evaluationStatus") or props.get("evaluationMode") or props.get("status") or ""
    event_id = feature.get("id") or props.get("unid") or props.get("source_id") or props.get("event_id") or ""
    return _normalize_common(
        source="EMSC",
        source_event_id=str(event_id),
        mag=mag,
        place=str(place or ""),
        time_value=time_value,
        depth_km=depth,
        lat=coords[1],
        lon=coords[0],
        url=str(props.get("url") or ""),
        status=str(status or ""),
        event_type=_event_type_text(props) or "earthquake",
        tz=tz,
    )


def fetch_regional_events(
    *,
    hours: int = DEFAULT_KLD_QUAKE_HOURS,
    radius_km: float = DEFAULT_KLD_QUAKE_RADIUS_KM,
    min_mag: float = DEFAULT_KLD_QUAKE_MIN_MAG,
    tz: str = "Europe/Kaliningrad",
) -> List[Dict[str, Any]]:
    now = datetime.now(timezone.utc)
    start = now - timedelta(hours=int(hours))
    params = {
        "format": "json",
        "starttime": start.isoformat().replace("+00:00", "Z"),
        "endtime": now.isoformat().replace("+00:00", "Z"),
        "lat": KLD_CENTER_LAT,
        "lon": KLD_CENTER_LON,
        "maxradius": float(radius_km) / 111.2,
        "minmag": float(min_mag),
        "orderby": "time",
    }
    resp = requests.get(SEISMICPORTAL_FDSN_URL, params=params, timeout=REQUEST_TIMEOUT)
    if resp.status_code == 204 or not (resp.text or "").strip():
        return []
    resp.raise_for_status()
    payload = resp.json()
    features = payload.get("features")
    if not isinstance(features, list):
        raise ValueError("regional earthquake payload has no features list")
    events = [_normalize_regional_feature(item, tz=tz) for item in features if isinstance(item, dict)]
    return [event for event in events if event is not None]


def fetch_usgs_events(
    *,
    hours: int = DEFAULT_KLD_QUAKE_HOURS,
    radius_km: float = DEFAULT_KLD_QUAKE_RADIUS_KM,
    min_mag: float = DEFAULT_KLD_QUAKE_MIN_MAG,
    tz: str = "Europe/Kaliningrad",
) -> List[Dict[str, Any]]:
    now = datetime.now(timezone.utc)
    start = now - timedelta(hours=int(hours))
    params = {
        "format": "geojson",
        "starttime": start.isoformat().replace("+00:00", "Z"),
        "endtime": now.isoformat().replace("+00:00", "Z"),
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
        raise ValueError("USGS earthquake payload has no features list")
    events = [_normalize_usgs_feature(item, tz=tz) for item in features if isinstance(item, dict)]
    return [event for event in events if event is not None]


def _event_time_seconds(event: Dict[str, Any]) -> float:
    parsed = _parse_time(event.get("time_utc"))
    return parsed.timestamp() if parsed else 0.0


def _events_duplicate(left: Dict[str, Any], right: Dict[str, Any]) -> bool:
    if abs(_event_time_seconds(left) - _event_time_seconds(right)) > 90:
        return False
    try:
        distance = _haversine_km(float(left["lat"]), float(left["lon"]), float(right["lat"]), float(right["lon"]))
        if distance > 30:
            return False
        return abs(float(left.get("mag") or 0) - float(right.get("mag") or 0)) <= 0.5
    except Exception:
        return False


def _better_event(left: Dict[str, Any], right: Dict[str, Any]) -> Dict[str, Any]:
    left_score = (2 if left.get("source") == "EMSC" else 1) + _status_rank(left.get("status"))
    right_score = (2 if right.get("source") == "EMSC" else 1) + _status_rank(right.get("status"))
    if sum(1 for value in right.values() if value not in (None, "")) > sum(1 for value in left.values() if value not in (None, "")):
        right_score += 1
    if left_score >= right_score:
        winner, loser = dict(left), right
    else:
        winner, loser = dict(right), left
    sources = []
    for source in list(winner.get("sources") or [winner.get("source")]) + list(loser.get("sources") or [loser.get("source")]):
        if source and source not in sources:
            sources.append(source)
    winner["sources"] = sources
    return winner


def deduplicate_events(events: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    merged: List[Dict[str, Any]] = []
    for event in events:
        replaced = False
        for idx, existing in enumerate(merged):
            if _events_duplicate(existing, event):
                merged[idx] = _better_event(existing, event)
                replaced = True
                break
        if not replaced:
            merged.append(event)
    return sorted(merged, key=lambda item: float(item.get("mag") or 0), reverse=True)


def _filter_events(
    events: Iterable[Dict[str, Any]],
    *,
    min_mag: float,
    radius_km: float,
    hours: int,
    now: datetime | None = None,
) -> List[Dict[str, Any]]:
    now_dt = now or datetime.now(timezone.utc)
    out: List[Dict[str, Any]] = []
    for event in events:
        try:
            if float(event.get("mag") or 0) < float(min_mag):
                continue
            if float(event.get("distance_from_center_km") or 10**9) > float(radius_km):
                continue
            parsed = _parse_time(event.get("time_utc"))
            if parsed is None:
                continue
            age_hours = (now_dt - parsed).total_seconds() / 3600
            if age_hours < -1 or age_hours > int(hours):
                continue
            if any(token in str(event.get("event_type") or "").lower() for token in BAD_EVENT_TYPES):
                continue
            out.append(event)
        except Exception:
            continue
    return out


def get_recent_earthquakes_kld(
    hours: int = DEFAULT_KLD_QUAKE_HOURS,
    radius_km: float = DEFAULT_KLD_QUAKE_RADIUS_KM,
    min_mag: float = DEFAULT_KLD_QUAKE_MIN_MAG,
) -> Optional[KldQuakeEvents]:
    source_status: Dict[str, Dict[str, Any]] = {}
    collected: List[Dict[str, Any]] = []

    try:
        regional = fetch_regional_events(hours=hours, radius_km=radius_km, min_mag=min_mag)
        source_status["regional"] = {"ok": True, "count": len(regional)}
        collected.extend(regional)
    except Exception as exc:
        source_status["regional"] = {"ok": False, "error": str(exc)}

    try:
        usgs = fetch_usgs_events(hours=hours, radius_km=radius_km, min_mag=min_mag)
        source_status["usgs"] = {"ok": True, "count": len(usgs)}
        collected.extend(usgs)
    except Exception as exc:
        source_status["usgs"] = {"ok": False, "error": str(exc)}

    if not any(status.get("ok") for status in source_status.values()):
        return None

    filtered = _filter_events(collected, min_mag=min_mag, radius_km=radius_km, hours=hours)
    return KldQuakeEvents(
        deduplicate_events(filtered),
        min_mag=min_mag,
        hours=hours,
        radius_km=radius_km,
        source_status=source_status,
    )


def _format_mag(mag: Any) -> str:
    try:
        return f"M{float(mag):.1f}"
    except Exception:
        return "Mн/д"


def _city_distance_phrase(event: Dict[str, Any]) -> str:
    city = _city_genitive(event.get("nearest_city"))
    dist = event.get("distance_km")
    if isinstance(dist, (int, float)):
        return f"{int(round(float(dist)))} км от {city}"
    return f"рядом с {city}"


def _micro_word(count: int) -> str:
    if count % 10 == 1 and count % 100 != 11:
        return "микрособытие"
    if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        return "микрособытия"
    return "микрособытий"


def _weak_word(count: int) -> str:
    if count % 10 == 1 and count % 100 != 11:
        return "слабое событие"
    if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        return "слабых события"
    return "слабых событий"


def _threshold_text(value: float) -> str:
    return f"M{float(value):.1f}+"


def _regional_failed_usgs_succeeded(events: Any) -> bool:
    status = getattr(events, "source_status", {}) or {}
    regional_ok = bool((status.get("regional") or {}).get("ok"))
    usgs_ok = bool((status.get("usgs") or {}).get("ok"))
    return not regional_ok and usgs_ok


def _usgs_fallback_part(event: Dict[str, Any]) -> str:
    mag = float(event.get("mag") or 0)
    if mag >= 4.0:
        depth = event.get("depth_km")
        depth_part = f", глубина {int(round(float(depth)))} км" if isinstance(depth, (int, float)) else ""
        return f" По USGS: ⚠️ {_format_mag(mag)}, {_city_distance_phrase(event)}{depth_part}."
    if mag >= 3.0:
        return f" По USGS: сильнейшее событие {_format_mag(mag)}, {_city_distance_phrase(event)}."
    if mag >= 2.0:
        return f" По USGS: слабое событие {_format_mag(mag)}, {_city_distance_phrase(event)}."
    return " По USGS: только микрособытия; региональный каталог нужен для проверки слабых событий."


def build_kld_quake_line(
    events: Optional[List[Dict[str, Any]]],
    tz: str = "Europe/Kaliningrad",
    show_calm: bool = False,
    *,
    min_mag: float = DEFAULT_KLD_QUAKE_MIN_MAG,
) -> Optional[str]:
    """Build a compact factual seismic line."""
    if events is None:
        return "🌍 Сейсмика: данные временно не обновились."

    threshold = float(getattr(events, "min_mag", min_mag))
    if _regional_failed_usgs_succeeded(events):
        if events:
            strongest = max(events, key=lambda item: float(item.get("mag") or 0))
            usgs_part = _usgs_fallback_part(strongest)
        else:
            usgs_part = " По каталогу USGS событий M2.5+ за 24 часа не найдено."
        return "🌍 Сейсмика: региональные данные по слабым событиям временно не обновились." + usgs_part

    if not events:
        return (
            "🌍 Сейсмика 24ч: по доступным региональным каталогам событий "
            f"{_threshold_text(threshold)} рядом с Калининградской областью не найдено."
        )

    clean_events = [event for event in events if float(event.get("mag") or 0) >= threshold]
    if not clean_events:
        return (
            "🌍 Сейсмика 24ч: по доступным региональным каталогам событий "
            f"{_threshold_text(threshold)} рядом с Калининградской областью не найдено."
        )

    micro = [event for event in clean_events if 0.9 <= float(event.get("mag") or 0) < 2.0]
    weak = [event for event in clean_events if 2.0 <= float(event.get("mag") or 0) < 3.0]
    strongest = max(clean_events, key=lambda item: float(item.get("mag") or 0))
    strongest_mag = float(strongest.get("mag") or 0)

    if strongest_mag >= 4.0:
        depth = strongest.get("depth_km")
        depth_part = f", глубина {int(round(float(depth)))} км" if isinstance(depth, (int, float)) else ""
        return f"🌍 Сейсмика 24ч: ⚠️ {_format_mag(strongest_mag)}, {_city_distance_phrase(strongest)}{depth_part}."

    if strongest_mag >= 3.0:
        return f"🌍 Сейсмика 24ч: сильнейшее событие {_format_mag(strongest_mag)}, {_city_distance_phrase(strongest)}."

    if weak:
        parts: List[str] = []
        if micro:
            parts.append(f"{len(micro)} {_micro_word(len(micro))}")
        parts.append(f"{len(weak)} {_weak_word(len(weak))}")
        return (
            "🌍 Сейсмика 24ч: "
            + " и ".join(parts)
            + f"; сильнейшее {_format_mag(strongest_mag)}, {_city_distance_phrase(strongest)}."
        )

    micro_count = len(micro)
    return (
        f"🌍 Сейсмика 24ч: {micro_count} {_micro_word(micro_count)} "
        "M0.9–1.9; заметных событий M2.0+ не найдено."
    )


__all__ = (
    "DEFAULT_KLD_QUAKE_MIN_MAG",
    "KldQuakeEvents",
    "_filter_events",
    "_haversine_km",
    "_nearest_city",
    "_normalize_regional_feature",
    "_normalize_usgs_feature",
    "build_kld_quake_line",
    "deduplicate_events",
    "fetch_regional_events",
    "fetch_usgs_events",
    "get_recent_earthquakes_kld",
)
