#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline checks for KLD local earthquake monitoring."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import earthquakes  # noqa: E402
from earthquakes import KldQuakeEvents, build_kld_quake_line  # noqa: E402
from format_v2 import build_evening_format_v2, build_morning_format_v2  # noqa: E402


def _event(
    mag: float,
    *,
    lat: float = 54.7104,
    lon: float = 20.4522,
    depth: float = 8.0,
    minutes_ago: int = 30,
    source: str = "EMSC",
    event_id: str = "evt",
    status: str = "reviewed",
    event_type: str = "earthquake",
    place: str = "Baltic region",
) -> dict:
    when = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    nearest_name, nearest_dist = earthquakes._nearest_city(lat, lon)
    return {
        "source": source,
        "sources": [source],
        "source_event_id": event_id,
        "mag": mag,
        "place": place,
        "time_utc": when.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "time_local": when.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "depth_km": depth,
        "lat": lat,
        "lon": lon,
        "distance_km": nearest_dist,
        "distance_from_center_km": earthquakes._haversine_km(
            earthquakes.KLD_CENTER_LAT,
            earthquakes.KLD_CENTER_LON,
            lat,
            lon,
        ),
        "nearest_city": nearest_name,
        "url": "https://example.test/quake",
        "status": status,
        "event_type": event_type,
    }


def _events(items, *, regional_ok: bool = True, usgs_ok: bool = True):
    return KldQuakeEvents(
        items,
        min_mag=0.9,
        hours=24,
        radius_km=500,
        source_status={
            "regional": {"ok": regional_ok, "count": len(items) if regional_ok else None},
            "usgs": {"ok": usgs_ok, "count": 0 if usgs_ok else None},
        },
    )


def test_m09_included_m08_excluded() -> None:
    filtered = earthquakes._filter_events(
        [_event(0.8, event_id="m08"), _event(0.9, event_id="m09")],
        min_mag=0.9,
        radius_km=500,
        hours=24,
        now=datetime.now(timezone.utc),
    )
    line = build_kld_quake_line(_events(filtered))
    assert "1 микрособытие" in line
    assert "M0.9–1.9" in line
    assert "M0.8" not in line


def test_micro_events_are_aggregated() -> None:
    items = [_event(0.9 + index * 0.2, event_id=f"micro{index}") for index in range(4)]
    line = build_kld_quake_line(_events(items))
    assert "4 микрособытия M0.9–1.9" in line
    assert "M1.1" not in line and "M1.5" not in line
    assert len(line.splitlines()) <= 2


def test_m23_weak_event_line() -> None:
    line = build_kld_quake_line(_events([_event(1.4, event_id="micro"), _event(2.3, event_id="m23")]))
    assert "1 микрособытие и 1 слабое событие" in line
    assert "сильнейшее M2.3" in line
    assert "Калининграда" in line
    assert "⚠️" not in line


def test_m34_clear_without_damage_claims() -> None:
    line = build_kld_quake_line(_events([_event(3.4, lat=54.6544, lon=19.9094, event_id="m34")]))
    low = line.lower()
    assert "сильнейшее событие M3.4" in line
    assert "Балтийска" in line
    assert "⚠️" not in line
    for forbidden in ("ущерб", "опасн", "разруш", "пострад"):
        assert forbidden not in low


def test_m42_warning_includes_depth() -> None:
    line = build_kld_quake_line(_events([_event(4.2, lat=54.6544, lon=19.9094, depth=18, event_id="m42")]))
    assert "⚠️ M4.2" in line
    assert "Балтийска" in line
    assert "глубина 18 км" in line


def test_no_events_threshold_aware_not_absolute() -> None:
    line = build_kld_quake_line(_events([]))
    assert line is None
    diagnostic = build_kld_quake_line(_events([]), publish_empty=True)
    assert "событий M0.9+ рядом с Калининградской областью не найдено" in diagnostic
    assert "землетрясений не было" not in diagnostic
    assert "спокойно" not in diagnostic


def test_complete_source_failure_is_not_hidden() -> None:
    line = build_kld_quake_line(None)
    assert line is None
    diagnostic = build_kld_quake_line(None, publish_source_failure=True)
    assert "данные временно не обновились" in diagnostic


def test_regional_failure_does_not_claim_no_m09_events() -> None:
    line = build_kld_quake_line(_events([], regional_ok=False, usgs_ok=True))
    assert line is None
    diagnostic = build_kld_quake_line(_events([], regional_ok=False, usgs_ok=True), publish_source_failure=True)
    assert "региональные данные" in diagnostic
    assert "M0.9+ рядом" not in diagnostic
    assert len(diagnostic.splitlines()) <= 2


def test_regional_failure_preserves_usgs_m4_warning() -> None:
    line = build_kld_quake_line(
        _events([_event(4.2, lat=54.6544, lon=19.9094, depth=18, source="USGS", event_id="usgs42")], regional_ok=False)
    )
    assert "региональные данные" not in line
    assert "⚠️ M4.2" in line
    assert "глубина 18 км" in line
    assert len(line.splitlines()) <= 2


def test_two_source_duplicate_counts_once() -> None:
    base = _event(1.4, event_id="emsc1", source="EMSC", minutes_ago=10, status="reviewed")
    duplicate = _event(1.5, event_id="usgs1", source="USGS", minutes_ago=11, status="automatic")
    merged = earthquakes.deduplicate_events([base, duplicate])
    line = build_kld_quake_line(_events(merged))
    assert len(merged) == 1
    assert "1 микрособытие" in line
    assert set(merged[0]["sources"]) == {"EMSC", "USGS"}


