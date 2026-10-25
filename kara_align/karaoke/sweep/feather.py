"""The moving clip with a soft edge: the boundary fades from the sung to the unsung colour.

``\\kf`` ends a syllable's fill with a hard vertical edge, which reads as characters flipping
one after another.  Here the band between ``feather`` px left of the sweep and the sweep itself
is drawn as a few strips whose colour runs from the sung colour to the unsung one, and those
strips travel with the edge — so the light moves through the line instead of stepping.

Cost: the strips multiply the events (xN per line); the width is
``timing.sweep_feather_px`` and the number of strips ``events.FEATHER_STEPS``.
"""

from __future__ import annotations

from ..events import sweep_strip_clip
from .base import LineJob, SweepCtx
from .clip import ClipSweep


class FeatherSweep(ClipSweep):
    name = "feather"

    def emit(self, ctx: SweepCtx, job: LineJob) -> None:
        self._emit(ctx, job, soft=True)
