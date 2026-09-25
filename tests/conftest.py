"""
Общие фикстуры: mock Streamlit до импорта пакета, временная БД.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class _FakeSession(dict):
    pass


def pytest_configure(config):
    """Подменить streamlit до collection/import модулей приложения."""
    fake = type("FakeSt", (), {})()
    fake.session_state = _FakeSession()
    fake.session_state["system_logs"] = []
    fake.set_page_config = lambda **kwargs: None
    fake.markdown = lambda *a, **k: None
    sys.modules["streamlit"] = fake


@pytest.fixture(autouse=True)
def ensure_log_list():
    import streamlit as st

    if "system_logs" not in st.session_state:
        st.session_state["system_logs"] = []
    yield


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    """Изолированная SQLite на каждый тест."""
    db_path = tmp_path / "test.db"
    import job_assistant.config as cfg
    import job_assistant.db.connection as conn_mod

    monkeypatch.setattr(cfg, "DB_NAME", str(db_path))
    monkeypatch.setattr(conn_mod, "DB_NAME", str(db_path))

    from job_assistant.db import init_db

    init_db()
    yield str(db_path)
