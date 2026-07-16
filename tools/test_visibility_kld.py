#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline checks for KLD fog, haze and visibility context.

No check in this module performs HTTP, image generation or Telegram sending.
"""
from __future__ import annotations

import datetime as dt
import os
import re
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("TELEGRAM_TOKEN_KLG", "test-token")
os.environ.setdefault("CHANNEL_ID_KLG", "test-channel")

telegram_stub = types.ModuleType("telegram")
telegram_stub.Bot = object
telegram_stub.constants = types.SimpleNamespace(ParseMode=types.SimpleNamespace(HTML="HTML"))
sys.modules.setdefault("telegram", telegram_stub)

from format_v2 import build_evening_format_v2, build_morning_format_v2  # noqa: E402
from image_prompt_kld import (  # noqa: E402
    build_kld_evening_prompt,
    kld_lunar_prompt_diagnostics,
    kld_scene_metadata,
    kld_visual_cache_key,
)
from image_prompt_kld_morning import build_kld_morning_prompt  # noqa: E402
from safe_test_post import _kld_evening_score_line, _kld_score_line  # noqa: E402
from visibility_context import (  # noqa: E402
    KldVisibilityContext,
    build_kld_visibility_line,
    classify_visibility_values,
    dew_point_spread_c,
    get_kld_visibility_context,
    normalize_visibility_m,
    visibility_air_penalty,
    visibility_diagnostics,
    visibility_penalty,
)
from visual_context_kld import build_visual_context  # noqa: E402
from visual_rules import apply_visual_rules, build_prompt_from_cues  # noqa: E402
import post_common  # noqa: E402
import weather  # noqa: E402


DATE = dt.date(2026, 7, 16)
NEXT_DATE = DATE + dt.timedelta(days=1)


def _payload(
    rows: list[tuple[str, object, object, object, object, object]],
    *,
    current: dict[str, object] | None = None,
) -> dict[str, object]:
    """Build an Open-Meteo-shaped fixture.

    Each row is time, visibility, RH, temperature, dew point and WMO code.
    """
    return {
        "timezone": "Europe/Kaliningrad",
        "current": current or {},
        "hourly": {
            "time": [row[0] for row in rows],
            "visibility": [row[1] for row in rows],
            "relative_humidity_2m": [row[2] for row in rows],
            "temperature_2m": [row[3] for row in rows],
            "dew_point_2m": [row[4] for row in rows],
            "weathercode": [row[5] for row in rows],
        },
    }


def _context(
    visibility: object,
    humidity: object,
    code: object = 3,
    *,
    temperature: object = 8,
    dew_point: object = 7,
    air: dict[str, object] | None = None,
    post_type: str = "morning",
    target: dt.date = DATE,
):
    return get_kld_visibility_context(
        _payload(
            [(f"{target.isoformat()}T06:00", visibility, humidity, temperature, dew_point, code)],
        ),
        post_type=post_type,
        target_date=target,
        air_data=air or {},
    )


def _score_value(text: str) -> float:
    match = re.search(r"VayboMeter(?: завтра)?:\s*(\d+(?:[.,]\d+)?)/10", text)
    assert match, text
    return float(match.group(1).replace(",", "."))


def _morning_source(visibility_line: str = "", *, aqi: int = 22) -> str:
    parts = [
        "<b>🌅 Калининградская область: погода на сегодня (16.07.2026)</b>",
        "✨ VayboMeter сегодня: 9.8/10 — отличный день.",
        "Погода: 🏙️ Калининград — 22/14 °C • ☀️ ясно • 💨 2 м/с.",
        "🌡 По области: днём теплее всего — Черняховск 25°, прохладнее — Балтийск 19°; холоднее ночью — Гусев 10°.",
        "Черняховск: 25/12 °C • ☀️ ясно",
        "Балтийск: 19/13 °C • ☀️ ясно • 🌊 17°C • волна 0.2 м",
        f"🏭 Воздух: 🟢 низкий (AQI {aqi}) • PM₂.₅ 5 / PM₁₀ 10",
    ]
    if visibility_line:
        parts.append(visibility_line)
    parts.extend(("✅ Сегодня: обычные дела и прогулка.", "#Калининград #погода #здоровье #сегодня #море"))
    return "\n".join(parts)


def _evening_source(visibility_line: str, moon_line: str) -> str:
    return "\n".join(
        (
            "<b>🌅 Калининградская область: погода на завтра (17.07.2026)</b>",
            "Погода: 🏙️ Калининград — 21/13 °C • ☁️ облачно • 💨 3 м/с.",
            "🌊 <b>Морские города</b>",
            "Светлогорск: 18/13 °C • ☁️ облачно • 🌊 17°C • волна 0.2 м",
            visibility_line,
            "🌅 Рассвет завтра: 04:26",
            "🌇 Закат завтра: 21:05",
            "📻 <b>Астрособытия</b>",
            moon_line,
            "💚 В плюсе: спокойные планы и восстановление.",
            "✅ <b>Рекомендации</b>",
            "#Калининград #погода #здоровье #море",
        )
    )


def dense_fog_case_a() -> None:
    ctx = _context(300, 97, 45, temperature=6, dew_point=5.7)
    assert ctx.condition == "dense_fog"
    line = build_kld_visibility_line(ctx, post_type="morning")
    assert line and "сильный туман" in line and "ниже 500 м" in line
    assert visibility_penalty(ctx) == 0.5
    diagnostics = visibility_diagnostics(ctx, air_penalty=0.0, fog_text_added=True, fog_visual_rule=True)
    for key in (
        "visibility_condition",
        "current_visibility_m",
        "morning_min_visibility_m",
        "humidity_pct",
        "temperature_c",
        "dew_point_c",
        "dew_point_spread_c",
        "weather_code",
        "aqi",
        "pm25",
        "pm10",
        "evidence_source",
        "observation_time",
        "classification_reason",
        "score_penalty",
        "fog_text_added",
        "fog_visual_rule",
        "dust_vs_fog_classification",
    ):
        assert key in diagnostics


def fog_case_b() -> None:
    ctx = _context(900, 94, temperature=7, dew_point=6)
    assert ctx.condition == "fog"
    assert "местами туман" in (build_kld_visibility_line(ctx) or "")


def mist_case_c() -> None:
    ctx = _context(2200, 91, temperature=8, dew_point=7)
    assert ctx.condition == "mist"
    assert "влажная дымка" in (build_kld_visibility_line(ctx) or "")


def dust_haze_case_d() -> None:
    ctx = _context(
        4000,
        40,
        temperature=24,
        dew_point=16,
        air={"aqi": 130, "pm25": 45, "pm10": 95},
    )
    assert ctx.condition == "dust_haze"
    assert "сухая дымка" in (build_kld_visibility_line(ctx) or "")


def mixed_visibility_case_e() -> None:
    ctx = _context(
        600,
        96,
        temperature=7,
        dew_point=6.5,
        air={"aqi": 130, "pm25": 45, "pm10": 95},
    )
    assert ctx.condition == "mixed_visibility"
    assert "смесь влажной дымки и загрязнения" in (build_kld_visibility_line(ctx) or "")
    assert visibility_air_penalty(ctx, 0.8) == 0.8


def morning_window_case_f() -> None:
    payload = _payload(
        [
            ("2026-07-16T06:00", 300, 97, 6, 5.6, 45),
            ("2026-07-16T09:00", 2500, 90, 9, 8, 3),
            ("2026-07-16T11:00", 18000, 65, 14, 8, 1),
        ],
        current={"visibility": 16000, "relative_humidity_2m": 65, "temperature_2m": 14, "dew_point_2m": 8, "weathercode": 1, "time": "2026-07-16T11:00"},
    )
    ctx = get_kld_visibility_context(payload, post_type="morning", target_date=DATE)
    line = build_kld_visibility_line(ctx, post_type="morning") or ""
    assert ctx.condition == "dense_fog"
    assert ctx.morning_min_visibility_m == 300
    assert "утром" in line
    assert "весь день" not in line and "целый день" not in line


def evening_next_morning_case_g() -> None:
    payload = _payload(
        [
            ("2026-07-17T05:00", 900, 95, 7, 6.5, 45),
            ("2026-07-17T08:00", 1800, 92, 9, 8, 3),
            ("2026-07-17T11:00", 16000, 66, 15, 9, 1),
        ],
        current={"visibility": 200, "relative_humidity_2m": 99, "temperature_2m": 5, "dew_point_2m": 5, "weathercode": 45, "time": "2026-07-16T20:00"},
    )
    ctx = get_kld_visibility_context(payload, post_type="evening", target_date=NEXT_DATE)
    line = build_kld_visibility_line(ctx, post_type="evening") or ""
    assert ctx.condition == "fog"
    assert ctx.morning_min_visibility_m == 900
    assert "завтра утром" in line


def clear_case_h() -> None:
    ctx = _context(16000, 65, 1, temperature=15, dew_point=8)
    assert ctx.condition == "clear"
    assert build_kld_visibility_line(ctx) is None
    assert visibility_penalty(ctx) == 0.0


def invalid_values_and_wmo_fallback() -> None:
    assert normalize_visibility_m(float("nan")) is None
    assert normalize_visibility_m(float("inf")) is None
    assert normalize_visibility_m(-1) is None
    assert dew_point_spread_c(5, 8) == 0.0
    condition, confidence, _reason = classify_visibility_values(
        visibility_m="bad",
        humidity_pct=95,
        temperature_c=7,
        dew_point_c=6,
        weather_code=45,
    )
    assert condition == "fog" and confidence == "medium"


def weather_fetch_fields_are_requested_offline() -> None:
    calls: list[dict[str, object]] = []
    original = weather._safe_http_get

    def fake_get(_url, **kwargs):
        calls.append(kwargs)
        if "hourly" in kwargs and "daily" in kwargs:
            return {"timezone": "Europe/Kaliningrad", "current_weather": {}, "current": {}, "hourly": {}, "daily": {}}
        return {"current_weather": {}, "current": {}}

    try:
        weather._safe_http_get = fake_get
        weather._openmeteo_hourly_daily(54.71, 20.51, "Europe/Kaliningrad", "2026-07-16", "2026-07-16")
        weather._openmeteo(54.71, 20.51)
        weather._openmeteo_current_only(54.71, 20.51)
    finally:
        weather._safe_http_get = original

    assert len(calls) == 3
    for params in calls:
        current = str(params.get("current") or "")
        assert "visibility" in current
        assert "relative_humidity_2m" in current
        assert "dew_point_2m" in current
        assert "weather_code" in current
    for params in calls[:2]:
        hourly = str(params.get("hourly") or "")
        assert "visibility" in hourly
        assert "relative_humidity_2m" in hourly
        assert "dew_point_2m" in hourly


def post_builder_visibility_fallback_is_nonfatal() -> None:
    fallback_payload = _payload(
        [("2026-07-16T06:00", 900, 95, 7, 6.5, 45)],
    )
    original = post_common.get_visibility_weather
    calls: list[str] = []

    def fake_fetch(_lat, _lon, *, tz, target_date):
        calls.append(f"{tz}|{target_date}")
        return fallback_payload

    try:
        post_common.get_visibility_weather = fake_fetch
        ctx, line = post_common._kld_visibility_for_post(
            {},
            {"aqi": 22, "pm25": 5, "pm10": 10},
            post_type="morning",
            target_date=DATE.isoformat(),
            tz_name="Europe/Kaliningrad",
        )
        assert ctx.condition == "fog" and line and "местами туман" in line
        assert calls == ["Europe/Kaliningrad|2026-07-16"]

        def failed_fetch(*_args, **_kwargs):
            raise RuntimeError("synthetic source failure")

        post_common.get_visibility_weather = failed_fetch
        clear_ctx, clear_line = post_common._kld_visibility_for_post(
            {},
            {},
            post_type="morning",
            target_date=DATE.isoformat(),
            tz_name="Europe/Kaliningrad",
        )
        assert clear_ctx.condition == "clear"
        assert clear_line is None
    finally:
        post_common.get_visibility_weather = original


def _production_evening_visibility(
    visibility: object,
    humidity: object,
    *,
    temperature: object,
    dew_point: object,
    weather_code: object = 3,
    forecast_air_data: dict[str, object] | None = None,
):
    payload = _payload(
        [
            (
                f"{NEXT_DATE.isoformat()}T06:00",
                visibility,
                humidity,
                temperature,
                dew_point,
                weather_code,
            )
        ]
    )
    return post_common._kld_visibility_for_post(
        payload,
        {"aqi": 150, "pm25": 55, "pm10": 110},
        post_type="evening",
        target_date=NEXT_DATE.isoformat(),
        tz_name="Europe/Kaliningrad",
        forecast_air_data=forecast_air_data,
    )


def evening_current_air_does_not_create_dust_case_a() -> None:
    ctx, line = _production_evening_visibility(
        4000,
        40,
        temperature=24,
        dew_point=16,
    )
    assert ctx.condition == "reduced_visibility"
    assert ctx.condition != "dust_haze"
    assert ctx.aqi is None and ctx.pm25 is None and ctx.pm10 is None
    assert line and "местами снижена" in line and "сухая дымка" not in line


def evening_current_air_does_not_make_fog_mixed_case_b() -> None:
    ctx, line = _production_evening_visibility(
        900,
        95,
        temperature=7,
        dew_point=6.5,
        weather_code=45,
    )
    assert ctx.condition == "fog"
    assert ctx.condition != "mixed_visibility"
    assert line and "местами туман" in line and "загрязнения" not in line


def evening_current_air_does_not_create_clear_haze_case_c() -> None:
    ctx, line = _production_evening_visibility(
        16000,
        40,
        temperature=24,
        dew_point=16,
    )
    assert ctx.condition == "clear"
    assert line is None


def evening_fog_score_ignores_current_air_case_d() -> None:
    ctx, line = _production_evening_visibility(
        900,
        95,
        temperature=7,
        dew_point=6.5,
        weather_code=45,
    )
    assert line
    weather_text = "🏙️ Калининград: дн/ночь 22/14 °C • ясно • 💨 2 м/с."
    base_score = _score_value(_kld_evening_score_line(weather_text))
    fog_score = _score_value(_kld_evening_score_line(weather_text + "\n" + line))
    assert visibility_air_penalty(ctx, 0.0) == 0.5
    assert round(base_score - fog_score, 1) == 0.5


def evening_reduced_score_ignores_current_air_case_e() -> None:
    ctx, line = _production_evening_visibility(
        4000,
        40,
        temperature=24,
        dew_point=16,
    )
    assert line
    weather_text = "🏙️ Калининград: дн/ночь 22/14 °C • ясно • 💨 2 м/с."
    base_score = _score_value(_kld_evening_score_line(weather_text))
    reduced_score = _score_value(_kld_evening_score_line(weather_text + "\n" + line))
    assert visibility_air_penalty(ctx, 0.0) == 0.2
    assert round(base_score - reduced_score, 1) == 0.2


def explicit_forecast_air_enables_dust_and_mixed_case_f() -> None:
    forecast_air = {"aqi": 150, "pm25": 55, "pm10": 110}
    dry_ctx, _dry_line = _production_evening_visibility(
        4000,
        40,
        temperature=24,
        dew_point=16,
        forecast_air_data=forecast_air,
    )
    wet_ctx, _wet_line = _production_evening_visibility(
        900,
        95,
        temperature=7,
        dew_point=6.5,
        weather_code=45,
        forecast_air_data=forecast_air,
    )
    assert dry_ctx.condition == "dust_haze"
    assert wet_ctx.condition == "mixed_visibility"
    assert dry_ctx.aqi == 150 and wet_ctx.pm25 == 55


def score_uses_max_air_or_visibility_penalty() -> None:
    fog = "🌫 Видимость: утром местами туман и низкая облачность; дальние объекты различимы хуже."
    clear_score = _score_value(_kld_score_line(_morning_source(aqi=22)))
    fog_score = _score_value(_kld_score_line(_morning_source(fog, aqi=22)))
    air_score = _score_value(_kld_score_line(_morning_source(aqi=120)))
    combined_score = _score_value(_kld_score_line(_morning_source(fog, aqi=120)))
    assert clear_score == 10.0
    assert fog_score == 9.5
    assert air_score == 9.2
    assert combined_score == 9.2


def morning_format_text_warning_plan_and_regional_regression() -> None:
    ctx = _context(300, 97, 45, temperature=6, dew_point=5.7)
    line = build_kld_visibility_line(ctx, post_type="morning") or ""
    output = build_morning_format_v2("Калининградская область", _morning_source(line))
    assert line in output
    assert "утренний туман" in output
    assert "⚠️ Главный нюанс: утром осторожнее на дорогах, развязках, мостах и открытых участках." in output
    assert "✅ План: утром учитывать плохую видимость" in output
    assert "🌡 По области: днём теплее всего — Черняховск 25°, прохладнее — Балтийск 19°; холоднее ночью — Гусев 10°." in output
    assert "Черняховск:" not in output and "Балтийск:" not in output


def evening_format_is_explicitly_next_morning() -> None:
    ctx = _context(900, 95, 45, temperature=7, dew_point=6.5, post_type="evening", target=NEXT_DATE)
    line = build_kld_visibility_line(ctx, post_type="evening") or ""
    output = build_evening_format_v2(
        "Калининградская область",
        _evening_source(line, "🌕 Полнолуние в ♑ — 100% освещённости."),
    )
    assert line in output and "завтра утром" in output
    assert "⚠️ Главный нюанс: завтра утром осторожнее" in output
    assert "✅ План завтра: утром учитывать плохую видимость" in output
    assert "весь день" not in output


def fog_visual_prompt_case_i() -> None:
    line = "🌫 Видимость: утром сильный туман; местами видимость может падать ниже 500 м."
    message = _morning_source(line)
    ctx = build_visual_context(message, post_type="morning")
    cues = apply_visual_rules(ctx)
    prompt = build_prompt_from_cues(cues)
    final_prompt, _style = build_kld_morning_prompt(message)
    assert ctx.visibility_condition == "dense_fog"
    assert ctx.visibility_forecast_window == "current_morning"
    assert "dense humid fog" in prompt
    assert "heavily reduced distant visibility" in final_prompt
    assert "Must show: mostly clear sky" not in prompt
    assert "good visibility and readable horizon" not in prompt
    for forbidden in ("crisp distant horizon", "perfectly clear horizon", "sharp postcard visibility", "completely transparent air"):
        assert forbidden in prompt


def dust_and_humid_fog_are_visually_distinct_case_j() -> None:
    fog_message = _morning_source("🌫 Видимость: утром местами туман и низкая облачность; дальние объекты различимы хуже.")
    dust_message = _morning_source("🌫 Видимость: утром сухая дымка; воздух и дальняя видимость хуже обычного.")
    fog_prompt, _ = build_kld_morning_prompt(fog_message)
    dust_prompt, _ = build_kld_morning_prompt(dust_message)
    assert "humid coastal fog" in fog_prompt
    assert "muted beige-grey dry atmospheric haze" in dust_prompt
    assert "dry suspended particles" in dust_prompt
    assert "Morning visibility adherence: humid coastal fog" not in dust_prompt
    assert "Must show: humid coastal fog" not in dust_prompt
    assert fog_prompt != dust_prompt


def evening_visibility_overrides_lunar_staging_only_when_needed() -> None:
    fog_line = "🌫 Видимость: завтра утром местами туман и низкая облачность; местами около 900 м; дальние объекты различимы хуже."
    full_message = _evening_source(fog_line, "🌕 Полнолуние в ♑ — 100% освещённости.")
    fog_ctx = build_visual_context(full_message, post_type="evening")
    fog_prompt, _ = build_kld_evening_prompt(
        NEXT_DATE,
        marine_mood="",
        inland_mood="",
        final_format_v2_message=full_message,
        post_type="evening",
    )
    diagnostics = kld_lunar_prompt_diagnostics(fog_prompt, fog_ctx, full_message)
    assert fog_ctx.visibility_forecast_window == "tomorrow_morning"
    assert "next-day early-morning forecast window only" in fog_prompt
    assert "humid coastal fog" in fog_prompt
    assert "Evening light adherence:" not in fog_prompt
    assert "Lunar cue:" not in fog_prompt
    assert diagnostics["final_lunar_rule"] == "tomorrow_morning_visibility_override"
    assert diagnostics["visible_moon_allowed"] is False

    clear_message = _evening_source("", "🌕 Полнолуние в ♑ — 100% освещённости.")
    clear_prompt, _ = build_kld_evening_prompt(
        NEXT_DATE,
        marine_mood="",
        inland_mood="",
        final_format_v2_message=clear_message,
        post_type="evening",
    )
    assert "Evening light adherence: blue-hour Baltic coast" in clear_prompt
    assert "Lunar cue: one realistic full Moon" in clear_prompt


def new_moon_visibility_never_returns_full_moon() -> None:
    line = "🌫 Видимость: завтра утром местами туман и низкая облачность; местами около 900 м."
    message = _evening_source(line, "🌑 Новолуние в ♑ — 1% освещённости.")
    ctx = build_visual_context(message, post_type="evening")
    prompt, _ = build_kld_evening_prompt(
        NEXT_DATE,
        marine_mood="",
        inland_mood="",
        final_format_v2_message=message,
        post_type="evening",
    )
    diagnostics = kld_lunar_prompt_diagnostics(prompt, ctx, message)
    assert "Lunar cue:" not in prompt
    assert "visible realistic full Moon" not in prompt
    assert diagnostics["final_lunar_rule"] == "new_moon_hidden"
    assert diagnostics["visible_moon_allowed"] is False


def structured_dense_fog_keeps_actual_measurement_case_g() -> None:
    structured = _context(300, 97, 45, temperature=6, dew_point=5.7)
    line = build_kld_visibility_line(structured, post_type="morning") or ""
    assert "ниже 500 м" in line
    message = _morning_source(line)
    ctx = build_visual_context(
        message,
        post_type="morning",
        visibility_context=structured,
    )
    metadata = kld_scene_metadata(
        ctx,
        date_key=DATE.isoformat(),
        post_type="morning",
        source_text=message,
    )
    assert ctx.morning_min_visibility_m == 300
    assert ctx.reported_visibility_threshold_m == 500
    assert metadata["visibility_condition"] == "dense_fog"
    assert metadata["visibility_forecast_window"] == "current_morning"
    assert metadata["morning_min_visibility_m"] == "300"
    assert metadata["reported_visibility_threshold_m"] == "500"
    cache_key = kld_visual_cache_key(metadata)
    assert "morning_min_visibility_m=300" in cache_key
    assert "morning_min_visibility_m=500" not in cache_key
    sidecar_ctx = build_visual_context(
        message,
        post_type="morning",
        visibility_context=metadata,
    )
    assert sidecar_ctx.visibility_condition == "dense_fog"
    assert sidecar_ctx.morning_min_visibility_m == 300
    assert sidecar_ctx.reported_visibility_threshold_m == 500

    _prompt, structured_style = build_kld_morning_prompt(
        message,
        visibility_context=structured,
    )
    sidecar_message = post_common.KldMorningMessage(
        message,
        visibility_context=structured,
    )
    _sidecar_prompt, sidecar_style = build_kld_morning_prompt(sidecar_message)
    _text_prompt, text_only_style = build_kld_morning_prompt(message)
    assert structured_style.startswith("format_v2_scene_cues_morning_")
    assert sidecar_style == structured_style
    assert structured_style != text_only_style


def text_threshold_is_not_actual_measurement_case_h() -> None:
    message = _morning_source(
        "🌫 Видимость: утром сильный туман; местами видимость может падать ниже 500 м."
    )
    ctx = build_visual_context(message, post_type="morning")
    metadata = kld_scene_metadata(
        ctx,
        date_key=DATE.isoformat(),
        post_type="morning",
        source_text=message,
    )
    assert ctx.visibility_condition == "dense_fog"
    assert ctx.reported_visibility_threshold_m == 500
    assert ctx.reported_visibility_m is None
    assert ctx.current_visibility_m is None
    assert ctx.morning_min_visibility_m is None
    assert metadata["morning_min_visibility_m"] == "unknown"
    assert metadata["reported_visibility_threshold_m"] == "500"


def reported_mist_does_not_replace_structured_actual_case_i() -> None:
    message = _morning_source(
        "🌫 Видимость: утром влажная дымка; местами около 2200 м; дальние ориентиры различимы хуже."
    )
    structured = KldVisibilityContext(
        morning_min_visibility_m=300,
        condition="mist",
        target_date=DATE.isoformat(),
    )
    ctx = build_visual_context(
        message,
        post_type="morning",
        visibility_context=structured,
    )
    metadata = kld_scene_metadata(
        ctx,
        date_key=DATE.isoformat(),
        post_type="morning",
        source_text=message,
    )
    assert ctx.visibility_condition == "mist"
    assert ctx.reported_visibility_m == 2200
    assert ctx.reported_visibility_threshold_m is None
    assert ctx.morning_min_visibility_m == 300
    assert metadata["reported_visibility_m"] == "2200"
    assert metadata["morning_min_visibility_m"] == "300"


def main() -> None:
    checks = (
        dense_fog_case_a,
        fog_case_b,
        mist_case_c,
        dust_haze_case_d,
        mixed_visibility_case_e,
        morning_window_case_f,
        evening_next_morning_case_g,
        clear_case_h,
        invalid_values_and_wmo_fallback,
        weather_fetch_fields_are_requested_offline,
        post_builder_visibility_fallback_is_nonfatal,
        evening_current_air_does_not_create_dust_case_a,
        evening_current_air_does_not_make_fog_mixed_case_b,
        evening_current_air_does_not_create_clear_haze_case_c,
        evening_fog_score_ignores_current_air_case_d,
        evening_reduced_score_ignores_current_air_case_e,
        explicit_forecast_air_enables_dust_and_mixed_case_f,
        score_uses_max_air_or_visibility_penalty,
        morning_format_text_warning_plan_and_regional_regression,
        evening_format_is_explicitly_next_morning,
        fog_visual_prompt_case_i,
        dust_and_humid_fog_are_visually_distinct_case_j,
        evening_visibility_overrides_lunar_staging_only_when_needed,
        new_moon_visibility_never_returns_full_moon,
        structured_dense_fog_keeps_actual_measurement_case_g,
        text_threshold_is_not_actual_measurement_case_h,
        reported_mist_does_not_replace_structured_actual_case_i,
    )
    for check in checks:
        check()
        print(f"PASS {check.__name__}")
    print(f"OK: {len(checks)} KLD visibility offline checks passed")


if __name__ == "__main__":
    main()
