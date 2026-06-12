#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FORMAT_V2 text transformer for Kaliningrad VayboMeter test posts."""
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
        if word.lower() in line.lower():
            return line.strip()
    return ""


def _city_line(lines: list[str], city: str) -> str:
    for line in lines:
        if city in line:
            return line.strip()
    return ""


def _astro(lines: list[str]) -> list[str]:
    sec = _section_after(lines, "Астрособытия")
    return [x for x in sec if x.startswith(("•", "🌙", "✅", "⚫"))][:3]


def _recommendations(lines: list[str]) -> list[str]:
    sec = _section_after(lines, "Рекомендации")
    return [x for x in sec if not _is_sep(x) and not x.startswith("#")][:3]


def build_format_v2(region_name: str, mode: str, safe_legacy_text: str) -> str:
    lines = [x.rstrip() for x in str(safe_legacy_text or "").splitlines()]
    date_s = _date_from_title(safe_legacy_text)
    title_date = f" ({date_s})" if date_s else ""

    kal = _city_line(lines, "Калининград")
    storm = _first_line_contains(lines, "Шторм") or _first_line_contains(lines, "шторм")
    sea = _section_after(lines, "Морские города")
    warm_cold = _section_between(lines, "Тёплые города", ("Астрособытия", "Рекомендации"))
    astro = _astro(lines)
    tips = _recommendations(lines)
    has_storm = bool(storm)
    has_rain = "дожд" in safe_legacy_text.lower()

    out: list[str] = [f"<b>🌅 Калининградская область завтра: берег и восток снова разные{title_date}</b>", ""]

    out.append("🧭 <b>Главный сценарий</b>")
    if has_storm:
        out.append("Главный фактор — ветер/порывы и осадки. У моря ощущение будет свежее и резче, чем в городе и внутри области.")
    elif has_rain:
        out.append("День прохладный и влажный: у моря свежее, в городе мягче, восток области может отличаться по температуре и осадкам.")
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
        out.append("🌙 <b>Луна и ритм дня</b>")
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
