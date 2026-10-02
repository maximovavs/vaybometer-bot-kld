#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic branded presentation for accepted daily AI visuals."""

from __future__ import annotations

from datetime import date
import hashlib
from pathlib import Path
import re
from typing import Iterable

from PIL import Image, ImageDraw, ImageFont, ImageOps, PngImagePlugin


PRESENTATION_VERSION = "kld_ai_primary_branded_v1"
CANVAS_SIZE = (1080, 1350)
_IMAGE_BOX = (40, 190, 1040, 900)
_HEADER_FILL = (9, 31, 46)
_FACT_FILL = (244, 248, 248)
_TEXT_DARK = (17, 42, 58)
_TEXT_LIGHT = (248, 251, 250)


def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    candidates = (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
    )
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            continue
    return ImageFont.truetype("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf", size=size)


def _sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _date_label(value: str) -> str:
    raw = str(value or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        return date.fromisoformat(raw).strftime("%d.%m")
    match = re.fullmatch(r"(\d{2})\.(\d{2})\.\d{4}", raw)
    if match:
        return f"{match.group(1)}.{match.group(2)}"
    return raw[:10]


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in str(text).split():
        candidate = f"{current} {word}".strip()
        box = draw.textbbox((0, 0), candidate, font=font)
        if box[2] - box[0] <= width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def render_branded_ai_presentation(
    source_image_path: str | Path,
    *,
    headline: str,
    date_value: str,
    facts: Iterable[str],
    branding: str,
    output_path: str | Path,
) -> dict[str, object]:
    source = Path(source_image_path)
    if not source.exists():
        raise RuntimeError(f"accepted provider image does not exist: {source}")

    canvas = Image.new("RGB", CANVAS_SIZE, _FACT_FILL)
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((0, 0, CANVAS_SIZE[0], 170), fill=_HEADER_FILL)

    title_font = _font(48, bold=True)
    brand_font = _font(21)
    date_font = _font(30, bold=True)
    draw.text((55, 45), str(headline).strip(), font=title_font, fill=_TEXT_LIGHT)
    draw.text((58, 112), str(branding).strip(), font=brand_font, fill=(205, 225, 233))
    date_text = _date_label(date_value)
    date_box = draw.textbbox((0, 0), date_text, font=date_font)
    draw.rounded_rectangle((865, 34, 1030, 135), radius=24, fill=(229, 239, 241))
    draw.text(
        (947 - (date_box[2] - date_box[0]) // 2, 84 - (date_box[3] - date_box[1]) // 2),
        date_text,
        font=date_font,
        fill=_TEXT_DARK,
    )

    with Image.open(source) as opened:
        visual = ImageOps.fit(
            opened.convert("RGB"),
            (_IMAGE_BOX[2] - _IMAGE_BOX[0], _IMAGE_BOX[3] - _IMAGE_BOX[1]),
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        )
    canvas.paste(visual, (_IMAGE_BOX[0], _IMAGE_BOX[1]))
    draw.rectangle(_IMAGE_BOX, outline=(215, 226, 229), width=2)

    clean_facts = [str(item).strip() for item in facts if str(item).strip()][:3]
    y = 950
    fact_layout: list[dict[str, object]] = []
    for fact in clean_facts:
        chosen_font = _font(36, bold=True)
        lines = _wrap(draw, fact, chosen_font, 920)
        if len(lines) > 2:
            chosen_font = _font(30, bold=True)
            lines = _wrap(draw, fact, chosen_font, 920)
        lines = lines[:2]
        line_boxes: list[list[int]] = []
        card_top = y
        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=chosen_font)
            draw.text((78, y), line, font=chosen_font, fill=_TEXT_DARK)
            line_boxes.append([78, y, 78 + bbox[2] - bbox[0], y + bbox[3] - bbox[1]])
            y += 47 if chosen_font.size >= 36 else 41
        card_bottom = y + 12
        draw.rounded_rectangle(
            (55, card_top - 12, 1025, card_bottom),
            radius=18,
            outline=(214, 225, 228),
            width=2,
        )
        fact_layout.append({"source_fact": fact, "lines": lines, "bboxes": line_boxes})
        y = card_bottom + 18
        if y > 1330:
            raise RuntimeError("AI presentation facts exceed safe vertical area")

    output = Path(output_path).with_suffix(".png")
    output.parent.mkdir(parents=True, exist_ok=True)
    source_sha256 = _sha256(source)
    info = PngImagePlugin.PngInfo()
    info.add_text("presentation_version", PRESENTATION_VERSION)
    info.add_text("source_sha256", source_sha256)
    info.add_text("headline", str(headline))
    info.add_text("date", str(date_value))
    canvas.save(output, format="PNG", pnginfo=info, compress_level=6)
    published_sha256 = _sha256(output)
    return {
        "path": str(output),
        "bytes": output.stat().st_size,
        "width": CANVAS_SIZE[0],
        "height": CANVAS_SIZE[1],
        "presentation_version": PRESENTATION_VERSION,
        "source_sha256": source_sha256,
        "published_sha256": published_sha256,
        "headline": str(headline),
        "facts": clean_facts,
        "fact_layout": fact_layout,
    }


__all__ = ["CANVAS_SIZE", "PRESENTATION_VERSION", "render_branded_ai_presentation"]
