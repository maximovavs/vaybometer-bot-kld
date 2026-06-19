#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regression checks for KLD morning FORMAT_V2 utility blocks."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from format_v2 import build_morning_format_v2  # noqa: E402


LEGACY_FIXTURE = """<b>🌅 Калининградская область: погода на сегодня (19.06.2026)</b>
Погода: 🏙️ Калининград — 22/14 °C • облачно • 💨 4.0 м/с • 🔹 1014 гПа.
💱 Курсы (утро): USD 90.00 ₽ • EUR 98.00 ₽ • CNY 12.00 ₽
☀️ УФ: 4 — умеренный
🏭 Воздух: 🟢 низкий (AQI 22) • PM₂.₅ 5 / PM₁₀ 10
🌇 Закат сегодня: 21:32
🧪 Safecast: 0.12 мкЗв/ч — фон спокойный.
✅ Сегодня: прогулка в удобное окно.
#Калининград #погода #здоровье #сегодня #море
"""


def kld_morning_has_sunset() -> None:
    text = build_morning_format_v2("Калининградская область", LEGACY_FIXTURE)
    assert "🌇 Закат сегодня: 21:32" in text
    assert text.index("🏭 Воздух:") < text.index("🌇 Закат сегодня:") < text.index("✅ План:")


def kld_morning_keeps_safecast() -> None:
    os.environ["FORMAT_V2_SENSOR_LINE"] = "1"
    from safe_test_post import _inject_sensor_line

    text = build_morning_format_v2("Калининградская область", LEGACY_FIXTURE)
    text = _inject_sensor_line(text, LEGACY_FIXTURE)
    assert "Safecast" in text
    assert text.count("Safecast") == 1


def kld_morning_keeps_fx_uv_air_plan() -> None:
    text = build_morning_format_v2("Калининградская область", LEGACY_FIXTURE)
    for marker in ("💱 Курсы", "☀️ УФ", "🏭 Воздух", "✅ План:"):
        assert marker in text


def main() -> None:
    checks = (
        kld_morning_has_sunset,
        kld_morning_keeps_safecast,
        kld_morning_keeps_fx_uv_air_plan,
    )
    for check in checks:
        check()
        print(f"PASS {check.__name__}")
    print(f"OK: {len(checks)} KLD morning FORMAT_V2 regression checks passed")


if __name__ == "__main__":
    main()
