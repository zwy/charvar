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
    ["pose", "姿势构图（人偶线稿参照）"],
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

  /* ---------- 来源：动作库（pose-db-01，灰人偶线稿只锚姿势构图） ----------
     与其它来源不同：动作库参考图的 role 固定 pose —— 图里没有角色外观可锚，
     身份全部来自主体档案或其它参考图。预设页与 Playground 共用此浏览器，
     onPick 由发起方（add / Playground 的 push）决定落入哪边。 */
  async function onPickPose() {
    showPoseBrowser((pick) => {
      add({
        source: "pose",
        pose_id: pick.pose_id,
        angle: pick.angle,
        // role 固定 pose；人偶线稿不锚身份，身份交给主体档案或其它参考图
        role: "pose",
        name: pick.name,
        url: pick.url,
      });
    });
  }

  /* 动作库浏览器（共用，master-detail 版）：左栏筛选（系統页签 + 分類 + 搜索）
     + 可滚动网格，右栏常驻详情（双语标题 / 系統·分類 / 视角切换 / 加入）。
     点卡片右栏即时更新；中日对照：中文为主阅读层级、日文原文 muted 小字。
     预设页与 Playground 共用（onPick 由发起方决定加入哪边）。 */
  function showPoseBrowser(onPick, { title = "动作库（灰人偶线稿 · 只锚姿势构图）" } = {}) {
    Media.showPanelModal(title, (body, close) => {
      let allData = null;
      const filter = { line: "", category: "", q: "" };
      let grid;
      let detailEl;
      let selectedPose = null;
      let selectedAngle = null;
      body.innerHTML =
        '<div class="pose-browser">' +
        '<div class="pose-master">' +
        '<div class="pose-toolbar">' +
        '<div id="pose-line-tabs" class="segmented"></div>' +
        '<div class="pose-toolbar-row">' +
        '<select id="pose-cat"></select>' +
        '<input id="pose-q" type="text" placeholder="按动作名 / 编号 / id 搜索（中/日/编号均可）"></input>' +
        "</div>" +
        "</div>" +
        '<div id="pose-grid" class="image-grid"></div>' +
        "</div>" +
        '<div id="pose-detail" class="pose-detail"></div>' +
        "</div>";
      grid = body.querySelector("#pose-grid");
      detailEl = body.querySelector("#pose-detail");

      function esc(s) {
        return escapeHtml(String(s == null ? "" : s));
      }

      /* 双语标签：中文（日文）；译文与原文相同（未收录）时只给一个 */
      function bilingual(zh, orig) {
        const z = zh || orig || "";
        return z === orig ? esc(z) : esc(z) + "<em>（" + esc(orig || "") + "）</em>";
      }

      function renderTabs() {
        const tabs = body.querySelector("#pose-line-tabs");
        const total = (allData.poses || []).length;
        const keys = [["", "全部", total]].concat(
          (allData.lines || []).map(function (l) {
            return [l.id, l.zh || l.id, l.poses];
          })
        );
        tabs.innerHTML = keys
          .map(function (k) {
            return (
              '<button type="button" class="seg' + (filter.line === k[0] ? " active" : "") +
              '" data-v="' + esc(k[0]) + '">' + esc(k[1]) + "<em>" + k[2] + "</em></button>"
            );
          })
          .join("");
        [...tabs.children].forEach(function (btn) {
          btn.addEventListener("click", function () {
            filter.line = btn.dataset.v;
            renderTabs();
            renderGrid();
          });
        });
      }

      function renderCategorySelect() {
        const sel = body.querySelector("#pose-cat");
        const cats = (allData.categories || []).filter(function (c) {
          return !filter.line || true;
        });
        sel.innerHTML =
          '<option value="">全部分類</option>' +
          cats
            .map(function (c) {
              return (
                '<option value="' + esc(c.id) + '"' + (filter.category === c.id ? " selected" : "") +
                ">" + bilingual(c.zh, c.id) + " · " + c.poses + " 动作</option>"
              );
            })
            .join("");
        sel.onchange = function () {
          filter.category = sel.value;
          filter.line = filter.line; // 分類过滤不依赖系統（分类只在全部系統下选）
          renderGrid();
        };
      }

      function wireSearch() {
        const input = body.querySelector("#pose-q");
        let timer = null;
        input.addEventListener("input", function () {
          clearTimeout(timer);
          timer = setTimeout(function () {
            filter.q = input.value.trim().toLowerCase();
            renderGrid();
          }, 180);
        });
      }

      function filteredPoses() {
        const poses = (allData && allData.poses) || [];
        return poses.filter(function (p) {
          if (filter.line && p.line !== filter.line) return false;
          if (filter.category && p.category !== filter.category) return false;
          if (filter.q) {
            const hay = (p.title + " " + (p.title_zh || "") + " " + p.number + " " + p.pose_id).toLowerCase();
            if (hay.indexOf(filter.q) === -1) return false;
          }
          return true;
        });
      }

      function renderGrid() {
        const poses = filteredPoses();
        grid.innerHTML = "";
        if (!poses.length) {
          grid.innerHTML = '<div class="pose-empty">没有符合条件的动作 —— 换个筛选条件</div>';
          return;
        }
        poses.forEach(function (p) {
          const front = (p.images && p.images[0]) || {};
          const card = document.createElement("div");
          card.className = "image-card pose-card" + (selectedPose && selectedPose.pose_id === p.pose_id ? " active" : "");
          card.innerHTML =
            '<div class="pose-thumb"><img src="' + esc(front.thumb_url) + '" alt=""></div>' +
            '<div class="image-meta">' +
            "<strong>" + esc(p.number) + "</strong>" +
            "<span class=\"pose-zh\">" + esc(p.title_zh || p.title) + "</span>" +
            "<span class=\"pose-jp\">" + esc(p.title) + "</span>" +
            "</div>";
          card.addEventListener("click", function () {
            selectPose(p);
          });
          grid.appendChild(card);
        });
        if (!selectedPose || !poses.some(function (p) { return p.pose_id === selectedPose.pose_id; })) {
          // 首次打开 / 筛选后选中项不在结果里 → 自动选中第一个（详情栏始终有内容）
          selectPose(poses[0]);
        } else {
          renderDetail();
        }
      }

      function selectPose(p) {
        selectedPose = p;
        selectedAngle = (p.images && p.images[0]) || null;
        // 选中环按 filteredPoses 的顺序标（与 renderGrid 的卡片顺序一致）
        const poses = filteredPoses();
        [...grid.querySelectorAll(".pose-card")].forEach(function (el, i) {
          el.classList.toggle("active", !!p && poses[i] && poses[i].pose_id === p.pose_id);
        });
        renderDetail();
      }

      function renderDetail() {
        const p = selectedPose;
        if (!p) {
          detailEl.innerHTML = '<div class="pose-empty">在左侧选择一个动作</div>';
          return;
        }
        const images = p.images || [];
        if (!selectedAngle || !images.some(function (im) { return im.angle === selectedAngle.angle; })) {
          selectedAngle = images[0] || null;
        }
        detailEl.innerHTML =
          '<div class="pose-detail-img" title="点看大图">' +
          '<img src="' + esc(selectedAngle.thumb_url) + '" alt="' + esc(p.number) + '"></div>' +
          '<div class="pose-detail-titles">' +
          "<h3>" + esc(p.number) + " " + esc(p.title_zh || p.title) + "</h3>" +
          '<div class="pose-jp">' + esc(p.title) + "</div>" +
          "</div>" +
          '<div class="pose-meta">' + bilingual(p.line_zh, p.line) + " · " +
          (p.category ? bilingual(p.category_zh, p.category) : "-") + "</div>" +
          '<div class="pose-angle-block">' +
          '<div class="pose-angle-label">选择视角</div>' +
          '<div class="pose-angle-grid" id="pose-angle-grid"></div>' +
          "</div>" +
          '<div class="pose-detail-actions">' +
          '<button id="pose-add-btn" type="button" class="primary">加入参考图</button>' +
          "</div>";
        const box = detailEl.querySelector(".pose-detail-img");
        box.addEventListener("click", function () {
          Media.showImageModal(selectedAngle.full_url, p.number + " " + p.title_zh + "（" + p.title + "）");
        });
        const angleBox = detailEl.querySelector("#pose-angle-grid");
        images.forEach(function (im) {
          const btn = document.createElement("button");
          btn.type = "button";
          btn.className = "btn-sm" + (selectedAngle.angle === im.angle ? " active" : "");
          btn.textContent = im.angle_zh;
          btn.addEventListener("click", function () {
            selectedAngle = im;
            renderDetail();
          });
          angleBox.appendChild(btn);
        });
        detailEl.querySelector("#pose-add-btn").addEventListener("click", function () {
          onPick({
            pose_id: p.pose_id,
            angle: selectedAngle.angle,
            name: p.number + " " + (p.title_zh || p.title) + "（" + p.title + "）· " + selectedAngle.angle_zh,
            url: selectedAngle.thumb_url,
          });
          close();
        });
      }

      api("/poses")
        .then(function (data) {
          allData = data;
          renderTabs();
          renderCategorySelect();
          wireSearch();
          renderGrid();
        })
        .catch(function (err) {
          grid.innerHTML = '<div class="pose-empty">加载失败：' + esc(err.message) + "</div>";
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
      } else if (ref.source === "pose") {
        spec.pose_id = ref.pose_id;
        spec.angle = ref.angle || "";
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
    const poseBtn = $("#btn-gen-pose-add");
    if (poseBtn) poseBtn.addEventListener("click", onPickPose);
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
    // 动作库浏览器（共用）：Playground（adhoc.js）也从这里发起，
    // onPick 由调用方决定加入哪边
    showPoseBrowser,
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
