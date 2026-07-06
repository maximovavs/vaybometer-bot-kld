#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Visual rules engine for VayboMeter weather images.

Step 3 of the visual weather matrix implementation.

Input: VisualContext from visual_context_kld.py.
Output: SceneCues used later by prompt builders.

This module does not generate or send images. It only applies deterministic
if/else rules preserved in docs/visual_weather_matrix.md.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from dataclasses import dataclass, field
from typing import Any, Optional

try:
    from visual_context_kld import VisualContext, build_visual_context
except Exception:  # pragma: no cover - allows docs/tools to import gracefully
    VisualContext = Any  # type: ignore
    build_visual_context = None  # type: ignore

WIND_LIGHT = 4
WIND_MODERATE = 7
WIND_STRONG = 10
WIND_VERY_STRONG = 13

GUST_MODERATE = 10
GUST_STRONG = 13
GUST_VERY_STRONG = 16

WAVE_LOW = 0.4
WAVE_MEDIUM = 0.8
WAVE_HIGH = 1.0

WARM_KLD = 22
MILD_KLD = 17
COOL_KLD = 13

SEA_COLD = 16
SEA_VERY_COLD = 13


@dataclass(frozen=True)
class SceneCues:
    region: str
    post_type: str
    base_scene: str
    palette: str
    light_style: str
    weather_visual: str
    sea_state: str
    activity_visual: str
    activity_scale: str
    moon_visual: str
    overall_mood: str
    must_show: list[str] = field(default_factory=list)
    must_avoid: list[str] = field(default_factory=list)
    validation_notes: list[str] = field(default_factory=list)
    source_context: dict[str, Any] = field(default_factory=dict)


def _has(value: Optional[float]) -> bool:
    return isinstance(value, (int, float))


def _add_unique(items: list[str], value: str | None) -> None:
    value = (value or "").strip()
    if value and value not in items:
        items.append(value)


def _score_mood(score: Optional[float]) -> str:
    if not _has(score):
        return "balanced weather mood"
    assert score is not None
    if score >= 8.5:
        return "pleasant, attractive, calm-confidence"
    if score >= 7.0:
        return "good with caveats"
    if score >= 6.0:
        return "mixed, cautious comfort"
    return "visibly cautious day"


def _derived_mood(ctx: VisualContext) -> str:
    score = getattr(ctx, "score", None)
    if _has(score):
        return _score_mood(score)

    weather = getattr(ctx, "weather_main", "unknown")
    wind_gust = getattr(ctx, "wind_gust", None)

    if weather == "storm":
        return "visibly cautious day"
    if weather == "rain":
        return "mixed, cautious comfort"
    if weather == "drizzle":
        return "soft cautious Baltic mood"
    if _has(wind_gust) and wind_gust is not None and wind_gust >= GUST_STRONG:
        return "good with wind caveats"
    return "balanced weather mood"


def _normalize_evidence_dict(evidence: dict[str, Any]) -> dict[str, Any]:
    """Keep evidence flat and JSON-clean for logs.

    Only numeric pairs are allowed inside temp_pairs. Any strings or lists of
    strings that accidentally appear there are moved to ignored_temp_like_lines.
    """
    out: dict[str, Any] = {"evidence_schema_version": 1}
    ignored_from_temp: list[str] = []
    for key, value in (evidence or {}).items():
        if key == "temp_pairs":
            clean_pairs: list[list[float]] = []
            if isinstance(value, list):
                for item in value:
                    if (
                        isinstance(item, (list, tuple))
                        and len(item) == 2
                        and isinstance(item[0], (int, float))
                        and isinstance(item[1], (int, float))
                    ):
                        clean_pairs.append([float(item[0]), float(item[1])])
                    elif isinstance(item, str):
                        ignored_from_temp.append(item)
                    elif isinstance(item, (list, tuple)) and all(isinstance(x, str) for x in item):
                        ignored_from_temp.extend(str(x) for x in item)
            out["temp_pairs"] = clean_pairs
            continue
        out[key] = value
    if ignored_from_temp:
        existing = out.get("ignored_temp_like_lines", [])
        if not isinstance(existing, list):
            existing = [str(existing)]
        out["ignored_temp_like_lines"] = list(existing) + ignored_from_temp
    return out


def _clean_source_context(ctx: VisualContext) -> dict[str, Any]:
    payload = dataclasses.asdict(ctx)
    payload["evidence"] = _normalize_evidence_dict(payload.get("evidence") or {})
    return payload


