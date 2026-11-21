/* 供应商生图参数面板共享引擎（预设页 gen.js / 图片页 adhoc.js —— 同一份枚举与联动逻辑）。
   页面只留自己的说明行文案与页面特有显隐；字段 id 约定 <prefix>（gen / adhoc）：

   #<p>-backend（segmented 容器）· #<p>-negative-field · #<p>-model-field（说明行）
   #<p>-wait-field（可选，cfg.waitField = 有这个字段要按 ComfyUI 显隐）
   #<p>-resolution-field / #<p>-resolution · #<p>-quality-field / #<p>-quality
   #<p>-model-select-field / #<p>-model-select（即梦模型下拉）

   cfg: { prefix, waitField?, getMode() → "t2i"|"i2i", withRefs() → bool, onRender() }
   getMode/withRefs 由页面给（预设页按参考图数量推导、图片页按页签显式选择），
   onRender 在每次面板变化后被调（页面渲染说明行 / 提示）。

   mkPanel(ctx?) 返回的实例方法在 ctx 缺省时按 cfg 现算：
   apply(ctx?)     按后端整面板重算（显隐 + 选项 + OR caps 异步拉取）
   caps()          当前 OR 模型能力 / modeModel
   parseLongSide   档位长边换算（与后端 exporters.dimensions_for 通用分支一致）
   QWEN_2K_SIZES   Qwen-Image-2.1 官方 ~4.2MP 像素表（后端 charvar/exporters/comfyui.py
                   _QWEN_2K_SIZES 手工同源，改一处另一处要同步） */

