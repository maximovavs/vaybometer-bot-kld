#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FORMAT_V2 text transformer for Kaliningrad VayboMeter posts."""
from __future__ import annotations

import re

from editorial_voice import build_evening_human_line, build_morning_human_line
from visibility_context import (
    visibility_air_penalty,
    visibility_condition_from_text,
    visibility_reason,
)
from weather_text import STORM_GUST_MS as _STORM_GUST_MS
from weather_text import clause_has_confirmed_storm as _clause_has_confirmed_storm
from weather_text import extract_max_gust_ms as _max_gust_ms
from weather_text import extract_max_wind_ms as _max_wind_ms
from weather_text import has_confirmed_storm_word as _has_explicit_storm_text
from weather_text import split_clauses as _split_storm_clauses


def _is_sep(line: str) -> bool:
    s = line.strip()
    return bool(s) and set(s) <= {"—", "-", "─"}


def _plain(line: str) -> str:
    return re.sub(r"</?b>", "", str(line or "")).strip()


def _fmt_num(value: float) -> str:
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:.1f}".rstrip("0").rstrip(".")


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


def _first_line_starts(lines: list[str], prefixes: tuple[str, ...]) -> str:
    for line in lines:
        s = line.strip()
        if s.startswith(prefixes):
            return s
    return ""


def _first_line_contains(lines: list[str], word: str) -> str:
    for line in lines:
        s = line.strip()
        low = s.lower()
        if s.startswith("#"):
            break
        if "доброе утро" in low or s.startswith("🌾"):
            continue
        if not (s.startswith("⚠️") or "предупреждение" in low):
            continue
        if word.lower() not in low or "погода на завтра" in low:
            continue
        # Clause-aware: a negation/hedge in one clause of the line must not
        # discard a genuine confirmation in another clause, e.g. "Риск шторма
        # невысок, но штормовое предупреждение действует у моря."
        if any(_clause_has_confirmed_storm(clause) for clause in _split_storm_clauses(s)):
            return s
    return ""


def _normalize_weather_line(line: str) -> str:
    s = str(line or "").strip()
    s = re.sub(r"\s*•\s*[—-]\s*•\s*", " • ", s)
    s = re.sub(r"\s*•\s*[—-]\s*(?=•|$)", "", s)
    s = re.sub(r"\bпорывы\s+до\s+(\d+)\s*м/с\s*(\d+)\s*м/с\b", r"порывы до \1\2 м/с", s, flags=re.I)
    s = re.sub(r"\bпорывы\s*[—-]\s*(\d+(?:[\.,]\d+)?)(?![\d\.,])(?:\s*м/с)?", r"порывы до \1 м/с", s, flags=re.I)
    s = re.sub(r"\bпорывы\s+до\s+(\d+(?:[\.,]\d+)?)(?![\d\.,])(?:\s*м/с)?", r"порывы до \1 м/с", s, flags=re.I)
    s = re.sub(r"[💧🔷🔹]\s*(?=\d{3,4}\s*гПа)", "давл. ", s, flags=re.I)
    s = re.sub(r"[💧🔷🔹]\s*давл\.", "давл.", s, flags=re.I)
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
    out: list[str] = []
    capture = False
    for line in lines:
        s = line.strip()
        if "Астрособытия" in s:
            capture = True
            continue
        if not capture:
            continue
        if _is_sep(s) or s.startswith(("🧲", "🧪", "🔎", "✅ Сегодня", "#")):
            break
        if s.startswith(("•", "🌙", "🌕", "🌔", "🌖", "✅", "💚", "⚫", "⚠️", "➿", "✨")):
            out.append(s)
    return out


def _is_moon_phase_line(line: str) -> bool:
    s = _plain(line)
    if not s.startswith(("🌙", "🌑", "🌒", "🌓", "🌔", "🌕", "🌖", "🌗", "🌘")):
        return False
    if re.search(r"\(\d{1,3}%\)", s):
        return True
    if any(word in s.lower() for word in ("луна", "серп", "четверть", "полнолу", "новолу", "растущ", "убыва")):
        return True
    return bool(re.search(r"[♈♉♊♋♌♍♎♏♐♑♒♓]", s))


def _sentence(text: str) -> str:
    s = re.sub(r"^[^0-9A-Za-zА-Яа-яЁё]+", "", _plain(text))
    s = re.sub(r"^(?:В целом|Астроритм|Сегодня)\s*:\s*", "", s, flags=re.I)
    s = re.sub(r"\s+", " ", s).strip(" .;—-")
    if not s:
        return ""
    return s[0].upper() + s[1:] + "."


def _moon_line(line: str) -> str:
    s = _plain(line)
    s = re.sub(r"^🌙\s*", "", s).strip()
    s = re.sub(r"^[🟡⚪⚫️🌑🌒🌓🌔🌕🌖🌗🌘]\s*", "", s).strip()
    pct = ""
    m_pct = re.search(r"\((\d{1,3}%)\)", s)
    if m_pct:
        pct = m_pct.group(1)
        s = (s[:m_pct.start()] + s[m_pct.end():]).strip()
    m_sign = re.search(r"([♈♉♊♋♌♍♎♏♐♑♒♓](?:\s+[А-Яа-яЁё]+)?)", s)
    sign = m_sign.group(1).strip() if m_sign else ""
    phase = (s[:m_sign.start()] + s[m_sign.end():]).strip() if m_sign else s
    phase = re.sub(r"\s*[•,]\s*$", "", phase).strip()
    phase = re.sub(r"\s+,", ",", phase)
    phase_low = phase.lower()
    if pct:
        pct_num_match = re.search(r"\d{1,3}", pct)
        pct_num = int(pct_num_match.group(0)) if pct_num_match else 0
    else:
        pct_num = 0
    if "полнолу" in phase_low:
        moon_emoji = "🌕"
        phase_text = "Почти полная Луна" if 90 <= pct_num < 97 else "Полнолуние"
    elif "новолу" in phase_low:
        moon_emoji = "🌑"
        phase_text = "Новолуние"
    elif "растущ" in phase_low and "серп" in phase_low:
        moon_emoji = "🌒"
        phase_text = "Растущий серп"
    elif "растущ" in phase_low:
        moon_emoji = "🌔"
        phase_text = phase
    elif "убыва" in phase_low:
        moon_emoji = "🌖"
        phase_text = phase
    else:
        moon_emoji = "🌙"
        phase_text = phase
    detail = phase_text
    if sign:
        detail += f" в {sign}"
    if pct:
        detail += f" — {pct} освещённости."
    return f"{moon_emoji} {detail}".strip()


def _astro_plus(moon: str, details: list[str]) -> str:
    useful = [
        _sentence(x)
        for x in details
        if x.strip().startswith(("•", "💚"))
        and not re.search(r"отлож|избег|неблагоприят|⛔", x, flags=re.I)
    ]
    useful = [x.rstrip(".") for x in useful if x]
    useful = [(x[0].lower() + x[1:]) if x and x[0].isalpha() else x for x in useful]
    sign_map = {
        "♍": "порядок, здоровье и аккуратное планирование",
        "Дева": "порядок, здоровье и аккуратное планирование",
        "♌": "творчество, самопрезентация и тёплое общение",
        "Лев": "творчество, самопрезентация и тёплое общение",
        "♋": "дом, восстановление и семья",
        "Рак": "дом, восстановление и семья",
        "♎": "баланс, красота и договорённости",
        "Весы": "баланс, красота и договорённости",
    }
    plus = next((value for key, value in sign_map.items() if key in moon), None)
    if useful:
        directions = useful[:2]
        if len(directions) < 2 and plus:
            directions.append(plus)
        return "💚 В плюсе: " + "; ".join(directions) + "."
    return f"💚 В плюсе: {plus or 'спокойные планы, восстановление и прогулки'}."