def _weather_visual(ctx: VisualContext, must_show: list[str], must_avoid: list[str]) -> str:
    w = getattr(ctx, "weather_main", "unknown")

    if w == "clear":
        _add_unique(must_show, "mostly clear sky")
        _add_unique(must_show, "good visibility and readable horizon")
        _add_unique(must_avoid, "rain, heavy clouds, storm mood")
        return "clear Baltic weather"

    if w == "partly_cloudy":
        _add_unique(must_show, "visible clouds with sun gaps")
        _add_unique(must_show, "balanced soft northern light")
        return "partly cloudy Baltic weather"

    if w == "cloudy":
        _add_unique(must_show, "cloud-dominant sky")
        _add_unique(must_show, "diffused light")
        _add_unique(must_show, "moon, if present, is partly veiled by clouds and not dominant")
        _add_unique(must_avoid, "hard bright sun")
        _add_unique(must_avoid, "clear moonlit postcard scene")
        return "cloudy Baltic weather"

    if w == "drizzle":
        _add_unique(must_show, "soft grey sky")
        _add_unique(must_show, "humid wet Baltic air")
        _add_unique(must_show, "subtle drizzle texture")
        _add_unique(must_show, "wet promenade or damp shoreline foreground")
        _add_unique(must_avoid, "bright harsh sun")
        _add_unique(must_avoid, "cheerful dry beach postcard look")
        return "drizzle and wet Baltic air"

    if w == "rain":
        _add_unique(must_show, "visible rain streaks")
        _add_unique(must_show, "overcast or mostly overcast sky")
        _add_unique(must_show, "wet surfaces and darker rainy clouds")
        _add_unique(must_show, "puddles or wet promenade if a promenade is visible")
        _add_unique(must_show, "steady visible rain streaks across the frame")
        _add_unique(must_show, "dense diagonal raindrop streaks")
        _add_unique(must_show, "rain clearly readable in foreground and midground")
        _add_unique(must_show, "wet grey Baltic atmosphere")
        _add_unique(must_show, "dark low rain clouds")
        _add_unique(must_show, "wet shoreline or wet promenade foreground")
        _add_unique(must_show, "continuous low overcast sky")
        _add_unique(must_show, "empty coast, no leisure mood")
        return "rainy Baltic weather"

    if w == "fog":
        _add_unique(must_show, "low horizon visibility")
        _add_unique(must_show, "soft haze and reduced contrast")
        _add_unique(must_show, "softened distant shoreline")
        return "foggy Baltic atmosphere"

    if w == "snow":
        _add_unique(must_show, "snow in the air or on the ground")
        _add_unique(must_show, "cold winter sky")
        _add_unique(must_avoid, "summer greenery")
        return "snowy Baltic winter scene"

    if w == "storm":
        _add_unique(must_show, "dramatic clouds")
        _add_unique(must_show, "rough sea")
        _add_unique(must_show, "spray and wave energy")
        _add_unique(must_show, "storm-warning Baltic atmosphere")
        _add_unique(must_show, "rougher choppy sea with white foam")
        _add_unique(must_show, "strong wind visible in dune grass and pines")
        _add_unique(must_show, "dark dramatic cloud shelf")
        _add_unique(must_show, "sea spray or wind-driven rain")
        _add_unique(must_show, "unsafe coastal conditions feeling")
        _add_unique(must_avoid, "relaxed casual sport foreground")
        _add_unique(must_avoid, "peaceful beach mood")
        _add_unique(must_avoid, "calm sea surface")
        _add_unique(must_avoid, "sunny breaks that make the scene feel pleasant")
        return "storm-warning Baltic atmosphere"

    return "general Baltic weather mood"


