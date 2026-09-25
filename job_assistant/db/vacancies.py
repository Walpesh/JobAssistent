"""
CRUD для таблицы vacancies.
Защита от дубликатов, сохранение анализа, очередь pending.
"""

from datetime import datetime
from typing import Any, Optional

from job_assistant.db.connection import get_connection
from job_assistant.utils.logging import add_log

# Допустимые статусы
VALID_STATUSES = ("new", "analyzed", "rejected", "sent")


def is_vacancy_exists(
    *,
    source: Optional[str] = None,
    external_id: Optional[str] = None,
    url: Optional[str] = None,
) -> bool:
    """
    Проверить, есть ли вакансия уже в БД.

    Приоритет проверки:
      1. source + external_id  (если оба переданы)
      2. url                   (если передан)

    Возвращает True, если дубликат найден.
    """
    if not any([source and external_id, url]):
        return False

    with get_connection() as conn:
        cur = conn.cursor()

        if source and external_id:
            cur.execute(
                """
                SELECT 1 FROM vacancies
                WHERE source = ? AND external_id = ?
                LIMIT 1
                """,
                (source, external_id),
            )
            if cur.fetchone():
                return True

        if url:
            cur.execute(
                """
                SELECT 1 FROM vacancies
                WHERE url = ?
                LIMIT 1
                """,
                (url,),
            )
            if cur.fetchone():
                return True

    return False


def save_vacancy(
    *,
    source: str,
    title: str,
    external_id: Optional[str] = None,
    url: Optional[str] = None,
    description: Optional[str] = None,
    company: Optional[str] = None,
    salary: Optional[str] = None,
    location: Optional[str] = None,
    remote: bool = False,
    status: str = "new",
    employment_type: Optional[str] = None,
) -> Optional[int]:
    """
    Сохранить новую вакансию.

    - Если дубликат (source+external_id или url) уже есть — не вставляет, возвращает None.
    - Иначе вставляет и возвращает id новой записи.

    status по умолчанию: 'new'
    """
    if status not in VALID_STATUSES:
        raise ValueError(f"Недопустимый status: {status}. Допустимо: {VALID_STATUSES}")

    if is_vacancy_exists(source=source, external_id=external_id, url=url):
        add_log(
            "WARNING",
            f"Дубликат вакансии пропущен: source={source}, external_id={external_id}, url={url}",
        )
        return None

    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO vacancies (
                    source, external_id, title, url, description,
                    company, salary, location, remote, parsed_at, status,
                    employment_type
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source,
                    external_id,
                    title,
                    url,
                    description,
                    company,
                    salary,
                    location,
                    1 if remote else 0,
                    datetime.now().isoformat(timespec="seconds"),
                    status,
                    employment_type or "unknown",
                ),
            )
            vacancy_id = cur.lastrowid

        add_log("SUCCESS", f"Вакансия сохранена id={vacancy_id}: «{title[:40]}…»")
        return vacancy_id
    except Exception as e:
        add_log("ERROR", f"Ошибка save_vacancy: {e}")
        raise


def update_vacancy_analysis(
    vacancy_id: int,
    *,
    match_score: Optional[float] = None,
    is_suitable: Optional[bool] = None,
    cover_letter: Optional[str] = None,
    analysis_result: Optional[str] = None,
    status: Optional[str] = None,
) -> bool:
    """
    Обновить результат анализа вакансии.

    Обновляются только переданные поля (None = не трогать).
    Если status не указан, но есть analysis_result — автоматически ставит 'analyzed'.
    Возвращает True, если запись найдена и обновлена.
    """
    if status is not None and status not in VALID_STATUSES:
        raise ValueError(f"Недопустимый status: {status}. Допустимо: {VALID_STATUSES}")

    fields: dict[str, Any] = {}
    if match_score is not None:
        fields["match_score"] = match_score
    if is_suitable is not None:
        fields["is_suitable"] = 1 if is_suitable else 0
    if cover_letter is not None:
        fields["cover_letter"] = cover_letter
    if analysis_result is not None:
        fields["analysis_result"] = analysis_result
    if status is not None:
        fields["status"] = status
    elif analysis_result is not None:
        fields["status"] = "analyzed"

    if not fields:
        return False

    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [vacancy_id]

    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                f"UPDATE vacancies SET {set_clause} WHERE id = ?",
                values,
            )
            updated = cur.rowcount > 0

        if updated:
            add_log("SUCCESS", f"Анализ вакансии id={vacancy_id} обновлён (status={fields.get('status', '—')}).")
        else:
            add_log("WARNING", f"Вакансия id={vacancy_id} не найдена для обновления.")
        return updated
    except Exception as e:
        add_log("ERROR", f"Ошибка update_vacancy_analysis: {e}")
        raise


def get_pending_vacancies(limit: int = 50) -> list[dict]:
    """
    Вернуть вакансии со статусом 'new' (ещё не проанализированные).
    Сортировка: сначала самые свежие.
    """
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, source, external_id, title, url, description,
                   company, salary, location, remote, parsed_at, status
            FROM vacancies
            WHERE status = 'new'
            ORDER BY parsed_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = cur.fetchall()

    return [dict(row) for row in rows]


