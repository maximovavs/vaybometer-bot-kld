#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline checks for the KLD earthquake alert-only line."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import earthquakes  # noqa: E402
from earthquakes import build_kld_quake_line  # noqa: E402
from format_v2 import build_evening_format_v2, build_morning_format_v2  # noqa: E402


def _feature(mag: float, lat: float, lon: float, depth_km: float = 8.0) -> dict:
    return {
        "type": "Feature",
        "properties": {
            "mag": mag,
            "place": "test event near Kaliningrad",
            "time": 1782383400000,  # 2026-06-25 10:30:00 UTC
            "url": "https://earthquake.usgs.gov/test",
        },
        "geometry": {
            "type": "Point",
            "coordinates": [lon, lat, depth_km],
        },
    }


def test_no_events_hidden() -> None:
    assert build_kld_quake_line([], show_calm=False) is None


def test_no_events_calm() -> None:
    line = build_kld_quake_line([], show_calm=True)
    assert line is not None
    assert "🌍 Сейсмика 24ч:" in line
    assert "спокойно" in line
    assert len(line) < 120


def test_weak_event_compact_line() -> None:
    event = earthquakes._normalize_event(_feature(2.3, 54.7104, 20.4522))
    line = build_kld_quake_line([event], show_calm=False)  # type: ignore[list-item]
    assert line is not None
    assert "🌍 Сейсмика 24ч:" in line
    assert "M2.3" in line
    assert "Калининграда" in line
    assert "глубина 8 км" in line
    assert "⚠️" not in line
    assert len(line) < 120


def test_significant_event_warning_line() -> None:
    event = earthquakes._normalize_event(_feature(4.1, 54.6544, 19.9094, 11.0))
    line = build_kld_quake_line([event], show_calm=False)  # type: ignore[list-item]
    assert line is not None
    assert "⚠️ M4.1" in line
    assert "Балтийска" in line


def test_malformed_response_no_crash() -> None:
    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"features": "not-a-list"}

    original_get = earthquakes.requests.get
    try:
        earthquakes.requests.get = lambda *args, **kwargs: FakeResponse()  # type: ignore[method-assign]
        assert earthquakes.get_recent_earthquakes_kld() is None
    finally:
        earthquakes.requests.get = original_get  # type: ignore[method-assign]


def test_format_v2_preserves_quake_line() -> None:
    quake = "🌍 Сейсмика 24ч: M2.3, 5 км от Калининграда, глубина 8 км, 12:30."

    morning_legacy = "\n".join(
        [
            "Калининград сегодня",
            "Калининград — 18° / облачно.",
            "🏭 Воздух: нормально.",
            quake,
            "☀️ УФ: низкий.",
            "🧪 Safecast: фон в норме.",
            "#Калининград #погода",
        ]
    )
    morning = build_morning_format_v2("Калининград", morning_legacy)
    assert "🌍 Сейсмика 24ч:" in morning

    evening_legacy = "\n".join(
        [
            "Калининградская область завтра",
            "Калининград — 17° / пасмурно.",
            quake,
            "✅ Рекомендации",
            "Планируй короткие прогулки по погоде.",
        ]
    )
    evening = build_evening_format_v2("Калининград", evening_legacy)
    assert "🌍 Сейсмика 24ч:" in evening


def main() -> None:
    tests = [
        test_no_events_hidden,
        test_no_events_calm,
        test_weak_event_compact_line,
        test_significant_event_warning_line,
        test_malformed_response_no_crash,
        test_format_v2_preserves_quake_line,
    ]
    for test in tests:
        test()
    print(f"OK: {len(tests)} KLD earthquake checks passed")


if __name__ == "__main__":
    main()
