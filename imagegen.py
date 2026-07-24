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

import base64
import hashlib
import io
import ipaddress
import json
import logging
import os
import socket
import time
from pathlib import Path
from typing import Optional, Tuple, Any
from urllib.error import HTTPError
from urllib.parse import quote, urljoin, urlparse
import urllib.request

log = logging.getLogger(__name__)

BASE_URL = "https://image.pollinations.ai/prompt"

DEFAULT_W = int(os.getenv("IMG_W", "1024"))
DEFAULT_H = int(os.getenv("IMG_H", "1024"))

HTTP_TIMEOUT = float(os.getenv("IMG_HTTP_TIMEOUT", "25"))
HTTP_RETRIES = int(os.getenv("IMG_HTTP_RETRIES", "3"))
HTTP_BACKOFF = float(os.getenv("IMG_HTTP_BACKOFF", "1.6"))

DEFAULT_DIR = Path(os.getenv("IMG_OUT_DIR", ".cache/images"))
HORDE_BASE_URL = "https://stablehorde.net/api/v2"

_LAST_GENERATION_DIAGNOSTICS: dict[str, dict[str, Any]] = {}


class ImageGenerationError(RuntimeError):
    """Provider failure with structured, secret-free HTTP attempt diagnostics."""

    def __init__(
        self,
        message: str,
        *,
        backend: str,
        reason: str,
        attempts: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.backend = backend
        self.reason = reason
        self.attempts = list(attempts or [])


def _set_generation_diagnostics(backend: str, payload: dict[str, Any]) -> None:
    _LAST_GENERATION_DIAGNOSTICS[backend] = dict(payload)


def get_generation_diagnostics(backend: str) -> dict[str, Any]:
    return dict(_LAST_GENERATION_DIAGNOSTICS.get(str(backend), {}))


def stable_horde_enabled() -> bool:
    return os.getenv("KLD_STABLE_HORDE_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"}


def _exception_attempt(attempt: int, exc: Exception) -> dict[str, Any]:
    return {
        "attempt": attempt,
        "exception_type": type(exc).__name__,
        "http_status": int(exc.code) if isinstance(exc, HTTPError) else None,
        "message": " ".join(str(exc).split())[:300],
    }


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


def _validate_download_payload(data: bytes, content_type: Optional[str]) -> None:
    if _sniff_ext(data, content_type) is None:
        raise ValueError("imagegen: response is not a supported image")
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as image:
            image.load()
            if image.width < 256 or image.height < 256:
                raise ValueError(
                    f"imagegen: image dimensions too small ({image.width}x{image.height})"
                )
    except ImportError:
        return
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("imagegen: response failed image validation") from exc


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
    backend: str = "pollinations",
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
    attempts: list[dict[str, Any]] = []
    retries = max(1, int(retries))

    for attempt in range(1, retries + 1):
        try:
            data, ctype = _http_get(url, timeout=timeout)

            if not data or len(data) < 128:
                raise ValueError(f"imagegen: response too small ({len(data)} bytes)")

            _validate_download_payload(data, ctype)
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

            attempts.append(
                {
                    "attempt": attempt,
                    "exception_type": "",
                    "http_status": 200,
                    "message": "",
                    "payload_bytes": len(payload),
                }
            )
            _set_generation_diagnostics(
                backend,
                {
                    "backend": backend,
                    "http_attempt_count": len(attempts),
                    "attempts": attempts,
                    "result": "success",
                },
            )
            log.info("imagegen: saved image -> %s (attempt=%d, mode=%s)", out_final, attempt, mode)
            return str(out_final)

        except Exception as e:
            last_err = e
            attempts.append(_exception_attempt(attempt, e))
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

    reason = (
        "invalid_image"
        if isinstance(last_err, ValueError) and "imagegen:" in str(last_err)
        else "provider_failure"
    )
    diagnostics = {
        "backend": backend,
        "http_attempt_count": len(attempts),
        "attempts": attempts,
        "result": "failed",
        "reason": reason,
    }
    _set_generation_diagnostics(backend, diagnostics)
    raise ImageGenerationError(
        f"imagegen: failed to download image after {retries} tries: {last_err}",
        backend=backend,
        reason=reason,
        attempts=attempts,
    )


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


def _horde_json_request(
    url: str,
    *,
    method: str,
    headers: dict[str, str],
    attempts: list[dict[str, Any]],
    stage: str,
    timeout: float,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    attempt_number = len(attempts) + 1
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            status = int(getattr(response, "status", 200) or 200)
            attempts.append(
                {
                    "attempt": attempt_number,
                    "stage": stage,
                    "exception_type": "",
                    "http_status": status,
                    "message": "",
                    "payload_bytes": len(raw),
                }
            )
    except Exception as exc:
        item = _exception_attempt(attempt_number, exc)
        item["stage"] = stage
        attempts.append(item)
        raise
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        attempts[-1]["exception_type"] = type(exc).__name__
        attempts[-1]["message"] = "invalid JSON response"
        raise ValueError(f"Stable Horde {stage} returned invalid JSON") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Stable Horde {stage} returned a non-object response")
    return data


def _public_horde_image_url(value: str) -> bool:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        return False
    hostname = parsed.hostname.lower()
    if hostname == "localhost" or hostname.endswith(".localhost"):
        return False
    try:
        addresses = [ipaddress.ip_address(hostname)]
    except ValueError:
        try:
            addresses = [
                ipaddress.ip_address(item[4][0])
                for item in socket.getaddrinfo(hostname, parsed.port or 443, type=socket.SOCK_STREAM)
            ]
        except (OSError, ValueError):
            return False
    return bool(addresses) and all(address.is_global for address in addresses)


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _horde_image_payload(
    value: Any,
    *,
    attempts: list[dict[str, Any]],
    timeout: float,
) -> bytes:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Stable Horde returned no image payload")
    raw = value.strip()
    if raw.startswith(("https://", "http://")):
        opener = urllib.request.build_opener(_NoRedirectHandler())
        current_url = raw
        for redirect_index in range(4):
            if not _public_horde_image_url(current_url):
                raise ValueError("Stable Horde returned an unsafe image URL")
            request = urllib.request.Request(
                current_url,
                headers={
                    "User-Agent": "VayboMeter-KLD/1.0",
                    "Accept": "image/png,image/jpeg,image/webp",
                },
                method="GET",
            )
            attempt_number = len(attempts) + 1
            try:
                response = opener.open(request, timeout=timeout)
            except HTTPError as exc:
                if exc.code in {301, 302, 303, 307, 308} and redirect_index < 3:
                    location = str(exc.headers.get("Location") or "").strip()
                    attempts.append(
                        {
                            "attempt": attempt_number,
                            "stage": "image_redirect",
                            "exception_type": "",
                            "http_status": int(exc.code),
                            "message": "",
                            "payload_bytes": 0,
                        }
                    )
                    if not location:
                        raise ValueError("Stable Horde image redirect had no location") from exc
                    current_url = urljoin(current_url, location)
                    continue
                item = _exception_attempt(attempt_number, exc)
                item["stage"] = "image_download"
                attempts.append(item)
                raise
            except Exception as exc:
                item = _exception_attempt(attempt_number, exc)
                item["stage"] = "image_download"
                attempts.append(item)
                raise
            try:
                content_type = str(response.headers.get("Content-Type") or "").lower()
                if not content_type.startswith("image/"):
                    raise ValueError("Stable Horde image URL returned non-image content")
                payload = response.read(25 * 1024 * 1024 + 1)
                if len(payload) > 25 * 1024 * 1024:
                    raise ValueError("Stable Horde image exceeded the payload limit")
                attempts.append(
                    {
                        "attempt": attempt_number,
                        "stage": "image_download",
                        "exception_type": "",
                        "http_status": int(getattr(response, "status", 200) or 200),
                        "message": "",
                        "payload_bytes": len(payload),
                    }
                )
                return payload
            except Exception as exc:
                item = _exception_attempt(attempt_number, exc)
                item["stage"] = "image_download"
                attempts.append(item)
                raise
            finally:
                response.close()
        raise ValueError("Stable Horde image redirect limit exceeded")
    if raw.lower().startswith("data:"):
        header, separator, raw = raw.partition(",")
        if not separator or ";base64" not in header.lower() or not header[5:].lower().startswith("image/"):
            raise ValueError("Stable Horde returned an invalid image data URL")
    try:
        return base64.b64decode(raw, validate=True)
    except Exception as exc:
        raise ValueError("Stable Horde returned invalid base64 image data") from exc


def _validated_horde_jpeg(payload: bytes) -> bytes:
    if len(payload) < 128:
        raise ValueError("Stable Horde image response is too small")
    try:
        from PIL import Image

        with Image.open(io.BytesIO(payload)) as image:
            image.load()
            if image.width < 256 or image.height < 256:
                raise ValueError("Stable Horde image dimensions are too small")
    except ImportError:
        if _sniff_ext(payload, None) is None:
            raise ValueError("Stable Horde returned an unsupported image payload")
    jpeg, _mode = _to_jpeg_bytes(payload)
    if _sniff_ext(jpeg, "image/jpeg") != ".jpg":
        raise ValueError("Stable Horde image could not be normalized")
    return jpeg


def generate_kld_stable_horde_image(*args, **kwargs) -> str:
    """Generate through anonymous/configured Stable Horde as the bounded second backend."""
    if not stable_horde_enabled():
        raise ImageGenerationError(
            "Stable Horde backend is disabled",
            backend="stable_horde",
            reason="backend_disabled",
            attempts=[],
        )

    prompt, style_name, out_path, extra = _extract_args(args, kwargs)
    final_prompt = (prompt.strip() + (f" style:{style_name}" if style_name else "")).strip()
    seed = extra.get("seed")
    if seed is None:
        seed = _sha_seed(final_prompt)
    width = min(768, max(512, int(extra.get("width") or 512)))
    height = min(768, max(512, int(extra.get("height") or 512)))
    width -= width % 64
    height -= height % 64
    if out_path is None:
        DEFAULT_DIR.mkdir(parents=True, exist_ok=True)
        out_path = str(DEFAULT_DIR / f"kld_horde_{int(seed)}.jpg")
    output = Path(out_path).with_suffix(".jpg")
    _ensure_parent(output)

    api_key = (
        os.getenv("KLD_STABLE_HORDE_API_KEY", "").strip()
        or os.getenv("AI_HORDE_API_KEY", "").strip()
        or os.getenv("HORDE_API_KEY", "").strip()
        or "0000000000"
    )
    headers = {
        "User-Agent": "VayboMeter-KLD/1.0",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "apikey": api_key,
    }
    total_timeout = max(30.0, float(os.getenv("KLD_STABLE_HORDE_TIMEOUT", "90")))
    poll_interval = max(2.0, float(os.getenv("KLD_STABLE_HORDE_POLL_INTERVAL", "5")))
    attempts: list[dict[str, Any]] = []
    started = time.monotonic()
    try:
        submission = _horde_json_request(
            f"{HORDE_BASE_URL}/generate/async",
            method="POST",
            headers=headers,
            attempts=attempts,
            stage="submit",
            timeout=15,
            payload={
                "prompt": final_prompt,
                "params": {
                    "width": width,
                    "height": height,
                    "steps": 24,
                    "n": 1,
                    "cfg_scale": 7,
                    "sampler_name": "k_euler",
                    "seed": str(int(seed)),
                },
                "nsfw": False,
                "censor_nsfw": True,
                "trusted_workers": False,
                "shared": True,
            },
        )
        job_id = str(submission.get("id") or "").strip()
        if not job_id:
            raise ValueError("Stable Horde submission returned no job id")

        while True:
            if time.monotonic() - started >= total_timeout:
                raise TimeoutError(f"Stable Horde timed out after {total_timeout:.0f}s")
            check = _horde_json_request(
                f"{HORDE_BASE_URL}/generate/check/{job_id}",
                method="GET",
                headers=headers,
                attempts=attempts,
                stage="check",
                timeout=12,
            )
            if check.get("faulted") or check.get("cancelled"):
                raise RuntimeError("Stable Horde job faulted or was cancelled")
            if check.get("done") or check.get("finished"):
                break
            time.sleep(poll_interval)

        status = _horde_json_request(
            f"{HORDE_BASE_URL}/generate/status/{job_id}",
            method="GET",
            headers=headers,
            attempts=attempts,
            stage="status",
            timeout=20,
        )
        generations = status.get("generations")
        if not isinstance(generations, list) or not generations:
            raise ValueError("Stable Horde returned no generations")
        generation = generations[0]
        if not isinstance(generation, dict) or generation.get("censored"):
            raise ValueError("Stable Horde generation was unavailable or censored")
        payload = _horde_image_payload(
            generation.get("img"),
            attempts=attempts,
            timeout=min(30.0, total_timeout),
        )
        jpeg = _validated_horde_jpeg(payload)
        output.write_bytes(jpeg)
    except Exception as exc:
        diagnostics = {
            "backend": "stable_horde",
            "http_attempt_count": len(attempts),
            "attempts": attempts,
            "result": "failed",
            "exception_type": type(exc).__name__,
        }
        _set_generation_diagnostics("stable_horde", diagnostics)
        raise ImageGenerationError(
            f"Stable Horde generation failed: {exc}",
            backend="stable_horde",
            reason="provider_failure",
            attempts=attempts,
        ) from exc

    _set_generation_diagnostics(
        "stable_horde",
        {
            "backend": "stable_horde",
            "http_attempt_count": len(attempts),
            "attempts": attempts,
            "result": "success",
            "elapsed_seconds": round(time.monotonic() - started, 3),
        },
    )
    return str(output)


# Compatibility aliases
generate_kld_image = generate_kld_evening_image
generate_astro_image = generate_kld_evening_image
generate_image = generate_kld_evening_image
