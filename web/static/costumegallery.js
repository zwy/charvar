/* 服装库共享渲染：顶栏「服装」整页（costumepage.js）与后续生图流程弹层同用这一套
   —— 网格 CSS 红线、检索规则、关键词复制只修一处，别再两处分叉。容器里搭
   目录页签 + 搜索 + 卡片网格 + 常驻详情（大图 / 目录双语 / 关键词块）。
   数据源：GET /costumes（items 全量返回，客户端过滤；日文搜索词经站点 JA 词表
   扩写成英文关键词）。costume-db-01 是 t2i 架空服装（无人物、2:3）：将来接生图
   流程时语义 =「服装锚」（与 pose 的「姿势锚」互补），那时在 genrefs 加 onPick，
   整页（无参考图上下文）不渲染加图按钮。 */
const CostumeGallery = (() => {
  function mount({ root, onPick, close, onStat, variant = "modal" }) {
    let allData = null;
    const filter = { category: "", q: "" };
    let selectedItem = null;

    root.innerHTML =
      '<div class="costume-browser' + (variant === "page" ? " costume-browser-page" : "") + '">' +
      '<div class="costume-master">' +
      '<div class="costume-toolbar">' +
      '<div class="costume-cat-tabs segmented"></div>' +
      '<div class="costume-toolbar-row">' +
      '<input class="costume-q" type="text" placeholder="按关键词 / id 搜索（英文关键词、日文词、id 均可）"></input>' +
      "</div>" +
      "</div>" +
      '<div class="costume-grid image-grid"></div>' +
      "</div>" +
      '<div class="costume-detail"></div>' +
      "</div>";
    const grid = root.querySelector(".costume-grid");
    const detailEl = root.querySelector(".costume-detail");

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

    function renderTabs() {
      const tabs = root.querySelector(".costume-cat-tabs");
      const total = (allData.items || []).length;
      const keys = [["", "全部", total]].concat(
        (allData.categories || []).map(function (c) {
          return [c.id, c.zh || c.id, c.count];
        })
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

    /* 检索命中（多词 AND）：id + 英文关键词 + 中日目录名；日文词经站点词表扩写
       （词表键是词/短语都能对上——「黒」对上「黒ドレス」这种连写也走包含判断）。 */
    function matches(item) {
      if (filter.category && item.category !== filter.category) return false;
      if (filter.q) {
        const hay = (item.item_id + " " + item.keywords + " " + item.category +
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

    /* 卡片：2:3 图位 + mono id + 关键词预览；1407 张缩略图用 lazy 加载。
       网格行的收缩红线见 style.css .costume-grid 注释（同 .pose-grid）。 */
    function renderGrid() {
      const items = filteredItems();
      grid.innerHTML = "";
      if (!items.length) {
        grid.innerHTML = '<div class="costume-empty">没有符合条件的服装 —— 换个筛选条件</div>';
        return;
      }
      items.forEach(function (it) {
        const card = document.createElement("div");
        card.className = "image-card costume-card" + (selectedItem && selectedItem.item_id === it.item_id ? " active" : "");
        card.innerHTML =
          '<div class="costume-thumb"><img src="' + esc(it.thumb_url) + '" alt="" loading="lazy"></div>' +
          '<div class="image-meta">' +
          "<strong>" + esc(it.item_id) + "</strong>" +
          '<span class="costume-kw">' + esc(it.keywords) + "</span>" +
          "</div>";
        card.addEventListener("click", function () {
          selectItem(it);
        });
        grid.appendChild(card);
      });
      if (!selectedItem || !items.some(function (it) { return it.item_id === selectedItem.item_id; })) {
        // 首次打开 / 筛选后选中项不在结果里 → 自动选中第一个（详情栏始终有内容）
        selectItem(items[0]);
      } else {
        renderDetail();
      }
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

    function renderDetail() {
      const it = selectedItem;
      if (!it) {
        detailEl.innerHTML = '<div class="costume-empty">在左侧选择一套服装</div>';
        return;
      }
      detailEl.innerHTML =
        '<div class="costume-detail-img" title="点看大图">' +
        '<img src="' + esc(it.image_url) + '" alt="' + esc(it.item_id) + '"></div>' +
        '<div class="costume-detail-titles">' +
        "<h3>" + esc(it.item_id) + "</h3>" +
        '<div class="costume-jp">' + bilingual(it.category_zh, it.category) + "</div>" +
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
        (onPick
          ? '<div class="costume-detail-actions">' +
            '<button type="button" class="costume-add-btn primary">加入参考图</button>' +
            "</div>"
          : "");
      detailEl.querySelector(".costume-detail-img").addEventListener("click", function () {
        Media.showImageModal(it.image_url, it.item_id + "（" + (it.category_zh || it.category) + "）");
      });
      const copyBtn = detailEl.querySelector(".costume-kw-copy");
      if (copyBtn) {
        copyBtn.addEventListener("click", function () {
          copyText(it.keywords)
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
      const addBtn = detailEl.querySelector(".costume-add-btn");
      if (addBtn) {
        addBtn.addEventListener("click", function () {
          onPick({
            item_id: it.item_id,
            name: it.item_id + "（" + (it.category_zh || it.category) + "）",
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
        renderTabs();
        wireSearch();
        renderGrid();
        if (typeof onStat === "function") {
          onStat((allData.items || []).length + " 套服装 · " + (allData.categories || []).length + " 类");
        }
      })
      .catch(function (err) {
        grid.innerHTML = '<div class="costume-empty">加载失败：' + esc(err.message) + "</div>";
        if (typeof onStat === "function") onStat("");
      });
  }

  return { mount };
})();