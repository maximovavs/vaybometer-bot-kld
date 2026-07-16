#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic Kaliningrad visibility, fog and haze classification.

The module is intentionally side-effect free: callers provide already-fetched
weather and optional air-quality mappings.  It performs no HTTP, Telegram, LLM,
image-provider or filesystem operations.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date as Date, datetime, timedelta
import math
from typing import Any, Mapping, Optional
from zoneinfo import ZoneInfo


DENSE_FOG_MAX_M = 500.0
FOG_MAX_M = 1000.0
MIST_MAX_M = 3000.0
REDUCED_VISIBILITY_MAX_M = 6000.0
MORNING_HOUR_FROM = 4
MORNING_HOUR_TO = 10

VISIBILITY_CONDITIONS = frozenset(
    {
        "dense_fog",
        "fog",
        "mist",
        "reduced_visibility",
        "dust_haze",
        "mixed_visibility",
        "clear",
    }
)


@dataclass(frozen=True)
class KldVisibilityContext:
    current_visibility_m: Optional[float] = None
    morning_min_visibility_m: Optional[float] = None
    humidity_pct: Optional[float] = None
    temperature_c: Optional[float] = None
    dew_point_c: Optional[float] = None
    dew_point_spread_c: Optional[float] = None
    weather_code: Optional[int] = None
    weather_code_source: Optional[str] = None
    aqi: Optional[float] = None
    pm25: Optional[float] = None
    pm10: Optional[float] = None
    condition: str = "clear"
    evidence_source: str = "unavailable"
    observation_time: Optional[str] = None
    target_date: Optional[str] = None
    confidence: str = "low"
    location_label: str = "Калининград"
    classification_reason: str = "no usable visibility evidence"

    @property
    def effective_visibility_m(self) -> Optional[float]:
        if self.evidence_source.startswith("current"):
            return self.current_visibility_m
        if self.evidence_source.startswith("hourly_morning"):
            return self.morning_min_visibility_m
        if self.morning_min_visibility_m is not None:
            return self.morning_min_visibility_m
        return self.current_visibility_m


def normalize_number(value: Any, *, non_negative: bool = False) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        number = float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    if non_negative and number < 0:
        return None
    return number


def normalize_visibility_m(value: Any) -> Optional[float]:
    return normalize_number(value, non_negative=True)


def _normalize_humidity(value: Any) -> Optional[float]:
    humidity = normalize_number(value, non_negative=True)
    if humidity is None or humidity > 100:
        return None
    return humidity


def dew_point_spread_c(temperature_c: Any, dew_point_c: Any) -> Optional[float]:
    temperature = normalize_number(temperature_c)
    dew_point = normalize_number(dew_point_c)
    if temperature is None or dew_point is None:
        return None
    return max(0.0, temperature - dew_point)


def _weather_code(value: Any) -> Optional[int]:
    number = normalize_number(value, non_negative=True)
    return int(number) if number is not None else None


def _first_present(*values: Any) -> Any:
    return next((value for value in values if value is not None), None)


def _air_value(air_data: Optional[Mapping[str, Any]], *keys: str) -> Optional[float]:
    data = air_data if isinstance(air_data, Mapping) else {}
    for key in keys:
        if key in data:
            return normalize_number(data.get(key), non_negative=True)
    return None


def _target_date_value(target_date: Any, post_type: str, tz_name: str) -> Date:
    if isinstance(target_date, datetime):
        return target_date.date()
    if isinstance(target_date, Date):
        return target_date
    if isinstance(target_date, str) and target_date.strip():
        try:
            return Date.fromisoformat(target_date.strip()[:10])
        except ValueError:
            pass
    today = datetime.now(ZoneInfo(tz_name)).date()
    return today if post_type.startswith("morn") else today + timedelta(days=1)


def _safe_zone(name: Any, fallback: str) -> ZoneInfo:
    try:
        return ZoneInfo(str(name or fallback))
    except Exception:
        return ZoneInfo(fallback)


