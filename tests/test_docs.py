"""仓库文档的链接与指针校验。

保护两类容易悄悄坏掉的东西：
  - AGENTS.md / README.md 里指向 `docs/*.md` 的指针（文件被移走或改名）
  - markdown 里的相对链接（`[文字](../path/file.md)`）能解析到真实文件

文档瘦身时最容易犯的错就是「把细节挪进 docs/ 却忘了改引用」，
或者「删了一个文件但别处还在链它」—— 这条测试专门拦这个。
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# AGENTS.md 的体量预算（UTF-8 字节）。它是**密度护栏**，不是运行时硬截断：
# 超了说明细节该往 docs/ 挪，而不是继续往这份「当前态契约」里塞。
AGENTS_BUDGET_BYTES = 36864

LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
DOC_REF_RE = re.compile(r"`(docs/[^`]+?\.(?:md|txt|json))`")
# 反引号里也可能是裸路径：docs/配方系统.md
BACKTICK_PATH_RE = re.compile(r"`([\w一-鿿-]+/[\w一-鿿./-]*\.(?:md|py|json|yaml))`")

DOCS = [
    ROOT / "AGENTS.md",
    ROOT / "README.md",
    *(sorted((ROOT / "docs").glob("*.md")) if (ROOT / "docs").is_dir() else []),
]


def test_docs_exist():
    assert (ROOT / "docs").is_dir(), "仓库里没有 docs/ 目录"


def test_doc_pointers_resolve():
    """AGENTS.md / README.md 里提到的 docs/ 文件必须真的存在。"""
    missing = []
    for path in DOCS:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for ref in set(DOC_REF_RE.findall(text)):
            if not (ROOT / ref).exists():
                missing.append(f"{path.relative_to(ROOT)} -> {ref}")
    assert not missing, "文档里指向了不存在的文件：" + "; ".join(missing)


def test_markdown_links_resolve():
    """markdown 相对链接要能解析到真实文件。"""
    broken = []
    for path in DOCS:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for target in LINK_RE.findall(text):
            if target.startswith(("http://", "https://", "#", "mailto:")):
                continue
            target = target.split("#")[0].strip()
            if not target:
                continue
            if not (path.parent / target).resolve().exists():
                broken.append(f"{path.relative_to(ROOT)} -> {target}")
    assert not broken, "markdown 链接断了：" + "; ".join(broken)


def test_backtick_paths_resolve():
    """反引号里写的仓库内路径要能解析到真实文件。"""
    missing = []
    for path in DOCS:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for ref in set(BACKTICK_PATH_RE.findall(text)):
            # data/ 与 out/ 是运行时产物（.gitignore 里），仓库里本来就没有，
            # 文档引用它们是合法的（说明产物落在哪），不校验存在性。
            if not ref.startswith(("presets/", "skills/", "web/", "charvar/",
                                   "tests/", "scripts/", "docs/", "fonts/")):
                continue
            if not (ROOT / ref).exists():
                missing.append(f"{path.relative_to(ROOT)} -> {ref}")
    assert not missing, "文档里引用了不存在的路径：" + "; ".join(missing)


def test_agents_md_fits_context_budget():
    """AGENTS.md 必须在 agent 指令预算内 —— 超了会被静默截断。

    截断是从文件**末尾**开始的，也就是最容易被忽略的「实现红线」章节会先消失。
    """
    size = (ROOT / "AGENTS.md").stat().st_size
    assert size <= AGENTS_BUDGET_BYTES, (
        f"AGENTS.md {size} 字节，超过 {AGENTS_BUDGET_BYTES} 字节预算，"
        "运行时会被截断（末尾内容 agent 读不到）。"
        "把细节挪进 docs/，别往 AGENTS.md 加。"
    )
