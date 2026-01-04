#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
imagegen.py — VayboMeter image generator (Pollinations → local file).

Role:
- Build Pollinations URL (no API key).
- Download image and save locally (JPEG/PNG/WebP).
- Return local file path for Telegram upload.

Compatibility:
  generate_astro_image(prompt, out_path)
  generate_astro_image(prompt, out_path, style_name="...")
  generate_kld_evening_image(prompt, style_name)
  generate_kld_evening_image(prompt, style_name, out_path)
  generate_kld_evening_image(prompt=..., style_name=..., out_path=...)

Extra (new, optional):
  - seed=... (int) to force variability
  - width=..., height=...
  - enhance=..., nologo=...
"""

from __future__ import annotations

import hashlib
import io
import logging
import os
import time
from pathlib import Path
from typing import Optional, Tuple, Any
from urllib.parse import quote
import urllib.request

log = logging.getLogger(__name__)

BASE_URL = "https://image.pollinations.ai/prompt"

DEFAULT_W = int(os.getenv("IMG_W", "1024"))
DEFAULT_H = int(os.getenv("IMG_H", "1024"))

HTTP_TIMEOUT = float(os.getenv("IMG_HTTP_TIMEOUT", "25"))
HTTP_RETRIES = int(os.getenv("IMG_HTTP_RETRIES", "3"))
HTTP_BACKOFF = float(os.getenv("IMG_HTTP_BACKOFF", "1.6"))

DEFAULT_DIR = Path(os.getenv("IMG_OUT_DIR", ".cache/images"))


def _sha_seed(text: str) -> int:
    """Deterministic seed from prompt+style."""
    h = hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
    return int(h, 16)


def build_pollinations_url(
    prompt: str,
    style_name: Optional[str] = None,
    *,
    width: int = DEFAULT_W,
    height: int = DEFAULT_H,
    seed: Optional[int] = None,
    enhance: bool = True,
    nologo: bool = True,
) -> str:
    """
    Build Pollinations URL.

    Notes:
    - prompt goes into PATH segment => escape everything via quote(..., safe="").
    - seed defaults to deterministic sha256(prompt+style) if not provided.
    """
    p = (prompt or "").strip()
    if not p:
        raise ValueError("imagegen: empty prompt")

    style_part = f" style:{style_name}" if style_name else ""
    final_prompt = (p + style_part).strip()

    if seed is None:
        seed = _sha_seed(final_prompt)

    encoded = quote(final_prompt, safe="")

    url = (
        f"{BASE_URL}/{encoded}"
        f"?width={int(width)}&height={int(height)}&seed={int(seed)}"
        f"&nologo={'true' if nologo else 'false'}"
        f"&enhance={'true' if enhance else 'false'}"
    )
    return url


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _looks_like_path(s: str) -> bool:
    if not s:
        return False
    sl = str(s).strip().lower()
    if any(sep in sl for sep in ("/", "\\", ":")):
        return True
    return sl.endswith((".jpg", ".jpeg", ".png", ".webp"))


def _sniff_ext(data: bytes, content_type: Optional[str]) -> Optional[str]:
    ct = (content_type or "").lower().split(";")[0].strip()
    if ct == "image/jpeg":
        return ".jpg"
    if ct == "image/png":
        return ".png"
    if ct == "image/webp":
        return ".webp"

    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data.startswith(b"RIFF") and b"WEBP" in data[:16]:
        return ".webp"
    return None


def _to_jpeg_bytes(data: bytes) -> Tuple[bytes, str]:
    """
    Convert supported formats to JPEG bytes (Telegram send_photo friendly).
    Returns (bytes, mode) where mode is "jpeg" if converted, else "original".
    """
    try:
        from PIL import Image  # type: ignore
    except Exception:
        return data, "original"

    try:
        im = Image.open(io.BytesIO(data))
        im.load()

        if im.mode in ("RGBA", "LA") or ("transparency" in getattr(im, "info", {})):
            bg = Image.new("RGB", im.size, (255, 255, 255))
            rgba = im.convert("RGBA")
            bg.paste(rgba, mask=rgba.split()[-1])
            im = bg
        else:
            im = im.convert("RGB")

        out = io.BytesIO()
        im.save(out, format="JPEG", quality=92, optimize=True)
        return out.getvalue(), "jpeg"
    except Exception:
        return data, "original"


def _http_get(url: str, *, timeout: float) -> Tuple[bytes, Optional[str]]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "VayboMeter/1.0 python-urllib",
            "Accept": "image/avif,image/webp,image/png,image/jpeg,*/*;q=0.8",
        },
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
        ctype = resp.headers.get("Content-Type")
        return data, ctype


def download_image_to_file(
    url: str,
    out_path: str | Path,
    *,
    timeout: float = HTTP_TIMEOUT,
    retries: int = HTTP_RETRIES,
) -> str:
    """
    Download image by URL and save to disk. Returns the final saved filepath.

    - Retries with exponential backoff.
    - Attempts JPEG normalization (Pillow) so Telegram reliably accepts the file.
    """
    out = Path(out_path)
    if out.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp"):
        out = out.with_suffix(".jpg")

    _ensure_parent(out)

    last_err: Optional[Exception] = None
    retries = max(1, int(retries))

    for attempt in range(1, retries + 1):
        try:
            data, ctype = _http_get(url, timeout=timeout)

            if not data or len(data) < 128:
                raise ValueError(f"imagegen: response too small ({len(data)} bytes)")

            jpg_bytes, mode = _to_jpeg_bytes(data)

            if mode == "jpeg":
                out_final = out.with_suffix(".jpg")
                payload = jpg_bytes
            else:
                ext = _sniff_ext(data, ctype) or out.suffix.lower() or ".jpg"
                out_final = out.with_suffix(ext)
                payload = data

            _ensure_parent(out_final)
            out_final.write_bytes(payload)

            log.info("imagegen: saved image -> %s (attempt=%d, mode=%s)", out_final, attempt, mode)
            return str(out_final)

        except Exception as e:
            last_err = e
            if attempt < retries:
                sleep_s = (HTTP_BACKOFF ** (attempt - 1))
                log.warning(
                    "imagegen: download failed (attempt %d/%d): %s; retry in %.1fs",
                    attempt,
                    retries,
                    e,
                    sleep_s,
                )
                time.sleep(sleep_s)

    raise RuntimeError(f"imagegen: failed to download image after {retries} tries: {last_err}")


def _extract_args(args: tuple[Any, ...], kwargs: dict[str, Any]) -> tuple[str, Optional[str], Optional[str], dict[str, Any]]:
    """
    Universal argument parsing.
    Returns: (prompt, style_name, out_path, extra_kwargs)
    """
    prompt = kwargs.get("prompt")
    style_name = kwargs.get("style_name") or kwargs.get("style") or kwargs.get("theme")
    out_path = kwargs.get("out_path") or kwargs.get("path") or kwargs.get("outfile") or kwargs.get("output")

    if prompt is None and len(args) >= 1:
        prompt = args[0]

    if len(args) >= 2:
        a1 = args[1]
        if out_path is None and isinstance(a1, str) and _looks_like_path(a1):
            out_path = a1
        elif style_name is None:
            style_name = str(a1)

    if len(args) >= 3:
        a2 = args[2]
        if out_path is None and isinstance(a2, str) and _looks_like_path(a2):
            out_path = a2
        elif style_name is None:
            style_name = str(a2)

    if not prompt or not str(prompt).strip():
        raise ValueError("imagegen: no prompt passed")

    # supported overrides
    extra = {
        "seed": kwargs.get("seed"),
        "width": kwargs.get("width"),
        "height": kwargs.get("height"),
        "enhance": kwargs.get("enhance"),
        "nologo": kwargs.get("nologo"),
    }
    # normalize
    if extra["seed"] is not None:
        try:
            extra["seed"] = int(extra["seed"])
        except Exception:
            extra["seed"] = None
    for k in ("width", "height"):
        if extra[k] is not None:
            try:
                extra[k] = int(extra[k])
            except Exception:
                extra[k] = None
    for k in ("enhance", "nologo"):
        if extra[k] is not None:
            extra[k] = str(extra[k]).strip().lower() in ("1", "true", "yes", "on")

    return str(prompt), (str(style_name) if style_name else None), (str(out_path) if out_path else None), extra


def generate_kld_evening_image(*args, **kwargs) -> str:
    """
    Main entry point. Returns local file path.

    Optional kwargs:
      seed=int, width=int, height=int, enhance=bool, nologo=bool
    """
    prompt, style_name, out_path, extra = _extract_args(args, kwargs)

    final_prompt = (prompt.strip() + (f" style:{style_name}" if style_name else "")).strip()

    seed = extra.get("seed")
    width = extra.get("width") or DEFAULT_W
    height = extra.get("height") or DEFAULT_H
    enhance = extra.get("enhance")
    nologo = extra.get("nologo")

    if enhance is None:
        enhance = True
    if nologo is None:
        nologo = True

    if out_path is None:
        DEFAULT_DIR.mkdir(parents=True, exist_ok=True)
        if seed is None:
            seed = _sha_seed(final_prompt)
        out_path = str(DEFAULT_DIR / f"kld_{int(seed)}.jpg")

    url = build_pollinations_url(
        prompt,
        style_name,
        width=width,
        height=height,
        seed=seed,
        enhance=bool(enhance),
        nologo=bool(nologo),
    )
    log.info("imagegen: Pollinations URL built (len=%d)", len(url))
    return download_image_to_file(url, out_path)


# Compatibility aliases
generate_kld_image = generate_kld_evening_image
generate_astro_image = generate_kld_evening_image
generate_image = generate_kld_evening_image
