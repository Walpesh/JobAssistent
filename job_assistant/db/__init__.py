"""
Слой данных JobAssistant.
"""

from job_assistant.db.schema import init_db
from job_assistant.db.chats import (
    save_chat_to_db,
    get_all_chats,
    get_chat_by_id,
    delete_chat,
)
from job_assistant.db.vacancies import (
    is_vacancy_exists,
    save_vacancy,
    update_vacancy_analysis,
    get_pending_vacancies,
    get_vacancy_by_id,
    get_vacancies_by_status,
    set_vacancy_status,
    list_vacancies,
    count_vacancies_by_status,
    delete_vacancies_by_ids,
    delete_tk_only_vacancies,
    delete_duplicate_vacancies,
)
from job_assistant.db.runs import (
    start_autofetch_run,
    finish_autofetch_run,
    get_autofetch_runs,
)
from job_assistant.db.cache import (
    get_cached_description,
    set_cached_description,
    cache_stats,
)

__all__ = [
    "init_db",
    "save_chat_to_db",
    "get_all_chats",
    "get_chat_by_id",
    "delete_chat",
    "is_vacancy_exists",
    "save_vacancy",
    "update_vacancy_analysis",
    "get_pending_vacancies",
    "get_vacancy_by_id",
    "get_vacancies_by_status",
    "set_vacancy_status",
    "list_vacancies",
    "count_vacancies_by_status",
    "delete_vacancies_by_ids",
    "delete_tk_only_vacancies",
    "delete_duplicate_vacancies",
    "start_autofetch_run",
    "finish_autofetch_run",
    "get_autofetch_runs",
    "get_cached_description",
    "set_cached_description",
    "cache_stats",
]
