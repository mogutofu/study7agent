/* 物理沙盒（M6.5 面板）：自己画轨道/场/电池，放物块，看它像高考大题那样动。
 *
 * 三件事各归各位：
 *   * 引擎 `webui\sim2d.py` 负责物理（数值积分 + 能量对账）；
 *   * 这里负责**画**和**把鼠标动作翻译成场景 JSON**（就是引擎的输入格式，两边不各写一份定义）；
 *   * 交互习惯照搬平面几何面板（`plane.js`）：点几下画线、悬停显示坐标、按住能拖、
 *     点一下选中再改数据、可选吸附网格。没照搬的是自由缩放/平移（沙盒固定 6 m × 3 m 视野）。
 *
 * 坐标：世界 6 m × 3 m，x 向右、y 向上；1 m = 140 px（画布 840×420）。
 * 屏幕坐标一律用 SVG 自己的 CTM 反算（`getScreenCTM().inverse()`）——之前用
 * `getBoundingClientRect` 手算，画布被 CSS 缩放/letterbox 后点哪儿都偏，物块就"错位"了。
 */
(function () {
  'use strict';

  var W = 6.0, H = 3.0, PX = 140;
  var SVG_W = W * PX, SVG_H = H * PX;
  var GRID = 0.1;                       // 默认吸附网格（面板上可以换）
  var VIEW = { zoom: 1, x: 0, y: 0 };   // 视野：只改 viewBox，不动场景数据

  function $(selector) { return document.querySelector(selector); }
  function num(selector, fallback) {
    var node = $(selector);
    var value = node ? parseFloat(node.value) : NaN;
    return isNaN(value) ? fallback : value;
  }

  /* 缩放平移只改 viewBox（和平面几何面板一个做法）：数据不动，重画也不丢。
     视野的**宽高比跟着画布容器走**——不然 viewBox 是 2:1、容器一比它高，SVG 会等比缩放着留白，
     看着就"只有上半部分有东西"。地面（user y=420）贴底，多出来的高度算"天上"。 */
  /* 画布尺寸按容器实测像素写进 width/height 属性（不靠 CSS 百分比：SVG 会按 viewBox
     比例自己算高，容器高一点就白白空出一大块）。viewBox 的宽高比再跟着容器走。 */
  function sizeCanvas() {
    var svg = $('#sbScene');
    var host = $('#sandboxPanel .sandbox-canvas');
    if (!svg || !host) return null;
    var box = host.getBoundingClientRect();
    if (box.width > 1 && box.height > 1) {
      svg.setAttribute('width', Math.round(box.width));
      svg.setAttribute('height', Math.round(box.height));
    }
    return box;
  }

  function viewSize() {
    /* 视野**至少要盖住 6 m x 3 m 的整个世界**（不然 y=2 m 以上的悬挂点、物块会被裁掉，
       老师反馈过"物块不见了"就是这个）；再按画布容器的宽高比把多出来的方向补满：
       画布又宽又扁就向左右多给一点，画布偏方就向上多给一点。宽高比跟容器一致，
       SVG 不用 letterbox，网格能铺满整块画布，鼠标缩放时光标下的点也不会漂。 */
    var width = SVG_W / VIEW.zoom, height = SVG_H / VIEW.zoom;
    var svg = $('#sbScene');
    var box = svg && svg.getBoundingClientRect ? svg.getBoundingClientRect() : null;
    if (box && box.width > 1 && box.height > 1) {
      var aspect = box.width / box.height;
      if (aspect > SVG_W / SVG_H) {
        width = height * aspect;
      } else {
        height = width / aspect;
      }
    }
    return [width, height];
  }

  function applyView() {
    var svg = $('#sbScene');
    if (!svg) return;
    /* 先按容器实测像素把 SVG 的 width/height 写死：不写的话它按 viewBox 的 2:1 算高，
       画布一变矮（下面多了图像区）就把下半截裁掉，板、轨道、地面全看不见。 */
    sizeCanvas();
    var size = viewSize();
    if (!VIEW.inited) {
      VIEW.x = (SVG_W - size[0]) / 2;
      VIEW.y = SVG_H - size[1];
      VIEW.inited = true;
    }
    var slackX = size[0] * 0.8, slackY = size[1] * 0.8;      // 允许拖出去一点，但别拖没了
    VIEW.x = Math.max(-slackX, Math.min(SVG_W - size[0] + slackX, VIEW.x));
    VIEW.y = Math.max(-slackY, Math.min(SVG_H - size[1] + slackY, VIEW.y));
    svg.setAttribute('viewBox', VIEW.x + ' ' + VIEW.y + ' ' + size[0] + ' ' + size[1]);
    var label = $('#sbZoomLabel');
    if (label) label.textContent = Math.round(VIEW.zoom * 100) + '%';
  }

  /* 把一段 SVG 文本里的线宽/字号/把手半径按 1/zoom 缩放，屏幕上粗细恒定 */
  function scaleStrokes(markup, factor) {
    return markup.replace(/stroke-width="([\d.]+)"/g, function (all, value) {
      return 'stroke-width="' + (parseFloat(value) * factor).toFixed(3) + '"';
    }).replace(/font-size="([\d.]+)"/g, function (all, value) {
      return 'font-size="' + (parseFloat(value) * factor).toFixed(3) + '"';
    }).replace(/r="5"/g, 'r="' + (5 * factor).toFixed(3) + '"');
  }

  function zoomAt(factor, clientX, clientY) {
    var svg = $('#sbScene');
    if (!svg || !svg.getScreenCTM) return;
    var point = null;
    var box = svg.getBoundingClientRect();
    if (clientX !== undefined && box.width) {
      var user = svg.createSVGPoint();
      user.x = clientX;
      user.y = clientY;
      point = user.matrixTransform(svg.getScreenCTM().inverse());
    }
    VIEW.zoom = Math.max(0.4, Math.min(40, VIEW.zoom * factor));
    var size = viewSize();
    if (point) {                         // 让光标下的那个点待在原地
      VIEW.x = point.x - (clientX - box.left) * (size[0] / box.width);
      VIEW.y = point.y - (clientY - box.top) * (size[1] / box.height);
    } else {
      VIEW.x = (SVG_W - size[0]) / 2;
      VIEW.y = SVG_H - size[1];
    }
    applyView();
    drawScene();
  }

  function resetView() {
    VIEW.zoom = 1;
    VIEW.inited = false;
    applyView();
    drawScene();
  }

  /* ------------------------------------------------------------ 坐标换算 */

  function toWorld(event) {
    var svg = $('#sbScene');
    if (!svg || !svg.getScreenCTM) return [0, 0];
    var point = svg.createSVGPoint();
    point.x = event.clientX;
    point.y = event.clientY;
    var user = point.matrixTransform(svg.getScreenCTM().inverse());
    return [user.x / PX, (SVG_H - user.y) / PX];
  }

  function toScreen(point) { return [point[0] * PX, SVG_H - point[1] * PX]; }

  function snapPoint(point) {
    var grid = num('#sbSnapPick', GRID);
    if (!grid) return point;
    return [Math.round(point[0] / grid) * grid, Math.round(point[1] / grid) * grid];
  }

  /* ------------------------------------------------------------ 场景数据 */

  var STATE = {
    objects: [],            // {id, type:'track'|'field'|'battery', ...}
    bodies: [],             // 可以有多个物块（碰撞 / 动量题）
    selected: null,         // 对象 id / 物块 id
    draft: null, drag: null, seq: 1,
    result: null, index: 0, timer: null, index3d: 0
  };

  /* 物块"现在画在哪儿"：动画在跑就是当前帧的位置，没跑过就是存的那个位置。
     命中判定、拖拽、画受力都得用它——不然物块被搬走之后就点不中了（老师反馈"根本选不中"）。 */
  function shownAt(index) {
    var frames = (STATE.result && STATE.result.frames) || [];
    var frame = frames[STATE.index];
    var slot = frame && frame.bodies && frame.bodies[index];
    if (slot) return [slot[0], slot[1]];
    var body = STATE.bodies[index];
    return body ? [body.at[0], body.at[1]] : null;
  }

  function shownAtOf(id) {
    for (var index = 0; index < STATE.bodies.length; index++) {
      if (STATE.bodies[index].id === id) return shownAt(index);
    }
    return null;
  }

  function bodyById(id) {
    for (var index = 0; index < STATE.bodies.length; index++) {
      if (STATE.bodies[index].id === id) return STATE.bodies[index];
    }
    return null;
  }

  function addBody(at) {
    var body = { id: 'p' + (STATE.seq++), name: '物块 ' + (STATE.bodies.length + 1),
                 showForces: true,
                 mass: 1.0, charge: 0.0, radius: 0.06, at: at, v: [0.0, 0.0] };
    STATE.bodies.push(body);
    STATE.selected = body.id;
    renderEditor();
    drawScene();
    return body;
  }

  function byId(id) {
    for (var index = 0; index < STATE.objects.length; index++) {
      if (STATE.objects[index].id === id) return STATE.objects[index];
    }
    return null;
  }

  function tracks() {
    return STATE.objects.filter(function (item) { return item.type === 'track'; });
  }

  /* ------------------------------------------------- 板 / 轮 / 弹簧 / 绳 / 悬挂点

     数据模型（和 docs\sandbox-linked-bodies.md 第 1 节的场景 JSON 一一对应）：
       plate  板 / 小车：at 板中心、size [宽, 高]、angle 度（逆时针正）、v [vx, vy]
       wheel  轮子：at 轮心、radius、roll 是否纯滚动、plate 挂在哪块板上
       spring 弹簧：a/b 两端（物块或板/轮的 id），b 为空时拴在世界固定点
       link   轻绳 / 轻杆：kind = rope | rod，两端同弹簧
       anchor 悬挂点：at 世界坐标
     轨道（track）多一个 body 键：坐标写成被挂板的**局部坐标**（原点=板中心、x 向右、
     逆时针为正），画和判命中时按当前帧的板位姿变换到世界坐标。 */

  function objectsOf(type) {
    return STATE.objects.filter(function (item) { return item.type === type; });
  }

  function plates() { return objectsOf('plate'); }
  function wheels() { return objectsOf('wheel'); }
  function springs() { return objectsOf('spring'); }
  function links() { return objectsOf('link'); }
  function anchors() { return objectsOf('anchor'); }

  /* 绕原点转一个角度（度，逆时针正）。板/轮的局部坐标和世界坐标互换都靠它。 */
  function rotate(point, degrees) {
    var rad = (degrees || 0) * Math.PI / 180;
    var cos = Math.cos(rad), sin = Math.sin(rad);
    return [point[0] * cos - point[1] * sin, point[0] * sin + point[1] * cos];
  }

  function frameNow() {
    var frames = (STATE.result && STATE.result.frames) || [];
    if (!frames.length) return null;
    return frames[Math.min(STATE.index, frames.length - 1)] || null;
  }

  /* 同类对象里的第几个（引擎的 frames[].plates / wheels 就是按这个顺序给的） */
  function typeOrder(type, id) {
    var count = 0, index;
    for (index = 0; index < STATE.objects.length; index++) {
      var item = STATE.objects[index];
      if (item.type !== type) continue;
      if (item.id === id) return count;
      count++;
    }
    return -1;
  }

  /* 板"现在画在哪儿"：动画在跑就是当前帧的位姿，没跑过就是存的那个位置 */
  function shownPlate(id) {
    var plate = byId(id);
    if (!plate || plate.type !== 'plate') return null;
    var order = typeOrder('plate', id);
    var frame = frameNow();
    var slot = (frame && frame.plates && order >= 0) ? frame.plates[order] : null;
    if (slot) return { at: [slot[0], slot[1]], v: [slot[2], slot[3]], angle: slot[4] || 0 };
    return { at: [plate.at[0], plate.at[1]], v: [plate.v[0], plate.v[1]], angle: plate.angle || 0 };
  }

  function shownWheel(id) {
    var wheel = byId(id);
    if (!wheel || wheel.type !== 'wheel') return null;
    var order = typeOrder('wheel', id);
    var frame = frameNow();
    var slot = (frame && frame.wheels && order >= 0) ? frame.wheels[order] : null;
    if (slot) return { at: [slot[0], slot[1]], angle: slot[2] || 0, v: [slot[3] || 0, 0] };
    return { at: [wheel.at[0], wheel.at[1]], angle: 0, v: [wheel.v[0], wheel.v[1]] };
  }

  function anchorAt(item) {
    if (!item) return null;
    var anchor = byId(item.anchorId);
    if (anchor && anchor.type === 'anchor') return [anchor.at[0], anchor.at[1]];
    if (anchor) {
      var where = objectPoint(item.anchorId, [0, 0]);
      if (where) return where;
    }
    return item.anchor ? [item.anchor[0], item.anchor[1]] : null;
  }

  /* 场景 JSON 里的引用有两种写法：id 字符串 / {id: ...}，或者直接给坐标 {at: [x, y]} / [x, y] */
  function refId(value) {
    if (typeof value === 'string' || typeof value === 'number') return String(value);
    if (value && typeof value === 'object' && !(value instanceof Array) &&
        value.id !== undefined && value.id !== null) return String(value.id);
    return null;
  }

  function refPoint(value) {
    var at = value;
    if (value && typeof value === 'object' && !(value instanceof Array)) at = value.at;
    if (!(at instanceof Array) || at.length < 2) return null;
    var x = Number(at[0]), y = Number(at[1]);
    if (!isFinite(x) || !isFinite(y)) return null;   // 'O'、undefined 这种脏坐标不许进场景
    return [x, y];
  }

  function pairOf(value, fallback) {
    return refPoint(value) || (fallback && fallback.length ? [fallback[0], fallback[1]] : [0, 0]);
  }

  /* 弹簧 / 绳的空端：解析成「悬挂点（或物体）id + 世界坐标」，坐标一律校验成数字 */
  function anchorRefInfo(value) {
    var id = refId(value);
    var at = refPoint(value);
    if (!id) return { id: null, at: at };
    var resolved = resolveSceneRef(id);
    var item = byId(resolved);
    if (item) {
      if (item.type === 'anchor') at = [item.at[0], item.at[1]];
      else at = objectPoint(resolved, [0, 0]) || at;
      return { id: resolved, at: at };
    }
    return { id: id, at: at };       // 引用的对象还没载入：id 先留着，别丢掉
  }

  /* 某个对象上的一点（局部偏移 -> 世界坐标）；板的偏移跟着板的转角转 */
  function objectPoint(id, offset) {
    var off = [offset ? offset[0] : 0, offset ? offset[1] : 0];
    var item = byId(id);
    if (item && item.type === 'plate') {
      var pose = shownPlate(id);
      if (!pose) return null;
      var turn = rotate(off, pose.angle || 0);
      return [pose.at[0] + turn[0], pose.at[1] + turn[1]];
    }
    if (item && item.type === 'wheel') {
      var spin = shownWheel(id);
      if (!spin) return null;
      var turn2 = rotate(off, spin.angle || 0);
      return [spin.at[0] + turn2[0], spin.at[1] + turn2[1]];
    }
    if (item && item.type === 'anchor') return [item.at[0], item.at[1]];
    for (var index = 0; index < STATE.bodies.length; index++) {
      if (STATE.bodies[index].id !== id) continue;
      var where = shownAt(index) || STATE.bodies[index].at;
      return [where[0] + off[0], where[1] + off[1]];
    }
    return null;
  }

  /* 世界坐标里的一点 -> 某个对象上的局部偏移（建弹簧/绳、换板时用） */
  function localOffset(id, point) {
    var item = byId(id);
    if (item && item.type === 'plate') {
      var pose = shownPlate(id);
      if (!pose) return [0, 0];
      return rotate([point[0] - pose.at[0], point[1] - pose.at[1]], -(pose.angle || 0));
    }
    if (item && item.type === 'wheel') {
      var spin = shownWheel(id);
      if (!spin) return [0, 0];
      return rotate([point[0] - spin.at[0], point[1] - spin.at[1]], -(spin.angle || 0));
    }
    var center = objectPoint(id, [0, 0]);
    if (!center) return [0, 0];
    return [point[0] - center[0], point[1] - center[1]];
  }

  /* 弹簧 / 绳的两端画在哪儿：'a' 一定拴在物体上，'b' 可以是物体也可以是世界固定点 */
  function attachEnd(item, which) {
    if (!item) return null;
    if (which === 'a') return objectPoint(item.a, item.at_a);
    if (item.b) return objectPoint(item.b, item.at_b);
    return anchorAt(item);
  }

  function trackPose(track) {
    return track && track.body ? shownPlate(track.body) : null;
  }

  function trackWorldPoint(track, point, pose) {
    var current = pose || trackPose(track);
    if (!current) return [point[0], point[1]];
    var turn = rotate(point, current.angle || 0);
    return [current.at[0] + turn[0], current.at[1] + turn[1]];
  }

  function trackWorldPoints(track) {
    var pose = trackPose(track);
    return (track.points || []).map(function (point) {
      return trackWorldPoint(track, point, pose);
    });
  }

  /* 圆弧：circle 轨道的可选 arc = [起角, 终角]（度、逆时针正、点集就是这两角之间的弧）。
     没写 arc 或整圈 360 度 = 整圆，返回 null。 */
  function arcOf(track) {
    var arc = track && track.arc;
    if (!arc || !arc.length) return null;
    var lo = numberOr(arc[0], 0), hi = numberOr(arc[1], 360);
    if (!(hi > lo)) return null;
    if (hi - lo >= 359.99) return null;
    return [lo, hi];
  }

  function circleWorld(track) {
    var pose = trackPose(track);
    if (!pose) return { center: [track.center[0], track.center[1]], angle: 0 };
    var turn = rotate(track.center, pose.angle || 0);
    return { center: [pose.at[0] + turn[0], pose.at[1] + turn[1]], angle: pose.angle || 0 };
  }

  /* 这个局部角度落在弧段上吗（整圆永远算在） */
  function inArc(track, angle) {
    var arc = arcOf(track);
    if (!arc) return true;
    var value = angle;
    while (value < arc[0]) value += 360;
    while (value > arc[1]) value -= 360;
    return value >= arc[0] - 1e-6 && value <= arc[1] + 1e-6;
  }

  /* 弧段折成折线（世界坐标，每 5 度一段），画弧和"弧外按端点算距离"都用它 */
  function arcWorldPoints(track) {
    var geo = circleWorld(track);
    var radius = track.radius || 0.6;
    var arc = arcOf(track);
    var start = arc ? arc[0] : 0;
    var end = arc ? arc[1] : 360;
    var steps = Math.max(4, Math.round((end - start) / 5));
    var points = [], index, angle;
    for (index = 0; index <= steps; index++) {
      angle = (start + (end - start) * index / steps + (geo.angle || 0)) * Math.PI / 180;
      points.push([geo.center[0] + Math.cos(angle) * radius,
                   geo.center[1] + Math.sin(angle) * radius]);
    }
    return points;
  }

  function insidePlate(plate, point) {
    var pose = shownPlate(plate.id);
    if (!pose) return false;
    var local = rotate([point[0] - pose.at[0], point[1] - pose.at[1]], -(pose.angle || 0));
    return Math.abs(local[0]) <= plate.size[0] / 2 + 0.02 &&
           Math.abs(local[1]) <= plate.size[1] / 2 + 0.02;
  }

  function distanceToSegment(point, a, b) {
    var vx = b[0] - a[0], vy = b[1] - a[1];
    var length2 = vx * vx + vy * vy;
    var t = length2 ? ((point[0] - a[0]) * vx + (point[1] - a[1]) * vy) / length2 : 0;
    t = Math.max(0, Math.min(1, t));
    return Math.hypot(point[0] - (a[0] + vx * t), point[1] - (a[1] + vy * t));
  }

  function distanceToLinkPoint(item, point) {
    var a = attachEnd(item, 'a'), b = attachEnd(item, 'b');
    var pulley = item.b ? anchorAt(item) : null;    // 定滑轮：两段线都要能点中
    if (pulley) {
      var best = a ? distanceToSegment(point, a, pulley) : Infinity;
      if (b) best = Math.min(best, distanceToSegment(point, pulley, b));
      return best;
    }
    if (!a || !b) return Infinity;
    return distanceToSegment(point, a, b);
  }

  /* 弹簧画成锯齿：两端之间来回偏一个振幅 */
  function zigzag(a, b, coils, amplitude) {
    var dx = b[0] - a[0], dy = b[1] - a[1];
    var length = Math.hypot(dx, dy) || 1e-9;
    var perp = [-dy / length * amplitude, dx / length * amplitude];
    var points = [[a[0], a[1]]];
    var count = Math.max(2, coils) * 2;
    for (var index = 1; index < count; index++) {
      var t = index / count;
      var sign = index % 2 ? 1 : -1;
      points.push([a[0] + dx * t + perp[0] * sign, a[1] + dy * t + perp[1] * sign]);
    }
    points.push([b[0], b[1]]);
    return points;
  }

  function post(payload) {
    var options = {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    };
    if (window.W7Api && window.W7Api.token) {
      options.headers['X-Auth-Token'] = window.W7Api.token;
    }
    return fetch('/api/physics', options).then(function (response) {
      return response.json().catch(function () { return {}; });
    });
  }

  function note(text, kind) {
    var box = $('#sbNote');
    if (!box) return;
    if (!text) { box.style.display = 'none'; box.textContent = ''; return; }
    box.style.display = '';
    box.textContent = text;
    box.className = 'notice' + (kind ? ' ' + kind : '');
  }

  function muArray(raw, count) {
    var out = [], index;
    if (raw instanceof Array) {
      for (index = 0; index < count; index++) {
        out.push(numberOr(raw[index], out.length ? out[out.length - 1] : 0));
      }
      return out;
    }
    for (index = 0; index < count; index++) out.push(numberOr(raw, 0));
    return out;
  }

  function numberOr(value, fallback) {
    var number = parseFloat(value);
    return isNaN(number) ? fallback : number;
  }

  /* ------------------------------------------------------------ 画场景 */

  var SELECT_COLOR = '#e0518f';

  function fieldColor(kind) {
    return kind === 'B' ? '#7d5ba6' : (kind === 'battery' ? '#c0392b' : '#e08a1e');
  }

  /* 电池（平行板）两板之间的电场：E = U/d，方向按"从正板到负板"给 */
  function batteryField(item) {
    var vertical = item.rect[2] >= item.rect[3];      // 宽 >= 高 -> 板是上下两块
    var gap = vertical ? item.rect[3] : item.rect[2];
    var value = gap > 1e-6 ? Math.abs(item.voltage) / gap : 0;
    var dir = item.dir || (vertical ? 'down' : 'right');
    var unit = { up: [0, 1], down: [0, -1], right: [1, 0], left: [-1, 0] }[dir];
    var sign = item.voltage >= 0 ? 1 : -1;
    return [unit[0] * value * sign, unit[1] * value * sign];
  }

  /* 受力矢量（老师要的"实时显示受力"）：当前帧每个物块的各个力画成箭头。
     长度按最大那个力统一缩放（不然重力一大，摩擦力就看不见了）；
     颜色：重力灰、电场力红、洛伦兹力紫、法向力蓝、摩擦力橙、合力黑（最粗）。 */
  var FORCE_STYLE = {
    gravity: ['#6b7280', '重力'],
    electric: ['#dc2626', '电场力'],
    magnetic: ['#7c3aed', '洛伦兹力'],
    normal: ['#2563eb', '法向力'],
    friction: ['#ea580c', '摩擦力'],
    net: ['#111827', '合力']
  };
  /* 同向的力会跟别的箭头重叠，用线型区分（起点都是同一个质心，不能靠错位） */
  var FORCE_DASH = { electric: '7 4', magnetic: '2 4' };
  var FORCE_ORDER = ['gravity', 'electric', 'magnetic', 'normal', 'friction', 'net'];

  /* 受力箭头是**每个物块自己**的事：默认开着，侧边栏里可以单独关掉（老师要的） */
  function bodyFlag(body) {
    return body.showForces !== false;
  }

  function forcesWanted() {
    return STATE.bodies.some(bodyFlag);
  }

  function frameHasForces() {
    var frames = (STATE.result && STATE.result.frames) || [];
    var frame = frames[STATE.index];
    return !!(frame && frame.forces && frame.forces.length);
  }

  function drawForces(parts) {
    if (!forcesWanted() || !frameHasForces()) return;
    var frames = STATE.result.frames;
    var frame = frames[STATE.index];
    var arrowPx = 96;                                  // 最长那根箭头的屏幕长度
    var biggest = 0;
    frame.forces.forEach(function (row, index) {
      if (!bodyFlag(STATE.bodies[index] || {})) return;
      FORCE_ORDER.forEach(function (key, order) {
        if (key === 'net') return;                     // 合力只用来定标
        var pair = row[order] || [0, 0];
        biggest = Math.max(biggest, Math.hypot(pair[0], pair[1]));
      });
    });
    if (biggest < 1e-9) biggest = 1;
    var scale = (arrowPx / PX) / biggest;              // N -> 米（画布单位）
    var drawn = [];
    var many = frame.forces.length > 1;
    frame.forces.forEach(function (row, index) {
      var slot = frame.bodies[index];
      if (!slot) return;
      var owner = STATE.bodies[index] || {};
      if (!bodyFlag(owner)) return;                    // 这个物块关了受力显示
      var origin = shownAt(index) || [slot[0], slot[1]];   // 和命中判定同一套"现在画在哪"

      var todo = [];
      FORCE_ORDER.forEach(function (key, order) {
        var pair = row[order] || [0, 0];
        var size = Math.hypot(pair[0], pair[1]);
        if (size < Math.max(1e-6, biggest * 0.02)) return;
        todo.push({ key: key, pair: pair, size: size });
      });
      todo.forEach(function (entry, position) {
        var key = entry.key;
        var pair = entry.pair;
        var size = entry.size;
        var style = FORCE_STYLE[key];
        var head = toScreen([origin[0] + pair[0] * scale, origin[1] + pair[1] * scale]);
        var tail = toScreen(origin);
        // **所有矢量共用同一个起点：物块质心**（老师："我要所有矢量共用一个起点"）。
        // 起点一个像素都不错开——同向的力靠线型区分（电场力/洛伦兹力画虚线），
        // 反向的力（重力与法向力）本来就是一根双向箭头，不会互相盖住。
        var angle = Math.atan2(head[1] - tail[1], head[0] - tail[0]);
        var width = key === 'net' ? 3.2 : 2;
        parts.push('<line x1="' + tail[0] + '" y1="' + tail[1] + '" x2="' + head[0] +
          '" y2="' + head[1] + '" stroke="' + style[0] + '" stroke-width="' + width +
          '" stroke-linecap="round"' +
          (FORCE_DASH[key] ? (' stroke-dasharray="' + FORCE_DASH[key] + '"') : '') + '/>');
        var wing = key === 'net' ? 10 : 8;
        [[-1, 0], [1, 0]].forEach(function (side) {
          var spot = [head[0] - wing * Math.cos(angle + side[0] * 0.42),
                      head[1] - wing * Math.sin(angle + side[0] * 0.42)];
          parts.push('<line x1="' + head[0] + '" y1="' + head[1] + '" x2="' + spot[0] +
            '" y2="' + spot[1] + '" stroke="' + style[0] + '" stroke-width="' + width +
            '" stroke-linecap="round"/>');
        });
        drawn.push({ key: key, color: style[0], size: size, head: head,
                     owner: many ? (owner.name || ('物块 ' + (index + 1))) : '' });
      });
    });
    if (drawn.length) {                                // 图例（右下角）
      var top = SVG_H - 14 - drawn.length * 15;
      drawn.forEach(function (item, order) {
        parts.push('<rect x="' + (SVG_W - 148) + '" y="' + (top + order * 15 - 9) +
          '" width="10" height="10" fill="' + item.color + '"/>');
        parts.push('<text x="' + (SVG_W - 132) + '" y="' + (top + order * 15) +
          '" font-size="12" fill="#3a4652">' +
          (item.owner ? (item.owner + ' ') : '') + FORCE_STYLE[item.key][1] + ' ' +
          item.size.toFixed(2) + ' N</text>');
      });
    }
  }

  function drawScene() {
    var svg = $('#sbScene');
    if (!svg) return;
    var parts = [], step;
    var size = viewSize();

    /* 网格按**当前可见范围**画（缩放平移后网格要跟着铺满），每 1 m 加粗 */
    var left = VIEW.x / PX, right = (VIEW.x + size[0]) / PX;
    var bottom = (SVG_H - VIEW.y - size[1]) / PX, top = (SVG_H - VIEW.y) / PX;
    var first = Math.floor(left / 0.1), last = Math.ceil(right / 0.1);
    for (step = first; step <= last && step - first < 600; step++) {
      var gridAt = toScreen([step * 0.1, 0]);
      parts.push('<line x1="' + gridAt[0] + '" y1="' + (VIEW.y) + '" x2="' + gridAt[0] +
        '" y2="' + (VIEW.y + size[1]) + '" stroke="' + (step % 10 === 0 ? '#dde4ec' : '#f2f5f9') +
        '" stroke-width="1"/>');
    }
    var firstRow = Math.floor(bottom / 0.1), lastRow = Math.ceil(top / 0.1);
    for (step = firstRow; step <= lastRow && step - firstRow < 600; step++) {
      var gridRow = toScreen([0, step * 0.1]);
      parts.push('<line x1="' + VIEW.x + '" y1="' + gridRow[1] + '" x2="' + (VIEW.x + size[0]) +
        '" y2="' + gridRow[1] + '" stroke="' + (step % 10 === 0 ? '#dde4ec' : '#f2f5f9') +
        '" stroke-width="1"/>');
    }
    /* 坐标轴（世界原点在左下角：user (0, 420)） */
    parts.push('<line x1="' + VIEW.x + '" y1="' + SVG_H + '" x2="' + (VIEW.x + size[0]) +
      '" y2="' + SVG_H + '" stroke="#3a4652" stroke-width="2"/>');
    parts.push('<line x1="0" y1="' + VIEW.y + '" x2="0" y2="' + (VIEW.y + size[1]) +
      '" stroke="#3a4652" stroke-width="2"/>');
    for (step = Math.max(0, Math.floor(left)); step <= Math.min(W, Math.ceil(right)); step++) {
      if (!step) continue;
      var xAt = toScreen([step, 0]);
      parts.push('<line x1="' + xAt[0] + '" y1="' + (SVG_H - 6) + '" x2="' + xAt[0] + '" y2="' +
        SVG_H + '" stroke="#3a4652"/><text x="' + (xAt[0] + 3) + '" y="' + (SVG_H - 8) +
        '" font-size="11" fill="#7b8794">' + step + '</text>');
    }
    for (step = Math.max(0, Math.floor(bottom)); step <= Math.min(H, Math.ceil(top)); step++) {
      if (!step) continue;
      var yAt = toScreen([0, step]);
      parts.push('<line x1="0" y1="' + yAt[1] + '" x2="6" y2="' + yAt[1] + '" stroke="#3a4652"/>' +
        '<text x="6" y="' + (yAt[1] - 3) + '" font-size="11" fill="#7b8794">' + step + '</text>');
    }
    parts.push('<text x="' + (SVG_W - 26) + '" y="' + (SVG_H - 8) +
      '" font-size="12" fill="#7b8794">x / m</text>');
    parts.push('<text x="8" y="16" font-size="12" fill="#7b8794">y / m（原点在左下角）</text>');

    STATE.objects.forEach(function (item) {
      if (item.type !== 'field' && item.type !== 'battery') return;
      var rect = item.rect;
      var left = toScreen([rect[0], rect[1] + rect[3]]);
      var right = toScreen([rect[0] + rect[2], rect[1]]);
      var color = fieldColor(item.type === 'battery' ? 'battery' : item.kind);
      var selected = STATE.selected === item.id;
      parts.push('<rect x="' + left[0] + '" y="' + left[1] + '" width="' + (right[0] - left[0]) +
        '" height="' + (right[1] - left[1]) + '" fill="' + color + '" fill-opacity="0.10" stroke="' +
        color + '" stroke-width="' + (selected ? 3 : 1) + '" stroke-dasharray="6 4"/>');
      var center = toScreen([rect[0] + rect[2] / 2, rect[1] + rect[3] / 2]);
      if (item.type === 'battery') {
        var vertical = rect[2] >= rect[3];
        var half = vertical ? rect[3] / 2 : rect[2] / 2;
        var mid = [rect[0] + rect[2] / 2, rect[1] + rect[3] / 2];
        var plateA, plateB;
        if (vertical) {
          plateA = [toScreen([rect[0], mid[1] + half]), toScreen([rect[0] + rect[2], mid[1] + half])];
          plateB = [toScreen([rect[0], mid[1] - half]), toScreen([rect[0] + rect[2], mid[1] - half])];
        } else {
          plateA = [toScreen([mid[0] - half, rect[1]]), toScreen([mid[0] - half, rect[1] + rect[3]])];
          plateB = [toScreen([mid[0] + half, rect[1]]), toScreen([mid[0] + half, rect[1] + rect[3]])];
        }
        parts.push('<line x1="' + plateA[0][0] + '" y1="' + plateA[0][1] + '" x2="' + plateA[1][0] +
          '" y2="' + plateA[1][1] + '" stroke="#3a4652" stroke-width="4"/>');
        parts.push('<line x1="' + plateB[0][0] + '" y1="' + plateB[0][1] + '" x2="' + plateB[1][0] +
          '" y2="' + plateB[1][1] + '" stroke="#3a4652" stroke-width="4"/>');
        parts.push('<text x="' + (center[0] - 40) + '" y="' + (center[1] - 6) + '" font-size="12" fill="' +
          color + '">U=' + item.voltage + ' V　' + (item.dir || (vertical ? 'down' : 'right')) +
          '</text>');
        return;
      }
      if (item.kind === 'B') {
        var dots, count = 12;
        for (dots = 0; dots < count; dots++) {
          var at = toScreen([rect[0] + 0.15 + (dots % 4) * (rect[2] - 0.3) / 4,
                             rect[1] + 0.2 + Math.floor(dots / 4) * (rect[3] - 0.4) / 2]);
          if (item.value >= 0) {
            parts.push('<circle cx="' + at[0] + '" cy="' + at[1] + '" r="3.4" fill="none" stroke="' +
              color + '"/><circle cx="' + at[0] + '" cy="' + at[1] + '" r="1.2" fill="' + color + '"/>');
          } else {
            parts.push('<circle cx="' + at[0] + '" cy="' + at[1] + '" r="3.4" fill="none" stroke="' +
              color + '"/><path d="M' + (at[0] - 2.3) + ' ' + (at[1] - 2.3) + 'L' + (at[0] + 2.3) +
              ' ' + (at[1] + 2.3) + 'M' + (at[0] + 2.3) + ' ' + (at[1] - 2.3) + 'L' +
              (at[0] - 2.3) + ' ' + (at[1] + 2.3) + '" stroke="' + color + '" stroke-width="1.2"/>');
          }
        }
        parts.push('<text x="' + (center[0] - 46) + '" y="' + (center[1] - 6) + '" font-size="12" fill="' +
          color + '">B=' + item.value + ' T ' + (item.value >= 0 ? '⊙出纸面' : '⊗入纸面') + '</text>');
      } else {
        parts.push('<text x="' + (center[0] - 52) + '" y="' + (center[1] - 6) + '" font-size="12" fill="' +
          color + '">E=' + item.value + ' N/C ' + item.dir + '</text>');
      }
    });

    tracks().forEach(function (track, order) {
      var selected = STATE.selected === track.id;
      var stroke = selected ? SELECT_COLOR : '#4d6bfe';
      var hang = track.body ? '（挂在' + plateName(track.body) + '上）' : '';
      if (track.kind === 'circle') {
        var geo = circleWorld(track);
        var center = toScreen(geo.center);
        var arc = arcOf(track);
        if (arc) {
          var arcPoints = arcWorldPoints(track).map(toScreen);
          parts.push('<polyline points="' + arcPoints.map(function (point) {
            return point[0] + ',' + point[1];
          }).join(' ') + '" fill="none" stroke="' + stroke + '" stroke-width="5" ' +
            'stroke-linecap="round"/>');
        } else {
          parts.push('<circle cx="' + center[0] + '" cy="' + center[1] + '" r="' + (track.radius * PX) +
            '" fill="none" stroke="' + stroke + '" stroke-width="5"/>');
        }
        parts.push('<circle cx="' + (center[0] + track.radius * PX) + '" cy="' + center[1] +
          '" r="5" fill="#fff" stroke="' + stroke + '" stroke-width="2"/>');
        parts.push('<text x="' + center[0] + '" y="' + (center[1] - track.radius * PX - 8) +
          '" font-size="12" fill="' + stroke + '" text-anchor="middle">' +
          (arc ? '圆弧 ' : '圆环 ') + (order + 1) +
          '（μ=' + track.mu + '，拖右侧白点改半径）' + hang + '</text>');
        return;
      }
      var world = trackWorldPoints(track);
      var points = world.map(toScreen);
      parts.push('<polyline points="' + points.map(function (point) {
        return point[0] + ',' + point[1];
      }).join(' ') + '" fill="none" stroke="' + stroke + '" stroke-width="7" stroke-linecap="round"/>');
      world.forEach(function (point, position) {
        var at = toScreen(point);
        parts.push('<circle cx="' + at[0] + '" cy="' + at[1] + '" r="5" fill="#fff" stroke="' +
          stroke + '" stroke-width="2"/>');
        if (position < track.mu.length && world[position + 1]) {
          var middle = toScreen([(point[0] + world[position + 1][0]) / 2,
                                 (point[1] + world[position + 1][1]) / 2]);
          parts.push('<text x="' + (middle[0] + 6) + '" y="' + (middle[1] - 6) + '" font-size="12" fill="' +
            (selected ? SELECT_COLOR : '#7b8794') + '">μ=' + track.mu[position] + '</text>');
        }
      });
      parts.push('<text x="' + points[0][0] + '" y="' + (points[0][1] - 14) + '" font-size="12" fill="' +
        stroke + '">轨道 ' + (order + 1) + hang + '</text>');
    });

    /* 板、轮、弹簧、绳、悬挂点（轨道之后画，物块之前画：它们大多是大件） */
    drawPlates(parts);
    drawWheels(parts);
    drawSprings(parts);
    drawLinks(parts);
    drawAnchors(parts);

    if (STATE.draft && STATE.draft.points.length) {
      var draft = STATE.draft.points.map(toScreen);
      if (STATE.draft.cursor) draft = draft.concat([toScreen(STATE.draft.cursor)]);
      parts.push('<polyline points="' + draft.map(function (point) {
        return point[0] + ',' + point[1];
      }).join(' ') + '" fill="none" stroke="#12a594" stroke-width="4" stroke-dasharray="6 4"/>');
    }
    if (STATE.drag && STATE.drag.rect) {
      var a = toScreen(STATE.drag.from), b = toScreen(STATE.drag.to);
      parts.push('<rect x="' + Math.min(a[0], b[0]) + '" y="' + Math.min(a[1], b[1]) + '" width="' +
        Math.abs(b[0] - a[0]) + '" height="' + Math.abs(b[1] - a[1]) + '" fill="#e08a1e" ' +
        'fill-opacity="0.14" stroke="#e08a1e" stroke-dasharray="6 4"/>');
    }
    drawPick(parts);

    /* 物块（可以有多个）：每个画成方框 + 名字；跑起来时各自留一条红色轨迹 */
    var frames = (STATE.result && STATE.result.frames) || [];
    var shown = Math.min(STATE.index, Math.max(0, frames.length - 1));
    STATE.bodies.forEach(function (body, order) {
      var trail = [];
      var cursor;
      for (cursor = 0; cursor <= shown; cursor++) {
        var row = frames[cursor];
        var slot = row && row.bodies && row.bodies[order];
        if (slot) trail.push(toScreen([slot[0], slot[1]]));
      }
      if (trail.length > 1) {
        parts.push('<polyline points="' + trail.map(function (point) {
          return point[0] + ',' + point[1];
        }).join(' ') + '" fill="none" stroke="#c0392b" stroke-width="2"/>');
      }
      var at = body.at;
      var slot2 = frames[shown] && frames[shown].bodies && frames[shown].bodies[order];
      if (slot2) at = [slot2[0], slot2[1]];
      var center = toScreen(at);
      var half = Math.max(7, body.radius * PX);
      var color = STATE.selected === body.id ? SELECT_COLOR : '#3a4652';
      parts.push('<rect class="sb-block" x="' + (center[0] - half) + '" y="' + (center[1] - half) +
        '" width="' + (half * 2) + '" height="' + (half * 2) + '" rx="3" fill="' + color + '"/>');
      parts.push('<text x="' + (center[0] + half + 4) + '" y="' + (center[1] + 4) +
        '" font-size="11" fill="#7b8794">' + body.name + ' ' + body.mass + 'kg (' +
        at[0].toFixed(2) + ', ' + at[1].toFixed(2) + ')</text>');
    });
    if (STATE.hover) {
      var hx = toScreen(STATE.hover);
      parts.push('<line x1="' + hx[0] + '" y1="0" x2="' + hx[0] + '" y2="' + SVG_H +
        '" stroke="#c3ccd8" stroke-dasharray="3 5"/>');
      parts.push('<line x1="0" y1="' + hx[1] + '" x2="' + SVG_W + '" y2="' + hx[1] +
        '" stroke="#c3ccd8" stroke-dasharray="3 5"/>');
    }
    drawForces(parts);
    svg.innerHTML = scaleStrokes(parts.join(''), 1 / VIEW.zoom);
  }

  /* ------------------------------------------------------------ 新对象怎么画 */

  function objectName(id, fallback) {
    var item = byId(id);
    if (item && item.name) return item.name;
    for (var index = 0; index < STATE.bodies.length; index++) {
      if (STATE.bodies[index].id === id) return STATE.bodies[index].name;
    }
    return fallback || String(id);
  }

  function plateName(id) { return objectName(id, '板'); }

  /* 板 = 圆角矩形 + 质量标签；角度按当前帧的转角转（SVG 里顺时针为正，所以取负） */
  function drawPlates(parts) {
    plates().forEach(function (plate) {
      var pose = shownPlate(plate.id);
      if (!pose) return;
      var center = toScreen(pose.at);
      var half = [Math.max(6, plate.size[0] / 2 * PX), Math.max(4, plate.size[1] / 2 * PX)];
      var selected = STATE.selected === plate.id;
      var stroke = selected ? SELECT_COLOR : '#8a6d3b';
      parts.push('<g transform="rotate(' + (-(pose.angle || 0)) + ' ' + center[0] + ' ' + center[1] + ')">' +
        '<rect x="' + (center[0] - half[0]) + '" y="' + (center[1] - half[1]) + '" width="' +
        (half[0] * 2) + '" height="' + (half[1] * 2) + '" rx="6" fill="#f3e6d0" fill-opacity="0.85" stroke="' +
        stroke + '" stroke-width="' + (selected ? 3 : 2) + '"/></g>');
      parts.push('<text x="' + center[0] + '" y="' + (center[1] - half[1] - 7) +
        '" font-size="12" fill="' + stroke + '" text-anchor="middle">' + plate.name + ' ' +
        plate.mass + 'kg' + (plate.fixed ? '（固定）' : '') +
        (plate.lock_rotation === false ? '（可转）' : '') + '</text>');
    });
  }

  /* 轮 = 圆 + 4 根辐条；跑起来时按帧里的滚转角转 */
  function drawWheels(parts) {
    wheels().forEach(function (wheel) {
      var pose = shownWheel(wheel.id);
      if (!pose) return;
      var center = toScreen(pose.at);
      var radius = Math.max(5, (wheel.radius || 0.15) * PX);
      var selected = STATE.selected === wheel.id;
      var stroke = selected ? SELECT_COLOR : '#2f6f8f';
      parts.push('<circle cx="' + center[0] + '" cy="' + center[1] + '" r="' + radius +
        '" fill="#eaf4fa" stroke="' + stroke + '" stroke-width="2"/>');
      var spin = -(pose.angle || 0) * Math.PI / 180;
      for (var spoke = 0; spoke < 4; spoke++) {
        var theta = spin + spoke * Math.PI / 2;
        parts.push('<line x1="' + center[0] + '" y1="' + center[1] + '" x2="' +
          (center[0] + Math.cos(theta) * radius) + '" y2="' + (center[1] + Math.sin(theta) * radius) +
          '" stroke="' + stroke + '" stroke-width="1.5"/>');
      }
      parts.push('<text x="' + center[0] + '" y="' + (center[1] - radius - 7) +
        '" font-size="11" fill="#7b8794" text-anchor="middle">' + wheel.name +
        (wheel.roll === false ? '（滑动）' : '（滚动）') + '</text>');
    });
  }

  function drawSprings(parts) {
    springs().forEach(function (spring) {
      var a = attachEnd(spring, 'a'), b = attachEnd(spring, 'b');
      if (!a || !b) return;
      var selected = STATE.selected === spring.id;
      var color = selected ? SELECT_COLOR : '#8e44ad';
      var points = zigzag(a, b, 8, 0.06).map(toScreen);
      parts.push('<polyline points="' + points.map(function (point) {
        return point[0] + ',' + point[1];
      }).join(' ') + '" fill="none" stroke="' + color + '" stroke-width="2"/>');
      var first = toScreen(a), last = toScreen(b);
      parts.push('<circle cx="' + first[0] + '" cy="' + first[1] + '" r="4" fill="#fff" stroke="' +
        color + '" stroke-width="2"/>');
      parts.push('<circle cx="' + last[0] + '" cy="' + last[1] + '" r="4" fill="#fff" stroke="' +
        color + '" stroke-width="2"/>');
      parts.push('<text x="' + ((first[0] + last[0]) / 2 + 6) + '" y="' + ((first[1] + last[1]) / 2 - 6) +
        '" font-size="11" fill="' + color + '">' + spring.name + ' k=' + spring.k +
        ' 原长' + spring.rest + 'm</text>');
    });
  }

  function drawLinks(parts) {
    links().forEach(function (link) {
      var a = attachEnd(link, 'a');
      var b = attachEnd(link, 'b');
      var pulley = link.b ? anchorAt(link) : null;   // b 和悬挂点同时有 = 绳绕过定滑轮
      if (!a || (!b && !pulley)) return;
      var selected = STATE.selected === link.id;
      var color = selected ? SELECT_COLOR : (link.kind === 'rod' ? '#7c4a12' : '#0f766e');
      var dash = link.kind === 'rod' ? '' : ' stroke-dasharray="7 5"';
      var segments = [];
      if (pulley) {
        segments.push([a, pulley]);
        if (b) segments.push([pulley, b]);       // 绳从定滑轮另一侧下去
      } else {
        segments.push([a, b || pulley]);
      }
      var ends = pulley ? (b ? [a, b] : [a]) : [a, b || pulley];
      segments.forEach(function (segment) {
        var from = toScreen(segment[0]), to = toScreen(segment[1]);
        parts.push('<line x1="' + from[0] + '" y1="' + from[1] + '" x2="' + to[0] + '" y2="' +
          to[1] + '" stroke="' + color + '" stroke-width="2"' + dash + '/>');
      });
      if (pulley) {
        var hub = toScreen(pulley);
        parts.push('<circle cx="' + hub[0] + '" cy="' + hub[1] + '" r="9" fill="none" stroke="' +
          color + '" stroke-width="1.5"/>');
        parts.push('<text x="' + (hub[0] + 12) + '" y="' + (hub[1] - 8) +
          '" font-size="11" fill="' + color + '">定滑轮</text>');
      }
      ends.forEach(function (end) {
        if (!end) return;
        var point = toScreen(end);
        parts.push('<circle cx="' + point[0] + '" cy="' + point[1] + '" r="4" fill="#fff" stroke="' +
          color + '" stroke-width="2"/>');
      });
      var labelAt = pulley ? toScreen(pulley)
        : toScreen([(a[0] + b[0]) / 2, (a[1] + b[1]) / 2]);
      parts.push('<text x="' + (labelAt[0] + 6) + '" y="' + (labelAt[1] - 6) +
        '" font-size="11" fill="' + color + '">' + link.name + ' L=' + link.length + 'm</text>');
    });
  }

  function drawAnchors(parts) {
    anchors().forEach(function (anchor) {
      var at = toScreen(anchor.at);
      var selected = STATE.selected === anchor.id;
      var color = selected ? SELECT_COLOR : '#0f766e';
      parts.push('<circle cx="' + at[0] + '" cy="' + at[1] + '" r="6" fill="#fff" stroke="' +
        color + '" stroke-width="2"/>');
      parts.push('<circle cx="' + at[0] + '" cy="' + at[1] + '" r="2" fill="' + color + '"/>');
      parts.push('<text x="' + (at[0] + 9) + '" y="' + (at[1] + 4) + '" font-size="11" fill="' +
        color + '">' + anchor.name + '</text>');
    });
  }

  /* 新工具点一半时的预览：板矩形、圆弧、弹簧/绳的橡皮筋 */
  function drawPick(parts) {
    var pick = STATE.pick;
    if (!pick || !pick.points.length) return;
    var first = pick.points[0];
    var cursor = pick.cursor || first;
    if (pick.tool === 'plate' && pick.points.length >= 2) {
      var a = toScreen(first), b = toScreen(pick.points[1]);
      parts.push('<rect x="' + Math.min(a[0], b[0]) + '" y="' + Math.min(a[1], b[1]) + '" width="' +
        Math.abs(b[0] - a[0]) + '" height="' + Math.abs(b[1] - a[1]) + '" fill="#8a6d3b" ' +
        'fill-opacity="0.12" stroke="#8a6d3b" stroke-dasharray="6 4"/>');
      return;
    }
    if (pick.tool === 'arc') {
      var center = toScreen(first);
      var reach = pick.points.length >= 2 ? pick.points[1] : cursor;
      var radius = Math.hypot(reach[0] - first[0], reach[1] - first[1]);
      var start = Math.atan2(reach[1] - first[1], reach[0] - first[0]);
      var finish = pick.points.length >= 3 ? pick.points[2] : cursor;
      var end = Math.atan2(finish[1] - first[1], finish[0] - first[0]);
      var sweep = end - start;
      while (sweep <= -Math.PI) sweep += Math.PI * 2;
      while (sweep > Math.PI) sweep -= Math.PI * 2;
      var steps = Math.max(6, Math.round(Math.abs(sweep) / (Math.PI / 36)));
      var arcParts = [];
      for (var step = 0; step <= steps; step++) {
        var theta = start + sweep * step / steps;
        var point = toScreen([first[0] + Math.cos(theta) * radius, first[1] + Math.sin(theta) * radius]);
        arcParts.push(point[0] + ',' + point[1]);
      }
      parts.push('<polyline points="' + arcParts.join(' ') + '" fill="none" stroke="#12a594" ' +
        'stroke-width="4" stroke-dasharray="6 4"/>');
      parts.push('<circle cx="' + center[0] + '" cy="' + center[1] + '" r="4" fill="#12a594"/>');
      return;
    }
    if (pick.tool === 'spring' || pick.tool === 'rope' || pick.tool === 'rod') {
      var from = toScreen(first), to = toScreen(cursor);
      if (pick.tool === 'spring') {
        var zig = zigzag(first, cursor, 8, 0.06).map(toScreen);
        parts.push('<polyline points="' + zig.map(function (point) {
          return point[0] + ',' + point[1];
        }).join(' ') + '" fill="none" stroke="#8e44ad" stroke-width="2" stroke-dasharray="6 4"/>');
      } else {
        parts.push('<line x1="' + from[0] + '" y1="' + from[1] + '" x2="' + to[0] + '" y2="' +
          to[1] + '" stroke="#0f766e" stroke-width="2" stroke-dasharray="6 4"/>');
      }
      return;
    }
  }

  /* ------------------------------------------------------------ 选中与编辑 */

  /* 点一下选中。顺序：**离得最近的物块** -> 轨道顶点/圆环把手（小目标，紧判定 0.08 m）
     -> 场区/电池（大件，点在框里就算） -> 轨道线。
     物块和顶点都排在大件前面：它们本来就小，再让大件抢就永远点不中（老师报过"物块选不中"；
     也踩过场区的边界正好压在轨道顶点上、顶点拖不动的坑）。 */
  function hitTest(point) {
    var index, item;
    var best = null;
    for (index = 0; index < STATE.bodies.length; index++) {
      item = STATE.bodies[index];
      var where = shownAt(index) || item.at;
      var gap = Math.hypot(point[0] - where[0], point[1] - where[1]) - item.radius;
      // 物块**优先级最高**：贴着轨道、又被搬走的时候都得点得中，
      // 容差 0.2 m（屏幕上约 28 px），只要在容差里就直接选物块
      if (gap < 0.2 && (!best || gap < best.gap)) best = { gap: gap, id: item.id };
    }
    if (best) return best.id;
    /* 新对象：悬挂点 -> 轮子 -> 弹簧/绳 -> 板。
       板是大件，排在弹簧和轮子后面判，不然它会把板上的小零件全盖住。 */
    for (index = STATE.objects.length - 1; index >= 0; index--) {
      item = STATE.objects[index];
      if (item.type === 'anchor' &&
          Math.hypot(point[0] - item.at[0], point[1] - item.at[1]) < 0.1) return item.id;
    }
    for (index = STATE.objects.length - 1; index >= 0; index--) {
      item = STATE.objects[index];
      if (item.type !== 'wheel') continue;
      var shownWheelPose = shownWheel(item.id);
      if (!shownWheelPose) continue;
      if (Math.hypot(point[0] - shownWheelPose.at[0], point[1] - shownWheelPose.at[1]) <=
          (item.radius || 0.15) + 0.03) return item.id;
    }
    for (index = STATE.objects.length - 1; index >= 0; index--) {
      item = STATE.objects[index];
      if (item.type !== 'spring' && item.type !== 'link') continue;
      if (distanceToLinkPoint(item, point) < 0.07) return item.id;
    }
    for (index = STATE.objects.length - 1; index >= 0; index--) {
      item = STATE.objects[index];
      if (item.type === 'plate' && insidePlate(item, point)) return item.id;
    }
    for (index = STATE.objects.length - 1; index >= 0; index--) {
      item = STATE.objects[index];
      if (item.type === 'track' && handleIndex(item, point, 0.08) >= 0) return item.id;
    }
    for (index = STATE.objects.length - 1; index >= 0; index--) {
      item = STATE.objects[index];
      if (item.type === 'field' || item.type === 'battery') {
        var rect = item.rect;
        if (point[0] >= rect[0] && point[0] <= rect[0] + rect[2] &&
            point[1] >= rect[1] && point[1] <= rect[1] + rect[3]) {
          return item.id;
        }
      }
    }
    for (index = STATE.objects.length - 1; index >= 0; index--) {
      item = STATE.objects[index];
      if (item.type !== 'track') continue;
      if (distanceToTrack(item, point) < 0.09) return item.id;
    }
    return null;
  }

  function distanceToTrack(track, point) {
    if (track.kind === 'circle') {
      var geo = circleWorld(track);
      var dx = point[0] - geo.center[0], dy = point[1] - geo.center[1];
      var gap = Math.abs(Math.hypot(dx, dy) - track.radius);
      if (!arcOf(track)) return gap;
      var angle = Math.atan2(dy, dx) * 180 / Math.PI - (geo.angle || 0);
      if (inArc(track, angle)) return gap;
      /* 弧段之外：按弧的两头端点算（弧外的圆不算轨道） */
      var ends = arcWorldPoints(track);
      var first = ends[0], last = ends[ends.length - 1];
      return Math.min(Math.hypot(point[0] - first[0], point[1] - first[1]),
                      Math.hypot(point[0] - last[0], point[1] - last[1]));
    }
    var points = trackWorldPoints(track);
    var best = Infinity;
    for (var index = 0; index < points.length - 1; index++) {
      best = Math.min(best, distanceToSegment(point, points[index], points[index + 1]));
    }
    return best;
  }

  function field(label, key, value, unit) {
    return '<label>' + label + ' <input class="lab-num" type="text" style="width:72px" value="' +
      value + '" data-edit="' + key + '">' + (unit ? ' ' + unit : '') + '</label>';
  }

  function choose(label, key, value, options) {
    return '<label>' + label + ' <select class="lab-num" style="width:110px" data-edit="' + key +
      '">' + options.map(function (option) {
        return '<option value="' + option[0] + '"' + (option[0] === value ? ' selected' : '') +
          '>' + option[1] + '</option>';
      }).join('') + '</select></label>';
  }

  var DIRS = [['down', '竖直向下'], ['up', '竖直向上'], ['right', '水平向右'], ['left', '水平向左']];

  /* 「挂在」下拉的选项：空 = 不挂（静止在世界里），否则挂到某块板上 */
  function plateOptionList() {
    var options = [['', '不挂（静止）']];
    plates().forEach(function (plate) { options.push([plate.id, plate.name]); });
    return options;
  }

  function hangBox(item) {
    return choose('挂在', 'body', item.body || '', plateOptionList());
  }

  /* 圆弧的两个数：起角、张角（张角 360 = 整圆） */
  function arcBox(item) {
    var arc = item.arc && item.arc.length ? item.arc : [0, 360];
    return field('起角 度', 'arc_from', Math.round(arc[0] * 10) / 10) +
      field('张角 度', 'arc_span', Math.round((arc[1] - arc[0]) * 10) / 10) +
      '<span class="muted">从起角逆时针量；张角 360 就是整圆（例子里的 1/4 弧填 90）</span>';
  }

  function renderEditor() {
    var box = $('#sbEdit');
    if (!box) return;
    var item = STATE.selected === 'block' ? null : byId(STATE.selected);
    var html = '';
    var body = STATE.selected === null ? null : bodyById(STATE.selected);
    if (body) {
      html = '<div class="sb-item"><b>' + body.name + '</b>' +
        field('质量 kg', 'mass', body.mass) +
        field('电荷量 C', 'charge', body.charge) +
        field('vx m/s', 'vx', body.v[0]) +
        field('vy m/s', 'vy', body.v[1]) +
        field('半径 m', 'radius', body.radius) +
        '<span class="muted">位置 (' + body.at[0].toFixed(2) + ', ' + body.at[1].toFixed(2) +
        ')　点住物块可以拖</span>' +
        '<label><input type="checkbox" data-show-forces' +
        (body.showForces === false ? '' : ' checked') +
        '> 显示受力（在这个物块上画力的箭头）</label>' +
        '<span class="muted">箭头跟着物块走：灰=重力、红=电场力、紫=洛伦兹力、' +
        '蓝=法向力、橙=摩擦力、黑=合力</span>' +
        '<button class="btn lab-mini" data-edit-action="delete">删掉这个物块</button></div>';
    } else if (item && item.type === 'track') {
      if (item.kind === 'circle') {
        html = '<div class="sb-item"><b>圆环</b>' + field('半径 m', 'radius', item.radius) +
          field('μ', 'mu', item.mu) +
          choose('内外', 'inner', item.inner ? 'in' : 'out', [['in', '内侧'], ['out', '外侧']]) +
          arcBox(item) + hangBox(item);
      } else {
        var rows = item.mu.map(function (mu, index) {
          return '<label>第' + (index + 1) + '段 μ <input class="lab-num" type="text" ' +
            'style="width:56px" value="' + mu + '" data-mu="' + index + '"></label>';
        }).join('');
        html = '<div class="sb-item"><b>轨道（' + item.points.length + ' 个点）</b>' +
          '<span class="muted">点住白点可以拖顶点，按住线可以整体挪</span>' + rows +
          '<label><input type="checkbox" data-edit="belt"' + (item.belt ? ' checked' : '') +
          '> 传送带</label>' +
          '<label>皮带速度 <input class="lab-num" type="text" style="width:64px" value="' +
          (item.v_belt || 0) + '" data-edit="v_belt"> m/s</label>' +
          '<span class="muted">正 = 顺着轨道点的方向，负 = 反向；物块会被摩擦力带着走</span>' +
          hangBox(item);
      }
      html += '<button class="btn lab-mini" data-edit-action="delete">删掉这条轨道</button></div>';
    } else if (item && item.type === 'plate') {
      html = '<div class="sb-item"><b>' + item.name + '</b>' +
        field('名字', 'name', item.name) +
        field('质量 kg', 'mass', item.mass) +
        field('宽 m', 'size_w', item.size[0]) +
        field('高 m', 'size_h', item.size[1]) +
        field('倾角 度', 'angle', item.angle || 0) +
        field('μ地面', 'mu_ground', item.mu_ground || 0) +
        field('vx m/s', 'vx', item.v[0]) +
        field('vy m/s', 'vy', item.v[1]) +
        '<label><input type="checkbox" data-edit="fixed"' + (item.fixed ? ' checked' : '') +
        '> 固定（钉死在地上，一点都不动）</label>' +
        '<label><input type="checkbox" data-edit="lock_rotation"' +
        (item.lock_rotation === false ? '' : ' checked') + '> 不转（只平动，小车就是这种）</label>' +
        '<span class="muted">点住板可以拖它的初始位置；轨道可以「挂在」它上面跟着它走</span>' +
        '<button class="btn lab-mini" data-edit-action="delete">删掉这块板</button></div>';
    } else if (item && item.type === 'wheel') {
      html = '<div class="sb-item"><b>' + item.name + '</b>' +
        field('名字', 'name', item.name) +
        field('半径 m', 'radius', item.radius) +
        field('质量 kg', 'mass', item.mass) +
        field('μ', 'mu', item.mu) +
        field('vx m/s', 'vx', item.v[0]) +
        field('vy m/s', 'vy', item.v[1]) +
        choose('挂在', 'plate', item.plate || '', plateOptionList()) +
        '<label><input type="checkbox" data-edit="roll"' + (item.roll === false ? '' : ' checked') +
        '> 纯滚动（不打滑）</label>' +
        '<span class="muted">取消纯滚动、μ 又小的时候才会打滑（滑动摩擦）</span>' +
        '<button class="btn lab-mini" data-edit-action="delete">删掉这个轮子</button></div>';
    } else if (item && item.type === 'spring') {
      html = '<div class="sb-item"><b>' + item.name + '</b>' +
        field('名字', 'name', item.name) +
        field('劲度 k N/m', 'k', item.k) +
        field('原长 m', 'rest', item.rest) +
        field('阻尼', 'damping', item.damping || 0) +
        '<span class="muted">A 端拴在：' + objectName(item.a, '物块') + '；B 端：' +
        (item.b ? objectName(item.b, '物块') : '世界固定点（悬挂点）') +
        '。弹簧力 = k ×（现在长度 − 原长）</span>' +
        '<button class="btn lab-mini" data-edit-action="delete">删掉这根弹簧</button></div>';
    } else if (item && item.type === 'link') {
      html = '<div class="sb-item"><b>' + item.name + '</b>' +
        field('名字', 'name', item.name) +
        choose('类型', 'kind', item.kind, [['rope', '轻绳（只能拉）'], ['rod', '轻杆（拉压都行）']]) +
        field('长度 m', 'length', item.length) +
        '<span class="muted">A 端拴在：' + objectName(item.a, '物块') + '；B 端：' +
        (item.b ? objectName(item.b, '物块') : '世界固定点（悬挂点）') +
        (item.b && anchorAt(item) ? '，并且绕过悬挂点（定滑轮）' : '') +
        '。超过长度就被拉住（绳）/顶住（杆）</span>' +
        '<button class="btn lab-mini" data-edit-action="delete">删掉这条绳/杆</button></div>';
    } else if (item && item.type === 'anchor') {
      html = '<div class="sb-item"><b>' + item.name + '</b>' +
        field('名字', 'name', item.name) +
        field('x m', 'ax', item.at[0]) +
        field('y m', 'ay', item.at[1]) +
        '<span class="muted">悬挂点：弹簧或绳的空端可以拴在这里，它自己不动物理</span>' +
        '<button class="btn lab-mini" data-edit-action="delete">删掉这个悬挂点</button></div>';
    } else if (item && (item.type === 'field' || item.type === 'battery')) {
      var rows2 = '';
      if (item.type === 'battery') {
        var field0 = batteryField(item);
        rows2 = field('电压 U V', 'voltage', item.voltage) +
          choose('电场方向', 'dir', item.dir || 'down', DIRS) +
          '<span class="muted">E = U/d = ' + Math.hypot(field0[0], field0[1]).toFixed(2) + ' N/C</span>';
      } else if (item.kind === 'B') {
        rows2 = field('磁感应强度 T', 'value', item.value) +
          '<span class="muted">正 = 出纸面 ⊙，负 = 入纸面 ⊗（垂直屏幕）</span>';
      } else {
        rows2 = field('场强 N/C', 'value', item.value) +
          choose('电场方向', 'dir', item.dir, DIRS);
      }
      html = '<div class="sb-item"><b>' + (item.type === 'battery' ? '电池（平行板）'
        : (item.kind === 'B' ? '磁场区' : '电场区')) + '</b>' +
        '<span class="muted">范围 x ' + item.rect[0].toFixed(2) + '~' +
        (item.rect[0] + item.rect[2]).toFixed(2) + '、y ' + item.rect[1].toFixed(2) + '~' +
        (item.rect[1] + item.rect[3]).toFixed(2) + '　按住方块可以整体拖</span>' +
        rows2 + '<button class="btn lab-mini" data-edit-action="delete">删掉</button></div>';
    } else {
      html = '<div class="muted">工具选「选择 / 拖动」，点一下轨道、场区、电池或物块，就能在这里直接改数据</div>';
    }
    box.innerHTML = html;
    var forceBox = box.querySelector('input[data-show-forces]');
    if (forceBox) {
      forceBox.onchange = function () {
        var target = bodyById(STATE.selected);
        if (!target) return;
        target.showForces = forceBox.checked;
        refreshForces();
      };
    }
    bindEditor(item);
  }

  /* 侧栏里改一个字段 -> 直接改数据（oninput 就生效，不用等重跑） */
  function applyFieldEdit(body, item, key, node) {
    var checked = node.type === 'checkbox' ? !!node.checked : null;
    var value = node.value;
    if (body) {
      if (key === 'mass') body.mass = Math.max(0.001, numberOr(value, 1));
      else if (key === 'charge') body.charge = numberOr(value, 0);
      else if (key === 'vx') body.v[0] = numberOr(value, 0);
      else if (key === 'vy') body.v[1] = numberOr(value, 0);
      else if (key === 'radius') body.radius = Math.max(0.01, numberOr(value, 0.06));
      else if (key === 'name') body.name = String(value || '').slice(0, 20) || body.name;
      return;
    }
    if (!item) return;
    if (item.type === 'plate') {
      if (key === 'name') item.name = String(value || '').slice(0, 20) || item.name;
      else if (key === 'mass') item.mass = Math.max(0.001, numberOr(value, 1));
      else if (key === 'size_w') item.size[0] = Math.max(0.1, numberOr(value, item.size[0]));
      else if (key === 'size_h') item.size[1] = Math.max(0.05, numberOr(value, item.size[1]));
      else if (key === 'angle') item.angle = numberOr(value, 0);
      else if (key === 'mu_ground') item.mu_ground = Math.max(0, numberOr(value, 0));
      else if (key === 'vx') item.v[0] = numberOr(value, 0);
      else if (key === 'vy') item.v[1] = numberOr(value, 0);
      else if (key === 'fixed') item.fixed = checked;
      else if (key === 'lock_rotation') item.lock_rotation = checked;
      return;
    }
    if (item.type === 'wheel') {
      if (key === 'name') item.name = String(value || '').slice(0, 20) || item.name;
      else if (key === 'radius') item.radius = Math.max(0.03, numberOr(value, 0.15));
      else if (key === 'mass') item.mass = Math.max(0.001, numberOr(value, 1));
      else if (key === 'mu') item.mu = Math.max(0, numberOr(value, 0));
      else if (key === 'vx') item.v[0] = numberOr(value, 0);
      else if (key === 'vy') item.v[1] = numberOr(value, 0);
      else if (key === 'plate') item.plate = value || null;
      else if (key === 'roll') item.roll = checked;
      return;
    }
    if (item.type === 'spring') {
      if (key === 'name') item.name = String(value || '').slice(0, 20) || item.name;
      else if (key === 'k') item.k = Math.max(0, numberOr(value, item.k));
      else if (key === 'rest') item.rest = Math.max(0.01, numberOr(value, item.rest));
      else if (key === 'damping') item.damping = Math.max(0, numberOr(value, 0));
      return;
    }
    if (item.type === 'link') {
      if (key === 'name') item.name = String(value || '').slice(0, 20) || item.name;
      else if (key === 'kind') item.kind = value === 'rod' ? 'rod' : 'rope';
      else if (key === 'length') item.length = Math.max(0.05, numberOr(value, item.length));
      return;
    }
    if (item.type === 'anchor') {
      if (key === 'name') item.name = String(value || '').slice(0, 20) || item.name;
      else if (key === 'ax') item.at[0] = numberOr(value, item.at[0]);
      else if (key === 'ay') item.at[1] = numberOr(value, item.at[1]);
      return;
    }
    if (item.type === 'track') {
      if (key === 'body') { setTrackBody(item, value || null); return; }
      if (key === 'radius') item.radius = Math.max(0.05, numberOr(value, item.radius));
      else if (key === 'mu') item.mu = Math.max(0, numberOr(value, 0));
      else if (key === 'inner') item.inner = value === 'in';
      else if (key === 'belt') {
        item.belt = checked;
        if (item.belt && !item.v_belt) item.v_belt = 1.0;      // 默认给个 1 m/s
      } else if (key === 'v_belt') {
        item.v_belt = numberOr(value, 0);
        item.belt = true;
      } else if (key === 'arc_from' || key === 'arc_span') {
        var arc = item.arc && item.arc.length ? [item.arc[0], item.arc[1]] : [0, 360];
        var from = key === 'arc_from' ? numberOr(value, arc[0]) : arc[0];
        var span = key === 'arc_span' ? numberOr(value, arc[1] - arc[0]) : (arc[1] - arc[0]);
        span = Math.max(0, Math.min(360, span));
        item.arc = span >= 359.99 ? null : [from, from + span];
      }
      return;
    }
    /* 场区 / 电池 */
    if (key === 'value') item.value = numberOr(value, 0);
    else if (key === 'voltage') item.voltage = numberOr(value, 0);
    else if (key === 'dir') item.dir = value;
  }

  function bindEditor(item) {
    var box = $('#sbEdit');
    if (!box) return;
    var body = bodyById(STATE.selected);
    Array.prototype.forEach.call(box.querySelectorAll('input[data-edit], select[data-edit]'), function (node) {
      var handle = function (rerender) {
        applyFieldEdit(body, item, node.getAttribute('data-edit'), node);
        invalidate();                     // 数据变了，上一次的结果就不能再当真的画
        if (rerender) renderEditor();
        drawScene();
      };
      node.oninput = function () {
        if (node.type === 'checkbox' || node.tagName === 'SELECT') return;
        handle(false);                   // 打字时立刻生效，只是别重画侧栏（会丢焦点）
      };
      node.onchange = function () { handle(true); };
    });
    Array.prototype.forEach.call(box.querySelectorAll('input[data-mu]'), function (node) {
      node.oninput = function () {
        item.mu[parseInt(node.getAttribute('data-mu'), 10)] = Math.max(0, numberOr(node.value, 0));
        invalidate();
        drawScene();
      };
    });
    Array.prototype.forEach.call(box.querySelectorAll('button[data-edit-action="delete"]'), function (node) {
      node.onclick = function () { deleteSelected(); };
    });
  }

  function deleteSelected() {
    var index, removed = STATE.selected;
    if (!removed) return;
    for (index = 0; index < STATE.bodies.length; index++) {
      if (STATE.bodies[index].id === removed) {
        STATE.bodies.splice(index, 1);
        dropReferences(removed);          // 拴在它上面的弹簧/绳一起收掉，免得留野指针
        STATE.selected = null;
        renderEditor();
        drawScene();
        return;
      }
    }
    for (index = 0; index < STATE.objects.length; index++) {
      if (STATE.objects[index].id === removed) {
        STATE.objects.splice(index, 1);
        break;
      }
    }
    dropReferences(removed);
    STATE.selected = null;
    renderEditor();
    drawScene();
  }

  /* ------------------------------------------------------------ 放板 / 轮 / 弹簧 / 绳 */

  function addAnchor(at, label) {
    var anchor = { id: 'a' + (STATE.seq++), type: 'anchor',
                   name: (label || '悬挂点') + ' ' + (anchors().length + 1),
                   at: [at[0], at[1]] };
    STATE.objects.push(anchor);
    return anchor;
  }

  function addPlate(from, to) {
    var width = Math.abs(to[0] - from[0]), height = Math.abs(to[1] - from[1]);
    var plate = { id: 'm' + (STATE.seq++), type: 'plate',
                  name: '板 ' + (plates().length + 1),
                  at: [Math.min(from[0], to[0]) + width / 2, Math.min(from[1], to[1]) + height / 2],
                  size: [width, height], mass: numberOr(num('#sbMass', 1), 1), v: [0, 0],
                  angle: 0, lock_rotation: true, fixed: false, mu_ground: 0, support: 'ground' };
    STATE.objects.push(plate);
    return plate;
  }

  function addWheel(at) {
    var wheel = { id: 'w' + (STATE.seq++), type: 'wheel', name: '轮子 ' + (wheels().length + 1),
                  at: [at[0], at[1]], radius: 0.15, mass: 0.5, v: [0, 0],
                  roll: true, mu: 0.6, plate: null };
    STATE.objects.push(wheel);
    return wheel;
  }

  /* 弹簧 / 轻绳 / 轻杆：第一端一定拴在物体上，第二端要么拴另一个物体，要么落一个固定点 */
  function addJoint(tool, aId, pointA, pointB, bId) {
    var atA = localOffset(aId, pointA);
    var atB = [0, 0], anchor = null, anchorId = null;
    if (bId) {
      atB = localOffset(bId, pointB);
    } else {
      var fixed = addAnchor(pointB, tool === 'spring' ? '固定点' : '悬挂点');
      anchor = [fixed.at[0], fixed.at[1]];
      anchorId = fixed.id;
    }
    var length = Math.max(0.05, Math.hypot(pointB[0] - pointA[0], pointB[1] - pointA[1]));
    var item;
    if (tool === 'spring') {
      item = { id: 's' + (STATE.seq++), type: 'spring', name: '弹簧 ' + (springs().length + 1),
               a: aId, at_a: atA, b: bId || null, at_b: atB, anchor: anchor, anchorId: anchorId,
               k: 200, rest: Math.round(length * 100) / 100, damping: 0 };
    } else {
      item = { id: 'l' + (STATE.seq++), type: 'link', kind: tool === 'rod' ? 'rod' : 'rope',
               name: (tool === 'rod' ? '轻杆 ' : '轻绳 ') + (links().length + 1),
               a: aId, at_a: atA, b: bId || null, at_b: atB, anchor: anchor, anchorId: anchorId,
               length: Math.round(length * 100) / 100 };
    }
    STATE.objects.push(item);
    return item;
  }

  /* 建弹簧/绳时"这一下点到了什么"：物块、板、轮子都算 */
  function pickHit(point) {
    var index, item;
    var body = hitBody(point, 0.12);
    if (body) return body;
    for (index = STATE.objects.length - 1; index >= 0; index--) {
      item = STATE.objects[index];
      if (item.type !== 'wheel') continue;
      var pose = shownWheel(item.id);
      if (pose && Math.hypot(point[0] - pose.at[0], point[1] - pose.at[1]) <=
          (item.radius || 0.15) + 0.03) return item.id;
    }
    for (index = STATE.objects.length - 1; index >= 0; index--) {
      item = STATE.objects[index];
      if (item.type === 'plate' && insidePlate(item, point)) return item.id;
    }
    return null;
  }

  function cancelPick() {
    if (!STATE.pick) return false;
    STATE.pick = null;
    drawScene();
    return true;
  }

  /* 新工具都是"点几下"：板两下、圆弧三下、轮子一下、弹簧/绳两下 */
  function handlePick(tool, point) {
    if (!STATE.pick || STATE.pick.tool !== tool) {
      STATE.pick = { tool: tool, points: [], cursor: point };
    }
    var pick = STATE.pick;
    pick.cursor = point;
    if (tool === 'wheel') {
      var wheel = addWheel(point);
      STATE.selected = wheel.id;
      STATE.pick = null;
      note('轮子放好了：勾着「纯滚动」它就只能滚，取消勾选才会打滑');
      return;
    }
    if (tool === 'plate') {
      pick.points.push(point);
      if (pick.points.length < 2) {
        note('再点一下另一个角，把板（小车）拖出来');
        return;
      }
      var width = Math.abs(pick.points[1][0] - pick.points[0][0]);
      var height = Math.abs(pick.points[1][1] - pick.points[0][1]);
      if (width < 0.1 || height < 0.05) {
        note('板太小了（至少 0.1 m × 0.05 m），重新点两个角', 'warn');
        pick.points = [];
        return;
      }
      var plate = addPlate(pick.points[0], pick.points[1]);
      STATE.selected = plate.id;
      STATE.pick = null;
      note('板放好了：侧边栏能改质量、宽高、μ地面，勾「固定」它就钉死不动');
      return;
    }
    if (tool === 'arc') {
      pick.points.push(point);
      if (pick.points.length === 1) {
        note('圆弧：这个是圆心，再点弧的起点');
        return;
      }
      if (pick.points.length === 2) {
        note('再点弧的终点（从起点逆时针数，画 1/4 弧就点 90 度那个位置）');
        return;
      }
      var center = pick.points[0], startPoint = pick.points[1], endPoint = pick.points[2];
      var radius = Math.hypot(startPoint[0] - center[0], startPoint[1] - center[1]);
      var from = Math.atan2(startPoint[1] - center[1], startPoint[0] - center[0]) * 180 / Math.PI;
      var to = Math.atan2(endPoint[1] - center[1], endPoint[0] - center[0]) * 180 / Math.PI;
      var sweep = to - from;
      while (sweep <= -180) sweep += 360;
      while (sweep > 180) sweep -= 360;
      if (radius < 0.1) {
        note('半径太小了（至少 0.1 m），重新点圆心', 'warn');
        pick.points = [];
        return;
      }
      if (Math.abs(sweep) < 5) {
        note('弧至少要 5 度：点完起点要顺着弧的方向再点一下终点', 'warn');
        pick.points = [];
        return;
      }
      var lo = from, hi = from + sweep;
      if (hi < lo) { var swap = lo; lo = hi; hi = swap; }
      var guard = 0;
      while (lo < 0 && guard++ < 8) { lo += 360; hi += 360; }
      while (lo >= 360) { lo -= 360; hi -= 360; }
      var arcTrack = { id: 't' + (STATE.seq++), type: 'track', kind: 'circle',
                       center: [center[0], center[1]], radius: radius,
                       mu: numberOr(num('#sbMu', 0.2), 0.2), inner: true,
                       belt: false, v_belt: 0, arc: [lo, hi], body: null };
      STATE.objects.push(arcTrack);
      STATE.selected = arcTrack.id;
      STATE.pick = null;
      note('圆弧画好了：侧边栏里能改起角和张角，也能把它挂到某块板上');
      return;
    }
    /* 弹簧 / 轻绳 / 轻杆 */
    if (!pick.points.length) {
      var owner = pickHit(point);
      if (!owner) {
        note('先点一个物块、板或轮子：弹簧/绳的一端要拴在它上面', 'warn');
        return;
      }
      pick.owner = owner;
      pick.points.push(point);
      note('再点另一端：点另一个物体就拴上去，点空白处就落一个固定点');
      return;
    }
    var bId = pickHit(point);
    if (bId && bId === pick.owner) {
      note('两端不能拴在同一个东西上', 'warn');
      return;
    }
    var joint = addJoint(tool, pick.owner, pick.points[0], point, bId);
    STATE.selected = joint.id;
    STATE.pick = null;
    note(bId ? '两端的物体都拴好了' : '另一端落成了一个固定点（悬挂点）');
  }

  /* 改了初始位置/参数：上一次算的结果作废，免得画面还按旧结果画 */
  function invalidate() {
    if (!STATE.result) return false;
    stopPlay();
    STATE.result = null;
    STATE.index = 0;
    var slider = $('#sbTime');
    if (slider) slider.value = '0';
    if ($('#sbTimeLabel')) $('#sbTimeLabel').textContent = '';
    if ($('#sbValues')) {
      $('#sbValues').innerHTML = '<div class="muted">改了位置或参数：点「开始」重新算一遍</div>';
    }
    clearChart('改了位置或参数，点「开始」重新算一遍');
    note('改了位置或参数：刚才那次结果作废，点「开始」重算', 'warn');
    return true;
  }

  function moveItem(item, drag, delta) {
    if (!item) return;
    if (item.type === 'plate' || item.type === 'wheel' || item.type === 'anchor') {
      if (!drag.at) return;
      item.at = snapPoint([drag.at[0] + delta[0], drag.at[1] + delta[1]]);
      invalidate();
      return;
    }
    if (item.type === 'track' && item.body) {
      /* 挂在板上的轨道按板局部坐标存，拖动的位移要先转到板坐标系里 */
      var pose = trackPose(item);
      var local = rotate(delta, -(pose ? (pose.angle || 0) : 0));
      if (item.kind === 'circle' && drag.center) {
        item.center = [drag.center[0] + local[0], drag.center[1] + local[1]];
      } else if (drag.points) {
        item.points = drag.points.map(function (point) {
          return [point[0] + local[0], point[1] + local[1]];
        });
      }
      invalidate();
      return;
    }
    if (item.points) {
      item.points = drag.points.map(function (point) {
        return snapPoint([point[0] + delta[0], point[1] + delta[1]]);
      });
    } else if (item.center) {
      item.center = snapPoint([drag.center[0] + delta[0], drag.center[1] + delta[1]]);
    } else if (item.rect) {
      item.rect = [drag.origin[0] + delta[0], drag.origin[1] + delta[1],
                   drag.origin[2], drag.origin[3]];
    }
    invalidate();
  }

  /* 轨道换板：把世界坐标的几何原样换算到新板的局部坐标里，看着不动 */
  function setTrackBody(track, plateId) {
    var oldPose = trackPose(track);
    var nextPose = plateId ? shownPlate(plateId) : null;
    var local = function (point) {
      if (!nextPose) return [point[0], point[1]];
      return rotate([point[0] - nextPose.at[0], point[1] - nextPose.at[1]], -(nextPose.angle || 0));
    };
    if (track.kind === 'circle') {
      var world = [track.center[0], track.center[1]];
      if (oldPose) {
        var turn = rotate(world, oldPose.angle || 0);
        world = [oldPose.at[0] + turn[0], oldPose.at[1] + turn[1]];
      }
      track.center = local(world);
    } else {
      track.points = track.points.map(function (point) {
        var at = [point[0], point[1]];
        if (oldPose) {
          var spin = rotate(at, oldPose.angle || 0);
          at = [oldPose.at[0] + spin[0], oldPose.at[1] + spin[1]];
        }
        return local(at);
      });
    }
    track.body = plateId || null;
  }

  /* 删掉一个对象后，把别的对象身上指向它的引用一起清掉，免得留下"野指针" */
  function dropReferences(id) {
    for (var index = STATE.objects.length - 1; index >= 0; index--) {
      var item = STATE.objects[index];
      if (item.type === 'spring' || item.type === 'link') {
        if (item.a === id || item.b === id) STATE.objects.splice(index, 1);
        continue;
      }
      if (item.type === 'track' && item.body === id) item.body = null;
      if (item.type === 'wheel' && item.plate === id) item.plate = null;
    }
  }

  /* ------------------------------------------------------------ 鼠标 */

  function bindCanvas() {
    var svg = $('#sbScene');
    if (!svg) return;
    var tool = function () { return ($('#sbTool') || {}).value || 'select'; };
    var panning = null;

    /* 滚轮缩放（跟着光标）*/
    svg.onwheel = function (event) {
      event.preventDefault();
      zoomAt(event.deltaY < 0 ? 1.15 : 1 / 1.15, event.clientX, event.clientY);
    };
    svg.oncontextmenu = function (event) { event.preventDefault(); };

    svg.onmousedown = function (event) {
      var raw = toWorld(event);            // 命中判定用真实位置（吸附过的点会偏半格，
      var point = snapPoint(raw);          // 物块又小又贴着轨道，一偏就点不着了）
      var mode = tool();
      /* 中键 / 右键 / 「移动视图」工具：平移画布 */
      if (event.button === 1 || event.button === 2 || mode === 'pan') {
        panning = { x: event.clientX, y: event.clientY, viewX: VIEW.x, viewY: VIEW.y };
        svg.className = 'panning';
        event.preventDefault();
        return;
      }
      /* 新工具：板 / 轮 / 圆弧 / 弹簧 / 绳（都是点几下出对象，不是拖拽） */
      if (mode === 'plate' || mode === 'wheel' || mode === 'arc' ||
          mode === 'spring' || mode === 'rope' || mode === 'rod') {
        handlePick(mode, raw);
        renderEditor();
        drawScene();
        return;
      }
      if (mode === 'track') {
        if (!STATE.draft) STATE.draft = { points: [] };
        STATE.draft.points.push(point);
        STATE.draft.cursor = point;
        drawScene();
        return;
      }
      if (mode === 'block') {
        var near = hitBody(raw, 0.09);
        if (near) {
          STATE.selected = near;                 // 点到已有物块：选中它（改数据/拖动）
        } else {
          addBody(snapToTrack(point));            // 点空处：再加一个物块
        }
        renderEditor();
        drawScene();
        return;
      }
      if (mode === 'E' || mode === 'B' || mode === 'battery') {
        STATE.drag = { from: point, to: point, kind: mode, rect: true };
        drawScene();
        return;
      }
      var hit = hitTest(raw);
      STATE.selected = hit;
      if (!hit) {
        /* 空白处按下：先当成"可能要平移"，真拖动了就平移，没动就是取消选中 */
        renderEditor();
        drawScene();
        panning = { x: event.clientX, y: event.clientY, viewX: VIEW.x, viewY: VIEW.y,
                    maybe: true };
        return;
      }
      var dragBody = bodyById(hit);
      if (dragBody) {
        // 拖之前把物块的位置对齐到"现在画的地方"，不然一按下去就跳回初始位置
        var shown = shownAtOf(hit);
        if (shown) dragBody.at = [shown[0], shown[1]];
        STATE.drag = { kind: 'body', id: hit, from: point, origin: dragBody.at.slice(0),
                       rect: false };
      } else {
        var item = byId(hit);
        var handle = handleIndex(item, point);
        if (handle >= 0) {
          STATE.drag = { kind: 'point', id: hit, index: handle, from: point, rect: false };
        } else {
          STATE.drag = { kind: 'move', id: hit, from: point, rect: false,
                         origin: item.rect ? item.rect.slice(0) : null,
                         points: item.points ? item.points.map(function (p) { return p.slice(0); }) : null,
                         center: item.center ? item.center.slice(0) : null,
                         at: item.at ? item.at.slice(0) : null };
        }
      }
      renderEditor();
      drawScene();
    };
    svg.onmousemove = function (event) {
      if (panning) {                       // 平移：把像素差换算成用户单位
        if (panning.maybe) {
          if (Math.abs(event.clientX - panning.x) < 3 && Math.abs(event.clientY - panning.y) < 3) {
            return;                        // 还没动够，先不算平移
          }
          panning.maybe = false;
          svg.className = 'panning';
        }
        var box = svg.getBoundingClientRect();
        var size = viewSize();
        VIEW.x = panning.viewX - (event.clientX - panning.x) * (size[0] / box.width);
        VIEW.y = panning.viewY - (event.clientY - panning.y) * (size[1] / box.height);
        applyView();
        drawScene();
        return;
      }
      var point = snapPoint(toWorld(event));
      STATE.hover = point;
      var readout = $('#sbCursor');
      if (readout) readout.textContent = '坐标 (' + point[0].toFixed(2) + ', ' + point[1].toFixed(2) + ') m';
      if (STATE.draft) STATE.draft.cursor = point;
      if (STATE.pick) STATE.pick.cursor = point;
      if (STATE.drag) {
        var delta = [point[0] - STATE.drag.from[0], point[1] - STATE.drag.from[1]];
        if (STATE.drag.rect) {
          STATE.drag.to = point;
        } else if (STATE.drag.kind === 'body') {
          var dragged = bodyById(STATE.drag.id);
          if (dragged) {
            dragged.at = snapPoint([STATE.drag.origin[0] + delta[0],
                                    STATE.drag.origin[1] + delta[1]]);
            invalidate();
          }
        } else {
          var item = byId(STATE.drag.id);
          if (item && STATE.drag.kind === 'point') {
            if (item.kind === 'circle') {
              /* 半径把手：量的是"世界坐标里到圆心"的距离（板挂着的轨道会跟着转） */
              var geo = circleWorld(item);
              item.radius = Math.max(0.05, Math.abs(Math.hypot(point[0] - geo.center[0],
                                                              point[1] - geo.center[1])));
            } else if (item.body) {
              /* 挂在板上的轨道顶点按板局部坐标存 */
              var pose = trackPose(item);
              item.points[STATE.drag.index] =
                rotate([point[0] - pose.at[0], point[1] - pose.at[1]], -(pose.angle || 0));
            } else {
              item.points[STATE.drag.index] = point;
            }
            invalidate();
          } else if (item) {
            moveItem(item, STATE.drag, delta);
          }
        }
      }
      drawScene();
    };
    svg.onmouseup = function (event) {
      if (panning) {
        panning = null;
        svg.className = '';
        return;
      }
      if (STATE.drag && STATE.drag.rect) {
        var drag = STATE.drag;
        drag.to = snapPoint(toWorld(event));
        var x = Math.min(drag.from[0], drag.to[0]), y = Math.min(drag.from[1], drag.to[1]);
        var width = Math.abs(drag.to[0] - drag.from[0]), height = Math.abs(drag.to[1] - drag.from[1]);
        if (width < 0.2 || height < 0.2) {
          note('这个区域太小了（至少 0.2 m × 0.2 m）', 'warn');
        } else if (drag.kind === 'battery') {
          STATE.objects.push({ id: 'b' + (STATE.seq++), type: 'battery', rect: [x, y, width, height],
                               voltage: num('#sbVoltage', 100),
                               dir: width >= height ? 'down' : 'right' });
        } else {
          STATE.objects.push({ id: 'f' + (STATE.seq++), type: 'field', kind: drag.kind,
                               rect: [x, y, width, height],
                               value: drag.kind === 'B' ? num('#sbFieldValue', 0.5)
                                                        : Math.abs(num('#sbFieldValue', 0.5)),
                               dir: width >= height ? 'down' : 'right' });
        }
        STATE.selected = STATE.objects[STATE.objects.length - 1].id;
        renderEditor();
      }
      STATE.drag = null;
      drawScene();
    };
    svg.onmouseleave = function () { STATE.hover = null; drawScene(); };
    svg.ondblclick = function () {
      if (STATE.pick) { cancelPick(); return; }
      if (STATE.draft) endTrack();
    };
  }

  /* 抓住的是哪个控制点（轨道顶点 / 圆环右侧的半径把手） */
  function handleIndex(item, point, tolerance) {
    var reach = tolerance === undefined ? 0.15 : tolerance;
    if (!item) return -1;
    if (item.kind === 'circle') {
      var geo = circleWorld(item);
      var right = [geo.center[0] + item.radius, geo.center[1]];
      return Math.hypot(point[0] - right[0], point[1] - right[1]) < reach ? 0 : -1;
    }
    var points = trackWorldPoints(item);
    for (var index = 0; index < points.length; index++) {
      if (Math.hypot(point[0] - points[index][0], point[1] - points[index][1]) < reach) return index;
    }
    return -1;
  }

  /* 找点附近的物块（用于"点一下选中"） */
  function hitBody(point, margin) {
    var best = null;
    STATE.bodies.forEach(function (body, index) {
      var where = shownAt(index) || body.at;      // 也用"现在画的地方"
      var gap = Math.hypot(point[0] - where[0], point[1] - where[1]) - body.radius;
      if (gap < (margin || 0) && (!best || gap < best.gap)) best = { gap: gap, id: body.id };
    });
    return best ? best.id : null;
  }

  function snapToTrack(point) {
    var best = null;
    tracks().forEach(function (track) {
      if (track.kind === 'circle') {
        var geo = circleWorld(track);
        var dx = point[0] - geo.center[0], dy = point[1] - geo.center[1];
        var length = Math.hypot(dx, dy) || 1e-9;
        var distance = Math.abs(length - track.radius);
        if (arcOf(track)) {
          var angle = Math.atan2(dy, dx) * 180 / Math.PI - (geo.angle || 0);
          if (!inArc(track, angle)) return;        // 弧段之外的圆面不是轨道
        }
        var at = [geo.center[0] + dx / length * track.radius,
                  geo.center[1] + dy / length * track.radius];
        if (distance < 0.2 && (!best || distance < best.distance)) best = { distance: distance, at: at };
        return;
      }
      var points = trackWorldPoints(track);
      for (var index = 0; index < points.length - 1; index++) {
        var a = points[index], b = points[index + 1];
        var vx = b[0] - a[0], vy = b[1] - a[1];
        var length2 = vx * vx + vy * vy;
        var t = length2 ? ((point[0] - a[0]) * vx + (point[1] - a[1]) * vy) / length2 : 0;
        t = Math.max(0, Math.min(1, t));
        var at2 = [a[0] + vx * t, a[1] + vy * t];
        var distance2 = Math.hypot(point[0] - at2[0], point[1] - at2[1]);
        if (distance2 < 0.2 && (!best || distance2 < best.distance)) {
          best = { distance: distance2, at: at2 };
        }
      }
    });
    return best ? [best.at[0], best.at[1]] : point;
  }

  /* ------------------------------------------------------------ 跑与播放 */

  /* 序列化成引擎的场景 JSON：字段名严格按 docs\sandbox-linked-bodies.md 第 1 节。
     新对象的顶层键**只在有对象时才写**（旧例子输出一个字节都不变）。 */
  function scene() {
    var tracksOut = [], fieldsOut = [], platesOut = [], wheelsOut = [];
    var springsOut = [], linksOut = [], anchorsOut = [];
    STATE.objects.forEach(function (item) {
      if (item.type === 'track') {
        var row = { id: item.id, kind: item.kind, mu: item.mu,
                    belt: !!item.belt, v_belt: item.v_belt || 0 };
        if (item.kind === 'circle') {
          row.center = item.center;
          row.radius = item.radius;
          row.inner = item.inner !== false;
          if (item.arc && item.arc.length) row.arc = [item.arc[0], item.arc[1]];
        } else {
          row.points = item.points;
        }
        if (item.body) row.body = item.body;
        tracksOut.push(row);
      } else if (item.type === 'plate') {
        platesOut.push({ id: item.id, name: item.name, at: item.at, size: item.size,
                         mass: item.mass, v: item.v, angle: item.angle || 0,
                         lock_rotation: item.lock_rotation !== false, fixed: !!item.fixed,
                         mu_ground: item.mu_ground || 0, support: item.support || 'ground' });
      } else if (item.type === 'wheel') {
        wheelsOut.push({ id: item.id, name: item.name, at: item.at, radius: item.radius,
                         mass: item.mass, v: item.v, roll: item.roll !== false,
                         mu: item.mu || 0, plate: item.plate || null });
      } else if (item.type === 'spring') {
        /* 引擎的弹簧只认 a / b：空端要写成 b 的坐标（写 anchor 键会被 400 打回，实测过） */
        var springEnd = item.b || anchorAt(item);
        var springRow = { id: item.id, name: item.name, a: item.a, at_a: item.at_a,
                          b: springEnd || null, at_b: item.at_b, k: item.k,
                          rest: item.rest, damping: item.damping || 0 };
        springsOut.push(springRow);
      } else if (item.type === 'link') {
        /* b 为空 -> anchor 是世界固定点；b 和 anchor 都有 -> 绳绕过定滑轮（引擎的扩展用法） */
        var linkRow = { id: item.id, name: item.name, kind: item.kind, a: item.a,
                        at_a: item.at_a, b: item.b || null, at_b: item.at_b,
                        length: item.length };
        var linkAnchor = anchorAt(item);
        if (linkAnchor) linkRow.anchor = linkAnchor;
        linksOut.push(linkRow);
      } else if (item.type === 'anchor') {
        var anchorPoint = refPoint(item.at);
        if (anchorPoint) anchorsOut.push({ id: item.id, at: anchorPoint, name: item.name });
      } else if (item.type === 'battery') {
        fieldsOut.push({ id: item.id, rect: item.rect, E: batteryField(item) });
      } else if (item.type === 'field') {
        if (item.kind === 'B') {
          fieldsOut.push({ id: item.id, rect: item.rect, B: item.value });
        } else {
          var unit = { up: [0, 1], down: [0, -1], right: [1, 0], left: [-1, 0] }[item.dir || 'down'];
          fieldsOut.push({ id: item.id, rect: item.rect,
                           E: [unit[0] * Math.abs(item.value), unit[1] * Math.abs(item.value)] });
        }
      }
    });
    var out = {
      tracks: tracksOut, fields: fieldsOut,
      bodies: STATE.bodies.map(function (body) {
        return { id: body.id, name: body.name, mass: body.mass, charge: body.charge,
                 radius: body.radius, at: body.at, v: body.v };
      }),
      forces: STATE.bodies.some(bodyFlag),
      restitution: Math.min(1, Math.max(0, num('#sbE', 1.0))),
      g: { value: num('#sbG', 9.8), dir: ($('#sbGdir') || {}).value || 'down' },
      duration: STATE.duration || 8.0, dt: STATE.dt || 0.0005
    };
    if (platesOut.length) out.plates = platesOut;
    if (wheelsOut.length) out.wheels = wheelsOut;
    if (springsOut.length) out.springs = springsOut;
    if (linksOut.length) out.links = linksOut;
    if (anchorsOut.length) out.anchors = anchorsOut;
    return out;
  }

  /* ------------------------------------------------------------ 图像（M8）

     x/y 轴各填一个记号（`t`、`Ek物块1`、`a小车`…），算完一遍就把整条曲线画出来；
     播放/拖时间条时只挪那个红点（用服务端给的 meta 自己算像素，不重新请求）。 */

  function symbolOf(name) {
    return String(name || '').replace(/\s+/g, '');
  }

  function bodyNames() {
    return STATE.bodies.map(function (body) { return symbolOf(body.name); });
  }

  function defaultAxes(force) {
    var x = $('#sbChartX');
    var y = $('#sbChartY');
    if (!x || !y) return;
    var firstBody = bodyNames()[0] || '物块1';
    var firstPlate = plates()[0];
    if (force || !(x.value || '').trim()) x.value = 't';
    if (force || !(y.value || '').trim()) {
      /* 有板的时候默认看板的速度（v小车），没有板才看第一个物块的动能 */
      y.value = firstPlate ? ('v' + symbolOf(firstPlate.name || '')) : ('Ek(' + firstBody + ')');
    }
  }

  function clearChart(text) {
    var box = $('#sbChart');
    if (box) box.innerHTML = '<div class="muted" style="padding:8px">' + (text || '') + '</div>';
  }

  function drawChart() {
    var x = $('#sbChartX');
    var y = $('#sbChartY');
    var box = $('#sbChart');
    if (!box || !x || !y) return Promise.resolve(null);
    if (!(x.value || '').trim() || !(y.value || '').trim()) {
      clearChart('x 轴和 y 轴都要填一个记号，比如 t / Ek物块1（写法见右边说明表）');
      return Promise.resolve(null);
    }
    if (!(STATE.result && (STATE.result.frames || []).length)) {
      clearChart('先点「算一遍」，然后把 x/y 轴填成想看的量（比如 x 填 t、y 填 Ek物块1）');
      return Promise.resolve(null);
    }
    note('画图中…');
    var payload = { action: 'chart', scene: scene(), x: x.value.trim(), y: y.value.trim(),
                    index: STATE.index };
    var ranges = window.W7ChartUI.readRanges('sbChart');
    if (ranges.x_range) { payload.xmin = ranges.x_range[0]; payload.xmax = ranges.x_range[1]; }
    if (ranges.y_range) { payload.ymin = ranges.y_range[0]; payload.ymax = ranges.y_range[1]; }
    return post(payload).then(function (data) {
      if (!data || !data.ok) {
        clearChart((data && data.error) || '画不出来');
        note((data && data.error) || '画不出来', 'warn');
        return null;
      }
      STATE.chart = data;
      window.W7ChartUI.paint('#sbChart', data, { help: '#sbChartHelp',
                                                 legend: '#sbChartLegend' });
      window.W7ChartUI.moveCursor('#sbChart', data.points, data.cursor, data.meta);
      note('图像：' + data.x_label + ' - ' + data.y_label + '，' + data.points.length + ' 个点');
      return data;
    });
  }

  /* 把红点挪到第 index 个采样点（播放时每帧都会调，纯本地计算） */
  function moveChartCursor(points, index, meta) {
    var chart = STATE.chart || {};
    window.W7ChartUI.moveCursor('#sbChart', points || chart.points,
                                (index === undefined || index === null) ? chart.cursor : index,
                                meta || chart.meta);
  }

  /* 侧边栏那个"显示受力"开关：打开时如果当前结果没有受力数据就重算一遍 */
  function refreshForces() {
    if (forcesWanted() && (STATE.result && STATE.result.frames || []).length &&
        !frameHasForces()) {
      note('正在算受力…');
      return run().catch(function (error) { note(String(error), 'warn'); });
    }
    drawScene();
    note(forcesWanted() ? '受力箭头：灰=重力、红=电场力、紫=洛伦兹力、蓝=法向力、橙=摩擦力、黑=合力'
                        : '这个物块不显示受力了');
    return Promise.resolve(null);
  }

  function run() {
    if (STATE.draft && STATE.draft.points.length > 1) endTrack();
    var hasField = STATE.objects.some(function (item) {
      return item.type === 'field' || item.type === 'battery';
    });
    /* 板/轮/弹簧/绳自己就能动（比如小车被弹簧推），没有轨道也允许算 */
    var hasJoint = STATE.objects.some(function (item) {
      return item.type === 'plate' || item.type === 'wheel' ||
             item.type === 'spring' || item.type === 'link';
    });
    var moving = STATE.bodies.some(function (body) {
      return Math.hypot(body.v[0] || 0, body.v[1] || 0) > 1e-9;
    });
    if (!tracks().length && !hasField && !hasJoint && !moving) {
      note('先画一条轨道、摆一个场区，或者给物块一个初速度', 'warn');
      return Promise.resolve(null);
    }
    stopPlay();
    note('正在算……');
    return post({ action: 'run', scene: scene() }).then(function (data) {
      if (!data || !data.ok) {
        note((data && data.error) || '算不出来', 'warn');
        return data;
      }
      STATE.result = data;
      STATE.index = 0;
      var frames = data.frames || [];
      var slider = $('#sbTime');
      if (slider) { slider.max = String(Math.max(0, frames.length - 1)); slider.value = '0'; }
      note('算完了：' + frames.length + ' 帧，' + (data.events || []).length + ' 个事件');
      renderValues(data);
      renderCards(data);
      defaultAxes(false);
      renderFrame(0);
      drawChart();
      startPlay();
      return data;
    });
  }

  function renderValues(data) {
    var box = $('#sbValues');
    if (!box) return;
    var values = data.values || {};
    var rows = [['time', '总用时', 's'], ['heat', '摩擦生热', 'J'],
                ['spring_energy', '弹簧势能', 'J'],
                ['landing_loss', '落回损失', 'J'], ['collision_loss', '碰撞损失', 'J'],
                ['collision_count', '碰撞次数', ''], ['work_electric', '电场力做功', 'J'],
                ['energy_gap', '能量对账', 'J']];
    var html = rows.filter(function (row) {
      /* 引擎没算的量不显示（旧例子没有弹簧/板，别印出 undefined） */
      return values[row[0]] !== undefined && values[row[0]] !== null;
    }).map(function (row) {
      return '<div class="phys-value"><span>' + row[1] + '</span><b>' + values[row[0]] + ' ' +
        row[2] + '</b></div>';
    }).join('');
    if (values.plates_end && values.plates_end.length) {
      var plateNames = plates().map(function (item) { return item.name; });
      html += values.plates_end.map(function (plate, order) {
        var vx = (plate && plate.length > 3) ? plate[2] : ((plate && plate[0]) || 0);
        var vy = (plate && plate.length > 3) ? plate[3] : ((plate && plate[1]) || 0);
        return '<div class="phys-value"><span>' + (plateNames[order] || ('板' + (order + 1))) +
          ' 末速度</span><b>vx=' + vx + '　vy=' + vy + ' m/s</b></div>';
      }).join('');
    }
    if (values.momentum_start) {
      html += '<div class="phys-value"><span>总动量 碰前/碰后</span><b>(' +
        values.momentum_start.join(', ') + ') -> (' + values.momentum_end.join(', ') +
        ') kg·m/s</b></div>';
    }
    box.innerHTML = html;
  }

  function renderCards(data) {
    var box = $('#sbCards');
    if (!box) return;
    box.innerHTML = (data.cards || []).map(function (card, order) {
      return '<div class="phys-card"><div class="phys-card-head">' + (order + 1) + '. ' + card.title +
        (card.law ? '<span class="muted">　' + card.law + '</span>' : '') + '</div>' +
        (card.forces ? '<div class="phys-line">' + card.forces + '</div>' : '') +
        (card.equations ? '<div class="phys-line">方程：' + card.equations + '</div>' : '') +
        (card.result ? '<div class="phys-line phys-line-key">结果：' + card.result + '</div>' : '') +
        '</div>';
    }).join('');
  }

  function renderFrame(index) {
    var frames = (STATE.result && STATE.result.frames) || [];
    if (!frames.length) return;
    STATE.index = Math.max(0, Math.min(frames.length - 1, index));
    drawScene();
    var frame = frames[STATE.index];
    var label = $('#sbTimeLabel');
    if (label) {
      var text = 't=' + frame.t + ' s　(' + frame.x + ', ' + frame.y + ')　v=' + frame.speed +
        ' m/s' + (frame.on ? '　在轨道上' : '　在空中');
      /* 有板的时候顺手报一下第一块板的速度：小车在做加速还是匀速一眼能看出来 */
      if (frame.plates && frame.plates.length && frame.plates[0]) {
        text += '　板 vx=' + frame.plates[0][2] + ' m/s';
      }
      label.textContent = text;
    }
    var slider = $('#sbTime');
    if (slider && slider.value !== String(STATE.index)) slider.value = String(STATE.index);
    moveChartCursor(null, STATE.index, null);
  }

  function stopPlay() {
    if (STATE.timer) { clearInterval(STATE.timer); STATE.timer = null; }
    var button = $('#sbPlayBtn');
    if (button) button.textContent = '重放';
  }

  function startPlay() {
    var frames = (STATE.result && STATE.result.frames) || [];
    if (!frames.length) return;
    var button = $('#sbPlayBtn');
    if (button) button.textContent = '暂停';
    STATE.timer = setInterval(function () {
      if (STATE.index >= frames.length - 1) { stopPlay(); return; }
      renderFrame(STATE.index + 1);
    }, 25);
  }

  function endTrack() {
    if (!STATE.draft) return;
    if (STATE.draft.points.length >= 2) {
      STATE.objects.push({ id: 't' + (STATE.seq++), type: 'track', kind: 'line',
                           points: STATE.draft.points,
                           mu: muArray(num('#sbMu', 0.2), STATE.draft.points.length - 1) });
      STATE.selected = STATE.objects[STATE.objects.length - 1].id;
      renderEditor();
    } else {
      note('一条轨道至少要点两个点', 'warn');
    }
    STATE.draft = null;
    drawScene();
  }

  /* ------------------------------------------------------------ 示例 / 清空 / 标签 */

  /* 例子库（M8）：装载一个现成题目，并把建议的坐标轴填上 */
  function loadExamples() {
    return post({ action: 'examples' }).then(function (data) {
      var pick = $('#sbExample');
      if (!pick || !data || !data.ok) return;
      STATE.examples = data.examples || [];
      pick.innerHTML = STATE.examples.map(function (item) {
        return '<option value="' + item.key + '">' + item.name + '</option>';
      }).join('');
    });
  }

  /* 场景 JSON 里引用的 id：先按 id 认，再按名字认（引擎的 name 是给人看和图像记号用的） */
  function resolveSceneRef(value) {
    if (value === undefined || value === null || value === '') return null;
    var index, item;
    for (index = 0; index < STATE.bodies.length; index++) {
      if (STATE.bodies[index].id === value) return value;
    }
    for (index = 0; index < STATE.objects.length; index++) {
      if (STATE.objects[index].id === value) return value;
    }
    for (index = 0; index < STATE.bodies.length; index++) {
      if (symbolOf(STATE.bodies[index].name) === symbolOf(value)) return STATE.bodies[index].id;
    }
    for (index = 0; index < STATE.objects.length; index++) {
      item = STATE.objects[index];
      if (symbolOf(item.name || '') === symbolOf(value)) return item.id;
    }
    return null;
  }

  function applyScene(scene, axes) {
    STATE.objects = [];
    (scene.plates || []).forEach(function (plate, order) {
      STATE.objects.push({
        id: plate.id || ('m' + (order + 1)), type: 'plate',
        name: plate.name || ('板 ' + (order + 1)),
        at: (plate.at || [1, 0.5]).slice(0),
        size: (plate.size || [1.0, 0.3]).slice(0),
        mass: plate.mass || 1, v: (plate.v || [0, 0]).slice(0),
        angle: plate.angle || 0,
        lock_rotation: plate.lock_rotation !== false, fixed: !!plate.fixed,
        mu_ground: plate.mu_ground || 0, support: plate.support || 'ground'
      });
    });
    (scene.wheels || []).forEach(function (wheel, order) {
      STATE.objects.push({
        id: wheel.id || ('w' + (order + 1)), type: 'wheel',
        name: wheel.name || ('轮子 ' + (order + 1)),
        at: (wheel.at || [0.5, 0.15]).slice(0), radius: wheel.radius || 0.15,
        mass: wheel.mass || 0.5, v: (wheel.v || [0, 0]).slice(0),
        roll: wheel.roll !== false, mu: wheel.mu || 0, plate: wheel.plate || null
      });
    });
    (scene.anchors || []).forEach(function (anchor, order) {
      var at = refPoint(anchor.at);
      if (!at) return;                     // 坐标不是数字的悬挂点直接丢掉，别让它污染场景
      STATE.objects.push({
        id: anchor.id || ('a' + (order + 1)), type: 'anchor',
        name: anchor.name || ('悬挂点 ' + (order + 1)),
        at: at
      });
    });
    (scene.tracks || []).forEach(function (track, order) {
      var item;
      if (track.kind === 'circle') {
        item = { id: track.id || ('c' + (order + 1)), type: 'track', kind: 'circle',
                 center: (track.center || [3, 1.5]).slice(0),
                 radius: track.radius || 0.6, mu: track.mu || 0,
                 inner: track.inner !== false, belt: !!track.belt, v_belt: track.v_belt || 0 };
      } else {
        item = { id: track.id || ('t' + (order + 1)), type: 'track', kind: 'line',
                 points: (track.points || []).map(function (point) { return point.slice(0); }),
                 mu: (track.mu instanceof Array) ? track.mu.slice(0) : muArray(track.mu || 0,
                       Math.max(1, (track.points || []).length - 1)),
                 belt: !!track.belt, v_belt: track.v_belt || 0 };
      }
      if (track.arc && track.arc.length) item.arc = [track.arc[0], track.arc[1]];
      item.body = track.body || null;
      STATE.objects.push(item);
    });
    (scene.fields || []).forEach(function (field, order) {
      var item = { id: field.id || ('f' + (order + 1)), type: 'field', kind: 'E',
                   rect: (field.rect || [0, 0, 1, 1]).slice(0), value: 0, dir: 'down' };
      if (field.B !== undefined && field.B !== null) { item.kind = 'B'; item.value = field.B; }
      else {
        var e = field.E || [0, 0];
        var size = Math.hypot(e[0], e[1]) || 1;
        item.value = size;
        item.dir = Math.abs(e[1]) > Math.abs(e[0]) ? (e[1] >= 0 ? 'up' : 'down')
                                                   : (e[0] >= 0 ? 'right' : 'left');
      }
      STATE.objects.push(item);
    });
    STATE.bodies = (scene.bodies || []).map(function (body, order) {
      return { id: body.id || ('p' + (order + 1)), name: body.name || ('物块 ' + (order + 1)),
               mass: body.mass || 1, charge: body.charge || 0, radius: body.radius || 0.06,
               at: (body.at || [0, 0]).slice(0), v: (body.v || [0, 0]).slice(0),
               showForces: body.showForces !== false };
    });
    (scene.springs || []).forEach(function (spring, order) {
      var bId = resolveSceneRef(refId(spring.b));
      var bPoint = refPoint(spring.b);              // b 直接写坐标 = 世界固定端
      var end = anchorRefInfo(spring.anchor);       // 兼容空端写 anchor 的旧写法
      STATE.objects.push({
        id: spring.id || ('s' + (order + 1)), type: 'spring',
        name: spring.name || ('弹簧 ' + (order + 1)),
        a: resolveSceneRef(refId(spring.a)), at_a: pairOf(spring.at_a),
        b: bId || null, at_b: pairOf(spring.at_b),
        anchor: bId ? null : (bPoint || end.at || null), anchorId: bId ? null : (end.id || null),
        k: spring.k !== undefined ? spring.k : 200,
        rest: spring.rest !== undefined ? spring.rest : 0.5,
        damping: spring.damping || 0
      });
    });
    (scene.links || []).forEach(function (link, order) {
      var bId = resolveSceneRef(refId(link.b));
      var bPoint = refPoint(link.b);
      var end = anchorRefInfo(link.anchor);         // id 字符串 "O" / {id} / [x, y] 都认
      STATE.objects.push({
        id: link.id || ('l' + (order + 1)), type: 'link',
        kind: link.kind === 'rod' ? 'rod' : 'rope',
        name: link.name || ((link.kind === 'rod' ? '轻杆 ' : '轻绳 ') + (order + 1)),
        a: resolveSceneRef(refId(link.a)), at_a: pairOf(link.at_a),
        b: bId || null, at_b: pairOf(link.at_b),
        anchor: end.at || bPoint || null, anchorId: end.id || null,
        length: link.length !== undefined ? link.length : 1
      });
    });
    /* 只有坐标、没有悬挂点对象的空端：补一个悬挂点，编辑器里才看得见也点得中 */
    var extra = 0;
    STATE.objects.forEach(function (item) {
      if ((item.type !== 'spring' && item.type !== 'link') || item.b || item.anchorId) return;
      if (!item.anchor) return;
      var anchor = null, index;
      for (index = 0; index < STATE.objects.length; index++) {
        var candidate = STATE.objects[index];
        if (candidate.type !== 'anchor') continue;
        if (Math.hypot(candidate.at[0] - item.anchor[0], candidate.at[1] - item.anchor[1]) < 0.05) {
          anchor = candidate;
          break;
        }
      }
      if (!anchor) {
        extra++;
        anchor = { id: 'ax' + extra, type: 'anchor', name: '固定点 ' + extra,
                   at: [item.anchor[0], item.anchor[1]] };
        STATE.objects.push(anchor);
      }
      item.anchorId = anchor.id;
      item.anchor = [anchor.at[0], anchor.at[1]];
    });
    STATE.seq = 20;
    STATE.selected = null;
    STATE.draft = null;
    STATE.result = null;
    STATE.chart = null;
    STATE.duration = (typeof scene.duration === 'number' && scene.duration > 0)
      ? scene.duration : 8.0;
    STATE.dt = scene.dt || 0.0005;
    if ($('#sbG')) {
      var g = scene.g;
      var magnitude = (g === undefined || g === null) ? 9.8
        : (typeof g === 'number' ? Math.abs(g) : (g.value !== undefined ? Math.abs(g.value)
                                                  : Math.hypot((g[0] || 0), (g[1] || 0))));
      $('#sbG').value = magnitude;
      if ($('#sbGdir')) {
        var dir = 'down';
        if (typeof g === 'object' && g && g.dir) dir = g.dir;
        else if (g instanceof Array) {
          if (Math.abs(g[1] || 0) >= Math.abs(g[0] || 0)) dir = (g[1] >= 0 ? 'up' : 'down');
          else dir = (g[0] >= 0 ? 'right' : 'left');
        } else if (typeof g === 'number') dir = g >= 0 ? 'down' : 'up';
        $('#sbGdir').value = dir;
      }
    }
    if ($('#sbE') && scene.restitution !== undefined) $('#sbE').value = scene.restitution;
    var axes2 = axes || {};
    if ($('#sbChartX') && axes2.x) $('#sbChartX').value = axes2.x;
    if ($('#sbChartY') && axes2.y) $('#sbChartY').value = axes2.y;
    resetView();          // 每个例子都从默认视野开始（上一题的缩放/平移不该带过来）
    applyView();
    drawScene();
    renderEditor();
    STATE.index = 0;
    if ($('#sbTime')) { $('#sbTime').value = '0'; $('#sbTimeLabel').textContent = ''; }
  }

  function loadExample(key) {
    return post({ action: 'example', key: key }).then(function (data) {
      if (!data || !data.ok) {
        note((data && data.error) || '装不上这个例子', 'warn');
        return null;
      }
      var item = data.example;
      applyScene(item.scene, item.chart);
      note(item.name + '：' + item.note);
      return run();
    });
  }

  function loadDemo() {
    return post({ action: 'scene_demo' }).then(function (data) {
      if (!data || !data.ok) { note('拿不到示例场景', 'warn'); return; }
      STATE.duration = (typeof data.scene.duration === 'number' && data.scene.duration > 0)
        ? data.scene.duration : 8.0;
      STATE.dt = data.scene.dt || 0.0005;
      STATE.objects = (data.scene.tracks || []).map(function (track, order) {
        if (track.kind === 'circle') {
          return { id: 't' + (order + 1), type: 'track', kind: 'circle', center: track.center,
                   radius: track.radius, inner: track.inner !== false, mu: numberOr(track.mu, 0) };
        }
        return { id: 't' + (order + 1), type: 'track', kind: 'line', points: track.points,
                 mu: muArray(track.mu, track.points.length - 1) };
      });
      (data.scene.fields || []).forEach(function (item, order) {
        if (item.B !== undefined && item.B !== null) {
          STATE.objects.push({ id: 'f' + (order + 1), type: 'field', kind: 'B', rect: item.rect,
                               value: item.B });
        } else {
          var e = item.E || [0, 0];
          STATE.objects.push({ id: 'f' + (order + 1), type: 'field', kind: 'E', rect: item.rect,
                               value: Math.hypot(e[0], e[1]),
                               dir: Math.abs(e[1]) >= Math.abs(e[0])
                                 ? (e[1] >= 0 ? 'up' : 'down') : (e[0] >= 0 ? 'right' : 'left') });
        }
      });
      STATE.seq = STATE.objects.length + 1;
      STATE.bodies = (data.scene.bodies || [data.scene.block]).map(function (item, order) {
        return { id: 'p' + (order + 1), name: item.name || ('物块 ' + (order + 1)),
                 mass: item.mass, charge: item.charge, radius: item.radius || 0.06,
                 at: (item.at || [0, 0]).slice(0), v: (item.v || [0, 0]).slice(0) };
      });
      if (data.scene.restitution !== undefined && $('#sbE')) {
        $('#sbE').value = data.scene.restitution;
      }
      var g = data.scene.g;
      if (typeof g === 'number') {
        if ($('#sbG')) $('#sbG').value = Math.abs(g);
        if ($('#sbGdir')) $('#sbGdir').value = g >= 0 ? 'down' : 'up';
      }
      STATE.result = null;
      STATE.selected = null;
      renderEditor();
      drawScene();
      note('示例题已装好：点「开始」；选「选择 / 拖动」点一下任意东西就能改它的数据');
    });
  }

  function clearAll() {
    stopPlay();
    STATE.objects = [];
    STATE.bodies = [];
    STATE.draft = null;
    STATE.pick = null;
    STATE.result = null;
    STATE.selected = null;
    STATE.index = 0;
    renderEditor();
    drawScene();
    note('');
  }

  /* 沙盒现在就是"物理"本身（三个现成模型那一版已经删掉），打开即整屏画板 */
  function openPanel() {
    var panel = $('#sandboxPanel');
    if (panel) panel.classList.remove('hidden');
    applyView();
    drawScene();
    renderEditor();
    /* 刚显示出来的那一瞬间容器还没排版完（量到的还是旧高度），下一帧再量一次 */
    setTimeout(function () { applyView(); drawScene(); }, 60);
    if (!STATE.objects.length && !STATE.draft && !STATE.result) {
      loadDemo().catch(function (error) { note(String(error), 'warn'); });
    }
  }

  function closePanel() {
    var panel = $('#sandboxPanel');
    if (panel) panel.classList.add('hidden');
  }

  function bind() {
    bindCanvas();
    var on = function (selector, handler) {
      var node = $(selector);
      if (node) node.onclick = handler;
    };
    on('#sbLaunch', function () { openPanel(); });
    on('#sbClose', function () { closePanel(); });
    on('#prLaunch', function () {
      closePanel();
      if (window.W7Practice) window.W7Practice.open();
    });
    on('#sbZoomIn', function () { zoomAt(1.25); });
    on('#sbZoomOut', function () { zoomAt(1 / 1.25); });
    on('#sbZoomFit', function () { resetView(); });
    on('#sbRunBtn', function () { run().catch(function (error) { note(String(error), 'warn'); }); });
    on('#sbPlayBtn', function () { if (STATE.timer) stopPlay(); else startPlay(); });
    on('#sbUndoBtn', function () {
      /* 放板/拉弹簧这种点几步才成形的操作，撤销先退它的点 */
      if (STATE.pick && STATE.pick.points && STATE.pick.points.length) {
        STATE.pick.points.pop();
        drawScene();
        return;
      }
      if (STATE.draft && STATE.draft.points.length) STATE.draft.points.pop();
      drawScene();
    });
    on('#sbEndTrackBtn', function () { endTrack(); });
    on('#sbDeleteBtn', function () { deleteSelected(); });
    on('#sbDemoBtn', function () { loadDemo().catch(function (error) { note(String(error), 'warn'); }); });
    loadExamples().catch(function () { /* 例子表拿不到不影响画板 */ });
    on('#sbClearBtn', function () { clearAll(); });
    on('#sbChartDraw', function () { drawChart(); });
    on('#sbExampleLoad', function () {
      loadExample(($('#sbExample') || {}).value).catch(function (error) {
        note(String(error), 'warn');
      });
    });
    on('#sbChartSave', function () {
      if (!window.W7ChartUI) return;
      window.W7ChartUI.exportPng('#sbChart', '物理图像').then(function (info) {
        note('图像已存成 PNG（' + info.width + 'x' + info.height + '）');
      }).catch(function (error) { note(String(error && error.message || error), 'warn'); });
    });
    on('#sbChartAuto', function () {
      window.W7ChartUI.clearRanges('sbChart');
      drawChart();
    });
    on('#btnPhysics', function () { openPanel(); });
    if (window.W7ChartUI) {
      window.W7ChartUI.bindGrip('#sbGrip', '#sandboxPanel .sandbox-main');
      window.W7ChartUI.bindWindow('#sandboxPanel', [['#sbWinE', 'e'], ['#sbWinS', 's'],
                                                    ['#sbWinSE', 'se']]);
    }
    var slider = $('#sbTime');
    if (slider) slider.oninput = function () { stopPlay(); renderFrame(parseInt(slider.value, 10) || 0); };
    var tool = $('#sbTool');
    if (tool) tool.onchange = function () {
      if (STATE.draft) endTrack();
      cancelPick();
      drawScene();
    };
    document.addEventListener('keydown', function (event) {
      if (event.key === 'Escape') {
        var panel = $('#sandboxPanel');
        if (panel && !panel.classList.contains('hidden')) {
          if (STATE.pick && STATE.pick.points && STATE.pick.points.length) {
            cancelPick();
            drawScene();
            return;
          }
          if (STATE.draft) { endTrack(); return; }
          closePanel();
          return;
        }
      }
      if (event.key !== 'Delete' && event.key !== 'Backspace') return;
      var tag = (event.target && event.target.tagName) || '';
      if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') return;
      if (STATE.selected) { event.preventDefault(); deleteSelected(); }
    });
    applyView();
    /* 窗口尺寸变了（拖窗口、投到大屏）画布要重新量一次，不然 viewBox 对不上容器 */
    window.addEventListener('resize', function () {
      var panel = $('#sandboxPanel');
      if (!panel || panel.classList.contains('hidden')) return;
      applyView();
      drawScene();
    });
    /* 面板显隐、侧栏折叠都会改画布大小；有 ResizeObserver 就让它盯着（老内核没有就用上面的兜底） */
    if (window.ResizeObserver) {
      var host = $('#sandboxPanel .sandbox-canvas');
      if (host) {
        new window.ResizeObserver(function () {
          var panel = $('#sandboxPanel');
          if (!panel || panel.classList.contains('hidden')) return;
          applyView();
          drawScene();
        }).observe(host);
      }
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bind);
  } else {
    bind();
  }

  window.W7Sandbox = { state: STATE, view: VIEW, run: run, drawScene: drawScene,
                       scene: scene, applyScene: applyScene,
                       loadDemo: loadDemo, clearAll: clearAll, endTrack: endTrack,
                       renderFrame: renderFrame, toWorld: toWorld,
                       toScreen: toScreen, hitTest: hitTest, deleteSelected: deleteSelected,
                       renderEditor: renderEditor, batteryField: batteryField,
                       openPanel: openPanel, closePanel: closePanel, zoomAt: zoomAt,
                       resetView: resetView, applyView: applyView };
})();
