# Design System — charvar

Local, single-user tool for batch-producing character-variant image prompts.
Existing product with an established visual language; this document records that language
authoritatively so design drafts stay on-system.

## Product context

**What it is.** A local FastAPI web app (127.0.0.1:8710) that turns one *subject profile*
(主体档案) + one *preset* (预设) into N variant image prompts, then pushes them to an
image backend (ComfyUI / OpenRouter / 即梦) and collects the outputs.

**Audience.** One power user (the repo owner) running it locally, often in long sessions
against a local ComfyUI box. Density and legibility beat decoration. Nothing is
marketing-grade; this is a workbench.

**Key pages / architecture**
- 预设 (preset page) — the 3-step batch flow: ① 生图设置 → ② 生成提示词 → ③ 生成结果
- Playground — one prompt → one image, for cross-project use
- 视频 / 配方 — video jobs and recipe pipelines
- 主体档案 — subject profiles, their image slots, their prompt templates
- 历史记录 / 设置 — run history and configuration

**Key features**
- Preset system (30-variant list presets, matrix presets) with stable sub-seeds
- LLM rewrite mode (classic template vs LLM-optimised prompt)
- Reference-image pipeline: upload / subject slot / recent output, each with a "role"
  (front / headshot / side / behind / character_sheet) that drives LLM anchoring
- Three image backends behind one panel; parameter enums come from each backend's own
  capability list
- Outfit presets (cartesian product with pose presets), deterministic text overlay,
  contact sheet, local video synthesis

**JTBD.** "I have a character sheet and a pose list; give me 30 consistent, on-brand
images without leaving the browser, and let me re-run one of them."

## Visual language

**Keep it exactly as it is.** This is a utilitarian desktop tool, not a consumer product:
white cards on a cool light grey, one blue accent, 1px borders, no shadows except focus
rings, no gradients, no illustrations, no rounded-pill everything. The aesthetic is
"well-organised spreadsheet", not "dashboard product".

### Color
| Token | Value | Use |
|---|---|---|
| `--bg` | `#f6f7f9` | page background |
| `--card` | `#ffffff` | card surface |
| `--line` | `#e4e7ec` | 1px borders and dividers |
| `--text` | `#1c2430` | primary text |
| `--muted` | `#6b7686` | labels, hints, secondary text |
| `--accent` | `#2563eb` | primary action, active state |
| `--accent-soft` | `#eef4ff` | active / hover fill |
| `--accent-line` | `#d7e4ff` | active border |
| `--danger` | `#dc2626` | destructive |

Hard-coded (not tokens, but part of the language): `#eef0f4` segmented track,
`#fafbfd` dashed-panel fill, `#f4f6f9` image placeholder + code fill, `#f1f3f6` log fill,
`#f6f8fb` form-note fill, `#e7f6ec`/`#bfe8cd`/`#15803d` ready badge,
`#fdecec`/`#f3c7c7` error badge.

### Typography
- UI: `-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif`
- Mono: `ui-monospace, SFMono-Regular, Menlo, Consolas, monospace`
- Sizes: 11px badge · 12px labels/hints/table body · 13px status/table head/segmented key ·
  14px body/tabs/buttons · 15px card h2 · 16px brand
- Labels and hints are always `--muted` at 12px. Body copy is `--text` at 14px.

### Spacing
Scale `--sp-1: 4px` · `--sp-2: 8px` · `--sp-3: 12px` · `--sp-4: 16px`.

**The governing rhythm rule:** inside a card, every direct child is separated by exactly
one `--sp-3` layer (`.card > * + * { margin-top: var(--sp-3); }`). Denser groups declare
their own tighter gap explicitly (`.comfy-panel` → `--sp-3`, `.form-field` → `--sp-1`).
Never hand-write a margin on a card's direct child.

**Grouping is one mechanism, app-wide:** `.form-section` (flex column, `--sp-2` gap) with
`.form-section-title`, separated by a 1px rule + `--sp-3` padding. Every page that has more
than one cluster of controls uses it — preset page, Playground, video, subject profile.
A page that is one card per topic (settings, recipes, history) does not need groups.
Do not introduce a second grouping class.

**This round's direction: tighten the vertical rhythm.** The current 16px layer between
every card child, combined with 18px card padding, makes the preset page read as a long
stack of loosely-connected blocks. The target is a visibly tighter page: card padding and
the in-card block gap should shrink by one step, and groups of related controls should
read as one cluster rather than as separate floating rows. Keep the 4/8/12/16 scale —
tighten *within* it, do not invent new values.

### Radii
`--radius-ctl: 8px` (inputs, buttons) · `--radius-panel: 10px` (dashed panels, image
cards) · `--radius-card: 12px` (page cards, modals) · `999px` pills (chip, badge).

### Control sizing
`--ctl-h: 35px` standard input/select/button height; checkbox rows borrow it to
bottom-align with the input row beside them. Small in-row buttons (`.btn-sm`): `4px 10px`,
12px text.

