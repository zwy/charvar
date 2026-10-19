# AGENTS.md

本仓库给编码代理的工作约定。

## 启动 / 重启 / 停止

- 启动：`./scripts/start.sh`（后台，端口 `CHARVAR_WEB_PORT`，默认 8710，仅监听 127.0.0.1）
- 停止：`./scripts/stop.sh`（默认 8710；只杀 `python3 -m web.app` 的本仓库进程）
- 没有热重载：改了 `charvar/**`、`web/app.py` 或直接改环境变量后必须先停再起；只改 `web/static/**` 刷新页面即可。
- 后台启动使用 screen 会话 `charvar-8710`；stop.sh 优先读 `.charvar-web-8710.pid`，也支持按端口 + 本仓库 cwd 匹配修复前的旧进程。
- 手动验证：`curl -s --noproxy '*' http://127.0.0.1:8710/api/version`

## 测试

- `python3 -m pytest -q` 必须全绿再交付。
- 与原工作流逐字相关的红线：`presets/q21_default_30.yaml` 的 30 条 variants 文本不许改动。第三方工作流 JSON 已移出仓库，tests/test_presets.py 改用 sha256 文本基准锁定（不得把原工作流 JSON 加回仓库根）。

## 实现红线

- 随机一律从 `charvar.wildcard.seed_for(seed, key)` 派生，禁止全局 `random` / `os.urandom` 参与提示词内容。
- 模板渲染只允许 `charvar.composer.render`（缺失占位符保留原样并记 warning），禁止 eval / Jinja 执行任意代码。
- ComfyUI 节点 id 一律来自用户提供的 mapping.json，不许硬编码。
- ComfyUI 请求必须 bypass 代理（exporter 里 httpx trust_env=False）：本机 shell 的 ALL_PROXY 会劫持内网地址。
- `.env`（COMFYUI_HOST/PORT 等）不进 git；改 .env 后要重启 web 服务。
