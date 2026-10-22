/* 临时生图页：给一段提示词出一张图。
   走的是 CLI `charvar image` 同一条通路（后端 charvar/adhoc.py），不落 runs 表。 */

const ADHOC_ASPECTS = ["3:4", "1:1", "16:9", "4:3", "9:16", "2:3", "3:2", "21:9"];
const ADHOC_MAX_REFS = 4;

const adhocState = {
  references: [], // [{type:"staged", id, name, url} | {type:"subject", subject_id, kind, name, url}]
  caps: null, // 当前 OpenRouter 模型的能力
  mode: null, // "t2i" | "i2i"：按当前参考图数判定，用于取模式默认
  subjectSlots: {}, // subject_id -> slots
};

/* ---------- 后端切换 ---------- */
function adhocBackend() {
  return $("#adhoc-backend").value;
}

function applyAdhocBackend() {
  const isOpenRouter = adhocBackend() === "openrouter";
  // OpenRouter 不支持负向提示词；ComfyUI 没有「模型 / 质量档」概念
  $("#adhoc-negative-field").hidden = isOpenRouter;
  $("#adhoc-url-field").hidden = isOpenRouter;
  $("#adhoc-model-field").hidden = !isOpenRouter;
  updateAdhocHints();
  if (isOpenRouter) {
    loadAdhocModelCaps().catch(() => {});
  } else {
    applyAdhocTierOptions();
  }
}

/* ---------- OpenRouter 模型能力（分辨率/质量档/参考图） ---------- */
/* 模式对齐 ComfyUI：不带参考图 = 文生图（t2i），带参考图 = 图片编辑（i2i）。
   未手填模型时按模式取设置页/env 的默认（OP_IMAGE_T2I_MODEL / OP_IMAGE_I2I_MODEL）。 */
function adhocMode() {
  return adhocState.references.length ? "i2i" : "t2i";
}

function updateAdhocMode() {
  const mode = adhocMode();
  if (adhocState.mode !== mode) {
    adhocState.mode = mode;
    // 模式切了 → 默认模型与档位能力都变了，重算一遍
    if (adhocBackend() === "openrouter") loadAdhocModelCaps().catch(() => {});
  }
}

async function loadAdhocModelCaps() {
  let model = $("#adhoc-model").value.trim();
  if (!model) {
    // 没指定模型就用模式默认：设置页 t2i/i2i 模型 > env（OP_IMAGE_T2I_MODEL 等）
    const settings = await api("/settings");
    const mode = adhocMode();
    model =
      (settings.openrouter && settings.openrouter[`${mode}_model`]) ||
      (mode === "t2i" ? settings.openrouter_default_t2i_model : settings.openrouter_default_i2i_model) ||
      "";
    if (!model) {
      adhocState.caps = null;
      applyAdhocCaps({});
      return;
    }
  }
  const caps = await api(`/openrouter/capabilities?model=${encodeURIComponent(model)}`);
  applyAdhocCaps(caps);
}

function applyAdhocCaps(caps) {
  adhocState.caps = caps || null;
  const resSelect = $("#adhoc-resolution");
  const enums = (caps && caps.resolutions) || [];
  const parts = [`<option value="">跟随设置默认</option>`];
  if (enums.length) {
    for (const value of enums) {
      parts.push(`<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`);
    }
  } else {
    // 模型没声明 resolution 枚举（如 muse-image）→ 档位纯算像素：charvar 档位。
    // muse 的清单声明未必准（声明为空但实际接受 size），别把档位锁死。
    parts.push(
      `<option value="1k">1k（长边 1024）</option>`,
      `<option value="2k">2k（长边 2048）</option>`,
    );
  }
  resSelect.innerHTML = parts.join("");

  const qualities = (caps && caps.qualities) || [];
  const qSelect = $("#adhoc-quality");
  const qField = $("#adhoc-quality-field");
  qSelect.innerHTML = [`<option value="">跟随设置默认</option>`]
    .concat(qualities.map((q) => `<option value="${escapeHtml(q)}">${escapeHtml(q)}</option>`))
    .join("");
  qField.hidden = qualities.length === 0;
  updateAdhocHints();
}

function applyAdhocTierOptions() {
  /* ComfyUI 通路：没有 provider 枚举，档位固定 charvar 档位（comfy 侧 2k 走官方像素表） */
  const select = $("#adhoc-resolution");
  select.innerHTML =
    `<option value="1k">1k（长边 1024）</option>` +
    `<option value="2k">2k（Qwen 官方像素 ~4.2MP）</option>`;
  select.value = "1k";
  $("#adhoc-quality-field").hidden = true;
}

