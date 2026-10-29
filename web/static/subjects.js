/* 主体页（/ui/subjects）：主体档案 CRUD、五槽位图、通用提示词模板。
   真多页版全局依赖：$/api/apiUpload/setStatus/escapeHtml/SLOT_* 在 core.js；
   renderMarkdown/Media 弹窗在 media.js —— 脚本清单见 subjects.html。
   拆页改动：loadSubjects 是本页版（只填 #subject-list,不碰预设页的 #gen-subject）。 */

/* ---------- 本页状态 ---------- */
const state = {
  subjects: [],
  currentSubject: null,
  subjectImages: [], // 主体档案图片槽位（当前主体）
  templateMd: "", // 当前主体的通用提示词模板
};

/* ---------- 本页初始化 ---------- */
async function loadSubjects() {
  state.subjects = await api("/subjects");
  renderSubjectList();
}
loadSubjects().catch((err) => setStatus($("#subject-list"), `主体列表加载失败：${err.message}`, true));/* ---------- 主体档案 ---------- */
function renderSubjectList() {
  const box = $("#subject-list");
  box.innerHTML = "";
  if (!state.subjects.length) {
    box.innerHTML = `<span class="hint">还没有档案：点「+ 新建」建档，或粘贴 YAML 导入。</span>`;
    return;
  }
  for (const s of state.subjects) {
    const card = document.createElement("button");
    card.type = "button";
    card.className = "subject-card"; // id ＋ 显示名两行的紧凑选择卡
    card.innerHTML = `<span class="sc-id">${escapeHtml(s.id)}</span><span class="sc-name">${escapeHtml(s.name)}</span>`;
    if (state.currentSubject === s.id) card.classList.add("active");
    card.addEventListener("click", () => fillSubjectForm(s));
    box.appendChild(card);
  }
}

function fillSubjectForm(s) {
  state.currentSubject = s.id;
  renderSubjectList();
  $("#subject-form-title").textContent = `档案详情：${s.id}${s.name ? `（${s.name}）` : ""}`;
  $("#s-id").value = s.id;
  $("#s-name").value = s.name;
  $("#s-noun").value = s.subject_noun;
  $("#s-trigger").value = s.trigger_word || "";
  $("#s-style").value = (s.style_lock || []).join("\n");
  $("#s-identity").value = (s.identity_lock || []).join("\n");
  $("#s-body").value = (s.body_lock || []).join("\n");
  $("#s-outfit").value = (s.outfit_lock || []).join("\n");
  $("#s-extra").value = s.extra || "";
  loadSubjectImages();
  loadSubjectTemplate();
}

function clearSubjectForm() {
  state.currentSubject = null;
  renderSubjectList();
  $("#subject-form-title").textContent = "档案详情";
  ["s-id", "s-name", "s-trigger", "s-style", "s-identity", "s-body", "s-outfit", "s-extra"].forEach((id) => ($(id).value = ""));
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
    body_lock: list("#s-body"),
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
          <button data-act="view" data-kind="${slot.kind}">查看</button>
          <button data-act="copy" data-kind="${slot.kind}">复制</button>
          <button data-act="suggest" data-kind="${slot.kind}">AI 起草</button>
          <button data-act="edit-prompt" data-kind="${slot.kind}">编辑提示词</button>
          <button data-act="generate" data-kind="${slot.kind}" class="primary">${slot.is_ready ? "Comfy重新生成" : "Comfy生成"}</button>
          <button data-act="generate-openrouter" data-kind="${slot.kind}">${slot.is_ready ? "OP重新生成" : "OP生成"}</button>
          <button data-act="generate-dreamina" data-kind="${slot.kind}">${slot.is_ready ? "即梦重新生成" : "即梦生成"}</button>
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
      if (!confirm(`删除 ${SLOT_LABELS[kind]} 的图片？`)) return;
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
