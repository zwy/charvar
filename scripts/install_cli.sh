#!/usr/bin/env bash
# 安装 charvar 全局 CLI + charvar-skill（幂等，可重复执行）
#
# - ~/.local/bin/charvar        → launcher 脚本，指向本仓库的 .venv
# - ~/.claude/skills/charvar-skill → 软链到仓库里的 skill（其他项目也能用）
# - <repo>/.claude/skills/charvar-skill → 软链（本项目内 Claude Code 自动发现）
# - Claude Desktop 聊天面的本地 agent 技能目录 → 复制一份并登记进它的 manifest
#
# 只做 macOS/Linux；Windows 版本另做。
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"

BIN_DIR="${CHARVAR_BIN_DIR:-$HOME/.local/bin}"
SKILLS_DIR="${CHARVAR_SKILLS_DIR:-$HOME/.claude/skills}"
SKILL_SRC="$ROOT/skills/charvar-skill"

mkdir -p "$BIN_DIR"

# launcher：固定指向本仓库的 venv + PYTHONPATH，随仓库改动自动生效
LAUNCHER="$BIN_DIR/charvar"
cat > "$LAUNCHER" <<EOF
#!/bin/sh
# charvar CLI launcher（由 scripts/install_cli.sh 生成，勿手改）
REPO="$ROOT"
export PYTHONPATH="\$REPO\${PYTHONPATH:+:\$PYTHONPATH}"
exec "\$REPO/.venv/bin/python" -m charvar.cli "\$@"
EOF
chmod +x "$LAUNCHER"

# skill 软链：全局（其他项目）+ 仓库内（本项目）
# 软链替换用 ln -sfn 原地覆盖：目标常是「指向本仓库目录的软链」，
# 对软链做 rm -rf 在部分 rm 实现/包装器（回收站 shim）下会误伤或卡住，
# ln -sfn 只换链接本身，不碰被指向的目录。
install_skill_link() {
  local target="$1"
  local source="$2"
  mkdir -p "$(dirname "$target")"
  if [ -L "$target" ]; then
    ln -sfn "$source" "$target"
  else
    rm -rf "$target"
    ln -s "$source" "$target"
  fi
}

if [ -d "$SKILL_SRC" ]; then
  install_skill_link "$SKILLS_DIR/charvar-skill" "$SKILL_SRC"
  # 仓库内那份用相对路径：绝对路径软链跟着仓库走会指向别人的机器
  install_skill_link "$ROOT/.claude/skills/charvar-skill" "../../skills/charvar-skill"
  SKILL_MSG="$SKILLS_DIR/charvar-skill"
else
  SKILL_MSG="（跳过：仓库里还没有 skills/charvar-skill）"
fi

# Claude Desktop 的聊天面（3p 部署的 local agent mode）不读 ~/.claude/skills ——
# 它用自己的技能目录：<Application Support>/Claude*/local-agent-mode-sessions/
# skills-plugin/<workspace>/<account>/{manifest.json, skills/}。
# 把同一个 skill 复制过去并在 manifest 里登记，桌面端聊天才能用。
sync_desktop_skill() {
  local plugin_dir
  plugin_dir="$(ls -d "$HOME"/Library/Application\ Support/Claude*/local-agent-mode-sessions/skills-plugin/*/* 2>/dev/null | head -1 || true)"
  if [ -z "$plugin_dir" ] || [ ! -d "$plugin_dir/skills" ]; then
    echo "（跳过：没找到 Claude Desktop 的本地 agent 技能目录）"
    return 0
  fi
  python3 - "$plugin_dir" "$SKILL_SRC" <<'PY'
import datetime, json, pathlib, shutil, sys

plugin_dir, skill_src = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
dest = plugin_dir / "skills" / skill_src.name
if dest.exists():
    shutil.rmtree(dest)
shutil.copytree(skill_src, dest, ignore=shutil.ignore_patterns(".git", "__pycache__"))

description = ""
frontmatter = (dest / "SKILL.md").read_text(encoding="utf-8")
if frontmatter.startswith("---"):
    for line in frontmatter.split("---", 2)[1].splitlines():
        if line.startswith("description:"):
            description = line.split(":", 1)[1].strip().strip('"')
            break

manifest_path = plugin_dir / "manifest.json"
data = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {"skills": []}
entry = {
    "skillId": skill_src.name,
    "name": skill_src.name,
    "description": description,
    "creatorType": "user",
    "syncManaged": False,
    "updatedAt": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
    "enabled": True,
}
data["skills"] = [s for s in data.get("skills", []) if s.get("name") != entry["name"]] + [entry]
data["lastUpdated"] = int(datetime.datetime.now().timestamp() * 1000)
tmp = manifest_path.with_suffix(".json.tmp")
tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
tmp.replace(manifest_path)
print(f"已同步到 Claude Desktop：{dest}")
PY
}

if [ -d "$SKILL_SRC" ]; then
  sync_desktop_skill
fi

echo "charvar CLI 已安装：$LAUNCHER"
echo "charvar-skill：$SKILL_MSG"
echo "提示：若命令找不到，执行 source ~/.zshrc 或新开一个终端。"
