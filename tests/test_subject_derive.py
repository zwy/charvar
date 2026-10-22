"""subject derive（MD 模板 → YAML 档案）：闸门与 CLI。LLM 全部打桩，不发真网。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from charvar import cli, llm, subject_derive as sd
from charvar import storage

runner = CliRunner()

_TEMPLATE = """# 瑜伽老师｜全场景通用提示词模板

版本：V1.0 · 自然真实摄影版

## 一、角色与创作边界

原创角色，成年女性，职业为瑜伽老师。识别锚点：

| 锚点 | 内容 |
|---|---|
| 头部 | 柔和鹅蛋脸，额头饱满，颧骨平缓，下颌圆润收窄，下巴小巧不过尖；杏眼，鼻梁挺直 |
| 发型 | 深棕近黑高马尾，头顶蓬松，额前侧分，碎发沿脸侧垂落 |
| 体型 | 健康丰满体型，腰线清晰，腿部紧实 |
| 上装 | 亮紫色单色无袖连体运动衣，高圆领，领口奶白色细滚边 |
| 下装 | 同亮紫弹力面料裤腿收紧至脚踝，白色中筒棉袜罗纹袜口 |

### 3.1 前缀

一位原创成年女性瑜伽老师角色，真实人物摄影，五官符合自然人体结构，保留细微皮肤纹理。
禁止 3D 渲染与蜡感皮肤。
"""

_V1 = {
    "id": "yoga_teacher",
    "name": "瑜伽老师",
    "subject_noun": "woman",
    "trigger_word": "yogateacher",
    "style_lock": ["真实人物摄影", "禁止 3D 渲染与蜡感皮肤"],
    "identity_lock": ["柔和鹅蛋脸，额头饱满，颧骨平缓，下颌圆润收窄"],
    "outfit_lock": ["亮紫色单色无袖连体运动衣，领口奶白色细滚边"],
    "extra": "Keep the same face and ponytail in every pose.",
    "candidates": [],
    "notes": "",
}


def _draft(**overrides) -> dict:
    data = {**_V1}
    for key, value in overrides.items():
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
    return data


def _chat_stub(monkeypatch, drafts: list[dict], verdicts: list[dict]) -> list[str]:
    """按 schema_name 分流的 chat_json 桩：起草案取 drafts（弹射）、审计取 verdicts。

    返回记录的 (usage) 列表："draft" / "verify"，用来断言调用轮数。
    """
    calls: list[str] = []
    # 复制成消费副本，避免用例间串状态
    draft_pool = list(drafts)
    verdict_pool = list(verdicts)

    def fake_chat_json(messages, **kwargs):
        schema_name = kwargs.get("schema_name")
        assert kwargs.get("schema"), "必须带 schema 调用（structured outputs）"
        if schema_name == "subject_profile_draft":
            assert draft_pool, "起草案弹空（轮数超预期）"
            calls.append("draft")
            return draft_pool.pop(0)
        assert schema_name == "subject_profile_verify", schema_name
        assert verdict_pool, "审计判决弹空（轮数超预期）"
        calls.append("verify")
        return verdict_pool.pop(0)

    monkeypatch.setattr(llm, "chat_json", fake_chat_json)
    return calls


# ---------- 引用核对 ----------

def test_citation_exact_and_punct_variants_hit():
    template_norm = sd._norm(_TEMPLATE)
    shingles = {template_norm[i:i + 2] for i in range(len(template_norm) - 1)}
    assert sd.citation_coverage("柔和鹅蛋脸，额头饱满", template_norm, shingles) == pytest.approx(1.0)
    # 标点/空白差异不误伤
    assert sd.citation_coverage("柔和鹅蛋脸， 额头饱满。", template_norm, shingles) > 0.99
    # 删词收紧（摘掉尾部条款）仍命中
    assert sd.citation_coverage("一位原创成年女性瑜伽老师角色", template_norm, shingles) == pytest.approx(1.0)


def test_citation_paraphrase_and_invention_miss():
    template_norm = sd._norm(_TEMPLATE)
    shingles = {template_norm[i:i + 2] for i in range(len(template_norm) - 1)}
    # 改写（加入模板没有的成分）
    assert sd.citation_coverage("真实人物摄影，皮肤是冷白皮", template_norm, shingles) < 0.8
    # 纯编造
    assert sd.citation_coverage("亮银色铠甲披风", template_norm, shingles) < 0.8
    # 超短条目按子串判
    assert sd.citation_coverage("杏眼", template_norm, shingles) == 1.0
    assert sd.citation_coverage("紫眼", template_norm, shingles) == 0.0


def test_citation_misses_lists_group_and_entry():
    profile = sd.profile_from_draft(
        {"id": "x", "name": "X", "subject_noun": "woman", "trigger_word": "",
         "style_lock": ["真实人物摄影"], "identity_lock": ["杏眼，鼻梁挺直"],
         "outfit_lock": ["亮银色铠甲披风"], "extra": ""},
    )
    misses = sd.citation_misses(_TEMPLATE, profile)
    assert ("outfit_lock", "亮银色铠甲披风") in misses
    assert all(group != "identity_lock" for group, _ in misses)


# ---------- 软校验 ----------

def test_quality_issues_flags_all_soft_rules():
    profile = sd.profile_from_draft({
        "id": "demo", "name": "Demo", "subject_noun": "wonman",
        "trigger_word": "", "extra": "x" * 301,
        "style_lock": [f"样式{i}" for i in range(11)] + ["真实人物摄影", "真实人物摄影"],
        "identity_lock": ["真实人物摄影"],  # 跨组重复
        "outfit_lock": ["亮" * 201],  # >200 字符
    })
    warnings = " ".join(sd.quality_issues(profile))
    assert "建议范围" in warnings              # noun 生僻
    assert "跨组重复" in warnings or "同一条锁定项" in warnings
    assert "软上限" in warnings                # 条数
    assert "单条超过" in warnings              # 长度
    assert "extra 超过" in warnings


def test_quality_issues_flags_scene_words():
    profile = sd.profile_from_draft({
        "id": "demo", "name": "Demo", "subject_noun": "woman", "trigger_word": "",
        "style_lock": ["黄昏逆光背景下的剪影风格"],
        "identity_lock": ["柔和鹅蛋脸"],
        "outfit_lock": ["亮紫色连体运动衣"],
        "extra": "",
    })
    warnings = sd.quality_issues(profile)
    assert any("场景词" in w for w in warnings)


# ---------- derive 主流程 ----------

def test_derive_success_zh(monkeypatch):
    calls = _chat_stub(monkeypatch, drafts=[_draft()], verdicts=[{"pass": True, "issues": []}])
    outcome = sd.derive(_TEMPLATE)
    assert outcome.verify_pass and outcome.draft_rounds == 1 and outcome.verify_rounds == 1
    assert outcome.profile.id == "yoga_teacher"
    assert outcome.profile.trigger_word == "yogateacher"
    assert outcome.citation_misses == [] and outcome.citation_repaired == []
    assert calls == ["draft", "verify"]


def test_derive_ambiguity_blocks_without_version(monkeypatch):
    calls = _chat_stub(
        monkeypatch,
        drafts=[_draft(candidates=["黑色系日常版", "白色系仪式版"])],
        verdicts=[],
    )
    with pytest.raises(sd.DeriveError) as exc_info:
        sd.derive(_TEMPLATE)
    assert "多套候选" in str(exc_info.value) and "白色系仪式版" in str(exc_info.value)
    assert "verify" not in calls  # 歧义在第一轮抛出，不浪费审计调用


def test_derive_version_hint_disambiguates(monkeypatch):
    calls = _chat_stub(monkeypatch, drafts=[_draft(name="瑜伽老师·仪式版")], verdicts=[{"pass": True, "issues": []}])
    outcome = sd.derive(_TEMPLATE, version_hint="仪式版")
    assert outcome.profile.name == "瑜伽老师·仪式版"
    assert "draft" in calls and "verify" in calls


def test_derive_reextracts_hallucinated_entries(monkeypatch):
    bad = _draft(outfit_lock=["亮银色铠甲披风"])
    capture: dict = {}

    def fake_chat_json(messages, **kwargs):
        if kwargs.get("schema_name") == "subject_profile_draft":
            capture["calls"] = capture.get("calls", 0) + 1
            if capture["calls"] == 2:
                capture["messages"] = messages  # 修复轮的 messages（带问题单）
            return dict(bad) if capture["calls"] == 1 else _draft()
        return {"pass": True, "issues": []}

    monkeypatch.setattr(llm, "chat_json", fake_chat_json)
    outcome = sd.derive(_TEMPLATE)
    assert outcome.draft_rounds == 2
    assert ("outfit_lock", "亮银色铠甲披风") in outcome.citation_repaired
    assert outcome.citation_misses == []
    # 修复轮的问题单包含未命中条目与组名
    repair_text = "\n".join(m["content"] for m in capture["messages"])
    assert "亮银色铠甲披风" in repair_text and "outfit_lock" in repair_text
    assert outcome.verify_pass


def test_derive_gives_up_citation_after_retries(monkeypatch):
    bad = _draft(outfit_lock=["亮银色铠甲披风"])
    _chat_stub(monkeypatch, drafts=[bad, bad, bad], verdicts=[])
    with pytest.raises(sd.DeriveError) as exc_info:
        sd.derive(_TEMPLATE)
    assert "找不到对应原文" in str(exc_info.value)


def test_derive_repairs_on_audit_issues(monkeypatch):
    issues = [{"severity": "omission", "quote": "白色中筒棉袜", "detail": "丢了下装的棉袜条目"}]
    calls = _chat_stub(
        monkeypatch,
        drafts=[_draft(outfit_lock=["亮紫色连体运动衣"]), _draft()],
        verdicts=[{"pass": False, "issues": issues}, {"pass": True, "issues": []}],
    )
    outcome = sd.derive(_TEMPLATE)
    assert outcome.verify_pass and outcome.verify_rounds == 2
    assert calls.count("verify") == 2


def test_derive_raises_when_audit_never_passes(monkeypatch):
    issues = [{"severity": "contradiction", "quote": "", "detail": "模板是马尾，档案写成短发"}]
    _chat_stub(
        monkeypatch,
        drafts=[_draft(), _draft(), _draft()],
        verdicts=[{"pass": False, "issues": issues}] * 3,
    )
    with pytest.raises(sd.DeriveError) as exc_info:
        sd.derive(_TEMPLATE)
    assert "审计" in str(exc_info.value)


def test_derive_en_skips_citation(monkeypatch):
    calls = _chat_stub(monkeypatch, drafts=[_draft()], verdicts=[{"pass": True, "issues": []}])
    outcome = sd.derive(_TEMPLATE, lang="en")
    assert outcome.lang == "en" and outcome.citation_repaired == []
    assert "en 翻译模式" in outcome.citation_summary()
    assert "verify" in calls


def test_derive_invalid_id_reports(monkeypatch):
    bad = _draft(id="Yoga Teacher!")
    _chat_stub(monkeypatch, drafts=[bad, bad, bad], verdicts=[])
    with pytest.raises(sd.DeriveError) as exc_info:
        sd.derive(_TEMPLATE)
    assert "字段不合规" in str(exc_info.value)


def test_derive_subject_id_and_name_override(monkeypatch):
    _chat_stub(monkeypatch, drafts=[_draft()], verdicts=[{"pass": True, "issues": []}])
    outcome = sd.derive(_TEMPLATE, subject_id="custom_id", name="自定义名")
    assert outcome.profile.id == "custom_id" and outcome.profile.name == "自定义名"


def test_derive_empty_template_rejected():
    with pytest.raises(sd.DeriveError):
        sd.derive("   \n")


# ---------- YAML 落盘 ----------

def test_write_profile_yaml_provenance_header(tmp_path):
    outcome_fixture = sd.DeriveOutcome(
        profile=sd.profile_from_draft(_draft()), lang="zh", draft_rounds=1, verify_rounds=1,
        verify_pass=True, verify_issues=[], citation_misses=[], citation_repaired=[],
        quality_warnings=[],
    )
    target = tmp_path / "demo.yaml"
    sd.write_profile_yaml(
        target, outcome_fixture.profile,
        source_name="yoga.md", source_sha256="a" * 64, model="m1", outcome=outcome_fixture,
    )
    text = target.read_text(encoding="utf-8")
    assert text.startswith("# 本文件由 `charvar subject derive`")
    assert "yoga.md" in text and "sha256:aaaaaaaaaaaa" in text
    assert "审计通过" in text
    data = yaml.safe_load(text)
    assert data["id"] == "yoga_teacher"


# ---------- CLI ----------

def _envdirs(monkeypatch, tmp_path) -> Path:
    monkeypatch.setenv("CHARVAR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CHARVAR_PRESETS_DIR", str(tmp_path / "presets"))
    (tmp_path / "templates").mkdir(parents=True)
    return tmp_path / "templates" / "template.md"


def _prepare_template(tmp_path) -> Path:
    path = tmp_path / "templates" / "template.md"
    path.write_text(_TEMPLATE, encoding="utf-8")
    return path


def test_cli_derive_writes_yaml_not_db(monkeypatch, tmp_path):
    _envdirs(monkeypatch, tmp_path)
    template_path = _prepare_template(tmp_path)
    _chat_stub(monkeypatch, drafts=[_draft()], verdicts=[{"pass": True, "issues": []}])
    result = runner.invoke(cli.app, ["subject", "derive", str(template_path)])
    assert result.exit_code == 0, result.output
    yml = tmp_path / "presets" / "subjects" / "yoga_teacher.yaml"
    assert yml.exists()
    data = yaml.safe_load(yml.read_text(encoding="utf-8"))
    assert data["id"] == "yoga_teacher"
    with storage.connect() as conn:
        assert storage.get_subject(conn, "yoga_teacher") is None  # 未入库


def test_cli_derive_json_pure_json(monkeypatch, tmp_path):
    _envdirs(monkeypatch, tmp_path)
    template_path = _prepare_template(tmp_path)
    _chat_stub(monkeypatch, drafts=[_draft()], verdicts=[{"pass": True, "issues": []}])
    result = runner.invoke(cli.app, ["subject", "derive", str(template_path), "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["profile"]["id"] == "yoga_teacher"
    assert data["verify_pass"] is True and data["draft_rounds"] == 1


def test_cli_derive_apply_saves_subject(monkeypatch, tmp_path):
    _envdirs(monkeypatch, tmp_path)
    template_path = _prepare_template(tmp_path)
    _chat_stub(monkeypatch, drafts=[_draft()], verdicts=[{"pass": True, "issues": []}])
    result = runner.invoke(cli.app, ["subject", "derive", str(template_path), "--apply"])
    assert result.exit_code == 0, result.output
    with storage.connect() as conn:
        assert storage.get_subject(conn, "yoga_teacher") is not None


def test_cli_derive_conflicts_need_overwrite(monkeypatch, tmp_path):
    _envdirs(monkeypatch, tmp_path)
    template_path = _prepare_template(tmp_path)
    _chat_stub(monkeypatch, drafts=[_draft()], verdicts=[{"pass": True, "issues": []}])
    # presets/subjects/yoga_teacher.yaml 已存在（仓库快照）
    yml_dir = tmp_path / "presets" / "subjects"
    yml_dir.mkdir(parents=True)
    (yml_dir / "yoga_teacher.yaml").write_text("id: yoga_teacher\n", encoding="utf-8")
    result = runner.invoke(cli.app, ["subject", "derive", str(template_path)])
    assert result.exit_code == 1
    assert "--overwrite" in result.output
    # -f 才放行
    _chat_stub(monkeypatch, drafts=[_draft()], verdicts=[{"pass": True, "issues": []}])
    result = runner.invoke(cli.app, ["subject", "derive", str(template_path), "--overwrite"])
    assert result.exit_code == 0, result.output


def test_cli_derive_ambiguity_errors(monkeypatch, tmp_path):
    _envdirs(monkeypatch, tmp_path)
    template_path = _prepare_template(tmp_path)
    _chat_stub(monkeypatch, drafts=[_draft(candidates=["黑色系日常版", "白色系仪式版"])], verdicts=[])
    result = runner.invoke(cli.app, ["subject", "derive", str(template_path)])
    assert result.exit_code == 1
    assert "白色系仪式版" in result.output


def test_cli_verify_needs_template(monkeypatch, tmp_path):
    _envdirs(monkeypatch, tmp_path)
    with storage.connect() as conn:
        profile = sd.profile_from_draft(_draft())
        storage.save_subject(conn, profile, replace=True)
    result = runner.invoke(cli.app, ["subject", "verify", "yoga_teacher"])
    assert result.exit_code == 1
    assert "没有模板" in result.output


def test_cli_verify_reports_verdict(monkeypatch, tmp_path):
    _envdirs(monkeypatch, tmp_path)
    issue = {"severity": "omission", "quote": "白色中筒棉袜", "detail": "丢了棉袜"}
    _chat_stub(monkeypatch, drafts=[], verdicts=[{"pass": False, "issues": [issue]}] * 2)
    with storage.connect() as conn:
        profile = sd.profile_from_draft(_draft())
        storage.save_subject(conn, profile, replace=True)
        storage.save_subject_template(conn, "yoga_teacher", _TEMPLATE)
    result = runner.invoke(cli.app, ["subject", "verify", "yoga_teacher"])
    assert result.exit_code == 1
    assert "丢了棉袜" in result.output
    result = runner.invoke(cli.app, ["subject", "verify", "yoga_teacher", "--json"])
    data = json.loads(result.stdout)
    assert data["pass"] is False and data["issues"][0]["detail"] == "丢了棉袜"