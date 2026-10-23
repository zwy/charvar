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
      const actionsHtml = promptText
        ? `<button class="btn-sm" data-prompt-view>查看提示词</button><button class="btn-sm" data-prompt-copy>复制</button><button class="btn-sm" data-regen title="同一提示词换个随机种子，按原出图设置重推 ComfyUI 出一张新图">重新生成</button>`
        : `<span class="hint">（无提示词记录）</span>`;
      if (imageSrc) {
        card.innerHTML = `<img src="${imageSrc}" alt="${item.variant_id || `图片 ${item.item_index}`}" loading="lazy"><div class="image-meta"><strong>#${String(item.item_index).padStart(3, "0")}</strong><span>${item.variant_id}</span></div><div class="image-actions">${actionsHtml}</div>`;
        card.querySelector("img").addEventListener("click", () =>
          this.showImageModal(item.original_url || item.primary_url, item.variant_id));
      } else {
        card.innerHTML = `<div class="image-placeholder"><strong>#${String(item.item_index).padStart(3, "0")}</strong><span>${item.variant_id}</span><span>${item.status}</span></div><div class="image-actions">${actionsHtml}</div>`;
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
document.addEventListener("DOMContentLoaded", () => {
  $("#btn-view-images").addEventListener("click", () => Media.setView("images"));
  $("#btn-view-table").addEventListener("click", () => Media.setView("table"));
  $("#btn-contact-sheet").addEventListener("click", async () => {
    const runId = Media.currentRunId;
    if (!runId) return;
    Media.setStatus("生成 contact sheet…");
    try {
      await api(`/runs/${encodeURIComponent(runId)}/contact-sheet`, {
        method: "POST",
        body: JSON.stringify({ mode: "contact" }),
      });
      await Media.loadImages(runId);
      Media.setStatus("contact sheet 已生成");
    } catch (error) {
      Media.setStatus(`contact sheet 失败：${error.message}`, true);
    }
  });
  $("#btn-sync-images").addEventListener("click", async () => {
    const runId = Media.currentRunId;
    if (!runId) return;
    Media.setStatus("同步中…");
    try {
      await Media.syncImages(runId);
      const data = Media.currentData;
      Media.setStatus(`图片 ${data.completed}/${data.items.length} 完成${data.errors ? `，失败 ${data.errors}` : ""}`);
    } catch (error) {
      Media.setStatus(`同步失败：${error.message}`, true);
    }
  });
  $("#btn-video").addEventListener("click", () => {
    const runId = Media.currentRunId;
    if (!runId) return;
    Media.showPanelModal("合成视频", async (body, close) => {
      let presets = [];
      try {
        presets = await api("/video-presets");
      } catch (_err) {
        presets = [];
      }
      if (!presets.length) {
        const empty = document.createElement("p");
        empty.className = "hint";
        empty.textContent = "还没有视频预设：在 presets/video/ 放一个 YAML（如 q21_turnaround.yaml）后再试。";
        body.appendChild(empty);
        return;
      }
      const label = document.createElement("label");
      label.innerHTML = `视频预设<select id="video-preset-select"></select>`;
      const select = label.querySelector("select");
      for (const p of presets) {
        const opt = document.createElement("option");
        opt.value = p.name;
        opt.textContent = p.name;
        select.appendChild(opt);
      }
      body.appendChild(label);
      const musicLabel = document.createElement("label");
      musicLabel.innerHTML = `背景音乐<select id="video-music-select"></select>`;
      const musicSel = musicLabel.querySelector("select");
      body.appendChild(musicLabel);
      let musicFiles = [];
      try {
        musicFiles = ((await api("/video-music")) || {}).files || [];
      } catch (_err) {
        musicFiles = [];
      }
      const defOpt = document.createElement("option");
      defOpt.value = "";
      musicSel.appendChild(defOpt);
      const noneOpt = document.createElement("option");
      noneOpt.value = "none";
      noneOpt.textContent = "不加音乐";
      musicSel.appendChild(noneOpt);
      for (const f of musicFiles) {
        const opt = document.createElement("option");
        opt.value = f;
        opt.textContent = f;
        musicSel.appendChild(opt);
      }
      const refreshDefaultOpt = () => {
        const preset = presets.find((x) => x.name === select.value);
        defOpt.textContent = `用预设默认（${preset && preset.music ? preset.music : "无"}）`;
      };
      const musicName = () => {
        if (musicSel.value === "none") return null;
        if (musicSel.value) return musicSel.value;
        const preset = presets.find((x) => x.name === select.value);
        return preset && preset.music ? preset.music : null;
      };
      const hint = document.createElement("div");
      hint.className = "hint";
      body.appendChild(hint);
      const updateHint = () => {
        const preset = presets.find((x) => x.name === select.value);
        if (!preset) return;
        refreshDefaultOpt();
        const count = Media.currentData ? Media.currentData.completed : 0;
        const total = (preset.include_contact_sheet ? preset.contact_sheet_seconds : 0) + count * preset.seconds_per_image;
        // size 为 null = aspect auto：渲染时按本组图片实际比例就近选画幅
        const sizeText = preset.size
          ? `${preset.size[0]}×${preset.size[1]}`
          : "比例自适应（按本组图片就近选画幅，黑边补底）";
        hint.textContent = [
          `${sizeText} · ${preset.fps}fps`,
          `每张 ${preset.seconds_per_image}s`,
          preset.include_contact_sheet ? `封面 contact sheet ${preset.contact_sheet_seconds}s` : null,
          `背景音乐：${musicName() || "无"}`,
          `预计约 ${Math.round(total)} 秒（按已生成 ${count} 张）`,
        ].filter((part) => part).join(" · ");
      };
      select.addEventListener("change", updateHint);
      musicSel.addEventListener("change", updateHint);
      refreshDefaultOpt();
      updateHint();
      const actions = document.createElement("div");
      actions.className = "actions";
      const btn = document.createElement("button");
      btn.className = "primary";
      btn.textContent = "开始合成";
      const status = document.createElement("span");
      status.className = "status";
      actions.appendChild(btn);
      actions.appendChild(status);
      body.appendChild(actions);
      const resultBox = document.createElement("div");
      resultBox.className = "video-result"; // 视频 + 下载按钮纵向排列（带间距，按钮不拉伸）
      body.appendChild(resultBox);
      btn.addEventListener("click", async () => {
        btn.disabled = true;
        status.classList.remove("error");
        status.textContent = "ffmpeg 合成中…（30 张图大约十几秒到一分钟）";
        try {
          const info = await api(`/runs/${encodeURIComponent(runId)}/video`, {
            method: "POST",
            body: JSON.stringify({ preset: select.value, music: musicSel.value || null }),
          });
          status.classList.remove("error");
          status.textContent = "完成：" + [
            info.size[0] + "×" + info.size[1],
            Math.round(info.duration) + "s",
            info.music ? "音乐 " + info.music : "无音乐",
            info.skipped && info.skipped.length ? "跳过未生成 " + info.skipped.length + " 张" : null,
          ].filter(Boolean).join(" · ");
          resultBox.innerHTML = "";
          const vid = document.createElement("video");
          vid.controls = true;
          vid.autoplay = true;
          vid.muted = true;
          vid.preload = "metadata";
          vid.src = info.url;
          resultBox.appendChild(vid);
          const dl = document.createElement("a");
          dl.className = "btn";
          dl.href = info.url;
          dl.download = info.name;
          dl.textContent = "下载 MP4";
          resultBox.appendChild(dl);
          await Media.loadImages(runId);
        } catch (error) {
          status.classList.add("error");
          status.textContent = "合成失败：" + error.message;
          btn.disabled = false;
        }
      });
    });
  });
});
