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
  6) --to-test           — публиковать в тестовый канал (CHANNEL_ID_TEST).
  7) --chat-id ID        — явный chat_id канала (перебивает всё остальное).

Переменные окружения:
  TELEGRAM_TOKEN_KLG — обязательно.
  CHANNEL_ID_KLG     — ID основного канала (если не задан --chat-id/--to-test).
  CHANNEL_ID_TEST    — ID тестового канала (для --to-test).
  CHANNEL_ID_OVERRIDE — явный chat_id (перебивает всё; удобно в Actions inputs).
  DISABLE_LLM_DAILY  — если "1"/"true" → ежедневный LLM отключён (читает post_common).
  TZ (опц.)          — таймзона, по умолчанию Europe/Kaliningrad.
"""

from __future__ import annotations

import os
import sys
import argparse
import asyncio
import logging
from typing import Dict, Any, Tuple
from pathlib import Path

import pendulum
from telegram import Bot

from post_common import main_common  # основной сборщик сообщения

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ────────────────────────────── Secrets / Env ────────────────────────────────

TOKEN_KLG = os.getenv("TELEGRAM_TOKEN_KLG", "")
if not TOKEN_KLG:
    logging.error("Не задан TELEGRAM_TOKEN_KLG")
    sys.exit(1)

# ───────────────────────────── Параметры региона ────────────────────────────

SEA_LABEL   = "Морские города"
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

# ───────────────────────────── FX helpers ────────────────────────────────────

FX_CACHE_PATH = Path("fx_cache.json")  # где хранить кэш для FX-постов

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

def _load_fx_rates(date_local: pendulum.DateTime, tz: pendulum.Timezone) -> Dict[str, Any]:
    """
    Пытаемся получить курсы валют через модуль fx.py (если он в проекте).
    Ожидаемый интерфейс: fx.get_rates(date=date_local, tz=tz) -> dict.
    Возвращаем {} при любой ошибке.
    """
    try:
        import importlib
        fx = importlib.import_module("fx")
        rates = fx.get_rates(date=date_local, tz=tz)  # type: ignore[attr-defined]
        return rates or {}
    except Exception as e:
        logging.warning("FX: модуль fx.py не найден/ошибка получения данных: %s", e)
        return {}

def _build_fx_message(date_local: pendulum.DateTime, tz: pendulum.Timezone) -> Tuple[str, Dict[str, Any]]:
    """Возвращает (текст_поста, словарь_rates) для блока «Курсы валют»."""
    rates = _load_fx_rates(date_local, tz)

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

    line = " • ".join([token("USD", "USD"), token("EUR", "EUR"), token("CNY", "CNY")])
    title = "💱 <b>Курсы валют</b>"
    return f"{title}\n{line}", rates

def _normalize_cbr_date(raw) -> str | None:
    """
    Приводим дату ЦБ к строке 'YYYY-MM-DD' в TZ Москвы.
    Поддерживаем варианты: pendulum Date/DateTime, unix timestamp, ISO-строка и т.п.
    """
    if raw is None:
        return None
    # pendulum Date/DateTime
    if hasattr(raw, "to_date_string"):
        try:
            return raw.to_date_string()
        except Exception:
            pass
    # unix timestamp
    if isinstance(raw, (int, float)):
        try:
            return pendulum.from_timestamp(int(raw), tz="Europe/Moscow").to_date_string()
        except Exception:
            return None
    # строка
    try:
        s = str(raw).strip()
        if "T" in s or " " in s:
            return pendulum.parse(s, tz="Europe/Moscow").to_date_string()
        # возможно уже YYYY-MM-DD
        pendulum.parse(s, tz="Europe/Moscow")
        return s
    except Exception:
        return None

async def _send_fx_only(
    bot: Bot,
    chat_id: int,
    date_local: pendulum.DateTime,
    tz: pendulum.Timezone,
    dry_run: bool
) -> None:
    # формируем текст и получаем полные rates
    text, rates = _build_fx_message(date_local, tz)

    # достаём дату ЦБ (учитываем разные ключи)
    raw_date = rates.get("as_of") or rates.get("date") or rates.get("cbr_date")
    cbr_date = _normalize_cbr_date(raw_date)

    # если есть should_publish_again — проверим кэш и, при необходимости, пропустим публикацию
    try:
        import importlib
        fx = importlib.import_module("fx")
        if cbr_date and hasattr(fx, "should_publish_again"):  # type: ignore[attr-defined]
            should = fx.should_publish_again(FX_CACHE_PATH, cbr_date)  # type: ignore[attr-defined]
            if not should:
                logging.info("Курсы ЦБ не обновились — пост пропущен.")
                return
    except Exception as e:
        # не считаем ошибкой — просто публикуем
        logging.warning("FX: skip-check failed (продолжаем отправку): %s", e)

    if dry_run:
        logging.info("DRY-RUN (fx-only):\n" + text)
        return

    await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML", disable_web_page_preview=True)

    # после успешной отправки обновим кэш
    try:
        import importlib
        fx = importlib.import_module("fx")
        if cbr_date and hasattr(fx, "save_fx_cache"):  # type: ignore[attr-defined]
            fx.save_fx_cache(FX_CACHE_PATH, cbr_date, text)  # type: ignore[attr-defined]
    except Exception as e:
        logging.warning("FX: save cache failed: %s", e)

# ───────────────────────────── Chat selection ────────────────────────────────

def resolve_chat_id(args_chat: str, to_test: bool) -> int:
    """
    Выбирает chat_id по приоритетам:
      1) --chat-id / CHANNEL_ID_OVERRIDE
      2) --to-test  → CHANNEL_ID_TEST
      3) CHANNEL_ID_KLG
    """
    # 1) явный аргумент или ENV override
    chat_override = (args_chat or "").strip() or os.getenv("CHANNEL_ID_OVERRIDE", "").strip()
    if chat_override:
        try:
            return int(chat_override)
        except Exception:
            logging.error("Неверный chat_id (override): %r", chat_override)
            sys.exit(1)

    # 2) тестовый канал
    if to_test:
        ch_test = os.getenv("CHANNEL_ID_TEST", "").strip()
        if not ch_test:
            logging.error("--to-test задан, но CHANNEL_ID_TEST не определён в окружении")
            sys.exit(1)
        try:
            return int(ch_test)
        except Exception:
            logging.error("CHANNEL_ID_TEST должен быть числом, получено: %r", ch_test)
            sys.exit(1)

    # 3) основной канал
    ch_main = os.getenv("CHANNEL_ID_KLG", "").strip()
    if not ch_main:
        logging.error("CHANNEL_ID_KLG не задан и не указан --chat-id/override")
        sys.exit(1)
    try:
        return int(ch_main)
    except Exception:
        logging.error("CHANNEL_ID_KLG должен быть числом, получено: %r", ch_main)
        sys.exit(1)

# ─────────────────────────── Патч даты для всего поста ──────────────────────

class _TodayPatch:
    """Контекстный менеджер для временной подмены `pendulum.today()` и `pendulum.now()`."""

    def __init__(self, base_date: pendulum.DateTime):
        self.base_date = base_date
        self._orig_today = None
        self._orig_now = None

    def __enter__(self):
        self._orig_today = pendulum.today
        self._orig_now = pendulum.now

        def _fake(dt: pendulum.DateTime, tz_arg=None):
            return dt.in_tz(tz_arg) if tz_arg else dt

        pendulum.today = lambda tz_arg=None: _fake(self.base_date, tz_arg)  # type: ignore[assignment]
        pendulum.now = lambda tz_arg=None: _fake(self.base_date, tz_arg)    # type: ignore[assignment]

        logging.info(
            "Дата для поста зафиксирована как %s (TZ %s)",
            self.base_date.to_datetime_string(),
            self.base_date.timezone_name,
        )
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._orig_today:
            pendulum.today = self._orig_today  # type: ignore[assignment]
        if self._orig_now:
            pendulum.now = self._orig_now  # type: ignore[assignment]
        # не подавляем исключения
        return False

# ───────────────────────────────── Main ─────────────────────────────────────

async def main_kld() -> None:
    parser = argparse.ArgumentParser(description="Kaliningrad daily post runner")
    parser.add_argument("--date", type=str, default="", help="Дата в формате YYYY-MM-DD (по умолчанию — сегодня в TZ)")
    parser.add_argument("--for-tomorrow", action="store_true", help="Использовать дату +1 день")
    parser.add_argument("--dry-run", action="store_true", help="Не отправлять сообщение, только лог")
    parser.add_argument("--fx-only", action="store_true", help="Отправить только блок «Курсы валют»")
    parser.add_argument("--to-test", action="store_true", help="Публиковать в тестовый канал (CHANNEL_ID_TEST)")
    parser.add_argument("--chat-id", type=str, default="", help="Явный chat_id канала (перебивает все остальные)")
    args = parser.parse_args()

    tz = pendulum.timezone(TZ_STR)
    base_date = pendulum.parse(args.date).in_tz(tz) if args.date else pendulum.now(tz)
    if args.for_tomorrow:
        base_date = base_date.add(days=1)

    chat_id = resolve_chat_id(args.chat_id, args.to_test)
    bot = Bot(token=TOKEN_KLG)

    # Подменяем pendulum.today, чтобы весь импортируемый код видел нужную дату
    with _TodayPatch(base_date):
        if args.fx_only:
            await _send_fx_only(bot, chat_id, base_date, tz, dry_run=args.dry_run)
            return

        if args.dry_run:
            logging.info("DRY-RUN: пропускаем отправку основного ежедневного поста")
            return

        # Обычный ежедневный пост
        await main_common(
            bot=bot,
            chat_id=chat_id,
            region_name="Калининградская область",
            sea_label=SEA_LABEL,
            sea_cities=SEA_CITIES_ORDERED,
            other_label=OTHER_LABEL,
            other_cities=OTHER_CITIES_ALL,
            tz=TZ_STR,  # post_common сам приведёт к pendulum.timezone
        )

if __name__ == "__main__":
    asyncio.run(main_kld())
