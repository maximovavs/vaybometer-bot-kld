#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic factual fallback cover for KLD daily publications."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

import weather_text
from weather_text import clause_has_confirmed_storm as _clause_has_confirmed_storm
from weather_text import split_clauses as _split_clauses


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
# "шторм" word/negation/uncertainty detection is shared with format_v2.py,
# safe_test_post.py and post_kld.py via weather_text.clause_has_confirmed_storm,
# so all four use one contract. "гроза" (thunderstorm) is a separate concept
# only this module tracks; it is evaluated independently of "шторм" (a negated
# storm must not cancel a confirmed thunderstorm in the same/other clause, and
# vice versa) using the same bounded-gap, actor-vs-cancellation discipline and
# the same two gap directions as precipitation (a cue-before-term gap must not
# cross a comma, so "Шторм возможен, гроза ожидается." keeps the thunderstorm).
_THUNDERSTORM_GAP_AFTER = r"(?:(?!гроз\w*)[^.!?\n]){0,40}?"
_THUNDERSTORM_GAP_BEFORE = r"(?:(?!гроз\w*)[^.!?\n,]){0,40}?"
_THUNDERSTORM_NEGATION_RE = re.compile(
    rf"гроз\w*{_THUNDERSTORM_GAP_AFTER}\b(?:не\s+(?:ожида\w*|будет|прогнозир\w*|предвид\w*|подтвержд\w*)|"
    r"маловероят\w*|исключ(?:ён\w*|ен[аоы]\w*))|"
    r"без\s+гроз\w*|"
    rf"(?:риск|вероятност\w*){_THUNDERSTORM_GAP_BEFORE}\bгроз\w*{_THUNDERSTORM_GAP_AFTER}"
    rf"\b(?:низк\w*|невысок\w*|минимал\w*|отсутств\w*)",
    re.IGNORECASE,
)
_THUNDERSTORM_UNCERTAIN_RE = re.compile(
    rf"гроз\w*{_THUNDERSTORM_GAP_AFTER}\b(?:провер\w*|уточн\w*|возмож\w*|вероятн\w*|не\s+исключ\w*|сохраня\w*)|"
    rf"(?:возмож\w*|вероятност\w*|риск){_THUNDERSTORM_GAP_BEFORE}\bгроз\w*",
    re.IGNORECASE,
)
_THUNDERSTORM_WORD_RE = re.compile(r"гроз\w*", re.IGNORECASE)
_THUNDERSTORM_ICON_RE = re.compile(r"⛈")

# Independent per-type evidence/negation/uncertainty for precipitation: a single
# global "any negation anywhere in the clause -> drop everything" check used to
# make "Дождь будет, снега не будет." lose the real rain along with the negated
# snow. Each type (rain, drizzle, snow, generic precipitation) now has its own
# regexes, and the term<->cue gap is walled off from the *other* types' stems.
# Two gap directions:
#  - term-first ("снега ... не будет"): the cue follows the term, so the gap may
#    cross commas (parenthetical modifiers: "снега, скорее всего, не будет").
#  - cue-first ("возможна морось", "риск дождя"): the cue precedes the term, so
#    the gap must NOT cross a comma, or the cue would bind to a following type
#    ("Дождь возможен, снег ожидается." must keep snow confirmed).
# Negation uses the passive participle "исключён/исключена" (fact removed), not
# a bare "исключ\w*", so the active verb "исключил" ("Снег исключил движение.")
# stays a confirmation.
_NEGATION_SUFFIX = r"не\s+(?:ожида\w*|будет|прогнозир\w*|предвид\w*|подтвержд\w*)|маловероят\w*|исключ(?:ён\w*|ен[аоы]\w*)"
_UNCERTAIN_SUFFIX_AFTER = r"провер\w*|уточн\w*|возмож\w*|вероятн\w*|не\s+исключ\w*|сохраня\w*"
_PRECIP_GROUP_STEMS = {
    "rain": ("дожд", "лив"),
    "drizzle": ("морос",),
    "snow": ("снег",),
    "precipitation": ("осадк",),
}


def _precip_gap(group_key: str, *, block_comma: bool, max_len: int = 40) -> str:
    other_stems = [
        stem
        for key, stems in _PRECIP_GROUP_STEMS.items()
        if key != group_key
        for stem in stems
    ]
    char_class = r"[^.!?\n,]" if block_comma else r"[^.!?\n]"
    if other_stems:
        forbidden = "|".join(rf"{stem}\w*" for stem in other_stems)
        return rf"(?:(?!{forbidden}){char_class}){{0,{max_len}}}?"
    return rf"{char_class}{{0,{max_len}}}?"


