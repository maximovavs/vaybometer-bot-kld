#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared visual policy for Kaliningrad image prompts and history gates.

The policy is intentionally deterministic and side-effect free. It keeps
seasonal appearance, weather-aware scene routing and visual diversity rules in
one place so morning and evening delivery use the same contract.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Mapping, MutableMapping, Sequence

KLD_VISUAL_POLICY_VERSION = "kld_visual_policy_v3"
SUMMER_MONTHS = frozenset({6, 7, 8})

SUMMER_VEGETATION_CUE = (
    "Summer vegetation adherence: humid Baltic summer vegetation is lush and fresh; "
    "coastal grass and dune vegetation are visibly natural green across foreground and midground; "
    "shrubs, reeds and pines are healthy green; pale beige and warm straw tones belong to sand, "
    "wood or dry non-living surfaces, not to living summer vegetation."
)

SCENE_NEUTRAL_PHOTO_CONTRACT = (
    "Scene identity adherence: the selected scene family is authoritative; render one coherent real "
    "Kaliningrad-region scene chosen by that family; preserve it as the dominant geography; include only "
    "landforms and objects naturally belonging to that scene; use realistic northern vegetation, "
    "weather and atmospheric perspective; ground-level or natural elevated photographic viewpoint."
)

PROVIDER_NEGATIVE_GUARD = (
    "Provider safety: no map, no satellite imagery, no cartographic view, no navigation screen, "
    "no screenshot, no browser interface, no app interface, no UI chrome, no dashboard, no poster, "
    "no infographic, no text overlay, no watermark."
)

_OPEN_BEACH_MACRO = frozenset(
    {
        "curonian_spit_dunes",
        "yantarny_wide_beach",
        "stormy_open_baltic",
    }
)

_LEGACY_GENERIC_GEOGRAPHY = (
    "dunes, pines, promenade, or baltic sea horizon",
    "dunes, pines, promenade, sea horizon",
)

_SCENE_TEXT = {
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
    "kaliningrad_urban_coastal_view": "urban Baltic coastal edge with modest Kaliningrad-region architecture, promenade and sea horizon",
    "rainy_coastal_road": "rain-darkened coastal road near dunes and pines, with the Baltic shoreline visible beyond",
}

WEATHER_SCENE_ROUTES: dict[str, tuple[str, ...]] = {
    "fog_visibility": (
        "quiet_lagoon_coast",
        "pine_forest_sea_path",
        "curonian_spit_dunes",
        "elevated_baltic_overlook",
        "zelenogradsk_promenade",
    ),
    "rain": (
        "wet_seaside_promenade",
        "rainy_coastal_road",
        "zelenogradsk_promenade",
        "kaliningrad_urban_coastal_view",
        "baltiysk_breakwater",
        "pine_forest_sea_path",
    ),
    "storm": (
        "baltiysk_breakwater",
        "svetlogorsk_cliff_coast",
        "stormy_open_baltic",
        "wet_seaside_promenade",
        "rainy_coastal_road",
        "elevated_baltic_overlook",
    ),
    "strong_wind": (
        "baltiysk_breakwater",
        "svetlogorsk_cliff_coast",
        "elevated_baltic_overlook",
        "zelenogradsk_promenade",
        "yantarny_wide_beach",
    ),
    "calm": (
        "kaliningrad_urban_coastal_view",
        "pine_forest_sea_path",
        "quiet_lagoon_coast",
        "zelenogradsk_promenade",
        "curonian_spit_dunes",
        "yantarny_wide_beach",
    ),
}

_FOG_VISIBILITY = frozenset(
    {
        "dense_fog",
        "fog",
        "mist",
        "reduced_visibility",
        "dust_haze",
        "mixed_visibility",
    }
)


def parse_date_key(value: str | dt.date | None) -> dt.date | None:
    if isinstance(value, dt.date):
        return value
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return dt.date.fromisoformat(raw[:10])
    except ValueError:
        return None


def seasonal_guard_label(value: str | dt.date | None) -> str:
    target = parse_date_key(value)
    if target and target.month in SUMMER_MONTHS:
        return "summer_green"
    return "seasonal_default"


