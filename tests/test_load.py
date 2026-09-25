"""
7.5 Нагрузочное тестирование сохранения / очереди (50–100 вакансий).

Сеть и Ollama не трогаем — синтетические записи.
Для live-нагрузки fetch: RUN_LIVE_FETCH=1 pytest tests/test_load.py -k live
"""

from __future__ import annotations

import os
import time

import pytest

from job_assistant.db import (
    get_pending_vacancies,
    is_vacancy_exists,
    list_vacancies,
    save_vacancy,
    update_vacancy_analysis,
)


def test_bulk_insert_100(temp_db):
    n = 100
    t0 = time.perf_counter()
    ids = []
    for i in range(n):
        vid = save_vacancy(
            source="hh.ru" if i % 2 == 0 else "remoteok",
            external_id=f"load-{i}",
            title=f"Load Test Vacancy {i}",
            url=f"https://example.com/job/{i}",
            description=f"Description for job {i} " * 5,
            company=f"Company-{i % 10}",
            remote=i % 3 == 0,
        )
        assert vid is not None
        ids.append(vid)
    elapsed = time.perf_counter() - t0
    assert len(ids) == n
    assert len(get_pending_vacancies(limit=500)) == n
    # повторный insert тех же external_id — все дубли
    for i in range(n):
        assert (
            save_vacancy(
                source="hh.ru" if i % 2 == 0 else "remoteok",
                external_id=f"load-{i}",
                title="dup",
                url=f"https://example.com/job/{i}",
            )
            is None
        )
    print(f"bulk 100 insert: {elapsed:.3f}s ({n/elapsed:.0f} rows/s)")
    assert elapsed < 5.0  # локальный SQLite должен быть быстрым


def test_bulk_analyze_status_transition(temp_db):
    for i in range(50):
        save_vacancy(
            source="hh.ru",
            external_id=f"an-{i}",
            title=f"Job {i}",
            url=f"https://example.com/a/{i}",
        )
    pending = get_pending_vacancies(limit=100)
    assert len(pending) == 50

    suitable = 0
    for i, vac in enumerate(pending):
        score = 40 + (i % 50)  # 40..89
        passes = score >= 65
        update_vacancy_analysis(
            vac["id"],
            match_score=float(score),
            is_suitable=passes,
            analysis_result=f"score={score}",
            status="analyzed" if passes else "rejected",
        )
        if passes:
            suitable += 1

    assert len(get_pending_vacancies(limit=100)) == 0
    analyzed = list_vacancies(status="analyzed", limit=200)
    rejected = list_vacancies(status="rejected", limit=200)
    assert len(analyzed) == suitable
    assert len(analyzed) + len(rejected) == 50


@pytest.mark.skipif(
    os.environ.get("RUN_LIVE_FETCH") != "1",
    reason="Живой fetch: RUN_LIVE_FETCH=1",
)
def test_live_fetch_volume():
    from job_assistant.fetcher import (
        HHRuSource,
        RemoteOKSource,
        SearchFilters,
        WeWorkRemotelySource,
        run_autofetch,
    )
    import job_assistant.config as cfg
    import job_assistant.db.connection as conn_mod
    from job_assistant.db import init_db
    import tempfile

    db = tempfile.mktemp(suffix=".db")
    cfg.DB_NAME = db
    conn_mod.DB_NAME = db
    init_db()

    filters = SearchFilters(keywords="python", remote_only=True, per_page=20, max_pages=1)
    report = run_autofetch(
        filters,
        sources=[HHRuSource(), RemoteOKSource(), WeWorkRemotelySource()],
        enrich=False,
    )
    print(
        f"live fetch: found={report.total_found} saved={report.saved} "
        f"dup={report.duplicates} by_source={report.by_source}"
    )
    assert report.total_found >= 5
    assert report.saved + report.duplicates >= report.saved
