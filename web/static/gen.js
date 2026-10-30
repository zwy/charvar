/* 预设页（/ui/gen）：① 生图设置 → ② 生成提示词 → ③ 生成结果。
   真多页版全局依赖：$/api/escapeHtml/SLOT_* 在 core.js；供应商枚举表在 tables.js；
   Media 在 media.js；PoseGallery（GenRefs 的「从动作库加入」）在 posegallery.js；
   GenRefs 在 genrefs.js —— 脚本清单见 gen.html。
   本文件管:生成模式 / 供应商面板 / 生成 / 轮次条 / 推送生图三通路。
   轮次条带 sessionStorage 恢复:本页离开再回来不丢正在跑的批次;历史页「查看」走 /gen?run=<run_id> 直达。 */

/* ---------- 本页状态 ---------- */
const state = {
  subjects: [],
  presets: [],
  presetDetails: {},
  lastRun: null, // GenerateResult(= 当前选中的轮次,结果卡 / 推送生图都作用于它)
  rounds: [], // 轮次条:{run_id, completed, total, errors, queued},按时间排
  genMode: "classic",
  backend: "comfyui", // ① 生图供应商:comfyui / openrouter / dreamina
  caps: null, // 当前 OpenRouter 模型的能力(api-response.json 推导)
  capsToken: 0, // 供应商切换的竞态守卫:只采纳最后一次拉取的能力
  modeModel: "", // OpenRouter 当前模型(跟随设置页,面板不选模型)
  dreaminaCliOk: undefined, // 本机有没有 dreamina CLI
  dreaminaDefaults: {}, // 设置页的即梦默认值(模型/分辨率)
  comfySettings: null,
  comfyTier: "2k", // ComfyUI 分辨率档(记住上次选择,切供应商不丢)
  orResolution: "", // OpenRouter 分辨率(记住上次选择)
};
/* ---------- 生成模式（经典模板 / LLM 优化） ---------- */
function setGenMode(mode) {
  state.genMode = mode;
  $$("#gen-mode-classic, #gen-mode-llm").forEach((btn) =>
    btn.classList.toggle("active", btn.dataset.mode === mode));
  $("#gen-llm-panel").hidden = mode !== "llm";
}

$("#gen-mode-classic").addEventListener("click", () => setGenMode("classic"));
$("#gen-mode-llm").addEventListener("click", () => setGenMode("llm"));

/* ---------- ① 生图设置：供应商面板（参考 Playground 页） ---------- */
const GEN_ASPECTS = ["auto", "3:4", "1:1", "16:9", "4:3", "9:16", "2:3", "3:2", "21:9"];
const TIER_TO_SIDE = { "1k": 1024, "2k": 2048 };
// Qwen-Image-2.1 官方 2K 像素（面积恒定 ~4.2MP）。后端 charvar/exporters/comfyui.py
// 的 _QWEN_2K_SIZES 是同一张表 —— 改了那边这里要同步，否则预览和实际不符。
const QWEN_2K_SIZES = {
  "1:1": [2048, 2048], "4:3": [2400, 1792], "3:4": [1792, 2400],
  "3:2": [2528, 1696], "2:3": [1696, 2528], "16:9": [2752, 1536],
  "9:16": [1536, 2752],
};

/* 有参考图 = 图片编辑（i2i / edit workflow / image2image）；没有 = 纯文生图 */
function genBackendMode() {
  return GenRefs.count() ? "i2i" : "t2i";
}

function applyGenBackendPanel() {
  const backend = state.backend;
  const isComfy = backend === "comfyui";
  const isOpenRouter = backend === "openrouter";
  const isDreamina = backend === "dreamina";
  $("#gen-negative-field").hidden = !isComfy;   // 仅 ComfyUI 有负向词
  $("#gen-wait-field").hidden = !isComfy;        // 仅 ComfyUI 支持等待完成
  $("#gen-model-select-field").hidden = !isDreamina;
  $("#gen-model-field").hidden = isComfy;        // 模型说明行：OR / 即梦才显示
  if (isComfy) {
    applyGenTierOptions();
  } else if (isOpenRouter) {
    // 能力异步回来才定枚举；先收起，等 applyGenCaps 再展开
    state.caps = null;
    state.modeModel = "";
    $("#gen-resolution-field").hidden = true;
    $("#gen-quality-field").hidden = true;
    loadGenOpenRouterCaps();
  } else {
    applyGenDreaminaOptions();
  }
  renderGenModelInfo();
  updateGenHints();
}

/* ComfyUI：档位固定 charvar 档（1k/2k），2k 走官方 ~4.2MP 像素表 */
function applyGenTierOptions() {
  const select = $("#gen-resolution");
  select.innerHTML =
    `<option value="1k">1k（长边 1024）</option>` +
    `<option value="2k">2k（Qwen 官方 ~4.2MP）</option>`;
  // 记住上次选的档：切走再切回来别重置
  select.value = state.comfyTier || "2k";
  $("#gen-resolution-field").hidden = false;
  $("#gen-quality-field").hidden = true;
}

/* 即梦：模型版本下拉 + 分辨率随模型配对（与 Playground 同表）
   3.0/3.1 只有 text2image —— 带参考图时（即 image2image）不列出来，
   免得用户选到提交前才被后端拦（后端 validate_model_resolution 仍兜底）。 */
function applyGenDreaminaOptions() {
  const def = state.dreaminaDefaults || {};
  const defLabel = `跟随设置默认（${def.model || "5.0"} · ${def.resolution || "2K"}）`;
  const withRefs = GenRefs.count() > 0;
  const models = Object.keys(DREAMINA_MODEL_RESOLUTIONS).filter(
    (m) => !withRefs || !DREAMINA_T2I_ONLY_MODELS.has(m)
  );
  const sel = $("#gen-model-select");
  const keep = sel.value;
  sel.innerHTML = [`<option value="">${defLabel}</option>`]
    .concat(models.map((m) => `<option value="${m}">${m}</option>`))
    .join("");
  sel.value = models.includes(keep) ? keep : "";
  $("#gen-model-select-field").hidden = false;
  applyGenDreaminaResolutionOptions();
}