def apply_summer_vegetation_guard(prompt: str, date_key: str | dt.date | None) -> str:
    """Append the Baltic green-summer invariant only for June-August."""
    target = parse_date_key(date_key)
    text = str(prompt or "").rstrip()
    if target is None or target.month not in SUMMER_MONTHS:
        return text
    if SUMMER_VEGETATION_CUE in text:
        return text
    return text + "\n" + SUMMER_VEGETATION_CUE


def scene_macro_family(scene_family: str) -> str:
    scene = str(scene_family or "").strip()
    if scene in _OPEN_BEACH_MACRO:
        return "open_beach_dunes"
    if scene in {"zelenogradsk_promenade", "wet_seaside_promenade", "kaliningrad_urban_coastal_view"}:
        return "promenade_urban"
    if scene in {"pine_forest_sea_path", "rainy_coastal_road"}:
        return "forest_road"
    if scene in {"svetlogorsk_cliff_coast", "elevated_baltic_overlook"}:
        return "cliff_overlook"
    if scene == "baltiysk_breakwater":
        return "breakwater_harbour"
    if scene == "quiet_lagoon_coast":
        return "lagoon"
    if scene == "local_informative_cover":
        return "local_cover"
    return scene or "unknown"


def weather_route_key(metadata: Mapping[str, Any]) -> str:
    visibility = str(metadata.get("visibility_condition") or "clear").strip().lower()
    weather = str(metadata.get("weather_scenario") or "unknown").strip().lower()
    gust = str(metadata.get("wind_gust_category") or "wind_unknown").strip().lower()
    if visibility in _FOG_VISIBILITY or weather == "fog":
        return "fog_visibility"
    if weather == "storm":
        return "storm"
    if weather in {"rain", "drizzle"}:
        return "rain"
    if gust in {"gust_10_14", "gust_15_plus"}:
        return "strong_wind"
    return "calm"


def weather_route_scenes(metadata: Mapping[str, Any]) -> tuple[str, ...]:
    return WEATHER_SCENE_ROUTES[weather_route_key(metadata)]


