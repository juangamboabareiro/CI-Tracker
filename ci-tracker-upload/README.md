# Comprehensible Input Tracker

Registra automáticamente cuánto contenido de YouTube consumís en cada idioma, a partir
del tiempo **realmente reproducido** (no de la duración de los videos).

> Filosofía: la app mide **exposición**, no aprendizaje. *Watched/content time* son datos
> registrados; la comprensibilidad es tu evaluación subjetiva y el "CI efectivo" (v0.4) será
> una métrica derivada y aproximada. Nada de esto es evidencia científica de adquisición.

Versión actual: **v0.3** (ver [CHANGELOG.md](CHANGELOG.md)).

## Arquitectura

```
YouTube (pestaña)
   │  page_bridge.js (MAIN world): lee la pista de subtítulos del reproductor
   │  content.js: polling 1 s del <video> → eventos play/progress/pause/seek/ended
   ▼
Service worker (background.js): cola persistente en chrome.storage → lotes
   │  POST /events/watch   (reintentos idempotentes: session_id + seq)
   ▼
Backend FastAPI ── SQLite (eventos crudos → segmentos → sesiones)
   │        └──── YouTube Data API v3 (sólo metadata, cacheada)
   ▼
Dashboard Streamlit (sólo habla con la API REST)
```

Cada componente se comunica por una interfaz simple (HTTP/JSON), así se puede reemplazar
cualquiera sin reescribir los demás.

```
LangMVP/
├─ backend/app/
│  ├─ config.py      configuración centralizada (.env)
│  ├─ database.py    engine, sesiones, create_all + mini-migración + idiomas iniciales
│  ├─ models.py      Video, Language, ViewingSession, WatchEvent, WatchedSegment, UserVideoSettings
│  ├─ schemas.py     contrato de la API (Pydantic)
│  ├─ segments.py    ★ reconstrucción de segmentos y deduplicación (lógica pura)
│  ├─ ingest.py      guarda eventos y recalcula la sesión
│  ├─ youtube.py     cliente YouTube Data API
│  ├─ metadata.py    caché de metadata (metadata_fetched_at)
│  ├─ stats.py       agregaciones por día / idioma / subtítulos / comprensibilidad
│  ├─ api.py         endpoints
│  └─ main.py        app factory
├─ extension/        Chrome MV3 (manifest.json, content.js, page_bridge.js, background.js)
├─ dashboard/        Streamlit (app.py, api_client.py)
├─ scripts/simulate_watch.py   simula la extensión (probar sin YouTube)
└─ tests/
```

## Instalación (Windows / PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env      # y completá YOUTUBE_API_KEY
```

**API key de YouTube** (opcional pero recomendada): Google Cloud Console → crear proyecto →
*APIs & Services* → habilitar **YouTube Data API v3** → *Credentials* → *Create API key*.
Restringila a esa API. Sin key la app funciona, pero el título sale de la pestaña y no hay
duración, canal ni detección de idioma.

## Ejecutar

Dos terminales, desde la carpeta del proyecto:

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --reload
```

```powershell
.\.venv\Scripts\python.exe -m streamlit run dashboard/app.py
```

- API + docs interactivas: http://127.0.0.1:8000/docs
- Dashboard: http://127.0.0.1:8501 (limitado a esta PC por `.streamlit/config.toml`)

### Cargar la extensión (Chrome 111+)

1. Chrome → `chrome://extensions` → activar **Modo de desarrollador**.
2. **Cargar extensión sin empaquetar** → elegir la carpeta `extension/`.
3. Abrir un video de YouTube. Nada más.

Depuración: en `chrome://extensions` → *Service worker* (logs del envío); en la pestaña de
YouTube, DevTools → Console (mensajes `[CI Tracker]`). Si cambiás el código de la extensión,
tocá ↻ y recargá YouTube.

### Probar sin YouTube

```powershell
.\.venv\Scripts\python.exe scripts/simulate_watch.py --video dQw4w9WgXcQ --minutes 12 --rate 1.25 --subs fr
```

`--subs off` = subtítulos apagados; sin `--subs` = no detectado.

### Tests

```powershell
.\.venv\Scripts\python.exe -m pytest
```

## Cómo se calcula el tiempo

1. La extensión emite **muestras** (posición, velocidad, pausado, subtítulos, timestamp). No calcula totales.
2. El backend toma pares consecutivos de muestras y los acepta como reproducción continua sólo
   si son plausibles: no pausado, sin `seek`, gap real ≤ 120 s, la posición avanzó y no más
   de lo posible a esa velocidad. Así no se confía ciegamente en el cliente.
3. Los pares contiguos con el mismo estado forman **segmentos** `[start, end]` (en segundos del video).
4. **Deduplicación:** cada segundo de un video cuenta como máximo **una vez por día**
   (zona horaria `TIMEZONE`). Cubre pausas, rewinds, refresh y reaperturas. Otro día sí suma.
   Si un tramo se repite, cuenta la primera vez que lo viste (con esa velocidad y esos subtítulos).

| Caso | Resultado |
|---|---|
| Veo 0→15 min de un video de 60 min | 15 min |
| 0→15 y después 30→40 | 25 min |
| 0→10, pausa, vuelvo a 5→15 | 15 min |
| 60 min a 1.5x | contenido 60 min · tiempo real 40 min |
| Anuncios, pestaña dormida, PC suspendida | no suman |

