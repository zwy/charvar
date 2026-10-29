/* 图片墙 / contact sheet / 大图查看 / 弹层面板 / markdown 展示。 */

/* 复制文本：clipboard API 优先，execCommand 兜底（返回是否成功）。 */
async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch (err) {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try {
      ok = document.execCommand("copy");
    } catch (_err) {
      ok = false;
    }
    ta.remove();
    return ok;
  }
}

/* 轻量 markdown 渲染（转义后输出，不支持任意 HTML 注入）：
   标题 / hr / 引用 / 围栏代码 / 表格 / 列表 / 段落，行内 **加粗** `code` [链接](url)。 */
function renderMarkdown(src) {
  const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const inline = (s) => esc(s)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\*([^*]+)\*/g, "<em>$1</em>")
    .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
  const lines = String(src || "").replace(/\r\n/g, "\n").split("\n");
  const out = [];
  let buf = [];
  const flush = () => {
    if (buf.length) out.push(`<p>${buf.map(inline).join("<br>")}</p>`);
    buf = [];
  };
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (/^\s*```/.test(line)) {
      flush();
      const body = [];
      i += 1;
      while (i < lines.length && !/^\s*```/.test(lines[i])) {
        body.push(lines[i]);
        i += 1;
      }
      i += 1; // 收尾围栏
      out.push(`<pre>${esc(body.join("\n"))}</pre>`);
      continue;
    }
    const heading = line.match(/^(#{1,6})\s+(.*)$/);
    if (heading) {
      flush();
      const level = heading[1].length;
      out.push(`<h${level}>${inline(heading[2])}</h${level}>`);
      i += 1;
      continue;
    }
    if (/^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(line)) {
      flush();
      out.push("<hr>");
      i += 1;
      continue;
    }
    if (/^\s*>\s?/.test(line)) {
      flush();
      const body = [];
      while (i < lines.length && /^\s*>\s?/.test(lines[i])) {
        body.push(lines[i].replace(/^\s*>\s?/, ""));
        i += 1;
      }
      out.push(`<blockquote>${body.map(inline).join("<br>")}</blockquote>`);
      continue;
    }
    if (/^\s*\|.*\|\s*$/.test(line) && /^\s*\|[\s:|-]+\|\s*$/.test(lines[i + 1] || "")) {
      flush();
      const rows = [];
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) {
        rows.push(lines[i]);
        i += 1;
      }
      const cells = (row) => row.trim().replace(/^\||\|$/g, "").split("|").map((c) => inline(c.trim()));
      const [head, , ...rest] = rows;
      out.push(
        "<table><thead><tr>" + cells(head).map((c) => `<th>${c}</th>`).join("") + "</tr></thead><tbody>"
        + rest.map((r) => "<tr>" + cells(r).map((c) => `<td>${c}</td>`).join("") + "</tr>").join("")
        + "</tbody></table>"
      );
      continue;
    }
    if (/^\s*(?:[-*+]|\d+[.)])\s+/.test(line)) {
      flush();
      const ordered = /^\s*\d/.test(line);
      const items = [];
      while (i < lines.length) {
        const m = lines[i].match(/^\s*(?:[-*+]|\d+[.)])\s+(.*)$/);
        if (!m) break;
        items.push(`<li>${inline(m[1])}</li>`);
        i += 1;
      }
      out.push(ordered ? `<ol>${items.join("")}</ol>` : `<ul>${items.join("")}</ul>`);
      continue;
    }
    if (!line.trim()) {
      flush();
      i += 1;
      continue;
    }
    buf.push(line.trim());
    i += 1;
  }
  flush();
  return out.join("\n");
}

