/* 视频（Playground）页（web 面板）：
   二级页签 = 供应商（OpenRouter / 即梦），一级页签 = 模式（文生视频 t2v /
   图生视频 i2v），显式选择。
   与出图（adhoc.js）同构：同一套暂存参考图四件套、GenRefs 弹层选图（主体档案 /
   动作库 / 人脸库 / 服饰库，见 genrefs.js）、供应商切换（档位切走再切回不重置）。
   输入区按「供应商 × 选中模型」的能力显隐（videoInputCaps）：
   - OpenRouter：按目录 supported_frame_images 定首帧/尾帧；参考图（input_references
     ≤4，图+视频合计）与参考视频任一模型可发（被拒自动降级）；
   - 即梦：按 DREAMINA_VIDEO_MODELS 的 modes（t2v / m2v / first_last_frame）与
     m2v_ref（图/视频/合计上限）；
   - 即梦首帧与参考素材互斥（后端 dispatch_mode 会拦），前端提交前拦。
   关键差异：视频是异步作业 —— 提交即返回 run_id，前端每 8s 轮询
   /videos/{run_id}/status 到 ready/failed；页面载入时找未终态的 run 恢复轮询
   （跨刷新不断线）；即梦态：seed 收起（即梦不暴露）、无逐条成本预估（按积分计费）。 */

const videoState = {
  backend: "openrouter",
  mode: "t2v",
  catalog: null,
  references: [],
  videoReferences: [],
  firstFrame: null,
  lastFrame: null,
  activeRun: null,
  pollTimer: null,
  capsToken: 0,
  /* 切供应商时各记各的档位（切回不重置）；dreaminaCliOk/即梦默认模型懒加载自 /settings */
  saved: {
    openrouter: { model: "", aspect: "", resolution: "", duration: "" },
    dreamina: { model: "", aspect: "", resolution: "", duration: "" },
  },
  dreaminaCliOk: undefined,
  dreaminaDefaults: null,
};

function videoSegToggle(containerSel, attr, value) {
  $$(containerSel + " .seg").forEach((btn) =>
    btn.classList.toggle("active", btn.dataset[attr] === value)
  );
}

/* ---------- 模型目录 ---------- */

async function loadVideoCatalog(refresh) {
  try {
    videoState.catalog = await api(`/videos/models${refresh ? "?refresh=true" : ""}`);
  } catch (err) {
    videoState.catalog = { data: [], default_model: "" };
    setStatus($("#video-status"), `模型目录加载失败：${err.message}`, true);
  }
  applyVideoCatalog();
}

function applyVideoCatalog() {
  const catalog = videoState.catalog || { data: [], default_model: "" };
  const models = catalog.data || [];
  const sel = $("#video-model");
  const def = catalog.default_model || "";
  sel.innerHTML = [`<option value="">跟随默认（${escapeHtml(def || "未配置")}）</option>`]
    .concat(models.map((m) =>
      `<option value="${escapeHtml(m.id)}">${escapeHtml(modelLabel(m.id, m.name))}</option>`))
    .join("");
  sel.value = "";
  applyVideoEnums();
}

function currentVideoModelEntry() {
  const catalog = videoState.catalog || { data: [], default_model: "" };
  const chosen = $("#video-model").value;
  const modelId = chosen || catalog.default_model || "";
  return (catalog.data || []).find((m) => m.id === modelId) || null;
}

function videoEnum(entry, key) {
  return (entry && Array.isArray(entry[key]) && entry[key].length)
    ? entry[key].map(String) : [];
}

/* 枚举落到三个 select：默认取第一项（heygen 目录里正好是 480p / 5s / 列表序）；
   目录缺失（未知模型）时收起该字段（跟随 provider 默认），别在前端造参数。
   seed 字段随模型声明显隐（seed:false 的模型如 avatar-iv 收起）。 */
function applyVideoEnums(restore = false) {
  if (videoState.backend === "dreamina") return applyDreaminaVideoEnums(restore);
  const entry = currentVideoModelEntry();
  const aspects = videoEnum(entry, "supported_aspect_ratios");
  const resList = videoEnum(entry, "supported_resolutions");
  const durations = videoEnum(entry, "supported_durations");

  const aspectSel = $("#video-aspect");
  aspectSel.innerHTML = aspects.length
    ? aspects.map((a) => `<option value="${escapeHtml(a)}">${escapeHtml(a)}</option>`).join("")
    : `<option value="">跟随 provider 默认</option>`;
  if (aspects.includes("9:16")) aspectSel.value = "9:16";
  else if (aspects.length) aspectSel.value = aspects[0];
  else aspectSel.value = "";
  aspectSel.parentElement.hidden = aspects.length === 0;

  const resSel = $("#video-resolution");
  resSel.innerHTML = resList.length
    ? resList.map((r) => `<option value="${escapeHtml(r)}">${escapeHtml(r)}</option>`).join("")
    : `<option value="">跟随 provider 默认</option>`;
  resSel.value = resList.length ? resList[0] : "";
  resSel.parentElement.hidden = resList.length === 0;

  const durSel = $("#video-duration");
  durSel.innerHTML = durations.length
    ? durations.map((d) => `<option value="${escapeHtml(String(d))}">${escapeHtml(String(d))}s</option>`).join("")
    : `<option value="">跟随 provider 默认</option>`;
  durSel.value = durations.length ? String(durations[0]) : "";
  durSel.parentElement.hidden = durations.length === 0;

  if (restore) {
    const saved = videoState.saved.openrouter;
    const apply = (sel, value) => {
      if (value && [...sel.options].some((o) => o.value === value)) sel.value = value;
    };
    const mSel = $("#video-model");
    if (saved.model && [...mSel.options].some((o) => o.value === saved.model)) mSel.value = saved.model;
    apply($("#video-aspect"), saved.aspect);
    apply($("#video-resolution"), saved.resolution);
    apply($("#video-duration"), saved.duration);
  }
  $("#video-seed-field").hidden = Boolean(entry && entry.seed === false);
  renderVideoModelInfo();
  applyVideoMode(); // 模型变了 → 能力（首帧/尾帧/参考图资质）可能变
  updateVideoCostHint();
}

/* ---------- 即梦（Dreamina）供应商 ---------- */

/* /settings 懒加载一次：即梦 CLI 是否可用 + 视频默认模型（DB > env > 常量）。 */
async function ensureDreaminaDefaults() {
  if (videoState.dreaminaDefaults !== null) return;
  try {
    const s = await api("/settings");
    videoState.dreaminaDefaults = {
      model: s.dreamina_video_default_model || DREAMINA_DEFAULT_VIDEO_MODEL,
      cliOk: s.dreamina_cli_available !== false,
    };
  } catch {
    videoState.dreaminaDefaults = { model: DREAMINA_DEFAULT_VIDEO_MODEL, cliOk: true };
  }
}

/* 模式过滤：t2v（文生视频页签，可带参考图走全能参考）只列支持 t2v 的模型；
   i2v（首帧页签）找 first_last_frame 能力。 */
function dreaminaModelOptions() {
  const want = videoState.mode === "i2v" ? "first_last_frame" : "t2v";
  return Object.entries(DREAMINA_VIDEO_MODELS).filter(([, e]) => e.modes.includes(want));
}

