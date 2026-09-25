"""
Сквозной пайплайн: target_total → save → analyze(saved_ids) → letters(suitable).
Ollama мокается.
"""

from __future__ import annotations

from unittest.mock import patch

from job_assistant.analyzer.models import SuitabilityResult
from job_assistant.db import get_vacancy_by_id, init_db, list_vacancies
from job_assistant.fetcher.base import NormalizedVacancy, SearchFilters
from job_assistant.fetcher.service import run_autofetch
from job_assistant.analyzer.service import run_suitability_analysis
from job_assistant.letter.service import run_letter_batch


class FakeSource:
    name = "fake"

    def __init__(self, n: int):
        self.n = n

    def fetch(self, filters: SearchFilters):
        out = []
        for i in range(self.n):
            out.append(
                NormalizedVacancy(
                    source=self.name,
                    external_id=f"e-{i}",
                    title=f"Job {i}",
                    url=f"https://example.com/{i}",
                    description=f"Python required for job {i}. " * 20,
                    company=f"Co{i}",
                    remote=True,
                )
            )
        return out

    def search(self, filters):
        return []

    def normalize(self, raw):
        return None


def test_target_total_caps_saves(temp_db):
    report = run_autofetch(
        SearchFilters(keywords="python", per_page=50, target_total=7),
        sources=[FakeSource(20)],
        enrich=False,
    )
    assert report.saved == 7
    assert len(report.saved_ids) == 7


def test_full_chain_analyze_and_letters(temp_db):
    """Все флаги: 5 новых → 5 оценок → письма для suitable (score>=65)."""
    report = run_autofetch(
        SearchFilters(keywords="python", target_total=5, per_page=10),
        sources=[FakeSource(10)],
        enrich=False,
    )
    assert report.saved == 5
    ids = report.saved_ids

    def fake_judge(profile, title, description, company=""):
        # чётные id-индексы — suitable
        n = int(title.split()[-1])
        score = 80.0 if n % 2 == 0 else 30.0
        return SuitabilityResult(
            suitable=score >= 65,
            match_score=score,
            reasons=["test"],
            missing_skills=[] if score >= 65 else ["K8s"],
            summary="mock",
        )

    with patch("job_assistant.analyzer.service.judge_vacancy", side_effect=fake_judge):
        ar = run_suitability_analysis(
            "Python pet projects FastAPI Streamlit",
            vacancy_ids=ids,
            threshold=65,
            generate_letters=False,
        )

    assert ar.processed == 5
    assert ar.suitable + ar.rejected == 5
    assert ar.suitable == 3  # 0,2,4
    assert ar.rejected == 2

    suitable_ids = [r["id"] for r in ar.results if r.get("suitable")]
    assert len(suitable_ids) == 3

    with patch(
        "job_assistant.letter.service.generate_cover_letter",
        return_value="### ✉️ Сопроводительное письмо\nТестовое письмо.",
    ):
        lr = run_letter_batch("profile", vacancy_ids=suitable_ids, pause_sec=0)

    assert lr.generated == 3
    for vid in suitable_ids:
        row = get_vacancy_by_id(vid)
        assert row["status"] == "analyzed"
        assert row["is_suitable"] == 1
        assert row["cover_letter"] and "письмо" in row["cover_letter"].lower()


def test_analyze_only_new_from_ids_not_old_queue(temp_db):
    """Оценка по vacancy_ids не трогает чужие new."""
    run_autofetch(
        SearchFilters(keywords="x", target_total=3, per_page=10),
        sources=[FakeSource(3)],
        enrich=False,
    )
    # ещё 2 «старых» new через второй прогон с другими id
    class S2:
        name = "fake2"
        def fetch(self, filters):
            from job_assistant.fetcher.base import NormalizedVacancy
            return [
                NormalizedVacancy(
                    source=self.name,
                    external_id=f"s2-{i}",
                    title=f"Other {i}",
                    url=f"https://other.example/{i}",
                    description="x " * 50,
                    company="Z",
                    remote=True,
                )
                for i in range(2)
            ]
        def search(self, f): return []
        def normalize(self, r): return None

    run_autofetch(
        SearchFilters(keywords="y", target_total=2, per_page=10),
        sources=[S2()],
        enrich=False,
    )

    from job_assistant.db import get_pending_vacancies

    all_new = get_pending_vacancies(limit=50)
    assert len(all_new) == 5
    only_first = [v["id"] for v in all_new if v["source"] == "fake"]

    with patch(
        "job_assistant.analyzer.service.judge_vacancy",
        return_value=SuitabilityResult(True, 70, ["ok"], [], "s"),
    ):
        ar = run_suitability_analysis("p", vacancy_ids=only_first, threshold=65)

    assert ar.processed == 3
    still_new = get_pending_vacancies(limit=50)
    assert len(still_new) == 2
    assert all(v["source"] == "fake2" for v in still_new)
