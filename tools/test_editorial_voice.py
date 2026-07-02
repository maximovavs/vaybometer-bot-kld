#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regression checks for KLD deterministic editorial voice."""
from __future__ import annotations

import re
import sys
import types
from datetime import date
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

pendulum_stub = types.ModuleType("pendulum")
pendulum_stub.DateTime = object
sys.modules.setdefault("pendulum", pendulum_stub)

telegram_stub = types.ModuleType("telegram")
telegram_stub.Bot = object
telegram_stub.constants = types.SimpleNamespace(ParseMode=types.SimpleNamespace(HTML="HTML"))
sys.modules.setdefault("telegram", telegram_stub)

from editorial_voice import KLD_EVENING_VARIANTS, KLD_VARIANTS, deterministic_variant  # noqa: E402
from format_v2 import build_evening_format_v2, build_morning_format_v2  # noqa: E402
from safe_test_post import _apply_editorial_voice  # noqa: E402
from send_weekly_forecast import build_weekly_forecast  # noqa: E402


FORBIDDEN = (
    "доверьтесь Вселенной",
    "повысьте вибрации",
    "энергии дня требуют",
    "судьбоносный период",
    "трансформация",
    "проявленность",
    "слушайте знаки",
    "аварии",
    "чрезвычайные ситуации",
    "операции лучше отложить",
    "воздушном пространстве",
)

MORNING = """<b>🌅 Калининградская область: погода на сегодня (27.06.2026)</b>
✨ VayboMeter сегодня: 7.9/10 — тёплый день.
Погода: 🏙️ Калининград — 26/18 °C • ясно • 💨 4.0 м/с • давл. 1015 гПа.
Балтийск: 21/15 °C • ясно • 🌊 21 • 0.3 м
Гвардейск: 27/16 °C • ясно
☀️ УФ: 7 — высокий.
🏭 Воздух: AQI 58 • PM₂.₅ 12 / PM₁₀ 24
🧲 Космопогода: спокойно, Kp 0.3.
🌇 Закат сегодня: 21:33
📻 <b>Астрособытия</b>
🌙 🌕 Полнолуние, ♐ (96%)
💚 В плюсе: прогулки, планы.
#Калининград #погода #здоровье #сегодня #море
"""

EVENING = """<b>🌅 Калининградская область: погода на завтра (28.06.2026)</b>
✨ VayboMeter завтра: 6.6/10 — рабочий день; местами осадки.
Погода: 🏙️ Калининград — 21/16 °C • 🌦 местами дождь • 💨 6 м/с.
🌊 <b>Морские города</b>
Балтийск: 19/15 °C • 🌦 дождь • 💨 8 м/с • порывы до 12 м/с • 🌊 21 • 0.4 м
———
🌡 <b>Тёплые города</b>
• Гвардейск: 23/15 °C • 🌦 местами дождь
🌅 Рассвет завтра: 04:08
🌇 Закат завтра: 21:33
📻 <b>Астрособытия</b>
🌙 🌔 Растущая Луна (86%) • ♎ Весы
💚 В плюсе: баланс, прогулки.
#Калининград #погода #здоровье #море
"""

SAFE_WARM_UV = """<b>🌅 Калининград сегодня (27.06.2026)</b>
✨ VayboMeter: 7.9/10 — с оговорками; тёплый день и высокий УФ.
Погода: 🏙️ Калининград — 26/18 °C • ясно • 💨 2.6 м/с • порывы до 6 м/с.
☀️ УФ 7 — высокий.
✅ План: прогулка в удобное окно; днём — SPF, очки/кепка, у воды учитывать ветер.
#Калининград #погода #здоровье #сегодня #море
"""

SAFE_WINDY = """<b>🌅 Калининград сегодня (27.06.2026)</b>
✨ VayboMeter: 7.4/10 — с оговорками; у воды порывы.
Погода: 🏙️ Калининград — 23/17 °C • ясно • 💨 4 м/с • порывы до 10 м/с.
☀️ УФ 4 — умеренный.
✅ План: прогулку держать гибкой.
#Калининград #погода #здоровье #сегодня #море
"""

WEATHER = {
    "daily": {
        "time": ["2026-07-01", "2026-07-02", "2026-07-03", "2026-07-04", "2026-07-05", "2026-07-06", "2026-07-07"],
        "temperature_2m_max": [21, 22, 23, 24, 22, 20, 19],
        "temperature_2m_min": [14, 15, 16, 16, 15, 14, 13],
        "wind_speed_10m_max": [6, 8, 8, 7, 6, 5, 5],
        "wind_gusts_10m_max": [9, 12, 11, 10, 8, 7, 7],
        "precipitation_probability_max": [20, 45, 50, 35, 20, 10, 10],
        "weathercode": [3, 61, 63, 3, 2, 1, 1],
        "uv_index_max": [4, 5, 5, 4, 4, 3, 3],
    }
}

LUNAR = {
    "days": {
        "2026-07-01": {"phase_name": "Полнолуние", "percent": 99},
        "2026-07-07": {"phase_name": "Убывающая Луна", "percent": 75},
    }
}


