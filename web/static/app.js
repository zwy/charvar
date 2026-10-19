/* charvar 管理页逻辑：生成 / 主体档案 / 历史 / ComfyUI 入队 */
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const state = {
  subjects: [],
  presets: [],
  presetDetails: {},
  currentSubject: null,
  lastRun: null, // GenerateResult
  genMode: "classic",
  subjectImages: [], // 主体档案图片槽位（当前主体）
};

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

/* ---------- tabs ---------- */
$$(".tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    $$(".tab").forEach((b) => b.classList.toggle("active", b === btn));
    $$(".tab-page").forEach((p) => p.classList.toggle("active", p.id === `tab-${btn.dataset.tab}`));
    if (btn.dataset.tab === "runs") loadRuns();
    if (btn.dataset.tab === "settings") loadSettings();
    if (btn.dataset.tab === "gen" && state.genMode === "llm") renderRefPickers();
  });
});

/* ---------- 生成模式（经典 / LLM 优化） ---------- */
function setGenMode(mode) {
  state.genMode = mode;
  $$("#gen-mode-classic, #gen-mode-llm").forEach((btn) =>
    btn.classList.toggle("active", btn.dataset.mode === mode));
  $("#gen-llm-panel").hidden = mode !== "llm";
  if (mode === "llm") renderRefPickers();
}

$("#gen-mode-classic").addEventListener("click", () => setGenMode("classic"));
$("#gen-mode-llm").addEventListener("click", () => setGenMode("llm"));

function selectedRefKinds() {
  return $$("#gen-refs .ref-pick.active").map((el) => el.dataset.kind);
}

async function renderRefPickers() {
  const sid = $("#gen-subject").value;
  const box = $("#gen-refs");
  if (!sid) {
    box.innerHTML = `<span class="hint">先选择主体档案</span>`;
    return;
  }
  try {
    const data = await api(`/subjects/${encodeURIComponent(sid)}/images`);
    state.refImages = data.slots;
  } catch {
    box.innerHTML = `<span class="hint">参考图加载失败</span>`;
    return;
  }
  box.innerHTML = "";
  for (const slot of state.refImages) {
    const el = document.createElement("button");
    el.type = "button";
    el.className = "ref-pick";
    el.dataset.kind = slot.kind;
    el.disabled = !slot.is_ready;
    const thumb = slot.thumb_url || slot.original_url;
    el.innerHTML = `${thumb ? `<img src="${thumb}" alt="">` : `<span class="ref-thumb-empty"></span>`}
      <span class="ref-name">${slot.label}</span>
      <span class="badge badge-${slot.status}">${slot.is_ready ? "就绪" : slot.status === "empty" ? "空" : slot.status}</span>`;
    el.addEventListener("click", () => {
      if (el.classList.contains("active")) {
        el.classList.remove("active");
      } else {
        if (selectedRefKinds().length >= 4) return; // 最多 4 张参考图
        el.classList.add("active");
      }
    });
    const img = el.querySelector("img");
    if (img) {
      img.title = "预览大图（卡片其余区域为选择/取消）";
      img.addEventListener("click", (ev) => {
        ev.stopPropagation();
        Media.showImageModal(slot.original_url, slot.label);
      });
    }
    box.appendChild(el);
  }
}

/* ---------- 初始加载 ---------- */
async function init() {
  await Promise.allSettled([loadSubjects(), loadPresets()]);
  if (state.subjects.length) {
    $("#gen-subject").value = state.subjects[0].id;
  }
  $("#gen-subject").addEventListener("change", () => {
    if (state.genMode === "llm") renderRefPickers();
  });
}

async function loadSubjects() {
  state.subjects = await api("/subjects");
  const sel = $("#gen-subject");
  sel.innerHTML = "";
  for (const s of state.subjects) {
    const opt = document.createElement("option");
    opt.value = s.id;
    opt.textContent = `${s.id} · ${s.name}`;
    sel.appendChild(opt);
  }
  renderSubjectList();
}

async function loadPresets() {
  state.presets = await api("/presets");
  const sel = $("#gen-preset");
  sel.innerHTML = "";
  for (const p of state.presets) {
    const opt = document.createElement("option");
    opt.value = p.name;
    opt.textContent = `${p.name}（${p.type === "list" ? "逐条" : "矩阵"} · ${p.count} 条）`;
    sel.appendChild(opt);
  }
  sel.addEventListener("change", () => fillPresetOptions(sel.value).catch(() => {}));
  if (state.presets.some((p) => p.name === "q21_default_30")) {
    sel.value = "q21_default_30";
  }
  if (state.presets.length) await fillPresetOptions(sel.value);
}

