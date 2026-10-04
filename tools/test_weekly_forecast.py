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
import weekly_snapshot as snapshot_module  # noqa: E402
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


def _snapshot_days_fixture(*, temp_shift: float = 0.0, rainy_days: int = 2, windy_days: int = 2, gust_peak: float = 12.0, precip: float = 0.5) -> list[dict]:
    from datetime import timedelta
    return [{"date":(date(2026,7,6)+timedelta(days=i)).isoformat(),"tmax":20.0+temp_shift,"tmin":12.0+temp_shift,
        "wind":5.0,"gust":gust_peak if i < windy_days else 7.0,"rainy":i < rainy_days,"precip_sum":precip} for i in range(7)]

def _snapshot_candidate_fixture(**kwargs) -> dict:
    return snapshot_module.build_snapshot_candidate(region="kld",week_start=date(2026,7,6),weather_days=_snapshot_days_fixture(**kwargs),
        weather_coverage_days=7,weather_coverage_complete=True,sea_temps=[20.0]*len(weekly_module.SEA_POINTS),
        expected_sea_samples=len(weekly_module.SEA_POINTS),generated_at_utc="2026-07-04T20:00:00Z")

def _previous_candidate(**kwargs) -> dict:
    from datetime import timedelta
    days=[dict(day,date=(date(2026,6,29)+timedelta(days=i)).isoformat()) for i,day in enumerate(_snapshot_days_fixture(**kwargs))]
    return snapshot_module.build_snapshot_candidate(region="kld",week_start=date(2026,6,29),weather_days=days,
        weather_coverage_days=7,weather_coverage_complete=True,sea_temps=[18.0]*len(weekly_module.SEA_POINTS),
        expected_sea_samples=len(weekly_module.SEA_POINTS),generated_at_utc="2026-06-27T20:00:00Z")

def _auth(candidate: dict, mid: int = 101) -> dict:
    return snapshot_module.finalize_snapshot(candidate,mid,published_at_utc="2026-07-04T20:05:00Z")

def test_weekly_snapshot_serialization_and_validation() -> None:
    auth=_auth(_snapshot_candidate_fixture())
    assert auth["precipitation_sum_mm"]==3.5
    with tempfile.TemporaryDirectory() as tmp:
        path=Path(tmp)/"snapshot.json"; snapshot_module.write_snapshot_atomic(path,auth)
        assert snapshot_module.load_snapshot_file(path,region="kld",expected_week_start="2026-07-06")==auth

def test_weekly_snapshot_rejects_corrupt_wrong_region_week_and_incomplete() -> None:
    auth=_auth(_snapshot_candidate_fixture())
    with tempfile.TemporaryDirectory() as tmp:
        path=Path(tmp)/"snapshot.json"; path.write_text("{broken",encoding="utf-8")
        assert snapshot_module.load_snapshot_file(path,region="kld",expected_week_start="2026-07-06") is None
    assert not snapshot_module.validate_snapshot(dict(auth,region="cyprus"),region="kld",expected_week_start="2026-07-06")
    assert not snapshot_module.validate_snapshot(auth,region="kld",expected_week_start="2026-06-29")
    assert not snapshot_module.validate_snapshot(dict(auth,weather_coverage_complete=False,weather_coverage_days=6),region="kld",expected_week_start="2026-07-06")

def test_weekly_snapshot_selects_exact_previous_and_earliest_canonical() -> None:
    first=_auth(_previous_candidate(),201); late=_auth(_previous_candidate(),202)
    selected=snapshot_module.select_earliest_valid_snapshot([
        {"created_at":"2026-07-04T20:00:00Z","payload":_auth(_snapshot_candidate_fixture(),303)},
        {"created_at":"2026-06-28T10:00:00Z","payload":late},{"created_at":"2026-06-27T20:00:00Z","payload":first}],
        region="kld",expected_week_start="2026-06-29")
    assert selected["production_text_message_id"]==201

