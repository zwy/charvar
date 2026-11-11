/* 放大（Playground）页脚本（upscale.js）：
   SeedVR2（ComfyUI workflow，画质修复，慢）与 FFmpeg（本地插值重编码，快）两条后端，
   都把**短边**放大到目标档位；源短边已达目标时后端原样复制返回（不跑放大）。
   与视频（video.js）同构的 adhoc 家族异步作业：提交 → POST /upscales/generate 返回
   run_id → 每 8s 轮询 /upscales/{run_id}/status 到 ready/failed；页面载入时从
   「最近放大」列表接上还在跑的作业（跨刷新不断线）。仅 1 条输入视频：上传暂存
   POST /upscales/upload（≤200MB，魔数判容器），ready 时后端已清暂存、前端摘条目。 */

"use strict";

const UP_STATUS_LABELS = {
  ready: "完成",
  failed: "失败",
  pending: "已入队",
  in_progress: "放大中",
};

const upsState = {
  backend: "comfyui",
  target: "1080p",
  input: null, // { id, name, localUrl }（暂存输入视频）
  activeRun: null,
  pollTimer: null,
  pollSince: null,
};

const UP_BACKEND_HINT = {
  comfyui: "SeedVR2 3B int8 超分修复 · ComfyUI 队列 · 可能几分钟",
  ffmpeg: "本机 FFmpeg lanczos 插值重编码 · 秒级出片",
};

function upsSegToggle() {
  $$("#ups-backend .seg").forEach((btn) =>
    btn.classList.toggle("active", btn.dataset.backend === upsState.backend)
  );
}

function makeUpsEmpty() {
  const box = document.createElement("div");
  box.className = "ref-empty";
  box.textContent = "未选输入视频";
  return box;
}

function renderUpsInput() {
  const box = $("#ups-input-list");
  if (!box) return;
  box.innerHTML = "";
  $("#ups-input-count").textContent = `已选 ${upsState.input ? 1 : 0} 条`;
  if (!upsState.input) {
    box.appendChild(makeUpsEmpty());
    return;
  }
  const el = document.createElement("div");
  el.className = "ref-pick";
  const preview = document.createElement("video");
  preview.muted = true;
  preview.preload = "metadata";
  preview.src = `/upscales/upload/${encodeURIComponent(upsState.input.id)}`;
  const name = document.createElement("span");
  name.className = "ref-name";
  name.textContent = upsState.input.name || "输入视频";
  el.appendChild(preview);
  el.appendChild(name);
  box.appendChild(el);
}
/* ---------- 输入视频上传 / 移除 ---------- */

$("#ups-input-file").addEventListener("change", async (ev) => {
  const file = (ev.target.files || [])[0];
  ev.target.value = "";
  if (!file) return;
  try {
    const out = await apiUpload("/upscales/upload", file);
    upsState.input = { id: out.id, name: out.name || file.name, localUrl: URL.createObjectURL(file) };
    renderUpsInput();
  } catch (err) {
    setStatus($("#ups-status"), `上传失败：${err.message}`, true);
  }
});

$("#btn-ups-input-clear").addEventListener("click", async () => {
  const ref = upsState.input;
  if (ref && ref.id) {
    try {
      await api(`/upscales/upload/${encodeURIComponent(ref.id)}`, { method: "DELETE" });
    } catch { /* 暂存可能已被清理，忽略 */ }
  }
  if (ref && ref.localUrl) URL.revokeObjectURL(ref.localUrl);
  upsState.input = null;
  renderUpsInput();
});

/* ---------- 提交与轮询（异步作业，同 video.js 的节奏） ---------- */

$("#btn-ups-generate").addEventListener("click", async () => {
  if (!upsState.input) {
    return setStatus($("#ups-status"), "先上传要放大的视频（≤200MB，mp4/webm/mov）", true);
  }
  const body = {
    staged_id: upsState.input.id,
    backend: upsState.backend, // 分段控件恒有显式值
    target: $("#ups-target").value || upsState.target,
  };
  const btn = $("#btn-ups-generate");
  btn.disabled = true;
  const costNote = upsState.backend === "comfyui"
    ? "SeedVR2 走 ComfyUI 队列，可能要几分钟"
    : "FFmpeg 本机重编码，一般几十秒内";
  setStatus($("#ups-status"), `提交中…（${costNote}）`);
  try {
    const out = await api("/upscales/generate", { method: "POST", body: JSON.stringify(body) });
    renderUpsResult(out);
    startUpsPolling(out.run_id);
    loadUpsRecent();
  } catch (err) {
    setStatus($("#ups-status"), `提交失败：${err.message}`, true);
    btn.disabled = false;
  }
});

