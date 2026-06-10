#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Safety helpers for VayboMeter test publishing.

This module is intentionally conservative: it does not invent data and it
removes lines that look broken, stale, or low-trust before a test post is sent.
"""
from __future__ import annotations

import html
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

_FORBIDDEN_PATTERNS = [
    (re.compile(r"\bKp\s+н/д\b", re.I), "Kp n/a"),
    (re.compile(r"\bКр\s+н/д\b", re.I), "Kp n/a"),
    (re.compile(r"/None\b", re.I), "/None artifact"),
    (re.compile(r"\bFull\s+Moon\b", re.I), "English moon phrase"),
    (re.compile(r"\bSagittarius\b", re.I), "English zodiac phrase"),
    (re.compile(r"#(?:К|здо)\b", re.I), "broken hashtag"),
]

@dataclass
class SafetyResult:
    text: str
    issues: List[str]


def _line_is_separator(line: str) -> bool:
    s = line.strip()
    return bool(s) and set(s) <= {"—", "-", "─"}


def _normalize_line(line: str) -> str:
    line = line.rstrip()
    line = re.sub(r"\s+/None\b", "", line, flags=re.I)
    line = re.sub(r"\((?:N|NE|E|SE|S|SW|W|NW)?/?None\)", "", line, flags=re.I)
    line = line.replace(" • —", "")
    line = line.replace(" — —", " —")
    line = re.sub(r"\s{2,}", " ", line)
    return line.strip()


def _line_should_drop(line: str) -> tuple[bool, str | None]:
    stripped = line.strip()
    if not stripped:
        return False, None

    for rx, reason in _FORBIDDEN_PATTERNS:
        if rx.search(stripped):
            return True, reason

    last_word = re.sub(r"[^A-Za-zА-Яа-яЁё]+", "", stripped.split()[-1]).lower() if stripped.split() else ""
    if last_word in _BROKEN_TAILS:
        return True, f"clipped tail: {last_word}"

    return False, None


def sanitize_post_text(text: str) -> SafetyResult:
    """Return a safer post text and a list of removed/changed-line reasons."""
    raw_lines = str(text or "").splitlines()
    out: list[str] = []
    issues: list[str] = []

    prev_sep = False
    blank_seen = False
    for raw in raw_lines:
        line = _normalize_line(raw)
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
    return SafetyResult(text=safe, issues=issues)


def split_telegram_text(text: str, limit: int = _SAFE_CHUNK_LIMIT) -> list[str]:
    """Split text into Telegram-safe chunks, preferably on paragraph boundaries."""
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
        return "✅ Safety validator: no issues found."
    escaped = [html.escape(x, quote=False) for x in result.issues[:20]]
    more = "" if len(result.issues) <= 20 else f"\n… and {len(result.issues) - 20} more"
    return "⚠️ Safety validator removed/changed lines:\n" + "\n".join(f"- {x}" for x in escaped) + more
