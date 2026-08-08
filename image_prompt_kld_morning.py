#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prompt builder for KLD FORMAT_V2 morning weather images."""
from __future__ import annotations

import datetime as dt
import logging
import re
from typing import Tuple

from kld_visual_policy import (
    PROVIDER_NEGATIVE_GUARD,
    SCENE_NEUTRAL_PHOTO_CONTRACT,
    apply_summer_vegetation_guard,
    build_scene_contract,
)

logger = logging.getLogger(__name__)

_SCENIC_ONLY_GUARD = (
    "Final image: one clean unmarked photorealistic Kaliningrad-region scene in natural Baltic daylight; "
    "the selected scene family is authoritative and fills the frame; realistic weather, geography, vegetation "
    "and atmospheric perspective."
)

_PURE_SCENE_CUES = (
    "Pure full-frame photorealistic Kaliningrad-region photography; one coherent real place and one dominant "
    "scene family; realistic northern vegetation; realistic weather state; natural atmospheric perspective; "
    "editorial-free scenic composition."
)

_TRIGGER_RE = re.compile(
    r"\b("
    r"moon|moonlit|lunar|crescent|"
    r"night|evening|sunset|"
    r"boat|sail|sailboat|yacht|mast|"
    r"sup|paddleboard|"
    r"text|caption|label|logo|watermark|number|numbers|ui|"
    r"letter|letters|word|words|writing|title|headline|"
    r"typography|poster|layout|panel|panels|infographic|card|"
    r"vaye|vaybo|vaybometer"
    r")\b",
    re.IGNORECASE,
)

_SAFE_MORNING_CUES = (
    "Morning safety cues: neutral Baltic morning daylight; fresh Baltic morning light; "
    "left-side morning light, sun from the left side of frame; "
    "practical weather-for-the-day mood; scene geography follows the selected scene family."
)

_RAIN_MORNING_CUES = (
    "Morning rain adherence: overcast or mostly overcast Baltic morning sky; "
    "fresh Baltic morning light muted by clouds; diffuse left-side morning light through overcast cloud; "
    "wet or damp surfaces appropriate to the selected scene; muted northern grey-blue palette; "
    "cool natural color temperature; realistic rain-dark cloud cover; "
    "subdued practical wet-weather mood."
)

_CLOUDY_DRIZZLE_SAFE_BLOCK = (
    "Morning overcast scene: broad daylight overcast morning sky; soft pale cloud cover; "
    "left-side morning light through overcast cloud layers; "
    "natural surfaces and vegetation appropriate to the selected scene family; "
    "fresh practical morning weather mood."
)

_VISIBILITY_MORNING_CUES = {
    "dense_fog": (
        "Morning visibility adherence: dense humid fog; heavily reduced distant visibility; "
        "partially obscured distant landmarks; soft diffused daylight; muted contrast; moist atmospheric depth."
    ),
    "fog": (
        "Morning visibility adherence: humid regional fog; reduced distant visibility; "
        "softened distant landmarks; diffused neutral daylight; soft Baltic morning haze."
    ),
    "mist": (
        "Morning visibility adherence: humid morning mist; gentle atmospheric depth; "
        "softened distant clarity; fresh neutral Baltic daylight."
    ),
    "reduced_visibility": (
        "Morning visibility adherence: reduced distant clarity; softened distance; restrained contrast; "
        "neutral Baltic daylight without invented dense fog."
    ),
    "dust_haze": (
        "Morning visibility adherence: muted beige-grey dry atmospheric haze; dry suspended particles; "
        "reduced clarity; neutral Baltic daylight."
    ),
    "mixed_visibility": (
        "Morning visibility adherence: muted grey mixed atmospheric haze; reduced distant clarity; "
        "restrained humid softness; restrained polluted-air haze."
    ),
}


def _fallback_morning_prompt() -> str:
    return "\n".join(
        [
            "Create a practical morning weather photograph for the Kaliningrad region.",
            "Base scene: one coherent real Kaliningrad-region outdoor scene chosen for the stated weather.",
            "Light: soft low-angle morning light from the left side of frame and pale cloud layers.",
            "Mood: fresh Baltic morning air and practical weather-for-the-day mood.",
            _SAFE_MORNING_CUES,
            _PURE_SCENE_CUES,
        ]
    )


