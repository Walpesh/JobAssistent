"""
Системное логирование:
  - session_state Streamlit (UI)
  - один файл logs/session_*.txt на браузерную сессию (не на каждый rerun)
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import streamlit as st

from job_assistant.utils.secrets import redact_pii

LOG_DIR = Path(os.environ.get("JOBASSISTANT_LOG_DIR", "logs"))

_session_log_path: Optional[Path] = None
_live_callbacks: list[Callable[[dict], None]] = []


def get_session_log_path() -> Optional[Path]:
    try:
        path = st.session_state.get("log_file_path")
        if path:
            return Path(path)
    except Exception:
        pass
    return _session_log_path


def register_live_callback(cb: Callable[[dict], None]) -> None:
    if cb not in _live_callbacks:
        _live_callbacks.append(cb)


def clear_live_callbacks() -> None:
    _live_callbacks.clear()


def init_logging() -> None:
    """
    Инициализация логов.
    Файл сессии создаётся ОДИН раз на session_state (не при каждом Streamlit rerun).
    """
    global _session_log_path

    if "system_logs" not in st.session_state:
        st.session_state["system_logs"] = []

    if st.session_state.get("log_file_initialized"):
        existing = st.session_state.get("log_file_path")
        if existing:
            _session_log_path = Path(existing)
        return

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    _session_log_path = LOG_DIR / f"session_{stamp}.txt"
    st.session_state["log_file_path"] = str(_session_log_path)
    st.session_state["log_file_initialized"] = True

    header = (
        f"{'=' * 72}\n"
        f"JobAssistant session log\n"
        f"Started: {datetime.now().isoformat(timespec='seconds')}\n"
        f"File: {_session_log_path}\n"
        f"{'=' * 72}\n"
    )
    try:
        _session_log_path.write_text(header, encoding="utf-8")
    except OSError:
        _session_log_path = None
        st.session_state["log_file_path"] = None

    add_log("INFO", f"Приложение запущено. Файл лога: {_session_log_path}")


def add_log(level: str, message: str) -> None:
    """Запись в UI-лог и в .txt файл сессии. level: INFO|SUCCESS|WARNING|ERROR"""
    safe_message = redact_pii(str(message))
    now = datetime.now()
    time_short = now.strftime("%H:%M:%S")
    time_full = now.strftime("%Y-%m-%d %H:%M:%S")
    entry = {
        "time": time_short,
        "level": level,
        "message": safe_message,
        "ts": time_full,
    }

    try:
        if "system_logs" not in st.session_state:
            st.session_state["system_logs"] = []
        st.session_state["system_logs"].insert(0, entry)
        if len(st.session_state["system_logs"]) > 80:
            st.session_state["system_logs"].pop()
    except Exception:
        pass

    path = get_session_log_path()
    if path:
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(f"[{time_full}] [{level:7}] {safe_message}\n")
        except OSError:
            pass

    for cb in list(_live_callbacks):
        try:
            cb(entry)
        except Exception:
            pass
