"""
JobAssistant — точка входа.
Модульный поток: config → utils → db → profile → parser → fetcher → analyzer → llm → ui
"""

from job_assistant.config import setup_page, PROFILE_FILENAME
from job_assistant.utils import init_logging
from job_assistant.db import init_db
from job_assistant.profile import load_profile_from_docx
from job_assistant.ui import render_sidebar, render_main_view, render_logs_panel


def main() -> None:
    # 1. Настройка страницы и CSS
    setup_page()

    # 2. Логирование
    init_logging()

    # 3. База данных
    init_db()

    # 4. Профиль пользователя
    user_profile = load_profile_from_docx(PROFILE_FILENAME)

    # 5. UI
    render_sidebar(user_profile)
    render_main_view(user_profile)
    render_logs_panel()


if __name__ == "__main__":
    main()