def _build_precip_negation_uncertain() -> tuple[dict[str, re.Pattern[str]], dict[str, re.Pattern[str]]]:
    negation_by_group: dict[str, re.Pattern[str]] = {}
    uncertain_by_group: dict[str, re.Pattern[str]] = {}
    for group_key, stems in _PRECIP_GROUP_STEMS.items():
        gap_after = _precip_gap(group_key, block_comma=False)   # term ... cue
        gap_before = _precip_gap(group_key, block_comma=True)   # cue ... term
        negation_parts: list[str] = []
        uncertain_parts: list[str] = []
        for stem in stems:
            negation_parts.append(rf"без\s+{stem}\w*")
            negation_parts.append(rf"{stem}\w*{gap_after}\b(?:{_NEGATION_SUFFIX})")
            negation_parts.append(
                rf"(?:риск|вероятност\w*){gap_before}\b{stem}\w*{gap_after}"
                rf"\b(?:низк\w*|невысок\w*|минимал\w*|отсутств\w*)"
            )
            uncertain_parts.append(rf"{stem}\w*{gap_after}\b(?:{_UNCERTAIN_SUFFIX_AFTER})")
            uncertain_parts.append(rf"(?:возмож\w*|вероятност\w*){gap_before}\b{stem}\w*")
        if group_key == "rain":
            negation_parts.append(r"преимущественно\s+сух\w*")
        negation_by_group[group_key] = re.compile("|".join(negation_parts), re.IGNORECASE)
        uncertain_by_group[group_key] = re.compile("|".join(uncertain_parts), re.IGNORECASE)
    return negation_by_group, uncertain_by_group


_PRECIP_NEGATION_RE_BY_GROUP, _PRECIP_UNCERTAIN_RE_BY_GROUP = _build_precip_negation_uncertain()

_RAIN_WORD_RE = re.compile(r"(?:дожд\w*|лив\w*)", re.IGNORECASE)
_RAIN_ICON_RE = re.compile(r"🌧")
_SHOWERS_ICON_RE = re.compile(r"🌦")
_DRIZZLE_RE = re.compile(r"морос\w*", re.IGNORECASE)
_SNOW_RE = re.compile(r"(?:❄|снег\w*)", re.IGNORECASE)
_PRECIPITATION_RE = re.compile(r"осад\w*", re.IGNORECASE)
_STRONG_WIND_RE = re.compile(r"(?:сильн\w*\s+ветер|штормов\w*\s+ветер)", re.IGNORECASE)


def _thunderstorm_confirmed(clause: str) -> bool:
    """Thunderstorm evidence, evaluated independently of "шторм".

    The ⛈ emoji is an unambiguous fact; the word "гроза" is confirmed only
    when it is neither negated ("Грозы не будет.") nor hedged ("гроза
    возможна", "риск грозы")."""
    low = clause.lower()
    if _THUNDERSTORM_ICON_RE.search(clause):
        return True
    if not _THUNDERSTORM_WORD_RE.search(low):
        return False
    return not _THUNDERSTORM_NEGATION_RE.search(low) and not _THUNDERSTORM_UNCERTAIN_RE.search(low)


