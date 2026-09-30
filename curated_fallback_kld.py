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

    title_box = (42, 84, 710, 230)
    badge_box = (865, 84, 1034, 240)
    facts_box = (38, 828, 762, 1232)
    title_color = _text_color(image, title_box)
    badge_color = _text_color(image, badge_box)
    facts_color = _text_color(image, facts_box)
    title_font = _font(46, bold=True)
    branding_font = _font(22)
    date_font = _font(28, bold=True)
    fact_font = _font(35, bold=True)

    title = str(metadata.get("title") or "КАЛИНИНГРАД СЕГОДНЯ")
    title_stroke = (255, 255, 255) if title_color[0] < 100 else (0, 0, 0)
    draw.text(
        (70, 103),
        title,
        font=title_font,
        fill=title_color,
        stroke_width=1,
        stroke_fill=title_stroke,
    )
    draw.text((72, 164), BRANDING, font=branding_font, fill=title_color)

    date_label = str(metadata.get("date") or "")
    if date_label:
        short_date = ".".join(date_label.split(".")[:2])
        box = draw.textbbox((0, 0), short_date, font=date_font)
        x = badge_box[0] + (badge_box[2] - badge_box[0] - (box[2] - box[0])) // 2
        y = badge_box[1] + (badge_box[3] - badge_box[1] - (box[3] - box[1])) // 2
        draw.text((x, y), short_date, font=date_font, fill=badge_color)

    y = 867
    layout: list[dict[str, Any]] = []
    for fact in list(metadata.get("facts") or [])[:3]:
        clean = re.sub(r"^[^\wА-ЯЁ+]+\s*", "", str(fact), flags=re.I)
        lines = _wrap(draw, clean, fact_font, 645)
        origins: list[list[int]] = []
        for line in lines[:2]:
            stroke = (255, 255, 255) if facts_color[0] < 100 else (0, 0, 0)
            draw.text(
                (78, y),
                line,
                font=fact_font,
                fill=facts_color,
                stroke_width=1,
                stroke_fill=stroke,
            )
            origins.append([78, y])
            y += 46
        layout.append({"source_fact": str(fact), "lines": lines[:2], "origins": origins})
        y += 22

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
            "panel_bbox": list(facts_box),
            "canvas": [1080, 1350],
            "fact_layout": layout,
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
