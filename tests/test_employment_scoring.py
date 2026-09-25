from job_assistant.utils.employment import (
    detect_employment_types,
    is_tk_only,
)
from job_assistant.analyzer.scoring import (
    compute_skill_overlap,
    blend_scores,
    reconcile_missing_skills,
    extract_requirements,
    assess_all_requirements,
    assert_profile_facts_not_denied,
)


PROFILE = """
Умею программировать на HTML, CSS, Python, C#, SQL, SQLite, mySQL, PostreSQL.
Unity, Photoshop, веб-дизайн, Figma. Опыт CRM систем.
"""


def test_detect_tk_only():
    types = detect_employment_types("Оформление: трудовой договор по ТК РФ")
    assert "tk" in types
    assert is_tk_only(types) is True


def test_detect_tk_and_gph_keeps():
    types = detect_employment_types(
        "Возможно оформление по ТК РФ или договор ГПХ / самозанятый"
    )
    assert "tk" in types
    assert "gph" in types or "self" in types
    assert is_tk_only(types) is False


def test_web_design_overlap_from_profile():
    vac = "Ищем web designer / веб-дизайнера в продукт"
    ov = compute_skill_overlap(PROFILE, "Web Designer", vac)
    assert ov["formula_score"] >= 50
    assert ov["required_matched"] >= 1 or any(
        "design" in x.lower() or "дизайн" in x.lower() or "figma" in x.lower()
        for x in ov.get("matched_labels", [])
    )


def test_or_languages_python_or_csharp_match():
    """Java OR JavaScript OR Python OR C# — достаточно Python или C# из профиля."""
    vac = """
Requirements:
- Java or JavaScript or Python or C#
- SQL
"""
    assessments = assess_all_requirements(PROFILE, "Backend Dev", vac)
    labels_status = {a.requirement.label().lower(): a.status for a in assessments}
    # OR-список должен быть MATCH
    or_hit = any(
        a.status == "MATCH" and ("python" in a.requirement.label().lower() or "java" in a.requirement.label().lower())
        for a in assessments
    )
    assert or_hit, labels_status
    ov = compute_skill_overlap(PROFILE, "Backend Dev", vac)
    assert ov["formula_score"] >= 50
    assert ov["required_missing"] == 0 or ov["required_matched"] >= 1


def test_cannot_deny_python_in_missing():
    missing = reconcile_missing_skills(
        ["Python", "Kubernetes"],
        covered_labels=["Python"],
        profile=PROFILE,
    )
    assert not any("python" in m.lower() for m in missing)
    assert any("kubernetes" in m.lower() for m in missing)


def test_assert_profile_facts_not_denied():
    reasons, missing = assert_profile_facts_not_denied(
        ["нет опыта Python", "слабый soft-skills"],
        ["Python", "Rust"],
        PROFILE,
    )
    assert not any("python" in r.lower() for r in reasons)
    assert not any("python" in m.lower() for m in missing)


def test_blend_scores_facts_dominant():
    # llm_weight 0.35 → facts 0.65
    s = blend_scores(20, 90, llm_weight=0.35)
    assert s > 60  # ближе к 90, чем к 20


def test_extract_or_list():
    reqs = extract_requirements(
        "Dev",
        "Requirements:\n- Java / JavaScript / Python / C#\nNice to have:\n- Docker\n",
    )
    assert any(len(r.alternatives) >= 2 for r in reqs)


def test_fallback_when_no_sections():
    from job_assistant.analyzer.scoring import (
        extract_requirements_with_status,
        compute_skill_overlap,
        build_requirements_breakdown_text,
    )

    # Сплошной текст без секций Requirements — structured может быть пуст,
    # fallback обязан найти Python/SQL
    title = "Backend engineer"
    desc = (
        "Мы ищем человека. В работе пригодятся python и sql, "
        "иногда docker. Опыт коммерческой разработки приветствуется."
    )
    reqs, status = extract_requirements_with_status(title, desc)
    assert status in ("structured", "fallback")
    assert len(reqs) >= 1

    ov = compute_skill_overlap(PROFILE, title, desc)
    assert ov["extraction_status"] in ("structured", "fallback")
    text = build_requirements_breakdown_text(
        ov["assessments"], extraction_status=ov["extraction_status"]
    )
    assert "Не удалось выделить" not in text
    assert "Разбор требований" in text


def test_empty_vacancy_still_analyzable():
    from job_assistant.analyzer.scoring import compute_skill_overlap, build_requirements_breakdown_text

    ov = compute_skill_overlap(PROFILE, "", "")
    assert ov["extraction_status"] == "empty"
    assert "formula_score" in ov
    text = build_requirements_breakdown_text([], extraction_status="empty")
    assert "Не удалось выделить" not in text
    assert "анализ продолжен" in text.lower() or "полному тексту" in text.lower()


def test_llm_json_extract_markdown():
    from job_assistant.analyzer.llm_judge import _extract_json

    raw = '```json\n{"suitable": true, "match_score": 70, "reasons": ["Python"], "missing_skills": [], "summary": "ok"}\n```'
    data, status = _extract_json(raw)
    assert data is not None
    assert data["match_score"] == 70
    assert status in ("markdown", "ok", "repaired")

    data2, status2 = _extract_json("not json at all")
    assert data2 is None
    assert status2 == "failed"
