/* 语音（Playground）页（web 面板）：
   文本 → 一行语音（OpenRouter 同步通路）。与视频页同构：目录下拉 + voice 枚举
   联动 + 最近生成；关键差异：/tts/generate 是**同步**请求（一次等音频返回），
   没有作业轮询、没有暂存池。
   voice 随所选模型联动：目录枚举非空时必填（实测 heygen/minimax 不带 voice 是
   400），后端空值自动兜底第一项 —— 下拉的「跟随默认」就是这个含义；枚举空则
   该字段收起（不发 voice，交 provider 默认）。
   HeyGen 系 5000 字符上限前端计数拦截；其他模型上限未知，交给 provider。 */

const TTS_HEYGEN_PREFIX = "heygen/";
const TTS_HEYGEN_MAX_CHARS = 5000;

const ttsState = {
  catalog: null,
};

function currentTtsModelEntry() {
  const catalog = ttsState.catalog || { data: [], default_model: "" };
  const chosen = $("#tts-model").value;
  const modelId = chosen || catalog.default_model || "";
  return (catalog.data || []).find((m) => m.id === modelId) || null;
}

function currentTtsModelId() {
  const entry = currentTtsModelEntry();
  return entry ? entry.id : "";
}

/* ---------- 模型目录 ---------- */

async function loadTtsCatalog(refresh) {
  try {
    ttsState.catalog = await api(`/tts/models${refresh ? "?refresh=true" : ""}`);
  } catch (err) {
    ttsState.catalog = { data: [], default_model: "" };
    setStatus($("#tts-status"), `模型目录加载失败：${err.message}`, true);
  }
  applyTtsCatalog();
}

function applyTtsCatalog() {
  const catalog = ttsState.catalog || { data: [], default_model: "" };
  const models = catalog.data || [];
  const sel = $("#tts-model");
  const def = catalog.default_model || "";
  sel.innerHTML = [`<option value="">跟随默认（${escapeHtml(def || "未配置")}）</option>`]
    .concat(models.map((m) =>
      `<option value="${escapeHtml(m.id)}">${escapeHtml(m.name || m.id)}</option>`))
    .join("");
  sel.value = "";
  applyTtsEnums();
}

/* voice 枚举联动：枚举非空 → 首项「跟随默认（枚举第一项）」+ 全量枚举；
   枚举空（fish/seed 等走克隆或 prompt 的模型）→ 该字段收起 + 灰字说明。 */
function applyTtsEnums() {
  const entry = currentTtsModelEntry();
  const voices = (entry && Array.isArray(entry.supported_voices))
    ? entry.supported_voices.map(String) : [];
  const voiceField = $("#tts-voice-field");
  const sel = $("#tts-voice");
  if (voices.length) {
    sel.innerHTML = [`<option value="">跟随默认（${escapeHtml(voices[0])}）</option>`]
      .concat(voices.map((v) => `<option value="${escapeHtml(v)}">${escapeHtml(v)}</option>`))
      .join("");
    sel.value = "";
    voiceField.hidden = false;
    $("#tts-voice-note").textContent = "";
  } else {
    sel.innerHTML = "";
    voiceField.hidden = true;
    $("#tts-voice-note").textContent = "该模型目录没列 voice 枚举，请求不发送 voice";
  }
  updateTtsCharHint();
}

function ttsCharLimit() {
  return currentTtsModelId().startsWith(TTS_HEYGEN_PREFIX) ? TTS_HEYGEN_MAX_CHARS : null;
}

function updateTtsCharHint() {
  const text = $("#tts-text").value;
  const count = [...text.trim()].length;
  const limit = ttsCharLimit();
  const hint = $("#tts-char-hint");
  hint.textContent = limit ? `${count}/${limit}` : `${count} 字符`;
  hint.classList.toggle("error", limit != null && count > limit);
}

/* ---------- 生成 ---------- */

$("#btn-tts-generate").addEventListener("click", async () => {
  const text = $("#tts-text").value.trim();
  if (!text) return setStatus($("#tts-status"), "文本为空", true);
  const limit = ttsCharLimit();
  if (limit != null && [...text].length > limit) {
    return setStatus($("#tts-status"), `HeyGen 单次合成上限 ${limit} 字符，当前 ${[...text].length}`, true);
  }
  const body = { text, voice: $("#tts-voice").value || "" };
  const model = $("#tts-model").value;
  if (model) body.model = model;
  const fmt = $("#tts-format").value;
  if (fmt) body.response_format = fmt;

  const btn = $("#btn-tts-generate");
  btn.disabled = true;
  setStatus($("#tts-status"), "合成中…（一般几秒到几十秒，文本越长越慢）");
  try {
    const out = await api("/tts/generate", { method: "POST", body: JSON.stringify(body) });
    renderTtsResult(out);
    loadTtsRecent();
    setStatus($("#tts-status"),
      `完成：${out.duration_s != null ? out.duration_s.toFixed(1) + "s 音频，" : ""}用时 ${(out.duration_ms / 1000).toFixed(0)}s` +
      (out.cost != null ? `，预计 $${Number(out.cost).toFixed(4)}` : ""));
  } catch (err) {
    setStatus($("#tts-status"), `合成失败：${err.message}`, true);
  } finally {
    btn.disabled = false;
  }
});

