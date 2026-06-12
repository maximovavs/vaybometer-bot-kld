#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build and optionally send a sanitized Kaliningrad VayboMeter post."""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
from typing import Union

import pendulum
from telegram import Bot, constants

from post_common import build_message
from post_safety import sanitize_post_text, split_telegram_text, validation_summary

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

TOKEN_KLG = os.getenv("TELEGRAM_TOKEN_KLG", "").strip()
TZ_STR = os.getenv("TZ", "Europe/Kaliningrad")

SEA_LABEL = "Морские города"
OTHER_LABEL = "Список не-морских городов (тёплые/холодные)"
SEA_CITIES_ORDERED = [
    ("Балтийск", (54.649, 20.055)),
    ("Янтарный", (54.912, 19.887)),
    ("Зеленоградск", (54.959, 20.478)),
    ("Пионерский", (54.930, 19.825)),
    ("Светлогорск", (54.952, 20.160)),
]
OTHER_CITIES_ALL = [
    ("Гурьевск", (54.658, 20.581)),
    ("Светлый", (54.836, 19.767)),
    ("Советск", (54.507, 21.347)),
    ("Черняховск", (54.630, 21.811)),
    ("Гусев", (54.590, 22.205)),
    ("Неман", (55.030, 21.877)),
    ("Мамоново", (54.657, 19.933)),
    ("Полесск", (54.809, 21.010)),
    ("Багратионовск", (54.368, 20.632)),
    ("Ладушкин", (54.872, 19.706)),
    ("Правдинск", (54.669, 21.330)),
    ("Славск", (54.765, 21.644)),
    ("Озёрск", (54.717, 20.282)),
    ("Нестеров", (54.620, 21.647)),
    ("Краснознаменск", (54.730, 21.104)),
    ("Гвардейск", (54.655, 21.078)),
]


def _env_on(name: str, default: bool = False) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return str(val).strip().lower() in ("1", "true", "yes", "on")


def resolve_chat_id(args_chat: str, to_test: bool) -> Union[int, str]:
    chat = (args_chat or "").strip()
    if chat:
        try:
            return int(chat)
        except Exception:
            return chat
    if to_test:
        chat = os.getenv("CHANNEL_ID_TEST", "").strip()
        if not chat:
            raise SystemExit("--to-test задан, но CHANNEL_ID_TEST не определён")
        try:
            return int(chat)
        except Exception:
            return chat
    raise SystemExit("Safe runner refuses production send. Use --to-test or --chat-id explicitly.")


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
        pendulum.now = lambda tz_arg=None: _fake(self.base_date, tz_arg)    # type: ignore[assignment]
        logging.info("Дата зафиксирована как %s (%s)", self.base_date.to_datetime_string(), self.base_date.timezone_name)
        return self

    def __exit__(self, *args):
        if self._orig_today:
            pendulum.today = self._orig_today  # type: ignore[assignment]
        if self._orig_now:
            pendulum.now = self._orig_now      # type: ignore[assignment]
        return False


async def main() -> None:
    parser = argparse.ArgumentParser(description="Safe post builder for Kaliningrad VayboMeter")
    parser.add_argument("--mode", choices=["morning", "evening"], default=os.getenv("POST_MODE", "evening"))
    parser.add_argument("--date", default=os.getenv("WORK_DATE", ""))
    parser.add_argument("--for-tomorrow", action="store_true")
    parser.add_argument("--to-test", action="store_true")
    parser.add_argument("--chat-id", default="")
    parser.add_argument("--format-v2", action="store_true", help="Build scenario-style FORMAT_V2 text after legacy sanitizing.")
    parser.add_argument("--send", action="store_true", help="Actually send to CHANNEL_ID_TEST / --chat-id. Omit for dry-run.")
    parser.add_argument("--no-test-label", action="store_true", help="Do not prepend the 'Test safe post' label when sending.")
    args = parser.parse_args()

    mode = (args.mode or "evening").strip().lower()
    os.environ["POST_MODE"] = mode
    use_format_v2 = bool(args.format_v2 or _env_on("FORMAT_V2"))
    os.environ["FORMAT_V2"] = "1" if use_format_v2 else "0"
    day_offset = 0 if mode == "morning" else 1
    os.environ["DAY_OFFSET"] = str(day_offset)
    os.environ["ASTRO_OFFSET"] = str(day_offset)
    if mode == "morning":
        os.environ.setdefault("SHOW_AIR", "1")
        os.environ.setdefault("SHOW_SPACE", "1")
        os.environ.setdefault("SHOW_SCHUMANN", "1")
    else:
        os.environ["SHOW_AIR"] = "0"
        os.environ["SHOW_SPACE"] = "0"
        os.environ["SHOW_SCHUMANN"] = "0"

    tz = pendulum.timezone(TZ_STR)
    base_date = pendulum.parse(args.date).in_tz(tz) if args.date else pendulum.now(tz)
    if args.for_tomorrow:
        base_date = base_date.add(days=1)

    with _TodayPatch(base_date):
        raw_msg = build_message(
            region_name="Калининградская область",
            sea_label=SEA_LABEL,
            sea_cities=SEA_CITIES_ORDERED,
            other_label=OTHER_LABEL,
            other_cities=OTHER_CITIES_ALL,
            tz=TZ_STR,
            mode=mode,
        )

    legacy_result = sanitize_post_text(raw_msg)
    final_result = legacy_result
    final_label = "SAFE MESSAGE"

    if use_format_v2:
        from format_v2 import build_format_v2
        v2_raw = build_format_v2("Калининградская область", mode, legacy_result.text)
        final_result = sanitize_post_text(v2_raw)
        final_label = "FORMAT_V2 MESSAGE"
        print("\n===== FORMAT_V2 RAW BEGIN =====\n")
        print(v2_raw)
        print("\n===== FORMAT_V2 RAW END =====\n")
        print("\n===== FORMAT_V2 SAFETY SUMMARY =====\n")
        print(validation_summary(final_result))

    chunks = split_telegram_text(final_result.text)

    print("\n===== RAW MESSAGE BEGIN =====\n")
    print(raw_msg)
    print("\n===== RAW MESSAGE END =====\n")
    print("\n===== LEGACY SAFETY SUMMARY =====\n")
    print(validation_summary(legacy_result))
    print(f"\n===== {final_label} BEGIN =====\n")
    print(final_result.text)
    print(f"\n===== {final_label} END =====\n")

    if not args.send:
        logging.info("SAFE DRY-RUN: отправка пропущена, format_v2=%s, chunks=%d", use_format_v2, len(chunks))
        return

    if not TOKEN_KLG:
        raise SystemExit("TELEGRAM_TOKEN_KLG не задан")
    chat_id = resolve_chat_id(args.chat_id, args.to_test)
    bot = Bot(token=TOKEN_KLG)
    for idx, chunk in enumerate(chunks, start=1):
        if args.no_test_label:
            text = chunk
        else:
            prefix = f"<b>Test safe post {idx}/{len(chunks)}</b>\n" if len(chunks) > 1 else "<b>Test safe post</b>\n"
            text = prefix + chunk
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=constants.ParseMode.HTML,
            disable_web_page_preview=True,
        )
    logging.info("SAFE TEST sent: chat=%s chunks=%d format_v2=%s", chat_id, len(chunks), use_format_v2)


if __name__ == "__main__":
    asyncio.run(main())
