/* 服饰库共享渲染：顶栏「服饰」整页（costumepage.js）与后续生图流程弹层同用这一套
   —— 网格 CSS 红线、检索规则、关键词复制只修一处，别再两处分叉。容器里搭
   来源页签 + 目录页签（行尾 ♥ 收藏筛选）+ 搜索 + 卡片网格 + 常驻详情（大图 / 徽章行 /
   换装语义 / 关键词块 / 文本层块 / 制图提示词块 + 重生成）。数据源：GET /costumes
   （items 全量返回，客户端过滤；日文搜索词经站点 JA 词表扩写成英文关键词）。
   站点库条目 = t2i 架空服饰（无人物、2:3、白底）；自建库（studio）= 平铺参考图
   （3:4）+ 中文标签 + 三层文本（keywords 短清单 / prompt_text 服饰段 / ref_prompt
   制图提示词）。展示语义 =「服装锚」（与 pose 的「姿势锚」互补）：将来接生图
   流程时在 genrefs 加 onPick，整页（无参考图上下文）不渲染加图按钮。 */

const CostumeGallery = (() => {
  function mount({ root, onPick, close, onStat, variant = "modal" }) {
    let allData = null;
    const filter = { source: "", category: "", q: "", fav: false };
    let selectedItem = null;

    root.innerHTML =
      '<div class="costume-browser' + (variant === "page" ? " costume-browser-page" : "") + '">' +
      '<div class="costume-master">' +
      '<div class="costume-toolbar">' +
      '<div class="costume-src-tabs segmented"></div>' +
      '<div class="costume-cat-tabs segmented"></div>' +
      '<div class="costume-toolbar-row">' +
      '<input class="costume-q" type="search" placeholder="按关键词 / id / 中文标签搜索（中/英/日文均可）">' +
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

    /* 换装语义显示名（subject_policy.clothing；空策略 = 换装工作流默认整套替换） */
    const POLICY_ZH = {
      "replace": "整套替换",
      "keep": "仅换配件（保留衣物）",
      "add": "叠加穿搭",
    };

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

    function favCount() {
      return (allData.items || []).filter(function (it) { return it.favorited; }).length;
    }

    /* 来源页签（全部 / 站点库 / 自建库）。♥ 收藏筛选 chip 不在这行 —— 三库归一後
       挂在第二行（目录页签）行尾，见 renderTabs。 */
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
        btn.addEventListener("click", function (ev) {
          filter.source = btn.dataset.v;
          /* 来源切换时目录若被筛空（如站点库下点自建家族），自动回落「全部目录」 */
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
       计数现算（随来源筛选联动）；被当前来源筛空的目录页签隐藏。行尾挂 ♥ 收藏
       筛选 chip（计数现算；0 收藏时隐藏——空筛选 chip 没有存在意义；三库归一
       都放内容维度行尾）。 */
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
      let html = keys
        .map(function (k) {
          return (
            '<button type="button" class="seg' + (filter.category === k[0] ? " active" : "") +
            '" data-v="' + esc(k[0]) + '">' + esc(k[1]) + '<em>' + k[2] + '</em></button>'
          );
        })
        .join("");
      const n = favCount();
      if (n) {
        html +=
          '<button type="button" class="seg costume-fav-chip' + (filter.fav ? " active" : "") +
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
      if (filter.fav && !item.favorited) return false;
      if (filter.q) {
        const hay = (item.item_id + " " + (item.label || "") + " " + item.keywords + " " +
          (item.prompt_text || "") + " " + item.category +
          " " + (item.category_zh || ""));
        if (!matchesQuery(hay, filter.q, glossary())) return false;
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

    /* 卡片：图位（站点 2:3 / 平铺 3:4）+ 收藏星标（右上角，点了不选卡）+ 中文 label
       （站点条目缺省回 id）+ mono id + 平铺徽章 + 关键词预览；无图条目渲染占位
       （服务端对空路径发空 URL）。1407 张缩略图用 lazy 加载。网格行的收缩红线见
       style.css .costume-grid 注释。 */
    function renderGrid() {
      const items = filteredItems();
      grid.innerHTML = "";
      if (!items.length) {
        grid.innerHTML = '<div class="costume-empty">没有符合条件的服饰 —— 换个筛选条件</div>';
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
          '<button type="button" class="costume-fav' + (it.favorited ? " on" : "") +
          '" title="收藏">' + (it.favorited ? "已收藏" : "收藏") + "</button>" +
          "</div>" +
          '<div class="image-meta">' +
          '<div class="costume-card-title">' +
          (isFlat(it) ? '<span class="costume-badge">平铺</span>' : "") +
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
        const favBtn = card.querySelector(".costume-fav");
        favBtn.addEventListener("click", function (ev) {
          ev.stopPropagation();
          toggleFav(it, favBtn);
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

    /* 卡片预览行：关键词先行；无关键词回退文本层。 */
    function itemPreview(it) {
      return it.keywords || it.prompt_text || "";
    }

    /* 收藏：乐观更新（先改内存状态再发请求），失败回滚 + 红角标提示。收藏态
       变化的统一出口 = ♥ chip（目录行尾）+ 网格 + 选中项详情；收藏筛选开着且全库
       归零时自动回「全部」（三库同款护栏，防止 chip 隐藏后困在空筛选里）。 */
    function toggleFav(it, btn) {
      const want = !it.favorited;
      const settle = function (val) {
        it.favorited = val;
        btn.classList.toggle("on", val);
        btn.textContent = val ? "已收藏" : "收藏";
        const wasFiltered = filter.fav;
        if (wasFiltered && !favCount()) filter.fav = false;
        renderTabs();
        renderGrid();  /* 卡片徽章实时；筛选开着时行数变化也要重算 */
        if (selectedItem === it) renderDetail();
      };
      settle(want);
      api("/costumes/" + encodeURIComponent(it.source_id) + "/" + encodeURIComponent(it.item_id) + "/favorite", { method: "POST" })
        .then(function (res) {
          settle(!!res.favorited);
        })
        .catch(function () {
          settle(!want);  /* 回滚到收藏前的状态 */
        });
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

    function selectItem(it) {
      selectedItem = it;
      const items = filteredItems();
      [...grid.querySelectorAll(".costume-card")].forEach(function (el, i) {
        el.classList.toggle("active", !!it && items[i] && items[i].item_id === it.item_id);
      });
      renderDetail();
    }

    /* ---------- 编辑（一次一个块）：三个文本字段共用一套 ---------- */
    const EDIT_FIELDS = { "keywords": "keywords", "prompt-text": "prompt_text", "ref-prompt": "ref_prompt" };
    const EDIT_TITLES = { "keywords": "服饰关键词", "prompt-text": "文生图提示词（服饰段）", "ref-prompt": "平铺制图提示词" };
    let editingKind = null;

    function isStudio(it) {
      return it.source_id === "studio";
    }

    function updateItem(it, changes) {
      Object.assign(it, changes);
      /* 卡片预览行也依赖 keywords/prompt_text —— 换了要重画 */
      renderGrid();
      renderDetail();
    }

    function saveEdit(kind, it, value, btn) {
      const field = EDIT_FIELDS[kind];
      const body = kind === "prompt-text"
        ? { text: value }
        : kind === "keywords" ? { keywords: value } : { ref_prompt: value };
      btn.disabled = true;
      btn.textContent = "保存中…";
      api("/costumes/" + encodeURIComponent(it.source_id) + "/" + encodeURIComponent(it.item_id) + "/" + kind, {
        method: "PUT",
        body: JSON.stringify(body),
      })
        .then(function () {
          editingKind = null;
          const changes = {};
          changes[field] = value;
          updateItem(it, changes);
        })
        .catch(function (err) {
          btn.disabled = false;
          btn.textContent = "保存失败：" + err.message;
        });
    }

    /* 详情栏：大图（无图 = 占位）+ 标题 + 目录/家族双语 + 徽章行（来源 / 展示形态 /
       无图纯文本）+ 换装语义（条目策略，两个下拉 + 保存）+ 关键词块（编辑钮，仅自建）
       + 文本层块（编辑钮）+ 制图提示词块（编辑钮 + 重新生成参考图，仅自建）。 */
    function renderDetail() {
      const it = selectedItem;
      if (!it) {
        detailEl.innerHTML = '<div class="costume-empty">在左侧选择一套服饰</div>';
        return;
      }
      const displayChip = isFlat(it)
        ? '<span class="costume-badge">平铺</span>'
        : '<span class="costume-badge costume-badge-muted">隐形模特</span>';
      const sourceChip = '<span class="costume-badge costume-badge-muted">' +
        esc(SOURCE_ZH[it.source_id] || it.source_id) + "</span>";
      const pol = it.subject_policy || {};
      const polChip = pol.clothing
        ? '<span class="costume-badge' + (pol.clothing === "add" ? '' : ' costume-badge-muted') + '">' +
          esc(POLICY_ZH[pol.clothing] || pol.clothing) + "</span>"
        : "";
      const favBtn = '<button type="button" class="btn-sm costume-detail-fav' +
        (it.favorited ? " on" : "") + '">' + (it.favorited ? "已收藏" : "收藏") + "</button>";

      /* 文本块（view 态）：头（标题+复制+编辑）+ 正文 */
      function renderTextBlock(kind, it) {
        const field = EDIT_FIELDS[kind];
        const value = it[field] || "";
        let body;
        if (editingKind === kind) {
          body =
            '<textarea class="costume-edit-ta" rows="5">' + esc(value) + "</textarea>" +
            '<div class="costume-edit-row">' +
            '<button type="button" class="btn-sm costume-edit-save">保存</button>' +
            '<button type="button" class="btn-sm costume-edit-cancel">取消</button>' +
            "</div>";
        } else {
          body =
            '<div class="costume-kw-text">' +
            (value ? esc(value) : '<span class="costume-kw-muted">' +
              (kind === "ref-prompt" ? "未写 —— 参考图重生成需要先写这个" : "（空）") +
              "</span>") +
            "</div>";
        }
        const head =
          '<div class="costume-kw-head">' +
          '<span class="costume-kw-label">' + esc(EDIT_TITLES[kind]) + "</span>" +
          '<span class="costume-kw-actions">' +
          (kind === "prompt-text" && !editingKind
            ? '<span class="costume-badge costume-badge-muted">' + (it.prompt_source === "ai_draft" ? "AI 起稿" : "手工维护") + "</span>"
            : "") +
          '<button type="button" class="btn-sm costume-copy" data-kind="' + kind + '"' +
          (value ? '' : ' disabled') +
          '>复制</button>' +
          '<button type="button" class="btn-sm costume-edit-btn" data-kind="' + kind + '">编辑</button>' +
          "</span>" +
          "</div>";
        return '<div class="costume-kw-block" data-block="' + kind + '">' + head + body + "</div>";
      }

      /* 编辑块集合：keywords 仅自建可编辑；prompt-text 两库；ref-prompt 仅自建展示 */
      const blocks = [];
      blocks.push(renderTextBlock("keywords", it));
      blocks.push(renderTextBlock("prompt-text", it));
      if (isStudio(it)) {
        blocks.push(renderTextBlock("ref-prompt", it));
      }

      /* 换装语义块（两库都有：策略是逐条目的） */
      const polClothing = pol.clothing || "replace";
      const polAcc = pol.accessories || "keep";
      const policyBlock =
        '<div class="costume-kw-block" data-block="policy">' +
        '<div class="costume-kw-head"><span class="costume-kw-label">换装语义</span></div>' +
        '<div class="costume-policy-row">' +
        '<select class="costume-pol-clothing">' +
        '<option value="replace"' + (polClothing === "replace" ? " selected" : "") + '>整套替换</option>' +
        '<option value="add"' + (polClothing === "add" ? " selected" : "") + '>叠加穿搭（换/加袜类）</option>' +
        '<option value="keep"' + (polClothing === "keep" ? " selected" : "") + '>仅换配件</option>' +
        "</select>" +
        '<select class="costume-pol-acc">' +
        '<option value="keep"' + (polAcc === "keep" ? " selected" : "") + '>保留配件</option>' +
        '<option value="remove"' + (polAcc === "remove" ? " selected" : "") + '>摘掉鞋袜与配件</option>' +
        "</select>" +
        '<button type="button" class="btn-sm costume-pol-save">保存</button>' +
        "</div>" +
        "</div>";

      /* 重生成行（仅自建 + 已有图）：没有 ref_prompt 时按钮禁用（没提示词不出图） */
      let regenRow = "";
      if (isStudio(it) && it.thumb_url) {
        regenRow =
          '<div class="costume-regen-row">' +
          '<button type="button" class="btn-sm costume-regen"' +
          (!it.ref_prompt ? " disabled" : "") +
          ">重新生成参考图</button>" +
          '<span class="costume-kw-muted">约 15 秒 · 旧图自动备份 · ' +
          (!it.ref_prompt ? "先写好制图提示词才能重生成" : "seed 可复现") +
          "</span>" +
          "</div>";
      }

      detailEl.innerHTML =
        (it.image_url
          ? '<div class="costume-detail-img" title="点看大图">' +
            '<img src="' + esc(it.image_url) + '" alt="' + esc(it.item_id) + '"></div>'
          : '<div class="costume-detail-img costume-detail-noimg">' +
            '<div class="costume-thumb-empty">无图 · 纯文本条目<br>（换装走文本通路）</div></div>') +
        '<div class="costume-detail-titles">' +
        "<h3>" + esc(it.label || it.item_id) + "</h3>" +
        '<div class="costume-jp">' + bilingual(it.category_zh, it.category) + "</div>" +
        '<div class="costume-tags">' + favBtn + sourceChip + displayChip + polChip +
        (it.image_url
          ? ""
          : '<span class="costume-badge costume-badge-muted">无图 · 纯文本</span>') +
        "</div>" +
        "</div>" +
        policyBlock +
        blocks.join("") +
        regenRow;

      /* 事件：详情收藏钮走同一条 toggleFav（卡片徽章 ♥ chip 与它实时同步，三库同款） */
      detailEl.querySelector(".costume-detail-fav").addEventListener("click", function () {
        toggleFav(it, detailEl.querySelector(".costume-detail-fav"));
      });
      const detailImg = detailEl.querySelector(".costume-detail-img");
      if (it.image_url) {
        detailImg.addEventListener("click", function () {
          Media.showImageModal(it.image_url, (it.label || it.item_id) +
            "（" + (it.category_zh || it.category) + "）");
        });
      }
      [...detailEl.querySelectorAll("[data-block]")].forEach(function (block) {
        /* 复制 / 编辑 / 保存 / 取消 */
        if (block.dataset.block === "policy") {
          block.querySelector(".costume-pol-save").addEventListener("click", function () {
            const clothing = block.querySelector(".costume-pol-clothing").value;
            const accessories = block.querySelector(".costume-pol-acc").value;
            const btn = block.querySelector(".costume-pol-save");
            btn.disabled = true;
            btn.textContent = "保存中…";
            api("/costumes/" + encodeURIComponent(it.source_id) + "/" + encodeURIComponent(it.item_id) + "/subject-policy", {
              method: "PUT",
              body: JSON.stringify({ clothing: clothing, accessories: accessories }),
            })
              .then(function (pol2) {
                it.subject_policy = pol2;
                editingKind = null;
                renderDetail();
                renderGrid();
              })
              .catch(function (err) {
                btn.disabled = false;
                btn.textContent = "保存失败";
              });
          });
          return;
        }
        const kind = block.dataset.block;
        const value = it[EDIT_FIELDS[kind]] || "";
        const copyBtn = block.querySelector(".costume-copy");
        if (copyBtn && value) wireCopy(copyBtn, value);
        const editBtn = block.querySelector(".costume-edit-btn");
        if (editBtn && kind === "keywords" && !isStudio(it)) {
          editBtn.disabled = true;
          editBtn.title = "站点条目的关键词是站点数据";
        } else if (editBtn) {
          editBtn.addEventListener("click", function () {
            editingKind = editingKind === kind ? null : kind;
            renderDetail();
          });
        }
        const saveBtn = block.querySelector(".costume-edit-save");
        if (saveBtn) {
          saveBtn.addEventListener("click", function () {
            saveEdit(kind, it, block.querySelector(".costume-edit-ta").value.trim(), saveBtn);
          });
        }
        const cancelBtn = block.querySelector(".costume-edit-cancel");
        if (cancelBtn) {
          cancelBtn.addEventListener("click", function () {
            editingKind = null;
            renderDetail();
          });
        }
      });
      const regenBtn = detailEl.querySelector(".costume-regen");
      if (regenBtn && !regenBtn.disabled) {
        regenBtn.addEventListener("click", function () {
          regenBtn.disabled = true;
          regenBtn.textContent = "生成中…（约 15 秒）";
          api("/costumes/" + encodeURIComponent(it.source_id) + "/" + encodeURIComponent(it.item_id) + "/regenerate", {
            method: "POST",
            body: JSON.stringify({}),
          })
            .then(function (res) {
              updateItem(it, {
                thumb_url: "/costumes/image/" + it.source_id + "/" + it.item_id + "/thumb?v=" + encodeURIComponent(res.updated_at),
                image_url: "/costumes/image/" + it.source_id + "/" + it.item_id + "/full?v=" + encodeURIComponent(res.updated_at),
              });
            })
            .catch(function (err) {
              regenBtn.textContent = "生成失败";
              setTimeout(function () {
                renderDetail();
              }, 1600);
            });
        });
      }
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