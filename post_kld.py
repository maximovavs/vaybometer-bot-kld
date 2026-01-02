#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
post_kld.py  •  Запуск «Kaliningrad daily post» для Telegram-канала.

Режимы:
  1) --mode evening      — вечерний пост (анонс «на завтра») — по умолчанию.
  2) --mode morning      — утренний пост («на сегодня»), структура как в Кипре.
  3) --fx-only           — отправляет только блок «Курсы валют».
  4) --dry-run           — ничего не отправляет (лог вместо публикации).
  5) --echo              — печатает готовый пост в stdout (без отправки).
  6) --date YYYY-MM-DD   — базовая дата (если не указана, берём WORK_DATE или сегодня в TZ).
  7) --for-tomorrow      — сдвиг базовой даты +1 день.
  8) --to-test           — публиковать в тестовый канал (CHANNEL_ID_TEST).
  9) --chat-id ID        — явный chat_id канала (перебивает всё остальное).

ENV:
  TELEGRAM_TOKEN_KLG, CHANNEL_ID_KLG, CHANNEL_ID_TEST, CHANNEL_ID_OVERRIDE,
  TZ (default Europe/Kaliningrad), WORK_DATE, MODE.

Примечание:
  Флаги показа блоков задаются через ENV непосредственно здесь (чтобы режимы отличались).
