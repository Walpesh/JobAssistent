from job_assistant.db.vacancies import (
    save_vacancy,
    delete_duplicate_vacancies,
    list_vacancies,
    get_vacancy_by_id,
)
from job_assistant.db import update_vacancy_analysis


def test_dedupe_by_url(temp_db):
    a = save_vacancy(
        source="hh.ru",
        external_id="1",
        title="Python Dev",
        url="https://hh.ru/vacancy/111?from=share",
        description="A" * 50,
        company="Co",
    )
    b = save_vacancy(
        source="hh.ru",
        external_id="2",
        title="Python Developer",
        url="https://hh.ru/vacancy/111",
        description="B" * 200,
        company="Co",
    )
    # unique index may block same external - we used different external_id
    assert a and b
    update_vacancy_analysis(b, match_score=80, is_suitable=True, status="analyzed")

    res = delete_duplicate_vacancies()
    assert res["deleted"] >= 1
    remaining_urls = [
        (r.get("url") or "")
        for r in list_vacancies(limit=50)
    ]
    # only one of the two urls family should remain
    assert sum(1 for u in remaining_urls if "111" in u) == 1


def test_dedupe_by_title_company(temp_db):
    a = save_vacancy(
        source="remoteok",
        external_id="x1",
        title="Senior Backend Engineer",
        url="https://example.com/a",
        company="Acme Inc",
        description="short",
    )
    b = save_vacancy(
        source="weworkremotely",
        external_id="x2",
        title="Senior Backend Engineer",
        url="https://example.com/b",
        company="Acme Inc",
        description="long description " * 20,
    )
    assert a and b
    res = delete_duplicate_vacancies()
    assert res["deleted"] == 1
    assert res["by_reason"]["title_company"] == 1
    left = list_vacancies(limit=50)
    assert len(left) == 1
    # longer description kept
    assert len(left[0].get("description") or "") > 50
