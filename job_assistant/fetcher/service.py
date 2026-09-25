"""
Оркестратор автопоиска: источники → normalize → save_vacancy (status=new).

Важно: target_total — это цель по УНИКАЛЬНЫМ сохранённым вакансиям за один
прогон. Оркестратор продолжает поиск в цикле, пока не наберёт target_total
уникальных вакансий (или пока не исчерпает все разумные стратегии поиска).

Стратегии расширения (по порядку):
  1. Увеличение max_pages (глубина пагинации)
  2. Поиск по каждому ключевому слову отдельно
  3. Ослабление фильтров: salary → experience → remote_only
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field, replace
from typing import Optional

from job_assistant.db import is_vacancy_exists, save_vacancy
from job_assistant.utils.employment import (
    detect_employment_types,
    employment_to_str,
    is_tk_only,
    EMP_TK,
)
from job_assistant.fetcher.base import NormalizedVacancy, SearchFilters, VacancySource
from job_assistant.fetcher.hh_ru import HHRuSource
from job_assistant.fetcher.remote_ok import RemoteOKSource
from job_assistant.fetcher.weworkremotely import WeWorkRemotelySource
from job_assistant.utils.logging import add_log


@dataclass
class FetchReport:
    """Итог автопоиска."""

    total_found: int = 0
    saved: int = 0
    duplicates: int = 0
    errors: list[str] = field(default_factory=list)
    by_source: dict[str, int] = field(default_factory=dict)
    saved_ids: list[int] = field(default_factory=list)
    rounds: int = 0
    target_reached: bool = False
    exhausted: bool = False


def get_default_sources() -> list[VacancySource]:
    """Стабильные источники по умолчанию."""
    return [HHRuSource(), RemoteOKSource(), WeWorkRemotelySource()]


def _enrich_description(source: VacancySource, item: NormalizedVacancy) -> NormalizedVacancy:
    """Если description короткий — пробуем get_details (HH)."""
    if item.description and len(item.description) > 400:
        return item
    try:
        details = source.get_details(item.url)
        if not details:
            return item
        desc = details.get("description") or ""
        if desc:
            text = re.sub(r"<[^>]+>", "\n", desc)
            text = re.sub(r"\n{3,}", "\n\n", text).strip()
            item.description = text[:10000]
    except Exception as e:
        add_log("WARNING", f"enrich description failed for {item.url}: {e}")
    return item


def _candidate_key(item: NormalizedVacancy) -> tuple[str, str, str]:
    """Стабильный ключ кандидата для дедупликации внутри одного прогона."""
    return (
        (item.source or "").strip().lower(),
        (item.external_id or "").strip(),
        (item.url or "").strip().rstrip("/"),
    )


def _split_keywords(keywords: str) -> list[str]:
    """Разбить строку ключевых слов на отдельные термины."""
    parts = [x.strip() for x in re.split(r"[,;|]+", keywords or "") if x.strip()]
    # Убрать слишком короткие / пустые
    return [p for p in parts if len(p) >= 2]


def _build_search_plan(filters: SearchFilters, target: int) -> list[SearchFilters]:
    """
    Построить план поисковых проходов, которые будут выполняться подряд,
    пока не наберём target уникальных сохранений.

    Каждый элемент плана — набор фильтров для одного «раунда».
    Глубина пагинации растёт; затем перебираем отдельные ключевые слова;
    затем ослабляем жёсткие фильтры.
    """
    base_pages = max(1, int(filters.max_pages or 1))
    base_per_page = max(1, int(filters.per_page or 10))
    keywords = (filters.keywords or "").strip()
    terms = _split_keywords(keywords)

    plan: list[SearchFilters] = []

    # --- Фаза 1: наращивание глубины по исходным keywords ---
    # Сколько страниц нужно примерно: target / per_page, с запасом x2.
    needed_pages = max(base_pages, int(math.ceil(target / base_per_page)) + 1)
    # Не больше 20 страниц на проход (HH API лимит ~2000, но бережём rate-limit)
    max_depth = min(20, max(needed_pages, base_pages * 5))

    for depth in range(base_pages, max_depth + 1):
        plan.append(replace(filters, max_pages=depth, target_total=None))

    # --- Фаза 2: каждое ключевое слово отдельно (если их > 1) ---
    if len(terms) > 1:
        for term in terms:
            for depth in (base_pages, base_pages * 2, base_pages * 3, max(5, base_pages * 4)):
                if depth > max_depth:
                    continue
                plan.append(
                    replace(
                        filters,
                        keywords=term,
                        max_pages=depth,
                        target_total=None,
                    )
                )

    # --- Фаза 3: ослабление фильтров ---
    relax_steps: list[dict] = []
    if filters.salary_min:
        relax_steps.append({"salary_min": None})
    if filters.experience:
        relax_steps.append({"experience": None})
    if filters.remote_only:
        relax_steps.append({"remote_only": False})
    # Комбинации: сначала по одному, потом все сразу
    if filters.salary_min and filters.experience:
        relax_steps.append({"salary_min": None, "experience": None})
    if filters.salary_min or filters.experience or filters.remote_only:
        relax_steps.append({
            "salary_min": None,
            "experience": None,
            "remote_only": False,
        })

    for relax in relax_steps:
        # С полным набором keywords
        for depth in (base_pages * 2, max(5, base_pages * 4)):
            plan.append(
                replace(
                    filters,
                    max_pages=min(depth, max_depth),
                    target_total=None,
                    **relax,
                )
            )
        # И по отдельным терминам
        if len(terms) > 1:
            for term in terms:
                plan.append(
                    replace(
                        filters,
                        keywords=term,
                        max_pages=min(base_pages * 3, max_depth),
                        target_total=None,
                        **relax,
                    )
                )

    # Дедуп одинаковых фильтров в плане (сохраняем порядок)
    seen: set[tuple] = set()
    unique_plan: list[SearchFilters] = []
    for f in plan:
        key = (
            f.keywords,
            f.remote_only,
            f.salary_min,
            f.experience,
            f.max_pages,
            f.per_page,
        )
        if key not in seen:
            seen.add(key)
            unique_plan.append(f)

    return unique_plan


def run_autofetch(
    filters: SearchFilters,
    sources: Optional[list[VacancySource]] = None,
    enrich: bool = True,
    on_event=None,
) -> FetchReport:
    """
    Запустить автопоиск и сохранить target_total УНИКАЛЬНЫХ вакансий.

    Алгоритм:
      1. Строим план проходов (глубина → отдельные keywords → ослабление фильтров).
      2. На каждом проходе обходим все источники.
      3. Останавливаемся ТОЛЬКО когда saved >= target_total,
         либо когда весь план пройден без новых кандидатов.
    """
    sources = sources or get_default_sources()
    report = FetchReport()

    def _emit(event: dict) -> None:
        if on_event:
            try:
                on_event(event)
            except Exception:
                pass

    target = filters.target_total
    base_per_page = max(1, int(filters.per_page or 1))

    add_log(
        "INFO",
        f"Автопоиск старт: keywords=«{filters.keywords}», remote={filters.remote_only}, "
        f"target={target}, sources={[s.name for s in sources]}",
    )
    _emit({
        "type": "start",
        "keywords": filters.keywords,
        "sources": [s.name for s in sources],
        "target": target,
    })

    # Кандидат, однажды увиденный в этом прогоне, больше не обрабатываем.
    seen_candidates: set[tuple[str, str, str]] = set()

    # Без target — один проход (старое поведение).
    if target is None:
        search_plan = [replace(filters, target_total=None)]
    else:
        search_plan = _build_search_plan(filters, target)

    consecutive_empty_rounds = 0
    # Сколько пустых раундов подряд допускаем, прежде чем сдаться
    # (после смены стратегии счётчик сбрасывается логикой ниже)
    max_empty_streak = 3

    for round_no, round_filters in enumerate(search_plan, start=1):
        if target is not None and report.saved >= target:
            report.target_reached = True
            break

        report.rounds = round_no
        round_new_candidates = 0
        pages = max(1, int(round_filters.max_pages or 1))

        strategy_hint = ""
        if round_filters.keywords != filters.keywords:
            strategy_hint = f", keywords=«{round_filters.keywords}»"
        if round_filters.salary_min != filters.salary_min:
            strategy_hint += ", без salary"
        if round_filters.experience != filters.experience:
            strategy_hint += ", без experience"
        if round_filters.remote_only != filters.remote_only:
            strategy_hint += ", remote=any"

        add_log(
            "INFO",
            f"Поисковый проход {round_no}/{len(search_plan)}: "
            f"saved={report.saved}/{target or '∞'}, max_pages={pages}{strategy_hint}",
        )
        _emit({
            "type": "round_start",
            "round": round_no,
            "target": target,
            "saved_total": report.saved,
            "max_pages": pages,
            "keywords": round_filters.keywords,
        })

        for source in sources:
            if target is not None and report.saved >= target:
                report.target_reached = True
                break

            source_filters = replace(
                round_filters,
                # Не просим источник искусственно ограничиваться target_total:
                # его задача — вернуть кандидатов, а дедупликацией занимается оркестратор.
                target_total=None,
            )

            try:
                add_log(
                    "INFO",
                    f"Источник {source.name}: запрос "
                    f"(round={round_no}, max_pages={pages}, kw=«{source_filters.keywords[:60]}»)…",
                )
                _emit({
                    "type": "source_start",
                    "source": source.name,
                    "round": round_no,
                })

                found = source.fetch(source_filters)
                report.total_found += len(found)
                report.by_source.setdefault(source.name, 0)

                add_log(
                    "INFO",
                    f"Источник {source.name}: получено {len(found)} кандидатов "
                    f"(round={round_no})",
                )
                _emit({
                    "type": "source_raw",
                    "source": source.name,
                    "count": len(found),
                    "round": round_no,
                })

                source_new = 0
                for item in found:
                    if target is not None and report.saved >= target:
                        report.target_reached = True
                        break

                    key = _candidate_key(item)
                    if key in seen_candidates:
                        # Повторная выдача той же вакансии — пропускаем молча
                        continue
                    seen_candidates.add(key)
                    round_new_candidates += 1
                    source_new += 1

                    try:
                        if is_vacancy_exists(
                            source=item.source,
                            external_id=item.external_id,
                            url=item.url,
                        ):
                            report.duplicates += 1
                            _emit({
                                "type": "duplicate",
                                "source": source.name,
                                "title": item.title,
                                "round": round_no,
                            })
                            continue

                        if enrich:
                            item = _enrich_description(source, item)

                        emp_types = detect_employment_types(
                            item.title or "",
                            item.description or "",
                            item.company or "",
                        )
                        emp_str = employment_to_str(emp_types)

                        # Фильтр по выбранным типам устройства (если задан)
                        preferred = getattr(filters, "employment_types", None) or None
                        if preferred:
                            preferred_set = set(preferred)
                            # unknown всегда пропускаем дальше (неизвестно)
                            if "unknown" not in emp_types:
                                # если вакансия только ТК, а ТК не выбран — skip
                                if is_tk_only(emp_types) and EMP_TK not in preferred_set:
                                    _emit({
                                        "type": "filtered_employment",
                                        "source": source.name,
                                        "title": item.title,
                                        "employment": emp_str,
                                        "round": round_no,
                                    })
                                    continue
                                # если ни один из типов вакансии не пересекается с preferred
                                if not (set(emp_types) & preferred_set):
                                    _emit({
                                        "type": "filtered_employment",
                                        "source": source.name,
                                        "title": item.title,
                                        "employment": emp_str,
                                        "round": round_no,
                                    })
                                    continue

                        vid = save_vacancy(
                            source=item.source,
                            external_id=item.external_id,
                            title=item.title,
                            url=item.url,
                            description=item.description,
                            company=item.company,
                            salary=item.salary,
                            location=item.location,
                            remote=item.remote,
                            status="new",
                            employment_type=emp_str,
                        )
                        if vid is not None:
                            report.saved += 1
                            report.saved_ids.append(vid)
                            report.by_source[source.name] = (
                                report.by_source.get(source.name, 0) + 1
                            )
                            add_log(
                                "SUCCESS",
                                f"+ сохранена [{source.name}] «{item.title[:50]}» id={vid} "
                                f"({report.saved}/{target or '∞'})",
                            )
                            _emit({
                                "type": "saved",
                                "source": source.name,
                                "title": item.title,
                                "id": vid,
                                "company": item.company,
                                "url": item.url,
                                "saved_total": report.saved,
                                "target": target,
                                "round": round_no,
                            })

                            if target is not None and report.saved >= target:
                                report.target_reached = True
                                break
                        else:
                            # Гонка/дополнительная DB-проверка обнаружила дубль.
                            report.duplicates += 1
                            _emit({
                                "type": "duplicate",
                                "source": source.name,
                                "title": item.title,
                                "round": round_no,
                            })

                    except Exception as e:
                        msg = (
                            f"{source.name}: ошибка сохранения "
                            f"«{item.title[:40]}»: {e}"
                        )
                        report.errors.append(msg)
                        add_log("ERROR", msg)
                        _emit({"type": "error", "message": msg, "round": round_no})

                _emit({
                    "type": "source_done",
                    "source": source.name,
                    "saved": report.by_source.get(source.name, 0),
                    "round": round_no,
                    "new_candidates": source_new,
                })

            except Exception as e:
                msg = f"{source.name}: критическая ошибка источника: {e}"
                report.errors.append(msg)
                add_log("ERROR", msg)
                _emit({"type": "error", "message": msg, "round": round_no})

        if report.target_reached:
            break

        if round_new_candidates == 0:
            consecutive_empty_rounds += 1
            add_log(
                "INFO",
                f"Проход {round_no}: новых кандидатов нет "
                f"(пусто подряд: {consecutive_empty_rounds})",
            )
            # Если несколько проходов подряд без новизны — можно ускорить
            # выход, но не раньше чем пройдём хотя бы несколько стратегий.
            if consecutive_empty_rounds >= max_empty_streak and round_no >= 5:
                # Ещё даём шанс оставшимся стратегиям ослабления фильтров,
                # но если и они пустые — выходим после max_empty_streak * 2
                if consecutive_empty_rounds >= max_empty_streak * 2:
                    report.exhausted = True
                    add_log(
                        "WARNING",
                        f"Источники исчерпаны после {round_no} проходов: "
                        f"новых кандидатов больше нет; сохранено={report.saved}/{target}",
                    )
                    _emit({
                        "type": "exhausted",
                        "saved_total": report.saved,
                        "target": target,
                    })
                    break
        else:
            consecutive_empty_rounds = 0

    if target is not None and report.saved >= target:
        report.target_reached = True

    if target is not None and not report.target_reached and report.saved < target:
        report.exhausted = True

    status = "достигнута цель" if report.target_reached else "источники исчерпаны"
    add_log(
        "SUCCESS",
        f"Автопоиск завершён: цель={target or '∞'}, статус={status}, "
        f"найдено-кандидатов={report.total_found}, сохранено={report.saved}, "
        f"дублей={report.duplicates}, ошибок={len(report.errors)}, "
        f"проходов={report.rounds}",
    )
    return report
