"""Karaoke subtitles as ASS, laid out chunk by chunk.

Every lyric line becomes a row of *chunks*.  A chunk is a piece of the line
that carries one ruby group (a kanji word and its reading) or plain text; kana
around kanji are split off so ruby sits only over kanji.  Widths are measured
with the fonts libass will draw with (``fonts.Measurer``), so the layout is
computed here: chunk widths (a reading may overhang a neighbour without ruby,
never another reading), the line's place in its row (alternating left / right,
shrunk to fit the margins), the row (``schedule``: lines take turns in
``layout.lines`` rows; a line sung while every row is still busy — a duet, a
backing vocal — gets an extra row beyond the block) and when it shows
(``lead_in`` / ``hold`` / early show, hidden across long pauses inside it).

Each chunk is written as its own positioned event (lyric) plus an optional ruby
event above it:

* ruby ``own``: both are ``\\kf`` / ``\\k`` karaoke events built from the unit
  times of the alignment result (manual edits included);
* ruby ``base`` (与歌词对齐): lyric and ruby are drawn twice — unsung, and sung
  cut by an animated ``\\clip`` at the lyric's sweep position (the unsung copy by
  the matching ``\\iclip``, so fades never show one through the other).  The
  ruby's cut follows the lyric's while it is over the lyric and covers the whole
  reading once its chunk is sung.

Around that: optional glow layers (a blurred border under the text), the
translation (under each line, or one line at an edge), per-syllable effects
(``effects``) and the song title card (``info``).  Fades are shortened so a line
never fades while it is sung.

Times written here are on the original audio timeline plus ``time_offset_ms``
(the audio start inside a video, for burn-in / use with that video), minus the
style's ``advance_ms``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from ..models import AlignmentResult, KaraokeStyle, Line, Project, Segment
from ..reading.japanese import is_kanji, to_hiragana, to_katakana
from .layout import (LaidLine, _sung_within, alternate_insets, build_chunks, chunk_widths, countdown_dots,
                     fade_tag, has_kanji, plan_countdowns, ruby_overhang, schedule, translation_placements,
                     translation_windows, visible_spans, wrap_line)
from .events import (BASE_BAND, DEFAULT_SIZE, FEATHER_STEPS, L_FX, L_GLOW, L_GLOW_SUNG, L_MAIN, L_RUBY,
                     L_TRANS, L_TRANS_GLOW, PAUSE_HIDE_MS, REF_WIDTH, Band, Chunk, Move, Part, Rect, _bgr_tag,
                     _karaoke, _rgb, ass_time, band_clip, bgr_tag, escape_text, sweep_clip, sweep_moves,
                     sweep_strip_clip, sweep_time)
from .sweep import LineJob, SweepCtx, engine_for
from .fonts import (BUNDLED_JP, BUNDLED_SC, HAN_FAMILIES, Measurer, bundled, covering_family, default_family,
                    installed, lacking, system_han_fallback)


# ---------------------------------------------------------------------------
# text helpers


def ass_color(hex_rgb: str, alpha_pct_transparent: float = 0) -> str:
    """#RRGGBB → &HAABBGGRR (alpha 0 = opaque)."""
    r, g, b = _rgb(hex_rgb)
    a = max(0, min(255, round(alpha_pct_transparent * 255 / 100)))
    return f"&H{a:02X}{b}{g}{r}"


# line / paragraph separators and other control characters: one space each


# ---------------------------------------------------------------------------
# chunk model


# ---------------------------------------------------------------------------
# scheduling and layout


# a lyric line may wrap after these (or before a space)


Box = tuple[float, float, float, float, float, float]  # shown from, to (ASS time), x0, y0, x1, y1


def piece_segments(c: Chunk, cx: float, width: float, t0: int, m_main) -> list[tuple[int, int, float, float]]:
    """Where and when the lyric's sweep runs over a chunk: [(start, end) ms from t0, x from, x to].

    The lyric's \\kf fills each piece from its left to its right edge over the piece's time
    (\\k: at once when it starts) and jumps over the space between pieces; ``width`` is the
    drawn width of the chunk's text centred at ``cx``.  The same centisecond timing as
    _karaoke()."""
    widths = [m_main.width(p.text) for p in c.base]
    norm = width / (sum(widths) or 1.0)
    x, cursor = cx - width / 2, 0
    segs: list[tuple[int, int, float, float]] = []
    for p, pw in zip(c.base, widths):
        s = int(round((p.start - t0) / 10)) if p.start is not None else cursor
        e = int(round((p.end - t0) / 10)) if p.end is not None else s
        s = max(s, cursor)
        e = max(e, s)
        segs.append((s * 10, e * 10, x, x + pw * norm))
        x += pw * norm
        cursor = e
    return segs


_ROLES = ("sung", "unsung", "outline", "glow_sung", "glow_unsung", "translation", "sparkle")


