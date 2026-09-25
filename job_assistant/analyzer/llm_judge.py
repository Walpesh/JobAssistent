"""
Оценка соответствия: требования вакансии ↔ факты профиля.

Extraction → (fallback) → coverage score → LLM (вторично).
Пустой structured extraction никогда не останавливает анализ.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Optional

import requests

from job_assistant.analyzer.models import SuitabilityResult
from job_assistant.analyzer.prompts import build_suitability_prompt
from job_assistant.analyzer.scoring import (
    assert_profile_facts_not_denied,
    blend_scores,
    build_requirements_breakdown_text,
    compute_skill_overlap,
    reconcile_missing_skills,
)
from job_assistant.config import OLLAMA_URL, OLLAMA_MODEL
from job_assistant.utils.logging import add_log

JUDGE_OPTIONS = {
    "temperature": 0.1,
    "top_p": 0.75,
    "top_k": 30,
    "repeat_penalty": 1.12,
    "num_ctx": 12288,
    "num_predict": 1000,
}


def _extract_json(text: str) -> tuple[Optional[dict], str]:
    """
    Достать JSON из ответа LLM.
    Возвращает (data|None, parse_status):
      ok | markdown | repaired | failed
    """
    if not text or not str(text).strip():
        return None, "failed"

    raw = str(text).strip()

    # 1) чистый JSON
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data, "ok"
    except json.JSONDecodeError:
        pass

    # 2) markdown fence
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(1))
            if isinstance(data, dict):
                return data, "markdown"
        except json.JSONDecodeError:
            pass

    # 3) первый объект { ... } с балансом скобок
    start = raw.find("{")
    if start >= 0:
        depth = 0
        for i in range(start, len(raw)):
            ch = raw[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    chunk = raw[start : i + 1]
                    try:
                        data = json.loads(chunk)
                        if isinstance(data, dict):
                            return data, "repaired"
                    except json.JSONDecodeError:
                        break

    # 4) мягкий regex (как раньше)
    m = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", raw, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(0))
            if isinstance(data, dict):
                return data, "repaired"
        except json.JSONDecodeError:
            pass

    return None, "failed"


def _safe_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x) for x in value if str(x).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _parse_result(data: dict, raw: str) -> SuitabilityResult:
    score = data.get("match_score", data.get("score", 0))
    try:
        score = float(score)
    except (TypeError, ValueError):
        score = 0.0
    score = max(0.0, min(100.0, score))

    suitable_raw = data.get("suitable", data.get("is_suitable", False))
    if isinstance(suitable_raw, str):
        suitable = suitable_raw.strip().lower() in ("1", "true", "yes", "да")
    else:
        suitable = bool(suitable_raw)

    return SuitabilityResult(
        suitable=suitable,
        match_score=score,
        reasons=_safe_list(data.get("reasons"))[:10],
        missing_skills=_safe_list(
            data.get("missing_skills") or data.get("missing")
        )[:10],
        summary=str(data.get("summary") or data.get("comment") or ""),
        raw_response=raw,
    )


def _requirements_hint(overlap: dict) -> str:
    assessments = overlap.get("assessments") or []
    status = overlap.get("extraction_status") or "unknown"
    if not assessments:
        return (
            f"extraction={status}; coverage_score={overlap.get('formula_score')}; "
            f"структурированных требований нет — оцени по полному тексту вакансии."
        )
    lines = [
        f"extraction={status}; coverage_score={overlap.get('formula_score')}; "
        f"must MATCH {overlap.get('required_matched')}/{overlap.get('required_total')}, "
        f"MISSING {overlap.get('required_missing')}"
    ]
    for a in assessments[:20]:
        try:
            label = a.requirement.label()[:90]
            st = a.status
            req = "must" if a.requirement.required else "nice"
            ev = (a.evidence or "")[:80]
            alt = f" (via {a.matched_alternative})" if a.matched_alternative else ""
            lines.append(f"- [{st}] ({req}) {label}{alt}: {ev}")
        except Exception:
            continue
    return "\n".join(lines)


def judge_vacancy(
    profile: str,
    title: str,
    description: str,
    company: str = "",
) -> SuitabilityResult:
    """
    1) Extraction (+fallback) требований.
    2) Coverage score по фактам профиля.
    3) LLM (вторично); при битом JSON — только факты.
    """
    overlap = compute_skill_overlap(profile, title, description)
    extraction_status = overlap.get("extraction_status") or "unknown"
    assessments = overlap.get("assessments") or []
    req_hint = _requirements_hint(overlap)
    breakdown = build_requirements_breakdown_text(
        assessments, extraction_status=extraction_status
    )
    fact_score = float(overlap.get("formula_score") or 50.0)

    add_log(
        "INFO",
        f"Extraction: status={extraction_status}, "
        f"requirements={len(assessments)}, coverage={fact_score:.0f}% "
        f"for «{(title or '')[:40]}»",
    )
    if extraction_status == "fallback":
        add_log(
            "INFO",
            "Extraction fallback: structured list empty → skill/full-text requirements",
        )
    elif extraction_status == "empty":
        add_log(
            "WARNING",
            "Extraction empty: нет маркеров в тексте — анализ по полному описанию (LLM+profile)",
        )
    elif extraction_status == "structured":
        add_log(
            "INFO",
            f"Extraction structured OK: {len(assessments)} requirement(s)",
        )

    prompt = build_suitability_prompt(
        profile,
        title,
        description,
        company,
        formula_hint=f"coverage_score={fact_score}; extraction={extraction_status}",
        requirements_hint=req_hint,
    )
    add_log("INFO", f"Suitability judge: «{(title or '')[:40]}…» → {OLLAMA_MODEL}")
    start = time.time()

    try:
        resp = requests.post(
            OLLAMA_URL,
            json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "format": "json",
                "options": JUDGE_OPTIONS,
                "keep_alive": "0",
            },
            timeout=300,
        )
        if resp.status_code != 200:
            add_log("ERROR", f"Ollama judge HTTP {resp.status_code}")
            return _result_from_facts_only(
                fact_score,
                overlap,
                breakdown,
                extra=f"Ollama HTTP {resp.status_code}",
                llm_status="http_error",
            )

        body = resp.json() if resp.content else {}
        raw_text = ""
        if isinstance(body, dict):
            raw_text = body.get("response") or ""
            msg = body.get("message")
            if not raw_text and isinstance(msg, dict):
                raw_text = msg.get("content") or msg.get("response") or ""
            elif not raw_text and isinstance(msg, str):
                raw_text = msg
        raw_text = raw_text or (resp.text or "")

        data, parse_status = _extract_json(raw_text)
        elapsed = round(time.time() - start, 2)

        if data is None:
            add_log(
                "WARNING",
                f"LLM JSON parse={parse_status} ({elapsed}s) — продолжаем по coverage/fallback",
            )
            return _result_from_facts_only(
                fact_score,
                overlap,
                breakdown,
                extra=f"LLM JSON {parse_status}",
                raw=raw_text,
                llm_status=f"json_{parse_status}",
            )

        add_log("INFO", f"LLM JSON parse={parse_status} ({elapsed}s)")
        result = _parse_result(data, raw_text)

        # Пустые поля LLM — не ошибка
        result.missing_skills = reconcile_missing_skills(
            result.missing_skills or [],
            list(overlap.get("matched_labels") or overlap.get("covered_labels") or []),
            profile,
        )
        result.reasons, result.missing_skills = assert_profile_facts_not_denied(
            result.reasons or [], result.missing_skills or [], profile
        )

        blended = blend_scores(result.match_score, fact_score, llm_weight=0.35)

        req_total = int(overlap.get("required_total") or 0)
        req_matched = int(overlap.get("required_matched") or 0)
        req_missing = int(overlap.get("required_missing") or 0)

        if req_total > 0 and req_missing == 0 and req_matched >= max(1, req_total - 1):
            blended = max(blended, min(85.0, max(fact_score, 70.0)))
            result.reasons = list(result.reasons) + [
                "Обязательные требования закрыты фактами профиля (AND/OR учтены)"
            ]

        if req_missing == 0 and result.match_score < fact_score - 15:
            blended = max(blended, fact_score)

        # Даже при empty extraction — score и reasons есть
        if extraction_status in ("empty", "fallback") and not result.reasons:
            result.reasons = [
                f"Оценка по полному тексту (extraction={extraction_status})",
            ]

        result.match_score = blended
        result.reasons = list(result.reasons)[:10]
        note = (
            f" [coverage {fact_score:.0f}% extraction={extraction_status}: must "
            f"{req_matched}/{req_total}, missing {req_missing}]"
        )
        result.summary = ((result.summary or "") + note).strip()
        result.requirements_breakdown = breakdown

        add_log(
            "SUCCESS",
            f"Judge «{(title or '')[:30]}…»: blend={result.match_score:.0f} "
            f"coverage={fact_score:.0f} extraction={extraction_status} "
            f"llm_json={parse_status} suitable={result.suitable} ({elapsed}s)",
        )
        return result

    except requests.exceptions.ConnectionError:
        add_log("ERROR", "Judge: Ollama недоступна")
        return _result_from_facts_only(
            fact_score, overlap, breakdown, extra="Ollama offline", llm_status="offline"
        )
    except Exception as e:
        add_log("ERROR", f"Judge exception: {e}")
        return _result_from_facts_only(
            fact_score * 0.5,
            overlap,
            breakdown,
            extra=str(e),
            llm_status="exception",
        )


def _result_from_facts_only(
    fact_score: float,
    overlap: dict,
    breakdown: str,
    *,
    extra: str = "",
    raw: str = "",
    llm_status: str = "facts_only",
) -> SuitabilityResult:
    missing = list(overlap.get("missing_labels") or [])
    matched = list(overlap.get("matched_labels") or [])
    extraction_status = overlap.get("extraction_status") or "unknown"
    reasons = [
        f"Оценка по покрытию требований ({fact_score:.0f}%, extraction={extraction_status})",
    ]
    if extra:
        reasons.append(extra)
    if matched:
        reasons.append("Закрыто: " + "; ".join(matched[:5]))
    if missing:
        reasons.append("Не закрыто: " + "; ".join(missing[:5]))
    if not matched and not missing:
        reasons.append(
            "Структурированный список требований неполный — "
            "используйте полный текст вакансии и профиль (анализ выполнен)."
        )

    result = SuitabilityResult(
        suitable=fact_score >= 65,
        match_score=float(fact_score),
        reasons=reasons[:8],
        missing_skills=missing[:10],
        summary=(
            f"Coverage-based score (llm={llm_status}, extraction={extraction_status})."
            f"{(' ' + extra) if extra else ''}"
        ),
        raw_response=(raw or "")[:1000],
    )
    result.requirements_breakdown = breakdown
    add_log(
        "INFO",
        f"Judge facts-only: score={fact_score:.0f} extraction={extraction_status} "
        f"llm_status={llm_status}",
    )
    return result
