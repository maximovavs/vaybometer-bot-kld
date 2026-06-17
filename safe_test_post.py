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

from post_common import build_message
from post_safety import sanitize_post_text, split_telegram_text, validation_summary

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
    return "отлично" if score >= 8.7 else "хорошо" if score >= 7 else "с оговорками" if score >= 5.5 else "бережный режим"


def _kld_weather_line(v2_text: str) -> str:
    lines = [x.strip() for x in str(v2_text or "").splitlines() if x.strip()]
    return next((x for x in lines if x.startswith("🏙️ Калининград")), "")


def _kld_conditions(v2_text: str) -> dict[str, float | bool | None]:
    weather = _kld_weather_line(v2_text)
    p = _plain(weather)
    uv_line = next((x.strip() for x in str(v2_text or "").splitlines() if x.strip().startswith("☀️")), "")
    air_line = next((x.strip() for x in str(v2_text or "").splitlines() if x.strip().startswith("🏭")), "")
    return {
        "tmax": _num(r"—\s*(-?\d+(?:[\.,]\d+)?)/", p),
        "tmin": _num(r"/(-?\d+(?:[\.,]\d+)?)\s*°", p),
        "wind": _num(r"💨\s*(\d+(?:[\.,]\d+)?)", p),
        "gust": _num(r"порывы\s+до\s*(\d+(?:[\.,]\d+)?)", p),
        "rain": any(w in p.lower() for w in ("дожд", "морось", "ливень")),
        "uv": _num(r"УФ\s*(\d+(?:[\.,]\d+)?)", uv_line),
        "aqi": _num(r"AQI\s*(\d+(?:[\.,]\d+)?)", air_line),
    }


def _kld_feels_line(v2_text: str) -> str:
    c = _kld_conditions(v2_text)
    tmax = c.get("tmax")
    tmin = c.get("tmin")
    wind = c.get("wind")
    gust = c.get("gust")
    has_rain = bool(c.get("rain"))

    parts: list[str] = []
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
    c = _kld_conditions(v2_text)
    tmax = c.get("tmax")
    wind = c.get("wind")
    gust = c.get("gust")
    has_rain = bool(c.get("rain"))
    windy = (isinstance(gust, (int, float)) and gust >= 7) or (isinstance(wind, (int, float)) and wind >= 3)

    if has_rain and windy:
        return "🕒 Лучшее окно: короткие выходы между дождём; у воды — по фактическому ветру."
    if has_rain:
        return "🕒 Лучшее окно: короткие выходы между дождём; для прогулки — защищённые маршруты."
    if windy:
        return "🕒 Лучшее окно: позднее утро и первая половина дня; у воды — по ветру."
    if isinstance(tmax, (int, float)) and tmax <= 17:
        return "🕒 Лучшее окно: позднее утро и первая половина дня; вечером будет свежее."
    return "🕒 Лучшее окно: позднее утро и время ближе к закату."


def _kld_smart_plan_line(v2_text: str) -> str:
    c = _kld_conditions(v2_text)
    wind = c.get("wind")
    gust = c.get("gust")
    has_rain = bool(c.get("rain"))
    windy = (isinstance(gust, (int, float)) and gust >= 7) or (isinstance(wind, (int, float)) and wind >= 3)
    uv = c.get("uv")

    if has_rain and windy:
        return "✅ План: дождевик/капюшон надёжнее зонта; закрытая обувь; выходы короткими блоками между дождём; у воды осторожнее с порывами."
    if has_rain:
        return "✅ План: зонт или дождевик, закрытая обувь; дела лучше короткими выходами между дождём."
    if windy:
        return "✅ План: ветровка/слой, у воды осторожнее с порывами; прогулку лучше в защищённых местах."
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

    score = 10.0
    reasons: list[str] = []
    if has_rain:
        score -= 1.4; reasons.append("дождь")
    if isinstance(gust, (int, float)):
        if gust >= 12:
            score -= 1.4; reasons.append("порывы")
        elif gust >= 10:
            score -= 1.1; reasons.append("порывы")
        elif gust >= 7:
            score -= 0.8; reasons.append("ветер у воды")
        if isinstance(wind, (int, float)) and wind >= 6:
            score -= 0.4; reasons.append("ветер")
    elif isinstance(wind, (int, float)) and wind >= 6:
        score -= 0.8; reasons.append("ветер")
    elif isinstance(wind, (int, float)) and wind >= 3:
        score -= 0.5; reasons.append("ветер")
    if isinstance(tmax, (int, float)):
        if tmax <= 14:
            score -= 1.0; reasons.append("прохладно")
        elif tmax <= 16:
            score -= 0.7; reasons.append("свежо")
        elif tmax <= 18:
            score -= 0.5; reasons.append("свежо")
    if isinstance(uv, (int, float)) and uv >= 6:
        score -= 0.3; reasons.append("УФ высокий")
    if isinstance(aqi, (int, float)) and aqi > 80:
        score -= 0.8; reasons.append("воздух похуже")

    score = max(1.0, min(10.0, score))
    label = _score_label(score)
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
    score = 10.0
    reasons: list[str] = []

    if "шторм" in low or "предупреждение" in low:
        score -= 1.8; reasons.append("предупреждение")
    if "дожд" in low or "морось" in low:
        score -= 1.1; reasons.append("осадки")
    if isinstance(max_gust, (int, float)):
        if max_gust >= 15:
            score -= 1.4; reasons.append("сильные порывы")
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

    score = max(1.0, min(10.0, score))
    label = _score_label(score)
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


