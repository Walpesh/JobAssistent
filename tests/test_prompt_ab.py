"""
7.3 A/B тестирование формулировок промпта оценки соответствия.

Запуск с реальной Ollama (опционально):
  RUN_LIVE_LLM=1 pytest tests/test_prompt_ab.py -v

Без Ollama проверяются только структура промптов и парсер JSON.
"""

from __future__ import annotations

import os

import pytest

from job_assistant.analyzer.llm_judge import _extract_json, _parse_result
from job_assistant.analyzer.prompts import SYSTEM_RULES, build_suitability_prompt

# --- Варианты system-промпта для A/B ---

PROMPT_A = SYSTEM_RULES  # текущий боевой

PROMPT_B = """
Ты — строгий независимый рекрутер. Ты НЕ работаешь на кандидата и НЕ заинтересован в его найме.
Оценивай профиль относительно вакансии максимально жёстко.

Правила:
1. Только явные факты из профиля. Нет факта = навыка нет.
2. Нельзя «дотягивать» смежный опыт (Unity ≠ gamedev-production на AAA).
3. Soft-skills не закрывают hard-skills.
4. suitable=true только при match_score >= 65 и закрытых ключевых hard-skills.
5. Ответ — только JSON:
{"suitable": bool, "match_score": 0-100, "reasons": [], "missing_skills": [], "summary": ""}
""".strip()

PROMPT_C = """
Role: adversarial hiring evaluator. Be skeptical.
Score hard skills 50%, relevant experience 30%, formal fit 20%.
If critical requirement missing → match_score <= 40 and suitable=false.
Never invent experience. Output JSON only:
{"suitable": bool, "match_score": 0-100, "reasons": [], "missing_skills": [], "summary": ""}
""".strip()

SAMPLE_PROFILE = """
Производственная практика в ДЮЦ Меридиан: замена преподавателей, занятия по Unity и Blender.
Pet-проекты на Python: Telegram-боты, парсинг, Streamlit.
Знание Git, Linux, основы FastAPI.
"""

SAMPLE_JOB_STRONG = """
Python-разработчик (Junior).
Требования: Python, Git, Linux, опыт pet-проектов или стажировки.
Будет плюсом: FastAPI, Streamlit.
"""

SAMPLE_JOB_WEAK = """
Senior Kubernetes Platform Engineer.
Требования: 5+ лет production K8s, Terraform, AWS EKS, on-call.
Обязателен опыт управления командой 5+.
"""


def _build_with_system(system: str, profile: str, title: str, desc: str) -> str:
    return f"""{system}

<candidate_profile>
{profile}
</candidate_profile>

<vacancy>
Название: {title}
Описание:
{desc}
</vacancy>

Оцени соответствие. Ответ — только JSON.
"""


def test_default_prompt_contains_hard_rules():
    p = build_suitability_prompt(SAMPLE_PROFILE, "Python Dev", SAMPLE_JOB_STRONG)
    assert "независим" in p.lower() or "НЕ заинтересован" in p
    assert "JSON" in p
    assert "match_score" in p
    assert SAMPLE_PROFILE[:20] in p


def test_ab_variants_differ():
    assert PROMPT_A != PROMPT_B != PROMPT_C
    for name, sys in [("A", PROMPT_A), ("B", PROMPT_B), ("C", PROMPT_C)]:
        full = _build_with_system(sys, SAMPLE_PROFILE, "Job", SAMPLE_JOB_STRONG)
        assert "candidate_profile" in full
        assert len(full) > 200, name


def test_json_parser_robust():
    samples = [
        '{"suitable": false, "match_score": 25, "reasons": ["нет K8s"], "missing_skills": ["Kubernetes"], "summary": "не подходит"}',
        '```json\n{"suitable": true, "match_score": 70, "reasons": ["Python"], "missing_skills": [], "summary": "ok"}\n```',
    ]
    for s in samples:
        data, status = _extract_json(s)
        assert data is not None, status
        r = _parse_result(data, s)
        assert 0 <= r.match_score <= 100


@pytest.mark.skipif(
    os.environ.get("RUN_LIVE_LLM") != "1",
    reason="Нужна Ollama: RUN_LIVE_LLM=1",
)
def test_live_ab_no_sycophancy():
    """
    7.4-ish: на «чужой» вакансии все варианты должны дать низкий score.
    """
    import requests
    from job_assistant.config import OLLAMA_URL, OLLAMA_MODEL

    scores = {}
    for name, sys in [("A", PROMPT_A), ("B", PROMPT_B), ("C", PROMPT_C)]:
        prompt = _build_with_system(
            sys, SAMPLE_PROFILE, "K8s Platform Lead", SAMPLE_JOB_WEAK
        )
        resp = requests.post(
            OLLAMA_URL,
            json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "format": "json",
                "options": {"temperature": 0.15, "num_predict": 500},
            },
            timeout=180,
        )
        assert resp.status_code == 200, name
        data, _st = _extract_json(resp.json().get("response") or "")
        assert data is not None, name
        result = _parse_result(data, "")
        scores[name] = result.match_score
        # не потакать: senior K8s при профиле Unity/junior Python
        assert result.match_score < 50, f"{name} слишком высокий score={result.match_score}"
        assert result.suitable is False

    print("A/B scores on weak job:", scores)
