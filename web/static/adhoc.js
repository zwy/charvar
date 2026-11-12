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
   - 即梦：模型版本下拉（空=跟随设置页默认 seedream_4.7；i2i 不列不收参考图的模型），
     分辨率随所选模型配对，默认 2K。
   参考图预处理在后端统一做；种子留空 = 前端随机 0–100000（随机源在调用方这一侧，
   adhoc.py 仍只收显式 seed，不引入自己的随机源），填数值 = 固定复现；即梦不暴露 seed。 */

const ADHOC_ASPECTS = ["3:4", "1:1", "16:9", "4:3", "9:16", "2:3", "3:2", "21:9"];
const ADHOC_MAX_REFS = 4;

/* 即梦 model_version -> 分辨率枚举在共享文件 tables.js（预设页也用同一张表）。 */
/* DREAMINA_IMAGE_MODELS（画布生图目录）在 tables.js，本页与后端 _IMAGE_MODELS 同源。 */

const adhocState = {
  flow: "free", // 生图模式：自由生图 / 单主体动作 / 双主体动作 / 人脸动作 / 换装（引擎在 adhocflow.js）
  backend: "comfyui", // 一级：后端
  mode: "t2i", // 二级：模式（仅自由生图；工作流模式恒带参考图 = 图片编辑）
  references: [], // [{type:"staged", id, name, url} | {type:"subject", ...}]
  caps: null, // 当前 OpenRouter 模式模型的清单能力
  modeModel: "", // 当前模式的默认模型（显示名 + capabilities 来源）
  capsToken: 0, // 后端/模式切换的竞态守卫：只采纳最后一次拉取的能力
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
  const isFlow = adhocState.flow !== "free"; // 工作流模式：参考构成槽位（恒图片编辑语义）
  const i2i = isFlow || adhocState.mode === "i2i";

  /* 通用显隐 */
  $("#adhoc-negative-field").hidden = !isComfy; // 仅 ComfyUI 有负向词
  $("#adhoc-model-field").hidden = isComfy; // 模型说明行：OR / 即梦才显示
  $("#adhoc-model-select-field").hidden = !isDreamina;
  $("#adhoc-seed-field").hidden = isDreamina; // 即梦不暴露 seed
  $("#adhoc-refs-block").hidden = !i2i || isFlow; // 参考图区：自由生图 × 图片编辑
  $("#adhoc-flow-block").hidden = !isFlow; // 参考构成：工作流模式
  $("#adhoc-mode").hidden = isFlow; // t2i/i2i 分段只属于自由生图
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
    const mode = adhocState.flow !== "free" ? "i2i" : adhocState.mode;
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
  const mode = adhocState.flow !== "free" ? "i2i" : adhocState.mode;
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
    if ((adhocState.flow !== "free" || mode === "i2i") && adhocState.caps && adhocState.caps.supports_references === false) {
      el.innerHTML += `；⚠ 该模型不支持参考图输入，参考图不会发送`;
    }
    return;
  }
  const cliNote =
    adhocState.dreaminaCliOk === false
      ? "；⚠ 未找到 dreamina CLI（需安装并 dreamina login）"
      : "";
  const def = adhocState.dreaminaDefaults || {};
  const defNote = `当前生效：模型 ${def.model || DREAMINA_DEFAULT_IMAGE_MODEL} · 分辨率 ${def.resolution || "2K"}`;
  el.innerHTML =
    `即梦共用一组设置：带参考图 = image2image，不带 = text2image；模型下拉空值 = 跟随设置页/env（${defNote}）${cliNote}`;
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

/* 即梦：分辨率选项随所选模型版本配对（tables.js 画布目录，与后端同源）
   模型空（跟随设置）就真跟随 —— 档位按设置页的默认模型列出来，但选中「跟随设置默认」。
   字面值按目录原样（大小写有语义：seedance 小写 720p 等）。 */
