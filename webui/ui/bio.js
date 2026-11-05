/* 生物过程动画（M10）：54 个课本过程的分步动画 + 文字说明。
   图和文字都由服务端 /api/bio 给（见 webui/biology.py），这里只负责选过程、翻步、播放。 */
(function () {
  'use strict';

  var STEP_MS = 1800;
  var DATA = { chapters: [], rows: [], detail: {}, key: '' };
  var PLAY = { timer: 0, playing: false, paused: false, index: 0 };
  var ANIM = { off: false };

  function stageSvg() {
    var stage = $('#bioStage');
    return stage ? stage.querySelector('svg') : null;
  }

  /* 图里的动画是 SVG 自带的（SMIL），所以暂停/继续直接调 SVG 的接口，
     不用自己写动画循环——老内核（Chromium 109）原生支持。 */
  function animOff() {
    var svg = stageSvg();
    if (!svg || !svg.pauseAnimations) return;
    ANIM.off = true;
    svg.pauseAnimations();
    var button = $('#bioAnim');
    if (button) button.textContent = '动画：关';
  }

  function animOn() {
    var svg = stageSvg();
    ANIM.off = false;
    if (svg && svg.unpauseAnimations) svg.unpauseAnimations();
    var button = $('#bioAnim');
    if (button) button.textContent = '动画：开';
  }

  function $(selector) { return document.querySelector(selector); }

  function post(payload) {
    var options = {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    };
    if (window.W7Api && window.W7Api.token) {
      options.headers['X-Auth-Token'] = window.W7Api.token;
    }
    return fetch('/api/bio', options).then(function (response) {
      return response.json().catch(function () { return {}; });
    });
  }

  function note(text, kind) {
    var box = $('#bioNote');
    if (!box) return;
    if (!text) { box.style.display = 'none'; box.textContent = ''; return; }
    box.style.display = '';
    box.textContent = text;
    box.className = 'notice' + (kind ? ' ' + kind : '');
  }

  function rowsOf(chapter) {
    var list = DATA.rows || [], out = [], order;
    if (!chapter || chapter === '全部') return list.slice(0);
    for (order = 0; order < list.length; order += 1) {
      if (list[order].chapter === chapter) out.push(list[order]);
    }
    return out;
  }

  function fillSelect(node, items, valueOf, labelOf) {
    if (!node) return;
    node.innerHTML = '';
    items.forEach(function (item) {
      var option = document.createElement('option');
      option.value = valueOf(item);
      option.textContent = labelOf(item);
      node.appendChild(option);
    });
  }

  function renderPicker() {
    var chapter = $('#bioChapter') ? $('#bioChapter').value : '全部';
    fillSelect($('#bioPick'), rowsOf(chapter), function (row) { return row.key; },
               function (row) { return row.name + '（' + row.steps.length + ' 步）'; });
  }

  function show(index) {
    var row = DATA.detail[DATA.key];
    if (!row) return;
    var total = (row.process.steps || []).length;
    if (index < 0) index = 0;
    if (index > total - 1) index = total - 1;
    PLAY.index = index;
    var total = (row.process.steps || []).length;
    if (index < 0) index = 0;
    if (index > total - 1) index = total - 1;
    PLAY.index = index;
    /* 有现成图片/GIF 的过程：**不给步骤**（图自己就把过程讲完了），
       右栏改成"中文详细说明"（原图多是外文，逐句翻译）。 */
    var hasMedia = !!row.process.media;
    var stage = $('#bioStage');
    if (stage) {
      if (hasMedia) {
        /* 图片比较抽象的过程（光反应/暗反应）：图上照旧显示，图**下面**再叠一段仿真动画 */
        var under = row.process.show_figure
          ? '<div style="flex:1 1 48%;min-height:0;display:flex;align-items:center;' +
            'margin-top:6px">' + (row.figures[index] || '') + '</div>' : '';
        stage.innerHTML = '<img class="bio-media bio-media-solo"' +
          (under ? ' style="max-height:42%"' : '') +
          ' src="' + row.process.media + '" alt="' + (row.process.name || '') + ' 插图">' + under;
      } else {
        stage.innerHTML = row.figures[index] || '';
      }
      if (ANIM.off) {
        var svg = stage.querySelector('svg');
        if (svg && svg.pauseAnimations) svg.pauseAnimations();
      } else {
        /* 场景是一轮循环动画：第 k 段要跳到这一段的相位去演，
           不然每段看到的都是同一个开头（老师指出过这个问题）。 */
        var svg2 = stage.querySelector('svg');
        var total2 = (row.process.steps || []).length;
        if (svg2 && svg2.setCurrentTime && total2 > 1) {
          svg2.setCurrentTime((index % total2) * (6.0 / total2));
        }
      }
    }
    var text = $('#bioText');
    if (text) {
      if (hasMedia) {
        text.innerHTML = '<div style="font-weight:600;font-size:13.5px;margin:2px 0 6px">' +
          (row.process.name || '') + '</div><div style="font-size:13px;line-height:1.75;' +
          'color:#2b3a46">' + (row.process.media_note || '') + '</div>';
      } else {
        /* 只显示"当前这一段"：步骤列表去掉了，跟着动画一段段换 */
        var steps = row.process.steps || [];
        text.innerHTML = '<div style="font-weight:600;font-size:13.5px;margin:2px 0 6px">' +
          (row.process.name || '') + '　第 ' + (index + 1) + ' / ' + steps.length + ' 段</div>' +
          '<div style="font-size:13.5px;line-height:1.8;color:#1f2d3a">' +
          (steps[index] || '') + '</div>';
      }
    }
    /* 步骤按钮一律不要了：各阶段自动连着播，每阶段停一下（见 armAuto）。
       只留一个「播放 / 暂停」让老师在课堂上控制。 */
    var controls = ['#bioPrev', '#bioNext', '#bioStepLabel', '#bioAnim'];
    for (var i = 0; i < controls.length; i += 1) {
      var node = $(controls[i]);
      if (node) node.style.display = 'none';
    }
    var playBtn = $('#bioPlay');
    if (playBtn) playBtn.style.display = total > 1 ? '' : 'none';
    armAuto(total);
  }

  /* 自动推进：每阶段停 2.4 秒，走到头回到第一阶段重新连播（老师可按暂停停住） */
  function armAuto(total) {
    if (PLAY.timer) { clearTimeout(PLAY.timer); PLAY.timer = 0; }
    if (!total || total < 2 || PLAY.paused) return;
    PLAY.timer = setTimeout(function () {
      show(PLAY.index + 1 >= total ? 0 : PLAY.index + 1);
    }, 2400);
  }

  function load(key) {
    if (!key) return Promise.resolve();
    if (DATA.detail[key]) {
      DATA.key = key;
      show(0);
      return Promise.resolve();
    }
    note('正在生成动画…');
    return post({ action: 'detail', key: key }).then(function (body) {
      if (!body || !body.ok) { note((body && body.error) || '取不到这个过程', 'warn'); return; }
      DATA.detail[key] = body;
      DATA.key = key;
      note('');
      show(0);
    }).catch(function (error) { note(String(error), 'warn'); });
  }

  function pause() {
    PLAY.playing = false;
    if (PLAY.timer) { clearTimeout(PLAY.timer); PLAY.timer = 0; }
    var svg = stageSvg();
    if (svg && svg.pauseAnimations) svg.pauseAnimations();
    var button = $('#bioPlay');
    if (button) button.textContent = '播放';
  }

  function play() {
    var row = DATA.detail[DATA.key];
    if (!row) { return; }
    var total = (row.process.steps || []).length;
    if (PLAY.paused) {                       /* 继续连播 */
      PLAY.paused = false;
      animOn();
      var resume = $('#bioPlay');
      if (resume) resume.textContent = '暂停';
      armAuto(total);
      return;
    }
    PLAY.paused = true;                      /* 暂停 */
    if (PLAY.timer) { clearTimeout(PLAY.timer); PLAY.timer = 0; }
    var button = $('#bioPlay');
    if (button) button.textContent = '继续播放';
  }

  function openPanel(key) {
    var panel = $('#bioPanel');
    if (panel) panel.classList.remove('hidden');
    if (DATA.rows.length) {
      if (key) { if ($('#bioPick')) $('#bioPick').value = key; load(key); }
      return Promise.resolve();
    }
    note('正在读取过程清单…');
    return post({ action: 'list' }).then(function (body) {
      if (!body || !body.ok) { note((body && body.error) || '读不到过程清单', 'warn'); return; }
      DATA.chapters = body.chapters || [];
      DATA.rows = body.processes || [];
      var chapters = [{ name: '全部' }].concat(DATA.chapters.map(function (name) {
        return { name: name };
      }));
      fillSelect($('#bioChapter'), chapters, function (row) { return row.name; },
                 function (row) { return row.name; });
      renderPicker();
      note('');
      var first = key || (DATA.rows[0] && DATA.rows[0].key);
      if (first) { if ($('#bioPick')) $('#bioPick').value = first; load(first); }
    }).catch(function (error) { note(String(error), 'warn'); });
  }

  function closePanel() {
    pause();
    var panel = $('#bioPanel');
    if (panel) panel.classList.add('hidden');
  }

  function bind() {
    var on = function (selector, handler) {
      var node = $(selector);
      if (node) node.onclick = handler;
    };
    on('#bioClose', closePanel);
    on('#bioPlay', play);
    on('#bioAnim', function () { if (ANIM.off) { animOn(); } else { animOff(); } });
    on('#bioPrev', function () { pause(); show(PLAY.index - 1); });
    on('#bioNext', function () { pause(); show(PLAY.index + 1); });
    on('#bioPhet', function () {
      if (window.W7Phet) { closePanel(); window.W7Phet.open('生物'); }
    });
    if ($('#bioChapter')) {
      $('#bioChapter').onchange = function () {
        pause();
        renderPicker();
        var pick = $('#bioPick');
        if (pick && pick.value) load(pick.value);
      };
    }
    if ($('#bioPick')) {
      $('#bioPick').onchange = function () {
        if (PLAY.timer) pause();
        PLAY.index = 0;
        load($('#bioPick').value);
      };
    }
    if ($('#btnBio')) $('#btnBio').onclick = function () { openPanel(); };
    document.addEventListener('keydown', function (event) {
      var panel = $('#bioPanel');
      if (!panel || panel.classList.contains('hidden')) return;
      if (event.key === 'Escape') { closePanel(); return; }
      if (event.key === 'ArrowRight') { pause(); show(PLAY.index + 1); }
      if (event.key === 'ArrowLeft') { pause(); show(PLAY.index - 1); }
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bind);
  } else {
    bind();
  }

  window.W7Bio = { openPanel: openPanel, closePanel: closePanel, show: show, play: play,
                   pause: pause, state: DATA };
})();
