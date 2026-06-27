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
        if s.startswith(("•", "🌙", "✅", "💚", "⚫")):
            out.append(s)
    return out


def _is_moon_phase_line(line: str) -> bool:
    s = _plain(line)
    if not s.startswith("🌙"):
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
    detail = ", ".join(x for x in (phase, sign) if x)
    if pct:
        detail += f" ({pct})"
    return f"🌙 {detail}".strip()


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


def _astro_block(lines: list[str], *, morning: bool) -> list[str]:
    details = _astro(lines)
    sunset = next((x.strip() for x in lines if x.strip().startswith("🌇 Закат")), "")
    sunrise = next((x.strip() for x in lines if x.strip().startswith("🌅 Рассвет")), "")
    moon_source = next((x for x in details if _is_moon_phase_line(x.strip())), "")
    if not (sunrise or sunset or moon_source or details):
        return []

    title = "🌅 <b>Солнце и ритм дня</b>" if morning else "🌅 <b>Солнце и ритм завтрашнего дня</b>"
    out = [title]
    if not morning and sunrise:
        out.append(sunrise)
    if sunset:
        out.append(sunset)
    moon = _moon_line(moon_source) if moon_source else ""
    if moon:
        out.append(moon)

    plus_source = next((x.strip() for x in details if x.strip().startswith("💚 В плюсе:")), "")
    period_source = next((x.strip() for x in details if x.strip().startswith("🌙 В этот период")), "")
    voc_source = next((x.strip() for x in details if x.strip().startswith("⚫")), "")
    if plus_source:
        out.append(plus_source)
    else:
        out.append(_astro_plus(moon, [x for x in details if x != moon_source]))
    for extra in (period_source, voc_source):
        if extra and extra not in out and len(out) < 5:
            out.append(extra)
    return out[:5]


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
            continue
        else:
            out.append(s.replace("гидрокостюм шорти 2 мм", "короткий гидрокостюм 2 мм"))
    return out


def _common_sup_water_line(lines: list[str]) -> str:
    text = "\n".join(lines)
    if "SUP" not in text and "гидрокостюм" not in text and "шорти" not in text:
        return ""
    suit = "короткий гидрокостюм 2 мм"
    m = re.search(r"((?:короткий\s+)?гидрокостюм\s*[^•\n]*мм|shorty\s*2\s*мм)", text, flags=re.I)
    if m:
        suit = m.group(1).strip()
    suit = suit.replace("гидрокостюм шорти 2 мм", "короткий гидрокостюм 2 мм")
    suit = suit.replace("шорти 2 мм", "короткий гидрокостюм 2 мм")
    return f"🏄 SUP/вода: только опытным, короткая сессия; {suit}. Главный риск — порывы ветра."


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


def _max_wind_ms(text: str) -> float | None:
    values: list[float] = []
    for m in re.finditer(r"(?:порывы\s*(?:до\s*)?)?(\d+(?:[\.,]\d+)?)\s*м/с", text, flags=re.I):
        try:
            values.append(float(m.group(1).replace(",", ".")))
        except Exception:
            continue
    return max(values) if values else None


def _evening_flags(lines: list[str], *, storm: str) -> dict[str, bool]:
    text = "\n".join(lines)
    max_temp = _max_temperature_c(text)
    max_wind = _max_wind_ms(text)
    return {
        "storm": bool(storm) or _has_any(text, ("шторм", "предупреждение")),
        "rain": _has_any(text, ("дожд", "морось", "ливн", "осад")),
        "wind": _has_any(text, ("порыв", "сильный ветер", "шторм")) or (isinstance(max_wind, (int, float)) and max_wind >= 8),
        "waves": _has_any(text, ("волна", "волн", "🌊")) and _has_any(text, ("0.8 м", "0.9 м", "1.0 м", "1 м", "1.1 м", "1.2 м")),
        "contrast": _has_any(text, ("тёплые города", "холодные города", "восток", "внутри области", "контраст")) or (isinstance(max_temp, (int, float)) and max_temp >= 25),
        "local": _has_any(text, ("локаль", "местами", "неравномер", "по области", "проверить утром")),
        "chill": _has_any(text, ("свеже", "холод", "прохлад", "ветровка")),
    }


def _evening_main_scenario(flags: dict[str, bool], score_line: str) -> str:
    if flags["storm"]:
        return "🧭 Главное завтра: главный фактор — ветер, порывы и осторожность у воды."
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
        return "⚠️ Нюанс: пирсы, открытый берег и водные активности — только после проверки фактического ветра."
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
    if flags["storm"] or flags["rain"] or flags["local"]:
        return "🎯 Уверенность: температура высокая; ветер/осадки лучше проверить утром."
    return ""


