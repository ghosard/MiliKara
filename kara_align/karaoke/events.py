"""ASS primitives shared by the renderer and the sweep engines.

The small, leaf-level pieces: time and colour formatting, text escaping, the chunk model the
sweep works on, and the animated-clip builders a moving sweep is made of.  They live here so a
sweep engine (``karaoke/sweep``) can be written without importing the whole renderer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


REF_WIDTH = 1920  # style pixel values are defined for a frame this wide; other widths scale

DEFAULT_SIZE = (1920, 1080)

PAUSE_HIDE_MS = 6000  # a pause inside a line at least this long hides the line meanwhile

FEATHER_STEPS = 6  # strips the soft sweep edge is drawn with (timing.sweep_feather_px)

def _rgb(hex_rgb: str) -> tuple[str, str, str]:
    """#RRGGBB → ("RR", "GG", "BB"); anything else is white (styles are validated, this is a last guard)."""
    h = (hex_rgb or "").strip().lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", h):
        h = "FFFFFF"
    h = h.upper()
    return h[0:2], h[2:4], h[4:6]

def bgr_tag(hex_rgb: str) -> str:
    """#RRGGBB → &HBBGGRR& (colour override tag value)."""
    r, g, b = _rgb(hex_rgb)
    return f"&H{b}{g}{r}&"

_bgr_tag = bgr_tag

def ass_time(ms: float) -> str:
    cs = max(0, int(round(ms / 10)))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, c = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{c:02d}"

_BREAKS = {c: " " for c in [*range(0x00, 0x20), 0x7F, 0x85, 0x2028, 0x2029]}

_ESCAPE = str.maketrans({**_BREAKS, "\\": "＼", "{": "｛", "}": "｝"})

def escape_text(s: str) -> str:
    """User text as literal ASS text.  libass has no escape for a backslash (``\\\\`` is shown
    as two, and a trailing one before the next ``{\\k..}`` breaks the tag), and ``\\N`` /
    ``\\n`` / ``\\h`` are line breaks / spaces: backslashes and braces become full-width.
    Line breaks of any kind (``\\n``, ``\\r``, U+2028 …) and other control characters become
    spaces (an event is one line of the file)."""
    return s.translate(_ESCAPE)

@dataclass
class Part:
    text: str
    start: Optional[int]
    end: Optional[int]

@dataclass
class Chunk:
    base: list[Part]  # lyric text pieces with their own karaoke timing
    ruby: list[Part] = field(default_factory=list)  # empty = no ruby
    wrap_before: bool = False  # the first chunk of a segment the AI suggested a line break before
    singers: tuple[int, ...] = ()  # who sings it (Line.singers / singer_spans); () = the style's own colours

    @property
    def base_text(self) -> str:
        return "".join(p.text for p in self.base)

    @property
    def ruby_text(self) -> str:
        return "".join(p.text for p in self.ruby)

Move = tuple[int, int, float]  # the sweep edge goes to x over [from, to] ms (to = from + 1: a jump)

def karaoke_tags(parts: list[Part], t0: int, tag: str) -> str:
    """The \\k / \\kf tags of a chunk, in centiseconds from the event's start."""
    out, cursor = [], 0
    for p in parts:
        s = int(round((p.start - t0) / 10)) if p.start is not None else cursor
        e = int(round((p.end - t0) / 10)) if p.end is not None else s
        s = max(s, cursor)
        e = max(e, s)
        if s > cursor:
            out.append(f"{{\\k{s - cursor}}}")
        out.append(f"{{\\{tag}{e - s}}}{escape_text(p.text)}")
        cursor = e
    return "".join(out)


Rect = tuple[float, float, float, float]  # x0, y0, x1, y1

def sweep_time(segs: list[tuple[int, int, float, float]], x: float, instant: bool) -> int:
    """When the sweep of piece_segments() reaches ``x`` (ms from t0): before the chunk its
    start, past it its end."""
    for s, e, xa, xb in segs:
        if x <= xa:
            return s
        if x < xb:
            return s if instant or xb <= xa else int(s + (e - s) * (x - xa) / (xb - xa))
    return segs[-1][1] if segs else 0

