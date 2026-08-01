#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prompt builder for KLD FORMAT_V2 morning weather images."""
from __future__ import annotations

import datetime as dt
import logging
import re
from typing import Tuple

logger = logging.getLogger(__name__)

_SCENIC_ONLY_GUARD = (
    "Final image: clean unmarked natural Baltic landscape only; open sky, sea, "
    "dunes, pines, clouds and daylight; pure photorealistic scenic photography without graphic "
    "overlay elements."
)

_PURE_SCENE_CUES = (
    "Pure full-frame photorealistic Baltic coastal photography; uninterrupted Baltic scenery; "
    "visible sky, beach, dunes, pines and sea filling the whole image; "
    "editorial-free scenic composition; realistic northern vegetation; realistic sea state; "
    "natural atmospheric perspective; no illustration; no digital painting; no poster."
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
    "empty Baltic shoreline; open sea horizon; natural wave texture only; "
    "quiet beach, dunes, pines, pale cloud layers; practical weather-for-the-day mood."
)

_RAIN_MORNING_CUES = (
    "Morning rain adherence: overcast or mostly overcast Baltic morning sky; "
    "fresh Baltic morning light muted by clouds; diffuse left-side morning light through overcast cloud; "
    "wet or damp sand and promenade surfaces; muted northern grey-blue palette; "
    "cool natural color temperature; realistic rain-dark cloud cover; "
    "subdued practical wet-weather mood."
)

_CLOUDY_DRIZZLE_SAFE_BLOCK = (
    "Morning overcast scene: broad daylight overcast morning sky; soft pale cloud cover; "
    "left-side morning light through overcast cloud layers; "
    "empty Baltic shoreline; open water with natural wave texture only; "
    "quiet dunes and pines; fresh practical morning weather mood."
)

_SUMMER_VEGETATION_MONTHS = frozenset({6, 7, 8})
_SUMMER_VEGETATION_CUE = (
    "Summer vegetation adherence: humid Baltic summer vegetation is lush and fresh; "
    "coastal grass and dune vegetation are visibly natural green across the foreground and midground; "
    "shrubs and pines are healthy green; pale beige tones belong to sand, not to living vegetation."
)

_VISIBILITY_MORNING_CUES = {
    "dense_fog": (
        "Morning visibility adherence: dense humid fog; heavily reduced distant visibility; "
        "partially obscured Baltic horizon; soft diffused daylight; muted contrast; moist atmospheric depth."
    ),
    "fog": (
        "Morning visibility adherence: humid coastal fog; reduced distant visibility; "
        "softened Baltic horizon; diffused neutral daylight; soft Baltic morning haze."
    ),
    "mist": (
        "Morning visibility adherence: humid morning mist; gentle atmospheric depth; "
        "softened distant clarity; fresh neutral Baltic daylight."
    ),
    "reduced_visibility": (
        "Morning visibility adherence: reduced distant clarity; softened horizon; restrained contrast; "
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
            "Create a practical morning weather illustration for the Kaliningrad region.",
            "Base scene: Baltic coast near Kaliningrad in daylight, dunes, pines, promenade, sea horizon.",
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
            cleaned.append("Activity cue: empty Baltic shoreline and natural sea surface; scale: none.")
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

    Image generators often treat negative words as objects, so morning prompts use
    positive daylight scenic cues only.
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
    # Second-pass guard for compound or unexpected trigger remnants.
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


def _apply_summer_vegetation_guard(prompt: str, date_key: str) -> str:
    """Keep June-August KLD vegetation green and moisture-rich.

    The Baltic/Kaliningrad summer scene should not drift toward a dry steppe or
    Mediterranean dune palette.  Keep this as a positive cue because image
    generators can turn negative prompt words into visible objects.
    """
    try:
        target_date = dt.date.fromisoformat(str(date_key)[:10])
    except Exception:
        return prompt
    if target_date.month not in _SUMMER_VEGETATION_MONTHS:
        return prompt
    if _SUMMER_VEGETATION_CUE in str(prompt or ""):
        return prompt
    return str(prompt or "").rstrip() + "\n" + _SUMMER_VEGETATION_CUE


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
        from image_prompt_kld import _format_v2_style_name, apply_kld_controlled_variety

        prompt = apply_kld_controlled_variety(
            prompt,
            ctx,
            date_key=date_key,
            post_type="morning",
            source_text=final_format_v2_message,
            variation_attempt=variation_attempt,
        )
        prompt = _apply_summer_vegetation_guard(prompt, date_key)
        style_name = _format_v2_style_name(
            ctx,
            date_key=date_key,
            post_type="morning",
            source_text=final_format_v2_message,
            variation_attempt=variation_attempt,
        )
        return prompt, "format_v2_scene_cues_morning_" + style_name.rsplit("_", 1)[-1]
    except Exception:
        logger.exception("KLD morning visual pipeline failed; using simple coastal fallback")
        fallback = _sanitize_morning_prompt(_fallback_morning_prompt())
        fallback = _apply_summer_vegetation_guard(fallback, date_key)
        return fallback, "format_v2_scene_cues_morning"


__all__ = ["build_kld_morning_prompt"]
