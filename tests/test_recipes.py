"""配方（presets/recipes/）：解析校验 / 线性执行器 / 检查点 / manifest 落盘。"""
from __future__ import annotations

import json
import os
import re
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
    demo = recipes_service.load_recipe("model_clip_demo")
    assert [s.kind for s in demo.steps] == ["adhoc_image", "video_model"]
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


def test_images_step_fixed_background_lighting(env):
    """fixed_background/fixed_lighting：images 步透传给 GenerateRequest（覆盖通配符池）。"""
    from charvar.models import SubjectProfile

    storage.save_subject(env, SubjectProfile(
        id="demo_witch", name="演示", subject_noun="woman", trigger_word="sw1tch",
        reference_tag="<image1>",
    ))
    recipe = recipes_service.load_recipe("demo_blend")
    recipe.steps[0].run_id = None
    recipe.steps[0].fixed_background = "gray"
    recipe.steps[0].fixed_lighting = "flat and shadowless"
    state = recipes_service.RecipeRunState(
        recipe=recipe.name, recipe_run_id=recipes_service._new_recipe_run_id(recipe, 11),
        created_at="x", seed=11,
        steps=[recipes_service.StepState(index=i, kind=s.kind, label=s.label,
                                         manual=recipes_service.manual_default(s))
               for i, s in enumerate(recipe.steps)],
    )
    state = recipes_service.advance(env, state, recipe)
    assert state.steps[0].status == "done"
    saved = storage.get_run(env, state.steps[0].outputs["run_id"])
    assert all(item.background == "gray" for item in saved.items)
    assert all(item.lighting == "flat and shadowless" for item in saved.items)
    # 固定值也进提示词（suffix 由 preset 后缀模板渲染）
    assert "The background is gray." in saved.items[0].prompt


def test_images_step_llm_mode(env, monkeypatch):
    """mode: llm → LLM 改写批次（模板/参考图槽位校验，LLM 结果替代 classic 拼接）。"""
    import charvar.subject_images as subject_images
    from charvar.composer import make_run_id
    from charvar.models import SubjectProfile

    storage.save_subject(env, SubjectProfile(
        id="demo_witch", name="演示", subject_noun="woman", trigger_word="sw1tch",
        reference_tag="<image1>",
    ))
    recipe = recipes_service.load_recipe("demo_blend")
    recipe.steps[0].run_id = None
    recipe.steps[0].mode = "llm"
    recipe.steps[0].reference_kinds = ["front"]
    recipe.steps[0].llm_instructions = "角度写极端明确"

    def _state():
        return recipes_service.RecipeRunState(
            recipe=recipe.name, recipe_run_id=recipes_service._new_recipe_run_id(recipe, 11),
            created_at="x", seed=11,
            steps=[recipes_service.StepState(index=i, kind=s.kind, label=s.label,
                                             manual=recipes_service.manual_default(s))
                   for i, s in enumerate(recipe.steps)],
        )

    # 参考图槽位未就绪 → 该步 failed（校验先于 LLM 调用，不烧 token）
    state = recipes_service.advance(env, _state(), recipe)
    assert state.steps[0].status == "failed"
    assert "参考图未就绪" in (state.steps[0].note or "")
    # 槽位就绪但没模板也要拦
    class _Ready:
        is_ready = True
    monkeypatch.setattr(subject_images, "get_slot", lambda *a, **kw: _Ready())
    state = recipes_service.advance(env, _state(), recipe)
    assert state.steps[0].status == "failed"
    assert "提示词模板" in (state.steps[0].note or "")

    storage.save_subject_template(env, "demo_witch", "# template body")
    seen: dict = {}

    def fake_generate_llm(subject, preset, req, template_md, kinds, *, config=None):
        seen.update(req=req, template_md=template_md, kinds=kinds, config=config)
        items = [PromptItem(index=i, variant_id=spec.id, prompt=f"LLM 拟的提示词 {i}",
                            master="", variant_text=spec.text,
                            background=req.fixed_background, lighting=None, caption="c", meta={})
                 for i, spec in enumerate(preset.variants)]
        return GenerateResult(run_id=make_run_id(subject.id, preset.name, req.seed),
                              subject_id=subject.id, preset=preset.name, seed=req.seed, items=items)

    import charvar.llm_compose as llm_compose
    monkeypatch.setattr(llm_compose, "generate_llm", fake_generate_llm)
    state = recipes_service.advance(env, _state(), recipe)
    assert state.steps[0].status == "done"
    assert seen["kinds"] == ["front"]
    assert seen["template_md"] == "# template body"
    assert seen["req"].mode == "llm"
    assert seen["req"].llm_instructions == "角度写极端明确"
    saved = storage.get_run(env, state.steps[0].outputs["run_id"])
    assert [item.prompt for item in saved.items] == ["LLM 拟的提示词 0", "LLM 拟的提示词 1"]


def test_push_comfyui_passes_overrides(env, monkeypatch):
    """push: comfyui 的参考图必须经 overrides 传给 submit（回归：曾丢失 →
    mapping 可选槽位不注入，节点按 workflow 内嵌默认图出图）。"""
    import charvar.assets as assets_mod
    import charvar.paths as paths_mod
    from charvar import subject_images
    from charvar.exporters import comfyui as comfyui_exporter
    from charvar.models import SubjectProfile

    storage.save_subject(env, SubjectProfile(
        id="demo_witch", name="演示", subject_noun="woman", trigger_word="sw1tch",
        reference_tag="<image1>",
    ))
    sd = subject_images.subject_dir("demo_witch")
    sd.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (64, 64), (200, 180, 120, 255)).save(sd / "front.png")

    recipe = recipes_service.load_recipe("demo_blend")
    recipe.steps[0].run_id = None
    recipe.steps[0].push = "comfyui"
    recipe.steps[0].reference_kinds = ["front"]
    recipe.steps[0].workflow = "fake_edit"
    state = recipes_service.RecipeRunState(
        recipe=recipe.name, recipe_run_id=recipes_service._new_recipe_run_id(recipe, 11),
        created_at="x", seed=11,
        steps=[recipes_service.StepState(index=i, kind=s.kind, label=s.label,
                                         manual=recipes_service.manual_default(s))
               for i, s in enumerate(recipe.steps)],
    )
    calls: dict = {}
    monkeypatch.setattr(paths_mod, "comfy_base_url", lambda: "http://comfy:8188")
    monkeypatch.setattr(comfyui_exporter, "load_workflow_pair",
                        lambda name: ({"1": {"class_type": "Stub", "inputs": {}}},
                                      {"mappings": [{"field": "image", "node_id": "1",
                                                     "input_key": "image",
                                                     "required": False, "default": None}]}))
    monkeypatch.setattr(comfyui_exporter, "upload_image",
                        lambda base, path, *, remote_name=None: calls.__setitem__(
                            "remote_name", remote_name) or "demo_witch_front.jpg")

    def fake_submit(result, workflow, mapping, comfy_url=None, overrides=None, wait=False,
                    aspect_ratio=None, resolution=None, on_submitted=None):
        calls["overrides"] = dict(overrides or {})
        calls["workflow_name_shape"] = sorted(workflow.keys())
        dir_ = original_dir(result.run_id)
        dir_.mkdir(parents=True, exist_ok=True)
        for item in result.items:
            Image.new("RGBA", (320, 320), (90, 90, 90, 255)).save(dir_ / f"{item.index:06d}.png")
        return []

    monkeypatch.setattr(comfyui_exporter, "submit", fake_submit)
    monkeypatch.setattr(assets_mod, "sync_run_outputs", lambda *a, **kw: None)

    state = recipes_service.advance(env, state, recipe)
    assert state.steps[0].status == "done"
    assert calls["remote_name"] == "demo_witch_front.jpg"
    # 回归断言：overrides 到达 submit（此前丢失 → 全部节点跑 workflow 内嵌默认图）
    assert calls["overrides"] == {"image": "demo_witch_front.jpg"}


def test_recipe_runs_listing(env):
    state = recipes_service.start_run(env, "demo_blend")
    runs = recipes_service.list_recipe_runs()
    assert any(r["recipe_run_id"] == state.recipe_run_id for r in runs)


