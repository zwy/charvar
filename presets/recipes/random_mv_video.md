# random_mv_video · 随机选角 MV（多镜头分镜）

`random_dance_video` 的 **MV 变体**：同样五步、每跑一次换一批选角，但脚本换成**多镜头
MV 分镜**（编号镜头 + 分秒切点 + 机位运镜 + 实录声场/配乐两段），成片是**首席+伴舞**的
15 秒宽屏（16:9）舞蹈 MV —— 结构对齐 Veo/Gemini 系原生音频模型的经典分镜提示词。

| 步 | 干什么 | manual |
|---|---|---|
| `pick` 随机选库 | 人脸库 **face-db-01**（face 正面；想扩库把 `sources` 加回 face-db-02-cn / 03-cn）+ 深度库站姿两条线（首席起势）+ 实人服饰**收窄日系可爱向**（春 215 + パーティ 48 + 舞台礼裙 4 ≈267 条；想扩删 `categories` 项）；选中动作没有描述按 A（同动作复制）→ B（LLM 起稿）补齐 | false |
| `scene_design` 场景设计 | LLM 按 动作+服饰 设计场景/光照；`hint:` 固定给 MV 方向：舞蹈摄影棚/舞台/天台等开阔场地、地面质感清晰可反光、留出群舞队型纵深、光感鲜明 | false |
| `combo_image` 首帧图 | 三库组合流出图 + `strip_accessories: true`（去包/帽/墨镜/手持物）+ `framing_extra`（**16:9 横画幅**：首席全身居中、四周留余白与纵深，给切镜群舞留空间）；ComfyUI 2k | **true** |
| `video_script` 视频脚本 | **看图 + 多镜模式**（`frame_from: 首帧图` + `multishot: true`）：附着真实首帧交给视觉 LLM——首帧就是**第 1 镜的开场画面**（0 秒 = 图中姿态），后续 `{shots} 个镜头`按脚本切换机位切出；每镜写明起始秒 + 镜头语言（特写/中景/全景/低位仰拍/跟拍/横摇/推近/环绕）+ 编舞；正文后附【实录声场】【配乐】两段（支持音频的模型照此出声，heygen 不支持当氛围读）；`ensemble: true`（**伴舞从第 2 镜入画**：3–8 名、同色系简化服、动作与首席同步、阵型让出首席中心）；`amplitude: large`；**风格池掷骰**（`types:` 6 种） | false |
| `video_model` 成片 | 脚本当 prompt + 首帧当 frame → OpenRouter 视频异步作业；本配方 **15s 768p 16:9**（目录 $0.036/s ≈$0.54，实测 $0.018/s ≈$0.27——480p 已实测 $0.15/条偏软，调试完升 768p）；**模型走设置页默认链**（现阶段 heygen） | **true** |

## 运行

```bash
charvar recipes run random_mv_video            # 随机选库 + 场景设计，停在「首帧图」检查点
charvar recipes run random_mv_video --continue # 出首帧 + 看图写多镜脚本，停在「成片」检查点
charvar recipes run random_mv_video --continue # 提交视频作业并轮询（真金白银，15s 768p）
```

或顶栏「配方」页 → 「random_mv_video」→ 开始运行 → 弹层里逐步「继续」。

## 分镜模式参数（改 YAML 后重建运行）

- **镜头数**：`video_script` 步 `shots:`（2–6）；删掉则按时长自动推（≥12s→4 镜 / ≥8s→3 镜 / 其余→2 镜）。`multishot` 要求显式 `duration:`。切点均匀度与镜头语言由脚本步的 brief 硬规则给出。
- **阵容**：`ensemble: false`（或删掉该行）= 全程仅首席一人；`true` = 伴舞从第 2 镜入画（人数 3–5 由 brief 锁定）。multishot 才带伴舞条款。
- **风格池**：`types:` 就是 MV 编舞风格池，想增删直接改（如加「宅舞」「复古 disco」「偶像打 call 舞」）。
- **时长/档位**：`video_script.duration` 与 `video_model.duration` 要一起改；heygen 支持 5–30s。
- **画质**：本配方 `resolution: 768p`（目录 0.036/s、实测 0.018/s；480p 实测会偏软；跨「长时档」费率另计，实测价自动记入 `data/openrouter/video-costs.json`）。

## 换有声模型（流程跑通后）

- **`google/gemini-omni-1.2-flash`**（目录原生音频、3–15s、720p ≈$0.0695/s）：`video_model` 步写 `model: google/gemini-omni-1.2-flash` + `resolution: 720p`（或 `duration: 8` 做短版）；【实录声场】【配乐】两段从「氛围描述」变成**真出声**。
- `kling-v3.0` / `wan-3.0` / 姿态：同理改 `model:`，枚举以 `charvar video models --provider openrouter` 目录为准。
- 想全局换默认：设置页「OpenRouter 视频」组改默认模型，YAML 不动。

## 注意

- **看图脚本要求设置页 LLM 为视觉模型**；站姿描述走 A 路复制、不需要视觉起稿。
- 首帧首席全身入镜手脚完整；伴舞由视频模型在切镜时自行生成，人数与面孔每镜可能波动——属 15s 单作业多镜模式的固有边界，出片不满意换种子重跑脚本步即可。
- `video_model` 提交即 202 作业；`wait_s: 900` 内收片（15s 片比 8s 慢），超时转 waiting 下次 continue 续查。
- 重跑/复现/换批语义与 `random_cast_video` 完全一致（种子 + run_id 掷骰；continue 不换批）。
