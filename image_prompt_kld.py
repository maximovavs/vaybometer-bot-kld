#!/usr/bin/env python3 
# -*- coding: utf-8 -*-
"""
image_prompt_kld.py

Промты для вечернего поста ВайбоМетра по Калининграду.

Стили:
1) "sea_dunes"      — Балтика + дюны/сосны + Луна.
2) "map_mood"       — стилизованная карта Калининградской области.
3) "mini_dashboard" — абстрактный «дашборд» без текста.
4) "moon_goddess"   — мифологичная сцена с Луной-богиней над Балтикой.

Для разнообразия:
- стиль и палитра выбираются детерминированно от даты;
- фаза Луны и знак берутся из lunar_calendar.json (на завтра),
  чтобы менялась форма Луны и настроение неба.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import random
import logging
import json
from pathlib import Path
from typing import Tuple, Optional, List


@dataclasses.dataclass(frozen=True)
class KldImageContext:
    date: dt.date
    marine_mood: str
    inland_mood: str
    astro_mood_en: str = ""


logger = logging.getLogger(__name__)

# Ветер/дождь по ключевым словам (если захочешь потом подставлять реальные mood'ы)
WIND_KEYWORDS = (
    "ветер", "ветрен", "шквал", "порыв", "бриз",
    "wind", "windy", "gust", "gusty", "breeze", "storm wind",
)

RAIN_KEYWORDS = (
    "дожд", "ливн", "гроза", "грoз",
    "rain", "rainy", "shower", "showers", "thunderstorm", "storm",
)

ZODIAC_RU_EN = {
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


# ───────────────────── утилиты случайности ─────────────────────

def _choice_by_date(ctx: KldImageContext, salt: str, options: List[str]) -> str:
    seed = ctx.date.toordinal() * 10007 + sum(ord(c) for c in salt)
    rnd = random.Random(seed)
    return rnd.choice(options)


# ───────────────────── lunar_calendar.json ─────────────────────

def _load_calendar(path: str = "lunar_calendar.json") -> dict:
    try:
        data = json.loads(Path(path).read_text("utf-8"))
    except Exception:
        return {}
    if isinstance(data, dict) and isinstance(data.get("days"), dict):
        return data["days"]
    return data if isinstance(data, dict) else {}


def _astro_phrase_from_calendar(date_for_astro: dt.date) -> str:
    cal = _load_calendar()
    rec = cal.get(date_for_astro.isoformat(), {})
    if not isinstance(rec, dict):
        return ""

    phase_raw = (rec.get("phase_name") or rec.get("phase") or "").lower()
    sign_raw = (rec.get("sign") or rec.get("zodiac") or "").lower()

    phase_en: Optional[str] = None
    if "полнолуние" in phase_raw or "full" in phase_raw:
        phase_en = "Full Moon"
    elif "новолуние" in phase_raw or "new" in phase_raw:
        phase_en = "New Moon"
    elif "первая четверть" in phase_raw or "first quarter" in phase_raw or "растущ" in phase_raw or "waxing" in phase_raw:
        phase_en = "First Quarter Moon"
    elif "последняя четверть" in phase_raw or "last quarter" in phase_raw or "убывающ" in phase_raw or "waning" in phase_raw:
        phase_en = "Last Quarter Moon"

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
    return " ".join(parts)


# ───────────────────── погода / астрология ─────────────────────

def _weather_flavour(marine_mood: str, inland_mood: str) -> str:
    text = f"{marine_mood} {inland_mood}".lower()
    is_windy = any(k in text for k in WIND_KEYWORDS)
    is_rainy = any(k in text for k in RAIN_KEYWORDS)

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

    parts: List[str] = []
    if phase == "full":
        parts.append("a bright full Moon hanging low above the Baltic sea")
    elif phase == "new":
        parts.append("a very dark northern night sky with only a thin lunar crescent")
    elif phase in ("first_quarter", "last_quarter"):
        parts.append("a strong crescent Moon cutting through a deep blue evening sky")

    if not parts:
        parts.append("a calm night sky with a clearly visible Moon")

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
        "her cold silver light spilling over the sea, dunes and pine forest"
    )


# ───────────────────── палитры ─────────────────────

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


# ───────────────────── стили ─────────────────────

def _style_prompt_map_mood(ctx: KldImageContext) -> Tuple[str, str]:
    style_name = "map_mood"

    weather_text = _weather_flavour(ctx.marine_mood, ctx.inland_mood)
    astro_sky = _astro_visual_sky(ctx.astro_mood_en)
    palette = _map_palette(ctx)

    prompt = (
        "Dreamy stylized flat map of the Kaliningrad region by the Baltic Sea. "
        "The outline of the region is clear but has no labels or text. "
        "The Baltic sea surrounds the region in the lower part of the image, "
        f"rendered with {palette}. "
        "Soft northern sunset–twilight sky in the upper half. "
        "Seaside towns and resorts along the coast feel like this: "
        f"{ctx.marine_mood or 'cool, breezy Baltic shoreline with long sandy beaches and a fresh wind'}. "
        "Inland areas feel different: "
        f"{ctx.inland_mood or 'quieter forests, lakes and the city of Kaliningrad with more grounded energy'}. "
        f"{weather_text} "
        "Simple clean shapes, subtle texture, cinematic lighting, soft gradients, high quality digital illustration. "
        "No text, no captions, no labels, no logos, no UI, absolutely no letters or numbers anywhere. "
        "Square aspect ratio, suitable as a VayboMeter Kaliningrad weather thumbnail for Telegram or Facebook."
    )

    if ctx.astro_mood_en:
        prompt += f" The overall astro energy feels like: {ctx.astro_mood_en}."
    if astro_sky:
        prompt += f" The sky area subtly shows this: {astro_sky}."
    return style_name, prompt


def _style_prompt_sea_dunes(ctx: KldImageContext) -> Tuple[str, str]:
    """
    Основной стиль: Балтийское море + дюны/сосны + Луна.
    """
    style_name = "sea_dunes"

    weather_text = _weather_flavour(ctx.marine_mood, ctx.inland_mood)
    astro_sky = _astro_visual_sky(ctx.astro_mood_en)
    palette = _sea_palette(ctx)

    prompt = (
        "Cinematic Baltic coastal evening near Kaliningrad. "
        "In the foreground, the Baltic sea with gentle but noticeable waves, "
        "cold teal and deep navy tones, a narrow band of light where the Moon reflects on the water. "
        "On the right or left side, sandy dunes and tall pine trees of the Curonian Spit "
        "or seaside towns like Zelenogradsk or Svetlogorsk, slightly silhouetted. "
        "Further inland, soft hints of forests and distant town lights represent Kaliningrad and other inland cities. "
        f"The shoreline mood is: {ctx.marine_mood or 'fresh, breezy Baltic air with long beaches and a bit of salt in the wind'}. "
        f"Inland mood is: {ctx.inland_mood or 'cooler forests and calmer city streets with grounded, slower energy'}. "
        f"{weather_text} "
        "Above everything, the northern night sky is painted with this palette: "
        f"{palette}. "
        "A clearly visible Moon dominates the composition, its light forming a shimmering path on the water. "
        "Atmospheric, slightly dramatic lighting, soft gradients, high quality digital painting, no people. "
        "No text, no captions, no labels, no logos, absolutely no letters or numbers anywhere. "
        "Square format composition, suitable as a VayboMeter Kaliningrad weather thumbnail."
    )

    if ctx.astro_mood_en:
        prompt += f" The Moon and sky subtly reflect this astro mood: {ctx.astro_mood_en}."
    if astro_sky:
        prompt += f" Visually the sky looks like: {astro_sky}."
    return style_name, prompt


def _style_prompt_mini_dashboard(ctx: KldImageContext) -> Tuple[str, str]:
    style_name = "mini_dashboard"

    weather_text = _weather_flavour(ctx.marine_mood, ctx.inland_mood)
    astro_sky = _astro_visual_sky(ctx.astro_mood_en)
    palette = _dashboard_palette(ctx)

    prompt = (
        "Modern minimalist weather dashboard–style illustration for the Kaliningrad region, but purely pictorial. "
        "A flat icon-like silhouette of the region in the center, with the Baltic sea below it, "
        "and several glowing circular markers along the coastline to represent seaside towns, "
        "plus a marker near the center for Kaliningrad and inland areas. "
        f"Coast markers feel cool and breezy: {ctx.marine_mood or 'typical Baltic evening by the sea, with wind and fresh air'}; "
        f"the inland marker feels calmer and more grounded: {ctx.inland_mood or 'forests, lakes and quieter city streets'}. "
        f"{weather_text} "
        "Above the region silhouette, an almost–full Moon and a few soft clouds hint at the astro energy. "
        f"The background and markers use this colour palette: {palette}. "
    )

    if ctx.astro_mood_en:
        prompt += f" The astro mood for tomorrow is: {ctx.astro_mood_en}. "
    if astro_sky:
        prompt += f"The sky zone of the dashboard visually reflects this: {astro_sky}. "

    prompt += (
        "Clean flat design, smooth gradients, subtle depth, no data tables. "
        "No text, no numbers, no labels, no country names, absolutely no typography of any kind. "
        "Square layout, high quality digital illustration, optimized as a neutral weather thumbnail."
    )
    return style_name, prompt


def _style_prompt_moon_goddess(ctx: KldImageContext) -> Tuple[str, str]:
    goddess = _astro_visual_goddess(ctx.astro_mood_en)
    weather_text = _weather_flavour(ctx.marine_mood, ctx.inland_mood)

    if not goddess:
        return _style_prompt_sea_dunes(ctx)

    style_name = "moon_goddess"
    palette = _sea_palette(ctx)

    prompt = (
        "Mythic evening scene above the Baltic coastline of the Kaliningrad region. "
        f"{weather_text} "
        f"Below, long sandy beaches, dark Baltic water and pine forest silhouettes reflect this mood: "
        f"{ctx.marine_mood or 'fresh wind, salty air and long horizon over the sea'}. "
        f"Inland you can feel: {ctx.inland_mood or 'quieter forests, lakes and the city of Kaliningrad glowing in the distance'}. "
        f"In the sky, {goddess}. "
        f"The sky and sea follow this color palette: {palette}. "
        "The sea and land are softly lit by her cold silver light, with subtle reflections on the water and dunes. "
        "Rich colours, cinematic fantasy illustration, high detail, soft glow. "
        "No text, no captions, no labels, no logos, absolutely no letters or numbers anywhere. "
        "Square composition, suitable as a mystical VayboMeter Kaliningrad thumbnail."
    )
    return style_name, prompt


_STYLES = [
    _style_prompt_sea_dunes,
    _style_prompt_map_mood,
    _style_prompt_mini_dashboard,
    _style_prompt_moon_goddess,
]


def build_kld_evening_prompt(
    date: dt.date,
    marine_mood: str,
    inland_mood: str,
    astro_mood_en: str = "",
) -> Tuple[str, str]:
    """
    Собирает промт и возвращает (prompt_text, style_name).

    - randomness детерминируется датой;
    - фаза Луны и знак подмешиваются из lunar_calendar.json на завтра.
    """
    cal_phrase = _astro_phrase_from_calendar(date + dt.timedelta(days=1))
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
    )

    rnd = random.Random(date.toordinal() * 9973 + 84)

    weighted_style_fns = (
        [_style_prompt_sea_dunes] * 2
        + [_style_prompt_map_mood] * 2
        + [_style_prompt_mini_dashboard] * 1
        + [_style_prompt_moon_goddess] * 1
    )

    style_fn = rnd.choice(weighted_style_fns)
    style_name, prompt = style_fn(ctx)

    logger.info(
        "KLD_IMG_PROMPT: date=%s style=%s marine=%r inland=%r astro=%r",
        date.isoformat(),
        style_name,
        ctx.marine_mood,
        ctx.inland_mood,
        ctx.astro_mood_en,
    )
    return prompt, style_name
