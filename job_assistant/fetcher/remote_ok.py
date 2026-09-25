"""
Адаптер RemoteOK — публичный JSON API (https://remoteok.com/api).
Без ключа. Первый элемент ответа — metadata, его пропускаем.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from job_assistant.fetcher.base import NormalizedVacancy, SearchFilters, VacancySource
from job_assistant.fetcher.http_utils import request_with_retry
from job_assistant.utils.logging import add_log

REMOTEOK_API = "https://remoteok.com/api"


class RemoteOKSource(VacancySource):
    name = "remoteok"

    def search(self, filters: SearchFilters) -> list[dict[str, Any]]:
        # RemoteOK: ?tag=python или несколько тегов
        params: dict[str, str] = {}
        keywords = (filters.keywords or "").strip().lower()
        # Не ограничиваем API первым ключевым словом. При вводе
        # "Python, C#, Unity, ..." старый код делал tag=python, а затем
        # клиентский фильтр уже никогда не мог увидеть C#/Unity-вакансии.
        # Если введено ровно одно простое слово, tag полезен; при нескольких
        # терминах получаем общий список и фильтруем его локально.
        keyword_parts = [t for t in re.split(r"[,;|]+", keywords) if t.strip()]
        if len(keyword_parts) == 1 and re.fullmatch(r"[a-z0-9_-]+", keyword_parts[0].strip()):
            params["tag"] = keyword_parts[0].strip()

        try:
            resp = request_with_retry(
                REMOTEOK_API,
                params=params or None,
                headers={"Accept": "application/json"},
                rate_limit_sec=0.5,
            )
            data = resp.json()
            if not isinstance(data, list):
                add_log("WARNING", "RemoteOK: неожиданный формат ответа")
                return []

            # Первый элемент — служебный объект с ключом "last_updated"
            jobs = [item for item in data if item.get("id") and item.get("position")]
            add_log("INFO", f"RemoteOK: получено {len(jobs)} вакансий (tag={params.get('tag', '—')})")

            # Клиентская фильтрация по keywords / salary
            if keywords:
                tokens = [t for t in re.split(r"[\s,+/]+", keywords) if t]
                jobs = [
                    j
                    for j in jobs
                    if any(
                        t in (j.get("position") or "").lower()
                        or t in " ".join(j.get("tags") or []).lower()
                        or t in (j.get("description") or "").lower()
                        for t in tokens
                    )
                ]

            if filters.salary_min:
                filtered = []
                for j in jobs:
                    smin = j.get("salary_min")
                    # У RemoteOK зарплата часто отсутствует — не отсекаем такие записи.
                    if smin is None or smin == "":
                        filtered.append(j)
                        continue
                    try:
                        if int(smin) >= int(filters.salary_min):
                            filtered.append(j)
                    except (TypeError, ValueError):
                        filtered.append(j)
                jobs = filtered

            return jobs[: filters.per_page * filters.max_pages]

        except Exception as e:
            add_log("ERROR", f"RemoteOK search error: {e}")
            return []

    def get_details(self, url: str) -> Optional[dict[str, Any]]:
        # RemoteOK отдаёт description уже в списке
        return None

    def normalize(self, raw: dict[str, Any]) -> Optional[NormalizedVacancy]:
        external_id = str(raw.get("id") or "")
        title = (raw.get("position") or "").strip()
        if not external_id or not title:
            return None

        company = (raw.get("company") or "").strip()
        location = (raw.get("location") or "Worldwide").strip()

        salary_str = ""
        smin, smax = raw.get("salary_min"), raw.get("salary_max")
        if smin or smax:
            parts = []
            if smin:
                parts.append(f"from ${smin}")
            if smax:
                parts.append(f"to ${smax}")
            salary_str = " ".join(parts)

        # description может быть HTML
        description = raw.get("description") or ""
        if isinstance(description, str) and "<" in description:
            description = re.sub(r"<[^>]+>", " ", description)
            description = re.sub(r"\s+", " ", description).strip()

        url = raw.get("url") or raw.get("apply_url") or ""
        if url and not url.startswith("http"):
            url = f"https://remoteok.com{url}"
        if not url and external_id:
            slug = raw.get("slug") or external_id
            url = f"https://remoteok.com/remote-jobs/{slug}"

        return NormalizedVacancy(
            source=self.name,
            external_id=external_id,
            title=title,
            url=url,
            description=description[:8000],
            company=company,
            salary=salary_str,
            location=location,
            remote=True,  # RemoteOK = только remote
            raw=raw,
        )
