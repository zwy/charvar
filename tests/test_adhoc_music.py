"""音乐通路单测：run_id 拦截、validate、同步生成落盘、失败留痕、列表（镜像 test_adhoc_tts）。

openrouter_music 层全打桩（不发自网请求）；模型目录注入本地缓存。
"""
from __future__ import annotations

import json
import pathlib
from pathlib import Path

import pytest

from charvar import adhoc_music, openrouter_music


CATALOG = {
    "data": [
        {
            "id": "google/lyria-3-clip-preview",
            "name": "Google: Lyria 3 Clip Preview",
            "supported_parameters": ["seed", "temperature"],
            "description": "30 second duration clips are priced at $0.04 per clip. Lyria 3 music.",
            "pricing": {"prompt": "0", "completion": "0"},
        },
    ]
}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("CHARVAR_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OP_IMAGE_API_KEY", "sk-test")
    cache = pathlib.Path(tmp_path) / "openrouter" / "music-models.json"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(CATALOG), encoding="utf-8")
    return tmp_path


def _music_result(env, *, ext="mp3", cost_real=0.04, seed_sent=None):
    audio = b"ID3" + b"\x00" * 16 if ext == "mp3" else b"\x00" * 64
    return openrouter_music.MusicResult(
        audio=audio,
        ext=ext,
        transcript="",
        text_content="A 30 second clip.",
        usage_cost=cost_real,
        seed_sent=seed_sent,
        request_body={},
    )


# ---------- run_id 与目录 ----------


def test_run_dir_rejects_bad_ids(env):
    for bad in ("adhoc_x", "../etc", "music_..evil", "", "music_"):
        with pytest.raises(adhoc_music.AdhocMusicError):
            adhoc_music.run_dir(bad)
    good = adhoc_music.run_dir("music_20261123-101010_abcd1234")
    assert good == adhoc_music.music_root() / "music_20261123-101010_abcd1234"


def test_audio_file_missing_raises(env):
    adhoc_music.run_dir("music_20261123-101010_abcd1234").mkdir(parents=True, exist_ok=True)
    with pytest.raises(FileNotFoundError):
        adhoc_music.audio_file("music_20261123-101010_abcd1234")


# ---------- validate ----------


def test_validate_rejects_empty_prompt(env):
    with pytest.raises(adhoc_music.AdhocMusicError):
        adhoc_music.validate(adhoc_music.MusicRequest(prompt="   "))
    with pytest.raises(adhoc_music.AdhocMusicError):
        adhoc_music.validate(adhoc_music.MusicRequest(prompt=""))


def test_validate_rejects_bad_seed(env):
    req = adhoc_music.MusicRequest(prompt="x", seed="abc")
    with pytest.raises(adhoc_music.AdhocMusicError, match="seed"):
        adhoc_music.validate(req)


def test_request_seed_roundtrip(env):
    assert adhoc_music.MusicRequest(prompt="x", seed="").request_seed() is None
    assert adhoc_music.MusicRequest(prompt="x", seed="42").request_seed() == 42


# ---------- generate（同步全流程） ----------


def test_generate_happy_path(env, monkeypatch):
    monkeypatch.setattr(openrouter_music, "generate", lambda **kw: _music_result(env))
    result = adhoc_music.generate(adhoc_music.MusicRequest(prompt="funky tune"))
    assert result.provider == "openrouter"
    assert result.model == "google/lyria-3-clip-preview"
    assert result.seed_sent is None
    audio = Path(result.audio_path)
    assert audio.exists() and audio.suffix == ".mp3"
    assert result.size_bytes == len(audio.read_bytes())
    assert result.cost == 0.04  # 目录 description 一口价预估
    assert result.cost_real == 0.04  # usage.cost 实收
    assert result.cost_real is not None
    assert result.run_id.startswith("music_")

    manifest = adhoc_music.read_manifest(result.run_id)
    assert manifest["status"] == "ready"
    assert manifest["request"]["prompt"] == "funky tune"
    assert manifest["result"]["audio_path"] == result.audio_path