def _local_datetime(value: Any, tz_name: str, source_tz: Any = None) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    target_zone = _safe_zone(tz_name, "Europe/Kaliningrad")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_safe_zone(source_tz, tz_name))
    try:
        return parsed.astimezone(target_zone)
    except Exception:
        return parsed


def _support_flags(
    *,
    humidity_pct: Optional[float],
    spread_c: Optional[float],
    weather_code: Optional[int],
    aqi: Optional[float],
    pm25: Optional[float],
    pm10: Optional[float],
) -> dict[str, bool]:
    fog_code = weather_code in {45, 48}
    wet_support = bool(
        fog_code
        or (humidity_pct is not None and humidity_pct >= 90)
        or (spread_c is not None and spread_c <= 2.0)
    )
    strong_wet_support = bool(
        fog_code
        or (humidity_pct is not None and humidity_pct >= 93)
        or (spread_c is not None and spread_c <= 1.5)
    )
    pollution_support = bool(
        (aqi is not None and aqi >= 100)
        or (pm25 is not None and pm25 >= 35)
        or (pm10 is not None and pm10 >= 80)
    )
    dry_support = bool(
        humidity_pct is not None
        and humidity_pct <= 60
        and (spread_c is None or spread_c >= 4.0)
    )
    return {
        "fog_code": fog_code,
        "wet_support": wet_support,
        "strong_wet_support": strong_wet_support,
        "pollution_support": pollution_support,
        "dry_support": dry_support,
    }


def classify_visibility_values(
    *,
    visibility_m: Any,
    humidity_pct: Any = None,
    temperature_c: Any = None,
    dew_point_c: Any = None,
    weather_code: Any = None,
    aqi: Any = None,
    pm25: Any = None,
    pm10: Any = None,
) -> tuple[str, str, str]:
    """Return ``(condition, confidence, reason)`` for normalized evidence."""
    visibility = normalize_visibility_m(visibility_m)
    humidity = _normalize_humidity(humidity_pct)
    temperature = normalize_number(temperature_c)
    dew_point = normalize_number(dew_point_c)
    spread = dew_point_spread_c(temperature, dew_point)
    code = _weather_code(weather_code)
    aqi_value = normalize_number(aqi, non_negative=True)
    pm25_value = normalize_number(pm25, non_negative=True)
    pm10_value = normalize_number(pm10, non_negative=True)
    support = _support_flags(
        humidity_pct=humidity,
        spread_c=spread,
        weather_code=code,
        aqi=aqi_value,
        pm25=pm25_value,
        pm10=pm10_value,
    )

    evidence: list[str] = []
    if support["fog_code"]:
        evidence.append(f"WMO {code}")
    if humidity is not None:
        evidence.append(f"RH {humidity:g}%")
    if spread is not None:
        evidence.append(f"spread {spread:g}°C")
    if support["pollution_support"]:
        evidence.append("pollution support")
    evidence_text = ", ".join(evidence) or "limited supporting evidence"

    if visibility is not None:
        if visibility <= REDUCED_VISIBILITY_MAX_M and support["wet_support"] and support["pollution_support"]:
            return "mixed_visibility", "high", f"visibility {visibility:g} m with wet and pollution support ({evidence_text})"
        if visibility <= DENSE_FOG_MAX_M and support["strong_wet_support"]:
            return "dense_fog", "high", f"visibility {visibility:g} m with strong wet support ({evidence_text})"
        if visibility <= FOG_MAX_M and support["wet_support"]:
            return "fog", "high", f"visibility {visibility:g} m with wet support ({evidence_text})"
        if FOG_MAX_M < visibility <= MIST_MAX_M and support["wet_support"]:
            return "mist", "high", f"visibility {visibility:g} m with wet support ({evidence_text})"
        if visibility <= REDUCED_VISIBILITY_MAX_M and support["dry_support"] and support["pollution_support"]:
            return "dust_haze", "high", f"visibility {visibility:g} m with dry pollution support ({evidence_text})"
        if visibility <= REDUCED_VISIBILITY_MAX_M:
            return "reduced_visibility", "medium", f"visibility {visibility:g} m without sufficient fog or dust evidence ({evidence_text})"
        return "clear", "high", f"visibility {visibility:g} m is above reduced-visibility threshold"

    if support["fog_code"] and support["wet_support"]:
        return "fog", "medium", f"WMO fog code without numeric visibility ({evidence_text})"
    if support["dry_support"] and support["pollution_support"]:
        return "dust_haze", "medium", f"dry pollution support without numeric visibility ({evidence_text})"
    if support["wet_support"] and support["pollution_support"]:
        return "mixed_visibility", "medium", f"wet and pollution support without numeric visibility ({evidence_text})"
    return "clear", "low", f"no numeric visibility and insufficient alert evidence ({evidence_text})"


