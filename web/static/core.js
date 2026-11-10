/* 全站共享的骨架（真多页版）：
   $/$$/api/apiUpload/setStatus/escapeHtml/SLOT_* 是各页 js 共同依赖的全局，
   全部集中这里定义 —— 页面 js 文件不再各自定义（真多页后同页不重名、跨页不共享，
   唯一例外都是「多页共用的东西」→ 归 core.js）。
   本文件的脚本标签在每页 script 清单里恒为第一位。 */

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

async function api(path, options = {}) {
  const resp = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      const data = await resp.json();
      detail = data.detail || JSON.stringify(data);
    } catch {}
    throw new Error(`${resp.status}: ${detail}`);
  }
  return resp.json();
}

/* 「最近生成」分页用：列表端点返回裸数组（老调用方兼容），总数在 X-Total-Count 响应头。 */
async function apiPaged(path, options = {}) {
  const resp = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      const data = await resp.json();
      detail = data.detail || JSON.stringify(data);
    } catch {}
    throw new Error(`${resp.status}: ${detail}`);
  }
  return { items: await resp.json(), total: Number(resp.headers.get("X-Total-Count") || 0) };
}

/* 「最近生成」分页条（Playground / 视频 / 语音三页共用）。page 从 0 起，goto(newPage) 翻页，
   返回值插到产物 grid 之后（.card > * + * 的间距规则自动生效）。 */
function buildRecentPager({ page, pageSize, total, goto }) {
  const row = document.createElement("div");
  row.className = "pager-row";
  const pages = Math.ceil(total / pageSize) || 1;
  const prev = document.createElement("button");
  prev.type = "button";
  prev.textContent = "上一页";
  prev.disabled = page <= 0;
  const info = document.createElement("span");
  info.className = "pager-info";
  info.textContent = `第 ${page + 1} / ${pages} 页 · 共 ${total} 条`;
  const next = document.createElement("button");
  next.type = "button";
  next.textContent = "下一页";
  next.disabled = page + 1 >= pages;
  prev.addEventListener("click", () => goto(page - 1));
  next.addEventListener("click", () => goto(page + 1));
  row.append(prev, info, next);
  return row;
}

/* 分页条替换式刷新：老条不在就插到 grid 之后（保持它在 card 里的最后一位）。 */
function refreshPager(grid, pagerId, payload) {
  const pager = buildRecentPager(payload);
  pager.id = pagerId;
  const old = document.getElementById(pagerId);
  if (old) old.replaceWith(pager);
  else grid.after(pager);
}

async function apiUpload(path, file) {
  const form = new FormData();
  form.append("file", file);
  const resp = await fetch(path, { method: "POST", body: form });
  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      const data = await resp.json();
      detail = data.detail || JSON.stringify(data);
    } catch {}
    throw new Error(`${resp.status}: ${detail}`);
  }
  return resp.json();
}

function setStatus(el, text, isError = false) {
  el.textContent = text;
  el.classList.toggle("error", isError);
}

function escapeHtml(text) {
  return String(text ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

/* 库浏览页共享的搜索命中（posegallery/costumegallery/facegallery 三处同一语义）：
   q 按空白切 token、全部 AND 子串命中 hay（大小写不敏感）。glossary 可选
   （日文词 → 英文关键词的映射，costume 库用）：token 未能直中时按词表扩写再试
   —— 词表键是词/短语都能对上，「黒」对上「黒ドレス」这种连写也走包含判断。 */
function matchesQuery(hay, q, glossary) {
  const hayLc = String(hay ?? "").toLowerCase();
  const toks = String(q ?? "").toLowerCase().split(/\s+/).filter(Boolean);
  for (const t of toks) {
    let ok = hayLc.indexOf(t) !== -1;
    if (!ok && glossary) {
      for (const ja in glossary) {
        if (t.indexOf(ja) !== -1 && hayLc.indexOf(glossary[ja].toLowerCase()) !== -1) {
          ok = true;
          break;
        }
      }
    }
    if (!ok) return false;
  }
  return true;
}

/* 卡片小动作按钮的线性 SVG 图标（14px，stroke: currentColor；全站唯一图标来源，
   design-system.md 的卡级动作按钮规范）。icon(name) 返回可直接 innerHTML 的 svg 字符串。 */
const ICON_PATHS = {
  play: '<path d="M3 2.5l12 7.5-12 7.5z" fill="currentColor" stroke="none"/>',
  download: '<path d="M10 2v11m0 0l-4.5-4.5M10 13l4.5-4.5M3.5 18h13" fill="none"/>',
  eye: '<path d="M1.8 10s3.2-5.4 8.2-5.4S18.2 10 18.2 10 15 15.4 10 15.4 1.8 10 1.8 10z" fill="none"/><circle cx="10" cy="10" r="2.6" fill="none"/>',
};
const ICON_NAMES = Object.keys(ICON_PATHS);
function icon(name) {
  const body = ICON_PATHS[name];
  if (!body) return "";
  return `<svg viewBox="0 0 20 20" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${body}</svg>`;
}

/* 主体档案 5 种槽位的文案（gen 页 sticker 提示与主体页槽位共用） */
const SLOT_LABELS = {
  character_sheet: "人物半身像＋三视图",
  headshot: "人物半身像",
  front: "全身正面",
  side: "全身侧面",
  behind: "全身背面",
};
const SLOT_HINTS = {
  character_sheet: "文生图，第一步",
  headshot: "基于三视图编辑",
  front: "基于三视图编辑",
  side: "基于三视图编辑",
  behind: "基于三视图编辑",
};