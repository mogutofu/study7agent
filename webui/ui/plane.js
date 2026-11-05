/*! plane.js —— 「平面」渲染与联动层（SVG 基线，原生 JS，无构建步骤）
 *
 * 职责：
 *   1. 把服务端返回的 PlaneState 画成 SVG（点 / 线 / 圆 / 弧 / 多边形 / 曲线 /
 *      不等式区域 / 滑动条 / 文字 / 轨迹 / 「交点」）；
 *   2. 选中与悬停：点一下图上的对象，右栏代数区和下面信息框一起高亮；
 *   3. 拖动联动：拖一个自由点时 POST /api/plane/apply（一条 update 运算），
 *      然后用「服务端返回的」状态整体重画，于是中点、圆、「交点」跟着动；
 *   4. 代数区：每个对象一行（标签 / 定义 / 当前值 / 显隐 / 删除）。
 *
 * 数学一律在后端算：这里只画后端已经采样好的点，前端不做任何求值——
 * 前端求值等于实现两份数学，迟早会对不上。
 *
 * 坐标：SVG 的 viewBox 直接用数据坐标（y 取 -ymax、宽高是数据跨度），
 * 画的时候用 (x, -y)。好处是缩放平移只改 viewBox 一个属性，不用重建 DOM；
 * 代价是半径/字号/偏移都要按"每数据单位多少像素"折算（U() 就是干这个的）。
 *
 * 兼容：Chrome 109 / 360 极速内核能直接跑。不用可选链、空值合并、幂运算符、类字段、协程关键字
 * 顶层 import —— 全文件 ES5 语法。
 */
