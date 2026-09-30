from __future__ import annotations

import base64
import hashlib
import io
import json
import re
from pathlib import Path
from typing import Any, Mapping

from PIL import Image, ImageDraw, ImageFont, ImageStat, PngImagePlugin

CATALOG_VERSION = "kld_curated_fallback_v1"
ROOT = Path(__file__).resolve().parent
ASSET_ROOT = ROOT / "assets" / "fallback" / "kld"
MANIFEST_PATH = ASSET_ROOT / "manifest.json"
OUTPUT_SIZE = (1080, 1350)
BRANDING = "VAYBOMETER · KLD"

def _manifest() -> dict[str, Any]:
    payload = json.loads(MANIFEST_PATH.read_text("utf-8"))
    if payload.get("version") != CATALOG_VERSION:
        raise RuntimeError("invalid KLD curated fallback manifest version")
    if payload.get("canvas") != [1080, 1350]:
        raise RuntimeError("invalid KLD curated fallback canvas")
    return payload

_MANIFEST = _manifest()
_ASSET_ORDER = tuple(str(value) for value in _MANIFEST["asset_order"])
_SCENARIO_POOLS = {
    str(key): tuple(str(value) for value in values)
    for key, values in _MANIFEST["scenario_pools"].items()
}
_CELL_SIZE = tuple(int(value) for value in _MANIFEST["cell_size"])


def _box(values: object, *, label: str) -> tuple[int, int, int, int]:
    if not isinstance(values, list) or len(values) != 4:
        raise RuntimeError(f"invalid KLD curated {label} box")
    box = tuple(int(value) for value in values)
    left, top, right, bottom = box
    if not (0 <= left < right <= OUTPUT_SIZE[0] and 0 <= top < bottom <= OUTPUT_SIZE[1]):
        raise RuntimeError(f"invalid KLD curated {label} geometry: {box}")
    return box


_OVERLAY_GEOMETRY: dict[str, dict[str, tuple[int, int, int, int]]] = {}
_raw_geometry = _MANIFEST.get("overlay_geometry")
if not isinstance(_raw_geometry, Mapping) or set(_raw_geometry) != set(_ASSET_ORDER):
    raise RuntimeError("invalid KLD curated per-asset overlay geometry")
for _asset_id in _ASSET_ORDER:
    _entry = _raw_geometry.get(_asset_id)
    if not isinstance(_entry, Mapping):
        raise RuntimeError(f"invalid KLD curated overlay geometry for {_asset_id}")
    _geometry = {
        key: _box(_entry.get(key), label=f"{_asset_id} {key}")
        for key in ("title_panel", "title_safe", "facts_panel", "facts_safe")
    }
    for _panel_key, _safe_key in (("title_panel", "title_safe"), ("facts_panel", "facts_safe")):
        _panel = _geometry[_panel_key]
        _safe = _geometry[_safe_key]
        if not (
            _panel[0] <= _safe[0] < _safe[2] <= _panel[2]
            and _panel[1] <= _safe[1] < _safe[3] <= _panel[3]
        ):
            raise RuntimeError(f"KLD curated {_safe_key} escapes {_panel_key} for {_asset_id}")
    _OVERLAY_GEOMETRY[_asset_id] = _geometry

def _load_atlas() -> Image.Image:
    encoded = "".join(
        (ASSET_ROOT / str(name)).read_text("ascii")
        for name in _MANIFEST["atlas_parts"]
    )
    raw = base64.b64decode(encoded, validate=True)
    with Image.open(io.BytesIO(raw)) as atlas:
        expected = tuple(int(value) for value in _MANIFEST["atlas_size"])
        if atlas.size != expected:
            raise RuntimeError(f"invalid KLD curated atlas size: {atlas.size}; expected {expected}")
        return atlas.convert("RGB")

def _asset_box(asset_id: str) -> tuple[int, int, int, int]:
    index = _ASSET_ORDER.index(asset_id)
    columns = int(_MANIFEST["columns"])
    col, row = index % columns, index // columns
    width, height = _CELL_SIZE
    return col * width, row * height, (col + 1) * width, (row + 1) * height

def _month(metadata: Mapping[str, Any]) -> int | None:
    match = re.search(r"\b(\d{2})\.(\d{2})\.(\d{4})\b", str(metadata.get("date") or ""))
    return int(match.group(2)) if match else None

