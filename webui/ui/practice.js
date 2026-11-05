/* 物理练习模式（M7）：出题 -> 作答 -> 判分 -> 进错题本。
 *
 * 这里**不做物理**：题目（题干、图、答案、误差限）都是服务端 `webui\practice.py` 给的，
 * 前端只负责把图/曲线画出来、把作答收上来、把判分结果和讲解显示清楚。
 * 判分在服务端（仪器读数还要看估读位数），前端不自己算对错。
 *
 * 面板布局复用沙盒那套整屏样式（.sandbox-*），不再另起一套。
 */
(function () {
  'use strict';

  function $(selector) { return document.querySelector(selector); }

  var STATE = { types: [], instruments: [], question: null, result: null, zoom: 1 };

  /* 图能缩放：老师嫌"离得远、看着难受"就是看不出刻度，滚轮放大最直接 */
  function applyZoom(factor) {
    STATE.zoom = Math.max(0.5, Math.min(4, STATE.zoom * factor));
    var scale = 'scale(' + STATE.zoom + ')';
    ['#prFigure', '#prChartBox'].forEach(function (selector) {
      var box = $(selector);
      if (box) {
        box.style.transform = scale;
        box.style.transformOrigin = 'top left';
      }
    });
    var label = $('#prZoomLabel');
    if (label) label.textContent = Math.round(STATE.zoom * 100) + '%';
  }

  function post(payload) {
    var options = {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    };
    if (window.W7Api && window.W7Api.token) {
      options.headers['X-Auth-Token'] = window.W7Api.token;
    }
    return fetch('/api/practice', options).then(function (response) {
      return response.json().catch(function () { return {}; });
    });
  }

  function note(text, kind) {
    var box = $('#prNote');
    if (!box) return;
    box.textContent = text || '';
    box.className = kind === 'warn' ? 'notice warn' : 'muted';
  }

  /* ------------------------------------------------------------ 选项与题目 */

  function loadTypes() {
    return post({ action: 'types' }).then(function (data) {
      if (!data || !data.ok) throw new Error((data && data.error) || '拿不到题型');
      STATE.types = data.types || [];
      STATE.instruments = data.instruments || [];
      var pick = $('#prType');
      pick.innerHTML = STATE.types.map(function (item) {
        return '<option value="' + item.key + '">' + item.name + '</option>';
      }).join('');
      pick.onchange = function () { fillKinds(); };
      fillKinds();
      return newQuestion();
    });
  }

  /* "具体"这一栏随题型变：读数题列仪器，计算题列模型，图像题没有具体项 */
  function fillKinds() {
    var qtype = ($('#prType') || {}).value || 'reading';
    var pick = $('#prKind');
    var options = [];
    if (qtype === 'reading') {
      options = STATE.instruments.map(function (item) {
        return { key: item.key, name: item.name };
      });
    } else if (qtype === 'calc' || qtype === 'fill') {
      options = [{ key: 'belt', name: '传送带' }, { key: 'efield', name: '类平抛' },
                 { key: 'circuit', name: '电路动态分析' }];
    } else {
      options = [{ key: 'vt', name: 'v–t 图像' }];
    }
    pick.innerHTML = options.map(function (item) {
      return '<option value="' + item.key + '">' + item.name + '</option>';
    }).join('');
  }

  function newQuestion() {
    var payload = { action: 'new', qtype: ($('#prType') || {}).value || 'reading',
                    kind: ($('#prKind') || {}).value || 'caliper20',
                    seed: Math.floor(Math.random() * 1e9) };
    note('出题中…');
    return post(payload).then(function (data) {
      if (!data || !data.ok) {
        note((data && data.error) || '出不了题', 'warn');
        return null;
      }
      STATE.question = data.question;
      STATE.result = null;
      renderQuestion();
      note('答完点「提交判分」；答错会自动记进错题本');
      return data.question;
    });
  }

  function renderQuestion() {
    var question = STATE.question;
    if (!question) return;
    $('#prTitle').textContent = question.title || '题目';
    $('#prPrompt').textContent = question.prompt || '';
    $('#prFigure').innerHTML = question.figure || '';
    renderChart(question.chart);
    $('#prBlanks').innerHTML = (question.blanks || []).map(function (blank) {
      return '<label class="pr-blank">' + blank.label +
        ' <input class="lab-num" type="text" style="width:110px" data-blank="' + blank.key +
        '" placeholder="' + (blank.unit || '') + '"> <span class="muted">' + (blank.unit || '') +
        '</span></label>';
    }).join('');
    $('#prResult').innerHTML = '';
    Array.prototype.forEach.call(document.querySelectorAll('#prBlanks input'), function (node) {
      node.onkeydown = function (event) {
        if (event.key === 'Enter') { event.preventDefault(); submit(); }
      };
    });
    if (question.hint) {
      $('#prResult').innerHTML = '<div class="phys-card"><div class="phys-card-head">提示</div>' +
        '<div class="phys-line">' + question.hint + '</div></div>';
    }
  }

  /* 曲线（图像读数题的图）：点由服务端算好，这里只画坐标轴和折线 */
  function renderChart(chart) {
    var box = $('#prChartBox');
    if (!box) return;
    if (!chart || !(chart.points || []).length) { box.innerHTML = ''; return; }
    var width = 460, height = 260, pad = 44;
    var xs = chart.points.map(function (point) { return point[0]; });
    var ys = chart.points.map(function (point) { return point[1]; });
    var minX = Math.min.apply(null, xs), maxX = Math.max.apply(null, xs);
    var minY = Math.min.apply(null, ys), maxY = Math.max.apply(null, ys);
    if (!(maxX > minX)) maxX = minX + 1;
    if (!(maxY > minY)) maxY = minY + 1;
    function px(x) { return pad + (x - minX) / (maxX - minX) * (width - pad - 16); }
    function py(y) { return height - pad - (y - minY) / (maxY - minY) * (height - pad - 22); }
    var parts = ['<svg width="' + width + '" height="' + height + '" viewBox="0 0 ' + width +
      ' ' + height + '" xmlns="http://www.w3.org/2000/svg">',
      '<line x1="' + pad + '" y1="' + (height - pad) + '" x2="' + (width - 16) + '" y2="' +
      (height - pad) + '" stroke="#3a4652"/>',
      '<line x1="' + pad + '" y1="' + (height - pad) + '" x2="' + pad + '" y2="22" ' +
      'stroke="#3a4652"/>'];
    /* 网格：横轴每 1 单位、纵轴每 1 单位（图读数的关键是能估读到半格） */
    var step;
    for (step = Math.ceil(minX); step <= maxX; step++) {
      parts.push('<line x1="' + px(step) + '" y1="22" x2="' + px(step) + '" y2="' +
        (height - pad) + '" stroke="#eef2f7"/>');
      parts.push('<text x="' + px(step) + '" y="' + (height - pad + 14) +
        '" font-size="11" fill="#7b8794" text-anchor="middle">' + step + '</text>');
    }
    for (step = Math.ceil(minY); step <= maxY; step++) {
      parts.push('<line x1="' + pad + '" y1="' + py(step) + '" x2="' + (width - 16) + '" y2="' +
        py(step) + '" stroke="#eef2f7"/>');
      parts.push('<text x="' + (pad - 6) + '" y="' + (py(step) + 4) +
        '" font-size="11" fill="#7b8794" text-anchor="end">' + step + '</text>');
    }
    parts.push('<polyline points="' + chart.points.map(function (point) {
      return px(point[0]).toFixed(1) + ',' + py(point[1]).toFixed(1);
    }).join(' ') + '" fill="none" stroke="#4d6bfe" stroke-width="2"/>');
    parts.push('<text x="' + (width - 60) + '" y="' + (height - pad + 16) +
      '" font-size="12" fill="#3a4652">' + (chart.x || '') + '</text>');
    parts.push('<text x="8" y="18" font-size="12" fill="#3a4652">' + (chart.y || '') + '</text>');
    parts.push('</svg>');
    box.innerHTML = parts.join('');
  }

  /* ------------------------------------------------------------ 判分与错题本 */

  function submit() {
    if (!STATE.question) return Promise.resolve(null);
    var answers = {};
    Array.prototype.forEach.call(document.querySelectorAll('#prBlanks input'), function (node) {
      answers[node.getAttribute('data-blank')] = node.value;
    });
    note('判分中…');
    return post({ action: 'grade', question: STATE.question, answers: answers,
                  record: true }).then(function (data) {
      if (!data || data.ok === undefined) {
        note((data && data.error) || '判分失败', 'warn');
        return null;
      }
      STATE.result = data;
      renderResult(data);
      note(data.ok ? ('全对（%d/%d）' % [data.right, data.total])
                   : ('错了 %d 个%s' % [data.total - data.right,
                                       data.recorded ? '，已记进错题本' : '']),
           data.ok ? '' : 'warn');
      refreshBook();
      return data;
    });
  }

  function renderResult(result) {
    var box = $('#prResult');
    if (!box) return;
    var html = '<div class="phys-card"><div class="phys-card-head">' +
      (result.ok ? '全对' : '对 %d / %d（%s 分）'.replace('%d', result.right)
        .replace('%d', result.total).replace('%s', result.score)) + '</div>';
    (result.items || []).forEach(function (item) {
      html += '<div class="phys-line' + (item.ok ? '' : ' phys-line-key') + '">' +
        (item.ok ? '对：' : '错：') + item.label + '：你填 ' + (item.yours || '（空）') +
        '，正确 ' + item.text + ' ' + item.unit + '　' + item.reason + '</div>';
    });
    html += '</div>';
    box.innerHTML = html;
  }

  function refreshBook() {
    return post({ action: 'mistakes', limit: 10 }).then(function (data) {
      var box = $('#prMistakes');
      if (!box) return;
      if (!data || !data.ok) { box.innerHTML = ''; return; }
      var items = ((data.mistakes || {}).items) || [];
      var total = ((data.mistakes || {}).total) || items.length;
      box.innerHTML = items.length
        ? items.slice(-6).reverse().map(function (item) {
            return '<div class="pr-mistake"><b>' + item.topic + '</b> ' +
              (item.question || '').slice(0, 40) + '<div class="muted">' +
              (item.reason || '').slice(0, 80) + '</div></div>';
          }).join('')
        : '<div class="muted">还没有错题</div>';
      $('#prStats').textContent = '错题本共 ' + total + ' 条';
    });
  }

  /* ------------------------------------------------------------ 开关 */

  function open(qtype) {
    var panel = $('#practicePanel');
    if (panel) panel.classList.remove('hidden');
    if (!STATE.types.length) {
      loadTypes().then(function () {
        if (qtype) selectType(qtype);
      }).catch(function (error) { note(String(error), 'warn'); });
    } else {
      if (qtype) selectType(qtype);
      refreshBook();
    }
  }

  /* 直接切到某个题型（顶栏「读数」按钮一进来就是读数题） */
  function selectType(qtype) {
    var pick = $('#prType');
    if (!pick) return;
    pick.value = qtype;
    if (pick.onchange) pick.onchange();
    newQuestion().catch(function (error) { note(String(error), 'warn'); });
  }

  function close() {
    var panel = $('#practicePanel');
    if (panel) panel.classList.add('hidden');
  }

  function bind() {
    var on = function (selector, handler) {
      var node = $(selector);
      if (node) node.onclick = handler;
    };
    on('#prClose', close);
    on('#prNew', function () { newQuestion().catch(function (error) { note(String(error), 'warn'); }); });
    on('#prNext', function () { newQuestion().catch(function (error) { note(String(error), 'warn'); }); });
    on('#prSubmit', function () { submit().catch(function (error) { note(String(error), 'warn'); }); });
    on('#prBook', function () { refreshBook(); });
    /* 对答案：直接把标准答案和该读到哪位写出来（不判分、不记错题本） */
    on('#prAnswer', function () {
      if (!STATE.question) return;
      var box = $('#prResult');
      box.innerHTML = '<div class="phys-card"><div class="phys-card-head">标准答案</div>' +
        (STATE.question.blanks || []).map(function (blank) {
          return '<div class="phys-line phys-line-key">' + blank.label + '：' +
            (blank.text || blank.answer) + ' ' + (blank.unit || '') + '（该读到 ' +
            (blank.digits === undefined ? '?' : blank.digits) + ' 位小数）</div>';
        }).join('') +
        '<div class="phys-line">' + (STATE.question.hint || '') + '</div></div>';
    });
    on('#prRead', function () { open('reading'); });
    on('#prZoomIn', function () { applyZoom(1.25); });
    on('#prZoomOut', function () { applyZoom(1 / 1.25); });
    on('#prZoomFit', function () {
      STATE.zoom = 1; applyZoom(1);
      var box = $('#prStage');
      if (box) { box.scrollLeft = 0; box.scrollTop = 0; }
    });
    /* 画布：滚轮缩放（Ctrl 或直接滚，和平面面板一致）+ 按住拖动平移视角 */
    var stage = $('#prStage');
    if (stage) {
      stage.style.cursor = 'grab';
      stage.addEventListener('wheel', function (event) {
        event.preventDefault();
        applyZoom(event.deltaY < 0 ? 1.12 : 1 / 1.12);
      }, { passive: false });
      var pan = null;
      stage.addEventListener('mousedown', function (event) {
        pan = { x: event.clientX, y: event.clientY, left: stage.scrollLeft, top: stage.scrollTop };
        stage.style.cursor = 'grabbing';
        event.preventDefault();
      });
      stage.addEventListener('mousemove', function (event) {
        if (!pan) return;
        stage.scrollLeft = pan.left - (event.clientX - pan.x);
        stage.scrollTop = pan.top - (event.clientY - pan.y);
      });
      var release = function () { pan = null; stage.style.cursor = 'grab'; };
      stage.addEventListener('mouseup', release);
      stage.addEventListener('mouseleave', release);
    }
    var toolbarButton = $('#btnRead');
    if (toolbarButton) toolbarButton.onclick = function () { open('reading'); };
    document.addEventListener('keydown', function (event) {
      var panel = $('#practicePanel');
      if (event.key === 'Escape' && panel && !panel.classList.contains('hidden')) close();
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bind);
  } else {
    bind();
  }

  window.W7Practice = { open: open, selectType: selectType, applyZoom: applyZoom, close: close, newQuestion: newQuestion, submit: submit,
                        refreshBook: refreshBook, state: STATE, renderChart: renderChart };
})();