function applyDreaminaResolutionOptions() {
  const modelValue = $("#adhoc-model-select").value;
  const def = adhocState.dreaminaDefaults || {};
  const sel = $("#adhoc-resolution");
  const base = modelValue || def.model || DREAMINA_DEFAULT_IMAGE_MODEL;
  const tiers = (DREAMINA_IMAGE_MODELS[base] || DREAMINA_IMAGE_MODELS[DREAMINA_DEFAULT_IMAGE_MODEL]).resolutions;
  sel.innerHTML = [`<option value="">跟随设置默认（${def.resolution || "2K"}）</option>`]
    .concat(tiers.map((t) => `<option value="${escapeHtml(t)}">${escapeHtml(t)}</option>`))
    .join("");
  sel.value = modelValue ? (tiers.includes("2K") ? "2K" : tiers[0]) : "";
  $("#adhoc-resolution-field").hidden = false;
}

/* 即梦：模型版本下拉；图片编辑页签不列连 t2i 兼收参考都没开的模型 */
async function applyAdhocDreaminaOptions() {
  if (adhocState.dreaminaCliOk === undefined) {
    try {
      const s = await api("/settings");
      adhocState.dreaminaCliOk = s.dreamina_cli_available !== false;
      // 生效默认 = DB > env > 缺省（后端 dreamina_config 已解析），别用 raw 设置值
      adhocState.dreaminaDefaults = s.dreamina_image_default || s.dreamina || {};
    } catch {
      adhocState.dreaminaCliOk = true;
      adhocState.dreaminaDefaults = {};
    }
  }
  const i2i = adhocState.mode === "i2i";
  const models = Object.entries(DREAMINA_IMAGE_MODELS).filter(
    ([, e]) => !i2i || e.refMax.i2i > 0 || e.refMax.t2i > 0
  );
  const def = adhocState.dreaminaDefaults || {};
  const defLabel = `跟随设置默认（${def.model || DREAMINA_DEFAULT_IMAGE_MODEL} · ${def.resolution || "2K"}）`;
  const sel = $("#adhoc-model-select");
  const keep = sel.value;
  sel.innerHTML = [`<option value="">${defLabel}</option>`]
    .concat(models.map(([id, e]) => `<option value="${escapeHtml(id)}">${escapeHtml(e.label)}</option>`))
    .join("");
  // 记住上次选的模型版本（切走再切回来别重置）
  sel.value = models.some(([id]) => id === keep) ? keep : "";
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
    del.className = "btn-icon";
    del.title = "移除";
    del.setAttribute("aria-label", "移除");
    del.innerHTML = icon("x");
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

/* 参考图四个库来源的弹层（引擎在 genrefs.js —— 与预设页/工作流模式同一套弹层）：
   主体档案 = 两步弹层（先选主体 → 再选一张槽位图）；人脸库 / 动作库 / 服饰库 = 对应库浏览器。
   出图请求体按 ref.type 映射 ReferenceSpec（见下方 generate）。 */

function addAdhocRef(ref) {
  if (adhocState.references.length >= ADHOC_MAX_REFS) {
    setStatus($("#adhoc-status"), `参考图最多 ${ADHOC_MAX_REFS} 张`, true);
    return false;
  }
  adhocState.references.push(ref);
  renderAdhocRefs();
  setStatus($("#adhoc-status"), "已加入参考图");
  return true;
}

$("#btn-adhoc-subject-add").addEventListener("click", () => {
  if (adhocState.references.length >= ADHOC_MAX_REFS) {
    return setStatus($("#adhoc-status"), `参考图最多 ${ADHOC_MAX_REFS} 张`, true);
  }
  GenRefs.showSubjectBrowser((pick) => {
    addAdhocRef({
      type: "subject",
      subject_id: pick.subject_id,
      kind: pick.kind,
      name: pick.name || `${pick.subject_id} / ${pick.kind}`,
      url: pick.url,
    });
  });
});

$("#btn-adhoc-face-add").addEventListener("click", () => {
  if (adhocState.references.length >= ADHOC_MAX_REFS) {
    return setStatus($("#adhoc-status"), `参考图最多 ${ADHOC_MAX_REFS} 张`, true);
  }
  GenRefs.showFaceBrowser((pick) => {
    addAdhocRef({
      type: "face",
      face_id: pick.face_id,
      face_source: pick.source || "",
      name: pick.name || pick.face_id,
      url: pick.url,
    });
  });
});

$("#btn-adhoc-pose-add").addEventListener("click", () => {
  if (adhocState.references.length >= ADHOC_MAX_REFS) {
    return setStatus($("#adhoc-status"), `参考图最多 ${ADHOC_MAX_REFS} 张`, true);
  }
  GenRefs.showPoseBrowser((pick) => {
    addAdhocRef({
      type: "pose",
      pose_id: pick.pose_id,
      angle: pick.angle,
      pose_source: pick.source || "",
      name: pick.name || pick.pose_id,
      url: pick.url,
    });
  });
});

/* 服饰库弹层（与工作流模式同一浏览器；requireImage = 纯文本条目禁选 —— 参考图必须真有图可发） */
$("#btn-adhoc-costume-add").addEventListener("click", () => {
  if (adhocState.references.length >= ADHOC_MAX_REFS) {
    return setStatus($("#adhoc-status"), `参考图最多 ${ADHOC_MAX_REFS} 张`, true);
  }
  GenRefs.showCostumeBrowser((pick) => {
    addAdhocRef({
      type: "costume",
      costume_id: pick.item_id,
      costume_source: pick.source_id || "",
      name: pick.name || pick.item_id,
      url: pick.url,
    });
  }, { requireImage: true });
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
  /* 工作流模式：提示词（可手改）+ compose 返回的参考规格原样发送 */
  const isFlow = AdhocFlow.isActive();
  if (isFlow && AdhocFlow.isStale()) {
    return setStatus($("#adhoc-status"), "构成已变化 —— 点「重新拼装」后再出图", true);
  }
  const i2i = isFlow || adhocState.mode === "i2i";
  if (!isFlow && i2i && !adhocState.references.length) {
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
  if (isFlow) {
    // 工作流模式：references = compose 返回的规格数组（顺序即 <image1>…），原样发送
    const refs = AdhocFlow.references();
    if (!refs || !refs.length) {
      return setStatus($("#adhoc-status"), "参考构成未选齐 —— 选齐后自动拼装，再出图", true);
    }
    body.references = refs;
  } else if (i2i) {
    body.references = adhocState.references.map((ref) =>
      ref.type === "staged"
        ? { staged_id: ref.id }
        : ref.type === "recent"
        ? { run_id: ref.run_id }
        : ref.type === "pose"
        ? {
            pose_id: ref.pose_id,
            angle: ref.angle || "",
            role: "pose",
            ...(ref.pose_source ? { source: ref.pose_source } : {}),
          }
        : ref.type === "face"
        ? {
            face_id: ref.face_id,
            role: "face",
            ...(ref.face_source ? { source: ref.face_source } : {}),
          }
        : ref.type === "costume"
        ? {
            costume_id: ref.costume_id,
            role: "costume",
            ...(ref.costume_source ? { source: ref.costume_source } : {}),
          }
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
    loadAdhocRecent(0); // 新图必然在最前，回到第一页看它
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
/* 下载原图：fetch 拿 blob 后走 object URL 触发下载，文件名用 run_id +
   实际格式后缀（从响应 MIME 推）—— 不依赖 URL 名，也不动后端。 */
const ADHOC_MIME_EXT = { "image/png": "png", "image/jpeg": "jpg", "image/webp": "webp" };
async function downloadAdhocRecentImage(rec) {
  try {
    const res = await fetch(`/adhoc/${encodeURIComponent(rec.run_id)}/image`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const blob = await res.blob();
    const ext = ADHOC_MIME_EXT[blob.type] || (blob.type.split("/")[1] || "png").replace(/^x-/, "");
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${rec.run_id}.${ext}`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 10_000);
    setStatus($(`#adhoc-status`), `已下载 ${rec.run_id}.${ext}`);
  } catch (err) {
    setStatus($(`#adhoc-status`), `下载失败：${err.message}`, true);
  }
}

/* ---------- 最近生成（分页） ---------- */
const ADHOC_RECENT_PAGE_SIZE = 24;
const adhocRecentState = { page: 0, seq: 0 };

async function loadAdhocRecent(page = adhocRecentState.page) {
  const seq = ++adhocRecentState.seq; // 快速连点/并发的旧响应不回写新页
  adhocRecentState.page = Math.max(0, page);
  const box = $("#adhoc-recent");
  try {
    const offset = adhocRecentState.page * ADHOC_RECENT_PAGE_SIZE;
    const { items, total } = await apiPaged(
      `/adhoc?limit=${ADHOC_RECENT_PAGE_SIZE}&offset=${offset}`
    );
    if (seq !== adhocRecentState.seq) return;
    box.innerHTML = "";
    if (!items.length) {
      if (adhocRecentState.page > 0) {
        // 过了末页（服务端记录被删过）：留在原页位，上一页可退回
        box.innerHTML = `<span class="hint">这一页没有记录</span>`;
        refreshPager(box, "adhoc-recent-pager", {
          page: adhocRecentState.page,
          pageSize: ADHOC_RECENT_PAGE_SIZE,
          total,
          goto: loadAdhocRecent,
        });
      } else {
        // 全库为空：老文案 + 不出分页条
        box.innerHTML = `<span class="hint">还没有 Playground 记录</span>`;
        document.getElementById("adhoc-recent-pager")?.remove();
      }
      return;
    }
    for (const rec of items) {
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
        promptBtn.textContent = "提示词";
        promptBtn.addEventListener("click", () =>
          Media.showTextModal("Playground · 提示词", rec.prompt || "", { copyable: true }));
        actions.appendChild(promptBtn);
      }
      if (rec.has_image) {
        const openBtn = document.createElement("button");
        openBtn.className = "btn-icon";
        openBtn.title = "查看大图";
        openBtn.setAttribute("aria-label", "查看大图");
        openBtn.innerHTML = icon("eye");
        openBtn.addEventListener("click", () =>
          Media.showImageModal(`/adhoc/${encodeURIComponent(rec.run_id)}/image`, rec.prompt || ""));
        actions.appendChild(openBtn);
        const dlBtn = document.createElement("button");
        dlBtn.className = "btn-icon";
        dlBtn.title = "下载图片";
        dlBtn.setAttribute("aria-label", "下载图片");
        dlBtn.innerHTML = icon("download");
        dlBtn.addEventListener("click", () => downloadAdhocRecentImage(rec));
        actions.appendChild(dlBtn);
      }
      const reuseBtn = document.createElement("button");
      reuseBtn.textContent = "复用";
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
    refreshPager(box, "adhoc-recent-pager", {
      page: adhocRecentState.page,
      pageSize: ADHOC_RECENT_PAGE_SIZE,
      total,
      goto: loadAdhocRecent,
    });
  } catch (err) {
    box.innerHTML = `<span class="hint">加载失败：${escapeHtml(err.message)}</span>`;
  }
}

$("#btn-adhoc-recent-refresh").addEventListener("click", () => loadAdhocRecent());

/* ---------- 初始化 ---------- */
function initAdhoc() {
  $("#adhoc-aspect").innerHTML = ADHOC_ASPECTS.map((a) => `<option value="${a}">${a}</option>`).join("");
  $("#adhoc-aspect").value = "3:4";
  renderAdhocRefs();
  applyAdhocPanel();
  loadAdhocRecent().catch(() => {});
  AdhocFlow.init({ onChange: applyAdhocPanel });
  $$("#adhoc-flow .seg").forEach((btn) =>
    btn.addEventListener("click", () => {
      if (adhocState.flow === btn.dataset.flow) return;
      adhocState.flow = btn.dataset.flow;
      adhocSegToggle("#adhoc-flow", "flow", adhocState.flow);
      AdhocFlow.setFlow(adhocState.flow);
      applyAdhocPanel();
    })
  );
  $("#btn-adhoc-recompose").addEventListener("click", () => AdhocFlow.recomposeNow());
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
