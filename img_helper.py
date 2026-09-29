# img_helper.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import re
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Tuple, Dict, Any

import pendulum


# -----------------------------
# ENV / Config
# -----------------------------

def _env_str(name: str, default: str = "") -> str:
    v = os.getenv(name)
    return default if v is None else str(v).strip()

def _env_int(name: str, default: int) -> int:
    v = _env_str(name, "")
    try:
        return int(v)
    except Exception:
        return default

def _env_bool(name: str, default: bool = False) -> bool:
    v = _env_str(name, "")
    if v == "":
        return default
    return v.lower() in ("1", "true", "yes", "y", "on")

@dataclass(frozen=True)
class ImgEnv:
    enabled: bool = True
    min_bytes: int = 8000
    attempts: int = 2
    morning_style: str = "auto"     # "auto" or "1..N"
    seed_offset: int = 0
    force_regen: bool = False
    provider: str = ""             # optional, do not enforce

    @staticmethod
    def from_env() -> "ImgEnv":
        return ImgEnv(
            enabled=_env_bool("IMG_ENABLED", True),
            min_bytes=_env_int("IMG_MIN_BYTES", 8000),
            attempts=max(1, _env_int("IMG_GEN_ATTEMPTS", 2)),
            morning_style=_env_str("MORNING_STYLE", "auto") or "auto",
            seed_offset=_env_int("MORNING_SEED_OFFSET", 0),
            force_regen=_env_bool("FORCE_REGEN", False),
            provider=_env_str("IMG_PROVIDER", ""),
        )


# -----------------------------
# Date / Theme / Style
# -----------------------------

def resolve_base_date(tz: str) -> pendulum.Date:
    """
    Base date for the pipeline, consistent with your WORK_DATE monkeypatch.
    If WORK_DATE is set, use it (YYYY-MM-DD).
    Else use pendulum.today(tz).
    """
    wd = _env_str("WORK_DATE", "")
    if wd:
        try:
            return pendulum.parse(wd, strict=False).in_timezone(tz).date()
        except Exception:
            logging.warning("IMG: invalid WORK_DATE=%r, fallback to pendulum.today(%s)", wd, tz)
    return pendulum.today(tz)

def resolve_post_date(tz: str, mode: str) -> pendulum.Date:
    """
    morning -> today(local TZ)
    evening -> tomorrow(local TZ)
    """
    base = resolve_base_date(tz)
    if mode.lower() == "evening":
        return base.add(days=1)
    return base

def pick_style_idx(post_date: pendulum.Date, n_styles: int, *, mode: str, env: ImgEnv) -> int:
    """
    Deterministic style:
      style_idx = ((date.toordinal() + SEED_OFFSET) % N) + 1
    For now, MORNING_STYLE controls morning. For evening we keep auto unless you later add EVENING_STYLE.
    """
    mode = mode.lower().strip()
    if mode == "morning":
        ms = (env.morning_style or "auto").lower()
        if ms != "auto":
            try:
                i = int(ms)
                return max(1, min(n_styles, i))
            except Exception:
                pass

    ordinal = post_date.to_date_string()  # keep for debug
    _ = ordinal  # noqa: keep readability

    # pendulum.Date has .toordinal() via underlying date
    idx = ((post_date.to_date().toordinal() + env.seed_offset) % n_styles) + 1
    return idx

def resolve_theme(*, storm: bool, moon_kind: Optional[str]) -> str:
    """
    Priority:
      A) storm
      B) special_moon (new/full)
      C) regular
    """
    if storm:
        return "storm"
    if moon_kind in ("new", "full"):
        return "special_moon"
    return "regular"


# -----------------------------
# Lunar calendar (graceful)
# -----------------------------

