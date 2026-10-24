/* 视频配方页：配方列表 / 详情 / 运行与检查点继续。
   依赖全局：api / $ / $$ / escapeHtml / Media.showPanelModal / renderMarkdown（app.js / media.js）。

   数据契约：
   GET /recipes → [{name,title,category,steps:[{index,label,kind,manual}],error?}]
   GET /recipes/{name} → { name,title,category,doc,steps:[{index,label,kind,manual,...}] }
   POST /recipes/{name}/run {seed?} → RecipeRunState
   GET  /recipe-runs → [{recipe_run_id, recipe, title, seed, status, created_at}]
   GET  /recipe-runs/{id} → RecipeRunState {steps:[{label,kind,status,note,outputs}]}
   POST /recipe-runs/{id}/continue

   检查点语义：waiting = 停在 pending 的 manual 步；continue = 批准并执行当前 manual 步，
   之后的 manual 再停；failed 步 continue 直接重试。 */

let recipeTabState = { recipes: [], currentRecipe: null };

function recipeCategoryText(category) {
  return category === "model" ? "生成配方" : category === "synthesis" ? "合成配方" : "未知";
}

function recipeStatusText(status) {
  const map = { waiting: "待继续（检查点）", running: "执行中", done: "完成", failed: "失败（continue 重试）" };
  return map[status] || status;
}

function recipeStepMark(status) {
  const marks = { done: "✓", failed: "✗", pending: "·" };
  return `${marks[status] || "?"} ${status}`;
}

/* ---------- 配方列表 ---------- */
async function loadRecipes() {
  const data = await api("/recipes");
  recipeTabState.recipes = data;
  const tbody = $("#recipes-table tbody");
  tbody.innerHTML = "";
  for (const r of data) {
    const tr = document.createElement("tr");
    if (r.error) {
      tr.innerHTML = `<td class="prompt">${escapeHtml(r.name)}</td><td>-</td><td>—</td>` +
        `<td class="prompt" style="color:#c00">配置错误：${escapeHtml(r.error)}</td><td></td>`;
      tbody.appendChild(tr);
      continue;
    }
    const chain = (r.steps || []).map((s) =>
      `${s.label ? escapeHtml(s.label) : s.index}（${s.kind}）` + (s.manual ? "［manual］" : "")
    ).join(" → ");
    tr.innerHTML = `<td class="prompt" title="${escapeHtml(r.name)}">${escapeHtml(r.name)}</td>` +
      `<td>${escapeHtml(r.title || "-")}</td>` +
      `<td>${recipeCategoryText(r.category)}</td>` +
      `<td class="prompt">${chain}</td>` +
      `<td><div class="row-actions"><button data-detail="${escapeHtml(r.name)}">详情</button></div></td>`;
    tbody.appendChild(tr);
  }
  tbody.querySelectorAll("button[data-detail]").forEach((btn) =>
    btn.addEventListener("click", () => showRecipe(btn.dataset.detail).catch((err) => alert(err.message))));
}

/* ---------- 配方详情（步骤 chips + 文档） ---------- */
async function showRecipe(name) {
  const d = await api(`/recipes/${encodeURIComponent(name)}`);
  recipeTabState.currentRecipe = name;
  $("#recipe-detail-card").hidden = false;
  $("#recipe-detail-title").textContent = `${d.name} · ${d.title || ""}`;
  $("#recipe-run-seed").placeholder = d.seed ? `配方默认（${d.seed}）` : "配方默认";
  const chips = $("#recipe-steps");
  chips.innerHTML = "";
  for (const s of d.steps) {
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.textContent = `${s.label}（${s.kind}${s.manual ? " · manual" : " · auto"}）`;
    chips.appendChild(chip);
}
}

/* ---------- 开始运行 ---------- */
async function runRecipe(name, seedFromInput) {
  const seed = seedFromInput !== "" && Number.isFinite(Number(seedFromInput))
    ? Math.floor(Number(seedFromInput)) : null;
  const state = await api(`/recipes/${encodeURIComponent(name)}/run`, {
    method: "POST",
    body: JSON.stringify({ seed }),
  });
  await loadRecipeRuns();
  return state;
}

