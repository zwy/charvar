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