"""配方（presets/recipes/）：解析校验 / 线性执行器 / 检查点 / manifest 落盘。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from charvar import recipes as recipes_service
from charvar import storage
from charvar.assets import original_dir, run_assets_dir, safe_run_id
from charvar.models import GenerateResult, PromptItem
from charvar.preset_loader import PresetError


_RECIPE_YAML = """
name: demo_blend
type: recipe
version: 1
title: 演示配方
doc: demo_blend.md
subject: demo_witch
seed: 11
steps:
- kind: images
  label: 主图
  presets: [demo_pose]
  run_id: demo_bound_run
- kind: overlay
  label: 贴字
  overlay: demo_bl
  input: 主图
  text_from: outfit_label
- kind: video_synth
  label: 成片
  video_preset: demo_video
  input: 主图
"""

# 动作预设（与 demo_blend 同目录约定：presets/ 顶层）
_DEMO_POSE = """
name: demo_pose
type: list
version: 1
master_template: 'Create an image of the {subject_noun} from {reference_tag}.'
suffix_template: 'The background is {background}.'
variants:
- id: wave_hand
  text: 人物正面朝向镜头站立，单手举起挥手。
  framing: full
  angle: front
  pose: wave_hand
  tags: [full, action, wave_hand]
- id: stand_still
  text: 人物正面朝向镜头站立。
  framing: full
  angle: front
  pose: stand_still
  tags: [full, action, stand_still]