### Shadows
Focus rings only: `0 0 0 3px var(--accent-soft)`. Active segmented key:
`0 1px 2px rgba(16, 24, 40, 0.12)`. No drop shadows on cards.

### Motion
Transitions are 150–180ms ease on colour / border / background / box-shadow only. No
transform, no layout animation, no spring. Honour `prefers-reduced-motion` (already
handled globally).

### Layout
`main { max-width: 1180px; margin: 20px auto; padding: 0 20px; }`
Form grid: `repeat(auto-fill, minmax(220px, 1fr))` → 3 columns at 1180px.
Settings page: `.settings-grid` — 2 columns, gap `--sp-3` / `--sp-4`, collapses at 720px.
It is the only field grid the settings cards use: `.form-grid`'s 8px row gap reads as a wall of
stuck-together fields there, 12px does not. The image-size card uses `.size-grid` (one cell per
slot, 3 columns). Same-kind controls share `--ctl-h` (35px) — a dropdown rendered a few px shorter
than its neighbours is visible immediately.
Breakpoints: 720px (two-column grids collapse), 500px (form grid → 1 column, topbar wraps,
card padding shrinks).

## Component language (must be preserved)

- **Segmented** for two-level/two-state switches (backend, generation mode, image-wall vs
  table) — grey track, white active key, blue text. **Chips** (pills) only for multi-select
  filters and round badges.
- **Cards** are the only container. Dashed `.comfy-panel` is the only nested sub-panel.
- **Form fields** are label-wrapped: muted 12px label above a full-width 35px control.
- **Hints** are muted 12px secondary text. Treat them as a scarce resource — see below.
- **Reference images** are 92px-min thumbnail cards with a role dropdown.
- **Icons**: the app has no icon set. Buttons are text-only. Keep it that way.

## Copy policy (this round's focus)

The preset page currently carries too much explanatory copy: every card head has a
right-hand hint, every parameter group has a hint line underneath it, and several hints
restate what the controls already show.

For drafts in this project:
- **Delete redundant hints.** A hint that repeats what the visible control already says
  (e.g. "分辨率" under a 分辨率 dropdown; "未选参考图 → 文生图 workflow: <name>" next to a
  workflow picker; a card-head hint like "提示词生成后可在第 ③ 步推送生图") is noise —
  remove it rather than restating it more neatly.
- **Keep only hints that carry information the user cannot get from the control itself**:
  current effective value / resolution (e.g. "当前输出 1696 × 2528px"), which backend
  model is active, which workflow will be used, and irreversible consequences (a hard
  limit that fails the request, a staged file that expires, per-second billing, credits
  spent).
- **One hint per block, at most.** Not a paragraph of hints stacked under a control.
- **No hints that describe navigation** ("第 ③ 步推送生图", "在①改") — the three numbered
  card titles already convey the flow.
- **No hints that describe implementation** — output paths, manifest files, which
  database table a run lands in, CLI equivalents. That belongs in the README.
- Prefer a short, concrete sentence over a hedged clause. "带参考图 = 图片编辑" beats
  "有参考图 = 图片编辑（ComfyUI 走图片编辑 workflow / OpenRouter 走 i2i 组 / 即梦走
  image2image）".
- **Empty states are frames, not sentences.** A dashed `.ref-empty` panel reading
  "暂无参考图" says more than a paragraph, and stays true when the buttons below it
  already explain how to add one.

## Non-negotiable functional content

The redesign must not remove or move these — they are the actual workflow:

- ① must show: 主体档案 · 种子 · 画幅比例 · 分辨率 · 质量档 (OpenRouter) · 模型版本 (即梦)
  · 负向提示词 (ComfyUI) · 等待完成 (ComfyUI) · the backend switch · the reference list
  with its add/remove controls · the 「生成提示词」 and 「推送生图」 buttons
- ② must show: 生成模式 · 预设 · 服装预设 · 组合顺序 · 条数 · 采样 · 拼接分隔符 ·
  随机背景/光照 · 固定背景/光照
- ③ must show: the run summary, export actions, 「推送生图」, the image wall / table toggle,
  同步图片 / Contact sheet / 贴字 / 合成视频
- Conditional visibility rules stay: 质量档 only for OpenRouter, 模型版本 only for 即梦,
  负向提示词 + 等待完成 only for ComfyUI, the LLM extra panel only in LLM mode.
- All copy is Chinese. Labels stay as they are: 主体档案, 种子, 画幅比例, 分辨率, 质量档,
  模型版本, 负向提示词, 预设, 服装预设, 组合顺序, 条数, 采样, 拼接分隔符, 参考图,
  生成模式, 经典模板, LLM 优化, 生成提示词, 推送生图.

## Fidelity constraint (applies to every draft prompt)

Use ONLY the fonts, colors, spacing, radius, and component styles defined in this design
system and in `web/static/style.css`. Do not introduce any fonts, colors, gradients,
shadows, icon sets, or visual styles not in the design system. Do not add illustrations,
hero sections, marketing copy, or decorative elements.
