---
name: charvar-skill
description: Generate images with the charvar project's ComfyUI, OpenRouter, or Dreamina backends from any project. This skill should be used when the user asks to "用 comfyui 生图", "用 openrouter 生图", "用即梦生图", "charvar 帮我出图", "用 charvar 出张图", "generate an image with comfyui", "generate an image with openrouter", "generate an image with dreamina", or otherwise asks to produce an image via the charvar CLI; also for generating short videos via OpenRouter ("charvar 帮我出段视频", "用 openrouter 生视频", "generate a video with charvar"), for text-to-speech via OpenRouter ("charvar 帮我出语音", "把这段文字变成语音", "用 openrouter 生语音", "generate speech / TTS with charvar"), and for charvar subject-profile tasks such as "导入角色档案", "从模板生成主体档案" or using the charvar subject CLI, and for drafting the five subject-image slot prompts (character_sheet/headshot/front/side/behind) without an existing profile via "charvar subject prompt" ("起草主体图提示词", "AI 起草定妆图提示词", "帮我起草设定图提示词").
---

# charvar 生图

版本：1.6.0  
更新：2026-10-28

通过 charvar 项目的 CLI 出图。三条后端：**comfyui**（本机/局域网 ComfyUI）、
**openrouter**（OpenRouter 在线生图）和 **dreamina**（即梦官方 CLI，消耗即梦积分）。
用户会明确说用哪条 —— 别自己猜。

## 何时用

用户说「用 comfyui 帮我生图」「用 openrouter 生图」「用即梦生图」「charvar 出张图」，
或给出一段提示词并要求出图时。

## 前置

CLI 由 `scripts/install_cli.sh` 装到 `~/.local/bin/charvar`。

**用绝对路径 `~/.local/bin/charvar` 调，别用裸 `charvar`** —— 不同 agent 的 shell
未必把 `~/.local/bin` 加进 PATH（Claude Desktop 聊天面就不一定）。命令找不到时先跑：

```bash
/path/to/charvar/scripts/install_cli.sh
```

## 用法

**ComfyUI**（本机或局域网；无参考图走文生图 workflow，有参考图自动切图像编辑 workflow）：

```bash
~/.local/bin/charvar image comfyui --prompt "a red apple on a wooden table" --aspect 1:1 --out out.png
```

**OpenRouter**（在线）：

```bash
~/.local/bin/charvar image openrouter --prompt "a red apple on a wooden table" --aspect 1:1 --out out.png
```

**即梦 Dreamina**（官方 CLI；需本机已 `dreamina login`，每张消耗即梦积分/权益）：

```bash
~/.local/bin/charvar image dreamina --prompt "a red apple on a wooden table" --aspect 1:1 --out out.png
```

**带参考图**（最多 4 张，按顺序对应提示词里的 `<image1>`/`<image2>`）：

```bash
~/.local/bin/charvar image comfyui --prompt "same character, different pose" --ref ref1.png --ref ref2.png --out out.png
```

**长提示词**（别塞进 shell 历史）：

```bash
~/.local/bin/charvar image comfyui --prompt-file prompt.md --out out.png
# 或从 stdin
cat prompt.md | ~/.local/bin/charvar image comfyui --prompt-file - --out out.png
```

## 给用户交付产物

- `--out` 指定的文件就是最终产物（后缀按你给的写）。
- 不给 `--out` 时产物在 `data/generated/adhoc/{时间戳}/original/000000.png`，
  同目录有 `manifest.json` 记录完整请求与结果。
- 出图后把路径告诉用户，并在合适的时候展示图片。

## 常用参数