def plan_bands(ids: tuple[int, ...], colors: dict[int, dict[str, str]], mix: str, direction: str,
               top: float, bottom: float, span: Optional[tuple[float, float]], W: int, H: int,
               k: float = 1.0, within: Optional[tuple[float, float]] = None) -> list[Band]:
    """The copies a chunk's text is drawn in: one for a single singer; for several, a band per singer
    (``mix == "split"``) or thin strips blending their colours (``"gradient"``), stacked top to
    bottom (``direction == "vertical"``, between ``top`` and ``bottom``: where the glyphs are) or
    side by side across ``span`` (``"horizontal"``: the x range of the whole run sung together, so a
    run of chunks is split / blended once from its left to its right end).  The outer bands reach the
    frame's edges, so outline and glow beyond the glyphs are drawn too.  ``within``: only the bands
    reaching into this x range (what the chunk can draw)."""
    ids = tuple(i for i in ids if i in colors)
    if not ids:
        return [BASE_BAND]
    if len(ids) == 1:
        return [Band(None, ids[0], colors[ids[0]])]
    n = len(ids)
    grad = mix == "gradient"

    def band(i: int, steps: int, rect: Rect) -> Band:
        if not grad:
            return Band(rect, ids[i], colors[ids[i]])
        from .themes import blend

        t = (i + 0.5) / steps
        cols = {r: blend([colors[j][r] for j in ids], t) for r in _ROLES}
        return Band(rect, ids[min(n - 1, int(t * n))], cols, True)

    out: list[Band] = []
    if direction == "vertical" or span is None:
        steps = n if not grad else max(2 * n, min(16, round((bottom - top) / (5 * k))))
        edges = [top + (bottom - top) * i / steps for i in range(steps + 1)]
        edges[0], edges[-1] = 0, H
        for i in range(steps):
            out.append(band(i, steps, (0, edges[i], W, edges[i + 1])))
        return out
    a, b = span
    steps = n if not grad else max(2 * n, min(32, round((b - a) / (10 * k))))
    edges = [a + (b - a) * i / steps for i in range(steps + 1)]
    edges[0], edges[-1] = 0, W
    for i in range(steps):
        if within is None or (edges[i + 1] > within[0] and edges[i] < within[1]):
            out.append(band(i, steps, (edges[i], 0, edges[i + 1], H)))
    return out


def shift_times(ass_text: str, delta_ms: int) -> str:
    """Move every event by ``delta_ms``; events that end before 0 are dropped, the rest clamped.

    Subtitle times are absolute from the audio's start, so burning onto a video that was cut
    out of the song needs exactly this: the video's first frame is at ``offset`` in the audio,
    shift by ``-offset`` and event times line up with the picture.
    """
    if not delta_ms:
        return ass_text
    out = []
    for line in ass_text.splitlines():
        if line.startswith("Dialogue:"):
            head, start, end, rest = line.split(",", 3)
            a, b = _parse_ass_time(start) + delta_ms, _parse_ass_time(end) + delta_ms
            if b <= 0:
                continue
            line = f"{head},{ass_time(max(0, a))},{ass_time(max(1, b))},{rest}"
        out.append(line)
    return "\n".join(out) + "\n"


def _parse_ass_time(text: str) -> int:
    h, m, s = text.strip().split(":")
    return int(h) * 3600000 + int(m) * 60000 + int(round(float(s) * 1000))


def _actor(name: str) -> str:
    """A singer's name for the Name field of an event (no commas, braces or line breaks)."""
    return re.sub(r"[,{}\\\x00-\x1f]", "", name or "").strip()[:40]


def _unit_times(result: AlignmentResult) -> dict[str, tuple[Optional[int], Optional[int]]]:
    return {u.unit_id: (u.start_ms, u.end_ms) for u in result.units}


def _romaji(project: Project) -> dict[str, str]:
    from ..reading.profiles import get_profile

    prof = get_profile("ja-hepburn")
    out: dict[str, str] = {}
    for ln in project.lyrics.lines:
        units = [u for s in ln.segments for u in s.units]
        segs = [s for s in ln.segments for _ in s.units]
        if not units:
            continue
        texts = prof.unit_texts([u.reading for u in units], [s.lang for s in segs], [u.flags for u in units])
        for u, t in zip(units, texts):
            out[u.id] = t
    return out


def resolution(project: Project) -> tuple[int, int]:
    """The frame the subtitles are made for: the background's, else the video's, else 1920×1080."""
    b = project.background
    if b is not None:
        from .background import frame_for

        return frame_for(b.width, b.height)
    v = project.video
    if v is not None and v.width and v.height:
        return int(v.width), int(v.height)
    return DEFAULT_SIZE


# Layers, bottom to top: effects drawn behind the text, translation glow, translation,
# text glow (unsung), text glow (sung), lyrics, ruby; then effects in front (7) and the title card (8, 9).


