#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Focused tests for separate KLD FX + Market Pulse posts."""
from __future__ import annotations

import os
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("TELEGRAM_TOKEN_KLG", "test-token")

telegram = types.ModuleType("telegram")
telegram.Bot = object
telegram.constants = types.SimpleNamespace(ParseMode=types.SimpleNamespace(HTML="HTML"))
sys.modules.setdefault("telegram", telegram)

pendulum = types.ModuleType("pendulum")
pendulum.DateTime = object
pendulum.Timezone = object
sys.modules.setdefault("pendulum", pendulum)

post_common = types.ModuleType("post_common")
post_common.build_message = lambda *args, **kwargs: ""
post_common.fx_morning_line = lambda *args, **kwargs: None
sys.modules.setdefault("post_common", post_common)

import post_kld  # noqa: E402
import post_kld_fx_market_pulse as pulse  # noqa: E402


def kld_fx_message_uses_arrows_and_summary() -> None:
    post_kld._load_fx_rates = lambda _date, _tz: {
        "date": "2026-06-27",
        "USD": {"value": 77.06, "delta": 1.43},
        "EUR": {"value": 87.40, "delta": 1.63},
        "CNY": {"value": 11.34, "delta": 0.29},
    }
    text, _rates = post_kld._build_fx_message(None, None)
    assert "💱 <b>Курсы ЦБ РФ на 27.06</b>" in text
    assert "USD 77.06 ₽ ↑1.43 · EUR 87.40 ₽ ↑1.63 · CNY 11.34 ₽ ↑0.29" in text
    assert "🧭 Рубль слабее к USD, EUR и CNY." in text
    assert "валюты подросли к ₽" not in text
    assert "(" not in text and ")" not in text


def kld_fx_summary_negative_is_stronger() -> None:
    post_kld._load_fx_rates = lambda _date, _tz: {
        "date": "2026-06-27",
        "USD": {"value": 77.06, "delta": -1.43},
        "EUR": {"value": 87.40, "delta": -1.63},
        "CNY": {"value": 11.34, "delta": -0.29},
    }
    text, _rates = post_kld._build_fx_message(None, None)
    assert "🧭 Рубль крепче к USD, EUR и CNY." in text


def kld_fx_summary_mixed_is_mixed() -> None:
    post_kld._load_fx_rates = lambda _date, _tz: {
        "date": "2026-06-27",
        "USD": {"value": 77.06, "delta": 1.43},
        "EUR": {"value": 87.40, "delta": -1.63},
        "CNY": {"value": 11.34, "delta": 0.29},
    }
    text, _rates = post_kld._build_fx_message(None, None)
    assert "🧭 Валюты к ₽ движутся смешанно." in text


def kld_market_pulse_is_compact() -> None:
    pulse._fetch_crypto = lambda: ["24ч: BTC $60.3K ↑1.2% · ETH $1.6K ↑2.0%"]
    pulse._fetch_gold = lambda: ["Gold/oz $4.1K"]
    block = pulse.build_market_pulse_block()
    assert "📊 <b>Пульс рынков</b>" in block
    assert "24ч: BTC $60.3K ↑1.2% · ETH $1.6K ↑2.0%" in block
    assert "Gold/oz $4.1K" in block
    assert "Gold/oz:" not in block
    assert "Инфо-ориентир, не инвестрекомендация." in block
    assert "(" not in block
    text = pulse.inject_market_pulse("💱 <b>Курсы ЦБ РФ на 27.06</b>", block)
    assert "#Калининград #курсы_валют #рынки" in text


def main() -> None:
    checks = (
        kld_fx_message_uses_arrows_and_summary,
        kld_fx_summary_negative_is_stronger,
        kld_fx_summary_mixed_is_mixed,
        kld_market_pulse_is_compact,
    )
    for check in checks:
        check()
        print(f"PASS {check.__name__}")
    print(f"OK: {len(checks)} KLD FX/Market Pulse checks passed")


if __name__ == "__main__":
    main()
