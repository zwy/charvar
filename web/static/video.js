/* 视频（Playground）页（web 面板）：
   一级页签 = 模式（文生视频 t2v / 图生视频 i2v = 首帧图生视频），显式选择。
   与出图（adhoc.js）同构：同一套暂存参考图四件套、主体档案选图、成本预估。
   关键差异：视频是异步作业 —— 提交即返回 run_id，前端每 8s 轮询
   /videos/{run_id}/status 到 ready/failed；页面载入时找未终态的 run 恢复轮询
   （跨刷新不断线）。
   字段显隐：i2v 只显示首帧区，t2v 只显示参考图区。 */

const VIDEO_MODES = {
  t2v: { refs: true, frame: false },
  i2v: { refs: false, frame: true },
};

const videoState = {
  mode: "t2v",
  catalog: null,
  references: [],
  firstFrame: null,
  activeRun: null,
  pollTimer: null,
  subjectSlots: {},
  capsToken: 0,
};

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
      `<option value="${escapeHtml(m.id)}">${escapeHtml(m.name || m.id)}</option>`))
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
function applyVideoEnums() {
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

  $("#video-seed-field").hidden = Boolean(entry && entry.seed === false);
  renderVideoModelInfo();
  updateVideoCostHint();
}

function renderVideoModelInfo() {
  const el = $("#video-model-field");
  if (!el) return;
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

/* 成本预估（展示用，不参与提交校验）：heygen 系列 pricing_skus 的键是
   duration_seconds_<分辨率小写>；带图（首帧/参考图）换 reference_duration_seconds_*
   + 每张 reference_input_images。算不出就不显示（别猜价）。 */
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
  const seconds = Number($("#video-duration").value || 0);
  // 计费口径：图生视频（有首帧）与文生视频+参考图都按 reference 档
  const imageCount = videoState.mode === "i2v"
    ? (videoState.firstFrame ? 1 : 0)
    : videoState.references.length;
  // 参考图档（heygen 系）算不出就回退普档（grok 系：参考图不影响每秒价）
  let rate = imageCount > 0 ? videoPrice("reference_duration_seconds") : null;
  if (rate == null) rate = videoPrice("duration_seconds");
  const perImage = imageCount > 0 ? (videoPrice("reference_input_images") || 0) : 0;
  if (!rate || !seconds) {
    el.textContent = "";
    return;
  }
  const estimate = rate * seconds + perImage * imageCount;
  const seg = (videoState.mode === "i2v" ? "图生视频" : "文生视频") +
    (imageCount ? ` · ${imageCount} 张图` : "");
  el.textContent = `预计 ≈ $${estimate.toFixed(2)}（${seg} · ${seconds}s${$("#video-resolution").value ? ` · ${$("#video-resolution").value}` : ""}，以账单为准）`;
}
/* ---------- 模式与面板显隐 ---------- */

function applyVideoMode() {
  const mode = VIDEO_MODES[videoState.mode] || VIDEO_MODES.t2v;
  $("#video-frame-block").hidden = !mode.frame;
  $("#video-refs-block").hidden = !mode.refs;
  const refs = videoState.references;
  const frameRef = videoState.firstFrame;
  const hint = $("#video-ref-hint");
  if (hint) {
    const parts = [];
    parts.push(refs.length
      ? `已选 ${refs.length} 张：${refs.map((r, i) => `<image${i + 1}>`).join("、")}`
      : "未选参考图");
    if (!mode.refs && frameRef) {
      parts.push(`${refs.length} 张参考图随「文生视频」发送；想带参考图请切回该页签`);
    }
    if (mode.refs && !refs.length && !frameRef) {
      parts.push("上传图，或从主体档案选就绪的图");
    }
    hint.textContent = parts.join(" · ");
  }
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
    box.innerHTML = `<span class="hint">未选首帧：上传一张，或从主体档案选就绪的「全身正面」</span>`;
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
    del.className = "btn-sm";
    del.textContent = "移除";
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
    updateVideoCostHint();
  } catch (err) {
    setStatus($("#video-status"), `首帧上传失败：${err.message}`, true);
  }
});

