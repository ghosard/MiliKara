"""Run a list of songs: lyrics, audio, readings, offset, alignment, look, burn — per song.

Two things make this useful in bulk rather than a loop around the CLI:

* **one process per job, the model loaded once per process** — the second song in a worker
  starts with the aligner already in memory (~20 s saved per song after the first);
* **resumable and isolated** — every song has its own project folder (the work done so far is
  reused), an existing ``out/<name>.mp4`` is skipped, and one broken song fails alone while
  the rest of the batch carries on.

The look is the plain template with a soft sweep edge and no glow; per song overrides (colour,
feather, indent, quality, background) come from the song list.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Literal, Optional

from .song import Song, SongDefaults

STAGES = ("lyrics", "audio", "readings", "calibration", "align", "look", "background", "burn")

Event = Callable[[str, str, float], None]  # (song name, stage, progress 0-1)


@dataclass
class Result:
    name: str
    status: Literal["ok", "skipped", "failed"]
    seconds: float = 0.0
    file: Optional[Path] = None
    project: Optional[Path] = None
    units: int = 0
    shift_ms: Optional[int] = None
    readings: str = ""
    warnings: list[str] = field(default_factory=list)
    error: str = ""


def _vignette(src: Path, cache: Path) -> Path:
    """``src`` with a soft dark gradient over its lower part, cached by content hash."""
    from PIL import Image

    cache.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(src.read_bytes()).hexdigest()[:16]
    out = cache / f"{key}.png"
    if out.exists():
        return out
    base = Image.open(src).convert("RGB")
    w = 1920
    h = max(2, round(base.height * w / base.width))
    base = base.resize((w, h), Image.LANCZOS)
    shade = Image.new("L", (w, h), 0)
    px = shade.load()
    start = int(h * 0.55)
    for y in range(start, h):
        a = int(255 * 0.55 * (((y - start) / (h - start)) ** 1.6))
        for x in range(w):
            px[x, y] = a
    Image.composite(Image.new("RGB", (w, h), (6, 2, 12)), base, shade).save(out)
    return out


def apply_fixes(h, fixes: dict[str, dict[str, str]]) -> int:
    """Manual kana for specific segments: ``{line_id: {surface: reading}}``."""
    from .. import service as S

    n = 0
    for line_id, per_surface in (fixes or {}).items():
        line = next((ln for ln in h.project.lyrics.lines if ln.id == line_id), None)
        if line is None:
            continue
        for seg in line.segments:
            reading = per_surface.get(seg.surface)
            if reading:
                S.set_segment_reading(h, line_id, seg.id, reading)
                n += 1
    return n


def look(style_color: str, feather: int, indent: int):
    """The batch look: plain colours, no glow, a soft sweep edge, alternating lines."""
    from ..karaoke.themes import theme_style

    from ..models import KaraokeStyle

    st = theme_style("plain", style_color, KaraokeStyle(), "")
    st.theme = None
    st.glow.enabled = False
    st.translation.enabled = True
    st.translation.position = "opposite"
    st.translation.glow = False
    st.info.enabled = True
    st.layout.position = "bottom"
    st.layout.lines = 2
    st.layout.arrangement = "alternate"
    st.layout.alternate_indent = indent
    st.timing.sweep_engine = "feather" if feather > 0 else "clip"
    st.timing.sweep_feather_px = feather
    st.ruby.sweep = "base"
    return st


def run_song(song: Song, *, root: Path, out_dir: Path, force: bool = False,
             on_event: Optional[Event] = None) -> Result:
    """Everything one song needs.  Never raises: a failure comes back as ``status="failed"``."""
    from .. import service as S

    def stage(name: str, pct: float = 0.0) -> None:
        if on_event:
            on_event(song.name, name, pct)

    t0 = time.time()
    out = out_dir / f"{song.name}.mp4"
    project_dir = root / song.name
    if out.exists() and not force:
        return Result(name=song.name, status="skipped", file=out, project=project_dir)

    try:
        stage("lyrics")
        if (project_dir / "project.json").exists():
            h = S.open_dir(project_dir)
        else:
            project_dir.mkdir(parents=True, exist_ok=True)
            h = S.create_dir(project_dir, song.title or song.name, "lrc")

        if not [ln for ln in h.project.lyrics.lines if ln.segments]:
            source = song.lyrics
            if source.startswith(("http://", "https://", "netease:", "qq:")):
                link = S.fetch_link(source)
                if link["kind"] != "song":
                    raise ValueError(f"链接是一个{link['kind']}，需要单曲链接")
                cs = link["song"]
                pv = S.parse_from_song(h, cs["platform"], cs["song_id"])
                if pv.get("error"):
                    raise ValueError(pv["error"])
                S.apply_lyrics(h, pv["preview_id"])
                text = (pv.get("extra_tracks") or {}).get("translation")
                if text:
                    prev = S.preview_track(h, text, "translation")
                    S.apply_track(h, "translation", prev["pairs"])
            else:
                path = Path(source).expanduser()
                pv = S.parse_lyrics(h, path.read_text(encoding="utf-8"), filename=path.name)
                if pv.get("error"):
                    raise ValueError(pv["error"])
                S.apply_lyrics(h, pv["preview_id"])

        stage("audio")
        if h.project.asset("original") is None:
            audio = Path(song.audio).expanduser()
            if not audio.exists():
                raise FileNotFoundError(f"找不到音频：{audio}")
            S.add_media(h, audio, "original", filename=audio.name, source_kind="upload")

        stage("readings")
        S.prepare_readings(h)
        fixed = apply_fixes(h, song.fixes)

        stage("calibration")
        if song.shift_ms is not None:
            S.calibration_op(h, "shift", user_shift_ms=int(song.shift_ms))
        elif not h.project.calibration.confirmed:
            from ..auto_calibrate import suggest_calibration

            S.calibration_op(h, "shift", user_shift_ms=int(suggest_calibration(h)["shift_ms"]))

        stage("align")
        r = S.run_align(h, audio_role="original")

        stage("look")
        S.set_karaoke_style(h, look(song.color, int(song.feather or 0), int(song.indent or 0)).model_dump())

        stage("background")
        if song.background and not song.video:
            S.set_background(h, _vignette(Path(song.background).expanduser(), root.parent / "backgrounds"))

        if song.only == "ass":
            # subtitles first, picture later: the ASS is in the audio's timeline (absolute ms),
            # so whatever video is edited later can be burned with --video-offset-ms
            out_dir.mkdir(parents=True, exist_ok=True)
            text, warnings = S.karaoke_ass(h)
            ass_path = out_dir / f"{song.name}.ass"
            ass_path.write_text(text, encoding="utf-8")
            (out_dir / f"{song.name}.style.json").write_text(
                json.dumps(h.project.karaoke.model_dump(), ensure_ascii=False, indent=1), encoding="utf-8")
            return Result(name=song.name, status="ok", seconds=round(time.time() - t0, 1),
                          file=ass_path, project=project_dir, units=len(r.units),
                          shift_ms=h.project.calibration.user_shift_ms,
                          readings=f"{fixed} 处读音修正", warnings=list(warnings or []))

        stage("burn")
        if song.video:
            out_dir.mkdir(parents=True, exist_ok=True)
            res = S.burn_over_video(h, Path(song.video).expanduser(), out=out,
                                    offset_ms=int(song.video_offset_ms), sound=song.sound or "keep",
                                    quality=song.quality or "standard")
            return Result(name=song.name, status="ok", seconds=round(time.time() - t0, 1), file=out,
                          project=project_dir, units=len(r.units),
                          shift_ms=h.project.calibration.user_shift_ms,
                          readings=f"{fixed} 处读音修正", warnings=list(res.get("warnings") or []))
        res = S.karaoke_burn(h, background="auto", audio="original", quality=song.quality or "standard")
        produced = project_dir / "exports" / res["filename"]
        out_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(produced, out)
        return Result(name=song.name, status="ok", seconds=round(time.time() - t0, 1), file=out,
                      project=project_dir, units=len(r.units),
                      shift_ms=h.project.calibration.user_shift_ms, readings=f"{fixed} 处读音修正",
                      warnings=list(res.get("warnings") or []))
    except Exception as e:
        return Result(name=song.name, status="failed", file=None, project=project_dir,
                      seconds=round(time.time() - t0, 1),
                      error=f"{type(e).__name__}: {e}",
                      warnings=[traceback.format_exc()[-800:]])


def _work(payload) -> Result:
    song, root, out_dir, force = payload
    return run_song(song, root=Path(root), out_dir=Path(out_dir), force=force)


def run(songs: Iterable[Song], *, defaults: Optional[SongDefaults] = None, root: Optional[Path] = None,
        out_dir: Optional[Path] = None, jobs: int = 1, force: bool = False,
        on_event: Optional[Event] = None) -> list[Result]:
    """Make every song; ``jobs > 1`` runs them in parallel processes (one model load each)."""
    from ..project.store import projects_root

    defaults = defaults or SongDefaults()
    songs = [s.resolved(defaults) for s in songs]
    root = Path(root) if root else projects_root() / "batch"
    out_dir = Path(out_dir) if out_dir else Path.cwd() / "out"
    if jobs <= 1:
        return [run_song(s, root=root, out_dir=out_dir, force=force, on_event=on_event) for s in songs]

    import multiprocessing as mp

    payload = [(s, str(root), str(out_dir), force) for s in songs]
    results: list[Result] = []
    with mp.Pool(processes=jobs) as pool:
        for res in pool.imap_unordered(_work, payload):
            results.append(res)
            if on_event:
                on_event(res.name, "done" if res.status != "failed" else "failed", 1.0)
    order = {s.name: i for i, s in enumerate(songs)}
    return sorted(results, key=lambda r: order.get(r.name, 0))
