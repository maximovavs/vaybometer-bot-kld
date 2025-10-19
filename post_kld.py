#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
post_kld.py  •  Запуск «Kaliningrad daily post» для Telegram-канала.

Режимы:
  1) --mode evening      — вечерний пост (анонс «на завтра») — по умолчанию.
  2) --mode morning      — утренний пост («на сегодня»), структура как в Кипре.
  3) --fx-only           — отправляет только блок «Курсы валют».
  4) --dry-run           — ничего не отправляет (лог вместо публикации).
  5) --date YYYY-MM-DD   — базовая дата (если не указана, берём WORK_DATE или сегодня в TZ).
  6) --for-tomorrow      — сдвиг базовой даты +1 день (удобно для ручного запуска).
  7) --to-test           — публиковать в тестовый канал (CHANNEL_ID_TEST).
  8) --chat-id ID        — явный chat_id канала (перебивает всё остальное).

Переменные окружения:
  TELEGRAM_TOKEN_KLG  — обязательно.
  CHANNEL_ID_KLG      — ID основного канала (если не задан --chat-id/--to-test).
  CHANNEL_ID_TEST     — ID тестового канала (для --to-test).
  CHANNEL_ID_OVERRIDE — явный chat_id (перебивает всё; удобно в Actions inputs).
  TZ                  — таймзона, по умолчанию Europe/Kaliningrad.
  WORK_DATE           — альтернативный способ задать базовую дату (YYYY-MM-DD).
  MODE                — дефолт для --mode (morning/evening). CLI приоритетнее.

Примечание:
  Флаги показа блоков теперь задаются здесь жёстко (SHOW_AIR/SHOW_SPACE/SHOW_SCHUMANN),
  чтобы утренний и вечерний посты гарантированно отличались по структуре.
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
    # знак минуса — узкий (−)
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
        pendulum.parse(s, tz="Europe/Moscow")  # валидация
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
    text, rates = _build_fx_message(date_local, tz)

    raw_date = rates.get("as_of") or rates.get("date") or rates.get("cbr_date")
    cbr_date = _normalize_cbr_date(raw_date)

    try:
        import importlib
        fx = importlib.import_module("fx")
        if cbr_date and hasattr(fx, "should_publish_again"):  # type: ignore[attr-defined]
            should = fx.should_publish_again(FX_CACHE_PATH, cbr_date)  # type: ignore[attr-defined]
            if not should:
                logging.info("Курсы ЦБ не обновились — пост пропущен.")
                return
    except Exception as e:
        logging.warning("FX: skip-check failed (продолжаем отправку): %s", e)

    if dry_run:
        logging.info("DRY-RUN (fx-only):\n" + text)
        return

    await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML", disable_web_page_preview=True)

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
    chat_override = (args_chat or "").strip() or os.getenv("CHANNEL_ID_OVERRIDE", "").strip()
    if chat_override:
        try:
            return int(chat_override)
        except Exception:
            logging.error("Неверный chat_id (override): %r", chat_override)
            sys.exit(1)

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
        return False  # не подавляем исключения

# ───────────────────────────────── Main ─────────────────────────────────────

async def main_kld() -> None:
    parser = argparse.ArgumentParser(description="Kaliningrad daily post runner")
    parser.add_argument("--mode",
                        choices=["morning", "evening"],
                        default=(os.getenv("MODE") or "evening"),
                        help="Режим поста: morning — на сегодня, evening — анонс на завтра (по умолчанию).")
    parser.add_argument("--date", type=str, default="", help="Дата в формате YYYY-MM-DD (по умолчанию — WORK_DATE или сегодня в TZ)")
    parser.add_argument("--for-tomorrow", action="store_true", help="Использовать дату +1 день")
    parser.add_argument("--dry-run", action="store_true", help="Не отправлять сообщение, только лог")
    parser.add_argument("--fx-only", action="store_true", help="Отправить только блок «Курсы валют»")
    parser.add_argument("--to-test", action="store_true", help="Публиковать в тестовый канал (CHANNEL_ID_TEST)")
    parser.add_argument("--chat-id", type=str, default="", help="Явный chat_id канала (перебивает все остальные)")
    args = parser.parse_args()

    tz = pendulum.timezone(TZ_STR)

    # Базовая дата: CLI > WORK_DATE > now(tz)
    raw_date = args.date.strip() or os.getenv("WORK_DATE", "").strip()
    base_date = pendulum.parse(raw_date).in_tz(tz) if raw_date else pendulum.now(tz)
    if args.for_tomorrow:
        base_date = base_date.add(days=1)

    # Выбранный режим
    mode = (args.mode or "evening").lower().strip()
    logging.info("Режим поста: %s", mode)

    # ── Прокинем флаги в post_common (жёстко для каждого режима)
    # DAY_OFFSET/ASTRO_OFFSET: 0 для morning, 1 для evening
    day_offset = 0 if mode == "morning" else 1
    os.environ["POST_MODE"] = mode
    os.environ["DAY_OFFSET"] = str(day_offset)
    os.environ["ASTRO_OFFSET"] = str(day_offset)

    # Показ блоков
    if mode == "morning":
        os.environ["SHOW_AIR"] = "1"
        os.environ["SHOW_SPACE"] = "1"
        # учитываем глобальный DISABLE_SCHUMANN (если был "1", то ниже блок всё равно не покажется)
        os.environ["SHOW_SCHUMANN"] = "1"
    else:  # evening
        os.environ["SHOW_AIR"] = "0"
        os.environ["SHOW_SPACE"] = "0"
        os.environ["SHOW_SCHUMANN"] = "0"

    logging.info("Флаги выводa: DAY_OFFSET=%s, ASTRO_OFFSET=%s, AIR=%s, SPACE=%s, SCHUMANN=%s",
                 os.environ.get("DAY_OFFSET"), os.environ.get("ASTRO_OFFSET"),
                 os.environ.get("SHOW_AIR"), os.environ.get("SHOW_SPACE"), os.environ.get("SHOW_SCHUMANN"))

    chat_id = resolve_chat_id(args.chat_id, args.to_test)
    bot = Bot(token=TOKEN_KLG)

    # Подменяем pendulum.today/now, чтобы весь импортируемый код видел нужную дату
    with _TodayPatch(base_date):
        if args.fx_only:
            await _send_fx_only(bot, chat_id, base_date, tz, dry_run=args.dry_run)
            return

        if args.dry_run:
            logging.info("DRY-RUN: пропускаем отправку основного ежедневного поста")
            return

        # Обычный ежедневный пост (формат/контент различается внутри post_common по ENV выше)
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