def sweep_moves(segs: list[tuple[int, int, float, float]], hi: float, instant: bool) -> list[Move]:
    """How the sweep edge over a chunk moves (from x = 0: nothing sung).

    Nothing is sung before the chunk's first piece starts; then the edge jumps to the lyric's
    left edge (so a reading reaching further left turns sung there at once), follows the
    lyric's sweep, and when the last piece is done jumps to ``hi`` (the right edge of what
    is drawn: a reading wider than its lyric turns sung completely).  Lyric and ruby of a
    chunk follow the same sweep over the lyric, so both are cut by one vertical line meanwhile."""
    moves: list[Move] = []
    if not segs:
        return moves
    cur: Optional[float] = None
    for i, (s, e, xa, xb) in enumerate(segs):
        if i == len(segs) - 1 and (instant or e <= s):
            xb = max(xb, hi)
        if cur is None or abs(xa - cur) > 0.5:  # on to the next piece
            moves.append((s, s + 1, xa))
        moves.append((s, e, xb) if not instant and e > s else (s, s + 1, xb))
        cur = xb
    last_e = segs[-1][1]
    if hi > (cur or 0) + 0.5:
        moves.append((last_e, last_e + 1, hi))
    return moves

def sweep_clip(segs: list[tuple[int, int, float, float]], hi: float, instant: bool, H: int,
               inverse: bool = False) -> str:
    """An animated \\clip (``inverse``: \\iclip, the rest) whose right edge is the sweep over one chunk
    (sweep_moves())."""
    name = "iclip" if inverse else "clip"

    def clip(x: float) -> str:
        return f"\\{name}(0,0,{int(round(x))},{H})"

    if not segs:
        return clip(hi)
    return clip(0) + "".join(f"\\t({a},{b},{clip(x)})" for a, b, x in sweep_moves(segs, hi, instant))

def sweep_strip_clip(segs: list[tuple[int, int, float, float]], hi: float, instant: bool, H: int,
                     dx0: float, dx1: float) -> str:
    """An animated \\clip that follows the sweep over one chunk, shifted by ``dx0`` on its left and
    ``dx1`` on its right: one strip of a soft (feathered) sweep edge."""
    def clip(x: float) -> str:
        a = max(0, int(round(x + dx0)))
        b = max(0, int(round(x + dx1)))
        return f"\\clip({a},0,{b},{H})"

    if not segs:
        return clip(hi)
    return clip(0) + "".join(f"\\t({a},{b},{clip(x)})" for a, b, x in sweep_moves(segs, hi, instant))

def band_clip(moves: list[Move], rect: Rect, sung: bool, hi: float) -> str:
    """An animated rectangular \\clip showing one band (``rect``) of a chunk: its part left of the sweep
    edge (``sung``) or right of it.  The two meet at the same rounded x, and neighbouring bands share
    their rounded edges, so every pixel of the chunk comes from exactly one of them."""
    x0, y0, x1, y1 = (int(round(v)) for v in rect)

    def at(x: float) -> int:
        return min(max(int(round(x)), x0), x1)

    def clip(x: float) -> str:
        e = at(x)
        return f"\\clip({x0},{y0},{e},{y1})" if sung else f"\\clip({e},{y0},{x1},{y1})"

    if not moves:
        return clip(hi)
    out, cur = [clip(0)], 0.0
    for a, b, x in moves:
        if b - a <= 1 or abs(x - cur) < 1e-9:  # a jump
            if at(x) != at(cur):
                out.append(f"\\t({a},{b},{clip(x)})")
            cur = x
            continue
        # a steady move from cur to x: only the stretch inside the band animates this clip
        lo, hi_ = sorted((cur, x))
        cuts = [a, b] + [int(round(a + (b - a) * (edge - cur) / (x - cur)))
                         for edge in (x0, x1) if lo < edge < hi_]
        cuts = sorted(set(cuts))
        for ta, tb in zip(cuts, cuts[1:]):
            xa = cur + (x - cur) * (ta - a) / (b - a)
            xb = cur + (x - cur) * (tb - a) / (b - a)
            if at(xa) != at(xb):
                out.append(f"\\t({ta},{max(tb, ta + 1)},{clip(xb)})")
        cur = x
    return "".join(out)

@dataclass
class Band:
    """One copy of a chunk's text for singers: in ``rect`` (None: all of it), in singer ``singer``'s
    colours (0: the style's own) or, for a gradient strip, in ``colors`` written into the event."""

    rect: Optional[Rect]
    singer: int
    colors: Optional[dict[str, str]] = None
    blend: bool = False

BASE_BAND = Band(None, 0)

L_FX, L_TRANS_GLOW, L_TRANS, L_GLOW, L_GLOW_SUNG, L_MAIN, L_RUBY = 0, 1, 2, 3, 4, 5, 6


_karaoke = karaoke_tags  # the name the renderer grew up with