def test_recipe_runs_newest_first_and_time_format(env):
    """列表按时间倒序（与历史页同语义）；时间格式与 runs 表一致（空格分隔）。

    顺带锁老 manifest 的兼容：ISO T 分隔的旧条目归一化成空格串参与排序与展示。
    """
    state = recipes_service.start_run(env, "demo_blend")
    assert "T" not in state.created_at
    assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$", state.created_at)
    recipe = recipes_service.load_recipe("demo_blend")

    fake = [
        ("recipe_future_run", "2099-01-01 00:00:00"),
        ("recipe_old_iso_run", "2020-01-01T00:00:00"),
    ]
    for run_id, created in fake:
        recipes_service.write_state(recipes_service.RecipeRunState(
            recipe=recipe.name, title=recipe.title, recipe_run_id=run_id,
            created_at=created, seed=0,
            steps=[recipes_service.StepState(index=0, kind="overlay", label="s", manual=False)],
        ))
    listed = recipes_service.list_recipe_runs()
    ids = [r["recipe_run_id"] for r in listed]
    assert ids[:3] == ["recipe_future_run", state.recipe_run_id, "recipe_old_iso_run"]
    old = next(r for r in listed if r["recipe_run_id"] == "recipe_old_iso_run")
    assert old["created_at"] == "2020-01-01 00:00:00"  # T → 空格归一化


def test_delete_recipe_run(env):
    """删除：run 目录整个清掉；再读/再删 404；非法 id 形态启动即拒。"""
    state = recipes_service.start_run(env, "demo_blend")
    rid = state.recipe_run_id
    assert recipes_service.manifest_path(rid).exists()
    recipes_service.delete_run(rid)
    assert not recipes_service.manifest_path(rid).exists()
    assert not any(r["recipe_run_id"] == rid for r in recipes_service.list_recipe_runs())
    with pytest.raises(PresetError):
        recipes_service.read_state(rid)          # 404
    with pytest.raises(PresetError):
        recipes_service.delete_run(rid)          # 再删 = 不存在
    with pytest.raises(PresetError):
        recipes_service.delete_run("../escape")  # 非法 id 形态


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

    # 删除：记录清掉（产物目录是另一码事，不在此端点管）；不存在 404
    assert client.delete(f"/recipe-runs/{rid}").json()["ok"] is True
    assert client.get(f"/recipe-runs/{rid}").status_code == 404
    assert client.delete(f"/recipe-runs/{rid}").status_code == 404



# ---- ⑤：生成配方（adhoc_image / video_model）----

_MODEL_YAML = """
name: demo_model_clip
type: recipe
version: 1
title: 生成配方演示
seed: 3
subject: demo_witch
refs:
- label: front
  from: subject
  slot: front
steps:
- kind: images
  label: 主图
  presets: [demo_pose]
  run_id: demo_bound_run
- kind: adhoc_image
  label: 单图
  prompt: a single frame of the subject
  backend: comfyui
  reference_labels: [front]
- kind: video_model
  label: 出片
  prompt: the subject dances slowly
  frame_from: 单图
  frame_index: 0
  reference_labels: [front]
  wait_s: 5.0
"""


def _write_model_fixtures(tmp_path: Path) -> None:
    from charvar import subject_images

    _write_fixtures(tmp_path)
    base = recipes_service.recipes_dir()
    (base / "demo_model_clip.yaml").write_text(_MODEL_YAML, encoding="utf-8")
    # 仓库的 ⑤ 示例配方抄进 tmp：顺带校验真实文件在干净环境下可解析
    repo_demo = Path(__file__).parent.parent / "presets" / "recipes" / "model_clip_demo.yaml"
    (base / "model_clip_demo.yaml").write_text(repo_demo.read_text(encoding="utf-8"), encoding="utf-8")
    # 主体 front 槽位图（refs source=subject 的消费前提）
    slot = subject_images.subject_dir("demo_witch")
    slot.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 96), (200, 180, 160)).save(slot / "front.png")


@pytest.fixture()
def model_env(tmp_path, monkeypatch):
    monkeypatch.setenv("CHARVAR_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CHARVAR_PRESETS_DIR", str(tmp_path / "presets"))
    _write_model_fixtures(tmp_path)
    _bound_run(tmp_path)
    with storage.connect(tmp_path / "test.db") as conn:
        yield conn


def _stub_adhoc_generate(monkeypatch):
    """stub charvar.adhoc.generate：返回已落盘的假单图（供 reuse/frame 消费）。"""
    from charvar import adhoc as adhoc_service

    run_dir = recipes_service.paths.data_dir() / "generated" / "adhoc" / "adhoc_fake"
    (run_dir / "original").mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 96), (10, 20, 30)).save(run_dir / "original" / "000000.png")
    (run_dir / "manifest.json").write_text("{}", encoding="utf-8")

    reqs = []

    def fake_generate(req, db_path=None):
        reqs.append(req)
        return adhoc_service.AdhocResult(
            image_path=str(run_dir / "original" / "000000.png"),
            provider="comfyui",
            manifest_path=str(run_dir / "manifest.json"),
            width=64,
            height=96,
            seed=req.seed,
        )

    monkeypatch.setattr(adhoc_service, "generate", fake_generate)
    return reqs


def _stub_adhoc_video(monkeypatch, refresh_seq, *, run_id="video_fake0001"):
    """stub adhoc_video.generate/refresh：提交恒成 run_id，refresh 按序列回放。"""
    from charvar import adhoc_video

    submit_reqs = []

    def fake_generate(req, db_path=None, *, poll_interval=5.0, timeout_s=900.0):
        submit_reqs.append(req)
        return adhoc_video.VideoResult(run_id=run_id, provider="openrouter",
                                       status="pending", seed=req.seed, manifest_path="")

    def fake_refresh(run_id_, db_path=None):
        return refresh_seq.pop(0) if refresh_seq else {"status": "ready", "job_id": "j1"}

    monkeypatch.setattr(adhoc_video, "generate", fake_generate)
    monkeypatch.setattr(adhoc_video, "refresh", fake_refresh)
    return submit_reqs


def test_parse_model_recipe_category_and_defaults(model_env):
    # 仓库的 ⑤ 示例配方不在 tmp 预设里 —— 真实文件的解析另测（test_parse_repo_recipe 补充）
    recipe = recipes_service.load_recipe("demo_model_clip")
    assert recipes_service.category_of(recipe) == "model"
    assert [recipes_service.manual_default(s) for s in recipe.steps] == [False, True, True]
    demo = recipes_service.load_recipe("model_clip_demo")
    assert recipes_service.category_of(demo) == "model"


def test_model_recipe_static_validation_errors(model_env):
    base = recipes_service.recipes_dir()
    cases = {
        "unknown_ref": _MODEL_YAML.replace("reference_labels: [front]", "reference_labels: [不存在]"),
        "frame_from_video_step": _MODEL_YAML.replace("frame_from: 单图", "frame_from: 出片"),
        "frame_forward": _MODEL_YAML.replace("frame_from: 单图", "frame_from: 出片2"),
        "video_ref_subject": _MODEL_YAML.replace(
            "  slot: front\nsteps:", "  slot: front\nvideo_refs:\n- label: mv\n  from: subject\n  slot: front\nsteps:"
        ),
    }
    for stem, text in cases.items():
        (base / f"badm_{stem}.yaml").write_text(text, encoding="utf-8")
        with pytest.raises(PresetError):
            recipes_service.load_recipe(f"badm_{stem}")
    with pytest.raises(PresetError, match="frame_from"):
        recipes_service.load_recipe("badm_frame_forward")


