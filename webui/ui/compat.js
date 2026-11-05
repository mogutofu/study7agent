/*!
 * compat.js —— 浏览器内核识别与能力自检
 *
 * 目的：360 / QQ / 搜狗这类双核浏览器有“极速模式（Blink）”和“兼容模式（IE 内核）”，
 * 兼容模式没有 fetch / ReadableStream，流式对话必然失败。这里把失败原因直接说清楚，
 * 而不是让使用者看到一句“网络错误”。
 */
(function (global) {
  'use strict';

  var REQUIRED = [
    ['fetch', 'fetch API'],
    ['Promise', 'Promise'],
    ['JSON', 'JSON'],
    ['TextDecoder', 'TextDecoder'],
    ['AbortController', 'AbortController']
  ];

  /* 从 UA 里识别内核与版本 */
  function detect(userAgent) {
    var ua = String(userAgent || '');
    var is360 = /360SE|360EE|QIHU\s*360|360Browser|360安全浏览器|360极速浏览器/i.test(ua);
    var isQQ = /QQBrowser|TencentTraveler/i.test(ua);
    var isSogou = /MetaSr|SogouMobileBrowser|Sogou/i.test(ua);
    var trident = /Trident\/|MSIE\s\d/i.test(ua);
    var chrome = /(?:Chrome|Chromium|CriOS)\/(\d+)/.exec(ua);
    var firefox = /Firefox\/(\d+)/.exec(ua);
    var major = chrome ? parseInt(chrome[1], 10) : (firefox ? parseInt(firefox[1], 10) : 0);
    var engine = 'unknown';
    if (trident) engine = 'trident';
    else if (chrome) engine = 'blink';
    else if (firefox) engine = 'gecko';

    var name = '未知浏览器';
    if (is360) name = '360 浏览器';
    else if (isQQ) name = 'QQ 浏览器';
    else if (isSogou) name = '搜狗浏览器';
    else if (/Edg\//.test(ua)) name = 'Edge';
    else if (firefox) name = 'Firefox';
    else if (chrome) name = 'Chromium 内核浏览器';

    return { engine: engine, major: major, name: name, is360: is360, trident: trident };
  }

  /* 检查运行环境；win 便于测试时注入假对象 */
  function check(win) {
    win = win || global;
    var navigator_ = win.navigator || {};
    var info = detect(navigator_.userAgent);
    var missing = [];
    for (var index = 0; index < REQUIRED.length; index++) {
      if (!win[REQUIRED[index][0]]) missing.push(REQUIRED[index][1]);
    }
    var streaming = !!(win.ReadableStream && win.TextDecoder);

    var result = {
      info: info,
      missing: missing,
      streaming: streaming,
      fatal: false,
      message: '',
      label: info.name + (info.major ? ' · Chromium ' + info.major : '')
    };

    if (info.trident) {
      result.fatal = true;
      result.message = '当前页面运行在 IE 内核下（双核浏览器的“兼容模式”），无法进行流式对话。' +
        '请点击地址栏右侧的内核切换按钮，把本站改成“极速模式”后刷新页面。';
      return result;
    }
    if (missing.length) {
      result.fatal = true;
      result.message = '当前浏览器内核过于陈旧，缺少必要能力：' + missing.join('、') +
        '。请改用 Chrome 109 / Firefox 115 ESR，或在双核浏览器中切换到极速模式。';
      return result;
    }
    if (!streaming) {
      result.message = '当前内核不支持流式响应，回复会一次性显示。建议升级到较新的浏览器内核。';
    } else if (info.engine === 'blink' && info.major && info.major < 84) {
      result.message = '当前内核较旧（Chromium ' + info.major +
        '），功能可用，但界面元素间距会偏紧。建议升级浏览器。';
    }
    return result;
  }

  global.W7Compat = { detect: detect, check: check };
})(typeof window === 'undefined' ? globalThis : window);
