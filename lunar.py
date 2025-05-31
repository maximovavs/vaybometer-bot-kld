#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
lunar.py  • функция get_day_lunar_info для поста и для генерации месячного календаря.
Ожидает файл lunar_calendar.json в корне репозитория, 
куда ежемесячно записывается подробный календарь.
"""

from __future__ import annotations
import json
from pathlib import Path
import pendulum
from typing import Any, Dict, List, Optional

def get_day_lunar_info(d: pendulum.Date) -> Optional[Dict[str, Any]]:
    """
    Возвращает информацию по дате d из lunar_calendar.json в формате:
      {
        "phase":            str,                   # название фазы + знак + "(XX% освещ.)"
        "percent":          int,                   # процент освещённости
        "sign":             str,                   # знак зодиака
        "aspects":          List[str],             # аспекты Луны к планетам
        "void_of_course":   Dict[str,str],         # период void-of-course
        "next_event":       str,                   # "→ через N дней …"
        "advice":           List[str],             # список практических советов
        "favorable_days":   Dict[str,List[int]],   # по категориям
        "unfavorable_days": Dict[str,List[int]],   # по категориям
      }
    или None, если файла нет или для даты нет записи.
    """
    fn = Path(__file__).parent / "lunar_calendar.json"
    if not fn.exists():
        return None

    try:
        data = json.loads(fn.read_text(encoding="utf-8"))
    except Exception:
        return None

    rec = data.get(d.format("YYYY-MM-DD"))
    if not rec:
        return None

    return {
        "phase":            rec.get("phase", ""),
        "percent":          rec.get("percent", 0),
        "sign":             rec.get("sign", ""),
        "aspects":          rec.get("aspects", []),
        "void_of_course":   rec.get("void_of_course", {}),
        "next_event":       rec.get("next_event", ""),
        "advice":           rec.get("advice", []),
        "favorable_days":   rec.get("favorable_days", {}),
        "unfavorable_days": rec.get("unfavorable_days", {}),
    }

# Тестовый запуск
if __name__ == "__main__":
    today = pendulum.now().date()
    from pprint import pprint
    pprint(get_day_lunar_info(today))
