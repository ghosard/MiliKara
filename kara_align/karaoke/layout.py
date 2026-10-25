"""Where every piece of a line sits, and when it is on screen.

The measurement half of the renderer: split each lyric line into chunks (a chunk is what the
sweep draws and the layout moves), measure them, decide where the line breaks, which of the
two rows it takes, how far in from its edge it sits, when it appears and disappears, how a
translation lines up with it — everything except drawing.

``ass.build_ass`` reads this and only writes events; nothing here knows about ASS tags.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Optional

from ..models import KaraokeStyle, Line, Segment
from ..reading.japanese import is_kanji, to_hiragana, to_katakana
from .events import PAUSE_HIDE_MS, Chunk, Part

_PUNCT = set(" \u3000、。，,.!?！？…‥・~〜～♪☆★「」『』()（）[]［］【】-—")
_OPENING = set("「『(（[［【")

@dataclass
class LaidLine:
    line: Line
    chunks: list[Chunk]
    start: int  # first sung time
    end: int  # last sung time
    translation: Optional[str] = None
    show_from: int = 0
    show_to: int = 0
    slot: int = 0  # row: 0 … lines-1 in the block; beyond it (-1, -2 / lines, lines+1) extra rows
    units: list[tuple[int, int]] = field(default_factory=list)  # timed units (start, end), for pauses
    # a long line wrapped into pieces (wrap_line): the translation goes with the first piece and is
    # shown until the last one is sung
    trans_until: Optional[int] = None
    countdown_ms: int = 0  # countdown dots before it (plan_countdowns): the time they need; 0 = none

    @property
    def text(self) -> str:
        return "".join(c.base_text for c in self.chunks)

def has_kanji(s: str) -> bool:
    return any(is_kanji(c) for c in s)

def _ruby_text(reading: str, script: str, romaji: Optional[str]) -> str:
    if script == "katakana":
        return to_katakana(to_hiragana(reading))
    if script == "romaji":
        return romaji if romaji is not None else reading
    return to_hiragana(reading)

def _kana_split(runs: list[tuple[str, bool]], reading: str) -> Optional[list[int]]:
    """Reading lengths of each run: kana runs match literally, kanji runs take at least one kana.

    Every valid split is tried.  The one where the kanji readings are the most even
    (the fewest kana on the busiest kanji) wins; if two splits are equally even the
    split is ambiguous (物の怪 / もののけ: も|の|のけ or もの|の|け) and None is returned.
    """
    found: list[list[int]] = []

    def walk(i: int, pos: int, acc: list[int]) -> None:
        if len(found) > 64:
            return
        if i == len(runs):
            if pos == len(reading):
                found.append(acc)
            return
        text, is_k = runs[i]
        if not is_k:
            kana = to_hiragana(text)
            if reading.startswith(kana, pos):
                walk(i + 1, pos + len(kana), acc + [len(kana)])
            return
        # leave at least one kana for every later kanji run and the literal kana runs
        rest = sum(1 if k else len(t) for t, k in runs[i + 1:])
        for n in range(1, len(reading) - pos - rest + 1):
            walk(i + 1, pos + n, acc + [n])

    walk(0, 0, [])
    if not found:
        return None

    def cost(lens: list[int]) -> float:
        return max(n / len(t) for n, (t, k) in zip(lens, runs) if k)

    best = min(cost(x) for x in found)
    top = [x for x in found if abs(cost(x) - best) < 1e-9]
    return top[0] if len(top) == 1 else None

def _split_affixes(seg: Segment) -> list[tuple[str, list]]:
    """Split the kana of a kanji word off its kanji: [(surface, units)].

    Handles a kana prefix, okurigana and kana between kanji (笑い合え →
    笑 い 合 え), so ruby sits only over kanji.  Only splits where the
    reading boundary falls on a unit boundary, so every piece keeps whole
    units (and their times); otherwise the word stays one piece.  A split the
    reading allows in more than one way is not made (_kana_split()).
    """
    surface, units = seg.surface, seg.units
    if not has_kanji(surface) or not units:
        return [(surface, units)]
    reading = "".join(to_hiragana(u.reading) for u in units)
    runs = [(m.group(), bool(m.group(1))) for m in re.finditer(r"([^ぁ-ヿ]+)|([ぁ-ヿ]+)", surface)]
    if len(runs) == 1:
        return [(surface, units)]
    lens = _kana_split(runs, reading)
    if lens is None:
        return [(surface, units)]
    edges = [0]
    for n in lens:
        edges.append(edges[-1] + n)
    unit_edges = [0]
    for u in units:
        unit_edges.append(unit_edges[-1] + len(u.reading))
    pieces: list[tuple[str, list]] = []
    ui = 0
    text_parts: list[str] = []
    for (text, _), end in zip(runs, edges[1:]):
        text_parts.append(text)
        if end not in unit_edges:
            continue  # the boundary cuts a unit: keep this run with the next one
        k = unit_edges.index(end)
        pieces.append(("".join(text_parts), units[ui:k]))
        ui, text_parts = k, []
    if text_parts or ui < len(units):
        return [(surface, units)]
    return pieces

def build_chunks(line: Line, times: dict[str, tuple[Optional[int], Optional[int]]], style: KaraokeStyle,
                 romaji: dict[str, str]) -> list[Chunk]:
    ruby_cfg = style.ruby
    chunks: list[Chunk] = []
    # who sings each character (only when the line says so; the segments spell out the line's text)
    chars = None
    if line.singers or line.singer_spans:
        from ..lyrics.singers import effective

        chars = effective(line)
        if "".join(s.surface for s in line.segments) != line.text:
            chars = [tuple(line.singers)] * sum(len(s.surface) for s in line.segments)
    pos = 0

    def sung_by(n: int) -> tuple[int, ...]:
        if chars is None:
            return ()
        from ..lyrics.singers import range_singers

        return range_singers(chars, pos, pos + n, line.text if len(line.text) == len(chars) else None)

    def shows_ruby(seg: Segment) -> bool:
        return (ruby_cfg.enabled and seg.lang == "ja" and bool(seg.units)
                and (has_kanji(seg.surface) or ruby_cfg.target == "all" or any(c.isdigit() for c in seg.surface)))

    ruby_before = False  # the word before shows its ruby
    for seg in line.segments:
        if seg.hidden and ruby_before:  # a reading in brackets: the word before shows it as ruby
            pos += len(seg.surface)
            continue
        ruby_before = shows_ruby(seg)
        if not seg.units:  # (also hidden brackets when the ruby is off: the reading stays readable)
            chunks.append(Chunk([Part(seg.surface, None, None)], wrap_before=seg.wrap_before,
                                singers=sung_by(len(seg.surface))))
            pos += len(seg.surface)
            continue
        pieces = _split_affixes(seg) if seg.lang == "ja" else [(seg.surface, seg.units)]
        for n_piece, (surface, units) in enumerate(pieces):
            t = [times.get(u.id, (None, None)) for u in units]
            kanji = has_kanji(surface)
            hira = to_hiragana(surface)
            readings = [u.reading for u in units]
            # kana (or latin) whose units map 1:1 onto the surface: per-unit karaoke
            if not kanji and seg.lang == "ja" and hira == "".join(readings):
                base, at = [], 0
                for u, (s, e) in zip(units, t):
                    base.append(Part(surface[at:at + len(u.reading)], s, e))
                    at += len(u.reading)
            elif all(u.surface for u in units) and "".join(u.surface for u in units) == surface:
                base = [Part(u.surface, s, e) for u, (s, e) in zip(units, t)]
            else:
                starts = [s for s, _ in t if s is not None]
                ends = [e for _, e in t if e is not None]
                base = [Part(surface, min(starts) if starts else None, max(ends) if ends else None)]
            ruby: list[Part] = []
            # "仅汉字" counts digits as kanji: how 24 is sung is not written either
            wants = shows_ruby(seg) and (kanji or ruby_cfg.target == "all" or any(c.isdigit() for c in surface))
            if wants:
                # a reading the lyrics write in katakana (宿敵 → ライバル) stays katakana over hiragana ruby
                script = "katakana" if seg.katakana and ruby_cfg.script == "hiragana" else ruby_cfg.script
                ruby = [Part(_ruby_text(u.reading, script, romaji.get(u.id)), s, e)
                        for u, (s, e) in zip(units, t)]
                if "".join(p.text for p in ruby) == surface:
                    ruby = []  # e.g. hiragana ruby over hiragana
            chunks.append(Chunk(base, ruby, wrap_before=seg.wrap_before and n_piece == 0,
                                singers=sung_by(len(surface))))
            pos += len(surface)
    _fill_missing_times(chunks)
    return chunks

def _fill_missing_times(chunks: list[Chunk]) -> None:
    """Untimed parts (punctuation, unaligned units) highlight instantly at
    their neighbour's boundary; this only affects display, never the data."""
    seq = [p for c in chunks for p in c.base]
    last = None
    for p in seq:
        if p.start is None or p.end is None:
            p.start = p.end = last
        else:
            last = p.end
    nxt = None
    for p in reversed(seq):
        if p.start is None:
            p.start = p.end = nxt
        else:
            nxt = p.start
    for c in chunks:
        for p in c.ruby:
            if p.start is None or p.end is None:
                p.start = c.base[0].start
                p.end = c.base[-1].end

