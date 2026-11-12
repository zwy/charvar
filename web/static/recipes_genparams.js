/* 配方页「生成参数」面板（运行级生成参数）。
   随「开始运行」随 POST 下发并锁进 manifest（continue 复用同一组；换参数 = 重开一次运行）。
   优先级 运行级显式 > 步级显式 > 设置页/env 默认链；空值 = 跟随默认链。
   面板值持久化 localStorage（切后端不重置该后端已选值）。
   数据源不造第二份表：OR 生图 = /openrouter/models + /openrouter/capabilities（caps 收展模式
   见 adhoc.js）；即梦 = tables.js DREAMINA_IMAGE_MODELS / DREAMINA_VIDEO_MODELS；
   OR 视频 = /videos/models（条目自带 supported_resolutions）。
   依赖全局：$ / $$ / api / escapeHtml（core.js）；DREAMINA_*（tables.js）。 */

const RP_GEN_STORAGE_KEY = "charvar.recipeGenParams";

const rpGenPanel = {
  image: {
    backend: "openrouter",
    openrouter: { model: "", resolution: "", quality: "" }, // 空 = 跟随设置默认模型/档位
    comfyui: { model: "", resolution: "2k", quality: "" },
    dreamina: { model: "", resolution: "" },
  },
  video: {
    backend: "openrouter",
    openrouter: { model: "", resolution: "" },
    dreamina: { model: "", resolution: "" },
  },
  orImageModels: [], // /openrouter/models 的 [{id, name}]
  orVideoModels: [], // /videos/models 的 data（条目带 supported_resolutions）
  caps: null,        // 当前 OpenRouter 生图模型的枚举（resolutions / qualities）
  capsToken: 0,      // 竞态守卫：只采纳最后一次拉取的能力
};

/* 恢复 localStorage 存量：只采信字符串字段，坏存档回落默认；下拉填充时选项不在即自动回落 */
function rpGenLoad() {
  try {
    const saved = JSON.parse(localStorage.getItem(RP_GEN_STORAGE_KEY) || "null");
    if (!saved || !saved.image || !saved.video) return;
    for (const group of ["image", "video"]) {
      const s = saved[group], d = rpGenPanel[group];
      if (s.backend in d) d.backend = s.backend;
      for (const be of Object.keys(d).filter((k) => k !== "backend")) {
        if (!s[be]) continue;
        for (const key of Object.keys(d[be])) {
          if (typeof s[be][key] === "string") d[be][key] = s[be][key];
        }
      }
    }
  } catch { /* 坏存档：回落默认 */ }
}

function rpGenSave() {
  const { image, video } = rpGenPanel;
  localStorage.setItem(RP_GEN_STORAGE_KEY, JSON.stringify({
    image: { backend: image.backend, ...cloneRp(image) },
    video: { backend: video.backend, ...cloneRp(video) },
  }));
}

function cloneRp(group) {
  const out = {};
  for (const be of Object.keys(group).filter((k) => k !== "backend")) {
    out[be] = { ...group[be] };
  }
  return out;
}

function rpSegActivate(sel, value) {
  $$(sel + " .seg").forEach((btn) => btn.classList.toggle("active", btn.dataset.backend === value));
}

/* items: [[value, label], ...]；keep 不在项里就回落第 0 项，返回实际选中值 */
function rpFillSelect(sel, items, keep) {
  const el = $(sel);
  el.innerHTML = items.map(([v, t]) => `<option value="${escapeHtml(v)}">${escapeHtml(t)}</option>`).join("");
  const values = items.map(([v]) => v);
  el.value = values.includes(keep || "") ? (keep || "") : values[0];
  return el.value;
}

/* ---------- 生图组的面板联动（按后端填充模型/分辨率/质量下拉） ---------- */
async function rpApplyImagePanel() {
  const group = rpGenPanel.image;
  const be = group.backend;
  rpSegActivate("#rp-image-backend", be);
  $("#rp-image-model-field").hidden = be === "comfyui"; // comfyui 无模型枚举（workflow 在设置页/步字段）
  $("#rp-image-quality-field").hidden = be !== "openrouter";
  if (be === "comfyui") {
    group.comfyui.resolution = rpFillSelect(
      "#rp-image-resolution", [["1k", "1k"], ["2k", "2k"]], group.comfyui.resolution || "2k");
    $("#rp-image-resolution-field").hidden = false;
    return;
  }
  if (be === "dreamina") {
    const items = [["", "跟随设置默认"]].concat(
      Object.entries(DREAMINA_IMAGE_MODELS).map(([id, e]) => [id, e.label]));
    group.dreamina.model = rpFillSelect("#rp-image-model", items, group.dreamina.model);
    rpApplyDreaminaImageRes();
    return;
  }
  // openrouter：模型下拉 + 具体模型的能力枚举（caps 拉取异步，先填当前值兜底）
  const items = [["", "跟随设置默认"]].concat(rpGenPanel.orImageModels.map((m) => [m.id, modelLabel(m.id, m.name)]));
  group.openrouter.model = rpFillSelect("#rp-image-model", items, group.openrouter.model);
  if (group.openrouter.model) rpLoadImageCaps(group.openrouter.model);
  else rpApplyImageCaps(null);
}

function rpApplyDreaminaImageRes() {
  const g = rpGenPanel.image.dreamina;
  const entry = DREAMINA_IMAGE_MODELS[g.model];
  const items = [["", "跟随默认"]].concat((entry ? entry.resolutions : []).map((v) => [v, v]));
  g.resolution = rpFillSelect("#rp-image-resolution", items, g.resolution);
  $("#rp-image-resolution-field").hidden = items.length === 1;
}

