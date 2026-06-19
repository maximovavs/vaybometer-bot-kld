#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prompt builder for KLD FORMAT_V2 morning weather images."""
from __future__ import annotations

import logging
from typing import Tuple

logger = logging.getLogger(__name__)

_TEXT_RESTRICTIONS = (
    "Text restrictions: no text, no captions, no labels, no logos, "
    "no numbers, no UI, no watermarks."
)


def _fallback_morning_prompt() -> str:
    return "\n".join(
        [
            "Create a practical morning weather illustration for the Kaliningrad region.",
            "Base scene: Baltic coast near Kaliningrad in daylight, dunes, pines, promenade, sea horizon.",
            "Light: soft low-angle morning light.",
            "Mood: fresh Baltic morning air and practical weather-for-the-day mood.",
            "Must avoid: moon; sunset colors; night atmosphere; mystical evening mood.",
            _TEXT_RESTRICTIONS,
        ]
    )


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
        return prompt, "format_v2_scene_cues_morning"
    except Exception:
        logger.exception("KLD morning visual pipeline failed; using simple coastal fallback")
        return _fallback_morning_prompt(), "format_v2_scene_cues_morning"


__all__ = ["build_kld_morning_prompt"]
