// @ts-check
/**
 * Content script: corre dentro de youtube.com y observa el <video>.
 *
 * Estrategia: polling cada 1 s del estado del reproductor (posición, velocidad,
 * reproduciendo o no) y emisión de eventos sólo cuando algo cambia o cada 5 s
 * mientras se reproduce. Los eventos se acumulan y se entregan en lotes al
 * service worker (background.js), que los persiste y los manda al backend.
 *
 * El backend reconstruye los segmentos; acá sólo describimos lo que pasó.
 */
"use strict";

const SAMPLE_INTERVAL_MS = 1000;
const PROGRESS_EVERY_MS = 5000;
const FLUSH_EVERY_MS = 15000;
const SEEK_TOLERANCE_S = 2;

/**
 * @typedef {{ on: boolean | null, language: string | null }} Subtitles
 * @typedef {{ at: number, position: number, rate: number, playing: boolean, subtitles: Subtitles }} Snapshot
 * @typedef {"play" | "progress" | "pause" | "seek" | "ended"} EventKind
 * @typedef {{
 *   source: "youtube", video_id: string, session_id: string, seq: number,
 *   timestamp: string, current_time: number, playback_rate: number,
 *   paused: boolean, event: EventKind, page_title?: string,
 *   subtitles_on: boolean | null, subtitle_language: string | null,
 *   audio_language_hint: string | null
 * }} WatchEvent
 * @typedef {{ videoId: string, sessionId: string, seq: number, last: Snapshot | null, lastEmittedAt: number }} Tracking
 */

/** @type {Tracking | null} */
let tracking = null;
/** @type {WatchEvent[]} */
let buffer = [];

// ---------- Lectura del DOM de YouTube ----------

/** @returns {string | null} */
function currentVideoId() {
  if (location.pathname !== "/watch") return null;
  return new URLSearchParams(location.search).get("v");
}

/** @returns {HTMLVideoElement | null} */
function getVideoElement() {
  return document.querySelector("#movie_player video.html5-main-video") || document.querySelector("#movie_player video");
}

/** Durante un anuncio el <video> reproduce el anuncio: no debe contar. */
function isAdShowing() {
  const player = document.querySelector("#movie_player");
  return !!player && player.classList.contains("ad-showing");
}

function pageTitle() {
  const title = document.title.replace(/^\(\d+\)\s*/, "").replace(/\s*-\s*YouTube$/, "").trim();
  return title ? title.slice(0, 300) : undefined;
}

/**
 * Estado de subtítulos publicado por page_bridge.js en un atributo del reproductor.
 * @returns {Subtitles}
 */
function readSubtitles() {
  const raw = document.querySelector("#movie_player")?.getAttribute("data-ci-subtitles");
  if (!raw) return { on: null, language: null };
  try {
    const parsed = JSON.parse(raw);
    return {
      on: typeof parsed.on === "boolean" ? parsed.on : null,
      language: typeof parsed.language === "string" ? parsed.language : null,
    };
  } catch {
    return { on: null, language: null };
  }
}

/**
 * Idioma del audio según los subtítulos automáticos (publicado por page_bridge.js).
 * Sólo se usa si corresponde al video que estamos registrando (en navegación SPA puede
 * quedar un instante el del video anterior).
 * @param {string} videoId
 * @returns {string | null}
 */
function readAudioHint(videoId) {
  const raw = document.querySelector("#movie_player")?.getAttribute("data-ci-audio");
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw);
    return parsed.video_id === videoId && typeof parsed.language === "string" ? parsed.language : null;
  } catch {
    return null;
  }
}

/** @param {Snapshot} a @param {Snapshot} b */
function subtitlesChanged(a, b) {
  return a.subtitles.on !== b.subtitles.on || a.subtitles.language !== b.subtitles.language;
}

/** @param {HTMLVideoElement} video @returns {Snapshot} */
function takeSnapshot(video) {
  return {
    at: Date.now(),
    position: video.currentTime,
    rate: video.playbackRate,
    playing: !video.paused && !video.ended && !isAdShowing(),
    subtitles: readSubtitles(),
  };
}

