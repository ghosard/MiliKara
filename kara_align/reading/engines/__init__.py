"""Reading engines: the steps that give every song its kana readings.

A reading is produced by a chain of engines, in order; each one may fill, override or
set aside (``hidden``) segments, and records where the reading came from in
``Segment.reading_source``.  Nothing later in the chain touches a segment an earlier
engine resolved (``manual`` / ``ai`` / ``annotation`` / ``confirmed``).

    rule       -> MeCab + UniDic lite (or the character-class fallback), pykakasi候选
    annotation -> the readings the LRC itself carries: 漢字(かな) becomes the reading of
                  the word before it and the bracket segment is hidden
    ai         -> a patch written by an AI (see reading/ai.py); applied out of band

Adding a source of readings = one class implementing ``ReadingEngine`` plus a
``register()`` call; nothing in the aligner or the renderer changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol

from ...models import LyricsDoc

DEFAULT_CHAIN = ("rule", "annotation")


@dataclass
class EngineResult:
    engine: str
    changed: int = 0
    messages: list[str] = field(default_factory=list)


@dataclass
class ReadingContext:
    doc: LyricsDoc
    overwrite_rule: bool = True
    # what the rule engine produced (kept for the service's PrepareReport compatibility)
    prepared: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)
    rederived: list[str] = field(default_factory=list)
    regrouped: list[str] = field(default_factory=list)


class ReadingEngine(Protocol):
    name: str

    def apply(self, ctx: ReadingContext) -> EngineResult:
        ...


_REGISTRY: dict[str, ReadingEngine] = {}


def register(engine: ReadingEngine) -> ReadingEngine:
    _REGISTRY[engine.name] = engine
    return engine


def available() -> list[str]:
    return sorted(_REGISTRY)


def get(name: str) -> ReadingEngine:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise KeyError(f"unknown reading engine: {name} (have {', '.join(available())})") from None


def chain(ctx: ReadingContext, names: Optional[tuple[str, ...]] = None) -> list[EngineResult]:
    """Run the engines in order; a failing engine does not stop the others."""
    out: list[EngineResult] = []
    for name in names or DEFAULT_CHAIN:
        try:
            out.append(get(name).apply(ctx))
        except Exception as e:  # one engine's problem must not cost the whole song its readings
            out.append(EngineResult(engine=name, messages=[f"{name}: {type(e).__name__}: {e}"]))
    return out


def _load() -> None:
    from . import annotation, rule  # noqa: F401  (registers themselves)


_load()
