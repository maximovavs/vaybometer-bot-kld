#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
image_prompt_kld.py

Prompts for the Kaliningrad evening post image (VayboMeter).

Priority is enforced by caller (post_kld.py):
  1) Storm warning => overlay "window" + stormy base scene.
  2) Full/New Moon => force moon_goddess + moon badge (phase + zodiac).

This file focuses on:
- Better prompts (avoid double moons / sun in night).
- Deterministic style selection by date (unless caller forces a style).
- Helpers to read lunar_calendar.json (get_lunar_meta).
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import random
import logging
import json
import re
from pathlib import Path
from typing import Tuple, Optional, List, Dict, Any


@dataclasses.dataclass(frozen=True)
class KldImageContext:
    date: dt.date
    marine_mood: str
    inland_mood: str
    astro_mood_en: str = ""
    storm: bool = False


@dataclasses.dataclass(frozen=True)
class LunarMeta:
    date: dt.date
    phase_key: Optional[str] = None          # full/new/first_quarter/last_quarter
    phase_en: Optional[str] = None           # "Full Moon" etc
    sign_en: Optional[str] = None            # "Leo" etc
    phrase_en: str = ""                      # "Full Moon in Leo"
    is_full_or_new: bool = False


logger = logging.getLogger(__name__)

WIND_KEYWORDS = (
    "ветер", "ветрен", "шквал", "порыв", "бриз",
    "wind", "windy", "gust", "gusty", "breeze", "storm wind",
)

RAIN_KEYWORDS = (
    "дожд", "ливн", "гроза",
    "rain", "rainy", "shower", "showers", "thunderstorm", "storm",
)

STORM_KEYWORDS = (
    "шторм", "storm", "gale", "squall", "шквал", "ураган",
)

ZODIAC_RU_EN: Dict[str, str] = {
    "овен": "Aries",
    "телец": "Taurus",
    "близнец": "Gemini",
    "рак": "Cancer",
    "лев": "Leo",
    "дева": "Virgo",
    "весы": "Libra",
    "скорпион": "Scorpio",
    "стрелец": "Sagittarius",
    "козерог": "Capricorn",
    "водолей": "Aquarius",
    "рыб": "Pisces",
}

ZODIAC_EN = [
    "Aries", "Taurus", "Gemini", "Cancer", "Leo", "Virgo",
    "Libra", "Scorpio", "Sagittarius", "Capricorn", "Aquarius", "Pisces",
]


def _choice_by_date(ctx: KldImageContext, salt: str, options: List[str]) -> str:
    seed = ctx.date.toordinal() * 10007 + sum(ord(c) for c in salt)
    rnd = random.Random(seed)
    return rnd.choice(options)


def _load_calendar(path: str = "lunar_calendar.json") -> dict:
    try:
        data = json.loads(Path(path).read_text("utf-8"))
    except Exception:
        return {}
    if isinstance(data, dict) and isinstance(data.get("days"), dict):
        return data["days"]
    return data if isinstance(data, dict) else {}


def get_lunar_meta(date_for_astro: dt.date, *, path: str = "lunar_calendar.json") -> LunarMeta:
    cal = _load_calendar(path)
    rec = cal.get(date_for_astro.isoformat(), {})
    if not isinstance(rec, dict):
        return LunarMeta(date=date_for_astro)

    phase_raw = (rec.get("phase_name") or rec.get("phase") or "").strip().lower()
    sign_raw = (rec.get("sign") or rec.get("zodiac") or "").strip().lower()

    phase_key: Optional[str] = None
    phase_en: Optional[str] = None

    if "полнолуние" in phase_raw or "full" in phase_raw:
        phase_key, phase_en = "full", "Full Moon"
    elif "новолуние" in phase_raw or "new" in phase_raw:
        phase_key, phase_en = "new", "New Moon"
    elif "первая четверть" in phase_raw or "first quarter" in phase_raw or "растущ" in phase_raw or "waxing" in phase_raw:
        phase_key, phase_en = "first_quarter", "First Quarter Moon"
    elif "последняя четверть" in phase_raw or "last quarter" in phase_raw or "убывающ" in phase_raw or "waning" in phase_raw:
        phase_key, phase_en = "last_quarter", "Last Quarter Moon"

    sign_en: Optional[str] = None
    if sign_raw:
        for ru, en in ZODIAC_RU_EN.items():
            if ru in sign_raw:
                sign_en = en
                break
        if sign_en is None:
            for en in ZODIAC_EN:
                if en.lower() in sign_raw:
                    sign_en = en
                    break
    parts: List[str] = []
    if phase_en:
        parts.append(phase_en)
    if sign_en:
        parts.append(f"in {sign_en}")
    phrase = " ".join(parts).strip()

    return LunarMeta(
        date=date_for_astro,
        phase_key=phase_key,
        phase_en=phase_en,
        sign_en=sign_en,
        phrase_en=phrase,
        is_full_or_new=bool(phase_key in ("full", "new")),
    )


def _astro_phrase_from_calendar(date_for_astro: dt.date) -> str:
    return get_lunar_meta(date_for_astro).phrase_en


def _weather_flavour(ctx: KldImageContext) -> str:
    text = f"{ctx.marine_mood} {ctx.inland_mood}".lower()
    is_windy = any(k in text for k in WIND_KEYWORDS)
    is_rainy = any(k in text for k in RAIN_KEYWORDS)
    is_storm = ctx.storm or any(k in text for k in STORM_KEYWORDS)

    if is_storm:
        return (
            "Storm-warning atmosphere: strong wind and gusts, restless waves, "
            "dramatic fast-moving clouds, spray in the air, a sense of urgency."
        )
    if is_windy and is_rainy:
        return (
            "Windy, rainy Baltic evening: strong gusts from the sea, "
            "wet reflections on the sand and promenade, dynamic clouds in the sky."
        )
    if is_windy:
        return (
            "Windy evening: noticeable Baltic gusts, moving waves, "
            "pines bending slightly and hair lifted by the wind."
        )
    if is_rainy:
        return (
            "Rainy evening: wet pavement, small puddles on the promenade, "
            "soft rain visible in lantern light and low clouds above the sea."
        )
    return (
        "Calm northern weather: light breeze, soft Baltic waves and clear visibility, "
        "no heavy storm right now."
    )


def _parse_moon_phase_and_sign(text: str) -> Tuple[Optional[str], Optional[str]]:
    if not text:
        return None, None

    s = text.lower()
    phase: Optional[str] = None

    if "полнолуние" in s or "full moon" in s:
        phase = "full"
    elif "новолуние" in s or "new moon" in s:
        phase = "new"
    elif "первая четверть" in s or "first quarter" in s or "waxing" in s or "растущ" in s:
        phase = "first_quarter"
    elif "последняя четверть" in s or "last quarter" in s or "waning" in s or "убывающ" in s:
        phase = "last_quarter"

    sign: Optional[str] = None
    for ru, en in ZODIAC_RU_EN.items():
        if ru in s:
            sign = en
            break
    if sign is None:
        for en in ZODIAC_EN:
            if en.lower() in s:
                sign = en
                break

    logger.debug("KLD astro parse: phase=%s sign=%s from %r", phase, sign, text)
    return phase, sign


