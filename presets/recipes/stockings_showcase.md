# 丝袜橱窗展示配方（stockings_showcase）

「合成配方」示例：不调视频模型——全部中间产物是系统批量出的静态图，最终成片由本地 ffmpeg 合成。

## 流程（4 步）

1. **主图**（3 张）：主体档案 `yellow_dress_bob` 的 front 参考图 + `white_skirt_stockings_poses`
   预设（3 个动作变体），9:16、2K，自动推 ComfyUI。可用作 reel 的 contact sheet 封面素材（当前
   配方未启用封面，`reel_adaptive` 无 contact sheet；想用封面合成请改走 `q21_turnaround` 类预设）。
2. **主图24**（24 张）：`white_skirt_stockings_poses`（3 动作）× 服装预设 `stockings_8`
   （8 档丝袜）笛卡尔积；`outfit_first` = 每只丝袜连播 3 个动作（这个顺序就是成片播放顺序；
   想按动作分组改 `outfit_order: pose_first`）。
3. **贴丝袜名**：贴字预设 `stockings_label_bl`（左下角·白字黑描边·半透明底板），文字取
   服装 label（黑丝15D …）。产物落在 run 的 `labeled/`，原图 `original/` 不动。
4. **成片**：视频合成预设 `reel_adaptive`（逐张轮播·比例自适应·无音乐）合成 MP4；
   优先喂贴字产物（`use_labeled: true`）。

## 检查点

`overlay` 与 `video_synth` 是 manual 步：执行到它们会停（waiting），人工过目 24 张成品
（在「历史记录」里看 run 图墙，废图可重推）后 `charvar recipes run stockings_showcase --continue`
继续。`images` 步自动推进：`push: comfyui` 自动提交 + 等待 + 同步（ComfyUI 不可达时停下来，
手动推送后 `--continue` 会重试：一张都没成就整体重推，部分完成则不动、提示手动补齐）。

## 限制与注意

- **服装段红线**：服装 text 追加在动作段之后，必须短（≤200 字符）——动作预设的实测：
  前置长文本会把动作稀释到不执行。改丝袜文案时保持短、动词明确。
- `images` 步支持 classic（中文动作段逐字进提示词，本配方用这个）与 `mode: llm`（LLM 改写，
  2026-10-24 接入——转面橱窗 turnaround_showcase 在用；硬前提：主体模板+参考图槽位就绪）。
- size 只支持 1k/2k（4K 已下线）；9:16 在 2K 走 Qwen 官方像素表 1536×2752。
- 成本：24 张 2K ComfyUI 出图 ≈ 24 × 10s 量级（本机 4090）；贴字/合成免费。
- 换主体：改 YAML 里两处 `subject:`（或跑的时候临时换配方副本）；主体 front 图必须就绪。
- 背景是通配符池随机（wildcards/backgrounds_v2）：images 步已支持 `fixed_background` /
  `fixed_lighting`（2026-10-24 接入，转面橱窗配方在用）；本配方保持随机——8 档服装 × 3 动作
  主要对比服装，不锁背景也可。

## 运行

```bash
# 全自动跑到第一个检查点（主图24 出图完会停在贴字前）
python3 -m charvar.cli recipes run stockings_showcase
# 人工过目后继续（贴字 + 合成）
python3 -m charvar.cli recipes run stockings_showcase --continue
# 查看状态
python3 -m charvar.cli recipes status
```