def _record_from_mapping(data: Mapping[str, Any], *, source: str) -> dict[str, Any]:
    temperature = normalize_number(_first_present(data.get("temperature_2m"), data.get("temperature")))
    dew_point = normalize_number(_first_present(data.get("dew_point_2m"), data.get("dewpoint_2m"), data.get("dew_point")))
    return {
        "visibility": normalize_visibility_m(data.get("visibility")),
        "humidity": _normalize_humidity(_first_present(data.get("relative_humidity_2m"), data.get("humidity"))),
        "temperature": temperature,
        "dew_point": dew_point,
        "spread": dew_point_spread_c(temperature, dew_point),
        "weather_code": _weather_code(_first_present(data.get("weather_code"), data.get("weathercode"))),
        "time": str(data.get("time") or "") or None,
        "source": source,
    }


def _hourly_value(hourly: Mapping[str, Any], key: str, index: int) -> Any:
    values = hourly.get(key)
    if not isinstance(values, list) or index < 0 or index >= len(values):
        return None
    return values[index]


def _morning_records(
    payload: Mapping[str, Any],
    *,
    target: Date,
    tz_name: str,
) -> list[dict[str, Any]]:
    hourly = payload.get("hourly") if isinstance(payload.get("hourly"), Mapping) else {}
    times = hourly.get("time_local") or hourly.get("time") or []
    source_tz = payload.get("timezone") or payload.get("timezone_abbreviation") or tz_name
    records: list[dict[str, Any]] = []
    if not isinstance(times, list):
        return records
    for index, raw_time in enumerate(times):
        local_dt = _local_datetime(raw_time, tz_name, source_tz)
        if local_dt is None or local_dt.date() != target or not (MORNING_HOUR_FROM <= local_dt.hour <= MORNING_HOUR_TO):
            continue
        records.append(
            _record_from_mapping(
                {
                    "visibility": _hourly_value(hourly, "visibility", index),
                    "relative_humidity_2m": _hourly_value(hourly, "relative_humidity_2m", index),
                    "temperature_2m": _hourly_value(hourly, "temperature_2m", index),
                    "dew_point_2m": _first_present(
                        _hourly_value(hourly, "dew_point_2m", index),
                        _hourly_value(hourly, "dewpoint_2m", index),
                    ),
                    "weather_code": _first_present(
                        _hourly_value(hourly, "weather_code", index),
                        _hourly_value(hourly, "weathercode", index),
                    ),
                    "time": raw_time,
                },
                source="hourly_morning",
            )
        )
    return records


def visibility_payload_has_morning_window(
    weather_data: Optional[Mapping[str, Any]],
    *,
    target_date: Any,
    tz: str = "Europe/Kaliningrad",
) -> bool:
    payload = weather_data if isinstance(weather_data, Mapping) else {}
    target = _target_date_value(target_date, "morning", tz)
    for record in _morning_records(payload, target=target, tz_name=tz):
        if any(record.get(key) is not None for key in ("visibility", "humidity", "dew_point", "weather_code")):
            return True
    return False


