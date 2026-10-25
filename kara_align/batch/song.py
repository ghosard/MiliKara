"""One song of a batch run, and the list of them.

A ``Song`` is the same information the simple-mode form collects — audio, lyrics, a background,
a look — as data, so a run can live in a YAML/JSON file and be version-controlled:

    defaults: {color: "#FF6FA5", feather: 60, quality: standard, background: bg.webp}
    songs:
      - {name: eien, audio: song.m4a, lyrics: "netease:498577461"}
      - {name: tonight, audio: b.m4a, lyrics: "netease:1895145781", quality: high}

``lyrics`` takes a music link (a single song), ``netease:ID`` / ``qq:MID``, or the path of an
LRC / plain lyric file.  Everything a song does not say comes from ``defaults``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


@dataclass
class Song:
    name: str
    audio: Path
    lyrics: str
    title: str = ""
    background: Optional[Path] = None
    color: Optional[str] = None
    feather: Optional[int] = None
    indent: Optional[int] = None
    quality: Optional[str] = None
    shift_ms: Optional[int] = None       # None: estimate it; 0: trust the LRC at 0 ms
    fixes: dict[str, dict[str, str]] = field(default_factory=dict)  # {line_id: {surface: reading}}
    # an already edited video to burn onto (instead of the background picture): its first frame
    # is at video_offset_ms of the audio, and sound says whose audio the result carries
    video: Optional[Path] = None
    video_offset_ms: int = 0
    sound: str = "keep"                  # keep | original | none
    # "video" burns a video; "ass" stops after the alignment and writes the subtitle files
    only: str = "video"

    def resolved(self, defaults: "SongDefaults") -> "Song":
        """A copy with every unset field taken from the run's defaults."""
        return Song(
            name=self.name,
            audio=self.audio,
            lyrics=self.lyrics,
            title=self.title or self.name,
            background=self.background or defaults.background,
            color=self.color or defaults.color,
            feather=defaults.feather if self.feather is None else self.feather,
            indent=defaults.indent if self.indent is None else self.indent,
            quality=self.quality or defaults.quality,
            shift_ms=self.shift_ms,
            fixes=self.fixes,
            video=self.video or defaults.video,
            video_offset_ms=self.video_offset_ms,
            sound=self.sound,
            only=self.only,
        )


@dataclass
class SongDefaults:
    color: str = "#FF6FA5"
    feather: int = 60
    indent: int = 240
    quality: str = "standard"
    background: Optional[Path] = None
    video: Optional[Path] = None      # a video to burn onto when the song has none of its own


def _path(value: Any, base: Path) -> Optional[Path]:
    if not value:
        return None
    p = Path(str(value)).expanduser()
    return p if p.is_absolute() else (base / p)


def loads(data: dict | list, base: Optional[Path] = None) -> tuple[list[Song], SongDefaults]:
    """``(songs, defaults)`` from an already-parsed document (a list is taken as songs only)."""
    base = base or Path.cwd()
    if isinstance(data, list):
        data = {"songs": data}
    raw_defaults = data.get("defaults") or {}
    defaults = SongDefaults(
        color=raw_defaults.get("color") or SongDefaults.color,
        feather=int(raw_defaults.get("feather", SongDefaults.feather)),
        indent=int(raw_defaults.get("indent", SongDefaults.indent)),
        quality=raw_defaults.get("quality") or SongDefaults.quality,
        background=_path(raw_defaults.get("background"), base),
        video=_path(raw_defaults.get("video"), base),
    )
    songs: list[Song] = []
    for i, item in enumerate(data.get("songs") or []):
        if not isinstance(item, dict):
            raise ValueError(f"song #{i + 1} is not a mapping")
        for key in ("name", "audio", "lyrics"):
            if not item.get(key):
                raise ValueError(f"song #{i + 1} has no {key}")
        songs.append(Song(
            name=str(item["name"]),
            audio=_path(item["audio"], base),
            lyrics=str(item["lyrics"]),
            title=str(item.get("title") or ""),
            background=_path(item.get("background"), base),
            color=item.get("color"),
            feather=None if item.get("feather") is None else int(item["feather"]),
            indent=None if item.get("indent") is None else int(item["indent"]),
            quality=item.get("quality"),
            shift_ms=None if item.get("shift_ms") is None else int(item["shift_ms"]),
            fixes={str(k): {str(a): str(b) for a, b in (v or {}).items()}
                   for k, v in (item.get("fixes") or {}).items()},
            video=_path(item.get("video"), base),
            video_offset_ms=int(item.get("video_offset_ms") or 0),
            sound=str(item.get("sound") or "keep"),
            only=str(item.get("only") or "video"),
        ))
    return songs, defaults


def load(path: Path) -> tuple[list[Song], SongDefaults]:
    """Read a songs file: YAML when the name says so (and PyYAML is there), otherwise JSON."""
    path = Path(path).expanduser()
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in (".yaml", ".yml"):
        try:
            import yaml  # type: ignore
        except ImportError:
            raise ValueError("YAML 需要 PyYAML：pip install pyyaml（或者把 song list 写成 .json）") from None
        data = yaml.safe_load(text)
    else:
        data = json.loads(text)
    return loads(data, base=path.parent)
