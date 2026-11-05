/* 电路编辑器（M7 补做）：自己摆元件、自己连线，服务端算电流电压。
 *
 * 分工：**电路图由服务端画**（`webui\circuit.py` 的 `render()`，里面是真正的电路符号），
 * 这里只做三件事：把点击翻译成"在哪个格子放什么元件""连哪两个接线柱"、
 * 把服务端给的一整张 SVG 贴上去、把读数表显示出来。
 * 接线柱在 SVG 里带 `data-terminal`，所以点哪个接线柱不用前端自己算坐标。
 */
(function () {
  'use strict';

  var CELL = 90, PAD = 60;
  var STATE = { types: [], circuit: { components: [], wires: [] }, selected: null,
                pending: null, via: [], mode: 'place', result: null, seq: 1, undo: [] };

  /* ------------------------------------------------------------ 视角（缩放 / 平移）

     和物理沙盒一套做法：只改 SVG 的 viewBox，**不动电路数据**。坐标换算
     （接线柱位置、点元件、连线）本来就都走 getScreenCTM，所以缩放平移之后照样准。 */
  var VIEW = { zoom: 1, x: 0, y: 0, w: 0, h: 0, inited: false };

  function baseViewSize(svg) {
    var view = svg && svg.viewBox && svg.viewBox.baseVal;
    if (view && view.width) return [view.width, view.height];
    return [660, 480];
  }

  function applyView() {
    var svg = $('#crCanvas svg');
    if (!svg || !VIEW.w) return;
    var width = VIEW.w / VIEW.zoom;
    var height = VIEW.h / VIEW.zoom;
    var slackX = width * 0.5;
    var slackY = height * 0.5;                 // 允许拖出去一点，但别拖没了
    VIEW.x = Math.max(-slackX, Math.min(VIEW.w - width + slackX, VIEW.x));
    VIEW.y = Math.max(-slackY, Math.min(VIEW.h - height + slackY, VIEW.y));
    svg.setAttribute('viewBox', VIEW.x + ' ' + VIEW.y + ' ' + width + ' ' + height);
    var label = $('#crZoomLabel');
    if (label) label.textContent = Math.round(VIEW.zoom * 100) + '%';
  }

  function resetView() {
    VIEW.zoom = 1;
    VIEW.x = 0;
    VIEW.y = 0;
    VIEW.inited = false;
    applyView();
  }

  function zoomAt(factor, clientX, clientY) {
    var svg = $('#crCanvas svg');
    if (!svg || !svg.getScreenCTM) return;
    var point = null;
    var box = svg.getBoundingClientRect();
    if (clientX !== undefined && box.width) {
      var user = svg.createSVGPoint();
      user.x = clientX;
      user.y = clientY;
      point = user.matrixTransform(svg.getScreenCTM().inverse());
    }
    VIEW.zoom = Math.max(0.3, Math.min(8, VIEW.zoom * factor));
    var width = VIEW.w / VIEW.zoom;
    var height = VIEW.h / VIEW.zoom;
    if (point) {                               // 让光标下的那个点待在原地
      VIEW.x = point.x - (clientX - box.left) * (width / box.width);
      VIEW.y = point.y - (clientY - box.top) * (height / box.height);
    } else {
      VIEW.x = (VIEW.w - width) / 2;
      VIEW.y = (VIEW.h - height) / 2;
    }
    applyView();
  }

  var TRACE_LIMIT = 60;

  /* 拖滑片走过的位置（去重 + 太少就补几个点，不然曲线是断的） */
  function rememberRatio(ratio) {
    var value = Math.round(Math.max(0, Math.min(1, ratio)) * 1000) / 1000;
    var trace = STATE.trace || (STATE.trace = []);
    if (trace.length && Math.abs(trace[trace.length - 1] - value) < 0.008) return;
    trace.push(value);
    if (trace.length > TRACE_LIMIT) trace.shift();
  }

  function hasRheostat() {
    var found = false;
    STATE.circuit.components.forEach(function (row) {
      if (row.type === 'rheostat') found = true;
    });
    return found;
  }

  /* 默认坐标轴：x = 变阻器接入电阻、y = 电源电流（最常用的 I-R 曲线）。
     老师自己填过就不动他的。 */
  function defaultAxes(force) {
    var x = $('#crChartX');
    var y = $('#crChartY');
    if (!x || !y) return;
    if (!force && (x.value || '').trim() && (y.value || '').trim()) return;
    var rheostat = null;
    var battery = null;
    STATE.circuit.components.forEach(function (row) {
      if (row.type === 'rheostat' && !rheostat) rheostat = row;
      if (row.type === 'battery' && !battery) battery = row;
    });
    if (rheostat && (force || !(x.value || '').trim())) {
      x.value = 'R(' + (rheostat.name || rheostat.id) + ')';   // 接入电阻（扫一遍就是它在变）
    }
    if (rheostat && (force || !(y.value || '').trim())) {
      y.value = 'P(' + (rheostat.name || rheostat.id) + ')';   // 它的功率：P-R 曲线有极值
    } else if (battery && (force || !(y.value || '').trim())) {
      y.value = 'I(' + (battery.name || battery.id) + ')';
    }
  }

  function clearChart(text) {
    var box = $('#crChart');
    if (box) {
      box.innerHTML = '<div class="muted" style="padding:8px">' + (text || '') + '</div>';
    }
  }

  function chartAxes() {
    var x = $('#crChartX');
    var y = $('#crChartY');
    return { x: (x && x.value || '').trim(), y: (y && y.value || '').trim() };
  }

  function currentRatio() {
    var item = null;
    STATE.circuit.components.forEach(function (row) {
      if (row.type === 'rheostat' && !item) item = row;
    });
    return item ? (item.ratio === undefined ? 1 : item.ratio) : null;
  }

  /* 自动画图：拖滑片时调用（节流 260ms，拖动过程中不会把服务端打爆） */
  var chartTimer = null;
  function scheduleChart() {
    if (chartTimer) clearTimeout(chartTimer);
    chartTimer = setTimeout(function () { chartTimer = null; drawChart(); }, 260);
  }

  function drawChart(options) {
    var axes = chartAxes();
    var box = $('#crChart');
    if (!box) return;
    if (!axes.x || !axes.y) {
      box.innerHTML = '<div class="muted" style="padding:8px">x 轴和 y 轴都要填一个记号，' +
        '比如 x 填 R滑1、y 填 I电源（写法见右边说明表）</div>';
      return;
    }
    var ranges = window.W7ChartUI.readRanges('crChart');
    var payload = { action: 'chart', components: STATE.circuit.components,
                    wires: STATE.circuit.wires, x: axes.x, y: axes.y,
                    ratio: currentRatio() };
    if (ranges.x_range) { payload.xmin = ranges.x_range[0]; payload.xmax = ranges.x_range[1]; }
    if (ranges.y_range) { payload.ymin = ranges.y_range[0]; payload.ymax = ranges.y_range[1]; }
    if (options && options.sweep) {
      payload.steps = 41;                       // 扫一遍：滑片 0 -> 100%
    } else if ((STATE.trace || []).length >= 2) {
      payload.ratios = STATE.trace.slice(0);     // 老师自己拖出来的那串位置
    } else {
      payload.steps = 41;
    }
    if (options && options.sweep) STATE.trace = [];
    note('画图中…');
    return post(payload).then(function (data) {
      if (!data || !data.ok) {
        box.innerHTML = '<div class="muted" style="padding:8px">' +
          ((data && data.error) || '画不出来') + '</div>';
        note((data && data.error) || '画不出来', 'warn');
        return null;
      }
      window.W7ChartUI.paint('#crChart', data, { help: '#crChartHelp',
                                                 legend: '#crChartLegend' });
      STATE.chart = data;
      note((data.skipped ? ('部分点算不出来：' + data.skipped + '；') : '') +
           ('图像：' + data.x_label + ' - ' + data.y_label + '，' + data.points.length + ' 个点'));
      return data;
    });
  }

  function renderChartLegend(data) {
    var help = $('#crChartHelp');
    if (help) help.textContent = data.help || '';
    var box = $('#crChartLegend');
    if (!box) return;
    box.innerHTML = (data.legend || []).map(function (row) {
      return '<div class="chart-legend-row"><b>' + row.symbol + '</b><span>' + row.meaning +
        (row.value === '' ? '' : (' = ' + row.value)) + '</span></div>';
    }).join('');
  }

  /* 把"当前点"那个红点挪到正确位置：靠服务端给的映射自己算，不用重新请求。
     动画/拖动时每帧请求一次就太费了（沙盒播放也用这一套）。 */
  function moveChartCursor(points, cursor, meta, box) {
    var host = box || $('#crChart');
    if (!host || !meta || !points || cursor === null || cursor === undefined) return;
    var svg = host.querySelector('svg');
    if (!svg) return;
    var hit = points[cursor];
    if (!hit) { hit = points[points.length - 1]; }
    if (!hit) return;
    var cx = meta.left + (hit[0] - meta.xmin) / (meta.xmax - meta.xmin) * meta.w;
    var cy = meta.top + meta.h - (hit[1] - meta.ymin) / (meta.ymax - meta.ymin) * meta.h;
    var dot = svg.querySelector('circle.chart-cursor');
    if (!dot) {
      dot = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
      dot.setAttribute('class', 'chart-cursor');
      dot.setAttribute('r', '5');
      dot.setAttribute('fill', '#e0518f');
      svg.appendChild(dot);
    }
    dot.setAttribute('cx', cx);
    dot.setAttribute('cy', cy);
  }

  /* 每次改动电路之前先存一份快照：老师点错了要能退回去 */
  function snapshot() {
    STATE.undo.push(JSON.stringify(STATE.circuit));
    if (STATE.undo.length > 50) STATE.undo.shift();
    var button = $('#crUndo');
    if (button) button.disabled = STATE.undo.length === 0;
  }

  function undo() {
    if (!STATE.undo.length) { note('没有可撤销的操作了', 'warn'); return; }
    STATE.circuit = JSON.parse(STATE.undo.pop());
    STATE.selected = null;
    STATE.pending = null;
    renderEditor();
    solve();
    note('撤销了一步');
  }

  function $(selector) { return document.querySelector(selector); }

  /* ------------------------------------------------------------ 图像（M8）

     老师要的：x/y 轴随便填（`R滑1`、`P滑1`、`I电源`…），拖滑片或点「扫一遍」就自动出图，
     比例自动调。记号怎么写在右边那张说明表里。

     采样点由服务端算（它才有电流电压），这里只负责：记住拖过的位置 -> 请求 -> 把图和
     说明表贴上去 -> 拖的时候把红点挪到当前位置。 */

  function post(payload) {
    var options = {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    };
    if (window.W7Api && window.W7Api.token) {
      options.headers['X-W7-Auth'] = window.W7Api.token;
      options.headers['X-Auth-Token'] = window.W7Api.token;
    }
    return fetch('/api/circuit', options).then(function (response) {
      return response.json().catch(function () { return {}; });
    });
  }

  function note(text, kind) {
    var box = $('#crNote');
    if (!box) return;
    box.textContent = text || '';
    box.className = kind === 'warn' ? 'notice warn' : 'muted';
  }

  /* 选中的元件画一个虚线框（像平面几何那样"看得见选中了谁"） */
  function markSelection() {
    var svg = $('#crCanvas svg');
    if (!svg) return;
    Array.prototype.forEach.call(svg.querySelectorAll('[data-wire]'), function (node) {
      var index = parseInt(node.getAttribute('data-wire'), 10);
      var hit = STATE.selected === 'w' + index;
      if (node.getAttribute('stroke-width') === '14') return;      // 透明热区不动
      node.setAttribute('stroke', hit ? '#e0518f' : '#3a4652');
      node.setAttribute('stroke-width', hit ? '4' : '2');
    });
    Array.prototype.forEach.call(svg.querySelectorAll('g[data-component]'), function (group) {
      var hit = group.getAttribute('data-component') === STATE.selected;
      var box = group.querySelector('.sb-pick');
      if (!hit) { if (box) box.parentNode.removeChild(box); return; }
      if (box) return;
      var rect = group.getBBox();
      box = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
      box.setAttribute('class', 'sb-pick');
      box.setAttribute('x', rect.x - 10);
      box.setAttribute('y', rect.y - 10);
      box.setAttribute('width', rect.width + 20);
      box.setAttribute('height', rect.height + 20);
      box.setAttribute('fill', 'none');
      box.setAttribute('stroke', '#e0518f');
      box.setAttribute('stroke-width', '2');
      box.setAttribute('stroke-dasharray', '6 4');
      box.setAttribute('pointer-events', 'none');
      group.appendChild(box);
    });
  }

  function typeOf(key) {
    for (var index = 0; index < STATE.types.length; index++) {
      if (STATE.types[index].key === key) return STATE.types[index];
    }
    return null;
  }

  /* ------------------------------------------------------------ 服务端往返 */

  function loadTypes() {
    return post({ action: 'types' }).then(function (data) {
      if (!data || !data.ok) throw new Error((data && data.error) || '拿不到元件表');
      STATE.types = data.types || [];
      var pick = $('#crType');
      pick.innerHTML = STATE.types.map(function (item) {
        return '<option value="' + item.key + '">' + item.name + '（' + item.symbol + '）' +
          '</option>';
      }).join('');
      if (window.__crExamplesLoaded !== true) {
        window.__crExamplesLoaded = true;
        loadExamples().catch(function () { /* 例子表拿不到不影响画板 */ });
      }
      return loadDemo();
    });
  }

  /* 例子库（M8）：装载一个现成实验，并把建议的坐标轴填上 */
  function loadExamples() {
    return post({ action: 'examples' }).then(function (data) {
      var pick = $('#crExample');
      if (!pick || !data || !data.ok) return;
      STATE.examples = data.examples || [];
      pick.innerHTML = STATE.examples.map(function (item) {
        return '<option value="' + item.key + '">' + item.name + '</option>';
      }).join('');
    });
  }

  function loadExample(key) {
    return post({ action: 'example', key: key }).then(function (data) {
      if (!data || !data.ok) {
        note((data && data.error) || '装不上这个例子', 'warn');
        return null;
      }
      var item = data.example;
      STATE.circuit = item.circuit;
      STATE.seq = 20;
      STATE.trace = [];
      STATE.selected = null;
      var axes = item.chart || {};
      var x = $('#crChartX');
      var y = $('#crChartY');
      if (x && axes.x) x.value = axes.x;
      if (y && axes.y) y.value = axes.y;
      renderEditor();
      note(item.name + '：' + item.note);
      return solve();
    });
  }

  function loadDemo() {
    return post({ action: 'demo' }).then(function (data) {
      if (data && data.circuit) {
        STATE.circuit = data.circuit;
        STATE.seq = 10;
        STATE.trace = [];
        defaultAxes(true);                     // 示例电路：坐标轴回到默认的 I-R
      }
      return solve();
    });
  }

  function solve() {
    if (!STATE.circuit.components.length) {
      render('');
      note('先放元件：选一种元件，然后在画布上点一下', 'warn');
      return Promise.resolve(null);
    }
    pruneWires();                    // 野导线先清掉，不然算不出来、画面还会停在旧图上
    return post({ action: 'solve', components: STATE.circuit.components,
                  wires: STATE.circuit.wires }).then(function (data) {
      if (!data || !data.ok) {
        /* 画到一半算不出来是常事（还有元件悬空）：图照样画出来，错误只当提示 */
        STATE.result = null;
        note((data && data.error) || '算不出来', 'warn');
        return post({ action: 'draw', components: STATE.circuit.components,
                      wires: STATE.circuit.wires }).then(function (drawn) {
          if (drawn && drawn.ok) render(drawn.figure);
          return null;
        });
      }
      STATE.result = data;
      render(data.figure);
      renderReadings(data);
      note((data.warnings || []).length ? data.warnings.join('；') : '算完了');
      defaultAxes(false);
      if (hasRheostat()) {
        scheduleChart();
      } else {
        clearChart('图像要有一个能连续变的量：放一个滑动变阻器，拖它的滑片就会自动出图。');
      }
      return data;
    });
  }

  /* ------------------------------------------------------------ 画布 */

  function render(figure) {
    var host = $('#crCanvas');
    if (!host) return;
    host.innerHTML = figure || '';
    var svg = host.querySelector('svg');
    if (!svg) return;
    svg.style.cursor = 'crosshair';
    svg.onclick = onClickSvg;
    svg.onmousedown = onDown;
    svg.onmousemove = function (event) {
      if (STATE.mode === 'wire' && STATE.pending) drawPreview(event);
      onMove(event);
    };
    svg.onmouseup = onUp;
    svg.onmouseleave = function () { DRAG = null; };
    svg.onwheel = function (event) {
      event.preventDefault();
      zoomAt(event.deltaY < 0 ? 1.12 : 1 / 1.12, event.clientX, event.clientY);
    };
    var size = baseViewSize(svg);
    VIEW.w = size[0];
    VIEW.h = size[1];
    if (!VIEW.inited) {
      VIEW.inited = true;
      VIEW.x = 0;
      VIEW.y = 0;
    }
    applyView();
    markSelection();
  }

  /* 用户坐标（不吸附）：拖滑片要连续，吸附到格子就会"瞬移" */
  function toUser(event) {
    var svg = $('#crCanvas svg');
    if (!svg || !svg.getScreenCTM) return null;
    var point = svg.createSVGPoint();
    point.x = event.clientX;
    point.y = event.clientY;
    return point.matrixTransform(svg.getScreenCTM().inverse());
  }

  function toGrid(event) {
    var svg = $('#crCanvas svg');
    if (!svg || !svg.getScreenCTM) return null;
    var point = svg.createSVGPoint();
    point.x = event.clientX;
    point.y = event.clientY;
    var user = point.matrixTransform(svg.getScreenCTM().inverse());
    return [Math.round((user.x - PAD) / CELL), Math.round((user.y - PAD) / CELL)];
  }

  function onClickSvg(event) {
    var terminal = event.target && event.target.getAttribute
      ? event.target.getAttribute('data-terminal') : null;
    var component = closestComponent(event.target);
    if (STATE.mode === 'wire') {
      if (!terminal) {
        /* 空白处：就是线段的另一头（和平面面板画线段一样，两点一线） */
        if (!STATE.pending) { note('先点一个接线柱开始连线', 'warn'); return; }
        var grid0 = toGrid(event);
        if (!grid0) return;
        snapshot();
        STATE.circuit.wires.push({ from: STATE.pending, to: STATE.pending,
                                   via: [grid0] });
        STATE.pending = null;
        STATE.via = [];
        note('连了一根线到 (' + grid0.join(', ') + ')');
        solve();
        return;
      }
      if (!STATE.pending) {
        STATE.pending = terminal;
        note('已经选中 ' + terminal + '，再点另一个接线柱');
      } else if (STATE.pending === terminal) {
        STATE.pending = null;
        note('同一头连不起来，重新点');
      } else {
        snapshot();
        STATE.circuit.wires.push({ from: STATE.pending, to: terminal,
                                   via: (STATE.via || []).slice(0) });
        note('连上了 ' + STATE.pending + ' - ' + terminal +
             ((STATE.via || []).length ? ('（自己走了 %d 个折点）' % STATE.via.length) : ''));
        STATE.pending = null;
        STATE.via = [];
        solve();
      }
      highlightPending();
      return;
    }
    if (STATE.mode === 'select') {
      var wireSel = wireIndex(event.target);
      STATE.selected = wireSel !== null ? ('w' + wireSel)
                                        : component;   // 点空处 = 取消选中
      renderEditor();
      markSelection();
      return;
    }
    var wire = wireIndex(event.target);
    if (wire !== null) {
      STATE.selected = 'w' + wire;
      renderEditor();
      markSelection();
      return;
    }
    if (component) {
      STATE.selected = component;
      renderEditor();
      markSelection();
      return;
    }
    if (STATE.mode !== 'place') return;
    var grid = toGrid(event);
    if (!grid) return;
    place(grid);
  }

  var DRAG = null;

  /* 滑片把手：按住红色滑片拖动 = 调电阻，而不是搬元件（老师："滑动变阻器不会拖"） */
  function knobOwner(event) {
    var node = event.target;
    while (node) {
      if (node.getAttribute && node.getAttribute('data-knob')) {
        var owner = null;
        STATE.circuit.components.forEach(function (row) {
          if (row.id === node.getAttribute('data-knob')) owner = row;
        });
        return owner;
      }
      node = node.parentNode;
    }
    return null;
  }

  var PAN = null;

  function startPan(event, clickToDeselect) {
    PAN = { clientX: event.clientX, clientY: event.clientY, x: VIEW.x, y: VIEW.y,
            moved: false, deselect: !!clickToDeselect };
    var svg = $('#crCanvas svg');
    if (svg) svg.style.cursor = 'grabbing';
    event.preventDefault();
  }

  function onDown(event) {
    if (STATE.mode === 'wire') return;
    if (event.button === 2) return;
    if (STATE.mode === 'pan' || event.button === 1) {
      startPan(event, false);
      return;
    }
    var rheostat = knobOwner(event);
    if (rheostat) {
      DRAG = { id: rheostat.id, item: rheostat, slider: true, from: toGrid(event), moved: false,
               ratio0: rheostat.ratio === undefined ? 1 : rheostat.ratio,
               clientX: event.clientX };
      STATE.selected = rheostat.id;
      renderEditor();
      markSelection();
      event.preventDefault();
      return;
    }
    var id = closestComponent(event.target);
    if (!id) {
      // 空白处按下：**选择模式下直接拖画面**（和几何画板一样，不用先切工具）。
      // 松手时如果没拖动过，就当成"点空处"= 取消选中。
      if (STATE.mode === 'select') startPan(event, true);
      return;
    }
    var item = null;
    STATE.circuit.components.forEach(function (row) { if (row.id === id) item = row; });
    if (!item) return;
    DRAG = { id: id, item: item, from: toGrid(event), moved: false };
    STATE.selected = id;
    renderEditor();
    markSelection();
    event.preventDefault();
  }

  function onMove(event) {
    if (PAN) {
      var svgNow = $('#crCanvas svg');
      var box = svgNow ? svgNow.getBoundingClientRect() : null;
      if (Math.abs(event.clientX - PAN.clientX) > 2 ||
          Math.abs(event.clientY - PAN.clientY) > 2) {
        PAN.moved = true;
      }
      if (box && box.width) {
        VIEW.x = PAN.x - (event.clientX - PAN.clientX) * (VIEW.w / VIEW.zoom / box.width);
        VIEW.y = PAN.y - (event.clientY - PAN.clientY) * (VIEW.h / VIEW.zoom / box.height);
        applyView();
      }
      return;
    }
    if (!DRAG || STATE.mode === 'wire') return;
    var grid = toGrid(event);
    if (!grid) return;
    if (DRAG.slider) {
      /* 滑片跟着鼠标**连续**走：只按"鼠标横向移动了多少像素 ÷ 滑片行程"算增量，
         不碰绝对坐标、也不吸附格子——之前按整格吸附，一格就是 100%，
         所以看着像"从一头瞬移到另一头"。 */
      if (!DRAG.saved) { snapshot(); DRAG.saved = true; }
      var svg = $('#crCanvas svg');
      var drawn = svg && svg.viewBox && svg.viewBox.baseVal.width
        ? svg.getBoundingClientRect().width / svg.viewBox.baseVal.width : 1;
      var span = 60 * (drawn || 1);                 // 滑片行程 60 用户单位 -> 屏幕像素
      DRAG.item.ratio = Math.max(0, Math.min(1,
        DRAG.ratio0 + (event.clientX - DRAG.clientX) / span));
      rememberRatio(DRAG.item.ratio);
      solve();
      return;
    }
    if (grid[0] === DRAG.from[0] && grid[1] === DRAG.from[1]) return;
    DRAG.moved = true;
    if (!DRAG.saved) { snapshot(); DRAG.saved = true; }
    DRAG.item.at = grid;
    DRAG.from = grid;
    solve();
  }

  function onUp() {
    if (PAN) {
      var pan = PAN;
      PAN = null;
      var svg = $('#crCanvas svg');
      if (svg) svg.style.cursor = 'crosshair';
      if (pan.deselect && !pan.moved) {          // 没拖动 = 点空处：取消选中
        if (STATE.selected) {
          STATE.selected = null;
          renderEditor();
          markSelection();
        }
      }
      return;
    }
    if (DRAG && DRAG.moved) {
      note(DRAG.slider
        ? ('把 ' + DRAG.id + ' 的滑片拖到了 ' + Math.round((DRAG.item.ratio || 0) * 100) +
           '%，接入电阻 ' + (Math.round((DRAG.item.rmax || 0) * (DRAG.item.ratio || 0) * 1000) / 1000) +
           ' Ω')
        : ('把 ' + DRAG.id + ' 挪到了第 ' + DRAG.item.at[0] + ' 列第 ' + DRAG.item.at[1] + ' 行'));
    }
    DRAG = null;
    markSelection();
  }

  /* 点在导线上（透明粗热区也带 data-wire） */
  function wireIndex(node) {
    while (node) {
      if (node.getAttribute && node.getAttribute('data-wire') !== null &&
          node.getAttribute('data-wire') !== undefined) {
        var value = node.getAttribute('data-wire');
        if (value !== null && value !== '') return parseInt(value, 10);
      }
      node = node.parentNode;
    }
    return null;
  }

  function closestComponent(node) {
    while (node) {
      if (node.getAttribute && node.getAttribute('data-component')) {
        return node.getAttribute('data-component');
      }
      node = node.parentNode;
    }
    return null;
  }

  /* 连线预览：从起点接线柱 -> 已点折点 -> 鼠标处（橡皮筋） */
  function drawPreview(event) {
    var svg = $('#crCanvas svg');
    if (!svg || !STATE.pending) return;
    var old = svg.querySelector('#crPreview');
    if (old) old.parentNode.removeChild(old);
    var anchor = svg.querySelector('[data-terminal="' + STATE.pending + '"]');
    if (!anchor) return;
    var chain = [[parseFloat(anchor.getAttribute('cx')), parseFloat(anchor.getAttribute('cy'))]];
    (STATE.via || []).forEach(function (grid) {
      chain.push([PAD + grid[0] * CELL, PAD + grid[1] * CELL]);
    });
    var user = event ? toUser(event) : null;      // 无事件时只画"起点+折点"
    if (user) chain.push([user.x, user.y]);
    var node = document.createElementNS('http://www.w3.org/2000/svg', 'polyline');
    node.setAttribute('id', 'crPreview');
    node.setAttribute('points', chain.map(function (p) { return p[0] + ',' + p[1]; }).join(' '));
    node.setAttribute('fill', 'none');
    node.setAttribute('stroke', '#12a594');
    node.setAttribute('stroke-width', '2');
    node.setAttribute('stroke-dasharray', '6 4');
    svg.appendChild(node);
  }

  function highlightPending() {
    Array.prototype.forEach.call(document.querySelectorAll('#crCanvas [data-terminal]'),
      function (node) {
        var on = node.getAttribute('data-terminal') === STATE.pending;
        node.setAttribute('fill', on ? '#e0518f' : '#eef2f7');
        node.setAttribute('r', on ? '7' : '5');
      });
  }

  function place(grid) {
    var kind = ($('#crType') || {}).value;
    var spec = typeOf(kind);
    if (!spec) return;
    var taken = STATE.circuit.components.some(function (item) {
      return item.at[0] === grid[0] && item.at[1] === grid[1];
    });
    if (taken) { note('这个格子上已经有元件了', 'warn'); return; }
    var item = { id: kind[0] + (STATE.seq++), type: kind, at: grid };
    if (spec.value === 'emf') item.emf = spec['default'];
    else if (spec.value === 'r') item.r = spec['default'];
    else if (spec.value === 'rmax') { item.rmax = spec['default']; item.ratio = 1.0; }
    else if (spec.value === 'closed') item.closed = true;
    else if (spec.value === 'c') item.c = spec['default'];
    else if (spec.value === 'to') item.to = spec['default'];
    snapshot();
    STATE.circuit.components.push(item);
    STATE.selected = item.id;
    renderEditor();
    solve();
  }

  /* 删掉当前选中的东西：导线按序号删，元件连带它的导线一起删 */
  function deleteSelected() {
    var selected = String(STATE.selected || '');
    if (!selected) { note('先点一下导线或元件', 'warn'); return; }
    if (selected.charAt(0) === 'w') {
      var which = parseInt(selected.slice(1), 10);
      if (isNaN(which)) return;
      snapshot();
      STATE.circuit.wires.splice(which, 1);
      STATE.selected = null;
      renderEditor();
      solve();
      note('删掉了第 ' + which + ' 根导线（Ctrl+Z 撤销）');
      return;
    }
    var item = null;
    STATE.circuit.components.forEach(function (row) { if (row.id === selected) item = row; });
    if (!item) return;
    snapshot();
    var gone = dropComponent(item.id);
    STATE.selected = null;
    renderEditor();
    solve();
    note('删掉了 ' + item.id + (gone ? '（连着它的 ' + gone + ' 根导线一起删了）' : '') +
         '，Ctrl+Z 撤销');
  }

  /* 野导线自愈：导线两头只要有哪一头的元件已经不在了，这根线就是废线。
     留着它后端会报"没有这个接线柱"，算不出来、画面停在旧图上——看起来就是"元件删不掉"。
     删元件的地方都会顺手清，这里再兜一道底（撤销、手动改过 state 也管得住）。

     注意导线有**两种写法**：界面上连的是 `{from, to, via}`，示例电路/老数据是
     `["b1.+", "r1.1"]` 这种两元数组。只认一种会把另一种当成废线全删掉
     （示例电路就这么被清空过一次：六个元件全悬空、13 个节点）。 */
  function wireEnds(pair) {
    if (Object.prototype.toString.call(pair) === '[object Array]') {
      return [String(pair[0] || ''), String(pair[1] || '')];
    }
    return [String((pair && pair.from) || ''), String((pair && pair.to) || '')];
  }

  function pruneWires() {
    var alive = {};
    STATE.circuit.components.forEach(function (row) { alive[row.id] = true; });
    var dropped = 0;
    STATE.circuit.wires = STATE.circuit.wires.filter(function (pair) {
      var ends = wireEnds(pair);
      var from = ends[0].split('.')[0];
      var to = ends[1].split('.')[0];
      var keep = !!from && !!to && alive[from] === true && alive[to] === true;
      if (!keep) dropped += 1;
      return keep;
    });
    return dropped;
  }

  /* 删掉一个元件：连着它的导线一起删（老师："无法自动删掉元件删连接的导线"） */
  function dropComponent(id) {
    var wires = STATE.circuit.wires.length;
    STATE.circuit.components = STATE.circuit.components.filter(function (row) {
      return row.id !== id;
    });
    pruneWires();
    return wires - STATE.circuit.wires.length;
  }

  function renderEditor() {
    var box = $('#crEdit');
    if (!box) return;
    var item = null;
    STATE.circuit.components.forEach(function (row) {
      if (row.id === STATE.selected) item = row;
    });
    if (String(STATE.selected || '').charAt(0) === 'w') {
      box.innerHTML = '<div class="sb-item"><b>导线</b>（第 ' +
        String(STATE.selected).slice(1) + ' 根）' +
        '<div class="muted">点「删掉这根导线」或按 Delete；删错了 Ctrl+Z 撤销</div>' +
        '<button class="btn lab-mini" data-action="delete">删掉这根导线</button></div>';
      var wireButton = box.querySelector('button[data-action="delete"]');
      if (wireButton) wireButton.onclick = function () { deleteSelected(); };
      return;
    }
    if (!item) {
      box.innerHTML = '<div class="muted">点画布上的元件或导线就能选中：元件能改数值、' +
        '也能删掉（「删掉这个元件」或按 Delete，连着它的导线会一起删）；' +
        '导线选中后按 Delete 删；工具选「连线」再点两下就连一根线</div>';
      return;
    }
    var NAMES = { battery: '电源', resistor: '定值电阻', rheostat: '滑动变阻器', lamp: '小灯泡',
                  ammeter: '电流表', voltmeter: '电压表', switch: '开关', switch2: '双掷开关',
                  capacitor: '电容', resistance_box: '电阻箱', diode: '二极管' };
    var LABELS = { emf: ['电动势', 'V'], r: ['阻值', 'Ω'], rmax: ['最大阻值', 'Ω'],
                   ratio: ['滑片位置', '（0~1：0=最左、1=最右）'],
                   c: ['电容', 'F（直流稳定后相当于断路）'] };
    var html = '<div class="sb-item"><b>' + (item.name || item.id) + '（' +
      (NAMES[item.type] || item.type) + '）</b>';
    html += '<label>名字 <input class="lab-num" type="text" style="width:96px" value="' +
      (item.name || item.id) + '" data-name>（图像里就用这个名字）</label>';
    ['emf', 'r', 'rmax', 'ratio', 'c'].forEach(function (key) {
      if (item[key] === undefined) return;
      var info = LABELS[key] || [key, ''];
      html += '<label>' + info[0] + ' <input class="lab-num" type="text" style="width:64px" ' +
        'value="' + item[key] + '" data-value="' + key + '"> ' + info[1] + '</label>';
    });
    if (item.type === 'rheostat') {
      var used = (item.rmax || 0) * (item.ratio === undefined ? 1 : item.ratio);
      html += '<div class="muted">现在接入电路的电阻 = 最大阻值 × 滑片位置 = ' +
        (Math.round(used * 1000) / 1000) + ' Ω（拖画布上那个红色滑片就能改）</div>';
    }
    if (item.type === 'switch') {
      html += '<label><input type="checkbox" data-closed' + (item.closed ? ' checked' : '') +
        '> 闭合</label>';
    }
    if (item.type === 'switch2') {
      var to = String(item.to || 'A').toUpperCase();
      html += '<label>刀接在 <select class="lab-num" data-to>' +
        '<option value="A"' + (to === 'A' ? ' selected' : '') + '>A 触点</option>' +
        '<option value="B"' + (to === 'B' ? ' selected' : '') + '>B 触点</option>' +
        '</select>（K 是公共端）</label>';
    }
    if (item.type === 'diode') {
      html += '<div class="muted">三角形那一头是 +（正极）。正偏导通、反偏截止，' +
        '连反了这里会提示。</div>';
    }
    if (item.type === 'capacitor') {
      html += '<div class="muted">直流电路稳定后电容相当于断路：两端有电压、不走电流。</div>';
    }
    html += '<button class="btn lab-mini" data-action="delete">删掉这个元件</button></div>';
    box.innerHTML = html;
    Array.prototype.forEach.call(box.querySelectorAll('input[data-value]'), function (node) {
      node.onchange = function () {
        item[node.getAttribute('data-value')] = parseFloat(node.value) || 0;
        solve();
      };
    });
    var nameField = box.querySelector('input[data-name]');
    if (nameField) {
      nameField.onchange = function () {
        var value = (nameField.value || '').trim();
        if (!value) { nameField.value = item.name || item.id; return; }
        item.name = value.slice(0, 12);
        renderEditor();
        solve();
        drawChart();
      };
    }
    var closed = box.querySelector('input[data-closed]');
    if (closed) {
      closed.onchange = function () { item.closed = closed.checked; solve(); };
    }
    var blade = box.querySelector('select[data-to]');
    if (blade) {
      blade.onchange = function () { item.to = blade.value; solve(); };
    }
    var remove = box.querySelector('button[data-action="delete"]');
    if (remove) {
      remove.onclick = function () {
        snapshot();
        var gone = dropComponent(item.id);
        STATE.selected = null;
        renderEditor();
        solve();
        note('删掉了 ' + item.id + (gone ? '（连着它的 ' + gone + ' 根导线一起删了）' : '') +
             '，Ctrl+Z 撤销');
      };
    }
  }

  function renderReadings(data) {
    var box = $('#crReadings');
    if (!box) return;
    var html = (data.readings || []).map(function (row) {
      var isMeter = row.type === 'ammeter' || row.type === 'voltmeter';
      return '<div class="phys-value"><span>' + row.name + ' ' + (row.label || row.id) +
        '</span><b>' +
        (row.reading || '') + (isMeter ? '（表读数）' : '') + '</b></div>';
    }).join('');
    html += '<div class="phys-value"><span>节点数</span><b>' + data.nodes + '</b></div>';
    box.innerHTML = html;
  }

  /* ------------------------------------------------------------ 开关 */

  function open() {
    var panel = $('#circuitPanel');
    if (panel) panel.classList.remove('hidden');
    if (!STATE.types.length) {
      loadTypes().catch(function (error) { note(String(error), 'warn'); });
    } else {
      solve();
    }
  }

  function close() {
    var panel = $('#circuitPanel');
    if (panel) panel.classList.add('hidden');
  }

  function bind() {
    var on = function (selector, handler) {
      var node = $(selector);
      if (node) node.onclick = handler;
    };
    on('#crClose', close);
    on('#btnCircuit', function () { open(); });
    on('#crSolve', function () { solve(); });
    on('#crUndo', function () { undo(); });
    on('#crDemo', function () { loadDemo(); });
    on('#crChartDraw', function () { STATE.trace = []; drawChart(); });
    on('#crExampleLoad', function () {
      loadExample(($('#crExample') || {}).value).catch(function (error) {
        note(String(error), 'warn');
      });
    });
    on('#crZoomIn', function () { zoomAt(1.25); });
    on('#crZoomOut', function () { zoomAt(1 / 1.25); });
    on('#crZoomFit', function () { resetView(); });
    on('#crChartSave', function () {
      if (!window.W7ChartUI) return;
      window.W7ChartUI.exportPng('#crChart', '电路图像').then(function (info) {
        note('图像已存成 PNG（' + info.width + 'x' + info.height + '）');
      }).catch(function (error) { note(String(error && error.message || error), 'warn'); });
    });
    on('#crChartAuto', function () {
      window.W7ChartUI.clearRanges('crChart');
      drawChart();
    });
    if (window.W7ChartUI) {
      window.W7ChartUI.bindGrip('#crGrip', '#circuitPanel .sandbox-main');
      window.W7ChartUI.bindWindow('#circuitPanel', [['#crWinE', 'e'], ['#crWinS', 's'],
                                                    ['#crWinSE', 'se']]);
    }
    on('#crChartSweep', function () { drawChart({ sweep: true }); });
    on('#crClear', function () {
      STATE.circuit = { components: [], wires: [] };
      STATE.selected = null;
      STATE.pending = null;
      renderEditor();
      render('');
      note('清空了');
    });
    var tool = $('#crTool');
    if (tool) {
      tool.onchange = function () {
        STATE.mode = tool.value;
        STATE.pending = null;
        note(tool.value === 'wire' ? '连线：先点一个接线柱，再点另一个'
             : (tool.value === 'place' ? '放元件：在画布上点一下就放一个'
                                       : '选择/移动：点元件选中，按住拖到别的格子'));
        markSelection();
      };
    }
    document.addEventListener('keydown', function (event) {
      var panel = $('#circuitPanel');
      if (!panel || panel.classList.contains('hidden')) return;
      if (event.key === 'Escape') {
        if (STATE.pending) { STATE.pending = null; STATE.via = []; highlightPending(); return; }
        close();
        return;
      }
      if (event.key === 'z' && (event.ctrlKey || event.metaKey)) { event.preventDefault(); undo(); return; }
      var tag = (event.target && event.target.tagName) || '';
      if (event.key === 'Delete' && STATE.selected && tag !== 'INPUT') {
        var remove = document.querySelector('#crEdit button[data-action="delete"]') ||
          document.querySelector('#crEdit button[data-edit-action="delete"]');
        if (remove && remove.onclick) remove.onclick();
      }
    });
    renderEditor();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bind);
  } else {
    bind();
  }

  window.W7Circuit = { open: open, undo: undo, markSelection: markSelection, close: close,
                       chart: drawChart, moveChartCursor: moveChartCursor, solve: solve, state: STATE,
                       loadDemo: loadDemo };
})();
