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
    validate_kld_cover_semantics,
)
from kld_visual_dedup import (  # noqa: E402
    evaluate_kld_visual_candidate,
    kld_visual_history_path,
    load_kld_visual_history,
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
        "cover_validation": None,
        "local_cover_published": False,
        "post_type": post_type,
        "provider_error": None,
        "provider_errors": [],
        "provider_attempts": [],
        "http_attempt_count": 0,
        "fallback_reason": "",
        "selected_scene_family": "",
        "selected_composition": "",
        "selected_cache_key": "",
        "dedup_reason": "",
        "dedup_distance": None,
        "scene_cooldown": [],
        "composition_cooldown": [],
        "dedup_results": [],
    }


def _error_payload(exc: Exception) -> dict[str, Any]:
    attempts = list(getattr(exc, "attempts", []) or [])
    return {
        "type": type(exc).__name__,
        "message": str(exc),
        "backend": str(getattr(exc, "backend", "") or ""),
        "reason": str(getattr(exc, "reason", "") or ""),
        "http_attempt_count": len(attempts),
        "attempts": attempts,
    }


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
    outcome["selected_scene_family"] = str(metadata.get("scene_family") or "")
    outcome["selected_composition"] = str(metadata.get("composition") or "")
    outcome["selected_cache_key"] = cache_key
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
    outcome["local_cover_published"] = backend == "local_informative_cover"
    outcome["result"] = "sent" if backend == "pollinations" else "fallback_sent"
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


def _recent_visual_cooldown(history_path: Path) -> tuple[list[str], list[str]]:
    scenes: list[str] = []
    compositions: list[str] = []
    for entry in reversed(load_kld_visual_history(history_path)):
        scene = str(entry.get("scene_family") or "")
        composition = str(entry.get("composition") or "")
        if scene and scene != "local_informative_cover" and scene not in scenes and len(scenes) < 3:
            scenes.append(scene)
        if composition and composition != "branded_weather_card" and composition not in compositions and len(compositions) < 4:
            compositions.append(composition)
        if len(scenes) >= 3 and len(compositions) >= 4:
            break
    return scenes, compositions


