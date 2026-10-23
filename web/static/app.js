/* charvar 管理页逻辑：生成 / 主体档案 / 历史 / ComfyUI 入队 */
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const state = {
  subjects: [],
  presets: [],
  presetDetails: {},
  currentSubject: null,
  lastRun: null, // GenerateResult（= 当前选中的轮次，结果卡 / 推送面板都作用于它）
  rounds: [], // 轮次条：{run_id, completed, total, errors, queued}，按时间排
  genMode: "classic",
  subjectImages: [], // 主体档案图片槽位（当前主体）
  openrouterSettings: null, // /settings 里的 openrouter 段
  openrouterCaps: null, // 当前生图模型的能力（api-response.json 推导）
  openrouterMode: "t2i", // 无参考图 = 文生图；勾选参考图 = 图片编辑（对齐 ComfyUI）
};

async function api(path, options = {}) {
  const resp = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      const data = await resp.json();
      detail = data.detail || JSON.stringify(data);
    } catch {}
    throw new Error(`${resp.status}: ${detail}`);
  }
  return resp.json();
}

async function apiUpload(path, file) {
  const form = new FormData();
  form.append("file", file);
  const resp = await fetch(path, { method: "POST", body: form });
  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      const data = await resp.json();
      detail = data.detail || JSON.stringify(data);
    } catch {}
    throw new Error(`${resp.status}: ${detail}`);
  }
  return resp.json();
}

function setStatus(el, text, isError = false) {
  el.textContent = text;
  el.classList.toggle("error", isError);
}

/* ---------- tabs ---------- */
$$(".tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    $$(".tab").forEach((b) => b.classList.toggle("active", b === btn));
    $$(".tab-page").forEach((p) => p.classList.toggle("active", p.id === `tab-${btn.dataset.tab}`));
    if (btn.dataset.tab === "runs") loadRuns();
    if (btn.dataset.tab === "settings") loadSettings();
    if (btn.dataset.tab === "gen") renderRefPickers();
  });
});

/* ---------- 生成模式（经典 / LLM 优化） ---------- */
function setGenMode(mode) {
  state.genMode = mode;
  $$("#gen-mode-classic, #gen-mode-llm").forEach((btn) =>
    btn.classList.toggle("active", btn.dataset.mode === mode));
  $("#gen-llm-panel").hidden = mode !== "llm";
  renderRefPickers(); // 参考图网格两模式共用（经典模式用于推送 ComfyUI 时上传参考图）
  updateComfyWorkflowHint(); // 面板开着时同步提示（参考图数随模式变化）
}

$("#gen-mode-classic").addEventListener("click", () => setGenMode("classic"));
$("#gen-mode-llm").addEventListener("click", () => setGenMode("llm"));

function selectedRefKinds() {
  return $$("#gen-refs .ref-pick.active").map((el) => el.dataset.kind);
}

async function renderRefPickers() {
  const panel = $("#gen-refs-panel");
  if (panel) panel.hidden = false; // 参考图面板经典/LLM 两模式共用
  const sid = $("#gen-subject").value;
  const box = $("#gen-refs");
  if (!sid) {
    box.innerHTML = `<span class="hint">先选择主体档案</span>`;
    return;
  }
  try {
    const data = await api(`/subjects/${encodeURIComponent(sid)}/images`);
    state.refImages = data.slots;
  } catch {
    box.innerHTML = `<span class="hint">参考图加载失败</span>`;
    return;
  }
  box.innerHTML = "";
  for (const slot of state.refImages) {
    const el = document.createElement("button");
    el.type = "button";
    el.className = "ref-pick";
    el.dataset.kind = slot.kind;
    el.disabled = !slot.is_ready;
    const thumb = slot.thumb_url || slot.original_url;
    el.innerHTML = `${thumb ? `<img src="${thumb}" alt="">` : `<span class="ref-thumb-empty"></span>`}
      <span class="ref-name">${slot.label}</span>
      <span class="badge badge-${slot.status}">${slot.is_ready ? "就绪" : slot.status === "empty" ? "空" : slot.status}</span>`;
    el.addEventListener("click", () => {
      if (el.classList.contains("active")) {
        el.classList.remove("active");
      } else {
        if (selectedRefKinds().length >= 4) return; // 最多 4 张参考图
        el.classList.add("active");
      }
      updateComfyWorkflowHint();
      updateRefsHint();
      updateOpenRouterHints();
    });
    const img = el.querySelector("img");
    if (img) {
      img.title = "预览大图（卡片其余区域为选择/取消）";
      img.addEventListener("click", (ev) => {
        ev.stopPropagation();
        Media.showImageModal(slot.original_url, slot.label);
      });
    }
    box.appendChild(el);
  }
  // 预设声明了 reference_order 时默认按顺序勾上就绪槽位（用户可随时调整）
  const order = (currentPresetMeta() || {}).reference_order || [];
  if (order.length) {
    for (const el of box.querySelectorAll(".ref-pick")) {
      if (!el.disabled && order.includes(el.dataset.kind)) el.classList.add("active");
    }
  }
  updateRefsHint();
  updateComfyWorkflowHint();
  updateOpenRouterHints();
}

