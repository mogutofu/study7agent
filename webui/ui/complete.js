/*!
 * complete.js —— 输入框补全：@ 引用工作区文件、/ 打开命令菜单
 *
 * 原生 ES5，无构建（和 app.js / panels.js 一样直接跑）。
 * - 文件列表走 /api/browse：和「文件」按钮弹出的选择器（panels.js 的 openPicker，
 *   见它的 load()）用的是同一个接口，没有新开路由；这里只是不带 filter，拿整个目录的条目。
 * - 面板、技能、会话操作走 window.W7Chat（app.js 暴露的少量入口），
 *   点下去执行的就是界面上那几个按钮原本调用的函数，不另写一份逻辑。
 * - 键盘只在弹出层开着的时候接管 Enter / Esc / 上下键；关着时一个键都不碰
 *   （监听挂在 document 的捕获阶段，必须早于 app.js 的「Enter = 发送」）。
 */
(function (global) {
  'use strict';

  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var MAX_ROWS = 40;          // 一次最多列这么多条，再多也没人翻

  var popup = null;
  var input = null;
  var dirCache = {};          // 目录 -> 条目（同一个目录只请求一次，边打边筛不再打服务端）
  var workspace = null;       // 工作区根：/api/browse 不带 path 时的 workspace 字段
  var workspacePending = null;
  var rows = [];              // 当前可选行：[{node, run}]
  var index = 0;
  var token = null;           // 光标处的补全片段：{mode, query, start, end}
  var seq = 0;                // 换了关键词就把旧响应丢掉
  var dismissed = -1;         // 按 Esc 关掉的那处 token.start：光标没离开就别再弹

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  /* ------------------------------------------------------------ 数据来源 */

  function get(path) {
    if (global.W7Api) return global.W7Api.get(path);      // 复用 app.js 的请求实现（带局域网令牌）
    return fetch(path).then(function (response) {
      return response.json().then(function (data) {
        if (!response.ok || data.ok === false) {
          throw new Error(data.error || ('请求失败：HTTP ' + response.status));
        }
        return data;
      });
    });
  }

  /* 工作区根：和选择器左边「工作区」快捷入口用的是同一个返回字段 */
  function workspaceRoot() {
    if (workspace) return Promise.resolve(workspace);
    if (!workspacePending) {
      workspacePending = get('/api/browse').then(function (data) {
        workspace = data.workspace || '';
        workspacePending = null;
        return workspace;
      }).catch(function (error) {
        workspacePending = null;
        throw error;
      });
    }
    return workspacePending;
  }

  function entriesOf(dir) {
    var key = String(dir).toLowerCase();
    if (dirCache[key]) return Promise.resolve(dirCache[key]);
    return get('/api/browse?path=' + encodeURIComponent(dir)).then(function (data) {
      dirCache[key] = data.entries || [];
      return dirCache[key];
    });
  }

  /* ------------------------------------------------------------ 弹出层 */

  function isOpen() { return !!popup && !popup.classList.contains('hidden'); }

  function openPopup() {
    if (popup) popup.classList.remove('hidden');
  }

  /* 只收起弹层，留着 token：选中的那一行还要靠它改输入框里的文字 */
  function hide() {
    rows = [];
    index = 0;
    if (popup) {
      popup.classList.add('hidden');
      popup.innerHTML = '';
    }
  }

  function close() {
    token = null;
    hide();
  }

  function beginRender() {
    popup.innerHTML = '';
    rows = [];
    index = 0;
  }

  function group(title) {
    popup.appendChild(el('div', 'complete-group', title));
  }

  function hint(text) {
    popup.appendChild(el('div', 'complete-empty', text));
  }

  function rowNode(label, note, badge) {
    var node = el('button', 'complete-row');
    node.type = 'button';
    node.setAttribute('role', 'option');
    node.appendChild(el('span', 'complete-name', label));
    if (note) node.appendChild(el('span', 'complete-hint', note));
    if (badge) node.appendChild(el('span', 'complete-badge', badge));
    return node;
  }

  function addRow(node, run) {
    var at = rows.length;
    node.onmousedown = function (event) { event.preventDefault(); };   // 别把输入框的焦点抢走
    node.onclick = function () { choose(at); };
    popup.appendChild(node);
    rows.push({ node: node, run: run });
  }

  /* 高亮当前行，并保证它在可视区里。
     不用 scrollIntoView：它会把左边的对话区一起滚了。 */
  function mark() {
    for (var i = 0; i < rows.length; i++) {
      rows[i].node.classList.toggle('active', i === index);
    }
    var node = rows[index] && rows[index].node;
    if (!node) return;
    var top = node.offsetTop;
    var bottom = top + node.offsetHeight;
    if (top < popup.scrollTop) popup.scrollTop = top - 4;
    else if (bottom > popup.scrollTop + popup.clientHeight) {
      popup.scrollTop = bottom - popup.clientHeight + 4;
    }
  }

  function move(delta) {
    if (!rows.length) return;
    index = (index + delta + rows.length) % rows.length;
    mark();
  }

  function choose(at) {
    var row = rows[at == null ? index : at];
    if (!row) return;
    var run = row.run;
    var live = token;
    hide();
    if (run) run(live);            // 行自己决定怎么改输入框（文件插入 / 命令清掉）
  }

  /* ------------------------------------------------------------ 光标处的片段 */

  /* @ 和 / 都要求前面是行首或空白：句子里随便打个斜杠不该弹菜单 */
  function tokenAt() {
    var value = input.value;
    var caret = typeof input.selectionStart === 'number' ? input.selectionStart : value.length;
    var before = value.slice(0, caret);
    var file = /(^|\s)@([^\s@]*)$/.exec(before);
    if (file) {
      return { mode: 'file', query: file[2], start: caret - file[2].length - 1, end: caret };
    }
    var command = /(^|\s)\/([^\s/]*)$/.exec(before);
    if (command) {
      return { mode: 'cmd', query: command[2], start: caret - command[2].length - 1, end: caret };
    }
    return null;
  }

  function replaceToken(live, insert) {
    var value = input.value;
    var next = value.slice(0, live.start) + insert + value.slice(live.end);
    input.value = next;
    var caret = live.start + insert.length;
    input.setSelectionRange(caret, caret);      // 光标停在补全出来的文字后面
    input.focus();
  }

  function evaluate() {
    var live = tokenAt();
    if (!live) { dismissed = -1; close(); return; }
    if (live.start === dismissed) { close(); return; }
    dismissed = -1;
    if (isOpen() && token && token.mode === live.mode && token.query === live.query) {
      token = live;
      return;
    }
    token = live;
    seq += 1;
    var mine = seq;
    if (live.mode === 'file') renderFiles(live, mine);
    else renderCommands(live, mine);
  }

  /* ------------------------------------------------------------ @ 文件 */

  function filterEntries(entries, leaf) {
    var needle = String(leaf).toLowerCase();
    var hits = [];
    for (var i = 0; i < entries.length; i++) {
      var name = String(entries[i].name || '');
      var at = name.toLowerCase().indexOf(needle);
      if (at < 0) continue;
      hits.push({ entry: entries[i], prefix: at === 0 });
    }
    hits.sort(function (left, right) {
      if (left.prefix !== right.prefix) return left.prefix ? -1 : 1;   // 前缀命中的排前面
      if (!!left.entry.dir !== !!right.entry.dir) return left.entry.dir ? -1 : 1;
      return String(left.entry.name).toLowerCase() < String(right.entry.name).toLowerCase() ? -1 : 1;
    });
    return hits;
  }

  function renderFiles(live, mine) {
    workspaceRoot().then(function (base) {
      var parts = live.query.split(/[\\/]/);
      var leaf = parts.pop();
      var dir = base;
      var rel = [];
      parts.forEach(function (part) {
        if (!part) return;
        rel.push(part);
        dir = dir.replace(/[\\/]+$/, '') + '\\' + part;
      });
      var prefix = rel.join('\\');
      return entriesOf(dir).then(function (entries) {
        if (mine !== seq) return;                       // 换了关键词，这次结果作废
        var hits = filterEntries(entries, leaf);
        beginRender();
        if (!hits.length) {
          hint('工作区里没有匹配的：' + live.query);
        } else {
          hits.slice(0, MAX_ROWS).forEach(function (item) {
            var entry = item.entry;
            var path = prefix ? prefix + '\\' + entry.name : entry.name;
            addRow(rowNode(entry.name, prefix || '工作区', entry.dir ? '文件夹' : '文件'),
              function (live) { pickFile(entry, path, live); });
          });
        }
        openPopup();
        mark();
      });
    }).catch(function (error) {
      if (mine !== seq) return;
      beginRender();
      hint('读取失败：' + error.message);
      openPopup();
    });
  }

  function pickFile(entry, path, live) {
    if (!live || live.mode !== 'file') return;
    if (entry.dir) {
      replaceToken(live, '@' + path + '\\');   // 进到刚选的文件夹，接着挑里面的东西
      evaluate();
      return;
    }
    replaceToken(live, '@' + path + ' ');
    close();
    // 和「文件」按钮同一个入口：真的进上下文条，不是一个只能看的字符串
    if (global.W7Chat && W7Chat.addFileContext) W7Chat.addFileContext(entry.path);
  }

  /* ------------------------------------------------------------ / 命令 */

  function commands() {
    var chat = global.W7Chat;
    if (!chat) return [];
    var out = [];
    var skills = chat.skillState ? chat.skillState() : [];
    skills.forEach(function (skill) {
      out.push({ group: '技能', label: skill.name,
        note: skill.on ? '已启用 · 点一下停用' : '点一下启用',
        run: function () { chat.toggleSessionSkill(skill.name, !skill.on); } });
    });
    if (global.W7Plane && global.W7Plane.open) {
      out.push({ group: '打开面板', label: '平面 / 几何画板', note: '点、线、圆、函数曲线',
        run: function () { global.W7Plane.open(); } });
    }
    out.push({ group: '打开面板', label: '计算器', note: '计算与单位换算',
      run: function () { chat.openCalc('math'); } });
    if (global.W7Extras) {
      out.push({ group: '打开面板', label: '备忘录', note: '记要点，存在本机',
        run: function () { global.W7Extras.openMemo(); } });
      out.push({ group: '打开面板', label: '日历', note: '节假日 / 调休 / 农历',
        run: function () { global.W7Extras.openCalendar(); } });
      out.push({ group: '打开面板', label: '翻译查词', note: '中英互译与词典',
        run: function () { global.W7Extras.openTranslate(); } });
    }
    out.push({ group: '打开面板', label: '计时', note: '课堂倒计时',
      run: function () { chat.openTimerDialog(); } });
    out.push({ group: '常用操作', label: '新会话', note: '开一段新的对话',
      run: function () { chat.newSession(); } });
    out.push({ group: '常用操作', label: '清空当前对话', note: '清掉消息记录',
      run: function () { chat.clickButton('#btnClear'); } });
    out.push({ group: '常用操作', label: '导出', note: '导出当前对话',
      run: function () { chat.clickButton('#btnExport'); } });
    out.push({ group: '常用操作', label: '停止', note: '中断正在生成的回答',
      run: function () { chat.stop(); } });
    return out;
  }

  /* 选中命令：先把输入框里的 /xxx 清掉，再立刻执行（面板 / 会话操作都是现成的函数） */
  function runCommand(run, live) {
    if (live && live.mode === 'cmd') {
      var value = input.value;
      input.value = value.slice(0, live.start) + value.slice(live.end);
      input.setSelectionRange(live.start, live.start);
      input.focus();
    }
    if (run) run();
  }

  function renderCommands(live, mine) {
    var needle = live.query.toLowerCase();
    var hits = commands().filter(function (item) {
      return !needle || item.label.toLowerCase().indexOf(needle) >= 0;
    });
    if (mine !== seq) return;
    beginRender();
    var last = '';
    hits.forEach(function (item) {
      if (item.group !== last) { group(item.group); last = item.group; }
      addRow(rowNode(item.label, item.note, ''), function (tokenNow) {
        runCommand(item.run, tokenNow);
      });
    });
    if (!hits.length) hint('没有匹配的命令');
    openPopup();
    mark();
  }

  /* ------------------------------------------------------------ 键盘 / 绑定 */

  function onKeyDown(event) {
    if (!isOpen() || event.target !== input) return;
    var key = event.key;
    if (key === 'ArrowDown') move(1);
    else if (key === 'ArrowUp') move(-1);
    else if (key === 'Enter' || key === 'Tab') choose(null);
    else if (key === 'Escape') { dismissed = token ? token.start : -1; close(); }
    else return;
    event.preventDefault();
    event.stopPropagation();     // 光标在补全里时，Enter 不能同时把消息发出去
  }

  function init() {
    popup = $('#completePopup');
    input = $('#input');
    if (!popup || !input) return;
    input.addEventListener('input', function () { dismissed = -1; evaluate(); });
    input.addEventListener('click', evaluate);
    input.addEventListener('keyup', function (event) {
      if (event.key === 'ArrowLeft' || event.key === 'ArrowRight' ||
          event.key === 'Home' || event.key === 'End') evaluate();
    });
    input.addEventListener('blur', function () { close(); });
    document.addEventListener('keydown', onKeyDown, true);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})(typeof window === 'undefined' ? globalThis : window);