def test_model_recipe_full_flow(model_env, monkeypatch):
    from charvar import adhoc_video

    monkeypatch.setattr(recipes_service, "_VIDEO_POLL_INTERVAL_S", 0.01)
    adhoc_reqs = _stub_adhoc_generate(monkeypatch)
    refresh_seq = [
        {"status": "in_progress", "job_id": "j1"},
        {"status": "ready", "job_id": "j1", "model": "heygen/heygen-video-1"},
    ]
    _stubbed_reqs = _stub_adhoc_video(monkeypatch, refresh_seq)

    state = recipes_service.start_run(model_env, "demo_model_clip")
    # images（绑定，auto）done；adhoc_image manual → waiting
    assert state.status == "waiting"
    assert state.steps[0].status == "done"
    assert state.steps[1].status == "pending"

    # continue 1：单图步执行（stub 落图）：outputs 有 run 与对外 URL
    state = recipes_service.advance_state(model_env, state.recipe_run_id)
    assert state.steps[1].status == "done"
    assert state.steps[1].outputs["image_run_id"] == "adhoc_fake"
    assert state.steps[1].outputs["image_url"] == "/adhoc/adhoc_fake/image"
    assert state.status == "waiting"  # video_model manual → 再停

    # continue 2：提交 + 轮询（第一次 in_progress → 循环继续 → ready → done）
    state = recipes_service.advance_state(model_env, state.recipe_run_id)
    assert state.status == "done"
    out = state.steps[2].outputs
    assert out["video_run_id"] == "video_fake0001"
    assert out["video_url"] == "/videos/video_fake0001/video"
    assert out["poster_url"] == "/videos/video_fake0001/poster"
    # 单图请求带了 refs 解析出的参考图（front 槽位）；视频请求带首帧 + 参考图
    assert len(adhoc_reqs) == 1
    assert all(p.exists() for p in adhoc_reqs[0].reference_paths)
    assert len(_stubbed_reqs) == 1
    video_req = _stubbed_reqs[0]
    assert video_req.first_frame is not None and video_req.first_frame.exists()
    assert str(video_req.first_frame).endswith("adhoc_fake/original/000000.png")
    assert video_req.aspect_ratio == ""  # 该步没写 aspect → 不发送
    assert len(video_req.reference_paths) == 1  # front（预处理缓存文件）


def test_video_model_gate_then_ready(model_env, monkeypatch):
    """作业提交后仍在生成 → gate waiting（不重复提交）；再 continue → ready → done。"""
    from charvar import adhoc_video

    _stub_adhoc_generate(monkeypatch)
    monkeypatch.setattr(recipes_service, "_VIDEO_POLL_INTERVAL_S", 0.01)
    # gate 用小预算：把示例配方的 wait_s 压到 0.1s，refresh 恒 in_progress
    base = recipes_service.recipes_dir()
    (base / "demo_model_clip.yaml").write_text(_MODEL_YAML.replace("wait_s: 5.0", "wait_s: 0.1"), encoding="utf-8")
    submitted = _stub_adhoc_video(
        monkeypatch,
        [{"status": "in_progress", "job_id": "j1"},
         {"status": "ready", "job_id": "j1", "model": "heygen/heygen-video-1"}],
        run_id="video_fake0002",
    )
    # 首次运行（gate 阶段）：refresh 恒 in_progress
    monkeypatch.setattr(adhoc_video, "refresh",
                        lambda run_id, db_path=None: {"status": "in_progress", "job_id": "j1"})

    state = recipes_service.start_run(model_env, "demo_model_clip")
    state = recipes_service.advance_state(model_env, state.recipe_run_id)  # 单图步
    # 出片步：0.1s 预算内没就绪 → gate → waiting
    state = recipes_service.advance_state(model_env, state.recipe_run_id)
    assert state.status == "waiting"
    assert state.steps[2].status == "pending"
    assert "仍在生成" in state.steps[2].note
    assert state.steps[2].outputs["video_run_id"] == "video_fake0002"
    assert len(submitted) == 1  # gate 期间没有重复提交


def test_video_model_failed_job_resubmits(model_env, monkeypatch):
    """已提交的作业失败 → 步 failed；continue 清掉死作业、重新提交并轮到 ready。"""
    from charvar import adhoc_video

    _stub_adhoc_generate(monkeypatch)
    monkeypatch.setattr(recipes_service, "_VIDEO_POLL_INTERVAL_S", 0.01)
    base = recipes_service.recipes_dir()
    (base / "demo_model_clip.yaml").write_text(_MODEL_YAML.replace("wait_s: 5.0", "wait_s: 0.1"), encoding="utf-8")
    # gate 阶段 refresh 恒 in_progress → waiting（outputs 记下 run_id）
    submitted = _stub_adhoc_video(
        monkeypatch,
        [{"status": "in_progress", "job_id": "j1"}],
        run_id="video_fake0003",
    )
    monkeypatch.setattr(adhoc_video, "refresh",
                        lambda run_id, db_path=None: {"status": "in_progress", "job_id": "j1"})
    state = recipes_service.start_run(model_env, "demo_model_clip")
    state = recipes_service.advance_state(model_env, state.recipe_run_id)  # 单图
    state = recipes_service.advance_state(model_env, state.recipe_run_id)  # gate
    assert state.status == "waiting"
    assert state.steps[2].outputs["video_run_id"] == "video_fake0003"

    # 模拟作业真失败：refresh 先回 failed（清死作业、重新提交），再回 ready（轮到终态）
    real_refresh_seq = [
        {"status": "failed", "error": "provider blew up", "job_id": "j1"},
        {"status": "ready", "job_id": "j1", "model": "m1"},
    ]
    monkeypatch.setattr(adhoc_video, "refresh",
                        lambda run_id, db_path=None: real_refresh_seq.pop(0))
    state = recipes_service.advance_state(model_env, state.recipe_run_id)
    assert state.steps[2].status == "done"  # 失败 → 重提交 → 这轮 ready
    assert len(submitted) == 2  # 重新提交过一次（stub 每次提交通成 run_id）
    assert state.steps[2].outputs["video_run_id"] == "video_fake0003"
    assert state.steps[2].outputs["video_url"] == "/videos/video_fake0003/video"


def test_video_model_refs_and_assets(model_env, monkeypatch):
    """video_ref_labels/video_from：asset 素材 + 前置合成步成片进 input_references。"""
    from charvar import adhoc_video

    _stub_adhoc_generate(monkeypatch)
    monkeypatch.setattr(recipes_service, "_VIDEO_POLL_INTERVAL_S", 0.01)
    base = recipes_service.recipes_dir()
    (base / "assets").mkdir(exist_ok=True)
    (base / "assets" / "motion.mp4").write_bytes(
        b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00isomiso2\x00" + b"\x00" * 16
    )
    # 本地合成预设（与 _DEMO_VIDEO 同款）
    (base.parent / "video" / "demo_video.yaml").write_text(_DEMO_VIDEO, encoding="utf-8")
    (base / "demo_model_refs.yaml").write_text(
        """
name: demo_model_refs
type: recipe
version: 1
title: refs 演示
subject: demo_witch
seed: 9
refs:
- label: front
  from: subject
  slot: front
video_refs:
- label: motion
  from: asset
  path: motion.mp4
steps:
- kind: images
  label: 底图
  presets: [demo_pose]
  run_id: demo_bound_run
- kind: video_synth
  label: 底片
  video_preset: demo_video
  input: 底图
  manual: false
- kind: video_model
  label: 成片
  prompt: moves like the reference
  reference_labels: [front]
  video_ref_labels: [motion]
  video_from: 底片
""",
        encoding="utf-8",
    )
    refresh_seq = [{"status": "ready", "job_id": "j1", "model": "m1"}]
    submitted = _stub_adhoc_video(monkeypatch, refresh_seq, run_id="video_fake0004")

    state = recipes_service.start_run(model_env, "demo_model_refs")
    assert state.status == "waiting"  # adhoc_image? no —— 底片 manual: false，成片 manual → waiting
    assert state.steps[0].status == "done"
    assert state.steps[1].status == "done"  # 本地合成（真的跑 ffmpeg）
    assert state.steps[2].status == "pending"
    state = recipes_service.advance_state(model_env, state.recipe_run_id)
    assert state.steps[2].status == "done"
    req = submitted[0]
    assert [str(p) for p in req.reference_video_paths] == [
        str(recipes_service.recipes_dir() / "assets" / "motion.mp4"),
        str(run_assets_dir("demo_bound_run") / "videos" / state.steps[1].outputs["video"]["name"]),
    ]
    assert req.first_frame is None  # 没写 frame_*：纯参考生成


