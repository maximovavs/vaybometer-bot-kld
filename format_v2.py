#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FORMAT_V2 text transformer for Kaliningrad VayboMeter posts."""
from __future__ import annotations

import re


def _is_sep(line: str) -> bool:
    s = line.strip()
    return bool(s) and set(s) <= {"—", "-", "─"}


def _plain(line: str) -> str:
    return re.sub(r"</?b>", "", str(line or "")).strip()


def _date_from_title(text: str) -> str:
    m = re.search(r"\((\d{2}\.\d{2}\.\d{4})\)", text)
    return m.group(1) if m else ""


def _section_after(lines: list[str], marker: str) -> list[str]:
    out: list[str] = []
    capture = False
    for line in lines:
        if marker in line:
            capture = True
            continue
        if capture:
            if _is_sep(line):
                break
            if line.strip():
                out.append(line.strip())
    return out


def _section_between(lines: list[str], start_marker: str, stop_markers: tuple[str, ...]) -> list[str]:
    out: list[str] = []
    capture = False
    for line in lines:
        if start_marker in line:
            capture = True
            out.append(line.strip())
            continue
        if capture:
            if any(m in line for m in stop_markers):
                break
            if line.strip() and not _is_sep(line):
                out.append(line.strip())
    return out


def _first_line_contains(lines: list[str], word: str) -> str:
    for line in lines:
        s = line.strip()
        low = s.lower()
        if "без шторма" in low or "доброе утро" in low or s.startswith("🌾"):
            continue
        if not (s.startswith("⚠️") or "предупреждение" in low):
            continue
        if word.lower() in low and "погода на завтра" not in low:
            return s
    return ""


def _normalize_weather_line(line: str) -> str:
    s = str(line or "").strip()
    s = re.sub(r"\s*•\s*[—-]\s*•\s*", " • ", s)
    s = re.sub(r"\s*•\s*[—-]\s*(?=•|$)", "", s)
    s = re.sub(r"\bпорывы\s+до\s+(\d+)\s*м/с\s*(\d+)\s*м/с\b", r"порывы до \1\2 м/с", s, flags=re.I)
    s = re.sub(r"\bпорывы\s*[—-]\s*(\d+(?:[\.,]\d+)?)(?![\d\.,])(?:\s*м/с)?", r"порывы до \1 м/с", s, flags=re.I)
    s = re.sub(r"\bпорывы\s+до\s+(\d+(?:[\.,]\d+)?)(?![\d\.,])(?:\s*м/с)?", r"порывы до \1 м/с", s, flags=re.I)
    s = re.sub(r"\s{2,}", " ", s).strip()
    return s


def _city_line(lines: list[str], city: str) -> str:
    for line in lines:
        p = _plain(line)
        if p.startswith(f"Погода: 🏙️ {city}"):
            return _normalize_weather_line(line.strip().replace("Погода: ", ""))
        if p.startswith(f"🏙️ {city}:") or p.startswith(f"{city}:") or p.startswith(f"🏙️ {city} —"):
            return _normalize_weather_line(line.strip())
    return ""


def _astro(lines: list[str]) -> list[str]:
    sec = _section_after(lines, "Астрособытия")
    return [x for x in sec if x.startswith(("•", "🌙", "✅", "⚫"))][:2]


def _tips_fallback(has_storm: bool, has_rain: bool) -> list[str]:
    if has_storm:
        return [
            "🧥 У моря — ветровка/капюшон.",
            "🌊 Открытые пирсы лучше пропустить.",
        ]
    if has_rain:
        return [
            "☔ Возьми зонт или дождевик.",
            "👟 Обувь лучше закрытая.",
        ]
    return [
        "🧥 У моря пригодится лёгкая ветровка.",
        "🚶 Прогулки лучше по защищённым от ветра маршрутам.",
    ]


def _recommendations(lines: list[str], has_storm: bool, has_rain: bool) -> list[str]:
    """Use deterministic weather-specific recommendations."""
    return _tips_fallback(has_storm, has_rain)


def _soften_sea_lines(lines: list[str]) -> list[str]:
    out: list[str] = []
    for line in lines:
        s = line.strip()
        if "🧜‍♂️" in s and "SUP" in s:
            suit = ""
            m = re.search(r"(гидрокостюм[^•]*)", s)
            if m:
                suit = " • " + m.group(1).strip()
            out.append("🧜‍♂️ SUP: только для опытных и короткой сессии" + suit)
        else:
            out.append(s)
    return out


def _hashtags(lines: list[str], fallback: str) -> str:
    for line in reversed(lines):
        s = line.strip()
        if s.startswith("#"):
            return s
    return fallback


def _morning_pick(lines: list[str], prefixes: tuple[str, ...]) -> list[str]:
    return [x.strip() for x in lines if x.strip().startswith(prefixes)]


def _clean_uv_line(line: str) -> str:
    s = str(line or "").strip()
    s = re.sub(r"^☀️\s*УФ:\s*", "☀️ УФ ", s)
    s = re.sub(r"\s*•\s*(Низкий|Умеренный|Средний|Высокий|Очень высокий):\s*", ": ", s, flags=re.I)
    s = re.sub(r"\s{2,}", " ", s).strip()
    return s


def _clean_kp_line(line: str) -> str:
    s = str(line or "").strip()
    # Remove text assessment and stale minute marker after numeric Kp/Kr value.
    # Example: "Кр 0.3 (умеренно, 🕓 4 мин назад)" -> "Kp 0.3".
    s = re.sub(r"(\b(?:Кр|Kp)\s*\d+(?:[\.,]\d+)?)\s*\([^)]*\)", r"\1", s, flags=re.I)
    s = re.sub(r"\bКр\b", "Kp", s)
    s = re.sub(r"\s{2,}", " ", s).strip()
    return s


