"""Reconstrucción de segmentos vistos y deduplicación. Lógica pura (sin DB).

La extensión manda "muestras": en el instante T el reproductor estaba en la
posición P, a velocidad R, reproduciendo o pausado, con tales subtítulos.
Entre dos muestras consecutivas (a, b) decimos que hubo reproducción continua sólo si:

  * a no estaba pausado y b no es un salto explícito ("seek"),
  * el tiempo real entre ambas es > 0 y no supera un máximo (tab dormida, PC suspendida...),
  * la posición avanzó (si retrocede es un rewind),
  * y no avanzó más de lo físicamente posible a esa velocidad (si no, es un seek).

Así no confiamos ciegamente en el cliente: un evento mal formado o un salto
nunca suma más tiempo del que realmente pudo pasar.

El estado (velocidad, subtítulos) de un tramo a -> b es el de la muestra `a`.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Sample:
    seq: int
    at: datetime
    position: float
    rate: float
    paused: bool
    kind: str  # play | progress | pause | seek | ended
    subtitles_on: bool | None = None  # None = no se pudo detectar
    subtitle_language: str | None = None


@dataclass
class Segment:
    start: float
    end: float
    rate: float
    started_at: datetime
    wall_seconds: float
    subtitles_on: bool | None = None
    subtitle_language: str | None = None

    @property
    def length(self) -> float:
        return self.end - self.start


@dataclass(frozen=True)
class ReconstructionRules:
    max_gap_seconds: float = 120.0
    tolerance_seconds: float = 2.0
    rate_slack: float = 1.25


def _is_continuous(a: Sample, b: Sample, rules: ReconstructionRules) -> bool:
    if a.paused or b.kind == "seek":
        return False
    elapsed = (b.at - a.at).total_seconds()
    if elapsed <= 0 or elapsed > rules.max_gap_seconds:
        return False
    advanced = b.position - a.position
    if advanced <= 0:
        return False
    max_possible = elapsed * max(a.rate, b.rate) * rules.rate_slack + rules.tolerance_seconds
    return advanced <= max_possible


def _same_state(segment: Segment, sample: Sample) -> bool:
    return (
        segment.rate == sample.rate
        and segment.subtitles_on == sample.subtitles_on
        and segment.subtitle_language == sample.subtitle_language
    )


def build_segments(
    samples: Iterable[Sample],
    rules: ReconstructionRules = ReconstructionRules(),
    max_position: float | None = None,
) -> list[Segment]:
    """Convierte muestras de una sesión en segmentos [start, end] reproducidos.

    Pares consecutivos contiguos y con el mismo estado (velocidad y subtítulos) se
    unen en un solo segmento. `max_position` (duración del video) recorta posiciones imposibles.
    """
    ordered = sorted(samples, key=lambda s: s.seq)
    segments: list[Segment] = []
    current: Segment | None = None

    for a, b in zip(ordered, ordered[1:]):
        if not _is_continuous(a, b, rules):
            current = None
            continue

        start, end = a.position, b.position
        if max_position is not None:
            end = min(end, max_position)
            if end <= start:
                current = None
                continue

        elapsed = (b.at - a.at).total_seconds()
        # Tiempo real: lo que tarda ese contenido a esa velocidad, sin superar lo transcurrido.
        wall = min(elapsed, (end - start) / a.rate)

        if current and abs(current.end - start) < 1e-6 and _same_state(current, a):
            current.end = end
            current.wall_seconds += wall
        else:
            current = Segment(
                start=start,
                end=end,
                rate=a.rate,
                started_at=a.at,
                wall_seconds=wall,
                subtitles_on=a.subtitles_on,
                subtitle_language=a.subtitle_language,
            )
            segments.append(current)

    return segments


def merge_intervals(intervals: Iterable[tuple[float, float]]) -> list[tuple[float, float]]:
    """[(0, 600), (300, 900), (1000, 1100)] -> [(0, 900), (1000, 1100)]"""
    merged: list[tuple[float, float]] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def union_length(intervals: Iterable[tuple[float, float]]) -> float:
    return sum(end - start for start, end in merge_intervals(intervals))


def _overlap(start: float, end: float, covered: Sequence[tuple[float, float]]) -> float:
    return sum(max(0.0, min(end, c_end) - max(start, c_start)) for c_start, c_end in covered)


def dedup_segments(segments: Iterable[Segment]) -> list[tuple[Segment, float, float]]:
    """Para cada segmento, cuánto contenido NUEVO aporta: [(segmento, content, wall), ...].

    Se recorren en orden cronológico: la primera vez que se vio un tramo es la que
    cuenta (con su velocidad y sus subtítulos); repeticiones posteriores no suman.
    """
    covered: list[tuple[float, float]] = []
    contributions: list[tuple[Segment, float, float]] = []
    for seg in sorted(segments, key=lambda s: s.started_at):
        new = seg.length - _overlap(seg.start, seg.end, covered)
        if new > 0:
            contributions.append((seg, new, new / seg.rate))
        covered = merge_intervals([*covered, (seg.start, seg.end)])
    return contributions


def dedup_coverage(segments: Iterable[Segment]) -> tuple[float, float]:
    """(content_seconds, wall_seconds) contando cada segundo del video una sola vez."""
    contributions = dedup_segments(segments)
    return sum(c for _, c, _ in contributions), sum(w for _, _, w in contributions)
