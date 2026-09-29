#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Focused checks for the monthly lunar calendar Telegram renderer."""
from __future__ import annotations

import os
import re
import sys
import types
from collections import OrderedDict
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("CHANNEL_ID", "1")
os.environ.setdefault("TELEGRAM_TOKEN_KLG", "test-token")
os.environ.setdefault("CHANNEL_ID_KLG", "1")

telegram_stub = types.ModuleType("telegram")
telegram_stub.Bot = object
telegram_stub.constants = types.SimpleNamespace(ParseMode=types.SimpleNamespace(HTML="HTML"))
sys.modules.setdefault("telegram", telegram_stub)

import pendulum  # noqa: E402
from send_monthly_calendar import TZ, build_message, load_calendar  # noqa: E402


def _day(day: int, phase_name: str, phase: str, sign: str) -> dict:
    return {
        "phase_name": phase_name,
        "phase": phase,
        "sign": sign,
        "long_desc": "Очень длинное повторяющееся описание фазы, которое не должно раздувать мобильный пост.",
    }


def _fixture_days() -> OrderedDict[str, dict]:
    signs = ["Козерог", "Водолей", "Рыбы", "Овен", "Телец", "Близнецы", "Рак", "Лев", "Дева"]
    days: OrderedDict[str, dict] = OrderedDict()
    for day in range(1, 26):
        if day <= 2:
            phase_name, phase = "Полнолуние", "🌕 Полнолуние"
        elif day <= 14:
            phase_name, phase = "Убывающая Луна", "🌖 Убывающая Луна"
        elif day <= 16:
            phase_name, phase = "Новолуние", "🌑 Новолуние"
        else:
            phase_name, phase = "Растущий серп", "🌒 Растущий серп"
        days[f"2026-07-{day:02d}"] = _day(day, phase_name, phase, signs[day % len(signs)])
    return days


def _fixture_cats() -> dict:
    return {
        "general": {
            "favorable": [17, 18, 19, 22],
            "unfavorable": [3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 14, 16, 17, 18, 19, 20, 28, 29, 30, 31],
        },
        "haircut": {"favorable": [5, 12]},
        "shopping": {"favorable": [1, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 14, 16, 28, 29, 30, 31]},
        "health": {"favorable": [6, 14]},
        "travel": {"favorable": [8, 24]},
    }


def _dt(day: int, hour: int, minute: int):
    return pendulum.datetime(2026, 7, day, hour, minute, tz=TZ)


def _fixture_voc() -> list:
    return [
        (_dt(1, 19, 13), _dt(1, 21, 33)),
        (_dt(3, 16, 15), _dt(4, 8, 30)),
        (_dt(5, 9, 0), _dt(5, 10, 0)),
        (_dt(6, 9, 0), _dt(6, 10, 0)),
        (_dt(7, 9, 0), _dt(7, 10, 0)),
        (_dt(8, 9, 0), _dt(8, 10, 0)),
        (_dt(9, 9, 0), _dt(9, 10, 0)),
        (_dt(10, 9, 0), _dt(10, 10, 0)),
        (_dt(11, 4, 0), _dt(11, 4, 30)),
        (_dt(12, 23, 0), _dt(12, 23, 30)),
    ]


def _monthly_text() -> str:
    return build_message(_fixture_days(), _fixture_voc(), _fixture_cats())


def monthly_calendar_has_new_readable_structure() -> None:
    text = _monthly_text()
    assert text.startswith("🌙 Лунный календарь ИЮЛЯ 2026")
    for heading in (
        "🧭 Главный ритм месяца",
        "🌕 Ключевые точки",
        "✅ Лучшие дни месяца",
        "⚠️ Осторожнее",
        "⚫️ VoC — важные окна",
    ):
        assert heading in text
    assert "🔭 Главный ритм месяца" not in text
    rhythm = text.split("🧭 Главный ритм месяца", 1)[1].split("🌕 Ключевые точки", 1)[0]
    rhythm_lines = [line for line in rhythm.splitlines() if line.strip()]
    assert 3 <= len(rhythm_lines) <= 5
    assert "🌕 Полнолуние:" in text
    assert "🌑 Новолуние:" in text
    assert "🌓 Рост Луны:" in text
    assert "🌘 Убывание Луны:" in text


