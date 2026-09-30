# Changelog

## v0.3 — Comprensibilidad

### Qué hace
- **Puntaje manual por video** (0–100 %, subjetivo), editable en la tabla de Videos (varios a
  la vez) o en el detalle de cada video.
- **Estimación por canal:** un video sin puntaje usa el promedio de tus puntajes manuales en
  ese canal, marcado como *estimado*. Se desactiva con `CHANNEL_COMPREHENSIBILITY_FALLBACK=false`.
- **Distribución** del tiempo visto por rango (90–100, 80–90, 70–80, < 70, sin puntuar),
  filtrable por idioma, al lado del desglose de subtítulos.

### Cambios
- **Backend**:
  - `GET /stats/comprehensibility?language=fr`;
  - `comprehensibility` y `comprehensibility_source` (`manual` | `channel`) en `VideoOut`;
  - `stats.py` resuelve idioma, subtítulos y comprensibilidad de cada video en una sola
    consulta (`video_contexts`) y recibe la configuración en un `StatsConfig`.
- **Dashboard**: la tabla de videos es editable (`st.data_editor`); nueva sección "Desgloses"
  en el Resumen.
- Sin cambios de esquema: `comprehensibility_score` ya existía desde la v0.1.
- La extensión no cambia respecto de la v0.2.

No incluye "CI efectivo" (tiempo × puntaje): queda para la v0.4.

## v0.2 — Subtítulos

### Qué hace
La extensión detecta automáticamente si los subtítulos están encendidos y en qué idioma
(incluida la traducción automática de YouTube). Cada cambio corta el segmento, así que un
mismo video puede tener tramos con y sin subtítulos.

Se guarda el dato crudo (`subtitles_on`, `subtitle_language`) y el **modo** se calcula al
consultar, comparando con el idioma del video y `NATIVE_LANGUAGE`:

| Detectado | Modo |
|---|---|
| apagados | `none` |
| mismo idioma que el video | `target_language` |
| `NATIVE_LANGUAGE` (p. ej. español) | `native_language` |
| otro idioma | `other_language` |
| no se pudo detectar / video sin idioma asignado | `unknown` |

Como se calcula al consultar, si asignás el idioma de un video después, sus tramos se
reclasifican solos. Si la detección falla, en el dashboard (Videos → Detalle) podés fijar
el modo a mano para ese video. "Automático" vuelve a la detección.

Si un tramo se vuelve a ver el mismo día con otros subtítulos, cuenta la primera vez.

### Cambios
- **Extensión**: nuevo `page_bridge.js`, que corre en el contexto de la página
  (`"world": "MAIN"`, Chrome 111+) porque la API del reproductor no es visible desde el
  content script. Publica el estado en el atributo `data-ci-subtitles` de `#movie_player`.
  `content.js` lo lee y lo manda en cada evento.
- **Backend**:
  - columnas `subtitles_on` y `subtitle_language` en `watch_events` y `watched_segments`;
  - `GET /stats/subtitles?language=fr`;
  - `subtitles` (desglose por modo) en `GET /videos/{id}`;
  - `POST /admin/rebuild`.
- **Migración automática**: al arrancar, el backend agrega a una base v0.1 las columnas
  que falten. Lo ya registrado queda como `unknown`, porque en ese momento no se capturaba.
- **Config**: `NATIVE_LANGUAGE=es` en `.env`.
- **Dashboard**:
  - sección "Subtítulos" en Resumen, filtrable por idioma;
  - desglose por video;
  - selector manual de modo;
  - cada tramo de las sesiones muestra sus subtítulos.
- **Simulador**: `python scripts/simulate_watch.py --video abc --minutes 5 --subs fr` (o `--subs off`).

### Limitación
La detección usa la API interna del reproductor de YouTube, que no es pública. Si YouTube
la cambia, cae a leer el botón "CC" (sin idioma) y, en el peor caso, a `unknown`.
El tracking de tiempo no se ve afectado.

### Actualizar desde v0.1
1. Reiniciar el backend (aplica la migración sola).
2. `chrome://extensions` → ↻ en la extensión → recargar las pestañas de YouTube.
3. Opcional: agregar `NATIVE_LANGUAGE=es` al `.env`.

## v0.1 — MVP
Tracking de tiempo realmente visto, segmentos sin doble conteo, metadata de YouTube,
idioma manual / YouTube / por canal, dashboard con totales, gráfico diario y videos.
