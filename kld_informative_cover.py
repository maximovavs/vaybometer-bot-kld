#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic factual fallback cover for KLD daily publications."""

from __future__ import annotations

import json
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
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_EDITORIAL_LINE_RE = re.compile(
    r"^\W*(?:главный\s+нюанс|нюанс|vaybometer|план|рекомендации|уверенность)"
    r"(?:\s+[^:]{1,32})?\s*:",
    re.IGNORECASE,
)
_STORM_NEGATION_RE = re.compile(
    r"(?:штормов\w*\s+предупрежден\w*\s+нет|"
    r"шторм\w*\s+не\s+ожида\w*|без\s+шторма|"
    r"риск\s+шторма\s+низк\w*|шторм\w*\s+не\s+подтвержд\w*|"
    r"гроз\w*\s+не\s+ожида\w*)",
    re.IGNORECASE,
)
_STORM_UNCERTAIN_RE = re.compile(
    r"(?:риск|вероятност\w*)\s+(?:шторма|гроз\w*)|"
    r"(?:шторм|гроз)\w*[^.!?\n]*(?:провер|уточн|возмож|вероятн|не\s+исключ)",
    re.IGNORECASE,
)
_STORM_RE = re.compile(r"(?:шторм\w*|⛈|гроз\w*)", re.IGNORECASE)
_THUNDERSTORM_RE = re.compile(r"(?:⛈|гроз\w*)", re.IGNORECASE)
_PRECIPITATION_NEGATION_RE = re.compile(
    r"(?:без\s+осадков|дожд\w*\s+не\s+ожида\w*|преимущественно\s+сух\w*|"
    r"вероятност\w*\s+дожд\w*\s+низк\w*|осадк\w*\s+не\s+подтвержд\w*)",
    re.IGNORECASE,
)
_PRECIPITATION_UNCERTAIN_RE = re.compile(
    r"(?:вероятност\w*\s+(?:дожд|осад)\w*|"
    r"(?:дожд|осад)\w*[^.!?\n]*(?:провер|уточн|возмож|вероятн|не\s+исключ))",
    re.IGNORECASE,
)
_RAIN_RE = re.compile(r"(?:🌧|🌦|дожд\w*|лив\w*)", re.IGNORECASE)
_DRIZZLE_RE = re.compile(r"морос\w*", re.IGNORECASE)
_SNOW_RE = re.compile(r"(?:❄|снег\w*)", re.IGNORECASE)
_PRECIPITATION_RE = re.compile(r"осад\w*", re.IGNORECASE)
_STRONG_WIND_RE = re.compile(r"(?:сильн\w*\s+ветер|штормов\w*\s+ветер)", re.IGNORECASE)


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


def _factual_weather_truth(message: str) -> dict[str, bool]:
    """Parse explicit weather facts without promoting editorial advice to observations."""
    explicit_storm = False
    actual_precipitation = False
    rain = False
    drizzle = False
    snow = False
    thunderstorm = False
    strong_wind = False

    for raw_line in message.splitlines():
        line = _HTML_TAG_RE.sub("", raw_line).strip().lower()
        if not line or _EDITORIAL_LINE_RE.match(line):
            continue

        gust_match = _GUST_RE.search(line)
        gust = _number(gust_match.group(1)) if gust_match else None
        if (gust is not None and gust >= 12) or _STRONG_WIND_RE.search(line):
            strong_wind = True

        storm_negated = bool(_STORM_NEGATION_RE.search(line))
        storm_uncertain = bool(_STORM_UNCERTAIN_RE.search(line))
        if not storm_negated and not storm_uncertain:
            if _STORM_RE.search(line):
                explicit_storm = True
            if _THUNDERSTORM_RE.search(line):
                thunderstorm = True

        precipitation_negated = bool(_PRECIPITATION_NEGATION_RE.search(line))
        precipitation_uncertain = bool(_PRECIPITATION_UNCERTAIN_RE.search(line))
        if precipitation_negated or precipitation_uncertain:
            continue

        line_rain = bool(_RAIN_RE.search(line))
        line_drizzle = bool(_DRIZZLE_RE.search(line))
        line_snow = bool(_SNOW_RE.search(line))
        line_precipitation = bool(_PRECIPITATION_RE.search(line))
        rain = rain or line_rain
        drizzle = drizzle or line_drizzle
        snow = snow or line_snow
        actual_precipitation = actual_precipitation or any(
            (line_rain, line_drizzle, line_snow, line_precipitation)
        )

    return {
        "explicit_storm": explicit_storm,
        "actual_precipitation": actual_precipitation,
        "rain": rain,
        "drizzle": drizzle,
        "snow": snow,
        "thunderstorm": thunderstorm,
        "strong_wind": strong_wind,
    }


