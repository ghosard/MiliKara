"""The rule engine: MeCab + UniDic lite (or the character-class fallback) for every line.

This is the engine that was ``prepare.prepare_doc``; it keeps writing its results into the
``ReadingContext`` so the service can still report "prepared / kept / re-derived" lines.
"""

from __future__ import annotations

from . import EngineResult, ReadingContext, register
from ..prepare import prepare_doc


class RuleEngine:
    name = "rule"

    def apply(self, ctx: ReadingContext) -> EngineResult:
        rep = prepare_doc(ctx.doc, overwrite_rule=ctx.overwrite_rule)
        ctx.prepared, ctx.kept = rep.prepared, rep.kept
        ctx.rederived, ctx.regrouped = rep.rederived, rep.regrouped
        return EngineResult(engine=self.name, changed=len(rep.prepared), messages=list(rep.messages))


register(RuleEngine())
