# model_clip_demo · 生成配方示例

两步的**生成配方**（末步 `video_model`，OpenRouter 视频通路）：

1. **首帧图**（`adhoc_image`，backend=comfyui）：用一段固定提示词在本地 ComfyUI 出一张 9:16 单图当首帧 —— 不进主体档案槽位、不跑预设批量。
2. **直出片段**（`video_model`）：首帧交给 OpenRouter 视频模型（heygen/heygen-video-1，$0.02/s @480p）直出 5s 片段。

## 运行

```bash
charvar recipes run model_clip_demo            # 停在「首帧图」检查点
charvar recipes run model_clip_demo --continue # 出首帧（ComfyUI，免费）
charvar recipes run model_clip_demo --continue # 提交视频作业并轮询（真金白银）
```

或顶栏「配方」页 → 选「model_clip_demo」→ 开始运行 → 弹层里逐步「继续」。

## 检查点语义

- 两步都缺省 manual：`start_run` 停在「首帧图」；continue 出首帧后停在「直出片段」；再 continue 提交作业并轮询（`wait_s: 300` 内收片，超了转 waiting，下次 continue 续查 —— 作业本身在 OpenRouter 侧继续跑，不浪费）。
- 作业失败 → 步 failed；continue 会**重新提交**（重新计费）。
- 中途想换提示词/模型：改完 YAML 后**重建**配方运行（同一次运行不会读新版定义）。

## 注意

- `video_model` 步参数枚举（时长/分辨率/画幅/首帧支持）按 `GET /videos/models` 目录在提交前校验；目录没收录的模型交给 provider 拒参自动降级。
- 没配 OpenRouter key 会在提交时失败（env `OP_IMAGE_API_KEY` / `LLM_API_KEY`）。
- 参考视频（Seedance gen2+ 的 video_url 通路）在配方里用 `video_refs:` + `video_ref_labels:` 声明，见 AGENTS.md「视频配方」。