def test_adhoc_image_uses_static_ref_and_reuses_output(model_env, monkeypatch):
    """单图步：refs 静态素材/主体槽位解析 + 产物复用（不重复生成）。"""
    from charvar import adhoc as adhoc_service

    base = recipes_service.recipes_dir()
    (base / "assets").mkdir(exist_ok=True)
    Image.new("RGB", (32, 32), (5, 5, 5)).save(base / "assets" / "frame.png")
    (base / "reuse_demo.yaml").write_text(
        """
name: reuse_demo
type: recipe
version: 1
title: x
subject: demo_witch
seed: 4
refs:
- label: front
  from: subject
  slot: front
- label: static_frame
  from: asset
  path: frame.png
steps:
- kind: adhoc_image
  label: 单图
  prompt: a frame
  backend: comfyui
  reference_labels: [front, static_frame]
  aspect: "9:16"
""",
        encoding="utf-8",
    )
    adhoc_reqs = _stub_adhoc_generate(monkeypatch)
    state = recipes_service.start_run(model_env, "reuse_demo")
    assert state.status == "waiting"
    assert state.steps[0].status == "pending"
    state = recipes_service.advance_state(model_env, state.recipe_run_id)
    assert state.steps[0].status == "done"
    assert state.status == "done"
    req = adhoc_reqs[0]
    assert len(req.reference_paths) == 2
    assert req.aspect_ratio == "9:16"
    # 复用：再跑一次 advance（同 outputs）→ stub 不再被打
    before = len(adhoc_reqs)
    state = recipes_service.advance_state(model_env, state.recipe_run_id)
    assert state.steps[0].status == "done"
    assert len(adhoc_reqs) == before


# ---------- ⑥ 随机三库配方（pick / scene_design / combo_image / video_script） ----------

_CAST_YAML = """
name: demo_cast
type: recipe
version: 1
title: 随机三库（测试）
seed: 7
steps:
- kind: pick
  label: 选库
  manual: false
  face: {sources: [face-db-01], section: face}
  pose: {sources: [pose-db-01, pose-db-02], angle: 正面}
  costume: {sources: [costume-db-01]}
- kind: scene_design
  label: 场景
  manual: false
  input: 选库
- kind: combo_image
  label: 首帧图
  input: 选库
  scene_from: 场景
  backend: comfyui
  aspect: "9:16"
- kind: video_script
  label: 脚本
  manual: false
  input: 选库
  scene_from: 场景
  duration: 5
- kind: video_model
  label: 成片
  prompt_from: 脚本
  frame_from: 首帧图
  duration: 5
  aspect: "9:16"
"""

_POSE_DESC = "她正面朝向镜头站立，双臂自然下垂，双腿并拢收立，重心居中。整体是安静的待机站姿。"

_SCENE_TEXT = "黄昏的海边栈道，夕阳侧逆光给人物勾出暖色轮廓光，海面泛着细碎的金光。"


def _script_stub_text() -> str:
    return (
        "以输入首帧为唯一视觉依据，生成一段约5秒的单人视频。"
        + "画面连续性：全程同一名人物，发型服装背景光线与首帧保持一致，不切镜头不换场景。" * 14
    )


def _write_cast_fixtures(tmp_path, *, face_ids=None):
    (tmp_path / "presets" / "recipes").mkdir(parents=True, exist_ok=True)
    (tmp_path / "presets" / "recipes" / "demo_cast.yaml").write_text(
        _CAST_YAML, encoding="utf-8"
    )
    from tests.test_costumes import _build_fake_costume_data
    from tests.test_faces import _build_fake_face_data
    from tests.test_poses import _build_fake_pose_data

    _build_fake_face_data(tmp_path)  # face 池 3 条（2 face + 1 styling）
    if face_ids:
        # reroll 测试用大池：再造 30 条 face 形态条目（ids 不与缺省冲突）
        plan = {fid: "face" for fid in face_ids}
        _build_fake_face_data(tmp_path, counts=plan, source="face-db-01")
    _build_fake_costume_data(tmp_path)
    # 动作池：线稿 + 深度各一条单人动作（P001），pair 不入库
    _build_fake_pose_data(tmp_path, counts={"full_sexy_P001": 5})
    _build_fake_pose_data(tmp_path, source="pose-db-02", counts={"full_sexy_P001": 5})


def _import_cast_libs(conn, *, seed_line_desc: bool = True):
    from charvar import costumes as costumes_service
    from charvar import faces as faces_service
    from charvar import poses as poses_service

    faces_service.import_catalog(conn)
    costumes_service.import_catalog(conn, "costume-db-01")
    poses_service.import_catalog(conn)  # pose-db-01
    poses_service.import_catalog(conn, "pose-db-02")
    if seed_line_desc:
        poses_service.set_pose_action(
            conn, "full_sexy_P001", "正面", _POSE_DESC,
            source="manual", source_id="pose-db-01",
        )


@pytest.fixture()
def cast_env(tmp_path, monkeypatch):
    monkeypatch.setenv("CHARVAR_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CHARVAR_PRESETS_DIR", str(tmp_path / "presets"))
    _write_cast_fixtures(tmp_path)
    # 库文件放默认名（charvar.db）：describe 起稿的缩略图查找走默认库路径，必须同库
    with storage.connect(tmp_path / "charvar.db") as conn:
        _import_cast_libs(conn, seed_line_desc=True)
        yield conn


def _stub_llms(monkeypatch, *, scene_text=_SCENE_TEXT, script_text=None):
    """stub 三个 LLM 环节：场景（chat_json 形态）/ 脚本 /（动作描述起稿由各测试自定）。"""
    from charvar import scene_design as scene_service
    from charvar import video_script as script_service

    monkeypatch.setattr(
        scene_service, "_call_llm",
        lambda messages, model=None, config=None: {"scene": scene_text},
    )
    monkeypatch.setattr(
        script_service, "call_llm",
        lambda messages, model=None, config=None: script_text or _script_stub_text(),
    )


def test_repo_random_cast_recipe_parses():
    """仓库自带随机三库配方解析合法；检查点布局 = 选库/场景自动 + 首帧/成片停。"""
    recipe = recipes_service.load_recipe("random_cast_video")
    assert [s.kind for s in recipe.steps] == [
        "pick", "scene_design", "combo_image", "video_script", "video_model",
    ]
    assert [recipes_service.manual_default(s) for s in recipe.steps] == [
        False, False, True, False, True,
    ]
    script, video = recipe.steps[3], recipe.steps[4]
    assert script.duration == 8 and script.frame_from == "首帧图"
    assert video.duration == 8 and video.resolution == "768p"
    assert recipe.steps[0].face["sources"] == ["face-db-01", "face-db-02-cn", "face-db-03-cn"]


def test_cast_static_validation_errors(model_env):
    base = recipes_service.recipes_dir()
    cases = {
        "unknown_key": _CAST_YAML.replace("section: face}", "sourse: [x]}"),
        "unregistered_source": _CAST_YAML.replace("[pose-db-01, pose-db-02]", "[pose-db-99]"),
        "no_target": _CAST_YAML.replace(
            "  face: {sources: [face-db-01], section: face}\n  pose: {sources: [pose-db-01, pose-db-02], angle: 正面}\n  costume: {sources: [costume-db-01]}\n", ""
        ),
        "scene_missing_input": _CAST_YAML.replace("  input: 选库\n- kind: combo_image\n", "- kind: combo_image\n"),
        "prompt_from_not_script": _CAST_YAML.replace("prompt_from: 脚本\n  frame_from: 首帧图", "prompt_from: 场景\n  frame_from: 首帧图\n  prompt: x"),
    }
    for stem, text in cases.items():
        (base / f"badc_{stem}.yaml").write_text(text, encoding="utf-8")
        with pytest.raises(PresetError):
            recipes_service.load_recipe(f"badc_{stem}")
    with pytest.raises(PresetError, match="face/pose/costume"):
        recipes_service.load_recipe("badc_no_target")
    with pytest.raises(PresetError, match="prompt"):
        recipes_service.load_recipe("badc_prompt_from_not_script")