def test_generate_seed_passthrough(env, monkeypatch):
    seen = {}

    def fake_generate(**kw):
        seen.update(kw)
        return _music_result(env, seed_sent=123)

    monkeypatch.setattr(openrouter_music, "generate", fake_generate)
    result = adhoc_music.generate(adhoc_music.MusicRequest(prompt="x", seed="123"))
    assert seen["seed"] == 123
    assert result.seed_sent == 123


def test_generate_failure_leaves_manifest(env, monkeypatch):
    def boom(**kw):
        raise openrouter_music.OpenRouterMusicAuthError("鉴权失败", http_status=401)

    monkeypatch.setattr(openrouter_music, "generate", boom)
    with pytest.raises(adhoc_music.AdhocMusicError):
        adhoc_music.generate(adhoc_music.MusicRequest(prompt="x"))
    records = adhoc_music.list_recent()
    failed = [r for r in records if r["status"] == "failed"]
    assert failed and "鉴权失败" in json.dumps(failed[0]["error"], ensure_ascii=False)
    assert failed[0]["has_audio"] is False


def test_generate_out_copy(env, monkeypatch):
    monkeypatch.setattr(openrouter_music, "generate", lambda **kw: _music_result(env))
    out = env / "exported" / "clip.mp3"
    result = adhoc_music.generate(adhoc_music.MusicRequest(prompt="x", out=out))
    assert out.exists()
    assert result.audio_path == str(out)


def test_generate_rejects_bad_seed(env):
    # 非法 seed 在提交前拦(不浪费流式提交)
    with pytest.raises(adhoc_music.AdhocMusicError, match="seed"):
        adhoc_music.generate(adhoc_music.MusicRequest(prompt="x", seed="0x10"))


def test_generate_rejects_empty_prompt(env):
    with pytest.raises(adhoc_music.AdhocMusicError):
        adhoc_music.generate(adhoc_music.MusicRequest(prompt="  "))


# ---------- list_recent ----------


def test_list_recent_order_and_fields(env, monkeypatch):
    monkeypatch.setattr(openrouter_music, "generate", lambda **kw: _music_result(env))
    first = adhoc_music.generate(adhoc_music.MusicRequest(prompt="第一条"))
    second = adhoc_music.generate(adhoc_music.MusicRequest(prompt="第二条", seed="7"))
    records = adhoc_music.list_recent()
    runs = {r["run_id"]: r for r in records}
    assert first.run_id in runs and second.run_id in runs
    for run_id, prompt in ((first.run_id, "第一条"), (second.run_id, "第二条")):
        rec = runs[run_id]
        assert rec["prompt"] == prompt
        assert rec["has_audio"] is True
        assert rec["status"] == "ready"
        assert rec["cost_real"] == 0.04
    assert runs[first.run_id]["model"] == "google/lyria-3-clip-preview"


# ---------- list_recent 翻页 ----------


def test_list_recent_offset_paging(env):
    root = adhoc_music.music_root()
    ids = [f"music_20300101-0000{i:02d}_mm{i}" for i in range(1, 6)]
    for run_id in ids:
        d = root / run_id
        d.mkdir(parents=True)
        (d / "manifest.json").write_text(
            json.dumps({"created_at": "", "status": "ready", "request": {"prompt": run_id}}),
            encoding="utf-8",
        )
    assert adhoc_music.count_recent() == 5
    page1 = adhoc_music.list_recent(limit=3)
    assert [r["run_id"] for r in page1] == list(reversed(ids[2:]))
    assert [r["run_id"] for r in adhoc_music.list_recent(limit=3, offset=3)] == [
        "music_20300101-000002_mm2",
        "music_20300101-000001_mm1",
    ]
    assert adhoc_music.list_recent(limit=3, offset=99) == []
    # 没有 manifest 的目录不算记录
    (root / "music_20300101-000099_zz").mkdir()
    assert adhoc_music.count_recent() == 5