/* 即梦分辨率：模型空（跟随设置）就真跟随 —— 档位仍列出来（按设置页的默认模型配对），
   但选中的是「跟随设置默认」；显式选了模型才按那个模型列档。
   后端会校验 model×resolution 组合（3.0/3.1 不能带参考图），前端先拦一道。 */
function applyGenDreaminaResolutionOptions() {
  const modelValue = $("#gen-model-select").value;
  const def = state.dreaminaDefaults || {};
  const sel = $("#gen-resolution");
  const base = modelValue || def.model || "5.0";
  const tiers = DREAMINA_MODEL_RESOLUTIONS[base] || DREAMINA_MODEL_RESOLUTIONS["5.0"];
  const display = tiers.map((t) => String(t).toUpperCase());
  sel.innerHTML = [`<option value="">跟随设置默认（${def.resolution || "2K"}）</option>`]
    .concat(display.map((t) => `<option value="${t}">${t}</option>`))
    .join("");
  sel.value = modelValue ? (display.includes("2K") ? "2K" : display[0]) : "";
  $("#gen-resolution-field").hidden = false;
  $("#gen-quality-field").hidden = true;
}

/* OpenRouter：档位按模型清单（api-response.json 的 supported_parameters）出枚举 */
async function loadGenOpenRouterCaps() {
  const token = (state.capsToken += 1);
  const settle = (fn) => {
    if (token === state.capsToken) fn();
  };
  let model = "";
  try {
    const settings = await api("/settings");
    state.openrouterSettings = settings;
    const mode = genBackendMode();
    const or = settings.openrouter || {};
    model = or[`${mode}_model`] || (mode === "i2i"
      ? settings.openrouter_default_i2i_model
      : settings.openrouter_default_t2i_model) || "";
    state.modeModel = model;
    if (!model) {
      settle(() => {
        state.caps = null;
        applyGenCaps({});
      });
      return;
    }
    const caps = await api(`/openrouter/capabilities?model=${encodeURIComponent(model)}`);
    settle(() => applyGenCaps(caps));
  } catch {
    settle(() => {
      state.caps = null;
      applyGenCaps({});
    });
  }
}

