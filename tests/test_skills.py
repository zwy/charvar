"""skills/ 下各 skill 包的静态校验（仓库级门禁）。

覆盖行业通用 skill 规范里「机器能查」的那部分：
  - frontmatter 有 name/description；name 与目录名一致（目录名 == 安装键）
  - name 合规（小写连字符）、description 写清「做什么 + 什么时候用」
  - 兄弟 skill 的 description 不近似重复（否则会抢同一个请求）
  - manifest.json 存在；版本与 SKILL.md / README.md 同步
  - manifest 的文件清单与磁盘实际文件一致
  - SKILL.md 体积受控（渐进披露），且不含写死的绝对路径
  - skill 自带的静态检查脚本能跑通
  - install_cli.sh 退役的旧 skill 名不再以目录形式存在

语义质量（设定板四描绘是否真是同一角色、证据措辞是否夸大、双模式是否混用）
机器查不了，仍是人工核对项 —— 见 docs/skill 开发规范.md。
"""
import difflib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = ROOT / "skills"
INSTALL_SCRIPT = ROOT / "scripts" / "install_cli.sh"

NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
VERSION_RE = re.compile(r"^版本：([\d.]+)", re.M)
FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.S)

# SKILL.md 是渐进披露的入口，超长说明细节该外置到 references/
MAX_SKILL_LINES = 500
MIN_DESCRIPTION_CHARS = 40


def discover_skills():
    if not SKILLS_DIR.is_dir():
        return []
    return sorted(p for p in SKILLS_DIR.iterdir() if p.is_dir() and (p / "SKILL.md").is_file())


def parse_frontmatter(text):
    """解析 SKILL.md 顶部的 YAML frontmatter。

    刻意不用 yaml.safe_load：这些 description 是长中文 + 内嵌引号的纯标量，
    PyYAML 的 plain scalar 规则在这种内容上容易出意外；这里跟
    scripts/install_cli.sh 的解析方式保持一致（行级 key: value）。
    """
    match = FRONTMATTER_RE.match(text)
    if not match:
        return None
    fields = {}
    for line in match.group(1).splitlines():
        if not line.strip() or line.startswith((" ", "\t", "#")):
            continue
        key, _, value = line.partition(":")
        if not _:
            continue
        fields[key.strip()] = value.strip().strip('"').strip("'")
    return fields


def skill_ids():
    return [p.name for p in discover_skills()]


def test_skills_found():
    assert SKILLS_DIR.is_dir(), "仓库里没有 skills/ 目录"
    assert discover_skills(), "skills/ 下没有找到任何含 SKILL.md 的 skill"


