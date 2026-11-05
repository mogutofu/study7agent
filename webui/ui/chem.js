/* 化学面板：配平 / 反应预测 / 查物质与结构 / 反应热。
 *
 * 和 agent 那边的 chem 工具走**同一个引擎**（服务端 /api/chem 转给 webui\chem.py），
 * 不在前端重写化学逻辑——两边答案不一致比没有功能更糟。
 *
 * 原生 ES5，不许构建；图标一律内联 SVG（Win7 没有 emoji 字体）。
 */
(function () {
  'use strict';

  var HISTORY = [];          /* 最近几条结果，方便老师回看 */

  function $(selector) { return document.querySelector(selector); }

  function post(payload) {
    if (!window.W7Api || !window.W7Api.post) {
      return Promise.reject(new Error('接口还没准备好'));
    }
    return window.W7Api.post('/api/chem', payload);
  }

  /* 有些"答案"就是 ok:false（配不平、不反应、资料未收录），不能当成请求失败。
     所以这些地方用 postRaw：拿到 JSON 自己判，不抛异常。 */
  function postRaw(payload) {
    var options = {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    };
    if (window.W7Api && window.W7Api.token) {
      options.headers['X-Auth-Token'] = window.W7Api.token;
    }
    return fetch('/api/chem', options).then(function (response) {
      return response.json().catch(function () { return {}; });
    });
  }

  function answerText(data) {
    if (!data) return '算不出来';
    return data.error || data.reason || '算不出来';
  }

  function clearBox() {
    var box = $('#chemResult');
    if (box) box.innerHTML = '';
    note('');
  }

  function note(text, kind) {
    var box = $('#chemNote');
    if (!box) return;
    if (!text) { box.style.display = 'none'; box.textContent = ''; return; }
    box.style.display = '';
    box.textContent = text;
    box.className = 'notice' + (kind ? ' ' + kind : '');
  }

  function line(className, text) {
    var node = document.createElement('div');
    node.className = className;
    node.textContent = text;
    return node;
  }

  function equationLine(text) {
    var node = document.createElement('div');
    node.className = 'chem-equation';
    node.textContent = text;
    return node;
  }

  function pushHistory(kind, text) {
    HISTORY.unshift({ kind: kind, text: text });
    if (HISTORY.length > 8) HISTORY.pop();
  }

  function showError(error) {
    note(error && error.message ? error.message : String(error), 'warn');
  }

  /* ---------------------------------------------------------------- 配平 */

  function balance(makeThermo) {
    var text = ($('#chemEquation') || {}).value || '';
    if (!text.trim()) { note('先写一条方程式', 'warn'); return; }
    clearBox();
    post({ action: 'balance', equation: text }).then(function (data) {
      if (makeThermo) { thermo(data.equation); return; }
      var box = $('#chemResult');
      box.appendChild(equationLine(data.equation));
      box.appendChild(line('chem-hint', '两边原子数已经对上了；要算反应热就点“算反应热”。'));
      pushHistory('配平', data.equation);
    }).catch(function (error) {
      /* 配不平不是错误，是结论：引擎会说明原因（元素没配全 / 两边写反了） */
      note('配不平：' + (error.message || error), 'warn');
    });
  }

  function thermo(equation) {
    var box = $('#chemResult');
    post({ action: 'thermo', equation: equation }).then(function (data) {
      box.appendChild(equationLine(data.equation));
      box.appendChild(line('chem-value',
        'ΔrH° = ' + data.dH + ' kJ/mol    ΔrS° = ' + data.dS + ' J/(mol·K)'));
      box.appendChild(line('chem-value',
        'ΔrG°(298 K) = ' + data.dG + ' kJ/mol    ' +
        (data.spontaneous ? '标准态下自发' : '标准态下不自发')));
      if (data.phases && data.phases.length) {
        box.appendChild(line('chem-hint', '用到的相态：' + data.phases.join('、')));
      }
      if (data.note) box.appendChild(line('chem-hint', data.note));
      pushHistory('反应热', equation + '  ΔrH°=' + data.dH);
    }).catch(function (error) {
      note(error.message || String(error), 'warn');
    });
  }

  /* ------------------------------------------------------------ 反应预测 */

  function predict() {
    var a = ($('#chemA') || {}).value || '';
    var b = ($('#chemB') || {}).value || '';
    if (!a.trim() || !b.trim()) { note('两种物质都要填', 'warn'); return; }
    clearBox();
    post({ action: 'predict', a: a, b: b }).then(renderReaction).catch(showError);
  }

  function replace() {
    var metal = ($('#chemMetal') || {}).value || '';
    var other = ($('#chemOther') || {}).value || '';
    if (!metal.trim() || !other.trim()) { note('金属和酸/盐溶液都要填', 'warn'); return; }
    clearBox();
    post({ action: 'replace', metal: metal, other: other }).then(renderReaction).catch(showError);
  }

  function renderReaction(data) {
    var box = $('#chemResult');
    if (!data || !data.ok) {
      /* 两种都不是错误：一是"确实不反应"（结论），二是"资料未收录"（不编造） */
      var reason = (data && (data.reason || data.error)) || '算不出来';
      note(reason + (data && data.detail ? '：' + data.detail : ''), 'warn');
      return;
    }
    box.appendChild(equationLine(data.equation));
    if (data.phenomenon) box.appendChild(line('chem-value', (applyFlameColor(data), applyReactionVisual(data), applyFlameColor(data), '现象：' + data.phenomenon + extraNote(data))));
    if (data.why) box.appendChild(line('chem-hint', '原因：' + data.why));
    if (data.predicted) {
      /* 这里是纯文本块（不是聊天里的 markdown 渲染），别写 ** 星号，会原样显示出来 */
      box.appendChild(line('chem-warn', '这是引擎按规则推出来的理论预测，和教材事实分开看。'));
    }
    box.appendChild(linkButton('算这条的反应热', function () { thermo(data.equation); }));
    pushHistory('反应', data.equation);
  }

  function linkButton(text, handler) {
    var button = document.createElement('button');
    button.className = 'btn chem-link';
    button.textContent = text;
    button.onclick = handler;
    return button;
  }

  /* -------------------------------------------------------- 查物质/结构 */

  function querySpecies() {
    var query = ($('#chemQuery') || {}).value || '';
    if (!query.trim()) { note('写个名字或分子式', 'warn'); return; }
    clearBox();
    post({ action: 'species', query: query }).then(function (data) {
      var box = $('#chemResult');
      if (!data.species || !data.species.length) {
        note('物种表里没有这个（随包表只有教材常见的那批）', 'warn');
        return;
      }
      data.species.forEach(function (row) {
        box.appendChild(line('chem-value',
          (row.name || '?') + '    ' + (row.formula_canonical || row.formula || '') +
          (row.cas ? '    CAS ' + row.cas : '')));
        if (row.smiles) box.appendChild(line('chem-hint', 'SMILES：' + row.smiles));
      });
      if (data.species[0].formula_canonical) {
        var formula = data.species[0].formula_canonical;
        box.appendChild(linkButton('拿 ' + formula + ' 去查结构', function () {
          if ($('#chemQuery')) $('#chemQuery').value = formula;
          queryStructures();
        }));
      }
    }).catch(showError);
  }

  function queryStructures() {
    var query = ($('#chemQuery') || {}).value || '';
    if (!query.trim()) { note('写个名字或分子式', 'warn'); return; }
    clearBox();
    post({ action: 'structure', query: query, limit: 20 }).then(function (data) {
      var box = $('#chemResult');
      if (!data.structures || !data.structures.length) {
        note('结构表里没有（ChEBI 那 20 万条里也没匹配上）', 'warn');
        return;
      }
      box.appendChild(line('chem-hint', '共匹配 ' + data.count + ' 条，显示前 ' +
        data.structures.length + ' 条（ChEBI，CC BY 4.0）：'));
      var table = document.createElement('table');
      table.className = 'chem-table';
      data.structures.forEach(function (row) {
        var tr = document.createElement('tr');
        [row.name, row.formula, row.smiles].forEach(function (value, index) {
          var td = document.createElement('td');
          td.textContent = value || '';
          if (index === 2) td.className = 'chem-smiles';
          tr.appendChild(td);
        });
        /* 点一行就把它的结构式画出来（有 SMILES 的才点得动） */
        if (row.smiles) {
          tr.setAttribute('data-smiles', row.smiles);
          tr.className = 'chem-clickable';
          tr.title = '点一下画这个分子的结构式';
          tr.onclick = function () {
            var input = $('#chemSmiles');
            if (input) input.value = row.smiles;
            drawStructure2d().catch(showError);
          };
        }
        table.appendChild(tr);
      });
      box.appendChild(table);
    }).catch(showError);
  }

  /* ------------------------------------------------------------ 实验台 */
  /* 器皿是**按形状画的 SVG**（不是 24×24 的小图标）：液面、液体颜色、固体、沉淀、气泡
     都画在器皿里，再配 CSS 动画。颜色与现象全部来自服务端引擎给的 visual 字段——
     前端不懂化学，只负责画。 */

  var GLASS = '#cfe0ef';          /* 玻璃描边 */
  var GLASS_FILL = 'rgba(210,232,246,.28)';

  /* 每种器皿：外形 + 内部裁剪形状（液体/固体只在里面画）+ 液体区域的坐标 */
  var SHAPES = {
    tube: {
      w: 120, h: 160,
      outline: 'M46 16h28v106a14 14 0 0 1-28 0z',
      rim: 'M42 16h36',
      inner: 'M49 22h22v98a11 11 0 0 1-22 0z',
      liquid: { x: 49, y: 30, w: 22, h: 100, bottom: 130 },
      marks: ''
    },
    beaker: {
      w: 120, h: 160,
      outline: 'M34 34h52v96a10 10 0 0 1-10 10h-32a10 10 0 0 1-10-10z',
      rim: 'M30 34h60',
      inner: 'M37 38h46v90a7 7 0 0 1-7 7h-32a7 7 0 0 1-7-7z',
      liquid: { x: 37, y: 46, w: 46, h: 88, bottom: 135 },
      marks: 'M42 56h9M42 74h9M42 92h9M42 110h9'
    },
    flask: {
      w: 120, h: 160,
      outline: 'M52 14h16v34l24 66a10 10 0 0 1-9 14h-46a10 10 0 0 1-9-14l24-66z',
      rim: 'M48 14h24',
      inner: 'M55 20h10v30l22 62a7 7 0 0 1-6 10h-42a7 7 0 0 1-6-10l22-62z',
      liquid: { x: 46, y: 74, w: 28, h: 46, bottom: 126 },
      marks: 'M48 92h6M46 106h6'
    },
    bottle: {
      w: 120, h: 160,
      outline: 'M38 44h44v92a8 8 0 0 1-8 8h-28a8 8 0 0 1-8-8z',
      rim: 'M34 40h52v6h-52z',
      inner: 'M41 48h38v86a5 5 0 0 1-5 5h-28a5 5 0 0 1-5-5z',
      liquid: { x: 41, y: 56, w: 38, h: 80, bottom: 137 },
      marks: ''
    },
    lamp: {
      w: 120, h: 160,
      outline: 'M40 96h40v26a8 8 0 0 1-8 8h-24a8 8 0 0 1-8-8z',
      rim: 'M56 96v-14h8v14',
      inner: '',
      liquid: null,
      marks: '',
      flame: 'M60 80c-8-9-6-16 0-24 6 8 8 15 0 24z'
    },
    burner: {
      /* 燃烧匙：一个金属小勺 + 一根手柄，跟试管完全不是一个样子；
         火焰路径给上，加热/点燃时 .lab-svg.heating 会让它烧起来（剧烈反应时再放大 1.6 倍） */
      w: 120, h: 160,
      outline: 'M46 52h46v8a23 16 0 0 1-46 0z',
      rim: 'M76 54h7v100h-7z',
      inner: 'M48 54h42v6a21 14 0 0 1-42 0z',
      liquid: { x: 49, y: 56, w: 40, h: 14, bottom: 70 },
      marks: '',
      flame: 'M69 50c-10-11-7-21 0-32 7 11 10 21 0 32z'
    },
    gasjar: {
      /* 集气瓶：广口瓶（上宽下平，瓶口带一圈厚边） */
      w: 120, h: 160,
      outline: 'M34 48h52v94a6 6 0 0 1-6 6H40a6 6 0 0 1-6-6z',
      rim: 'M28 40h64v9H28z',
      inner: 'M38 52h44v86a3 3 0 0 1-3 3H41a3 3 0 0 1-3-3z',
      liquid: { x: 38, y: 58, w: 44, h: 80, bottom: 138 },
      marks: ''
    },
    stand: {
      w: 120, h: 160,
      outline: 'M20 146h80v8h-80z',
      rim: '',
      inner: '',
      liquid: null,
      marks: '',
      rod: 'M57 30h6v116h-6z',
      clamp: 'M40 62h40v6h-40zM42 68a9 6 0 0 0 0 12'
    },
    trough: {
      w: 120, h: 160,
      outline: 'M14 86h92v56a8 8 0 0 1-8 8H22a8 8 0 0 1-8-8z',
      rim: 'M10 86h100',
      inner: 'M18 90h84v48a5 5 0 0 1-5 5H23a5 5 0 0 1-5-5z',
      liquid: { x: 18, y: 92, w: 84, h: 46, bottom: 138 },
      level: 0.62,
      marks: ''
    },
    autoclave: {
      /* 恒容密闭反应釜：厚壁圆柱 + 法兰盖 + 一个进气阀（要能看出"这是密封的"） */
      w: 120, h: 160,
      outline: 'M28 46h64a6 6 0 0 1 6 6v88a6 6 0 0 1-6 6H28a6 6 0 0 1-6-6V52a6 6 0 0 1 6-6z',
      rim: 'M20 34h80v12H20z',
      inner: 'M30 52h60v82a4 4 0 0 1-4 4H34a4 4 0 0 1-4-4z',
      rod: 'M92 22h8v14h-8z',
      marks: 'M30 108h60M30 128h60',
      sealed: true,
      liquid: { x: 30, y: 92, w: 60, h: 42, bottom: 134 }
    }
  };

  /* 接口点（SVG 局部坐标）：连导管就是在两个接口点之间拉一根玻璃管 */
  var PORTS = {
    tube: [{ id: '口', x: 60, y: 18 }],
    beaker: [{ id: '口', x: 60, y: 36 }],
    flask: [{ id: '口', x: 60, y: 16 }],
    bottle: [{ id: '口', x: 60, y: 42 }],
    trough: [{ id: '水面', x: 60, y: 92 }],
    autoclave: [{ id: '进气口', x: 96, y: 22 }],
    stand: [{ id: '夹', x: 60, y: 64 }]
  };
  /* 器皿在台面上的显示尺寸：比原始刻度放大 1.3 倍（老师反馈"分层/液面看不清"）——
     放大以后色带、液面、固体堆都明显得多。两个方向的比例必须相等，不然器皿会变形。 */
  var SVG_W = 120, SVG_H = 160, CSS_W = 171, CSS_H = 228;
  var SCALE_X = CSS_W / SVG_W, SCALE_Y = CSS_H / SVG_H;
  /* 试管倾斜：角度和支点必须和 style.css 里 .lab-item.tilt .lab-svg 一致，
     不然接口点算出来的位置和画出来的管口对不上，导管就"脱开"了。 */
  var TILT_DEG = -12, TILT_X = 86, TILT_Y = 195;

  function portPoint(item, index) {
    if (!item) return null;
    var list = PORTS[item.kind] || [];
    var port = list[index || 0];
    if (!port) return null;
    var x = port.x * SCALE_X, y = port.y * SCALE_Y;
    if (item.tilt) {
      var rad = TILT_DEG * Math.PI / 180;
      var dx = x - TILT_X, dy = y - TILT_Y;
      x = TILT_X + dx * Math.cos(rad) - dy * Math.sin(rad);
      y = TILT_Y + dx * Math.sin(rad) + dy * Math.cos(rad);
    }
    return { x: item.x + x, y: item.y + y };
  }

  var LAB = { items: [], seq: 0, catalog: null, links: [], connectMode: false, pending: null };

  /* 把十六进制色按比例调暗，用来画沉淀/固体的阴影 */
  function shade(color, ratio) {
    var match = /^#([0-9a-f]{6})$/i.exec(color || '');
    if (!match) return color || '#dfe5ec';
    var value = parseInt(match[1], 16);
    var parts = [(value >> 16) & 255, (value >> 8) & 255, value & 255].map(function (item) {
      return Math.max(0, Math.round(item * ratio));
    });
    return 'rgb(' + parts.join(',') + ')';
  }

  function labRoot() { return $('#chemLabBench'); }

  function labFind(id) {
    for (var index = 0; index < LAB.items.length; index++) {
      if (LAB.items[index].id === id) return LAB.items[index];
    }
    return null;
  }

  /* 器皿里"现在有什么"。
     化学**全在引擎里**：服务端 op=sim 会告诉我们每种物质算液体/固体/气体、什么颜色、
     还剩多少 mol、以及从加入药品开始的整条时间轨迹（含反应限度）。界面只按它画——
     所以"每点一次加热就多堆一层黑固体"这类问题不可能再出现：每一帧都是引擎给的组成。 */
  function labState(item) {
    if (!item.state) {
      item.state = { heating: false, temperature: 298.15, play: null, gasIn: false,
                     gas: false, timer: null };
    }
    return item.state;
  }

  function labMap(rows) {
    var out = {};
    (rows || []).forEach(function (row) { out[row.key] = row.mol; });
    return out;
  }

  /* 容器里有哪些物质（化学式，按加入顺序，去重） */
  function labKeys(item) {
    var out = [];
    (item.contents || []).forEach(function (row) {
      if (out.indexOf(row.key) < 0) out.push(row.key);
    });
    return out;
  }

  /* 这一帧画多少：优先用正在播的那一帧，其次是这次模拟的终态，最后才是用户加进去的量 */
  function labAmounts(item) {
    var state = labState(item);
    if (state.play) return state.play;
    if (item.sim && item.sim.amounts) return item.sim.amounts;
    var out = {};
    (item.contents || []).forEach(function (row) { out[row.key] = row.mol || 0; });
    return out;
  }

  /* 液面高度：按**溶在里面的物质的量**算（0.05 mol 一份约为半管多一点） */
  function labLevel(mol) {
    return Math.min(0.88, 0.20 + 0.62 * (mol / (mol + 0.04)));
  }

  /* 把"引擎给的组成"翻译成"这一帧要画的样子"（颜色、液面、固体、沉淀、气泡） */
  function labLook(item) {
    var amounts = labAmounts(item);
    var sim = item.sim || {};
    var appearance = sim.appearance || {};
    var visual = sim.visual || {};
    var state = labState(item);
    var liquid = 0;
    var color = '';
    var solids = [];
    var precipitate = null;
    /* 会分层的东西（有机层）由色带画，不再在杯底画成一堆固体——不然同一份油脂画两遍 */
    var layered = {};
    (visual.layers || []).forEach(function (row) { layered[row.key] = true; });
    Object.keys(amounts).forEach(function (key) {
      var mol = amounts[key] || 0;
      if (mol <= 1e-9) return;
      var look = appearance[key] || {};
      if (layered[key]) {
        if (look.kind !== 'gas') liquid += mol;      /* 分层也算进"杯里有多少液体"（决定液面高度） */
        return;
      }
      if (look.kind === 'solid') {
        solids.push({ formula: key, mol: mol, color: look.color });
      } else if (look.kind === 'precipitate') {
        solids.push({ formula: key, mol: mol, color: look.color });
        if (!precipitate) precipitate = { formula: key, color: look.color };
      } else if (look.kind !== 'gas') {
        liquid += mol;
        if (!color && look.color) color = look.color;
      }
    });
    /* 滴了指示剂就整杯变色（酸碱性是引擎判的，见 rules.indicators） */
    var indicator = (sim.indicator || [])[0];
    var before = visual.solution_before || '';
    var after = visual.solution_after || '';
    var alpha = visual.solution_alpha_after || 0;
    var level = labLevel(liquid);
    /* 扣在水槽里收集气体（排水法）：瓶里本来是满的水，气体进来把水挤出去，
       液面就一层层降下来——"收集到多少"由导管那头的出气量给（state.collected）。 */
    var shape = SHAPES[item.kind];
    if (item.in && shape && shape.liquid) {
      level = Math.max(0.06, 0.88 - 0.8 * Math.min(1, (state.collected || 0) / COLLECT_FULL));
    }
    /* 分层：引擎告诉我们哪一层在上、哪一层在下、什么颜色，这里排成"从上到下的色带"。
       最多可能三条：未反应的苯浮在最上面、水层中间、硝基苯沉在最下面。 */
    var layers = visual.layers || [];
    var bands = [];
    var oilMol = 0;
    var aqueousMol = layers.length ? (layers[0].aqueous_mol || 0) : 0;
    layers.forEach(function (row) {
      oilMol += row.mol || 0;
      if (row.where === 'up') {
        bands.push({ color: row.color, mol: row.mol, solid: row.solid });
      }
    });
    if (layers.length && aqueousMol > 1e-9) {
      bands.push({ color: color || after || before || 'rgba(150,205,235,.55)',
                   mol: aqueousMol, aqueous: true });
    }
    layers.forEach(function (row) {
      if (row.where === 'down') {
        bands.push({ color: row.color, mol: row.mol, solid: row.solid });
      }
    });
    var bandTotal = 0;
    bands.forEach(function (band) { bandTotal += band.mol; });
    if (bandTotal > 0 && bands.length > 1) {
      /* 每一层至少占 12% 高，不然薄薄一层看不出来；再归一化回总高度 */
      var sum = 0;
      bands.forEach(function (band) { band.share = Math.max(0.12, band.mol / bandTotal); sum += band.share; });
      bands.forEach(function (band) { band.share = band.share / sum; });
    } else {
      bands = [];
    }
    return {
      color: indicator ? indicator.color : (color || after || before),
      /* 颜色浓淡："蓝色变浅"也画得出来（同一个色、淡一点） */
      alpha: indicator ? 0.85 : Math.max(0.34, Math.min(0.85, 0.42 + 0.5 * alpha)),
      level: level,
      layers: layers,
      bands: bands,
      solids: solids,
      precipitate: precipitate,
      gas: !!state.gas && !!((visual.gas || []).length || state.gasIn),
      gasIn: !!state.gasIn
    };
  }

  /* 排水法：集气瓶/试管装满水的量（mol）——到了这个量就算收集满一瓶 */
  var COLLECT_FULL = 0.02;

  /* 出气的那一头接上导管以后，气体是"沿着导管走到另一头"的：
     导管上跑气泡（labRenderWires 里画），另一头要是能盛液体（水槽/集气瓶）就跟着冒泡、
     扣在水槽里的集气瓶还会被"排"出水来。

     方向必须记下来：以前连线是从**第一次点的那个接口**画到第二个，所以气体方向跟着点击顺序走，
     点反了动画就反着跑。现在 `link.from` 记的是**真正出气的那一头**，画线时永远从它出发。 */
  /* 现象后面补一句气体颜色与剧烈程度（引擎已经给出 gas_colors / intensity） */
  /* 气体按引擎给的颜色画、剧烈反应画得更凶（引擎结果里的 gas_colors / intensity） */
  var GAS_COLOR_CSS = { '黄绿色': '#b8d94a', '淡黄绿色': '#d6e86a', '红棕色': '#a1502a',
                        '红棕色（蒸气）': '#a1502a', '紫红色（蒸气）': '#8a4fb0',
                        '无色': '#e9f3fb', '无色（有刺激性气味）': '#e9f3fb',
                        '无色（瓶口有白雾）': '#eef5fb', '无色（臭鸡蛋气味）': '#e9f3fb' };
  /* 焰色反应：颜色记在**正在做实验的那件器皿**上，再写进火焰 path 的内联样式
     （内联样式不会被任何 CSS 盖掉；重画器皿时 labSvg 照着状态再写一遍，不会丢）。
     血的教训：面板容器的 id 以前**写错了**（少看了一个字母、指向一个不存在的元素），
     querySelector 一直返回 null 当场 return，所以火焰颜色从来就没生效过，全是同一个橙黄。 */
  /* 焰色 / 燃烧火焰的颜色名 -> CSS 颜色。数据里出现的每一个色名这里都必须有，
     否则会退回默认橙黄、看着又"全是一个色"（自检里有交叉检查，见 TestChemLab）。 */
  var FLAME_COLOR_CSS = { '黄色': '#f5d33a', '浅紫色（透过蓝色钴玻璃看）': '#b39ddb',
                          '砖红色': '#c1440e', '洋红色': '#e0409a', '黄绿色': '#a8d04a',
                          '绿色': '#3fae6a', '紫红色': '#c2185b', '紫色': '#8e5bd0',
                          '蓝色': '#3f7fe0', '淡蓝色': '#8ec8f0',
                          '淡蓝色（在氧气中蓝紫色）': '#9b8ef0',
                          '明亮（带黑烟）': '#ffd76a', '明亮（浓黑烟）': '#ffcf4d',
                          '黄白色（大量白烟）': '#fff2b8', '耀眼白光': '#fdfdff',
                          '火星四射': '#ff9b3d', '红热（无焰）': '#e2603a' };
  function flameCssOf(item) {
    var state = item ? labState(item) : null;
    return (state && state.flameCss) || '';
  }
  function applyFlameColor(row, itemId) {
    if (!row || !row.flame_color) return;
    var item = itemId ? labFind(itemId) : null;
    if (!item) return;                     /* 没台面（反应预测那种）就没有火焰可上色 */
    var css = FLAME_COLOR_CSS[row.flame_color] || '#f0a92b';
    labState(item).flameCss = css;         /* 记在器皿上：重画器材时 labSvg 会照它再写一遍 */
    var node = document.querySelector('.lab-item[data-lab-id="' + itemId + '"]');
    if (!node) return;
    var flames = node.querySelectorAll('.lab-flame');
    for (var i = 0; i < flames.length; i++) flames[i].style.fill = css;
  }

  function applyReactionVisual(row) {
    var panel = document.querySelector('#chemPaneLab');
    if (!panel || !row) return;
    var colors = row.gas_colors || {};
    for (var key in colors) {
      if (Object.prototype.hasOwnProperty.call(colors, key)) {
        panel.style.setProperty('--gas-color', GAS_COLOR_CSS[colors[key]] || '#dbe8f5');
        break;
      }
    }
    if (row.intensity === 'violent') {
      panel.classList.add('chem-violent');
      clearTimeout(panel._violentTimer);
      panel._violentTimer = setTimeout(function () { panel.classList.remove('chem-violent'); }, 3000);
    }
  }

  function extraNote(row) {
    if (!row) return '';
    var out = [];
    var colors = row.gas_colors || {};
    for (var key in colors) {
      if (Object.prototype.hasOwnProperty.call(colors, key)) out.push(key + '：' + colors[key]);
    }
    if (out.length) out = ['气体颜色 ' + out.join('，')];
    if (row.intensity === 'violent') out.push('反应很剧烈');
    return out.length ? '（' + out.join('；') + '）' : '';
  }

  function labFlow(item, on, gasMol) {
    LAB.links.forEach(function (link) {
      var otherId = null;
      if (link.a.item === item.id) otherId = link.b.item;
      else if (link.b.item === item.id) otherId = link.a.item;
      if (!otherId) return;
      link.flowing = on;
      link.from = on ? item.id : null;
      var other = labFind(otherId);
      var shape = other ? SHAPES[other.kind] : null;
      if (shape && shape.liquid) {
        var state = labState(other);
        state.gasIn = on;
        state.collected = on ? (gasMol || 0) : 0;
        /* 排水法：真正在收集气体的是**扣在水槽里的那个瓶子**，气体量记在它头上
           （水槽自己的水位几乎不变，看得见的是瓶子里的水被排出去）。 */
        if (other.kind === 'trough') {
          LAB.items.forEach(function (nested) {
            if (nested.in !== other.id) return;
            var nestedState = labState(nested);
            nestedState.gasIn = on;
            nestedState.collected = state.collected;
          });
        }
      }
    });
  }

  /* 可加热的器皿底部也画一簇火：老师点「加热」时火就在器皿下面，
     不用非得摆一个酒精灯（摆了的话酒精灯自己也会亮，见 labRender 里的 lamp 处理）。 */
  var BOTTOM_FLAME = 'M60 152c-9-8-7-15 0-22 7 7 9 14 0 22z';

  function labSvg(item) {
    var shape = SHAPES[item.kind] || SHAPES.tube;
    var state = labState(item);
    var look = labLook(item);
    var clip = 'labclip-' + item.id;
    var parts = [];
    var flameFill = flameCssOf(item);
    var flameStyle = flameFill ? ' style="fill:' + flameFill + '"' : '';
    parts.push('<svg class="lab-svg' + (state.heating ? ' heating' : '') +
      '" viewBox="0 0 ' + shape.w + ' ' + shape.h + '">');
    parts.push('<defs><clipPath id="' + clip + '"><path d="' + shape.inner + '"/></clipPath></defs>');
    if (shape.rod) parts.push('<path class="lab-metal" d="' + shape.rod + '"/>');
    if (shape.clamp) parts.push('<path class="lab-metal" d="' + shape.clamp + '"/>');
    if (shape.liquid && state.heating) {
      parts.push('<path class="lab-flame bottom" d="' + BOTTOM_FLAME + '"' + flameStyle + '/>');
    }
    if (shape.liquid) {
      var area = shape.liquid;
      /* 水槽默认就有水（排水法收集本来就是这么用的），别的器皿从空开始 */
      var level = look.level || shape.level || 0;
      var height = Math.max(0, Math.min(1, level)) * area.h;
      /* 扣在水槽里的器皿是**倒扣**的（CSS 把整个 svg 转 180°）：所以液体要贴着"瓶底"那侧画，
         转过来以后水才在下面（瓶口那侧），气体在上面（瓶底那侧）——排水法的样子。 */
      var surface = item.in ? area.y : area.bottom - height;
      parts.push('<g clip-path="url(#' + clip + ')">');
      if (height > 2) {
        var aqueous = look.color || 'rgba(150,205,235,.35)';
        if (!item.in && (look.bands || []).length) {
          /* 分层：从上到下画几条色带，之间画分界线（有机层在上还是在下按密度，引擎给的）。
             每层至少 9 像素高（薄的有机层在投影上就看不见了），超了再整体按比例压回去。 */
          var heights = look.bands.map(function (band) {
            return Math.max(9, height * band.share);
          });
          var heightsTotal = 0;
          heights.forEach(function (value) { heightsTotal += value; });
          var scale = heightsTotal > height ? height / heightsTotal : 1;
          var cursor = surface;
          look.bands.forEach(function (band, index) {
            var bandHeight = heights[index] * scale;
            /* 固体有机层（油脂、硬脂酸）画实一点，液态的带一点透明 */
            parts.push('<rect class="lab-liquid" x="' + area.x + '" y="' + cursor +
              '" width="' + area.w + '" height="' + bandHeight + '" style="opacity:' +
              (band.solid ? 1 : look.alpha) + '" fill="' + (band.color || aqueous) + '"/>');
            if (index < look.bands.length - 1) {
              parts.push('<path class="lab-layer-line" d="M' + area.x + ' ' +
                (cursor + bandHeight) + 'H' + (area.x + area.w) + '"/>');
            }
            cursor += bandHeight;
          });
        } else {
          parts.push('<rect class="lab-liquid" x="' + area.x + '" y="' + surface +
            '" width="' + area.w + '" height="' + height +
            '" style="opacity:' + look.alpha + '" fill="' + aqueous + '"/>');
        }
        /* 液面：一道亮一点的椭圆，看着才像有水面 */
        parts.push('<ellipse class="lab-surface" cx="' + (area.x + area.w / 2) + '" cy="' +
          (item.in ? surface + height : surface) + '" rx="' + (area.w / 2) + '" ry="3.2" fill="' +
          (look.color ? shade(look.color, 1.25) : 'rgba(190,225,245,.75)') + '"/>');
      }
      /* 固体按**物质的量**堆：加得多就堆得高一点、宽一点（不会每点一次加热就多一层） */
      (look.solids || []).forEach(function (solid, index) {
        var share = Math.min(1, (solid.mol || 0) / 0.02);
        var width = Math.max(10, (area.w - 6 - index * 4) * (0.45 + 0.55 * share));
        var thick = 2.4 + 3.4 * share;
        var centerY = area.bottom - 3 - index * 4.5;
        parts.push('<ellipse class="lab-solid" cx="' + (area.x + area.w / 2) + '" cy="' +
          centerY + '" rx="' + (width / 2) + '" ry="' + thick + '" fill="' +
          (solid.color || '#c9ced6') + '"/>');
        parts.push('<ellipse cx="' + (area.x + area.w / 2) + '" cy="' + (centerY - thick * 0.4) +
          '" rx="' + (width / 2 - 2) + '" ry="' + (thick * 0.7) + '" fill="' +
          shade(solid.color || '#c9ced6', 1.18) + '" opacity=".55"/>');
      });
      if (look.precipitate) {
        /* 沉淀：底下厚厚一层 + 上面还在慢慢下沉的一点浑浊 */
        var pcolor = look.precipitate.color || '#f2f4f7';
        parts.push('<ellipse class="lab-precipitate" cx="' + (area.x + area.w / 2) + '" cy="' +
          (area.bottom - 4) + '" rx="' + (area.w / 2 - 1) + '" ry="7" fill="' + pcolor + '"/>');
        parts.push('<ellipse class="lab-precipitate" cx="' + (area.x + area.w / 2) + '" cy="' +
          (area.bottom - 9) + '" rx="' + (area.w / 2 - 4) + '" ry="4.5" fill="' +
          shade(pcolor, 0.94) + '"/>');
        parts.push('<ellipse cx="' + (area.x + area.w / 2) + '" cy="' + (area.bottom - 16) +
          '" rx="' + (area.w / 2 - 7) + '" ry="3" fill="' + pcolor + '" opacity=".28"/>');
      }
      if (look.gas || look.gasIn) {
        for (var index = 0; index < 6; index++) {
          var bubbleX = area.x + 4 + (index % 3) * (area.w / 3);
          var bubbleY = area.bottom - 8 - (index % 2) * 6;
          parts.push('<circle class="lab-bubble b' + index + '" cx="' + bubbleX + '" cy="' +
            bubbleY + '" r="' + (2.2 + (index % 3) * 0.6) + '" fill="rgba(255,255,255,.85)"/>');
        }
      }
      parts.push('</g>');
    }
    if (shape.flame) {
      parts.push('<path class="lab-flame" d="' + shape.flame + '"' + flameStyle + '/>');
    }
    parts.push('<path class="lab-glass-fill" d="' + shape.outline + '" fill="' + GLASS_FILL + '"/>');
    parts.push('<path class="lab-glass" d="' + shape.outline + '"/>');
    if (shape.rim) parts.push('<path class="lab-glass" d="' + shape.rim + '"/>');
    if (shape.marks) parts.push('<path class="lab-marks" d="' + shape.marks + '"/>');
    parts.push(labPortsHtml(item));
    parts.push('</svg>');
    return parts.join('');
  }

  /* 台面上器材最多放到这个 y：再往下标题和按钮就被台面框切掉了（台面高 348、器材连标题带按钮 226） */
  var LAB_MAX_Y = 118;
  /* 能扣进水槽里收集气体的器皿（排水法）；别的器材不给放进去，免得看不出在干什么 */
  var NESTABLE = { bottle: true, tube: true };

  /* 器材实际画在哪：扣在水槽里的会跟着水槽走，还缩小一点（看着就是"在里面"） */
  function labSpot(item) {
    var spot = { x: item.x, y: item.y, scale: 1, inside: false };
    if (!item.in) return spot;
    var host = labFind(item.in);
    if (!host) {
      item.in = null;
      return spot;
    }
    var area = (SHAPES[host.kind] || SHAPES.trough).liquid || { x: 18, y: 92, w: 84 };
    return { x: host.x + (area.x + area.w * 0.16) * SCALE_X,
             y: host.y + (area.y - 18) * SCALE_Y,
             scale: 0.5, inside: true };
  }

  function labPlace(kind, label, x, y, key) {
    LAB.seq += 1;
    var item = {
      id: 'lab' + LAB.seq, kind: kind, label: label, key: key || '',
      /* 默认沿台面排一排，站在那条台面线上 */
      x: (typeof x === 'number' ? x : 18 + (LAB.items.length % 6) * 183),
      y: Math.max(0, Math.min(LAB_MAX_Y, typeof y === 'number' ? y : LAB_MAX_Y)),
      contents: []
    };
    LAB.items.push(item);
    labRender();
    return item;
  }

  function labDrawItem(item) {
    var node = document.createElement('div');
    node.className = 'lab-item';
    node.setAttribute('data-lab-id', item.id);
    var spot = labSpot(item);
    node.style.left = spot.x + 'px';
    node.style.top = spot.y + 'px';
    if (spot.inside) {
      /* 扣在水槽里的器皿：小一号，标题也小一点 */
      node.className += ' inside';
      node.style.transform = 'scale(' + spot.scale + ')';
      node.style.transformOrigin = '0 0';
    }
    if (item.tilt) node.className += ' tilt';
    if (LAB.connectMode && (PORTS[item.kind] || []).length && !item.in) {
      node.className += ' connectable';
    }
    node.innerHTML = labSvg(item);
    /* 连接模式：点器材就是点它的接口点（点到接口圆圈上就用那个接口，否则算第一个） */
    node.onclick = function (event) { labClickItem(item, event); };
    /* 抓住器材本体可以挪位置（按在「加热 / 倒掉」这些按钮上不算） */
    node.addEventListener('mousedown', function (event) {
      labStartDrag(event, { move: item.id, label: item.label, kind: item.kind });
    });
    var caption = document.createElement('div');
    caption.className = 'lab-caption';
    var state = labState(item);
    var marks = [];
    var keys = labKeys(item);
    if (keys.length) {
      /* 标题别写全（一长串会把按钮挤下去）：最多显示两种，多的用省略号。
         沉淀/限度放在结果框里，不占标题行。 */
      marks.push(keys.slice(0, 2).join('+') + (keys.length > 2 ? '…' : ''));
    }
    caption.textContent = item.label + (marks.length ? '：' + marks.join(' ') : '');
    /* 水槽里扣着东西的话，写在**水槽**这行的标题上：扣进去的器皿自己不再画标题，
       不然两个标题会叠在同一个位置（"水槽集气瓶"糊成一团）。 */
    var nestled = [];
    if (item.kind === 'trough') {
      LAB.items.forEach(function (other) {
        if (other.in === item.id) nestled.push(other.label);
      });
    }
    if (nestled.length) caption.textContent += '（扣着' + nestled.join('、') + '）';
    node.appendChild(caption);
    /* 右上角的「×」：把这件器材从台面上拿掉（连线、扣在里面的东西一起收拾干净）。
       老师反馈"加了就删不掉"，所以每件器材都得有个出口。 */
    var remove = document.createElement('button');
    remove.className = 'btn lab-remove';
    remove.textContent = '×';
    remove.title = '把' + item.label + '从台面上拿掉';
    remove.onclick = function (event) {
      event.stopPropagation();
      labRemove(item.id);
    };
    node.appendChild(remove);
    /* 试管多一个「倾斜」：加热固体时管口要略向下倾斜，倾斜状态是几何，检查装置要看它 */
    if (item.kind === 'tube' || item.contents.length) {
      var tools = document.createElement('div');
      tools.className = 'lab-tools';
      if (item.kind === 'tube') {
        var tilt = document.createElement('button');
        tilt.className = 'btn lab-mini';
        tilt.textContent = item.tilt ? '竖直' : '倾斜';
        tilt.onclick = function (event) {
          event.stopPropagation();
          item.tilt = !item.tilt;
          labRender();
        };
        tools.appendChild(tilt);
      }
      if (item.contents.length) {
        var state0 = labState(item);
        var heat = document.createElement('button');
        heat.className = 'btn lab-mini' + (state0.heating ? ' active' : '');
        /* 一个按钮走完温度阶梯：常温 -> 加热 -> 强热 -> 常温。
           温度是**条件**：引擎按它决定反应快不快、平衡往哪边偏。 */
        heat.textContent = state0.temperature > 1273 - 0.5 ? '停止加热'
          : (state0.heating ? '强热' : '加热');
        heat.onclick = function (event) {
          event.stopPropagation();
          labHeat(item.id).catch(showError);
        };
        var clear = document.createElement('button');
        clear.className = 'btn lab-mini';
        clear.textContent = '倒掉';
        clear.onclick = function (event) {
          event.stopPropagation();
          item.contents = [];
          item.sim = null;
          item.state = null;
          labFlow(item, false);
          labRender();
          note('');
        };
        tools.appendChild(heat);
        tools.appendChild(clear);
      }
      node.appendChild(tools);
    }
    return node;
  }

  /* 把一件器材从台面上拿掉：连线、扣在里面的东西、正在播的动画都要一起收拾，
     不然会留下"指向不存在的器材"的连线（画出来是一条断线）。 */
  function labRemove(itemId) {
    var item = labFind(itemId);
    if (!item) return;
    if (labState(item).timer) clearTimeout(labState(item).timer);
    LAB.links = LAB.links.filter(function (link) {
      return link.a.item !== itemId && link.b.item !== itemId;
    });
    LAB.items.forEach(function (other) {
      if (other.in === itemId) other.in = null;
    });
    LAB.items = LAB.items.filter(function (other) { return other.id !== itemId; });
    note('已把' + item.label + '从台面上拿掉');
    labRender();
  }

  /* 清空整个台面（器材 + 试剂 + 连线）。误点会丢一节课的摆法，所以问一句。 */
  function labClear() {
    if (!LAB.items.length) {
      note('台面上已经没有东西了');
      return;
    }
    if (window.confirm && !window.confirm('清空整个台面？（器材、试剂、连线都会清掉）')) return;
    LAB.items.forEach(function (item) {
      if (labState(item).timer) clearTimeout(labState(item).timer);
    });
    LAB.items = [];
    LAB.links = [];
    LAB.pending = null;
    LAB.connectMode = false;
    labConnectChip(false);
    var box = $('#chemLabResult');
    if (box) box.innerHTML = '';
    note('台面已清空');
    labRender();
  }

  function labRender() {
    var root = labRoot();
    if (!root) return;
    root.innerHTML = '';
    labVesselBar();
    if (!LAB.items.length) {
      var hint = document.createElement('div');
      hint.className = 'lab-empty';
      hint.textContent = '把左边的器材拖到台面上（点一下也能直接放上来），再把药瓶拖到器材上就是加试剂：' +
        '反应从加进去那一刻就开始，按反应限度自己进行，条件不够会写明缺什么；' +
        '点「加热」升温（再点继续升到强热，再点回常温）。点「导管」进连接模式可以把装置连起来';
      root.appendChild(hint);
    }
    LAB.items.forEach(function (item) { root.appendChild(labDrawItem(item)); });
    labRenderWires();
  }

  /* 导管：两个接口点之间的一段玻璃管。连线画在台面下面那层 SVG 里，
     器材挪动时跟着重画，所以看起来"接在一起"。 */
  function labRenderWires() {
    var layer = $('#chemLabWires');
    if (!layer) return;
    var parts = [];
    LAB.links.forEach(function (link, index) {
      var a = portPoint(labFind(link.a.item), link.a.port);
      var b = portPoint(labFind(link.b.item), link.b.port);
      if (!a || !b) return;
      /* 出气的那一头当起点：气泡才会从发生装置流向收集装置（跟着点击顺序画就会反） */
      if (link.flowing && link.from && link.from === link.b.item) {
        var swap = a;
        a = b;
        b = swap;
      }
      var lift = Math.max(34, Math.abs(a.x - b.x) * 0.28);
      var path = 'M' + a.x + ' ' + a.y +
        ' C' + a.x + ' ' + (a.y - lift) + ' ' + b.x + ' ' + (b.y - lift) + ' ' + b.x + ' ' + b.y;
      var id = 'labwire' + index;
      parts.push('<path id="' + id + '" class="lab-wire-shadow" d="' + path + '"/>');
      parts.push('<path class="lab-wire" d="' + path + '"/>');
      if (link.flowing) {
        /* 气体沿导管走：SMIL 的 animateMotion 沿同一条路径动，不需要 JS 定时器 */
        parts.push('<circle class="lab-gas" r="3.4">' +
          '<animateMotion dur="1.8s" repeatCount="indefinite">' +
          '<mpath href="#' + id + '"/></animateMotion></circle>');
        parts.push('<circle class="lab-gas" r="2.6">' +
          '<animateMotion dur="1.8s" begin="0.6s" repeatCount="indefinite">' +
          '<mpath href="#' + id + '"/></animateMotion></circle>');
      }
    });
    layer.innerHTML = parts.join('');
  }

  /* 接口点：扣在水槽里的器皿不给接口（排水法是把导管伸到水槽里、扣在瓶口下面） */
  function labPortsHtml(item) {
    if (!LAB.connectMode || item.in) return '';
    var list = PORTS[item.kind] || [];
    return list.map(function (port, index) {
      var pending = LAB.pending && LAB.pending.item === item.id && LAB.pending.port === index;
      return '<circle class="lab-port' + (pending ? ' pending' : '') + '" data-lab-port="' +
        index + '" cx="' + port.x + '" cy="' + port.y + '" r="7"/>';
    }).join('');
  }

  function labClickItem(item, event) {
    if (!LAB.connectMode) return false;
    var portIndex = 0;
    var node = event && event.target;
    if (node && node.getAttribute && node.getAttribute('data-lab-port') !== null) {
      portIndex = parseInt(node.getAttribute('data-lab-port'), 10) || 0;
    }
    if (!LAB.pending) {
      LAB.pending = { item: item.id, port: portIndex };
      note('已选好一个接口点（' + item.label + '），再点另一个器材的接口点就接上了', '');
    } else if (LAB.pending.item === item.id) {
      LAB.pending = null;
      note('');
    } else {
      LAB.links.push({ a: LAB.pending, b: { item: item.id, port: portIndex } });
      LAB.pending = null;
      LAB.connectMode = false;
      labConnectChip(false);        /* 接过一根就退出连接模式，按钮的高亮也要跟着灭 */
      note('导管接好了：' + item.label + '。反应出气时能看到气泡沿导管过去', '');
    }
    labRender();
    return true;
  }

  /* 「导管」不是摆件，是一段连接：点它进/出连接模式（器材上会浮出接口点）。 */
  function labConnectChip(on) {
    var chip = document.querySelector('.lab-chip[data-apparatus="pipe"]');
    if (chip) chip.classList.toggle('active', !!on);
  }

  function labToggleConnect() {
    LAB.connectMode = !LAB.connectMode;
    LAB.pending = null;
    labConnectChip(LAB.connectMode);
    note(LAB.connectMode
      ? '连接模式：点两个器材上的接口点，就接上一段导管（再点一次「导管」退出）' : '');
    labRender();
  }

  /* 检查装置：几何交给自己判、化学交给引擎，所以这里先把台面上的摆法报给服务端。 */
  function labCheck() {
    var order = {};
    var apparatus = [];
    LAB.items.forEach(function (item, index) {
      order[item.id] = index;
      var state = labState(item);
      apparatus.push({
        kind: item.kind, label: item.label, contents: labKeys(item),
        heated: !!state.heating, tilt: !!item.tilt
      });
    });
    var links = [];
    LAB.links.forEach(function (link) {
      if (order[link.a.item] === undefined || order[link.b.item] === undefined) return;
      links.push([order[link.a.item], order[link.b.item]]);
    });
    return post({ action: 'lab', op: 'check', apparatus: apparatus, links: links })
      .then(function (data) {
        var box = $('#chemLabResult');
        if (!box) return data;
        box.innerHTML = '';
        if (!LAB.items.length) {
          box.appendChild(line('chem-hint', '台面上还没有器材'));
          return data;
        }
        var problems = data.problems || [];
        if (!problems.length) {
          box.appendChild(line('chem-value', '装置没看出问题'));
        } else {
          box.appendChild(line('chem-warn', '装置有 ' + problems.length + ' 处要改：'));
          problems.forEach(function (problem) {
            box.appendChild(line('chem-hint', '· ' + problem.text));
          });
        }
        if ((data.tips || []).length) {
          box.appendChild(line('chem-hint', '这几条界面判不了，自己看一眼：' + data.tips.join('；')));
        }
        return data;
      });
  }

  function labAddReagent(itemId, reagentKey) {
    var item = labFind(itemId);
    if (!item) return Promise.reject(new Error('没找到这件器材'));
    /* 加进去就开始算——反应是不是发生、进行到哪个限度、多长时间，全由引擎给 */
    item.contents.push({ key: reagentKey, mol: 0 });
    return labSimulate(item);
  }

  /* 温度阶梯：常温 -> 酒精灯加热 -> 强热 -> 回到常温。
     温度是**条件**：它决定速率（条件不够时几乎不动）和平衡的位置。 */
  var TEMPERATURES = [298.15, 673.15, 1273.15];
  var SIM_SECONDS = 6;

  function labHeat(itemId) {
    var item = labFind(itemId);
    if (!item) return Promise.reject(new Error('没找到这件器材'));
    var state = labState(item);
    var index = 0;
    for (var i = 0; i < TEMPERATURES.length; i++) {
      if (Math.abs(TEMPERATURES[i] - state.temperature) < 1) index = i;
    }
    state.temperature = TEMPERATURES[(index + 1) % TEMPERATURES.length];
    state.heating = state.temperature > 300;
    note(state.heating
      ? '加热到约 ' + Math.round(state.temperature) + ' K：反应速率和平衡都可能跟着变'
      : '停止加热，回到常温');
    return labSimulate(item);
  }

  function labSimulate(item) {
    var state = labState(item);
    var contents = (item.contents || []).map(function (row) {
      return { key: row.key, mol: row.mol || 0 };
    });
    if (!contents.length) {
      item.sim = null;
      state.play = null;
      state.gas = false;
      labRender();
      return Promise.resolve(null);
    }
    /* 恒容密闭反应釜：气体不逸出，按 pV=nRT 算压强——这个标志要交给引擎，
       不然釜里的可逆反应会被当成"气体跑掉了"而算成完全反应。 */
    var shape = SHAPES[item.kind] || {};
    var vessel = (LAB.catalog && LAB.catalog.apparatus || []).filter(function (row) {
      return row.kind === item.kind;
    })[0] || {};
    return postRaw({ action: 'lab', op: 'sim', contents: contents,
                     temperature: state.temperature, seconds: SIM_SECONDS,
                     sealed: !!(shape.sealed || vessel.sealed),
                     pressure: state.pressure || null,
                     volume: shape.sealed ? (vessel.volume || 1.0) : 0.05 })
      .then(function (data) {
        if (!data || !data.ok) {
          labShowResult(data, item);
          return data;
        }
        labApplySim(item, data);
        return data;
      });
  }

  function labApplySim(item, data) {
    item.sim = {
      appearance: data.appearance || {},
      visual: data.visual || {},
      indicator: data.indicator || [],
      reactions: data.reactions || [],
      frozen: data.frozen || [],
      conditions: data.conditions || [],
      amounts: labMap(data.final),
      limit: data.limit_note || '',
      t95: data.t95 || 0,
      steps: data.steps || []
    };
    /* 组成以引擎为准（mol 数也是它算的），界面不自己记账 */
    item.contents = (data.initial || []).map(function (row) {
      return { key: row.key, mol: row.mol };
    });
    labPlaySim(item, data);
    labShowResult(data, item);
  }

  /* 按引擎给的轨迹播几个关键帧：颜色靠 CSS 过渡，固体/沉淀/气泡按组成重画。
     不做 60fps 重画（每帧 innerHTML 都会把 CSS 动画重启一遍，反而看着抖）。 */
  function labPlaySim(item, data) {
    var state = labState(item);
    if (state.timer) {
      clearTimeout(state.timer);
      state.timer = null;
    }
    var steps = data.steps || [];
    var produces = !!((data.visual || {}).gas || []).length;
    if (!steps.length || !data.t95) {
      state.play = null;
      state.gas = false;
      labFlow(item, false);
      labRender();
      return;
    }
    var stride = Math.max(1, Math.floor(steps.length / 6));
    var frames = [];
    for (var index = 0; index < steps.length; index += stride) frames.push(steps[index]);
    frames.push(steps[steps.length - 1]);
    var gap = Math.max(180, Math.round(SIM_SECONDS * 1000 / frames.length));
    var play = function (position) {
      if (position >= frames.length) {
        state.play = null;
        state.gas = false;
        labFlow(item, false);
        state.timer = null;
        labRender();
        return;
      }
      state.play = frames[position].amounts;
      state.gas = produces && position < frames.length - 1;   /* 反应进行中才冒气泡 */
      /* 已经放出多少气体（mol）：扣在水槽里的集气瓶按这个把水排出去 */
      var gasMol = 0;
      ((data.visual || {}).gas || []).forEach(function (key) {
        gasMol += (state.play[key] || 0) - ((data.initial || []).filter(function (row) {
          return row.key === key;
        })[0] || { mol: 0 }).mol;
      });
      labFlow(item, state.gas, Math.max(0, gasMol));
      labRender();
      state.timer = setTimeout(function () { play(position + 1); }, gap);
    };
    play(0);
  }

  function labShowResult(data, item) {
    var box = $('#chemLabResult');
    if (!box) return;
    box.innerHTML = '';
    if (!data || !data.ok) {
      box.appendChild(line('chem-warn', answerText(data) +
        (data && data.detail ? '：' + data.detail : '')));
      return;
    }
    if (data.empty) {
      box.appendChild(line('chem-hint', '这件器材里还没有东西'));
      return;
    }
    var live = (data.reactions || []).filter(function (row) { return row.met; });
    live.forEach(function (row) {
      box.appendChild(equationLine(row.equation));
      if (row.phenomenon) box.appendChild(line('chem-value', (applyFlameColor(row, item && item.id), applyReactionVisual(row), '现象：' + row.phenomenon + extraNote(row))));
      var extra = [];
      if (row.extent > 0 && row.limit_mol > 0) {
        extra.push('转化率约 ' + Math.round(100 * row.extent / row.limit_mol) + '%');
      }
      if (row.k !== null && row.k !== undefined) {
        extra.push('K≈' + (row.k >= 1e6 ? '很大' : (row.k <= 1e-6 ? '极小' : row.k.toPrecision(2))));
      }
      if (data.t95) extra.push('约 ' + data.t95 + ' 秒后基本完成');
      if (extra.length) box.appendChild(line('chem-hint', extra.join('；')));
    });
    if (!live.length) {
      box.appendChild(line('chem-hint', '加进去的药品之间没有发生反应'));
    }
    (data.frozen || []).forEach(function (row) {
      box.appendChild(line('chem-warn', row.equation + ' 暂时不会进行：' + row.missing));
    });
    if (data.limit_note) box.appendChild(line('chem-hint', '限度：' + data.limit_note));
    if (data.sealed && data.pressure) {
      box.appendChild(line('chem-value', '釜内压强约 ' + data.pressure.kPa + ' kPa（气体共 ' +
        data.pressure.mol + ' mol，' + data.pressure.volume + ' L）'));
    }
    if (data.conditions && data.conditions.length) {
      box.appendChild(line('chem-hint', '条件：' + data.conditions.join('、')));
    }
    var visual = data.visual || {};
    var seen = [];
    if ((visual.gas || []).length) seen.push('冒气泡（' + visual.gas.join('、') + '）');
    if (visual.precipitate) seen.push('生成沉淀 ' + visual.precipitate.formula);
    if (visual.solids && visual.solids.length) {
      seen.push('析出 ' + visual.solids.map(function (row) { return row.formula; }).join('、'));
    }
    if ((visual.layers || []).length) {
      seen.push('液体分层（' + visual.layers.map(function (row) {
        return (row.where === 'up' ? '上层 ' : '下层 ') + row.key;
      }).join('、') + '）');
    }
    if (visual.solution_before !== visual.solution_after) seen.push('溶液颜色变化');
    if ((data.indicator || []).length) {
      seen.push('指示剂变色（' + data.indicator[0].nature + '）');
    }
    if (seen.length) box.appendChild(line('chem-hint', '画面上：' + seen.join('；')));
  }

  function labLoadCatalog() {
    return post({ action: 'lab', op: 'catalog' }).then(function (data) {
      LAB.catalog = data;
      var bench = $('#chemLabShelf');
      if (!bench) return data;
      bench.innerHTML = '';
      (data.apparatus || []).forEach(function (item) {
        var shape = SHAPES[item.kind] || SHAPES.tube;
        var node = document.createElement('button');
        node.className = 'lab-chip';
        node.setAttribute('data-apparatus', item.kind);
        if (item.connector) {
          /* 「导管」不是摆件：点它是进/出连接模式，所以画一段弯管子当图标 */
          node.innerHTML = '<svg class="lab-thumb" viewBox="0 0 120 160"><path class="lab-wire" ' +
            'd="M30 132C30 60 90 60 90 132"/></svg><span>' + item.label + '</span>';
          node.onclick = function () { labToggleConnect(); };
          bench.appendChild(node);
          return;
        }
        node.innerHTML = '<svg class="lab-thumb" viewBox="0 0 ' + shape.w + ' ' +
          shape.h + '"><path class="lab-glass" d="' + shape.outline + '"/></svg><span>' +
          item.label + '</span>';
        node.onclick = function () { labPlace(item.kind, item.label); };
        node.addEventListener('mousedown', function (event) {
          labStartDrag(event, { kind: item.kind, label: item.label });
        });
        bench.appendChild(node);
      });
      var shelf = $('#chemLabReagents');
      if (shelf) {
        shelf.innerHTML = '';
        /* 五十多瓶药按货架分组（酸/碱/盐/氧化物/单质/有机物/指示剂…）：
           一长条平铺老师根本找不着。分组顺序按数据里第一次出现的先后走。 */
        var groups = [];
        (data.reagents || []).forEach(function (item) {
          var tag = item.tag || '其他';
          var group = null;
          groups.forEach(function (row) { if (row.tag === tag) group = row; });
          if (!group) {
            group = { tag: tag, items: [] };
            groups.push(group);
          }
          group.items.push(item);
        });
        groups.forEach(function (group) {
          var box = document.createElement('div');
          box.className = 'lab-group';
          var title = document.createElement('div');
          title.className = 'lab-group-title';
          title.textContent = group.tag;
          box.appendChild(title);
          var row = document.createElement('div');
          row.className = 'lab-shelf';
          group.items.forEach(function (item) {
            var node = document.createElement('button');
            node.className = 'lab-chip reagent' + (item.kind === 'solid' ? ' solid' : '');
            node.setAttribute('data-reagent', item.key);
            /* 药瓶上标本色：溶液按溶液颜色、固体按固体颜色（实验室里就是靠这个认的） */
            var dot = document.createElement('span');
            dot.className = 'lab-dot';
            dot.style.background = item.color || 'rgba(190,205,220,.55)';
            node.appendChild(dot);
            var text = document.createElement('span');
            text.textContent = item.label;
            node.appendChild(text);
            node.title = item.key + (item.kind === 'gas' ? '（气体，通入）' : '');
            node.addEventListener('mousedown', function (event) {
              labStartDrag(event, { reagent: item.key, label: item.label, kind: item.kind,
                                    color: item.color });
            });
            row.appendChild(node);
          });
          box.appendChild(row);
          shelf.appendChild(box);
        });
      }
      var presetBox = $('#chemLabPresets');
      if (presetBox) {
        presetBox.innerHTML = '';
        (data.presets || []).forEach(function (preset) {
          var node = document.createElement('button');
          node.className = 'btn lab-preset';
          node.textContent = preset.label;
          node.onclick = function () { labRunPreset(preset); };
          presetBox.appendChild(node);
        });
      }
      return data;
    });
  }

  function labRunPreset(preset) {
    LAB.items = [];
    var labelOf = {};
    (LAB.catalog.apparatus || []).forEach(function (item) { labelOf[item.kind] = item.label; });
    (preset.apparatus || []).forEach(function (kind, index) {
      labPlace(kind, labelOf[kind] || kind, 16 + index * 150, 24);
    });
    /* 预设里同时有水槽和集气瓶：按排水法把瓶子扣进水槽（老师要的就是这个摆法） */
    var trough = null;
    LAB.items.forEach(function (item) { if (item.kind === 'trough') trough = item; });
    if (trough) {
      LAB.items.forEach(function (item) {
        if (NESTABLE[item.kind] && item.kind === 'bottle') item.in = trough.id;
      });
      labRender();
    }
    var box = $('#chemLabResult');
    if (box) {
      box.innerHTML = '';
      box.appendChild(line('chem-value', preset.label));
      (preset.steps || []).forEach(function (step, index) {
        box.appendChild(line('chem-hint', (index + 1) + '. ' + step));
      });
    }
    /* 把试剂按顺序加进第一个"能装东西的"容器里，走同一条引擎 */
    var target = null;
    LAB.items.forEach(function (item) {
      if (!target && SHAPES[item.kind] && SHAPES[item.kind].liquid) target = item;
    });
    if (!target) return;
    var keys = preset.reagents || [];
    var chain = Promise.resolve();
    keys.forEach(function (key) {
      chain = chain.then(function () {
        return labAddReagent(target.id, key).catch(function () {});
      });
    });
  }

  /* 恒容密闭反应釜才有的那排控件：改温度、改压强（按该压强重新配料）。
     没摆釜就整排藏起来——平时用不到，别占地方。 */
  function labVesselBar() {
    var bar = $('#chemLabVessel');
    if (!bar) return;
    var kettle = null;
    LAB.items.forEach(function (item) {
      if (!kettle && (SHAPES[item.kind] || {}).sealed) kettle = item;
    });
    if (!kettle) {
      bar.style.display = 'none';
      return;
    }
    bar.style.display = '';
    var name = $('#chemLabVesselName');
    if (name) name.textContent = kettle.label;
    var temp = $('#chemVesselTemp');
    var press = $('#chemVesselPressure');
    if (temp && document.activeElement !== temp) {
      temp.value = Math.round(labState(kettle).temperature);
    }
    if (press && document.activeElement !== press) {
      press.value = labState(kettle).pressure || 100;
    }
  }

  function labVessel(target) {
    var found = null;
    LAB.items.forEach(function (item) {
      if (!found && (SHAPES[item.kind] || {}).sealed) found = item;
    });
    return found;
  }

  /* 改釜的温度/压强：温度走温度阶梯之外的精确值，压强交给引擎按 pV=nRT 重新配料 */
  function labSetVessel(temperature, pressure) {
    var kettle = labVessel();
    if (!kettle) return Promise.resolve(null);
    var state = labState(kettle);
    if (typeof temperature === 'number' && temperature > 0) {
      state.temperature = temperature;
      state.heating = temperature > 300;
    }
    if (typeof pressure === 'number' && pressure > 0) {
      state.pressure = pressure;
    }
    note('釜：' + Math.round(state.temperature) + ' K、' +
      (state.pressure ? Math.round(state.pressure) + ' kPa' : '不设压强'), '');
    return labSimulate(kettle);
  }

  /* 拖放：mousedown 抓起，mouseup 落在台面器材上 = 加试剂；落在台面空白 = 摆放；
     抓住台面上已有的器材拖 = 挪位置（导管跟着走）。
     只"点一下"不算拖：chip 自己的 onclick 负责放上去，不然点一次会摆两个。 */
  var DRAG = null;

  function labStartDrag(event, payload) {
    if (payload.move && event.target && event.target.tagName === 'BUTTON') return;
    DRAG = payload;
    DRAG.startX = event.clientX;
    DRAG.startY = event.clientY;
    DRAG.moved = false;
    /* 抓住器材的哪一点就端着哪一点，挪动时不会跳一下 */
    if (payload.move) {
      var moving = labFind(payload.move);
      var stage = labRoot();
      if (moving && stage) {
        var box = stage.getBoundingClientRect();
        DRAG.grabX = event.clientX - box.left - moving.x;
        DRAG.grabY = event.clientY - box.top - moving.y;
      }
      /* 被拿起来的那件器材先"虚"一点，看着就是被端走了 */
      if (event.currentTarget && event.currentTarget.classList) {
        DRAG.node = event.currentTarget;
        DRAG.node.classList.add('dragging');
      }
    }
    event.preventDefault();
    /* 药瓶是从上面的架子往下面的台面拖：台面要是滚出视野了，先把台面拉回来，
       不然鼠标在屏幕外松手（elementFromPoint 直接返回 null），试剂就掉了。 */
    var bench = labRoot();
    if (bench && bench.scrollIntoView) {
      try { bench.scrollIntoView({ block: 'nearest' }); } catch (error) { bench.scrollIntoView(); }
    }
    document.addEventListener('mousemove', labMoveGhost);
    document.addEventListener('mouseup', labEndDrag);
    document.addEventListener('touchmove', labMoveGhost);
    document.addEventListener('touchend', labEndDrag);
  }

  function labMoveGhost(event) {
    if (!DRAG) return;
    var point = event.touches && event.touches.length ? event.touches[0] : event;
    if (!DRAG.moved) {
      var dx = point.clientX - DRAG.startX, dy = point.clientY - DRAG.startY;
      if (dx * dx + dy * dy < 16) return;      /* 4px 以内还是"点一下" */
      DRAG.moved = true;
    }
    var ghost = $('#chemLabGhost');
    if (!ghost) {
      ghost = document.createElement('div');
      ghost.className = 'lab-ghost';
      ghost.id = 'chemLabGhost';
      ghost.innerHTML = labGhostHtml(DRAG);
      labGhostHost().appendChild(ghost);          /* 必须挂在打开的 dialog 里（顶层） */
    }
    ghost.style.left = (point.clientX + 12) + 'px';
    ghost.style.top = (point.clientY + 12) + 'px';
    labMarkTarget(labHolderAt(point.clientX, point.clientY));
  }

  /* 跟在鼠标上的那个东西：药瓶（带液面颜色）或器材的轮廓。
     以前只跟着一行字，看不出在拖什么——老师说"根本看不出来自己在拖动药品"。

    挂载点很关键：面板是 showModal() 打开的原生 <dialog>，它在**顶层（top layer）**里，
     挂到 document.body 上的东西会被对话框和 ::backdrop 盖住——看得见才怪（真踩过）。 */
  function labGhostHost() {
    return document.querySelector('dialog[open]') || document.body;
  }

  function labGhostHtml(drag) {
    var label = drag.label || drag.reagent || '';
    if (drag.reagent) {
      var color = drag.color || 'rgba(190,205,220,.6)';
      var solid = drag.kind === 'solid';
      return '<svg viewBox="0 0 34 44" width="30" height="39" aria-hidden="true">' +
        '<rect x="12" y="2" width="10" height="7" rx="2" fill="#8d939b"/>' +
        '<path d="M10 9h14v7l4 6v18a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2V22l4-6z" ' +
        'fill="rgba(255,255,255,.92)" stroke="#8fa6bb" stroke-width="1.6"/>' +
        (solid
          ? '<path d="M8 34h18v8a2 2 0 0 1-2 2H10a2 2 0 0 1-2-2z" fill="' + color + '"/>'
          : '<path d="M7 26h20v16a2 2 0 0 1-2 2H9a2 2 0 0 1-2-2z" fill="' + color + '"/>') +
        '</svg><span>' + label + '</span>';
    }
    var shape = SHAPES[drag.kind];
    if (shape) {
      /* 器材：直接把那个器皿的轮廓缩小画一份，拖的时候看得出拿的是什么 */
      return '<svg viewBox="0 0 ' + shape.w + ' ' + shape.h + '" width="30" height="40" ' +
        'aria-hidden="true"><path class="lab-glass" d="' + shape.outline + '"/>' +
        (shape.liquid ? '<rect x="' + shape.liquid.x + '" y="' +
          (shape.liquid.bottom - shape.liquid.h * 0.5) + '" width="' + shape.liquid.w +
          '" height="' + (shape.liquid.h * 0.5) + '" fill="rgba(150,205,235,.5)"/>' : '') +
        '</svg><span>' + label + '</span>';
    }
    return '<span>' + label + '</span>';
  }

  /* 悬停在哪件器材上：给它描个边，老师才知道松手会倒进哪儿 */
  function labHolderAt(x, y) {
    var node = document.elementFromPoint(x, y);
    while (node && node !== document.body &&
           !(node.getAttribute && node.getAttribute('data-lab-id'))) {
      node = node.parentNode;
    }
    return node && node.getAttribute ? node : null;
  }

  function labMarkTarget(node) {
    var previous = document.querySelector('.lab-item.drop-target');
    if (previous && previous !== node) previous.classList.remove('drop-target');
    if (node && node.classList && DRAG && DRAG.reagent) node.classList.add('drop-target');
  }

  function labEndDrag(event) {
    document.removeEventListener('mousemove', labMoveGhost);
    document.removeEventListener('mouseup', labEndDrag);
    document.removeEventListener('touchmove', labMoveGhost);
    document.removeEventListener('touchend', labEndDrag);
    var ghost = $('#chemLabGhost');
    if (ghost && ghost.parentNode) ghost.parentNode.removeChild(ghost);
    labMarkTarget(null);
    if (DRAG && DRAG.node && DRAG.node.classList) DRAG.node.classList.remove('dragging');
    if (!DRAG) return;
    var drag = DRAG;
    DRAG = null;
    if (!drag.moved) return;
    var point = event.changedTouches && event.changedTouches.length
      ? event.changedTouches[0] : event;
    var target = document.elementFromPoint(point.clientX, point.clientY);
    var holder = target;
    while (holder && holder !== document.body &&
           !(holder.getAttribute && holder.getAttribute('data-lab-id'))) {
      holder = holder.parentNode;
    }
    var bench = labRoot();
    if (drag.move) {
      var item = labFind(drag.move);
      if (item && bench) {
        var over = holder ? labFind(holder.getAttribute('data-lab-id')) : null;
        /* 拖到水槽上：扣进去（排水法收集）。拖到别处：从水槽里拿出来。 */
        if (over && over.id !== item.id && NESTABLE[item.kind] && over.kind === 'trough') {
          item.in = over.id;
          note(item.label + '已扣进' + over.label + '（排水法收集：气体进来会把水排出去）');
          labRender();
          return;
        }
        if (item.in) {
          item.in = null;
          note(item.label + '已从水槽里拿出来');
        }
        var stage = bench.getBoundingClientRect();
        item.x = Math.max(0, point.clientX - stage.left - (drag.grabX || 60));
        item.y = Math.max(0, Math.min(LAB_MAX_Y,
                                     point.clientY - stage.top - (drag.grabY || 88)));
        labRender();
      }
      return;
    }
    if (drag.reagent) {
      if (holder && holder.getAttribute('data-lab-id')) {
        labAddReagent(holder.getAttribute('data-lab-id'), drag.reagent).catch(showError);
      } else {
        note('把药瓶拖到某件器材上才是加试剂', 'warn');
      }
    } else if (holder && NESTABLE[drag.kind] &&
               (labFind(holder.getAttribute('data-lab-id')) || {}).kind === 'trough') {
      /* 从器材架直接把集气瓶/试管拖到水槽上：扣进去 */
      var trough = labFind(holder.getAttribute('data-lab-id'));
      var nested = labPlace(drag.kind, drag.label);
      nested.in = trough.id;
      note(drag.label + '已扣进' + trough.label + '（排水法收集：气体进来会把水排出去）');
      labRender();
    } else if (bench && bench.contains && bench.contains(target)) {
      var rect = bench.getBoundingClientRect();
      labPlace(drag.kind, drag.label,
               Math.max(0, point.clientX - rect.left - 86),
               Math.max(0, point.clientY - rect.top - 104));
    } else {
      labPlace(drag.kind, drag.label);
    }
  }

  /* ------------------------------------------------- 结构式 / 球棍模型（M5） */
  /* SMILES -> 引擎给 SVG（结构式）或原子坐标（球棍）；3D 的旋转在前端做，
     拖一下就能转，老师讲"甲烷是正四面体"时最直观。 */

  var STRUCT = { rotation: { x: -0.35, y: 0.6 }, model: null, dragging: false, jsme: false };

  function structNote(text, kind) {
    var box = $('#chemStructNote');
    if (!box) return;
    box.className = kind === 'warn' ? 'notice warn' : 'muted';
    box.textContent = text || '';
  }

  function structUrl() {
    var input = $('#chemSmiles');
    return input ? String(input.value || '').trim() : '';
  }

  /* 输入框里写的不是 SMILES 时（老师会直接写「乙醇」），服务端按名字查到 SMILES 再画。
     这里把查到的东西告诉他，并把输入框换成 SMILES —— 免得他以为"写名字也能当式子用"。 */
  function noteResolved(data) {
    if (!data || !data.resolved) return false;
    var input = $('#chemSmiles');
    if (input) input.value = data.resolved.smiles;
    structNote('「' + data.resolved.from + '」不是 SMILES，我按名称查到 ' +
      data.resolved.smiles + '（' + data.resolved.name + '）来画：分子式 ' + data.formula);
    return true;
  }

  function drawStructure2d() {
    var smiles = structUrl();
    if (!smiles) {
      structNote('先写一个 SMILES，或者点上面查出来的结构行', 'warn');
      return Promise.resolve(null);
    }
    return postRaw({ action: 'structure2d', smiles: smiles }).then(function (data) {
      var box = $('#chemStructBox');
      if (!box) return data;
      box.innerHTML = '';
      if (!data || !data.ok) {
        box.innerHTML = '';
        structNote((data && (data.error || data.reason)) || '画不出来', 'warn');
        return data;
      }
      box.innerHTML = data.svg || '';
      STRUCT.model = null;
      if (noteResolved(data)) return data;
      structNote('分子式 ' + data.formula + '（' + data.atoms + ' 个原子、' +
        data.bonds + ' 根键）' + ((data.groups || []).length
          ? '　官能团：' + data.groups.join('、') : ''));
      return data;
    });
  }

  function drawStructure3d() {
    var smiles = structUrl();
    if (!smiles) {
      structNote('先写一个 SMILES，或者点上面查出来的结构行', 'warn');
      return Promise.resolve(null);
    }
    return postRaw({ action: 'ball3d', smiles: smiles }).then(function (data) {
      var box = $('#chemStructBox');
      if (!box) return data;
      if (!data || !data.ok) {
        structNote((data && (data.error || data.reason)) || '画不出来', 'warn');
        return data;
      }
      STRUCT.model = data;
      STRUCT.zoom = null;                       /* 新分子：先自动适配（大分子一出来就看得全） */
      box.innerHTML = '<svg class="mol-svg" id="chemBallSvg" width="320" height="240" ' +
        'viewBox="0 0 320 240" xmlns="http://www.w3.org/2000/svg"></svg>';
      renderBall();
      if (!noteResolved(data)) {
        structNote('分子式 ' + data.formula + '　' + data.note +
          '（按住图拖一下可以转，滚轮缩放，双击回到合适大小）');
      }
      return data;
    });
  }

  /* 正交投影：球棍模型和反应动画共用（同一个旋转角，拖任意一个两个一起转） */
  function projectPoints(atoms, rotation, centerX, centerY, scale) {
    var cosY = Math.cos(rotation.y), sinY = Math.sin(rotation.y);
    var cosX = Math.cos(rotation.x), sinX = Math.sin(rotation.x);
    return (atoms || []).map(function (atom) {
      var x = atom.x || 0, y = atom.y || 0, z = atom.z || 0;
      var x1 = x * cosY + z * sinY;
      var z1 = -x * sinY + z * cosY;
      var y1 = y * cosX - z1 * sinX;
      var z2 = y * sinX + z1 * cosX;
      return { x: centerX + x1 * scale, y: centerY - y1 * scale, z: z2, atom: atom };
    });
  }

  /* 球棍模型：正交投影 + 按 z 排序（远的先画），球用径向渐变画成有立体感的球。
     zoom 为 null 表示"自动适配"（刚画出来时把整个分子装进画框——大分子才不会看不全）；
     老师滚过滚轮之后就固定成他调的那个倍数，双击回到自动。 */
  var BALL_CENTER = { x: 160, y: 120, margin: 12 };
  var BALL_ZOOM_MIN = 0.3, BALL_ZOOM_MAX = 4, BALL_SCALE = 42;

  function ballRadius(atom) {
    return 15 * ((atom && atom.radius) || 0.42) / 0.42;
  }

  function ballFitZoom(points) {
    var halfX = 0, halfY = 0;
    (points || []).forEach(function (point) {
      var radius = ballRadius(point.atom);
      halfX = Math.max(halfX, Math.abs(point.x - BALL_CENTER.x) + radius);
      halfY = Math.max(halfY, Math.abs(point.y - BALL_CENTER.y) + radius);
    });
    if (!halfX || !halfY) return 1;
    var fit = Math.min((BALL_CENTER.x - BALL_CENTER.margin) / halfX,
                       (BALL_CENTER.y - BALL_CENTER.margin) / halfY);
    return Math.max(BALL_ZOOM_MIN, Math.min(1, fit));   /* 只缩小不放大：小分子保持原来大小 */
  }

  function setBallZoom(value) {
    var zoom = Number(value);
    if (!isFinite(zoom) || zoom <= 0) return STRUCT.zoom;
    STRUCT.zoom = Math.max(BALL_ZOOM_MIN, Math.min(BALL_ZOOM_MAX, zoom));
    renderBall();
    return STRUCT.zoom;
  }

  function renderBall() {
    var svg = $('#chemBallSvg');
    var model = STRUCT.model;
    if (!svg || !model) return;
    var preview = projectPoints(model.atoms, STRUCT.rotation, BALL_CENTER.x, BALL_CENTER.y,
                                BALL_SCALE);
    if (!STRUCT.zoom) STRUCT.zoom = ballFitZoom(preview);
    var zoom = STRUCT.zoom;
    var points = zoom === 1 ? preview
      : projectPoints(model.atoms, STRUCT.rotation, BALL_CENTER.x, BALL_CENTER.y,
                      BALL_SCALE * zoom);
    var parts = [];
    model.bonds.forEach(function (bond) {
      var left = points[bond.a];
      var right = points[bond.b];
      parts.push({ z: Math.min(left.z, right.z) - 0.01, svg: '<line class="mol-stick" x1="' +
        left.x.toFixed(1) + '" y1="' + left.y.toFixed(1) + '" x2="' + right.x.toFixed(1) +
        '" y2="' + right.y.toFixed(1) + '"/>' });
      if (bond.order >= 2) {                       // 双键画两根棍
        var dx = right.x - left.x, dy = right.y - left.y;
        var length = Math.sqrt(dx * dx + dy * dy) || 1;
        var nx = -dy / length * 3 * zoom, ny = dx / length * 3 * zoom;
        parts.push({ z: Math.min(left.z, right.z) - 0.01,
                     svg: '<line class="mol-stick" x1="' + (left.x + nx).toFixed(1) + '" y1="' +
                       (left.y + ny).toFixed(1) + '" x2="' + (right.x + nx).toFixed(1) +
                       '" y2="' + (right.y + ny).toFixed(1) + '"/>' });
      }
    });
    points.forEach(function (point) {
      var radius = ballRadius(point.atom) * zoom;
      parts.push({ z: point.z, svg: '<circle class="mol-ball" cx="' + point.x.toFixed(1) +
        '" cy="' + point.y.toFixed(1) + '" r="' + radius.toFixed(1) + '" fill="' +
        point.atom.color + '"/>' });
    });
    parts.sort(function (left, right) { return left.z - right.z; });
    svg.innerHTML = parts.map(function (part) { return part.svg; }).join('');
  }

  function bindBallDrag() {
    var box = $('#chemStructBox');
    if (!box || box.getAttribute('data-ball-bound')) return;
    box.setAttribute('data-ball-bound', '1');
    box.addEventListener('mousedown', function (event) {
      if (!STRUCT.model) return;
      STRUCT.dragging = { x: event.clientX, y: event.clientY };
      event.preventDefault();
    });
    document.addEventListener('mousemove', function (event) {
      if (!STRUCT.dragging || !STRUCT.model) return;
      STRUCT.rotation.y += (event.clientX - STRUCT.dragging.x) * 0.012;
      STRUCT.rotation.x += (event.clientY - STRUCT.dragging.y) * 0.012;
      STRUCT.dragging = { x: event.clientX, y: event.clientY };
      renderBall();
      renderAnim();
    });
    document.addEventListener('mouseup', function () { STRUCT.dragging = false; });
    /* 滚轮缩放：不加 passive 选项，元素上的 wheel 监听本来就不是 passive，
       preventDefault 能生效（不然滚轮会连带把对话框滚走）。 */
    box.addEventListener('wheel', function (event) {
      if (!STRUCT.model) return;
      event.preventDefault();
      setBallZoom((STRUCT.zoom || 1) * (event.deltaY > 0 ? 0.88 : 1.14));
    }, false);
    box.addEventListener('dblclick', function () {
      if (!STRUCT.model) return;
      STRUCT.zoom = null;                     /* 双击 = 回到自动适配 */
      renderBall();
    });
  }

  /* 自己画（JSME）：懒加载第三方库，**读不到就说清楚**（老内核上没有这个能力，不能白屏） */
  function openJsme() {
    var box = $('#chemJsmeBox');
    if (!box) return;
    box.style.display = '';
    if (STRUCT.jsme) return;
    structNote('正在加载分子编辑器（JSME，约 2 MB，随包离线）…');
    var script = document.createElement('script');
    script.src = 'vendor/jsme/jsme.nocache.js';
    script.onerror = function () {
      structNote('这个浏览器打不开分子编辑器（JSME 要新一些的内核）。' +
        '用 Study7Agent 内置内核打开就能用；也可以直接在上面粘 SMILES。', 'warn');
    };
    script.onload = function () {
      var tries = 0;
      var wait = function () {
        tries++;
        if (window.JSApplet) {
          STRUCT.jsme = true;
          box.innerHTML = '<div id="jsmeHost" style="width:480px;height:320px"></div>' +
            '<div class="lab-bar"><button class="btn" id="chemJsmeTake">取回 SMILES 画结构式</button></div>';
          /* JSME 的方法要等 afterInit 回调里才能用（直接调会 not a function） */
          var editor = new window.JSApplet.JSME('jsmeHost', '480px', '320px', {
            afterInit: function (instance) {
              STRUCT.editor = instance;
              try { instance.setSmiles('CCO'); } catch (error) { /* 初始示例画不出来不影响使用 */ }
            }
          });
          STRUCT.editor = STRUCT.editor || editor;
          var take = $('#chemJsmeTake');
          if (take) {
            take.onclick = function () {
              if (!STRUCT.editor || !STRUCT.editor.getSmiles) {
                structNote('编辑器还没准备好，稍等一下再点', 'warn');
                return;
              }
              var smiles = STRUCT.editor.getSmiles();
              var input = $('#chemSmiles');
              if (input) input.value = smiles;
              drawStructure2d();
            };
          }
          structNote('在下面画分子，画完点「取回 SMILES 画结构式」');
          return;
        }
        if (tries > 60) {
          structNote('分子编辑器没能启动（等了 12 秒）。可以直接粘 SMILES 画结构式。', 'warn');
          return;
        }
        setTimeout(wait, 200);
      };
      wait();
    };
    document.head.appendChild(script);
  }

  /* 反应动画：反应物一侧（要断的键闪红）-> 产物一侧（新成的键长出来）。
     分子坐标是引擎给的，界面只负责按时间轴播——所以"哪根键断了"不是前端猜的。 */
  function loadMechanisms() {
    var pick = $('#chemAnimPick');
    if (!pick) return Promise.resolve(null);
    return postRaw({ action: 'mechanisms' }).then(function (data) {
      pick.innerHTML = '';
      ((data && data.list) || []).forEach(function (row) {
        var option = document.createElement('option');
        option.value = row.key;
        option.textContent = row.label;
        pick.appendChild(option);
      });
      return data;
    });
  }

  function playMechanism() {
    var pick = $('#chemAnimPick');
    var box = $('#chemAnimBox');
    var key = pick ? pick.value : '';
    if (!box || !key) return Promise.resolve(null);
    return postRaw({ action: 'mechanism', key: key }).then(function (data) {
      if (!data || !data.ok) {
        box.innerHTML = '';
        structNote((data && data.error) || '这条动画放不了', 'warn');
        return data;
      }
      var note = $('#chemAnimNote');
      if (note) note.textContent = data.note || '';
      STRUCT.anim = data;
      STRUCT.animSide = 'reactants';
      /* 画布宽度按"更宽的那一侧"算：引擎给的原子坐标已经缩放到 ~90 的方框里了，
         前端**不能再缩**（之前又乘了 26，原子全跑到画布外面去了）。 */
      var widest = 0;
      [data.reactants, data.products].forEach(function (panels) {
        var total = 20;
        (panels || []).forEach(function (panel) { total += panel.width + 26; });
        if (total > widest) widest = total;
      });
      var boxWidth = Math.max(320, Math.min(920, Math.round(widest)));
      var boxHeight = 240;
      box.innerHTML = '<svg class="mol-svg" id="chemAnimSvg" width="' + boxWidth +
        '" height="' + boxHeight + '" viewBox="0 0 ' + boxWidth + ' ' + boxHeight +
        '" xmlns="http://www.w3.org/2000/svg"></svg>';
      bindAnimDrag();
      renderAnim();
      /* 1.4 秒后切到产物一侧（新键绿色长出来） */
      if (STRUCT.animTimer) clearTimeout(STRUCT.animTimer);
      STRUCT.animTimer = setTimeout(function () {
        STRUCT.animSide = 'products';
        renderAnim();
      }, 1400);
      return data;
    });
  }

  /* 动画当前这一侧：用和球棍模型同一套 3D 投影画，所以也能拖着转 */
  function renderAnim() {
    var svg = $('#chemAnimSvg');
    var data = STRUCT.anim;
    if (!svg || !data) return;
    var panels = (STRUCT.animSide === 'products' ? data.products : data.reactants) || [];
    var parts = [];
    var cursor = 20;
    panels.forEach(function (panel) {
      var points = projectPoints(panel.atoms, STRUCT.rotation, cursor + panel.width / 2,
                                 110, 1.0);
      var bondParts = [];
      panel.bonds.forEach(function (bond) {
        var left = points[bond.a];
        var right = points[bond.b];
        if (!left || !right) return;
        var cls = 'mol-stick' + (bond.marked
          ? (STRUCT.animSide === 'products' ? ' anim-form' : ' anim-break') : '');
        var color = bond.marked
          ? (STRUCT.animSide === 'products' ? data.form_color : data.break_color) : '#7b8794';
        var line = function (x1, y1, x2, y2) {
          return { z: Math.min(left.z, right.z) - 0.01,
                   svg: '<line class="' + cls + '" x1="' + x1.toFixed(1) + '" y1="' +
                     y1.toFixed(1) + '" x2="' + x2.toFixed(1) + '" y2="' + y2.toFixed(1) +
                     '" style="stroke:' + color + '"/>' };
        };
        bondParts.push(line(left.x, left.y, right.x, right.y));
        if (bond.order >= 2) {
          var dx = right.x - left.x, dy = right.y - left.y;
          var length = Math.sqrt(dx * dx + dy * dy) || 1;
          var nx = -dy / length * 2.6, ny = dx / length * 2.6;
          bondParts.push(line(left.x + nx, left.y + ny, right.x + nx, right.y + ny));
        }
      });
      Array.prototype.push.apply(parts, bondParts);
      points.forEach(function (point) {
        var radius = 13 * point.atom.r / 12;
        parts.push({ z: point.z,
                     svg: '<circle class="mol-ball" cx="' + point.x.toFixed(1) + '" cy="' +
                       point.y.toFixed(1) + '" r="' + radius.toFixed(1) + '" fill="' +
                       point.atom.color + '"/>' });
      });
      parts.push({ z: 99, svg: '<text class="mol-atom" x="' + (cursor + panel.width / 2) +
        '" y="' + 214 + '" text-anchor="middle" font-size="11" fill="#5b6673">' +
        panel.formula + '</text>' });
      cursor += panel.width + 26;
      if (panels.indexOf(panel) < panels.length - 1) {
        parts.push({ z: 99, svg: '<text class="mol-atom" x="' + (cursor - 18) +
          '" y="110" font-size="16" fill="#3a4652">+</text>' });
      }
    });
    parts.sort(function (left, right) { return left.z - right.z; });
    svg.innerHTML = parts.map(function (part) { return part.svg; }).join('');
  }

  function bindAnimDrag() {
    var svg = $('#chemAnimSvg');
    if (!svg || svg.getAttribute('data-anim-bound')) return;
    svg.setAttribute('data-anim-bound', '1');
    svg.addEventListener('mousedown', function (event) {
      STRUCT.animDragging = { x: event.clientX, y: event.clientY };
      event.preventDefault();
    });
    document.addEventListener('mousemove', function (event) {
      if (!STRUCT.animDragging) return;
      STRUCT.rotation.y += (event.clientX - STRUCT.animDragging.x) * 0.012;
      STRUCT.rotation.x += (event.clientY - STRUCT.animDragging.y) * 0.012;
      STRUCT.animDragging = { x: event.clientX, y: event.clientY };
      renderAnim();
      renderBall();
    });
    document.addEventListener('mouseup', function () { STRUCT.animDragging = false; });
  }

  /* ------------------------------------------------------------ 面板 */

  function setTab(tab) {
    var tabs = document.querySelectorAll('#chemTabs .chip');
    Array.prototype.forEach.call(tabs, function (chip) {
      var active = chip.getAttribute('data-chem-tab') === tab;
      chip.classList.toggle('active', active);
    });
    ['balance', 'react', 'query', 'lab'].forEach(function (name) {
      var pane = $('#chemPane' + name.charAt(0).toUpperCase() + name.slice(1));
      if (pane) pane.style.display = (name === tab) ? '' : 'none';
    });
    clearBox();
    if (tab === 'lab') {
      if (!LAB.catalog) labLoadCatalog().catch(showError);
      else labRender();
    }
  }

  function open(tab) {
    var dialog = $('#chemDialog');
    if (!dialog) return;
    setTab(tab || 'balance');
    if (!dialog.open) dialog.showModal();
    setTimeout(function () {
      var target = tab === 'react' ? $('#chemA')
        : (tab === 'query' ? $('#chemQuery')
          : (tab === 'lab' ? null : $('#chemEquation')));
      if (target) target.focus();
    }, 30);
  }

  function bind() {
    var tabs = document.querySelectorAll('#chemTabs .chip');
    Array.prototype.forEach.call(tabs, function (chip) {
      chip.onclick = function () { setTab(chip.getAttribute('data-chem-tab')); };
    });
    function on(id, handler) {
      var node = $(id);
      if (node) node.onclick = handler;
    }
    on('#chemBalanceBtn', function () { balance(false); });
    on('#chemThermoBtn', function () { balance(true); });
    on('#chemReactBtn', predict);
    on('#chemReplaceBtn', replace);
    on('#chemLabCheckBtn', function () { labCheck().catch(showError); });
    on('#chemLabClearBtn', labClear);
    on('#chemDraw2d', function () { drawStructure2d().catch(showError); });
    on('#chemDraw3d', function () { drawStructure3d().catch(showError); });
    on('#chemDrawJsme', function () { openJsme(); });
    on('#chemAnimPlay', function () { playMechanism().catch(showError); });
    loadMechanisms().catch(function () { /* 清单拿不到就不显示动画，不影响别的功能 */ });
    bindBallDrag();
    on('#chemVesselApply', function () {
      var temp = $('#chemVesselTemp');
      var press = $('#chemVesselPressure');
      labSetVessel(temp ? parseFloat(temp.value) : null,
                   press ? parseFloat(press.value) : null).catch(showError);
    });
    [['#chemVesselPreset1', 298.15, 100], ['#chemVesselPreset2', 773.15, 10000],
     ['#chemVesselPreset3', 773.15, 30000], ['#chemVesselCool', 298.15, 100]]
      .forEach(function (row) {
        on(row[0], function () { labSetVessel(row[1], row[2]).catch(showError); });
      });
    on('#chemSpeciesBtn', querySpecies);
    on('#chemStructureBtn', queryStructures);
    ['#chemEquation', '#chemA', '#chemB', '#chemMetal', '#chemOther', '#chemQuery']
      .forEach(function (selector) {
        var node = $(selector);
        if (!node) return;
        node.addEventListener('keydown', function (event) {
          if (event.key === 'Enter') {
            event.preventDefault();
            if (selector === '#chemEquation') balance(false);
            else if (selector === '#chemQuery') querySpecies();
            else predict();
          }
        });
      });
    var button = $('#btnChem');
    if (button) button.onclick = function () { open('balance'); };
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bind);
  } else {
    bind();
  }

  /* 实验台的操作函数也导出：自检（tmp-chem-check.js）不用合成拖放事件就能驱动，
     拖动只是这些函数上面很薄的一层。 */
  window.W7Chem = {
    open: open,
    balance: balance,
    thermo: thermo,
    lab: {
      load: labLoadCatalog,
      place: labPlace,
      addReagent: labAddReagent,
      heat: labHeat,
      runPreset: labRunPreset,
      connect: labToggleConnect,
      link: function (aId, aPort, bId, bPort) {
        LAB.links.push({ a: { item: aId, port: aPort || 0 }, b: { item: bId, port: bPort || 0 } });
        labRender();
      },
      check: labCheck,
      simulate: labSimulate,
      vessel: labSetVessel,
      remove: labRemove,
      clear: labClear,
      keys: function (item) { return labKeys(item); },
      look: function (item) { return labLook(item); },
      items: function () { return LAB.items; },
      links: function () { return LAB.links; }
    },
    /* 结构式/球棍模型是**面板级**的功能，不属于实验台，导出在顶层 */
    draw2d: drawStructure2d,
    draw3d: drawStructure3d,
    playAnim: playMechanism,
    jsem: openJsme,
    rotation: function () { return STRUCT.rotation; },
    /* 球棍模型的缩放：自检/面板都能驱动（值给 null 就回到自动适配） */
    zoom: function (value) {
      if (value === null || value === undefined) {
        STRUCT.zoom = null;
        renderBall();
        return STRUCT.zoom;
      }
      return setBallZoom(value);
    }
  };
})();
