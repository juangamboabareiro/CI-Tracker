"""Objetivos (v0.5): crear, ver progreso y borrar."""

import streamlit as st

import api_client as api
from ui import label

PERIOD_LABELS = {"total": "Total acumulado", "daily": "Por día"}
METRIC_LABELS = {"content": "Contenido visto", "effective_ci": "CI efectivo (estimado)"}


def goal_label(goal: dict, names: dict[str, str]) -> str:
    who = label(goal["language"], names.get(goal["language"])) if goal["language"] else "Todos los idiomas"
    metric = "" if goal["metric"] == "content" else " · CI efectivo"
    return f"{who} · {PERIOD_LABELS[goal['period']].lower()}{metric}"


def goal_value(goal: dict, seconds: float) -> str:
    """Totales en horas; diarios en minutos."""
    return f"{seconds / 3600:.1f} h" if goal["period"] == "total" else f"{seconds / 60:.0f} min"


def render(languages: list[dict]) -> None:
    st.subheader("Objetivos")
    st.caption("Son metas tuyas: no se asume que sean lingüísticamente óptimas.")
    names = {lang["code"]: lang["name"] for lang in languages}

    goals = api.get_goals()
    if not goals:
        st.caption("Todavía no definiste objetivos.")
    for goal in goals:
        _render_goal(goal, names)

    st.markdown("**Nuevo objetivo**")
    _render_form(languages, names)


def _render_goal(goal: dict, names: dict[str, str]) -> None:
    left, right = st.columns([5, 1])
    with left:
        current, target = goal_value(goal, goal["current_seconds"]), goal_value(goal, goal["target_seconds"])
        st.progress(
            min(goal["progress"], 1.0),
            text=f"{goal_label(goal, names)}: {current} / {target} ({goal['progress']:.0%})" + (" ✓" if goal["met"] else ""),
        )
        if goal["period"] == "daily":
            streak = goal["streak"]
            st.caption(
                f"Cumplido {goal['days_met_last_30']} de los últimos 30 días · "
                f"racha {streak['current_days']} días (récord {streak['longest_days']})"
                + ("" if streak["today_counts"] else " · hoy todavía no")
            )
    if right.button("Borrar", key=f"delete-goal-{goal['id']}"):
        api.delete_goal(goal["id"])
        st.rerun()


def _render_form(languages: list[dict], names: dict[str, str]) -> None:
    codes = [lang["code"] for lang in languages]
    with st.form("new-goal", clear_on_submit=True):
        c1, c2, c3, c4 = st.columns(4)
        language = c1.selectbox(
            "Idioma", [None, *codes], format_func=lambda c: "Todos" if c is None else label(c, names[c])
        )
        period = c2.selectbox("Tipo", list(PERIOD_LABELS), format_func=PERIOD_LABELS.get)
        metric = c3.selectbox("Métrica", list(METRIC_LABELS), format_func=METRIC_LABELS.get)
        amount = c4.number_input(
            "Meta (horas si es total, minutos si es por día)", min_value=1.0, value=60.0, step=5.0
        )
        if st.form_submit_button("Crear objetivo"):
            api.create_goal(language, period, metric, amount * (3600 if period == "total" else 60))
            st.rerun()
