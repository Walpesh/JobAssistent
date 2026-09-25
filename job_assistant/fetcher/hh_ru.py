"""
Адаптер HH.ru — официальный API (api.hh.ru) + HTML-fallback.

Несколько стратегий поиска, чтобы не зацикливаться на одних и тех же страницах:
  - разная сортировка (дата / релевантность / зарплата)
  - разные регионы (РФ / Москва / СПб / без area)
  - период публикации (1 / 3 / 7 дней и без ограничения)
  - professional_role для IT-ролей
  - поиск только по названию вакансии
  - HTML-fallback с пагинацией
"""

from __future__ import annotations

import re
from typing import Any, Callable, Iterator, Optional
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from job_assistant.fetcher.base import NormalizedVacancy, SearchFilters, VacancySource
from job_assistant.fetcher.http_utils import request_with_retry
from job_assistant.utils.logging import add_log

HH_API = "https://api.hh.ru"
HH_SEARCH = "https://hh.ru/search/vacancy"
HH_USER_AGENT = "JobAssistant/1.2 (local career tool; contact=walpesh1@gmail.com)"
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# IT / support professional roles (HH dictionary)
_IT_ROLES = [
    96,   # Программист, разработчик
    104,  # Руководитель группы разработки
    160,  # DevOps-инженер
    165,  # Системный администратор
    148,  # Системный аналитик
    10,   # Аналитик
    150,  # Специалист технической поддержки
    121,  # Специалист по информационной безопасности
    124,  # Product manager
    113,  # Тестировщик
]


