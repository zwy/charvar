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

/* 状态徽章：全站 badge 同式（done=绿 / waiting=蓝 / failed=红）；title 带完整语义 */
function recipeStatusBadge(status) {
  const map = {
    waiting: ["badge-queued", "待继续"],
    running: ["badge", "执行中"],
    done: ["badge-ready", "完成"],
    failed: ["badge-error", "失败"],
  };
  const [cls, label] = map[status] || ["", status];
  return `<span class="badge ${cls}" title="${escapeHtml(recipeStatusText(status))}">${escapeHtml(label)}</span>`;
}

function recipeStepMark(status) {
  const text = { done: "✓ 已完成", failed: "✗ 失败（继续重试）", pending: "· 未执行" };
  return text[status] || status;
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
      `${s.label ? s.label : s.index}（${s.kind}）` + (s.manual ? "［manual］" : "")
    ).join(" → ");
    tr.innerHTML = `<td class="prompt" title="${escapeHtml(r.name)}">${escapeHtml(r.name)}</td>` +
      `<td>${escapeHtml(r.title || "-")}</td>` +
      `<td>${recipeCategoryText(r.category)}</td>` +
      `<td class="prompt recipe-chain" title="${escapeHtml(chain)}">${escapeHtml(chain)}</td>` +
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
    body: JSON.stringify({ seed, gen: recipeGenPayload() }),
  });
  await loadRecipeRuns();
  return state;
}

