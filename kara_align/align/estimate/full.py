"""The accurate estimator: a trial alignment, then let the lines vote.

This is what ``auto_calibrate.estimate_lrc_shift`` always did — a plain-mode alignment on a
throw-away copy of the project, one vote per timed line (sung start − LRC time), the densest
cluster wins.  Expensive (a whole alignment) and the only thing that works when the audio and
the LRC differ in tempo or in version, so it stays as the cascade's fallback.
"""

from __future__ import annotations

from . import CalibrationEstimate, register


class FullAlignmentEstimator:
    name = "full"
    cost = "alignment"

    def estimate(self, h, *, cancel=None, progress=None) -> CalibrationEstimate:
        from ...auto_calibrate import estimate_lrc_shift

        fit = estimate_lrc_shift(h, cancel=cancel, progress=progress)
        return CalibrationEstimate(
            shift_ms=int(fit["shift_ms"]),
            confident=bool(fit["confident"]),
            method=self.name,
            reason="" if fit["confident"] else (fit.get("reason") or "证据不足"),
            agree=float(fit.get("agree") or 0.0),
            tight=float(fit.get("tight") or 0.0),
            lines_checked=int(fit.get("lines") or 0),
            drift_ms=fit.get("drift_ms"),
            extra={"line_starts": fit.get("starts") or {}, "cost": "alignment"},
        )


register(FullAlignmentEstimator())
