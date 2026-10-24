/* 参考图选择器（预设页）：
   三种来源与 Playground 同源 —— 上传暂存 / 最近生成的成图 / 主体档案槽位；
   每张图带一个「分工」标签（role），决定 LLM 改写的锚定句里它管哪一摊
   （全身正面 / 半身 / 侧面 / 背面 / 设定图）。顺序即提示词里的 <image1>、<image2>…。

   浏览器永远不给路径：来源只给 id（staged_id / subject_id+kind / run_id+index），
   文件由服务端从自己的目录里取。 */
const GenRefs = (() => {
  const MAX_REFS = 4;
  const ROLES = [
    ["front", "全身正面（体型/服装/鞋）"],
    ["headshot", "半身（脸/发型）"],
    ["side", "侧面（朝向/贴身细节）"],
    ["behind", "背面（后脑/背部）"],
    ["character_sheet", "设定图（整套）"],
  ];
  const DEFAULT_ROLE = "front";

  const state = {
    references: [], // [{source, role, name, url, localUrl}]
    subjectSlots: {},
    onChange: null,
  };

  const $ = (sel) => document.querySelector(sel);

  function notify() {
    if (typeof state.onChange === "function") state.onChange(state.references);
    renderHint();
  }

  function roleLabel(role) {
    const found = ROLES.find(([value]) => value === role);
    return found ? found[1] : role;
  }

  /* ---------- 列表渲染 ---------- */
  function render() {
    const box = $("#gen-ref-list");
    if (!box) return;
    box.innerHTML = "";
    if (!state.references.length) {
      // 空态：虚线占位框，别让整块区空着看不出是干什么的
      const empty = document.createElement("div");
      empty.className = "ref-empty";
      empty.textContent = "暂无参考图";
      box.appendChild(empty);
    }
    state.references.forEach((ref, index) => {
      const el = document.createElement("div");
      el.className = "ref-pick active";
      el.innerHTML = `${ref.url ? `<img src="${ref.url}" alt="">` : `<span class="ref-thumb-empty"></span>`}
        <span class="ref-name">${escapeHtml(ref.name || `参考图 ${index + 1}`)}</span>
        <span class="badge">#${index + 1}</span>`;
      const img = el.querySelector("img");
      if (img) {
        img.title = "预览大图";
        img.addEventListener("click", (ev) => {
          ev.stopPropagation();
          Media.showImageModal(ref.url, ref.name || "");
        });
      }
      // 分工标签：LLM 锚定句按它决定这张图管哪一摊
      const select = document.createElement("select");
      select.className = "ref-role";
      select.innerHTML = ROLES.map(
        ([value, label]) =>
          `<option value="${value}"${value === ref.role ? " selected" : ""}>${escapeHtml(label)}</option>`
      ).join("");
      select.addEventListener("change", () => {
        ref.role = select.value;
        notify();
      });
      select.addEventListener("click", (ev) => ev.stopPropagation());
      el.appendChild(select);
      const del = document.createElement("button");
      del.type = "button";
      del.className = "btn-sm";
      del.textContent = "移除";
      del.addEventListener("click", (ev) => {
        ev.stopPropagation();
        remove(index);
      });
      el.appendChild(del);
      box.appendChild(el);
    });
    notify();
  }

  function renderHint() {
    // 参考图组标题右侧：只报数量（说明文字在别处不再重复）
    const count = state.references.length;
    const el = $("#gen-ref-count");
    if (el) el.textContent = `已选 ${count} 张`;
  }

  function releaseRef(ref) {
    // objectURL 不会自动回收，移除时手动放掉
    if (ref && ref.localUrl) URL.revokeObjectURL(ref.localUrl);
  }

  async function remove(index) {
    const ref = state.references[index];
    if (ref && ref.source === "staged") {
      try {
        await api(`/adhoc/references/${encodeURIComponent(ref.id)}`, { method: "DELETE" });
      } catch {
        // 暂存文件可能已被清理，忽略
      }
    }
    releaseRef(ref);
    state.references.splice(index, 1);
    render();
  }

  function add(ref) {
    if (state.references.length >= MAX_REFS) {
      setStatus($("#gen-status"), `参考图最多 ${MAX_REFS} 张`, true);
      return false;
    }
    state.references.push(ref);
    render();
    return true;
  }

  /* ---------- 来源：上传暂存 ---------- */
  async function onUpload(ev) {
    const files = Array.from(ev.target.files || []);
    ev.target.value = "";
    for (const file of files) {
      try {
        const out = await apiUpload("/adhoc/references", file);
        add({
          source: "staged",
          id: out.id,
          role: DEFAULT_ROLE,
          name: out.name || file.name,
          // 本地 objectURL 预览：暂存文件 24 小时后会被清掉，服务器 URL 预览会变破图
          url: URL.createObjectURL(file),
          localUrl: true,
        });
      } catch (err) {
        setStatus($("#gen-status"), `上传失败：${err.message}`, true);
      }
    }
  }

  /* ---------- 来源：主体档案槽位 ----------
     主体只有一处选择：第 ① 步的「主体档案」（#gen-subject）—— 提示词主体与参考图
     必须是同一个主体，否则提示词里的锁定项和参考图里的人会对不上。 */
  async function loadKindOptions() {
    const subjectId = ($("#gen-subject") || {}).value || "";
    const kindSel = $("#gen-ref-kind");
    if (!kindSel) return;
    if (!subjectId) {
      kindSel.innerHTML = `<option value="">（先在①选主体档案）</option>`;
      state.subjectSlots[subjectId] = [];
      return;
    }
    try {
      const data = await api(`/subjects/${encodeURIComponent(subjectId)}/images`);
      state.subjectSlots[subjectId] = data.slots || [];
      const ready = (data.slots || []).filter((s) => s.is_ready);
      kindSel.innerHTML = ready.length
        ? ready.map((s) => `<option value="${escapeHtml(s.kind)}">${escapeHtml(s.label)}</option>`).join("")
        : `<option value="">（该主体还没有就绪的图片）</option>`;
    } catch {
      kindSel.innerHTML = `<option value="">（加载失败）</option>`;
    }
  }

  function onAddFromSubject() {
    const subjectId = ($("#gen-subject") || {}).value || "";
    const kind = $("#gen-ref-kind").value;
    if (!subjectId || !kind) {
      setStatus($("#gen-status"), "先在①选主体档案和图片类型（只有就绪的图片能选）", true);
      return;
    }
    const slots = state.subjectSlots[subjectId] || [];
    const slot = slots.find((s) => s.kind === kind);
    add({
      source: "subject",
      subject_id: subjectId,
      kind,
      // 档案槽位自带分工（front/headshot/…），不用猜
      role: kind,
      name: `${subjectId} / ${(slot && slot.label) || kind}`,
      url: slot ? slot.thumb_url || slot.original_url : "",
    });
  }

  /* ---------- 来源：最近生成 ---------- */
  function onPickRecent() {
    Media.showPanelModal("从最近生成选择参考图", (body, close) => {
      body.innerHTML = `<span class="hint">加载中…</span>`;
      const grid = document.createElement("div");
      grid.className = "image-grid ref-picker-grid";
      const pick = (rec) => {
        close();
        add({
          source: "run",
          run_id: rec.run_id,
          index: rec.index || 0,
          role: DEFAULT_ROLE,
          name: rec.name || rec.run_id,
          url: rec.url || "",
        });
      };
      // 历史生成（本页/历史记录跑出来的成图）
      api("/runs?limit=8")
        .then(async (runs) => {
          const blocks = [];
          for (const run of runs || []) {
            const data = await api(`/runs/${encodeURIComponent(run.run_id)}/images`).catch(() => null);
            if (!data) continue;
            const done = (data.items || []).filter((it) => it.status === "done");
            if (!done.length) continue;
            blocks.push({ run, done });
          }
          for (const { run, done } of blocks) {
            const title = document.createElement("div");
            title.className = "hint";
            title.textContent = `${run.subject_id || ""} · ${run.preset || run.run_id} · ${(run.created_at || "").slice(5, 16)}`;
            grid.appendChild(title);
            for (const item of done) {
              const card = document.createElement("div");
              card.className = "image-card";
              card.innerHTML = `<img src="${item.thumb_url || item.primary_url || ""}" alt="" loading="lazy">`;
              card.addEventListener("click", () =>
                pick({
                  run_id: run.run_id,
                  index: item.item_index,
                  url: item.primary_url || item.thumb_url,
                  name: `${run.preset || run.run_id} #${item.item_index}`,
                })
              );
              grid.appendChild(card);
            }
          }
          // Playground 产物
          const adhoc = await api("/adhoc?limit=12").catch(() => []);
          const adhocDone = (adhoc || []).filter((r) => r.has_image);
          if (adhocDone.length) {
            const title = document.createElement("div");
            title.className = "hint";
            title.textContent = "Playground 产物";
            grid.appendChild(title);
            for (const rec of adhocDone) {
              const card = document.createElement("div");
              card.className = "image-card";
              card.innerHTML = `<img src="/adhoc/${encodeURIComponent(rec.run_id)}/thumb" alt="" loading="lazy">`;
              card.addEventListener("click", () =>
                pick({
                  run_id: rec.run_id,
                  index: 0,
                  url: `/adhoc/${encodeURIComponent(rec.run_id)}/image`,
                  name: (rec.prompt || rec.run_id).slice(0, 40),
                })
              );
              grid.appendChild(card);
            }
          }
          if (!grid.children.length) {
            grid.innerHTML = `<span class="hint">还没有可用的图 —— 先出几张</span>`;
          }
          body.replaceChildren(grid);
        })
        .catch((err) => {
          body.innerHTML = `<span class="hint">加载失败：${escapeHtml(err.message)}</span>`;
        });
    });
  }

  /* ---------- 对外接口 ---------- */
  function specs() {
    return state.references.map((ref) => {
      const spec = { role: ref.role || DEFAULT_ROLE };
      if (ref.source === "staged") spec.staged_id = ref.id;
      else if (ref.source === "subject") {
        spec.subject_id = ref.subject_id;
        spec.kind = ref.kind;
      } else {
        spec.run_id = ref.run_id;
        spec.index = ref.index || 0;
      }
      return spec;
    });
  }

  function roles() {
    return state.references.map((ref) => ref.role || DEFAULT_ROLE);
  }

  function count() {
    return state.references.length;
  }

  async function clear() {
    const refs = state.references.slice();
    state.references = [];
    render();
    for (const ref of refs) {
      releaseRef(ref);
      if (ref.source === "staged") {
        try {
          await api(`/adhoc/references/${encodeURIComponent(ref.id)}`, { method: "DELETE" });
        } catch {
          // 已清理
        }
      }
    }
  }

  /* 主体换了 → 主体档案来源的参考图要清掉：提示词主体与参考图主体必须一致，
     否则提示词里的锁定项和参考图里的人对不上（上传暂存 / 最近生成的不受主体约束，保留）。 */
  async function dropSubjectRefs() {
    const keep = state.references.filter((r) => r.source !== "subject");
    const dropped = state.references.length - keep.length;
    if (!dropped) return 0;
    const removed = state.references.filter((r) => r.source === "subject");
    state.references = keep;
    for (const ref of removed) releaseRef(ref);
    render();
    return dropped;
  }

  function mount() {
    const file = $("#gen-ref-file");
    if (file) file.addEventListener("change", onUpload);
    const addBtn = $("#btn-gen-ref-add");
    if (addBtn) addBtn.addEventListener("click", onAddFromSubject);
    const recentBtn = $("#btn-gen-recent-add");
    if (recentBtn) recentBtn.addEventListener("click", onPickRecent);
    const clearBtn = $("#btn-gen-ref-clear");
    if (clearBtn) clearBtn.addEventListener("click", () => clear().catch(() => {}));
    render();
    loadKindOptions().catch(() => {});
  }

  return {
    mount,
    specs,
    roles,
    count,
    clear,
    roleLabel,
    ROLES,
    MAX_REFS,
    // 主体档案换了 → 参考图的槽位下拉要重刷（主体只有一个选择器，在①）
    reloadKinds() {
      return loadKindOptions();
    },
    // 换主体时清掉主体档案来源的参考图（旧主体的人跟新主体对不上）
    dropSubjectRefs,
    onChange(fn) {
      state.onChange = fn;
    },
  };
})();
