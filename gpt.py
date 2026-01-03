#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
gpt.py

Обёртка для LLM-вызовов и генерации блока «Вывод / Рекомендации».

Цели (по вашему запросу):
- Порядок провайдеров: OpenAI → Gemini → Groq.
- OpenAI пробуем ОДИН раз: если получили 429/insufficient_quota, отключаем OpenAI до конца текущего запуска
  (чтобы не «стучать» повторно по каждому вызову gpt_complete()).
- Gemini: перебираем модели в заданном порядке, но сначала сверяемся со списком доступных моделей,
  чтобы не тратить время на заведомо отсутствующие (404).
- Groq: как раньше — перебор моделей.

Требования окружения:
- OPENAI_API_KEY (опционально)
- GEMINI_API_KEY (опционально)
- GROQ_API_KEY (опционально)

Gemini OpenAI-compat endpoint:
- список моделей:  GET  https://generativelanguage.googleapis.com/v1beta/openai/models?key=...
- чат:            POST https://generativelanguage.googleapis.com/v1beta/openai/chat/completions?key=...

Настройки:
- GEMINI_MODELS (опционально): список через запятую, например:
  "gemini-3-flash,gemini-3-pro,gemini-2.5-flash,gemini-3-flash-preview"
- GROQ_MODELS (опционально): список через запятую, если хотите переопределить дефолт.
"""

from __future__ import annotations

import logging
import os
import random
from typing import Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

try:
    from openai import OpenAI  # type: ignore
except Exception:
    OpenAI = None  # type: ignore

try:
    import requests  # type: ignore
except Exception:
    requests = None  # type: ignore

# ── ключи ───────────────────────────────────────────────────────────────────
OPENAI_KEY = os.getenv("OPENAI_API_KEY") or ""
GEMINI_KEY = os.getenv("GEMINI_API_KEY") or ""
GROQ_KEY = os.getenv("GROQ_API_KEY") or ""

# ── настройки Gemini (по вашему порядку) ────────────────────────────────────
_DEFAULT_GEMINI_PREF = [
    "gemini-3-flash",
    "gemini-3-pro",
    "gemini-2.5-flash",
    "gemini-3-flash-preview",
]
_GEMINI_PREF = [
    m.strip() for m in (os.getenv("GEMINI_MODELS") or ",".join(_DEFAULT_GEMINI_PREF)).split(",")
    if m.strip()
]

# ── модели Groq ─────────────────────────────────────────────────────────────
_DEFAULT_GROQ_MODELS = [
    "moonshotai/kimi-k2-instruct-0905",
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
    "gemma2-9b-it",
    "qwen/qwen3-32b",
    "deepseek-r1-distill-llama-70b",
]
_GROQ_MODELS = [
    m.strip() for m in (os.getenv("GROQ_MODELS") or ",".join(_DEFAULT_GROQ_MODELS)).split(",")
    if m.strip()
]

# ── провайдеры (фиксированный порядок) ──────────────────────────────────────
PROVIDER_ORDER = [p for p in ("openai", "gemini", "groq")]

# ── «один раз» / кэш доступности в рамках запуска ──────────────────────────
_OPENAI_DISABLED = False
_OPENAI_DISABLED_REASON = ""

_GEMINI_DISABLED = False
_GEMINI_DISABLED_REASON = ""

_GEMINI_AVAILABLE: Optional[set[str]] = None          # нормализованные id (без "models/")
_GEMINI_MODELS_FETCHED = False
_GEMINI_UNSUPPORTED: set[str] = set()                 # модели, по которым получили 404/unsupported


# ───────────────────────────── helpers ──────────────────────────────────────
def _looks_like_quota_or_429(err_text: str) -> bool:
    t = (err_text or "").lower()
    return any(k in t for k in ("insufficient_quota", "rate limit", "429", "quota"))


def _looks_like_model_not_found(err_text: str) -> bool:
    t = (err_text or "").lower()
    return ("not found" in t) or ("model" in t and "not" in t and "found" in t) or ("404" in t)


def _openai_client() -> Optional["OpenAI"]:
    """
    Клиент OpenAI с отключёнными внутренними ретраями:
    при 429/insufficient_quota быстро переключаемся дальше.
    """
    if _OPENAI_DISABLED:
        return None
    if not OPENAI_KEY or not OpenAI:
        return None
    try:
        return OpenAI(api_key=OPENAI_KEY, timeout=20.0, max_retries=0)
    except Exception as e:
        log.warning("OpenAI client init error: %s", e)
        return None


def _groq_client() -> Optional["OpenAI"]:
    """
    OpenAI-совместимый клиент для Groq через base_url.
    """
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


def _gemini_base_url() -> str:
    # OpenAI-compat у Gemini
    return "https://generativelanguage.googleapis.com/v1beta/openai"


def _norm_gemini_model_id(mid: str) -> str:
    """
    В списке моделей Gemini иногда встречается "models/<name>" — нормализуем к "<name>".
    Также на всякий случай берём последний сегмент пути.
    """
    mid = (mid or "").strip()
    if not mid:
        return mid
    if "/" in mid:
        mid = mid.split("/")[-1].strip()
    return mid


def _gemini_list_models() -> set[str]:
    """
    Получает список доступных моделей Gemini (OpenAI-compat) и кеширует его.
    Возвращает множество нормализованных id.
    """
    global _GEMINI_AVAILABLE, _GEMINI_MODELS_FETCHED, _GEMINI_DISABLED, _GEMINI_DISABLED_REASON

    if _GEMINI_AVAILABLE is not None:
        return _GEMINI_AVAILABLE

    _GEMINI_AVAILABLE = set()
    _GEMINI_MODELS_FETCHED = True

    if _GEMINI_DISABLED:
        return _GEMINI_AVAILABLE
    if not GEMINI_KEY or not requests:
        return _GEMINI_AVAILABLE

    try:
        url = f"{_gemini_base_url()}/models"
        resp = requests.get(url, params={"key": GEMINI_KEY}, timeout=20)
        if resp.status_code != 200:
            body = (resp.text or "")[:300].replace("\n", " ")
            log.warning("Gemini models.list() failed (%s): %s", resp.status_code, body)
            # если ключ/доступа нет — отключаем Gemini до конца запуска
            if resp.status_code in (401, 403):
                _GEMINI_DISABLED = True
                _GEMINI_DISABLED_REASON = f"http {resp.status_code}"
            return _GEMINI_AVAILABLE

        data = resp.json() or {}
        models = data.get("data") or data.get("models") or []
        # OpenAI-compat может возвращать: {"data":[{"id":"models/gemini-2.5-flash", ...}, ...]}
        for m in models:
            mid = _norm_gemini_model_id(str(m.get("id") or m.get("name") or ""))
            if mid:
                _GEMINI_AVAILABLE.add(mid)

        log.info("Gemini models.list(): %s models", len(_GEMINI_AVAILABLE))
        return _GEMINI_AVAILABLE

    except Exception as e:
        log.warning("Gemini models.list() exception: %s", e)
        return _GEMINI_AVAILABLE


def _gemini_chat(
    messages: List[dict],
    model: str,
    temperature: float,
    max_tokens: int,
) -> str:
    """
    OpenAI-compat chat/completions для Gemini.
    Возвращает текст или "".
    """
    if _GEMINI_DISABLED or not GEMINI_KEY or not requests:
        return ""

    url = f"{_gemini_base_url()}/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    resp = requests.post(url, params={"key": GEMINI_KEY}, json=payload, timeout=25)

    if resp.status_code == 200:
        data = resp.json() or {}
        choices = data.get("choices") or []
        if choices:
            msg = (choices[0].get("message") or {}).get("content") or ""
            return str(msg).strip()
        return ""

    # Обработка ошибок
    body = (resp.text or "")[:300].replace("\n", " ")
    if resp.status_code == 404 or "not found" in body.lower():
        raise RuntimeError(f"MODEL_NOT_FOUND: {model}: http {resp.status_code} {body}")
    if resp.status_code in (401, 403):
        raise RuntimeError(f"AUTH_ERROR: http {resp.status_code} {body}")
    if resp.status_code == 429:
        raise RuntimeError(f"RATE_LIMIT: http 429 {body}")

    raise RuntimeError(f"Gemini http {resp.status_code}: {body}")


# ───────────────────────────── main wrapper ────────────────────────────────
def gpt_complete(
    prompt: str,
    system: Optional[str] = None,
    temperature: float = 0.7,
    max_tokens: int = 600,
) -> str:
    """
    Универсальный вызов LLM. Пробует по очереди: OpenAI → Gemini → Groq.
    Возвращает text или "" (если всё недоступно).
    """
    global _OPENAI_DISABLED, _OPENAI_DISABLED_REASON, _GEMINI_DISABLED, _GEMINI_DISABLED_REASON

    # Сообщения в формате OpenAI
    messages: List[dict] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    text = ""

    # 1) OpenAI (ровно один «первый удар»; после 429/квоты отключаем до конца запуска)
    if "openai" in PROVIDER_ORDER and not text and not _OPENAI_DISABLED:
        cli = _openai_client()
        if cli:
            try:
                r = cli.chat.completions.create(
                    model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                text = (r.choices[0].message.content or "").strip()
                if text:
                    return text
            except Exception as e:
                msg = str(e)
                if _looks_like_quota_or_429(msg):
                    # ключевая правка: больше не пытаемся OpenAI в этом запуске
                    _OPENAI_DISABLED = True
                    _OPENAI_DISABLED_REASON = msg[:200]
                    log.warning("OpenAI quota/rate-limit → disable for this run: %s", msg)
                else:
                    log.warning("OpenAI error: %s", e)

    # 2) Gemini (перебор моделей; избегаем повторных 404 и учитываем models.list)
    if "gemini" in PROVIDER_ORDER and not text and not _GEMINI_DISABLED and GEMINI_KEY and requests:
        # узнаём доступные модели (если не получилось — всё равно попробуем как есть, но с кешем 404)
        avail = _gemini_list_models()

        models_to_try = _GEMINI_PREF[:]
        # если список доступных непустой — фильтруем
        if avail:
            models_to_try = [m for m in models_to_try if _norm_gemini_model_id(m) in avail] or models_to_try

        for mdl in models_to_try:
            mdl_norm = _norm_gemini_model_id(mdl)
            if mdl_norm in _GEMINI_UNSUPPORTED:
                continue

            try:
                out = _gemini_chat(messages, mdl_norm, temperature, max_tokens)
                if out:
                    log.info("LLM: Gemini ok (model=%s)", mdl_norm)
                    return out
                log.warning("Gemini: empty response (model=%s)", mdl_norm)
            except Exception as e:
                em = str(e)
                if em.startswith("MODEL_NOT_FOUND") or _looks_like_model_not_found(em):
                    _GEMINI_UNSUPPORTED.add(mdl_norm)
                    log.warning("Gemini model %s not found/unsupported, trying next.", mdl_norm)
                    continue
                if em.startswith("AUTH_ERROR"):
                    _GEMINI_DISABLED = True
                    _GEMINI_DISABLED_REASON = em[:200]
                    log.warning("Gemini auth error → disable for this run: %s", em)
                    break
                if em.startswith("RATE_LIMIT") or _looks_like_quota_or_429(em):
                    log.warning("Gemini rate-limit/quota on %s → switch to next provider.", mdl_norm)
                    break

                log.warning("Gemini error on %s: %s", mdl_norm, e)
                continue

    # 3) Groq (как раньше, перебор моделей)
    if "groq" in PROVIDER_ORDER and not text:
        cli = _groq_client()
        if cli:
            for mdl in _GROQ_MODELS:
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
                    msg = str(e).lower()
                    if "decommissioned" in msg or ("model" in msg and "not found" in msg):
                        log.warning("Groq model %s decommissioned/not found, trying next.", mdl)
                        continue
                    if "rate limit" in msg or "429" in msg:
                        log.warning("Groq rate limit on %s, trying next.", mdl)
                        continue
                    log.warning("Groq error on %s: %s", mdl, e)
                    continue

    return ""


# ── словари фолбэков ──────────────────────────────────────────────────────
CULPRITS: Dict[str, Dict[str, object]] = {
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


# ── публичная функция для «Вывод/Рекомендации» ────────────────────────────
def gpt_blurb(culprit: str) -> Tuple[str, List[str]]:
    """
    Возвращает (summary: str, tips: List[str]) — контракт как раньше.
    """
    culprit_lower = culprit.lower().strip()

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

    # 1) «Погодный» фактор из словаря CULPRITS
    if culprit_lower in CULPRITS:
        tips_pool = CULPRITS[culprit_lower]["tips"]  # type: ignore[index]
        prompt = _make_prompt(culprit, astro=False)
        text = gpt_complete(prompt=prompt, system=None, temperature=0.7, max_tokens=500)
        if not text:
            summary = f"Если завтра что-то пойдёт не так, вините {culprit}! 😉"
            return summary, random.sample(list(tips_pool), min(3, len(tips_pool)))  # type: ignore[arg-type]
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        return _from_lines(culprit, lines, list(tips_pool))  # type: ignore[arg-type]

    # 2) «Астрофактор»
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

    # 3) Общий случай
    prompt = _make_prompt(culprit, astro=True)
    text = gpt_complete(prompt=prompt, system=None, temperature=0.7, max_tokens=500)
    if not text:
        summary = f"Если завтра что-то пойдёт не так, вините {culprit}! 😉"
        return summary, random.sample(ASTRO_HEALTH_FALLBACK, 3)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    fallback_pool = ASTRO_HEALTH_FALLBACK + sum((c["tips"] for c in CULPRITS.values()), [])  # type: ignore[list-item]
    return _from_lines(culprit, lines, fallback_pool)
