"""Dashboard Streamlit (MVP v0.1).

Ejecutar desde la raíz del proyecto:
    streamlit run dashboard/app.py
"""

import pandas as pd
import plotly.express as px
import streamlit as st

import api_client as api

FLAGS = {
    "fr": "🇫🇷", "ru": "🇷🇺", "it": "🇮🇹", "de": "🇩🇪", "en": "🇬🇧", "es": "🇪🇸",
    "pt": "🇵🇹", "ja": "🇯🇵", "zh": "🇨🇳", "ko": "🇰🇷", "nl": "🇳🇱", "ar": "🇸🇦",
    "unassigned": "❔",
}
UNASSIGNED = "unassigned"
# "unknown" como override manual significa "usar la detección automática".
SUBTITLE_LABELS = {
    "none": "Sin subtítulos",
    "target_language": "En el idioma del video",
    "native_language": "En mi idioma nativo",
    "other_language": "En otro idioma",
    "unknown": "No detectado",
}
COMPREHENSIBILITY_LABELS = {
    "90-100": "90–100 %",
    "80-90": "80–90 %",
    "70-80": "70–80 %",
    "<70": "< 70 %",
    "unrated": "Sin puntuar",
}
SCORE_COLUMN = "Comprensión %"


def fmt_hm(seconds: float) -> str:
    minutes = int(round(seconds / 60))
    return f"{minutes // 60}h {minutes % 60:02d}m"


def fmt_clock(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    s = int(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def label(code: str, name: str | None = None) -> str:
    return f"{FLAGS.get(code, '🌐')} {name or code}"


# ---------- Secciones ----------

def render_summary(summary: dict) -> None:
    st.subheader("Hoy")
    today = [lang for lang in summary["languages"] if lang["today"]["content_seconds"] > 0]
    if not today:
        st.caption("Todavía no hay tiempo registrado hoy.")
    for col, lang in zip(st.columns(max(len(today), 1)), today):
        col.metric(label(lang["language"], lang["name"]), fmt_hm(lang["today"]["content_seconds"]))

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
        }
        for lang in summary["languages"]
    ]
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

    total = summary["total"]["all_time"]
    c1, c2, c3 = st.columns(3)
    c1.metric("Contenido consumido (total)", fmt_hm(total["content_seconds"]))
    c2.metric("Tiempo real (total)", fmt_hm(total["wall_clock_seconds"]))
    c3.metric("Videos vistos", summary["videos_watched"])
    st.caption(
        "**Contenido** = segundos de video reproducidos (sin repetir el mismo tramo en el mismo día). "
        "**Tiempo real** = reloj de pared equivalente según la velocidad de reproducción."
    )
    render_breakdowns(summary)


def render_breakdowns(summary: dict) -> None:
    st.subheader("Desgloses")
    names = {lang["language"]: lang["name"] for lang in summary["languages"]}
    language = st.selectbox(
        "Idioma del contenido",
        [None, *names],
        format_func=lambda c: "Todos" if c is None else label(c, names.get(c)),
        key="breakdown-language",
    )
    left, right = st.columns(2)
    with left:
        st.markdown("**Subtítulos**")
        render_breakdown_table(api.get_subtitle_stats(language), "subtitle_mode", SUBTITLE_LABELS)
    with right:
        st.markdown("**Comprensibilidad**")
        render_breakdown_table(api.get_comprehensibility_stats(language), "bucket", COMPREHENSIBILITY_LABELS)
        st.caption(
            "Evaluación subjetiva tuya, no una medida objetiva. Los videos sin puntaje propio "
            "usan el promedio de su canal."
        )


def render_breakdown_table(stats: list[dict], key: str, labels: dict[str, str]) -> None:
    total = sum(s["all_time"]["content_seconds"] for s in stats)
    if total == 0:
        st.caption("Sin datos todavía.")
        return
    rows = [
        {
            "": labels[s[key]],
            "30 días": fmt_hm(s["last_30_days"]["content_seconds"]),
            "Total": fmt_hm(s["all_time"]["content_seconds"]),
            "%": f"{s['all_time']['content_seconds'] / total:.0%}",
        }
        for s in stats
        if s["all_time"]["content_seconds"] > 0
    ]
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


