"""Detección liviana de idioma por texto (v0.6). Lógica pura, sin dependencias ni IA.

Dos pasos:
  1. Alfabeto: cirílico, kana, hangul, han o árabe definen el idioma casi sin dudas.
  2. Alfabeto latino: se cuentan palabras funcionales muy frecuentes ("le", "der", "the"...)
     de cada idioma y gana el que más aparece, si supera un mínimo y un margen.

Es deliberadamente conservador: prefiere no responder a responder mal. Por eso es la
ÚLTIMA fuente en la resolución de idioma (ver stats.py).
"""

import re
from dataclasses import dataclass

MIN_HITS = 2  # los títulos son cortos; el margen de MIN_CONFIDENCE evita falsos positivos
MIN_CONFIDENCE = 0.6  # best / (best + segundo): 0.6 = al menos 1.5 veces más evidencia que el segundo
SCRIPT_MIN_SHARE = 0.3

_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)

STOPWORDS: dict[str, frozenset[str]] = {
    "fr": frozenset(
        "le la les des une est et du au aux que qui dans pour pas sur avec ce cette sont nous vous "
        "ils elle mais ou comme très tout faire être avoir je tu il on mon ma mes son ses leur".split()
    ),
    "es": frozenset(
        "el la los las una unos es y del al que en por para con no sus pero como más muy este esta "
        "son yo tú él ella nosotros hay ser estar también porque cuando todo mi".split()
    ),
    "it": frozenset(
        "il lo la gli le una è e del della che di per non con sono ma come più molto questo questa "
        "io tu lui lei noi voi anche perché quando tutto mio si ci nel nella dei delle alla cosa".split()
    ),
    "pt": frozenset(
        "o a os as um uma é e do da dos das que em para com não por mas como mais muito este esta "
        "são eu você ele ela nós também porque quando tudo meu".split()
    ),
    "de": frozenset(
        "der die das und ist nicht ein eine mit auf für von zu den dem sich auch es ich du er sie "
        "wir ihr aber wie mehr sehr dieser diese sind oder wenn was über im am zum zur warum".split()
    ),
    "en": frozenset(
        "the and is are of to in that it for with this on you not but what how was be have "
        "they we he she my your from about".split()
    ),
    "nl": frozenset(
        # Sin "de"/"en": son igual de frecuentes en francés/español y sesgarían hacia el neerlandés.
        "het een is van op niet met voor zijn dat ook maar hoe meer heel deze ik jij hij "
        "zij wij jullie als wanneer".split()
    ),
}

# Caracteres que casi sólo aparecen en un idioma: suman como pista extra.
DISTINCTIVE_CHARS: dict[str, str] = {"es": "ñ¿¡", "pt": "ãõ", "de": "ßäöü", "fr": "œçèêë"}


@dataclass(frozen=True)
class Detection:
    language: str
    confidence: float  # 0-1


def _script_language(text: str) -> Detection | None:
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return None
    counts = {
        "ru": sum("Ѐ" <= ch <= "ӿ" for ch in letters),
        "ja": sum("぀" <= ch <= "ヿ" for ch in letters),
        "ko": sum("가" <= ch <= "힯" for ch in letters),
        "zh": sum("一" <= ch <= "鿿" for ch in letters),
        "ar": sum("؀" <= ch <= "ۿ" for ch in letters),
    }
    if counts["ja"]:  # el japonés mezcla kana con kanji (han): el kana lo delata
        counts["ja"] += counts["zh"]
        counts["zh"] = 0
    language, count = max(counts.items(), key=lambda kv: kv[1])
    share = count / len(letters)
    return Detection(language, round(min(1.0, share), 2)) if share >= SCRIPT_MIN_SHARE else None


def _stopword_language(text: str) -> Detection | None:
    words = [w.lower() for w in _WORD_RE.findall(text)]
    scores = {lang: float(sum(w in stops for w in words)) for lang, stops in STOPWORDS.items()}
    lowered = text.lower()
    for lang, chars in DISTINCTIVE_CHARS.items():
        scores[lang] += 0.5 * sum(lowered.count(ch) for ch in chars)

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    (language, best), (_, second) = ranked[0], ranked[1]
    if best < MIN_HITS:
        return None
    # Contra el segundo (no contra el total): palabras compartidas entre idiomas latinos
    # ("la", "que") inflarían el total y bajarían la confianza de un texto claro.
    confidence = best / (best + second)
    return Detection(language, round(confidence, 2)) if confidence >= MIN_CONFIDENCE else None


def detect_language(text: str | None) -> Detection | None:
    """Idioma más probable del texto, o None si no hay evidencia suficiente."""
    if not text or not text.strip():
        return None
    return _script_language(text) or _stopword_language(text)
