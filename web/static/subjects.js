/* 主体页（/ui/subjects）：主体档案库浏览、五槽位图、通用提示词模板。
   上半是与人脸/动作/服饰三库同形态的 master-detail：左侧搜索 + 档案卡网格
   （front 缩略图、缺图占位），右侧详情 = 可编辑档案表单（id 只读展示）。
   建档统一走 CLI（空态文案指向 CLI 建档），页面只做选择 / 编辑现有档案 / 删除。
   真多页版全局依赖：$/api/apiUpload/setStatus/escapeHtml/SLOT_* 在 core.js；
   renderMarkdown/Media 弹窗在 media.js —— 脚本清单见 subjects.html。
   拆页改动：loadSubjects 列表只填本页 #subject-list，不碰预设页的 #gen-subject。 */

/* ---------- 本页状态 ---------- */
const state = {
  subjects: [],
  currentSubject: null,
  subjectImages: [], // 主体档案图片槽位（当前主体）
  templateMd: "", // 当前主体的通用提示词模板
  q: "", // 搜索词（id / 显示名 / 锁定项）
};/* ---------- 主体档案库（上半：搜索 + 网格 + 常驻详情） ---------- */
async function loadSubjects() {
  state.subjects = await api("/subjects");
  renderSubjectPage();
}
loadSubjects().catch((err) => {
  const grid = $("#subject-list");
  if (grid) grid.innerHTML = `<span class="status error">主体列表加载失败：${escapeHtml(err.message)}</span>`;
});

/* 搜索素材串：id + 显示名 + 四组锁定项 + extra（matchesQuery 是 core.js 的
   多词 AND 语义，与三库搜索同一入口） */
function subjectSearchHay(s) {
  return [
    s.id,
    s.name,
    ...(s.style_lock || []),
    ...(s.identity_lock || []),
    ...(s.body_lock || []),
    ...(s.outfit_lock || []),
    s.extra || "",
  ].join(" ");
}

function filteredSubjects() {
  return state.subjects.filter(function (s) {
    return !state.q || matchesQuery(subjectSearchHay(s), state.q);
  });
}

/* 档案卡：2:3 图位（front 缩略图，缺图/加载失败渲染占位）+ 显示名 + mono id。
   与三库卡片同一形态（.image-card 基类 + .subject-card 修饰）。 */
function subjectThumbUrl(s) {
  return `/subjects/${encodeURIComponent(s.id)}/images/front/thumb?t=${Date.now()}`;
}

function renderSubjectPage() {
  const stat = $("#subject-page-stat");
  if (stat) stat.textContent = state.subjects.length + " 个主体";
  renderSubjectList();
  wireSubjectSearch();
}

/* 缺图占位：与 costume/pose 库的 thumb-empty 同款居中灰字 */
function emptyThumb() {
  const ph = document.createElement("div");
  ph.className = "subject-thumb-empty";
  ph.textContent = "无图";
  return ph;
}

function renderSubjectList() {
  const grid = $("#subject-list");
  grid.innerHTML = "";
  const items = filteredSubjects();
  if (!items.length) {
    grid.innerHTML = state.subjects.length
      ? '<div class="subject-empty">没有符合条件的主体 —— 换个搜索词</div>'
      : '<div class="subject-empty">暂无主体档案 —— 用 CLI 建档：charvar subject add presets/subjects/&lt;id&gt;.yaml</div>';
  }
  items.forEach(function (s) {
    const card = document.createElement("div");
    card.className = "image-card subject-card"; // 库网格通用卡 + 主体缩略图修饰
    card.innerHTML =
      '<div class="subject-thumb">' +
      '<img src="' + escapeHtml(subjectThumbUrl(s)) + '" alt="' + escapeHtml(s.id) + '" loading="lazy">' +
      "</div>" +
      '<div class="image-meta">' +
      '<span class="subject-card-name">' + escapeHtml(s.name || s.id) + "</span>" +
      '<span class="subject-card-id">' + escapeHtml(s.id) + "</span>" +
      "</div>";
    card.addEventListener("click", function () {
      selectSubject(s);
    });
    const img = card.querySelector("img");
    img.addEventListener("error", function () {
      img.replaceWith(emptyThumb()); // 缺图占位（对齐 costume-thumb-empty）
    });
    grid.appendChild(card);
  });
  markActiveSubjectCard();
  /* 首次打开 / 选中项不在当前筛选结果里 → 自动选中第一个（详情栏始终跟随可见项；
     与三库同一行为）。搜索无命中 → 详情栏清空成占位。 */
  if (items.length && items.some(function (s) { return s.id === state.currentSubject; })) return;
  if (items[0]) {
    selectSubject(items[0]);
  } else {
    state.currentSubject = null;
    renderDetailPane();
  }
}

