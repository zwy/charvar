# Components — charvar

This is a **vanilla HTML/CSS/JS** app (FastAPI + `web/static/`). There is no React/Vue
component tree — every "component" is a CSS class plus an HTML fragment, and behaviour
lives in a plain `.js` file that queries elements by id.

- Markup + copy: `web/static/index.html`
- Styling: `web/static/style.css` (full source in `theme.md` Part 2)
- Behaviour: `web/static/app.js` (admin page), `web/static/media.js`, `web/static/genrefs.js`,
  `web/static/adhoc.js`, `web/static/video.js`, `web/static/recipes.js`
- Shared table: `web/static/tables.js`

## Button / `.btn`

- Source: `web/static/style.css` (rules) + `web/static/index.html` (usage)
- Variants: `button.primary` (accent fill), `button.danger` (red text on pale border),
  `.btn-sm` (in-row small action, 12px, `4px 10px`), `a.btn` (link styled as button),
  `label.btn.file-btn` (label wrapping a hidden `input[type=file]`).

```html
<button id="btn-generate" class="primary">生成提示词</button>
<button id="btn-gen-ref-clear" type="button">清空参考图</button>
<button class="btn-sm" type="button">移除</button>
<a id="dl-txt" class="btn" hidden>下载 TXT</a>
<label class="btn file-btn">上传参考图<input type="file" id="gen-ref-file" accept="image/*" multiple hidden></label>
```

```css
button, .btn {
  font: inherit;
  padding: 6px 14px;
  min-height: var(--ctl-h);
  border-radius: var(--radius-ctl);
  border: 1px solid var(--line);
  background: #fff;
  cursor: pointer;
  color: var(--text);
  text-decoration: none;
  transition: color .15s ease, border-color .15s ease, background-color .15s ease, box-shadow .15s ease;
}
button:hover, .btn:hover { border-color: var(--accent); color: var(--accent); }
button.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
button.primary:hover { color: #fff; opacity: 0.92; }
button.danger { color: var(--danger); border-color: #f3c7c7; }
button.danger:hover { color: var(--danger); background: #fdecec; }
button:disabled { opacity: 0.5; cursor: not-allowed; }
.btn-sm, .row-actions button, .slot-actions button, .slot-actions .btn,
.image-card .image-actions button, .image-card .image-actions .btn,
.ref-pick .btn-sm {
  padding: 4px 10px;
  min-height: 0;
  font-size: 12px;
  line-height: 1.5;
  border-radius: var(--radius-ctl);
}
```

## Segmented control — `.segmented` / `.seg`

Two-level / two-state switches only (backend switch, generation mode, image-wall vs table).
Grey track, white active key.

```html
<div class="segmented" id="gen-backend">
  <button type="button" class="seg active" data-backend="comfyui">ComfyUI</button>
  <button type="button" class="seg" data-backend="openrouter">OpenRouter</button>
  <button type="button" class="seg" data-backend="dreamina">即梦</button>
</div>
```

```css
.segmented {
  display: inline-flex;
  gap: 2px;
  padding: 3px;
  background: #eef0f4;
  border-radius: var(--radius-panel);
}
.segmented .seg {
  border: 0;
  background: transparent;
  padding: 5px 14px;
  min-height: 0;
  border-radius: var(--radius-ctl);
  cursor: pointer;
  color: var(--muted);
  font-size: 13px;
  font-family: inherit;
}
.segmented .seg:hover { color: var(--text); }
.segmented .seg.active {
  background: #fff;
  color: var(--accent);
  font-weight: 600;
  box-shadow: 0 1px 2px rgba(16, 24, 40, 0.12);
}
.seg-sm .seg { padding: 3px 10px; font-size: 12px; }
```

## Switch row — `.adhoc-switch`

A wrapping row that holds a segmented control + an inline model-info hint. Used by the
preset page and the Playground page.

```html
<div class="adhoc-switch">
  <div class="segmented" id="gen-backend">…</div>
  <span id="gen-model-field" class="hint"></span>
</div>
```

