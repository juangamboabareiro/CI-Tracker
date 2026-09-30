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
    const value = JSON.stringify(readSubtitles(player));
    if (player.getAttribute(ATTR) !== value) player.setAttribute(ATTR, value);
  }, 1000);
})();
