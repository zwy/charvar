/* Playground 页（web 面板）：
   一级页签 = 后端（ComfyUI / OpenRouter / 即梦），
   二级页签 = 模式（文生图 t2i / 图片编辑 i2i），显式选择。
   后端语义不变：AdhocRequest 有参考图才走 edit workflow / i2i 组 / image2image，
   所以文生图页签不传 references 即可 —— charvar/adhoc.py 零改动。
   文生图页签不发送已选参考图（列表保留，切回图片编辑生效）；
   图片编辑页签要求 >=1 张参考图才许出图。
   字段显隐：
   - ComfyUI：分辨率 1k/2k 默认 2k、负向词、种子；地址/workflow 跟随 .env 与设置页；
   - OpenRouter：模型跟随设置页 t2i/i2i 组（只显示说明行）；
     分辨率/质量 = 该模式模型的清单枚举（有才显示，含 2K 默认 2K）；
   - 即梦：模型版本下拉（空=跟随设置页默认 5.0；i2i 不列 3.0/3.1），
     分辨率随所选模型配对，默认 2K。
   参考图预处理在后端统一做；种子留空 = 前端随机 0–100000（随机源在调用方这一侧，
   adhoc.py 仍只收显式 seed，不引入自己的随机源），填数值 = 固定复现；即梦不暴露 seed。 */

const ADHOC_ASPECTS = ["3:4", "1:1", "16:9", "4:3", "9:16", "2:3", "3:2", "21:9"];
const ADHOC_MAX_REFS = 4;

/* 即梦 model_version -> 分辨率枚举在共享文件 tables.js（预设页也用同一张表）。 */
/* DREAMINA_T2I_ONLY_MODELS（仅文生图模型）同样在 tables.js。 */

const adhocState = {
  backend: "comfyui", // 一级：后端
  mode: "t2i", // 二级：模式（t2i 文生图 / i2i 图片编辑），显式选择
  references: [], // [{type:"staged", id, name, url} | {type:"subject", ...}]
  caps: null, // 当前 OpenRouter 模式模型的清单能力
  modeModel: "", // 当前模式的默认模型（显示名 + capabilities 来源）
  capsToken: 0, // 后端/模式切换的竞态守卫：只采纳最后一次拉取的能力
  subjectSlots: {}, // subject_id -> slots
  comfyTier: "2k", // ComfyUI 分辨率档（记住上次选择，切后端不丢）
};

function adhocBackend() {
  return adhocState.backend;
}

function adhocSegToggle(containerSel, attr, value) {
  $$(containerSel + " .seg").forEach((btn) =>
    btn.classList.toggle("active", btn.dataset[attr] === value)
  );
}

/* ---------- 面板应用：按「后端 × 模式」重算字段显隐与选项 ---------- */
function applyAdhocPanel() {
  const backend = adhocState.backend;
  const isComfy = backend === "comfyui";
  const isOpenRouter = backend === "openrouter";
  const isDreamina = backend === "dreamina";
  const i2i = adhocState.mode === "i2i";

  /* 通用显隐 */
  $("#adhoc-negative-field").hidden = !isComfy; // 仅 ComfyUI 有负向词
  $("#adhoc-model-field").hidden = isComfy; // 模型说明行：OR / 即梦才显示
  $("#adhoc-model-select-field").hidden = !isDreamina;
  $("#adhoc-seed-field").hidden = isDreamina; // 即梦不暴露 seed
  $("#adhoc-refs-block").hidden = !i2i; // 参考图区仅图片编辑页签出现
  const t2iHint = $("#adhoc-refs-t2i-hint");
  t2iHint.hidden = !(!i2i && adhocState.references.length);
  if (!t2iHint.hidden) {
    t2iHint.textContent = `已选 ${adhocState.references.length} 张参考图不随文生图发送；切到「图片编辑」后生效（暂存 24 小时后清理）`;
  }

  if (isComfy) {
    applyAdhocTierOptions();
  } else if (isOpenRouter) {
    // 能力异步回来才定枚举；先收起，等 applyAdhocCaps 再展开
    adhocState.caps = null;
    adhocState.modeModel = "";
    $("#adhoc-resolution-field").hidden = true;
    $("#adhoc-quality-field").hidden = true;
    adhocState.capsLoading = true;
    renderAdhocModelInfo();
    loadAdhocModelCaps();
  } else {
    applyAdhocDreaminaOptions();
  }
  updateAdhocHints();
}

