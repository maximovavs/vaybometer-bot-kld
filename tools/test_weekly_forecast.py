#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regression checks for Kaliningrad weekly VayboMeter forecast."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PIL import Image  # type: ignore  # noqa: E402
import send_weekly_forecast as weekly_module  # noqa: E402
import weather as weather_module  # noqa: E402
from send_weekly_forecast import build_weekly_forecast  # noqa: E402
from weekly_cover import RENDERER_VERSION as WEEKLY_COVER_VERSION, render_weekly_cover  # noqa: E402


WEATHER = {
    "daily": {
        "time": [
            "2026-07-01",
            "2026-07-02",
            "2026-07-03",
            "2026-07-04",
            "2026-07-05",
            "2026-07-06",
            "2026-07-07",
        ],
        "temperature_2m_max": [21, 22, 23, 24, 22, 20, 19],
        "temperature_2m_min": [14, 15, 16, 16, 15, 14, 13],
        "wind_speed_10m_max": [6, 8, 8, 7, 6, 5, 5],
        "wind_gusts_10m_max": [9, 12, 11, 10, 8, 7, 7],
        "precipitation_probability_max": [20, 45, 50, 35, 20, 10, 10],
        "weathercode": [3, 61, 63, 3, 2, 1, 1],
        "uv_index_max": [4, 5, 5, 4, 4, 3, 3],
    }
}

AIR = {"aqi": 58, "pm25": 12, "pm10": 24}
KP = (1.0, "спокойно", 123456, "fixture")
LUNAR = {
    "days": {
        "2026-07-01": {
            "phase_name": "Полнолуние",
            "percent": 99,
            "void_of_course": {"start": "01.07 20:13", "end": "01.07 22:33"},
        },
        "2026-07-03": {
            "phase_name": "Убывающая Луна",
            "percent": 92,
            "void_of_course": {"start": "03.07 17:15", "end": "04.07 00:00"},
        },
        "2026-07-07": {"phase_name": "Убывающая Луна", "percent": 75},
    }
}

FORBIDDEN = ("аварии", "чрезвычайные ситуации", "операции лучше отложить", "воздушном пространстве")


class _Parser(HTMLParser):
    pass


def _base_text(extra_paths: list[Path] | None = None) -> str:
    return build_weekly_forecast(
        date(2026, 7, 1),
        weather_payload=WEATHER,
        air_data=AIR,
        sea_temps=[20.1, 21.8, 20.6],
        kp_tuple=KP,
        lunar_data=LUNAR,
        astro_events_paths=extra_paths or [Path("__missing_astro_events.json")],
    )


def test_weekly_forecast_structure_without_optional_config() -> None:
    text = _base_text()
    assert "🗓 Вайб недели" in text
    assert "✨ Главный фон недели" in text
    assert "🌿 Смысл недели" in text
    assert text.index("✨ Главный фон недели") < text.index("🌿 Смысл недели") < text.index("🌦 Погода")
    assert "🌦 Погода" in text
    assert "🌊 Балтика" in text
    assert "Балтика: вода" not in text
    assert "Вода" in text
    assert "🏄 Вода и спорт" in text
    assert "SUP:" in text
    assert "Кайт/винг/винд:" in text
    assert "Серф:" in text
    assert "SUP: короткие окна в защищённых местах." in text
    assert "Кайт/винг/винд: только опытным; проверять порывы и направление." in text
    assert "Серф: по фактической волне; Балтика быстро меняется." in text
    assert "🏭 Воздух сейчас" in text
    assert "Текущий снимок воздуха:" in text
    assert "🧲 Космопогода сейчас" in text
    assert "это текущий снимок, а не прогноз на всю неделю" in text
    assert "🌙 Луна и астроритм (интерпретация)" in text
    assert "✅ Как прожить неделю" in text
    assert "🌕" in text and "Полнолуние" in text
    assert "01.07 01.07" not in text
    assert "03.07 03.07" not in text
    assert "01.07 20:13–22:33" in text
    assert "03.07 17:15–04.07 00:00" in text
    assert "утреннюю проверку ветра" in text
    assert "Балтику планировать по фактическому ветру и волне." in text
    assert text.splitlines()[-1] == "#Калининград #вайбнедели #погода #Балтика #астропогода"
    assert not any(phrase in text.lower() for phrase in FORBIDDEN)
    _Parser().feed(text)


