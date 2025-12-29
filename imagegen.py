#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
imagegen.py

Простейший генератор картинок для Вайбометра.

Идея:
- post_common / KLD-логика уже собирает красивый prompt через
  image_prompt_kld.build_kld_evening_prompt(date, marine_mood, inland_mood, astro_mood_en)
- здесь мы по этому prompt'у строим URL для Pollinations
  (без API-ключа) и возвращаем его как строку.

python-telegram-bot умеет принимать URL в send_photo / sendMediaGroup,
поэтому достаточно вернуть строку-URL.

На будущее:
- сюда же можно будет «подложить» Stable Horde или другой сервис,
  не меняя интерфейс для остального кода.
"""

from __future__ import annotations

import logging
import hashlib
from urllib.parse import quote_plus
from typing import Optional

log = logging.getLogger(__name__)

# Базовый эндпоинт Pollinations
BASE_URL = "https://image.pollinations.ai/prompt"


def _build_url(prompt: str, style_name: Optional[str] = None) -> str:
    """
    Строим URL для Pollinations.

    - style_name подмешиваем в prompt, чтобы немного менять картинку по стилям.
    - seed считаем из prompt+style, чтобы картинка была стабильной для одного дня.
    """
    style_part = f" style:{style_name}" if style_name else ""
    final_prompt = (prompt or "").strip() + style_part

    if not final_prompt:
        raise ValueError("imagegen: empty prompt")

    encoded = quote_plus(final_prompt)

    # детерминированный seed, чтобы у одного и того же prompt+style
    # картинка была одинаковой
    seed = int(hashlib.sha256(final_prompt.encode("utf-8")).hexdigest()[:8], 16)

    url = (
        f"{BASE_URL}/{encoded}"
        f"?width=1024&height=1024&seed={seed}&nologo=true&enhance=true"
    )
    return url


def _core_generate(prompt: str, style_name: Optional[str] = None, **kwargs) -> str:
    """
    Внутренний генератор: принимает prompt и style_name, возвращает URL.
    """
    url = _build_url(prompt, style_name)
    log.info("imagegen: built Pollinations URL: %s", url)
    return url


def _extract_prompt_and_style(args, kwargs):
    """
    Универсальный разбор аргументов:

    Позволяет пережить разные варианты вызова:
      imagegen.fn(prompt, style)
      imagegen.fn(prompt, style, date, tz, ...)
      imagegen.fn(prompt=..., style_name=...)
    """
    prompt = kwargs.get("prompt")
    style_name = (
        kwargs.get("style_name")
        or kwargs.get("style")
        or kwargs.get("theme")
    )

    if prompt is None and args:
        prompt = args[0]
    if style_name is None and len(args) > 1:
        style_name = args[1]

    if not prompt:
        raise ValueError("imagegen: no prompt passed")

    return prompt, style_name


# ───────── Основная функция для KLD ─────────

def generate_kld_evening_image(*args, **kwargs) -> str:
    """
    Основная точка входа для Калининграда (вечерний пост).

    Ожидается вызов вроде:
        url = imagegen.generate_kld_evening_image(prompt, style_name)
    или с дополнительными аргументами (date, tz, ...), которые мы игнорируем.
    """
    prompt, style_name = _extract_prompt_and_style(args, kwargs)
    return _core_generate(prompt, style_name)


# ───────── Алиасы на всякий случай ─────────
# Чтобы не ловить AttributeError, если в post_common
# вдруг используется другое имя функции.

generate_kld_image   = generate_kld_evening_image
generate_astro_image = generate_kld_evening_image
generate_image       = generate_kld_evening_image
