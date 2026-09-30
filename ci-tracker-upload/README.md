# Comprehensible Input Tracker

Registra automáticamente cuánto contenido de YouTube consumís en cada idioma, a partir
del tiempo **realmente reproducido** (no de la duración de los videos).

> Filosofía: la app mide **exposición**, no aprendizaje. *Watched/content time* son datos
> registrados; la comprensibilidad es tu evaluación subjetiva y el "CI efectivo" es una
> métrica derivada y aproximada. Nada de esto es evidencia científica de adquisición.

Versión actual: **v0.7**, con todo el roadmap implementado (ver [CHANGELOG.md](CHANGELOG.md)).

## Arquitectura

```
YouTube (pestaña)
   │  page_bridge.js (MAIN world): lee subtítulos activos + idioma del audio (subtítulos automáticos)
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
│  ├─ models.py      Video, Language, ViewingSession, WatchEvent, WatchedSegment, UserVideoSettings, Goal
│  ├─ schemas.py     contrato de la API (Pydantic)
│  ├─ segments.py    ★ reconstrucción de segmentos y deduplicación (lógica pura)
│  ├─ ingest.py      guarda eventos y recalcula la sesión
│  ├─ youtube.py     cliente YouTube Data API
│  ├─ metadata.py    caché de metadata (metadata_fetched_at)
│  ├─ language_detection.py  detector liviano por alfabeto + palabras frecuentes (lógica pura)
│  ├─ stats.py       resolución por video (idioma, subtítulos, puntaje, tipo) y agregaciones
│  ├─ streaks.py     streaks (lógica pura)
│  ├─ goals.py       progreso de objetivos
│  ├─ routes/        endpoints: events, videos, stats, goals (+ deps compartidas)
│  └─ main.py        app factory
├─ extension/        Chrome MV3 (manifest.json, content.js, page_bridge.js, background.js)
├─ dashboard/        Streamlit: app.py (pestañas), ui.py (formato/estilo), api_client.py,
│                    views/ (summary, timeline, analytics, videos, goals)
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

## Idioma (detección automática, v0.6)

Se usa la primera fuente disponible, de la más a la menos confiable:

| # | Fuente | De dónde sale |
|---|---|---|
| 1 | `manual` | lo que asignaste en el dashboard (siempre gana) |
| 2 | `youtube` | `defaultAudioLanguage` de la metadata (requiere API key) |
| 3 | `captions` | idioma de los **subtítulos automáticos** de YouTube, que se generan del audio; la extensión lo lee aunque tengas los subtítulos apagados |
| 4 | `channel` | idioma manual más usado en otros videos del mismo canal |
| 5 | `text` | detector liviano sobre título + descripción ([language_detection.py](backend/app/language_detection.py)) |

El detector de texto no usa IA ni dependencias: identifica el alfabeto (cirílico, kana,
hangul, han, árabe) o cuenta palabras funcionales frecuentes ("le", "der", "the"...). Es
conservador: si no hay suficiente evidencia no responde. El dashboard muestra de qué fuente
salió el idioma de cada video.

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

El puntaje es por video (no por tramo).

## CI efectivo (v0.4)

Métrica **derivada y estimada**, que el dashboard muestra siempre separada del tiempo
realmente visto:

```
CI efectivo = contenido visto × comprensibilidad        (60 min × 0.80 = 48 min)
```

- Se usa el **contenido** (no el tiempo real): 60 min de video a 1.5x con 80 % dan 48 min.
- El tiempo **sin puntaje no suma** (no se inventa un valor). La **cobertura** indica qué parte
  del contenido visto tiene puntaje y, por lo tanto, sobre cuánto se calculó.
- Los puntajes estimados por canal sí cuentan.
- En la API, todos los totales por período incluyen `effective_ci_seconds` y
  `rated_content_seconds`, y cada video trae su `effective_ci_seconds` (`null` si no tiene puntaje).
- En el dashboard aparece en el Resumen (total y por idioma, con cobertura), en el gráfico
  (selector "Mostrar") y en el detalle de cada video.

## Objetivos y streaks (v0.5)

- **Streak:** un día cuenta si viste al menos `STREAK_THRESHOLD_SECONDS` (60 s por defecto).
  Se muestra el streak actual y el récord, global y por idioma. Si hoy todavía no llegaste
  pero ayer sí, el streak sigue vivo ("falta hoy").
- **Objetivos** (pestaña Objetivos), por idioma o para todos, sobre contenido visto o CI efectivo:
  - *total*: acumulado histórico, p. ej. 100 h de francés → `48.2 / 100 h`;
  - *por día*: p. ej. 60 min/día → progreso de hoy, días cumplidos en los últimos 30 y racha.
  Son metas tuyas: no se asume que sean lingüísticamente óptimas.
- **Calendario** tipo GitHub (pestaña Evolución): un cuadro por día del último año, más
  oscuro = más horas.

## Análisis (v0.7)

Pestaña **Análisis**, filtrable por idioma:

- exposición por idioma (% del contenido total);
- contenido consumido vs. tiempo real, y cuánto ganaste por mirar a más de 1x;
- canales que más horas aportan (necesita API key para saber el canal);
- tipo de contenido: se asigna en Videos → Detalle, y los otros videos del canal lo heredan;
- velocidad de reproducción (horas a 1x, 1.25x, 1.5x...);
- subtítulos y comprensibilidad.

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
| GET | `/stats/streaks?threshold_seconds=60` | streak global + uno por idioma |
| GET | `/stats/channels?language=fr` | horas por canal |
| GET | `/stats/content-types?language=fr` | horas por tipo de contenido |
| GET | `/stats/speeds?language=fr` | horas por velocidad |
| GET / POST | `/goals` | listar (con progreso) / crear `{"language": "fr", "period": "daily", "metric": "content", "target_seconds": 3600}` |
| DELETE | `/goals/{id}` | borrar objetivo |
| GET | `/videos?language=fr` | |
| GET | `/videos/{id}` | detalle + sesiones + segmentos + desglose de subtítulos |
| PATCH | `/videos/{id}/settings` | `{"language": "fr", "subtitle_mode": "none", "comprehensibility_score": 0.85, "content_type": "podcast"}` |
| POST | `/admin/rebuild` | recalcula segmentos (desde eventos crudos) e idioma por texto |

En el `PATCH` solo cambian los campos enviados; `null` borra el valor manual. Fechas de la API: UTC.

## Configuración (`.env`)

| Variable | Default | |
|---|---|---|
| `YOUTUBE_API_KEY` | vacío | metadata de YouTube |
| `DATABASE_URL` | `data/ci_tracker.db` | |
| `TIMEZONE` | `UTC` | define "qué día" es cada sesión |
| `NATIVE_LANGUAGE` | `es` | para clasificar subtítulos |
| `CHANNEL_COMPREHENSIBILITY_FALLBACK` | `true` | estimar puntaje por canal |
| `STREAK_THRESHOLD_SECONDS` | `60` | mínimo diario para que un día cuente en el streak |
| `BACKEND_URL` | `http://127.0.0.1:8000` | lo usa el dashboard |