async function fillPresetOptions(name) {
  if (!state.presetDetails[name]) {
    state.presetDetails[name] = await api(`/presets/${encodeURIComponent(name)}`);
  }
  const detail = state.presetDetails[name];
  $("#bg-options").innerHTML = detail.backgrounds.map((v) => `<option value="${v}"></option>`).join("");
  $("#light-options").innerHTML = detail.lighting.map((v) => `<option value="${v}"></option>`).join("");
  $("#gen-count").placeholder = `留空 = 全部 ${detail.count} 条`;
}

/* ---------- 生成 ---------- */
$("#btn-generate").addEventListener("click", async () => {
  if (!state.subjects.length) return setStatus($("#gen-status"), "先去「主体档案」建一个档案", true);
  const payload = {
    subject_id: $("#gen-subject").value,
    preset: $("#gen-preset").value,
    seed: Number($("#gen-seed").value || 0),
    sampling: $("#gen-sampling").value,
    randomize_background: $("#gen-rand-bg").checked,
    randomize_lighting: $("#gen-rand-light").checked,
  };
  if ($("#gen-count").value) payload.count = Number($("#gen-count").value);
  if ($("#gen-fixed-bg").value.trim()) payload.fixed_background = $("#gen-fixed-bg").value.trim();
  if ($("#gen-fixed-light").value.trim()) payload.fixed_lighting = $("#gen-fixed-light").value.trim();
  const sep = $("#gen-separator").value;
  if (sep !== " ") payload.separator = sep;
  payload.mode = state.genMode;
  if (state.genMode === "llm") {
    payload.reference_kinds = selectedRefKinds();
    if (!payload.reference_kinds.length) {
      return setStatus($("#gen-status"), "LLM 模式需要选择 1–4 张参考图", true);
    }
    if ($("#gen-llm-instructions").value.trim()) {
      payload.llm_instructions = $("#gen-llm-instructions").value.trim();
    }
  }
  try {
    setStatus($("#gen-status"), "生成中…");
    state.lastRun = await api("/generate", { method: "POST", body: JSON.stringify(payload) });
    setStatus($("#gen-status"), `完成：${state.lastRun.items.length} 条（run_id: ${state.lastRun.run_id}）`);
    renderResult(state.lastRun);
    Media.setView("images");
    Media.loadImages(state.lastRun.run_id)
      .then((data) => Media.setStatus(`图片 ${data.completed}/${data.items.length} 完成${data.errors ? `，失败 ${data.errors}` : ""}`))
      .catch(() => Media.setStatus("图片加载失败", true));
  } catch (err) {
    setStatus($("#gen-status"), `失败：${err.message}`, true);
  }
});

function renderResult(result) {
  const run = result.run_id;
  $("#result-card").hidden = false;
  const subjectOk = state.subjects.find((s) => s.id === result.subject_id);
  $("#result-summary").textContent = `主体 ${result.subject_id}${subjectOk ? `（${subjectOk.name}）` : ""} · 预设 ${result.preset} · 种子 ${result.seed} · ${result.items.length} 条`;
  for (const [id, fmt] of [["dl-txt", "txt"], ["dl-jsonl", "jsonl"], ["dl-zip", "captions_zip"]]) {
    const link = $(`#${id}`);
    link.href = `/runs/${encodeURIComponent(run)}/export?format=${fmt}&download=1`;
    link.hidden = false;
  }
  const tbody = $("#result-table tbody");
  tbody.innerHTML = "";
  for (const item of result.items) {
    const tr = document.createElement("tr");
    const m = item.meta || {};
    const promptCell = `<td class="copy-cell" title="点击复制">${escapeHtml(item.prompt)}</td>`;
    const capCell = `<td class="copy-cell" title="点击复制" data-caption="1"><span class="caption">${escapeHtml(item.caption)}</span></td>`;
    tr.innerHTML = `<td>${item.index}</td><td>${escapeHtml(item.variant_id)}</td>
      <td>${escapeHtml(m.framing || "-")}</td><td>${escapeHtml(m.angle || "-")}</td><td>${escapeHtml(m.expression || "-")}</td>
      <td>${escapeHtml(item.background || "—")}</td><td>${escapeHtml(item.lighting || "—")}</td>
      ${promptCell}${capCell}`;
    tbody.appendChild(tr);
  }
  // 点击复制
  tbody.onclick = (ev) => {
    const cell = ev.target.closest(".copy-cell");
    if (!cell) return;
    const text = cell.dataset.caption
      ? cell.querySelector(".caption").textContent
      : cell.textContent;
    navigator.clipboard.writeText(text.trim());
  };
}

