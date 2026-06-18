#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""KLD VisualContext parser for VayboMeter weather images.

Step 2 of the visual weather matrix implementation.

Input: final FORMAT_V2 Telegram message text.
Output: structured VisualContext that can be passed to visual_rules.py.

This module is intentionally read-only and side-effect free: it does not send
messages and does not generate images. It is safe to call from safe-test jobs in
"prompt-only" mode.
"""
from __future__ import annotations

import argparse
import dataclasses
import html
import json
import re
import sys
from dataclasses import dataclass, field
from statistics import median
from typing import Any, Literal, Optional

Region = Literal["kaliningrad"]
PostType = Literal["morning", "evening", "forecast_tomorrow", "unknown"]
WeatherMain = Literal[
    "clear",
    "partly_cloudy",
    "cloudy",
    "drizzle",
    "rain",
    "fog",
    "snow",
    "storm",
    "unknown",
]
Sport = Literal["none", "sup", "kite", "wing", "windsurf"]
SportLevel = Literal["none", "excellent", "good", "experienced_only", "not_recommended"]
MoonPhase = Literal[
    "new",
    "waxing_crescent",
    "first_quarter",
    "waxing_gibbous",
    "full",
    "waning_gibbous",
    "last_quarter",
    "waning_crescent",
    "unknown",
]
TimeHint = Literal["day", "sunrise", "sunset", "night", "unknown"]

SECTION_MARKERS = (
    "морские города",
    "тёплые города",
    "теплые города",
    "холодные города",
    "рекомендации",
    "астроритм",
    "вывод",
    "главный сценарий",
    "главный нюанс",
    "уверенность",
)

SPORT_WORDS = ("sup", "сап", "кайт", "винг", "винд", "гидрокостюм", "боты")


@dataclass(frozen=True)
class VisualContext:
    region: Region = "kaliningrad"
    post_type: PostType = "unknown"
    weather_main: WeatherMain = "unknown"
    temp_max: Optional[float] = None
    temp_min: Optional[float] = None
    wind_avg: Optional[float] = None
    wind_gust: Optional[float] = None
    sea_temp: Optional[float] = None
    wave_height: Optional[float] = None
    sport: Sport = "none"
    sport_level: SportLevel = "none"
    moon_phase: MoonPhase = "unknown"
    time_hint: TimeHint = "unknown"
    uv_index: Optional[float] = None
    score: Optional[float] = None
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class _SportLine:
    sport: Sport
    level: SportLevel
    line: str


def _clean_text(text: str) -> str:
    text = html.unescape(text or "")
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("\u00a0", " ")
    text = text.replace("−", "-").replace("–", "-").replace("—", "-")
    return text


def _lines(text: str) -> list[str]:
    return [ln.strip() for ln in _clean_text(text).splitlines() if ln.strip()]


def _num(raw: str | None) -> Optional[float]:
    if raw is None:
        return None
    try:
        return float(str(raw).replace(",", ".").strip())
    except Exception:
        return None


def _first_num(pattern: str, text: str, flags: int = re.I) -> Optional[float]:
    m = re.search(pattern, text, flags)
    if not m:
        return None
    return _num(m.group(1))


def _is_section_line(line: str) -> bool:
    low = line.lower()
    return any(marker in low for marker in SECTION_MARKERS)


def _is_sport_or_wetsuit_line(line: str) -> bool:
    low = line.lower()
    return any(word in low for word in SPORT_WORDS)


def _city_weather_lines(text: str) -> list[str]:
    """Return lines that look like real city forecast rows.

    This intentionally excludes headers like "❄️ Холодные города" and water-sport
    recommendation rows like "гидрокостюм 4/3 мм", which previously polluted
    weather and temperature parsing.
    """
    out: list[str] = []
    for line in _lines(text):
        low = line.lower()
        if _is_section_line(line):
            continue
        if _is_sport_or_wetsuit_line(line):
            continue
        if "°" not in line and "°c" not in low:
            continue
        if ":" not in line:
            continue
        if not re.search(r":\s*-?\d+(?:[\.,]\d+)?\s*/\s*-?\d+(?:[\.,]\d+)?\s*°", line):
            continue
        out.append(line)
    return out


def _detect_weather_from_lines(lines: list[str], *, allow_snow: bool = True) -> WeatherMain:
    s = "\n".join(lines).lower()
    if not s:
        return "unknown"

    if any(x in s for x in ("шторм", "штормовое", "storm", "gale", "squall", "ураган")):
        return "storm"
    if "⛈" in s or "гроза" in s or "thunder" in s:
        return "rain"
    if allow_snow and ("снег" in s or "snow" in s or re.search(r"(?:^|\s)❄️?\s*(?:снег|snow)", s)):
        return "snow"
    if "🌧" in s or "ливн" in s or re.search(r"\bдожд", s) or "rain" in s or "shower" in s:
        return "rain"
    if "🌦" in s or "морось" in s or "drizzle" in s:
        return "drizzle"
    if "🌫" in s or "туман" in s or "fog" in s or "mist" in s:
        return "fog"
    if "🌥" in s or "пасм" in s or "☁" in s or "обл" in s or "cloudy" in s:
        return "cloudy"
    if "⛅" in s or "ч.обл" in s or "переменн" in s or "partly" in s:
        return "partly_cloudy"
    if "☀" in s or "ясно" in s or "clear" in s:
        return "clear"
    return "unknown"


def detect_post_type(text: str, override: str | None = None) -> PostType:
    ov = (override or "").strip().lower()
    if ov in ("morning", "утро"):
        return "morning"
    if ov in ("evening", "вечер"):
        return "evening"
    s = _clean_text(text).lower()
    if "на завтра" in s or "завтра" in s:
        return "evening"
    if "сегодня" in s or "утро" in s:
        return "morning"
    return "unknown"


def detect_time_hint(post_type: PostType, text: str) -> TimeHint:
    s = _clean_text(text).lower()
    if "рассвет" in s:
        return "sunrise"
    if "закат" in s or post_type in ("evening", "forecast_tomorrow"):
        return "sunset"
    if post_type == "morning":
        return "day"
    return "unknown"


def detect_weather_main(text: str, temp_max: Optional[float] = None) -> tuple[WeatherMain, dict[str, Any]]:
    lines = _city_weather_lines(text)
    evidence: dict[str, Any] = {"weather_lines": lines[:20]}
    weather = _detect_weather_from_lines(lines, allow_snow=True)

    # Consistency guard: if a decorative snowflake or a single polluted token gets
    # through while the day is clearly warm, fall back to non-snow interpretation.
    if weather == "snow" and temp_max is not None and temp_max >= 8:
        fallback = _detect_weather_from_lines(lines, allow_snow=False)
        evidence["weather_consistency_guard"] = {
            "from": "snow",
            "to": fallback,
            "reason": f"temp_max={temp_max} >= 8; snow is inconsistent",
        }
        weather = fallback if fallback != "unknown" else "cloudy"
    return weather, evidence


def extract_score(text: str) -> Optional[float]:
    s = _clean_text(text)
    patterns = [
        r"(?:VayboMeter|ВайбоМетр)[^0-9]{0,80}(\d+(?:[\.,]\d+)?)\s*/\s*10",
        r"Score[^0-9]{0,80}(\d+(?:[\.,]\d+)?)\s*/\s*10",
    ]
    for p in patterns:
        v = _first_num(p, s)
        if v is not None:
            return v
    return None


def extract_temperatures(text: str) -> tuple[Optional[float], Optional[float], dict[str, Any]]:
    pairs: list[tuple[float, float]] = []
    ignored: list[str] = []
    for line in _lines(text):
        if _is_sport_or_wetsuit_line(line):
            if re.search(r"\d+(?:[\.,]\d+)?\s*/\s*\d+(?:[\.,]\d+)?", line):
                ignored.append(line)
            continue
        if line not in _city_weather_lines(text):
            continue
        m = re.search(r":\s*(-?\d+(?:[\.,]\d+)?)\s*/\s*(-?\d+(?:[\.,]\d+)?)\s*°", line)
        if not m:
            continue
        a = _num(m.group(1))
        b = _num(m.group(2))
        if a is not None and b is not None:
            pairs.append((a, b))

    evidence: dict[str, Any] = {"temp_pairs": pairs[:20]}
    if ignored:
        evidence["ignored_temp_like_lines"] = ignored[:20]
    if pairs:
        return max(a for a, _ in pairs), min(b for _, b in pairs), evidence
    return None, None, evidence


def extract_wind(text: str) -> tuple[Optional[float], Optional[float], dict[str, Any]]:
    s = _clean_text(text)
    evidence: dict[str, Any] = {}

    gust_patterns = [
        r"порыв\w*[^\d]{0,16}(?:до\s*)?(\d+(?:[\.,]\d+)?)\s*м\s*/?\s*с",
        r"gusts?[^\d]{0,16}(?:up to\s*)?(\d+(?:[\.,]\d+)?)\s*m\s*/?\s*s",
    ]
    gusts: list[float] = []
    for p in gust_patterns:
        for m in re.finditer(p, s, re.I):
            v = _num(m.group(1))
            if v is not None:
                gusts.append(v)
    wind_gust = max(gusts) if gusts else None
    if gusts:
        evidence["gust_candidates"] = gusts[:20]

    avg_candidates: list[float] = []
    range_patterns = [
        r"ветер[^\d]{0,24}(\d+(?:[\.,]\d+)?)\s*-\s*(\d+(?:[\.,]\d+)?)\s*м\s*/?\s*с",
        r"wind[^\d]{0,24}(\d+(?:[\.,]\d+)?)\s*-\s*(\d+(?:[\.,]\d+)?)\s*m\s*/?\s*s",
    ]
    for p in range_patterns:
        for m in re.finditer(p, s, re.I):
            a = _num(m.group(1))
            b = _num(m.group(2))
            if a is not None and b is not None:
                avg_candidates.append((a + b) / 2)

    single_patterns = [
        r"(?:^|\n)[^\n]{0,80}ветер[^\d]{0,24}(\d+(?:[\.,]\d+)?)\s*м\s*/?\s*с",
        r"(?:^|\n)[^\n]{0,80}wind[^\d]{0,24}(\d+(?:[\.,]\d+)?)\s*m\s*/?\s*s",
    ]
    for p in single_patterns:
        for m in re.finditer(p, s, re.I):
            v = _num(m.group(1))
            if v is not None:
                avg_candidates.append(v)

    wind_avg = max(avg_candidates) if avg_candidates else None
    if avg_candidates:
        evidence["wind_avg_candidates"] = avg_candidates[:20]
    return wind_avg, wind_gust, evidence


def extract_sea(text: str) -> tuple[Optional[float], Optional[float], dict[str, Any]]:
    sea_temps: list[float] = []
    waves: list[float] = []
    source_lines: list[str] = []

    for line in _city_weather_lines(text):
        if "🌊" not in line and "волна" not in line.lower():
            continue
        source_lines.append(line)
        sea = _first_num(r"🌊\s*(\d+(?:[\.,]\d+)?)", line)
        if sea is not None:
            sea_temps.append(sea)
        wave = _first_num(r"(?:^|[•\s])\s*(\d+(?:[\.,]\d+)?)\s*м\b", line)
        if wave is not None:
            waves.append(wave)

    sea_temp = float(median(sea_temps)) if sea_temps else None
    wave_height = max(waves) if waves else None

    evidence = {}
    if source_lines:
        evidence["sea_source_lines"] = source_lines[:20]
    if sea_temps:
        evidence["sea_temp_candidates"] = sea_temps[:20]
    if waves:
        evidence["wave_height_candidates"] = waves[:20]
    return sea_temp, wave_height, evidence


def _level_from_line(line: str) -> SportLevel:
    s = line.lower()
    if any(x in s for x in ("не рекоменд", "не стоит", "опас", "закрыт", "нельзя", "not recommended")):
        return "not_recommended"
    if any(x in s for x in ("только", "опыт", "подготов", "коротк", "осторож", "experienced")):
        return "experienced_only"
    if any(x in s for x in ("отлич", "идеаль", "excellent")):
        return "excellent"
    if any(x in s for x in ("хорош", "допуст", "можно", "норм", "good", "ok")):
        return "good"
    return "good"


def extract_sport(text: str, wind_gust: Optional[float] = None) -> tuple[Sport, SportLevel, dict[str, Any]]:
    sport_lines: list[tuple[Sport, SportLevel, str]] = []
    for line in _lines(text):
        low = line.lower()
        if "sup" in low or "сап" in low or "сапе" in low:
            sport_lines.append(("sup", _level_from_line(line), line))
        if "кайт" in low or "wing" in low or "винг" in low or "винд" in low or "windsurf" in low:
            if "wing" in low or "винг" in low:
                sp: Sport = "wing"
            elif "винд" in low or "windsurf" in low:
                sp = "windsurf"
            else:
                sp = "kite"
            sport_lines.append((sp, _level_from_line(line), line))

    if not sport_lines:
        return "none", "none", {"sport_lines": []}

    rank = {"none": 0, "not_recommended": 1, "experienced_only": 2, "good": 3, "excellent": 4}

    def sort_key(item: tuple[Sport, SportLevel, str]) -> tuple[int, int]:
        sp, lvl, _ = item
        wind_bonus = 1 if (wind_gust is not None and wind_gust >= 10 and sp in ("kite", "wing", "windsurf")) else 0
        return rank.get(lvl, 0), wind_bonus

    chosen = sorted(sport_lines, key=sort_key, reverse=True)[0]
    return chosen[0], chosen[1], {"sport_lines": [dataclasses.asdict(_SportLine(*x)) for x in sport_lines]}


def extract_moon_phase(text: str) -> MoonPhase:
    s = _clean_text(text).lower()
    if "новолуние" in s or "new moon" in s:
        return "new"
    if "полнолуние" in s or "full moon" in s:
        return "full"
    if "растущий серп" in s or "waxing crescent" in s:
        return "waxing_crescent"
    if "убывающий серп" in s or "waning crescent" in s:
        return "waning_crescent"
    if "первая четверть" in s or "first quarter" in s:
        return "first_quarter"
    if "последняя четверть" in s or "last quarter" in s:
        return "last_quarter"
    if "растущ" in s or "waxing" in s:
        return "waxing_gibbous"
    if "убыва" in s or "waning" in s:
        return "waning_gibbous"
    return "unknown"


def build_visual_context(message: str, *, post_type: str | None = None) -> VisualContext:
    clean = _clean_text(message)
    pt = detect_post_type(clean, post_type)
    temp_max, temp_min, temp_ev = extract_temperatures(clean)
    weather, weather_ev = detect_weather_main(clean, temp_max=temp_max)
    wind_avg, wind_gust, wind_ev = extract_wind(clean)
    sea_temp, wave_height, sea_ev = extract_sea(clean)
    sport, sport_level, sport_ev = extract_sport(clean, wind_gust)
    moon_phase = extract_moon_phase(clean)
    score = extract_score(clean)
    time_hint = detect_time_hint(pt, clean)

    evidence: dict[str, Any] = {}
    evidence.update(weather_ev)
    evidence.update(temp_ev)
    evidence.update(wind_ev)
    evidence.update(sea_ev)
    evidence.update(sport_ev)

    return VisualContext(
        region="kaliningrad",
        post_type=pt,
        weather_main=weather,
        temp_max=temp_max,
        temp_min=temp_min,
        wind_avg=wind_avg,
        wind_gust=wind_gust,
        sea_temp=sea_temp,
        wave_height=wave_height,
        sport=sport,
        sport_level=sport_level,
        moon_phase=moon_phase,
        time_hint=time_hint,
        uv_index=None,
        score=score,
        evidence=evidence,
    )


def to_json(ctx: VisualContext, *, pretty: bool = True) -> str:
    return json.dumps(dataclasses.asdict(ctx), ensure_ascii=False, indent=2 if pretty else None)


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse KLD FORMAT_V2 weather message into VisualContext")
    parser.add_argument("--message-file", default="", help="Path to text file. If omitted, stdin is used.")
    parser.add_argument("--post-type", default="", choices=["", "morning", "evening", "forecast_tomorrow", "unknown"])
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()

    if args.message_file:
        with open(args.message_file, "r", encoding="utf-8") as f:
            message = f.read()
    else:
        message = sys.stdin.read()

    ctx = build_visual_context(message, post_type=args.post_type or None)
    print(to_json(ctx, pretty=not args.compact))


if __name__ == "__main__":
    main()


__all__ = ["VisualContext", "build_visual_context", "to_json"]
