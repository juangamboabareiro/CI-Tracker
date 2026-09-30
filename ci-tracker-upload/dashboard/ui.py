"""Constantes, formato y estilo compartidos por todas las vistas del dashboard."""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

UNASSIGNED = "unassigned"

FLAGS = {
    "fr": "🇫🇷", "ru": "🇷🇺", "it": "🇮🇹", "de": "🇩🇪", "en": "🇬🇧", "es": "🇪🇸",
    "pt": "🇵🇹", "ja": "🇯🇵", "zh": "🇨🇳", "ko": "🇰🇷", "nl": "🇳🇱", "ar": "🇸🇦",
    UNASSIGNED: "❔",
}

# Paleta categórica validada (colorblind-safe), en orden fijo. El color sigue al IDIOMA,
# no a su posición: filtrar nunca repinta. Idiomas fuera de las 8 posiciones van en gris.
LANGUAGE_COLORS = {
    "fr": "#2a78d6", "ru": "#eb6834", "it": "#1baf7a", "de": "#eda100",
    "en": "#e87ba4", "es": "#008300", "pt": "#4a3aa7", "ja": "#e34948",
}
OTHER_COLOR = "#898781"
UNASSIGNED_COLOR = "#c3c2b7"
SERIES_COLOR = "#2a78d6"  # gráficos de una sola serie
# Secuencial de un solo tono (claro -> oscuro); el cero es un gris neutro.
HEATMAP_SCALE = [[0.0, "#f0efec"], [0.0001, "#cde2fb"], [0.35, "#86b6ef"], [0.7, "#2a78d6"], [1.0, "#0d366b"]]

SUBTITLE_LABELS = {
    "none": "Sin subtítulos",
    "target_language": "En el idioma del video",
    "native_language": "En mi idioma nativo",
    "other_language": "En otro idioma",
    "unknown": "No detectado",  # como override manual significa "automático"
}
COMPREHENSIBILITY_LABELS = {
    "90-100": "90–100 %", "80-90": "80–90 %", "70-80": "70–80 %", "<70": "< 70 %", "unrated": "Sin puntuar",
}
CONTENT_TYPE_LABELS = {
    "conversation": "Conversación", "podcast": "Podcast", "news": "Noticias", "vlog": "Vlog",
    "documentary": "Documental", "gaming": "Gaming", "education": "Educación", "music": "Música",
    "movie": "Película", "series": "Serie", "other": "Otro", "unclassified": "Sin clasificar",
}
LANGUAGE_SOURCE_LABELS = {
    "manual": "manual",
    "youtube": "metadata de YouTube",
    "captions": "subtítulos automáticos (audio)",
    "channel": "heredado del canal",
    "text": "detectado por el texto",
}

EFFECTIVE_DISCLAIMER = (
    "**CI efectivo (estimado)** = contenido visto × tu comprensibilidad. Es una métrica personal "
    "y aproximada, no una medida científica de adquisición. Sólo cuenta el tiempo con puntaje "
    "(**cobertura**); el resto no suma."
)


# ---------- Formato ----------

def fmt_hm(seconds: float) -> str:
    minutes = int(round(seconds / 60))
    return f"{minutes // 60}h {minutes % 60:02d}m"


def fmt_hours(seconds: float) -> str:
    return f"{seconds / 3600:.1f} h"


def fmt_clock(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    s = int(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def fmt_effective(period: dict) -> str:
    """CI efectivo de un período; '—' si no hay nada puntuado (no es lo mismo que 0)."""
    return fmt_hm(period["effective_ci_seconds"]) if period["rated_content_seconds"] > 0 else "—"


def fmt_coverage(period: dict) -> str:
    content = period["content_seconds"]
    return f"{period['rated_content_seconds'] / content:.0%}" if content > 0 else "—"


def label(code: str | None, name: str | None = None) -> str:
    code = code or UNASSIGNED
    return f"{FLAGS.get(code, '🌐')} {name or code}"


def language_color(code: str) -> str:
    if code == UNASSIGNED:
        return UNASSIGNED_COLOR
    return LANGUAGE_COLORS.get(code, OTHER_COLOR)


def to_local(value: str | None, tz: str) -> pd.Timestamp | None:
    """La API devuelve UTC sin zona: lo pasamos a la zona configurada."""
    return pd.to_datetime(value).tz_localize("UTC").tz_convert(tz) if value else None


# ---------- Componentes ----------

def style_figure(fig: go.Figure, height: int = 320) -> go.Figure:
    """Estilo común: grilla y ejes discretos, leyenda arriba, sin márgenes de más."""
    fig.update_layout(
        height=height,
        margin=dict(t=10, b=10, l=10, r=10),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, title_text=""),
        bargap=0.25,
        hoverlabel=dict(bgcolor="white"),
    )
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(gridcolor="#e1e0d9", zeroline=False)
    return fig


def breakdown_table(stats: list[dict], key: str, labels: dict[str, str]) -> None:
    """Tabla 30 días / total / % para desgloses con períodos (subtítulos, comprensibilidad...)."""
    total = sum(s["all_time"]["content_seconds"] for s in stats)
    if total == 0:
        st.caption("Sin datos todavía.")
        return
    rows = [
        {
            "": labels.get(s[key], s[key]),
            "30 días": fmt_hm(s["last_30_days"]["content_seconds"]),
            "Total": fmt_hm(s["all_time"]["content_seconds"]),
            "%": f"{s['all_time']['content_seconds'] / total:.0%}",
        }
        for s in stats
        if s["all_time"]["content_seconds"] > 0
    ]
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


def language_selector(summary: dict, key: str, label_text: str = "Idioma") -> str | None:
    names = {lang["language"]: lang["name"] for lang in summary["languages"]}
    return st.selectbox(
        label_text, [None, *names], format_func=lambda c: "Todos" if c is None else label(c, names.get(c)), key=key
    )
