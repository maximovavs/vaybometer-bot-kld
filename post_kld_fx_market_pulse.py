#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Separate Kaliningrad daytime FX post with local Market Pulse.

This runner is used only by the noon FX workflow, never by morning/evening
weather posts. It keeps the existing CBR anti-duplicate/cache behavior and adds
BTC/ETH/Gold as a compact market pulse.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import logging
import os
from typing import Any, Union

import pendulum
import requests
from telegram import Bot, constants

from post_kld import (
    FX_CACHE_PATH,
    TOKEN_KLG,
    TZ_STR,
    _build_fx_message,
    _normalize_cbr_date,
    resolve_chat_id,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


def _to_float(x: Any) -> float | None:
    try:
        return float(x)
    except Exception:
        return None


def _fmt_usd_compact(value: float | None) -> str:
    if value is None:
        return "н/д"
    if abs(value) >= 1000:
        return f"${value / 1000:.1f}K"
    if abs(value) >= 100:
        return f"${value:.0f}"
    return f"${value:.2f}"


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return ""
    if value > 0:
        return f" ↑{abs(value):.1f}%"
    if value < 0:
        return f" ↓{abs(value):.1f}%"
    return " →0.0%"


def _fetch_crypto() -> list[str]:
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={
                "ids": "bitcoin,ethereum",
                "vs_currencies": "usd",
                "include_24hr_change": "true",
            },
            timeout=10,
            headers={"User-Agent": "VayboMeter/1.0"},
        )
        r.raise_for_status()
        data = r.json() or {}
    except Exception as e:
        logging.warning("KLD Market Pulse: crypto unavailable: %s", e)
        return []

    out: list[str] = []
    btc = data.get("bitcoin") or {}
    eth = data.get("ethereum") or {}
    btc_usd = _to_float(btc.get("usd"))
    eth_usd = _to_float(eth.get("usd"))
    bits: list[str] = []
    if btc_usd is not None:
        bits.append(f"BTC {_fmt_usd_compact(btc_usd)}{_fmt_pct(_to_float(btc.get('usd_24h_change')))}")
    if eth_usd is not None:
        bits.append(f"ETH {_fmt_usd_compact(eth_usd)}{_fmt_pct(_to_float(eth.get('usd_24h_change')))}")
    if bits:
        out.append("24ч: " + " · ".join(bits))
    return out


def _fetch_gold_from_stooq(symbol: str) -> float | None:
    try:
        r = requests.get(
            "https://stooq.com/q/l/",
            params={"s": symbol, "f": "sd2t2ohlcv", "h": "", "e": "csv"},
            timeout=10,
            headers={"User-Agent": "VayboMeter/1.0"},
        )
        r.raise_for_status()
        lines = [x.strip() for x in r.text.splitlines() if x.strip()]
        if len(lines) < 2:
            return None
        parts = lines[-1].split(",")
        if len(parts) <= 6 or parts[6].upper() == "N/D":
            return None
        return _to_float(parts[6])
    except Exception:
        return None


def _fetch_gold_from_yahoo(symbol: str) -> float | None:
    try:
        r = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
            params={"range": "1d", "interval": "1d"},
            timeout=10,
            headers={"User-Agent": "VayboMeter/1.0"},
        )
        r.raise_for_status()
        result = (((r.json() or {}).get("chart") or {}).get("result") or [None])[0] or {}
        meta = result.get("meta") or {}
        price = _to_float(meta.get("regularMarketPrice"))
        if price is not None:
            return price
        quote = (((result.get("indicators") or {}).get("quote") or [None])[0] or {})
        for raw in reversed(quote.get("close") or []):
            price = _to_float(raw)
            if price is not None:
                return price
    except Exception:
        return None
    return None