def test_quarry_blast_explosion_excluded() -> None:
    feature = {
        "id": "blast",
        "properties": {
            "mag": 1.6,
            "place": "KLD quarry",
            "time": int(datetime.now(timezone.utc).timestamp() * 1000),
            "type": "quarry blast",
            "url": "https://example.test/blast",
        },
        "geometry": {"type": "Point", "coordinates": [20.45, 54.71, 3.0]},
    }
    assert earthquakes._normalize_usgs_feature(feature) is None
    feature["properties"]["type"] = "explosion"
    assert earthquakes._normalize_usgs_feature(feature) is None


def test_default_fetch_uses_m09() -> None:
    old_regional = earthquakes.fetch_regional_events
    old_usgs = earthquakes.fetch_usgs_events
    seen: list[float] = []

    def fake_regional(**kwargs):
        seen.append(float(kwargs["min_mag"]))
        return []

    def fake_usgs(**kwargs):
        seen.append(float(kwargs["min_mag"]))
        return []

    earthquakes.fetch_regional_events = fake_regional
    earthquakes.fetch_usgs_events = fake_usgs
    try:
        events = earthquakes.get_recent_earthquakes_kld()
    finally:
        earthquakes.fetch_regional_events = old_regional
        earthquakes.fetch_usgs_events = old_usgs
    assert isinstance(events, KldQuakeEvents)
    assert seen == [0.9, 0.9]


def test_format_v2_preserves_quake_line() -> None:
    quake = "🌍 Сейсмика 24ч: ⚠️ M4.2, 18 км от Балтийска, глубина 18 км."
    morning_legacy = "\n".join(
        [
            "Калининград сегодня",
            "Погода: 🏙️ Калининград — 18/12 °C • облачно • 💨 3 м/с • 🔹 1014 гПа.",
            "🏭 Воздух: нормально.",
            quake,
            "☀️ УФ: низкий.",
            "#Калининград #погода",
        ]
    )
    morning = build_morning_format_v2("Калининград", morning_legacy)
    assert "🌍 Сейсмика 24ч:" in morning

    evening_legacy = "\n".join(["Калининградская область завтра", quake, "✅ Рекомендации"])
    evening = build_evening_format_v2("Калининград", evening_legacy)
    assert "🌍 Сейсмика 24ч:" in evening


def test_format_v2_omits_empty_or_source_health_quake_lines() -> None:
    for quake in (
        "🌍 Сейсмика 24ч: по доступным региональным каталогам событий M0.9+ рядом с Калининградской областью не найдено.",
        "🌍 Сейсмика: данные временно не обновились.",
        "🌍 Сейсмика: региональные данные по слабым событиям временно не обновились.",
    ):
        morning = build_morning_format_v2(
            "Калининград",
            "\n".join(
                [
                    "Калининград сегодня",
                    "Погода: 🏙️ Калининград — 18/12 °C • облачно • 💨 3 м/с.",
                    quake,
                    "#Калининград #погода",
                ]
            ),
        )
        assert "🌍 Сейсмика" not in morning


def test_air_failure_does_not_remove_seismic_output() -> None:
    pendulum_stub = types.ModuleType("pendulum")
    pendulum_stub.DateTime = object
    pendulum_stub.Timezone = object
    pendulum_stub.timezone = lambda name: types.SimpleNamespace(name=name)
    pendulum_stub.today = lambda *_args, **_kwargs: None
    pendulum_stub.now = lambda *_args, **_kwargs: None
    sys.modules.setdefault("pendulum", pendulum_stub)
    telegram_stub = types.ModuleType("telegram")
    telegram_stub.Bot = object
    telegram_stub.constants = types.SimpleNamespace(ParseMode=types.SimpleNamespace(HTML="HTML"))
    sys.modules.setdefault("telegram", telegram_stub)
    import post_common

    old_env = post_common.os.environ.get("KLD_QUAKES_24H")
    old_get = post_common.get_recent_earthquakes_kld
    post_common.os.environ["KLD_QUAKES_24H"] = "1"
    post_common.get_recent_earthquakes_kld = lambda **_kwargs: _events([_event(2.3, event_id="m23")])
    try:
        line = post_common._kld_quake_line_24h()
        assert line is not None
        assert "M2.3" in line
    finally:
        post_common.get_recent_earthquakes_kld = old_get
        if old_env is None:
            post_common.os.environ.pop("KLD_QUAKES_24H", None)
        else:
            post_common.os.environ["KLD_QUAKES_24H"] = old_env


def main() -> None:
    tests = [
        test_m09_included_m08_excluded,
        test_micro_events_are_aggregated,
        test_m23_weak_event_line,
        test_m34_clear_without_damage_claims,
        test_m42_warning_includes_depth,
        test_no_events_threshold_aware_not_absolute,
        test_complete_source_failure_is_not_hidden,
        test_regional_failure_does_not_claim_no_m09_events,
        test_regional_failure_preserves_usgs_m4_warning,
        test_two_source_duplicate_counts_once,
        test_quarry_blast_explosion_excluded,
        test_default_fetch_uses_m09,
        test_format_v2_preserves_quake_line,
        test_format_v2_omits_empty_or_source_health_quake_lines,
        test_air_failure_does_not_remove_seismic_output,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"OK: {len(tests)} KLD earthquake checks passed")


if __name__ == "__main__":
    main()
