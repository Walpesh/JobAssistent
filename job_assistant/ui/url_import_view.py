"""
Вкладка: импорт вакансий по ссылке поиска HH.ru.
Сохранение в ту же БД; очереди анализа/писем — общие.
"""

from __future__ import annotations

from typing import Optional

import streamlit as st

from job_assistant.fetcher import run_hh_search_url_import
from job_assistant.fetcher.hh_ru import HHRuSource
from job_assistant.ui.pipeline_view import (
    _render_results_table,
    _render_runs_history,
    render_shared_queue_actions,
)
from job_assistant.utils.logging import add_log, get_session_log_path
from job_assistant.ui.progress_popup import (
    ProgressSlot,
    apply_progress_event,
    progress_finish,
    progress_start,
    progress_update,
)


def render_url_import_view(user_profile: Optional[str]) -> None:
    st.markdown("### 🔗 База вакансий по ссылке")
    st.caption(
        "Вставьте URL поиска hh.ru (с фильтрами). "
        "Программа обойдёт все страницы результатов и сохранит вакансии в общую базу. "
        "Дальше — те же очереди оценки и писем, без повторного поиска."
    )

    default_url = st.session_state.get("hh_search_url_input", "")
    url = st.text_input(
        "Ссылка на поиск HH.ru",
        value=default_url,
        placeholder="https://hh.ru/search/vacancy?text=python&area=1&...",
        key="hh_search_url_field",
    )

    col_a, col_b = st.columns(2)
    with col_a:
        start = st.button(
            "📥 Импортировать все вакансии из поиска",
            type="primary",
            use_container_width=True,
        )
    with col_b:
        stop = st.button(
            "⏹ Остановить импорт",
            use_container_width=True,
            help="Сработает между страницами текущего импорта (флаг остановки).",
        )

    if stop:
        st.session_state["hh_url_import_stop"] = True
        st.warning("Запрошена остановка. Текущая страница завершится, затем импорт прервётся.")

    if start:
        st.session_state["hh_url_import_stop"] = False
        url_clean = (url or "").strip()
        if not url_clean:
            st.error("Вставьте ссылку на поиск.")
        else:
            try:
                params_preview = HHRuSource.parse_search_url(url_clean)
                st.caption(
                    "Параметры API: "
                    + ", ".join(f"{k}={params_preview[k]}" for k in list(params_preview)[:10])
                    + ("…" if len(params_preview) > 10 else "")
                )
            except ValueError as e:
                st.error(str(e))
                params_preview = None

            if params_preview is not None:
                progress = st.progress(0.0, text="Старт импорта…")
                status = st.empty()
                live = st.empty()
                lines: list[str] = []

                def should_stop() -> bool:
                    return bool(st.session_state.get("hh_url_import_stop"))

                def on_event(ev: dict) -> None:
                    et = ev.get("type")
                    if et == "page":
                        pages = max(1, int(ev.get("pages") or 1))
                        page = int(ev.get("page") or 0)
                        found = ev.get("found") or 0
                        fetched = ev.get("fetched") or 0
                        frac = min(0.85, (page + 1) / pages)
                        progress.progress(
                            frac,
                            text=(
                                f"Страница {page + 1}/{pages} · "
                                f"найдено {found} · загружено {fetched}"
                            ),
                        )
                        lines.insert(
                            0,
                            f"стр. {page + 1}/{pages}: fetched={fetched}, found={found}",
                        )
                    elif et == "saved":
                        lines.insert(
                            0,
                            f"+ сохранено: {(ev.get('title') or '')[:50]} "
                            f"(всего {ev.get('saved_total')})",
                        )
                    elif et == "duplicate":
                        lines.insert(0, f"· дубль: {(ev.get('title') or '')[:50]}")
                    elif et == "error":
                        lines.insert(0, f"! {ev.get('message')}")
                    elif et == "done":
                        progress.progress(
                            1.0,
                            text=(
                                f"Готово: сохранено {ev.get('saved')}, "
                                f"дублей {ev.get('duplicates')}"
                            ),
                        )
                    live.code("\n".join(lines[:35]) or "…", language=None)

                status.info("Импорт с HH.ru…")
                add_log("INFO", f"UI URL-import: {url_clean[:100]}")
                slot = ProgressSlot()
                progress_start(
                    "Импорт по ссылке HH",
                    total=0,
                    phase="Загрузка страниц поиска",
                    steps=[
                        {"id": "fetch", "label": "Страницы", "status": "running"},
                        {"id": "save", "label": "Сохранение", "status": "pending"},
                    ],
                )
                slot.refresh()

                def on_event_wrapped(ev: dict) -> None:
                    on_event(ev)
                    et = ev.get("type")
                    if et == "page":
                        pages = max(1, int(ev.get("pages") or 1))
                        page = int(ev.get("page") or 0)
                        progress_update(
                            done=page + 1,
                            total=pages,
                            phase="Загрузка страниц поиска",
                            detail=f"Стр. {page + 1}/{pages}, загружено {ev.get('fetched')}",
                            step_id="fetch",
                            step_status="running",
                        )
                        slot.refresh()
                    elif et == "saved":
                        progress_update(
                            phase="Сохранение в БД",
                            detail=f"Сохранено: {(ev.get('title') or '')[:50]}",
                            step_id="fetch",
                            step_status="done",
                        )
                        progress_update(step_id="save", step_status="running")
                        slot.refresh()
                    elif et == "done":
                        progress_finish(
                            detail=(
                                f"Сохранено {ev.get('saved')}, "
                                f"дублей {ev.get('duplicates')}"
                            ),
                            status="stopped" if ev.get("stopped") else "done",
                        )
                        slot.refresh()

                report = run_hh_search_url_import(
                    url_clean,
                    on_event=on_event_wrapped,
                    should_stop=should_stop,
                )
                st.session_state["last_url_import"] = {
                    "found": report.found,
                    "fetched": report.fetched,
                    "saved": report.saved,
                    "duplicates": report.duplicates,
                    "errors": report.errors,
                    "stopped": report.stopped,
                    "saved_ids": report.saved_ids,
                }
                if report.stopped:
                    status.warning(
                        f"Остановлено. Загружено {report.fetched}, "
                        f"сохранено {report.saved}, дублей {report.duplicates}."
                    )
                elif report.errors and report.saved == 0:
                    status.error(
                        "Импорт не удался: " + "; ".join(report.errors[:3])
                    )
                else:
                    status.success(
                        f"Готово: на HH найдено ≈{report.found}, "
                        f"загружено {report.fetched}, "
                        f"новых в БД {report.saved}, "
                        f"дублей {report.duplicates}."
                    )
                if report.errors:
                    with st.expander("Ошибки"):
                        for e in report.errors[:20]:
                            st.text(e)
                log_path = get_session_log_path()
                if log_path:
                    st.caption(f"Лог: `{log_path}`")

    last = st.session_state.get("last_url_import")
    if last and not start:
        st.info(
            f"Последний импорт: найдено ≈{last.get('found')}, "
            f"загружено {last.get('fetched')}, "
            f"сохранено {last.get('saved')}, "
            f"дублей {last.get('duplicates')}"
            + (", остановлен" if last.get("stopped") else "")
        )

    # Общий workflow с автопоиском
    render_shared_queue_actions(user_profile, key_prefix="url")
    st.markdown("---")
    _render_results_table(key_prefix="url_results")
    st.markdown("---")
    _render_runs_history(key_prefix="url_runs")