| 参数 | 说明 |
|---|---|
| `--prompt` / `--prompt-file` | 提示词，二选一；`--prompt-file -` 读 stdin |
| `--ref` | 参考图路径，可重复，最多 4 张 |
| `--aspect` | `1:1` `3:4` `16:9` `4:3` `9:16` `2:3` `3:2` `21:9`（默认 `3:4`） |
| `--resolution` | `1k` `2k`（默认 `1k`）或模型声明枚举值（512/1K/1.5K/2K/4K…；OpenRouter 通路）。即梦通路收 `1k`（仅 3.0/3.1 文生图）`2k` `4k` `1.5k`（5.0Pro），空 = 设置页/env 默认 2k。**2K 的像素表只对 ComfyUI 成立**（Qwen-Image-2.1 官方像素，面积恒定 ~4.2MP）；OpenRouter 通路走通用长边归一，别拿 Qwen 像素表去推算 |
| `--seed` | 采样种子，默认 `0`（固定复现）；要新图就换种子。**即梦通路没有 `--seed`**（即梦不暴露 seed） |
| `--out` | 产物路径 |
| `--json` | 结果以 JSON 输出（给脚本消费时用） |
| `--negative` | 负向提示词（仅 ComfyUI 支持） |
| `--workflow` | 指定 ComfyUI workflow 名或 JSON 路径 |
| `--model` | OpenRouter 模型 slug（默认按模式取 `OP_IMAGE_T2I_MODEL` / `OP_IMAGE_I2I_MODEL`）；即梦通路填 model_version（`3.0`–`5.0Pro`，默认 5.0，即 `DREAMINA_IMAGE_MODEL`） |
| `--quality` | OpenRouter 质量档（模型声明支持才生效） |

完整参数见 `references/cli.md`。

## 用户想在网页上出图

charvar 的网页顶栏也有「Playground」页签（`http://127.0.0.1:8710`），走的是同一条通路。
用户明确说「在网页上出图 / 用 charvar 的网页」时指那个 —— 告诉用户地址即可，别自己用 CLI 顶替。

## 视频（OpenRouter 通路）

给一段提示词出**一段视频**（异步作业；按秒计费 —— 提交成功就开始计费，长跑前提醒用户）。
默认模型 `heygen/heygen-video-1`（便宜档 480p $0.02/s），设置里可用 `OP_VIDEO_MODEL` 换：

```bash
~/.local/bin/charvar video openrouter --prompt "a young woman performs a smooth dance" --aspect 9:16 --resolution 480p --duration 5
```

带**首帧图**（图生视频，主体一致靠它；用单人全身照，别用四视图拼图）：

```bash
~/.local/bin/charvar video openrouter --prompt "the woman dances, spins, ends in a pose" --first-frame out/character_front.png --duration 8
```

- 时长/分辨率/画幅的合法值随所选模型目录变（超枚举提交前会报错）：先跑
  `~/.local/bin/charvar video models` 看清单，heygen 支持 5–15s、480p/768p/2K。
- 生成要 1–3 分钟，命令阻塞轮询（`--timeout` 默认 900s）；`--json` 给脚本消费。
- 网页顶栏「视频」页签是同一条通路（`http://127.0.0.1:8710`）。

## 语音（OpenRouter TTS 通路）

给一段文本出**一段音频**（同步：命令块到响应为止，一般几秒到几十秒）。
默认模型 `heygen/heygen-voice-1-turbo:free`（免费，实测一句 5 秒文本 ~15s 出片），
设置里可用 `OP_TTS_MODEL` 换（付费版 heygen-voice-1 $15/百万字符）：

```bash
~/.local/bin/charvar tts openrouter --prompt "欢迎收听本期的角色播报。" --voice sabrina --out out/narration.wav
```

- voice：`~/.local/bin/charvar tts models` 看清单（heygen 10 个默认 voice：
  nadine/tabitha/bram/gareth/noelle/meredith/sabrina/lucian/weston/vincent）。
  **voice 实测必填** —— 目录枚举非空的模型不给 `--voice` 时自动用枚举第一项兜底；
  fish/seed 这类没列枚举的不发送、交 provider 默认。
- 格式自动协商：输出一律是落地可播的 wav（裸 PCM 按响应头采样率封装）或 mp3；
  HeyGen 只出 pcm、MiniMax 只出 mp3（给错自动换档重试）。`--format mp3|pcm` 可显式指定。
