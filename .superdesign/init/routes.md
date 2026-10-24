# Routes — charvar

## UI routes (client-side tabs, no URL routing)

Each tab is a `<section class="tab-page">` in `web/static/index.html`; the top nav buttons
carry `data-tab` and toggle `.active`.

| data-tab | Section id | Title | Content |
|---|---|---|---|
| `gen` | `#tab-gen` | 预设 | ① 生图设置 / ② 生成提示词 / 轮次条 / ③ 生成结果 |
| `adhoc` | `#tab-adhoc` | Playground | 参数卡（提示词 + 供应商×模式 + 参考图） / 结果卡 / 最近生成卡 |
| `video` | `#tab-video` | 视频 | 参数卡 / 结果卡 / 最近生成卡 |
| `recipes` | `#tab-recipes` | 配方 | 配方列表 / 配方详情 / 配方运行 |
| `subjects` | `#tab-subjects` | 主体档案 | 档案列表 / 档案详情表单 / 档案图片槽位 / 模板 |
| `runs` | `#tab-runs` | 历史记录 | 生成历史表 |
| `settings` | `#tab-settings` | 设置 | LLM / 主体图默认尺寸 / 生图 Workflow / OpenRouter 生图 / 即梦生图 / OpenRouter 视频 / 保存 |

The `gen` tab is the **preset batch-production page**: it drives the preset → prompt →
push-to-image pipeline. It is the page targeted by the current design task.

## HTTP API (FastAPI, `charvar/api.py`, served on 127.0.0.1:8710)

### Generation
| Method | Path | Purpose |
|---|---|---|
| POST | `/generate` | GenerateResult from a preset batch (body: GenerateRequest, incl. `references`) |
| GET | `/runs` | List runs |
| GET | `/runs/{run_id}` | One run (incl. its `references`) |
| DELETE | `/runs/{run_id}` | Delete a run |
| GET | `/runs/{run_id}/export?format=txt\|jsonl\|captions_zip` | Export |
| POST | `/runs/{run_id}/comfyui` | Push the batch to ComfyUI |
| POST | `/runs/{run_id}/openrouter` | Push the batch to OpenRouter |
| POST | `/runs/{run_id}/dreamina` | Push the batch to 即梦 (Dreamina) |
| POST | `/runs/{run_id}/regenerate` | Regenerate one item |
| POST | `/runs/{run_id}/sync-outputs` | Pull outputs from ComfyUI history |
| GET | `/runs/{run_id}/images` | Image-wall data |
| GET | `/runs/{run_id}/comfy-image/{item_index}` | One output image |
| GET | `/runs/{run_id}/assets/{scene}/{filename}`, `/runs/{run_id}/sheets/{kind}` | Local assets |
| POST | `/runs/{run_id}/contact-sheet` | Build contact sheet |
| POST | `/runs/{run_id}/overlay` | Deterministic text overlay on outputs |

### Presets / subjects / settings
| Method | Path | Purpose |
|---|---|---|
| GET | `/presets`, `/presets/{name}` | Preset list / detail |
| GET/POST/PUT/DELETE | `/subjects[/{id}]` | Subject-profile CRUD |
| PUT | `/subjects/{id}/template` | Subject prompt template |
| GET | `/subjects/{id}/images` | Subject image slots (source of reference images) |
| POST | `/subjects/{id}/images/{kind}/upload` · `/generate` · `/generate-openrouter` | Slot actions |
| GET/POST | `/settings` | Read / write all settings |
| GET | `/outfit-presets` | Outfit presets |
| GET | `/comfy/workflows`, `/comfy/status` | Workflow dir / connectivity |

### Playground / video / recipes
| Method | Path | Purpose |
|---|---|---|
| POST | `/adhoc/generate` | Playground: prompt → one image |
| GET | `/adhoc?limit=` | Playground recent items |
| POST | `/adhoc/references`, DELETE `/adhoc/references/{id}` | Staged reference pool (upload) |
| GET | `/adhoc/reference/{id}` | Serve a staged reference |
| GET | `/adhoc/{run_id}/image`, `/thumb`, `/manifest` | Playground artifacts |
| GET | `/videos/models`, `/videos/generate`, `/videos/{id}/status` | Video jobs |
| GET/POST | `/recipes`, `/recipes/{name}/run` | Recipe definitions and runs |
| POST | `/recipe-runs/{id}/continue` | Resume at the next checkpoint |