function applyDreaminaVideoModel() {
  const sel = $("#video-dreamina-model");
  const models = dreaminaModelOptions();
  const def = videoState.dreaminaDefaults || {};
  // 当前值优先（模式/模型切换不丢已选）；当前值不在新清单才回落 saved → 跟随默认
  const keep = sel.value || videoState.saved.dreamina.model || "";
  sel.innerHTML = [`<option value="">跟随设置默认（${escapeHtml(def.model || DREAMINA_DEFAULT_VIDEO_MODEL)}）</option>`]
    .concat(models.map(([id, e]) =>
      `<option value="${escapeHtml(id)}">${escapeHtml(e.label)}</option>`))
    .join("");
  sel.value = models.some(([id]) => id === keep) ? keep : "";
  $("#video-dreamina-model-field").hidden = false;
}

function dreaminaModelEntry() {
  const id = $("#video-dreamina-model").value || (videoState.dreaminaDefaults || {}).model || "";
  return DREAMINA_VIDEO_MODELS[id] || null;
}

/* 即梦枚举：画幅按模型（i2v 首行「自动 = 按首帧推断」发空串，后端从首帧归一）、
   分辨率按模型（字面值大小写按目录透传）、时长按模型展开 lo..hi。
   restore=true（从另一供应商切回）→ 恢复 saved；restore=false（模式/模型切换）
   → 以当前值为准（不丢已选），当前值不在新枚举里才落默认。 */
function applyDreaminaVideoEnums(restore = false) {
  const entry = dreaminaModelEntry() || { resolutions: ["720p"], durations: [4, 15], ratios: DREAMINA_VIDEO_RATIOS };
  const i2v = videoState.mode === "i2v";
  const saved = videoState.saved.dreamina;
  const keep = restore
    ? { aspect: saved.aspect, resolution: saved.resolution, duration: saved.duration }
    : {
        aspect: $("#video-aspect").value,
        resolution: $("#video-resolution").value,
        duration: $("#video-duration").value,
      };
  const aspectItems = (i2v ? [["", "自动（按首帧推断）"]] : [])
        .concat((entry.ratios || DREAMINA_VIDEO_RATIOS).map((a) => [a, a]));
  const aspectSel = $("#video-aspect");
  aspectSel.innerHTML = aspectItems.map(([v, label]) =>
    `<option value="${v}">${escapeHtml(label)}</option>`).join("");
  aspectSel.parentElement.hidden = false; // 从 OpenRouter 侧切来时清掉「目录缺失收起」的泄漏
  const aspectDefault = i2v ? "" : "9:16";
  aspectSel.value = aspectItems.some(([v]) => v === keep.aspect) ? keep.aspect : aspectDefault;

  const resSel = $("#video-resolution");
  const resList = entry.resolutions || ["720p"];
  resSel.innerHTML = resList.map((r) => `<option value="${r}">${r}</option>`).join("");
  resSel.parentElement.hidden = false;
  resSel.value = resList.includes(keep.resolution) ? keep.resolution : resList[0];

  const durSel = $("#video-duration");
  const [lo, hi] = entry.durations || [4, 15];
  const durItems = [];
  for (let d = lo; d <= hi; d++) durItems.push(String(d));
  durSel.innerHTML = durItems.map((d) => `<option value="${d}">${d}s</option>`).join("");
  durSel.parentElement.hidden = false;
  durSel.value = durItems.includes(keep.duration) ? keep.duration : (durItems.includes("5") ? "5" : durItems[0]);

  $("#video-seed-field").hidden = true; // 即梦不暴露 seed
  renderVideoModelInfo();
  applyVideoMode(); // 模型变了 → 能力（首帧/尾帧/参考图资质）可能变
  updateVideoCostHint();
}

function renderDreaminaInfo(el) {
  const def = videoState.dreaminaDefaults || {};
  const modelValue = $("#video-dreamina-model").value || def.model || "";
  const entry = dreaminaModelEntry() || { resolutions: ["720p"], durations: [4, 15], ratios: DREAMINA_VIDEO_RATIOS };
  const parts = [
    `即梦 ${(DREAMINA_VIDEO_MODELS[modelValue] || {}).label || modelValue}`,
    `分辨率 ${(entry.resolutions || ["720p"]).join("/")}`,
    `时长 ${(entry.durations || [4, 15]).join("-")}s`,
    `${(entry.ratios || []).length} 档画幅`,
  ];
  if (def.cliOk === false) parts.push("⚠ dreamina-canvas CLI 不可用（设置页有提示）");
  el.textContent = parts.join(" · ");
}
function renderVideoModelInfo() {
  const el = $("#video-model-field");
  if (!el) return;
  if (videoState.backend === "dreamina") return renderDreaminaInfo(el);
  const entry = currentVideoModelEntry();
  if (!entry) {
    el.textContent = "模型目录加载中…";
    return;
  }
  const parts = [
    `模型 ${entry.name || entry.id}（${entry.id}）`,
    videoEnum(entry, "supported_resolutions").length
      ? `分辨率 ${videoEnum(entry, "supported_resolutions").join("/")}` : null,
    videoEnum(entry, "supported_durations").length
      ? `时长 ${videoEnum(entry, "supported_durations").join("/")}s` : "时长跟随 provider",
    (entry.supported_frame_images || []).includes("first_frame") ? "首帧支持" : null,
    entry.seed ? "seed 支持" : "seed 不支持（字段已收起）",
  ];
  el.textContent = parts.filter(Boolean).join(" · ");
}

/* 成本预估（展示用，不参与提交校验）。目录 pricing_skus 认两套键名：heygen 系
   duration_seconds_<分辨率小写>（美元/秒，reference_duration_seconds_* = 多图档）；
   grok 系 cents_per_video_output_second_*（美分/秒，每张输入图
   cents_per_image_input 美分）。实测对账（2026-11-06，全部成片账单）：heygen
   实收 = 目录价的一半，单图/无图按普档、参考图档无每张图费 —— 有成片历史时
   估算优先用实测每秒率（learned_rates，/videos/models 带回），目录价兜底。 */
function videoPrice(prefix) {
  const entry = currentVideoModelEntry();
  const skus = (entry && entry.pricing_skus) || {};
  const res = ($("#video-resolution").value || "").toLowerCase();
  const keys = [];
  if (res) keys.push(`${prefix}_${res}`);
  keys.push(...Object.keys(skus).filter((k) => k.startsWith(prefix)));
  for (const k of keys) {
    const v = Number(skus[k]);
    if (Number.isFinite(v)) return v;
  }
  // grok 系键名单位是美分：cents_per_video_output_second_<res>（每秒）/
  // cents_per_image_input（每张输入图）—— 换算成美元返回
  const centsPrefix = prefix === "duration_seconds"
    ? "cents_per_video_output_second"
    : prefix === "reference_input_images" ? "cents_per_image_input" : null;
  if (!centsPrefix) return null;
  const centsKeys = [];
  if (res) centsKeys.push(`${centsPrefix}_${res}`);
  centsKeys.push(...Object.keys(skus).filter((k) => k.startsWith(centsPrefix)));
  for (const k of centsKeys) {
    const v = Number(skus[k]);
    if (Number.isFinite(v)) return v / 100;
  }
  return null;
}