- **HeyGen 系 5000 字符单次上限**（提交前拦截，超了会报错；其他模型上限未知交给 provider）。
- 语音落盘 `data/generated/adhoc_tts/<run_id>/original/000000.wav|mp3` + manifest.json，不落 runs 表。
- 网页顶栏「语音」页签是同一条通路（`http://127.0.0.1:8710`），可直接试放/下载。
- 声音克隆（参考音频音色复刻）暂未接入；需要时先在网页/CLI 里用默认 voice。

## 主体档案：从模板导入（subject derive / add / verify）

手里只有「角色全场景通用提示词模板」.md、要建主体档案时：

```bash
~/.local/bin/charvar subject derive <模板.md> --id <slug>
# 默认只写 presets/subjects/<id>.yaml（带出处注释头），不落库；--apply 才入库
# --apply 会把模板全文一并入库（档案 subjects + 模板 subject_templates 两张表都写）
# 模板含多版本时会列候选并拒绝 → 用 --version <关键词> 指定抽哪个
# 锁定项默认从模板原文摘录（防幻觉核对）；--lang en 才翻译成英文
```

已有 YAML 档案要建档或改档时：

```bash
~/.local/bin/charvar subject add presets/subjects/<slug>.yaml --overwrite
# 档案与**同目录同名 <slug>.md 模板一起入库**；--template <路径> 可指定别的模板
# 找不到模板会告警（只导档案）；--json 给脚本消费
```

⚠️ **只改仓库里的 yaml/md 而不入库，网页与出图仍读 DB 旧档案**（DB 是运行时真源）。
改完仓库文件必须 `subject add` 回灌，档案与模板两边才算同步。

已入库档案 ↔ 模板的审计（漏抽/矛盾/违例）：

```bash
~/.local/bin/charvar subject verify <id> --json
```

exit 0 = 通过，exit 1 = 有问题单。完整参数与 `--json` 字段见 `references/cli.md`。

### 交付包图片导入（槽位图 → 运行时）

交付包目录（`final/<id>/`）里的槽位图，一条命令装进运行时：**落盘 + 生成缩略图 + 建槽位行**。

```bash
~/.local/bin/charvar subject image import <目录> --subject <id>
# 目录导入缺省用目录名当主体 id；单文件用 --kind <kind> 指定槽位
# --json：{"subject_id","imported":[{"kind","source_file","file_path","thumb"}],"skipped":[...]}
```

也可在 `subject add` 时一并装图：`~/.local/bin/charvar subject add <yaml> --overwrite --images <目录>`。

⚠️ **手动把 PNG 拷进 `data/subjects/<id>/` 不会建槽位行** —— 网页仍显示空槽。
缩略图缺失会在读取时自动补，但槽位行只能由导入命令或网页上传产生。

⚠️ **`verify` 是 LLM 判定，不是确定性检查**：同一个档案连跑几次，报出的条目不一样，
一轮「通过」不等于档案完整。正确做法是跑多次 → 汇总各轮条目去重 → 逐条对照模板补全 → 再跑。

⚠️ **`derive` 的抽取同样非确定**：它按「识别锚点表」＋「固定前缀」两节把锚点分配进锁项，
同一份模板连跑几次，**同一行锚点表可能这次进 `outfit_lock`、下次被判「已在别处覆盖」而漏掉**，
审计随即报 `[omission]`，且 derive 失败时不写任何文件。
实测连续 5 次分别在「背面补全」「配饰无」「金属拉链」上摇摆。
**连续失败不要反复重跑**（每次要几分钟 LLM 调用）：核对锚点表里那几行是不是与固定前缀
「说法冲突」——例如锚点表写「裤脚自然落在靴口外侧」而固定前缀写「塞进靴筒」，
模型会判成矛盾而摇摆。**先把模板内部矛盾修掉**。
若模板无矛盾仍摇摆，可按 derive 自己给出的提示**手写 YAML**：沿用上一轮已通过审计的锁定项，
只更新溯源注释里的来源 sha，再用 `subject add --overwrite` 入库；
`subject_templates.template_md` 只由 `derive --apply` 或 `storage.save_subject_template` 写入，
手写 YAML 路径下要单独同步，否则三方 sha 会对不上。