def build_morning_format_v2(region_name: str, safe_legacy_text: str) -> str:
    """Compact morning post: current weather + FX + air + UV + space weather + 2 practical tips."""
    lines = [x.rstrip() for x in str(safe_legacy_text or "").splitlines() if x.strip()]
    date_s = _date_from_title(safe_legacy_text)
    title_date = f" ({date_s})" if date_s else ""

    weather = _city_line(lines, "Калининград")
    warning = _first_line_contains(lines, "Шторм") or _first_line_contains(lines, "шторм")
    fx = _morning_pick(lines, ("💱",))
    air = [x for x in _morning_pick(lines, ("🏭", "🌫", "🌬", "🌿", "🫁", "💨", "🟢", "🟡", "🔴", "ℹ️")) if "Safecast" not in x]
    uv = _morning_pick(lines, ("☀️", "🌞", "🔥"))
    space = [x for x in _morning_pick(lines, ("🧲",)) if "н/д" not in x]
    tags = _hashtags(lines, "#Калининград #погода #здоровье #сегодня #море")

    has_warning = bool(warning)
    has_rain = "дожд" in safe_legacy_text.lower() or "морось" in safe_legacy_text.lower()

    out: list[str] = [f"<b>🌅 Калининград сегодня{title_date}</b>"]

    if weather:
        out.append(weather)
    if fx:
        out.append(fx[0])
    if warning:
        out.append("⚠️ " + warning)
    if uv:
        out.append(_clean_uv_line(uv[0]))
    if air:
        out.append(air[0])
    if space:
        out.append(_clean_kp_line(space[0]))

    tips = _tips_fallback(has_warning, has_rain)
    out.append("✅ " + " ".join(tips))
    out.append(tags)
    return "\n".join(out).strip()


def build_evening_format_v2(region_name: str, safe_legacy_text: str) -> str:
    lines = [x.rstrip() for x in str(safe_legacy_text or "").splitlines()]
    date_s = _date_from_title(safe_legacy_text)
    title_date = f" ({date_s})" if date_s else ""

    kal = _city_line(lines, "Калининград")
    storm = _first_line_contains(lines, "Шторм") or _first_line_contains(lines, "шторм")
    sea = _soften_sea_lines(_section_after(lines, "Морские города"))
    warm_cold = _section_between(lines, "Тёплые города", ("Астрособытия", "Рекомендации"))
    astro = _astro(lines)
    has_storm = bool(storm)
    has_rain = "дожд" in safe_legacy_text.lower()
    tips = _recommendations(lines, has_storm, has_rain)

    out: list[str] = [f"<b>🌅 Калининградская область завтра: берег, город и восток — разные сценарии{title_date}</b>", ""]

    out.append("🧭 <b>Главный сценарий</b>")
    if has_storm:
        out.append("Главный фактор — ветер/порывы и осадки. У моря ощущение будет свежее и резче, чем в городе и внутри области.")
    elif has_rain:
        out.append("День прохладный и влажный: у моря свежее, в Калининграде мягче, восток области может отличаться по температуре и осадкам.")
    else:
        out.append("Типичный областной контраст: побережье живёт морским ветром, Калининград — более мягким городским сценарием, восток области — своим температурным режимом.")
    out.append("")

    out.append("🎯 <b>Уверенность прогноза</b>")
    out.append("✅ Температура: высокая — общий диапазон надёжный.")
    out.append("🟡 Морской ветер: средняя — порывы и ощущение у воды лучше перепроверить утром.")
    out.append("🟡 Осадки: локально — дождь может идти неравномерно по области.")
    out.append("✅ Разница берег/внутри области: учитывать обязательно.")
    out.append("")

    if kal:
        out.append("🏙 <b>Калининград</b>")
        out.append(kal)
        out.append("")

    if storm:
        out.append("⚠️ <b>Предупреждение</b>")
        out.append(storm)
        out.append("")

    if sea:
        out.append("🌊 <b>Морские города</b>")
        out.extend(sea)
        out.append("")

    if warm_cold:
        out.append("🌡 <b>Внутри области</b>")
        out.extend(warm_cold)
        out.append("")

    out.append("🌊 <b>Морская поправка</b>")
    if has_storm:
        out.append("У воды ориентируйся не на температуру, а на ветер, порывы и волну. Для прогулок лучше короткий маршрут и защита от дождя/ветра.")
    else:
        out.append("На побережье одинаковые градусы ощущаются холоднее: ветер и влажность быстро меняют комфорт, особенно вечером.")
    out.append("")

    if astro:
        out.append("🌙 <b>Астроритм</b>")
        out.extend(astro)
        out.append("")

    if tips:
        out.append("✅ <b>Рекомендации</b>")
        out.extend(tips)
        out.append("")

    out.append("📌 <b>Вывод</b>")
    if has_storm:
        out.append("День лучше планировать с запасом: море — осторожно, город — по погоде, поездки по области — с проверкой дождя и порывов утром.")
    else:
        out.append("Главная идея — не усреднять область: берег, Калининград и восточные города завтра могут ощущаться как разные погодные сценарии.")
    out.append("")
    out.append("#Калининград #погода #здоровье #море")
    return "\n".join(out).strip()


def build_format_v2(region_name: str, mode: str, safe_legacy_text: str) -> str:
    mode_s = (mode or "").strip().lower()
    if mode_s.startswith("morn"):
        return build_morning_format_v2(region_name, safe_legacy_text)
    return build_evening_format_v2(region_name, safe_legacy_text)