def _normalize_voc_line(line: str, date_s: str = "") -> str:
    s = str(line or "").strip()
    if not s:
        return ""
    s = re.sub(r"^⚫️?\s*", "⚫️ ", s)
    s = re.sub(r"^⚫️\s*VoC\s*:", "⚫️ VoC:", s, flags=re.I)
    m = re.search(r"(?<!\d)(\d{2}:\d{2})\s*[–-]\s*(\d{2}:\d{2})(?!\d)", s)
    if not m or not date_s:
        return s
    start_t, end_t = m.group(1), m.group(2)
    if end_t > start_t:
        return s
    dm = re.match(r"(\d{2})\.(\d{2})\.(\d{4})", date_s)
    if not dm:
        return s
    import datetime as _dt

    base = _dt.date(int(dm.group(3)), int(dm.group(2)), int(dm.group(1)))
    end_date = base + _dt.timedelta(days=1)
    interval = f"{base:%d.%m} {start_t}–{end_date:%d.%m} {end_t}"
    suffix = s[m.end():].strip()
    if suffix in ("", "."):
        suffix = "."
    elif not suffix.startswith(("—", "-", "–")):
        suffix = " " + suffix
    else:
        suffix = " " + suffix
    return f"⚫️ VoC: {interval}{suffix}"


def _astro_block(lines: list[str], *, morning: bool, date_s: str = "") -> list[str]:
    details = _astro(lines)
    sunset = next((x.strip() for x in lines if x.strip().startswith("🌇 Закат")), "")
    sunrise = next((x.strip() for x in lines if x.strip().startswith("🌅 Рассвет")), "")
    moon_source = next((x for x in details if _is_moon_phase_line(x.strip())), "")
    if not (sunrise or sunset or moon_source or details):
        return []

    title = "🌇 <b>Солнце, Луна и ритм дня</b>" if morning else "🌇 <b>Солнце, Луна и ритм завтрашнего дня</b>"
    out = [title]
    if not morning and sunrise:
        out.append(sunrise)
    if sunset:
        out.append(sunset)
    moon = _moon_line(moon_source) if moon_source else ""
    if moon:
        out.append(moon)

    plus_source = next((x.strip() for x in details if x.strip().startswith("💚 В плюсе:")), "")
    illumination_source = next(
        (x.strip() for x in details if x.strip().startswith("✨") and re.search(r"%|освещ", x, flags=re.I)),
        "",
    )
    general_source = next((x.strip() for x in details if "Общий фон" in x), "")
    period_source = next((x.strip() for x in details if x.strip().startswith("🌙 В этот период")), "")
    voc_source = next((x.strip() for x in details if x.strip().startswith("⚫") and not _is_noop_voc(x)), "")
    if voc_source:
        voc_source = _normalize_voc_line(voc_source, date_s)
    if illumination_source and illumination_source not in out:
        out.append(illumination_source)
    if general_source and general_source not in out:
        out.append(general_source)
    if plus_source:
        out.append(plus_source)
    else:
        out.append(_astro_plus(moon, [x for x in details if x != moon_source]))
    if not morning:
        if voc_source and voc_source not in out and len(out) < 8:
            out.append(voc_source)
        if period_source and period_source not in out and len(out) < 8:
            out.append(period_source)
        return out[:8]

    extras = (period_source, voc_source)
    for extra in extras:
        if extra and extra not in out and len(out) < 7:
            out.append(extra)
    return out[:8]


def _is_noop_voc(line: str) -> bool:
    s = _plain(line)
    m = re.search(r"VoC:?\s*(\d{2}:\d{2})\s*[–-]\s*(\d{2}:\d{2})", s, flags=re.I)
    return bool(m and m.group(1) == m.group(2))


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
        if _is_water_sport_recommendation_line(s):
            continue
        parts = [part.strip() for part in s.split("•")]
        clean_parts: list[str] = []
        for part in parts:
            p = part.strip()
            water_match = re.fullmatch(r"🌊\s*(\d+(?:[\.,]\d+)?)(?:\s*°?\s*C)?", p, flags=re.I)
            if water_match:
                p = f"🌊 {_fmt_num(float(water_match.group(1).replace(',', '.')))}°C"
            wave_match = re.fullmatch(r"(\d+(?:[\.,]\d+)?)\s*м", p, flags=re.I)
            if wave_match:
                p = f"волна {_fmt_num(float(wave_match.group(1).replace(',', '.')))} м"
            clean_parts.append(p)
        out.append(" • ".join(part for part in clean_parts if part))
    return out


def _is_water_sport_recommendation_line(line: str) -> bool:
    s = str(line or "").strip()
    low = s.lower()
    if not any(word in low for word in ("sup", "сап", "сёрф", "серф", "кайт", "винг", "винд", "гидрокостюм", "shorty")):
        return False
    return bool(re.search(r"^(?:[🏄🛶🧜‍♂️⚠️✅]|[-•])|Отлично:|SUP:|С[ёе]рф:|Кайт|Винг|Винд", s, flags=re.I))


def _range_from_values(values: list[float]) -> tuple[float, float] | None:
    if not values:
        return None
    return min(values), max(values)


def _format_measure_range(bounds: tuple[float, float] | None, unit: str) -> str:
    if not bounds:
        return ""
    low, high = bounds
    if round(low, 1) == round(high, 1):
        return f"{_fmt_num(low)} {unit}"
    return f"{_fmt_num(low)}–{_fmt_num(high)} {unit}"


def _wave_range_m(text: str) -> tuple[float, float] | None:
    values: list[float] = []
    for m in re.finditer(r"(\d+(?:[\.,]\d+)?)\s*[–—-]\s*(\d+(?:[\.,]\d+)?)\s*м(?!\s*/\s*с)", text, flags=re.I):
        for raw in (m.group(1), m.group(2)):
            try:
                values.append(float(raw.replace(",", ".")))
            except Exception:
                pass
    for m in re.finditer(r"(?<!/)(\d+(?:[\.,]\d+)?)\s*м(?!\s*/\s*с)", text, flags=re.I):
        try:
            value = float(m.group(1).replace(",", "."))
        except Exception:
            continue
        if 0 <= value <= 5:
            values.append(value)
    return _range_from_values(values)


def _water_range_c(text: str) -> tuple[float, float] | None:
    values: list[float] = []
    for m in re.finditer(r"🌊\s*(\d+(?:[\.,]\d+)?)(?:\s*°?\s*C)?", text, flags=re.I):
        try:
            value = float(m.group(1).replace(",", "."))
        except Exception:
            continue
        if 5 <= value <= 30:
            values.append(value)
    return _range_from_values(values)