def _astro_visual_sky(text: str) -> str:
    phase, sign = _parse_moon_phase_and_sign(text)
    if not phase and not sign:
        return ""

    strict = "exactly one Moon, no sun, no second moon, no planets"

    parts: List[str] = []
    if phase == "full":
        parts.append(f"a large bright full Moon, {strict}")
    elif phase == "new":
        parts.append(f"a deep dark northern sky with a subtle new-moon halo, {strict}")
    elif phase in ("first_quarter", "last_quarter"):
        parts.append(f"a crisp crescent Moon cutting through a deep blue evening sky, {strict}")
    else:
        parts.append(f"a calm night sky with a clearly visible Moon, {strict}")

    if sign:
        parts.append(f"the atmosphere subtly reflects the energy of {sign}")
    return " ".join(parts)


def _astro_visual_goddess(text: str) -> str:
    phase, sign = _parse_moon_phase_and_sign(text)
    if not phase and not sign:
        return ""

    phase_desc = {
        "full": "full-moon",
        "new": "new-moon",
        "first_quarter": "first-quarter",
        "last_quarter": "last-quarter",
    }.get(phase or "", "lunar")

    sign_phrase = sign or "the zodiac"

    return (
        f"a luminous Moon goddess in the {phase_desc} phase, "
        f"hovering above the Baltic coastline near Kaliningrad, "
        f"playing with the symbol of {sign_phrase}, "
        "her cold silver light spilling over the sea, dunes and pine forest; "
        "exactly one Moon in the sky, no sun, no second moon"
    )


def _sea_palette(ctx: KldImageContext) -> str:
    return _choice_by_date(
        ctx,
        "kld_sea_palette",
        [
            "cold teal and deep navy water with a band of warm peach light on the horizon",
            "steel-blue Baltic sea with soft turquoise highlights and a lilac–pink northern twilight sky",
            "dark indigo sea with pale moonlit reflections and almost monochrome blue–grey sky",
            "stormy teal and graphite water under heavy clouds with small breaks of warm light",
        ],
    )


def _map_palette(ctx: KldImageContext) -> str:
    return _choice_by_date(
        ctx,
        "kld_map_palette",
        [
            "cool teal sea around the region, muted green land and warm amber lights near the coast",
            "deep navy water, desaturated green land and soft orange–pink glow over seaside towns",
            "dark cyan sea with pale sand of the Curonian Spit and cold violet-blue sky above",
        ],
    )


def _dashboard_palette(ctx: KldImageContext) -> str:
    return _choice_by_date(
        ctx,
        "kld_dashboard_palette",
        [
            "cool blue and teal background with subtle neon-like cyan accents",
            "gradient from deep navy to violet with soft turquoise islands of light",
            "dark blue background with pale aqua and amber glows hinting at sea and land",
        ],
    )


_NO_TEXT_GUARD = (
    "No visible text anywhere, no tiny white text at the bottom, no pseudo-caption, "
    "no text, no captions, no labels, no UI, no logos, no watermarks, "
    "no watermark, no logo, no artist signature, no signature, no letters, no artist mark, no brand marks, "
    "absolutely no letters or numbers anywhere."
)

_NO_DOUBLE_MOON_GUARD = (
    "Night scene only. Exactly one Moon in the sky. No sun. No second moon. "
    "No extra bright discs."
)

_FORMAT_V2_PROMPT_VERSION = "v6"

_VISIBLE_MOON_PHASES = frozenset(
    {
        "waxing_crescent",
        "first_quarter",
        "waxing_gibbous",
        "full",
        "waning_gibbous",
        "last_quarter",
        "waning_crescent",
    }
)
_NEW_MOON_SKY_RULE = (
    "New-moon sky: no visible lunar disc; moonless or nearly moonless dark sky; "
    "clouds and Baltic weather remain the visual focus."
)
_NEW_MOON_NEGATIVE_RULE = (
    "Lunar negative: no visible moon, no crescent, no full moon, no lunar disc, no moon reflection."
)

KLD_SCENE_FAMILIES: tuple[str, ...] = (
    "curonian_spit_dunes",
    "svetlogorsk_cliff_coast",
    "zelenogradsk_promenade",
    "baltiysk_breakwater",
    "yantarny_wide_beach",
    "pine_forest_sea_path",
    "stormy_open_baltic",
    "quiet_lagoon_coast",
    "wet_seaside_promenade",
    "elevated_baltic_overlook",
)

_KLD_SCENE_TEXT = {
    "curonian_spit_dunes": "Curonian Spit dunes with marram grass, pine forest edge and open Baltic water",
    "svetlogorsk_cliff_coast": "Svetlogorsk cliff coast with steep green slope, sea below and northern sky",
    "zelenogradsk_promenade": "Zelenogradsk seaside promenade with Baltic horizon and realistic coastal railings",
    "baltiysk_breakwater": "Baltiysk breakwater stones, working-harbour edge in the distance and open sea",
    "yantarny_wide_beach": "Yantarny wide pale sand beach with low dune grasses and spacious Baltic horizon",
    "pine_forest_sea_path": "pine forest sea path opening toward the Baltic shore, realistic northern vegetation",
    "stormy_open_baltic": "open Baltic sea view with restless water, whitecaps when windy and layered cloud bands",
    "quiet_lagoon_coast": "quiet lagoon-like Baltic coast with reeds, low shore and subdued northern atmosphere",
    "wet_seaside_promenade": "wet seaside promenade after rain with dry-to-damp stone texture and realistic reflections",
    "elevated_baltic_overlook": "elevated Baltic overlook from a dune or cliff path, wide sea and coastline below",
}

KLD_COMPOSITIONS: tuple[str, ...] = (
    "wide diagonal shoreline composition",
    "foreground dune grass with open water behind",
    "low coastal stones leading line",
    "pine-framed side composition",
    "elevated overlook panorama",
    "promenade railing foreground",
    "open horizon with large sky",
    "breakwater perspective line",
)


def _filter_prompt_list(line: str, blocked_tokens: tuple[str, ...], add_items: list[str] | None = None) -> str:
    prefix, raw = line.split(":", 1)
    items = [x.strip().rstrip(".") for x in raw.split(";") if x.strip()]
    clean: list[str] = []
    for item in items:
        low = item.lower()
        if any(tok in low for tok in blocked_tokens):
            continue
        if item not in clean:
            clean.append(item)
    for item in add_items or []:
        if item and item not in clean:
            clean.append(item)
    return prefix + ": " + "; ".join(clean) + "."


def _extract_lunar_illumination(text: str) -> Optional[float]:
    for line in str(text or "").splitlines():
        low = line.lower()
        if not any(
            token in low
            for token in (
                "🌙",
                "moon",
                "lunar",
                "луна",
                "лун",
                "четверть",
                "серп",
                "полнолуние",
                "новолуние",
            )
        ):
            continue
        match = re.search(r"\b(\d{1,3}(?:[.,]\d+)?)\s*%", line)
        if match:
            value = float(match.group(1).replace(",", "."))
            if 0 <= value <= 100:
                return value
    return None