def test_cast_full_flow_through_two_checkpoints(cast_env, monkeypatch):
    import re

    from charvar import pose_describe as describe_service

    describe_calls: list = []

    def fake_describe_llm(messages, model=None, config=None):
        describe_calls.append(messages)
        return _POSE_DESC

    monkeypatch.setattr(describe_service, "_call_llm", fake_describe_llm)
    _stub_llms(monkeypatch)
    monkeypatch.setattr(recipes_service, "_VIDEO_POLL_INTERVAL_S", 0.01)
    adhoc_reqs = _stub_adhoc_generate(monkeypatch)
    _stubbed = _stub_adhoc_video(
        monkeypatch,
        [{"status": "ready", "job_id": "j1", "model": "heygen/heygen-video-1"}],
    )

    # run 1：pick（自动）+ 场景（自动）→ 停在「首帧图」
    state = recipes_service.start_run(cast_env, "demo_cast")
    assert state.status == "waiting"  # combo_image 缺省 manual
    pick_out = state.steps[0].outputs
    assert set(pick_out) == {"face", "pose", "costume"}
    assert pick_out["face"]["section"] == "face"  # styling 条目被过滤器排除
    assert pick_out["face"]["thumb_url"].startswith("/faces/image/face-db-01/")
    assert pick_out["costume"]["ref"] in ("costume-db-01/SPR-0001", "costume-db-01/SXY-0099")
    pose_out = pick_out["pose"]
    assert pose_out["action_text"] == _POSE_DESC
    assert re.match(r"^(existing|twin_copy\(pose-db-0[12]→pose-db-0[12]\))$", pose_out["action_origin"])
    assert pose_out["line"] == "全身1人・セクシー系"
    assert state.steps[1].outputs["scene_text"] == _SCENE_TEXT
    assert not describe_calls  # 池内该动作在双库之一有描述 → A 复制/已有，没烧 LLM

    # continue 1：组合出图（stub）+ 视频脚本（stub）→ 停在「成片」
    state = recipes_service.advance_state(cast_env, state.recipe_run_id)
    assert state.status == "waiting"
    combo_out = state.steps[2].outputs
    assert combo_out["image_run_id"] == "adhoc_fake"
    assert combo_out["image_url"] == "/adhoc/adhoc_fake/image"
    req = adhoc_reqs[0]
    assert len(req.reference_paths) == 3  # 人脸 → 动作 → 服饰
    assert all(p.exists() for p in req.reference_paths)
    # 场景段替换了固定背景句；动作描述与服饰要点在 prompt 里
    assert _SCENE_TEXT in req.prompt
    assert "背景简洁，突出角色" not in req.prompt
    assert _POSE_DESC in req.prompt
    assert "服装要点" in req.prompt
    # 脚本步：脚本 = stub 文本，类型出自池子
    script_out = state.steps[3].outputs
    from charvar import video_script as script_service

    assert script_out["script"].startswith("以输入首帧为唯一视觉依据")
    assert script_out["clip_type"] in script_service.DEFAULT_TYPES

    # continue 2：视频提交（脚本当 prompt、首帧当 frame）→ ready → done
    state = recipes_service.advance_state(cast_env, state.recipe_run_id)
    assert state.status == "done"
    video_req = _stubbed[0]
    assert video_req.prompt == _script_stub_text()
    assert video_req.first_frame is not None
    assert str(video_req.first_frame).endswith("adhoc_fake/original/000000.png")


def test_cast_pick_b_llm_then_failed_retry(cast_env, monkeypatch):
    """两库都缺描述 → B LLM 起稿落库；LLM 没配 → 步 failed，修好后 continue 重试（同批不重掷）。"""
    from charvar import pose_describe as describe_service

    conn = cast_env
    # 清掉线稿库预置描述：两库都空 → 只能走 B
    conn.execute("DELETE FROM pose_actions")
    conn.commit()

    monkeypatch.setattr(
        describe_service, "_call_llm",
        lambda *a, **kw: (_ for _ in ()).throw(__import__("charvar").llm.LLMError("no api key set")),
    )
    _stub_llms(monkeypatch)
    monkeypatch.setattr(recipes_service, "_VIDEO_POLL_INTERVAL_S", 0.01)
    _stub_adhoc_generate(monkeypatch)

    state = recipes_service.start_run(cast_env, "demo_cast")
    assert state.status == "failed"
    assert state.steps[0].status == "failed"
    assert "LLM" in state.steps[0].note

    # 修好（换可用 stub 的方式模拟配置修复）→ continue：failed 步直接重试；重试同批
    monkeypatch.setattr(
        describe_service, "_call_llm", lambda messages, model=None, config=None: _POSE_DESC
    )
    state = recipes_service.advance_state(cast_env, state.recipe_run_id)
    assert state.steps[0].status == "done"
    assert state.steps[0].outputs["pose"]["action_origin"] == "ai_draft"
    row = conn.execute(
        "SELECT source FROM pose_actions WHERE pose_id='full_sexy_P001' AND angle='正面'"
    ).fetchone()
    assert row["source"] == "ai_draft"  # 起稿落库（复用给后续运行与网页）
    assert state.status == "waiting"  # 接着推进到「首帧图」检查点

    # 同一运行再重掷也不会换批：outputs 已在，重跑 handler 复用（pick 幂等）
    again = recipes_service._run_pick_step(conn, recipes_service.load_recipe("demo_cast"),
                                           state, recipes_service.load_recipe("demo_cast").steps[0], state.steps[0])
    assert again["face"] == state.steps[0].outputs["face"]


def test_cast_pick_rerolls_across_runs(cast_env, monkeypatch):
    """掷骰 key 含 run_id：同一 seed 的各次运行各掷各的（6 连抽要出现 ≥2 种选取）。

    概率注：池 33 条，全同概率 ≈ (1/33)^5 —— 依赖 rng key 含 run_id 的语义本身。
    """
    from charvar import faces as faces_service
    from charvar import pose_describe
    from tests.test_faces import _build_fake_face_data

    plan = {f"rollface{i:05d}": "face" for i in range(30)}
    _build_fake_face_data(Path(os.environ["CHARVAR_DATA_DIR"]), counts=plan)
    faces_service.import_catalog(cast_env)
    monkeypatch.setattr(pose_describe, "_call_llm",
                        lambda messages, model=None, config=None: _POSE_DESC)
    _stub_llms(monkeypatch)
    monkeypatch.setattr(recipes_service, "_VIDEO_POLL_INTERVAL_S", 0.01)
    _stub_adhoc_generate(monkeypatch)

    picks = []
    for _ in range(6):
        state = recipes_service.start_run(cast_env, "demo_cast")
        picks.append(state.steps[0].outputs["face"]["face_id"])
    assert len(set(picks)) >= 2


# ---------- ⑦ 随机舞蹈配方（random_dance_video：去包帽 / 看图脚本 / 大幅档） ----------

_DANCE_YAML = """
name: demo_dance
type: recipe
version: 1
title: 随机舞蹈（测试）
seed: 3
steps:
- kind: pick
  label: 选库
  manual: false
  face: {sources: [face-db-01, face-db-02-cn, face-db-03-cn], section: face}
  pose: {sources: [pose-db-02], lines: [全身1人・セクシー系, 全身1人・標準系], categories: [立ち], angle: 正面}
  costume: {sources: [costume-db-01], categories: [春, 夏, 秋, 冬, パーティ, 水着, セクシー]}
- kind: scene_design
  label: 场景
  manual: false
  input: 选库
  hint: 舞蹈场景方向。
- kind: combo_image
  label: 首帧图
  input: 选库
  scene_from: 场景
  strip_accessories: true
  framing_extra: 全身入镜，四周留余白。
  backend: comfyui
  aspect: "9:16"
- kind: video_script
  label: 脚本
  manual: false
  input: 选库
  scene_from: 场景
  frame_from: 首帧图
  amplitude: large
  duration: 8
  types: [街舞律动, 爵士舞]
- kind: video_model
  label: 成片
  prompt_from: 脚本
  frame_from: 首帧图
  duration: 8
  aspect: "9:16"
"""


def test_repo_dance_recipe_parses():
    """仓库随机舞蹈配方解析合法；舞蹈开关与检查点布局锁进测试。"""
    recipe = recipes_service.load_recipe("random_dance_video")
    assert [s.kind for s in recipe.steps] == [
        "pick", "scene_design", "combo_image", "video_script", "video_model",
    ]
    assert [recipes_service.manual_default(s) for s in recipe.steps] == [
        False, False, True, False, True,
    ]
    combo, script, video = recipe.steps[2], recipe.steps[3], recipe.steps[4]
    assert combo.strip_accessories is True and "余白" in (combo.framing_extra or "")
    assert script.amplitude == "large" and script.frame_from == "首帧图"
    assert script.duration == 8 and len(script.types) == 6
    assert video.duration == 8 and video.resolution == "768p"


@pytest.fixture()
def dance_env(cast_env):
    (Path(os.environ["CHARVAR_DATA_DIR"]) / "presets" / "recipes" / "demo_dance.yaml").write_text(
        _DANCE_YAML, encoding="utf-8"
    )
    return cast_env


