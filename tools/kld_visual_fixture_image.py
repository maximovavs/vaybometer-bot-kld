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
    extract_kld_cover_facts,
    render_kld_informative_cover,
    validate_kld_cover_semantics,
)
import daily_ai_presentation as ai_presentation  # noqa: E402
from kld_visual_dedup import (  # noqa: E402
    evaluate_kld_visual_candidate,
    kld_visual_history_path,
    load_kld_visual_history,
    record_kld_visual_publication,
)
from kld_visual_policy import (  # noqa: E402
    KLD_VISUAL_POLICY_VERSION,
    apply_weather_scene_route,
    build_stable_horde_prompt_parts,
    finalize_kld_provider_prompt,
    scene_macro_family,
    scene_policy_rejection,
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
    metadata = dict(
        kld_scene_metadata(
            ctx,
            date_key=date_key,
            post_type=post_type,
            source_text=message,
            variation_attempt=variation_attempt,
        )
    )
    metadata["prompt_version"] = (
        f"{metadata.get('prompt_version', 'unknown')}+{KLD_VISUAL_POLICY_VERSION}"
    )
    image_prompt = finalize_kld_provider_prompt(
        image_prompt,
        metadata=metadata,
        date_key=date_key,
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
        "visual_policy_version": KLD_VISUAL_POLICY_VERSION,
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
        "candidate_pool": [],
        "dedup_results": [],
        "terminal_local_relaxation": {
            "used": False,
            "reason": "not_considered",
            "attempt_count": 0,
        },
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


def _normalized_date(value: object) -> str:
    raw = str(value or "").strip()[:10]
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return dt.datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return ""


def _duplicate_payload(duplicate: Any, *, attempt: int, backend: str) -> dict[str, Any]:
    matched_entry = getattr(duplicate, "matched_entry", None)
    matched_target_date = ""
    if isinstance(matched_entry, Mapping):
        matched_target_date = _normalized_date(
            matched_entry.get("target_date") or matched_entry.get("date")
        )
    return {
        "attempt": attempt,
        "backend": backend,
        "accepted": bool(getattr(duplicate, "accepted", False)),
        "reason": str(getattr(duplicate, "reason", "unknown")),
        "sha256": str(getattr(duplicate, "sha256", "")),
        "perceptual_hash": getattr(duplicate, "perceptual_hash", None),
        "min_distance": getattr(duplicate, "min_distance", None),
        "matched_target_date": matched_target_date,
    }


def _scene_aware_evening_caption(scene_family: str) -> str:
    macro = scene_macro_family(scene_family)
    if macro in {"open_beach_dunes", "cliff_overlook"}:
        return "Визуальный вайб завтрашнего вечера над Балтикой 🌊"
    if macro == "promenade_urban":
        return "Визуальный вайб завтрашнего вечера у воды 🌆"
    if macro == "breakwater_harbour":
        return "Визуальный вайб завтрашнего вечера у Балтики 🌊"
    if macro == "lagoon":
        return "Визуальный вайб завтрашнего вечера у залива 🌊"
    if macro == "forest_road":
        return "Визуальный вайб завтрашнего вечера на побережье 🌲"
    if macro == "local_cover":
        return "Погодный вайб Калининградской области на завтра 🌦"
    return "Визуальный вайб завтрашнего вечера в Калининградской области 🌆"


def _caption(args: argparse.Namespace, metadata: Mapping[str, Any]) -> str:
    explicit = str(getattr(args, "caption", "") or "").strip()
    if explicit:
        return explicit
    if args.post_type == "morning":
        return "🧪 KLD morning image • FORMAT_V2 SceneCues"
    prefix = str(getattr(args, "caption_prefix", "") or "").strip()
    return (prefix + " " if prefix else "") + _scene_aware_evening_caption(
        str(metadata.get("scene_family") or "")
    )

def _sha256_file(path: str | Path) -> str:
    image_path = Path(path)
    if not image_path.exists():
        return ""
    digest = hashlib.sha256()
    with image_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
    history_image_path: str | None = None,
    presentation_metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    outcome["backend"] = backend
    source_image_path = str(history_image_path or image_path)
    outcome["image_path"] = image_path
    outcome["source_image_path"] = source_image_path
    outcome["published_image_path"] = image_path
    outcome["source_sha256"] = _sha256_file(source_image_path)
    outcome["published_sha256"] = _sha256_file(image_path)
    if presentation_metadata:
        outcome["presentation_version"] = str(
            presentation_metadata.get("presentation_version") or ""
        )
        outcome["presentation_metadata"] = dict(presentation_metadata)
    outcome["selected_scene_family"] = str(metadata.get("scene_family") or "")
    outcome["selected_composition"] = str(metadata.get("composition") or "")
    outcome["selected_cache_key"] = cache_key
    if not args.send_to_test:
        outcome["result"] = "generated"
        return outcome

    try:
        message_id = send_photo(image_path, _caption(args, metadata), chat_id_override=args.chat_id)
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
            image_path=source_image_path,
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


def _select_candidate_attempts(
    candidate_metadata: list[tuple[int, Mapping[str, Any]]],
    *,
    blocked_scenes: list[str],
    blocked_compositions: list[str],
    count: int,
    policy_history: list[Mapping[str, Any]] | None = None,
) -> tuple[list[int], list[str]]:
    """Select strict candidates, relaxing only oldest compositions on full exhaustion."""

    def select(
        effective_blocked_compositions: set[str],
        *,
        require_hard_policy: bool = False,
    ) -> list[int]:
        selected: list[int] = []
        used_scenes: set[str] = set()
        used_compositions: set[str] = set()
        for variation_attempt, metadata in candidate_metadata:
            scene = str(metadata["scene_family"])
            composition = str(metadata["composition"])
            if scene in used_scenes or composition in used_compositions:
                continue
            if scene in blocked_scenes or composition in effective_blocked_compositions:
                continue
            if require_hard_policy and policy_history is not None:
                policy_reason, _ = scene_policy_rejection(
                    policy_history,
                    scene_family=scene,
                    composition="",
                )
                if policy_reason in {"scene_cooldown", "scene_macro_cooldown"}:
                    continue
            selected.append(variation_attempt)
            used_scenes.add(scene)
            used_compositions.add(composition)
            if len(selected) >= count:
                break
        return selected

    selected = select(set(blocked_compositions))
    if selected or not blocked_compositions:
        return selected, []

    # _recent_visual_cooldown returns newest -> oldest. Relax the oldest
    # composition first. Continue only when the current depth yields no
    # candidate that can pass the hard scene/macro policy.
    for relaxation_depth in range(1, len(blocked_compositions) + 1):
        effective = set(blocked_compositions[:-relaxation_depth])
        selected = select(effective, require_hard_policy=True)
        if selected:
            return selected, list(blocked_compositions[-relaxation_depth:])
    return [], []


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

    candidate_metadata: list[tuple[int, dict[str, Any]]] = []
    for index in range(48):
        metadata = dict(
            kld_scene_metadata(
                context,
                date_key=date_key,
                post_type=args.post_type,
                source_text=message,
                variation_attempt=index,
            )
        )
        apply_weather_scene_route(metadata)
        candidate_metadata.append((index, metadata))

    selected_attempts, relaxed_compositions = _select_candidate_attempts(
        candidate_metadata,
        blocked_scenes=blocked_scenes,
        blocked_compositions=blocked_compositions,
        count=count,
        policy_history=load_kld_visual_history(history_path),
    )
    relaxation_depth = len(relaxed_compositions)
    candidates: list[dict[str, Any]] = []
    for attempt in selected_attempts[:count]:
        candidate = payload_for(attempt)
        composition = str(candidate["metadata"].get("composition") or "")
        relaxed = composition in relaxed_compositions
        candidate["composition_cooldown_relaxed"] = relaxed
        if relaxed:
            candidate["composition_cooldown_relaxed_value"] = composition
            candidate["composition_cooldown_relaxation_depth"] = relaxation_depth
        candidates.append(candidate)

    return candidates, blocked_scenes, blocked_compositions


def _provider_attempt_payload(
    *,
    backend: str,
    candidate: Mapping[str, Any],
    result: str,
    diagnostics: Mapping[str, Any] | None = None,
    error: Mapping[str, Any] | None = None,
    prompt_contract: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    metadata = candidate["metadata"]
    diagnostics = dict(diagnostics or {})
    error = dict(error or {})
    http_attempts = list(diagnostics.get("attempts") or error.get("attempts") or [])
    payload = {
        "backend": backend,
        "variation_attempt": int(candidate.get("variation_attempt") or 0),
        "scene_family": str(metadata.get("scene_family") or ""),
        "composition": str(metadata.get("composition") or ""),
        "cache_key": str(candidate.get("cache_key") or ""),
        "composition_cooldown_relaxed": bool(candidate.get("composition_cooldown_relaxed")),
        "composition_cooldown_relaxed_value": str(candidate.get("composition_cooldown_relaxed_value") or ""),
        "composition_cooldown_relaxation_depth": int(candidate.get("composition_cooldown_relaxation_depth") or 0),
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
    if prompt_contract:
        payload["prompt_contract"] = dict(prompt_contract)
    return payload


def _rejection_fallback_reason(reasons: list[str]) -> str:
    if any(str(reason).startswith("content_guard:") for reason in reasons):
        return "semantic_mismatch"
    if any(reason in {"scene_cooldown", "scene_macro_cooldown"} for reason in reasons):
        return "scene_policy_rejected"
    if "near_duplicate" in reasons:
        return "near_duplicate"
    if "exact_duplicate" in reasons:
        return "exact_duplicate"
    return "candidate_rejected"


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
    presentation_renderer: Callable[..., Mapping[str, Any]] = ai_presentation.render_branded_ai_presentation,
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
        count=3,
    )
    outcome["scene_cooldown"] = scene_cooldown
    outcome["composition_cooldown"] = composition_cooldown
    outcome["candidate_pool"] = [
        {
            "variation_attempt": int(candidate.get("variation_attempt") or 0),
            "scene_family": str(candidate["metadata"].get("scene_family") or ""),
            "composition": str(candidate["metadata"].get("composition") or ""),
            "cache_key": str(candidate.get("cache_key") or ""),
            "composition_cooldown_relaxed": bool(candidate.get("composition_cooldown_relaxed")),
            "composition_cooldown_relaxed_value": str(candidate.get("composition_cooldown_relaxed_value") or ""),
            "composition_cooldown_relaxation_depth": int(candidate.get("composition_cooldown_relaxation_depth") or 0),
        }
        for candidate in candidates
    ]
    duplicate_reasons: list[str] = []
    provider_failed = False
    presentation_failed = False
    provider_failure_kinds: list[str] = []
    for provider_index, (backend, generator) in enumerate(providers):
        if candidates:
            offset = provider_index % len(candidates)
            provider_candidates = candidates[offset:] + candidates[:offset]
        else:
            provider_candidates = []
        for candidate in provider_candidates:
            metadata = candidate["metadata"]
            generation_kwargs: dict[str, Any] = {
                "prompt": candidate["image_prompt"],
                "style_name": candidate["style_name"],
                "seed": _seed_from_cache_key(candidate["cache_key"]),
            }
            prompt_contract: dict[str, Any] = {"profile": "full_provider_prompt"}
            if backend == "stable_horde":
                positive_prompt, negative_prompt = build_stable_horde_prompt_parts(metadata)
                generation_kwargs.update(
                    prompt=positive_prompt,
                    style_name="",
                    negative_prompt=negative_prompt,
                )
                prompt_contract = {
                    "profile": "kld_short_positive_negative",
                    "positive_words": len(positive_prompt.split()),
                    "negative_words": len(negative_prompt.split()),
                    "negative_separated": True,
                }
            try:
                img_path = generator(**generation_kwargs)
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
                    prompt_contract=prompt_contract,
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
                prompt_contract=prompt_contract,
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
                    allow_composition_cooldown_relaxation=bool(candidate.get("composition_cooldown_relaxed")),
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
                print(f"Selected KLD raw image: backend={backend} path={img_path}")
                try:
                    factual_metadata = extract_kld_cover_facts(
                        message,
                        post_type=args.post_type,
                        visibility_context=visibility_context,
                    )
                    presentation_validation = dict(
                        validate_cover(
                            message,
                            factual_metadata,
                            post_type=args.post_type,
                            visibility_context=visibility_context,
                        )
                    )
                    if not presentation_validation.get("valid"):
                        raise RuntimeError(
                            "KLD AI presentation facts failed deterministic semantic validation: "
                            + "; ".join(str(item) for item in presentation_validation.get("errors") or [])
                        )
                    source_path = Path(img_path)
                    presentation_path = source_path.with_name(source_path.stem + ".branded.png")
                    presentation_metadata = dict(
                        presentation_renderer(
                            source_path,
                            headline=str(factual_metadata.get("title") or ""),
                            date_value=str(factual_metadata.get("date") or metadata["target_date"]),
                            facts=list(factual_metadata.get("facts") or [])[:3],
                            branding="VAYBOMETER · KLD",
                            output_path=presentation_path,
                        )
                    )
                    outcome["presentation_validation"] = presentation_validation
                except Exception as exc:
                    presentation_failed = True
                    error = _error_payload(exc)
                    outcome["presentation_error"] = error
                    outcome["error_type"] = error["type"]
                    outcome["error_message"] = error["message"]
                    outcome["provider_attempts"][-1]["presentation_error_type"] = error["type"]
                    outcome["provider_attempts"][-1]["presentation_error_message"] = error["message"]
                    print(
                        "WARNING: KLD accepted raw image presentation failed; "
                        f"validated local cover will be attempted: {error['type']}: {error['message']}"
                    )
                    break
                publication_path = str(presentation_metadata["path"])
                print(
                    "Selected KLD branded presentation: "
                    f"backend={backend} raw={img_path} published={publication_path}"
                )
                return _send_and_record(
                    args=args,
                    outcome=outcome,
                    backend=backend,
                    image_path=publication_path,
                    history_image_path=img_path,
                    presentation_metadata=presentation_metadata,
                    metadata=metadata,
                    cache_key=candidate["cache_key"],
                    style_name=candidate["style_name"],
                    history_path=history_path,
                    send_photo=send_photo,
                    record_publication=record_publication,
                )
            duplicate_reasons.append(str(duplicate.reason))
            # Hard rejection: a near duplicate, semantic mismatch, or scene
            # policy violation is never promoted merely because it is the least
            # similar of the rejected candidates.
        if presentation_failed:
            break

    if presentation_failed:
        fallback_reason = "presentation_failure"
    elif provider_failed and duplicate_reasons:
        fallback_reason = "provider_failure_after_rejection"
    elif provider_failed:
        fallback_reason = (
            "invalid_image"
            if provider_failure_kinds and all(kind == "invalid_image" for kind in provider_failure_kinds)
            else "provider_failure"
        )
    else:
        fallback_reason = _rejection_fallback_reason(duplicate_reasons)
    outcome["fallback_reason"] = fallback_reason
    if duplicate_reasons and not provider_failed:
        outcome["error_type"] = "CandidateRejected"
        outcome["error_message"] = (
            "all generated candidates were rejected: " + ", ".join(sorted(set(duplicate_reasons)))
        )
    print(
        "::warning::KLD AI image unavailable after provider/dedup ladder; "
        "validated local informative cover will be attempted."
    )

    outcome["cover_attempted"] = True
    cover_path = str(Path(args.cover_path))
    metadata = initial_payload["metadata"]
    duplicate_cover_reasons = {"exact_duplicate", "near_duplicate"}

    def _render_validate_dedup_cover(
        *,
        requested_asset_id: str | None,
        attempt: int,
    ) -> tuple[dict[str, Any], dict[str, Any], Any, dict[str, Any]]:
        render_kwargs: dict[str, Any] = {
            "post_type": args.post_type,
            "visibility_context": visibility_context,
            "output_path": cover_path,
        }
        if requested_asset_id is not None:
            render_kwargs["curated_asset_id"] = requested_asset_id
        cover_metadata = dict(cover_renderer(message, **render_kwargs))
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
            return cover_metadata, cover_validation, None, {}
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
        cover_dedup = _duplicate_payload(
            cover_duplicate,
            attempt=attempt,
            backend="local_informative_cover",
        )
        outcome["dedup_results"].append(cover_dedup)
        outcome["dedup_reason"] = cover_dedup["reason"]
        outcome["dedup_distance"] = cover_dedup["min_distance"]
        outcome["selected_scene_family"] = "local_informative_cover"
        outcome["selected_composition"] = "branded_weather_card"
        outcome["selected_cache_key"] = (
            f"{initial_payload['cache_key']};renderer={LOCAL_COVER_RENDERER_VERSION}"
        )
        return cover_metadata, cover_validation, cover_duplicate, cover_dedup

    def _publish_local_cover() -> dict[str, Any]:
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

    try:
        cover_metadata, cover_validation, cover_duplicate, cover_dedup = _render_validate_dedup_cover(
            requested_asset_id=None,
            attempt=0,
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

    first_reason = str(getattr(cover_duplicate, "reason", ""))
    if first_reason not in duplicate_cover_reasons:
        return _publish_local_cover()

    selected_asset = str(cover_metadata.get("curated_asset_id") or "")
    pool = tuple(str(item) for item in (cover_metadata.get("curated_pool") or []) if str(item))
    candidate_order = tuple(
        str(item) for item in (cover_metadata.get("curated_candidates") or []) if str(item)
    )
    if (
        selected_asset
        and candidate_order
        and candidate_order[0] == selected_asset
        and set(candidate_order).issubset(set(pool))
    ):
        remaining_assets = [asset_id for asset_id in candidate_order[1:] if asset_id in pool]
    elif selected_asset and selected_asset in pool:
        remaining_assets = [asset_id for asset_id in pool if asset_id != selected_asset]
    else:
        remaining_assets = []

    for cover_attempt, alternate_asset in enumerate(remaining_assets, start=1):
        try:
            (
                alternate_metadata,
                alternate_validation,
                alternate_duplicate,
                alternate_dedup,
            ) = _render_validate_dedup_cover(
                requested_asset_id=alternate_asset,
                attempt=cover_attempt,
            )
        except Exception as exc:
            error = _error_payload(exc)
            print(
                "WARNING: KLD alternate local informative cover failed: "
                f"asset={alternate_asset} {error['type']}: {error['message']}"
            )
            continue
        if not alternate_validation.get("valid"):
            print(
                "WARNING: KLD alternate local informative cover failed semantic validation: "
                f"asset={alternate_asset}; "
                + "; ".join(str(item) for item in alternate_validation.get("errors") or [])
            )
            continue
        alternate_reason = str(getattr(alternate_duplicate, "reason", ""))
        if alternate_reason in duplicate_cover_reasons:
            print(
                "WARNING: KLD alternate local informative cover is a duplicate "
                f"asset={alternate_asset} "
                f"({alternate_dedup['reason']}, distance={alternate_dedup['min_distance']}); "
                "trying next bounded variant."
            )
            continue
        if not bool(getattr(alternate_duplicate, "accepted", False)):
            print(
                "WARNING: KLD alternate local informative cover was rejected "
                f"asset={alternate_asset} reason={alternate_reason}; trying next bounded variant."
            )
            continue
        outcome["cover_metadata"] = alternate_metadata
        return _publish_local_cover()

    local_dedup_results = [
        item
        for item in outcome["dedup_results"]
        if item.get("backend") == "local_informative_cover"
    ]
    expected_attempts = 1 + len(remaining_assets)
    current_target_date = _normalized_date(metadata.get("target_date"))
    final_cover_metadata = outcome.get("cover_metadata")
    if not isinstance(final_cover_metadata, Mapping):
        final_cover_metadata = {}
    final_cover_validation = outcome.get("cover_validation")
    if not isinstance(final_cover_validation, Mapping):
        final_cover_validation = {}
    final_cover_date = _normalized_date(final_cover_metadata.get("date"))
    all_near_duplicates = bool(local_dedup_results) and all(
        item.get("reason") == "near_duplicate"
        for item in local_dedup_results
    )
    all_matches_are_older_targets = bool(local_dedup_results) and all(
        item.get("matched_target_date")
        and item.get("matched_target_date") != current_target_date
        for item in local_dedup_results
    )
    terminal_local_eligible = bool(
        len(local_dedup_results) == expected_attempts
        and all_near_duplicates
        and all_matches_are_older_targets
        and final_cover_validation.get("valid") is True
        and current_target_date
        and final_cover_date == current_target_date
    )
    if terminal_local_eligible:
        outcome["dedup_reason"] = "near_duplicate_terminal_local_allowed"
        outcome["terminal_local_relaxation"] = {
            "used": True,
            "reason": "all_curated_near_duplicates_from_older_target_dates",
            "attempt_count": len(local_dedup_results),
            "target_date": current_target_date,
            "matched_target_date": local_dedup_results[-1].get("matched_target_date"),
            "curated_asset_id": final_cover_metadata.get("curated_asset_id"),
        }
        print(
            "WARNING: KLD terminal local-cover relaxation used after every eligible "
            "curated cover was a near-duplicate from an older target date; "
            f"attempts={len(local_dedup_results)} asset={final_cover_metadata.get('curated_asset_id')}"
        )
        return _publish_local_cover()

    blocker = "terminal_local_requirements_not_met"
    if any(item.get("reason") == "exact_duplicate" for item in local_dedup_results):
        blocker = "exact_duplicate_forbidden"
    elif any(
        item.get("matched_target_date") == current_target_date
        for item in local_dedup_results
        if item.get("matched_target_date")
    ):
        blocker = "same_target_date_forbidden"
    elif len(local_dedup_results) != expected_attempts:
        blocker = "not_all_curated_candidates_validly_checked"
    elif final_cover_validation.get("valid") is not True:
        blocker = "cover_validation_failed"
    elif not current_target_date or final_cover_date != current_target_date:
        blocker = "current_target_date_not_proven"
    elif not all_matches_are_older_targets:
        blocker = "older_target_match_not_proven"
    outcome["terminal_local_relaxation"] = {
        "used": False,
        "reason": blocker,
        "attempt_count": len(local_dedup_results),
        "target_date": current_target_date,
    }
    outcome.update(
        result="skipped_duplicate",
        backend="local_informative_cover",
        telegram_image_sent=False,
        history_recorded=False,
    )
    print(
        "WARNING: KLD local informative cover candidates exhausted after duplicate rejection; "
        f"attempts={expected_attempts}; terminal_relaxation={blocker}; continuing without image."
    )
    return outcome



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
    parser.add_argument("--caption", default="", help="Explicit caption override for sent image")
    parser.add_argument("--caption-prefix", default="", help="Optional prefix for the scene-aware evening caption")
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
        "visual_policy_version": KLD_VISUAL_POLICY_VERSION,
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