```css
.adhoc-switch {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px 18px;
}
```

## Card — `.card`

White surface, 1px border, 12px radius, `18px 20px` padding, 16px bottom margin.
**Every direct child of a card is separated by exactly one `--sp-4` layer** via
`.card > * + * { margin-top: var(--sp-4); }`.

```html
<div class="card">
  <div class="row-head"><h2>① 生图设置</h2><span class="hint">…</span></div>
  <div class="form-grid">…</div>
</div>
```

## Card head — `.row-head`

Title on the left, actions on the right, wraps.

```html
<div class="row-head"><h2>② 生成提示词</h2><span class="hint">提示词生成后可在第 ③ 步推送生图</span></div>
```

```css
.row-head { display: flex; justify-content: space-between; align-items: center; gap: var(--sp-3); flex-wrap: wrap; }
.row-head h2 { margin: 0; }
```

## Form grid — `.form-grid`

Auto-fill grid, min 220px per column → 3 columns at the 1180px page width.
Row gap `--sp-3` (12px), column gap `--sp-4` (16px).

```html
<div class="form-grid">
  <label>主体档案<select id="gen-subject"></select></label>
  <label id="gen-seed-field">种子<input type="number" id="gen-seed" min="0" placeholder="留空 = 随机（0–100000）"></label>
  <label>画幅比例<select id="gen-aspect"></select></label>
</div>
```

```css
.form-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: var(--sp-3) var(--sp-4);
}
```

## Form field — `label` / `label.check`

A label wraps its control and lays out column-first with a 4px micro-gap. The label text
is the muted 12px role; the control is 35px tall and full width.
`label.check` flips to a row (checkbox + text) and borrows `--ctl-h` so it bottom-aligns
with the input row next to it.

```css
label { display: flex; flex-direction: column; gap: var(--sp-1); font-size: 12px; color: var(--muted); }
label input, label select, label textarea {
  font: inherit; color: var(--text); padding: 7px 9px; min-height: var(--ctl-h);
  border: 1px solid var(--line); border-radius: var(--radius-ctl); background: #fff; width: 100%;
}
label.check { flex-direction: row; align-items: center; gap: var(--sp-2); align-self: end; min-height: var(--ctl-h); }
label.check input { width: auto; }
label input[type="checkbox"] { min-height: 0; }
label.span2 { grid-column: span 2; }
```

## Form field group — `.form-field`

A labelled block (label + arbitrary content) laid out column-first with a 4px gap.
`.field-label` is the muted 12px caption.

```html
<div class="form-field">
  <div class="field-label">参考图（最多 4 张，按顺序对应提示词里的 &lt;image1&gt;、&lt;image2&gt;…）</div>
  <div id="gen-ref-list" class="ref-grid"></div>
  <div class="actions">…</div>
  <span id="gen-ref-hint" class="hint"></span>
</div>
```

```css
.form-field { display: flex; flex-direction: column; gap: var(--sp-1); font-size: 12px; color: var(--muted); }
.field-label { color: var(--muted); font-size: 12px; }
```

## Hint — `.hint`

Secondary 12px muted text. **Inline by default** — a `.hint` that is a direct child of a
`.card` does not receive the `> * + *` margin rule (inline boxes ignore vertical
margins), so it visually hugs the element above it. Give it `display: block` when it
needs its own rhythm layer.

```css
.hint { color: var(--muted); font-size: 12px; margin: 0; }
.form-field .hint { margin-top: var(--sp-1); }
```

## Form note — `.form-note`

Full-row explanatory paragraph inside a form area: pale fill, thin border, 12px muted
text, 1.6 line-height. Used for the long "提示：…" paragraphs on the settings page.

```css
.form-note {
  grid-column: 1 / -1;
  margin: 0;
  padding: 8px 12px;
  background: #f6f8fb;
  border: 1px solid var(--line);
  border-radius: var(--radius-ctl);
  font-size: 12px;
  color: var(--muted);
  line-height: 1.6;
}
```

## Dashed panel — `.comfy-panel`

