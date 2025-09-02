#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gen_lunar_calendar.py

Генерация помесячного лунного календаря в lunar_calendar.json.

Сохраняет по каждому дню:
- phase_name   : "Растущая Луна" / "Полнолуние" и т.д.
- phase        : "🌔 Растущая Луна" (эмодзи + краткое имя)
- sign         : знак зодиака (ru)
- long_desc    : ДВА предложения (сначала Gemini → доспрос → фолбэк)
- void_of_course: {"start": ISO, "end": ISO} — если VoC стартует в этот день
- favorable_days: блок с «Благоприятные/…» (один и тот же объект для всех дней)

Примечание по VoC:
send_monthly_calendar.py собирает единый список, проходя по дням и читая
void_of_course. Мы кладём запись в тот день, когда VoC НАЧИНАЕТСЯ.
"""

from __future__ import annotations
import os
import re
import json
import time
import calendar
from dataclasses import dataclass
from typing import Dict, Any, List, Optional

import pendulum

# --- внешние модули проекта (оставляем как есть) ---------------------------
try:
    import yaml  # для monthly_calendar.yml
except Exception:
    yaml = None

try:
    import lunar as LUN  # твой модуль с фазами/знаками/VoC
except Exception:
    LUN = None

# --- Настройки --------------------------------------------------------------

TZ = pendulum.timezone(os.getenv("LUNAR_TZ", "Asia/Nicosia"))
OUT_FILE = os.getenv("LUNAR_OUT", "lunar_calendar.json")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
GEMINI_ROUND_PAUSE = float(os.getenv("GEMINI_ROUND_PAUSE", "1.5"))
GEMINI_RETRY_ATTEMPTS = int(os.getenv("GEMINI_RETRY_ATTEMPTS", "3"))

# --- Русские месяцы в ПРЕДЛОЖНОМ падеже (для фолбэков) ---------------------

MONTH_IN_RU_PREP = {
    1: "январе", 2: "феврале", 3: "марте", 4: "апреле",
    5: "мае", 6: "июне", 7: "июле", 8: "августе",
    9: "сентябре", 10: "октябре", 11: "ноябре", 12: "декабре",
}

# --- Утилиты совместимости с lunar.py ---------------------------------------

def moon_phase_name(dt: pendulum.DateTime) -> str:
    for name in ("phase_name", "moon_phase_name", "get_moon_phase_name"):
        if LUN and hasattr(LUN, name):
            try:
                return str(getattr(LUN, name)(dt))
            except Exception:
                pass
    # безопасный дефолт
    return "Фаза Луны"

def moon_phase_emoji(dt: pendulum.DateTime) -> str:
    for name in ("phase_emoji", "moon_phase_emoji", "get_moon_phase_emoji"):
        if LUN and hasattr(LUN, name):
            try:
                s = str(getattr(LUN, name)(dt))
                if s.strip():
                    return s.strip()
            except Exception:
                pass
    return "🌙"

def moon_sign(dt: pendulum.DateTime, tz: pendulum.Timezone) -> str:
    for name in ("sign_name", "moon_sign", "zodiac_sign", "get_sign"):
        if LUN and hasattr(LUN, name):
            try:
                return str(getattr(LUN, name)(dt, tz))
            except Exception:
                pass
    return ""

def voc_for_day(dt: pendulum.DateTime, tz: pendulum.Timezone) -> Optional[Dict[str, str]]:
    """
    Возвращает {"start": ISO, "end": ISO} если в ЭТОТ день начинается VoC.
    """
    if not LUN:
        return None
    for name in ("void_of_course", "get_voc", "voc_for_day", "compute_voc"):
        if hasattr(LUN, name):
            try:
                res = getattr(LUN, name)(dt, tz)
                if not res:
                    return None
                # поддержка разных форматов возврата
                if isinstance(res, dict):
                    s, e = res.get("start"), res.get("end")
                elif isinstance(res, (list, tuple)) and len(res) >= 2:
                    s, e = res[0], res[1]
                else:
                    s = e = None
                if s and e:
                    ss = pendulum.instance(s, tz=tz) if not isinstance(s, pendulum.DateTime) else s.in_tz(tz)
                    ee = pendulum.instance(e, tz=tz) if not isinstance(e, pendulum.DateTime) else e.in_tz(tz)
                    return {"start": ss.to_datetime_string(), "end": ee.to_datetime_string()}
            except Exception:
                pass
    return None

# --- Загрузка «благоприятных» из monthly_calendar.yml -----------------------

def load_favorables_for(month: int, year: int) -> Dict[str, Any]:
    """
    Читает ./monthly_calendar.yml. Если нет — возвращает пустые списки.
    Ожидаемый формат:
      <YYYY-MM>:
        general: {favorable: [...], unfavorable: [...]}
        haircut: {favorable: [...]}
        travel : {favorable: [...]}
        shopping:{favorable: [...]}
        health : {favorable: [...]}
    """
    blank = {
        "general": {"favorable": [], "unfavorable": []},
        "haircut": {"favorable": []},
        "travel": {"favorable": []},
        "shopping": {"favorable": []},
        "health": {"favorable": []},
    }
    path = "monthly_calendar.yml"
    if not yaml:
        return blank
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        key = f"{year:04d}-{month:02d}"
        return data.get(key) or blank
    except Exception:
        return blank

# --- Группировка по фазам для запроса к Gemini ------------------------------

@dataclass
class Group:
    id: int
    start: str  # YYYY-MM-DD
    end: str
    phase: str
    signs: List[str]

def _group_month_by_phase(days: Dict[str, Dict[str, Any]]) -> List[Group]:
    keys = sorted(days.keys())
    groups: List[Group] = []
    i = 0
    gid = 1
    while i < len(keys):
        j = i
        phase = (days[keys[i]].get("phase_name") or "").strip()
        signs: set[str] = set()
        while j + 1 < len(keys) and (days[keys[j + 1]].get("phase_name") or "").strip() == phase:
            j += 1
        for k in keys[i:j+1]:
            s = (days[k].get("sign") or "").strip()
            if s:
                signs.add(s)
        groups.append(Group(gid, keys[i], keys[j], phase, sorted(signs)))
        gid += 1
        i = j + 1
    return groups

def _human_span(start: str, end: str) -> str:
    d1 = pendulum.parse(start)
    d2 = pendulum.parse(end)
    if d1.date() == d2.date():
        return d2.format("D MMM", locale="ru")
    if d1.month == d2.month:
        return f"{d1.format('D', locale='ru')}–{d2.format('D MMM', locale='ru')}"
    return f"{d1.format('D MMM', locale='ru')}–{d2.format('D MMM', locale='ru')}"

def _ru_month(dt: pendulum.DateTime) -> str:
    return dt.format("MMMM YYYY", locale="ru")

# --- Gemini JSON helper -----------------------------------------------------

def _gemini_json(prompt: str,
                 *,
                 model: str = GEMINI_MODEL,
                 temperature: float = 0.7,
                 max_output_tokens: int = 2048,
                 retry: int = 2,
                 backoff: float = 1.5) -> Optional[dict]:
    if not GEMINI_API_KEY:
        return None
    try:
        import google.generativeai as genai
    except Exception:
        return None

    genai.configure(api_key=GEMINI_API_KEY)

    schema = {
        "type": "object",
        "properties": {
            "segments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "desc": {"type": "string"}
                    },
                    "required": ["id", "desc"]
                }
            }
        },
        "required": ["segments"]
    }

    last_err = None
    for attempt in range(retry + 1):
        try:
            model_obj = genai.GenerativeModel(
                model_name=model,
                generation_config={
                    "temperature": temperature,
                    "max_output_tokens": max_output_tokens,
                    "response_mime_type": "application/json",
                    "response_schema": schema,
                },
            )
            resp = model_obj.generate_content(prompt)
            txt = (resp.text or "").strip()
            if txt:
                return json.loads(txt)
        except Exception as e:
            last_err = e
            # попытка выдрать JSON из «свободного» ответа
            try:
                model_obj2 = genai.GenerativeModel(model_name=model)
                resp2 = model_obj2.generate_content(prompt)
                raw = (resp2.text or "")
                m = re.search(r"\{.*\}", raw, re.S)
                if m:
                    return json.loads(m.group(0))
            except Exception as e2:
                last_err = e2

        if attempt < retry:
            time.sleep(backoff ** attempt)

    return None

# --- Фолбэки (как в старых постах, с правильным месяцем) -------------------

def fallback_two_sentences(phase: str, month_num: int) -> str:
    m = MONTH_IN_RU_PREP.get(month_num, "")
    # нормализация названия фазы
    ph = (phase or "").lower()
    # Маппинг → тексты (2 предложения)
    if "первая четверть" in ph or "1-я четверть" in ph or "первая четверть луны" in ph:
        return (f"В первую четверть в {m} энергия Луны побуждает к действию и реализации задуманного. "
                "Используйте этот период для постановки целей и активных шагов к их достижению.")
    if "растущ" in ph:
        return (f"Растущая Луна в {m} наполняет нас энергией роста и новых начинаний. "
                "Используйте этот период для реализации своих мечт и укрепления уверенности в себе.")
    if "полнолуние" in ph or "полнолуния" in ph:
        return (f"Полнолуние в {m} приносит мощное очищение и возможность трансформации. "
                "Это удачное время для завершения старых дел и создания пространства для нового.")
    if "убывающ" in ph and "серп" not in ph:
        return (f"Убывающая Луна в {m} подталкивает к завершению и мягкому отпусканию лишнего. "
                "Подведите итоги и подготовьте почву для следующего цикла.")
    if "последняя четверть" in ph or "3-я четверть" in ph:
        return (f"В период последней четверти в {m} самое время для рефлексии и наведения порядка. "
                "Сфокусируйтесь на внутреннем равновесии и завершении незавершённого.")
    if "убывающий серп" in ph or ("убывающ" in ph and "серп" in ph):
        return (f"Убывающий серп в {m} помогает мягко завершить начатое и отпустить лишнее. "
                "Позаботьтесь о себе и накопите ресурсы для нового этапа.")
    if "новолуние" in ph:
        return (f"В новолуние в {m} открываются ворота к новым намерениям и перезапуску. "
                "Сформулируйте желания, очистите пространство и посейте семена будущих дел.")
    if "растущий серп" in ph or ("растущ" in ph and "серп" in ph):
        return (f"Растущий серп в {m} приносит импульс к началу дел и укреплению связей. "
                "Двигайтесь шаг за шагом, чтобы создать прочный фундамент для успеха.")
    # универсальный
    return (f"Энергии {m} поддерживают неспешный, осознанный прогресс. "
            "Доверяйте циклам и выстраивайте планы с оглядкой на внутренний ритм.")

# --- Описание всех блоков через Gemini + доспрос ---------------------------

def describe_month_via_gemini(days: Dict[str, Dict[str, Any]]) -> Dict[int, str]:
    groups = _group_month_by_phase(days)
    if not groups:
        return {}
    first = pendulum.parse(sorted(days.keys())[0])
    month_title = _ru_month(first)

    def mk_list(gs: List[Group]) -> str:
        lines = []
        for g in gs:
            span = _human_span(g.start, g.end)
            signs = ", ".join(g.signs) if g.signs else "—"
            # компактная строка для промпта (легко парсится моделью)
            lines.append(f'- {{"id": {g.id}, "span": "{span}", "phase": "{g.phase}", "signs": "{signs}"}}')
        return "\n".join(lines)

    base_prompt = f"""Ты — лаконичный русскоязычный редактор-астролог.
