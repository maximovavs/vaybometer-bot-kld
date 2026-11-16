#!/usr/bin/env python3
"""KLD frozen-weekly-payload checks; no weather, provider, or Telegram traffic."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import send_weekly_forecast as weekly

START = date(2026, 11, 16)
TEXT = (
    "🗓 Вайб недели: 16–22 ноября\n\n🌦 Погода\n"
    "Температура держится в диапазоне 5–9°C; дождевые окна возможны во второй половине недели.\n"
    "🏄 Вода и спорт\nSUP: безопасность по спотам и времени не подтверждена; выход без локальной проверки не планировать.\n"
    + weekly.HASHTAGS
)
CANDIDATE = {
    "region": "kld", "week_start": "2026-11-16", "week_end": "2026-11-22",
    "weather_coverage_complete": True, "weather_coverage_days": 7,
}


def rejected(path: Path, start: date | None = START) -> None:
    try:
        weekly._read_prepared(path, expected_start=start)
    except SystemExit as exc:
        assert "KLD_WEEKLY_PREPARED_REJECTED" in str(exc), exc
    else:
        raise AssertionError("unsafe prepared payload was accepted")


def test_roundtrip_single_source() -> None:
    with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"GITHUB_RUN_ID": "synthetic-001"}):
        path = Path(tmp) / "frozen.json"
        weekly._write_prepared(path, START, TEXT, CANDIDATE)
        assert weekly._read_prepared(path, START) == (START, TEXT, CANDIDATE)
        assert weekly._read_prepared(path) == (START, TEXT, CANDIDATE)
        assert json.loads(path.read_text(encoding="utf-8"))["sha256"]


def test_integrity_date_lifecycle_and_run_guards() -> None:
    with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"GITHUB_RUN_ID": "synthetic-001"}):
        path = Path(tmp) / "frozen.json"
        weekly._write_prepared(path, START, TEXT, CANDIDATE)
        original = json.loads(path.read_text(encoding="utf-8"))
        rejected(path, date(2026, 11, 23))
        for key, mutation in (
            ("text", TEXT.replace("не подтверждена", "подтверждена")),
            ("snapshot_candidate", dict(CANDIDATE, week_start="2026-11-23")),
            ("week_start", "2026-11-23"),
        ):
            tampered = dict(original, **{key: mutation})
            path.write_text(json.dumps(tampered, ensure_ascii=False), encoding="utf-8")
            rejected(path)
        # K1: unrelated numeric weather edits must fail the checksum even when
        # schema, week, hashtags and SUP guard remain structurally valid.
        numeric_tamper = dict(original, text=TEXT.replace("5–9°C", "25–29°C"))
        path.write_text(json.dumps(numeric_tamper, ensure_ascii=False), encoding="utf-8")
        rejected(path)
        for mutation in (
            dict(original, created_at_utc=(datetime.now(timezone.utc) - timedelta(minutes=31)).isoformat()),
            dict(original, created_at_utc=(datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()),
            dict(original, github_run_id="other-run"),
            dict(original, region="cyprus"),
            dict(original, snapshot_candidate=dict(CANDIDATE, week_start="2026-11-23")),
            dict(original, text=TEXT.replace("Температура держится в диапазоне 5–9°C", "Полный прогноз на все 7 дней пока не собран")),
        ):
            mutation.pop("sha256", None)
            mutation["sha256"] = weekly._prepared_digest(mutation)
            path.write_text(json.dumps(mutation, ensure_ascii=False), encoding="utf-8")
            rejected(path)
        # K7: valid digest must NOT make structurally incomplete publication safe.
        for incomplete_text in (
            TEXT.replace(weekly.HASHTAGS, ""),
            TEXT.replace("SUP: безопасность по спотам и времени не подтверждена; выход без локальной проверки не планировать.", ""),
        ):
            malformed = dict(original, text=incomplete_text)
            malformed.pop("sha256", None)
            malformed["sha256"] = weekly._prepared_digest(malformed)
            path.write_text(json.dumps(malformed, ensure_ascii=False), encoding="utf-8")
            rejected(path)
        path.write_text("{unreadable", encoding="utf-8")
        rejected(path)


def test_consumer_never_fetches_and_two_destinations_use_same_payload() -> None:
    with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"GITHUB_RUN_ID": "synthetic-001"}):
        path = Path(tmp) / "frozen.json"
        weekly._write_prepared(path, START, TEXT, CANDIDATE)
        attempts = []
        async def fake_send(text, chat_id, start, **kwargs):
            attempts.append((text, chat_id, start, kwargs["snapshot_candidate"]))
            return 42
        def forbid_fetch(*args, **kwargs):
            raise AssertionError("consumer unexpectedly fetched live facts")
        with patch.object(weekly, "_send", fake_send), \
             patch.object(weekly, "_fetch_weather", forbid_fetch), \
             patch.object(weekly, "_fetch_sea_temps", forbid_fetch), \
             patch.object(weekly, "_fetch_air", forbid_fetch), \
             patch.object(weekly, "_fetch_kp", forbid_fetch), \
             patch.object(weekly, "_load_lunar_calendar", forbid_fetch):
            for chat in ("test", "production"):
                with patch.object(sys, "argv", ["send_weekly_forecast.py", "--date", "2026-11-16",
                                               "--prepared-in", str(path), "--send", "--chat-id", chat]):
                    asyncio.run(weekly.main())
        assert len(attempts) == 2
        assert {x[0] for x in attempts} == {TEXT}
        assert {x[2] for x in attempts} == {START}
        assert all(x[3] == CANDIDATE for x in attempts)


def test_preview_freezes_fallback_from_14nov_without_second_weather_call() -> None:
    # The real 14 Nov run printed an incomplete preview and a second, numeric send.
    # This fixture tests the safe incomplete path. It is not a replay of raw API responses.
    with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"GITHUB_RUN_ID": "synthetic-001"}):
        path = Path(tmp) / "frozen.json"
        calls = {"weather": 0}
        def weather(_start):
            calls["weather"] += 1
            return {"daily": {"time": []}}
        with patch.object(weekly, "_fetch_weather", weather), \
             patch.object(weekly, "_fetch_sea_temps", return_value=[]), \
             patch.object(weekly, "_fetch_air", return_value={}), \
             patch.object(weekly, "_fetch_kp", return_value=None), \
             patch.object(weekly, "_load_lunar_calendar", return_value={}):
            text, candidate = weekly._build_prepared(START)
        assert calls == {"weather": 1}
        assert candidate is None
        assert "Полный прогноз на все 7 дней пока не собран" in text
        weekly._write_prepared(path, START, text, candidate)
        assert weekly._read_prepared(path, START) == (START, text, None)


def test_workflow_reuses_prepared_payload_without_refetch() -> None:
    # K9: a refetching --send command would bypass even a correctly validated payload.
    workflow = (ROOT / ".github" / "workflows" / "weekly_forecast.yml").read_text(encoding="utf-8")
    commands = {line.strip() for line in workflow.splitlines()}
    assert 'python "${args[@]}" --prepared-out "$prepared_payload" | tee weekly_forecast_preview.log' in commands
    assert 'send_args=("${args[@]}" "--prepared-in" "$prepared_payload" "--send" "--chat-id" "${targets[$i]}")' in commands
    assert 'python "${args[@]}" | tee weekly_forecast_preview.log' not in commands


TESTS = (
    test_roundtrip_single_source,
    test_integrity_date_lifecycle_and_run_guards,
    test_consumer_never_fetches_and_two_destinations_use_same_payload,
    test_preview_freezes_fallback_from_14nov_without_second_weather_call,
    test_workflow_reuses_prepared_payload_without_refetch,
)

if __name__ == "__main__":
    for test in TESTS:
        test()
        print("PASS", test.__name__)
    print("PASS KLD frozen weekly payload checks")