def _apply_moon_phase_guard(prompt: str, ctx: Any, source_text: str) -> str:
    """Keep evening moon geometry aligned with the stated lunar phase."""
    phase = getattr(ctx, "moon_phase", "unknown")
    if getattr(ctx, "post_type", "unknown") == "morning" or phase in ("unknown", "new"):
        return prompt

    illumination = _extract_lunar_illumination(source_text)
    if phase == "full" and (illumination is None or illumination >= 97):
        return prompt
    if phase != "full" and illumination is not None and illumination >= 97:
        return prompt

    moon_cues = {
        "waxing_crescent": "a small non-dominant thin waxing crescent Moon with the right side illuminated, not a full moon",
        "first_quarter": "a small non-dominant waxing half-to-gibbous Moon with the right side illuminated, not a full moon",
        "waxing_gibbous": "a small non-dominant waxing gibbous Moon with the right side illuminated, visibly not a full round moon",
        "waning_gibbous": "a small non-dominant waning gibbous Moon with the left side illuminated, visibly not a full round moon",
        "last_quarter": "a small non-dominant waning half-to-gibbous Moon with the left side illuminated, not a full moon",
        "waning_crescent": "a small non-dominant thin waning crescent Moon with the left side illuminated, not a full moon",
    }
    source_low = str(source_text or "").lower()
    is_waning_gibbous = phase == "waning_gibbous" or "убывающ" in source_low or "waning" in source_low
    is_waxing_gibbous = phase == "waxing_gibbous" or "растущ" in source_low or "waxing" in source_low
    illum_text = _fmt_percent(illumination)
    medium_non_full = illumination is not None and 35 <= illumination < 75
    if medium_non_full and phase == "first_quarter":
        cue = (
            f"a modest physically accurate waxing half-to-slight-gibbous Moon, about {illum_text} percent illuminated, "
            "right side illuminated; small non-dominant natural scale, not a full moon and not near-full"
        )
    elif medium_non_full and (phase == "last_quarter" or (phase == "waning_gibbous" and is_waning_gibbous)):
        cue = (
            f"a modest physically accurate waning half-to-slight-gibbous Moon, about {illum_text} percent illuminated, "
            "left side illuminated; small non-dominant natural scale, not a full moon and not near-full"
        )
    elif medium_non_full:
        cue = (
            f"a modest physically accurate non-full Moon, about {illum_text} percent illuminated; "
            "small non-dominant natural scale, not a full moon and not near-full"
        )
    elif illumination is not None and 90 <= illumination < 97 and is_waning_gibbous:
        cue = "a realistic waning gibbous Moon, 90-96 percent illuminated, visibly not a perfect full moon"
    elif illumination is not None and 90 <= illumination < 97 and is_waxing_gibbous:
        cue = "a realistic waxing gibbous Moon, 90-96 percent illuminated, visibly not a perfect full moon"
    elif illumination is not None and 90 <= illumination < 97:
        cue = "a realistic gibbous Moon, 90-96 percent illuminated, visibly not a perfect full moon"
    else:
        cue = moon_cues.get(phase)
    if not cue:
        return prompt

    guard_items = [
        "no full moon unless the actual phase is full moon",
        "no perfect full moon when illumination is below 97 percent",
        "no oversized full circular moon",
        "no fantasy supermoon",
        "no oversized moon",
        "no dominant focal moon",
        "no large bright round moon",
        "no oversized round moon for quarter or crescent phases",
        "no near-full moon for 35-75 percent illumination",
        "no giant decorative moon",
        "no poster-like lunar disc",
    ]
    positive_moon_blocked = (
        "bright full moon",
        "full moon",
        "perfect full moon",
        "large bright round moon",
        "oversized full circular moon",
        "fantasy supermoon",
        "near-full moon",
        "giant moon",
        "decorative moon",
        "poster-like lunar disc",
    )
    weather = getattr(ctx, "weather_main", "unknown")
    wind_gust = getattr(ctx, "wind_gust", None)
    cloud_softener = "the moon may be faint or partially obscured by clouds"
    allow_cloud_softener = weather in ("cloudy", "drizzle", "rain", "storm") or (
        isinstance(wind_gust, (int, float)) and wind_gust >= 12
    )
    out: list[str] = []
    for line in prompt.splitlines():
        stripped = line.strip()
        if stripped.startswith("Moon cue:"):
            out.append(f"Moon cue: {cue}.")
            continue
        if stripped.startswith("Must show:"):
            add_items = [cue]
            if allow_cloud_softener:
                add_items.append(cloud_softener)
            out.append(_filter_prompt_list(line, positive_moon_blocked, add_items))
            continue
        if stripped.startswith("Must avoid:"):
            out.append(_filter_prompt_list(line, (), guard_items))
            continue
        out.append(line)
    return "\n".join(out)


def _sanitize_format_v2_image_prompt(prompt: str, ctx: Any) -> str:
    """Remove image-generator trigger words from FORMAT_V2 prompts.

    Pollinations often treats words in negative instructions as objects to draw.
    For the current KLD safe-test we therefore convert weak crescent/SUP cues into
    positive empty-sky / empty-water cues instead of repeatedly saying what not to
    draw.
    """
    weather = getattr(ctx, "weather_main", "unknown")
    moon = getattr(ctx, "moon_phase", "unknown")
    sport = getattr(ctx, "sport", "none")
    level = getattr(ctx, "sport_level", "none")
    wind_gust = getattr(ctx, "wind_gust", None)

    hide_weak_moon = moon in ("waxing_crescent", "waning_crescent") and weather in ("cloudy", "drizzle", "rain", "storm")
    remove_sup = sport == "sup" and level == "experienced_only"
    if isinstance(wind_gust, (int, float)) and wind_gust >= 12:
        remove_sup = sport == "sup"

    moon_tokens = ("moon", "lunar", "crescent", "disc", "reflection path")
    sup_tokens = ("sup", "paddle", "board", "boat", "yacht", "sail", "mast", "windsurf")

    out: list[str] = []
    for line in prompt.splitlines():
        stripped = line.strip()
        if hide_weak_moon and stripped.startswith("Moon cue:"):
            out.append("Sky cue: cloud-dominant evening sky; celestial details hidden by clouds.")
            continue
        if remove_sup and stripped.startswith("Activity cue:"):
            out.append("Activity cue: unoccupied shoreline and open Baltic water; scale: none.")
            continue
        if stripped.startswith("Must show:"):
            blocked = ()
            add_items: list[str] = []
            if hide_weak_moon:
                blocked += moon_tokens
                add_items.extend([
                    "layered cloud cover as the main sky feature",
                    "no highlighted celestial object",
                ])
            if remove_sup:
                blocked += sup_tokens
                add_items.extend([
                    "unoccupied shoreline",
                    "open Baltic water with only natural wave texture",
                ])
            out.append(_filter_prompt_list(line, blocked, add_items) if blocked else line)
            continue
        if stripped.startswith("Must avoid:"):
            blocked = ()
            if hide_weak_moon:
                blocked += moon_tokens
            if remove_sup:
                blocked += sup_tokens
            out.append(_filter_prompt_list(line, blocked) if blocked else line)
            continue
        if stripped.startswith("Text restrictions:"):
            out.append("Text restrictions: " + _NO_TEXT_GUARD)
            continue
        out.append(line)
    return "\n".join(out)