def get_kld_visibility_context(
    weather_data: Optional[Mapping[str, Any]],
    *,
    post_type: str = "morning",
    target_date: Any = None,
    tz: str = "Europe/Kaliningrad",
    air_data: Optional[Mapping[str, Any]] = None,
    location_label: str = "Калининград",
) -> KldVisibilityContext:
    payload = weather_data if isinstance(weather_data, Mapping) else {}
    target = _target_date_value(target_date, post_type, tz)
    current_data = payload.get("current") if isinstance(payload.get("current"), Mapping) else {}
    current_record = _record_from_mapping(current_data, source="current")
    morning_records = _morning_records(payload, target=target, tz_name=tz)

    numeric_morning = [record for record in morning_records if record["visibility"] is not None]
    morning_min_record = min(numeric_morning, key=lambda item: item["visibility"]) if numeric_morning else None
    morning_min = morning_min_record["visibility"] if morning_min_record else None
    current_visibility = current_record["visibility"]

    numeric_candidates: list[dict[str, Any]] = []
    if morning_min_record:
        numeric_candidates.append(morning_min_record)
    if post_type.startswith("morn") and current_visibility is not None:
        numeric_candidates.append(current_record)
    selected = min(numeric_candidates, key=lambda item: item["visibility"]) if numeric_candidates else None

    if selected is None:
        fallback_records = list(morning_records)
        if post_type.startswith("morn"):
            fallback_records.append(current_record)
        selected = next((record for record in fallback_records if record["weather_code"] in {45, 48}), None)
        if selected is None:
            selected = next(
                (
                    record
                    for record in fallback_records
                    if record["humidity"] is not None or record["spread"] is not None
                ),
                None,
            )

    aqi = _air_value(air_data, "aqi")
    pm25 = _air_value(air_data, "pm25", "pm2_5", "pm2.5")
    pm10 = _air_value(air_data, "pm10")
    selected = selected or {
        "visibility": None,
        "humidity": None,
        "temperature": None,
        "dew_point": None,
        "spread": None,
        "weather_code": None,
        "time": None,
        "source": "unavailable",
    }
    condition, confidence, reason = classify_visibility_values(
        visibility_m=selected["visibility"],
        humidity_pct=selected["humidity"],
        temperature_c=selected["temperature"],
        dew_point_c=selected["dew_point"],
        weather_code=selected["weather_code"],
        aqi=aqi,
        pm25=pm25,
        pm10=pm10,
    )
    source = str(selected.get("source") or "unavailable")
    if any(value is not None for value in (aqi, pm25, pm10)):
        source += "+air_quality"

    return KldVisibilityContext(
        current_visibility_m=current_visibility,
        morning_min_visibility_m=morning_min,
        humidity_pct=selected["humidity"],
        temperature_c=selected["temperature"],
        dew_point_c=selected["dew_point"],
        dew_point_spread_c=selected["spread"],
        weather_code=selected["weather_code"],
        weather_code_source=selected["source"] if selected["weather_code"] is not None else None,
        aqi=aqi,
        pm25=pm25,
        pm10=pm10,
        condition=condition,
        evidence_source=source,
        observation_time=selected["time"],
        target_date=target.isoformat(),
        confidence=confidence,
        location_label=location_label,
        classification_reason=reason,
    )