function applyGenCaps(caps) {
  state.caps = caps || null;
  const resSelect = $("#gen-resolution");
  const enums = (caps && caps.resolutions) || [];
  if (enums.length) {
    resSelect.innerHTML = [`<option value="">跟随设置默认</option>`]
      .concat(enums.map((value) => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`))
      .join("");
    // 默认 2K（枚举里有，大小写不敏感）；上次选过且还在枚举里就沿用
    const twoK = enums.find((v) => String(v).toLowerCase() === "2k");
    const keep = enums.find((v) => v === state.orResolution);
    resSelect.value = keep || twoK || "";
    $("#gen-resolution-field").hidden = false;
  } else {
    resSelect.innerHTML = `<option value=""></option>`;
    resSelect.value = "";
    $("#gen-resolution-field").hidden = true;
  }
  const qualities = (caps && caps.qualities) || [];
  const qSelect = $("#gen-quality");
  qSelect.innerHTML = [`<option value="">跟随设置默认</option>`]
    .concat(qualities.map((q) => `<option value="${escapeHtml(q)}">${escapeHtml(q)}</option>`))
    .join("");
  qSelect.value = "";
  $("#gen-quality-field").hidden = qualities.length === 0;
  renderGenModelInfo();
  updateGenHints();
}

/* 供应商切换后，segmented 右边那行说明：当前生效的 workflow / 模型 / 枚举。
   只放「控件里看不到」的信息，其余一律不留。 */
function renderGenModelInfo() {
  const el = $("#gen-model-field");
  if (!el) return;
  el.hidden = false;
  if (state.backend === "comfyui") {
    const workload = autoWorkload();
    el.textContent = workload
      ? `workflow：${workload}`
      : "未配置 workflow —— 去「设置」页选";
    return;
  }
  if (state.backend === "openrouter") {
    const mode = genBackendMode();
    const model = state.modeModel || "";
    const label = mode === "i2i" ? "图片编辑" : "文生图";
    const caps = state.caps;
    const bits = [];
    if (caps && caps.resolutions && caps.resolutions.length) {
      bits.push(`分辨率 ${caps.resolutions.join("/")}`);
    }
    if (caps && caps.qualities && caps.qualities.length) {
      bits.push(`质量 ${caps.qualities.join("/")}`);
    }
    el.textContent = model
      ? `${label}：${model}${bits.length ? " · " + bits.join(" · ") : ""}`
      : `${label}：设置页还没配模型`;
    if (caps && caps.supports_references === false) el.textContent += " · ⚠ 不支持参考图";
    return;
  }
  // 即梦：当前生效的模型版本与分辨率档（都可能是「跟随设置」）
  const def = state.dreaminaDefaults || {};
  const model = $("#gen-model-select").value;
  const res = $("#gen-resolution").value;
  const cliNote = state.dreaminaCliOk === false ? " · ⚠ 未找到 dreamina CLI" : "";
  el.textContent =
    `即梦：${model || `跟随设置（${def.model || "5.0"}）`} · ${res || `跟随设置（${def.resolution || "2K"}）`}${cliNote}`;
}

/* 参数网格下方那行：只说「当前会出多大的图」——比例与像素。 */
function updateGenHints() {
  const presetRatio = (currentPresetMeta() || {}).preferred_aspect_ratio;
  const ratio = $("#gen-aspect").value;
  const hint = $("#gen-dim-preview");
  if (!hint) return;
  if (!ratio) {
    hint.textContent = "";
    return;
  }
  if (state.backend === "dreamina") {
    hint.textContent =
      ratio === "auto"
        ? presetRatio
          ? `按预设比例 ${presetRatio}（像素以即梦侧为准）`
          : "按条目自带画幅（没有则 3:4）"
        : `输出比例 ${ratio}（像素以即梦侧为准）`;
    return;
  }
  const tier = $("#gen-resolution").value;
  if (ratio === "auto") {
    hint.textContent = presetRatio
      ? `按预设比例 ${presetRatio}（手动选其他比例不被无声覆盖）`
      : "按景别：全身 2:3，胸像/头像/表情 3:4";
  } else {
    const parts = ratio.split(":").map((n) => Number(n.trim()));
    if (parts.length !== 2 || parts.some((n) => !Number.isFinite(n) || n <= 0)) {
      hint.textContent = "比例无效";
    } else if (state.backend === "comfyui" && tier === "2k" && QWEN_2K_SIZES[ratio]) {
      const [w, h] = QWEN_2K_SIZES[ratio];
      hint.textContent = `当前输出 ${w} × ${h}px（Qwen 官方 2K 像素，面积 ~4.2MP）`;
    } else {
      const longSide = parseLongSide(tier) || 1024;
      const [w, h] = parts;
      const shortSide = Math.max(64, Math.round((longSide * Math.min(w, h)) / Math.max(w, h) / 64) * 64);
      hint.textContent = `当前输出 ${w >= h ? longSide : shortSide} × ${h >= w ? longSide : shortSide}px`;
    }
  }
}

/* workflow 自动选择：带参考图 → 图片编辑 workflow；否则文生图（同 Playground）。 */
function autoWorkload() {
  const s = state.comfySettings || {};
  return GenRefs.count() ? s.comfy_edit_workflow : s.comfy_t2i_workflow;
}

async function loadComfyWorkflowSettings() {
  state.comfySettings = await api("/settings");
}

async function refreshComfyWorkflows() {
  const info = await api("/comfy/workflows");
  state.comfyLoaded = true;
  state.comfyWorkflows = (info.workflows || []).filter((w) => w.has_workflow && w.has_mapping);
}

/* 面板档位：charvar 档位（1k/2k）或 provider 枚举值（512/1K/1.5K/2K/4K…）。
   长边换算与后端 exporters.dimensions_for 的通用分支一致（1K=1024 长边）。 */
function parseLongSide(value) {
  const m = String(value || "").trim().match(/^(\d+(?:\.\d+)?)\s*([kK])?$/);
  if (!m) return null;
  return Math.max(64, Math.round(parseFloat(m[1]) * (m[2] ? 1024 : 1)));
}

/* 预设元信息（/presets 列表项，含 preferred_aspect_ratio） */
function currentPresetMeta() {
  const name = $("#gen-preset").value;
  return (state.presets || []).find((p) => p.name === name) || null;
}

/* ---------- 初始加载 ---------- */
async function init() {
  // 画幅比例（智能 = 按预设/景别推导）
  $("#gen-aspect").innerHTML = GEN_ASPECTS.map((a) =>
    `<option value="${a}">${a === "auto" ? "智能（按预设/景别）" : a}</option>`
  ).join("");
  $("#gen-aspect").value = "auto";
  $("#gen-aspect").addEventListener("change", updateGenHints);
  $("#gen-resolution").addEventListener("change", () => {
    // 记住这次的档：切供应商再切回来别重置
    if (state.backend === "comfyui") state.comfyTier = $("#gen-resolution").value;
    else if (state.backend === "openrouter") state.orResolution = $("#gen-resolution").value;
    updateGenHints();
  });
  $("#gen-model-select").addEventListener("change", applyGenDreaminaResolutionOptions);
  // 主体只有一个选择器（①）：提示词主体与参考图主体必须一致 → 换主体要重刷槽位、
  // 并清掉旧主体档案的参考图
  $("#gen-subject").addEventListener("change", async () => {
    await GenRefs.reloadKinds().catch(() => {});
    const dropped = await GenRefs.dropSubjectRefs();
    if (dropped) {
      setStatus(
        $("#gen-status"),
        `主体已切换：清掉了 ${dropped} 张旧主体的档案参考图（提示词主体与参考图必须同一个人）`
      );
    }
  });
  GenRefs.mount();
  // 参考图增减会改模式（有图 = 图片编辑）→ 供应商面板要跟着重算
  let genRefCount = 0;
  GenRefs.onChange(() => {
    const count = GenRefs.count();
    if (count === genRefCount) return;
    genRefCount = count;
    applyGenBackendPanel();
  });
  await Promise.allSettled([loadSubjects(), loadPresets(), loadOutfits(), loadHairstyles()]);
  if (state.subjects.length) {
    $("#gen-subject").value = state.subjects[0].id;
  }
  await GenRefs.reloadKinds().catch(() => {});
  await refreshComfyWorkflows().catch(() => {});
  await loadComfyWorkflowSettings().catch(() => {});
  await restoreRoundsFromSession();
  const runParam = new URLSearchParams(location.search).get("run");
  if (runParam) await selectRound(runParam).catch(() => {});
  await refreshGenBackendPanel().catch(() => {});
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
}

async function loadOutfits() {
  state.outfits = await api("/outfit-presets").catch(() => []);
  const sel = $("#gen-outfit");
  sel.innerHTML = "";
  const blank = document.createElement("option");
  blank.value = "";
  blank.textContent = "不用（仅动作/表情预设）";
  sel.appendChild(blank);
  for (const o of state.outfits) {
    const opt = document.createElement("option");
    opt.value = o.name;
    opt.textContent = `${o.name_zh || o.name}（${o.count} 档）`;
    sel.appendChild(opt);
  }
}

async function loadHairstyles() {
  state.hairstyles = await api("/hairstyle-presets").catch(() => []);
  const sel = $("#gen-hairstyle");
  sel.innerHTML = "";
  const blank = document.createElement("option");
  blank.value = "";
  blank.textContent = "不用（跟随参考图/档案发型）";
  sel.appendChild(blank);
  for (const h of state.hairstyles) {
    const opt = document.createElement("option");
    opt.value = h.name;
    opt.textContent = `${h.name}（${h.count} 款）`;
    sel.appendChild(opt);
  }
}

async function loadPresets() {
  state.presets = await api("/presets");
  const sel = $("#gen-preset");
  sel.innerHTML = "";
  // 按归属分组：通用预设 + 每个主体专属一组（分组键 = 预设的 subject 元数据）
  const groups = new Map();
  for (const p of state.presets) {
    const key = p.subject || "";
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(p);
  }
  const appendGroup = (label, items) => {
    if (!items.length) return;
    const group = document.createElement("optgroup");
    group.label = label;
    for (const p of items) {
      const opt = document.createElement("option");
      opt.value = p.name;
      opt.textContent = `${p.name}（${p.type === "list" ? "逐条" : "矩阵"} · ${p.count} 条）`;
      group.appendChild(opt);
    }
    sel.appendChild(group);
  };
  appendGroup("通用预设", groups.get("") || []);
  for (const [subject, items] of groups) {
    if (!subject) continue;
    appendGroup(`主体专属 · ${subject}`, items);
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
  // schema 2 带字预设：显式提示；数量即本预设条数（不再硬编码 30）
  const stickerHint = $("#gen-sticker-hint");
  if (stickerHint) {
    const isSticker = detail.purpose === "sticker";
    stickerHint.hidden = !isSticker;
    if (isSticker) {
      const refNames = (detail.reference_order || ["front"]).map((k) => (SLOT_LABELS[k] || k));
      stickerHint.querySelector("span.hint").textContent =
        `含指定中文文字：模型原生出字、须人工验字；出图按预设 ${detail.preferred_aspect_ratio || "1:1"} 方图`
        + `（推送面板"继承默认"会注入空负向，确保无内置禁字默认）；本预设 ${detail.count} 条，`
        + `参考图建议${refNames.join("、")}一张。`;
    }
  }
  updateGenHints(); // 预设切换：画幅预览跟着变（sticker 的 1:1 快照）
}

/* ---------- 生成 ---------- */
$("#btn-generate").addEventListener("click", async () => {
  if (!state.subjects.length) return setStatus($("#gen-status"), "先去「主体」建一个档案", true);
  const rawSeed = $("#gen-seed").value.trim();
  // 种子：留空（默认）= 每次随机 0–100000（用户填了用填的，与固定背景/固定光照
  // 「留空随机」同一习惯；提示词内容随机仍由后端 seed_for(seed, key) 派生）。
  const seed = rawSeed !== "" && Number.isFinite(Number(rawSeed))
    ? Math.floor(Number(rawSeed))
    : Math.floor(Math.random() * 100001);
  const payload = {
    subject_id: $("#gen-subject").value,
    preset: $("#gen-preset").value,
    seed,
    sampling: $("#gen-sampling").value,
    randomize_background: $("#gen-rand-bg").checked,
    randomize_lighting: $("#gen-rand-light").checked,
  };
  if ($("#gen-count").value) payload.count = Number($("#gen-count").value);
  if ($("#gen-fixed-bg").value.trim()) payload.fixed_background = $("#gen-fixed-bg").value.trim();
  if ($("#gen-fixed-light").value.trim()) payload.fixed_lighting = $("#gen-fixed-light").value.trim();
  // 发型/服饰组合：选了才带上（笛卡尔积在服务端展开；层级固定 动作→发型→服装）
  if ($("#gen-hairstyle").value) {
    payload.hairstyle_preset = $("#gen-hairstyle").value;
  }
  if ($("#gen-outfit").value) {
    payload.outfit_preset = $("#gen-outfit").value;
    payload.outfit_order = $("#gen-outfit-order") ? $("#gen-outfit-order").value : "outfit_first";
  }
  const sep = $("#gen-separator").value;
  if (sep !== " ") payload.separator = sep;
  payload.mode = state.genMode;
  // 参考图：Playground 式来源（上传暂存 / 最近生成 / 主体档案槽位）+ 分工标签。
  // 提示词阶段只用分工定锚定句；出图阶段（第 ③ 步）按来源取文件。
  const references = GenRefs.specs();
  if (references.length) payload.references = references;
  if (state.genMode === "llm") {
    if (!references.length) {
      return setStatus($("#gen-status"), "LLM 模式需要 1–4 张参考图（第 ① 步里选）", true);
    }
    // 提示词语言：any=不做强制性限制（后端缺省）/ zh=强制中文 / en=强制英文
    const llmLanguage = $("#gen-llm-language").value;
    if (llmLanguage) payload.llm_language = llmLanguage;
    if ($("#gen-llm-instructions").value.trim()) {
      payload.llm_instructions = $("#gen-llm-instructions").value.trim();
    }
  }
  try {
    $("#btn-generate").disabled = true; // 生成中不许再点（防止两轮并发互相覆盖视图）
    setStatus($("#gen-status"), "生成中…");
    state.lastRun = await api("/generate", { method: "POST", body: JSON.stringify(payload) });
    let msg = `完成：${state.lastRun.items.length} 条（run_id: ${state.lastRun.run_id}）`;
    if ((state.lastRun.warnings || []).length) {
      msg += `　⚠ 生成声明：${state.lastRun.warnings.join("；")}`;
    }
    setStatus($("#gen-status"), msg);
    addRound(state.lastRun.run_id, { completed: 0, errors: 0, queued: 0, items: state.lastRun.items });
    renderResult(state.lastRun);
    Media.setView("images");
    Media.loadImages(state.lastRun.run_id)
      .then((data) => Media.setStatus(`图片 ${data.completed}/${data.items.length} 完成${data.errors ? `，失败 ${data.errors}` : ""}`))
      .catch(() => Media.setStatus("图片加载失败", true));
  } catch (err) {
    setStatus($("#gen-status"), `失败：${err.message}`, true);
  } finally {
    $("#btn-generate").disabled = false;
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
    const dt = m.display_text || null; // sticker 快照为 meta 扁平字段（display_text/placement/flow/emphasis）
    const dtTitle = dt
      ? `layout: ${dt.layout} · 位置：${dt.placement} · 排布：${dt.flow} · 重音：${dt.emphasis}（点击复制）`
      : "";
    const dtCell = dt
      ? `<td class="copy-cell" data-dt="1" title="${escapeHtml(dtTitle)}"><span class="caption">${escapeHtml(dt.content)}</span></td>`
      : `<td><span class="caption">—</span></td>`;
    const promptCell = `<td class="copy-cell" title="点击复制">${escapeHtml(item.prompt)}</td>`;
    const capCell = `<td class="copy-cell" title="点击复制" data-caption="1"><span class="caption">${escapeHtml(item.caption)}</span></td>`;
    tr.innerHTML = `<td>${item.index}</td><td>${escapeHtml(item.variant_id)}</td>
      <td>${escapeHtml(m.framing || "-")}</td><td>${escapeHtml(m.angle || "-")}</td><td>${escapeHtml(m.expression || "-")}</td>
      <td>${escapeHtml(m.pose || "-")}</td>
      <td>${escapeHtml(item.background || "—")}</td><td>${escapeHtml(item.lighting || "—")}</td>
      ${promptCell}${dtCell}${capCell}`;
    tbody.appendChild(tr);
  }
  // 点击复制（闪一下底色示意已复制，无弹窗打断）
  tbody.onclick = (ev) => {
    const cell = ev.target.closest(".copy-cell");
    if (!cell) return;
    const text = cell.dataset.caption
      ? cell.querySelector(".caption").textContent
      : cell.textContent;
    navigator.clipboard.writeText(text.trim());
    cell.classList.add("cell-copied");
    setTimeout(() => cell.classList.remove("cell-copied"), 600);
  };
}


/* ---------- 轮次条（多轮生成变体互不干扰） ---------- */
/* 生成页只显示一个轮次的完整视图（结果表 / 图墙 / 推送面板，绑定 state.lastRun）；
   上面加一排轮次徽标，新一轮完成后自动切过去，旧轮点徽标切回；
   还有未出图（尤其 ComfyUI 未同步）的轮次由轮询自动 sync-outputs。 */
const ROUNDS_MAX = 12;
let roundsRenderKey = "";

function addRound(runId, counts) {
  let r = state.rounds.find((x) => x.run_id === runId);
  if (!r) {
    r = { run_id: runId, completed: 0, total: 0, errors: 0, queued: 0 };
    state.rounds.push(r);
    // 上限：只保留最近 ROUNDS_MAX 轮（更早的去历史记录看）
    while (state.rounds.length > ROUNDS_MAX) state.rounds.shift();
  }
  if (counts) assignRound(r, counts);
  renderRounds();
  return r;
}

/* counts 兼容 /images 与 /sync-outputs 两种响应（items 完整列表 + completed/errors/queued） */
function assignRound(r, data) {
  if (!r || !data) return;
  r.completed = data.completed ?? 0;
  r.errors = data.errors ?? 0;
  r.queued = data.queued ?? 0;
  r.total = data.items ? data.items.length : r.total;
  // 是否真的推过生图（有提交记录 / 已成品）。纯提示词轮次（没推任何端）不该一直转轮询
  r.everSubmitted = Boolean(data.items?.some((it) => it.record || it.prompt_id || it.status === "done"));
}

function roundSettled(r) {
  return r.total > 0 && (r.completed + r.errors + r.queued >= r.total || !r.everSubmitted);
}

/* media.js 的 Media.loadImages 回调：任何图墙刷新都会带来最新计数 */
function updateRoundStatus(data) {
  if (!data || !data.run_id) return;
  const r = state.rounds.find((x) => x.run_id === data.run_id);
  if (!r) return;
  const before = roundKey(r);
  assignRound(r, data);
  if (roundKey(r) !== before) renderRounds();
}

function roundKey(r) {
  return [r.completed, r.errors, r.queued, r.total].join("/");
}

function renderRounds() {
  const bar = $("#rounds-bar");
  const list = $("#rounds-list");
  if (!list) return;
  bar.hidden = state.rounds.length === 0;
  const key = state.rounds.map(roundKey).join("|") + "#" + (state.lastRun?.run_id || "");
  if (key === roundsRenderKey) return; // 轮询不改内容就不重建，免得闪烁
  roundsRenderKey = key;
  persistRounds(); // 真多页：本页离开再回来靠 sessionStorage 恢复轮次条
  list.innerHTML = "";
  state.rounds.forEach((r, i) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "chip round-chip" + (r.run_id === state.lastRun?.run_id ? " active" : "")
      + (roundSettled(r) ? " done" : "");
    btn.title = `${r.run_id}（第 ${i + 1} 轮）`;
    const idSpan = document.createElement("span");
    idSpan.textContent = `${i + 1}. ${r.run_id.length > 14 ? r.run_id.slice(0, 14) + "…" : r.run_id}`;
    const countSpan = document.createElement("span");
    countSpan.className = "rc-count";
    countSpan.textContent = r.total ? `${r.completed}/${r.total}` : "";
    btn.appendChild(idSpan);
    if (r.errors) {
      const err = document.createElement("span");
      err.className = "rc-err";
      err.textContent = `⚠${r.errors}`;
      btn.appendChild(err);
    }
    btn.appendChild(countSpan);
    if (!roundSettled(r)) {
      const spin = document.createElement("span");
      spin.textContent = "⟳";
      btn.appendChild(spin);
    }
    btn.addEventListener("click", () => { selectRound(r.run_id).catch((err) => setStatus($("#gen-status"), `切换轮次失败：${err.message}`, true)); });
    list.appendChild(btn);
  });
}

async function selectRound(runId) {
  if (state.lastRun?.run_id === runId) return;
  state.lastRun = await api(`/runs/${encodeURIComponent(runId)}`);
  addRound(runId);
  renderResult(state.lastRun);
  Media.setView("images");
  await Media.loadImages(state.lastRun.run_id).catch(() => Media.setStatus("图片加载失败", true));
  renderRounds();
}

/* 轮询：未完成且有提交记录的轮次每 5s 查一次状态；有 ComfyUI 真实提交在队列里
   （record/prompt_id 已落库但还没成品）才真正 sync-outputs 取图。
   当前选中轮的图墙一并刷新（Media.loadImages 自带渲染）。 */
let roundsPollBusy = false;
async function pollRounds() {
  if (roundsPollBusy || document.visibilityState !== "visible") return;
  const pending = state.rounds.filter((r) => r.total > 0 && r.everSubmitted && !roundSettled(r));
  if (!pending.length) return;
  roundsPollBusy = true;
  try {
    for (const r of pending) {
      let data;
      if (r.run_id === Media.currentRunId) {
        data = await Media.loadImages(r.run_id); // 当前轮：顺带刷图墙
      } else {
        data = await api(`/runs/${encodeURIComponent(r.run_id)}/images`);
      }
      assignRound(r, data);
      const hasQueuedSub = (data.items || []).some((it) => it.status === "queued" && (it.record || it.prompt_id));
      if (hasQueuedSub) {
        // 有在 ComfyUI 队列里的，取图（同步也会重建 sheet）；OpenRouter / 即梦在推送时已落记录，无需 sync
        const s = await api(`/runs/${encodeURIComponent(r.run_id)}/sync-outputs`, {
          method: "POST", body: JSON.stringify({ build_sheets: true }),
        });
        assignRound(r, s);
        if (r.run_id === Media.currentRunId) await Media.loadImages(r.run_id);
      }
      renderRounds();
    }
  } catch (_err) {
    // 单次轮询失败（如 ComfyUI 断连）静默跳过，下一轮再试
  } finally {
    roundsPollBusy = false;
  }
}
setInterval(pollRounds, 5000);

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

/* ---------- ③ 推送生图：按第 ① 步的供应商走对应通路 ---------- */
async function refreshGenBackendPanel() {
  // 设置页改了模型/档位 → 重新读设置并按当前供应商重算面板
  try {
    const settings = await api("/settings");
    state.openrouterSettings = settings;
    state.dreaminaDefaults = settings.dreamina || {};
    state.dreaminaCliOk = settings.dreamina_cli_available !== false;
  } catch {
    state.dreaminaDefaults = state.dreaminaDefaults || {};
  }
  applyGenBackendPanel();
}

$("#btn-push-image").addEventListener("click", async () => {
  if (!state.lastRun) return;
  const run = state.lastRun.run_id;
  const backend = state.backend;
  const references = GenRefs.specs();
  const log = $("#push-result");
  log.hidden = false;
  const waitHints = {
    comfyui: "推送中…（ComfyUI 约 20-60 秒/张）",
    openrouter: "推送中…（并发 4 路，OpenRouter 约 15-45 秒/张）",
    dreamina: "推送中…（即梦约 40-90 秒/张，消耗积分/权益）",
  };
  setStatus(log, waitHints[backend] || "推送中…");
  try {
    if (backend === "comfyui") {
      const workload = autoWorkload();
      if (!workload) {
        return setStatus(log, "未配置 workflow：先在「设置」页选好（文生图 / 图片编辑）workflow", true);
      }
      const body = {
        workload,
        // 空 = 继承默认（workflow 内置 / 景别回退）；带字预设会注入空负向清掉内置禁字
        negative_prompt: $("#gen-negative").value.trim() || null,
        wait: $("#gen-wait").checked,
        aspect_ratio: $("#gen-aspect").value,
        resolution: $("#gen-resolution").value,
      };
      if (references.length) body.references = references;
      const out = await api(`/runs/${run}/comfyui`, {
        method: "POST",
        body: JSON.stringify(body),
      });
      log.textContent =
        `成功 ${out.succeeded} / 失败 ${out.failed}\n` +
        out.submissions.map((s) => {
          const head = `#${String(s.index).padStart(3, "0")} ${s.variant_id}`;
          if (!s.ok) return `${head} × ${s.error}`;
          const files = (s.files || []).map((f) => f.filename).join(", ");
          return `${head} → prompt_id ${s.prompt_id}${files ? ` → ${files}` : ""}`;
        }).join("\n");
      // 不在入队后立即同步图片：未勾「等待完成」时刚入队必然还没有成品图
      if ($("#gen-wait").checked) {
        Media.syncImages(run)
          .then((data) =>
            Media.setStatus(`图片 ${data.completed}/${data.items.length} 完成${data.errors ? `，失败 ${data.errors}` : ""}`)
          )
          .catch((error) => Media.setStatus(`同步失败：${error.message}`, true));
      } else {
        log.textContent += "\n提示：ComfyUI 出图完成后，点上方「同步图片」取图。";
      }
      return;
    }
    if (backend === "openrouter") {
      const body = { aspect_ratio: $("#gen-aspect").value };
      const resolution = $("#gen-resolution").value;
      if (resolution) body.resolution = resolution; // 空 = 后端用设置页/env 的模式默认
      const quality = $("#gen-quality").value;
      if (quality && !$("#gen-quality-field").hidden) body.quality = quality;
      if (references.length) body.references = references;
      const out = await api(`/runs/${run}/openrouter`, {
        method: "POST",
        body: JSON.stringify(body),
      });
      const cost = (out.items || []).reduce((sum, e) => sum + (e.cost || 0), 0);
      log.textContent =
        `模型 ${out.model}｜成功 ${out.succeeded} / 失败 ${out.failed}｜合计 $${cost.toFixed(3)}\n` +
        (out.items || []).map((e) => {
          const head = `#${String(e.index).padStart(3, "0")} ${e.variant_id}`;
          if (!e.ok) return `${head} × ${e.error}`;
          return `${head} ✓${e.cost != null ? ` $${e.cost.toFixed(3)}` : ""}`;
        }).join("\n");
      // OpenRouter 是同步出图，产物已落盘 → 直接刷新图片墙
      await Media.syncImages(run);
      Media.setStatus(`图片 ${Media.currentData.completed}/${Media.currentData.items.length} 完成`);
      return;
    }
    // 即梦
    const body = { aspect_ratio: $("#gen-aspect").value };
    const resolution = $("#gen-resolution").value;
    if (resolution) body.resolution = resolution; // 空 = 后端用设置页/env 默认
    const model = $("#gen-model-select").value;
    if (model) body.model = model;
    if (references.length) body.references = references;
    const out = await api(`/runs/${run}/dreamina`, {
      method: "POST",
      body: JSON.stringify(body),
    });
    log.textContent =
      `模型 ${out.model}｜成功 ${out.succeeded} / 失败 ${out.failed}\n` +
      (out.items || []).map((e) => {
        const head = `#${String(e.index).padStart(3, "0")} ${e.variant_id}`;
        if (!e.ok) return `${head} × ${e.error}`;
        return `${head} ✓${e.width ? ` ${e.width}×${e.height}` : ""}`;
      }).join("\n");
    // 即梦是同步出图，产物已落盘 → 直接刷新图片墙
    await Media.syncImages(run);
    Media.setStatus(`图片 ${Media.currentData.completed}/${Media.currentData.items.length} 完成`);
  } catch (err) {
    setStatus(log, `失败：${err.message}`, true);
  }
});

