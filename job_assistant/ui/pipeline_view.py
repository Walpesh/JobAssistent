"""
Дашборд автопоиска: настройки, прогресс, таблица результатов, история, экспорт.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Optional

import streamlit as st

from job_assistant.analyzer import (
    DEFAULT_MATCH_THRESHOLD,
    reanalyze_all_vacancies,
    run_suitability_analysis,
)
from job_assistant.utils.employment import LABELS as EMP_LABELS, format_employment_label
from job_assistant.db import (
    count_vacancies_by_status,
    delete_tk_only_vacancies,
    delete_duplicate_vacancies,
    finish_autofetch_run,
    get_autofetch_runs,
    list_vacancies,
    set_vacancy_status,
    start_autofetch_run,
)
from job_assistant.fetcher import (
    HHRuSource,
    RemoteOKSource,
    SearchFilters,
    WeWorkRemotelySource,
    run_autofetch,
)
from job_assistant.fetcher.hh_ru import build_apply_url
from job_assistant.letter import get_vacancies_awaiting_letter, run_letter_batch
from job_assistant.utils.logging import add_log, clear_live_callbacks, get_session_log_path, register_live_callback
from job_assistant.ui.progress_popup import (
    ProgressSlot,
    apply_progress_event,
    progress_finish,
    progress_start,
)


SOURCE_MAP = {
    "hh.ru": HHRuSource,
    "remoteok": RemoteOKSource,
    "weworkremotely": WeWorkRemotelySource,
}


def _export_csv(rows: list[dict]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "id",
            "title",
            "company",
            "source",
            "url",
            "match_score",
            "status",
            "salary",
            "location",
            "remote",
            "cover_letter",
        ]
    )
    for r in rows:
        writer.writerow(
            [
                r.get("id"),
                r.get("title"),
                r.get("company"),
                r.get("source"),
                r.get("url"),
                r.get("match_score"),
                r.get("status"),
                r.get("salary"),
                r.get("location"),
                r.get("remote"),
                (r.get("cover_letter") or "")[:5000],
            ]
        )
    return buf.getvalue().encode("utf-8-sig")


def _export_xlsx(rows: list[dict]) -> Optional[bytes]:
    try:
        from openpyxl import Workbook
    except ImportError:
        return None

    wb = Workbook()
    ws = wb.active
    ws.title = "vacancies"
    headers = [
        "id",
        "title",
        "company",
        "source",
        "url",
        "match_score",
        "status",
        "salary",
        "location",
        "remote",
        "cover_letter",
    ]
    ws.append(headers)
    for r in rows:
        ws.append(
            [
                r.get("id"),
                r.get("title"),
                r.get("company"),
                r.get("source"),
                r.get("url"),
                r.get("match_score"),
                r.get("status"),
                r.get("salary"),
                r.get("location"),
                r.get("remote"),
                (r.get("cover_letter") or "")[:5000],
            ]
        )
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def _run_full_pipeline(
    user_profile: str,
    *,
    keywords: str,
    sources: list[str],
    remote_only: bool,
    salary_min: int,
    experience: str,
    per_page: int,
    target_total: int,
    threshold: float,
    do_analyze: bool,
    do_letters: bool,
    analyze_limit: int,
    letter_limit: int,
    employment_types: list[str] | None = None,
    progress=None,
    status_box=None,
    live_log=None,
    live_results=None,
) -> dict:
    """
    Полный прогон: fetch → (analyze) → (letters).
    live_log / live_results — st.empty() для real-time вывода.
    """
    filters = SearchFilters(
        keywords=keywords.strip(),
        remote_only=remote_only,
        salary_min=salary_min if salary_min > 0 else None,
        experience=experience or None,
        per_page=per_page,
        max_pages=1,
        target_total=target_total if target_total > 0 else None,
        employment_types=employment_types or None,
    )
    source_objs = [SOURCE_MAP[n]() for n in sources if n in SOURCE_MAP]

    run_id = start_autofetch_run(
        keywords=keywords,
        sources=sources,
        filters={
            "remote_only": remote_only,
            "salary_min": salary_min,
            "experience": experience,
            "threshold": threshold,
            "do_analyze": do_analyze,
            "do_letters": do_letters,
        },
    )

    result = {
        "run_id": run_id,
        "found": 0,
        "saved": 0,
        "duplicates": 0,
        "analyzed": 0,
        "suitable": 0,
        "rejected": 0,
        "letters": 0,
        "errors": [],
    }

    def _status(kind: str, msg: str) -> None:
        if status_box is None:
            return
        fn = getattr(status_box, kind, None)
        if callable(fn):
            fn(msg)

    def _progress(value: float, text: str = "") -> None:
        if progress is None:
            return
        if text:
            progress.progress(value, text=text)
        else:
            progress.progress(value)

    try:
        # --- 1. Fetch (с live-логом на сайте) ---
        _status("info", "Шаг 1/3: поиск вакансий…")
        _progress(0.1, "Автопоиск…")
        progress_start(
            "Автопоиск и обработка",
            total=max(1, int(target_total or per_page)),
            phase="Поиск вакансий",
            steps=[
                {"id": "fetch", "label": "1. Поиск", "status": "running"},
                {"id": "analyze", "label": "2. Оценка", "status": "pending"},
                {"id": "letters", "label": "3. Письма", "status": "pending"},
            ],
        )
        slot = ProgressSlot()
        slot.refresh()

        live_lines: list[str] = []
        live_cards: list[str] = []

        def _render_live() -> None:
            if live_log is not None:
                # newest first, limit
                body = "\n".join(live_lines[:40]) or "…"
                live_log.code(body, language=None)
            if live_results is not None and live_cards:
                live_results.markdown("\n".join(live_cards[:25]))

        def _on_fetch_event(ev: dict) -> None:
            et = ev.get("type")
            if et == "source_start":
                live_lines.insert(0, f"→ источник {ev.get('source')}…")
            elif et == "source_raw":
                live_lines.insert(
                    0, f"  {ev.get('source')}: сырых {ev.get('count')}"
                )
            elif et == "saved":
                title = (ev.get("title") or "")[:55]
                live_lines.insert(
                    0,
                    f"  + [{ev.get('source')}] {title}  (всего новых: {ev.get('saved_total')})",
                )
                try:
                    slot.update(
                        done=int(ev.get("saved_total") or 0),
                        total=max(1, int(target_total or 1)),
                        phase="Поиск вакансий",
                        detail=f"Сохранено: {title}",
                        step_id="fetch",
                        step_status="running",
                    )
                except Exception:
                    pass
                live_cards.insert(
                    0,
                    f"- **{title}** · {ev.get('company') or '—'} · `{ev.get('source')}`",
                )
                _progress(
                    min(0.05 + 0.3 * (ev.get("saved_total") or 0) / max(per_page * len(sources), 1), 0.34),
                    f"Сохранено {ev.get('saved_total')}…",
                )
            elif et == "duplicate":
                live_lines.insert(0, f"  · дубль: {(ev.get('title') or '')[:40]}")
            elif et == "error":
                live_lines.insert(0, f"  ! {ev.get('message')}")
            elif et == "source_done":
                live_lines.insert(
                    0, f"✓ {ev.get('source')}: новых {ev.get('saved')}"
                )
            _render_live()

        def _on_log_entry(entry: dict) -> None:
            live_lines.insert(
                0, f"[{entry.get('time')}] [{entry.get('level')}] {entry.get('message')}"
            )
            if len(live_lines) > 60:
                live_lines.pop()
            _render_live()

        clear_live_callbacks()
        register_live_callback(_on_log_entry)

        fetch_report = run_autofetch(
            filters, sources=source_objs, on_event=_on_fetch_event
        )
        clear_live_callbacks()

        result["found"] = fetch_report.total_found
        result["saved"] = fetch_report.saved
        result["duplicates"] = fetch_report.duplicates
        result["errors"].extend(fetch_report.errors)
        _progress(
            0.35,
            f"Уникальных сохранено {fetch_report.saved}/{target_total}",
        )
        live_lines.insert(
            0,
            f"=== Fetch done: candidates={fetch_report.total_found} "
            f"saved={fetch_report.saved}/{target_total} dup={fetch_report.duplicates} "
            f"rounds={fetch_report.rounds}",
        )
        _render_live()

        # Анализ и письма разрешены только после достижения целевого количества.
        # Если источники физически исчерпаны раньше — не запускаем частичный пайплайн.
        saved_ids = list(fetch_report.saved_ids)
        target_reached = (
            fetch_report.target_reached
            or target_total <= 0
            or fetch_report.saved >= target_total
        )
        if not target_reached:
            msg = (
                f"Цель не достигнута: сохранено {fetch_report.saved} из "
                f"{target_total} уникальных вакансий. "
                f"Источники исчерпаны, анализ и письма пропущены."
            )
            add_log("WARNING", msg)
            result["errors"].append(msg)
            _progress(1.0, msg)
            _status("warning", msg)
            finish_autofetch_run(
                run_id,
                found_count=result["found"],
                saved_count=result["saved"],
                duplicates=result["duplicates"],
                analyzed_count=0,
                suitable_count=0,
                rejected_count=0,
                letters_count=0,
                status="exhausted",
                error_message=msg,
            )
            try:
                progress_finish(detail=msg, status="stopped")
                slot.refresh()
            except Exception:
                pass
            return result

        # --- 2. Analyze: только вакансии ЭТОГО прогона (saved_ids) ---
        if do_analyze and user_profile and saved_ids:
            _status("info", "Шаг 2/3: оценка соответствия (Qwen)…")
            _progress(0.45, "Suitability…")
            from job_assistant.ui.progress_popup import progress_update
            progress_update(
                step_id="fetch",
                step_status="done",
                phase="Оценка соответствия",
            )
            progress_update(step_id="analyze", step_status="running")
            slot.refresh()
            # Берём не больше analyze_limit из только что сохранённых
            ids_to_judge = saved_ids[: max(1, int(analyze_limit))]
            add_log(
                "INFO",
                f"Пайплайн: оценка {len(ids_to_judge)} из {len(saved_ids)} новых id",
            )
            # Пауза между вакансиями + обязательный enrich description
            # (исправляет «плохой» авто-анализ сразу после парсинга)
            def _on_analyze(ev: dict) -> None:
                apply_progress_event(ev, slot)
                if ev.get("type") == "item":
                    progress_update(
                        done=int(ev.get("done") or 0),
                        total=int(ev.get("total") or len(ids_to_judge)),
                        phase="Оценка соответствия",
                        detail=str(ev.get("detail") or ""),
                        step_id="analyze",
                        step_status="running",
                    )
                    slot.refresh()

            analyze_report = run_suitability_analysis(
                user_profile,
                threshold=threshold,
                vacancy_ids=ids_to_judge,
                generate_letters=False,
                pause_sec=1.5,
                limit=len(ids_to_judge) or analyze_limit,
                on_progress=_on_analyze,
            )
            result["analyzed"] = analyze_report.processed
            result["suitable"] = analyze_report.suitable
            result["rejected"] = analyze_report.rejected
            result["errors"].extend(analyze_report.errors)
            # id suitable из этого прогона
            suitable_ids = [
                r["id"]
                for r in analyze_report.results
                if isinstance(r, dict) and r.get("suitable") and r.get("id")
            ]
            _progress(
                0.7,
                f"Оценено {analyze_report.processed}: suitable={analyze_report.suitable}",
            )
        else:
            suitable_ids = []
            if do_analyze and not saved_ids:
                add_log("INFO", "Пайплайн: новых вакансий нет — оценка пропущена")
            _progress(0.7, "Оценка пропущена")

        # --- 3. Letters: только suitable этого прогона ---
        if do_letters and user_profile and suitable_ids:
            _status("info", "Шаг 3/3: генерация писем…")
            _progress(0.8, "Cover letters…")
            ids_for_letters = suitable_ids[: max(1, int(letter_limit))]
            add_log(
                "INFO",
                f"Пайплайн: письма для {len(ids_for_letters)} suitable id",
            )
            progress_update(
                step_id="analyze",
                step_status="done",
                phase="Генерация писем",
            )
            progress_update(step_id="letters", step_status="running")
            slot.refresh()

            def _on_letters(ev: dict) -> None:
                apply_progress_event(ev, slot)
                if ev.get("type") == "item":
                    progress_update(
                        done=int(ev.get("done") or 0),
                        total=int(ev.get("total") or len(ids_for_letters)),
                        phase="Генерация писем",
                        detail=str(ev.get("detail") or ""),
                        step_id="letters",
                        step_status="running",
                    )
                    slot.refresh()

            letter_report = run_letter_batch(
                user_profile,
                vacancy_ids=ids_for_letters,
                pause_sec=1.0,
                on_progress=_on_letters,
            )
            result["letters"] = letter_report.generated
            result["errors"].extend(letter_report.errors)
            _progress(0.95, f"Писем: {letter_report.generated}")
        elif do_letters and user_profile and do_analyze and not suitable_ids:
            add_log("INFO", "Пайплайн: suitable=0 — письма не нужны")
            _progress(0.95, "Нет suitable для писем")
        else:
            _progress(0.95, "Письма пропущены")

        finish_autofetch_run(
            run_id,
            found_count=result["found"],
            saved_count=result["saved"],
            duplicates=result["duplicates"],
            analyzed_count=result["analyzed"],
            suitable_count=result["suitable"],
            rejected_count=result["rejected"],
            letters_count=result["letters"],
            status="done",
            error_message="; ".join(result["errors"][:5]) if result["errors"] else None,
        )
        _progress(1.0, "Готово")
        progress_finish(
            detail=(
                f"Готово: найдено {result['found']}, новых {result['saved']}, "
                f"suitable {result['suitable']}, писем {result['letters']}"
            ),
            status="done",
        )
        try:
            slot.refresh()
        except Exception:
            pass
        _status(
            "success",
            f"Готово: найдено {result['found']}, новых {result['saved']}, "
            f"suitable {result['suitable']}, писем {result['letters']}",
        )
    except Exception as e:
        add_log("ERROR", f"Pipeline failed: {e}")
        result["errors"].append(str(e))
        finish_autofetch_run(
            run_id,
            found_count=result["found"],
            saved_count=result["saved"],
            duplicates=result["duplicates"],
            analyzed_count=result["analyzed"],
            suitable_count=result["suitable"],
            rejected_count=result["rejected"],
            letters_count=result["letters"],
            status="error",
            error_message=str(e),
        )
        _status("error", f"Ошибка пайплайна: {e}")
        _progress(1.0, "Ошибка")
        try:
            progress_finish(detail=str(e), status="error")
        except Exception:
            pass

    return result


def _render_results_table(*, key_prefix: str = "results") -> None:
    kp = key_prefix
    """Таблица результатов + действия."""
    st.markdown("### 📋 Результаты")

    col_f1, col_f2, col_f3 = st.columns(3)
    with col_f1:
        status_filter = st.selectbox(
            "Статус",
            options=["all", "new", "analyzed", "rejected", "sent"],
            format_func=lambda x: {
                "all": "Все",
                "new": "new",
                "analyzed": "analyzed (подходит)",
                "rejected": "rejected",
                "sent": "sent (откликнулся)",
            }[x],
            key=f"{kp}_status_filter",
        )
    with col_f2:
        suitable_only = st.checkbox("Только suitable", value=False, key=f"{kp}_suitable")
    with col_f3:
        has_letter = st.selectbox(
            "Письмо",
            options=["any", "yes", "no"],
            format_func=lambda x: {"any": "Любые", "yes": "С письмом", "no": "Без письма"}[x],
            key=f"{kp}_letter_filter",
        )

    rows = list_vacancies(
        status=None if status_filter == "all" else status_filter,
        suitable_only=suitable_only,
        has_letter={"yes": True, "no": False, "any": None}[has_letter],
        limit=200,
    )

    if not rows:
        st.caption("Нет вакансий по выбранным фильтрам.")
        return

    # Экспорт
    c1, c2, c3 = st.columns([1, 1, 2])
    with c1:
        st.download_button(
            "📥 CSV",
            data=_export_csv(rows),
            file_name=f"vacancies_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
            mime="text/csv",
            use_container_width=True,
            key=f"{kp}_dl_csv",
        )
    with c2:
        xlsx = _export_xlsx(rows)
        if xlsx:
            st.download_button(
                "📥 Excel",
                data=xlsx,
                file_name=f"vacancies_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
                key=f"{kp}_dl_xlsx",
            )
        else:
            st.caption("Excel: pip install openpyxl")

    st.caption(f"Показано записей: {len(rows)}")

    for v in rows:
        score = v.get("match_score")
        score_s = f"{score:.0f}%" if score is not None else "—"
        status = v.get("status") or "—"
        title = v.get("title") or "Без названия"
        url = v.get("url") or ""
        company = v.get("company") or "—"
        source = v.get("source") or "—"
        letter = (v.get("cover_letter") or "").strip()
        vid = v["id"]

        with st.container(border=True):
            head_l, head_r = st.columns([4, 1])
            with head_l:
                link = f"[{title}]({url})" if url else title
                st.markdown(f"**{link}** · {company} · `{source}`")
                emp_label = format_employment_label(v.get("employment_type"))
                st.caption(
                    f"id={vid} · match **{score_s}** · status **{status}** · "
                    f"устройство: **{emp_label}**"
                )
            with head_r:
                apply_url = build_apply_url(
                    source=source,
                    url=url,
                    external_id=str(v.get("external_id") or ""),
                )
                # Кнопка «Откликнуться»: открывает форму отклика + показывает письмо
                if apply_url and status != "rejected":
                    st.link_button(
                        "🚀 Откликнуться",
                        apply_url,
                        use_container_width=True,
                        help="Откроет страницу отклика. Скопируйте письмо ниже и вставьте в форму.",
                    )
                if status != "sent":
                    if st.button(
                        "✅ Отметить отклик",
                        key=f"{kp}_sent_{vid}",
                        use_container_width=True,
                        help="Пометить вакансию как sent после того, как отправили отклик.",
                    ):
                        set_vacancy_status(vid, "sent")
                        st.rerun()
                if status not in ("rejected",):
                    if st.button("🚫 Не интересно", key=f"{kp}_rej_{vid}", use_container_width=True):
                        set_vacancy_status(vid, "rejected")
                        st.rerun()

            # Письмо: для suitable / analyzed сразу раскрываем, чтобы скопировать при отклике
            if letter:
                expand_letter = status in ("analyzed", "new") or bool(v.get("is_suitable"))
                with st.expander("✉️ Сопроводительное письмо — скопируйте и вставьте в форму отклика", expanded=expand_letter):
                    st.text_area(
                        "letter",
                        value=letter,
                        height=220,
                        key=f"{kp}_letter_text_{vid}",
                        label_visibility="collapsed",
                    )
                    st.code(letter, language=None)
                    st.caption(
                        "1) Нажмите «🚀 Откликнуться» — откроется сайт вакансии.  "
                        "2) Скопируйте письмо выше (Ctrl+A в поле / из блока code).  "
                        "3) Вставьте в поле сопроводительного письма на сайте.  "
                        "4) Нажмите «✅ Отметить отклик»."
                    )
            elif status in ("analyzed",) and v.get("is_suitable"):
                st.info("Письмо ещё не сгенерировано — нажмите «Только сгенерировать письма» выше.")

            if v.get("analysis_result"):
                label = "❌ Почему отклонено" if status == "rejected" else "📊 Анализ"
                with st.expander(label, expanded=(status == "rejected")):
                    st.markdown(v["analysis_result"][:4000])


def _render_runs_history(*, key_prefix: str = "runs") -> None:
    st.markdown("### 🕘 История автопоисков")
    runs = get_autofetch_runs(limit=20)
    if not runs:
        st.caption("Пока нет запусков.")
        return

    for r in runs:
        started = r.get("started_at") or "—"
        status = r.get("status") or "—"
        kw = r.get("keywords") or "—"
        st.markdown(
            f"**#{r['id']}** · `{status}` · {started}  \n"
            f"«{kw}» · источники: {r.get('sources') or '—'}  \n"
            f"найдено {r.get('found_count') or 0} · новых {r.get('saved_count') or 0} · "
            f"suitable {r.get('suitable_count') or 0} · писем {r.get('letters_count') or 0}"
        )
        if r.get("error_message"):
            st.caption(f"⚠ {r['error_message'][:200]}")
        st.divider()


def render_shared_queue_actions(
    user_profile: Optional[str],
    *, key_prefix: str = "queue",
) -> None:
    """Очереди анализа/писем и служебные кнопки — общие для вкладок автопоиска и URL-импорта."""
    kp = key_prefix
    st.markdown("---")
    st.markdown("#### ⚙️ Очереди без нового поиска")
    try:
        counts = count_vacancies_by_status()
        awaiting = len(get_vacancies_awaiting_letter(limit=500))
    except Exception:
        counts, awaiting = {}, 0
    st.caption(
        f"new: **{counts.get('new', 0)}** · analyzed: **{counts.get('analyzed', 0)}** · "
        f"rejected: **{counts.get('rejected', 0)}** · ждут письмо: **{awaiting}**"
    )
    c1, c2, c3 = st.columns(3)
    with c1:
        thr = st.number_input("Порог", min_value=40, max_value=90, value=65, key=f"{kp}_thr")
    with c2:
        lim_a = st.number_input("Лимит оценки", min_value=1, max_value=30, value=5, key=f"{kp}_lim_a")
    with c3:
        lim_l = st.number_input("Лимит писем", min_value=1, max_value=15, value=3, key=f"{kp}_lim_l")

    b1, b2 = st.columns(2)
    with b1:
        if st.button("🧠 Только оценить очередь new", use_container_width=True, key=f"{kp}_btn_analyze"):
            if not user_profile:
                st.error("Нет профиля.")
            elif counts.get("new", 0) == 0:
                st.info("Очередь new пуста.")
            else:
                slot = ProgressSlot()
                progress_start(
                    "Оценка очереди new",
                    total=int(lim_a),
                    phase="Оценка соответствия",
                    steps=[{"id": "analyze", "label": "Оценка", "status": "running"}],
                )
                slot.refresh()

                def _on_q_analyze(ev: dict) -> None:
                    apply_progress_event(ev, slot)

                with st.spinner("Оценка…"):
                    rep = run_suitability_analysis(
                        user_profile,
                        limit=int(lim_a),
                        threshold=float(thr),
                        pause_sec=1.5,
                        on_progress=_on_q_analyze,
                    )
                progress_finish(
                    detail=f"Оценено {rep.processed}: suitable={rep.suitable}",
                    status="done",
                )
                slot.refresh()
                st.success(
                    f"Оценено {rep.processed}: suitable={rep.suitable}, rejected={rep.rejected}"
                )
                st.rerun()
    with b2:
        if st.button("✍️ Только сгенерировать письма", use_container_width=True, key=f"{kp}_btn_letters"):
            if not user_profile:
                st.error("Нет профиля.")
            elif awaiting == 0:
                st.info("Нет вакансий, ждущих письмо.")
            else:
                slot = ProgressSlot()
                progress_start(
                    "Генерация писем",
                    total=int(lim_l),
                    phase="Сопроводительные письма",
                    steps=[{"id": "letters", "label": "Письма", "status": "running"}],
                )
                slot.refresh()

                def _on_q_letters(ev: dict) -> None:
                    apply_progress_event(ev, slot)

                with st.spinner("Письма…"):
                    rep = run_letter_batch(
                        user_profile,
                        limit=int(lim_l),
                        pause_sec=1.0,
                        on_progress=_on_q_letters,
                    )
                progress_finish(
                    detail=f"Писем: {rep.generated}/{rep.processed}",
                    status="done",
                )
                slot.refresh()
                st.success(f"Писем: {rep.generated}/{rep.processed}")
                st.rerun()

    b3, b4 = st.columns(2)
    with b3:
        if st.button(
            "🔄 Повторный анализ всех вакансий",
            use_container_width=True,
            key=f"{kp}_btn_reanalyze",
            help="new + analyzed + rejected. Каждая вакансия — отдельный запрос к модели "
            "с паузой; используется my_profile.docx и формула пересечения навыков.",
        ):
            if not user_profile:
                st.error("Нет профиля (my_profile.docx).")
            else:
                total = sum(counts.get(s, 0) for s in ("new", "analyzed", "rejected"))
                if total == 0:
                    st.info("Нет вакансий для повторного анализа.")
                else:
                    slot = ProgressSlot()
                    progress_start(
                        "Повторный анализ",
                        total=total,
                        phase="Повторный анализ всех вакансий",
                        steps=[{"id": "reanalyze", "label": "Анализ", "status": "running"}],
                    )
                    slot.refresh()

                    def _on_re(ev: dict) -> None:
                        apply_progress_event(ev, slot)

                    with st.spinner(f"Повторный анализ {total} вакансий (по одной, с паузой)…"):
                        rep = reanalyze_all_vacancies(
                            user_profile,
                            threshold=float(thr),
                            pause_sec=1.5,
                            limit=max(total, int(lim_a)),
                            on_progress=_on_re,
                        )
                    progress_finish(
                        detail=f"Повторно: {rep.processed}, suitable={rep.suitable}",
                        status="done",
                    )
                    slot.refresh()
                    st.success(
                        f"Повторно оценено {rep.processed}: "
                        f"suitable={rep.suitable}, rejected={rep.rejected}, "
                        f"ошибок={len(rep.errors)}"
                    )
                    st.rerun()
    with b4:
        if st.button(
            "🗑️ Отсеять только ТК РФ",
            use_container_width=True,
            key=f"{kp}_btn_tk",
            help="Удаляет вакансии, где указано ТОЛЬКО официальное оформление по ТК РФ. "
            "Если в тексте есть и ТК, и ГПХ/самозанятый — вакансия остаётся.",
        ):
            with st.spinner("Отсев вакансий только с ТК РФ…"):
                res = delete_tk_only_vacancies()
            st.success(
                f"Удалено: {res['deleted']}. Оставлено: {res['kept']} "
                f"(смешанные ТК+ГПХ сохранены)."
            )
            st.rerun()

    if st.button(
        "🧹 Отсеять повторяющиеся",
        use_container_width=True,
        key=f"{kp}_btn_dedupe",
        help="Сканирует всю базу и удаляет дубликаты: одинаковый URL, "
        "source+external_id, или одинаковые название+компания. "
        "Оставляет более ценную запись (sent/analyzed, с письмом, выше score).",
    ):
        with st.spinner("Поиск и удаление дублей по всей базе…"):
            res = delete_duplicate_vacancies()
        br = res.get("by_reason") or {}
        st.success(
            f"Дублей удалено: {res['deleted']}. Осталось: {res['kept']}. "
            f"(URL: {br.get('url', 0)}, source+id: {br.get('source_external', 0)}, "
            f"название+компания: {br.get('title_company', 0)})"
        )
        st.rerun()



def render_pipeline_view(user_profile: Optional[str]) -> None:
    """Главный экран автопайплайна."""
    st.markdown("### 🔍 Автопоиск и обработка вакансий")
    st.caption("Поиск → оценка соответствия → письма · прогресс и история ниже")

    with st.form("pipeline_settings"):
        st.markdown("#### Настройки поиска")
        keywords = st.text_input("Ключевые слова / навыки", value="python developer")

        sources = st.multiselect(
            "Источники",
            options=list(SOURCE_MAP.keys()),
            default=list(SOURCE_MAP.keys()),
        )

        c1, c2, c3 = st.columns(3)
        with c1:
            remote_only = st.checkbox("Только remote", value=True)
        with c2:
            salary_min = st.number_input("Мин. зарплата", min_value=0, value=0, step=10000)
        with c3:
            per_page = st.number_input(
                "Макс. с источника",
                min_value=1,
                max_value=50,
                value=10,
                step=1,
                help="Сколько сырых вакансий запрашивать у каждого API/RSS",
            )

        target_total = st.number_input(
            "Сколько НОВЫХ вакансий взять в работу (всего)",
            min_value=1,
            max_value=200,
            value=10,
            step=1,
            help="Цель по уникальным новым вакансиям за прогон. Дубли не считаются. "
            "Поиск идёт в цикле: сначала углубляет страницы, затем ищет по каждому "
            "ключевому слову отдельно, затем ослабляет фильтры (зарплата/опыт/remote), "
            "пока не наберёт нужное число или не исчерпает источники.",
        )

        experience = st.selectbox(
            "Опыт (HH)",
            options=["", "noExperience", "between1And3", "between3And6", "moreThan6"],
            format_func=lambda x: {
                "": "Любой",
                "noExperience": "Нет опыта",
                "between1And3": "1–3 года",
                "between3And6": "3–6 лет",
                "moreThan6": "6+ лет",
            }.get(x, x),
        )

        employment_types = st.multiselect(
            "Вид устройства на работу",
            options=["tk", "gph", "self", "agree"],
            default=["tk", "gph", "self", "agree"],
            format_func=lambda x: {
                "tk": "Официальное ТК РФ",
                "gph": "Договор ГПХ",
                "self": "Самозанятый",
                "agree": "По договорённости",
            }.get(x, x),
            help="Фильтр при сохранении. Если в вакансии только ТК, а ТК не выбран — она не попадёт в базу. "
            "Смешанные (ТК+ГПХ) сохраняются, если выбран хотя бы один из типов.",
        )

        st.markdown("#### Обработка")
        threshold = st.slider(
            "Порог матчинга",
            min_value=40,
            max_value=90,
            value=int(DEFAULT_MATCH_THRESHOLD),
            step=5,
        )
        do_analyze = st.checkbox("Сразу оценить соответствие (Qwen)", value=True)
        do_letters = st.checkbox("Сразу генерировать письма для suitable", value=False)

        c4, c5 = st.columns(2)
        with c4:
            analyze_limit = st.number_input(
                "Лимит оценки (из новых)",
                min_value=1,
                max_value=200,
                value=10,
                help="Сколько из только что найденных оценить. Поставьте = «Сколько новых», чтобы оценить все.",
            )
        with c5:
            letter_limit = st.number_input(
                "Лимит писем (из suitable)",
                min_value=1,
                max_value=50,
                value=10,
                help="Сколько писем сгенерировать среди suitable этого прогона.",
            )

        submitted = st.form_submit_button(
            "🔍 Запустить автопоиск вакансий",
            type="primary",
            use_container_width=True,
        )

    if submitted:
        if not sources:
            st.warning("Выберите хотя бы один источник.")
        else:
            progress = st.progress(0, text="Старт…")
            status_box = st.empty()
            st.markdown("#### 📡 Ход поиска (real-time)")
            live_log = st.empty()
            live_results = st.empty()
            live_log.info("Ожидание событий…")
            pipeline_result = _run_full_pipeline(
                user_profile or "",
                keywords=keywords,
                sources=sources,
                remote_only=remote_only,
                salary_min=int(salary_min),
                experience=experience,
                per_page=int(per_page),
                target_total=int(target_total),
                threshold=float(threshold),
                do_analyze=do_analyze,
                do_letters=do_letters,
                analyze_limit=int(analyze_limit),
                letter_limit=int(letter_limit),
                employment_types=list(employment_types) if employment_types else None,
                progress=progress,
                status_box=status_box,
                live_log=live_log,
                live_results=live_results,
            )
            st.session_state["last_pipeline_result"] = pipeline_result
            log_path = get_session_log_path()
            if log_path:
                st.caption(f"Детальный лог сессии: `{log_path}`")

    render_shared_queue_actions(user_profile, key_prefix="auto")

    st.markdown("---")
    _render_results_table(key_prefix="auto_results")
    st.markdown("---")
    _render_runs_history(key_prefix="auto_runs")