def render_timeline(summary: dict) -> None:
    st.subheader("Evolución")
    languages = [lang["language"] for lang in summary["languages"]]
    names = {lang["language"]: lang["name"] for lang in summary["languages"]}
    c1, c2, c3 = st.columns(3)
    language = c1.selectbox(
        "Idioma", [None, *languages], format_func=lambda c: "Todos" if c is None else label(c, names.get(c))
    )
    granularity = c2.radio("Agrupar por", ["Día", "Semana", "Mes"], horizontal=True)
    days = c3.selectbox("Rango", [30, 90, 365, 3650], format_func=lambda d: "Todo" if d == 3650 else f"{d} días")

    points = api.get_daily(days, language)
    if not points:
        st.caption("Sin datos en el rango.")
        return
    df = pd.DataFrame(points)
    df["date"] = pd.to_datetime(df["date"])
    freq = {"Día": "D", "Semana": "W-MON", "Mes": "MS"}[granularity]
    grouped = (
        df.groupby([pd.Grouper(key="date", freq=freq, label="left", closed="left"), "language"])["content_seconds"]
        .sum()
        .reset_index()
    )
    grouped["hours"] = grouped["content_seconds"] / 3600
    grouped["language"] = grouped["language"].map(lambda c: label(c, names.get(c)))
    fig = px.bar(grouped, x="date", y="hours", color="language", labels={"hours": "Horas", "date": "", "language": ""})
    fig.update_layout(bargap=0.15, legend_orientation="h", margin=dict(t=10, b=10))
    st.plotly_chart(fig, width="stretch")


def render_videos(languages: list[dict]) -> None:
    st.subheader("Videos vistos")
    videos = api.get_videos()
    if not videos:
        st.caption("Todavía no hay videos. Mirá algo en YouTube con la extensión activa.")
        return

    table = pd.DataFrame(
        {
            "Título": [v["title"] or v["source_video_id"] for v in videos],
            "Canal": [v["channel_name"] or "—" for v in videos],
            "Idioma": [label(v["language"] or UNASSIGNED) for v in videos],
            SCORE_COLUMN: [
                round(v["comprehensibility"] * 100) if v["comprehensibility"] is not None else None for v in videos
            ],
            "Puntaje": [
                {"manual": "tuyo", "channel": "estimado (canal)"}.get(v["comprehensibility_source"], "—") for v in videos
            ],
            "Visto": [fmt_hm(v["content_seconds"]) for v in videos],
            "Duración": [fmt_clock(v["duration_seconds"]) for v in videos],
            "Completado": [f"{v['completion']:.0%}" if v["completion"] is not None else "—" for v in videos],
            "Última vez": [
                pd.to_datetime(v["last_watched_at"]).tz_localize("UTC").tz_convert(TZ).strftime("%Y-%m-%d %H:%M")
                if v["last_watched_at"] else "—"
                for v in videos
            ],
        },
        index=[v["source_video_id"] for v in videos],
    ).astype({SCORE_COLUMN: "Float64"})

    st.caption(f"Podés editar **{SCORE_COLUMN}** directamente en la tabla y guardar varios a la vez.")
    edited = st.data_editor(
        table,
        hide_index=True,
        width="stretch",
        disabled=[c for c in table.columns if c != SCORE_COLUMN],
        column_config={
            SCORE_COLUMN: st.column_config.NumberColumn(
                min_value=0, max_value=100, step=5, format="%d",
                help="Qué porcentaje del contenido sentiste que entendiste (subjetivo).",
            )
        },
        # La key cambia con los datos: tras guardar, el editor arranca limpio con los valores nuevos.
        key=f"videos-{hash(table.to_json())}",
    )
    changes = score_changes(table[SCORE_COLUMN], edited[SCORE_COLUMN])
    if changes and st.button(f"Guardar {len(changes)} puntaje(s)", type="primary"):
        for video_id, score in changes.items():
            api.update_video_settings(video_id, comprehensibility_score=score)
        st.rerun()

    st.subheader("Detalle / editar")
    options = {v["source_video_id"]: v for v in videos}
    selected = st.selectbox(
        "Video",
        list(options),
        format_func=lambda vid: f"{label(options[vid]['language'] or UNASSIGNED)}  {options[vid]['title'] or vid}",
    )
    render_video_detail(api.get_video(selected), languages)


def score_changes(before: pd.Series, after: pd.Series) -> dict[str, float | None]:
    """{video_id: nuevo puntaje 0-1 (o None = borrar)} sólo para las celdas modificadas."""
    changes: dict[str, float | None] = {}
    for video_id, new in after.items():
        old = before.get(video_id)
        if pd.isna(old) and pd.isna(new):
            continue
        if not pd.isna(old) and not pd.isna(new) and float(old) == float(new):
            continue
        changes[video_id] = None if pd.isna(new) else float(new) / 100
    return changes