async function rpLoadImageCaps(modelId) {
  const token = (rpGenPanel.capsToken += 1);
  try {
    const caps = await api(`/openrouter/capabilities?model=${encodeURIComponent(modelId)}`);
    if (token === rpGenPanel.capsToken) {
      rpGenPanel.caps = caps || null;
      rpApplyImageCaps(rpGenPanel.caps);
    }
  } catch {
    if (token === rpGenPanel.capsToken) {
      rpGenPanel.caps = null;
      rpApplyImageCaps(null);
    }
  }
}

function rpApplyImageCaps(caps) {
  const g = rpGenPanel.image.openrouter;
  const enums = (caps && caps.resolutions) || [];
  const resItems = [["", "跟随默认"]].concat(enums.map((v) => [v, v]));
  g.resolution = rpFillSelect("#rp-image-resolution", resItems, g.resolution);
  $("#rp-image-resolution-field").hidden = resItems.length === 1; // 清单为空的模型：档位跟默认链，面板不造参数
  const qItems = [["", "跟随默认"]].concat(((caps && caps.qualities) || []).map((v) => [v, v]));
  g.quality = rpFillSelect("#rp-image-quality", qItems, ""); // 质量不跨模型保留（枚举按模型变）
  $("#rp-image-quality-field").hidden = qItems.length === 1;
}

/* ---------- 生视频组的面板联动 ---------- */
function rpApplyVideoPanel() {
  const group = rpGenPanel.video;
  rpSegActivate("#rp-video-backend", group.backend);
  if (group.backend === "dreamina") {
    const items = [["", "跟随设置默认"]].concat(
      Object.entries(DREAMINA_VIDEO_MODELS).map(([id, e]) => [id, e.label]));
    group.dreamina.model = rpFillSelect("#rp-video-model", items, group.dreamina.model);
    const entry = DREAMINA_VIDEO_MODELS[group.dreamina.model];
    const resItems = [["", "跟随默认"]].concat((entry ? entry.resolutions : []).map((v) => [v, v]));
    group.dreamina.resolution = rpFillSelect("#rp-video-resolution", resItems, group.dreamina.resolution);
    $("#rp-video-resolution-field").hidden = resItems.length === 1;
  } else {
    const items = [["", "跟随设置默认"]].concat(rpGenPanel.orVideoModels.map((m) => [m.id, modelLabel(m.id, m.name)]));
    group.openrouter.model = rpFillSelect("#rp-video-model", items, group.openrouter.model);
    const entry = rpGenPanel.orVideoModels.find((m) => m.id === group.openrouter.model);
    const resItems = [["", "跟随默认"]].concat(((entry && entry.supported_resolutions) || []).map((v) => [v, v]));
    group.openrouter.resolution = rpFillSelect("#rp-video-resolution", resItems, group.openrouter.resolution);
    $("#rp-video-resolution-field").hidden = resItems.length === 1;
  }
}

/* ---------- 下发与摘要 ---------- */
function recipeGenPayload() {
  const { image, video } = rpGenPanel;
  return {
    image: {
      backend: image.backend,
      model: image[image.backend].model || "",
      resolution: image[image.backend].resolution || "",
      quality: (image.backend === "openrouter" ? image.openrouter.quality : "") || "",
    },
    video: {
      backend: video.backend,
      model: video[video.backend].model || "",
      resolution: video[video.backend].resolution || "",
    },
  };
}

/* 运行详情弹层 meta 行的只读摘要（manifest 的 gen 回填） */
function recipeGenSummary(gen) {
  if (!gen) return "";
  const gi = gen.image || {}, gv = gen.video || {};
  const img = [gi.backend || "", gi.model || "", gi.resolution || ""].filter(Boolean).join(" · ");
  const vid = [gv.backend || "", gv.model || "", gv.resolution || ""].filter(Boolean).join(" · ");
  return `生图 ${img || "默认"} · 生视频 ${vid || "默认"}`;
}

/* ---------- 初始化（DOMContentLoaded）---------- */
async function initRpGenPanel() {
  rpGenLoad();
  $("#rp-image-backend").addEventListener("click", (ev) => {
    const btn = ev.target.closest(".seg");
    if (!btn || btn.dataset.backend === rpGenPanel.image.backend) return;
    rpGenPanel.image.backend = btn.dataset.backend;
    rpGenSave();
    rpApplyImagePanel();
  });
  $("#rp-video-backend").addEventListener("click", (ev) => {
    const btn = ev.target.closest(".seg");
    if (!btn || btn.dataset.backend === rpGenPanel.video.backend) return;
    rpGenPanel.video.backend = btn.dataset.backend;
    rpGenSave();
    rpApplyVideoPanel();
  });
  for (const [sel, group, key, refresh] of [
    ["#rp-image-model", "image", "model", true],
    ["#rp-image-resolution", "image", "resolution", false],
    ["#rp-image-quality", "image", "quality", false],
    ["#rp-video-model", "video", "model", true],
    ["#rp-video-resolution", "video", "resolution", false],
  ]) {
    $(sel).addEventListener("change", () => {
      const g = rpGenPanel[group];
      g[g.backend][key] = $(sel).value;
      rpGenSave();
      if (refresh) group === "image" ? rpApplyImagePanel() : rpApplyVideoPanel();
    });
  }
  // 目录拉取失败不阻塞面板：下拉回落「跟随默认」单项
  await Promise.allSettled([
    api("/openrouter/models").then((d) => { rpGenPanel.orImageModels = (d && d.models) || []; }),
    api("/videos/models").then((d) => { rpGenPanel.orVideoModels = (d && d.data) || []; }),
  ]);
  rpApplyImagePanel();
  rpApplyVideoPanel();
}

document.addEventListener("DOMContentLoaded", initRpGenPanel);
