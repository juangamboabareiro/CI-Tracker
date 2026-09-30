"""Análisis (v0.7): exposición por idioma, canales, tipos de contenido, velocidad, subtítulos y comprensibilidad."""

import pandas as pd
import plotly.express as px
import streamlit as st

import api_client as api
from ui import (
    COMPREHENSIBILITY_LABELS,
    CONTENT_TYPE_LABELS,
    SERIES_COLOR,
    SUBTITLE_LABELS,
    breakdown_table,
    fmt_hm,
    fmt_hours,
    label,
    language_color,
    language_selector,
    style_figure,
)

TOP_CHANNELS = 15


def render(summary: dict) -> None:
    st.subheader("Análisis")
    language = language_selector(summary, key="analytics-language", label_text="Filtrar por idioma")

    if language is None:
        _render_exposure(summary)
    _render_content_vs_wall(summary, language)

    left, right = st.columns(2)
    with left:
        st.markdown("**Canales que más horas aportan**")
        _ranked_bars(api.get_channel_stats(language)[:TOP_CHANNELS], names=None)
    with right:
        st.markdown("**Tipo de contenido**")
        _ranked_bars(api.get_content_type_stats(language), names=CONTENT_TYPE_LABELS)
        st.caption("Asigná el tipo en Videos → Detalle; los demás videos del canal lo heredan.")

    st.markdown("**Velocidad de reproducción**")
    _render_speeds(api.get_speed_stats(language))

    left, right = st.columns(2)
    with left:
        st.markdown("**Subtítulos**")
        breakdown_table(api.get_subtitle_stats(language), "subtitle_mode", SUBTITLE_LABELS)
    with right:
        st.markdown("**Comprensibilidad**")
        breakdown_table(api.get_comprehensibility_stats(language), "bucket", COMPREHENSIBILITY_LABELS)
        st.caption("Evaluación subjetiva tuya; los videos sin puntaje propio usan el promedio de su canal.")


def _render_exposure(summary: dict) -> None:
    st.markdown("**Exposición por idioma**")
    total = summary["total"]["all_time"]["content_seconds"]
    if total == 0:
        st.caption("Sin datos todavía.")
        return
    df = pd.DataFrame(
        {
            "idioma": [label(s["language"], s["name"]) for s in summary["languages"]],
            "code": [s["language"] for s in summary["languages"]],
            "share": [s["all_time"]["content_seconds"] / total for s in summary["languages"]],
            "hours": [s["all_time"]["content_seconds"] / 3600 for s in summary["languages"]],
        }
    )
    fig = px.bar(
        df, x="share", y="idioma", orientation="h", color="idioma",
        color_discrete_map={row.idioma: language_color(row.code) for row in df.itertuples()},
        text=df["share"].map(lambda v: f"{v:.0%}"),
        hover_data={"hours": ":.1f", "share": ":.1%", "idioma": False},
        labels={"share": "% del contenido", "idioma": "", "hours": "Horas"},
    )
    fig.update_traces(textposition="outside", cliponaxis=False)
    fig.update_layout(showlegend=False)
    fig.update_xaxes(tickformat=".0%", range=[0, 1.1])
    fig.update_yaxes(autorange="reversed")
    st.plotly_chart(style_figure(fig, height=60 + 40 * len(df)), width="stretch")


def _render_content_vs_wall(summary: dict, language: str | None) -> None:
    if language is None:
        period = summary["total"]["all_time"]
    else:
        period = next((s["all_time"] for s in summary["languages"] if s["language"] == language), None)
    if not period:
        return
    content, wall = period["content_seconds"], period["wall_clock_seconds"]
    c1, c2, c3 = st.columns(3)
    c1.metric("Contenido consumido", fmt_hm(content))
    c2.metric("Tiempo real", fmt_hm(wall))
    c3.metric(
        "Ganado por velocidad",
        fmt_hm(max(content - wall, 0)),
        help="Contenido de más que consumiste respecto del tiempo real, por mirar a más de 1x.",
    )


def _ranked_bars(groups: list[dict], names: dict[str, str] | None) -> None:
    """Barras horizontales de una serie, ordenadas de mayor a menor, con la etiqueta de horas."""
    groups = [g for g in groups if g["all_time"]["content_seconds"] > 0]
    if not groups:
        st.caption("Sin datos todavía.")
        return
    df = pd.DataFrame(
        {
            "name": [(names or {}).get(g["key"], g["name"]) for g in groups],
            "hours": [g["all_time"]["content_seconds"] / 3600 for g in groups],
            "videos": [g["videos"] for g in groups],
        }
    )
    fig = px.bar(
        df, x="hours", y="name", orientation="h",
        text=[fmt_hours(h * 3600) for h in df["hours"]],
        hover_data={"videos": True, "hours": ":.2f"},
        labels={"hours": "Horas", "name": "", "videos": "Videos"},
        color_discrete_sequence=[SERIES_COLOR],
    )
    fig.update_traces(textposition="outside", cliponaxis=False)
    fig.update_yaxes(autorange="reversed")
    fig.update_xaxes(range=[0, df["hours"].max() * 1.25])
    st.plotly_chart(style_figure(fig, height=60 + 32 * len(df)), width="stretch")


def _render_speeds(groups: list[dict]) -> None:
    groups = [g for g in groups if g["all_time"]["content_seconds"] > 0]
    if not groups:
        st.caption("Sin datos todavía.")
        return
    df = pd.DataFrame(
        {
            "speed": [g["key"] for g in groups],
            "hours": [g["all_time"]["content_seconds"] / 3600 for g in groups],
            "wall": [g["all_time"]["wall_clock_seconds"] / 3600 for g in groups],
        }
    )
    fig = px.bar(
        df, x="speed", y="hours", text=[fmt_hours(h * 3600) for h in df["hours"]],
        hover_data={"wall": ":.2f", "hours": ":.2f"},
        labels={"speed": "Velocidad", "hours": "Horas de contenido", "wall": "Horas reales"},
        color_discrete_sequence=[SERIES_COLOR],
    )
    fig.update_traces(textposition="outside", cliponaxis=False)
    fig.update_xaxes(type="category")
    style_figure(fig, height=280).update_layout(bargap=0.6)  # pocas categorías: barras finas
    st.plotly_chart(fig, width="stretch")