def _sea_state(ctx: VisualContext, must_show: list[str], must_avoid: list[str]) -> str:
    wind_avg = getattr(ctx, "wind_avg", None)
    wind_gust = getattr(ctx, "wind_gust", None)
    wave_height = getattr(ctx, "wave_height", None)
    sea_temp = getattr(ctx, "sea_temp", None)

    sea_state = "cool Baltic sea"

    if _has(wind_avg) and _has(wind_gust):
        assert wind_avg is not None and wind_gust is not None
        if wind_avg <= WIND_LIGHT and wind_gust <= GUST_MODERATE:
            sea_state = "calm or lightly textured water"
            _add_unique(must_show, "calm or lightly textured Baltic water")
        elif wind_avg <= WIND_MODERATE:
            sea_state = "light chop on the Baltic water"
            _add_unique(must_show, "light chop on water")
            _add_unique(must_show, "mild movement in dune grass or pines")
        elif wind_avg <= WIND_STRONG:
            sea_state = "active textured Baltic water"
            _add_unique(must_show, "active textured water")
            _add_unique(must_show, "visible movement in vegetation and sky")
        else:
            sea_state = "windy Baltic sea"
            _add_unique(must_show, "windy sea")
            _add_unique(must_show, "dynamic sky motion")

    if _has(wind_gust) and wind_gust is not None and wind_gust >= GUST_STRONG:
        _add_unique(must_show, "dynamic sea texture")
        _add_unique(must_show, "windy atmosphere")
        _add_unique(must_show, "wind is visible in clouds, dune grass, pines, and water texture")
        _add_unique(must_avoid, "mirror-flat sea")
        _add_unique(must_avoid, "full calm visual language")
        _add_unique(must_avoid, "romantic glassy moon reflection path")
        sea_state = "gusty, dynamic Baltic sea"
    elif _has(wind_gust) and wind_gust is not None and wind_gust >= 8:
        _add_unique(must_show, "visibly textured Baltic water")
        _add_unique(must_show, "small wind-made whitecaps where the sea is exposed")
        _add_unique(must_show, "wind-shaped dune grass and moving pine branches")
        _add_unique(must_avoid, "perfectly calm water")
        sea_state = "breezy textured Baltic sea"

    if _has(wave_height):
        assert wave_height is not None
        if wave_height >= WAVE_HIGH:
            _add_unique(must_show, "visibly wavy sea")
            _add_unique(must_show, "choppy water with irregular wave texture")
            _add_unique(must_show, "white foam near shoreline")
            _add_unique(must_show, "wind force visible on the sea surface")
            _add_unique(must_avoid, "perfect calm activity mood")
            sea_state = "visibly wavy Baltic sea"
        elif wave_height >= WAVE_MEDIUM:
            _add_unique(must_show, "moderately active sea")
            sea_state = "moderately active Baltic sea"
        elif wave_height < WAVE_LOW:
            _add_unique(must_show, "quite calm sea with light wind texture, not mirror-flat")
            sea_state = "quite calm Baltic sea"

    if _has(sea_temp):
        assert sea_temp is not None
        if sea_temp <= SEA_VERY_COLD:
            _add_unique(must_show, "very cold water feeling")
            _add_unique(must_show, "cool blue-grey water palette")
            if getattr(ctx, "sport", "none") != "none":
                _add_unique(must_show, "wetsuit implied for any water-sport figure")
        elif sea_temp <= SEA_COLD:
            _add_unique(must_show, "fresh cold water feeling")

    return sea_state


