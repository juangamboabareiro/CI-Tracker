"""Dashboard Streamlit. Cada pestaña vive en su módulo dentro de views/.

Ejecutar desde la raíz del proyecto:
    streamlit run dashboard/app.py
"""

import streamlit as st

import api_client as api
from views import analytics, goals, summary, timeline, videos

st.set_page_config(page_title="CI Tracker", page_icon="🎧", layout="wide")
st.title("Comprehensible Input")

try:
    summary_data = api.get_summary()
    languages = api.get_languages()
except api.BackendError as exc:
    st.error(str(exc))
    st.info("Levantá el backend con: `uvicorn backend.app.main:app --host 127.0.0.1 --port 8000`")
    st.stop()

tabs = st.tabs(["Resumen", "Evolución", "Análisis", "Videos", "Objetivos"])
with tabs[0]:
    summary.render(summary_data)
with tabs[1]:
    timeline.render(summary_data)
with tabs[2]:
    analytics.render(summary_data)
with tabs[3]:
    videos.render(languages, summary_data["timezone"])
with tabs[4]:
    goals.render(languages)