def _weather_flags(
    message: str,
    visibility_context: Mapping[str, Any] | None,
    *,
    post_type: str,
) -> dict[str, bool | str]:
    lowered = message.lower()
    condition = str((visibility_context or {}).get("visibility_condition") or "").strip().lower()
    factual = _factual_weather_truth(message)
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
    if weather_main == "unknown" and factual["actual_precipitation"]:
        if factual["rain"]:
            weather_main = "rain"
        elif factual["drizzle"]:
            weather_main = "drizzle"
        elif factual["snow"]:
            weather_main = "snow"
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
        "storm": factual["explicit_storm"],
        **factual,
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
    if flags["explicit_storm"]:
        facts.append("ШТОРМОВОЕ ПРЕДУПРЕЖДЕНИЕ")
    elif flags["snow"]:
        facts.append("СНЕГ МЕСТАМИ")
    elif flags["drizzle"]:
        facts.append("МОРОСЬ МЕСТАМИ")
    elif flags["rain"]:
        facts.append("ДОЖДЬ МЕСТАМИ")
    elif flags["actual_precipitation"]:
        facts.append("ОСАДКИ МЕСТАМИ")
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
    if weather["explicit_storm"]:
        return (32, 46, 59), (73, 91, 105), (190, 204, 211)
    if weather["actual_precipitation"]:
        return (80, 105, 122), (134, 154, 166), (220, 229, 233)
    if weather["fog"] or weather["mixed_visibility"]:
        return (184, 190, 190), (216, 220, 216), (240, 239, 229)
    if weather["dust_haze"]:
        return (167, 156, 135), (205, 194, 171), (232, 224, 204)
    return (111, 154, 181), (182, 204, 214), (231, 228, 207)


def _draw_weather_graphics(
    draw: Any,
    *,
    width: int,
    weather: Mapping[str, Any],
) -> dict[str, Any]:
    """Draw visible factual motifs and return coordinates for pixel regressions."""
    rain_color = (203, 220, 228)
    lightning_color = (235, 226, 170)
    wind_color = (220, 230, 232)
    rain_lines: list[tuple[int, int, int, int]] = []
    lightning_line: tuple[tuple[int, int], ...] = ()
    wind_arcs: list[tuple[int, int, int, int]] = []

    if weather["rain"]:
        for x in range(30, width, 65):
            y = 610 + (x % 130)
            segment = (x, y, x - 24, y + 90)
            rain_lines.append(segment)
            draw.line(segment, fill=rain_color, width=3)
    if weather["explicit_storm"]:
        lightning_line = ((850, 615), (805, 710), (850, 700), (790, 825))
        draw.line(lightning_line, fill=lightning_color, width=8)
    if weather["strong_wind"] and not weather["rain"]:
        for y in (625, 690, 755):
            arc = (700, y, 1040, y + 95)
            wind_arcs.append(arc)
            draw.arc(arc, 180, 350, fill=wind_color, width=4)

    return {
        "rain_lines": rain_lines,
        "rain_color": rain_color,
        "lightning_line": lightning_line,
        "lightning_color": lightning_color,
        "wind_arcs": wind_arcs,
        "wind_color": wind_color,
    }


def render_kld_informative_cover(
    message: str,
    *,
    post_type: str,
    visibility_context: Mapping[str, Any] | None = None,
    output_path: str | Path = "outputs/kld_informative_cover.png",
) -> dict[str, Any]:
    """Render a deterministic 1080px factual card with no external calls."""
    from PIL import Image, ImageDraw, PngImagePlugin

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
    if weather["fog"] or weather["mixed_visibility"]:
        draw.rounded_rectangle((0, 500, width, 760), radius=80, fill=(224, 225, 218))
    graphics = _draw_weather_graphics(draw, width=width, weather=weather)

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
    metadata["graphics"] = graphics
    metadata["rain_graphics"] = bool(graphics["rain_lines"])
    metadata["lightning_graphics"] = bool(graphics["lightning_line"])
    png_info = PngImagePlugin.PngInfo()
    png_info.add_text("renderer_version", RENDERER_VERSION)
    png_info.add_text("weather_flags", json.dumps(weather, ensure_ascii=False, sort_keys=True))
    png_info.add_text("graphics", json.dumps(graphics, ensure_ascii=False, sort_keys=True))
    png_info.add_text("explicit_storm", str(bool(weather["explicit_storm"])).lower())
    png_info.add_text("actual_precipitation", str(bool(weather["actual_precipitation"])).lower())
    png_info.add_text("rain_graphics", str(metadata["rain_graphics"]).lower())
    png_info.add_text("lightning_graphics", str(metadata["lightning_graphics"]).lower())
    image.save(temporary, format="PNG", optimize=True, pnginfo=png_info)
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
