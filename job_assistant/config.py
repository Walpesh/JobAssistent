"""
Конфигурация приложения: константы, заголовки, CSS, настройки Ollama.
"""

# --- Файлы и БД ---
PROFILE_FILENAME = "my_profile.docx"
DB_NAME = "chat_history.db"

# --- Ollama ---
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "qwen2.5:14b-instruct"
OLLAMA_OPTIONS = {
    "temperature": 0.35,
    "top_p": 0.85,
    "top_k": 40,
    "repeat_penalty": 1.12,
    "num_ctx": 10000,
    "num_predict": 2500,
}

# --- HTTP-заголовки для парсинга HH.ru ---
HH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# --- Контакты, добавляемые в конец ответа ---
CONTACTS_BLOCK = """
---
Контакты для связи:
📞 Телефон: +7 (951) 591-32-39
✉️ Email: walpesh1@gmail.com
📱 Telegram: https://t.me/iwalpesh
🔗 GitHub: https://github.com/Walpesh

Недавно я разработал локального карьерного AI-ассистента для облегчения поиска работы, он использует информацию о моей жизни, мой опыт и навыки, чтобы отправить вам это письмо. Код этого ассистента находится в моём профиле GitHub :)
"""

# --- Кастомный CSS ---
CUSTOM_CSS = """
<style>
    .main .block-container {
        padding-top: 1.5rem;
        padding-bottom: 2rem;
        max-width: 1200px;
    }
    h1 {
        font-weight: 700 !important;
        letter-spacing: -0.5px;
    }
    section[data-testid="stSidebar"] {
        background: linear-gradient(180deg, #0f172a 0%, #1e293b 100%);
    }
    section[data-testid="stSidebar"] * {
        color: #e2e8f0 !important;
    }
    section[data-testid="stSidebar"] .stButton > button {
        background: rgba(255,255,255,0.08);
        border: 1px solid rgba(255,255,255,0.12);
        color: #f1f5f9 !important;
        border-radius: 10px;
        transition: all 0.2s ease;
    }
    section[data-testid="stSidebar"] .stButton > button:hover {
        background: rgba(59, 130, 246, 0.25);
        border-color: #3b82f6;
    }
    .stButton > button[kind="primary"] {
        border-radius: 10px;
        font-weight: 600;
        padding: 0.6rem 1.4rem;
    }
    .info-box {
        background: rgba(59, 130, 246, 0.1);
        border-left: 4px solid #3b82f6;
        padding: 0.9rem 1.1rem;
        border-radius: 0 10px 10px 0;
        margin: 0.8rem 0;
    }
    .success-box {
        background: rgba(34, 197, 94, 0.1);
        border-left: 4px solid #22c55e;
        padding: 0.9rem 1.1rem;
        border-radius: 0 10px 10px 0;
    }

    /* --- Progress popup --- */
    .ja-progress-popup {
        position: fixed;
        right: 20px;
        bottom: 20px;
        z-index: 9999;
        width: min(420px, calc(100vw - 32px));
        background: linear-gradient(160deg, #0f172a 0%, #1e293b 100%);
        border: 1px solid rgba(59, 130, 246, 0.45);
        border-radius: 16px;
        box-shadow: 0 18px 40px rgba(0,0,0,0.45), 0 0 0 1px rgba(255,255,255,0.04);
        color: #e2e8f0;
        padding: 16px 18px 14px;
        font-family: system-ui, -apple-system, sans-serif;
        animation: ja-pop-in 0.25s ease-out;
    }
    .ja-progress-popup.minimized {
        width: auto;
        min-width: 200px;
        padding: 10px 14px;
        cursor: pointer;
    }
    @keyframes ja-pop-in {
        from { opacity: 0; transform: translateY(12px) scale(0.96); }
        to { opacity: 1; transform: translateY(0) scale(1); }
    }
    @keyframes ja-spin {
        to { transform: rotate(360deg); }
    }
    @keyframes ja-pulse {
        0%, 100% { opacity: 0.55; }
        50% { opacity: 1; }
    }
    .ja-progress-title {
        font-weight: 700;
        font-size: 0.95rem;
        margin: 0 0 4px 0;
        display: flex;
        align-items: center;
        gap: 8px;
    }
    .ja-spinner {
        width: 16px;
        height: 16px;
        border: 2px solid rgba(148,163,184,0.35);
        border-top-color: #60a5fa;
        border-radius: 50%;
        animation: ja-spin 0.8s linear infinite;
        flex-shrink: 0;
    }
    .ja-spinner.done {
        animation: none;
        border-color: #34d399;
        border-top-color: #34d399;
        background: #34d399;
    }
    .ja-progress-phase {
        font-size: 0.8rem;
        color: #94a3b8;
        margin-bottom: 10px;
    }
    .ja-progress-bar-bg {
        height: 8px;
        background: rgba(148,163,184,0.2);
        border-radius: 999px;
        overflow: hidden;
        margin: 8px 0 6px;
    }
    .ja-progress-bar-fg {
        height: 100%;
        background: linear-gradient(90deg, #3b82f6, #22d3ee);
        border-radius: 999px;
        transition: width 0.35s ease;
        box-shadow: 0 0 12px rgba(59,130,246,0.5);
    }
    .ja-progress-bar-fg.indeterminate {
        width: 40% !important;
        animation: ja-indeterminate 1.2s ease-in-out infinite;
    }
    @keyframes ja-indeterminate {
        0% { transform: translateX(-100%); }
        100% { transform: translateX(280%); }
    }
    .ja-progress-counts {
        display: flex;
        justify-content: space-between;
        font-size: 0.82rem;
        color: #cbd5e1;
        margin-bottom: 8px;
    }
    .ja-progress-detail {
        font-size: 0.78rem;
        color: #94a3b8;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
        max-width: 100%;
        animation: ja-pulse 2s ease-in-out infinite;
    }
    .ja-steps {
        display: flex;
        flex-wrap: wrap;
        gap: 6px;
        margin-top: 10px;
    }
    .ja-step {
        font-size: 0.72rem;
        padding: 3px 8px;
        border-radius: 999px;
        background: rgba(148,163,184,0.12);
        color: #94a3b8;
        border: 1px solid transparent;
    }
    .ja-step.done {
        background: rgba(52,211,153,0.15);
        color: #6ee7b7;
        border-color: rgba(52,211,153,0.35);
    }
    .ja-step.running {
        background: rgba(59,130,246,0.2);
        color: #93c5fd;
        border-color: rgba(59,130,246,0.45);
        animation: ja-pulse 1.5s ease-in-out infinite;
    }
    .ja-step.pending { opacity: 0.55; }
    .ja-step.error {
        background: rgba(248,113,113,0.15);
        color: #fca5a5;
    }
</style>
"""


def setup_page() -> None:
    """Инициализация страницы Streamlit и CSS."""
    import streamlit as st

    st.set_page_config(
        page_title="Карьерный ассистент",
        page_icon="🤖",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    st.markdown(CUSTOM_CSS, unsafe_allow_html=True)
