#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Static checks for the KLD weekly forecast workflow schedule."""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "weekly_forecast.yml"


def _read() -> str:
    return WORKFLOW.read_text("utf-8")


def _assert(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{name}: {detail or 'assertion failed'}")


def _week_start_from_scheduled_saturday_anchor(local_date: date) -> date:
    days_since_saturday = (local_date.weekday() - 5) % 7
    scheduled_saturday = local_date - timedelta(days=days_since_saturday)
    return scheduled_saturday + timedelta(days=2)


def test_scheduled_cron_is_event_based() -> None:
    text = _read()
    _assert("weekly_cron_present", 'cron: "0 20 * * 6"' in text)
    _assert("schedule_expr_env", "SCHEDULE_EXPR: ${{ github.event.schedule }}" in text)
    _assert("schedule_expr_guard", '"0 20 * * 6"' in text)
    _assert("no_weekday_guard", "date +%u" not in text)
    _assert("no_hour_guard", "date +%H" not in text)
    _assert("accept_log", "Accepted KLD weekly scheduled event" in text)
    _assert("uses_saturday_anchor", "days_since_saturday = (today.weekday() - 5) % 7" in text)
    _assert("uses_scheduled_saturday", "scheduled_saturday = today - timedelta(days=days_since_saturday)" in text)
    print("PASS scheduled_cron_is_event_based")


def test_scheduled_delays_anchor_to_most_recent_saturday() -> None:
    cases = {
        date(2026, 7, 4): date(2026, 7, 6),
        date(2026, 7, 5): date(2026, 7, 6),
        date(2026, 7, 6): date(2026, 7, 6),
        date(2026, 7, 7): date(2026, 7, 6),
        date(2026, 7, 11): date(2026, 7, 13),
    }
    for local_date, expected in cases.items():
        actual = _week_start_from_scheduled_saturday_anchor(local_date)
        _assert(f"week_start_{local_date.isoformat()}", actual == expected, str(actual))
    print("PASS scheduled_delays_anchor_to_most_recent_saturday")


def test_scheduled_run_passes_explicit_week_start() -> None:
    text = _read()
    _assert("schedule_output_week_start", "echo \"week_start=$week_start\" >> \"$GITHUB_OUTPUT\"" in text)
    _assert(
        "scheduled_week_start_assignment",
        'WEEK_START_DATE="${{ steps.schedule_guard.outputs.week_start }}"' in text,
    )
    _assert("send_uses_date", 'args+=("--date" "$WEEK_START_DATE")' in text)
    _assert("no_empty_scheduled_date", 'WEEK_START_DATE=""' not in text)
    print("PASS scheduled_run_passes_explicit_week_start")


def test_scheduled_routing_is_production_only() -> None:
    text = _read()
    scheduled_branch = text.split('if [ "$GITHUB_EVENT_NAME" = "schedule" ]; then', 1)[1].split("fi", 1)[0]
    _assert("scheduled_override_empty", 'CHANNEL_ID_OVERRIDE=""' in scheduled_branch)
    _assert("scheduled_test_false", 'SEND_TO_TEST="false"' in scheduled_branch)
    _assert("scheduled_prod_true", 'PUBLISH_TO_PROD="true"' in scheduled_branch)
    _assert("scheduled_dry_false", 'DRY_RUN="false"' in scheduled_branch)
    print("PASS scheduled_routing_is_production_only")


def test_manual_date_semantics_remain_unchanged() -> None:
    text = _read()
    _assert("manual_env_date", "WEEK_START_DATE: ${{ github.event.inputs.date }}" in text)
    _assert("manual_input_date", "description: \"Week start date YYYY-MM-DD, empty = today\"" in text)
    _assert("manual_non_schedule_allowed", 'if [ "$GITHUB_EVENT_NAME" != "schedule" ]; then' in text)
    print("PASS manual_date_semantics_remain_unchanged")


TESTS = (
    test_scheduled_cron_is_event_based,
    test_scheduled_delays_anchor_to_most_recent_saturday,
    test_scheduled_run_passes_explicit_week_start,
    test_scheduled_routing_is_production_only,
    test_manual_date_semantics_remain_unchanged,
)


def main() -> None:
    for test in TESTS:
        test()
    print(f"OK: {len(TESTS)} KLD weekly workflow schedule checks passed")


if __name__ == "__main__":
    main()
