"""Batch runs: the song list is data, the run is resumable, a broken song fails alone."""

from pathlib import Path

import pytest

from kara_align.batch import Song, SongDefaults, loads, run
from kara_align.batch import song as song_mod


def test_song_defaults_fill_unset_fields():
    songs, defaults = loads({
        "defaults": {"color": "#123456", "feather": 20, "quality": "high", "background": "bg.png"},
        "songs": [{"name": "a", "audio": "a.m4a", "lyrics": "netease:1"},
                  {"name": "b", "audio": "b.m4a", "lyrics": "lrc.txt", "color": "#AAAAAA", "quality": "standard"}],
    }, base=Path("/tmp/x"))
    a, b = (s.resolved(defaults) for s in songs)
    assert a.color == "#123456" and a.feather == 20 and a.quality == "high"
    assert a.background == Path("/tmp/x/bg.png") and a.title == "a"
    assert b.color == "#AAAAAA" and b.quality == "standard" and b.feather == 20


def test_paths_are_relative_to_the_song_list():
    songs, defaults = loads({"songs": [{"name": "a", "audio": "sub/a.m4a", "lyrics": "a.lrc"}]},
                            base=Path("/music/list"))
    assert songs[0].audio == Path("/music/list/sub/a.m4a")
    songs, _ = loads({"songs": [{"name": "a", "audio": "/abs/a.m4a", "lyrics": "a.lrc"}]})
    assert songs[0].audio == Path("/abs/a.m4a")


def test_a_song_missing_its_audio_or_lyrics_is_refused():
    with pytest.raises(ValueError, match="audio"):
        loads({"songs": [{"name": "a", "lyrics": "netease:1"}]})
    with pytest.raises(ValueError, match="lyrics"):
        loads({"songs": [{"name": "a", "audio": "a.m4a"}]})


def test_readings_fixes_are_strings_keyed_by_line():
    songs, _ = loads({"songs": [{"name": "a", "audio": "a.m4a", "lyrics": "a.lrc",
                                 "fixes": {"L0006": {"終": "つい"}}}]})
    assert songs[0].fixes == {"L0006": {"終": "つい"}}


def test_an_existing_output_is_skipped_without_touching_the_project(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "done.mp4").write_bytes(b"x")
    song = Song(name="done", audio=tmp_path / "missing.m4a", lyrics="netease:1")
    [res] = run([song], root=tmp_path / "projects", out_dir=out, jobs=1)
    assert res.status == "skipped" and res.file == out / "done.mp4"


def test_a_broken_song_fails_alone(tmp_path):
    lrc = tmp_path / "x.lrc"
    lrc.write_text("[00:01.00]懐かし空\n[00:05.00]雲は流れ\n", encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()
    (out / "ok.mp4").write_bytes(b"x")
    songs = [Song(name="ok", audio=tmp_path / "x.m4a", lyrics=str(lrc)),
             Song(name="bad", audio=tmp_path / "nope.m4a", lyrics=str(lrc))]
    results = {r.name: r for r in run(songs, root=tmp_path / "projects", out_dir=out, jobs=1)}
    assert results["ok"].status == "skipped"
    assert results["bad"].status == "failed"          # a missing audio file fails that song only
    assert "找不到音频" in results["bad"].error


def test_the_batch_look_has_no_glow_and_a_soft_edge():
    from kara_align.batch.runner import look

    st = look("#FF6FA5", 60, 240)
    assert st.glow.enabled is False
    assert st.timing.sweep_engine == "feather" and st.timing.sweep_feather_px == 60
    assert st.layout.arrangement == "alternate" and st.layout.alternate_indent == 240
    assert st.translation.enabled and st.info.enabled
    plain = look("#FF6FA5", 0, 240)
    assert plain.timing.sweep_engine == "clip" and plain.timing.sweep_feather_px == 0


def test_video_and_ass_only_are_song_data():
    songs, defaults = loads({"songs": [
        {"name": "cut", "audio": "a.m4a", "lyrics": "a.lrc", "video": "cut.mp4", "video_offset_ms": 10000,
         "sound": "original"},
        {"name": "subs", "audio": "a.m4a", "lyrics": "a.lrc", "only": "ass"},
    ]})
    cut, subs = (s.resolved(defaults) for s in songs)
    assert cut.video is not None and cut.video.name == "cut.mp4"
    assert cut.video_offset_ms == 10000 and cut.sound == "original" and cut.only == "video"
    assert subs.only == "ass" and subs.video is None
