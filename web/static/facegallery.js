/* 人脸库共享渲染：genrefs.js 的弹层与顶栏「人脸」整页（facepage.js）同用这一套
   —— 网格 CSS 红线、双语规则、筛选交互只修一处，别再两处分叉。多库结构：
   来源页签（全部 / AI 顔データベース face-db-01 —— 新库/自建库加 source 即可）
   → 形态页签（全部 / 人脸 face=只锚脸 / 造型 styling=锚脸+发型+服装 —— 站点
   section，随条目存）→ 发型下拉（4 档）→ 搜索 → 卡片网格 + 常驻详情。
   中日对照：中文为主阅读层级、日文原文 muted 小字。每条一张图，无视角双键。
   收藏（face_favorites 用户层，重灌不碰）对齐动作库：卡片右上「收藏 / 已收藏」
   徽章（点了不选卡）+ ♥ 收藏页签（形态行尾，0 收藏时隐藏）+ 详情钮，卡片↔详情
   同步；整页没有参考图上下文，不渲染「加入参考图」。 */
const FaceGallery = (() => {
  /* 来源中文标签（与后端 FACE_SOURCES 的 zh 同步维护） */
  const FACE_SOURCE_ZH = { "face-db-01": "AI 顔" };
  function mount({ root, onPick, close, onStat, variant = "modal" }) {
    let allData = null;
    const filter = { source: "", section: "", hair: "", q: "", fav: false };
    let selectedFace = null;

    root.innerHTML =
      '<div class="face-browser' + (variant === "page" ? " face-browser-page" : "") + '">' +
      '<div class="face-master">' +
      '<div class="face-toolbar">' +
      '<div class="face-src-tabs segmented"></div>' +
      '<div class="face-sec-tabs segmented"></div>' +
      '<div class="face-toolbar-row">' +
      '<select class="face-hair"></select>' +
      '<input class="face-q" type="search" placeholder="按发型细注 / 发型 / id 搜索（中/日均可）"></input>' +
      "</div>" +
      "</div>" +
      '<div class="face-grid image-grid"></div>' +
      "</div>" +
      '<div class="face-detail"></div>' +
      "</div>";
    const grid = root.querySelector(".face-grid");
    const detailEl = root.querySelector(".face-detail");

    function esc(s) {
      return escapeHtml(String(s == null ? "" : s));
    }

    /* 双语标签：中文（日文）；译文与原文相同（未收录）时只给一个 */
    function bilingual(zh, orig) {
      const z = zh || orig || "";
      return z === orig ? esc(z) : esc(z) + "<em>（" + esc(orig || "") + "）</em>";
    }

    /* 条目展示名：style（日文发型细注）优先，缺省发型双语；都不空则回 id */
    function cardTitle(p) {
      return p.style || p.hair_zh || p.face_id;
    }
    function refName(p) {
      const main = p.style || (p.hair_zh ? p.hair_zh : p.face_id);
      return main + "（" + (p.section_zh || p.section) + "）· " + p.face_id.slice(0, 8);
    }

    /* 当前筛选来源下的形态/发型清单：「全部」时合并各库（同名累加 —— 词表
       相同则并成一行、不同则并列展示）。 */
    function sectionsFor() {
      const map = (allData && allData.sections) || {};
      if (filter.source) return map[filter.source] || [];
      const merged = [];
      const index = {};
      Object.keys(map).forEach(function (src) {
        (map[src] || []).forEach(function (s) {
          const i = index[s.id];
          if (i === undefined) {
            merged.push({ id: s.id, zh: s.zh, count: s.count });
            index[s.id] = merged.length - 1;
          } else {
            merged[i].count += s.count;
          }
        });
      });
      return merged;
    }

    function hairsFor() {
      const map = (allData && allData.hairs) || {};
      if (filter.source) return map[filter.source] || [];
      const merged = [];
      const index = {};
      Object.keys(map).forEach(function (src) {
        (map[src] || []).forEach(function (h) {
          const i = index[h.id];
          if (i === undefined) {
            merged.push({ id: h.id, zh: h.zh, count: h.count });
            index[h.id] = merged.length - 1;
          } else {
            merged[i].count += h.count;
          }
        });
      });
      return merged;
    }

    function favCount() {
      return (allData.items || []).filter(function (p) { return p.favorited; }).length;
    }

    /* 来源页签（全部 / 各库 —— 标签取 FACE_SOURCE_ZH，缺项回显 id；计数来自
       服务端 sources）。来源切换时被当前来源筛空的形态/发型自动回落「全部」
       （自建库词表可能不同）。 */
    function renderSourceTabs() {
      const tabs = root.querySelector(".face-src-tabs");
      const total = (allData.items || []).length;
      const counts = {};
      (allData.sources || []).forEach(function (s) {
        counts[s.id] = s.count;
      });
      const keys = [["", "全部", total]].concat(
        (allData.sources || []).map(function (s) {
          return [s.id, FACE_SOURCE_ZH[s.id] || s.id, counts[s.id] || 0];
        })
      );
      tabs.innerHTML = keys
        .map(function (k) {
          return (
            '<button type="button" class="seg' + (filter.source === k[0] ? " active" : "") +
            '" data-v="' + esc(k[0]) + '">' + esc(k[1]) + "<em>" + k[2] + "</em></button>"
          );
        })
        .join("");
      [...tabs.children].forEach(function (btn) {
        btn.addEventListener("click", function () {
          filter.source = btn.dataset.v;
          if (filter.section && !sectionsFor().some(function (s) { return s.id === filter.section; })) {
            filter.section = "";
          }
          if (filter.hair && !hairsFor().some(function (h) { return h.id === filter.hair; })) {
            filter.hair = "";
          }
          renderSourceTabs();
          renderSectionTabs();
          renderHairSelect();
          renderGrid();
        });
      });
    }

    function renderSectionTabs() {
      const tabs = root.querySelector(".face-sec-tabs");
      const inSource = (allData.items || []).filter(function (p) {
        return !filter.source || p.source === filter.source;
      });
      const keys = [["", "全部", inSource.length]].concat(
        sectionsFor().map(function (s) {
          return [s.id, s.zh || s.id, s.count];
        })
      );
      /* ♥ 收藏筛选 chip（形态行尾，三库归一同一位置；计数全库口径，0 收藏隐藏） */
      let html = keys
        .map(function (k) {
          return (
            '<button type="button" class="seg' + (filter.section === k[0] ? " active" : "") +
            '" data-v="' + esc(k[0]) + '">' + esc(k[1]) + "<em>" + k[2] + "</em></button>"
          );
        })
        .join("");
      const n = favCount();
      if (n) {
        html +=
          '<button type="button" class="seg face-fav-chip' + (filter.fav ? " active" : "") +
          '" data-fav="1">♥ 收藏 <em>' + n + "</em></button>";
      }
      tabs.innerHTML = html;
      [...tabs.children].forEach(function (btn) {
        if (btn.dataset.fav) {
          btn.addEventListener("click", function () {
            filter.fav = !filter.fav;
            renderSectionTabs();
            renderGrid();
          });
          return;
        }
        btn.addEventListener("click", function () {
          filter.section = btn.dataset.v;
          renderSectionTabs();
          renderGrid();
        });
      });
    }

    function renderHairSelect() {
      const sel = root.querySelector(".face-hair");
      const hairs = hairsFor();
      sel.innerHTML =
        '<option value="">全部发型</option>' +
        hairs
          .map(function (h) {
            return (
              '<option value="' + esc(h.id) + '"' + (filter.hair === h.id ? " selected" : "") +
              ">" + bilingual(h.zh, h.id) + " · " + h.count + " 条</option>"
            );
          })
          .join("");
      sel.onchange = function () {
        filter.hair = sel.value;
        renderGrid();
      };
    }

    function wireSearch() {
      const input = root.querySelector(".face-q");
      let timer = null;
      input.addEventListener("input", function () {
        clearTimeout(timer);
        timer = setTimeout(function () {
          filter.q = input.value.trim().toLowerCase();
          renderGrid();
        }, 180);
      });
    }

    /* 搜索命中：matchesQuery 共享语义（多词 AND）—— id + 发型（中/日） + style；
       haystack 本身中日双份，无需词表。 */
    function filteredFaces() {
      const items = (allData && allData.items) || [];
      return items.filter(function (p) {
        if (filter.source && p.source !== filter.source) return false;
        if (filter.section && p.section !== filter.section) return false;
        if (filter.hair && p.hair !== filter.hair) return false;
        if (filter.fav && !p.favorited) return false;
        if (filter.q) {
          const hay = (p.face_id + " " + (p.style || "") + " " + p.hair + " " + (p.hair_zh || ""));
          if (!matchesQuery(hay, filter.q)) return false;
        }
        return true;
      });
    }

    /* 卡片：3:4 图位 + mono id 段 + 展示名（style 优先，缺省发型）+ 形态角标。
       网格行的收缩红线见 style.css .face-grid 注释（overflow:hidden 卡片 +
       auto 行高会逐行压叠 —— face-card 带 overflow:visible）。 */
    function renderGrid() {
      const items = filteredFaces();
      grid.innerHTML = "";
      if (!items.length) {
        grid.innerHTML = '<div class="face-empty">没有符合条件的条目 —— 换个筛选条件</div>';
        return;
      }
      items.forEach(function (p) {
        const card = document.createElement("div");
        card.className = "image-card face-card" +
          (selectedFace && selectedFace.source === p.source && selectedFace.face_id === p.face_id ? " active" : "");
        card.innerHTML =
          '<div class="face-thumb">' +
          '<img src="' + esc(p.thumb_url) + '" alt="" loading="lazy">' +
          '<button type="button" class="face-fav' + (p.favorited ? " on" : "") +
          '" title="收藏">' + (p.favorited ? "已收藏" : "收藏") + "</button>" +
          "</div>" +
          '<div class="image-meta">' +
          '<span class="face-badge' + (p.section === "styling" ? " face-badge-sec" : "") + '">' +
          esc(p.section_zh || p.section) + "</span>" +
          '<span class="face-zh">' + esc(cardTitle(p)) + "</span>" +
          '<span class="face-jp">' + esc(p.face_id) + "</span>" +
          "</div>";
        card.addEventListener("click", function () {
          selectFace(p);
        });
        const favBtn = card.querySelector(".face-fav");
        favBtn.addEventListener("click", function (ev) {
          ev.stopPropagation();
          toggleFav(p, favBtn);
        });
        grid.appendChild(card);
      });
      if (!selectedFace || !items.some(function (p) { return p.source === selectedFace.source && p.face_id === selectedFace.face_id; })) {
        // 首次打开 / 筛选后选中项不在结果里 → 自动选中第一个（详情栏始终有内容）
        selectFace(items[0]);
      } else {
        renderDetail();
      }
    }

    /* 收藏态变化的统一出口：按钮即时态 + ♥ chip 行（0 收藏回「全部」护栏）+ 无条件
       重放网格（卡片徽章实时；筛选开着时行数变化也重画 —— 选中态由 renderGrid
       尾部带回详情重画，三处同步只走这一个出口，动作库同款）。 */
    function applyFavState(p, btn) {
      if (btn) {
        btn.classList.toggle("on", p.favorited);
        btn.textContent = p.favorited ? "已收藏" : "收藏";
      }
      const wasFiltered = filter.fav;
      if (wasFiltered && !favCount()) filter.fav = false;
      renderSectionTabs();
      renderGrid();
    }

    /* 收藏：乐观更新（先改内存状态再发请求），失败回滚。 */
    function toggleFav(p, btn) {
      const settle = function (val) {
        p.favorited = val;
        applyFavState(p, btn);
      };
      settle(!p.favorited);
      api("/faces/" + encodeURIComponent(p.source) + "/" + encodeURIComponent(p.face_id) + "/favorite", { method: "POST" })
        .then(function (res) {
          settle(!!res.favorited);
        })
        .catch(function () {
          settle(!p.favorited);
        });
    }

    function selectFace(p) {
      selectedFace = p;
      // 选中环按 filteredFaces 的顺序标（与 renderGrid 的卡片顺序一致）
      const items = filteredFaces();
      [...grid.querySelectorAll(".face-card")].forEach(function (el, i) {
        el.classList.toggle("active", !!p && items[i] && items[i].source === p.source && items[i].face_id === p.face_id);
      });
      renderDetail();
    }

    function renderDetail() {
      const p = selectedFace;
      if (!p) {
        detailEl.innerHTML = '<div class="face-empty">在左侧选择一个条目</div>';
        return;
      }
      detailEl.innerHTML =
        '<div class="face-detail-img" title="点看大图">' +
        '<img src="' + esc(p.full_url) + '" alt="' + esc(p.face_id) + '"></div>' +
        '<div class="face-detail-titles">' +
        "<h3>" + esc(cardTitle(p)) + "</h3>" +
        '<div class="face-jp">' + esc(p.face_id) + "</div>" +
        "</div>" +
        '<div class="face-meta">' + esc(FACE_SOURCE_ZH[p.source] || p.source) + " · " +
        "<strong>" + esc(p.section_zh || p.section) + "</strong>" + " · " +
        bilingual(p.hair_zh, p.hair) + " · " + p.width + "×" + p.height + "</div>" +
        '<div class="face-tags">' +
        '<button type="button" class="btn-sm face-detail-fav' + (p.favorited ? " on" : "") +
        '">' + (p.favorited ? "已收藏" : "收藏") + "</button>" +
        "</div>" +
        (onPick
          ? '<div class="face-detail-actions">' +
            '<button type="button" class="face-add-btn primary">加入参考图</button>' +
            "</div>"
          : "");
      detailEl.querySelector(".face-detail-img").addEventListener("click", function () {
        Media.showImageModal(p.full_url, refName(p));
      });
      detailEl.querySelector(".face-detail-fav").addEventListener("click", function () {
        toggleFav(p, detailEl.querySelector(".face-detail-fav"));
      });
      const addBtn = detailEl.querySelector(".face-add-btn");
      if (addBtn) {
        addBtn.addEventListener("click", function () {
          onPick({
            source: p.source,
            face_id: p.face_id,
            section: p.section,
            name: refName(p),
            url: p.thumb_url,
          });
          close();
        });
      }
    }

    api("/faces")
      .then(function (data) {
        allData = data;
        renderSourceTabs();
        renderSectionTabs();
        renderHairSelect();
        wireSearch();
        renderGrid();
        if (typeof onStat === "function") {
          const items = allData.items || [];
          const faceCount = items.filter(function (p) { return p.section === "face"; }).length;
          onStat(items.length + " 条 · 人脸 " + faceCount + " · 造型 " + (items.length - faceCount));
        }
      })
      .catch(function (err) {
        grid.innerHTML = '<div class="face-empty">加载失败：' + esc(err.message) + "</div>";
        if (typeof onStat === "function") onStat("");
      });
  }

  return { mount };
})();