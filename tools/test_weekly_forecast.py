#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regression checks for Kaliningrad weekly VayboMeter forecast."""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import date
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from send_weekly_forecast import build_weekly_forecast  # noqa: E402


WEATHER = {
    "daily": {
        "time": [
            "2026-07-06",
            "2026-07-07",
            "2026-07-08",
            "2026-07-09",
            "2026-07-10",
            "2026-07-11",
            "2026-07-12",
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
KP = (5.3, "неспокойно", 123456, "fixture")
LUNAR = {
    "days": {
        "2026-07-06": {"phase_name": "Растущая Луна", "percent": 75},
        "2026-07-07": {
            "phase_name": "Полнолуние",
            "percent": 96,
            "void_of_course": {"start": "07.07 12:00", "end": "07.07 13:20"},
        },
        "2026-07-12": {"phase_name": "Убывающая Луна", "percent": 70},
    }
}

FORBIDDEN = ("аварии", "чрезвычайные ситуации", "операции лучше отложить", "воздушном пространстве")


class _Parser(HTMLParser):
    pass


def _base_text(extra_paths: list[Path] | None = None) -> str:
    return build_weekly_forecast(
        date(2026, 7, 6),
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
    assert "🌦 Погода" in text
    assert "🌊 Балтика" in text
    assert "🏭 Воздух" in text
    assert "🧲 Космопогода" in text
    assert "🌙 Луна" in text
    assert "✅ Как прожить неделю" in text
    assert "Kp повышен" in text
    assert "🌕" in text and "Полнолуние" in text
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


def main() -> None:
    checks = (
        test_weekly_forecast_structure_without_optional_config,
        test_weekly_forecast_includes_curated_astro_events,
    )
    for check in checks:
        check()
        print(f"PASS {check.__name__}")
    print(f"OK: {len(checks)} Kaliningrad weekly forecast checks passed")


if __name__ == "__main__":
    main()