"""

from __future__ import annotations

import os
import sys
import re
import argparse
import asyncio
import logging
from typing import Dict, Any, Tuple, Union, Optional
from pathlib import Path

import pendulum
from telegram import Bot, constants

# Берём сборку/рендер и конструктор текста
from post_common import build_message, fx_morning_line  # type: ignore
# (оставляем импорт main_common на случай обратной совместимости)
# from post_common import main_common  # noqa: F401

import imagegen  # локальный генератор картинки (Pollinations download → файл)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ────────────────────────────── Secrets / Env ────────────────────────────────

TOKEN_KLG = os.getenv("TELEGRAM_TOKEN_KLG", "")
if not TOKEN_KLG:
    logging.error("Не задан TELEGRAM_TOKEN_KLG")
    sys.exit(1)

# ───────────────────────────── Параметры региона ────────────────────────────

SEA_LABEL   = "Морские города"
OTHER_LABEL = "Список не-морских городов (тёплые/холодные)"
TZ_STR      = os.getenv("TZ", "Europe/Kaliningrad")

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

# ───────────────────────────── FX helpers (как было) ─────────────────────────

FX_CACHE_PATH = Path("fx_cache.json")


def _fmt_delta(x: float | int | None) -> str:
    if x is None:
        return "0.00"
    try:
        x = float(x)
    except Exception:
        return "0.00"
    sign = "−" if x < 0 else ""
    return f"{sign}{abs(x):.2f}"


def _load_fx_rates(date_local: pendulum.DateTime, tz: pendulum.Timezone) -> Dict[str, Any]:
    try:
        import importlib
        fx = importlib.import_module("fx")
        rates = fx.get_rates(date=date_local, tz=tz)  # type: ignore[attr-defined]
        return rates or {}
    except Exception as e:
        logging.warning("FX: модуль fx.py не найден/ошибка получения данных: %s", e)
        return {}


def _build_fx_message(date_local: pendulum.DateTime, tz: pendulum.Timezone) -> Tuple[str, Dict[str, Any]]:
    rates = _load_fx_rates(date_local, tz)

    def token(code: str, name: str) -> str:
        r = rates.get(code) or {}
        val = r.get("value"); dlt = r.get("delta")
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


def _normalize_cbr_date(raw) -> Optional[str]:
    if raw is None:
        return None
    if hasattr(raw, "to_date_string"):
        try:
            return raw.to_date_string()
        except Exception:
            pass
    if isinstance(raw, (int, float)):
        try:
            return pendulum.from_timestamp(int(raw), tz="Europe/Moscow").to_date_string()
        except Exception:
            return None
    try:
        s = str(raw).strip()
        if "T" in s or " " in s:
            return pendulum.parse(s, tz="Europe/Moscow").to_date_string()
        pendulum.parse(s, tz="Europe/Moscow")
        return s
    except Exception:
        return None


async def _send_fx_only(
    bot: Bot,
    chat_id: Union[int, str],
    date_local: pendulum.DateTime,
    tz: pendulum.Timezone,
    dry_run: bool,
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
        logging.info("DRY-RUN (fx-only):\n%s", text)
        return

    try:
        m = await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=constants.ParseMode.HTML,
            disable_web_page_preview=True,
        )
        logging.info(
            "FX sent: chat=%s message_id=%s",
            getattr(m.chat, "id", "?"),
            getattr(m, "message_id", "?"),
        )
    except Exception:
        logging.exception("FX send failed")
        raise

    try:
        import importlib
        fx = importlib.import_module("fx")
        if cbr_date and hasattr(fx, "save_fx_cache"):  # type: ignore[attr-defined]
            fx.save_fx_cache(FX_CACHE_PATH, cbr_date, text)  # type: ignore[attr-defined]
    except Exception as e:
        logging.warning("FX: save cache failed: %s", e)

# ───────────────────────────── Chat selection ────────────────────────────────


def resolve_chat_id(args_chat: str, to_test: bool) -> Union[int, str]:
    """
    Возвращает chat_id как int ИЛИ строку '@channelusername'.
    """
    chat_override = (args_chat or "").strip() or os.getenv("CHANNEL_ID_OVERRIDE", "").strip()
    if chat_override:
        try:
            return int(chat_override)
        except Exception:
            logging.warning(
                "CHAT_ID override не число — используем как строку: %r",
                chat_override,
            )
            return chat_override

    if to_test:
        ch_test = os.getenv("CHANNEL_ID_TEST", "").strip()
        if not ch_test:
            logging.error("--to-test задан, но CHANNEL_ID_TEST не определён в окружении")
            sys.exit(1)
        try:
            return int(ch_test)
        except Exception:
            logging.info(
                "CHANNEL_ID_TEST не число — используем как строку: %r",
                ch_test,
            )
            return ch_test

    ch_main = os.getenv("CHANNEL_ID_KLG", "").strip()
    if not ch_main:
        logging.error("CHANNEL_ID_KLG не задан и не указан --chat-id/override")
        sys.exit(1)
    try:
        return int(ch_main)
    except Exception:
        logging.info(
            "CHANNEL_ID_KLG не число — используем как строку: %r",
            ch_main,
        )
        return ch_main


# ─────────────────────────── Картинка для вечернего поста ────────────────────


async def _maybe_send_kld_image(
    bot: Bot,
    chat_id: Union[int, str],
    base_date: "pendulum.DateTime",
    mode: str,
    dry_run: bool,
) -> None:
    """
    Пытается сгенерировать и отправить атмосферную картинку для вечернего поста.

    Теперь:
      • используем build_kld_evening_prompt из image_prompt_kld.py;
      • генерируем картинку локальным файлом через imagegen.py (Pollinations, без API-ключа);
      • отправляем в Telegram как файл (не URL), чтобы превью работало стабильнее.
    """
    # Картинка есть только для вечернего анонса
    if mode != "evening":
        return

    try:
        from image_prompt_kld import build_kld_evening_prompt  # type: ignore
    except Exception as e:
        logging.info(
            "KLD image: build_kld_evening_prompt недоступен — картинка пропущена (%s)",
            e,
        )
        return

    try:
        # Пока mood'ы не прокидываем — используем дефолтные описания внутри image_prompt_kld
        prompt, style_name = build_kld_evening_prompt(
            date=base_date.date(),
            marine_mood="",
            inland_mood="",
            astro_mood_en="",
        )

        # Путь для файла
        out_dir = Path(".cache") / "kld_images"
        out_dir.mkdir(parents=True, exist_ok=True)

        safe_style = re.sub(r"[^a-zA-Z0-9_-]+", "_", (style_name or "default")).strip("_") or "default"
        out_path = out_dir / f"kld_{base_date.to_date_string()}_{safe_style}.jpg"

        # Генерируем (скачиваем) картинку локально
        local_path = imagegen.generate_kld_evening_image(
            prompt=prompt,
            style_name=style_name,
            out_path=str(out_path),
        )

        if dry_run:
            logging.info("KLD image: DRY-RUN — картинка сгенерирована, отправка пропущена: %s", local_path)
            return

        caption = "Визуальный вайб завтрашнего вечера над Балтикой 🌊🌕"
        with open(local_path, "rb") as f:
            msg = await bot.send_photo(chat_id=chat_id, photo=f, caption=caption)

        logging.info(
            "KLD image: photo sent: chat=%s message_id=%s",
            getattr(msg.chat, "id", "?"),
            getattr(msg, "message_id", "?"),
        )
    except Exception:
        logging.exception("KLD image: ошибка при генерации/отправке картинки")
        # Не валим весь пост из-за картинки


# ─────────────────────────── Патч даты для всего поста ──────────────────────


class _TodayPatch:
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
        pendulum.now = lambda tz_arg=None: _fake(self.base_date, tz_arg)  # type: ignore[assignment]

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
        return False


# ───────────────────────────────── Main ─────────────────────────────────────


async def main_kld() -> None:
    parser = argparse.ArgumentParser(description="Kaliningrad daily post runner")
    parser.add_argument(
        "--mode",
        choices=["morning", "evening"],
        default=(os.getenv("MODE") or "evening"),
        help="morning — на сегодня, evening — анонс на завтра (по умолчанию).",
    )
    parser.add_argument(
        "--date",
        type=str,
        default="",
        help="YYYY-MM-DD (по умолчанию — WORK_DATE или сегодня в TZ)",
    )
    parser.add_argument(
        "--for-tomorrow",
        action="store_true",
        help="Использовать дату +1 день",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Не отправлять сообщение, только лог",
    )
    parser.add_argument(
        "--echo",
        action="store_true",
        help="Вывести готовый текст поста и выйти (без отправки)",
    )
    parser.add_argument(
        "--fx-only",
        action="store_true",
        help="Отправить только блок «Курсы валют»",
    )
    parser.add_argument(
        "--to-test",
        action="store_true",
        help="Публиковать в тестовый канал (CHANNEL_ID_TEST)",
    )
    parser.add_argument(
        "--chat-id",
        type=str,
        default="",
        help="Явный chat_id канала (перебивает все остальные)",
    )
    args = parser.parse_args()

    tz = pendulum.timezone(TZ_STR)

    raw_date = (args.date or "").strip() or os.getenv("WORK_DATE", "").strip()
    base_date = pendulum.parse(raw_date).in_tz(tz) if raw_date else pendulum.now(tz)
    if args.for_tomorrow:
        base_date = base_date.add(days=1)

    mode = (args.mode or "evening").lower().strip()
    logging.info("Режим поста: %s", mode)

    # Прокинем ENV для post_common
    day_offset = 0 if mode == "morning" else 1
    os.environ["POST_MODE"]    = mode
    os.environ["DAY_OFFSET"]   = str(day_offset)
    os.environ["ASTRO_OFFSET"] = str(day_offset)
    if mode == "morning":
        os.environ["SHOW_AIR"] = "1"
        os.environ["SHOW_SPACE"] = "1"
        os.environ["SHOW_SCHUMANN"] = (
            os.getenv("DISABLE_SCHUMANN", "0").lower() in ("1", "true", "yes", "on")
            and "0"
            or "1"
        )
    else:
        os.environ["SHOW_AIR"] = "0"
        os.environ["SHOW_SPACE"] = "0"
        os.environ["SHOW_SCHUMANN"] = "0"

    logging.info(
        "Флаги: DAY_OFFSET=%s, ASTRO_OFFSET=%s, AIR=%s, SPACE=%s, SCHUMANN=%s",
        os.environ.get("DAY_OFFSET"),
        os.environ.get("ASTRO_OFFSET"),
        os.environ.get("SHOW_AIR"),
        os.environ.get("SHOW_SPACE"),
        os.environ.get("SHOW_SCHUMANN"),
    )

    chat_id = resolve_chat_id(args.chat_id, args.to_test)
    logging.info("Resolved chat_id: %r", chat_id)

    bot = Bot(token=TOKEN_KLG)

    with _TodayPatch(base_date):
        # FX-only
        if args.fx_only:
            await _send_fx_only(bot, chat_id, base_date, tz, dry_run=args.dry_run)
            return

        # Сконструируем текст поста (как в post_common)
        msg = build_message(
            region_name="Калининградская область",
            sea_label=SEA_LABEL,
            sea_cities=SEA_CITIES_ORDERED,
            other_label=OTHER_LABEL,
            other_cities=OTHER_CITIES_ALL,
            tz=TZ_STR,
        )

        if args.echo or args.dry_run:
            print("\n===== ECHO MESSAGE BEGIN =====\n")
            print(msg)
            print("\n===== ECHO MESSAGE END =====\n")
            if args.dry_run:
                logging.info("DRY-RUN: отправка пропущена")
                return

        # Проверка токена
        try:
            me = await bot.get_me()
            logging.info(
                "Bot OK: @%s (id=%s)",
                getattr(me, "username", "?"),
                getattr(me, "id", "?"),
            )
        except Exception:
            logging.exception("get_me() failed — проверь TELEGRAM_TOKEN_KLG")
            raise

        # Отправка
        try:
            m = await bot.send_message(
                chat_id=chat_id,
                text=msg,
                parse_mode=constants.ParseMode.HTML,
                disable_web_page_preview=True,
            )
            logging.info(
                "Sent OK: chat=%s message_id=%s",
                getattr(m.chat, "id", "?"),
                getattr(m, "message_id", "?"),
            )
        except Exception:
            logging.exception(
                "send_message failed — проверь права бота в канале и chat_id",
            )
            raise

        # Попробуем дополнительно отправить иллюстрацию для вечернего поста
        await _maybe_send_kld_image(bot, chat_id, base_date, mode, args.dry_run)


if __name__ == "__main__":
    asyncio.run(main_kld())
