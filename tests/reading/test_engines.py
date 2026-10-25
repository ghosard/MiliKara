"""The reading engine chain: rule readings, then the readings the lyrics carry themselves."""

from kara_align.models import Line, LyricsDoc
from kara_align.reading.engines import ReadingContext, available, chain, get
from kara_align.reading.prepare import prepare_line, set_segment_reading


def build(*texts: str) -> LyricsDoc:
    doc = LyricsDoc(lines=[Line(text=t) for t in texts])
    chain(ReadingContext(doc=doc))
    return doc


def units_of(seg) -> list[str]:
    return [u.reading for u in seg.units]


def test_chain_is_registered():
    assert "rule" in available() and "annotation" in available()
    assert get("annotation").name == "annotation"


def test_bracket_reading_becomes_the_word_reading():
    doc = build("帰りし夢の終(つい)まで")
    segs = doc.lines[0].segments
    # the parentheses are gone from what is sung: one hidden segment carries them
    assert "".join(s.surface for s in segs) == "帰りし夢の終(つい)まで"
    sung = [s for s in segs if s.units]
    assert [s.surface for s in sung] == ["帰り", "し", "夢", "の", "終", "まで"]
    end = next(s for s in sung if s.surface == "終")
    assert end.reading == "つい" and units_of(end) == ["つ", "い"]
    assert end.reading_source == "annotation"
    hidden = [s for s in segs if s.hidden]
    assert len(hidden) == 1 and hidden[0].surface == "(つい)"


def test_bracket_can_cover_only_the_last_kanji():
    doc = build("十が揺れ垂(しだ)る川面に")
    segs = doc.lines[0].segments
    word = next(s for s in segs if s.surface == "揺れ垂")
    assert word.reading == "ゆれしだ"          # 揺れ keeps its own reading, しだ comes from the bracket
    assert units_of(word) == ["ゆ", "れ", "し", "だ"]
    assert [s.surface for s in segs if s.units] == ["十", "が", "揺れ垂", "る", "川面", "に"]


def test_bracket_that_is_not_a_reading_is_left_alone():
    doc = build("みんなで(合唱)しよう")
    segs = doc.lines[0].segments
    assert not any(s.hidden for s in segs)
    assert [s.surface for s in segs if s.units] == ["みんな", "で", "合唱", "しよう"]


def test_manual_reading_is_not_overridden():
    doc = LyricsDoc(lines=[Line(text="明日(あした)の私")])
    prepare_line(doc.lines[0])
    first = doc.lines[0].segments[0]
    set_segment_reading(doc.lines[0], first.id, "みょうにち")
    chain(ReadingContext(doc=doc))
    segs = doc.lines[0].segments
    assert segs[0].reading == "みょうにち" and segs[0].reading_source == "manual"
    assert not any(s.hidden for s in segs)


def test_running_twice_changes_nothing():
    doc = build("明日(あした)の私を")
    before = [(s.surface, s.reading, s.hidden) for s in doc.lines[0].segments]
    chain(ReadingContext(doc=doc))
    after = [(s.surface, s.reading, s.hidden) for s in doc.lines[0].segments]
    assert before == after
