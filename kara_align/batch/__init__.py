"""Batch: many songs in, many videos out.

    from kara_align.batch import Song, load, run

    songs, defaults = load(Path("songs.yaml"))
    results = run(songs, defaults=defaults, jobs=2, out_dir=Path("out"))

Every song gets its own project folder, so a run that is interrupted continues where it
stopped and one broken song does not take the others with it.  ``milikara run`` is this API
with a progress line.
"""

from __future__ import annotations

from .runner import STAGES, Result, run, run_song
from .song import Song, SongDefaults, load, loads

__all__ = ["STAGES", "Result", "Song", "SongDefaults", "load", "loads", "run", "run_song"]
