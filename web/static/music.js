/* 音乐页（web 面板）：
   提示词 → 一行歌曲（OpenRouter Lyria 等，chat/completions 流式音频）。与语音页同构：
   目录下拉 + 最近生成；关键差异：/music/generate 也是**同步**请求，但整曲生成可能要
   几分钟（按钮旁 hint 说明等待，按钮禁用直到返回），没有作业轮询、没有暂存池。
   seed 空串 = 不发送；显式给才透传（目录声明 seed 的模型）。 */

const musicState = {
  catalog: null,
};

function currentMusicModelId() {
  return $("#music-model").value || "";
}

/* ---------- 模型目录 ---------- */

async function loadMusicCatalog(refresh) {
  try {
    musicState.catalog = await api(`/music/models${refresh ? "?refresh=true" : ""}`);
  } catch (err) {
    musicState.catalog = { data: [], default_model: "" };
    setStatus($("#music-status"), `模型目录加载失败：${err.message}`, true);
  }
  applyMusicCatalog();
}

function applyMusicCatalog() {
  const catalog = musicState.catalog || { data: [], default_model: "" };
  const models = catalog.data || [];
  const sel = $("#music-model");
  const def = catalog.default_model || "";
  sel.innerHTML = [`<option value="">跟随默认（${escapeHtml(def || "未配置")}）</option>`]
    .concat(models.map((m) =>
      `<option value="${escapeHtml(m.id)}">${escapeHtml(m.name || m.id)}</option>`))
    .join("");
  sel.value = "";
}

/* ---------- 生成 ---------- */

$("#btn-music-generate").addEventListener("click", async () => {
  const prompt = $("#music-prompt").value.trim();
  if (!prompt) return setStatus($("#music-status"), "提示词为空", true);
  const body = { prompt };
  const model = currentMusicModelId();
  if (model) body.model = model;
  const seed = String($("#music-seed").value || "").trim();
  if (seed) body.seed = seed;

  const btn = $("#btn-music-generate");
  btn.disabled = true;
  setStatus($("#music-status"), "生成中…（整曲可能要几分钟，等流收完才返回）");
  try {
    const out = await api("/music/generate", { method: "POST", body: JSON.stringify(body) });
    renderMusicResult(out);
    loadMusicRecent(0); // 新音频必然在最前，回到第一页看它
    setStatus($("#music-status"),
      `完成：${out.duration_s != null ? out.duration_s.toFixed(1) + "s 音频，" : ""}用时 ${(out.duration_ms / 1000).toFixed(0)}s` +
      (out.cost_real != null ? `，实收 $${Number(out.cost_real).toFixed(4)}` : ""));
  } catch (err) {
    setStatus($("#music-status"), `生成失败：${err.message}`, true);
  } finally {
    btn.disabled = false;
  }
});

function renderMusicResult(s) {
  $("#music-result-card").hidden = false;
  const bits = [];
  if (s.model) bits.push(String(s.model));
  if (s.seed_sent != null) bits.push(`seed ${String(s.seed_sent)}`);
  if (s.ext) bits.push(String(s.ext));
  $("#music-result-summary").textContent = bits.join(" · ");
  const player = $("#music-result-player");
  const audioUrl = `/music/${encodeURIComponent(s.run_id)}/audio`;
  player.src = audioUrl;
  const text = (s.text_content || "").trim() || (s.transcript || "").trim();
  const textEl = $("#music-result-text");
  if (text) {
    textEl.hidden = false;
    textEl.textContent = text;
  } else {
    textEl.hidden = true;
    textEl.textContent = "";
  }
  const meta = [];
  if (s.duration_s != null) meta.push(`${s.duration_s.toFixed(1)}s`);
  if (s.size_bytes) meta.push(`${(s.size_bytes / 1024).toFixed(0)} KB`);
  if (s.cost_real != null) meta.push(`实收 $${Number(s.cost_real).toFixed(4)}`);
  else if (s.cost != null) meta.push(`预计 $${Number(s.cost).toFixed(4)}`);
  if (s.duration_ms) meta.push(`用时 ${(s.duration_ms / 1000).toFixed(0)}s`);
  $("#music-result-meta").textContent = meta.join(" · ");
  const dl = $("#music-dl-audio");
  dl.hidden = false;
  dl.href = audioUrl;
  dl.download = `${s.run_id}.${s.ext || "mp3"}`;
  const mf = $("#music-dl-manifest");
  mf.hidden = false;
  mf.href = `/music/${encodeURIComponent(s.run_id)}/manifest`;
}

/* ---------- 最近生成（分页） ---------- */
const MUSIC_RECENT_PAGE_SIZE = 24;
const musicRecentState = { page: 0, seq: 0 };