def _common_sup_water_line(lines: list[str], *, has_storm: bool = False) -> str:
    raw_text = "\n".join(lines)
    text = "\n".join(line for line in lines if not _is_water_sport_recommendation_line(line))
    has_water_sport = re.search(r"\b(?:SUP|Кайт|Винг|Винд|С[ёе]рф)\b|гидрокостюм|шорти|shorty", raw_text, flags=re.I)
    wave_range = _wave_range_m(text)
    if not has_water_sport:
        return ""
    max_wind = _max_wind_ms(text)
    # The storm-tier water guidance keys on a confirmed storm or a real gust at
    # the threshold — never on average wind alone. Strong average wind still
    # makes the guidance strict, but via the "risky" branch below, not the
    # storm branch (contract: water caution may be strict on wind, but is not
    # labelled a storm just because the average wind is high).
    max_gust = _max_gust_ms(text)
    stormy = has_storm or (isinstance(max_gust, (int, float)) and max_gust >= _STORM_GUST_MS)
    if stormy:
        wave = _format_measure_range(wave_range, "м")
        wave_part = f"волна {wave}, но " if wave else ""
        return (
            f"🏄 Сёрф: только опытным; {wave_part}выход только после проверки конкретного спота и предупреждений.\n"
            "🛶 SUP: на открытой воде не рекомендован; рассматривать только защищённую акваторию после фактической проверки условий."
        )
    if wave_range and 0.8 <= wave_range[1] <= 2.0 and (not isinstance(max_wind, (int, float)) or max_wind < 15):
        return "🏄 Сёрф: есть рабочие окна по волне; проверить конкретный спот."
    risky = _has_any(text, ("порыв", "дожд", "морось", "ливн", "осад", "шторм", "только опытным")) or (
        isinstance(max_wind, (int, float)) and max_wind >= 8
    )
    if risky:
        return "🏄 Вода/ветер: только опытным; короткая сессия после фактической проверки условий. Экипировку выбирать по длительности сессии и индивидуальной переносимости воды."
    return "🏄 SUP/вода: короткая сессия; экипировку выбирать по длительности сессии, ветру и индивидуальной переносимости воды."


def _has_any(text: str, words: tuple[str, ...]) -> bool:
    low = _plain(text).lower()
    return any(word in low for word in words)


def _max_temperature_c(text: str) -> float | None:
    values: list[float] = []
    for m in re.finditer(r"(-?\d+(?:[\.,]\d+)?)\s*/\s*-?\d+(?:[\.,]\d+)?\s*°C", text):
        try:
            values.append(float(m.group(1).replace(",", ".")))
        except Exception:
            continue
    return max(values) if values else None


def _morning_kaliningrad_temps(lines: list[str]) -> tuple[float | None, float | None]:
    weather = _city_line(lines, "Калининград")
    m = re.search(r"(-?\d+(?:[\.,]\d+)?)\s*/\s*(-?\d+(?:[\.,]\d+)?)\s*°", _plain(weather))
    if not m:
        return None, None
    try:
        return float(m.group(1).replace(",", ".")), float(m.group(2).replace(",", "."))
    except Exception:
        return None, None


def _city_temperature_pairs(lines: list[str] | str) -> list[tuple[str, float, float | None]]:
    source_lines = str(lines or "").splitlines() if isinstance(lines, str) else lines
    out: list[tuple[str, float, float | None]] = []
    seen: set[str] = set()
    for line in source_lines:
        p = _plain(line).lstrip("• ").strip()
        p = re.sub(r"^Погода:\s*", "", p, flags=re.I)
        p = re.sub(r"^\s*(?:[-–—*•]+|\d+[.)])\s*", "", p)
        p = re.sub(r"^[^A-Za-zА-Яа-яЁё]+", "", p).strip()
        m = re.match(
            r"(?P<city>[А-ЯЁA-Z][^:—\n]{1,40}?)(?:[:—-]|\s+)\s*"
            r"(?P<hi>-?\d+(?:[\.,]\d+)?)\s*/\s*(?P<lo>-?\d+(?:[\.,]\d+)?)\s*°?\s*C",
            p,
        )
        if not m:
            continue
        try:
            city = m.group("city").strip()
            key = city.casefold()
            if key in seen:
                continue
            seen.add(key)
            out.append(
                (
                    city,
                    float(m.group("hi").replace(",", ".")),
                    float(m.group("lo").replace(",", ".")),
                )
            )
        except Exception:
            continue
    return out


def _normalize_city_temperature_pairs(values: object) -> list[tuple[str, float, float | None]]:
    out: list[tuple[str, float, float | None]] = []
    seen: set[str] = set()
    for item in values or []:  # type: ignore[union-attr]
        try:
            city = str(item[0]).strip()
            high = float(item[1])
            low = float(item[2]) if len(item) >= 3 and item[2] is not None else None
        except Exception:
            continue
        key = city.casefold()
        if not city or key in seen:
            continue
        seen.add(key)
        out.append((city, high, low))
    return out


def _format_region_temperature(value: float) -> str:
    return str(int(round(value))) if abs(value - round(value)) < 1e-9 else f"{value:.1f}".rstrip("0").rstrip(".")


def _region_tie_label(
    pairs: list[tuple[str, float, float | None]],
    target: float,
    value_index: int,
) -> str:
    cities = [item[0] for item in pairs if item[value_index] is not None and abs(float(item[value_index]) - target) < 1e-9]
    return ", ".join(cities[:2])


def _morning_region_context_from_pairs(values: object) -> str:
    pairs = _normalize_city_temperature_pairs(values)
    if len(pairs) < 2:
        return ""

    highs = [item[1] for item in pairs]
    warm_high = max(highs)
    cool_high = min(highs)
    has_day_contrast = abs(warm_high - cool_high) >= 0.1
    night_pairs = [item for item in pairs if item[2] is not None]
    cold_low = min(float(item[2]) for item in night_pairs) if len(night_pairs) >= 2 else None

    if not has_day_contrast:
        if len(night_pairs) < 2:
            return ""
        night_values = [float(item[2]) for item in night_pairs]
        if max(night_values) - min(night_values) < 0.1:
            return ""
        cold_cities = _region_tie_label(pairs, float(cold_low), 2)
        return (
            "🌡 По области: дневные температуры почти одинаковые; "
            f"холоднее всего ночью — {cold_cities} {_format_region_temperature(float(cold_low))}°."
        )

    warm_cities = _region_tie_label(pairs, warm_high, 1)
    cool_cities = _region_tie_label(pairs, cool_high, 1)
    day_part = (
        f"теплее всего — {warm_cities} {_format_region_temperature(warm_high)}°, "
        f"прохладнее — {cool_cities} {_format_region_temperature(cool_high)}°"
    )
    if cold_low is None:
        return f"🌡 По области: {day_part}."
    cold_cities = _region_tie_label(pairs, float(cold_low), 2)
    return (
        f"🌡 По области: днём {day_part}; ночью холоднее всего — "
        f"{cold_cities} {_format_region_temperature(float(cold_low))}°."
    )


# _max_wind_ms / _max_gust_ms are imported from weather_text (the single shared
# parser). _max_wind_ms is the average/max wind for the softer windy /
# water-caution / comfort cues; _max_gust_ms counts only "порыв …" values and
# is the ONLY input to the storm-gust threshold — a strong average wind must
# never be read as a storm gust.


def _has_actual_precipitation(text: str) -> bool:
    plain = _plain(text)
    low = plain.lower()
    if re.search(r"\b(?:дождь|дождя|дождём|дождем|дожди|дождик|дождев\w*|морось|ливень|ливни|ливнев\w*)\b", low):
        return True
    for line in plain.splitlines():
        s = line.lower()
        if "осад" not in s:
            continue
        if re.search(
            r"(?:провер\w*|уточн\w*|вероятност\w*|возможны\s+ли)[^.\n;:]{0,45}осад|осад[^.\n;:]{0,45}(?:провер\w*|уточн\w*)",
            s,
            flags=re.I,
        ):
            continue
        if re.search(r"(?:местами|ожида\w*|пройдут|будут|возможны|прогнозируются)[^.\n;:]{0,35}осад", s, flags=re.I):
            return True
    return False


def _valid_best_window_line(line: str) -> str:
    s = str(line or "").strip()
    if not s.startswith(("🕘 Лучшее окно", "🕒 Лучшее окно")):
        return ""
    m = re.search(r"\b(\d{1,2}):(\d{2})\s*[–—-]\s*(\d{1,2}):(\d{2})\b", s)
    if not m:
        return ""
    start = int(m.group(1)) * 60 + int(m.group(2))
    end = int(m.group(3)) * 60 + int(m.group(4))
    if end <= start or end - start < 120:
        return ""
    return re.sub(r"^🕒", "🕘", s)