### 还有一个确定性闸门：`quality_issues`

`verify` 查「档案与模板一致吗」，`quality_issues` 查「档案本身合不合格」。后者纯规则、
每次结果一样，不消耗额度：

| 检查 | 软上限 | 为什么 |
|---|---|---|
| 每组锁项条数 | `MAX_LOCK_COUNT = 10` | 锁项逐条拼进**每张图**的提示词 |
| 单条锁项长度 | `MAX_LOCK_CHARS = 200` | 单条过长会挤掉别的信息 |
| `extra` 长度 | `MAX_EXTRA_CHARS = 300` | 兜底句建议短 |

```bash
python3 - <<'PY'
from charvar import storage
from charvar.subject_derive import quality_issues
conn = storage.connect()
for sid, pj in conn.execute("SELECT id, profile_json FROM subjects ORDER BY id"):
    p = storage.SubjectProfile.model_validate_json(pj)
    for w in quality_issues(p):
        print(sid, w)
PY
```

**两个闸门要一起看。** `verify` 过了但 `quality_issues` 报的条目才是真正影响出图的
（锁项每条都会进每张图的提示词）。

**锁项抽取丢条的最常见原因：模板里的锚点没逐锚点成句。** 用分号把多个锚点串成一整段
（`丰满有肉感的成年女性体型：肩线窄、胸量大、腰细、臀宽`），LLM 抽取时只取走第一个子句，
后面的锚点整段丢失。改成每个锚点独立成句、以名词开头（「肩线窄而舒展。」「胸部体量很大，……」）
就能完整抽出来。锁项条数或内容对不上模板时，先查这一条。

## 起草主体图提示词（subject prompt；没档案没模板也能用）

网页主体档案的「AI 起草」同一条链路。**手头只有一段角色描述、或一张 character_sheet 的
文生图提示词、或任何资料** —— 没有主体档案、没有模板也能起草 5 槽位图片提示词
（character_sheet / headshot / front / side / behind）：

```bash
~/.local/bin/charvar subject prompt front --material "一位银发巫女少女，银白长发，尖顶帽……"
# stdout 只放提示词本身，可直接管道接 charvar image
~/.local/bin/charvar subject prompt --all --material-file 资料.md --json
# 5 槽全起草（5 次独立 LLM 调用）；--json 给脚本消费
```

没有档案、从零跑完一套定妆图（两步，第二步必须把设定图当参考图）：

```bash
# 1) 设定图（character_sheet）是文生图，直接出
~/.local/bin/charvar subject prompt character_sheet --material "..." | \
  ~/.local/bin/charvar image comfyui --prompt-file - --out sheet.png
# 2) 其余 4 槽的草稿含 <image1>（以设定图为基准的编辑指令）—— 把设定图成品 --ref 进去
~/.local/bin/charvar subject prompt front --material "..." | \
  ~/.local/bin/charvar image comfyui --prompt-file - --ref sheet.png --out front.png
```

要点：

- **语言不钉死**：缺省不做语言规定，输出跟随你给的材料（中文资料出中文草稿，推即梦顺手）；
  目标后端要英文（SD 类老模型）加 `--lang en`。
- **默认不碰 DB**；`--apply` 才写回本机 DB 的槽位草稿（需 `--subject <已入库id或yaml>`，未入库会明确报错）。
  主体与模板是可选加权项：`--subject <yaml路径或id>`、`--template <md>`（缺省链：显式 > 档案同目录
  同名 `<id>.md` > DB 模板）。