def render_video_detail(video: dict, languages: list[dict]) -> None:
    vid = video["source_video_id"]
    left, right = st.columns([1, 2])
    if video["thumbnail_url"]:
        left.image(video["thumbnail_url"])
    right.markdown(f"**[{video['title'] or vid}](https://www.youtube.com/watch?v={vid})**  \n{video['channel_name'] or ''}")
    source = {"manual": "manual", "youtube": "detectado por YouTube", "channel": "heredado del canal"}
    right.markdown(
        f"Idioma: {label(video['language'] or UNASSIGNED)}"
        + (f" _({source[video['language_source']]})_" if video["language_source"] else "")
    )

    c1, c2, c3 = st.columns(3)
    c1.metric("Duración", fmt_clock(video["duration_seconds"]))
    c2.metric("Visto", fmt_clock(video["coverage_seconds"]))
    c3.metric("Completado", f"{video['completion']:.1%}" if video["completion"] is not None else "—")

    subtitles = {mode: secs for mode, secs in video["subtitles"].items() if secs > 0}
    if subtitles:
        st.markdown(
            "Subtítulos: " + " · ".join(f"{SUBTITLE_LABELS[m]} {fmt_hm(s)}" for m, s in sorted(subtitles.items()))
        )
    if video["comprehensibility"] is not None:
        estimated = " _(estimado por el promedio del canal)_" if video["comprehensibility_source"] == "channel" else ""
        st.markdown(f"Comprensibilidad: {video['comprehensibility']:.0%}{estimated}")

    settings = video["settings"] or {}
    codes = [lang["code"] for lang in languages]
    names = {lang["code"]: lang["name"] for lang in languages}
    modes = list(SUBTITLE_LABELS)
    manual_score = settings.get("comprehensibility_score")
    with st.form(f"settings-{vid}"):
        c1, c2, c3 = st.columns(3)
        language = c1.selectbox(
            "Idioma (manual)",
            [None, *codes],
            index=codes.index(settings["language"]) + 1 if settings.get("language") in codes else 0,
            format_func=lambda c: "— automático —" if c is None else label(c, names[c]),
        )
        subtitle_mode = c2.selectbox(
            "Subtítulos (manual)",
            modes,
            index=modes.index(settings.get("subtitle_mode") or "unknown"),
            format_func=lambda m: "— automático —" if m == "unknown" else SUBTITLE_LABELS[m],
            help="Sólo si la detección automática falló: aplica a todo el tiempo visto de este video.",
        )
        score = c3.number_input(
            "Comprensibilidad % (manual)",
            min_value=0,
            max_value=100,
            step=5,
            value=None if manual_score is None else round(manual_score * 100),
            placeholder="sin puntuar",
            help="Vacío = sin puntaje propio (se usa el promedio del canal, si existe).",
        )
        if st.form_submit_button("Guardar"):
            api.update_video_settings(
                vid,
                language=language,
                subtitle_mode=subtitle_mode,
                comprehensibility_score=None if score is None else score / 100,
            )
            st.rerun()

    if video["sessions"]:
        st.markdown("**Sesiones**")
        for s in video["sessions"]:
            start = pd.to_datetime(s["started_at"]).tz_localize("UTC").tz_convert(TZ)
            end = pd.to_datetime(s["ended_at"]).tz_localize("UTC").tz_convert(TZ) if s["ended_at"] else None
            span = f"{start:%b %d  %H:%M} → {end:%H:%M}" if end is not None else f"{start:%b %d  %H:%M}"
            ranges = ", ".join(
                f"{fmt_clock(g['start_second'])}–{fmt_clock(g['end_second'])} ({subtitle_tag(g)})" for g in s["segments"]
            )
            st.markdown(f"- {span} · {fmt_hm(s['content_seconds'])} · {s['playback_speed']:.2f}x · tramos: {ranges}")


def subtitle_tag(segment: dict) -> str:
    if segment["subtitles_on"] is None:
        return "subs ?"
    if not segment["subtitles_on"]:
        return "sin subs"
    return f"subs {(segment['subtitle_language'] or '?').upper()}"


# ---------- Página ----------

st.set_page_config(page_title="CI Tracker", page_icon="🎧", layout="wide")
st.title("Comprehensible Input")

try:
    summary = api.get_summary()
    languages = api.get_languages()
except api.BackendError as exc:
    st.error(str(exc))
    st.info("Levantá el backend con: `uvicorn backend.app.main:app --host 127.0.0.1 --port 8000`")
    st.stop()

TZ = summary["timezone"]
tab_summary, tab_timeline, tab_videos = st.tabs(["Resumen", "Evolución", "Videos"])
with tab_summary:
    render_summary(summary)
with tab_timeline:
    render_timeline(summary)
with tab_videos:
    render_videos(languages)