function updateAdhocHints() {
  const hint = $("#adhoc-ref-hint");
  if (!hint) return;
  const parts = [];
  const count = adhocState.references.length;
  parts.push(count ? `已选 ${count} 张：${adhocState.references.map((r, i) => `<image${i + 1}>`).join("、")}` : "未选参考图");
  const caps = adhocState.caps;
  if (adhocBackend() === "openrouter" && caps && caps.supports_references === false) {
    parts.push(`当前模型 ${caps.model} 只支持文生图，参考图不会发送`);
  }
  if (count >= ADHOC_MAX_REFS) parts.push(`已达上限 ${ADHOC_MAX_REFS} 张`);
  hint.innerHTML = parts.map(escapeHtml).join("；");
  updateAdhocMode();
}

/* ---------- 参考图 ---------- */
function renderAdhocRefs() {
  const box = $("#adhoc-ref-list");
  box.innerHTML = "";
  adhocState.references.forEach((ref, index) => {
    const el = document.createElement("div");
    el.className = "ref-pick";
    el.innerHTML = `${ref.url ? `<img src="${ref.url}" alt="">` : `<span class="ref-thumb-empty"></span>`}
      <span class="ref-name">${escapeHtml(ref.name || `参考图 ${index + 1}`)}</span>
      <span class="badge">#${index + 1}</span>`;
    const img = el.querySelector("img");
    if (img) {
      img.title = "预览大图";
      img.addEventListener("click", (ev) => {
        ev.stopPropagation();
        Media.showImageModal(ref.url, ref.name || "");
      });
    }
    const del = document.createElement("button");
    del.type = "button";
    del.className = "chip";
    del.textContent = "移除";
    del.addEventListener("click", () => removeAdhocRef(index));
    el.appendChild(del);
    box.appendChild(el);
  });
  updateAdhocHints();
}

function releaseAdhocRef(ref) {
  // objectURL 不会自动回收，移除时手动放掉
  if (ref && ref.localUrl) URL.revokeObjectURL(ref.localUrl);
}

async function removeAdhocRef(index) {
  const ref = adhocState.references[index];
  if (ref && ref.type === "staged") {
    try {
      await api(`/adhoc/references/${encodeURIComponent(ref.id)}`, { method: "DELETE" });
    } catch {
      // 暂存文件可能已被清理，忽略
    }
  }
  releaseAdhocRef(ref);
  adhocState.references.splice(index, 1);
  renderAdhocRefs();
}

$("#adhoc-ref-file").addEventListener("change", async (ev) => {
  const files = Array.from(ev.target.files || []);
  ev.target.value = "";
  for (const file of files) {
    if (adhocState.references.length >= ADHOC_MAX_REFS) {
      setStatus($("#adhoc-status"), `参考图最多 ${ADHOC_MAX_REFS} 张`, true);
      break;
    }
    try {
      const out = await apiUpload("/adhoc/references", file);
      adhocState.references.push({
        type: "staged",
        id: out.id,
        name: out.name || file.name,
        // 本地 objectURL 预览：出图后服务器上的暂存文件会被清掉，
        // 用服务器 URL 预览就会变破图。
        url: URL.createObjectURL(file),
        localUrl: true,
      });
    } catch (err) {
      setStatus($("#adhoc-status"), `上传失败：${err.message}`, true);
    }
  }
  renderAdhocRefs();
});

$("#btn-adhoc-ref-clear").addEventListener("click", async () => {
  const refs = adhocState.references.slice();
  adhocState.references = [];
  renderAdhocRefs();
  for (const ref of refs) {
    releaseAdhocRef(ref);
    if (ref.type === "staged") {
      try {
        await api(`/adhoc/references/${encodeURIComponent(ref.id)}`, { method: "DELETE" });
      } catch {
        // 已清理
      }
    }
  }
});

/* 主体档案里的参考图：下拉选主体 → 选图片类型 → 加入 */
async function loadAdhocSubjectOptions() {
  const sel = $("#adhoc-ref-subject");
  const kindSel = $("#adhoc-ref-kind");
  try {
    const subjects = await api("/subjects");
    sel.innerHTML = subjects.length
      ? subjects.map((s) => `<option value="${escapeHtml(s.id)}">${escapeHtml(s.id)} · ${escapeHtml(s.name)}</option>`).join("")
      : `<option value="">（还没有主体档案）</option>`;
    sel.disabled = !subjects.length;
    await loadAdhocKindOptions();
  } catch {
    sel.innerHTML = `<option value="">（加载失败）</option>`;
  }
}