def test_weekly_delta_all_threshold_directions() -> None:
    prev=_auth(_previous_candidate())
    assert snapshot_module.derive_delta(_snapshot_candidate_fixture(temp_shift=2.0),prev,region="kld")["temperature"]["direction"]=="warmer"
    assert snapshot_module.derive_delta(_snapshot_candidate_fixture(temp_shift=-2.0),prev,region="kld")["temperature"]["direction"]=="colder"
    assert "temperature" not in snapshot_module.derive_delta(_snapshot_candidate_fixture(temp_shift=1.9),prev,region="kld")
    assert snapshot_module.derive_delta(_snapshot_candidate_fixture(rainy_days=4),prev,region="kld")["rain"]["direction"]=="wetter"
    assert snapshot_module.derive_delta(_snapshot_candidate_fixture(rainy_days=0),prev,region="kld")["rain"]["direction"]=="drier"
    assert "rain" not in snapshot_module.derive_delta(_snapshot_candidate_fixture(rainy_days=3),prev,region="kld")
    assert snapshot_module.derive_delta(_snapshot_candidate_fixture(windy_days=4),prev,region="kld")["wind"]["direction"]=="windier"
    assert snapshot_module.derive_delta(_snapshot_candidate_fixture(windy_days=0),prev,region="kld")["wind"]["direction"]=="calmer"
    assert snapshot_module.derive_delta(_snapshot_candidate_fixture(windy_days=2,gust_peak=15.0),prev,region="kld")["wind"]["direction"]=="windier"
    calm_prev=_auth(_previous_candidate(windy_days=2,gust_peak=15.0),404)
    assert snapshot_module.derive_delta(_snapshot_candidate_fixture(windy_days=2,gust_peak=12.0),calm_prev,region="kld")["wind"]["direction"]=="calmer"
    conflict_prev=_auth(_previous_candidate(windy_days=4,gust_peak=12.0),405)
    assert snapshot_module.derive_delta(_snapshot_candidate_fixture(windy_days=2,gust_peak=16.0),conflict_prev,region="kld")["wind"]["direction"]=="mixed"

def test_weekly_sea_delta_both_directions_and_complete_coverage() -> None:
    prev=_auth(_previous_candidate(),501); cur=_snapshot_candidate_fixture()
    assert snapshot_module.derive_delta(cur,prev,region="kld")["sea"]["direction"]=="warmer"
    cold_prev=_auth(_previous_candidate(),502); cold_prev=dict(cold_prev,sea_mean_c=22.0,sea_min_c=22.0,sea_max_c=22.0)
    assert snapshot_module.derive_delta(cur,cold_prev,region="kld")["sea"]["direction"]=="colder"
    assert "sea" not in snapshot_module.derive_delta(dict(cur,sea_sample_count=len(weekly_module.SEA_POINTS)-1),prev,region="kld")

def test_weekly_snapshot_rain_evidence_missing_fields_fail_soft() -> None:
    def classify(prob, code):
        payload=json.loads(json.dumps(WEATHER))
        payload["daily"]["precipitation_probability_max"]=[prob]*7
        payload["daily"]["weathercode"]=[code]*7
        rows=weekly_module._snapshot_weather_days(payload,date(2026,7,1))
        assert len(rows)==7
        return rows[0]["rainy"], payload

    rainy,_=classify(20,None)
    assert rainy is False
    rainy,_=classify(60,None)
    assert rainy is True
    rainy,_=classify(None,61)
    assert rainy is True
    rainy,_=classify(None,3)
    assert rainy is False
    rainy,missing=classify(None,None)
    assert rainy is None

    candidate=weekly_module._build_snapshot_candidate(
        date(2026,7,1),
        missing,
        [20.0]*len(weekly_module.SEA_POINTS),
    )
    assert candidate is not None
    assert candidate["rainy_day_count"] is None
    assert "rain" not in snapshot_module.derive_delta(candidate,_auth(_previous_candidate()),region="kld")


def test_weekly_incomplete_current_is_not_snapshot_authority() -> None:
    partial=json.loads(json.dumps(WEATHER)); partial["daily"]["time"]=partial["daily"]["time"][:-1]
    for key,values in list(partial["daily"].items()):
        if key!="time" and isinstance(values,list): partial["daily"][key]=values[:-1]
    assert weekly_module._build_snapshot_candidate(date(2026,7,1),partial,[20.0]*len(weekly_module.SEA_POINTS)) is None

