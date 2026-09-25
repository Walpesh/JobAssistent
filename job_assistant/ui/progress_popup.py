"""
Всплывающее окно прогресса длительных операций.

- Показывает этап, сделано/всего, анимацию
- Можно свернуть в компактный чип и снова открыть
- Состояние в st.session_state["ja_progress"]
"""

from __future__ import annotations

from typing import Any, Optional

import streamlit as st

_STATE_KEY = "ja_progress"


def _default_state() -> dict[str, Any]:
    return {
        "visible": False,
        "minimized": False,
        "title": "",
        "phase": "",
        "done": 0,
        "total": 0,
        "detail": "",
        "status": "idle",  # idle | running | done | error | stopped
        "steps": [],  # [{id, label, status}]
    }


def get_progress_state() -> dict[str, Any]:
    if _STATE_KEY not in st.session_state:
        st.session_state[_STATE_KEY] = _default_state()
    return st.session_state[_STATE_KEY]


def progress_start(
    title: str,
    *,
    total: int = 0,
    steps: Optional[list[dict[str, str]]] = None,
    phase: str = "",
) -> None:
    st.session_state[_STATE_KEY] = {
        "visible": True,
        "minimized": False,
        "title": title,
        "phase": phase or title,
        "done": 0,
        "total": max(0, int(total)),
        "detail": "Запуск…",
        "status": "running",
        "steps": list(steps or []),
    }


def progress_update(
    *,
    done: Optional[int] = None,
    total: Optional[int] = None,
    phase: Optional[str] = None,
    detail: Optional[str] = None,
    step_id: Optional[str] = None,
    step_status: Optional[str] = None,
    status: Optional[str] = None,
) -> None:
    s = get_progress_state()
    if not s.get("visible"):
        s["visible"] = True
        s["status"] = "running"
    if done is not None:
        s["done"] = int(done)
    if total is not None:
        s["total"] = max(0, int(total))
    if phase is not None:
        s["phase"] = phase
    if detail is not None:
        s["detail"] = detail
    if status is not None:
        s["status"] = status
    if step_id and step_status:
        for step in s.get("steps") or []:
            if step.get("id") == step_id:
                step["status"] = step_status
                break
    st.session_state[_STATE_KEY] = s


def progress_finish(
    *,
    detail: str = "Готово",
    status: str = "done",
) -> None:
    s = get_progress_state()
    s["status"] = status
    s["detail"] = detail
    if s.get("total") and status == "done":
        s["done"] = s["total"]
    for step in s.get("steps") or []:
        if step.get("status") == "running":
            step["status"] = "done" if status == "done" else step["status"]
    s["visible"] = True
    st.session_state[_STATE_KEY] = s


def progress_hide() -> None:
    s = get_progress_state()
    s["visible"] = False
    s["status"] = "idle"
    st.session_state[_STATE_KEY] = s