- **即梦长度警告**：起草稿是长文（实测常超即梦 text2image 1592 / image2image 1992 字符硬上限），
  推即梦前先把草稿压缩过线（提交前会被拦，不扣积分但白折腾）。
- LLM 配置与 `subject derive` 同链路（`LLM_API_KEY` 等，见排错表）。
- `--out`：单槽=文件路径；`--all` 时要目录，落 `<kind>.txt`。完整参数见 `references/cli.md`。

## 注意

- **seed 默认 0 = 同提示词每次出同一张图**。用户要「再来一张」时换 `--seed`。
  （ComfyUI 出的 PNG 里嵌了当次的 workflow 元数据，所以文件 md5 会不一样 —— 像素是一样的。
  OpenRouter/即梦不保证同 seed 复现。）
- ComfyUI 出图要 20~60 秒，命令会阻塞等待；`--no-wait` 只入队并回 prompt_id。
  即梦要 40~90 秒（异步任务：提交 + 轮询），命令总是阻塞等完。
- 一次只出一张图。要多张就跑多次（换 seed）。
- 参考图会被压到长边 1536 / JPEG q88 —— 这是项目统一规格，不用自己压。
- 列 OpenRouter 可用模型：`charvar image models`。
- 即梦的前置：本机装好官方 CLI 并登录过 —— `dreamina -h` 能跑、`dreamina user_credit`
  能返回积分就算就绪；没登录时先让用户手动跑 `dreamina login`（CLI 打印的授权 URL
  建议用户手动在浏览器打开完成授权）。
- 即梦提示词有硬上限：**text2image 1592 字符、image2image（带参考图）1992 字符**。
  charvar 在提交前就拦（超了不会白扣即梦积分）；上游硬上限是 1600 / 2000，
  charvar 留了 8 字符余量防字节／码点计数口径差异 —— **按 1592 / 1992 写，别按 1600 / 2000**。

## 排错

| 现象 | 原因 |
|---|---|
| `charvar: command not found` | 用绝对路径 `~/.local/bin/charvar`；还不行就跑 `scripts/install_cli.sh` |
| ComfyUI 连不上 | 检查 `COMFYUI_HOST` / `COMFYUI_PORT`（`.env`）与 ComfyUI 是否在跑 |
| OpenRouter 报 key 缺失 | 设 `OP_IMAGE_API_KEY` 或 `LLM_API_KEY` |
| 即梦报登录态无效 | 让用户手动跑 `dreamina login`（授权 URL 手动在浏览器打开），别由 agent 代登 |
| 即梦报找不到 dreamina CLI | 跑 `curl -s https://jimeng.jianying.com/cli \| bash` 安装，或设 `DREAMINA_CLI` 指向可执行文件 |
| 即梦报提示词超限 | text2image 1592 / image2image 1992 字符（上游硬上限是 1600 / 2000，charvar 留了余量），先压缩提示词 |
| 参考图超 4 张 | ComfyUI edit workflow 只有 4 个参考图槽位 |
| workflow 找不到 | `data/workflows/` 里没有该名字；用 `--workflow` 给 JSON 路径 |

## 版本记录

1.6.0（2026-10-28）：**新增语音生成通路（OpenRouter TTS）**：`charvar tts openrouter`（文本 → wav/mp3，同步）与 `charvar tts models`（模型/voice 清单）。默认模型免费档 `heygen/heygen-voice-1-turbo:free`（`OP_TTS_MODEL` 可换），网页顶栏新增「语音」页签同一条通路。要点：voice 目录枚举非空时实测必填（空值自动取第一项兜底）；HeyGen 系 5000 字符单次上限提交前拦截；裸 PCM 按响应头采样率封装成 wav，MiniMax 只出 mp3（格式给错自动换档重试并记探测缓存）。声音克隆（参考音频）第二期再接。