function updateVideoCostHint() {
  const el = $("#video-cost-hint");
  if (!el) return;
  if (videoState.backend === "dreamina") {
    el.textContent = "即梦按积分/会员权益计费，暂无逐条预估（余额 dreamina user_credit 自查）";
    return;
  }
  const seconds = Number($("#video-duration").value || 0);
  const imageCount = videoState.mode === "i2v"
    ? (videoState.firstFrame ? 1 : 0)
    : videoState.references.length;
  const res = ($("#video-resolution").value || "").toLowerCase();
  // 实测价优先：/videos/models 带回的实测每秒率（成片 usage.cost 折算，全包价）
  const entry0 = currentVideoModelEntry();
  if (entry0 && res && seconds > 0) {
    const bucket = imageCount >= 2 ? "2+" : String(imageCount);
    const learned = Number(((entry0.learned_rates || {})[`${res}|${bucket}`] || {}).per_s);
    if (Number.isFinite(learned) && learned > 0) {
      const seg = (videoState.mode === "i2v" ? "图生视频" : "文生视频") +
        (imageCount ? ` · ${imageCount} 张图` : "");
      el.textContent = `预计 ≈ $${(learned * seconds).toFixed(2)}（实测价 · ${seg} · ${seconds}s${$("#video-resolution").value ? ` · ${$("#video-resolution").value}` : ""}，以账单为准）`;
      return;
    }
  }
  // 目录 SKU 估算（分档语义按实测对账）：输入图 ≥2 才走参考图档（无每张图费）；
  // 单图/无图走普档（实测 heygen 单图不带费）；参考图档缺失的兜底保留每张图费
  const entry = currentVideoModelEntry();
  const hasRefRate = Object.keys((entry && entry.pricing_skus) || {}).some(
    (k) => k.startsWith("reference_duration_seconds")
  );
  let rate = null;
  let perImage = 0;
  if (hasRefRate) {
    if (imageCount >= 2) {
      rate = videoPrice("reference_duration_seconds");
    } else {
      rate = videoPrice("duration_seconds"); // 单图（首帧）/无图：普档，无图费
    }
  } else {
    rate = videoPrice("duration_seconds");
    perImage = imageCount > 0 ? (videoPrice("reference_input_images") || 0) : 0;
  }
  if (!rate || !seconds) {
    el.textContent = "";
    return;
  }
  const estimate = rate * seconds + perImage * imageCount;
  const seg = (videoState.mode === "i2v" ? "图生视频" : "文生视频") +
    (imageCount ? ` · ${imageCount} 张图` : "");
  el.textContent = `预计 ≈ $${estimate.toFixed(2)}（目录价 · ${seg} · ${seconds}s${$("#video-resolution").value ? ` · ${$("#video-resolution").value}` : ""}，以账单为准）`;
}
/* ---------- 模式与面板显隐 ---------- */

/* 供应商 × 选中模型的输入能力：显隐与上限都从它读。
   OpenRouter 目录未声明 supported_frame_images 时按「支持」处理
   （后端 build_body 同一语义，被拒走降级）；参考图/参考视频目录里没有
   独立声明字段 → 可发，被拒降级（与后端一致）。
   即梦：modes 含 first_last_frame 才有首帧/尾帧；含 m2v 才有参考图/参考视频
   （m2v_ref 有图/视频分项上限）。 */
function videoInputCaps() {
  if (videoState.backend === "dreamina") {
    const entry = dreaminaModelEntry() || {
      modes: ["t2v", "m2v", "first_last_frame"],
      m2v_ref: { total: 12, image: 9, video: 3 },
    };
    const modes = entry.modes || [];
    // tables.js 的表用 m2vRef（camelCase），后端表是 m2v_ref —— 两边同源只差键名风格
    const caps = entry.m2vRef || entry.m2v_ref || { total: 0, image: 0, video: 0 };
    const hasM2v = modes.includes("m2v");
    const hasFrames = modes.includes("first_last_frame");
    return {
      firstFrame: hasFrames,
      lastFrame: hasFrames,
      refs: hasM2v,
      videoRefs: hasM2v && (caps.video || 0) > 0,
      maxImage: hasM2v ? (caps.image || 0) : 0,
      maxVideo: hasM2v ? (caps.video || 0) : 0,
      maxTotal: hasM2v ? (caps.total || 0) : 0,
      exclusive: true, // 即梦首帧与参考素材互斥
    };
  }
  const entry = currentVideoModelEntry();
  const frames = (entry && entry.supported_frame_images) || [];
  return {
    firstFrame: true,
    lastFrame: !entry || frames.length === 0 || frames.includes("last_frame"),
    refs: true,
    videoRefs: true,
    maxImage: 4, maxVideo: 4, maxTotal: 4,
    exclusive: false, // 官方语义：首帧优先，参考图只作外观参考
  };
}

/* 输入区显隐：模式 × 能力。文生视频全部收起；图生视频按所选模型亮
   （首帧区恒显 —— 两供应商现有目录里所有模型都收首帧；尾帧区在模型支持
   且首帧已设时亮；参考图/参考视频区按 m2v 资质与目录）。 */
function applyVideoMode() {
  const isI2v = videoState.mode === "i2v";
  const caps = videoInputCaps();
  $("#video-frame-block").hidden = !isI2v || !caps.firstFrame;
  $("#video-lastframe-block").hidden = !isI2v || !caps.lastFrame || !videoState.firstFrame;
  $("#video-refs-block").hidden = !isI2v || !caps.refs;
  $("#video-vrefs-block").hidden = !isI2v || !caps.videoRefs;
  updateVideoRefHint();
  updateVideoVrefHint();
}

/* 参考图 / 参考视频区的标题计数：数量 + 控件里看不到的上限/互斥（灰字只补信息） */
function updateVideoRefHint() {
  const el = $("#video-ref-count");
  if (!el) return;
  const n = videoState.references.length;
  const parts = [`已选 ${n} 张`];
  const caps = videoInputCaps();
  if (videoState.mode === "i2v" || caps.refs) {
    if (videoState.backend === "dreamina" && caps.refs) {
      if (caps.maxImage < 9) parts.push(`本模型参考图 ≤${caps.maxImage}`);
      if (videoState.firstFrame) parts.push("已设首帧 —— 即梦互斥，本次不发送（请清掉首帧或清参考图）");
    } else if (videoState.backend !== "dreamina") {
      parts.push(`与参考视频合计 ≤${caps.maxTotal}`);
      if (videoState.firstFrame) parts.push("首帧优先，参考图只作外观参考");
    }
  }
  el.textContent = parts.join(" · ");
}

function updateVideoVrefHint() {
  const el = $("#video-vref-count");
  if (!el) return;
  const n = videoState.videoReferences.length;
  const parts = [`已选 ${n} 段`];
  const caps = videoInputCaps();
  if (caps.videoRefs) {
    if (videoState.backend === "dreamina") {
      if (caps.maxVideo < 10) parts.push(`本模型参考视频 ≤${caps.maxVideo}`);
      if (videoState.firstFrame) parts.push("已设首帧 —— 即梦互斥，本次不发送");
    } else {
      parts.push("与参考图合计 ≤4（仅部分模型接视频参考，被拒自动降级）");
      if (videoState.firstFrame) parts.push("首帧优先");
    }
  }
  el.textContent = parts.join(" · ");
}

/* ---------- 首帧 / 参考图列表 ---------- */

function releaseVideoRef(ref) {
  if (ref && ref.localUrl) URL.revokeObjectURL(ref.localUrl);
}

function renderVideoFrame() {
  const box = $("#video-frame-list");
  box.innerHTML = "";
  const ref = videoState.firstFrame;
  if (!ref) {
    const empty = document.createElement("div");
    empty.className = "ref-empty";
    empty.textContent = "未选首帧";
    box.appendChild(empty);
    return;
  }
  const el = document.createElement("div");
  el.className = "ref-pick";
  el.innerHTML = `${ref.url ? `<img src="${ref.url}" alt="">` : `<span class="ref-thumb-empty"></span>`}
    <span class="ref-name">${escapeHtml(ref.name || "首帧")}</span>
    <span class="badge">#首帧</span>`;
  const img = el.querySelector("img");
  if (img) {
    img.title = "预览大图";
    img.addEventListener("click", (ev) => {
      ev.stopPropagation();
      Media.showImageModal(ref.url, ref.name || "");
    });
  }
  box.appendChild(el);
}