def _morning_score_label(score: float) -> str:
    if score >= 8.0:
        return "очень хороший"
    if score >= 7.0:
        return "хороший"
    if score >= 6.0:
        return "нормальный, с поправками"
    if score >= 5.0:
        return "неустойчивый"
    return "сложный"


def _morning_score_value(flags: dict[str, object]) -> float:
    tmax = flags.get("max_temp")
    gust = flags.get("gust")
    wind = flags.get("wind_ms")
    uv = flags.get("uv")
    score = 10.0
    if flags.get("rain"):
        score -= 1.6
    elif flags.get("drizzle"):
        score -= 1.0
    if isinstance(gust, (int, float)):
        if gust >= 13:
            score -= 2.0
        elif gust >= 10:
            score -= 1.3
        elif gust >= 8:
            score -= 0.8
    elif isinstance(wind, (int, float)):
        if wind >= 8:
            score -= 1.0
        elif wind >= 5:
            score -= 0.6
    if isinstance(tmax, (int, float)):
        if tmax >= 35:
            score -= 1.2
        elif tmax >= 30:
            score -= 0.6
        elif tmax <= 14:
            score -= 1.0
        elif tmax <= 17:
            score -= 0.7
        elif tmax <= 21 and (flags.get("rain") or flags.get("drizzle")) and (flags.get("windy")):
            score -= 0.7
    if isinstance(uv, (int, float)) and uv >= 6:
        score -= 0.3
    score -= visibility_air_penalty(
        flags.get("visibility_condition", "clear"),
        flags.get("air_penalty", 0.0),
    )
    return max(1.0, min(10.0, score))


def _morning_score_reason(flags: dict[str, object], score: float) -> str:
    visibility = str(flags.get("visibility_condition") or "clear")
    if flags.get("rain") and flags.get("windy"):
        return "дождь и порывы снижают комфорт."
    if flags.get("rain"):
        return "дождь снижает комфорт."
    if flags.get("drizzle") and flags.get("windy"):
        return "морось и порывы снижают комфорт."
    if visibility != "clear":
        return visibility_reason(visibility) + "."
    if flags.get("heat") or (flags.get("uv_high") and flags.get("heat_word_ok")):
        return "с оговорками; жара и высокий УФ."
    if flags.get("uv_high") and flags.get("warm_uv_day"):
        return "с оговорками; тёплый день и высокий УФ."
    if flags.get("uv_high"):
        return "с оговорками; высокий УФ."
    if flags.get("windy"):
        return "порывы требуют поправки на маршрут."
    return _morning_score_label(score) + "."


def _clean_storm_warning_line(line: str) -> str:
    s = _plain(line)
    s = re.sub(r"^⚠️?\s*", "", s).strip()
    s = re.sub(r"^Предупреждение\s*:?\s*", "", s, flags=re.I)
    s = re.sub(r"^Штормовое(?:\s+предупреждение)?\s*:?\s*", "", s, flags=re.I).strip()
    gusts = [float(x.replace(",", ".")) for x in re.findall(r"порыв\w*\s*(?:до\s*)?(\d+(?:[\.,]\d+)?)\s*м/с", s, flags=re.I)]
    if gusts:
        return f"⚠️ Штормовое предупреждение: порывы до {_fmt_num(max(gusts))} м/с."
    detail = s.strip(" .")
    if not detail:
        return "⚠️ Штормовое предупреждение."
    detail = detail[0].lower() + detail[1:] if detail[0].isalpha() else detail
    return f"⚠️ Штормовое предупреждение: {detail}."


def _evening_flags(lines: list[str], *, storm: str) -> dict[str, bool]:
    effective_lines = []
    for line in lines:
        if line.strip().startswith("#"):
            break
        effective_lines.append(line)
    text = "\n".join(effective_lines)
    max_temp = _max_temperature_c(text)
    max_wind = _max_wind_ms(text)
    # storm_gust keys on the actual gust ("порыв …"), never on average wind:
    # "ветер 16 м/с, порывы до 14 м/с" is NOT a storm (gust 14 < threshold).
    max_gust = _max_gust_ms(text)
    storm_gust = isinstance(max_gust, (int, float)) and max_gust >= _STORM_GUST_MS
    visibility_condition = visibility_condition_from_text(text)
    return {
        "storm": bool(storm) or _has_explicit_storm_text(text) or storm_gust,
        "rain": _has_any(text, ("дожд", "морось", "ливн", "осад")),
        "temp_high": isinstance(max_temp, (int, float)) and max_temp >= 25,
        "temp_mild": isinstance(max_temp, (int, float)) and 18 <= max_temp < 25,
        "heat": isinstance(max_temp, (int, float)) and max_temp >= 35,
        "max_temp": max_temp,
        "max_wind": max_wind,
        "max_gust": max_gust,
        "storm_gust": storm_gust,
        "wind": _has_any(text, ("порыв", "сильный ветер", "шторм")) or (isinstance(max_wind, (int, float)) and max_wind >= 8),
        "waves": _has_any(text, ("волна", "волн", "🌊")) and _has_any(text, ("0.8 м", "0.9 м", "1.0 м", "1 м", "1.1 м", "1.2 м")),
        "contrast": _has_any(text, ("тёплые города", "холодные города", "восток", "внутри области", "контраст")) or (isinstance(max_temp, (int, float)) and max_temp >= 25),
        "local": _has_any(text, ("локаль", "местами", "неравномер", "по области", "проверить утром")),
        "chill": _has_any(text, ("свеже", "холод", "прохлад", "ветровка")),
        "visibility_condition": visibility_condition,
        "visibility_alert": visibility_condition != "clear",
    }


def _evening_main_scenario(flags: dict[str, bool], score_line: str) -> str:
    if flags["storm"]:
        return "🧭 Главное завтра: неустойчивое погодное окно; береговые планы лучше держать гибкими."
    if flags.get("visibility_condition") in {"dense_fog", "fog"}:
        return "🧭 Главное завтра: ранним утром местами туман; после его рассеивания важнее ветер и осадки."
    if flags.get("visibility_alert"):
        return "🧭 Главное завтра: утром местами ухудшится дальняя видимость; днём ориентируйся на основной прогноз."
    if flags["heat"] and flags["wind"]:
        return "🧭 Главное завтра: днём жара, у воды — ветер; активность лучше утром/вечером."
    if flags["heat"]:
        return "🧭 Главное завтра: главный фактор — дневная жара, дела лучше сместить на утро и вечер."
    if flags["rain"] and flags["wind"]:
        return "🧭 Главное завтра: влажный и ветреный день, особенно заметный на побережье."
    if flags["rain"]:
        return "🧭 Главное завтра: осадки важнее средних температур — держи маршрут гибким."
    if flags["wind"]:
        return "🧭 Главное завтра: у воды главный фактор — ветер и порывы."
    if flags["contrast"]:
        return "🧭 Главное завтра: заметен контраст побережья, Калининграда и востока области."
    if flags["chill"]:
        return "🧭 Главное завтра: день ощущается свежим, особенно у открытой воды."
    if score_line:
        reason = re.sub(r"^.*?—\s*", "", score_line).strip(" .")
        if reason:
            return "🧭 Главное завтра: " + reason[0].lower() + reason[1:] + "."
    return "🧭 Главное завтра: спокойный областной день без резких погодных акцентов."


