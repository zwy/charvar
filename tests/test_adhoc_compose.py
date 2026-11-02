"""Playground 工作流拼装（POST /adhoc/compose）：构成校验 + 提示词同源同结构。

覆盖（待完善清单 B3/B4 网页侧）：
- 四种工作流模式的 prompt 与 pose_prompt.build_prompt / build_costume_prompt
  逐字一致（网页不另造拼装 —— B3 红线）；
- references 顺序 = 身份 → （乙）→ 动作 → 服饰，与 CLI refs.append 次序一致；
- 校验矩阵 400：face×subject、face×双人、costume×双人、subject2×非双人、
  换装缺服饰、缺身份锚、空动作描述、纯文本服饰条目、未知工作流；
- costume 参考图在 /adhoc/generate 里解析成真实文件（mock ComfyUI 全链路）。
"""
from __future__ import annotations

import json
import pathlib

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from charvar import costumes as costumes_service
from charvar import faces as faces_service
from charvar import poses as poses_service
from charvar import pose_prompt
from charvar import storage
from charvar.api import build_app
from charvar.models import SubjectProfile
from tests.test_adhoc import _mock_comfy, _setup_workflows
from tests.test_costumes import _build_fake_costume_data
from tests.test_faces import _build_fake_face_data
from tests.test_poses import _png_bytes


POSE_PLAN = {
    "full_sexy_P001": ("全身1人・セクシー系", "呈自然的 S 形曲线，双手自然垂放"),
    "upper80v2_P005": ("上半身1人", "手掌贴在左脸颊，指尖齐眉"),
    "pair60_P010": ("2人絡み", "后方角色从身后环抱前方角色，下巴轻搭"),
}


def _seed_all(data_dir: pathlib.Path, db_path: pathlib.Path | None = None) -> None:
    """姿势/人脸/服饰三库 + 主体档案（front+headshot 槽位图）一次备齐。"""
    src = data_dir / "poses" / poses_service.SOURCE_ID
    (src / "img").mkdir(parents=True, exist_ok=True)
    (src / "thumbs").mkdir(parents=True, exist_ok=True)
    items = []
    for pose_id, (line, _desc) in POSE_PLAN.items():
        item_id = f"{pose_id}_front"
        Image.new("RGB", (90, 120), (120, 120, 120)).save(src / f"img/{item_id}.png", format="PNG")
        Image.new("RGB", (30, 40), (120, 120, 120)).save(src / f"thumbs/{item_id}.jpg", format="JPEG")
        items.append({
            "id": item_id, "系統": line, "ポーズ番号": pose_id.split("_")[-1],
            "分類": "立ち", "ポーズ名": pose_id, "アングル": "正面",
            "サムネ": f"thumbs/{item_id}.jpg", "原寸": f"img/{item_id}.png",
        })
    (src / "catalog.json").write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    _build_fake_face_data(data_dir)
    _build_fake_costume_data(data_dir)
    with storage.connect(db_path) as conn:
        poses_service.import_catalog(conn)
        for pose_id, (_line, desc) in POSE_PLAN.items():
            poses_service.set_pose_action(conn, pose_id, "", desc, source_id=poses_service.SOURCE_ID)
        faces_service.import_catalog(conn)
        costumes_service.import_catalog(conn)
        # 纯文本服饰条目（无参考图）：图锚定用不了（400 案例）
        conn.execute(
            "INSERT INTO costume_items (source_id, item_id, keywords, created_at, updated_at)"
            " VALUES ('studio', 'text_only_entry', 'plain text keywords', '2026', '2026')"
        )
        conn.commit()
        profile = SubjectProfile(
            id="demo_witch", name="演示", subject_noun="woman",
            style_lock=["clean lineart"], identity_lock=["short silver bob"],
            outfit_lock=["flight suit"], body_lock=["keep soft full figure"],
            extra="ok",
        )
        storage.save_subject(conn, profile, replace=True)
        for kind in ("front", "headshot"):
            png = _png_bytes()
            (data_dir / "subjects" / "demo_witch" / f"{kind}.png").parent.mkdir(parents=True, exist_ok=True)
            (data_dir / "subjects" / "demo_witch" / f"{kind}.png").write_bytes(png)
            storage.upsert_subject_image(conn, {
                "subject_id": "demo_witch", "kind": kind, "status": "ready",
                "source": "comfyui", "file_path": f"subjects/demo_witch/{kind}.png",
            })

@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CHARVAR_DATA_DIR", str(tmp_path))
    db_path = pathlib.Path(tmp_path) / "test.db"
    _seed_all(tmp_path, db_path)
    app = build_app(static_dir=None, db_path=db_path)
    return TestClient(app)