def _pct(done: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return max(0.0, min(100.0, 100.0 * done / total))


def _build_html(state: dict[str, Any]) -> str:
    title = state.get("title") or "Операция"
    phase = state.get("phase") or ""
    done = int(state.get("done") or 0)
    total = int(state.get("total") or 0)
    detail = state.get("detail") or ""
    status = state.get("status") or "running"
    minimized = bool(state.get("minimized"))

    pct = _pct(done, total)
    spinner_cls = "ja-spinner done" if status in ("done", "stopped") else "ja-spinner"
    bar_cls = "ja-progress-bar-fg"
    if total <= 0 and status == "running":
        bar_cls += " indeterminate"
        width = 40
    else:
        width = pct

    counts = (
        f"<span>Сделано: <b>{done}</b>"
        + (f" / <b>{total}</b>" if total > 0 else "")
        + "</span>"
        + (f"<span>{pct:.0f}%</span>" if total > 0 else "<span>…</span>")
    )

    steps_html = ""
    steps = state.get("steps") or []
    if steps and not minimized:
        chips = []
        for step in steps:
            st_status = step.get("status") or "pending"
            label = step.get("label") or step.get("id") or ""
            chips.append(f'<span class="ja-step {st_status}">{label}</span>')
        steps_html = f'<div class="ja-steps">{"".join(chips)}</div>'

    if minimized:
        return f"""
        <div class="ja-progress-popup minimized" title="Разверните окно прогресса ниже">
            <div class="ja-progress-title">
                <div class="{spinner_cls}"></div>
                <span>{title}</span>
            </div>
            <div class="ja-progress-counts" style="margin:4px 0 0 0">{counts}</div>
        </div>
        """

    return f"""
    <div class="ja-progress-popup">
        <div class="ja-progress-title">
            <div class="{spinner_cls}"></div>
            <span>{title}</span>
        </div>
        <div class="ja-progress-phase">{phase}</div>
        <div class="ja-progress-counts">{counts}</div>
        <div class="ja-progress-bar-bg">
            <div class="{bar_cls}" style="width:{width:.1f}%"></div>
        </div>
        <div class="ja-progress-detail">{detail}</div>
        {steps_html}
    </div>
    """


def render_progress_popup() -> None:
    """
    Отрисовать pop-up + кнопки свернуть/развернуть/закрыть.
    Вызывать на каждом rerun (например из main_view).
    """
    state = get_progress_state()
    if not state.get("visible"):
        return

    # Кнопки управления над «невидимой» зоной — в сайдбаре-подобном fixed слое
    # Streamlit buttons must live in normal flow; place a compact control row at top.
    c1, c2, c3, c4 = st.columns([2.2, 1, 1, 1])
    with c1:
        st.caption("📡 Прогресс задачи")
    with c2:
        if not state.get("minimized"):
            if st.button("⬇️ Свернуть", key="ja_prog_min", use_container_width=True):
                state["minimized"] = True
                st.session_state[_STATE_KEY] = state
                st.rerun()
        else:
            if st.button("⬆️ Открыть", key="ja_prog_max", use_container_width=True):
                state["minimized"] = False
                st.session_state[_STATE_KEY] = state
                st.rerun()
    with c3:
        if state.get("status") in ("done", "error", "stopped"):
            if st.button("✕ Закрыть", key="ja_prog_close", use_container_width=True):
                progress_hide()
                st.rerun()
    with c4:
        pass

    st.markdown(_build_html(state), unsafe_allow_html=True)


class ProgressSlot:
    """
    Обновляемый HTML-слот во время длинной операции (без полного rerun).
    """

    def __init__(self, placeholder=None):
        self._ph = placeholder or st.empty()

    def refresh(self) -> None:
        state = get_progress_state()
        if not state.get("visible"):
            self._ph.empty()
            return
        self._ph.markdown(_build_html(state), unsafe_allow_html=True)

    def start(self, title: str, **kwargs) -> None:
        progress_start(title, **kwargs)
        self.refresh()

    def update(self, **kwargs) -> None:
        progress_update(**kwargs)
        self.refresh()

    def finish(self, **kwargs) -> None:
        progress_finish(**kwargs)
        self.refresh()


def apply_progress_event(ev: dict, slot: Optional[ProgressSlot] = None) -> None:
    """Унифицированная обработка событий fetch/analyze/letters → popup."""
    et = ev.get("type")
    phase = str(ev.get("phase") or "")
    if et == "start":
        progress_update(
            done=0,
            total=int(ev.get("total") or 0),
            phase=phase or "running",
            detail=str(ev.get("detail") or "Старт…"),
            status="running",
        )
    elif et in ("item", "page", "saved"):
        progress_update(
            done=int(ev.get("done") if ev.get("done") is not None else ev.get("saved_total") or 0),
            total=int(ev.get("total") or 0) or None,
            phase=phase or None,
            detail=str(ev.get("detail") or ev.get("title") or ""),
            status="running",
        )
    elif et == "done":
        progress_finish(
            detail=str(ev.get("detail") or "Готово"),
            status="stopped" if ev.get("stopped") else "done",
        )
        if ev.get("done") is not None or ev.get("total") is not None:
            progress_update(
                done=int(ev.get("done") or 0),
                total=int(ev.get("total") or 0) or None,
            )
    elif et == "error":
        progress_update(
            detail=str(ev.get("message") or ev.get("detail") or "Ошибка"),
            status="error",
        )
    if slot is not None:
        slot.refresh()
