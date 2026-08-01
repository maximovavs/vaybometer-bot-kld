#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Focused checks for plain-language KLD FX movement summaries."""
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

import post_kld_fx_market_pulse as pulse  # noqa: E402


def mixed_production_case_is_explained_plainly() -> None:
    rates = {
        "USD": {"delta": -0.39},
        "EUR": {"delta": 0.31},
        "CNY": {"delta": -0.05},
    }
    summary = pulse.build_plain_ruble_summary(rates)
    assert summary == (
        "🧭 Проще: по сравнению с прошлым курсом ЦБ "
        "доллар и юань — дешевле, евро — дороже."
    )


def all_dearer_is_grouped() -> None:
    rates = {
        "USD": {"delta": 0.10},
        "EUR": {"delta": 0.20},
        "CNY": {"delta": 0.03},
    }
    assert pulse.build_plain_ruble_summary(rates) == (
        "🧭 Проще: по сравнению с прошлым курсом ЦБ доллар, евро и юань — дороже."
    )


def all_cheaper_is_grouped() -> None:
    rates = {
        "USD": {"delta": -0.10},
        "EUR": {"delta": -0.20},
        "CNY": {"delta": -0.03},
    }
    assert pulse.build_plain_ruble_summary(rates) == (
        "🧭 Проще: по сравнению с прошлым курсом ЦБ доллар, евро и юань — дешевле."
    )


def zero_delta_is_explained() -> None:
    rates = {
        "USD": {"delta": 0},
        "EUR": {"delta": 0.20},
        "CNY": {"delta": -0.03},
    }
    assert pulse.build_plain_ruble_summary(rates) == (
        "🧭 Проще: по сравнению с прошлым курсом ЦБ юань — дешевле, "
        "евро — дороже, доллар — без изменений."
    )


def existing_navigation_line_is_replaced() -> None:
    fx_text = "\n".join(
        [
            "💱 <b>Курсы ЦБ РФ на 01.08</b>",
            "USD 79.46 ₽ ↓0.39 · EUR 91.19 ₽ ↑0.31 · CNY 11.77 ₽ ↓0.05",
            "🧭 Валюты к ₽ движутся смешанно.",
        ]
    )
    rates = {
        "USD": {"delta": -0.39},
        "EUR": {"delta": 0.31},
        "CNY": {"delta": -0.05},
    }
    text = pulse.apply_plain_ruble_summary(fx_text, rates)
    assert "Валюты к ₽ движутся смешанно" not in text
    assert "доллар и юань — дешевле, евро — дороже" in text
    assert text.count("🧭") == 1


def missing_deltas_leave_text_unchanged() -> None:
    fx_text = "💱 Курсы\n🧭 Валюты к ₽ движутся смешанно."
    assert pulse.apply_plain_ruble_summary(fx_text, {}) == fx_text


def main() -> None:
    checks = (
        mixed_production_case_is_explained_plainly,
        all_dearer_is_grouped,
        all_cheaper_is_grouped,
        zero_delta_is_explained,
        existing_navigation_line_is_replaced,
        missing_deltas_leave_text_unchanged,
    )
    for check in checks:
        check()
        print(f"PASS: {check.__name__}")
    print(f"OK: {len(checks)} KLD FX plain-language checks passed")


if __name__ == "__main__":
    main()