def build_ass(project: Project, result: AlignmentResult, style: Optional[KaraokeStyle] = None, *,
              time_offset_ms: float = 0.0, size: Optional[tuple[int, int]] = None) -> tuple[str, list[str]]:
    """Return (ASS text, warnings).  ``size``: the frame the subtitles are drawn on (PlayRes)."""
    from .effects import FX_STYLE, Syllable, ball_room, syllable_events
    from .info import info_events, info_style
    from .themes import mix

    style = style or project.karaoke
    audio_offset_ms = time_offset_ms  # the title card keeps real time
    # show / highlight everything a little before it is sung (display only)
    time_offset_ms -= style.timing.advance_ms
    W, H = size or resolution(project)
    W, H = max(16, int(W)), max(16, int(H))
    k = W / REF_WIDTH  # by width: the text takes the same share of the frame's width at any size
    lay, txt, rb, tr, glow, tm = style.layout, style.text, style.ruby, style.translation, style.glow, style.timing
    # a style may name a font this machine does not have (styles travel between machines, the built-in
    # 暖阳 uses macOS fonts): then the default font is used for measuring *and* drawing, so the layout
    # still matches what libass draws
    missing_fonts: list[str] = []

    def usable(name: str) -> str:
        if name and not installed(name):
            missing_fonts.append(name)
            return ""
        return name

    family = usable(txt.font) or default_family()
    ruby_family = (usable(rb.font) or family) if rb.enabled else family
    trans_family = (usable(tr.font) or family) if tr.enabled else family
    if tr.enabled and trans_family == BUNDLED_JP and not usable(tr.font) and bundled(BUNDLED_SC):
        trans_family = BUNDLED_SC  # the bundled font's Chinese face: a Chinese translation in Chinese glyph forms
    font_notes: list[str] = []
    if tr.enabled and not system_han_fallback():
        # a translation (usually Chinese) in a Japanese font lacks many characters; libass fills them in
        # from a font of its own choosing at that font's scale, so they jump in size (Windows: Yu Gothic).
        # A font with every character is used instead — unless it is the one the style chose.
        trans_text = "".join(ln.translation or "" for ln in project.lyrics.sung_lines())
        miss = lacking(trans_family, tr.bold, trans_text) if trans_text else ""
        if miss:
            chosen = bool(tr.font) and trans_family == tr.font
            better = None if chosen else covering_family(trans_text, tr.bold, HAN_FAMILIES)
            if better:
                font_notes.append(f"翻译里有 {trans_family} 没有的字（如「{miss[:6]}」），翻译改用 {better}")
                trans_family = better
            else:
                font_notes.append(f"字体 {trans_family} 没有翻译里的一些字（如「{miss[:6]}」），这些字会用别的字体显示，"
                                  "大小可能不一致；可以在“卡拉OK字幕”里给翻译换一个中文字体")
    # guards for styles built in code without validation: every size positive, room left between the margins
    main_size = max(1.0, txt.size * k)
    ruby_size = max(1.0, main_size * max(1, rb.size_pct) / 100)
    trans_size = max(1.0, main_size * max(1, tr.size_pct) / 100)
    gap = rb.gap * k
    trans_gap = 6 * k
    m_main = Measurer(family, txt.bold, main_size)
    m_trans = Measurer(trans_family, tr.bold, trans_size)
    m_ruby = Measurer(ruby_family, txt.bold, ruby_size) if rb.enabled else None
    times = _unit_times(result)
    romaji = _romaji(project) if (rb.enabled and rb.script == "romaji") else {}
    # singers (多人演唱): each one's colours, by number; parts sung together are drawn in bands
    from .themes import singer_colors

    sg = style.singers
    scol: dict[int, dict[str, str]] = {i + 1: singer_colors(m) for i, m in enumerate(sg.members)}
    sname = {i + 1: _actor(m.name) or str(i + 1) for i, m in enumerate(sg.members)}
    warnings: list[str] = []
    if missing_fonts:
        warnings.append(f"这台电脑没有字体 {'、'.join(sorted(set(missing_fonts)))}，已改用 {family}")
    warnings += font_notes

    margin_h = min(lay.margin_h * k, W * 0.4)
    avail = W - 2 * margin_h

    def extent(chunks: list[Chunk]) -> float:
        """How wide a line of these chunks is drawn, readings reaching past its ends included."""
        widths = chunk_widths(chunks, m_main, m_ruby, ruby_size, rb.fit)
        over_l, over_r = ruby_overhang(chunks, widths, m_ruby)
        return (sum(widths) or 1.0) + over_l + over_r

    covered = set(result.coverage.line_ids) if not result.coverage.full else None
    laid: list[LaidLine] = []
    skipped = 0
    for ln in project.lyrics.sung_lines():
        if covered is not None and ln.id not in covered:
            continue
        chunks = build_chunks(ln, times, style, romaji)
        starts = [p.start for c in chunks for p in c.base if p.start is not None]
        ends = [p.end for c in chunks for p in c.base if p.end is not None]
        if not starts or not ends:
            skipped += 1
            continue
        unit_times = [times[u.id] for u in ln.units() if u.id in times and None not in times[u.id]]
        # a line too wide for the room between the margins is wrapped into pieces
        laid += wrap_line(LaidLine(ln, chunks, min(starts), max(ends),
                                   translation=(ln.translation or None) if tr.enabled else None,
                                   units=unit_times), extent, avail, lay.wrap)  # type: ignore[arg-type]
    if skipped:
        warnings.append(f"{skipped} 行没有任何时间，未写入字幕")
    if tr.enabled and not any(ll.translation for ll in laid):
        warnings.append("已开启翻译字幕，但歌词里没有翻译")
    laid.sort(key=lambda x: x.start)
    plan_countdowns(laid, style)
    extra = schedule(laid, style)
    if extra:
        warnings.append(f"{extra} 行与其他行同时演唱（对唱 / 和声），已临时显示在歌词区外多出的一行")

    has_ruby = rb.enabled and any(c.ruby for ll in laid for c in ll.chunks)
    per_line_trans = tr.enabled and tr.position == "line"
    ruby_h = (ruby_size + gap) if has_ruby else 0.0
    slot_h = main_size + ruby_h + ((trans_size + trans_gap) if per_line_trans else 0)
    n = max(1, lay.lines)
    # the bouncing ball hops above each line: rows keep room for it
    spacing = max(lay.line_spacing * k, ball_room(style, main_size, k))
    margin_v = min(lay.margin_v * k, H * 0.4)
    block_h = n * slot_h + (n - 1) * spacing
    block_top = max(0.0, H - margin_v - block_h) if lay.position == "bottom" else margin_v
    top_row = min((ll.slot for ll in laid), default=0)

    fade_in, fade_out = max(0, tm.fade_in_ms), max(0, tm.fade_out_ms)
    g_alpha = f"&H{int(round(255 * (1 - glow.strength / 100))):02X}&"

    def glow_tags(color: str, width: float, font: str, size: float, bold: bool) -> str:
        return (f"\\fn{font}\\fs{size:.1f}\\b{1 if bold else 0}\\3c{bgr_tag(color)}\\3a{g_alpha}"
                f"\\bord{width:.1f}\\blur{glow.blur * k:.1f}\\shad0")

    events: list[str] = []
    boxes: list[Box] = []  # where the lyrics and translations are, and when (for the title card)

    def emit(layer: int, t_from: float, t_to: float, name: str, tags: str, body: str, fad: str,
             actor: str = "") -> None:
        events.append(f"Dialogue: {layer},{ass_time(t_from + time_offset_ms)},{ass_time(t_to + time_offset_ms)},"
                      f"{name},{actor},0,0,0,,{{{tags}{fad}}}{body}")

    def band_look(b: Band, name: str, unsung: str) -> tuple[str, str, str, str, str]:
        """(style name, unsung colour, sung glow, unsung glow, actor) of one band; a band of the style's own
        colours uses the style as before."""
        if not b.singer:
            return name, unsung, glow.color_sung, glow.color_unsung, ""
        c = b.colors or scol[b.singer]
        return f"{name}_{b.singer}", c["unsung"], c["glow_sung"], c["glow_unsung"], sname.get(b.singer, "")

    def blend_tags(b: Band, *roles: str) -> str:
        """A gradient strip's own colours (\\1c sung, \\2c unsung, \\3c outline) written into the event."""
        if not (b.blend and b.colors):
            return ""
        tag_of = {"sung": "1c", "unsung": "2c", "outline": "3c"}
        return "".join(f"\\{tag_of[r]}{bgr_tag(b.colors[r])}" for r in roles)

    def cut_of(b: Band) -> str:
        return "" if b.rect is None else "\\clip({},{},{},{})".format(*(int(round(v)) for v in b.rect))

    def emit_trans(t_from: float, t_to: float, pos: str, text: str, fad: str, ids: tuple[int, ...],
                   span: tuple[float, float]) -> None:
        """A translation in its own colours; its glow in the colours of the line's singers (``ids``, in the
        order they sing; several: blended from left to right across ``span``, the text's x range)."""
        ids = ids if tr.singer_glow else ()
        actor = sname.get(ids[0], "") if ids else ""
        if glow.enabled and tr.glow:
            size = glow.size * k * 0.7
            if len(ids) >= 2:
                for b in plan_bands(ids, scol, "gradient", "horizontal", 0, 0, span, W, H, k):
                    emit(L_TRANS_GLOW, t_from, t_to, "KGlow",
                         pos + glow_tags(b.colors["glow_unsung"], size, trans_family, trans_size, tr.bold) + cut_of(b),
                         escape_text(text), fad, actor)
            else:
                emit(L_TRANS_GLOW, t_from, t_to, "KGlow",
                     pos + glow_tags(scol[ids[0]]["glow_unsung"] if ids else glow.color_unsung, size, trans_family,
                                     trans_size, tr.bold),
                     escape_text(text), fad, actor)
        emit(L_TRANS, t_from, t_to, "KTrans", pos, escape_text(text), fad, actor)

    def live(ids: tuple[int, ...]) -> tuple[int, ...]:
        return tuple(i for i in ids if i in scol)

    def line_singers(ll: LaidLine) -> tuple[int, ...]:
        """Who sings a line, in the order they first sing in it (its parts from left to right)."""
        out: list[int] = []
        for c in ll.chunks:
            for i in live(c.singers):
                if i not in out:
                    out.append(i)
        return tuple(out) or live(tuple(ll.line.singers))

    ruby_bands = rb.follow_colors  # a ruby with its own colours keeps them (no singer colours)
    main_ink = m_main.ink
    ruby_ink = m_ruby.ink if m_ruby is not None else (0.0, 1.0)

    def bands_for(ids: tuple[int, ...], cx: float, width: float, bottom_y: float, size: float,
                  ink: tuple[float, float], run: Optional[tuple[float, float]],
                  ruby: bool = False) -> tuple[list[Band], list[Band]]:
        """The bands of a chunk's text and of its glow, in the look of these singers (their combination's
        or the singers' setting).  The glow of a part sung together always blends (a blurred edge cut
        sharp between two colours shows as a seam beside the glyphs).  A reading is split like its
        lyric, or takes the first singer's colours (KaraokeSingers.ruby)."""
        ids = live(ids)
        mix, direction = sg.look(ids)
        if len(ids) < 2 or (ruby and not sg.ruby_split(direction)):
            one = plan_bands(ids[:1], scol, mix, direction, 0, 0, None, W, H, k)
            return one, one
        top = bottom_y - size
        y0, y1 = top + size * ink[0], top + size * ink[1]
        span = run if direction == "horizontal" else None
        reach = (cx - width / 2 - edge, cx + width / 2 + edge)
        fill = plan_bands(ids, scol, mix, direction, y0, y1, span, W, H, k, reach)
        if not glow.enabled or mix == "gradient":
            return fill, fill
        return fill, plan_bands(ids, scol, "gradient", direction, y0, y1, span, W, H, k, reach)

    def runs_of(ll: LaidLine, cxs: list[float], scale: float) -> list[Optional[tuple[float, float]]]:
        """For each chunk sung together (side by side): the x range of its whole run, the neighbouring
        chunks with the same singers (their lyric and readings)."""
        out: list[Optional[tuple[float, float]]] = [None] * len(ll.chunks)
        i = 0
        while i < len(ll.chunks):
            ids = live(ll.chunks[i].singers)
            j = i
            while j + 1 < len(ll.chunks) and live(ll.chunks[j + 1].singers) == ids:
                j += 1
            if len(ids) >= 2 and sg.look(ids)[1] == "horizontal":
                lo, hi = float("inf"), float("-inf")
                for c, cx in zip(ll.chunks[i:j + 1], cxs[i:j + 1]):
                    half = max(m_main.width(c.base_text), m_ruby.width(c.ruby_text) if (c.ruby and m_ruby) else 0) * scale / 2
                    lo, hi = min(lo, cx - half), max(hi, cx + half)
                for x in range(i, j + 1):
                    out[x] = (lo, hi)
            i = j + 1
        return out

    def emit_countdown(ll: LaidLine, t_from: float, x0: float, line_top: float, scale: float) -> float:
        """Countdown dots above the start of the line, going one by one until its first syllable (their
        times shifted like the lyrics, so the last goes as the sweep starts); returns their top edge."""
        goes = countdown_dots(t_from, ll.start, style.countdown.dots)
        if not goes:
            return line_top
        r = max(3.0, main_size * scale * 0.12)
        cy = line_top - r * 1.9
        ids = live(ll.chunks[0].singers) if ll.chunks else ()
        c = scol[ids[0]] if ids else None
        fill = c["sung"] if c else txt.color_sung
        outline = c["outline"] if c else txt.outline_color
        kappa = 0.5523 * r
        circle = (f"m {r:.1f} 0 b {r + kappa:.1f} 0 {2 * r:.1f} {r - kappa:.1f} {2 * r:.1f} {r:.1f} "
                  f"b {2 * r:.1f} {r + kappa:.1f} {r + kappa:.1f} {2 * r:.1f} {r:.1f} {2 * r:.1f} "
                  f"b {r - kappa:.1f} {2 * r:.1f} 0 {r + kappa:.1f} 0 {r:.1f} "
                  f"b 0 {r - kappa:.1f} {r - kappa:.1f} 0 {r:.1f} 0")
        for i, t_go in enumerate(goes):
            cx = x0 + r + i * r * 3.2
            fi = int(max(0, min(fade_in, (t_go - t_from) / 2)))
            tags = (f"\\an5\\pos({cx:.1f},{cy:.1f})\\1c{bgr_tag(fill)}\\3c{bgr_tag(outline)}"
                    f"\\bord{max(1.0, txt.outline * k * 0.5):.1f}\\shad0\\p1")
            emit(L_RUBY, t_from, t_go, "KDots", tags, circle, f"\\fad({fi},0)" if fi else "",
                 sname.get(ids[0], "") if ids else "")
        return cy - r * 1.5

    def fx_color(ids: tuple[int, ...]) -> Optional[str]:
        """Effects fired by a singer's part: in the singer's colours (its sung glow / sung colour when the
        effect follows, else a pale tint like a template's sparkles)."""
        ids = live(ids)
        if not ids:
            return None
        c = scol[ids[0]]
        if style.effects.color:
            return c["sparkle"]
        return c["glow_sung"] if glow.enabled else c["sung"]

    tag = "kf" if tm.highlight == "sweep" else "k"
    instant = tm.highlight != "sweep"
    # which engine draws the sweep (see karaoke/sweep): the style picks it, "auto" keeps the rule
    # every style written before the engines existed was rendered with
    feather = max(0, tm.sweep_feather_px) * k
    sweep = engine_for(style, has_ruby=has_ruby)
    following = sweep.needs_segments  # the moving-clip family needs the piece segments
    ctx = SweepCtx(emit=emit, band_look=band_look, blend_tags=blend_tags, cut_of=cut_of,
                   glow_tags=glow_tags, glow=glow, bold=txt.bold, height=H, instant=instant,
                   feather=feather, karaoke_tag=tag, mix=mix, steps=FEATHER_STEPS)
    ruby_unsung = (txt if rb.follow_colors else rb).color_unsung
    ruby_sung = (txt if rb.follow_colors else rb).color_sung
    edge = (max(txt.outline, 0) + max(txt.shadow, 0) + (glow.size + glow.blur if glow.enabled else 0)) * k + 2
    fx_on = style.effects.kind != "none"
    syllables: list[Syllable] = []
    # widths first: how far a line sits in from its edge depends on the lines shown next to it
    # a line still wider than the room between the margins (it could not wrap) may reach out to
    # `edge_x` from the frame's edges, moving only as far as it needs to; only wider than that it shrinks
    edge_x = min(lay.edge_margin * k, margin_h)
    room = W - 2 * edge_x
    geom = []
    for ll in laid:
        widths = chunk_widths(ll.chunks, m_main, m_ruby, ruby_size, rb.fit)
        line_w = sum(widths) or 1.0
        over_l, over_r = ruby_overhang(ll.chunks, widths, m_ruby)
        extent = line_w + over_l + over_r  # readings reaching past the line's ends count too
        scale = min(1.0, room / extent) if lay.shrink_long_lines else 1.0
        if scale < 1.0:
            warnings.append(f"「{ll.text}」过长，已缩小到 {scale:.0%}")
        align = "center"
        if lay.arrangement == "alternate" and n > 1 and 0 <= ll.slot < n:
            align = "left" if ll.slot == 0 else ("right" if ll.slot == n - 1 else "center")
        wide = extent * scale > avail + 0.5
        geom.append((widths, line_w * scale, over_l * scale, over_r * scale, scale, align, wide))
    insets = alternate_insets(laid, geom, lay.alternate_indent * k, avail)
    for ll, (widths, line_w, over_l, over_r, scale, align, wide), inset in zip(laid, geom, insets):
        ext = line_w + over_l + over_r
        if align == "left":
            # a wide line keeps its start while it can, else moves toward the left edge
            start_x = max(edge_x, min(margin_h, W - edge_x - ext)) if wide else margin_h + inset
            x0 = start_x + over_l
        elif align == "right":
            end_x = min(W - edge_x, max(W - margin_h, edge_x + ext)) if wide else W - margin_h - inset
            x0 = end_x - over_r - line_w
        else:  # centred, but its readings kept inside the margins (the edges for a wide line)
            side = edge_x if wide else margin_h
            lo, hi = side + over_l, W - side - over_r - line_w
            x0 = min(max((W - line_w) / 2, lo), hi) if lo <= hi else (lo + hi) / 2
        slot_top = block_top + ll.slot * (slot_h + spacing)
        main_y = slot_top + ruby_h + main_size
        # shrunk lines keep their bottom edge where it was
        ruby_y = main_y - main_size * scale - gap * scale
        line_top = (ruby_y - ruby_size * scale) if has_ruby else (main_y - main_size * scale)
        room_above = spacing if ll.slot > top_row else line_top  # free room above the line (the ball)
        fs = "" if scale >= 1.0 else f"\\fscx{scale * 100:.1f}\\fscy{scale * 100:.1f}"
        spans = visible_spans(ll, style)
        if len(spans) > 1:
            pause = (spans[1][0] - spans[0][1] + tm.hold_ms + tm.lead_in_ms) / 1000
            warnings.append(f"「{ll.line.text}」中间停顿约 {pause:.0f} 秒，停顿期间暂时隐藏该行")
        cxs, x = [], x0
        for w in widths:
            cxs.append(x + w * scale / 2)
            x += w * scale
        for n_span, (t_from, t_to) in enumerate(spans):
            # an ASS event cannot start before 0:00: start it there and time the sweep from there,
            # or the fill would lag by what was cut off
            t_from = max(t_from, -time_offset_ms)
            if t_to <= t_from:
                continue
            fad = fade_tag(fade_in, fade_out, t_from, t_to, *_sung_within(ll, t_from, t_to))
            bottom = main_y + ((trans_gap + trans_size) if (ll.translation and per_line_trans) else 0)
            top = line_top
            if ll.countdown_ms and n_span == 0:
                top = emit_countdown(ll, t_from, x0, line_top, scale)
            boxes.append((t_from + time_offset_ms, t_to + time_offset_ms, x0 - over_l, top,
                          x0 + line_w + over_r, bottom))
            runs = runs_of(ll, cxs, scale)
            for c, cx, run in zip(ll.chunks, cxs, runs):
                main_pos = f"\\an2\\pos({cx:.1f},{main_y:.1f}){fs}"
                ruby_pos = f"\\an2\\pos({cx:.1f},{ruby_y:.1f}){fs}"
                bw = m_main.width(c.base_text) * scale
                rw = m_ruby.width(c.ruby_text) * scale if (c.ruby and m_ruby is not None) else 0.0
                segs = piece_segments(c, cx, bw, int(t_from), m_main) if (following or fx_on) else []
                main_bands = bands_for(c.singers, cx, bw, main_y, main_size * scale, main_ink, run)
                r_bands = ([BASE_BAND], [BASE_BAND])
                if c.ruby and m_ruby is not None and ruby_bands:
                    r_bands = bands_for(c.singers, cx, rw, ruby_y, ruby_size * scale, ruby_ink, run, ruby=True)
                sweep.emit(ctx, LineJob(
                    layer=L_MAIN, t_from=t_from, t_to=t_to, style_name="KMain", pos=main_pos,
                    text=c.base_text, parts=c.base, unsung=txt.color_unsung, sung=txt.color_sung,
                    font=family, size=main_size, glow_width=glow.size * k, with_glow=True, fad=fad,
                    bands=main_bands, segs=segs, hi=cx + bw / 2 + edge))
                if c.ruby:
                    sweep.emit(ctx, LineJob(
                        layer=L_RUBY, t_from=t_from, t_to=t_to, style_name="KRuby", pos=ruby_pos,
                        text=c.ruby_text, parts=c.ruby, unsung=ruby_unsung, sung=ruby_sung,
                        font=ruby_family, size=ruby_size, glow_width=glow.size * k * 0.55,
                        with_glow=glow.ruby, fad=fad, bands=r_bands, segs=segs,
                        hi=max(cx + bw / 2, cx + rw / 2) + edge))
                if fx_on:
                    group = f"{ll.line.id}@{t_from}"
                    color = fx_color(c.singers)
                    syllables += _syllables(c.base, cx, main_y, main_size, scale, m_main, family, False, t_from,
                                            t_to, group, line_top, room_above, color=color)
                    if c.ruby and style.effects.ruby and m_ruby is not None:
                        # with the ruby following the lyric, a reading syllable is sung when the sweep
                        # passes it, not at its own time
                        timing = (lambda xa, xb: (int(t_from) + sweep_time(segs, xa, instant),
                                                  int(t_from) + sweep_time(segs, xb, instant))) if following else None
                        syllables += _syllables(c.ruby, cx, ruby_y, ruby_size, scale, m_ruby, ruby_family, True,
                                                t_from, t_to, group, line_top, room_above, timing, color=color)
            if ll.translation and per_line_trans:
                ty = main_y + trans_gap + trans_size
                # a translation wider than the room between the margins is shrunk, and kept inside them
                tw = m_trans.width(ll.translation) or 1.0
                tfs = f"\\fscx{avail / tw * 100:.1f}\\fscy{avail / tw * 100:.1f}" if tw > avail else ""
                tw = min(tw, avail)
                an, tx = {"left": (1, max(margin_h, min(x0, W - margin_h - tw))),
                          "right": (3, min(W - margin_h, max(x0 + line_w, margin_h + tw))),
                          "center": (2, W / 2)}[align]
                left = {1: tx, 3: tx - tw, 2: tx - tw / 2}[an]
                emit_trans(t_from, t_to, f"\\an{an}\\pos({tx:.1f},{ty:.1f}){tfs}", ll.translation, fad,
                           line_singers(ll), (left, left + tw))

    if tr.enabled and not per_line_trans:
        fad = f"\\fad({fade_in},{fade_out})" if (fade_in or fade_out) else ""
        for t0, t1, pos, text, (bx0, by0, bx1, by1), ll in translation_placements(
                laid, style, W, H, block_top, block_h, trans_size, m_trans, margin_h, margin_v):
            emit_trans(t0, t1, pos, text, fad, line_singers(ll), (bx0, bx1))
            boxes.append((t0 + time_offset_ms, t1 + time_offset_ms, bx0, by0, bx1, by1))

    if fx_on:
        for layer, t0, t1, tags, body in syllable_events(style, syllables, k):
            events.append(f"Dialogue: {layer},{ass_time(t0 + time_offset_ms)},{ass_time(t1 + time_offset_ms)},"
                          f"KFx,,0,0,0,,{{{tags}}}{body}")

    # the title card: in its corner at the top, narrowed (wrapped) to keep clear of what else is drawn;
    # the same card again at the end of the song
    orig = project.asset("original")
    song_end = (orig.duration_ms + audio_offset_ms) if orig is not None and orig.duration_ms else None
    events += info_events(project, style, W, H, k, family, audio_offset_ms, boxes=boxes, warnings=warnings,
                          end_ms=song_end)

    shadow_back = ass_color(txt.shadow_color, 100 - txt.shadow_opacity)

    def style_line(name: str, font: str, size: float, sung: str, unsung: str, outline_c: str, outline: float,
                   shadow: float, bold: bool, fill_alpha: int = 0) -> str:
        return (f"Style: {name},{font},{size:.1f},{ass_color(sung, fill_alpha)},{ass_color(unsung, fill_alpha)},"
                f"{ass_color(outline_c)},{shadow_back},{-1 if bold else 0},0,0,0,100,100,0,0,1,"
                f"{max(0.0, outline) * k:.2f},{max(0.0, shadow) * k:.2f},2,0,0,0,1")

    rc = txt if rb.follow_colors else rb
    ruby_outline = rb.outline if not rb.follow_colors else max(1.0, txt.outline * 0.6)
    # each singer's own styles (KMain_1 …): its colours, the rest as the style's
    singer_styles: list[str] = []
    for n, c in scol.items():
        singer_styles.append(style_line(f"KMain_{n}", family, main_size, c["sung"], c["unsung"], c["outline"],
                                        txt.outline, txt.shadow, txt.bold))
        if rb.follow_colors:
            singer_styles.append(style_line(f"KRuby_{n}", ruby_family, ruby_size, c["sung"], c["unsung"],
                                            c["outline"], ruby_outline, txt.shadow * 0.6, txt.bold))
    header = [
        "[Script Info]",
        "; generated by MiliKara",
        "ScriptType: v4.00+",
        f"PlayResX: {W}",
        f"PlayResY: {H}",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "YCbCr Matrix: TV.709",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding",
        style_line("KMain", family, main_size, txt.color_sung, txt.color_unsung, txt.outline_color, txt.outline,
                   txt.shadow, txt.bold),
        style_line("KRuby", ruby_family, ruby_size, rc.color_sung, rc.color_unsung, rc.outline_color,
                   ruby_outline, txt.shadow * 0.6, txt.bold),
        style_line("KTrans", trans_family, trans_size, tr.color, tr.color, tr.outline_color, tr.outline, tr.shadow,
                   tr.bold),
        # glow layers: invisible fill, the (blurred) border is the glow; sizes set per event
        style_line("KGlow", family, main_size, "#FFFFFF", "#FFFFFF", "#FFFFFF", 0, 0, txt.bold, fill_alpha=100),
        *singer_styles,
        # countdown dots (drawings; colours set per event)
        style_line("KDots", family, main_size, txt.color_sung, txt.color_sung, txt.outline_color, txt.outline * 0.5,
                   0, txt.bold),
        FX_STYLE,
        info_style(family, main_size),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    return "\n".join(header + events) + "\n", warnings


def _syllables(parts: list[Part], cx: float, bottom_y: float, size: float, scale: float, measurer, font: str,
               ruby: bool, t_from: float, t_to: float, group: str = "", top: float = 0.0, room: float = 1e9,
               timing=None, color: Optional[str] = None) -> list:
    """Where each sung piece of a chunk sits on screen (the chunk text is centred at ``cx``, its
    bottom at ``bottom_y`` as drawn with \\an2); ``timing(x0, x1)`` gives a piece's (start, end)
    when it is not its own (a reading that follows the lyric's sweep)."""
    from .effects import Syllable

    widths = [measurer.width(p.text) * scale for p in parts]
    left = cx - sum(widths) / 2
    out = []
    for p, w in zip(parts, widths):
        start, end = (p.start, p.end) if timing is None else timing(left, left + w)
        if start is not None and end is not None and p.text.strip() and t_from <= start < t_to:
            # \an5 at the middle of the line box (its height is the font size) sits exactly on the
            # glyphs drawn with \an2 at the bottom, and scales around their centre
            out.append(Syllable(text=p.text, start=int(start), end=int(max(end, start)), x=left + w / 2,
                                y=bottom_y - size * scale * 0.5, w=max(w, size * scale * 0.4), h=size * scale,
                                font=font, size=size * scale, ruby=ruby, visible_until=int(t_to), group=group,
                                top=top, room=room, color=color))
        left += w
    return out