def _evening_plan(flags: dict[str, bool]) -> str:
    if flags["storm"]:
        return "✅ План завтра: короткий маршрут, защита от ветра/дождя и без риска на пирсах."
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


def _clean_safecast_line(line: str) -> str:
    s = str(line or "").strip()
    low = s.lower()
    if re.search(r"alert|опасн|превыш|🔴", low):
        return "🧪 Радиационный фон: высокий по частному датчику; проверьте динамику и официальные сообщения."
    if re.search(r"выше|повыш|⚠️|🟡", low):
        return "🧪 Фон по частному датчику: выше обычной точки наблюдения; смотрим динамику, не разовое значение."
    if re.search(r"норм|спокой|🟢", low):
        return "🧪 Фон по частному датчику: спокойно."
    return "🧪 Фон по частному датчику: есть свежий замер; смотрим динамику, не разовое значение."


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
    return cleaned


def _clean_morning_weather_line(line: str) -> str:
    s = _normalize_weather_line(line)
    s = s.replace("🏙️", "🏙")
    s = re.sub(r"^🏙\s*Калининград:", "🏙 Калининград —", s)
    s = re.sub(r"^Калининград:", "🏙 Калининград —", s)
    return s


def _final_plan_line(lines: list[str], has_warning: bool, has_rain: bool) -> str:
    for line in lines:
        s = line.strip()
        if s.startswith("✅ План:"):
            return s
        if s.startswith("✅ Сегодня:"):
            return "✅ План: " + s.split(":", 1)[1].strip()
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
    air = [x for x in _morning_pick(lines, ("🏭", "🌫", "🌬", "🌿", "🫁", "💨", "🟢", "🟡", "🔴", "ℹ️")) if "Safecast" not in x]
    quakes = _morning_pick(lines, ("🌍 Сейсмика 24ч:",))
    uv = _morning_pick(lines, ("☀️", "🌞", "🔥"))
    sunset = _morning_pick(lines, ("🌇",))
    astro = _astro_block(lines, morning=True)
    safecast = [x for x in _morning_pick(lines, ("🧪",)) if "Safecast" in x]
    space = [x for x in _morning_pick(lines, ("🧲",)) if "н/д" not in x]
    tags = _hashtags(lines, "#Калининград #погода #здоровье #сегодня #море")

    has_warning = bool(warning)
    has_rain = "дожд" in safe_legacy_text.lower() or "морось" in safe_legacy_text.lower()

    out: list[str] = [f"<b>🌅 Калининград сегодня{title_date}</b>"]

    for line in (score, scenario):
        if line and line not in out:
            out.append(line)
    if weather:
        out.append(_clean_morning_weather_line(weather))
    for line in (feels, best_window):
        if line:
            out.append(line)
    if main_nuance:
        out.append(main_nuance)
    elif warning:
        out.append("⚠️ " + warning)
    if fx:
        out.append(_clean_fx_line(fx[0]))
    if uv:
        out.append(_clean_uv_line(uv[0]))
    if air:
        out.append(air[0])
    for line in quakes:
        if line not in out:
            out.append(line)
    if astro:
        out.extend(astro)
    elif sunset:
        out.append(sunset[0])
    if space:
        out.append(_clean_kp_line(space[0]))
    if safecast:
        out.append(_clean_safecast_line(safecast[0]))

    out.append(_final_plan_line(lines, has_warning, has_rain))
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
    sup_water = _common_sup_water_line(raw_sea)
    warm_cold = _section_between(lines, "Тёплые города", ("🌅 Рассвет", "🌇 Закат", "Астрособытия", "Рекомендации"))
    astro = _astro_block(lines, morning=False)
    quakes = _morning_pick(lines, ("🌍 Сейсмика 24ч:",))
    score = _first_line_starts(lines, ("✨ VayboMeter завтра:", "✨ VayboMeter:"))
    flags = _evening_flags(lines, storm=storm)
    nuance = _evening_nuance(flags, bool(sea), bool(warm_cold))
    confidence = _evening_confidence_line(flags)

    out: list[str] = [f"<b>🌅 Калининградская область завтра{title_date}</b>"]

    if score:
        out.append(score)
    out.append(_evening_main_scenario(flags, score))
    if nuance:
        out.append(nuance)
    if confidence:
        out.append(confidence)
    out.append("")

    if kal:
        out.append(_clean_morning_weather_line(kal))
        out.append("")

    if storm:
        out.append("⚠️ <b>Предупреждение</b>")
        out.append(storm)
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