function markActiveSubjectCard() {
  const items = filteredSubjects();
  $$("#subject-list .subject-card").forEach(function (el, i) {
    el.classList.toggle("active", items[i] && items[i].id === state.currentSubject);
  });
}

/* 选中主体：标环 + 详情栏（表单值 + 下半两张卡）一次到位 */
function selectSubject(s) {
  state.currentSubject = s ? s.id : null;
  markActiveSubjectCard();
  renderDetailPane();
}

function wireSubjectSearch() {
  const input = $("#subject-q");
  if (input && !input.dataset.wired) {
    input.dataset.wired = "1";
    let timer = null;
    input.addEventListener("input", function () {
      clearTimeout(timer);
      timer = setTimeout(function () {
        state.q = input.value.trim().toLowerCase();
        renderSubjectList();
      }, 180);
    });
  }
}

/* ---------- 详情栏：档案表单（id 只读，保存/删除） ---------- */
/* 详情栏一次渲染：标题（显示名 + mono id）+ 表单值；建档走 CLI，id 不再编辑。
   初次建档（表单为空）不再提供 —— currentSubject 为空时整栏退回空态。 */
function renderDetailPane() {
  const s = state.subjects.find(function (x) { return x.id === state.currentSubject; });
  const has = !!s;
  $("#subject-detail-empty").hidden = has;
  $("#subject-form").hidden = !has;
  $("#subject-images-card").hidden = !has;
  $("#template-card").hidden = !has;
  if (!has) return;
  $("#subject-detail-name").textContent = s.name || s.id;
  $("#s-id").textContent = s.id;
  $("#s-name").value = s.name;
  $("#s-noun").value = s.subject_noun;
  $("#s-trigger").value = s.trigger_word || "";
  $("#s-style").value = (s.style_lock || []).join("\n");
  $("#s-identity").value = (s.identity_lock || []).join("\n");
  $("#s-body").value = (s.body_lock || []).join("\n");
  $("#s-outfit").value = (s.outfit_lock || []).join("\n");
  $("#s-extra").value = s.extra || "";
  setStatus($("#subject-form-status"), "");
  loadSubjectImages();
  loadSubjectTemplate();
}

function collectSubjectFromForm() {
  const list = (id) => $(id).value.split("\n").map((v) => v.trim()).filter(Boolean);
  return {
    id: state.currentSubject,
    name: $("#s-name").value.trim(),
    subject_noun: $("#s-noun").value.trim() || "person",
    trigger_word: $("#s-trigger").value.trim() || null,
    style_lock: list("#s-style"),
    identity_lock: list("#s-identity"),
    body_lock: list("#s-body"),
    outfit_lock: list("#s-outfit"),
    extra: $("#s-extra").value.trim(),
  };
}