async function loadAdhocKindOptions() {
  const subjectId = $("#adhoc-ref-subject").value;
  const kindSel = $("#adhoc-ref-kind");
  if (!subjectId) {
    kindSel.innerHTML = `<option value="">（先选主体）</option>`;
    return;
  }
  try {
    const data = await api(`/subjects/${encodeURIComponent(subjectId)}/images`);
    adhocState.subjectSlots[subjectId] = data.slots || [];
    const ready = (data.slots || []).filter((s) => s.is_ready);
    kindSel.innerHTML = ready.length
      ? ready.map((s) => `<option value="${escapeHtml(s.kind)}">${escapeHtml(s.label)}</option>`).join("")
      : `<option value="">（该主体还没有就绪的图片）</option>`;
  } catch {
    kindSel.innerHTML = `<option value="">（加载失败）</option>`;
  }
}

$("#adhoc-ref-subject").addEventListener("change", loadAdhocKindOptions);

$("#btn-adhoc-ref-add").addEventListener("click", () => {
  if (adhocState.references.length >= ADHOC_MAX_REFS) {
    return setStatus($("#adhoc-status"), `参考图最多 ${ADHOC_MAX_REFS} 张`, true);
  }
  const subjectId = $("#adhoc-ref-subject").value;
  const kind = $("#adhoc-ref-kind").value;
  if (!subjectId || !kind) {
    return setStatus($("#adhoc-status"), "先选主体和图片类型（只有就绪的图片能选）", true);
  }
  const slots = adhocState.subjectSlots[subjectId] || [];
  const slot = slots.find((s) => s.kind === kind);
  adhocState.references.push({
    type: "subject",
    subject_id: subjectId,
    kind,
    name: `${subjectId} / ${(slot && slot.label) || kind}`,
    url: slot ? slot.thumb_url || slot.original_url : "",
  });
  renderAdhocRefs();
  setStatus($("#adhoc-status"), "已加入参考图");
});

/* ---------- 出图 ---------- */
$("#btn-adhoc-generate").addEventListener("click", async () => {
  const prompt = $("#adhoc-prompt").value.trim();
  if (!prompt) return setStatus($("#adhoc-status"), "提示词为空", true);
  const backend = adhocBackend();
  const body = {
    prompt,
    backend,
    aspect_ratio: $("#adhoc-aspect").value,
    // 分辨率空 = 后端用设置页/env 的模式默认（OpenRouter）或 1k（ComfyUI）
    resolution: $("#adhoc-resolution").value || "",
    seed: Number($("#adhoc-seed").value || 0),
    references: adhocState.references.map((ref) =>
      ref.type === "staged"
        ? { staged_id: ref.id }
        : { subject_id: ref.subject_id, kind: ref.kind }),
  };
  if (backend === "comfyui") {
    const negative = $("#adhoc-negative").value;
    body.negative_prompt = negative === "" ? null : negative;
    const url = $("#adhoc-comfy-url").value.trim();
    if (url) body.comfy_url = url;
  } else {
    const model = $("#adhoc-model").value.trim();
    if (model) body.model = model;
    const quality = $("#adhoc-quality").value;
    if (quality && !$("#adhoc-quality-field").hidden) body.quality = quality;
  }

  const btn = $("#btn-adhoc-generate");
  btn.disabled = true;
  setStatus($("#adhoc-status"), backend === "comfyui" ? "出图中…（ComfyUI 约 20–60 秒）" : "出图中…（OpenRouter 约 15–45 秒）");
  try {
    const out = await api("/adhoc/generate", { method: "POST", body: JSON.stringify(body) });
    renderAdhocResult(out);
    // 出图成功后服务器上的暂存参考图被清掉了 → 列表里也别留着（会误导成还能用）
    if (out.status === "ready") {
      const usedIds = new Set(
        adhocState.references.filter((r) => r.type === "staged").map((r) => r.id)
      );
      const kept = adhocState.references.filter((r) => !usedIds.has(r.id));
      adhocState.references.filter((r) => usedIds.has(r.id)).forEach(releaseAdhocRef);
      adhocState.references = kept;
      renderAdhocRefs();
    }
    setStatus($("#adhoc-status"), `完成：${out.width} × ${out.height}px，用时 ${(out.duration_ms / 1000).toFixed(1)}s`);
    loadAdhocRecent();
  } catch (err) {
    setStatus($("#adhoc-status"), `失败：${err.message}`, true);
  } finally {
    btn.disabled = false;
  }
});

