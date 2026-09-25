"""
Модуль генерации сопроводительных писем (пакетный режим).
"""

from job_assistant.letter.service import (
    LetterReport,
    generate_letter_for_vacancy,
    get_vacancies_awaiting_letter,
    run_letter_batch,
)

__all__ = [
    "LetterReport",
    "generate_letter_for_vacancy",
    "get_vacancies_awaiting_letter",
    "run_letter_batch",
]
