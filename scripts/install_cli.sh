#!/usr/bin/env bash
# 安装 charvar 全局 CLI + 仓库内的 skills（幂等，可重复执行）
#
# - ~/.local/bin/charvar          → launcher 脚本，指向本仓库的 .venv
# - ~/.claude/skills/<name>       → 软链到仓库里的 skill（其他项目也能用）
# - <repo>/.claude/skills/<name>  → 软链（本项目内 Claude Code 自动发现）
# - Claude Desktop 聊天面的本地 agent 技能目录 → 复制一份并登记进它的 manifest
#
# 仓库里要装的 skill 列在 SKILL_NAMES（目录名 = skill 名 = 软链名）：
#   charvar-skill   —— 按提示词实际出图（ComfyUI / OpenRouter / 即梦）
#   character-forge —— 角色开发与提示词资产化（不出图，出提示词与资产文档）
#
# 只做 macOS/Linux；Windows 版本另做。
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"

BIN_DIR="${CHARVAR_BIN_DIR:-$HOME/.local/bin}"
SKILLS_DIR="${CHARVAR_SKILLS_DIR:-$HOME/.claude/skills}"

SKILL_NAMES=(charvar-skill character-forge)

# 被改名/合并取代的旧 skill 名：装完新 skill 后把旧副本退役，
# 否则新旧两个 skill 描述几乎相同，会在同一个请求上重复触发。
RETIRED_SKILL_NAMES=(character-development)
RETIRED_CSV="$(IFS=,; echo "${RETIRED_SKILL_NAMES[*]}")"

mkdir -p "$BIN_DIR"

# launcher：固定指向本仓库的解释器 + PYTHONPATH，随仓库改动自动生效
# 优先仓库 venv；全新克隆还没建 venv 时退回 PATH 里的 python3 —— 别让 CLI 直接不可用。
LAUNCHER="$BIN_DIR/charvar"
cat > "$LAUNCHER" <<EOF
#!/bin/sh
# charvar CLI launcher（由 scripts/install_cli.sh 生成，勿手改）
REPO="$ROOT"
export PYTHONPATH="\$REPO\${PYTHONPATH:+:\$PYTHONPATH}"
PY="\$REPO/.venv/bin/python"
[ -x "\$PY" ] || PY="python3"
exec "\$PY" -m charvar.cli "\$@"
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

SKILL_MSG=""
for name in "${SKILL_NAMES[@]}"; do
  skill_src="$ROOT/skills/$name"
  if [ -d "$skill_src" ]; then
    install_skill_link "$SKILLS_DIR/$name" "$skill_src"
    # 仓库内那份用相对路径：绝对路径软链跟着仓库走会指向别人的机器
    install_skill_link "$ROOT/.claude/skills/$name" "../../skills/$name"
    SKILL_MSG="$SKILL_MSG $SKILLS_DIR/$name"
  else
    echo "（跳过：仓库里没有 skills/$name）"
  fi
done

# 退役旧 skill 副本：只删 RETIRED_SKILL_NAMES 里点名的目录，不碰别的
for name in "${RETIRED_SKILL_NAMES[@]}"; do
  for target in "$SKILLS_DIR/$name" "$ROOT/.claude/skills/$name"; do
    if [ -L "$target" ] || [ -e "$target" ]; then
      rm -rf "$target"
      echo "已退役旧 skill：$target"
    fi
  done
done

# Claude Desktop 的聊天面（3p 部署的 local agent mode）不读 ~/.claude/skills ——
# 它用自己的技能目录：<Application Support>/Claude*/local-agent-mode-sessions/
# skills-plugin/<workspace>/<account>/{manifest.json, skills/}。
# 把同一个 skill 复制过去并在 manifest 里登记，桌面端聊天才能用。
sync_desktop_skills() {
  local plugin_dir
  plugin_dir="$(ls -d "$HOME"/Library/Application\ Support/Claude*/local-agent-mode-sessions/skills-plugin/*/* 2>/dev/null | head -1 || true)"
  if [ -z "$plugin_dir" ] || [ ! -d "$plugin_dir/skills" ]; then
    echo "（跳过：没找到 Claude Desktop 的本地 agent 技能目录）"
    return 0
  fi
  python3 - "$plugin_dir" "$ROOT/skills" "$RETIRED_CSV" "${SKILL_NAMES[@]}" <<'PY'
import datetime, json, pathlib, shutil, sys

plugin_dir = pathlib.Path(sys.argv[1])
skills_root = pathlib.Path(sys.argv[2])
retired = [n for n in sys.argv[3].split(",") if n]
names = sys.argv[4:]

manifest_path = plugin_dir / "manifest.json"
data = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {"skills": []}
entries = data.setdefault("skills", [])

# 退役旧 skill：删目录 + 删 manifest 登记（app 也会清掉没登记的东西，
# 但显式删掉才能立刻生效，避免新旧两个 skill 同时抢同一个请求）
for name in retired:
    dest = plugin_dir / "skills" / name
    if dest.is_symlink():
        dest.unlink()
    elif dest.exists():
        shutil.rmtree(dest)
    if any(s.get("name") == name for s in entries):
        entries = [s for s in entries if s.get("name") != name]
        print(f"已退役 Claude Desktop 旧 skill：{name}")

synced = 0
for name in names:
    skill_src = skills_root / name
    if not skill_src.is_dir():
        continue
    dest = plugin_dir / "skills" / name
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(skill_src, dest, ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"))

    description = ""
    frontmatter = (dest / "SKILL.md").read_text(encoding="utf-8")
    if frontmatter.startswith("---"):
        for line in frontmatter.split("---", 2)[1].splitlines():
            if line.startswith("description:"):
                description = line.split(":", 1)[1].strip().strip('"')
                break

    entry = {
        "skillId": name,
        "name": name,
        "description": description,
        "creatorType": "user",
        "syncManaged": False,
        "updatedAt": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "enabled": True,
    }
    entries = [s for s in entries if s.get("name") != entry["name"]] + [entry]
    data["skills"] = entries
    synced += 1
    print(f"已同步到 Claude Desktop：{dest}")

if not synced and not retired:
    print("（跳过：没有可同步的 skill）")
    raise SystemExit(0)

data["skills"] = entries
data["lastUpdated"] = int(datetime.datetime.now().timestamp() * 1000)
tmp = manifest_path.with_suffix(".json.tmp")
tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
tmp.replace(manifest_path)
PY
}

sync_desktop_skills

echo "charvar CLI 已安装：$LAUNCHER"
echo "skills：$SKILL_MSG"
echo "提示：若命令找不到，执行 source ~/.zshrc 或新开一个终端。"
