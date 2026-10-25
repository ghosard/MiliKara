"""First-syllable offset: estimators and the cascade that picks one.

``suggest_calibration`` used to answer this question one way only: run a whole plain-mode
alignment on a throw-away copy of the project, read each line's sung start, and cluster the
differences to the LRC times.  That is accurate and costs a full alignment (~20 s with the
model load), which dominates a batch run and is paid even when the answer is obvious.

The estimators here answer the same question at different costs; each returns the same
``CalibrationEstimate``, and a cascade takes the first confident one:

      sampled  the trial alignment above, but only on a dozen lines spread over the song: a
             local rerun per line, so the decode covers a neighbourhood instead of the
             whole track.  Same votes, same clustering, a fraction of the cost
    full     the trial alignment on the whole song, always available, always the fallback

An estimator that is not confident does not guess: the cascade moves on, and if nobody is
confident the result says so (``reason``), exactly as before.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol

DEFAULT_CHAIN = ("full",)


@dataclass
class CalibrationEstimate:
    shift_ms: int
    confident: bool
    method: str
    reason: str = ""
    agree: float = 0.0          # share of lines agreeing with the offset (within its tolerance)
    tight: float = 0.0          # share within TIGHT_MS
    lines_checked: int = 0
    drift_ms: Optional[int] = None
    # what the estimate was computed from; the cascade keeps the first confident one
    extra: dict = field(default_factory=dict)

    def part(self) -> dict:
        """The evidence, without the offset itself (for diagnostics / logs)."""
        return {"method": self.method, "confident": self.confident, "reason": self.reason,
                "agree": round(self.agree, 3), "tight": round(self.tight, 3),
                "lines_checked": self.lines_checked, "drift_ms": self.drift_ms, **(self.extra or {})}


class CalibrationEstimator(Protocol):
    name: str
    cost: str  # "cheap" | "model" | "alignment"

    def estimate(self, h, *, cancel=None, progress=None) -> CalibrationEstimate:
        ...


_REGISTRY: dict[str, CalibrationEstimator] = {}


def register(estimator: CalibrationEstimator) -> CalibrationEstimator:
    _REGISTRY[estimator.name] = estimator
    return estimator


def available() -> list[str]:
    return sorted(_REGISTRY)


def get(name: str) -> CalibrationEstimator:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise KeyError(f"unknown calibration estimator: {name} (have {', '.join(available())})") from None


def cascade(h, names: Optional[tuple[str, ...]] = None, *, cancel=None,
            progress=None) -> CalibrationEstimate:
    """The first confident estimate; otherwise the last one, marked not confident."""
    tried: list[CalibrationEstimate] = []
    for name in names or DEFAULT_CHAIN:
        try:
            est = get(name).estimate(h, cancel=cancel, progress=progress)
        except Exception as e:  # an estimator that breaks is just not confident
            est = CalibrationEstimate(shift_ms=0, confident=False, method=name,
                                      reason=f"{type(e).__name__}: {e}")
        est.extra.setdefault("tried", [e.method for e in tried])
        if est.confident:
            return est
        tried.append(est)
    if tried:
        return tried[-1]
    return CalibrationEstimate(shift_ms=0, confident=False, method="none", reason="没有可用的估计方法")


def _load() -> None:
    from . import full  # noqa: F401

_load()