def _blank(c: Chunk) -> bool:
    return not c.base_text.strip()

def _trim(piece: list[Chunk]) -> list[Chunk]:
    """A piece of a wrapped line without the space at its ends (copies; the line keeps its chunks)."""
    piece = list(piece)
    for idx, strip in ((0, str.lstrip), (-1, str.rstrip)):
        c = piece[idx]
        part = c.base[idx]
        text = strip(part.text)
        if text != part.text and text:
            base = list(c.base)
            base[idx] = Part(text, part.start, part.end)
            piece[idx] = Chunk(base, c.ruby, c.wrap_before, c.singers)
    return piece

def wrap_line(ll: LaidLine, extent, avail: float, mode: str, max_pieces: int = 4) -> list[LaidLine]:
    """A line wider than ``avail`` (``extent``: chunks -> px) split into pieces that fit, each sung
    one after the other (they take turns in the rows like lines).  Breaks go between chunks (a chunk
    is a word or a kana run): with ``mode == "ai"`` where the AI readings suggested one first; else
    after punctuation / at a space, outside brackets; else the most even split between words.  A
    piece keeps at least 3 characters.  The translation goes with the first piece and lasts until
    the last one is sung."""
    if mode == "off" or len(ll.chunks) < 2 or extent(ll.chunks) <= avail:
        return [ll]
    chunks = ll.chunks
    depth, d = [], 0
    for c in chunks:  # bracket depth before each chunk
        depth.append(d)
        for ch in c.base_text:
            d = d + 1 if ch in _OPENING else max(0, d - 1) if ch in "」』)）]］】" else d
    best = None
    for b in range(1, len(chunks)):
        left, right = [c for c in chunks[:b]], [c for c in chunks[b:]]
        while left and _blank(left[-1]):
            left.pop()
        while right and _blank(right[0]):
            right.pop(0)
        if not left or not right:
            continue
        left, right = _trim(left), _trim(right)
        lt, rt = "".join(c.base_text for c in left).strip(), "".join(c.base_text for c in right).strip()
        if len(lt) < 3 or len(rt) < 3:
            continue
        prev, nxt = chunks[b - 1].base_text, chunks[b].base_text
        if nxt[:1] in "、。，,.!?！？…‥ー" or prev[-1:] in _OPENING:
            rank = 9  # never before closing punctuation or after an opening bracket, if avoidable
        elif mode == "ai" and chunks[b].wrap_before:
            rank = 0
        else:
            space = not prev.strip() or not nxt.strip() or prev[-1:].isspace() or nxt[:1].isspace()
            sentence = prev.rstrip()[-1:] in "、。，,.!?！？…‥"
            natural = space or sentence or prev[-1:] in _PUNCT or nxt[:1] in _PUNCT
            rank = (1 if space else 2 if sentence else 3 if natural else 5) if depth[b] == 0 else (4 if natural else 5)
        wl, wr = extent(left), extent(right)
        if rank and max(wl, wr) > 0.72 * (wl + wr):
            rank += 3  # very uneven: only when nothing better fits (the AI's own choice is kept)
        # the AI's choice is taken even when a piece is still too wide (that piece wraps again)
        fits = rank == 0 or (wl <= avail and wr <= avail)
        key = (not fits, rank, max(wl, wr))
        if best is None or key < best[0]:
            best = (key, left, right)
    if best is None:
        return [ll]
    out = []
    for piece in (best[1], best[2]):
        times = [p for c in piece for p in c.base if p.start is not None and p.end is not None]
        if not times:
            return [ll]
        a, z = min(p.start for p in times), max(p.end for p in times)
        sub = LaidLine(ll.line, piece, a, z, units=[u for u in ll.units if a <= u[0] <= z])
        out.append(sub)
    out[0].translation, out[0].trans_until = ll.translation, max(x.end for x in out)
    if max_pieces > 2:  # a piece still too wide: split again
        pieces: list[LaidLine] = []
        for i, x in enumerate(out):
            more = wrap_line(x, extent, avail, mode, max_pieces - 1) if extent(x.chunks) > avail else [x]
            pieces += more
        pieces[0].translation, pieces[0].trans_until = ll.translation, max(x.end for x in pieces)
        for x in pieces[1:]:
            x.translation, x.trans_until = None, None
        out = pieces
    return out

