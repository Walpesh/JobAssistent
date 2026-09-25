"""
Единый интерфейс источников вакансий (VacancySource).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class SearchFilters:
    """Фильтры автопоиска."""

    keywords: str = ""
    remote_only: bool = True
    salary_min: Optional[int] = None
    currency: str = "RUR"  # RUR | USD | EUR
    experience: Optional[str] = None  # noExperience | between1And3 | between3And6 | moreThan6
    language: Optional[str] = None  # ru | en | …
    location: Optional[str] = None
    per_page: int = 20
    max_pages: int = 1  # сколько страниц за один прогон
    target_total: int | None = None  # общий лимит новых вакансий за прогон (все источники)
    # Предпочтительные типы устройства: tk | gph | self | agree (пусто = любые)
    employment_types: list[str] | None = None


@dataclass
class NormalizedVacancy:
    """Единый формат вакансии после normalize()."""

    source: str
    external_id: str
    title: str
    url: str
    description: str = ""
    company: str = ""
    salary: str = ""
    location: str = ""
    remote: bool = False
    raw: dict[str, Any] = field(default_factory=dict)


class VacancySource(ABC):
    """
    Абстрактный адаптер источника вакансий.

    Реализации:
      - search(filters)  → список сырых dict
      - get_details(url) → расширенное описание (опционально)
      - normalize(raw)   → NormalizedVacancy
    """

    name: str = "base"

    @abstractmethod
    def search(self, filters: SearchFilters) -> list[dict[str, Any]]:
        """Найти вакансии. Возвращает сырые dict с источника."""
        ...

    def get_details(self, url: str) -> Optional[dict[str, Any]]:
        """Подгрузить полное описание (по умолчанию — no-op)."""
        return None

    @abstractmethod
    def normalize(self, raw: dict[str, Any]) -> Optional[NormalizedVacancy]:
        """Привести сырую запись к NormalizedVacancy. None = пропустить."""
        ...

    def fetch(self, filters: SearchFilters) -> list[NormalizedVacancy]:
        """search → normalize. Удобный метод верхнего уровня."""
        results: list[NormalizedVacancy] = []
        for raw in self.search(filters):
            try:
                item = self.normalize(raw)
                if item:
                    results.append(item)
            except Exception:
                continue
        return results
