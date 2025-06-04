#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
gpt.py

Доработанная логика для блока «Вывод» и «Рекомендации»:

• Если culprit — «погодный» фактор (ключ из словаря CULPRITS), генерируем запрос к GPT-4o-mini
  (или берём фолбэк из CULPRITS) с советами по функциональной медицине в контексте этого фактора.

• Если culprit содержит лексемы, связанные с Лунными фазами/астрофакторами
  (например, «Луна», «новолуние», «полнолуние», «четверть» и т. д.), формируем запрос к GPT-4o-mini
  за общей health-coach рекомендацией (сон, питание, дыхательные практики) в контексте влияния Луны,
  или возвращаем фоллбэк ASTRO_HEALTH_FALLBACK при отсутствии API-ключа.

• Во всех остальных случаях считаем culprit «общим» faktor’ом (может быть текстом от pick_culprit)
  и запрашиваем у GPT-4o-mini три универсальных health-coach совета с упоминанием culprit в summary,
  либо возвращаем фоллбэк ASTRO_HEALTH_FALLBACK при отсутствии API-ключа.

Фолбэк-списки:
  • CULPRITS             — «погодные» факторы с 3–4 советами, если нет API-ключа.
  • ASTRO_HEALTH_FALLBACK — универсальные советы по здоровью на случай, когда виновата Луна или нет API-ключа.
