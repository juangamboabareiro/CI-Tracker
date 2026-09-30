// @ts-check
/**
 * Corre en el "MAIN world" (el mismo contexto JavaScript que YouTube), porque la API
 * interna del reproductor (player.getOption) no es visible desde el content script,
 * que corre aislado. Publicamos el estado en un atributo del DOM, que sí se comparte:
 *
 *   <div id="movie_player" data-ci-subtitles='{"on":true,"language":"fr"}'>
 *
 * Si YouTube cambia su API interna, se degrada a leer el botón "CC" (sin idioma)
 * y, en el peor caso, a {"on": null} = "no detectado". Nunca rompe el tracking.
 */
(() => {
  "use strict";

  const ATTR = "data-ci-subtitles";
  const AUDIO_ATTR = "data-ci-audio";

  /**
   * Los subtítulos automáticos (kind "asr") se generan del audio: su idioma ES el idioma
   * hablado del video, aunque el usuario tenga los subtítulos apagados (v0.6).
   * @param {any} player
   * @returns {{ video_id: string, language: string } | null}
   */
  function readAudioHint(player) {
    try {
      if (typeof player.getPlayerResponse !== "function") return null;
      const response = player.getPlayerResponse();
      const videoId = response && response.videoDetails && response.videoDetails.videoId;
      const renderer = response && response.captions && response.captions.playerCaptionsTracklistRenderer;
      const tracks = (renderer && renderer.captionTracks) || [];
      const asr = tracks.find((/** @type {any} */ t) => t.kind === "asr");
      return videoId && asr && asr.languageCode ? { video_id: videoId, language: asr.languageCode } : null;
    } catch {
      return null;
    }
  }

  /** @param {HTMLElement} el @param {string} name @param {unknown} value */
  function publish(el, name, value) {
    const serialized = value == null ? null : JSON.stringify(value);
    if (serialized === null) el.removeAttribute(name);
    else if (el.getAttribute(name) !== serialized) el.setAttribute(name, serialized);
  }

  /**
   * @param {any} player
   * @returns {{ on: boolean | null, language: string | null }}
   */
  function readSubtitles(player) {
    const button = player.querySelector(".ytp-subtitles-button");
    const pressed = button ? button.getAttribute("aria-pressed") : null;
    if (pressed === "false") return { on: false, language: null };

    let track = null;
    try {
      if (typeof player.getOption === "function") track = player.getOption("captions", "track");
    } catch {
      track = null;
    }
    if (track && track.languageCode) {
      // Con "traducción automática" lo que se lee en pantalla es el idioma traducido.
      const translated = track.translationLanguage && track.translationLanguage.languageCode;
      return { on: true, language: translated || track.languageCode };
    }

    if (pressed === "true") return { on: true, language: null };
    return { on: null, language: null };
  }

  setInterval(() => {
    const player = document.getElementById("movie_player");
    if (!player) return;
    publish(player, ATTR, readSubtitles(player));
    publish(player, AUDIO_ATTR, readAudioHint(player));
  }, 1000);
})();