function escapeHtml(text) {
  return String(text ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

$("#btn-copy-prompts").addEventListener("click", () => {
  if (!state.lastRun) return;
  navigator.clipboard.writeText(state.lastRun.items.map((i) => i.prompt).join("\n"));
  setStatus($("#gen-status"), "已复制全部提示词");
});

$("#btn-copy-captions").addEventListener("click", () => {
  if (!state.lastRun) return;
  navigator.clipboard.writeText(state.lastRun.items.map((i) => i.caption).join("\n"));
  setStatus($("#gen-status"), "已复制全部 caption");
});

/* ---------- ComfyUI ---------- */
$("#btn-toggle-comfy").addEventListener("click", async () => {
  $("#comfy-panel").hidden = !$("#comfy-panel").hidden;
  if (!$("#comfy-panel").hidden && !state.comfyLoaded) await refreshComfyWorkflows().catch(() => {});
  // LLM 模式默认选图片编辑 workflow
  if (!$("#comfy-panel").hidden && state.genMode === "llm") {
    try {
      const settings = await api("/settings");
      if (settings.comfy_edit_workflow) $("#comfy-workflow").value = settings.comfy_edit_workflow;
    } catch {}
  }
});

async function refreshComfyWorkflows() {
  const info = await api("/comfy/workflows");
  state.comfyLoaded = true;
  $("#comfy-url").value = info.base_url || $("#comfy-url").value;
  const sel = $("#comfy-workflow");
  sel.innerHTML = "";
  state.comfyWorkflows = (info.workflows || []).filter((w) => w.has_workflow && w.has_mapping);
  for (const w of state.comfyWorkflows) {
    const opt = document.createElement("option");
    opt.value = w.name;
    opt.textContent = w.name;
    sel.appendChild(opt);
  }
  if (!state.comfyWorkflows.length) {
    sel.innerHTML = `<option value="">（${info.workflows_dir} 没有可用的成对 workflow）</option>`;
  }
}

let ASPECT_RATIO = "auto";
const ASPECT_BUTTONS = $$("#comfy-aspect .chip");
const DIM_PREVIEW = $("#comfy-dim-preview");
const TIER_TO_SIDE = { "1k": 1024, "2k": 2048, "4k": 4096 };

function updateComfyDimensionPreview() {
  const ratio = ASPECT_RATIO;
  const longSide = TIER_TO_SIDE[$("#comfy-resolution").value];
  if (ratio === "auto") {
    DIM_PREVIEW.textContent = "智能：全身 2:3，胸像/头像/表情 3:4。以上按长边档位计算。";
    return;
  }
  const parts = ratio.split(":").map((n) => Number(n.trim()));
  if (parts.length !== 2 || parts.some((n) => !Number.isFinite(n) || n <= 0)) {
    DIM_PREVIEW.textContent = "比例无效"; return;
  }
  const [w, h] = parts;
  const shortSide = Math.max(64, Math.round((longSide * Math.min(w, h)) / Math.max(w, h) / 64) * 64);
  const width = w >= h ? longSide : shortSide;
  const height = h >= w ? longSide : shortSide;
  DIM_PREVIEW.textContent = `当前输出 ${width} × ${height}px（4K 长边上限 4096）`;
}

function setAspect(value) {
  ASPECT_RATIO = value;
  ASPECT_BUTTONS.forEach((btn) => btn.classList.toggle("active", btn.dataset.ratio === value));
  updateComfyDimensionPreview();
}

ASPECT_BUTTONS.forEach((btn) => {
  btn.addEventListener("click", () => setAspect(btn.dataset.ratio || "auto"));
});
$("#comfy-resolution").addEventListener("change", updateComfyDimensionPreview);
setAspect("auto");
Media.setView("images");

$("#btn-comfy-status").addEventListener("click", async () => {
  setStatus($("#comfy-result"), "检查中…");
  try {
    const q = encodeURIComponent($("#comfy-url").value.trim());
    const info = await api(`/comfy/status?comfy_url=${q}`);
    const sys = info.system_stats?.system || {};
    setStatus($("#comfy-result"), `已连接：ComfyUI ${sys.comfyui_version || ""}（${sys.os || "?"}）`);
  } catch (err) {
    setStatus($("#comfy-result"), `连接失败：${err.message}`, true);
  }
});

$("#btn-comfy-queue").addEventListener("click", async () => {
  if (!state.lastRun) return;
  const workload = $("#comfy-workflow").value;
  if (!workload) return setStatus($("#comfy-result"), "先在 data/workflows/ 放入 workflow + mapping.json 成对文件", true);
  setStatus($("#comfy-result"), "提交中…");
  try {
    const body = {
      workload,
      negative_prompt: $("#comfy-negative").value.trim() || null,
      wait: $("#comfy-wait").checked,
      comfy_url: $("#comfy-url").value.trim(),
      aspect_ratio: ASPECT_RATIO,
      resolution: $("#comfy-resolution").value,
    };
    if (state.genMode === "llm") body.reference_kinds = selectedRefKinds();
    const out = await api(`/runs/${encodeURIComponent(state.lastRun.run_id)}/comfyui`, {
      method: "POST",
      body: JSON.stringify(body),
    });
    $("#comfy-result").textContent =
      `成功 ${out.succeeded} / 失败 ${out.failed}\n` +
      out.submissions.map((s) => {
        const head = `#${String(s.index).padStart(3, "0")} ${s.variant_id}`;
        if (!s.ok) return `${head} × ${s.error}`;
        const files = (s.files || []).map((f) => f.filename).join(", ");
        return `${head} → prompt_id ${s.prompt_id}${files ? ` → ${files}` : ""}`;
      }).join("\n");
    Media.syncImages(state.lastRun.run_id).then((data) => {
      Media.setStatus(`图片 ${data.completed}/${data.items.length} 完成${data.errors ? `，失败 ${data.errors}` : ""}`);
    }).catch((error) => Media.setStatus(`同步失败：${error.message}`, true));
  } catch (err) {
    setStatus($("#comfy-result"), `失败：${err.message}`, true);
  }
});

/* ---------- 主体档案 ---------- */
function renderSubjectList() {
  const ul = $("#subject-list");
  ul.innerHTML = "";
  for (const s of state.subjects) {
    const li = document.createElement("li");
    li.innerHTML = `<span>${s.id}</span><span class="meta">${s.name}</span>`;
    if (state.currentSubject === s.id) li.classList.add("active");
    li.addEventListener("click", () => fillSubjectForm(s));
    ul.appendChild(li);
  }
}

function fillSubjectForm(s) {
  state.currentSubject = s.id;
  renderSubjectList();
  $("#subject-form-title").textContent = `档案详情：${s.id}`;
  $("#s-id").value = s.id;
  $("#s-name").value = s.name;
  $("#s-noun").value = s.subject_noun;
  $("#s-trigger").value = s.trigger_word || "";
  $("#s-style").value = (s.style_lock || []).join("\n");
  $("#s-identity").value = (s.identity_lock || []).join("\n");
  $("#s-outfit").value = (s.outfit_lock || []).join("\n");
  $("#s-extra").value = s.extra || "";
  loadSubjectImages();
  loadSubjectTemplate();
}

function clearSubjectForm() {
  state.currentSubject = null;
  renderSubjectList();
  $("#subject-form-title").textContent = "档案详情";
  ["s-id", "s-name", "s-trigger", "s-style", "s-identity", "s-outfit", "s-extra"].forEach((id) => ($(id).value = ""));
  $("#s-noun").value = "person";
  $("#subject-images-card").hidden = true;
  $("#template-card").hidden = true;
}

function collectSubjectFromForm() {
  const list = (id) => $(id).value.split("\n").map((v) => v.trim()).filter(Boolean);
  return {
    id: $("#s-id").value.trim(),
    name: $("#s-name").value.trim(),
    subject_noun: $("#s-noun").value.trim() || "person",
    trigger_word: $("#s-trigger").value.trim() || null,
    style_lock: list("#s-style"),
    identity_lock: list("#s-identity"),
    outfit_lock: list("#s-outfit"),
    extra: $("#s-extra").value.trim(),
  };
}

$("#btn-subject-new").addEventListener("click", clearSubjectForm);

$("#btn-subject-save").addEventListener("click", async () => {
  const profile = collectSubjectFromForm();
  if (!profile.id || !profile.name) return setStatus($("#gen-status"), "档案需要 ID 与显示名", true);
  try {
    if (state.currentSubject === profile.id) {
      await api(`/subjects/${encodeURIComponent(profile.id)}`, { method: "PUT", body: JSON.stringify(profile) });
    } else {
      await api("/subjects", { method: "POST", body: JSON.stringify(profile) });
    }
    await loadSubjects();
    fillSubjectForm(Object.assign(profile, await api(`/subjects/${encodeURIComponent(profile.id)}`)));
    setStatus($("#gen-status"), `已保存档案：${profile.id}`);
  } catch (err) {
    setStatus($("#gen-status"), `保存失败：${err.message}`, true);
  }
});

$("#btn-subject-delete").addEventListener("click", async () => {
  const id = $("#s-id").value.trim();
  if (!id) return;
  try {
    await api(`/subjects/${encodeURIComponent(id)}`, { method: "DELETE" });
    clearSubjectForm();
    await loadSubjects();
    setStatus($("#gen-status"), `已删除档案：${id}`);
  } catch (err) {
    setStatus($("#gen-status"), `删除失败：${err.message}`, true);
  }
});

/* 极简 YAML 解析（主体档案的扁平 schema） */
function parseSimpleYaml(text) {
  const data = {};
  let lastKey = null;
  for (let raw of text.split("\n")) {
    const line = raw.replace(/\s+#.*$/, "").trimEnd();
    if (!line.trim() || line.trim().startsWith("#")) continue;
    const indent = raw.length - raw.trimStart().length;
    const item = line.trim();
    if (item.startsWith("- ") && lastKey) {
      if (!Array.isArray(data[lastKey])) data[lastKey] = [];
      data[lastKey].push(unquote(item.slice(2).trim()));
      continue;
    }
    const idx = item.indexOf(":");
    if (idx === -1) continue;
    const key = item.slice(0, idx).trim();
    let value = item.slice(idx + 1).trim();
    lastKey = key;
    if (!value) { data[key] = value; continue; }
    if (value.startsWith("[")) {
      const inner = value.replace(/^\[|\]$/g, "");
      data[key] = inner.split(",").map((v) => unquote(v.trim())).filter(Boolean);
    } else {
      data[key] = unquote(value);
    }
  }
  return data;
}

function unquote(text) {
  if ((text.startsWith('"') && text.endsWith('"')) || (text.startsWith("'") && text.endsWith("'"))) {
    return text.slice(1, -1);
  }
  return text;
}

$("#btn-subject-import").addEventListener("click", async () => {
  const data = parseSimpleYaml($("#s-yaml").value);
  try {
    await api("/subjects", { method: "POST", body: JSON.stringify(data) });
    await loadSubjects();
    fillSubjectForm(await api(`/subjects/${encodeURIComponent(data.id)}`));
    setStatus($("#gen-status"), `已导入档案：${data.id}`);
  } catch (err) {
    setStatus($("#gen-status"), `导入失败：${err.message}`, true);
  }
});

/* ---------- 主体档案图片（5 种槽位） ---------- */
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

async function loadSubjectImages() {
  const sid = state.currentSubject;
  if (!sid) return;
  $("#subject-images-card").hidden = false;
  $("#subject-image-slots").innerHTML = `<span class="hint">加载中…</span>`;
  try {
    state.subjectImages = (await api(`/subjects/${encodeURIComponent(sid)}/images`)).slots;
  } catch (err) {
    $("#subject-image-slots").innerHTML = `<span class="status error">加载失败：${escapeHtml(err.message)}</span>`;
    return;
  }
  if (state.currentSubject !== sid) return; // 期间切了主体
  renderSubjectImageSlots();
}

function slotStatusText(slot) {
  if (slot.status === "ready") return slot.source === "uploaded" ? "已上传" : "已生成";
  if (slot.status === "queued") return "生成中（可点同步）";
  if (slot.status === "error") return slot.error || "出错";
  return "空";
}

function renderSubjectImageSlots() {
  const wrap = $("#subject-image-slots");
  wrap.innerHTML = "";
  for (const slot of state.subjectImages) {
    const card = document.createElement("div");
    card.className = "subject-image-slot";
    card.dataset.slotKind = slot.kind; // 供工具栏批量操作定位单个卡片
    const thumb = slot.thumb_url || slot.original_url;
    card.innerHTML = `
      <div class="slot-thumb">${thumb ? `<img src="${thumb}?t=${Date.now()}" alt="${slot.kind}" loading="lazy">` : `<div class="image-placeholder slot-placeholder"><span>空</span></div>`}</div>
      <div class="slot-body">
        <div class="slot-head"><strong>${SLOT_LABELS[slot.kind] || slot.kind}</strong>
          <span class="badge badge-${slot.status}">${slotStatusText(slot)}</span></div>
        <span class="hint">${SLOT_HINTS[slot.kind] || ""} · 默认 ${slot.aspect_ratio} / ${slot.resolution}</span>
        <textarea class="slot-prompt" rows="3" placeholder="提示词（生成 / 编辑指令）">${escapeHtml(slot.prompt || "")}</textarea>
        <div class="slot-actions">
          <button data-act="view" data-kind="${slot.kind}">查看</button>
          <button data-act="copy" data-kind="${slot.kind}">复制</button>
          <button data-act="suggest" data-kind="${slot.kind}">AI 起草</button>
          <button data-act="generate" data-kind="${slot.kind}" class="primary">${slot.is_ready ? "重新生成" : "生成"}</button>
          <label class="btn file-btn">上传<input type="file" data-upload="${slot.kind}" accept=".png,.jpg,.jpeg,.webp" hidden></label>
          ${slot.is_ready ? `<button data-act="delete" data-kind="${slot.kind}" class="danger">删除</button>` : ""}
          ${slot.status === "queued" ? `<button data-act="sync" data-kind="${slot.kind}">同步</button>` : ""}
        </div>
        <span class="slot-status status"></span>
      </div>`;
    const thumbImg = card.querySelector(".slot-thumb img");
    if (thumbImg) {
      thumbImg.title = "点击预览大图";
      thumbImg.addEventListener("click", () =>
        Media.showImageModal(slot.original_url, `${SLOT_LABELS[slot.kind] || slot.kind} · ${slotStatusText(slot)}`));
    }
    wrap.appendChild(card);
  }
}

$("#subject-image-slots").addEventListener("click", async (ev) => {
  const btn = ev.target.closest("button[data-act]");
  if (!btn) return;
  const kind = btn.dataset.kind;
  const card = btn.closest(".subject-image-slot");
  const statusEl = card.querySelector(".slot-status");
  const sid = state.currentSubject;
  if (!sid) return;
  const slot = state.subjectImages.find((s) => s.kind === kind);
  const promptEl = card.querySelector(".slot-prompt");
  const act = btn.dataset.act;
  try {
    if (act === "view") {
      const text = promptEl.value.trim();
      if (!text) {
        setStatus(statusEl, "提示词为空：先 AI 起草或手写");
        return;
      }
      Media.showTextModal(`${SLOT_LABELS[kind] || kind} · 提示词`, text, { copyable: true });
    } else if (act === "copy") {
      const text = promptEl.value.trim();
      if (!text) {
        setStatus(statusEl, "提示词为空：先 AI 起草或手写");
        return;
      }
      const ok = await copyText(text);
      setStatus(statusEl, ok ? "提示词已复制" : "复制失败（可在「查看」弹窗里复制）", !ok);
    } else if (act === "suggest") {
      setStatus(statusEl, "AI 起草中…");
      const data = await api(`/subjects/${encodeURIComponent(sid)}/images/${kind}/suggest-prompt`, { method: "POST" });
      promptEl.value = data.prompt;
      slot.prompt = data.prompt;
      setStatus(statusEl, "已起草，可修改后点生成");
    } else if (act === "generate") {
      btn.disabled = true;
      setStatus(statusEl, "提交 ComfyUI…");
      const updated = await api(`/subjects/${encodeURIComponent(sid)}/images/${kind}/generate`, {
        method: "POST",
        body: JSON.stringify({
          prompt: promptEl.value.trim() || null,
          seed: Number($("#slot-seed").value || 0), // 统一种子：工具栏唯一入口
        }),
      });
      setStatus(statusEl, updated.status === "queued" ? "已提交，稍后点「同步」取图" : "完成");
      await loadSubjectImages();
    } else if (act === "sync") {
      btn.disabled = true;
      setStatus(statusEl, "同步中…");
      await api(`/subjects/${encodeURIComponent(sid)}/images/${kind}/sync`, { method: "POST" });
      await loadSubjectImages();
    } else if (act === "delete") {
      if (!confirm(`删除 ${SLOT_LABELS[kind]} 的图片？`)) return;
      await api(`/subjects/${encodeURIComponent(sid)}/images/${kind}`, { method: "DELETE" });
      await loadSubjectImages();
    }
  } catch (err) {
    setStatus(statusEl, `失败：${err.message}`, true);
    btn.disabled = false;
  }
});

/* 工具栏：统一种子 + 一键 AI 起草（只补没有提示词的槽位，逐个串行调用） */
function slotCard(kind) {
  return $(`#subject-image-slots [data-slot-kind="${kind}"]`);
}

$("#btn-slots-draft").addEventListener("click", async () => {
  const sid = state.currentSubject;
  if (!sid) return;
  const statusEl = $("#slot-toolbar-status");
  const btn = $("#btn-slots-draft");
  const targets = state.subjectImages
    .filter((s) => !(s.prompt || "").trim())
    .map((s) => s.kind);
  if (!targets.length) {
    setStatus(statusEl, "5 个槽位都已有提示词，无需起草");
    return;
  }
  btn.disabled = true;
  const failed = [];
  for (let i = 0; i < targets.length; i += 1) {
    const kind = targets[i];
    setStatus(statusEl, `AI 起草中 ${i + 1}/${targets.length}：${SLOT_LABELS[kind] || kind}…`);
    const cardEl = slotCard(kind);
    const cardStatus = cardEl?.querySelector(".slot-status");
    if (cardStatus) setStatus(cardStatus, "AI 起草中…");
    try {
      const data = await api(`/subjects/${encodeURIComponent(sid)}/images/${kind}/suggest-prompt`, { method: "POST" });
      const slot = state.subjectImages.find((s) => s.kind === kind);
      slot.prompt = data.prompt;
      const ta = cardEl?.querySelector(".slot-prompt");
      if (ta) ta.value = data.prompt;
      if (cardStatus) setStatus(cardStatus, "已起草");
    } catch (err) {
      failed.push(kind);
      if (cardStatus) setStatus(cardStatus, `起草失败：${err.message}`, true);
    }
  }
  setStatus(statusEl, failed.length ? `起草完成，${failed.length} 个失败：${failed.join("、")}` : `起草完成 ${targets.length}/${targets.length} 个`);
  btn.disabled = false;
});

/* 提示词 textarea 失焦保存（change 事件）：没改动不请求。 */
async function saveSlotPrompt(textarea) {
  const card = textarea.closest(".subject-image-slot");
  const kind = card?.dataset.slotKind;
  const sid = state.currentSubject;
  if (!card || !kind || !sid) return;
  const slot = state.subjectImages.find((s) => s.kind === kind);
  const text = textarea.value.trim();
  if (slot && (slot.prompt || "") === text) return; // 与已保存内容一致
  const statusEl = card.querySelector(".slot-status");
  try {
    await api(`/subjects/${encodeURIComponent(sid)}/images/${kind}/prompt`, {
      method: "PUT",
      body: JSON.stringify({ prompt: text }),
    });
    if (slot) slot.prompt = text;
    setStatus(statusEl, "提示词已保存");
  } catch (err) {
    setStatus(statusEl, `提示词保存失败：${err.message}`, true);
  }
}

$("#subject-image-slots").addEventListener("change", async (ev) => {
  if (ev.target.classList?.contains("slot-prompt")) {
    await saveSlotPrompt(ev.target);
    return;
  }
  const input = ev.target.closest("input[data-upload]");
  if (!input || !input.files.length) return;
  const kind = input.dataset.upload;
  const sid = state.currentSubject;
  if (!sid) return;
  const card = input.closest(".subject-image-slot");
  const statusEl = card.querySelector(".slot-status");
  try {
    setStatus(statusEl, "上传中…");
    await apiUpload(`/subjects/${encodeURIComponent(sid)}/images/${kind}/upload`, input.files[0]);
    await loadSubjectImages();
  } catch (err) {
    setStatus(statusEl, `上传失败：${err.message}`, true);
  }
});

/* ---------- 通用提示词模板（展示层：渲染 markdown；编辑：弹窗 textarea） ---------- */
async function loadSubjectTemplate() {
  const sid = state.currentSubject;
  if (!sid) return;
  $("#template-card").hidden = false;
  setStatus($("#template-status"), "");
  try {
    const resp = await fetch(`/subjects/${encodeURIComponent(sid)}/template`);
    state.templateMd = resp.ok ? await resp.text() : "";
    if (!resp.ok && resp.status !== 404) throw new Error(resp.statusText);
  } catch (err) {
    state.templateMd = "";
    setStatus($("#template-status"), `读取失败：${err.message}`, true);
  }
  if (state.currentSubject !== sid) return; // 期间切了主体
  renderTemplateView();
}

function renderTemplateView() {
  const view = $("#template-md-view");
  const md = state.templateMd || "";
  view.hidden = false;
  if (md.trim()) {
    view.innerHTML = renderMarkdown(md);
  } else {
    view.innerHTML = `<span class="hint">（还没有模板——点「编辑模板」新建，或从 .md 文件载入）</span>`;
  }
}

/* 编辑弹窗：textarea + 保存模板 / 取消；initial 传入时优先（.md 文件载入场景）。 */
function openTemplateEditor(initial) {
  const start = initial !== undefined ? initial : state.templateMd || "";
  Media.showPanelModal(`编辑模板：${state.currentSubject}`, (body, close) => {
    const ta = document.createElement("textarea");
    ta.className = "md-edit";
    ta.rows = 20;
    ta.value = start;
    body.appendChild(ta);
    const row = document.createElement("div");
    row.className = "actions";
    const save = document.createElement("button");
    save.className = "primary";
    save.textContent = "保存模板";
    const cancel = document.createElement("button");
    cancel.textContent = "取消";
    const status = document.createElement("span");
    status.className = "status";
    row.append(save, cancel, status);
    body.appendChild(row);
    cancel.addEventListener("click", close);
    save.addEventListener("click", async () => {
      save.disabled = true;
      try {
        await api(`/subjects/${encodeURIComponent(state.currentSubject)}/template`, {
          method: "PUT",
          body: JSON.stringify({ template_md: ta.value }),
        });
        state.templateMd = ta.value;
        renderTemplateView();
        setStatus($("#template-status"), "模板已保存");
        close();
      } catch (err) {
        save.disabled = false;
        setStatus(status, `保存失败：${err.message}`, true);
      }
    });
  });
}

$("#btn-template-edit").addEventListener("click", () => {
  if (!state.currentSubject) return;
  openTemplateEditor();
});

$("#template-file").addEventListener("change", (ev) => {
  const file = ev.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => openTemplateEditor(String(reader.result || ""));
  reader.readAsText(file);
  ev.target.value = "";
});

/* ---------- 设置 ---------- */
const ASPECT_OPTIONS = ["3:4", "1:1", "16:9", "4:3", "9:16", "2:3", "3:2", "21:9"];

async function loadSettings() {
  try {
    const [settings, comfy] = await Promise.all([api("/settings"), api("/comfy/workflows")]);
    const names = (comfy.workflows || []).filter((w) => w.has_workflow && w.has_mapping).map((w) => w.name);
    for (const [sel, value] of [["#set-t2i", settings.comfy_t2i_workflow], ["#set-edit", settings.comfy_edit_workflow]]) {
      const el = $(sel);
      el.innerHTML = names.map((n) => `<option value="${n}">${n}</option>`).join("") ||
        `<option value="">（data/workflows 没有成对 workflow）</option>`;
      if (value && names.includes(value)) el.value = value;
    }
    $("#set-llm-base").value = settings.llm.base_url || "";
    $("#set-llm-model").value = settings.llm.model || "";
    $("#set-llm-key").value = "";
    $("#set-llm-key").placeholder = settings.llm_configured ? "已配置（留空保持不变）" : "留空 = 环境变量 LLM_API_KEY";
    $("#set-negative").value = settings.default_negative_prompt || "";
    const sizes = $("#settings-sizes");
    sizes.innerHTML = "";
    for (const [kind, size] of Object.entries(settings.image_sizes)) {
      const label = SLOT_LABELS[kind] || kind;
      const div = document.createElement("label");
      div.innerHTML = `${label}
        <div class="size-row">
          <select data-size-aspect="${kind}">${ASPECT_OPTIONS.map((r) => `<option value="${r}" ${r === size.aspect_ratio ? "selected" : ""}>${r}</option>`).join("")}</select>
          <select data-size-res="${kind}">${["1k", "2k", "4k"].map((r) => `<option value="${r}" ${r === size.resolution ? "selected" : ""}>${r}</option>`).join("")}</select>
        </div>`;
      sizes.appendChild(div);
    }
  } catch (err) {
    setStatus($("#settings-status"), `加载失败：${err.message}`, true);
  }
}

$("#btn-settings-save").addEventListener("click", async () => {
  const image_sizes = {};
  $$("#settings-sizes [data-size-aspect]").forEach((el) => {
    const kind = el.dataset.sizeAspect;
    image_sizes[kind] = {
      aspect_ratio: el.value,
      resolution: $(`[data-size-res="${kind}"]`).value,
    };
  });
  const patch = {
    comfy_t2i_workflow: $("#set-t2i").value,
    comfy_edit_workflow: $("#set-edit").value,
    image_sizes,
    default_negative_prompt: $("#set-negative").value.trim(),
  };
  // llm 三项整行发送（清空的 base_url/model 要能落库）；api_key 空串 = 后端保留已存值
  patch.llm = {
    api_key: $("#set-llm-key").value.trim(),
    base_url: $("#set-llm-base").value.trim(),
    model: $("#set-llm-model").value.trim(),
  };
  try {
    await api("/settings", { method: "PUT", body: JSON.stringify(patch) });
    setStatus($("#settings-status"), "设置已保存");
    await loadSettings();
  } catch (err) {
    setStatus($("#settings-status"), `保存失败：${err.message}`, true);
  }
});

/* ---------- 历史记录 ---------- */
async function loadRuns() {
  const runs = await api("/runs?limit=100");
  const tbody = $("#runs-table tbody");
  tbody.innerHTML = "";
  for (const r of runs) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td class="prompt">${escapeHtml(r.run_id)}</td><td>${escapeHtml(r.subject_id)}</td>
      <td>${escapeHtml(r.preset)}</td><td>${r.seed}</td><td>${r.item_count}</td>
      <td>${r.comfy_count}</td><td>${escapeHtml(r.created_at)}</td>
      <td><button data-run="${escapeHtml(r.run_id)}">查看</button>
          <button class="danger" data-del="${escapeHtml(r.run_id)}">删除</button></td>`;
    tbody.appendChild(tr);
  }
  tbody.querySelectorAll("button[data-run]").forEach((btn) =>
    btn.addEventListener("click", async () => {
      try {
        state.lastRun = await api(`/runs/${encodeURIComponent(btn.dataset.run)}`);
        renderResult(state.lastRun);
    Media.setView("images");
    Media.loadImages(state.lastRun.run_id)
      .then((data) => Media.setStatus(`图片 ${data.completed}/${data.items.length} 完成${data.errors ? `，失败 ${data.errors}` : ""}`))
      .catch(() => Media.setStatus("图片加载失败", true));
        $$(".tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === "gen"));
        $$(".tab-page").forEach((p) => p.classList.toggle("active", p.id === "tab-gen"));
      } catch (err) {
        alert(err.message);
      }
    })
  );
  tbody.querySelectorAll("button[data-del]").forEach((btn) =>
    btn.addEventListener("click", async () => {
      if (!confirm(`删除生成记录 ${btn.dataset.del}？`)) return;
      await api(`/runs/${encodeURIComponent(btn.dataset.del)}`, { method: "DELETE" });
      loadRuns();
    })
  );
}

$("#btn-runs-refresh").addEventListener("click", loadRuns);

init();