1.5.0（2026-10-26）：**主体图槽位提示词起草开放到 CLI**（网页「AI 起草」同一条链路）。此前起草
槽位提示词只能在网页上对「已入库 + 有模板」的主体做；现在 `charvar subject prompt` 支持
`--material` / `--material-file` 自定义参考资料 —— **没有主体档案、没有模板，只有一段角色描述
或现成提示词也能起草** 5 槽位（character_sheet 是文生图；headshot/front/side/behind 是含
`<image1>` 的编辑指令，生成时必须把设定图成品 `--ref` 进去）。两处行为变化：① 起草语言不再钉死
英文 —— 缺省不写语言规定、输出跟随材料语言（网页同链生效），要英文加 `--lang en`；② CLI 缺省
不落库，`--apply` 才写回 DB 槽位草稿（网页仍即时落库）。**推即梦注意**：英文草稿实测常超
1592/1992 字符上限，先压缩再提交。

1.4.0（2026-10-26）：**补上 CLI 导入槽位图** —— 此前 `charvar subject add` 只导档案与模板，
**完全没有导入图片的命令**：交付包（`final/<id>/`）里的槽位图只能靠网页上传，或手写脚本拷进
`data/subjects/<id>/`。手写脚本那条路有两个坑：不会生成 WebP 缩略图（缩略图端点 404、网页
不显示预览），也不会建 `subject_images` 行（网页仍显示空槽）。现在新增
`charvar subject image import <目录> --subject <id>`：按文件名认槽位，一次做完
落盘 + 生成缩略图 + 建槽位行；`subject add` 也可加 `--images <目录>` 一条命令连档案带图一起导。
装的是同一张图时保留原来的 `source`/`provider`/`prompt_id`（交付包回灌不该抹掉出处），
换成不同的图才回到 `source=uploaded`。另补一层兜底：读取槽位时若发现原图在、缩略图缺，
自动补出（`subject_images.ensure_thumb`）—— 手动拷进来的图也能出预览。

1.3.0（2026-10-26）：主体档案补两个闸门说明与一条抽取规则：`verify` 是 LLM 判定、非确定性
（连跑多次汇总去重再逐条对照）；新增确定性闸门 `quality_issues`（纯规则、不耗额度）；
锁项抽取丢条的最常见原因是模板锚点没逐锚点成句。

1.2.0（2026-10-25）：**主体档案导入补全** —— 此前 `charvar subject add` 只写档案（`subjects` 表），**从不写模板表**，且不报错、不警告，退出码还是 0。于是「改了仓库里的 yaml/md → 跑一次 subject add」这条路只导了一半：档案更新了，通用提示词模板仍停在旧版，网页与出图继续用旧文案。现在 `subject add` 会把**同目录同名 `<id>.md` 模板一并入库**（`--template` 可指定别的路径），找不到模板时明确告警并提示 DB 里旧模板可能已过期；`--json` 输出给脚本消费；档案头里的 `sha256` 指纹与模板实际内容不符时告警（溯源链断了）。`subject derive --apply` 此前也只入档案，现一并写入模板全文。SKILL.md 此前完全没提 `subject add`，也没提模板要单独上传 —— 按旧描述走的 agent 会以为导入已完成。

1.1.0（2026-10-25）：规范对齐体检后的修正。补上 `references/cli.md` 缺失的视频章节 —— 此前 SKILL.md 说「完整参数见 `references/cli.md`」，但该文件里一条 `charvar video` 命令都没有；另补 `OP_VIDEO_MODEL` / `OP_VIDEO_TIMEOUT` 两个环境变量。**即梦提示词上限改成可执行数字**：此前写「约 1600 / 2000」，但 charvar 实际在 **1592 / 1992** 就拦（上游硬上限 1600 / 2000，留了 8 字符余量防字节／码点计数口径差异），照 1600 写会被拒。`--resolution` 一行补明 **2K 像素表只对 ComfyUI 成立**（OpenRouter 通路走通用长边归一，别拿 Qwen 像素表推算）。

1.0.0（2026-10-25）：补齐 `manifest.json` 与版本号，使两个 skill 结构统一（此前 charvar-skill 没有 manifest，版本纪律无从校验）。
