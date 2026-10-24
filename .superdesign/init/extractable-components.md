# Extractable components — charvar

Vanilla HTML/CSS app: every "component" is a CSS class plus an HTML fragment. The
extraction below lists the shared pieces worth turning into `<sd-component>` tags so
repeated drafts stay visually consistent.

## TopNav
- Source: `web/static/index.html` (header, lines 3–4) + `web/static/style.css` (`.topbar`, `.brand`, `nav`, `.tab`)
- Category: layout
- Description: Sticky top bar — wordmark on the left, tab buttons on the right.
- Extractable props: `activeTab` (string, defaultValue: "gen")
- Hardcoded: brand text `charvar` + subtitle `角色变体提示词生成器`, all nav item labels
  (预设 / Playground / 视频 / 配方 / 主体档案 / 历史记录 / 设置), all CSS

## Card
- Source: `web/static/style.css` (`.card`, `.card > * + *`, `.card h2`)
- Category: basic
- Description: White surface with 1px border, 12px radius, `18px 20px` padding, one
  `--sp-4` rhythm layer between direct children.
- Extractable props: none
- Hardcoded: all CSS

## CardHead
- Source: `web/static/style.css` (`.row-head`)
- Category: basic
- Description: Title + optional right-side actions/hint row.
- Extractable props: none
- Hardcoded: all CSS

## Segmented
- Source: `web/static/style.css` (`.segmented`, `.seg`, `.seg-sm`)
- Category: basic
- Description: Grey-track segmented control with a white active key.
- Extractable props: `activeValue` (string, defaultValue: ""), `size` (string, defaultValue: "md" → `seg-sm` when "sm")
- Hardcoded: all CSS

## FormField
- Source: `web/static/style.css` (`label`, `label input/select/textarea`, `label.check`)
- Category: basic
- Description: Label-wrapped control; label text is muted 12px above a 35px control.
- Extractable props: `type` (string, defaultValue: "text" → select/number/checkbox variants)
- Hardcoded: all CSS

## FormGrid
- Source: `web/static/style.css` (`.form-grid`)
- Category: basic
- Description: Auto-fill grid, min 220px columns, 12px/16px gaps.
- Extractable props: none
- Hardcoded: all CSS

## FormNote
- Source: `web/static/style.css` (`.form-note`)
- Category: basic
- Description: Full-row pale explanatory paragraph inside a form area.
- Extractable props: none
- Hardcoded: all CSS

## Hint
- Source: `web/static/style.css` (`.hint`)
- Category: basic
- Description: Muted 12px secondary text. Inline by default.
- Extractable props: none
- Hardcoded: all CSS

## DashedPanel
- Source: `web/static/style.css` (`.comfy-panel`)
- Category: basic
- Description: Dashed-border sub-panel with denser internal rhythm.
- Extractable props: none
- Hardcoded: all CSS

## Actions
- Source: `web/static/style.css` (`.actions`)
- Category: basic
- Description: Wrapping horizontal button row, 10px gap.
- Extractable props: none
- Hardcoded: all CSS

## RefPick
- Source: `web/static/genrefs.js` (render function, lines 38–81) + `web/static/style.css` (`.ref-pick`)
- Category: basic
- Description: Thumbnail card for one selected reference image, with optional role
  `<select>` and a remove button.
- Extractable props: `role` (string, defaultValue: "front"), `index` (number, defaultValue: 1)
- Hardcoded: role option labels (全身正面（体型/服装/鞋）/ 半身（脸/发型）/ 侧面（朝向/贴身细节）/ 背面（后脑/背部）/ 设定图（整套）), `#N` badge, 「移除」button text, all CSS

## ImageCard
- Source: `web/static/style.css` (`.image-card`, `.image-grid`)
- Category: basic
- Description: Clickable thumbnail card used by every image wall.
- Extractable props: none
- Hardcoded: all CSS

## Table
- Source: `web/static/style.css` (`.table-wrap`, `table`, `th`, `td`)
- Category: basic
- Description: Horizontally scrollable data table with a sticky last column option.
- Extractable props: `stickyLastColumn` (boolean, defaultValue: false)
- Hardcoded: all CSS

## Modal
- Source: `web/static/style.css` (`.image-overlay`, `.image-modal`, `.modal-head`, `.modal-body`)
- Category: basic
- Description: Backdrop + centered white panel with head and scrolling body.
- Extractable props: none
- Hardcoded: all CSS

## Chip
- Source: `web/static/style.css` (`.chip`, `.chip-row`)
- Category: basic
- Description: Pill-shaped multi-select filter.
- Extractable props: `active` (boolean, defaultValue: false)
- Hardcoded: all CSS

## Badge
- Source: `web/static/style.css` (`.badge`, `.badge-ready`, `.badge-queued`, `.badge-error`)
- Category: basic
- Description: Pill state label.
- Extractable props: `variant` (string, defaultValue: "default" → ready/queued/error)
- Hardcoded: all CSS

## StatusText
- Source: `web/static/style.css` (`.status`, `.status.error`)
- Category: basic
- Description: Inline 13px message next to an action.
- Extractable props: `error` (boolean, defaultValue: false)
- Hardcoded: all CSS

## LogBlock
- Source: `web/static/style.css` (`.log`)
- Category: basic
- Description: Monospace scrollable pre block for CLI-style output.
- Extractable props: none
- Hardcoded: all CSS

## SubjectCard
- Source: `web/static/style.css` (`.subject-cards`, `.subject-card`)
- Category: basic
- Description: Two-line subject-profile picker card (mono id + muted name).
- Extractable props: `active` (boolean, defaultValue: false)
- Hardcoded: all CSS
