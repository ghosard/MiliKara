"""Write the golden ASS files this test compares against.

    python tests/karaoke/update_golden.py

Only after a deliberate change of the output — an accidental one should show up as a diff.
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

from tests.karaoke.test_ass_golden import VARIANTS, build  # noqa: E402

HERE.joinpath("golden").mkdir(exist_ok=True)
for name, style in VARIANTS.items():
    ass, _ = build(style)
    path = HERE / "golden" / f"{name}.ass"
    path.write_text(ass, encoding="utf-8")
    print(f"{path.name}: {len(ass.splitlines())} lines")
