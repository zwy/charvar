"""设置服务：默认值、partial 合并、api_key 掩码、llm_overrides。"""
from __future__ import annotations

import pathlib

import pytest
from fastapi.testclient import TestClient

from charvar.api import build_app
from charvar.settings_service import default_image_sizes, get_settings, llm_overrides
from charvar import storage


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CHARVAR_DATA_DIR", str(tmp_path))
    for key in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL"):
        monkeypatch.delenv(key, raising=False)
    app = build_app(static_dir=None, db_path=pathlib.Path(tmp_path) / "test.db")
    return TestClient(app)


def test_defaults(client):
    data = client.get("/settings").json()
    assert data["comfy_t2i_workflow"] == "image_qwen_image_2_1_turbo_t2i_easy_api"
    assert data["comfy_edit_workflow"] == "image_qwen_image_2_1_turbo_image_edit_easy_api"
    assert set(data["image_sizes"].keys()) == {"character_sheet", "headshot", "front", "side", "behind"}
    assert data["image_sizes"]["front"]["aspect_ratio"] == "2:3"
    assert data["image_sizes"]["character_sheet"]["aspect_ratio"] == "3:4"
    assert data["llm_configured"] is False
    assert data["llm"]["api_key"] == ""


def test_partial_merge_persists(client, tmp_path):
    client.put("/settings", json={"default_negative_prompt": "blurry"})
    client.put("/settings", json={"llm": {"model": "m-1"}})
    data = client.get("/settings").json()
    assert data["default_negative_prompt"] == "blurry"
    assert data["llm"]["model"] == "m-1"
    # 直接读库确认持久化
    conn = storage.connect(pathlib.Path(tmp_path) / "test.db")
    assert get_settings(conn).default_negative_prompt == "blurry"


def test_api_key_masked(client):
    put = client.put("/settings", json={"llm": {"api_key": "sk-secret"}}).json()
    assert put["llm"]["api_key"] == ""
    assert put["llm_configured"] is True
    got = client.get("/settings").json()
    assert got["llm"]["api_key"] == ""
    assert got["llm_configured"] is True
    # 未知键被忽略而不是报错
    client.put("/settings", json={"bogus_key": 1})


def test_llm_overrides_blank_means_none(client, tmp_path, monkeypatch):
    monkeypatch.setenv("CHARVAR_DATA_DIR", str(tmp_path))
    conn = storage.connect(pathlib.Path(tmp_path) / "test.db")
    assert llm_overrides(conn) is None
    storage.set_setting(conn, "llm", {"model": "m", "api_key": "k"})
    overrides = llm_overrides(conn)
    assert overrides == {"model": "m", "api_key": "k"}


def test_llm_api_key_kept_when_blank(client, tmp_path):
    db = pathlib.Path(tmp_path) / "test.db"
    client.put("/settings", json={"llm": {"api_key": "sk-keep", "base_url": "https://old"}})
    # 整行覆盖 + api_key 空 = 保留已存 key（读接口掩码无法回填，空串只能是「没改」）
    data = client.put("/settings", json={"llm": {"base_url": "https://new", "model": "m"}}).json()
    assert data["llm_configured"] is True
    settings = get_settings(storage.connect(db))
    assert settings.llm.api_key == "sk-keep"
    assert settings.llm.base_url == "https://new"
    assert settings.llm.model == "m"
    # 显式传新 key 正常覆盖
    client.put("/settings", json={"llm": {"api_key": "sk-new"}})
    settings = get_settings(storage.connect(db))
    assert settings.llm.api_key == "sk-new"


def test_default_image_sizes_shape():
    sizes = default_image_sizes()
    assert sizes["character_sheet"].aspect_ratio == "3:4"
    assert sizes["behind"].aspect_ratio == "2:3"
    assert all(s.resolution == "1k" for s in sizes.values())


# ---------- OpenRouter 按模式配置（t2i 文生图 / i2i 图片编辑） ----------


