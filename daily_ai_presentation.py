#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic full-bleed branded presentation for accepted KLD AI visuals."""

from __future__ import annotations

from datetime import date
import hashlib
from pathlib import Path
import re
from typing import Iterable

from PIL import Image, ImageDraw, ImageFont, ImageOps, PngImagePlugin


PRESENTATION_VERSION = "kld_ai_primary_branded_v2_full_bleed"
CANVAS_SIZE = (1080, 1350)
# Telegram crops the top of tall photos in the chat preview, so the top 10% of
# the canvas carries no header panel or text. Header geometry starts below it.
_TELEGRAM_SAFE_TOP = 135
_TITLE_PANEL = (36, 150, 710, 316)
_TITLE_SAFE = (72, 170, 676, 232)
_BRAND_ORIGIN = (_TITLE_SAFE[0], 247)
_DATE_PANEL = (866, 154, 1040, 314)
_FACT_PANEL = (36, 905, 770, 1248)
_FACT_SAFE = (78, 950, 730, 1208)
_GLASS_FILL = (226, 236, 240, 158)
_GLASS_OUTLINE = (248, 251, 252, 222)
_GLASS_SHADOW = (0, 0, 0, 42)
_TEXT_DARK = (13, 32, 47)
_TEXT_STROKE = (247, 250, 250)


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
        box = draw.textbbox((0, 0), candidate, font=font, stroke_width=1)
        if box[2] - box[0] <= width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _fit_single_line(
    draw: ImageDraw.ImageDraw,
    text: str,
    safe_box: tuple[int, int, int, int],
    *,
    maximum_size: int,
    minimum_size: int,
) -> tuple[ImageFont.FreeTypeFont, int, tuple[int, int], tuple[int, int, int, int]]:
    max_width = safe_box[2] - safe_box[0]
    max_height = safe_box[3] - safe_box[1]
    for size in range(maximum_size, minimum_size - 1, -1):
        font = _font(size, bold=True)
        measured = draw.textbbox((0, 0), text, font=font, stroke_width=1)
        width = measured[2] - measured[0]
        height = measured[3] - measured[1]
        if width > max_width or height > max_height:
            continue
        origin = (
            safe_box[0] - measured[0],
            safe_box[1] + (max_height - height) // 2 - measured[1],
        )
        bbox = draw.textbbox(origin, text, font=font, stroke_width=1)
        return font, size, origin, bbox
    raise RuntimeError(f"KLD AI presentation headline does not fit safe-zone: {text!r}")


def _fit_fact_layout(
    draw: ImageDraw.ImageDraw,
    facts: list[str],
    safe_box: tuple[int, int, int, int],
) -> tuple[ImageFont.FreeTypeFont, int, list[list[str]], int, int, int]:
    max_width = safe_box[2] - safe_box[0]
    max_height = safe_box[3] - safe_box[1]
    for size in range(35, 22, -1):
        font = _font(size, bold=True)
        wrapped = [_wrap(draw, fact, font, max_width) for fact in facts]
        if any(len(lines) > 2 for lines in wrapped):
            continue
        line_gap = max(5, round(size * 0.18))
        fact_gap = max(12, round(size * 0.48))
        total_height = 0
        for lines in wrapped:
            for line_index, line in enumerate(lines):
                measured = draw.textbbox((0, 0), line, font=font, stroke_width=1)
                total_height += measured[3] - measured[1]
                if line_index + 1 < len(lines):
                    total_height += line_gap
        total_height += fact_gap * max(0, len(wrapped) - 1)
        if total_height <= max_height:
            return font, size, wrapped, line_gap, fact_gap, total_height
    raise RuntimeError("KLD AI presentation facts do not fit full-bleed safe-zone")


def _require_telegram_safe(label: str, box: Iterable[int]) -> None:
    top = list(box)[1]
    if top < _TELEGRAM_SAFE_TOP:
        raise RuntimeError(
            f"KLD AI presentation {label} enters Telegram preview unsafe zone: top={top}"
        )


