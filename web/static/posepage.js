/* 动作库整页（顶栏「动作」页签）：渲染与弹层共用 posegallery.js —— 网格红线、
   双语规则、动作描述块只此一份。页面没有参考图上下文，不渲染「加入参考图」，
   选中动作后可「复制」标题旁的描述文本；动作描述的编辑（textarea + PUT
   /poses/{pose_id}/action）后续单独做。 */
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