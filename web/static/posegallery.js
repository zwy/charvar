/* 动作库共享渲染：genrefs.js 的弹层与顶栏「动作」整页（posepage.js）同用这一套
   —— 网格 CSS 红线、双语规则、筛选交互只修一处，别再两处分叉。容器里搭
   系統页签 + 分類/搜索 + 卡片网格 + 常驻详情（双语标题 / 系統·分類 / 视角切换 /
   动作描述块）。中日对照：中文为主阅读层级、日文原文 muted 小字。
   动作描述（pose_actions 表：动作×视角 双键，文本挂 images[] 每视角）有 → 卡片带「描述」角标、详情块跟所选视角带「复制」；
   无 → 详情块给灰字空态。整页没有参考图上下文，不渲染「加入参考图」。 */
const PoseGallery = (() => {
  function mount({ root, onPick, close, onStat, variant = "modal" }) {
    let allData = null;
    const filter = { line: "", category: "", q: "" };
    let selectedPose = null;
    let selectedAngle = null;

    root.innerHTML =
      '<div class="pose-browser' + (variant === "page" ? " pose-browser-page" : "") + '">' +
      '<div class="pose-master">' +
      '<div class="pose-toolbar">' +
      '<div class="pose-line-tabs segmented"></div>' +
      '<div class="pose-toolbar-row">' +
      '<select class="pose-cat"></select>' +
      '<input class="pose-q" type="text" placeholder="按动作名 / 编号 / id 搜索（中/日/编号均可）"></input>' +
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

    function renderTabs() {
      const tabs = root.querySelector(".pose-line-tabs");
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
      const sel = root.querySelector(".pose-cat");
      const cats = allData.categories || [];
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

    /* 搜索命中：中日动作名 + 编号 + id + 动作描述（pose_actions 的文本） */
    function filteredPoses() {
      const poses = (allData && allData.poses) || [];
      return poses.filter(function (p) {
        if (filter.line && p.line !== filter.line) return false;
        if (filter.category && p.category !== filter.category) return false;
        if (filter.q) {
          const hay = (p.title + " " + (p.title_zh || "") + " " + p.number + " " + p.pose_id +
            " " + (p.images || []).map(function (im) { return im.action_text || ""; }).join(" ")).toLowerCase();
          if (hay.indexOf(filter.q) === -1) return false;
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
        grid.innerHTML = '<div class="pose-empty">没有符合条件的动作 —— 换个筛选条件</div>';
        return;
      }
      poses.forEach(function (p) {
        const front = (p.images && p.images[0]) || {};
        const hasText = (p.images || []).some(function (im) { return String(im.action_text || "").trim(); });
        const card = document.createElement("div");
        card.className = "image-card pose-card" + (selectedPose && selectedPose.pose_id === p.pose_id ? " active" : "");
        card.innerHTML =
          (hasText ? '<span class="pose-desc-badge">描述</span>' : "") +
          '<div class="pose-thumb"><img src="' + esc(front.thumb_url) + '" alt=""></div>' +
          '<div class="image-meta">' +
          "<strong>" + esc(p.number) + "</strong>" +
          '<span class="pose-zh">' + esc(p.title_zh || p.title) + "</span>" +
          '<span class="pose-jp">' + esc(p.title) + "</span>" +
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
        '<div class="pose-meta">' + bilingual(p.line_zh, p.line) + " · " +
        (p.category ? bilingual(p.category_zh, p.category) : "-") + "</div>" +
        '<div class="pose-angle-block">' +
        '<div class="pose-angle-label">选择视角</div>' +
        '<div class="pose-angle-grid"></div>' +
        "</div>" +
        '<div class="pose-action-block">' +
        '<div class="pose-action-head">' +
        '<span class="pose-angle-label">动作描述</span>' +
        (actionText ? '<button type="button" class="btn-sm pose-action-copy">复制</button>' : "") +
        "</div>" +
        '<div class="pose-action-text">' +
        (actionText ? esc(actionText) : '<span class="pose-action-muted">还没有动作描述</span>') +
        "</div>" +
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
      const addBtn = detailEl.querySelector(".pose-add-btn");
      if (addBtn) {
        addBtn.addEventListener("click", function () {
          onPick({
            pose_id: p.pose_id,
            angle: selectedAngle.angle,
            name: p.number + " " + (p.title_zh || p.title) + "（" + p.title + "）· " + selectedAngle.angle_zh,
            url: selectedAngle.thumb_url,
          });
          close();
        });
      }
    }

    api("/poses")
      .then(function (data) {
        allData = data;
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