Напиши для каждого периода месяца (см. список) ДВА предложения: мягкая, ободряющая интерпретация периода.
Без медицинских советов и категоричных прогнозов. Без эмодзи. Без форматирования.
В каждом тексте опирайся на фазу и набор знаков; избегай повтора одних и тех же фраз между блоками.

Отвечай СТРОГО в JSON:
{{"segments":[{{"id":1,"desc":"текст"}}, ...]}}
Никакого текста вне JSON.

Месяц: {month_title}
Периоды:
{mk_list(groups)}
"""
    result: Dict[int, str] = {}
    got = _gemini_json(base_prompt, retry=GEMINI_RETRY_ATTEMPTS)
    if got and isinstance(got.get("segments"), list):
        for seg in got["segments"]:
            try:
                sid = int(seg.get("id"))
                desc = (seg.get("desc") or "").strip()
                if sid and desc:
                    result[sid] = desc
            except Exception:
                pass

    # доспрос недостающих
    missing = [g for g in groups if g.id not in result]
    rounds = 0
    while missing and rounds < GEMINI_RETRY_ATTEMPTS:
        rounds += 1
        time.sleep(GEMINI_ROUND_PAUSE)
        sub_prompt = f"""Продолжение задания. Верни ОПИСАНИЯ только для этих периодов.
