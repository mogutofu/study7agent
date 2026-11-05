/*!
 * icons.js —— 内联 SVG 图标集
 *
 * 为什么不用 emoji：Windows 7 没有 emoji 字体（Segoe UI Emoji 从 Win8 才有），
 * 回形针、链接、图表、工具锤这类 emoji 字符在 Win7 上会显示成空白或方框。
 * 这里用内联 SVG（currentColor 描边）保证任何 Windows 版本、任何内核都一致。
 */
(function (global) {
  'use strict';

  /* 24x24 视图框；默认描边风格，个别图标自带 fill */
  var ICONS = {
    menu: '<path d="M4 7h16M4 12h16M4 17h16"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    settings: '<path d="M4 6h16M4 12h16M4 18h16"/><circle cx="9" cy="6" r="2.1"/><circle cx="15" cy="12" r="2.1"/><circle cx="7" cy="18" r="2.1"/>',
    theme: '<circle cx="12" cy="12" r="8.6"/><path d="M12 3.4a8.6 8.6 0 0 0 0 17.2z" fill="currentColor" stroke="none"/>',
    chart: '<path d="M4 20h16M7.5 20v-8M12 20V5M16.5 20v-5.5"/>',
    ruler: '<rect x="2.6" y="8" width="18.8" height="8" rx="1.6"/><path d="M6 8v3.4M9.4 8v3.4M12.8 8v3.4M16.2 8v3.4M19.6 8v3.4"/>',
    atom: '<circle cx="12" cy="12" r="2.1"/><ellipse cx="12" cy="12" rx="8.6" ry="3.5"/><ellipse cx="12" cy="12" rx="8.6" ry="3.5" transform="rotate(60 12 12)"/><ellipse cx="12" cy="12" rx="8.6" ry="3.5" transform="rotate(120 12 12)"/>',
    calculator: '<rect x="4.5" y="3.5" width="15" height="17" rx="2.4"/><path d="M8 7.5h8"/><path d="M8.5 12h.01M12 12h.01M15.5 12h.01M8.5 15.5h.01M12 15.5h.01M15.5 15.5h.01"/>',
    /* 化学：锥形瓶 */
    flask: '<path d="M9.6 3.5h4.8M10.4 3.6v5.4l-4.6 8.4a1.6 1.6 0 0 0 1.4 2.4h9.6a1.6 1.6 0 0 0 1.4-2.4l-4.6-8.4V3.6"/><path d="M8.1 14.6h7.8"/>',
    backspace: '<path d="M9 5h9.6a1.4 1.4 0 0 1 1.4 1.4v11.2a1.4 1.4 0 0 1-1.4 1.4H9L4.2 12.6a1 1 0 0 1 0-1.2z"/><path d="M11.6 9.6l4.8 4.8M16.4 9.6l-4.8 4.8"/>',
    history: '<circle cx="12" cy="12" r="8.6"/><path d="M12 7.2v5.2l3.2 2"/>',
    download: '<path d="M12 4v11M7.5 11.5 12 16l4.5-4.5M5 20h14"/>',
    paperclip: '<path d="M8.2 12.6l6.1-6.1a3.05 3.05 0 0 1 4.3 4.3l-7.7 7.7a4.7 4.7 0 0 1-6.6-6.6l7.2-7.2"/>',
    link: '<path d="M10.6 13.4a4 4 0 0 0 5.7 0l2.3-2.3a4 4 0 1 0-5.7-5.7l-1.2 1.2"/><path d="M13.4 10.6a4 4 0 0 0-5.7 0l-2.3 2.3a4 4 0 1 0 5.7 5.7l1.2-1.2"/>',
    terminal: '<rect x="3.5" y="4.5" width="17" height="15" rx="2"/><path d="M7.5 9.5l3 2.5-3 2.5M13 15h3.5"/>',
    file: '<path d="M13.8 3.5H7.5A2 2 0 0 0 5.5 5.5v13a2 2 0 0 0 2 2h9a2 2 0 0 0 2-2V8.2z"/><path d="M13.8 3.5v4.7h4.7"/>',
    save: '<path d="M5.5 4.5h10.2l3.3 3.3v11.7h-13.5z"/><path d="M8.8 4.5v5.2h6.4V4.5M8.8 19.5v-5h6.4v5"/>',
    folder: '<path d="M3.5 7.2a2 2 0 0 1 2-2h3.4l2 2h7.6a2 2 0 0 1 2 2v8.6a2 2 0 0 1-2 2H5.5a2 2 0 0 1-2-2z"/>',
    slides: '<rect x="3" y="4" width="18" height="12.5" rx="1.6"/><path d="M6.8 8h10.4M6.8 11.4h6.2"/><path d="M12 16.5V20M8.5 20h7"/>',
    image: '<rect x="3" y="4.5" width="18" height="15" rx="2"/><circle cx="8.6" cy="10" r="1.6"/><path d="M4.5 17.2l4.6-4.3 3.5 3.2 2.9-2.6 4 3.7"/>',
    display: '<rect x="2.5" y="4" width="19" height="12.8" rx="2"/><path d="M8 20h8M12 16.8V20"/>',
    globe: '<circle cx="12" cy="12" r="8.6"/><path d="M3.4 12h17.2M12 3.4c2.8 3.4 2.8 13.8 0 17.2-2.8-3.4-2.8-13.8 0-17.2z"/>',
    pencil: '<path d="M4 20h4.2L19.6 8.6l-4.2-4.2L4 15.8z"/><path d="M14.4 5.4l4.2 4.2"/>',
    trash: '<path d="M4.5 7h15M9.5 7V4.8h5V7M6.4 7l1 12.2h9.2L17.6 7M10.2 10.8v6M13.8 10.8v6"/>',
    close: '<path d="M6.5 6.5l11 11M17.5 6.5l-11 11"/>',
    up: '<path d="M12 19.5V5M6 11l6-6 6 6"/>',
    chevron: '<path d="M6.5 9.5l5.5 5.5 5.5-5.5"/>',
    agent: '<circle cx="12" cy="7.5" r="3.2"/><path d="M4.8 20.2a7.2 7.2 0 0 1 14.4 0"/>',
    users: '<circle cx="9.2" cy="8" r="3.2"/><path d="M3.2 19.6a6 6 0 0 1 12 0"/><path d="M16.2 6.2a3.1 3.1 0 0 1 0 5.8M17.6 19.6a5.8 5.8 0 0 0-2-4.4"/>',
    timer: '<circle cx="12" cy="13.2" r="7.4"/><path d="M12 9.4v4.2l2.8 1.7M9.4 3.4h5.2"/>',
    book: '<path d="M4.6 4.6h11.2a2 2 0 0 1 2 2v13.2H6.6a2 2 0 0 1-2-2z"/><path d="M4.6 17.8h13.2"/>',
    stop: '<rect x="7" y="7" width="10" height="10" rx="1.5"/>',
    play: '<path d="M8.5 5.4 19 12 8.5 18.6z"/>',
    search: '<circle cx="10.8" cy="10.8" r="6.3"/><path d="M15.4 15.4 20 20"/>',
    check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    folderPlus: '<path d="M3.5 7.2a2 2 0 0 1 2-2h3.4l2 2h6.6a2 2 0 0 1 2 2v3.4"/><path d="M3.5 7.2v10.6a2 2 0 0 0 2 2h6.9"/><path d="M17.6 14.6v6M14.6 17.6h6"/>',
    filter: '<path d="M4 7h16M4 12h16M4 17h16"/><circle cx="9.2" cy="7" r="1.9"/><circle cx="14.8" cy="12" r="1.9"/><circle cx="8.4" cy="17" r="1.9"/>',
    panel: '<rect x="3.5" y="4.5" width="17" height="15" rx="2.2"/><path d="M10 4.5v15"/>',
    more: '<path d="M6.6 12h.01M12 12h.01M17.4 12h.01" stroke-width="2.6"/>',
    refresh: '<path d="M20 12a8 8 0 1 1-2.6-5.9"/><path d="M20 4.4V9h-4.6"/>',
    /* 产物卡片用：打开（用系统默认程序）、复制结果、交换、日历、备忘录、翻译查词 */
    open: '<path d="M13.5 4.5h6v6"/><path d="M19.5 4.5 11 13"/><path d="M17.5 14.5v4a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V9a2 2 0 0 1 2-2h4"/>',
    copy: '<rect x="8.8" y="8.8" width="10.7" height="10.7" rx="2"/><path d="M15.2 8.8V6.2a2 2 0 0 0-2-2H6.2a2 2 0 0 0-2 2v7a2 2 0 0 0 2 2h2.6"/>',
    swap: '<path d="M6.5 9h11l-3.2-3.2M17.5 15h-11l3.2 3.2"/>',
    calendar: '<rect x="3.5" y="5" width="17" height="15.5" rx="2.2"/><path d="M3.5 9.6h17M8 3.2v3.4M16 3.2v3.4"/>',
    note: '<rect x="4.5" y="3.5" width="15" height="17" rx="2.2"/><path d="M8.4 8.6h7.2M8.4 12.2h7.2M8.4 15.8h4.2"/>',
    translate: '<path d="M3.6 6.4h8M7.6 4.2v2.2c0 4.2-1.6 7.6-4.2 9.4"/><path d="M6.2 10c1.4 2.6 3.4 4.6 5.6 5.6"/><path d="M12.4 20.4l3.6-8.6 3.6 8.6M13.8 17.4h4.8"/>',
    circuit: '<path d="M3 12h3.4l2-4 3 8 2-4h3.2"/><path d="M17 8.6v6.8M20 8.6v6.8"/><path d="M17 12h3"/>',
    phet: '<rect x="3" y="4.5" width="18" height="13" rx="2"/><path d="M10.2 8.4l5 3-5 3z"/><path d="M9 20.4h6"/>',
    physics: '<path d="M3 19.4h18"/><path d="M3 19.4L13.6 6.4"/><circle cx="16.4" cy="10" r="2.8"/><path d="M8.6 13.4l2.6-3.1"/>',
    bio: '<path d="M8.4 3.2c0 6.2 7.2 6.2 7.2 8.8s-7.2 2.6-7.2 8.8"/><path d="M15.6 3.2c0 6.2-7.2 6.2-7.2 8.8s7.2 2.6 7.2 8.8"/><path d="M9.6 7.4h4.8M9.6 16.6h4.8"/>',
    figure: '<path d="M4 19.6L12 5.4l8 14.2z"/><circle cx="12" cy="14.4" r="2.8"/><path d="M12 5.4v3.2"/>',
    inspector: '<path d="M3.6 17.4a8.4 8.4 0 1 1 16.8 0"/><path d="M12 17.4l4.2-5.2"/><circle cx="12" cy="17.4" r="1.7"/>',
    /* 思考那一行行首用：灯泡（模型正在想什么） */
    bulb: '<path d="M12 3.6a5.9 5.9 0 0 1 3.5 10.7c-.5.4-.8 1-.8 1.6v1.4H9.3v-1.4c0-.6-.3-1.2-.8-1.6A5.9 5.9 0 0 1 12 3.6z"/><path d="M9.9 20.5h4.2"/>',
  };

  function markup(name, size) {
    var body = ICONS[name];
    if (!body) return '';
    var px = Number(size) || 16;
    return '<svg class="ico" viewBox="0 0 24 24" width="' + px + '" height="' + px + '"' +
      ' fill="none" stroke="currentColor" stroke-width="1.8"' +
      ' stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">' +
      body + '</svg>';
  }

  /* 把页面上所有 <span data-icon="name"> 填充为对应图标（可重复调用） */
  function hydrate(root) {
    var scope = root || (typeof document === 'undefined' ? null : document);
    if (!scope || !scope.querySelectorAll) return 0;
    var nodes = scope.querySelectorAll('[data-icon]');
    var filled = 0;
    for (var index = 0; index < nodes.length; index++) {
      var node = nodes[index];
      if (node.getAttribute('data-icon-done') === '1') continue;
      var html = markup(node.getAttribute('data-icon'),
        Number(node.getAttribute('data-icon-size')) || 16);
      if (!html) continue;
      node.insertAdjacentHTML('afterbegin', html);
      node.setAttribute('data-icon-done', '1');
      filled += 1;
    }
    return filled;
  }

  global.W7Icons = { markup: markup, hydrate: hydrate, names: Object.keys(ICONS) };
})(typeof window === 'undefined' ? globalThis : window);
