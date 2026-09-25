"""
Панель системных логов внизу страницы + путь к .txt файлу сессии.
"""

import streamlit as st

from job_assistant.utils.logging import add_log, get_session_log_path


def render_logs_panel() -> None:
    """Отрисовать раскрывающуюся панель логов."""
    st.markdown("---")
    log_path = get_session_log_path() or st.session_state.get("log_file_path")

    with st.expander(
        "🛠️ Системные логи (real-time) + файл сессии",
        expanded=bool(st.session_state.get("system_logs")),
    ):
        col_log1, col_log2, col_log3 = st.columns([3, 1, 1])
        with col_log1:
            if log_path:
                st.caption(f"Файл: `{log_path}`")
            else:
                st.caption("История событий приложения, парсера и Ollama.")
        with col_log2:
            if st.button("🔄 Обновить", use_container_width=True, key="refresh_logs"):
                st.rerun()
        with col_log3:
            if st.button("🧹 Очистить UI", use_container_width=True, key="clear_logs"):
                st.session_state["system_logs"] = []
                add_log("INFO", "UI-логи очищены пользователем (файл сессии сохранён).")
                st.rerun()

        log_container = st.container(height=260)
        with log_container:
            logs = st.session_state.get("system_logs") or []
            if not logs:
                st.text("Логи пусты...")
            else:
                for log in logs:
                    level = log["level"]
                    time_str = log["time"]
                    msg = log["message"]
                    color = {
                        "SUCCESS": "#22c55e",
                        "ERROR": "#ef4444",
                        "WARNING": "#f59e0b",
                    }.get(level, "#3b82f6")
                    st.markdown(
                        f"<span style='color: {color};'>[{time_str}] [{level}]</span> {msg}",
                        unsafe_allow_html=True,
                    )

        if log_path:
            try:
                from pathlib import Path

                raw = Path(log_path).read_text(encoding="utf-8")
                st.download_button(
                    "📥 Скачать лог сессии (.txt)",
                    data=raw.encode("utf-8"),
                    file_name=Path(log_path).name,
                    mime="text/plain",
                    use_container_width=True,
                )
            except OSError:
                pass
