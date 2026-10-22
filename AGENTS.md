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
- **网页入口**：顶栏「临时生图」页签（`web/static/adhoc.js`），后端 `/adhoc/*`（`charvar/api.py`）。
  走的是**同一个** `charvar/adhoc.py` —— 网页与 CLI 是同一套参数语义、同一套落盘、同一份 manifest。
  改动 `charvar/adhoc.py` 的参数名或输出格式时，CLI、网页、skill 三处都要同步。
- 网页的参考图只认两种来源：上传暂存（`/adhoc/references`）或主体档案里已就绪的档案图。
  **不接受任意路径** —— 那是把文件读权限开给了浏览器。
- 这是给本项目之外的项目和其他 agent 用的；改动它要考虑外部调用方依赖的参数名与输出格式。
- `skills/charvar-skill/` 是配套的 Claude Code skill，改 CLI 参数时要同步改 skill 里的命令示例。
  要「全局可用」得装到**两处**（`scripts/install_cli.sh` 负责，幂等，`start.sh` 会调）：
  - `~/.claude/skills/charvar-skill`（软链到仓库）→ Claude Code CLI + Claude Desktop 的 **Code 标签页**；
  - Claude Desktop 的**聊天面**（3p 部署的 local agent mode）**不读 `~/.claude/skills/`** —— 它用自己的一套：
    `<Application Support>/Claude*/local-agent-mode-sessions/skills-plugin/<workspace>/<account>/`，
    技能 = `skills/` 下的**真实目录** + `manifest.json` 里一条 `creatorType: "user"` 记录
    （`ensurePluginStructure` 会清掉 app 没登记过的东西，所以必须同时写 manifest）。
- skill 里调 CLI 一律用绝对路径 `~/.local/bin/charvar` —— Desktop 聊天面的 shell 不保证把
  `~/.local/bin` 加进 PATH。

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
- 主体档案的锁定项中英文皆可（2026-10-22 放开，此前只许英文）：`style_lock` / `identity_lock` / `outfit_lock`
  每条会被拼成一句 `Maintain ...` 进提示词——classic 模式下**原样进提示词**（中文条目就是中文进图，
  Qwen-Image / OpenRouter 这类双语或 LLM 系模型没问题）；LLM 模式下锁定项只作为 LLM 输入、由
  LLM 重写成英文，语言无所谓。目标后端要纯英文提示词时（SD 类老模型）建议仍写英文。
  导入/校验工具不得把非 ASCII 锁定项当错误。
- 主体档案的 Markdown 模板上限 24,000 字符（LLM 输入截断阈值，`charvar.llm_prompts._TEMPLATE_MAX_CHARS`）；超长模板尾部会被静默截掉，别指望模型看到。
- 临时生图（`charvar/adhoc.py`）的红线：参考图槽位名从 mapping 读（`image`/`image2`/…），不许写死节点 id；ComfyUI 上传的 remote_name 必须带唯一前缀（`upload_image` 发 `overwrite: true`，同名会互相覆盖 → 队列里的旧 prompt 取到新图）；`--out` 指定的文件后缀与内容必须一致（不一致时转格式，别让后缀骗人）；adhoc 不读 `settings.image_sizes`（那是主体档案槽位的尺寸）；不引入随机源（seed 由调用方显式给）。
- 临时生图的 run_id 直接拼进文件路径（`/adhoc/{run_id}/image` 直接 serve 磁盘文件）：
  `charvar.adhoc.run_dir()` 用自己的正则校验（`^adhoc_[A-Za-z0-9_.-]+$` 且不含 `..`），
  比 `assets.safe_run_id` 严 —— 后者放行 `..`。**别退回用 safe_run_id**。
- 上传的参考图按**实际图片格式**判后缀（用 PIL 读，不信文件名），落 `data/generated/adhoc/_refs/`；
  出图成功后自动清掉，失败时保留以便重试。
- **2K 必须用 Qwen-Image-2.1 官方像素表**（`charvar/exporters/comfyui.py` 的 `_QWEN_2K_SIZES`：
  1:1=2048×2048、4:3=2400×1792、3:4=1792×2400、3:2=2528×1696、2:3=1696×2528、
  16:9=2752×1536、9:16=1536×2752）。模型原生是**恒定 ~4.2MP 的 2K**（按面积定义，不是长边）——
  早期按「长边 2048」归一算出的 2K 只有原生面积的 56%（16:9 才 2048×1152），
  模型被迫上采样 → 图糊。**别退回长边归一**。前端 `web/static/app.js` 的 `QWEN_2K_SIZES`
  是同一张表，改一处要同步另一处，否则预览像素和实际不符。
- **4K 档已下线**（`1k` / `2k` 两档）。官方明说远超 2K 训练尺寸会丢提示词遵循度；
  实测 4096×4096 耗时 75s（2K 只要 10s）且细节崩坏。`settings_service.ImageSize`
  有 validator 把存量设置里的 "4k" 降级到 "1k"（别删 —— 存量 DB 里可能还有）。
- 2K 官方表**只给 ComfyUI 通路**（跑的是 Qwen-Image-2.1）。OpenRouter 通路
  （muse/grok/gpt-image）传 `use_model_native=False` 走通用长边归一 —— 那边的模型
  不认 Qwen 的尺寸表。

## 导入主体档案（外部 Markdown → 系统）

1. 在 `presets/subjects/<id>.yaml` 写档案（id/名/锁定项/extra），随仓库走、可复现。
2. 建档：`python3 -m charvar.cli subject add presets/subjects/<id>.yaml`（同 id 覆盖加 `--overwrite`）。
3. 模板全文存 `subject_templates`：`PUT /subjects/<id>/template`（body `{"template_md": "..."}`），或网页「主体档案 → 从 .md 文件载入」。
4. 锁定项与模板全文逐字保留原文，不做改写或摘要。
5. 导入后跑一次 `python3 -m pytest -q`，并用 `/subjects/<id>/images/<kind>/suggest-prompt` 验一次 LLM 起草能跑通。