/* 预设元信息（/presets 列表项，含 reference_order） */
function currentPresetMeta() {
  const name =$("#gen-preset").value;
  return (state.presets || []).find((p) => p.name === name) || null;
}

/* 参考图面板提示：模式用途 + 与预设 reference_order 对齐状态 */
function updateRefsHint() {
  const hint = $("#gen-refs-hint");
  if (!hint) return;
  const presetMeta = currentPresetMeta();
  const order = (presetMeta && presetMeta.reference_order) || [];
  const parts = [];
  if (state.genMode === "llm") {
    parts.push("LLM 模式：已选参考图按勾选顺序作为 <image1>…<imageN> 注入提示词与出图。");
  } else {
    parts.push("经典模式：参考图在「推送 ComfyUI」时按勾选顺序作为 <image1>…<imageN> 上传（图片编辑 workflow）；选空则走文生图 workflow。");
  }
  if (order.length) {
    const selected = selectedRefKinds();
    const aligned = order.length === selected.length && order.every((k, i) => selected.includes(k));
    parts.push(
      aligned
        ? `当前预设文本按 ${order.length} 张参考图描述（${order.join(" → ")}），已对齐。`
        : `当前预设文本按 ${order.length} 张参考图描述（建议勾选：${order.join(" → ")}）`,
    );
  }
  hint.textContent = parts.join(" ");
}

/* ---------- 初始加载 ---------- */
async function init() {
  await Promise.allSettled([loadSubjects(), loadPresets()]);
  if (state.subjects.length) {
    $("#gen-subject").value = state.subjects[0].id;
  }
  $("#gen-subject").addEventListener("change", () => {
    renderRefPickers(); // 主体切换影响参考图网格（两模式都需要）
  });
  renderRefPickers().catch(() => {}); // 首次进入：立即渲染参考图面板
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
  renderSubjectList();
}

