"""
Кэш полных описаний вакансий (по url / source+external_id).
Снижает повторные запросы к источникам.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from job_assistant.db.connection import get_connection
from job_assistant.utils.logging import add_log

# TTL кэша по умолчанию (дни)
DEFAULT_CACHE_TTL_DAYS = 7


def get_cached_description(
    *,
    url: Optional[str] = None,
    source: Optional[str] = None,
    external_id: Optional[str] = None,
    max_age_days: int = DEFAULT_CACHE_TTL_DAYS,
) -> Optional[str]:
    """Вернуть закэшированное описание или None."""
    with get_connection() as conn:
        cur = conn.cursor()
        if source and external_id:
            cur.execute(
                """
                SELECT description, cached_at FROM description_cache
                WHERE source = ? AND external_id = ?
                LIMIT 1
                """,
                (source, external_id),
            )
        elif url:
            cur.execute(
                """
                SELECT description, cached_at FROM description_cache
                WHERE url = ?
                LIMIT 1
                """,
                (url,),
            )
        else:
            return None

        row = cur.fetchone()
        if not row:
            return None

        cached_at = row["cached_at"] or ""
        try:
            ts = datetime.fromisoformat(str(cached_at))
            if datetime.now() - ts > timedelta(days=max_age_days):
                return None
        except ValueError:
            pass

        desc = row["description"] or ""
        return desc if desc.strip() else None


def set_cached_description(
    description: str,
    *,
    url: Optional[str] = None,
    source: Optional[str] = None,
    external_id: Optional[str] = None,
) -> None:
    """Сохранить / обновить описание в кэше."""
    if not description or not description.strip():
        return
    if not url and not (source and external_id):
        return

    now = datetime.now().isoformat(timespec="seconds")
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            # upsert по url или source+external_id
            if source and external_id:
                cur.execute(
                    """
                    INSERT INTO description_cache (url, source, external_id, description, cached_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(source, external_id) DO UPDATE SET
                        description = excluded.description,
                        url = COALESCE(excluded.url, description_cache.url),
                        cached_at = excluded.cached_at
                    """,
                    (url, source, external_id, description[:15000], now),
                )
            elif url:
                cur.execute(
                    """
                    INSERT INTO description_cache (url, source, external_id, description, cached_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(url) DO UPDATE SET
                        description = excluded.description,
                        cached_at = excluded.cached_at
                    """,
                    (url, source, external_id, description[:15000], now),
                )
    except Exception as e:
        add_log("WARNING", f"description cache write failed: {e}")


def cache_stats() -> dict:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) AS c FROM description_cache")
        row = cur.fetchone()
        return {"entries": row["c"] if row else 0}
