"""
Адаптер We Work Remotely — RSS-лента (стабильный канал без ключа).
Категория programming по умолчанию.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any, Optional

from job_assistant.fetcher.base import NormalizedVacancy, SearchFilters, VacancySource
from job_assistant.fetcher.http_utils import request_with_retry
from job_assistant.utils.logging import add_log

WWR_RSS = "https://weworkremotely.com/categories/remote-programming-jobs.rss"


class WeWorkRemotelySource(VacancySource):
    name = "weworkremotely"

    def search(self, filters: SearchFilters) -> list[dict[str, Any]]:
        try:
            resp = request_with_retry(
                WWR_RSS,
                headers={"Accept": "application/rss+xml, application/xml, text/xml"},
                rate_limit_sec=0.6,
            )
            root = ET.fromstring(resp.content)
            channel = root.find("channel")
            if channel is None:
                add_log("WARNING", "WWR: RSS без channel")
                return []

            items: list[dict[str, Any]] = []
            keywords = (filters.keywords or "").lower().strip()
            tokens = [t for t in re.split(r"[\s,+/]+", keywords) if t] if keywords else []

            for item in channel.findall("item"):
                title = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip()
                description = (item.findtext("description") or "").strip()
                guid = (item.findtext("guid") or link or "").strip()
                pub_date = (item.findtext("pubDate") or "").strip()

                if tokens:
                    hay = f"{title} {description}".lower()
                    if not any(t in hay for t in tokens):
                        continue

                items.append(
                    {
                        "title": title,
                        "link": link,
                        "description": description,
                        "guid": guid,
                        "pubDate": pub_date,
                    }
                )

            limit = filters.per_page * filters.max_pages
            add_log("INFO", f"WeWorkRemotely: {len(items)} вакансий после фильтра")
            return items[:limit]

        except Exception as e:
            add_log("ERROR", f"WeWorkRemotely search error: {e}")
            return []

    def get_details(self, url: str) -> Optional[dict[str, Any]]:
        return None

    def normalize(self, raw: dict[str, Any]) -> Optional[NormalizedVacancy]:
        title = (raw.get("title") or "").strip()
        url = (raw.get("link") or "").strip()
        if not title or not url:
            return None

        # WWR title часто: "Company: Position"
        company = ""
        clean_title = title
        if ":" in title:
            parts = title.split(":", 1)
            company = parts[0].strip()
            clean_title = parts[1].strip() or title

        guid = raw.get("guid") or url
        # external_id из хвоста URL
        external_id = guid.rstrip("/").split("/")[-1] or guid

        description = raw.get("description") or ""
        if "<" in description:
            description = re.sub(r"<[^>]+>", " ", description)
            description = re.sub(r"\s+", " ", description).strip()

        return NormalizedVacancy(
            source=self.name,
            external_id=str(external_id),
            title=clean_title,
            url=url,
            description=description[:8000],
            company=company,
            salary="",
            location="Remote",
            remote=True,
            raw=raw,
        )
