/* 设置页（/ui/settings）：LLM / ComfyUI workflow / OpenRouter 凭据+生图（t2i+i2i 两组）/ 即梦 /
   OpenRouter 视频 / OpenRouter 语音 / 主体图默认尺寸。
   真多页版全局依赖：$/api/setStatus/escapeHtml/SLOT_LABELS 在 core.js —— 脚本清单见 settings.html。
   拆页改动：不再跨页调 gen 页的 refreshGenBackendPanel（真跳页后 gen 页加载时本来就重算）。 *//* ---------- 设置 ---------- */
const ASPECT_OPTIONS = ["3:4", "1:1", "16:9", "4:3", "9:16", "2:3", "3:2", "21:9"];

/* OpenRouter 设置页：两种模式（t2i 文生图 / i2i 图片编辑）各一组模型 + 档位。
   模型选完按 api-response.json 的 supported_parameters 动态加载档位；
   该模型不支持的重置为空（空 = 请求显式值 > env 默认 > 不发送）。 */
let OR_MODEL_OPTIONS = []; // /openrouter/models 的 [{id, name}]
const OR_CAPS_BY_MODE = { t2i: null, i2i: null };

function orModelOptionsHtml() {
  const parts = [`<option value="">（跟随 env 默认）</option>`];
  for (const m of OR_MODEL_OPTIONS) {
    parts.push(`<option value="${escapeHtml(m.id)}">${escapeHtml(modelLabel(m.id, m.name))}</option>`);
  }
  return parts.join("");
}

async function fillOpenRouterSettingParams(mode, settings, modelOverride = null) {
  const or = (settings && settings.openrouter) || {};
  // modelOverride：用户刚在下拉里选了还没保存的值（设置页存的值此刻是旧的）
  const modelValue = modelOverride != null ? modelOverride : (or[`${mode}_model`] || "");
  $(`#set-or-${mode}-model`).value = modelValue;
  const effectiveModel =
    modelValue ||
    (mode === "t2i" ? settings.openrouter_default_t2i_model : settings.openrouter_default_i2i_model) ||
    "";
  let caps = null;
  if (effectiveModel) {
    try {
      caps = await api(`/openrouter/capabilities?model=${encodeURIComponent(effectiveModel)}`);
    } catch {} // 清单读不到就先不带档位，保存的值仍会原样存
  }
  OR_CAPS_BY_MODE[mode] = caps;

  const resEnums = (caps && caps.resolutions) || [];
  const qEnums = (caps && caps.qualities) || [];
  const resSel = $(`#set-or-${mode}-resolution`);
  const resField = $(`#set-or-${mode}-res-field`);
  const qSel = $(`#set-or-${mode}-quality`);
  const qField = $(`#set-or-${mode}-quality-field`);
  const optionHtml = (values) =>
    [`<option value="">（不发送）</option>`]
      .concat(values.map((v) => `<option value="${escapeHtml(v)}">${escapeHtml(v)}</option>`))
      .join("");
  resSel.innerHTML = optionHtml(resEnums);
  resField.hidden = resEnums.length === 0;
  // 模型换了 → 不在枚举里的旧值重置为空
  const storedRes = or[`${mode}_resolution`] || "";
  resSel.value = storedRes && resEnums.some((v) => v.toLowerCase() === storedRes.toLowerCase()) ? storedRes : "";
  qSel.innerHTML = optionHtml(qEnums);
  qField.hidden = qEnums.length === 0;
  const storedQ = or[`${mode}_quality`] || "";
  qSel.value = storedQ && qEnums.some((v) => v.toLowerCase() === storedQ.toLowerCase()) ? storedQ : "";

  // 模式不匹配的模型给个警示：只支持文生图的模型不能当图片编辑模型
  const hints = [];
  if (caps && caps.supports_references === false && mode === "i2i") {
    hints.push(`图片编辑模型 ${caps.model} 只支持文生图（清单里没有 image 输入），带参考图的生图会失败`);
  }
  return hints;
}

async function refreshOpenRouterSettingFields(settings) {
  const [t2iHints, i2iHints] = await Promise.all([
    fillOpenRouterSettingParams("t2i", settings),
    fillOpenRouterSettingParams("i2i", settings),
  ]);
  const hintEl = $("#or-model-hints");
  if (hintEl) hintEl.textContent = t2iHints.concat(i2iHints).join("｜");
}

