"""
Модуль автопоиска и парсинга вакансий (Vacancy Fetcher).

Источники:
  - hh.ru          (официальный API)
  - remoteok       (публичный JSON API)
  - weworkremotely (RSS)

Оркестратор: run_autofetch(filters) → FetchReport
"""

from job_assistant.fetcher.base import NormalizedVacancy, SearchFilters, VacancySource
from job_assistant.fetcher.service import FetchReport, get_default_sources, run_autofetch
from job_assistant.fetcher.hh_ru import HHRuSource, build_apply_url
from job_assistant.fetcher.url_import import UrlImportReport, run_hh_search_url_import
from job_assistant.fetcher.remote_ok import RemoteOKSource
from job_assistant.fetcher.weworkremotely import WeWorkRemotelySource

__all__ = [
    "NormalizedVacancy",
    "SearchFilters",
    "VacancySource",
    "FetchReport",
    "get_default_sources",
    "run_autofetch",
    "HHRuSource",
    "RemoteOKSource",
    "WeWorkRemotelySource",
    "build_apply_url",
    "UrlImportReport",
    "run_hh_search_url_import",
]
