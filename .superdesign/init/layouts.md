# Layouts — charvar

Single-page app, no router library. The "shell" is a sticky top bar + one `main` that
holds every tab page; only one `<section class="tab-page">` is visible at a time
(`display: none` / `display: block`).

## App shell — `web/static/index.html` (lines 1–6) + `web/static/app.js` (tab switch)

```html
<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>charvar · 角色变体提示词生成器</title><link rel="stylesheet" href="/static/style.css"><link rel="icon" type="image/svg+xml" href="/static/favicon.svg"><link rel="alternate icon" type="image/png" href="/static/icon.png"><link rel="apple-touch-icon" href="/static/icon.png"></head><body>
<header class="topbar"><div class="brand">charvar <span class="sub">角色变体提示词生成器</span></div><nav>
<button class="tab active" data-tab="gen">预设</button><button class="tab" data-tab="adhoc">Playground</button><button class="tab" data-tab="video">视频</button><button class="tab" data-tab="recipes">配方</button><button class="tab" data-tab="subjects">主体档案</button><button class="tab" data-tab="runs">历史记录</button><button class="tab" data-tab="settings">设置</button></nav></header>
<main>
```

```js
/* app.js — tab switch */
$$(".tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    $$(".tab").forEach((b) => b.classList.toggle("active", b === btn));
    $$(".tab-page").forEach((p) => p.classList.toggle("active", p.id === `tab-${btn.dataset.tab}`));
    if (btn.dataset.tab === "runs") loadRuns();
    if (btn.dataset.tab === "settings") loadSettings();
    if (btn.dataset.tab === "gen") applyGenBackendPanel();
    if (btn.dataset.tab === "recipes") {
      loadRecipes().catch(() => {});
      loadRecipeRuns().catch(() => {});
    }
  });
});
```

```css
.topbar {
  display: flex;
  align-items: center;
  gap: 24px;
  padding: 12px 24px;
  background: var(--card);
  border-bottom: 1px solid var(--line);
  position: sticky;
  top: 0;
  z-index: 10;
}
.brand { font-weight: 700; font-size: 16px; }
.brand .sub { font-weight: 400; color: var(--muted); font-size: 12px; margin-left: 8px; }
nav { display: flex; flex-wrap: wrap; gap: 6px; }
.tab {
  border: 1px solid transparent;
  background: transparent;
  padding: 6px 14px;
  border-radius: var(--radius-ctl);
  cursor: pointer;
  color: var(--muted);
  font-size: 14px;
  min-height: 0;
  white-space: nowrap;
}
.tab.active { background: var(--accent-soft); color: var(--accent); border-color: var(--accent-line); }
.tab-page { display: none; }
.tab-page.active { display: block; }
main { max-width: 1180px; margin: 20px auto; padding: 0 20px; }
```

## Nav items (in DOM order)

`预设` (gen) · `Playground` (adhoc) · `视频` (video) · `配方` (recipes) ·
`主体档案` (subjects) · `历史记录` (runs) · `设置` (settings)

## Script load order (bottom of `index.html`) — ORDER MATTERS

```
tables.js → media.js → genrefs.js → app.js → adhoc.js → video.js → recipes.js
```

- `tables.js` holds shared global `const` tables (`DREAMINA_MODEL_RESOLUTIONS`,
  `DREAMINA_T2I_ONLY_MODELS`) — it must load before `app.js`, otherwise `app.js` hits a
  temporal-dead-zone `ReferenceError`.
- `app.js` calls `init()` at the very end of its file, so `GenRefs` (`genrefs.js`) must
  already exist when `init()` runs.
- `app.js` and `adhoc.js` both define page-scoped logic; they must not declare the same
  global `const` twice (duplicate declaration = `SyntaxError` that kills the whole file).

## Section shells per tab

Every tab is one or more `.card` blocks inside `<section class="tab-page">`:

- `#tab-gen` — ① 生图设置 / ② 生成提示词 / 轮次条 / ③ 生成结果（4 cards）
- `#tab-adhoc` — Playground（参数卡 / 结果卡 / 最近生成卡）
- `#tab-video` — 视频（参数卡 / 结果卡 / 最近生成卡）
- `#tab-recipes` — 视频配方（列表 / 详情 / 运行）
- `#tab-subjects` — 主体档案（列表 / 详情 / 档案图片 / 模板）
- `#tab-runs` — 生成历史（单表卡）
- `#tab-settings` — 设置（LLM / 主体图尺寸 / Workflow / OpenRouter / 即梦 / 视频 / 保存）

## Script order in `index.html` (tail)

```html
<script src="/static/tables.js"></script>
<script src="/static/media.js"></script>
<script src="/static/genrefs.js"></script>
<script src="/static/app.js"></script>
<script src="/static/adhoc.js"></script>
<script src="/static/video.js"></script>
<script src="/static/recipes.js"></script>
```

## Responsive shell

```css
@media (max-width: 500px) {
  .form-grid { grid-template-columns: 1fr; }
  label.span2, .full-row { grid-column: auto; }
  .topbar { flex-wrap: wrap; gap: 8px 24px; padding: 10px 16px; }
  main { margin: 12px auto; padding: 0 12px; }
  .card { padding: 14px 16px; }
}
@media (max-width: 720px) {
  .workflow-grid { grid-template-columns: 1fr; }
}
```
