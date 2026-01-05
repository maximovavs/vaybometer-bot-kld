#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gpt.py

Обёртка LLM для VayboMeter (Kaliningrad):

- Порядок провайдеров: OpenAI → Gemini → Groq.
- При 429/insufficient_quota у OpenAI отключаем OpenAI на весь текущий запуск,
  чтобы не «стучать» повторно в платный провайдер.
- Gemini перебираем по списку моделей (как вы просили), а затем (если нужно) идём в Groq.
- Контракт gpt_blurb(culprit) сохранён: возвращает (summary: str, tips: List[str]).

Важно про Gemini:
- В OpenAI-совместимом эндпоинте Gemini требуется заголовок Authorization: Bearer <API_KEY>.
- Поэтому Gemini здесь вызывается через OpenAI SDK с base_url=.../v1beta/openai/,
  а ключ берётся из переменной окружения GEMINI_API_KEY.
"""

from __future__ import annotations

import logging
import os
import random
from typing import List, Optional, Tuple

log = logging.getLogger(__name__)

try:
    from openai import OpenAI  # type: ignore
except Exception:
    OpenAI = None  # type: ignore


# ── ключи ────────────────────────────────────────────────────────────────────
OPENAI_KEY = (os.getenv("OPENAI_API_KEY") or "").strip()
GEMINI_KEY = (os.getenv("GEMINI_API_KEY") or "").strip()
GROQ_KEY = (os.getenv("GROQ_API_KEY") or "").strip()

# ── модели ───────────────────────────────────────────────────────────────────
OPENAI_MODEL = (os.getenv("OPENAI_MODEL") or "gpt-4o-mini").strip()

# Gemini: перебор моделей (как вы попросили)
GEMINI_MODELS = [
    "gemini-3-flash",
    "gemini-3-pro",
    "gemini-2.5-flash",
    "gemini-3-flash-preview",
]

# Groq: перебираем по порядку (первая доступная сработает)
GROQ_MODELS = [
    "moonshotai/kimi-k2-instruct-0905",
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
    "gemma2-9b-it",
    "qwen/qwen3-32b",
    "deepseek-r1-distill-llama-70b",
]

# ── глобальные флаги на запуск ───────────────────────────────────────────────
_OPENAI_DISABLED_FOR_RUN = False
_GEMINI_DISABLED_FOR_RUN = False
_GEMINI_MODEL_SET: Optional[set[str]] = None


# ── клиенты ────────────────────────────────────────────────────────────────
def _openai_client() -> Optional["OpenAI"]:
    if not OPENAI_KEY or not OpenAI:
        return None
    try:
        return OpenAI(api_key=OPENAI_KEY, timeout=20.0, max_retries=0)
    except Exception as e:
        log.warning("OpenAI client init error: %s", e)
        return None


def _gemini_openai_compat_client() -> Optional["OpenAI"]:
    """Gemini через OpenAI-совместимый эндпоинт."""
    if not GEMINI_KEY or not OpenAI:
        return None
    try:
        return OpenAI(
            api_key=GEMINI_KEY,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            timeout=25.0,
            max_retries=0,
        )
    except Exception as e:
        log.warning("Gemini client init error: %s", e)
        return None


def _groq_client() -> Optional["OpenAI"]:
    if not GROQ_KEY or not OpenAI:
        return None
    try:
        return OpenAI(
            api_key=GROQ_KEY,
            base_url="https://api.groq.com/openai/v1",
            timeout=25.0,
            max_retries=0,
        )
    except Exception as e:
        log.warning("Groq client init error: %s", e)
        return None


def _is_quota_or_rate_limit(err: Exception) -> bool:
    msg = str(err).lower()
    return any(k in msg for k in ("insufficient_quota", "rate limit", "429", "quota"))


def _is_model_not_found(err: Exception) -> bool:
    msg = str(err).lower()
    return ("not found" in msg) or ("decommissioned" in msg) or ("unsupported" in msg)


def _gemini_models_available(cli: "OpenAI") -> Optional[set[str]]:
    """Пытаемся получить список моделей Gemini через /models."""
    global _GEMINI_MODEL_SET
    if _GEMINI_MODEL_SET is not None:
        return _GEMINI_MODEL_SET

    try:
        models = cli.models.list()
        names: set[str] = set()
        for m in getattr(models, "data", []) or []:
            name = getattr(m, "id", None) or getattr(m, "name", None)
            if isinstance(name, str) and name.strip():
                names.add(name.strip())
        _GEMINI_MODEL_SET = names if names else set()
        if names:
            log.info("Gemini models.list(): %d models", len(names))
        else:
            log.warning("Gemini models.list(): empty list")
        return _GEMINI_MODEL_SET
    except Exception as e:
        msg = str(e).lower()
        if any(k in msg for k in ("missing authorization", "unauthorized", "permission_denied", "invalid api key", "401", "403")):
            _GEMINI_DISABLED_FOR_RUN = True
            log.warning("Gemini models.list() auth error → disable for this run: %s", e)
            return None
        log.warning("Gemini models.list() failed: %s", e)
        return None


# ── общая обёртка ─────────────────────────────────────────────────────────
def gpt_complete(
    prompt: str,
    system: Optional[str] = None,
    temperature: float = 0.7,
    max_tokens: int = 600,
) -> str:
    """
    Универсальный вызов LLM. Пробует по очереди: OpenAI → Gemini → Groq.
    Возвращает text или "" (если все провайдеры недоступны).
    """
    global _OPENAI_DISABLED_FOR_RUN, _GEMINI_DISABLED_FOR_RUN

    if not prompt or not str(prompt).strip():
        return ""

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    # 1) OpenAI
    if not _OPENAI_DISABLED_FOR_RUN:
        cli = _openai_client()
        if cli:
            try:
                r = cli.chat.completions.create(
                    model=OPENAI_MODEL,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                text = (r.choices[0].message.content or "").strip()
                if text:
                    return text
            except Exception as e:
                if _is_quota_or_rate_limit(e):
                    _OPENAI_DISABLED_FOR_RUN = True
                    log.warning("OpenAI quota/rate-limit → disable for this run: %s", e)
                else:
                    log.warning("OpenAI error: %s", e)

    # 2) Gemini
    if (not _GEMINI_DISABLED_FOR_RUN) and GEMINI_KEY:
        cli = _gemini_openai_compat_client()
        if cli:
            available = _gemini_models_available(cli)
            if isinstance(available, set) and available:
                preferred = [m for m in GEMINI_MODELS if m in available]
                rest = [m for m in GEMINI_MODELS if m not in preferred]
                candidates = preferred + rest
            else:
                candidates = GEMINI_MODELS[:]

            for mdl in candidates:
                try:
                    r = cli.chat.completions.create(
                        model=mdl,
                        messages=messages,
                        temperature=temperature,
                        max_tokens=max_tokens,
                    )
                    text = (r.choices[0].message.content or "").strip()
                    if text:
                        log.info("LLM: Gemini ok (model=%s)", mdl)
                        return text
                except Exception as e:
                    msg = str(e).lower()
                    if "missing authorization" in msg or "unauth" in msg or "401" in msg:
                        _GEMINI_DISABLED_FOR_RUN = True
                        log.warning("Gemini auth error → disable for this run: %s", e)
                        break
                    if _is_model_not_found(e):
                        log.warning("Gemini model %s not found/unsupported, trying next.", mdl)
                        continue
                    if _is_quota_or_rate_limit(e):
                        log.warning("Gemini rate/quota on %s, trying next.", mdl)
                        continue
                    log.warning("Gemini error on %s: %s", mdl, e)
                    continue
        else:
            _GEMINI_DISABLED_FOR_RUN = True
            log.warning("Gemini client unavailable — disabling for this run")
    elif not GEMINI_KEY:
        log.info("Gemini skipped: GEMINI_API_KEY is not set")

    # 3) Groq
    cli = _groq_client()
    if cli:
        for mdl in GROQ_MODELS:
            try:
                r = cli.chat.completions.create(
                    model=mdl,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                text = (r.choices[0].message.content or "").strip()
                if text:
                    log.info("LLM: Groq ok (model=%s)", mdl)
                    return text
            except Exception as e:
                if _is_model_not_found(e):
                    log.warning("Groq model %s decommissioned/not found, trying next.", mdl)
                    continue
                if _is_quota_or_rate_limit(e):
                    log.warning("Groq rate/quota on %s, trying next.", mdl)
                    continue
                log.warning("Groq error on %s: %s", mdl, e)
                continue

    return ""


# ── словари фолбэков ──────────────────────────────────────────────────────
CULPRITS = {
    "туман": {
        "emoji": "🌁",
        "tips": [
            "🔦 Светлая одежда и фонарь",
            "🚗 Водите аккуратно",
            "⏰ Планируйте поездки заранее",
            "🕶️ Используйте очки против бликов",
        ],
    },
    "магнитные бури": {
        "emoji": "🧲",
        "tips": [
            "🧘 5-минутная дыхательная пауза",
            "🌿 Заварите чай с травами",
            "🙅 Избегайте стрессовых новостей",
            "😌 Лёгкая растяжка перед сном",
        ],
    },
    "низкое давление": {
        "emoji": "🌡️",
        "tips": [
            "💧 Пейте больше воды",
            "😴 20-минутный дневной отдых",
            "🤸 Лёгкая зарядка утром",
            "🥗 Лёгкий ужин без соли",
        ],
    },
    "шальной ветер": {
        "emoji": "💨",
        "tips": [
            "🧣 Захватите шарф",
            "🚶 Короткая прогулка",
            "🕶️ Защитите глаза от пыли",
            "🌳 Избегайте открытых пространств",
        ],
    },
    "жара": {
        "emoji": "🔥",
        "tips": [
            "💦 Держите бутылку воды рядом",
            "🧢 Носите головной убор",
            "🌳 Ищите тень в полдень",
            "❄️ Прохладный компресс на лоб",
        ],
    },
    "сырость": {
        "emoji": "💧",
        "tips": [
            "👟 Смените обувь при необходимости",
            "🌂 Держите компактный зонт",
            "🌬️ Проветривайте жилище",
            "🧥 Лёгкая непромокаемая куртка",
        ],
    },
    "полная луна": {
        "emoji": "🌕",
        "tips": [
            "📝 Запишите яркие идеи перед сном",
            "🧘 Мягкая медитация вечером",
            "🌙 Посмотрите на луну без гаджетов",
            "📚 Чтение на свежем воздухе",
        ],
    },
    "мини-парад планет": {
        "emoji": "✨",
        "tips": [
            "🔭 Посмотрите на небо на рассвете",
            "📸 Сделайте фотографию заката",
            "🤔 Подумайте о бескрайних просторах",
            "🎶 Слушайте спокойную музыку вечером",
        ],
    },
}

ASTRO_HEALTH_FALLBACK: List[str] = [
    "💤 Соблюдайте режим сна: ложитесь не позже 23:00",
    "🥦 Включите в рацион свежие овощи и зелень",
    "🧘 Делайте лёгкую растяжку утром и вечером",
    "🚶 Прогуливайтесь 20 минут на свежем воздухе",
    "💧 Пейте воду небольшими порциями",
]


def gpt_blurb(culprit: str) -> Tuple[str, List[str]]:
    """Возвращает (summary: str, tips: List[str])."""
    culprit = (culprit or "").strip() or "погоду"
    culprit_lower = culprit.lower().strip()

    def _make_prompt(cul: str) -> str:
        return (
            "Действуй как экспертный health coach со знаниями функциональной медицины, "
            "который постоянно изучает что-то новое, но пишет грамотно. "
            f"Напиши одной строкой: «Если завтра что-то пойдёт не так, вините {cul}!». "
            "После точки — короткий позитив ≤12 слов для подписчиков. "
            "Затем дай ровно 3 совета (сон, питание, дыхание/лёгкая активность) "
            "≤12 слов с эмодзи. Не пиши слово «совет». Ответ — по строкам."
        )

    def _from_lines(cul: str, lines: List[str], fallback_pool: List[str]) -> Tuple[str, List[str]]:
        summary = lines[0] if lines else f"Если завтра что-то пойдёт не так, вините {cul}! 😉"
        tips = [ln for ln in lines[1:] if ln][:3]
        if len(tips) < 3:
            remaining = [t for t in fallback_pool if t not in tips]
            if remaining:
                tips += random.sample(remaining, min(3 - len(tips), len(remaining)))
        return summary, tips[:3]

    if culprit_lower in CULPRITS:
        tips_pool = CULPRITS[culprit_lower]["tips"]
        text = gpt_complete(prompt=_make_prompt(culprit), temperature=0.7, max_tokens=500)
        if not text:
            return f"Если завтра что-то пойдёт не так, вините {culprit}! 😉", random.sample(tips_pool, 3)
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        return _from_lines(culprit, lines, tips_pool)

    text = gpt_complete(prompt=_make_prompt(culprit), temperature=0.7, max_tokens=500)
    if not text:
        return f"Если завтра что-то пойдёт не так, вините {culprit}! 😉", random.sample(ASTRO_HEALTH_FALLBACK, 3)

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    fallback_pool = ASTRO_HEALTH_FALLBACK + sum((c["tips"] for c in CULPRITS.values()), [])
    return _from_lines(culprit, lines, fallback_pool)