"""

# 视频合成预设（fit=contain 黑底，1:1，无音乐）——与本测试的 320×320 成品图同比例
_DEMO_VIDEO = """
name: demo_video
type: video
version: 1
aspect: '1:1'
resolution: 720p
fps: 12
seconds_per_image: 0.25
include_contact_sheet: false
transition: cut
fit: contain
background: black
order_mode: sequential
"""

_OVERLAY_YAML = """
name: demo_bl
type: overlay
version: 1
position: left_bottom
font: ""
size: 0.05
color: "#FFFFFF"
plate: pill
"""


def _write_fixtures(tmp_path: Path) -> None:
    presets = tmp_path / "presets"
    (presets / "recipes").mkdir(parents=True, exist_ok=True)
    (presets / "overlays").mkdir(exist_ok=True)
    (presets / "video").mkdir(exist_ok=True)
    (presets / "recipes" / "demo_blend.yaml").write_text(_RECIPE_YAML, encoding="utf-8")
    (presets / "recipes" / "demo_blend.md").write_text("# 演示配方文档\n\nhello", encoding="utf-8")
    (presets / "demo_pose.yaml").write_text(_DEMO_POSE, encoding="utf-8")
    (presets / "overlays" / "demo_bl.yaml").write_text(_OVERLAY_YAML, encoding="utf-8")
    (presets / "video" / "demo_video.yaml").write_text(_DEMO_VIDEO, encoding="utf-8")


def _bound_run(tmp_path: Path) -> str:
    """建一个成品图齐全的既有 run（供 images 步绑定）。"""
    run_id = "demo_bound_run"
    dir_ = original_dir(run_id)
    dir_.mkdir(parents=True, exist_ok=True)
    for index, vid in enumerate(("wave_hand", "stand_still")):
        Image.new("RGBA", (320, 320), (140, 40, 40, 255)).save(dir_ / f"{index:06d}.png")
    items = [
        PromptItem(
            index=index,
            variant_id=vid,
            prompt="p", master="m", variant_text="t",
            background=None, lighting=None, caption="c",
            meta={"pose": vid, "outfit_label": f"服装{vid}"},
        )
        for index, vid in enumerate(("wave_hand", "stand_still"))
    ]
    result = GenerateResult(run_id=run_id, subject_id="demo_witch", preset="demo_pose", seed=11, items=items)
    with storage.connect(tmp_path / "test.db") as conn:
        storage.save_run(conn, result)
    return run_id


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """tmp 数据目录 + tmp 预设目录 + 已绑定的成品 run（返回 db conn）。"""
    monkeypatch.setenv("CHARVAR_DATA_DIR", str(tmp_path))
    _write_fixtures(tmp_path)
    monkeypatch.setenv("CHARVAR_PRESETS_DIR", str(tmp_path / "presets"))
    _bound_run(tmp_path)
    with storage.connect(tmp_path / "test.db") as conn:
        yield conn


def test_parse_repo_recipe():
    """仓库自带配方解析合法；文档能读。"""
    recipe = recipes_service.load_recipe("stockings_showcase")
    assert recipe.name == "stockings_showcase"
    assert [s.kind for s in recipe.steps] == ["images", "images", "overlay", "video_synth"]
    assert recipes_service.category_of(recipe) == "synthesis"
    doc = recipes_service.read_doc("stockings_showcase")
    assert doc and "丝袜橱窗" in doc


def test_parse_rejects_errors(env):
    base = recipes_service.recipes_dir()
    # 未知根字段
    (base / "bad_unknown.yaml").write_text(_RECIPE_YAML + "whatever: 1\n", encoding="utf-8")
    with pytest.raises(PresetError):
        recipes_service.parse_recipe_file(base / "bad_unknown.yaml")
    # label 重复（input 按 label 引用，必须唯一）
    dup = _RECIPE_YAML.replace("label: 贴字", "label: 主图")
    (base / "bad_dup.yaml").write_text(dup, encoding="utf-8")
    with pytest.raises(PresetError):
        recipes_service.parse_recipe_file(base / "bad_dup.yaml")
    # 消耗步没有前置 images 步骤
    no_src = "name: bad_nosrc\ntype: recipe\nversion: 1\nsteps:\n- kind: overlay\n  label: 贴字\n  overlay: demo_bl\n"
    (base / "bad_nosrc.yaml").write_text(no_src, encoding="utf-8")
    with pytest.raises(PresetError):
        recipes_service.parse_recipe_file(base / "bad_nosrc.yaml")
    # 画幅没加引号（YAML 读成 sexagesimal int）
    bad = _RECIPE_YAML.replace("label: 主图\n  presets: [demo_pose]", "label: 主图\n  presets: [demo_pose]\n  aspect: 9:16")
    (base / "bad_aspect.yaml").write_text(bad, encoding="utf-8")
    with pytest.raises(PresetError):
        recipes_service.parse_recipe_file(base / "bad_aspect.yaml")
    # 非法 size
    bad = _RECIPE_YAML.replace("label: 主图\n  presets: [demo_pose]", "label: 主图\n  presets: [demo_pose]\n  size: 4k")
    (base / "bad_size.yaml").write_text(bad, encoding="utf-8")
    with pytest.raises(PresetError):
        recipes_service.parse_recipe_file(base / "bad_size.yaml")
    # overlay 步缺 overlay 名
    bad = _RECIPE_YAML.replace("  overlay: demo_bl\n", "")
    (base / "bad_nooverlay.yaml").write_text(bad, encoding="utf-8")
    with pytest.raises(PresetError):
        recipes_service.parse_recipe_file(base / "bad_nooverlay.yaml")


def test_start_run_stops_at_manual_checkpoint(env):
    state = recipes_service.start_run(env, "demo_blend")
    # images（bind，非 manual）自动 done；overlay 是 manual → waiting
    assert [s.status for s in state.steps] == ["done", "pending", "pending"]
    assert state.status == "waiting"
    assert state.steps[0].outputs["run_id"] == "demo_bound_run"
    assert "等待人工" in state.steps[1].note
    # manifest 落盘
    assert recipes_service.manifest_path(state.recipe_run_id).exists()
    on_disk = json.loads(recipes_service.manifest_path(state.recipe_run_id).read_text(encoding="utf-8"))
    assert on_disk["status"] == "waiting"


def test_full_pipeline_through_checkpoints(env):
    state = recipes_service.start_run(env, "demo_blend")
    # 检查点 1：贴字（manual → advance 执行后停在成片前）
    state = recipes_service.advance_state(env, state.recipe_run_id)
    assert state.status == "waiting"  # 成片步 manual
    assert state.steps[1].status == "done"
    assert state.steps[1].outputs["run_id"] == "demo_bound_run"
    labeled_dir = Path(state.steps[1].outputs["overlay_dir"])
    assert (labeled_dir / "000000.png").exists()
    # 检查点 2：成片（吃 labeled/ 产物）
    state = recipes_service.advance_state(env, state.recipe_run_id)
    assert state.status == "done"
    assert state.steps[2].status == "done"
    assert state.steps[2].outputs["images_dir"] == str(labeled_dir)
    video_name = state.steps[2].outputs["video"]["name"]
    assert (run_assets_dir("demo_bound_run") / "videos" / video_name).exists()


def test_images_step_creates_run_without_push(env):
    """不绑 run：images 步建批（classic 建提示词）；成品缺失 → 消耗步带明确原因停。"""
    from charvar.models import SubjectProfile

    storage.save_subject(env, SubjectProfile(
        id="demo_witch", name="演示", subject_noun="woman", trigger_word="sw1tch",
        reference_tag="<image1>",
    ))
    recipe = recipes_service.load_recipe("demo_blend")
    recipe.steps[0].run_id = None  # 不绑定 → 建批
    state = recipes_service.RecipeRunState(
        recipe=recipe.name, recipe_run_id=recipes_service._new_recipe_run_id(recipe, 11),
        created_at="x", seed=11,
        steps=[recipes_service.StepState(index=i, kind=s.kind, label=s.label,
                                         manual=recipes_service.manual_default(s))
               for i, s in enumerate(recipe.steps)],
    )
    state = recipes_service.advance(env, state, recipe)
    assert state.steps[0].status == "done"
    saved_run = state.steps[0].outputs["run_id"]
    safe_run_id(saved_run)
    assert saved_run.startswith("demo_witch_demo_pose_s11_")
    # overlay manual：过目（其实没成品）后 continue → gate 停，note 指明成品 0/2
    state = recipes_service.advance_state(env, state.recipe_run_id)
    assert state.status == "waiting"
    assert state.steps[1].status == "pending"
    assert "成品 0/2 未齐" in state.steps[1].note


def test_recipe_runs_listing(env):
    state = recipes_service.start_run(env, "demo_blend")
    runs = recipes_service.list_recipe_runs()
    assert any(r["recipe_run_id"] == state.recipe_run_id for r in runs)


def test_input_defaults_to_latest_images_step(env):
    """input 缺省 = 最近的 images 步；引用不存在 label 运行期报错。"""
    recipe = recipes_service.load_recipe("demo_blend")
    recipe.steps[1].input = None
    state = recipes_service.RecipeRunState(
        recipe=recipe.name, recipe_run_id="recipe_x", created_at="x", seed=1,
        steps=[recipes_service.StepState(index=0, kind="images", label="主图", manual=False,
                                         status="done", outputs={"run_id": "demo_bound_run"}),
               recipes_service.StepState(index=1, kind="overlay", label="贴字", manual=False),
               recipes_service.StepState(index=2, kind="video_synth", label="成片", manual=False)],
    )
    hit = recipes_service._resolve_input_step(recipe, state, recipe.steps[2], 2)
    assert hit.outputs["run_id"] == "demo_bound_run"
    recipe.steps[2].input = "不存在"
    with pytest.raises(PresetError):
        recipes_service._resolve_input_step(recipe, state, recipe.steps[2], 2)


def test_step_count_mismatch_refused(env):
    """manifest 与配方定义步数不一致（定义改版）→ 要求重建，不静默。"""
    state = recipes_service.start_run(env, "demo_blend")
    recipe = recipes_service.load_recipe("demo_blend")
    recipe.steps.append(recipes_service.RecipeStep(kind="overlay", label="多余", overlay="demo_bl"))
    (recipes_service.recipes_dir() / "demo_blend.yaml").write_text(
        _RECIPE_YAML + "- kind: overlay\n  label: 多余\n  overlay: demo_bl\n", encoding="utf-8"
    )
    with pytest.raises(PresetError):
        recipes_service.advance_state(env, state.recipe_run_id)


# ---- API 冒烟 ----

def test_api_recipe_endpoints(tmp_path, monkeypatch):
    import pathlib

    from fastapi.testclient import TestClient

    from charvar.api import build_app
    from charvar.models import SubjectProfile

    monkeypatch.setenv("CHARVAR_DATA_DIR", str(tmp_path))
    _write_fixtures(tmp_path)
    monkeypatch.setenv("CHARVAR_PRESETS_DIR", str(tmp_path / "presets"))
    _bound_run(tmp_path)
    with storage.connect(tmp_path / "test.db") as conn:
        storage.save_subject(conn, SubjectProfile(
            id="demo_witch", name="演示", subject_noun="woman", trigger_word="sw1tch",
            reference_tag="<image1>",
        ))
    app = build_app(static_dir=None, db_path=pathlib.Path(tmp_path) / "test.db")
    client = TestClient(app)

    listed = client.get("/recipes").json()
    assert any(r["name"] == "demo_blend" for r in listed)
    detail = client.get("/recipes/demo_blend").json()
    assert detail["doc"].startswith("# 演示配方文档")
    assert detail["category"] == "synthesis"

    # run：绑定了既有成品的 images 步 → done，overlay manual → waiting
    run = client.post("/recipes/demo_blend/run", json={"seed": 11}).json()
    assert run["status"] == "waiting"
    assert run["steps"][0]["status"] == "done"
    rid = run["recipe_run_id"]
    assert client.get(f"/recipe-runs/{rid}").json()["recipe"] == "demo_blend"
    # 非法 id 形态 = 400；合法形态但不存在 = 404
    assert client.get("/recipe-runs/../../etc/passwd").status_code in (400, 404)
    assert client.get("/recipe-runs/recipe_not_created_x").status_code == 404

    # continue：贴字完成（有成品图），停在成片检查点
    cont = client.post(f"/recipe-runs/{rid}/continue")
    assert cont.status_code == 200
    assert cont.json()["steps"][1]["status"] == "done"
    # 成片产物在最后 continue 落盘
    final = client.post(f"/recipe-runs/{rid}/continue").json()
    video_name = final["steps"][2]["outputs"]["video"]["name"]
    assert (run_assets_dir("demo_bound_run") / "videos" / video_name).exists()