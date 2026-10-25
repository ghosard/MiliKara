"""What a sweep engine is given, and what it may do.

A sweep engine draws one line's karaoke text: where the highlight is at each moment and what
the sung and unsung parts look like.  Everything else — measuring, wrapping, where the line
sits, which colours, when it appears — is decided before the engine is called, so adding a
way of sweeping the light is one file in ``engines/`` and one name in the style.

    from kara_align.karaoke.sweep import engine_for
    engine_for(style).emit(ctx, job)

``SweepCtx`` carries the few things every engine needs (the event writer, the glow, the
karaoke tag, the feather width); ``LineJob`` is the line being drawn, already resolved.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional, Protocol


@dataclass
class SweepCtx:
    """Everything an engine may use that belongs to the whole render, not to one line."""

    emit: Callable[..., None]                 # (layer, t_from, t_to, style, tags, body, fade, actor)
    band_look: Callable[..., tuple]           # (band, style name, unsung) -> (style, unsung, glow sung, glow unsung, actor)
    blend_tags: Callable[..., str]            # a gradient strip's own colours, written into the event
    cut_of: Callable[..., str]                # a singer band's rectangle as \\clip ("" for all of it)
    glow_tags: Callable[..., str]             # the override tags a glow copy is drawn with
    glow: Any                                 # KaraokeGlow (only ``enabled`` is read)
    bold: bool
    height: int                               # frame height, for the clips
    instant: bool                             # timing.highlight != "sweep": no movement, jumps
    feather: float                            # px of soft edge (already scaled to the frame)
    karaoke_tag: str                          # "kf" (fill) or "k" (jump at each syllable)
    mix: Callable[[str, str, float], str]     # blend two colours
    steps: int                                # strips a soft edge is drawn with


@dataclass
class LineJob:
    """One line to draw (the lyric, or its reading), with its resolved look."""

    layer: int
    t_from: float
    t_to: float
    style_name: str                # "KMain" / "KRuby"
    pos: str                       # \\an2\\pos(…) + the font size override
    text: str                      # the plain text of the line
    parts: list                    # Part list (per-syllable times), for \\kf / \\k
    unsung: str
    sung: Optional[str]
    font: str
    size: float
    glow_width: float
    with_glow: bool
    fad: str
    bands: tuple                   # (fill bands, glow bands)
    segs: Optional[list] = None    # piece_segments(): where the sweep runs, for clip engines
    hi: Optional[float] = None     # the right edge of the line, for clip engines


class SweepEngine(Protocol):
    name: str
    needs_segments: bool           # does it read ``job.segs`` (the moving-clip family)

    def emit(self, ctx: SweepCtx, job: LineJob) -> None:
        ...
