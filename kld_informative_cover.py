#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic factual fallback cover for KLD daily publications."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping


RENDERER_VERSION = "kld_local_informative_cover_v1"
_NUMBER = r"-?\d+(?:[.,]\d+)?"
_CITY_TEMPERATURE_RE = re.compile(
    rf"Калининград[^\n]*?({_NUMBER})\s*/\s*({_NUMBER})\s*°?C?",
    re.IGNORECASE,
)
_WIND_RANGE_RE = re.compile(rf"({_NUMBER})\s*[–—-]\s*({_NUMBER})\s*м/с", re.IGNORECASE)
_WIND_RE = re.compile(rf"(?:ветер\s*:?)?\s*({_NUMBER})\s*м/с", re.IGNORECASE)
_GUST_RE = re.compile(rf"порыв\w*\s*(?:до\s*)?({_NUMBER})\s*м/с", re.IGNORECASE)
_SEA_RE = re.compile(rf"🌊\s*({_NUMBER})\s*°?C", re.IGNORECASE)
_WAVE_RE = re.compile(rf"волна\s*({_NUMBER})\s*м", re.IGNORECASE)
_DATE_RE = re.compile(r"\b(\d{2}\.\d{2}\.\d{4})\b")


def _number(value: object) -> float | None:
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _fmt(value: float, *, signed: bool = False) -> str:
    rendered = f"{value:.1f}".rstrip("0").rstrip(".")
    if signed and value > 0:
        return "+" + rendered
    return rendered


def _first_match(pattern: re.Pattern[str], text: str) -> tuple[float, ...]:
    match = pattern.search(text)
    if not match:
        return ()
    values = tuple(_number(item) for item in match.groups())
    return tuple(value for value in values if value is not None)


def _weather_flags(
    message: str,
    visibility_context: Mapping[str, Any] | None,
    *,
    post_type: str,
) -> dict[str, bool | str]:
    lowered = message.lower()
    condition = str((visibility_context or {}).get("visibility_condition") or "").strip().lower()
    explicit_storm = any(token in lowered for token in ("штормовое предупреждение", "шторм", "⛈", "гроза"))
    weather_main = "unknown"
    try:
        from visual_context_kld import build_visual_context

        weather_main = build_visual_context(
            message,
            post_type=post_type,
            visibility_context=visibility_context,
        ).weather_main
    except Exception:
        # The fallback cover must remain available even if optional visual parsing fails.
        weather_main = "unknown"
    if weather_main == "unknown":
        weather_main = "rain" if any(token in message for token in ("🌧", "🌦")) else "unknown"
    rain = explicit_storm or weather_main in {"rain", "drizzle", "storm"}
    fog = condition in {"dense_fog", "fog", "mist"}
    dust = condition == "dust_haze"
    mixed = condition == "mixed_visibility"
    reduced = condition == "reduced_visibility"
    if not condition:
        fog = "туман" in lowered and "без туман" not in lowered
        dust = "сухая дымка" in lowered
        mixed = "смесь влажной дымки" in lowered
        reduced = "видимость" in lowered and "снижен" in lowered
    return {
        "condition": condition or "clear",
        "weather_main": weather_main,
        "storm": explicit_storm,
        "rain": rain,
        "drizzle": weather_main == "drizzle",
        "fog": fog,
        "dust_haze": dust,
        "mixed_visibility": mixed,
        "reduced_visibility": reduced,
    }


