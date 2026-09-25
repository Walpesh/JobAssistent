from job_assistant.llm.client import (
    build_prompt,
    stream_ollama_response,
    generate_cover_letter,
    extract_letter_body,
)
from job_assistant.llm.prompts import build_cover_letter_prompt

__all__ = [
    "build_prompt",
    "build_cover_letter_prompt",
    "stream_ollama_response",
    "generate_cover_letter",
    "extract_letter_body",
]