def plan_countdowns(lines: list[LaidLine], style: KaraokeStyle) -> None:
    """Which lines (sorted by start) get countdown dots: the first line (``countdown.intro``) and a
    line after a pause of at least ``min_gap_ms`` since everything before it was sung
    (``countdown.interlude``), unless the line itself says otherwise (``Line.countdown``).  Only
    the first piece of a wrapped line."""
    cd = style.countdown
    seen: set[str] = set()
    sung_to: Optional[int] = None
    for ll in lines:
        if ll.line.id not in seen:
            seen.add(ll.line.id)
            want = ll.line.countdown
            if want is None:
                want = cd.intro if sung_to is None else (cd.interlude and ll.start - sung_to >= cd.min_gap_ms)
            ll.countdown_ms = cd.dots * 1000 if want else 0
        sung_to = ll.end if sung_to is None else max(sung_to, ll.end)

def countdown_dots(appear: float, t0: float, n: int) -> list[float]:
    """When each of ``n`` dots (left to right) goes, for a line shown from ``appear`` and sung from
    ``t0``: the rightmost first, one a second, the leftmost as the singing starts; evenly over a shorter
    wait.  [] when there is hardly any time to show them."""
    wait = t0 - appear
    if wait < 300 or n < 1:
        return []
    step = min(1000.0, wait / n)
    return [t0 - i * step for i in range(n)]