def _apply_evening_moonlit_guard(prompt: str, ctx: Any, source_text: str) -> str:
    if getattr(ctx, "post_type", "unknown") != "evening":
        return prompt

    source_low = str(source_text or "").lower()
    illumination = _extract_lunar_illumination(source_text)
    phase = getattr(ctx, "moon_phase", "unknown")
    claims_full = phase == "full" or "полнолу" in source_low or "full moon" in source_low
    full_context = (claims_full and (illumination is None or illumination >= 97)) or (
        illumination is not None and illumination >= 97
    )
    near_full_context = full_context or claims_full or (illumination is not None and illumination >= 95)
    if not near_full_context:
        return prompt

    moon_wording = (
        "visible realistic full moon"
        if full_context
        else "visible realistic near-full Moon with realistic scale"
    )
    add_lines = [
        (
            "Evening moonlit cue: blue-hour Baltic coast; soft evening twilight; "
            f"{moon_wording}; cool moonlit sea; residual pale horizon glow on the right side of frame; "
            "realistic moon scale and natural moon position."
        ),
        (
            "Evening visual avoid: no bright daytime look; no morning look; "
            "no sun-dominant scene; no bright golden sunset; no oversized moon; "
            "no fantasy planet; no fantasy supermoon."
        ),
    ]
    if all(line.lower() in prompt.lower() for line in add_lines):
        return prompt
    lines = str(prompt or "").splitlines()
    insert_at = next((idx for idx, line in enumerate(lines) if line.startswith("Text restrictions:")), len(lines))
    for offset, line in enumerate(add_lines):
        if line.lower() not in prompt.lower():
            lines.insert(insert_at + offset, line)
    return "\n".join(lines)


def _apply_evening_direction_guard(prompt: str, ctx: Any) -> str:
    if getattr(ctx, "post_type", "unknown") != "evening":
        return prompt
    line = (
        "Evening direction cue: right-side horizon glow; late-day Baltic evening light "
        "comes from the right side of frame when any residual sun glow is visible."
    )
    if line.lower() in str(prompt or "").lower():
        return prompt
    lines = str(prompt or "").splitlines()
    insert_at = next((idx for idx, item in enumerate(lines) if item.startswith("Text restrictions:")), len(lines))
    lines.insert(insert_at, line)
    return "\n".join(lines)


def _fmt_percent(value: float | None) -> str:
    if value is None:
        return ""
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:.1f}".rstrip("0").rstrip(".")


def _apply_storm_moon_visual_guard(prompt: str, ctx: Any, source_text: str) -> str:
    if getattr(ctx, "post_type", "unknown") != "evening":
        return prompt

    prompt = str(prompt or "").replace(
        "Create an atmospheric, information-driven weather illustration for VayboMeter Kaliningrad.",
        "Create a photorealistic Baltic coastline weather scene for VayboMeter Kaliningrad.",
    )
    source_low = str(source_text or "").lower()
    weather = getattr(ctx, "weather_main", "unknown")
    wind_gust = getattr(ctx, "wind_gust", None)
    stormy = weather == "storm" or "шторм" in source_low or (
        isinstance(wind_gust, (int, float)) and wind_gust >= 15
    )
    illumination = _extract_lunar_illumination(source_text)
    phase = getattr(ctx, "moon_phase", "unknown")
    moon_hidden = phase == "new" or (illumination is not None and illumination <= 5)
    visible_moon = phase in _VISIBLE_MOON_PHASES and not moon_hidden
    medium_non_full = illumination is not None and 35 <= illumination < 90 and phase in (
        "first_quarter",
        "waxing_gibbous",
        "waning_gibbous",
        "last_quarter",
    )
    if not stormy and not (phase == "waning_gibbous" and illumination is not None and 90 <= illumination < 97) and not medium_non_full:
        return prompt

    moon_phrase = ""
    if medium_non_full and phase in ("last_quarter", "waning_gibbous"):
        moon_phrase = f"physically accurate waning non-full Moon, {_fmt_percent(illumination)}% illuminated, left side lit, modest non-dominant natural scale"
    elif medium_non_full and phase in ("first_quarter", "waxing_gibbous"):
        moon_phrase = f"physically accurate waxing non-full Moon, {_fmt_percent(illumination)}% illuminated, right side lit, modest non-dominant natural scale"
    elif phase == "waning_gibbous" and illumination is not None:
        moon_phrase = f"realistic waning gibbous Moon, {_fmt_percent(illumination)}% illuminated"
    elif phase == "waxing_gibbous" and illumination is not None:
        moon_phrase = f"realistic waxing gibbous Moon, {_fmt_percent(illumination)}% illuminated"
    elif illumination is not None and 90 <= illumination < 97:
        moon_phrase = f"realistic gibbous Moon, {_fmt_percent(illumination)}% illuminated"

    add_lines = []
    if stormy:
        add_lines.append(
            (
                "Storm visual adherence: photorealistic Baltic coastline; blue-hour stormy evening; "
                "strong wind and restless waves; natural weather-photo realism."
            )
        )
    elif medium_non_full and visible_moon:
        add_lines.append(
            (
                "Moon visual adherence: photorealistic Baltic evening; phase-accurate Moon stays modest, "
                "secondary, and physically plausible; weather cues remain dominant."
            )
        )
    avoid_label = "Storm visual avoid" if stormy else "Moon visual avoid"
    if visible_moon:
        add_lines.append(
            (
                "Moon scale adherence: "
                + (moon_phrase or "phase-accurate Moon")
                + "; small-to-medium natural moon scale."
            )
        )
    lunar_avoid = (
        "no perfect full moon, no near-full moon for medium illumination, no oversized moon, "
        "no giant decorative moon, no fantasy supermoon, "
        if visible_moon
        else ""
    )
    add_lines.append(
        (
            f"{avoid_label}: no illustration, no vector art, no painting, no poster, no cartoon, "
            f"{lunar_avoid}no bright daytime."
        )
    )
    lines = prompt.splitlines()
    insert_at = next((idx for idx, item in enumerate(lines) if item.startswith("Text restrictions:")), len(lines))
    for offset, line in enumerate(add_lines):
        if line.lower() not in prompt.lower():
            lines.insert(insert_at + offset, line)
    return "\n".join(lines)


def _date_from_key(date_key: str) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(date_key)[:10])
    except Exception:
        return None


def _target_date_for(date_key: str, post_type: str) -> str:
    base = _date_from_key(date_key)
    if not base:
        return date_key
    if (post_type or "").strip().lower() == "evening":
        return (base + dt.timedelta(days=1)).isoformat()
    return base.isoformat()


def _weather_scenario(ctx: Any) -> str:
    return str(getattr(ctx, "weather_main", "unknown") or "unknown")


def _gust_category(ctx: Any) -> str:
    gust = getattr(ctx, "wind_gust", None)
    wind = getattr(ctx, "wind_speed", None)
    value = gust if isinstance(gust, (int, float)) else wind
    if not isinstance(value, (int, float)):
        return "wind_unknown"
    if value >= 15:
        return "gust_15_plus"
    if value >= 10:
        return "gust_10_14"
    if value >= 7:
        return "gust_7_9"
    return "calm_to_breezy"


def _rain_cloud_fog_category(ctx: Any) -> str:
    visibility = str(getattr(ctx, "visibility_condition", "clear") or "clear")
    if visibility != "clear":
        return visibility
    weather = _weather_scenario(ctx)
    if weather in {"rain", "storm", "drizzle", "snow", "fog", "cloudy"}:
        return weather
    return "clear_or_mixed"


def _scene_index(date_key: str, post_type: str, weather_main: str, variation_attempt: int) -> int:
    base_date = _date_from_key(date_key)
    ordinal = base_date.toordinal() if base_date else _stable_index(date_key, "scene_ordinal", 10_000)
    offset = 5 if (post_type or "").strip().lower() == "evening" else 0
    if weather_main == "storm":
        offset += KLD_SCENE_FAMILIES.index("stormy_open_baltic")
    elif weather_main in {"rain", "drizzle"}:
        offset += KLD_SCENE_FAMILIES.index("wet_seaside_promenade")
    return (ordinal * 3 + offset + int(variation_attempt or 0)) % len(KLD_SCENE_FAMILIES)