async function loadPresets() {
  state.presets = await api("/presets");
  const sel = $("#gen-preset");
  sel.innerHTML = "";
  for (const p of state.presets) {
    const opt = document.createElement("option");
    opt.value = p.name;
    opt.textContent = `${p.name}（${p.type === "list" ? "逐条" : "矩阵"} · ${p.count} 条）`;
    sel.appendChild(opt);
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
  renderRefPickers().catch(() => {}); // 预设切换：按 reference_order 重新默认勾选
  updateComfyDimensionPreview(); // sticker 预设切换后 auto 预览要跟着变（1:1 快照）
}

/* ---------- 生成 ---------- */
$("#btn-generate").addEventListener("click", async () => {
  if (!state.subjects.length) return setStatus($("#gen-status"), "先去「主体档案」建一个档案", true);
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
  const sep = $("#gen-separator").value;
  if (sep !== " ") payload.separator = sep;
  payload.mode = state.genMode;
  if (state.genMode === "llm") {
    payload.reference_kinds = selectedRefKinds();
    if (!payload.reference_kinds.length) {
      return setStatus($("#gen-status"), "LLM 模式需要选择 1–4 张参考图", true);
    }
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

function escapeHtml(text) {
  return String(text ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
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

/* ---------- ComfyUI ---------- */
$("#btn-toggle-comfy").addEventListener("click", async () => {
  $("#comfy-panel").hidden = !$("#comfy-panel").hidden;
  if (!$("#comfy-panel").hidden) {
    if (!state.comfyLoaded) await refreshComfyWorkflows().catch(() => {});
    await loadComfyWorkflowSettings().catch(() => {});
    updateComfyWorkflowHint();
  }
});

/* workflow 自动选择：LLM 模式勾了参考图 → 图片编辑 workflow；否则文生图（同主体图逻辑）。 */
function autoWorkload() {
  const s = state.comfySettings || {};
  const refs = selectedRefKinds().length; // 两模式一致：有参考图走编辑 workflow
  return refs ? s.comfy_edit_workflow : s.comfy_t2i_workflow;
}

function updateComfyWorkflowHint() {
  const el = $("#comfy-workflow-hint");
  if (!el) return;
  const count = selectedRefKinds().length;
  const workload = autoWorkload();
  el.textContent = workload
    ? count
      ? `已选 ${count} 张参考图 → 图片编辑 workflow：${workload}`
      : `未选参考图 → 文生图 workflow：${workload}`
    : "未配置 workflow：先在「设置」页选好（文生图 / 图片编辑）workflow";
}

async function loadComfyWorkflowSettings() {
  state.comfySettings = await api("/settings");
}

async function refreshComfyWorkflows() {
  const info = await api("/comfy/workflows");
  state.comfyLoaded = true;
  $("#comfy-url").value = info.base_url || $("#comfy-url").value;
  state.comfyWorkflows = (info.workflows || []).filter((w) => w.has_workflow && w.has_mapping);
}

let ASPECT_RATIO = "auto";
const ASPECT_BUTTONS = $$("#comfy-aspect .chip");
const DIM_PREVIEW = $("#comfy-dim-preview");
const TIER_TO_SIDE = { "1k": 1024, "2k": 2048 };
// Qwen-Image-2.1 官方 2K 像素（面积恒定 ~4.2MP）。后端 charvar/exporters/comfyui.py
// 的 _QWEN_2K_SIZES 是同一张表 —— 改了那边这里要同步，否则预览和实际不符。
const QWEN_2K_SIZES = {
  "1:1": [2048, 2048], "4:3": [2400, 1792], "3:4": [1792, 2400],
  "3:2": [2528, 1696], "2:3": [1696, 2528], "16:9": [2752, 1536],
  "9:16": [1536, 2752],
};

function updateComfyDimensionPreview() {
  const ratio = ASPECT_RATIO;
  const tier = $("#comfy-resolution").value;
  const presetRatio = (currentPresetMeta() || {}).preferred_aspect_ratio;
  if (ratio === "auto") {
    DIM_PREVIEW.textContent = presetRatio
      ? `智能（当前预设指定 ${presetRatio}）：自动采用预设比例；手动选择其他比例不被无声覆盖`
      : "智能：全身 2:3，胸像/头像/表情 3:4。以上按长边档位计算。";
    return;
  }
  const parts = ratio.split(":").map((n) => Number(n.trim()));
  if (parts.length !== 2 || parts.some((n) => !Number.isFinite(n) || n <= 0)) {
    DIM_PREVIEW.textContent = "比例无效"; return;
  }
  if (tier === "2k" && QWEN_2K_SIZES[ratio]) {
    const [w, h] = QWEN_2K_SIZES[ratio];
    DIM_PREVIEW.textContent = `当前输出 ${w} × ${h}px（Qwen 官方 2K 像素，面积 ~4.2MP）`;
    return;
  }
  const longSide = TIER_TO_SIDE[tier];
  const [w, h] = parts;
  const shortSide = Math.max(64, Math.round((longSide * Math.min(w, h)) / Math.max(w, h) / 64) * 64);
  const width = w >= h ? longSide : shortSide;
  const height = h >= w ? longSide : shortSide;
  DIM_PREVIEW.textContent = `当前输出 ${width} × ${height}px`;
}

function setAspect(value) {
  ASPECT_RATIO = value;
  ASPECT_BUTTONS.forEach((btn) => btn.classList.toggle("active", btn.dataset.ratio === value));
  updateComfyDimensionPreview();
}

ASPECT_BUTTONS.forEach((btn) => {
  btn.addEventListener("click", () => setAspect(btn.dataset.ratio || "auto"));
});
$("#comfy-resolution").addEventListener("change", updateComfyDimensionPreview);

/* 负向词三态：继承默认（input 禁用、请求传 null）/ 自定义（input 可编辑、原样提交，空串 = 明确不用） */
$("#comfy-negative-mode").addEventListener("change", () => {
  const custom = $("#comfy-negative-mode").value === "custom";
  const input = $("#comfy-negative");
  input.disabled = !custom;
  input.placeholder = custom
    ? "自定义负向提示词；留空 = 明确注入空负向"
    : "继承默认（workflow 内置 / 景别回退）";
});
setAspect("auto");
Media.setView("images");

$("#btn-comfy-status").addEventListener("click", async () => {
  setStatus($("#comfy-result"), "检查中…");
  try {
    const q = encodeURIComponent($("#comfy-url").value.trim());
    const info = await api(`/comfy/status?comfy_url=${q}`);
    const sys = info.system_stats?.system || {};
    setStatus($("#comfy-result"), `已连接：ComfyUI ${sys.comfyui_version || ""}（${sys.os || "?"}）`);
  } catch (err) {
    setStatus($("#comfy-result"), `连接失败：${err.message}`, true);
  }
});

$("#btn-comfy-queue").addEventListener("click", async () => {
  if (!state.lastRun) return;
  const run = state.lastRun.run_id;
  const refs = selectedRefKinds(); // 经典/LLM 一致：勾了参考图就按序上传（<image1>..<imageN>）
  const workload = autoWorkload();
  if (!workload) return setStatus($("#comfy-result"), "未配置 workflow：先在「设置」页选好（文生图 / 图片编辑）workflow", true);
  setStatus($("#comfy-result"), "提交中…");
  try {
    const body = {
      workload,
      // 负向词三态：继承默认(null) / 自定义(允许空串=明确不用)。不再 trim()||null。
      negative_prompt: $("#comfy-negative-mode").value === "custom" ? $("#comfy-negative").value.trim() : null,
      wait: $("#comfy-wait").checked,
      comfy_url: $("#comfy-url").value.trim(),
      aspect_ratio: ASPECT_RATIO,
      resolution: $("#comfy-resolution").value,
    };
    if (refs.length) body.reference_kinds = refs;
    const out = await api(`/runs/${run}/comfyui`, {
      method: "POST",
      body: JSON.stringify(body),
    });
    $("#comfy-result").textContent =
      `成功 ${out.succeeded} / 失败 ${out.failed}\n` +
      out.submissions.map((s) => {
        const head = `#${String(s.index).padStart(3, "0")} ${s.variant_id}`;
        if (!s.ok) return `${head} × ${s.error}`;
        const files = (s.files || []).map((f) => f.filename).join(", ");
        return `${head} → prompt_id ${s.prompt_id}${files ? ` → ${files}` : ""}`;
      }).join("\n");
    // 不在入队后立即同步图片：未勾「等待完成」时刚入队必然还没有成品图，同步只会报「没有可用图片」。
    // 勾了「等待完成」说明出图已结束、输出文件名已记录，此时可以放心同步取图。
    if ($("#comfy-wait").checked) {
      Media.syncImages(state.lastRun.run_id).then((data) => {
        Media.setStatus(`图片 ${data.completed}/${data.items.length} 完成${data.errors ? `，失败 ${data.errors}` : ""}`);
      }).catch((error) => Media.setStatus(`同步失败：${error.message}`, true));
    } else {
      $("#comfy-result").textContent += "\n提示：ComfyUI 出图完成后，点上方「同步图片」取图。";
    }
  } catch (err) {
    setStatus($("#comfy-result"), `失败：${err.message}`, true);
  }
});

/* ---------- OpenRouter 在线生图 ---------- */
$("#btn-toggle-openrouter").addEventListener("click", async () => {
  $("#openrouter-panel").hidden = !$("#openrouter-panel").hidden;
  if (!$("#openrouter-panel").hidden) {
    await loadOpenRouterSettings().catch(() => {});
    updateOpenRouterHints();
  }
});

let OPENROUTER_ASPECT_RATIO = "auto";
const OPENROUTER_ASPECT_BUTTONS = $$("#openrouter-aspect .chip");
const OPENROUTER_DIM_PREVIEW = $("#openrouter-dim-preview");

function setOpenRouterAspect(value) {
  OPENROUTER_ASPECT_RATIO = value;
  OPENROUTER_ASPECT_BUTTONS.forEach((btn) => btn.classList.toggle("active", btn.dataset.ratio === value));
  updateOpenRouterDimensionPreview();
}

OPENROUTER_ASPECT_BUTTONS.forEach((btn) => {
  btn.addEventListener("click", () => setOpenRouterAspect(btn.dataset.ratio || "auto"));
});
$("#openrouter-resolution").addEventListener("change", updateOpenRouterDimensionPreview);

function updateOpenRouterDimensionPreview() {
  const ratio = OPENROUTER_ASPECT_RATIO;
  const resSelect = $("#openrouter-resolution");
  const resValue = resSelect && !resSelect.closest("label").hidden ? resSelect.value : "";
  // 空 = 跟随设置页/env 模式默认；像素预览按 1k 档估（实际由后端按配置定）
  const longSide = parseLongSide(resValue) || 1024;
  const presetRatio = (currentPresetMeta() || {}).preferred_aspect_ratio;
  if (ratio === "auto") {
    OPENROUTER_DIM_PREVIEW.textContent = presetRatio
      ? `智能（当前预设指定 ${presetRatio}）：自动采用预设比例；手动选择其他比例不被无声覆盖`
      : "智能：全身 2:3，胸像/头像/表情 3:4。以上按长边档位计算。";
    return;
  }
  const parts = ratio.split(":").map((n) => Number(n.trim()));
  if (parts.length !== 2 || parts.some((n) => !Number.isFinite(n) || n <= 0)) {
    OPENROUTER_DIM_PREVIEW.textContent = "比例无效"; return;
  }
  const [w, h] = parts;
  const shortSide = Math.max(64, Math.round((longSide * Math.min(w, h)) / Math.max(w, h) / 64) * 64);
  const width = w >= h ? longSide : shortSide;
  const height = w >= h ? shortSide : longSide;
  OPENROUTER_DIM_PREVIEW.textContent = resValue
    ? `当前输出 ${width} × ${height}px（档位 ${resValue}）`
    : `当前比例 ${ratio}（档位跟随设置默认，默认为空时按长边 1024 估）`;
}

function updateOpenRouterHints() {
  const refs = selectedRefKinds();
  const mode = refs.length ? "i2i" : "t2i";
  // 勾选参考图切换了模式 → 能力与默认档位按新模式的模型重算
  if (state.openrouterMode !== mode) {
    state.openrouterMode = mode;
    loadOpenRouterSettings().catch(() => {});
    return; // 模式刚切换，等 loadOpenRouterSettings 回来再刷新提示
  }
  const caps = state.openrouterCaps;
  const noRefs = caps && caps.supports_references === false;
  $("#openrouter-refs-hint").textContent = noRefs
    ? `当前模型 ${caps.model} 只支持文生图（清单里没有 image 输入），参考图不会发送。`
    : refs.length
      ? `已选 ${refs.length} 张：${refs.join("、")}（依次对应 the first / second… reference image）`
      : "未选参考图：按纯文生图处理，提示词里的参考图指向句会被移除。";
  updateOpenRouterDimensionPreview();
}

/* 面板档位：charvar 档位（1k/2k）或 provider 枚举值（512/1K/1.5K/2K/4K…）。
   长边换算与后端 exporters.dimensions_for 的通用分支一致（1K=1024 长边）。 */
function parseLongSide(value) {
  const m = String(value || "").trim().match(/^(\d+(?:\.\d+)?)\s*([kK])?$/);
  if (!m) return null;
  return Math.max(64, Math.round(parseFloat(m[1]) * (m[2] ? 1024 : 1)));
}

function setOpenRouterResolutionOptions(caps) {
  const select = $("#openrouter-resolution");
  const enums = (caps && caps.resolutions) || [];
  const parts = [`<option value="">跟随设置默认</option>`];
  if (enums.length) {
    for (const value of enums) {
      parts.push(`<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`);
    }
  } else {
    // 模型没声明 resolution 枚举 → 档位纯算像素：charvar 档位
    for (const [value, label] of [["1k", "1k（长边 1024）"], ["2k", "2k（长边 2048）"]]) {
      parts.push(`<option value="${value}">${label}</option>`);
    }
  }
  select.innerHTML = parts.join("");
}

function setOpenRouterQualityOptions(qualities) {
  const select = $("#openrouter-quality");
  const parts = [`<option value="">跟随设置默认</option>`];
  for (const value of qualities || []) {
    parts.push(`<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`);
  }
  select.innerHTML = parts.join("");
}

/* 面板按模型清单（api-response.json）动态生成可用档位：
   声明了枚举就按枚举出选项；默认值取设置页该模式的配置（空 = 不发参数）。 */
function applyOpenRouterCapabilities(caps, defaults) {
  state.openrouterCaps = caps;
  setOpenRouterResolutionOptions(caps);
  const resSelect = $("#openrouter-resolution");
  const resField = resSelect.closest("label");
  resField.hidden = false;
  const resDefault = (defaults && defaults.resolution) || "";
  if (resDefault && Array.from(resSelect.options).some((o) => o.value === resDefault)) {
    resSelect.value = resDefault;
  }

  const qSelect = $("#openrouter-quality");
  const qField = qSelect.closest("label");
  const qualities = (caps && caps.qualities) || [];
  setOpenRouterQualityOptions(qualities);
  if (qualities.length) {
    const qDefault = (defaults && defaults.quality) || "";
    if (qDefault && Array.from(qSelect.options).some((o) => o.value === qDefault)) {
      qSelect.value = qDefault;
    }
  }
  qField.hidden = qualities.length === 0;
  qSelect.hidden = qualities.length === 0;

  const notes = [];
  if (caps && caps.resolutions && caps.resolutions.length) notes.push(`分辨率枚举：${caps.resolutions.join(" / ")}`);
  if (caps && caps.qualities && caps.qualities.length) notes.push(`质量枚举：${caps.qualities.join(" / ")}`);
  if (caps && caps.supports_references === false) notes.push("该模型不支持参考图");
  notes.push("空档 = 跟随设置默认（设置页没配 = 不发该参数）");
  $("#openrouter-caps-hint").textContent = notes.join("｜");
  updateOpenRouterHints();
}

function openrouterModeDefaults(settings, mode) {
  const or = (settings && settings.openrouter) || {};
  return {
    model: or[`${mode}_model`] || "",
    resolution: or[`${mode}_resolution`] || "",
    quality: or[`${mode}_quality`] || "",
  };
}

async function loadOpenRouterSettings() {
  const settings = await api("/settings");
  state.openrouterSettings = settings;
  const mode = state.openrouterMode || "t2i";
  const defaults = openrouterModeDefaults(settings, mode);
  const t2iModel = (settings.openrouter && settings.openrouter.t2i_model) || settings.openrouter_default_t2i_model || "（env 默认）";
  const i2iModel = (settings.openrouter && settings.openrouter.i2i_model) || settings.openrouter_default_i2i_model || "（env 默认）";
  $("#openrouter-model-hint").textContent = `文生图模型：${t2iModel}｜图片编辑模型：${i2iModel}（勾选参考图自动切到这组）`;
  const model =
    defaults.model ||
    (mode === "t2i" ? settings.openrouter_default_t2i_model : settings.openrouter_default_i2i_model) ||
    "";
  if (model) {
    try {
      const caps = await api(`/openrouter/capabilities?model=${encodeURIComponent(model)}`);
      applyOpenRouterCapabilities(caps, defaults);
    } catch (err) {
      state.openrouterCaps = null;
    }
  }
}

$("#btn-openrouter-queue").addEventListener("click", async () => {
  if (!state.lastRun) return;
  const run = state.lastRun.run_id;
  const refs = selectedRefKinds();
  setStatus($("#openrouter-result"), "推送中…（并发 4 路，30 条 1K 约 2 分钟）");
  try {
    const body = { aspect_ratio: OPENROUTER_ASPECT_RATIO };
    const resolution = $("#openrouter-resolution").value;
    if (resolution) body.resolution = resolution; // 空 = 后端用设置页/env 的模式默认
    const quality = $("#openrouter-quality").value;
    if (quality) body.quality = quality;
    if (refs.length) body.reference_kinds = refs;
    const out = await api(`/runs/${run}/openrouter`, {
      method: "POST",
      body: JSON.stringify(body),
    });
    const cost = (out.items || []).reduce((sum, e) => sum + (e.cost || 0), 0);
    $("#openrouter-result").textContent =
      `模型 ${out.model}｜成功 ${out.succeeded} / 失败 ${out.failed}｜合计 $${cost.toFixed(3)}\n` +
      (out.items || []).map((e) => {
        const head = `#${String(e.index).padStart(3, "0")} ${e.variant_id}`;
        if (!e.ok) return `${head} × ${e.error}`;
        return `${head} ✓${e.cost != null ? ` $${e.cost.toFixed(3)}` : ""}`;
      }).join("\n");
    // OpenRouter 是同步出图，产物已落盘 → 直接刷新图片墙
    await Media.syncImages(run);
    Media.setStatus(`图片 ${Media.currentData.completed}/${Media.currentData.items.length} 完成`);
  } catch (err) {
    setStatus($("#openrouter-result"), `失败：${err.message}`, true);
  }
});

setOpenRouterAspect("auto");

/* ---------- 即梦（Dreamina）在线生图 ---------- */
$("#btn-toggle-dreamina").addEventListener("click", async () => {
  $("#dreamina-panel").hidden = !$("#dreamina-panel").hidden;
  if (!$("#dreamina-panel").hidden) {
    await loadDreaminaSettings().catch(() => {});
  }
  updateDreaminaHints();
});

let DREAMINA_ASPECT_RATIO = "auto";
const DREAMINA_ASPECT_BUTTONS = $$("#dreamina-aspect .chip");

DREAMINA_ASPECT_BUTTONS.forEach((btn) => {
  btn.addEventListener("click", () => {
    DREAMINA_ASPECT_RATIO = btn.dataset.ratio || "auto";
    DREAMINA_ASPECT_BUTTONS.forEach((b) => b.classList.toggle("active", b.dataset.ratio === DREAMINA_ASPECT_RATIO));
    updateDreaminaDimPreview();
  });
});
$("#dreamina-resolution").addEventListener("change", updateDreaminaDimPreview);

/* 即梦按 --ratio 出图，像素以即梦侧为准；auto 按预设/景别规则走后端 */
function updateDreaminaDimPreview() {
  const ratio = DREAMINA_ASPECT_RATIO;
  const presetRatio = (currentPresetMeta() || {}).preferred_aspect_ratio;
  if (ratio === "auto") {
    $("#dreamina-dim-preview").textContent = presetRatio
      ? `智能（当前预设指定 ${presetRatio}）：自动按预设比例出图`
      : "智能：按条目自带的画幅出图（没有则 3:4）。";
    return;
  }
  $("#dreamina-dim-preview").textContent = `输出比例 ${ratio}（像素以即梦侧为准）`;
}

async function loadDreaminaSettings() {
  const settings = await api("/settings");
  const dm = settings.dreamina || {};
  const parts = [
    `model_version：${dm.model || "（跟随 env，CLI 默认 5.0）"}`,
    `分辨率：${dm.resolution || "（跟随 env，默认 2K）"}`,
  ];
  if (settings.dreamina_cli_available === false) {
    parts.push("⚠ 未找到 dreamina CLI（需安装并 dreamina login）");
  }
  $("#dreamina-model-hint").textContent = parts.join("｜");
}

function updateDreaminaHints() {
  const refs = selectedRefKinds();
  $("#dreamina-refs-hint").textContent = refs.length
    ? `已选 ${refs.length} 张：${refs.join("、")}（带参考图 = image2image，按顺序对应）`
    : "未选参考图：纯 text2image（提示词上限约 1600 字符）。";
  updateDreaminaDimPreview();
}

$("#btn-dreamina-queue").addEventListener("click", async () => {
  if (!state.lastRun) return;
  const run = state.lastRun.run_id;
  const refs = selectedRefKinds();
  setStatus($("#dreamina-result"), "推送中…（每张消耗即梦积分，单张约 60 秒）");
  try {
    const body = { aspect_ratio: DREAMINA_ASPECT_RATIO };
    const resolution = $("#dreamina-resolution").value;
    if (resolution) body.resolution = resolution; // 空 = 后端用设置页/env 默认
    if (refs.length) body.reference_kinds = refs;
    const out = await api(`/runs/${run}/dreamina`, {
      method: "POST",
      body: JSON.stringify(body),
    });
    $("#dreamina-result").textContent =
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
    setStatus($("#dreamina-result"), `失败：${err.message}`, true);
  }
});

/* ---------- 主体档案 ---------- */
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
  $("#s-outfit").value = (s.outfit_lock || []).join("\n");
  $("#s-extra").value = s.extra || "";
  loadSubjectImages();
  loadSubjectTemplate();
}

function clearSubjectForm() {
  state.currentSubject = null;
  renderSubjectList();
  $("#subject-form-title").textContent = "档案详情";
  ["s-id", "s-name", "s-trigger", "s-style", "s-identity", "s-outfit", "s-extra"].forEach((id) => ($(id).value = ""));
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
const SLOT_LABELS = {
  character_sheet: "人物半身像＋三视图",
  headshot: "人物半身像",
  front: "全身正面",
  side: "全身侧面",
  behind: "全身背面",
};
const SLOT_HINTS = {
  character_sheet: "文生图，第一步",
  headshot: "基于三视图编辑",
  front: "基于三视图编辑",
  side: "基于三视图编辑",
  behind: "基于三视图编辑",
};

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

/* ---------- 设置 ---------- */
const ASPECT_OPTIONS = ["3:4", "1:1", "16:9", "4:3", "9:16", "2:3", "3:2", "21:9"];

/* OpenRouter 设置页：两种模式（t2i 文生图 / i2i 图片编辑）各一组模型 + 档位。
   模型选完按 api-response.json 的 supported_parameters 动态加载档位；
   该模型不支持的重置为空（空 = 请求显式值 > env 默认 > 不发送）。 */
let OR_MODEL_OPTIONS = []; // /openrouter/models 的 [{id, name}]
const OR_CAPS_BY_MODE = { t2i: null, i2i: null };

function orModelOptionsHtml() {
  const parts = [`<option value="">（跟随 env 默认）</option>`];
  for (const m of OR_MODEL_OPTIONS) {
    parts.push(`<option value="${escapeHtml(m.id)}">${escapeHtml(m.name)}</option>`);
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
    for (const [sel, value] of [["#set-t2i", settings.comfy_t2i_workflow], ["#set-edit", settings.comfy_edit_workflow]]) {
      const el = $(sel);
      el.innerHTML = names.map((n) => `<option value="${n}">${n}</option>`).join("") ||
        `<option value="">（data/workflows 没有成对 workflow）</option>`;
      if (value && names.includes(value)) el.value = value;
    }
    $("#set-llm-base").value = settings.llm.base_url || "";
    $("#set-llm-model").value = settings.llm.model || "";
    $("#set-llm-key").value = "";
    $("#set-llm-key").placeholder = settings.llm_configured ? "已配置（留空保持不变）" : "留空 = 环境变量 LLM_API_KEY";
    $("#set-negative").value = settings.default_negative_prompt || "";
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
    list.innerHTML = OR_MODEL_OPTIONS.map((m) => `<option value="${escapeHtml(m.id)}">${escapeHtml(m.name)}</option>`).join("");
    await refreshOpenRouterSettingFields(settings);
    $("#set-or-key").value = "";
    $("#set-or-key").placeholder = settings.openrouter_configured ? "已配置（留空保持不变）" : "留空 = OP_IMAGE_API_KEY / LLM_API_KEY";
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
      .concat(videoModels.map((m) => `<option value="${escapeHtml(m.id)}">${escapeHtml(m.name || m.id)}</option>`));
    if (orv.model && !videoModels.some((m) => m.id === orv.model)) {
      vOptions.push(`<option value="${escapeHtml(orv.model)}">${escapeHtml(orv.model)}（不在当前清单里）</option>`);
    }
    vSel.innerHTML = vOptions.join("");
    vSel.value = orv.model || "";
    // 即梦：model 自由文本（空 = env/CLI 默认）；分辨率档固定 2K/4K 枚举
    const dm = settings.dreamina || {};
    $("#set-dreamina-model").value = dm.model || "";
    const dmRes = $("#set-dreamina-resolution");
    dmRes.value = Array.from(dmRes.options).some((o) => o.value === dm.resolution) ? dm.resolution : "";
    $("#dreamina-cli-status").textContent = settings.dreamina_cli_available === false
      ? "⚠ 本机没有找到 dreamina CLI：安装（curl -s https://jimeng.jianying.com/cli | bash）并完成 dreamina login 后才能出图"
      : "dreamina CLI 已就绪；登录态用 dreamina user_credit 可以自查";
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
  // openrouter 同理：model 空串 = 落库为空 = 回到 env；api_key 空串 = 保留已存值；
  // 分辨率/质量收不起来（模型没声明）时 select 值就是空串 → 落库空 = env 默认
  patch.openrouter = {
    t2i_model: $("#set-or-t2i-model").value.trim(),
    t2i_resolution: $("#set-or-t2i-res-field").hidden ? "" : $("#set-or-t2i-resolution").value,
    t2i_quality: $("#set-or-t2i-quality-field").hidden ? "" : $("#set-or-t2i-quality").value,
    i2i_model: $("#set-or-i2i-model").value.trim(),
    i2i_resolution: $("#set-or-i2i-res-field").hidden ? "" : $("#set-or-i2i-resolution").value,
    i2i_quality: $("#set-or-i2i-quality-field").hidden ? "" : $("#set-or-i2i-quality").value,
    api_key: $("#set-or-key").value.trim(),
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
  try {
    await api("/settings", { method: "PUT", body: JSON.stringify(patch) });
    setStatus($("#settings-status"), "设置已保存");
    await loadSettings();
    // 模型改了 → 面板的可用档位要按新模型的能力重算
    await loadOpenRouterSettings().catch(() => {});
  } catch (err) {
    setStatus($("#settings-status"), `保存失败：${err.message}`, true);
  }
});

/* 拉取 OpenRouter 可用生图模型：填 datalist（Playground 页的模型输入框）
   并重算设置页两个模式的档位 + 面板能力（模型换了要重新校对参数能力）。
   refresh=1 强制重新拉取并覆盖本地缓存。 */
$("#btn-or-models-refresh").addEventListener("click", async () => {
  const statusEl = $("#or-models-status");
  setStatus(statusEl, "拉取中…");
  try {
    const data = await api("/openrouter/models?refresh=1");
    const models = data.models || [];
    setStatus(statusEl, `已刷新 ${models.length} 个模型`);
    // 清单变了 → 设置页两模式与生图面板的档位都可能要重算
    await loadSettings();
    await loadOpenRouterSettings().catch(() => {});
  } catch (err) {
    setStatus(statusEl, `拉取失败：${err.message}`, true);
  }
});

/* ---------- 历史记录 ---------- */
async function loadRuns() {
  const runs = await api("/runs?limit=100");
  const tbody = $("#runs-table tbody");
  tbody.innerHTML = "";
  for (const r of runs) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td class="prompt" title="${escapeHtml(r.run_id)}">${escapeHtml(r.run_id)}</td><td>${escapeHtml(r.subject_id)}</td>
      <td>${escapeHtml(r.preset)}</td><td>${r.seed}</td><td>${r.item_count}</td>
      <td>${r.comfy_count}</td><td>${escapeHtml(r.created_at)}</td>
      <td><div class="row-actions"><button data-run="${escapeHtml(r.run_id)}">查看</button>
          <button class="danger" data-del="${escapeHtml(r.run_id)}">删除</button></div></td>`;
    tbody.appendChild(tr);
  }
  tbody.querySelectorAll("button[data-run]").forEach((btn) =>
    btn.addEventListener("click", async () => {
      try {
        await selectRound(btn.dataset.run); // 复用轮次条切换：进轮次条 + 结果卡 + 图墙
        $$(".tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === "gen"));
        $$(".tab-page").forEach((p) => p.classList.toggle("active", p.id === "tab-gen"));
      } catch (err) {
        alert(err.message);
      }
    })
  );
  tbody.querySelectorAll("button[data-del]").forEach((btn) =>
    btn.addEventListener("click", async () => {
      if (!confirm(`删除生成记录 ${btn.dataset.del}？`)) return;
      await api(`/runs/${encodeURIComponent(btn.dataset.del)}`, { method: "DELETE" });
      loadRuns();
    })
  );
}

$("#btn-runs-refresh").addEventListener("click", loadRuns);

init();
