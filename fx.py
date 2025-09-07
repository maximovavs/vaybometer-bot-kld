#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fx.py — курсы валют для ежедневных постов.

Источник: ЦБ РФ (https://www.cbr-xml-daily.ru/daily_json.js)
Обновляется обычно около 11:30 мск.

Публичный интерфейс:
- fetch_cbr_daily() -> dict                         # сырой JSON ЦБ
- parse_cbr_rates(data) -> dict                     # нормализованные курсы
- format_rates_line(rates) -> str                   # строка для поста
- should_publish_again(cache_path, cbr_date) -> bool# постить ли снова в 12:00
- get_rates(date, tz) -> dict                       # удобный словарь для post_kld.py
- save_fx_cache(cache_path, cbr_date, text) -> None # записать факт публикации

Кэш кладём в fx_cache.json. Если существует папка "data", используем "data/fx_cache.json".
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Any, Optional

import json
import requests
import pendulum

CBR_URL = "https://www.cbr-xml-daily.ru/daily_json.js"

# Где хранить кэш
FX_CACHE_PATH = Path("fx_cache.json")
if Path("data").is_dir():
    FX_CACHE_PATH = Path("data") / "fx_cache.json"


# ─────────────────────────── сетевые функции ────────────────────────────────
def fetch_cbr_daily(timeout: float = 10.0) -> Dict[str, Any]:
    """Тянет JSON с дневными курсами ЦБ. Возвращает {} при ошибке."""
    try:
        r = requests.get(CBR_URL, timeout=timeout, headers={"User-Agent": "VayboMeter/1.0"})
        r.raise_for_status()
        return r.json()
    except Exception:
        return {}


# ─────────────────────────── парсинг & формат ───────────────────────────────
def _get_safe_val(d: Dict[str, Any], key: str, default: Optional[float] = None) -> Optional[float]:
    try:
        v = d.get(key)
        return float(v) if v is not None else default
    except Exception:
        return default


def parse_cbr_rates(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Превращает JSON ЦБ в компактный словарь:
    {
      "date": "YYYY-MM-DD",
      "USD": {"value": 94.12, "prev": 94.47, "delta": -0.35},
      "EUR": {...},
      "CNY": {...}
    }
    """
    if not data:
        return {}

    date_iso = data.get("Date") or ""
    try:
        date_utc = pendulum.parse(date_iso)  # в ответе есть таймзона
        date_out = date_utc.in_tz("Europe/Moscow").format("YYYY-MM-DD")
    except Exception:
        # на всякий случай уходим в «сегодня МСК»
        date_out = pendulum.now("Europe/Moscow").format("YYYY-MM-DD")

    out: Dict[str, Any] = {"date": date_out}
    valute = data.get("Valute", {})

    for code in ("USD", "EUR", "CNY"):
        row = valute.get(code) or {}
        value = _get_safe_val(row, "Value")
        prev  = _get_safe_val(row, "Previous")
        delta = (value - prev) if (value is not None and prev is not None) else None
        out[code] = {"value": value, "prev": prev, "delta": delta}

    return out


def _fmt_delta(x: Optional[float]) -> str:
    if x is None:
        return "0.00"
    sign = "−" if x < 0 else ""
    return f"{sign}{abs(x):.2f}"


def format_rates_line(rates: Dict[str, Any]) -> str:
    """
    Делает компактную строку вида:
    USD: 94.12 ₽ (−0.35) • EUR: 101.43 ₽ (−0.27) • CNY: 12.90 ₽ (0.00)
    """
    def item(code: str) -> str:
        r = rates.get(code) or {}
        v = r.get("value")
        try:
            vs = f"{float(v):.2f}"
        except Exception:
            vs = "—"
        return f"{code}: {vs} ₽ ({_fmt_delta(r.get('delta'))})"

    return " • ".join([item("USD"), item("EUR"), item("CNY")])


# ───────────────────────────── публикационный кэш ───────────────────────────
def _read_cache(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text("utf-8"))
    except Exception:
        return {}


def should_publish_again(cache_path: Path = FX_CACHE_PATH, cbr_date: str = "") -> bool:
    """
    Возвращает True, если ЦБ обновил дату (значит, в 12:00 можно публиковать).
    Если дата в кэше совпадает с cbr_date — False (не дублируем пост).
    """
    if not cbr_date:
        return True
    cached = _read_cache(cache_path)
    last = cached.get("last_cbr_date", "")
    return last != cbr_date


def save_fx_cache(cache_path: Path = FX_CACHE_PATH, cbr_date: str = "", text: str = "") -> None:
    """Сохраняет дату последней публикации и текст (для отладки)."""
    payload = {"last_cbr_date": cbr_date, "last_text": text, "saved_at": pendulum.now("UTC").to_iso8601_string()}
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
    except Exception:
        pass


# ───────────────────────── интерфейс для post_kld.py ────────────────────────
def get_rates(date: pendulum.DateTime, tz: pendulum.timezone) -> Dict[str, Any]:
    """
    Унифицированный интерфейс для поста:
    возвращает {"USD": {"value":..,"delta":..}, "EUR": {...}, "CNY": {...}, "as_of": "YYYY-MM-DD"}.
    Если сеть недоступна — {}.
    """
    raw = fetch_cbr_daily()
    if not raw:
        return {}

    parsed = parse_cbr_rates(raw)
    rates = {
        "USD": {"value": parsed.get("USD", {}).get("value"), "delta": parsed.get("USD", {}).get("delta")},
        "EUR": {"value": parsed.get("EUR", {}).get("value"), "delta": parsed.get("EUR", {}).get("delta")},
        "CNY": {"value": parsed.get("CNY", {}).get("value"), "delta": parsed.get("CNY", {}).get("delta")},
        "as_of": parsed.get("date"),
    }
    return rates


# ───────────────────────────── CLI тест (опц.) ──────────────────────────────
if __name__ == "__main__":
    # Быстрый тест: просто выведем строку для поста и обновим кэш
    raw = fetch_cbr_daily()
    parsed = parse_cbr_rates(raw)
    line = format_rates_line(parsed)
    print(f"Дата ЦБ: {parsed.get('date')}\n{line}")
    if should_publish_again(FX_CACHE_PATH, parsed.get("date", "")):
        save_fx_cache(FX_CACHE_PATH, parsed.get("date", ""), line)
        print("Кэш обновлён.")
    else:
        print("Дата уже публиковалась — пропускаем.")