def _candidate_payloads(
    *,
    args: argparse.Namespace,
    message: str,
    visibility_context: Mapping[str, Any] | None,
    history_path: Path,
    count: int = 3,
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    blocked_scenes, blocked_compositions = _recent_visual_cooldown(history_path)

    def payload_for(variation_attempt: int) -> dict[str, Any]:
        if args.message_file:
            return build_payload(
                message,
                "message_file",
                post_type=args.post_type,
                variation_attempt=variation_attempt,
                visibility_context=visibility_context,
            )
        return build_fixture_payload(
            args.scenario,
            post_type=args.post_type,
            variation_attempt=variation_attempt,
        )

    context = build_visual_context(
        message,
        post_type=args.post_type,
        visibility_context=visibility_context,
    )
    date_key = _extract_prompt_date(message, dt.date(2026, 6, 19))
    candidate_metadata = [
        (
            index,
            kld_scene_metadata(
                context,
                date_key=date_key,
                post_type=args.post_type,
                source_text=message,
                variation_attempt=index,
            ),
        )
        for index in range(48)
    ]
    selected_attempts: list[int] = []
    used_scenes: set[str] = set()
    used_compositions: set[str] = set()
    for cooldown_mode in ("strict", "scene_only", "relaxed"):
        for variation_attempt, metadata in candidate_metadata:
            scene = str(metadata["scene_family"])
            composition = str(metadata["composition"])
            if scene in used_scenes or composition in used_compositions:
                continue
            if cooldown_mode in {"strict", "scene_only"} and scene in blocked_scenes:
                continue
            if cooldown_mode == "strict" and composition in blocked_compositions:
                continue
            selected_attempts.append(variation_attempt)
            used_scenes.add(scene)
            used_compositions.add(composition)
            if len(selected_attempts) >= count:
                return (
                    [payload_for(attempt) for attempt in selected_attempts],
                    blocked_scenes,
                    blocked_compositions,
                )
    return (
        [payload_for(attempt) for attempt in selected_attempts[:count]],
        blocked_scenes,
        blocked_compositions,
    )


def _provider_attempt_payload(
    *,
    backend: str,
    candidate: Mapping[str, Any],
    result: str,
    diagnostics: Mapping[str, Any] | None = None,
    error: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    metadata = candidate["metadata"]
    diagnostics = dict(diagnostics or {})
    error = dict(error or {})
    http_attempts = list(diagnostics.get("attempts") or error.get("attempts") or [])
    return {
        "backend": backend,
        "variation_attempt": int(candidate.get("variation_attempt") or 0),
        "scene_family": str(metadata.get("scene_family") or ""),
        "composition": str(metadata.get("composition") or ""),
        "cache_key": str(candidate.get("cache_key") or ""),
        "result": result,
        "exception_type": str(error.get("type") or diagnostics.get("exception_type") or ""),
        "error_message": str(error.get("message") or ""),
        "http_attempt_count": int(
            diagnostics.get("http_attempt_count")
            or error.get("http_attempt_count")
            or len(http_attempts)
        ),
        "http_attempts": http_attempts,
    }


def execute_image_delivery(
    *,
    args: argparse.Namespace,
    message: str,
    initial_payload: dict[str, Any],
    visibility_context: Mapping[str, Any] | None,
    history_path: Path,
    generate_image: Callable[..., str] | None = None,
    secondary_generate_image: Callable[..., str] | None = None,
    provider_diagnostics: Callable[[str], Mapping[str, Any]] | None = None,
    evaluate_candidate: Callable[..., Any] = evaluate_kld_visual_candidate,
    cover_renderer: Callable[..., Mapping[str, Any]] = render_kld_informative_cover,
    validate_cover: Callable[..., Mapping[str, Any]] = validate_kld_cover_semantics,
    send_photo: Callable[..., int | None] | None = None,
    record_publication: Callable[..., Mapping[str, Any]] = record_kld_visual_publication,
) -> dict[str, Any]:
    """Try two providers, then a validated factual cover, without fatal image-only exits."""
    if generate_image is None:
        import imagegen

        generate_image = imagegen.generate_kld_evening_image
        if imagegen.stable_horde_enabled():
            secondary_generate_image = imagegen.generate_kld_stable_horde_image
        provider_diagnostics = imagegen.get_generation_diagnostics
    if send_photo is None:
        send_photo = lambda path, caption, chat_id_override="": asyncio.run(  # noqa: E731
            _send_photo(path, caption, chat_id_override=chat_id_override)
        )

    outcome = _base_outcome(post_type=args.post_type)
    providers = [("pollinations", generate_image)]
    if secondary_generate_image is not None:
        providers.append(("stable_horde", secondary_generate_image))
    candidates, scene_cooldown, composition_cooldown = _candidate_payloads(
        args=args,
        message=message,
        visibility_context=visibility_context,
        history_path=history_path,
        count=3 * len(providers),
    )
    outcome["scene_cooldown"] = scene_cooldown
    outcome["composition_cooldown"] = composition_cooldown
    duplicate_reasons: list[str] = []
    provider_failed = False
    provider_failure_kinds: list[str] = []
    for provider_index, (backend, generator) in enumerate(providers):
        start = provider_index * 3
        provider_candidates = candidates[start : start + 3]
        for candidate in provider_candidates:
            metadata = candidate["metadata"]
            try:
                img_path = generator(
                    prompt=candidate["image_prompt"],
                    style_name=candidate["style_name"],
                    seed=_seed_from_cache_key(candidate["cache_key"]),
                )
            except Exception as exc:
                provider_failed = True
                error = _error_payload(exc)
                if not error["backend"]:
                    error["backend"] = backend
                outcome["provider_error"] = error
                outcome["provider_errors"].append(error)
                outcome["error_type"] = error["type"]
                outcome["error_message"] = error["message"]
                provider_failure_kinds.append(str(error.get("reason") or "provider_failure"))
                if backend == "pollinations":
                    outcome["fallback_reason"] = str(error.get("reason") or "provider_failure")
                attempt_payload = _provider_attempt_payload(
                    backend=backend,
                    candidate=candidate,
                    result="failed",
                    error=error,
                )
                outcome["provider_attempts"].append(attempt_payload)
                outcome["http_attempt_count"] += attempt_payload["http_attempt_count"]
                print(
                    "WARNING: KLD image provider unavailable: "
                    f"backend={backend} {error['type']}: {error['message']}"
                )
                break

            diagnostics = provider_diagnostics(backend) if provider_diagnostics else {}
            attempt_payload = _provider_attempt_payload(
                backend=backend,
                candidate=candidate,
                result="generated",
                diagnostics=diagnostics,
            )
            outcome["provider_attempts"].append(attempt_payload)
            outcome["http_attempt_count"] += attempt_payload["http_attempt_count"]
            print(
                "Generated KLD image: "
                f"backend={backend} variation={candidate['variation_attempt']} "
                f"scene={metadata['scene_family']} composition={metadata['composition']} path={img_path}"
            )
            try:
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
                provider_failed = True
                error = _error_payload(exc)
                error["backend"] = backend
                error["reason"] = "invalid_image"
                provider_failure_kinds.append("invalid_image")
                outcome["provider_error"] = error
                outcome["provider_errors"].append(error)
                outcome["error_type"] = error["type"]
                outcome["error_message"] = error["message"]
                outcome["provider_attempts"][-1]["result"] = "invalid_image"
                outcome["provider_attempts"][-1]["exception_type"] = error["type"]
                outcome["provider_attempts"][-1]["error_message"] = error["message"]
                print(f"WARNING: KLD generated image validation failed: {error['type']}: {error['message']}")
                break

            dedup = _duplicate_payload(
                duplicate,
                attempt=int(candidate["variation_attempt"]),
                backend=backend,
            )
            outcome["dedup_results"].append(dedup)
            outcome["dedup_reason"] = dedup["reason"]
            outcome["dedup_distance"] = dedup["min_distance"]
            outcome["provider_attempts"][-1]["dedup_reason"] = dedup["reason"]
            outcome["provider_attempts"][-1]["dedup_distance"] = dedup["min_distance"]
            print(
                "KLD image duplicate check: "
                f"backend={backend} variation={candidate['variation_attempt']} "
                f"accepted={duplicate.accepted} reason={duplicate.reason} "
                f"scene={metadata['scene_family']} composition={metadata['composition']} "
                f"min_distance={duplicate.min_distance}"
            )
            if duplicate.accepted:
                print(f"Selected KLD image: backend={backend} path={img_path}")
                return _send_and_record(
                    args=args,
                    outcome=outcome,
                    backend=backend,
                    image_path=img_path,
                    metadata=metadata,
                    cache_key=candidate["cache_key"],
                    style_name=candidate["style_name"],
                    history_path=history_path,
                    send_photo=send_photo,
                    record_publication=record_publication,
                )
            duplicate_reasons.append(str(duplicate.reason))
            # Hard rejection: a near duplicate is never promoted merely because
            # it is the least similar of the rejected candidates.

    if provider_failed and duplicate_reasons:
        fallback_reason = "provider_failure_after_duplicate"
    elif provider_failed:
        fallback_reason = (
            "invalid_image"
            if provider_failure_kinds and all(kind == "invalid_image" for kind in provider_failure_kinds)
            else "provider_failure"
        )
    elif "near_duplicate" in duplicate_reasons:
        fallback_reason = "near_duplicate"
    else:
        fallback_reason = "exact_duplicate"
    outcome["fallback_reason"] = fallback_reason
    if duplicate_reasons and not provider_failed:
        outcome["error_type"] = "DuplicateOnly"
        outcome["error_message"] = "all generated candidates matched visual history"
    print(
        "::warning::KLD AI image unavailable after provider/dedup ladder; "
        "validated local informative cover will be attempted."
    )

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
        outcome["cover_metadata"] = cover_metadata
        cover_validation = dict(
            validate_cover(
                message,
                cover_metadata,
                post_type=args.post_type,
                visibility_context=visibility_context,
            )
        )
        outcome["cover_validation"] = cover_validation
        if not cover_validation.get("valid"):
            outcome.update(
                result="failed_nonfatal",
                backend="none",
                error_type="InvalidLocalCover",
                error_message="; ".join(str(item) for item in cover_validation.get("errors") or []),
            )
            print(
                "WARNING: KLD local informative cover failed semantic validation; "
                "continuing without image: "
                + outcome["error_message"]
            )
            return outcome
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

    cover_dedup = _duplicate_payload(cover_duplicate, attempt=0, backend="local_informative_cover")
    outcome["dedup_results"].append(cover_dedup)
    outcome["dedup_reason"] = cover_dedup["reason"]
    outcome["dedup_distance"] = cover_dedup["min_distance"]
    outcome["selected_scene_family"] = "local_informative_cover"
    outcome["selected_composition"] = "branded_weather_card"
    outcome["selected_cache_key"] = f"{initial_payload['cache_key']};renderer={LOCAL_COVER_RENDERER_VERSION}"
    if str(getattr(cover_duplicate, "reason", "")) in {"exact_duplicate", "near_duplicate"}:
        outcome.update(
            result="skipped_duplicate",
            backend="local_informative_cover",
            telegram_image_sent=False,
            history_recorded=False,
        )
        print(
            "WARNING: KLD local informative cover is a duplicate "
            f"({cover_dedup['reason']}, distance={cover_dedup['min_distance']}); "
            "continuing without image."
        )
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