function startUpsPolling(runId) {
  stopUpsPolling();
  upsState.activeRun = runId;
  upsState.pollSince = Date.now();
  setStatus($("#ups-status"), "放大中…（可离开页面，历史列表会留着）");
  const tick = async () => {
    let s;
    try {
      s = await api(`/upscales/${encodeURIComponent(runId)}/status`);
    } catch (err) {
      setStatus($("#ups-status"), `查询失败重试中：${err.message}`, true);
      return;
    }
    if (s.poll_error) {
      setStatus($("#ups-status"), `查询重试中：${s.poll_error}`, true);
      return;
    }
    if (s.status === "ready") {
      stopUpsPolling();
      $("#btn-ups-generate").disabled = false;
      renderUpsResult(s);
      dropUpsInputSent();
      loadUpsRecent();
      setStatus($("#ups-status"),
        `完成：${s.passthrough ? "源已达标，原样返回" : `${s.target_short || "?"}p 短边已就绪`}` +
        `，用时 ${(s.duration_ms / 1000).toFixed(0)}s`);
      return;
    }
    if (s.status === "failed") {
      stopUpsPolling();
      $("#btn-ups-generate").disabled = false;
      renderUpsResult(s);
      loadUpsRecent();
      setStatus($("#ups-status"), `失败：${s.error || "作业失败"}`, true);
      return;
    }
    const waited = Math.round((Date.now() - (upsState.pollSince || Date.now())) / 1000);
    setStatus($("#ups-status"), `放大中…（已等 ${waited}s，${UP_STATUS_LABELS[s.status] || s.status}）`);
  };
  tick();
  upsState.pollTimer = setInterval(tick, 8000);
}

function stopUpsPolling() {
  if (upsState.pollTimer) clearInterval(upsState.pollTimer);
  upsState.pollTimer = null;
  upsState.activeRun = null;
  upsState.pollSince = null;
}

/* ready 时后端已清暂存输入：前端同步摘掉本地条目（objectURL 记得放掉） */
function dropUpsInputSent() {
  if (upsState.input && upsState.input.localUrl) URL.revokeObjectURL(upsState.input.localUrl);
  upsState.input = null;
  renderUpsInput();
}
/* ---------- 结果卡 ---------- */

function renderUpsResult(s) {
  $("#ups-result-card").hidden = false;
  $("#ups-result-summary").textContent = [
    s.backend === "comfyui" ? "SeedVR2" : "FFmpeg",
    s.target || "",
    (s.width ? `${s.width}×${s.height}` : ""),
    s.passthrough ? "源已达标（未放大）" : (s.multiplier ? `×${Number(s.multiplier).toFixed(2)}` : ""),
  ].filter(Boolean).join(" · ");
  const player = $("#ups-result-player");
  if (s.status === "ready" && s.video_url) {
    player.src = s.video_url;
    player.poster = s.poster_url || "";
    const dl = $("#ups-dl-video");
    dl.href = s.video_url;
    dl.download = `${s.run_id}.mp4`; // URL 末段是 "video"，不显式给名会存成无扩展名
    dl.hidden = false;
    const manifest = $("#ups-dl-manifest");
    manifest.href = `/upscales/${encodeURIComponent(s.run_id)}/manifest`;
    manifest.hidden = false;
    $("#ups-result-log").textContent =
      `run_id: ${s.run_id}\n后端: ${s.backend || ""}\n用时: ${(s.duration_ms / 1000).toFixed(1)}s` +
      `\n源短边: ${s.source_short || "?"} → 目标: ${s.target_short || s.target || "?"}` +
      (s.passthrough ? "\n源已达标：原样复制返回" : "");
    player.scrollIntoView({ block: "nearest" });
  } else if (s.status === "failed") {
    player.removeAttribute("src");
    $("#ups-result-log").textContent = `run_id: ${s.run_id}\n失败原因：${s.error || ""}`;
  } else {
    player.removeAttribute("src");
    $("#ups-result-log").textContent =
      `run_id: ${s.run_id}\n状态: ${s.status || "pending"}（作业 ${s.job_id || ""}）`;
  }
}

/* ---------- 最近放大（分页，同 video.js 的分页器用法） ---------- */

const UPS_RECENT_PAGE_SIZE = 24;
const upsRecentState = { page: 0, seq: 0 };

async function loadUpsRecent(page = upsRecentState.page) {
  const seq = ++upsRecentState.seq; // 快速连点/并发的旧响应不回写新页
  upsRecentState.page = Math.max(0, page);
  const box = $("#ups-recent");
  try {
    const offset = upsRecentState.page * UPS_RECENT_PAGE_SIZE;
    const { items, total } = await apiPaged(
      `/upscales?limit=${UPS_RECENT_PAGE_SIZE}&offset=${offset}`
    );
    if (seq !== upsRecentState.seq) return;
    box.innerHTML = "";
    if (!items.length) {
      if (upsRecentState.page > 0) {
        box.innerHTML = `<span class="hint">这一页没有记录</span>`;
        refreshPager(box, "ups-recent-pager", {
          page: upsRecentState.page,
          pageSize: UPS_RECENT_PAGE_SIZE,
          total,
          goto: loadUpsRecent,
        });
      } else {
        box.innerHTML = `<span class="hint">还没有放大记录</span>`;
      }
      return;
    }
    for (const rec of items) {
      box.appendChild(buildUpsRecentCard(rec));
    }
    refreshPager(box, "ups-recent-pager", {
      page: upsRecentState.page,
      pageSize: UPS_RECENT_PAGE_SIZE,
      total,
      goto: loadUpsRecent,
    });
    // 页面刷新后接着盯还在跑的作业（跨刷新不断线）；只看当前页
    const pending = items.find((r) => r.status === "pending" || r.status === "in_progress");
    if (pending && upsState.activeRun !== pending.run_id) {
      startUpsPolling(pending.run_id);
    }
  } catch (err) {
    box.innerHTML = `<span class="hint">加载失败：${escapeHtml(err.message)}</span>`;
  }
}

