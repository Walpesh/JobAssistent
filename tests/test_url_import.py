from job_assistant.fetcher.hh_ru import HHRuSource


def test_parse_hh_search_url_basic():
    url = (
        "https://hh.ru/search/vacancy?text=python+developer"
        "&area=1&experience=between1And3&schedule=remote&salary=100000"
        "&only_with_salary=true&order_by=publication_time"
    )
    p = HHRuSource.parse_search_url(url)
    assert "python" in p["text"].lower()
    assert p["area"] == "1"
    assert p["experience"] == "between1And3"
    assert p.get("work_format") == "REMOTE"
    assert p["salary"] == "100000"
    assert int(p["per_page"]) >= 1
    assert "page" not in p


def test_parse_rejects_non_hh():
    try:
        HHRuSource.parse_search_url("https://example.com/jobs")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_parse_url_without_scheme():
    p = HHRuSource.parse_search_url("hh.ru/search/vacancy?text=java")
    assert p["text"] == "java"


def test_parse_novokuznetsk_url_sanitizes_web_params():
    url = (
        "https://novokuznetsk.hh.ru/search/vacancy?from=suggest_post"
        "&ored_clusters=true&text=Unity+developer&experience=noExperience"
        "&search_field=name&search_field=description"
    )
    p = HHRuSource.parse_search_url(url)
    assert "hhtmFrom" not in p and "ored_clusters" not in p and "from" not in p
    assert p["experience"] == "noExperience"
    assert p["search_field"] == ["name", "description"] or "name" in str(p["search_field"])
    assert "_original_url" in p
    assert "novokuznetsk.hh.ru" in p["_original_url"]
