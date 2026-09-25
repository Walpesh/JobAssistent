"""
7.2 Тесты на дубликаты и корректность сохранения.
"""

from __future__ import annotations

from job_assistant.db import (
    get_pending_vacancies,
    get_vacancy_by_id,
    is_vacancy_exists,
    list_vacancies,
    save_vacancy,
    set_vacancy_status,
    update_vacancy_analysis,
)


def test_save_and_exists(temp_db):
    vid = save_vacancy(
        source="hh.ru",
        external_id="1001",
        title="Python Dev",
        url="https://hh.ru/vacancy/1001",
        description="Need Python",
        company="Corp",
        remote=True,
    )
    assert vid is not None
    assert is_vacancy_exists(source="hh.ru", external_id="1001") is True
    assert is_vacancy_exists(url="https://hh.ru/vacancy/1001") is True


def test_duplicate_source_external_skipped(temp_db):
    a = save_vacancy(
        source="hh.ru",
        external_id="2002",
        title="First",
        url="https://hh.ru/vacancy/2002",
    )
    b = save_vacancy(
        source="hh.ru",
        external_id="2002",
        title="Duplicate",
        url="https://hh.ru/vacancy/2002-other",
    )
    assert a is not None
    assert b is None


def test_duplicate_url_skipped(temp_db):
    a = save_vacancy(
        source="remoteok",
        external_id="r1",
        title="A",
        url="https://remoteok.com/remote-jobs/same",
    )
    b = save_vacancy(
        source="weworkremotely",
        external_id="w1",
        title="B",
        url="https://remoteok.com/remote-jobs/same",
    )
    assert a is not None
    assert b is None  # same url


def test_same_external_different_source_allowed(temp_db):
    a = save_vacancy(source="hh.ru", external_id="x", title="HH", url="https://hh.ru/x")
    b = save_vacancy(
        source="remoteok",
        external_id="x",
        title="RO",
        url="https://remoteok.com/x",
    )
    assert a is not None and b is not None


def test_update_analysis_and_status(temp_db):
    vid = save_vacancy(
        source="hh.ru",
        external_id="3003",
        title="Analyst",
        url="https://hh.ru/vacancy/3003",
        description="desc",
    )
    assert vid
    ok = update_vacancy_analysis(
        vid,
        match_score=72.5,
        is_suitable=True,
        analysis_result="### Оценка: 72%\n- Python ok",
        cover_letter="Письмо...",
        status="analyzed",
    )
    assert ok is True
    row = get_vacancy_by_id(vid)
    assert row["match_score"] == 72.5
    assert row["is_suitable"] == 1
    assert row["status"] == "analyzed"
    assert "Письмо" in row["cover_letter"]


def test_pending_queue(temp_db):
    save_vacancy(source="hh.ru", external_id="n1", title="New1", url="https://hh.ru/n1")
    vid = save_vacancy(
        source="hh.ru", external_id="n2", title="New2", url="https://hh.ru/n2"
    )
    update_vacancy_analysis(vid, status="analyzed", match_score=80, is_suitable=True)
    pending = get_pending_vacancies()
    titles = {p["title"] for p in pending}
    assert "New1" in titles
    assert "New2" not in titles


def test_set_status_sent_rejected(temp_db):
    vid = save_vacancy(
        source="hh.ru", external_id="s1", title="Send me", url="https://hh.ru/s1"
    )
    set_vacancy_status(vid, "sent")
    assert get_vacancy_by_id(vid)["status"] == "sent"
    set_vacancy_status(vid, "rejected")
    assert get_vacancy_by_id(vid)["status"] == "rejected"


def test_list_vacancies_filters(temp_db):
    v1 = save_vacancy(
        source="hh.ru", external_id="l1", title="Suitable", url="https://hh.ru/l1"
    )
    update_vacancy_analysis(
        v1, match_score=80, is_suitable=True, status="analyzed", cover_letter="Hi"
    )
    save_vacancy(source="hh.ru", external_id="l2", title="New", url="https://hh.ru/l2")
    analyzed = list_vacancies(status="analyzed")
    assert any(r["title"] == "Suitable" for r in analyzed)
    with_letter = list_vacancies(has_letter=True)
    assert any(r["title"] == "Suitable" for r in with_letter)
