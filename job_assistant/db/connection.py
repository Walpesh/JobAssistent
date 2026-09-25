"""
Общее подключение к SQLite.
"""

import sqlite3
from contextlib import contextmanager
from typing import Iterator

from job_assistant.config import DB_NAME


@contextmanager
def get_connection() -> Iterator[sqlite3.Connection]:
    """Контекстный менеджер подключения с row_factory = Row."""
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