function renderTtsResult(s) {
  $("#tts-result-card").hidden = false;
  const bits = [];
  if (s.model) bits.push(escapeHtml(String(s.model)));
  if (s.voice) bits.push(`voice ${escapeHtml(String(s.voice))}`);
  bits.push(escapeHtml(s.response_format || "pcm"));
  if (s.sample_rate) bits.push(`${s.sample_rate} Hz`);
  $("#tts-result-summary").textContent = bits.join(" · ");
  const player = $("#tts-result-player");
  player.src = `/tts/${encodeURIComponent(s.run_id)}/audio`;
  const audioUrl = `/tts/${encodeURIComponent(s.run_id)}/audio`;
  const meta = [];
  if (s.duration_s != null) meta.push(`${s.duration_s.toFixed(1)}s`);
  if (s.size_bytes) meta.push(`${(s.size_bytes / 1024).toFixed(0)} KB`);
  if (s.cost != null) meta.push(`预计 $${Number(s.cost).toFixed(4)}`);
  if (s.duration_ms) meta.push(`用时 ${(s.duration_ms / 1000).toFixed(0)}s`);
  $("#tts-result-meta").textContent = meta.join(" · ");
  const dl = $("#tts-dl-audio");
  dl.hidden = false;
  dl.href = audioUrl;
  dl.download = `${s.run_id}.${s.response_format === "mp3" ? "mp3" : "wav"}`;
  const mf = $("#tts-dl-manifest");
  mf.hidden = false;
  mf.href = `/tts/${encodeURIComponent(s.run_id)}/manifest`;
}

$("#tts-text").addEventListener("input", updateTtsCharHint);
$("#tts-model").addEventListener("change", applyTtsEnums);

/* ---------- 最近生成 ---------- */

async function loadTtsRecent() {
  const box = $("#tts-recent");
  try {
    const records = await api("/tts?limit=24");
    box.innerHTML = "";
    if (!records.length) {
      box.innerHTML = `<span class="hint">还没有语音记录</span>`;
      return;
    }
    for (const rec of records) {
      box.appendChild(buildTtsRecentCard(rec));
    }
  } catch (err) {
    box.innerHTML = `<span class="hint">加载失败：${escapeHtml(err.message)}</span>`;
  }
}

const TTS_STATUS_LABELS = {
  ready: "完成",
  failed: "失败",
  in_progress: "合成中",
};

function buildTtsRecentCard(rec) {
  const card = document.createElement("div");
  card.className = "image-card tts-card";
  card.dataset.runId = rec.run_id;
  const audio = document.createElement("audio");
  audio.controls = true;
  audio.preload = "none";
  if (rec.has_audio) {
    audio.src = `/tts/${encodeURIComponent(rec.run_id)}/audio`;
    card.appendChild(audio);
  } else {
    card.insertAdjacentHTML(
      "beforeend",
      `<div class="image-placeholder">${escapeHtml(TTS_STATUS_LABELS[rec.status] || rec.status)}</div>`
    );
  }
  const meta = document.createElement("div");
  meta.className = "image-meta";
  meta.innerHTML = `<span>${escapeHtml(String(rec.model || ""))}</span>
    <span>${escapeHtml((rec.created_at || "").slice(5, 16))}</span>`;
  card.appendChild(meta);
  const p = document.createElement("p");
  p.textContent = rec.text || "";
  p.title = rec.text || "";
  card.appendChild(p);
  const actions = document.createElement("div");
  actions.className = "image-actions";
  if (rec.text && rec.text.trim()) {
    const textBtn = document.createElement("button");
    textBtn.textContent = "查看文本";
    textBtn.addEventListener("click", () =>
      Media.showTextModal("语音 · 文本", rec.text || "", { copyable: true }));
    actions.appendChild(textBtn);
  }
  if (rec.has_audio) {
    const playBtn = document.createElement("button");
    playBtn.textContent = "试放";
    playBtn.addEventListener("click", () => audio.play().catch(() => {}));
    actions.appendChild(playBtn);
    const dl = document.createElement("a");
    dl.className = "btn";
    dl.href = `/tts/${encodeURIComponent(rec.run_id)}/audio`;
    dl.download = `${rec.run_id}.wav`;
    dl.textContent = "下载";
    actions.appendChild(dl);
  }
  const reuseBtn = document.createElement("button");
  reuseBtn.textContent = "复用文本";
  reuseBtn.addEventListener("click", () => {
    $("#tts-text").value = rec.text || "";
    if (rec.voice) {
      const sel = $("#tts-voice");
      if ([...sel.options].some((o) => o.value === rec.voice)) sel.value = rec.voice;
    }
    updateTtsCharHint();
    window.scrollTo({ top: 0, behavior: "smooth" });
  });
  actions.appendChild(reuseBtn);
  card.appendChild(actions);
  const note = document.createElement("div");
  note.className = "image-meta";
  note.innerHTML = `<span>${escapeHtml([
    rec.voice || "",
    rec.duration_s != null ? rec.duration_s + "s" : "",
    TTS_STATUS_LABELS[rec.status] || rec.status,
  ].filter(Boolean).join(" · "))}</span>`;
  card.appendChild(note);
  if (rec.error) {
    const errLine = document.createElement("p");
    errLine.className = "hint";
    errLine.textContent = rec.error;
    card.appendChild(errLine);
    errLine.title = rec.error;
  }
  return card;
}

$("#btn-tts-recent-refresh").addEventListener("click", loadTtsRecent);

/* ---------- 初始化 ---------- */

function initTts() {
  updateTtsCharHint();
  loadTtsCatalog().catch(() => {});
  loadTtsRecent().catch(() => {});
}

initTts();