def _dance_script_text() -> str:
    return (
        "以输入首帧为唯一视觉依据，生成一段约8秒的单人街舞律动视频。"
        + "画面连续性：全程同一名人物，发型服装背景光线与首帧保持一致，不切镜头不换场景。首帧" * 14
    )


_MV_YAML_BASE = _DANCE_YAML.replace("name: demo_dance", "name: demo_mv").replace(
    "  amplitude: large\n  duration: 8\n",
    "  amplitude: large\n  multishot: true\n  ensemble: true\n  shots: 4\n  duration: 15\n",
).replace("  duration: 8\n  aspect", "  duration: 15\n  aspect").replace(
    "  duration: 15\n  aspect: \"9:16\"", "  duration: 15\n  resolution: 480p\n  aspect: \"16:9\""
)


_MV_SCRIPT_TEXT = (
    "以输入首帧为唯一视觉依据，生成一段约15秒、4个镜头的群舞齐舞卡点MV视频。"
    "镜头一 0 秒起，镜头二 4 秒起，镜头三 8 秒起，镜头四 11 秒起。"
    + "画面连续性：全程同一首席，发型服装背景光线与首帧保持一致。首帧" * 14
)


def test_dance_full_flow(dance_env, monkeypatch):
    """舞蹈全链：站姿池选取 → 去包帽首帧 → 看图脚本（多模态消息）→ 成片。"""
    conn = dance_env
    from charvar import video_script as script_service

    script_calls = []

    def fake_script_llm(messages, model=None, config=None):
        script_calls.append(messages)
        return _dance_script_text()

    _stub_llms(monkeypatch)
    monkeypatch.setattr(script_service, "call_llm", fake_script_llm)
    monkeypatch.setattr(recipes_service, "_VIDEO_POLL_INTERVAL_S", 0.01)
    adhoc_reqs = _stub_adhoc_generate(monkeypatch)
    _stub_adhoc_video(
        monkeypatch,
        [{"status": "ready", "job_id": "j1", "model": "heygen/heygen-video-1"}],
    )

    # run 1：pick + 场景（自动）→ 停在「首帧图」
    state = recipes_service.start_run(conn, "demo_dance")
    assert state.status == "waiting"
    pose_out = state.steps[0].outputs["pose"]
    assert pose_out["source"] == "pose-db-02"
    assert pose_out["category"] == "立ち"  # 站姿类过滤器生效（坐/卧类不进池）
    assert pose_out["action_text"] == _POSE_DESC  # A 路：线稿库同动作描述复制

    # continue 1：去包帽首帧 + 看图脚本 → 停在「成片」
    state = recipes_service.advance_state(conn, state.recipe_run_id)
    assert state.status == "waiting"
    combo_req = adhoc_reqs[0]
    assert len(combo_req.reference_paths) == 3
    assert "一律不带入画面" in combo_req.prompt          # 剥离锚句入文
    assert "同样要装到角色身上" not in combo_req.prompt  # 配件穿戴措辞不残留
    assert "全身入镜，四周留余白。" in combo_req.prompt  # framing_extra 追加在文
    assert _SCENE_TEXT in combo_req.prompt               # 场景段不受影响
    content = script_calls[0][0]["content"]
    assert isinstance(content, list)                     # 看图模式：多模态消息
    assert content[0]["type"] == "text" and "已附的这张图片" in content[0]["text"]
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    script_out = state.steps[3].outputs
    assert script_out["clip_type"] in ("街舞律动", "爵士舞")
    assert script_out["script"] == _dance_script_text()

    # continue 2：成片（脚本当 prompt）
    state = recipes_service.advance_state(conn, state.recipe_run_id)
    assert state.status == "done"


def test_dance_script_gates_on_pending_frame(dance_env, monkeypatch):
    """看图脚本的帧步未完成 → _GateStop 等待（不写脚本，与成片帧同语义）。"""
    conn = dance_env
    _stub_llms(monkeypatch)
    _stub_adhoc_generate(monkeypatch)
    state = recipes_service.start_run(conn, "demo_dance")  # 停在「首帧图」（combo 未跑）
    assert state.status == "waiting"
    recipe = recipes_service.load_recipe("demo_dance")
    with pytest.raises(recipes_service._GateStop, match="首帧来源步骤"):
        recipes_service._run_video_script_step(conn, recipe, state, recipe.steps[3], state.steps[3])


def test_dance_frame_from_static_validation(tmp_path, monkeypatch):
    """video_script 的 frame_from 指错位置 / amplitude 非法 → 解析期报错。"""
    monkeypatch.setenv("CHARVAR_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CHARVAR_PRESETS_DIR", str(tmp_path / "presets"))
    base = tmp_path / "presets" / "recipes"
    base.mkdir(parents=True, exist_ok=True)

    def bad(stem: str, text: str) -> None:
        (base / stem).write_text(text, encoding="utf-8")
        with pytest.raises(PresetError, match="frame_from|amplitude"):
            recipes_service.load_recipe(stem[:-5])

    bad(
        "badd_kind.yaml",
        _DANCE_YAML.replace("frame_from: 首帧图", "frame_from: 选库"),
    )
    bad(
        "badd_late.yaml",
        _DANCE_YAML.replace("frame_from: 首帧图", "frame_from: 成片"),
    )
    bad(
        "badd_amp.yaml",
        _DANCE_YAML.replace("amplitude: large", "amplitude: huge"),
    )


def test_video_script_amplitude_brief_and_image_mode(tmp_path, monkeypatch):
    """大幅档 brief（规则 6/7 分叉）与看图模式消息构造；normal 路径保持原行为。"""
    from charvar import video_script as script_service

    seen: dict = {}

    def fake(messages, model=None, config=None):
        seen["latest"] = messages
        return _dance_script_text()

    monkeypatch.setattr(script_service, "call_llm", fake)

    script_service.write_script(
        pose_text="自然站立。", pose_title="叉腰", clip_type="街舞律动", duration=8
    )
    normal = seen["latest"][0]["content"]
    assert isinstance(normal, str)
    assert "视频的首帧画面已经存在（另一张已生成的图）" in normal
    assert "动作幅度适中" in normal and "编排结构" not in normal

    script_service.write_script(
        pose_text="自然站立。", pose_title="叉腰", clip_type="街舞律动", duration=8, amplitude="large"
    )
    text = seen["latest"][0]["content"]
    assert "动作幅度大" in text and "编排结构" in text and "动作幅度适中" not in text
    assert "跳跃、转体、甩头、跨步与大幅挥臂" in text

    frame = tmp_path / "frame.png"
    Image.new("RGB", (2000, 1000), (10, 20, 30)).save(frame)
    script_service.write_script(
        pose_text="自然站立。", pose_title="叉腰", clip_type="街舞律动", duration=8,
        amplitude="large", image_path=frame,
    )
    content = seen["latest"][0]["content"]
    assert content[0]["type"] == "text" and "已附的这张图片" in content[0]["text"]
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert "首帧的实际姿态" in content[0]["text"]


def test_video_script_multishot_brief(tmp_path, monkeypatch):
    """多镜 brief：镜头数/分镜/声场/配乐/伴舞条款；shots 推导；缺分镜词拒。"""
    from charvar import video_script as script_service

    seen: dict = {}

    def fake(messages, model=None, config=None):
        seen["latest"] = messages
        return _MV_SCRIPT_TEXT

    monkeypatch.setattr(script_service, "call_llm", fake)

    script_service.write_script(
        pose_text="自然站立。", pose_title="叉腰", clip_type="群舞齐舞卡点",
        duration=15, multishot=True, ensemble=True, shots=4,
    )
    text = seen["latest"][0]["content"]
    assert isinstance(text, str)
    assert "15 秒、4 个镜头" in text and "多镜头 MV 分镜" in text
    assert "第几秒起" in text and "低位仰拍" in text
    assert "伴舞安排" in text and "3–5 名" in text
    assert "【实录声场】" in text and "【配乐】" in text
    assert "【首席·动作描述】" in text and "【本支 MV 风格】群舞齐舞卡点" in text
    assert "【全局质感" not in text  # 缺省 brief 不带质感段
    # 配方 style_lock 经 write_script(style_text=…) 铆进 brief 材料段
    script_service.write_script(
        pose_text="自然站立。", pose_title="叉腰", clip_type="群舞齐舞卡点",
        duration=15, multishot=True,
        style_text="cinematic live-action，真人实拍电影感画质；high-key bright lighting",
    )
    text = seen["latest"][0]["content"]
    assert "【全局质感（来自配方 style_lock，全程一致）】" in text
    assert "high-key bright lighting" in text and "cinematic live-action" in text

    script_service.write_script(
        pose_text="自然站立。", pose_title="叉腰", clip_type="群舞齐舞卡点",
        duration=15, multishot=True,
    )
    text = seen["latest"][0]["content"]
    assert "只有首席一人出镜" in text and "伴舞安排" not in text
    assert "15 秒、4 个镜头" in text  # shots 缺省按时长推：15s → 4

    script_service.write_script(
        pose_text="自然站立。", pose_title="叉腰", clip_type="群舞齐舞卡点",
        duration=8, multishot=True,
    )
    assert "8 秒、3 个镜头" in seen["latest"][0]["content"]

    # 多镜校验：缺「镜头」拒；提到「镜头」放行
    with pytest.raises(ValueError):
        script_service.validate_script("首帧" + "画" * 410, multishot=True)
    ok = script_service.validate_script("以输入首帧为唯一视觉依据。" + "镜头" + "画" * 405, multishot=True)
    assert ok


