#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prompt-only visual probe for KLD FORMAT_V2 weather posts.

Step 4a of the visual weather matrix implementation.

This script mirrors the safe FORMAT_V2 text-building path and prints:
- FORMAT_V2 message text (optional)
- VISUAL_CONTEXT
- VISUAL_CUES
- VISUAL_PROMPT

It does not send Telegram messages and does not generate images. It is intended
for safe workflow/log verification before enabling real image generation.
"""
from __future__ import annotations

import argparse
import os

import pendulum

from post_common import build_message
from post_safety import sanitize_post_text, validation_summary
from format_v2 import build_format_v2
from safe_test_post import (
    OTHER_CITIES_ALL,
    OTHER_LABEL,
    SEA_CITIES_ORDERED,
    SEA_LABEL,
    TZ_STR,
    _TodayPatch,
    _apply_astro_cleanup,
    _apply_compact,
    _apply_confidence_polish,
    _apply_format_v2_test_polish,
    _apply_score_conclusion,
    _env_on,
    _inject_evening_score,
    _inject_morning_best_window,
    _inject_morning_feels,
    _inject_morning_score,
    _inject_morning_smart_plan,
    _inject_sensor_line,
    _insert_main_nuance,
)
from visual_context_kld import build_visual_context, to_json as context_to_json
from visual_rules import apply_visual_rules, build_prompt_from_cues, to_json as cues_to_json


def _prepare_env(mode: str, use_format_v2: bool) -> None:
    os.environ["POST_MODE"] = mode
    os.environ["FORMAT_V2"] = "1" if use_format_v2 else "0"
    day_offset = 0 if mode == "morning" else 1
    os.environ["DAY_OFFSET"] = str(day_offset)
    os.environ["ASTRO_OFFSET"] = str(day_offset)
    if mode == "morning":
        os.environ.setdefault("SHOW_AIR", "1")
        os.environ.setdefault("SHOW_SPACE", "1")
        os.environ.setdefault("SHOW_SCHUMANN", "1")
    else:
        os.environ["SHOW_AIR"] = "0"
        os.environ["SHOW_SPACE"] = "0"
        os.environ["SHOW_SCHUMANN"] = "0"


def build_safe_format_v2_message(*, mode: str, date: str = "", for_tomorrow: bool = False) -> tuple[str, str, str]:
    """Return (raw_message, legacy_sanitized_text, final_format_v2_text)."""
    mode = (mode or "evening").strip().lower()
    _prepare_env(mode, True)

    tz = pendulum.timezone(TZ_STR)
    base_date = pendulum.parse(date).in_tz(tz) if date else pendulum.now(tz)
    if for_tomorrow:
        base_date = base_date.add(days=1)

    with _TodayPatch(base_date):
        raw_msg = build_message(
            region_name="Калининградская область",
            sea_label=SEA_LABEL,
            sea_cities=SEA_CITIES_ORDERED,
            other_label=OTHER_LABEL,
            other_cities=OTHER_CITIES_ALL,
            tz=TZ_STR,
            mode=mode,
        )

    legacy_result = sanitize_post_text(raw_msg)

    v2_raw = build_format_v2("Калининградская область", mode, legacy_result.text)
    v2_raw = _inject_morning_feels(v2_raw, mode)
    v2_raw = _inject_morning_best_window(v2_raw, mode)
    v2_raw = _inject_morning_score(v2_raw, mode)
    v2_raw = _inject_evening_score(v2_raw, mode)
    v2_raw = _inject_sensor_line(v2_raw, legacy_result.text)
    v2_raw = _apply_format_v2_test_polish(v2_raw)
    v2_raw = _apply_confidence_polish(v2_raw)
    v2_raw = _insert_main_nuance(v2_raw)
    v2_raw = _apply_astro_cleanup(v2_raw)
    v2_raw = _apply_score_conclusion(v2_raw)
    v2_raw = _inject_morning_smart_plan(v2_raw, mode)
    v2_raw = _apply_compact(v2_raw)
    final_result = sanitize_post_text(v2_raw)

    return raw_msg, legacy_result.text, final_result.text


def main() -> None:
    parser = argparse.ArgumentParser(description="KLD visual prompt-only probe")
    parser.add_argument("--mode", choices=["morning", "evening"], default=os.getenv("POST_MODE", "evening"))
    parser.add_argument("--date", default=os.getenv("WORK_DATE", ""))
    parser.add_argument("--for-tomorrow", action="store_true")
    parser.add_argument("--print-message", action="store_true", help="Also print the final FORMAT_V2 message")
    parser.add_argument("--compact", action="store_true", help="Compact JSON output")
    args = parser.parse_args()

    raw_msg, legacy_text, final_text = build_safe_format_v2_message(
        mode=args.mode,
        date=args.date,
        for_tomorrow=bool(args.for_tomorrow),
    )

    ctx = build_visual_context(final_text, post_type=args.mode)
    cues = apply_visual_rules(ctx)
    prompt = build_prompt_from_cues(cues)

    if args.print_message:
        print("\n===== FORMAT_V2 MESSAGE BEGIN =====\n")
        print(final_text)
        print("\n===== FORMAT_V2 MESSAGE END =====\n")

    print("\n===== VISUAL_CONTEXT BEGIN =====\n")
    print(context_to_json(ctx, pretty=not args.compact))
    print("\n===== VISUAL_CONTEXT END =====\n")

    print("\n===== VISUAL_CUES BEGIN =====\n")
    print(cues_to_json(cues, pretty=not args.compact))
    print("\n===== VISUAL_CUES END =====\n")

    print("\n===== VISUAL_PROMPT BEGIN =====\n")
    print(prompt)
    print("\n===== VISUAL_PROMPT END =====\n")

    print("\n===== FORMAT_V2 SAFETY SUMMARY =====\n")
    print(validation_summary(sanitize_post_text(final_text)))


if __name__ == "__main__":
    main()


__all__ = ["build_safe_format_v2_message"]
