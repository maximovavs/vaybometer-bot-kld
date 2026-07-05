#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build or send KLD visual fixture images.

Side-effect free by default:
- no weather/marine fetch;
- no LLM call;
- no Telegram send unless --send-to-test is explicitly set;
- no image generation unless --generate or --send-to-test is explicitly set.

Examples:
    python tools/kld_visual_fixture_image.py --scenario drizzle
    python tools/kld_visual_fixture_image.py --scenario rain --generate
    python tools/kld_visual_fixture_image.py --scenario storm --send-to-test
    python tools/kld_visual_fixture_image.py --message-file format_v2_message.txt --generate
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from image_prompt_kld import (  # noqa: E402
    _extract_prompt_date,
    build_kld_evening_prompt,
    kld_scene_metadata,
    kld_visual_cache_key,
)
from image_prompt_kld_morning import build_kld_morning_prompt  # noqa: E402
from kld_visual_dedup import (  # noqa: E402
    evaluate_kld_visual_candidate,
    kld_visual_history_path,
    record_kld_visual_publication,
)
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


async def _send_photo(path: str, caption: str, *, chat_id_override: str = "") -> None:
    from telegram import Bot  # imported only for explicit send mode

    token = os.getenv("TELEGRAM_TOKEN_KLG", "").strip()
    chat_id_raw = (chat_id_override or os.getenv("CHANNEL_ID_TEST", "")).strip()
    if not token:
        raise SystemExit("TELEGRAM_TOKEN_KLG is required for --send-to-test")
    if not chat_id_raw:
        raise SystemExit("CHANNEL_ID_TEST or --chat-id is required for --send-to-test")

    with open(path, "rb") as f:
        msg = await Bot(token=token).send_photo(chat_id=_chat_id(chat_id_raw), photo=f, caption=caption)
    print(f"Sent KLD image, chat={chat_id_raw}, message_id={getattr(msg, 'message_id', '?')}")


def build_payload(
    message: str,
    label: str,
    *,
    post_type: str = "evening",
    variation_attempt: int = 0,
) -> dict[str, Any]:
    ctx = build_visual_context(message, post_type=post_type)
    cues = apply_visual_rules(ctx)
    diagnostic_prompt = build_prompt_from_cues(cues)
    if post_type == "morning":
        image_prompt, style_name = build_kld_morning_prompt(
            message,
            post_type="morning",
            variation_attempt=variation_attempt,
        )
    else:
        image_prompt, style_name = build_kld_evening_prompt(
            dt.date(2026, 6, 19),
            marine_mood="",
            inland_mood="",
            final_format_v2_message=message,
            post_type="evening",
            variation_attempt=variation_attempt,
        )
    date_key = _extract_prompt_date(message, dt.date(2026, 6, 19))
    metadata = kld_scene_metadata(
        ctx,
        date_key=date_key,
        post_type=post_type,
        source_text=message,
        variation_attempt=variation_attempt,
    )
    cache_key = kld_visual_cache_key(metadata)
    return {
        "scenario": label,
        "post_type": post_type,
        "variation_attempt": variation_attempt,
        "message": message,
        "context": ctx,
        "cues": cues,
        "diagnostic_prompt": diagnostic_prompt,
        "image_prompt": image_prompt,
        "style_name": style_name,
        "metadata": metadata,
        "cache_key": cache_key,
    }


def build_fixture_payload(
    scenario: str,
    *,
    post_type: str = "evening",
    variation_attempt: int = 0,
) -> dict[str, Any]:
    return build_payload(FIXTURES[scenario], scenario, post_type=post_type, variation_attempt=variation_attempt)


