"""Golden ASS files: the renderer's output has to stay byte-for-byte the same while it is refactored.

``VARIANTS`` are the shapes worth guarding — the default per-syllable fill, the moving-clip
sweep that cuts lyric and reading with one line, and the feathered edge.  Each is built from
the fixture project in ``assfix`` (two sung lines, one bracket annotation, a translation) and
compared to ``golden/<name>.ass``; font families are normalised away because they depend on
the machine, not on the renderer.

When the output is meant to change, run ``python tests/karaoke/update_golden.py`` and look at
the diff before committing it.
"""

from pathlib import Path

import pytest

from kara_align.karaoke.ass import build_ass
from kara_align.karaoke.styles import KaraokeStyle
from tests.karaoke import assfix

GOLDEN = Path(__file__).resolve().parent / "golden"


def variant(*, engine: str = "auto", feather: int = 0, ruby_sweep: str = "own",
            glow: bool = True, singers: bool = False) -> KaraokeStyle:
    st = KaraokeStyle()
    st.translation.enabled = True
    st.glow.enabled = glow
    st.timing.sweep_engine = engine
    st.timing.sweep_feather_px = feather
    st.ruby.sweep = ruby_sweep
    if singers:
        from kara_align.models import KaraokeSinger

        st.singers.members = [KaraokeSinger(name="A", color="#FF6FA5"),
                              KaraokeSinger(name="B", color="#4FD1C5")]
        st.singers.mix = "gradient"
    return st


VARIANTS = {
    "kf-default": variant(),
    "clip-shared": variant(engine="clip", ruby_sweep="base"),
    "feather-60": variant(engine="feather", feather=60),
    "no-glow": variant(glow=False),
    "singers-gradient": variant(singers=True),
}


def build(style: KaraokeStyle) -> tuple[str, list[str]]:
    project = assfix.project()
    if style.singers.members:
        # two singers: line 2 is split between them, so the bands and their gradient are covered too
        ln = project.lyrics.lines[1]
        ln.singers = [1, 2]
        ln.singer_spans = []
    ass, warnings = build_ass(project, project.result(), style, size=(1920, 1080))
    return assfix.normalize(ass), warnings


@pytest.mark.parametrize("name", sorted(VARIANTS))
def test_ass_matches_golden(name):
    ass, _ = build(VARIANTS[name])
    expected = (GOLDEN / f"{name}.ass").read_text(encoding="utf-8")
    assert ass == expected, (
        f"{name}: the ASS changed.  If that is intended, run tests/karaoke/update_golden.py "
        f"and commit the diff; if not, the refactor changed the output."
    )