def _apply_format_v2_test_polish(v2_text: str) -> str:
    if not _env_any("FORMAT_V2_POLISH", "FORMAT_V2_TEST_POLISH"):
        return v2_text
    text = _translate_shore_notes(v2_text)
    text = _downgrade_sup_lines(text)
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
    warning = any(x in low for x in ("шторм", "предупреждение"))
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
    if not _env_on("FORMAT_V2_MAIN_NUANCE") or "⚠️ Главный нюанс:" in v2_text:
        return v2_text
    return _inject_after_anchor(v2_text, _kld_main_nuance(v2_text), ("✨ VayboMeter завтра:", "✨ VayboMeter:"))


def _sensor_line_from_legacy(legacy_text: str) -> str:
    marker = "Safe" + "cast"
    for line in str(legacy_text or "").splitlines():
        if marker.lower() not in line.lower():
            continue
        s = line.strip()
        s = re.sub(r"^\s*🧪\s*", "", s)
        s = re.sub(r"\s*—\s*(?:🔴\s*)?высокий.*$", " — выше обычного по датчику; сверяем с динамикой и официальным фоном.", s, flags=re.I)
        if "—" not in s:
            s += " — сверяем с динамикой и официальным фоном."
        return "🧪 " + s
    return ""


def _inject_sensor_line(v2_text: str, legacy_text: str) -> str:
    if not _env_any("FORMAT_V2_SENSOR_LINE", "FORMAT_V2_TEST_SENSOR", "FORMAT_V2_TEST_SAFECAST"):
        return v2_text
    line = _sensor_line_from_legacy(legacy_text)
    if not line or line in v2_text:
        return v2_text
    return _insert_before_anchor(v2_text, line, ("🌙 <b>Астроритм", "✅ <b>Рекомендации", "📌 <b>Вывод"))


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
        if stripped.startswith(("🌙 <b>Астроритм", "☀️ <b>Солнце")):
            in_astro = True
            astro_details = 0
            out.append(line)
            continue
        if in_astro and stripped.startswith(("✅ <b>Рекомендации", "📌 <b>Вывод", "#")):
            in_astro = False
        if in_astro and stripped:
            if stripped.endswith("для первых") or stripped.endswith("и вдо…"):
                continue
            if "…" in stripped and len(stripped) > 80:
                continue
            if stripped.startswith("✅ В целом:"):
                line = "✅ Астроритм: благоприятный."
                stripped = line
            if not stripped.startswith(("🌅", "🌙", "✅", "💚")):
                continue
            astro_details += 1
            if astro_details > 3:
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
        if not replaced and line.strip().startswith("✅"):
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
    feels = _kld_feels_line(v2_text)
    if not feels:
        return v2_text
    return _inject_after_anchor(v2_text, feels, ("🏙️ Калининград",))


def _inject_morning_best_window(v2_text: str, mode: str) -> str:
    if not (mode.startswith("morn") and _env_on("MORNING_BEST_WINDOW")):
        return v2_text
    window = _kld_best_window_line(v2_text)
    if not window:
        return v2_text
    if "🌡 Ощущается:" in v2_text:
        return _inject_after_anchor(v2_text, window, ("🌡 Ощущается:",))
    return _inject_after_anchor(v2_text, window, ("🏙️ Калининград",))


def _inject_morning_score(v2_text: str, mode: str) -> str:
    if not (mode.startswith("morn") and _env_on("MORNING_VAYBOMETER_SCORE")):
        return v2_text
    score = _kld_score_line(v2_text)
    if "🕒 Лучшее окно:" in v2_text:
        return _inject_after_anchor(v2_text, score, ("🕒 Лучшее окно:",))
    if "🌡 Ощущается:" in v2_text:
        return _inject_after_anchor(v2_text, score, ("🌡 Ощущается:",))
    return _inject_after_anchor(v2_text, score, ("🏙️ Калининград",))


def _inject_evening_score(v2_text: str, mode: str) -> str:
    if mode.startswith("morn") or not _env_on("EVENING_VAYBOMETER_SCORE"):
        return v2_text
    return _insert_before_anchor(v2_text, _kld_evening_score_line(v2_text), ("🎯 <b>Уверенность", "🎯"))


def _inject_morning_smart_plan(v2_text: str, mode: str) -> str:
    if not (mode.startswith("morn") and _env_on("MORNING_SMART_PLAN")):
        return v2_text
    return _replace_plan(v2_text, _kld_smart_plan_line(v2_text))


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
        v2_raw = _inject_morning_feels(v2_raw, mode)
        v2_raw = _inject_morning_best_window(v2_raw, mode)
        v2_raw = _inject_morning_score(v2_raw, mode)
        v2_raw = _inject_evening_score(v2_raw, mode)
        v2_raw = _inject_sensor_line(v2_raw, legacy_result.text)
        v2_raw = _apply_format_v2_test_polish(v2_raw)
        v2_raw = _apply_confidence_polish(v2_raw)
        v2_raw = _insert_main_nuance(v2_raw)
        v2_raw = _apply_astro_cleanup(v2_raw)
        v2_raw = _apply_score_conclusion(v2_raw)
        v2_raw = _inject_morning_smart_plan(v2_raw, mode)
        v2_raw = _apply_compact(v2_raw)
        final_result = sanitize_post_text(v2_raw)
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
