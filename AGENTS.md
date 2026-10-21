# AGENTS.md

本仓库给编码代理的工作约定。

## 启动 / 重启 / 停止

- 启动：`./scripts/start.sh`（后台，端口 `CHARVAR_WEB_PORT`，默认 8710，仅监听 127.0.0.1）
- 停止：`./scripts/stop.sh`（默认 8710；只杀 `python3 -m web.app` 的本仓库进程）
- 没有热重载：改了 `charvar/**`、`web/app.py` 或直接改环境变量后必须先停再起；只改 `web/static/**` 刷新页面即可。
- 后台启动使用 screen 会话 `charvar-8710`；stop.sh 优先读 `.charvar-web-8710.pid`，也支持按端口 + 本仓库 cwd 匹配修复前的旧进程。
- 手动验证：`curl -s --noproxy '*' http://127.0.0.1:8710/api/version`
- `start.sh` 会顺带跑 `scripts/install_cli.sh`（幂等）：把 `charvar` 装到 `~/.local/bin/`、把 `skills/charvar-skill` 软链到 `~/.claude/skills/`。安装失败不阻断 web 启动。

## 临时生图（跨项目调用）

- `charvar/adhoc.py` 是「给一段提示词出一张图」的独立通路：不进主体档案 5 槽位、不跑预设批量、不落 runs 表。
- CLI：`charvar image comfyui|openrouter|models`（`--prompt`/`--prompt-file`/`--ref`/`--aspect`/`--seed`/`--out`/`--json`）。产物默认落 `data/generated/adhoc/{id}/`（图 + 缩略图 + manifest.json）。
- 这是给本项目之外的项目和其他 agent 用的；改动它要考虑外部调用方依赖的参数名与输出格式。
- `skills/charvar-skill/` 是配套的 Claude Code skill（装到 `~/.claude/skills/` 后跨项目可用）。改 CLI 参数时要同步改 skill 里的命令示例。

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
- 档案图片有两条生图通路：ComfyUI（`subject_images.generate_subject_image`）与 OpenRouter（`subject_images.generate_subject_image_openrouter`）。两者同语义：两步流程（character_sheet 先出，其余 4 种以它为参考图）、同槽位状态机、同落盘位置（`data/subjects/{id}/{kind}.{ext}`）。产出哪个后端记在 `subject_images.provider` 列（`comfyui` / `openrouter` / 空=上传）。
- `web/static/style.css` 里 `label { display: flex }` 会盖掉浏览器默认的 `[hidden] { display: none }`，所以有 `[hidden] { display: none !important }` 这条兜底。新增按条件显隐的控件时，`<select>` 自身带 `hidden` 的话，JS 要同时清掉 select 和它外层 label 上的 `hidden`（`applyOpenRouterCapabilities` 就是这么做的）。
- `.env`（COMFYUI_HOST/PORT 等）不进 git；改 .env 后要重启 web 服务。
- 主体档案的锁定项必须是英文：`style_lock` / `identity_lock` / `outfit_lock` 每条会被拼成一句 `Maintain ...` 进提示词，中文会中英夹杂。
- 主体档案的 Markdown 模板上限 24,000 字符（LLM 输入截断阈值，`charvar.llm_prompts._TEMPLATE_MAX_CHARS`）；超长模板尾部会被静默截掉，别指望模型看到。
- 临时生图（`charvar/adhoc.py`）的红线：参考图槽位名从 mapping 读（`image`/`image2`/…），不许写死节点 id；ComfyUI 上传的 remote_name 必须带唯一前缀（`upload_image` 发 `overwrite: true`，同名会互相覆盖 → 队列里的旧 prompt 取到新图）；`--out` 指定的文件后缀与内容必须一致（不一致时转格式，别让后缀骗人）；adhoc 不读 `settings.image_sizes`（那是主体档案槽位的尺寸）；不引入随机源（seed 由调用方显式给）。

## 导入主体档案（外部 Markdown → 系统）

1. 在 `presets/subjects/<id>.yaml` 写档案（id/名/锁定项/extra），随仓库走、可复现。
2. 建档：`python3 -m charvar.cli subject add presets/subjects/<id>.yaml`（同 id 覆盖加 `--overwrite`）。
3. 模板全文存 `subject_templates`：`PUT /subjects/<id>/template`（body `{"template_md": "..."}`），或网页「主体档案 → 从 .md 文件载入」。
4. 锁定项与模板全文逐字保留原文，不做改写或摘要。
5. 导入后跑一次 `python3 -m pytest -q`，并用 `/subjects/<id>/images/<kind>/suggest-prompt` 验一次 LLM 起草能跑通。
