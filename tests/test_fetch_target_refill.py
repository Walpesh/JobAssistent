from job_assistant.fetcher.base import NormalizedVacancy, SearchFilters
from job_assistant.fetcher.service import run_autofetch


class PagedFakeSource:
    name = "paged"

    def __init__(self, total: int):
        self.total = total
        self.calls = []

    def fetch(self, filters: SearchFilters):
        self.calls.append(filters.max_pages)
        n = min(self.total, filters.per_page * filters.max_pages)
        return [
            NormalizedVacancy(
                source=self.name,
                external_id=f"job-{i}",
                title=f"Job {i}",
                url=f"https://example.com/{i}",
                description="Python " * 100,
                company="Co",
                remote=True,
            )
            for i in range(n)
        ]


def test_refills_until_target_after_duplicates(temp_db):
    source = PagedFakeSource(20)

    report = run_autofetch(
        SearchFilters(keywords="python", per_page=5, max_pages=1, target_total=12),
        sources=[source],
        enrich=False,
    )

    assert report.saved == 12
    assert len(report.saved_ids) == 12
    assert report.target_reached is True
    assert report.exhausted is False
    # Должен быть хотя бы один повторный запрос с большей глубиной
    assert len(source.calls) >= 2
    assert max(source.calls) >= 2


def test_refill_ignores_db_duplicates_and_continues(temp_db):
    source = PagedFakeSource(20)

    first = run_autofetch(
        SearchFilters(keywords="python", per_page=5, target_total=5),
        sources=[source],
        enrich=False,
    )
    assert first.saved == 5

    # Вторая цель — ещё 7 новых. Первые 5 во втором проходе являются DB-дублями,
    # затем оркестратор расширяет выборку и берёт вакансии 5..11.
    second = run_autofetch(
        SearchFilters(keywords="python", per_page=5, target_total=7),
        sources=[source],
        enrich=False,
    )

    assert second.saved == 7
    assert second.target_reached is True
    assert second.duplicates >= 5


def test_reports_exhaustion_without_analyzing_partial_result(temp_db):
    source = PagedFakeSource(6)

    report = run_autofetch(
        SearchFilters(keywords="python", per_page=3, target_total=10),
        sources=[source],
        enrich=False,
    )

    assert report.saved == 6
    assert report.target_reached is False
    assert report.exhausted is True


def test_keeps_searching_until_target_100(temp_db):
    """Цель 100 — оркестратор крутит проходы, пока не наберёт 100."""
    source = PagedFakeSource(150)

    report = run_autofetch(
        SearchFilters(keywords="python", per_page=10, max_pages=1, target_total=100),
        sources=[source],
        enrich=False,
    )

    assert report.saved == 100
    assert report.target_reached is True
    assert report.exhausted is False
    assert len(source.calls) >= 2
