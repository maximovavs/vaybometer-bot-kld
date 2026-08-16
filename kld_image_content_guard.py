#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Conservative offline content guard for provider-generated KLD images.

This gate is deliberately conservative. It rejects high-confidence screen/UI
outputs plus narrow geographic/seasonal failures that can be established from
image structure without an external vision service: a dry golden steppe
replacing living summer vegetation, a land-only frame for a scene whose
defining feature is open Baltic water, and a broad snow/ice-like lower-ground
surface during Baltic summer. Sand, wood, reeds, sea foam, light stone and
autumn colour are protected by season, geometry and colour-context checks.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import colorsys
import datetime as dt
import logging
from pathlib import Path
from typing import Any

try:
    from PIL import Image, ImageFilter, ImageOps
except Exception:  # pragma: no cover
    Image = None  # type: ignore
    ImageFilter = None  # type: ignore
    ImageOps = None  # type: ignore

LOG = logging.getLogger("kld.image_content_guard")

_SAMPLE_SIZE = 256
_EDGE_THRESHOLD = 45
_TOP_START_ROW = 2
_TOP_END_ROW = 32
_BODY_START_ROW = 48
_BODY_END_ROW = 254

_OPEN_BALTIC_SCENES = frozenset(
    {
        "curonian_spit_dunes",
        "svetlogorsk_cliff_coast",
        "zelenogradsk_promenade",
        "baltiysk_breakwater",
        "yantarny_wide_beach",
        "stormy_open_baltic",
        "elevated_baltic_overlook",
        "kaliningrad_urban_coastal_view",
    }
)
_LIVING_VEGETATION_SCENES = frozenset(
    {
        "curonian_spit_dunes",
        "svetlogorsk_cliff_coast",
        "yantarny_wide_beach",
        "pine_forest_sea_path",
        "quiet_lagoon_coast",
        "elevated_baltic_overlook",
        "rainy_coastal_road",
    }
)


@dataclass(frozen=True)
class KldImageContentVerdict:
    valid: bool
    reason: str
    top_edge_density: float
    body_edge_density: float
    top_to_body_edge_ratio: float
    dense_top_rows: int
    lower_gold_fraction: float
    lower_green_fraction: float
    water_band_fraction: float
    water_rows: int
    dense_gold_rows: int
    lower_cold_white_fraction: float
    dense_cold_white_rows: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _edge_metrics(image: "Image.Image") -> tuple[float, float, float, int]:
    sample = ImageOps.grayscale(
        image.convert("RGB").resize((_SAMPLE_SIZE, _SAMPLE_SIZE), Image.Resampling.LANCZOS)
    )
    edges = sample.filter(ImageFilter.FIND_EDGES)
    values = list(edges.getdata())
    rows: list[float] = []
    for y in range(_SAMPLE_SIZE):
        start = y * _SAMPLE_SIZE
        row = values[start : start + _SAMPLE_SIZE]
        rows.append(sum(value >= _EDGE_THRESHOLD for value in row) / _SAMPLE_SIZE)

    top_rows = rows[_TOP_START_ROW:_TOP_END_ROW]
    body_rows = rows[_BODY_START_ROW:_BODY_END_ROW]
    top = sum(top_rows) / len(top_rows)
    body = sum(body_rows) / len(body_rows)
    ratio = top / max(body, 1e-6)
    dense_top_rows = sum(value >= 0.20 for value in top_rows)
    return top, body, ratio, dense_top_rows


def _semantic_colour_metrics(image: "Image.Image") -> tuple[float, float, float, int, int]:
    sample_size = 160
    sample = image.convert("RGB").resize(
        (sample_size, sample_size), Image.Resampling.BILINEAR
    )
    rows = [
        list(sample.crop((0, y, sample_size, y + 1)).getdata())
        for y in range(sample_size)
    ]
    lower_start = int(sample_size * 0.48)
    lower_end = int(sample_size * 0.98)
    water_start = int(sample_size * 0.43)
    water_end = int(sample_size * 0.80)
    gold = 0
    green = 0
    lower_total = max(1, (lower_end - lower_start) * sample_size)
    water = 0
    water_total = max(1, (water_end - water_start) * sample_size)
    water_rows = 0
    dense_gold_rows = 0

    for y, row in enumerate(rows):
        row_gold = 0
        row_water = 0
        for red, green_channel, blue in row:
            hue, saturation, value = colorsys.rgb_to_hsv(
                red / 255.0,
                green_channel / 255.0,
                blue / 255.0,
            )
            is_green = (
                0.19 <= hue <= 0.46
                and saturation >= 0.18
                and 0.12 <= value <= 0.88
                and green_channel >= red * 0.82
                and green_channel >= blue * 0.80
            )
            is_gold = (
                0.07 <= hue <= 0.18
                and saturation >= 0.20
                and 0.30 <= value <= 0.92
                and red >= green_channel * 0.95
                and green_channel >= blue * 1.12
            )
            blue_water = (
                0.48 <= hue <= 0.72
                and saturation >= 0.12
                and 0.15 <= value <= 0.90
            )
            cool_grey_water = (
                saturation < 0.20
                and 0.24 <= value <= 0.78
                and blue >= red * 1.03
                and green_channel >= red * 0.98
            )
            if lower_start <= y < lower_end:
                if is_green:
                    green += 1
                if is_gold:
                    gold += 1
                    row_gold += 1
            if water_start <= y < water_end and (blue_water or cool_grey_water):
                water += 1
                row_water += 1
        if lower_start <= y < lower_end and row_gold / sample_size >= 0.42:
            dense_gold_rows += 1
        if water_start <= y < water_end and row_water / sample_size >= 0.32:
            water_rows += 1

    return (
        gold / lower_total,
        green / lower_total,
        water / water_total,
        water_rows,
        dense_gold_rows,
    )