Sub-panel with a dashed border and a slightly denser internal rhythm (`--sp-3`).

```css
.comfy-panel {
  padding: 14px 16px;
  background: #fafbfd;
  border: 1px dashed var(--line);
  border-radius: var(--radius-panel);
}
.comfy-panel > * + * { margin-top: var(--sp-3); }
```

## Action row — `.actions`

Horizontal, 10px gap, wraps. Also the home of standalone `<select>` elements
(`.actions select` has its own rule because it is not a `label` child).

```css
.actions { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.actions select { font: inherit; padding: 6px 9px; min-height: var(--ctl-h); border: 1px solid var(--line); border-radius: var(--radius-ctl); background: #fff; color: var(--text); max-width: 220px; }
```

## Reference pick — `.ref-pick`

A thumbnail card for one selected reference image. On the preset page it also carries a
role `<select>` and a remove button, so the whole card is not a click target.

```html
<div class="ref-pick active">
  <img src="…" alt="">
  <span class="ref-name">white_skirt_stockings / 全身正面</span>
  <span class="badge">#1</span>
  <select class="ref-role"><option>全身正面（体型/服装/鞋）</option>…</select>
  <button class="btn-sm" type="button">移除</button>
</div>
```

```css
.ref-grid { display: flex; flex-wrap: wrap; gap: var(--sp-2); }
.ref-pick { display: flex; flex-direction: column; align-items: center; gap: var(--sp-1); padding: var(--sp-2); border: 1px solid var(--line); border-radius: var(--radius-panel); background: #fff; cursor: pointer; min-width: 92px; }
.ref-pick img { width: auto; height: auto; max-width: 120px; max-height: 96px; object-fit: contain; border-radius: 6px; background: #f4f6f9; cursor: zoom-in; }
.ref-pick .ref-thumb-empty { width: 72px; height: 96px; border-radius: 6px; background: #f4f6f9; border: 1px dashed var(--line); }
.ref-pick .ref-name { font-size: 12px; color: var(--text); }
.ref-pick:hover:not(:disabled) { border-color: var(--accent); }
.ref-pick.active { background: var(--accent-soft); border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft); }
.ref-pick:disabled { opacity: 0.45; cursor: not-allowed; }
#gen-ref-list .ref-pick { cursor: default; }
```

## Chip — `.chip` / `.chip-row`

Pill-shaped multi-select filters and round badges only.

```css
.chip-row { display: flex; flex-wrap: wrap; gap: 6px; }
.chip { padding: 5px 10px; border: 1px solid var(--line); border-radius: 999px; background: #fff; font-size: 12px; color: var(--text); cursor: pointer; min-height: 0; }
.chip.active { background: var(--accent-soft); border-color: var(--accent-line); color: var(--accent); font-weight: 600; }
```

## Badge — `.badge`

Pill label with state colors: `badge-ready` (green), `badge-queued` (accent), `badge-error` (red).

```css
.badge { padding: 2px 8px; border-radius: 999px; font-size: 11px; border: 1px solid var(--line); color: var(--muted); background: #fff; }
.badge-ready { background: #e7f6ec; border-color: #bfe8cd; color: #15803d; }
.badge-queued { background: var(--accent-soft); border-color: var(--accent-line); color: var(--accent); }
.badge-error { background: #fdecec; border-color: #f3c7c7; color: var(--danger); }
```

## Status text — `.status`

Inline muted 13px message next to a button; `.status.error` turns red.

```css
.status { color: var(--muted); font-size: 13px; display: inline-block; min-height: 18px; }
.status.error { color: var(--danger); }
```

## Log block — `.log`

Monospace pre block, muted grey fill, scrollable, max 180px tall.

```css
.log { font-family: var(--mono); font-size: 12px; background: #f1f3f6; border-radius: var(--radius-ctl); padding: 10px 12px; max-height: 180px; overflow: auto; white-space: pre-wrap; margin: 0; }
```

## Image card — `.image-card`

Thumbnail card used by the image wall, Playground recent list, and video recent list.
Whole card is clickable (opens the image modal).

