"""
Работа с локальной моделью через Ollama API.
- stream_ollama_response: UI (стриминг)
- generate_cover_letter: пакетный режим (один ответ целиком)
"""

from __future__ import annotations

import json as json_lib
import re
import time
from typing import Generator, Optional

import requests

from job_assistant.config import OLLAMA_URL, OLLAMA_MODEL, OLLAMA_OPTIONS, CONTACTS_BLOCK
from job_assistant.llm.prompts import build_cover_letter_prompt
from job_assistant.utils.logging import add_log

# Обратная совместимость: старое имя
build_prompt = build_cover_letter_prompt


def stream_ollama_response(prompt: str) -> Generator[str, None, None]:
    """
    Стримить ответ от Ollama.
    Yield'ит токены по мере поступления.
    В конце добавляет блок контактов.
    """
    add_log("INFO", f"Отправка запроса к Ollama API ({OLLAMA_MODEL})...")
    start_time = time.time()

    try:
        response = requests.post(
            OLLAMA_URL,
            json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": True,
                "options": OLLAMA_OPTIONS,
            },
            stream=True,
            timeout=300,
        )

        if response.status_code != 200:
            add_log("ERROR", f"Ollama API вернула код ошибки: {response.status_code}")
            yield f"❌ Ошибка Ollama API: {response.status_code}"
            return

        for line in response.iter_lines():
            if line:
                chunk_data = json_lib.loads(line.decode("utf-8"))
                token = chunk_data.get("response", "")
                yield token

        yield CONTACTS_BLOCK

        elapsed = round(time.time() - start_time, 2)
        add_log("SUCCESS", f"Генерация ответа успешно завершена за {elapsed} сек.")

    except requests.exceptions.ConnectionError:
        add_log(
            "ERROR",
            "Критическая ошибка: Не удалось подключиться к Ollama по адресу localhost:11434.",
        )
        yield "❌ Не удалось подключиться к Ollama. Проверьте, запущено ли приложение локально."
    except Exception as e:
        add_log("ERROR", f"Ошибка при генерации: {e}")
        yield f"❌ Ошибка генерации: {e}"


def generate_cover_letter(
    user_profile: str,
    job_description: str,
    *,
    vacancy_title: str = "",
    company: str = "",
    match_score: Optional[float] = None,
    append_contacts: bool = True,
) -> str:
    """
    Синхронная генерация письма (для батчей, без стриминга).
    Возвращает полный текст ответа модели (+ контакты).
    """
    prompt = build_cover_letter_prompt(
        user_profile,
        job_description,
        vacancy_title=vacancy_title,
        company=company,
        match_score=match_score,
    )
    add_log(
        "INFO",
        f"Batch cover letter: «{(vacancy_title or job_description[:40])}…» → {OLLAMA_MODEL}",
    )
    start = time.time()

    try:
        resp = requests.post(
            OLLAMA_URL,
            json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": OLLAMA_OPTIONS,
            },
            timeout=300,
        )
        if resp.status_code != 200:
            add_log("ERROR", f"Cover letter HTTP {resp.status_code}")
            return f"❌ Ошибка Ollama API: {resp.status_code}"

        text = (resp.json().get("response") or "").strip()
        if append_contacts:
            text = text + "\n" + CONTACTS_BLOCK

        elapsed = round(time.time() - start, 2)
        add_log("SUCCESS", f"Cover letter готово за {elapsed}s ({len(text)} символов)")
        return text

    except requests.exceptions.ConnectionError:
        add_log("ERROR", "Cover letter: Ollama недоступна")
        return "❌ Не удалось подключиться к Ollama."
    except Exception as e:
        add_log("ERROR", f"Cover letter error: {e}")
        return f"❌ Ошибка генерации: {e}"


def extract_letter_body(full_response: str) -> str:
    """Выделить блок письма из полного ответа (если есть заголовок)."""
    m = re.search(
        r"###\s*✉[️]?\s*Сопроводительное письмо\s*(.+)",
        full_response,
        re.DOTALL | re.IGNORECASE,
    )
    if m:
        return m.group(1).strip()
    return full_response.strip()