def _compose(client, **kw):
    req = {"workflow": kw.pop("workflow", "pose")}
    req.update(kw)
    return client.post("/adhoc/compose", json=req)


SINGLE = {"workflow": "pose", "subject_id": "demo_witch",
          "pose_id": "full_sexy_P001", "pose_angle": "", "pose_source": ""}
COSTUME_ITEM = {"costume_id": "SPR-0001", "costume_source": "costume-db-01"}


def test_single_subject_pose_matches_cli(client):
    """单主体动作：prompt 与 build_prompt 同源同结构；references = [主体, 动作]。"""
    res = _compose(client, subject_id="demo_witch", pose_id="full_sexy_P001")
    assert res.status_code == 200, res.text
    data = res.json()
    expect = pose_prompt.build_prompt("全身1人・セクシー系", POSE_PLAN["full_sexy_P001"][1])
    assert data["prompt"] == expect
    assert [r["role"] for r in data["references"]] == ["front", "pose"]
    assert data["references"][0]["subject_id"] == "demo_witch"
    assert data["references"][0]["kind"] == "front"
    assert data["references"][1]["pose_id"] == "full_sexy_P001" and data["references"][1]["angle"] == "正面"
    assert data["meta"]["line_kind"] == "full"


def test_upper_body_uses_headshot_slot(client):
    upper = dict(SINGLE, pose_id="upper80v2_P005")
    data = _compose(client, **upper).json()
    expect = pose_prompt.build_prompt("上半身1人", POSE_PLAN["upper80v2_P005"][1])
    assert data["prompt"] == expect
    assert data["references"][0]["kind"] == "headshot"
    assert data["meta"]["line_kind"] == "upper"


def test_pair_two_subjects(client):
    data = _compose(client, subject_id="demo_witch", subject2_id="demo_witch",
                    pose_id="pair60_P010").json()
    expect = pose_prompt.build_prompt("2人絡み", POSE_PLAN["pair60_P010"][1], pair_two_subjects=True)
    assert data["prompt"] == expect
    assert [r["role"] for r in data["references"]] == ["front", "front", "pose"]
    assert data["references"][1]["subject_id"] == "demo_witch"  # 乙也进 image2（同 id 两位 = 同角色双人）


def test_pose_with_costume_matches_cli(client):
    data = _compose(client, subject_id="demo_witch", pose_id="full_sexy_P001", **COSTUME_ITEM).json()
    expect = pose_prompt.build_prompt(
        "全身1人・セクシー系", POSE_PLAN["full_sexy_P001"][1], costume=True,
        body_lock=["keep soft full figure"],
        costume_keywords="ivory frill knit denim mini skirt coat socks white sneakers",
        costume_display="ghost",
    )
    assert data["prompt"] == expect
    assert [r["role"] for r in data["references"]] == ["front", "pose", "costume"]
    assert data["meta"]["costume_mode"] == "replace"


def test_face_identity_pose_workflow(client):
    """人脸动作：face 条目当 image1（section 随条目）；body_lock 逐字进 prompt。"""
    data = _compose(client, face_id="faceaaa11111", face_source="face-db-01",
                    pose_id="full_sexy_P001", body_lock=["slim waist, long legs"]).json()
    expect = pose_prompt.build_prompt(
        "全身1人・セクシー系", POSE_PLAN["full_sexy_P001"][1],
        body_lock=["slim waist, long legs"], face_section="face",
    )
    assert data["prompt"] == expect
    refs = data["references"]
    assert refs[0]["face_id"] == "faceaaa11111" and refs[0]["role"] == "face"
    assert refs[1]["role"] == "pose"
    assert data["meta"]["face_section"] == "face"


def test_face_styling_uses_subject_segments(client):
    """styling 条目（锚整体造型）走主体版分段（同 CLI），身份句/体形锚不换 face 版。"""
    data = _compose(client, face_id="styleccc33333", pose_id="full_sexy_P001",
                    body_lock=["keep soft full figure"]).json()
    expect = pose_prompt.build_prompt(
        "全身1人・セクシー系", POSE_PLAN["full_sexy_P001"][1], body_lock=["keep soft full figure"],
    )
    assert data["prompt"] == expect
    assert data["meta"]["face_section"] == "styling"


def test_costume_workflow_subject(client):
    data = _compose(client, workflow="costume", subject_id="demo_witch", **COSTUME_ITEM).json()
    expect = pose_prompt.build_costume_prompt(
        body_lock=["keep soft full figure"],
        costume_keywords="ivory frill knit denim mini skirt coat socks white sneakers",
        costume_display="ghost",
    )
    assert data["prompt"] == expect
    assert [r["role"] for r in data["references"]] == ["front", "costume"]
    assert data["meta"]["workflow"] == "costume"


