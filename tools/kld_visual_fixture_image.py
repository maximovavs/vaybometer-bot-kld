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
from collections.abc import Mapping
import datetime as dt
import hashlib
import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any, Callable

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
from kld_informative_cover import (  # noqa: E402
    RENDERER_VERSION as LOCAL_COVER_RENDERER_VERSION,
    render_kld_informative_cover,
)
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


async def _send_photo(path: str, caption: str, *, chat_id_override: str = "") -> int | None:
    from telegram import Bot  # imported only for explicit send mode

    token = os.getenv("TELEGRAM_TOKEN_KLG", "").strip()
    chat_id_raw = (chat_id_override or os.getenv("CHANNEL_ID_TEST", "")).strip()
    if not token:
        raise ValueError("TELEGRAM_TOKEN_KLG is required for --send-to-test")
    if not chat_id_raw:
        raise ValueError("CHANNEL_ID_TEST or --chat-id is required for --send-to-test")

    with open(path, "rb") as f:
        msg = await Bot(token=token).send_photo(chat_id=_chat_id(chat_id_raw), photo=f, caption=caption)
    message_id = getattr(msg, "message_id", None)
    print(f"Sent KLD image, message_id={message_id if message_id is not None else '?'}")
    return int(message_id) if message_id is not None else None