class _Parser(HTMLParser):
    pass


def _assert_clean(text: str) -> None:
    low = text.lower()
    assert not any(phrase.lower() in low for phrase in FORBIDDEN)
    assert "Кипр" not in text
    assert "море зовёт" not in low
    assert text.splitlines()[-1].startswith("#")
    _Parser().feed(text)


def _voice_line(text: str, prefix: str) -> str:
    return next(line for line in text.splitlines() if line.startswith(prefix))


def _phrases(bank: dict[str, list[str]], scenario: str | None = None) -> set[str]:
    if scenario:
        return set(bank[scenario])
    out: set[str] = set()
    for values in bank.values():
        out.update(values)
    return out


def test_deterministic_variant_is_stable_and_rotates() -> None:
    variants = KLD_VARIANTS["WINDY_BALTIC"]
    first = deterministic_variant("Калининград", "2026-07-01", "WINDY_BALTIC", variants)
    second = deterministic_variant("Калининград", "2026-07-01", "WINDY_BALTIC", variants)
    assert first == second
    rotated = {
        deterministic_variant("Калининград", f"2026-07-{day:02d}", "WINDY_BALTIC", variants)
        for day in range(1, 15)
    }
    assert len(rotated) > 1
    assert "hash(" not in (ROOT / "editorial_voice.py").read_text(encoding="utf-8")


def test_morning_output_has_one_human_line_and_keeps_facts() -> None:
    text = build_morning_format_v2("Калининградская область", MORNING)
    assert text.count("💬 По-человечески:") == 1
    assert "Калининград — 26/18 °C" in text
    assert "Гвардейск (27°)" in text
    assert "Балтика: вода 21°C" in text
    assert "Kp 0.3" in text
    assert "96% освещённости" in text
    _assert_clean(text)


def test_safe_low_wind_high_uv_selects_warm_uv() -> None:
    text = _apply_editorial_voice(SAFE_WARM_UV, "morning")
    line = _voice_line(text, "💬 По-человечески:")
    phrase = line.split(": ", 1)[1]
    assert phrase in _phrases(KLD_VARIANTS, "WARM_UV")
    assert phrase not in _phrases(KLD_VARIANTS, "WINDY_BALTIC")
    assert "2.6 м/с" in text and "порывы до 6 м/с" in text


def test_safe_gust_10_selects_windy_baltic() -> None:
    text = _apply_editorial_voice(SAFE_WINDY, "morning")
    line = _voice_line(text, "💬 По-человечески:")
    phrase = line.split(": ", 1)[1]
    assert phrase in _phrases(KLD_VARIANTS, "WINDY_BALTIC")
    assert "порывы до 10 м/с" in text


def test_evening_output_has_one_human_line_and_keeps_facts() -> None:
    text = build_evening_format_v2("Калининградская область", EVENING)
    assert text.count("💬 Настрой на завтра:") == 1
    line = _voice_line(text, "💬 Настрой на завтра:")
    phrase = line.split(": ", 1)[1]
    assert phrase in _phrases(KLD_EVENING_VARIANTS)
    assert not re.search(r"(?<![А-Яа-яЁё])сегодня(?![А-Яа-яЁё])", line, flags=re.I)
    assert "Калининград — 21/16 °C" in text
    assert "Балтийск: 19/15 °C" in text
    assert "порывы до 12 м/с" in text
    assert "86% освещённости" in text
    _assert_clean(text)


def test_weekly_output_contains_meaning_block_and_keeps_facts() -> None:
    text = build_weekly_forecast(
        date(2026, 7, 1),
        weather_payload=WEATHER,
        air_data={"aqi": 58, "pm25": 12, "pm10": 24},
        sea_temps=[20.1, 21.8, 20.6],
        kp_tuple=(1.0, "спокойно", 123456, "fixture"),
        lunar_data=LUNAR,
        astro_events_paths=[Path("__missing_astro_events.json")],
    )
    assert "🌿 Смысл недели" in text
    assert text.index("🌿 Смысл недели") > text.index("✨ Главный фон недели")
    assert text.index("🌿 Смысл недели") < text.index("🌦 Погода")
    assert "Температура держится в диапазоне 19–24°C" in text
    assert "AQI 58" in text
    assert "Вода 20–22°C" in text
    assert "Kp 1.0" in text
    assert "Полнолуние" in text
    _assert_clean(text)


def main() -> None:
    checks = (
        test_deterministic_variant_is_stable_and_rotates,
        test_morning_output_has_one_human_line_and_keeps_facts,
        test_safe_low_wind_high_uv_selects_warm_uv,
        test_safe_gust_10_selects_windy_baltic,
        test_evening_output_has_one_human_line_and_keeps_facts,
        test_weekly_output_contains_meaning_block_and_keeps_facts,
    )
    for check in checks:
        check()
        print(f"PASS {check.__name__}")
    print(f"OK: {len(checks)} KLD editorial voice checks passed")


if __name__ == "__main__":
    main()