const Media = {
  currentRunId: null,
  currentData: null,
  view: "images",

  async loadImages(runId) {
    this.currentRunId = runId;
    const data = await api(`/runs/${encodeURIComponent(runId)}/images`);
    this.currentData = data;
    this.renderImages(data);
    this.setStatus(`图片 ${data.completed}/${data.items.length} 完成${data.errors ? `，失败 ${data.errors}` : ""}`);
    // 生成页轮次条同步计数（app.js 提供，media.js 先加载、运行时才有）
    if (typeof updateRoundStatus === "function") updateRoundStatus(data);
    return data;
  },

  async syncImages(runId) {
    await api(`/runs/${encodeURIComponent(runId)}/sync-outputs`, {
      method: "POST",
      body: JSON.stringify({ build_sheets: true }),
    });
    await this.loadImages(runId);
    return this.currentData;
  },

  renderImages(data) {
    const grid = $("#result-images");
    if (!grid || !data) return;
    grid.innerHTML = "";
    if (data.contact_sheet) {
      const card = document.createElement("div");
      card.className = "image-card sheet-card";
      const sheetImageSrc = `/runs/${encodeURIComponent(data.run_id)}/sheets/contact`;
      card.innerHTML = `<img src="${sheetImageSrc}" loading="lazy" alt="contact sheet"><div class="image-meta"><span>Contact sheet</span><button class="btn-sm" data-sheet>查看</button></div>`;
      card.querySelector("img").addEventListener("click", () =>
        this.showImageModal(sheetImageSrc, "Contact sheet"));
      card.querySelector("button[data-sheet]").addEventListener("click", () =>
        this.showImageModal(`/runs/${encodeURIComponent(data.run_id)}/sheets/contact`, "Contact sheet"));
      grid.appendChild(card);
    }
    for (const item of data.items) {
      const card = document.createElement("div");
      card.className = "image-card";
      const imageSrc = item.thumb_url || item.primary_url;
      const promptText = String(item.text || "").trim();
      // 查看提示词 / 复制 / 重新生成（与档案图片槽位同一套弹层：可滚动 pre + 复制全文）
      let actionsHtml = promptText
        ? `<button class="btn-sm" data-prompt-view>查看提示词</button><button class="btn-sm" data-prompt-copy>复制</button><button class="btn-sm" data-regen title="同一提示词换个随机种子，按原出图设置重推 ComfyUI 出一张新图">重新生成</button>`
        : `<span class="hint">（无提示词记录）</span>`;
      if (item.labeled_url) {
        actionsHtml =
          `<button class="btn-sm" data-labeled title="贴字后的成片（labeled/，原图不动）">看贴字图</button>` +
          actionsHtml;
      }
      if (imageSrc) {
        card.innerHTML = `<img src="${imageSrc}" alt="${item.variant_id || `图片 ${item.item_index}`}" loading="lazy"><div class="image-meta"><strong>#${String(item.item_index).padStart(3, "0")}</strong><span>${item.variant_id}</span></div><div class="image-actions">${actionsHtml}</div>`;
        card.querySelector("img").addEventListener("click", () =>
          this.showImageModal(item.original_url || item.primary_url, item.variant_id));
      } else {
        card.innerHTML = `<div class="image-placeholder"><strong>#${String(item.item_index).padStart(3, "0")}</strong><span>${item.variant_id}</span><span>${item.status}</span></div><div class="image-actions">${actionsHtml}</div>`;
      }
      const labeledBtn = card.querySelector("[data-labeled]");
      if (labeledBtn) {
        labeledBtn.addEventListener("click", () =>
          this.showImageModal(item.labeled_url, `#${String(item.item_index).padStart(3, "0")} ${item.variant_id} · 贴字`));
      }
      const viewBtn = card.querySelector("[data-prompt-view]");
      if (viewBtn) {
        viewBtn.addEventListener("click", () =>
          this.showTextModal(`#${String(item.item_index).padStart(3, "0")} ${item.variant_id} · 提示词`, promptText, { copyable: true }));
      }
      const copyBtn = card.querySelector("[data-prompt-copy]");
      if (copyBtn) {
        copyBtn.addEventListener("click", async () => {
          const ok = await copyText(promptText);
          copyBtn.textContent = ok ? "已复制 ✓" : "复制失败";
          setTimeout(() => { copyBtn.textContent = "复制"; }, 1500);
        });
      }
      const regenBtn = card.querySelector("[data-regen]");
      if (regenBtn) {
        regenBtn.addEventListener("click", async () => {
          const label = `#${String(item.item_index).padStart(3, "0")}`;
          // 与「生成」按钮同一约定：点按钮时随机取一个采样种子（0–100000）；
          // 提示词/出图设置不变，由后端重放上批入队快照。
          const seed = Math.floor(Math.random() * 100001);
          regenBtn.disabled = true;
          regenBtn.textContent = "生成中…";
          Media.setStatus(`${label} 重新生成中…（种子 ${seed}）`);
          const runId = data.run_id;
          let out;
          try {
            out = await api(`/runs/${encodeURIComponent(runId)}/regenerate`, {
              method: "POST",
              body: JSON.stringify({ item_index: item.item_index, seed }),
            });
          } catch (err) {
            Media.setStatus(`${label} 重新生成失败：${err.message}`, true);
            regenBtn.disabled = false;
            regenBtn.textContent = "重新生成";
            return;
          }
          if (!out.ok) {
            // 已入队但等待超时：稍后同步取图，按钮恢复可点
            Media.setStatus(`${label} ${out.error || "已入队但出图超时"}`, true);
            regenBtn.disabled = false;
            regenBtn.textContent = "重新生成";
            return;
          }
          try {
            // wait=true 时服务端已等到该条出图；同步覆盖下载新图 + 重建 contact sheet + 刷墙
            await Media.syncImages(runId);
            Media.setStatus(`${label} 已重新生成（种子 ${out.seed}）`);
          } catch (err) {
            Media.setStatus(`${label} 已入队，同步失败：${err.message}（稍后点「同步图片」）`, true);
          }
        });
      }
      grid.appendChild(card);
    }
    for (const vid of data.videos || []) {
      const card = document.createElement("div");
      card.className = "image-card video-card";
      card.innerHTML = `<video controls preload="metadata" src="${vid.url}"></video><div class="image-meta"><strong>合成视频</strong><span>${vid.name}</span></div><div class="image-actions"><a class="btn" href="${vid.url}" download>下载 MP4</a></div>`;
      grid.appendChild(card);
    }
  },

  /* 通用弹层：标题 + 自定义内容（Esc / 点遮罩 / 关闭按钮退出）。build(body, close)。 */
  showPanelModal(title, build) {
    const overlay = document.createElement("div");
    const close = () => {
      document.removeEventListener("keydown", handleKey);
      document.body.classList.remove("modal-open");
      overlay.remove();
    };
    const handleKey = (event) => {
      if (event.key === "Escape") close();
    };
    overlay.className = "image-overlay";
    overlay.setAttribute("role", "dialog");
    overlay.setAttribute("aria-modal", "true");
    overlay.innerHTML = `<div class="image-modal prompt-modal"><div class="modal-head"><strong></strong><button class="chip" data-close>关闭</button></div><div class="modal-body"></div></div>`;
    overlay.querySelector(".modal-head strong").textContent = title || "";
    overlay.querySelector("[data-close]").addEventListener("click", close);
    overlay.addEventListener("click", (event) => { if (event.target === overlay) close(); });
    document.addEventListener("keydown", handleKey);
    document.body.classList.add("modal-open");
    build(overlay.querySelector(".modal-body"), close);
    document.body.appendChild(overlay);
    overlay.querySelector("[data-close]")?.focus();
  },

  /* 只读文本弹层（长提示词查看）；copyable 时附一个复制按钮。 */
  showTextModal(title, text, { copyable = false } = {}) {
    this.showPanelModal(title, (body) => {
      const pre = document.createElement("pre");
      pre.className = "prompt-view";
      pre.textContent = text || "";
      body.appendChild(pre);
      if (copyable) {
        const btn = document.createElement("button");
        btn.className = "btn-sm";
        btn.textContent = "复制全文";
        btn.addEventListener("click", async () => {
          btn.textContent = (await copyText(text)) ? "已复制 ✓" : "复制失败";
          setTimeout(() => { btn.textContent = "复制全文"; }, 1500);
        });
        body.appendChild(btn);
      }
    });
  },

  showImageModal(url, caption) {
    if (!url) return;
    const overlay = document.createElement("div");
    const close = () => {
      document.removeEventListener("keydown", handleKey);
      document.body.classList.remove("modal-open");
      overlay.remove();
    };
    const handleKey = (event) => {
      if (event.key === "Escape") close();
    };
    overlay.className = "image-overlay";
    overlay.setAttribute("role", "dialog");
    overlay.setAttribute("aria-modal", "true");
    // 与 showPanelModal 同一骨架：modal-head（关闭钮靠右）+ modal-body（图片 + 可选图注）
    overlay.innerHTML = `<div class="image-modal"><div class="modal-head"><strong></strong><button class="btn-sm" data-close>关闭</button></div><div class="modal-body"><img alt="生成结果"></div></div>`;
    if (caption) {
      const pre = document.createElement("pre");
      pre.textContent = caption;
      overlay.querySelector(".modal-body").appendChild(pre);
    }
    overlay.querySelector(".modal-body img").src = url;
    overlay.querySelector("[data-close]").addEventListener("click", close);
    overlay.addEventListener("click", (event) => { if (event.target === overlay) close(); });
    document.addEventListener("keydown", handleKey);
    document.body.classList.add("modal-open");
    document.body.appendChild(overlay);
    overlay.querySelector("[data-close]")?.focus();
  },

  /* 「从最近生成」选图：列 Playground 最近的出图记录（只列有图的），点卡片即选定。
     onPick(rec) 收到 /adhoc 列表记录（含 run_id / prompt），由调用方决定装进首帧还是参考图。 */
  pickRecentImage(onPick, { title = "从最近生成选择" } = {}) {
    this.showPanelModal(title, (body, close) => {
      body.innerHTML = `<span class="hint">加载中…</span>`;
      api("/adhoc?limit=24")
        .then((all) => {
          const records = (all || []).filter((r) => r.has_image);
          const grid = document.createElement("div");
          grid.className = "image-grid ref-picker-grid";
          if (!records.length) {
            grid.innerHTML = `<span class="hint">还没有可用的图 —— 先在 Playground 出几张图</span>`;
          }
          for (const rec of records) {
            const card = document.createElement("div");
            card.className = "image-card";
            card.innerHTML =
              `<img src="/adhoc/${encodeURIComponent(rec.run_id)}/thumb" alt="" loading="lazy">` +
              `<div class="image-meta"><span>${escapeHtml(rec.backend || "")}</span>` +
              `<span>${escapeHtml((rec.created_at || "").slice(5, 16))}</span></div>`;
            card.title = rec.prompt || rec.run_id;
            card.addEventListener("click", () => {
              close();
              onPick(rec);
            });
            grid.appendChild(card);
          }
          body.replaceChildren(grid);
        })
        .catch((err) => {
          body.innerHTML = `<span class="hint">加载失败：${escapeHtml(err.message)}</span>`;
        });
    });
  },

  setView(view) {
    this.view = view;
    const grid = $("#result-images");
    const table = $("#result-table-wrap");
    if (!grid || !table) return;
    grid.hidden = view !== "images";
    table.hidden = view !== "table";
    $("#btn-view-images").classList.toggle("active", view === "images");
    $("#btn-view-table").classList.toggle("active", view === "table");
    $("#btn-view-images").setAttribute("aria-pressed", view === "images" ? "true" : "false");
    $("#btn-view-table").setAttribute("aria-pressed", view === "table" ? "true" : "false");
  },
  setStatus(text, isError = false) {
    const el = $("#image-status");
    if (!el) return;
    el.textContent = text;
    el.classList.toggle("error", isError);
  },
};