def test_weekly_forecast_includes_curated_astro_events() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "astro_events_monthly.json"
        path.write_text(
            json.dumps(
                [
                    {
                        "date": "2026-07-07",
                        "title": "Нептун разворачивается ретроградно",
                        "tone": "эмоциональная чувствительность, переоценка целей",
                        "advice": "не спешить с обещаниями, проверять факты",
                    }
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        text = _base_text([path])
    assert "Нептун разворачивается ретроградно" in text
    assert "проверять факты" in text



def test_weekly_cover_is_high_contrast_factual_projection() -> None:
    text = _base_text()
    with tempfile.TemporaryDirectory() as tmp:
        metadata = render_weekly_cover(
            text,
            start=date(2026, 7, 1),
            output_path=Path(tmp) / "weekly.png",
        )
        assert metadata["renderer_version"] == WEEKLY_COVER_VERSION
        assert metadata["main_fact"] in text
        assert metadata["weather_fact"] in text
        assert metadata["sea_fact"] in text
        with Image.open(metadata["path"]) as image:
            assert image.size == (1080, 1350)
            assert image.format == "PNG"
            assert image.info["renderer_version"] == WEEKLY_COVER_VERSION
            assert image.info["week_start"] == "2026-07-01"
            image.verify()



def _run_weekly_send_case(*, render_fails: bool = False, image_fails: bool = False) -> list[str]:
    events: list[str] = []
    old_token = os.environ.get("TELEGRAM_TOKEN_KLG")
    old_telegram = sys.modules.get("telegram")
    old_renderer = weekly_module.render_weekly_cover

    class _ParseMode:
        HTML = "HTML"

    class _Constants:
        ParseMode = _ParseMode

    class _FakeBot:
        def __init__(self, token: str):
            assert token == "weekly-test-token"

        async def send_photo(self, **kwargs):
            events.append("photo")
            assert kwargs["chat_id"] == -100123
            assert kwargs["caption"] == "Вайб недели: 01–07 июля"
            if image_fails:
                raise RuntimeError("synthetic image send failure")
            return object()

        async def send_message(self, **kwargs):
            events.append("text")
            assert kwargs["chat_id"] == -100123
            assert kwargs["text"] == "weekly text"
            assert kwargs["parse_mode"] == "HTML"
            return object()

    telegram_module = ModuleType("telegram")
    telegram_module.Bot = _FakeBot
    telegram_module.constants = _Constants

    with tempfile.TemporaryDirectory() as tmp:
        def fake_render(text: str, *, start: date, output_path: str | Path):
            events.append("cover")
            assert text == "weekly text"
            assert start == date(2026, 7, 1)
            if render_fails:
                raise RuntimeError("synthetic cover render failure")
            path = Path(tmp) / "weekly-cover.png"
            path.write_bytes(b"offline-cover")
            return {"path": str(path)}

        try:
            os.environ["TELEGRAM_TOKEN_KLG"] = "weekly-test-token"
            sys.modules["telegram"] = telegram_module
            weekly_module.render_weekly_cover = fake_render
            asyncio.run(weekly_module._send("weekly text", "-100123", date(2026, 7, 1)))
        finally:
            weekly_module.render_weekly_cover = old_renderer
            if old_telegram is None:
                sys.modules.pop("telegram", None)
            else:
                sys.modules["telegram"] = old_telegram
            if old_token is None:
                os.environ.pop("TELEGRAM_TOKEN_KLG", None)
            else:
                os.environ["TELEGRAM_TOKEN_KLG"] = old_token
    return events


def test_weekly_send_orders_cover_before_text_without_network() -> None:
    assert _run_weekly_send_case() == ["cover", "photo", "text"]


def test_weekly_send_survives_cover_render_failure() -> None:
    assert _run_weekly_send_case(render_fails=True) == ["cover", "text"]


def test_weekly_send_survives_image_send_failure() -> None:
    assert _run_weekly_send_case(image_fails=True) == ["cover", "photo", "text"]


def _weekly_api_fixture(dates: list[str]) -> dict:
    count = len(dates)
    return {
        "daily": {
            "time": dates,
            "temperature_2m_max": [20 + idx for idx in range(count)],
            "temperature_2m_min": [12 + idx for idx in range(count)],
            "weathercode": [3] * count,
            "precipitation_probability_max": [20] * count,
            "precipitation_sum": [0.5] * count,
            "wind_speed_10m_max": [5.0] * count,
            "wind_gusts_10m_max": [8.0] * count,
            "uv_index_max": [4.0] * count,
        },
        "daily_units": {
            "temperature_2m_max": "°C",
            "temperature_2m_min": "°C",
            "precipitation_probability_max": "%",
            "precipitation_sum": "mm",
            "wind_speed_10m_max": "m/s",
            "wind_gusts_10m_max": "m/s",
        },
    }


def test_weekly_source_requests_exact_range_and_units_without_network() -> None:
    captured: list[dict] = []
    old_http = weather_module._safe_http_get
    dates = [f"2026-07-{day:02d}" for day in range(6, 13)]

    def fake_http(url: str, **kwargs) -> dict:
        assert url == weather_module.OPEN_METEO_URL
        captured.append(dict(kwargs))
        return _weekly_api_fixture(dates)

    try:
        weather_module._safe_http_get = fake_http
        payload = weather_module.get_weekly_weather(
            54.7104,
            20.4522,
            start_date="2026-07-06",
            end_date="2026-07-12",
            tz_name="Europe/Kaliningrad",
        )
    finally:
        weather_module._safe_http_get = old_http

    assert len(captured) == 1
    query = captured[0]
    assert query["start_date"] == "2026-07-06"
    assert query["end_date"] == "2026-07-12"
    assert query["wind_speed_unit"] == "ms"
    assert query["precipitation_unit"] == "mm"
    assert query["temperature_unit"] == "celsius"
    assert "precipitation_probability_max" in query["daily"]
    assert "precipitation_sum" in query["daily"]
    assert payload["_weekly_meta"]["coverage_complete"] is True
    assert payload["_weekly_meta"]["coverage_days"] == 7
    assert payload["_weekly_meta"]["normalized_units"]["wind_speed"] == "m/s"
    assert payload["_weekly_meta"]["normalized_units"]["precipitation"] == "mm"


def test_weekly_source_rejects_six_eight_and_wrong_dates() -> None:
    old_http = weather_module._safe_http_get
    variants = [
        [f"2026-07-{day:02d}" for day in range(6, 12)],
        [f"2026-07-{day:02d}" for day in range(5, 13)],
        [f"2026-07-{day:02d}" for day in range(7, 14)],
    ]
    try:
        for dates in variants:
            weather_module._safe_http_get = lambda url, dates=dates, **kwargs: _weekly_api_fixture(dates)
            payload = weather_module.get_weekly_weather(
                54.7104,
                20.4522,
                start_date="2026-07-06",
                end_date="2026-07-12",
                tz_name="Europe/Kaliningrad",
            )
            assert payload["_weekly_meta"]["coverage_complete"] is False
    finally:
        weather_module._safe_http_get = old_http


def test_weekly_synthetic_current_fallback_is_not_seven_day_coverage() -> None:
    synthetic = {
        "daily": {
            "temperature_2m_max": [18, 18],
            "temperature_2m_min": [18, 18],
            "weathercode": [500, 500],
        }
    }
    assert weekly_module._daily_rows(synthetic, date(2026, 7, 6)) == []
    days, complete = weekly_module._payload_weekly_coverage(synthetic, date(2026, 7, 6))
    assert days == 0
    assert complete is False
    text = build_weekly_forecast(
        date(2026, 7, 6),
        weather_payload=synthetic,
        air_data=AIR,
        sea_temps=[20.1, 21.8, 20.6],
        kp_tuple=KP,
        lunar_data=LUNAR,
        astro_events_paths=[Path("__missing_astro_events.json")],
    )
    assert "Полный прогноз на все 7 дней пока не собран" in text


def test_weekly_openweather_code_system_is_not_accepted_as_wmo() -> None:
    dates = [f"2026-07-{day:02d}" for day in range(6, 13)]
    payload = _weekly_api_fixture(dates)
    payload["daily"]["weathercode"] = [500] * 7
    payload["_weekly_meta"] = {
        "coverage_complete": True,
        "weather_code_system": "openweather",
        "normalized_units": {"wind_speed": "m/s", "precipitation": "mm"},
    }
    days, complete = weekly_module._payload_weekly_coverage(payload, date(2026, 7, 6))
    assert days == 7
    assert complete is False


def main() -> None:
    checks = (
        test_weekly_source_requests_exact_range_and_units_without_network,
        test_weekly_source_rejects_six_eight_and_wrong_dates,
        test_weekly_synthetic_current_fallback_is_not_seven_day_coverage,
        test_weekly_openweather_code_system_is_not_accepted_as_wmo,
        test_weekly_forecast_structure_without_optional_config,
        test_weekly_forecast_includes_curated_astro_events,
        test_weekly_cover_is_high_contrast_factual_projection,
        test_weekly_send_orders_cover_before_text_without_network,
        test_weekly_send_survives_cover_render_failure,
        test_weekly_send_survives_image_send_failure,
    )
    for check in checks:
        check()
        print(f"PASS {check.__name__}")
    print(f"OK: {len(checks)} Kaliningrad weekly forecast checks passed")


if __name__ == "__main__":
    main()
