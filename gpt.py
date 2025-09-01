# gpt.py — shim под Gemini вместо OpenAI
from __future__ import annotations
import os
from typing import List, Tuple

# Ленивая инициализация клиента
_GEM_CLIENT = None
_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")  # быстрый и дешёвый; можно "gemini-1.5-pro"

SYSTEM = (
    "Ты пишешь очень короткие, дружелюбные рекомендации по самочувствию и быту. "
    "Никакой диагностики и медицинских советов. Без тревожных формулировок. "
    "Пиши по-русски, просто и позитивно."
)

def _client():
    global _GEM_CLIENT
    if _GEM_CLIENT is not None:
        return _GEM_CLIENT
    import google.generativeai as genai
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set")
    genai.configure(api_key=api_key)
    _GEM_CLIENT = genai.GenerativeModel(
        model_name=_MODEL,
        system_instruction=SYSTEM,
        generation_config={
            "temperature": 0.6,
            "top_p": 0.9,
            "top_k": 40,
            "max_output_tokens": 512,
        },
        safety_settings={  # мягкие дефолты
            "HARASSMENT": "BLOCK_NONE",
            "HATE_SPEECH": "BLOCK_NONE",
            "SEXUAL_CONTENT": "BLOCK_NONE",
            "DANGEROUS_CONTENT": "BLOCK_NONE",
        },
    )
    return _GEM_CLIENT

def _parse_list(text: str) -> List[str]:
    # извлекаем 3–5 буллетов из ответа (строки, начинающиеся с -/*/•/—/1.)
    lines = []
    for raw in (text or "").splitlines():
        s = raw.strip().lstrip("-•—*0123456789. ").strip()
        if len(s) >= 2:
            lines.append(s)
    # fallback: если не распознался список — просто нарежем по предложениям
    if not lines:
        parts = [p.strip() for p in text.split("•") if p.strip()] or [p.strip() for p in text.split(".") if p.strip()]
        lines = parts
    # вернём до 5 коротких
    out = []
    for s in lines:
        if len(out) >= 5: break
        if len(s) > 140: s = s[:137] + "…"
        out.append(s)
    return out or ["Больше воды, меньше стресса, нормальный сон"]

def gpt_blurb(culprit: str) -> Tuple[str, List[str]]:
    """
    Совместимый интерфейс:
      return title: str, tips: List[str]
    """
    prompt = (
        f"Короткое резюме (одной строкой) и 3–5 дружелюбных советов "
        f"на день для читателя. Контекст: возможное влияние — «{culprit}». "
        "Не упоминай диагнозы, препараты, сложные практики. "
        "Верни сначала одну строку-резюме, затем список буллетов."
    )
    try:
        model = _client()
        resp = model.generate_content(prompt)
        text = (resp.text or "").strip()
    except Exception as e:
        # надёжный фоллбэк, чтобы бот не падал
        title = "Спокойный настрой и щадящий режим"
        tips = ["Пейте воду", "Прогуляйтесь 15–20 минут", "Ложитесь спать вовремя"]
        return title, tips

    # разделим первый абзац как заголовок, остальное — буллеты
    parts = [p.strip() for p in text.split("\n\n") if p.strip()]
    title = parts[0][:120] if parts else "Мягкий режим дня"
    tips_text = "\n".join(parts[1:]) if len(parts) > 1 else text
    tips = _parse_list(tips_text)
    return title, tips
