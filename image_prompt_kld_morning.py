#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prompt builder for KLD FORMAT_V2 morning weather images."""
from __future__ import annotations

import logging
import re
from typing import Tuple

logger = logging.getLogger(__name__)

_TEXT_RESTRICTIONS = (
    "Text restrictions: no text, no captions, no labels, no logos, "
    "no numbers, no UI, no watermarks."
)

_TRIGGER_RE = re.compile(
    r"\b("
    r"moon|moonlit|lunar|crescent|"
    r"night|evening|sunset|"
    r"boat|sail|sailboat|yacht|mast|"
    r"sup|paddleboard"
    r")\b",
    re.IGNORECASE,
)

_SAFE_MORNING_CUES = (
    "Morning safety cues: clear daylight sky; fresh Baltic morning light; "
    "empty Baltic shoreline; open sea horizon; natural wave texture only; "
    "quiet beach, dunes, pines, pale cloud layers; practical weather-for-the-day mood."
)

_CLOUDY_DRIZZLE_SAFE_BLOCK = (
    "Morning overcast scene: broad daylight overcast morning sky; soft pale cloud cover; "
    "empty Baltic shoreline; open water with natural wave texture only; "
    "quiet dunes and pines; fresh practical morning weather mood."
)


def _fallback_morning_prompt() -> str:
    return "\n".join(
        [
            "Create a practical morning weather illustration for the Kaliningrad region.",
            "Base scene: Baltic coast near Kaliningrad in daylight, dunes, pines, promenade, sea horizon.",
            "Light: soft low-angle morning light and pale cloud layers.",
            "Mood: fresh Baltic morning air and practical weather-for-the-day mood.",
            _SAFE_MORNING_CUES,
            _TEXT_RESTRICTIONS,
        ]
    )


def _remove_trigger_lines(prompt: str) -> str:
    cleaned: list[str] = []
    for raw_line in (prompt or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if _TRIGGER_RE.search(line):
            continue
        cleaned.append(line)
    return "\n".join(cleaned)


def _sanitize_morning_prompt(prompt: str, *, weather_main: str = "") -> str:
    """Remove object/night trigger words from final morning prompt.

    Image generators often treat negative words as objects, so morning prompts use
    positive daylight scene cues only.
    """
    cleaned = _remove_trigger_lines(prompt)
    parts: list[str] = []

    weather = (weather_main or "").strip().lower()
    if weather in {"cloudy", "drizzle"}:
        parts.append(_CLOUDY_DRIZZLE_SAFE_BLOCK)

    if cleaned:
        parts.append(cleaned)

    parts.append(_SAFE_MORNING_CUES)
    parts.append(_TEXT_RESTRICTIONS)

    final_prompt = "\n".join(parts)
    # Second-pass guard for compound or unexpected trigger remnants.
    final_prompt = _TRIGGER_RE.sub("", final_prompt)
    final_prompt = re.sub(r"[ \t]{2,}", " ", final_prompt)
    final_prompt = re.sub(r"\n{3,}", "\n\n", final_prompt).strip()
    return final_prompt


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
        return prompt, "format_v2_scene_cues_morning"
    except Exception:
        logger.exception("KLD morning visual pipeline failed; using simple coastal fallback")
        return _sanitize_morning_prompt(_fallback_morning_prompt()), "format_v2_scene_cues_morning"


__all__ = ["build_kld_morning_prompt"]