/* ---------- OpenRouter 能力拉取与枚举填充 ---------- */
async function loadAdhocModelCaps() {
  const token = (adhocState.capsToken += 1);
  const settle = (fn) => {
    if (token === adhocState.capsToken) fn();
  };
  adhocState.capsLoading = true;
  renderAdhocModelInfo(); // 加载中…
  let model = "";
  try {
    const settings = await api("/settings");
    const mode = adhocState.mode;
    model =
      (settings.openrouter && settings.openrouter[`${mode}_model`]) ||
      (mode === "t2i"
        ? settings.openrouter_default_t2i_model
        : settings.openrouter_default_i2i_model) ||
      "";
    adhocState.modeModel = model;
    if (!model) {
      settle(() => {
        adhocState.capsLoading = false;
        adhocState.caps = null;
        applyAdhocCaps({});
      });
      return;
    }
    const caps = await api(`/openrouter/capabilities?model=${encodeURIComponent(model)}`);
    settle(() => {
      adhocState.capsLoading = false;
      applyAdhocCaps(caps);
    });
  } catch {
    settle(() => {
      adhocState.capsLoading = false;
      adhocState.caps = null;
      applyAdhocCaps({});
    });
  }
}

function applyAdhocCaps(caps) {
  adhocState.caps = caps || null;
  const resSelect = $("#adhoc-resolution");
  const resField = $("#adhoc-resolution-field");
  const enums = (caps && caps.resolutions) || [];
  if (enums.length) {
    resSelect.innerHTML = [`<option value="">跟随设置默认</option>`]
      .concat(enums.map((value) => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`))
      .join("");
    // 默认 2K：枚举里有（大小写不敏感）就选它
    const twoK = enums.find((v) => String(v).toLowerCase() === "2k");
    resSelect.value = twoK || "";
    resField.hidden = false;
  } else {
    // muse-image 这类清单为空的模型：分辨率只跟设置页/env，面板不造参数
    resSelect.innerHTML = `<option value=""></option>`;
    resSelect.value = "";
    resField.hidden = true;
  }

  const qualities = (caps && caps.qualities) || [];
  const qSelect = $("#adhoc-quality");
  const qField = $("#adhoc-quality-field");
  qSelect.innerHTML = [`<option value="">跟随设置默认</option>`]
    .concat(qualities.map((q) => `<option value="${escapeHtml(q)}">${escapeHtml(q)}</option>`))
    .join("");
  qSelect.value = "";
  qField.hidden = qualities.length === 0;
  renderAdhocModelInfo();
  updateAdhocHints();
}

/* ---------- 说明行 / 参考图提示 ---------- */
function renderAdhocModelInfo() {
  const el = $("#adhoc-model-field");
  if (!el) return;
  if (adhocState.backend === "comfyui") {
    el.hidden = true;
    return;
  }
  const mode = adhocState.mode;
  if (adhocState.backend === "openrouter") {
    if (adhocState.capsLoading) {
      el.textContent = "模型能力加载中…";
      return;
    }
    const label = mode === "i2i" ? "图片编辑" : "文生图";
    const model = adhocState.modeModel || "";
    el.innerHTML = model
      ? `当前走${label}：模型 <code>${escapeHtml(model)}</code>（跟随设置页，面板不选模型）`
      : `当前走${label}：设置页 / OP_IMAGE_${mode.toUpperCase()}_MODEL 还没配模型`;
    if (mode === "i2i" && adhocState.caps && adhocState.caps.supports_references === false) {
      el.innerHTML += `；⚠ 该模型不支持参考图输入，参考图不会发送`;
    }
    return;
  }
  const cliNote =
    adhocState.dreaminaCliOk === false
      ? "；⚠ 未找到 dreamina CLI（需安装并 dreamina login）"
      : "";
  el.innerHTML =
    `即梦共用一组设置：带参考图 = image2image，不带 = text2image；模型下拉空值 = 跟随设置页/env（模型默认 5.0，分辨率默认 2K）${cliNote}`;
}

function updateAdhocHints() {
  // 参考图组标题右侧：只报数量 + 控件里看不到的限制
  const refs = adhocState.references;
  const count = refs.length;
  const el = $("#adhoc-ref-count");
  if (el) {
    const parts = [`已选 ${count} 张`];
    const caps = adhocState.caps;
    if (adhocState.backend === "openrouter" && caps && caps.supports_references === false) {
      parts.push("该模型只支持文生图，参考图不会发送");
    }
    if (count >= ADHOC_MAX_REFS) parts.push(`已达上限 ${ADHOC_MAX_REFS} 张`);
    el.textContent = parts.join(" · ");
  }
  const t2iHint = $("#adhoc-refs-t2i-hint");
  const isT2i = adhocState.mode !== "i2i";
  t2iHint.hidden = !isT2i || !count;
  if (!t2iHint.hidden) {
    t2iHint.textContent = `已选 ${count} 张参考图不随文生图发送；切到「图片编辑」后生效（暂存 24 小时后清理）`;
  }
}

/* ---------- ComfyUI / 即梦的档位选项 ---------- */
function applyAdhocTierOptions() {
  /* ComfyUI 通路没有 provider 枚举，档位固定 charvar 档；2k 走官方 ~4.2MP 像素表 */
  const select = $("#adhoc-resolution");
  select.innerHTML =
    `<option value="1k">1k（长边 1024）</option>` +
    `<option value="2k">2k（Qwen 官方 ~4.2MP）</option>`;
  select.value = adhocState.comfyTier || "2k";
  $("#adhoc-resolution-field").hidden = false;
  $("#adhoc-quality-field").hidden = true;
}

/* 即梦：分辨率选项随所选模型版本配对（_MODEL_RESOLUTIONS）
   模型空（跟随设置）就真跟随 —— 档位按设置页的默认模型列出来，但选中「跟随设置默认」。 */
function applyDreaminaResolutionOptions() {
  const modelValue = $("#adhoc-model-select").value;
  const def = adhocState.dreaminaDefaults || {};
  const sel = $("#adhoc-resolution");
  const base = modelValue || def.model || "5.0";
  const tiers = DREAMINA_MODEL_RESOLUTIONS[base] || DREAMINA_MODEL_RESOLUTIONS["5.0"];
  const display = tiers.map((t) => String(t).toUpperCase());
  sel.innerHTML = [`<option value="">跟随设置默认（${def.resolution || "2K"}）</option>`]
    .concat(display.map((t) => `<option value="${t}">${t}</option>`))
    .join("");
  sel.value = modelValue ? (display.includes("2K") ? "2K" : display[0]) : "";
  $("#adhoc-resolution-field").hidden = false;
}

/* 即梦：模型版本下拉；图片编辑页签不列仅文生图的 3.0/3.1 */
async function applyAdhocDreaminaOptions() {
  if (adhocState.dreaminaCliOk === undefined) {
    try {
      const s = await api("/settings");
      adhocState.dreaminaCliOk = s.dreamina_cli_available !== false;
      adhocState.dreaminaDefaults = s.dreamina || {};
    } catch {
      adhocState.dreaminaCliOk = true;
      adhocState.dreaminaDefaults = {};
    }
  }
  const i2i = adhocState.mode === "i2i";
  const models = Object.keys(DREAMINA_MODEL_RESOLUTIONS).filter(
    (m) => !i2i || !DREAMINA_T2I_ONLY_MODELS.has(m)
  );
  const def = adhocState.dreaminaDefaults || {};
  const defLabel = `跟随设置默认（${def.model || "5.0"} · ${def.resolution || "2K"}）`;
  const sel = $("#adhoc-model-select");
  const keep = sel.value;
  sel.innerHTML = [`<option value="">${defLabel}</option>`]
    .concat(models.map((m) => `<option value="${m}">${m}</option>`))
    .join("");
  // 记住上次选的模型版本（切走再切回来别重置）
  sel.value = models.includes(keep) ? keep : "";
  $("#adhoc-model-select-field").hidden = false;
  applyDreaminaResolutionOptions();
  renderAdhocModelInfo();
}

/* ---------- 参考图：渲染 / 上传 / 移除 / 清空 ---------- */
function renderAdhocRefs() {
  const box = $("#adhoc-ref-list");
  box.innerHTML = "";
  if (!adhocState.references.length) {
    // 空态：虚线占位框，跟预设页的参考图区一致
    const empty = document.createElement("div");
    empty.className = "ref-empty";
    empty.textContent = "暂无参考图";
    box.appendChild(empty);
  }
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
    del.className = "btn-sm";
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
        // 本地 objectURL 预览：出图后暂存文件会被清掉，服务器 URL 预览会变破图
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

/* 主体档案里的参考图：下拉选主体 -> 选图片类型 -> 加入 */
async function loadAdhocSubjectOptions() {
  const sel = $("#adhoc-ref-subject");
  const kindSel = $("#adhoc-ref-kind");
  try {
    const subjects = await api("/subjects");
    sel.innerHTML = subjects.length
      ? subjects
          .map(
            (s) =>
              `<option value="${escapeHtml(s.id)}">${escapeHtml(s.id)} · ${escapeHtml(s.name)}</option>`
          )
          .join("")
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

/* 最近生成里有现成的产物图：点开弹层选一张，直接当参考图（不用先下载再上传）。
   图片是 run 的落盘产物（original/），出图成功后不会被清理 —— 和暂存图不同。 */
$("#btn-adhoc-recent-add").addEventListener("click", () => {
  if (adhocState.references.length >= ADHOC_MAX_REFS) {
    return setStatus($("#adhoc-status"), `参考图最多 ${ADHOC_MAX_REFS} 张`, true);
  }
  Media.pickRecentImage((rec) => {
    adhocState.references.push({
      type: "recent",
      run_id: rec.run_id,
      name: (rec.prompt || "").slice(0, 40) || rec.run_id,
      url: `/adhoc/${encodeURIComponent(rec.run_id)}/thumb`,
    });
    renderAdhocRefs();
    setStatus($("#adhoc-status"), "已加入参考图");
  }, { title: "Playground · 从最近生成选择参考图" });
});

/* ---------- 出图 ---------- */
$("#btn-adhoc-generate").addEventListener("click", async () => {
  const prompt = $("#adhoc-prompt").value.trim();
  if (!prompt) return setStatus($("#adhoc-status"), "提示词为空", true);
  const backend = adhocState.backend;
  const i2i = adhocState.mode === "i2i";
  if (i2i && !adhocState.references.length) {
    return setStatus(
      $("#adhoc-status"),
      "图片编辑需要至少 1 张参考图：上传、从最近生成选，或从主体档案选就绪的图",
      true
    );
  }
  // 种子留空 = 随机 0–100000（同主生成页「随机种子」的取值习惯）
  const seedRaw = $("#adhoc-seed").value.trim();
  const seed = seedRaw === "" ? Math.floor(Math.random() * 100001) : Number(seedRaw);
  const body = {
    prompt,
    backend,
    aspect_ratio: $("#adhoc-aspect").value,
    // 分辨率空 = 后端用设置页/env 的模式默认（OpenRouter/即梦）；ComfyUI 恒有值（1k/2k）
    resolution: $("#adhoc-resolution").value || "",
    seed,
    // 文生图页签不传 references：后端按「有没有参考图」走 t2i / i2i
    references: [],
  };
  if (i2i) {
    body.references = adhocState.references.map((ref) =>
      ref.type === "staged"
        ? { staged_id: ref.id }
        : ref.type === "recent"
        ? { run_id: ref.run_id }
        : { subject_id: ref.subject_id, kind: ref.kind }
    );
  }
  if (backend === "comfyui") {
    const negative = $("#adhoc-negative").value;
    body.negative_prompt = negative === "" ? null : negative;
    // ComfyUI 地址跟随 .env COMFYUI_HOST/PORT，面板已去掉该参数
  } else {
    const quality = $("#adhoc-quality").value;
    if (quality && !$("#adhoc-quality-field").hidden) body.quality = quality;
    if (backend === "dreamina") {
      const model = $("#adhoc-model-select").value;
      if (model) body.model = model;
    }
    // OpenRouter 模型不传：后端按模式取设置页/env 默认
  }

  const btn = $("#btn-adhoc-generate");
  btn.disabled = true;
  const waitHints = {
    comfyui: "出图中…（ComfyUI 约 20-60 秒）",
    openrouter: "出图中…（OpenRouter 约 15-45 秒）",
    dreamina: "出图中…（即梦约 40-90 秒，消耗积分/权益）",
  };
  setStatus($("#adhoc-status"), waitHints[backend] || "出图中…");
  try {
    const out = await api("/adhoc/generate", { method: "POST", body: JSON.stringify(body) });
    renderAdhocResult(out);
    // 出图成功后暂存参考图已被清理 -> 列表里也别留着；文生图 refs 为空不清理
    if (out.status === "ready") {
      const usedIds = new Set(
        adhocState.references.filter((r) => r.type === "staged").map((r) => r.id)
      );
      const kept = adhocState.references.filter((r) => !usedIds.has(r.id));
      adhocState.references.filter((r) => usedIds.has(r.id)).forEach(releaseAdhocRef);
      adhocState.references = kept;
      renderAdhocRefs();
    }
    setStatus(
      $("#adhoc-status"),
      `完成：${out.width} × ${out.height}px，用时 ${(out.duration_ms / 1000).toFixed(1)}s`
    );
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
  img.title = "点击预览大图";
  img.onclick = () => Media.showImageModal(out.image_url, "");
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
      box.innerHTML = `<span class="hint">还没有 Playground 记录</span>`;
      return;
    }
    for (const rec of records) {
      const card = document.createElement("div");
      card.className = "image-card";
      // 只有真落了产物才有图；入队未完成与失败画占位，不取不存在的图
      if (rec.has_image) {
        card.innerHTML = `<img src="/adhoc/${encodeURIComponent(rec.run_id)}/thumb" alt="" loading="lazy">`;
        const thumb = card.querySelector("img");
        thumb.title = "点击预览大图";
        thumb.addEventListener("click", () =>
          Media.showImageModal(`/adhoc/${encodeURIComponent(rec.run_id)}/image`, rec.prompt || ""));
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
      // 提示词在卡片里最多 3 行（.adhoc-recent p 的 CSS），全文走弹层
      if (rec.prompt && rec.prompt.trim()) {
        const promptBtn = document.createElement("button");
        promptBtn.textContent = "查看提示词";
        promptBtn.addEventListener("click", () =>
          Media.showTextModal("Playground · 提示词", rec.prompt || "", { copyable: true }));
        actions.appendChild(promptBtn);
      }
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
        $("#adhoc-aspect").value =
          ADHOC_ASPECTS.includes(rec.aspect_ratio) ? rec.aspect_ratio : $("#adhoc-aspect").value;
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
  $("#adhoc-aspect").innerHTML = ADHOC_ASPECTS.map((a) => `<option value="${a}">${a}</option>`).join("");
  $("#adhoc-aspect").value = "3:4";
  renderAdhocRefs();
  applyAdhocPanel();
  loadAdhocSubjectOptions().catch(() => {});
  loadAdhocRecent().catch(() => {});
}

$$("#adhoc-backend .seg").forEach((btn) =>
  btn.addEventListener("click", () => {
    if (adhocState.backend === btn.dataset.backend) return;
    adhocState.backend = btn.dataset.backend;
    adhocSegToggle("#adhoc-backend", "backend", adhocState.backend);
    applyAdhocPanel();
  })
);

$$("#adhoc-mode .seg").forEach((btn) =>
  btn.addEventListener("click", () => {
    if (adhocState.mode === btn.dataset.mode) return;
    adhocState.mode = btn.dataset.mode;
    adhocSegToggle("#adhoc-mode", "mode", adhocState.mode);
    applyAdhocPanel();
  })
);

$("#adhoc-model-select").addEventListener("change", applyDreaminaResolutionOptions);
$("#adhoc-resolution").addEventListener("change", () => {
  // ComfyUI 的档位记住，切后端再切回来不重置
  if (adhocState.backend === "comfyui") adhocState.comfyTier = $("#adhoc-resolution").value;
});

initAdhoc();