"""
7.1 Юнит-тесты адаптеров источников (без сети — mock HTTP).
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from job_assistant.fetcher.base import SearchFilters
from job_assistant.fetcher.hh_ru import HHRuSource
from job_assistant.fetcher.remote_ok import RemoteOKSource
from job_assistant.fetcher.weworkremotely import WeWorkRemotelySource


def _mock_response(payload, status=200, content=None):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = payload
    resp.content = content if content is not None else b""
    resp.text = content.decode() if isinstance(content, bytes) else (content or "")
    resp.raise_for_status = MagicMock()
    return resp


class TestHHRuSource:
    def test_normalize_api_item(self):
        raw = {
            "id": "123",
            "name": "Python Developer",
            "alternate_url": "https://hh.ru/vacancy/123",
            "employer": {"name": "TechCorp"},
            "salary": {"from": 150000, "to": 250000, "currency": "RUR"},
            "area": {"name": "Москва"},
            "schedule": {"id": "remote"},
            "snippet": {"requirement": "Python", "responsibility": "API"},
        }
        item = HHRuSource().normalize(raw)
        assert item is not None
        assert item.source == "hh.ru"
        assert item.external_id == "123"
        assert item.title == "Python Developer"
        assert item.company == "TechCorp"
        assert item.remote is True
        assert "150000" in item.salary or "от" in item.salary

    def test_normalize_skips_empty(self):
        assert HHRuSource().normalize({"id": "", "name": ""}) is None
        assert HHRuSource().normalize({"id": "1", "name": ""}) is None

    def test_search_api_success(self):
        payload = {
            "found": 1,
            "pages": 1,
            "items": [
                {
                    "id": "99",
                    "name": "Backend",
                    "alternate_url": "https://hh.ru/vacancy/99",
                    "employer": {"name": "X"},
                    "salary": None,
                    "area": {"name": "СПб"},
                    "schedule": {"id": "remote"},
                    "snippet": {},
                }
            ],
        }
        with patch(
            "job_assistant.fetcher.hh_ru.request_with_retry",
            return_value=_mock_response(payload),
        ):
            items = HHRuSource().search(SearchFilters(keywords="python", per_page=5))
        assert len(items) == 1
        assert items[0]["id"] == "99"

    def test_search_api_fail_falls_to_html(self):
        html = """
        <div data-qa="vacancy-serp__vacancy">
          <a data-qa="serp-item__title" href="/vacancy/555">ML Engineer</a>
          <a data-qa="vacancy-serp__vacancy-employer">AI Lab</a>
        </div>
        """
        with patch(
            "job_assistant.fetcher.hh_ru.request_with_retry",
            side_effect=[
                RuntimeError("API down"),
                _mock_response({}, content=html.encode("utf-8")),
            ],
        ):
            # _search_api catches and returns [], then _search_html is called
            src = HHRuSource()
            with patch.object(src, "_search_api", return_value=[]):
                items = src._search_html(SearchFilters(keywords="ml", per_page=5))
        # HTML path with mock on request inside _search_html
        with patch(
            "job_assistant.fetcher.hh_ru.request_with_retry",
            return_value=_mock_response({}, content=html.encode("utf-8")),
        ):
            items = HHRuSource()._search_html(SearchFilters(keywords="ml", per_page=5))
        assert any(i.get("name") == "ML Engineer" for i in items)

    def test_fetch_pipeline(self):
        raw = {
            "id": "7",
            "name": "DevOps",
            "alternate_url": "https://hh.ru/vacancy/7",
            "employer": {"name": "Ops"},
            "salary": None,
            "area": {"name": "Remote"},
            "schedule": {"id": "remote"},
            "snippet": {},
        }
        with patch.object(HHRuSource, "search", return_value=[raw]):
            result = HHRuSource().fetch(SearchFilters(keywords="devops"))
        assert len(result) == 1
        assert result[0].title == "DevOps"


class TestRemoteOKSource:
    def test_normalize(self):
        raw = {
            "id": "42",
            "position": "Senior Python",
            "company": "RemoteCo",
            "location": "Worldwide",
            "salary_min": 80000,
            "salary_max": 120000,
            "description": "<p>Build APIs</p>",
            "url": "/remote-jobs/senior-python",
            "tags": ["python"],
        }
        item = RemoteOKSource().normalize(raw)
        assert item is not None
        assert item.source == "remoteok"
        assert item.remote is True
        assert "Build APIs" in item.description
        assert item.url.startswith("https://")

    def test_search_filters_metadata(self):
        payload = [
            {"last_updated": 123},  # metadata
            {
                "id": "1",
                "position": "Python Engineer",
                "company": "A",
                "tags": ["python"],
                "description": "python fastapi",
            },
            {
                "id": "2",
                "position": "Java Lead",
                "company": "B",
                "tags": ["java"],
                "description": "spring",
            },
        ]
        with patch(
            "job_assistant.fetcher.remote_ok.request_with_retry",
            return_value=_mock_response(payload),
        ):
            items = RemoteOKSource().search(
                SearchFilters(keywords="python", per_page=10)
            )
        assert all("position" in i for i in items)
        assert any(i["id"] == "1" for i in items)


class TestWeWorkRemotelySource:
    def test_normalize_title_split(self):
        raw = {
            "title": "Acme Inc: Platform Engineer",
            "link": "https://weworkremotely.com/jobs/123",
            "guid": "https://weworkremotely.com/jobs/123",
            "description": "Remote role",
        }
        item = WeWorkRemotelySource().normalize(raw)
        assert item is not None
        assert item.company == "Acme Inc"
        assert item.title == "Platform Engineer"
        assert item.remote is True

    def test_search_rss(self):
        rss = b"""<?xml version="1.0"?>
        <rss><channel>
          <item>
            <title>Co: Python Dev</title>
            <link>https://weworkremotely.com/jobs/9</link>
            <guid>https://weworkremotely.com/jobs/9</guid>
            <description>Need python</description>
          </item>
          <item>
            <title>Other: Java</title>
            <link>https://weworkremotely.com/jobs/10</link>
            <guid>10</guid>
            <description>java only</description>
          </item>
        </channel></rss>
        """
        with patch(
            "job_assistant.fetcher.weworkremotely.request_with_retry",
            return_value=_mock_response({}, content=rss),
        ):
            items = WeWorkRemotelySource().search(
                SearchFilters(keywords="python", per_page=10)
            )
        assert len(items) == 1
        assert "Python" in items[0]["title"]


def test_build_apply_url_hh():
    from job_assistant.fetcher.hh_ru import build_apply_url

    assert "vacancyId=12345" in build_apply_url("hh.ru", "https://hh.ru/vacancy/12345", "12345")
    assert build_apply_url("remoteok", "https://remoteok.com/x", "") == "https://remoteok.com/x"


def test_hh_multi_strategy_dedupes():
    """Несколько стратегий не должны плодить дубликаты по id."""
    payload = {
        "found": 1,
        "pages": 1,
        "items": [
            {
                "id": "42",
                "name": "Python Dev",
                "alternate_url": "https://hh.ru/vacancy/42",
                "employer": {"name": "Co"},
                "salary": None,
                "area": {"name": "Москва"},
                "schedule": {"id": "remote"},
                "snippet": {},
            }
        ],
    }
    with patch(
        "job_assistant.fetcher.hh_ru.request_with_retry",
        return_value=_mock_response(payload),
    ):
        items = HHRuSource().search(SearchFilters(keywords="python", per_page=10, max_pages=2))
    assert len(items) == 1
    assert items[0]["id"] == "42"