def _activity_visual(ctx: VisualContext, must_show: list[str], must_avoid: list[str], validation_notes: list[str]) -> tuple[str, str]:
    sport = getattr(ctx, "sport", "none")
    level = getattr(ctx, "sport_level", "none")
    wind_gust = getattr(ctx, "wind_gust", None)
    wave_height = getattr(ctx, "wave_height", None)
    weather_main = getattr(ctx, "weather_main", "unknown")

    if sport == "none":
        _add_unique(must_avoid, "visible main water-sport athlete")
        return "no explicit water-sport athlete", "none"

    if sport == "sup":
        _add_unique(must_avoid, "sailboat")
        _add_unique(must_avoid, "boat or yacht replacing the SUP")
        _add_unique(must_avoid, "visible sail or mast")
        _add_unique(must_avoid, "windsurfer sail confused with paddleboard")

        if weather_main in ("rain", "storm"):
            _add_unique(must_avoid, "visible SUP rider in rain")
            _add_unique(must_avoid, "relaxed SUP holiday mood")
            _add_unique(validation_notes, "Rain/storm + SUP: remove visible SUP rider")
            return "no visible paddleboarder", "none"

        if weather_main == "drizzle" and level == "experienced_only":
            _add_unique(must_show, "only a tiny distant paddleboard silhouette if visible at all")
            _add_unique(must_show, "if visible, it is a standing human silhouette with a paddle on a flat board, no sail")
            _add_unique(must_show, "damp coastal atmosphere is more important than the athlete")
            _add_unique(must_avoid, "clear prominent SUP rider")
            _add_unique(must_avoid, "relaxed beginner SUP scene")
            _add_unique(validation_notes, "Drizzle + experienced-only SUP: tiny hint only")
            return "tiny distant standing paddleboard silhouette only, no sail", "tiny_hint"

        if level == "excellent":
            visual = "one visible standing paddleboarder with paddle on relatively calm water"
            scale = "visible_secondary"
            _add_unique(must_show, "one visible standing paddleboarder with paddle")
            _add_unique(must_show, "relatively calm water around SUP")
        elif level == "good":
            visual = "one secondary standing paddleboarder with paddle in the midground"
            scale = "secondary"
            _add_unique(must_show, "one paddleboarder as a secondary scene element, standing with paddle")
        elif level == "experienced_only":
            visual = "small distant standing paddleboarder with paddle only, no sail"
            scale = "distant"
            _add_unique(must_show, "small distant standing paddleboarder with paddle")
            _add_unique(must_show, "flat paddleboard silhouette, not a boat")
            _add_unique(must_show, "conditions are visually more important than the athlete")
            _add_unique(must_avoid, "relaxed beginner SUP scene")
        else:
            visual = "SUP not recommended; avoid prominent paddleboarder"
            scale = "remove_or_tiny_hint"
            _add_unique(must_avoid, "clear prominent SUP rider")

        if _has(wind_gust) and wind_gust is not None and wind_gust > 12:
            scale = "distant_or_removed"
            _add_unique(must_avoid, "hero SUP rider")
            _add_unique(validation_notes, "SUP with gusts > 12 m/s: no hero SUP")
        if _has(wind_gust) and wind_gust is not None and wind_gust > 14:
            scale = "experienced_only_or_removed"
            _add_unique(validation_notes, "SUP with gusts > 14 m/s: experienced-only or remove SUP")
        if _has(wave_height) and wave_height is not None and wave_height >= 0.8:
            _add_unique(must_avoid, "calm beginner SUP look")
            _add_unique(validation_notes, "SUP with wave >= 0.8 m: avoid calm beginner look")
        return visual, scale

    if sport in ("kite", "wing", "windsurf"):
        sport_name = {"kite": "kite", "wing": "wing", "windsurf": "windsurfer"}.get(sport, "wind sport")
        if level == "excellent":
            visual = f"1-3 small {sport_name} riders or kites on the horizon"
            scale = "visible_horizon"
            _add_unique(must_show, f"1-3 small {sport_name} cues on the horizon")
            _add_unique(must_show, "dynamic sea texture")
        elif level == "good":
            visual = f"1-2 small {sport_name} cues on the horizon"
            scale = "small_horizon"
            _add_unique(must_show, f"1-2 small {sport_name} cues")
        elif level == "experienced_only":
            visual = f"small distant {sport_name} rider only"
            scale = "distant"
            _add_unique(must_show, f"small distant {sport_name} rider")
            _add_unique(must_show, "wind and water conditions dominate the image")
            _add_unique(must_avoid, "easy beginner recreational mood")
        else:
            visual = f"avoid hero {sport_name} rider"
            scale = "remove_or_tiny_hint"
            _add_unique(must_avoid, f"hero {sport_name} rider")

        if _has(wind_gust) and wind_gust is not None and wind_gust >= 10:
            _add_unique(must_show, "small kites or wind-sport cue on the horizon")
        if _has(wind_gust) and wind_gust is not None and wind_gust < 8:
            _add_unique(must_avoid, "large kite as key object")
            _add_unique(validation_notes, "Wind sport with gusts < 8 m/s: avoid prominent kite")
        if weather_main == "storm" and level != "excellent":
            _add_unique(must_avoid, "hero wind-sport rider in storm")
            _add_unique(validation_notes, "Storm + wind sport: no hero rider unless explicitly excellent")
        return visual, scale

    return "no explicit water-sport athlete", "none"


