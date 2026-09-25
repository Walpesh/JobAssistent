"""
Загрузка секретов и защита персональных данных.

Приоритет:
  1. Streamlit secrets (st.secrets)
  2. переменные окружения
  3. .env (если установлен python-dotenv) — только локально
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from typing import Optional


def _load_dotenv_once() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(dotenv_path=os.path.join(os.getcwd(), ".env"), override=False)
    except ImportError:
        pass


@lru_cache(maxsize=1)
def _env_ready() -> bool:
    _load_dotenv_once()
    return True


def get_secret(key: str, default: str = "") -> str:
    """
    Безопасно получить секрет. Не логировать возвращаемое значение.
    """
    _env_ready()

    # Streamlit secrets
    try:
        import streamlit as st

        if hasattr(st, "secrets") and key in st.secrets:
            val = st.secrets[key]
            return str(val) if val is not None else default
    except Exception:
        pass

    return os.environ.get(key, default) or default


# --- маскирование PII в логах ---

_PHONE_RE = re.compile(r"(?:\+7|8)[\s\-]?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}")
_EMAIL_RE = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
_TG_RE = re.compile(r"https?://t\.me/\S+")
_TOKEN_RE = re.compile(r"(?i)(api[_-]?key|token|secret|password|bearer)\s*[:=]\s*\S+")


def redact_pii(text: str) -> str:
    """Убрать телефоны, email, токены из строки перед логированием."""
    if not text:
        return text
    text = _PHONE_RE.sub("[PHONE]", text)
    text = _EMAIL_RE.sub("[EMAIL]", text)
    text = _TG_RE.sub("[TELEGRAM]", text)
    text = _TOKEN_RE.sub(r"\1=[REDACTED]", text)
    return text
