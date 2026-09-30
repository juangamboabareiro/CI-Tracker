"""Videos: tabla editable (comprensibilidad), detalle y ajustes manuales."""

import pandas as pd
import streamlit as st

import api_client as api
from ui import CONTENT_TYPE_LABELS, LANGUAGE_SOURCE_LABELS, SUBTITLE_LABELS, fmt_clock, fmt_hm, label, to_local

SCORE_COLUMN = "Comprensión %"
CONTENT_TYPES = [k for k in CONTENT_TYPE_LABELS if k != "unclassified"]


def render(languages: list[dict], tz: str) -> None:
    st.subheader("Videos vistos")
    videos = api.get_videos()
    if not videos:
        st.caption("Todavía no hay videos. Mirá algo en YouTube con la extensión activa.")
        return

    names = {lang["code"]: lang["name"] for lang in languages}
    _render_table(videos, names, tz)

    st.subheader("Detalle / editar")
    options = {v["source_video_id"]: v for v in videos}
    selected = st.selectbox(
        "Video",
        list(options),
        format_func=lambda vid: f"{label(options[vid]['language'])}  {options[vid]['title'] or vid}",
    )
    _render_detail(api.get_video(selected), languages, names, tz)


def _render_table(videos: list[dict], names: dict[str, str], tz: str) -> None:
    table = pd.DataFrame(
        {
            "Título": [v["title"] or v["source_video_id"] for v in videos],
            "Canal": [v["channel_name"] or "—" for v in videos],
            "Idioma": [label(v["language"], names.get(v["language"])) for v in videos],
            "Tipo": [CONTENT_TYPE_LABELS.get(v["content_type"] or "unclassified") for v in videos],
            SCORE_COLUMN: [
                round(v["comprehensibility"] * 100) if v["comprehensibility"] is not None else None for v in videos
            ],
            "Puntaje": [
                {"manual": "tuyo", "channel": "estimado (canal)"}.get(v["comprehensibility_source"], "—") for v in videos
            ],
            "Visto": [fmt_hm(v["content_seconds"]) for v in videos],
            "CI efectivo*": [
                fmt_hm(v["effective_ci_seconds"]) if v["effective_ci_seconds"] is not None else "—" for v in videos
            ],
            "Duración": [fmt_clock(v["duration_seconds"]) for v in videos],
            "Completado": [f"{v['completion']:.0%}" if v["completion"] is not None else "—" for v in videos],
            "Última vez": [
                to_local(v["last_watched_at"], tz).strftime("%Y-%m-%d %H:%M") if v["last_watched_at"] else "—"
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


def _render_detail(video: dict, languages: list[dict], names: dict[str, str], tz: str) -> None:
    vid = video["source_video_id"]
    left, right = st.columns([1, 2])
    if video["thumbnail_url"]:
        left.image(video["thumbnail_url"])
    right.markdown(f"**[{video['title'] or vid}](https://www.youtube.com/watch?v={vid})**  \n{video['channel_name'] or ''}")
    source = LANGUAGE_SOURCE_LABELS.get(video["language_source"])
    language = label(video["language"], names.get(video["language"]))
    right.markdown(f"Idioma: {language}" + (f" _({source})_" if source else ""))
    if video["content_type"]:
        inherited = " _(heredado del canal)_" if video["content_type_source"] == "channel" else ""
        right.markdown(f"Tipo: {CONTENT_TYPE_LABELS[video['content_type']]}{inherited}")

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
        st.markdown(
            f"Comprensibilidad: {video['comprehensibility']:.0%}{estimated} · "
            f"Visto {fmt_clock(video['content_seconds'])} → "
            f"**CI efectivo (estimado): {fmt_clock(video['effective_ci_seconds'])}**"
        )
    else:
        st.markdown("CI efectivo: — _(puntuá la comprensibilidad para calcularlo)_")

    _render_settings_form(video, languages)
    _render_sessions(video, tz)


def _render_settings_form(video: dict, languages: list[dict]) -> None:
    vid = video["source_video_id"]
    settings = video["settings"] or {}
    codes = [lang["code"] for lang in languages]
    names = {lang["code"]: lang["name"] for lang in languages}
    modes = list(SUBTITLE_LABELS)
    manual_score = settings.get("comprehensibility_score")
    manual_type = settings.get("content_type")

    with st.form(f"settings-{vid}"):
        c1, c2, c3, c4 = st.columns(4)
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
        content_type = c3.selectbox(
            "Tipo de contenido",
            [None, *CONTENT_TYPES],
            index=CONTENT_TYPES.index(manual_type) + 1 if manual_type in CONTENT_TYPES else 0,
            format_func=lambda t: "— automático —" if t is None else CONTENT_TYPE_LABELS[t],
            help="Los demás videos del canal sin tipo propio lo heredan.",
        )
        score = c4.number_input(
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
                content_type=content_type,
                comprehensibility_score=None if score is None else score / 100,
            )
            st.rerun()


def _render_sessions(video: dict, tz: str) -> None:
    if not video["sessions"]:
        return
    st.markdown("**Sesiones**")
    for s in video["sessions"]:
        start, end = to_local(s["started_at"], tz), to_local(s["ended_at"], tz)
        span = f"{start:%b %d  %H:%M} → {end:%H:%M}" if end is not None else f"{start:%b %d  %H:%M}"
        ranges = ", ".join(
            f"{fmt_clock(g['start_second'])}–{fmt_clock(g['end_second'])} ({_subtitle_tag(g)})" for g in s["segments"]
        )
        st.markdown(f"- {span} · {fmt_hm(s['content_seconds'])} · {s['playback_speed']:.2f}x · tramos: {ranges}")


def _subtitle_tag(segment: dict) -> str:
    if segment["subtitles_on"] is None:
        return "subs ?"
    if not segment["subtitles_on"]:
        return "sin subs"
    return f"subs {(segment['subtitle_language'] or '?').upper()}"