def test_costume_workflow_face(client):
    data = _compose(client, workflow="costume", face_id="faceaaa11111", body_lock=["slim figure"],
                    **COSTUME_ITEM).json()
    expect = pose_prompt.build_costume_prompt(
        body_lock=["slim figure"],
        costume_keywords="ivory frill knit denim mini skirt coat socks white sneakers",
        costume_display="ghost", face_section="face",
    )
    assert data["prompt"] == expect
    assert [r["role"] for r in data["references"]] == ["face", "costume"]


# ---------- 校验矩阵（400） ----------

def test_validation_matrix(client):
    assert _compose(client, subject_id="demo_witch", face_id="faceaaa11111",
                    pose_id="full_sexy_P001").status_code == 400  # face×subject
    assert _compose(client, face_id="faceaaa11111", pose_id="pair60_P010").status_code == 400  # face×双人
    body = dict(SINGLE, pose_id="pair60_P010", costume_id="SPR-0001", costume_source="costume-db-01")
    assert _compose(client, **body).status_code == 400  # costume×双人? —— line 是 2人絡み 但只给了单 subject
    assert _compose(client, subject_id="demo_witch", pose_id="full_sexy_P001",
                    subject2_id="demo_witch").status_code == 400  # subject2×非双人
    assert _compose(client, subject_id="demo_witch", subject2_id="demo_witch",
                    pose_id="pair60_P010", costume_id="SPR-0001",
                    costume_source="costume-db-01").status_code == 400  # 双人×服饰
    assert _compose(client, workflow="costume", subject_id="demo_witch").status_code == 400  # 换装缺服饰
    assert _compose(client, workflow="costume", costume_id="SPR-0001").status_code == 400  # 缺身份锚
    assert _compose(client, workflow="nonsense", subject_id="demo_witch",
                    pose_id="full_sexy_P001").status_code == 400  # 未知工作流


def test_text_only_costume_rejected(client):
    res = _compose(client, workflow="costume", subject_id="demo_witch",
                   costume_id="text_only_entry", costume_source="studio")
    assert res.status_code == 400 and "纯文本" in res.json()["detail"]



def test_empty_action_text_rejected(client):
    """没写动作描述的视角不能拼装（清掉正面描述后 400）。"""
    put = client.put(
        "/poses/pose-db-01/full_sexy_P001/action",
        json={"angle": "正面", "action_text": ""},
    )
    assert put.status_code == 200
    res = _compose(client, subject_id="demo_witch", pose_id="full_sexy_P001")
    assert res.status_code == 400 and "动作描述" in res.json()["detail"]

@pytest.fixture()
def gclient(tmp_path, monkeypatch):
    """带 mock ComfyUI 的端到端 client（工作流 mapping + 设置 + 传输打桩）。"""
    monkeypatch.setenv("CHARVAR_DATA_DIR", str(tmp_path))
    _setup_workflows(tmp_path, monkeypatch)
    monkeypatch.setenv("COMFYUI_HOST", "127.0.0.1")
    monkeypatch.setenv("COMFYUI_PORT", "59999")
    db_path = pathlib.Path(tmp_path) / "test.db"
    _seed_all(tmp_path, db_path)
    with storage.connect(db_path) as conn:
        storage.set_setting(conn, "comfy_t2i_workflow", "TestT2I")
        storage.set_setting(conn, "comfy_edit_workflow", "TestEdit")
    app = build_app(static_dir=None, db_path=db_path)
    return TestClient(app)

def test_composed_references_generate_end_to_end(tmp_path, monkeypatch, gclient):
    """工作流模式端到端：compose 出的 references（含 costume）在 generate 里解析成文件。"""
    uploads: list[str] = []
    _mock_comfy(monkeypatch, uploads, [])
    body = {"workflow": "costume", "subject_id": "demo_witch",
            "costume_id": "SPR-0001", "costume_source": "costume-db-01"}
    composed = gclient.post("/adhoc/compose", json=body)
    assert composed.status_code == 200, composed.text
    refs = composed.json()["references"]
    assert [r["role"] for r in refs] == ["front", "costume"]
    gen = {"prompt": composed.json()["prompt"], "backend": "comfyui", "seed": 7,
           "aspect_ratio": "3:4", "resolution": "1k", "references": refs}
    resp = gclient.post("/adhoc/generate", json=gen)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "ready"
    # 主体槽位图 + 服饰图两张参考都上传了（mock edit workflow 恰好两个槽位）
    refs_sent = [f for f in uploads if f.endswith(".jpg")]
    assert len(refs_sent) == 2
