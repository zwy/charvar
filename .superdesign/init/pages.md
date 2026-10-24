# Pages — charvar

Vanilla app: every "page" is a `<section class="tab-page">` in `web/static/index.html`
plus the JS file that drives it. The dependency tree below lists the files that must be
read to reproduce that page faithfully.

## `#tab-gen` — 预设 (preset batch production) ← **CURRENT DESIGN TARGET**

Entry: `web/static/index.html` (section `#tab-gen`, lines 6–81)
Dependencies:
- `web/static/index.html:1:81` — shell (top bar) + the whole `#tab-gen` markup
- `web/static/style.css` — every class used here (see `theme.md` Part 2 for full source)
- `web/static/app.js` — the page's behaviour:
  - `web/static/app.js:86:330` — backend panel (供应商切换 / 档位枚举 / 提示行)
  - `web/static/app.js:334:430` — init + subject/preset/outfit loading + 主体摘要
  - `web/static/app.js:431:495` — 「生成提示词」handler (builds the `/generate` payload)
  - `web/static/app.js:496:560` — result rendering
  - `web/static/app.js:735:830` — 「推送生图」handler (per-backend request bodies)
- `web/static/genrefs.js` — reference-image picker module (`GenRefs`):
  - `web/static/genrefs.js:1:145` — module header, roles, render, upload, remove
  - `web/static/genrefs.js:146:215` — subject-slot source (follows `#gen-subject`), add-from-subject
  - `web/static/genrefs.js:295:365` — public API (`specs()`, `roles()`, `count()`, `reloadKinds()`, `dropSubjectRefs()`)
- `web/static/tables.js` — shared 即梦 model→resolution table + 仅文生图模型集合
- `web/static/media.js` — image wall / modal / sync (used by ③ 生成结果)

**What the page renders** (the render branch that actually shows):

Three stacked white cards, then two more that appear after a run:

1. **① 生图设置** card
   - `.row-head`: `h2` "① 生图设置" + muted hint on the right
   - `.adhoc-switch`: segmented backend switch (ComfyUI / OpenRouter / 即梦) + a muted
     inline model-info span (`#gen-model-field`)
   - `.form-grid` (3 columns): 主体档案 select · 种子 number input · 画幅比例 select ·
     分辨率 select · 质量档 select (OpenRouter only) · 模型版本 select (即梦 only) ·
     负向提示词 text input (ComfyUI only) · 等待完成 checkbox (ComfyUI only)
   - two muted hint lines (`#gen-dim-preview`, `#gen-caps-hint`)
   - `.form-field` "参考图（最多 4 张…）": `.ref-grid` of `.ref-pick` cards, then an
     `.actions` row (上传参考图 / 从最近生成加入 / 槽位 select / 从主体档案加入 / 清空参考图),
     then `#gen-ref-hint`
2. **② 生成提示词** card
   - `.form-field.gen-mode-row`: "生成模式" label + segmented (经典模板 / LLM 优化)
   - `#gen-subject-hint` — a muted one-line read-only summary of the current subject
   - `.form-grid`: 预设 · 服装预设 · 组合顺序 · 条数 · 采样 · 拼接分隔符 · 随机背景 ·
     随机光照 · 固定背景 · 固定光照
   - `#gen-sticker-hint` (dashed `.comfy-panel`, only for sticker presets)
   - `#gen-llm-panel` (dashed `.comfy-panel`, only in LLM mode): 提示词语言 select +
     附加要求 input + two long muted hints
   - `.actions`: 「生成提示词」primary button + `#gen-status` status text
3. `#rounds-bar` card (hidden until a run exists) — round chips
4. **③ 生成结果** card `#result-card` (hidden until a run exists)
   - `.row-head`: `h2` "③ 生成结果" + `#result-summary` + `.actions` (复制提示词 / 复制
     caption / 下载 TXT / JSONL / ZIP / **「推送生图」primary**)
   - `#push-result` `.log` (hidden until pushed)
   - `.media-toolbar`: 同步图片 · segmented(图片墙|表格) · Contact sheet · 贴字… · 合成视频 · status
   - `#result-images` `.image-grid` + `#result-table-wrap` table

## `#tab-adhoc` — Playground

Entry: `web/static/index.html` (section `#tab-adhoc`)
Dependencies:
- `web/static/index.html:185:214` — markup
- `web/static/adhoc.js` — panel logic, reference list, submit
- `web/static/media.js` — recent-image grid + modal
- `web/static/tables.js` — 即梦 tables

Renders: intro `.form-note`; `.adhoc-switch` with two segmented groups (backend ×
mode t2i/i2i) + model hint; `.form-grid` (画幅 / 分辨率 / 模型版本 / 质量档 / 种子 /
负向 / 提示词 textarea); `.form-field` reference block (hidden in t2i); 出图 button;
result card with the image + `.log`; 最近生成 image grid.

## `#tab-video` — 视频

Entry: `web/static/index.html` (section `#tab-video`)
Dependencies: `web/static/video.js`, `web/static/media.js`

Renders: parameter card (提示词 / 模型 / 时长 / 分辨率 / 画幅 / 首帧 / 参考图 / 参考视频),
result card (player + download MP4), 最近生成 grid.

## `#tab-recipes` — 配方

Entry: `web/static/index.html` (section `#tab-recipes`)
Dependencies: `web/static/recipes.js`

Renders: recipe table card; hidden detail card (标题 / 种子 / 开始运行 / 查看文档 + step
chips + doc `.log`); recipe-runs table card.

## `#tab-subjects` — 主体档案

Entry: `web/static/index.html` (section `#tab-subjects`)
Dependencies: `web/static/app.js` (subject list, form, slots, template)

Renders: `.subject-cards` grid of `.subject-card`s; detail card with three
`.form-section` groups (基本信息 / 锁定项 / 补充) + YAML import `<details>`; hidden
archive-images card with `.slot-grid`; hidden template card with `.md-view`.

## `#tab-runs` — 历史记录

Entry: `web/static/index.html` (section `#tab-runs`)
Dependencies: `web/static/app.js` (`loadRuns`)

Renders: single card with a wide table (run_id / 主体 / 预设 / 种子 / 条数 / ComfyUI /
时间 / 操作) with a sticky action column.

## `#tab-settings` — 设置

Entry: `web/static/index.html` (section `#tab-settings`)
Dependencies: `web/static/app.js` (settings load/save, OpenRouter model lists)

Renders: LLM card; 主体图默认尺寸 card; 生图 Workflow card (2-col + a `.form-note`);
OpenRouter 生图 card (two side-by-side `.or-mode-group`s + `.or-foot` API-key row);
即梦 card; OpenRouter 视频 card; a full-width 保存设置 action card.
