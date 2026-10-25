# 转面橱窗展示配方（turnaround_showcase）

「合成配方」：不调视频模型——全部中间产物是系统批量出的静态图，最终成片由本地 ffmpeg 合成（免费）。
reel 参数参照视频合成预设 `q21_turnaround`：**每张 0.5 秒**（其 1.0 秒的 2 倍速），其余
（9:16 · 1080p · 24fps · 景别转圈顺序 · contact sheet 封面 · 音乐 轻跳节拍）维持不变。

## 流程（2 步）

1. **转面图**（30 张）：主体档案 `yellow_dress_bob` + `q21_default_30` 预设（原工作流的
   3 景别 × 8 朝向 + 6 表情），9:16、2K，自动推 ComfyUI。
   - **mode: llm**（生成页同款 LLM 优化）：30 条变体原文本+锁定项喂给 LLM 改写成完整英文提示词
     （预设文本只是 LLM 输入，不逐字拼接——角度类指令会被 LLM 写得更冗余明确，正面先验
     不容易把「背面」带偏）；配方另给 `llm_instructions` 强调视角无歧义/单人/无文字。
   - **front 垫图**：`reference_kinds: [front]`（编辑链 image1 = 主体正面参考，跑前必须就绪）。
   - images 步 `fixed_background: gray` + `fixed_lighting: cool overcast outdoor light, flat and
     shadowless`（这两个字段 2026-10-24 接入）：转面 reel 每张只 0.5 秒，背景/光线跳变最伤观感，
     跨帧一致是硬需求。
2. **成片**：视频合成预设 `q21_turnaround_fast`（除每张秒数外与 `q21_turnaround` 完全同参）：
   开头 contact sheet 封面 2s → `full/chest/head` 三组各顺时针转一圈（front → l45 → l90 → l135
   → back → r135 → r90 → r45，每组 8 张）→ 6 张表情按生成顺序，每张 0.5s → 总时长
   2 + 30×0.5 = 17 秒；背景音乐 轻跳节拍.wav（103s，超长自动截到视频长度，结尾淡出 1s）。

## 检查点

`images` 步自动推进：`push: comfyui` 自动提交（真注入 front 垫图）+ 等待 + 同步（30 张 2K
≈ 30 × 10s 量级），推完停在合成前。人工过目 30 张成品（「历史记录」图墙）后
`charvar recipes run turnaround_showcase --continue` 合成。合成免费；不满意换 seed 重跑出图批次即可。

## 限制与注意

- `presets/actions/q21_default_30.yaml` 的 30 条 variants 文本是原工作流逐字红线，**只读**：
  LLM 模式下也只是作为改写输入传给 LLM，预设文件本身不改；要控角度歧义在 `llm_instructions` 里写。
- 表情组条目的元数据是 framing=head + angle=front + expression=…：合成器只把它们归入表情段，
  不进 head 组（render_video 的既有规则，表情段在最后）。
- LLM 模式硬前提（缺了直接 failed，改好后再 continue 也不行——images 步 failed 要重跑配方）：
  主体 front 参考图就绪、主体已上传「角色全场景通用提示词模板」。
- `fps: 24` 下每张 0.5 秒 = 12 帧/张；硬切无叠化（fade 预留未实现）。
- 想慢回 1s/张：把 video_synth 的 `video_preset` 换回 `q21_turnaround` 即可，其余不动。
- 换主体：改 YAML 里 images 步的 `subject:`（`reference_kinds: [front]` 会自动换成该主体的正面图，
  不再依赖 workflow 文件内嵌的默认图——2026-10-24 修掉了 executor 丢 overrides 的问题）。
- 表达式/角度元数据来自预设 variants（framing/angle/expression），改预设结构会影响播放顺序。

## 试跑记录

| 版本 | 模式 | 结果 |
| --- | --- | --- |
| v1 | classic（变体文本逐字拼接）+ 垫图**未生效**（executor 丢 overrides，编辑链跑的是 workflow JSON 内嵌默认图） | 17.0s reel 成片，播放顺序/卡点全对；但 #007 full_back 实际是正面（full 组 7/8），单独重推 3 次（换 seed/换参考图）仍是正面——垫图没真正到位，文本约束不够强 |
| v2 | mode llm + front 垫图真注入（executor 修复 + `llm_instructions` 强调视角） | 30/30 角度全对（full_back 出了真背面；head 组 8/8；chest 的 l135/r135 身体朝向对、头是回眸过肩式——模型自选的姿势润色，不改）。成片 17.000s，408 帧 = 2s 封面 + 30×0.5s，逐槽像素匹配原样图全部命中（margin 4.2–29.4） |

## 运行

```bash
# 全自动转到合成检查点（转面图推完 ComfyUI 后自动停）
python3 -m charvar.cli recipes run turnaround_showcase
# 人工过目 30 张后合成
python3 -m charvar.cli recipes run turnaround_showcase --continue
# 查看状态
python3 -m charvar.cli recipes status
```