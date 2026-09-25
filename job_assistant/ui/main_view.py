"""
Основной интерфейс: вкладки «Автопайплайн» и «Ручной анализ».
"""

from __future__ import annotations

import streamlit as st

from job_assistant.db import get_chat_by_id, save_chat_to_db
from job_assistant.parser import fetch_job_text
from job_assistant.llm import build_prompt, stream_ollama_response
from job_assistant.ui.pipeline_view import render_pipeline_view
from job_assistant.ui.progress_popup import render_progress_popup
from job_assistant.ui.url_import_view import render_url_import_view
from job_assistant.utils.logging import add_log


def _render_saved_chat(chat_id: int) -> None:
    """Просмотр сохранённого ручного анализа."""
    row = get_chat_by_id(chat_id)
    if not row:
        st.warning("Чат не найден.")
        st.session_state["current_chat_id"] = None
        if st.button("← Назад"):
            st.rerun()
        return

    job_url_db, job_desc_db, analysis_db, letter_db, title_db = row

    st.markdown(
        f"""
    <div class="info-box">
        <strong>📁 Сохранённый ручной анализ</strong><br>
        {title_db}
    </div>
    """,
        unsafe_allow_html=True,
    )

    with st.expander("📄 Описание вакансии", expanded=False):
        st.text_area(
            "vacancy_saved",
            value=job_desc_db or "",
            height=180,
            disabled=True,
            label_visibility="collapsed",
        )

    st.markdown("---")
    st.markdown(analysis_db or "_Пусто_")

    st.markdown("---")
    if st.button("← К ручному анализу", use_container_width=False):
        st.session_state["current_chat_id"] = None
        st.rerun()


def _render_manual_analysis(user_profile: str) -> None:
    """Одна вакансия: URL/текст → письмо через стриминг Ollama."""
    st.markdown("### Ручной разбор вакансии")
    st.caption("Для разового отклика. Массовый поиск — во вкладке «Автопоиск».")

    if "parsed_job" not in st.session_state:
        st.session_state["parsed_job"] = ""

    col1, col2 = st.columns([1.1, 1.9], gap="large")

    with col1:
        st.markdown("**Ссылка**")
        job_url = st.text_input(
            "URL",
            placeholder="https://hh.ru/vacancy/...",
            label_visibility="collapsed",
            key="job_url_input",
        )
        if st.button("📥 Загрузить по ссылке", use_container_width=True):
            if not job_url.strip():
                st.warning("Вставьте URL.")
            else:
                with st.spinner("Парсинг…"):
                    text = fetch_job_text(job_url.strip())
                    st.session_state["parsed_job"] = text
                    st.session_state["manual_job_description"] = text
                st.success("Текст загружен.")
                st.rerun()

    with col2:
        st.markdown("**Текст вакансии**")
        # Единый state: key widget = parsed_job
        if "manual_job_description" not in st.session_state:
            st.session_state["manual_job_description"] = st.session_state.get("parsed_job", "")
        job_description = st.text_area(
            "Текст вакансии",
            height=260,
            placeholder="Вставьте текст или загрузите по ссылке…",
            label_visibility="collapsed",
            key="manual_job_description",
        )
        st.session_state["parsed_job"] = job_description or ""

    st.markdown("---")
    _, mid, _ = st.columns([1, 2, 1])
    with mid:
        run = st.button(
            "🚀 Проанализировать и составить письмо",
            type="primary",
            use_container_width=True,
        )

    if not run:
        return

    text = (st.session_state.get("parsed_job") or "").strip()
    if not text:
        st.warning("Нужно описание вакансии.")
        add_log("WARNING", "Ручной анализ: пустое описание.")
        return

    prompt = build_prompt(user_profile, text)
    add_log("INFO", "Ручной анализ: запрос к Ollama…")

    with st.spinner("Генерация (Qwen)…"):
        st.markdown("### Результат")
        placeholder = st.empty()
        full = ""
        for token in stream_ollama_response(prompt):
            full += token
            placeholder.markdown(full + "▌")
        placeholder.markdown(full)

        title = text.split("\n")[0][:50] or "Ручная вакансия"
        save_chat_to_db(title, st.session_state.get("job_url_input") or "", text, full)
        add_log("SUCCESS", f"Ручной анализ сохранён: «{title}»")
        st.markdown(
            '<div class="success-box">✅ Сохранено в историю ручных откликов (сайдбар)</div>',
            unsafe_allow_html=True,
        )


def render_main_view(user_profile: str) -> None:
    st.title("Локальный карьерный ассистент")
    st.caption("Автопоиск → оценка → письма · или разовый ручной отклик")
    render_progress_popup()

    if not user_profile:
        st.error("Файл **my_profile.docx** не найден рядом с `app.py`.")
        st.stop()

    if st.session_state.get("current_chat_id"):
        _render_saved_chat(st.session_state["current_chat_id"])
        return

    tab_auto, tab_url, tab_manual = st.tabs(
        [
            "🔍 Автопоиск и результаты",
            "🔗 База вакансий по ссылке",
            "✍️ Ручной анализ",
        ]
    )
    with tab_auto:
        render_pipeline_view(user_profile)
    with tab_url:
        render_url_import_view(user_profile)
    with tab_manual:
        _render_manual_analysis(user_profile)
