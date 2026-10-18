/* 图片墙 / contact sheet / 大图查看。 */
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
      card.innerHTML = `<img src="${sheetImageSrc}" loading="lazy" alt="contact sheet"><div class="image-meta"><span>Contact sheet</span><button class="chip" data-sheet>查看</button></div>`;
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
      if (imageSrc) {
        card.innerHTML = `<img src="${imageSrc}" alt="${item.variant_id || `图片 ${item.item_index}`}" loading="lazy"><div class="image-meta"><strong>#${String(item.item_index).padStart(3, "0")}</strong><span>${item.variant_id}</span></div>`;
        card.querySelector("img").addEventListener("click", () =>
          this.showImageModal(item.original_url || item.primary_url, item.variant_id));
      } else {
        card.innerHTML = `<div class="image-placeholder"><strong>#${String(item.item_index).padStart(3, "0")}</strong><span>${item.variant_id}</span><span>${item.status}</span></div>`;
      }
      grid.appendChild(card);
    }
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
    overlay.innerHTML = `<div class="image-modal"><button class="chip" data-close>关闭</button><img src="${url}" alt="生成结果"><pre></pre></div>`;
    overlay.querySelector("pre").textContent = caption || "";
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
});