def apply_weather_scene_route(metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Bias the final provider scene toward weather-relevant KLD geography.

    Existing date-based selection is preserved when it is already compatible
    with the active weather route. Otherwise a deterministic route scene is
    chosen from variation_attempt. Mutable metadata is updated in-place so the
    cache key, final dedup gate and history all describe the scene actually sent
    to the provider.
    """
    routed = dict(metadata)
    route_key = weather_route_key(routed)
    route = WEATHER_SCENE_ROUTES[route_key]
    current = str(routed.get("scene_family") or "")
    if current not in route:
        try:
            attempt = int(routed.get("variation_attempt") or 0)
        except (TypeError, ValueError):
            attempt = 0
        stable_offset = sum(ord(char) for char in current) % len(route) if current else 0
        current = route[(attempt + stable_offset) % len(route)]
        routed["scene_family"] = current
        routed["scene_text"] = _SCENE_TEXT[current]
    routed["scene_route"] = route_key
    if isinstance(metadata, MutableMapping):
        metadata.update(routed)
    return routed


def recent_real_scene_entries(history: Sequence[Mapping[str, Any]], *, limit: int = 5) -> list[Mapping[str, Any]]:
    out: list[Mapping[str, Any]] = []
    for entry in reversed(list(history)):
        scene = str(entry.get("scene_family") or "")
        if not scene or scene == "local_informative_cover":
            continue
        out.append(entry)
        if len(out) >= max(1, int(limit)):
            break
    return out


def scene_policy_rejection(
    history: Sequence[Mapping[str, Any]],
    *,
    scene_family: str,
    composition: str = "",
    scene_cooldown: int = 3,
    composition_cooldown: int = 4,
    macro_window: int = 5,
    max_open_beach: int = 2,
) -> tuple[str, Mapping[str, Any] | None]:
    """Return a hard diversity rejection reason or ("", None).

    Both exact scene-family and composition cooldowns are final invariants. The
    broader open-beach/dunes macro remains capped at 2 of the last 5 real visual
    posts.
    """
    recent = recent_real_scene_entries(
        history,
        limit=max(scene_cooldown, composition_cooldown, macro_window),
    )
    for entry in recent[: max(0, int(scene_cooldown))]:
        if str(entry.get("scene_family") or "") == str(scene_family or ""):
            return "scene_cooldown", entry

    if composition:
        for entry in recent[: max(0, int(composition_cooldown))]:
            if str(entry.get("composition") or "") == str(composition):
                return "composition_cooldown", entry

    macro = scene_macro_family(scene_family)
    if macro == "open_beach_dunes":
        macro_recent = recent[:macro_window]
        matches = [
            entry
            for entry in macro_recent
            if scene_macro_family(str(entry.get("scene_family") or "")) == macro
        ]
        if len(matches) >= max_open_beach:
            return "scene_macro_cooldown", matches[0]
    return "", None


def build_scene_contract(metadata: Mapping[str, Any]) -> str:
    """Build a scene-authoritative line without forcing beach+dunes+pines into every image."""
    scene_family = str(metadata.get("scene_family") or "unknown")
    scene_text = str(metadata.get("scene_text") or "real Kaliningrad-region scene")
    composition = str(metadata.get("composition") or "natural editorial composition")
    return (
        "Controlled scene: "
        f"dominant scene family {scene_family}; {scene_text}; composition: {composition}. "
        + SCENE_NEUTRAL_PHOTO_CONTRACT
    )


def _clean_must_show(line: str) -> str:
    prefix, raw = line.split(":", 1)
    items = [item.strip().rstrip(".") for item in raw.split(";") if item.strip()]
    kept = [
        item
        for item in items
        if not any(token in item.lower() for token in _LEGACY_GENERIC_GEOGRAPHY)
    ]
    return f"{prefix}: " + "; ".join(kept) + ("." if kept else "")


def finalize_kld_provider_prompt(
    prompt: str,
    *,
    metadata: Mapping[str, Any],
    date_key: str | dt.date | None = None,
) -> str:
    """Apply the final shared provider contract to morning/evening prompts.

    The upstream VisualRules layer still carries a legacy generic coast base
    scene. This finalizer removes that geography lock and any older controlled
    composition line, applies weather-aware scene routing, then inserts exactly
    one scene-authoritative contract. Provider/UI safety and summer vegetation
    are applied at the same final point for both morning and evening delivery.
    Seasonal appearance follows the visual target date.
    """
    routed_metadata = apply_weather_scene_route(metadata)
    target = parse_date_key(
        routed_metadata.get("target_date") or date_key or routed_metadata.get("forecast_date")
    )
    summer = bool(target and target.month in SUMMER_MONTHS)
    out: list[str] = []
    for line in str(prompt or "").splitlines():
        stripped = line.strip()
        if stripped.startswith(
            (
                "Base scene:",
                "Regional setting:",
                "Controlled composition:",
                "Controlled scene:",
                "Provider safety:",
                "Summer vegetation adherence:",
                "Weather scene route:",
            )
        ):
            continue
        if stripped.startswith("Must show:"):
            cleaned = _clean_must_show(line)
            if cleaned.split(":", 1)[1].strip(" ."):
                out.append(cleaned)
            continue
        if summer and stripped.startswith("Palette:"):
            out.append(
                "Palette: humid northern Baltic grey-blue, fresh natural summer greens, "
                "restrained pale sand and stone tones."
            )
            continue
        out.append(line)

    policy_lines = [
        "Regional setting: one real Kaliningrad-region location; selected scene family controls the geography.",
        f"Weather scene route: {routed_metadata.get('scene_route', 'calm')}.",
        build_scene_contract(routed_metadata),
        PROVIDER_NEGATIVE_GUARD,
    ]
    if summer:
        policy_lines.append(SUMMER_VEGETATION_CUE)

    insert_at = next(
        (index for index, line in enumerate(out) if line.startswith("Text restrictions:")),
        len(out),
    )
    out[insert_at:insert_at] = policy_lines
    return "\n".join(out)


__all__ = [
    "KLD_VISUAL_POLICY_VERSION",
    "PROVIDER_NEGATIVE_GUARD",
    "SCENE_NEUTRAL_PHOTO_CONTRACT",
    "SUMMER_MONTHS",
    "SUMMER_VEGETATION_CUE",
    "WEATHER_SCENE_ROUTES",
    "apply_summer_vegetation_guard",
    "apply_weather_scene_route",
    "build_scene_contract",
    "finalize_kld_provider_prompt",
    "parse_date_key",
    "recent_real_scene_entries",
    "scene_macro_family",
    "scene_policy_rejection",
    "seasonal_guard_label",
    "weather_route_key",
    "weather_route_scenes",
]
