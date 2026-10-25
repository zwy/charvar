"""presets/ 结构校验：命名规则、目录归属、引用完整性、垃圾文件。

这是 `presets/README.md` 里那套布局契约的机器检查——规范写在文档里，
这里负责让它不漂。

覆盖：
- 动作预设只住在 actions/，文件名与 name 字段一致；
- 命名规则：小写 snake_case、`_vN` 后缀与 version 字段一致、条目数后缀只留给冻结预设；
- subject 归属指向存在的 subject 档案；
- 配方 doc 指向同名 .md；通配符 _ref 指向存在的池文件；
- 各目录的 type 字段与目录职责一致；无垃圾文件。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

PRESETS_ROOT = Path(__file__).resolve().parent.parent / "presets"

# 条目数后缀（_9 / _30 之类）只允许出现在**冻结**预设上：
# 它们的条数不会再变，留在文件名里是准确信息；会演进的预设一律不写条目数。
FROZEN_COUNT_SUFFIX_PRESETS = {"q21_default_30", "q21_legacy_30", "q21_v2_30"}

FILENAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
VERSION_SUFFIX_RE = re.compile(r"^(?P<stem>.+)_v(?P<version>\d+)$")
COUNT_SUFFIX_RE = re.compile(r"^(?P<stem>.+)_(?P<count>\d+)$")

# 目录 → 该目录下 yaml 允许的 type 值（None = 不带 type 字段）
DIR_TYPE_RULES: dict[str, set[str] | None] = {
    "actions": {"list", "matrix"},
    "outfits": {"outfit"},
    "overlays": {"overlay"},
    "video": {"video"},
    "recipes": {"recipe"},
    "subjects": None,
    "wildcards": None,
}


def _load(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    assert isinstance(data, dict), f"{path} 不是 YAML 映射"
    return data


def _yaml_files(directory: str) -> list[Path]:
    d = PRESETS_ROOT / directory
    if not d.is_dir():
        return []
    return sorted(d.glob("*.yaml"))


def _action_presets() -> list[Path]:
    return _yaml_files("actions")


# ---------- 目录归属 ----------

def test_action_presets_only_live_in_actions_dir():
    """顶层不放动作预设（actions/ 是唯一归属目录）。"""
    stray = [
        p.name for p in sorted(PRESETS_ROOT.glob("*.yaml"))
        if _load(p).get("type") in ("list", "matrix")
    ]
    assert stray == [], f"动作预设应放 presets/actions/，顶层发现：{stray}"


@pytest.mark.parametrize("directory,allowed", sorted(DIR_TYPE_RULES.items()))
def test_directory_type_matches_its_role(directory, allowed):
    """每个目录里的 yaml 声明的 type 与目录职责一致。"""
    for path in _yaml_files(directory):
        got = _load(path).get("type")
        if allowed is None:
            assert got is None, f"{path.relative_to(PRESETS_ROOT)} 不该带 type 字段（got {got!r}）"
        else:
            assert got in allowed, (
                f"{path.relative_to(PRESETS_ROOT)} 的 type={got!r} 不属于 {allowed}"
            )


# ---------- 命名 ----------

def test_action_preset_name_matches_filename():
    """yaml 的 name 字段必须等于文件名 stem —— 名字是稳定标识，不能漂。"""
    for path in _action_presets():
        name = _load(path).get("name")
        assert name == path.stem, (
            f"{path.relative_to(PRESETS_ROOT)} 的 name={name!r} != 文件名 stem {path.stem!r}"
        )


def test_action_preset_names_unique():
    seen: dict[str, str] = {}
    for path in _action_presets():
        name = _load(path).get("name")
        assert name not in seen, f"预设名重复：{name!r}（{seen.get(name)} 与 {path.name}）"
        seen[name] = path.name


def test_action_preset_filename_is_lowercase_snake_case():
    for path in _action_presets():
        assert FILENAME_RE.match(path.stem), (
            f"文件名不合规范：{path.name}（只允许小写字母/数字/下划线，且以字母开头）"
        )


def test_version_suffix_matches_version_field():
    """文件名里的 _vN 必须与 version 字段一致（版本只有一个真相源）。"""
    for path in _action_presets():
        match = VERSION_SUFFIX_RE.match(path.stem)
        if not match:
            continue
        version = _load(path).get("version", 1)
        assert version == int(match.group("version")), (
            f"{path.name} 的文件名版本号 _v{match.group('version')} 与 version: {version} 不一致"
        )


def test_count_suffix_only_on_frozen_presets():
    """条目数不进文件名（会演进的预设删条目就漂）；只留给冻结预设。"""
    for path in _action_presets():
        match = COUNT_SUFFIX_RE.match(path.stem)
        if not match:
            continue
        # _v2 这类版本后缀不算条目数
        if VERSION_SUFFIX_RE.match(path.stem):
            continue
        assert path.stem in FROZEN_COUNT_SUFFIX_PRESETS, (
            f"{path.name} 带条目数后缀，但会演进的预设不该把条数写进文件名"
            "（条数由 list_presets() 现算）"
        )


def test_presets_top_level_has_only_readme():
    """顶层只允许 README.md 和子目录。"""
    allowed_files = {"README.md"}
    stray = {
        p.name for p in PRESETS_ROOT.iterdir()
        if p.is_file() and p.name not in allowed_files and not p.name.startswith(".")
    }
    assert stray == set(), f"presets/ 顶层出现未登记的文件：{sorted(stray)}"


def test_no_junk_files():
    """没有 .bak / ~ / .orig / .tmp 之类的垃圾文件。"""
    junk = []
    for path in PRESETS_ROOT.rglob("*"):
        if not path.is_file():
            continue
        name = path.name
        if name.startswith("."):  # .DS_Store 等系统文件（已 gitignore）
            continue
        if name.endswith((".bak", "~", ".orig", ".tmp", ".rej")):
            junk.append(str(path.relative_to(PRESETS_ROOT)))
    assert junk == [], f"presets/ 里有垃圾文件：{junk}"


# ---------- 引用完整性 ----------

def test_subject_attr_points_to_existing_subject():
    """subject: 归属必须指向真实存在的 subject 档案。"""
    for path in _action_presets():
        subject = _load(path).get("subject")
        if not subject:
            continue
        target = PRESETS_ROOT / "subjects" / f"{subject}.yaml"
        assert target.exists(), (
            f"{path.name} 的 subject={subject!r} 指向不存在的档案 {target.relative_to(PRESETS_ROOT)}"
        )


def test_subject_preset_ids_match_filename():
    """subjects/<id>.yaml 的 id 字段等于文件名 stem。"""
    for path in _yaml_files("subjects"):
        subject_id = _load(path).get("id")
        assert subject_id == path.stem, (
            f"{path.name} 的 id={subject_id!r} != 文件名 stem {path.stem!r}"
        )


def test_recipe_doc_points_to_existing_md():
    """配方的 doc: 必须有同名 .md 兜底。"""
    for path in _yaml_files("recipes"):
        doc = _load(path).get("doc")
        assert doc, f"{path.name} 缺 doc:（配方文档）"
        assert (path.parent / doc).exists(), (
            f"{path.name} 的 doc={doc!r} 指向的文件不存在"
        )


def test_recipe_yaml_md_paired():
    """配方 yaml / md 成对：多出来的 md 也是错。"""
    for path in _yaml_files("recipes"):
        assert path.with_suffix(".md").exists(), f"{path.name} 缺少同名 .md"


def test_wildcard_refs_resolve():
    """backgrounds_ref / lighting_ref 指向的池文件必须存在（相对 presets 根）。"""
    for path in _action_presets():
        data = _load(path)
        for key in ("backgrounds_ref", "lighting_ref"):
            ref = data.get(key)
            if not ref:
                continue
            assert (PRESETS_ROOT / ref).exists(), (
                f"{path.name} 的 {key}={ref!r} 指向的池文件不存在"
            )


def test_wildcard_files_have_items_list():
    """通配符池只有 items: [..]。"""
    for path in _yaml_files("wildcards"):
        data = _load(path)
        items = data.get("items")
        assert isinstance(items, list) and items, f"{path.name} 缺少 items: [..]"
        assert all(isinstance(v, str) for v in items), f"{path.name} 的 items 必须是字符串列表"


def test_recipe_preset_refs_resolve():
    """配方里引用的预设名必须真实存在（按名引用，不是按路径）。

    配方的一步可以同时挂动作预设和服装预设（笛卡尔积），两个族都要认。
    """
    from charvar import preset_loader
    from charvar import outfit

    known = {_load(p).get("name") for p in _action_presets()}
    outfit_names = {o["name"] for o in outfit.list_outfit_presets()}
    for path in _yaml_files("recipes"):
        data = _load(path)
        for step in data.get("steps") or []:
            for name in step.get("presets") or []:
                assert name in known or name in outfit_names, (
                    f"{path.name} 的步骤引用了不存在的预设 {name!r}"
                )
                if name in known:
                    assert preset_loader.resolve(name).exists()
