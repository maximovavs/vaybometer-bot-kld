#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Conservative offline content guard for provider-generated KLD images.

This gate is deliberately narrow.  It rejects only high-confidence screen/UI
outputs that are technically valid image files but unsuitable for a weather
channel.  Geographic relevance is primarily enforced by prompt and scene
policy; broad image-semantic classification is intentionally not guessed here.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
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


@dataclass(frozen=True)
class KldImageContentVerdict:
    valid: bool
    reason: str
    top_edge_density: float
    body_edge_density: float
    top_to_body_edge_ratio: float
    dense_top_rows: int

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


def inspect_kld_provider_image(path: str | Path) -> KldImageContentVerdict:
    """Return a conservative verdict for an AI-provider image."""
    if Image is None:
        return KldImageContentVerdict(
            valid=False,
            reason="pillow_unavailable",
            top_edge_density=0.0,
            body_edge_density=0.0,
            top_to_body_edge_ratio=0.0,
            dense_top_rows=0,
        )

    try:
        with Image.open(path) as opened:
            image = opened.convert("RGB")
            top, body, ratio, dense_top_rows = _edge_metrics(image)
    except Exception as exc:
        LOG.warning("KLD provider content inspection failed: %s", exc)
        return KldImageContentVerdict(
            valid=False,
            reason=f"content_guard_error:{exc.__class__.__name__}",
            top_edge_density=0.0,
            body_edge_density=0.0,
            top_to_body_edge_ratio=0.0,
            dense_top_rows=0,
        )

    screenshot_chrome = (
        top >= 0.11
        and body <= 0.08
        and ratio >= 3.0
        and dense_top_rows >= 5
    )
    reason = "screen_or_ui_chrome" if screenshot_chrome else "accepted"
    return KldImageContentVerdict(
        valid=reason == "accepted",
        reason=reason,
        top_edge_density=round(top, 6),
        body_edge_density=round(body, 6),
        top_to_body_edge_ratio=round(ratio, 6),
        dense_top_rows=dense_top_rows,
    )


__all__ = ["KldImageContentVerdict", "inspect_kld_provider_image"]