/* 模型换了 → 该模式的档位按新模型能力重算（不支持的重置为空） */
async function onOpenRouterSettingModelChange(mode) {
  try {
    const settings = await api("/settings");
    await fillOpenRouterSettingParams(mode, settings, $(`#set-or-${mode}-model`).value.trim());
    const hintEl = $("#or-model-hints");
    const otherMode = mode === "t2i" ? "i2i" : "t2i";
    const otherHints = OR_CAPS_BY_MODE[otherMode]
      ? (otherMode === "i2i" && OR_CAPS_BY_MODE[otherMode].supports_references === false
          ? [`图片编辑模型 ${OR_CAPS_BY_MODE[otherMode].model} 只支持文生图（清单里没有 image 输入），带参考图的生图会失败`]
          : [])
      : [];
    if (hintEl) hintEl.textContent = otherHints.join("｜");
  } catch {}
}

$("#set-or-t2i-model").addEventListener("change", () => onOpenRouterSettingModelChange("t2i"));
$("#set-or-i2i-model").addEventListener("change", () => onOpenRouterSettingModelChange("i2i"));

async function loadSettings() {
  try {
    const [settings, comfy] = await Promise.all([api("/settings"), api("/comfy/workflows")]);
    const names = (comfy.workflows || []).filter((w) => w.has_workflow && w.has_mapping).map((w) => w.name);
    for (const [sel, value] of [["#set-t2i", settings.comfy_t2i_workflow], ["#set-edit", settings.comfy_edit_workflow],
        ["#set-ups-workflow", settings.upscale ? settings.upscale.workflow : ""]]) {
      const el = $(sel);
      el.innerHTML = names.map((n) => `<option value="${n}">${n}</option>`).join("") ||
        `<option value="">（data/workflows 没有成对 workflow）</option>`;
      const stored = value || "";
      if (stored && names.includes(stored)) el.value = stored;
      else el.value = "";
    }
    $("#set-llm-base").value = settings.llm.base_url || "";
    $("#set-llm-model").value = settings.llm.model || "";
    $("#set-llm-key").value = "";
    $("#set-llm-key").placeholder = settings.llm_configured ? "已配置（留空保持不变）" : "留空 = 环境变量 LLM_API_KEY";
    $("#set-negative").value = settings.default_negative_prompt || "";
    // 「放大页默认」：后端/目标短边（workflow 下拉在上面已按成对名单填充）
    const up = settings.upscale || {};
    if ($("#set-ups-backend")) $("#set-ups-backend").value = up.backend || "comfyui";
    if ($("#set-ups-target")) $("#set-ups-target").value = ["720p", "1080p", "4k"].includes(up.target) ? up.target : "1080p";
    try {
      const models = await api("/openrouter/models");
      OR_MODEL_OPTIONS = models.models || [];
    } catch {} // 清单读不到时下拉里只剩「跟随 env 默认」
    // 已存的模型值必须在下拉里，否则赋值会静默失败，下一次保存还会把它清掉
    const or = settings.openrouter || {};
    for (const mode of ["t2i", "i2i"]) {
      const stored = or[`${mode}_model`];
      if (stored && !OR_MODEL_OPTIONS.some((m) => m.id === stored)) {
        OR_MODEL_OPTIONS.push({ id: stored, name: `${stored}（不在当前清单里）` });
      }
    }
    const optionsHtml = orModelOptionsHtml();
    $("#set-or-t2i-model").innerHTML = optionsHtml;
    $("#set-or-i2i-model").innerHTML = optionsHtml;
    const list = $("#or-model-options");
    list.innerHTML = OR_MODEL_OPTIONS.map((m) => `<option value="${escapeHtml(m.id)}">${escapeHtml(modelLabel(m.id, m.name))}</option>`).join("");
    await refreshOpenRouterSettingFields(settings);
    $("#set-or-base").value = or.base_url || "";
    $("#set-or-key").value = "";
    $("#set-or-key").placeholder = settings.openrouter_configured ? "已配置（留空保持不变）" : "留空 = OP_API_KEY（再回退 LLM_API_KEY）";
    // OpenRouter 视频：模型下拉来自 /videos/models 目录；已存值不在清单里时补一项
    const orv = settings.openrouter_video || {};
    let videoCatalog = null;
    try {
      videoCatalog = await api("/videos/models");
    } catch {} // 清单读不到时下拉里只剩「跟随默认」
    const videoModels = (videoCatalog && videoCatalog.data) || [];
    const vSel = $("#set-or-video-model");
    const defaultVideoModel = (videoCatalog && videoCatalog.default_model) || "";
    const vOptions = [`<option value="">跟随默认（${escapeHtml(defaultVideoModel || "OP_VIDEO_MODEL / heygen/heygen-video-1")}）</option>`]
      .concat(videoModels.map((m) => `<option value="${escapeHtml(m.id)}">${escapeHtml(modelLabel(m.id, m.name))}</option>`));
    if (orv.model && !videoModels.some((m) => m.id === orv.model)) {
      vOptions.push(`<option value="${escapeHtml(orv.model)}">${escapeHtml(orv.model)}（不在当前清单里）</option>`);
    }
    vSel.innerHTML = vOptions.join("");
    vSel.value = orv.model || "";
    // OpenRouter 语音：模型下拉来自 /tts/models 目录；已存值不在清单里时补一项
    const ort = settings.openrouter_tts || {};
    let ttsCatalog = null;
    try {
      ttsCatalog = await api("/tts/models");
    } catch {} // 清单读不到时下拉里只剩「跟随默认」
    const ttsModels = (ttsCatalog && ttsCatalog.data) || [];
    const tSel = $("#set-or-tts-model");
    const defaultTtsModel = (ttsCatalog && ttsCatalog.default_model) || "";
    const tOptions = [`<option value="">跟随默认（${escapeHtml(defaultTtsModel || "OP_TTS_MODEL / heygen-voice-1-turbo:free")}）</option>`]
      .concat(ttsModels.map((m) => `<option value="${escapeHtml(m.id)}">${escapeHtml(modelLabel(m.id, m.name))}</option>`));
    if (ort.model && !ttsModels.some((m) => m.id === ort.model)) {
      tOptions.push(`<option value="${escapeHtml(ort.model)}">${escapeHtml(ort.model)}（不在当前清单里）</option>`);
    }
    tSel.innerHTML = tOptions.join("");
    tSel.value = ort.model || "";
    // 即梦：model 下拉（画布目录，tables.js 与后端 _IMAGE_MODELS 同源）+ 分辨率档按模型配对；
    // 已存值不在清单/档位时补一项（存旧值不丢）。空 = env/默认链（seedream_4.7 · 2K）。
    const dm = settings.dreamina || {};
    const dmi = settings.dreamina_image_default || {}; // 生效默认（DB > env > 缺省）
    const dmModelSel = $("#set-dreamina-model");
    const dmResSel = $("#set-dreamina-resolution");
    const dmEntries = Object.entries(DREAMINA_IMAGE_MODELS);
    const dmOptions = [`<option value="">跟随默认（当前生效 ${escapeHtml(dmi.model || DREAMINA_DEFAULT_IMAGE_MODEL)}）</option>`]
      .concat(dmEntries.map(([id, e]) => `<option value="${escapeHtml(id)}">${escapeHtml(e.label)}</option>`));
    if (dm.model && !dmEntries.some(([id]) => id === dm.model)) {
      dmOptions.push(`<option value="${escapeHtml(dm.model)}">${escapeHtml(dm.model)}（不在当前清单）</option>`);
    }
    dmModelSel.innerHTML = dmOptions.join("");
    dmModelSel.value = dm.model || "";
    const dreaminaResolutionOptions = (modelId, current, keepUnknown) => {
      const entry = DREAMINA_IMAGE_MODELS[modelId] || DREAMINA_IMAGE_MODELS[DREAMINA_DEFAULT_IMAGE_MODEL];
      const tiers = entry.resolutions;
      const effRes = modelId ? "" : dmi.resolution || "";
      const opts = [`<option value="">（跟随 env 默认${effRes ? `，当前生效 ${escapeHtml(effRes)}` : ""}）</option>`]
        .concat(tiers.map((t) => `<option value="${escapeHtml(t)}">${escapeHtml(t)}</option>`));
      if (keepUnknown && current && !tiers.includes(current)) {
        opts.push(`<option value="${escapeHtml(current)}">${escapeHtml(current)}（不在档位里）</option>`);
      }
      dmResSel.innerHTML = opts.join("");
      dmResSel.value = tiers.includes(current) || (keepUnknown && current) ? current : "";
    };
    dreaminaResolutionOptions(dm.model || "", dm.resolution || "", true);
    dmModelSel.onchange = () => dreaminaResolutionOptions(dmModelSel.value, dmResSel.value, false);
    // 即梦视频：模型下拉来自 tables.js 静态枚举表；已存值不在清单里时补一项
    const dv = settings.dreamina_video || {};
    const dvTable = typeof DREAMINA_VIDEO_MODELS === 'undefined' ? {} : DREAMINA_VIDEO_MODELS;
    const dvModels = Object.keys(dvTable);
    const dvSel = $("#set-dreamina-video-model");
    const dvDefaultHtml = escapeHtml(settings.dreamina_video_default_model || DREAMINA_DEFAULT_VIDEO_MODEL);
    const dvOptions = [`<option value="">跟随默认（${dvDefaultHtml}）</option>`]
      .concat(dvModels.map((m) => `<option value="${m}">${m}</option>`));
    if (dv.model && !dvModels.includes(dv.model)) {
      dvOptions.push(`<option value="${escapeHtml(dv.model)}">${escapeHtml(dv.model)}（不在当前清单里）</option>`);
    }
    dvSel.innerHTML = dvOptions.join("");
    dvSel.value = dv.model || "";
    // 放在卡片标题行右侧，长句塞不进标题行
    $("#dreamina-cli-status").textContent = settings.dreamina_cli_available === false
      ? "⚠ 未找到 dreamina-canvas CLI：安装并 dreamina-canvas auth login 后才能出图"
      : "dreamina-canvas CLI 已就绪（积分余额用 `dreamina-canvas auth account` 自查）";
    const sizes = $("#settings-sizes");
    sizes.innerHTML = "";
    for (const [kind, size] of Object.entries(settings.image_sizes)) {
      const label = SLOT_LABELS[kind] || kind;
      const div = document.createElement("label");
      div.innerHTML = `${label}
        <div class="size-row">
          <select data-size-aspect="${kind}">${ASPECT_OPTIONS.map((r) => `<option value="${r}" ${r === size.aspect_ratio ? "selected" : ""}>${r}</option>`).join("")}</select>
          <select data-size-res="${kind}">${["1k", "2k"].map((r) => `<option value="${r}" ${r === size.resolution ? "selected" : ""}>${r}</option>`).join("")}</select>
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
  // openrouter：凭据整行发送（base_url/model 空串要能落库=回到 env）；
  // api_key 空串 = 后端保留已存值；分辨率/质量收不起来（模型没声明）时
  // select 值就是空串 → 落库空 = env 默认
  patch.openrouter = {
    base_url: $("#set-or-base").value.trim(),
    api_key: $("#set-or-key").value.trim(),
    t2i_model: $("#set-or-t2i-model").value.trim(),
    t2i_resolution: $("#set-or-t2i-res-field").hidden ? "" : $("#set-or-t2i-resolution").value,
    t2i_quality: $("#set-or-t2i-quality-field").hidden ? "" : $("#set-or-t2i-quality").value,
    i2i_model: $("#set-or-i2i-model").value.trim(),
    i2i_resolution: $("#set-or-i2i-res-field").hidden ? "" : $("#set-or-i2i-resolution").value,
    i2i_quality: $("#set-or-i2i-quality-field").hidden ? "" : $("#set-or-i2i-quality").value,
  };
  // 即梦：model 空串 = 落库为空 = 回到 env / CLI 默认；分辨率档空 = env（2K）默认
  patch.dreamina = {
    model: $("#set-dreamina-model").value.trim(),
    resolution: $("#set-dreamina-resolution").value,
  };
  // OpenRouter 视频：model 空串 = 落库为空 = 回到 env（OP_VIDEO_MODEL）/ heygen 默认
  patch.openrouter_video = {
    model: $("#set-or-video-model").value.trim(),
  };
  // 即梦视频：model 空串 = 落库为空 = 回到 env / seedance_2.0_fast_vip 默认
  patch.dreamina_video = {
    model: $("#set-dreamina-video-model").value.trim(),
  };
  // OpenRouter 语音：model 空串 = 落库为空 = 回到 env（OP_TTS_MODEL）/ 免费默认
  patch.openrouter_tts = {
    model: $("#set-or-tts-model").value.trim(),
  };
  // 放大页默认：workflow 空串 = 落库为空 = adhoc_upscale 兜底 workflow
  patch.upscale = {
    backend: $("#set-ups-backend").value,
    target: $("#set-ups-target").value,
    workflow: $("#set-ups-workflow").value.trim(),
  };
  try {
    await api("/settings", { method: "PUT", body: JSON.stringify(patch) });
    setStatus($("#settings-status"), "设置已保存");
    await loadSettings();
  } catch (err) {
    setStatus($("#settings-status"), `保存失败：${err.message}`, true);
  }
});

/* 拉取 OpenRouter 模型目录（生图 / 视频 / 语音三个端点共用）：refresh=1 强制重新拉取
   并覆盖本地缓存，然后 loadSettings() 让下拉按新目录重算（生图还要按新清单重算档位）。
   三个端点返回形状不同（models / data），计数取法由调用方给。 */
async function refreshModelCatalog(path, countOf, statusEl) {
  setStatus(statusEl, "拉取中…");
  try {
    const data = await api(path);
    setStatus(statusEl, `已刷新 ${countOf(data)} 个模型`);
    await loadSettings();
  } catch (err) {
    setStatus(statusEl, `拉取失败：${err.message}`, true);
  }
}

$("#btn-or-models-refresh").addEventListener("click", () =>
  refreshModelCatalog("/openrouter/models?refresh=1", (d) => (d.models || []).length, $("#or-models-status")));
$("#btn-or-video-models-refresh").addEventListener("click", () =>
  refreshModelCatalog("/videos/models?refresh=1", (d) => (d.data || []).length, $("#or-video-models-status")));
$("#btn-or-tts-models-refresh").addEventListener("click", () =>
  refreshModelCatalog("/tts/models?refresh=1", (d) => (d.data || []).length, $("#or-tts-models-status")));

loadSettings();
