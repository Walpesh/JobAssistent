"""
Сайдбар — только навигация и статус.
Автопоиск / оценка / письма живут на главной вкладке «Автопоиск и результаты».
"""

from __future__ import annotations

import streamlit as st

from job_assistant.db import (
    count_vacancies_by_status,
    delete_chat,
    get_all_chats,
)
from job_assistant.letter import get_vacancies_awaiting_letter
from job_assistant.utils.logging import add_log, get_session_log_path


def render_sidebar(user_profile: str | None = None) -> None:
    """Компактная боковая панель без дублирования основного UI."""
    with st.sidebar:
        st.markdown("### 🤖 JobAssistant")
        st.caption("Локально · Qwen 2.5 · Ollama")

        st.markdown("---")

        # Профиль
        if user_profile:
            st.success("Профиль: `my_profile.docx` ✓")
        else:
            st.error("Нет `my_profile.docx`")

        # Очереди (read-only сводка)
        st.markdown("#### 📊 Очереди")
        try:
            counts = count_vacancies_by_status()
            awaiting_letters = len(get_vacancies_awaiting_letter(limit=500))
        except Exception:
            counts = {"new": 0, "analyzed": 0, "rejected": 0, "sent": 0}
            awaiting_letters = 0

        st.markdown(
            f"""
| Статус | Кол-во |
|--------|--------|
| new | **{counts.get('new', 0)}** |
| analyzed | **{counts.get('analyzed', 0)}** |
| rejected | **{counts.get('rejected', 0)}** |
| sent | **{counts.get('sent', 0)}** |
| ждут письмо | **{awaiting_letters}** |
"""
        )
        st.caption("Управление — во вкладке «Автопоиск и результаты».")

        st.markdown("---")

        # Ручной режим
        st.markdown("#### ✍️ Ручной анализ")
        if st.button("➕ Новый ручной анализ", use_container_width=True):
            st.session_state["current_chat_id"] = None
            st.session_state["parsed_job"] = ""
            st.session_state["active_tab_hint"] = "manual"
            add_log("INFO", "Новый сеанс ручного анализа.")
            st.rerun()

        chats = get_all_chats()
        if not chats:
            st.caption("История ручных откликов пуста")
        else:
            st.caption("Сохранённые ручные анализы:")
            for chat_id, title, date in chats[:20]:
                col_a, col_b = st.columns([0.82, 0.18])
                with col_a:
                    short = (title[:28] + "…") if len(title) > 28 else title
                    if st.button(
                        f"📌 {short}",
                        key=f"chat_{chat_id}",
                        use_container_width=True,
                    ):
                        st.session_state["current_chat_id"] = chat_id
                        add_log("INFO", f"Открыт архивный чат ID: {chat_id}")
                        st.rerun()
                with col_b:
                    if st.button("🗑️", key=f"del_{chat_id}", help="Удалить"):
                        delete_chat(chat_id)
                        if st.session_state.get("current_chat_id") == chat_id:
                            st.session_state["current_chat_id"] = None
                        st.rerun()

        st.markdown("---")
        log_path = get_session_log_path()
        if log_path:
            st.caption(f"Лог: `{log_path.name}`")