## Seguridad

- `YOUTUBE_API_KEY` sólo en `.env` (está en `.gitignore`); el navegador nunca la ve.
- Backend y dashboard escuchan sólo en `127.0.0.1`.

## Migraciones

Al arrancar, el backend agrega a una base existente las columnas nuevas opcionales
(`database.add_missing_columns`). Para cambios más complejos (renombrar, cambiar tipos)
habrá que sumar Alembic.

## Limitaciones conocidas

- La detección de subtítulos y del idioma del audio usa la API interna del reproductor de
  YouTube (no es pública). Si YouTube la cambia, los subtítulos caen a leer el botón "CC" (sin
  idioma) o a `unknown`, y el idioma del audio simplemente no se envía: quedan las otras
  fuentes. El tracking de tiempo no se ve afectado.
- El detector de idioma por texto es aproximado: con títulos muy cortos o mezclados puede no
  responder o equivocarse. Una asignación manual siempre lo corrige.
- Cirílico se asume ruso (no distingue ucraniano, búlgaro, etc.).
- En Windows, los emojis de banderas se ven como letras ("FR"). Es cosa del sistema operativo.
- El miniplayer (seguir viendo fuera de `/watch`) no se registra.
- Si Chrome se cierra de golpe se pueden perder hasta ~15 s (el lote pendiente en la pestaña).

## Roadmap

~~v0.1 MVP~~ ✓ · ~~v0.2 subtítulos~~ ✓ · ~~v0.3 comprensibilidad~~ ✓ · ~~v0.4 CI efectivo~~ ✓
· ~~v0.5 objetivos + streaks~~ ✓ · ~~v0.6 detección automática de idioma~~ ✓ · ~~v0.7 analytics~~ ✓

Ideas a futuro: otras fuentes (Netflix, Spotify, Twitch; el modelo ya usa `source` +
`source_video_id`), puntuar desde un popup de la extensión, Postgres + Alembic.
