"""
Модели результата оценки соответствия.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class SuitabilityResult:
    """Структурированный ответ модели-оценщика."""

    suitable: bool
    match_score: float  # 0–100
    reasons: list[str] = field(default_factory=list)
    missing_skills: list[str] = field(default_factory=list)
    summary: str = ""
    raw_response: str = ""
    # Прозрачный разбор требований (не обязательно в JSON LLM)
    requirements_breakdown: str = ""

    @property
    def passes_threshold(self) -> bool:
        return self.suitable and self.match_score >= 0

    def to_analysis_text(self) -> str:
        """Человекочитаемый блок для analysis_result в БД."""
        lines = [
            f"### Оценка соответствия: {self.match_score:.0f}%",
            f"**Подходит:** {'да' if self.suitable else 'нет'}",
            "",
            "**Почему:**",
        ]
        for r in self.reasons:
            lines.append(f"- {r}")
        if self.missing_skills:
            lines.append("")
            lines.append("**Не хватает (обязательное, не найдено в профиле):**")
            for s in self.missing_skills:
                lines.append(f"- {s}")
        if self.requirements_breakdown:
            lines.append("")
            lines.append(self.requirements_breakdown)
        if self.summary:
            lines.append("")
            lines.append(f"**Кратко:** {self.summary}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "suitable": self.suitable,
            "match_score": self.match_score,
            "reasons": self.reasons,
            "missing_skills": self.missing_skills,
            "summary": self.summary,
        }
