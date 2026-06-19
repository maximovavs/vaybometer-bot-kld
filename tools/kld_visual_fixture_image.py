#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build or send synthetic KLD visual fixture images.

Side-effect free by default:
- no weather/marine fetch;
- no LLM call;
- no Telegram send unless --send-to-test is explicitly set;
- no image generation unless --generate or --send-to-test is explicitly set.

Examples:
    python tools/kld_visual_fixture_image.py --scenario drizzle
    python tools/kld_visual_fixture_image.py --scenario rain --generate
    python tools/kld_visual_fixture_image.py --scenario storm --send-to-test
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from image_prompt_kld import build_kld_evening_prompt  # noqa: E402
from visual_context_kld import build_visual_context  # noqa: E402
from visual_rules import apply_visual_rules, build_prompt_from_cues, to_json  # noqa: E402

BASE_WIND = "💨 Ветер: 3–5 м/с, порывы до 7 м/с"
GUSTY_WIND = "💨 Ветер: 5–8 м/с, порывы до 14 м/с"
STORM_WIND = "💨 Ветер: 8–12 м/с, порывы до 18 м/с"
SUP_EXPERIENCED = "🧜‍♂️ SUP: только для опытных и короткой сессии • гидрокостюм 4/3 мм (боты)"
MOON_CRESCENT = "🌙 Луна: растущий серп"
MOON_NEW = "🌙 Луна: новолуние"
MOON_FULL = "🌙 Луна: полнолуние"


def _msg(lines: list[str]) -> str:
    return "\n".join(lines) + "\n"


FIXTURES: dict[str, str] = {
    "cloudy": _msg([
        "🌊 Морские города",
        "Светлогорск: 30/16 °C • 🌥 пасм • 🌊 15 • 0.2 м",
        "Зеленоградск: 29/13 °C • 🌥 пасм • 🌊 16",
        "Балтийск: 29/15 °C • 🌥 пасм • 🌊 18 • 0.1 м",
        "Янтарный: 23/16 °C • 🌥 пасм • 🌊 15 • 0.3 м",
        "Пионерский: 22/16 °C • 🌦 морось • 🌊 15",
        "Мамоново: 29/16 °C • 🌧 дождь",
        GUSTY_WIND,
        SUP_EXPERIENCED,
        MOON_CRESCENT,
    ]),
    "drizzle": _msg([
        "🌊 Морские города",
        "Светлогорск: 20/16 °C • 🌦 морось • 🌊 15 • 0.2 м",
        "Зеленоградск: 20/15 °C • 🌦 морось • 🌊 15",
        "Балтийск: 20/15 °C • 🌦 морось • 🌊 15 • 0.3 м",
        "Янтарный: 20/16 °C • 🌥 пасм • 🌊 15 • 0.2 м",
        "Пионерский: 20/16 °C • 🌦 морось • 🌊 15",
        BASE_WIND,
        SUP_EXPERIENCED,
        MOON_CRESCENT,
    ]),
    "rain": _msg([
        "🌊 Морские города",
        "Светлогорск: 18/15 °C • 🌧 дождь • 🌊 15 • 0.4 м",
        "Зеленоградск: 18/14 °C • 🌧 дождь • 🌊 15",
        "Балтийск: 17/14 °C • 🌧 дождь • 🌊 15 • 0.5 м",
        "Янтарный: 18/15 °C • 🌥 пасм • 🌊 15 • 0.4 м",
        "Пионерский: 18/15 °C • 🌧 дождь • 🌊 15",
        GUSTY_WIND,
        SUP_EXPERIENCED,
        MOON_CRESCENT,
    ]),
    "storm": _msg([
        "🌊 Морские города",
        "⚠️ Штормовое предупреждение: сильный ветер, гроза, волна и прибой на побережье",
        "Светлогорск: 16/13 °C • ⛈ гроза • 🌊 14 • 1.7 м",
        "Зеленоградск: 16/13 °C • ⛈ гроза • 🌊 14 • 1.6 м",
        "Балтийск: 15/13 °C • ⛈ гроза • 🌊 14 • 1.8 м",
        "Янтарный: 16/13 °C • ⛈ гроза • 🌊 14 • 1.7 м",
        STORM_WIND,
        SUP_EXPERIENCED,
        MOON_CRESCENT,
    ]),
    "new_moon": _msg([
        "🌊 Морские города",
        "Светлогорск: 20/15 °C • 🌥 пасм • 🌊 15 • 0.2 м",
        "Зеленоградск: 20/15 °C • 🌥 пасм • 🌊 15",
        BASE_WIND,
        MOON_NEW,
    ]),
    "full_moon": _msg([
        "🌊 Морские города",
        "Светлогорск: 20/15 °C • 🌥 пасм • 🌊 15 • 0.2 м",
        "Зеленоградск: 20/15 °C • 🌥 пасм • 🌊 15",
        BASE_WIND,
        MOON_FULL,
    ]),
}


