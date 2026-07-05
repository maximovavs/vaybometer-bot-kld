#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline checks for KLD visual duplicate detection."""

from __future__ import annotations

from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kld_visual_dedup import (  # noqa: E402
    KLD_VISUAL_DHASH_THRESHOLD,
    dhash_file,
    ensure_pillow_for_visual_dedup,
    evaluate_kld_visual_candidate,
    hamming_distance_hex,
    kld_visual_history_path,
    load_kld_visual_history,
    pillow_available,
    record_kld_visual_publication,
)


def _write_ppm(path: Path, *, mode: str, tint: int = 0) -> None:
    width = 64
    height = 64
    payload = bytearray()
    for y in range(height):
        for x in range(width):
            if mode == "coast_a":
                value = int(255 * x / (width - 1))
                if 20 <= x <= 44 and 18 <= y <= 48:
                    value = max(0, value - 18)
            elif mode == "coast_a_cropped":
                source_x = min(width - 1, max(0, x + 2))
                value = int(255 * source_x / (width - 1))
                if 18 <= x <= 42 and 16 <= y <= 46:
                    value = max(0, value - 18)
            elif mode == "coast_b":
                value = int(255 * (width - 1 - x) / (width - 1))
            else:
                value = (x * 7 + y * 13) % 256
            r = max(0, min(255, value + tint))
            g = max(0, min(255, value + tint // 2))
            b = max(0, min(255, value - tint // 2))
            payload.extend((r, g, b))
    path.write_bytes(f"P6\n{width} {height}\n255\n".encode("ascii") + bytes(payload))


def _tmpdir() -> Path:
    return Path(tempfile.mkdtemp(prefix="kld_visual_dedup_"))


def _evaluate(path: Path, history: Path, date_value: str = "2026-07-05"):
    return evaluate_kld_visual_candidate(
        path,
        date_value=date_value,
        target_date=date_value,
        post_type="morning",
        scene_family="curonian_spit_dunes",
        composition="wide diagonal shoreline composition",
        prompt_version="kld_visual_v_test",
        history_path=history,
    )


def _record(history: Path, image: Path, *, date_value: str = "2026-07-01", post_type: str = "morning"):
    return record_kld_visual_publication(
        date_value=date_value,
        target_date=date_value,
        post_type=post_type,
        image_path=image,
        scene_family="curonian_spit_dunes",
        composition="wide diagonal shoreline composition",
        prompt_version="kld_visual_v_test",
        cache_key="region=kld;forecast_date=2026-07-01",
        style_name="style_test",
        history_path=history,
    )


def kld_dedup_exact_sha_is_rejected() -> None:
    root = _tmpdir()
    try:
        history = root / "history.json"
        image = root / "image.ppm"
        _write_ppm(image, mode="coast_a")
        _record(history, image)
        result = _evaluate(image, history)
        assert result.accepted is False
        assert result.reason == "exact_duplicate"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def kld_dedup_near_duplicate_recolor_crop_is_rejected() -> None:
    root = _tmpdir()
    try:
        history = root / "history.json"
        original = root / "original.ppm"
        similar = root / "similar.ppm"
        _write_ppm(original, mode="coast_a")
        _write_ppm(similar, mode="coast_a_cropped", tint=8)
        _record(history, original, post_type="evening")
        distance = hamming_distance_hex(dhash_file(original), dhash_file(similar))
        assert distance is not None and distance <= KLD_VISUAL_DHASH_THRESHOLD
        result = _evaluate(similar, history, date_value="2026-07-02")
        assert result.accepted is False
        assert result.reason == "near_duplicate"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def kld_dedup_genuinely_different_image_is_accepted() -> None:
    root = _tmpdir()
    try:
        history = root / "history.json"
        original = root / "original.ppm"
        different = root / "different.ppm"
        _write_ppm(original, mode="coast_a")
        _write_ppm(different, mode="coast_b")
        _record(history, original)
        result = _evaluate(different, history)
        assert result.accepted is True
        assert result.reason == "accepted"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def kld_dedup_history_namespaces_are_separate() -> None:
    assert kld_visual_history_path("prod").name == "kld_visual_history_prod.json"
    assert kld_visual_history_path("test").name == "kld_visual_history_test.json"


def kld_dedup_record_is_atomic_and_dedupes_same_publication() -> None:
    root = _tmpdir()
    try:
        history = root / "history.json"
        image = root / "image.ppm"
        _write_ppm(image, mode="coast_a")
        for _ in range(2):
            _record(history, image, date_value="2026-07-05", post_type="morning")
        loaded = load_kld_visual_history(history)
        assert len(loaded) == 1

        _record(history, image, date_value="2026-07-05", post_type="evening")
        loaded = load_kld_visual_history(history)
        assert len(loaded) == 2
        assert {entry["post_type"] for entry in loaded} == {"morning", "evening"}
        assert {"date", "target_date", "sha256", "perceptual_hash", "scene_family", "composition"} <= set(loaded[0])
    finally:
        shutil.rmtree(root, ignore_errors=True)


def kld_dedup_malformed_history_keeps_backup() -> None:
    root = _tmpdir()
    try:
        history = root / "history.json"
        history.write_text("{not valid json", "utf-8")
        loaded = load_kld_visual_history(history)
        assert loaded == []
        backups = list(root.glob("history.json.malformed.*.bak"))
        assert backups
        assert backups[0].read_text("utf-8") == "{not valid json"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def kld_dedup_fresh_run_restore_simulation() -> None:
    root = _tmpdir()
    try:
        run1 = root / "run1"
        run2 = root / "run2"
        run3 = root / "run3"
        run4 = root / "run4"
        for folder in (run1, run2, run3, run4):
            folder.mkdir()

        image_a = run1 / "a.ppm"
        _write_ppm(image_a, mode="coast_a")
        history1 = run1 / "kld_visual_history_prod.json"
        _record(history1, image_a)

        history2 = run2 / history1.name
        shutil.copy2(history1, history2)
        image_a2 = run2 / "a.ppm"
        shutil.copy2(image_a, image_a2)
        exact = _evaluate(image_a2, history2, date_value="2026-07-02")
        assert exact.accepted is False
        assert exact.reason == "exact_duplicate"

        history3 = run3 / history1.name
        shutil.copy2(history1, history3)
        near_image = run3 / "near.ppm"
        _write_ppm(near_image, mode="coast_a_cropped", tint=8)
        near = _evaluate(near_image, history3, date_value="2026-07-03")
        assert near.accepted is False
        assert near.reason == "near_duplicate"

        history4 = run4 / history1.name
        shutil.copy2(history1, history4)
        image_b = run4 / "b.ppm"
        _write_ppm(image_b, mode="coast_b")
        different = _evaluate(image_b, history4, date_value="2026-07-04")
        assert different.accepted is True
        _record(history4, image_b, date_value="2026-07-04")
        assert len(load_kld_visual_history(history4)) == 2
    finally:
        shutil.rmtree(root, ignore_errors=True)


def kld_dedup_no_record_without_explicit_success_call() -> None:
    root = _tmpdir()
    try:
        history = root / "history.json"
        image = root / "image.ppm"
        _write_ppm(image, mode="coast_b")
        assert _evaluate(image, history).accepted is True
        assert load_kld_visual_history(history) == []
    finally:
        shutil.rmtree(root, ignore_errors=True)


def kld_dedup_png_jpg_hashes_when_pillow_available() -> None:
    if not pillow_available():
        requirements = (ROOT / "requirements.txt").read_text("utf-8")
        assert "Pillow>=10,<12" in requirements
        assert ensure_pillow_for_visual_dedup() is False
        return

    from PIL import Image

    root = _tmpdir()
    try:
        png = root / "sample.png"
        jpg = root / "sample.jpg"
        image = Image.new("RGB", (32, 32), color=(80, 140, 200))
        image.save(png)
        image.save(jpg)
        for path in (png, jpg):
            digest = dhash_file(path)
            assert digest is not None
            assert len(digest) == 16
            int(digest, 16)
    finally:
        shutil.rmtree(root, ignore_errors=True)


TESTS = [
    kld_dedup_exact_sha_is_rejected,
    kld_dedup_near_duplicate_recolor_crop_is_rejected,
    kld_dedup_genuinely_different_image_is_accepted,
    kld_dedup_history_namespaces_are_separate,
    kld_dedup_record_is_atomic_and_dedupes_same_publication,
    kld_dedup_malformed_history_keeps_backup,
    kld_dedup_fresh_run_restore_simulation,
    kld_dedup_no_record_without_explicit_success_call,
    kld_dedup_png_jpg_hashes_when_pillow_available,
]


def main() -> None:
    for test in TESTS:
        test()
        print(f"PASS: {test.__name__}")
    print(f"OK: {len(TESTS)} KLD visual dedup checks passed")


if __name__ == "__main__":
    main()