def get_vacancy_by_id(vacancy_id: int) -> Optional[dict]:
    """Получить одну вакансию по внутреннему id."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM vacancies WHERE id = ?", (vacancy_id,))
        row = cur.fetchone()
    return dict(row) if row else None


def get_vacancies_by_status(status: str, limit: int = 100) -> list[dict]:
    """Вернуть вакансии с указанным статусом."""
    if status not in VALID_STATUSES:
        raise ValueError(f"Недопустимый status: {status}")

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, source, external_id, title, url, company, salary,
                   location, remote, match_score, is_suitable, status, parsed_at
            FROM vacancies
            WHERE status = ?
            ORDER BY parsed_at DESC
            LIMIT ?
            """,
            (status, limit),
        )
        rows = cur.fetchall()
    return [dict(row) for row in rows]


def set_vacancy_status(vacancy_id: int, status: str) -> bool:
    """Быстрая смена статуса (rejected / sent / ...)."""
    if status not in VALID_STATUSES:
        raise ValueError(f"Недопустимый status: {status}")

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE vacancies SET status = ? WHERE id = ?",
            (status, vacancy_id),
        )
        updated = cur.rowcount > 0

    if updated:
        add_log("INFO", f"Статус вакансии id={vacancy_id} → {status}")
    return updated


def list_vacancies(
    *,
    status: Optional[str] = None,
    suitable_only: bool = False,
    has_letter: Optional[bool] = None,
    limit: int = 200,
) -> list[dict]:
    """Список вакансий для UI-таблицы результатов."""
    clauses = []
    params: list[Any] = []

    if status:
        clauses.append("status = ?")
        params.append(status)
    if suitable_only:
        clauses.append("is_suitable = 1")
    if has_letter is True:
        clauses.append("cover_letter IS NOT NULL AND TRIM(cover_letter) != ''")
    elif has_letter is False:
        clauses.append("(cover_letter IS NULL OR TRIM(cover_letter) = '')")

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = f"""
        SELECT id, source, external_id, title, url, company, salary, location,
               remote, match_score, is_suitable, cover_letter, analysis_result,
               status, parsed_at, description, employment_type
        FROM vacancies
        {where}
        ORDER BY
            CASE status
                WHEN 'analyzed' THEN 0
                WHEN 'sent' THEN 1
                WHEN 'new' THEN 2
                WHEN 'rejected' THEN 3
                ELSE 4
            END,
            match_score IS NULL, match_score DESC,
            id DESC
        LIMIT ?
    """
    params.append(limit)

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def count_vacancies_by_status() -> dict[str, int]:
    """Счётчики по статусам: {new: N, analyzed: N, ...}."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT status, COUNT(*) AS c FROM vacancies GROUP BY status"
        )
        rows = cur.fetchall()
    result = {s: 0 for s in VALID_STATUSES}
    for r in rows:
        result[r["status"]] = r["c"]
    return result


def delete_vacancies_by_ids(ids: list[int]) -> int:
    """Удалить вакансии по id. Возвращает число удалённых."""
    if not ids:
        return 0
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            deleted = 0
            for vid in ids:
                cur.execute("DELETE FROM vacancies WHERE id = ?", (vid,))
                deleted += cur.rowcount
        add_log("SUCCESS", f"Удалено вакансий: {deleted}")
        return deleted
    except Exception as e:
        add_log("ERROR", f"delete_vacancies_by_ids: {e}")
        raise


def delete_tk_only_vacancies() -> dict:
    """
    Удалить вакансии, где указано ТОЛЬКО официальное устройство по ТК РФ.
    Если в тексте/поле есть и ТК, и ГПХ/самозанятый/по договорённости — не трогаем.
    Для записей без employment_type — определяем по title+description.
    """
    from job_assistant.utils.employment import (
        detect_employment_types,
        employment_to_str,
        is_tk_only,
        parse_employment_str,
    )

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, title, description, employment_type
            FROM vacancies
            """
        )
        rows = [dict(r) for r in cur.fetchall()]

    to_delete: list[int] = []
    kept = 0
    for r in rows:
        et = r.get("employment_type")
        types = parse_employment_str(et)
        if types == ["unknown"] or not et:
            types = detect_employment_types(r.get("title") or "", r.get("description") or "")
            # обновим поле для будущих фильтров
            try:
                with get_connection() as conn:
                    conn.cursor().execute(
                        "UPDATE vacancies SET employment_type = ? WHERE id = ?",
                        (employment_to_str(types), r["id"]),
                    )
            except Exception:
                pass
        if is_tk_only(types):
            to_delete.append(int(r["id"]))
        else:
            kept += 1

    deleted = delete_vacancies_by_ids(to_delete) if to_delete else 0
    add_log(
        "SUCCESS",
        f"Отсев ТК РФ: удалено={deleted}, оставлено={kept} "
        f"(смешанные ТК+ГПХ сохранены)",
    )
    return {"deleted": deleted, "kept": kept, "ids": to_delete}