def build_kld_visibility_line(
    context: KldVisibilityContext,
    *,
    post_type: str = "morning",
) -> Optional[str]:
    timing = "утром" if post_type.startswith("morn") else "завтра утром"
    value = context.effective_visibility_m
    distance = f"; местами около {int(round(value))} м" if value is not None else ""
    if context.condition == "dense_fog":
        threshold = "; местами видимость может падать ниже 500 м" if value is not None else ""
        return f"🌫 Видимость: {timing} сильный туман{threshold}; осторожнее на дорогах и у моря."
    if context.condition == "fog":
        return f"🌫 Видимость: {timing} местами туман и низкая облачность{distance}; дальние объекты различимы хуже."
    if context.condition == "mist":
        return f"🌫 Видимость: {timing} влажная дымка{distance}; дальние ориентиры и береговая линия различимы хуже обычного."
    if context.condition == "reduced_visibility":
        return f"🌫 Видимость: {timing} местами снижена{distance}; на дорогах и у побережья обзор короче обычного."
    if context.condition == "dust_haze":
        return f"🌫 Видимость: {timing} сухая дымка{distance}; воздух и дальняя видимость хуже обычного."
    if context.condition == "mixed_visibility":
        return f"🌫 Видимость: {timing} снижена{distance}; возможна смесь влажной дымки и загрязнения воздуха."
    return None


def visibility_condition_from_text(text: str) -> str:
    low = str(text or "").lower()
    if "смесь влажной дымки и загрязнения" in low:
        return "mixed_visibility"
    if "видимость:" in low and "сухая дымка" in low:
        return "dust_haze"
    if "видимость:" in low and "сильный туман" in low:
        return "dense_fog"
    if "видимость:" in low and "туман" in low:
        return "fog"
    if "видимость:" in low and "влажная дымка" in low:
        return "mist"
    if "видимость:" in low and "снижен" in low:
        return "reduced_visibility"
    return "clear"


def visibility_penalty(context_or_condition: Any) -> float:
    if isinstance(context_or_condition, KldVisibilityContext):
        condition = context_or_condition.condition
    elif hasattr(context_or_condition, "visibility_condition"):
        condition = str(getattr(context_or_condition, "visibility_condition") or "clear")
    else:
        condition = str(context_or_condition or "clear")
    if condition in {"dense_fog", "fog", "mixed_visibility"}:
        return 0.5
    if condition in {"mist", "reduced_visibility", "dust_haze"}:
        return 0.2
    return 0.0


def visibility_air_penalty(context_or_condition: Any, air_penalty: Any) -> float:
    existing_air = normalize_number(air_penalty, non_negative=True) or 0.0
    return max(existing_air, visibility_penalty(context_or_condition))


def visibility_reason(condition: Any) -> str:
    value = str(condition or "clear")
    if value in {"dense_fog", "fog"}:
        return "утренний туман"
    if value in {"mist", "reduced_visibility"}:
        return "видимость снижена"
    if value == "mixed_visibility":
        return "видимость и воздух хуже"
    if value == "dust_haze":
        return "воздух и дальность обзора хуже"
    return ""


def visibility_diagnostics(
    context: KldVisibilityContext,
    *,
    air_penalty: float = 0.0,
    fog_text_added: bool,
    fog_visual_rule: bool,
) -> dict[str, Any]:
    payload = asdict(context)
    payload.update(
        {
            "visibility_condition": context.condition,
            "score_penalty": visibility_air_penalty(context, air_penalty),
            "fog_text_added": bool(fog_text_added),
            "fog_visual_rule": bool(fog_visual_rule),
            "dust_vs_fog_classification": context.condition,
        }
    )
    return payload


__all__ = [
    "DENSE_FOG_MAX_M",
    "FOG_MAX_M",
    "KldVisibilityContext",
    "MIST_MAX_M",
    "REDUCED_VISIBILITY_MAX_M",
    "VISIBILITY_CONDITIONS",
    "build_kld_visibility_line",
    "classify_visibility_values",
    "dew_point_spread_c",
    "get_kld_visibility_context",
    "normalize_number",
    "normalize_visibility_m",
    "visibility_air_penalty",
    "visibility_condition_from_text",
    "visibility_diagnostics",
    "visibility_payload_has_morning_window",
    "visibility_penalty",
    "visibility_reason",
]