def test_openrouter_per_mode_fields_and_legacy_migration(client, tmp_path):
    """旧的单模型配置（openrouter.model）迁移进 t2i/i2i_model；新字段各自可覆盖。"""
    db = pathlib.Path(tmp_path) / "test.db"
    client.put("/settings", json={"openrouter": {"model": "qwen/qwen-image-2.1-turbo"}})
    settings = get_settings(storage.connect(db))
    assert settings.openrouter.t2i_model == "qwen/qwen-image-2.1-turbo"
    assert settings.openrouter.i2i_model == "qwen/qwen-image-2.1-turbo"
    # 设置了 t2i_model 后 i2i 仍回退旧值；单独设置 i2i_model 则各自生效
    client.put(
        "/settings",
        json={"openrouter": {"t2i_model": "a/t2i", "i2i_model": "b/i2i", "api_key": ""}},
    )
    settings = get_settings(storage.connect(db))
    assert settings.openrouter.t2i_model == "a/t2i"
    assert settings.openrouter.i2i_model == "b/i2i"


def test_openrouter_mode_config_merges_env(client, tmp_path, monkeypatch):
    """openrouter_mode_config：DB > env（model 走 resolve_model 全链路）。"""
    from charvar import settings_service

    monkeypatch.setenv("OP_IMAGE_T2I_MODEL", "env/t2i")
    monkeypatch.setenv("OP_IMAGE_I2I_RESOLUTION", "4K")
    db = pathlib.Path(tmp_path) / "test.db"
    client.put(
        "/settings",
        json={"openrouter": {"t2i_model": "db/t2i", "t2i_resolution": "2K", "t2i_quality": "high", "api_key": ""}},
    )
    modes = settings_service.openrouter_mode_config(storage.connect(db))
    assert modes["t2i"] == {"model": "db/t2i", "resolution": "2K", "quality": "high"}
    # i2i：模型没配 → env 链（i2i env 缺 → 旧 OP_IMAGE_MODEL 缺 → 默认）；分辨率取 env
    assert modes["i2i"]["model"] == "meta/muse-image"
    assert modes["i2i"]["resolution"] == "4K"
    assert modes["i2i"]["quality"] == ""


# ---------- 即梦（Dreamina）设置 ----------

def test_dreamina_in_settings_payload(client):
    data = client.get("/settings").json()
    assert data["dreamina"] == {"model": "", "resolution": ""}
    assert isinstance(data["dreamina_cli_available"], bool)


def test_dreamina_roundtrip_and_full_dict_overwrite(client):
    """dreamina 与 llm/openrouter 同一存储惯例：顶层键整行覆盖，调用方要发全量 dict。"""
    client.put("/settings", json={"dreamina": {"model": "5.0Pro", "resolution": "4K"}})
    data = client.get("/settings").json()
    assert data["dreamina"]["model"] == "5.0Pro"
    assert data["dreamina"]["resolution"] == "4K"
    # 整行覆盖（和 openrouter 一样）：新 dict 里没有的子键会被清掉
    client.put("/settings", json={"dreamina": {"resolution": ""}})
    data = client.get("/settings").json()
    assert data["dreamina"]["model"] == ""
    assert data["dreamina"]["resolution"] == ""


def test_dreamina_config_chain(tmp_path, monkeypatch):
    monkeypatch.setenv("CHARVAR_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DREAMINA_IMAGE_MODEL", raising=False)
    monkeypatch.delenv("DREAMINA_IMAGE_RESOLUTION", raising=False)
    from charvar import storage
    from charvar.settings_service import dreamina_config

    conn = storage.connect(pathlib.Path(tmp_path) / "t.db")
    # env > 缺省
    monkeypatch.setenv("DREAMINA_IMAGE_RESOLUTION", "4K")
    cfg = dreamina_config(conn)
    assert cfg == {"model": "", "resolution": "4k"}
    monkeypatch.delenv("DREAMINA_IMAGE_RESOLUTION", raising=False)
    # DB > env
    monkeypatch.setenv("DREAMINA_IMAGE_RESOLUTION", "4K")
    storage.set_setting(conn, "dreamina", {"model": "4.7", "resolution": "2K"})
    cfg = dreamina_config(conn)
    assert cfg == {"model": "4.7", "resolution": "2k"}
