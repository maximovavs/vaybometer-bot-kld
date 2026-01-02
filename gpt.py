#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
gpt.py

Логика для блока «Вывод» и «Рекомендации» + общая обёртка gpt_complete().

Требования (обновлено):
• Основной порядок провайдеров: OpenAI → Gemini → Groq.
• OpenAI: один запрос (без ретраев). На 429 / insufficient_quota — сразу уходим дальше.
• Gemini: перебираем модели по приоритетному списку (можно переопределить ENV).
  Реализация через OpenAI-совместимый эндпоинт Gemini (base_url=.../v1beta/openai/).
• Groq: как и раньше, перебираем модели по списку.

ENV:
  OPENAI_API_KEY, GEMINI_API_KEY, GROQ_API_KEY
  OPENAI_MODEL (default: gpt-4o-mini)
  GEMINI_MODELS (comma-separated list; default: see GEMINI_MODELS_DEFAULT)
  PROVIDER_ORDER (optional, comma-separated: openai,gemini,groq)
"""

from __future__ import annotations

import logging
import os
import random
from typing import List, Optional, Tuple, Iterable, Set

log = logging.getLogger(__name__)

try:
    from openai import OpenAI  # type: ignore
except Exception:  # ImportError / RuntimeError etc.
    OpenAI = None  # type: ignore


# ── ключи ────────────────────────────────────────────────────────────────────
OPENAI_KEY = (os.getenv("OPENAI_API_KEY") or "").strip()
GEMINI_KEY = (os.getenv("GEMINI_API_KEY") or "").strip()
GROQ_KEY = (os.getenv("GROQ_API_KEY") or "").strip()

OPENAI_MODEL = (os.getenv("OPENAI_MODEL") or "gpt-4o-mini").strip()

# Приоритет моделей Gemini (по запросу пользователя)
GEMINI_MODELS_DEFAULT = [
    "gemini-3-flash",
    "gemini-3-pro",
    "gemini-2.5-flash",
    "gemini-3-flash-preview",
]

# Порядок провайдеров (можно переопределить ENV PROVIDER_ORDER="openai,gemini,groq")
_PROVIDER_ENV = (os.getenv("PROVIDER_ORDER") or "").strip()
if _PROVIDER_ENV:
    PROVIDER_ORDER = [p.strip().lower() for p in _PROVIDER_ENV.split(",") if p.strip()]
else:
    PROVIDER_ORDER = ["openai", "gemini", "groq"]

# Модели Groq (первая доступная сработает)
GROQ_MODELS = [
    "moonshotai/kimi-k2-instruct-0905",
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
    "gemma2-9b-it",
    "qwen/qwen3-32b",
    "deepseek-r1-distill-llama-70b",
]

# ── кеш доступных моделей (чтобы не ловить 404 по кругу) ─────────────────────────
_GEMINI_AVAILABLE: Optional[Set[str]] = None

def _gemini_available_models(cli: "OpenAI") -> Optional[Set[str]]:
    """Пытаемся один раз получить список доступных моделей Gemini через models.list()."""
    global _GEMINI_AVAILABLE
    if _GEMINI_AVAILABLE is not None:
        return _GEMINI_AVAILABLE
    try:
        resp = cli.models.list()
        ids = set()
        for m in getattr(resp, "data", []) or []:
            mid = getattr(m, "id", None)
            if isinstance(mid, str) and mid.strip():
                ids.add(mid.strip())
        _GEMINI_AVAILABLE = ids or set()
        if _GEMINI_AVAILABLE:
            log.info("Gemini models.list(): %d models", len(_GEMINI_AVAILABLE))
        return _GEMINI_AVAILABLE
    except Exception as e:
        log.warning("Gemini models.list() failed (will try by probing): %s", e)
        _GEMINI_AVAILABLE = set()  # помечаем как «не удалось/пусто», чтобы не дергать постоянно
        return None

# ── клиенты ──────────────────────────────────────────────────────────────────
def _openai_client() -> Optional["OpenAI"]:
    """Клиент OpenAI с отключёнными ретраями."""
    if not OPENAI_KEY or not OpenAI:
        return None
    try:
        return OpenAI(api_key=OPENAI_KEY, timeout=20.0, max_retries=0)
    except Exception as e:
        log.warning("OpenAI client init error: %s", e)
        return None


def _gemini_client() -> Optional["OpenAI"]:
    """OpenAI-совместимый клиент Gemini."""
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
    """OpenAI-совместимый клиент Groq."""
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


# ── утилиты ──────────────────────────────────────────────────────────────────
def _is_quota_or_ratelimit(err_text: str) -> bool:
    s = (err_text or "").lower()
    return any(k in s for k in (
        "insufficient_quota",
        "quota",
        "rate limit",
        "ratelimit",
        "429",
        "too many requests",
    ))


def _is_model_not_found(err_text: str) -> bool:
    s = (err_text or "").lower()
    return any(k in s for k in (
        "not found",
        "model not found",
        "does not exist",
        "unsupported",
        "404",
        "decommissioned",
        "invalid model",
    ))


def _unique_keep_order(items: Iterable[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for x in items:
        x = (x or "").strip()
        if not x or x in seen:
            continue
        seen.add(x)
        out.append(x)
    return out


def _gemini_models_to_try() -> List[str]:
    """Берём список моделей Gemini из ENV (если задан), иначе — дефолтный."""
    env_list = (os.getenv("GEMINI_MODELS") or "").strip()
    if env_list:
        models = [m.strip() for m in env_list.split(",") if m.strip()]
        return _unique_keep_order(models)

    single = (os.getenv("GEMINI_MODEL") or "").strip()
    if single:
        return _unique_keep_order([single] + GEMINI_MODELS_DEFAULT)

    return list(GEMINI_MODELS_DEFAULT)


# ── общая обёртка ────────────────────────────────────────────────────────────
def gpt_complete(
    prompt: str,
    system: Optional[str] = None,
    temperature: float = 0.7,
    max_tokens: int = 600,
) -> str:
    """Пробует по очереди: OpenAI → Gemini → Groq. Возвращает text или ""."""
    if not prompt:
        return ""

    text = ""

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    # 1) OpenAI (один запрос)
    if "openai" in PROVIDER_ORDER and not text:
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
                    log.info("LLM: OpenAI ok (model=%s)", OPENAI_MODEL)
            except Exception as e:
                msg = str(e)
                if _is_quota_or_ratelimit(msg):
                    log.warning("OpenAI error (skip to next): %s", e)
                else:
                    log.warning("OpenAI error: %s", e)
                text = ""

    # 2) Gemini (перебор моделей)
    if "gemini" in PROVIDER_ORDER and not text:
        cli = _gemini_client()
        if cli:
            models_to_try = _gemini_models_to_try()
            avail = _gemini_available_models(cli)
            if avail:
                # фильтруем по реально доступным id, но сохраняем приоритет
                filtered = [m for m in models_to_try if m in avail]
                if filtered:
                    models_to_try = filtered
            for mdl in models_to_try:
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
                        break
                except Exception as e:
                    msg = str(e)
                    if _is_model_not_found(msg):
                        log.warning("Gemini model %s not found/unsupported, trying next.", mdl)
                        continue
                    if _is_quota_or_ratelimit(msg):
                        log.warning("Gemini quota/rate limit on %s, trying next.", mdl)
                        continue
                    log.warning("Gemini error on %s: %s", mdl, e)
                    continue

    # 3) Groq (перебор моделей)
    if "groq" in PROVIDER_ORDER and not text:
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
                        break
                except Exception as e:
                    msg = str(e)
                    if _is_model_not_found(msg):
                        log.warning("Groq model %s decommissioned/not found, trying next.", mdl)
                        continue
                    if _is_quota_or_ratelimit(msg):
                        log.warning("Groq rate limit on %s, trying next.", mdl)
                        continue
                    log.warning("Groq error on %s: %s", mdl, e)
                    continue

    return text or ""


# ── словари фолбэков ─────────────────────────────────────────────────────────
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
    "🥛 Пейте тёплое молоко с мёдом перед сном",
    "🧘 Делайте лёгкую растяжку утром и вечером",
    "🚶 Прогуливайтесь 20 минут на свежем воздухе",
]


def gpt_blurb(culprit: str) -> Tuple[str, List[str]]:
    """Публичная функция для блока «Вывод/Рекомендации»."""
    culprit_lower = (culprit or "").lower().strip()

    def _make_prompt(cul: str, astro: bool) -> str:
        if astro:
            return (
                "Действуй как экспертный health coach со знаниями функциональной медицины, "
                "который постоянно изучает что-то новое, но пишет грамотно. "
                f"Напиши одной строкой: «Если завтра что-то пойдёт не так, вините {cul}!». "
                "После точки — короткий позитив ≤12 слов для подписчиков. Не пиши само слово «совет». "
                "Затем дай ровно 3 совета (сон, питание, дыхание/лёгкая активность) "
                "≤12 слов с эмодзи. Ответ — по строкам."
            )
        return (
            "Действуй как экспертный health coach со знаниями функциональной медицины, "
            "который постоянно изучает что-то новое, но пишет грамотно. "
            f"Напиши одной строкой: «Если завтра что-то пойдёт не так, вините {cul}!». "
            "После точки — короткий позитив ≤12 слов для подписчиков. "
            "Затем дай ровно 3 совета по функциональной медицине "
            "(питание, сон, лёгкая физическая активность) ≤12 слов с эмодзи. "
            "Не пиши само слово «совет». Ответ — по строкам."
        )

    def _from_lines(cul: str, lines: List[str], fallback_pool: List[str]) -> Tuple[str, List[str]]:
        summary = lines[0] if lines else f"Если завтра что-то пойдёт не так, вините {cul}! 😉"
        tips = [ln for ln in lines[1:] if ln][:3]
        if len(tips) < 2:
            remaining = [t for t in fallback_pool if t not in tips]
            if remaining:
                tips += random.sample(remaining, min(3 - len(tips), len(remaining)))
        return summary, tips[:3]

    if culprit_lower in CULPRITS:
        tips_pool = CULPRITS[culprit_lower]["tips"]
        prompt = _make_prompt(culprit, astro=False)
        text = gpt_complete(prompt=prompt, system=None, temperature=0.7, max_tokens=500)
        if not text:
            summary = f"Если завтра что-то пойдёт не так, вините {culprit}! 😉"
            return summary, random.sample(tips_pool, min(3, len(tips_pool)))
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        return _from_lines(culprit, lines, tips_pool)

    astro_keywords = ["луна", "новолуние", "полнолуние", "четверть"]
    is_astro = any(k in culprit_lower for k in astro_keywords)
    if is_astro:
        prompt = _make_prompt(culprit, astro=True)
        text = gpt_complete(prompt=prompt, system=None, temperature=0.7, max_tokens=500)
        if not text:
            summary = f"Если завтра что-то пойдёт не так, вините {culprit}! 😉"
            return summary, random.sample(ASTRO_HEALTH_FALLBACK, 3)
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        return _from_lines(culprit, lines, ASTRO_HEALTH_FALLBACK)

    prompt = _make_prompt(culprit, astro=True)
    text = gpt_complete(prompt=prompt, system=None, temperature=0.7, max_tokens=500)
    if not text:
        summary = f"Если завтра что-то пойдёт не так, вините {culprit}! 😉"
        return summary, random.sample(ASTRO_HEALTH_FALLBACK, 3)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    fallback_pool = ASTRO_HEALTH_FALLBACK + sum((c["tips"] for c in CULPRITS.values()), [])
    return _from_lines(culprit, lines, fallback_pool)