def load_lunar_entry(lunar_path: str, date_yyyy_mm_dd: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Tries to load lunar_calendar.json and extract entry for date.
    Returns (entry, moon_kind) where moon_kind is 'new'/'full'/None.
    If file missing or unknown structure -> returns (None, None).
    """
    p = Path(lunar_path)
    if not p.exists():
        return None, None

    import json
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        logging.warning("IMG: lunar_calendar.json read failed: %s", e)
        return None, None

    entry = None
    if isinstance(data, dict):
        # common patterns: dict[date]=entry OR {"days":[...]}
        if date_yyyy_mm_dd in data and isinstance(data[date_yyyy_mm_dd], dict):
            entry = data[date_yyyy_mm_dd]
        elif "days" in data and isinstance(data["days"], list):
            for it in data["days"]:
                if isinstance(it, dict) and str(it.get("date", "")) == date_yyyy_mm_dd:
                    entry = it
                    break
    elif isinstance(data, list):
        for it in data:
            if isinstance(it, dict) and str(it.get("date", "")) == date_yyyy_mm_dd:
                entry = it
                break

    if not isinstance(entry, dict):
        return None, None

    # Detect new/full moon
    txt = " ".join([
        str(entry.get("phase", "")),
        str(entry.get("name", "")),
        str(entry.get("event", "")),
        str(entry.get("moon", "")),
    ]).lower()

    moon_kind = None
    if "new" in txt and "moon" in txt:
        moon_kind = "new"
    if "full" in txt and "moon" in txt:
        moon_kind = "full"

    return entry, moon_kind


# -----------------------------
# File naming / dirs
# -----------------------------

_slug_re = re.compile(r"[^a-zA-Z0-9._-]+")

def slug(s: str) -> str:
    s = (s or "").strip()
    s = s.replace(" ", "_")
    s = _slug_re.sub("_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s[:80] if len(s) > 80 else s

def make_image_name(region: str, mode: str, d: pendulum.Date, theme: str, style_tag: str, ext: str = ".jpg") -> str:
    return f"{slug(region)}_{slug(mode)}_{d.to_date_string()}_{slug(theme)}_{slug(style_tag)}{ext}"

def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


# -----------------------------
# Sanity validation
# -----------------------------

def sniff_image_format(path: Path) -> Optional[str]:
    """
    Returns: 'jpeg' | 'png' | 'webp' | None
    """
    try:
        with path.open("rb") as f:
            head = f.read(16)
    except Exception:
        return None

    if len(head) < 12:
        return None

    # JPEG
    if head.startswith(b"\xFF\xD8\xFF"):
        return "jpeg"
    # PNG
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    # WEBP: RIFF....WEBP
    if head[0:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    return None

def validate_image_file(path: Path, min_bytes: int) -> Tuple[bool, str, int, Optional[str]]:
    """
    ok, reason, size, fmt
    """
    if not path.exists():
        return False, "missing", 0, None
    try:
        size = path.stat().st_size
    except Exception:
        return False, "stat_failed", 0, None

    if size < int(min_bytes):
        return False, f"too_small<{min_bytes}", size, None

    fmt = sniff_image_format(path)
    if fmt not in ("jpeg", "png", "webp"):
        return False, "bad_magic", size, fmt

    return True, "ok", size, fmt


# -----------------------------
# Retry loop (generator wrapper)
# -----------------------------

GeneratorFn = Callable[[str, Path], None]  # (prompt, out_path) -> writes file

def generate_with_retries(
    *,
    region: str,
    mode: str,
    tz: str,
    post_date: pendulum.Date,
    theme: str,
    style_tag: str,
    prompt: str,
    out_dir: Path,
    ext: str,
    env: ImgEnv,
    generator: GeneratorFn,
) -> Optional[Path]:
    """
    Returns valid image file path or None (soft fail).
    """
    ensure_dir(out_dir)
    filename = make_image_name(region, mode, post_date, theme, style_tag, ext=ext)
    out_path = out_dir / filename

    # Reuse if exists and not force_regen
    if out_path.exists() and not env.force_regen:
        ok, reason, size, fmt = validate_image_file(out_path, env.min_bytes)
        logging.info(
            "IMG: reuse region=%s mode=%s tz=%s date=%s theme=%s style=%s prompt_len=%d file=%s ok=%s reason=%s size=%d fmt=%s",
            region, mode, tz, post_date.to_date_string(), theme, style_tag, len(prompt), str(out_path), ok, reason, size, fmt
        )
        if ok:
            return out_path
        # stale/bad cache -> delete and regenerate
        try:
            out_path.unlink(missing_ok=True)
        except Exception:
            pass

    last_reason = "unknown"
    for attempt in range(1, env.attempts + 1):
        try:
            # best effort cleanup
            try:
                out_path.unlink(missing_ok=True)
            except Exception:
                pass

            generator(prompt, out_path)

            ok, reason, size, fmt = validate_image_file(out_path, env.min_bytes)
            logging.info(
                "IMG: gen attempt=%d/%d region=%s mode=%s tz=%s date=%s theme=%s style=%s prompt_len=%d file=%s ok=%s reason=%s size=%d fmt=%s",
                attempt, env.attempts, region, mode, tz, post_date.to_date_string(), theme, style_tag, len(prompt),
                str(out_path), ok, reason, size, fmt
            )
            if ok:
                return out_path

            last_reason = reason
            # delete bad file
            try:
                out_path.unlink(missing_ok=True)
            except Exception:
                pass

        except Exception as e:
            last_reason = f"exception:{type(e).__name__}"
            logging.warning(
                "IMG: gen failed attempt=%d/%d region=%s mode=%s tz=%s date=%s theme=%s style=%s err=%s",
                attempt, env.attempts, region, mode, tz, post_date.to_date_string(), theme, style_tag, e
            )
            try:
                out_path.unlink(missing_ok=True)
            except Exception:
                pass

    logging.warning(
        "IMG: giving up region=%s mode=%s tz=%s date=%s theme=%s style=%s last_reason=%s",
        region, mode, tz, post_date.to_date_string(), theme, style_tag, last_reason
    )
    return None


# -----------------------------
# Mini-test for rotation
# -----------------------------

def demo_rotation(tz: str, n_styles: int = 5, seed_offset: int = 0, start: Optional[str] = None, days: int = 10) -> None:
    env = ImgEnv.from_env()
    env = ImgEnv(
        enabled=env.enabled,
        min_bytes=env.min_bytes,
        attempts=env.attempts,
        morning_style="auto",
        seed_offset=seed_offset,
        force_regen=env.force_regen,
        provider=env.provider,
    )

    d0 = pendulum.parse(start).in_timezone(tz).date() if start else pendulum.today(tz)
    for i in range(days):
        d = d0.add(days=i)
        idx = pick_style_idx(d, n_styles, mode="morning", env=env)
        print(d.to_date_string(), "-> style", idx)

if __name__ == "__main__":
    # Example:
    #   python img_helper.py
    #   python img_helper.py  (with WORK_DATE/MORNING_SEED_OFFSET envs set)
    demo_rotation(tz=_env_str("TZ", "Europe/Nicosia"), n_styles=5, seed_offset=_env_int("MORNING_SEED_OFFSET", 0))
