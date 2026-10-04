#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic high-contrast weekly cover for Kaliningrad VayboMeter."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any


RENDERER_VERSION = "kld_weekly_cover_v2_portrait"
BRANDING = "VAYBOMETER · KLD"
TITLE = "ВАЙБ НЕДЕЛИ"


def _font(size: int, *, bold: bool = False):
    from PIL import ImageFont

    names = (
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    )
    for name in names:
        if Path(name).exists():
            return ImageFont.truetype(name, size=size)
    return ImageFont.load_default()


def _section_value(text: str, heading: str) -> str:
    lines = [line.strip() for line in str(text or "").splitlines()]
    for index, line in enumerate(lines):
        if line == heading:
            for candidate in lines[index + 1:]:
                if candidate:
                    return candidate
    return ""


def _week_label(text: str, start: date) -> str:
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if line.startswith("🗓 Вайб недели:"):
            return line.split(":", 1)[1].strip()
    return start.isoformat()


def _wrap(draw: Any, text: str, font: Any, max_width: int, max_lines: int = 2) -> list[str]:
    words = str(text or "").split()
    if not words:
        return ["ДАННЫЕ ОБНОВЛЯЮТСЯ"]
    lines: list[str] = []
    current = ""
    for word in words:
        probe = f"{current} {word}".strip()
        box = draw.textbbox((0, 0), probe, font=font)
        if box[2] - box[0] <= max_width:
            current = probe
            continue
        if current:
            lines.append(current)
        current = word
        if len(lines) >= max_lines:
            break
    if current and len(lines) < max_lines:
        lines.append(current)
    if len(lines) == max_lines and len(" ".join(lines).split()) < len(words):
        last = lines[-1]
        while last and draw.textbbox((0, 0), last + "…", font=font)[2] > max_width:
            last = last[:-1].rstrip()
        lines[-1] = last + "…"
    return lines[:max_lines]


def render_weekly_cover(text: str, *, start: date, output_path: str | Path) -> dict[str, Any]:
    from PIL import Image, ImageDraw, PngImagePlugin

    week_label = _week_label(text, start)
    main_fact = _section_value(text, "✨ Главный фон недели")
    weather_fact = _section_value(text, "🌦 Погода")
    sea_fact = _section_value(text, "🌊 Балтика")
    variant = ("baltic_slate", "amber_coast", "north_sea")[start.isocalendar().week % 3]

    palettes = {
        "baltic_slate": ((25, 45, 59), (77, 119, 139), (203, 196, 166)),
        "amber_coast": ((48, 58, 64), (133, 122, 101), (224, 200, 149)),
        "north_sea": ((20, 52, 70), (82, 139, 155), (208, 211, 195)),
    }
    top, middle, bottom = palettes[variant]
    width, height = 1080, 1350
    image = Image.new("RGB", (width, height), top)
    draw = ImageDraw.Draw(image)
    for y in range(height):
        ratio = y / (height - 1)
        if ratio < 0.62:
            local = ratio / 0.62
            color = tuple(round(top[i] * (1 - local) + middle[i] * local) for i in range(3))
        else:
            local = (ratio - 0.62) / 0.38
            color = tuple(round(middle[i] * (1 - local) + bottom[i] * local) for i in range(3))
        draw.line((0, y, width, y), fill=color)

    draw.rectangle((0, 1120, 1080, 1190), fill=(61, 105, 125))
    for x in range(80, 1030, 150):
        draw.arc((x - 170, 1105, x + 230, 1235), 195, 345, fill=(214, 229, 231), width=5)

    draw.rounded_rectangle((64, 58, 1016, 300), radius=42, fill=(17, 31, 41), outline=(232, 239, 238), width=3)
    draw.text((105, 94), BRANDING, font=_font(27, bold=True), fill=(166, 207, 220))
    draw.text((105, 145), TITLE, font=_font(66, bold=True), fill=(250, 251, 247))
    draw.text((108, 234), week_label, font=_font(34), fill=(218, 230, 230))

    cards = (
        ("ГЛАВНОЕ", main_fact),
        ("ПОГОДА", weather_fact),
        ("БАЛТИКА", sea_fact),
    )
    y = 370
    body_font = _font(34, bold=True)
    label_font = _font(24, bold=True)
    for label, fact in cards:
        draw.rounded_rectangle((88, y, 992, y + 190), radius=26, fill=(248, 250, 247), outline=(218, 230, 230), width=2)
        draw.text((125, y + 24), label, font=label_font, fill=(42, 100, 119))
        lines = _wrap(draw, fact, body_font, 815, 2)
        line_y = y + 72
        for line in lines:
            draw.text((125, line_y), line, font=body_font, fill=(18, 40, 52))
            line_y += 46
        y += 220

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    info = PngImagePlugin.PngInfo()
    metadata = {
        "renderer_version": RENDERER_VERSION,
        "region": "kld",
        "week_start": start.isoformat(),
        "week_label": week_label,
        "variant": variant,
        "main_fact": main_fact,
        "weather_fact": weather_fact,
        "sea_fact": sea_fact,
    }
    for key, value in metadata.items():
        info.add_text(key, str(value))
    image.save(output, format="PNG", optimize=True, pnginfo=info)
    metadata.update(path=str(output), width=width, height=height)
    return metadata


__all__ = ["RENDERER_VERSION", "render_weekly_cover"]