def _load_visibility_context_file(path_value: str) -> Mapping[str, Any] | None:
    """Load an optional structured sidecar, falling back safely to message text."""
    raw_path = str(path_value or "").strip()
    if not raw_path:
        return None
    path = Path(raw_path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(f"WARNING: KLD visibility sidecar unavailable; using text-only fallback: {exc}")
        return None
    if not isinstance(payload, Mapping):
        print("WARNING: KLD visibility sidecar is not a JSON object; using text-only fallback")
        return None
    return dict(payload)


def build_payload(
    message: str,
    label: str,
    *,
    post_type: str = "evening",
    variation_attempt: int = 0,
    visibility_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    ctx = build_visual_context(
        message,
        post_type=post_type,
        visibility_context=visibility_context,
    )
    cues = apply_visual_rules(ctx)
    diagnostic_prompt = build_prompt_from_cues(cues)
    if post_type == "morning":
        image_prompt, style_name = build_kld_morning_prompt(
            message,
            post_type="morning",
            variation_attempt=variation_attempt,
            visibility_context=visibility_context,
        )
    else:
        image_prompt, style_name = build_kld_evening_prompt(
            dt.date(2026, 6, 19),
            marine_mood="",
            inland_mood="",
            final_format_v2_message=message,
            post_type="evening",
            variation_attempt=variation_attempt,
            visibility_context=visibility_context,
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


def _write_json(path_value: str | Path, payload: Mapping[str, Any]) -> None:
    path = Path(path_value)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _base_outcome(*, post_type: str) -> dict[str, Any]:
    return {
        "result": "failed_nonfatal",
        "backend": "none",
        "error_type": "",
        "error_message": "",
        "telegram_image_sent": False,
        "telegram_image_message_id": None,
        "history_recorded": False,
        "cover_attempted": False,
        "post_type": post_type,
        "provider_error": None,
        "dedup_results": [],
    }


def _error_payload(exc: Exception) -> dict[str, str]:
    return {"type": type(exc).__name__, "message": str(exc)}


def _duplicate_payload(duplicate: Any, *, attempt: int, backend: str) -> dict[str, Any]:
    return {
        "attempt": attempt,
        "backend": backend,
        "accepted": bool(getattr(duplicate, "accepted", False)),
        "reason": str(getattr(duplicate, "reason", "unknown")),
        "sha256": str(getattr(duplicate, "sha256", "")),
        "perceptual_hash": getattr(duplicate, "perceptual_hash", None),
        "min_distance": getattr(duplicate, "min_distance", None),
    }


def _caption(args: argparse.Namespace) -> str:
    default_suffix = args.scenario or "FORMAT_V2 message"
    if args.post_type == "morning":
        default_caption = "🧪 KLD morning image • FORMAT_V2 SceneCues"
    else:
        default_caption = f"🧪 KLD image • {default_suffix} • FORMAT_V2 SceneCues"
    return args.caption.strip() or default_caption


def _send_and_record(
    *,
    args: argparse.Namespace,
    outcome: dict[str, Any],
    backend: str,
    image_path: str,
    metadata: Mapping[str, Any],
    cache_key: str,
    style_name: str,
    history_path: Path,
    send_photo: Callable[..., int | None],
    record_publication: Callable[..., Mapping[str, Any]],
) -> dict[str, Any]:
    outcome["backend"] = backend
    outcome["image_path"] = image_path
    if not args.send_to_test:
        outcome["result"] = "generated"
        return outcome

    try:
        message_id = send_photo(image_path, _caption(args), chat_id_override=args.chat_id)
    except Exception as exc:
        error = _error_payload(exc)
        outcome.update(
            result="failed_nonfatal",
            error_type=error["type"],
            error_message=error["message"],
            telegram_image_sent=False,
            history_recorded=False,
        )
        print(f"WARNING: KLD Telegram image send failed: {error['type']}: {error['message']}")
        return outcome

    outcome["telegram_image_sent"] = True
    outcome["telegram_image_message_id"] = message_id
    outcome["result"] = "fallback_sent" if backend == "local_informative_cover" else "sent"
    try:
        entry = record_publication(
            date_value=str(metadata["forecast_date"]),
            target_date=str(metadata["target_date"]),
            post_type=args.post_type,
            image_path=image_path,
            scene_family=str(metadata["scene_family"]),
            composition=str(metadata["composition"]),
            prompt_version=str(metadata["prompt_version"]),
            cache_key=cache_key,
            style_name=style_name,
            history_path=history_path,
        )
    except Exception as exc:
        error = _error_payload(exc)
        outcome.update(
            error_type=error["type"],
            error_message=f"image sent but history failed: {error['message']}",
            history_recorded=False,
        )
        print(f"WARNING: KLD image sent but history was not recorded: {error['type']}: {error['message']}")
    else:
        outcome["history_recorded"] = True
        outcome["history_sha256"] = entry.get("sha256")
        print(f"Recorded KLD image history: sha256={entry.get('sha256')} scene={entry.get('scene_family')}")
    return outcome


def execute_image_delivery(
    *,
    args: argparse.Namespace,
    message: str,
    initial_payload: dict[str, Any],
    visibility_context: Mapping[str, Any] | None,
    history_path: Path,
    generate_image: Callable[..., str] | None = None,
    evaluate_candidate: Callable[..., Any] = evaluate_kld_visual_candidate,
    cover_renderer: Callable[..., Mapping[str, Any]] = render_kld_informative_cover,
    send_photo: Callable[..., int | None] | None = None,
    record_publication: Callable[..., Mapping[str, Any]] = record_kld_visual_publication,
) -> dict[str, Any]:
    """Attempt AI image, then a local factual cover, without fatal image-only exits."""
    if generate_image is None:
        import imagegen

        generate_image = imagegen.generate_kld_evening_image
    if send_photo is None:
        send_photo = lambda path, caption, chat_id_override="": asyncio.run(  # noqa: E731
            _send_photo(path, caption, chat_id_override=chat_id_override)
        )

    outcome = _base_outcome(post_type=args.post_type)
    selected: tuple[dict[str, Any], str, Any] | None = None
    least_similar: tuple[dict[str, Any], str, Any] | None = None
    fallback_reason = ""

    for attempt in range(3):
        candidate = (
            build_payload(
                message,
                "message_file",
                post_type=args.post_type,
                variation_attempt=attempt,
                visibility_context=visibility_context,
            )
            if args.message_file
            else build_fixture_payload(args.scenario, post_type=args.post_type, variation_attempt=attempt)
        )
        try:
            img_path = generate_image(
                prompt=candidate["image_prompt"],
                style_name=candidate["style_name"],
                seed=_seed_from_cache_key(candidate["cache_key"]),
            )
            print(f"Generated KLD image attempt={attempt}: {img_path}")
            metadata = candidate["metadata"]
            duplicate = evaluate_candidate(
                img_path,
                date_value=metadata["forecast_date"],
                target_date=metadata["target_date"],
                post_type=args.post_type,
                scene_family=metadata["scene_family"],
                composition=metadata["composition"],
                prompt_version=metadata["prompt_version"],
                history_path=history_path,
            )
        except Exception as exc:
            error = _error_payload(exc)
            outcome["provider_error"] = error
            outcome["error_type"] = error["type"]
            outcome["error_message"] = error["message"]
            fallback_reason = "provider_failure"
            print(f"WARNING: KLD AI image unavailable: {error['type']}: {error['message']}")
            print("::warning::KLD AI image failed; local informative cover will be attempted.")
            break

        dedup = _duplicate_payload(duplicate, attempt=attempt, backend="pollinations")
        outcome["dedup_results"].append(dedup)
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
        print("WARNING: all KLD AI candidates were near-duplicates; using least similar candidate.")
    if selected is not None:
        payload, img_path, _duplicate = selected
        print(f"Selected KLD image: {img_path}")
        return _send_and_record(
            args=args,
            outcome=outcome,
            backend="pollinations",
            image_path=img_path,
            metadata=payload["metadata"],
            cache_key=payload["cache_key"],
            style_name=payload["style_name"],
            history_path=history_path,
            send_photo=send_photo,
            record_publication=record_publication,
        )

    if not fallback_reason:
        fallback_reason = "exact_duplicate"
        outcome["error_type"] = "ExactDuplicateOnly"
        outcome["error_message"] = "all AI candidates matched exact visual history entries"
        print("WARNING: all KLD AI candidates were exact duplicates; trying local informative cover.")
        print("::warning::KLD AI image duplicate; local informative cover will be attempted.")

    outcome["cover_attempted"] = True
    cover_path = str(Path(args.cover_path))
    try:
        cover_metadata = dict(
            cover_renderer(
                message,
                post_type=args.post_type,
                visibility_context=visibility_context,
                output_path=cover_path,
            )
        )
        metadata = initial_payload["metadata"]
        cover_duplicate = evaluate_candidate(
            cover_path,
            date_value=metadata["forecast_date"],
            target_date=metadata["target_date"],
            post_type=args.post_type,
            scene_family="local_informative_cover",
            composition="branded_weather_card",
            prompt_version=LOCAL_COVER_RENDERER_VERSION,
            history_path=history_path,
        )
    except Exception as exc:
        error = _error_payload(exc)
        outcome.update(
            result="failed_nonfatal",
            backend="none",
            error_type=error["type"],
            error_message=error["message"],
            cover_error=error,
        )
        print(f"WARNING: KLD local informative cover failed: {error['type']}: {error['message']}")
        return outcome

    outcome["cover_metadata"] = cover_metadata
    outcome["fallback_reason"] = fallback_reason
    outcome["dedup_results"].append(_duplicate_payload(cover_duplicate, attempt=0, backend="local_informative_cover"))
    if str(getattr(cover_duplicate, "reason", "")) == "exact_duplicate":
        outcome.update(
            result="skipped_duplicate",
            backend="local_informative_cover",
            telegram_image_sent=False,
            history_recorded=False,
        )
        print("WARNING: KLD local informative cover is an exact duplicate; continuing without image.")
        return outcome

    cover_history_metadata = {
        "forecast_date": metadata["forecast_date"],
        "target_date": metadata["target_date"],
        "scene_family": "local_informative_cover",
        "composition": "branded_weather_card",
        "prompt_version": LOCAL_COVER_RENDERER_VERSION,
    }
    print(f"Using KLD local informative cover: {cover_path}")
    return _send_and_record(
        args=args,
        outcome=outcome,
        backend="local_informative_cover",
        image_path=cover_path,
        metadata=cover_history_metadata,
        cache_key=f"{initial_payload['cache_key']};renderer={LOCAL_COVER_RENDERER_VERSION}",
        style_name=LOCAL_COVER_RENDERER_VERSION,
        history_path=history_path,
        send_photo=send_photo,
        record_publication=record_publication,
    )


def _print_payload(payload: Mapping[str, Any]) -> None:
    print("\n===== FIXTURE_MESSAGE BEGIN =====\n")
    print(payload["message"])
    print("===== FIXTURE_MESSAGE END =====\n")
    print("\n===== FIXTURE_VISUAL_CONTEXT BEGIN =====\n")
    print(json.dumps(payload["context"].__dict__, ensure_ascii=False, indent=2))
    print("\n===== FIXTURE_VISUAL_CONTEXT END =====\n")
    print("\n===== FIXTURE_VISUAL_CUES BEGIN =====\n")
    print(to_json(payload["cues"]))
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build/send KLD visual image")
    parser.add_argument("--scenario", choices=sorted(FIXTURES), default="")
    parser.add_argument("--message-file", default="", help="Use an already-built FORMAT_V2 message from file instead of fixture")
    parser.add_argument(
        "--visibility-context-file",
        default="",
        help="Optional JSON sidecar written by safe_test_post.py.",
    )
    parser.add_argument("--post-type", choices=("evening", "morning"), default="evening")
    parser.add_argument("--generate", action="store_true", help="Generate local image but do not send")
    parser.add_argument("--send-to-test", action="store_true", help="Generate and send image. Defaults to CHANNEL_ID_TEST unless --chat-id is provided")
    parser.add_argument("--chat-id", default="", help="Explicit chat id for --send-to-test")
    parser.add_argument("--caption", default="", help="Caption for sent image")
    parser.add_argument("--history-namespace", choices=("prod", "test"), default="", help="Visual history namespace for duplicate checks")
    parser.add_argument("--result-file", default="image_result.json", help="Structured image outcome JSON")
    parser.add_argument("--prompt-metadata-file", default="image_prompt_metadata.json", help="Prompt/cover metadata JSON")
    parser.add_argument("--cover-path", default="outputs/kld_local_informative_cover.png", help="Local fallback PNG path")
    args = parser.parse_args(argv)

    visibility_context = _load_visibility_context_file(args.visibility_context_file)
    outcome = _base_outcome(post_type=args.post_type)
    try:
        if args.message_file:
            message = Path(args.message_file).read_text(encoding="utf-8")
            payload = build_payload(
                message,
                "message_file",
                post_type=args.post_type,
                visibility_context=visibility_context,
            )
        elif args.scenario:
            message = FIXTURES[args.scenario]
            payload = build_fixture_payload(args.scenario, post_type=args.post_type)
        else:
            raise ValueError("Provide --scenario or --message-file")
    except Exception as exc:
        error = _error_payload(exc)
        outcome.update(result="fatal_input", error_type=error["type"], error_message=error["message"])
        _write_json(args.result_file, outcome)
        print(f"ERROR: KLD image input is invalid: {error['type']}: {error['message']}")
        return 2

    _print_payload(payload)
    prompt_metadata: dict[str, Any] = {
        "post_type": args.post_type,
        "style_name": payload["style_name"],
        "cache_key": payload["cache_key"],
        "metadata": payload["metadata"],
        "prompt_sha256": hashlib.sha256(payload["image_prompt"].encode("utf-8")).hexdigest(),
        "visibility_context": dict(visibility_context or {}),
        "local_cover_renderer": LOCAL_COVER_RENDERER_VERSION,
    }
    _write_json(args.prompt_metadata_file, prompt_metadata)

    if not (args.generate or args.send_to_test):
        print("Image generation skipped. Use --generate or --send-to-test.")
        outcome["result"] = "not_requested"
        _write_json(args.result_file, outcome)
        return 0

    namespace = args.history_namespace or ("test" if args.send_to_test else "prod")
    history_path = kld_visual_history_path(namespace)
    print(f"KLD_IMAGE_HISTORY_NAMESPACE: {namespace}")
    print(f"KLD_IMAGE_HISTORY_PATH: {history_path}")
    try:
        outcome = execute_image_delivery(
            args=args,
            message=message,
            initial_payload=payload,
            visibility_context=visibility_context,
            history_path=history_path,
        )
    except Exception as exc:
        traceback.print_exc()
        error = _error_payload(exc)
        outcome.update(
            result="failed_nonfatal",
            error_type=error["type"],
            error_message=error["message"],
            telegram_image_sent=False,
            history_recorded=False,
        )
        _write_json(args.result_file, outcome)
        return 1

    _write_json(args.result_file, outcome)
    prompt_metadata["image_outcome"] = outcome
    _write_json(args.prompt_metadata_file, prompt_metadata)
    print("KLD_IMAGE_RESULT:", json.dumps(outcome, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