def _moon_visual(ctx: VisualContext, must_show: list[str], must_avoid: list[str], validation_notes: list[str]) -> str:
    phase = getattr(ctx, "moon_phase", "unknown")
    post_type = getattr(ctx, "post_type", "unknown")
    time_hint = getattr(ctx, "time_hint", "unknown")

    if post_type == "morning":
        _add_unique(must_avoid, "moon")
        _add_unique(must_avoid, "moon emphasis")
        _add_unique(validation_notes, "Morning: suppress all moon cues")
        return "not used for morning daylight scene"

    if phase == "new":
        _add_unique(must_show, "moonless dark sky if evening or night")
        _add_unique(must_avoid, "visible moon")
        _add_unique(must_avoid, "crescent moon")
        _add_unique(must_avoid, "full moon")
        _add_unique(must_avoid, "lunar disc")
        _add_unique(must_avoid, "moon reflection on water")
        _add_unique(validation_notes, "New moon: do not draw any visible moon")
        return "no visible moon / moonless sky"

    if phase == "waxing_crescent":
        _add_unique(must_show, "small thin waxing crescent moon, not a full circular moon")
        _add_unique(must_show, "crescent is subtle and secondary behind or between clouds")
        _add_unique(must_avoid, "full moon")
        _add_unique(must_avoid, "round moon")
        _add_unique(must_avoid, "large bright moon")
        _add_unique(must_avoid, "dominant moon disc")
        _add_unique(must_avoid, "moon reflection path on water")
        _add_unique(validation_notes, "Waxing crescent: avoid full/round moon and bright reflection path")
        return "small thin waxing crescent moon, subtle not dominant"
    if phase == "first_quarter":
        _add_unique(must_show, "half moon")
        _add_unique(must_avoid, "full moon")
        return "first quarter half moon"
    if phase == "waxing_gibbous":
        _add_unique(must_show, "almost full waxing moon, not full")
        _add_unique(must_avoid, "perfect full moon")
        return "waxing gibbous moon"
    if phase == "full":
        _add_unique(must_show, "bright full moon")
        if time_hint in ("sunset", "night") or post_type in ("evening", "forecast_tomorrow"):
            _add_unique(must_show, "moon reflection on Baltic water if water is visible")
        return "bright full moon"
    if phase == "waning_gibbous":
        _add_unique(must_show, "bright waning gibbous moon")
        _add_unique(must_avoid, "perfect full moon")
        return "waning gibbous moon"
    if phase == "last_quarter":
        _add_unique(must_show, "half moon")
        _add_unique(must_avoid, "full moon")
        return "last quarter half moon"
    if phase == "waning_crescent":
        _add_unique(must_show, "thin waning crescent moon")
        _add_unique(must_avoid, "full moon")
        _add_unique(must_avoid, "round moon")
        _add_unique(must_avoid, "moon reflection path on water")
        return "thin waning crescent moon"

    if post_type == "morning":
        _add_unique(must_avoid, "moon as dominant subject")
        return "moon not emphasized"
    return "moon optional, not dominant unless phase is explicit"


def _temperature_visual(ctx: VisualContext, must_show: list[str], must_avoid: list[str]) -> None:
    temp_max = getattr(ctx, "temp_max", None)
    sea_temp = getattr(ctx, "sea_temp", None)
    sport = getattr(ctx, "sport", "none")
    post_type = getattr(ctx, "post_type", "unknown")

    if not _has(temp_max) or temp_max is None:
        return

    if temp_max < MILD_KLD:
        _add_unique(must_show, "fresh Baltic feeling")
        _add_unique(must_show, "cooler tones")
        _add_unique(must_avoid, "beach-relax summer mood")
        return

    if temp_max >= WARM_KLD:
        coast_is_fresh = (_has(sea_temp) and sea_temp is not None and sea_temp <= SEA_COLD) or sport != "none" or post_type in ("evening", "forecast_tomorrow")
        if coast_is_fresh:
            _add_unique(must_show, "mild-to-warm Baltic coastal evening with fresh sea air, still northern not tropical")
            if _has(sea_temp) and sea_temp is not None and sea_temp <= SEA_COLD:
                _add_unique(must_show, "warm inland temperatures contrast with fresh Baltic water")
            return
        _add_unique(must_show, "pleasant warm Baltic day, still northern not tropical")
        return

    _add_unique(must_show, "mild fresh Baltic coastal weather")


