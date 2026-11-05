/*
 * 图像面板的公共零件（M8）：坐标范围输入、说明表、红点、上下分界线拖动。
 *
 * 电路画板和物理沙盒的图像区长得一样、行为也一样，所以只写这一份：
 *   W7ChartUI.readRanges('crChart')   -> {x_range, y_range}（留空 = 自动定标）
 *   W7ChartUI.clearRanges('crChart')  -> 点「自动」时清空
 *   W7ChartUI.paint('#crChart', data) -> 贴图 + 说明表 + 帮助文字
 *   W7ChartUI.moveCursor('#crChart', points, index, meta)
 *   W7ChartUI.bindGrip('#crGrip', 上面那块画布的父层选择器)
 */
(function () {
  function $(selector) { return document.querySelector(selector); }

  function valueOf(id) {
    var node = $('#' + id);
    return node ? (node.value || '').trim() : '';
  }

  function numberOrNull(text) {
    if (text === '' || text === null || text === undefined) return null;
    var value = parseFloat(text);
    return isNaN(value) ? null : value;
  }

  /* 手填的坐标范围：两个都填了才算（只填一个按自动，避免半截范围让人看不懂） */
  function readRanges(prefix) {
    var x1 = numberOrNull(valueOf(prefix + 'XMin'));
    var x2 = numberOrNull(valueOf(prefix + 'XMax'));
    var y1 = numberOrNull(valueOf(prefix + 'YMin'));
    var y2 = numberOrNull(valueOf(prefix + 'YMax'));
    return {
      x_range: (x1 === null || x2 === null) ? null : [x1, x2],
      y_range: (y1 === null || y2 === null) ? null : [y1, y2]
    };
  }

  function clearRanges(prefix) {
    ['XMin', 'XMax', 'YMin', 'YMax'].forEach(function (tail) {
      var node = $('#' + prefix + tail);
      if (node) node.value = '';
    });
  }

  function paint(boxSelector, data, ids) {
    var box = $(boxSelector);
    if (!box) return;
    box.innerHTML = data.figure;
    ids = ids || {};
    if (ids.help) {
      var help = $(ids.help);
      if (help) help.textContent = data.help || '';
    }
    if (ids.legend) {
      var legend = $(ids.legend);
      if (legend) {
        legend.innerHTML = (data.legend || []).map(function (row) {
          return '<div class="chart-legend-row"><b>' + row.symbol + '</b><span>' +
            row.meaning + (row.value === '' ? '' : (' = ' + row.value)) + '</span></div>';
        }).join('');
      }
    }
  }

  function message(boxSelector, text) {
    var box = $(boxSelector);
    if (box) box.innerHTML = '<div class="muted" style="padding:8px">' + (text || '') + '</div>';
  }

  /* 红点：播放/拖动时挪它，不重新请求服务端 */
  function moveCursor(boxSelector, points, index, meta) {
    if (!points || !meta || !points.length) return;
    var box = $(boxSelector);
    var svg = box && box.querySelector('svg');
    if (!svg) return;
    var shown = Math.max(0, Math.min(points.length - 1,
                                     (index === null || index === undefined) ? 0 : index));
    var hit = points[shown];
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

  /* 按住分界线拖动：改上面那块画布的高度，下面图像区自己占剩下的 */
  function bindGrip(gripSelector, mainSelector) {
    var grip = $(gripSelector);
    var main = $(mainSelector);
    if (!grip || !main) return;
    var dragging = false;
    var startY = 0;
    var startHeight = 0;
    grip.onmousedown = function (event) {
      dragging = true;
      startY = event.clientY;
      startHeight = main.getBoundingClientRect().height;
      grip.classList.add('active');
      event.preventDefault();
    };
    document.addEventListener('mousemove', function (event) {
      if (!dragging) return;
      var panel = main.parentNode;
      var room = (panel ? panel.getBoundingClientRect().height : 600) - 200;
      var height = Math.max(120, Math.min(room, startHeight + (event.clientY - startY)));
      main.style.flex = 'none';
      main.style.height = height + 'px';
    });
    document.addEventListener('mouseup', function () {
      if (!dragging) return;
      dragging = false;
      grip.classList.remove('active');
    });
  }

  /* 整块面板（物理沙盒 / 电路画板）的窗口大小：拖右边缘 / 下边缘 / 右下角。
     双击任何一个把手 = 还原全屏。面板本来是铺满屏幕的 fixed 层，第一次拖的时候
     才把它钉成"左上角 + 明确宽高"，里面的排版按新尺寸自己重算。 */
  var WINDOW_STYLE_KEYS = ['inset', 'left', 'top', 'right', 'bottom', 'width', 'height',
                           'padding', 'alignItems', 'justifyContent'];

  function bindWindow(panelSelector, handles) {
    var panel = $(panelSelector);
    if (!panel) return;

    function fit(rect, width, height) {
      panel.style.inset = 'auto';
      panel.style.right = 'auto';
      panel.style.bottom = 'auto';
      panel.style.left = rect.left + 'px';
      panel.style.top = rect.top + 'px';
      panel.style.width = width + 'px';
      panel.style.height = height + 'px';
      panel.style.padding = '6px';
      panel.style.alignItems = 'stretch';
      panel.style.justifyContent = 'stretch';
      if (window.dispatchEvent) window.dispatchEvent(new Event('resize'));
    }

    function restore() {
      WINDOW_STYLE_KEYS.forEach(function (key) { panel.style[key] = ''; });
      if (window.dispatchEvent) window.dispatchEvent(new Event('resize'));
    }

    (handles || []).forEach(function (pair) {
      var node = $(pair[0]);
      if (!node) return;
      var which = pair[1];
      node.onmousedown = function (event) {
        var rect = panel.getBoundingClientRect();
        var start = { x: event.clientX, y: event.clientY, w: rect.width, h: rect.height };
        function move(moveEvent) {
          var width = which === 's' ? start.w
            : Math.max(560, Math.min(window.innerWidth - 6,
                                     start.w + (moveEvent.clientX - start.x)));
          var height = which === 'e' ? start.h
            : Math.max(360, Math.min(window.innerHeight - 6,
                                     start.h + (moveEvent.clientY - start.y)));
          fit(rect, width, height);
        }
        function up() {
          document.removeEventListener('mousemove', move);
          document.removeEventListener('mouseup', up);
        }
        document.addEventListener('mousemove', move);
        document.addEventListener('mouseup', up);
        event.preventDefault();
      };
      node.ondblclick = restore;
    });
  }

  /* 图像存成 PNG（老师要贴到课件/试卷里）：SVG -> canvas -> 下载。
     2 倍分辨率存出来清楚一点；中文标注跟着页面字体走，不会变方框。 */
  function exportPng(boxSelector, fileBase) {
    var box = $(boxSelector);
    var svg = box && box.querySelector('svg');
    if (!svg) return Promise.reject(new Error('还没有图像可以存'));
    return new Promise(function (resolve, reject) {
      var clone = svg.cloneNode(true);
      var view = svg.viewBox && svg.viewBox.baseVal;
      var width = (view && view.width) || 660;
      var height = (view && view.height) || 380;
      clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
      clone.setAttribute('width', width);
      clone.setAttribute('height', height);
      var blob = new Blob([new XMLSerializer().serializeToString(clone)],
                          { type: 'image/svg+xml;charset=utf-8' });
      var url = URL.createObjectURL(blob);
      var image = new Image();
      image.onload = function () {
        try {
          var scale = 2;
          var canvas = document.createElement('canvas');
          canvas.width = width * scale;
          canvas.height = height * scale;
          var context = canvas.getContext('2d');
          context.fillStyle = '#ffffff';
          context.fillRect(0, 0, canvas.width, canvas.height);
          context.setTransform(scale, 0, 0, scale, 0, 0);
          context.drawImage(image, 0, 0, width, height);
          var data = canvas.toDataURL('image/png');
          URL.revokeObjectURL(url);
          var link = document.createElement('a');
          link.href = data;
          link.download = (fileBase || '图像') + '.png';
          document.body.appendChild(link);
          link.click();
          document.body.removeChild(link);
          resolve({ width: canvas.width, height: canvas.height, bytes: data.length });
        } catch (error) {
          reject(error);
        }
      };
      image.onerror = function () {
        URL.revokeObjectURL(url);
        reject(new Error('图像导出失败'));
      };
      image.src = url;
    });
  }

  window.W7ChartUI = { readRanges: readRanges, clearRanges: clearRanges, paint: paint,
                       message: message, moveCursor: moveCursor, bindGrip: bindGrip,
                       bindWindow: bindWindow, exportPng: exportPng };
})();