@pytest.mark.parametrize("skill_dir", discover_skills(), ids=skill_ids())
class TestSkillPackage:
    def test_frontmatter_present(self, skill_dir):
        text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        fields = parse_frontmatter(text)
        assert fields is not None, f"{skill_dir.name}/SKILL.md 缺少 YAML frontmatter"

    def test_name_matches_directory(self, skill_dir):
        fields = parse_frontmatter((skill_dir / "SKILL.md").read_text(encoding="utf-8"))
        assert fields.get("name"), "frontmatter 缺 name"
        assert fields["name"] == skill_dir.name, (
            f"name({fields['name']}) 必须等于目录名({skill_dir.name}) —— "
            "目录名就是安装软链名，不一致会装到别的 key 上"
        )

    def test_name_format(self, skill_dir):
        fields = parse_frontmatter((skill_dir / "SKILL.md").read_text(encoding="utf-8"))
        name = fields["name"]
        assert NAME_RE.match(name), f"name({name}) 必须是小写连字符格式"
        assert len(name) <= 64, f"name({name}) 超长"

    def test_description_is_actionable(self, skill_dir):
        """description 是唯一常驻上下文 —— 它决定 skill 何时被加载。

        写「什么时候用」比写「是什么」重要：宿主靠它做路由。
        """
        fields = parse_frontmatter((skill_dir / "SKILL.md").read_text(encoding="utf-8"))
        desc = fields.get("description", "")
        assert len(desc) >= MIN_DESCRIPTION_CHARS, "description 太短，说不清这个 skill 干什么"
        assert len(desc) <= 1024, "description 超长，每次都要进上下文"
        assert "。" in desc or "." in desc, "description 应该是完整句子"

    def test_skill_md_is_lean(self, skill_dir):
        """渐进披露：SKILL.md 只放入口，细节外置到 references/。"""
        lines = (skill_dir / "SKILL.md").read_text(encoding="utf-8").splitlines()
        assert len(lines) <= MAX_SKILL_LINES, (
            f"SKILL.md {len(lines)} 行，超过 {MAX_SKILL_LINES} 行上限；"
            "细节该拆到 references/ 里按需加载"
        )

    def test_no_hardcoded_absolute_paths(self, skill_dir):
        """换台机器还要能用 —— 不能写死某人的家目录。"""
        for path in skill_dir.rglob("*.md"):
            if "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            assert "/Users/" not in text, f"{path.relative_to(ROOT)} 写死了 /Users/ 绝对路径"

    def test_manifest_exists_and_lists_real_files(self, skill_dir):
        manifest_path = skill_dir / "manifest.json"
        assert manifest_path.is_file(), f"{skill_dir.name} 缺 manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest.get("name") == skill_dir.name
        assert manifest.get("entrypoint") == "SKILL.md"

        listed = set(manifest.get("files", {}))
        actual = {
            str(p.relative_to(skill_dir))
            for p in skill_dir.rglob("*")
            if p.is_file()
            and "__pycache__" not in p.parts
            and p.suffix in (".md", ".py")
        }
        actual.add("manifest.json")
        assert actual == listed, (
            f"{skill_dir.name} 的 manifest 文件清单与磁盘不一致；"
            f"多={sorted(listed - actual)} 少={sorted(actual - listed)}"
        )

    def test_version_in_sync(self, skill_dir):
        """版本三处同步：manifest（权威）= SKILL.md = README.md（有的话）。"""
        manifest = json.loads((skill_dir / "manifest.json").read_text(encoding="utf-8"))
        version = manifest.get("version")
        assert version, "manifest.json 缺 version"

        skill_md = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        found = VERSION_RE.search(skill_md)
        assert found, "SKILL.md 缺「版本：X.Y.Z」行"
        assert found.group(1) == version, (
            f"版本不同步：SKILL.md={found.group(1)} manifest={version}"
        )

        readme = skill_dir / "README.md"
        if readme.is_file():
            found = VERSION_RE.search(readme.read_text(encoding="utf-8"))
            assert found, "README.md 缺「版本：X.Y.Z」行"
            assert found.group(1) == version, (
                f"版本不同步：README.md={found.group(1)} manifest={version}"
            )

    def test_own_checks_pass(self, skill_dir):
        """skill 自带的静态检查脚本必须能跑通。"""
        checker = skill_dir / "tests" / "run_checks.py"
        if not checker.is_file():
            pytest.skip(f"{skill_dir.name} 没有自带 checks 脚本")
        proc = subprocess.run(
            [sys.executable, str(checker)],
            capture_output=True, text=True, cwd=skill_dir,
        )
        assert proc.returncode == 0, (
            f"{skill_dir.name} 的自带检查未通过：\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}"
        )


def test_sibling_descriptions_are_distinct():
    """兄弟 skill 的 description 不能近似重复。

    描述几乎一样时，宿主路由会两个都命中，用户看到两份一样的技能。
    """
    skills = discover_skills()
    descriptions = {}
    for skill_dir in skills:
        fields = parse_frontmatter((skill_dir / "SKILL.md").read_text(encoding="utf-8"))
        descriptions[skill_dir.name] = fields.get("description", "")

    for i, (name_a, desc_a) in enumerate(descriptions.items()):
        for name_b, desc_b in list(descriptions.items())[i + 1:]:
            ratio = difflib.SequenceMatcher(None, desc_a, desc_b).ratio()
            assert ratio < 0.85, (
                f"{name_a} 与 {name_b} 的 description 相似度 {ratio:.2f} 过高，"
                "两者会抢同一个请求；请把各自的触发条件写清楚"
            )


