#!/usr/bin/env bash
# 安装 charvar 全局 CLI + charvar-skill（幂等，可重复执行）
#
# - ~/.local/bin/charvar        → launcher 脚本，指向本仓库的 .venv
# - ~/.claude/skills/charvar-skill → 软链到仓库里的 skill（其他项目也能用）
# - <repo>/.claude/skills/charvar-skill → 软链（本项目内 Claude Code 自动发现）
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
install_skill_link() {
  local target="$1"
  local source="$2"
  mkdir -p "$(dirname "$target")"
  rm -rf "$target"
  ln -s "$source" "$target"
}

if [ -d "$SKILL_SRC" ]; then
  install_skill_link "$SKILLS_DIR/charvar-skill" "$SKILL_SRC"
  # 仓库内那份用相对路径：绝对路径软链跟着仓库走会指向别人的机器
  install_skill_link "$ROOT/.claude/skills/charvar-skill" "../../skills/charvar-skill"
  SKILL_MSG="$SKILLS_DIR/charvar-skill"
else
  SKILL_MSG="（跳过：仓库里还没有 skills/charvar-skill）"
fi

echo "charvar CLI 已安装：$LAUNCHER"
echo "charvar-skill：$SKILL_MSG"
echo "提示：若命令找不到，执行 source ~/.zshrc 或新开一个终端。"
