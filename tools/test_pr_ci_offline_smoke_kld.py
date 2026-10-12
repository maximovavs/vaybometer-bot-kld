#!/usr/bin/env python3
"""Deterministic CI entrypoint smoke for daily and FX dry-run routing.

Real weather and exchange-rate acquisition are deliberately replaced by
injected fixtures.  Format quality remains covered by the dedicated FORMAT_V2,
weekly, FX and visual regression suites.
"""
from __future__ import annotations

import asyncio
from contextlib import redirect_stdout
from io import StringIO
import os
import sys
from unittest.mock import patch

# Must be set BEFORE importing post_kld because it validates token at import.
os.environ["TELEGRAM_TOKEN_KLG"] = "test"
os.environ["CHANNEL_ID_KLG"] = "-1001234567890"
os.environ["TZ"] = "Europe/Kaliningrad"

import post_kld  # noqa: E402
import fx  # noqa: E402

FIXTURE_MESSAGE = "🌅 <b>Калининградская область завтра</b>\n🏙 Калининград: 15/9 °C\n#Калининград #погода"
FIXTURE_RATES = {
    "date": "2025-09-05",
    "USD": {"value": 81.5, "delta": 0.2},
    "EUR": {"value": 93.0, "delta": -0.1},
    "CNY": {"value": 11.4, "delta": 0.0},
}


class NoSendBot:
    """Sends must fail tests even if the runtime ignores --dry-run."""

    def __init__(self, *, token: str):
        assert token == "test", "CI must never import a production token"

    async def get_me(self, *args, **kwargs):
        raise AssertionError("dry-run attempted Telegram get_me")

    async def send_message(self, *args, **kwargs):
        raise AssertionError("dry-run attempted Telegram send_message")

    async def send_photo(self, *args, **kwargs):
        raise AssertionError("dry-run attempted Telegram send_photo")


def test_daily_cli_dry_run() -> None:
    calls = []

    def fake_build_message(**kwargs):
        calls.append(kwargs)
        return FIXTURE_MESSAGE

    stream = StringIO()
    with (
        patch.object(post_kld, "Bot", NoSendBot),
        patch.object(post_kld, "build_message", fake_build_message),
        patch.object(sys, "argv", ["post_kld.py", "--date", "2025-09-05", "--for-tomorrow", "--dry-run"]),
        patch.dict(os.environ, {}, clear=False),
        redirect_stdout(stream),
    ):
        asyncio.run(post_kld.main_kld())
    assert len(calls) == 1, "daily smoke must invoke message builder exactly once"
    assert calls[0]["region_name"] == "Калининградская область"
    assert "ECHO MESSAGE BEGIN" in stream.getvalue()
    assert FIXTURE_MESSAGE in stream.getvalue()
    print("PASS daily entrypoint fixture dry-run")


def test_fx_cli_dry_run() -> None:
    load_calls = []

    def fake_load_rates(_date, _tz):
        load_calls.append((_date, _tz))
        return FIXTURE_RATES

    with (
        patch.object(post_kld, "Bot", NoSendBot),
        patch.object(post_kld, "_load_fx_rates", fake_load_rates),
        patch.object(fx, "should_publish_again", return_value=True),
        patch.object(sys, "argv", ["post_kld.py", "--date", "2025-09-05", "--fx-only", "--dry-run"]),
        patch.dict(os.environ, {}, clear=False),
    ):
        asyncio.run(post_kld.main_kld())
    assert len(load_calls) == 1, "FX smoke must request fixture rates exactly once"
    print("PASS FX entrypoint fixture dry-run")


def main():
    test_daily_cli_dry_run()
    test_fx_cli_dry_run()


if __name__ == "__main__":
    main()
