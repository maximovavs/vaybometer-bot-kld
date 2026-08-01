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


def _rates(usd: float | None, eur: float | None, cny: float | None) -> dict:
    return {
        "date": "2026-08-01",
        "USD": {"value": 79.46, "delta": usd},
        "EUR": {"value": 91.19, "delta": eur},
        "CNY": {"value": 11.77, "delta": cny},
    }


def kld_fx_message_keeps_numeric_line() -> None:
    post_kld._load_fx_rates = lambda _date, _tz: _rates(-0.39, 0.31, -0.05)
    text, _rates_data = post_kld._build_fx_message(None, None)
    assert "💱 <b>Курсы ЦБ РФ на 01.08</b>" in text
    assert "USD 79.46 ₽ ↓0.39 · EUR 91.19 ₽ ↑0.31 · CNY 11.77 ₽ ↓0.05" in text


def kld_plain_summary_explains_mixed_moves() -> None:
    rates = _rates(-0.39, 0.31, -0.05)
    raw = "💱 Курсы\nUSD/EUR/CNY\n🧭 Валюты к ₽ движутся смешанно."
    text = pulse.replace_ruble_summary(raw, rates)
    assert text.endswith(
        "🧭 К рублю: доллар подешевел на 39 коп., евро подорожал на 31 коп., "
        "юань подешевел на 5 коп."
    )
    assert "движутся смешанно" not in text
    assert "Рубль слабее" not in text
    assert "Рубль крепче" not in text


def kld_plain_summary_uses_rubles_for_large_moves() -> None:
    summary = pulse.build_plain_ruble_summary(_rates(1.43, -1.63, 0.29))
    assert summary == (
        "🧭 К рублю: доллар подорожал на 1,43 ₽, евро подешевел на 1,63 ₽, "
        "юань подорожал на 29 коп."
    )


def kld_plain_summary_handles_zero_and_missing() -> None:
    rates = _rates(0.0, None, 0.004)
    summary = pulse.build_plain_ruble_summary(rates)
    assert summary == "🧭 К рублю: доллар почти не изменился, юань почти не изменился."


def kld_plain_summary_is_appended_when_old_line_missing() -> None:
    rates = _rates(-0.39, 0.31, -0.05)
    text = pulse.replace_ruble_summary("💱 Курсы\nUSD/EUR/CNY", rates)
    assert text.count("🧭 К рублю:") == 1


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
        kld_fx_message_keeps_numeric_line,
        kld_plain_summary_explains_mixed_moves,
        kld_plain_summary_uses_rubles_for_large_moves,
        kld_plain_summary_handles_zero_and_missing,
        kld_plain_summary_is_appended_when_old_line_missing,
        kld_market_pulse_is_compact,
    )
    for check in checks:
        check()
        print(f"PASS {check.__name__}")
    print(f"OK: {len(checks)} KLD FX/Market Pulse checks passed")


if __name__ == "__main__":
    main()