"""

import os
import random
from typing import Tuple, List

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

OPENAI_KEY = os.getenv("OPENAI_API_KEY")

# Словарь «погодные / прочие» факторы → эмоджи и фолбэк-советы
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

# Универсальные health-coach рекомендации для астрофакторов (если API-ключ отсутствует)
ASTRO_HEALTH_FALLBACK: List[str] = [
    "💤 Соблюдайте режим сна: ложитесь не позже 23:00",
    "🥦 Включите в рацион свежие овощи и зелень",
    "🥛 Пейте тёплое молоко с мёдом перед сном",
    "🧘 Делайте лёгкую растяжку утром и вечером",
    "🚶 Прогуливайтесь 20 минут на свежем воздухе",
]


def gpt_blurb(culprit: str) -> Tuple[str, List[str]]:
    """
    Возвращает (summary: str, tips: List[str]):

    1) Если culprit (строка) в списке ключей CULPRITS → «погодный» фактор:
       • без API-ключа: summary = «Если завтра что-то пойдёт не так, вините {culprit}! 😉»,
         tips = случайные 3 из CULPRITS[culprit]["tips"].
       • с API-ключом: упрощённый запрос к GPT-4o-mini, где в summary упоминаем culprit,
         а затем три совета по функциональной медицине в контексте данного фактора.

    2) Если culprit содержит слова, связанные с луной/астрофакторами:
       («луна», «новолуние», «полнолуние», «четверть»), независимо от регистра:
       • без API-ключа: summary = «Если завтра … вините {culprit}! 😉»,
         tips = случайные 3 из ASTRO_HEALTH_FALLBACK.
       • с API-ключом: запрос к GPT-4o-mini: summary как «… вините {culprit}!», а советы —
         три health-coach рекомендации (сон, питание, дыхание и т. д.).

    3) Для всех прочих случаев (непредвиденный culprit):
       • без API-ключа: summary = «Если завтра … вините {culprit}! 😉»,
         tips = случайные 3 из ASTRO_HEALTH_FALLBACK.
       • с API-ключом: аналогично пункту 2: просим GPT-4o-mini вывести summary + три универсальных совета.

    В итоге даже при «астрофакторе» (Луна) всегда возвращаются трёхстрочные
    рекомендации от health-coach’а.
    """
    culprit_lower = culprit.lower().strip()

    # 1) «Погодный» фактор из словаря CULPRITS
    if culprit_lower in CULPRITS:
        tips_pool = CULPRITS[culprit_lower]["tips"]
        # Если нет ключа или библиотека OpenAI — фоллбэк из CULPRITS
        if not OPENAI_KEY or not OpenAI or not tips_pool:
            summary = f"Если завтра что-то пойдёт не так, вините {culprit}! 😉"
            return summary, random.sample(tips_pool, min(3, len(tips_pool)))

        # Формируем промпт для GPT-4o-mini
        prompt = (
            f"Действуй как опытный health coach со знаниями функциональной медицины. "
            f"Напиши одной строкой: «Если завтра что-то пойдёт не так, вините {culprit}!». "
            f"После точки — короткий позитив ≤12 слов. "
            f"Затем дай ровно 3 совета по функциональной медицине "
            f"(питание, сон, лёгкая физическая активность) ≤12 слов с эмодзи."
        )

        client = OpenAI(api_key=OPENAI_KEY)
        try:
            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                temperature=0.7,
                messages=[{"role": "user", "content": prompt}],
            )
            raw_lines = resp.choices[0].message.content.strip().splitlines()
            lines = [ln.strip() for ln in raw_lines if ln.strip()]
        except Exception:
            # В случае ошибки GPT → фоллбэк из CULPRITS
            summary = f"Если завтра что-то пойдёт не так, вините {culprit}! 😉"
            return summary, random.sample(tips_pool, min(3, len(tips_pool)))

        # Первая строка — summary
        summary = lines[0] if lines else f"Если завтра что-то пойдёт не так, вините {culprit}! 😉"
        # Следующие строки (до 3) — советы
        tips = lines[1:4]
        if len(tips) < 2:
            remaining = [t for t in tips_pool if t not in tips]
            tips += random.sample(remaining, min(3 - len(tips), len(remaining)))
        return summary, tips

    # 2) «Астрофактор» (Луна / Новолуние / Полнолуние / Четверть)
    astro_keywords = ["луна", "новолуние", "полнолуние", "четверть"]
    if any(keyword in culprit_lower for keyword in astro_keywords):
        # Фоллбэк без API-ключа
        if not OPENAI_KEY or not OpenAI:
            summary = f"Если завтра что-то пойдёт не так, вините {culprit}! 😉"
            return summary, random.sample(ASTRO_HEALTH_FALLBACK, 3)

        # Запрос к GPT-4o-mini за health-coach советами с упоминанием culprit
        prompt = (
            f"Действуй как опытный health coach со знаниями функциональной медицины. "
            f"Напиши одной строкой: «Если завтра что-то пойдёт не так, вините {culprit}!». "
            f"После точки — короткий позитив ≤12 слов. "
            f"Затем дай ровно 3 совета по функциональной медицине "
            f"(сон, питание, дыхательные практики, лёгкая физическая активность) ≤12 слов с эмодзи."
        )

        client = OpenAI(api_key=OPENAI_KEY)
        try:
            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                temperature=0.7,
                messages=[{"role": "user", "content": prompt}],
            )
            raw_lines = resp.choices[0].message.content.strip().splitlines()
            lines = [ln.strip() for ln in raw_lines if ln.strip()]
        except Exception:
            summary = f"Если завтра что-то пойдёт не так, вините {culprit}! 😉"
            return summary, random.sample(ASTRO_HEALTH_FALLBACK, 3)

        summary = lines[0] if lines else f"Если завтра что-то пойдёт не так, вините {culprit}! 😉"
        tips = lines[1:4]
        if len(tips) < 2:
            remaining = [t for t in ASTRO_HEALTH_FALLBACK if t not in tips]
            tips += random.sample(remaining, min(3 - len(tips), len(remaining)))
        return summary, tips

    # 3) Любой другой «culprit» (непредвиденный)
    #    Действуем как в случае астрофактора: общие health-coach советы
    if not OPENAI_KEY or not OpenAI:
        summary = f"Если завтра что-то пойдёт не так, вините {culprit}! 😉"
        return summary, random.sample(ASTRO_HEALTH_FALLBACK, 3)

    prompt = (
        f"Действуй как опытный health coach со знаниями функциональной медицины. "
        f"Напиши одной строкой: «Если завтра что-то пойдёт не так, вините {culprit}!». "
        f"После точки — короткий позитив ≤12 слов. "
        f"Затем дай ровно 3 совета по функциональной медицине "
        f"(сон, питание, дыхательные практики, лёгкая физическая активность) ≤12 слов с эмодзи."
    )
    client = OpenAI(api_key=OPENAI_KEY)
    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.7,
            messages=[{"role": "user", "content": prompt}],
        )
        raw_lines = resp.choices[0].message.content.strip().splitlines()
        lines = [ln.strip() for ln in raw_lines if ln.strip()]
    except Exception:
        summary = f"Если завтра что-то пойдёт не так, вините {culprit}! 😉"
        return summary, random.sample(ASTRO_HEALTH_FALLBACK, 3)

    summary = lines[0] if lines else f"Если завтра что-то пойдёт не так, вините {culprit}! 😉"
    tips = lines[1:4]
    if len(tips) < 2:
        fallback_pool = ASTRO_HEALTH_FALLBACK + sum((c["tips"] for c in CULPRITS.values()), [])
        remaining = [t for t in fallback_pool if t not in tips]
        tips += random.sample(remaining, min(3 - len(tips), len(remaining)))
    return summary, tips