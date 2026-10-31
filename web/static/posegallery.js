/* 动作库共享渲染：genrefs.js 的弹层与顶栏「动作」整页（posepage.js）同用这一套
   —— 网格 CSS 红线、双语规则、筛选交互只修一处，别再两处分叉。多库结构：
   来源页签（全部 / 线稿 pose-db-01 / 深度 pose-db-02 —— 同作者姊妹站、同一
   pose 集同 id 共库分键；未来新库/自建库加 source 即可）→ 系統页签 → 分類/
   搜索 → 卡片网格 + 常驻详情（双语标题 / 系統·分類 / 视角切换 / 动作描述块 /
   收藏钮）。中日对照：中文为主阅读层级、日文原文 muted 小字。
   动作描述（pose_actions 表：库×动作×视角 键，文本挂 images[] 每视角）详情块
   跟所选视角「复制 / 编辑」（textarea + PUT /poses/{source}/{id}/action）；
   收藏（pose_favorites 用户层，重灌不碰，按库分键）对齐服饰库：卡片右上
   「收藏 / 已收藏」徽章（点了不选卡）+ ♥ 收藏页签（0 收藏时隐藏）+ 详情钮，
   卡片↔详情同步。整页没有参考图上下文，不渲染「加入参考图」。 */
const PoseGallery = (() => {
  /* 来源中文标签（与后端 POSE_SOURCES 的 zh 同步维护） */
  const POSE_SOURCE_ZH = { "pose-db-01": "线稿", "pose-db-02": "深度" };
  function mount({ root, onPick, close, onStat, variant = "modal" }) {
    let allData = null;
    const filter = { source: "", line: "", category: "", q: "", fav: false };
    let selectedPose = null;
    let selectedAngle = null;
    let editingAction = false;  /* 动作描述编辑态：textarea + 保存/取消，只对当前条目×视角 */

    root.innerHTML =
      '<div class="pose-browser' + (variant === "page" ? " pose-browser-page" : "") + '">' +
      '<div class="pose-master">' +
      '<div class="pose-toolbar">' +
      '<div class="pose-src-tabs segmented"></div>' +
      '<div class="pose-line-tabs segmented"></div>' +
      '<div class="pose-toolbar-row">' +
      '<select class="pose-cat"></select>' +
      '<input class="pose-q" type="search" placeholder="按动作名 / 编号 / 动作描述 搜索（中/日/编号均可）"></input>' +
      "</div>" +
      "</div>" +
      '<div class="pose-grid image-grid"></div>' +
      "</div>" +
      '<div class="pose-detail"></div>' +
      "</div>";
    const grid = root.querySelector(".pose-grid");
    const detailEl = root.querySelector(".pose-detail");

    function esc(s) {
      return escapeHtml(String(s == null ? "" : s));
    }

    /* 双语标签：中文（日文）；译文与原文相同（未收录）时只给一个 */
    function bilingual(zh, orig) {
      const z = zh || orig || "";
      return z === orig ? esc(z) : esc(z) + "<em>（" + esc(orig || "") + "）</em>";
    }

    /* 当前筛选来源下的系統/分類清单：「全部」时合并各库（同名累加 —— 同作者
       姊妹站词表相同）；「全部」合并时以线稿库的清单次序为准（先插入先排）。 */
    function linesFor() {
      const map = (allData && allData.lines) || {};
      if (filter.source) return map[filter.source] || [];
      const merged = [];
      const index = {};
      Object.keys(map).forEach(function (src) {
        (map[src] || []).forEach(function (l) {
          const i = index[l.id];
          if (i === undefined) {
            merged.push({ id: l.id, zh: l.zh, poses: l.poses });
            index[l.id] = merged.length - 1;
          } else {
            merged[i].poses += l.poses;
          }
        });
      });
      return merged;
    }

    function categoriesFor() {
      const map = (allData && allData.categories) || {};
      if (filter.source) return map[filter.source] || [];
      const merged = [];
      const index = {};
      Object.keys(map).forEach(function (src) {
        (map[src] || []).forEach(function (c) {
          const i = index[c.id];
          if (i === undefined) {
            merged.push({ id: c.id, zh: c.zh, poses: c.poses });
            index[c.id] = merged.length - 1;
          } else {
            merged[i].poses += c.poses;
          }
        });
      });
      merged.sort(function (a, b) {
        return (b.poses - a.poses) || String(a.id).localeCompare(String(b.id));
      });
      return merged;
    }

    function favCount() {
      return (allData.poses || []).filter(function (p) { return p.favorited; }).length;
    }

    /* 来源页签（全部 / 线稿 / 深度 —— 标签取 POSE_SOURCE_ZH，缺项回显 id；计数
       来自服务端 sources 计数）。来源切换时被当前来源筛空的系統/分類自动回落
       「全部」（自建库词表可能不同）。 */
    function renderSourceTabs() {
      const tabs = root.querySelector(".pose-src-tabs");
      const total = (allData.poses || []).length;
      const counts = {};
      (allData.sources || []).forEach(function (s) {
        counts[s.id] = s.poses;
      });
      const keys = [["", "全部", total]].concat(
        (allData.sources || []).map(function (s) {
          return [s.id, POSE_SOURCE_ZH[s.id] || s.id, counts[s.id] || 0];
        })
      );
      let html = keys
        .map(function (k) {
          return (
            '<button type="button" class="seg' + (filter.source === k[0] ? " active" : "") +
            '" data-v="' + esc(k[0]) + '">' + esc(k[1]) + "<em>" + k[2] + "</em></button>"
          );
        })
        .join("");
      tabs.innerHTML = html;
      [...tabs.children].forEach(function (btn) {
        btn.addEventListener("click", function () {
          filter.source = btn.dataset.v;
          if (filter.line && !linesFor().some(function (l) { return l.id === filter.line; })) {
            filter.line = "";
          }
          if (filter.category && !categoriesFor().some(function (c) { return c.id === filter.category; })) {
            filter.category = "";
          }
          renderSourceTabs();
          renderTabs();
          renderCategorySelect();
          renderGrid();
        });
      });
    }

    function renderTabs() {
      const tabs = root.querySelector(".pose-line-tabs");
      const inSource = (allData.poses || []).filter(function (p) {
        return !filter.source || p.source === filter.source;
      });
      const keys = [["", "全部", inSource.length]].concat(
        linesFor().map(function (l) {
          return [l.id, l.zh || l.id, l.poses];
        })
      );
      let html = keys
        .map(function (k) {
          return (
            '<button type="button" class="seg' + (filter.line === k[0] ? " active" : "") +
            '" data-v="' + esc(k[0]) + '">' + esc(k[1]) + "<em>" + k[2] + "</em></button>"
          );
        })
        .join("");
      const n = favCount();
      if (n) {
        html +=
          '<button type="button" class="seg pose-fav-chip' + (filter.fav ? " active" : "") +
          '" data-fav="1">♥ 收藏 <em>' + n + "</em></button>";
      }
      tabs.innerHTML = html;
      [...tabs.children].forEach(function (btn) {
        if (btn.dataset.fav) {
          btn.addEventListener("click", function () {
            filter.fav = !filter.fav;
            renderTabs();
            renderGrid();
          });
          return;
        }
        btn.addEventListener("click", function () {
          filter.line = btn.dataset.v;
          renderTabs();
          renderGrid();
        });
      });
    }

    function renderCategorySelect() {
      const sel = root.querySelector(".pose-cat");
      const cats = categoriesFor();
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
        renderGrid();
      };
    }

    function wireSearch() {
      const input = root.querySelector(".pose-q");
      let timer = null;
      input.addEventListener("input", function () {
        clearTimeout(timer);
        timer = setTimeout(function () {
          filter.q = input.value.trim().toLowerCase();
          renderGrid();
        }, 180);
      });
    }

    /* 搜索命中：matchesQuery 共享语义（多词 AND）—— 中日动作名 + 编号 + id +
       动作描述（pose_actions 的文本）；haystack 本身中日双份，无需词表。 */
    function filteredPoses() {
      const poses = (allData && allData.poses) || [];
      return poses.filter(function (p) {
        if (filter.source && p.source !== filter.source) return false;
        if (filter.line && p.line !== filter.line) return false;
        if (filter.category && p.category !== filter.category) return false;
        if (filter.fav && !p.favorited) return false;
        if (filter.q) {
          const hay = (p.title + " " + (p.title_zh || "") + " " + p.number + " " + p.pose_id +
            " " + (p.images || []).map(function (im) { return im.action_text || ""; }).join(" "));
          if (!matchesQuery(hay, filter.q)) return false;
        }
        return true;
      });
    }

    /* 卡片：3:4 图位 + mono 编号 + 中文（主阅读）+ 日文 muted；写过动作描述加角标。
       网格行的收缩红线见 style.css .pose-grid 注释（overflow:hidden 卡片 + auto 行高）。 */
    function renderGrid() {
      const poses = filteredPoses();
      grid.innerHTML = "";
      if (!poses.length) {
        // 空库（还没初始化）与「筛完没结果」是两回事，别都写成「换个筛选条件」——
        // 前者要给出下载+灌库的命令，否则新克隆的用户不知道去哪儿初始化。
        grid.innerHTML = (allData && (allData.poses || []).length)
          ? '<div class="pose-empty">没有符合条件的动作 —— 换个筛选条件</div>'
          : '<div class="pose-empty">姿势库还没初始化 —— 先跑 <code>scripts/download_pose_db.py</code> 下载，'
            + '再 <code>charvar pose import</code> 灌库</div>';
        return;
      }
      poses.forEach(function (p) {
        const front = (p.images && p.images[0]) || {};
        const card = document.createElement("div");
        card.className = "image-card pose-card" + (selectedPose && selectedPose.source === p.source && selectedPose.pose_id === p.pose_id ? " active" : "");
        card.innerHTML =
          '<div class="pose-thumb">' +
          '<img src="' + esc(front.thumb_url) + '" alt="" loading="lazy">' +
          '<button type="button" class="pose-fav' + (p.favorited ? " on" : "") +
          '" title="收藏">' + (p.favorited ? "已收藏" : "收藏") + "</button>" +
          "</div>" +
          '<div class="image-meta">' +
          "<strong>" + esc(p.number) + "</strong>" +
          '<span class="pose-badge pose-badge-muted">' + esc(POSE_SOURCE_ZH[p.source] || p.source) + "</span>" +
          '<span class="pose-zh">' + esc(p.title_zh || p.title) + "</span>" +
          '<span class="pose-jp">' + esc(p.title) + "</span>" +
          "</div>";
        card.addEventListener("click", function () {
          selectPose(p);
        });
        const favBtn = card.querySelector(".pose-fav");
        favBtn.addEventListener("click", function (ev) {
          ev.stopPropagation();
          toggleFav(p, favBtn);
        });
        grid.appendChild(card);
      });
      if (!selectedPose || !poses.some(function (p) { return p.source === selectedPose.source && p.pose_id === selectedPose.pose_id; })) {
        // 首次打开 / 筛选后选中项不在结果里 → 自动选中第一个（详情栏始终有内容）
        selectPose(poses[0]);
      } else {
        renderDetail();
      }
    }

    /* 收藏态变化的统一出口：按钮即时态 + 页签行（0 收藏回全部护栏）+ 无条件重放
       网格（卡片徽章实时；筛选开着时行数变化也重画 —— 选中态由 renderGrid 尾部
       带回详情重画，三处同步只走这一个出口）。 */
    function applyFavState(p, btn) {
      if (btn) {
        btn.classList.toggle("on", p.favorited);
        btn.textContent = p.favorited ? "已收藏" : "收藏";
      }
      const wasFiltered = filter.fav;
      if (wasFiltered && !favCount()) filter.fav = false;
      renderTabs();
      renderGrid();
    }

    /* 收藏：乐观更新（先改内存状态再发请求），失败回滚。 */
    function toggleFav(p, btn) {
      const settle = function (val) {
        p.favorited = val;
        applyFavState(p, btn);
      };
      settle(!p.favorited);
      api("/poses/" + encodeURIComponent(p.source) + "/" + encodeURIComponent(p.pose_id) + "/favorite", { method: "POST" })
        .then(function (res) {
          settle(!!res.favorited);
        })
        .catch(function () {
          settle(!p.favorited);
        });
    }


    function selectPose(p) {
      selectedPose = p;
      selectedAngle = (p.images && p.images[0]) || null;
      editingAction = false;  /* 换动作退出编辑（草稿不跨条目） */
      // 选中环按 filteredPoses 的顺序标（与 renderGrid 的卡片顺序一致）
      const poses = filteredPoses();
      [...grid.querySelectorAll(".pose-card")].forEach(function (el, i) {
        el.classList.toggle("active", !!p && poses[i] && poses[i].source === p.source && poses[i].pose_id === p.pose_id);
      });
      renderDetail();
    }

    /* 复制动作描述：clipboard API 优先，藏 textarea + execCommand 兜底 */
    function copyText(text) {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        return navigator.clipboard.writeText(text);
      }
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      try {
        document.execCommand("copy");
      } finally {
        ta.remove();
      }
      return Promise.resolve();
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
      const actionText = selectedAngle ? String(selectedAngle.action_text || "").trim() : "";
      detailEl.innerHTML =
        '<div class="pose-detail-img" title="点看大图">' +
        '<img src="' + esc(selectedAngle.thumb_url) + '" alt="' + esc(p.number) + '"></div>' +
        '<div class="pose-detail-titles">' +
        "<h3>" + esc(p.number) + " " + esc(p.title_zh || p.title) + "</h3>" +
        '<div class="pose-jp">' + esc(p.title) + "</div>" +
        "</div>" +
        '<div class="pose-meta">' + esc(POSE_SOURCE_ZH[p.source] || p.source) + " · " +
        bilingual(p.line_zh, p.line) + " · " +
        (p.category ? bilingual(p.category_zh, p.category) : "-") + "</div>" +
        '<div class="pose-tags">' +
        '<button type="button" class="btn-sm pose-detail-fav' + (p.favorited ? " on" : "") +
        '">' + (p.favorited ? "已收藏" : "收藏") + "</button>" +
        "</div>" +
        '<div class="pose-angle-block">' +
        '<div class="pose-angle-label">选择视角</div>' +
        '<div class="pose-angle-grid"></div>' +
        "</div>" +
        '<div class="pose-action-block">' +
        '<div class="pose-action-head">' +
        '<span class="pose-angle-label">动作描述 · ' + esc(selectedAngle.angle_zh) + "</span>" +
        '<span class="pose-action-btns">' +
        (editingAction
          ? ""
          : (actionText ? '<button type="button" class="btn-sm pose-action-copy">复制</button>' : "") +
            '<button type="button" class="btn-sm pose-action-edit">编辑</button>') +
        "</span>" +
        "</div>" +
        (editingAction
          ? '<textarea class="pose-edit-ta" rows="5">' + esc(actionText) + "</textarea>" +
            '<div class="pose-edit-row">' +
            '<button type="button" class="btn-sm pose-action-save">保存</button>' +
            '<button type="button" class="btn-sm pose-action-cancel">取消</button>' +
            "</div>"
          : '<div class="pose-action-text">' +
            (actionText ? esc(actionText) : '<span class="pose-action-muted">还没有动作描述</span>') +
            "</div>") +
        "</div>" +
        (onPick
          ? '<div class="pose-detail-actions">' +
            '<button type="button" class="pose-add-btn primary">加入参考图</button>' +
            "</div>"
          : "");
      detailEl.querySelector(".pose-detail-img").addEventListener("click", function () {
        Media.showImageModal(selectedAngle.full_url, p.number + " " + (p.title_zh || p.title) + "（" + p.title + "）");
      });
      const angleBox = detailEl.querySelector(".pose-angle-grid");
      images.forEach(function (im) {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "btn-sm" + (selectedAngle.angle === im.angle ? " active" : "");
        btn.textContent = im.angle_zh;
        btn.addEventListener("click", function () {
          selectedAngle = im;
          editingAction = false;  /* 换视角退出编辑（描述逐视角写，草稿不跨视角） */
          renderDetail();
        });
        angleBox.appendChild(btn);
      });
      const copyBtn = detailEl.querySelector(".pose-action-copy");
      if (copyBtn) {
        copyBtn.addEventListener("click", function () {
          copyText(actionText)
            .then(function () {
              copyBtn.textContent = "已复制";
              copyBtn.disabled = true;
              setTimeout(function () {
                copyBtn.textContent = "复制";
                copyBtn.disabled = false;
              }, 1200);
            })
            .catch(function () {
              copyBtn.textContent = "复制失败";
              setTimeout(function () {
                copyBtn.textContent = "复制";
              }, 1200);
            });
        });
      }
      const editBtn = detailEl.querySelector(".pose-action-edit");
      if (editBtn) {
        editBtn.addEventListener("click", function () {
          editingAction = true;
          renderDetail();
        });
      }
      const saveBtn = detailEl.querySelector(".pose-action-save");
      if (saveBtn) {
        saveBtn.addEventListener("click", function () {
          const value = detailEl.querySelector(".pose-edit-ta").value;
          const angle = selectedAngle.angle;
          saveBtn.disabled = true;
          saveBtn.textContent = "保存中…";
            api("/poses/" + encodeURIComponent(p.source) + "/" + encodeURIComponent(p.pose_id) + "/action", {
            method: "PUT",
            body: JSON.stringify({ angle: angle, action_text: value }),
          })
            .then(function (resp) {
              selectedAngle.action_text = resp.action_text || "";
              editingAction = false;
              renderDetail();
            })
            .catch(function (err) {
              saveBtn.disabled = false;
              saveBtn.textContent = "保存失败：" + err.message;
            });
        });
      }
      const cancelBtn = detailEl.querySelector(".pose-action-cancel");
      if (cancelBtn) {
        cancelBtn.addEventListener("click", function () {
          editingAction = false;
          renderDetail();
        });
      }
      const addBtn = detailEl.querySelector(".pose-add-btn");
      if (addBtn) {
        addBtn.addEventListener("click", function () {
          onPick({
            source: p.source,
            pose_id: p.pose_id,
            angle: selectedAngle.angle,
            name: p.number + " " + (p.title_zh || p.title) + "（" + p.title + "）· " + selectedAngle.angle_zh,
            url: selectedAngle.thumb_url,
          });
          close();
        });
      }
      detailEl.querySelector(".pose-detail-fav").addEventListener("click", function () {
        toggleFav(p, detailEl.querySelector(".pose-detail-fav"));
      });
    }

    api("/poses")
      .then(function (data) {
        allData = data;
        renderSourceTabs();
        renderTabs();
        renderCategorySelect();
        wireSearch();
        renderGrid();
        if (typeof onStat === "function") {
          const poses = allData.poses || [];
          const images = poses.reduce(function (n, p) {
            return n + (p.images || []).length;
          }, 0);
          onStat(poses.length + " 动作 · " + images + " 张");
        }
      })
      .catch(function (err) {
        grid.innerHTML = '<div class="pose-empty">加载失败：' + esc(err.message) + "</div>";
        if (typeof onStat === "function") onStat("");
      });
  }

  return { mount };
})();
