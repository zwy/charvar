/* Playground 工作流引擎：参考构成槽位 + /adhoc/compose 拼装。
   五种生图模式：自由生图 = 现状平铺参考图（锚定句手写）；
   单主体动作 / 双主体动作 / 人脸动作 / 换装 = 固定槽位 → 后端 /adhoc/compose
   拼装（文案来自 pose_prompt，与 CLI 同源同结构；页面不另造锚定句）。
   槽位状态分模式独立保存（切模式来回不丢）；构成变化自动拼装；
   提示词可手改 —— 手改后构成再变不自动覆盖，改用「重新拼装」按钮。 */
const AdhocFlow = (() => {
  const FLOWS = {
    free: { label: "自由生图" },
    single: { label: "单主体动作", workflow: "pose", lineConstraint: "single" },
    pair: { label: "双主体动作", workflow: "pose", lineConstraint: "pair" },
    face: { label: "人脸动作", workflow: "pose", lineConstraint: "single" },
    costume: { label: "换装", workflow: "costume" },
  };
  const flow = { current: "free", references: null, meta: null, promptEdited: false, pending: false };
  const states = {
    single: { subject: "", pose: null, costume: null },
    pair:   { subject1: "", subject2: "", pose: null },
    face:   { face: null, bodyLock: "", pose: null, costume: null },
    costume: { identity: "subject", subject: "", face: null, costume: null },
  };
  let onChange = null;
  let composeToken = 0;
  let subjects = null;      // subjects 列表缓存
  let slotsCache = {};      // subject_id -> slots（预取，主体槽预览用）

  const $ = (sel) => document.querySelector(sel);

  function notify() {
    if (typeof onChange === "function") onChange();
  }
  /* ---------- 主体档案槽位：与其它槽位同构（空态虚线框 + 弹窗卡片选） ---------- */

  async function ensureSlots(subjectId) {
    if (!subjectId || slotsCache[subjectId]) return;
    try {
      const data = await api("/subjects/" + encodeURIComponent(subjectId) + "/images");
      slotsCache[subjectId] = data.slots || [];
    } catch {
      slotsCache[subjectId] = [];
    }
  }
  /* 主体槽显示值：缩略图按「拼装将用的槽位」取（meta.slot_label，缺省 front；
     kindOverride 给 pair 乙这类恒用 front 的槽）。 */
  function subjectSlotValue(id, kindOverride) {
    if (!id) return null;
    const s = (subjects || []).find(function (x) { return x.id === id; });
    const slots = slotsCache[id] || [];
    const kind = kindOverride || (flow.meta && flow.meta.slot_label) || "front";
    let slot = slots.find(function (x) { return x.kind === kind; });
    if (!slot && kind !== "front") slot = slots.find(function (x) { return x.kind === "front"; });
    if (!slot && slots.length) slot = slots[0];
    return {
      url: slot ? (slot.thumb_url || slot.original_url || "") : "",
      title: slot ? "image1 = " + (slot.label || slot.kind) + " 槽位图" : "",
      name: s ? (id + " · " + s.name) : id,
    };
  }
  async function ensureSubjects() {
    if (!subjects) {
      try {
        subjects = await api("/subjects");
      } catch {
        subjects = null;
      }
    }
  }
  /* 主体档案弹窗选择器：档案卡网格（front 槽位图做展示，同其它弹层卡片样式）。
     currentId 用于高亮当前已选档案。 */
  function openSubjectPicker(onPick, currentId) {
    Media.showPanelModal("主体档案 · 选择主体（决定提示词主体与 image1）", function (body, close) {
      const wrap = document.createElement("div");
      wrap.textContent = "载入中…";
      body.appendChild(wrap);
      ensureSubjects().then(function () {
        return Promise.all((subjects || []).map(function (s) { return ensureSlots(s.id); }));
      }).then(function () {
        body.innerHTML = "";
        if (!subjects || !subjects.length) {
          body.innerHTML = "";
          const empty = document.createElement("div");
          empty.textContent = "还没有主体档案 —— 先在「主体档案」页建档";
          body.appendChild(empty);
          return;
        }
        const grid = document.createElement("div");
        grid.className = "image-grid subject-pick-grid";
        subjects.forEach(function (s) {
          const v = subjectSlotValue(s.id);
          const card = document.createElement("div");
          card.className = "image-card subject-pick-card" + (currentId === s.id ? " active" : "");
          card.innerHTML =
            (v.url ? '<img src="' + escapeHtml(v.url) + '" alt="" loading="lazy">' : '<span class="ref-thumb-empty"></span>') +
            '<div class="image-meta"><strong>' + escapeHtml(s.id) + "</strong>" +
            "<span>" + escapeHtml(s.name) + "</span></div>";
          card.addEventListener("click", function () {
            onPick(s);
            close();
          });
          grid.appendChild(card);
        });
        body.appendChild(grid);
      }).catch(function () {
        body.innerHTML = "";
        const bad = document.createElement("div");
        bad.textContent = "档案列表载入失败 —— 刷新后重试";
        body.appendChild(bad);
      });
    });
  }
  /* ---------- 槽位卡：空态 = 虚线占位（选择按钮），填态 = 缩略图 + 名称 + 固定徽章 ---------- */
  function slotCard(st, key, opts) {
    const image = opts.image || "";
    const pickType = opts.pick;
    const raw = st[key];
    const value = raw ? (opts.valueMap ? opts.valueMap(raw) : raw) : null;
    const el = document.createElement("div");
    el.className = "slot-pick";
    const head = document.createElement("div");
    head.className = "slot-head";
    head.innerHTML = '<span class="slot-tag">' + escapeHtml(image) + "</span>" +
      '<span class="slot-name">' + escapeHtml(opts.label) + "</span>" +
      (opts.optional ? '<span class="slot-opt">可选</span>' : "");
    el.appendChild(head);
    const body = document.createElement("div");
    body.className = "slot-body";
    if (value) {
      body.innerHTML = '<div class="ref-pick active">' +
        (value.url
          ? '<img src="' + escapeHtml(value.url) + '" alt=""' + (value.title ? ' title="' + escapeHtml(value.title) + '"' : ' title="预览大图"') + '>'
          : '<span class="ref-thumb-empty"></span>') +
        '<span class="ref-name">' + escapeHtml(value.name || "") + "</span>" +
        '<span class="badge">' + escapeHtml(image + " · " + opts.badge) + "</span>" +
        "<button type=\"button\" class=\"btn-sm\">移除</button></div>";
      const del = body.querySelector(".btn-sm");
      del.addEventListener("click", function (ev) {
        ev.stopPropagation();
        st[key] = null;
        changed();
        renderSlots();
      });
      const simg = body.querySelector(".ref-pick img");
      if (simg) simg.addEventListener("click", function (ev) {
        ev.stopPropagation();
        Media.showImageModal(value.url, value.name || "");
      });
    } else {
      body.innerHTML = '<div class="slot-empty"><button type="button" class="btn-sm">选择</button></div>';
      const emptyBox = body.querySelector(".slot-empty");
      emptyBox.addEventListener("click", function (ev) {
        ev.stopPropagation();
        openPicker(pickType);
      });
    }
    el.appendChild(body);
    return el;
  }
  /* ---------- 弹层发起 ---------- */
  function openPicker(type) {
    if (type === "subject") {
      openSubjectPicker(function (pick) {
        states[flow.current].subject = pick.id;
        subjectSaved();
      }, states[flow.current].subject);
      return;
    }
    if (type === "subject1") {
      openSubjectPicker(function (pick) {
        states.pair.subject1 = pick.id;
        subjectSaved();
      }, states.pair.subject1);
      return;
    }
    if (type === "subject2") {
      openSubjectPicker(function (pick) {
        states.pair.subject2 = pick.id;
        subjectSaved();
      }, states.pair.subject2);
      return;
    }
    if (type === "pose") {
      const def = FLOWS[flow.current];
      const lc = def ? def.lineConstraint || null : null;
      GenRefs.showPoseBrowser(function (pick) {
        const st = states[flow.current];
        if (flow.current === "pair") states.pair.pose = pick;
        else if (flow.current === "single") states.single.pose = pick;
        else if (flow.current === "face") states.face.pose = pick;
        poseSaved();
      }, { title: "动作库 · 选择动作与视角", lineConstraint: lc, requireAction: true });
    } else if (type === "face") {
      GenRefs.showFaceBrowser(function (pick) {
        if (flow.current === "face") states.face.face = pick;
        else if (flow.current === "costume") states.costume.face = pick;
        faceSaved();
      }, { title: "人脸库 · 选择身份锚（face=锚脸 / styling=锚整体造型）" });
    } else if (type === "costume") {
      Media.showPanelModal("服饰库 · 选择服饰（只锚服装与配件）", function (body, close) {
        CostumeGallery.mount({ root: body, onPick: function (pick) {
          states[flow.current].costume = pick;
          costumeSaved();
        }, close });
      });
    }
  }
  /* 选完落槽 + 触发拼装 */
  function subjectSaved() { changed(); renderSlots(); }
  function poseSaved() { changed(); renderSlots(); }
  function faceSaved() { changed(); renderSlots(); }
  function costumeSaved() { changed(); renderSlots(); }
  /* ---------- 构成变化 → 拼装（手改守卫） ---------- */
  function setComposeState(text, isErr) {
    const el = document.getElementById("adhoc-compose-state");
    if (el) el.textContent = text;
    const row = document.getElementById("adhoc-compose-row");
    if (row) row.hidden = !isErr;
  }
  function bodyLockField() {
    const wrap = document.createElement("div");
    wrap.className = "flow-body-lock";
    const lab = document.createElement("label");
    lab.className = "flow-subject-name";
    lab.textContent = "体形锚（可选，每行一条英文短句）";
    const ta = document.createElement("textarea");
    ta.className = "flow-body-ta";
    ta.rows = 2;
    ta.placeholder = "例：keep body proportions soft and full-figured";
    ta.value = states.face.bodyLock || "";
    ta.addEventListener("blur", function () {
      states.face.bodyLock = ta.value;
      changed();
    });
    wrap.appendChild(lab);
    wrap.appendChild(ta);
    return wrap;
  }
  /* ---------- 换装身份锚：[主体档案 | 人脸库] 二选一 ---------- */
  function identitySeg() {
    const st = states.costume;
    const wrap = document.createElement("div");
    wrap.className = "flow-identity-row";
    const lab = document.createElement("div");
    lab.className = "flow-subject-name";
    lab.textContent = "身份锚";
    const seg = document.createElement("div");
    seg.className = "segmented seg-sm";
    seg.innerHTML =
      '<button type="button" class="seg' + (st.identity === "subject" ? " active" : "") + '" data-id="subject">主体档案</button>' +
      '<button type="button" class="seg' + (st.identity === "face" ? " active" : "") + '" data-id="face">人脸库</button>';
    [...seg.children].forEach(function (btn) {
      btn.addEventListener("click", function () {
        if (st.identity === btn.dataset.id) return;
        st.identity = btn.dataset.id;
        /* 重新渲染本组：身份锚槽位随之切换 */
        renderSlots();
        changed();
      });
    });
    wrap.appendChild(lab);
    wrap.appendChild(seg);
    return wrap;
  }
  /* 换装-主体身份锚的选主体同走 subject 槽位卡（image1）；人脸身份走 face 槽位卡 */
  function renderSlots() {
    const box = document.getElementById("adhoc-flow-slots");
    if (!box) return;
    box.innerHTML = "";
    if (flow.current === "free") return;
    const st = states[flow.current];
    if (flow.current === "single") {
      box.appendChild(slotCard(st, "subject", { image: "image1", label: "主体档案", badge: "主体", pick: "subject",
        valueMap: function () { return subjectSlotValue(st.subject); } }));
      box.appendChild(slotCard(st, "pose", { image: "image2", label: "动作", badge: "动作", pick: "pose" }));
      box.appendChild(slotCard(st, "costume", { image: "image3", label: "服饰", badge: "服饰", pick: "costume", optional: true }));
    } else if (flow.current === "pair") {
      box.appendChild(slotCard(st, "subject1", { image: "image1", label: "主体甲（描述里的「甲」）", badge: "主体甲", pick: "subject1",
        valueMap: function () { return subjectSlotValue(st.subject1); } }));
      box.appendChild(slotCard(st, "subject2", { image: "image2", label: "主体乙（描述里的「乙」）", badge: "主体乙", pick: "subject2",
        valueMap: function () { return subjectSlotValue(st.subject2, "front"); } }));
      box.appendChild(slotCard(st, "pose", { image: "image3", label: "双人动作", badge: "双人动作", pick: "pose" }));
    } else if (flow.current === "face") {
      box.appendChild(slotCard(st, "face", { image: "image1", label: "人脸", badge: "人脸", pick: "face" }));
      box.appendChild(bodyLockField());
      box.appendChild(slotCard(st, "pose", { image: "image2", label: "动作", badge: "动作", pick: "pose" }));
      box.appendChild(slotCard(st, "costume", { image: "image3", label: "服饰", badge: "服饰", pick: "costume", optional: true }));
    } else if (flow.current === "costume") {
      box.appendChild(identitySeg());
      if (st.identity === "subject") {
        box.appendChild(slotCard(st, "subject", { image: "image1", label: "主体档案", badge: "主体", pick: "subject",
          valueMap: function () { return subjectSlotValue(st.subject); } }));
      } else {
        box.appendChild(slotCard(st, "face", { image: "image1", label: "人脸", badge: "人脸", pick: "face" }));
      }
      box.appendChild(slotCard(st, "costume", { image: "image2", label: "服饰", badge: "服饰", pick: "costume" }));
    }
    updateAnchors();
  }
  /* 组标题右侧：各模式的 <imageN> 锚定分配（控件里看不到的信息） */
  function updateAnchors() {
    const el = document.getElementById("adhoc-flow-anchors");
    if (!el) return;
    const hint = {
      single: "image1 主体 · image2 动作 · 可选 image3 服饰",
      pair: "image1 甲 · image2 乙 · image3 双人动作",
      face: "image1 人脸 · image2 动作 · 可选 image3 服饰",
      costume: "image1 主体/人脸 · image2 服饰",
    };
    el.textContent = hint[flow.current] || "";
  }
  /* ---------- compose 请求构造 ---------- */
  function composeReq() {
    const f = flow.current;
    const st = states[f];
    if (f === "single") {
      if (!st.subject || !st.pose) return null;
      const out = {
        workflow: "pose", subject_id: st.subject,
        pose_id: st.pose.pose_id, pose_angle: st.pose.angle || "",
        pose_source: st.pose.source || "",
      };
      if (st.costume) {
        out.costume_id = st.costume.item_id;
        out.costume_source = st.costume.source_id || "";
      }
      return out;
    }
    if (f === "pair") {
      if (!st.subject1 || !st.subject2 || !st.pose) return null;
      return {
        workflow: "pose", subject_id: st.subject1, subject2_id: st.subject2,
        pose_id: st.pose.pose_id, pose_angle: st.pose.angle || "",
        pose_source: st.pose.source || "",
      };
    }
    if (f === "face") {
      if (!st.face || !st.pose) return null;
      const bl = String(st.bodyLock || "").split("\n").map(function (x) { return x.trim(); }).filter(Boolean);
      const out = {
        workflow: "pose", face_id: st.face.face_id, face_source: st.face.source || "",
        pose_id: st.pose.pose_id, pose_angle: st.pose.angle || "",
        pose_source: st.pose.source || "",
        body_lock: bl,
      };
      if (st.costume) {
        out.costume_id = st.costume.item_id;
        out.costume_source = st.costume.source_id || "";
      }
      return out;
    }
    if (f === "costume") {
      const isFace = st.identity === "face";
      if (isFace && !st.face) return null;
      if (!isFace && !st.subject) return null;
      if (!st.costume) return null;
      const out = { workflow: "costume", costume_id: st.costume.item_id, costume_source: st.costume.source_id || "" };
      if (isFace) {
        out.face_id = st.face.face_id;
        out.face_source = st.face.source || "";
      } else {
        out.subject_id = st.subject;
      }
      return out;
    }
    return null;
  }
  /* ---------- 自动拼装 ---------- */
  function changed() {
    if (flow.current === "free") return;
    if (flow.promptEdited) {
      flow.pending = true;
      renderComposeState();
      return;
    }
    recompose();
  }

  async function recompose(force) {
    const req = composeReq();
    if (!req) {
      flow.references = null;
      flow.meta = null;
      flow.pending = false;
      const st2 = document.getElementById("adhoc-compose-state");
      if (st2) st2.textContent = "构成未选齐 —— 选齐后自动拼装";
      const mEl = document.getElementById("adhoc-compose-meta");
      if (mEl) { mEl.hidden = true; }
      const row = document.getElementById("adhoc-compose-row");
      if (row) row.hidden = true;
      renderSlots();
      return;
    }
    if (!force && flow.promptEdited) {
      flow.pending = true;
      renderComposeState();
      return;
    }
    const token = ++composeToken;
    const stEl = document.getElementById("adhoc-compose-state");
    if (stEl) stEl.textContent = "拼装中…";
    try {
      const out = await api("/adhoc/compose", { method: "POST", body: JSON.stringify(req) });
      if (token !== composeToken) return;
      flow.references = out.references || [];
      flow.meta = out.meta || null;
      flow.promptEdited = false;
      flow.pending = false;
      const ta = document.getElementById("adhoc-prompt");
      if (ta) ta.value = out.prompt;
      renderSlots();
      renderComposeState();
    } catch (err) {
      if (token !== composeToken) return;
      flow.references = null;
      flow.meta = null;
      setComposeState("", false);
      renderSlots();
      setStatus($("#adhoc-status"), "拼装失败：" + err.message, true);
    }
  }
  /* ---------- 拼装状态行 + meta 小字 ---------- */
  function metaLine() {
    const m = flow.meta || {};
    if (!m.workflow) return "";
    const parts = [];
    if (m.line_kind === "upper") parts.push("半身构图 · image1 用 headshot 槽位");
    else if (m.line_kind === "pair") parts.push("双人构图");
    else if (m.workflow === "pose") parts.push("全身构图");
    if (m.face_section === "face") parts.push("身份锚 = 锚脸正面胸像");
    else if (m.face_section === "styling") parts.push("身份锚 = 整体造型（含发型服装）");
    if (m.costume_mode === "add") parts.push("服饰 = 叠加语义（原衣物保留）");
    else if (m.costume_mode === "replace") parts.push("服饰 = 整套替换");
    return parts.join(" · ");
  }

  function renderComposeState() {
    const el = document.getElementById("adhoc-compose-state");
    const row = document.getElementById("adhoc-compose-row");
    if (!el) return;
    const freeHide = flow.current === "free";
    if (freeHide) {
      el.textContent = "";
      if (row) row.hidden = true;
      const mEl = document.getElementById("adhoc-compose-meta");
      if (mEl) mEl.hidden = true;
      return;
    }
    if (flow.composing) {
      el.textContent = "拼装中…";
      if (row) row.hidden = true;
      return;
    }
    if (flow.pending) {
      el.textContent = "构成已变化（提示词已手改）· 出图前先重新拼装";
      if (row) row.hidden = false;
      return;
    }
    if (flow.promptEdited) {
      el.textContent = "提示词已手改 · 构成变化不再自动拼装";
      if (row) row.hidden = true;
      return;
    }
    const mEl = document.getElementById("adhoc-compose-meta");
    const filled = flow.references && flow.references.length;
    if (filled) {
      el.textContent = "已按构成拼装，可手改";
      if (row) row.hidden = true;
    } else {
      el.textContent = "构成未选齐 —— 选齐后自动拼装";
      if (row) row.hidden = true;
    }
    const metaText = filled ? metaLine() : "";
    if (mEl) {
      mEl.hidden = !metaText;
      mEl.textContent = metaText;
    }
  }
  /* ---------- 初始化与对外接口 ---------- */
  function init(o) {
    onChange = (o && o.onChange) || null;
    const ta = document.getElementById("adhoc-prompt");
    if (ta) {
      ta.addEventListener("input", function () {
        if (flow.current === "free") return;
        flow.promptEdited = true;
        renderComposeState();
      });
    }
    loadSubjects();
  }

  async function loadSubjects() {
    try {
      subjects = await api("/subjects");
    } catch {
      subjects = null;
    }
    if (subjects) renderSlots();
  }

  function setFlow(key) {
    flow.current = key;
    flow.references = null;
    flow.meta = null;
    flow.pending = false;
    flow.promptEdited = false;
    renderSlots();
    renderComposeState();
    updateAnchors();
    notify();
    if (composeReq()) recompose();
  }

  return {
    init,
    setFlow,
    isActive: function () { return flow.current !== "free"; },
    references: function () { return flow.current === "free" ? null : flow.references; },
    isStale: function () { return flow.pending; },
    recomposeNow: function () { recompose(true); },
  };
})();