Se guardan dos métricas: **content_seconds** (cuánto contenido consumiste) y
**wall_clock_seconds** (cuánto tiempo real te llevó).

## Idioma

Manual → `defaultAudioLanguage` de YouTube → idioma manual más usado en ese **canal**.
Asignás un idioma una vez a un video de Easy French y los siguientes videos del canal se
clasifican solos.

## Subtítulos (v0.2)

La extensión detecta si los subtítulos están encendidos y en qué idioma (incluida la
traducción automática de YouTube). Cada cambio corta el segmento. El **modo** se calcula al
consultar, comparando con el idioma del video y `NATIVE_LANGUAGE`:

| Detectado | Modo |
|---|---|
| apagados | `none` |
| mismo idioma que el video | `target_language` |
| `NATIVE_LANGUAGE` (p. ej. español) | `native_language` |
| otro idioma | `other_language` |
| no se pudo detectar / video sin idioma asignado | `unknown` |

Si asignás el idioma de un video después, sus tramos se reclasifican solos. Si la detección
falla, podés fijar el modo a mano en el dashboard (Videos → Detalle). "Automático" vuelve a
la detección.

## Comprensibilidad (v0.3)

Puntaje **subjetivo** de 0 a 100 %: qué parte del contenido sentiste que entendiste. Es una
evaluación personal, no una medida objetiva.

- Se carga en el dashboard, en la tabla de **Videos** (editás la columna y guardás varios a la
  vez) o en el detalle de cada video.
- **Estimación por canal:** un video sin puntaje propio usa el **promedio de tus puntajes en
  ese canal** y se marca como *estimado*. Puntuando algunos videos de un canal queda cubierto
  el resto. Se desactiva con `CHANNEL_COMPREHENSIBILITY_FALLBACK=false`.
- El Resumen muestra la **distribución** del tiempo visto: 90–100 %, 80–90 %, 70–80 %, < 70 %,
  sin puntuar.

El puntaje es por video (no por tramo). El "CI efectivo" (tiempo × comprensibilidad) llega
en la v0.4.

## API

| Método | Ruta | |
|---|---|---|
| GET | `/health` | |
| POST | `/events/watch` | lote de eventos (máx. 1000) |
| POST | `/videos/{id}/metadata` | fuerza la recarga desde YouTube |
| GET | `/languages` | |
| GET | `/stats/summary` | hoy / 7 d / 30 d / año / total, global y por idioma |
| GET | `/stats/languages` | |
| GET | `/stats/subtitles?language=fr` | tiempo por modo de subtítulos |
| GET | `/stats/comprehensibility?language=fr` | tiempo por rango de comprensibilidad |
| GET | `/stats/daily?days=30&language=fr` | serie diaria |
| GET | `/videos?language=fr` | |
| GET | `/videos/{id}` | detalle + sesiones + segmentos + desglose de subtítulos |
| PATCH | `/videos/{id}/settings` | `{"language": "fr", "subtitle_mode": "none", "comprehensibility_score": 0.85}` |
| POST | `/admin/rebuild` | recalcula todos los segmentos desde los eventos crudos |

En el `PATCH` solo cambian los campos enviados; `null` borra el valor manual. Fechas de la API: UTC.

## Configuración (`.env`)

| Variable | Default | |
|---|---|---|
| `YOUTUBE_API_KEY` | vacío | metadata de YouTube |
| `DATABASE_URL` | `data/ci_tracker.db` | |
| `TIMEZONE` | `UTC` | define "qué día" es cada sesión |
| `NATIVE_LANGUAGE` | `es` | para clasificar subtítulos |
| `CHANNEL_COMPREHENSIBILITY_FALLBACK` | `true` | estimar puntaje por canal |
| `BACKEND_URL` | `http://127.0.0.1:8000` | lo usa el dashboard |

## Seguridad

- `YOUTUBE_API_KEY` sólo en `.env` (está en `.gitignore`); el navegador nunca la ve.
- Backend y dashboard escuchan sólo en `127.0.0.1`.

## Migraciones

Al arrancar, el backend agrega a una base existente las columnas nuevas opcionales
(`database.add_missing_columns`). Para cambios más complejos (renombrar, cambiar tipos)
habrá que sumar Alembic.

## Limitaciones conocidas

- La detección de subtítulos usa la API interna del reproductor de YouTube (no es pública).
  Si YouTube la cambia, cae a leer el botón "CC" (sin idioma) y, en el peor caso, a `unknown`.
  El tracking de tiempo no se ve afectado.
- En Windows, los emojis de banderas se ven como letras ("FR"). Es cosa del sistema operativo.
- El miniplayer (seguir viendo fuera de `/watch`) no se registra.
- Si Chrome se cierra de golpe se pueden perder hasta ~15 s (el lote pendiente en la pestaña).

## Roadmap

~~v0.1 MVP~~ ✓ · ~~v0.2 subtítulos~~ ✓ · ~~v0.3 comprensibilidad~~ ✓ · v0.4 CI efectivo
· v0.5 objetivos + streaks · v0.6 detección automática de idioma · v0.7 analytics avanzados
