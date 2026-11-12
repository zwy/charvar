/* 历史页（/ui/runs）：runs 表的查看/复制/删除。
   真多页版全局依赖：$/api/setStatus/escapeHtml 在 core.js；「查看」= 真跳 /ui/gen?run=<run_id>。 */
async function loadRuns() {
  const runs = await api("/runs?limit=100");
  const tbody = $("#runs-table tbody");
  tbody.innerHTML = "";
  for (const r of runs) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td class="prompt" title="${escapeHtml(r.run_id)}">${escapeHtml(r.run_id)}</td><td>${escapeHtml(r.subject_id)}</td>
      <td>${escapeHtml(r.preset)}</td><td>${r.seed}</td><td>${r.item_count}</td>
      <td>${r.comfy_count}</td><td>${escapeHtml(r.created_at)}</td>
      <td><div class="row-actions"><button class="btn-icon" data-run="${escapeHtml(r.run_id)}" title="查看" aria-label="查看">${icon("eye")}</button>
          <button class="btn-icon" data-copy="${escapeHtml(r.run_id)}" title="复制 ID" aria-label="复制 ID">${icon("copy")}</button>
          <button class="btn-icon danger" data-del="${escapeHtml(r.run_id)}" title="删除" aria-label="删除">${icon("trash")}</button></div></td>`;
    tbody.appendChild(tr);
  }
  tbody.querySelectorAll("button[data-run]").forEach((btn) =>
    btn.addEventListener("click", () => {
      // 真多页：查看 = 真跳预设页，由 ?run= 直达该轮次
      location.href = `/ui/gen?run=${encodeURIComponent(btn.dataset.run)}`;
    })
  );
  tbody.querySelectorAll("button[data-copy]").forEach((btn) =>
    btn.addEventListener("click", () => {
      navigator.clipboard.writeText(btn.dataset.copy);
      btn.innerHTML = icon("check"); // 按钮本身给反馈，1.2s 后还原
      setTimeout(() => { btn.innerHTML = icon("copy"); }, 1200);
    })
  );
  tbody.querySelectorAll("button[data-del]").forEach((btn) =>
    btn.addEventListener("click", async () => {
      if (!(await Media.showConfirm({
        title: "删除记录",
        message: `删除生成记录 ${btn.dataset.del}？历史记录删除，不可恢复。`,
      }))) return;
      await api(`/runs/${encodeURIComponent(btn.dataset.del)}`, { method: "DELETE" });
      loadRuns();
    })
  );
}

$("#btn-runs-refresh").addEventListener("click", loadRuns);

loadRuns().catch((err) => setStatus($("#runs-table"), `加载失败：${err.message}`, true));
