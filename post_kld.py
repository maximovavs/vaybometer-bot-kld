#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
post_kld.py  •  Запуск «Kaliningrad daily post» для Telegram-канала.

... (докстринг сохранён; добавлена логика приоритетов картинки и оверлеев)
"""

from __future__ import annotations

import os
import sys
import re
import argparse
import asyncio
import logging
import secrets
from typing import Dict, Any, Tuple, Union, Optional
from pathlib import Path

import pendulum
from telegram import Bot, constants

from post_common import build_message, fx_morning_line  # type: ignore

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

TOKEN_KLG = os.getenv("TELEGRAM_TOKEN_KLG", "")
if not TOKEN_KLG:
    logging.error("Не задан TELEGRAM_TOKEN_KLG")
    sys.exit(1)

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

FX_CACHE_PATH = Path("fx_cache.json")

def _fmt_delta(x: float | int | None) -> str:
    if x is None:
        return "→0.00"
    try:
        x = float(x)
    except Exception:
        return "→0.00"
    if x > 0:
        return f"↑{abs(x):.2f}"
    if x < 0:
        return f"↓{abs(x):.2f}"
    return "→0.00"


def _ruble_summary(deltas: list[float]) -> str:
    if deltas and all(x > 0 for x in deltas):
        return "🧭 Рубль слабее к USD, EUR и CNY."
    if deltas and all(x < 0 for x in deltas):
        return "🧭 Рубль крепче к USD, EUR и CNY."
    return "🧭 Валюты к ₽ движутся смешанно."


def _fx_title_date_ddmm(date_local: pendulum.DateTime | None, rates: Dict[str, Any]) -> str:
    raw_date = (
        rates.get("effective_date")
        or rates.get("as_of")
        or rates.get("date")
        or rates.get("cbr_date")
    )
    if raw_date is not None:
        raw_s = str(raw_date).strip()
        if len(raw_s) >= 10 and raw_s[4] == "-" and raw_s[7] == "-":
            return f"{raw_s[8:10]}.{raw_s[5:7]}"
        normalized = _normalize_cbr_date(raw_date)
        if normalized:
            try:
                return pendulum.parse(normalized).format("DD.MM")
            except Exception:
                pass
    if date_local is not None and hasattr(date_local, "format"):
        try:
            return date_local.format("DD.MM")
        except Exception:
            pass
    return ""

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
    shown_deltas: list[float] = []

    def token(code: str, name: str) -> str:
        r = rates.get(code) or {}
        val = r.get("value"); dlt = r.get("delta")
        if val is None:
            return f"{name} — ₽"
        try:
            val_s = f"{float(val):.2f}"
        except Exception:
            val_s = "—"
        try:
            shown_deltas.append(float(dlt))
        except Exception:
            pass
        return f"{name} {val_s} ₽ {_fmt_delta(dlt)}"

    line = " · ".join([token("USD", "USD"), token("EUR", "EUR"), token("CNY", "CNY")])
    title_date = _fx_title_date_ddmm(date_local, rates)
    title = f"💱 <b>Курсы ЦБ РФ на {title_date}</b>" if title_date else "💱 <b>Курсы ЦБ РФ</b>"
    return f"{title}\n{line}\n{_ruble_summary(shown_deltas)}", rates

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
        logging.info("FX sent: chat=%s message_id=%s", getattr(m.chat, "id", "?"), getattr(m, "message_id", "?"))
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

def resolve_chat_id(args_chat: str, to_test: bool) -> Union[int, str]:
    chat_override = (args_chat or "").strip() or os.getenv("CHANNEL_ID_OVERRIDE", "").strip()
    if chat_override:
        try:
            return int(chat_override)
        except Exception:
            logging.warning("CHAT_ID override не число — используем как строку: %r", chat_override)
            return chat_override

    if to_test:
        ch_test = os.getenv("CHANNEL_ID_TEST", "").strip()
        if not ch_test:
            logging.error("--to-test задан, но CHANNEL_ID_TEST не определён в окружении")
            sys.exit(1)
        try:
            return int(ch_test)
        except Exception:
            logging.info("CHANNEL_ID_TEST не число — используем как строку: %r", ch_test)
            return ch_test

    ch_main = os.getenv("CHANNEL_ID_KLG", "").strip()
    if not ch_main:
        logging.error("CHANNEL_ID_KLG не задан и не указан --chat-id/override")
        sys.exit(1)
    try:
        return int(ch_main)
    except Exception:
        logging.info("CHANNEL_ID_KLG не число — используем как строку: %r", ch_main)
        return ch_main


# -------- priorities helpers --------

ZODIAC_GLYPH = {
    "Aries": "♈", "Taurus": "♉", "Gemini": "♊", "Cancer": "♋",
    "Leo": "♌", "Virgo": "♍", "Libra": "♎", "Scorpio": "♏",
    "Sagittarius": "♐", "Capricorn": "♑", "Aquarius": "♒", "Pisces": "♓",
}

def _gust_from_line(line: str) -> Optional[float]:
    values: list[float] = []
    for raw in re.findall(r"порыв\w*\s*(?:до\s*)?(\d+(?:[\.,]\d+)?)\s*м\s*/?\s*с", str(line or ""), flags=re.I):
        try:
            values.append(float(raw.replace(",", ".")))
        except Exception:
            pass
    return max(values) if values else None


def _is_warning_line(line: str) -> bool:
    s = str(line or "").strip()
    return s.startswith("⚠️") or s.startswith("⚠")


def _is_weather_line(line: str) -> bool:
    s = str(line or "").strip()
    low = s.lower()
    return "погода:" in low or "°c" in low or "°C" in s


def _skip_overlay_storm_line(line: str) -> bool:
    s = str(line or "").strip()
    return s.startswith((
        "✨ VayboMeter",
        "🧭 Главное завтра",
        "💬 ",
        "⚠️ Нюанс:",
        "⚠ Нюанс:",
        "⚠️ Общий фон:",
        "⚠ Общий фон:",
        "✅ План",
    ))


def _storm_overlay_subtitle(line: str) -> str:
    s = re.sub(r"</?b>", "", str(line or "")).strip()
    gust = _gust_from_line(s)
    if isinstance(gust, (int, float)) and gust >= 15:
        value = int(gust) if float(gust).is_integer() else gust
        return f"Порывы до {value} м/с"
    s = re.sub(r"^⚠️?\s*", "", s).strip()
    s = re.sub(r"^Штормовое\s+предупреждение\s*:?\s*", "", s, flags=re.I).strip()
    s = re.sub(r"^Предупреждение\s*:?\s*", "", s, flags=re.I).strip()
    if s:
        return s[:1].upper() + s[1:]
    return "Сильный ветер и порывы на побережье"


def _extract_storm_warning(msg: str) -> Optional[str]:
    if not msg:
        return None
    explicit_warning: list[str] = []
    warning_gust: list[str] = []
    weather_gust: list[str] = []
    fallback: list[str] = []
    for line in str(msg or "").splitlines():
        stripped = line.strip()
        low = stripped.lower()
        if not stripped or "без шторма" in low:
            continue
        gust = _gust_from_line(stripped)
        if _skip_overlay_storm_line(stripped):
            continue
        if stripped.startswith("⚠️ Штормовое предупреждение:") or ("шторм" in low and "предупреждение" in low):
            explicit_warning.append(stripped)
            continue
        if isinstance(gust, (int, float)) and gust >= 15 and _is_warning_line(stripped):
            warning_gust.append(stripped)
            continue
        if isinstance(gust, (int, float)) and gust >= 15 and _is_weather_line(stripped):
            weather_gust.append(stripped)
            continue
        if "шторм" in low:
            fallback.append(stripped)
    for bucket in (explicit_warning, warning_gust, weather_gust, fallback):
        if bucket:
            return _storm_overlay_subtitle(bucket[0])
    return None

def _seed_for_image(base_date: pendulum.DateTime, *, style_name: str) -> Optional[int]:
    mode = os.getenv("IMG_SEED_MODE", "daily").strip().lower()
    variant = os.getenv("IMG_VARIANT", "").strip()
    try:
        v = int(variant) if variant else 0
    except Exception:
        v = 0

    if mode == "deterministic":
        return None
    if mode == "random":
        return secrets.randbelow(2_000_000_000)

    key = f"{base_date.to_date_string()}|{style_name}|{v}"
    return abs(hash(key)) % 2_000_000_000


async def _maybe_send_kld_image(
    bot: Bot,
    chat_id: Union[int, str],
    base_date: "pendulum.DateTime",
    mode: str,
    dry_run: bool,
    *,
    msg_text: str,
) -> None:
    if mode != "evening":
        return

    try:
        from image_prompt_kld import build_kld_evening_prompt, get_lunar_meta  # type: ignore
    except Exception as e:
        logging.info("KLD image: image_prompt_kld недоступен — картинка пропущена (%s)", e)
        return

    try:
        import imagegen
    except Exception as e:
        logging.info("KLD image: imagegen.py недоступен — картинка пропущена (%s)", e)
        return

    overlay_mod = None
    try:
        import image_overlays  # type: ignore
        overlay_mod = image_overlays
    except Exception:
        overlay_mod = None

    try:
        storm_line = _extract_storm_warning(msg_text)
        storm_on = bool(storm_line)

        tomorrow = base_date.date().add(days=1)
        lunar = get_lunar_meta(tomorrow)

        force_style = None
        if storm_on:
            force_style = "sea_dunes"
        elif getattr(lunar, "is_full_or_new", False):
            force_style = "moon_goddess"

        prompt, style_name = build_kld_evening_prompt(
            date=base_date.date(),
            marine_mood=("storm warning, strong gusts, waves" if storm_on else ""),
            inland_mood=("windy cold night" if storm_on else ""),
            astro_mood_en=getattr(lunar, "phrase_en", "") or "",
            force_style=force_style,
            storm=storm_on,
            final_format_v2_message=msg_text,
        )

        seed = _seed_for_image(base_date, style_name=style_name)
        img_path = imagegen.generate_kld_evening_image(prompt=prompt, style_name=style_name, seed=seed)
        logging.info("KLD image: local image ready: %s", img_path)

        final_path = img_path

        if overlay_mod:
            try:
                from PIL import Image  # type: ignore
                src = Path(img_path)
                out = src.with_name(src.stem + "_ov.jpg")

                im = Image.open(src).convert("RGB")

                if storm_on and hasattr(overlay_mod, "overlay_storm_window"):
                    data = overlay_mod.StormData(
                        title="Штормовое предупреждение",
                        subtitle=(storm_line or "Сильный ветер и порывы на побережье"),
                        icon="⚠️",
                    )
                    im = overlay_mod.overlay_storm_window(im, data, anchor="top_left")

                if (not storm_on) and getattr(lunar, "is_full_or_new", False) and hasattr(overlay_mod, "overlay_moon_badge"):
                    phase = getattr(lunar, "phase_key", "") or ""
                    sign = getattr(lunar, "sign_en", "") or ""
                    glyph = ZODIAC_GLYPH.get(sign, "✶")
                    caption = getattr(lunar, "phrase_en", "") or ""
                    if caption:
                        caption = caption.replace(" in ", " • ")
                    data = overlay_mod.MoonData(phase=phase, zodiac=glyph, caption=caption)
                    im = overlay_mod.overlay_moon_badge(im, data, anchor="top_right")

                im.save(out, format="JPEG", quality=92, optimize=True)
                final_path = str(out)
                logging.info("KLD image: overlay applied -> %s", final_path)
            except Exception:
                logging.exception("KLD image: overlay failed (continue with base image)")

        if dry_run:
            logging.info("KLD image: DRY-RUN — отправка картинки пропущена")
            return

        if storm_on:
            caption = "⚠️ Визуальный вайб: штормовое предупреждение над Балтикой"
        elif getattr(lunar, "is_full_or_new", False):
            caption = f"🌙 Визуальный вайб: {getattr(lunar,'phrase_en','Moon')} над Балтикой"
        else:
            caption = "Визуальный вайб завтрашнего вечера над Балтикой 🌊"

        with open(final_path, "rb") as f:
            msg = await bot.send_photo(chat_id=chat_id, photo=f, caption=caption)

        logging.info("KLD image: photo sent: chat=%s message_id=%s", getattr(msg.chat, "id", "?"), getattr(msg, "message_id", "?"))
    except Exception:
        logging.exception("KLD image: ошибка при генерации/отправке картинки")


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

        logging.info("Дата для поста зафиксирована как %s (TZ %s)", self.base_date.to_datetime_string(), self.base_date.timezone_name)
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._orig_today:
            pendulum.today = self._orig_today  # type: ignore[assignment]
        if self._orig_now:
            pendulum.now = self._orig_now  # type: ignore[assignment]
        return False


async def main_kld() -> None:
    parser = argparse.ArgumentParser(description="Kaliningrad daily post runner")
    parser.add_argument("--mode", choices=["morning", "evening"], default=(os.getenv("MODE") or "evening"))
    parser.add_argument("--date", type=str, default="")
    parser.add_argument("--for-tomorrow", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--echo", action="store_true")
    parser.add_argument("--fx-only", action="store_true")
    parser.add_argument("--to-test", action="store_true")
    parser.add_argument("--chat-id", type=str, default="")
    args = parser.parse_args()

    tz = pendulum.timezone(TZ_STR)

    raw_date = (args.date or "").strip() or os.getenv("WORK_DATE", "").strip()
    base_date = pendulum.parse(raw_date).in_tz(tz) if raw_date else pendulum.now(tz)
    if args.for_tomorrow:
        base_date = base_date.add(days=1)

    mode = (args.mode or "evening").lower().strip()
    logging.info("Режим поста: %s", mode)

    day_offset = 0 if mode == "morning" else 1
    os.environ["POST_MODE"] = mode
    os.environ["DAY_OFFSET"] = str(day_offset)
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

    chat_id = resolve_chat_id(args.chat_id, args.to_test)
    bot = Bot(token=TOKEN_KLG)

    with _TodayPatch(base_date):
        if args.fx_only:
            await _send_fx_only(bot, chat_id, base_date, tz, dry_run=args.dry_run)
            return

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

        await bot.get_me()

        m = await bot.send_message(
            chat_id=chat_id,
            text=msg,
            parse_mode=constants.ParseMode.HTML,
            disable_web_page_preview=True,
        )
        logging.info("Sent OK: chat=%s message_id=%s", getattr(m.chat, "id", "?"), getattr(m, "message_id", "?"))

        await _maybe_send_kld_image(bot, chat_id, base_date, mode, args.dry_run, msg_text=msg)


if __name__ == "__main__":
    asyncio.run(main_kld())
