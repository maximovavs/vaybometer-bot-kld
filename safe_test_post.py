#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build and optionally send a sanitized Kaliningrad VayboMeter post."""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
from typing import Union

import pendulum
from telegram import Bot, constants

from editorial_voice import build_evening_human_line, build_morning_human_line
from post_common import build_message
from post_safety import sanitize_post_text, split_telegram_text, validation_summary
from visibility_context import (
    visibility_air_penalty,
    visibility_condition_from_text,
    visibility_reason,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

TOKEN_KLG = os.getenv("TELEGRAM_TOKEN_KLG", "").strip()
TZ_STR = os.getenv("TZ", "Europe/Kaliningrad")

SEA_LABEL = "Морские города"
OTHER_LABEL = "Список не-морских городов (тёплые/холодные)"
SEA_CITIES_ORDERED = [
    ("Балтийск", (54.649, 20.055)),
    ("Янтарный", (54.912, 19.887)),
    ("Зеленоградск", (54.959, 20.478)),
    ("Пионерский", (54.930, 19.825)),
    ("Светлогорск", (54.952, 20.160)),
]
OTHER_CITIES_ALL = [
    ("Гурьевск", (54.658, 20.581)),
    ("Светлый", (54.836, 19.767)),
    ("Советск", (54.507, 21.347)),
    ("Черняховск", (54.630, 21.811)),
    ("Гусев", (54.590, 22.205)),
    ("Неман", (55.030, 21.877)),
    ("Мамоново", (54.657, 19.933)),
    ("Полесск", (54.809, 21.010)),
    ("Багратионовск", (54.368, 20.632)),
    ("Ладушкин", (54.872, 19.706)),
    ("Правдинск", (54.669, 21.330)),
    ("Славск", (54.765, 21.644)),
    ("Озёрск", (54.717, 20.282)),
    ("Нестеров", (54.620, 21.647)),
    ("Краснознаменск", (54.730, 21.104)),
    ("Гвардейск", (54.655, 21.078)),
]

_DIR_RU = {
    "N": "северный ветер",
    "NE": "северо-восточный ветер",
    "E": "восточный ветер",
    "SE": "юго-восточный ветер",
    "S": "южный ветер",
    "SW": "юго-западный ветер",
    "W": "западный ветер",
    "NW": "северо-западный ветер",
}


def _env_on(name: str, default: bool = False) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return str(val).strip().lower() in ("1", "true", "yes", "on")


def _env_any(*names: str) -> bool:
    return any(_env_on(name) for name in names)


def _plain(text: str) -> str:
    return re.sub(r"</?b>", "", str(text or "")).strip()


def _num(pattern: str, text: str) -> float | None:
    m = re.search(pattern, _plain(text), flags=re.I)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", "."))
    except Exception:
        return None


def _numbers(pattern: str, text: str) -> list[float]:
    out: list[float] = []
    for raw in re.findall(pattern, _plain(text), flags=re.I):
        val = raw[0] if isinstance(raw, tuple) else raw
        try:
            out.append(float(str(val).replace(",", ".")))
        except Exception:
            pass
    return out


def _score_label(score: float) -> str:
    if score >= 8.0:
        return "очень хороший"
    if score >= 7.0:
        return "хороший"
    if score >= 6.0:
        return "нормальный, с поправками"
    if score >= 5.0:
        return "неустойчивый"
    return "сложный"


def _kld_weather_line(v2_text: str) -> str:
    lines = [x.strip() for x in str(v2_text or "").splitlines() if x.strip()]
    return next(
        (
            x
            for x in lines
            if x.startswith(("🏙️ Калининград", "🏙 Калининград", "Калининград"))
            or ("Калининград" in x and "🏙" in x)
        ),
        "",
    )


def _kld_conditions(v2_text: str) -> dict[str, float | bool | str | None]:
    weather = _kld_weather_line(v2_text)
    p = _plain(weather)
    uv_line = next((x.strip() for x in str(v2_text or "").splitlines() if x.strip().startswith("☀️")), "")
    air_line = next((x.strip() for x in str(v2_text or "").splitlines() if x.strip().startswith("🏭")), "")
    return {
        "tmax": _num(r"(?:дн/ночь\s*)?(-?\d+(?:[\.,]\d+)?)/", p),
        "tmin": _num(r"/(-?\d+(?:[\.,]\d+)?)\s*°", p),
        "wind": _num(r"💨\s*(\d+(?:[\.,]\d+)?)", p),
        "gust": _num(r"порывы\s+до\s*(\d+(?:[\.,]\d+)?)", p),
        "rain": any(w in p.lower() for w in ("дожд", "морось", "ливень")),
        "uv": _num(r"УФ\s*(\d+(?:[\.,]\d+)?)", uv_line),
        "aqi": _num(r"AQI\s*(\d+(?:[\.,]\d+)?)", air_line),
        "visibility_condition": visibility_condition_from_text(v2_text),
    }


_KLD_MISSING_CORE_LINE = "⚠️ Данные по Калининграду обновились не полностью; проверяем источник."
_KLD_MISSING_CORE_PLAN = "✅ План: перед выходом проверьте актуальный прогноз; пост обновится после восстановления данных."


def _kld_core_weather_available(v2_text: str) -> bool:
    c = _kld_conditions(v2_text)
    return isinstance(c.get("tmax"), (int, float)) and isinstance(c.get("wind"), (int, float))


def _kld_feels_line(v2_text: str) -> str:
    c = _kld_conditions(v2_text)
    tmax = c.get("tmax")
    tmin = c.get("tmin")
    wind = c.get("wind")
    gust = c.get("gust")
    has_rain = bool(c.get("rain"))

    parts: list[str] = []
    if has_rain and ((isinstance(gust, (int, float)) and gust >= 8) or (isinstance(wind, (int, float)) and wind >= 4)):
        return "🌡 Ощущается: прохладно и сыро; на открытых местах заметно свежее."
    if has_rain:
        return "🌡 Ощущается: прохладно и сыро."
    if has_rain and ((isinstance(gust, (int, float)) and gust >= 7) or (isinstance(wind, (int, float)) and wind >= 3)):
        parts.append("прохладно, влажно и ветровито")
    elif has_rain:
        parts.append("прохладно и влажно")
    elif isinstance(tmax, (int, float)) and tmax <= 17:
        parts.append("свежо")
    else:
        parts.append("мягко для коротких прогулок")
    if (isinstance(gust, (int, float)) and gust >= 7) or (isinstance(wind, (int, float)) and wind >= 3):
        parts.append("у воды ощутимо свежее")
    if isinstance(tmin, (int, float)) and tmin <= 12:
        parts.append("утром лучше слой/ветровка")
    return "🌡 Ощущается: " + "; ".join(parts[:3]) + "."


def _kld_best_window_line(v2_text: str) -> str:
    return ""


def _kld_smart_plan_line(v2_text: str) -> str:
    c = _kld_conditions(v2_text)
    wind = c.get("wind")
    gust = c.get("gust")
    has_rain = bool(c.get("rain"))
    windy = (isinstance(gust, (int, float)) and gust >= 7) or (isinstance(wind, (int, float)) and wind >= 3)
    uv = c.get("uv")
    tmax = c.get("tmax")
    visibility = str(c.get("visibility_condition") or "clear")

    if visibility in {"dense_fog", "fog"}:
        return "✅ План: утром учитывать плохую видимость; позже ориентироваться на ветер, осадки и температуру."
    if visibility in {"mist", "reduced_visibility"}:
        return "✅ План: утром оставить запас дистанции и времени; позже сверить ветер и осадки."
    if visibility in {"dust_haze", "mixed_visibility"}:
        return "✅ План: утром проверить воздух и дальность обзора; прогулку скорректировать по факту."

    if (isinstance(tmax, (int, float)) and tmax >= 35) or (
        isinstance(tmax, (int, float)) and tmax >= 28 and isinstance(uv, (int, float)) and uv >= 6
    ):
        return "✅ План: дела и прогулка утром/вечером; днём — вода, тень, SPF и короткие выходы."
    if isinstance(tmax, (int, float)) and 25 <= tmax < 28 and isinstance(uv, (int, float)) and uv >= 6:
        return "✅ План: дела и прогулка утром/вечером; днём — SPF, вода, тень и паузы."
    if isinstance(uv, (int, float)) and uv >= 6:
        return "✅ План: прогулка в удобное окно; днём — SPF, очки/кепка, у воды учитывать ветер."
    if has_rain and windy:
        return "✅ План: дождевик и закрытая обувь; у моря выбирать защищённый маршрут."
    if has_rain:
        return "✅ План: зонт или дождевик, закрытая обувь; дела лучше короткими выходами между дождём."
    if windy:
        return "✅ План: ветровка/слой; прогулку лучше в защищённых местах."
    if isinstance(uv, (int, float)) and uv >= 6:
        return "✅ План: очки/кепка и SPF; прогулка в лучшее окно; вечером взять лёгкий слой."
    return ""


def _kld_score_line(v2_text: str) -> str:
    c = _kld_conditions(v2_text)
    tmax = c.get("tmax")
    wind = c.get("wind")
    gust = c.get("gust")
    has_rain = bool(c.get("rain"))
    uv = c.get("uv")
    aqi = c.get("aqi")
    visibility = str(c.get("visibility_condition") or "clear")

    score = 10.0
    reasons: list[str] = []
    if has_rain:
        score -= 1.6; reasons.append("дождь")
    if isinstance(gust, (int, float)):
        if gust >= 13:
            score -= 2.0; reasons.append("порывы")
        elif gust >= 10:
            score -= 1.3; reasons.append("порывы")
        elif gust >= 8:
            score -= 0.8; reasons.append("порывы")
        if isinstance(wind, (int, float)) and wind >= 6:
            score -= 0.4; reasons.append("ветер")
    elif isinstance(wind, (int, float)) and wind >= 6:
        score -= 0.8; reasons.append("ветер")
    elif isinstance(wind, (int, float)) and wind >= 3:
        score -= 0.5; reasons.append("ветер")
    if isinstance(tmax, (int, float)):
        if tmax >= 35:
            score -= 1.2; reasons.append("жара")
        elif tmax >= 30:
            score -= 0.6; reasons.append("жарко")
        if tmax <= 14:
            score -= 1.0; reasons.append("прохладно")
        elif tmax <= 16:
            score -= 0.7; reasons.append("свежо")
        elif tmax <= 18:
            score -= 0.5; reasons.append("свежо")
        elif tmax <= 21 and has_rain and isinstance(gust, (int, float)) and gust >= 8:
            score -= 0.7; reasons.append("сыро и прохладно")
    if isinstance(uv, (int, float)) and uv >= 6:
        score -= 0.3; reasons.append("УФ высокий")
    air_penalty = 0.8 if isinstance(aqi, (int, float)) and aqi > 80 else 0.0
    atmospheric_penalty = visibility_air_penalty(visibility, air_penalty)
    if atmospheric_penalty:
        score -= atmospheric_penalty
        if visibility != "clear":
            reasons.append(visibility_reason(visibility))
        elif air_penalty:
            reasons.append("воздух похуже")

    score = max(1.0, min(10.0, score))
    if isinstance(tmax, (int, float)) and tmax >= 35:
        score = min(score, 7.9)
        return f"✨ VayboMeter: {score:.1f}/10 — с оговорками; жара и высокий УФ."
    if isinstance(uv, (int, float)) and uv >= 6:
        score = min(score, 7.9)
        if isinstance(tmax, (int, float)) and tmax >= 28:
            return f"✨ VayboMeter: {score:.1f}/10 — с оговорками; жара и высокий УФ."
        if isinstance(tmax, (int, float)) and tmax >= 25:
            return f"✨ VayboMeter: {score:.1f}/10 — с оговорками; тёплый день и высокий УФ."
        if visibility != "clear":
            return f"✨ VayboMeter: {score:.1f}/10 — с оговорками; высокий УФ и {visibility_reason(visibility)}."
        return f"✨ VayboMeter: {score:.1f}/10 — с оговорками; высокий УФ и ветер у воды."
    label = _score_label(score)
    if has_rain and isinstance(gust, (int, float)) and gust >= 8:
        return f"✨ VayboMeter: {score:.1f}/10 — {label}; дождь и порывы снижают комфорт."
    if reasons:
        return f"✨ VayboMeter: {score:.1f}/10 — {label}; " + ", ".join(reasons[:3]) + "."
    return f"✨ VayboMeter: {score:.1f}/10 — {label} для обычных дел и прогулок."


def _kld_evening_score_line(v2_text: str) -> str:
    text = _plain(v2_text)
    low = text.lower()
    temps = _numbers(r"(-?\d+(?:[\.,]\d+)?)\s*°", text)
    gusts = _numbers(r"порывы\s+до\s*(\d+(?:[\.,]\d+)?)", text)
    max_t = max(temps) if temps else None
    max_gust = max(gusts) if gusts else None
    visibility = visibility_condition_from_text(text)
    score = 10.0
    reasons: list[str] = []

    has_warning = "шторм" in low or (isinstance(max_gust, (int, float)) and max_gust >= 15)
    has_precip = _has_actual_precipitation(text)
    if has_warning:
        score -= 1.8
    if has_precip:
        score -= 1.1
    if isinstance(max_gust, (int, float)):
        if max_gust >= 15:
            score -= 1.4
        elif max_gust >= 10:
            score -= 1.0; reasons.append("порывы")
        elif max_gust >= 7:
            score -= 0.6; reasons.append("ветер у воды")
    elif "ветер" in low or "порыв" in low:
        score -= 0.4; reasons.append("ветер у воды")
    if isinstance(max_t, (int, float)):
        if max_t <= 14:
            score -= 0.9; reasons.append("прохладно")
        elif max_t <= 17:
            score -= 0.5; reasons.append("свежо")
    if "локально" in low or "неравномерно" in low:
        score -= 0.2; reasons.append("локальность прогноза")
    visibility_score_penalty = visibility_air_penalty(visibility, 0.0)
    if visibility_score_penalty:
        score -= visibility_score_penalty
        reasons.append(visibility_reason(visibility))

    score = max(1.0, min(10.0, score))
    label = _score_label(score)
    if has_warning or (isinstance(max_gust, (int, float)) and max_gust >= 15):
        if has_precip:
            return f"✨ VayboMeter завтра: {score:.1f}/10 — неустойчивый день: локальные осадки и штормовые порывы."
        return f"✨ VayboMeter завтра: {score:.1f}/10 — день с повышенной осторожностью: штормовые порывы."
    if reasons:
        return f"✨ VayboMeter завтра: {score:.1f}/10 — {label}; " + ", ".join(reasons[:3]) + "."
    return f"✨ VayboMeter завтра: {score:.1f}/10 — {label} для обычных дел и прогулок."


def _translate_shore_notes(text: str) -> str:
    def repl(match: re.Match[str]) -> str:
        d = match.group(1).upper()
        direction = _DIR_RU.get(d, d)
        if match.group(2).lower() == "none":
            return f"({direction})"
        return match.group(0)

    return re.sub(r"\((N|NE|E|SE|S|SW|W|NW)/(None)\)", repl, str(text or ""), flags=re.I)


def _fmt_num(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:.1f}"


def _downgrade_sup_lines(text: str) -> str:
    lines = str(text or "").splitlines()
    out: list[str] = []
    last_water: float | None = None
    last_wave: float | None = None
    last_weather = ""
    for line in lines:
        if "°C" in line and "🌊" in line:
            last_water = _num(r"🌊\s*(\d+(?:[\.,]\d+)?)", line)
            last_wave = _num(r"(?:^|•)\s*(\d+(?:[\.,]\d+)?)\s*м\b", line)
            last_weather = line.lower()
        if "SUP" in line and "Отлично" in line:
            reasons: list[str] = []
            if isinstance(last_wave, (int, float)) and last_wave >= 0.6:
                reasons.append(f"волна {_fmt_num(last_wave)} м")
            if isinstance(last_water, (int, float)) and last_water <= 16:
                reasons.append(f"вода {_fmt_num(last_water)}°")
            if any(w in last_weather for w in ("морось", "дожд", "ливень")):
                reasons.append("осадки")
            if reasons:
                out.append("🧜‍♂️ SUP: только опытным и короткой сессии • " + ", ".join(reasons[:3]))
                continue
        out.append(line)
    return "\n".join(out)


def _downgrade_windsport_lines(text: str) -> str:
    if not _env_on("FORMAT_V2_WINDSPORT_POLISH"):
        return text
    lines = str(text or "").splitlines()
    out: list[str] = []
    last_air: float | None = None
    last_water: float | None = None
    last_wave: float | None = None
    last_weather = ""
    last_has_wind = False
    for line in lines:
        if "°C" in line and "🌊" in line:
            last_air = _num(r":\s*(-?\d+(?:[\.,]\d+)?)\s*/", line)
            last_water = _num(r"🌊\s*(\d+(?:[\.,]\d+)?)", line)
            last_wave = _num(r"(?:^|•)\s*(\d+(?:[\.,]\d+)?)\s*м\b", line)
            last_weather = line.lower()
            last_has_wind = ("💨" in line) or ("порыв" in line.lower())
        low_line = line.lower()
        is_windsport = any(x in low_line for x in ("кайт", "винг", "винд"))
        if is_windsport and "Отлично" in line:
            hard_reasons: list[str] = []
            soft_reasons: list[str] = []
            if isinstance(last_water, (int, float)) and last_water <= 16:
                hard_reasons.append(f"вода {_fmt_num(last_water)}°")
            if isinstance(last_wave, (int, float)) and last_wave >= 0.6:
                hard_reasons.append(f"волна {_fmt_num(last_wave)} м")
            if any(w in last_weather for w in ("морось", "дожд", "ливень")):
                hard_reasons.append("осадки")
            if isinstance(last_air, (int, float)) and last_air <= 16:
                hard_reasons.append(f"воздух {_fmt_num(last_air)}°")
            if not last_has_wind:
                soft_reasons.append("ветер сверить утром")
            if hard_reasons:
                out.append("🏄 Кайт/Винг/Винд: только подготовленным; гидрокостюм обязателен, ветер и волну сверить утром.")
                continue
            if soft_reasons:
                out.append("🏄 Кайт/Винг/Винд: возможно для опытных; по фактическому ветру и состоянию воды.")
                continue
        out.append(line)
    return "\n".join(out)


def _apply_format_v2_test_polish(v2_text: str) -> str:
    if not _env_any("FORMAT_V2_POLISH", "FORMAT_V2_TEST_POLISH"):
        return v2_text
    text = _translate_shore_notes(v2_text)
    text = _downgrade_sup_lines(text)
    text = _downgrade_windsport_lines(text)
    text = re.sub(r"\s+,", ",", text)
    text = re.sub(r"🌙\s+🌙", "🌙", text)
    return text


def _score_line(v2_text: str) -> str:
    return next((x.strip() for x in str(v2_text or "").splitlines() if "VayboMeter" in x and "/10" in x), "")


def _score_value(v2_text: str) -> float | None:
    return _num(r"VayboMeter\s+завтра:\s*(\d+(?:[\.,]\d+)?)\s*/\s*10", v2_text)


def _score_reasons(v2_text: str) -> str:
    line = _score_line(v2_text)
    m = re.search(r";\s*(.*?)\.?$", line)
    return (m.group(1) if m else "").lower()


def _kld_score_conclusion(score: float) -> str:
    if score >= 8.7:
        return "День комфортный для обычных дел и прогулок; у моря всё равно стоит сверить ветер утром."
    if score >= 7:
        return "День в целом рабочий и прогулочный, но у воды лучше держать ветровку и сверить порывы утром."
    if score >= 5.5:
        return "День рабочий, но не прогулочный: лучше короткие маршруты, слой одежды и запасной план на дождь."
    return "День лучше вести в бережном режиме: короткие выходы, защита от ветра/дождя и минимум открытого побережья."


def _kld_reason_conclusion(score: float, reasons: str, v2_text: str) -> str:
    low = (reasons + " " + _plain(v2_text)).lower()
    precip = any(x in low for x in ("осадки", "дожд", "морось"))
    cool = any(x in low for x in ("прохлад", "свеж"))
    wind = any(x in low for x in ("порыв", "ветер"))
    gusts = _numbers(r"порывы\s*(?:до\s*)?(\d+(?:[\.,]\d+)?)", v2_text)
    warning = "шторм" in low or (gusts and max(gusts) >= 15)
    if warning:
        return "День лучше вести с запасным планом: сверить предупреждения утром, у воды не рисковать и держать короткие маршруты."
    if precip and cool and wind:
        return "День рабочий, но не прогулочный: короткие маршруты, слой одежды, закрытая обувь и запасной план на дождь."
    if precip and cool:
        return "Главная нагрузка — влажная прохлада: лучше короткие выходы, тёплый слой и сухая обувь."
    if wind:
        return "У воды осторожнее: выбирай защищённые променады и маршруты за домами, а порывы перепроверь утром."
    if precip:
        return "Планируй день короткими выходами между осадками; зонт/капюшон и закрытая обувь будут полезнее долгой прогулки."
    return _kld_score_conclusion(score)


def _replace_conclusion(v2_text: str, conclusion: str) -> str:
    lines = str(v2_text or "").splitlines()
    out: list[str] = []
    in_conclusion = False
    replaced = False
    for line in lines:
        if line.strip().startswith("📌 <b>Вывод"):
            in_conclusion = True
            replaced = False
            out.append(line)
            continue
        if in_conclusion and not replaced and line.strip():
            out.append(conclusion)
            replaced = True
            in_conclusion = False
            continue
        out.append(line)
    return "\n".join(out)


def _apply_score_conclusion(v2_text: str) -> str:
    if not _env_any("FORMAT_V2_SCORE_CONCLUSION", "FORMAT_V2_TEST_CONCLUSION"):
        return v2_text
    score = _score_value(v2_text)
    if score is None:
        return v2_text
    if _env_on("FORMAT_V2_REASON_CONCLUSION"):
        return _replace_conclusion(v2_text, _kld_reason_conclusion(score, _score_reasons(v2_text), v2_text))
    return _replace_conclusion(v2_text, _kld_score_conclusion(score))


def _kld_main_nuance(v2_text: str) -> str:
    low = (_score_reasons(v2_text) + " " + _plain(v2_text)).lower()
    visibility = visibility_condition_from_text(v2_text)
    if visibility in {"dense_fog", "fog"}:
        return "⚠️ Главный нюанс: утром осторожнее на дорогах, развязках, мостах и открытых участках."
    if visibility in {"mist", "reduced_visibility"}:
        return "⚠️ Главный нюанс: утром обзор местами короче обычного; на дорогах держать запас дистанции."
    if visibility in {"dust_haze", "mixed_visibility"}:
        return "⚠️ Главный нюанс: утром воздух и дальняя видимость могут быть хуже обычного."
    precip = any(x in low for x in ("осадки", "морось", "дожд"))
    cool = any(x in low for x in ("прохлад", "свеж"))
    wind = any(x in low for x in ("порыв", "ветер"))
    if precip and cool and wind:
        return "⚠️ Главный нюанс: морось, свежий ветер и прохладное побережье."
    if precip and cool:
        return "⚠️ Главный нюанс: влажная прохлада и короткие окна для прогулок."
    if wind:
        return "⚠️ Главный нюанс: у воды ветер ощущается сильнее, чем в городе."
    if precip:
        return "⚠️ Главный нюанс: осадки могут идти неравномерно по области."
    return ""


def _insert_main_nuance(v2_text: str) -> str:
    if (
        not _env_on("FORMAT_V2_MAIN_NUANCE")
        or "⚠️ Главный нюанс:" in v2_text
        or "⚠️ Нюанс:" in v2_text
    ):
        return v2_text
    return _inject_after_anchor(v2_text, _kld_main_nuance(v2_text), ("✨ VayboMeter завтра:", "✨ VayboMeter:"))


def _date_from_text(v2_text: str) -> str:
    m = re.search(r"\((\d{2}\.\d{2}\.\d{4})\)", str(v2_text or ""))
    return m.group(1) if m else ""


def _without_editorial_voice(v2_text: str) -> list[str]:
    return [
        line
        for line in str(v2_text or "").splitlines()
        if not line.strip().startswith(("💬 По-человечески:", "💬 Настрой на завтра:"))
    ]


def _kld_voice_conditions(v2_text: str) -> dict[str, object]:
    c = _kld_conditions(v2_text)
    plain = _plain(v2_text)
    text = plain.lower()
    gusts = _numbers(r"порывы\s*(?:до\s*)?(\d+(?:[\.,]\d+)?)", plain)
    winds = _numbers(r"💨\s*(\d+(?:[\.,]\d+)?)\s*м/с", plain)
    if isinstance(c.get("gust"), (int, float)):
        gusts.append(float(c["gust"]))
    if isinstance(c.get("wind"), (int, float)):
        winds.append(float(c["wind"]))
    max_gust = max(gusts) if gusts else None
    max_wind = max(winds) if winds else None
    explicit_strong_wind = bool(re.search(r"сильный ветер|шторм|резкие порывы", text, flags=re.I))
    return {
        "max_temp": c.get("tmax"),
        "uv": c.get("uv"),
        "uv_high": isinstance(c.get("uv"), (int, float)) and c["uv"] >= 6,
        "wind": explicit_strong_wind
        or isinstance(max_gust, (int, float)) and max_gust >= 8
        or isinstance(max_wind, (int, float)) and max_wind >= 6,
        "gust": max_gust,
        "rain": _has_actual_precipitation(plain),
        "warm": isinstance(c.get("tmax"), (int, float)) and c["tmax"] >= 20,
    }


def _has_actual_precipitation(text: str) -> bool:
    plain = _plain(text)
    low = plain.lower()
    if re.search(r"\b(?:дождь|дождя|дождём|дождем|дожди|дождевые\s+окна|морось|ливень|ливни|ливнев\w*)\b", low, flags=re.I):
        return True
    for line in plain.splitlines():
        s = line.lower()
        if "осад" not in s:
            continue
        uncertainty = re.search(
            r"(?:провер\w*|уточн\w*|вероятност\w*|возможны\s+ли)[^.\n;:]{0,45}осад|осад[^.\n;:]{0,45}(?:провер\w*|уточн\w*)",
            s,
            flags=re.I,
        )
        if uncertainty:
            continue
        if re.search(r"(?:местами|ожида\w*|пройдут|будут|возможны|прогнозируются)[^.\n;:]{0,35}осад|осад[^.\n;:]{0,35}(?:могут\s+идти|ожида\w*|неравномерн\w*)", s, flags=re.I):
            return True
    return False


def _insert_editorial_after(lines: list[str], line_to_add: str, prefixes: tuple[str, ...]) -> str:
    if not line_to_add:
        return "\n".join(lines)
    insert_at = None
    for idx, line in enumerate(lines):
        if line.strip().startswith(prefixes):
            insert_at = idx
    if insert_at is None:
        insert_at = 0
    out = list(lines)
    out.insert(insert_at + 1, line_to_add)
    return "\n".join(out)


def _apply_editorial_voice(v2_text: str, mode: str) -> str:
    lines = _without_editorial_voice(v2_text)
    date_s = _date_from_text(v2_text)
    conditions = _kld_voice_conditions(v2_text)
    if mode.startswith("morn"):
        if not _kld_core_weather_available(v2_text):
            return "\n".join(lines)
        gust = conditions.get("gust")
        max_temp = conditions.get("max_temp")
        if conditions.get("rain") and isinstance(gust, (int, float)) and gust >= 8 and isinstance(max_temp, (int, float)) and max_temp <= 21:
            line = "💬 По-человечески: прохладно и сыро; для обычных дел нормально в непромокаемой одежде."
        else:
            line = build_morning_human_line("Калининград", date_s or "today", conditions)
        return _insert_editorial_after(lines, line, ("✨ VayboMeter:",))
    line = build_evening_human_line("Калининград", date_s or "tomorrow", conditions)
    return _insert_editorial_after(lines, line, ("⚠️ Нюанс:", "⚠️ Главный нюанс:", "🧭 Главное завтра:", "✨ VayboMeter завтра:", "✨ VayboMeter:"))


def _is_kld_nuance_line(line: str) -> bool:
    return line.strip().startswith(("⚠️ Нюанс:", "⚠️ Главный нюанс:"))


def _storm_score_replacement(line: str, full_text: str) -> str:
    if "VayboMeter" not in line or "/10" not in line:
        return line
    plain = _plain(full_text)
    gusts = _numbers(r"порывы\s*(?:до\s*)?(\d+(?:[\.,]\d+)?)", plain)
    max_gust = max(gusts) if gusts else None
    low = plain.lower()
    if not ("шторм" in low or (isinstance(max_gust, (int, float)) and max_gust >= 15)):
        return line
    replacement = (
        "— неустойчивый день: локальные осадки и штормовые порывы."
        if _has_actual_precipitation(plain)
        else "— день с повышенной осторожностью: штормовые порывы."
    )
    return re.sub(r"—\s*[^.\n]*\.?", replacement, line.strip(), flags=re.I)


def _storm_warning_replacement(line: str) -> str:
    s = _plain(line)
    gusts = _numbers(r"порыв\w*\s*(?:до\s*)?(\d+(?:[\.,]\d+)?)\s*м/с", s)
    if gusts:
        return f"⚠️ Штормовое предупреждение: порывы до {_fmt_num(max(gusts))} м/с."
    detail = re.sub(r"^⚠️?\s*", "", s).strip()
    detail = re.sub(r"^(?:Предупреждение|Штормовое(?:\s+предупреждение)?)\s*:?\s*", "", detail, flags=re.I).strip(" .")
    if not detail:
        return "⚠️ Штормовое предупреждение."
    return f"⚠️ Штормовое предупреждение: {detail}."


def _finalize_kld_evening_safe_text(v2_text: str, mode: str) -> str:
    if mode.startswith("morn"):
        return v2_text

    max_temp_values = _numbers(r"(-?\d+(?:[\.,]\d+)?)\s*/\s*-?\d+(?:[\.,]\d+)?\s*°C", v2_text)
    max_temp = max(max_temp_values) if max_temp_values else None
    if isinstance(max_temp, (int, float)) and max_temp >= 28:
        temp_part = "температура высокая"
    elif isinstance(max_temp, (int, float)) and max_temp >= 24:
        temp_part = "температура тёплая"
    else:
        temp_part = "по температуре спокойно"
    if "🎯 Уверенность: температура высокая; ветер/осадки лучше проверить утром." in str(v2_text or ""):
        v2_text = str(v2_text or "").replace(
            "🎯 Уверенность: температура высокая; ветер/осадки лучше проверить утром.",
            f"🎯 Уверенность: {temp_part}; осадки и условия у воды лучше проверить утром.",
        )
    if "🎯 Уверенность: температура высокая; осадки и условия у воды лучше проверить утром." in str(v2_text or ""):
        v2_text = str(v2_text or "").replace(
            "🎯 Уверенность: температура высокая; осадки и условия у воды лучше проверить утром.",
            f"🎯 Уверенность: {temp_part}; осадки и условия у воды лучше проверить утром.",
        )

    v2_text = str(v2_text or "")
    v2_text = v2_text.replace(
        "🧭 Главное завтра: главный фактор — ветер, порывы и осторожность у воды.",
        "🧭 Главное завтра: неустойчивое погодное окно; береговые планы лучше держать гибкими.",
    )
    v2_text = v2_text.replace(
        "🧭 Главное завтра: штормовые порывы; у воды и на открытых участках особенно осторожно.",
        "🧭 Главное завтра: неустойчивое погодное окно; береговые планы лучше держать гибкими.",
    )
    v2_text = re.sub(
        r"(?:короткий\s+)?гидрокостюм\s*(?:шорти\s*)?\d+(?:/\d+)?\s*мм|shorty\s*2\s*мм",
        "экипировку выбрать по длительности сессии, ветру и индивидуальной переносимости воды",
        v2_text,
        flags=re.I,
    )

    lines = v2_text.splitlines()
    before_tags: list[str] = []
    hashtag_line = ""
    seen_hashtags = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            if not hashtag_line:
                hashtag_line = stripped
            seen_hashtags = True
            continue
        if seen_hashtags:
            continue
        before_tags.append(line)

    has_compact_nuance = any(line.strip().startswith("⚠️ Нюанс:") for line in before_tags)
    out: list[str] = []
    nuance_seen = False
    skip_next_specific_warning = False
    for line in before_tags:
        stripped = line.strip()
        if skip_next_specific_warning:
            skip_next_specific_warning = False
            if "шторм" in stripped.lower() or "порыв" in stripped.lower():
                out.append(_storm_warning_replacement(stripped))
                continue
        if has_compact_nuance and stripped.startswith("⚠️ Главный нюанс:"):
            continue
        if stripped == "⚠️ <b>Предупреждение</b>" or stripped == "⚠️ Предупреждение":
            skip_next_specific_warning = True
            continue
        if stripped.startswith("⚠️") and ("шторм" in stripped.lower() or "порыв" in stripped.lower()):
            normalized = _storm_warning_replacement(stripped)
            if normalized not in out:
                out.append(normalized)
            continue
        if "VayboMeter" in stripped and "/10" in stripped:
            out.append(_storm_score_replacement(stripped, v2_text))
            continue
        if "Отлично" in stripped and re.search(r"\b(?:SUP|С[ёе]рф|Кайт|Винг|Винд)\b", stripped, flags=re.I):
            continue
        if _is_kld_nuance_line(stripped):
            if nuance_seen:
                continue
            nuance_seen = True
        out.append(line)

    while out and not out[-1].strip():
        out.pop()
    if hashtag_line:
        out.append(hashtag_line)
    return "\n".join(out)


def _city_temperature_pairs(text: str) -> list[tuple[str, float, float | None]]:
    from format_v2 import _city_temperature_pairs as parse_city_temperature_pairs

    return parse_city_temperature_pairs(text)


def _regional_context_from_source(source_text: str) -> str:
    from format_v2 import _morning_region_context_from_pairs

    structured = getattr(source_text, "regional_city_temperatures", None)
    if structured:
        line = _morning_region_context_from_pairs(structured)
        if line:
            return line
    for raw in str(source_text or "").splitlines():
        line = raw.strip()
        if line.startswith("🌡 По области:") and any(
            marker in line
            for marker in ("днём теплее всего", "дневные температуры почти одинаковые", "теплее всего —")
        ):
            return line
    return _morning_region_context_from_pairs(_city_temperature_pairs(source_text))


def _kp_line_from_source(source_text: str) -> str:
    for raw in str(source_text or "").splitlines():
        s = _plain(raw).replace("\u00a0", " ")
        if not re.search(r"\b(?:Кр|Kp)\b|Kp[-\s]?index|индекс\s*Kp", s, flags=re.I):
            continue
        if re.search(r"\b(?:Кр|Kp)\s*[:=]?\s*н/д\b", s, flags=re.I):
            continue
        m = re.search(
            r"(?:\b(?:Кр|Kp)\b|Kp[-\s]?index|индекс\s*Kp)\s*(?:[:=—-])?\s*(\d+(?:[\.,]\d+)?)",
            s,
            flags=re.I,
        )
        if not m:
            continue
        try:
            kp = float(m.group(1).replace(",", "."))
        except Exception:
            continue
        alert = bool(re.search(r"бур|шторм|возмущ|alert|storm", s, flags=re.I))
        mood = "спокойно" if kp < 4 and not alert else "возмущённо"
        return f"🧲 Космопогода: {mood}, Kp {kp:.1f}."
    return ""


def _baltic_line_from_source(source_text: str) -> str:
    waters: list[float] = []
    waves: list[float] = []
    for raw in str(source_text or "").splitlines():
        s = _plain(raw).replace("\u00a0", " ").strip()
        low = s.lower()
        if "морские города" in low:
            continue
        if not any(marker in low for marker in ("балтика", "море", "вода", "волна", "🌊")):
            continue

        water: float | None = None
        wave: float | None = None
        water_match = re.search(r"(?:вода|море)[^\d-]{0,20}(-?\d+(?:[\.,]\d+)?)\s*°?\s*C?", s, flags=re.I)
        if water_match:
            try:
                water = float(water_match.group(1).replace(",", "."))
            except Exception:
                water = None
        wave_match = re.search(r"(?:волна|wave)[^\d]{0,20}(\d+(?:[\.,]\d+)?)\s*м", s, flags=re.I)
        if wave_match:
            try:
                wave = float(wave_match.group(1).replace(",", "."))
            except Exception:
                wave = None

        if "🌊" in s:
            tail = s.split("🌊", 1)[1]
            nums: list[float] = []
            for num in re.findall(r"(\d+(?:[\.,]\d+)?)", tail):
                try:
                    nums.append(float(num.replace(",", ".")))
                except Exception:
                    pass
            if water is None and nums and 5 <= nums[0] <= 30:
                water = nums[0]
            if wave is None and len(nums) >= 2 and 0 <= nums[1] <= 5:
                wave = nums[1]

        if water is not None:
            waters.append(water)
        if wave is not None:
            waves.append(wave)
    if not waters and not waves:
        return ""

    parts: list[str] = []
    if waters:
        low_water, high_water = min(waters), max(waters)
        if round(low_water, 1) == round(high_water, 1):
            parts.append(f"вода {_fmt_num(low_water)}°C")
        else:
            parts.append(f"вода {_fmt_num(low_water)}–{_fmt_num(high_water)}°C")
    if waves:
        low_wave, high_wave = min(waves), max(waves)
        if round(low_wave, 1) == round(high_wave, 1):
            parts.append(f"волна {_fmt_num(low_wave)} м")
        else:
            parts.append(f"волна {_fmt_num(low_wave)}–{_fmt_num(high_wave)} м")
    if waters:
        return "🌊 Балтика: " + "; ".join(parts) + "; у воды свежее, ветер ощущается заметнее."
    return "🌊 Балтика: " + "; ".join(parts) + "; у открытой воды ветер заметнее."


def _replace_or_insert_line(v2_text: str, line_to_add: str, *, prefix: str, anchors: tuple[str, ...]) -> str:
    if not line_to_add:
        return v2_text
    lines = str(v2_text or "").splitlines()
    out = [line for line in lines if not line.strip().startswith(prefix)]
    text = "\n".join(out)
    return _inject_after_anchor(text, line_to_add, anchors)


def _replace_or_insert_regional_line(v2_text: str, line_to_add: str) -> str:
    if not line_to_add:
        return v2_text
    lines = str(v2_text or "").splitlines()
    out = [
        line
        for line in lines
        if not line.strip().startswith(("🌡 По области:", "🌡 Теплее всего —"))
    ]
    insert_after = next(
        (idx for idx, line in reversed(list(enumerate(out))) if line.strip().startswith("💬 По-человечески:")),
        None,
    )
    if insert_after is None:
        insert_after = next(
            (idx for idx, line in enumerate(out) if line.strip().startswith("✨ VayboMeter")),
            0,
        )
    out.insert(insert_after + 1, line_to_add)
    return "\n".join(out)


def _remove_vague_regional_fallback(v2_text: str) -> str:
    lines = [
        line
        for line in str(v2_text or "").splitlines()
        if not line.strip().startswith("🌡 По области:")
    ]
    return "\n".join(lines)


def _insert_kp_line(v2_text: str, line_to_add: str) -> str:
    if not line_to_add:
        return v2_text
    lines = [line for line in str(v2_text or "").splitlines() if not line.strip().startswith("🧲")]
    out: list[str] = []
    inserted = False
    for line in lines:
        if not inserted and line.strip().startswith(("🌊 Балтика:", "💱", "🌇 <b>Солнце", "🌅 <b>Солнце", "✅ План:", "#")):
            out.append(line_to_add)
            inserted = True
        out.append(line)
    if not inserted:
        out.append(line_to_add)
    return "\n".join(out)


def _replace_or_insert_baltic_line(v2_text: str, line_to_add: str) -> str:
    if not line_to_add:
        return v2_text
    lines = [line for line in str(v2_text or "").splitlines() if not line.strip().startswith("🌊 Балтика:")]
    return _insert_before_anchor(
        "\n".join(lines),
        line_to_add,
        ("💱", "🌇 <b>Солнце", "🌅 <b>Солнце", "🌙 <b>Астроритм", "✅ План:", "#"),
    )


def _remove_vague_baltic_fallback(v2_text: str) -> str:
    lines = [
        line
        for line in str(v2_text or "").splitlines()
        if not line.strip().startswith("🌊 Балтика: у воды")
    ]
    return "\n".join(lines)


def _apply_morning_raw_context(v2_text: str, raw_text: str, mode: str) -> str:
    if not mode.startswith("morn"):
        return v2_text
    if not _kld_core_weather_available(v2_text):
        return v2_text
    out = v2_text
    regional = _regional_context_from_source(raw_text)
    if regional:
        out = _replace_or_insert_regional_line(out, regional)
    out = _insert_kp_line(out, _kp_line_from_source(raw_text))
    baltic = _baltic_line_from_source(raw_text)
    if baltic:
        out = _replace_or_insert_baltic_line(out, baltic)
    return out


def _sensor_line_from_legacy(legacy_text: str) -> str:
    marker = "Safe" + "cast"
    for line in str(legacy_text or "").splitlines():
        low = line.lower()
        if marker.lower() not in low and "радиационный фон" not in low and "частный датчик" not in low:
            continue
        value_match = re.search(
            r"(\d+(?:[\.,]\d+)?)\s*(?:μsv/h|µsv/h|usv/h|мкзв/ч|мкз/ч)",
            low,
            flags=re.I,
        )
        baseline_match = re.search(
            r"(?:обычно|фон|baseline|норм(?:а|ально)?|референс|диапазон)[^\d]{0,24}"
            r"(\d+(?:[\.,]\d+)?)",
            low,
            flags=re.I,
        )
        has_age = bool(re.search(r"\b\d{1,2}:\d{2}\b|обнов|замер|🕓|timestamp|ts|\d+\s*(?:мин|ч|час)", low, flags=re.I))
        if not (value_match and baseline_match and has_age):
            continue
        try:
            value = float(value_match.group(1).replace(",", "."))
            baseline = float(baseline_match.group(1).replace(",", "."))
        except Exception:
            continue
        if re.search(r"critical|alert|опасн|🔴", low) and value >= 0.30:
            interp = "выше контрольного уровня; проверьте динамику и официальные сообщения"
        elif value - baseline >= 0.02:
            interp = "немного выше локального фона"
        elif baseline - value >= 0.02:
            interp = "ниже локального фона"
        else:
            interp = "около локального фона"
        return f"🧪 Частный датчик радиации: {value:.2f} μSv/h, обычно {baseline:.2f} — {interp}."
    return ""


def _soften_private_sensor_wording(v2_text: str) -> str:
    lines: list[str] = []
    for line in str(v2_text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("🧪") and not _sensor_line_from_legacy(stripped):
            continue
        lines.append(line)
    return "\n".join(lines)


def _replace_sensor_lines(v2_text: str, line_to_add: str) -> str:
    lines = [line for line in str(v2_text or "").splitlines() if not line.strip().startswith("🧪")]
    if not line_to_add:
        return "\n".join(lines)
    return _insert_before_anchor(
        "\n".join(lines),
        line_to_add,
        ("🧲", "🌊 Балтика", "💱", "🌇 <b>Солнце", "🌅 <b>Солнце", "🌙 <b>Астроритм", "✅ План:", "✅ <b>Рекомендации", "📌 <b>Вывод", "#"),
    )


def _inject_sensor_line(v2_text: str, legacy_text: str) -> str:
    if not _env_any("FORMAT_V2_SENSOR_LINE", "FORMAT_V2_TEST_SENSOR", "FORMAT_V2_TEST_SAFECAST"):
        return v2_text
    line = _sensor_line_from_legacy(legacy_text) or _sensor_line_from_legacy(v2_text)
    if not line:
        return v2_text
    if any(existing.strip().startswith("🧪") for existing in str(v2_text or "").splitlines()):
        return _replace_sensor_lines(v2_text, line)
    if "Safecast" in v2_text:
        out: list[str] = []
        replaced = False
        for existing in str(v2_text or "").splitlines():
            if not replaced and "Safecast" in existing:
                out.append(line)
                replaced = True
            else:
                out.append(existing)
        return chr(10).join(out)
    return _insert_before_anchor(v2_text, line, ("🧲", "🌊 Балтика", "💱", "🌇 <b>Солнце", "🌅 <b>Солнце", "🌙 <b>Астроритм", "✅ План:", "✅ <b>Рекомендации", "📌 <b>Вывод"))


def _finalize_kld_morning_safe_text(v2_text: str, raw_msg: str, legacy_text: str, mode: str) -> str:
    if not mode.startswith("morn"):
        return v2_text
    out = v2_text
    if not _kld_core_weather_available(out):
        return _replace_plan(_soften_private_sensor_wording(out), _KLD_MISSING_CORE_PLAN)

    regional = _regional_context_from_source(raw_msg) or _regional_context_from_source(legacy_text)
    if regional:
        out = _replace_or_insert_regional_line(out, regional)
    else:
        out = _remove_vague_regional_fallback(out)

    sensor = _sensor_line_from_legacy(raw_msg) or _sensor_line_from_legacy(legacy_text) or _sensor_line_from_legacy(out)
    if sensor:
        out = _replace_sensor_lines(out, sensor)
    else:
        out = _replace_sensor_lines(out, "")

    kp_line = _kp_line_from_source(raw_msg) or _kp_line_from_source(legacy_text) or _kp_line_from_source(out)
    out = _insert_kp_line(out, kp_line)

    baltic = _baltic_line_from_source(raw_msg) or _baltic_line_from_source(legacy_text)
    if baltic:
        out = _replace_or_insert_baltic_line(out, baltic)
    else:
        out = _remove_vague_baltic_fallback(out)

    return _soften_private_sensor_wording(out)


def _apply_confidence_polish(v2_text: str) -> str:
    if not _env_on("FORMAT_V2_CONFIDENCE_POLISH"):
        return v2_text
    return str(v2_text or "").replace(
        "✅ Разница берег/внутри области: учитывать обязательно.",
        "✅ Берег и восток области: ощущаются по-разному — не усредняй прогноз.",
    )


def _apply_astro_cleanup(v2_text: str) -> str:
    if not _env_on("FORMAT_V2_ASTRO_CLEANUP"):
        return v2_text
    lines = str(v2_text or "").splitlines()
    out: list[str] = []
    in_astro = False
    astro_details = 0
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(("🌙 <b>Астроритм", "☀️ <b>Солнце", "🌅 <b>Солнце и ритм")):
            in_astro = True
            astro_details = 0
            out.append(line)
            continue
        if in_astro and stripped.startswith(("🧲", "🧪", "✅ План:", "✅ <b>Рекомендации", "📌 <b>Вывод", "#")):
            in_astro = False
        if in_astro and stripped:
            if stripped.endswith("для первых") or stripped.endswith("и вдо…"):
                continue
            if "…" in stripped and len(stripped) > 80:
                continue
            if stripped.startswith("✅ В целом:"):
                line = "✅ Астроритм: благоприятный."
                stripped = line
            is_valid_astro = (
                stripped.startswith(("🌅", "🌇", "🌑", "🌒", "🌓", "🌔", "🌕", "🌖", "🌗", "🌘", "🌙"))
                or (stripped.startswith("✨") and re.search(r"%|освещ", stripped, flags=re.I))
                or (stripped.startswith(("✅", "⚠️", "➿")) and "общий фон" in stripped.lower())
                or stripped.startswith("✅ Астроритм")
                or stripped.startswith("💚 В плюсе")
                or stripped.startswith(("⚫️ VoC", "⚫ VoC"))
            )
            if not is_valid_astro:
                continue
            astro_details += 1
            if astro_details > 7:
                continue
        out.append(line)
    return "\n".join(out)


def _apply_compact(v2_text: str) -> str:
    if not _env_on("FORMAT_V2_COMPACT"):
        return v2_text
    lines = str(v2_text or "").splitlines()
    out: list[str] = []
    in_main = False
    main_text_seen = 0
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("🧭 <b>Главный сценарий"):
            in_main = True
            main_text_seen = 0
            out.append(line)
            continue
        if in_main and stripped.startswith(("✨ VayboMeter", "🎯")):
            in_main = False
        if in_main and stripped and not stripped.startswith("🧭"):
            main_text_seen += 1
            if main_text_seen > 1:
                continue
        if stripped.startswith("🧜‍♂️ Отлично:"):
            continue
        out.append(line)
    return "\n".join(out)


def _inject_after_anchor(v2_text: str, line_to_add: str, anchors: tuple[str, ...]) -> str:
    if not line_to_add:
        return v2_text
    lines = str(v2_text or "").splitlines()
    out: list[str] = []
    inserted = False
    for line in lines:
        out.append(line)
        if not inserted and line.strip().startswith(anchors):
            out.append(line_to_add)
            inserted = True
    return "\n".join(out)


def _insert_before_anchor(v2_text: str, line_to_add: str, anchors: tuple[str, ...]) -> str:
    if not line_to_add:
        return v2_text
    lines = str(v2_text or "").splitlines()
    out: list[str] = []
    inserted = False
    for line in lines:
        if not inserted and line.strip().startswith(anchors):
            if out and out[-1].strip():
                out.append("")
            out.append(line_to_add)
            out.append("")
            inserted = True
        out.append(line)
    if not inserted:
        out.append(line_to_add)
    return "\n".join(out)


def _replace_plan(v2_text: str, new_plan: str) -> str:
    if not new_plan:
        return v2_text
    lines = str(v2_text or "").splitlines()
    out: list[str] = []
    replaced = False
    for line in lines:
        if not replaced and line.strip().startswith(("✅ План:", "✅ Сегодня:")):
            out.append(new_plan)
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append(new_plan)
    return "\n".join(out)


def _inject_morning_feels(v2_text: str, mode: str) -> str:
    if not (mode.startswith("morn") and _env_on("MORNING_FEELS_LIKE")):
        return v2_text
    if not _kld_core_weather_available(v2_text):
        return v2_text
    if "🌡 Ощущается:" in v2_text:
        return v2_text
    feels = _kld_feels_line(v2_text)
    if not feels:
        return v2_text
    return _inject_after_anchor(v2_text, feels, ("🏙️ Калининград",))


def _inject_morning_best_window(v2_text: str, mode: str) -> str:
    if not (mode.startswith("morn") and _env_on("MORNING_BEST_WINDOW")):
        return v2_text
    if not _kld_core_weather_available(v2_text):
        return v2_text
    if "Лучшее окно:" in v2_text:
        return v2_text
    window = _kld_best_window_line(v2_text)
    if not window:
        return v2_text
    if "🌡 Ощущается:" in v2_text:
        return _inject_after_anchor(v2_text, window, ("🌡 Ощущается:",))
    return _inject_after_anchor(v2_text, window, ("🏙️ Калининград",))


def _valid_best_window_line(line: str) -> bool:
    s = str(line or "").strip()
    if "Лучшее окно:" not in s:
        return True
    m = re.search(r"\b(\d{1,2}):(\d{2})\s*[–—-]\s*(\d{1,2}):(\d{2})\b", s)
    if not m:
        return False
    start = int(m.group(1)) * 60 + int(m.group(2))
    end = int(m.group(3)) * 60 + int(m.group(4))
    return end > start and end - start >= 120


def _remove_invalid_best_window(v2_text: str) -> str:
    return "\n".join(line for line in str(v2_text or "").splitlines() if _valid_best_window_line(line))


def _inject_morning_score(v2_text: str, mode: str) -> str:
    if not (mode.startswith("morn") and _env_on("MORNING_VAYBOMETER_SCORE")):
        return v2_text
    if not _kld_core_weather_available(v2_text):
        return v2_text
    score = _kld_score_line(v2_text)
    lines = str(v2_text or "").splitlines()
    score_indexes = [idx for idx, line in enumerate(lines) if "VayboMeter" in line]
    if score_indexes:
        c = _kld_conditions(v2_text)
        tmax = c.get("tmax")
        uv = c.get("uv")
        heat = isinstance(tmax, (int, float)) and tmax >= 35
        heat_word_ok = isinstance(tmax, (int, float)) and tmax >= 28
        warm_uv_day = isinstance(tmax, (int, float)) and 25 <= tmax < 28
        uv_high = isinstance(uv, (int, float)) and uv >= 6
        rain_or_gust = (
            bool(c.get("rain"))
            or isinstance(c.get("gust"), (int, float)) and c["gust"] >= 8
            or str(c.get("visibility_condition") or "clear") != "clear"
        )
        out: list[str] = []
        replaced = False
        for idx, line in enumerate(lines):
            if idx in score_indexes:
                if not replaced:
                    if "/10" in line:
                        cleaned = re.sub(r"^✨\s*VayboMeter\s+сегодня\s*:", "✨ VayboMeter:", line.strip(), flags=re.I)
                        if rain_or_gust:
                            cleaned = score
                        elif heat or (uv_high and heat_word_ok):
                            cleaned = re.sub(
                                r"(VayboMeter:\s*\d+(?:[\.,]\d+)?/10\s+—\s*).*$",
                                r"\1с оговорками; жара и высокий УФ.",
                                cleaned,
                                flags=re.I,
                            )
                        elif uv_high and warm_uv_day:
                            cleaned = re.sub(
                                r"(VayboMeter:\s*\d+(?:[\.,]\d+)?/10\s+—\s*).*$",
                                r"\1с оговорками; тёплый день и высокий УФ.",
                                cleaned,
                                flags=re.I,
                            )
                        elif uv_high:
                            cleaned = re.sub(
                                r"(VayboMeter:\s*\d+(?:[\.,]\d+)?/10\s+—\s*).*$",
                                r"\1с оговорками; высокий УФ.",
                                cleaned,
                                flags=re.I,
                            )
                        out.append(cleaned)
                    else:
                        out.append(score)
                    replaced = True
                continue
            out.append(line)
        return "\n".join(out)
    if "🕒 Лучшее окно:" in v2_text:
        return _inject_after_anchor(v2_text, score, ("🕒 Лучшее окно:",))
    if "🌡 Ощущается:" in v2_text:
        return _inject_after_anchor(v2_text, score, ("🌡 Ощущается:",))
    return _inject_after_anchor(v2_text, score, ("🏙️ Калининград",))


def _inject_evening_score(v2_text: str, mode: str) -> str:
    if mode.startswith("morn") or not _env_on("EVENING_VAYBOMETER_SCORE"):
        return v2_text
    if any("VayboMeter" in line and "/10" in line for line in str(v2_text or "").splitlines()):
        return v2_text
    return _insert_before_anchor(v2_text, _kld_evening_score_line(v2_text), ("🎯 <b>Уверенность", "🎯"))


def _inject_morning_smart_plan(v2_text: str, mode: str) -> str:
    if not (mode.startswith("morn") and _env_on("MORNING_SMART_PLAN")):
        return v2_text
    if not _kld_core_weather_available(v2_text):
        return _replace_plan(v2_text, _KLD_MISSING_CORE_PLAN)
    return _replace_plan(v2_text, _kld_smart_plan_line(v2_text))


def _apply_format_v2_safe_postprocess(v2_raw: str, raw_msg: str, legacy_text: str, mode: str) -> str:
    out = _apply_morning_raw_context(v2_raw, raw_msg, mode)
    out = _inject_morning_feels(out, mode)
    out = _inject_morning_best_window(out, mode)
    out = _inject_morning_score(out, mode)
    out = _inject_evening_score(out, mode)
    out = _inject_sensor_line(out, raw_msg or legacy_text)
    out = _apply_format_v2_test_polish(out)
    out = _apply_confidence_polish(out)
    out = _insert_main_nuance(out)
    out = _apply_editorial_voice(out, mode)
    out = _apply_astro_cleanup(out)
    out = _apply_score_conclusion(out)
    out = _inject_morning_smart_plan(out, mode)
    out = _apply_compact(out)
    out = _finalize_kld_morning_safe_text(out, raw_msg, legacy_text, mode)
    if mode.startswith("morn"):
        out = _remove_invalid_best_window(out)
        out = _soften_private_sensor_wording(out)
    else:
        out = _finalize_kld_evening_safe_text(out, mode)
    return out


def resolve_chat_id(args_chat: str, to_test: bool) -> Union[int, str]:
    chat = (args_chat or "").strip()
    if chat:
        try:
            return int(chat)
        except Exception:
            return chat
    if to_test:
        chat = os.getenv("CHANNEL_ID_TEST", "").strip()
        if not chat:
            raise SystemExit("--to-test задан, но CHANNEL_ID_TEST не определён")
        try:
            return int(chat)
        except Exception:
            return chat
    raise SystemExit("Safe runner refuses production send. Use --to-test or --chat-id explicitly.")


class _TodayPatch:
    def __init__(self, base_date: pendulum.DateTime):
        self.base_date = base_date
        self._orig_today = None
        self._orig_now = None

    def __enter__(self):
        self._orig_today = pendulum.today
        self._orig_now = pendulum.now

        def _fake(dt: pendulum.DateTime, tz_arg=None):
            return dt.in_tz(tz_arg) if tz_arg else dt

        pendulum.today = lambda tz_arg=None: _fake(self.base_date, tz_arg)  # type: ignore[assignment]
        pendulum.now = lambda tz_arg=None: _fake(self.base_date, tz_arg)    # type: ignore[assignment]
        logging.info("Дата зафиксирована как %s (%s)", self.base_date.to_datetime_string(), self.base_date.timezone_name)
        return self

    def __exit__(self, *args):
        if self._orig_today:
            pendulum.today = self._orig_today  # type: ignore[assignment]
        if self._orig_now:
            pendulum.now = self._orig_now      # type: ignore[assignment]
        return False


async def main() -> None:
    parser = argparse.ArgumentParser(description="Safe post builder for Kaliningrad VayboMeter")
    parser.add_argument("--mode", choices=["morning", "evening"], default=os.getenv("POST_MODE", "evening"))
    parser.add_argument("--date", default=os.getenv("WORK_DATE", ""))
    parser.add_argument("--for-tomorrow", action="store_true")
    parser.add_argument("--to-test", action="store_true")
    parser.add_argument("--chat-id", default="")
    parser.add_argument("--format-v2", action="store_true", help="Build scenario-style FORMAT_V2 text after legacy sanitizing.")
    parser.add_argument("--send", action="store_true", help="Actually send to CHANNEL_ID_TEST / --chat-id. Omit for dry-run.")
    parser.add_argument("--no-test-label", action="store_true", help="Do not prepend the 'Test safe post' label when sending.")
    args = parser.parse_args()

    mode = (args.mode or "evening").strip().lower()
    os.environ["POST_MODE"] = mode
    use_format_v2 = bool(args.format_v2 or _env_on("FORMAT_V2"))
    os.environ["FORMAT_V2"] = "1" if use_format_v2 else "0"
    day_offset = 0 if mode == "morning" else 1
    os.environ["DAY_OFFSET"] = str(day_offset)
    os.environ["ASTRO_OFFSET"] = str(day_offset)
    if mode == "morning":
        os.environ.setdefault("SHOW_AIR", "1")
        os.environ.setdefault("SHOW_SPACE", "1")
        os.environ.setdefault("SHOW_SCHUMANN", "1")
    else:
        os.environ["SHOW_AIR"] = "0"
        os.environ["SHOW_SPACE"] = "0"
        os.environ["SHOW_SCHUMANN"] = "0"

    tz = pendulum.timezone(TZ_STR)
    base_date = pendulum.parse(args.date).in_tz(tz) if args.date else pendulum.now(tz)
    if args.for_tomorrow:
        base_date = base_date.add(days=1)

    with _TodayPatch(base_date):
        raw_msg = build_message(
            region_name="Калининградская область",
            sea_label=SEA_LABEL,
            sea_cities=SEA_CITIES_ORDERED,
            other_label=OTHER_LABEL,
            other_cities=OTHER_CITIES_ALL,
            tz=TZ_STR,
            mode=mode,
        )

    legacy_result = sanitize_post_text(raw_msg)
    final_result = legacy_result
    final_label = "SAFE MESSAGE"

    if use_format_v2:
        from format_v2 import build_format_v2
        v2_raw = build_format_v2("Калининградская область", mode, legacy_result.text)
        v2_raw = _apply_format_v2_safe_postprocess(v2_raw, raw_msg, legacy_result.text, mode)
        final_result = sanitize_post_text(v2_raw)
        final_text = _finalize_kld_morning_safe_text(final_result.text, raw_msg, legacy_result.text, mode)
        if final_text != final_result.text:
            final_result = type(final_result)(text=final_text, issues=final_result.issues)
        final_label = "FORMAT_V2 MESSAGE"
        print("\n===== FORMAT_V2 RAW BEGIN =====\n")
        print(v2_raw)
        print("\n===== FORMAT_V2 RAW END =====\n")
        print("\n===== FORMAT_V2 SAFETY SUMMARY =====\n")
        print(validation_summary(final_result))

    chunks = split_telegram_text(final_result.text)

    print("\n===== RAW MESSAGE BEGIN =====\n")
    print(raw_msg)
    print("\n===== RAW MESSAGE END =====\n")
    print("\n===== LEGACY SAFETY SUMMARY =====\n")
    print(validation_summary(legacy_result))
    print(f"\n===== {final_label} BEGIN =====\n")
    print(final_result.text)
    print(f"\n===== {final_label} END =====\n")

    if not args.send:
        logging.info("SAFE DRY-RUN: отправка пропущена, format_v2=%s, chunks=%d", use_format_v2, len(chunks))
        return

    if not TOKEN_KLG:
        raise SystemExit("TELEGRAM_TOKEN_KLG не задан")
    chat_id = resolve_chat_id(args.chat_id, args.to_test)
    bot = Bot(token=TOKEN_KLG)
    for idx, chunk in enumerate(chunks, start=1):
        if args.no_test_label:
            text = chunk
        else:
            prefix = f"<b>Test safe post {idx}/{len(chunks)}</b>\n" if len(chunks) > 1 else "<b>Test safe post</b>\n"
            text = prefix + chunk
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=constants.ParseMode.HTML,
            disable_web_page_preview=True,
        )
    logging.info("SAFE TEST sent: chat=%s chunks=%d format_v2=%s", chat_id, len(chunks), use_format_v2)


if __name__ == "__main__":
    asyncio.run(main())