class HHRuSource(VacancySource):
    name = "hh.ru"

    def search(self, filters: SearchFilters) -> list[dict[str, Any]]:
        """
        Собрать уникальные вакансии несколькими стратегиями API.
        Если API ничего не дал — HTML-fallback.
        """
        target_count = max(
            filters.per_page * max(1, filters.max_pages),
            filters.per_page,
            20,
        )
        # Верхняя граница за один вызов search (не раздувать память)
        target_count = min(target_count, 200)

        by_id: dict[str, dict[str, Any]] = {}
        strategies = list(self._iter_strategies(filters))
        pages_per_strategy = max(1, min(filters.max_pages, 5))

        for idx, (label, extra) in enumerate(strategies, start=1):
            if len(by_id) >= target_count:
                break
            try:
                batch = self._search_api_with(
                    filters,
                    extra_params=extra,
                    max_pages=pages_per_strategy,
                    strategy_label=label,
                )
            except Exception as e:
                add_log("WARNING", f"HH.ru strategy «{label}» error: {e}")
                continue

            added = 0
            for item in batch:
                vid = str(item.get("id") or "")
                if not vid or vid in by_id:
                    continue
                by_id[vid] = item
                added += 1
                if len(by_id) >= target_count:
                    break

            add_log(
                "INFO",
                f"HH.ru strategy [{idx}/{len(strategies)}] «{label}»: "
                f"+{added} новых (всего уникальных={len(by_id)}/{target_count})",
            )

        if by_id:
            return list(by_id.values())[:target_count]

        add_log("WARNING", "HH.ru API недоступен/пуст → HTML-fallback")
        return self._search_html(filters)

    def _iter_strategies(
        self, filters: SearchFilters
    ) -> Iterator[tuple[str, dict[str, Any]]]:
        """
        Набор complementary-стратегий. Порядок: сначала «обычный» поиск,
        затем расширения (регионы, сортировка, период, роли).
        """
        # 1. Базовый: как раньше
        yield ("base/publication_time", {"order_by": "publication_time"})

        # 2. Релевантность и зарплата
        yield ("order/relevance", {"order_by": "relevance"})
        yield ("order/salary_desc", {"order_by": "salary_desc"})

        # 3. Только свежие
        yield ("period/1d", {"order_by": "publication_time", "period": 1})
        yield ("period/3d", {"order_by": "publication_time", "period": 3})
        yield ("period/7d", {"order_by": "publication_time", "period": 7})

        # 4. Регионы
        yield ("area/moscow", {"order_by": "publication_time", "area": 1})
        yield ("area/spb", {"order_by": "publication_time", "area": 2})
        yield ("area/all", {"order_by": "publication_time", "area": None})  # без area

        # 5. Поиск по названию вакансии
        yield (
            "search_field/name",
            {"order_by": "relevance", "search_field": "name"},
        )

        # 6. IT professional roles (без text или с text)
        yield (
            "roles/it",
            {
                "order_by": "publication_time",
                "professional_role": _IT_ROLES[:6],
            },
        )
        yield (
            "roles/support_devops",
            {
                "order_by": "publication_time",
                "professional_role": [160, 165, 150, 121],
            },
        )

        # 7. Гибрид / офис, если remote_only — как расширение (оркестратор
        # может ослабить remote; здесь даём альтернативу work_format)
        if filters.remote_only:
            yield (
                "work_format/hybrid",
                {"order_by": "publication_time", "work_format": "HYBRID"},
            )

        # 8. Без only_with_salary, если salary задан — отдельная ветка
        if filters.salary_min:
            yield (
                "no_salary_filter",
                {
                    "order_by": "publication_time",
                    "drop_salary": True,
                },
            )

    def _base_api_params(self, filters: SearchFilters) -> dict[str, Any]:
        params: dict[str, Any] = {
            "text": self._build_hh_query(filters.keywords),
            "per_page": min(max(1, filters.per_page), 50),
            "order_by": "publication_time",
        }
        if filters.remote_only:
            params["work_format"] = "REMOTE"
        if filters.salary_min:
            params["salary"] = filters.salary_min
            params["currency"] = filters.currency or "RUR"
            params["only_with_salary"] = "true"
        if filters.experience:
            params["experience"] = filters.experience

        if filters.location:
            loc = filters.location.lower()
            if "москв" in loc:
                params["area"] = 1
            elif "петербург" in loc or "спб" in loc:
                params["area"] = 2
            else:
                params["area"] = 113
        else:
            params["area"] = 113

        return params

    def _search_api_with(
        self,
        filters: SearchFilters,
        *,
        extra_params: dict[str, Any],
        max_pages: int,
        strategy_label: str,
    ) -> list[dict[str, Any]]:
        params = self._base_api_params(filters)

        # Применить overlay стратегии (копия, чтобы не портить исходный dict)
        extra = dict(extra_params)
        drop_salary = bool(extra.pop("drop_salary", False))
        if drop_salary:
            params.pop("salary", None)
            params.pop("only_with_salary", None)
            params.pop("currency", None)

        # area=None → убрать ограничение по региону
        if "area" in extra and extra["area"] is None:
            params.pop("area", None)
            extra.pop("area")

        # professional_role — список → несколько одноимённых ключей через requests
        roles = extra.pop("professional_role", None)

        params.update(extra)

        headers = {"User-Agent": HH_USER_AGENT, "HH-User-Agent": HH_USER_AGENT}
        items: list[dict[str, Any]] = []

        for page in range(max(1, max_pages)):
            req_params: dict[str, Any] = dict(params)
            req_params["page"] = page

            # requests поддерживает list в params → professional_role=1&professional_role=2
            if roles:
                req_params["professional_role"] = roles

            try:
                resp = request_with_retry(
                    f"{HH_API}/vacancies",
                    params=req_params,
                    headers=headers,
                    rate_limit_sec=0.35,
                    max_retries=2,
                )
                data = resp.json()
                page_items = data.get("items") or []
                items.extend(page_items)
                add_log(
                    "INFO",
                    f"HH.ru API [{strategy_label}] page {page} → {len(page_items)} "
                    f"(found={data.get('found', '?')})",
                )
                if page >= data.get("pages", 1) - 1:
                    break
                if not page_items:
                    break
            except Exception as e:
                add_log(
                    "WARNING",
                    f"HH.ru API error [{strategy_label}] (page={page}): {e}",
                )
                break

        return items

    def _search_api(self, filters: SearchFilters) -> list[dict[str, Any]]:
        """Совместимость со старыми тестами: один базовый проход."""
        return self._search_api_with(
            filters,
            extra_params={"order_by": "publication_time"},
            max_pages=max(1, filters.max_pages),
            strategy_label="compat",
        )

    @staticmethod
    def _build_hh_query(keywords: str) -> str:
        """Преобразовать список ключевых слов в OR-запрос HH."""
        raw = [x.strip() for x in re.split(r"[,;|]+", keywords or "") if x.strip()]
        if not raw:
            return "python"
        if len(raw) == 1:
            return raw[0]
        return " OR ".join(f'"{x}"' if " " in x else x for x in raw)

    def _search_html(self, filters: SearchFilters) -> list[dict[str, Any]]:
        """Fallback: страница поиска hh.ru с пагинацией и несколькими сортировками."""
        order_variants = ["publication_time", "relevance", "salary_desc"]
        items_by_id: dict[str, dict[str, Any]] = {}
        target = min(filters.per_page * max(1, filters.max_pages), 100)

        for order_by in order_variants:
            if len(items_by_id) >= target:
                break
            params: dict[str, Any] = {
                "text": filters.keywords or "python",
                "items_on_page": min(filters.per_page, 50),
                "order_by": order_by,
            }
            if filters.remote_only:
                params["schedule"] = "remote"
            if filters.salary_min:
                params["salary"] = filters.salary_min
                params["only_with_salary"] = "true"
            if filters.experience:
                params["experience"] = filters.experience

            for page in range(max(1, filters.max_pages)):
                params["page"] = page
                try:
                    resp = request_with_retry(
                        HH_SEARCH,
                        params=params,
                        headers=BROWSER_HEADERS,
                        rate_limit_sec=0.8,
                    )
                    soup = BeautifulSoup(resp.text, "html.parser")
                    cards = soup.select(
                        "[data-qa='vacancy-serp__vacancy'], "
                        "[data-qa='vacancy-serp__vacancy_response'], "
                        "[data-qa='vacancy-serp__vacancy-card'], "
                        "div.serp-item, div.vacancy-serp-item, div[data-qa*='vacancy-serp']"
                    )
                    if not cards:
                        vacancy_links = soup.select("a[href*='/vacancy/']")
                        seen_card_ids: set[str] = set()
                        cards = []
                        for link in vacancy_links:
                            m = re.search(r"/vacancy/(\d+)", link.get("href", ""))
                            if not m or m.group(1) in seen_card_ids:
                                continue
                            seen_card_ids.add(m.group(1))
                            card = link.find_parent(["div", "li", "article"])
                            if card is not None:
                                cards.append(card)

                    page_added = 0
                    for card in cards:
                        title_el = card.select_one(
                            "[data-qa='serp-item__title'], "
                            "[data-qa='vacancy-serp__vacancy-title'], "
                            "a[data-qa*='vacancy-serp__vacancy-title'], "
                            "a[href*='/vacancy/']"
                        )
                        if not title_el:
                            continue
                        title = title_el.get_text(strip=True)
                        href = title_el.get("href") or ""
                        if href.startswith("/"):
                            href = "https://hh.ru" + href
                        m = re.search(r"/vacancy/(\d+)", href)
                        vid = m.group(1) if m else href
                        if not vid or str(vid) in items_by_id:
                            continue

                        company_el = card.select_one(
                            "[data-qa='vacancy-serp__vacancy-employer'], "
                            "a[data-qa='vacancy-serp__vacancy-employer'], .company-info-text"
                        )
                        company = company_el.get_text(strip=True) if company_el else ""

                        salary_el = card.select_one(
                            "[data-qa='vacancy-serp__vacancy-compensation'], "
                            ".bloko-header-section-2, span[data-qa='vacancy-serp__vacancy-compensation']"
                        )
                        salary = salary_el.get_text(strip=True) if salary_el else ""

                        area_el = card.select_one(
                            "[data-qa='vacancy-serp__vacancy-address'], "
                            ".vacancy-serp-item__meta-info-company"
                        )
                        location = area_el.get_text(strip=True) if area_el else ""

                        items_by_id[str(vid)] = {
                            "id": str(vid),
                            "name": title,
                            "alternate_url": href,
                            "employer": {"name": company},
                            "salary_text": salary,
                            "area": {"name": location},
                            "schedule": {"id": "remote"} if filters.remote_only else {},
                            "snippet": {},
                            "_from_html": True,
                        }
                        page_added += 1
                        if len(items_by_id) >= target:
                            break

                    add_log(
                        "INFO",
                        f"HH.ru HTML [{order_by}] page {page} → "
                        f"{page_added} новых (уник={len(items_by_id)})",
                    )
                    if page_added == 0:
                        break
                except Exception as e:
                    add_log("ERROR", f"HH.ru HTML fallback error: {e}")
                    break

        return list(items_by_id.values())[:target]

    def get_details(self, url: str) -> Optional[dict[str, Any]]:
        vacancy_id = None
        if "/vacancy/" in url:
            vacancy_id = url.rstrip("/").split("/vacancy/")[-1].split("?")[0]
        elif url.isdigit():
            vacancy_id = url
        if not vacancy_id:
            return None

        try:
            resp = request_with_retry(
                f"{HH_API}/vacancies/{vacancy_id}",
                headers={"User-Agent": HH_USER_AGENT, "HH-User-Agent": HH_USER_AGENT},
                max_retries=2,
            )
            return resp.json()
        except Exception:
            pass

        try:
            resp = request_with_retry(
                f"https://hh.ru/vacancy/{vacancy_id}",
                headers=BROWSER_HEADERS,
            )
            soup = BeautifulSoup(resp.text, "html.parser")
            desc = soup.find(attrs={"data-qa": "vacancy-description"})
            text = desc.get_text("\n", strip=True) if desc else ""
            return {"description": text, "id": vacancy_id}
        except Exception as e:
            add_log("WARNING", f"HH.ru get_details({vacancy_id}): {e}")
            return None

    def normalize(self, raw: dict[str, Any]) -> Optional[NormalizedVacancy]:
        vacancy_id = str(raw.get("id") or "")
        if not vacancy_id:
            return None

        title = (raw.get("name") or "").strip()
        if not title:
            return None

        salary_str = raw.get("salary_text") or ""
        if not salary_str:
            sal = raw.get("salary") or {}
            if sal:
                parts = []
                if sal.get("from"):
                    parts.append(f"от {sal['from']}")
                if sal.get("to"):
                    parts.append(f"до {sal['to']}")
                currency = sal.get("currency") or ""
                salary_str = " ".join(parts)
                if currency:
                    salary_str = f"{salary_str} {currency}".strip()

        area = raw.get("area") or {}
        location = area.get("name") or ""

        schedule = (raw.get("schedule") or {}).get("id") or ""
        # API work_format may also indicate remote
        work_formats = raw.get("work_format") or []
        if isinstance(work_formats, list):
            remote = schedule == "remote" or any(
                (wf.get("id") if isinstance(wf, dict) else wf) == "REMOTE"
                for wf in work_formats
            )
        else:
            remote = schedule == "remote"

        snippet = raw.get("snippet") or {}
        description = " ".join(
            filter(None, [snippet.get("requirement"), snippet.get("responsibility")])
        )
        # Если API уже отдал description (get_details) — использовать
        if not description and raw.get("description"):
            description = re.sub(r"<[^>]+>", " ", str(raw["description"]))
            description = re.sub(r"\s+", " ", description).strip()

        company = ((raw.get("employer") or {}).get("name")) or ""
        url = raw.get("alternate_url") or f"https://hh.ru/vacancy/{vacancy_id}"

        return NormalizedVacancy(
            source=self.name,
            external_id=vacancy_id,
            title=title,
            url=url,
            description=description,
            company=company,
            salary=salary_str,
            location=location,
            remote=remote,
            raw=raw,
        )


    # ------------------------------------------------------------------
    # Импорт по готовой ссылке поиска hh.ru/search/vacancy?...
    # ------------------------------------------------------------------

    @staticmethod
    def parse_search_url(url: str) -> dict[str, Any]:
        """
        Разобрать URL поиска HH в параметры API + служебные поля.

        _original_url — исходная ссылка (HTML/browser fallback)
        _host — хост (novokuznetsk.hh.ru и т.п.)
        Web-only параметры (hhtm*, ored_clusters, …) в API не передаются.
        """
        from urllib.parse import urlparse, parse_qs, unquote

        raw = (url or "").strip()
        if not raw:
            raise ValueError("Пустой URL")
        if not raw.startswith("http"):
            raw = "https://" + raw.lstrip("/")

        parsed = urlparse(raw)
        host = (parsed.netloc or "").lower()
        path = (parsed.path or "").lower()

        if "hh.ru" not in host and "hh.kz" not in host and "hh.uz" not in host:
            raise ValueError("Ожидается ссылка на hh.ru (поиск вакансий)")

        if "/search/vacancy" not in path and "/vacancies" not in path:
            if "vacancy" not in path and not parsed.query:
                raise ValueError(
                    "Нужна ссылка на поиск: https://hh.ru/search/vacancy?text=..."
                )

        qs = parse_qs(parsed.query, keep_blank_values=False)

        API_KEYS = {
            "text", "search_field", "experience", "schedule", "employment",
            "area", "metro", "professional_role", "industry", "salary",
            "only_with_salary", "currency", "period", "date_from", "date_to",
            "order_by", "label", "cluster", "excluded_text", "employer_id",
            "work_format", "part_time", "accept_temporary",
        }
        multi_keys = {
            "professional_role", "employment", "schedule", "search_field",
            "area", "label",
        }

        params: dict[str, Any] = {}
        for key, values in qs.items():
            if key not in API_KEYS or not values:
                continue
            if key in multi_keys and len(values) > 1:
                params[key] = [unquote(v) for v in values]
            else:
                params[key] = unquote(values[0])

        schedule = params.get("schedule")
        if schedule:
            sched_list = schedule if isinstance(schedule, list) else [schedule]
            if any(str(s).lower() == "remote" for s in sched_list):
                params["work_format"] = "REMOTE"

        if "items_on_page" in qs and "per_page" not in params:
            try:
                params["per_page"] = min(
                    100, max(1, int(unquote(qs["items_on_page"][0])))
                )
            except (TypeError, ValueError, IndexError):
                pass

        if "per_page" not in params:
            params["per_page"] = 50
        else:
            try:
                params["per_page"] = min(100, max(1, int(params["per_page"])))
            except (TypeError, ValueError):
                params["per_page"] = 50

        params.pop("page", None)
        if "order_by" not in params:
            params["order_by"] = "publication_time"

        params["_original_url"] = raw
        params["_host"] = host
        return params

    @staticmethod
    def _api_params_only(params: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in params.items() if not str(k).startswith("_")}

    def fetch_search_url_page(
        self,
        api_params: dict[str, Any],
        page: int,
    ) -> tuple[list[dict[str, Any]], int, int]:
        headers = {
            "User-Agent": HH_USER_AGENT,
            "HH-User-Agent": HH_USER_AGENT,
            "Accept": "application/json",
        }
        req_params: dict[str, Any] = dict(self._api_params_only(api_params))
        req_params["page"] = max(0, int(page))
        per_page = int(req_params.get("per_page") or 50)
        req_params["per_page"] = min(100, max(1, per_page))

        resp = request_with_retry(
            f"{HH_API}/vacancies",
            params=req_params,
            headers=headers,
            timeout=25,
            max_retries=3,
        )
        data = resp.json()
        items = data.get("items") or []
        found = int(data.get("found") or 0)
        pages = int(data.get("pages") or 1)
        return items, found, pages

    def _html_cards_from_soup(self, soup: Any, base_host: str) -> list[dict[str, Any]]:
        cards = soup.select('[data-qa="vacancy-serp__vacancy"]')
        items: list[dict[str, Any]] = []
        seen: set[str] = set()

        def _add(vid: str, title: str, href: str, company: str = "", snip: str = "") -> None:
            if not vid or vid in seen:
                return
            seen.add(vid)
            if href and href.startswith("/"):
                href = f"https://{base_host}{href}"
            items.append(
                {
                    "id": vid,
                    "name": title or f"Vacancy {vid}",
                    "alternate_url": href or f"https://{base_host}/vacancy/{vid}",
                    "employer": {"name": company} if company else {},
                    "snippet": {"requirement": snip or ""},
                    "_from_html": True,
                }
            )

        if cards:
            for card in cards:
                a = (
                    card.select_one('a[data-qa="serp-item__title"]')
                    or card.select_one('a[href*="/vacancy/"]')
                )
                if not a:
                    continue
                href = a.get("href") or ""
                m = re.search(r"/vacancy/(\d+)", href)
                if not m:
                    continue
                company_el = card.select_one('[data-qa="vacancy-serp__vacancy-employer"]')
                salary_el = card.select_one(
                    '[data-qa="vacancy-serp__vacancy-compensation"]'
                )
                _add(
                    m.group(1),
                    a.get_text(" ", strip=True),
                    href.split("?")[0],
                    company_el.get_text(" ", strip=True) if company_el else "",
                    salary_el.get_text(" ", strip=True) if salary_el else "",
                )
        else:
            for a in soup.select('a[href*="/vacancy/"]'):
                href = a.get("href") or ""
                m = re.search(r"/vacancy/(\d+)", href)
                if not m:
                    continue
                title = a.get_text(" ", strip=True)
                if not title or len(title) < 3:
                    continue
                _add(m.group(1), title, href.split("?")[0])

        return items

    def _parse_found_count(self, soup: Any, html: str) -> int:
        for sel in ('[data-qa="vacancies-total-found"]', "h1[data-qa]", "h1"):
            el = soup.select_one(sel)
            if not el:
                continue
            text = el.get_text(" ", strip=True)
            m = re.search(r"(\d[\d\s\xa0]*)\s*вакан", text, re.I)
            if m:
                try:
                    return int(
                        m.group(1).replace(" ", "").replace("\xa0", "").replace("\u202f", "")
                    )
                except ValueError:
                    pass
        m = re.search(r"Найдено\s+(\d[\d\s\xa0]*)\s*вакан", html or "", re.I)
        if m:
            try:
                return int(
                    m.group(1).replace(" ", "").replace("\xa0", "").replace("\u202f", "")
                )
            except ValueError:
                pass
        return 0

    def fetch_search_url_html_all(
        self,
        original_url: str,
        *,
        should_stop: Optional[Callable[[], bool]] = None,
        on_progress: Optional[Callable[[dict[str, Any]], None]] = None,
        max_pages_hard: int = 40,
        human_delay: bool = True,
    ) -> list[dict[str, Any]]:
        """HTML-обход страниц поиска по исходной ссылке (обход 403 API)."""
        from urllib.parse import urlparse, urlencode, parse_qs, urlunparse
        import time
        import random
        from bs4 import BeautifulSoup

        raw = (original_url or "").strip()
        if not raw.startswith("http"):
            raw = "https://" + raw.lstrip("/")
        parsed = urlparse(raw)
        host = parsed.netloc or "hh.ru"
        base_qs = parse_qs(parsed.query, keep_blank_values=False)

        all_items: list[dict[str, Any]] = []
        seen: set[str] = set()
        found_total = 0
        pages_guess = max_pages_hard

        for page in range(max_pages_hard):
            if should_stop and should_stop():
                add_log("WARNING", f"HH HTML URL-import: stop at page={page}")
                break

            qs = {k: list(v) for k, v in base_qs.items()}
            qs["page"] = [str(page)]
            if "items_on_page" not in qs:
                qs["items_on_page"] = ["50"]
            # doseq for multi search_field
            pairs = []
            for k, vals in qs.items():
                for v in vals:
                    pairs.append((k, v))
            query = urlencode(pairs)
            page_url = urlunparse(
                (parsed.scheme or "https", host, parsed.path, "", query, "")
            )

            try:
                if human_delay and page > 0:
                    time.sleep(random.uniform(0.9, 2.4))
                resp = request_with_retry(
                    page_url,
                    headers={
                        "User-Agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/122.0.0.0 Safari/537.36"
                        ),
                        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8",
                        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
                        "Referer": f"https://{host}/",
                    },
                    timeout=30,
                    max_retries=3,
                )
                html = resp.text
                soup = BeautifulSoup(html, "lxml")
            except Exception as e:
                add_log("ERROR", f"HH HTML page={page}: {e}")
                if on_progress:
                    on_progress(
                        {
                            "status": "error",
                            "page": page,
                            "pages": pages_guess,
                            "found": found_total,
                            "fetched": len(all_items),
                            "error": str(e),
                        }
                    )
                break

            if page == 0:
                found_total = self._parse_found_count(soup, html)
                if found_total > 0:
                    pages_guess = max(
                        1, min(max_pages_hard, (found_total + 49) // 50)
                    )

            batch = self._html_cards_from_soup(soup, host)
            new_on_page = 0
            for it in batch:
                vid = str(it.get("id") or "")
                if not vid or vid in seen:
                    continue
                seen.add(vid)
                all_items.append(it)
                new_on_page += 1

            if on_progress:
                on_progress(
                    {
                        "status": "running",
                        "page": page,
                        "pages": pages_guess,
                        "found": found_total or len(all_items),
                        "fetched": len(all_items),
                        "page_items": len(batch),
                        "page_new": new_on_page,
                        "mode": "html",
                    }
                )
            add_log(
                "INFO",
                f"HH HTML URL-import page {page + 1}: +{new_on_page} "
                f"(всего {len(all_items)}, found≈{found_total})",
            )

            if new_on_page == 0:
                break
            if found_total and len(all_items) >= found_total:
                break
            if page + 1 >= pages_guess:
                break

        return all_items

    def fetch_all_from_search_url(
        self,
        url: str,
        *,
        should_stop: Optional[Callable[[], bool]] = None,
        on_progress: Optional[Callable[[dict[str, Any]], None]] = None,
        max_pages_hard: int = 40,
        browser_fallback: bool = True,
    ) -> list[NormalizedVacancy]:
        """
        Все вакансии по URL: API → HTML → browser emulation.
        """
        api_params = self.parse_search_url(url)
        original = str(api_params.get("_original_url") or url)
        max_pages_hard = max(1, min(int(max_pages_hard), 40))

        all_items: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        found_total = 0
        mode = "api"

        page = 0
        while page < max_pages_hard:
            if should_stop and should_stop():
                break
            try:
                items, found_total, pages_total = self.fetch_search_url_page(
                    api_params, page
                )
            except Exception as e:
                add_log("WARNING", f"HH API URL-import недоступен (page={page}): {e}")
                break

            pages_total = max(1, min(int(pages_total or 1), max_pages_hard))
            new_on_page = 0
            for it in items:
                vid = str(it.get("id") or "")
                if not vid or vid in seen_ids:
                    continue
                seen_ids.add(vid)
                all_items.append(it)
                new_on_page += 1

            if on_progress:
                on_progress(
                    {
                        "status": "running",
                        "page": page,
                        "pages": pages_total,
                        "found": found_total,
                        "fetched": len(all_items),
                        "page_new": new_on_page,
                        "mode": "api",
                    }
                )
            add_log(
                "INFO",
                f"HH API URL-import page {page + 1}/{pages_total}: +{new_on_page} "
                f"(всего {len(all_items)})",
            )
            if page >= pages_total - 1 or not items:
                break
            page += 1

        if not all_items:
            mode = "html"
            add_log("INFO", "HH URL-import: API пуст/403 → HTML по исходной ссылке")
            for it in self.fetch_search_url_html_all(
                original,
                should_stop=should_stop,
                on_progress=on_progress,
                max_pages_hard=max_pages_hard,
            ):
                vid = str(it.get("id") or "")
                if not vid or vid in seen_ids:
                    continue
                seen_ids.add(vid)
                all_items.append(it)
            found_total = max(found_total, len(all_items))

        if not all_items and browser_fallback:
            mode = "browser"
            add_log("INFO", "HH URL-import: HTML пуст → browser emulation")
            try:
                from job_assistant.fetcher.browser_hh import fetch_search_via_browser

                for it in fetch_search_via_browser(
                    original,
                    should_stop=should_stop,
                    on_progress=on_progress,
                    max_pages=max_pages_hard,
                ):
                    vid = str(it.get("id") or "")
                    if not vid or vid in seen_ids:
                        continue
                    seen_ids.add(vid)
                    all_items.append(it)
            except Exception as e:
                add_log("ERROR", f"HH browser fallback failed: {e}")

        vacancies: list[NormalizedVacancy] = []
        for raw in all_items:
            try:
                if raw.get("_from_html") or raw.get("_from_browser"):
                    nv = NormalizedVacancy(
                        source=self.name,
                        external_id=str(raw.get("id") or ""),
                        title=str(raw.get("name") or ""),
                        url=str(raw.get("alternate_url") or ""),
                        description=str(
                            (raw.get("snippet") or {}).get("requirement")
                            or raw.get("description")
                            or ""
                        ),
                        company=str((raw.get("employer") or {}).get("name") or ""),
                        salary="",
                        location="",
                        remote=False,
                        raw=raw,
                    )
                    if nv.external_id and nv.title:
                        vacancies.append(nv)
                else:
                    nv = self.normalize(raw)
                    if nv:
                        vacancies.append(nv)
            except Exception as e:
                add_log("WARNING", f"HH URL-import normalize: {e}")

        add_log(
            "SUCCESS",
            f"HH URL-import [{mode}]: нормализовано {len(vacancies)} "
            f"(сырых {len(all_items)}, found≈{found_total})",
        )
        return vacancies


def build_apply_url(source: str, url: str, external_id: str = "") -> str:
    """
    Ссылка для отклика.
    HH: страница вакансии (кнопка «Откликнуться» на сайте) или popup response.
    Остальные источники — исходный url.
    """
    src = (source or "").lower()
    if "hh" in src:
        vid = (external_id or "").strip()
        if not vid and url:
            m = re.search(r"/vacancy/(\d+)", url)
            if m:
                vid = m.group(1)
        if vid:
            # Прямая форма отклика на HH (откроется login/response popup)
            return f"https://hh.ru/applicant/vacancy_response?vacancyId={vid}"
    return url or ""
