#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
imagegen.py — простой генератор изображений для VayboMeter (Kaliningrad).

Основная цель:
  - Сгенерировать картинку по prompt через Pollinations (без API-ключей),
  - скачать результат в локальный файл,
  - чтобы Telegram отправлял изображение как "file upload", а не URL.

Использование (из кода):
  from imagegen import generate_kld_evening_image
  path = generate_kld_evening_image(prompt, style_name, out_path=".../img.jpg")
"""

from __future__ import annotations

import os
import re
import time
import random
import logging
from pathlib import Path
from typing import Optional

import requests
from urllib.parse import quote_plus

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

POLLINATIONS_BASE = "https://image.pollinations.ai/prompt/"
DEFAULT_SIZE = (1024, 1024)

# Где хранить картинки по умолчанию (если out_path не задан)
DEFAULT_DIR = Path(".cache") / "kld_images"

# User-Agent помогает избежать странных ответов от CDN
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; VayboMeterBot/1.0; +https://t.me/vaybometer_39reg)"
}


def _safe_slug(s: str, fallback: str = "default") -> str:
    s = (s or "").strip()
    if not s:
        return fallback
    s = re.sub(r"[^a-zA-Z0-9_-]+", "_", s).strip("_")
    return s or fallback


def _build_pollinations_url(prompt: str, seed: int, width: int, height: int) -> str:
    encoded_prompt = quote_plus(prompt)
    return (
        POLLINATIONS_BASE
        + encoded_prompt
        + f"?width={width}&height={height}&seed={seed}&nologo=true&enhance=true"
    )


def _download_file(url: str, out_path: Path, timeout: int = 25, max_bytes: int = 20_000_000) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with requests.get(url, headers=DEFAULT_HEADERS, stream=True, timeout=timeout) as r:
        r.raise_for_status()
        total = 0
        tmp = out_path.with_suffix(out_path.suffix + ".part")
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                f.write(chunk)
                total += len(chunk)
                if total > max_bytes:
                    raise RuntimeError(f"Image too large (> {max_bytes} bytes) from {url}")
        tmp.replace(out_path)


def generate_kld_evening_image(
    prompt: str,
    style_name: str = "",
    out_path: Optional[str] = None,
    width: int = DEFAULT_SIZE[0],
    height: int = DEFAULT_SIZE[1],
    seed: Optional[int] = None,
    retries: int = 2,
    retry_sleep: float = 1.5,
) -> str:
    """
    Генерирует картинку через Pollinations и сохраняет в out_path.
    Возвращает путь к файлу (строкой).

    Важно:
      - Это не "рисование" в локальном окружении, а HTTP-запрос к Pollinations.
      - Мы не используем API-ключи.
    """
    if not prompt or not str(prompt).strip():
        raise ValueError("prompt is empty")

    if seed is None:
        # Детерминированно + немного шума: стиль + дата/время
        seed = (hash(prompt) ^ hash(style_name) ^ int(time.time())) & 0xFFFFFFFF

    if out_path:
        out = Path(out_path)
    else:
        DEFAULT_DIR.mkdir(parents=True, exist_ok=True)
        slug = _safe_slug(style_name, "default")
        out = DEFAULT_DIR / f"kld_{slug}_{seed}.jpg"

    url = _build_pollinations_url(prompt=prompt, seed=seed, width=width, height=height)
    logging.info("imagegen: pollinations url: %s", url)

    last_err: Optional[Exception] = None
    for attempt in range(1, retries + 2):
        try:
            _download_file(url, out)
            if out.exists() and out.stat().st_size > 10_000:
                logging.info("imagegen: saved %s (%d bytes)", out, out.stat().st_size)
                return str(out)
            raise RuntimeError("Downloaded file is too small / invalid")
        except Exception as e:
            last_err = e
            logging.warning("imagegen: attempt %d failed: %s", attempt, e)
            if attempt < retries + 1:
                time.sleep(retry_sleep + random.random() * 0.5)

    raise RuntimeError(f"imagegen: failed after retries: {last_err}") from last_err
