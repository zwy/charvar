/* 服装库整页（顶栏「服装」页签）：渲染与后续生图流程弹层共用 costumegallery.js ——
   网格红线、检索规则、关键词复制块只此一份。页面没有参考图上下文，不渲染
   「加入参考图」；选中服装后可「复制」关键词（英文描述）当提示词原料。 */
(() => {
  function init() {
    const root = document.getElementById("costume-page-root");
    if (!root) return;
    CostumeGallery.mount({
      root,
      variant: "page",
      onStat(text) {
        const stat = document.getElementById("costume-page-stat");
        if (stat) stat.textContent = text;
      },
    });
  }
  init();
})();