def _run_snapshot_authority_case(*, cover_fails=False, text_fails=False, production=True) -> bool:
    old_token=os.environ.get("TELEGRAM_TOKEN_KLG"); old_telegram=sys.modules.get("telegram"); old_renderer=weekly_module.render_weekly_cover
    class Message: message_id=777
    class ParseMode: HTML="HTML"
    class Constants: pass
    Constants.ParseMode=ParseMode
    class Bot:
        def __init__(self,token): pass
        async def send_photo(self,**kwargs): return object()
        async def send_message(self,**kwargs):
            if text_fails: raise RuntimeError("synthetic text failure")
            return Message()
    module=ModuleType("telegram"); module.Bot=Bot; module.constants=Constants
    with tempfile.TemporaryDirectory() as tmp:
        out=Path(tmp)/"snapshot.json"
        def fake_render(text: str, *, start: date, output_path: str | Path):
            if cover_fails: raise RuntimeError("synthetic cover failure")
            path=Path(tmp)/"cover.png"; path.write_bytes(b"fixture"); return {"path":str(path)}
        try:
            os.environ["TELEGRAM_TOKEN_KLG"]="test"; sys.modules["telegram"]=module; weekly_module.render_weekly_cover=fake_render
            try:
                asyncio.run(weekly_module._send("weekly text","-100123",date(2026,7,6),snapshot_candidate=_snapshot_candidate_fixture(),
                    production_snapshot_out=out,production_chat_id="-100123" if production else "-100999"))
            except RuntimeError: pass
            return out.exists()
        finally:
            weekly_module.render_weekly_cover=old_renderer
            if old_telegram is None: sys.modules.pop("telegram",None)
            else: sys.modules["telegram"]=old_telegram
            if old_token is None: os.environ.pop("TELEGRAM_TOKEN_KLG",None)
            else: os.environ["TELEGRAM_TOKEN_KLG"]=old_token

def test_weekly_snapshot_authority_follows_production_text_success() -> None:
    assert _run_snapshot_authority_case(cover_fails=True) is True
    assert _run_snapshot_authority_case(text_fails=True) is False
    assert _run_snapshot_authority_case(production=False) is False

def test_weekly_first_run_without_history_and_delta_labeling() -> None:
    text=build_weekly_forecast(date(2026,7,1),weather_payload=WEATHER,air_data=AIR,sea_temps=[20.1,21.8,20.6],kp_tuple=KP,lunar_data=LUNAR,
        astro_events_paths=[Path("__missing_astro_events.json")],delta_lines=[])
    assert "↔️ К прошлому недельному прогнозу" not in text
    delta=build_weekly_forecast(date(2026,7,1),weather_payload=WEATHER,air_data=AIR,sea_temps=[20.1,21.8,20.6],kp_tuple=KP,lunar_data=LUNAR,
        astro_events_paths=[Path("__missing_astro_events.json")],delta_lines=["Температура: новый недельный прогноз заметно теплее."])
    assert "↔️ К прошлому недельному прогнозу" in delta and "фактически была" not in delta

def test_weekly_snapshot_workflow_contract_is_production_only_and_30_days() -> None:
    workflow=(ROOT/".github"/"workflows"/"weekly_forecast.yml").read_text("utf-8")
    assert 'if [ "${DRY_RUN:-}" = "true" ]; then' in workflow
    assert 'if [ "${labels[$i]}" = "production" ]' in workflow
    assert "--production-snapshot-out" in workflow and "retention-days: 30" in workflow
    assert "weekly-snapshot-kld-" in workflow and "canonical-exists" in workflow


def main() -> None:
    checks = (
        test_weekly_snapshot_serialization_and_validation,
        test_weekly_snapshot_rejects_corrupt_wrong_region_week_and_incomplete,
        test_weekly_snapshot_selects_exact_previous_and_earliest_canonical,
        test_weekly_delta_all_threshold_directions,
        test_weekly_sea_delta_both_directions_and_complete_coverage,
        test_weekly_snapshot_rain_evidence_missing_fields_fail_soft,
        test_weekly_incomplete_current_is_not_snapshot_authority,
        test_weekly_snapshot_authority_follows_production_text_success,
        test_weekly_first_run_without_history_and_delta_labeling,
        test_weekly_snapshot_workflow_contract_is_production_only_and_30_days,
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