def _remove_trigger_lines(prompt: str) -> str:
    cleaned: list[str] = []
    for raw_line in (prompt or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith(("Must show:", "Must avoid:")):
            prefix, raw_items = line.split(":", 1)
            items = [item.strip().rstrip(".") for item in raw_items.split(";") if item.strip()]
            safe_items = [item for item in items if not _TRIGGER_RE.search(item)]
            if safe_items:
                cleaned.append(prefix + ": " + "; ".join(safe_items) + ".")
            continue
        if line.startswith("Activity cue:") and _TRIGGER_RE.search(line):
            cleaned.append("Activity cue: no foreground activity; natural scene scale only.")
            continue
        if _TRIGGER_RE.search(line):
            continue
        cleaned.append(line)
    return "\n".join(cleaned)


def _sanitize_morning_prompt(
    prompt: str,
    *,
    weather_main: str = "",
    visibility_condition: str = "clear",
) -> str:
    """Remove object/night/layout trigger words from final morning prompt.

    The result stays scene-neutral: geography is supplied later by the selected
    scene family instead of forcing beach+dunes+pines into every image.
    """
    cleaned = _remove_trigger_lines(prompt)
    parts: list[str] = []

    weather = (weather_main or "").strip().lower()
    visibility = (visibility_condition or "clear").strip().lower()
    visibility_cue = _VISIBILITY_MORNING_CUES.get(visibility)
    if visibility_cue:
        parts.append(visibility_cue)
    if weather == "rain":
        parts.append(_RAIN_MORNING_CUES)
    elif weather in {"cloudy", "drizzle"}:
        parts.append(_CLOUDY_DRIZZLE_SAFE_BLOCK)

    if cleaned:
        parts.append(cleaned)

    if weather != "rain" and not visibility_cue:
        parts.append(_SAFE_MORNING_CUES)
    parts.append(_PURE_SCENE_CUES)

    final_prompt = "\n".join(parts)
    if weather == "rain":
        final_prompt = re.sub(r"clear daylight sky;?\s*", "", final_prompt, flags=re.I)
        final_prompt = re.sub(r"bright sun(?:ny)?|golden[- ]hour|golden sunny", "", final_prompt, flags=re.I)
    final_prompt = _TRIGGER_RE.sub("", final_prompt)
    final_prompt = re.sub(r";\s*no\s*(?=[.;])", "", final_prompt, flags=re.I)
    final_prompt = re.sub(r"no\s*\.", "", final_prompt, flags=re.I)
    final_prompt = re.sub(r"[ \t]{2,}", " ", final_prompt)
    final_prompt = re.sub(r"\n{3,}", "\n\n", final_prompt).strip()
    return final_prompt + "\n" + _SCENIC_ONLY_GUARD


def _extract_message_date_key(text: str, fallback: dt.date | None = None) -> str:
    """Extract the forecast date without depending on the main visual pipeline."""
    value = str(text or "")
    match = re.search(r"\b(\d{2})[./-](\d{2})[./-](\d{4})\b", value)
    if match:
        return f"{match.group(3)}-{match.group(2)}-{match.group(1)}"
    match = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", value)
    if match:
        return match.group(0)
    return fallback.isoformat() if fallback else "undated"


def _replace_generic_controlled_composition(prompt: str, scene_contract: str) -> str:
    """Replace the legacy coastal framing line with the selected scene contract."""
    kept = [
        line
        for line in str(prompt or "").splitlines()
        if not line.strip().startswith("Controlled composition:")
    ]
    kept.append(scene_contract)
    return "\n".join(line for line in kept if line.strip())


def _finalize_morning_visual_policy(prompt: str, *, date_key: str, metadata: dict[str, str]) -> str:
    text = _replace_generic_controlled_composition(prompt, build_scene_contract(metadata))
    if SCENE_NEUTRAL_PHOTO_CONTRACT not in text:
        text += "\n" + SCENE_NEUTRAL_PHOTO_CONTRACT
    text = apply_summer_vegetation_guard(text, date_key)
    text += "\n" + PROVIDER_NEGATIVE_GUARD
    return text


def build_kld_morning_prompt(
    final_format_v2_message: str,
    *,
    post_type: str = "morning",
    variation_attempt: int = 0,
    visibility_context: object | None = None,
) -> Tuple[str, str]:
    """Build a deterministic morning prompt from the final FORMAT_V2 message."""
    date_key = _extract_message_date_key(final_format_v2_message, dt.date.today())
    try:
        from visual_context_kld import build_visual_context
        from visual_rules import apply_visual_rules, build_prompt_from_cues

        structured_visibility = visibility_context
        if structured_visibility is None:
            structured_visibility = getattr(final_format_v2_message, "visibility_context", None)
        ctx = build_visual_context(
            final_format_v2_message,
            post_type=post_type or "morning",
            visibility_context=structured_visibility,
        )
        cues = apply_visual_rules(ctx)
        prompt = build_prompt_from_cues(cues)
        prompt = _sanitize_morning_prompt(
            prompt,
            weather_main=getattr(ctx, "weather_main", ""),
            visibility_condition=getattr(ctx, "visibility_condition", "clear"),
        )
        from image_prompt_kld import (
            _format_v2_style_name,
            apply_kld_controlled_variety,
            kld_scene_metadata,
        )

        prompt = apply_kld_controlled_variety(
            prompt,
            ctx,
            date_key=date_key,
            post_type="morning",
            source_text=final_format_v2_message,
            variation_attempt=variation_attempt,
        )
        metadata = kld_scene_metadata(
            ctx,
            date_key=date_key,
            post_type="morning",
            source_text=final_format_v2_message,
            variation_attempt=variation_attempt,
        )
        prompt = _finalize_morning_visual_policy(prompt, date_key=date_key, metadata=metadata)
        style_name = _format_v2_style_name(
            ctx,
            date_key=date_key,
            post_type="morning",
            source_text=final_format_v2_message,
            variation_attempt=variation_attempt,
        )
        return prompt, "format_v2_scene_cues_morning_" + style_name.rsplit("_", 1)[-1]
    except Exception:
        logger.exception("KLD morning visual pipeline failed; using simple regional fallback")
        fallback = _sanitize_morning_prompt(_fallback_morning_prompt())
        fallback = apply_summer_vegetation_guard(fallback, date_key)
        fallback += "\n" + PROVIDER_NEGATIVE_GUARD
        return fallback, "format_v2_scene_cues_morning"


__all__ = ["build_kld_morning_prompt"]
