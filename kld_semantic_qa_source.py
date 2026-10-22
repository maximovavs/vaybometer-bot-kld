"""R5-A offline-only strict Kaliningrad semantic weather/lunar source adapter.

Consumes a result of the already-running Open-Meteo visibility collector. Never
makes provider calls, guesses a good forecast, or authenticates an arbitrary
JSON sidecar. Transport trust ends at weather.get_visibility_weather(); the
embedded SHA-256 is an *integrity* check, not a cryptographic source signature.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json
import math
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from kld_semantic_qa_facts import FactsUnavailable, build_verified_facts
from weather_text import STORM_GUST_MS

KLD_TZ = ZoneInfo("Europe/Kaliningrad")
LAT, LON = 54.710426, 20.452214
SOURCE = "open-meteo-kld-hourly-v1"
CAPTURE_SCHEMA = "kld_r5_openmeteo_collector_capture_v1"
WINDOW_HOURS = tuple(range(4, 11))
MAX_CAPTURE_AGE = timedelta(hours=2)
# WMO interpretation is specifically for Open-Meteo; OpenWeather IDs are never WMO.
WMO_GROUPS = {
    0: "clear", 1: "partly_cloudy", 2: "partly_cloudy", 3: "cloudy",
    45: "fog", 48: "fog", 51: "drizzle", 53: "drizzle", 55: "drizzle",
    56: "rain", 57: "rain", 61: "rain", 63: "rain", 65: "rain",
    66: "rain", 67: "rain", 71: "snow", 73: "snow", 75: "snow",
    77: "snow", 80: "rain", 81: "rain", 82: "rain", 85: "snow",
    86: "snow", 95: "storm", 96: "storm", 99: "storm",
}
RANK = {name: i for i, name in enumerate(
    ("clear", "partly_cloudy", "cloudy", "fog", "drizzle", "rain", "snow", "storm")
)}
PHASES = ("new", "waxing_crescent", "first_quarter", "waxing_gibbous",
          "full", "waning_gibbous", "last_quarter", "waning_crescent")


def canonical_sha256(payload: Mapping[str, Any]) -> str:
    try:
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise FactsUnavailable("weather_payload_unserializable") from exc
    return sha256(raw).hexdigest()


def _number(value: Any, *, minimum: float = -math.inf, maximum: float = math.inf) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise FactsUnavailable("weather_numeric_invalid")
    result = float(value)
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise FactsUnavailable("weather_numeric_invalid")
    return result


def _utc_stamp(value: Any) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise FactsUnavailable("capture_timestamp_invalid")
    try:
        d = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise FactsUnavailable("capture_timestamp_invalid") from exc
    if d.utcoffset() != timedelta(0):
        raise FactsUnavailable("capture_timestamp_invalid")
    return d


def _lunar_source(day: date, at: datetime) -> dict[str, Any]:
    """Swiss-Ephemeris geometric phase for KLD 04/07/10; no calendar text."""
    try:
        import swisseph as swe
    except ImportError as exc:
        raise FactsUnavailable("lunar_ephemeris_unavailable") from exc

    def angle(hour: int) -> float:
        local = datetime(day.year, day.month, day.day, hour, tzinfo=KLD_TZ)
        utc = local.astimezone(timezone.utc)
        h = utc.hour + utc.minute / 60 + utc.second / 3600
        jd = swe.julday(utc.year, utc.month, utc.day, h)
        try:
            moon = swe.calc_ut(jd, swe.MOON, swe.FLG_MOSEPH)[0][0]
            sun = swe.calc_ut(jd, swe.SUN, swe.FLG_MOSEPH)[0][0]
        except Exception as exc:
            raise FactsUnavailable("lunar_ephemeris_unavailable") from exc
        v = (moon - sun) % 360.0
        if not math.isfinite(v):
            raise FactsUnavailable("lunar_ephemeris_unavailable")
        return v

    a4, a7, a10 = (angle(h) for h in (4, 7, 10))
    # All three snapshots must resolve to same eight-bin phase; otherwise
    # there is no single phase that describes the whole forecast window.
    phases = [PHASES[int(((a + 22.5) % 360) // 45)] for a in (a4, a7, a10)]
    if len(set(phases)) != 1:
        raise FactsUnavailable("lunar_window_phase_boundary")
    # A claimed quarter is a specific 90/270-degree event, NOT a generic
    # waxing/waning half-cycle. Require actual crossing on target KLD day.
    selected = phases[1]
    if selected in {"first_quarter", "last_quarter"}:
        a0 = angle(0)
        # Evaluate next local midnight independently (not the same-day 00:00).
        tomorrow = day + timedelta(days=1)
        midnight_next = datetime(tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=KLD_TZ).astimezone(timezone.utc)
        jd = swe.julday(midnight_next.year, midnight_next.month, midnight_next.day,
                        midnight_next.hour + midnight_next.minute / 60)
        try:
            a24 = (swe.calc_ut(jd, swe.MOON, swe.FLG_MOSEPH)[0][0]
                   - swe.calc_ut(jd, swe.SUN, swe.FLG_MOSEPH)[0][0]) % 360
        except Exception as exc:
            raise FactsUnavailable("lunar_ephemeris_unavailable") from exc
        target = 90.0 if selected == "first_quarter" else 270.0
        crossed = (target - a0) % 360 <= (a24 - a0) % 360
        if not crossed:
            selected = ("waxing_crescent" if a7 < 90 else "waxing_gibbous") if target == 90 else (
                "waning_gibbous" if a7 < 270 else "waning_crescent")
    evidence = {"date": day.isoformat(), "angles_deg": [round(a, 6) for a in (a4, a7, a10)],
                "phase": selected, "timezone": "Europe/Kaliningrad", "algorithm": "swe-moseph-elongation-v1"}
    return {"source_id": "swisseph-moseph-geocentric-v1", "raw_payload_sha256": canonical_sha256(evidence),
            "lunar_phase": selected, "angles_deg": evidence["angles_deg"], "retrieved_at_utc": at.isoformat().replace("+00:00", "Z")}


def build_trusted_snapshot(
    response: Mapping[str, Any] | None, *, target_date: str, post_type: str,
    publication_target_date: str, now_utc: datetime | None = None,
    scene_coordinates: tuple[float, float] = (LAT, LON),
) -> dict[str, Any]:
    """Strict producer from actual collector envelope; no fetch, no optimistic fill."""
    if post_type not in {"morning", "evening"} or target_date != publication_target_date:
        raise FactsUnavailable("publication_target_mismatch")
    try:
        day = date.fromisoformat(target_date)
    except (TypeError, ValueError) as exc:
        raise FactsUnavailable("target_date_invalid") from exc
    now = now_utc or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() != timedelta(0):
        raise FactsUnavailable("capture_timestamp_invalid")
    if not isinstance(response, Mapping):
        raise FactsUnavailable("weather_source_unavailable")
    meta = response.get("_kld_r5_capture")
    if not isinstance(meta, Mapping) or meta.get("schema") != CAPTURE_SCHEMA or meta.get("source_id") != SOURCE:
        raise FactsUnavailable("weather_source_unavailable")
    captured = _utc_stamp(meta.get("retrieved_at_utc"))
    if not timedelta(0) <= now - captured <= MAX_CAPTURE_AGE:
        raise FactsUnavailable("weather_source_stale_or_future")
    # A valid target-date label is not enough: today's morning cannot be
    # represented by tomorrow's data, nor vice versa.
    expected_civil = captured.astimezone(KLD_TZ).date() + (timedelta(days=1) if post_type == "evening" else timedelta(0))
    if day != expected_civil:
        raise FactsUnavailable("weather_publication_window_mismatch")
    if meta.get("requested_target_date") != target_date or meta.get("requested_timezone") != "Europe/Kaliningrad":
        raise FactsUnavailable("weather_window_mismatch")
    raw = {k: v for k, v in response.items() if k != "_kld_r5_capture"}
    if meta.get("raw_payload_sha256") != canonical_sha256(raw):
        raise FactsUnavailable("weather_payload_hash_mismatch")
    if response.get("timezone") != "Europe/Kaliningrad" or response.get("utc_offset_seconds") != 7200:
        raise FactsUnavailable("weather_timezone_mismatch")
    lat = _number(response.get("latitude"), minimum=-90, maximum=90)
    lon = _number(response.get("longitude"), minimum=-180, maximum=180)
    if abs(lat - LAT) > 0.07 or abs(lon - LON) > 0.07:
        raise FactsUnavailable("weather_location_mismatch")
    if (abs(scene_coordinates[0] - LAT) > 0.07 or abs(scene_coordinates[1] - LON) > 0.07):
        raise FactsUnavailable("scene_location_unverified")
    h = response.get("hourly")
    units = response.get("hourly_units")
    if not isinstance(h, Mapping) or not isinstance(units, Mapping):
        raise FactsUnavailable("weather_hourly_missing")
    if units.get("visibility") != "m" or units.get("rain") != "mm" or units.get("weathercode") not in {"wmo code", "wmo_code"}:
        raise FactsUnavailable("weather_units_invalid")
    wind_unit = units.get("wind_gusts_10m")
    if wind_unit not in {"km/h", "m/s"}:
        raise FactsUnavailable("weather_units_invalid")
    fields = ("time", "weathercode", "visibility", "rain", "wind_gusts_10m")
    arrs = [h.get(name) for name in fields]
    if not all(isinstance(a, list) and len(a) == len(arrs[0]) for a in arrs) or not arrs[0]:
        raise FactsUnavailable("weather_hourly_missing")
    timevalues = h["time"]
    if any(not isinstance(t, str) for t in timevalues):
        raise FactsUnavailable("weather_time_invalid")
    try:
        parsed = [datetime.fromisoformat(t) for t in timevalues]
    except ValueError as exc:
        raise FactsUnavailable("weather_time_invalid") from exc
    if any(t.tzinfo is not None or t.minute or t.second or t.microsecond for t in parsed):
        raise FactsUnavailable("weather_time_invalid")
    if any(a >= b for a, b in zip(parsed, parsed[1:])):
        raise FactsUnavailable("weather_time_duplicate_or_unordered")
    selected_idx = [i for i, stamp in enumerate(parsed) if stamp.date() == day and stamp.hour in WINDOW_HOURS]
    actual = [parsed[i].hour for i in selected_idx]
    if actual != list(WINDOW_HOURS):
        raise FactsUnavailable("weather_hourly_window_incomplete")
    labels: list[str] = []
    vis: list[float] = []
    rainfall: list[float] = []
    gust: list[float] = []
    codes: list[int] = []
    for i in selected_idx:
        code = h["weathercode"][i]
        if isinstance(code, bool) or not isinstance(code, int) or code not in WMO_GROUPS:
            raise FactsUnavailable("weather_wmo_invalid")
        codes.append(code)
        labels.append(WMO_GROUPS[code])
        vis.append(_number(h["visibility"][i], minimum=0, maximum=100_000))
        rainfall.append(_number(h["rain"][i], minimum=0, maximum=500))
        v = _number(h["wind_gusts_10m"][i], minimum=0, maximum=250)
        gust.append(v / 3.6 if wind_unit == "km/h" else v)
    # Any heavy precipitation in otherwise benign window is not proof of a
    # uniformly safe/clear scene. Avoid hiding severe sample via aggregation.
    if any(s in {"rain", "snow", "storm"} for s in labels) and any(s == "clear" for s in labels):
        raise FactsUnavailable("weather_window_mixed")
    if max(gust) >= STORM_GUST_MS:
        weather = "storm"
        storm_signal = "confirmed_gust"
    elif "storm" in labels:
        weather = "storm"
        storm_signal = "confirmed_weather"
    else:
        weather = max(labels, key=RANK.__getitem__)
        storm_signal = "none"
    if max(rainfall) > 0 and weather in {"clear", "partly_cloudy", "cloudy", "fog"}:
        weather = "rain"  # never let a rainy hour be reported as clear
    vis_min = min(vis)
    if vis_min <= 500:
        visibility_condition = "dense_fog"
    elif vis_min <= 1000:
        visibility_condition = "fog"
    elif vis_min <= 3000:
        visibility_condition = "mist"
    elif vis_min < 6000:
        visibility_condition = "reduced_visibility"
    else:
        visibility_condition = "clear"
    lunar = _lunar_source(day, captured)
    window = "current_morning" if post_type == "morning" else "tomorrow_morning"
    section = {"target_date": target_date, "source_status": "verified", "source_id": SOURCE,
               "retrieved_at_utc": meta["retrieved_at_utc"], "window": window,
               "raw_payload_sha256": meta["raw_payload_sha256"]}
    snapshot = {
        "capture_schema": "kld_r5_strict_producer_v1",
        "weather": dict(section, weather_main=weather, storm_signal=storm_signal,
                        observation_hours=[parsed[i].isoformat() for i in selected_idx],
                        max_gust_ms=max(gust), wmo_codes=codes,
                        official_warning_status="not_evaluated",
                        marine_wave_status="not_available_in_source"),
        "visibility": dict(section, visibility_condition=visibility_condition,
                           min_visibility_m=vis_min),
        "lunar": {"target_date": target_date, "source_status": "verified",
                  "source_id": lunar["source_id"], "retrieved_at_utc": lunar["retrieved_at_utc"],
                  "raw_payload_sha256": lunar["raw_payload_sha256"],
                  "lunar_phase": lunar["lunar_phase"], "angles_deg": lunar["angles_deg"]},
        "provenance": {"timezone": "Europe/Kaliningrad", "latitude": lat, "longitude": lon,
                       "source_id": SOURCE, "captured_at_utc": meta["retrieved_at_utc"],
                       "raw_payload_sha256": meta["raw_payload_sha256"],
                       "window_hours_local": list(WINDOW_HOURS), "source_schema": CAPTURE_SCHEMA},
    }
    # Final check through the *existing* R4 contract.
    build_verified_facts(snapshot, target_date=target_date, post_type=post_type,
                         publication_target_date=publication_target_date)
    return snapshot
