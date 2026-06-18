#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Safety helpers for VayboMeter publishing.

This module is intentionally conservative: it does not invent data and it
removes lines that look broken, stale, or low-trust before a post is sent.
"""
from __future__ import annotations

import html
import os
import re
from dataclasses import dataclass
from typing import List

TG_MESSAGE_LIMIT = 4096
_SAFE_CHUNK_LIMIT = 3800

_BROKEN_TAILS = {
    "освобо",
    "истинные",
    "призыва",
    "первых",
    "выс",
    "наступает",
}

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

_SHORE_RU = {
    "onshore": "к берегу",
    "offshore": "от берега",
    "cross": "вдоль берега",
}

_TEMP_PREFIXES = ("🥵", "😎", "😊", "😌", "🙄", "😮‍💨", "🥶", "🌤", "🌥")

_FORBIDDEN_PATTERNS = [
    (re.compile(r"\bKp\s+н/д\b", re.I), "Kp n/a"),
    (re.compile(r"\bКр\s+н/д\b", re.I), "Kp n/a"),
    (re.compile(r"/None\b", re.I), "/None artifact"),
    (re.compile(r"\bFull\s+Moon\b", re.I), "English moon phrase"),
    (re.compile(r"\bSagittarius\b", re.I), "English zodiac phrase"),
    (re.compile(r"#(?:К|здо)\b", re.I), "broken hashtag"),
]

_DROP_LINE_PATTERNS = [
    (re.compile(r"Освещ[её]нность\s*:\s*(?:н/д|—|-)", re.I), "empty moon illumination"),
    (re.compile(r"\bЛуна\s*[—-]\s*держи курс на простые", re.I), "generic moon placeholder"),
]

@dataclass
class SafetyResult:
    text: str
    issues: List[str]


def _env_on(name: str, default: bool = False) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return str(val).strip().lower() in ("1", "true", "yes", "on")


def _line_is_separator(line: str) -> bool:
    s = line.strip()
    return bool(s) and set(s) <= {"—", "-", "─"}


def _replace_shore_terms(line: str, issues: list[str]) -> str:
    def repl(match: re.Match[str]) -> str:
        d = match.group(1).upper()
        shore = match.group(2).lower()
        d_ru = _DIR_RU.get(d, d)
        shore_ru = _SHORE_RU.get(shore, shore)
        issues.append(f"translated shore note: ({d}/{shore})")
        return f"({d_ru}, {shore_ru})"

    return re.sub(
        r"\b\((N|NE|E|SE|S|SW|W|NW)/(onshore|offshore|cross)\)\b",
        repl,
        line,
        flags=re.I,
    )


def _normalize_line(line: str, issues: list[str] | None = None) -> str:
    issues = issues if issues is not None else []
    original = line
    line = line.rstrip()
    line = re.sub(r"\s+/None\b", "", line, flags=re.I)
    line = re.sub(r"\((?:N|NE|E|SE|S|SW|W|NW)?/?None\)", "", line, flags=re.I)
    line = _replace_shore_terms(line, issues)
    line = line.replace(" • —", "")
    line = line.replace(" • -", "")
    line = line.replace(" — —", " —")
    line = line.replace(" - -", " -")
    line = re.sub(r"\bпорывы\s+до\s+(\d+)\s*м/с\s*(\d+)\s*м/с\b", r"порывы до \1\2 м/с", line, flags=re.I)
    line = re.sub(r"\bпорывы\s*[—-]\s*(\d+(?:[\.,]\d+)?)(?![\d\.,])(?:\s*м/с)?", r"порывы до \1 м/с", line, flags=re.I)
    line = re.sub(r"\bпорывы\s+до\s+(\d+(?:[\.,]\d+)?)(?![\d\.,])(?:\s*м/с)?", r"порывы до \1 м/с", line, flags=re.I)
    line = re.sub(r"\s*•\s*[—-]\s*•\s*", " • ", line)
    line = re.sub(r"\s{2,}", " ", line)
    line = line.strip()
    line = re.sub(
        r"^✅\s*(?:В целом|Общий фон):\s*благоприятный день\.?$",
        "✅ Астроритм: благоприятный.",
        line,
        flags=re.I,
    )
    if original.strip() != line and not (issues and issues[-1].startswith("translated shore note")):
        issues.append(f"normalized line: {original.strip()[:120]}")
    return line


def _line_should_drop(line: str) -> tuple[bool, str | None]:
    stripped = line.strip()
    if not stripped:
        return False, None

    for rx, reason in _FORBIDDEN_PATTERNS:
        if rx.search(stripped):
            return True, reason

    for rx, reason in _DROP_LINE_PATTERNS:
        if rx.search(stripped):
            return True, reason

    last_word = re.sub(r"[^A-Za-zА-Яа-яЁё]+", "", stripped.split()[-1]).lower() if stripped.split() else ""
    if last_word in _BROKEN_TAILS:
        return True, f"clipped tail: {last_word}"

    return False, None


def _score_label(score: float) -> str:
    return "отлично" if score >= 8.7 else "хорошо" if score >= 7 else "с оговорками" if score >= 5.5 else "бережный режим"


def _cap_kld_morning_score(text: str) -> str:
    s = str(text or "")
    if "Калининград сегодня" not in s or "завтра" in s.lower():
        return s
    line = next((x.strip() for x in s.splitlines() if x.strip().startswith("✨ VayboMeter:") and "/10" in x), "")
    m = re.search(r"VayboMeter:\s*(\d+(?:[\.,]\d+)?)\s*/\s*10", line)
    if not m:
        return s
    score = float(m.group(1).replace(",", "."))
    low = s.lower()
    gusts: list[float] = []
    for raw in re.findall(r"порывы\s+до\s*(\d+(?:[\.,]\d+)?)", s, flags=re.I):
        try:
            gusts.append(float(raw.replace(",", ".")))
        except Exception:
            pass
    max_gust = max(gusts) if gusts else 0.0
    water_wind = "ветер у воды" in low or "у воды ветер" in low or max_gust >= 6
    cap = 10.0
    if any(x in low for x in ("морось", "дожд", "ливень")):
        cap = min(cap, 7.2)
    if "уф" in low and "высок" in low and water_wind:
        cap = min(cap, 8.3)
    if max_gust >= 8:
        cap = min(cap, 8.4)
    if water_wind or "свеж" in low:
        cap = min(cap, 8.6)
    if score <= cap:
        return s
    repl = re.sub(
        r"(VayboMeter:\s*)\d+(?:[\.,]\d+)?(/10\s+—\s*)[^;\.\n]+",
        rf"\g<1>{cap:.1f}\g<2>{_score_label(cap)}",
        line,
    )
    if water_wind and "ветер" not in repl.lower():
        repl = repl.rstrip(".") + ", у воды ветер заметнее."
    return s.replace(line, repl, 1)


def _kld_temp_emoji(t: float) -> str:
    if t >= 25:
        return "😎"
    if t >= 20:
        return "😊"
    if t >= 17:
        return "😌"
    if t >= 14:
        return "😮‍💨"
    return "🥶"


def _fix_kld_temperature_emojis(text: str) -> str:
    def repl(match: re.Match[str]) -> str:
        prefix = match.group("prefix") or ""
        city_part = match.group("city") or ""
        temp_raw = (match.group("temp") or "").replace(",", ".")
        try:
            t = float(temp_raw)
        except Exception:
            return match.group(0)
        return f"{prefix}{_kld_temp_emoji(t)} {city_part}{match.group('temp')}/"

    emoji_alt = "|".join(re.escape(x) for x in _TEMP_PREFIXES)
    pattern = re.compile(
        rf"^(?P<prefix>\s*)(?:{emoji_alt})\s+(?P<city>[^:\n]+:\s*)(?P<temp>-?\d+(?:[\.,]\d+)?)/",
        flags=re.M,
    )
    return pattern.sub(repl, str(text or ""))


def _move_safecast_before_hashtags(text: str) -> str:
    lines = str(text or "").splitlines()
    safecast = [line for line in lines if line.strip().startswith("🧪")]
    if not safecast:
        return text
    kept = [line for line in lines if not line.strip().startswith("🧪")]
    hash_idx = next((idx for idx, line in enumerate(kept) if line.strip().startswith("#")), -1)
    if hash_idx < 0:
        return "\n".join(kept + ([""] if kept and kept[-1].strip() else []) + safecast)
    insert = []
    if hash_idx > 0 and kept[hash_idx - 1].strip():
        insert.append("")
    insert.extend(safecast)
    insert.append("")
    return "\n".join(kept[:hash_idx] + insert + kept[hash_idx:])


def _promote_vaybometer_after_title(text: str) -> str:
    if not _env_on("FORMAT_V2"):
        return text
    lines = str(text or "").splitlines()
    score_idx = next((idx for idx, line in enumerate(lines) if line.strip().startswith("✨ VayboMeter") and "/10" in line), -1)
    if score_idx <= 0:
        return text
    title_idx = next((idx for idx, line in enumerate(lines) if line.strip().startswith("<b>🌅")), -1)
    if title_idx < 0 or score_idx == title_idx + 1:
        return text
    score_line = lines.pop(score_idx)
    insert_at = title_idx + 1
    while insert_at < len(lines) and not lines[insert_at].strip():
        insert_at += 1
    lines.insert(insert_at, score_line)
    return "\n".join(lines)


def _apply_kld_morning_spacing(text: str) -> str:
    if not _env_on("FORMAT_V2_MORNING_SPACING"):
        return text
    s = str(text or "").strip()
    if "Калининград сегодня" not in s or "🧭 <b>Главный сценарий" in s or "завтра" in s.lower():
        return text

    markers_before = (
        "🌡 Ощущается:",
        "💱",
        "🏭",
        "🧲",
        "✅ План:",
        "🧪",
        "#",
    )
    lines = s.splitlines()
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(markers_before) and out and out[-1].strip():
            out.append("")
        out.append(line)
    spaced = "\n".join(out).strip()
    return re.sub(r"\n{3,}", "\n\n", spaced)


def sanitize_post_text(text: str) -> SafetyResult:
    raw_lines = str(text or "").splitlines()
    out: list[str] = []
    issues: list[str] = []

    prev_sep = False
    blank_seen = False
    for raw in raw_lines:
        line = _normalize_line(raw, issues)
        drop, reason = _line_should_drop(line)
        if drop:
            issues.append(f"removed line ({reason}): {raw.strip()[:120]}")
            continue

        if not line:
            if not blank_seen and out:
                out.append("")
            blank_seen = True
            prev_sep = False
            continue

        is_sep = _line_is_separator(line)
        if is_sep and prev_sep:
            issues.append("collapsed duplicate separator")
            continue

        out.append(line)
        blank_seen = False
        prev_sep = is_sep

    while out and (not out[-1].strip() or _line_is_separator(out[-1])):
        out.pop()

    safe = "\n".join(out).strip()
    safe = re.sub(r"\n{3,}", "\n\n", safe)
    safe = _cap_kld_morning_score(safe)
    safe = _fix_kld_temperature_emojis(safe)
    safe = _move_safecast_before_hashtags(safe)
    safe = _promote_vaybometer_after_title(safe)
    safe = _apply_kld_morning_spacing(safe)
    return SafetyResult(text=safe, issues=issues)


def _structure_summary(text: str) -> str:
    if not _env_on("FORMAT_V2"):
        return ""

    issues: list[str] = []
    s = str(text or "")
    if not any(marker in s for marker in ("🧭 <b>Главный сценарий", "✨ VayboMeter", "🎯 <b>Уверенность", "📌 <b>Вывод")):
        return ""
    plain = re.sub(r"</?b>", "", s)
    is_evening = "завтра" in plain.lower()
    score_expected = _env_on("MORNING_VAYBOMETER_SCORE") or _env_on("EVENING_VAYBOMETER_SCORE") or _env_on("FORMAT_V2_COMPACT")

    if not s.lstrip().startswith("<b>🌅"):
        issues.append("missing title")
    if is_evening and "🧭 <b>Главный сценарий" not in s:
        issues.append("missing main scenario block")
    if score_expected and "VayboMeter" not in s:
        issues.append("missing VayboMeter line")
    if is_evening and _env_on("FORMAT_V2_MAIN_NUANCE") and "VayboMeter" in s and "⚠️ Главный нюанс:" not in s:
        issues.append("missing main nuance line")
    if is_evening and "🎯 <b>Уверенность" not in s:
        issues.append("missing confidence block")
    if is_evening and "📌 <b>Вывод" not in s:
        issues.append("missing conclusion block")
    if not ("🌊 <b>Морские города" in s or "🏙 <b>Калининград" in s or "🏙️ Калининград" in s or "🌡 <b>Внутри области" in s):
        issues.append("missing weather/cities block")
    if "#" not in s:
        issues.append("missing hashtags")

    forbidden_checks = [
        (r"\bKp\s+н/д\b", "forbidden Kp n/a"),
        (r"\bКр\s+н/д\b", "forbidden Cyrillic Kp n/a"),
        (r"/None\b", "forbidden /None artifact"),
        (r"INFO:", "log line leaked into post"),
        (r"Общий фон:\s*благоприятный день", "ambiguous astro background line"),
    ]
    for pattern, label in forbidden_checks:
        if re.search(pattern, s, flags=re.I):
            issues.append(label)
    for line in s.splitlines():
        if "…" in line and len(line.strip()) > 80:
            issues.append("long clipped ellipsis line")
            break

    if issues:
        return "⚠️ Structure validator:\n" + "\n".join(f"- {x}" for x in issues[:12])
    length = len(s.strip())
    compact = "on" if _env_on("FORMAT_V2_COMPACT") else "off"
    return f"✅ Structure validator: all required blocks found. Length: {length} chars. Compact: {compact}."


def split_telegram_text(text: str, limit: int = _SAFE_CHUNK_LIMIT) -> list[str]:
    text = str(text or "").strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    cur: list[str] = []
    cur_len = 0
    for para in re.split(r"(\n\n+)", text):
        if not para:
            continue
        add_len = len(para)
        if cur and cur_len + add_len > limit:
            chunks.append("".join(cur).strip())
            cur = [para]
            cur_len = add_len
        else:
            cur.append(para)
            cur_len += add_len
    if cur:
        chunks.append("".join(cur).strip())

    final: list[str] = []
    for ch in chunks:
        while len(ch) > limit:
            cut = ch.rfind("\n", 0, limit)
            if cut < limit // 2:
                cut = ch.rfind(" ", 0, limit)
            if cut < limit // 2:
                cut = limit
            final.append(ch[:cut].strip())
            ch = ch[cut:].strip()
        if ch:
            final.append(ch)
    return final


def validation_summary(result: SafetyResult) -> str:
    if not result.issues:
        base = "✅ Safety validator: no issues found."
    else:
        escaped = [html.escape(x, quote=False) for x in result.issues[:20]]
        more = "" if len(result.issues) <= 20 else f"\n… and {len(result.issues) - 20} more"
        base = "⚠️ Safety validator removed/changed lines:\n" + "\n".join(f"- {x}" for x in escaped) + more
    structure = _structure_summary(result.text)
    return base if not structure else base + "\n" + structure
