/* 全站顶栏（真多页版单一来源）：所有页面的 topbar 都由这里渲染，
   链接是普通 <a>（默认同窗口切换；cmd-click / 右键 / 拖拽即可在新窗口打开另一页）。
   页面统一挂 /ui/ 前缀（根路径的 /subjects /runs 等已是数据 API,见 charvar/api.py _PAGE_ROUTES）。
   加新页面 = NAV_PAGES 加一项 + charvar/api.py _PAGE_ROUTES + web/static/<name>.html —— 三处同步。 */

const NAV_PAGES = [
  ["gen", "预设"], ["adhoc", "Playground"], ["video", "视频"],
  ["tts", "语音"], ["recipes", "配方"], ["subjects", "主体"],
  ["poses", "动作"], ["costumes", "服装"], ["runs", "历史"], ["settings", "设置"],
];

(function initNav() {
  const header = $("header.topbar");
  if (!header) return;
  const m = location.pathname.match(/^\/ui\/([a-z]+)$/);
  const current = m ? m[1] : (location.pathname === "/" ? "gen" : "");
  header.innerHTML = `
    <div class="brand">charvar <span class="sub">角色变体提示词生成器</span></div>
    <nav>${NAV_PAGES.map(([page, label]) =>
      `<a class="tab${page === current ? " active" : ""}" href="/ui/${page}">${label}</a>`
    ).join("")}</nav>`;
})();