def _winter_surface_metrics(image: "Image.Image") -> tuple[float, int]:
    """Measure only broad bright neutral/cool surfaces at the bottom of frame.

    The lower-ground crop intentionally avoids cloud-dominant sky and almost all
    shoreline foam. Warm pale sand is excluded by saturation/colour balance;
    ordinary concrete and wet stone remain below the deliberately high value
    threshold. The signal is therefore narrow rather than a generic white-pixel
    detector.
    """
    sample_size = 160
    sample = image.convert("RGB").resize(
        (sample_size, sample_size), Image.Resampling.BILINEAR
    )
    ground_start = int(sample_size * 0.70)
    ground_end = int(sample_size * 0.98)
    cold_white = 0
    ground_total = max(1, (ground_end - ground_start) * sample_size)
    dense_rows = 0

    for y in range(ground_start, ground_end):
        row = list(sample.crop((0, y, sample_size, y + 1)).getdata())
        row_cold_white = 0
        for red, green_channel, blue in row:
            _hue, saturation, value = colorsys.rgb_to_hsv(
                red / 255.0,
                green_channel / 255.0,
                blue / 255.0,
            )
            is_cold_white = (
                saturation <= 0.12
                and value >= 0.80
                and green_channel >= red - 3
                and blue >= red + 4
            )
            if is_cold_white:
                cold_white += 1
                row_cold_white += 1
        if row_cold_white / sample_size >= 0.60:
            dense_rows += 1

    return cold_white / ground_total, dense_rows


def _is_summer(value: str | dt.date | None) -> bool:
    if isinstance(value, dt.date):
        return value.month in {6, 7, 8}
    try:
        return dt.date.fromisoformat(str(value or "")[:10]).month in {6, 7, 8}
    except ValueError:
        return False


def inspect_kld_provider_image(
    path: str | Path,
    *,
    scene_family: str = "",
    target_date: str | dt.date | None = None,
) -> KldImageContentVerdict:
    """Return a conservative verdict for an AI-provider image."""
    if Image is None:
        return KldImageContentVerdict(
            valid=False,
            reason="pillow_unavailable",
            top_edge_density=0.0,
            body_edge_density=0.0,
            top_to_body_edge_ratio=0.0,
            dense_top_rows=0,
            lower_gold_fraction=0.0,
            lower_green_fraction=0.0,
            water_band_fraction=0.0,
            water_rows=0,
            dense_gold_rows=0,
            lower_cold_white_fraction=0.0,
            dense_cold_white_rows=0,
        )

    try:
        with Image.open(path) as opened:
            image = opened.convert("RGB")
            top, body, ratio, dense_top_rows = _edge_metrics(image)
            gold, green, water, water_rows, dense_gold_rows = _semantic_colour_metrics(image)
            cold_white, dense_cold_white_rows = _winter_surface_metrics(image)
    except Exception as exc:
        LOG.warning("KLD provider content inspection failed: %s", exc)
        return KldImageContentVerdict(
            valid=False,
            reason=f"content_guard_error:{exc.__class__.__name__}",
            top_edge_density=0.0,
            body_edge_density=0.0,
            top_to_body_edge_ratio=0.0,
            dense_top_rows=0,
            lower_gold_fraction=0.0,
            lower_green_fraction=0.0,
            water_band_fraction=0.0,
            water_rows=0,
            dense_gold_rows=0,
            lower_cold_white_fraction=0.0,
            dense_cold_white_rows=0,
        )

    screenshot_chrome = (
        top >= 0.11
        and body <= 0.08
        and ratio >= 3.0
        and dense_top_rows >= 5
    )
    scene = str(scene_family or "").strip()
    summer_snow_or_ice = (
        _is_summer(target_date)
        and cold_white >= 0.58
        and dense_cold_white_rows >= 18
    )
    summer_dry_steppe = (
        _is_summer(target_date)
        and scene in _LIVING_VEGETATION_SCENES
        and gold >= 0.32
        and green <= 0.07
        and dense_gold_rows >= 12
        and (water < 0.10 or water_rows < 4)
    )
    required_baltic_missing = (
        scene in _OPEN_BALTIC_SCENES
        and water < 0.02
        and water_rows == 0
        and (green >= 0.25 or gold >= 0.25)
    )
    if screenshot_chrome:
        reason = "screen_or_ui_chrome"
    elif summer_snow_or_ice:
        reason = "summer_snow_or_ice"
    elif summer_dry_steppe:
        reason = "summer_dry_steppe"
    elif required_baltic_missing:
        reason = "required_baltic_missing"
    else:
        reason = "accepted"
    return KldImageContentVerdict(
        valid=reason == "accepted",
        reason=reason,
        top_edge_density=round(top, 6),
        body_edge_density=round(body, 6),
        top_to_body_edge_ratio=round(ratio, 6),
        dense_top_rows=dense_top_rows,
        lower_gold_fraction=round(gold, 6),
        lower_green_fraction=round(green, 6),
        water_band_fraction=round(water, 6),
        water_rows=water_rows,
        dense_gold_rows=dense_gold_rows,
        lower_cold_white_fraction=round(cold_white, 6),
        dense_cold_white_rows=dense_cold_white_rows,
    )


__all__ = ["KldImageContentVerdict", "inspect_kld_provider_image"]
