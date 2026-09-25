"""
Промпт оценки: факты профиля vs требования вакансии (AND/OR, must/nice).
"""

SYSTEM_RULES = """
Ты — независимый HR-аналитик. Ты НЕ заинтересован в найме кандидата.
Твоя задача — сопоставить КОНКРЕТНЫЕ требования вакансии с ФАКТАМИ из профиля.

ПРАВИЛА (обязательны):
1. Опирайся ТОЛЬКО на текст <candidate_profile>. Ничего не выдумывай.
2. ЗАПРЕЩЕНО писать, что навыка нет, если он ЯВНО есть в профиле (в любой форме).
3. Синонимы и эквиваленты = тот же навык:
   Python ≡ Django/Flask; C# ≡ .NET/Unity; веб-дизайн ≡ UI/UX/Figma/Photoshop;
   SQL ≡ PostgreSQL/MySQL/SQLite; техподдержка ≡ IT Support/Helpdesk.
4. Логика OR: «Java OR JavaScript OR Python OR C#» выполнена, если в профиле
   подтверждён ХОТЯ БЫ ОДИН вариант (например, Python или C#).
5. Логика AND: несколько обязательных пунктов должны закрываться по отдельности.
6. Различай обязательные (Requirements / must-have) и желательные (Nice to have):
   отсутствие nice-to-have не делает кандидата unsuitable само по себе.
7. Блок <precheck> — справочный разбор требований. Не игнорируй факты профиля.
   Если precheck = MATCH по требованию — нельзя ставить его в missing_skills.
8. Ответ — ТОЛЬКО валидный JSON без markdown и текста вне JSON.

Формат:
{
  "suitable": true/false,
  "match_score": 0-100,
  "reasons": ["кратко по ключевым требованиям"],
  "missing_skills": ["только реально отсутствующие обязательные"],
  "matched_skills": ["что закрыто профилем"],
  "requirement_notes": [
    {"requirement": "...", "status": "MATCH|PARTIAL|MISSING|UNKNOWN", "note": "..."}
  ],
  "summary": "1-2 предложения"
}

match_score:
- 80-100: обязательные hard-requirements почти все MATCH
- 65-79: обязательные в основном закрыты, мелкие пробелы
- 40-64: частичное покрытие обязательных
- 0-39: ключевые обязательные MISSING

suitable=true только если match_score >= 65 и критичные must-have не MISSING
(с учётом OR и синонимов).
""".strip()


def build_suitability_prompt(
    profile: str,
    vacancy_title: str,
    vacancy_description: str,
    company: str = "",
    formula_hint: str = "",
    requirements_hint: str = "",
) -> str:
    company_line = f"Компания: {company}\n" if company else ""
    precheck = ""
    if requirements_hint or formula_hint:
        precheck = f"""
<precheck>
Разбор требований (справка, не заменяет чтение профиля):
{requirements_hint or formula_hint}
Если status=MATCH — этот пункт НЕ включай в missing_skills.
Не утверждай отсутствие навыка, который есть в <candidate_profile>.
</precheck>
"""
    return f"""{SYSTEM_RULES}

<candidate_profile>
{profile[:14000]}
</candidate_profile>

<vacancy>
Название: {vacancy_title}
{company_line}Описание:
{vacancy_description[:9000]}
</vacancy>
{precheck}
Сопоставь требования вакансии с фактами профиля. Ответ — только JSON.
"""