async function loadMusicRecent(page = musicRecentState.page) {
  const seq = ++musicRecentState.seq; // 快速连点/并发的旧响应不回写新页
  musicRecentState.page = Math.max(0, page);
  const box = $("#music-recent");
  try {
    const offset = musicRecentState.page * MUSIC_RECENT_PAGE_SIZE;
    const { items, total } = await apiPaged(
      `/music?limit=${MUSIC_RECENT_PAGE_SIZE}&offset=${offset}`
    );
    if (seq !== musicRecentState.seq) return;
    box.innerHTML = "";
    if (!items.length) {
      if (musicRecentState.page > 0) {
        // 过了末页（服务端记录被删过）：留在原页位，上一页可退回
        box.innerHTML = `<span class="hint">这一页没有记录</span>`;
        refreshPager(box, "music-recent-pager", {
          page: musicRecentState.page,
          pageSize: MUSIC_RECENT_PAGE_SIZE,
          total,
          goto: loadMusicRecent,
        });
      } else {
        // 全库为空：老文案 + 不出分页条
        box.innerHTML = `<span class="hint">还没有音乐记录</span>`;
        document.getElementById("music-recent-pager")?.remove();
      }
      return;
    }
    for (const rec of items) {
      box.appendChild(buildMusicRecentCard(rec));
    }
    refreshPager(box, "music-recent-pager", {
      page: musicRecentState.page,
      pageSize: MUSIC_RECENT_PAGE_SIZE,
      total,
      goto: loadMusicRecent,
    });
  } catch (err) {
    box.innerHTML = `<span class="hint">加载失败：${escapeHtml(err.message)}</span>`;
  }
}

const MUSIC_STATUS_LABELS = {
  ready: "完成",
  failed: "失败",
  in_progress: "生成中",
};

function buildMusicRecentCard(rec) {
  const card = document.createElement("div");
  card.className = "image-card tts-card";
  card.dataset.runId = rec.run_id;
  const audio = document.createElement("audio");
  audio.controls = true;
  audio.preload = "none";
  if (rec.has_audio) {
    audio.src = `/music/${encodeURIComponent(rec.run_id)}/audio`;
    card.appendChild(audio);
  } else {
    card.insertAdjacentHTML(
      "beforeend",
      `<div class="image-placeholder">${escapeHtml(MUSIC_STATUS_LABELS[rec.status] || rec.status)}</div>`
    );
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
    const textBtn = document.createElement("button");
    textBtn.textContent = "文本";
    textBtn.addEventListener("click", () =>
      Media.showTextModal("音乐 · 提示词", rec.prompt || "", { copyable: true }));
    actions.appendChild(textBtn);
  }
  if (rec.has_audio) {
    const playBtn = document.createElement("button");
    playBtn.className = "btn-icon";
    playBtn.title = "试放";
    playBtn.setAttribute("aria-label", "试放");
    playBtn.innerHTML = icon("play");
    playBtn.addEventListener("click", () => audio.play().catch(() => {}));
    actions.appendChild(playBtn);
    const dl = document.createElement("a");
    dl.className = "btn btn-icon";
    dl.href = `/music/${encodeURIComponent(rec.run_id)}/audio`;
    dl.download = `${rec.run_id}.mp3`;
    dl.title = "下载音频";
    dl.setAttribute("aria-label", "下载音频");
    dl.innerHTML = icon("download");
    actions.appendChild(dl);
  }
  const reuseBtn = document.createElement("button");
  reuseBtn.textContent = "复用";
  reuseBtn.addEventListener("click", () => {
    $("#music-prompt").value = rec.prompt || "";
    window.scrollTo({ top: 0, behavior: "smooth" });
  });
  actions.appendChild(reuseBtn);
  const delBtn = document.createElement("button");
  delBtn.className = "btn-icon danger";
  delBtn.title = "删除";
  delBtn.setAttribute("aria-label", "删除");
  delBtn.innerHTML = icon("trash");
  delBtn.addEventListener("click", async () => {
    if (!(await Media.showConfirm({
      title: "删除记录",
      message: `删除这条音乐记录（${rec.run_id}）？音频与 manifest 一并删除，不可恢复。`,
    }))) return;
    try {
      await api(`/music/${encodeURIComponent(rec.run_id)}`, { method: "DELETE" });
      setStatus($("#music-status"), `已删除：${rec.run_id}`);
      loadMusicRecent(musicRecentState.page);
    } catch (err) {
      setStatus($("#music-status"), `删除失败：${err.message}`, true);
    }
  });
  actions.appendChild(delBtn);
  card.appendChild(actions);
  const note = document.createElement("div");
  note.className = "image-meta";
  note.innerHTML = `<span>${escapeHtml([
    rec.duration_s != null ? rec.duration_s + "s" : "",
    rec.cost_real != null ? `$${Number(rec.cost_real).toFixed(4)}` : "",
    MUSIC_STATUS_LABELS[rec.status] || rec.status,
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

$("#btn-music-recent-refresh").addEventListener("click", () => loadMusicRecent());

/* ---------- 初始化 ---------- */

function initMusic() {
  loadMusicCatalog().catch(() => {});
  loadMusicRecent().catch(() => {});
}

initMusic();