def _seed_from_cache_key(cache_key: str) -> int:
    return int(hashlib.sha256(cache_key.encode("utf-8")).hexdigest()[:8], 16)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build/send KLD visual image")
    parser.add_argument("--scenario", choices=sorted(FIXTURES), default="")
    parser.add_argument("--message-file", default="", help="Use an already-built FORMAT_V2 message from file instead of fixture")
    parser.add_argument("--post-type", choices=("evening", "morning"), default="evening")
    parser.add_argument("--generate", action="store_true", help="Generate local image but do not send")
    parser.add_argument("--send-to-test", action="store_true", help="Generate and send image. Defaults to CHANNEL_ID_TEST unless --chat-id is provided")
    parser.add_argument("--chat-id", default="", help="Explicit chat id for --send-to-test")
    parser.add_argument("--caption", default="", help="Caption for sent image")
    parser.add_argument("--history-namespace", choices=("prod", "test"), default="", help="Visual history namespace for duplicate checks")
    args = parser.parse_args()

    if args.message_file:
        message = Path(args.message_file).read_text(encoding="utf-8")
        payload = build_payload(message, "message_file", post_type=args.post_type)
    elif args.scenario:
        payload = build_fixture_payload(args.scenario, post_type=args.post_type)
    else:
        raise SystemExit("Provide --scenario or --message-file")

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

    print("\n===== FIXTURE_IMAGE_METADATA BEGIN =====\n")
    print(json.dumps(payload["metadata"], ensure_ascii=False, indent=2))
    print("\n===== FIXTURE_IMAGE_METADATA END =====\n")

    print("\n===== FIXTURE_IMAGE_CACHE_KEY BEGIN =====\n")
    print(payload["cache_key"])
    print("\n===== FIXTURE_IMAGE_CACHE_KEY END =====\n")

    print("\n===== FIXTURE_IMAGE_PROMPT BEGIN =====\n")
    print(payload["image_prompt"])
    print("\n===== FIXTURE_IMAGE_PROMPT END =====\n")

    if not (args.generate or args.send_to_test):
        print("Image generation skipped. Use --generate or --send-to-test.")
        return

    import imagegen  # imported only for explicit image mode

    namespace = args.history_namespace or ("test" if args.send_to_test else "prod")
    history_path = kld_visual_history_path(namespace)
    print(f"KLD_IMAGE_HISTORY_NAMESPACE: {namespace}")
    print(f"KLD_IMAGE_HISTORY_PATH: {history_path}")

    selected: tuple[dict[str, Any], str, Any] | None = None
    least_similar: tuple[dict[str, Any], str, Any] | None = None
    for attempt in range(3):
        if args.message_file:
            candidate = build_payload(message, "message_file", post_type=args.post_type, variation_attempt=attempt)
        else:
            candidate = build_fixture_payload(args.scenario, post_type=args.post_type, variation_attempt=attempt)

        img_path = imagegen.generate_kld_evening_image(
            prompt=candidate["image_prompt"],
            style_name=candidate["style_name"],
            seed=_seed_from_cache_key(candidate["cache_key"]),
        )
        print(f"Generated KLD image attempt={attempt}: {img_path}")
        metadata = candidate["metadata"]
        duplicate = evaluate_kld_visual_candidate(
            img_path,
            date_value=metadata["forecast_date"],
            target_date=metadata["target_date"],
            post_type=args.post_type,
            scene_family=metadata["scene_family"],
            composition=metadata["composition"],
            prompt_version=metadata["prompt_version"],
            history_path=history_path,
        )
        print(
            "KLD image duplicate check: "
            f"attempt={attempt} accepted={duplicate.accepted} reason={duplicate.reason} "
            f"scene={metadata['scene_family']} composition={metadata['composition']} "
            f"min_distance={duplicate.min_distance}"
        )
        if duplicate.accepted:
            selected = (candidate, img_path, duplicate)
            break
        if duplicate.reason != "exact_duplicate":
            if least_similar is None:
                least_similar = (candidate, img_path, duplicate)
            else:
                previous_distance = least_similar[2].min_distance
                current_distance = duplicate.min_distance
                if current_distance is not None and (previous_distance is None or current_distance > previous_distance):
                    least_similar = (candidate, img_path, duplicate)

    if selected is None and least_similar is not None:
        selected = least_similar
        print("WARNING: all KLD image candidates were near-duplicates; using least similar candidate.")
    if selected is None:
        raise SystemExit("KLD image generation produced only exact duplicate candidates; refusing to send.")

    payload, img_path, _duplicate = selected
    print(f"Selected KLD image: {img_path}")

    if args.send_to_test:
        default_suffix = args.scenario or "FORMAT_V2 message"
        if args.post_type == "morning":
            default_caption = "🧪 KLD morning image • FORMAT_V2 SceneCues"
        else:
            default_caption = f"🧪 KLD image • {default_suffix} • FORMAT_V2 SceneCues"
        caption = args.caption.strip() or default_caption
        asyncio.run(_send_photo(img_path, caption, chat_id_override=args.chat_id))
        metadata = payload["metadata"]
        entry = record_kld_visual_publication(
            date_value=metadata["forecast_date"],
            target_date=metadata["target_date"],
            post_type=args.post_type,
            image_path=img_path,
            scene_family=metadata["scene_family"],
            composition=metadata["composition"],
            prompt_version=metadata["prompt_version"],
            cache_key=payload["cache_key"],
            style_name=payload["style_name"],
            history_path=history_path,
        )
        print(f"Recorded KLD image history: sha256={entry['sha256']} scene={entry['scene_family']}")
    else:
        print("KLD image history not recorded: no successful Telegram send in this run.")


if __name__ == "__main__":
    main()
