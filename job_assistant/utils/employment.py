"""
Определение типа устройства: ТК РФ / ГПХ / самозанятый / по договорённости.
"""

from __future__ import annotations

import re
from typing import Iterable

# Флаги
EMP_TK = "tk"
EMP_GPH = "gph"
EMP_SELF = "self"
EMP_AGREE = "agree"
EMP_UNKNOWN = "unknown"

ALL_TYPES = (EMP_TK, EMP_GPH, EMP_SELF, EMP_AGREE)

LABELS = {
    EMP_TK: "Официальное ТК РФ",
    EMP_GPH: "Договор ГПХ",
    EMP_SELF: "Самозанятый",
    EMP_AGREE: "По договорённости",
    EMP_UNKNOWN: "Не указано",
}

_TK_PATTERNS = [
    r"трудов(ой|ому|ого|ым)?\s+договор",
    r"\bтк\s*рф\b",
    r"\bпо\s+тк\b",
    r"официальн(ое|ый|ая)\s+(трудоустрой|оформлен)",
    r"оформлен\w*\s+в\s+штат",
    r"\bв\s+штат\b",
    r"бессрочн\w*\s+трудов",
    r"официальное\s+устройство",
]

_GPH_PATTERNS = [
    r"\bгпх\b",
    r"гражданско[-\s]?правов",
    r"договор\s+(подряда|оказания\s+услуг|возмездного)",
    r"договор\s+гпх",
    r"\bcontract\b.*\bcontractor\b",
]

_SELF_PATTERNS = [
    r"самозанят",
    r"\bнпд\b",
    r"налог\s+на\s+профессиональн",
    r"self[-\s]?employed",
]

_AGREE_PATTERNS = [
    r"по\s+договор[её]нности",
    r"тип\s+оформления\s+обсужда",
    r"формат\s+сотрудничества\s+обсужда",
]


def _any_match(text: str, patterns: Iterable[str]) -> bool:
    for p in patterns:
        if re.search(p, text, re.IGNORECASE):
            return True
    return False


def detect_employment_types(*parts: str) -> list[str]:
    """
    Вернуть список флагов, найденных в тексте вакансии.
    Может быть несколько: ['tk', 'gph'].
    """
    text = " ".join(p for p in parts if p).lower().replace("ё", "е")
    if not text.strip():
        return [EMP_UNKNOWN]

    found: list[str] = []
    if _any_match(text, _TK_PATTERNS):
        found.append(EMP_TK)
    if _any_match(text, _GPH_PATTERNS):
        found.append(EMP_GPH)
    if _any_match(text, _SELF_PATTERNS):
        found.append(EMP_SELF)
    if _any_match(text, _AGREE_PATTERNS):
        found.append(EMP_AGREE)

    return found if found else [EMP_UNKNOWN]


def employment_to_str(types: list[str]) -> str:
    return ",".join(types) if types else EMP_UNKNOWN


def parse_employment_str(value: str | None) -> list[str]:
    if not value:
        return [EMP_UNKNOWN]
    parts = [p.strip() for p in str(value).split(",") if p.strip()]
    return parts or [EMP_UNKNOWN]


def is_tk_only(types: list[str] | str | None) -> bool:
    """
    True, если указано ТОЛЬКО официальное ТК РФ (без ГПХ/самозанятый/по договорённости).
    Если вместе с ТК есть ГПХ или self — False (вакансию оставляем).
    """
    if isinstance(types, str):
        types = parse_employment_str(types)
    types = [t for t in (types or []) if t and t != EMP_UNKNOWN]
    if not types:
        return False
    return types == [EMP_TK] or (EMP_TK in types and not any(
        t in types for t in (EMP_GPH, EMP_SELF, EMP_AGREE)
    ))


def format_employment_label(value: str | None) -> str:
    types = parse_employment_str(value)
    if types == [EMP_UNKNOWN]:
        return LABELS[EMP_UNKNOWN]
    return " + ".join(LABELS.get(t, t) for t in types)