function renderAdhocResult(out) {
  $("#adhoc-result-card").hidden = false;
  const img = $("#adhoc-result-image");
  img.src = out.image_url;
  const dl = $("#adhoc-dl-image");
  dl.href = out.image_url;
  dl.hidden = false;
  const manifest = $("#adhoc-dl-manifest");
  manifest.href = `/adhoc/${out.run_id}/manifest`;
  manifest.hidden = false;
  $("#adhoc-result-summary").textContent =
    `${out.provider} · ${out.width} × ${out.height}px · 种子 ${out.seed}` +
    (out.model ? ` · ${out.model}` : "") +
    (out.cost != null ? ` · $${out.cost.toFixed(4)}` : "");
  $("#adhoc-result-log").textContent =
    `run_id: ${out.run_id}\n产物: ${out.image_path}\nmanifest: ${out.manifest_path}` +
    (out.prompt_id ? `\nprompt_id: ${out.prompt_id}` : "") +
    `\n用时: ${(out.duration_ms / 1000).toFixed(1)}s`;
  img.scrollIntoView({ block: "nearest" });
}

/* ---------- 最近生成 ---------- */
async function loadAdhocRecent() {
  const box = $("#adhoc-recent");
  try {
    const records = await api("/adhoc?limit=24");
    box.innerHTML = "";
    if (!records.length) {
      box.innerHTML = `<span class="hint">还没有临时生图记录</span>`;
      return;
    }
    for (const rec of records) {
      const card = document.createElement("div");
      card.className = "image-card";
      // 只有真的落了产物才有图；只入队（--no-wait）与失败的记录画占位，别去取不存在的图
      if (rec.has_image) {
        card.innerHTML = `<img src="/adhoc/${encodeURIComponent(rec.run_id)}/thumb" alt="" loading="lazy">`;
      } else {
        const label = rec.status === "error" ? "失败" : "已入队";
        card.innerHTML = `<div class="image-placeholder">${label}</div>`;
      }
      const meta = document.createElement("div");
      meta.className = "image-meta";
      meta.innerHTML = `<span>${escapeHtml(rec.backend)} · ${escapeHtml(rec.aspect_ratio)}</span>
        <span>${escapeHtml((rec.created_at || "").slice(5, 16))}</span>`;
      card.appendChild(meta);
      const p = document.createElement("p");
      p.textContent = rec.prompt || "";
      p.title = rec.prompt || "";
      card.appendChild(p);
      const actions = document.createElement("div");
      actions.className = "image-actions";
      if (rec.has_image) {
        const openBtn = document.createElement("button");
        openBtn.textContent = "查看大图";
        openBtn.addEventListener("click", () =>
          Media.showImageModal(`/adhoc/${encodeURIComponent(rec.run_id)}/image`, rec.prompt || ""));
        actions.appendChild(openBtn);
      }
      const reuseBtn = document.createElement("button");
      reuseBtn.textContent = "复用提示词";
      reuseBtn.addEventListener("click", () => {
        $("#adhoc-prompt").value = rec.prompt || "";
        $("#adhoc-aspect").value = rec.aspect_ratio || $("#adhoc-aspect").value;
        if (rec.seed != null) $("#adhoc-seed").value = rec.seed;
      });
      actions.appendChild(reuseBtn);
      card.appendChild(actions);
      box.appendChild(card);
    }
  } catch (err) {
    box.innerHTML = `<span class="hint">加载失败：${escapeHtml(err.message)}</span>`;
  }
}

$("#btn-adhoc-recent-refresh").addEventListener("click", loadAdhocRecent);

/* ---------- 初始化 ---------- */
function initAdhoc() {
  const sel = $("#adhoc-aspect");
  sel.innerHTML = ADHOC_ASPECTS.map((a) => `<option value="${a}">${a}</option>`).join("");
  sel.value = "3:4";
  renderAdhocRefs();
  applyAdhocBackend();
  loadAdhocSubjectOptions().catch(() => {});
  loadAdhocRecent().catch(() => {});
}

$("#adhoc-backend").addEventListener("change", applyAdhocBackend);
$("#adhoc-model").addEventListener("change", () => {
  loadAdhocModelCaps().catch(() => {});
});

initAdhoc();