def extract_kld_cover_facts(
    message: str,
    *,
    post_type: str,
    visibility_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Extract at most three display facts without turning thresholds into measurements."""
    flags = _weather_flags(message, visibility_context, post_type=post_type)
    temperatures = _first_match(_CITY_TEMPERATURE_RE, message)

    wind_line = next(
        (line for line in message.splitlines() if "м/с" in line and ("Калининград" in line or "ветер" in line.lower())),
        "",
    )
    wind_range = _first_match(_WIND_RANGE_RE, wind_line)
    wind = _first_match(_WIND_RE, wind_line) if not wind_range else ()
    gust = _first_match(_GUST_RE, wind_line)
    sea = _first_match(_SEA_RE, message)
    wave = _first_match(_WAVE_RE, message)
    visibility_actual = None
    for key in ("current_visibility_m", "morning_min_visibility_m", "reported_visibility_m"):
        visibility_actual = _number((visibility_context or {}).get(key))
        if visibility_actual is not None:
            break

    facts: list[str] = []
    if flags["storm"]:
        facts.append("ШТОРМОВОЕ ПРЕДУПРЕЖДЕНИЕ")
    elif flags["drizzle"]:
        facts.append("МОРОСЬ МЕСТАМИ")
    elif flags["rain"]:
        facts.append("ДОЖДЬ МЕСТАМИ")
    elif flags["fog"]:
        facts.append("ТУМАН УТРОМ")
    elif flags["dust_haze"]:
        facts.append("СУХАЯ ДЫМКА")
    elif flags["mixed_visibility"]:
        facts.append("СМЕШАННАЯ ДЫМКА")
    elif flags["reduced_visibility"]:
        facts.append("ВИДИМОСТЬ УТРОМ СНИЖЕНА")

    if len(temperatures) == 2:
        facts.append(f"{_fmt(temperatures[0], signed=True)}° / {_fmt(temperatures[1], signed=True)}°")

    if wind or len(wind_range) == 2:
        wind_value = (
            f"{_fmt(wind_range[0])}–{_fmt(wind_range[1])}"
            if len(wind_range) == 2
            else _fmt(wind[0])
        )
        wind_fact = f"ВЕТЕР {wind_value} М/С"
        if gust:
            wind_fact += f" · ПОРЫВЫ {_fmt(gust[0])} М/С"
        facts.append(wind_fact)
    elif sea or wave:
        parts: list[str] = []
        if sea:
            parts.append(f"ВОДА {_fmt(sea[0])}°C")
        if wave:
            parts.append(f"ВОЛНА {_fmt(wave[0])} М")
        facts.append("БАЛТИКА · " + " · ".join(parts))

    facts = facts[:3]
    date_match = _DATE_RE.search(message)
    return {
        "renderer_version": RENDERER_VERSION,
        "post_type": post_type,
        "title": "КАЛИНИНГРАД ЗАВТРА" if post_type == "evening" else "КАЛИНИНГРАД СЕГОДНЯ",
        "date": date_match.group(1) if date_match else "",
        "facts": facts,
        "weather": flags,
        "actual_values": {
            "temp_max_c": temperatures[0] if len(temperatures) == 2 else None,
            "temp_min_c": temperatures[1] if len(temperatures) == 2 else None,
            "wind_mps": wind[0] if wind else None,
            "wind_min_mps": wind_range[0] if len(wind_range) == 2 else None,
            "wind_max_mps": wind_range[1] if len(wind_range) == 2 else None,
            "gust_mps": gust[0] if gust else None,
            "sea_temp_c": sea[0] if sea else None,
            "wave_m": wave[0] if wave else None,
            "visibility_m": visibility_actual,
        },
    }


def _font(size: int, *, bold: bool = False):
    from PIL import ImageFont

    names = (
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    )
    for name in names:
        if Path(name).exists():
            return ImageFont.truetype(name, size=size)
    return ImageFont.load_default()


def _palette(metadata: Mapping[str, Any]) -> tuple[tuple[int, int, int], tuple[int, int, int], tuple[int, int, int]]:
    weather = metadata["weather"]
    if weather["storm"]:
        return (32, 46, 59), (73, 91, 105), (190, 204, 211)
    if weather["rain"]:
        return (80, 105, 122), (134, 154, 166), (220, 229, 233)
    if weather["fog"] or weather["mixed_visibility"]:
        return (184, 190, 190), (216, 220, 216), (240, 239, 229)
    if weather["dust_haze"]:
        return (167, 156, 135), (205, 194, 171), (232, 224, 204)
    return (111, 154, 181), (182, 204, 214), (231, 228, 207)


def render_kld_informative_cover(
    message: str,
    *,
    post_type: str,
    visibility_context: Mapping[str, Any] | None = None,
    output_path: str | Path = "outputs/kld_informative_cover.png",
) -> dict[str, Any]:
    """Render a deterministic 1080px factual card with no external calls."""
    from PIL import Image, ImageDraw

    metadata = extract_kld_cover_facts(
        message,
        post_type=post_type,
        visibility_context=visibility_context,
    )
    top, middle, sand = _palette(metadata)
    width = height = 1080
    image = Image.new("RGB", (width, height), top)
    draw = ImageDraw.Draw(image)

    for y in range(height):
        ratio = y / (height - 1)
        if ratio < 0.66:
            local = ratio / 0.66
            color = tuple(round(top[i] * (1 - local) + middle[i] * local) for i in range(3))
        else:
            local = (ratio - 0.66) / 0.34
            color = tuple(round(middle[i] * (1 - local) + sand[i] * local) for i in range(3))
        draw.line((0, y, width, y), fill=color)

    # Baltic horizon and restrained natural texture.
    draw.rectangle((0, 665, width, 850), fill=(74, 119, 142))
    for offset in range(0, 360, 32):
        y = 700 + (offset % 120)
        draw.arc((-120 + offset * 3, y, 260 + offset * 3, y + 55), 190, 345, fill=(178, 207, 217), width=4)
    draw.polygon(((0, 850), (240, 795), (520, 850), (810, 800), (1080, 835), (1080, 1080), (0, 1080)), fill=sand)

    weather = metadata["weather"]
    if weather["rain"]:
        for x in range(20, width, 65):
            draw.line((x, 120 + (x % 170), x - 24, 225 + (x % 170)), fill=(203, 220, 228), width=3)
    if weather["storm"]:
        draw.line((835, 135, 790, 250, 838, 240, 785, 370), fill=(235, 226, 170), width=8)
    gust = metadata["actual_values"].get("gust_mps")
    if isinstance(gust, (int, float)) and gust >= 12 and not weather["rain"]:
        for y in (180, 235, 295):
            draw.arc((720, y, 1040, y + 95), 180, 350, fill=(220, 230, 232), width=4)
    if weather["fog"] or weather["mixed_visibility"]:
        draw.rounded_rectangle((0, 500, width, 760), radius=80, fill=(224, 225, 218))

    # Branded information panel; no pseudo-photographic text.
    draw.rounded_rectangle((70, 70, 1010, 590), radius=42, fill=(20, 34, 45), outline=(230, 238, 240), width=3)
    draw.text((115, 112), "VAYBOMETER · KLD", font=_font(28, bold=True), fill=(154, 199, 216))
    draw.text((115, 174), metadata["title"], font=_font(61, bold=True), fill=(247, 249, 246))
    if metadata["date"]:
        draw.text((117, 254), metadata["date"], font=_font(29), fill=(194, 211, 217))

    facts = metadata["facts"] or ["АКТУАЛЬНЫЙ ПРОГНОЗ — В ТЕКСТЕ"]
    y = 326
    for fact in facts:
        draw.rounded_rectangle((112, y - 8, 968, y + 66), radius=18, fill=(42, 62, 74))
        draw.text((142, y + 8), fact, font=_font(31, bold=True), fill=(245, 247, 242))
        y += 82

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".tmp")
    image.save(temporary, format="PNG", optimize=True)
    temporary.replace(output)
    metadata["path"] = str(output)
    metadata["width"] = width
    metadata["height"] = height
    return metadata


__all__ = [
    "RENDERER_VERSION",
    "extract_kld_cover_facts",
    "render_kld_informative_cover",
]