def test_retired_skills_are_gone():
    """install_cli.sh 里退役的旧 skill 名，不能还以目录形式留在 skills/ 下。"""
    if not INSTALL_SCRIPT.is_file():
        pytest.skip("没有 install_cli.sh")
    text = INSTALL_SCRIPT.read_text(encoding="utf-8")
    match = re.search(r"RETIRED_SKILL_NAMES=\(([^)]*)\)", text)
    if not match:
        pytest.skip("install_cli.sh 没有 RETIRED_SKILL_NAMES")
    retired = [n.strip() for n in match.group(1).split() if n.strip()]
    for name in retired:
        assert not (SKILLS_DIR / name).exists(), (
            f"{name} 已登记退役，但 skills/{name} 还在 —— "
            "要么删掉旧目录，要么从 RETIRED_SKILL_NAMES 里去掉"
        )


def test_install_script_covers_every_skill():
    """仓库里每个 skill 都要在 install_cli.sh 的 SKILL_NAMES 里登记。"""
    if not INSTALL_SCRIPT.is_file():
        pytest.skip("没有 install_cli.sh")
    text = INSTALL_SCRIPT.read_text(encoding="utf-8")
    match = re.search(r"SKILL_NAMES=\(([^)]*)\)", text)
    assert match, "install_cli.sh 没有 SKILL_NAMES 数组"
    declared = {n.strip() for n in match.group(1).split() if n.strip()}
    for skill_dir in discover_skills():
        assert skill_dir.name in declared, (
            f"{skill_dir.name} 不在 install_cli.sh 的 SKILL_NAMES 里 —— "
            "它不会被装到任何 Agent"
        )


def _typer_commands(app) -> tuple[set[str], set[str]]:
    """枚举 typer app 的命令与子命令组（含嵌套子组）。

    返回 ``(可用命令, 组名)``：可用命令 = 顶层命令 + 「组 子命令」（递归到
    嵌套组，如 ``subject image import``）；组名单独返回，用来判断两段式写法
    （``charvar image models``）的第一个词到底是不是一个组。
    """
    commands: set[str] = set()
    groups: set[str] = set()

    def walk(typer_app, prefix: str) -> None:
        for cmd in typer_app.registered_commands:
            name = cmd.name or (cmd.callback.__name__.replace("_", "-") if cmd.callback else "")
            if name:
                commands.add(f"{prefix} {name}".strip())
        for group in typer_app.registered_groups:
            gname = group.name or ""
            if not gname:
                continue
            full = f"{prefix} {gname}".strip()
            groups.add(full)
            walk(group.typer_instance, full)

    walk(app, "")
    return commands, groups


def _strip_frontmatter(text: str) -> str:
    """去掉 YAML frontmatter —— description 是路由文案，不是命令文档。"""
    match = FRONTMATTER_RE.match(text)
    return text[match.end():] if match else text


def test_documented_commands_exist():
    """文档里写的每条 charvar 命令都必须真的存在。

    这条专门防「skill 教 agent 去跑一条不存在的命令」——那种错外部 agent
    照着跑只会拿到 "No such command"，而且没人会怀疑文档。
    """
    from charvar import cli

    commands, groups = _typer_commands(cli.app)
    assert commands, "没枚举到任何 CLI 命令"

    docs = [
        ROOT / "AGENTS.md",
        ROOT / "README.md",
        ROOT / "skills" / "charvar-skill" / "SKILL.md",
        *sorted((ROOT / "skills" / "charvar-skill" / "references").glob("*.md")),
    ]
    mentioned: set[str] = set()
    for path in docs:
        if not path.is_file():
            continue
        text = _strip_frontmatter(path.read_text(encoding="utf-8"))
        # 两段式：只有第一个词真是组名时才算一条命令（滤掉 "subject-profile tasks"）
        for group, sub in re.findall(r"\bcharvar ([a-z][a-z0-9-]*) ([a-z][a-z0-9-]*)", text):
            if group in groups:
                mentioned.add(f"{group} {sub}")
        # 单段式：顶层命令或组名
        for word in re.findall(r"\bcharvar ([a-z][a-z0-9-]*)\b", text):
            if word in commands or word in groups:
                mentioned.add(word)

    missing = sorted(m for m in mentioned if m not in commands and m not in groups)
    assert not missing, (
        f"文档里写了不存在的 CLI 命令：{missing}\n"
        f"（实际存在的：{sorted(commands)}）"
    )