def schedule(lines: list[LaidLine], style: KaraokeStyle) -> int:
    """Give each line a display window and a slot (rows stacked on screen); return how many
    lines had to go to an extra row.

    Lines take the rows in turn; a line with countdown dots takes the top row (when it is free) and
    the turn goes on from there.  A line appears ``lead_in_ms`` before its first syllable
    (with early show: as soon as its row is free, at most ``early_max_ms`` ahead), but never
    before the previous line in its row has gone; that line's hold is cut short if needed so
    each line is visible at least 0.2 s before it is sung.  A row whose line is still being
    sung is never taken: the line goes to the row that frees first, and when every row is
    busy (lines sung at the same time: a duet, a backing vocal inside a long line) to an
    extra row beyond the block (above it at the bottom of the frame, below it at the top).
    No line is ever dropped.
    """
    tm = style.timing
    n = max(1, style.layout.lines)
    lead, hold = max(0, tm.lead_in_ms), max(0, tm.hold_ms)
    early = max(lead, tm.early_max_ms) if tm.early_show else lead
    step = -1 if style.layout.position == "bottom" else 1  # extra rows go away from the frame edge
    last: dict[int, LaidLine] = {}
    turn = 0
    extra = 0
    for ll in lines:
        ll.show_to = ll.end + hold

        # a line with countdown dots appears as its countdown begins (the first dot goes a second later),
        # not earlier with the early show: the dots never sit still waiting
        lead_ll = ll.countdown_ms or lead
        early_ll = ll.countdown_ms or early

        def appear(slot: int) -> int:
            prev = last.get(slot)
            free_at = prev.show_to if prev is not None else 0
            t = max(0, ll.start - early_ll, min(free_at, ll.start - lead_ll))
            if prev is not None and prev.show_to > t:
                t = max(t, min(prev.show_to, max(prev.end, ll.start - 200)))
            return t

        free = [s for s in range(n) if s not in last or last[s].end <= ll.start]
        natural = turn % n
        if free:
            if ll.countdown_ms and 0 in free:
                # a line with countdown dots takes the top row: its dots sit above it, where no other
                # line is (in a lower row they would cover the line above); the next ones go on in turn
                slot = 0
            elif natural in free and appear(natural) <= ll.start - 200:
                slot = natural
            else:
                slot = min(free, key=lambda s: (appear(s), s != natural, s))
        else:
            extra += 1
            rows = sorted((s for s in last if not 0 <= s < n), key=abs)  # nearest the block first
            slot = next((s for s in rows if last[s].end <= ll.start), None)
            if slot is None:
                slot = (-1 if step < 0 else n) + step * len(rows)
        ll.slot = slot
        ll.show_from = appear(slot)
        prev = last.get(slot)
        if prev is not None:
            prev.show_to = min(prev.show_to, ll.show_from)
        last[slot] = ll
        if 0 <= slot < n:
            turn = slot + 1
    return extra