function renderVideoRefs() {
  const box = $("#video-ref-list");
  box.innerHTML = "";
  if (!videoState.references.length) {
    const empty = document.createElement("div");
    empty.className = "ref-empty";
    empty.textContent = "暂无参考图";
    box.appendChild(empty);
  }
  videoState.references.forEach((ref, index) => {
    const el = document.createElement("div");
    el.className = "ref-pick";
    el.innerHTML = `${ref.url ? `<img src="${ref.url}" alt="">` : `<span class="ref-thumb-empty"></span>`}
      <span class="ref-name">${escapeHtml(ref.name || "参考图 " + (index + 1))}</span>
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
    del.addEventListener("click", () => removeVideoRef(index));
    el.appendChild(del);
    box.appendChild(el);
  });
  applyVideoMode();
}

async function removeVideoRef(index) {
  const ref = videoState.references[index];
  if (ref && ref.type === "staged") {
    try {
      await api(`/videos/references/${encodeURIComponent(ref.id)}`, { method: "DELETE" });
    } catch {
      /* 暂存文件可能已被清理，忽略 */
    }
  }
  releaseVideoRef(ref);
  videoState.references.splice(index, 1);
  renderVideoRefs();
  updateVideoCostHint();
}

/* 首帧/尾帧共用的单图选择（上传/最近生成/主体档案）;尾帧区显隐随 applyVideoMode */
function videoPickLastFrameRef(ref) {
  if (videoState.lastFrame) releaseVideoRef(videoState.lastFrame);
  videoState.lastFrame = ref;
  renderVideoLastFrame();
  updateVideoCostHint();
  setStatus($("#video-status"), "已设为尾帧");
}

function renderVideoLastFrame() {
  const box = $("#video-lastframe-list");
  if (!box) return;
  box.innerHTML = "";
  const ref = videoState.lastFrame;
  if (!ref) {
    const empty = document.createElement("div");
    empty.className = "ref-empty";
    empty.textContent = "未选尾帧";
    box.appendChild(empty);
    return;
  }
  const el = document.createElement("div");
  el.className = "ref-pick";
  el.innerHTML = `${ref.url ? `<img src="${ref.url}" alt="">` : `<span class="ref-thumb-empty"></span>`}
    <span class="ref-name">${escapeHtml(ref.name || "尾帧")}</span>
    <span class="badge">#尾帧</span>`;
  const img = el.querySelector("img");
  if (img) {
    img.title = "预览大图";
    img.addEventListener("click", (ev) => {
      ev.stopPropagation();
      Media.showImageModal(ref.url, ref.name || "");
    });
  }
  box.appendChild(el);
}

$("#video-frame-file").addEventListener("change", async (ev) => {
  const file = (ev.target.files || [])[0];
  ev.target.value = "";
  if (!file) return;
  if (videoState.firstFrame) releaseVideoRef(videoState.firstFrame);
  try {
    const out = await apiUpload("/videos/references", file);
    videoState.firstFrame = {
      type: "staged",
      id: out.id,
      name: out.name || file.name,
      url: URL.createObjectURL(file),
      localUrl: true,
    };
    renderVideoFrame();
    applyVideoMode(); // 首帧就位 → 亮尾帧区（模型支持时）
    updateVideoCostHint();
  } catch (err) {
    setStatus($("#video-status"), `首帧上传失败：${err.message}`, true);
  }
});

$("#video-lastframe-file").addEventListener("change", async (ev) => {
  const file = (ev.target.files || [])[0];
  ev.target.value = "";
  if (!file) return;
  try {
    const out = await apiUpload("/videos/references", file);
    videoPickLastFrameRef({
      type: "staged",
      id: out.id,
      name: out.name || file.name,
      url: URL.createObjectURL(file),
      localUrl: true,
    });
  } catch (err) {
    setStatus($("#video-status"), `尾帧上传失败：${err.message}`, true);
  }
});

$("#video-ref-file").addEventListener("change", async (ev) => {
  const files = Array.from(ev.target.files || []);
  ev.target.value = "";
  for (const file of files) {
    if (!videoCanAdd("image")) break;
    try {
      const out = await apiUpload("/videos/references", file);
      videoState.references.push({
        type: "staged",
        id: out.id,
        name: out.name || file.name,
        url: URL.createObjectURL(file),
        localUrl: true,
      });
    } catch (err) {
      setStatus($("#video-status"), `上传失败：${err.message}`, true);
    }
  }
  renderVideoRefs();
  updateVideoCostHint();
});

$("#btn-video-frame-clear").addEventListener("click", async () => {
  const ref = videoState.firstFrame;
  if (ref && ref.type === "staged") {
    try {
      await api(`/videos/references/${encodeURIComponent(ref.id)}`, { method: "DELETE" });
    } catch { /* 已清理 */ }
  }
  releaseVideoRef(ref);
  videoState.firstFrame = null;
  // 首帧清掉 → 尾帧一并清（即梦按 [首,尾] 发送，单独尾帧会被拦；区也随即隐藏）
  if (videoState.lastFrame) {
    const last = videoState.lastFrame;
    if (last.type === "staged") {
      try {
        await api(`/videos/references/${encodeURIComponent(last.id)}`, { method: "DELETE" });
      } catch { /* 已清理 */ }
    }
    releaseVideoRef(last);
    videoState.lastFrame = null;
    renderVideoLastFrame();
  }
  renderVideoFrame();
  applyVideoMode();
  updateVideoRefHint();
  updateVideoVrefHint();
  updateVideoCostHint();
});

/* 尾帧：从最近生成 / 主体档案弹层加入（与首帧同一套来源语义） */
$("#btn-video-lastframe-recent").addEventListener("click", () => {
  Media.pickRecentImage((rec) => {
    videoPickLastFrameRef({
      type: "recent",
      run_id: rec.run_id,
      name: (rec.prompt || "").slice(0, 40) || rec.run_id,
      url: `/adhoc/${encodeURIComponent(rec.run_id)}/thumb`,
    });
  }, { title: "视频 · 从最近生成选择尾帧图" });
});

$("#btn-video-lastframe-subject").addEventListener("click", () => {
  GenRefs.showSubjectBrowser((pick) => {
    videoPickLastFrameRef({
      type: "subject",
      subject_id: pick.subject_id,
      kind: pick.kind,
      name: pick.name || `${pick.subject_id} / ${pick.label || pick.kind}`,
      url: pick.url,
    });
  });
});

$("#btn-video-lastframe-clear").addEventListener("click", async () => {
  const ref = videoState.lastFrame;
  if (ref && ref.type === "staged") {
    try {
      await api(`/videos/references/${encodeURIComponent(ref.id)}`, { method: "DELETE" });
    } catch { /* 已清理 */ }
  }
  releaseVideoRef(ref);
  videoState.lastFrame = null;
  renderVideoLastFrame();
  updateVideoCostHint();
});

$("#btn-video-ref-clear").addEventListener("click", async () => {
  const refs = videoState.references.slice();
  videoState.references = [];
  renderVideoRefs();
  for (const ref of refs) {
    releaseVideoRef(ref);
    if (ref.type === "staged") {
      try {
        await api(`/videos/references/${encodeURIComponent(ref.id)}`, { method: "DELETE" });
      } catch { /* 已清理 */ }
    }
  }
});

/* ---------- 参考视频（≤2 条；Seedance gen2+ 接 video_url，其它 provider 忽略） ---------- */

function renderVideoVrefs() {
  const box = $("#video-vref-list");
  box.innerHTML = "";
  if (!videoState.videoReferences.length) {
    const empty = document.createElement("div");
    empty.className = "ref-empty";
    empty.textContent = "暂无参考视频";
    box.appendChild(empty);
  }
  videoState.videoReferences.forEach((ref, index) => {
    const el = document.createElement("div");
    el.className = "ref-pick";
    const preview = document.createElement("video");
    preview.muted = true;
    preview.preload = "metadata";
    preview.src = `/videos/video-reference/${encodeURIComponent(ref.id)}`;
    el.appendChild(preview);
    const name = document.createElement("span");
    name.className = "ref-name";
    name.textContent = ref.name || `参考视频 ${index + 1}`;
    el.appendChild(name);
    const badge = document.createElement("span");
    badge.className = "badge";
    badge.textContent = `#v${index + 1}`;
    el.appendChild(badge);
    const del = document.createElement("button");
    del.type = "button";
    del.className = "btn-icon";
    del.title = "移除";
    del.setAttribute("aria-label", "移除");
    del.innerHTML = icon("x");
    del.addEventListener("click", async () => {
      const ref0 = videoState.videoReferences[index];
      if (ref0 && ref0.type === "staged") {
        try {
          await api(`/videos/video-references/${encodeURIComponent(ref0.id)}`, { method: "DELETE" });
        } catch { /* 已清理 */ }
      }
      videoState.videoReferences.splice(index, 1);
      renderVideoVrefs();
    });
    el.appendChild(del);
    box.appendChild(el);
  });
  updateVideoVrefHint();
}

$("#video-vref-file").addEventListener("change", async (ev) => {
  const files = Array.from(ev.target.files || []);
  ev.target.value = "";
  for (const file of files) {
    if (!videoCanAdd("video")) break;
    try {
      const out = await apiUpload("/videos/video-references", file);
      videoState.videoReferences.push({ type: "staged", id: out.id, name: out.name || file.name });
    } catch (err) {
      setStatus($("#video-status"), `上传失败：${err.message}`, true);
    }
  }
  renderVideoVrefs();
});

$("#btn-video-vref-clear").addEventListener("click", async () => {
  const refs = videoState.videoReferences.slice();
  videoState.videoReferences = [];
  renderVideoVrefs();
  for (const ref of refs) {
    if (ref.type === "staged") {
      try {
        await api(`/videos/video-references/${encodeURIComponent(ref.id)}`, { method: "DELETE" });
      } catch { /* 已清理 */ }
    }
  }
});

/* ---------- 参考图弹层选图（引擎在 genrefs.js —— 与 Playground 同一套弹层） ----------
   主体档案 = 两步弹层（先选主体 → 再选一张槽位图）；动作库 / 人脸库 / 服饰库 = 对应库浏览器。
   首帧区只给主体档案弹层（首帧要有完整人物外观，姿势线稿/服装平铺当首帧不合适）；
   参考图区给全部来源。动作/人脸/服饰参考图走后端同一套 spec 解析（role 在视频通路被忽略）。 */

function videoPickFrameRef(ref) {
  if (videoState.firstFrame) releaseVideoRef(videoState.firstFrame);
  videoState.firstFrame = ref;
  renderVideoFrame();
  applyVideoMode(); // 首帧就位 → 亮尾帧区
  updateVideoRefHint();
  updateVideoVrefHint();
  updateVideoCostHint();
  setStatus($("#video-status"), "已设为首帧");
}

function videoAddRef(ref) {
  if (videoState.references.length >= 4) {
    setStatus($("#video-status"), "参考图最多 4 张", true);
    return false;
  }
  videoState.references.push(ref);
  renderVideoRefs();
  updateVideoCostHint();
  setStatus($("#video-status"), "已加入参考图");
  return true;
}

/* 按模型上限拦新增（图、视频分别查分项与合计）；OR 合计 4 张（input_references） */
function videoCanAdd(kind, delta = 1) {
  const caps = videoInputCaps();
  const label = kind === "image" ? "参考图" : "参考视频";
  if (kind === "image" && videoState.references.length + delta > caps.maxImage) {
    setStatus($("#video-status"), `${label}最多 ${caps.maxImage} 张（当前模型上限）`, true);
    return false;
  }
  if (kind === "video" && videoState.videoReferences.length + delta > caps.maxVideo) {
    setStatus($("#video-status"), `${label}最多 ${caps.maxVideo} 段（当前模型上限）`, true);
    return false;
  }
  const total = videoState.references.length + videoState.videoReferences.length + delta;
  if (total > caps.maxTotal) {
    setStatus($("#video-status"), `参考图 + 参考视频合计最多 ${caps.maxTotal}（当前模型上限）`, true);
    return false;
  }
  return true;
}

$("#btn-video-frame-subject").addEventListener("click", () => {
  GenRefs.showSubjectBrowser((pick) => {
    videoPickFrameRef({
      type: "subject",
      subject_id: pick.subject_id,
      kind: pick.kind,
      name: pick.name || `${pick.subject_id} / ${pick.label || pick.kind}`,
      url: pick.url,
    });
  });
});

$("#btn-video-ref-subject").addEventListener("click", () => {
  if (!videoCanAdd("image")) return;
  GenRefs.showSubjectBrowser((pick) => {
    videoAddRef({
      type: "subject",
      subject_id: pick.subject_id,
      kind: pick.kind,
      name: pick.name || `${pick.subject_id} / ${pick.label || pick.kind}`,
      url: pick.url,
    });
  });
});

/* 动作库（线稿/深度 · 只锚姿势构图）：pick.source = 库 id，转成 ref.pose_source */
$("#btn-video-ref-pose").addEventListener("click", () => {
  if (!videoCanAdd("image")) return;
  GenRefs.showPoseBrowser((pick) => {
    videoAddRef({
      type: "pose",
      pose_id: pick.pose_id,
      angle: pick.angle,
      pose_source: pick.source || "",
      name: pick.name || pick.pose_id,
      url: pick.url,
    });
  });
});

/* 人脸库（AI 架构虚构人物 · 锚脸部身份） */
$("#btn-video-ref-face").addEventListener("click", () => {
  if (!videoCanAdd("image")) return;
  GenRefs.showFaceBrowser((pick) => {
    videoAddRef({
      type: "face",
      face_id: pick.face_id,
      face_source: pick.source || "",
      name: pick.name || pick.face_id,
      url: pick.url,
    });
  });
});

/* 服饰库（只锚服装与整套配件；requireImage = 纯文本条目禁选 —— 参考图必须真有图可发） */
$("#btn-video-ref-costume").addEventListener("click", () => {
  if (!videoCanAdd("image")) return;
  GenRefs.showCostumeBrowser((pick) => {
    videoAddRef({
      type: "costume",
      costume_id: pick.item_id,
      costume_source: pick.source_id || "",
      name: pick.name || pick.item_id,
      url: pick.url,
    });
  }, { requireImage: true });
});

/* 最近生成里有现成的产物图（Playground 出的图）：点开弹层选一张，直接当首帧/参考图，
   不用先下载到本地再上传。图片是 Playground run 的落盘产物，不会随一次出片被清理。 */
$("#btn-video-frame-recent").addEventListener("click", () => {
  Media.pickRecentImage((rec) => {
    if (videoState.firstFrame) releaseVideoRef(videoState.firstFrame);
    videoState.firstFrame = {
      type: "recent",
      run_id: rec.run_id,
      name: (rec.prompt || "").slice(0, 40) || rec.run_id,
      url: `/adhoc/${encodeURIComponent(rec.run_id)}/thumb`,
    };
    renderVideoFrame();
    updateVideoCostHint();
    setStatus($("#video-status"), "已设为首帧");
  }, { title: "视频 · 从最近生成选择首帧图" });
});

$("#btn-video-ref-recent").addEventListener("click", () => {
  if (!videoCanAdd("image")) return;
  Media.pickRecentImage((rec) => {
    videoState.references.push({
      type: "recent",
      run_id: rec.run_id,
      name: (rec.prompt || "").slice(0, 40) || rec.run_id,
      url: `/adhoc/${encodeURIComponent(rec.run_id)}/thumb`,
    });
    renderVideoRefs();
    updateVideoCostHint();
    setStatus($("#video-status"), "已加入参考图");
  }, { title: "视频 · 从最近生成选择参考图" });
});

/* ---------- 提交与轮询（异步作业） ---------- */

$("#btn-video-generate").addEventListener("click", async () => {
  const prompt = $("#video-prompt").value.trim();
  if (!prompt) return setStatus($("#video-status"), "提示词为空", true);
  const isI2v = videoState.mode === "i2v";
  if (isI2v) {
    // 图生视频：至少要有一路输入素材（首帧 / 参考图 / 参考视频）
    if (!videoState.firstFrame && !videoState.references.length && !videoState.videoReferences.length) {
      return setStatus($("#video-status"), "图生视频需要至少一路输入：首帧图、参考图或参考视频（上方任一区加入）", true);
    }
    // 即梦互斥：首帧与参考素材同给会被后端拦，前端先拦并给出明确指引
    if (videoState.backend === "dreamina" && videoState.firstFrame &&
        (videoState.references.length || videoState.videoReferences.length)) {
      return setStatus($("#video-status"), "即梦首帧与参考图/参考视频互斥：清掉首帧走全能参考，或清掉参考素材走首帧生视频", true);
    }
    // 即梦单独尾帧不收（按 首帧→尾帧 发送）
    if (videoState.backend === "dreamina" && videoState.lastFrame && !videoState.firstFrame) {
      return setStatus($("#video-status"), "即梦尾帧必须与首帧成对（先设首帧）", true);
    }
  }
  // 种子留空 = 随机 0–100000（与生成页/Playground 同一约定，随机源在调用方这一侧）
  const seedRaw = $("#video-seed").value.trim();
  const seed = seedRaw === "" ? Math.floor(Math.random() * 100001) : Number(seedRaw);
  const body = {
    prompt,
    backend: videoState.backend,
    aspect_ratio: $("#video-aspect").value || "",
    duration: $("#video-duration").value ? Number($("#video-duration").value) : null,
    resolution: $("#video-resolution").value || null,
    seed,
    // spec 映射与 Playground（adhoc.js）同一套：pose/face/costume 带 role 与库 id，
    // 视频通路后端只解析文件路径（role 被忽略）。t2v 不发任何素材。
    references: isI2v
      ? videoState.references.map((ref) =>
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
            : { subject_id: ref.subject_id, kind: ref.kind })
      : [],
    video_references: videoState.mode === "i2v"
      ? videoState.videoReferences.map((ref) => ({ staged_id: ref.id }))
      : [],
    // 首帧区 UI 只出 staged/recent/subject 三种来源（四库只进参考图区）；
    // 后端 /videos 的 spec 解析对首帧同样支持六种来源，改 UI 时这里同步
    first_frame: videoState.mode === "i2v" && videoState.firstFrame
      ? (videoState.firstFrame.type === "staged"
          ? { staged_id: videoState.firstFrame.id }
          : videoState.firstFrame.type === "recent"
          ? { run_id: videoState.firstFrame.run_id }
          : { subject_id: videoState.firstFrame.subject_id, kind: videoState.firstFrame.kind })
      : null,
    // 尾帧：模型支持 + 首帧已设才可见，同来源子集
    last_frame: videoState.mode === "i2v" && videoState.lastFrame && videoState.firstFrame
      ? (videoState.lastFrame.type === "staged"
          ? { staged_id: videoState.lastFrame.id }
          : videoState.lastFrame.type === "recent"
          ? { run_id: videoState.lastFrame.run_id }
          : { subject_id: videoState.lastFrame.subject_id, kind: videoState.lastFrame.kind })
      : null,
  };
  const model = videoState.backend === "dreamina"
    ? $("#video-dreamina-model").value
    : $("#video-model").value;
  if (model) body.model = model;

  const btn = $("#btn-video-generate");
  btn.disabled = true;
  const costNote = videoState.backend === "dreamina"
    ? "即梦按积分/会员权益计费"
    : "OpenRouter 按秒计费，提交成功即开始计费";
  setStatus($("#video-status"), `提交作业中…（${costNote}）`);
  try {
    const out = await api("/videos/generate", { method: "POST", body: JSON.stringify(body) });
    renderVideoResult(out); // pending 阶段先给出 run_id/manifest 信息，轮询起来后更新
    startVideoPolling(out.run_id, videoState.backend);
    loadVideoRecent(0); // 新作业必然在最前，回到第一页看它
  } catch (err) {
    setStatus($("#video-status"), `提交失败：${err.message}`, true);
    btn.disabled = false;
  }
});

function startVideoPolling(runId, backend) {
  stopVideoPolling();
  videoState.activeRun = runId;
  videoState.pollBackend = backend || "openrouter";
  setStatus($("#video-status"), "生成中…（一般 1–3 分钟，可离开页面，历史列表会留着）");
  const tick = async () => {
    let s;
    try {
      s = await api(`/videos/${encodeURIComponent(runId)}/status`);
    } catch (err) {
      // 网络抖动：状态行提示但不停止轮询（后端 poll_error 同样不停）
      setStatus($("#video-status"), `查询失败重试中：${err.message}`, true);
      return;
    }
    if (s.poll_error) {
      setStatus($("#video-status"), `查询重试中：${s.poll_error}`, true);
      return;
    }
    if (s.status === "ready") {
      stopVideoPolling();
      $("#btn-video-generate").disabled = false; // 作业终态：按钮复原，可直接再生成一个
      renderVideoResult(s);
      dropVideoRefsSent(s.run_id);
      loadVideoRecent();
      setStatus($("#video-status"),
        `完成：${s.duration != null ? s.duration + "s 片子" : "片子已就绪"}，用时 ${(s.duration_ms / 1000).toFixed(0)}s` +
        (s.cost != null ? `，$${Number(s.cost).toFixed(3)}` : ""));
      return;
    }
    if (s.status === "failed") {
      stopVideoPolling();
      $("#btn-video-generate").disabled = false;
      renderVideoResult(s);
      loadVideoRecent();
      setStatus($("#video-status"), `失败：${s.error || "作业失败"}`, true);
      return;
    }
    const waited = Math.round((Date.now() - (videoState.pollSince || Date.now())) / 1000);
    const stateLabel = videoState.pollBackend === "dreamina"
      ? `即梦状态 ${s.job_status || "querying"}`
      : `OpenRouter 状态 ${s.status}`;
    setStatus($("#video-status"), `生成中…（已等 ${waited}s，${stateLabel}）`);
  };
  videoState.pollSince = Date.now();
  tick();
  videoState.pollTimer = setInterval(tick, 8000);
}

function stopVideoPolling() {
  if (videoState.pollTimer) clearInterval(videoState.pollTimer);
  videoState.pollTimer = null;
  videoState.activeRun = null;
  videoState.pollSince = null;
}

/* 提交时带的暂存图后端在 ready 时已清；列表里同步摘掉（objectURL 记得放掉） */
function dropVideoRefsSent() {
  const usedTypes = new Set(["staged", "subject"]);
  const freed = videoState.references.filter((r) => usedTypes.has(r.type));
  videoState.references = [];
  videoState.videoReferences = [];
  videoState.firstFrame = null;
  videoState.lastFrame = null;
  freed.forEach(releaseVideoRef);
  renderVideoFrame();
  renderVideoLastFrame();
  renderVideoVrefs();
  applyVideoMode();
}

function renderVideoResult(s) {
  $("#video-result-card").hidden = false;
  $("#video-result-summary").textContent = [
    s.model || "",
    s.resolution || "",
    (s.duration != null ? `${s.duration}s` : ""),
    s.aspect_ratio || "",
    s.cost != null ? `$${Number(s.cost).toFixed(3)}` : "",
  ].filter(Boolean).join(" · ");
  const player = $("#video-result-player");
  if (s.status === "ready" && s.video_url) {
    player.src = s.video_url;
    player.poster = s.poster_url || "";
    const dl = $("#video-dl-video");
    dl.href = s.video_url;
    // URL 末段是 "video"，不显式给文件名的话存下来会叫 "video"（无扩展名）
    dl.download = `${s.run_id}.mp4`;
    dl.hidden = false;
    const manifest = $("#video-dl-manifest");
    manifest.href = `/videos/${encodeURIComponent(s.run_id)}/manifest`;
    manifest.hidden = false;
    $("#video-result-log").textContent =
      `run_id: ${s.run_id}\n作业: ${s.job_id || ""}\n用时: ${(s.duration_ms / 1000).toFixed(1)}s` +
      (s.cost != null ? `\n费用: $${Number(s.cost).toFixed(4)}` : "");
    player.scrollIntoView({ block: "nearest" });
  } else if (s.status === "failed") {
    player.removeAttribute("src");
    $("#video-result-log").textContent = `run_id: ${s.run_id}\n失败原因：${s.error || ""}`;
  } else {
    player.removeAttribute("src");
    $("#video-result-log").textContent =
      `run_id: ${s.run_id}\n状态: ${s.status || "pending"}（作业 ${s.job_id || ""}）\n查看: manifest.json`;
  }
}

/* ---------- 最近生成 ---------- */

/* ---------- 最近生成（分页） ---------- */
const VIDEO_RECENT_PAGE_SIZE = 24;
const videoRecentState = { page: 0, seq: 0 };

async function loadVideoRecent(page = videoRecentState.page) {
  const seq = ++videoRecentState.seq; // 快速连点/并发的旧响应不回写新页
  videoRecentState.page = Math.max(0, page);
  const box = $("#video-recent");
  try {
    const offset = videoRecentState.page * VIDEO_RECENT_PAGE_SIZE;
    const { items, total } = await apiPaged(
      `/videos?limit=${VIDEO_RECENT_PAGE_SIZE}&offset=${offset}`
    );
    if (seq !== videoRecentState.seq) return;
    box.innerHTML = "";
    if (!items.length) {
      if (videoRecentState.page > 0) {
        // 过了末页（服务端记录被删过）：留在原页位，上一页可退回
        box.innerHTML = `<span class="hint">这一页没有记录</span>`;
        refreshPager(box, "video-recent-pager", {
          page: videoRecentState.page,
          pageSize: VIDEO_RECENT_PAGE_SIZE,
          total,
          goto: loadVideoRecent,
        });
      } else {
        // 全库为空：老文案 + 不出分页条
        box.innerHTML = `<span class="hint">还没有视频记录</span>`;
        document.getElementById("video-recent-pager")?.remove();
      }
      return;
    }
    for (const rec of items) {
      box.appendChild(buildVideoRecentCard(rec));
    }
    refreshPager(box, "video-recent-pager", {
      page: videoRecentState.page,
      pageSize: VIDEO_RECENT_PAGE_SIZE,
      total,
      goto: loadVideoRecent,
    });
    // 页面刷新后接着盯生成中的作业（跨刷新不断线）；只看当前页，翻走就不续盯（回页 0 会再接上）
    const pending = items.find((r) => r.status === "pending" || r.status === "in_progress");
    if (pending && videoState.activeRun !== pending.run_id) {
      startVideoPolling(pending.run_id, pending.backend);
    }
  } catch (err) {
    box.innerHTML = `<span class="hint">加载失败：${escapeHtml(err.message)}</span>`;
  }
}

const VIDEO_STATUS_LABELS = {
  ready: "完成",
  failed: "失败",
  pending: "已入队",
  in_progress: "生成中",
};

function buildVideoRecentCard(rec) {
  const card = document.createElement("div");
  card.className = "image-card video-card";
  card.dataset.runId = rec.run_id;
  if (rec.has_video) {
    card.innerHTML = `<video controls preload="metadata" poster="/videos/${encodeURIComponent(rec.run_id)}/poster" src="/videos/${encodeURIComponent(rec.run_id)}/video"></video>`;
  } else {
    const label = VIDEO_STATUS_LABELS[rec.status] || rec.status;
    card.innerHTML = `<div class="image-placeholder">${escapeHtml(label)}</div>`;
  }
  const meta = document.createElement("div");
  meta.className = "image-meta";
  meta.innerHTML = `<span>${escapeHtml((rec.backend === "dreamina" ? "即梦 " : "") + String(rec.model || ""))}</span>
    <span>${escapeHtml((rec.created_at || "").slice(5, 16))}</span>`;
  card.appendChild(meta);
  const p = document.createElement("p");
  p.textContent = rec.prompt || "";
  p.title = rec.prompt || "";
  card.appendChild(p);
  const actions = document.createElement("div");
  actions.className = "image-actions";
  if (rec.prompt && rec.prompt.trim()) {
    const promptBtn = document.createElement("button");
    promptBtn.textContent = "提示词";
    promptBtn.addEventListener("click", () =>
      Media.showTextModal("视频 · 提示词", rec.prompt || "", { copyable: true }));
    actions.appendChild(promptBtn);
  }
  if (rec.has_video) {
    const openBtn = document.createElement("button");
    openBtn.className = "btn-icon";
    openBtn.title = "弹层播放";
    openBtn.setAttribute("aria-label", "弹层播放");
    openBtn.innerHTML = icon("play");
    openBtn.addEventListener("click", () => {
      Media.showPanelModal("视频 · " + rec.run_id, (body) => {
        const vid = document.createElement("video");
        vid.controls = true;
        vid.autoplay = true;
        vid.preload = "metadata";
        vid.poster = `/videos/${encodeURIComponent(rec.run_id)}/poster`;
        vid.src = `/videos/${encodeURIComponent(rec.run_id)}/video`;
        vid.style.maxWidth = "100%";
        body.appendChild(vid);
      });
    });
    actions.appendChild(openBtn);
    // 下载 MP4：与「生成结果 → 合成视频」卡同一个做法（media.js 的下载链接）。
    // URL 末段是 "video"，所以显式给文件名，否则存下来没有扩展名。
    const dl = document.createElement("a");
    dl.className = "btn btn-icon";
    dl.href = `/videos/${encodeURIComponent(rec.run_id)}/video`;
    dl.download = `${rec.run_id}.mp4`;
    dl.title = "下载 MP4";
    dl.setAttribute("aria-label", "下载 MP4");
    dl.innerHTML = icon("download");
    actions.appendChild(dl);
  }
  const reuseBtn = document.createElement("button");
  reuseBtn.textContent = "复用";
  reuseBtn.addEventListener("click", () => {
    $("#video-prompt").value = rec.prompt || "";
    if (rec.aspect_ratio) {
      const sel = $("#video-aspect");
      if ([...sel.options].some((o) => o.value === rec.aspect_ratio)) sel.value = rec.aspect_ratio;
    }
    if (rec.seed != null) $("#video-seed").value = rec.seed;
  });
  actions.appendChild(reuseBtn);
  if (rec.prompt && rec.prompt.trim()) {
    const copyBtn = document.createElement("button");
    copyBtn.textContent = "AI 文案";
    copyBtn.addEventListener("click", async () => {
      if (rec.copy) return showVideoCopyModal(rec);
      copyBtn.disabled = true;
      copyBtn.textContent = "生成中…";
      try {
        rec.copy = await api(`/videos/${encodeURIComponent(rec.run_id)}/copy`, { method: "POST" });
      } catch (err) {
        setStatus($("#video-status"), `AI 文案生成失败（${rec.run_id}）：${err.message}`, true);
        return;
      } finally {
        copyBtn.disabled = false;
        copyBtn.textContent = "AI 文案";
      }
      showVideoCopyModal(rec);
    });
    actions.appendChild(copyBtn);
  }
  card.appendChild(actions);
  const note = document.createElement("div");
  note.className = "image-meta";
  note.innerHTML = `<span>${escapeHtml([rec.resolution, rec.duration != null ? rec.duration + "s" : "", VIDEO_STATUS_LABELS[rec.status] || rec.status].filter(Boolean).join(" · "))}</span>`;
  card.appendChild(note);
  if (rec.error) {
    const errLine = document.createElement("p");
    errLine.className = "hint";
    errLine.textContent = rec.error;
    card.appendChild(errLine);
  }
  return card;
}

/* ---------- 视频文案弹层（LLM 生成，存 manifest["copy"]） ---------- */

/* 弹层内一处文案（标签行 + 复制钮 + 可滚动 pre）；返回 { wrap, sync }，sync 换新文案用。 */
function buildCopySection(label, initialText) {
  const wrap = document.createElement("div");
  wrap.className = "copy-section";
  const head = document.createElement("div");
  head.className = "copy-section-head";
  head.innerHTML = `<strong>${escapeHtml(label)}</strong>`;
  const copyBtn = document.createElement("button");
  copyBtn.className = "btn-icon";
  copyBtn.title = "复制";
  copyBtn.setAttribute("aria-label", "复制");
  copyBtn.innerHTML = icon("copy");
  let text = initialText || "";
  copyBtn.addEventListener("click", async () => {
    const ok = await copyText(text);
    copyBtn.innerHTML = icon(ok ? "check" : "refresh"); // 图标反馈，1.5s 后还原
    setTimeout(() => { copyBtn.innerHTML = icon("copy"); }, 1500);
  });
  head.appendChild(copyBtn);
  const pre = document.createElement("pre");
  pre.className = "prompt-view";
  wrap.append(head, pre);
  return {
    wrap,
    set(next) {
      text = next || "";
      pre.textContent = text;
    },
  };
}

/* AI 文案弹层：有存稿直接展示，「重新生成」调 POST 覆盖（成功后同步 rec）。 */
function showVideoCopyModal(rec) {
  Media.showPanelModal("视频 · AI 文案", (body) => {
    const title = buildCopySection("标题", "");
    const intro = buildCopySection("简介", "");
    const tags = buildCopySection("话题 #", "");
    const row = document.createElement("div");
    row.className = "actions";
    const regen = document.createElement("button");
    regen.className = "btn-sm";
    regen.textContent = "重新生成";
    const stamp = document.createElement("span");
    stamp.className = "hint";
    const tagsText = (copy) => (copy.tags || []).map((t) => "#" + t).join(" ");
    const render = (copy) => {
      title.set(copy.title);
      intro.set(copy.intro);
      tags.set(tagsText(copy));
      stamp.textContent = copy.generated_at ? "生成于 " + copy.generated_at : "";
      rec.copy = copy;
    };
    const copyAll = document.createElement("button");
    copyAll.className = "btn-icon";
    copyAll.title = "复制全部";
    copyAll.setAttribute("aria-label", "复制全部");
    copyAll.innerHTML = icon("copy");
    copyAll.addEventListener("click", async () => {
      const copy = rec.copy || {};
      const text = [copy.title, copy.intro, tagsText(copy)].filter(Boolean).join("\n");
      const ok = await copyText(text);
      copyAll.innerHTML = icon(ok ? "check" : "refresh");
      setTimeout(() => { copyAll.innerHTML = icon("copy"); }, 1500);
    });
    const generate = async () => {
      regen.disabled = true;
      stamp.className = "hint";
      stamp.textContent = "生成中…";
      try {
        render(await api(`/videos/${encodeURIComponent(rec.run_id)}/copy`, { method: "POST" }));
      } catch (err) {
        stamp.className = "hint status error";
        stamp.textContent = "生成失败：" + err.message;
      }
      regen.disabled = false;
    };
    regen.addEventListener("click", generate);
    row.append(regen, copyAll, stamp);
    body.append(title.wrap, intro.wrap, tags.wrap, row);
    if (rec.copy) render(rec.copy);
    else generate();
  });
}

$("#btn-video-recent-refresh").addEventListener("click", () => loadVideoRecent());

/* ---------- 供应商切换（档位各记各的，切回不重置） ---------- */

async function applyDreaminaPanel(restore) {
  await ensureDreaminaDefaults();
  applyDreaminaVideoModel();
  applyDreaminaVideoEnums(restore);
  applyVideoMode();
  renderVideoModelInfo();
  updateVideoCostHint();
}

async function applyBackendPanel() {
  const dreamina = videoState.backend === "dreamina";
  $("#video-openrouter-model-field").hidden = dreamina;
  $("#video-dreamina-model-field").hidden = !dreamina;
  if (dreamina) {
    await applyDreaminaPanel(true); // 切回供应商：恢复各自档位
  } else {
    applyVideoEnums(true);
    applyVideoMode();
    renderVideoModelInfo();
    updateVideoCostHint();
  }
}

/* ---------- 初始化 ---------- */

function initVideo() {
  renderVideoFrame();
  renderVideoLastFrame();
  renderVideoRefs();
  renderVideoVrefs();
  applyVideoMode();
  $("#video-model").addEventListener("change", () => applyVideoEnums(false));
  $("#video-dreamina-model").addEventListener("change", () => {
    videoState.saved.dreamina.model = $("#video-dreamina-model").value;
    applyDreaminaVideoEnums(false);
  });
  $("#video-duration").addEventListener("change", updateVideoCostHint);
  $("#video-resolution").addEventListener("change", updateVideoCostHint);
  $$("#video-backend .seg").forEach((btn) =>
    btn.addEventListener("click", () => {
      if (videoState.backend === btn.dataset.backend) return;
      const prev = videoState.backend;
      const sel = prev === "dreamina" ? "#video-dreamina-model" : "#video-model";
      videoState.saved[prev].model = $(sel).value;
      videoState.saved[prev].aspect = $("#video-aspect").value;
      videoState.saved[prev].resolution = $("#video-resolution").value;
      videoState.saved[prev].duration = $("#video-duration").value;
      videoState.backend = btn.dataset.backend;
      videoSegToggle("#video-backend", "backend", videoState.backend);
      applyBackendPanel();
    })
  );
  $$("#video-mode .seg").forEach((btn) =>
    btn.addEventListener("click", () => {
      if (videoState.mode === btn.dataset.mode) return;
      videoState.mode = btn.dataset.mode;
      $$("#video-mode .seg").forEach((b) =>
        b.classList.toggle("active", b.dataset.mode === videoState.mode));
      if (videoState.backend === "dreamina") applyDreaminaPanel(false);
      else { applyVideoMode(); updateVideoCostHint(); }
    })
  );
  loadVideoCatalog().catch(() => {});
  loadVideoRecent().catch(() => {});
}

initVideo();
