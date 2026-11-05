/*!
 * extras.js —— 翻译查词 / 备忘录 / 日历 三个面板
 *
 * 为什么单独一个文件：app.js 已经 7000 多行，再往里面塞四个面板就没人看得动了。
 * 这些面板都要悬浮工具栏上的按钮来叫出来（见 app.js 的 runFloatCommand），
 * 所以这个文件在 index.html 里**排在 app.js 后面**，W7Api / W7Icons / MD 一定已经就绪。
 *
 * 依赖的服务端接口（由服务端那一半提供）：
 *   POST /api/translate         {"text": "...", "mode": "translate" | "word"}
 *   GET  /api/memo              {"ok":true,"items":[{id,text,at,done}]}
 *   POST /api/memo              {"op":"add|update|delete|clear_done|clear_all", ...} -> 整份列表
 *   GET  /api/calendar?year=&month=   节假日 / 调休 / 农历
 */
(function (global) {
  'use strict';

  function $(sel) { return document.querySelector(sel); }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function icon(name, size) {
    return global.W7Icons ? W7Icons.markup(name, size || 14) : '';
  }

  function toast(message, kind) {
    if (global.W7Toast) global.W7Toast(message, kind);
  }

  function api() {
    if (!global.W7Api) throw new Error('页面还没准备好（W7Api 未就绪），刷新一下再试');
    return global.W7Api;
  }

  function closeDialog(id) {
    var dialog = document.getElementById(id);
    if (dialog && dialog.open) dialog.close();
  }

  /* 等结果时的「思考中」：跟作图面板共用同一套视觉（蓝点呼吸 + 一行文字），
     不再另造一套转圈动画——Win7 上没有 emoji 字体，图标字符一概不能用。 */
  function thinkingRow(text) {
    var row = el('div', 'figure-status');
    row.appendChild(el('span', 'dot'));
    row.appendChild(el('span', null, text || '思考中'));
    return row;
  }

  function failRow(message) {
    return el('div', 'notice error', message);
  }

  function copyText(text) {
    if (global.W7Copy) return global.W7Copy(text);
    return Promise.reject(new Error('复制功能不可用'));
  }

  /* ==================================================================== 翻译查词 */
  var TRANSLATE_MODES = {
    translate: { go: '翻译', hint: '整句翻译：写一句话或一段话，返回译文' },
    word: { go: '查一下', hint: '查词：写一个词，返回读音、词性与释义' }
  };
  var translate = { mode: 'translate', busy: false, answer: '' };

  function savedTranslateMode() {
    try {
      return localStorage.getItem('w7a.translateMode') === 'word' ? 'word' : 'translate';
    } catch (error) {
      return 'translate';
    }
  }

  function openTranslate() {
    var dialog = $('#translateDialog');
    if (!dialog) return;
    setTranslateMode(translate.mode);
    if (!dialog.open) dialog.showModal();
    var input = $('#trInput');
    if (input) input.focus();
  }

  function setTranslateMode(mode) {
    translate.mode = mode === 'word' ? 'word' : 'translate';
    try { localStorage.setItem('w7a.translateMode', translate.mode); } catch (error) { /* 隐私模式忽略 */ }
    var chipTranslate = $('#trModeTranslate');
    var chipWord = $('#trModeWord');
    if (chipTranslate) chipTranslate.classList.toggle('active', translate.mode === 'translate');
    if (chipWord) chipWord.classList.toggle('active', translate.mode === 'word');
    var go = $('#trGo');
    if (go && !translate.busy) go.textContent = TRANSLATE_MODES[translate.mode].go;
    var hint = $('#trHint');
    if (hint) {
      hint.textContent = TRANSLATE_MODES[translate.mode].hint +
        '；写完按 Enter 直接查（Shift+Enter 换行，Ctrl+Enter 一样提交）';
    }
    var input = $('#trInput');
    if (input) {
      input.placeholder = translate.mode === 'word'
        ? '一个词就行，例如：光合作用、photosynthesis'
        : '例如：把这句话译成英文——今天讲分数的意义';
    }
  }

  function translateBusy(on) {
    translate.busy = on;
    var go = $('#trGo');
    if (go) {
      go.disabled = on;
      go.textContent = on ? '正在查…' : TRANSLATE_MODES[translate.mode].go;
    }
  }

  function runTranslate() {
    var input = $('#trInput');
    var box = $('#trResult');
    var text = input ? String(input.value || '').trim() : '';
    if (!text) {
      box.innerHTML = '';
      box.appendChild(failRow(translate.mode === 'word' ? '先写一个要查的词' : '先写要翻译的内容'));
      if (input) input.focus();
      return;
    }
    if (translate.busy) return;
    translateBusy(true);
    box.innerHTML = '';
    box.appendChild(thinkingRow('思考中'));      // 等的时候一定要看得见「思考中」
    api().post('/api/translate', { text: text, mode: translate.mode }).then(function (res) {
      translateBusy(false);
      showTranslate(box, res);
    }).catch(function (error) {
      translateBusy(false);
      box.innerHTML = '';
      box.appendChild(failRow('查询失败：' + error.message +
        (/404|未知接口/.test(String(error.message)) ? '（服务端还没有 /api/translate 这个接口）' : '')));
    });
  }

  /* 服务端那一半只冻结了请求体，返回什么字段由它决定：这里几种常见的都认，
     认不出来就把原始内容摊开给老师看——总比一个空白框强。 */
  var TRANSLATE_TEXT_KEYS = ['text', 'result', 'translation', 'output', 'content', 'answer', 'message'];

  function translateAnswer(res) {
    for (var index = 0; index < TRANSLATE_TEXT_KEYS.length; index++) {
      var value = res ? res[TRANSLATE_TEXT_KEYS[index]] : '';
      if (typeof value === 'string' && value.trim()) return value.trim();
    }
    return '';
  }

  function translateWordList(res) {
    var list = (res && (res.words || res.entries || res.items || res.means)) || null;
    return Object.prototype.toString.call(list) === '[object Array]' ? list : [];
  }

  function wordLine(item) {
    if (typeof item === 'string') return '- ' + item;
    var head = item.word || item.text || item.title || '';
    var phonetic = item.phonetic || item.pronunciation || '';
    var meaning = item.meaning || item.definition || item.translation || '';
    var parts = item.means || item.meanings || item.pos || [];
    if (Object.prototype.toString.call(parts) === '[object Array]') {
      meaning += (meaning ? '；' : '') + parts.map(function (one) {
        return typeof one === 'string' ? one : (one.meaning || one.text || '');
      }).filter(function (one) { return !!one; }).join('；');
    }
    return '- **' + head + '**' + (phonetic ? ' /' + phonetic + '/' : '') +
      (meaning ? '：' + meaning : '');
  }

  function showTranslate(box, res) {
    box.innerHTML = '';
    translate.answer = translateAnswer(res);
    var body = translate.answer;
    translateWordList(res).forEach(function (item) {
      body += (body ? '\n\n' : '') + wordLine(item);
    });
    if (!body) {
      box.appendChild(el('div', 'muted', '服务端返回的内容和面板预期的不一样，下面是原始结果：'));
      body = '```\n' + JSON.stringify(res || {}, null, 2) + '\n```';
    }
    var content = el('div', 'tr-text');
    if (global.MD && global.MD.render) content.innerHTML = MD.render(body);
    else content.textContent = body;
    box.appendChild(content);

    var actions = el('div', 'tr-actions');
    var copy = el('button', 'btn small');
    copy.type = 'button';
    copy.textContent = '复制结果';
    copy.onclick = function () {
      copyText(body).then(function () { toast('已复制', 'ok'); })
        .catch(function () { toast('复制失败，请手动选择文本', 'error'); });
    };
    var swap = el('button', 'btn small');
    swap.type = 'button';
    swap.textContent = '交换';
    swap.title = '把结果放回上面的输入框，方便再译回去（例如英译中之后再中译英）';
    swap.onclick = function () {
      var input = $('#trInput');
      if (!input) return;
      input.value = body;
      input.focus();
      toast('结果已放回输入框，可以直接再查一次', 'ok');
    };
    actions.appendChild(copy);
    actions.appendChild(swap);
    box.appendChild(actions);
  }

  /* ====================================================================== 备忘录 */
  var memo = { items: [] };

  function openMemo() {
    var dialog = $('#memoDialog');
    if (!dialog) return;
    if (!dialog.open) dialog.showModal();
    loadMemo();
    var input = $('#memoInput');
    if (input) input.focus();
  }

  function loadMemo() {
    var list = $('#memoList');
    if (!list) return;
    list.innerHTML = '';
    list.appendChild(el('div', 'muted', '正在读取…'));
    api().get('/api/memo').then(function (res) {
      renderMemo(res.items || []);
    }).catch(function (error) {
      list.innerHTML = '';
      list.appendChild(failRow('读不出备忘录：' + error.message));
    });
  }

  function renderMemo(items) {
    memo.items = items || [];
    var list = $('#memoList');
    if (!list) return;
    list.innerHTML = '';
    var done = memo.items.filter(function (item) { return !!item.done; }).length;
    var count = $('#memoCount');
    if (count) {
      count.textContent = memo.items.length
        ? ('共 ' + memo.items.length + ' 条' + (done ? '，已完成 ' + done + ' 条' : ''))
        : '';
    }
    if (!memo.items.length) {
      list.appendChild(el('div', 'memo-empty',
        '还没有记东西。在上面写一句、按 Enter 就存下来了' +
        '（存在程序的 config\\memo.json 里：关掉网页、关掉电脑都还在，这个文件不进发布包，' +
        '以后升级、重装都不会把它覆盖掉）。'));
      return;
    }
    memo.items.forEach(function (item) { list.appendChild(memoRow(item)); });
  }

  function memoRow(item) {
    var row = el('div', 'memo-row' + (item.done ? ' done' : ''));
    var label = el('label', 'switch memo-check');
    var box = document.createElement('input');
    box.type = 'checkbox';
    box.checked = !!item.done;
    box.title = item.done ? '点一下改成「还没做」' : '点一下标记为已完成';
    box.onchange = function () {
      memoPost({ op: 'update', id: item.id, done: box.checked });
    };
    label.appendChild(box);
    row.appendChild(label);
    var text = el('span', 'memo-text', item.text || '');
    text.title = item.text || '';
    row.appendChild(text);
    row.appendChild(el('span', 'memo-time', item.at || ''));
    var remove = el('button', 'btn icon ghost memo-del');
    remove.type = 'button';
    remove.title = '删掉这条';
    remove.innerHTML = icon('trash', 13);
    remove.onclick = function () { memoPost({ op: 'delete', id: item.id }); };
    row.appendChild(remove);
    return row;
  }

  /* 不用"先改界面再发请求"那套乐观更新：服务端每次都回整份列表，照着它重画最省事也最准。
     请求排成一条链：老师连着勾两个复选框、或者勾完马上删，一个操作都不会被丢掉，
     而且渲染顺序跟操作顺序一致（后一个响应一定是最后那个状态）。 */
  var memoChain = Promise.resolve();

  function memoPost(body, onOk) {
    memoChain = memoChain.then(function () {
      return api().post('/api/memo', body).then(function (res) {
        renderMemo(res.items || []);
        if (onOk) onOk();
      }).catch(function (error) {
        toast('没成功：' + error.message, 'error');
        loadMemo();
      });
    });
    return memoChain;
  }

  function memoAdd() {
    var input = $('#memoInput');
    var text = input ? String(input.value || '').trim() : '';
    if (!text) {
      toast('先写一句要记的内容', 'warn');
      if (input) input.focus();
      return;
    }
    memoPost({ op: 'add', text: text }, function () {
      if (input) input.value = '';
      toast('记下来了', 'ok');
    });
  }

  /* ======================================================================== 日历 */
  var CAL_SOURCE_LABEL = { online: '联网获取', cache: '本地缓存',
                           bundled: '内置离线表', none: '离线（没连上）' };
  var calendar = { year: 0, month: 0, data: null, retries: 0 };
  var calendarSeq = 0;          // 每请求一次 +1：老师连着点「下一月」时，后到的旧响应不许覆盖新月份

  function pad2(value) { return value < 10 ? '0' + value : String(value); }

  function dateKey(date) {
    return date.getFullYear() + '-' + pad2(date.getMonth() + 1) + '-' + pad2(date.getDate());
  }

  /* 本地把这一个月的格子先摆出来（哪天是周几、今天几号），服务端的数据按日期往格子里填。
     这么做的好处：服务端只回一部分日子（或者干脆没回）时，日历照样是完整的。 */
  function monthCells(year, month) {
    var cells = [];
    var todayKey = dateKey(new Date());
    var total = new Date(year, month, 0).getDate();
    for (var day = 1; day <= total; day++) {
      var date = new Date(year, month - 1, day);
      var weekday = date.getDay();
      cells.push({
        date: dateKey(date),
        day: day,
        weekday: weekday === 0 ? 7 : weekday,      // 周一=1 … 周日=7（中文日历习惯）
        isToday: dateKey(date) === todayKey
      });
    }
    return cells;
  }

  function openCalendar() {
    var dialog = $('#calendarDialog');
    if (!dialog) return;
    if (!calendar.year) {
      var now = new Date();
      setCalendarMonth(now.getFullYear(), now.getMonth() + 1);
    }
    if (!dialog.open) dialog.showModal();
    loadCalendar();
  }

  function setCalendarMonth(year, month) {
    while (month < 1) { month += 12; year -= 1; }
    while (month > 12) { month -= 12; year += 1; }
    calendar.year = year;
    calendar.month = month;
  }

  function shiftMonth(step) {
    setCalendarMonth(calendar.year, calendar.month + step);
    loadCalendar();
  }

  function backToThisMonth() {
    var now = new Date();
    setCalendarMonth(now.getFullYear(), now.getMonth() + 1);
    loadCalendar();
  }

  function setCalTitle() {
    var title = $('#calTitle');
    if (title) title.textContent = calendar.year + ' 年 ' + calendar.month + ' 月';
  }

  function loadCalendar() {
    calendarSeq += 1;
    var seq = calendarSeq;
    setCalTitle();
    var grid = $('#calGrid');
    var note = $('#calNote');
    if (note) note.style.display = 'none';
    grid.innerHTML = '';
    grid.appendChild(el('div', 'muted cal-loading', '正在获取节假日与农历…'));
    api().get('/api/calendar?year=' + calendar.year + '&month=' + calendar.month)
      .then(function (res) {
        if (seq !== calendarSeq) return;          // 已经翻到别的月份了，这份响应作废
        renderCalendar(res || {});
        // 服务端说"手里这份不是刚联网拿的，正在后台刷"：过一会儿自己再问几次，
        // 真数据回来了就补上 —— 老师不用再点任何东西。最多追 3 次，不来回打服务端。
        if (res && res.pending && calendar.retries < 3) {
          calendar.retries += 1;
          setTimeout(function () {
            if (seq === calendarSeq) loadCalendar();
          }, 2500 * calendar.retries);
        } else if (res && !res.pending) {
          calendar.retries = 0;                     // 拿到真数据了，下次还可以再刷
        }
      }).catch(function (error) {
        if (seq !== calendarSeq) return;
        // 接口暂时没有 / 连不上：照样把日历画出来，只是没有农历和休班标记
        renderCalendar({ year: calendar.year, month: calendar.month,
                         source: 'none', days: [], error: error.message });
      });
  }

  function renderCalendar(data) {
    calendar.data = data || {};
    var byDate = {};
    (calendar.data.days || []).forEach(function (item) {
      if (item && item.date) byDate[String(item.date)] = item;
    });
    var cells = monthCells(calendar.year, calendar.month);
    var grid = $('#calGrid');
    grid.innerHTML = '';
    var lead = cells.length ? (cells[0].weekday - 1) : 0;
    for (var blank = 0; blank < lead; blank++) {
      grid.appendChild(el('div', 'cal-cell blank'));
    }
    cells.forEach(function (cell) {
      grid.appendChild(calCell(cell, byDate[cell.date] || {}));
    });
    setCalTitle();
    renderCalSource();
  }

  function calCell(cell, info) {
    var holiday = info.holiday || '';
    var term = info.term || '';
    var isHoliday = info.isHoliday === true;
    var isWorkday = info.isWorkday === true;
    var today = info.isToday === true || cell.isToday;
    var box = el('div', 'cal-cell' + (today ? ' today' : '') +
      (isHoliday ? ' rest' : '') + (isWorkday ? ' work' : ''));
    box.title = cell.date +
      (info.lunar ? '　农历 ' + info.lunar : '') +
      (term ? '　节气 ' + term : '') +
      (holiday ? '　' + holiday : '') +
      (isHoliday ? '　放假' : '') + (isWorkday ? '　调休上班' : '');
    box.appendChild(el('span', 'cal-day', String(cell.day)));
    box.appendChild(el('span', 'cal-lunar', info.lunar || ''));
    if (holiday) box.appendChild(el('span', 'cal-fest', holiday));
    if (term) box.appendChild(el('span', 'cal-term', term));
    if (isHoliday || isWorkday) {
      box.appendChild(el('span', 'cal-badge ' + (isHoliday ? 'rest' : 'work'),
        isHoliday ? '休' : '班'));
    }
    return box;
  }

  function renderCalSource() {
    var data = calendar.data || {};
    var line = $('#calSource');
    if (line) {
      var label = CAL_SOURCE_LABEL[data.source];
      if (!label) label = '未知（服务端没说是从哪来的）';
      line.textContent = '数据来源：' + label + (data.fetchedAt ? '（' + data.fetchedAt + ' 更新）' : '') +
        (data.pending ? '，正在后台更新…' : '');
    }
    var note = $('#calNote');
    if (!note) return;
    if (data.source === 'none' || !(data.days || []).length) {
      note.style.display = '';
      note.className = 'notice warn';
      note.textContent = '这次没取到节假日（' +
        (data.error || '网络不通，或者服务端的日历接口暂时不可用') +
        '）。日历格子照常显示农历和周末；服务端会自己在后台重试，联网之后不用你操作，' +
        '过一会儿翻一下月份就是新的。';
    } else {
      note.style.display = 'none';
    }
  }

  /* ==================================================================== 启动接线 */
  function bind(id, handler) {
    var node = document.getElementById(id);
    if (node) node.onclick = handler;
  }

  function init() {
    if (!$('#translateDialog')) return;
    translate.mode = savedTranslateMode();
    bind('trModeTranslate', function () { setTranslateMode('translate'); });
    bind('trModeWord', function () { setTranslateMode('word'); });
    bind('trGo', runTranslate);
    var input = $('#trInput');
    if (input) {
      input.addEventListener('keydown', function (event) {
        if (event.key !== 'Enter') return;
        if (event.ctrlKey || !event.shiftKey) {
          event.preventDefault();
          runTranslate();
        }
      });
    }

    bind('memoAdd', memoAdd);
    bind('memoRefresh', loadMemo);
    bind('memoClearDone', function () {
      memoPost({ op: 'clear_done' }, function () { toast('已清除完成项', 'ok'); });
    });
    var memoInput = $('#memoInput');
    if (memoInput) {
      memoInput.addEventListener('keydown', function (event) {
        if (event.key === 'Enter') {
          event.preventDefault();
          memoAdd();
        }
      });
    }

    bind('calPrev', function () { shiftMonth(-1); });
    bind('calNext', function () { shiftMonth(1); });
    bind('calToday', backToThisMonth);

  }

  global.W7Extras = {
    init: init,
    openTranslate: openTranslate,
    setTranslateMode: setTranslateMode,
    openMemo: openMemo,
    openCalendar: openCalendar,
    closeDialog: closeDialog
  };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})(window);
