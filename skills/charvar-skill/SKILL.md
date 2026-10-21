---
name: charvar-skill
description: Generate images with the charvar project's ComfyUI or OpenRouter backends from any project. This skill should be used when the user asks to "用 comfyui 生图", "用 openrouter 生图", "charvar 帮我出图", "用 charvar 出张图", "generate an image with comfyui", "generate an image with openrouter", or otherwise asks to produce an image via the charvar CLI.
---

# charvar 生图

通过 charvar 项目的 CLI 出图。两条后端：**comfyui**（本机/局域网 ComfyUI）和
**openrouter**（OpenRouter 在线生图）。用户会明确说用哪条 —— 别自己猜。

## 何时用

用户说「用 comfyui 帮我生图」「用 openrouter 生图」「charvar 出张图」，
或给出一段提示词并要求出图时。

## 前置

CLI 由 `scripts/install_cli.sh` 装到 `~/.local/bin/charvar`（已在 PATH）。
如果 `charvar` 命令找不到，先跑：

```bash
/path/to/charvar/scripts/install_cli.sh
```

## 用法

**ComfyUI**（本机或局域网；无参考图走文生图 workflow，有参考图自动切图像编辑 workflow）：

```bash
charvar image comfyui --prompt "a red apple on a wooden table" --aspect 1:1 --out out.png
```

**OpenRouter**（在线）：

```bash
charvar image openrouter --prompt "a red apple on a wooden table" --aspect 1:1 --out out.png
```

**带参考图**（最多 4 张，按顺序对应提示词里的 `<image1>`/`<image2>`）：

```bash
charvar image comfyui --prompt "same character, different pose" --ref ref1.png --ref ref2.png --out out.png
```

**长提示词**（别塞进 shell 历史）：

```bash
charvar image comfyui --prompt-file prompt.md --out out.png
# 或从 stdin
cat prompt.md | charvar image comfyui --prompt-file - --out out.png
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
| `--resolution` | `1k` `2k`（默认 `1k`）。2K 是 Qwen 官方像素，面积恒定 ~4.2MP |
| `--seed` | 采样种子，默认 `0`（固定复现）；要新图就换种子 |
| `--out` | 产物路径 |
| `--json` | 结果以 JSON 输出（给脚本消费时用） |
| `--negative` | 负向提示词（仅 ComfyUI 支持） |
| `--workflow` | 指定 ComfyUI workflow 名或 JSON 路径 |
| `--model` | OpenRouter 模型 slug |
| `--quality` | OpenRouter 质量档（模型声明支持才生效） |

完整参数见 `references/cli.md`。

## 用户想在网页上出图

charvar 的网页顶栏也有「临时生图」页签（`http://127.0.0.1:8710`），走的是同一条通路。
用户明确说「在网页上出图 / 用 charvar 的网页」时指那个 —— 告诉用户地址即可，别自己用 CLI 顶替。

## 注意

- **seed 默认 0 = 同提示词每次出同一张图**。用户要「再来一张」时换 `--seed`。
  （ComfyUI 出的 PNG 里嵌了当次的 workflow 元数据，所以文件 md5 会不一样 —— 像素是一样的。）
- ComfyUI 出图要 20~60 秒，命令会阻塞等待；`--no-wait` 只入队并回 prompt_id。
- 一次只出一张图。要多张就跑多次（换 seed）。
- 参考图会被压到长边 1536 / JPEG q88 —— 这是项目统一规格，不用自己压。
- 列 OpenRouter 可用模型：`charvar image models`。

## 排错

| 现象 | 原因 |
|---|---|
| `charvar: command not found` | 跑 `scripts/install_cli.sh`，或 `source ~/.zshrc` |
| ComfyUI 连不上 | 检查 `COMFYUI_HOST` / `COMFYUI_PORT`（`.env`）与 ComfyUI 是否在跑 |
| OpenRouter 报 key 缺失 | 设 `OP_IMAGE_API_KEY` 或 `LLM_API_KEY` |
| 参考图超 4 张 | ComfyUI edit workflow 只有 4 个参考图槽位 |
| workflow 找不到 | `data/workflows/` 里没有该名字；用 `--workflow` 给 JSON 路径 |