/* ---------- 配方运行列表 ---------- */
async function loadRecipeRuns() {
  const rows = await api("/recipe-runs");
  const tbody = $("#recipe-runs-table tbody");
  tbody.innerHTML = "";
  for (const r of rows) {
    const tr = document.createElement("tr");
    const controllable = r.status === "waiting" || r.status === "failed";
    tr.innerHTML = `<td class="prompt" title="${escapeHtml(r.recipe_run_id)}">${escapeHtml(r.recipe_run_id)}</td>` +
      `<td>${escapeHtml(r.recipe)}</td>` +
      `<td>${recipeStatusText(r.status)}</td>` +
      `<td>${r.seed}</td><td>${escapeHtml(r.created_at || "")}</td>` +
      `<td><div class="row-actions">` +
      `<button data-view="${escapeHtml(r.recipe_run_id)}">查看</button>` +
      (controllable ? `<button data-cont="${escapeHtml(r.recipe_run_id)}">继续</button>` : "") +
      `</div></td>`;
    tbody.appendChild(tr);
  }
  tbody.querySelectorAll("button[data-view]").forEach((btn) =>
    btn.addEventListener("click", () => showRecipeRun(btn.dataset.view)));
  tbody.querySelectorAll("button[data-cont]").forEach((btn) =>
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      btn.textContent = "继续中…";
      try {
        const state = await api(`/recipe-runs/${encodeURIComponent(btn.dataset.cont)}/continue`, { method: "POST" });
        await loadRecipeRuns();
        showRecipeRun(state.recipe_run_id);
      } catch (err) {
        alert("继续失败：" + err.message);
        btn.disabled = false;
        btn.textContent = "继续";
      }
    }));
}

/* ---------- 运行详情弹层（步骤状态 + 继续 + 成片预览） ---------- */
function showRecipeRun(runId) {
  Media.showPanelModal(`配方运行 ${runId}`, (body, close) => {
    api(`/recipe-runs/${encodeURIComponent(runId)}`).then((state) => {
      body.innerHTML = "";
      for (const st of state.steps) {
        const row = document.createElement("div");
        row.className = "recipe-step-row";
        const mark = st.status === "done" ? "✓" : st.status === "failed" ? "✗" : "·";
        row.innerHTML = `<strong>${mark} ${escapeHtml(st.label || "#" + st.index)}</strong>` +
          `<span>（${KIND_LABEL(st.kind)}）</span>` +
          `<span>${recipeStepMark(st.status)}</span>` +
          (st.note ? `<div class="hint">${escapeHtml(st.note)}</div>` : "");
        body.appendChild(row);
      }
      const modalActions = document.createElement("div");
      modalActions.className = "actions";
      if (state.status === "waiting" || state.status === "failed") {
        const contBtn = document.createElement("button");
        contBtn.className = "primary";
        contBtn.textContent = "继续（批准当前检查点）";
        contBtn.addEventListener("click", async () => {
          contBtn.disabled = true;
          try {
            await api(`/recipe-runs/${encodeURIComponent(runId)}/continue`, { method: "POST" });
            await loadRecipeRuns();
            close();
            showRecipeRun(runId);
          } catch (error) {
            contBtn.textContent = "继续失败：" + error.message;
          }
        });
        modalActions.appendChild(contBtn);
      } else if (state.status === "done") {
        const vid = state.steps.find((s) => s.outputs && s.outputs.video && s.outputs.video.name);
        if (vid && vid.outputs.run_id) {
          const url = `/runs/${encodeURIComponent(vid.outputs.run_id)}/assets/videos/${encodeURIComponent(vid.outputs.video.name)}`;
          const player = document.createElement("video");
          player.controls = true;
          player.muted = true;
          player.preload = "metadata";
          player.src = url;
          body.appendChild(player);
          const dl = document.createElement("a");
          dl.className = "btn";
          dl.href = url;
          dl.download = vid.outputs.video.name;
          dl.textContent = "下载成片";
          body.appendChild(dl);
        }
      }
      body.appendChild(modalActions);
    });
  });
}

function KIND_LABEL(kind) {
  const map = { images: "出图", overlay: "贴字", video_synth: "本地合成", video_model: "模型出片" };
  return map[kind] || kind;
}

/* ---------- 顶栏按钮绑定（DOMContentLoaded 一次） ---------- */
document.addEventListener("DOMContentLoaded", () => {
  $("#btn-recipes-refresh").addEventListener("click", loadRecipes);
  $("#btn-recipe-runs-refresh").addEventListener("click", loadRecipeRuns);
  $("#btn-recipe-run").addEventListener("click", async () => {
    const name = recipeTabState.currentRecipe;
    if (!name) return;
    const btn = $("#btn-recipe-run");
    btn.disabled = true;
    try {
      const state = await runRecipe(name, $("#recipe-run-seed").value.trim());
      await loadRecipeRuns();
      showRecipeRun(state.recipe_run_id);
    } catch (err) {
      alert("运行失败：" + err.message);
    } finally {
      btn.disabled = false;
    }
  });
  $("#btn-recipe-doc").addEventListener("click", async () => {
    if (!recipeTabState.currentRecipe) return;
    try {
      const d = await api(`/recipes/${encodeURIComponent(recipeTabState.currentRecipe)}`);
      const box = $("#recipe-doc");
      box.hidden = !box.hidden;
      if (!box.hidden) box.innerHTML = renderMarkdown(d.doc || "（配方还没有文档：presets/recipes/<name>.md）");
    } catch (err) {
      alert(err.message);
    }
  });
});
