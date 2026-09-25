"""
Парсер вакансий HH.ru и универсальный fallback.
"""

import re
from typing import Optional

import requests
from bs4 import BeautifulSoup

from job_assistant.config import HH_HEADERS
from job_assistant.utils.logging import add_log


def _clean_text(text: str) -> str:
    if not text:
        return ""
    text = text.replace("\xa0", " ").replace("\u202f", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _get_text_by_qa(soup: BeautifulSoup, qa: str) -> Optional[str]:
    el = soup.find(attrs={"data-qa": qa})
    if el:
        return _clean_text(el.get_text(" ", strip=True))
    return None


def _extract_hh_header(soup: BeautifulSoup) -> list[str]:
    lines = []
    for qa in [
        "vacancy-title",
        "vacancy-salary",
        "compensation-frequency-text",
        "work-experience-text",
        "common-employment-text",
        "work-schedule-by-days-text",
        "working-hours-text",
        "work-formats-text",
    ]:
        val = _get_text_by_qa(soup, qa)
        if val:
            lines.append(val)
    return lines


def _extract_hh_description(soup: BeautifulSoup) -> str:
    parts = []
    branded = soup.find(class_="vacancy-branded-description")
    if branded:
        content = branded.find(class_="vacancy-branded-description-content") or branded
        text = _clean_text(content.get_text("\n", strip=True))
        if text:
            parts.append(text)

    desc = soup.find(attrs={"data-qa": "vacancy-description"})
    if desc:
        blocks = []
        for el in desc.find_all(
            ["p", "ul", "ol", "h1", "h2", "h3", "h4", "strong"], recursive=True
        ):
            if el.name in ("ul", "ol"):
                items = [
                    f"* {_clean_text(li.get_text(' ', strip=True))}"
                    for li in el.find_all("li", recursive=False)
                    if li.get_text(strip=True)
                ]
                if items:
                    blocks.append("\n".join(items))
            elif el.name == "p":
                if el.find_parent(["ul", "ol"]):
                    continue
                p_text = _clean_text(el.get_text(" ", strip=True))
                if p_text:
                    blocks.append(p_text)
            elif el.name in ("h1", "h2", "h3", "h4", "strong"):
                if el.find_parent(["ul", "ol", "p"]):
                    continue
                h_text = _clean_text(el.get_text(" ", strip=True))
                if h_text:
                    blocks.append(h_text)
        if not blocks:
            blocks.append(_clean_text(desc.get_text("\n", strip=True)))
        desc_text = "\n\n".join(blocks)
        if desc_text and desc_text not in "\n".join(parts):
            parts.append(desc_text)

    result = "\n\n".join(parts)
    result = re.sub(r"\n?#\s*Контакт.*$", "", result, flags=re.DOTALL | re.IGNORECASE)
    return _clean_text(result)


def parse_hh_vacancy(soup: BeautifulSoup) -> str:
    header_lines = _extract_hh_header(soup)
    description = _extract_hh_description(soup)
    output = []
    if header_lines:
        output.append("\n".join(header_lines))
    if description:
        output.append("---")
        output.append(description)
    return "\n".join(output)


def fetch_job_text(url: str) -> str:
    """
    Загрузить и распарсить текст вакансии по URL.
    Для hh.ru использует специализированный парсер, иначе — универсальный.
    """
    try:
        add_log("INFO", f"Запрос на парсинг URL: {url}")
        response = requests.get(url, headers=HH_HEADERS, timeout=15)
        response.raise_for_status()
        response.encoding = "utf-8"
        soup = BeautifulSoup(response.text, "html.parser")

        if "hh.ru" in url:
            result = parse_hh_vacancy(soup)
            if result.strip():
                add_log("SUCCESS", "Вакансия успешно распарсена через кастомный парсер HH.ru.")
                return result[:10000]

        for s in soup(["script", "style", "nav", "footer", "header", "aside"]):
            s.decompose()
        add_log("SUCCESS", "Вакансия распарсена универсальным методом.")
        return soup.get_text(separator=" ", strip=True)[:6000]
    except Exception as e:
        add_log("ERROR", f"Ошибка парсинга URL: {e}")
        return f"Не удалось загрузить страницу автоматически: {e}"