/* 供应商切换 */
$$("#gen-backend .seg").forEach((btn) =>
  btn.addEventListener("click", () => {
    if (state.backend === btn.dataset.backend) return;
    state.backend = btn.dataset.backend;
    $$("#gen-backend .seg").forEach((b) => b.classList.toggle("active", b === btn));
    applyGenBackendPanel();
  })
);
/* ---------- 轮次条 sessionStorage 恢复（真多页：真跳页不丢在跑的批次） ---------- */
function persistRounds() {
  try {
    sessionStorage.setItem(
      "charvar.gen-rounds",
      JSON.stringify(state.rounds.map((r) => ({
        run_id: r.run_id,
        completed: r.completed,
        errors: r.errors,
        queued: r.queued,
        total: r.total,
        everSubmitted: r.everSubmitted,
      })))
    );
  } catch (_err) {
    // 存储不可用（隐私模式等）就只当没有恢复，不影响本页面会话内的行为
  }
}

function restoreRoundsFromSession() {
  let saved = [];
  try {
    saved = JSON.parse(sessionStorage.getItem("charvar.gen-rounds") || "[]");
  } catch (_err) {}
  if (!Array.isArray(saved)) return;
  const pending = saved.filter((r) => r && r.run_id);
  if (pending.length) {
    state.rounds = pending;
    renderRounds();
    // 未 settled 且有提交记录的轮次由既有的 pollRounds(5s) 自动续查;
    // 选中轮的图墙不自动加载 —— 恢复只重建轮次条,结果卡让用户点轮次徽标或 ?run= 进
  }
}
/* ---------- 结果卡工具栏（图墙/表格切换、同步、contact sheet、贴字、合成视频 —— 预设页专属，从 media.js 移入真多页的 gen.js） ---------- */
document.addEventListener("DOMContentLoaded", () => {
  $("#btn-view-images").addEventListener("click", () => Media.setView("images"));
  $("#btn-view-table").addEventListener("click", () => Media.setView("table"));
  $("#btn-contact-sheet").addEventListener("click", async () => {
    const runId = Media.currentRunId;
    if (!runId) return;
    Media.setStatus("生成 contact sheet…");
    try {
      await api(`/runs/${encodeURIComponent(runId)}/contact-sheet`, {
        method: "POST",
        body: JSON.stringify({ mode: "contact" }),
      });
      await Media.loadImages(runId);
      Media.setStatus("contact sheet 已生成");
    } catch (error) {
      Media.setStatus(`contact sheet 失败：${error.message}`, true);
    }
  });
  $("#btn-sync-images").addEventListener("click", async () => {
    const runId = Media.currentRunId;
    if (!runId) return;
    Media.setStatus("同步中…");
    try {
      await Media.syncImages(runId);
      const data = Media.currentData;
      Media.setStatus(`图片 ${data.completed}/${data.items.length} 完成${data.errors ? `，失败 ${data.errors}` : ""}`);
    } catch (error) {
      Media.setStatus(`同步失败：${error.message}`, true);
    }
  });
  $("#btn-overlay").addEventListener("click", () => {
    const runId = Media.currentRunId;
    if (!runId) return;
    Media.showPanelModal("贴字（PIL 后处理，不改原图）", (body) => {
      api("/overlay-presets").then((presets) => {
        if (!presets || !presets.length) {
          const empty = document.createElement("p");
          empty.className = "hint";
          empty.textContent = "还没有贴字预设：在 presets/overlays/ 放一个 YAML（如 stockings_label_bl.yaml）后再试。";
          body.appendChild(empty);
          return;
        }
        const label = document.createElement("label");
        label.innerHTML = `贴字预设<select id="overlay-preset-select"></select>`;
        const select = label.querySelector("select");
        for (const p of presets) {
          const opt = document.createElement("option");
          opt.value = p.name;
          opt.textContent = `${p.name}（${p.position}）${p.font_available ? "" : " · 字体不可用"}`;
          select.appendChild(opt);
        }
        body.appendChild(label);
        const fromLabel = document.createElement("label");
        fromLabel.innerHTML = `文字来源<select id="overlay-text-from"><option value="outfit_label">服饰名（组合变体）</option><option value="pose_tag">动作名</option><option value="variant_id">变体名</option><option value="fixed">固定文本</option></select>`;
        const fromSel = fromLabel.querySelector("select");
        body.appendChild(fromLabel);
        const fixedLabel = document.createElement("label");
        fixedLabel.innerHTML = `固定文本<input type="text" id="overlay-fixed-text" placeholder="文字来源 = 固定文本时使用">`;
        const fixedInput = fixedLabel.querySelector("input");
        fixedLabel.hidden = true;
        body.appendChild(fixedLabel);
        fromSel.addEventListener("change", () => {
          fixedLabel.hidden = fromSel.value !== "fixed";
        });
        const hint = document.createElement("div");
        hint.className = "hint";
        hint.textContent = "对已完成条目贴字，产物落 labeled/（原图 original/ 不动）；重复应用会覆盖旧贴字图。";
        body.appendChild(hint);
        const actions = document.createElement("div");
        actions.className = "actions";
        const btn = document.createElement("button");
        btn.className = "primary";
        btn.textContent = "开始贴字";
        const status = document.createElement("span");
        status.className = "status";
        actions.appendChild(btn);
        actions.appendChild(status);
        body.appendChild(actions);
        btn.addEventListener("click", async () => {
          btn.disabled = true;
          status.classList.remove("error");
          status.textContent = "贴字中…";
          try {
            const info = await api(`/runs/${encodeURIComponent(runId)}/overlay`, {
              method: "POST",
              body: JSON.stringify({
                overlay: select.value,
                text_from: fromSel.value,
                fixed: fromSel.value === "fixed" ? fixedInput.value : null,
              }),
            });
            status.classList.remove("error");
            status.textContent = `完成：贴字 ${info.applied.length} 张${info.skipped.length ? `，跳过 ${info.skipped.length} 张` : ""}`;
            await Media.loadImages(runId);
          } catch (error) {
            status.classList.add("error");
            status.textContent = "贴字失败：" + error.message;
            btn.disabled = false;
          }
        });
      }).catch((err) => {
        body.innerHTML = `<span class="hint">加载贴字预设失败：${escapeHtml(err.message)}</span>`;
      });
    });
  });
  $("#btn-video").addEventListener("click", () => {
    const runId = Media.currentRunId;
    if (!runId) return;
    Media.showPanelModal("合成视频", async (body, close) => {
      let presets = [];
      try {
        presets = await api("/video-presets");
      } catch (_err) {
        presets = [];
      }
      if (!presets.length) {
        const empty = document.createElement("p");
        empty.className = "hint";
        empty.textContent = "还没有视频预设：在 presets/video/ 放一个 YAML（如 q21_turnaround.yaml）后再试。";
        body.appendChild(empty);
        return;
      }
      const label = document.createElement("label");
      label.innerHTML = `视频预设<select id="video-preset-select"></select>`;
      const select = label.querySelector("select");
      for (const p of presets) {
        const opt = document.createElement("option");
        opt.value = p.name;
        opt.textContent = p.name;
        select.appendChild(opt);
      }
      body.appendChild(label);
      const musicLabel = document.createElement("label");
      musicLabel.innerHTML = `背景音乐<select id="video-music-select"></select>`;
      const musicSel = musicLabel.querySelector("select");
      body.appendChild(musicLabel);
      let musicFiles = [];
      try {
        musicFiles = ((await api("/video-music")) || {}).files || [];
      } catch (_err) {
        musicFiles = [];
      }
      const defOpt = document.createElement("option");
      defOpt.value = "";
      musicSel.appendChild(defOpt);
      const noneOpt = document.createElement("option");
      noneOpt.value = "none";
      noneOpt.textContent = "不加音乐";
      musicSel.appendChild(noneOpt);
      for (const f of musicFiles) {
        const opt = document.createElement("option");
        opt.value = f;
        opt.textContent = f;
        musicSel.appendChild(opt);
      }
      const refreshDefaultOpt = () => {
        const preset = presets.find((x) => x.name === select.value);
        defOpt.textContent = `用预设默认（${preset && preset.music ? preset.music : "无"}）`;
      };
      const musicName = () => {
        if (musicSel.value === "none") return null;
        if (musicSel.value) return musicSel.value;
        const preset = presets.find((x) => x.name === select.value);
        return preset && preset.music ? preset.music : null;
      };
      const hint = document.createElement("div");
      hint.className = "hint";
      body.appendChild(hint);
      const updateHint = () => {
        const preset = presets.find((x) => x.name === select.value);
        if (!preset) return;
        refreshDefaultOpt();
        const count = Media.currentData ? Media.currentData.completed : 0;
        const total = (preset.include_contact_sheet ? preset.contact_sheet_seconds : 0) + count * preset.seconds_per_image;
        // size 为 null = aspect auto：渲染时按本组图片实际比例就近选画幅
        const sizeText = preset.size
          ? `${preset.size[0]}×${preset.size[1]}`
          : "比例自适应（按本组图片就近选画幅，黑边补底）";
        hint.textContent = [
          `${sizeText} · ${preset.fps}fps`,
          `每张 ${preset.seconds_per_image}s`,
          preset.include_contact_sheet ? `封面 contact sheet ${preset.contact_sheet_seconds}s` : null,
          `背景音乐：${musicName() || "无"}`,
          `预计约 ${Math.round(total)} 秒（按已生成 ${count} 张）`,
        ].filter((part) => part).join(" · ");
      };
      select.addEventListener("change", updateHint);
      musicSel.addEventListener("change", updateHint);
      refreshDefaultOpt();
      updateHint();
      const actions = document.createElement("div");
      actions.className = "actions";
      const btn = document.createElement("button");
      btn.className = "primary";
      btn.textContent = "开始合成";
      const status = document.createElement("span");
      status.className = "status";
      actions.appendChild(btn);
      actions.appendChild(status);
      body.appendChild(actions);
      const resultBox = document.createElement("div");
      resultBox.className = "video-result"; // 视频 + 下载按钮纵向排列（带间距，按钮不拉伸）
      body.appendChild(resultBox);
      btn.addEventListener("click", async () => {
        btn.disabled = true;
        status.classList.remove("error");
        status.textContent = "ffmpeg 合成中…（30 张图大约十几秒到一分钟）";
        try {
          const info = await api(`/runs/${encodeURIComponent(runId)}/video`, {
            method: "POST",
            body: JSON.stringify({ preset: select.value, music: musicSel.value || null }),
          });
          status.classList.remove("error");
          status.textContent = "完成：" + [
            info.size[0] + "×" + info.size[1],
            Math.round(info.duration) + "s",
            info.music ? "音乐 " + info.music : "无音乐",
            info.skipped && info.skipped.length ? "跳过未生成 " + info.skipped.length + " 张" : null,
          ].filter(Boolean).join(" · ");
          resultBox.innerHTML = "";
          const vid = document.createElement("video");
          vid.controls = true;
          vid.autoplay = true;
          vid.muted = true;
          vid.preload = "metadata";
          vid.src = info.url;
          resultBox.appendChild(vid);
          const dl = document.createElement("a");
          dl.className = "btn";
          dl.href = info.url;
          dl.download = info.name;
          dl.textContent = "下载 MP4";
          resultBox.appendChild(dl);
          await Media.loadImages(runId);
        } catch (error) {
          status.classList.add("error");
          status.textContent = "合成失败：" + error.message;
          btn.disabled = false;
        }
      });
    });
  });
});

init();
