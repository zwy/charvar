---
name: charvar-skill
description: Generate images with the charvar project's ComfyUI, OpenRouter, or Dreamina backends from any project. This skill should be used when the user asks to "用 comfyui 生图", "用 openrouter 生图", "用即梦生图", "charvar 帮我出图", "用 charvar 出张图", "generate an image with comfyui", "generate an image with openrouter", "generate an image with dreamina", or otherwise asks to produce an image via the charvar CLI; also for generating short videos via OpenRouter ("charvar 帮我出段视频", "用 openrouter 生视频", "generate a video with charvar"), and for charvar subject-profile tasks such as "导入角色档案", "从模板生成主体档案" or using the charvar subject CLI.
---

# charvar 生图

版本：1.3.0  
更新：2026-10-26

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

⚠️ **`verify` 是 LLM 判定，不是确定性检查**：同一个档案连跑几次，报出的条目不一样，
一轮「通过」不等于档案完整。正确做法是跑多次 → 汇总各轮条目去重 → 逐条对照模板补全 → 再跑。

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

1.2.0（2026-10-25）：**主体档案导入补全** —— 此前 `charvar subject add` 只写档案（`subjects` 表），**从不写模板表**，且不报错、不警告，退出码还是 0。于是「改了仓库里的 yaml/md → 跑一次 subject add」这条路只导了一半：档案更新了，通用提示词模板仍停在旧版，网页与出图继续用旧文案。现在 `subject add` 会把**同目录同名 `<id>.md` 模板一并入库**（`--template` 可指定别的路径），找不到模板时明确告警并提示 DB 里旧模板可能已过期；`--json` 输出给脚本消费；档案头里的 `sha256` 指纹与模板实际内容不符时告警（溯源链断了）。`subject derive --apply` 此前也只入档案，现一并写入模板全文。SKILL.md 此前完全没提 `subject add`，也没提模板要单独上传 —— 按旧描述走的 agent 会以为导入已完成。

1.1.0（2026-10-25）：规范对齐体检后的修正。补上 `references/cli.md` 缺失的视频章节 —— 此前 SKILL.md 说「完整参数见 `references/cli.md`」，但该文件里一条 `charvar video` 命令都没有；另补 `OP_VIDEO_MODEL` / `OP_VIDEO_TIMEOUT` 两个环境变量。**即梦提示词上限改成可执行数字**：此前写「约 1600 / 2000」，但 charvar 实际在 **1592 / 1992** 就拦（上游硬上限 1600 / 2000，留了 8 字符余量防字节／码点计数口径差异），照 1600 写会被拒。`--resolution` 一行补明 **2K 像素表只对 ComfyUI 成立**（OpenRouter 通路走通用长边归一，别拿 Qwen 像素表推算）。

1.0.0（2026-10-25）：补齐 `manifest.json` 与版本号，使两个 skill 结构统一（此前 charvar-skill 没有 manifest，版本纪律无从校验）。
