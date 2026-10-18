#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Opt-in photographic weekly cover. Pure presentation: does not fetch or change forecasts."""
from __future__ import annotations

import hashlib
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any

WIDTH, HEIGHT = 1080, 1350
PHOTO_RENDERER_VERSION = "weekly_photo_v1_candidate"
ASSETS: dict[str, dict[str, str]] = {
    "cyprus": {
        "neutral": "cy_clouds.jpg", "sunny": "cy_sunny.jpg", "sunset": "cy_sunset.jpg",
    },
    "kld": {
        "neutral": "kld_blue_evening.jpg", "wet": "kld_blue_evening.jpg",
        "coast": "kld_baltic_coast.jpg", "golden": "kld_golden_city.jpg",
    },
}
RUSSIAN_MONTHS = (
    "", "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)


_SECTION_HEADINGS = (
    "✨ Главный фон недели", "🌿 Смысл недели", "🌦 Погода", "🌊 Море", "🌊 Балтика",
    "🏄 Вода и спорт", "🏭 Воздух сейчас", "🧲 Космопогода сейчас",
    "🌙 Луна и астроритм (интерпретация)", "↔️ К прошлому недельному прогнозу",
    "✅ Как прожить неделю",
)


def _section(text: str, heading: str) -> str:
    """Stop at another section header: missing weather must never borrow another fact."""
    lines = [line.strip() for line in text.splitlines()]
    for i, line in enumerate(lines):
        if line == heading:
            for candidate in lines[i + 1:]:
                if not candidate:
                    continue
                if candidate in _SECTION_HEADINGS:
                    return ""
                return candidate
            return ""
    return ""


def _section_lines(text: str, heading: str) -> list[str]:
    """Every non-empty line of a section, for safety scans (not for display)."""
    lines = [line.strip() for line in text.splitlines()]
    for i, line in enumerate(lines):
        if line == heading:
            found: list[str] = []
            for candidate in lines[i + 1:]:
                if candidate in _SECTION_HEADINGS:
                    break
                if candidate:
                    found.append(candidate)
            return found
    return []


def _is_partial(text: str) -> bool:
    signal = text.casefold()
    return any(phrase in signal for phrase in (
        "не подтвержд", "пока не собран", "прогноз уточн", "недостаточно данных",
        "данные обновляются", "неполный прогноз", "нет данных",
    ))


def _temperature(weather: str, main_fact: str) -> str:
    # Fail closed whenever the weekly forecast says its own seven-day coverage is not confirmed.
    if _is_partial(weather) or _is_partial(main_fact):
        return "Уточняется"
    match = re.search(r"(?<!\d)(-?\d{1,2})\s*[–—−-]\s*(-?\d{1,2})\s*°\s*[CС]", weather)
    return f"{match.group(1)}–{match.group(2)}°C" if match else "Не указана"


def _sea(sea_line: str) -> str:
    if _is_partial(sea_line):
        return "Уточняется"
    match = re.search(r"(?<!\d)(-?\d{1,2})\s*[–—−-]\s*(-?\d{1,2})\s*°\s*[CС]", sea_line)
    if match:
        return f"{match.group(1)}–{match.group(2)}°C"
    match = re.search(r"(?<!\d)(-?\d{1,2})\s*°\s*[CС]", sea_line)
    return f"{match.group(1)}°C" if match else "Не указана"


def _critical_warning(content: str) -> bool:
    """Preserve genuine safety cues, ignoring only bounded explicit storm negations."""
    # A headline saying "без штормовых предупреждений" does not itself signal
    # a storm.  Remove ONLY explicit storm negations from a detection copy;
    # distinct storm/wave/gust/ice/thunder warnings still force legacy fallback.
    signal = content.casefold()
    storm = r"шторм\w*"
    for pattern in (
        rf"\b(?:без|нет|никаких)\s+{storm}(?:\s+предупрежден\w*)?\b",
        rf"\bотсутству\w*\s+{storm}(?:\s+предупрежден\w*)?\b",
        rf"\bне\s+(?:ожида\w*|прогнозиру\w*|предвид\w*|будет)\s+{storm}\b",
        rf"\b{storm}(?:\s+предупрежден\w*)?\s+(?:нет|отсутству\w*|не\s+(?:ожида\w*|прогнозиру\w*|предвид\w*|будет))\b",
    ):
        signal = re.sub(pattern, " ", signal)
    return bool(re.search(
        r"\b(?:шторм\w*|опасн\w*|порыв\w*|гроз\w*|голол[её]д\w*|метел\w*|"
        r"высоки[ех] волны|не выходить|отложить|запрещен\w*|запрещён\w*)\b",
        signal,
    ))


# Routine advice such as "проверять порывы и направление" is not itself a
# gust warning.  Only that bounded phrase is neutralised; a gust value or any
# other hazard cue on the same line still counts.
_SPORT_ADVICE = re.compile(
    r"\b(?:провер\w*|учитыва\w*|следить\s+за)\s+порыв\w*(?:\s+и\s+направлени\w*)?(?=\s*(?:[.;,]|$))"
)
_SPORT_DANGER = re.compile(
    r"\b(?:сильн\w*\s+(?:ветер|ветр\w*|порыв\w*|волн\w*)|небезопасн\w*|не\s+рекоменд\w*)\b"
)


def _sport_warning(content: str) -> bool:
    """A water/sport line that reports dangerous wind, gusts, waves or unsafe activity."""
    signal = _SPORT_ADVICE.sub(" ", content.casefold())
    return _critical_warning(signal) or bool(_SPORT_DANGER.search(signal))


def _weather_summary(weather: str, main_fact: str) -> str:
    if _is_partial(weather) or _is_partial(main_fact):
        return "Прогноз на 7 дней\nуточняется"
    tail = weather.split(";", 1)[1].strip() if ";" in weather else weather.strip()
    # Never suppress a leading storm/ice warning when splitting off a temperature clause.
    if _critical_warning(weather.split(";", 1)[0]):
        return weather.strip()
    return tail if tail else "Без подтверждённых данных"


def _scenario(region: str, weather: str, main_fact: str) -> str:
    # Backdrop is an illustration, NOT a meteorological observation.
    if _is_partial(weather) or _is_partial(main_fact):
        return "neutral"
    s = f"{main_fact} {weather}".casefold()
    if region == "cyprus":
        if any(x in s for x in ("ясн", "солнечн", "безоблач")):
            return "sunny"
        if any(x in s for x in ("закат", "теплые вечера", "тёплые вечера")):
            return "sunset"
        return "neutral"
    # A negated storm/rain cue must not select a hazardous/wet background.
    # Strip only bounded explicit negations; a separate positive hazard still wins.
    storm = r"(?:шторм\w*|сильн\w*\s+(?:ветр\w*|порыв\w*))"
    rain = r"(?:дожд\w*|ливн\w*)"
    for cue in (storm, rain):
        for pattern in (
            rf"\b(?:без|нет|никаких|отсутствуют|отсутствует)\s+{cue}(?:\s+предупрежден\w*)?",
            rf"\bне\s+(?:ожида\w*|прогнозиру\w*|предвид\w*|будет)\s+{cue}",
            rf"\b{cue}(?:\s+предупрежден\w*)?\s+не\s+(?:ожида\w*|прогнозиру\w*|предвид\w*|подтвержд\w*)",
            rf"\b{cue}(?:\s+предупрежден\w*)?\s+(?:нет|отсутствуют|отсутствует)",
        ):
            s = re.sub(pattern, " ", s)
    if re.search(storm, s):
        return "coast"
    if any(x in s for x in ("дожд", "ливн", "сыро", "влажн")):
        return "wet"
    if any(x in s for x in ("ясн", "солнечн")):
        return "golden"
    return "neutral"


def _font(size: int, bold: bool = False):
    from PIL import ImageFont
    paths = (
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        ("/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"),
        ("C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf"),
    )
    for path in paths:
        if Path(path).exists():
            return ImageFont.truetype(path, size=size)
    raise RuntimeError("No Cyrillic-capable TrueType font available for weekly photo cover")


def _fit(draw: Any, content: str, font: Any, width: int, max_lines: int) -> list[str]:
    """Fit the entire fact, or refuse the photo cover; never elide safety information."""
    words = content.replace("\n", " \uFFF0 ").split()
    lines: list[str] = []
    line = ""
    for word in words:
        if word == "\uFFF0":
            if line:
                lines.append(line)
                line = ""
            continue
        if draw.textbbox((0, 0), word, font=font)[2] > width:
            raise ValueError("Weekly photo fact contains an overwide word; legacy fallback required")
        trial = (line + " " + word).strip()
        if draw.textbbox((0, 0), trial, font=font)[2] <= width:
            line = trial
        else:
            if line:
                lines.append(line)
            line = word
    if line:
        lines.append(line)
    if len(lines) > max_lines:
        raise ValueError("Weekly photo fact exceeds glass panel; legacy fallback required")
    return lines or ["—"]


def _icon(draw: Any, symbol: str, x: int, y: int) -> None:
    from math import cos, sin, pi
    white = (249, 250, 250)
    if symbol == "cloud":
        draw.arc((x-23,y-13,x+11,y+15),start=175,end=312,fill=white,width=2)
        draw.arc((x-9,y-23,x+22,y+14),start=175,end=352,fill=white,width=2)
        draw.line((x-20,y+12,x+23,y+12),fill=white,width=2)
    elif symbol == "sun":
        draw.ellipse((x - 10, y - 10, x + 10, y + 10), outline=white, width=2)
        for a in range(8):
            t = 2 * pi * a / 8
            draw.line((x + 15*cos(t), y + 15*sin(t), x + 23*cos(t), y + 23*sin(t)), fill=white, width=2)
    elif symbol == "sea":
        for dy in (-9, 0, 9):
            draw.arc((x-22, y+dy-7, x+22, y+dy+9), start=190, end=340, fill=white, width=2)
    elif symbol == "wind":
        for dy in (-10, 0, 11):
            draw.arc((x-14, y+dy-9, x+17, y+dy+12), 155, 330, fill=white, width=2)
    else:  # thermometer
        draw.rounded_rectangle((x-5,y-20,x+5,y+12), radius=5,outline=white,width=2)
        draw.ellipse((x-8,y+5,x+8,y+22), fill=white)


def _week_label(start: date) -> str:
    from datetime import timedelta
    end = start + timedelta(days=6)
    if start.month == end.month:
        return f"{start.day}–{end.day} {RUSSIAN_MONTHS[start.month]}"
    return f"{start.day} {RUSSIAN_MONTHS[start.month]} – {end.day} {RUSSIAN_MONTHS[end.month]}"


def _kld_autumn_photo_week(start: date) -> bool:
    """Only full October/November weeks match the three autumn foliage backdrops."""
    end = start + timedelta(days=6)
    return start.month in (10, 11) and end.month in (10, 11)


# KLD weekly images are 1080×1350; keep header overlays below Telegram's top 10%.
_KLD_TELEGRAM_SAFE_TOP = 135
_KLD_HEADER_GLASS = (36, 150, 1044, 330)
_KLD_COLUMN_LEFT = (82, 421, 760)
_KLD_COLUMN_TEXT_WIDTH = 264


def _kld_header_glass(image: Any) -> Any:
    """Dark translucent heading on non-coast KLD JPGs, preserving top 135px."""
    from PIL import Image, ImageDraw
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    ImageDraw.Draw(overlay).rounded_rectangle(
        _KLD_HEADER_GLASS, radius=28, fill=(12, 27, 37, 225),
        outline=(245, 250, 252, 180), width=2,
    )
    return Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")


# Baked-in coast glass occupies x≈44–939, y≈66–243 on the original JPG.
# Do not composite a second panel; all text must stay below Telegram y=135.
_KLD_COAST_HEADER_SAFE = (60, 135, 920, 233)


def _kld_coast_header(draw: Any, start: date) -> tuple[tuple[int, int, int, int], ...]:
    """Render two lines INSIDE the original coast panel, without new glass."""
    lines = (
        ("ВАЙБ НЕДЕЛИ", (70, 137), _font(46, True)),
        (f"{_week_label(start)} · VAYBOMETER · KALININGRAD", (70, 205), _font(19)),
    )
    left, top, right, bottom = _KLD_COAST_HEADER_SAFE
    boxes = []
    # Check *all* glyphs before painting; a new/longer locale must fail closed.
    for content, origin, font in lines:
        bbox = draw.textbbox(origin, content, font=font, stroke_width=1)
        if not (left <= bbox[0] <= bbox[2] <= right and top <= bbox[1] <= bbox[3] <= bottom):
            raise ValueError("KLD coast heading outside baked glass safe zone")
        boxes.append(bbox)
    for content, origin, font in lines:
        draw.text(origin, content, font=font, fill=(250, 250, 251),
                  stroke_width=1, stroke_fill=(17, 30, 39))
    return tuple(boxes)


def render_photo_cover(text: str, *, start: date, output_path: str | Path, region: str) -> dict[str, Any]:
    from PIL import Image, ImageDraw, PngImagePlugin
    if region not in ASSETS:
        raise ValueError("Unsupported weekly cover region")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Weekly cover text required: do not invent data")
    # These three KLD JPGs show autumn foliage. Never use them for a week
    # containing September, December, winter, spring or summer dates.
    # The caller catches this refusal and keeps the complete legacy cover.
    if region == "kld" and not _kld_autumn_photo_week(start):
        raise ValueError("KLD autumn-only photo assets cannot cover this week")
    main_fact = _section(text, "✨ Главный фон недели")
    weather_fact = _section(text, "🌦 Погода")
    sea_heading = "🌊 Море" if region == "cyprus" else "🌊 Балтика"
    sea_fact = _section(text, sea_heading)
    if not main_fact or not weather_fact or not sea_fact:
        raise ValueError("Weekly source sections are incomplete; refusing photo cover")
    scenario = _scenario(region, weather_fact, main_fact)
    asset = Path(__file__).resolve().parent / "assets" / "weekly" / ("cy" if region == "cyprus" else "kld") / ASSETS[region][scenario]
    if not asset.is_file():
        raise FileNotFoundError(f"Missing weekly candidate cover: {asset}")
    if asset.stat().st_size > 8_000_000:
        raise ValueError("Weekly asset unexpectedly large")
    with Image.open(asset) as source:
        source.load()
        if source.width < WIDTH or source.height < HEIGHT:
            raise ValueError("Weekly template resolution too low")
        image = source.convert("RGB").resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS)
    if region == "kld" and asset.name != "kld_baltic_coast.jpg":
        image = _kld_header_glass(image)
    draw = ImageDraw.Draw(image)
    white = (250, 250, 251)
    light = (221, 236, 243)
    if region == "kld" and asset.name == "kld_baltic_coast.jpg":
        _kld_coast_header(draw, start)
    else:
        brand_y, title_y, date_y, region_y = (170, 207, 270, 179) if region == "kld" else (76, 116, 192, 145)
        draw.text((65, brand_y), "VAYBOMETER · " + ("CYPRUS" if region == "cyprus" else "KALININGRAD"), font=_font(22, True), fill=light)
        draw.text((64, title_y), "ВАЙБ НЕДЕЛИ", font=_font(53, True), fill=white, stroke_width=1, stroke_fill=(40, 55, 64))
        draw.text((66, date_y), _week_label(start), font=_font(29), fill=white)
        small_region = "ЛИМАССОЛ\nИ ОСТРОВ" if region == "cyprus" else "КАЛИНИНГРАД\nИ ОБЛАСТЬ"
        right_font = _font(17, True)
        for index, s in enumerate(small_region.splitlines()):
            box = draw.textbbox((0, 0), s, font=right_font)
            draw.text((896-(box[2]-box[0]), region_y + 23*index), s, font=right_font, fill=light)

    weather_summary = _weather_summary(weather_fact, main_fact)
    # This three-column cover cannot safely show independent marine alerts.
    # Reject it rather than display a reassuring temperature-only sea column.
    # Scan every line of a section: the columns only show its first line.
    if any(_critical_warning(line) for line in _section_lines(text, sea_heading)):
        raise ValueError("Weekly marine alert cannot be represented in photo columns")
    if _critical_warning(main_fact) and not _critical_warning(weather_summary):
        raise ValueError("Weekly headline alert would be omitted from photo columns")
    if any(_critical_warning(line) for line in _section_lines(text, "✨ Главный фон недели")[1:]):
        raise ValueError("Weekly headline alert on a later line would be omitted from photo columns")
    if any(_critical_warning(line) for line in _section_lines(text, "🌦 Погода")[1:]):
        raise ValueError("Weekly weather alert on a later line would be omitted from photo columns")
    if any(_sport_warning(line) for line in _section_lines(text, "🏄 Вода и спорт")):
        raise ValueError("Weekly water/sport alert cannot be represented in photo columns")
    temp = _temperature(weather_fact, main_fact)
    sea = _sea(sea_fact)
    facts = [
        ("ПОГОДА", weather_summary, "sun" if scenario in ("sunny", "sunset", "golden") else "cloud"),
        ("ТЕМПЕРАТУРА", temp, "temp"),
        (("МОРЕ" if region == "cyprus" else "БАЛТИКА"), sea, "sea"),
    ]
    # Glass panels are baked into each JPG at slightly different vertical positions.
    # Keep the bottom text inside their measured safe interior, including all 3 lines.
    panel_top = {
        "kld_blue_evening.jpg": 1075,
        "kld_baltic_coast.jpg": 1041,
        "kld_golden_city.jpg": 1038,
    }.get(asset.name, 1075)
    for index, (heading, value, symbol) in enumerate(facts):
        # Inset all KLD columns past the widest baked-in frame (R12-V).
        # Cyprus remains unchanged; original JPGs and their glass are untouched.
        left = _KLD_COLUMN_LEFT[index] if region == "kld" else 49 + index * 339
        text_width = _KLD_COLUMN_TEXT_WIDTH if region == "kld" else 293
        font = _font(24, bold=(index != 0))
        wrapped_lines = _fit(draw, value, font, text_width, 3)
        # Explicit geometry contract: even the lowest glyph must remain in glass.
        glass_bottom = panel_top + 207
        for lineno, wrapped in enumerate(wrapped_lines):
            bounds = draw.textbbox((left, panel_top + 107 + lineno*31), wrapped, font=font, stroke_width=1)
            if bounds[3] > glass_bottom or (region == "kld" and (bounds[0] < left - 1 or bounds[2] > left + text_width + 2)):
                raise ValueError("Weekly photo text outside glass safe zone")
        heading_font = _font(19, True)
        heading_bounds = draw.textbbox((left, panel_top + 73), heading, font=heading_font)
        if region == "kld" and heading_bounds[2] > left + text_width:
            raise ValueError("Weekly photo heading outside glass safe zone")
        _icon(draw, symbol, left + 24, panel_top + 38)
        draw.text((left, panel_top + 73), heading, font=heading_font, fill=white)
        for lineno, wrapped in enumerate(wrapped_lines):
            draw.text((left, panel_top + 107 + lineno*31), wrapped, font=font, fill=white, stroke_width=1, stroke_fill=(38, 44, 49))
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    metadata: dict[str, Any] = {
        "renderer_version": PHOTO_RENDERER_VERSION, "region": region,
        "week_start": start.isoformat(), "week_label": _week_label(start),
        "variant": scenario, "main_fact": main_fact,
        "weather_fact": weather_fact, "sea_fact": sea_fact,
        "temperature_label": temp, "sea_temperature_label": sea,
        "asset_sha256": hashlib.sha256(asset.read_bytes()).hexdigest(),
        "source_text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "backdrop_is_artistic_illustration": True,
    }
    info = PngImagePlugin.PngInfo()
    for k, v in metadata.items():
        info.add_text(k, str(v))
    image.save(output, format="PNG", pnginfo=info, optimize=True)
    return dict(metadata, path=str(output), width=WIDTH, height=HEIGHT)


__all__ = ["PHOTO_RENDERER_VERSION", "render_photo_cover"]
