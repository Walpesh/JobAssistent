"""
Инициализация схемы БД: таблицы chats + vacancies, индексы, миграции.
"""

from job_assistant.db.connection import get_connection
from job_assistant.utils.logging import add_log

# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

CHATS_DDL = """
CREATE TABLE IF NOT EXISTS chats (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    title           TEXT,
    job_url         TEXT,
    job_description TEXT,
    analysis_result TEXT,
    cover_letter    TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

VACANCIES_DDL = """
CREATE TABLE IF NOT EXISTS vacancies (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT NOT NULL,              -- hh.ru | remoteok | superjob | manual | ...
    external_id     TEXT,                       -- ID вакансии на источнике (nullable для manual)
    title           TEXT NOT NULL,
    url             TEXT,
    description     TEXT,
    company         TEXT,
    salary          TEXT,
    location        TEXT,
    remote          INTEGER NOT NULL DEFAULT 0,  -- 0/1 (bool)
    parsed_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    match_score     REAL,                       -- 0.0 – 100.0
    is_suitable     INTEGER,                    -- 0/1/NULL
    cover_letter    TEXT,
    analysis_result TEXT,
    status          TEXT NOT NULL DEFAULT 'new'
                    CHECK (status IN ('new', 'analyzed', 'rejected', 'sent'))
)
"""


AUTOFETCH_RUNS_DDL = """
CREATE TABLE IF NOT EXISTS autofetch_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    finished_at     TIMESTAMP,
    keywords        TEXT,
    sources         TEXT,
    filters_json    TEXT,
    found_count     INTEGER DEFAULT 0,
    saved_count     INTEGER DEFAULT 0,
    duplicates      INTEGER DEFAULT 0,
    analyzed_count  INTEGER DEFAULT 0,
    suitable_count  INTEGER DEFAULT 0,
    rejected_count  INTEGER DEFAULT 0,
    letters_count   INTEGER DEFAULT 0,
    status          TEXT DEFAULT 'running',
    error_message   TEXT
)
"""

# Уникальность пары (source, external_id) — защита от дубликатов с одного сайта
INDEX_SOURCE_EXTERNAL = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_vacancies_source_external
ON vacancies (source, external_id)
WHERE external_id IS NOT NULL
"""

# Быстрый поиск по URL
INDEX_URL = """
CREATE INDEX IF NOT EXISTS idx_vacancies_url
ON vacancies (url)
WHERE url IS NOT NULL
"""

# Фильтр по статусу (очередь pending)
INDEX_STATUS = """
CREATE INDEX IF NOT EXISTS idx_vacancies_status
ON vacancies (status)
"""



DESCRIPTION_CACHE_DDL = """
CREATE TABLE IF NOT EXISTS description_cache (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    url             TEXT,
    source          TEXT,
    external_id     TEXT,
    description     TEXT NOT NULL,
    cached_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(url),
    UNIQUE(source, external_id)
)
"""

def init_db() -> None:
    """
    Создать / мигрировать таблицы и индексы при старте приложения.
    Идемпотентно: безопасно вызывать при каждом запуске.
    """
    try:
        with get_connection() as conn:
            cur = conn.cursor()

            # 1. Таблица чатов (обратная совместимость)
            cur.execute(CHATS_DDL)

            # 2. Таблица вакансий
            cur.execute(VACANCIES_DDL)

            # 2b. История автопоисков
            cur.execute(AUTOFETCH_RUNS_DDL)
            cur.execute(DESCRIPTION_CACHE_DDL)

            # 3. Индексы
            cur.execute(INDEX_SOURCE_EXTERNAL)
            cur.execute(INDEX_URL)
            cur.execute(INDEX_STATUS)

            # 4. Миграции: employment_type
            cur.execute("PRAGMA table_info(vacancies)")
            cols = {row[1] for row in cur.fetchall()}
            if "employment_type" not in cols:
                cur.execute(
                    "ALTER TABLE vacancies ADD COLUMN employment_type TEXT DEFAULT 'unknown'"
                )

        add_log("SUCCESS", "БД инициализирована: chats + vacancies + autofetch_runs + description_cache.")
    except Exception as e:
        add_log("ERROR", f"Ошибка инициализации БД: {e}")
        raise
