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
    # Dense high-frequency toolbar stripes create a deterministic UI-chrome
    # edge band without relying on fonts or platform rendering.
    for x in range(0, 512, 6):
        draw.rectangle((x, 5, min(511, x + 2), 58), fill=(238, 241, 243))
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


def _write_layered_scene(
    path: Path,
    *,
    sky: tuple[int, int, int],
    water: tuple[int, int, int] | None,
    ground: tuple[int, int, int],
    water_top: int = 210,
    ground_top: int = 370,
) -> bool:
    Image, ImageDraw = _require_pillow()
    if Image is None:
        return False
    image = Image.new("RGB", (512, 512), color=sky)
    draw = ImageDraw.Draw(image)
    if water is not None:
        draw.rectangle((0, water_top, 511, ground_top - 1), fill=water)
        for y in range(water_top + 10, ground_top, 24):
            draw.line((0, y, 511, y), fill=tuple(min(255, value + 12) for value in water), width=2)
    else:
        ground_top = water_top
    draw.rectangle((0, ground_top, 511, 511), fill=ground)
    for y in range(ground_top + 8, 512, 20):
        draw.line((0, y, 511, y), fill=tuple(max(0, value - 10) for value in ground), width=2)
    image.save(path)
    return True


def _write_foamy_baltic_scene(path: Path) -> bool:
    Image, ImageDraw = _require_pillow()
    if Image is None:
        return False
    image = Image.new("RGB", (512, 512), color=(138, 151, 161))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 190, 511, 369), fill=(72, 105, 124))
    for y in range(215, 360, 26):
        draw.line((0, y, 511, y), fill=(236, 241, 243), width=8)
    draw.rectangle((0, 370, 511, 511), fill=(112, 122, 125))
    for y in range(385, 512, 28):
        draw.line((0, y, 511, y), fill=(96, 105, 108), width=3)
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


def kld_guard_rejects_summer_dry_steppe_without_baltic() -> None:
    root = _tmpdir()
    try:
        path = root / "dry-field.png"
        if not _write_layered_scene(
            path,
            sky=(150, 181, 205),
            water=None,
            ground=(184, 148, 62),
            water_top=190,
        ):
            return
        verdict = inspect_kld_provider_image(
            path,
            scene_family="curonian_spit_dunes",
            target_date="2026-08-09",
        )
        assert verdict.valid is False
        assert verdict.reason == "summer_dry_steppe"
        assert verdict.lower_gold_fraction >= 0.32
        assert verdict.lower_green_fraction <= 0.07
    finally:
        shutil.rmtree(root, ignore_errors=True)


def kld_guard_rejects_august_snow_covered_promenade() -> None:
    root = _tmpdir()
    try:
        path = root / "summer-snow.png"
        if not _write_layered_scene(
            path,
            sky=(148, 164, 178),
            water=(67, 102, 124),
            ground=(229, 237, 244),
        ):
            return
        verdict = inspect_kld_provider_image(
            path,
            scene_family="zelenogradsk_promenade",
            target_date="2026-08-17",
        )
        assert verdict.valid is False, verdict
        assert verdict.reason == "summer_snow_or_ice"
        assert verdict.lower_cold_white_fraction >= 0.58
        assert verdict.dense_cold_white_rows >= 18
    finally:
        shutil.rmtree(root, ignore_errors=True)


def kld_guard_allows_same_snow_surface_in_winter() -> None:
    root = _tmpdir()
    try:
        path = root / "winter-snow.png"
        if not _write_layered_scene(
            path,
            sky=(148, 164, 178),
            water=(67, 102, 124),
            ground=(229, 237, 244),
        ):
            return
        verdict = inspect_kld_provider_image(
            path,
            scene_family="zelenogradsk_promenade",
            target_date="2026-12-17",
        )
        assert verdict.reason != "summer_snow_or_ice", verdict
        assert verdict.valid is True, verdict
    finally:
        shutil.rmtree(root, ignore_errors=True)


def kld_guard_accepts_green_baltic_coast() -> None:
    root = _tmpdir()
    try:
        path = root / "green-coast.png"
        if not _write_layered_scene(
            path,
            sky=(156, 181, 198),
            water=(65, 108, 130),
            ground=(72, 119, 64),
        ):
            return
        verdict = inspect_kld_provider_image(
            path,
            scene_family="curonian_spit_dunes",
            target_date="2026-08-09",
        )
        assert verdict.valid is True, verdict
        assert verdict.water_rows >= 4
        assert verdict.lower_green_fraction > 0.10
    finally:
        shutil.rmtree(root, ignore_errors=True)