def _fetch_gold() -> list[str]:
    price = None
    for symbol in ("xauusd", "gc.f"):
        price = _fetch_gold_from_stooq(symbol)
        if price is not None:
            break
    if price is None:
        for symbol in ("GC=F", "XAUUSD=X"):
            price = _fetch_gold_from_yahoo(symbol)
            if price is not None:
                break
    if price is not None:
        return [f"Gold/oz {_fmt_usd_compact(price)}"]
    return ["Gold/oz н/д"]


def build_market_pulse_block() -> str:
    items = _fetch_crypto() + _fetch_gold()
    if not items:
        return ""
    return "📊 <b>Пульс рынков</b>\n" + "\n".join(items) + "\n<i>Инфо-ориентир, не инвестрекомендация.</i>"


def inject_market_pulse(fx_text: str, block: str) -> str:
    if not block or "<b>Market Pulse</b>" in fx_text or "<b>Пульс рынков</b>" in fx_text:
        return fx_text
    return fx_text.rstrip() + "\n\n" + block + "\n\n#Калининград #курсы_валют #рынки"


def _force_publish(to_test: bool) -> bool:
    return bool(to_test) or str(os.getenv("FX_FORCE", "")).strip().lower() in ("1", "true", "yes", "on")


def _should_publish(cbr_date: str | None, force: bool) -> bool:
    if not cbr_date:
        logging.info("FX: cbr_date не определена — антидубль по ЦБ пропущен.")
        return True
    try:
        fx = importlib.import_module("fx")
        if hasattr(fx, "should_publish_again"):
            should = fx.should_publish_again(FX_CACHE_PATH, cbr_date)  # type: ignore[attr-defined]
            if not should and not force:
                logging.info("Курсы ЦБ не обновились — пост пропущен.")
                return False
            if not should and force:
                logging.info("Курсы ЦБ не обновились, но FX_FORCE/to_test=1 — публикуем.")
    except Exception as e:
        logging.warning("FX: skip-check failed (продолжаем отправку): %s", e)
    return True


def _save_fx_cache(cbr_date: str | None, text: str) -> None:
    try:
        fx = importlib.import_module("fx")
        if cbr_date and hasattr(fx, "save_fx_cache"):
            fx.save_fx_cache(FX_CACHE_PATH, cbr_date, text)  # type: ignore[attr-defined]
        else:
            FX_CACHE_PATH.write_text(json.dumps({"cbr_date": cbr_date, "text": text}, ensure_ascii=False), "utf-8")
    except Exception as e:
        logging.warning("FX: save cache failed: %s", e)


async def main() -> None:
    parser = argparse.ArgumentParser(description="KLD separate FX post with Market Pulse")
    parser.add_argument("--date", default="")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--to-test", action="store_true")
    parser.add_argument("--chat-id", default="")
    args = parser.parse_args()

    if not TOKEN_KLG:
        raise SystemExit("TELEGRAM_TOKEN_KLG is not set")

    tz = pendulum.timezone(TZ_STR)
    date_local = pendulum.parse(args.date).in_tz(tz) if args.date else pendulum.now(tz)
    fx_text, rates = _build_fx_message(date_local, tz)
    text = inject_market_pulse(fx_text, build_market_pulse_block())
    raw_date = rates.get("as_of") or rates.get("date") or rates.get("cbr_date")
    cbr_date = _normalize_cbr_date(raw_date)

    if not _should_publish(cbr_date, _force_publish(args.to_test)):
        return

    if args.dry_run:
        logging.info("DRY-RUN (kld-fx-market-pulse):\n%s", text)
        return

    chat_id: Union[int, str] = resolve_chat_id(args.chat_id, args.to_test)
    bot = Bot(token=TOKEN_KLG)
    msg = await bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode=constants.ParseMode.HTML,
        disable_web_page_preview=True,
    )
    _save_fx_cache(cbr_date, text)
    logging.info("KLD FX Market Pulse sent: chat=%s message_id=%s", getattr(msg.chat, "id", "?"), getattr(msg, "message_id", "?"))


if __name__ == "__main__":
    asyncio.run(main())
