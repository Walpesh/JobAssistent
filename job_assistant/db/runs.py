"""
История автопоисков (autofetch_runs).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional

from job_assistant.db.connection import get_connection


def start_autofetch_run(
    *,
    keywords: str = "",
    sources: list[str] | None = None,
    filters: dict[str, Any] | None = None,
) -> int:
    """Создать запись о запуске, вернуть id."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO autofetch_runs (keywords, sources, filters_json, status, started_at)
            VALUES (?, ?, ?, 'running', ?)
            """,
            (
                keywords,
                ",".join(sources or []),
                json.dumps(filters or {}, ensure_ascii=False),
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        return int(cur.lastrowid)


def finish_autofetch_run(
    run_id: int,
    *,
    found_count: int = 0,
    saved_count: int = 0,
    duplicates: int = 0,
    analyzed_count: int = 0,
    suitable_count: int = 0,
    rejected_count: int = 0,
    letters_count: int = 0,
    status: str = "done",
    error_message: Optional[str] = None,
) -> None:
    """Обновить итоги прогона."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE autofetch_runs SET
                finished_at = ?,
                found_count = ?,
                saved_count = ?,
                duplicates = ?,
                analyzed_count = ?,
                suitable_count = ?,
                rejected_count = ?,
                letters_count = ?,
                status = ?,
                error_message = ?
            WHERE id = ?
            """,
            (
                datetime.now().isoformat(timespec="seconds"),
                found_count,
                saved_count,
                duplicates,
                analyzed_count,
                suitable_count,
                rejected_count,
                letters_count,
                status,
                error_message,
                run_id,
            ),
        )


def get_autofetch_runs(limit: int = 30) -> list[dict]:
    """История запусков, новые сверху."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT * FROM autofetch_runs
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [dict(r) for r in cur.fetchall()]
