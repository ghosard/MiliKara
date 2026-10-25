"""The annotation engine: readings the lyrics already carry as 漢字(かな).

Japanese LRC files (and the platform lyrics behind them) often spell a word's reading in
brackets right after it, precisely where the dictionary reading would be ambiguous:

    帰りし夢の終(つい)まで          終 is sung つい, not おわり
    明日(あした)の私を見捨てるとして  明日 is あした, not あす
    十が揺れ垂(しだ)る川面に…        垂 is しだ, so the word is ゆれしだ

Rules are forced to read the dictionary form and then the bracket contents end up as
separate "words" that get aligned and drawn (the parentheses too).  This engine folds
such a bracket into the word before it and hides the bracket segment, so the reading
lands where a lyric sheet means it — as ruby over the word, not as extra lyrics.

Only applied when the bracket holds kana and the word before it ends in a kanji run;
anything else (backing vocals, "(x2)", "合唱"…) is left alone.
"""

from __future__ import annotations

import re
from typing import Optional

from ..prepare import rule_segments_for, units_from_spec
from ...models import Segment
from . import EngineResult, ReadingContext, register

OPEN = ("(", "（")
CLOSE = (")", "）")
PROTECTED = ("manual", "ai", "annotation")
KANA_RUN = re.compile(r"^[\u3040-\u309F\u30A0-\u30FFー・\u30FD\u30FE]+$")
KANJI_TAIL = re.compile(r"[\u3400-\u4DBF\u4E00-\u9FFF々〆]+$")


def _protected(seg: Segment) -> bool:
    return seg.confirmed or seg.hidden or seg.reading_source in PROTECTED


def _merged_reading(prev: Segment, inner: str) -> Optional[str]:
    """``inner`` as the reading of the trailing kanji run of ``prev``'s surface."""
    run = KANJI_TAIL.search(prev.surface)
    if run is None:
        return None
    tail = run.group()
    if tail == prev.surface:  # the whole word is that kanji run: 終(つい), 明日(あした)
        return inner
    head = prev.surface[: -len(tail)]
    # 揺れ垂(しだ): keep the reading of 揺れ, take しだ for 垂
    pre = rule_segments_for(head, prev.lang)
    pre_reading = "".join(s.reading or "" for s in pre)
    return (pre_reading + inner) if pre_reading else None


class AnnotationEngine:
    name = "annotation"

    def apply(self, ctx: ReadingContext) -> EngineResult:
        folded, conflicts = 0, []
        for line in ctx.doc.lines:
            segs = line.segments
            i = 0
            while i < len(segs) - 1:
                if segs[i].surface.strip() not in OPEN or i == 0:
                    i += 1
                    continue
                j, acc, closed = i, "", False
                while j < len(segs):
                    acc += segs[j].surface
                    if segs[j].surface.strip() in CLOSE:
                        closed = True
                        j += 1
                        break
                    j += 1
                inner = acc.strip()[1:-1].strip()
                prev = segs[i - 1]
                if not closed or not inner or not KANA_RUN.match(inner) or _protected(prev):
                    i += 1
                    continue
                reading = _merged_reading(prev, inner)
                if not reading:
                    i += 1
                    continue
                if prev.reading and prev.reading != reading:
                    conflicts.append(f"{line.id} {prev.surface}={prev.reading}->{reading}")
                prev.reading = reading
                prev.units = units_from_spec(reading, None, prev.lang)
                prev.reading_source = "annotation"
                prev.uncertain = False
                prev.confirmed = False
                # one hidden segment keeps the surface concatenation equal to the line text
                segs[i:j] = [Segment(surface=acc, lang="ja", hidden=True, reading=None, units=[],
                                     reading_source="annotation")]
                folded += 1
                i += 1
        msgs = [f"用了歌词自带的注音：{folded} 处"] if folded else []
        if conflicts:
            msgs.append("歌词注音覆盖了词典读音：" + "；".join(conflicts[:4]))
        return EngineResult(engine=self.name, changed=folded, messages=msgs)


register(AnnotationEngine())
