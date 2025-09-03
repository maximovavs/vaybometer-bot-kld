#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
gpt.py — советы/вывод с LLM и мягким фолбэком.

Поведение сохранено:
- Внешняя функция gpt_blurb(culprit) -> (summary, tips)
- При недоступности LLM остаются прежние фолбэки.

Новое:
- Внутренний фолбэк-порядок провайдеров: OpenAI -> Gemini -> Groq
- ENV-модели переопределяемы: OPENAI_MODEL / GEMINI_MODEL / GROQ_MODEL
"""

from __future__ import annotations
import os
import random
import logging
from typing import Tuple, List, Optional

# ── провайдеры (ленивые импорты ниже) ──────────────────────────────────────
try:
    from openai import OpenAI  # type: ignore
except Exception:
    OpenAI = None  # noqa: N816

try:
    import google.generativeai as genai  # type: ignore
except Exception:
    genai = None  # type: ignore

try:
    from groq import Groq  # type: ignore
except Exception:
    Groq = None  # type: ignore

# ── ключи и модели ─────────────────────────────────────────────────────────
OPENAI_KEY  = os.getenv("OPENAI_API_KEY", "")
GEMINI_KEY  = os.getenv("GEMINI_API_KEY", "")
GROQ_KEY    = os.getenv("GROQ_API_KEY", "")

OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
GROQ_MODEL   = os.getenv("GROQ_MODEL",   "llama-3.1-70b-versatile")

# ── логирование (тихо по умолчанию) ───────────────────────────────────────
log = logging.getLogger(__name__)
if not log.handlers:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ── фолбэки как у тебя раньше ─────────────────────────────────────────────
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
    "🥛 Тёплый напиток вечером — без кофеина",
    "🧘 Лёгкая растяжка утром и вечером",
    "🚶 Прогулка 20 мин на свежем воздухе",
]

ASTRO_KEYWORDS = ["луна", "новолуние", "полнолуние", "четверть"]


# ── утилиты LLM ───────────────────────────────────────────────────────────

def _clean_lines(s: str) -> List[str]:
    """Чистим ответ LLM в список строк (убираем нумерацию и пустоты)."""
    raw = [ln.strip() for ln in (s or "").splitlines()]
    out: List[str] = []
    for ln in raw:
        if not ln:
            continue
        # убираем возможные лидеры "1. ", "- " и т.п.
        out.append(ln.lstrip("•*-–—0123456789. ").strip())
    return out


def _try_openai(prompt: str, temperature: float, max_tokens: int) -> Optional[List[str]]:
    if not (OPENAI_KEY and OpenAI):
        return None
    try:
        client = OpenAI(api_key=OPENAI_KEY)
        resp = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=25,
        )
        text = (resp.choices[0].message.content or "").strip()
        lines = _clean_lines(text)
        if lines:
            log.info("LLM provider: OpenAI (%s)", OPENAI_MODEL)
            return lines
    except Exception as e:
        log.warning("OpenAI error: %s", e)
    return None


def _try_gemini(prompt: str, temperature: float, max_tokens: int) -> Optional[List[str]]:
    if not (GEMINI_KEY and genai):
        return None
    try:
        genai.configure(api_key=GEMINI_KEY)
        model = genai.GenerativeModel(GEMINI_MODEL)
        resp = model.generate_content(
            prompt,
            generation_config={
                "temperature": float(temperature),
                "max_output_tokens": int(max_tokens),
            }
        )
        # в SDK ответ в resp.text
        text = (getattr(resp, "text", None) or "").strip()
        lines = _clean_lines(text)
        if lines:
            log.info("LLM provider: Gemini (%s)", GEMINI_MODEL)
            return lines
    except Exception as e:
        log.warning("Gemini error: %s", e)
    return None


def _try_groq(prompt: str, temperature: float, max_tokens: int) -> Optional[List[str]]:
    if not (GROQ_KEY and Groq):
        return None
    try:
        client = Groq(api_key=GROQ_KEY)
        resp = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=25,
        )
        text = (resp.choices[0].message.content or "").strip()
        lines = _clean_lines(text)
        if lines:
            log.info("LLM provider: Groq (%s)", GROQ_MODEL)
            return lines
    except Exception as e:
        log.warning("Groq error: %s", e)
    return None


def __llm_complete_with_fallback(prompt: str,
                                 temperature: float = 0.7,
                                 max_tokens: int = 600) -> Optional[List[str]]:
    """
    Единственная точка входа к моделям.
    Порядок: OpenAI -> Gemini -> Groq. Возвращает список строк или None.
    """
    for fn in (_try_openai, _try_gemini, _try_groq):
        lines = fn(prompt, temperature, max_tokens)
        if lines:
            return lines
    return None


# Опциональная утилита (может быть полезна где-то ещё)
def gpt_complete(prompt: str,
                 temperature: float = 0.7,
                 max_tokens: int = 600) -> str:
    lines = __llm_complete_with_fallback(prompt, temperature, max_tokens)
    return "\n".join(lines) if lines else ""


def _extract_summary_and_tips(lines: List[str],
                              culprit: str,
                              fallback_pool: List[str]) -> Tuple[str, List[str]]:
    """
    Первая строка — summary (если нет, генерим локальный).
    Следующие 3 строки — советы (дополняем из fallback_pool).
    """
    default_summary = f"Если завтра что-то пойдёт не так, вините {culprit}! 😉"

    if not lines:
        return default_summary, random.sample(fallback_pool, min(3, len(fallback_pool)))

    summary = lines[0].strip() or default_summary
    tips = [ln for ln in lines[1:] if ln][:3]

    if len(tips) < 3:
        remain = [t for t in fallback_pool if t not in tips]
        tips += random.sample(remain, min(3 - len(tips), len(remain)))
        if len(tips) < 3 and fallback_pool:
            tips += random.sample(fallback_pool, 3 - len(tips))

    # на случай редкого «мусора»
    tips = [t for t in tips if t and t != summary][:3]
    if len(tips) < 3 and fallback_pool:
        tips += random.sample(fallback_pool, min(3 - len(tips), len(fallback_pool)))
    return summary, tips[:3]


# ── публичная функция как раньше ──────────────────────────────────────────
def gpt_blurb(culprit: str) -> Tuple[str, List[str]]:
    """
    Возвращает (summary: str, tips: List[str]) с сохранением прежней логики:

    - Если culprit — «погодный» из CULPRITS -> сначала LLM, если не ответил -> фолбэк CULPRITS.
    - Если culprit содержит Луну/фазы -> сначала LLM, если не ответил -> ASTRO_HEALTH_FALLBACK.
    - Иначе -> сначала LLM, если не ответил -> ASTRO_HEALTH_FALLBACK.
    """
    culprit_lower = culprit.lower().strip()

    # 1) Погодный фактор
    if culprit_lower in CULPRITS:
        tips_pool = CULPRITS[culprit_lower]["tips"]

        prompt = (
            f"Действуй как опытный health coach со знаниями функциональной медицины. "
            f"Сначала одной строкой напиши: «Если завтра что-то пойдёт не так, вините {culprit}!». "
            f"После точки — короткий позитив (≤12 слов). "
            f"Затем выведи ровно 3 отдельные строки советов (≤12 слов) с эмодзи, "
            f"учитывая влияние фактора «{culprit}» на самочувствие."
        )
        lines = __llm_complete_with_fallback(prompt, temperature=0.7, max_tokens=600)
        if not lines:
            summary = f"Если завтра что-то пойдёт не так, вините {culprit}! 😉"
            return summary, random.sample(tips_pool, min(3, len(tips_pool)))
        return _extract_summary_and_tips(lines, culprit, tips_pool)

    # 2) Астро-факторы (Луна/фазы)
    if any(k in culprit_lower for k in ASTRO_KEYWORDS):
        prompt = (
            f"Действуй как опытный health coach. "
            f"Сначала одной строкой напиши: «Если завтра что-то пойдёт не так, вините {culprit}!». "
            f"После точки — короткий позитив (≤12 слов). "
            f"Затем выведи ровно 3 строки советов (сон, питание, дыхание, лёгкая активность) "
            f"≤12 слов с эмодзи, в контексте влияния Луны/фазы."
        )
        lines = __llm_complete_with_fallback(prompt, temperature=0.7, max_tokens=600)
        if not lines:
            summary = f"Если завтра что-то пойдёт не так, вините {culprit}! 😉"
            return summary, random.sample(ASTRO_HEALTH_FALLBACK, 3)
        return _extract_summary_and_tips(lines, culprit, ASTRO_HEALTH_FALLBACK)

    # 3) Прочие факторы
    prompt = (
        f"Действуй как опытный health coach. "
        f"Сначала одной строкой напиши: «Если завтра что-то пойдёт не так, вините {culprit}!». "
        f"После точки — короткий позитив (≤12 слов). "
        f"Затем выведи ровно 3 строки советов (сон, питание, дыхание, лёгкая активность) "
        f"≤12 слов с эмодзи — универсальные рекомендации."
    )
    lines = __llm_complete_with_fallback(prompt, temperature=0.7, max_tokens=600)
    if not lines:
        summary = f"Если завтра что-то пойдёт не так, вините {culprit}! 😉"
        # для разнообразия — объединённый пул
        pool = ASTRO_HEALTH_FALLBACK + sum((c["tips"] for c in CULPRITS.values()), [])
        return summary, random.sample(pool, 3) if len(pool) >= 3 else pool[:3]
    return _extract_summary_and_tips(lines, culprit, ASTRO_HEALTH_FALLBACK)