def test_repo_random_mv_recipe_parses():
    """仓库随机 MV 配方解析合法；多镜字段、16:9 与检查点布局锁进测试。"""
    recipe = recipes_service.load_recipe("random_mv_video")
    assert [s.kind for s in recipe.steps] == [
        "pick", "scene_design", "combo_image", "video_script", "video_model",
    ]
    assert [recipes_service.manual_default(s) for s in recipe.steps] == [
        False, False, True, False, True,
    ]
    combo, script, video = recipe.steps[2], recipe.steps[3], recipe.steps[4]
    assert recipe.steps[0].face["sources"] == ["face-db-01"]
    assert combo.strip_accessories is True and "16:9" in (combo.framing_extra or "")
    assert script.multishot is True and script.ensemble is True and script.shots == 4
    assert script.duration == 15 and script.frame_from == "首帧图"
    assert script.amplitude == "large" and len(script.types) == 5
    assert video.duration == 15 and video.resolution == "768p"
    assert video.aspect == "16:9" and video.frame_from == "首帧图"
    assert len(combo.style_lock) == 3 and any("high-key" in w for w in combo.style_lock)
    assert any("live-action" in w for w in script.style_lock)


def test_mv_script_static_validation(tmp_path, monkeypatch):
    """multishot 静态约束：ensemble 必配 multishot / shots 限 2–6 / duration 必显式。"""
    monkeypatch.setenv("CHARVAR_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CHARVAR_PRESETS_DIR", str(tmp_path / "presets"))
    base = tmp_path / "presets" / "recipes"
    base.mkdir(parents=True, exist_ok=True)
    (base / "mvok_base.yaml").write_text(_MV_YAML_BASE, encoding="utf-8")
    recipe = recipes_service.load_recipe("mvok_base")
    assert recipe.steps[3].multishot and recipe.steps[3].ensemble and recipe.steps[3].shots == 4

    def bad(stem: str, text: str) -> None:
        (base / stem).write_text(text, encoding="utf-8")
        with pytest.raises(PresetError, match="multishot|ensemble|shots"):
            recipes_service.load_recipe(stem[:-5])

    bad("demmv_ens.yaml", _MV_YAML_BASE.replace("  multishot: true\n", ""))
    bad("demmv_shots.yaml", _MV_YAML_BASE.replace("shots: 4", "shots: 8"))
    bad(
        "demmv_dur.yaml",
        _MV_YAML_BASE.replace(
            "  multishot: true\n  ensemble: true\n  shots: 4\n  duration: 15\n",
            "  multishot: true\n  ensemble: true\n  shots: 4\n",
        ),
    )


@pytest.fixture()
def mv_env(dance_env):
    (Path(os.environ["CHARVAR_DATA_DIR"]) / "presets" / "recipes" / "demo_mv.yaml").write_text(
        _MV_YAML_BASE, encoding="utf-8"
    )
    return dance_env


def test_mv_full_flow(mv_env, monkeypatch):
    """MV 全链：多镜看图脚本（multishot/ensemble/shots 进 brief）→ 15s 480p 16:9 成片。"""
    conn = mv_env
    from charvar import video_script as script_service

    script_calls = []
    submit_reqs = []

    def fake_script_llm(messages, model=None, config=None):
        script_calls.append(messages)
        return _MV_SCRIPT_TEXT

    _stub_llms(monkeypatch)
    monkeypatch.setattr(script_service, "call_llm", fake_script_llm)
    monkeypatch.setattr(recipes_service, "_VIDEO_POLL_INTERVAL_S", 0.01)
    _stub_adhoc_generate(monkeypatch)
    submit_reqs = _stub_adhoc_video(
        monkeypatch,
        [{"status": "ready", "job_id": "j1", "model": "heygen/heygen-video-1"}],
    )

    state = recipes_service.start_run(conn, "demo_mv")
    assert state.status == "waiting"  # 停在「首帧图」

    state = recipes_service.advance_state(conn, state.recipe_run_id)
    assert state.status == "waiting"  # 停在「成片」
    content = script_calls[0][0]["content"]
    assert isinstance(content, list)  # 看图模式照常生效
    text = content[0]["text"]
    assert "第 1 镜的开场画面" in text and "15 秒、4 个镜头" in text
    assert "伴舞安排" in text and "【实录声场】" in text and "【配乐】" in text
    script_out = state.steps[3].outputs
    assert script_out["script"] == _MV_SCRIPT_TEXT

    state = recipes_service.advance_state(conn, state.recipe_run_id)
    assert state.status == "done"
    req = submit_reqs[0]
    assert req.duration == 15 and req.resolution == "480p"
    assert req.aspect_ratio == "16:9"


_UP_YAML_BASE = (
    _MV_YAML_BASE
    .replace("name: demo_mv", "name: demo_up")
    .replace("  strip_accessories: true\n  framing_extra: 全身入镜，四周留余白。\n", "  close_up: true\n")
    .replace(
        "  multishot: true\n  ensemble: true\n  shots: 4\n  duration: 15\n",
        "  multishot: true\n  ensemble: true\n  shots: 4\n  duration: 15\n"
        "  opening: face\n  text_fx: true\n  text_pool: [HIT, OWN IT, SHINE]\n",
    )
)


def test_video_script_text_fx_and_closeup_brief(tmp_path, monkeypatch):
    """文字特效：掷骰词对/池子覆盖/brief 编词/丢词校验；特写开场 brief 分叉。"""
    from charvar import video_script as script_service

    # 同种子同词对、池子覆盖、两词互异
    a = script_service.pick_text_words(7, "x", ["OWN IT", "HIT", "LOVE"])
    b = script_service.pick_text_words(7, "x", ["OWN IT", "HIT", "LOVE"])
    assert a == b and len(a) == 2 and a[0] != a[1]
    assert a[0] in ("OWN IT", "HIT", "LOVE")
    assert script_service.pick_text_words(1, "y", []) == ["LOVE", "SHINE"] or len(
        script_service.pick_text_words(1, "y", [])
    ) == 2  # 缺省池回退且两词

    seen: dict = {}

    def fake(messages, model=None, config=None):
        seen["latest"] = messages
        return _MV_SCRIPT_TEXT + " HIT OWN IT SHINE GO GO LOVE 画面大字。"

    monkeypatch.setattr(script_service, "call_llm", fake)

    script_service.write_script(
        pose_text="自然站立。", pose_title="叉腰", clip_type="群舞齐舞卡点",
        duration=15, multishot=True, ensemble=True, shots=4,
        opening="face", text_words=["GO GO", "SHINE"],
    )
    text = seen["latest"][0]["content"]
    assert isinstance(text, str)
    assert "面部大特写" in text and "特写开场" in text
    assert "光带" in text and "不频闪、不往复" in text  # 特写扫光条款（校准自原片）
    assert "第 2 镜起切到全身" in text
    assert "「GO GO」「SHINE」" in text and "文字特效" in text
    # 特写开场伴舞顺延条款在 brief 里出现
    assert "特写开场时第 3 镜起入画" in text

    # body 开场（缺省）不含特写条款
    script_service.write_script(
        pose_text="自然站立。", pose_title="叉腰", clip_type="群舞齐舞卡点",
        duration=15, multishot=True, shots=4,
    )
    text = seen["latest"][0]["content"]
    assert "面部大特写" not in text and "「GO GO」" not in text
    assert "7. 正文之后" in text  # 无文字特效时收尾条款回 7 号

    # 校验：丢词拒；两词齐放行（长度补足 400+）
    with pytest.raises(ValueError):
        script_service.validate_script("首帧" + "画" * 410 + " GO GO", multishot=True, text_words=["GO GO", "SHINE"])
    ok = script_service.validate_script(
        "以输入首帧为唯一视觉依据。" + "镜头" + "画" * 200 + " GO GO SHINE" + "画" * 200,
        multishot=True, text_words=["GO GO", "SHINE"],
    )
    assert ok