$("#video-ref-file").addEventListener("change", async (ev) => {
  const files = Array.from(ev.target.files || []);
  ev.target.value = "";
  for (const file of files) {
    if (videoState.references.length >= 4) {
      setStatus($("#video-status"), "参考图最多 4 张", true);
      break;
    }
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
  renderVideoFrame();
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

/* ---------- 主体档案选图（首帧 / 参考图各一对下拉） ---------- */

async function loadVideoKindOptions(subjectId, kindSel) {
  if (!subjectId) {
    kindSel.innerHTML = `<option value="">（先选主体）</option>`;
    return;
  }
  try {
    const data = await api(`/subjects/${encodeURIComponent(subjectId)}/images`);
    videoState.subjectSlots[subjectId] = data.slots || [];
    const ready = (data.slots || []).filter((s) => s.is_ready);
    kindSel.innerHTML = ready.length
      ? ready.map((s) => `<option value="${escapeHtml(s.kind)}">${escapeHtml(s.label)}</option>`).join("")
      : `<option value="">（该主体还没有就绪的图片）</option>`;
  } catch {
    kindSel.innerHTML = `<option value="">（加载失败）</option>`;
  }
}

async function loadVideoSubjects() {
  let subjects = [];
  try {
    subjects = await api("/subjects");
  } catch { /* 列表空态由各下拉兜底 */ }
  const options = subjects.length
    ? subjects.map((s) =>
        `<option value="${escapeHtml(s.id)}">${escapeHtml(s.id)} · ${escapeHtml(s.name)}</option>`).join("")
    : `<option value="">（还没有主体档案）</option>`;
  for (const id of ["video-frame-subject", "video-ref-subject"]) {
    const sel = $("#" + id);
    sel.innerHTML = options;
    sel.disabled = !subjects.length;
  }
  await loadVideoKindOptions($("#video-frame-subject").value, $("#video-frame-kind"));
  await loadVideoKindOptions($("#video-ref-subject").value, $("#video-ref-kind"));
}

$("#video-frame-subject").addEventListener("change", () =>
  loadVideoKindOptions($("#video-frame-subject").value, $("#video-frame-kind")));
$("#video-ref-subject").addEventListener("change", () =>
  loadVideoKindOptions($("#video-ref-subject").value, $("#video-ref-kind")));

function videoSlotFor(subjectId, kind) {
  return (videoState.subjectSlots[subjectId] || []).find((s) => s.kind === kind);
}

$("#btn-video-frame-add").addEventListener("click", () => {
  const subjectId = $("#video-frame-subject").value;
  const kind = $("#video-frame-kind").value;
  if (!subjectId || !kind) {
    return setStatus($("#video-status"), "先选主体和图片类型（只有就绪的图片能选）", true);
  }
  const slot = videoSlotFor(subjectId, kind);
  if (videoState.firstFrame) releaseVideoRef(videoState.firstFrame);
  videoState.firstFrame = {
    type: "subject",
    subject_id: subjectId,
    kind,
    name: `${subjectId} / ${(slot && slot.label) || kind}`,
    url: slot ? slot.thumb_url || slot.original_url : "",
  };
  renderVideoFrame();
  updateVideoCostHint();
  setStatus($("#video-status"), "已加入首帧");
});

$("#btn-video-ref-add").addEventListener("click", () => {
  if (videoState.references.length >= 4) {
    return setStatus($("#video-status"), "参考图最多 4 张", true);
  }
  const subjectId = $("#video-ref-subject").value;
  const kind = $("#video-ref-kind").value;
  if (!subjectId || !kind) {
    return setStatus($("#video-status"), "先选主体和图片类型（只有就绪的图片能选）", true);
  }
  const slot = videoSlotFor(subjectId, kind);
  videoState.references.push({
    type: "subject",
    subject_id: subjectId,
    kind,
    name: `${subjectId} / ${(slot && slot.label) || kind}`,
    url: slot ? slot.thumb_url || slot.original_url : "",
  });
  renderVideoRefs();
  updateVideoCostHint();
  setStatus($("#video-status"), "已加入参考图");
});

/* ---------- 提交与轮询（异步作业） ---------- */

$("#btn-video-generate").addEventListener("click", async () => {
  const prompt = $("#video-prompt").value.trim();
  if (!prompt) return setStatus($("#video-status"), "提示词为空", true);
  const mode = VIDEO_MODES[videoState.mode];
  if (videoState.mode === "i2v" && !videoState.firstFrame) {
    return setStatus($("#video-status"), "图生视频需要 1 张首帧图：上传，或从主体档案选就绪的图", true);
  }
  // 种子留空 = 随机 0–100000（与生成页/Playground 同一约定，随机源在调用方这一侧）
  const seedRaw = $("#video-seed").value.trim();
  const seed = seedRaw === "" ? Math.floor(Math.random() * 100001) : Number(seedRaw);
  const body = {
    prompt,
    aspect_ratio: $("#video-aspect").value || "",
    duration: $("#video-duration").value ? Number($("#video-duration").value) : null,
    resolution: $("#video-resolution").value || null,
    seed,
    // 图生视频发首帧不发参考图；文生视频只发参考图
    references: videoState.mode === "t2v"
      ? videoState.references.map((ref) =>
          ref.type === "staged"
            ? { staged_id: ref.id }
            : { subject_id: ref.subject_id, kind: ref.kind })
      : [],
    first_frame: videoState.mode === "i2v" && videoState.firstFrame
      ? (videoState.firstFrame.type === "staged"
          ? { staged_id: videoState.firstFrame.id }
          : { subject_id: videoState.firstFrame.subject_id, kind: videoState.firstFrame.kind })
      : null,
  };
  const model = $("#video-model").value;
  if (model) body.model = model;

  const btn = $("#btn-video-generate");
  btn.disabled = true;
  setStatus($("#video-status"), "提交作业中…（OpenRouter 按秒计费，提交成功即开始计费）");
  try {
    const out = await api("/videos/generate", { method: "POST", body: JSON.stringify(body) });
    renderVideoResult(out); // pending 阶段先给出 run_id/manifest 信息，轮询起来后更新
    startVideoPolling(out.run_id);
    loadVideoRecent();
  } catch (err) {
    setStatus($("#video-status"), `提交失败：${err.message}`, true);
    btn.disabled = false;
  }
});

function startVideoPolling(runId) {
  stopVideoPolling();
  videoState.activeRun = runId;
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
      renderVideoResult(s);
      loadVideoRecent();
      setStatus($("#video-status"), `失败：${s.error || "作业失败"}`, true);
      return;
    }
    const waited = Math.round((Date.now() - (videoState.pollSince || Date.now())) / 1000);
    setStatus($("#video-status"), `生成中…（已等 ${waited}s，OpenRouter 状态 ${s.status}）`);
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
  videoState.firstFrame = null;
  freed.forEach(releaseVideoRef);
  renderVideoFrame();
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

async function loadVideoRecent() {
  const box = $("#video-recent");
  try {
    const records = await api("/videos?limit=24");
    box.innerHTML = "";
    if (!records.length) {
      box.innerHTML = `<span class="hint">还没有视频记录</span>`;
      return;
    }
    for (const rec of records) {
      box.appendChild(buildVideoRecentCard(rec));
    }
    // 页面刷新后接着盯生成中的作业（跨刷新不断线）
    const pending = records.find((r) => r.status === "pending" || r.status === "in_progress");
    if (pending && videoState.activeRun !== pending.run_id) {
      startVideoPolling(pending.run_id);
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
  meta.innerHTML = `<span>${escapeHtml(String(rec.model || ""))}</span>
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
    promptBtn.textContent = "查看提示词";
    promptBtn.addEventListener("click", () =>
      Media.showTextModal("视频 · 提示词", rec.prompt || "", { copyable: true }));
    actions.appendChild(promptBtn);
  }
  if (rec.has_video) {
    const openBtn = document.createElement("button");
    openBtn.textContent = "在弹层播放";
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
  }
  const reuseBtn = document.createElement("button");
  reuseBtn.textContent = "复用提示词";
  reuseBtn.addEventListener("click", () => {
    $("#video-prompt").value = rec.prompt || "";
    if (rec.aspect_ratio) {
      const sel = $("#video-aspect");
      if ([...sel.options].some((o) => o.value === rec.aspect_ratio)) sel.value = rec.aspect_ratio;
    }
    if (rec.seed != null) $("#video-seed").value = rec.seed;
  });
  actions.appendChild(reuseBtn);
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

$("#btn-video-recent-refresh").addEventListener("click", loadVideoRecent);

/* ---------- 初始化 ---------- */

function initVideo() {
  renderVideoFrame();
  renderVideoRefs();
  applyVideoMode();
  $("#video-model").addEventListener("change", applyVideoEnums);
  $("#video-duration").addEventListener("change", updateVideoCostHint);
  $("#video-resolution").addEventListener("change", updateVideoCostHint);
  $$("#video-mode .seg").forEach((btn) =>
    btn.addEventListener("click", () => {
      if (videoState.mode === btn.dataset.mode) return;
      videoState.mode = btn.dataset.mode;
      $$("#video-mode .seg").forEach((b) =>
        b.classList.toggle("active", b.dataset.mode === videoState.mode));
      applyVideoMode();
      updateVideoCostHint();
    })
  );
  loadVideoCatalog().catch(() => {});
  loadVideoSubjects().catch(() => {});
  loadVideoRecent().catch(() => {});
}

initVideo();