def _norm_url(url: str | None) -> str:
    if not url:
        return ""
    u = str(url).strip().lower().rstrip("/")
    # убрать query/fragment — часто одни и те же вакансии с метками
    for sep in ("?", "#"):
        if sep in u:
            u = u.split(sep, 1)[0]
    return u.rstrip("/")


def _norm_title_company(title: str | None, company: str | None) -> str:
    import re

    t = (title or "").lower().replace("ё", "е")
    c = (company or "").lower().replace("ё", "е")
    t = re.sub(r"\s+", " ", t).strip()
    c = re.sub(r"\s+", " ", c).strip()
    # лёгкая нормализация пунктуации
    t = re.sub(r"[«»\"'()\[\].,;:!?]+", "", t)
    c = re.sub(r"[«»\"'()\[\].,;:!?]+", "", c)
    if not t:
        return ""
    return f"{t}||{c}"


def _keeper_rank(row: dict) -> tuple:
    """
    Чем выше ранг — тем ценнее запись (её оставляем).
    Приоритет: sent > analyzed > new > rejected;
    есть письмо; выше score; длиннее description; меньший id (старее).
    """
    status_rank = {
        "sent": 4,
        "analyzed": 3,
        "new": 2,
        "rejected": 1,
    }.get((row.get("status") or "").lower(), 0)
    has_letter = 1 if (row.get("cover_letter") or "").strip() else 0
    try:
        score = float(row.get("match_score") or 0)
    except (TypeError, ValueError):
        score = 0.0
    desc_len = len(row.get("description") or "")
    # id с минусом: при прочих равных оставляем меньший id
    vid = int(row.get("id") or 0)
    return (status_rank, has_letter, score, desc_len, -vid)


def delete_duplicate_vacancies() -> dict:
    """
    Просканировать всю таблицу vacancies и удалить повторы.

    Критерии дубля (в порядке применения):
      1. Одинаковый нормализованный URL
      2. Одинаковые source + external_id
      3. Одинаковые нормализованные title + company (если company не пустая)

    В каждой группе остаётся одна «лучшая» запись (см. _keeper_rank).
    """
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, source, external_id, title, url, company, description,
                   status, match_score, cover_letter, is_suitable
            FROM vacancies
            ORDER BY id ASC
            """
        )
        rows = [dict(r) for r in cur.fetchall()]

    if len(rows) < 2:
        return {
            "deleted": 0,
            "kept": len(rows),
            "groups": 0,
            "ids": [],
            "by_reason": {"url": 0, "source_external": 0, "title_company": 0},
        }

    # id -> row
    by_id = {int(r["id"]): r for r in rows}
    # id, который уже помечен на удаление
    doomed: set[int] = set()
    by_reason = {"url": 0, "source_external": 0, "title_company": 0}

    def _collapse(groups: dict[str, list[int]], reason: str) -> None:
        for key, ids in groups.items():
            if not key or len(ids) < 2:
                continue
            alive = [i for i in ids if i not in doomed and i in by_id]
            if len(alive) < 2:
                continue
            alive_sorted = sorted(alive, key=lambda i: _keeper_rank(by_id[i]), reverse=True)
            keep = alive_sorted[0]
            for dup_id in alive_sorted[1:]:
                if dup_id not in doomed:
                    doomed.add(dup_id)
                    by_reason[reason] = by_reason.get(reason, 0) + 1
            # keep остаётся

    # 1) URL
    url_groups: dict[str, list[int]] = {}
    for r in rows:
        key = _norm_url(r.get("url"))
        if not key:
            continue
        url_groups.setdefault(key, []).append(int(r["id"]))
    _collapse(url_groups, "url")

    # 2) source + external_id
    se_groups: dict[str, list[int]] = {}
    for r in rows:
        src = (r.get("source") or "").strip().lower()
        ext = (r.get("external_id") or "").strip()
        if not src or not ext:
            continue
        se_groups.setdefault(f"{src}::{ext}", []).append(int(r["id"]))
    _collapse(se_groups, "source_external")

    # 3) title + company (только если company задана — иначе слишком агрессивно)
    tc_groups: dict[str, list[int]] = {}
    for r in rows:
        company = (r.get("company") or "").strip()
        if not company:
            continue
        key = _norm_title_company(r.get("title"), company)
        if not key or key.endswith("||"):
            continue
        tc_groups.setdefault(key, []).append(int(r["id"]))
    _collapse(tc_groups, "title_company")

    to_delete = sorted(doomed)
    deleted = delete_vacancies_by_ids(to_delete) if to_delete else 0
    kept = len(rows) - deleted
    groups_count = sum(1 for n in by_reason.values() if n > 0)

    add_log(
        "SUCCESS",
        f"Отсев дублей: удалено={deleted}, оставлено={kept}, "
        f"по URL={by_reason['url']}, source+id={by_reason['source_external']}, "
        f"title+company={by_reason['title_company']}",
    )
    return {
        "deleted": deleted,
        "kept": kept,
        "groups": groups_count,
        "ids": to_delete,
        "by_reason": by_reason,
    }
