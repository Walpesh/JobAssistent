"""
Импорт вакансий по готовой ссылке поиска HH.ru.

Переиспользует HHRuSource.normalize + save_vacancy (тот же путь, что автопоиск).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from job_assistant.db import save_vacancy
from job_assistant.fetcher.hh_ru import HHRuSource
from job_assistant.fetcher.service import FetchReport
from job_assistant.utils.employment import (
    EMP_TK,
    detect_employment_types,
    employment_to_str,
    is_tk_only,
)
from job_assistant.utils.logging import add_log


@dataclass
class UrlImportReport:
    found: int = 0
    fetched: int = 0
    saved: int = 0
    duplicates: int = 0
    errors: list[str] = field(default_factory=list)
    stopped: bool = False
    pages_done: int = 0
    pages_total: int = 0
    saved_ids: list[int] = field(default_factory=list)


def run_hh_search_url_import(
    url: str,
    *,
    on_event: Optional[Callable[[dict[str, Any]], None]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
    employment_types: Optional[list[str]] = None,
) -> UrlImportReport:
    """
    Спарсить все страницы поиска HH по URL и сохранить в БД.

    - normalize через HHRuSource
    - дедуп через save_vacancy / is_vacancy_exists
    - should_stop проверяется между страницами (внутри fetch_all_from_search_url)
      и перед сохранением каждой пачки
    """
    report = UrlImportReport()
    source = HHRuSource()

    def _emit(ev: dict[str, Any]) -> None:
        if on_event:
            try:
                on_event(ev)
            except Exception:
                pass

    try:
        api_params = HHRuSource.parse_search_url(url)
    except ValueError as e:
        report.errors.append(str(e))
        add_log("ERROR", f"URL-import: {e}")
        _emit({"type": "error", "message": str(e)})
        return report

    add_log("INFO", f"URL-import старт: {url[:120]}… params={list(api_params.keys())}")
    _emit({"type": "start", "url": url, "params": {k: api_params[k] for k in list(api_params)[:12]}})

    progress_state: dict[str, Any] = {}

    def on_progress(info: dict[str, Any]) -> None:
        progress_state.update(info)
        report.found = int(info.get("found") or report.found)
        report.pages_total = int(info.get("pages") or report.pages_total)
        report.pages_done = int(info.get("page") or 0) + 1
        report.fetched = int(info.get("fetched") or report.fetched)
        if info.get("status") == "stopped":
            report.stopped = True
        _emit(
            {
                "type": "page",
                "page": info.get("page"),
                "pages": info.get("pages"),
                "found": info.get("found"),
                "fetched": info.get("fetched"),
                "status": info.get("status"),
            }
        )

    try:
        vacancies = source.fetch_all_from_search_url(
            url,
            should_stop=should_stop,
            on_progress=on_progress,
        )
    except Exception as e:
        report.errors.append(str(e))
        add_log("ERROR", f"URL-import fetch failed: {e}")
        _emit({"type": "error", "message": str(e)})
        return report

    report.fetched = len(vacancies)
    report.found = max(report.found, len(vacancies))
    preferred = set(employment_types) if employment_types else None

    for item in vacancies:
        if should_stop and should_stop():
            report.stopped = True
            add_log("WARNING", "URL-import: остановка перед сохранением оставшихся")
            break

        try:
            emp_types = detect_employment_types(
                item.title or "",
                item.description or "",
                item.company or "",
            )
            emp_str = employment_to_str(emp_types)

            if preferred and "unknown" not in emp_types:
                if is_tk_only(emp_types) and EMP_TK not in preferred:
                    continue
                if not (set(emp_types) & preferred):
                    continue

            vid = save_vacancy(
                source=item.source,
                external_id=item.external_id,
                title=item.title,
                url=item.url,
                description=item.description,
                company=item.company,
                salary=item.salary,
                location=item.location,
                remote=item.remote,
                status="new",
                employment_type=emp_str,
            )
            if vid is None:
                report.duplicates += 1
                _emit(
                    {
                        "type": "duplicate",
                        "title": item.title,
                        "url": item.url,
                    }
                )
            else:
                report.saved += 1
                report.saved_ids.append(vid)
                _emit(
                    {
                        "type": "saved",
                        "id": vid,
                        "title": item.title,
                        "saved_total": report.saved,
                    }
                )
        except Exception as e:
            report.errors.append(f"{item.title[:40]}: {e}")
            _emit({"type": "error", "message": str(e)})

    status = "stopped" if report.stopped else "done"
    add_log(
        "SUCCESS",
        f"URL-import {status}: found≈{report.found}, fetched={report.fetched}, "
        f"saved={report.saved}, duplicates={report.duplicates}, "
        f"errors={len(report.errors)}",
    )
    _emit(
        {
            "type": "done",
            "found": report.found,
            "fetched": report.fetched,
            "saved": report.saved,
            "duplicates": report.duplicates,
            "errors": len(report.errors),
            "stopped": report.stopped,
        }
    )
    return report