def visible_spans(ll: LaidLine, style: KaraokeStyle) -> list[tuple[int, int]]:
    """Display intervals of a scheduled line.

    Normally one interval.  If the line itself contains a long pause (the
    singer stops mid-line for an interlude), the line is hidden during it:
    it stays ``hold_ms`` after the last sung part before the pause and comes
    back ``lead_in_ms`` before singing resumes.
    """
    tm = style.timing
    min_pause = max(PAUSE_HIDE_MS, tm.hold_ms + tm.lead_in_ms + 2000)
    times = ll.units or [(p.start, p.end) for c in ll.chunks for p in c.base
                         if p.start is not None and p.end is not None]
    spans: list[tuple[int, int]] = []
    a = ll.show_from
    reach: Optional[int] = None
    for s, e in sorted(times):
        if reach is not None and s - reach >= min_pause:
            spans.append((a, reach + tm.hold_ms))
            a = s - tm.lead_in_ms
        reach = e if reach is None else max(reach, e)
    spans.append((a, ll.show_to))
    return [(x, y) for x, y in spans if y > x]

def alternate_insets(laid: list[LaidLine], geom: list[tuple], indent: float, avail: float) -> list[float]:
    """How far the left / right rows sit in from their margins (the alternating layout's indent).

    One indent for the whole song, so every left row starts at the same x and every right row ends
    at the same x.  It is the style's indent, reduced once as far as the lyrics need: every line
    must fit (a long line needs a smaller indent), and a left and a right row shown together keep
    the staircase — the upper (left) one starts and ends no further right than the lower (right)
    one, i.e. ``2 · indent <= avail − the longer line's extent``."""
    ext = [g[1] + g[2] + g[3] for g in geom]
    # (lines wider than the room between the margins reach toward the edges instead: no indent,
    # and they do not narrow everyone else's)
    side = [i for i, g in enumerate(geom) if g[5] != "center" and not (len(g) > 6 and g[6])]
    inset = indent
    for i in side:
        inset = min(inset, max(0.0, avail - ext[i]))  # the line itself must fit
        for j in side:
            if geom[j][5] == geom[i][5] or laid[j].show_to <= laid[i].show_from or laid[j].show_from >= laid[i].show_to:
                continue
            inset = min(inset, max(0.0, avail - max(ext[i], ext[j])) / 2)
    inset = max(0.0, inset)
    return [0.0 if g[5] == "center" or (len(g) > 6 and g[6]) else inset for g in geom]

def _sung_within(ll: LaidLine, a: float, b: float) -> tuple[float, float]:
    """First start and last end of the line's singing inside the display span [a, b)."""
    times = ll.units or [(p.start, p.end) for c in ll.chunks for p in c.base
                         if p.start is not None and p.end is not None]
    inside = [(s, e) for s, e in times if a <= s < b] or [(ll.start, ll.end)]
    return min(s for s, _ in inside), max(e for _, e in inside)

def fade_tag(fade_in: float, fade_out: float, t_from: float, t_to: float, sung_from: float, sung_to: float) -> str:
    """\\fad shortened so the line never fades while it is sung (a line right after another
    in the same row has almost no time to ease in)."""
    fi = int(max(0, min(fade_in, sung_from - t_from)))
    fo = int(max(0, min(fade_out, t_to - sung_to)))
    return f"\\fad({fi},{fo})" if (fi or fo) else ""