def _chat_id(value: str) -> int | str:
    value = (value or "").strip()
    try:
        return int(value)
    except Exception:
        return value


async def _send_photo(path: str, caption: str) -> None:
    from telegram import Bot  # imported only for explicit send mode

    token = os.getenv("TELEGRAM_TOKEN_KLG", "").strip()
    chat_id_raw = os.getenv("CHANNEL_ID_TEST", "").strip()
    if not token:
        raise SystemExit("TELEGRAM_TOKEN_KLG is required for --send-to-test")
    if not chat_id_raw:
        raise SystemExit("CHANNEL_ID_TEST is required for --send-to-test")

    with open(path, "rb") as f:
        msg = await Bot(token=token).send_photo(chat_id=_chat_id(chat_id_raw), photo=f, caption=caption)
    print(f"Sent fixture image to CHANNEL_ID_TEST, message_id={getattr(msg, 'message_id', '?')}")


def build_fixture_payload(scenario: str) -> dict[str, Any]:
    message = FIXTURES[scenario]
    ctx = build_visual_context(message, post_type="evening")
    cues = apply_visual_rules(ctx)
    diagnostic_prompt = build_prompt_from_cues(cues)
    image_prompt, style_name = build_kld_evening_prompt(
        dt.date(2026, 6, 19),
        marine_mood="",
        inland_mood="",
        final_format_v2_message=message,
        post_type="evening",
    )
    return {
        "scenario": scenario,
        "message": message,
        "context": ctx,
        "cues": cues,
        "diagnostic_prompt": diagnostic_prompt,
        "image_prompt": image_prompt,
        "style_name": style_name,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build/send KLD synthetic visual fixture image")
    parser.add_argument("--scenario", choices=sorted(FIXTURES), required=True)
    parser.add_argument("--generate", action="store_true", help="Generate local image but do not send")
    parser.add_argument("--send-to-test", action="store_true", help="Generate and send image to CHANNEL_ID_TEST only")
    args = parser.parse_args()

    payload = build_fixture_payload(args.scenario)
    ctx = payload["context"]
    cues = payload["cues"]

    print("\n===== FIXTURE_MESSAGE BEGIN =====\n")
    print(payload["message"])
    print("===== FIXTURE_MESSAGE END =====\n")

    print("\n===== FIXTURE_VISUAL_CONTEXT BEGIN =====\n")
    print(json.dumps(ctx.__dict__, ensure_ascii=False, indent=2))
    print("\n===== FIXTURE_VISUAL_CONTEXT END =====\n")

    print("\n===== FIXTURE_VISUAL_CUES BEGIN =====\n")
    print(to_json(cues))
    print("\n===== FIXTURE_VISUAL_CUES END =====\n")

    print("\n===== FIXTURE_DIAGNOSTIC_PROMPT BEGIN =====\n")
    print(payload["diagnostic_prompt"])
    print("\n===== FIXTURE_DIAGNOSTIC_PROMPT END =====\n")

    print("\n===== FIXTURE_IMAGE_PROMPT_STYLE BEGIN =====\n")
    print(payload["style_name"])
    print("\n===== FIXTURE_IMAGE_PROMPT_STYLE END =====\n")

    print("\n===== FIXTURE_IMAGE_PROMPT BEGIN =====\n")
    print(payload["image_prompt"])
    print("\n===== FIXTURE_IMAGE_PROMPT END =====\n")

    if not (args.generate or args.send_to_test):
        print("Image generation skipped. Use --generate or --send-to-test.")
        return

    import imagegen  # imported only for explicit image mode

    img_path = imagegen.generate_kld_evening_image(
        prompt=payload["image_prompt"],
        style_name=payload["style_name"],
    )
    print(f"Generated fixture image: {img_path}")

    if args.send_to_test:
        caption = f"🧪 KLD fixture image • {args.scenario} • FORMAT_V2 SceneCues"
        asyncio.run(_send_photo(img_path, caption))


if __name__ == "__main__":
    main()
