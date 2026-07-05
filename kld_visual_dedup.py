#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""KLD visual history and duplicate detection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any


KLD_VISUAL_HISTORY_PATH = Path(os.getenv("KLD_VISUAL_HISTORY_PATH", ".cache/kld_visual_history_prod.json"))
KLD_VISUAL_HISTORY_PROD_PATH = Path(
    os.getenv("KLD_VISUAL_HISTORY_PROD_PATH", ".cache/kld_visual_history_prod.json")
)
KLD_VISUAL_HISTORY_TEST_PATH = Path(
    os.getenv("KLD_VISUAL_HISTORY_TEST_PATH", ".cache/kld_visual_history_test.json")
)
KLD_VISUAL_EXACT_DAYS = 30
KLD_VISUAL_NEAR_DAYS = 14
KLD_VISUAL_DHASH_THRESHOLD = 6


@dataclass(frozen=True)
class KldVisualDuplicateResult:
    accepted: bool
    reason: str
    sha256: str
    perceptual_hash: str | None
    min_distance: int | None = None
    matched_entry: dict[str, Any] | None = None


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_date(value: object) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _today() -> date:
    return datetime.utcnow().date()


def _within_days(entry: dict[str, Any], current: date, days: int) -> bool:
    entry_date = _parse_date(entry.get("date") or entry.get("target_date"))
    if entry_date is None:
        return True
    return current - timedelta(days=days) <= entry_date <= current


def kld_visual_history_path(namespace: str = "prod") -> Path:
    value = str(namespace or "prod").strip().lower()
    if value in {"prod", "production"}:
        return KLD_VISUAL_HISTORY_PROD_PATH
    if value in {"test", "safe_test"}:
        return KLD_VISUAL_HISTORY_TEST_PATH
    if value in {"dry", "dry_run", "none"}:
        return KLD_VISUAL_HISTORY_TEST_PATH
    raise ValueError("namespace must be 'prod' or 'test'")


def _backup_malformed_history(path: Path) -> None:
    if not path.exists():
        return
    stamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    backup = path.with_name(f"{path.name}.malformed.{stamp}.bak")
    try:
        backup.write_bytes(path.read_bytes())
        logging.warning("KLD visual malformed history backed up to %s", backup)
    except Exception as exc:
        logging.warning("KLD visual malformed history backup failed: %s", exc)


def load_kld_visual_history(path: str | Path = KLD_VISUAL_HISTORY_PATH) -> list[dict[str, Any]]:
    history_path = Path(path)
    if not history_path.exists():
        return []
    try:
        data = json.loads(history_path.read_text("utf-8"))
    except Exception as exc:
        logging.warning("KLD visual history read failed: %s", exc)
        _backup_malformed_history(history_path)
        return []
    if not isinstance(data, list):
        logging.warning("KLD visual history is not a list: %s", history_path)
        _backup_malformed_history(history_path)
        return []
    return [entry for entry in data if isinstance(entry, dict)]


def save_kld_visual_history(entries: list[dict[str, Any]], path: str | Path = KLD_VISUAL_HISTORY_PATH) -> None:
    history_path = Path(path)
    history_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = history_path.with_suffix(history_path.suffix + ".tmp")
    tmp.write_text(json.dumps(entries, ensure_ascii=False, indent=2, sort_keys=True), "utf-8")
    tmp.replace(history_path)


def _hamming_hex(left: str, right: str) -> int:
    try:
        return (int(left, 16) ^ int(right, 16)).bit_count()
    except Exception:
        return 10**9


def hamming_distance_hex(left: str | None, right: str | None) -> int | None:
    if not left or not right:
        return None
    return _hamming_hex(left, right)


def pillow_available() -> bool:
    try:
        import PIL  # noqa: F401

        return True
    except Exception:
        return False


def ensure_pillow_for_visual_dedup() -> bool:
    available = pillow_available()
    if not available:
        logging.error("KLD visual near-duplicate detection unavailable: Pillow missing.")
    return available


def _dhash_from_pixels(pixels: list[int], width: int, height: int, *, hash_size: int = 8) -> str:
    if width <= 0 or height <= 0 or len(pixels) < width * height:
        raise ValueError("invalid pixel buffer")
    target_w = hash_size + 1
    target_h = hash_size
    sample: list[int] = []
    for y in range(target_h):
        src_y = min(height - 1, int((y + 0.5) * height / target_h))
        for x in range(target_w):
            src_x = min(width - 1, int((x + 0.5) * width / target_w))
            sample.append(pixels[src_y * width + src_x])

    bits: list[str] = []
    for y in range(target_h):
        row = y * target_w
        for x in range(hash_size):
            bits.append("1" if sample[row + x] > sample[row + x + 1] else "0")
    return f"{int(''.join(bits), 2):0{hash_size * hash_size // 4}x}"


