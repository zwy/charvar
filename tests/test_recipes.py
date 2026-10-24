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
