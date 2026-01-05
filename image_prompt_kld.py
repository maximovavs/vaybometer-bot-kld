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
import random
import logging
import json
from pathlib import Path
from typing import Tuple, Optional, List, Dict


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
    "No text, no captions, no labels, no UI, no logos, no watermarks, "
    "absolutely no letters or numbers anywhere."
)

_NO_DOUBLE_MOON_GUARD = (
    "Night scene only. Exactly one Moon in the sky. No sun. No second moon. "
    "No extra bright discs."
)


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
) -> Tuple[str, str]:
    """
    Returns: (prompt_text, style_name)
    """
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
