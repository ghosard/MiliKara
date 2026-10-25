"""The moving clip: one vertical line cuts the lyric — and its reading — as the singing goes.

Two copies of the same text are emitted; the unsung one is ``\\iclip``-cut to the right of the
sweep edge and the sung one ``\\clip``-cut to its left, so every pixel comes from exactly one
of them and both survive the line's fade.  Because the edge is computed here instead of by
libass's ``\\kf``, the lyric and its reading can be cut by one line and the edge can be moved
by hand — which is what makes a soft (feathered) edge possible at all.
"""

from __future__ import annotations

from ..events import (L_GLOW, L_GLOW_SUNG, band_clip, bgr_tag, escape_text, sweep_clip, sweep_moves,
                       sweep_strip_clip)
from .base import LineJob, SweepCtx


class ClipSweep:
    name = "clip"
    needs_segments = True

    def emit(self, ctx: SweepCtx, job: LineJob) -> None:
        self._emit(ctx, job, soft=False)

    def _emit(self, ctx: SweepCtx, job: LineJob, *, soft: bool) -> None:
        plain = escape_text(job.text)
        moves: list = []
        segs, hi, instant = job.segs or [], job.hi or 0.0, ctx.instant

        def cuts(b) -> tuple[str, str]:
            nonlocal moves
            if b.rect is None:
                return sweep_clip(segs, hi, instant, ctx.height), sweep_clip(segs, hi, instant, ctx.height, True)
            moves = moves or sweep_moves(segs, hi, instant)
            return band_clip(moves, b.rect, True, hi), band_clip(moves, b.rect, False, hi)

        sung = job.sung
        soft = soft and sung is not None and ctx.feather > 0
        steps, feather = ctx.steps, ctx.feather

        fill, glows = job.bands
        if ctx.glow.enabled and job.with_glow:
            for b in glows:
                if soft and b.rect is None and not b.blend:
                    # the glow of the sung part follows softly too, one step behind the fill
                    g_soft = job.glow_width * 0.5
                    _, _, g_sung, g_unsung, actor = ctx.band_look(b, job.style_name, job.unsung)
                    for i in range(steps):
                        t = (i + 1) / (steps + 1)
                        a0 = -g_soft + g_soft * i / steps
                        a1 = -g_soft + g_soft * (i + 1) / steps
                        ctx.emit(L_GLOW, job.t_from, job.t_to, "KGlow",
                                 job.pos + ctx.glow_tags(ctx.mix(g_sung, g_unsung, t), job.glow_width, job.font,
                                                         job.size, ctx.bold)
                                 + sweep_strip_clip(segs, hi, instant, ctx.height, a0, a1), plain, job.fad, actor)
                    clip, iclip = cuts(b)
                    ctx.emit(L_GLOW, job.t_from, job.t_to, "KGlow",
                             job.pos + ctx.glow_tags(g_unsung, job.glow_width, job.font, job.size, ctx.bold) + iclip,
                             plain, job.fad, actor)
                    continue
                clip, iclip = cuts(b)
                _, _, g_sung, g_unsung, actor = ctx.band_look(b, job.style_name, job.unsung)
                ctx.emit(L_GLOW, job.t_from, job.t_to, "KGlow",
                         job.pos + ctx.glow_tags(g_unsung, job.glow_width, job.font, job.size, ctx.bold) + iclip,
                         plain, job.fad, actor)
                ctx.emit(L_GLOW_SUNG, job.t_from, job.t_to, "KGlow",
                         job.pos + ctx.glow_tags(g_sung, job.glow_width, job.font, job.size, ctx.bold) + clip,
                         plain, job.fad, actor)
        for b in fill:
            clip, iclip = cuts(b)
            sty, un, _, _, actor = ctx.band_look(b, job.style_name, job.unsung)
            ctx.emit(job.layer, job.t_from, job.t_to, sty,
                     job.pos + f"\\1c{bgr_tag(un)}" + ctx.blend_tags(b, "outline") + iclip, plain, job.fad, actor)
            if soft and b.rect is None and not b.blend:
                # the part left of the soft band: fully sung
                ctx.emit(job.layer, job.t_from, job.t_to, sty,
                         job.pos + f"\\1c{bgr_tag(sung)}" + ctx.blend_tags(b, "outline")
                         + sweep_strip_clip(segs, hi, instant, ctx.height, -1e5, -feather), plain, job.fad, actor)
                for i in range(steps):
                    t = (i + 1) / (steps + 1)
                    a0 = -feather + feather * i / steps
                    a1 = -feather + feather * (i + 1) / steps
                    ctx.emit(job.layer, job.t_from, job.t_to, sty,
                             job.pos + f"\\1c{bgr_tag(ctx.mix(sung, un, t))}" + ctx.blend_tags(b, "outline")
                             + sweep_strip_clip(segs, hi, instant, ctx.height, a0, a1), plain, job.fad, actor)
                continue
            ctx.emit(job.layer, job.t_from, job.t_to, sty,
                     job.pos + ctx.blend_tags(b, "sung", "outline") + clip, plain, job.fad, actor)
