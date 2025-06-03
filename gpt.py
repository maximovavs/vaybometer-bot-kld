#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
gpt.py

• Расширен словарь CULPRITS: добавлены ключи «жара», «сырость», «полная луна» и т. д.
• Каждый culprit имеет 3–4 совета, из которых выбираются ≤3.
• Если нет OPENAI_KEY — советы берутся из CULPRITS случайным образом.
• При наличии ключа отправляется упрощённый запрос к GPT-4o-mini за одной строкой и 3 советами.
"""

import os
import random
from typing import Tuple, List

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

OPENAI_KEY = os.getenv("OPENAI_API_KEY")

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
            "😴 20-мин дневной отдых",
            "🤸 Нежная утренняя зарядка",
            "🥗 Лёгкий ужин без соли",
        ],
    },
    "шальной ветер": {
        "emoji": "💨",
        "tips": [
            "🧣 Захватите шарф",
            "🚶 Короткая быстрая прогулка",
            "🕶️ Защитите глаза от пыли",
            "🌳 Избегайте открытых пространств",
        ],
    },
    "жара": {
        "emoji": "🔥",
        "tips": [
            "💦 Держите бутылку воды под рукой",
            "🧢 Носите головной убор",
            "🌳 Ищите тень в полдень",
            "❄️ Используйте прохладный компресс",
        ],
    },
    "сырость": {
        "emoji": "💧",
        "tips": [
            "👟 Сменная обувь не помешает",
            "🌂 Компактный зонт в рюкзак",
            "🌬️ Проветривайте помещения",
            "🧥 Лёгкая непромокаемая куртка",
        ],
    },
    "полная луна": {
        "emoji": "🌕",
        "tips": [
            "📝 Запишите яркие идеи",
            "🧘 Мягкая медитация перед сном",
            "🌙 Полюбуйтесь луной без гаджетов",
            "📚 Чтение на свежем воздухе",
        ],
    },
    "мини-парад планет": {
        "emoji": "✨",
        "tips": [
            "🔭 Посмотрите на небо на рассвете",
            "📸 Сделайте фото заката",
            "🤔 Подумайте о бескрайних просторах",
            "🎶 Слушайте спокойную музыку вечером",
        ],
    },
}


def gpt_blurb(culprit: str) -> Tuple[str, List[str]]:
    """
    Возвращает кортеж (summary: str, tips: List[str]).

    • summary — одна строка вида «Если завтра что-то пойдёт не так, вините {culprit}! …»
    • tips — список до 3 коротких советов с эмодзи.
    """
    tips_pool = CULPRITS.get(culprit, {}).get("tips", [])

    # Если нет API-ключа, библиотеки OpenAI или подсказок для данного culprint, берём случайные советы
    if not OPENAI_KEY or not OpenAI or not tips_pool:
        summary = f"Если завтра что-то пойдёт не так, вините {culprit}! 😉"
        return summary, random.sample(tips_pool, min(3, len(tips_pool)))

    # Если ключ есть — формируем упрощённый запрос к GPT-4o-mini
    prompt = (
        f"Действуй как health coach со знаниями функциональной медицины. "
        f"Напиши одной строкой: «Если завтра что-то пойдёт не так, вините {culprit}!». "
        "После точки — короткий позитив ≤12 слов. Затем ровно 3 совета ≤12 слов с эмодзи."
    )

    client = OpenAI(api_key=OPENAI_KEY)
    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.6,
            messages=[{"role": "user", "content": prompt}],
        )
        raw_content = resp.choices[0].message.content.strip()
    except Exception:
        # В случае ошибки GPT-фиговых возвращаем фоллбэк
        summary = f"Если завтра что-то пойдёт не так, вините {culprit}! 😉"
        return summary, random.sample(tips_pool, min(3, len(tips_pool)))

    # Разбиваем ответ на строки и убираем пустые
    lines = [line.strip() for line in raw_content.splitlines() if line.strip()]

    # Первая строка — summary
    summary = lines[0] if lines else f"Если завтра что-то пойдёт не так, вините {culprit}! 😉"

    # Следующие до 3 строк — советы
    tips = lines[1:4]

    # Если GPT вернул меньше 2 советов, дополним случайными из словаря
    if len(tips) < 2:
        remaining = [t for t in tips_pool if t not in tips]
        tips += random.sample(remaining, min(3 - len(tips), len(remaining)))

    return summary, tips