function buildUpsRecentCard(rec) {
  const card = document.createElement("div");
  card.className = "image-card video-card";
  card.dataset.runId = rec.run_id;
  if (rec.has_video) {
    card.innerHTML = `<video controls preload="metadata" poster="/upscales/${encodeURIComponent(rec.run_id)}/poster" src="/upscales/${encodeURIComponent(rec.run_id)}/video"></video>`;
  } else {
    card.innerHTML = `<div class="image-placeholder">${escapeHtml(UP_STATUS_LABELS[rec.status] || rec.status)}</div>`;
  }
  const meta = document.createElement("div");
  meta.className = "image-meta";
  const backendLabel = rec.backend === "comfyui" ? "SeedVR2" : (rec.backend === "ffmpeg" ? "FFmpeg" : (rec.backend || ""));
  meta.innerHTML = `<span>${escapeHtml(backendLabel)} · ${escapeHtml(rec.target || "")}</span>
    <span>${escapeHtml((rec.created_at || "").slice(5, 16))}</span>`;
  card.appendChild(meta);
  const p = document.createElement("p");
  p.textContent = rec.input_name || "";
  p.title = rec.input_name || "";
  card.appendChild(p);
  const actions = document.createElement("div");
  actions.className = "image-actions";
  if (rec.has_video) {
    const openBtn = document.createElement("button");
    openBtn.className = "btn-icon";
    openBtn.title = "弹层播放";
    openBtn.setAttribute("aria-label", "弹层播放");
    openBtn.innerHTML = icon("play");
    openBtn.addEventListener("click", () => {
      Media.showPanelModal("放大 · " + rec.run_id, (body) => {
        const vid = document.createElement("video");
        vid.controls = true;
        vid.autoplay = true;
        vid.preload = "metadata";
        vid.poster = `/upscales/${encodeURIComponent(rec.run_id)}/poster`;
        vid.src = `/upscales/${encodeURIComponent(rec.run_id)}/video`;
        vid.style.maxWidth = "100%";
        body.appendChild(vid);
      });
    });
    actions.appendChild(openBtn);
    const dl = document.createElement("a");
    dl.className = "btn btn-icon";
    dl.href = `/upscales/${encodeURIComponent(rec.run_id)}/video`;
    dl.download = `${rec.run_id}.mp4`;
    dl.title = "下载 MP4";
    dl.setAttribute("aria-label", "下载 MP4");
    dl.innerHTML = icon("download");
    actions.appendChild(dl);
  }
  card.appendChild(actions);
  const note = document.createElement("div");
  note.className = "image-meta";
  const mult = rec.multiplier ? `×${Number(rec.multiplier).toFixed(2)}` : (rec.status === "ready" ? "已达标" : "");
  note.innerHTML = `<span>${escapeHtml([rec.width ? `${rec.width}×${rec.height}` : "", mult, UP_STATUS_LABELS[rec.status] || rec.status].filter(Boolean).join(" · "))}</span>`;
  card.appendChild(note);
  if (rec.error) {
    const errLine = document.createElement("p");
    errLine.className = "hint";
    errLine.textContent = rec.error;
    card.appendChild(errLine);
  }
  return card;
}

$("#btn-ups-recent-refresh").addEventListener("click", () => loadUpsRecent());

/* ---------- 后端切换与初始化 ---------- */

function applyUpsBackendUI() {
  upsSegToggle();
  $("#ups-backend-hint").textContent = UP_BACKEND_HINT[upsState.backend] || "";
}

function initUps() {
  applyUpsBackendUI();
  $$("#ups-backend .seg").forEach((btn) =>
    btn.addEventListener("click", () => {
      if (upsState.backend === btn.dataset.backend) return;
      upsState.backend = btn.dataset.backend;
      applyUpsBackendUI();
    })
  );
  // 设置页存的默认偏好只做初始值（请求永远显式发送当前选中项；用户已动过就不回填）
  api("/settings").then((s) => {
    const up = s.upscale || {};
    if (upsState.activeRun === null && upsState.pollTimer === null) {
      if (["comfyui", "ffmpeg"].includes(up.backend)) upsState.backend = up.backend;
      if (["720p", "1080p", "4k"].includes(up.target)) $("#ups-target").value = up.target;
      applyUpsBackendUI();
    }
  }).catch(() => {});
  renderUpsInput();
  loadUpsRecent().catch(() => {});
}

initUps();