// ---------- Emisión de eventos ----------

/** @param {EventKind} kind @param {Snapshot} snap */
function emit(kind, snap) {
  if (!tracking) return;
  if (kind === "progress" && snap.at === tracking.lastEmittedAt) return; // ya emitido
  buffer.push({
    source: "youtube",
    video_id: tracking.videoId,
    session_id: tracking.sessionId,
    seq: tracking.seq++,
    timestamp: new Date(snap.at).toISOString(),
    current_time: Math.round(snap.position * 1000) / 1000,
    playback_rate: snap.rate,
    paused: !snap.playing,
    event: kind,
    page_title: pageTitle(),
    subtitles_on: snap.subtitles.on,
    subtitle_language: snap.subtitles.on ? snap.subtitles.language : null,
    audio_language_hint: readAudioHint(tracking.videoId),
  });
  tracking.lastEmittedAt = snap.at;
}

function flush() {
  if (buffer.length === 0) return;
  const events = buffer;
  buffer = [];
  try {
    chrome.runtime.sendMessage({ type: "watch-events", events }).catch(() => {
      buffer = events.concat(buffer); // el service worker no respondió: reintentamos luego
    });
  } catch (err) {
    // "Extension context invalidated": la extensión se recargó; esta pestaña queda huérfana.
    console.warn("[CI Tracker] could not deliver events", err);
  }
}

// ---------- Ciclo de vida de una sesión ----------

/** @param {string} videoId */
function startTracking(videoId) {
  tracking = { videoId, sessionId: crypto.randomUUID(), seq: 0, last: null, lastEmittedAt: 0 };
}

function stopTracking() {
  if (tracking?.last?.playing) {
    emit("pause", { ...tracking.last, playing: false });
  }
  flush();
  tracking = null;
}

/** Compara el estado anterior con el actual y emite los eventos correspondientes. */
function tick() {
  const videoId = currentVideoId();
  if (tracking && tracking.videoId !== videoId) stopTracking(); // cambio de video o navegación
  if (!videoId) return;

  const video = getVideoElement();
  if (!video) return;
  if (!tracking) startTracking(videoId);
  const t = /** @type {Tracking} */ (tracking);

  const now = takeSnapshot(video);
  const prev = t.last;

  if (!prev) {
    if (now.playing) emit("play", now);
  } else if (!prev.playing && now.playing) {
    emit("play", now);
  } else if (prev.playing && !now.playing) {
    // En un anuncio, currentTime es el del anuncio: usamos la última posición del video real.
    const at = isAdShowing() ? { ...prev, playing: false } : now;
    emit(video.ended ? "ended" : "pause", at);
    flush();
  } else if (prev.playing && now.playing) {
    const expected = prev.position + ((now.at - prev.at) / 1000) * prev.rate;
    if (Math.abs(now.position - expected) > SEEK_TOLERANCE_S) {
      emit("progress", prev); // cerramos el tramo en la última posición conocida
      emit("seek", now);
    } else if (
      now.rate !== prev.rate ||
      subtitlesChanged(prev, now) || // cortamos el segmento al prender/apagar/cambiar subtítulos
      now.at - t.lastEmittedAt >= PROGRESS_EVERY_MS
    ) {
      emit("progress", now);
    }
  }
  t.last = now;
}

// ---------- Arranque ----------

setInterval(tick, SAMPLE_INTERVAL_MS);
setInterval(flush, FLUSH_EVERY_MS);

// Cierre de pestaña / refresh / navegación fuera de YouTube.
window.addEventListener("pagehide", () => {
  const video = getVideoElement();
  if (tracking?.last?.playing && video && !isAdShowing()) {
    tracking.last = { ...takeSnapshot(video), playing: true }; // stopTracking cierra en esta posición
  }
  stopTracking();
});

// Al ocultar la pestaña mandamos lo pendiente (el video puede seguir sonando y se sigue contando).
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "hidden") flush();
});