def _evening_nuance(flags: dict[str, bool], has_sea: bool, has_region: bool) -> str:
    if flags["storm"]:
        # Report the actual gust ("порывы до N м/с"), not the average wind.
        gust = flags.get("max_gust")
        if isinstance(gust, (int, float)):
            return f"⚠️ Главный нюанс: на открытом берегу и пирсах порывы до {_fmt_num(gust)} м/с."
        return "⚠️ Главный нюанс: открытый берег, пирсы и водные активности — только после фактической проверки условий."
    if flags.get("visibility_condition") in {"dense_fog", "fog"}:
        return "⚠️ Главный нюанс: завтра утром осторожнее на дорогах, мостах и открытых участках у моря."
    if flags.get("visibility_condition") in {"mist", "reduced_visibility"}:
        return "⚠️ Нюанс: утром обзор местами короче обычного — на трассах лучше держать запас дистанции."
    if flags.get("visibility_condition") in {"dust_haze", "mixed_visibility"}:
        return "⚠️ Нюанс: утром воздух и дальняя видимость могут быть хуже обычного."
    if flags["heat"] and flags["wind"]:
        return ""
    if flags["rain"] and flags["wind"]:
        return "⚠️ Нюанс: у моря дождь и порывы ощущаются резче, чем в городе."
    if flags["rain"]:
        return "⚠️ Нюанс: дождь может идти неравномерно — лучше проверить радар утром."
    if flags["wind"] and has_sea:
        return "⚠️ Нюанс: на побережье ощущение меняют порывы, а не только градусы."
    if flags["waves"]:
        return "⚠️ Нюанс: волна и холодная вода важнее формальной температуры воздуха."
    if flags["contrast"] and has_region:
        return "⚠️ Нюанс: восток области может быть заметно теплее/холоднее берега."
    return ""


def _evening_confidence_line(flags: dict[str, bool]) -> str:
    if flags["storm"] or flags["rain"] or flags["local"] or flags.get("visibility_alert"):
        max_temp = flags.get("max_temp")
        if isinstance(max_temp, (int, float)) and max_temp >= 28:
            temp_part = "температура высокая"
        elif isinstance(max_temp, (int, float)) and max_temp >= 24:
            temp_part = "температура тёплая"
        else:
            temp_part = "по температуре спокойно"
        return f"🎯 Уверенность: {temp_part}; осадки и условия у воды лучше проверить утром."
    return ""


def _evening_plan(flags: dict[str, bool]) -> str:
    if flags["storm"]:
        return "✅ План завтра: короткий маршрут, непромокаемый слой и без риска на пирсах."
    if flags.get("visibility_condition") in {"dense_fog", "fog"}:
        return "✅ План завтра: утром учитывать плохую видимость; позже ориентироваться на ветер, осадки и температуру."
    if flags.get("visibility_condition") in {"mist", "reduced_visibility"}:
        return "✅ План завтра: утром оставить запас дистанции и времени; позже сверить ветер и осадки."
    if flags.get("visibility_condition") in {"dust_haze", "mixed_visibility"}:
        return "✅ План завтра: утром проверить воздух и дальность обзора; долгую прогулку скорректировать по факту."
    if flags["heat"] and flags["wind"]:
        return "✅ План завтра: основные дела утром/вечером, днём — тень и вода; у моря учитывать порывы."
    if flags["heat"]:
        return "✅ План завтра: активность утром/вечером, днём — вода, тень и паузы."
    if flags["rain"] and flags["wind"]:
        return "✅ План завтра: непромокаемый слой, закрытая обувь и запасной indoor-вариант."
    if flags["rain"]:
        return "✅ План завтра: зонт/дождевик и гибкое окно для прогулки."
    if flags["wind"]:
        return "✅ План завтра: у моря выбирать защищённые променады и сверить порывы утром."
    if flags["contrast"]:
        return "✅ План завтра: не усреднять область — берег, город и восток проверить отдельно."
    return "✅ План завтра: обычные дела и прогулки, с короткой проверкой ветра у воды утром."


def _limit_warm_cold(lines: list[str], per_group: int = 3) -> list[str]:
    out: list[str] = []
    item_count = 0
    in_group = False
    for line in lines:
        s = line.strip()
        if not s:
            continue
        if "Тёплые города" in s or "Холодные города" in s:
            if "Холодные города" in s:
                s = "❄️ <b>Самые прохладные ночи</b>"
            out.append(s)
            item_count = 0
            in_group = True
            continue
        if s.startswith("•"):
            if not in_group or item_count >= per_group:
                continue
            out.append(s)
            item_count += 1
            continue
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


def _first_morning_pick(lines: list[str], prefixes: tuple[str, ...]) -> str:
    picked = _morning_pick(lines, prefixes)
    return picked[0] if picked else ""


def _clean_uv_line(line: str) -> str:
    s = str(line or "").strip()
    s = re.sub(r"^☀️\s*УФ:\s*", "☀️ УФ ", s)
    s = re.sub(r"\s*•\s*(Низкий|Умеренный|Средний|Высокий|Очень высокий):\s*", ": ", s, flags=re.I)
    s = re.sub(r"\s{2,}", " ", s).strip()
    return s


def _clean_kp_line(line: str) -> str:
    s = str(line or "").strip()
    m = re.search(r"\b(?:Кр|Kp)\s*[:=]?\s*(\d+(?:[\.,]\d+)?)", s, flags=re.I)
    kp = None
    if m:
        try:
            kp = float(m.group(1).replace(",", "."))
        except Exception:
            kp = None
    alert = bool(re.search(r"бур|шторм|возмущ|alert|storm", s, flags=re.I))
    if kp is not None:
        mood = "спокойно" if kp < 4 and not alert else "возмущённо"
        return f"🧲 Космопогода: {mood}, Kp {kp:.1f}."
    return "🧲 Космопогода: спокойно."


def _sensor_age_ok(text: str) -> bool:
    low = str(text or "").lower()
    m = re.search(r"(\d+(?:[\.,]\d+)?)\s*(?:мин|m|min)\b", low, flags=re.I)
    if m:
        try:
            return float(m.group(1).replace(",", ".")) <= 24 * 60
        except Exception:
            return False
    m = re.search(r"(\d+(?:[\.,]\d+)?)\s*(?:ч|час|часа|часов|h|hour)", low, flags=re.I)
    if m:
        try:
            return float(m.group(1).replace(",", ".")) <= 24
        except Exception:
            return False
    return bool(re.search(r"\b\d{1,2}:\d{2}\b|обнов|замер|🕓|timestamp|ts", low, flags=re.I))


def _clean_safecast_line(line: str) -> str:
    s = str(line or "").strip()
    low = s.lower()
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
    has_unit = bool(re.search(r"μsv/h|µsv/h|usv/h|мкзв/ч|мкз/ч", low, flags=re.I))
    if not (value_match and has_unit and baseline_match and _sensor_age_ok(s)):
        return ""
    try:
        value = float(value_match.group(1).replace(",", "."))
        baseline = float(baseline_match.group(1).replace(",", "."))
    except Exception:
        return ""
    if re.search(r"critical|alert|опасн|🔴", low) and value >= 0.30:
        interpretation = "выше контрольного уровня; проверьте динамику и официальные сообщения"
    elif value - baseline >= 0.02:
        interpretation = "немного выше локального фона"
    elif baseline - value >= 0.02:
        interpretation = "ниже локального фона"
    else:
        interpretation = "около локального фона"
    return (
        f"🧪 Частный датчик радиации: {value:.2f} μSv/h, обычно {baseline:.2f} — "
        f"{interpretation}."
    )


