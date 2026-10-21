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
- OpenRouter 生图（`charvar/openrouter_image.py`）同样 bypass 代理；画幅必须走 `size` 显式像素（provider 的 `aspect_ratio` 枚举不含 21:9，会 400）；seed 必须收敛到 ≤ 2^53-1（charvar 的 subseed 是 64 位 sha256，不收敛直接 400）。
- OpenRouter 生图的参数以 `data/openrouter/api-response.json` 里每个模型的 `supported_parameters` 为准（`resolution` / `quality` / `aspect_ratio` 的枚举值都从那里取，模型没声明就不发）。清单是目录级声明、未必准（muse-image 声明为空但实际接受 `size`；grok 声明 `quality` 但 provider 拒收），所以 provider 拒收参数时自动降级重试并把结果记进 `data/openrouter/capabilities.json`。
- 参考图统一走 `charvar/reference_images.py` 预处理（长边 1536 / JPEG q88 / 透明白底合成），ComfyUI 上传与 OpenRouter 发送共用同一份缓存 —— 别再写新的压缩逻辑。
- `web/static/style.css` 里 `label { display: flex }` 会盖掉浏览器默认的 `[hidden] { display: none }`，所以有 `[hidden] { display: none !important }` 这条兜底。新增按条件显隐的控件时，`<select>` 自身带 `hidden` 的话，JS 要同时清掉 select 和它外层 label 上的 `hidden`（`applyOpenRouterCapabilities` 就是这么做的）。
- `.env`（COMFYUI_HOST/PORT 等）不进 git；改 .env 后要重启 web 服务。
- 主体档案的锁定项必须是英文：`style_lock` / `identity_lock` / `outfit_lock` 每条会被拼成一句 `Maintain ...` 进提示词，中文会中英夹杂。
- 主体档案的 Markdown 模板上限 24,000 字符（LLM 输入截断阈值，`charvar.llm_prompts._TEMPLATE_MAX_CHARS`）；超长模板尾部会被静默截掉，别指望模型看到。

## 导入主体档案（外部 Markdown → 系统）

1. 在 `presets/subjects/<id>.yaml` 写档案（id/名/锁定项/extra），随仓库走、可复现。
2. 建档：`python3 -m charvar.cli subject add presets/subjects/<id>.yaml`（同 id 覆盖加 `--overwrite`）。
3. 模板全文存 `subject_templates`：`PUT /subjects/<id>/template`（body `{"template_md": "..."}`），或网页「主体档案 → 从 .md 文件载入」。
4. 锁定项与模板全文逐字保留原文，不做改写或摘要。
5. 导入后跑一次 `python3 -m pytest -q`，并用 `/subjects/<id>/images/<kind>/suggest-prompt` 验一次 LLM 起草能跑通。