def kld_scene_metadata(
    ctx: Any,
    *,
    date_key: str,
    post_type: str,
    source_text: str = "",
    variation_attempt: int = 0,
) -> dict[str, str]:
    weather = _weather_scenario(ctx)
    scene_family = KLD_SCENE_FAMILIES[_scene_index(date_key, post_type, weather, variation_attempt)]
    composition_idx = (
        (_date_from_key(date_key).toordinal() if _date_from_key(date_key) else 0)
        + _stable_index(weather, "kld_composition_offset", len(KLD_COMPOSITIONS))
        + int(variation_attempt or 0) * 3
    ) % len(KLD_COMPOSITIONS)
    composition = KLD_COMPOSITIONS[composition_idx]
    illumination = _extract_lunar_illumination(source_text)
    return {
        "region": "kld",
        "forecast_date": date_key,
        "target_date": _target_date_for(date_key, post_type),
        "post_type": post_type or "evening",
        "prompt_version": _FORMAT_V2_PROMPT_VERSION,
        "scene_family": scene_family,
        "scene_text": _KLD_SCENE_TEXT[scene_family],
        "composition": composition,
        "weather_scenario": weather,
        "wind_gust_category": _gust_category(ctx),
        "rain_cloud_fog_category": _rain_cloud_fog_category(ctx),
        "visibility_condition": str(getattr(ctx, "visibility_condition", "clear") or "clear"),
        "visibility_forecast_window": str(getattr(ctx, "visibility_forecast_window", "none") or "none"),
        "current_visibility_m": _fmt_percent(getattr(ctx, "current_visibility_m", None)) or "unknown",
        "morning_min_visibility_m": _fmt_percent(getattr(ctx, "morning_min_visibility_m", None)) or "unknown",
        "reported_visibility_m": _fmt_percent(getattr(ctx, "reported_visibility_m", None)) or "unknown",
        "reported_visibility_threshold_m": _fmt_percent(getattr(ctx, "reported_visibility_threshold_m", None)) or "unknown",
        "lunar_phase": str(getattr(ctx, "moon_phase", "unknown") or "unknown"),
        "lunar_illumination": _fmt_percent(illumination) or "unknown",
        "variation_attempt": str(int(variation_attempt or 0)),
    }


def kld_visual_cache_key(metadata: dict[str, str]) -> str:
    fields = (
        "region",
        "forecast_date",
        "target_date",
        "post_type",
        "prompt_version",
        "scene_family",
        "composition",
        "weather_scenario",
        "wind_gust_category",
        "rain_cloud_fog_category",
        "visibility_condition",
        "visibility_forecast_window",
        "current_visibility_m",
        "morning_min_visibility_m",
        "reported_visibility_m",
        "reported_visibility_threshold_m",
        "lunar_phase",
        "lunar_illumination",
        "variation_attempt",
    )
    return ";".join(f"{field}={metadata.get(field, '')}" for field in fields)


def _format_v2_style_name(
    ctx: Any,
    *,
    date_key: str,
    post_type: str,
    source_text: str,
    variation_attempt: int = 0,
) -> str:
    illumination = _extract_lunar_illumination(source_text)
    wind_gust = getattr(ctx, "wind_gust", None)
    storm_state = "storm" if (
        getattr(ctx, "weather_main", "unknown") == "storm"
        or "шторм" in str(source_text or "").lower()
        or (isinstance(wind_gust, (int, float)) and wind_gust >= 15)
    ) else "nonstorm"
    seed = "|".join(
        [
            _FORMAT_V2_PROMPT_VERSION,
            date_key,
            post_type or "evening",
            str(getattr(ctx, "weather_main", "unknown")),
            storm_state,
            str(getattr(ctx, "moon_phase", "unknown")),
            str(getattr(ctx, "visibility_condition", "clear")),
            str(getattr(ctx, "visibility_forecast_window", "none")),
            _fmt_percent(getattr(ctx, "current_visibility_m", None)) or "current_visibility_unknown",
            _fmt_percent(getattr(ctx, "morning_min_visibility_m", None)) or "morning_visibility_unknown",
            _fmt_percent(getattr(ctx, "reported_visibility_m", None)) or "reported_visibility_unknown",
            _fmt_percent(getattr(ctx, "reported_visibility_threshold_m", None)) or "visibility_threshold_unknown",
            _fmt_percent(illumination) or "illum_unknown",
            str(int(variation_attempt or 0)),
        ]
    )
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:8]
    return f"format_v2_scene_cues_{_FORMAT_V2_PROMPT_VERSION}_{digest}"


_KLD_SCENE_FRAMING = (
    "dune path leading toward the Baltic shoreline",
    "seaside promenade facing the Baltic horizon",
    "pine-framed Baltic shore",
    "open beach horizon",
    "elevated promenade view over the coast",
)
_KLD_FOREGROUND = (
    "dune grass in the foreground",
    "wooden railing in the foreground",
    "wet sand foreground",
    "coastal stones in the foreground",
    "pine branch silhouette framing one edge",
)
_KLD_DISTANCE = (
    "wider coastal panorama",
    "medium coastal scene",
    "closer shoreline texture",
)
_KLD_EVENING_DETAIL = (
    "layered cloud bands remain subordinate to the stated weather",
    "subtle celestial detail only when allowed by the lunar and cloud cues",
    "shoreline texture carries the composition without changing the sea state",
)
_KLD_MORNING_DETAIL = (
    "broad daylight depth with a clearly readable horizon",
    "fresh daylight separation between shore, sea, and sky",
    "practical daytime shoreline emphasis",
)
_KLD_VISIBILITY_DETAIL = (
    "softened Baltic horizon matching the stated visibility",
    "atmospheric depth carries the early-morning composition",
    "shoreline landmarks fade naturally with distance",
)


def _extract_prompt_date(text: str, fallback: dt.date | None = None) -> str:
    value = str(text or "")
    match = re.search(r"\b(\d{2})[./-](\d{2})[./-](\d{4})\b", value)
    if match:
        return f"{match.group(3)}-{match.group(2)}-{match.group(1)}"
    match = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", value)
    if match:
        return match.group(0)
    return fallback.isoformat() if fallback else "undated"


def _stable_variant(seed: str, dimension: str, options: tuple[str, ...]) -> str:
    digest = hashlib.sha256(f"{seed}|{dimension}".encode("utf-8")).digest()
    return options[int.from_bytes(digest[:8], "big") % len(options)]


