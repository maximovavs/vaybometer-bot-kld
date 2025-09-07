#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
post_kld.py  •  Запуск «Kaliningrad daily post» для Telegram-канала.

Режимы:
  1) Обычный ежедневный пост (по умолчанию) — вызывает post_common.main_common().
  2) --fx-only           — отправляет только блок «Курсы валют».
  3) --dry-run           — ничего не отправляет (полезно для теста workflow).
  4) --date YYYY-MM-DD   — дата для заголовков/FX (по умолчанию — сегодня в TZ).
  5) --for-tomorrow      — сдвиг даты +1 день (удобно для «поста на завтра»).

Переменные окружения:
  TELEGRAM_TOKEN_KLG, CHANNEL_ID_KLG — обязательно.
  DISABLE_LLM_DAILY — если "1"/"true" → ежедневный LLM отключён (чтение в post_common).
  TZ (опц.) — таймзона, по умолчанию Europe/Kaliningrad.
"""

from __future__ import annotations

import os
import sys
import argparse
import asyncio
import logging
from pathlib import Path
from typing import Dict, Any

import pendulum
from telegram import Bot

from post_common import main_common  # основной сборщик сообщения

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ────────────────────────────── Secrets / Env ────────────────────────────────

TOKEN_KLG      = os.getenv("TELEGRAM_TOKEN_KLG", "")
CHANNEL_ID_KLG = os.getenv("CHANNEL_ID_KLG", "")

if not TOKEN_KLG or not CHANNEL_ID_KLG:
    logging.error("Не заданы TELEGRAM_TOKEN_KLG и/или CHANNEL_ID_KLG")
    sys.exit(1)

try:
    CHAT_ID_KLG = int(CHANNEL_ID_KLG)
except ValueError:
    logging.error("CHANNEL_ID_KLG должен быть числом (например, -1001234567890)")
    sys.exit(1)

# ───────────────────────────── Параметры региона ────────────────────────────

SEA_LABEL   = "Морские города (топ-5)"
OTHER_LABEL = "Список не-морских городов (тёплые/холодные)"

# Часовой пояс — Калининград (можно переопределить переменной TZ)
TZ_STR = os.getenv("TZ", "Europe/Kaliningrad")

SEA_CITIES_ORDERED = [
    ("Балтийск",     (54.649, 20.055)),
    ("Янтарный",     (54.912, 19.887)),
    ("Зеленоградск", (54.959, 20.478)),
    ("Пионерский",   (54.930, 19.825)),
    ("Светлогорск",  (54.952, 20.160)),
]

OTHER_CITIES_ALL = [
    ("Гурьевск",        (54.658, 20.581)),
    ("Светлый",         (54.836, 19.767)),
    ("Советск",         (54.507, 21.347)),
    ("Черняховск",      (54.630, 21.811)),
    ("Гусев",           (54.590, 22.205)),
    ("Неман",           (55.030, 21.877)),
    ("Мамоново",        (54.657, 19.933)),
    ("Полесск",         (54.809, 21.010)),
    ("Багратионовск",   (54.368, 20.632)),
    ("Ладушкин",        (54.872, 19.706)),
    ("Правдинск",       (54.669, 21.330)),
    ("Славск",          (54.765, 21.644)),
    ("Озёрск",          (54.717, 20.282)),
    ("Нестеров",        (54.620, 21.647)),
    ("Краснознаменск",  (54.730, 21.104)),
    ("Гвардейск",       (54.655, 21.078)),
]

# ───────────────────────────── FX helpers ─────────────────────────────

FX_CACHE_PATH = Path("fx_cache.json")  # кэш в корне репозитория

def _fmt_delta(x: float | int | None) -> str:
    if x is None:
        return "0.00"
    try:
        x = float(x)
    except Exception:
        return "0.00"
    # знак минуса — узкий (–)
    sign = "−" if x < 0 else ""
    return f"{sign}{abs(x):.2f}"

def _fallback_build_fx_line(rates: Dict[str, Any]) -> str:
    """Резервный форматтер линии курсов на случай отсутствия fx.format_rates_line."""
    def token(code: str, name: str) -> str:
        r = rates.get(code) or {}
        val = r.get("value")
        dlt = r.get("delta")
        if val is None:
            return f"{name}: — ₽ (—)"
        try:
            val_s = f"{float(val):.2f}"
        except Exception:
            val_s = "—"
        return f"{name}: {val_s} ₽ ({_fmt_delta(dlt)})"
    return " • ".join([token("USD", "USD"), token("EUR", "EUR"), token("CNY", "CNY")])

def _load_fx_rates(date_local: pendulum.DateTime, tz) -> Dict[str, Any]:
    """
    Универсальная загрузка курсов:
      1) fx.get_rates(date=..., tz=...)
      2) fx.fetch_cbr_daily() + fx.parse_cbr_rates(...)
    Возвращает dict (должен включать ключ 'date' с датой курсов).
    """
    try:
        import importlib
        fx = importlib.import_module("fx")
        # при наличии — get_rates
        if hasattr(fx, "get_rates"):
            rates = fx.get_rates(date=date_local, tz=tz)  # type: ignore[attr-defined]
            return rates or {}
        # иначе — fetch + parse
        if hasattr(fx, "fetch_cbr_daily") and hasattr(fx, "parse_cbr_rates"):
            raw = fx.fetch_cbr_daily()                    # type: ignore[attr-defined]
            rates = fx.parse_cbr_rates(raw) or {}         # type: ignore[attr-defined]
            return rates
    except Exception as e:
        logging.warning("FX: модуль fx.py не найден/ошибка получения: %s", e)
    return {}

def _build_fx_message(date_local: pendulum.DateTime, tz) -> tuple[str, Dict[str, Any]]:
    """
    Возвращает (текст_сообщения, rates_dict).
    Сообщение содержит заголовок и одну строку с курсами.
    """
    rates = _load_fx_rates(date_local, tz) or {}
    # пробуем форматтер из fx.py
    fx_line = None
    try:
        import importlib
        fx = importlib.import_module("fx")
        if hasattr(fx, "format_rates_line"):
            fx_line = fx.format_rates_line(rates)  # type: ignore[attr-defined]
    except Exception:
        fx_line = None
    if not fx_line:
        fx_line = _fallback_build_fx_line(rates)

    title = "💱 <b>Курсы валют</b>"
    return f"{title}\n{fx_line}", rates

async def _send_fx_only(bot: Bot, chat_id: int, date_local: pendulum.DateTime, tz, dry_run: bool) -> None:
    """
    Отправка только блока «Курсы валют» с проверкой кэша:
      - если should_publish_again(...) вернул False → лог и выход;
      - если отправили — save_fx_cache(...).
    """
    text, rates = _build_fx_message(date_local, tz)

    # достаём дату курсов ЦБ из rates
    cbr_date = rates.get("date") or rates.get("cbr_date")
    if isinstance(cbr_date, pendulum.DateTime):
        cbr_date = cbr_date.to_date_string()
    elif hasattr(cbr_date, "to_date_string"):
        cbr_date = cbr_date.to_date_string()
    elif isinstance(cbr_date, (int, float)):
        # unix ts → дата
        try:
            cbr_date = pendulum.from_timestamp(int(cbr_date), tz="Europe/Moscow").to_date_string()
        except Exception:
            cbr_date = None
    else:
        cbr_date = str(cbr_date) if cbr_date else None

    # проверка кэша (если у нас есть дата)
    try:
        import importlib
        fx = importlib.import_module("fx")
        if cbr_date and hasattr(fx, "should_publish_again"):
            ok = fx.should_publish_again(FX_CACHE_PATH, cbr_date)  # type: ignore[attr-defined]
            if not ok:
                logging.info("Курсы ЦБ не обновились — пост пропущен")
                return
    except Exception as e:
        # если проверка не удалась — публикуем (лучше лишний пост, чем пропуск)
        logging.warning("FX cache check failed: %s (продолжаем публикацию)", e)

    if dry_run:
        logging.info("DRY-RUN (fx-only):\n%s", text)
        return

    # отправка
    await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML", disable_web_page_preview=True)

    # сохранение кэша после успешной отправки
    try:
        import importlib
        fx = importlib.import_module("fx")
        if cbr_date and hasattr(fx, "save_fx_cache"):
            fx.save_fx_cache(FX_CACHE_PATH, cbr_date, text)  # type: ignore[attr-defined]
    except Exception as e:
        logging.warning("FX cache save failed: %s", e)

# ───────────────────────────────── Main ─────────────────────────────────────

async def main_kld() -> None:
    parser = argparse.ArgumentParser(description="Kaliningrad daily post runner")
    parser.add_argument("--date", type=str, default="", help="Дата в формате YYYY-MM-DD (по умолчанию — сегодня в TZ)")
    parser.add_argument("--for-tomorrow", action="store_true", help="Использовать дату +1 день")
    parser.add_argument("--dry-run", action="store_true", help="Не отправлять сообщение, только лог")
    parser.add_argument("--fx-only", action="store_true", help="Отправить только блок «Курсы валют»")
    args = parser.parse_args()

    tz = pendulum.timezone(TZ_STR)
    base_date = pendulum.parse(args.date).in_tz(tz) if args.date else pendulum.now(tz)
    if args.for_tomorrow:
        base_date = base_date.add(days=1)

    bot = Bot(token=TOKEN_KLG)

    if args.fx_only:
        await _send_fx_only(bot, CHAT_ID_KLG, base_date, tz, dry_run=args.dry_run)
        return

    if args.dry_run:
        logging.info("DRY-RUN: пропускаем отправку основного ежедневного поста")
        return

    # Обычный ежедневный пост
    await main_common(
        bot=bot,
        chat_id=CHAT_ID_KLG,
        region_name="Калининградская область",
        sea_label=SEA_LABEL,
        sea_cities=SEA_CITIES_ORDERED,
        other_label=OTHER_LABEL,
        other_cities=OTHER_CITIES_ALL,
        tz=TZ_STR,  # post_common сам приведёт к pendulum.timezone
    )

if __name__ == "__main__":
    asyncio.run(main_kld())