def _clean_fx_line(line: str) -> str:
    s = str(line or "").strip()

    def repl(match: re.Match[str]) -> str:
        code = match.group(1)
        raw_value = match.group(2).replace(",", ".")
        arrow_delta = match.group(3)
        raw_delta = match.group(4)
        try:
            value = f"{float(raw_value):.2f}"
        except Exception:
            value = raw_value
        if arrow_delta:
            delta = arrow_delta.replace("−", "↓")
        elif raw_delta is None:
            delta = "→0.00"
        else:
            raw = raw_delta.replace("−", "-").replace(",", ".")
            try:
                d = float(raw)
                if d > 0:
                    delta = f"↑{abs(d):.2f}"
                elif d < 0:
                    delta = f"↓{abs(d):.2f}"
                else:
                    delta = "→0.00"
            except Exception:
                delta = raw_delta
        return f"{code} {value} ₽ {delta}"

    pattern = (
        r"\b(USD|EUR|CNY)\s*:?\s*(\d+(?:[\.,]\d+)?)\s*₽"
        r"(?:\s*([↑↓→][+-]?\d+(?:[\.,]\d+)?))?"
        r"(?:\s*\(([-−+]?\d+(?:[\.,]\d+)?)\))?"
    )
    cleaned = re.sub(pattern, repl, s)
    cleaned = cleaned.replace("−", "↓")
    cleaned = re.sub(r"\+\s*(\d)", r"↑\1", cleaned)
    cleaned = re.sub(r"^💱\s*Курсы(?:\s*\([^)]*\))?\s*:", "💱 Курсы:", cleaned, flags=re.I)
    cleaned = re.sub(r"\s*•\s*", " · ", cleaned)
    return cleaned


def _clean_morning_weather_line(line: str) -> str:
    s = _normalize_weather_line(line)
    s = s.replace("🏙️", "🏙")
    s = re.sub(r"^🏙\s*Калининград:", "🏙 Калининград —", s)
    s = re.sub(r"^Калининград:", "🏙 Калининград —", s)
    return s


def _clean_evening_score_line(line: str, flags: dict[str, bool]) -> str:
    s = str(line or "").strip()
    if flags.get("storm"):
        if flags.get("storm_gust") and flags.get("rain"):
            replacement = "— неустойчивый день: локальные осадки и штормовые порывы."
        elif flags.get("storm_gust"):
            replacement = "— день с повышенной осторожностью: штормовые порывы."
        elif flags.get("rain") and flags.get("wind"):
            replacement = "— неустойчивый день: локальные осадки и порывы у моря."
        else:
            replacement = "— день с повышенной осторожностью: ветер у воды."
        s = re.sub(r"—\s*[^.\n]*\.?", replacement, s, flags=re.I)
    elif flags.get("heat") and flags.get("wind"):
        s = re.sub(r"—\s*отлично\b[^.\n]*\.?", "— жарко; у моря порывы.", s, flags=re.I)
        s = re.sub(r"—\s*хорошо\b[^.\n]*\.?", "— жарко; у моря порывы.", s, flags=re.I)
    elif flags.get("heat"):
        s = re.sub(r"—\s*отлично\b[^.\n]*\.?", "— днём жарко; активность лучше утром/вечером.", s, flags=re.I)
    return s


def _uv_value(line: str) -> float | None:
    m = re.search(r"\bУФ\s*:?\s*(\d+(?:[\.,]\d+)?)", line or "", flags=re.I)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", "."))
    except Exception:
        return None


_KLD_MISSING_CORE_LINE = "⚠️ Данные по Калининграду обновились не полностью; проверяем источник."
_KLD_MISSING_CORE_PLAN = "✅ План: перед выходом проверьте актуальный прогноз; пост обновится после восстановления данных."


def _morning_core_weather_available(weather_line: str) -> bool:
    p = _plain(_normalize_weather_line(weather_line or "")).replace("\u00a0", " ")
    if not p:
        return False
    has_temp = bool(
        re.search(r"-?\d+(?:[\.,]\d+)?\s*/\s*-?\d+(?:[\.,]\d+)?\s*°\s*C?", p, flags=re.I)
    )
    has_wind = bool(
        re.search(r"(?:💨|ветер)[^.\n;•]{0,40}\d+(?:[\.,]\d+)?\s*м/с", p, flags=re.I)
        or re.search(r"\d+(?:[\.,]\d+)?\s*м/с", p, flags=re.I)
    )
    return has_temp and has_wind


def _morning_flags(lines: list[str], uv_line: str) -> dict[str, bool]:
    text = "\n".join(lines)
    max_temp = _max_temperature_c(text)
    max_wind = _max_wind_ms(text)
    uv = _uv_value(uv_line)
    kal_tmax, kal_tmin = _morning_kaliningrad_temps(lines)
    rain = _has_actual_precipitation(text)
    drizzle = bool(re.search(r"\bморось|drizzle\b", _plain(text).lower(), flags=re.I))
    wind_avg = None
    weather = _city_line(lines, "Калининград")
    m_wind = re.search(r"(?:💨|ветер)[^0-9]{0,16}(\d+(?:[\.,]\d+)?)\s*м/с", _plain(weather), flags=re.I)
    if m_wind:
        try:
            wind_avg = float(m_wind.group(1).replace(",", "."))
        except Exception:
            wind_avg = None
    windy = _has_any(text, ("порыв", "сильный ветер", "шторм")) or (
        isinstance(max_wind, (int, float)) and max_wind >= 8
    )
    visibility_condition = visibility_condition_from_text(text)
    aqi_match = re.search(r"\bAQI\s*(\d+(?:[\.,]\d+)?)", _plain(text), flags=re.I)
    aqi = None
    if aqi_match:
        try:
            aqi = float(aqi_match.group(1).replace(",", "."))
        except Exception:
            aqi = None
    return {
        "heat": isinstance(max_temp, (int, float)) and max_temp >= 35,
        "heat_word_ok": isinstance(max_temp, (int, float)) and max_temp >= 28,
        "warm_uv_day": isinstance(max_temp, (int, float)) and 25 <= max_temp < 28,
        "warm": isinstance(max_temp, (int, float)) and 28 <= max_temp < 35,
        "uv_high": isinstance(uv, (int, float)) and uv >= 6,
        "wind": windy,
        "windy": windy,
        "rain": rain,
        "drizzle": drizzle and not rain,
        "max_temp": kal_tmax if isinstance(kal_tmax, (int, float)) else max_temp,
        "min_temp": kal_tmin,
        "gust": max_wind,
        "wind_ms": wind_avg,
        "uv": uv,
        "visibility_condition": visibility_condition,
        "visibility_alert": visibility_condition != "clear",
        "air_penalty": 0.8 if isinstance(aqi, (int, float)) and aqi > 80 else 0.0,
    }


def _kld_voice_conditions(lines: list[str], *, flags: dict[str, bool] | None = None, uv_line: str = "") -> dict[str, object]:
    text = "\n".join(lines)
    max_temp = _max_temperature_c(text)
    max_wind = _max_wind_ms(text)
    source_flags = flags or {}
    uv = _uv_value(uv_line or text)
    return {
        "max_temp": source_flags.get("max_temp", max_temp),
        "uv": uv,
        "uv_high": bool(source_flags.get("uv_high")) or isinstance(uv, (int, float)) and uv >= 6,
        "warm": bool(source_flags.get("warm") or source_flags.get("warm_uv_day") or source_flags.get("temp_high")),
        "wind": bool(source_flags.get("wind")) or isinstance(max_wind, (int, float)) and max_wind >= 8,
        "gust": max_wind,
        "rain": bool(source_flags.get("rain")) or _has_actual_precipitation(text),
    }