/* ---------- 配方运行列表 ---------- */
async function loadRecipeRuns() {
  const rows = await api("/recipe-runs");
  const tbody = $("#recipe-runs-table tbody");
  tbody.innerHTML = "";
  if (!rows.length) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td colspan="6"><span class="hint">还没有配方运行——在上方选配方点「开始运行」</span></td>`;
    tbody.appendChild(tr);
    return;
  }
  for (const r of rows) {
    const tr = document.createElement("tr");
    const controllable = r.status === "waiting" || r.status === "failed";
    tr.innerHTML = `<td class="prompt" title="${escapeHtml(r.recipe_run_id)}">${escapeHtml(r.recipe_run_id)}</td>` +
      `<td>${escapeHtml(r.recipe)}</td>` +
      `<td>${recipeStatusBadge(r.status)}</td>` +
      `<td>${r.seed}</td><td class="created-at">${escapeHtml(r.created_at || "")}</td>` +
      `<td><div class="row-actions">` +
      `<button data-view="${escapeHtml(r.recipe_run_id)}">查看</button>` +
      (controllable ? `<button data-cont="${escapeHtml(r.recipe_run_id)}">继续</button>` : "") +
      `<button class="danger" data-del="${escapeHtml(r.recipe_run_id)}">删除</button>` +
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
  tbody.querySelectorAll("button[data-del]").forEach((btn) =>
    btn.addEventListener("click", async () => {
      if (!confirm(`删除配方运行 ${btn.dataset.del}？`)) return;
      try {
        await api(`/recipe-runs/${encodeURIComponent(btn.dataset.del)}`, { method: "DELETE" });
        loadRecipeRuns();
      } catch (err) {
        alert("删除失败：" + err.message);
      }
    }));
}

/* ---------- 运行详情弹层（步骤状态 + 继续 + 成片预览） ---------- */
function showRecipeRun(runId) {
  Media.showPanelModal(`配方运行 ${runId}`, (body, close) => {
    api(`/recipe-runs/${encodeURIComponent(runId)}`).then((state) => {
      body.innerHTML = "";
      const meta = document.createElement("div");
      meta.className = "hint";
      meta.textContent = `${state.recipe} · 种子 ${state.seed} · ${String(state.created_at || "").replace("T", " ")}` +
        (state.gen ? ` · ${recipeGenSummary(state.gen)}` : "");
      body.appendChild(meta);
      let videoProbe = null;
      for (const st of state.steps) {
        const row = document.createElement("div");
        row.className = "recipe-step-row";
        const mark = st.status === "done" ? "✓" : st.status === "failed" ? "✗" : "·";
        row.innerHTML = `<strong>${mark} ${escapeHtml(st.label || "#" + st.index)}</strong>` +
          `<span>（${KIND_LABEL(st.kind)}）</span>` +
          `<span>${recipeStepMark(st.status)}</span>` +
          (st.note ? `<div class="hint">${escapeHtml(st.note)}</div>` : "");
        body.appendChild(row);
        renderStepOutputs(st, body);
        if (st.kind === "video_model" && st.status !== "done" && (st.outputs || {}).video_run_id && !videoProbe) {
          // 视频作业在 OpenRouter 继续跑（wait_s 超时转 waiting 后 manifest 不自更新）：
          // 开弹层时探一次作业状态，检查点过目就知道是「在跑」还是「已出片」
          const live = document.createElement("div");
          live.className = "hint";
          live.textContent = "视频作业：查询中…";
          body.appendChild(live);
          videoProbe = { el: live, runId: String(st.outputs.video_run_id) };
        }
      }
      if (videoProbe) {
        api(`/videos/${encodeURIComponent(videoProbe.runId)}/status`)
          .then((v) => {
            videoProbe.el.textContent = (
              { ready: "视频作业已出片——点「继续」收片",
                in_progress: "视频作业执行中（一般几分钟）——可待收片后「继续」",
                pending: "视频作业刚提交" }[v.status]
              || (v.status === "failed" ? "视频作业失败——「继续」清掉死作业重新提交" : `视频作业状态：${v.status}`)
            );
          })
          .catch(() => { videoProbe.el.textContent = "视频作业状态查不到（作业可能还在跑）"; });
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
        // 成片预览：video_synth 吃 runs 资产（assets/videos/<name>），video_model 吃
        // adhoc_video 的 /videos/<run>/video；两处都可能有（生成配方里成片 = 最后一步）。
        const vidModel = state.steps.find((s) => s.kind === "video_model" && s.outputs && s.outputs.video_url);
        const vid = state.steps.find((s) => s.outputs && s.outputs.video && s.outputs.video.name);
        const url = vidModel
          ? vidModel.outputs.video_url
          : vid && vid.outputs.run_id
          ? `/runs/${encodeURIComponent(vid.outputs.run_id)}/assets/videos/${encodeURIComponent(vid.outputs.video.name)}`
          : "";
        if (url) {
          const player = document.createElement("video");
          player.controls = true;
          player.muted = true;
          player.preload = "metadata";
          // video_model 步 outputs 自带 poster_url（海报帧）；本地合成的 runs 资产没有 poster 通路
          if (vidModel && vidModel.outputs.poster_url) player.poster = vidModel.outputs.poster_url;
          player.src = url;
          body.appendChild(player);
          const dl = document.createElement("a");
          dl.className = "btn";
          dl.href = url;
          dl.download = vidModel ? vidModel.outputs.video_run_id : vid.outputs.video.name;
          dl.textContent = "下载成片";
          body.appendChild(dl);
        }
      }
      const delBtn = document.createElement("button");
      delBtn.className = "danger";
      delBtn.textContent = "删除此运行";
      delBtn.addEventListener("click", async () => {
        if (!confirm(`删除配方运行 ${runId}？`)) return;
        delBtn.disabled = true;
        try {
          await api(`/recipe-runs/${encodeURIComponent(runId)}`, { method: "DELETE" });
          close();
          loadRecipeRuns();
        } catch (error) {
          delBtn.disabled = false;
          alert("删除失败：" + error.message);
        }
      });
      if (state.status !== "running") modalActions.appendChild(delBtn);
      body.appendChild(modalActions);
    });
  });
}

function KIND_LABEL(kind) {
  const map = {
    images: "出图", adhoc_image: "单图", overlay: "贴字", video_synth: "本地合成", video_model: "模型出片",
    pick: "随机选库", scene_design: "场景设计", combo_image: "组合出图", video_script: "视频脚本",
  };
  return map[kind] || kind;
}

/* ---------- 检查点过目用的产物渲染（按步种给出能看的东西） ---------- */
function renderStepOutputs(st, body) {
  const out = st.outputs || {};
  if (st.kind === "pick" && (out.face || out.pose || out.costume)) {
    const wrap = document.createElement("div");
    wrap.style.cssText = "display:flex;flex-wrap:wrap;gap:12px;margin-top:6px;";
    const cards = [];
    if (out.face) {
      cards.push({
        url: out.face.thumb_url,
        title: out.face.face_id,
        sub: [out.face.section_zh, out.face.hair_zh].filter(Boolean).join(" · "),
      });
    }
    if (out.pose) {
      cards.push({
        url: out.pose.thumb_url,
        title: out.pose.title_zh || out.pose.title || out.pose.pose_id,
        sub: `动作 · ${out.pose.pose_id || ""} · ${out.pose.angle || ""}`,
      });
    }
    if (out.costume) {
      cards.push({
        url: out.costume.thumb_url,
        title: out.costume.label || out.costume.item_id,
        sub: "服饰 · " + (out.costume.ref || out.costume.item_id),
      });
    }
    for (const card of cards) {
      const fig = document.createElement("figure");
      fig.style.cssText = "margin:0;width:88px;";
      if (card.url) {
        const img = document.createElement("img");
        img.src = card.url;
        img.alt = card.title;
        img.loading = "lazy";
        img.style.cssText = "width:88px;height:116px;object-fit:cover;border-radius:6px;display:block;";
        fig.appendChild(img);
      } else {
        // 无图条目（如文本动作）：不出破图，占位块照常给标题
        const ph = document.createElement("div");
        ph.textContent = "无图 · 文本";
        ph.style.cssText =
          "width:88px;height:116px;border:1px dashed var(--line,#ccc);border-radius:6px;" +
          "display:flex;align-items:center;justify-content:center;font-size:11px;color:#888;";
        fig.appendChild(ph);
      }
      const cap = document.createElement("figcaption");
      cap.textContent = card.title;
      cap.style.cssText = "font-size:12px;margin-top:4px;word-break:break-all;";
      fig.appendChild(cap);
      if (card.sub) {
        const sub = document.createElement("div");
        sub.className = "hint";
        sub.textContent = card.sub;
        fig.appendChild(sub);
      }
      wrap.appendChild(fig);
    }
    body.appendChild(wrap);
    const origin = out.pose && out.pose.action_origin;
    if (origin) {
      const note = document.createElement("div");
      note.className = "hint";
      note.textContent = "动作描述：" + (
        { existing: "库内已有", ai_draft: "LLM 起稿（ai_draft 落库，建议抽看）" }[origin] || origin
      );
      body.appendChild(note);
    }
    return;
  }
  if (st.kind === "scene_design" && out.scene_text) {
    const h = document.createElement("div");
    h.className = "hint";
    h.style.cssText = "white-space:pre-wrap;margin-top:6px;";
    h.textContent = "场景：" + out.scene_text;
    body.appendChild(h);
    return;
  }
  if (st.kind === "video_script" && out.script) {
    if (out.clip_type) {
      const h = document.createElement("div");
      h.className = "hint";
      h.textContent = "片段类型：" + out.clip_type;
      body.appendChild(h);
    }
    const pre = document.createElement("pre");
    pre.style.cssText = "white-space:pre-wrap;font-size:12px;margin:4px 0 0;max-height:220px;overflow:auto;";
    pre.textContent = out.script;
    body.appendChild(pre);
    return;
  }
  if (st.kind === "combo_image" && out.image_url) {
    const img = document.createElement("img");
    img.src = out.thumb_url || out.image_url;
    img.alt = "首帧";
    img.loading = "lazy";
    // .modal-body img 的 width:100% 会把 9:16 缩略图横向拉宽 —— 覆盖成含框小图
    img.style.cssText = "display:block;width:auto;max-width:180px;height:auto;max-height:240px;" +
      "object-fit:contain;background:#f4f6f9;border:1px solid var(--line);border-radius:var(--radius-panel);" +
      "margin-top:6px;cursor:zoom-in;";
    img.addEventListener("click", () =>
      Media.showImageModal(out.image_url, "首帧图（点缩略看原图）"));
    body.appendChild(img);
  }
}

/* ---------- 顶栏按钮绑定（DOMContentLoaded 一次） ---------- */
document.addEventListener("DOMContentLoaded", () => {
  /* 真多页：配方页加载即初始化（原来靠旧单页的 tab 切换懒加载） */
  loadRecipes().catch(() => {});
  loadRecipeRuns().catch(() => {});
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
