"""A small, deterministic project for ASS golden tests: two lines, ruby, a translation, a gap.

Nothing here touches audio, models or the network: the alignment times are written by hand,
so the ASS this produces is a pure function of the style and the renderer.
"""

from __future__ import annotations

import re

from kara_align.models import (AlignmentResult, AlignConfig, BackendInfo, InputSnapshot, Line, LineTiming,
                               LyricsDoc, Project, UnitTiming)
from kara_align.reading.prepare import prepare_doc

LYRICS = [
    ("L0001", "懐かし空 雲は流れ", "怀念的天空 云在流动"),
    ("L0002", "夕が暮れ帳落ちる夜", "黄昏降临 夜幕落下"),
    ("L0003", "帰りし夢の終(つい)まで", "直到归去之梦的尽头"),
]

FONT_RE = re.compile(r"\\fn[^\\}]*")


def project() -> Project:
    """Lyrics with rule readings, and unit times laid out 300 ms apart from 1 s."""
    doc = LyricsDoc(language="ja", lines=[
        Line(id=lid, text=text, translation=tr) for lid, text, tr in LYRICS
    ])
    prepare_doc(doc)

    result = AlignmentResult(
        mode="plain",
        backend=BackendInfo(name="fixture", model_id="fixture", model_revision="0",
                            profile="ja-hepburn", sample_rate=16000),
        config=AlignConfig(),
        snapshot=InputSnapshot(mode="plain", lyrics_text_revision="t", lyrics_reading_revision="r",
                               audio_asset_id="a", audio_sha256="0" * 64, audio_role="original",
                               line_ids=[ln.id for ln in doc.lines], config_hash="c"))
    t = 1000
    for ln in doc.lines:
        starts = []
        for seg in ln.segments:
            for unit in seg.units:
                result.units.append(UnitTiming(unit_id=unit.id, line_id=ln.id, segment_id=seg.id,
                                               reading=unit.reading, start_ms=t, end_ms=t + 260))
                starts.append(t)
                t += 300
        result.lines.append(LineTiming(line_id=ln.id, start_ms=min(starts) if starts else None,
                                       end_ms=(t - 300 + 260) if starts else None))
        t += 900  # a gap between lines

    return Project(id="p_fixture", name="fixture", mode="plain", lyrics=doc,
                   results=[result], active_result_id=result.id)


def normalize(ass: str) -> str:
    """Replace the font families: they depend on what the machine has installed, not on the renderer."""
    lines = []
    for line in ass.splitlines():
        if line.startswith("Style:"):
            fields = line.split(",")
            fields[1] = "<FONT>"          # Name, Fontname, …
            line = ",".join(fields)
        lines.append(FONT_RE.sub(r"\\fn<FONT>", line))
    return "\n".join(lines) + "\n"