const ImagePanel = (() => {
  // Qwen-Image-2.1 官方 2K 像素（面积恒定 ~4.2MP，按面积定义不是长边）
  const QWEN_2K_SIZES = {
    "1:1": [2048, 2048], "4:3": [2400, 1792], "3:4": [1792, 2400],
    "4:5": [1856, 2320], "3:2": [2528, 1696], "2:3": [1696, 2528],
    "16:9": [2752, 1536], "9:16": [1536, 2752],
  };

  function parseLongSide(value) {
    const m = String(value || "").trim().match(/^(\d+(?:\.\d+)?)\s*([kK])?$/);
    if (!m) return null;
    return Math.max(64, Math.round(parseFloat(m[1]) * (m[2] ? 1024 : 1)));
  }

  function mkPanel(cfg) {
    const prefix = cfg.prefix;
    const $id = (name) => $(`#${prefix}-${name}`);
    const st = {
      comfyTier: "2k",   // ComfyUI 分辨率档（记住上次选择，切供应商不丢）
      orResolution: "",  // OpenRouter 分辨率（记住上次选择）
      caps: null,        // 当前 OR 模型能力（清单 supported_parameters 推导）
      modeModel: "",     // OR 当前模型（跟随设置页，面板不选模型）
      capsToken: 0,      // 后端/模式切换的竞态守卫：只采纳最后一次拉取的能力
      capsLoading: false,
      dreaminaCliOk: undefined,
      openrouterSettings: null,
    };

    /* ---------- OpenRouter：档位按模型清单出枚举 ---------- */
    async function loadCaps(mode, settings) {
      const token = (st.capsToken += 1);
      const settle = (fn) => {
        if (token === st.capsToken) fn();
      };
      st.capsLoading = true;
      cfg.onRender();
      st.modeModel = "";
      try {
        if (!settings) settings = await api("/settings");
        st.openrouterSettings = settings;
        const or = (settings && settings.openrouter) || {};
        const model =
          (or && or[`${mode}_model`]) ||
          (mode === "i2i" ? (settings && settings.openrouter_default_i2i_model) : (settings && settings.openrouter_default_t2i_model)) ||
          "";
        st.modeModel = model;
        if (!model) {
          settle(() => {
            st.capsLoading = false;
            st.caps = null;
            applyCaps(null);
          });
          return;
        }
        const caps = await api(`/openrouter/capabilities?model=${encodeURIComponent(model)}`);
        settle(() => {
          st.capsLoading = false;
          applyCaps(caps);
        });
      } catch {
        settle(() => {
          st.capsLoading = false;
          st.caps = null;
          applyCaps(null);
        });
      }
    }

    function applyCaps(caps) {
      st.caps = caps || null;
      const resSelect = $id("resolution");
      const resField = $id("resolution-field");
      const enums = (caps && caps.resolutions) || [];
      if (enums.length) {
        resSelect.innerHTML = [`<option value="">跟随设置默认</option>`]
          .concat(enums.map((value) => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`))
          .join("");
        // 默认 2K（枚举里有，大小写不敏感）；上次选过且还在枚举里就沿用
        const twoK = enums.find((v) => String(v).toLowerCase() === "2k");
        const keep = enums.find((v) => v === st.orResolution);
        resSelect.value = keep || twoK || "";
        resField.hidden = false;
      } else {
        // muse-image 这类清单为空的模型：档位只跟设置页/env，面板不造参数
        resSelect.innerHTML = `<option value=""></option>`;
        resSelect.value = "";
        resField.hidden = true;
      }
      const qualities = (caps && caps.qualities) || [];
      const qSelect = $id("quality");
      qSelect.innerHTML = [`<option value="">跟随设置默认</option>`]
        .concat(qualities.map((q) => `<option value="${escapeHtml(q)}">${escapeHtml(q)}</option>`))
        .join("");
      qSelect.value = "";
      $id("quality-field").hidden = qualities.length === 0;
      cfg.onRender();
    }

    /* ---------- ComfyUI：档位固定 charvar 档（1k/2k） ---------- */
    function applyTierOptions(keep) {
      const select = $id("resolution");
      select.innerHTML =
        `<option value="1k">1k（长边 1024）</option>` +
        `<option value="2k">2k（Qwen 官方 ~4.2MP）</option>`;
      select.value = keep !== undefined ? keep : (st.comfyTier || "2k");
      $id("resolution-field").hidden = false;
      $id("quality-field").hidden = true;
    }

    /* ---------- 即梦：模型下拉 + 分辨率随模型配对（tables.js 目录与后端同源） ---------- */
    function applyDreaminaResolutionOptions(dreaminaDefaults) {
      const def = dreaminaDefaults || {};
      const modelValue = $id("model-select").value;
      const sel = $id("resolution");
      const base = modelValue || def.model || DREAMINA_DEFAULT_IMAGE_MODEL;
      const tiers = (DREAMINA_IMAGE_MODELS[base] || DREAMINA_IMAGE_MODELS[DREAMINA_DEFAULT_IMAGE_MODEL]).resolutions;
      sel.innerHTML = [`<option value="">跟随设置默认（${def.resolution || "2K"}）</option>`]
        .concat(tiers.map((t) => `<option value="${escapeHtml(t)}">${escapeHtml(t)}</option>`))
        .join("");
      sel.value = modelValue ? (tiers.includes("2K") ? "2K" : tiers[0]) : "";
      $id("resolution-field").hidden = false;
      $id("quality-field").hidden = true;
    }

    function applyDreaminaOptions({ withRefs = false, dreaminaDefaults = {} } = {}) {
      const def = dreaminaDefaults || {};
      const defLabel = `跟随设置默认（${def.model || DREAMINA_DEFAULT_IMAGE_MODEL} · ${def.resolution || "2K"}）`;
      // 带参考图（i2i 语境）时不列连文生图都不收参考的模型，免得选到提交前才被拦
      const models = Object.entries(DREAMINA_IMAGE_MODELS)
        .filter(([, e]) => !withRefs || (e.refMax.i2i > 0 || e.refMax.t2i > 0));
      const sel = $id("model-select");
      const keep = sel.value;
      sel.innerHTML = [`<option value="">${defLabel}</option>`]
        .concat(models.map(([id, e]) => `<option value="${escapeHtml(id)}">${escapeHtml(e.label)}</option>`))
        .join("");
      sel.value = models.some(([id]) => id === keep) ? keep : "";
      $id("model-select-field").hidden = false;
      applyDreaminaResolutionOptions(dreaminaDefaults);
    }

    /* ---------- 整面板：按后端重算显隐与选项 ---------- */
    async function apply(ctx = {}) {
      const backend = ctx.backend;
      const isComfy = backend === "comfyui";
      const isOpenRouter = backend === "openrouter";
      const isDreamina = backend === "dreamina";
      $id("negative-field").hidden = !isComfy; // 仅 ComfyUI 有负向词
      $id("model-field").hidden = isComfy;     // 模型说明行：OR / 即梦才显示
      $id("model-select-field").hidden = !isDreamina;
      if (cfg.waitField) $id("wait-field").hidden = !isComfy; // 仅 ComfyUI 支持等待完成
      if (isComfy) {
        applyTierOptions();
      } else if (isOpenRouter) {
        // 能力异步回来才定枚举；先收起，等 applyCaps 再展开
        st.caps = null;
        st.modeModel = "";
        $id("resolution-field").hidden = true;
        $id("quality-field").hidden = true;
        cfg.onRender();
        const mode = cfg.getMode ? cfg.getMode() : (ctx.mode || "t2i");
        loadCaps(mode, ctx.settings);
      } else {
        applyDreaminaOptions({ withRefs: ctx.withRefs, dreaminaDefaults: ctx.dreaminaDefaults });
        cfg.onRender();
      }
    }

    /* ---------- 事件绑定（档位记忆 / 即梦模型联动） ---------- */
    function bind() {
      $id("resolution").addEventListener("change", () => {
        const backend = cfg.getBackend();
        if (backend === "comfyui") st.comfyTier = $id("resolution").value;
        else if (backend === "openrouter") st.orResolution = $id("resolution").value;
        cfg.onRender();
      });
      $id("model-select").addEventListener("change", () => {
        // 即梦：换模型 → 分辨率档随模型重配
        applyDreaminaResolutionOptions(cfg.getDreaminaDefaults() || {});
        cfg.onRender();
      });
    }

    return {
      apply,
      loadCaps,
      applyCaps,
      applyTierOptions,
      applyDreaminaOptions,
      applyDreaminaResolutionOptions,
      bind,
      get comfyTier() { return st.comfyTier; },
      get orResolution() { return st.orResolution; },
      get caps() { return st.caps; },
      get modeModel() { return st.modeModel; },
      get capsLoading() { return st.capsLoading; },
      get openrouterSettings() { return st.openrouterSettings; },
      get dreaminaCliOk() { return st.dreaminaCliOk; },
      setDreaminaCli(value) { st.dreaminaCliOk = value; },
    };
  }

  return { mkPanel, QWEN_2K_SIZES, parseLongSide };
})();
