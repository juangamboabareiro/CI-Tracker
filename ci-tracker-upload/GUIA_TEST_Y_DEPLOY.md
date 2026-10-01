# CI Tracker: guía de prueba y deploy

Todos los comandos se corren en PowerShell, desde `C:\Users\T01JGAMBOA\Desktop\LangMVP`.

---

## 1. Preparar (una sola vez)

El entorno `.venv` ya está creado y el `.env` ya existe. Revisá que el `.env` tenga estas
líneas (podés copiarlas de `.env.example`):

```
YOUTUBE_API_KEY=tu_key
TIMEZONE=America/Argentina/Buenos_Aires
NATIVE_LANGUAGE=es
```

Sin API key la app funciona igual, pero no vas a ver canal, duración ni idioma desde YouTube.

Si alguna vez tenés que recrear el entorno:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

---

## 2. Levantar la app (cada vez que la uses)

**Terminal 1, el backend:**

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

Para comprobar que anda, abrí http://127.0.0.1:8000/health: tiene que responder `{"status":"ok"}`.
La documentación interactiva de la API está en http://127.0.0.1:8000/docs.

**Terminal 2, el dashboard:**

```powershell
.\.venv\Scripts\python.exe -m streamlit run dashboard/app.py
```

Queda en http://127.0.0.1:8501.

---

## 3. Cargar la extensión (una vez, y cada vez que cambie su código)

1. En Chrome (versión 111 o más nueva) entrá a `chrome://extensions` y activá el
   **Modo de desarrollador**.
2. Usá **Cargar extensión sin empaquetar** y elegí la carpeta `LangMVP\extension`.
3. Si ya la tenías cargada, tocá ↻ en la extensión y recargá las pestañas de YouTube.

---

## 4. Probar

### Tests automáticos (opcional)

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Tienen que pasar los 103.

### Prueba real

1. Mirá unos minutos de un video en francés, prendé y apagá los subtítulos, pausá y
   retrocedé un poco. Después cerrá la pestaña.
2. Recargá el dashboard y revisá cada pestaña:
   - **Resumen:** aparecen los minutos de hoy, el streak en 1 día y el idioma detectado.
   - **Videos:** el video tiene el idioma y su fuente, y los tramos muestran "sin subs" o
     "subs FR". Probá puntuar la comprensibilidad y asignar el tipo de contenido.
   - **Objetivos:** creá uno (por ejemplo, "French, por día, 20 min") y fijate que aparezca
     la barra de progreso.
   - **Evolución y Análisis:** tienen que mostrar gráficos, sin errores en rojo.
3. Para recalcular la detección de idioma de los videos que ya tenías (una sola vez):

   ```powershell
   .\.venv\Scripts\python.exe -c "import httpx; print(httpx.post('http://127.0.0.1:8000/admin/rebuild', timeout=120).json())"
   ```

### Probar sin YouTube (simulador)

```powershell
.\.venv\Scripts\python.exe scripts/simulate_watch.py --video abc123 --minutes 10 --rate 1.25 --subs fr
```

`--subs off` = subtítulos apagados; sin `--subs` = no detectado.

### Si algo falla, mirá estos tres lugares

| Dónde | Qué buscar |
|---|---|
| Terminal 1 (backend) | líneas `Ingested batch: accepted=...` |
| `chrome://extensions` → *Service worker* → Console | errores de envío al backend |
| Pestaña de YouTube → F12 → Console | mensajes `[CI Tracker]` |

---

## 5. Subir a GitHub

1. Sincronizá la carpeta de subida `Desktop\ci-tracker-upload` con la versión actual (es una
   copia del proyecto sin `.env`, `.venv` ni cachés).
2. En tu repo, andá a **Add file → Upload files**, arrastrá **todo el contenido** de esa
   carpeta (no la carpeta en sí) y hacé commit con un mensaje como
   `v0.7: objetivos, streaks, detección de idioma, analytics`.
3. **Borrá a mano `backend/app/api.py`** en GitHub: abrís el archivo, menú ⋯ → *Delete file*.
   Subir archivos no elimina los viejos, y ese quedó reemplazado por `backend/app/routes/`.
4. Revisá que **no** aparezca `.env` en el repo. Si apareciera, borralo y generá una API key
   nueva en Google Cloud.

"Deployar" en este caso es eso: el código queda en GitHub y la app corre en tu PC, porque el
backend y la base de datos son locales.

### Sincronizar la carpeta de subida (versiones futuras)

```powershell
robocopy . ..\ci-tracker-upload /MIR /XD .venv .claude .pytest_cache __pycache__ data /XF .env *.pyc
```

(robocopy devuelve códigos 1–3 cuando copia bien; no son errores).
