/* 动作库整页（顶栏「动作」页签）：渲染与弹层共用 posegallery.js —— 网格红线、
   双语规则、动作描述块、收藏只此一份。页面没有参考图上下文，不渲染「加入参考图」；
   动作描述在这里可编辑（textarea + PUT /poses/{source}/{pose_id}/action，逐视角保存）。 */
(() => {
  function init() {
    const root = document.getElementById("pose-page-root");
    if (!root) return;
    PoseGallery.mount({
      root,
      variant: "page",
      onStat(text) {
        const stat = document.getElementById("pose-page-stat");
        if (stat) stat.textContent = text;
      },
    });
  }
  init();
})();