def _morning_score_line(source: str, flags: dict[str, bool]) -> str:
    score = _morning_score_value(flags)
    reason = _morning_score_reason(flags, score)
    if source:
        s = source.strip()
        s = re.sub(r"^✨\s*VayboMeter\s+сегодня\s*:", "✨ VayboMeter:", s, flags=re.I)
        if flags.get("rain") or flags.get("drizzle") or flags.get("visibility_alert") or (
            flags.get("windy") and not (flags.get("heat") or flags.get("uv_high"))
        ):
            s = re.sub(
                r"VayboMeter:\s*\d+(?:[\.,]\d+)?/10\s+—\s*[^.\n]*\.?",
                f"VayboMeter: {score:.1f}/10 — {_morning_score_label(score)}; {reason}",
                s,
                flags=re.I,
            )
        elif flags["heat"] or (flags["uv_high"] and flags.get("heat_word_ok")):
            s = re.sub(
                r"—\s*[^.\n]*\.?",
                "— с оговорками; жара и высокий УФ.",
                s,
                flags=re.I,
            )
        elif flags["uv_high"] and flags.get("warm_uv_day"):
            s = re.sub(
                r"—\s*[^.\n]*\.?",
                "— с оговорками; тёплый день и высокий УФ.",
                s,
                flags=re.I,
            )
        elif flags["uv_high"]:
            replacement = "— с оговорками; высокий УФ."
            s = re.sub(r"—\s*[^.\n]*\.?", replacement, s, flags=re.I)
        return s
    return f"✨ VayboMeter: {score:.1f}/10 — {_morning_score_label(score)}; {reason}"


def _morning_feels_line(source: str, flags: dict[str, bool]) -> str:
    if (flags.get("rain") or flags.get("drizzle")) and flags.get("windy"):
        return "🌡 Ощущается: прохладно и сыро; на открытых местах заметно свежее."
    if flags.get("rain") or flags.get("drizzle"):
        return "🌡 Ощущается: прохладно и сыро."
    if source:
        return source.strip()
    if flags["heat"]:
        return "🌡 Ощущается: жарко; на солнце высокая нагрузка."
    if flags["warm"]:
        return "🌡 Ощущается: тепло; активность лучше без перегруза."
    return "🌡 Ощущается: комфортно для обычных дел."


def _morning_best_window_line(source: str, flags: dict[str, bool]) -> str:
    return _valid_best_window_line(source)


def _morning_main_nuance_line(source: str, warning: str, flags: dict[str, bool]) -> str:
    visibility = str(flags.get("visibility_condition") or "clear")
    if visibility in {"dense_fog", "fog"}:
        return "⚠️ Главный нюанс: утром осторожнее на дорогах, развязках, мостах и открытых участках."
    if visibility in {"mist", "reduced_visibility"}:
        return "⚠️ Главный нюанс: утром обзор местами короче обычного; на дорогах держать запас дистанции."
    if visibility in {"dust_haze", "mixed_visibility"}:
        return "⚠️ Главный нюанс: утром воздух и дальняя видимость могут быть хуже обычного."
    if source:
        return source.strip()
    if flags.get("windy"):
        return "⚠️ Главный нюанс: у воды порывы ощущаются сильнее, чем в городе."
    if flags["heat"] and flags["uv_high"]:
        return "⚠️ Главный нюанс: жара и УФ важнее формальной облачности."
    if flags["uv_high"]:
        return "⚠️ Главный нюанс: высокий УФ днём; планировать активность лучше без перегруза."
    if warning:
        return "⚠️ " + warning
    return ""


def _morning_plan_line(lines: list[str], flags: dict[str, bool], has_warning: bool, has_rain: bool) -> str:
    visibility = str(flags.get("visibility_condition") or "clear")
    if visibility in {"dense_fog", "fog"}:
        return "✅ План: утром учитывать плохую видимость; позже ориентироваться на ветер, осадки и температуру."
    if visibility in {"mist", "reduced_visibility"}:
        return "✅ План: утром оставить запас дистанции и времени; позже сверить ветер и осадки."
    if visibility in {"dust_haze", "mixed_visibility"}:
        return "✅ План: утром проверить воздух и дальность обзора; прогулку скорректировать по факту."
    if (flags.get("rain") or flags.get("drizzle")) and flags.get("windy"):
        return "✅ План: дождевик и закрытая обувь; у моря выбирать защищённый маршрут."
    if flags.get("rain") or flags.get("drizzle"):
        return "✅ План: дождевик или зонт, закрытая обувь; дела лучше короткими выходами."
    if flags["heat"] or (flags["uv_high"] and flags.get("heat_word_ok")):
        return "✅ План: дела и прогулка утром/вечером; днём — вода, тень, SPF и короткие выходы."
    if flags["uv_high"] and flags.get("warm_uv_day"):
        return "✅ План: дела и прогулка утром/вечером; днём — SPF, вода, тень и паузы."
    if flags["uv_high"]:
        return "✅ План: SPF, очки/кепка и короткие паузы в тени."
    return _final_plan_line(lines, has_warning, has_rain)


def _morning_region_context_line(lines: list[str], flags: dict[str, bool]) -> str:
    del flags
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("🌡 По области:") and any(
            marker in stripped
            for marker in ("днём теплее всего", "дневные температуры почти одинаковые", "теплее всего —")
        ):
            return stripped
    return _morning_region_context_from_pairs(_city_temperature_pairs(lines))


def _morning_human_line(lines: list[str], flags: dict[str, object], date_s: str) -> str:
    if (flags.get("rain") or flags.get("drizzle")) and flags.get("windy"):
        return "💬 По-человечески: прохладно и сыро; для обычных дел нормально в непромокаемой одежде."
    if flags.get("rain") or flags.get("drizzle"):
        return "💬 По-человечески: день сырой; обычные дела лучше держать короткими выходами."
    return build_morning_human_line("Калининград", date_s or "today", _kld_voice_conditions(lines, flags=flags, uv_line=""))


def _quake_line_publishable(line: str) -> bool:
    low = _plain(line).lower()
    if "сейсмика" not in low:
        return False
    if "не найдено" in low or "данные временно" in low or "региональные данные" in low:
        return False
    return bool(re.search(r"\bm\d+(?:[\.,]\d+)?|микрособыт|слабое событие|сильнейшее|⚠️", low, flags=re.I))


def _clean_baltic_line(line: str) -> str:
    s = _normalize_weather_line(line)
    water = ""
    wave = ""
    water_match = re.search(r"(?:вода|море)[^\d-]{0,20}(-?\d+(?:[\.,]\d+)?)\s*°?\s*C?", s, flags=re.I)
    if not water_match:
        water_match = re.search(r"🌊\s*(-?\d+(?:[\.,]\d+)?)(?:\s*(?:°?\s*C|•|$))", s, flags=re.I)
    if water_match:
        water = water_match.group(1).replace(",", ".")
    wave_match = re.search(r"(?:волна|wave)[^\d]{0,20}(\d+(?:[\.,]\d+)?)\s*м", s, flags=re.I)
    if not wave_match:
        wave_match = re.search(r"•\s*(\d+(?:[\.,]\d+)?)\s*м\b", s)
    if wave_match:
        wave = wave_match.group(1).replace(",", ".")
    if water or wave:
        parts = []
        if water:
            parts.append(f"вода {water}°C")
        if wave:
            parts.append(f"волна {wave} м")
        return "🌊 Балтика: " + "; ".join(parts) + "; у воды свежее, ветер ощущается заметнее."
    return s


def _morning_sea_lines(lines: list[str]) -> list[str]:
    out: list[str] = []
    for line in lines:
        s = line.strip()
        if s.startswith("🌊") and "Морские города" not in s:
            out.append(_clean_baltic_line(s))
        elif s.startswith(("Балтика:", "Море:")):
            out.append(_clean_baltic_line("🌊 " + s))
        elif "🌊" in s and "°C" in s and "Морские города" not in s:
            out.append(_clean_baltic_line(s))
    if out:
        return out[:1]
    return []


