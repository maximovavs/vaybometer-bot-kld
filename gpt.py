#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
gpt.py

Доработанная логика для блока «Вывод» и «Рекомендации» + общая обёртка gpt_complete():

• Основной порядок провайдеров: OpenAI → Gemini → Groq.
• На 429/недостаток квоты быстро переключаемся к следующему провайдеру.
• gpt_blurb(culprit) сохраняет прежний контракт (summary, tips).

Фолбэк-списки:
  • CULPRITS              — «погодные» факторы с 3–4 советами, если нет ответа от LLM.
  • ASTRO_HEALTH_FALLBACK — универсальные советы по здоровью.
"""

from __future__ import annotations
import os
import random
import logging
from typing import Tuple, List, Optional

# ── setup ──────────────────────────────────────────────────────────────────
log = logging.getLogger(__name__)

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

try:
    import requests  # для Gemini через HTTP API
except Exception:
    requests = None

# ── ключи и порядок провайдеров ───────────────────────────────────────────
OPENAI_KEY = os.getenv("OPENAI_API_KEY") or ""
GEMINI_KEY = os.getenv("GEMINI_API_KEY") or ""
GROQ_KEY   = os.getenv("GROQ_API_KEY") or ""

# порядок провайдеров: OpenAI -> Gemini -> Groq
PROVIDER_ORDER = [p for p in ("openai", "gemini", "groq")]

# актуальные модели Groq, пробуем по порядку (первая доступная сработает)

# (любой из списка, по порядку)
GROQ_MODELS = [
    "llama-3.1-8b-instant",     # быстрый
    "llama-3.1-70b-specdec",    # если доступен в твоём аккаунте
    "llama-3.2-11b-text-preview",
    "llama-3.3-70b-versatile",
    "llama-3.1-70b",
    "llama3-70b-8192",
    "mixtral-8x7b-32768",
    "gemma-7b-it"
]

# ── клиенты ────────────────────────────────────────────────────────────────
def _openai_client() -> Optional["OpenAI"]:
    """
    Клиент OpenAI c отключёнными внутренними ретраями, чтобы при 429 быстро
    переключаться дальше.
    """
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
        return OpenAI(api_key=GROQ_KEY, base_url="https://api.groq.com/openai/v1", timeout=25.0)
    except Exception as e:
        log.warning("Groq client init error: %s", e)
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
    text = ""

    # Сообщения в формате OpenAI
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    # 1) OpenAI
    if "openai" in PROVIDER_ORDER and not text:
        cli = _openai_client()
        if cli:
            try:
                r = cli.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                text = (r.choices[0].message.content or "").strip()
            except Exception as e:
                msg = str(e).lower()
                if any(k in msg for k in ("rate limit", "insufficient_quota", "429")):
                    log.warning("OpenAI error (skip to next): %s", e)
                    text = ""
                else:
                    log.warning("OpenAI error: %s", e)
                    text = ""

    # 2) Gemini (HTTP API)
    if "gemini" in PROVIDER_ORDER and not text and GEMINI_KEY and requests:
        try:
            # Простой и совместимый способ: склеиваем system + prompt
            full_prompt = f"{system.strip()}\n\n{prompt}" if system else prompt
            url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent"
            params = {"key": GEMINI_KEY}
            payload = {
                "contents": [{"parts": [{"text": full_prompt}]}],
                "generationConfig": {
                    "temperature": temperature,
                    "maxOutputTokens": max_tokens
                }
            }
            resp = requests.post(url, params=params, json=payload, timeout=25)
            if resp.status_code == 200:
                data = resp.json()
                # candidates[0].content.parts[*].text
                cand = (data.get("candidates") or [{}])[0]
                parts = ((cand.get("content") or {}).get("parts") or [])
                text = "".join(p.get("text", "") for p in parts).strip()
            else:
                log.warning("Gemini error %s: %s", resp.status_code, resp.text[:300])
        except Exception as e:
            log.warning("Gemini exception: %s", e)

    # 3) Groq (OpenAI-совместимый)
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
                        break
                except Exception as e:
                    msg = str(e).lower()
                    # модель снята/не найдена → пробуем следующую
                    if "decommissioned" in msg or ("model" in msg and "not found" in msg):
                        log.warning("Groq model %s decommissioned/not found, trying next.", mdl)
                        continue
                    # rate limit — тоже пробуем следующую модель
                    if "rate limit" in msg or "429" in msg:
                        log.warning("Groq rate limit on %s, trying next.", mdl)
                        continue
                    log.warning("Groq error on %s: %s", mdl, e)
                    continue

    return text or ""


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
    "🥛 Пейте тёплое молоко с мёдом перед сном",
    "🧘 Делайте лёгкую растяжку утром и вечером",
    "🚶 Прогуливайтесь 20 минут на свежем воздухе",
]

# ── публичная функция для «Вывод/Рекомендации» ────────────────────────────
def gpt_blurb(culprit: str) -> Tuple[str, List[str]]:
    """
    Возвращает (summary: str, tips: List[str]):

    1) Если culprit в CULPRITS → «погодный» фактор:
       • без ответа LLM: summary «Если завтра что-то пойдёт не так, вините {culprit}! 😉»
         + 3 случайных совета из словаря;
       • при ответе LLM: первая строка — summary, следующие 3 — советы.


    2) Если culprit содержит «луна/новолуние/полнолуние/четверть»:
       • без ответа LLM → 3 из ASTRO_HEALTH_FALLBACK,
       • иначе — берём из модели.

    3) Иначе — «общий» фактор: универсальные 3 совета.
    """
    culprit_lower = culprit.lower().strip()

    # подготовка промпта — единообразная для всех случаев
    def _make_prompt(cul: str, astro: bool) -> str:
        if astro:
            return (
                f"Действуй как опытный health coach со знаниями функциональной медицины. "
                f"Напиши одной строкой: «Если завтра что-то пойдёт не так, вините {cul}!». "
                f"После точки — короткий позитив ≤12 слов. "
                f"Затем дай ровно 3 совета (сон, питание, дыхание/лёгкая активность) "
                f"≤12 слов с эмодзи. Ответ — по строкам."
            )
        else:
            return (
                f"Действуй как опытный health coach со знаниями функциональной медицины. "
                f"Напиши одной строкой: «Если завтра что-то пойдёт не так, вините {cul}!». "
                f"После точки — короткий позитив ≤12 слов. "
                f"Затем дай ровно 3 совета по функциональной медицине "
                f"(питание, сон, лёгкая физическая активность) ≤12 слов с эмодзи. "
                f"Ответ — по строкам."
            )

    def _from_lines(cul: str, lines: List[str], fallback_pool: List[str]) -> Tuple[str, List[str]]:
        summary = lines[0] if lines else f"Если завтра что-то пойдёт не так, вините {culprit}! 😉"
        tips = [ln for ln in lines[1:] if ln][:3]
        if len(tips) < 2:
            remaining = [t for t in fallback_pool if t not in tips]
            tips += random.sample(remaining, min(3 - len(tips), len(remaining))) if remaining else []
        return summary, tips[:3]

    # 1) «Погодный» фактор из словаря CULPRITS
    if culprit_lower in CULPRITS:
        tips_pool = CULPRITS[culprit_lower]["tips"]
        prompt = _make_prompt(culprit, astro=False)
        text = gpt_complete(prompt=prompt, system=None, temperature=0.7, max_tokens=500)
        if not text:
            summary = f"Если завтра что-то пойдёт не так, вините {culprit}! 😉"
            return summary, random.sample(tips_pool, min(3, len(tips_pool)))
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        return _from_lines(culprit, lines, tips_pool)

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
    # Здесь как fallback-пул возьмём ASTRO_HEALTH_FALLBACK + все советы из CULPRITS
    fallback_pool = ASTRO_HEALTH_FALLBACK + sum((c["tips"] for c in CULPRITS.values()), [])
    return _from_lines(culprit, lines, fallback_pool)