def _precip_group_confirmed(group_key: str, clause: str, *, has_evidence: bool) -> bool:
    if not has_evidence:
        return False
    if _PRECIP_NEGATION_RE_BY_GROUP[group_key].search(clause):
        return False
    if _PRECIP_UNCERTAIN_RE_BY_GROUP[group_key].search(clause):
        return False
    return True


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
    max_gust: float | None = None

    for raw_line in message.splitlines():
        line = _HTML_TAG_RE.sub("", raw_line).strip().lower()
        if not line or _EDITORIAL_LINE_RE.match(line):
            continue

        # Gust value via the single shared parser (weather_text), counting only
        # "порыв …" — never average wind — so storm_gust below matches the other
        # layers exactly.
        gust = weather_text.extract_max_gust_ms(line)
        if gust is not None:
            max_gust = gust if max_gust is None else max(max_gust, gust)
        # strong_wind is a softer "notable wind" cue (>=12 м/с) and can begin
        # well below the storm threshold — it must never stand in for
        # storm_gust, which is a strict >=STORM_GUST_MS test derived below.
        if (gust is not None and gust >= 12) or _STRONG_WIND_RE.search(line):
            strong_wind = True

        # Split into clauses (sentence punctuation, or ", но"/", а" joining two
        # independent statements) so a negation in one clause ("Шторма не будет
        # утром.", "Снега не будет утром.") cannot cancel a genuine confirmation
        # in a different clause on the same line ("Вечером ожидается шторм.").
        for raw_clause in _split_clauses(line):
            clause = raw_clause.strip()
            if not clause:
                continue

            # Storm ("шторм") and thunderstorm ("гроза"/⛈) are strictly
            # independent per clause: "Шторма не будет, гроза ожидается."
            # confirms thunderstorm ONLY (explicit_storm stays False); "Грозы
            # не будет, шторм ожидается." confirms the storm ONLY. Neither flag
            # raises the other — the umbrella "either severe phenomenon" case
            # is the separate derived `severe_weather` flag computed below.
            if _clause_has_confirmed_storm(clause):
                explicit_storm = True
            if _thunderstorm_confirmed(clause):
                thunderstorm = True

            drizzle_evidence = bool(_DRIZZLE_RE.search(clause))
            snow_evidence = bool(_SNOW_RE.search(clause))
            explicit_rain_evidence = bool(_RAIN_WORD_RE.search(clause) or _RAIN_ICON_RE.search(clause))
            showers_icon = bool(_SHOWERS_ICON_RE.search(clause))
            rain_evidence = explicit_rain_evidence or (
                showers_icon and not drizzle_evidence and not snow_evidence
            )
            precipitation_evidence = bool(_PRECIPITATION_RE.search(clause))

            clause_rain = _precip_group_confirmed("rain", clause, has_evidence=rain_evidence)
            clause_drizzle = _precip_group_confirmed("drizzle", clause, has_evidence=drizzle_evidence)
            clause_snow = _precip_group_confirmed("snow", clause, has_evidence=snow_evidence)
            clause_precipitation = _precip_group_confirmed(
                "precipitation", clause, has_evidence=precipitation_evidence
            )
            rain = rain or clause_rain
            drizzle = drizzle or clause_drizzle
            snow = snow or clause_snow
            actual_precipitation = actual_precipitation or any(
                (clause_rain, clause_drizzle, clause_snow, clause_precipitation)
            )

    # Numeric storm scenario: gusts at/above the threshold are a storm even
    # when the word "шторм" never appears (e.g. "порывы до 16 м/с"). The
    # threshold is read live from weather_text so an env override + reload is
    # picked up here exactly as it is in format_v2/safe_test_post/post_kld.
    storm_gust = max_gust is not None and max_gust >= weather_text.STORM_GUST_MS
    # storm_badge is what drives the "ШТОРМОВОЕ ПРЕДУПРЕЖДЕНИЕ" cover fact:
    # a confirmed storm word OR a gust-threshold storm. thunderstorm alone
    # does NOT raise it (that is a lightning motif, not a storm warning).
    storm_badge = explicit_storm or storm_gust
    # severe_weather is the umbrella for the dramatic palette: storm word,
    # thunderstorm, or gust-threshold storm.
    severe_weather = explicit_storm or thunderstorm or storm_gust
    return {
        "explicit_storm": explicit_storm,
        "actual_precipitation": actual_precipitation,
        "rain": rain,
        "drizzle": drizzle,
        "snow": snow,
        "thunderstorm": thunderstorm,
        "storm_gust": storm_gust,
        "storm_badge": storm_badge,
        "severe_weather": severe_weather,
        "strong_wind": strong_wind,
    }