(function (global) {
  'use strict';

  var NS = 'http://www.w3.org/2000/svg';
  /* 访问令牌：app.js 启动时会把 ?token= 记进 localStorage，这里照着取。
     局域网模式下不带它就是一片 401，老师那边只会看到"读不到平面状态"。 */
  var TOKEN = (function () {
    try { return global.localStorage.getItem('w7a.token') || ''; } catch (error) { return ''; }
  })();

  var $ = function (sel) { return document.querySelector(sel); };

  /* 类型名（代数区和信息框里显示中文，画布上只画标签） */
  var TYPE_CN = {
    point: '点', intersect: '交点', segment: '线段', ray: '射线', line: '直线',
    vector: '向量', perpendicular: '垂线', parallel: '平行线',
    perpendicular_bisector: '中垂线', angle_bisector: '角平分线', tangent: '切线',
    centroid: '重心', incenter: '内心', orthocenter: '垂心',
    circle: '圆', arc: '弧', sector: '扇形', polygon: '多边形',
    regular_polygon: '正多边形', angle: '角', distance: '距离', length: '长度',
    area: '面积', slope: '斜率', radius: '半径', function: '函数', implicit: '隐式曲线',
    parametric: '参数曲线', polar: '极坐标曲线', inequality: '不等式区域',
    conic: '圆锥曲线', slider: '滑动条', number: '数值', text: '文字',
    boolean: '布尔值', locus: '轨迹', trace: '追踪', measure: '度量', curve: '曲线'
  };
  /* 只有数值、没有图形的类型：它们只出现在代数区，画布上跳过不算"不认识" */
  var VALUE_ONLY = {
    number: 1, boolean: 1, measure: 1, distance: 1, length: 1, area: 1, slope: 1, radius: 1
  };
  var LINE_TYPES = { perpendicular: 1, parallel: 1, perpendicular_bisector: 1,
                     angle_bisector: 1, tangent: 1 };
  /* 无界直线（求交、求垂线方向这类"只认方向"的地方用；线段 / 射线有边界，不能算进去） */
  var UNBOUNDED_LINES = { line: 1, perpendicular: 1, parallel: 1, perpendicular_bisector: 1,
                          angle_bisector: 1, tangent: 1 };
  /* 能当"一条线"用的对象：线段/直线/射线/向量 + 上面这些构造出来的线（垂线/切线…） */
  function isLineLike(obj) {
    if (!obj) return false;
    var type = obj.type;
    return type === 'segment' || type === 'line' || type === 'ray' ||
           type === 'vector' || !!LINE_TYPES[type];
  }
  var CURVE_TYPES = { function: 1, implicit: 1, parametric: 1, polar: 1, curve: 1 };

  /* 导出成独立 SVG/PNG 时内联的样式。
     为什么另抄一份：独立文件里没有 :root 的 var()，也没加载 plane.css，
     抄一份写死颜色最省事也最不容易出错（打印/贴课件用浅色底最合适）。 */
  var EXPORT_CSS = [
    '.plane-grid{stroke:#dde4f5;stroke-width:1}',
    '.plane-axis{stroke:#5f6b86;stroke-width:1.4}',
    '.plane-tick{fill:#5f6b86;font-size:13px;font-family:Consolas,monospace}',
    '.plane-label{fill:#1b2030;font-size:14px;font-family:Consolas,monospace;',
    'paint-order:stroke;stroke:#ffffff;stroke-width:3px;stroke-linejoin:round}',
    '.plane-curve{fill:none;stroke:#4d6bfe;stroke-width:2.2;stroke-linejoin:round}',
    '.plane-shape{fill:none;stroke:#4d6bfe;stroke-width:2.2;stroke-linejoin:round}',
    '.plane-poly{fill:rgba(77,107,254,.14);stroke:#4d6bfe;stroke-width:2.2}',
    '.plane-region{fill:rgba(77,107,254,.16);stroke:none}',
    '.plane-point{fill:#4d6bfe;stroke:#ffffff;stroke-width:1.5}',
    '.plane-point.on{fill:#4d6bfe;stroke:#ffffff;stroke-width:2.5}',
    '.plane-isect{fill:#ffffff;stroke:#3550f0;stroke-width:2.4}',
    '.plane-isect.on{fill:#ffffff;stroke:#3550f0;stroke-width:3.6}',
    '.plane-trace{fill:none;stroke:#d98613;stroke-width:2;stroke-dasharray:5 4}',
    '.plane-slider-bar{stroke:#dde4f5;stroke-width:5;stroke-linecap:round}',
    '.plane-slider-knob{fill:#4d6bfe;stroke:#ffffff;stroke-width:1.5}',
    '.plane-text{fill:#1b2030;font-size:15px}',
    '.plane-mark{fill:none;stroke:#3550f0;stroke-width:1.8;stroke-linejoin:round}',
    '.plane-hit{display:none}'
  ].join('');

  var state = {
    plane: null,          // 服务端给的 PlaneState（原样保存，不改造）
    view: null,           // 客户端视野 {xmin,ymin,xmax,ymax,grid,axes}（缩放平移只改它）
    syncedView: null,     // 上一次报给服务端的"可见范围"（自动区间的曲线按它采样）
    viewSyncTimer: null,  // 视野同步的防抖计时器
    svg: null,
    layers: null,
    unit: 1 / 30,         // 一个屏幕像素等于多少数据单位（渲染时按上屏尺寸重算）
    selected: null,
    warnings: [],
    rendered: 0,
    renderedBox: null,
    rows: {},
    opened: false,
    serverReady: false,
    serverError: '',
    lastNote: '',
    drag: null,
    pan: null,
    pendingOp: null,
    slide: null,          // 沿线拖：{host: 轨道 id, drag: 正在滑的那条线段}
    inFlight: false,
    pendingLocus: [],
    sliderSlot: 0,
    slowTimer: null,
    hardTimer: null,
    stream: null,
    streaming: false,
    refreshTimer: null,
    statusTimer: null,
    lastDiagnostics: null,
    pendingRelayout: false    // ResizeObserver 折到下一帧的那次重排（见 boot 里的 frame()）
  };

  /* ------------------------------------------------------------ 小工具 */

  function svgNode(name, attrs) {
    var node = document.createElementNS(NS, name);
    if (attrs) {
      for (var key in attrs) {
        if (Object.prototype.hasOwnProperty.call(attrs, key) && attrs[key] !== null
            && attrs[key] !== undefined) {
          node.setAttribute(key, attrs[key]);
        }
      }
    }
    return node;
  }

  function node(tag, className, text) {
    var element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined && text !== null) element.textContent = text;
    return element;
  }

  /* ------------------------------------------------- 能力探测（老内核兜底）

     **整块画板的交互只有一份**：这一段探测失败就必须真的换另一条路，绝不能
     静默什么都不做 —— 老师报的"y=x^2 建好了显示不出动点、也拖不了"就是这个：
     面板上所有监听器都挂在 Pointer Events 上，而 360 / 搜狗这类双核浏览器在
     Win7 上用的是很老的 Blink（Chromium 55 之前没有 PointerEvent），
     `canvas.addEventListener('pointerdown', …)` 注册得上、**永远不会触发** ——
     不报错、不提示，点、拖、放动点、平移缩放全部没反应。
     所以这里探一次，缺了就走 mouse 事件（老内核一定有）。 */
  function inputCapability() {
    var pointer = typeof global.PointerEvent === 'function';
    return {
      mode: pointer ? 'pointer' : 'mouse',
      down: pointer ? 'pointerdown' : 'mousedown',
      move: pointer ? 'pointermove' : 'mousemove',
      up: pointer ? 'pointerup' : 'mouseup',
      cancel: pointer ? 'pointercancel' : 'mouseleave',
      lost: pointer ? 'lostpointercapture' : 'mouseleave',
      why: pointer ? '内核支持 Pointer Events' : '内核没有 PointerEvent，改用鼠标事件'
    };
  }

  /* 监听器的注册要能容错：老内核（或者被 polyfill 动过的 Object）上，
     `addEventListener(type, fn, {passive:false})` 这种"选项对象"形式的第三个参数
     可能直接抛 TypeError。**抛在 boot() 里就整块死掉**：后面的 pointerdown / wheel
     全都没注册上。所以失败就退回布尔形式再试一次。 */
  function listen(target, type, handler, options) {
    if (!target || !target.addEventListener) return false;
    try {
      target.addEventListener(type, handler, options);
      return true;
    } catch (error) {
      try {
        target.addEventListener(type, handler, !!options);
        return true;
      } catch (again) {
        return false;
      }
    }
  }

  /* requestAnimationFrame 要**探测再用**：app.js / sandbox.js 都是这么写的，
     这块面板原来没写。缺了它（或者被嵌壳换成空壳）时回调永不执行 ——
     面板尺寸变化后的重排就再也不会发生。 */
  function frame(callback) {
    if (typeof global.requestAnimationFrame === 'function') {
      global.requestAnimationFrame(callback);
      return;
    }
    setTimeout(callback, 16);
  }

  var INPUT = inputCapability();

  function clear(element) {
    while (element && element.firstChild) element.removeChild(element.firstChild);
  }

  function num(value, fallback) {
    var parsed = Number(value);
    return isFinite(parsed) ? parsed : fallback;
  }

  function clamp(value, low, high) {
    return value < low ? low : (value > high ? high : value);
  }

  function round4(value) {
    return Math.round(num(value, 0) * 10000) / 10000;
  }

  function fmt(value) {
    var parsed = num(value, null);
    if (parsed === null) return String(value);
    var text = String(Math.round(parsed * 10000) / 10000);
    return text === '-0' ? '0' : text;
  }

  function Y(y) { return -num(y, 0); }

  /* 一个屏幕像素折算成数据单位（点半径、字号、偏移都要用） */
  function U(px) { return px * state.unit; }

  /* 字号必须写成「内联样式」，不能用 font-size 属性。
     原因：样式表里的 .plane-label{font-size:14px} 优先级高于 SVG 呈现属性，
     属性会被无声地盖掉——于是"14"被当成 14 个「数据单位」，一屏巨字（真踩过，
     而且只查元素个数查不出来）。内联样式优先级最高，才压得住。 */
  function setFont(node, px) {
    node.style.fontSize = U(px) + 'px';
    return node;
  }

  function scaleOf() {
    var view = state.view;
    var boxW = view ? view.xmax - view.xmin : 0;
    var boxH = view ? view.ymax - view.ymin : 0;
    if (state.svg && boxW > 0 && boxH > 0) {
      var rect = state.svg.getBoundingClientRect();
      if (rect && rect.width > 10 && rect.height > 10) {
        var scale = Math.min(rect.width / boxW, rect.height / boxH);
        if (scale > 0) return scale;
      }
    }
    // 还没上屏（面板刚打开、或者被折叠）：按 600px 宽估一个，别让字号算成 0
    return boxW > 0 ? 600 / boxW : 30;
  }

  function orderedObjects() {
    var plane = state.plane;
    var list = [];
    if (!plane || !plane.objects) return list;
    var order = plane.order;
    var index;
    if (order && order.length) {
      for (index = 0; index < order.length; index++) {
        if (plane.objects[order[index]]) list.push(plane.objects[order[index]]);
      }
    }
    var seen = {};
    for (index = 0; index < list.length; index++) seen[list[index].id] = 1;
    for (var id in plane.objects) {
      if (Object.prototype.hasOwnProperty.call(plane.objects, id) && !seen[id]) {
        list.push(plane.objects[id]);
      }
    }
    return list;
  }

  function objById(id) {
    if (!id || !state.plane || !state.plane.objects) return null;
    return state.plane.objects[id] || null;
  }

  function isHidden(obj) {
    return !!(obj && obj.style && obj.style.visible === false);
  }

  function isIntersection(obj) {
    if (!obj) return false;
    if (obj.type === 'intersect') return true;
    var def = obj.definition || {};
    return !!(def.intersect || def.intersections);
  }

  function pointOf(obj) {
    if (!obj) return null;
    var value = obj.value;
    if (value) {
      if (typeof value.x === 'number' && typeof value.y === 'number') {
        return { x: value.x, y: value.y };
      }
      if (typeof value[0] === 'number' && typeof value[1] === 'number') {
        return { x: value[0], y: value[1] };
      }
      if (value.point) return pointOf({ value: value.point });
    }
    var def = obj.definition || {};
    if (typeof def.x === 'number' && typeof def.y === 'number') {
      return { x: def.x, y: def.y };
    }
    return null;
  }

  function normalizePointList(raw) {
    var out = [];
    if (!raw || !raw.length) return out;
    if (typeof raw[0] === 'number') {
      for (var index = 0; index + 1 < raw.length; index += 2) {
        out.push({ x: num(raw[index], 0), y: num(raw[index + 1], 0) });
      }
      return out;
    }
    for (var i = 0; i < raw.length; i++) {
      var item = raw[i];
      if (!item) continue;
      if (typeof item.x === 'number' && typeof item.y === 'number') {
        out.push({ x: item.x, y: item.y });
      } else if (typeof item[0] === 'number' && typeof item[1] === 'number') {
        out.push({ x: item[0], y: item[1] });
      }
    }
    return out;
  }

  /* 后端采样好的曲线：单条 points / 多条 segments，两种写法都吃 */
  function sampledSegments(obj) {
    var value = (obj && obj.value) || {};
    var out = [];
    var groups = value.segments || value.branches || value.curves;
    var index;
    if (groups && groups.length) {
      for (index = 0; index < groups.length; index++) {
        var list = normalizePointList(groups[index]);
        if (list.length >= 2) out.push(list);
      }
    }
    if (!out.length) {
      var single = normalizePointList(value.points || value.samples || value.polyline);
      if (single.length >= 2) out.push(single);
    }
    return out;
  }

  /* 哪几种曲线上的动点按"弧长占比"取参：方程曲线 / 参数曲线 / 极坐标曲线。
     它们**没有**像 y=f(x) 那样的天然参数，而且采样框跟着视野走 —— 用弧长占比
     才和视野无关，服务端算出来的点才落在老师看见的那条线上。 */
  var ARC_CURVES = { implicit: 1, parametric: 1, polar: 1 };

  /* 折线 + 累计弧长。给"参数 = 弧长占比"用的，和服务端 _nearest_on_segments /
     _segments_at 一一对应（两边口径必须一致，否则拖起来会跳）。 */
  function arcPath(segments) {
    var out = [];
    for (var index = 0; index < segments.length; index++) {
      var line = segments[index];
      var lengths = [0];
      var total = 0;
      for (var position = 0; position + 1 < line.length; position++) {
        total += Math.sqrt(Math.pow(line[position + 1].x - line[position].x, 2) +
                           Math.pow(line[position + 1].y - line[position].y, 2));
        lengths.push(total);
      }
      if (total > 0) out.push({ line: line, lengths: lengths, total: total });
    }
    return out;
  }

  /* 弧长占比 u 处的坐标。**只在自己那一支里走**：隐式曲线可能是好几支
     （例如 x*y=1 的两条），跨支插值会算出一个两段之间根本不存在的点。 */
  function arcPointAt(path, u) {
    if (!path.length) return null;
    var branch = path[0];
    var line = branch.line;
    var want = u * branch.total;
    for (var index = 0; index + 1 < line.length; index++) {
      if (want <= branch.lengths[index + 1] || index === line.length - 2) {
        var span = branch.lengths[index + 1] - branch.lengths[index];
        var place = span <= 0 ? 0 : (want - branch.lengths[index]) / span;
        place = clamp(place, 0, 1);
        return { x: line[index].x + (line[index + 1].x - line[index].x) * place,
                 y: line[index].y + (line[index + 1].y - line[index].y) * place };
      }
    }
    return { x: line[line.length - 1].x, y: line[line.length - 1].y };
  }

  /* 鼠标位置在这条曲线上的**就近点**：投影到最近的那一段采样折线上
     （不是最近的网格点 / 采样点 —— 那样会偏半格），并换算成弧长占比。
     多支一起比，取距离最近的那一支：这就是老师说的"就近动点"。 */
  function arcNearest(path, point) {
    var best = null;
    var bestGap = Infinity;
    for (var branch = 0; branch < path.length; branch++) {
      var line = path[branch].line;
      var lengths = path[branch].lengths;
      var total = path[branch].total;
      for (var index = 0; index + 1 < line.length; index++) {
        var ax = line[index].x;
        var ay = line[index].y;
        var dx = line[index + 1].x - ax;
        var dy = line[index + 1].y - ay;
        var span = dx * dx + dy * dy;
        var ratio = span <= 0 ? 0 : ((point.x - ax) * dx + (point.y - ay) * dy) / span;
        ratio = clamp(ratio, 0, 1);
        var px = ax + dx * ratio;
        var py = ay + dy * ratio;
        var gap = Math.sqrt(Math.pow(point.x - px, 2) + Math.pow(point.y - py, 2));
        if (gap < bestGap) {
          bestGap = gap;
          var along = lengths[index] + Math.sqrt(Math.pow(px - ax, 2) + Math.pow(py - ay, 2));
          best = { x: px, y: py, gap: gap, parameter: total > 0 ? along / total : 0 };
        }
      }
    }
    return best;
  }

  function endpointsOf(obj) {
    if (!obj) return [null, null];
    var value = obj.value || {};
    var def = obj.definition || {};
    // 端点优先认"活的父点"，而不是 value 里那份服务端算好的副本：
    // 拖动时本地预览只改了端点的坐标，段自己那份 value 还是上一趟往返的旧值，
    // 认副本的话线段就停在原地、等服务端回来才被拉过去（老师看到的"一直来回抽"）。
    // 不在拖动时两者本来就相等，这条等于没改行为。
    var heads = [pointOf(objById(def.start)), pointOf(objById(def.end))];
    if (heads[0] && heads[1]) return heads;
    if (value.start && value.end) {
      return [normalizePointList([value.start])[0], normalizePointList([value.end])[0]];
    }
    // 垂线 / 平行线 / 中垂线 / 角平分线 / 切线这类"点 + 方向"的直线没有 start/end：
    // 用方向现推两个端点。画的时候按视野延伸，命中判定、动点取参也就都当直线用了
    // （服务端 line_of 认的就是这个形状；参数 t=0 落在 point 上，和网页端取参一致）。
    var anchor = normalizePointList([value.point])[0];
    if (anchor && value.direction) {
      var way = normalizePointList([value.direction])[0];
      if (way && (way.x || way.y)) {
        return [anchor, { x: anchor.x + way.x, y: anchor.y + way.y }];
      }
    }
    if (value.a && value.b) {
      return [normalizePointList([value.a])[0], normalizePointList([value.b])[0]];
    }
    return heads;
  }

  function centerOf(obj) {
    var value = (obj && obj.value) || {};
    if (value.center) {
      var parsed = normalizePointList([value.center])[0];
      if (parsed) return parsed;
    }
    if (typeof value.cx === 'number' && typeof value.cy === 'number') {
      return { x: value.cx, y: value.cy };
    }
    var def = (obj && obj.definition) || {};
    return pointOf(objById(def.center));
  }

  function radiusOf(obj) {
    var value = (obj && obj.value) || {};
    var def = (obj && obj.definition) || {};
    var radius = num(value.radius, num(value.r, num(def.radius, null)));
    if (radius !== null && radius > 0) return radius;
    var center = centerOf(obj);
    var through = pointOf(objById(def.through));
    if (center && through) {
      var dx = through.x - center.x;
      var dy = through.y - center.y;
      return Math.sqrt(dx * dx + dy * dy);
    }
    return null;
  }

  function defineText(obj) {
    var def = (obj && obj.definition) || {};
    var type = obj.type;
    if (type === 'point' || type === 'intersect') {
      if (isIntersection(obj)) {
        var pair = def.intersect || def.intersections || [];
        return '交点：' + (pair.length ? pair.join(' 与 ') : '父对象') +
               (def.index ? '（第 ' + (def.index + 1) + ' 个）' : '');
      }
      if (def.on) {
        return '对象上的动点：' + def.on +
               (def.parameter !== undefined ? '，参数 ' + fmt(def.parameter) : '');
      }
      return '自由点：(' + fmt(def.x) + ', ' + fmt(def.y) + ')';
    }
    if (def.start !== undefined || def.end !== undefined) {
      return '两点定线：' + fmt(def.start) + ' 与 ' + fmt(def.end);
    }
    if (def.center !== undefined) {
      var text = '圆心 ' + fmt(def.center);
      if (def.radius !== undefined) text += '，半径 ' + fmt(def.radius);
      if (def.through !== undefined && typeof def.through !== 'object') {
        text += '，过 ' + fmt(def.through);
      }
      if (def.from !== undefined) text += '，从 ' + fmt(def.from) + ' 到 ' + fmt(def.to);
      return text;
    }
    if (def.through !== undefined && Array.isArray(def.through)) {
      return '过三点：' + def.through.join('、');
    }
    if (def.points !== undefined) return '顶点：' + String(def.points);
    if (def.a !== undefined && def.n !== undefined) return '正 ' + def.n + ' 边形，边 ' + def.a + '-' + def.b;
    if (def.a !== undefined && def.b !== undefined && def.c !== undefined) {
      return '点 ' + def.a + '、' + def.b + '、' + def.c;
    }
    if (def.a !== undefined && def.b !== undefined) return '点 ' + def.a + '、' + def.b;
    if (def.through !== undefined && def.to !== undefined) return '过 ' + def.through + '，关于 ' + def.to;
    if (def.to !== undefined && def.at !== undefined) return '切 ' + def.to + ' 于 ' + def.at;
    if (def.expr !== undefined) {
      var head = 'y = ' + def.expr;
      if (obj.type === 'inequality') head = def.expr;
      if (obj.type === 'implicit') head = def.expr + ' = 0';
      if (obj.type === 'polar') head = 'r = ' + def.expr;
      return head + (def.xmin !== undefined ? '（x 从 ' + fmt(def.xmin) + ' 到 ' + fmt(def.xmax) + '）' : '')
                  + (def.tmin !== undefined ? '（参数从 ' + fmt(def.tmin) + ' 到 ' + fmt(def.tmax) + '）' : '');
    }
    if (def.exprs !== undefined) return String(def.exprs) + '（' + (def.op || 'and') + '）';
    if (def.x !== undefined && def.y !== undefined && typeof def.x === 'string') {
      return 'x = ' + def.x + '，y = ' + def.y;
    }
    if (def.text !== undefined) return '文字：' + def.text;
    if (def.of !== undefined) return '关于 ' + String(def.of);
    if (def.value !== undefined) return '值：' + fmt(def.value);
    if (def.kind !== undefined) return String(def.kind) + ' ' + JSON.stringify(def);
    var keys = [];
    for (var key in def) {
      if (Object.prototype.hasOwnProperty.call(def, key)) {
        keys.push(key + '=' + (typeof def[key] === 'object' ? JSON.stringify(def[key]) : def[key]));
      }
    }
    return keys.length ? keys.join('，') : '（没有定义字段）';
  }

  /* 度量的显示：静止时给"分数 + 根号"的精确形式，拖动时给小数。

     精度不是算不出来，而是拖动中坐标一直在变：写成分数根号又长又没意义，还慢 ——
     和空间那边（plot._solid_angle_info 的 want_exact）一个规矩。 */
  function measureText(obj) {
    var value = (obj && obj.value) || {};
    var busy = !!(state.drag || state.rotate);
    if (typeof value.degrees === 'number') {
      var degrees = fmt(value.degrees) + '°';
      if (busy || value.approximate) {
        return degrees + '（sin ' + fmt(value.sinValue) + '，cos ' + fmt(value.cosValue) + '）';
      }
      return degrees + '（sin ' + (value.sin || '—') + '，cos ' + (value.cos || '—') + '）';
    }
    if (obj.type === 'polygon' || obj.type === 'regular_polygon') {
      if (typeof value.area === 'number') {
        var area = (!busy && value.areaExact) ? value.areaExact : fmt(value.area);
        var extra = typeof value.perimeter === 'number' ? '，周长 ' + fmt(value.perimeter) : '';
        return '面积 ' + area + extra;
      }
    }
    if (busy) {
      return (typeof value.decimal === 'string' && value.decimal) ? value.decimal : fmt(value.value);
    }
    if (typeof value.exact === 'string' && value.exact) return value.exact;
    return (typeof value.decimal === 'string' && value.decimal) ? value.decimal : fmt(value.value);
  }

  function valueText(obj) {
    var value = (obj && obj.value) || {};
    var point = pointOf(obj);
    if (obj.type === 'point' || obj.type === 'intersect') {
      return point ? '(' + fmt(point.x) + ', ' + fmt(point.y) + ')' : '还没算出来';
    }
    if (obj.type === 'measure' || obj.type === 'angle' ||
        obj.type === 'polygon' || obj.type === 'regular_polygon') {
      return measureText(obj);
    }
    if (obj.type === 'circle') {
      var center = centerOf(obj);
      var radius = radiusOf(obj);
      if (center && radius !== null) {
        return '圆心 (' + fmt(center.x) + ', ' + fmt(center.y) + ')，半径 ' + fmt(radius);
      }
    }
    if (obj.type === 'segment' || obj.type === 'line' || obj.type === 'ray' || obj.type === 'vector') {
      var ends = endpointsOf(obj);
      if (ends[0] && ends[1]) {
        var dx = ends[1].x - ends[0].x;
        var dy = ends[1].y - ends[0].y;
        var length = Math.sqrt(dx * dx + dy * dy);
        return '长度 ' + fmt(length) + '，斜率 ' + (dx ? fmt(dy / dx) : '不存在');
      }
    }
    if (LINE_TYPES[obj.type]) {
      // 垂线 / 平行线 / 中垂线 / 角平分线 / 切线：跟直线一样先给"过哪点、斜率多少"；
      // 它是谁的切线、垂直/平行于谁，写在上面的定义那行里。
      var anchor = normalizePointList([value.point])[0];
      var way = normalizePointList([value.direction])[0];
      if (anchor && way) {
        return '过 (' + fmt(anchor.x) + ', ' + fmt(anchor.y) + ')，斜率 ' +
               (Math.abs(way.x) < 1e-12 ? '不存在' : fmt(way.y / way.x));
      }
    }
    if (typeof value.value === 'number') return fmt(value.value);
    if (typeof value.value === 'string') return value.value;
    if (typeof value.text === 'string') return value.text;
    if (typeof value.number === 'number') return fmt(value.number);
    var segments = sampledSegments(obj);
    if (segments.length) {
      var total = 0;
      for (var index = 0; index < segments.length; index++) total += segments[index].length;
      return '采样 ' + segments.length + ' 段，共 ' + total + ' 个点';
    }
    if (value.polygons && value.polygons.length) return '区域 ' + value.polygons.length + ' 块';
    var keys = [];
    for (var key in value) {
      if (Object.prototype.hasOwnProperty.call(value, key)) keys.push(key);
    }
    return keys.length ? keys.join('、') : '（没有值）';
  }

  /* ------------------------------------------------------------ 请求层 */

  function problemText(data, status) {
    if (data && data.error) return String(data.error);
    var errors = (data && data.errors) || [];
    if (errors.length) {
      var first = errors[0] || {};
      var message = first.message || first.code || '平面操作被拒绝';
      if (first.hint) message += '（' + first.hint + '）';
      return String(message);
    }
    return '请求失败：HTTP ' + status;
  }

  function request(path, body, timeoutMs) {
    var options = { method: body ? 'POST' : 'GET' };
    var headers = {};
    if (body) {
      headers['Content-Type'] = 'application/json';
      options.body = JSON.stringify(body);
    }
    if (TOKEN) headers['X-Auth-Token'] = TOKEN;
    options.headers = headers;
    var controller = null;
    var timer = null;
    if (global.AbortController) {
      controller = new AbortController();
      options.signal = controller.signal;
      timer = setTimeout(function () { controller.abort(); }, timeoutMs || 20000);
    }
    return fetch(path, options).then(function (response) {
      return response.text().then(function (text) {
        var data = {};
        try { data = text ? JSON.parse(text) : {}; }
        catch (error) { data = { ok: false, error: '服务端返回的不是 JSON' }; }
        if (!response.ok || data.ok === false) throw new Error(problemText(data, response.status));
        return data;
      });
    }).then(function (data) {
      if (timer) clearTimeout(timer);
      return data;
    }, function (error) {
      if (timer) clearTimeout(timer);
      if (error && error.name === 'AbortError') throw new Error('服务端响应超时');
      throw error;
    });
  }

  function showStatus(text, kind, autoHideMs) {
    var box = $('#planeStatus');
    $('#planeStatusText').textContent = text;
    box.className = 'plane-status' + (kind ? ' ' + kind : '');
    if (state.statusTimer) { clearTimeout(state.statusTimer); state.statusTimer = null; }
    if (autoHideMs) {
      state.statusTimer = setTimeout(function () {
        state.statusTimer = null;
        $('#planeStatus').className = 'plane-status hidden';
      }, autoHideMs);
    }
  }

  function hideStatus() {
    if (state.statusTimer) { clearTimeout(state.statusTimer); state.statusTimer = null; }
    $('#planeStatus').className = 'plane-status hidden';
  }

  function setNote(text) {
    state.lastNote = text || '';
    $('#planeNote').textContent = text || '';
  }

  /* ------------------------------------------------------------ 视野 */

  function viewFromPlane(plane) {
    var view = (plane && plane.view) || {};
    var xmin = num(view.xmin, -10);
    var xmax = num(view.xmax, 10);
    var ymin = num(view.ymin, -6);
    var ymax = num(view.ymax, 6);
    if (!(xmax > xmin)) xmax = xmin + 1;
    if (!(ymax > ymin)) ymax = ymin + 1;
    return { xmin: xmin, ymin: ymin, xmax: xmax, ymax: ymax,
             grid: view.grid !== false, axes: view.axes !== false, polar: !!view.polar };
  }

  function applyView() {
    if (!state.svg || !state.view) return;
    var view = state.view;
    var width = view.xmax - view.xmin;
    var height = view.ymax - view.ymin;
    // 数据 y 向上、SVG y 向下：viewBox 的 y 取 -ymax，画的时候写 -y
    state.svg.setAttribute('viewBox', view.xmin + ' ' + (-view.ymax) + ' ' + width + ' ' + height);
    state.svg.setAttribute('data-view',
      [round4(view.xmin), round4(view.ymin), round4(view.xmax), round4(view.ymax)].join(','));
  }

  function niceStep(span, target) {
    if (!(span > 0)) return 1;
    var raw = span / Math.max(1, target);
    var power = Math.pow(10, Math.floor(Math.log(raw) / Math.LN10));
    var scaled = raw / power;
    var step = scaled <= 1 ? 1 : scaled <= 2 ? 2 : scaled <= 5 ? 5 : 10;
    return step * power;
  }

  function fmtTick(value, step) {
    var decimals = step < 1 ? Math.min(4, Math.ceil(-Math.log(step) / Math.LN10)) : 0;
    var text = Number(value.toFixed(decimals)).toString();
    return text === '-0' ? '0' : text;
  }

  function drawGrid() {
    var layer = state.layers.grid;
    clear(layer);
    var view = state.view;
    if (!view) return;
    var stepX = niceStep(view.xmax - view.xmin, 10);
    var stepY = niceStep(view.ymax - view.ymin, 8);
    var axisY = (view.ymin <= 0 && view.ymax >= 0) ? 0 : view.ymin;
    var axisX = (view.xmin <= 0 && view.xmax >= 0) ? 0 : view.xmin;
    var index;
    if (view.grid) {
      var startX = Math.ceil(view.xmin / stepX) * stepX;
      for (index = startX; index <= view.xmax + 1e-9; index += stepX) {
        layer.appendChild(svgNode('line', { class: 'plane-grid', x1: index, y1: Y(view.ymin),
                                            x2: index, y2: Y(view.ymax),
                                            'vector-effect': 'non-scaling-stroke' }));
      }
      var startY = Math.ceil(view.ymin / stepY) * stepY;
      for (index = startY; index <= view.ymax + 1e-9; index += stepY) {
        layer.appendChild(svgNode('line', { class: 'plane-grid', x1: view.xmin, y1: Y(index),
                                            x2: view.xmax, y2: Y(index),
                                            'vector-effect': 'non-scaling-stroke' }));
      }
    }
    if (view.axes) {
      layer.appendChild(svgNode('line', { class: 'plane-axis', x1: view.xmin, y1: Y(axisY),
                                          x2: view.xmax, y2: Y(axisY),
                                          'vector-effect': 'non-scaling-stroke' }));
      layer.appendChild(svgNode('line', { class: 'plane-axis', x1: axisX, y1: Y(view.ymin),
                                          x2: axisX, y2: Y(view.ymax),
                                          'vector-effect': 'non-scaling-stroke' }));
    }
    // 刻度数字：位置跟着坐标轴走，字号固定是屏幕上的 12px（用 U() 折算）
    var startTickX = Math.ceil(view.xmin / stepX) * stepX;
    for (index = startTickX; index <= view.xmax + 1e-9; index += stepX) {
      if (Math.abs(index) < 1e-9 && view.ymin <= 0 && view.ymax >= 0) continue;
      var labelX = svgNode('text', { class: 'plane-tick', x: index, y: Y(axisY) + U(15),
                                     'text-anchor': 'middle' });
      setFont(labelX, 12);
      labelX.textContent = fmtTick(index, stepX);
      layer.appendChild(labelX);
    }
    var startTickY = Math.ceil(view.ymin / stepY) * stepY;
    for (index = startTickY; index <= view.ymax + 1e-9; index += stepY) {
      if (Math.abs(index) < 1e-9 && view.xmin <= 0 && view.xmax >= 0) continue;
      var labelY = svgNode('text', { class: 'plane-tick', x: X(axisX) - U(7), y: Y(index) + U(4),
                                     'text-anchor': 'end' });
      setFont(labelY, 12);
      labelY.textContent = fmtTick(index, stepY);
      layer.appendChild(labelY);
    }
  }

  function X(x) { return num(x, 0); }

  /* ------------------------------------------------------------ 画图 */

  function drawLabel(obj, point, isect) {
    if (obj.style && obj.style.label === false) return;
    var text = obj.label || obj.id;
    if (!text) return;
    var element = svgNode('text', {
      class: 'plane-label' + (state.selected === obj.id ? ' on' : ''),
      x: X(point.x) + U(8), y: Y(point.y) - U(8),
      'data-plane-label': obj.id
    });
    setFont(element, 14);
    // 描边白圈也是按屏幕像素折算的：写死的 3px 在数据坐标里是 3 个数据单位
    element.style.strokeWidth = U(3) + 'px';
    element.textContent = text;
    if (isect) element.setAttribute('data-plane-kind', 'intersect-label');
    state.layers.labels.appendChild(element);
  }

  function drawPoint(obj) {
    var point = pointOf(obj);
    if (!point) {
      state.warnings.push('对象 ' + obj.id + ' 是点，但服务端还没给出坐标，这一轮先不画。');
      return;
    }
    var isect = isIntersection(obj);
    var chosen = state.selected === obj.id;
    var radius = U(isect ? 4.2 : 4.6) * (chosen ? 1.5 : 1);
    var attrs = {
      class: (isect ? 'plane-isect' : 'plane-point') + (chosen ? ' on' : ''),
      cx: X(point.x), cy: Y(point.y), r: radius,
      'vector-effect': 'non-scaling-stroke',
      'data-plane-id': obj.id,
      'data-plane-kind': isect ? 'intersect' : 'point'
    };
    state.layers.points.appendChild(svgNode('circle', attrs));
    state.rendered += 1;
    if (isDraggable(obj)) {
      state.layers.points.appendChild(svgNode('circle', {
        class: 'plane-hit grab', cx: X(point.x), cy: Y(point.y), r: U(HIT_PX),
        'data-plane-id': obj.id, 'data-plane-drag': '1'
      }));
    }
    drawLabel(obj, point, isect);
  }

  function reach() {
    var view = state.view;
    if (!view) return 30;
    var dx = view.xmax - view.xmin;
    var dy = view.ymax - view.ymin;
    return Math.sqrt(dx * dx + dy * dy) * 1.5;
  }

  function drawLineLike(obj, kind) {
    var ends = endpointsOf(obj);
    if (!ends[0] || !ends[1]) {
      var segments = sampledSegments(obj);
      if (segments.length) { drawPolyline(obj, segments, 'plane-shape'); return; }
      state.warnings.push('对象 ' + obj.id + '（' + (TYPE_CN[obj.type] || obj.type) +
                          '）缺少两个端点，这一轮先不画。');
      return;
    }
    var a = ends[0];
    var b = ends[1];
    if (obj.type === 'segment') {
      state.layers.shapes.appendChild(svgNode('line', {
        class: 'plane-shape' + (state.selected === obj.id ? ' on' : ''),
        x1: X(a.x), y1: Y(a.y), x2: X(b.x), y2: Y(b.y),
        'vector-effect': 'non-scaling-stroke', 'data-plane-id': obj.id
      }));
      state.rendered += 1;
      return;
    }
    // 直线两头都伸出去、射线只从 a 伸出去：长度按视野对角线算，怎么缩放都够
    var dx = b.x - a.x;
    var dy = b.y - a.y;
    var length = Math.sqrt(dx * dx + dy * dy) || 1;
    var from = (obj.type === 'ray' || obj.type === 'vector') ? 0 : -reach();
    var to = reach();
    state.layers.shapes.appendChild(svgNode('line', {
      class: 'plane-shape' + (state.selected === obj.id ? ' on' : ''),
      x1: X(a.x + dx / length * from), y1: Y(a.y + dy / length * from),
      x2: X(a.x + dx / length * to), y2: Y(a.y + dy / length * to),
      'vector-effect': 'non-scaling-stroke', 'data-plane-id': obj.id
    }));
    state.rendered += 1;
    if (obj.type === 'vector') {
      // 箭头用内联 SVG 画的三角形（Win7 没有 emoji 字体，不能拿字符当箭头）
      var size = U(9);
      var angle = Math.atan2(dy, dx);
      var tipX = b.x;
      var tipY = b.y;
      var leftX = tipX - size * Math.cos(angle - 0.42);
      var leftY = tipY - size * Math.sin(angle - 0.42);
      var rightX = tipX - size * Math.cos(angle + 0.42);
      var rightY = tipY - size * Math.sin(angle + 0.42);
      state.layers.shapes.appendChild(svgNode('polygon', {
        class: 'plane-vector-head', fill: 'currentColor',
        points: [X(tipX) + ',' + Y(tipY), X(leftX) + ',' + Y(leftY),
                 X(rightX) + ',' + Y(rightY)].join(' ')
      }));
    }
    if (kind === 'line' && state.selected === obj.id) {
      state.layers.shapes.appendChild(svgNode('line', {
        class: 'plane-shape on', x1: X(a.x), y1: Y(a.y), x2: X(b.x), y2: Y(b.y),
        'vector-effect': 'non-scaling-stroke'
      }));
    }
  }

  function drawPolyline(obj, segments, className) {
    for (var index = 0; index < segments.length; index++) {
      var list = segments[index];
      var parts = [];
      for (var position = 0; position < list.length; position++) {
        parts.push((position ? 'L' : 'M') + X(list[position].x).toFixed(3) + ' ' +
                   Y(list[position].y).toFixed(3));
      }
      state.layers.curves.appendChild(svgNode('path', {
        class: className + (state.selected === obj.id ? ' on' : ''),
        d: parts.join(' '), 'vector-effect': 'non-scaling-stroke',
        'data-plane-id': obj.id
      }));
      state.rendered += 1;
    }
  }

  function drawCircle(obj) {
    var center = centerOf(obj);
    var radius = radiusOf(obj);
    if (!center || radius === null || !(radius > 0)) {
      state.warnings.push('对象 ' + obj.id + ' 是圆，但圆心或半径还没算出来，这一轮先不画。');
      return;
    }
    state.layers.shapes.appendChild(svgNode('circle', {
      class: 'plane-shape' + (state.selected === obj.id ? ' on' : ''),
      cx: X(center.x), cy: Y(center.y), r: radius,
      'vector-effect': 'non-scaling-stroke', 'data-plane-id': obj.id
    }));
    state.rendered += 1;
  }

  function arcAngles(obj) {
    var value = (obj && obj.value) || {};
    var center = centerOf(obj);
    if (!center) return null;
    var start = num(value.startAngle, num(value.a1, null));
    var end = num(value.endAngle, num(value.a2, null));
    if (start !== null && end !== null) return { center: center, from: start, to: end };
    var def = (obj && obj.definition) || {};
    var fromPoint = pointOf(objById(def.from));
    var toPoint = pointOf(objById(def.to));
    if (fromPoint && toPoint) {
      return { center: center,
               from: Math.atan2(fromPoint.y - center.y, fromPoint.x - center.x),
               to: Math.atan2(toPoint.y - center.y, toPoint.x - center.x) };
    }
    return null;
  }

  function drawArcLike(obj, isSector) {
    var segments = sampledSegments(obj);
    if (segments.length) { drawPolyline(obj, segments, 'plane-shape'); return; }
    var angles = arcAngles(obj);
    var radius = radiusOf(obj);
    if (!angles || radius === null || !(radius > 0)) {
      state.warnings.push('对象 ' + obj.id + '（' + (TYPE_CN[obj.type] || obj.type) +
                          '）缺圆心/半径/端点，这一轮先不画。');
      return;
    }
    var delta = angles.to - angles.from;
    while (delta > Math.PI * 2) delta -= Math.PI * 2;
    while (delta < -Math.PI * 2) delta += Math.PI * 2;
    var startX = angles.center.x + radius * Math.cos(angles.from);
    var startY = angles.center.y + radius * Math.sin(angles.from);
    var endX = angles.center.x + radius * Math.cos(angles.to);
    var endY = angles.center.y + radius * Math.sin(angles.to);
    var large = Math.abs(delta) > Math.PI ? 1 : 0;
    // 数学里角度是逆时针为正，画布 y 被我们翻过来了，所以 sweep 取 0
    var path = 'M ' + X(startX) + ' ' + Y(startY) +
               ' A ' + radius + ' ' + radius + ' 0 ' + large + ' 0 ' + X(endX) + ' ' + Y(endY);
    if (isSector) path += ' L ' + X(angles.center.x) + ' ' + Y(angles.center.y) + ' Z';
    state.layers.shapes.appendChild(svgNode('path', {
      class: (isSector ? 'plane-poly' : 'plane-shape') + (state.selected === obj.id ? ' on' : ''),
      d: path, 'vector-effect': 'non-scaling-stroke', 'data-plane-id': obj.id
    }));
    state.rendered += 1;
  }

  function polygonPoints(obj) {
    var value = (obj && obj.value) || {};
    var def = (obj && obj.definition) || {};
    var points = normalizePointList(value.points || value.vertices);
    if (points.length) return points;
    var ids = def.points || [];
    var out = [];
    for (var index = 0; index < ids.length; index++) {
      var point = pointOf(objById(ids[index]));
      if (point) out.push(point);
    }
    return out;
  }

  function drawPolygon(obj) {
    var points = polygonPoints(obj);
    if (points.length < 3) {
      state.warnings.push('对象 ' + obj.id + '（' + (TYPE_CN[obj.type] || obj.type) +
                          '）的顶点还没算齐，这一轮先不画。');
      return;
    }
    var list = [];
    for (var index = 0; index < points.length; index++) {
      list.push(X(points[index].x) + ',' + Y(points[index].y));
    }
    // 描边要按屏幕像素画（和线段、圆一样）：viewBox 用的是数据坐标，漏掉这一条，
    // 2.2 就成了 2.2 个数据单位 —— 视野宽 20 单位时单边 70 多像素，整个图形糊成一坨。
    state.layers.regions.appendChild(svgNode('polygon', {
      class: 'plane-poly' + (state.selected === obj.id ? ' on' : ''),
      points: list.join(' '), 'vector-effect': 'non-scaling-stroke',
      'data-plane-id': obj.id
    }));
    state.rendered += 1;
  }

  function drawCurve(obj) {
    var segments = sampledSegments(obj);
    if (!segments.length) {
      state.warnings.push('对象 ' + obj.id + '（' + (TYPE_CN[obj.type] || obj.type) +
                          '）还没有采样点，这一轮先不画（网页端不做数学）。');
      return;
    }
    drawPolyline(obj, segments, obj.type === 'inequality' ? 'plane-region' : 'plane-curve');
  }

  function drawRegion(obj) {
    var value = (obj && obj.value) || {};
    var groups = value.polygons || value.regions || [];
    var index;
    var drew = 0;
    for (index = 0; index < groups.length; index++) {
      var points = normalizePointList(groups[index]);
      if (points.length < 3) continue;
      var list = [];
      for (var position = 0; position < points.length; position++) {
        list.push(X(points[position].x) + ',' + Y(points[position].y));
      }
      // 同上：今天 .plane-region 没描边，但画法要和其它图形一致，免得以后一加描边就糊。
      state.layers.regions.appendChild(svgNode('polygon', {
        class: 'plane-region' + (state.selected === obj.id ? ' on' : ''),
        points: list.join(' '), 'vector-effect': 'non-scaling-stroke',
        'data-plane-id': obj.id
      }));
      drew += 1;
    }
    if (!drew) {
      var segments = sampledSegments(obj);
      if (segments.length) { drawPolyline(obj, segments, 'plane-region'); return; }
      state.warnings.push('对象 ' + obj.id + ' 是不等式区域，但服务端还没给出区域多边形，这一轮先不画。');
      return;
    }
    state.rendered += drew;
  }

  function drawSlider(obj) {
    // 滑动条画成左上角的 HUD（跟着视野走）：它不是一个几何对象，钉在数据坐标上会更奇怪
    var def = (obj && obj.definition) || {};
    var value = num((obj.value || {}).value, num(def.value, 0));
    var low = num(def.min, 0);
    var high = num(def.max, 1);
    if (!(high > low)) high = low + 1;
    var slot = state.sliderSlot || 0;
    state.sliderSlot = slot + 1;
    var view = state.view;
    var x0 = view.xmin + U(14);
    var y0 = view.ymax - U(20) - slot * U(30);
    var width = U(120);
    var ratio = clamp((value - low) / (high - low), 0, 1);
    var group = svgNode('g', { 'data-plane-id': obj.id, 'data-plane-kind': 'slider' });
    group.appendChild(svgNode('line', { class: 'plane-slider-bar', x1: x0, y1: Y(y0),
                                        x2: x0 + width, y2: Y(y0),
                                        'vector-effect': 'non-scaling-stroke' }));
    group.appendChild(svgNode('circle', { class: 'plane-slider-knob', cx: x0 + width * ratio,
                                          cy: Y(y0), r: U(5.5),
                                          'vector-effect': 'non-scaling-stroke' }));
    var caption = svgNode('text', { class: 'plane-label', x: x0, y: Y(y0) - U(7) });
    setFont(caption, 14);
    caption.style.strokeWidth = U(3) + 'px';
    caption.textContent = obj.label || obj.id;
    group.appendChild(caption);
    var reading = svgNode('text', { class: 'plane-tick', x: x0 + width + U(8), y: Y(y0) + U(4) });
    setFont(reading, 12);
    reading.textContent = fmt(value) + ' / ' + fmt(low) + '..' + fmt(high);
    group.appendChild(reading);
    state.layers.labels.appendChild(group);
    state.rendered += 1;
  }

  function drawTextObject(obj) {
    var def = (obj && obj.definition) || {};
    var value = (obj && obj.value) || {};
    var text = value.text !== undefined ? value.text : def.text;
    var point = pointOf(obj);
    if (text === undefined || text === null) {
      state.warnings.push('对象 ' + obj.id + ' 是文字对象，但没有文字内容，已跳过。');
      return;
    }
    if (!point) {
      state.warnings.push('对象 ' + obj.id + ' 是文字对象，但没给位置，这一轮先不画。');
      return;
    }
    var element = svgNode('text', { class: 'plane-text', x: X(point.x), y: Y(point.y),
                                    'data-plane-id': obj.id });
    setFont(element, 15);
    element.textContent = String(text);
    state.layers.labels.appendChild(element);
    state.rendered += 1;
  }

  function drawLocus(obj) {
    var segments = sampledSegments(obj);
    if (!segments.length) {
      // 轨迹点还没算出来：不画也不编，只在信息框里说清楚
      state.pendingLocus = state.pendingLocus || [];
      state.pendingLocus.push(obj.id);
      return;
    }
    drawPolyline(obj, segments, obj.type === 'trace' ? 'plane-trace' : 'plane-curve');
  }

  function drawAngle(obj) {
    var value = (obj && obj.value) || {};
    var def = (obj && obj.definition) || {};
    var vertex = normalizePointList([value.vertex])[0] || pointOf(objById(def.b));
    var first = normalizePointList([value.from])[0] || pointOf(objById(def.a));
    var second = normalizePointList([value.to])[0] || pointOf(objById(def.c));
    if (!vertex || !first || !second) {
      var segments = sampledSegments(obj);
      if (segments.length) { drawPolyline(obj, segments, 'plane-shape'); return; }
      state.warnings.push('对象 ' + obj.id + ' 是角，但三个点还没算齐，这一轮先不画。');
      return;
    }
    var left = Math.atan2(first.y - vertex.y, first.x - vertex.x);
    var right = Math.atan2(second.y - vertex.y, second.x - vertex.x);
    var delta = right - left;
    while (delta > Math.PI) delta -= Math.PI * 2;
    while (delta < -Math.PI) delta += Math.PI * 2;
    var radius = U(30);
    var startX = vertex.x + radius * Math.cos(left);
    var startY = vertex.y + radius * Math.sin(left);
    var endX = vertex.x + radius * Math.cos(right);
    var endY = vertex.y + radius * Math.sin(right);
    // 直角改用小方块：老师讲解「这个角是直角」时，圆弧谁都画得出，方块才是课本的画法。
    var armFirst = { x: first.x - vertex.x, y: first.y - vertex.y };
    var armSecond = { x: second.x - vertex.x, y: second.y - vertex.y };
    var lengthFirst = Math.sqrt(armFirst.x * armFirst.x + armFirst.y * armFirst.y);
    var lengthSecond = Math.sqrt(armSecond.x * armSecond.x + armSecond.y * armSecond.y);
    var cosine = (lengthFirst > 1e-12 && lengthSecond > 1e-12)
      ? (armFirst.x * armSecond.x + armFirst.y * armSecond.y) / (lengthFirst * lengthSecond)
      : 1;
    var notch = '';
    if (Math.abs(cosine) < 1e-9) {
      var side = Math.min(U(MARK_SIZE_PX), Math.min(lengthFirst, lengthSecond) * 0.3);
      var one = { x: armFirst.x / lengthFirst, y: armFirst.y / lengthFirst };
      var two = { x: armSecond.x / lengthSecond, y: armSecond.y / lengthSecond };
      notch = ' M ' + X(vertex.x + one.x * side) + ' ' + Y(vertex.y + one.y * side) +
              ' L ' + X(vertex.x + (one.x + two.x) * side) + ' ' +
              Y(vertex.y + (one.y + two.y) * side) +
              ' L ' + X(vertex.x + two.x * side) + ' ' + Y(vertex.y + two.y * side);
    } else {
      notch = ' M ' + X(startX) + ' ' + Y(startY) + ' A ' + radius + ' ' + radius + ' 0 0 ' +
              (delta > 0 ? 0 : 1) + ' ' + X(endX) + ' ' + Y(endY);
    }
    state.layers.shapes.appendChild(svgNode('path', {
      class: 'plane-shape' + (state.selected === obj.id ? ' on' : ''),
      d: 'M ' + X(first.x) + ' ' + Y(first.y) + ' L ' + X(vertex.x) + ' ' + Y(vertex.y) +
         ' L ' + X(second.x) + ' ' + Y(second.y) + notch,
      'vector-effect': 'non-scaling-stroke', 'data-plane-id': obj.id
    }));
    state.rendered += 1;
  }

  function drawObject(obj) {
    var type = obj.type;
    if (type === 'point' || type === 'intersect') return drawPoint(obj);
    // 重心 / 内心 / 垂心：服务端算出来就是一个坐标，照点画（标签由 drawPoint 一并画）
    if (type === 'centroid' || type === 'incenter' || type === 'orthocenter') return drawPoint(obj);
    if (type === 'segment') return drawLineLike(obj, 'segment');
    if (type === 'ray' || type === 'line' || type === 'vector') return drawLineLike(obj, type);
    if (type === 'circle') return drawCircle(obj);
    if (type === 'arc') return drawArcLike(obj, false);
    if (type === 'sector') return drawArcLike(obj, true);
    if (type === 'polygon' || type === 'regular_polygon') return drawPolygon(obj);
    if (type === 'inequality') return drawRegion(obj);
    if (CURVE_TYPES[type]) return drawCurve(obj);
    if (type === 'slider') return drawSlider(obj);
    if (type === 'text') return drawTextObject(obj);
    if (type === 'locus' || type === 'trace') return drawLocus(obj);
    if (type === 'angle') return drawAngle(obj);
    if (type === 'conic') {
      var segments = sampledSegments(obj);
      if (segments.length) return drawPolyline(obj, segments, 'plane-curve');
      return drawLineLike(obj, 'line');
    }
    if (LINE_TYPES[type]) {
      var samples = sampledSegments(obj);
      if (samples.length) return drawPolyline(obj, samples, 'plane-shape');
      return drawLineLike(obj, 'line');
    }
    if (VALUE_ONLY[type]) return;   // 只有数值：画布上本来就没它，代数区里有
    state.warnings.push('不认识的对象类型「' + type + '」（' + obj.id + '），已跳过不画。');
  }

  /* ---------------------------------------------------------- 讲解用的几何记号

     等长（中垂线把线段分成相等的两半）、等角（角平分线把角分成相等的两半）、
     直角（垂线 / 中垂线和被它垂直的那条线）—— 老师指着屏幕讲解全靠这几个记号。
     记号只负责"画"：不参与求值、不参与命中（marks 图层带 pointer-events:none），
     位置全部由对象**活的父点**现推，所以拖动端点时记号跟着走。
     画出来的是数据坐标，线宽用 vector-effect 抵掉缩放，和别的图层一个规矩。 */

  var MARK_SIZE_PX = 12;    // 直角小方块的边长（屏幕像素）
  var MARK_TICK_PX = 9;     // 等长小横杠的长度
  var MARK_ARC_PX = 20;     // 等角小圆弧的半径

  /* 单位方向向量：长度为 0 返回 null（退化时不画记号，绝不硬算） */
  function unitOf(way) {
    if (!way) return null;
    var length = Math.sqrt(way.x * way.x + way.y * way.y);
    if (!(length > 1e-12)) return null;
    return { x: way.x / length, y: way.y / length };
  }

  /* 过 spot 作直线的垂足（点 + 方向那种直线；构造线也用得上） */
  function footOnLine(anchor, way, spot) {
    if (!anchor || !way || !spot) return null;
    var square = way.x * way.x + way.y * way.y;
    if (!(square > 1e-18)) return null;
    var t = ((spot.x - anchor.x) * way.x + (spot.y - anchor.y) * way.y) / square;
    return { x: anchor.x + way.x * t, y: anchor.y + way.y * t };
  }

  /* 一条线（含构造线）的锚点 + 方向：都取活的端点，拖动中记号也不落后 */
  function lineFrameOf(obj) {
    if (!obj) return null;
    var ends = endpointsOf(obj);
    if (!ends[0] || !ends[1]) return null;
    var way = { x: ends[1].x - ends[0].x, y: ends[1].y - ends[0].y };
    if (!(Math.abs(way.x) > 1e-12) && !(Math.abs(way.y) > 1e-12)) return null;
    return { anchor: ends[0], way: way };
  }

  function appendMark(obj, d) {
    if (!d || !state.layers.marks) return;
    state.layers.marks.appendChild(svgNode('path', {
      class: 'plane-mark' + (state.selected === obj.id ? ' on' : ''),
      d: d,
      'vector-effect': 'non-scaling-stroke',
      'data-plane-mark': obj.id
    }));
  }

  /* 直角：在两条互相垂直的线交点处画一个小方块（老师小时候画的那个"口"） */
  function markRightAngle(obj, corner, first, second) {
    var u = unitOf(first);
    var v = unitOf(second);
    if (!u || !v) return;
    var size = U(MARK_SIZE_PX);
    var mid = { x: corner.x + (u.x + v.x) * size, y: corner.y + (u.y + v.y) * size };
    appendMark(obj, 'M ' + X(corner.x + u.x * size) + ' ' + Y(corner.y + u.y * size) +
                    ' L ' + X(mid.x) + ' ' + Y(mid.y) +
                    ' L ' + X(corner.x + v.x * size) + ' ' + Y(corner.y + v.y * size));
  }

  /* 垂线：在垂足处画直角（被它垂直的那条线 + 它自己） */
  function markPerpendicular(obj) {
    var def = obj.definition || {};
    var target = lineFrameOf(objById(def.to));
    if (!target) return;
    var self = lineFrameOf(obj);
    if (!self) return;
    var through = pointOf(objById(def.through)) ||
                  normalizePointList([(obj.value || {}).point])[0];
    if (!through) return;
    var foot = footOnLine(target.anchor, target.way, through);
    if (!foot) return;
    markRightAngle(obj, foot, target.way, self.way);
  }

  /* 中垂线：直角 + 两段等长的小横杠（这是课本上"垂直平分"的画法） */
  function markPerpendicularBisector(obj) {
    var def = obj.definition || {};
    var first = pointOf(objById(def.a));
    var second = pointOf(objById(def.b));
    if (!first || !second) return;
    var middle = { x: (first.x + second.x) / 2, y: (first.y + second.y) / 2 };
    var along = { x: second.x - first.x, y: second.y - first.y };
    var u = unitOf(along);
    if (!u) return;
    var self = lineFrameOf(obj);
    if (self) markRightAngle(obj, middle, along, self.way);
    // 等长记号：两半各画一道垂直于线段的小横杠（同一道 = 等长）
    var half = Math.sqrt(along.x * along.x + along.y * along.y) / 2;
    if (!(half > 1e-9)) return;
    var size = U(MARK_TICK_PX) / 2;
    var tick = { x: -u.y, y: u.x };
    var spots = [
      { x: first.x + along.x / 4, y: first.y + along.y / 4 },
      { x: middle.x + along.x / 4, y: middle.y + along.y / 4 }
    ];
    var d = '';
    for (var index = 0; index < spots.length; index++) {
      var spot = spots[index];
      d += (index ? ' ' : '') + 'M ' + X(spot.x - tick.x * size) + ' ' + Y(spot.y - tick.y * size) +
           ' L ' + X(spot.x + tick.x * size) + ' ' + Y(spot.y + tick.y * size);
    }
    appendMark(obj, d);
  }

  /* 角平分线：两侧各画一道同样的小圆弧（等角记号） */
  function markAngleBisector(obj) {
    var def = obj.definition || {};
    var first = pointOf(objById(def.a));
    var vertex = pointOf(objById(def.b));
    var third = pointOf(objById(def.c));
    if (!first || !vertex || !third) return;
    var u = unitOf({ x: first.x - vertex.x, y: first.y - vertex.y });
    var v = unitOf({ x: third.x - vertex.x, y: third.y - vertex.y });
    if (!u || !v) return;
    var middle = { x: u.x + v.x, y: u.y + v.y };
    if (!(Math.abs(middle.x) > 1e-12) && !(Math.abs(middle.y) > 1e-12)) {
      middle = { x: -u.y, y: u.x };          // 平角：取垂直方向（和服务端一致）
    }
    var middleUnit = unitOf(middle);
    if (!middleUnit) return;
    var radius = U(MARK_ARC_PX);
    var span = Math.abs(Math.atan2(u.x * middleUnit.y - u.y * middleUnit.x,
                                   u.x * middleUnit.x + u.y * middleUnit.y)) * 2;
    if (!(span > 1e-6)) return;
    // 两道弧别叠在一起：用两个半径，一道近一道远
    var radii = [radius, radius * 1.25];
    var d = '';
    for (var index = 0; index < 2; index++) {
      var start = index === 0 ? u : middleUnit;
      var end = index === 0 ? middleUnit : v;
      var sweep = ((start.x * end.y - start.y * end.x) > 0) ? 0 : 1;
      var far = radii[index];
      d += (index ? ' ' : '') +
           'M ' + X(vertex.x + start.x * far) + ' ' + Y(vertex.y + start.y * far) +
           ' A ' + far + ' ' + far + ' 0 0 ' + sweep + ' ' +
           X(vertex.x + end.x * far) + ' ' + Y(vertex.y + end.y * far);
    }
    appendMark(obj, d);
  }

  function drawMarks(list) {
    if (!state.layers.marks) return;
    for (var index = 0; index < list.length; index++) {
      var obj = list[index];
      if (!obj || !obj.id || isHidden(obj)) continue;
      var type = obj.type;
      if (type === 'perpendicular') markPerpendicular(obj);
      else if (type === 'perpendicular_bisector') markPerpendicularBisector(obj);
      else if (type === 'angle_bisector') markAngleBisector(obj);
    }
  }

  function drawGeometry() {
    var layers = state.layers;
    clear(layers.regions);
    clear(layers.curves);
    clear(layers.shapes);
    if (layers.marks) clear(layers.marks);
    clear(layers.points);
    clear(layers.labels);
    state.warnings = [];
    state.pendingLocus = [];
    state.sliderSlot = 0;
    state.rendered = 0;
    var list = orderedObjects();
    for (var index = 0; index < list.length; index++) {
      var obj = list[index];
      if (!obj || !obj.id || isHidden(obj)) continue;
      // 一个对象画崩了不能把整块画布带走：这里兜住，变成一句中文警告
      try {
        drawObject(obj);
      } catch (error) {
        state.warnings.push('对象 ' + obj.id + ' 画不出来：' +
                            (error && error.message ? error.message : String(error)));
      }
    }
    // 记号单独一趟画：等长 / 等角 / 直角要看"几个对象之间的关系"，
    // 混在 drawObject 的逐对象循环里会互相踩（同一条中垂线既要画直角又要画两道等长杠）。
    if (layers.marks) {
      try {
        drawMarks(list);
      } catch (error) {
        state.warnings.push('几何记号画不出来：' +
                            (error && error.message ? error.message : String(error)));
      }
    }
    var pending = state.pendingLocus || [];
    for (var position = 0; position < pending.length; position++) {
      state.warnings.push('轨迹 ' + pending[position] + ' 还没有采样点，先不画（网页端不做数学）。');
    }
  }

  function buildSvg() {
    var host = $('#planeCanvas');
    if (!host) return;
    if (state.svg && state.svg.parentNode === host) return;
    clear(host);
    var svg = svgNode('svg', { viewBox: '0 0 1 1', preserveAspectRatio: 'xMidYMid meet',
                               'data-renderer': 'svg' });
    var layers = {};
    // marks 是"等长 / 等角 / 直角"这些讲解用的记号，画在图形之上、点之下：
    // 记号不能盖住点，也不能被线挡住（老师指图讲解时看得见最要紧）。
    var names = ['grid', 'regions', 'curves', 'shapes', 'marks', 'points', 'labels'];
    for (var index = 0; index < names.length; index++) {
      var attrs = { class: 'plane-layer-' + names[index] };
      // 记号层只给人看，不参与鼠标交互：点选一律走 hitTest 的几何判定，
      // 否则点在记号上会"点不中下面那条线"。
      if (names[index] === 'marks') attrs['pointer-events'] = 'none';
      var group = svgNode('g', attrs);
      layers[names[index]] = group;
      svg.appendChild(group);
    }
    host.appendChild(svg);
    state.svg = svg;
    state.layers = layers;
  }

  /* ------------------------------------------------------------ 渲染入口 */

  /* 这一版权威状态是不是"过期的"，不能拿去覆盖更新的预测。

     客户端预测 + 服务端和解（Gabriel Gambetta《Fast-Paced Multiplayer》第二篇）：
     服务端状态永远是对的，但**不能拿比已经画过的那版更旧的状态去覆盖客户端已经预测到的
     更新状态** —— 那正是"突然一下被拽回去"的来源。我们这边有两个具体的来路：
       · 拖动中收到的状态，对应的是服务端还没处理完手指输入的那一刻；
       · apply 的响应之后还有一趟"整份回读"，两趟回读会并发，旧的最后到就把图往回画。
     松手之后一律照收（导入 / 清空本来就会带来更小的版本号，那是合法的）。 */
  function isStaleRender(plane) {
    if (!plane || typeof plane.version !== 'number') return false;
    if (!(state.slide && state.slide.drag)) return false;
    return typeof state.renderedVersion === 'number' && plane.version < state.renderedVersion;
  }

  function renderPlane(plane) {
    if (!plane || typeof plane !== 'object') return;
    if (isStaleRender(plane)) return;
    state.plane = plane;
    if (typeof plane.version === 'number') state.renderedVersion = plane.version;
    if (!state.view) state.view = viewFromPlane(plane);
    if (!state.svg) buildSvg();
    if (!state.svg) return;
    applyView();
    state.unit = 1 / scaleOf();
    rememberBox();
    // 服务端是按哪个"可见范围"采的样，记下来：视野走出这个范围（含它自己那份余量）
    // 就要重新同步一次（见 maybeSyncView）。
    var win = plane.window;
    var here = state.view || {};
    state.syncedView = (win && typeof win.xmin === 'number' && typeof win.xmax === 'number')
      ? { xmin: win.xmin, xmax: win.xmax,
          ymin: typeof win.ymin === 'number' ? win.ymin : here.ymin,
          ymax: typeof win.ymax === 'number' ? win.ymax : here.ymax } : null;
    drawGrid();
    // 拖动中由 reapplyPreview 直接按预览画（它自己会重画），省掉一次整图重建 ——
    // 每帧两三次整图重建正是主线程被占满、"一卡一卡"的来源之一。
    if (!reapplyPreview()) drawGeometry();
    if (!chromeFrozen()) refreshChrome();
  }

  /* 拖动期间右栏 / 信息区 / 版本提示一律不刷新。

     它们和画布是同一个 flex 列里的兄弟节点：内容一变高度就变，画布跟着被挤得变尺寸；
     画布一改尺寸，"数据 <-> 像素"的换算跟着变 —— 老师看到的是线段相对手指一会跟不上、
     一会超前，整个窗口还在抖（页面滚动条也跟着一会出现一会没有）。
     拖动期间只需要画布跟手，这些内容松手后刷一次就够（见 endPointer）。 */
  function chromeFrozen() {
    return !!(state.slide && state.slide.drag);
  }

  function refreshChrome() {
    if (!state.plane) return;
    renderInfo();
    renderAlgebra();
    var count = orderedObjects().length;
    setNote(count ? '共 ' + count + ' 个对象，第 ' + fmt(state.plane.version) + ' 版'
                  : '还没有对象：让 AI 在对话里建，或者先在右侧代数区看看。');
  }

  /* 记下这次渲染时的画布尺寸。

     字号/点半径都是"屏幕像素折算成数据单位"再写进 SVG 的，所以「渲染那一刻量到的
     尺寸」决定了它们最终多大。面板刚显示出来、布局还没落定时量到的高度可能是几十像素，
     折算出来的字就会变成几百像素高（无头自检里真出现过满屏巨字）。
     这里记住尺寸，下面的观察器一旦发现尺寸变了就整块重画一次。 */
  function rememberBox() {
    if (!state.svg) return;
    var rect = state.svg.getBoundingClientRect();
    state.renderedBox = { w: rect.width, h: rect.height };
  }

  /* 只重画网格：缩放平移时不用重建整个 DOM */
  function refreshViewOnly() {
    applyView();
    state.unit = 1 / scaleOf();
    drawGrid();
  }

  function redraw() {
    if (!state.plane) return;
    renderPlane(state.plane);
  }

  /* ------------------------------------------------------------ 选中与命中 */

  /* 命中容差，一律按**屏幕像素**量（再折成数据单位），缩放前后手感一样。
     14px 是按教室触摸屏定的：手指的落点误差大约 10-15px，而圆/线只有 1-2px 的描边，
     老师本来就不可能正好压在线上——「点一下圆放不上动点」就是这儿来的。
     这个值也是「8px 外还点得中」那条验收的余量。 */
  var HIT_PX = 14;
  /* 圆是个例外：空心圆看着是"一圈线"，但老师会点在圆**里面**。
     规则：圆周两侧 14px 以内算点到它；点在圆里面时放宽到离圆周 28px 以内；
     再往里就不算这个圆了——不然一个大圆会把里面的点、线、文字全吞掉，
     老师点圆心的时候反而什么也点不到（而且半径越大吞得越多）。 */
  var HIT_INNER_PX = 28;

  /* 圆的命中：纯函数，自检（test_webui.py 的 TestPlaneHitTest）就是抽它出去跑的。
     返回 distance（点到圆周的距离）和 limit（这一次允许的容差）。 */
  function circlePick(toCenter, radius, outer, inner) {
    return { distance: Math.abs(toCenter - radius),
             limit: toCenter < radius ? inner : outer };
  }

  /* 两个候选谁赢：点（tier 0）永远优先于线/圆（tier 1），同一档比距离。
     点本身就是个小目标，老师按下去多半是想点它，不能因为旁边 3px 有条线就选线。 */
  function betterHit(candidate, best) {
    if (!best) return true;
    if (candidate.tier !== best.tier) return candidate.tier < best.tier;
    return candidate.distance < best.distance;
  }

  /* 一个对象离这一点的距离 + 它自己的容差 + 优先级；够不着返回 null。 */
  function objectPick(obj, point, outer, inner) {
    var type = obj.type;
    if (type === 'point' || type === 'intersect') {
      var self = pointOf(obj);
      if (!self) return null;
      return { distance: Math.sqrt(Math.pow(self.x - point.x, 2) +
                                   Math.pow(self.y - point.y, 2)),
               limit: outer, tier: 0 };
    }
    if (type === 'circle') {
      var center = centerOf(obj);
      var radius = radiusOf(obj);
      if (!center || radius === null) return null;
      var toCenter = Math.sqrt(Math.pow(center.x - point.x, 2) +
                               Math.pow(center.y - point.y, 2));
      var picked = circlePick(toCenter, radius, outer, inner);
      picked.tier = 1;
      return picked;
    }
    var distance;
    if (type === 'polygon' || type === 'regular_polygon') {
      distance = distanceToLoop(point, polygonPoints(obj));
    } else {
      var ends = endpointsOf(obj);
      if (ends[0] && ends[1]) {
        distance = (type === 'line' || LINE_TYPES[type])
          ? distanceToSegment(point, ends[0], ends[1], false)   // 无限长：整条都能点中
          : distanceToSegment(point, ends[0], ends[1]);
      } else {
        distance = distanceToSegments(point, sampledSegments(obj));
      }
    }
    if (!isFinite(distance)) return null;
    return { distance: distance, limit: outer, tier: 1 };
  }

  /* 命中的判定：不在容差里就返回 null，老师那边会收到一句中文的"要怎么点"。 */
  /* 沿线拖要抓哪条线段：**按中点离鼠标最近**来挑（老师定的就近原则）。

     为什么不用"点到线段的垂距"：两条线段挨在一起、或者按在共用的端点附近时，
     垂距几乎一样大，挑中的常常不是老师想拖的那条；中点离得最近的那条才是他心里那条。
     先按常规命中范围（点到线段本身够近）收一批候选，再按中点距离排序；
     另外"按在中点附近、但离线本身稍远"也算数，否则抓一条短线段会很难按中。 */
  function segmentForSlide(point) {
    var unit = state.unit || 0.03;
    var outer = HIT_PX * unit;
    var midLimit = outer * 2;
    var list = orderedObjects();
    var best = null;
    var bestMid = Infinity;
    for (var index = 0; index < list.length; index++) {
      var obj = list[index];
      if (!obj || obj.type !== 'segment' || isHidden(obj)) continue;
      var ends = endpointsOf(obj);
      if (!ends[0] || !ends[1]) continue;
      var mid = { x: (ends[0].x + ends[1].x) / 2, y: (ends[0].y + ends[1].y) / 2 };
      var midDistance = Math.hypot(point.x - mid.x, point.y - mid.y);
      var pick = objectPick(obj, point, outer, outer);
      var nearBody = !!pick && isFinite(pick.distance) && pick.distance <= pick.limit;
      if (!nearBody && midDistance > midLimit) continue;
      if (midDistance < bestMid) {
        bestMid = midDistance;
        best = obj;
      }
    }
    return best;
  }

  function hitTest(point) {
    var unit = state.unit || 0.03;
    var outer = HIT_PX * unit;
    var inner = HIT_INNER_PX * unit;
    var list = orderedObjects();
    var best = null;
    var bestPick = null;
    for (var index = 0; index < list.length; index++) {
      var obj = list[index];
      if (!obj || isHidden(obj)) continue;
      var pick = objectPick(obj, point, outer, inner);
      if (!pick || !isFinite(pick.distance) || pick.distance > pick.limit) continue;
      if (betterHit(pick, bestPick)) {
        bestPick = pick;
        best = obj;
      }
    }
    return best;
  }

  /* 按顺序连成一条闭合折线的距离（多边形用） */
  function distanceToLoop(point, points) {
    if (!points || points.length < 2) return Infinity;
    var best = Infinity;
    for (var index = 0; index < points.length; index++) {
      var distance = distanceToSegment(point, points[index], points[(index + 1) % points.length]);
      if (distance < best) best = distance;
    }
    return best;
  }

  /* 点到线段的距离。clamped=false 时当成**无限长直线**算：投影参数不夹在 [0,1] 里，
     这样直线/垂线/切线伸到视野外的那些部分也点得中（画的时候本来就是延伸出去的）。 */
  function distanceToSegment(point, a, b, clamped) {
    var dx = b.x - a.x;
    var dy = b.y - a.y;
    var length2 = dx * dx + dy * dy;
    if (!(length2 > 0)) return Math.sqrt(Math.pow(point.x - a.x, 2) + Math.pow(point.y - a.y, 2));
    var t = ((point.x - a.x) * dx + (point.y - a.y) * dy) / length2;
    if (clamped !== false) t = clamp(t, 0, 1);
    var px = a.x + t * dx;
    var py = a.y + t * dy;
    return Math.sqrt(Math.pow(point.x - px, 2) + Math.pow(point.y - py, 2));
  }

  function distanceToSegments(point, segments) {
    var best = Infinity;
    for (var index = 0; index < segments.length; index++) {
      var list = segments[index];
      for (var position = 0; position + 1 < list.length; position++) {
        var distance = distanceToSegment(point, list[position], list[position + 1]);
        if (distance < best) best = distance;
      }
    }
    return best;
  }

  function viewBoxMetrics() {
    var rect = state.svg.getBoundingClientRect();
    var view = state.svg.viewBox.baseVal;
    var boxW = view && view.width ? view.width : 1;
    var boxH = view && view.height ? view.height : 1;
    // preserveAspectRatio="xMidYMid meet"：容器比例和 viewBox 不一样时会等比缩放并居中留边。
    // 不算这个偏移的话，明明按在点上程序却以为离得很远（作图面板真踩过）。
    var scale = Math.min(rect.width / boxW, rect.height / boxH) || 1;
    return { left: rect.left, top: rect.top, scale: scale, boxW: boxW, boxH: boxH,
             padX: (rect.width - boxW * scale) / 2, padY: (rect.height - boxH * scale) / 2 };
  }

  function clientToData(clientX, clientY) {
    var metrics = viewBoxMetrics();
    var view = state.svg.viewBox.baseVal;
    return { x: (clientX - metrics.left - metrics.padX) / metrics.scale + view.x,
             y: -((clientY - metrics.top - metrics.padY) / metrics.scale + view.y) };
  }

  function selectObject(id) {
    state.selected = id || null;
    redraw();
  }

  /* ------------------------------------------------------------ 信息框 / 代数区 */

  function infoCard(title, rows) {
    var card = node('div', 'info-card');
    card.appendChild(node('h4', null, title));
    for (var index = 0; index < rows.length; index++) {
      var row = node('div', 'info-row');
      row.appendChild(node('span', null, rows[index][0]));
      var value = node('span', null, rows[index][1]);
      row.appendChild(value);
      card.appendChild(row);
    }
    return card;
  }

  function renderInfo() {
    var host = $('#planeInfo');
    clear(host);
    var obj = state.selected ? objById(state.selected) : null;
    if (!state.plane) {
      host.appendChild(node('div', 'info-card', state.serverError || '正在读取平面状态…'));
      return;
    }
    if (obj) {
      var rows = [['类型', TYPE_CN[obj.type] || obj.type],
                  ['定义', defineText(obj)],
                  ['当前值', valueText(obj)]];
      if (obj.parents && obj.parents.length) rows.push(['依赖', obj.parents.join('、')]);
      if (obj.children && obj.children.length) rows.push(['被依赖', obj.children.join('、')]);
      if (obj.meta && obj.meta.source) rows.push(['来源', obj.meta.source === 'agent' ? 'AI' : '本人']);
      host.appendChild(infoCard(obj.label || obj.id, rows));
      var hint = node('div', 'info-card');
      hint.appendChild(node('h4', null, '能做什么'));
      hint.appendChild(node('div', 'info-hint', dragHint(obj)));
      var spec = editSpec(obj);
      hint.appendChild(node('div', 'info-hint', (spec.fields && spec.fields.length)
        ? '要改它的' + fieldsText(spec.fields) + '：看右栏高亮的这一行，填好按回车生效，按 Esc 不改。'
        : '这一项在右栏里没有可以直接改的字段。'));
      host.appendChild(hint);
    } else {
      var tips = node('div', 'info-card');
      tips.appendChild(node('h4', null, '用法'));
      tips.appendChild(node('div', 'info-hint',
        '点一下图上的对象可以选中它（这里会显示它的定义和当前值）；按住自由点拖动，' +
        '中点和交点会跟着动。滚轮缩放，拖空白处平移。' +
        '选中之后右栏那一行会变成可以改的表单（点的坐标、动点的参数、函数和方程的表达式、圆的半径），' +
        '改完按回车生效，按 Esc 不改。'));
      host.appendChild(tips);
    }
    if (state.warnings.length) {
      var warn = node('div', 'plane-warn', state.warnings.join('　'));
      host.appendChild(warn);
    }
    if (state.serverError) host.appendChild(node('div', 'plane-warn', state.serverError));
  }

  function dragHint(obj) {
    if (isIntersection(obj)) {
      return '这是交点：它的位置由父对象决定，不能单独拖动。' +
             '拖一拖它的某个父对象，它就会跟着动。';
    }
    if (obj.type !== 'point') return '这类对象由它的父对象决定，拖动父对象即可改变它。';
    var def = obj.definition || {};
    if (def.on) {
      var host = objById(def.on);
      var name = host ? (host.label || host.id) : def.on;
      var type = host ? host.type : '';
      if (gliderParameter(obj, { x: 0, y: 0 }, true)) {
        if (ARC_CURVES[type]) {
          return '这是「' + (TYPE_CN[type] || type) + ' ' + name + '」上的动点：拖动时它顺着曲线滑，' +
                 '位置按"鼠标到曲线最近的那一段"重新算（不是最近的网格点）。';
        }
        return '这是「' + (TYPE_CN[type] || type) + ' ' + name +
               '」上的动点：拖动时它只会沿着它滑动，位置由后端重新算。';
      }
      return '这个点挂在 ' + name + ' 上，但网页端还不知道怎么把鼠标位置换算成它的参数，' +
             '所以先不给拖（免得发出错误的指令）。';
    }
    return '这是自由点：可以直接拖动，和它相关的线段、圆、交点都会跟着更新。';
  }

  /* 老师正在输入框里打字时**不要重建右栏**：重建会把输入框整个换掉，
     光标、已选的文字、输入法的组字状态全丢（表现成"打一个字就跳走"）。
     提交/取消的时候我们会先 blur，所以正常流程不受影响；点到画布也会先 blur。 */
  function editingInput() {
    var active = document.activeElement;
    if (!active || !active.getAttribute) return null;
    return active.getAttribute('data-plane-field') ? active : null;
  }

  function blurEditingInput() {
    var input = editingInput();
    if (input && input.blur) input.blur();
  }

  function editFormOf(input) {
    var node = input;
    while (node && node.getAttribute) {
      if (node.getAttribute('data-plane-edit')) return node;
      node = node.parentNode;
    }
    return null;
  }

  function renderAlgebra() {
    if (editingInput()) return;
    var list = $('#planeList');
    clear(list);
    state.rows = {};
    var objects = orderedObjects();
    $('#planeCount').textContent = objects.length ? objects.length + ' 个' : '';
    if (!objects.length) {
      list.appendChild(node('div', 'plane-empty',
        state.serverError || '还没有对象。让 AI 在对话里说「画一个圆和一条割线」，这里就会列出来。'));
      return;
    }
    for (var index = 0; index < objects.length; index++) {
      list.appendChild(buildRow(objects[index]));
    }
  }

  function buildRow(obj) {
    var row = node('div', 'plane-row' + (state.selected === obj.id ? ' on' : '') +
                          (isHidden(obj) ? ' dim' : ''));
    row.setAttribute('data-plane-row', obj.id);
    var main = node('div', 'plane-row-main');
    var head = node('div', 'plane-row-id');
    head.appendChild(document.createTextNode(obj.label || obj.id));
    head.appendChild(node('span', 'plane-row-tag', TYPE_CN[obj.type] || obj.type));
    main.appendChild(head);
    main.appendChild(node('div', 'plane-row-def', defineText(obj)));
    main.appendChild(node('div', 'plane-row-val', valueText(obj)));
    // 选中的那一行就地变成可改的表单：老师点的是这一行，改的地方就在这一行
    if (state.selected === obj.id) {
      try {
        main.appendChild(buildEditForm(obj));
      } catch (error) {
        main.appendChild(node('div', 'plane-edit-note',
          '这一项的表单没建出来：' + (error && error.message ? error.message : String(error))));
      }
    }
    row.appendChild(main);

    var acts = node('div', 'plane-row-acts');
    var toggle = node('button', 'plane-mini', isHidden(obj) ? '显示' : '隐藏');
    toggle.setAttribute('title', '只影响这一张图上的显示，不改对象本身');
    toggle.onclick = function (event) {
      event.stopPropagation();
      setVisibility(obj.id, isHidden(obj));
    };
    acts.appendChild(toggle);
    var remove = node('button', 'plane-mini', '删除');
    remove.setAttribute('title', '删掉它；依赖它的对象会被一起删掉');
    remove.onclick = function (event) {
      event.stopPropagation();
      deleteObject(obj.id);
    };
    acts.appendChild(remove);
    row.appendChild(acts);

    row.onclick = function () {
      if (state.selected === obj.id) selectObject(null);
      else { selectObject(obj.id); row.scrollIntoView && row.scrollIntoView(false); }
    };
    state.rows[obj.id] = row;
    return row;
  }

  /* ------------------------------------------------- 右栏直接改对象（代数区可编辑） */
  /* 老师要的是"选中就能改坐标、改方程"。
     三条纪律：
       1. 只发 update 一条运算（sendOps），字段名就是架构文档 §4 冻结的那些，不另造；
       2. 改完**按服务端返回的状态重画**，前端不做数学、也不先改本地值
          （乐观更新迟早会和后端算出来的值对不上）；
       3. 服务端拒了、或者收下了却算不出来（比如表达式写错），都不许留一个坏对象。 */

  function coordsText(point) {
    return point ? '(' + fmt(point.x) + ', ' + fmt(point.y) + ')' : '还没算出来';
  }

  function endpointText(id) {
    if (!id) return '（没有）';
    var point = pointOf(objById(id));
    return String(id) + ' ' + coordsText(point);
  }

  function typeName(obj) {
    return TYPE_CN[(obj && obj.type) || ''] || ((obj && obj.type) || '对象');
  }

  function fieldsText(fields) {
    var labels = [];
    for (var index = 0; index < fields.length; index++) labels.push(fields[index].label);
    return labels.join('、');
  }

  /* 每种类型能改什么。没有可改字段的**必须说清楚为什么、以及该怎么办**，
     不能只给一个空框子——老师会以为界面坏了。 */
  function editSpec(obj) {
    var def = (obj && obj.definition) || {};
    var type = (obj && obj.type) || '';
    if (type === 'point' || type === 'intersect') {
      if (isIntersection(obj)) {
        return { note: '交点：位置由它的两个父对象算出来，改不了。拖它的父对象，它就跟着动。' };
      }
      if (def.on) {
        return {
          fields: [{ key: 'parameter', label: '参数', kind: 'range', min: 0, max: 1, step: 0.001 },
                   { key: 'x', label: 'x', kind: 'number' },
                   { key: 'y', label: 'y', kind: 'number' }],
          note: '这是 ' + def.on + ' 上的动点：拉滑杆按参数挪，也可以直接填坐标 ——' +
                '填的坐标不在线上时，就取线上离它最近的那个点。'
        };
      }
      if (typeof def.x === 'number' && typeof def.y === 'number') {
        return { fields: [{ key: 'x', label: 'x', kind: 'number' },
                          { key: 'y', label: 'y', kind: 'number' }] };
      }
      return { note: '这个点的定义里没有能直接改的坐标，改它的父对象就行。' };
    }
    if (type === 'function') {
      // 起点/终点留空 = 自动区间：服务端按可见范围（再多采几屏）采样，曲线就是"无限"的
      var pinned = typeof def.xmin === 'number' && typeof def.xmax === 'number';
      return { fields: [{ key: 'expr', label: '表达式', kind: 'expr' },
                        { key: 'xmin', label: '起点', kind: 'number', optional: true },
                        { key: 'xmax', label: '终点', kind: 'number', optional: true }],
               note: '只写等号右边那串，例如 x^2+sin(x)。' +
                     (pinned
                       ? '把起点 / 终点清空就回到自动区间：曲线跟着视野画，屏幕外也多画几屏。'
                       : '现在是自动区间：曲线跟着视野走（屏幕外也画几屏）；' +
                         '要钉死范围就填起点 / 终点。') };
    }
    if (type === 'implicit' || type === 'polar') {
      return { fields: [{ key: 'expr', label: '表达式', kind: 'expr' }],
               note: type === 'implicit' ? '写成 F(x,y) = 0 里的 F，例如 x^2+y^2-1。'
                                         : '写成 r = 右边那串，例如 1+cos(t)。' };
    }
    if (type === 'parametric') {
      return { fields: [{ key: 'x', label: 'x(t)', kind: 'expr' },
                        { key: 'y', label: 'y(t)', kind: 'expr' }] };
    }
    if (type === 'inequality') {
      if (def.exprs) {
        return { note: '这条区域由好几条不等式拼起来（' + String(def.exprs) + '），' +
                       '右栏里先不给改：删掉重新画一次更快。' };
      }
      return { fields: [{ key: 'expr', label: '不等式', kind: 'expr' }],
               note: '带不等号的整句，例如 y > x^2。' };
    }
    if (type === 'circle') {
      if (typeof def.radius === 'number') {
        return { fields: [{ key: 'radius', label: '半径', kind: 'number', positive: true }] };
      }
      var readouts = [];
      if (def.center !== undefined) readouts.push(['圆心', endpointText(def.center)]);
      if (typeof def.through === 'string') {
        readouts.push(['圆上点', endpointText(def.through)]);
      } else if (def.through && def.through.length) {
        for (var index = 0; index < def.through.length; index++) {
          readouts.push(['过点 ' + (index + 1), endpointText(def.through[index])]);
        }
      }
      readouts.push(['算出来的半径', fmt(radiusOf(obj))]);
      return { readouts: readouts,
               note: '这个圆的半径是算出来的（由上面的点决定），改不了：' +
                     '拖圆上那个点，圆就跟着变大变小。' };
    }
    if (type === 'segment' || type === 'ray' || type === 'line' || type === 'vector') {
      return { readouts: [['起点', endpointText(def.start)], ['终点', endpointText(def.end)]],
               note: '两端由 ' + fmt(def.start) + '、' + fmt(def.end) + ' 决定，这里没有可以改的字段：' +
                     '拖那两个点就能改这条线。' };
    }
    return { note: '这种对象（' + typeName(obj) + '）的位置由它的父对象决定，右栏里没有可以直接改的字段：' +
                   '改它的父对象就行。' };
  }

  function fieldValue(obj, key) {
    var def = (obj && obj.definition) || {};
    var value = def[key];
    // 动点的定义里没有 x/y（只有 on + parameter），但右栏那两个框要显示它现在的坐标：
    // 用服务端算出来的当前值填进去，老师改哪个就按哪个换算。
    if (value === undefined && (key === 'x' || key === 'y') && def.on) {
      var spot = pointOf(obj);
      return spot ? spot[key] : '';
    }
    return value === undefined || value === null ? '' : value;
  }

  /* 只认十进制数字（连带符号、小数、科学计数法）。空、abc、1,2 一律 null。 */
  function readNumber(raw) {
    var text = String(raw === undefined || raw === null ? '' : raw).trim();
    if (!text) return null;
    var parsed = Number(text);
    return isFinite(parsed) ? parsed : null;
  }

  function cloneDefinition(definition) {
    // 定义里只有数字/字符串/数组，JSON 转一圈就够 update 回写用了
    try { return JSON.parse(JSON.stringify(definition || {})); }
    catch (error) { return {}; }
  }

  function buildEditForm(obj) {
    var spec = editSpec(obj);
    var box = node('div', 'plane-edit');
    box.setAttribute('data-plane-edit', obj.id);
    // 点输入框不能把这一行的选中给切掉（行的 onclick 是"再点一下取消选中"）
    box.onclick = function (event) { event.stopPropagation(); };
    var fields = spec.fields || [];
    var index;
    for (index = 0; index < fields.length; index++) {
      var field = fields[index];
      var line = node('div', 'plane-edit-row');
      line.appendChild(node('label', 'plane-edit-label', field.label));
      var input = document.createElement('input');
      input.className = 'plane-edit-input';
      input.setAttribute('data-plane-field', field.key);
      input.setAttribute('spellcheck', 'false');
      input.setAttribute('autocomplete', 'off');
      if (field.kind === 'range') {
        input.setAttribute('type', 'range');
        input.setAttribute('min', field.min);
        input.setAttribute('max', field.max);
        input.setAttribute('step', field.step);
      } else {
        input.setAttribute('type', field.kind === 'number' ? 'number' : 'text');
        if (field.kind === 'number') input.setAttribute('step', 'any');
      }
      input.value = String(fieldValue(obj, field.key));
      input.addEventListener('keydown', function (event) { onEditKey(event, obj); });
      if (field.kind === 'range') {
        // 滑杆拖完（松手）才提交：拖动过程中一直提交会把 DOM 重建到手指底下
        input.addEventListener('change', function () { commitForm(obj); });
      }
      line.appendChild(input);
      box.appendChild(line);
    }
    var readouts = spec.readouts || [];
    for (index = 0; index < readouts.length; index++) {
      var readout = node('div', 'plane-edit-ro');
      readout.appendChild(node('span', 'plane-edit-rolabel', readouts[index][0]));
      readout.appendChild(node('span', 'plane-edit-rovalue', String(readouts[index][1])));
      box.appendChild(readout);
    }
    if (spec.note) box.appendChild(node('div', 'plane-edit-note', spec.note));
    if (fields.length) {
      box.appendChild(node('div', 'plane-edit-tip', '改完按回车生效；按 Esc 或者点到别处就不改'));
    }
    return box;
  }

  function onEditKey(event, obj) {
    if (event.key === 'Enter') {
      event.preventDefault();
      event.stopPropagation();
      commitForm(obj);
      return;
    }
    if (event.key === 'Escape') {
      // Esc = 不改，回到服务端的值。一定要拦住这个 Esc：
      // 全局那个 Esc 是"关掉整个平面面板"，老师想取消一个输入框却把面板关了会懵。
      event.preventDefault();
      event.stopPropagation();
      if (event.stopImmediatePropagation) event.stopImmediatePropagation();
      blurEditingInput();
      showStatus('已取消这一处改动，保持原样', 'done', 3000);
      redraw();
    }
  }

  function commitForm(obj) {
    var row = state.rows[obj.id];
    var form = row ? row.querySelector('.plane-edit') : null;
    if (!form) return;
    var spec = editSpec(obj);
    var fields = spec.fields || [];
    var patch = {};
    var changed = 0;
    var index;
    for (index = 0; index < fields.length; index++) {
      var field = fields[index];
      var input = form.querySelector('input[data-plane-field="' + field.key + '"]');
      if (!input) continue;
      var raw = String(input.value === undefined || input.value === null ? '' : input.value).trim();
      if (field.kind === 'number' || field.kind === 'range') {
        if (field.optional && !raw) {
          // 留空 = 把这个字段删掉（函数的起点/终点留空就是回到"自动区间"）
          if (fieldValue(obj, field.key) !== '') { patch[field.key] = null; changed += 1; }
          continue;
        }
        var parsed = readNumber(raw);
        if (parsed === null) {
          showStatus('「' + field.label + '」要填一个数字，比如 2 或 -1.5；这次没改动。', 'warn', 9000);
          if (input.focus) input.focus();
          return;
        }
        if (field.positive && !(parsed > 0)) {
          showStatus('「' + field.label + '」要大于 0，比如 3；这次没改动。', 'warn', 9000);
          if (input.focus) input.focus();
          return;
        }
        raw = parsed;
      } else if (!raw) {
        showStatus('「' + field.label + '」不能空着，例如 x^2+sin(x)；这次没改动。', 'warn', 9000);
        if (input.focus) input.focus();
        return;
      }
      if (String(raw) !== String(fieldValue(obj, field.key))) {
        patch[field.key] = raw;
        changed += 1;
      }
    }
    if (!changed) {
      showStatus('没有改动', 'done', 2500);
      return;
    }
    // 区间要一起合理，服务端不替老师检查这个（xmin > xmax 它照收，曲线就画没了）
    if (patch.xmin !== undefined || patch.xmax !== undefined) {
      var xmin = patch.xmin !== undefined ? patch.xmin : readNumber(fieldValue(obj, 'xmin'));
      var xmax = patch.xmax !== undefined ? patch.xmax : readNumber(fieldValue(obj, 'xmax'));
      if (xmin === null || xmax === null || !(xmin < xmax)) {
        showStatus('函数的起点要小于终点（现在起点 ' + fmt(xmin) + '、终点 ' + fmt(xmax) +
                   '）；这次没改动。', 'warn', 9000);
        return;
      }
    }
    // 动点：老师填的是坐标，服务端要的是参数 —— 在这里换算一次，走的是"就近"规则
    // （坐标不在线上时，取线上离它最近的那个点）。只填了 x 或只填了 y 就用现值补另一个。
    if (obj.type === 'point' && (obj.definition || {}).on &&
        (patch.x !== undefined || patch.y !== undefined)) {
      var now = pointOf(obj) || { x: 0, y: 0 };
      var wanted = { x: patch.x !== undefined ? patch.x : now.x,
                     y: patch.y !== undefined ? patch.y : now.y };
      var near = gliderParameter(obj, wanted);
      if (!near) {
        showStatus('在 ' + (obj.definition.on) + ' 上取不到离 (' + fmt(wanted.x) + ', ' +
                   fmt(wanted.y) + ') 最近的点：这个点的父对象现在没有能用的值' +
                   '（或者这类父对象还不支持）。这次没改动。', 'warn', 11000);
        return;
      }
      patch = { on: obj.definition.on, parameter: round4(near.parameter) };
    }
    blurEditingInput();
    applyEdit(obj, patch);
  }

  /* 服务端收下了、但算不出这个对象（曲线会把原因写在 value.error 里）。 */
  function valueBroken(obj) {
    return !!(obj && obj.value && obj.value.error);
  }

  function brokenMessage(obj) {
    var error = obj && obj.value ? obj.value.error : null;
    var message = error && (error.message || error.code);
    return message ? String(message) : ('服务端算不出 ' + ((obj && obj.id) || '这个对象'));
  }

  function applyEdit(obj, patch) {
    var previous = cloneDefinition(obj.definition);
    var name = obj.label || obj.id;
    showStatus('正在改 ' + name + '…', '', 0);
    sendOps([{ op: 'update', id: obj.id, definition: patch }]).then(function () {
      // apply 的返回值里**没有 state**（只有 warnings 和版本号），本地那份还是旧的。
      // 所以必须重新读一次，拿服务端真正算出来的值来判断，绝不猜。
      return loadPlane().then(function () {
        var fresh = objById(obj.id);
        if (fresh && valueBroken(fresh)) {
          revertEdit(obj.id, previous, '这个改动服务端算不出来：' + brokenMessage(fresh));
          return;
        }
        showStatus('已改 ' + name, 'done', 3000);
      });
    })['catch'](function (error) {
      // 服务端没接受（整批回滚）＝对象没变，实话实说
      showStatus('没改成：' + error.message, 'err', 9000);
    });
  }

  function revertEdit(id, definition, message) {
    return sendOps([{ op: 'update', id: id, definition: definition }]).then(function () {
      showStatus(message + '（已经改回原来的写法）', 'err', 12000);
    }, function (error) {
      showStatus(message + '；而且改回原样也失败了：' + error.message, 'err', 12000);
    });
  }

  /* ------------------------------------------------------------ 改状态 */

  function noteResult(result) {
    if (!result) return;
    var bits = [];
    if (result.created && result.created.length) bits.push('新建 ' + result.created.join('、'));
    if (result.updated && result.updated.length) bits.push('更新 ' + result.updated.join('、'));
    if (result.removed && result.removed.length) bits.push('删除 ' + result.removed.join('、'));
    if (result.explanation) bits.push(String(result.explanation));
    var warnings = result.warnings || [];
    if (warnings.length) {
      setNote(bits.join('；') + '　注意：' + warningsText(warnings));
    } else if (bits.length) {
      setNote(bits.join('；'));
    }
  }

  function warningsText(warnings) {
    var out = [];
    for (var index = 0; index < warnings.length; index++) {
      var item = warnings[index];
      if (item && typeof item === 'object') out.push(item.message || item.code || JSON.stringify(item));
      else out.push(String(item));
    }
    return out.join('　');
  }

  /* 拖动中每隔这么久才跟服务端"整份对齐"一次。

     一次对齐 = 一趟 20KB 的状态回读 + JSON 解析 + 整图重建 + 右栏重建，全在主线程上
     同步跑十几毫秒；拖动时每个 commit 都来一次的话，指针事件全排在它后面 —— 表现就是
     "一卡一卡、跟不紧鼠标"。其余时间完全靠本地预览渲染（那一帧就是手指的位置），
     每 250ms 对齐一次只是为了把预览覆盖不到的派生对象（度量等）拉回真值。 */
  var DRAG_RENDER_MS = 250;

  /* 需要整份对齐时的统一入口：拖动中限流并记一笔"欠着"，平时立刻回读。 */
  function refreshForDrag() {
    var drag = state.slide && state.slide.drag;
    if (!drag) { loadPlane(); return; }
    var now = Date.now();
    if (now - (drag.renderedAt || 0) < DRAG_RENDER_MS) { drag.owed = true; return; }
    drag.renderedAt = now;
    drag.owed = false;
    loadPlane();
  }

  function applyResult(result) {
    if (!result) return;
    if (result.state) renderPlane(result.state);
    else if (typeof result.stateVersion === 'number' && state.plane &&
             result.stateVersion !== state.plane.version) {
      // 冻结形状里没带 state：按版本号重新拉一次，绝不用本地猜测顶替
      refreshForDrag();
    }
    noteResult(result);   // 放在 renderPlane 后面：说明性文字优先于"共 N 个对象"
    var warnings = result.warnings || [];
    if (warnings.length) showStatus(warningsText(warnings), 'warn', 6000);
  }

  /* ---------------------------------------------------------------- 画图工具 */
  /* 老师要的是"手动画图、亲手拖点"。这一排工具就是一个小状态机：
     选中工具 -> 在画布上点几下 -> 攒够参数就发一条 create 出去（走已有的 sendOps）。
     点已有对象上的点会**复用它**，不会在原地叠一个重复的点（GeoGebra 就是这个手感）。 */

  var TOOL_HINTS = {
    select: '拖动自由点会带着后面的图形一起动；拖背景平移，滚轮缩放',
    point: '在空白处点一下放一个点',
    segment: '点两个点画线段',
    line: '点两个点画直线',
    ray: '点两个点画射线',
    circle: '先点圆心，再点圆上一点',
    midpoint: '点两个点，连成线段并标出中点',
    perp: '先点一个点，再点一条线，作过该点的垂线',
    pbisector: '点两个点：先连出线段，再作它的垂直平分线；也可以直接点一条现成的线段',
    parallel: '先点一个点，再点一条线，作过该点的平行线',
    abisector: '依次点三个点：角的一边上一点、顶点、另一边上一点（顶点是中间那个点）',
    circumcircle: '依次点三个点，作过这三点的外接圆（三点共线就画不出来）',
    centroid: '依次点三角形的三个顶点，求重心（三条中线的交点）',
    incenter: '依次点三角形的三个顶点，求内心（三条角平分线的交点，也是内切圆的圆心）',
    orthocenter: '依次点三角形的三个顶点，求垂心（三条高线的交点）',
    intersect: '先后点两个对象求交点；有两个交点时，取离你点的那一下最近的那个',
    area: '依次点几个点，点完按「完成」：围成的多边形面积算在代数区那一行（静止精确、拖动小数）',
    angle: '先点一条直线/线段，再点另一条：算两条线所成的角（锐角），给度数、sin、cos',
    poly: '依次点多边形顶点，点「完成」收尾',
    trace: '选中一个点再点这里就行（也可以点它之后去画布上点那个点）；之后拖它走一圈，轨迹会留在屏幕上',
    rotate: '先点一个定点（旋转中心），再点一条直线或线段，然后按住拖动：它就绕定点转',
    slide: '沿线拖（保线段长度）：先点一条轨道（直线/线段/圆/弧/函数曲线），再按住一条线段拖动；' +
           '轨道是曲线时，另一端按"到这一端距离不变"解出来 —— 这就是定长弦',
    slidex: '沿线拖（保 x 方向长度）：拖的时候两端的 Δx 不变（曲线轨道上按这个解另一端）',
    slidey: '沿线拖（保 y 方向长度）：拖的时候两端的 Δy 不变（曲线轨道上按这个解另一端）',
    motion: '点一个圆 / 线段 / 直线 / 弧 / 函数曲线 / 方程曲线，就在它上面放一个动点（之后可以顺着它滑）',
    tangent: '点一个在曲线上的点（动点、交点、自己放的点都行），就在那儿画切线；认不出哪条曲线就再点一条',
    'delete': '点一个对象把它删掉'
  };

  function setTool(name) {
    state.tool = name || 'select';
    state.pending = [];
    state.rotate = null;   // 换工具 = 这次旋转作废（再点一次「旋转」也是重新开始）
    state.slide = null;    // 沿线拖也一样：换工具就重新挑轨道
    var buttons = document.querySelectorAll('.plane-tool');
    for (var index = 0; index < buttons.length; index++) {
      var button = buttons[index];
      var on = button.getAttribute('data-plane') === state.tool;
      if (on) button.classList.add('on'); else button.classList.remove('on');
    }
    updateHint();
  }

  function updateHint(extra) {
    var label = TOOL_HINTS[state.tool] || '';
    var hint = $('#planeHint');
    if (!hint) return;
    var count = (state.pending || []).length;
    hint.textContent = extra || (count ? (label + ' · 已选 ' + count + ' 个') : label);
  }

  /* 切线能切在谁身上：函数 / 隐式 / 参数 / 极坐标曲线，以及圆。
     和服务端 _value_constructed_line 的两条分支一一对应（曲线用导数，圆用半径的垂线）。 */
  var TANGENT_HOSTS = { function: 1, implicit: 1, parametric: 1, polar: 1, circle: 1 };

  /* 这个点踩在哪条曲线（或圆）上：先看它的定义（动点挂在谁身上），
     否则拿坐标去比（交点、自己放的点都走这条）。认不出来返回 null —— 不猜。 */
  function tangentHostOf(point) {
    var def = point.definition || {};
    if (def.on) {
      var host = objById(def.on);
      if (host && TANGENT_HOSTS[host.type]) return def.on;
    }
    var spot = pointOf(point);
    if (!spot) return null;
    var outer = HIT_PX * (state.unit || 0.03);
    var list = orderedObjects();
    var found = null;
    for (var index = 0; index < list.length; index++) {
      var obj = list[index];
      if (!obj || !TANGENT_HOSTS[obj.type]) continue;
      var gap = null;
      if (obj.type === 'circle') {
        var center = centerOf(obj);
        var radius = radiusOf(obj);
        if (center && radius !== null) {
          gap = Math.abs(Math.sqrt(Math.pow(center.x - spot.x, 2) +
                                   Math.pow(center.y - spot.y, 2)) - radius);
        }
      } else {
        var segments = sampledSegments(obj);
        if (segments.length) gap = distanceToSegments(spot, segments);
      }
      if (gap === null || !isFinite(gap) || gap > outer) continue;
      if (found) return null;      // 好几条都过它 —— 让老师点一条，猜不得
      found = obj.id;
    }
    return found;
  }

  function makeTangent(hostId, pointId) {
    sendOpsQuiet([{ op: 'create', type: 'tangent', id: nextId('t', []),
                    definition: { to: hostId, at: pointId } }]).then(function (result) {
      if (result && result.ok) {
        showStatus('已画出 ' + pointId + ' 处的切线（切 ' + hostId + '）', 'done', 5000);
      }
    });
  }

  /* 给一个点开追踪。老师最自然的用法是"先选中那个点，再点「追踪」"，
     所以两条路都走这里：① 选中点 + 点工具按钮；② 点工具按钮 + 点画布上的点。 */
  function traceObject(id) {
    var obj = objById(id);
    if (!obj) return;
    if (obj.type !== 'point') {
      showStatus((TYPE_CN[obj.type] || obj.type) + ' 追踪不了：追踪只能开在一个点上' +
                 '（先选一个点，或者用「交点」做出一个点来）', 'warn', 8000);
      return;
    }
    sendOpsQuiet([{ op: 'create', type: 'trace', id: nextId('t', []),
                    definition: { of: id } }]).then(function (result) {
      // 等服务端真的收下了再说"已开"：它可能根本不认这个对象
      if (result && result.ok) {
        showStatus('已给 ' + id + ' 开追踪：拖它走一圈，轨迹会留在屏幕上', 'done', 7000);
      }
    });
  }

  /* A、B、C…Z，然后 A1、B1…；已被占用的名字跳过 */
  function nextId(prefix, used) {
    var alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ';
    var objects = (state.plane && state.plane.objects) || {};
    function taken(name) {
      if (objects[name]) return true;
      return (used || []).indexOf(name) >= 0;
    }
    for (var index = 0; index < alphabet.length; index++) {
      if (!taken(prefix + alphabet.charAt(index))) return prefix + alphabet.charAt(index);
    }
    for (var suffix = 1; suffix < 200; suffix++) {
      for (var again = 0; again < alphabet.length; again++) {
        var name = prefix + alphabet.charAt(again) + suffix;
        if (!taken(name)) return name;
      }
    }
    return prefix + 'X' + Date.now();
  }

  function sendOpsQuiet(ops) {
    return sendOps(ops)['catch'](function (error) {
      var message = '这一步没做成：' + error.message;
      // 服务端是**整批原子**的：它回滚之后本地那份状态可能已经跟服务端不一样了
      // （真踩过：回滚会把所有对象算出来的值一起清掉）。所以这里必须重新读一次，
      // 绝不让界面继续显示服务端已经没有的东西。读回来之后再报错，别被 hideStatus 盖掉。
      return loadPlane().then(function () { showStatus(message, 'err', 9000); },
                              function () { showStatus(message, 'err', 9000); });
    });
  }

  /* 点画布：要么复用已有的点，要么在点的位置新建一个 */
  function pickOrMakePoint(hit, at) {
    if (hit && hit.type === 'point') return hit.id;
    var name = nextId('', (state.pending || []).slice());
    // 落点取整到 1 位小数（W7Snap.value）；整数磁吸那是拖动时的手感，落点不该吸
    sendOpsQuiet([{ op: 'create', type: 'point', id: name,
                    definition: { x: W7Snap.value(at.x), y: W7Snap.value(at.y) } }]);
    return name;
  }

  function twoPoints(tool, hit, at) {
    var pending = state.pending || (state.pending = []);
    var id = pickOrMakePoint(hit, at);
    if (pending.indexOf(id) < 0) pending.push(id);
    if (pending.length < 2) { updateHint(); return; }
    var first = pending[0];
    var second = pending[1];
    if (first === second) {
      showStatus('两个点不能是同一个', 'warn', 5000);
      state.pending = [];
      updateHint();
      return;
    }
    var ops = [];
    if (tool === 'midpoint') {
      var segment = nextId('s', []);
      ops.push({ op: 'create', type: 'segment', id: segment,
                 definition: { start: first, end: second } });
      // 中点按冻结文档的等价构造：点 = 线段上参数 0.5 的位置（不造 midpoint 类型）
      ops.push({ op: 'create', type: 'point', id: nextId('M', [segment]),
                 definition: { on: segment, parameter: 0.5 } });
    } else {
      ops.push({ op: 'create', type: tool,
                 id: nextId(tool === 'segment' ? 's' : (tool === 'ray' ? 'r' : 'l'), []),
                 definition: { start: first, end: second } });
    }
    state.pending = [];
    sendOpsQuiet(ops);
    updateHint();
  }

  /* 三点工具（角平分线 / 外接圆 / 重心 / 内心 / 垂心）共用的收集器。

     五种工具都是"依次点三个点"：点已有对象上的点会复用它，点空白处就先落一个点；
     攒够三个互不相同的点才发一条 create 出去（服务端整批原子，被拒绝会整批回滚并重读）。 */
  function threePoints(tool, hit, at) {
    var pending = state.pending || (state.pending = []);
    var id = pickOrMakePoint(hit, at);
    if (pending.indexOf(id) >= 0) {
      showStatus('这个点已经点过了：' + (TYPE_CN[tool] || tool) + '要三个互不相同的点', 'warn', 6000);
      updateHint();
      return;
    }
    pending.push(id);
    if (pending.length < 3) {
      updateHint('已选 ' + pending.length + ' 个点，还要点 ' + (3 - pending.length) + ' 个');
      return;
    }
    var first = pending[0];
    var second = pending[1];
    var third = pending[2];
    state.pending = [];
    var ops;
    if (tool === 'abisector') {
      // 顶点在中间那个点（服务端 angle_bisector 的 b 就是顶点）
      ops = [{ op: 'create', type: 'angle_bisector', id: nextId('ab', []),
               definition: { a: first, b: second, c: third } }];
    } else if (tool === 'circumcircle') {
      // 三点外接圆：circle 的 through 直接收一个点表（服务端走 _circumcircle）
      ops = [{ op: 'create', type: 'circle', id: nextId('c', []),
               definition: { through: [first, second, third] } }];
    } else {
      var prefix = tool === 'centroid' ? 'g' : (tool === 'incenter' ? 'i' : 'H');
      ops = [{ op: 'create', type: tool, id: nextId(prefix, []),
               definition: { a: first, b: second, c: third } }];
    }
    sendOpsQuiet(ops).then(function (result) {
      if (!result || !result.ok) return;
      if (tool === 'circumcircle') {
        showStatus('外接圆作好了：圆心到三个顶点的距离相等', 'done', 6000);
      } else if (tool === 'abisector') {
        showStatus('角平分线作好了：顶点是中间点的 ' + second, 'done', 6000);
      } else {
        showStatus((TYPE_CN[tool] || tool) + ' 求出来了：三角形 ' +
                   first + '、' + second + '、' + third + ' 的那个点', 'done', 6000);
      }
    });
    updateHint();
  }

  function toolClick(hit, at) {
    var tool = state.tool;
    if (tool === 'point') {
      var name = nextId('', []);
      sendOpsQuiet([{ op: 'create', type: 'point', id: name,
                      definition: { x: W7Snap.value(at.x), y: W7Snap.value(at.y) } }]);
      return;
    }
    if (tool === 'segment' || tool === 'line' || tool === 'ray' || tool === 'midpoint') {
      twoPoints(tool, hit, at);
      return;
    }
    if (tool === 'abisector' || tool === 'circumcircle' || tool === 'centroid' ||
        tool === 'incenter' || tool === 'orthocenter') {
      // 五种"三个点"的构造：角平分线、外接圆、重心、内心、垂心
      threePoints(tool, hit, at);
      return;
    }
    if (tool === 'circle') {
      var pending = state.pending || (state.pending = []);
      if (!pending.length) {
        pending.push(pickOrMakePoint(hit, at));
        updateHint();
        return;
      }
      var center = pending[0];
      var through = pickOrMakePoint(hit, at);
      if (center === through) {
        showStatus('圆上那一点不能跟圆心重合', 'warn', 5000);
        state.pending = [];
        updateHint();
        return;
      }
      state.pending = [];
      sendOpsQuiet([{ op: 'create', type: 'circle', id: nextId('c', []),
                      definition: { center: center, through: through } }]);
      updateHint();
      return;
    }
    if (tool === 'perp') {
      var list = state.pending || (state.pending = []);
      if (!list.length) {
        if (!hit || hit.type !== 'point') {
          showStatus('先点一个点（垂线要过这个点）', 'warn', 5000);
          return;
        }
        list.push(hit.id);
        updateHint();
        return;
      }
      if (!hit || hit.id === list[0]) {
        showStatus('再点一条线或线段', 'warn', 5000);
        return;
      }
      var through = list[0];
      state.pending = [];
      sendOpsQuiet([{ op: 'create', type: 'perpendicular', id: nextId('pd', []),
                      definition: { through: through, to: hit.id } }]);
      updateHint();
      return;
    }
    if (tool === 'parallel') {
      // 过一点作已知直线的平行线：和「垂线」是同一个手感，点一个点、再点一条线
      var lane = state.pending || (state.pending = []);
      if (!lane.length) {
        if (!hit || hit.type !== 'point') {
          showStatus('先点一个点（平行线要过这个点）', 'warn', 5000);
          return;
        }
        lane.push(hit.id);
        updateHint();
        return;
      }
      if (!hit || hit.id === lane[0] || !isLineLike(hit)) {
        showStatus('再点一条直线或线段（新线平行于它）', 'warn', 6000);
        return;
      }
      var over = lane[0];
      state.pending = [];
      sendOpsQuiet([{ op: 'create', type: 'parallel', id: nextId('pr', []),
                      definition: { through: over, to: hit.id } }]);
      updateHint();
      return;
    }
    if (tool === 'pbisector') {
      /* 中垂线（垂直平分线）。老师说的用法是"点两点，先连出线段再作中垂线"，
         所以两条路都走：
         ① 直接点一条现成的线段（直线 / 射线也行），立刻作它的垂直平分线；
         ② 点两个点，先把这两点连成线段，再作这条线段的垂直平分线。 */
      var spans = state.pending || (state.pending = []);
      var span = (hit && isLineLike(hit)) ? (hit.definition || {}) : null;
      if (span && span.start && span.end) {
        if (spans.length) {
          showStatus('中垂线要么直接点一条线段，要么点两个点 —— 别混着来', 'warn', 7000);
          state.pending = [];
          updateHint();
          return;
        }
        state.pending = [];
        sendOpsQuiet([{ op: 'create', type: 'perpendicular_bisector', id: nextId('pb', []),
                        definition: { a: span.start, b: span.end } }]);
        updateHint();
        return;
      }
      var spot = pickOrMakePoint(hit, at);
      if (spans.indexOf(spot) >= 0) {
        showStatus('这个点已经点过了：中垂线要两个不同的点', 'warn', 6000);
        updateHint();
        return;
      }
      spans.push(spot);
      if (spans.length < 2) {
        updateHint('已选 1 个点，再点一个（会先连出线段，再作它的中垂线）');
        return;
      }
      var left = spans[0];
      var right = spans[1];
      state.pending = [];
      var ops = [];
      var existing = null;
      // 这两点之间已经有线段了就直接用，别在原地叠一条重复的
      var all = orderedObjects();
      for (var scan = 0; scan < all.length; scan++) {
        var piece = all[scan];
        if (!piece || piece.type !== 'segment' || !piece.definition) continue;
        var head = piece.definition.start;
        var tail = piece.definition.end;
        if ((head === left && tail === right) || (head === right && tail === left)) {
          existing = piece.id;
          break;
        }
      }
      if (!existing) {
        existing = nextId('s', []);
        ops.push({ op: 'create', type: 'segment', id: existing,
                   definition: { start: left, end: right } });
      }
      ops.push({ op: 'create', type: 'perpendicular_bisector', id: nextId('pb', [existing]),
                 definition: { a: left, b: right } });
      sendOpsQuiet(ops);
      updateHint();
      return;
    }
    if (tool === 'intersect') {
      var pair = state.pending || (state.pending = []);
      if (!hit) {
        showStatus('要交点：请先后点两个对象（圆、直线、线段都行）；离图形 14 像素以内就算点中', 'warn', 6000);
        return;
      }
      if (!pair.length) { pair.push(hit.id); updateHint(); return; }
      if (pair[0] === hit.id) { showStatus('要选两个不同的对象，同一个算不出交点', 'warn', 5000); return; }
      state.pending = [];
      // 就近取交点：把这一下的点击位置一起发给服务端，两个交点时它取离这里近的那个。
      // （以前是"同一对再点一次换成另一个"——那是瞎挑，点哪儿就该是哪儿。）
      sendOpsQuiet([{ op: 'create', type: 'point', id: nextId('X', []),
                      definition: { intersect: [pair[0], hit.id],
                                    near: { x: round4(at.x), y: round4(at.y) } } }]);
      updateHint();
      return;
    }
    if (tool === 'poly' || tool === 'area') {
      // 多边形和「算面积」是同一套点顶点：面积就是多边形自己的 value.area，
      // 代数区那一行直接显示（静止给精确值，拖动给小数）—— 不另建一个度量对象。
      if (!hit && !at) return;
      var points = state.pending || (state.pending = []);
      var id = pickOrMakePoint(hit, at);
      if (points.indexOf(id) < 0) points.push(id);
      updateHint();
      return;
    }
    if (tool === 'angle') {
      // 两条线所成的角（取锐角）：和空间那边的线线角同一套算法，精确值由服务端算
      var picked = state.pending || (state.pending = []);
      if (!hit) {
        showStatus('先点一条直线或线段（算两条线所成的角）', 'warn', 6000);
        return;
      }
      if (!isLineLike(hit)) {
        showStatus('角是两条「线」所成的：请点直线 / 线段 / 射线 / 切线这些（现在点的是 ' +
                   (TYPE_CN[hit.type] || hit.type) + '）', 'warn', 9000);
        return;
      }
      if (!picked.length) { picked.push(hit.id); updateHint(); return; }
      if (picked[0] === hit.id) { showStatus('要选两条不同的线', 'warn', 5000); return; }
      var firstLine = picked[0];
      state.pending = [];
      sendOpsQuiet([{ op: 'create', type: 'measure', id: nextId('m', []),
                      definition: { kind: 'angle', args: [firstLine, hit.id] } }]).then(
        function (result) {
          if (result && result.ok) {
            showStatus('角算好了：' + firstLine + ' 与 ' + hit.id + ' 所成的角看代数区那一行' +
                       '（静止时给分数 / 根号，拖动时给小数）', 'done', 8000);
          }
        });
      updateHint();
      return;
    }
    if (tool === 'motion') {
      // 动点：挂在被点的对象上，参数 0.5 = 放在中间，之后能顺着它滑。
      // 挂在谁身上由服务端校验（它才认哪些类型能承载动点），这里只负责挑对象。
      if (!hit) {
        showStatus('要放动点：请点一个圆、线段、直线、弧、函数曲线或方程曲线——' +
                   '点在曲线附近（14 像素内）就算点中；' +
                   '点在圆的太里面不算，那一片要留给里面的对象', 'warn', 8000);
        return;
      }
      if (hit.type === 'point') {
        showStatus('要点的是一条线或一个圆，不是点本身（点已经能直接拖了）', 'warn', 6000);
        return;
      }
      var hostId = hit.id;
      sendOpsQuiet([{ op: 'create', type: 'point', id: nextId('', []),
                      definition: { on: hostId, parameter: 0.5 } }]).then(function (result) {
        if (!result || !result.ok) return;   // 失败的原因 sendOpsQuiet 已经说了
        var made = (result.created && result.created.length) ? objById(result.created[0]) : null;
        if (made && !pointOf(made)) {
          // 服务端收下了动点、却没给出坐标：它挂着的对象现在没有算出来的值
          // （比如那张图是被"整批回滚"弄坏过）。这时候拖着也是白拖，得说明白。
          showStatus('动点 ' + made.id + ' 放上去了，但服务端没算出坐标：' + hostId +
                     ' 现在没有算出来的值。请把 ' + hostId + ' 重画一次（或让 AI 重画这张图），' +
                     '然后把动点删掉重新放一个。', 'err', 14000);
          return;
        }
        showStatus('已放上动点：切回「选择」拖它，它会顺着 ' + hostId + ' 滑', 'done', 8000);
      });
      return;
    }
    if (tool === 'tangent') {
      // 切线：点在①挂在曲线上的动点 ②交点 ③自己放的点 上都行 —— 只要这一点落在曲线
      // （或圆）上，就在那儿画切线。斜率由服务端用导数算，前端不做数学。
      // 它踩在哪条曲线上：先看定义，再看坐标离哪条曲线的采样点最近；
      // 认不出来（没挨着 / 同时挨着好几条）就再点一条曲线指定。
      var waiting = state.pending || (state.pending = []);
      if (waiting.length) {
        if (!hit || !TANGENT_HOSTS[hit.type]) {
          showStatus('再点一条曲线（或圆）：切线要画在这个点所在的那条上', 'warn', 8000);
          return;
        }
        var atId = waiting[0];
        state.pending = [];
        makeTangent(hit.id, atId);
        return;
      }
      if (!hit || hit.type !== 'point') {
        showStatus('先点一个点：动点、交点、自己放的点都行（它要在曲线或圆上）', 'warn', 7000);
        return;
      }
      var hostId = tangentHostOf(hit);
      if (hostId) { makeTangent(hostId, hit.id); return; }
      waiting.push(hit.id);
      updateHint('再点一条曲线或圆：切线画在 ' + hit.id + ' 所在的那条上');
      showStatus('认不出 ' + hit.id + ' 踩在哪条曲线上（没挨着曲线，或者同时挨着好几条）：' +
                 '再点一条曲线，就在那儿画切线', 'warn', 10000);
      return;
    }
    if (tool === 'rotate') {
      // 旋转：① 点一个定点（旋转中心）② 点要转的直线/线段 ③ 按住拖动就绕定点转。
      var picks = state.pending || (state.pending = []);
      if (state.rotate && hit && ROTATE_TARGETS[hit.type]) {
        armRotate(state.rotate.center, hit);     // 换个目标：定点不变
        return;
      }
      if (!picks.length) {
        if (!hit || hit.type !== 'point') {
          showStatus('先点一个定点（旋转中心）：点一个已经有的点', 'warn', 7000);
          return;
        }
        picks.push(hit.id);
        updateHint();
        return;
      }
      armRotate(picks[0], hit);
      return;
    }
    if (SLIDE_MODES[tool]) {
      armSlide(hit);
      return;
    }
    if (tool === 'trace') {
      if (!hit || hit.type !== 'point') {
        showStatus('追踪只能开在一个点上：请点一个点（圆、直线、线段都不行）', 'warn', 6000);
        return;
      }
      traceObject(hit.id);
      return;
    }
    if (tool === 'delete') {
      if (!hit) {
        showStatus('要删东西：请点一下要删的那个对象（离它 14 像素以内就算点中）', 'warn', 6000);
        return;
      }
      sendOpsQuiet([{ op: 'delete', id: hit.id }]);
      return;
    }
  }

  /* ---------------------------------------------------------- 写方程就画出来 */
  /* 把老师敲的一行字变成对象类型。**只做有把握的**：
     显函数、隐式方程（圆锥曲线走这条）、不等式、点。
     极坐标和参数方程的变量约定（θ 还是 t）冻结文档里没写死，宁可不做也不猜错。 */
  function classifyEquation(text) {
    var raw = String(text || '').trim();
    if (!raw) return { error: '先写点什么：y = x^2、x^2/4 + y^2/9 = 1、A=(1,2)' };
    // 点：A=(1,2) / P = (-2, 3.5)
    var pointMatch = raw.match(/^([A-Za-z][A-Za-z0-9_]*)\s*=\s*\(\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*\)$/);
    if (pointMatch) {
      return { id: pointMatch[1], type: 'point',
               definition: { x: parseFloat(pointMatch[2]), y: parseFloat(pointMatch[3]) } };
    }
    // 不等式：带 > < >= <= 的（== 不算）
    if (/[<>]/.test(raw) && raw.indexOf('=') >= 0 || /[<>]/.test(raw)) {
      if (/[<>]/.test(raw)) return { type: 'inequality', definition: { expr: raw } };
    }
    var sides = raw.split('=');
    if (sides.length === 2) {
      var left = sides[0].trim();
      var right = sides[1].trim();
      if (!left || !right) return { error: '等号两边都要有内容' };
      // y = f(x) -> 显函数
      if (left === 'y' || left === 'f(x)') {
        return { type: 'function', definition: { expr: right, var: 'x' } };
      }
      // 左边是 x 或含 x，右边含 y（或者反过来）：当成隐式方程，全部移到一边
      var hasY = /(^|[^a-zA-Z])y([^a-zA-Z]|$)/.test(raw);
      var hasX = /(^|[^a-zA-Z])x([^a-zA-Z]|$)/.test(raw);
      if (hasY && hasX) {
        // F(x,y) = 左边 - 右边 = 0；圆锥曲线、圆、椭圆、双曲线都走这条
        return { type: 'implicit', definition: { expr: '(' + left + ') - (' + right + ')' } };
      }
      return { error: '这条等式看不懂：想要函数请写 y = ...，想要曲线请让 x 和 y 都出现' };
    }
    // 没有等号：只有 x 就直接当 y = ...
    if (/(^|[^a-zA-Z])x([^a-zA-Z]|$)/.test(raw) && !/(^|[^a-zA-Z])y([^a-zA-Z]|$)/.test(raw)) {
      return { type: 'function', definition: { expr: raw, var: 'x' } };
    }
    return { error: '看不懂：「' + raw + '」。试试 y = x^2、x^2/4 + y^2/9 = 1、A=(1,2)' };
  }

  function drawEquation() {
    var input = $('#planeInput');
    if (!input) return;
    var answer = classifyEquation(input.value);
    if (answer.error) {
      showStatus(answer.error, 'warn', 9000);
      setNote(answer.error);
      return;
    }
    var op = { op: 'create', type: answer.type,
               id: answer.id || nextId(answer.type === 'point' ? '' : 'g', []),
               definition: answer.definition };
    sendOpsQuiet([op]).then(function (result) {
      if (result && result.ok) {
        input.value = '';
        setNote('已画出：' + (result.explanation || op.id));
        showStatus('已画出 ' + op.id, 'done', 4000);
      }
    });
  }

  function clearPlane() {
    var objects = (state.plane && state.plane.objects) || {};
    /* 只删"根"对象（没有任何还活着的父对象的那些），让服务端自己往下级联。
       以前是每个对象发一条 delete：删掉父对象会把它级联删掉，后面那条就变成
       "没有这个对象"，而这一批是**整批原子**的——于是一次失败整批回滚，
       平面上一个都没删掉，老师那边看起来就是"点了清空没反应"（而且回滚还会把
       所有对象算出来的值清空）。删根不会撞车：根不会被别的根级联掉。 */
    var roots = [];
    for (var key in objects) {
      if (!objects.hasOwnProperty(key)) continue;
      var parents = objects[key].parents || [];
      var alive = false;
      for (var index = 0; index < parents.length; index++) {
        if (objects[parents[index]]) { alive = true; break; }
      }
      if (!alive) roots.push(key);
    }
    if (!roots.length) { showStatus('平面上现在是空的', 'warn', 4000); return; }
    var ops = [];
    for (var position = 0; position < roots.length; position++) {
      ops.push({ op: 'delete', id: roots[position] });
    }
    sendOpsQuiet(ops).then(function (result) {
      if (result && result.ok) showStatus('已清空平面', 'done', 4000);
    });
  }

  function finishPoly() {
    var points = state.pending || [];
    var wantArea = state.tool === 'area';
    if (points.length < 3) {
      showStatus((wantArea ? '算面积' : '多边形') + '至少要三个顶点（现在 ' + points.length + ' 个）。' +
                 '接着点顶点，点完按「完成」', 'warn', 7000);
      return;
    }
    state.pending = [];
    var polygonId = nextId('poly', []);
    sendOpsQuiet([{ op: 'create', type: 'polygon', id: polygonId,
                    definition: { points: points.slice() } }]).then(function (result) {
      if (!result || !result.ok) return;      // 失败原因 sendOpsQuiet 已经说了
      showStatus(wantArea
        ? '面积算好了：看 ' + polygonId + ' 那一行（静止时给精确值，拖动时给小数）'
        : '多边形画好了：' + polygonId, 'done', 6000);
    });
    updateHint();
  }

  function sendOps(ops) {
    return request('/api/plane/apply', { ops: ops }).then(function (data) {
      applyResult(data);
      return data;
    });
  }

  function simpleAction(path, label) {
    return request(path, {}).then(function (data) {
      applyResult(data);
      showStatus(label + '完成', 'done', 2500);
      return data;
    })['catch'](function (error) {
      showStatus(label + '失败：' + error.message, 'err', 8000);
    });
  }

  function setVisibility(id, visible) {
    var obj = objById(id);
    if (!obj) return;
    // 先本地反馈（点一下立刻有反应），服务端回了之后以服务端为准
    obj.style = obj.style || {};
    obj.style.visible = visible;
    redraw();
    sendOps([{ op: 'update', id: id, style: { visible: visible } }])['catch'](function (error) {
      showStatus('显隐没改成：' + error.message + '（已经改回服务端的状态）', 'err', 8000);
      loadPlane();
    });
  }

  function deleteObject(id) {
    var obj = objById(id);
    var name = obj ? (obj.label || obj.id) : id;
    showStatus('删除 ' + name + '…', '', 0);
    sendOps([{ op: 'delete', id: id }]).then(function (data) {
      if (state.selected === id) state.selected = null;
      var removed = (data && data.removed) || [];
      if (removed.length > 1) {
        showStatus('删除 ' + name + ' 完成，连带删掉了 ' + removed.join('、'), 'warn', 6000);
      } else {
        showStatus('删除 ' + name + ' 完成', 'done', 2500);
      }
    })['catch'](function (error) {
      showStatus('删除失败：' + error.message, 'err', 8000);
    });
  }

  /* ------------------------------------------------------------ 拖动联动 */

  /* 绕定点旋转：只有"两个点连成的"线段/直线/射线/向量能整体转 ——
     转的是那两个端点的坐标，所以它们必须是自由点（动点、交点的位置是算出来的，改不了）。 */
  var ROTATE_TARGETS = { segment: 1, line: 1, ray: 1, vector: 1 };

  function freePointOf(id) {
    var obj = objById(id);
    if (!obj || obj.type !== 'point') return null;
    var def = obj.definition || {};
    if (def.on || def.intersect) return null;
    if (typeof def.x !== 'number' || typeof def.y !== 'number') return null;
    return obj;
  }

  function armRotate(centerId, hit) {
    var center = pointOf(objById(centerId));
    if (!center) {
      showStatus('定点 ' + centerId + ' 还没有坐标，转不了（先让它算出来）', 'warn', 8000);
      return;
    }
    if (!hit || !ROTATE_TARGETS[hit.type]) {
      showStatus('再点一条直线或线段（要转的那条）', 'warn', 6000);
      return;
    }
    var def = hit.definition || {};
    if (typeof def.start !== 'string' || typeof def.end !== 'string') {
      showStatus('这条 ' + (TYPE_CN[hit.type] || hit.type) + '（' + hit.id + '）不是用两个点定出来的，' +
                 '整体转不了：能转的是「两点连成的」线段 / 直线 / 射线 / 向量。', 'warn', 13000);
      return;
    }
    var first = freePointOf(def.start);
    var second = freePointOf(def.end);
    if (!first || !second) {
      showStatus('这条 ' + (TYPE_CN[hit.type] || hit.type) + '（' + hit.id + '）的端点是算出来的' +
                 '（动点 / 交点 / 别的对象定的），整体转不了：' +
                 '换成两个自由点连的线，或者直接拖那两个端点。', 'warn', 13000);
      return;
    }
    state.rotate = { center: centerId, target: hit.id, ids: [first.id, second.id],
                     base: [{ x: first.definition.x, y: first.definition.y },
                            { x: second.definition.x, y: second.definition.y }],
                     start: null, moved: false };
    state.pending = [];
    updateHint('按住 ' + hit.id + ' 拖动：它绕 ' + centerId + ' 转（松手结束，能接着再拖；' +
               '点别的线就换一条，点别的点就换定点）');
    showStatus('旋转：按住 ' + hit.id + ' 拖动，它就绕定点 ' + centerId + ' 转', 'done', 8000);
  }

  function startRotate(event, point) {
    var center = pointOf(objById(state.rotate.center));
    if (!center) { state.rotate = null; return; }
    state.rotate.start = Math.atan2(point.y - center.y, point.x - center.x);
    state.rotate.moved = false;
    state.pan = null;
    state.drag = null;
    try { state.svg.setPointerCapture && state.svg.setPointerCapture(event.pointerId); }
    catch (error) { /* 老内核没有 pointer capture：window 上那个兜底照样能收尾 */ }
    hideStatus();
  }

  /* 按鼠标相对定点转过的角度，把两个端点一起转过去（刚体转动，线长不变）。
     两个端点必须**同一批**发出去：分两条发，中间那一帧会画出一条被拉长的线。 */
  function rotateTo(point) {
    var turn = state.rotate;
    var center = pointOf(objById(turn.center));
    if (!center) { state.rotate = null; return; }
    var angle = Math.atan2(point.y - center.y, point.x - center.x) - turn.start;
    var cos = Math.cos(angle);
    var sin = Math.sin(angle);
    var ops = [];
    for (var index = 0; index < 2; index++) {
      var base = turn.base[index];
      var dx = base.x - center.x;
      var dy = base.y - center.y;
      ops.push({ op: 'update', id: turn.ids[index],
                 definition: { x: round4(center.x + dx * cos - dy * sin),
                               y: round4(center.y + dx * sin + dy * cos) } });
    }
    turn.moved = true;
    queueOp(ops);
  }

  /* -------------------------------------------------- 沿线拖（整条线段沿着轨道滑）

     老师要的："沿线拖动线段；曲线之类的就是拖动曲线上的定长弦。"
     后来又指出：**同一个 Δ 参数不等于长度不变** —— 在函数曲线上同一个 Δ 参数保的是
     "x 方向长度"，不是线段长度。所以拆成三种，各按各的不变量走：

       · slide     保**线段长度**（真正的定长弦）
       · slidex    保 **x 方向长度**（两端的 Δx 不变）
       · slidey    保 **y 方向长度**（两端的 Δy 不变）

     直线/线段/射线/向量这几种轨道上，整条线段是**刚体平移**，三个不变量同时成立，
     所以那几种轨道三种模式走同一条路（精确、不用解方程）。
     只有曲线（函数 / 圆 / 弧）上三者才会分开：保长度和保 y 需要**沿着曲线解一下**
     另一端的参数 —— 解用的是这段曲线自己的采样点，和绘制、和服务端都是同一份数据。
     和 rotateTo 一样：两个端点必须**同一批**发出去，分两条发中间那一帧会把线段画歪。 */

  var SLIDE_HOSTS = { segment: 1, ray: 1, line: 1, vector: 1, circle: 1, arc: 1, sector: 1,
                      'function': 1, implicit: 1, parametric: 1, polar: 1 };
  // 工具名 -> 保哪个不变量
  var SLIDE_MODES = { slide: 'length', slidex: 'x', slidey: 'y' };
  var SLIDE_LABELS = { length: '线段长度', x: 'x 方向长度', y: 'y 方向长度' };
  var STRAIGHT_HOSTS = { segment: 1, ray: 1, line: 1, vector: 1 };

  /* 某个点在某条轨道上的参数。借用动点那套参数化（gliderParameter 只看 definition.on），
     传个假对象进去就行 —— 这样"轨道上的位置怎么算"只有一份实现，不会两边算得不一样。 */
  function hostParameter(hostId, point) {
    return gliderParameter({ definition: { on: hostId } }, point);
  }

  /* 函数轨道的参数区间：钉了 xmin/xmax 就用它；没钉（自动区间）用**采样点自己的 x 范围**。

     不能在这里假设一个 ±10：自动区间是服务端按可见范围采的，视野一拉远，真实区间可能是
     ±30，那么"参数 -> x"整体被缩放，手指走一格线段走三格（老师说的"轻轻一拖就跑很远"），
     按下时的取参也会和缓存对不上、一按就跳。采样点就是服务端采的那条折线，范围天然一致。 */
  function hostRange(host) {
    var def = (host && host.definition) || {};
    if (typeof def.xmin === 'number' && typeof def.xmax === 'number' && def.xmax > def.xmin) {
      return { low: def.xmin, high: def.xmax };
    }
    var groups = sampledSegments(host);
    var low = null;
    var high = null;
    for (var g = 0; g < groups.length; g++) {
      var line = groups[g];
      for (var k = 0; k < line.length; k++) {
        var x = line[k].x;
        if (low === null || x < low) low = x;
        if (high === null || x > high) high = x;
      }
    }
    if (low !== null && high !== null && high > low) return { low: low, high: high };
    return { low: -10, high: 10 };
  }

  /* 参数 t 在轨道上的坐标。**必须和 hostParameter 一一对应**，否则拖起来会跳：
     直线类：端点之间线性插值；圆：角度 = 2πt；弧：从起始角扫 t 倍；
     函数曲线：x = xmin + (xmax-xmin)·t，y 用采样折线插值（前端不解析表达式）；
     方程 / 参数 / 极坐标曲线：t 是弧长占比，沿采样折线按弧长走。 */
  function hostPointAt(host, parameter) {
    if (!host) return null;
    var type = host.type;
    var t = clamp(parameter, 0, 1);
    if (ARC_CURVES[type]) {
      return arcPointAt(arcPath(sampledSegments(host)), t);
    }
    if (type === 'circle') {
      var center = centerOf(host);
      var radius = radiusOf(host);
      if (!center || radius === null) return null;
      var angle = t * Math.PI * 2;
      return { x: center.x + radius * Math.cos(angle), y: center.y + radius * Math.sin(angle) };
    }
    if (type === 'arc' || type === 'sector') {
      var angles = arcAngles(host);
      var r = radiusOf(host);
      if (!angles || r === null) return null;
      var sweep = angles.to - angles.from;
      var at = angles.from + sweep * t;
      return { x: angles.center.x + r * Math.cos(at), y: angles.center.y + r * Math.sin(at) };
    }
    if (STRAIGHT_HOSTS[type] || LINE_TYPES[type]) {
      var ends = endpointsOf(host);
      if (!ends[0] || !ends[1]) return null;
      return { x: ends[0].x + (ends[1].x - ends[0].x) * t,
               y: ends[0].y + (ends[1].y - ends[0].y) * t };
    }
    if (type === 'function') {
      var range = hostRange(host);
      var low = range.low;
      var high = range.high;
      if (!(high > low)) return null;
      var want = low + (high - low) * t;
      var groups = sampledSegments(host);
      // 找**夹住** want 的那两个采样点（不是"最近点 + 邻居"：那样会差半格，
      // 解出来的另一端就会偏，拖动时误差还会累积）
      var left = null;
      var right = null;
      var near = null;
      var nearGap = Infinity;
      for (var g = 0; g < groups.length; g++) {
        var line = groups[g];
        for (var k = 0; k < line.length; k++) {
          var sample = line[k];
          var gap = Math.abs(sample.x - want);
          if (gap < nearGap) { nearGap = gap; near = sample; }
          if (sample.x <= want && (!left || sample.x > left.x)) left = sample;
          if (sample.x >= want && (!right || sample.x < right.x)) right = sample;
        }
      }
      if (!left || !right) return near ? { x: near.x, y: near.y } : null;
      if (Math.abs(right.x - left.x) < 1e-12) return { x: left.x, y: left.y };
      var ratio = (want - left.x) / (right.x - left.x);
      return { x: left.x + (right.x - left.x) * ratio,
               y: left.y + (right.y - left.y) * ratio };
    }
    return null;
  }


  function armSlide(hit) {
    if (!hit || !SLIDE_HOSTS[hit.type]) {
      showStatus('先点一条轨道：直线 / 线段 / 射线 / 圆 / 弧 / 函数曲线 / 方程曲线都行',
                 'warn', 9000);
      return;
    }
    var mode = SLIDE_MODES[state.tool] || 'length';
    state.slide = { host: hit.id, drag: null, mode: mode };
    state.pending = [];
    updateHint('轨道是 ' + hit.id + '（保' + SLIDE_LABELS[mode] + '）：' +
               '按住一条线段拖动，它整条沿着它滑（点别的对象就换轨道）');
    showStatus('沿线拖：按住线段拖动，它就沿着 ' + hit.id + ' 滑（保' + SLIDE_LABELS[mode] + '）',
               'done', 8000);
  }

  /* 直线类轨道：整条线段刚体平移（三个不变量同时成立），Δ 在这里收边 */
  function slideTargets(hostId, base, delta) {
    var host = objById(hostId);
    var type = host ? host.type : '';
    var low = Math.min(base[0], base[1]);
    var high = Math.max(base[0], base[1]);
    if (type === 'segment') {
      // 轨道是线段：Δ 收到"两个端点都不出界"为止。
      // **不能各自夹**：那样一头顶住之后另一头还在走，线段会被挤短。
      delta = clamp(delta, -low, 1 - high);
    } else if (type === 'ray') {
      delta = Math.max(delta, -low);
    }
    var out = [];
    for (var index = 0; index < 2; index++) {
      var value = base[index] + delta;
      if (type === 'circle') {
        value = value % 1;                     // 圆：绕回去（还是同一根弦）
        if (value < 0) value += 1;
      } else if (type === 'arc' || type === 'sector' || type === 'function' ||
                 ARC_CURVES[type]) {
        value = clamp(value, 0, 1);            // 弧 / 函数 / 方程曲线：夹在轨道范围内
      }
      out.push(value);
    }
    return out;
  }

  function slideOpsFor(hostId, ids, values) {
    var ops = [];
    for (var index = 0; index < 2; index++) {
      ops.push({ op: 'update', id: ids[index],
                 definition: { on: hostId, parameter: round4(values[index]) } });
    }
    return ops;
  }

  function startSlide(event, point, segment) {
    // 轨道就是这条线段自己 = 怎么拖都不动（参数被夹在同一条线段上），先说清楚
    if (state.slide.host === segment.id) {
      showStatus('轨道不能是这条线段自己：先点一条它要沿着滑的线（曲线、别的直线或圆），再按它拖',
                 'warn', 9000);
      return;
    }
    var def = segment.definition || {};
    var first = objById(def.start);
    var second = objById(def.end);
    var ends = [pointOf(first), pointOf(second)];
    var from = hostParameter(state.slide.host, point);
    if (!first || !second || !ends[0] || !ends[1] || !from) {
      showStatus('这条线段的端点算不出在轨道上的位置，拖不了（端点得是有坐标的点）', 'warn', 8000);
      return;
    }
    var one = hostParameter(state.slide.host, ends[0]);
    var two = hostParameter(state.slide.host, ends[1]);
    if (!one || !two) {
      showStatus('这个轨道认不出位置：换成直线 / 线段 / 圆 / 弧 / 函数曲线试试', 'warn', 8000);
      return;
    }
    var host = objById(state.slide.host);
    var mode = state.slide.mode || 'length';
    // 谁跟着鼠标走：离按下的地方更近的那个端点（另一端按不变量解出来）
    var nearFirst = Math.abs(one.parameter - from.parameter)
                  <= Math.abs(two.parameter - from.parameter);
    var leader = nearFirst ? one.parameter : two.parameter;
    var follower = nearFirst ? two.parameter : one.parameter;
    var keep = { x: ends[1].x - ends[0].x, y: ends[1].y - ends[0].y,
                 length: Math.hypot(ends[1].x - ends[0].x, ends[1].y - ends[0].y) };
    state.slide.drag = {
      ids: nearFirst ? [first.id, second.id] : [second.id, first.id],
      base: [leader, follower],
      // 手指按下点和带头端点之间差多少参数：拖动时保持这个差，线段才不会一按就跳
      grab: from.parameter - leader,
      // 另一端在带头的**哪一侧**（按下时定死）：保长度时拖动过程中不许换边 ——
      // 弦长不变这个条件两个方向都成立，曲线陡的地方"后面那个解"会消失，
      // 不钉住次序的话求解器会顺手取前面那个，端点就"跑到另一边"了。
      // 保 Δx / 保 Δy 不用这个（对称曲线上另一侧才是唯一解），见 cacheSolve 里的 usable。
      side: follower > leader ? 1 : -1,
      // 拖动中取参的起点（= 手指按下处在轨道上的参数）和圆轨道的绕回标记
      where: from.parameter, wrap: host.type === 'circle',
      keep: keep, mode: mode, straight: !!STRAIGHT_HOSTS[host.type],
      //  轨道采样只做一次：拖动中每帧都要"参数<->坐标"来回换算，
      //   每帧都去扫一遍曲线采样点（几百到几千个）会直接卡成幻灯片。
      //   GeoGebra 那类工具也是这么干的：拖动开始时把路径缓存成一条折线，
      //   之后每帧只在缓存上走一小步（见 slideTo 里的局部搜索）。
      cache: hostCache(host)
    };
    state.drag = null;
    state.pan = null;
    try { state.svg.setPointerCapture && state.svg.setPointerCapture(event.pointerId); }
    catch (error) { /* 老内核没有 pointer capture：window 上那个兜底照样能收尾 */ }
    updateHint('按住线段拖动：它沿着 ' + state.slide.host + ' 滑（保' + SLIDE_LABELS[mode] +
               '；松手结束，能接着再拖）');
  }

  /* 收掉这次沿线拖：拖动作废、轨道清掉、工具回到「选择」。
     按画布空白处走的就是它 —— 不然工具一直架着、线段又停在远处，
     老师会觉得"点什么都复位不了"。 */
  function endSlide() {
    if (state.slide) state.slide.drag = null;
    setTool('select');   // setTool 里顺带清掉 state.slide 和提示
  }

  /* 把轨道缓存成一条"参数均匀"的折线：拖动中每帧只用它，不再回到对象/采样点上去扫。

     参数一律归一到 [0,1]，采样点按参数等距排好 —— 这样：
       · 参数 -> 坐标：算下标 + 相邻两点插值，O(1)；
       · 坐标 -> 参数（鼠标落在轨道上的哪儿）：只需扫这 ~240 个点，量级可控；
       · 解另一端：在这条缓存上走小步（见 cacheSolve），一帧几十次运算就够。 */
  function hostCache(host) {
    var count = 240;
    var params = [];
    var points = [];
    var index;
    if (!host) return null;
    var type = host.type;
    if (type === 'function') {
      // 函数：采样点本身就是折线，x 归一化就是参数（区间认 hostRange，别在这里猜 ±10）
      var range = hostRange(host);
      var low = range.low;
      var high = range.high;
      if (!(high > low)) return null;
      var groups = sampledSegments(host);
      var flat = [];
      for (index = 0; index < groups.length; index++) {
        flat = flat.concat(groups[index]);
      }
      if (flat.length < 2) return null;
      for (index = 0; index < flat.length; index++) {
        params.push(clamp((flat[index].x - low) / (high - low), 0, 1));
        points.push({ x: flat[index].x, y: flat[index].y });
      }
      return { params: params, points: points, count: points.length };
    }
    for (index = 0; index <= count; index++) {
      var t = index / count;
      var at = hostPointAt(host, t);
      if (!at) return null;
      params.push(t);
      points.push(at);
    }
    return { params: params, points: points, count: points.length };
  }

  /* 缓存上取参数 t 的坐标：等距缓存 -> 直接算下标，O(1) */
  function cachePointAt(cache, t) {
    if (!cache || !cache.count) return null;
    var value = clamp(t, 0, 1);
    var last = cache.count - 1;
    var position = value * last;
    var index = Math.floor(position);
    if (index >= last) return cache.points[last];
    var ratio = position - index;
    var a = cache.points[index];
    var b = cache.points[index + 1];
    return { x: a.x + (b.x - a.x) * ratio, y: a.y + (b.y - a.y) * ratio };
  }

  /* 轨道参数落到缓存认得的范围：圆绕回 [0,1)，其余夹在 [0,1]。 */
  function useParameter(value, wrap) {
    if (!wrap) return clamp(value, 0, 1);
    value = value % 1;
    return value < 0 ? value + 1 : value;
  }

  /* 拖动中的取参：**只在上一帧附近扫**（±span 格），再把手指**投影到折线上**。

     两件事都得做，少一件就会抽：
       · 只在附近扫：全局最近点在曲线"内侧"是多值的 —— 老师按着弦的中段拖（那地方
         离圆一两百像素），手指轻轻一动最近点可能翻到对面去，线段一下飞很远；
       · 投影到折线上：只认最近的采样点的话，参数是 1/240 一挡的台阶，圈一大
         （大圆、拉远的函数曲线）一格就是好几像素，线段一格一格地跳、长度也跟着变。
     直线轨道传 span=1（整条都扫）：直线上的投影本来就唯一，要的就是精确。
     圆的 0/1 接缝按 wrap 绕回来，跨接缝的那条边照样投影得出来。 */
  function cacheProject(cache, point, around, span, wrap) {
    if (!cache || cache.count < 2) return null;
    var last = cache.count - 1;
    var step = 1 / last;
    var steps = Math.max(1, Math.floor((span || 0.12) / step));
    var nearest = useParameter(around, wrap);
    var nearestGap = Infinity;
    var k;
    for (k = -steps; k <= steps; k++) {
      var t = useParameter(around + k * step, wrap);
      var at = cachePointAt(cache, t);
      if (!at) continue;
      var gap = (at.x - point.x) * (at.x - point.x) + (at.y - point.y) * (at.y - point.y);
      if (gap < nearestGap) { nearestGap = gap; nearest = t; }
    }
    var center = Math.round(nearest * last);
    var best = nearest;
    var bestGap = nearestGap;
    for (k = -1; k <= 0; k++) {
      var low = center + k;
      var from = cachePointAt(cache, useParameter(low * step, wrap));
      var to = cachePointAt(cache, useParameter((low + 1) * step, wrap));
      if (!from || !to) continue;
      var edgeX = to.x - from.x;
      var edgeY = to.y - from.y;
      var edge2 = edgeX * edgeX + edgeY * edgeY;
      var ratio = edge2 > 0
        ? clamp(((point.x - from.x) * edgeX + (point.y - from.y) * edgeY) / edge2, 0, 1)
        : 0;
      var spotX = from.x + edgeX * ratio;
      var spotY = from.y + edgeY * ratio;
      var edgeGap = (spotX - point.x) * (spotX - point.x) + (spotY - point.y) * (spotY - point.y);
      if (edgeGap < bestGap) {
        bestGap = edgeGap;
        best = useParameter((low + ratio) * step, wrap);
      }
    }
    return best;
  }

  /* 在缓存上解"不变量"：从上次的解出发**走小步**找目标，再用三分法细化。

     拖动时鼠标一帧走不了多远，所以从上次的解往前后各走一小段就够 ——
     这也是 GeoGebra 的路径拖动器（path mover）的做法：增量搜索，不做全局重扫。

     三个坑（老师都报过，别再改回去）：
       · 定长弦在曲线上**常常有两个解**（一左一右）。只认"窗口里误差最小"的话会跳到对面去，
         表现就是"长度没变，但一个端点跑到另一边去了"。所以先只认**误差够小**的候选
         （band），再在其中挑**离上一帧最近**的那个 —— 那才是连着的那一支。
       · **还得钉住两端的前后次序**（side）：弦长不变这个条件本身两个方向都成立，
         曲线陡的地方"后面那个解"会消失，求解器就顺手取了前面那个 —— 那一端会**越过**
         另一端（老师看到的"一会跟不上、一会超前、来回抽"）。按下时谁前谁后是定死的，
         拖动过程中不许换边：换不了就停在原地（弦长差一点点），绝不翻过去。
       · 曲线陡的地方另一端的参数跑得比手指快得多，一帧 ±0.12 的窗口追不上：
         窗口里找不到就往外扩 3 倍再找（最多 3 轮，够覆盖整条曲线）。
     返回的参数在圆轨道上会绕回 [0,1)。 */
  function cacheSolve(cache, mode, leader, target, hint, wrap, side) {
    var lead = cachePointAt(cache, useParameter(leader, wrap));
    if (!lead) return null;
    function error(parameter) {
      var at = cachePointAt(cache, useParameter(parameter, wrap));
      if (!at) return Infinity;
      if (mode === 'x') return Math.abs((at.x - lead.x) - target);
      if (mode === 'y') return Math.abs((at.y - lead.y) - target);
      return Math.abs(Math.hypot(at.x - lead.x, at.y - lead.y) - target);
    }
    /* 这个候选算不算数。

       保长度：两端不许换边（按下时谁前谁后是定死的）—— 换边就是"一端跑到另一边去"。
       保 Δx / 保 Δy：**没有"边"这回事**。对称曲线（抛物线）上"同侧"常常根本没有解，
       另一侧才是唯一解（水平弦就是关于对称轴的那一对），锁死边会让整个模式失灵；
       但必须排掉"两端重合"那个假解 —— 重合时 Δx=Δy=0，误差正好是 0，会被当成完美解。 */
    function usable(parameter) {
      var delta = parameter - leader;
      if (wrap) {
        delta = delta % 1;
        if (delta > 0.5) delta -= 1;
        if (delta < -0.5) delta += 1;
      }
      if (mode === 'x' || mode === 'y') return Math.abs(delta) > 1e-3;
      if (!side) return true;
      return delta * side > 0;
    }
    var start = useParameter(hint === null || hint === undefined ? leader : hint, wrap);
    // band：挑候选时松一点（网格有格子，真解不一定正好落在格点上）
    // done：定案要准（三分法细化之后必须真达到这个精度，否则说明这段弦解不出来）
    var band = Math.max(Math.abs(target) * 0.02, 0.01);
    var done = Math.max(Math.abs(target) * 0.002, 1e-3);
    var step = 0.005;
    var span = 0.12;
    var walk;
    var pass;
    var fallback = null;
    var fallbackError = Infinity;
    for (pass = 0; pass < 3; pass++) {
      var steps = Math.max(1, Math.ceil(span / step));
      var best = null;
      var bestError = Infinity;
      var good = null;
      var goodGap = Infinity;
      for (walk = -steps; walk <= steps; walk++) {
        var around = useParameter(start + step * walk, wrap);
        if (!usable(around)) continue;
        var gap = error(around);
        if (gap < bestError) { bestError = gap; best = around; }
        if (gap <= band) {
          var along = Math.abs(around - start);
          if (wrap && along > 0.5) along = 1 - along;
          if (along < goodGap) { goodGap = along; good = around; }
        }
      }
      if (best !== null) {
        // 挑哪一支：误差够小的候选里**离上一帧最近**的那个（连续的同一支）；
        // 一个都不够小（比如采样折线根本做不出这个长度）就细化误差最小的那个，
        // 总之必须给出一个解 —— 返回 null 就等于原地冻住，老师只会说"拖不动了"。
        var chosen = (bestError <= band && good !== null) ? good : best;
        var low = -step;
        var high = step;
        for (walk = 0; walk < 12; walk++) {
          var midLow = low + (high - low) / 3;
          var midHigh = high - (high - low) / 3;
          if (error(useParameter(chosen + midLow, wrap)) <=
              error(useParameter(chosen + midHigh, wrap))) {
            high = midHigh;
          } else {
            low = midLow;
          }
        }
        var found = useParameter(chosen + (low + high) / 2, wrap);
        var foundError = error(found);
        if (foundError <= done) return found;
        if (foundError < fallbackError) { fallbackError = foundError; fallback = found; }
      }
      span = span * 3;
    }
    // 三轮都没有"误差够小"的解：把最好的那个交出去，**不要原地冻住** ——
    // 采样折线本身有离散误差（弦长只能落在折线能做到的那些值上），
    // 一冻住老师就会说"拖到一定距离就拖不动了"。宁可长度差一点点，也要跟手。
    // （但保长度时换边是绝对不行的：换边就是"跑到另一边去"，见上面 usable。）
    if (fallback === null) return start;
    return fallback;
  }

  function slideTo(point) {
    var drag = state.slide && state.slide.drag;
    if (!drag) return;
    var host = objById(state.slide.host);
    if (!host) return;
    var cache = drag.cache || (drag.cache = hostCache(host));
    if (!cache) return;
    // 手指没挪够 1 像素：这一帧什么都不做（触屏的传感器噪声就在这一像素里）。
    // 判据是"离上次真正接受的位置"，所以慢慢拖会攒够 1 像素再走，不会卡住也不会漂。
    // **别把这个值调大**：调成 3 像素时对象就变成 3 像素一跳，老师反馈"一卡一卡的"。
    var dead = state.unit || 0.03;
    if (drag.anchor) {
      var movedX = point.x - drag.anchor.x;
      var movedY = point.y - drag.anchor.y;
      if (movedX * movedX + movedY * movedY < dead * dead) return;
    }
    drag.anchor = { x: point.x, y: point.y };
    // 手指落在轨道上的参数：投影到折线上（连续，不吸附格子）。
    // 直线类整条扫（投影唯一，要精确）；曲线只扫上一帧附近（全局最近点在曲线内侧会翻面）。
    var where = cacheProject(cache, point, typeof drag.where === 'number' ? drag.where : 0,
                             drag.straight ? 1 : 0.12, drag.wrap);
    if (where === null) return;
    drag.where = where;
    // 到头了要说一声：不然老师会以为"拖到一定距离就拖不动了"（其实是轨道就这么长）
    if (!drag.wrap && (where - drag.grab < 0 || where - drag.grab > 1)) hintSlideEdge(drag);
    // 带头端点跟着鼠标（保持按下时的相对位置）；圆的参数要绕回 [0,1)
    var leader = useParameter(where - drag.grab, drag.wrap);
    if (drag.straight) {
      // 直线类轨道：整条刚体平移，三个不变量同时成立 —— 精确平移，不用解方程。
      // 直线上的"参数 -> 坐标"是精确的，所以本地预览和发出去的参数完全一致。
      var want = leader - drag.base[0];
      var spots = slideTargets(state.slide.host, drag.base, want);
      if (Math.abs((spots[0] - drag.base[0]) - want) > 1e-9) hintSlideEdge(drag);
      previewSlide(drag, spots);
      queueOp(slideOpsFor(state.slide.host, drag.ids, spots));
      return;
    }
    var value = drag.mode === 'x' ? drag.keep.x
              : (drag.mode === 'y' ? drag.keep.y : drag.keep.length);
    // 每一帧都重解：不变量必须是精确的（上一版"手指没挪够就沿用旧解"会让长度差一格，
    // 老师看得见 —— 抖动应该在**输入**那一头挡住，见上面的 drag.anchor，不是在输出这头糊）。
    var follower = cacheSolve(cache, drag.mode, leader, value, drag.base[1], drag.wrap,
                              drag.side);
    if (follower === null) {
      // 这一段弦在这个位置画不出来了（解合并/消失）：原地不动，说一次原因就行，
      // 别每帧刷屏 —— 老师往回拖一点就能接着走。
      if (!drag.stuck) {
        drag.stuck = true;
        showStatus('这一段弦到这里就到头了（再拖就解不出另一端），往回拖一点接着走', 'warn', 5000);
      }
      return;
    }
    drag.stuck = false;
    // 记下这一次的解：下一帧从它出发走小步（不做全局重扫），解也就跟着连续
    drag.base[1] = follower;
    // 本地先动（见 previewSlide），每帧照样把同一份参数发给服务端。
    previewSlide(drag, [leader, follower]);
    queueOp(slideOpsFor(state.slide.host, drag.ids, [leader, follower]));
  }

  /* 已经滑到轨道尽头：每条轨道只提醒一次。

     不说的话，老师会以为"拖到一定距离就拖不动了"——其实是这条轨道（线段 / 射线 / 弧 /
     一段采样出来的函数曲线）就到这儿，另一头再走就出界了。 */
  function hintSlideEdge(drag) {
    if (drag.edgeHinted) return;
    drag.edgeHinted = true;
    showStatus('已经滑到轨道尽头了（这条轨道就这么长），往回拖一点接着走', 'warn', 6000);
  }

  /* 拖动中的本地预览：把两个端点按**参数**挪到轨道缓存上的位置并重画，线段立刻跟着手指走，
     不用干等服务端那一趟；同时把这份坐标记进 drag.spots。

     记下来是给 reapplyPreview 用的：服务端回来的那一版对应的是**上一帧左右**的手指位置，
     画上去会把端点（连同线段）拉回头，看起来就是"一直来回抽"。
     光挪两个端点还不够 —— 线段自己那份 value 是服务端算好的副本，
     所以 endpointsOf 那边也改成优先认活的父点，两处合起来才是完整的一段。 */
  function previewSlide(drag, values) {
    var spots = [cachePointAt(drag.cache, values[0]), cachePointAt(drag.cache, values[1])];
    if (!spots[0] || !spots[1]) return;
    drag.spots = spots;
    if (optimisticMove(drag.ids, spots)) drawPreviewSoon();
  }

  function optimisticMove(ids, spots) {
    var changed = false;
    for (var index = 0; index < ids.length; index++) {
      var obj = objById(ids[index]);
      var spot = spots[index];
      if (!obj || !spot) continue;
      if (!obj.value) obj.value = {};
      obj.value.x = spot.x;
      obj.value.y = spot.y;
      changed = true;
    }
    if (changed) previewDependents(ids, spots);
    return changed;
  }

  /* 预览的重画：**立刻画**，不合并到动画帧。

     踩过的坑：合并到 requestAnimationFrame 之后，只要那一帧的回调没来（页面在后台、
     或者被节流），`previewFrame` 就一直挂着，后面所有预览都不再重画 ——
     表现正是"拖不动 / 一会跟不上"。"少画一次"省不下什么，代价却是整条预览停摆。 */
  function drawPreviewSoon() {
    if (typeof drawGeometry === 'function') drawGeometry();
  }

  /* 被拖的点一动，挂在它身上的"点 + 方向"直线（切线 / 垂线 / 平行线 / 中垂线）也得跟着走。

     服务端给这些线的 value 里，锚点（value.point）和方向都是**烤好的副本**：只挪端点不挪它们，
     就成"线段两个端点跟着手指走了、切线还钉在老位置"，而且切线是往视野外延伸的长线，
     方向差一点点远端就甩出去很远 —— 老师报的"切线跟线段端点严重错位"就是这样。

     方向尽量算准：垂线 / 平行线拿目标直线的方向，中垂线拿两点的中点 + 垂线方向；
     切线要导数，前端不复刻服务端的求导，就在服务端已经采好的那条折线上做中心差分
     （参数化复用 gliderParameter/hostPointAt，和服务端一一对应，圆/弧/函数都一样）。
     这是**一帧的预览**：服务端结果一回来就是精确值。 */
  function previewDependents(moved, spots) {
    var objects = (state.plane && state.plane.objects) || {};
    var movedAt = {};
    var index;
    for (index = 0; index < moved.length; index++) movedAt[moved[index]] = spots[index];
    for (var key in objects) {
      if (!Object.prototype.hasOwnProperty.call(objects, key)) continue;
      var obj = objects[key];
      if (!obj || !obj.value) continue;
      var def = obj.definition || {};
      var kind = obj.type;
      var anchor = movedAt[def.at] || movedAt[def.through] || null;
      var direction = null;
      if (kind === 'tangent') {
        // 切点动了：切线要重新"过这一点"，方向也要按新的位置重算
        if (anchor) direction = tangentDirection(def.to, anchor);
      } else if (kind === 'perpendicular' || kind === 'parallel') {
        // 方向取目标直线**活的**两个端点（拖动中它的 value 也是旧副本），拿不到才退回副本。
        // 锚点没动、目标直线动了（比如"过定点作被拖线段的垂线"）时方向照样要重算。
        var target = objById(def.to);
        var targetEnds = target ? endpointsOf(target) : [null, null];
        var way = (targetEnds[0] && targetEnds[1])
          ? { x: targetEnds[1].x - targetEnds[0].x, y: targetEnds[1].y - targetEnds[0].y }
          : ((target && target.value && target.value.direction) || null);
        if (way && (way.x || way.y)) {
          direction = kind === 'perpendicular' ? { x: -way.y, y: way.x } : { x: way.x, y: way.y };
        }
      } else if (kind === 'perpendicular_bisector') {
        var first = movedAt[def.a] || pointOf(objById(def.a));
        var second = movedAt[def.b] || pointOf(objById(def.b));
        if (first && second) {
          anchor = { x: (first.x + second.x) / 2, y: (first.y + second.y) / 2 };
          direction = { x: -(second.y - first.y), y: second.x - first.x };
        }
      } else if (kind === 'angle_bisector') {
        // 顶点是 b：角的顶点一动，平分线整条都要重新定向（算法和服务端那三行一一对应）
        var armFirst = movedAt[def.a] || pointOf(objById(def.a));
        var vertex = movedAt[def.b] || pointOf(objById(def.b));
        var armThird = movedAt[def.c] || pointOf(objById(def.c));
        if (armFirst && vertex && armThird) {
          var ux = armFirst.x - vertex.x;
          var uy = armFirst.y - vertex.y;
          var vx = armThird.x - vertex.x;
          var vy = armThird.y - vertex.y;
          var normU = Math.hypot(ux, uy);
          var normV = Math.hypot(vx, vy);
          if (normU > 1e-12 && normV > 1e-12) {
            anchor = vertex;
            direction = { x: ux / normU + vx / normV, y: uy / normU + vy / normV };
            if (Math.abs(direction.x) < 1e-12 && Math.abs(direction.y) < 1e-12) {
              direction = { x: -uy / normU, y: ux / normU };   // 平角：取垂直方向
            }
          }
        }
      } else if (kind === 'centroid' || kind === 'incenter' || kind === 'orthocenter') {
        // 三角形的三个心：顶点一动，心就得立刻跟着走 —— 否则松手那一下才"跳"过去
        var spotOne = movedAt[def.a] || pointOf(objById(def.a));
        var spotTwo = movedAt[def.b] || pointOf(objById(def.b));
        var spotThree = movedAt[def.c] || pointOf(objById(def.c));
        var heart = (spotOne && spotTwo && spotThree && !obj.value.error)
          ? triangleCenter(kind, spotOne, spotTwo, spotThree) : null;
        if (heart) {
          obj.value.x = heart.x;
          obj.value.y = heart.y;
        }
        continue;
      }
      // 交点：两条**无界直线**（含切线 / 垂线 / 平行线 / 中垂线 / 角平分线）的交点可以精确算，
      // 而且必须跟着预览走 —— 老师报的"切线的性质错位，比如两条切线的交点"就是这里：
      // 切线跟着端点动了、交点还是服务端上一趟算出来的老位置。
      // 有界的（线段 / 曲线 / 圆）不在这里硬算：那不是"直线求交"，算错了更糟，留给服务端。
      if (def.intersect && def.intersect.length === 2) {
        var crossLines = crossUnbounded(objById(def.intersect[0]), objById(def.intersect[1]));
        if (crossLines) {
          obj.value.x = crossLines.x;
          obj.value.y = crossLines.y;
        }
        continue;
      }
      if (anchor) obj.value.point = { x: anchor.x, y: anchor.y };
      if (direction && (direction.x || direction.y)) {
        obj.value.direction = { x: direction.x, y: direction.y };
      }
    }
  }

  /* 两条**无界直线**的交点：2x2 解一下。两边都取"当前"的端点（可能正是预览出来的
     切线的锚点 + 方向），所以交点和画在屏幕上的那两条线对得上。
     只要有一条不是无界直线（线段 / 射线 / 曲线 / 圆），就返回 null —— 那种求交有分支、
     有边界，前端不猜，等 service 端的结果。 */
  function crossUnbounded(first, second) {
    if (!first || !second) return null;
    if (!UNBOUNDED_LINES[first.type] || !UNBOUNDED_LINES[second.type]) return null;
    var one = endpointsOf(first);
    var two = endpointsOf(second);
    if (!one[0] || !one[1] || !two[0] || !two[1]) return null;
    var ax = one[1].x - one[0].x;
    var ay = one[1].y - one[0].y;
    var bx = two[1].x - two[0].x;
    var by = two[1].y - two[0].y;
    var det = ax * by - ay * bx;
    if (Math.abs(det) < 1e-12) return null;            // 平行 / 重合：没有唯一交点
    var t = ((two[0].x - one[0].x) * by - (two[0].y - one[0].y) * bx) / det;
    return { x: one[0].x + ax * t, y: one[0].y + ay * t };
  }

  /* 三角形的心（拖动预览用，只求坐标）：重心 / 内心 / 垂心。

     和服务端 _value_triangle_center 一一对应：重心是三个顶点的平均，
     内心按对边长度加权，垂心用 H = A + B + C - 2O（O 是外心）——
     直接解两条高线会在"高线竖直"时碰除零，这条恒等式不会。 */
  function triangleCenter(kind, one, two, three) {
    var ax = one.x;
    var ay = one.y;
    var bx = two.x;
    var by = two.y;
    var cx = three.x;
    var cy = three.y;
    if (kind === 'centroid') {
      return { x: (ax + bx + cx) / 3, y: (ay + by + cy) / 3 };
    }
    var sideA = Math.sqrt((bx - cx) * (bx - cx) + (by - cy) * (by - cy));
    var sideB = Math.sqrt((ax - cx) * (ax - cx) + (ay - cy) * (ay - cy));
    var sideC = Math.sqrt((ax - bx) * (ax - bx) + (ay - by) * (ay - by));
    var total = sideA + sideB + sideC;
    if (!(total > 1e-12)) return null;                 // 三点全重合
    var area2 = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax);
    // 共线时内心和垂心都没有定义（内切圆半径 0、高线平行），预览也不能编一个点出来
    if (!(Math.abs(area2) > 1e-12)) return null;
    if (kind === 'incenter') {
      return { x: (sideA * ax + sideB * bx + sideC * cx) / total,
               y: (sideA * ay + sideB * by + sideC * cy) / total };
    }
    var a2 = ax * ax + ay * ay;
    var b2 = bx * bx + by * by;
    var c2 = cx * cx + cy * cy;
    var denominator = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by));
    var ox = (a2 * (by - cy) + b2 * (cy - ay) + c2 * (ay - by)) / denominator;
    var oy = (a2 * (cx - bx) + b2 * (ax - cx) + c2 * (bx - ax)) / denominator;
    return { x: ax + bx + cx - 2 * ox, y: ay + by + cy - 2 * oy };
  }

  /* 曲线在某个点上的切线方向：中心差分。参数化走 hostParameter/hostPointAt，
     和服务端 point_on_object 是同一套，所以圆、弧、函数曲线都适用。 */
  function tangentDirection(curveId, spot) {
    var curve = objById(curveId);
    if (!curve) return null;
    var here = hostParameter(curveId, spot);
    if (!here) return null;
    var delta = 0.001;
    var one = hostPointAt(curve, clamp(here.parameter - delta, 0, 1));
    var two = hostPointAt(curve, clamp(here.parameter + delta, 0, 1));
    if (!one || !two) return null;
    if (Math.abs(two.x - one.x) < 1e-12 && Math.abs(two.y - one.y) < 1e-12) return null;
    return { x: two.x - one.x, y: two.y - one.y };
  }

  /* 权威状态画完之后，把本地预览再贴回去（拖动期间才贴）。
     拖动没在跑、或者还没预览过，就什么也不做（返回 false，让调用方照常重画整图）。
     松手后不再贴，最后一次服务端结果就是终点位置。 */
  function reapplyPreview() {
    var drag = state.slide && state.slide.drag;
    if (!drag || !drag.spots) return false;
    if (optimisticMove(drag.ids, drag.spots) && typeof drawGeometry === 'function') {
      drawGeometry();     // 这里必须**立刻**画：权威状态刚重建完，晚一帧就会闪一下旧位置
    }
    return true;
  }

  function isDraggable(obj) {
    if (!obj || obj.type !== 'point' || isIntersection(obj)) return false;
    if (obj.meta && obj.meta.frozen) return false;
    if (!pointOf(obj)) return false;
    var def = obj.definition || {};
    if (def.on) return !!gliderParameter(obj, pointOf(obj), true);
    return typeof def.x === 'number' && typeof def.y === 'number';
  }

  /* 动点在宿主对象上的参数化：线段/射线/直线/向量按投影，圆/弧按角度，函数按 x，
     方程 / 参数 / 极坐标曲线按**弧长占比**（就近投影到采样折线上）。
     宿主类型不认识时返回 null —— 宁可不给拖，也不发一条猜出来的错误指令。
     这里的取参方式必须和服务端 plane_math.point_on_object 一一对应，否则拖起来会跳。 */
  function gliderParameter(obj, point, probe) {
    var def = (obj && obj.definition) || {};
    var host = objById(def.on);
    if (!host) return null;
    var type = host.type;
    if (type === 'segment' || type === 'ray' || type === 'line' || type === 'vector' ||
        LINE_TYPES[type]) {
      var ends = endpointsOf(host);
      if (!ends[0] || !ends[1]) return null;
      var dx = ends[1].x - ends[0].x;
      var dy = ends[1].y - ends[0].y;
      var length2 = dx * dx + dy * dy;
      if (!(length2 > 0)) return null;
      var t = ((point.x - ends[0].x) * dx + (point.y - ends[0].y) * dy) / length2;
      if (type === 'segment') t = clamp(t, 0, 1);
      else if (type === 'ray') t = Math.max(0, t);
      return { kind: 'line', parameter: t };
    }
    if (type === 'circle') {
      var center = centerOf(host);
      var radius = radiusOf(host);
      if (!center || radius === null || !(radius > 0)) return null;
      var angle = Math.atan2(point.y - center.y, point.x - center.x);
      if (angle < 0) angle += Math.PI * 2;
      return { kind: 'circle', parameter: angle / (Math.PI * 2) };
    }
    if (type === 'arc' || type === 'sector') {
      var angles = arcAngles(host);
      var radius2 = radiusOf(host);
      if (!angles || radius2 === null) return null;
      var sweep = angles.to - angles.from;
      while (sweep > Math.PI * 2) sweep -= Math.PI * 2;
      while (sweep < -Math.PI * 2) sweep += Math.PI * 2;
      if (Math.abs(sweep) < 1e-9) return null;
      var current = Math.atan2(point.y - angles.center.y, point.x - angles.center.x);
      var delta = current - angles.from;
      while (delta > Math.PI * 2) delta -= Math.PI * 2;
      while (delta < -Math.PI * 2) delta += Math.PI * 2;
      return { kind: 'arc', parameter: clamp(delta / sweep, 0, 1) };
    }
    if (type === 'function') {
      // 函数曲线：一律"就近取点" —— 服务端已经把曲线采样成折线了（value 里的点），
      // 在这些采样点里找离目标最近的那个，用它的 x 反推参数（服务端就是 x = xmin + (xmax-xmin)·t）。
      // 不直接拿目标点的 x 归一化：曲线陡的地方那样会把点甩出去很远，不符合"就近"。
      var hostRangeUsed = hostRange(host);
      var low = hostRangeUsed.low;
      var high = hostRangeUsed.high;
      if (!(high > low)) return null;
      var groups = sampledSegments(host);
      var best = null;
      var bestDistance = Infinity;
      for (var g = 0; g < groups.length; g++) {
        for (var k = 0; k < groups[g].length; k++) {
          var sample = groups[g][k];
          var gap = (sample.x - point.x) * (sample.x - point.x) +
                    (sample.y - point.y) * (sample.y - point.y);
          if (gap < bestDistance) { bestDistance = gap; best = sample; }
        }
      }
      if (best) return { kind: 'function', parameter: clamp((best.x - low) / (high - low), 0, 1) };
      return { kind: 'function', parameter: clamp((point.x - low) / (high - low), 0, 1) };
    }
    if (ARC_CURVES[type]) {
      // 方程 / 参数 / 极坐标曲线：投影到**最近的那一段折线**上，参数就是弧长占比。
      var found = arcNearest(arcPath(sampledSegments(host)), point);
      if (!found) return null;
      return { kind: 'arc', parameter: clamp(found.parameter, 0, 1) };
    }
    if (probe) return null;
    return null;
  }

  function opForDrag(obj, point) {
    var def = obj.definition || {};
    if (def.on) {
      var glider = gliderParameter(obj, point);
      if (!glider) return null;
      return { op: 'update', id: obj.id, definition: { on: def.on, parameter: round4(glider.parameter) } };
    }
    // 自由点：整数"顿一下"（磁吸 + 迟滞）交给 app.js 的 W7Snap，三处手感才一致。
    // 阈值按屏幕像素算，所以缩放前后的吸附范围一样；perUnit 就是当前的每单位像素数。
    var snap = global.W7Snap;
    var x = point.x;
    var y = point.y;
    if (snap && state.drag) {
      var perUnit = 1 / (state.unit || 1);
      x = snap.axis(state.drag.prevX, snap.value(point.x), perUnit);
      y = snap.axis(state.drag.prevY, snap.value(point.y), perUnit);
      state.drag.prevX = x;   // 迟滞要记住上一帧的结果
      state.drag.prevY = y;
    } else {
      x = round4(x);
      y = round4(y);
    }
    return { op: 'update', id: obj.id, definition: { x: x, y: y } };
  }

  /* 请求合并：一次只发一条，发完再补最后那一条。
     拖动一秒能产生几十上百个位置，全发出去服务端会被打爆，而且返回顺序还没保证。 */
  /* 拖动时两次**提交**之间至少隔这么久。

     手感由本地预览负责，服务端这一路只在后面"对齐真值"。以前是响应一回来马上补发下一条，
     一趟往返 2ms 就是每秒几百次：服务端每次都要整份重算 + 深拷贝 + 序列化，网页这边每次
     响应还要整份回读 + 整图重画 —— 主线程被占满，表现就是"一卡一卡、跟不住鼠标"。
     25 次/秒足够让权威值跟上（预览那一帧已经是手指的位置）。 */
  var OP_GAP_MS = 40;

  function queueOp(op) {
    state.pendingOp = op;
    pumpOps();
  }

  /* 该不该现在发：有排队的、没有在飞的、而且离上次提交够久了。不够久就定个时再试。 */
  function pumpOps() {
    if (state.opTimer || state.inFlight || !state.pendingOp) return;
    var wait = OP_GAP_MS - (Date.now() - (state.opSentAt || 0));
    if (wait > 0) {
      state.opTimer = setTimeout(function () {
        state.opTimer = null;
        pumpOps();
      }, wait);
      return;
    }
    flushOps();
  }

  function flushOps() {
    if (!state.pendingOp) return;
    var op = state.pendingOp;
    state.pendingOp = null;
    state.inFlight = true;
    state.opSentAt = Date.now();
    if (state.slowTimer) clearTimeout(state.slowTimer);
    state.slowTimer = setTimeout(function () {
      state.slowTimer = null;
      if (state.inFlight) showStatus('服务端算得有点慢，正在等它返回…', 'warn', 0);
    }, 400);
    // 兜底：万一请求永远不落地，也不能让界面卡在"正在拖"的状态里
    if (state.hardTimer) clearTimeout(state.hardTimer);
    state.hardTimer = setTimeout(function () {
      state.hardTimer = null;
      if (state.inFlight) {
        state.inFlight = false;
        state.pendingOp = null;
        showStatus('服务端一直没有响应，已经停下这次拖动。', 'err', 8000);
      }
    }, 15000);
    request('/api/plane/apply', { ops: Array.isArray(op) ? op : [op] }).then(function (data) {
      state.inFlight = false;
      if (state.slowTimer) { clearTimeout(state.slowTimer); state.slowTimer = null; }
      if (state.hardTimer) { clearTimeout(state.hardTimer); state.hardTimer = null; }
      applyResult(data);
      if (!state.pendingOp) hideStatus();
      pumpOps();
    }, function (error) {
      state.inFlight = false;
      if (state.slowTimer) { clearTimeout(state.slowTimer); state.slowTimer = null; }
      if (state.hardTimer) { clearTimeout(state.hardTimer); state.hardTimer = null; }
      // 失败就不补发了：把最后那一条丢掉，界面回到服务端的真实状态，
      // 别让画布停在一个后端根本没接受的位置上。
      state.pendingOp = null;
      state.drag = null;
      state.rotate = null;   // 转的那两个端点按的是旧坐标，回读之后不能再按它算
      showStatus('这次拖动没成功：' + error.message + '（已经回到上一次成功的位置）', 'err', 9000);
      loadPlane();
    });
  }

  function onPointerDown(event) {
    if (!state.plane || !state.svg) return;
    if (event.button !== undefined && event.button !== 0) return;
    // 点到画布 = 这一处编辑结束（不提交，跟 Esc 一个意思）：先收掉输入框，
    // 否则下面的重画会被"正在输入"挡住，老师会觉得画面卡住了。
    // （点到右栏 / 按钮那些地方，document 上那个 pointerdown 已经先收过一遍了。）
    blurEditingInput();
    var point = clientToData(event.clientX, event.clientY);
    var hit = hitTest(point);
    // 旋转已经架好（定点 + 目标线都选完了）：按住目标线、或者按在空白处，都算开始转；
    // 按在别的对象上会落到下面 toolClick，当作重新挑一条线 / 换定点。
    if (state.tool === 'rotate' && state.rotate &&
        (!hit || hit.id === state.rotate.target)) {
      startRotate(event, point);
      return;
    }
    if (SLIDE_MODES[state.tool] && state.slide) {
      // 沿线拖要拖哪条线段：按**中点离鼠标最近**挑（老师定的就近原则），
      // 不是按"点到线段本身最近" —— 两条线段挨在一起时那样挑不准。
      var target = segmentForSlide(point);
      if (target) {
        startSlide(event, point, target);
        return;
      }
      if (!hit) {
        // 按在空白处 = 取消这次沿线拖（老师要的"点一下屏幕就复位"）。
        // 按在别的对象上仍然是换轨道，落到下面的 toolClick。
        endSlide();
        showStatus('已取消沿线拖（要再来就点「沿线拖」：先点轨道，再按住线段拖）', 'done', 6000);
        return;
      }
    }
    // 画图工具优先：选中工具后点画布是"画"，不是"选/拖"。
    // 这样拖动手感不会被工具打扰（要拖就先切回「选择」）。
    if (state.tool && state.tool !== 'select') {
      toolClick(hit, point);
      return;
    }
    var start = { xmin: state.view.xmin, ymin: state.view.ymin,
                  xmax: state.view.xmax, ymax: state.view.ymax };
    try { state.svg.setPointerCapture && state.svg.setPointerCapture(event.pointerId); }
    catch (error) { /* 老内核没有 pointer capture：下面 window 上的兜底照样能收尾 */ }
    if (hit && isDraggable(hit)) {
      // prevX/prevY 是整数磁吸的迟滞状态：「每次按下都要清空」，
      // 不清的话上一个点"吸住"的状态会漏到这一次拖动上（W7Snap 明确要求）。
      state.drag = { id: hit.id, moved: false, prevX: null, prevY: null };
      state.pan = null;
      hideStatus();
      return;
    }
    // 没拖动的按下 = 点选：按在对象上选中它，按在空白处取消选中
    state.drag = null;
    state.pan = { x: event.clientX, y: event.clientY, start: start,
                  moved: false, hit: hit ? hit.id : null };
  }

  function onPointerMove(event) {
    if (!state.plane || !state.svg) return;
    if (state.rotate && state.rotate.start !== null) {
      rotateTo(clientToData(event.clientX, event.clientY));
      return;
    }
    if (state.slide && state.slide.drag) {
      slideTo(clientToData(event.clientX, event.clientY));
      return;
    }
    if (state.drag) {
      var point = clientToData(event.clientX, event.clientY);
      var obj = objById(state.drag.id);
      if (!obj) return;
      var op = opForDrag(obj, point);
      if (!op) {
        state.drag = null;
        showStatus('这个点不能直接拖：网页端不知道怎么把鼠标位置换算成它的参数。', 'warn', 7000);
        return;
      }
      state.drag.moved = true;
      queueOp(op);
      return;
    }
    if (state.pan) {
      var metrics = viewBoxMetrics();
      var dx = (event.clientX - state.pan.x) / (metrics.scale || 1);
      var dy = (event.clientY - state.pan.y) / (metrics.scale || 1);
      if (Math.abs(dx) > 0.5 || Math.abs(dy) > 0.5) {
        state.pan.moved = true;
        state.svg.classList.add('panning');
      }
      var start = state.pan.start;
      state.view = { xmin: start.xmin - dx, xmax: start.xmax - dx,
                     ymin: start.ymin + dy, ymax: start.ymax + dy,
                     grid: state.view.grid, axes: state.view.axes };
      refreshViewOnly();
      maybeSyncView();
      return;
    }
    // 没按键：把光标换成"可抓"提示
    var hover = hitTest(clientToData(event.clientX, event.clientY));
    var hits = state.svg.querySelectorAll('.plane-hit');
    for (var index = 0; index < hits.length; index++) {
      var active = !!(hover && hits[index].getAttribute('data-plane-id') === hover.id);
      hits[index].setAttribute('class', 'plane-hit ' + (active ? 'grab' : ''));
    }
  }

  function endPointer(event) {
    if (state.rotate && state.rotate.start !== null) {
      var done = state.rotate;
      done.start = null;        // 这一次拖完了；工具还架着，能接着再转
      if (done.moved) {
        showStatus('转好了：' + done.target + ' 绕 ' + done.center + ' 转过来了，接着拖还能继续转',
                   'done', 5000);
      }
    }
    if (state.pan) {
      if (!state.pan.moved) {
        if (state.pan.hit) selectObject(state.pan.hit);
        else if (state.selected) selectObject(null);
      }
      state.pan = null;
    }
    if (state.slide && state.slide.drag) {
      // 拖动期间被限流挡掉的那些"整份对齐"，在这里补上：松手后必须落到服务端的真值上。
      var owed = state.slide.drag.owed;
      state.slide.drag = null;      // 松手结束这一次滑动；轨道留着，能接着再拖
      if (owed) loadPlane();
      // 拖动期间冻结的右栏 / 信息区，这里补一次
      refreshChrome();
    }
    if (state.drag) {
      var dragged = state.drag;
      state.drag = null;
      // 按下又没挪动 = 点了一下：选中它
      if (!dragged.moved && state.selected !== dragged.id) selectObject(dragged.id);
    }
    if (state.svg) {
      state.svg.classList.remove('panning');
      try { state.svg.releasePointerCapture && state.svg.releasePointerCapture(event.pointerId); }
      catch (error) { /* 没捕获过就算了 */ }
    }
  }

  function onWheel(event) {
    if (!state.plane || !state.svg || !state.view) return;
    if (event.preventDefault) event.preventDefault();
    var point = clientToData(event.clientX, event.clientY);
    var factor = event.deltaY > 0 ? 1.12 : (event.deltaY < 0 ? 1 / 1.12 : 1);
    zoomAt(point, factor);
  }

  function zoomAt(point, factor) {
    var view = state.view;
    var xmin = point.x + (view.xmin - point.x) * factor;
    var xmax = point.x + (view.xmax - point.x) * factor;
    var ymin = point.y + (view.ymin - point.y) * factor;
    var ymax = point.y + (view.ymax - point.y) * factor;
    if (!(xmax - xmin > 1e-6) || !(ymax - ymin > 1e-6)) return;
    state.view = { xmin: xmin, ymin: ymin, xmax: xmax, ymax: ymax,
                   grid: view.grid, axes: view.axes };
    // 字号/半径是按屏幕像素折算的：缩放之后必须整块重画一次
    redraw();
    maybeSyncView();
  }

  /* -------------------------------------------------- 曲线"无限"：视野同步 */

  /* 服务端把可见范围往两边各多采 AUTO_WINDOW_MARGIN 屏（见 plane.py 里的同名常量，
     两边必须一致），所以视野在采样范围内挪动不用重新采样；走出去了才报一次新的范围。 */
  var AUTO_WINDOW_MARGIN = 2.0;

  /* 哪些曲线是"自动区间"：只有它们才需要跟着视野重采样。
     函数是没写 xmin/xmax 的那种；隐式方程和不等式区域**钉不了区间**
     （它们吃的是整块二维采样框），所以一直算自动的 —— 老师写 y=x^2 这种方程
     能看到"只画了一小截"，就是这里没跟着视野走的缘故。 */
  var AUTO_RANGE_TYPES = { function: 1, implicit: 1, inequality: 1 };

  function autoRangeCurves() {
    var objects = (state.plane && state.plane.objects) || {};
    var found = [];
    for (var id in objects) {
      if (!Object.prototype.hasOwnProperty.call(objects, id)) continue;
      var obj = objects[id];
      if (!obj || !AUTO_RANGE_TYPES[obj.type]) continue;
      if (obj.type === 'function') {
        var def = obj.definition || {};
        if (typeof def.xmin === 'number' && typeof def.xmax === 'number') continue;
      }
      found.push(id);
    }
    return found;
  }

  function maybeSyncView() {
    var view = state.view;
    if (!view || !state.plane) return;
    if (!autoRangeCurves().length) return;   // 没有自动区间的曲线：不麻烦服务端
    var synced = state.syncedView;
    if (synced) {
      var width = synced.xmax - synced.xmin;
      var height = synced.ymax - synced.ymin;
      var low = synced.xmin - width * AUTO_WINDOW_MARGIN;
      var high = synced.xmax + width * AUTO_WINDOW_MARGIN;
      var bottom = synced.ymin - height * AUTO_WINDOW_MARGIN;
      var top = synced.ymax + height * AUTO_WINDOW_MARGIN;
      var padX = (high - low) * 0.1;         // 留一成余量：贴着边也算快走出去了
      var padY = (top - bottom) * 0.1;
      if (view.xmin >= low + padX && view.xmax <= high - padX
          && view.ymin >= bottom + padY && view.ymax <= top - padY) return;
    }
    if (state.viewSyncTimer) clearTimeout(state.viewSyncTimer);
    // 拖一下能产生几十次视野变化：停手 250 毫秒再报一次，别把服务端打爆
    state.viewSyncTimer = setTimeout(syncView, 250);
  }

  function syncView() {
    state.viewSyncTimer = null;
    var view = state.view;
    if (!view) return;
    state.syncedView = { xmin: view.xmin, xmax: view.xmax, ymin: view.ymin, ymax: view.ymax };
    request('/api/plane/window', { xmin: view.xmin, xmax: view.xmax,
                                   ymin: view.ymin, ymax: view.ymax })
      .then(function () {
        reseedSlideCache();
        return loadPlane();
      },
            function () { /* 同步视野失败不影响本地看图，下次动视野再报 */ });
  }

  /* 视野一同步，自动区间的函数就是按新范围重采的：参数 t 的含义变了，
     按下时缓存的轨道也过期了。不重标的话，拖动中会出现"突然移位"，之后一直对不上手指。
     重标的做法：重采缓存，然后按两个端点**现在的位置**反推参数（位置不动，参数变）。 */
  function reseedSlideCache() {
    var slide = state.slide;
    var drag = slide && slide.drag;
    if (!drag) return;
    var host = objById(slide.host);
    if (!host) return;
    var fresh = hostCache(host);
    if (!fresh) return;
    drag.cache = fresh;
    // 手指那头：用最后接受的手指位置反推（不是用端点，按下时的相对位置才保持得住）
    if (drag.anchor) drag.where = cacheProject(fresh, drag.anchor, drag.where, 1, drag.wrap);
    var leader = pointOf(objById(drag.ids[0]));
    var follower = pointOf(objById(drag.ids[1]));
    if (leader) drag.base[0] = cacheProject(fresh, leader, drag.base[0], 1, drag.wrap);
    if (follower) drag.base[1] = cacheProject(fresh, follower, drag.base[1], 1, drag.wrap);
    drag.anchor = null;          // 死区重新起算，别把同步前的手指位置当成门槛
    drag.stuck = false;
    drag.spots = null;           // 这一帧的预览按新参数重出（但位置是一样的）
  }

  function resetView() {
    state.view = viewFromPlane(state.plane);
    redraw();
    maybeSyncView();
    showStatus('视野已复位', 'done', 2000);
  }

  function fitView() {
    if (!state.svg || !state.plane) return;
    var layers = state.layers;
    var boxes = [];
    var names = ['regions', 'curves', 'shapes', 'points'];
    for (var index = 0; index < names.length; index++) {
      var group = layers[names[index]];
      if (!group || !group.childNodes.length) continue;
      try {
        var box = group.getBBox();
        if (box && (box.width > 0 || box.height > 0)) boxes.push(box);
      } catch (error) { /* 空组没 bbox，跳过 */ }
    }
    if (!boxes.length) { resetView(); return; }
    var xmin = Infinity, xmax = -Infinity, ymin = Infinity, ymax = -Infinity;
    for (var position = 0; position < boxes.length; position++) {
      var item = boxes[position];
      // getBBox 的单位是数据坐标，但 y 被我们翻过：minY/maxY 要对调
      xmin = Math.min(xmin, item.x);
      xmax = Math.max(xmax, item.x + item.width);
      ymin = Math.min(ymin, -(item.y + item.height));
      ymax = Math.max(ymax, -item.y);
    }
    if (!(xmax > xmin) || !(ymax > ymin)) { resetView(); return; }
    var padX = (xmax - xmin) * 0.08 + 1e-6;
    var padY = (ymax - ymin) * 0.08 + 1e-6;
    state.view = { xmin: xmin - padX, xmax: xmax + padX, ymin: ymin - padY, ymax: ymax + padY,
                   grid: state.view.grid, axes: state.view.axes };
    redraw();
    maybeSyncView();
    showStatus('已缩放到全部对象', 'done', 2000);
  }

  /* ------------------------------------------------------------ 与服务端同步 */

  function loadPlane() {
    return request('/api/plane/state').then(function (data) {
      state.serverReady = true;
      state.serverError = '';
      renderPlane(data.state);
      hideStatus();
      return data;
    }, function (error) {
      state.serverReady = false;
      state.serverError = '读不到平面状态：' + error.message;
      showStatus(state.serverError, 'err', 0);
      renderInfo();
      renderAlgebra();
      setNote('平面模块还没就绪：' + error.message);
    });
  }

  function scheduleRefresh() {
    if (state.refreshTimer) return;
    state.refreshTimer = setTimeout(function () {
      state.refreshTimer = null;
      if (state.opened) refreshForDrag();
    }, 150);
  }

  /* app.js 收到 SSE 的 {"t":"plane", ...} 就调这里。
     服务端只推"有新版本了"（不搬状态），所以这里比一下版本号再自己去取。 */
  function handlePlaneEvent(event) {
    if (!event) return;
    if (event.state && typeof event.state === 'object') { renderPlane(event.state); return; }
    if (event.hello && event.ready === false && event.error) {
      state.serverReady = false;
      state.serverError = '平面模块还没就绪：' + event.error;
      if (state.opened) { renderInfo(); renderAlgebra(); }
      return;
    }
    if (!state.opened || !state.plane) return;
    if (typeof event.version === 'number' && event.version === state.plane.version) return;
    scheduleRefresh();
  }

  function beginStream() {
    if (state.streaming || !global.fetch || !global.ReadableStream) return;
    state.streaming = true;
    var options = {};
    if (TOKEN) options.headers = { 'X-Auth-Token': TOKEN };
    fetch('/api/plane/events', options).then(function (response) {
      if (!response.ok || !response.body) throw new Error('平面同步连不上');
      var reader = response.body.getReader();
      var decoder = new TextDecoder();
      var buffer = '';
      function pump() {
        return reader.read().then(function (chunk) {
          if (chunk.done) throw new Error('平面同步已断开');
          buffer += decoder.decode(chunk.value, { stream: true });
          var index;
          while ((index = buffer.indexOf('\n\n')) >= 0) {
            var frame = buffer.slice(0, index);
            buffer = buffer.slice(index + 2);
            var lines = frame.split('\n');
            for (var position = 0; position < lines.length; position++) {
              if (lines[position].indexOf('data:') !== 0) continue;   // ': keepalive' 之类的注释跳过
              var payload = lines[position].slice(5).trim();
              if (!payload) continue;
              var parsed = null;
              try { parsed = JSON.parse(payload); } catch (error) { parsed = null; }
              if (parsed) handlePlaneEvent(parsed);
            }
          }
          return pump();
        });
      }
      return pump();
    })['catch'](function () {
      state.streaming = false;
      // 断了就过一会儿再连：服务端重启、或者本来就没起来，都不该让面板变砖
      if (state.opened) setTimeout(beginStream, 5000);
    });
  }

  /* ------------------------------------------------------------ 导出 */

  function exportText(format) {
    if (format === 'json') {
      return JSON.stringify(state.plane || {}, null, 2);
    }
    if (!state.svg) return '';
    var clone = state.svg.cloneNode(true);
    // 把屏幕上的辅助元素（透明大点击圈）和选中态去掉，导出的是一张干净的图
    var hits = clone.querySelectorAll('.plane-hit');
    for (var index = 0; index < hits.length; index++) {
      hits[index].parentNode.removeChild(hits[index]);
    }
    clone.setAttribute('xmlns', NS);
    clone.setAttribute('xmlns:xlink', 'http://www.w3.org/1999/xlink');
    var view = state.view;
    var boxW = view ? view.xmax - view.xmin : 1;
    var width = 1200;
    var height = Math.round(width * (view ? (view.ymax - view.ymin) / boxW : 0.75)) || 900;
    clone.setAttribute('width', width);
    clone.setAttribute('height', height);
    var style = svgNode('style', {});
    style.textContent = EXPORT_CSS;
    clone.insertBefore(style, clone.firstChild);
    var background = svgNode('rect', { class: 'plane-export-bg',
      x: view ? view.xmin : 0, y: view ? -view.ymax : 0, width: boxW,
      height: view ? view.ymax - view.ymin : 1 });
    clone.insertBefore(background, style.nextSibling);
    return '<?xml version="1.0" encoding="UTF-8"?>\n' +
           new XMLSerializer().serializeToString(clone);
  }

  function download(filename, href) {
    var link = document.createElement('a');
    link.setAttribute('download', filename);
    link.setAttribute('href', href);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  }

  function downloadText(filename, mime, text) {
    if (!global.Blob || !global.URL || !global.URL.createObjectURL) {
      // 老内核没有 Blob/对象地址：退到 data: 地址，同一个 <a download>
      download(filename, 'data:' + mime + ';charset=utf-8,' + encodeURIComponent(text));
      return;
    }
    var url = global.URL.createObjectURL(new Blob([text], { type: mime }));
    download(filename, url);
    setTimeout(function () { global.URL.revokeObjectURL(url); }, 30000);
  }

  function stamp() {
    var now = new Date();
    var pad = function (value) { return (value < 10 ? '0' : '') + value; };
    return now.getFullYear() + pad(now.getMonth() + 1) + pad(now.getDate()) + '-' +
           pad(now.getHours()) + pad(now.getMinutes());
  }

  function exportSvg() {
    var text = exportText('svg');
    if (!text) { showStatus('还没有画布可以导出', 'warn', 5000); return; }
    downloadText('平面-' + stamp() + '.svg', 'image/svg+xml', text);
    showStatus('已导出 SVG', 'done', 3000);
  }

  /* PNG 在网页端做：把画布序列化成 SVG，画到 canvas，再导出位图。
     服务端是纯标准库，不做 PNG 编码（那要自己写一遍 zlib+PNG 头）。 */
  function pngDataUrl() {
    return new Promise(function (resolve, reject) {
      var text = exportText('svg');
      if (!text) { reject(new Error('还没有画布可以导出')); return; }
      var image = new Image();
      image.onload = function () {
        var canvas = document.createElement('canvas');
        canvas.width = image.width || 1200;
        canvas.height = image.height || 900;
        var context = canvas.getContext('2d');
        context.fillStyle = '#ffffff';
        context.fillRect(0, 0, canvas.width, canvas.height);
        context.drawImage(image, 0, 0);
        try { resolve(canvas.toDataURL('image/png')); }
        catch (error) { reject(new Error('浏览器不允许读取这张画布：' + error.message)); }
      };
      image.onerror = function () { reject(new Error('浏览器无法把这张图转成位图')); };
      image.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(text);
    });
  }

  function exportPng() {
    showStatus('正在生成 PNG…', '', 0);
    pngDataUrl().then(function (url) {
      download('平面-' + stamp() + '.png', url);
      showStatus('已导出 PNG', 'done', 3000);
    }, function (error) {
      showStatus('导出 PNG 失败：' + error.message, 'err', 8000);
    });
  }

  function exportJson() {
    if (!state.plane) { showStatus('还没有状态可以导出', 'warn', 5000); return; }
    downloadText('平面-' + stamp() + '.json', 'application/json', exportText('json'));
    showStatus('已导出 JSON', 'done', 3000);
  }

  /* ------------------------------------------------------------ 面板 */

  function openPanel() {
    var panel = $('#planePanel');
    if (!panel) return;
    panel.classList.remove('hidden');
    state.opened = true;
    // 先把作图面板收掉：两个都是整屏面板，叠在一起老师会以为界面坏了
    var figure = $('#figurePanel');
    if (figure && !figure.classList.contains('hidden')) figure.classList.add('hidden');
    if (!state.svg) buildSvg();
    beginStream();
    loadPlane();
    var focusTarget = $('#planeCanvas');
    if (focusTarget && focusTarget.focus) focusTarget.focus();
  }

  function closePanel() {
    var panel = $('#planePanel');
    if (panel) panel.classList.add('hidden');
    state.drag = null;
    state.pan = null;
  }

  function togglePanel() {
    var panel = $('#planePanel');
    if (!panel) return;
    if (panel.classList.contains('hidden')) openPanel();
    else closePanel();
  }

  /* ------------------------------------------------------------ 诊断（无头自检用） */

  function countBy(predicate) {
    var list = state.svg ? state.svg.querySelectorAll('[data-plane-kind]') : [];
    var total = 0;
    for (var index = 0; index < list.length; index++) {
      if (predicate(list[index].getAttribute('data-plane-kind'))) total += 1;
    }
    return total;
  }

  function rendererMode() {
    /* 基线永远是 SVG：不引 WebGL 依赖。这里只探一下能力，方便以后需要时切换，
       探测失败不影响任何功能（Win7 上的 360 极速内核本来也够用）。 */
    var webgl = false;
    try {
      var probe = document.createElement('canvas');
      webgl = !!(probe.getContext && (probe.getContext('webgl') ||
                                      probe.getContext('experimental-webgl')));
    } catch (error) { webgl = false; }
    return { mode: 'svg', webgl: webgl };
  }

  function diagnostics() {
    var svg = state.svg;
    var box = null;
    var rect = null;
    if (svg) {
      rect = svg.getBoundingClientRect();
      if (state.layers && state.layers.points) {
        try { box = state.layers.points.getBBox(); } catch (error) { box = null; }
      }
    }
    return {
      version: state.plane ? state.plane.version : null,
      objects: orderedObjects().length,
      rendered: state.rendered,
      selected: state.selected,
      warnings: state.warnings.slice(0),
      rows: Object.keys(state.rows).length,
      view: state.view ? [round4(state.view.xmin), round4(state.view.ymin),
                          round4(state.view.xmax), round4(state.view.ymax)] : null,
      viewBox: svg ? svg.getAttribute('viewBox') : null,
      svgRect: rect ? [Math.round(rect.width), Math.round(rect.height)] : null,
      pointsBBox: box ? [box.x, box.y, box.width, box.height] : null,
      counts: {
        points: countBy(function (kind) { return kind === 'point'; }),
        intersects: countBy(function (kind) { return kind === 'intersect'; }),
        sliders: countBy(function (kind) { return kind === 'slider'; }),
        labels: svg ? svg.querySelectorAll('.plane-label').length : 0,
        curves: svg ? svg.querySelectorAll('.plane-curve').length : 0,
        shapes: svg ? svg.querySelectorAll('.plane-shape').length : 0,
        regions: svg ? svg.querySelectorAll('.plane-region').length : 0,
        grids: svg ? svg.querySelectorAll('.plane-grid').length : 0,
        hits: svg ? svg.querySelectorAll('.plane-hit').length : 0
      },
      serverReady: state.serverReady,
      serverError: state.serverError,
      // 交互走哪一路事件（自检与老师排查用）：pointer = Pointer Events，mouse = 老内核兜底
      input: INPUT.mode,
      inputWhy: INPUT.why,
      dragging: !!state.drag,
      // 沿线拖的内部状态（自检用：拖动时到底谁在动、参数是多少）
      slide: (state.slide && state.slide.drag) ? {
        host: state.slide.host,
        ids: state.slide.drag.ids.join(','),
        leader: round4(state.slide.drag.base[0]),
        follower: round4(state.slide.drag.base[1]),
        where: round4(state.slide.drag.where),
        side: state.slide.drag.side,
        grab: round4(state.slide.drag.grab),
        spots: (state.slide.drag.spots || []).map(function (item) {
          return [round4(item.x), round4(item.y)];
        })
      } : null,
      inFlight: state.inFlight,
      renderer: rendererMode()
    };
  }

  /* ------------------------------------------------------------ 启动 */

  function boot() {
    // 顶栏的「平面」按钮已经去掉（老师要求：平面合并进画板，顶栏只留「作图」一个入口）。
    // 入口由 app.js 绑在 #btnFigure 上，那里会优先让路给我们（window.W7Plane.open）。
    var close = $('#planeClose');
    if (close) close.onclick = closePanel;
    var undo = $('#planeUndo');
    if (undo) undo.onclick = function () { simpleAction('/api/plane/undo', '撤销'); };
    var redo = $('#planeRedo');
    if (redo) redo.onclick = function () { simpleAction('/api/plane/redo', '重做'); };
    var refresh = $('#planeRefresh');
    if (refresh) refresh.onclick = function () { state.view = null; loadPlane(); };
    var toolButtons = document.querySelectorAll('.plane-tool');
    for (var toolIndex = 0; toolIndex < toolButtons.length; toolIndex++) {
      (function (button) {
        button.onclick = function () {
          var name = button.getAttribute('data-plane');
          setTool(name);
          // 选中了东西再点「追踪」= 直接给选中的那个开追踪，不用再去画布上点它；
          // 没选中就还是老样子：点了工具，再去画布上点那个点。
          if (name === 'trace' && state.selected) traceObject(state.selected);
        };
      })(toolButtons[toolIndex]);
    }
    var done = $('#planeDone');
    if (done) done.onclick = finishPoly;
    var clear = $('#planeClear');
    if (clear) clear.onclick = clearPlane;
    var draw = $('#planeDraw');
    if (draw) draw.onclick = drawEquation;
    var input = $('#planeInput');
    if (input) {
      input.addEventListener('keydown', function (event) {
        if (event.key === 'Enter') { event.preventDefault(); drawEquation(); }
      });
    }
    var toolUndo = $('#planeToolUndo');
    if (toolUndo) { toolUndo.onclick = function () { simpleAction('/api/plane/undo', '撤销'); }; }
    setTool('select');
    /* 老师要求：「把平面做进几何画板模块」。所以顶栏那个「作图」按钮直接开平面 ——
       平面就是现在的几何画板（点/线/圆/函数/方程/交点/动点/轨迹都在这里）。
       旧的作图面板留着画立体：AI 那边的 show_figure 也只剩 kind=solid 了，两边一致。
       plane.js 在 app.js 之后加载，所以这里的绑定会覆盖 app.js 里的那个。 */
    var figureButton = $('#btnFigure');
    if (figureButton) {
      figureButton.onclick = function () { openPanel(); };
      figureButton.title = '几何画板：点、线、圆、函数、方程、交点、动点、轨迹（立体在右上角的「立体」里）';
    }
    var fit = $('#planeFit');
    if (fit) fit.onclick = fitView;
    var reset = $('#planeReset');
    if (reset) reset.onclick = resetView;
    var sideToggle = $('#planeSideToggle');
    if (sideToggle) {
      sideToggle.onclick = function () {
        var side = $('#planeSide');
        side.classList.toggle('hidden');
        sideToggle.classList.toggle('on', !side.classList.contains('hidden'));
        redraw();
      };
    }
    var svgButton = $('#planeExportSvg');
    if (svgButton) svgButton.onclick = exportSvg;
    var pngButton = $('#planeExportPng');
    if (pngButton) pngButton.onclick = exportPng;
    var jsonButton = $('#planeExportJson');
    if (jsonButton) jsonButton.onclick = exportJson;

    var canvas = $('#planeCanvas');
    if (canvas) {
      /* 老内核兜底（见 INPUT）：没有 PointerEvent 就用鼠标事件，绝不让整块交互静默失效。
         mouse 那一路要显式收尾：手指/鼠标移出画布时不会再收到 move（pointer 有
         setPointerCapture，鼠标事件没有），不补一个 mouseleave 就会一直卡在"正在拖"。 */
      listen(canvas, INPUT.down, onPointerDown);
      listen(canvas, INPUT.move, onPointerMove);
      listen(canvas, INPUT.up, endPointer);
      listen(canvas, INPUT.cancel, endPointer);
      // 指针捕获掉了（切窗口、系统弹窗）也要收尾，不然会卡在"正在拖"
      listen(canvas, INPUT.lost, endPointer);
      listen(canvas, 'wheel', onWheel, { passive: false });
    }
    /* 点到别处（右栏另一行、工具按钮、画布…）= 结束这一处编辑，但不提交。
       必须是捕获阶段：右栏行的 onclick、画布自己的 pointerdown 都在后面；
       不先收掉输入框的话，它们触发的重画会被"正在输入"挡掉（老师会以为界面卡死）。 */
    listen(document, INPUT.down, function (event) {
      var input = editingInput();
      if (!input) return;
      var form = editFormOf(input);
      if (form && form.contains && event.target && form.contains(event.target)) return;
      blurEditingInput();
    }, true);
    // 手指/鼠标在画布外面松开也要收尾，不然界面会一直停在"正在拖"的状态。
    // **沿线拖也要算进来**：漏掉这次 pointerup 的话，那段线就"没按着也一直跟着鼠标走"，
    // 点哪儿都停不下来（老师报的"轻轻一拖就跑很远、点屏幕也复位不了"就是它）。
    var busyDragging = function () {
      return !!(state.drag || state.pan || (state.slide && state.slide.drag));
    };
    listen(window, INPUT.up, function (event) {
      if (busyDragging()) endPointer(event);
    });
    listen(window, INPUT.cancel, function (event) {
      if (busyDragging()) endPointer(event);
    });
    window.addEventListener('blur', function () {
      state.drag = null;
      state.pan = null;
      if (state.slide) state.slide.drag = null;   // 切窗口 = 这次滑动作废（轨道留着，能接着再拖）
      if (state.svg) state.svg.classList.remove('panning');
    });
    // 画布尺寸一变就整块重画：字号和点半径是"屏幕像素折算成数据单位"写进 SVG 的，
    // 用布局中途量到的尺寸渲染，字会大得离谱（无头自检真抓到过满屏巨字）。
    // ResizeObserver 能同时覆盖"窗口缩放"和"面板内部布局落定"两种情况。
    var relayout = function () {
      if (!state.opened || !state.plane || !state.svg || !state.renderedBox) return;
      var rect = state.svg.getBoundingClientRect();
      if (Math.abs(rect.width - state.renderedBox.w) > 1 ||
          Math.abs(rect.height - state.renderedBox.h) > 1) {
        redraw();
      }
    };
    if (global.ResizeObserver) {
      var observer = new ResizeObserver(function () {
        if (state.pendingRelayout) return;
        state.pendingRelayout = true;
        // 折到下一帧：重画本身可能再改一次尺寸，用一帧的间隔把抖动收敛掉。
        // 走 frame() 而不是直接 rAF：老内核没有 rAF 时回调永不执行，
        // 面板尺寸落定后的重排就再也不会发生（点半径/字号都按上屏尺寸折算，会全画错）。
        frame(function () {
          state.pendingRelayout = false;
          relayout();
        });
      });
      observer.observe($('#planeCanvas'));
    } else {
      var resizeTimer = null;
      window.addEventListener('resize', function () {
        if (resizeTimer) clearTimeout(resizeTimer);
        resizeTimer = setTimeout(function () {
          resizeTimer = null;
          relayout();
        }, 200);
      });
    }
    // Esc 关面板。注册在 app.js 之前（本文件在 DOMContentLoaded 之前执行），
    // 所以这里 stopImmediatePropagation 能拦住后面那个全局 Esc（不然会顺手把对话停掉）。
    document.addEventListener('keydown', function (event) {
      if (event.key !== 'Escape') return;
      var panel = $('#planePanel');
      if (!panel || panel.classList.contains('hidden')) return;
      if (document.querySelector('dialog[open]')) return;
      closePanel();
      if (event.stopImmediatePropagation) event.stopImmediatePropagation();
      event.stopPropagation();
    });
  }

  global.W7Plane = {
    open: openPanel,
    close: closePanel,
    toggle: togglePanel,
    refresh: loadPlane,
    // app.js 把 SSE 的 {"t":"plane"} 事件转到这儿
    onEvent: handlePlaneEvent,
    // 自检用：导出文本与诊断信息（不是测试专用的旁路，导出按钮走的是同一份实现）
    exportText: exportText,
    pngDataUrl: pngDataUrl,
    diagnostics: diagnostics,
    exportSvg: exportSvg,
    exportPng: exportPng,
    exportJson: exportJson,
    // 自检 / 老师排查用：现在这条交互走的是 Pointer Events 还是鼠标兜底
    input: inputCapability
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})(typeof window === 'undefined' ? globalThis : window);
