"""
Оркестратор Suitability Analyzer:
очередь new / повторный анализ → enrich description → formula+judge → update DB.
Каждая вакансия обрабатывается отдельным запросом к модели (без общей «сессии»).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from job_assistant.analyzer.llm_judge import judge_vacancy
from job_assistant.analyzer.models import SuitabilityResult
from job_assistant.db import (
    get_pending_vacancies,
    get_vacancy_by_id,
    list_vacancies,
    update_vacancy_analysis,
)
from job_assistant.db.connection import get_connection
from job_assistant.utils.logging import add_log

DEFAULT_MATCH_THRESHOLD = 65.0
MIN_DESCRIPTION_LEN = 200
# Пауза между запросами к Ollama — снижает деградацию качества при пачке
DEFAULT_JUDGE_PAUSE_SEC = 1.2


@dataclass
class AnalyzeReport:
    processed: int = 0
    suitable: int = 0
    rejected: int = 0
    errors: list[str] = field(default_factory=list)
    results: list[dict] = field(default_factory=list)


def _persist_description(vacancy_id: int, description: str) -> None:
    """Сохранить обогащённое описание в БД, чтобы следующий проход не шёл с snippet."""
    if not description or not vacancy_id:
        return
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE vacancies SET description = ? WHERE id = ?",
                (description[:15000], vacancy_id),
            )
    except Exception as e:
        add_log("WARNING", f"Не удалось сохранить description id={vacancy_id}: {e}")


def _enrich_vacancy_description(vacancy: dict) -> str:
    """
    Если description короткий — кэш, затем адаптер источника.
    Успешные полные тексты пишутся в description_cache и в vacancies.description.
    """
    import re

    from job_assistant.db.cache import get_cached_description, set_cached_description

    desc = (vacancy.get("description") or "").strip()
    if len(desc) >= MIN_DESCRIPTION_LEN:
        return desc

    url = vacancy.get("url") or ""
    source = vacancy.get("source") or ""
    external_id = vacancy.get("external_id") or ""
    vacancy_id = vacancy.get("id")

    cached = get_cached_description(
        url=url or None, source=source or None, external_id=external_id or None
    )
    if cached and len(cached) >= MIN_DESCRIPTION_LEN:
        add_log("INFO", f"Description cache HIT id={vacancy_id}")
        if vacancy_id:
            _persist_description(int(vacancy_id), cached)
        return cached[:10000]

    if not url:
        return desc

    try:
        if source == "hh.ru":
            from job_assistant.fetcher.hh_ru import HHRuSource

            details = HHRuSource().get_details(url)
            if details:
                raw_desc = details.get("description") or ""
                if raw_desc:
                    text = re.sub(r"<[^>]+>", "\n", raw_desc)
                    text = re.sub(r"\n{3,}", "\n\n", text).strip()
                    if len(text) > len(desc):
                        set_cached_description(
                            text,
                            url=url,
                            source=source,
                            external_id=str(external_id) if external_id else None,
                        )
                        if vacancy_id:
                            _persist_description(int(vacancy_id), text)
                        add_log(
                            "INFO",
                            f"Enrich HH id={vacancy_id}: {len(text)} символов",
                        )
                        return text[:10000]
        elif source == "remoteok":
            # description обычно уже полный в API
            pass
    except Exception as e:
        add_log("WARNING", f"Enrich failed id={vacancy_id}: {e}")

    return desc


def analyze_vacancy(
    vacancy: dict,
    profile: str,
    threshold: float = DEFAULT_MATCH_THRESHOLD,
) -> SuitabilityResult:
    """
    Оценить одну вакансию и записать результат в БД.
    status: analyzed если score >= threshold, иначе rejected.
    """
    vacancy_id = vacancy["id"]
    title = vacancy.get("title") or "Без названия"
    company = vacancy.get("company") or ""
    description = _enrich_vacancy_description(vacancy)

    if not description.strip():
        description = (
            f"{title}\n{company}\n"
            f"{vacancy.get('salary') or ''}\n{vacancy.get('location') or ''}"
        )

    result = judge_vacancy(profile, title, description, company)

    passes = result.match_score >= threshold
    result.suitable = passes
    status = "analyzed" if passes else "rejected"

    update_vacancy_analysis(
        vacancy_id,
        match_score=result.match_score,
        is_suitable=passes,
        analysis_result=result.to_analysis_text(),
        status=status,
    )

    return result


def run_suitability_analysis(
    profile: str,
    *,
    limit: int = 20,
    threshold: float = DEFAULT_MATCH_THRESHOLD,
    vacancy_ids: Optional[list[int]] = None,
    generate_letters: bool = False,
    letter_pause_sec: float = 1.0,
    pause_sec: float = DEFAULT_JUDGE_PAUSE_SEC,
    include_statuses: Optional[list[str]] = None,
    on_progress: Optional[Callable[[dict[str, Any]], None]] = None,
) -> AnalyzeReport:
    """
    Прогнать очередь status='new' (или явный список id).

    pause_sec — пауза между вакансиями (нагрузка на Ollama + качество).
    """
    report = AnalyzeReport()

    if not profile or not profile.strip():
        report.errors.append("Профиль пуст — анализ невозможен")
        add_log("ERROR", "Suitability: пустой профиль")
        return report

    if vacancy_ids:
        vacancies = []
        for vid in vacancy_ids:
            v = get_vacancy_by_id(vid)
            if not v:
                continue
            # при явном списке id разрешаем new (и другие, если include_statuses)
            if include_statuses:
                if v.get("status") in include_statuses:
                    vacancies.append(v)
            elif v.get("status") == "new":
                vacancies.append(v)
        vacancies = vacancies[: max(1, int(limit))] if limit else vacancies
    else:
        vacancies = get_pending_vacancies(limit=limit)

    add_log(
        "INFO",
        f"Suitability analysis: {len(vacancies)} вакансий, threshold={threshold}, "
        f"pause={pause_sec}s, generate_letters={generate_letters}",
    )

    suitable_ids: list[int] = []
    total_n = len(vacancies)

    def _prog(ev: dict) -> None:
        if on_progress:
            try:
                on_progress(ev)
            except Exception:
                pass

    _prog({"type": "start", "total": total_n, "phase": "analyze"})

    for i, vac in enumerate(vacancies):
        _prog({
            "type": "item",
            "done": i,
            "total": total_n,
            "phase": "analyze",
            "detail": f"Оценка: {(vac.get('title') or '')[:60]}",
        })
        try:
            result = analyze_vacancy(vac, profile, threshold=threshold)
            report.processed += 1
            passes = bool(result.suitable and result.match_score >= threshold)
            if passes:
                report.suitable += 1
                suitable_ids.append(vac["id"])
            else:
                report.rejected += 1

            report.results.append(
                {
                    "id": vac["id"],
                    "title": vac.get("title"),
                    "suitable": passes,
                    "score": result.match_score,
                }
            )
        except Exception as e:
            msg = f"id={vac.get('id')}: {e}"
            report.errors.append(msg)
            add_log("ERROR", f"Suitability error {msg}")

        # Пауза между вакансиями (кроме последней)
        if pause_sec > 0 and i < len(vacancies) - 1:
            time.sleep(pause_sec)

    _prog({
        "type": "done",
        "done": report.processed,
        "total": total_n,
        "phase": "analyze",
        "detail": f"Оценка завершена: {report.processed}/{total_n}",
    })

    if generate_letters and suitable_ids:
        try:
            from job_assistant.letter import run_letter_batch

            letter_report = run_letter_batch(
                profile,
                vacancy_ids=suitable_ids,
                pause_sec=letter_pause_sec,
            )
            add_log(
                "INFO",
                f"Letters after analyze: {letter_report.generated}/{letter_report.processed}",
            )
        except Exception as e:
            report.errors.append(f"letters: {e}")
            add_log("ERROR", f"Letters after analyze failed: {e}")

    add_log(
        "SUCCESS",
        f"Suitability готово: processed={report.processed}, "
        f"suitable={report.suitable}, rejected={report.rejected}, "
        f"errors={len(report.errors)}",
    )
    return report


def reanalyze_all_vacancies(
    profile: str,
    *,
    threshold: float = DEFAULT_MATCH_THRESHOLD,
    pause_sec: float = DEFAULT_JUDGE_PAUSE_SEC,
    statuses: Optional[list[str]] = None,
    limit: int = 500,
    on_progress: Optional[Callable[[dict[str, Any]], None]] = None,
) -> AnalyzeReport:
    """
    Повторный анализ вакансий одним прогоном.

    По умолчанию: new + analyzed + rejected (не sent).
    Каждая вакансия — отдельный запрос к модели, с паузой.
    """
    if statuses is None:
        statuses = ["new", "analyzed", "rejected"]

    report = AnalyzeReport()
    if not profile or not profile.strip():
        report.errors.append("Профиль пуст")
        return report

    # Собираем id по статусам
    rows: list[dict] = []
    for st in statuses:
        part = list_vacancies(status=st, limit=limit)
        rows.extend(part)

    # дедуп по id, свежие первыми уже из list
    seen: set[int] = set()
    ordered: list[dict] = []
    for r in rows:
        vid = r.get("id")
        if vid is None or vid in seen:
            continue
        seen.add(vid)
        ordered.append(r)
        if len(ordered) >= limit:
            break

    add_log(
        "INFO",
        f"Повторный анализ: {len(ordered)} вакансий, statuses={statuses}, "
        f"threshold={threshold}, pause={pause_sec}s",
    )

    total_n = len(ordered)

    def _prog(ev: dict) -> None:
        if on_progress:
            try:
                on_progress(ev)
            except Exception:
                pass

    _prog({"type": "start", "total": total_n, "phase": "reanalyze"})

    for i, vac in enumerate(ordered):
        _prog({
            "type": "item",
            "done": i,
            "total": total_n,
            "phase": "reanalyze",
            "detail": f"Повторный анализ: {(vac.get('title') or '')[:60]}",
        })
        try:
            # Сброс статуса в new логически не нужен: analyze_vacancy перезапишет
            result = analyze_vacancy(vac, profile, threshold=threshold)
            report.processed += 1
            passes = bool(result.suitable and result.match_score >= threshold)
            if passes:
                report.suitable += 1
            else:
                report.rejected += 1
            report.results.append(
                {
                    "id": vac["id"],
                    "title": vac.get("title"),
                    "suitable": passes,
                    "score": result.match_score,
                }
            )
            add_log(
                "INFO",
                f"Reanalyze [{i+1}/{len(ordered)}] id={vac['id']} "
                f"→ {result.match_score:.0f}% suitable={passes}",
            )
        except Exception as e:
            report.errors.append(f"id={vac.get('id')}: {e}")
            add_log("ERROR", f"Reanalyze error id={vac.get('id')}: {e}")

        if pause_sec > 0 and i < len(ordered) - 1:
            time.sleep(pause_sec)

    _prog({
        "type": "done",
        "done": report.processed,
        "total": total_n,
        "phase": "reanalyze",
        "detail": f"Повторный анализ: {report.processed}/{total_n}",
    })
    add_log(
        "SUCCESS",
        f"Повторный анализ готов: processed={report.processed}, "
        f"suitable={report.suitable}, rejected={report.rejected}",
    )
    return report