def _glass_panel(
    overlay: Image.Image,
    box: tuple[int, int, int, int],
    *,
    radius: int,
) -> None:
    draw = ImageDraw.Draw(overlay)
    shadow = (box[0] + 4, box[1] + 6, box[2] + 4, box[3] + 6)
    draw.rounded_rectangle(shadow, radius=radius, fill=_GLASS_SHADOW)
    draw.rounded_rectangle(
        box,
        radius=radius,
        fill=_GLASS_FILL,
        outline=_GLASS_OUTLINE,
        width=2,
    )


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

    with Image.open(source) as opened:
        canvas_rgba = ImageOps.fit(
            opened.convert("RGBA"),
            CANVAS_SIZE,
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        )

    clean_facts = [str(item).strip() for item in facts if str(item).strip()][:3]
    overlay = Image.new("RGBA", CANVAS_SIZE, (0, 0, 0, 0))
    _glass_panel(overlay, _TITLE_PANEL, radius=34)
    _glass_panel(overlay, _DATE_PANEL, radius=32)
    if clean_facts:
        _glass_panel(overlay, _FACT_PANEL, radius=34)
    canvas = Image.alpha_composite(canvas_rgba, overlay).convert("RGB")
    draw = ImageDraw.Draw(canvas)

    title = str(headline).strip()
    title_font, title_font_size, title_origin, title_bbox = _fit_single_line(
        draw,
        title,
        _TITLE_SAFE,
        maximum_size=46,
        minimum_size=30,
    )
    draw.text(
        title_origin,
        title,
        font=title_font,
        fill=_TEXT_DARK,
        stroke_width=1,
        stroke_fill=_TEXT_STROKE,
    )
    brand_font = _font(22)
    brand_origin = _BRAND_ORIGIN
    draw.text(
        brand_origin,
        str(branding).strip(),
        font=brand_font,
        fill=_TEXT_DARK,
    )
    brand_bbox = draw.textbbox(brand_origin, str(branding).strip(), font=brand_font)
    if brand_bbox[2] > _TITLE_PANEL[2] - 24 or brand_bbox[3] > _TITLE_PANEL[3] - 18:
        raise RuntimeError("KLD AI presentation branding escapes title panel")

    date_text = _date_label(date_value)
    date_font = _font(30, bold=True)
    date_box = draw.textbbox((0, 0), date_text, font=date_font, stroke_width=1)
    date_x = _DATE_PANEL[0] + (_DATE_PANEL[2] - _DATE_PANEL[0] - (date_box[2] - date_box[0])) // 2
    date_y = _DATE_PANEL[1] + (_DATE_PANEL[3] - _DATE_PANEL[1] - (date_box[3] - date_box[1])) // 2
    date_origin = (date_x - date_box[0], date_y - date_box[1])
    draw.text(
        date_origin,
        date_text,
        font=date_font,
        fill=_TEXT_DARK,
        stroke_width=1,
        stroke_fill=_TEXT_STROKE,
    )
    date_bbox = draw.textbbox(date_origin, date_text, font=date_font, stroke_width=1)

    for label, box in (
        ("title panel", _TITLE_PANEL),
        ("date panel", _DATE_PANEL),
        ("title", title_bbox),
        ("branding", brand_bbox),
        ("date", date_bbox),
    ):
        _require_telegram_safe(label, box)

    fact_layout: list[dict[str, object]] = []
    fact_font_size = 0
    if clean_facts:
        fact_font, fact_font_size, wrapped_facts, line_gap, fact_gap, total_height = _fit_fact_layout(
            draw,
            clean_facts,
            _FACT_SAFE,
        )
        y = _FACT_SAFE[1] + max(0, (_FACT_SAFE[3] - _FACT_SAFE[1] - total_height) // 2)
        for fact_index, (fact, lines) in enumerate(zip(clean_facts, wrapped_facts)):
            origins: list[list[int]] = []
            bboxes: list[list[int]] = []
            for line_index, line in enumerate(lines):
                measured = draw.textbbox((0, 0), line, font=fact_font, stroke_width=1)
                origin = (_FACT_SAFE[0] - measured[0], y - measured[1])
                draw.text(
                    origin,
                    line,
                    font=fact_font,
                    fill=_TEXT_DARK,
                    stroke_width=1,
                    stroke_fill=_TEXT_STROKE,
                )
                bbox = draw.textbbox(origin, line, font=fact_font, stroke_width=1)
                origins.append([origin[0], origin[1]])
                bboxes.append(list(bbox))
                y = bbox[3]
                if line_index + 1 < len(lines):
                    y += line_gap
            fact_layout.append(
                {
                    "source_fact": fact,
                    "lines": lines,
                    "origins": origins,
                    "bboxes": bboxes,
                }
            )
            if fact_index + 1 < len(wrapped_facts):
                y += fact_gap

    output = Path(output_path).with_suffix(".png")
    output.parent.mkdir(parents=True, exist_ok=True)
    source_sha256 = _sha256(source)
    info = PngImagePlugin.PngInfo()
    info.add_text("presentation_version", PRESENTATION_VERSION)
    info.add_text("layout_mode", "full_bleed_glass")
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
        "layout_mode": "full_bleed_glass",
        "source_sha256": source_sha256,
        "published_sha256": published_sha256,
        "headline": str(headline),
        "facts": clean_facts,
        "title_panel_bbox": list(_TITLE_PANEL),
        "date_panel_bbox": list(_DATE_PANEL),
        "facts_panel_bbox": list(_FACT_PANEL) if clean_facts else None,
        "title_layout": {
            "origin": list(title_origin),
            "bbox": list(title_bbox),
            "font_size": title_font_size,
        },
        "date_layout": {
            "origin": list(date_origin),
            "bbox": list(date_bbox),
        },
        "fact_font_size": fact_font_size,
        "fact_layout": fact_layout,
    }


__all__ = ["CANVAS_SIZE", "PRESENTATION_VERSION", "render_branded_ai_presentation"]
