"""
CRUD для таблицы chats (история ручных анализов — обратная совместимость).
"""

from job_assistant.db.connection import get_connection
from job_assistant.utils.logging import add_log


def save_chat_to_db(
    title: str,
    job_url: str,
    job_desc: str,
    analysis: str,
) -> None:
    """Сохранить результат ручного анализа в таблицу chats."""
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO chats (title, job_url, job_description, analysis_result, cover_letter)
                VALUES (?, ?, ?, ?, ?)
                """,
                (title, job_url, job_desc, analysis, ""),
            )
        add_log("SUCCESS", f"Чат '{title[:30]}...' успешно сохранен в SQLite.")
    except Exception as e:
        add_log("ERROR", f"Ошибка сохранения в БД: {e}")


def get_all_chats() -> list:
    """Вернуть список всех чатов (id, title, created_at) по убыванию id."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id, title, created_at FROM chats ORDER BY id DESC")
        rows = cur.fetchall()
    # Сохраняем формат tuple для обратной совместимости с UI
    return [(r["id"], r["title"], r["created_at"]) for r in rows]


def get_chat_by_id(chat_id: int) -> tuple | None:
    """Вернуть данные одного чата по id (формат как раньше)."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT job_url, job_description, analysis_result, cover_letter, title
            FROM chats WHERE id = ?
            """,
            (chat_id,),
        )
        row = cur.fetchone()
    if not row:
        return None
    return (
        row["job_url"],
        row["job_description"],
        row["analysis_result"],
        row["cover_letter"],
        row["title"],
    )


def delete_chat(chat_id: int) -> None:
    """Удалить чат по id."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM chats WHERE id = ?", (chat_id,))
    add_log("INFO", f"Чат с ID {chat_id} удален из базы данных.")
