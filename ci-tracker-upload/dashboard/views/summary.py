"""Resumen: hoy, streaks, objetivos y totales por idioma."""

import pandas as pd
import streamlit as st

import api_client as api
from ui import EFFECTIVE_DISCLAIMER, fmt_coverage, fmt_effective, fmt_hm, label
from views.goals import baseline_note, goal_label, goal_value


def render(summary: dict) -> None:
    streaks = api.get_streaks()
    goals = api.get_goals()
    names = {lang["language"]: lang["name"] for lang in summary["languages"]}

    _render_today(summary, streaks[0])
    if goals:
        _render_goals(goals, names)
    _render_languages(summary, {s["language"]: s for s in streaks[1:]})


def _render_today(summary: dict, overall_streak: dict) -> None:
    st.subheader("Hoy")
    today = [lang for lang in summary["languages"] if lang["today"]["content_seconds"] > 0]
    columns = st.columns(len(today) + 1)
    for col, lang in zip(columns, today):
        col.metric(label(lang["language"], lang["name"]), fmt_hm(lang["today"]["content_seconds"]))

    pending = "" if overall_streak["today_counts"] else " · falta hoy"
    columns[-1].metric(
        "Streak actual",
        f"{overall_streak['current_days']} días",
        help=f"Récord: {overall_streak['longest_days']} días. Un día cuenta con al menos "
        f"{overall_streak['threshold_seconds'] / 60:g} min{pending}.",
    )
    st.caption(f"Récord: {overall_streak['longest_days']} días{pending}")
    if not today:
        st.caption("Todavía no hay tiempo registrado hoy.")


def _render_goals(goals: list[dict], names: dict[str, str]) -> None:
    st.subheader("Objetivos")
    for goal in goals:
        current, target = goal_value(goal, goal["current_seconds"]), goal_value(goal, goal["target_seconds"])
        text = f"{goal_label(goal, names)}: {current} / {target} ({goal['progress']:.0%})"
        if goal["met"]:
            text += " ✓"
        text += baseline_note(goal)
        st.progress(min(goal["progress"], 1.0), text=text)


def _render_languages(summary: dict, streaks: dict[str, dict]) -> None:
    st.subheader("Por idioma")
    rows = [
        {
            "Idioma": label(lang["language"], lang["name"]),
            "Hoy": fmt_hm(lang["today"]["content_seconds"]),
            "7 días": fmt_hm(lang["last_7_days"]["content_seconds"]),
            "30 días": fmt_hm(lang["last_30_days"]["content_seconds"]),
            "Este año": fmt_hm(lang["this_year"]["content_seconds"]),
            "Total": fmt_hm(lang["all_time"]["content_seconds"]),
            "Tiempo real (total)": fmt_hm(lang["all_time"]["wall_clock_seconds"]),
            "CI efectivo* (total)": fmt_effective(lang["all_time"]),
            "Cobertura*": fmt_coverage(lang["all_time"]),
            "Streak": f"{streaks.get(lang['language'], {}).get('current_days', 0)} d",
        }
        for lang in summary["languages"]
    ]
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

    total = summary["total"]["all_time"]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Contenido consumido (total)", fmt_hm(total["content_seconds"]))
    c2.metric("Tiempo real (total)", fmt_hm(total["wall_clock_seconds"]))
    c3.metric(
        "CI efectivo* (estimado)",
        fmt_effective(total),
        help=f"Calculado sobre el {fmt_coverage(total)} del contenido visto (el que tiene puntaje).",
    )
    c4.metric("Videos vistos", summary["videos_watched"])
    st.caption(
        "**Contenido** = segundos de video reproducidos (sin repetir el mismo tramo en el mismo día). "
        "**Tiempo real** = reloj de pared equivalente según la velocidad de reproducción."
    )
    st.caption("\\* " + EFFECTIVE_DISCLAIMER)
