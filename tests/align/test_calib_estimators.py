"""The calibration cascade: one confident answer, the fallback when nobody is sure."""

import pytest

from kara_align.align import estimate as E
from kara_align.align.estimate import CalibrationEstimate


class Fake:
    def __init__(self, name, result, cost="cheap"):
        self.name = name
        self.cost = cost
        self._result = result
        self.calls = 0

    def estimate(self, h, *, cancel=None, progress=None):
        self.calls += 1
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


def confident(shift=250, method="a"):
    return CalibrationEstimate(shift_ms=shift, confident=True, method=method)


def shy(shift=0, reason="证据不足", method="b"):
    return CalibrationEstimate(shift_ms=shift, confident=False, method=method, reason=reason)


def test_first_confident_wins(monkeypatch):
    a, b = Fake("a", confident(250, "a")), Fake("b", confident(900, "b"))
    monkeypatch.setitem(E._REGISTRY, "a", a)
    monkeypatch.setitem(E._REGISTRY, "b", b)
    out = E.cascade(None, ("a", "b"))
    assert out.shift_ms == 250 and out.method == "a"
    assert b.calls == 0, "a later estimator must not run once one is confident"


def test_not_confident_falls_through(monkeypatch):
    a, b = Fake("a", shy(method="a")), Fake("b", confident(120, "b"))
    monkeypatch.setitem(E._REGISTRY, "a", a)
    monkeypatch.setitem(E._REGISTRY, "b", b)
    out = E.cascade(None, ("a", "b"))
    assert out.shift_ms == 120 and out.method == "b"
    assert out.extra["tried"] == ["a"], "the cascade records what it tried before the answer"


def test_a_broken_estimator_is_just_not_confident(monkeypatch):
    a, b = Fake("a", RuntimeError("boom")), Fake("b", confident(75, "b"))
    monkeypatch.setitem(E._REGISTRY, "a", a)
    monkeypatch.setitem(E._REGISTRY, "b", b)
    out = E.cascade(None, ("a", "b"))
    assert out.shift_ms == 75 and out.method == "b"


def test_nobody_confident_returns_the_last_with_its_reason(monkeypatch):
    monkeypatch.setitem(E._REGISTRY, "a", Fake("a", shy(reason="行太少", method="a")))
    monkeypatch.setitem(E._REGISTRY, "b", Fake("b", shy(shift=40, reason="漂移", method="b")))
    out = E.cascade(None, ("a", "b"))
    assert not out.confident and out.method == "b" and out.reason == "漂移"
    assert out.extra["tried"] == ["a"]


def test_unknown_estimator_is_rejected():
    with pytest.raises(KeyError):
        E.get("nope")
    assert "full" in E.available()