def test_repo_random_mv_up_recipe_parses():
    """仓库随机 MV 升级版配方解析合法；特写/文字特效字段与 768p 档锁进测试。"""
    recipe = recipes_service.load_recipe("random_mv_video_up")
    assert [s.kind for s in recipe.steps] == [
        "pick", "scene_design", "combo_image", "video_script", "video_model",
    ]
    assert [recipes_service.manual_default(s) for s in recipe.steps] == [
        False, False, True, False, True,
    ]
    combo, script, video = recipe.steps[2], recipe.steps[3], recipe.steps[4]
    assert combo.close_up is True
    assert combo.strip_accessories is False and not combo.framing_extra
    assert script.multishot is True and script.opening == "face"
    assert script.text_fx is True
    assert script.text_pool == ["HIT", "OWN IT", "SHINE", "GO GO", "LOVE"]
    assert script.shots == 4 and script.duration == 15 and script.frame_from == "首帧图"
    assert script.amplitude == "large" and len(script.types) == 5
    assert video.duration == 15 and video.resolution == "768p"  # 过审档（实测 $0.018/s）
    assert video.aspect == "16:9" and video.frame_from == "首帧图"
    assert len(combo.style_lock) == 3 and any("high-key" in w for w in combo.style_lock)
    assert any("live-action" in w for w in script.style_lock)


def test_repo_photo_idol_mv10_recipe_parses():
    """固定模板特效配方（照片变身打歌 MV 10 秒版）解析合法；两段提示词逐字、10s 768p 档锁进测试。"""
    recipe = recipes_service.load_recipe("photo_idol_mv10")
    assert [s.kind for s in recipe.steps] == ["adhoc_image", "video_model"]
    assert all(recipes_service.manual_default(s) for s in recipe.steps)
    ref = recipe.refs[0]
    assert ref.label == "用户照片" and ref.source == "asset" and ref.path == "user_photo.jpg"
    img, video = recipe.steps[0], recipe.steps[1]
    # 首帧通路钉 openai/gpt-image-2.6-flare：Qwen-Edit 试跑不过验收清单（肩入画+素颜），编辑系模型才跟得动
    # 「极近特写重构图 + 日系 idol 妆」两条硬要求（实测 flare 一把过，$0.023/张）
    assert img.backend == "openrouter" and img.model == "openai/gpt-image-2.6-flare"
    assert img.aspect == "16:9" and img.resolution is None  # flare 不声明 resolution，不发
    assert img.reference_labels == ["用户照片"]
    # 提示词 A 逐字锚点（身份唯一参考 + 构图红线 + 质感锁柔光变体）
    assert "以素材1中的人物为唯一身份参考" in img.prompt
    assert "脖子、肩膀、锁骨及任何衣服、领口、肩带均不得入画" in img.prompt
    assert "high-key bright lighting，高调明亮柔光" in img.prompt
    assert "cinematic live-action" in img.prompt
    # 提示词 B 逐字锚点（四镜 2+2+3+3 / 双字卡 / 身份稳定设计 / 统一约束）
    assert "【镜头一｜0–2秒：贴脸对视】" in video.prompt
    assert "【镜头二｜2–4秒：READY，登场即起舞】" in video.prompt
    assert "【镜头三｜4–7秒：全员齐舞，动作爆发】" in video.prompt
    assert "【镜头四｜7–10秒：SHINE，最后一拍亮相】" in video.prompt
    assert "单次扫完，不频闪、不往复" in video.prompt
    assert "主角不从远处人群中走近" in video.prompt and "不甩头、不转身" in video.prompt
    assert "除“READY”“SHINE”外，无其他文字、字幕、Logo或水印" in video.prompt
    assert video.model == "heygen/heygen-video-1"  # 固定模板钉供应商（无声，音乐段当氛围读）
    assert video.duration == 10 and video.resolution == "768p"  # 实测折半 ≈$0.18/条
    assert video.aspect == "16:9" and video.frame_from == "首帧图" and video.frame_slot == "first_frame"


def test_mv_up_static_validation(tmp_path, monkeypatch):
    """升级版静态约束：opening/text_fx 必配 multishot、text_pool 空白词、close_up 冲突。"""
    monkeypatch.setenv("CHARVAR_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CHARVAR_PRESETS_DIR", str(tmp_path / "presets"))
    base = tmp_path / "presets" / "recipes"
    base.mkdir(parents=True, exist_ok=True)
    (base / "mvup_base.yaml").write_text(_UP_YAML_BASE, encoding="utf-8")
    recipe = recipes_service.load_recipe("mvup_base")
    assert recipe.steps[2].close_up and recipe.steps[3].opening == "face"

    def bad(stem: str, text: str, match: str) -> None:
        (base / stem).write_text(text, encoding="utf-8")
        with pytest.raises(PresetError, match=match):
            recipes_service.load_recipe(stem[:-5])

    bad(
        "mvup_open.yaml",
        _UP_YAML_BASE.replace(
            "  multishot: true\n  ensemble: true\n  shots: 4\n  duration: 15\n"
            "  opening: face\n  text_fx: true\n  text_pool: [HIT, OWN IT, SHINE]\n",
            "  duration: 15\n  opening: face\n",
        ),
        "opening",
    )
    bad(
        "mvup_fx.yaml",
        _UP_YAML_BASE.replace(
            "  multishot: true\n  ensemble: true\n  shots: 4\n  duration: 15\n"
            "  opening: face\n  text_fx: true\n  text_pool: [HIT, OWN IT, SHINE]\n",
            "  duration: 15\n  text_fx: true\n  text_pool: [HIT, SHINE]\n",
        ),
        "text_fx",
    )
    bad(
        "mvup_pool.yaml",
        _UP_YAML_BASE.replace("text_pool: [HIT, OWN IT, SHINE]", 'text_pool: [HIT, ""]'),
        "text_pool",
    )
    bad(
        "mvup_style.yaml",
        _UP_YAML_BASE.replace(
            "text_pool: [HIT, OWN IT, SHINE]",
            'text_pool: [HIT, SHINE]\n  style_lock: [""]',
        ),
        "style_lock",
    )
    bad(
        "mvup_conflict.yaml",
        _UP_YAML_BASE.replace("  close_up: true\n", "  close_up: true\n  framing_extra: x\n"),
        "close_up",
    )


def test_scene_fallback_keeps_fixed_background(model_env):
    """不接 scene_from（或场景文本为空）→ build_prompt 保持固定背景句（CLI/网页零改动）。"""
    from charvar import pose_prompt
    from charvar.scene_design import validate_scene

    prompt = pose_prompt.build_prompt("全身1人・セクシー系", "她正面朝向镜头站立。")
    assert "背景简洁，突出角色。" in prompt
    prompt2 = pose_prompt.build_prompt("全身1人・セクシー系", "她正面朝向镜头站立。", background_text=_SCENE_TEXT)
    assert _SCENE_TEXT in prompt2 and "背景简洁，突出角色。" not in prompt2
    prompt3 = pose_prompt.build_prompt("全身1人・セクシー系", "她正面朝向镜头站立。", background_text="   ")
    assert "背景简洁，突出角色。" in prompt3
    # 场景文本校验边界：实测 LLM 偶发 150+ 字 —— 220 内放行，221 拦下
    fine_long = "一间" + "浅" * 150 + "室内。"
    assert len(fine_long) == 155
    assert validate_scene(fine_long) == fine_long
    with pytest.raises(ValueError):
        validate_scene("短")
    with pytest.raises(ValueError):
        validate_scene("长" * 221)
