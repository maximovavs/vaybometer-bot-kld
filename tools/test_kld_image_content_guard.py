#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline checks for KLD provider-image relevance guard."""
from __future__ import annotations

from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kld_image_content_guard import inspect_kld_provider_image  # noqa: E402
from kld_visual_dedup import evaluate_kld_visual_candidate  # noqa: E402


def _tmpdir() -> Path:
    return Path(tempfile.mkdtemp(prefix="kld_content_guard_"))


def _require_pillow():
    try:
        from PIL import Image, ImageDraw
    except Exception:
        requirements = (ROOT / "requirements.txt").read_text("utf-8")
        assert "Pillow" in requirements
        return None, None
    return Image, ImageDraw


def _write_screen_like(path: Path) -> bool:
    Image, ImageDraw = _require_pillow()
    if Image is None:
        return False
    image = Image.new("RGB", (512, 512), color=(180, 198, 207))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 511, 65), fill=(18, 25, 31))
    for y in (8, 18, 28, 38, 48):
        for x in range(12, 492, 28):
            draw.rectangle((x, y, x + 10, y + 5), fill=(235, 238, 240))
    draw.rectangle((30, 110, 480, 450), fill=(166, 186, 196))
    image.save(path)
    return True


def _write_landscape_like(path: Path) -> bool:
    Image, ImageDraw = _require_pillow()
    if Image is None:
        return False
    image = Image.new("RGB", (512, 512), color=(170, 195, 210))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 511, 260), fill=(150, 180, 198))
    draw.rectangle((0, 260, 511, 390), fill=(67, 111, 130))
    draw.rectangle((0, 390, 511, 511), fill=(82, 124, 72))
    for offset in range(0, 512, 32):
        draw.line((offset, 330, min(511, offset + 80), 330), fill=(205, 218, 218), width=2)
    image.save(path)
    return True


def kld_guard_rejects_dense_top_ui_band() -> None:
    root = _tmpdir()
    try:
        path = root / "screen.png"
        if not _write_screen_like(path):
            return
        verdict = inspect_kld_provider_image(path)
        assert verdict.valid is False
        assert verdict.reason == "screen_or_ui_chrome"
        assert verdict.dense_top_rows >= 5
    finally:
        shutil.rmtree(root, ignore_errors=True)


def kld_guard_accepts_simple_landscape() -> None:
    root = _tmpdir()
    try:
        path = root / "landscape.png"
        if not _write_landscape_like(path):
            return
        verdict = inspect_kld_provider_image(path)
        assert verdict.valid is True
        assert verdict.reason == "accepted"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def kld_dedup_gate_propagates_content_rejection() -> None:
    root = _tmpdir()
    try:
        path = root / "screen.png"
        history = root / "history.json"
        if not _write_screen_like(path):
            return
        result = evaluate_kld_visual_candidate(
            path,
            date_value="2026-08-08",
            target_date="2026-08-08",
            post_type="morning",
            scene_family="pine_forest_sea_path",
            composition="pine-framed side composition",
            prompt_version="test",
            history_path=history,
        )
        assert result.accepted is False
        assert result.reason == "content_guard:screen_or_ui_chrome"
        assert result.content_guard and result.content_guard["valid"] is False
    finally:
        shutil.rmtree(root, ignore_errors=True)


def kld_local_cover_bypasses_provider_content_guard() -> None:
    root = _tmpdir()
    try:
        path = root / "cover.png"
        history = root / "history.json"
        if not _write_screen_like(path):
            return
        result = evaluate_kld_visual_candidate(
            path,
            date_value="2026-08-08",
            target_date="2026-08-08",
            post_type="morning",
            scene_family="local_informative_cover",
            composition="branded_weather_card",
            prompt_version="cover_test",
            history_path=history,
        )
        assert result.accepted is True
        assert result.content_guard is None
    finally:
        shutil.rmtree(root, ignore_errors=True)


TESTS = [
    kld_guard_rejects_dense_top_ui_band,
    kld_guard_accepts_simple_landscape,
    kld_dedup_gate_propagates_content_rejection,
    kld_local_cover_bypasses_provider_content_guard,
]


def main() -> None:
    for test in TESTS:
        test()
        print(f"PASS: {test.__name__}")
    print(f"OK: {len(TESTS)} KLD image content guard checks passed")


if __name__ == "__main__":
    main()