def kld_guard_accepts_sand_wood_reeds_and_cloudy_baltic() -> None:
    root = _tmpdir()
    try:
        cases = (
            ("sand", (151, 181, 199), (61, 104, 127), (208, 188, 137), "curonian_spit_dunes"),
            ("wood", (151, 181, 199), (61, 104, 127), (132, 91, 49), "zelenogradsk_promenade"),
            ("reeds", (151, 181, 199), (61, 104, 127), (176, 146, 78), "quiet_lagoon_coast"),
            ("cloudy", (132, 142, 149), (91, 105, 113), (73, 104, 67), "curonian_spit_dunes"),
        )
        for name, sky, water, ground, scene in cases:
            path = root / f"{name}.png"
            assert _write_layered_scene(path, sky=sky, water=water, ground=ground)
            verdict = inspect_kld_provider_image(
                path,
                scene_family=scene,
                target_date="2026-08-09",
            )
            assert verdict.valid is True, (name, verdict)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def kld_guard_accepts_august_pale_sand() -> None:
    root = _tmpdir()
    try:
        path = root / "pale-sand.png"
        if not _write_layered_scene(
            path,
            sky=(151, 181, 199),
            water=(61, 104, 127),
            ground=(218, 203, 164),
        ):
            return
        verdict = inspect_kld_provider_image(
            path,
            scene_family="yantarny_wide_beach",
            target_date="2026-08-17",
        )
        assert verdict.valid is True, verdict
        assert verdict.reason == "accepted"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def kld_guard_accepts_august_clouds_and_white_foam() -> None:
    root = _tmpdir()
    try:
        path = root / "white-foam.png"
        if not _write_foamy_baltic_scene(path):
            return
        verdict = inspect_kld_provider_image(
            path,
            scene_family="stormy_open_baltic",
            target_date="2026-08-17",
        )
        assert verdict.valid is True, verdict
        assert verdict.reason == "accepted"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def kld_guard_accepts_august_light_and_wet_promenade() -> None:
    root = _tmpdir()
    try:
        cases = (
            ("light", (191, 196, 200)),
            ("wet", (118, 129, 136)),
        )
        for name, ground in cases:
            path = root / f"{name}-promenade.png"
            assert _write_layered_scene(
                path,
                sky=(143, 158, 169),
                water=(69, 102, 120),
                ground=ground,
            )
            verdict = inspect_kld_provider_image(
                path,
                scene_family="zelenogradsk_promenade",
                target_date="2026-08-17",
            )
            assert verdict.valid is True, (name, verdict)
            assert verdict.reason == "accepted"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def kld_guard_accepts_august_bright_neutral_paving() -> None:
    root = _tmpdir()
    try:
        cases = (
            ("neutral-concrete", (236, 236, 236)),
            ("warm-limestone", (236, 232, 218)),
        )
        for name, ground in cases:
            path = root / f"{name}.png"
            assert _write_layered_scene(
                path,
                sky=(143, 158, 169),
                water=(69, 102, 120),
                ground=ground,
            )
            verdict = inspect_kld_provider_image(
                path,
                scene_family="zelenogradsk_promenade",
                target_date="2026-08-17",
            )
            assert verdict.valid is True, (name, verdict)
            assert verdict.reason == "accepted"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def kld_guard_accepts_autumn_gold_with_baltic() -> None:
    root = _tmpdir()
    try:
        path = root / "autumn.png"
        if not _write_layered_scene(
            path,
            sky=(140, 155, 165),
            water=(78, 102, 118),
            ground=(177, 137, 57),
        ):
            return
        verdict = inspect_kld_provider_image(
            path,
            scene_family="curonian_spit_dunes",
            target_date="2026-10-09",
        )
        assert verdict.valid is True, verdict
    finally:
        shutil.rmtree(root, ignore_errors=True)


def kld_guard_rejects_land_only_frame_for_open_baltic_scene() -> None:
    root = _tmpdir()
    try:
        path = root / "inland-meadow.png"
        if not _write_layered_scene(
            path,
            sky=(160, 185, 203),
            water=None,
            ground=(69, 122, 65),
            water_top=200,
        ):
            return
        verdict = inspect_kld_provider_image(
            path,
            scene_family="curonian_spit_dunes",
            target_date="2026-08-09",
        )
        assert verdict.valid is False
        assert verdict.reason == "required_baltic_missing"
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


def kld_dedup_gate_propagates_summer_snow_rejection() -> None:
    root = _tmpdir()
    try:
        path = root / "summer-snow.png"
        history = root / "history.json"
        if not _write_layered_scene(
            path,
            sky=(148, 164, 178),
            water=(67, 102, 124),
            ground=(229, 237, 244),
        ):
            return
        result = evaluate_kld_visual_candidate(
            path,
            date_value="2026-08-17",
            target_date="2026-08-17",
            post_type="evening",
            scene_family="zelenogradsk_promenade",
            composition="promenade railing foreground",
            prompt_version="test",
            history_path=history,
        )
        assert result.accepted is False
        assert result.reason == "content_guard:summer_snow_or_ice"
        assert result.content_guard and result.content_guard["reason"] == "summer_snow_or_ice"
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
    kld_guard_rejects_summer_dry_steppe_without_baltic,
    kld_guard_rejects_august_snow_covered_promenade,
    kld_guard_allows_same_snow_surface_in_winter,
    kld_guard_accepts_green_baltic_coast,
    kld_guard_accepts_sand_wood_reeds_and_cloudy_baltic,
    kld_guard_accepts_august_pale_sand,
    kld_guard_accepts_august_clouds_and_white_foam,
    kld_guard_accepts_august_light_and_wet_promenade,
    kld_guard_accepts_august_bright_neutral_paving,
    kld_guard_accepts_autumn_gold_with_baltic,
    kld_guard_rejects_land_only_frame_for_open_baltic_scene,
    kld_dedup_gate_propagates_content_rejection,
    kld_dedup_gate_propagates_summer_snow_rejection,
    kld_local_cover_bypasses_provider_content_guard,
]


def main() -> None:
    for test in TESTS:
        test()
        print(f"PASS: {test.__name__}")
    print(f"OK: {len(TESTS)} KLD image content guard checks passed")


if __name__ == "__main__":
    main()
