#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline target-date boundary checks for KLD seasonal visual policy."""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kld_visual_policy import SUMMER_VEGETATION_CUE, finalize_kld_provider_prompt  # noqa: E402


BASE_PROMPT = "\n".join(
    [
        "Base scene: Baltic coast near Kaliningrad, dunes, pines, promenade, sea horizon.",
        "Palette: cool Baltic grey-blue, muted sand, pine green, restrained northern light.",
        "Must show: recognizable Baltic/Kaliningrad atmosphere; dunes, pines, promenade, or Baltic sea horizon.",
        "Text restrictions: no text, no captions, no labels.",
    ]
)


def _metadata(*, forecast_date: str, target_date: str) -> dict[str, str]:
    return {
        "forecast_date": forecast_date,
        "target_date": target_date,
        "scene_family": "pine_forest_sea_path",
        "scene_text": "pine forest sea path opening toward the Baltic shore, realistic northern vegetation",
        "composition": "pine-framed side composition",
    }


def may_evening_for_june_target_is_summer() -> None:
    prompt = finalize_kld_provider_prompt(
        BASE_PROMPT,
        metadata=_metadata(forecast_date="2026-05-31", target_date="2026-06-01"),
        date_key="2026-05-31",
    )
    assert SUMMER_VEGETATION_CUE in prompt
    assert "fresh natural summer greens" in prompt


def august_evening_for_september_target_is_not_forced_summer() -> None:
    prompt = finalize_kld_provider_prompt(
        BASE_PROMPT,
        metadata=_metadata(forecast_date="2026-08-31", target_date="2026-09-01"),
        date_key="2026-08-31",
    )
    assert SUMMER_VEGETATION_CUE not in prompt
    assert "fresh natural summer greens" not in prompt


def main() -> None:
    may_evening_for_june_target_is_summer()
    print("PASS: may_evening_for_june_target_is_summer")
    august_evening_for_september_target_is_not_forced_summer()
    print("PASS: august_evening_for_september_target_is_not_forced_summer")
    print("OK: 2 KLD target-date seasonal boundary checks passed")


if __name__ == "__main__":
    main()