def apply_visual_rules(ctx: VisualContext) -> SceneCues:
    must_show: list[str] = []
    must_avoid: list[str] = []
    validation_notes: list[str] = []

    base_scene = "Baltic coast near Kaliningrad, dunes, pines, promenade, sea horizon"
    palette = "cool Baltic grey-blue, muted sand, pine green, restrained northern light"
    light_style = "soft northern light"
    post_type = getattr(ctx, "post_type", "unknown")

    if post_type == "morning":
        base_scene = "Baltic coast near Kaliningrad in daylight, dunes, pines, promenade, sea horizon"
        palette = "fresh Baltic morning grey-blue, muted sand, pine green, natural daylight"
        light_style = "soft low-angle morning light"
        _add_unique(must_show, "neutral morning daylight")
        _add_unique(must_show, "fresh Baltic morning air")
        _add_unique(must_show, "soft low-angle morning light")
        _add_unique(must_show, "practical weather-for-the-day mood")
        _add_unique(must_avoid, "moon")
        _add_unique(must_avoid, "sunset colors")
        _add_unique(must_avoid, "night atmosphere")
        _add_unique(must_avoid, "mystical evening mood")
        _add_unique(must_avoid, "sunset palette")
        _add_unique(must_avoid, "night sky")

    _add_unique(must_show, "recognizable Baltic/Kaliningrad atmosphere")
    _add_unique(must_show, "dunes, pines, promenade, or Baltic sea horizon")
    _add_unique(must_avoid, "tropical palette")
    _add_unique(must_avoid, "palm trees")
    _add_unique(must_avoid, "Caribbean lagoon")

    _temperature_visual(ctx, must_show, must_avoid)

    weather_visual = _weather_visual(ctx, must_show, must_avoid)
    sea_state = _sea_state(ctx, must_show, must_avoid)
    activity_visual, activity_scale = _activity_visual(ctx, must_show, must_avoid, validation_notes)
    moon_visual = _moon_visual(ctx, must_show, must_avoid, validation_notes)
    overall_mood = _derived_mood(ctx)

    if getattr(ctx, "score", None) is not None and ctx.score is not None and ctx.score < 6.0:
        _add_unique(must_avoid, "idyllic holiday postcard look")

    return SceneCues(
        region="kaliningrad",
        post_type=getattr(ctx, "post_type", "unknown"),
        base_scene=base_scene,
        palette=palette,
        light_style=light_style,
        weather_visual=weather_visual,
        sea_state=sea_state,
        activity_visual=activity_visual,
        activity_scale=activity_scale,
        moon_visual=moon_visual,
        overall_mood=overall_mood,
        must_show=must_show,
        must_avoid=must_avoid,
        validation_notes=validation_notes,
        source_context=_clean_source_context(ctx),
    )


def build_prompt_from_cues(cues: SceneCues) -> str:
    parts = [
        "Create an atmospheric, information-driven weather illustration for VayboMeter Kaliningrad.",
        f"Base scene: {cues.base_scene}.",
        f"Palette: {cues.palette}.",
        f"Light: {cues.light_style}.",
        f"Weather: {cues.weather_visual}.",
        f"Sea and wind state: {cues.sea_state}.",
        f"Activity cue: {cues.activity_visual}; scale: {cues.activity_scale}.",
        f"Overall mood: {cues.overall_mood}.",
    ]
    if cues.post_type != "morning":
        parts.insert(7, f"Moon cue: {cues.moon_visual}.")
    if cues.must_show:
        parts.append("Must show: " + "; ".join(cues.must_show) + ".")
    if cues.must_avoid:
        parts.append("Must avoid: " + "; ".join(cues.must_avoid) + ".")
    parts.append(
        "Text restrictions: no text, no captions, no labels, no logos, no numbers, no UI, "
        "no watermarks, no watermark, no logo, no signature, no letters, no brand marks."
    )
    return "\n".join(parts)


def to_json(obj: Any, *, pretty: bool = True) -> str:
    return json.dumps(dataclasses.asdict(obj), ensure_ascii=False, indent=2 if pretty else None)


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply KLD visual rules to a weather message")
    parser.add_argument("--message-file", default="", help="Path to text file. If omitted, stdin is used.")
    parser.add_argument("--post-type", default="", choices=["", "morning", "evening", "forecast_tomorrow", "unknown"])
    parser.add_argument("--prompt", action="store_true", help="Print prompt-only output after JSON cues")
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()

    if build_visual_context is None:
        raise SystemExit("visual_context_kld.py is required")

    if args.message_file:
        with open(args.message_file, "r", encoding="utf-8") as f:
            message = f.read()
    else:
        message = sys.stdin.read()

    ctx = build_visual_context(message, post_type=args.post_type or None)
    cues = apply_visual_rules(ctx)

    print("\n===== VISUAL_CONTEXT BEGIN =====\n")
    print(json.dumps(_clean_source_context(ctx), ensure_ascii=False, indent=2 if not args.compact else None))
    print("\n===== VISUAL_CONTEXT END =====\n")

    print("\n===== VISUAL_CUES BEGIN =====\n")
    print(to_json(cues, pretty=not args.compact))
    print("\n===== VISUAL_CUES END =====\n")

    if args.prompt:
        print("\n===== VISUAL_PROMPT BEGIN =====\n")
        print(build_prompt_from_cues(cues))
        print("\n===== VISUAL_PROMPT END =====\n")


if __name__ == "__main__":
    main()


__all__ = ["SceneCues", "apply_visual_rules", "build_prompt_from_cues", "to_json"]
