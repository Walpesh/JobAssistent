"""
Модуль 2 — Suitability Analyzer.
Строгая оценка соответствия профиля вакансии через локальную LLM + формула навыков.
"""

from job_assistant.analyzer.models import SuitabilityResult
from job_assistant.analyzer.service import (
    DEFAULT_MATCH_THRESHOLD,
    AnalyzeReport,
    analyze_vacancy,
    reanalyze_all_vacancies,
    run_suitability_analysis,
)

__all__ = [
    "SuitabilityResult",
    "AnalyzeReport",
    "DEFAULT_MATCH_THRESHOLD",
    "analyze_vacancy",
    "run_suitability_analysis",
    "reanalyze_all_vacancies",
]
