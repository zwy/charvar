/* 服装库共享渲染：顶栏「服装」整页（costumepage.js）与后续生图流程弹层同用这一套
   —— 网格 CSS 红线、检索规则、关键词复制只修一处，别再两处分叉。容器里搭
   来源页签 + 目录页签 + 搜索 + 卡片网格 + 常驻详情（大图 / 徽章行 / 关键词块 /
   文本层块）。数据源：GET /costumes（items 全量返回，客户端过滤；日文搜索词经站点
   JA 词表扩写成英文关键词）。站点库条目 = t2i 架空服装（无人物、2:3、白底）；
   自建库（studio）= 平铺参考图（3:4）+ 中文标签 + 文本层（prompt_text 优先于
   keywords 进批）。展示语义 =「服装锚」（与 pose 的「姿势锚」互补）：将来接生图
   流程时在 genrefs 加 onPick，整页（无参考图上下文）不渲染加图按钮。 */
const CostumeGallery = (() => {
  function mount({ root, onPick, close, onStat, variant = "modal" }) {
    let allData = null;
    const filter = { source: "", category: "", q: "" };
    let selectedItem = null;

    root.innerHTML =
      '<div class="costume-browser' + (variant === "page" ? " costume-browser-page" : "") + '">' +
      '<div class="costume-master">' +
      '<div class="costume-toolbar">' +
      '<div class="costume-src-tabs segmented"></div>' +
      '<div class="costume-cat-tabs segmented"></div>' +
      '<div class="costume-toolbar-row">' +
      '<input class="costume-q" type="text" placeholder="按关键词 / id / 中文标签搜索（英文关键词、日文词、id、中文标签均可）">' +
      "</div>" +
      "</div>" +
      '<div class="costume-grid image-grid"></div>' +
      "</div>" +
      '<div class="costume-detail"></div>' +
      "</div>";
    const grid = root.querySelector(".costume-grid");
    const detailEl = root.querySelector(".costume-detail");

    /* 来源中文名（自建库与站点库的来源页签/徽章行都读它；未知 id 原样显示） */
    const SOURCE_ZH = { "costume-db-01": "站点库", "studio": "自建库" };

    function esc(s) {
      return escapeHtml(String(s == null ? "" : s));
    }

    function glossary() {
      return (allData && allData.glossary) || {};
    }

    /* 双语标签：中文（原文）；译文与原文相同（未收录）时只给一个 */
    function bilingual(zh, orig) {
      const z = zh || orig || "";
      return z === orig ? esc(z) : esc(z) + "<em>（" + esc(orig || "") + "）</em>";
    }

    /* 目录计数（随当前来源筛选联动；来源页签点击用它判断目录是否被筛空） */
    function catCount(category) {
      return (allData.items || []).filter(function (it) {
        return (!filter.source || it.source_id === filter.source) && it.category === category;
      }).length;
    }

    /* 来源页签（全部 / 站点库 / 自建库）：计数用总清单（不随目录级筛选联动，避免循环）。
       目录切换 / 来源切换都要重算对方页签的计数 —— renderTabs 的计数现算。 */
    function renderSourceTabs() {
      const tabs = root.querySelector(".costume-src-tabs");
      const total = (allData.items || []).length;
      const counts = {};
      (allData.sources || []).forEach(function (s) {
        counts[s.id] = s.count;
      });
      const keys = [["", "全部", total]].concat(
        ["costume-db-01", "studio"].map(function (id) {
          return [id, SOURCE_ZH[id] || id, counts[id] || 0];
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
          /* 来源切换时目录若被筛空（如站点库下点自建家族），自动回落到「全部目录」 */
          if (filter.category && !catCount(filter.category)) {
            filter.category = "";
          }
          renderSourceTabs();
          renderTabs();
          renderGrid();
        });
      });
    }

    /* 目录页签：类目与次序来自服务端（站点固定次序 + 自建家族尾部追加），
       计数现算（随来源筛选联动）；被当前来源筛空的目录页签隐藏。 */
    function renderTabs() {
      const tabs = root.querySelector(".costume-cat-tabs");
      const items = (allData.items || []).filter(function (it) {
        return !filter.source || it.source_id === filter.source;
      });
      const keys = [["", "全部", items.length]].concat(
        (allData.categories || [])
          .map(function (c) {
            const n = items.filter(function (it) { return it.category === c.id; }).length;
            return [c.id, c.zh || c.id, n];
          })
          .filter(function (k) { return k[2] > 0; })
      );
      tabs.innerHTML = keys
        .map(function (k) {
          return (
            '<button type="button" class="seg' + (filter.category === k[0] ? " active" : "") +
            '" data-v="' + esc(k[0]) + '">' + esc(k[1]) + "<em>" + k[2] + "</em></button>"
          );
        })
        .join("");
      [...tabs.children].forEach(function (btn) {
        btn.addEventListener("click", function () {
          filter.category = btn.dataset.v;
          renderTabs();
          renderGrid();
        });
      });
    }

    function wireSearch() {
      const input = root.querySelector(".costume-q");
      let timer = null;
      input.addEventListener("input", function () {
        clearTimeout(timer);
        timer = setTimeout(function () {
          filter.q = input.value.trim().toLowerCase();
          renderGrid();
        }, 180);
      });
    }

    /* 检索命中（多词 AND）：id + 中文标签 + 英文关键词 + 文本层 + 中日目录名；
       日文词经站点词表扩写（词表键是词/短语都能对上——「黒」对上「黒ドレス」这种
       连写也走包含判断）。 */
    function matches(item) {
      if (filter.source && item.source_id !== filter.source) return false;
      if (filter.category && item.category !== filter.category) return false;
      if (filter.q) {
        const hay = (item.item_id + " " + (item.label || "") + " " + item.keywords + " " +
          (item.prompt_text || "") + " " + item.category +
          " " + (item.category_zh || "")).toLowerCase();
        const g = glossary();
        const toks = filter.q.split(/\s+/).filter(Boolean);
        for (const t of toks) {
          let ok = hay.indexOf(t) !== -1;
          if (!ok) {
            for (const ja in g) {
              if (t.indexOf(ja) !== -1 && hay.indexOf(g[ja].toLowerCase()) !== -1) {
                ok = true;
                break;
              }
            }
          }
          if (!ok) return false;
        }
      }
      return true;
    }

    function filteredItems() {
      return (allData && allData.items ? allData.items : []).filter(matches);
    }

    /* 平铺条目的缩略图框是 3:4（图像本体 3:4，塞 2:3 框会上下留灰带）；站点库 2:3。 */
    function thumbClass(it) {
      return it.display_type === "flat_lay" ? " costume-thumb-34" : "";
    }

    /* 卡片：图位（站点 2:3 / 平铺 3:4）+ 中文 label（站点条目缺省回 id）+ mono id +
       平铺徽章 + 关键词预览；无图条目渲染占位（服务端对空路径发空 URL）。
       1407 张缩略图用 lazy 加载。网格行的收缩红线见 style.css .costume-grid 注释。 */
    function renderGrid() {
      const items = filteredItems();
      grid.innerHTML = "";
      if (!items.length) {
        grid.innerHTML = '<div class="costume-empty">没有符合条件的服装 —— 换个筛选条件</div>';
        return;
      }
      items.forEach(function (it) {
        const card = document.createElement("div");
        const active = selectedItem &&
          selectedItem.source_id === it.source_id && selectedItem.item_id === it.item_id;
        card.className = "image-card costume-card" + (active ? " active" : "");
        card.innerHTML =
          '<div class="costume-thumb' + thumbClass(it) + '">' +
          (it.thumb_url
            ? '<img src="' + esc(it.thumb_url) + '" alt="" loading="lazy">'
            : '<div class="costume-thumb-empty">无图 · 纯文本</div>') +
          "</div>" +
          '<div class="image-meta">' +
          '<div class="costume-card-title">' +
          (isFlat(it) ? '<span class="costume-chip">平铺</span>' : "") +
          "<strong>" + esc(it.label || it.item_id) + "</strong>" +
          "</div>" +
          (it.label
            ? '<span class="costume-card-id">' + esc(it.item_id) + "</span>"
            : "") +
          '<span class="costume-kw">' + esc(itemPreview(it)) + "</span>" +
          "</div>";
        card.addEventListener("click", function () {
          selectItem(it);
        });
        grid.appendChild(card);
      });
      if (!selectedItem || !items.some(function (it) {
        return selectedItem.source_id === it.source_id && selectedItem.item_id === it.item_id;
      })) {
        // 首次打开 / 筛选后选中项不在结果里 → 自动选中第一个（详情栏始终有内容）
        selectItem(items[0]);
      } else {
        renderDetail();
      }
    }

    function isFlat(it) {
      return it.display_type === "flat_lay";
    }

    /* 卡片预览行：关键词先行（站库与自建库通用的提示词原料）；无关键词时回退文本层。 */
    function itemPreview(it) {
      return it.keywords || it.prompt_text || "";
    }

    function selectItem(it) {
      selectedItem = it;
      const items = filteredItems();
      [...grid.querySelectorAll(".costume-card")].forEach(function (el, i) {
        el.classList.toggle("active", !!it && items[i] && items[i].item_id === it.item_id);
      });
      renderDetail();
    }

    /* 复制关键词：clipboard API 优先，藏 textarea + execCommand 兜底 */
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

    function wireCopy(btn, text) {
      if (!btn) return;
      btn.addEventListener("click", function () {
        copyText(text)
          .then(function () {
            btn.textContent = "已复制";
            btn.disabled = true;
            setTimeout(function () {
              btn.textContent = "复制";
              btn.disabled = false;
            }, 1200);
          })
          .catch(function () {
            btn.textContent = "复制失败";
            setTimeout(function () {
              btn.textContent = "复制";
            }, 1200);
          });
      });
    }

    /* 详情栏：大图（无图条目 = 占位）+ 标题（中文 label，站点缺省回 id）+ 目录/家族双语
       + 徽章行（来源 / 展示形态 / 无图纯文本）+ 关键词块（站点库原料）+ 文本层块
       （自建库才有；prompt_source 手工维护 / AI 起稿）+ 参考图动作（弹层用）。 */
    function renderDetail() {
      const it = selectedItem;
      if (!it) {
        detailEl.innerHTML = '<div class="costume-empty">在左侧选择一套服装</div>';
        return;
      }
      const displayChip = isFlat(it)
        ? '<span class="costume-chip">平铺</span>'
        : '<span class="costume-chip costume-chip-muted">隐形模特</span>';
      const sourceChip = '<span class="costume-chip costume-chip-muted">' +
        esc(SOURCE_ZH[it.source_id] || it.source_id) + "</span>";
      detailEl.innerHTML =
        (it.image_url
          ? '<div class="costume-detail-img" title="点看大图">' +
            '<img src="' + esc(it.image_url) + '" alt="' + esc(it.item_id) + '"></div>'
          : '<div class="costume-detail-img costume-detail-noimg">' +
            '<div class="costume-thumb-empty">无图 · 纯文本条目<br>（换装走文本通路）</div></div>') +
        '<div class="costume-detail-titles">' +
        "<h3>" + esc(it.label || it.item_id) + "</h3>" +
        '<div class="costume-jp">' + bilingual(it.category_zh, it.category) + "</div>" +
        '<div class="costume-tags">' + sourceChip + displayChip +
        (it.image_url
          ? ""
          : '<span class="costume-chip costume-chip-muted">无图 · 纯文本</span>') +
        "</div>" +
        "</div>" +
        '<div class="costume-kw-block">' +
        '<div class="costume-kw-head">' +
        '<span class="costume-kw-label">服装关键词</span>' +
        (it.keywords ? '<button type="button" class="btn-sm costume-kw-copy">复制</button>' : "") +
        "</div>" +
        '<div class="costume-kw-text">' +
        (it.keywords ? esc(it.keywords) : '<span class="costume-kw-muted">这条没有关键词描述</span>') +
        "</div>" +
        "</div>" +
        (it.prompt_text
          ? '<div class="costume-kw-block">' +
            '<div class="costume-kw-head">' +
            '<span class="costume-kw-label">文生图提示词（文本层）</span>' +
            '<button type="button" class="btn-sm costume-pt-copy">复制</button>' +
            "</div>" +
            '<div class="costume-pt-src">' +
            (it.prompt_source === "ai_draft" ? "AI 起稿" : "手工维护") +
            "</div>" +
            '<div class="costume-kw-text">' + esc(it.prompt_text) + "</div>" +
            "</div>"
          : "");
      if (it.image_url) {
        detailEl.querySelector(".costume-detail-img").addEventListener("click", function () {
          Media.showImageModal(it.image_url, (it.label || it.item_id) +
            "（" + (it.category_zh || it.category) + "）");
        });
      }
      wireCopy(detailEl.querySelector(".costume-kw-copy"), it.keywords);
      wireCopy(detailEl.querySelector(".costume-pt-copy"), it.prompt_text);
      const addBtn = detailEl.querySelector(".costume-add-btn");
      if (addBtn) {
        addBtn.addEventListener("click", function () {
          onPick({
            item_id: it.item_id,
            name: (it.label || it.item_id) + "（" + (it.category_zh || it.category) + "）",
            keywords: it.keywords,
            url: it.thumb_url,
          });
          close();
        });
      }
    }

    api("/costumes")
      .then(function (data) {
        allData = data;
        renderSourceTabs();
        renderTabs();
        wireSearch();
        renderGrid();
        if (typeof onStat === "function") {
          const src = (allData.sources || []).map(function (s) {
            return (SOURCE_ZH[s.id] || s.id) + " " + s.count;
          }).join(" · ");
          onStat("共 " + (allData.items || []).length + " 套" + (src ? "（" + src + "）" : ""));
        }
      })
      .catch(function (err) {
        grid.innerHTML = '<div class="costume-empty">加载失败：' + esc(err.message) + "</div>";
        if (typeof onStat === "function") onStat("");
      });
  }

  return { mount };
})();