def _final_plan_line(lines: list[str], has_warning: bool, has_rain: bool) -> str:
    forbidden_generic = (
        "избегайте стрессовых новостей",
        "лёгкая растяжка перед сном",
        "легкая растяжка перед сном",
        "планируйте поездки заранее",
    )
    for line in lines:
        s = line.strip()
        if s.startswith("✅ План:"):
            if any(phrase in s.lower() for phrase in forbidden_generic):
                continue
            return s
        if s.startswith("✅ Сегодня:"):
            plan = "✅ План: " + s.split(":", 1)[1].strip()
            if any(phrase in plan.lower() for phrase in forbidden_generic):
                continue
            return plan
    tips = _tips_fallback(has_warning, has_rain)
    return "✅ План: " + " ".join(tips)


def build_morning_format_v2(region_name: str, safe_legacy_text: str) -> str:
    """Compact morning post: current weather + FX + air + UV + space weather + 2 practical tips."""
    lines = [x.rstrip() for x in str(safe_legacy_text or "").splitlines() if x.strip()]
    date_s = _date_from_title(safe_legacy_text)
    title_date = f" ({date_s})" if date_s else ""

    weather = _city_line(lines, "Калининград")
    warning = _first_line_contains(lines, "Шторм") or _first_line_contains(lines, "шторм")
    score = _first_morning_pick(lines, ("✨ VayboMeter", "✨"))
    scenario = _first_morning_pick(lines, ("🧭",))
    feels = _first_morning_pick(lines, ("🌡 Ощущается", "🌡️ Ощущается"))
    best_window = _first_morning_pick(lines, ("🕘 Лучшее окно",))
    main_nuance = _first_morning_pick(lines, ("⚠️ Главный нюанс",))
    fx = _morning_pick(lines, ("💱",))
    air = [x for x in _morning_pick(lines, ("🏭", "🌬", "🌿", "🫁", "💨", "🟢", "🟡", "🔴", "ℹ️")) if "Safecast" not in x]
    visibility = _morning_pick(lines, ("🌫 Видимость:",))
    quakes = _morning_pick(lines, ("🌍 Сейсмика",))
    uv = _morning_pick(lines, ("☀️", "🌞", "🔥"))
    uv_line = _clean_uv_line(uv[0]) if uv else ""
    sunset = _morning_pick(lines, ("🌇",))
    astro = _astro_block(lines, morning=True)
    safecast = _morning_pick(lines, ("🧪",))
    sea = _morning_sea_lines(lines)
    space = [x for x in _morning_pick(lines, ("🧲",)) if "н/д" not in x]
    tags = _hashtags(lines, "#Калининград #погода #здоровье #сегодня #море")
    flags = _morning_flags(lines, uv_line)

    has_warning = bool(warning)
    has_rain = _has_actual_precipitation(safe_legacy_text)

    out: list[str] = [f"<b>🌅 Калининград сегодня{title_date}</b>"]

    if not _morning_core_weather_available(weather):
        out.append(_KLD_MISSING_CORE_LINE)
        if astro:
            out.append("")
            out.extend(astro)
        elif sunset:
            out.append(sunset[0])
        out.append(_KLD_MISSING_CORE_PLAN)
        out.append(tags)
        return "\n".join(out).strip()

    for line in (_morning_score_line(score, flags), scenario):
        if line and line not in out:
            out.append(line)
    human_line = _morning_human_line(lines, flags, date_s)
    if human_line:
        out.append(human_line)
    region_context = _morning_region_context_line(lines, flags)
    if region_context:
        out.append(region_context)
    if weather:
        out.append(_clean_morning_weather_line(weather))
    out.append(_morning_feels_line(feels, flags))
    best_line = _morning_best_window_line(best_window, flags)
    if best_line:
        out.append(best_line)
    nuance = _morning_main_nuance_line(main_nuance, warning, flags)
    if nuance:
        out.append(nuance)
    if uv_line:
        out.append(uv_line)
    if air:
        out.append(air[0])
    if visibility:
        out.append(visibility[0])
    if safecast:
        sensor_line = _clean_safecast_line(safecast[0])
        if sensor_line:
            out.append(sensor_line)
    if space:
        out.append(_clean_kp_line(space[0]))
    for line in sea:
        if line not in out:
            out.append(line)
    for line in [line for line in quakes if _quake_line_publishable(line)]:
        if line not in out:
            out.append(line)
    if fx:
        out.append(_clean_fx_line(fx[0]))
    if astro:
        if fx and out and out[-1].strip():
            out.append("")
        out.extend(astro)
    elif sunset:
        if fx and out and out[-1].strip():
            out.append("")
        out.append(sunset[0])

    out.append(_morning_plan_line(lines, flags, has_warning, has_rain))
    out.append(tags)
    return "\n".join(out).strip()


def build_evening_format_v2(region_name: str, safe_legacy_text: str) -> str:
    lines = [x.rstrip() for x in str(safe_legacy_text or "").splitlines()]
    date_s = _date_from_title(safe_legacy_text)
    title_date = f" ({date_s})" if date_s else ""

    kal = _city_line(lines, "Калининград")
    storm = _first_line_contains(lines, "Шторм") or _first_line_contains(lines, "шторм")
    raw_sea = _section_after(lines, "Морские города")
    sea = _soften_sea_lines(raw_sea)
    warm_cold = _section_between(lines, "Тёплые города", ("🌅 Рассвет", "🌇 Закат", "Астрособытия", "Рекомендации"))
    astro = _astro_block(lines, morning=False, date_s=date_s)
    quakes = _morning_pick(lines, ("🌍 Сейсмика",))
    visibility = _morning_pick(lines, ("🌫 Видимость:",))
    score = _first_line_starts(lines, ("✨ VayboMeter завтра:", "✨ VayboMeter:"))
    flags = _evening_flags(lines, storm=storm)
    sup_water = _common_sup_water_line(raw_sea, has_storm=bool(flags.get("storm")))
    nuance = _evening_nuance(flags, bool(sea), bool(warm_cold))
    confidence = _evening_confidence_line(flags)

    out: list[str] = [f"<b>🌅 Калининградская область завтра{title_date}</b>"]

    if score:
        out.append(_clean_evening_score_line(score, flags))
    out.append(_evening_main_scenario(flags, score))
    if nuance:
        out.append(nuance)
    if visibility:
        out.append(visibility[0])
    human_line = build_evening_human_line("Калининград", date_s or "tomorrow", _kld_voice_conditions(lines, flags=flags))
    if human_line:
        out.append(human_line)
    if confidence:
        out.append(confidence)
    out.append("")

    if kal:
        out.append(_clean_morning_weather_line(kal))
        out.append("")

    if sea:
        out.append("🌊 <b>Морские города</b>")
        out.extend(sea)
        if sup_water:
            out.append(sup_water)
        out.append("")

    if warm_cold:
        out.append("🌡 <b>Внутри области</b>")
        out.extend(_limit_warm_cold(warm_cold))
        out.append("")

    if astro:
        out.extend(astro)
        out.append("")

    quakes = [line for line in quakes if _quake_line_publishable(line)]
    if quakes:
        for line in quakes:
            if line not in out:
                out.append(line)
        out.append("")

    out.append(_evening_plan(flags))
    out.append("#Калининград #погода #здоровье #море")
    return "\n".join(out).strip()


def build_format_v2(region_name: str, mode: str, safe_legacy_text: str) -> str:
    mode_s = (mode or "").strip().lower()
    if mode_s.startswith("morn"):
        return build_morning_format_v2(region_name, safe_legacy_text)
    return build_evening_format_v2(region_name, safe_legacy_text)
