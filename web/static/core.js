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