def scenario_for_metadata(
    metadata: Mapping[str, Any],
    *,
    post_type: str,
    source_text: str = "",
) -> str:
    weather = metadata.get("weather") if isinstance(metadata.get("weather"), Mapping) else {}
    values = metadata.get("actual_values") if isinstance(metadata.get("actual_values"), Mapping) else {}
    display = str(weather.get("precipitation_display") or "none")
    temp_max = values.get("temp_max_c")
    temp_min = values.get("temp_min_c")
    near_freezing = any(
        isinstance(value, (int, float)) and -2.5 <= float(value) <= 3.5
        for value in (temp_max, temp_min)
    )
    month = _month(metadata)
    mode = str(post_type or "").strip().lower()

    if weather.get("thunderstorm") or weather.get("explicit_storm") or weather.get("storm_badge"):
        return "strong_wind"
    if display in {"mixed_snow_rain", "snow_and_drizzle"} and near_freezing:
        return "slush"
    if display in {"snow", "snow_and_drizzle", "mixed_snow_rain"}:
        if weather.get("strong_wind") or weather.get("severe_weather"):
            return "heavy_snow"
        return "snow"
    if weather.get("fog"):
        return "fog"
    if display in {"rain", "rain_and_drizzle", "drizzle", "precipitation"}:
        return "rain_evening" if mode == "evening" else "rain_day"
    if weather.get("mixed_visibility") or weather.get("reduced_visibility"):
        return "fog"
    if weather.get("strong_wind") or weather.get("severe_weather"):
        return "windy_autumn" if month in {9, 10, 11} else "strong_wind"

    below_zero = any(
        isinstance(value, (int, float)) and float(value) <= 0.0
        for value in (temp_max, temp_min)
    )
    cloudy = bool(re.search(r"(?:облач|пасм|☁|🌥)", str(source_text), re.I))
    period = str(metadata.get("visual_period") or "").strip().lower()
    if period == "night":
        return "night"
    if below_zero and month in {12, 1, 2, 3}:
        if mode == "evening" and re.search(r"(?:закат|сумерк)", str(source_text), re.I):
            return "frost_evening"
        return "frost"
    if cloudy and month in {12, 1, 2, 3}:
        return "winter_overcast"
    if cloudy:
        return "overcast"
    if mode == "evening" and re.search(r"(?:закат|сумерк)", str(source_text), re.I):
        return "sunset"
    return "clear"

def select_asset(
    metadata: Mapping[str, Any],
    *,
    post_type: str,
    source_text: str = "",
) -> tuple[str, str, tuple[str, ...]]:
    scenario = scenario_for_metadata(metadata, post_type=post_type, source_text=source_text)
    pool = _SCENARIO_POOLS[scenario]
    missing = [asset_id for asset_id in pool if asset_id not in _ASSET_ORDER]
    if missing:
        raise RuntimeError(f"KLD curated manifest missing assets: {missing}")
    digest = hashlib.sha256(
        f"{metadata.get('date', '')}|{post_type}|{scenario}|{CATALOG_VERSION}".encode("utf-8")
    ).digest()
    asset_id = pool[int.from_bytes(digest[:4], "big") % len(pool)]
    return scenario, asset_id, pool

def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    candidates = (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
    )
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            continue
    return ImageFont.truetype("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf", size=size)

def _text_color(image: Image.Image, box: tuple[int, int, int, int]) -> tuple[int, int, int]:
    red, green, blue = ImageStat.Stat(image.crop(box).resize((1, 1))).mean[:3]
    luminance = 0.2126 * red + 0.7152 * green + 0.0722 * blue
    return (10, 27, 42) if luminance >= 145 else (250, 252, 250)