Формат ответа СТРОГО тот же JSON (без пояснений и без Markdown):
{{"segments":[{{"id":<id>,"desc":"два предложения"}}, ...]}}

Периоды:
{mk_list(missing)}
"""
        got2 = _gemini_json(sub_prompt, retry=1)
        if got2 and isinstance(got2.get("segments"), list):
            for seg in got2["segments"]:
                try:
                    sid = int(seg.get("id"))
                    desc = (seg.get("desc") or "").strip()
                    if sid and desc:
                        result[sid] = desc
                except Exception:
                    pass
        missing = [g for g in groups if g.id not in result]

    return result

# --- Построение данных месяца ----------------------------------------------

def build_month(year: int, month: int) -> Dict[str, Dict[str, Any]]:
    last_day = calendar.monthrange(year, month)[1]
    days: Dict[str, Dict[str, Any]] = {}

    favorables = load_favorables_for(month, year)

    for d in range(1, last_day + 1):
        dt = pendulum.datetime(year, month, d, tz=TZ)
        key = dt.to_date_string()  # YYYY-MM-DD

        ph_name = moon_phase_name(dt)
        ph_emoji = moon_phase_emoji(dt)
        sign = moon_sign(dt, TZ)

        # VoC: сохраняем только если СТАРТ в этот день
        voc = voc_for_day(dt, TZ)

        days[key] = {
            "phase_name": ph_name,
            "phase": f"{ph_emoji} {ph_name}".strip(),
            "sign": sign,
            "long_desc": "",                  # заполним ниже
            "void_of_course": voc,            # либо None
            "favorable_days": favorables,     # одинаковый объект для месяца
        }

    # 1) Описания через Gemini (все блоки сразу + доспрос)
    by_id = describe_month_via_gemini(days)

    # 2) Раздаём описания по группам; если нет — фолбэк из старых формулировок
    groups = _group_month_by_phase(days)
    for g in groups:
        text = (by_id.get(g.id) or "").strip()
        if not text:
            # старый любимый фолбэк
            text = fallback_two_sentences(g.phase, month)

        cursor = pendulum.parse(g.start)
        end_dt = pendulum.parse(g.end).date()
        while cursor.date() <= end_dt:
            key = cursor.to_date_string()
            if key in days:
                days[key]["long_desc"] = text
            cursor = cursor.add(days=1)

    return days

# --- main -------------------------------------------------------------------

def main() -> int:
    # Дата «сегодня» может быть переопределена в workflow (см. WORK_DATE)
    today = pendulum.today(TZ)
    year, month = today.year, today.month

    data = build_month(year, month)

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"Wrote {OUT_FILE}: {len(data)} days, TZ={TZ.name}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())