def _stable_index(seed: str, dimension: str, count: int) -> int:
    if count <= 0:
        return 0
    digest = hashlib.sha256(f"{seed}|{dimension}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % count


def apply_kld_controlled_variety(
    prompt: str,
    ctx: Any,
    *,
    date_key: str,
    post_type: str,
    source_text: str = "",
    variation_attempt: int = 0,
) -> str:
    """Add deterministic composition variety without changing weather rules."""
    mode = (post_type or "evening").strip().lower()
    metadata = kld_scene_metadata(
        ctx,
        date_key=date_key,
        post_type=mode,
        source_text=source_text,
        variation_attempt=variation_attempt,
    )
    seed = "|".join(
        [
            date_key,
            mode,
            str(getattr(ctx, "weather_main", "unknown")),
            str(getattr(ctx, "moon_phase", "unknown")),
            str(getattr(ctx, "sport", "none")),
            str(getattr(ctx, "region", "kaliningrad")),
            metadata["scene_family"],
            metadata["composition"],
            str(int(variation_attempt or 0)),
        ]
    )
    visibility_window = str(getattr(ctx, "visibility_forecast_window", "none") or "none")
    visibility_condition = str(getattr(ctx, "visibility_condition", "clear") or "clear")
    if visibility_window in {"current_morning", "tomorrow_morning"} and visibility_condition != "clear":
        detail_options = _KLD_VISIBILITY_DETAIL
    else:
        detail_options = _KLD_MORNING_DETAIL if mode == "morning" else _KLD_EVENING_DETAIL
    variety_line = (
        "Controlled composition: "
        f"dominant Baltic scene family: {metadata['scene_family']}; "
        f"scene: {metadata['scene_text']}; "
        f"composition: {metadata['composition']}; "
        f"{_stable_variant(seed, 'framing', _KLD_SCENE_FRAMING)}; "
        f"{_stable_variant(seed, 'foreground', _KLD_FOREGROUND)}; "
        f"{_stable_variant(seed, 'distance', _KLD_DISTANCE)}; "
        f"{_stable_variant(seed, 'time-detail', detail_options)}; "
        "photorealistic Baltic coastal photography; realistic northern vegetation; "
        "realistic sea state; natural atmospheric perspective; no illustration; "
        "no digital painting; no poster."
    )
    lines = str(prompt or "").splitlines()
    insert_at = next(
        (index for index, line in enumerate(lines) if line.startswith("Text restrictions:")),
        len(lines),
    )
    lines.insert(insert_at, variety_line)
    return "\n".join(lines)


def _is_lunar_prompt_fragment(value: str) -> bool:
    low = str(value or "").lower()
    return any(
        token in low
        for token in (
            "moon",
            "lunar",
            "crescent",
            "gibbous",
            "quarter",
            "celestial detail",
            "celestial object",
        )
    )


def _strip_lunar_list_items(line: str) -> str:
    prefix, raw = line.split(":", 1)
    items = [item.strip().rstrip(".") for item in raw.split(";") if item.strip()]
    kept = [item for item in items if not _is_lunar_prompt_fragment(item)]
    return f"{prefix}: " + "; ".join(kept) + ("." if kept else "")


def _strip_lunar_avoid_clauses(line: str) -> str:
    prefix, raw = line.split(":", 1)
    clauses = [item.strip().rstrip(".") for item in re.split(r"[;,]", raw) if item.strip()]
    kept = [item for item in clauses if not _is_lunar_prompt_fragment(item)]
    if not kept:
        return ""
    if prefix.strip() == "Moon visual avoid":
        prefix = "Visual avoid"
    return f"{prefix}: " + "; ".join(kept) + "."


def _canonical_visible_lunar_cue(phase: str, illumination: float | None, source_text: str) -> str | None:
    if phase not in _VISIBLE_MOON_PHASES:
        return None
    pct = _fmt_percent(illumination)
    pct_text = f", {pct}% illuminated" if pct else ""
    source_low = str(source_text or "").lower()

    if phase == "full" and illumination is not None and illumination < 97:
        if "убывающ" in source_low or "waning" in source_low:
            phase = "waning_gibbous"
        elif "растущ" in source_low or "waxing" in source_low:
            phase = "waxing_gibbous"
        else:
            return (
                f"Lunar cue: one realistic gibbous Moon{pct_text}, visibly not full, "
                "at small-to-medium natural non-dominant scale."
            )

    descriptions = {
        "waxing_crescent": "one realistic thin waxing crescent Moon, right side lit",
        "first_quarter": "one physically accurate first-quarter Moon, right side lit, visibly non-full",
        "waxing_gibbous": "one realistic waxing gibbous Moon, right side lit, visibly non-full",
        "full": "one realistic full Moon",
        "waning_gibbous": "one realistic waning gibbous Moon, left side lit, visibly non-full",
        "last_quarter": "one physically accurate last-quarter Moon, left side lit, visibly non-full",
        "waning_crescent": "one realistic thin waning crescent Moon, left side lit",
    }
    return f"Lunar cue: {descriptions[phase]}{pct_text}, at small-to-medium natural non-dominant scale."


def _canonical_lunar_negative(phase: str, illumination: float | None) -> str:
    if phase == "full" and (illumination is None or illumination >= 97):
        return "Lunar negative: no oversized moon, no fantasy supermoon, no duplicate moon."
    if phase in ("waxing_crescent", "waning_crescent"):
        return (
            "Lunar negative: no round lunar disc, no oversized moon, "
            "no duplicate moon, no water reflection path."
        )
    return (
        "Lunar negative: no perfect full moon, no oversized moon, "
        "no fantasy supermoon, no duplicate moon."
    )


def finalize_kld_lunar_prompt(prompt: str, ctx: Any, source_text: str) -> str:
    """Apply the final lunar truth without changing weather or scene composition."""
    if getattr(ctx, "post_type", "unknown") != "evening":
        return prompt

    phase = str(getattr(ctx, "moon_phase", "unknown") or "unknown")
    illumination = _extract_lunar_illumination(source_text)
    moon_hidden = phase == "new" or (illumination is not None and illumination <= 5)
    visible_moon = phase in _VISIBLE_MOON_PHASES and not moon_hidden
    visibility_override = (
        str(getattr(ctx, "visibility_forecast_window", "none") or "none") == "tomorrow_morning"
        and str(getattr(ctx, "visibility_condition", "clear") or "clear") != "clear"
    )
    out: list[str] = []

    for line in str(prompt or "").splitlines():
        stripped = line.strip()
        if stripped.startswith(("Moon cue:", "Moon scale adherence:", "Moon visual adherence:", "Lunar cue:", "Lunar negative:", "New-moon sky:")):
            continue
        if stripped.startswith("Evening moonlit cue:"):
            if not visibility_override:
                out.append(
                    "Evening light adherence: blue-hour Baltic coast; soft evening twilight; "
                    "residual pale horizon glow on the right side of frame."
                )
            continue
        if stripped.startswith("Sky cue:") and _is_lunar_prompt_fragment(stripped):
            out.append("Sky cue: cloud-dominant evening sky.")
            continue
        if stripped.startswith(("Must show:", "Must avoid:", "Controlled composition:")):
            cleaned = _strip_lunar_list_items(line)
            if cleaned.split(":", 1)[1].strip(" ."):
                out.append(cleaned)
            continue
        if stripped.startswith(("Evening visual avoid:", "Storm visual avoid:", "Moon visual avoid:", "Visual avoid:")):
            cleaned = _strip_lunar_avoid_clauses(line)
            if cleaned:
                out.append(cleaned)
            continue
        out.append(line)

    insert_at = next((idx for idx, line in enumerate(out) if line.startswith("Text restrictions:")), len(out))
    lunar_lines: list[str] = []
    if visibility_override:
        lunar_lines.extend(
            (
                "Visibility time adherence: next-day early-morning forecast window only; neutral diffused Baltic morning light; not an all-day condition.",
                "Visibility visual avoid: no evening twilight; no moon-led scene; no all-day fog implication.",
            )
        )
        if moon_hidden:
            lunar_lines.append(_NEW_MOON_NEGATIVE_RULE)
    elif moon_hidden:
        lunar_lines.extend((_NEW_MOON_SKY_RULE, _NEW_MOON_NEGATIVE_RULE))
    elif visible_moon:
        cue = _canonical_visible_lunar_cue(phase, illumination, source_text)
        if cue:
            lunar_lines.append(cue)
            lunar_lines.append(_canonical_lunar_negative(phase, illumination))
    out[insert_at:insert_at] = lunar_lines
    return "\n".join(out)


def _final_prompt_contains_visible_moon_cue(prompt: str) -> bool:
    for line in str(prompt or "").splitlines():
        stripped = line.strip().lower()
        if stripped.startswith(("lunar negative:", "must avoid:", "evening visual avoid:", "storm visual avoid:", "visual avoid:", "visibility visual avoid:")):
            continue
        if stripped.startswith("new-moon sky:"):
            continue
        if stripped.startswith(("lunar cue:", "moon cue:", "moon scale adherence:", "evening moonlit cue:")):
            return True
        if any(
            token in stripped
            for token in (
                "visible moon",
                "bright moon",
                "full moon",
                "crescent moon",
                "gibbous moon",
                "phase-accurate moon",
                "moonlit sea",
                "moon reflection",
                "lunar disc",
                "celestial detail",
                "celestial object",
            )
        ):
            return True
    return False


def kld_lunar_prompt_diagnostics(prompt: str, ctx: Any, source_text: str) -> dict[str, Any]:
    phase = str(getattr(ctx, "moon_phase", "unknown") or "unknown")
    illumination = _extract_lunar_illumination(source_text)
    moon_hidden = phase == "new" or (illumination is not None and illumination <= 5)
    visibility_override = (
        str(getattr(ctx, "visibility_forecast_window", "none") or "none") == "tomorrow_morning"
        and str(getattr(ctx, "visibility_condition", "clear") or "clear") != "clear"
    )
    visible_allowed = phase in _VISIBLE_MOON_PHASES and not moon_hidden and not visibility_override
    if moon_hidden:
        rule = "new_moon_hidden"
    elif visibility_override:
        rule = "tomorrow_morning_visibility_override"
    elif visible_allowed:
        rule = f"{phase}_visible"
    else:
        rule = "unknown_no_moon"
    return {
        "parsed_moon_phase": phase,
        "lunar_illumination": illumination,
        "visible_moon_allowed": visible_allowed,
        "final_lunar_rule": rule,
        "final_prompt_contains_visible_moon_cue": _final_prompt_contains_visible_moon_cue(prompt),
    }


def _build_format_v2_visual_prompt(
    final_format_v2_message: str,
    *,
    post_type: str = "evening",
    date: Optional[dt.date] = None,
    variation_attempt: int = 0,
    visibility_context: Any = None,
) -> Tuple[str, str]:
    """Build KLD FORMAT_V2 image prompt through VisualContext -> SceneCues.

    This path is deterministic and side-effect free: it parses the already built
    Telegram message and does not fetch weather/marine data, call LLM, send
    Telegram messages, or generate an image.
    """
    from visual_context_kld import build_visual_context
    from visual_rules import apply_visual_rules, build_prompt_from_cues

    structured_visibility = visibility_context
    if structured_visibility is None:
        structured_visibility = getattr(final_format_v2_message, "visibility_context", None)
    ctx = build_visual_context(
        final_format_v2_message,
        post_type=post_type,
        visibility_context=structured_visibility,
    )
    cues = apply_visual_rules(ctx)
    prompt = build_prompt_from_cues(cues)
    prompt = _apply_moon_phase_guard(prompt, ctx, final_format_v2_message)
    prompt = _sanitize_format_v2_image_prompt(prompt, ctx)
    prompt = _apply_evening_moonlit_guard(prompt, ctx, final_format_v2_message)
    prompt = _apply_evening_direction_guard(prompt, ctx)
    prompt = _apply_storm_moon_visual_guard(prompt, ctx, final_format_v2_message)
    date_key = _extract_prompt_date(final_format_v2_message, date)
    prompt = apply_kld_controlled_variety(
        prompt,
        ctx,
        date_key=date_key,
        post_type=post_type,
        source_text=final_format_v2_message,
        variation_attempt=variation_attempt,
    )
    prompt = finalize_kld_lunar_prompt(prompt, ctx, final_format_v2_message)
    style_name = _format_v2_style_name(
        ctx,
        date_key=date_key,
        post_type=post_type,
        source_text=final_format_v2_message,
        variation_attempt=variation_attempt,
    )
    logger.info(
        "KLD_FORMAT_V2_IMG_PROMPT: post_type=%s weather=%s sport=%s/%s moon=%s source=%s",
        getattr(ctx, "post_type", post_type),
        getattr(ctx, "weather_main", "unknown"),
        getattr(ctx, "sport", "none"),
        getattr(ctx, "sport_level", "none"),
        getattr(ctx, "moon_phase", "unknown"),
        (getattr(ctx, "evidence", {}) or {}).get("weather_source_used"),
    )
    logger.info(
        "KLD lunar prompt diagnostics: %s",
        json.dumps(
            kld_lunar_prompt_diagnostics(prompt, ctx, final_format_v2_message),
            ensure_ascii=False,
            sort_keys=True,
        ),
    )
    return prompt, style_name


def _style_prompt_map_mood(ctx: KldImageContext) -> Tuple[str, str]:
    style_name = "map_mood"

    weather_text = _weather_flavour(ctx)
    astro_sky = _astro_visual_sky(ctx.astro_mood_en)
    palette = _map_palette(ctx)

    storm_hint = ""
    if ctx.storm:
        storm_hint = (
            "A storm front approaches from the sea: darker clouds over the coast, "
            "wind lines over water, more contrast and drama."
        )

    prompt = (
        "Dreamy stylized flat map of the Kaliningrad region by the Baltic Sea. "
        "The outline of the region is clear but has no labels. "
        "The Baltic sea surrounds the region, rendered with "
        f"{palette}. "
        "Soft northern twilight sky in the upper half. "
        f"{storm_hint} "
        "Seaside towns along the coast feel like this: "
        f"{ctx.marine_mood or 'cool, breezy Baltic shoreline with long sandy beaches and a fresh wind'}. "
        "Inland areas feel different: "
        f"{ctx.inland_mood or 'quieter forests, lakes and the city of Kaliningrad with more grounded energy'}. "
        f"{weather_text} "
        "Simple clean shapes, subtle texture, cinematic lighting, soft gradients, high quality digital illustration. "
        f"{_NO_TEXT_GUARD} "
        "Square aspect ratio, suitable as a VayboMeter Kaliningrad weather thumbnail. "
        f"{_NO_DOUBLE_MOON_GUARD}"
    )

    if ctx.astro_mood_en:
        prompt += f" Astro mood: {ctx.astro_mood_en}."
    if astro_sky:
        prompt += f" Sky cue: {astro_sky}."
    return style_name, prompt


def _style_prompt_sea_dunes(ctx: KldImageContext) -> Tuple[str, str]:
    style_name = "sea_dunes"

    weather_text = _weather_flavour(ctx)
    astro_sky = _astro_visual_sky(ctx.astro_mood_en)
    palette = _sea_palette(ctx)

    storm_extra = ""
    if ctx.storm:
        storm_extra = (
            "The sea is rougher with higher waves and white foam; "
            "wind-driven spray; dramatic clouds and a sense of storm warning."
        )

    prompt = (
        "Cinematic Baltic coastal evening near Kaliningrad. "
        "In the foreground, the Baltic sea with waves and moonlit reflections. "
        "On one side, sandy dunes and tall pine trees of the Curonian Spit "
        "or seaside towns like Zelenogradsk or Svetlogorsk, slightly silhouetted. "
        "Further inland, faint hints of forests and distant town lights represent Kaliningrad. "
        f"The shoreline mood is: {ctx.marine_mood or 'fresh, breezy Baltic air with long beaches and a bit of salt in the wind'}. "
        f"Inland mood is: {ctx.inland_mood or 'cooler forests and calmer city streets with grounded, slower energy'}. "
        f"{weather_text} "
        f"{storm_extra} "
        "Above everything, the northern night sky is painted with this palette: "
        f"{palette}. "
        "A clearly visible Moon dominates the composition, its light forming a shimmering path on the water. "
        "Atmospheric, slightly dramatic lighting, soft gradients, high quality digital painting, no people. "
        f"{_NO_TEXT_GUARD} "
        "Square format composition, suitable as a VayboMeter Kaliningrad weather thumbnail. "
        f"{_NO_DOUBLE_MOON_GUARD}"
    )

    if ctx.astro_mood_en:
        prompt += f" The Moon and sky reflect: {ctx.astro_mood_en}."
    if astro_sky:
        prompt += f" Sky cue: {astro_sky}."
    return style_name, prompt


def _style_prompt_mini_dashboard(ctx: KldImageContext) -> Tuple[str, str]:
    style_name = "mini_dashboard"

    weather_text = _weather_flavour(ctx)
    astro_sky = _astro_visual_sky(ctx.astro_mood_en)
    palette = _dashboard_palette(ctx)

    storm_hint = ""
    if ctx.storm:
        storm_hint = "Subtle storm motif: sharper diagonals, higher contrast, a warning-like energy (still no text). "

    prompt = (
        "Modern minimalist weather dashboard–style illustration for the Kaliningrad region, but purely pictorial. "
        "A flat icon-like silhouette of the region in the center, with the Baltic sea below it, "
        "and several glowing circular markers along the coastline to represent seaside towns, "
        "plus a marker near the center for Kaliningrad and inland areas. "
        f"{storm_hint}"
        f"Coast markers feel cool and breezy: {ctx.marine_mood or 'typical Baltic evening by the sea, with wind and fresh air'}; "
        f"the inland marker feels calmer: {ctx.inland_mood or 'forests, lakes and quieter city streets'}. "
        f"{weather_text} "
        "Above the region silhouette, a clearly visible Moon and a few soft clouds hint at the astro energy. "
        f"The background and markers use this colour palette: {palette}. "
        "Clean flat design, smooth gradients, subtle depth. "
        f"{_NO_TEXT_GUARD} "
        "Square layout, high quality digital illustration, optimized as a neutral weather thumbnail. "
        f"{_NO_DOUBLE_MOON_GUARD}"
    )

    if ctx.astro_mood_en:
        prompt += f" Astro mood: {ctx.astro_mood_en}."
    if astro_sky:
        prompt += f" Sky cue: {astro_sky}."
    return style_name, prompt


def _style_prompt_moon_goddess(ctx: KldImageContext) -> Tuple[str, str]:
    goddess = _astro_visual_goddess(ctx.astro_mood_en)
    weather_text = _weather_flavour(ctx)

    if not goddess:
        return _style_prompt_sea_dunes(ctx)

    style_name = "moon_goddess"
    palette = _sea_palette(ctx)

    storm_extra = ""
    if ctx.storm:
        storm_extra = "Storm-warning mood: wind-whipped sea, dramatic clouds, and silver spray in the air. "

    prompt = (
        "Mythic night scene above the Baltic coastline of the Kaliningrad region. "
        f"{weather_text} "
        f"{storm_extra}"
        "Below, long sandy beaches, dark Baltic water and pine forest silhouettes. "
        f"Inland you can feel: {ctx.inland_mood or 'quieter forests, lakes and the city of Kaliningrad glowing in the distance'}. "
        f"In the sky, {goddess}. "
        f"The sky and sea follow this color palette: {palette}. "
        "The sea and land are softly lit by her cold silver light, with subtle reflections on the water and dunes. "
        "Rich colours, cinematic fantasy illustration, high detail, soft glow. "
        f"{_NO_TEXT_GUARD} "
        "Square composition, suitable as a mystical VayboMeter Kaliningrad thumbnail. "
        f"{_NO_DOUBLE_MOON_GUARD}"
    )
    return style_name, prompt


_STYLE_FUNCS = {
    "sea_dunes": _style_prompt_sea_dunes,
    "map_mood": _style_prompt_map_mood,
    "mini_dashboard": _style_prompt_mini_dashboard,
    "moon_goddess": _style_prompt_moon_goddess,
}


def build_kld_evening_prompt(
    date: dt.date,
    marine_mood: str,
    inland_mood: str,
    astro_mood_en: str = "",
    *,
    force_style: Optional[str] = None,
    storm: bool = False,
    final_format_v2_message: Optional[str] = None,
    post_type: str = "evening",
    variation_attempt: int = 0,
    visibility_context: Any = None,
) -> Tuple[str, str]:
    """
    Returns: (prompt_text, style_name)

    FORMAT_V2 path: if final_format_v2_message is provided, build the prompt
    through VisualContext -> SceneCues. If that pipeline fails, log the exception
    and fall back to the legacy deterministic style prompt below.
    """
    if final_format_v2_message and final_format_v2_message.strip():
        try:
            return _build_format_v2_visual_prompt(
                final_format_v2_message,
                post_type=post_type or "evening",
                date=date,
                variation_attempt=variation_attempt,
                visibility_context=visibility_context,
            )
        except Exception:
            logger.exception("KLD_FORMAT_V2_IMG_PROMPT failed; falling back to legacy image prompt")

    date_for_astro = date + dt.timedelta(days=1)
    cal_phrase = _astro_phrase_from_calendar(date_for_astro)

    if cal_phrase and astro_mood_en:
        astro_combined = f"{cal_phrase}. {astro_mood_en}"
    elif cal_phrase:
        astro_combined = cal_phrase
    else:
        astro_combined = astro_mood_en or ""

    ctx = KldImageContext(
        date=date,
        marine_mood=(marine_mood or "").strip(),
        inland_mood=(inland_mood or "").strip(),
        astro_mood_en=astro_combined.strip(),
        storm=bool(storm),
    )

    if force_style and force_style in _STYLE_FUNCS:
        style_name, prompt = _STYLE_FUNCS[force_style](ctx)
    else:
        rnd = random.Random(date.toordinal() * 9973 + 84)

        weighted_style_fns = (
            [_style_prompt_sea_dunes] * 2
            + [_style_prompt_map_mood] * 2
            + [_style_prompt_mini_dashboard] * 1
            + [_style_prompt_moon_goddess] * 1
        )

        if ctx.storm:
            weighted_style_fns = (
                [_style_prompt_sea_dunes] * 4
                + [_style_prompt_map_mood] * 3
                + [_style_prompt_mini_dashboard] * 1
                + [_style_prompt_moon_goddess] * 1
            )

        style_fn = rnd.choice(weighted_style_fns)
        style_name, prompt = style_fn(ctx)

    logger.info(
        "KLD_IMG_PROMPT: date=%s style=%s marine=%r inland=%r astro=%r storm=%s",
        date.isoformat(),
        style_name,
        ctx.marine_mood,
        ctx.inland_mood,
        ctx.astro_mood_en,
        ctx.storm,
    )
    return prompt, style_name