def translation_windows(laid: list[LaidLine], style: KaraokeStyle) -> list[tuple[LaidLine, int, int]]:
    """When each line's translation is shown as a single line (not under its lyric).

    One translation at a time, following the singing: from shortly before a line
    is sung until it is done (plus the hold), cut short when the next line starts.
    """
    tm = style.timing
    lead = min(tm.lead_in_ms, 800)
    out: list[tuple[LaidLine, int, int]] = []
    lines = [ll for ll in laid if ll.translation]
    prev_to = 0
    for i, ll in enumerate(lines):
        end = max(ll.end, ll.trans_until or 0)  # (a wrapped line: until its last piece is sung)
        t_to = end + tm.hold_ms
        if i + 1 < len(lines):
            t_to = min(t_to, max(lines[i + 1].start - lead, end))
        t_from = max(ll.start - lead, prev_to, 0)
        if t_to > t_from:
            out.append((ll, t_from, t_to))
            prev_to = t_to
    return out

def translation_placements(laid: list[LaidLine], style: KaraokeStyle, W: int, H: int, block_top: float,
                           block_h: float, size: float, measurer, margin_h: float,
                           margin_v: float) -> list[tuple[int, int, str, str, tuple[float, float, float, float], LaidLine]]:
    """(from, to, position tags, text, (x0, y0, x1, y1), line): the translation as one line at the
    other edge of the frame or just outside the lyric block."""
    lay = style.layout
    bottom = lay.position == "bottom"
    gap = size * 0.6
    if style.translation.position == "opposite":
        an, y = (8, margin_v) if bottom else (2, H - margin_v)
    else:  # "block": right outside the lyric block, on the side away from the edge
        an, y = (2, block_top - gap) if bottom else (8, block_top + block_h + gap)
    avail = max(1.0, W - 2 * margin_h)
    out = []
    for ll, t0, t1 in translation_windows(laid, style):
        text = ll.translation or ""
        w = measurer.width(text) or 1.0
        fs = f"\\fscx{avail / w * 100:.1f}\\fscy{avail / w * 100:.1f}" if w > avail else ""
        w, h = min(w, avail), size * min(1.0, avail / w)
        box = (W / 2 - w / 2, y if an == 8 else y - h, W / 2 + w / 2, y + h if an == 8 else y)
        out.append((t0, t1, f"\\an{an}\\pos({W / 2:.1f},{y:.1f}){fs}", text, box, ll))
    return out

def chunk_widths(chunks: list[Chunk], m_main, m_ruby, ruby_size: float, fit: str) -> list[float]:
    """Width of each chunk on the line.

    A ruby wider than its lyric may overhang a neighbour that has no ruby of
    its own (usually kana) by up to one ruby character, and by no more than
    that neighbour is wide — half of it when a ruby on its other side overhangs
    it too — so two readings never meet, as in normal Japanese typesetting.  At
    the line's edges it may overhang by one ruby character (counted in the
    line's extent, ruby_overhang()).  With ``fit == "widen"`` the lyric is spaced
    out only by what is still needed; ``"overflow"`` never widens.
    """
    base = [m_main.width(c.base_text) for c in chunks]
    if fit != "widen" or m_ruby is None:
        return base
    pad = ruby_size * 0.1
    need = [max(0.0, m_ruby.width(c.ruby_text) + pad - bw) if c.ruby else 0.0 for c, bw in zip(chunks, base)]
    n = len(chunks)

    def room(i: int, j: int) -> float:
        """How far chunk i's ruby may reach over its neighbour j (-1 / n: the line's edge)."""
        if not 0 <= j < n:
            return ruby_size
        if chunks[j].ruby:
            return 0.0
        other = j - 1 if j < i else j + 1  # the neighbour's other side
        shared = 0 <= other < n and need[other] > 0
        return min(ruby_size, base[j] / 2 if shared else base[j])

    out = []
    for i, bw in enumerate(base):
        if need[i] <= 0:
            out.append(bw)
            continue
        # the ruby is centred, so the overhang is symmetric: limited by the tighter side
        allowance = min(room(i, i - 1), room(i, i + 1))
        out.append(bw + max(0.0, need[i] - 2 * allowance))
    return out

def ruby_overhang(chunks: list[Chunk], widths: list[float], m_ruby) -> tuple[float, float]:
    """How far the readings reach past the line's left and right edge (unscaled px)."""
    if m_ruby is None or not chunks:
        return 0.0, 0.0
    total = sum(widths)
    left = right = 0.0
    x = 0.0
    for c, w in zip(chunks, widths):
        if c.ruby:
            half = m_ruby.width(c.ruby_text) / 2
            left = max(left, half - (x + w / 2))
            right = max(right, x + w / 2 + half - total)
        x += w
    return max(0.0, left), max(0.0, right)