$("#btn-subject-save").addEventListener("click", async () => {
  const sid = state.currentSubject;
  if (!sid) return;
  const profile = collectSubjectFromForm();
  if (!profile.name) return setStatus($("#subject-form-status"), "档案需要显示名", true);
  try {
    await api(`/subjects/${encodeURIComponent(sid)}`, { method: "PUT", body: JSON.stringify(profile) });
    const fresh = await api(`/subjects/${encodeURIComponent(sid)}`);
    const row = state.subjects.find(function (x) { return x.id === sid; });
    if (row) Object.assign(row, fresh);
    $("#subject-detail-name").textContent = fresh.name || fresh.id;
    markActiveSubjectCard();
    setStatus($("#subject-form-status"), `已保存：${sid}`);
  } catch (err) {
    setStatus($("#subject-form-status"), `保存失败：${err.message}`, true);
  }
});

$("#btn-subject-delete").addEventListener("click", async () => {
  const sid = state.currentSubject;
  if (!sid) return;
  if (!(await Media.showConfirm({
    title: "删除主体",
    message: `删除主体 ${sid}？档案、五槽位图与模板一并删除，不可恢复。`,
  }))) return;
  try {
    await api(`/subjects/${encodeURIComponent(sid)}`, { method: "DELETE" });
    state.currentSubject = null;
    setStatus($("#subject-form-status"), `已删除：${sid}`);
    await loadSubjects(); // 重选剩下第一个
  } catch (err) {
    setStatus($("#subject-form-status"), `删除失败：${err.message}`, true);
  }
});

/* ---------- 主体档案图片（5 种槽位） ---------- */
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
  if (slot.status === "ready") {
    if (slot.source === "uploaded") return "已上传";
    if (slot.provider === "openrouter") return "已生成（OpenRouter）";
    if (slot.provider === "dreamina") return "已生成（即梦）";
    return "已生成";
  }
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
        <textarea class="slot-prompt" rows="3" placeholder="提示词（失焦自动保存）">${escapeHtml(slot.prompt || "")}</textarea>
        <div class="slot-actions">
          <button class="btn-icon" data-act="view" data-kind="${slot.kind}" title="查看提示词" aria-label="查看提示词">${icon("eye")}</button>
          <button class="btn-icon" data-act="copy" data-kind="${slot.kind}" title="复制提示词" aria-label="复制提示词">${icon("copy")}</button>
          <button data-act="suggest" data-kind="${slot.kind}">AI 起草</button>
          <button class="btn-icon" data-act="edit-prompt" data-kind="${slot.kind}" title="编辑提示词" aria-label="编辑提示词">${icon("edit")}</button>
          <button data-act="generate" data-kind="${slot.kind}" class="primary">${slot.is_ready ? "Comfy重新生成" : "Comfy生成"}</button>
          <button data-act="generate-openrouter" data-kind="${slot.kind}">${slot.is_ready ? "OP重新生成" : "OP生成"}</button>
          <button data-act="generate-dreamina" data-kind="${slot.kind}">${slot.is_ready ? "即梦重新生成" : "即梦生成"}</button>
          <label class="btn file-btn">上传<input type="file" data-upload="${slot.kind}" accept=".png,.jpg,.jpeg,.webp" hidden></label>
          ${slot.is_ready ? `<button class="btn-icon danger" data-act="delete" data-kind="${slot.kind}" title="删除" aria-label="删除">${icon("trash")}</button>` : ""}
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
    } else if (act === "edit-prompt") {
      openSlotPromptEditor(slot, promptEl);
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
    } else if (act === "generate-openrouter") {
      btn.disabled = true;
      setStatus(statusEl, "OpenRouter 生图中…（单张约 15–45 秒）");
      const updated = await api(
        `/subjects/${encodeURIComponent(sid)}/images/${kind}/generate-openrouter`,
        {
          method: "POST",
          body: JSON.stringify({
            prompt: promptEl.value.trim() || null,
            seed: Number($("#slot-seed").value || 0), // 统一种子：工具栏唯一入口
          }),
        },
      );
      setStatus(statusEl, updated.status === "ready" ? "完成" : `状态：${updated.status}`);
      await loadSubjectImages();
    } else if (act === "generate-dreamina") {
      btn.disabled = true;
      setStatus(statusEl, "即梦生图中…（单张约 60 秒，消耗积分）");
      const updated = await api(
        `/subjects/${encodeURIComponent(sid)}/images/${kind}/generate-dreamina`,
        {
          method: "POST",
          body: JSON.stringify({
            prompt: promptEl.value.trim() || null,
            seed: Number($("#slot-seed").value || 0), // 统一种子：工具栏唯一入口（即梦不暴露 seed，仅存档）
          }),
        },
      );
      setStatus(statusEl, updated.status === "ready" ? "完成" : `状态：${updated.status}`);
      await loadSubjectImages();
    } else if (act === "sync") {
      btn.disabled = true;
      setStatus(statusEl, "同步中…");
      await api(`/subjects/${encodeURIComponent(sid)}/images/${kind}/sync`, { method: "POST" });
      await loadSubjectImages();
    } else if (act === "delete") {
      if (!(await Media.showConfirm({
        title: "删除槽位图",
        message: `删除 ${SLOT_LABELS[kind]} 的图片？不可恢复。`,
      }))) return;
      await api(`/subjects/${encodeURIComponent(sid)}/images/${kind}`, { method: "DELETE" });
      await loadSubjectImages();
    }
  } catch (err) {
    setStatus(statusEl, `失败：${err.message}`, true);
    btn.disabled = false;
  }
});