def _wrap(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        box = draw.textbbox((0, 0), candidate, font=font)
        if box[2] - box[0] <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _fit_single_line(
    draw: ImageDraw.ImageDraw,
    text: str,
    safe_box: tuple[int, int, int, int],
    *,
    maximum_size: int,
    minimum_size: int,
) -> tuple[ImageFont.FreeTypeFont, int, tuple[int, int], tuple[int, int, int, int]]:
    max_width = safe_box[2] - safe_box[0]
    max_height = safe_box[3] - safe_box[1]
    for size in range(maximum_size, minimum_size - 1, -1):
        font = _font(size, bold=True)
        measured = draw.textbbox((0, 0), text, font=font, stroke_width=1)
        width = measured[2] - measured[0]
        height = measured[3] - measured[1]
        if width > max_width or height > max_height:
            continue
        origin = (
            safe_box[0] - measured[0],
            safe_box[1] + (max_height - height) // 2 - measured[1],
        )
        bbox = draw.textbbox(origin, text, font=font, stroke_width=1)
        return font, size, origin, bbox
    raise RuntimeError(f"KLD curated headline does not fit safe-zone: {text!r}")


def _fit_fact_layout(
    draw: ImageDraw.ImageDraw,
    facts: list[str],
    safe_box: tuple[int, int, int, int],
) -> tuple[ImageFont.FreeTypeFont, int, list[list[str]], int, int, int]:
    max_width = safe_box[2] - safe_box[0]
    max_height = safe_box[3] - safe_box[1]
    fallback: tuple[ImageFont.FreeTypeFont, int, list[list[str]], int, int, int] | None = None
    for size in range(35, 21, -1):
        font = _font(size, bold=True)
        wrapped = [_wrap(draw, fact, font, max_width) for fact in facts]
        rendered = [lines[:2] for lines in wrapped]
        line_gap = max(5, round(size * 0.18))
        fact_gap = max(12, round(size * 0.48))
        heights: list[int] = []
        widths: list[int] = []
        fact_widths: list[list[int]] = []
        line_count = 0
        for lines in rendered:
            current_widths: list[int] = []
            for line in lines:
                measured = draw.textbbox((0, 0), line, font=font, stroke_width=1)
                line_width = measured[2] - measured[0]
                widths.append(line_width)
                current_widths.append(line_width)
                heights.append(measured[3] - measured[1])
                line_count += 1
            fact_widths.append(current_widths)
        total_height = sum(heights)
        total_height += line_gap * max(0, line_count - len(rendered))
        total_height += fact_gap * max(0, len(rendered) - 1)
        candidate = (font, size, rendered, line_gap, fact_gap, total_height)
        fallback = candidate
        if any(len(lines) > 2 for lines in wrapped):
            continue
        if any(
            len(line_widths) == 2 and line_widths[-1] < max_width * 0.28
            for line_widths in fact_widths
        ):
            continue
        if (not widths or max(widths) <= max_width) and total_height <= max_height:
            return candidate
    if fallback is not None and fallback[5] <= max_height:
        return fallback
    raise RuntimeError("KLD curated facts do not fit selected asset safe-zone")

def render_curated_cover(
    metadata: Mapping[str, Any],
    *,
    post_type: str,
    source_text: str,
    output_path: str | Path,
) -> dict[str, Any]:
    scenario, asset_id, pool = select_asset(
        metadata,
        post_type=post_type,
        source_text=source_text,
    )
    atlas = _load_atlas()
    image = atlas.crop(_asset_box(asset_id)).resize(OUTPUT_SIZE, Image.Resampling.LANCZOS)
    draw = ImageDraw.Draw(image)

    geometry = _OVERLAY_GEOMETRY[asset_id]
    title_panel = geometry["title_panel"]
    title_safe = geometry["title_safe"]
    badge_box = (865, 84, 1034, 240)
    facts_panel = geometry["facts_panel"]
    facts_safe = geometry["facts_safe"]
    title_color = _text_color(image, title_panel)
    badge_color = _text_color(image, badge_box)
    facts_color = _text_color(image, facts_panel)
    branding_font = _font(22)
    date_font = _font(28, bold=True)

    title = str(metadata.get("title") or "КАЛИНИНГРАД СЕГОДНЯ")
    title_stroke = (255, 255, 255) if title_color[0] < 100 else (0, 0, 0)
    title_font, title_font_size, title_origin, title_bbox = _fit_single_line(
        draw,
        title,
        title_safe,
        maximum_size=46,
        minimum_size=24,
    )
    draw.text(
        title_origin,
        title,
        font=title_font,
        fill=title_color,
        stroke_width=1,
        stroke_fill=title_stroke,
    )
    branding_origin = (title_safe[0] + 2, title_safe[3] + 5)
    branding_bbox = draw.textbbox(branding_origin, BRANDING, font=branding_font)
    if branding_bbox[3] > title_panel[3]:
        raise RuntimeError(f"KLD curated branding escapes title panel for {asset_id}")
    draw.text(branding_origin, BRANDING, font=branding_font, fill=title_color)

    date_label = str(metadata.get("date") or "")
    date_bbox: tuple[int, int, int, int] | None = None
    if date_label:
        short_date = ".".join(date_label.split(".")[:2])
        box = draw.textbbox((0, 0), short_date, font=date_font)
        x = badge_box[0] + (badge_box[2] - badge_box[0] - (box[2] - box[0])) // 2
        y = badge_box[1] + (badge_box[3] - badge_box[1] - (box[3] - box[1])) // 2
        draw.text((x, y), short_date, font=date_font, fill=badge_color)
        date_bbox = draw.textbbox((x, y), short_date, font=date_font)

    clean_facts = [
        re.sub(r"^[^\wА-ЯЁ+]+\s*", "", str(fact), flags=re.I)
        for fact in list(metadata.get("facts") or [])[:3]
    ]
    fact_font, fact_font_size, wrapped_facts, line_gap, fact_gap, total_height = _fit_fact_layout(
        draw,
        clean_facts,
        facts_safe,
    )
    y = facts_safe[1] + max(0, (facts_safe[3] - facts_safe[1] - total_height) // 2)
    layout: list[dict[str, Any]] = []
    facts = list(metadata.get("facts") or [])[:3]
    for fact_index, (fact, lines) in enumerate(zip(facts, wrapped_facts)):
        origins: list[list[int]] = []
        bboxes: list[list[int]] = []
        for line_index, line in enumerate(lines):
            stroke = (255, 255, 255) if facts_color[0] < 100 else (0, 0, 0)
            measured = draw.textbbox((0, 0), line, font=fact_font, stroke_width=1)
            origin = (facts_safe[0] - measured[0], y - measured[1])
            draw.text(
                origin,
                line,
                font=fact_font,
                fill=facts_color,
                stroke_width=1,
                stroke_fill=stroke,
            )
            bbox = draw.textbbox(origin, line, font=fact_font, stroke_width=1)
            origins.append([origin[0], origin[1]])
            bboxes.append(list(bbox))
            y = bbox[3]
            if line_index + 1 < len(lines):
                y += line_gap
        layout.append(
            {
                "source_fact": str(fact),
                "lines": lines,
                "origins": origins,
                "bboxes": bboxes,
            }
        )
        if fact_index + 1 < len(wrapped_facts):
            y += fact_gap

    weather = metadata.get("weather") if isinstance(metadata.get("weather"), Mapping) else {}
    result = dict(metadata)
    result.update(
        {
            "renderer_version": CATALOG_VERSION,
            "catalog_version": CATALOG_VERSION,
            "curated_scenario": scenario,
            "curated_asset_id": asset_id,
            "curated_pool": list(pool),
            "cover_variant": asset_id,
            "panel_bbox": list(facts_panel),
            "title_panel_bbox": list(title_panel),
            "title_safe_bbox": list(title_safe),
            "facts_safe_bbox": list(facts_safe),
            "title_layout": {
                "text": title,
                "origin": list(title_origin),
                "bbox": list(title_bbox),
                "font_size": title_font_size,
            },
            "branding_layout": {
                "text": BRANDING,
                "origin": list(branding_origin),
                "bbox": list(branding_bbox),
            },
            "date_layout": {
                "text": ".".join(date_label.split(".")[:2]) if date_label else "",
                "bbox": list(date_bbox) if date_bbox is not None else None,
            },
            "canvas": [1080, 1350],
            "fact_layout": layout,
            "fact_font_size": fact_font_size,
            "precipitation_display": str(weather.get("precipitation_display") or "none"),
            "rain_graphics": bool(weather.get("rain")),
            "drizzle_graphics": bool(weather.get("drizzle") and not weather.get("rain")),
            "snow_graphics": bool(weather.get("snow")),
            "lightning_graphics": bool(weather.get("thunderstorm")),
        }
    )
    cache_payload = {
        "catalog_version": CATALOG_VERSION,
        "asset_id": asset_id,
        "scenario": scenario,
        "date": metadata.get("date"),
        "post_type": post_type,
        "facts": list(metadata.get("facts") or []),
    }
    cache_json = json.dumps(cache_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    result["cache_key"] = f"{CATALOG_VERSION}:{hashlib.sha256(cache_json.encode('utf-8')).hexdigest()}"

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".tmp")
    info = PngImagePlugin.PngInfo()
    info.add_text("renderer_version", CATALOG_VERSION)
    info.add_text("catalog_version", CATALOG_VERSION)
    info.add_text("curated_scenario", scenario)
    info.add_text("curated_asset_id", asset_id)
    info.add_text("curated_pool", json.dumps(list(pool), ensure_ascii=False))
    info.add_text(
        "overlay_safe_zones",
        json.dumps({"title": list(title_safe), "facts": list(facts_safe)}, separators=(",", ":")),
    )
    info.add_text("weather_flags", json.dumps(dict(weather), ensure_ascii=False, sort_keys=True))
    info.add_text("precipitation_display", result["precipitation_display"])
    info.add_text("rain_graphics", str(result["rain_graphics"]).lower())
    info.add_text("drizzle_graphics", str(result["drizzle_graphics"]).lower())
    info.add_text("snow_graphics", str(result["snow_graphics"]).lower())
    info.add_text("lightning_graphics", str(result["lightning_graphics"]).lower())
    info.add_text("canvas", "1080x1350")
    image.save(temporary, format="PNG", compress_level=6, pnginfo=info)
    temporary.replace(output)
    result["path"] = str(output)
    result["width"] = 1080
    result["height"] = 1350
    result["bytes"] = output.stat().st_size
    return result

__all__ = [
    "CATALOG_VERSION",
    "scenario_for_metadata",
    "select_asset",
    "render_curated_cover",
]
