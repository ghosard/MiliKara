"""The small conveniences that keep callers from guessing the shape of the models."""

from kara_align.models import (AlignmentResult, AlignConfig, BackendInfo, InputSnapshot, Issue,
                               LineTiming, UnitTiming)


def result_with(units, lines) -> AlignmentResult:
    return AlignmentResult(mode="plain",
                           backend=BackendInfo(name="t", model_id="m", profile="p", sample_rate=16000),
                           config=AlignConfig(),
                           snapshot=InputSnapshot(mode="plain", lyrics_text_revision="a",
                                                  lyrics_reading_revision="b", audio_asset_id="c",
                                                  audio_sha256="0" * 64, audio_role="original",
                                                  line_ids=[], config_hash="d"),
                           units=units, lines=lines)


def test_units_of_returns_that_lines_units_in_order():
    r = result_with([UnitTiming(unit_id="u1", line_id="L1", segment_id="s", reading="あ"),
                     UnitTiming(unit_id="u2", line_id="L2", segment_id="s", reading="い"),
                     UnitTiming(unit_id="u3", line_id="L1", segment_id="s", reading="う")],
                    [LineTiming(line_id="L1", start_ms=0, end_ms=10)])
    assert [u.unit_id for u in r.units_of("L1")] == ["u1", "u3"]
    assert r.units_of("nope") == []
    assert [lt.line_id for lt in r.lines_of("L1")] == ["L1"]


def test_issue_kind_is_its_code():
    assert Issue(code="short_unit", message="x").kind == "short_unit"
