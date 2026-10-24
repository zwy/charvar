# dance_a_sway · 舞蹈配方 A：轻快摇摆卡点舞

生成配方：单图首帧（ComfyUI/Qwen 图片编辑，免费）→ OpenRouter 视频模型直出（heygen/heygen-video-1，480p 5s ≈ $0.10）。

源自《单图舞蹈特效 Workflow：A 摇摆卡点舞 B 猫爪弹弹舞 V1.0》（2026-10-24）第 3 章；提示词为已交付原文。charvar 变化只有一处并在此记录（按原文 5.3「修改版要记录」）：首帧提示词里抖音的 `素材1` 替换为 charvar 通路的 `<image1>`（参考图 = yellow_dress_bob 档案「全身正面」槽位，经 `reference_labels: [front]` 注入）。视频提示词原文未动（"输入首帧" = 实际上传的生成首帧，即上游 adhoc_image 步产物）。

## 运行

```bash
charvar recipes run dance_a_sway --continue   # 首帧（ComfyUI，免费）
charvar recipes run dance_a_sway --continue   # 视频作业（ heygen 5s 480p；wait_s=420 内收片，超了再 continue 续查）
```

## 检查重点（原文 3.4）

- 起势直接接上首帧，没有先垂手再重新抬手。
- 能辨认「左右弹摆 → 高低换拳 → 双拳侧摆 → 歪头亮相」的顺序，而不是全程随机晃手。
- 人脸、服装和构图保持连续，双手留在画面内。
- 收尾有轻微余动，而不是突然僵住。

## 参数备忘

| 项 | 值 |
| --- | --- |
| 首帧模型 | ComfyUI 工作流（设置页 comfy_edit_workflow，默认编辑链） |
| 视频模型 | heygen/heygen-video-1（与 10-23 首次效果测试同模型） |
| duration / resolution / aspect | 5s / 480p / 9:16（提交前按目录枚举校验） |
| seed | 配方级 0；换种子出不同片 |
