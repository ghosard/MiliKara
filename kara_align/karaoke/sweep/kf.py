"""The per-syllable fill: ``\\kf`` (or ``\\k``) inside one event.

libass fills each piece of the line from its own ``\\kf`` tag, so the sweep is drawn by the
text itself — the cheapest way to sweep, and what every style did before the engines.
"""

from __future__ import annotations

from ..events import L_GLOW, L_GLOW_SUNG, escape_text, karaoke_tags
from .base import LineJob, SweepCtx


class KfSweep:
    name = "kf"
    needs_segments = False

    def emit(self, ctx: SweepCtx, job: LineJob) -> None:
        plain = escape_text("".join(p.text for p in job.parts))
        fill, glows = job.bands
        if ctx.glow.enabled and job.with_glow:
            for b in glows:
                _, _, g_sung, g_unsung, actor = ctx.band_look(b, job.style_name, "")
                ctx.emit(L_GLOW, job.t_from, job.t_to, "KGlow",
                         job.pos + ctx.glow_tags(g_unsung, job.glow_width, job.font, job.size, ctx.bold)
                         + ctx.cut_of(b), plain, job.fad, actor)
                # \ko: the border (the glow) appears as each syllable is sung
                ctx.emit(L_GLOW_SUNG, job.t_from, job.t_to, "KGlow",
                         job.pos + ctx.glow_tags(g_sung, job.glow_width, job.font, job.size, ctx.bold)
                         + ctx.cut_of(b), karaoke_tags(job.parts, int(job.t_from), "ko"), job.fad, actor)
        for b in fill:
            sty, _, _, _, actor = ctx.band_look(b, job.style_name, "")
            ctx.emit(job.layer, job.t_from, job.t_to, sty,
                     job.pos + ctx.blend_tags(b, "sung", "unsung", "outline") + ctx.cut_of(b),
                     karaoke_tags(job.parts, int(job.t_from), ctx.karaoke_tag), job.fad, actor)
