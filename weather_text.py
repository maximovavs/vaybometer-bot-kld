#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared, dependency-free weather-text classification for KLD posts.

format_v2.py, safe_test_post.py, post_kld.py and kld_informative_cover.py each
grew their own "шторм" word/negation regex and their own literal gust
threshold (15), and the four copies drifted out of sync with each other and
with STORM_GUST_MS. This module is the single contract for both, so all
four import from here instead of maintaining separate regexes. It has no
dependencies beyond the standard library, so importing it does not pull in
any of post_common's heavier runtime requirements.
"""
from __future__ import annotations

import os
import re

STORM_GUST_MS = float(os.getenv("STORM_GUST_MS", "15"))

_HTML_TAG_RE = re.compile(r"<[^>]+>")

# Single wind/gust parser shared by all layers, so a gust threshold is applied
# to the same value everywhere.
#
# A GUST is only a "порыв …" construction ("порывы до 15 м/с", "порыв 15 м/с",
# "порывы 15 м/с"). Plain wind ("ветер 15 м/с", "💨 15 м/с", "15 м/с") is NOT a
# gust — this is the whole point of the split: storm classification keys on the
# gust, never on average wind. WIND is any "N м/с" number (average or gust),
# used for the softer windy / water-caution / comfort cues.
_GUST_MS_RE = re.compile(r"порыв\w*\s*(?:до\s*)?(-?\d+(?:[.,]\d+)?)\s*м\s*/?\s*с", re.IGNORECASE)
_WIND_MS_RE = re.compile(r"(-?\d+(?:[.,]\d+)?)\s*м\s*/?\s*с", re.IGNORECASE)


def _max_ms(pattern: re.Pattern[str], text: str) -> float | None:
    values: list[float] = []
    for raw in pattern.findall(str(text or "")):
        try:
            values.append(float(raw.replace(",", ".")))
        except (TypeError, ValueError):
            continue
    return max(values) if values else None


def extract_max_gust_ms(text: str) -> float | None:
    """Max gust in m/s, counting ONLY explicit "порыв …" constructions.

    Plain average wind ("ветер 16 м/с", "💨 16 м/с", "16 м/с") returns None
    here — it is never a gust, so it never drives the storm threshold."""
    return _max_ms(_GUST_MS_RE, text)


def extract_max_wind_ms(text: str) -> float | None:
    """Max wind in m/s over any "N м/с" number (average or gust), for the
    softer windy / water-caution / comfort cues — not for storm classification."""
    return _max_ms(_WIND_MS_RE, text)

# Sentence-ending punctuation, or a comma followed by a contrastive
# conjunction ("но"/"а") joining two independent weather statements, e.g.
# "Риск шторма невысок, но штормовое предупреждение действует у моря."
_CLAUSE_SPLIT_RE = re.compile(r"(?<=[.!?;])\s+|,\s+(?:но|а)\s+")


def split_clauses(line: str) -> list[str]:
    """Split a line into independent clauses so a negation in one clause
    cannot be mistaken for negating a different clause."""
    return [clause.strip() for clause in _CLAUSE_SPLIT_RE.split(str(line or "")) if clause.strip()]


STORM_WORD_RE = re.compile(r"шторм\w*", re.IGNORECASE)

# Bounded gap (<=40 chars) between the storm term and its negation/hedge, so
# modifiers ("Шторма точно не будет.", "Риск шторма в другой части области
# невысок.") are still recognized. Two directions, like the precipitation
# gaps in kld_informative_cover:
#  - AFTER (term then cue, "шторм ... не будет"): may cross commas so
#    parenthetical modifiers work ("шторма, скорее всего, не будет").
#  - BEFORE (cue then term, "возможен шторм", "риск шторма"): must NOT cross a
#    comma, so a cue that belongs to a preceding statement does not bind the
#    storm ("Гроза возможна, шторм ожидается." keeps the storm confirmed).
# Both gaps also forbid another "шторм" and the sibling severe type "гроз", so
# a cue/negation cannot leap over an unrelated, separately-classified mention
# ("Шторм ожидается, гроза возможна." must keep the storm confirmed — the
# "возможна" belongs to the thunderstorm, on the far side of "гроза").
_STORM_GAP_AFTER = r"(?:(?!шторм\w*|гроз\w*)[^.!?\n]){0,40}?"
_STORM_GAP_BEFORE = r"(?:(?!шторм\w*|гроз\w*)[^.!?\n,]){0,40}?"

# A weather warning is cancelled only when the *warning* is the subject of a
# cancellation verb ("предупреждение отменено/снято/не действует"). This is
# deliberately NOT a bare "отмен\w*"/"исключ\w*": those also match the storm
# being the *actor* ("Шторм отменил паромные рейсы.", "Из-за шторма отменены
# рейсы."), which confirm a storm rather than deny one. Passive-fact removal
# ("исключён из прогноза") is handled by the исключ(ён|ена…) participle form,
# which does not match the active verb "исключил".
_WARNING_CANCELLED = r"предупрежден\w*{gap}\b(?:отмен(?:ён\w*|ен[аоы]\w*)|снят\w*|не\s+действ\w*)"
STORM_NEGATION_RE = re.compile(
    r"штормов\w*\s+предупрежден\w*\s+нет|"
    + _WARNING_CANCELLED.format(gap=_STORM_GAP_AFTER)
    + r"|"
    rf"шторм\w*{_STORM_GAP_AFTER}\b(?:не\s+(?:ожида\w*|будет|прогнозир\w*|предвид\w*|подтвержд\w*)|"
    r"маловероят\w*|исключ(?:ён\w*|ен[аоы]\w*))|"
    r"без\s+шторм\w*|"
    rf"(?:риск|вероятност\w*){_STORM_GAP_BEFORE}\bшторм\w*{_STORM_GAP_AFTER}\b(?:низк\w*|невысок\w*|минимал\w*|отсутств\w*)",
    re.IGNORECASE,
)

# A storm mention is not a confirmed fact when it is hedged: possibility
# ("Шторм возможен.", "Возможен шторм."), probability ("Вероятность шторма
# 30%."), a persisting-but-unrealized risk ("Риск шторма сохраняется."), a
# "check/clarify later" note ("Шторм следует уточнить утром."), or an explicit
# "not ruled out" ("Шторм не исключён.").
_STORM_UNCERTAIN_CUE = r"провер\w*|уточн\w*|возмож\w*|вероятн\w*|не\s+исключ\w*|сохраня\w*"
STORM_UNCERTAIN_RE = re.compile(
    rf"шторм\w*{_STORM_GAP_AFTER}\b(?:{_STORM_UNCERTAIN_CUE})|"
    rf"(?:возмож\w*|вероятност\w*|риск){_STORM_GAP_BEFORE}\bшторм\w*",
    re.IGNORECASE,
)

EDITORIAL_LINE_RE = re.compile(
    r"^\W*(?:главный\s+нюанс|нюанс|vaybometer|план|рекомендации|уверенность)"
    r"(?:\s+[^:]{1,32})?\s*:",
    re.IGNORECASE,
)


def clause_has_confirmed_storm(clause: str) -> bool:
    """True only if this single clause has a "шторм" mention that is neither
    negated (STORM_NEGATION_RE) nor merely hedged (STORM_UNCERTAIN_RE)."""
    low = clause.lower()
    return (
        bool(STORM_WORD_RE.search(low))
        and not STORM_NEGATION_RE.search(low)
        and not STORM_UNCERTAIN_RE.search(low)
    )


def has_confirmed_storm_word(text: str) -> bool:
    """True if any non-editorial clause in the text has a non-negated
    "шторм" mention.

    Checked per clause (line, then sentence/contrast split): a negation in
    one clause ("риск шторма в другой части области невысок") must not
    cancel a genuine confirmation in a different clause or line ("Штормовое
    предупреждение: порывы до 18 м/с.").
    """
    for raw_line in str(text or "").splitlines():
        line = _HTML_TAG_RE.sub("", raw_line).strip()
        if not line or EDITORIAL_LINE_RE.match(line.lower()):
            continue
        for clause in split_clauses(line):
            if clause_has_confirmed_storm(clause):
                return True
    return False


__all__ = [
    "STORM_GUST_MS",
    "extract_max_gust_ms",
    "extract_max_wind_ms",
    "split_clauses",
    "STORM_WORD_RE",
    "STORM_NEGATION_RE",
    "STORM_UNCERTAIN_RE",
    "EDITORIAL_LINE_RE",
    "clause_has_confirmed_storm",
    "has_confirmed_storm_word",
]
