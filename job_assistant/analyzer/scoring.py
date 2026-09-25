"""
Сопоставление требований вакансии с профилем кандидата.

1) Извлекаем отдельные требования (обязательные / желательные, AND / OR).
2) Для каждого: MATCH | PARTIAL | MISSING | UNKNOWN — только по фактам профиля.
3) match_score = покрытие обязательных + вклад желательных (не «число skill-групп»).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Literal, Optional

Status = Literal["MATCH", "PARTIAL", "MISSING", "UNKNOWN"]

# ---------------------------------------------------------------------------
# Синонимы / эквиваленты (канон → варианты в тексте)
# ---------------------------------------------------------------------------

SKILL_SYNONYMS: dict[str, set[str]] = {
    "python": {"python", "питон", "django", "flask", "fastapi", "pandas", "numpy"},
    "c#": {"c#", "csharp", "с#", "c sharp", ".net", "dotnet", "asp.net", "unity"},
    "java": {"java", "spring", "spring boot", "kotlin"},
    "javascript": {
        "javascript", "js", "typescript", "ts", "node.js", "nodejs", "node",
        "react", "vue", "angular",
    },
    "sql": {
        "sql", "postgresql", "postgres", "postresql", "mysql", "sqlite",
        "mssql", "база данных", "бд", "бд.",
    },
    "html_css": {"html", "css", "верстка", "frontend", "front-end", "фронтенд"},
    "web_design": {
        "web design", "web-design", "веб-дизайн", "веб дизайн", "вебдизайн",
        "ui", "ux", "ui/ux", "figma", "photoshop", "illustrator", "illustator",
        "веб дизайнер", "web designer", "graphic design", "графический дизайн",
    },
    "unity": {"unity", "unity3d", "unity 3d", "unreal", "game dev", "геймдев"},
    "devops": {
        "devops", "docker", "kubernetes", "k8s", "ci/cd", "jenkins", "gitlab ci",
    },
    "sysadmin": {
        "sysadmin", "системный администратор", "system administrator",
        "linux", "windows server", "администрирование", "локальн", "dns",
        "коммутатор", "настройка ос", "локальные сети",
    },
    "support": {
        "support", "техническая поддержка", "it support", "helpdesk", "help desk",
        "техподдержка", "customer support",
    },
    "crm": {"crm", "bitrix", "битрикс", "salesforce", "amocrm", "amo crm"},
    "prompt": {
        "prompt engineer", "prompt engineering", "промпт", "llm", "chatgpt",
        "нейросеть", "ai engineer", "mistral",
    },
    "php": {"php", "laravel"},
    "go": {"go", "golang"},
    "1c": {"1c", "1с", "одинэс"},
    "office": {
        "microsoft office", "ms office", "word", "excel", "powerpoint", "access",
    },
    "network": {
        "локальные сети", "локальн", "сеть", "dns", "коммутатор", "tcp/ip",
    },
}

# Термины, которые часто встречаются в OR-списках языков
LANGUAGE_TERMS = {
    "python", "java", "javascript", "js", "typescript", "ts", "c#", "csharp",
    "с#", "c++", "cpp", "go", "golang", "php", "ruby", "kotlin", "swift",
    "rust", "scala", "питон",
}


def _normalize(text: str) -> str:
    t = (text or "").lower().replace("ё", "е")
    t = t.replace("\u00a0", " ").replace("\u202f", " ")
    t = re.sub(r"[ \t]+", " ", t)
    return t


def _contains_term(haystack: str, term: str) -> bool:
    term = term.lower().strip()
    if not term:
        return False
    if len(term) >= 3 and any(ch in term for ch in " -/."):
        return term in haystack
    # C#, .net и т.п.
    if re.search(r"[#.]", term) or term.startswith("."):
        return term in haystack
    pattern = (
        r"(?<![a-zA-Zа-яА-Я0-9_])"
        + re.escape(term)
        + r"(?![a-zA-Zа-яА-Я0-9_])"
    )
    return re.search(pattern, haystack, re.IGNORECASE) is not None


def profile_has_any(profile: str, terms: Iterable[str]) -> bool:
    hay = _normalize(profile)
    return any(_contains_term(hay, t) for t in terms)


def expand_terms_for_token(token: str) -> set[str]:
    """Расширить токен синонимами из известных групп."""
    t = token.lower().strip()
    out = {t}
    for _canon, syns in SKILL_SYNONYMS.items():
        if t in syns or any(t in s or s in t for s in syns if len(s) >= 3):
            out |= syns
    return out


# ---------------------------------------------------------------------------
# Извлечение требований
# ---------------------------------------------------------------------------

@dataclass
class Requirement:
    """Одно логическое требование вакансии."""

    text: str
    required: bool = True  # False = nice-to-have
    # Альтернативы (OR): достаточно одного MATCH
    alternatives: list[str] = field(default_factory=list)
    # Если alternatives пуст — требование = целая фраза text

    def label(self) -> str:
        if self.alternatives and len(self.alternatives) > 1:
            return " OR ".join(self.alternatives)
        return self.text.strip()


_SECTION_REQUIRED = re.compile(
    r"(?i)(requirements?|обязательн\w*|требования|необходим\w*|must[- ]have|"
    r"what\s+we\s+(look|expect|need)|you\s+have|required\s+skills?|"
    r"что\s+(мы\s+)?ждём|ключн\w*\s+навык|hard[- ]skills?|"
    r"вы\s+нам\s+подходит|ожидания|нужн\w*\s+опыт|наш\s+кандидат|"
    r"skills?\s+and\s+experience|квалификация)"
)
_SECTION_NICE = re.compile(
    r"(?i)(nice[- ]to[- ]have|будет\s+плюс|желательн\w*|преимуществ\w*|"
    r"будет\s+плюсом|nice\s+to\s+have|optional|хорош\w*\s+если|"
    r"plus\s+if|would\s+be\s+a\s+plus|дополнительн\w*\s+навык)"
)
_SECTION_STOP = re.compile(
    r"(?i)(обязанност\w*|responsibilit\w*|условия|conditions|мы\s+предлагаем|"
    r"about\s+us|о\s+компании|задачи|what\s+you.?ll\s+do|"
    r"what\s+you\s+will\s+do|benefits?|мы\s+предлагаем)"
)

_OR_SPLIT = re.compile(
    r"\s+или\s+|\s+or\s+|/",
    re.IGNORECASE,
)
_BULLET = re.compile(r"^[\s•\-\*–—·▪►▶]+")


def _split_or_list(line: str) -> list[str]:
    """Разбить строку на OR-альтернативы, если это список технологий."""
    raw = line.strip()
    if not raw:
        return []
    # «Java / JavaScript / Python / C#» или «Java, JavaScript, Python или C#»
    # Только если похоже на список коротких токенов
    parts = re.split(r"\s+или\s+|\s+or\s+", raw, flags=re.IGNORECASE)
    expanded: list[str] = []
    for p in parts:
        # также split по / и запятым если все куски короткие
        sub = re.split(r"\s*/\s*|\s*,\s*", p)
        expanded.extend(s.strip() for s in sub if s.strip())

    # эвристика: OR-список, если >=2 коротких токена и большинство — «языкоподобные»
    if len(expanded) >= 2:
        short = sum(1 for x in expanded if len(x) <= 24)
        langish = sum(
            1
            for x in expanded
            if _normalize(x) in LANGUAGE_TERMS
            or any(_normalize(x) in syns for syns in SKILL_SYNONYMS.values())
        )
        if short >= len(expanded) - 1 and (langish >= 1 or len(expanded) >= 3):
            return expanded
    return [raw]


def _is_skillish_line(line: str) -> bool:
    s = line.strip()
    if not s or len(s) > 180:
        return False
    # отсечь явный мусор
    if re.search(r"(?i)http[s]?://|www\.", s):
        return False
    return True



def extract_requirements(
    vacancy_title: str,
    vacancy_description: str,
) -> list[Requirement]:
    """
    Структурированное извлечение требований.

    Секции Requirements / Nice to have / Требования / Желательно,
    маркированные списки, OR-конструкции, навыки из title.
    Может вернуть [] — вызывающий код использует fallback.
    """
    text = vacancy_description or ""
    title = vacancy_title or ""
    text = re.sub(r"<[^>]+>", "\n", text)
    text = text.replace("\r", "\n")
    lines_raw = [ln.strip() for ln in text.split("\n")]
    lines_raw = [_BULLET.sub("", ln).strip() for ln in lines_raw if ln and ln.strip()]

    reqs: list[Requirement] = []
    mode: Optional[str] = None

    def add_line(line: str, required: bool) -> None:
        if not _is_skillish_line(line):
            return
        if _SECTION_REQUIRED.fullmatch(line) or _SECTION_NICE.fullmatch(line):
            return
        if _SECTION_STOP.fullmatch(line):
            return
        alts = _split_or_list(line)
        if len(alts) > 1:
            reqs.append(Requirement(text=line, required=required, alternatives=alts))
        else:
            reqs.append(Requirement(text=line, required=required, alternatives=[]))

    for ln in lines_raw:
        if _SECTION_NICE.search(ln) and len(ln) < 120:
            mode = "nice"
            if ":" in ln:
                tail_part = ln.split(":", 1)[1].strip()
                if tail_part:
                    add_line(tail_part, False)
            continue
        if _SECTION_REQUIRED.search(ln) and len(ln) < 140:
            mode = "required"
            if ":" in ln:
                tail_part = ln.split(":", 1)[1].strip()
                if tail_part:
                    add_line(tail_part, True)
            continue
        if _SECTION_STOP.search(ln) and len(ln) < 100:
            mode = None
            continue
        if mode == "required":
            add_line(ln, True)
        elif mode == "nice":
            add_line(ln, False)

    if not reqs:
        for ln in lines_raw:
            cleaned = _BULLET.sub("", ln).strip()
            if len(cleaned) < 120 and (
                re.match(r"^[\d\.\)\-•]", ln)
                or len(cleaned.split()) <= 14
                or _OR_SPLIT.search(cleaned)
                or any(
                    _contains_term(_normalize(cleaned), s)
                    for syns in SKILL_SYNONYMS.values()
                    for s in syns
                )
            ):
                add_line(cleaned, True)

    if title:
        title_alts = _split_or_list(title)
        title_norm = _normalize(title)
        found_tokens: list[str] = []
        for syns in SKILL_SYNONYMS.values():
            for s in sorted(syns, key=len, reverse=True):
                if _contains_term(title_norm, s):
                    found_tokens.append(s)
                    break
        if len(title_alts) > 1 and all(len(a) <= 30 for a in title_alts):
            reqs.insert(
                0, Requirement(text=title, required=True, alternatives=title_alts)
            )
        for tok in found_tokens[:5]:
            if any(tok.lower() in r.label().lower() for r in reqs):
                continue
            reqs.append(Requirement(text=tok, required=True, alternatives=[tok]))

    seen: set[str] = set()
    unique: list[Requirement] = []
    for r in reqs:
        key = r.label().lower()
        if key in seen or len(key) < 2:
            continue
        seen.add(key)
        unique.append(r)
    return unique[:40]


def extract_requirements_fallback(
    vacancy_title: str,
    vacancy_description: str,
) -> list[Requirement]:
    """
    Fallback: skill-термины и OR-фразы из полного текста вакансии.
    Анализ не должен останавливаться из-за пустого structured extraction.
    """
    blob = f"{vacancy_title or ''}\n{vacancy_description or ''}"
    hay = _normalize(blob)
    reqs: list[Requirement] = []
    seen: set[str] = set()

    for _canon, syns in SKILL_SYNONYMS.items():
        hits = [
            s for s in sorted(syns, key=len, reverse=True) if _contains_term(hay, s)
        ]
        if not hits:
            continue
        label = hits[0]
        if label in seen:
            continue
        seen.add(label)
        reqs.append(
            Requirement(
                text=label,
                required=True,
                alternatives=list(dict.fromkeys(hits[:6])),
            )
        )

    for m in re.finditer(
        r"([A-Za-zА-Яа-я#.+][A-Za-zА-Яа-я0-9#.+]{0,20})"
        r"(?:\s*/\s*|\s+или\s+|\s+or\s+)"
        r"([A-Za-zА-Яа-я#.+][A-Za-zА-Яа-я0-9#.+]{0,20})"
        r"(?:(?:\s*/\s*|\s+или\s+|\s+or\s+)"
        r"([A-Za-zА-Яа-я#.+][A-Za-zА-Яа-я0-9#.+]{0,20}))?",
        blob,
        flags=re.IGNORECASE,
    ):
        parts = [p for p in m.groups() if p]
        if len(parts) >= 2:
            key = " OR ".join(p.lower() for p in parts)
            if key not in seen:
                seen.add(key)
                reqs.append(
                    Requirement(text=m.group(0), required=True, alternatives=parts)
                )

    if not reqs and (vacancy_title or "").strip():
        reqs.append(
            Requirement(
                text=(vacancy_title or "").strip()[:120],
                required=True,
                alternatives=[],
            )
        )

    if not reqs and hay.strip():
        tokens = re.findall(r"[a-zA-Zа-яА-Я#.]{3,20}", hay)
        stop = {
            "the", "and", "for", "with", "you", "наш", "для", "или", "опыт",
            "работы", "команда", "vacancy", "job", "this", "that", "will",
            "from", "your", "have", "are",
        }
        for t in tokens:
            if t in stop or t in seen:
                continue
            seen.add(t)
            reqs.append(Requirement(text=t, required=True, alternatives=[t]))
            if len(reqs) >= 8:
                break

    return reqs[:40]


def extract_requirements_with_status(
    vacancy_title: str,
    vacancy_description: str,
) -> tuple[list[Requirement], str]:
    """extraction_status: structured | fallback | empty"""
    structured = extract_requirements(vacancy_title, vacancy_description)
    if structured:
        return structured, "structured"
    fallback = extract_requirements_fallback(vacancy_title, vacancy_description)
    if fallback:
        return fallback, "fallback"
    return [], "empty"


@dataclass
class RequirementAssessment:
    requirement: Requirement
    status: Status
    evidence: str = ""  # что нашли в профиле / почему missing
    matched_alternative: str = ""

    def to_dict(self) -> dict:
        return {
            "requirement": self.requirement.label(),
            "required": self.requirement.required,
            "status": self.status,
            "evidence": self.evidence,
            "matched_alternative": self.matched_alternative,
        }


def _match_phrase_against_profile(phrase: str, profile: str) -> tuple[Status, str]:
    """
    Сопоставить одну фразу/навык с профилем.
    MATCH — явный термин или синоним;
    PARTIAL — частичное пересечение значимых токенов;
    MISSING — уверенно нет;
    UNKNOWN — слишком общая фраза, нельзя судить.
    """
    hay = _normalize(profile)
    phrase_n = _normalize(phrase)
    if not phrase_n:
        return "UNKNOWN", "пустая формулировка"

    # Прямое вхождение фразы
    if len(phrase_n) >= 4 and phrase_n in hay:
        return "MATCH", f"в профиле есть «{phrase.strip()[:60]}»"

    # Расширенные синонимы для всей фразы и токенов
    tokens = re.findall(r"[a-zA-Zа-яА-Я#.#+][a-zA-Zа-яА-Я0-9.#+#]*", phrase_n)
    tokens = [t for t in tokens if len(t) >= 2 and t not in {
        "и", "or", "или", "опыт", "знание", "знания", "умение", "работы",
        "work", "with", "years", "год", "года", "лет", "от", "на", "в",
    }]

    if not tokens:
        return "UNKNOWN", "слишком общая формулировка"

    # Если фраза целиком мапится на skill-группу
    for _canon, syns in SKILL_SYNONYMS.items():
        if any(_contains_term(phrase_n, s) for s in syns):
            if profile_has_any(profile, syns):
                hit = next(s for s in syns if _contains_term(hay, s))
                return "MATCH", f"эквивалент в профиле: «{hit}»"
            return "MISSING", f"в профиле нет вариантов: {', '.join(sorted(syns)[:5])}"

    matched = 0
    evidence_bits: list[str] = []
    for tok in tokens:
        expanded = expand_terms_for_token(tok)
        if profile_has_any(profile, expanded):
            matched += 1
            hit = next((t for t in expanded if _contains_term(hay, t)), tok)
            evidence_bits.append(hit)

    if matched == 0:
        # Не делаем MISSING для длинных soft-формулировок без hard-skill
        if len(tokens) >= 4 and not any(
            _normalize(t) in LANGUAGE_TERMS or any(_normalize(t) in s for s in SKILL_SYNONYMS.values())
            for t in tokens
        ):
            return "UNKNOWN", "нет проверяемых hard-skill маркеров"
        return "MISSING", f"не найдено в профиле: {', '.join(tokens[:6])}"

    if matched >= max(1, (len(tokens) + 1) // 2):
        if matched >= len(tokens):
            return "MATCH", "подтверждено: " + ", ".join(evidence_bits[:5])
        return "PARTIAL", "частично: " + ", ".join(evidence_bits[:5])

    return "PARTIAL", "слабо: " + ", ".join(evidence_bits[:5])


def assess_requirement(req: Requirement, profile: str) -> RequirementAssessment:
    """Оценить одно требование с учётом OR-альтернатив."""
    if req.alternatives and len(req.alternatives) > 1:
        # OR: достаточно одного MATCH; PARTIAL копятся
        best: Optional[RequirementAssessment] = None
        partials: list[str] = []
        for alt in req.alternatives:
            st, ev = _match_phrase_against_profile(alt, profile)
            if st == "MATCH":
                return RequirementAssessment(
                    requirement=req,
                    status="MATCH",
                    evidence=ev,
                    matched_alternative=alt,
                )
            if st == "PARTIAL":
                partials.append(f"{alt}: {ev}")
                if best is None or best.status != "PARTIAL":
                    best = RequirementAssessment(
                        requirement=req,
                        status="PARTIAL",
                        evidence=ev,
                        matched_alternative=alt,
                    )
        if best is not None:
            best.evidence = "; ".join(partials[:4]) or best.evidence
            return best
        return RequirementAssessment(
            requirement=req,
            status="MISSING",
            evidence="ни одна альтернатива не подтверждена: "
            + ", ".join(req.alternatives[:8]),
        )

    phrase = req.alternatives[0] if req.alternatives else req.text
    st, ev = _match_phrase_against_profile(phrase, profile)
    return RequirementAssessment(
        requirement=req,
        status=st,
        evidence=ev,
        matched_alternative=phrase if st in ("MATCH", "PARTIAL") else "",
    )



def assess_all_requirements(
    profile: str,
    vacancy_title: str,
    vacancy_description: str,
) -> list[RequirementAssessment]:
    reqs, _status = extract_requirements_with_status(vacancy_title, vacancy_description)
    return [assess_requirement(r, profile) for r in reqs]


def assess_all_requirements_with_meta(
    profile: str,
    vacancy_title: str,
    vacancy_description: str,
) -> tuple[list[RequirementAssessment], str]:
    reqs, status = extract_requirements_with_status(vacancy_title, vacancy_description)
    return [assess_requirement(r, profile) for r in reqs], status


def compute_match_score_from_assessments(
    assessments: list[RequirementAssessment],
) -> dict:
    """Score по покрытию требований. Пустой список → нейтральные 50, анализ идёт дальше."""
    required = [a for a in assessments if a.requirement.required]
    nice = [a for a in assessments if not a.requirement.required]

    def unit(a: RequirementAssessment) -> Optional[float]:
        if a.status == "MATCH":
            return 1.0
        if a.status == "PARTIAL":
            return 0.5
        if a.status == "MISSING":
            return 0.0
        return None

    def avg(items: list[RequirementAssessment]) -> Optional[float]:
        vals = [v for v in (unit(a) for a in items) if v is not None]
        if not vals:
            return None
        return sum(vals) / len(vals)

    req_avg = avg(required)
    nice_avg = avg(nice)

    if not assessments:
        formula_score = 50.0
    elif req_avg is None and nice_avg is None:
        formula_score = 50.0
    elif req_avg is None:
        formula_score = 40.0 + 50.0 * float(nice_avg or 0)
    elif nice_avg is None:
        formula_score = 100.0 * req_avg
    else:
        formula_score = 100.0 * (0.85 * req_avg + 0.15 * nice_avg)

    hard_missing = sum(1 for a in required if a.status == "MISSING")
    hard_total = sum(1 for a in required if a.status != "UNKNOWN")
    if hard_total > 0 and hard_missing == hard_total:
        formula_score = min(formula_score, 35.0)
    elif hard_total >= 2 and hard_missing >= hard_total * 0.6:
        formula_score = min(formula_score, 55.0)

    formula_score = max(0.0, min(100.0, round(formula_score, 1)))
    matched = [a for a in assessments if a.status == "MATCH"]
    missing = [a for a in assessments if a.status == "MISSING"]
    partial = [a for a in assessments if a.status == "PARTIAL"]

    return {
        "formula_score": formula_score,
        "required_total": len(required),
        "required_matched": sum(1 for a in required if a.status == "MATCH"),
        "required_partial": sum(1 for a in required if a.status == "PARTIAL"),
        "required_missing": hard_missing,
        "nice_matched": sum(1 for a in nice if a.status == "MATCH"),
        "matched_labels": [a.requirement.label()[:80] for a in matched[:12]],
        "missing_labels": [a.requirement.label()[:80] for a in missing[:12]],
        "partial_labels": [a.requirement.label()[:80] for a in partial[:8]],
        "assessments": assessments,
    }


def build_requirements_breakdown_text(
    assessments: list[RequirementAssessment],
    *,
    extraction_status: str = "structured",
) -> str:
    """Разбор требований. Никогда не показывает «сбой извлечения» как ошибку анализа."""
    source_note = {
        "structured": "структурированное извлечение",
        "fallback": "fallback по полному тексту / skill-терминам",
        "empty": "текст вакансии пуст или без распознаваемых маркеров",
        "llm_only": "только LLM по полному тексту",
    }.get(extraction_status, extraction_status)

    if not assessments:
        return (
            f"**Разбор требований** ({source_note}): отдельных пунктов не сформировано; "
            "MATCH/PARTIAL/MISSING и match_score строятся по полному тексту вакансии "
            "и фактам профиля (анализ продолжен)."
        )

    lines = [f"**Разбор требований** ({source_note}):"]
    for a in assessments:
        flag = {
            "MATCH": "✅",
            "PARTIAL": "🟨",
            "MISSING": "❌",
            "UNKNOWN": "❓",
        }.get(a.status, "•")
        kind = "must" if a.requirement.required else "nice"
        label = a.requirement.label()[:100]
        ev = a.evidence[:120] if a.evidence else ""
        extra = f" ← {a.matched_alternative}" if a.matched_alternative else ""
        lines.append(f"- {flag} `{kind}` **{label}**{extra}: {ev}")
    return "\n".join(lines)


def compute_skill_overlap(
    profile: str,
    vacancy_title: str,
    vacancy_description: str,
) -> dict:
    """Требование-ориентированная оценка; всегда с extraction_status."""
    assessments, extraction_status = assess_all_requirements_with_meta(
        profile, vacancy_title, vacancy_description
    )
    result = compute_match_score_from_assessments(assessments)
    result["required_groups"] = result["required_total"]
    result["covered_groups"] = result["required_matched"]
    result["covered_labels"] = result["matched_labels"]
    result["assessments"] = assessments
    result["extraction_status"] = extraction_status
    return result


def blend_scores(
    llm_score: float,
    formula_score: float,
    *,
    llm_weight: float = 0.35,
) -> float:
    """
    Итог: факты (formula) главные; LLM лишь слегка корректирует.
    По умолчанию 65% факты + 35% LLM.
    """
    w = max(0.0, min(1.0, llm_weight))
    blended = w * float(llm_score) + (1.0 - w) * float(formula_score)
    return max(0.0, min(100.0, round(blended, 1)))


def reconcile_missing_skills(
    llm_missing: Iterable[str],
    covered_labels: list[str],
    profile: str,
) -> list[str]:
    """Убрать из missing то, что явно есть в профиле или уже MATCH."""
    prof = _normalize(profile)
    covered_norm = {_normalize(x) for x in covered_labels}
    result: list[str] = []
    for item in llm_missing:
        s = str(item).strip()
        if not s:
            continue
        sn = _normalize(s)
        if any(c and (c in sn or sn in c) for c in covered_norm):
            continue
        if _contains_term(prof, sn) or sn in prof:
            continue
        expanded = expand_terms_for_token(sn)
        if profile_has_any(profile, expanded):
            continue
        denied_by_syn = False
        for syns in SKILL_SYNONYMS.values():
            if any(_contains_term(sn, t) or t in sn for t in syns):
                if profile_has_any(profile, syns):
                    denied_by_syn = True
                    break
        if denied_by_syn:
            continue
        result.append(s)
    return result[:10]


def assert_profile_facts_not_denied(
    reasons: list[str],
    missing: list[str],
    profile: str,
) -> tuple[list[str], list[str]]:
    """
    Запрет: нельзя писать «нет X», если X явно в профиле.
    Фильтрует reasons и missing.
    """
    prof = _normalize(profile)
    ban_patterns = []
    for syns in SKILL_SYNONYMS.values():
        if profile_has_any(profile, syns):
            ban_patterns.extend(syns)

    def _denies_fact(text: str) -> bool:
        t = _normalize(text)
        if not re.search(r"\b(нет|отсутств|не\s+указан|не\s+име|missing|lack|без\s+)\b", t):
            return False
        return any(_contains_term(t, p) or p in t for p in ban_patterns if len(p) >= 2)

    new_reasons = [r for r in reasons if not _denies_fact(str(r))]
    new_missing = []
    for m in missing:
        sm = _normalize(str(m))
        if profile_has_any(profile, expand_terms_for_token(sm)):
            continue
        if any(_contains_term(prof, p) for p in expand_terms_for_token(sm)):
            continue
        new_missing.append(m)
    return new_reasons, new_missing