```css
.image-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(210px, 1fr)); gap: var(--sp-3); align-items: start; }
.image-card { display: flex; flex-direction: column; gap: 6px; background: #fff; border: 1px solid var(--line); border-radius: var(--radius-panel); overflow: hidden; cursor: pointer; transition: border-color .18s ease; }
.image-card:hover, .image-card:focus-visible { border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft); }
.image-card img { display: block; width: 100%; height: auto; object-fit: contain; background: #f4f6f9; }
```

## Data table — `.table-wrap` + `table`

Horizontal scroll wrapper; sticky last column for the history table.

```css
.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; }
th, td { text-align: left; padding: 7px 10px; border-bottom: 1px solid var(--line); font-size: 13px; vertical-align: top; }
th { color: var(--muted); font-weight: 500; white-space: nowrap; }
tbody tr:hover { background: var(--accent-soft); }
```

## Modal — `.image-overlay` / `.image-modal` / `.modal-head` / `.modal-body`

Full-screen backdrop, centered white panel, head + scrolling body.

```css
.image-overlay { position: fixed; inset: 0; display: grid; place-items: center; background: rgba(14,19,26,0.62); padding: 20px; z-index: 100; overflow: auto; }
.image-modal { max-width: min(1000px, 90vw); max-height: 90vh; overflow: auto; background: #fff; border-radius: var(--radius-card); padding: var(--sp-4); }
.modal-head { display: flex; justify-content: space-between; align-items: center; gap: var(--sp-3); margin-bottom: var(--sp-3); }
.modal-body { display: flex; flex-direction: column; gap: var(--sp-3); overflow: auto; max-height: 78vh; }
```

## Section group — `.form-section` + `.form-section-title`

Grouped form blocks separated by a hairline.

```css
.form-section + .form-section { margin-top: var(--sp-4); padding-top: var(--sp-3); border-top: 1px solid var(--line); }
.form-section-title { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; margin-bottom: var(--sp-2); font-size: 12px; font-weight: 600; color: var(--muted); }
```

## Subject card — `.subject-card`

Compact subject-profile picker card: mono id + muted display name, two lines.

```css
.subject-cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(185px, 1fr)); gap: var(--sp-2); }
.subject-card { display: flex; flex-direction: column; align-items: stretch; gap: 3px; padding: 8px 11px; min-width: 0; min-height: 0; text-align: left; }
.subject-card .sc-id { font-family: var(--mono); font-size: 12px; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.subject-card .sc-name { font-size: 12px; color: var(--muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.subject-card.active { background: var(--accent-soft); border-color: var(--accent-line); }
.subject-card.active .sc-id { color: var(--accent); }
```

## Markdown view — `.md-view` (and `.prompt-view`, `.md-edit` for the editor modals)

```css
.md-view { border: 1px solid var(--line); border-radius: var(--radius-panel); background: #fff; padding: 14px 20px var(--sp-4); max-height: 62vh; overflow: auto; font-size: 13px; line-height: 1.7; }
.md-view pre { font-family: var(--mono); font-size: 12px; line-height: 1.6; background: #f4f6f9; border: 1px solid var(--line); border-radius: var(--radius-ctl); padding: 10px 12px; overflow: auto; white-space: pre-wrap; word-break: break-word; }
.md-view code { font-family: var(--mono); font-size: 12px; background: #f1f3f6; padding: 1px 5px; border-radius: 4px; }
```

## Shared tables — `web/static/tables.js`

```js
const DREAMINA_MODEL_RESOLUTIONS = {
  "3.0": ["1k", "2k"], "3.1": ["1k", "2k"],
  "4.0": ["2k", "4k"], "4.1": ["2k", "4k"], "4.5": ["2k", "4k"], "4.6": ["2k", "4k"], "4.7": ["2k", "4k"],
  "5.0": ["2k", "4k"], "5.0Pro": ["1.5k", "2k", "4k"],
};
const DREAMINA_T2I_ONLY_MODELS = new Set(["3.0", "3.1"]);
```
