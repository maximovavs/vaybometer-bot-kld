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
    "dunes, pines, clouds and daylight; pure scenic painting without graphic "
    "overlay elements."
)

_PURE_SCENE_CUES = (
    "Pure full-frame natural landscape painting; uninterrupted Baltic scenery; "
    "clean open sky, beach, dunes, pines and sea filling the whole image; "
    "calm editorial-free scenic composition."
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
    "Morning safety cues: clear daylight sky; fresh Baltic morning light; "
    "left-side morning light, sun from the left side of frame; "
    "empty Baltic shoreline; open sea horizon; natural wave texture only; "
    "quiet beach, dunes, pines, pale cloud layers; practical weather-for-the-day mood."
)

_CLOUDY_DRIZZLE_SAFE_BLOCK = (
    "Morning overcast scene: broad daylight overcast morning sky; soft pale cloud cover; "
    "left-side morning light through overcast cloud layers; "
    "empty Baltic shoreline; open water with natural wave texture only; "
    "quiet dunes and pines; fresh practical morning weather mood."
)


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


def _sanitize_morning_prompt(prompt: str, *, weather_main: str = "") -> str:
    """Remove object/night/layout trigger words from final morning prompt.

    Image generators often treat negative words as objects, so morning prompts use
    positive daylight scenic cues only.
    """
    cleaned = _remove_trigger_lines(prompt)
    parts: list[str] = []

    weather = (weather_main or "").strip().lower()
    if weather in {"cloudy", "drizzle"}:
        parts.append(_CLOUDY_DRIZZLE_SAFE_BLOCK)

    if cleaned:
        parts.append(cleaned)

    parts.append(_SAFE_MORNING_CUES)
    parts.append(_PURE_SCENE_CUES)

    final_prompt = "\n".join(parts)
    # Second-pass guard for compound or unexpected trigger remnants.
    final_prompt = _TRIGGER_RE.sub("", final_prompt)
    final_prompt = re.sub(r"[ \t]{2,}", " ", final_prompt)
    final_prompt = re.sub(r"\n{3,}", "\n\n", final_prompt).strip()
    return final_prompt + "\n" + _SCENIC_ONLY_GUARD


def build_kld_morning_prompt(
    final_format_v2_message: str,
    *,
    post_type: str = "morning",
) -> Tuple[str, str]:
    """Build a deterministic morning prompt from the final FORMAT_V2 message."""
    try:
        from visual_context_kld import build_visual_context
        from visual_rules import apply_visual_rules, build_prompt_from_cues

        ctx = build_visual_context(final_format_v2_message, post_type=post_type or "morning")
        cues = apply_visual_rules(ctx)
        prompt = build_prompt_from_cues(cues)
        prompt = _sanitize_morning_prompt(prompt, weather_main=getattr(ctx, "weather_main", ""))
        from image_prompt_kld import _extract_prompt_date, apply_kld_controlled_variety

        prompt = apply_kld_controlled_variety(
            prompt,
            ctx,
            date_key=_extract_prompt_date(final_format_v2_message, dt.date.today()),
            post_type="morning",
        )
        return prompt, "format_v2_scene_cues_morning"
    except Exception:
        logger.exception("KLD morning visual pipeline failed; using simple coastal fallback")
        return _sanitize_morning_prompt(_fallback_morning_prompt()), "format_v2_scene_cues_morning"


__all__ = ["build_kld_morning_prompt"]
