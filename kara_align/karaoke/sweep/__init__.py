"""Sweep engines: how the light moves across a line.

    engine_for(style) -> a SweepEngine with .emit(ctx, job)

Registered engines:

    kf       per-syllable ``\\kf`` / ``\\k`` fill (default; libass draws the edge)
    clip     one moving clip cutting the lyric and its reading with a single line
    feather  that clip with a soft, blended edge (timing.sweep_feather_px)

The style picks one with ``timing.sweep_engine``; ``"auto"`` keeps the rule styles were
rendered with before the engines existed — ``kf`` normally, the moving clip when the reading
shares the lyric's sweep line (``ruby.sweep == "base"``) or a soft edge was asked for.
"""

from __future__ import annotations

from typing import Optional

from .base import LineJob, SweepCtx, SweepEngine
from .clip import ClipSweep
from .feather import FeatherSweep
from .kf import KfSweep

ENGINES: dict[str, SweepEngine] = {e.name: e for e in (KfSweep(), ClipSweep(), FeatherSweep())}


def get(name: str) -> SweepEngine:
    try:
        return ENGINES[name]
    except KeyError:
        raise KeyError(f"unknown sweep engine: {name} (have {', '.join(sorted(ENGINES))})") from None


def available() -> list[str]:
    return sorted(ENGINES)


def engine_for(style, *, has_ruby: bool = False) -> SweepEngine:
    """The engine this style asks for, resolving ``"auto"`` to the historical rule."""
    timing = style.timing
    choice = getattr(timing, "sweep_engine", "auto")
    if choice in ENGINES:
        return ENGINES[choice]
    feather = getattr(timing, "sweep_feather_px", 0)
    shared_line = getattr(style.ruby, "sweep", "own") == "base" and has_ruby
    if feather > 0:
        return ENGINES["feather"]
    return ENGINES["clip"] if shared_line else ENGINES["kf"]


__all__ = ["ENGINES", "LineJob", "SweepCtx", "SweepEngine", "available", "engine_for", "get"]