def monthly_calendar_keeps_useful_best_day_categories() -> None:
    text = _monthly_text()
    assert "• Общие дела: 17–19, 22" in text
    assert "• Стрижка: 5, 12" in text
    assert "• Покупки: 1, 3–12, 14, 16, 28–31" in text
    assert "• Здоровье: 6, 14" in text
    assert "• Путешествия: 8, 24" in text


def monthly_calendar_adds_final_hashtags() -> None:
    text = _monthly_text()
    final_line = [line for line in text.splitlines() if line.strip()][-1]
    assert final_line == "#Калининград #лунный_календарь #астропогода #июль"
    assert "#лунный_календарь" in text
    assert "#астропогода" in text


def monthly_calendar_compresses_long_day_lists() -> None:
    text = _monthly_text()
    assert "1, 3–12, 14, 16, 28–31" in text
    assert "• Не для резких стартов: 3–12, 14, 16, 20, 28–31" in text
    assert "• Дни с двойным фоном: 17–19" in text
    assert "3, 4, 5, 6, 7, 8, 9, 10, 11, 12" not in text


def monthly_calendar_explains_favorable_unfavorable_overlap() -> None:
    text = _monthly_text()
    assert "• Не для резких стартов: 3–12, 14, 16, 20, 28–31" in text
    assert "• Дни с двойным фоном: 17–19" in text
    assert "лучше для завершения, анализа и мягких решений, не для резких стартов" in text
    assert not re.search(r"Не для резких стартов: [^\n]*(17|18|19)", text)
    assert "❌ <b>Неблагоприятные:</b>" not in text


def monthly_calendar_limits_visible_voc_windows() -> None:
    text = _monthly_text()
    voc = text.split("⚫️ VoC — важные окна", 1)[1]
    visible_items = [line for line in voc.splitlines() if re.match(r"^\d{2}\.\d{2}", line)]
    assert len(visible_items) <= 8
    assert "01.07 19:13–21:33" in text
    assert "03.07 16:15–04.07 08:30" in text
    assert "Ещё 2 коротких VoC-окон — используем как паузы, не как запрет." in text
    assert "⚫️ VoC — время “без курса”: лучше завершать, отдыхать, не запускать важное с нуля." in text


def monthly_calendar_output_is_html_parseable() -> None:
    HTMLParser().feed(_monthly_text())


def monthly_calendar_accepts_load_calendar_output_shape() -> None:
    days = _fixture_days()
    first_key = next(iter(days))
    days[first_key]["favorable_days"] = _fixture_cats()
    obj = {
        "days": days,
        "month_voc": [
            {"start": "01.07 19:13", "end": "01.07 21:33"},
            {"start": "03.07 16:15", "end": "04.07 08:30"},
        ],
    }
    days_map, month_voc, cats = load_calendar(obj)
    text = build_message(days_map, month_voc, cats)
    assert "🌙 Лунный календарь ИЮЛЯ 2026" in text
    assert "✅ Лучшие дни месяца" in text
    assert "⚫️ VoC — важные окна" in text


def main() -> None:
    checks = (
        monthly_calendar_has_new_readable_structure,
        monthly_calendar_keeps_useful_best_day_categories,
        monthly_calendar_adds_final_hashtags,
        monthly_calendar_compresses_long_day_lists,
        monthly_calendar_explains_favorable_unfavorable_overlap,
        monthly_calendar_limits_visible_voc_windows,
        monthly_calendar_output_is_html_parseable,
        monthly_calendar_accepts_load_calendar_output_shape,
    )
    for check in checks:
        check()
        print(f"PASS {check.__name__}")
    print(f"OK: {len(checks)} monthly lunar calendar checks passed")


if __name__ == "__main__":
    main()