/* 工具栏：统一种子 + 随机种子 + 一键 AI 起草（只补没有提示词的槽位，逐个串行调用） */
function slotCard(kind) {
  return $(`#subject-image-slots [data-slot-kind="${kind}"]`);
}

/* 随机种子按钮：点一下填入 0–100000 的随机数（种子取值助手，不参与提示词内容随机——
   提示词内容仍由后端 seed_for(seed, key) 派生）。 */
$("#btn-slot-seed-random").addEventListener("click", () => {
  $("#slot-seed").value = Math.floor(Math.random() * 100001);
});

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

/* 编辑提示词弹窗：大 textarea + 显式保存。走的是已有接口
   PUT /subjects/{id}/images/{kind}/prompt（与内联 textarea 失焦保存同一落库），
   不新增后端接口。打开时以卡片内联 textarea 的当前值为准（可能含未失焦的草稿）。 */
function openSlotPromptEditor(slot, promptEl) {
  const sid = state.currentSubject;
  if (!sid) return;
  const label = SLOT_LABELS[slot.kind] || slot.kind;
  Media.showPanelModal(`编辑提示词 · ${label}`, (body, close) => {
    const ta = document.createElement("textarea");
    ta.className = "md-edit";
    ta.rows = 12;
    ta.placeholder = "生图提示词；留空则生成前需先 AI 起草或手写";
    ta.value = promptEl ? promptEl.value : (slot.prompt || "");
    body.appendChild(ta);
    const hint = document.createElement("span");
    hint.className = "hint";
    hint.textContent = "保存即落库；「Comfy生成 / OP生成」未在前端另填提示词时用这条。";
    body.appendChild(hint);
    const row = document.createElement("div");
    row.className = "actions";
    const save = document.createElement("button");
    save.className = "primary";
    save.textContent = "保存提示词";
    const cancel = document.createElement("button");
    cancel.textContent = "取消";
    const status = document.createElement("span");
    status.className = "status";
    row.append(save, cancel, status);
    body.appendChild(row);
    cancel.addEventListener("click", close);
    save.addEventListener("click", async () => {
      save.disabled = true;
      const text = ta.value.trim();
      try {
        await api(`/subjects/${encodeURIComponent(sid)}/images/${slot.kind}/prompt`, {
          method: "PUT",
          body: JSON.stringify({ prompt: text }),
        });
        slot.prompt = text;
        if (promptEl) promptEl.value = text;
        setStatus(status, "提示词已保存");
        const cardStatus = slotCard(slot.kind)?.querySelector(".slot-status");
        if (cardStatus) setStatus(cardStatus, "提示词已保存");
        close();
      } catch (err) {
        save.disabled = false;
        setStatus(status, `保存失败：${err.message}`, true);
      }
    });
  });
}

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