def _read_ppm_or_pgm(path: Path) -> tuple[list[int], int, int] | None:
    data = path.read_bytes()
    index = 0

    def token() -> bytes:
        nonlocal index
        while index < len(data):
            byte = data[index]
            if byte == 35:
                while index < len(data) and data[index] not in b"\r\n":
                    index += 1
            elif chr(byte).isspace():
                index += 1
            else:
                break
        start = index
        while index < len(data) and not chr(data[index]).isspace():
            index += 1
        return data[start:index]

    magic = token()
    if magic not in {b"P5", b"P6"}:
        return None
    try:
        width = int(token())
        height = int(token())
        max_value = int(token())
    except ValueError:
        return None
    if width <= 0 or height <= 0 or max_value <= 0 or max_value > 255:
        return None
    while index < len(data) and chr(data[index]).isspace():
        index += 1
        break
    raw = data[index:]
    expected = width * height * (3 if magic == b"P6" else 1)
    if len(raw) < expected:
        return None
    pixels: list[int] = []
    if magic == b"P5":
        pixels = [int(value) for value in raw[: width * height]]
    else:
        for offset in range(0, expected, 3):
            r, g, b = raw[offset], raw[offset + 1], raw[offset + 2]
            pixels.append((299 * r + 587 * g + 114 * b) // 1000)
    return pixels, width, height


def dhash_file(path: str | Path, *, hash_size: int = 8) -> str | None:
    image_path = Path(path)
    try:
        from PIL import Image, ImageOps  # type: ignore

        with Image.open(image_path) as image:
            image = ImageOps.grayscale(image)
            try:
                resample = Image.Resampling.LANCZOS
            except AttributeError:  # pragma: no cover
                resample = Image.LANCZOS
            image = image.resize((hash_size + 1, hash_size), resample)
            values = list(image.getdata())
        return _dhash_from_pixels(values, hash_size + 1, hash_size, hash_size=hash_size)
    except Exception:
        ppm = _read_ppm_or_pgm(image_path)
        if ppm is None:
            logging.error("KLD visual near-duplicate detection unavailable: Pillow missing.")
            return None
        pixels, width, height = ppm
        return _dhash_from_pixels(pixels, width, height, hash_size=hash_size)


def evaluate_kld_visual_candidate(
    image_path: str | Path,
    *,
    date_value: str,
    target_date: str,
    post_type: str,
    scene_family: str,
    composition: str,
    prompt_version: str,
    history_path: str | Path = KLD_VISUAL_HISTORY_PATH,
    current_date: date | None = None,
    threshold: int = KLD_VISUAL_DHASH_THRESHOLD,
) -> KldVisualDuplicateResult:
    current = current_date or _parse_date(target_date) or _parse_date(date_value) or _today()
    history = load_kld_visual_history(history_path)
    digest = sha256_file(image_path)
    perceptual = dhash_file(image_path)

    for entry in history:
        if not _within_days(entry, current, KLD_VISUAL_EXACT_DAYS):
            continue
        if str(entry.get("sha256") or "") == digest:
            return KldVisualDuplicateResult(
                accepted=False,
                reason="exact_duplicate",
                sha256=digest,
                perceptual_hash=perceptual,
                matched_entry=entry,
            )

    min_distance: int | None = None
    nearest_entry: dict[str, Any] | None = None
    if perceptual:
        for entry in history:
            if not _within_days(entry, current, KLD_VISUAL_NEAR_DAYS):
                continue
            previous_hash = str(entry.get("perceptual_hash") or "")
            if not previous_hash:
                continue
            distance = _hamming_hex(perceptual, previous_hash)
            if min_distance is None or distance < min_distance:
                min_distance = distance
                nearest_entry = entry
        if min_distance is not None and min_distance <= threshold:
            return KldVisualDuplicateResult(
                accepted=False,
                reason="near_duplicate",
                sha256=digest,
                perceptual_hash=perceptual,
                min_distance=min_distance,
                matched_entry=nearest_entry,
            )

    return KldVisualDuplicateResult(
        accepted=True,
        reason="accepted",
        sha256=digest,
        perceptual_hash=perceptual,
        min_distance=min_distance,
        matched_entry=nearest_entry,
    )


def record_kld_visual_publication(
    *,
    date_value: str,
    target_date: str,
    post_type: str,
    image_path: str | Path,
    scene_family: str,
    composition: str,
    prompt_version: str,
    cache_key: str,
    style_name: str,
    history_path: str | Path = KLD_VISUAL_HISTORY_PATH,
) -> dict[str, Any]:
    current = _parse_date(target_date) or _parse_date(date_value) or _today()
    entries = [
        entry
        for entry in load_kld_visual_history(history_path)
        if _within_days(entry, current, 45)
    ]
    entry = {
        "date": date_value,
        "target_date": target_date,
        "post_type": post_type,
        "sha256": sha256_file(image_path),
        "perceptual_hash": dhash_file(image_path),
        "scene_family": scene_family,
        "composition": composition,
        "prompt_version": prompt_version,
        "cache_key": cache_key,
        "style_name": style_name,
        "path": str(Path(image_path)),
    }
    dedup_key = (entry["date"], entry["post_type"], entry["sha256"])
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for existing in entries:
        key = (
            str(existing.get("date") or ""),
            str(existing.get("post_type") or ""),
            str(existing.get("sha256") or ""),
        )
        if key == dedup_key:
            continue
        if key in seen:
            continue
        seen.add(key)
        merged.append(existing)
    merged.append(entry)
    save_kld_visual_history(merged, history_path)
    return entry


__all__ = [
    "KLD_VISUAL_DHASH_THRESHOLD",
    "KLD_VISUAL_EXACT_DAYS",
    "KLD_VISUAL_HISTORY_PATH",
    "KLD_VISUAL_HISTORY_PROD_PATH",
    "KLD_VISUAL_HISTORY_TEST_PATH",
    "KLD_VISUAL_NEAR_DAYS",
    "KldVisualDuplicateResult",
    "dhash_file",
    "ensure_pillow_for_visual_dedup",
    "evaluate_kld_visual_candidate",
    "hamming_distance_hex",
    "kld_visual_history_path",
    "load_kld_visual_history",
    "pillow_available",
    "record_kld_visual_publication",
    "save_kld_visual_history",
    "sha256_file",
]
