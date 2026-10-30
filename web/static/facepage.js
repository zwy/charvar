/* 人脸库整页（顶栏「人脸」页签）：渲染与弹层共用 facegallery.js —— 网格红线、
   双语规则、详情块只此一份。页面没有参考图上下文，只渲染大图/元数据/加入参考图；
   v1 无用户层（收藏/动作描述是人脸库没建的东西），onStat 回写页签统计。 */
(() => {
  function init() {
    const root = document.getElementById("face-page-root");
    if (!root) return;
    FaceGallery.mount({
      root,
      variant: "page",
      onStat(text) {
        const stat = document.getElementById("face-page-stat");
        if (stat) stat.textContent = text;
      },
    });
  }
  init();
})();
