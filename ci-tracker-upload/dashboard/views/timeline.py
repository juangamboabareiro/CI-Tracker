"""Evolución: horas por día/semana/mes y calendario tipo GitHub."""

from datetime import timedelta

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

import api_client as api
from ui import EFFECTIVE_DISCLAIMER, HEATMAP_SCALE, label, language_color, language_selector, style_figure

METRICS = {"content_seconds": "Contenido visto", "effective_ci_seconds": "CI efectivo (estimado)"}
WEEKDAYS = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
MONTHS = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


def render(summary: dict) -> None:
    st.subheader("Evolución")
    names = {lang["language"]: lang["name"] for lang in summary["languages"]}
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        language = language_selector(summary, key="timeline-language")
    granularity = c2.radio("Agrupar por", ["Día", "Semana", "Mes"], horizontal=True)
    days = c3.selectbox("Rango", [30, 90, 365, 3650], format_func=lambda d: "Todo" if d == 3650 else f"{d} días")
    metric = c4.radio("Mostrar", list(METRICS), format_func=METRICS.get, horizontal=True)

    points = api.get_daily(max(days, 365), language)  # el calendario siempre usa 1 año
    if not points:
        st.caption("Sin datos en el rango.")
        return
    df = pd.DataFrame(points)
    df["date"] = pd.to_datetime(df["date"])

    _render_bars(df[df["date"] > df["date"].max() - pd.Timedelta(days=days)], granularity, metric, names)
    if metric == "effective_ci_seconds":
        st.caption(EFFECTIVE_DISCLAIMER)

    st.subheader("Calendario (último año)")
    _render_heatmap(df, metric)


def _render_bars(df: pd.DataFrame, granularity: str, metric: str, names: dict[str, str]) -> None:
    freq = {"Día": "D", "Semana": "W-MON", "Mes": "MS"}[granularity]
    grouped = (
        df.groupby([pd.Grouper(key="date", freq=freq, label="left", closed="left"), "language"])[metric]
        .sum()
        .reset_index()
    )
    grouped["hours"] = grouped[metric] / 3600
    grouped["label"] = grouped["language"].map(lambda c: label(c, names.get(c)))
    colors = {label(c, names.get(c)): language_color(c) for c in grouped["language"].unique()}
    fig = px.bar(
        grouped,
        x="date",
        y="hours",
        color="label",
        color_discrete_map=colors,
        labels={"hours": "Horas" if metric == "content_seconds" else "Horas de CI efectivo (estimado)", "date": ""},
        hover_data={"hours": ":.2f", "label": True},
    )
    fig.update_traces(marker_line_width=1, marker_line_color="white")  # separa los segmentos apilados
    st.plotly_chart(style_figure(fig), width="stretch")


def _render_heatmap(df: pd.DataFrame, metric: str) -> None:
    """GitHub-style: columnas = semanas, filas = días de la semana, color = horas."""
    by_day = df.groupby("date")[metric].sum() / 3600
    end = by_day.index.max().normalize()
    start = end - timedelta(days=364)
    start -= timedelta(days=start.weekday())  # arrancar en lunes
    days = pd.date_range(start, end, freq="D")
    hours = by_day.reindex(days, fill_value=0.0)

    weeks = sorted({d - timedelta(days=d.weekday()) for d in days})
    week_index = {w: i for i, w in enumerate(weeks)}
    z = [[None] * len(weeks) for _ in range(7)]
    text = [[""] * len(weeks) for _ in range(7)]
    for day, value in hours.items():
        col = week_index[day - timedelta(days=day.weekday())]
        z[day.weekday()][col] = value
        text[day.weekday()][col] = f"{day:%d/%m/%Y}: {value:.1f} h"

    # Etiqueta de mes en la primera semana de cada mes.
    ticks = [(i, MONTHS[w.month - 1]) for i, w in enumerate(weeks) if i == 0 or w.month != weeks[i - 1].month]
    fig = go.Figure(
        go.Heatmap(
            z=z,
            text=text,
            hoverinfo="text",
            colorscale=HEATMAP_SCALE,
            zmin=0,
            xgap=2,
            ygap=2,
            colorbar=dict(title="h", thickness=10),
        )
    )
    fig.update_yaxes(tickvals=list(range(7)), ticktext=WEEKDAYS, autorange="reversed", showgrid=False)
    fig.update_xaxes(tickvals=[i for i, _ in ticks], ticktext=[m for _, m in ticks], showgrid=False)
    style_figure(fig, height=220)
    fig.update_yaxes(showgrid=False)
    st.plotly_chart(fig, width="stretch")
    st.caption("Cada cuadro es un día. Más oscuro = más horas. Gris = sin actividad.")
