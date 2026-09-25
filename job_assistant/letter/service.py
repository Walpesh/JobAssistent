"""
Пакетная генерация сопроводительных писем для suitable-вакансий.
Очередь + батчинг, чтобы не перегружать Ollama.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from job_assistant.db import get_vacancies_by_status, get_vacancy_by_id, update_vacancy_analysis
from job_assistant.db.connection import get_connection
from job_assistant.llm import generate_cover_letter, extract_letter_body
from job_assistant.utils.logging import add_log

# Пауза между запросами к Ollama в батче (сек)
DEFAULT_BATCH_PAUSE_SEC = 1.0


@dataclass
class LetterReport:
    processed: int = 0
    generated: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    results: list[dict] = field(default_factory=list)


def get_vacancies_awaiting_letter(limit: int = 20) -> list[dict]:
    """
    Вакансии со status=analyzed, is_suitable=1 и без cover_letter.
    """
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, source, external_id, title, url, description,
                   company, salary, location, remote, match_score,
                   is_suitable, analysis_result, status
            FROM vacancies
            WHERE status = 'analyzed'
              AND is_suitable = 1
              AND (cover_letter IS NULL OR TRIM(cover_letter) = '')
            ORDER BY match_score DESC, parsed_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = cur.fetchall()
    return [dict(r) for r in rows]


def generate_letter_for_vacancy(
    vacancy: dict,
    profile: str,
    *,
    append_contacts: bool = True,
) -> Optional[str]:
    """
    Сгенерировать письмо для одной вакансии и сохранить в БД.
    Возвращает текст письма или None при ошибке.
    """
    vacancy_id = vacancy["id"]
    title = vacancy.get("title") or ""
    company = vacancy.get("company") or ""
    description = (vacancy.get("description") or "").strip()
    if not description:
        description = f"{title}\n{company}\n{vacancy.get('salary') or ''}"

    match_score = vacancy.get("match_score")
    try:
        match_score_f = float(match_score) if match_score is not None else None
    except (TypeError, ValueError):
        match_score_f = None

    full_text = generate_cover_letter(
        profile,
        description,
        vacancy_title=title,
        company=company,
        match_score=match_score_f,
        append_contacts=append_contacts,
    )

    if full_text.startswith("❌"):
        add_log("ERROR", f"Letter failed id={vacancy_id}: {full_text[:80]}")
        return None

    letter_body = extract_letter_body(full_text)

    # analysis_result: если уже есть оценка — дополняем, иначе пишем полный ответ
    existing_analysis = vacancy.get("analysis_result") or ""
    if existing_analysis and "### 📊" not in full_text:
        combined_analysis = existing_analysis
    else:
        # полный ответ модели содержит и % и письмо — храним целиком в analysis,
        # письмо отдельно в cover_letter
        combined_analysis = existing_analysis or full_text

    # Если в полном ответе есть блок анализа — можно обновить analysis_result
    if "### 📊" in full_text or "Процент совпадения" in full_text:
        combined_analysis = full_text

    update_vacancy_analysis(
        vacancy_id,
        cover_letter=letter_body,
        analysis_result=combined_analysis,
        # status остаётся analyzed
    )
    return letter_body


def run_letter_batch(
    profile: str,
    *,
    limit: int = 5,
    pause_sec: float = DEFAULT_BATCH_PAUSE_SEC,
    vacancy_ids: Optional[list[int]] = None,
    on_progress: Optional[Callable[[dict[str, Any]], None]] = None,
) -> LetterReport:
    """
    Пакетная генерация писем.

    - Берёт analyzed+suitable без cover_letter (или явный список id).
    - Между запросами — пауза pause_sec (защита Ollama/VRAM).
    """
    report = LetterReport()

    if not profile or not profile.strip():
        report.errors.append("Профиль пуст")
        add_log("ERROR", "Letter batch: пустой профиль")
        return report

    if vacancy_ids:
        vacancies = []
        for vid in vacancy_ids:
            v = get_vacancy_by_id(vid)
            if not v:
                continue
            if v.get("status") != "analyzed" or not v.get("is_suitable"):
                report.skipped += 1
                continue
            if v.get("cover_letter") and str(v.get("cover_letter")).strip():
                report.skipped += 1
                continue
            vacancies.append(v)
    else:
        vacancies = get_vacancies_awaiting_letter(limit=limit)

    add_log("INFO", f"Letter batch: {len(vacancies)} вакансий, pause={pause_sec}s")
    total_n = len(vacancies)

    def _prog(ev: dict) -> None:
        if on_progress:
            try:
                on_progress(ev)
            except Exception:
                pass

    _prog({"type": "start", "total": total_n, "phase": "letters"})

    for i, vac in enumerate(vacancies):
        _prog({
            "type": "item",
            "done": i,
            "total": total_n,
            "phase": "letters",
            "detail": f"Письмо: {(vac.get('title') or '')[:60]}",
        })
        try:
            letter = generate_letter_for_vacancy(vac, profile)
            report.processed += 1
            if letter:
                report.generated += 1
                report.results.append(
                    {
                        "id": vac["id"],
                        "title": vac.get("title"),
                        "score": vac.get("match_score"),
                        "letter_len": len(letter),
                    }
                )
            else:
                report.errors.append(f"id={vac['id']}: генерация не удалась")
        except Exception as e:
            msg = f"id={vac.get('id')}: {e}"
            report.errors.append(msg)
            add_log("ERROR", f"Letter batch error {msg}")

        # пауза между вызовами (кроме последнего)
        if i < len(vacancies) - 1 and pause_sec > 0:
            time.sleep(pause_sec)

    _prog({
        "type": "done",
        "done": report.processed,
        "total": total_n,
        "phase": "letters",
        "detail": f"Письма: {report.generated}/{report.processed}",
    })
    add_log(
        "SUCCESS",
        f"Letter batch: processed={report.processed}, generated={report.generated}, "
        f"skipped={report.skipped}, errors={len(report.errors)}",
    )
    return report