def _precipitation_display(factual: Mapping[str, bool]) -> str:
    """Resolve one deterministic presentation without changing factual truth flags."""
    rain = bool(factual.get("rain"))
    drizzle = bool(factual.get("drizzle"))
    snow = bool(factual.get("snow"))
    if snow and rain:
        return "mixed_snow_rain"
    if snow and drizzle:
        return "snow_and_drizzle"
    if snow:
        return "snow"
    if rain and drizzle:
        return "rain_and_drizzle"
    if rain:
        return "rain"
    if drizzle:
        return "drizzle"
    if factual.get("actual_precipitation"):
        return "precipitation"
    return "none"


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
        "precipitation_display": _precipitation_display(factual),
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
    if flags["storm_badge"]:
        facts.append("ШТОРМОВОЕ ПРЕДУПРЕЖДЕНИЕ")
    elif flags["precipitation_display"] != "none":
        precipitation_facts = {
            "drizzle": "МОРОСЬ МЕСТАМИ",
            "rain": "ДОЖДЬ МЕСТАМИ",
            "rain_and_drizzle": "ДОЖДЬ И МОРОСЬ МЕСТАМИ",
            "snow": "СНЕГ МЕСТАМИ",
            "snow_and_drizzle": "СНЕГ И МОРОСЬ МЕСТАМИ",
            "mixed_snow_rain": "СНЕГ И ДОЖДЬ МЕСТАМИ",
            "precipitation": "ОСАДКИ МЕСТАМИ",
        }
        facts.append(precipitation_facts[str(flags["precipitation_display"])])
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
    # Dramatic backdrop for either severe phenomenon (storm or thunderstorm),
    # via the derived umbrella flag — a thunderstorm-only day should still read
    # as severe without overloading explicit_storm.
    if weather.get("severe_weather"):
        return (32, 46, 59), (73, 91, 105), (190, 204, 211)
    precipitation_display = weather["precipitation_display"]
    if precipitation_display in {"snow", "snow_and_drizzle"}:
        return (126, 151, 165), (181, 198, 205), (232, 235, 229)
    if precipitation_display == "drizzle":
        return (105, 133, 149), (157, 178, 188), (224, 230, 229)
    if precipitation_display != "none":
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
    drizzle_color = (174, 211, 229)
    snow_color = (248, 250, 252)
    lightning_color = (235, 226, 170)
    wind_color = (220, 230, 232)
    rain_lines: list[tuple[int, int, int, int]] = []
    drizzle_lines: list[tuple[int, int, int, int]] = []
    snow_dots: list[tuple[int, int, int, int]] = []
    lightning_line: tuple[tuple[int, int], ...] = ()
    wind_arcs: list[tuple[int, int, int, int]] = []
    precipitation_display = str(weather["precipitation_display"])

    if precipitation_display in {"rain", "rain_and_drizzle", "mixed_snow_rain"}:
        for x in range(30, width, 65):
            y = 610 + (x % 130)
            segment = (x, y, x - 24, y + 90)
            rain_lines.append(segment)
            draw.line(segment, fill=rain_color, width=3)
    elif precipitation_display in {"drizzle", "snow_and_drizzle"}:
        for x in range(70, width, 125):
            y = 625 + (x % 95)
            segment = (x, y, x - 6, y + 24)
            drizzle_lines.append(segment)
            draw.line(segment, fill=drizzle_color, width=1)
    if precipitation_display in {"snow", "snow_and_drizzle", "mixed_snow_rain"}:
        for x in range(55, width, 115):
            y = 615 + (x % 155)
            dot = (x - 3, y - 3, x + 3, y + 3)
            snow_dots.append(dot)
            draw.ellipse(dot, fill=snow_color)
    # Lightning is a thunderstorm motif — driven by the thunderstorm flag, not
    # by a (possibly storm-only) severe-weather day.
    if weather.get("thunderstorm"):
        lightning_line = ((850, 615), (805, 710), (850, 700), (790, 825))
        draw.line(lightning_line, fill=lightning_color, width=8)
    if weather["strong_wind"] and not weather["rain"]:
        for y in (625, 690, 755):
            arc = (700, y, 1040, y + 95)
            wind_arcs.append(arc)
            draw.arc(arc, 180, 350, fill=wind_color, width=4)

    return {
        "precipitation_display": precipitation_display,
        "rain_lines": rain_lines,
        "rain_color": rain_color,
        "drizzle_lines": drizzle_lines,
        "drizzle_color": drizzle_color,
        "snow_dots": snow_dots,
        "snow_color": snow_color,
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
    metadata["precipitation_display"] = weather["precipitation_display"]
    metadata["rain_graphics"] = bool(graphics["rain_lines"])
    metadata["drizzle_graphics"] = bool(graphics["drizzle_lines"])
    metadata["snow_graphics"] = bool(graphics["snow_dots"])
    metadata["lightning_graphics"] = bool(graphics["lightning_line"])
    png_info = PngImagePlugin.PngInfo()
    png_info.add_text("renderer_version", RENDERER_VERSION)
    png_info.add_text("weather_flags", json.dumps(weather, ensure_ascii=False, sort_keys=True))
    png_info.add_text("graphics", json.dumps(graphics, ensure_ascii=False, sort_keys=True))
    png_info.add_text("explicit_storm", str(bool(weather["explicit_storm"])).lower())
    png_info.add_text("thunderstorm", str(bool(weather["thunderstorm"])).lower())
    png_info.add_text("storm_gust", str(bool(weather.get("storm_gust"))).lower())
    png_info.add_text("storm_badge", str(bool(weather.get("storm_badge"))).lower())
    png_info.add_text("severe_weather", str(bool(weather.get("severe_weather"))).lower())
    png_info.add_text("actual_precipitation", str(bool(weather["actual_precipitation"])).lower())
    png_info.add_text("precipitation_display", str(weather["precipitation_display"]))
    png_info.add_text("rain_graphics", str(metadata["rain_graphics"]).lower())
    png_info.add_text("drizzle_graphics", str(metadata["drizzle_graphics"]).lower())
    png_info.add_text("snow_graphics", str(metadata["snow_graphics"]).lower())
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
