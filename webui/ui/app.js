/*!
 * app.js —— Study7Agent WebUI 前端逻辑
 * 依赖：md.js（Markdown 渲染）；原生 fetch + ReadableStream 消费 SSE。
 */
(function () {
  'use strict';

  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };

  var state = {
    config: null,
    skills: [],
    sessions: [],
    session: null,
    streaming: false,
    runId: null,
    stopForce: null,          // 停止看门狗：超时后强制停止
    stopGiveUp: null,
    migrationNoted: false,    // 配置迁移提示只弹一次
    run: { started: 0, rounds: 0, tools: [], prompt: 0, completion: 0, hit: 0, miss: 0, reasoning: 0, cost: 0 },
    current: null,
    controller: null,
    confirmMeta: null,
    lastUsage: null,
    images: [],
    activeSkills: [],
    scopeAll: false,
    subCards: {},
    subagents: {},        // 右侧「子代理」栏：id -> {role, task, status, steps, started, ...}
    subToolArgs: {},      // 子代理内部工具调用的参数（产物识别要用，结束时事件里没有参数）
    trace: '',
    floatHandle: null     // 悬浮工具栏的心跳定时器（见 startFloatWatch）
  };

  /* 悬浮工具栏（scripts\study7agent-float.ps1）通过服务端把命令排给网页，
     网页靠这个心跳报活并取命令。2 秒一次，打的是本机 127.0.0.1，开销可以忽略。 */
  var FLOAT_PING_MS = 2000;

  /* ------------------------------------------------------------- 基础工具 */
  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  /* 图标统一用内联 SVG：Win7 没有 emoji 字体，字符图标会变成空白 */
  function svgIcon(name, size) {
    return window.W7Icons ? W7Icons.markup(name, size || 15) : '';
  }

  function iconButton(className, iconName, size, title) {
    var button = el('button', className);
    button.innerHTML = svgIcon(iconName, size);
    if (title) button.title = title;
    return button;
  }

  function toast(message, kind) {
    var box = el('div', 'toast ' + (kind || ''), message);
    $('#toasts').appendChild(box);
    setTimeout(function () {
      box.style.opacity = '0';
      box.style.transition = 'opacity .3s';
      setTimeout(function () { box.remove(); }, 320);
    }, kind === 'error' ? 7000 : 3600);
  }

  window.W7Toast = toast;
  window.W7Copy = copyText;

  function fmtNumber(value) {
    var number = Number(value || 0);
    if (number >= 1000000) return (number / 1000000).toFixed(2) + 'M';
    if (number >= 1000) return (number / 1000).toFixed(1) + 'k';
    return String(number);
  }

  function fmtCost(value, currency) {
    var number = Number(value || 0);
    var symbol = (currency || 'USD') === 'CNY' ? '¥' : '$';
    if (number === 0) return symbol + '0';
    if (number < 0.0001) return symbol + number.toExponential(2);
    return symbol + number.toFixed(6).replace(/0+$/, '').replace(/\.$/, '');
  }

  function fmtTime(seconds) {
    if (seconds < 60) return seconds.toFixed(1) + 's';
    return Math.floor(seconds / 60) + 'm' + Math.round(seconds % 60) + 's';
  }

  function shortPath(path) {
    if (!path) return '';
    var text = String(path);
    return text.length > 62 ? '…' + text.slice(-60) : text;
  }

  /* 访问令牌：监听非本机地址（例如教室电脑 + 平板遥控）时接口需要带令牌。
     命令行里给的地址带 ?token=xxx，这里记住它并 attach 到每个请求上。 */
  var AUTH_TOKEN = (function () {
    try {
      var match = /[?&]token=([^&#]+)/.exec(window.location.search || '');
      if (match) {
        var value = decodeURIComponent(match[1]);
        localStorage.setItem('w7a.token', value);
        return value;
      }
      return localStorage.getItem('w7a.token') || '';
    } catch (error) {
      return '';
    }
  })();

  function imageUrl(path) {
    return '/api/image?path=' + encodeURIComponent(path) +
           (AUTH_TOKEN ? '&token=' + encodeURIComponent(AUTH_TOKEN) : '');
  }

  function fmtBytes(bytes) {
    var value = Number(bytes || 0);
    if (value >= 1048576) return (value / 1048576).toFixed(1) + 'MB';
    if (value >= 1024) return Math.round(value / 1024) + 'KB';
    return value + 'B';
  }

  function api(path, options) {
    options = options || {};
    if (options.body && typeof options.body !== 'string') {
      options.headers = Object.assign({ 'Content-Type': 'application/json' }, options.headers || {});
      options.body = JSON.stringify(options.body);
    }
    if (AUTH_TOKEN) {
      options.headers = Object.assign({ 'X-Auth-Token': AUTH_TOKEN }, options.headers || {});
    }
    return fetch(path, options).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (data) {
        if (!response.ok || data.ok === false) {
          throw new Error(data.error || ('请求失败：HTTP ' + response.status));
        }
        return data;
      });
    });
  }

  /* 供 panels.js / extras.js 复用（避免两份请求实现） */
  window.W7Api = {
    get: function (path) { return api(path); },
    post: function (path, body) { return api(path, { method: 'POST', body: body || {} }); },
    /* postRaw 的用武之地：有些接口 ok:false 也是"答案"（配不平、不反应），不能用 api()
       那套"ok:false 就抛异常"的约定。它自己拼 fetch，所以要拿到令牌。 */
    token: AUTH_TOKEN,
    /* 纯文本接口（悬浮工具栏那几条是 OK|… 的纯文本约定，不是 JSON） */
    text: function (path) {
      return fetch(path, AUTH_TOKEN ? { headers: { 'X-Auth-Token': AUTH_TOKEN } } : {})
        .then(function (response) { return response.text(); });
    },
    imageUrl: imageUrl
  };

  /* 供 complete.js（输入框补全）复用：给它的都是界面上已有的入口，
     点 /菜单 和点按钮走的是同一个函数，不另写一份逻辑。 */
  window.W7Chat = {
    addFileContext: addFileContext,          // 「文件」按钮加入上下文用的就是它
    newSession: newSession,
    stop: stop,
    openCalc: openCalc,
    openTimerDialog: openTimerDialog,
    toggleSessionSkill: toggleSessionSkill,  // 技能 chip 的点击也是它
    /* 技能列表 + 本会话是否已启用：跟 renderSkills() 用的是同一份 state */
    skillState: function () {
      var enabled = sessionSkillNames();
      return (state.skills || []).map(function (skill) {
        return { name: skill.name, description: skill.description || '',
                 on: enabled.indexOf(skill.name) >= 0 };
      });
    },
    /* 「清空」「导出」在顶栏是按钮，这里直接触发它，逻辑只有一份 */
    clickButton: function (selector) {
      var button = $(selector);
      if (button && button.onclick) button.onclick();
    }
  };

  /* ------------------------------------------------------------- 启动 */
  function boot() {
    var compat = window.W7Compat ? W7Compat.check(window) : { fatal: false, message: '', label: '' };
    if (compat.fatal) {
      showFatal({ message: compat.message }, '当前浏览器无法运行本页面',
        '按提示切换浏览器或内核后刷新页面即可。');
      return;
    }
    if (window.W7Icons) W7Icons.hydrate(document);
    var theme = localStorage.getItem('w7a.theme') || 'light';
    document.documentElement.setAttribute('data-theme', theme);
    applyTouch(localStorage.getItem('w7a.touch') === '1');
    // 默认列出所有工作区（当前那个展开、其余收着），点「筛选」可以只看本工作区
    state.scopeAll = localStorage.getItem('w7a.scopeAll') !== '0';
    state.timer = null;
    state.timerHandle = null;
    var last = localStorage.getItem('w7a.session') || '';
    var url = '/api/state' + (last ? '?session=' + encodeURIComponent(last) : '');
    api(url).then(function (data) {
      state.config = data.config;
      state.uiStamp = data.uiStamp || '';
      state.skills = data.skills || [];
      state.sessions = data.sessions || [];
      noteMigrations(data.config);
      applyThemeToConfig();
      renderHeader();
      renderSkills();
      renderSessions();
      if (data.session) {
        state.session = data.session;
        // 恢复到上次的对话时，工作区也要跟着回到它所属的目录，否则 AI 会把文件写到别处
        if (data.session.workspace) setWorkspaceOpen(data.session.workspace, true);
        return switchWorkspace(data.session.workspace, { quiet: true });
      } else if (state.sessions.length) {
        return openSession(state.sessions[0].id);
      } else {
        return api('/api/session/new', { method: 'POST', body: { model: state.config.model } }).then(function (res) {
          state.session = res.session;
          state.sessions.unshift({ id: res.session.id, title: res.session.title, updated: res.session.updated, messages: 0 });
        });
      }
    }).then(function () {
      renderAll();
      bindEvents();
      if (window.W7Panels) W7Panels.init();
      if (window.W7Icons) W7Icons.hydrate(document);
      var compat = window.W7Compat ? W7Compat.check(window) : { message: '' };
      if (compat.message) toast(compat.message, 'warn');
      startFloatWatch();
      refreshModels(true);
    }).catch(function (error) {
      showFatal(error);
    });
  }

  function showFatal(error, title, hint) {
    $('#overlayError').classList.remove('hidden');
    $('#overlayTitle').textContent = title || '无法连接本地服务';
    $('#overlayHint').textContent = hint || '请确认 webui\\server.py 仍在运行，然后刷新页面。';
    $('#overlayDetail').textContent = String(error && error.message ? error.message : error);
  }

  function applyThemeToConfig() {
    // 主题来自本地偏好，优先于服务端配置，避免每次刷新闪烁
    document.documentElement.setAttribute('data-theme', localStorage.getItem('w7a.theme') || 'light');
  }

  /* 触屏模式：教室一体机用手指点按，需要更大的字号与点击区域 */
  function applyTouch(enabled) {
    document.body.classList.toggle('touch', !!enabled);
    localStorage.setItem('w7a.touch', enabled ? '1' : '0');
    $('#btnTouch').classList.toggle('primary', !!enabled);
  }

  /* 推理等级：官方取 low/high/max（minimal/medium 会被映射），空值 = 不改动接口参数 */
  var EFFORT_LABELS = {
    '': '默认',
    minimal: '最省',
    low: '低',
    medium: '中',
    high: '高',
    max: '最高'
  };

  function effortLabel(level) {
    return EFFORT_LABELS[level] || level;
  }

  function effortHint(level) {
    var hints = {
      '': '不改动接口参数，沿用接口 / 模型自己的默认推理行为。',
      minimal: '几乎不展开推理，最快、最省 token，适合改写、翻译、格式转换这类明确的活。',
      low: '轻量推理，适合日常问答和简单整理。',
      medium: '中等推理，适合出题、写教案、分析数据这类需要想一想的活。',
      high: '最充分的推理，适合复杂的多步任务（做整套课件、跨文件核对）；更慢，思考 token 也更多。',
      max: '最高档（官方 max）：最慢最贵，留给最难的题或需要反复核对的活。'
    };
    return hints[level] || '';
  }

  function fillEffortSelect(select, current, fallbackLabel) {
    var levels = [''].concat((state.config && state.config.reasoningEfforts) ||
                             ['minimal', 'low', 'medium', 'high', 'max']);
    select.innerHTML = '';
    levels.forEach(function (level) {
      var text = effortLabel(level);
      if (!level) text = '默认' + (fallbackLabel ? ' · ' + fallbackLabel : '');
      var option = el('option', null, text);
      option.value = level;
      select.appendChild(option);
    });
    select.value = levels.indexOf(current) >= 0 ? current : '';
  }

  /* ------------------------------------------------------------- 渲染 */
  function renderHeader() {
    var config = state.config;
    var select = $('#modelSelect');
    select.innerHTML = '';
    var fallback = el('option', null, '默认 · ' + (config.model || '未配置'));
    fallback.value = '';
    select.appendChild(fallback);
    (config.profiles || []).forEach(function (name) {
      var profile = (config.models || {})[name];
      var option = el('option', null, name + (profile && profile.model ? ' · ' + profile.model : ''));
      option.value = name;
      select.appendChild(option);
    });
    select.value = config.profile || '';
    select.title = (config.profiles || []).length
      ? '在 agent.json 的 models 配置块之间切换'
      : '可在设置中增加 models 配置块以支持多模型切换';
    // 推理等级跟着会话走；会话没选就用设置里的默认值
    renderEffort();
    $('#endpointLabel').textContent = config.endpoint ? config.endpoint.replace(/^https?:\/\//, '') : '';
    var badge = $('#versionBadge');
    if (badge) badge.textContent = config.version ? 'v' + String(config.version).replace(/^v/, '') : 'v1';
    $('#workspacePicker').title = '当前工作区：' + (config.workspace || '') + '\n点击切换 / 新建（AI 新建的文件默认放在这里）';
    // 标题上一直显示当前工作区：列表里只列“有对话”的工作区，空的那个不占一行
    var sectionLabel = $('#wsSectionLabel');
    if (sectionLabel) {
      sectionLabel.textContent = '工作区 · ' + folderName(config.workspace);
      sectionLabel.title = '当前工作区：' + (config.workspace || '') + '\nAI 新建的文件默认放在这里';
    }
    renderBanner();
  }

  function renderBanner() {
    var existing = $('.banner');
    if (existing) existing.remove();
    if (state.config.hasApiKey) return;
    var banner = el('div', 'banner');
    banner.appendChild(el('span', null, '尚未配置 API Key，请在设置中填写 config/agent.json 的 api_key 后再开始对话。'));
    var button = el('button', 'btn', '打开设置');
    button.onclick = openSettings;
    banner.appendChild(button);
    $('#thread').appendChild(banner);
  }

  /* 本会话"生效中"的技能名。session.skills 是老师**手动选过**的显式列表；
     还是空列表就说明一次都没手动选过 —— 这时服务端会把全部技能都注入
     （core.py 的 _choose_skills 里这么写的）。界面必须按同一套语义显示，
     否则小标签全是灰的，看起来像"技能没默认启用"。 */
  function sessionSkillNames() {
    var chosen = (state.session && state.session.skills) || [];
    if (chosen.length) return chosen.slice();
    return (state.skills || []).map(function (skill) { return skill.name; });
  }

  function renderSkills() {
    var box = $('#skillChips');
    var enabled = sessionSkillNames();
    box.innerHTML = '';
    state.skills.forEach(function (skill) {
      var on = enabled.indexOf(skill.name) >= 0;
      var chip = el('span', 'chip' + (on ? ' active' : ''), skill.name);
      chip.title = (skill.description || '') + '（点击' + (on ? '取消' : '启用') + '，本会话生效）';
      chip.onclick = function () { toggleSessionSkill(skill.name, !on); };
      box.appendChild(chip);
    });
  }

  function reloadSkills() {
    return api('/api/skills').then(function (data) {
      state.skills = (data.skills || []).map(function (skill) {
        return { name: skill.name, description: skill.description };
      });
      renderSkills();
      return state.skills;
    }).catch(function () { return state.skills; });
  }

  /* chip 和技能面板的勾选框都走这里。第一次点击就是把"默认全开"这份隐式状态
     落成显式列表（全部减掉这一个），这样服务端 _choose_skills 和界面所见一致。 */
  function toggleSessionSkill(name, on) {
    if (!state.session) return Promise.resolve();
    var enabled = sessionSkillNames();
    var index = enabled.indexOf(name);
    if (on && index < 0) enabled.push(name);
    if (!on && index >= 0) enabled.splice(index, 1);
    if (!enabled.length) {
      // 空列表在服务端就是"默认全开"，表达不出"一个都不留"：留住这一个，并说清原因
      toast('至少要保留一个技能：全部停用等于恢复默认全开', 'warn');
      renderSkills();
      return Promise.resolve();
    }
    return api('/api/session/skills', { method: 'POST', body: { id: state.session.id, skills: enabled } })
      .then(function (res) {
        state.session = res.session;
        renderSkills();
        toast(on ? ('已启用技能：' + name) : ('已停用技能：' + name), 'ok');
      }).catch(function (error) { toast(error.message, 'error'); });
  }

  function folderName(path) {
    return String(path || '').split(/[\\/]/).filter(function (part) { return part; }).pop() || '工作区';
  }

  function isSamePath(left, right) {
    var clean = function (value) {
      var text = String(value || '').replace(/\\/g, '/').replace(/\/+/g, '/');
      text = text.replace(/\/+$/, '');
      return text.toLowerCase();
    };
    return clean(left) === clean(right);
  }

  /* 未命名时用文件夹名；同名不同目录时补一级父目录，避免看起来像“重复的工作区” */
  function workspaceLabel(entry, siblings) {
    var name = entry.title || folderName(entry.path);
    if (entry.title) return name;
    var ambiguous = (siblings || []).some(function (other) {
      return !isSamePath(other.path, entry.path) &&
             (other.title || folderName(other.path)) === name;
    });
    if (!ambiguous) return name;
    var parts = String(entry.path || '').split(/[\\/]/).filter(function (part) { return part; });
    var parent = parts.length > 1 ? parts[parts.length - 2] : '';
    return parent ? parent + '\\' + name : name;
  }

  /* 工作区列表 = 已登记的工作区（新建 / 添加 / 切过都会登记），加上“有对话但没登记的”放最后 */
  function workspaceList() {
    var entries = [];
    function add(path, title) {
      if (!path) return;
      for (var index = 0; index < entries.length; index++) {
        if (isSamePath(entries[index].path, path)) return;
      }
      entries.push({ path: path, title: title || '' });
    }
    ((state.config && state.config.workspaces) || []).forEach(function (item) {
      add(item.path, item.title);
    });
    state.sessions.forEach(function (item) { add(item.workspace, ''); });
    return entries;
  }

  function workspaceEntries() {
    // 有对话的照常显示；没对话的只有“登记过”的才占一行（刚新建的工作区要能看到才能建对话）
    var registered = (state.config && state.config.workspaces) || [];
    return workspaceList().filter(function (entry) {
      if (sessionCount(entry.path) > 0) return true;
      return registered.some(function (item) { return isSamePath(item.path, entry.path); });
    });
  }

  function sessionCount(path) {
    return state.sessions.filter(function (item) { return isSamePath(item.workspace, path); }).length;
  }

  /* 会话所在的工作区没登记过（例如工作区从列表里移除过）就归到「未分组」 */
  function ungroupedSessions(entries) {
    return state.sessions.filter(function (item) {
      return !entries.some(function (entry) { return isSamePath(entry.path, item.workspace); });
    });
  }

  /* 相对时间：DSH 那种“刚刚 / 9分钟 / 3小时 / 昨天 / 03-14”，比一串时间戳好认 */
  function relativeTime(text) {
    var parts = String(text || '').match(/(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/);
    if (!parts) return '';
    var when = new Date(Number(parts[1]), Number(parts[2]) - 1, Number(parts[3]),
                        Number(parts[4]), Number(parts[5]), 0);
    var seconds = (Date.now() - when.getTime()) / 1000;
    if (seconds < 0 || isNaN(seconds)) return '';
    if (seconds < 60) return '刚刚';
    if (seconds < 3600) return Math.floor(seconds / 60) + ' 分钟';
    if (seconds < 86400) return Math.floor(seconds / 3600) + ' 小时';
    if (seconds < 172800) return '昨天';
    if (seconds < 604800) return Math.floor(seconds / 86400) + ' 天';
    return parts[2] + '-' + parts[3];
  }

  function expandedWorkspaces() {
    try {
      return JSON.parse(localStorage.getItem('w7a.openWs') || '{}') || {};
    } catch (error) {
      return {};
    }
  }

  function setWorkspaceOpen(path, open) {
    var map = expandedWorkspaces();
    map[String(path).toLowerCase()] = !!open;
    localStorage.setItem('w7a.openWs', JSON.stringify(map));
  }

  function toggleWorkspace(path) {
    var open = !isWorkspaceOpen(path);
    setWorkspaceOpen(path, open);
    renderSessions();
    return open;
  }

  function isWorkspaceOpen(path) {
    var map = expandedWorkspaces();
    var key = String(path).toLowerCase();
    if (key in map) return !!map[key];
    // 默认：当前工作区展开，其他收着，一眼看到“我在哪、聊过什么”
    return isSamePath(path, (state.config && state.config.workspace) || '');
  }

  /* 选中一个工作区（和 DSH 一样：点侧栏里那一行就切过去） */
  function switchWorkspace(path, options) {
    var current = (state.config && state.config.workspace) || '';
    if (!path || isSamePath(path, current)) return Promise.resolve(false);
    return api('/api/workspace', { method: 'POST', body: { path: path } }).then(function (res) {
      if (res.config) state.config = res.config;
      renderHeader();
      renderSessions();
      updateTokenHint();
      if (!(options && options.quiet)) {
        toast('已切换到工作区：' + folderName(path), 'ok');
      }
      return true;
    }).catch(function (error) {
      toast(error.message, 'error');
      return false;
    });
  }

  /* 在指定的工作区里新建对话（先切过去，再建，AI 就会把文件写在那） */
  function newSessionIn(path) {
    if (state.streaming) {
      toast('正在生成，等这一轮结束再新建', 'warn');
      return;
    }
    $('#sidebar').classList.remove('open');
    return switchWorkspace(path, { quiet: true }).then(function () {
      return newSession();
    });
  }

  function sessionRow(item) {
    var row = el('div', 'session-item' + (state.session && state.session.id === item.id ? ' active' : ''));
    row.appendChild(el('div', 'session-title', item.title || '新对话'));
    row.appendChild(el('span', 'session-time', relativeTime(item.updated) || ''));
    var actions = el('div', 'session-actions');
    var rename = iconButton('btn icon ghost', 'pencil', 13, '重命名');
    rename.onclick = function (event) {
      event.stopPropagation();
      var title = window.prompt('会话名称', item.title || '');
      if (title == null) return;
      api('/api/session/rename', { method: 'POST', body: { id: item.id, title: title } }).then(function () {
        if (state.session && state.session.id === item.id) state.session.title = title;
        return reloadSessions();
      }).catch(function (error) { toast(error.message, 'error'); });
    };
    var remove = iconButton('btn icon ghost', 'trash', 13, '删除');
    remove.onclick = function (event) {
      event.stopPropagation();
      if (!window.confirm('删除会话“' + (item.title || '') + '”？此操作不可恢复。')) return;
      api('/api/session/delete', { method: 'POST', body: { id: item.id } }).then(function () {
        if (state.session && state.session.id === item.id) state.session = null;
        return reloadSessions();
      }).then(function () {
        if (!state.session) {
          if (state.sessions.length) return openSession(state.sessions[0].id);
          return newSession();
        }
      }).catch(function (error) { toast(error.message, 'error'); });
    };
    actions.appendChild(rename);
    actions.appendChild(remove);
    row.appendChild(actions);
    row.onclick = function () {
      $('#sidebar').classList.remove('open');
      openSession(item.id);
    };
    return row;
  }

  function workspaceGroup(entry, items, siblings) {
    var current = (state.config && state.config.workspace) || '';
    var here = isSamePath(entry.path, current);
    var open = isWorkspaceOpen(entry.path);
    var group = el('div', 'ws-group' + (open ? ' open' : '') + (here ? ' current' : ''));
    var row = el('div', 'ws-row');
    row.title = entry.path + (here ? '（当前工作区）' : '（点一下切到这个工作区，对话都在这一行下面）');
    var icon = el('span', 'ico-slot');
    icon.innerHTML = svgIcon('folder', 15);
    row.appendChild(icon);
    row.appendChild(el('span', 'ws-row-name', workspaceLabel(entry, siblings)));
    row.appendChild(el('span', 'ws-count', items.length ? String(items.length) : ''));
    var actions = el('div', 'ws-actions');
    var add = iconButton('btn icon ghost ws-add', 'plus', 13,
                         '在“' + workspaceLabel(entry, siblings) + '”中新建对话');
    add.onclick = function (event) {
      event.stopPropagation();
      newSessionIn(entry.path);
    };
    actions.appendChild(add);

    // 行右侧的「⋯」：重命名 / 打开文件夹 / 从列表移除（和 DSH 的工作区操作一致）
    var wrap = el('div', 'ws-menu-wrap');
    var more = iconButton('btn icon ghost ws-more', 'more', 13, '工作区操作');
    more.onclick = function (event) {
      event.stopPropagation();
      var menu = wrap.querySelector('.menu');
      var wasHidden = menu.classList.contains('hidden');
      closeWorkspaceMenus();
      menu.classList.toggle('hidden', !wasHidden);
    };
    wrap.appendChild(more);
    var menu = el('div', 'menu hidden');
    menu.appendChild(menuButton('pencil', '重命名工作区', function () { renameWorkspaceEntry(entry); }));
    menu.appendChild(menuButton('download', '在资源管理器中打开',
                                function () { openWorkspaceFolder(entry.path); }));
    menu.appendChild(menuButton('close', '从工作区列表移除',
                                function () { removeWorkspaceEntry(entry); }));
    wrap.appendChild(menu);
    actions.appendChild(wrap);
    row.appendChild(actions);
    var caret = el('span', 'ico-slot ws-caret');
    caret.innerHTML = svgIcon('chevron', 13);
    row.appendChild(caret);
    row.onclick = function () {
      if (here) {                       // 当前工作区：点一下收起/展开它的对话
        toggleWorkspace(entry.path);
        return;
      }
      setWorkspaceOpen(entry.path, true);   // 别的工作区：点一下切过去并展开
      switchWorkspace(entry.path);
    };
    group.appendChild(row);
    var list = el('div', 'ws-sessions');
    items.forEach(function (item) { list.appendChild(sessionRow(item)); });
    if (!items.length) {
      var empty = el('div', 'ws-empty', '还没有对话');
      var start = el('button', 'btn ghost small', '在这里新建');
      start.onclick = function (event) {
        event.stopPropagation();
        newSessionIn(entry.path);
      };
      empty.appendChild(start);
      list.appendChild(empty);
    }
    group.appendChild(list);
    return group;
  }

  function closeWorkspaceMenus() {
    var menus = document.querySelectorAll('#sessionList .ws-menu-wrap .menu');
    for (var index = 0; index < menus.length; index++) menus[index].classList.add('hidden');
  }

  function menuButton(icon, label, action) {
    var button = el('button');
    button.type = 'button';
    var slot = el('span', 'ico-slot');
    slot.innerHTML = svgIcon(icon, 14);
    button.appendChild(slot);
    button.appendChild(document.createTextNode(label));
    button.onclick = function (event) {
      event.stopPropagation();
      closeWorkspaceMenus();
      action();
    };
    return button;
  }

  function openWorkspaceFolder(path) {
    api('/api/open', { method: 'POST', body: { path: path } })
      .then(function () { toast('已打开文件夹：' + folderName(path), 'ok'); })
      .catch(function (error) { toast(error.message, 'error'); });
  }

  function renameWorkspaceEntry(entry) {
    var name = window.prompt('工作区名称（只改显示名，不动文件夹）', workspaceLabel(entry, []));
    if (name == null) return;
    api('/api/workspace/rename', { method: 'POST', body: { path: entry.path, title: name } })
      .then(function (res) {
        if (res.config) state.config = res.config;
        renderHeader();
        renderSessions();
        toast('已重命名工作区：' + (name.trim() || folderName(entry.path)), 'ok');
      }).catch(function (error) { toast(error.message, 'error'); });
  }

  function removeWorkspaceEntry(entry) {
    var name = workspaceLabel(entry, []);
    if (!window.confirm('将把“' + name + '”从工作区列表中移除。\n文件夹与会话记录都会保留，'
                        + '它的对话会显示在「未分组」下面。')) return;
    api('/api/workspace/remove', { method: 'POST', body: { path: entry.path } })
      .then(function (res) {
        if (res.config) state.config = res.config;
        renderHeader();
        renderSessions();
        toast('已从工作区列表移除：' + name, 'ok');
      }).catch(function (error) { toast(error.message, 'error'); });
  }

  function renderSessions() {
    var box = $('#sessionList');
    var filter = ($('#sessionSearch').value || '').trim().toLowerCase();
    var current = (state.config && state.config.workspace) || '';
    box.innerHTML = '';
    var others = sidebarOtherCount();
    var scopeButton = $('#btnScope');
    if (scopeButton) {
      scopeButton.classList.toggle('on', !!state.scopeAll);
      scopeButton.title = state.scopeAll ? '当前：显示全部工作区（点一下只看本工作区）'
                                        : '当前：只看本工作区（点一下显示全部工作区）';
    }

    // 搜索时跨工作区平铺结果，平时按工作区分组
    if (filter) {
      var hits = state.sessions.filter(function (item) {
        return (item.title || '').toLowerCase().indexOf(filter) >= 0 ||
               (item.preview || '').toLowerCase().indexOf(filter) >= 0;
      });
      box.appendChild(el('div', 'section-label', '搜索结果 ' + hits.length));
      hits.forEach(function (item) { box.appendChild(sessionRow(item)); });
      if (!hits.length) box.appendChild(el('div', 'muted', '没有匹配的对话'));
      return;
    }

    var entries = workspaceEntries();
    if (!state.scopeAll) {
      entries = entries.filter(function (entry) { return isSamePath(entry.path, current); });
    }
    var shown = 0;
    entries.forEach(function (entry) {
      var items = state.sessions.filter(function (item) {
        return isSamePath(item.workspace, entry.path);
      });
      box.appendChild(workspaceGroup(entry, items, entries));
      shown += 1;
    });
    // 会话所在的工作区不在列表里（被移除过）就和 DSH 一样放进「未分组」
    var loose = ungroupedSessions(entries);
    if (loose.length) {
      var looseBox = el('div', 'ws-group');
      var looseRow = el('div', 'ws-row');
      var looseIcon = el('span', 'ico-slot');
      looseIcon.innerHTML = svgIcon('folder', 15);
      looseRow.appendChild(looseIcon);
      looseRow.appendChild(el('span', 'ws-row-name', '未分组'));
      looseRow.appendChild(el('span', 'ws-count', String(loose.length)));
      looseRow.title = '这些对话的工作区已经不在列表里了；点开仍然能继续用';
      looseBox.appendChild(looseRow);
      var looseList = el('div', 'ws-sessions');
      loose.forEach(function (item) { looseList.appendChild(sessionRow(item)); });
      looseBox.appendChild(looseList);
      box.appendChild(looseBox);
      shown += 1;
    }
    if (!shown) {
      // 一个对话都没有：不摆一个空工作区，只给一行提示（当前工作区名在标题上）
      var hint = el('div', 'ws-hint');
      hint.appendChild(el('span', null, state.sessions.length
        ? '「' + folderName(current) + '」还没有对话'
        : '还没有对话'));
      var start = el('button', 'btn ghost small', '在这里新建');
      start.onclick = function (event) {
        event.stopPropagation();
        newSession();
      };
      hint.appendChild(start);
      box.appendChild(hint);
    }
    if (!state.scopeAll && others > 0) {
      var link = el('div', 'other-ws');
      link.appendChild(el('span', null, '其他工作区还有 ' + others + ' 个对话'));
      link.title = '点一下显示全部工作区';
      link.onclick = function () {
        state.scopeAll = true;
        localStorage.setItem('w7a.scopeAll', '1');
        renderSessions();
      };
      box.appendChild(link);
    }
  }

  function sidebarOtherCount() {
    var current = (state.config && state.config.workspace) || '';
    return state.sessions.filter(function (item) { return !isSamePath(item.workspace, current); }).length;
  }

  /* 工作区对话框：上面列已有的（点一行就切），下面新建
     （和文件选择器一个思路：能看见自己在哪、点一下就切换，不用记路径） */
  var WS_BAD_CHARS = /[\\/:*?"<>|]/g;
  var WS_HAS_BAD_CHARS = /[\\/:*?"<>|]/;      // .test() 用不带 g 的：带 g 的 test 会记住 lastIndex
  var WS_RESERVED = ['CON', 'PRN', 'AUX', 'NUL', 'COM1', 'COM2', 'COM3', 'COM4', 'COM5', 'COM6',
                     'COM7', 'COM8', 'COM9', 'LPT1', 'LPT2', 'LPT3', 'LPT4', 'LPT5', 'LPT6',
                     'LPT7', 'LPT8', 'LPT9'];

  function sanitizeWorkspaceName(raw) {
    return String(raw || '').replace(WS_BAD_CHARS, '').replace(/[. ]+$/, '').trim();
  }

  function workspaceNameProblem(raw) {
    var name = String(raw || '').trim();
    if (!name) return '请先给工作区起个名字。';
    if (WS_HAS_BAD_CHARS.test(name)) {
      return '名称里不能有 \\ / : * ? " < > | 这些字符，已自动去掉。';
    }
    if (WS_RESERVED.indexOf(sanitizeWorkspaceName(name).toUpperCase()) >= 0) {
      return 'Windows 不允许用 ' + name + ' 当文件夹名（系统保留名），换一个吧。';
    }
    if (/[. ]$/.test(name)) return '名称不能以句点或空格结尾。';
    return '';
  }

  function openCreateWorkspace() {
    if (!state.config) return;
    var parent = $('#wsParent');
    if (!parent.value) parent.value = state.config.workspaceParent || '';
    if (!parent.value) {
      var parts = String(state.config.workspace || '').split(/[\\/]/);
      parts.pop();
      parent.value = parts.join('\\');
    }
    $('#wsName').value = '';
    $('#wsCreateNote').style.display = 'none';
    renderWorkspaceList();
    renderWorkspacePreview();
    var dialog = $('#wsCreateDialog');
    if (!dialog.open) dialog.showModal();
    $('#wsName').focus();
  }

  function renderWorkspaceList() {
    var box = $('#wsExistingList');
    if (!box) return;
    box.innerHTML = '';
    var entries = workspaceList();
    var current = (state.config && state.config.workspace) || '';
    if (!entries.length) {
      box.appendChild(el('div', 'muted', '还没有登记工作区，用下面的「新建」建一个。'));
      return;
    }
    entries.forEach(function (entry) {
      var here = isSamePath(entry.path, current);
      var count = state.sessions.filter(function (item) {
        return isSamePath(item.workspace, entry.path);
      }).length;
      var row = el('div', 'ws-pick-row' + (here ? ' current' : ''));
      var icon = el('span', 'ico-slot');
      icon.innerHTML = svgIcon('folder', 15);
      row.appendChild(icon);
      var text = el('div', 'ws-pick-text');
      text.appendChild(el('div', 'ws-pick-name', workspaceLabel(entry, entries)));
      text.appendChild(el('div', 'ws-pick-path', entry.path));
      row.appendChild(text);
      row.appendChild(el('span', 'ws-pick-meta', count ? count + ' 个对话' : '还没有对话'));
      if (here) row.appendChild(el('span', 'badge ok', '当前'));
      row.title = here ? '当前工作区' : '点一下切换到：' + entry.path;
      row.onclick = function () { pickWorkspaceFromDialog(entry, here); };
      var open = iconButton('btn icon ghost', 'download', 13, '在资源管理器中打开');
      open.onclick = function (event) {
        event.stopPropagation();
        openWorkspaceFolder(entry.path);
      };
      row.appendChild(open);
      box.appendChild(row);
    });
  }

  function pickWorkspaceFromDialog(entry, here) {
    if (here) {
      toast('已经是当前工作区：' + workspaceLabel(entry, []), 'ok');
      return;
    }
    switchWorkspace(entry.path).then(function (changed) {
      if (!changed) return;
      setWorkspaceOpen(entry.path, true);
      state.scopeAll = true;
      localStorage.setItem('w7a.scopeAll', '1');
      renderSessions();
      renderWorkspaceList();
      renderWorkspacePreview();
    });
  }

  /* 新建前的预览：告诉老师到底会在哪里建出什么文件夹，重名/非法名也在这里说清楚 */
  function renderWorkspacePreview() {
    var box = $('#wsPreview');
    var button = $('#wsCreateGo');
    if (!box || !button) return;
    var raw = ($('#wsName').value || '').trim();
    var name = sanitizeWorkspaceName(raw);
    var parent = ($('#wsParent').value || '').replace(/[\\/]+$/, '');
    var problem = workspaceNameProblem(raw);
    button.disabled = !!problem;
    if (!name) {
      box.className = 'ws-preview';
      box.textContent = '上面填个名字，这里会显示最终建出来的文件夹。';
      return;
    }
    var target = parent ? parent + '\\' + name : name;
    var registered = (state.config.workspaces || []).filter(function (item) {
      return isSamePath(item.path, target);
    }).length > 0;
    var sameName = (state.config.workspaces || []).filter(function (item) {
      return !isSamePath(item.path, target) && folderName(item.path) === name;
    })[0];
    if (problem) {
      box.className = 'ws-preview warn';
      box.textContent = problem + '（将使用：' + name + '）';
      return;
    }
    box.className = 'ws-preview';
    if (registered) {
      box.textContent = '这个文件夹已经是工作区，会直接切过去用它：' + target;
    } else if (sameName) {
      box.textContent = '将创建文件夹：' + target + '（注意：另有一个同名工作区 ' + sameName.path + '）';
    } else {
      box.textContent = '将创建文件夹（已存在就直接用）：' + target;
    }
  }

  function createWorkspace(event) {
    var note = $('#wsCreateNote');
    var raw = $('#wsName').value || '';
    var problem = workspaceNameProblem(raw);
    if (problem) {
      note.style.display = '';
      note.className = 'notice error';
      note.textContent = problem;
      $('#wsName').focus();
      return;
    }
    var name = sanitizeWorkspaceName(raw);
    var button = $('#wsCreateGo');
    button.disabled = true;
    api('/api/workspace/create', { method: 'POST', body: { name: name, parent: $('#wsParent').value } })
      .then(function (res) {
        if (res.config) state.config = res.config;
        state.scopeAll = true;
        localStorage.setItem('w7a.scopeAll', '1');
        setWorkspaceOpen(res.workspace, true);
        $('#wsCreateDialog').close();
        renderHeader();
        renderSessions();
        renderWorkspaceList();          // 对话框已经关了，但列表保持最新，再打开时不会看到旧的
        updateTokenHint();
        toast('已新建工作区：' + name, 'ok');
        return newSession();
      }).catch(function (error) {
        note.style.display = '';
        note.className = 'notice error';
        note.textContent = error.message;
      }).then(function () { button.disabled = false; });
  }

  /* 把一个已有文件夹加进列表（不新建文件夹），并切过去 */
  function addExistingWorkspace() {
    W7Panels.openPicker({
      title: '选择一个文件夹作为工作区',
      mode: 'dir',
      start: $('#wsParent').value || state.config.workspace,
      filters: []
    }).then(function (result) {
      if (!result || !result.paths.length) return;
      var path = result.paths[0];
      return api('/api/workspace/add', { method: 'POST', body: { path: path } }).then(function (res) {
        if (res.config) state.config = res.config;
        return switchWorkspace(path, { quiet: true });
      }).then(function () {
        state.scopeAll = true;
        localStorage.setItem('w7a.scopeAll', '1');
        setWorkspaceOpen(path, true);
        $('#wsCreateDialog').close();
        renderHeader();
        renderSessions();
        renderWorkspaceList();
        toast('已添加工作区：' + folderName(path), 'ok');
      });
    }).catch(function (error) { toast(error.message, 'error'); });
  }
  function renderAll() {
    renderEffort();
    renderThread();
    renderContext();
    renderSkills();
    renderInspector();
    renderSessions();
    updateTokenHint();
  }

  function renderThread() {
    var thread = $('#thread');
    if (!thread) return;
    if (!state.session || !state.session.messages.length) {
      historyTurns = null;
      thread.innerHTML = '';
      renderBanner();
      thread.appendChild(emptyState());
      return;
    }
    var messages = state.session.messages;
    var toolsById = {};
    messages.forEach(function (message) {
      if (message.role === 'tool') toolsById[message.tool_call_id] = message;
    });
    // 把「一次提问 + 之后连续的思考/工具/回答」归成一轮，折叠栏按轮合并。
    // 系统自动追加的消息（自动续写、注入的图片）属于轮内过程，不另起气泡、也不断开这一轮。
    var turns = [];
    messages.forEach(function (message) {
      var internal = message.role === 'user' && (message.autoContinue || message.injected);
      if (message.role === 'user' && !internal) {
        turns.push({ user: message, parts: [] });
        return;
      }
      if (message.role !== 'assistant' && message.role !== 'tool' && !internal) return;
      if (!turns.length) turns.push({ user: null, parts: [] });
      turns[turns.length - 1].parts.push(message);
    });
    var unfinished = !!(state.session && state.session.incomplete);
    /* 换了会话：上一轮的展开状态不能跟着串门 */
    if (state.session.id !== turnOpenSession) {
      turnOpenSession = state.session.id;
      turnOpen = {};
    }
    historyTurns = turns;
    historyToolsById = toolsById;
    historyUnfinished = unfinished;
    /* 超长会话只画最近 HISTORY_SHOW 轮：更早的那批**根本不建节点**（见 paintHistoryWindow） */
    historyShownFrom = turns.length > HISTORY_SHOW + 10
      ? turns.length - HISTORY_SHOW : 0;
    wireHistoryAutoReveal();
    paintHistoryWindow(true);
  }

  /* 一轮 = 一次提问 + 后面的思考/工具/回答（不含顶部提示条）。
     重画历史窗口时按轮建节点：窗口外的那些连节点都没有。 */
  function renderTurnInto(thread, turn, index) {
    var turns = historyTurns;
    var unfinished = historyUnfinished;
    if (turn.user) thread.appendChild(userNode(turn.user));
    if (turn.parts.length) {
      thread.appendChild(assistantTurnNode(turn.parts, historyToolsById, index,
                                           unfinished && index === turns.length - 1));
    }
  }

  /* -------------------------------------------------------------- 时间线
   * 一条回答里发生的事情**按发生顺序**往下排：
   *     思考一行 -> 正文一段 -> 工具卡片 -> 思考一行 -> 正文一段 -> …
   * 一轮想完、说了话、调完工具，下一轮再思考时**另起一行出现在下面**，
   * 而不是永远堆在最上面那个折叠栏里；正文也不再是一条到底的流水账，
   * 所以越聊越长的时候，最新的思考和工具调用照样在眼前（它们就在最下面）。
   * 最后那次输出自然就是最末一段——也就是老师要的"总的信息"。
   */
  function streamNode() {
    return el('div', 'stream');
  }

  function streamAppend(ctx, node) {
    if (!ctx || !ctx.stream) return node;
    ctx.stream.appendChild(node);
    return node;
  }

  /* 一段思考：恒定一行，点开才在下面渲染 Markdown 全文（照 DSH） */
  function streamThink(ctx, text) {
    var step = thinkStep();
    step.classList.add('live');
    /* 流式（text 为空）＝进行中：摘要取"最后一段已完成的首行"；
       历史回放（带全文）＝已经结束：摘要取整段第一行（跟 DSH 的 running 一样） */
    step.running = !text;
    streamAppend(ctx, step);
    if (text) thinkSet(step, text);
    return step;
  }

  /* 一段正文：单独一个 Markdown 块（下一段正文会是**新的**一块） */
  function streamText(ctx, text) {
    var block = el('div', 'content caret');
    block.raw = '';
    if (text) {
      block.innerHTML = MD.render(text);
      block.raw = text;
      block.classList.remove('caret');
    }
    streamAppend(ctx, block);
    return block;
  }

  /* 一段结束：之后再有思考/正文就另起节点（工具卡片插在当前位置，不受影响） */
  /* 把"正在流式"的标记全摘掉：.caret/.live 上挂的是无限 CSS 动画，
     留在已结束的消息上会让浏览器一直重绘（老师反馈：结束的消息还在闪蓝色）。 */
  var lastDeltaAt = 0;
  function markDelta() { lastDeltaAt = Date.now(); }
  function clearLiveMarks() {
    var nodes = document.querySelectorAll('.stream .caret, .stream .live');
    for (var i = 0; i < nodes.length; i += 1) {
      nodes[i].classList.remove('caret');
      nodes[i].classList.remove('live');
    }
  }
  /* 长对话性能：**没显示出来的历史根本不建节点**。
     这里踩过一个坑：以前是把前面几十条 display:none 藏起来——节点还挂在 DOM 里，
     布局/样式重算/绘制的成本一分没少，会话一长照样卡得打不动字（老师报的"上下文一长直接卡爆"）。
     现在只把最近 HISTORY_SHOW 轮建成节点，更早的躺在 state.session.messages 里，
     点"显示更早的"才真正建出来。折叠 = 不渲染（DSH 就是这么做的），而不是藏起来。 */
  var HISTORY_SHOW = 30;              /* 一次只让最近 30 条参与布局与绘制（视口附近就够了） */
  var historyTurns = null;            /* 当前会话按轮切好的数据：重画历史窗口时用 */
  var historyToolsById = null;
  var historyUnfinished = false;
  var historyShownFrom = 0;           /* 已经画出来的第一轮的序号；> 0 表示前面还有没画的 */
  var moreHistoryButton = null;
  var lastRevealAt = 0;

  /* 按历史窗口重画聊天区：从 historyShownFrom 这一轮开始建节点，前面只留一个按钮 */
  function paintHistoryWindow(scrollBottom) {
    var thread = $('#thread');
    if (!thread || !historyTurns) return;
    thread.innerHTML = '';
    renderBanner();
    moreHistoryButton = null;
    if (historyShownFrom > 0) {
      moreHistoryButton = el('button', 'btn lab-mini',
                             '显示更早的 ' + historyShownFrom + ' 条消息');
      moreHistoryButton.onclick = historyRevealMore;
      thread.appendChild(moreHistoryButton);
    }
    for (var index = historyShownFrom; index < historyTurns.length; index += 1) {
      renderTurnInto(thread, historyTurns[index], index);
    }
    if (scrollBottom) scrollToBottom(true);
  }

  /* 再放一批出来：把更早的 HISTORY_SHOW 轮**真正建出来**。
     流式过程中不碰：正在写的那一段还只在 DOM 里，重画会把它抹掉。 */
  function historyRevealMore() {
    if (state.streaming || historyShownFrom <= 0) return;
    lastRevealAt = Date.now();
    historyShownFrom = Math.max(0, historyShownFrom - HISTORY_SHOW);
    paintHistoryWindow(false);
    var box = $('#messages');
    if (box) box.scrollTop = 0;        /* 新放出来的这批在最上面，直接把视线带过去 */
  }

  /* 滚到最上面就自动再放一批（一次滚动只放一批，否则一滑到底就会连着建几百轮节点） */
  function wireHistoryAutoReveal() {
    var box = $('#messages');
    if (!box || box.historyWired) return;
    box.historyWired = true;
    box.addEventListener('scroll', function () {
      if (historyShownFrom <= 0 || box.scrollTop >= 240) return;
      if (Date.now() - lastRevealAt < 400) return;
      historyRevealMore();
    });
  }

  /* 一轮刚跑完：重画一遍，把这一轮的过程折成一行（照 DSH「已完成，用时 X ⌄」），
     顺手把窗口外的历史收掉（renderThread 里按 HISTORY_SHOW 重算 historyShownFrom）。
     必须等服务端的完整消息到手之后才调（调用点见 sendText 的收尾）：流式中那条回答只
     存在于 DOM 里，重画会把它抹掉。
     展开状态在 turnOpen 里（不在 DOM 上），所以重画不会把老师点开过的那几轮收回去。 */
  function settleTurnView() {
    if (state.streaming || !state.session) return;
    var box = $('#messages');
    var keep = box ? box.scrollTop : 0;
    /* 老师正翻在上面看东西时别把他拽回底部（贴底时照旧跟到最新） */
    var away = box ? (box.scrollHeight - box.scrollTop - box.clientHeight > 120) : false;
    renderThread();
    if (box && away) box.scrollTop = keep;
  }

  /* 空闲就把整页动画冻住（见 style.css 的 html.no-anim）：不生成内容时不该有任何重绘 */
  function setAnimationsPaused(paused) {
    var root = document.documentElement;
    if (!root) return;
    if (paused) root.classList.add('no-anim');
    else root.classList.remove('no-anim');
  }

  var liveCleared = false;
  setInterval(function () {
    var idle = !lastDeltaAt || Date.now() - lastDeltaAt > 3000;
    setAnimationsPaused(idle);
    if (!idle) { liveCleared = false; return; }     /* 正在跑：留着光标 */
    if (liveCleared) return;                        /* 已经清过，别每秒都扫 */
    liveCleared = true;
    clearLiveMarks();
  }, 1000);
  /* 历史消息里可能带着上次没摘干净的闪烁标记，加载后兜一次 */
  setTimeout(function () {
    clearLiveMarks();
    liveCleared = true;
  }, 1500);

  function streamBreak(ctx) {
    if (!ctx) return;
    thinkSettle(ctx.thinkStep);              // 这一段思考收尾：摘要改取第一行
    flushRender(ctx.textBlock);              // 这一段收尾：按 markdown 落定一次
    if (ctx.textBlock) ctx.textBlock.classList.remove('caret');
    ctx.thinkStep = null;
    ctx.textBlock = null;
     clearLiveMarks();
  }

  /* ------------------------------------------------------------ 思考单行（照 DSH）
   * 一段思考 = 恒定一行的「思考 · <摘要>」，做法跟 DSH 的 ReasoningRow 一样：
   *   · 折叠态只有这一行（nowrap + 省略号），高度恒定，不再一会儿三行一会儿八行
   *     把页面顶来顶去；也不再靠横向滚动条"跑马灯"（那一行自己会滚，看久了很晕）。
   *   · 摘要在**进行中**取「最后一段已完成内容的首行」（DSH 的
   *     latestCompletedParagraphFirstLine），**结束后**取整段思考的第一行（DSH 的
   *     firstLine），并且去掉 Markdown 的 ** 记号（DSH 同样处理）。
   *   · 点开才在下面另起一块 think-body 渲染 Markdown 全文；**收起时那一块整块删掉**
   *     （DSH 的 DisclosureRow 就是 `open && children`，折叠不留 DOM）。
   *
   * 关键：**每一段思考各自一个节点**，按发生顺序插进时间线里（见 streamThink）；
   * 流式刷新只写 `step.line.textContent` 这一处，不整段重写。
   */
  var THINK_SUMMARY_MAX = 400;      /* 摘要那一行最多多少字（DSH 交给 CSS，我们兜一层） */
  var THINK_RAW_LIMIT = 200000;     /* 思考原文最多留多少字（照旧；后端要求原样保留） */
  var THINK_BODY_MAX = 40000;       /* 展开时正文最多渲染多少字，再多就只给一句提示 */
  var THINK_BODY_MIN_MS = 250;      /* 流式重画正文的最小间隔 */

  /* DSH 的 firstLine：整段的第一行 */
  function dshFirstLine(text) {
    text = String(text || '');
    var cut = text.indexOf('\n');
    return cut < 0 ? text : text.slice(0, cut);
  }

  /* DSH 的 latestCompletedParagraphFirstLine：空行分段，取最后一段里"已经写完"的
     那一行的首行；最后一段的第一行还没写完（整段还没换行）就退回上一段。 */
  function dshLatestCompletedParagraphFirstLine(text) {
    text = String(text || '');
    var re = /\r?\n(?:[\t ]*\r?\n)+/g;
    var last = '';
    var start = 0;
    var match = re.exec(text);
    while (match) {
      var paragraph = text.slice(start, match.index);
      var newline = paragraph.indexOf('\n');
      if (newline >= 0) last = paragraph.slice(0, newline);
      start = match.index + match[0].length;
      match = re.exec(text);
    }
    if (text.slice(start).indexOf('\n') >= 0) last = dshFirstLine(text.slice(start));
    return last;
  }

  /* 这一行显示的摘要（DSH 的 summaryText + replaceAll('**', '')） */
  function thinkSummary(step) {
    var text = step.raw || '';
    var line = step.running ? dshLatestCompletedParagraphFirstLine(text) : dshFirstLine(text);
    if (!line) line = dshFirstLine(text);        /* 首行还没写完：别让这一行空着 */
    line = line.replace(/\*\*/g, '').replace(/^\s+|\s+$/g, '');
    if (line.length > THINK_SUMMARY_MAX) line = line.slice(0, THINK_SUMMARY_MAX - 1) + '…';
    return line;
  }

  function thinkStep() {
    var step = el('div', 'step think');
    /* 「思考 ·」是一个固定在最左边的小标签：省略号在右边，标签不跟着跑掉。
       灯泡图标也挂在这个标签里（内联 SVG，颜色跟着这一行的灰字）。 */
    var tag = el('span', 'think-tag');
    tag.innerHTML = svgIcon('bulb', TOOL_ICON_SIZE) + '思考 ·';
    step.appendChild(tag);
    var line = el('span', 'think-line', '');
    step.appendChild(line);
    step.raw = '';
    step.running = true;                          /* 进行中：摘要取"最后一段已完成的首行" */
    step.line = line;
    step.body = null;                             /* 展开的那块正文（折叠时是 null） */
    step.bodyAt = 0;
    step.title = '点一下展开完整思考';
    step.onclick = function () {
      step.classList.toggle('expanded');
      if (step.classList.contains('expanded')) {
        step.scrollTop = step.scrollHeight;
        thinkBodyPaint(step, true);
      } else {
        thinkBodyDrop(step);
      }
    };
    return step;
  }

  /* 展开的那块正文：MD.render 全文（DSH 用 MarkdownText 渲染），超长只渲染前面一截。
     折叠时这块节点**整块删掉**，不留 DOM。 */
  function thinkBodyPaint(step, force) {
    if (!step.classList.contains('expanded')) { thinkBodyDrop(step); return; }
    var now = Date.now();
    if (!force && step.body && now - step.bodyAt < THINK_BODY_MIN_MS) return;
    step.bodyAt = now;
    var text = step.raw || '';
    var dropped = 0;
    if (text.length > THINK_BODY_MAX) {
      dropped = text.length - THINK_BODY_MAX;
      text = text.slice(0, THINK_BODY_MAX);
    }
    if (!step.body) {
      step.body = el('div', 'think-body');
      step.appendChild(step.body);
    }
    step.body.innerHTML = MD.render(text) +
      (dropped ? '<p class="think-more">（已省略 ' + dropped + ' 字）</p>' : '');
  }

  function thinkBodyDrop(step) {
    if (step.body && step.body.parentNode) step.body.parentNode.removeChild(step.body);
    step.body = null;
  }

  function thinkTrim(step) {
    step.line.textContent = thinkSummary(step);
  }

  /* 下一帧执行：浏览器用 rAF；测试环境（Node）里没有 window，退回 setTimeout */
  function nextFrame(fn) {
    if (typeof window !== 'undefined' && window.requestAnimationFrame) {
      window.requestAnimationFrame(fn);
      return;
    }
    fn();    /* 没有 rAF 的环境（Node 测试沙箱）：直接同步执行，别把更新丢掉 */
  }

  /* 一段思考结束（一轮答完 / 开始调工具）：此后摘要改取整段的第一行 */
  function thinkSettle(step) {
    if (!step) return;
    step.running = false;
    step.classList.remove('live');
    thinkTrim(step);
  }

  function thinkAppend(step, text) {
    step.raw += text;
    if (step.raw.length > THINK_RAW_LIMIT) step.raw = step.raw.slice(-THINK_RAW_LIMIT / 2);
    thinkTrim(step);    /* 同步写：按帧合并会让测试沙箱里的更新永远不落地 */
    if (step.classList.contains('expanded')) thinkBodyPaint(step, false);
  }

  function thinkSet(step, text) {
    step.raw = text || '';
    thinkTrim(step);
    if (step.classList.contains('expanded')) thinkBodyPaint(step, true);
  }

  /* ------------------------------------------------- 一轮跑完：过程折成一行（照 DSH）
     DSH 的一轮跑完之后只剩一行「已完成，用时 4分47秒 ⌄」，下面是最终答复；
     点这一行才把过程（思考、工具调用、注入的截图、中间正文）展开。
     我们也这么折，而且**折叠 = 过程节点一个都不建**（老师明确要求"折叠后是彻底
     取消渲染"，所以不能像 DSH 那样用 hidden="until-found" 把 DOM 留着）。
     正因为节点都不在，展开状态必须存在这里（DOM 之外），重画历史窗口时才认得出来
     老师点开过哪一轮 —— DSH 也是把 turnProcesses 放在 store 里。 */
  var turnOpen = {};                  /* key = 会话 id + '#' + 轮序号 */
  var turnOpenSession = '';

  function turnKey(index) {
    return ((state.session && state.session.id) || '') + '#' + index;
  }

  /* 一轮的总用时：把这一轮每条 assistant 消息的秒数加起来（DSH 用 turn.start/end） */
  function turnSeconds(parts) {
    var total = 0;
    parts.forEach(function (message) {
      if (message.role === 'assistant') total += Number(message.seconds || 0);
    });
    return total;
  }

  /* 照 DSH 的 formatRunDuration：满 60 秒才出现"分"，满 3600 秒才出现"小时" */
  function turnDurationText(seconds) {
    var total = Math.max(0, Math.floor(Number(seconds) || 0));
    var text = '';
    var hours = Math.floor(total / 3600);
    if (hours > 0) text += hours + '小时';
    if (total >= 60) text += (Math.floor(total / 60) % 60) + '分';
    return text + (total % 60) + '秒';
  }

  /* 折起来的那一行：整行一个按钮，左边"已完成，用时 X"，右边一个朝下的箭头 */
  function turnProcessRow(open, seconds, onToggle) {
    var row = el('button', 'turn-process' + (open ? ' open' : ''));
    row.type = 'button';
    row.title = open ? '收起这一轮的思考与工具调用' : '展开这一轮的思考与工具调用';
    row.setAttribute('aria-expanded', open ? 'true' : 'false');
    var label = el('span', 'tp-label', seconds > 0 ? '已完成，用时 ' : '已完成');
    if (seconds > 0) label.appendChild(el('span', 'tp-num', turnDurationText(seconds)));
    row.appendChild(label);
    var chevron = el('span', 'tp-chevron');
    chevron.innerHTML = svgIcon('chevron', 13);
    row.appendChild(chevron);
    row.onclick = onToggle;
    return row;
  }

  /* 这一轮按发生顺序摊开：思考 / 正文 / 工具调用 / 注入的图片（系统塞进来的截图） */
  function turnTimeline(parts, toolsById) {
    var items = [];
    parts.forEach(function (message) {
      if (message.role === 'user' && message.injected && (message.images || []).length) {
        message.images.forEach(function (image) {
          items.push({ kind: 'image', path: image.path, name: image.name || '' });
        });
        return;
      }
      if (message.role !== 'assistant') return;
      var reasoning = (message.reasoning || '').trim();
      if (reasoning) items.push({ kind: 'think', text: reasoning });
      var text = (message.content || '').trim();
      if (text) items.push({ kind: 'text', text: text });
      (message.tool_calls || []).forEach(function (call) {
        items.push({ kind: 'tool', call: call, result: (toolsById || {})[call.id] || null });
      });
    });
    return items;
  }

  /* 系统注入的截图小卡：流式（onInjectedImage）与历史回放共用一套结构 */
  function injectedImageNode(path, name) {
    var chip = el('div', 'image-chip');
    var img = document.createElement('img');
    img.src = imageUrl(path);
    img.alt = name || '';
    chip.appendChild(img);
    var meta = el('div', 'i-meta');
    meta.appendChild(el('div', 'i-name', name || shortPath(path)));
    meta.appendChild(el('div', 'i-size', '已提供给模型'));
    chip.appendChild(meta);
    return chip;
  }

  function assistantTurnNode(parts, toolsById, index, unfinished) {
    var assistants = parts.filter(function (message) { return message.role === 'assistant'; });
    var last = assistants[assistants.length - 1] || {};
    var hasError = parts.some(function (message) { return message.error; });
    var wrap = el('div', 'msg assistant' + (hasError ? ' error' : ''));
    wrap.appendChild(avatarWhale());
    var bubble = el('div', 'bubble');
    bubble.appendChild(assistantHead(last, index));

    // 历史也按时间线排：每轮的「思考 -> 正文 -> 工具」依次往下，
    // 这样刷新/切会话后再看，结构跟当时跑的时候一样（而不是"所有思考在最上面、
    // 所有正文糊在最下面"）。一轮跑完之后，这一轮的过程折成一行（照 DSH）。
    var timeline = turnTimeline(parts, toolsById);
    var cut = -1;                        /* 最后一个"过程"节点（思考/工具/注入图片）在哪 */
    timeline.forEach(function (item, at) { if (item.kind !== 'text') cut = at; });
    /* 还在跑的这一轮不折：过程就平铺在下面，跟当时一样（DSH 也要等 turn 关闭才折） */
    var foldable = !unfinished && cut >= 0;
    var key = turnKey(index);
    var open = foldable && !!turnOpen[key];
    var stream = streamNode();
    if (foldable) {
      bubble.appendChild(turnProcessRow(open, turnSeconds(parts), function () {
        turnOpen[key] = !open;
        var fresh = assistantTurnNode(parts, toolsById, index, unfinished);
        if (wrap.parentNode) wrap.parentNode.replaceChild(fresh, wrap);
      }));
    }
    bubble.appendChild(stream);
    var historyCtx = { stream: stream, thinkStep: null, textBlock: null };
    timeline.forEach(function (item, at) {
      /* 折起来的这一段**一个节点都不建**（不是 display:none 藏起来） */
      if (foldable && !open && at <= cut) return;
      if (item.kind === 'think') { streamThink(historyCtx, item.text); return; }
      if (item.kind === 'tool') { stream.appendChild(toolNode(item.call, item.result, false)); return; }
      if (item.kind === 'image') { stream.appendChild(injectedImageNode(item.path, item.name)); return; }
      streamText(historyCtx, item.text);
    });
    if (hasError) {
      var failed = parts.filter(function (message) { return message.error; })[0] || {};
      bubble.appendChild(el('div', 'notice error', '调用失败：' + failed.error));
    }
    var withUsage = parts.filter(function (message) { return message.usage; }).pop();
    if (withUsage) bubble.appendChild(usageLine(withUsage));
    // 这一轮做出来的文件：刷新页面/切会话回来照样能看见、能点开
    var produced = turnArtifacts(parts, toolsById);
    if (produced.length) bubble.appendChild(artifactStrip(produced));
    // 这一轮是被打断的（刷新页面后也认得出来），照样给一键继续
    if (unfinished) bubble.appendChild(continueRow());
    wrap.appendChild(bubble);
    return wrap;
  }

  function emptyState() {
    var box = el('div', 'empty-state');
    box.appendChild(el('h1', null, 'Study7Agent'));
    box.appendChild(el('p', null, '直接说任务就行：做课件、出练习、统计成绩、批改作业、读内网页面…'));
    return box;
  }

  function userNode(message) {
    var wrap = el('div', 'msg user');
    wrap.appendChild(el('div', 'avatar', '我'));
    var bubble = el('div', 'bubble');
    var images = message.images || [];
    if (images.length) {
      var gallery = el('div', 'msg-images');
      images.forEach(function (item) {
        var path = typeof item === 'string' ? item : item.path;
        if (!path) return;
        var img = document.createElement('img');
        img.src = imageUrl(path);
        img.alt = (item && item.name) || shortPath(path);
        img.title = img.alt;
        gallery.appendChild(img);
      });
      bubble.appendChild(gallery);
    }
    var content = el('div', 'content');
    content.innerHTML = MD.render(message.content || '');
    bubble.appendChild(content);
    var head = el('div', 'msg-head');
    head.appendChild(el('span', null, message.ts || ''));
    bubble.appendChild(head);
    var actions = el('div', 'msg-actions');
    actions.appendChild(copyButton(message.content || ''));
    head.appendChild(actions);
    wrap.appendChild(bubble);
    return wrap;
  }

  function assistantHead(message, index) {
    var head = el('div', 'msg-head');
    head.appendChild(el('span', null, 'Study7Agent'));
    if (message.model) head.appendChild(el('span', 'muted', message.model));
    if (message.seconds) head.appendChild(el('span', 'muted', message.seconds + 's'));
    var actions = el('div', 'msg-actions');
    if (message.content) actions.appendChild(copyButton(message.content));
    if (index != null && !state.streaming) {
      var retry = el('button', null, '重新生成');
      retry.onclick = regenerate;
      actions.appendChild(retry);
    }
    head.appendChild(actions);
    return head;
  }

  function copyButton(text) {
    var button = el('button', null, '复制');
    button.onclick = function () {
      copyText(text).then(function () { toast('已复制', 'ok'); })
        .catch(function () { toast('复制失败，请手动选择文本', 'error'); });
    };
    return button;
  }

  function copyText(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text);
    }
    return new Promise(function (resolve, reject) {
      var area = document.createElement('textarea');
      area.value = text;
      area.style.position = 'fixed';
      area.style.opacity = '0';
      document.body.appendChild(area);
      area.select();
      var ok = false;
      try { ok = document.execCommand('copy'); } catch (error) { ok = false; }
      area.remove();
      ok ? resolve() : reject(new Error('copy failed'));
    });
  }

  /* -------------------------------------------------------- 工具调用：一行灰字
   * 照 DSH 的观感：一次调用就是**一行很小的灰字**——
   *     读取 · docs\roadmap-1.1.0.md
   *     编辑 · docs\roadmap-1.1.0.md  +14 -0
   *     工具调用 · job_kill · pwsh-1          （没有中文标签的工具退回原名）
   * 没有卡片、没有边框、没有图标块；工具输出默认**不显示**，点这一行才展开
   * （展开也朴素：纯文本 + 等宽字体）。
   *
   * 关键：一次调用只建**一个** DOM 节点。老实现是 卡片 + 头 + 图标 + 名字 + 参数 + 徽章 +
   * 产物行 + 输出块 + <pre>，十来个子节点；老师反馈"工具调用一多就严重卡顿"就是这么来的。
   * 结束时也**就地改文字**（见 updateToolRow），不重建节点。
   */
  var TOOL_LABELS = {
    run_command: '运行命令', read_file: '读取', write_file: '写入', edit_file: '编辑',
    list_dir: '列目录', view_image: '看图', read_url: '读取网页', web_search: '联网搜索',
    download: '下载', download_status: '下载进度', extract_media: '提取音视频',
    inspect_media: '看媒体', replace_media: '换素材', diagnose_pptx: '体检课件',
    snapshot: '看界面', screenshot: '截屏', display_inventory: '显示器',
    wait: '等待', wait_for: '等界面', click: '鼠标', type: '打字', scroll: '滚轮',
    move: '移动拖动', shortcut: '快捷键', app: '窗口程序', process: '进程',
    registry: '注册表', multi_select: '多选', multi_edit: '批量填写',
    clipboard: '剪贴板', draw_path: '画笔',
    pick_students: '抽学生', show_figure: '作图', plane: '画板', plane_batch: '画板批量',
    class_timer: '课堂计时', show_on_screen: '投屏', mistake_book: '错题本',
    score_stats: '成绩统计', run_subagent: '子代理', self_check: '自检',
    restart_service: '重启服务', send_email: '发邮件', notify_webhook: '发通知',
    send_qq_message: '发 QQ'
  };
  var TOOL_LINE_MAX = 108;          /* 这一行最长多少字符：超了就截断（一行永远是一行） */
  var TOOL_ICON_SIZE = 12;          /* 行首小图标的边长（12px：看得清，又不撑行高） */
  var TOOL_OUT_MAX = 4000;          /* 展开体里"输出"最多放多少字（照旧，超出给一句提示） */

  function oneLineText(text) {
    return String(text == null ? '' : text).replace(/\s+/g, ' ').replace(/^ | $/g, '');
  }

  /* 编辑类工具在行尾给个 +N -M（跟 DSH 一样，一眼看出改了多少行） */
  function toolDiffStat(meta) {
    var rows = (meta && meta.diff) || [];
    var add = 0;
    var cut = 0;
    for (var i = 0; i < rows.length; i += 1) {
      if (rows[i].kind === 'add') add += 1;
      else if (rows[i].kind === 'del') cut += 1;
    }
    return rows.length ? '+' + add + ' -' + cut : '';
  }

  /* 参数里最关键的那一个：编辑看**改了哪个文件**，其余交给已有的 summarizeArgs */
  function toolDetail(name, args) {
    args = args || {};
    if (name === 'edit_file' && args.path) return shortPath(String(args.path));
    var text = summarizeArgs(name, args);
    /* summarizeArgs 认不出来的工具会兜底成一坨 JSON；只有一个参数时只留那个值，
       这样读起来跟 DSH 一样（`工具调用 · job_kill · pwsh-1`） */
    if (text.charAt(0) === '{') {
      var keys = Object.keys(args);
      if (keys.length === 1) text = String(args[keys[0]]);
    }
    return text;
  }

  /* 这一行写什么。limit 传 0 表示不截断（悬停提示用完整的那份）。 */
  function toolRowText(name, args, status, meta, limit) {
    var max = limit == null ? TOOL_LINE_MAX : limit;
    var detail = oneLineText(toolDetail(name, args));
    var text = TOOL_LABELS[name] || ('工具调用 · ' + name);
    if (detail) text += ' · ' + detail;
    if (max > 0 && text.length > max) text = text.slice(0, max - 1) + '…';
    if (status && status !== 'ok' && status !== 'run') text += '（' + badgeText(status) + '）';
    var stat = toolDiffStat(meta);
    return stat ? text + '  ' + stat : text;
  }

  /* 成功灰、失败淡红（不做图标、不做徽章，颜色差别极小就够） */
  function toolRowClass(status) {
    if (status === 'error' || status === 'denied') return 'tool err';
    if (status === 'run' || status === 'pending') return 'tool run';
    return 'tool ok';
  }

  /* 入参格式化：DSH 展开出来的「输入」段就是格式化后的入参 */
  function toolArgsText(args) {
    if (!args || typeof args !== 'object') return '';
    if (!Object.keys(args).length) return '';
    try { return JSON.stringify(args, null, 2); } catch (err) { return ''; }
  }

  /* 「输入 / 输出」一段：左边标签、右边 pre-wrap 文本（DSH 的 ioSection） */
  function ioSection(label, text) {
    var box = el('div', 'io-section');
    box.appendChild(el('span', 'io-label', label));
    box.appendChild(el('span', 'io-text', text));
    return box;
  }

  /* 展开体（照 DSH 的 ToolRow）：有 diff 的编辑类先给 diff 块（DSH 是 DiffBlock），
     其余的给一张「输入 / 输出」小卡 —— 每段各自滚动、最高 150px，长输出不会把页面顶开。
     展开才建节点；收起时这些节点**全删掉**（DSH 的 DisclosureRow = `open && children`）。 */
  function renderToolOutput(row) {
    collapseToolOutput(row);                       // 先摘掉上一次的输出
    row.classList.add('expanded');
    if (row.diff && row.diff.length) row.appendChild(diffNode(row.diff));
    var input = row.argsText || '';
    var output = String(row.rawOut == null ? '' : row.rawOut);
    if (output.length > TOOL_OUT_MAX) {
      output = output.slice(0, TOOL_OUT_MAX) + '\n…（已省略 ' + (output.length - TOOL_OUT_MAX) + ' 字符）';
    }
    var card = el('div', 'tool-io');
    if (input) {
      card.appendChild(ioSection('输入', input));
      card.appendChild(el('div', 'io-divider'));
    }
    card.appendChild(ioSection('输出', output || '（无输出）'));
    row.appendChild(card);
  }

  /* 收起 = 只摘掉文字节点**后面**的输出节点：行首图标和那一行文字原地不动 */
  function collapseToolOutput(row) {
    row.classList.remove('expanded');
    while (row.lastChild && row.lastChild !== row.textNode) row.removeChild(row.lastChild);
  }

  function toggleToolOutput(row) {
    row.expanded = !row.expanded;
    if (row.expanded) renderToolOutput(row);
    else collapseToolOutput(row);
  }

  function expandToolRow(row) {
    if (row.expanded) return;
    row.expanded = true;
    renderToolOutput(row);
  }

  /* 只写这一个文字节点：图标（创建时挂上去的那个 svg）和节点身份都不动，
     所以流式/结束时只是改字，不会重排这一行，也不会新建节点。 */
  function toolRowPaint(row, name, args, status, meta) {
    row.className = toolRowClass(status);
    row.lineText = toolRowText(name, args, status, meta);
    row.textNode.nodeValue = row.lineText;
    row.title = toolRowText(name, args, status, meta, 0) + '（点一下展开）';
    row.argsText = toolArgsText(args);
    if (row.expanded) renderToolOutput(row);
  }

  function toolNode(call, result, streaming) {
    var name = (call.function && call.function.name) || call.name || 'tool';
    var args = toolArgsOf(call);
    var status = (result && result.status) || call.status || (streaming ? 'run' : 'ok');
    var row = el('div', 'tool');                 /* 一次调用 = 这一个节点 */
    if (call.id || result) row.setAttribute('data-call', call.id || result.tool_call_id);
    /* 行首小图标：内联 SVG（Win7 没有字体图标），颜色跟着这一行的灰字走 */
    row.innerHTML = svgIcon(iconFor(name, args), TOOL_ICON_SIZE);
    row.textNode = document.createTextNode('');
    row.appendChild(row.textNode);
    row.expanded = false;
    row.rawOut = result ? result.content : '等待执行…';
    row.diff = (result && result.meta && result.meta.diff) || null;
    toolRowPaint(row, name, args, status, result && result.meta);
    row.onclick = function () { toggleToolOutput(row); };
    return row;
  }

  /* 工具结束：**就地**改这一行的文字，不换节点（换节点会让整条消息重排） */
  function updateToolRow(row, name, args, status, result) {
    row.rawOut = (result && result.content) || '';
    row.diff = (result && result.meta && result.meta.diff) || null;
    toolRowPaint(row, name, args, status, result && result.meta);
  }

  function diffNode(rows) {
    var box = el('div');
    rows.forEach(function (row) {
      box.appendChild(el('div', 'diff-line ' + (row.kind || ''), row.text));
    });
    return box;
  }

  /* --------------------------------------------------------- 产物（做出来的文件）
   * 老师最想知道的不是"调了哪个工具"，而是"东西做出来放哪儿了、点一下能不能看"。
   * 以前工具卡片上只有一行路径文字，得自己复制到资源管理器里粘。
   * 这里把工具返回的**真实落盘路径**变成可点的文件块（参考本地 DSH 的产出文件卡片）：
   *   打开          用系统默认程序打开（POST /api/open）
   *   文件夹        打开所在目录（POST /api/open 传目录；Windows 上就是资源管理器）
   *   预览          图片走 /api/image、文本走 /api/file，直接在页面里看一眼
   * 卡片上只显示**文件名**，完整路径挂在悬停提示里（老师不用看一长串 C:\Users\…）。
   * 只有"确有结果且成功"的调用才给按钮——不能给一个点下去报错的死按钮。
   */
  var ARTIFACT_ARG_KEYS = ['path', 'file', 'target', 'save_as', 'new_file', 'out_dir', 'template'];
  var IMAGE_EXT = /\.(png|jpe?g|gif|webp|bmp)$/i;
  var TEXT_EXT = /\.(txt|md|markdown|csv|tsv|json|log|ini|cfg|conf|xml|html?|css|js|py|ps1|cmd|bat|yml|yaml|tex|srt|vtt)$/i;
  var ABS_PATH = /^[a-zA-Z]:[\\\/]/;      // 只认盘符开头的绝对路径（服务端 resolve 后回的都是这种）

  function artifactName(path) {
    var text = String(path || '').replace(/[\\\/]+$/, '');
    var cut = Math.max(text.lastIndexOf('\\'), text.lastIndexOf('/'));
    return cut >= 0 ? text.slice(cut + 1) : text;
  }

  function artifactParent(path) {
    var text = String(path || '').replace(/[\\\/]+$/, '');
    var cut = Math.max(text.lastIndexOf('\\'), text.lastIndexOf('/'));
    if (cut < 0) return text;
    var head = text.slice(0, cut);
    if (/^[a-zA-Z]:$/.test(head)) return head + '\\';     // 盘符根目录
    return head || text;
  }

  function artifactKindLabel(kind) {
    return ({ new: '新生成', edit: '已改过', read: '引用' })[kind] || '';
  }

  function canPreviewArtifact(item) {
    if (item.dir) return false;
    return IMAGE_EXT.test(item.path) || TEXT_EXT.test(item.path);
  }

  /* 这些工具落下来的文件是**给 AI 自己看的**，不进「本次产物」：
     截图、界面快照、看图、发 QQ 之后的确认截图。
     老师要的是"我让你做出来的东西"，而这些是过程里的眼睛——
     截一次屏就多一个缩略图，产物栏很快就成了一堵墙（老师反馈"太冗杂"）。
     要看这些图片，工具结果本身就在对话里。 */
  var ARTIFACT_SKIP_TOOLS = { screenshot: 1, snapshot: 1, view_image: 1,
                              send_qq_message: 1, app: 1 };

  /* 一次工具调用牵扯到的本机文件。meta 是服务端回的真实落盘结果（最可信），
     args 只在 meta 里没路径时兜底（例如只给了保存目录的下载）。 */
  function toolArtifacts(name, args, meta, status) {
    var out = [];
    var seen = {};
    args = args || {};
    meta = meta || {};
    if (ARTIFACT_SKIP_TOOLS[name]) return out;            // 自己看的截图之类，不摆出来
    function addFile(path, kind, bytes) {
      var value = String(path == null ? '' : path).trim().replace(/^"|"$/g, '');
      if (!value) return;
      var dir = /[\\\/]$/.test(value);
      value = value.replace(/[\\\/]+$/, '');
      if (!ABS_PATH.test(value)) return;
      var key = value.toLowerCase();
      if (seen[key]) return;
      seen[key] = true;
      out.push({ path: value, name: artifactName(value) || value,
                 kind: kind || 'read', bytes: Number(bytes || 0), dir: dir });
    }
    if (status && status !== 'ok') return out;            // 失败/被拒：不摆出点开就报错的文件
    if (name === 'write_file') {
      addFile(meta.path || args.path, meta.created === false ? 'edit' : 'new');
      addFile(meta.notes_path, 'new');                    // pptx 顺手生成的讲稿
    } else if (name === 'edit_file' || name === 'replace_media') {
      addFile(meta.path || args.path, 'edit');
    } else if (name === 'download') {
      (meta.paths || []).forEach(function (item) { addFile(item.path, 'new', item.bytes); });
      if (!(meta.paths || []).length) addFile(args.path, 'new');  // 只给保存目录时，至少给个文件夹入口
    } else if (name === 'extract_media') {
      addFile(meta.dir || args.out_dir, 'new');
    } else if (name === 'read_file' || name === 'inspect_media'
               || name === 'diagnose_pptx') {
      addFile(meta.path || args.path, 'read', meta.bytes);
    } else if (name === 'mistake_book' || name === 'score_stats') {
      addFile(meta.path, 'new');
    } else if (meta.path) {
      addFile(meta.path, 'read', meta.bytes);
    }
    // 兜底：以后新加的工具只要参数里带 path/file/target/save_as… 就自动有产物入口
    ARTIFACT_ARG_KEYS.forEach(function (key) { addFile(args[key], 'read'); });
    return out;
  }

  function artifactAction(item) {
    if (item.dir) {
      api('/api/open', { method: 'POST', body: { path: item.path } })
        .catch(function (error) { toast('打不开文件夹：' + error.message, 'error'); });
      return;
    }
    api('/api/open', { method: 'POST', body: { path: item.path } })
      .catch(function (error) { toast('打不开这个文件：' + error.message, 'error'); });
  }

  function artifactFolder(item) {
    var target = item.dir ? item.path : artifactParent(item.path);
    api('/api/open', { method: 'POST', body: { path: target } })
      .catch(function (error) { toast('打不开所在文件夹：' + error.message, 'error'); });
  }

  /* 文本走 /api/file（服务端最多回 200000 字节），图片走 /api/image。
     两者都是本来就有的接口，没有为产物新造任何服务端能力。 */
  function toggleArtifactPreview(host, item) {
    var old = host.querySelector('.af-preview');
    var same = old && old.getAttribute('data-path') === item.path;
    if (old && old.parentNode) old.parentNode.removeChild(old);
    if (same || !host) return;
    var box = el('div', 'af-preview');
    box.setAttribute('data-path', item.path);
    var head = el('div', 'af-preview-head');
    head.appendChild(el('span', 'af-preview-title', item.path));
    var close = iconButton('btn icon ghost', 'close', 12, '收起预览');
    close.onclick = function () {
      if (box.parentNode) box.parentNode.removeChild(box);
    };
    head.appendChild(close);
    box.appendChild(head);
    var body = el('div', 'af-preview-body');
    body.appendChild(el('div', 'muted', '正在读取…'));
    box.appendChild(body);
    host.appendChild(box);

    if (IMAGE_EXT.test(item.path)) {
      var img = document.createElement('img');
      img.className = 'af-image';
      img.alt = item.name;
      img.onerror = function () {
        body.innerHTML = '';
        body.appendChild(el('div', 'muted',
          '这张图显示不出来（可能超过 8MB，或者不是常见的 png/jpg/gif/webp/bmp）'));
      };
      img.src = imageUrl(item.path);
      body.innerHTML = '';
      body.appendChild(img);
      return;
    }
    api('/api/file?path=' + encodeURIComponent(item.path)).then(function (res) {
      body.innerHTML = '';
      var pre = el('pre', 'af-text', res.text || '（这个文件是空的）');
      body.appendChild(pre);
      if (res.bytes > 200000) {
        body.appendChild(el('div', 'muted', '文件比较长，这里只显示了开头 200000 字节。'));
      }
    }).catch(function (error) {
      body.innerHTML = '';
      body.appendChild(el('div', 'muted', '读不出内容：' + error.message));
    });
  }

  /* 产物上的动作：一行灰字右边的小链接（不是按钮块，所以连图标都不建） */
  function artifactLink(item, host, action, title, label) {
    var link = el('button', 'af-btn', label);
    link.type = 'button';
    link.title = title;
    link.onclick = function (event) {
      if (event && event.stopPropagation) event.stopPropagation();
      if (action === 'preview') toggleArtifactPreview(host, item);
      else if (action === 'folder') artifactFolder(item);
      else artifactAction(item);
    };
    return link;
  }

  function artifactChip(item, host) {
    var chip = el('span', 'af-chip' + (item.kind === 'read' ? ' ref' : ''));
    var name = el('span', 'af-name', item.name);
    name.title = item.path;                       // 完整路径只在悬停时给
    chip.appendChild(name);
    var tail = artifactKindLabel(item.kind) + (item.bytes ? ' · ' + fmtBytes(item.bytes) : '');
    if (tail) chip.appendChild(el('span', 'af-meta', tail));
    chip.appendChild(artifactLink(item, host, 'open',
      item.dir ? '打开这个文件夹' : '用系统默认程序打开', '打开'));
    chip.appendChild(artifactLink(item, host, 'folder',
      item.dir ? '在资源管理器里打开' : '打开所在文件夹', '文件夹'));
    if (canPreviewArtifact(item)) {
      chip.appendChild(artifactLink(item, host, 'preview', '在网页里直接看一眼', '预览'));
    }
    return chip;
  }

  /* 一轮的「本次产物」：只列**做出来的**文件（读过的、只是引用的不进这里），
     按做出来的先后排（最新的在最后），同一个文件只出现一次。 */
  function turnArtifacts(parts, toolsById) {
    var items = [];
    var seen = {};
    parts.forEach(function (message) {
      if (message.role !== 'assistant') return;
      (message.tool_calls || []).forEach(function (call) {
        var result = (toolsById || {})[call.id] || null;
        var name = (call.function && call.function.name) || call.name || '';
        toolArtifacts(name, toolArgsOf(call), result && result.meta,
                      result ? result.status : '').forEach(function (item) {
          if (item.kind === 'read') return;
          var key = item.path.toLowerCase();
          if (seen[key]) return;
          seen[key] = true;
          items.push(item);
        });
      });
    });
    return items;
  }

  /* 「本次产物」压成一行灰字 + 小链接（打开 / 文件夹 / 预览都还在，只是变成行内小链接）。
     这条**增量**追加：再做出文件就往同一行里补一个，不再整条拆掉重建
     （老写法每做出一个文件就把整条 O(n) 个节点重建一遍）。 */
  function artifactStrip(items) {
    var strip = el('div', 'af-strip');
    var head = el('div', 'af-strip-head');
    var ico = el('span', 'af-strip-ico');
    ico.innerHTML = svgIcon('folder', 12);
    head.appendChild(ico);
    head.appendChild(el('span', 'af-strip-title', '产物'));
    strip.countNode = el('span', 'af-strip-count', '');
    head.appendChild(strip.countNode);
    head.appendChild(el('span', 'af-strip-hint', '点小链接打开；图片和文本可以预览'));
    strip.appendChild(head);
    strip.body = el('div', 'af-strip-body');
    strip.appendChild(strip.body);
    strip.total = 0;
    artifactPush(strip, items || []);
    return strip;
  }

  function artifactPush(strip, items) {
    if (!strip || !items.length) return;
    strip.total += items.length;
    strip.countNode.textContent = strip.total + ' 个文件';
    items.forEach(function (item) { strip.body.appendChild(artifactChip(item, strip.body)); });
  }

  /* 流式过程中每做出一个文件就补进「本次产物」，不用等这一轮结束（老师能立刻点开看） */
  function addLiveArtifacts(ctx, list) {
    var fresh = [];
    list.forEach(function (item) {
      if (item.kind === 'read') return;
      var key = item.path.toLowerCase();
      if (ctx.artifactSeen[key]) return;
      ctx.artifactSeen[key] = true;
      ctx.artifacts.push(item);
      fresh.push(item);
    });
    if (!fresh.length || !ctx.bubble) return;
    if (!ctx.artifactStrip) {
      ctx.artifactStrip = artifactStrip([]);
      ctx.bubble.appendChild(ctx.artifactStrip);    // 挂在回答最下面：这一轮做出来的东西
    }
    artifactPush(ctx.artifactStrip, fresh);
    scrollToBottom(false);
  }

  function toolArgsOf(call) {
    var text = (call.function && call.function.arguments) || call.argsText || '';
    try {
      return typeof text === 'string' ? JSON.parse(text || '{}') : (text || {});
    } catch (error) {
      return { _raw: text };
    }
  }

  function iconFor(name, args) {
    if (name === 'write_file' || name === 'read_file') {
      var path = String((args && args.path) || '').toLowerCase();
      if (/\.pptx$/.test(path)) return 'slides';
      if (/\.xlsx$/.test(path)) return 'chart';
      if (/\.docx$/.test(path)) return 'file';
      return name === 'write_file' ? 'save' : 'file';
    }
    return ({
      run_command: 'terminal', read_file: 'file', write_file: 'save', edit_file: 'pencil',
      list_dir: 'folder', read_url: 'globe', web_search: 'search',
      pick_students: 'users', class_timer: 'timer', show_on_screen: 'display',
      show_figure: 'chart',
      mistake_book: 'book', score_stats: 'chart', run_subagent: 'agent',
      inspect_media: 'play', diagnose_pptx: 'slides', extract_media: 'download',
      replace_media: 'refresh',
      screenshot: 'image', display_inventory: 'display', app: 'panel',
      clipboard: 'file', shortcut: 'terminal', click: 'pencil',
      type: 'pencil', move: 'pencil', scroll: 'refresh',
      snapshot: 'search', multi_select: 'check', multi_edit: 'pencil',
      wait: 'timer', wait_for: 'timer', process: 'terminal', registry: 'file',
      draw_path: 'pencil', download: 'download', download_status: 'refresh',
      send_email: 'link', notify_webhook: 'link', send_qq_message: 'users'
    })[name] || 'terminal';
  }

  function badgeClass(status) {
    if (status === 'ok') return 'ok';
    if (status === 'error') return 'err';
    if (status === 'denied') return 'warn';
    return 'run';
  }

  function badgeText(status) {
    return ({ ok: '完成', error: '失败', denied: '已拒绝', run: '执行中', pending: '待确认' })[status] || status;
  }

  function summarizeArgs(name, args) {
    if (name === 'edit_file') return (args.find ? String(args.find).split('\n')[0].slice(0, 50) : '')
      + ' 改成 ' + (args.replace ? String(args.replace).split('\n')[0].slice(0, 30) : '（删除）');
    if (name === 'run_command') return String(args.command || '');
    if (name === 'send_email') return String(args.subject || '') + (args.to ? ' 发给 ' + args.to : '');
    if (name === 'notify_webhook') return String(args.text || '');
    if (name === 'send_qq_message') return (args.contact ? args.contact + '：' : '') + String(args.text || '');
    if (name === 'app') return String(args.mode || '') + ' ' + String(args.name || args.window || args.executable || '');
    if (name === 'snapshot') {
      var look = String(args.window || '整个桌面');
      if (args.find) look += ' 找「' + String(args.find) + '」';
      if (args.use_vision === false) look += '（不截画面）';
      return look;
    }
    if (name === 'screenshot') return args.display ? '第 ' + args.display + ' 块屏' : '整屏';
    if (name === 'click') {
      var aim = args.label != null && args.label !== '' ? ('编号 ' + args.label)
        : ('(' + args.loc + ')');
      return String(args.button || 'left') + (args.clicks > 1 ? ' ×' + args.clicks : '') + ' ' + aim;
    }
    if (name === 'type' || name === 'multi_edit') {
      var typed = String(args.text || '');
      return typed.length > 60 ? typed.slice(0, 60) + '…' : (typed || (String(args.label != null ? '编号 ' + args.label : '') || '批量填写'));
    }
    if (name === 'scroll') return String(args.type || 'vertical') + ' ' + String(args.direction || 'down')
      + (args.wheel_times > 1 ? ' ×' + args.wheel_times : '') + (args.label != null ? ' 编号 ' + args.label : '');
    if (name === 'move') {
      var spot = args.label != null ? ('编号 ' + args.label) : ('(' + args.loc + ')');
      return (args.drag ? '拖动到 ' + spot + (args.from_loc ? ' 从 (' + args.from_loc + ')' : '') : '移到 ' + spot);
    }
    if (name === 'multi_select') {
      var picks = args.labels || args.locs || [];
      return '选 ' + picks.length + ' 个' + (args.press_ctrl === false ? '（不多选）' : '');
    }
    if (name === 'wait') return String(args.seconds != null ? args.seconds : 1) + ' 秒';
    if (name === 'wait_for') return String(args.condition || 'window') + (args.text ? '：' + String(args.text) : '');
    if (name === 'process') return String(args.action || 'list') + (args.name ? ' ' + args.name : '')
      + (args.pid ? ' pid ' + args.pid : '');
    if (name === 'registry') return String(args.action || 'get') + ' ' + String(args.path || '');
    if (name === 'display_inventory') return '显示器清单';
    if (name === 'draw_path') {
      var strokes = args.strokes || [];
      var points = 0;
      strokes.forEach(function (stroke) { points += (stroke || []).length; });
      return strokes.length + ' 笔 / ' + points + ' 个点';
    }
    if (name === 'show_figure') {
      return (args.kind === 'geometry'
        ? ('几何图形 ' + ((args.shapes || []).length) + ' 条')
        : ('y = ' + String(args.expression || '')));
    }
    if (name === 'download') {
      var urls = (args.urls && args.urls.length ? args.urls : [args.url || '']);
      return urls.length + ' 个地址' + (args.background ? '（后台）' : '')
        + (args.max_mb ? '，最多 ' + args.max_mb + 'MB' : '');
    }
    if (name === 'download_status') return String(args.action || 'list')
      + (args.job_id ? ' ' + args.job_id : '');
    if (name === 'shortcut') return String(args.shortcut || '');
    if (name === 'clipboard') {
      var value = String(args.text || '');
      return value.length > 60 ? value.slice(0, 60) + '…' : (value || String(args.action || 'get'));
    }
    if (args.path) return shortPath(String(args.path));
    if (args.url) return String(args.url);
    var text = JSON.stringify(args);
    return text.length > 90 ? text.slice(0, 90) + '…' : text;
  }

  function usageLine(message) {
    var usage = message.usage || {};
    var line = el('div', 'usage-line');
    line.appendChild(el('span', null, '输入 ' + fmtNumber(usage.prompt_tokens)));
    if (usage.cache_hit_tokens) {
      var rate = Math.round(usage.cache_hit_tokens / Math.max(1, usage.prompt_tokens) * 100);
      line.appendChild(el('span', null, '缓存命中 ' + fmtNumber(usage.cache_hit_tokens) + '（' + rate + '%）'));
    }
    line.appendChild(el('span', null, '输出 ' + fmtNumber(usage.completion_tokens)));
    if (usage.reasoning_tokens) line.appendChild(el('span', null, '思考 ' + fmtNumber(usage.reasoning_tokens)));
    if (message.cost) {
      var price = (state.config.price && state.config.price.currency) || 'USD';
      line.appendChild(el('span', null, '费用 ' + fmtCost(message.cost, price)));
    }
    return line;
  }

  function renderContext() {
    var box = $('#contextBar');
    box.innerHTML = '';
    var context = (state.session && state.session.context) || [];
    context.forEach(function (item) {
      var chip = el('span', 'context-chip' + (item.kind === 'url' ? ' source' : ''));
      chip.appendChild(el('span', null, (item.kind === 'url' ? '页面：' : '文件：') + shortPath(item.source)));
      chip.title = (item.source || '') + '（' + fmtNumber(item.chars) + ' 字符）';
      var remove = iconButton(null, 'close', 12, '移出上下文');
      remove.onclick = function () {
        api('/api/context/remove', { method: 'POST', body: { id: state.session.id, index: item.index } })
          .then(function (res) {
            state.session = res.session;
            renderContext();
            renderInspector();
          }).catch(function (error) { toast(error.message, 'error'); });
      };
      chip.appendChild(remove);
      box.appendChild(chip);
    });
  }

  /* 右侧「子代理」栏：让老师一眼看到谁在干活、干到哪一步了 */
  function subagentRecord(id) {
    if (!state.subagents[id]) {
      state.subagents[id] = {
        id: id, role: '子代理', task: '', status: 'run',
        steps: 0, last: '', started: Date.now(), rounds: 0, seconds: null, summary: ''
      };
    }
    return state.subagents[id];
  }

  function resetSubagents() {
    state.subagents = {};
    state.subToolArgs = {};
    renderSubagents();
  }

  function subagentRows() {
    return Object.keys(state.subagents).map(function (id) { return state.subagents[id]; })
      .sort(function (a, b) { return (a.started || 0) - (b.started || 0); });
  }

  function renderSubagents() {
    var list = $('#subagentList');
    var summary = $('#subagentSummary');
    if (!list) return;
    var rows = subagentRows();
    if (summary) {
      var running = rows.filter(function (item) { return item.status === 'run'; }).length;
      summary.textContent = rows.length
        ? (running ? running + ' 个正在工作 · 共 ' + rows.length + ' 个' : '共 ' + rows.length + ' 个（已结束）')
        : '';
    }
    if (!rows.length) {
      list.innerHTML = '';
      list.appendChild(el('div', 'muted', '模型派子代理并行干活时，这里会列出它们各自在做什么。'));
      return;
    }
    list.innerHTML = '';
    rows.slice(-8).forEach(function (item) {
      var row = el('div', 'timeline-item sub-row');
      row.appendChild(el('span', 'dot ' + badgeClass(item.status)));
      row.appendChild(el('span', 't-name', item.role || '子代理'));
      var detail = item.status === 'run'
        ? (item.last ? item.last : '已派出，等待工具调用…')
        : (item.status === 'ok' ? '已完成' : badgeText(item.status));
      row.appendChild(el('span', 't-detail', detail));
      var meta = [];
      if (item.steps) meta.push(item.steps + ' 步');
      if (item.status === 'run') meta.push(fmtTime((Date.now() - item.started) / 1000));
      else if (item.seconds != null) meta.push(item.seconds + 's');
      row.appendChild(el('span', 't-time', meta.join(' · ')));
      if (item.task) row.title = item.task;
      list.appendChild(row);
      if (item.task) {
        var task = el('div', 'sub-task-line', item.task);
        task.title = item.task;
        list.appendChild(task);
      }
    });
  }

  /* 右侧「工具调用」列表：一行灰字、**增量**渲染。
     老写法每次事件都 innerHTML = '' 重建整张表 —— 一次运行 n 次调用就是 n² 个节点，
     工具一多这里是最大的卡顿源（老师反馈"工具调用一多就严重卡顿"）。
     现在一次调用只追加一行、结束时只改那一行的文字；没变的行一个字都不动。 */
  var timelineRows = [];

  function timelineText(item) {
    return item.name + (item.detail ? ' · ' + item.detail : '') + ' · '
      + (item.seconds != null ? item.seconds + 's'
                              : (item.status === 'run' ? '…' : badgeText(item.status)));
  }

  function renderTimeline() {
    var timeline = $('#timeline');
    var tools = state.run.tools;
    if (timelineRows.length > tools.length) {          /* 换会话/重开一轮：整表重建这一次就够 */
      timeline.innerHTML = '';
      timelineRows = [];
    }
    if (!tools.length) {
      if (!timeline.firstChild) {
        timeline.innerHTML = '<div class="muted" style="font-size:12px">暂无工具调用</div>';
      }
      return;
    }
    if (!timelineRows.length) timeline.innerHTML = '';  // 去掉"暂无工具调用"那句提示
    for (var i = timelineRows.length; i < tools.length; i += 1) {
      var row = el('div', 'tl-one');
      timeline.appendChild(row);
      timelineRows.push(row);
    }
    for (var j = 0; j < tools.length; j += 1) {
      var text = timelineText(tools[j]);
      if (timelineRows[j].painted === text) continue;   // 没变就不碰 DOM
      timelineRows[j].painted = text;
      timelineRows[j].className = 'tl-one ' + badgeClass(tools[j].status);
      timelineRows[j].textContent = text;
    }
  }

  function renderInspector() {
    var run = state.run;
    $('#runStatus').textContent = state.streaming ? '运行中' : '空闲';
    $('#runModel').textContent = state.config.model || '-';
    $('#runEffort').textContent = effortLabel(state.run.effort || currentEffort());
    $('#runMeta').textContent = run.rounds + ' / '
      + (run.started ? fmtTime((Date.now() - run.started) / 1000) : '0s');
    var usage = (state.session && state.session.usage) || {};
    var prompt = run.prompt || usage.prompt_tokens || 0;
    var output = run.completion || usage.completion_tokens || 0;
    var hit = run.hit || usage.cache_hit_tokens || 0;
    var rate = prompt > 0 ? Math.round(hit / prompt * 100) : 0;
    $('#tokenLine').textContent = fmtNumber(prompt) + ' 入 / ' + fmtNumber(output)
      + ' 出 · 缓存 ' + rate + '%';
    var currency = (state.config.price && state.config.price.currency) || 'USD';
    var cost = run.cost || usage.cost || 0;
    $('#costLine').textContent = cost ? fmtCost(cost, currency) : '-';

    renderTimeline();
    renderSubagents();
  }

  /* 思考链路：右侧只显示**要点**（服务端边流边提炼），完整思考在左边折叠栏里 */
  function traceReset() {
    state.tracePoints = 0;
    var box = $('#reasonTrace');
    box.innerHTML = '';
    box.appendChild(el('span', 'muted', '要点会随着模型思考实时出现在这里；完整思考在左边折叠栏。'));
  }

  function traceRound(round) {
    var box = $('#reasonTrace');
    if (!state.tracePoints) box.innerHTML = '';
    box.appendChild(el('div', 'trace-round', '第 ' + round + ' 轮'));
    traceScroll();
  }

  /* 接口抖动重试：这次已经写出来的内容会作废重来，别让老师看到重复的两段 */
  function onRetry(event) {
    var ctx = state.current;
    if (!ctx) return;
    if (!ctx.retryNote) {
      ctx.retryNote = el('div', 'notice warn');
      streamAppend(ctx, ctx.retryNote);
    }
    ctx.retryNote.textContent = '接口暂时失败（' + (event.code || '') + '），第 '
      + event.attempt + '/' + event.max + ' 次重试，' + event.delay + ' 秒后继续…';
    scrollToBottom(false);
  }

  function onRetryReset() {
    var ctx = state.current;
    if (!ctx) return;
    // 丢掉这一次的部分输出：请求会原样重发，内容不会重复。
    // 时间线里这两段（那次的思考和正文）直接撤掉，别留半截在那儿。
    ctx.raw.content = '';
    ctx.raw.reasoning = '';
    [ctx.thinkStep, ctx.textBlock].forEach(function (node) {
      if (node && node.parentNode) node.parentNode.removeChild(node);
    });
    ctx.thinkStep = null;
    ctx.textBlock = null;
    if (ctx.retryNote) ctx.retryNote.textContent = '上一次尝试的内容已作废，正在重新生成…';
    scrollToBottom(true);
  }

  function onCompacted(event) {
    var ctx = state.current;
    var node = el('div', 'notice');
    node.appendChild(el('strong', null, '已压缩上下文：'));
    node.appendChild(el('span', null, '把 ' + (event.removed || 0) + ' 条较早的对话总结成摘要，'
      + '保留最近 ' + (event.kept || 0) + ' 条（原始消息仍保存在会话文件里）。'));
    if (ctx) streamAppend(ctx, node);
    else $('#thread').appendChild(node);
    toast('上下文接近上限，已自动压缩较早的对话', 'ok');
  }

  /* 时间线只留最近 TRACE_KEEP 条：它只是进度提示，攒到几千条会把布局拖死 */
  var TRACE_KEEP = 60;

  function onTracePoint(event) {
    var box = $('#reasonTrace');
    if (!state.tracePoints) box.innerHTML = '';
    var point = el('div', 'trace-point', event.d || '');
    point.title = event.d || '';
    box.appendChild(point);
    while (box.childNodes.length > TRACE_KEEP) box.removeChild(box.firstChild);
    state.tracePoints += 1;
    traceScroll();
    var hint = $('#traceHint');
    if (hint) hint.textContent = '已提炼 ' + state.tracePoints + ' 条要点';
  }

  /* 时间线滚动也按帧合并：每段思考都滚一次同样是强制重排 */
  var tracePending = false;
  function traceScrollSoon() {
    if (tracePending) return;
    tracePending = true;
    var run = function () { tracePending = false; traceScroll(); };
    nextFrame(run);
  }

  function traceScroll() {
    var box = $('#reasonTrace');
    if (box.scrollHeight - box.scrollTop - box.clientHeight < 120) {
      box.scrollTop = box.scrollHeight;
    }
  }

  function updateTokenHint() {
    var input = $('#input');
    input.style.height = 'auto';
    /* 只在换行数变了才量高：每敲一个字就量会强制重排整个页面 */
    var lines = (input.value.match(/\n/g) || []).length;
    if (input.growLines === lines) return;
    input.growLines = lines;
    input.style.height = Math.min(220, input.scrollHeight) + 'px';
    var context = (state.session && state.session.context) || [];
    var chars = context.reduce(function (sum, item) { return sum + (item.chars || 0); }, 0);
    var parts = [];
    if (state.images.length) parts.push('图片 ' + state.images.length + ' 张');
    if (chars) parts.push('上下文 ' + fmtNumber(chars) + ' 字符');
    if (state.session) parts.push('历史 ' + (state.session.messages || []).length + ' 条');
    $('#tokenHint').textContent = parts.join(' · ');
  }

  function scrollToBottom(force) {
    var box = $('#messages');
    var nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 160;
    if (force || nearBottom) box.scrollTop = box.scrollHeight;
    updateScrollButton();
  }

  function updateScrollButton() {
    var box = $('#messages');
    var away = box.scrollHeight - box.scrollTop - box.clientHeight > 400;
    $('#scrollBottom').classList.toggle('hidden', !away);
  }

  /* ------------------------------------------------------------- 会话操作 */
  function reloadSessions() {
    return api('/api/state').then(function (data) {
      state.sessions = data.sessions || [];
      renderSessions();
    });
  }

  function openSession(id) {
    return api('/api/session?id=' + encodeURIComponent(id)).then(function (res) {
      var session = res.session;
      // 对话属于创建它的工作区：打开时切回去（这个工作区也顺便展开），避免之后把文件写错地方
      if (session.workspace) setWorkspaceOpen(session.workspace, true);
      return switchWorkspace(session.workspace, { quiet: !!session.workspace }).then(function (switched) {
        if (switched) toast('已切到该对话的工作区：' + folderName(session.workspace), 'ok');
        applySession(session);
      });
    }).catch(function (error) { toast(error.message, 'error'); });
  }

  function applySession(session) {
    state.session = session;
    localStorage.setItem('w7a.session', session.id);
    renderAll();
  }

  function newSession() {
    return api('/api/session/new', { method: 'POST', body: {
      model: state.config.model, workspace: state.config.workspace } })
      .then(function (res) {
        state.session = res.session;
        localStorage.setItem('w7a.session', res.session.id);
        $('#sidebar').classList.remove('open');
        return reloadSessions();
      }).then(function () {
        renderAll();
        $('#input').focus();
      }).catch(function (error) { toast(error.message, 'error'); });
  }

  function regenerate() {
    if (state.streaming || !state.session) return;
    api('/api/session/rewind', { method: 'POST', body: { id: state.session.id } })
      .then(function (res) {
        state.session = res.session;
        renderAll();
        if (res.text) return send(res.text);
      }).catch(function (error) { toast(error.message, 'error'); });
  }

  /* ------------------------------------------------------------- 发送与流式 */
  /* 上一次运行还在收尾时服务端会回 409。别让老师对着"这个对话正在生成中"发愣：
     先强制停掉那一次运行，等 1 秒自动把这条消息重发一次（只重试一次，避免打转）。 */
  function retryAfterBusy(sessionId, text, attached, images, attempt) {
    api('/api/stop', { method: 'POST', body: { runId: state.runId, force: true } })
      .catch(function () { /* 运行可能已经结束了，无所谓 */ })
      .then(function () {
        setTimeout(function () {
          if (!state.session || state.session.id !== sessionId) return;
          toast('上一次运行已强制结束，正在重发…', 'warn');
          sendText(sessionId, text, attached, images, (attempt || 0) + 1);
        }, 1000);
      });
  }

  function send(text) {
    if (state.streaming) return;
    text = (text == null ? $('#input').value : text).trim();
    if (!text) return;
    if (!state.session) return;
    if (!state.config.model) { toast('请先在设置中填写模型名称', 'error'); openSettings(); return; }
    var attached = state.images.slice();
    state.images = [];
    renderImageBar();
    $('#input').value = '';
    updateTokenHint();
    sendText(state.session.id, text, attached, attached, 0);
  }

  function sendText(sessionId, text, attached, images, attempt) {
    if (!state.session || state.session.id !== sessionId) return;

    setStreaming(true);
    if (!state.session.messages.length) {
      $('#thread').innerHTML = '';
    }
    var userMessage = { role: 'user', content: text, ts: nowText() };
    if (attached && attached.length) userMessage.images = attached.slice();
    state.session.messages.push(userMessage);
    $('#thread').appendChild(userNode(userMessage));
    renderImageBar();
    scrollToBottom(true);

    state.run = { started: Date.now(), rounds: 0, tools: [], prompt: 0, completion: 0,
                  hit: 0, miss: 0, reasoning: 0, cost: 0, effort: currentEffort() };

    var controller = new AbortController();
    state.controller = controller;

    fetch('/api/chat', {
      method: 'POST',
      headers: AUTH_TOKEN ? { 'Content-Type': 'application/json', 'X-Auth-Token': AUTH_TOKEN }
                          : { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sessionId: sessionId, text: text,
                             model: currentModelSelection(),
                             effort: currentEffort(),
                             images: (attached || []).map(function (item) {
                               return { path: item.path, name: item.name, mime: item.mime };
                             }) }),
      signal: controller.signal
    }).then(function (response) {
      if (!response.ok) {
        return response.json().catch(function () { return {}; }).then(function (data) {
          var message = data.error || ('HTTP ' + response.status);
          // "正在生成中"：先把上一次运行强制收掉，再自动重发一次
          if (response.status === 409 && (attempt || 0) < 1 &&
              message.indexOf('生成中') >= 0) {
            state.session.messages.pop();          // 这条消息马上会重发，别留在界面上两份
            if ($('#thread').lastChild) $('#thread').removeChild($('#thread').lastChild);
            setStreaming(false);
            retryAfterBusy(sessionId, text, attached, images, attempt);
            return;
          }
          throw new Error(message);
        });
      }
      return readStream(response);
    }).catch(function (error) {
      if (error.name === 'AbortError') return;
      toast(error.message, 'error');
    }).then(function () {
      clearStopTimers();                 // 流结束了，停止看门狗收工
      setStreaming(false);
      renderInspector();
      reloadSessions();
      // 只同步元数据，不重画整条对话：重画会把折叠状态和刚出现的「继续生成」按钮一起冲掉
      if (state.session) {
        api('/api/session?id=' + encodeURIComponent(state.session.id)).then(function (res) {
          var session = res.session || {};
          state.session.title = session.title || state.session.title;
          state.session.usage = session.usage || state.session.usage;
          state.session.incomplete = !!session.incomplete;
          /* 会话跑长了就把窗口外的历史收掉。这里必须**先把服务端的完整消息取回来**：
             刚写完的这轮回答此前只存在于 DOM 里，光重画会把老师刚看到的答案抹掉。
             取回来之后重画一次：这一轮的过程折成一行（照 DSH），窗口外的历史也不建节点。 */
          if (session.messages) state.session.messages = session.messages;
          settleTurnView();
          renderHeader();
          renderInspector();
          reloadSessions();
        }).catch(function () { /* 同步失败不影响已经画出来的对话 */ });
      }
    });
  }

  function currentModelSelection() {
    return $('#modelSelect').value || '';
  }

  function currentEffort() {
    return $('#effortSelect') ? $('#effortSelect').value || '' : '';
  }

  /* 推理等级跟着会话走：会话里选过就用会话的，否则沿用设置里的默认值 */
  function renderEffort() {
    var select = $('#effortSelect');
    if (!select) return;
    var session = state.session || {};
    var fallbackLevel = (state.config && state.config.reasoningEffort) || '';
    fillEffortSelect(select, session.effort || '', fallbackLevel ? effortLabel(fallbackLevel) : '');
    select.title = '推理等级：越高想得越久、越周密，也越慢越贵。\n'
      + effortHint(session.effort || fallbackLevel);
  }

  function readStream(response) {
    var reader = response.body.getReader();
    var decoder = new TextDecoder('utf-8');
    var buffer = '';
    return reader.read().then(function step(chunk) {
      if (chunk.done) {
        flushFrames();
        return;
      }
      buffer += decoder.decode(chunk.value, { stream: true });
      var index;
      while ((index = buffer.indexOf('\n\n')) >= 0) {
        var frame = buffer.slice(0, index);
        buffer = buffer.slice(index + 2);
        handleFrame(frame);
      }
      return reader.read().then(step);
    });

    function flushFrames() {
      if (buffer.trim()) handleFrame(buffer);
      buffer = '';
    }
  }

  function handleFrame(frame) {
    frame.split('\n').forEach(function (line) {
      if (line.indexOf('data:') !== 0) return;
      var payload = line.slice(5).trim();
      if (!payload) return;
      var event;
      try { event = JSON.parse(payload); } catch (error) { return; }
      handleEvent(event);
    });
  }

  function handleEvent(event) {
    switch (event.t) {
      case 'start': return onStart(event);
      case 'round': return onRound(event);
      case 'reasoning': return onDelta('reasoning', event.d);
      case 'trace_point': return onTracePoint(event);
      case 'retry': return onRetry(event);
      case 'retry_reset': return onRetryReset();
      case 'compacted': return onCompacted(event);
      case 'content': return onDelta('content', event.d);
      case 'tool_start': return onToolStart(event);
      case 'tool_end': return onToolEnd(event);
      case 'confirm': return onConfirm(event);
      case 'image': return onInjectedImage(event);
      case 'skills': return onActiveSkills(event);
      case 'circuit': return onCircuit(event);
      case 'board': return onBoard(event);
      case 'figure': return onFigure(event.figure);
      case 'plane': return window.W7Plane && window.W7Plane.onEvent(event);
      case 'timer': return onTimer(event);
      case 'sub_start': return onSubStart(event);
      case 'sub_done': return onSubDone(event);
      case 'turn': return onTurn(event);
      case 'notice': return onNotice(event);
      case 'stopped': return onStopped(event);
      case 'error': return onError(event);
      case 'done': return onDone(event);
      case 'close': return;
      default: return;
    }
  }

  function onStart(event) {
    state.runId = event.runId;
    state.run.started = Date.now();
    state.run.model = event.model;
    state.run.effort = event.effort || '';      // 服务端实际用的等级（可能被接口拒绝后降级）
    beginAssistant();
    traceReset();
    resetSubagents();
    setStatus('生成中', 'busy');
  }

  function beginAssistant() {
    var wrap = el('div', 'msg assistant');
    wrap.appendChild(avatarWhale());
    var bubble = el('div', 'bubble');
    var head = el('div', 'msg-head');
    var title = el('span', null, 'Study7Agent');
    var meta = el('span', 'muted', '思考中…');
    head.appendChild(title);
    head.appendChild(meta);
    bubble.appendChild(head);

    // 时间线：思考、正文、工具调用按发生顺序往下排（最新的一定在最下面）
    var stream = streamNode();
    bubble.appendChild(stream);
    var usage = el('div', 'usage-line');
    usage.style.display = 'none';
    bubble.appendChild(usage);
    wrap.appendChild(bubble);
    $('#thread').appendChild(wrap);

    state.current = {
      wrap: wrap, bubble: bubble, meta: meta, stream: stream, thinkStep: null,
      textBlock: null, usage: usage, raw: { content: '', reasoning: '' }, pending: false,
      toolNodes: {}, started: Date.now(), message: null,
      // 本轮"做出来的文件"：工具参数、去重表、清单、以及那条「本次产物」
      toolArgs: {}, artifacts: [], artifactSeen: {}, artifactStrip: null
    };
    scrollToBottom(true);
  }

  function onRound(event) {
    state.run.rounds = event.round;
    var ctx = state.current;
    if (ctx) {
      ctx.meta.textContent = '第 ' + event.round + ' 轮';
      streamBreak(ctx);            // 新的一轮：思考与正文都另起节点
    }
    traceRound(event.round);
    renderInspector();
  }

  function onDelta(kind, text) {
    var ctx = state.current;
    if (!ctx) return;
    if (kind === 'reasoning') {
      markDelta();
      ctx.raw.reasoning += text;
      if (!ctx.thinkStep) ctx.thinkStep = streamThink(ctx, '');   // 新的一段思考，另起一行
      thinkAppend(ctx.thinkStep, text);
      state.run.reasoning = Math.round(ctx.raw.reasoning.length / 4);
    } else {
      markDelta();
      ctx.raw.content += text;
      if (!ctx.textBlock) ctx.textBlock = streamText(ctx, '');    // 新的一段正文，另起一块
      var block = ctx.textBlock;
      block.raw += text;
      scheduleRender(ctx, block);       // 把**这一段**传进去：中途换段也不会把内容画到别的块上
    }
    if (kind === 'reasoning') traceScrollSoon();     // 全量思考进时间线；右侧只显示要点（trace_point 事件）
    /* 滚动也跟着节流：每个增量都 scrollToBottom + getBoundingClientRect 会强制同步布局，
       一秒钟几十次就是"越输出越卡"的另一半原因。 */
    var now = Date.now();
    if (!ctx.lastScrollAt || now - ctx.lastScrollAt > 200) {
      ctx.lastScrollAt = now;
      scrollToBottom(false);
      keepLatestInView(ctx);
    }
  }

  /* 正文一长，最新的思考和工具调用就会被挤到视野外。这里保证"最新那一条"始终可见：
     流式过程中把最新节点滚进视野（只在这一条已经在屏幕附近时才动，避免和老师抢滚动条）。 */
  function keepLatestInView(ctx) {
    if (!ctx || !ctx.stream) return;
    var last = ctx.stream.lastElementChild;
    if (!last) return;
    /* 滚动的是 #messages（外层容器），不是 #thread；以前这里取的是根本不存在的
       .thread-wrap，退回 #thread 后 scrollHeight 等于 clientHeight，
       nearBottom 恒为真，于是每 200 毫秒都要 scrollIntoView 一次，白白引起重排。 */
    var box = $('#messages') || document.querySelector('.thread-wrap') || $('#thread');
    if (!box) return;
    var nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 160;
    if (!nearBottom) return;                     // 老师自己滚上去看了，就别硬拉回来
    var rect = last.getBoundingClientRect();
    var boxRect = box.getBoundingClientRect();
    if (rect.bottom > boxRect.bottom - 12 || rect.top < boxRect.top) {
      last.scrollIntoView({ block: 'nearest' });
    }
  }

  /* 流式正文的重画节奏 —— 这里以前是"每 90 毫秒把整段 markdown 重新解析一遍再 innerHTML 重写"，
     成本随正文长度**平方增长**：答案一长就卡得打不动字，工具一多（会话更长）更明显
     （老师："多跑几个工具就卡，尤其内容输出卡得不得了"）。
     现在两条闸门：① 距上次重画至少 180 毫秒；② 至少新增 160 个字才值得重画。
     正文照旧是"流式就已经是 markdown"，只是重画次数少了十几倍；每段结束时再补一次最终渲染，
     短答案也不会因为没攒够字数而一直不出样式。 */
  var RENDER_MIN_MS = 180;
  var RENDER_MIN_CHARS = 160;

  function paintText(ctx, target) {
    if (!target || !target.parentNode) return;
    target.innerHTML = MD.render(target.raw || '');
    target.painted = (target.raw || '').length;
    target.paintedAt = Date.now();
  }

  /* 这一段结束了（换思考、开始调工具、整条回答完成）：立刻按 markdown 落定一次 */
  function flushRender(target) {
    if (!target) return;
    var raw = target.raw || '';
    if (target.painted === raw.length) return;     // 已经画过了，别白重排一次
    paintText(null, target);
    target.classList.remove('caret');
  }

  function scheduleRender(ctx, block) {
    var target = block || ctx.textBlock;
    if (!target) return;                           // 这一段已经被撤掉（比如重试作废），不画了
    if (ctx.pending) return;
    var raw = target.raw || '';
    if (raw.length - (target.painted || 0) < RENDER_MIN_CHARS) return;   // 涨得不多，先攒着
    var since = Date.now() - (target.paintedAt || 0);
    ctx.pending = true;
    setTimeout(function () {
      ctx.pending = false;
      if (state.current !== ctx) return;           // 已经换了一轮，别再动旧节点
      paintText(ctx, target);
      target.classList.add('caret');
      scrollToBottom(false);
    }, since < RENDER_MIN_MS ? (RENDER_MIN_MS - since) : 0);
  }

  function onToolStart(event) {
    var ctx = state.current;
    if (!ctx) return;
    if (event.parent) {  // 子代理内部的工具调用：嵌到子代理卡片里
      onSubStep(event.parent, event, 'run');
      return;
    }
    // 开始调工具：这一段思考收尾（下一次思考会另起一行），工具行插在当前位置
    thinkSettle(ctx.thinkStep);
    ctx.thinkStep = null;
    flushRender(ctx.textBlock);            // 正文段落收尾：按 markdown 落定一次
    if (ctx.textBlock) ctx.textBlock.classList.remove('caret');
    ctx.textBlock = null;
    if (event.name === 'run_subagent') {
      var card = subagentCard(event);
      streamAppend(ctx, card.box);
      ctx.toolNodes[event.id] = card.box;
      state.subCards[event.id] = card;
      state.run.tools.push({ name: '子代理', detail: '', status: 'run', seconds: null });
      renderInspector();
      scrollToBottom(true);
      return;
    }
    var node = toolNode({ id: event.id, function: { name: event.name, arguments: JSON.stringify(event.args, null, 2) } },
                        null, true);
    streamAppend(ctx, node);
    ctx.toolNodes[event.id] = node;
    ctx.toolArgs[event.id] = event.args || {};    // 工具结束时事件里没有参数，先记下来给产物识别用
    state.run.tools.push({ name: TOOL_LABELS[event.name] || event.name,
                           detail: oneLineText(toolDetail(event.name, event.args || {})),
                           status: 'run', seconds: null });
    renderInspector();
    scrollToBottom(true);
  }

  function subagentCard(event) {
    var box = el('div', 'sub-card');
    var head = el('div', 'sub-head');
    var icon = el('span', 'p-ico');
    icon.innerHTML = svgIcon('agent', 15);
    head.appendChild(icon);
    var role = el('span', 'sub-role', '子代理');
    var task = el('span', 'sub-task', '');
    var badge = el('span', 'badge run', '执行中');
    head.appendChild(role);
    head.appendChild(task);
    head.appendChild(badge);
    box.appendChild(head);
    var steps = el('div', 'sub-steps');
    box.appendChild(steps);
    return { box: box, role: role, task: task, badge: badge, steps: steps, summary: null };
  }

  function onSubStart(event) {
    var record = subagentRecord(event.id);
    record.role = event.role || '子代理';
    record.task = event.task || '';
    record.status = 'run';
    record.started = record.started || Date.now();
    renderSubagents();
    var card = state.subCards[event.id];
    if (!card) {
      var ctx = state.current;
      if (!ctx) return;
      card = subagentCard(event);
      streamAppend(ctx, card.box);
      state.subCards[event.id] = card;
    }
    card.role.textContent = event.role || '子代理';
    card.task.textContent = event.task || '';
    card.task.title = event.task || '';
    scrollToBottom(true);
  }

  function onSubStep(parentId, event, status) {
    var record = subagentRecord(parentId);
    if (status === 'run') {
      // 只统计"开始执行"的次数；子代理是否还在跑由 sub_done 决定，
      // 不能因为它的某个工具结束了就把整个子代理标成完成
      record.steps += 1;
      state.subToolArgs[event.id] = event.args || {};   // 子代理做的文件也要能出现在「本次产物」里
    }
    record.last = (event.name || '工具') + ' '
      + summarizeArgs(event.name || '', event.args || {});
    renderSubagents();
    var card = state.subCards[parentId];
    var ctx = state.current;
    if (ctx && status !== 'run') {
      addLiveArtifacts(ctx, toolArtifacts(event.name || '',
        state.subToolArgs[event.id] || {}, event.meta, status));
    }
    if (!card && ctx) {
      card = subagentCard({ id: parentId });
      streamAppend(ctx, card.box);
      state.subCards[parentId] = card;
    }
    if (!card) return;
    /* 子代理的每一步也是一行灰字、**一个节点**（跟主时间线同一个观感）。
       老写法每步 4 个子节点（点 + 名字 + 参数 + 时间），子代理一多同样拖慢页面。 */
    var key = 'step-' + event.id;
    var row = card.steps.querySelector('[data-step="' + key + '"]');
    if (!row) {
      row = el('div', 'sub-step');
      row.setAttribute('data-step', key);
      row.args = event.args || {};                 // 结束时事件里没有参数，先记下来
      card.steps.appendChild(row);
    } else if (status === 'run') {
      row.args = event.args || {};
    }
    /* 状态文案由 toolRowText 自己写（失败会长成「…（失败）」），这里只补耗时 */
    var took = event.meta && event.meta.seconds != null ? ' · ' + event.meta.seconds + 's' : '';
    row.className = 'sub-step ' + badgeClass(status);
    row.textContent = toolRowText(event.name || '工具', row.args || {}, status, null, 0)
      + (status === 'run' ? ' · …' : took);
    scrollToBottom(false);
  }

  function onSubDone(event) {
    var record = subagentRecord(event.id);
    record.status = event.status || 'ok';
    record.rounds = event.rounds || 0;
    record.seconds = event.seconds != null ? event.seconds : null;
    record.summary = event.summary || '';
    record.last = record.summary || record.last || '已结束';
    renderSubagents();
    var card = state.subCards[event.id];
    if (card) {
      card.badge.className = 'badge ' + badgeClass(event.status || 'ok');
      card.badge.textContent = event.status === 'ok'
        ? ('完成 · ' + (event.rounds || 0) + ' 轮') : badgeText(event.status || 'ok');
      if (event.summary) {
        card.summary = el('div', 'sub-summary', event.summary);
        card.box.appendChild(card.summary);
      }
    }
    var item = state.run.tools.filter(function (row) { return row.status === 'run'; })[0];
    if (item) {
      item.status = event.status || 'ok';
      item.seconds = event.seconds;
    }
    renderInspector();
    scrollToBottom(true);
  }

  /* 停止原因自己说清楚：超时 / 重复 / 其它。
     "已自动熔断"这四个字太笼统，老师看不出到底为什么停、下一步该干什么。 */
  var CIRCUIT_TITLES = {
    timeout: '到了时长上限，先停一下',
    repeat: '同一件事重复太多次，先停一下',
    reasoning: '这一轮想得太久，先停一下'
  };

  function onCircuit(event) {
    var ctx = state.current;
    var node = el('div', 'notice warn');
    node.appendChild(el('strong', null, (CIRCUIT_TITLES[event.reason] || '先停一下') + '：'));
    node.appendChild(el('span', null, event.message || '检测到可能陷入循环，已停止本次运行。'));
    if (ctx) streamAppend(ctx, node);
    else $('#thread').appendChild(node);
    setStatus('已停止', 'warn');
    toast(event.message || '已停止本次运行', 'warn');
  }

  /* 老配置迁移提示：只弹一次，免得老师以为设置自己变了 */
  function noteMigrations(config) {
    var list = (config && config.migratedSettings) || [];
    if (!list.length || state.migrationNoted) return;
    state.migrationNoted = true;
    var names = { max_tool_rounds: '工具最大轮次', trim_messages: '历史条数上限',
                  max_run_seconds: '单次运行最长秒数', max_images: '图片张数上限' };
    var text = list.map(function (item) {
      return (names[item.key] || item.key) + ' ' + item.from + ' 改成 ' + item.to;
    }).join('；');
    toast('已把旧版本的默认设置升级到新值：' + text + '（设置界面里可以改回来）', 'warn');
  }

  function onToolEnd(event) {
    if (event.parent) {
      onSubStep(event.parent, event, event.status || 'ok');
      return;
    }
    var ctx = state.current;
    var item = state.run.tools.filter(function (row) { return row.status === 'run'; })[0];
    if (item) {
      item.status = event.status;
      item.seconds = event.meta && event.meta.seconds;
    }
    if (event.name === 'run_subagent') {
      var card = state.subCards[event.id];
      if (card) {
        card.badge.className = 'badge ' + badgeClass(event.status || 'ok');
        card.badge.textContent = event.status === 'ok' ? '已返回' : badgeText(event.status);
      }
      var node = ctx && ctx.toolNodes[event.id];
      if (node) {
        var summary = el('div', 'sub-summary', event.output || '');
        node.appendChild(summary);
      }
      renderInspector();
      scrollToBottom(true);
      return;
    }
    if (ctx && ctx.toolNodes[event.id]) {
      /* 就地改那一行（不换节点）：换节点 = 整条消息重新布局，工具一多就卡 */
      updateToolRow(ctx.toolNodes[event.id], event.name,
                    (ctx.toolArgs || {})[event.id] || {}, event.status,
                    { content: event.output, meta: event.meta, status: event.status });
    }
    if (ctx) {
      // 这一轮又做出东西了就补进「本次产物」（老师不用等这一轮结束才能点开看）
      addLiveArtifacts(ctx, toolArtifacts(event.name,
        (ctx.toolArgs || {})[event.id] || {}, event.meta, event.status));
    }
    renderInspector();
    scrollToBottom(false);
  }

  function onConfirm(event) {
    state.confirmMeta = event;
    $('#confirmReason').textContent = event.reason || '';
    $('#confirmCommand').textContent = (event.args && event.args.command) || JSON.stringify(event.args, null, 2);
    var dialog = $('#confirmDialog');
    if (!dialog.open) dialog.showModal();
  }

  function answerConfirm(approved) {
    var dialog = $('#confirmDialog');
    if (dialog.open) dialog.close();
    var meta = state.confirmMeta;
    state.confirmMeta = null;
    if (!meta) return;
    api('/api/confirm', { method: 'POST', body: { runId: meta.runId || state.runId, id: meta.id, approved: approved } })
      .catch(function (error) { toast(error.message, 'error'); });
  }

  function onInjectedImage(event) {
    var ctx = state.current;
    if (!ctx) return;
    streamAppend(ctx, injectedImageNode(event.path, event.name));
    scrollToBottom(true);
  }

  function onActiveSkills(event) {
    state.activeSkills = event.names || [];
    var box = $('#activeSkills');
    box.textContent = state.activeSkills.length ? state.activeSkills.join('、') : '未启用';
    box.title = '本次运行实际带入的技能';
  }

  /* ---------------------------------------------------------- 课堂大屏与计时 */
  function onBoard(event) {
    var board = $('#board');
    if (event.action === 'hide') {
      board.classList.add('hidden');
      return;
    }
    var kinds = { points: '要点', question: '题目', answer: '答案', title: '主题',
                  notice: '提醒', pick: '随机抽到', group: '分组结果' };
    $('#boardKind').textContent = kinds[event.kind] || '展示';
    $('#boardTitle').textContent = event.title || '';
    var body = $('#boardBody');
    body.innerHTML = '';
    body.classList.toggle('plain', event.kind === 'question' || event.kind === 'answer');
    (event.body || []).forEach(function (line) {
      var row = el('div', 'board-line');
      row.appendChild(el('span', null, line));
      body.appendChild(row);
    });
    $('#boardFooter').textContent = event.footer || '';
    // 「完全重置 / 抽下一个」只在抽学号的场合出现：分组结果、题目、要点这些用不上
    $('#boardActions').classList.toggle('hidden', event.kind !== 'pick');
    board.classList.remove('hidden');
  }

  function closeBoard() {
    $('#board').classList.add('hidden');
    stopRing();          // 倒计时刚到点时大屏上正响着"叮"：关掉大屏必须立刻静音
  }

  /* 大屏上的「完全重置 / 抽下一个」。
     抽签这件事的真实记录（抽过哪些学号）存在服务端的会话文件里，所以必须打接口，
     不能在本地自己 random —— 那样会和模型抽的重复，而且刷新页面就忘了。 */
  function boardPick(action) {
    if (!state.session) { toast('还没有打开会话', 'error'); return; }
    api('/api/classroom/pick', { method: 'POST', body: { id: state.session.id, action: action } })
      .then(function (res) {
        (res.events || []).forEach(onBoard);
        if (res.text) toast(res.text, 'ok');
      }).catch(function (error) { toast(error.message, 'error'); });
  }

  /* 课堂计时：时长让老师在网页里选（悬浮工具栏的「计时」按钮也走这里） */
  var TIMER_PRESETS = [1, 3, 5, 10, 15];

  function openTimerDialog() {
    var box = $('#timerPresets');
    if (!box.childNodes.length) {
      TIMER_PRESETS.forEach(function (minutes) {
        var chip = el('button', 'chip', minutes + ' 分钟');
        chip.onclick = function () {
          $('#timerSeconds').value = String(minutes * 60);
          closeDialog('timerDialog');
          startTimerFromDialog();
        };
        box.appendChild(chip);
      });
    }
    if (!$('#timerSeconds').value) $('#timerSeconds').value = '300';
    var dialog = $('#timerDialog');
    // 工具栏连点两下「计时」会排两条命令：已经开着就不要再 showModal（会抛 InvalidStateError）
    if (!dialog.open) dialog.showModal();
  }

  function startTimerFromDialog() {
    var seconds = Math.round(Number($('#timerSeconds').value) || 0);
    if (!(seconds > 0)) { toast('请填写计时秒数', 'error'); return; }
    onTimer({ action: 'start', seconds: Math.min(7200, seconds),
              label: ($('#timerLabelInput').value || '').trim() || '课堂计时' });
  }

  function closeDialog(id) {
    var dialog = document.getElementById(id);
    if (dialog) dialog.close();
  }

  /* ------------------------------------------------------------- 作图面板 */
  /* 坐标全部由服务端算好（plot.py），这里只负责画。
     两个来源共用一个面板：老师自己敲表达式（POST /api/figure），
     或者 AI 调 show_figure 之后推过来的 figure 事件。 */
  var FIG_W = 1000;              // 长边的 viewBox 尺寸（短边按容器比例算，见 figureBox）
  var FIG_H = 640;
  var FIG_PAD = 34;              // 边距小一点：屏幕上的坐标系能占更大地方

  /* viewBox 的尺寸**跟着容器比例走**。

     以前写死 1000x640：容器是 990x800 时比例对不上，preserveAspectRatio 会
     上下各留一条黑边，白白浪费掉 160 多像素的高度（老师反馈"屏幕上的坐标系
     显示区域太小"）。现在按容器的真实比例算，一点不浪费。 */
  function figureBox() {
    var canvas = document.getElementById('figureCanvas');
    var width = (canvas && canvas.clientWidth) || 1000;
    var height = (canvas && canvas.clientHeight) || 640;
    if (!(width > 0) || !(height > 0)) return { w: 1000, h: 640 };
    var k = 1000 / Math.max(width, height);
    return { w: Math.max(200, Math.round(width * k)),
             h: Math.max(200, Math.round(height * k)) };
  }

  /* 作图面板上的"AI 在不在画"。
     老师点了「让 AI 画」之后盯着一块空白面板，不给个明确状态他不知道是没开始、
     还在想、还是压根没画。所以：等的时候转圈 + 「正在思考」，画完变绿说「画好了」，
     这一轮结束还没画就变黄说清楚——**不能默默什么都不显示**。 */
  var figureWaiting = false;
  var figureStatusTimer = null;

  function setFigureStatus(text, kind, autoHideMs) {
    var box = $('#figureStatus');
    if (figureStatusTimer) { clearTimeout(figureStatusTimer); figureStatusTimer = null; }
    if (!text) {
      box.className = 'figure-status hidden';
      return;
    }
    box.className = 'figure-status' + (kind ? ' ' + kind : '');
    $('#figureStatusText').textContent = text;
    if (autoHideMs) {
      figureStatusTimer = setTimeout(function () {
        figureStatusTimer = null;
        box.className = 'figure-status hidden';
      }, autoHideMs);
    }
  }

  /* --------------------------------------------------- 计算器 / 单位换算 */
  // 一体机上多半是触摸屏，所以要有够大的键盘；实体键盘也能直接敲。
  var CALC_PAD = [
    ['C', 'clear'], ['back', 'back'], ['(', 'ins:('], [')', 'ins:)'], ['√', 'ins:sqrt('],
    ['sin', 'ins:sin('], ['cos', 'ins:cos('], ['tan', 'ins:tan('], ['π', 'ins:pi'], ['^', 'ins:^'],
    ['7', 'ins:7'], ['8', 'ins:8'], ['9', 'ins:9'], ['÷', 'ins:/'], ['%', 'ins:%'],
    ['4', 'ins:4'], ['5', 'ins:5'], ['6', 'ins:6'], ['×', 'ins:*'], ['!', 'ins:!'],
    ['1', 'ins:1'], ['2', 'ins:2'], ['3', 'ins:3'], ['−', 'ins:-'], ['ln', 'ins:ln('],
    ['0', 'ins:0'], ['.', 'ins:.'], ['=', 'eval'], ['+', 'ins:+'], ['log', 'ins:log10(']
  ];
  var calcHistory = [];
  var calcDegrees = false;
  // 搜索防抖的定时器**必须先声明**：读一个没声明过的变量会直接抛 ReferenceError，
  // 那个 oninput 处理函数就死在那儿了——表现是"搜单位根本搜不了"（真机上就是这个）。
  var unitSearchTimer = null;
  var unitState = { measure: 'length', units: [], from: '', to: '' };
  // 「让 AI 解方程 / 因式分解」在等这一轮：记下结果往哪个盒子里放
  var aiCapture = null;

  /* 把一句话发给 AI，等它这一轮跑完，把回答显示到面板上的盒子里。
     跑的时候盒子里显示"AI 正在思考"，这样老师知道它在干活（不然就是个空白框）。 */
  function askAiInto(box, noteBox, prompt) {
    if (!state.session) { showNote(noteBox, '还没有打开对话'); return; }
    if (!state.config.model) { showNote(noteBox, '请先在设置里填写模型名称'); return; }
    if (state.streaming) { showNote(noteBox, '正在生成中，等这一轮结束再试'); return; }
    noteBox.style.display = 'none';
    box.innerHTML = '';
    box.appendChild(el('div', 'ai-wait', 'AI 正在思考…'));
    aiCapture = { box: box, prompt: prompt };
    send(prompt);
  }

  function finishAiCapture() {
    var job = aiCapture;
    aiCapture = null;
    if (!job) return;
    var ctx = state.current;
    var text = (ctx && ctx.raw && ctx.raw.content ? ctx.raw.content : '').trim();
    job.box.innerHTML = '';
    if (!text) {
      job.box.appendChild(el('div', 'muted',
        'AI 这一轮没给出文字结果，看看左边对话里它说了什么。'));
      return;
    }
    job.box.appendChild(el('div', 'ai-answer', '')).innerHTML = MD.render(text);
  }

  function showNote(box, text) {
    box.style.display = '';
    box.className = 'notice error';
    box.textContent = text;
  }

  /* ---------------------------------------------------- 解方程 / 因式分解 */
  var solveKind = 'equation';

  function setSolveKind(kind) {
    solveKind = kind === 'factor' ? 'factor' : 'equation';
    $('#solveKindEquation').classList.toggle('active', solveKind === 'equation');
    $('#solveKindFactor').classList.toggle('active', solveKind === 'factor');
    $('#solveInput').placeholder = solveKind === 'factor'
      ? '例如 x^2 - 9、x^2 - 5x + 6、2x^2 + 4x'
      : '例如 x^2 - 5x + 6 = 0、2x + 3 = 7';
    $('#solveGo').textContent = solveKind === 'factor' ? '让 AI 分解' : '让 AI 解';
  }

  function solveWithAi(kind) {
    var text = ($('#solveInput').value || '').trim();
    var note = $('#solveNote');
    if (!text) { showNote(note, '先把方程或式子写进来'); return; }
    var prompt;
    if (kind === 'factor') {
      prompt = '把下面的式子因式分解：' + text + '\n'
        + '要求：给出分解结果（用乘方写紧凑），并注明用到的公式或方法（提公因式 / 平方差 / '
        + '完全平方 / 十字相乘 / 分组分解 / 求根公式）。只讲这一件事，不要展开别的。';
    } else {
      prompt = '解下面的方程：' + text + '\n'
        + '要求：先给解集（写成 x=… 的形式，能用分数或根号就用精确值，不要只给小数），'
        + '再简单写一两句步骤要点。只讲这一件事，不要展开别的。';
    }
    askAiInto($('#solveResult'), note, prompt);
  }

  function openCalc(tab) {
    var dialog = $('#calcDialog');
    buildCalcPad();
    setCalcTab(tab === 'units' ? 'units' : 'math');
    if (!dialog.open) dialog.showModal();
    if (tab === 'units') {
      if (!unitState.units.length) loadMeasures();
      setTimeout(function () { $('#unitSearch').focus(); }, 30);
    } else {
      setTimeout(function () { $('#calcInput').focus(); }, 30);
    }
  }

  function buildCalcPad() {
    var pad = $('#calcPad');
    if (pad.childNodes.length) return;
    CALC_PAD.forEach(function (item) {
      var label = item[0];
      var action = item[1];
      var button = el('button', 'calc-key' + (action === 'eval' ? ' primary' : '')
        + (label === 'C' ? ' danger' : ''));
      if (label === 'back') {
        // 退格用内联 SVG：Win7 上没有左箭头的字形，用字符会显示成方框
        button.innerHTML = svgIcon('backspace', 18);
        button.title = '退格';
      } else {
        button.textContent = label;
      }
      button.onclick = function () { calcKey(action); };
      pad.appendChild(button);
    });
  }

  function calcKey(action) {
    var box = $('#calcInput');
    if (action === 'clear') { box.value = ''; showCalcResult(null); return; }
    if (action === 'back') { box.value = box.value.slice(0, -1); return; }
    if (action === 'eval') { calcEvaluate(); return; }
    if (action.indexOf('ins:') === 0) {
      box.value += action.slice(4);
      box.focus();
    }
  }

  function setCalcTab(tab) {
    $('#calcTabMath').classList.toggle('active', tab === 'math');
    $('#calcTabUnits').classList.toggle('active', tab === 'units');
    $('#calcTabSolve').classList.toggle('active', tab === 'solve');
    $('#calcMathPane').classList.toggle('hidden', tab !== 'math');
    $('#calcUnitsPane').classList.toggle('hidden', tab !== 'units');
    $('#calcSolvePane').classList.toggle('hidden', tab !== 'solve');
    $('#unitSwap').classList.toggle('hidden', tab !== 'units');
    // 从顶栏进来时先落在「计算器」页，这时还没拉过物理量列表；
    // 一切到换算页就得拉，否则两个下拉是空的（截图里真看到了）
    if (tab === 'units' && !unitState.units.length) loadMeasures();
    if (tab === 'solve') setTimeout(function () { $('#solveInput').focus(); }, 30);
  }

  function showCalcResult(result) {
    var exact = $('#calcExact');
    var decimal = $('#calcDecimal');
    if (!result) { exact.textContent = ''; decimal.textContent = ''; return; }
    exact.textContent = result.exact || '';
    decimal.textContent = (result.approximate && !result.exact ? '近似值 ' : '= ')
      + result.decimal;
  }

  function calcEvaluate() {
    var text = ($('#calcInput').value || '').trim();
    if (!text) return;
    api('/api/calc', { method: 'POST', body: { expression: text, degrees: calcDegrees } })
      .then(function (res) {
        showCalcResult(res.result);
        if (calcHistory[0] !== text) {
          calcHistory.unshift(text);
          calcHistory = calcHistory.slice(0, 8);
          renderCalcHistory();
        }
      }).catch(function (error) {
        showCalcResult(null);
        $('#calcDecimal').textContent = error.message;
      });
  }

  function renderCalcHistory() {
    var box = $('#calcHistory');
    box.innerHTML = '';
    if (!calcHistory.length) return;
    var head = el('div', 'calc-history-head');
    head.appendChild(el('span', 'muted', '刚才算过的'));
    var clear = el('button', 'btn small', '清空记录');
    clear.onclick = function () {
      calcHistory = [];
      renderCalcHistory();
    };
    head.appendChild(clear);
    box.appendChild(head);
    calcHistory.forEach(function (text) {
      var row = el('button', 'calc-history-row', text);
      row.onclick = function () { $('#calcInput').value = text; calcEvaluate(); };
      box.appendChild(row);
    });
  }

  function setCalcMode(degrees) {
    calcDegrees = !!degrees;
    $('#calcModeRad').classList.toggle('active', !calcDegrees);
    $('#calcModeDeg').classList.toggle('active', calcDegrees);
    if (($('#calcInput').value || '').trim()) calcEvaluate();
  }

  function loadMeasures() {
    api('/api/units', { method: 'POST', body: { action: 'measures' } }).then(function (res) {
      unitState.measures = res.measures || [];
      renderMeasures(unitState.measures, unitState.measure);
      loadUnits(unitState.measure);
    }).catch(function (error) { toast(error.message, 'error'); });
  }

  /* 物理量用一排小按钮，不用下拉：29 个物理量的原生下拉一展开就是一个长条，
     一体机上常常顶到屏幕外面去（老师反馈过"上面看不到"）。
     这里限高 + 可滚，顶部永远露着。 */
  function renderMeasures(measures, current) {
    var box = $('#unitMeasures');
    box.innerHTML = '';
    (measures || []).forEach(function (item) {
      var chip = el('button', 'unit-measure' + (item.key === current ? ' active' : ''),
                    item.cn);
      chip.title = item.cn + '（' + item.count + ' 个单位）';
      chip.onclick = function () {
        unitState.from = '';
        unitState.to = '';
        renderMeasures(measures, item.key);
        loadUnits(item.key);
      };
      box.appendChild(chip);
    });
    if (current) {
      var active = box.querySelector('.unit-measure.active');
      if (active && active.scrollIntoView) active.scrollIntoView({ block: 'nearest' });
    }
  }

  function loadUnits(measure) {
    unitState.measure = measure;
    api('/api/units', { method: 'POST', body: { action: 'units', measure: measure } })
      .then(function (res) {
        unitState.units = res.units || [];
        fillUnitSelect($('#unitFrom'), res.units, unitState.from || res.base);
        fillUnitSelect($('#unitTo'), res.units, unitState.to || pickSecond(res.units, res.base));
        unitState.from = $('#unitFrom').value;
        unitState.to = $('#unitTo').value;
        doConvert();
      }).catch(function (error) { toast(error.message, 'error'); });
  }

  function pickSecond(units, base) {
    // 默认挑一个和基准不同的单位，否则默认换算结果永远是 1
    for (var index = 0; index < (units || []).length; index++) {
      if (units[index].code !== base) return units[index].code;
    }
    return base;
  }

  function fillUnitSelect(select, units, chosen) {
    select.innerHTML = '';
    (units || []).forEach(function (unit) {
      var option = el('option', null, unit.cn + '（' + unit.code + '）' + (unit.hot ? ' · 常用' : ''));
      option.value = unit.code;
      select.appendChild(option);
    });
    select.value = chosen;
  }

  function doConvert() {
    var box = $('#unitResult');
    var value = Number($('#unitValue').value);
    if (!isFinite(value)) { box.textContent = '数值填得不对'; return; }
    api('/api/units', {
      method: 'POST',
      body: { action: 'convert', measure: unitState.measure, value: value,
              from: $('#unitFrom').value, to: $('#unitTo').value }
    }).then(function (res) {
      box.textContent = value + ' ' + res.from + ' = ' + res.exact + ' ' + res.to;
      $('#unitNote').style.display = 'none';
    }).catch(function (error) {
      box.textContent = '';
      $('#unitNote').style.display = '';
      $('#unitNote').className = 'notice error';
      $('#unitNote').textContent = error.message;
    });
  }

  function searchUnits() {
    var query = ($('#unitSearch').value || '').trim();
    var box = $('#unitHits');
    if (!query) { box.innerHTML = ''; return; }
    api('/api/units', { method: 'POST', body: { action: 'search', query: query } })
      .then(function (res) {
        box.innerHTML = '';
        var hits = res.hits || [];
        hits.forEach(function (hit) {
          var row = el('button', 'unit-hit', hit.cn + '（' + hit.code + '）· ' + hit.measureCn);
          row.onclick = function () {
            $('#unitSearch').value = '';
            box.innerHTML = '';
            unitState.from = hit.code;
            unitState.to = '';
            renderMeasures(unitState.measures || [], hit.measure);
            loadUnits(hit.measure);
          };
          box.appendChild(row);
        });
        if (!hits.length) {
          box.appendChild(el('div', 'muted', '没找到这个单位，换个说法试试（中文名或符号都行）'));
        }
      }).catch(function (error) {
        box.innerHTML = '';
        box.appendChild(el('div', 'muted', '搜索失败：' + error.message));
      });
  }

  /* 作图面板现在只画立体。

     函数 / 方程 / 平面几何全部搬到「几何画板」（plane.js + 平面面板）了：
     老师要求"把平面模块用来替换以前的函数、方程、平面几何模块"，所以这里不再有
     kind 切换，也不再处理 geometry 的图——**两条入口并存是这次要消掉的东西**。 */

  /* 面板当前这张图 + 动点状态。**必须声明在最早用到它的函数之前**：
     JS 里 `var` 只提升声明不提升赋值，`onFigure` 里读一个还没执行到的
     `var figureState` 就是 ReferenceError（点一下「作图」整块就死）。
     原来的声明夹在下面那堆二维动点代码里，删那堆时被一起删掉过一次（真踩过）。 */
  var figureState = { current: null, point: null };

  function onFigure(figure) {
    if (!figure) return;
    closeBoard();                       // 两个都是全屏内容，别叠在一起
    var panel = $('#figurePanel');
    panel.classList.remove('hidden');
    $('#figureTitleLabel').textContent = figure.title || figure.label || '';
    $('#figureNoteLabel').textContent = figure.note || '';
    var box = $('#figureCanvas');
    box.innerHTML = '';
    var svg = buildFigureSvg(figure);
    box.appendChild(svg);
    figureState.current = figure;
    solidShowTools(true);
    setupSolidGestures(svg, figure);
    setupSolidTools(svg, figure);
    // 这张图是不是"我们自己刚提交的那个"？是的话**不许重置工具状态**。
    // 原来每帧都 solidReset：拖动中每 70ms 重画一次就把 constraint / 工具选择清掉，
    // 表现就是"拖到一半突然不跟手、一点就取消拖动"（老师反馈的正是这个）。
    var source = (figure.source || []).join("\n");
    if (source !== solidState.sentSource || !solidState.shapes.length) {
      solidReset(figure.source || []);
    }
    renderSolidInfo($('#figureInfo'), figure);
    setFigureStatus('按住拖动可以转动视角，滚轮缩放；点一下图上的点可以选中它', 'done');
    // 画出来了：把"正在思考"收掉，明确告诉老师这一步完成了（8 秒后自己消失）
    if (figureWaiting) {
      figureWaiting = false;
      setFigureStatus('画好了', 'done', 8000);
    }
    // 这里**故意不抢焦点**：AI 画完只是把结果显示在这个面板里，
    // 老师可能正在看别的程序，弹到最前面会打断他（浏览器本来也抬不高自己的窗口）。
  }

  function closeFigure() {
    $('#figurePanel').classList.add('hidden');
  }

  var FIGURE_SAMPLE = {
    // 打开面板时先摆一张示例图：空画布上点工具、转视角都是"没反应"，
    // 老师会以为功能坏了（真被这么反馈过）。有东西在屏幕上，一切都立刻可用。
    solid: { kind: 'solid',
             shapes: ['A(0,0,0)', 'B(4,0,0)', 'C(4,3,0)', 'D(0,3,0)', 'box A B C D 3'],
             title: '长方体' }
  };

  /* 先摆一张空的立体图：面板开了就有效果，老师不用先猜画什么。 */
  function drawSampleFigure() {
    if (figureState.current) return;                 // 已经有图了就别覆盖
    var sample = FIGURE_SAMPLE.solid;
    api('/api/figure', { method: 'POST',
                         body: { kind: sample.kind, title: sample.title,
                                 shapes: sample.shapes } }).then(function (res) {
      // 示例图只负责"填一张空画布"：老师已经动手画了就别再插一脚。
      // 否则请求在路上飞的时候老师一点工具，示例图的响应回来就把 shapes 重置了。
      if (!res.figure || figureState.current) return;
      onFigure(res.figure);
    }).catch(function () { /* 示例图失败不打扰老师 */ });
  }

  function openFigurePanel() {
    $('#figurePanel').classList.remove('hidden');
    $('#panelNote').style.display = 'none';
    if (!$('#figureCanvas').childNodes.length) {
      $('#figureCanvas').appendChild(el('div', 'figure-empty',
        '在下面写立体图形（例如 box A B C D 3）点「画出来」，' +
        '或者写一句话点「让 AI 画」，比如「画一个底面边长 4 高 3 的正四棱柱」。' +
        '函数、方程、平面几何在顶栏「作图」打开的几何画板里。'));
      drawSampleFigure();
    }
    var dialog = $('#figureDialog');
    if (dialog.open) dialog.close();
    setTimeout(function () { $('#panelInput').focus(); }, 30);
  }

  /* 面板上直接画：只剩立体，一行一条（分号或换行隔开） */
  function drawFromPanel() {
    var text = ($('#panelInput').value || '').trim();
    if (!text) { panelError('先写点什么：立体图形一行一条，例如 A(0,0,0)；B(4,0,0)；box A B C D 3'); return; }
    var body = { kind: 'solid', title: ($('#figureTitleInput').value || '').trim(),
                 shapes: text.split(/[;\n；]/).map(function (line) { return line.trim(); })
                   .filter(function (line) { return line.length > 0; }) };
    api('/api/figure', { method: 'POST', body: body }).then(function (res) {
      $('#panelNote').style.display = 'none';
      onFigure(res.figure);
    }).catch(function (error) { panelError(error.message); });
  }

  /* 面板上直接让 AI 画：把这句话当普通提问发出去，AI 会用 show_figure 画到这块面板里 */
  function askAIFromPanel() {
    var text = ($('#panelInput').value || '').trim();
    if (!text) { panelError('用一句话说清要画什么，例如「画一个底面边长 4 高 3 的正四棱柱」'); return; }
    if (state.streaming) { panelError('正在生成中，等这一轮结束再让它画。'); return; }
    if (!state.session) { panelError('还没有打开对话'); return; }
    if (!state.config.model) { panelError('请先在设置里填写模型名称'); return; }
    $('#panelNote').style.display = 'none';
    $('#panelInput').value = '';
    figureWaiting = true;
    setFigureStatus('AI 正在思考，画好会直接出现在上面…');
    send(text);
  }

  function panelError(text) {
    var box = $('#panelNote');
    box.style.display = '';
    box.className = 'notice error';
    box.textContent = text;
  }

  /* 刻度步长取 1/2/5 × 10^k，让 8-10 条刻度刚好铺满 */
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

  function svgEl(name, attrs) {
    var node = document.createElementNS('http://www.w3.org/2000/svg', name);
    Object.keys(attrs || {}).forEach(function (key) { node.setAttribute(key, attrs[key]); });
    return node;
  }

  function buildFigureSvg(figure) {
    var box = figureBox();
    var W = box.w;
    var H = box.h;
    var svg = svgEl('svg', { viewBox: '0 0 ' + W + ' ' + H,
                             preserveAspectRatio: 'xMidYMid meet' });

    // 空间几何走 3D 渲染（正交投影 + 画家算法），不用二维那套坐标轴
    if (figure.kind === 'solid') {
      svg.__solidBox = { w: W, h: H };
      var solidGroup = svgEl('g', {});
      svg.appendChild(solidGroup);
      svg.__solidGroup = solidGroup;
      solidScene(svg, figure, solidGroup);
      return svg;
    }
    // 二维视图（view）已经随旧的函数/方程面板一起删掉了：平面作图全部归几何画板。
    // 这块面板只剩立体，所以这里**不再有任何视图状态**，直接用图自己带的坐标范围——
    // 留着下面的 `view` 引用会在老图回放时抛 ReferenceError。
    var xMin = Number(figure.x_min);
    var xMax = Number(figure.x_max);
    var yMin = Number(figure.y_min);
    var yMax = Number(figure.y_max);
    if (!(xMax > xMin)) { xMax = xMin + 1; }
    if (!(yMax > yMin)) { yMax = yMin + 1; }

    // 横轴纵轴**永远同一个比例**：老师明确要求过（不然圆画成椭圆、
    // 直角画成不是直角、切线和动点看着就"错位"）。范围由 view 保证等比，
    // 这里取一个共用的比例，两个方向都用它。
    var scale = Math.min((W - FIG_PAD * 2) / (xMax - xMin),
                         (H - FIG_PAD * 2) / (yMax - yMin));
    var scaleX = scale;
    var scaleY = scale;
    var offsetX = FIG_PAD + ((W - FIG_PAD * 2) - (xMax - xMin) * scaleX) / 2;
    var offsetY = FIG_PAD + ((H - FIG_PAD * 2) - (yMax - yMin) * scaleY) / 2;
    var toX = function (x) { return offsetX + (x - xMin) * scaleX; };
    var toY = function (y) { return H - offsetY - (y - yMin) * scaleY; };

    // 先把画图那一组建出来：它下面要挂进 __fig，而 __fig 又必须在 return svg 之前挂好
    // （顺序错一步就出毛病：挂在末尾 -> 后面的手势拿不到 __fig；用还没创建的 group ->
    // 平移/缩放一进来就 return。两个都真踩过。）
    // 现在这块面板只剩立体（上面就 return 了），这条路径只服务"会话里回放的老图"。
    var group = svgEl('g', {});
    svg.appendChild(group);

    // 换算函数挂到 svg 上：动点拖动、缩放锚点都要用它把鼠标位置换成数据坐标
    svg.__fig = { figure: figure, toX: toX, toY: toY, group: group,
                  w: W, h: H,
                  xMin: xMin, xMax: xMax, yMin: yMin, yMax: yMax,
                  scaleX: scaleX, scaleY: scaleY,
                  offsetX: offsetX, offsetY: offsetY };

    var stepX = niceStep(xMax - xMin, 10);
    var stepY = niceStep(yMax - yMin, 8);

    var index;
    var startX = Math.ceil(xMin / stepX) * stepX;
    for (index = startX; index <= xMax + 1e-9; index += stepX) {
      group.appendChild(svgEl('line', { class: 'fig-grid', x1: toX(index), y1: FIG_PAD,
                                        x2: toX(index), y2: H - FIG_PAD }));
      // 原点那个 0 由纵轴标（就在左边一点），这里别再写一个，否则原点看着像 "0 0"
      if (Math.abs(index) < 1e-9 && yMin <= 0 && yMax >= 0) continue;
      var labelX = svgEl('text', { class: 'fig-tick', x: toX(index), y: toY(0) + 20,
                                   'text-anchor': 'middle' });
      labelX.textContent = fmtTick(index, stepX);
      group.appendChild(labelX);
    }
    var startY = Math.ceil(yMin / stepY) * stepY;
    for (index = startY; index <= yMax + 1e-9; index += stepY) {
      group.appendChild(svgEl('line', { class: 'fig-grid', x1: FIG_PAD, y1: toY(index),
                                        x2: W - FIG_PAD, y2: toY(index) }));
      var labelY = svgEl('text', { class: 'fig-tick', x: toX(0) - 10, y: toY(index) + 5,
                                   'text-anchor': 'end' });
      labelY.textContent = fmtTick(index, stepY);
      group.appendChild(labelY);
    }
    // 坐标轴：0 在范围内才画，否则贴着下边/左边画一条边框
    var axisY = (yMin <= 0 && yMax >= 0) ? toY(0) : toY(yMin);
    var axisX = (xMin <= 0 && xMax >= 0) ? toX(0) : toX(xMin);
    group.appendChild(svgEl('line', { class: 'fig-axis', x1: FIG_PAD, y1: axisY,
                                      x2: W - FIG_PAD, y2: axisY }));
    group.appendChild(svgEl('line', { class: 'fig-axis', x1: axisX, y1: FIG_PAD,
                                      x2: axisX, y2: H - FIG_PAD }));

    // 平面图形的绘制分支（几何 / 曲线）已经删掉：这块面板只画立体。
    // 留着 segments 的循环是因为**旧的图还能从会话里回放**（figure 事件、快照里的图）。
    (figure.segments || []).forEach(function (segment) {
      var d = segment.map(function (point, position) {
        return (position ? 'L' : 'M') + toX(point[0]).toFixed(1) + ' ' + toY(point[1]).toFixed(1);
      }).join(' ');
      group.appendChild(svgEl('path', { class: 'fig-curve', d: d }));
    });
    if (figure.label) {
      var caption = svgEl('text', { class: 'fig-label', x: W - FIG_PAD,
                                    y: FIG_PAD + 6, 'text-anchor': 'end' });
      caption.textContent = figure.label;
      group.appendChild(caption);
    }
    return svg;
  }

  /* ------------------------------------------------ viewBox <-> 浏览器像素

     SVG 上有 preserveAspectRatio="xMidYMid meet"：容器比例和 viewBox 比例不一样时，
     内容会等比缩放并**居中留黑边**。所以换算不能直接用 rect.width / FIG_W ——
     那样会算出一个几十像素的偏移：明明按在动点上，程序却认为离得很远
     （抓不住动点、切线错位都是这么来的，真踩过）。
     这里把缩放和偏移都算出来，所有换算统一走这两个函数。 */
  function viewBoxMetrics(svg) {
    var rect = svg.getBoundingClientRect();
    var view = svg.viewBox.baseVal;
    var boxW = view && view.width ? view.width : FIG_W;
    var boxH = view && view.height ? view.height : FIG_H;
    var scale = Math.min(rect.width / boxW, rect.height / boxH);
    return { left: rect.left, top: rect.top, scale: scale,
             boxW: boxW, boxH: boxH,
             padX: (rect.width - boxW * scale) / 2,
             padY: (rect.height - boxH * scale) / 2 };
  }

  function viewBoxToClient(svg, vx, vy) {
    var m = viewBoxMetrics(svg);
    return { x: m.left + m.padX + vx * m.scale, y: m.top + m.padY + vy * m.scale };
  }

  function clientToViewBox(svg, clientX, clientY) {
    var m = viewBoxMetrics(svg);
    return { x: (clientX - m.left - m.padX) / (m.scale || 1),
             y: (clientY - m.top - m.padY) / (m.scale || 1) };
  }

  /* ------------------------------------------------------------ 空间几何（3D）

     没有 WebGL，也不需要：正交投影 + 画家算法排序，几十个面用 SVG 画完全够，
     而且和别的图形共用同一个面板、同一套交互。 */
  var solidView = { azimuth: -0.62, elevation: 0.42, zoom: 1 };

  /* 相机复位。注意别和 solidReset（工具状态复位）撞名：
     同名函数是后者胜出、不报错，撞了就会莫名其妙少一个功能。 */
  function solidResetCamera() {
    solidView.azimuth = -0.62;
    solidView.elevation = 0.42;
    solidView.zoom = 1;
  }

  /* 世界坐标 -> 屏幕（viewBox 坐标）。

     先绕 z 轴转方位角，再按仰角把"进深"压扁（正交投影，不搞透视，
     教学图看着更规矩）。返回的 depth 给画家算法排序用。 */
  function solidProject(point, center, radius, W, H) {
    var cosA = Math.cos(solidView.azimuth);
    var sinA = Math.sin(solidView.azimuth);
    var cosE = Math.cos(solidView.elevation);
    var sinE = Math.sin(solidView.elevation);
    var x = point[0] - center[0];
    var y = point[1] - center[1];
    var z = point[2] - center[2];
    var rx = x * cosA - y * sinA;
    var ry = x * sinA + y * cosA;
    var scale = Math.min(W, H) * 0.34 / (radius || 1) * solidView.zoom;
    return {
      x: W / 2 + rx * scale,
      y: H / 2 - (z * cosE - ry * sinE) * scale,
      depth: ry * cosE + z * sinE
    };
  }

  function appendLine(group, a, b, cls) {
    group.appendChild(svgEl('line', { class: cls,
      x1: a.x.toFixed(1), y1: a.y.toFixed(1), x2: b.x.toFixed(1), y2: b.y.toFixed(1) }));
  }

  /* 只重画三维那部分（不动 DOM 结构、不联网）。

     选中点、确定约束、切工具之后都要调它：不重画的话状态变了屏幕上毫无变化，
     老师就会觉得"点不中"（真被这么反馈过）。 */
  function refreshSolid() {
    var svg = currentFigureSvg();
    if (!svg || !svg.__solidGroup) return;
    solidScene(svg, figureState.current, svg.__solidGroup);
  }

  function solidScene(svg, figure, group) {
    while (group.firstChild) group.removeChild(group.firstChild);
    var W = svg.__solidBox.w;
    var H = svg.__solidBox.h;
    var box = figure.bbox || [0, 0, 0, 1, 1, 1];
    // 取景要把**原点**也框进来：坐标轴是过原点画的，原点跑到画面外就"对不上"了
    var lo = [Math.min(box[0], 0), Math.min(box[1], 0), Math.min(box[2], 0)];
    var hi = [Math.max(box[3], 0), Math.max(box[4], 0), Math.max(box[5], 0)];
    var center = [(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, (lo[2] + hi[2]) / 2];
    var radius = Math.max(
      Math.hypot(hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]) / 2, 0.5);
    svg.__solidCenter = center;
    svg.__solidRadius = radius;

    function project(point) {
      return solidProject(point, center, radius, W, H);
    }
    var at = {};
    (figure.points || []).forEach(function (point) {
      at[point.name] = [point.x, point.y, point.z];
    });

    // 参考网格 + 三条坐标轴。
    //
    // 三条轴一律**过原点**（以前是从包围盒中心画的，所以"坐标系和图形对不上"），
    // 网格画在 z = 0 这个水平面上，和老师在黑板上画的一样。
    var reach = Math.max(hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2], 2) * 0.75;
    var gridStep = niceStep(reach * 2, 8);
    var half = Math.ceil(reach / gridStep) * gridStep;
    [-1, 1].forEach(function (side) {
      for (var value = 0; value <= half + 1e-9; value += gridStep) {
        var offset = value * side;
        // 平行于 x 的线（y = offset）
        appendLine(group, project([-half, offset, 0]), project([half, offset, 0]), 'fig-grid3');
        if (Math.abs(offset) < 1e-9) continue;
        // 平行于 y 的线（x = offset）
        appendLine(group, project([offset, -half, 0]), project([offset, half, 0]), 'fig-grid3');
      }
    });
    var axisNames = ['x', 'y', 'z'];
    [0, 1, 2].forEach(function (axis) {
      var from = [0, 0, 0];
      var to = [0, 0, 0];
      from[axis] = -half;
      to[axis] = half;
      appendLine(group, project(from), project(to), 'fig-axis3');
      // 轴名：夹在画面里，别跑出去
      var tip = project(to);
      var labelX = Math.max(24, Math.min(W - 24, tip.x + 8));
      var labelY = Math.max(28, Math.min(H - 14, tip.y - 4));
      var label = svgEl('text', { class: 'fig-axis3-name',
                                  x: labelX.toFixed(1), y: labelY.toFixed(1) });
      label.textContent = axisNames[axis];
      group.appendChild(label);
      // 刻度数字：老师要能看出"这一格是多少"
      for (var value = -half; value <= half + 1e-9; value += gridStep) {
        if (Math.abs(value) < 1e-9) continue;
        var point = [0, 0, 0];
        point[axis] = value;
        var spot = project(point);
        if (spot.x < 8 || spot.x > W - 8 || spot.y < 12 || spot.y > H - 8) continue;
        var tick = svgEl('text', { class: 'fig-tick3',
                                   x: (spot.x + 5).toFixed(1), y: (spot.y + 4).toFixed(1) });
        tick.textContent = fmtTick(value, gridStep);
        group.appendChild(tick);
      }
    });

    // 面：画家算法，远的先画。半透明是老师要的"透明模型"效果。
    var painted = [];
    (figure.faces || []).forEach(function (face) {
      var coords = face.map(function (name) { return at[name]; }).filter(Boolean);
      if (coords.length < 3) return;
      var projected = coords.map(project);
      var depth = projected.reduce(function (sum, item) { return sum + item.depth; }, 0)
                  / projected.length;
      painted.push({ depth: depth, points: projected });
    });
    painted.sort(function (a, b) { return b.depth - a.depth; });
    painted.forEach(function (face) {
      group.appendChild(svgEl('polygon', {
        class: 'fig-face3',
        points: face.points.map(function (item) {
          return item.x.toFixed(1) + ',' + item.y.toFixed(1);
        }).join(' ')
      }));
    });

    // 选中的圆：用另一种颜色描粗，老师才知道选中了哪个
    (solidState.pickedCurves || []).forEach(function (label) {
      var curve = null;
      (figure.curves || []).forEach(function (item) {
        if (item.name === label) curve = item;
      });
      if (!curve) return;
      var ring = curve.ring || [];
      for (var index = 0; index < ring.length; index++) {
        var a = at[ring[index]];
        var b = at[ring[(index + 1) % ring.length]];
        if (a && b) appendLine(group, project(a), project(b), 'fig-picked-curve');
      }
    });

    // 正在选的那一步也要高亮：已经确定的组 + 当前正选着的点，
    // 按"两个点=一条线 / 三个点=一个面"实时画出来，老师才知道自己选到哪了。
    var armedNow = solidState.armed;
    if (armedNow) {
      var previews = armedNow.groups.slice();
      if (solidState.selected.length) previews.push(solidState.selected);
      previews.forEach(function (picked) {
        // at[name] 已经是 [x, y, z] 数组：以前按 point.x 取，全是 undefined，
        // 坐标算成 NaN，多边形退化成 0x0 —— 元素在、颜色对，就是看不见。
        var spots = picked.map(function (name) {
          var point = at[name];
          return point ? project(point) : null;
        });
        if (spots.some(function (item2) { return !item2; })) return;
        if (picked.length === 2) {
          appendLine(group, spots[0], spots[1], 'fig-angle-line');
        } else if (picked.length >= 3) {
          group.appendChild(svgEl('polygon', { class: 'fig-angle-face',
            points: spots.map(function (item2) {
              return item2.x.toFixed(1) + ',' + item2.y.toFixed(1);
            }).join(' ') }));
        }
      });
    }

    // 参与计算的角：把它用到的那条线 / 那个面用**另一种颜色**标出来
    // （半透明保留，不然挡住里面的棱）
    (figure.angles || []).forEach(function (item) {
      if (item.error || !item.names) return;
      var names = item.names;
      function spotOf(name) {
        var point = at[name];        // 这里也是 [x, y, z] 数组
        return point ? project(point) : null;
      }
      function markLine(first, second) {
        var a2 = spotOf(first);
        var b2 = spotOf(second);
        if (a2 && b2) appendLine(group, a2, b2, 'fig-angle-line');
      }
      function markFace(list) {
        var spots = list.map(spotOf);
        if (spots.some(function (item2) { return !item2; })) return;
        group.appendChild(svgEl('polygon', { class: 'fig-angle-face',
          points: spots.map(function (item2) {
            return item2.x.toFixed(1) + ',' + item2.y.toFixed(1);
          }).join(' ') }));
      }
      // 按服务端给的 groups 切：面可以整圈选（4 个点也常见），
      // 写死"前 3 个"会把两组混在一起（就是"两个面高亮不出来"的原因之一）
      var parts = item.groups && item.groups.length === 2
        ? item.groups
        : [names.slice(0, item.type === 'lineangle' ? 2 : 3),
           names.slice(item.type === 'lineangle' ? 2 : 3)];
      if (item.type === 'lineangle') {
        markLine(parts[0][0], parts[0][1]);
        markLine(parts[1][0], parts[1][1]);
      } else if (item.type === 'lineplane') {
        markLine(parts[0][0], parts[0][1]);
        markFace(parts[1]);
      } else if (item.type === 'planeangle') {
        markFace(parts[0]);
        markFace(parts[1]);
      }
    });

    // 侧面竖棱（圆柱的两圈之间、圆锥的底圆到顶点）不画：
    // 课本上的圆柱就是上下两个椭圆 + 半透明侧面，画上竖棱就成了笼子。
    // 只跳过属于同一个曲面体的那些，别的棱一条不动。
    var skipEdge = {};
    (figure.surfaces || []).forEach(function (surface) {
      if (surface.kind !== 'cylinder' && surface.kind !== 'cone') return;
      var rings = [];
      (surface.circles || []).forEach(function (label) {
        (figure.curves || []).forEach(function (curve) {
          if (curve.name === label) rings.push(curve.ring || []);
        });
      });
      if (surface.kind === 'cone') {
        rings.forEach(function (ring) {
          ring.forEach(function (name) {
            skipEdge[name + '|' + surface.apex] = true;
            skipEdge[surface.apex + '|' + name] = true;
          });
        });
      } else if (rings.length === 2) {
        rings[0].forEach(function (name) {
          var index = rings[0].indexOf(name);
          var other = rings[1][index];
          if (other) {
            skipEdge[name + '|' + other] = true;
            skipEdge[other + '|' + name] = true;
          }
        });
      }
    });

    // 棱
    (figure.edges || []).forEach(function (edge) {
      if (skipEdge[edge[0] + '|' + edge[1]]) return;
      var a = at[edge[0]];
      var b = at[edge[1]];
      if (!a || !b) return;
      var pa = project(a);
      var pb = project(b);
      group.appendChild(svgEl('line', { class: 'fig-edge3',
        x1: pa.x.toFixed(1), y1: pa.y.toFixed(1), x2: pb.x.toFixed(1), y2: pb.y.toFixed(1) }));
    });

    // 选中的那条线 / 那个面：画一层醒目的高亮，不然老师不知道选上没有
    var constraint = solidState.constraint;
    if (constraint && constraint.type === 'line' && constraint.line) {
      appendLine(group, project(constraint.line[0]), project(constraint.line[1]),
                 'fig-constraint');
    }
    if (constraint && constraint.type === 'face' && constraint.plane) {
      group.appendChild(svgEl('polygon', {
        class: 'fig-face-on',
        points: constraint.plane.map(function (item) {
          var spot = project(item);
          return spot.x.toFixed(1) + ',' + spot.y.toFixed(1);
        }).join(' ')
      }));
    }

    // 点和名字。
    // 三种状态要看得出来：普通、被选中的（白心）、以及**现在能拖的**（外面套个圈）。
    (figure.points || []).forEach(function (point) {
      if (point.hidden) return;      // 曲面逼近出来的顶点：不画点、不写名字
      var spot = project([point.x, point.y, point.z]);
      var chosen = solidState.selected.indexOf(point.name) >= 0;
      // 已经登记成"某张曲面上的动点"的点：外面画个绿圈，
      // 老师一眼看得出"设为圆上动点"那一步成没成
      var bound = null;
      (solidState.dynamicPoints || []).forEach(function (item) {
        if (item.name === point.name) bound = item;
      });
      if (bound) {
        group.appendChild(svgEl('circle', { class: 'fig-draggable',
          cx: spot.x.toFixed(1), cy: spot.y.toFixed(1), r: 10 }));
      }
      var draggable = constraint && pointOnConstraint(point, constraint, figure);
      if (draggable) {
        group.appendChild(svgEl('circle', { class: 'fig-draggable',
          cx: spot.x.toFixed(1), cy: spot.y.toFixed(1), r: 10 }));
      }
      group.appendChild(svgEl('circle', {
        class: chosen ? 'fig-point3 fig-point-on' : 'fig-point3',
        cx: spot.x.toFixed(1), cy: spot.y.toFixed(1), r: chosen ? 6.5 : 4.5 }));
      var name = svgEl('text', {
        class: chosen ? 'fig-label fig-label-on' : 'fig-label',
        x: (spot.x + 8).toFixed(1), y: (spot.y - 7).toFixed(1) });
      name.textContent = point.name;
      group.appendChild(name);
    });
  }

  /* 转动视角：鼠标拖 / 单指拖 = 转，滚轮 / 双指 = 缩放。
     和二维那边一样，拖动中不重建 SVG，改完 attributes 直接重画这一组。 */
  function setupSolidGestures(svg, figure) {
    var group = svg.__solidGroup;
    if (!group) return;
    var turning = false;
    var lastX = 0;
    var lastY = 0;
    var pinch = 0;

    svg.addEventListener('mousedown', function (event) {
      // 每次按下都重新开始记"这一下动了多少"。dragMoved 是**上一次**拖动累积下来的，
      // 不清掉的话后面每一次点击都会被当成"刚拖过"而丢掉（老师反馈"点屏幕选不中点了"）。
      solidState.afterDrag = false;
      solidState.dragMoved = 0;
      solidState.dragLastX = null;
      solidState.dragLastY = null;
      // 按在"能拖的点"上是要拖它，不能转视角
      // （以前这里无条件 turning=true，所以点根本拖不动、只会转视角）
      var pressed = solidPointUnder(svg, event);
      /* 两个工具分开（老师要求"沿线拖只能拖点"）：
         · 「拖棱」模式：**先抓棱**。棱的命中范围只有 14 像素、点有 30 像素，
           而且线段的端点附近几乎全被"点"吃掉 —— 不先抓棱就"根本选不中棱"（老师原话）；
         · 其它情况：「沿线拖点」只管点，按到点就是拖点（会沿着轨道滑），
           只有没按到点时才试棱。 */
      if (solidState.edgeMode) {
        if (solidBeginEdgeDrag(svg, event, figure)) return;
      }
      if (pressed && solidBeginDrag(pressed, figure)) return;
      if (!solidState.edgeMode && !pressed && solidBeginEdgeDrag(svg, event, figure)) return;
      event.preventDefault();          // 转视角时同样别选中文字
      turning = true;
      lastX = event.clientX;
      lastY = event.clientY;
      svg.style.cursor = 'grabbing';
    });
    svg.addEventListener('mousemove', function (event) {
      if (!turning) return;
      solidView.azimuth -= (event.clientX - lastX) * 0.012;
      solidView.elevation = Math.max(-1.45, Math.min(1.45,
        solidView.elevation + (event.clientY - lastY) * 0.012));
      lastX = event.clientX;
      lastY = event.clientY;
      solidScene(svg, figure, group);
    });
    window.addEventListener('mouseup', function () {
      turning = false;
      svg.style.cursor = '';
    });
    svg.addEventListener('wheel', function (event) {
      event.preventDefault();
      solidView.zoom = Math.max(0.2, Math.min(6,
        solidView.zoom * (event.deltaY > 0 ? 0.92 : 1.08)));
      solidScene(svg, figure, group);
    }, { passive: false });

    var touchStart = null;                 // 这一指是按在哪儿的（用来认"轻点"）
    var touchMoved = false;
    svg.addEventListener('touchstart', function (event) {
      if (event.touches.length === 2) {
        pinch = solidTouchDistance(event);
        touchMoved = true;
      } else if (event.touches.length === 1) {
        // 单指按在能拖的点上 = 拖那个点，不是转视角。
        // 触屏一体机上这里以前**无条件**转视角，所以"沿线拖 / 在面拖"在触屏上
        // 根本用不了（老师反馈"更不用说触屏"）。
        var first = event.touches[0];
        touchStart = { clientX: first.clientX, clientY: first.clientY };
        touchMoved = false;
        // 跟鼠标一样：每一指都重新开始记"动了多少"，别用上一指留下的数
        solidState.afterDrag = false;
        solidState.dragMoved = 0;
        solidState.dragLastX = null;
        solidState.dragLastY = null;
        var pressed = solidPointUnder(svg, first);
        // 「拖棱」模式先抓棱（端点附近几乎全被"点"吃掉，不先抓棱就选不中棱）
        if (solidState.edgeMode && solidBeginEdgeDrag(svg, first, figure)) {
          event.preventDefault();
          touchMoved = true;
          return;
        }
        if (pressed && solidBeginDrag(pressed, figure)) {
          event.preventDefault();
          touchMoved = true;               // 在拖点：这一下不许再当轻点
          return;
        }
        if (!solidState.edgeMode && !pressed && solidBeginEdgeDrag(svg, first, figure)) {
          event.preventDefault();
          touchMoved = true;
          return;
        }
        turning = true;
        lastX = first.clientX;
        lastY = first.clientY;
      }
    }, { passive: false });
    svg.addEventListener('touchmove', function (event) {
      if (solidState.dragging && event.touches.length === 1) {
        event.preventDefault();
        solidDragMove(event.touches[0]);     // 只用得到 clientX / clientY
        return;
      }
      if (event.touches.length === 2 && pinch > 0) {
        event.preventDefault();
        var distance = solidTouchDistance(event);
        if (distance > 0) {
          solidView.zoom = Math.max(0.2, Math.min(6, solidView.zoom * (distance / pinch)));
          pinch = distance;
          solidScene(svg, figure, group);
        }
      } else if (turning && event.touches.length === 1) {
        event.preventDefault();
        var touch = event.touches[0];
        // 动过 8 像素以上就不算"轻点"了（手指在触屏上几乎不可能一动不动）
        if (touchStart && (Math.abs(touch.clientX - touchStart.clientX) > 8
                           || Math.abs(touch.clientY - touchStart.clientY) > 8)) {
          touchMoved = true;
        }
        solidView.azimuth -= (touch.clientX - lastX) * 0.012;
        solidView.elevation = Math.max(-1.45, Math.min(1.45,
          solidView.elevation + (touch.clientY - lastY) * 0.012));
        lastX = touch.clientX;
        lastY = touch.clientY;
        solidScene(svg, figure, group);
      }
    }, { passive: false });
    window.addEventListener('touchend', function (event) {
      if (solidState.dragging) solidDragEnd();     // 手指抬起来 = 松手，把精确值算回来
      if (event.touches.length === 0) {
        // 手指抬起来了：没滑过就是"轻点"，自己认这一下。
        // 不能指望浏览器补的那个 click —— 触屏上手指稍微一晃就会走到上面的
        // preventDefault 分支，click 根本不发，选点就变成"时灵时不灵"。
        if (touchStart && !touchMoved) {
          solidState.touchPickedAt = Date.now();
          solidPickAt(svg, figure, touchStart);
        }
        touchStart = null;
        touchMoved = false;
        turning = false;
      }
      if (event.touches.length < 2) pinch = 0;
    });
  }

  function solidTouchDistance(event) {
    var dx = event.touches[0].clientX - event.touches[1].clientX;
    var dy = event.touches[0].clientY - event.touches[1].clientY;
    return Math.sqrt(dx * dx + dy * dy);
  }

  /* -------------------------------------------------- 空间几何的工具栏

     和平面那套对齐：选点（点一下选，右边看坐标）、按坐标加点、连线、画面、
     删除、撤销、清空。另外多了"约束拖动"：点只能沿着某条线或某个面滑动。 */
  // constraint: {type:'line'|'face', names:[...]} —— 选中的是「那条线/那个面」，
  // 线上任意一个点都能拖着走（定义点除外）；armed: 工具已点开、正在选点
  var solidState = { tool: 'select', shapes: [], selected: [], constraint: null,
                     armed: null, dragging: false, dragTimer: null,
                     snapDrag: null, snapX: null, snapY: null, snapZ: null,
                     // 最近一次提交给服务端的 shapes：回来的那张图跟它一样 = 是我们自己的回声，
                     // 不能拿它重置工具状态（否则拖动中一重画就断）
                     sentSource: null,
                     // 这次拖动要不要顺手把点绑到线/面上（不在线上也允许拖，落了就绑）
                     dragBind: null, dragMoved: 0, dragLastX: null, dragLastY: null,
                     // 拖整条棱（沿线拖线段 / 圆上的定长弦）：两个端点 + 按下时落在约束上的位置
                     dragEdge: null, edgeFrom: null, edgeHinted: false,
    // 「拖棱」模式：棱的命中范围比点窄，这个模式下先抓棱。切别的工具 / 「自由拖」会关掉。
    edgeMode: false,
                     afterDrag: false,
                     pickedCurves: [], pickedSurface: null,
                     // 动点挂在哪张曲面上：**不随工具切换丢失**
                     // （以前挂在 constraint 上，点一下别的工具就被清掉，
                     //   动点就再也拖不动了）
                     dynamicPoints: [] };

  function solidReset(shapes) {
    solidState.shapes = (shapes || []).slice();
    solidState.selected = [];
    solidState.constraint = null;
    solidState.tool = 'select';
    solidShowTools(true);
    solidHighlight();
    setSolidTool('select');
  }

  /* 高亮当前工具。老师点一下要看得见"现在是哪个工具"，
     不然会觉得按钮没反应（真被这么反馈过）。 */
  function solidHighlight() {
    var armedKind = solidState.armed ? solidState.armed.kind : null;
    var constraintKind = solidState.constraint ? solidState.constraint.type : null;
    var mapping = {
      solidFree: !armedKind && !constraintKind,
      solidAlongLine: armedKind === 'alignline',
    solidAlongEdge: armedKind === 'edgealong' || !!solidState.edgeMode,
      solidOnFace: armedKind === 'alignface' || constraintKind === 'face',
      solidLine: armedKind === 'segment',
      solidFace: armedKind === 'face',
      solidCircle3: armedKind === 'circle3',
      solidSphere: armedKind === 'sphereoa',
      solidAngleLL: armedKind === 'aa',
      solidAngleLP: armedKind === 'ap',
      solidAnglePP: armedKind === 'pp'
    };
    Object.keys(mapping).forEach(function (id) {
      var node = document.getElementById(id);
      if (node) node.classList.toggle('on', !!mapping[id]);
    });
  }

  function setSolidTool(key) {
    solidState.tool = key;
    solidState.selected = [];
    refreshSolid();
    var buttons = document.querySelectorAll('.solid-tool[data-solid]');
    for (var index = 0; index < buttons.length; index++) {
      buttons[index].classList.toggle('on', buttons[index].getAttribute('data-solid') === key);
    }
    solidHint();
  }

  function escapeRegExp(value) {
    return String(value).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  }

  /* 坐标/角度的显示统一保留 4 位小数，去掉尾巴上的 0：老师要看的是 3.5，不是 3.5000。 */
  function round4(value) {
    var rounded = Math.round(Number(value) * 10000) / 10000;
    if (!isFinite(rounded)) return '—';
    return String(rounded);
  }

  /* 当前作图面板里的那块 svg（拖动中的节流重画会换掉它，所以每次现查） */
  function currentFigureSvg() {
    return document.querySelector('#figureCanvas svg');
  }

  /* 拖动时的取值：只保留 1 位小数。
     **整数吸附不在这里做** —— 统一交给下面的 detentAxis，免得两套吸附打架
     （原来按世界坐标算的整数吸附，放大以后会莫名跳）。 */
  function dragValue(value) {
    return Math.round(value * 10) / 10;
  }

  /* 整数坐标"顿一下"（磁吸 + 迟滞）。平面和空间共用这一份。

     perUnit = 每个世界单位对应多少**屏幕像素**。阈值必须按像素算：
     按世界坐标算的话，视图一放大吸附范围就变成几十像素（点乱跳），
     一缩小又完全吸不住。

     进入吸附 6px、离开要 14px —— "顿一下"就是这个迟滞：
     没有它，指针停在整数附近时点会在整数和指针之间来回抖。
     刻意不做任何视觉提示（老师要求别太明显）。

     **这两个函数是共享件，删旧面板时差点被连带删掉**（真踩过）：
     空间几何的拖动和 plane.js 的平面拖动都用它们，见下面的 window.W7Snap。 */
  function detentAxis(prev, next, perUnit) {
    if (!(perUnit > 0)) return next;
    var enter = 6 / perUnit;
    var leave = 14 / perUnit;
    var target = Math.round(next);
    var held = (typeof prev === 'number') && Math.abs(prev - Math.round(prev)) < 1e-9;
    return Math.abs(next - target) <= (held ? leave : enter) ? target : next;
  }

  /* 供 plane.js（新的"平面"模块）复用：同一份手感，不要各写一套。 */
  window.W7Snap = { axis: detentAxis, value: dragValue };

  /* 下一个没被用过的点名 A、B、C…（空间几何按坐标加点要用） */
  function nextPointName(figure, extra) {
    var used = {};
    ((figure && figure.points) || []).forEach(function (point) { used[point.name] = true; });
    (extra || []).forEach(function (name) { used[name] = true; });
    var alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ';
    for (var index = 0; index < alphabet.length; index++) {
      if (!used[alphabet[index]]) return alphabet[index];
    }
    for (var suffix = 1; suffix < 100; suffix++) {
      for (var letter = 0; letter < alphabet.length; letter++) {
        var name = alphabet[letter] + suffix;
        if (!used[name]) return name;
      }
    }
    return 'P' + Date.now();
  }

  function solidShowTools(visible) {
    var items = document.querySelectorAll('.solid-tool');
    for (var index = 0; index < items.length; index++) {
      items[index].classList.toggle('hidden', !visible);
    }
    ['solidSep', 'solidPointBox', 'solidConstraintBox'].forEach(function (id) {
      var node = document.getElementById(id);
      if (node) node.classList.toggle('hidden', !visible);
    });
  }

  function solidHint(text) {
    var hint = document.getElementById('geoHint');
    if (!hint) return;
    if (text) {
      hint.textContent = text;
      hint.classList.remove('hidden');
      return;
    }
    var names = solidState.selected;
    if (solidState.constraint) {
      hint.textContent = solidState.constraint.type === 'line'
        ? '已选中线段 ' + solidState.constraint.names.join('')
        : '已选中平面 ' + solidState.constraint.names.join('');
    } else if (solidState.armed) {
      var spec = SOLID_ARMS[solidState.armed.kind];
      var need = spec.steps[solidState.armed.stage];
      hint.textContent = spec.label + '：第 ' + (solidState.armed.stage + 1) + ' 步，'
        + stepHintOf(spec, solidState.armed.stage)
        + '（已选 ' + (names.join('、') || '0 个') + ' / 要 ' + need + ' 个）';
    } else if (names.length) {
      hint.textContent = '已选 ' + names.join('、')
        + '（选一个点 + 两个点=沿直线拖，选一个点 + 三个点=在面上拖）';
    } else {
      hint.textContent = '点一下图上的点选中它，右边看坐标；拖动图面可以转视角';
    }
    hint.classList.remove('hidden');
  }

  /* 空间工具统一走"分步选点 + 确定"。

     每一步要选几个点写在 steps 里；两步的工具（三种角）第一步确定完
     会提示"再选第二步"，第二步确定完才出结果。 */
  var SOLID_ARMS = {
    segment: { label: '连线', steps: [2], build: function (groups) {
      return 'seg ' + groups[0].join(' ');
    } },
    face: { label: '画面', steps: [3], build: function (groups) {
      return 'face ' + groups[0].join(' ');
    } },
    circle3: { label: '画圆', steps: [3], build: function (groups) {
      return 'circle3 ' + groups[0].join(' ');
    }, stepHint: ['选三个点（不在同一条直线上）'] },
    sphereoa: { label: '画球', steps: [2], build: function (groups) {
      return 'sphere ' + groups[0][0] + ' ' + groups[0][1];
    }, stepHint: ['先点球心，再点球面上的一个点'] },
    alignline: { label: '沿线拖点', steps: [2], constraint: 'line',
      stepHint: ['点这条直线上的两个点当轨道',
                 '轨道选好了：拖点只能沿着它滑（拖棱请用旁边的「拖棱」）'] },
    /* 拖棱：**只管棱**，和「沿线拖点」彻底分开（老师要求："沿线拖只能拖点"）。
       点一条棱当轨道，然后按住别的棱拖 —— 整条棱沿着它滑，长度不变。
       真正的处理在 solidPickAt 里（选中轨道棱就立刻定下约束），这里只是占位让按钮亮起来。 */
    edgealong: { label: '拖棱', steps: [2, 2], build: function () { return ''; },
      stepHint: ['点**轨道直线**上的两个点（要沿着的方向）',
                 '再点要滑动的那条线段的两个端点'] },
    alignface: { label: '在面拖', steps: [3], constraint: 'face' },
    aa: { label: '线线角', steps: [2, 2],
      stepHint: ['先选第一条直线上的两个点', '再选第二条直线上的两个点'],
      build: function (groups) {
        return '线线角 ' + groups[0].join(' ') + ' | ' + groups[1].join(' ');
      } },
    ap: { label: '线面角', steps: [2, 3],
      stepHint: ['先选直线上的两个点', '再选平面上（不在同一直线上）的三个点'],
      build: function (groups) {
        return '线面角 ' + groups[0].join(' ') + ' | ' + groups[1].join(' ');
      } },
    pp: { label: '面面角', steps: [3, 3],
      stepHint: ['先选第一个平面上的三个点', '再选第二个平面上的三个点'],
      build: function (groups) {
        return '面面角 ' + groups[0].join(' ') + ' | ' + groups[1].join(' ');
      } }
  };

  function armSolid(kind) {
    var spec = SOLID_ARMS[kind];
    if (!spec) return;
    if (solidState.armed && solidState.armed.kind === kind) {
      solidState.armed = null;
      solidState.constraint = null;
      solidState.selected = [];
      solidHighlight();
      refreshSolid();
      solidHint('取消「' + spec.label + '」了');
      return;
    }
    // 保留已经选好的点：老师可能是"先点两个点再点工具"，一进来就清空
    // 他刚选的就被抹掉了（真被这么反馈过）。只有切换工具时才清。
    if (solidState.armed && solidState.armed.kind !== kind) solidState.selected = [];
    // 开始选点：把"点过圆的边"那个状态清掉 —— 留着它，后面每点一个点都只会替换成
    // 那一个点（老师反馈的"无论怎么点都只能选中一个点"就是这么来的，而且看不出来）。
    solidState.pickedCurves = [];
    solidState.pickedSurface = null;
    solidState.armed = { kind: kind, stage: 0, groups: [] };
    solidState.constraint = null;
    // 「拖棱」模式只在「拖棱」这个工具下成立；切到别的工具（尤其沿线拖点）就关掉，
    // 否则按到点也会先去抓棱，点了半天拖不动点（老师要求的"沿线拖只能拖点"）。
    solidState.edgeMode = (kind === 'edgealong');
    solidHighlight();
    refreshSolid();
    solidHint(spec.label + '：' + stepHintOf(spec, 0)
      + (solidState.selected.length
         ? '（已选 ' + solidState.selected.join('、') + '）' : ''));
  }

  function stepHintOf(spec, stage) {
    var need = spec.steps[stage];
    var extra = spec.stepHint ? spec.stepHint[stage] : '';
    return extra + (need <= 2 ? '（点 ' + need + ' 个，然后按「确定」）'
                              : '（点 ' + need + ' 个或更多，然后按「确定」）');
  }

  /* 「确定」：按当前进度推进。最后一步确定完才真的把东西加上去。 */
  function confirmConstraint() {
    var figure = figureState.current;
    var armed = solidState.armed;
    if (!figure || figure.kind !== 'solid' || !armed) {
      setFigureStatus('先点一个工具（连线 / 画面 / 沿线拖 / 三种角）', 'warn');
      return;
    }
    var spec = SOLID_ARMS[armed.kind];
    var need = spec.steps[armed.stage];
    var names = solidState.selected;
    // 两个点的那一步要正好两个（一条线就是两点确定的）；
    // 面的那一步 3 个起步，多选也行 —— 老师会把一个面整圈点下来
    var enough = need <= 2 ? names.length === need : names.length >= need;
    if (!enough) {
      setFigureStatus(spec.label + '第 ' + (armed.stage + 1) + ' 步'
        + (need <= 2 ? '要选 ' + need + ' 个点' : '至少要选 ' + need + ' 个点')
        + '（现在选了 ' + names.length + ' 个）', 'warn');
      return;
    }
    var at = {};
    (figure.points || []).forEach(function (point) { at[point.name] = point; });
    if (names.some(function (name) { return !at[name]; })) {
      setFigureStatus('有点找不到了，重新选一下', 'warn');
      return;
    }
    armed.groups.push(names.slice());
    solidState.selected = [];
    armed.stage += 1;

    if (armed.stage < spec.steps.length) {
      solidHint(spec.label + '：第 ' + (armed.stage + 1) + ' 步——'
        + stepHintOf(spec, armed.stage));
      setFigureStatus('第 ' + armed.stage + ' 步选好了：'
        + armed.groups[armed.groups.length - 1].join('、') + '，接着选下一步', 'done');
      refreshSolid();
      return;
    }

    if (armed.kind === 'edgealong') {
      /* 「拖棱」：第 1 步两个点定轨道方向，第 2 步两个点定要滑的那条线段。
         之后按住这两点里任意一个拖，**两个端点一起走同样的量**（长度不变）。
         老师要求的就是这个流程：选两个点 + 再选两个点，不用去点棱 */
      var track = armed.groups[0].map(function (name) { return at[name]; });
      var pair = armed.groups[1].map(function (name) { return at[name]; });
      solidState.constraint = {
        type: 'line', names: armed.groups[0].slice(),
        line: [[track[0].x, track[0].y, track[0].z], [track[1].x, track[1].y, track[1].z]],
        plane: null
      };
      solidState.slidePair = armed.groups[1].slice();
      solidState.edgeMode = true;
      solidState.armed = null;
      solidState.selected = [];
      solidHighlight();
      refreshSolid();
      solidHint('要滑的是 ' + armed.groups[1].join('') + '：按住 ' + armed.groups[1].join(' 或 ')
                + ' 拖动，它俩一起沿着 ' + armed.groups[0].join('') + ' 滑（长度不变）');
      setFigureStatus('按住 ' + armed.groups[1].join(' 或 ') + ' 拖动（右上「自由拖」可以解开）',
                      'done');
      return;
    }

    if (spec.constraint) {
      var coords = armed.groups[0].map(function (name) { return at[name]; });
      if (spec.constraint === 'face') {
        var e1 = [coords[1].x - coords[0].x, coords[1].y - coords[0].y,
                  coords[1].z - coords[0].z];
        var e2 = [coords[2].x - coords[0].x, coords[2].y - coords[0].y,
                  coords[2].z - coords[0].z];
        var cross = [e1[1] * e2[2] - e1[2] * e2[1], e1[2] * e2[0] - e1[0] * e2[2],
                     e1[0] * e2[1] - e1[1] * e2[0]];
        if (Math.hypot(cross[0], cross[1], cross[2]) < 1e-9) {
          setFigureStatus('这三个点共线，定不出平面', 'warn');
          solidState.armed = { kind: armed.kind, stage: 0, groups: [] };
          return;
        }
      }
      // 位置**冻结**在这里：以后拖线上任何点（含端点）时，线本身都不动
      solidState.constraint = {
        type: spec.constraint, names: armed.groups[0].slice(),
        line: spec.constraint === 'line'
          ? [[coords[0].x, coords[0].y, coords[0].z],
             [coords[1].x, coords[1].y, coords[1].z]] : null,
        plane: spec.constraint === 'face'
          ? [[coords[0].x, coords[0].y, coords[0].z],
             [coords[1].x, coords[1].y, coords[1].z],
             [coords[2].x, coords[2].y, coords[2].z]] : null
      };
      solidState.armed = null;
      solidHighlight();
      refreshSolid();
      solidHint(spec.constraint === 'line'
        ? '已选中线段 ' + armed.groups[0].join('')
        : '已选中平面 ' + armed.groups[0].join(''));
      setFigureStatus('长按这上面的任意一个点就能拖着走', 'done');
      return;
    }

    solidState.shapes.push(spec.build(armed.groups));
    solidState.armed = null;
    solidHighlight();
    solidHint(spec.label + '：算好了，看右边');
    solidApply();
  }

  /* 屏幕上的点 -> 三维里的位置：正交投影下是一条射线，
     用参数 u（旋转后的 y）表示。返回给定 u 时的世界坐标。 */
  function solidRayAt(svg, clientX, clientY, u) {
    var meta = svg.__solidBox;
    var center = svg.__solidCenter;
    var radius = svg.__solidRadius;
    var scale = Math.min(meta.w, meta.h) * 0.34 / (radius || 1) * solidView.zoom;
    var cosA = Math.cos(solidView.azimuth);
    var sinA = Math.sin(solidView.azimuth);
    var cosE = Math.cos(solidView.elevation);
    var sinE = Math.sin(solidView.elevation);
    var at = clientToViewBox(svg, clientX, clientY);
    var rx = (at.x - meta.w / 2) / scale;
    var k = (meta.h / 2 - at.y) / scale;          // = z cosE - u sinE
    var z = (k + u * sinE) / (cosE || 1e-6);
    return [center[0] + rx * cosA + u * sinA,
            center[1] - rx * sinA + u * cosA,
            center[2] + z];
  }

  /* 找出鼠标底下的空间点（投影之后按屏幕距离比） */
  function solidPointUnder(svg, event) {
    var figure = figureState.current;
    if (!figure || figure.kind !== 'solid') return null;
    var box = svg.__solidBox;
    var center = svg.__solidCenter;
    var radius = svg.__solidRadius;
    var best = null;
    var bestDistance = Infinity;
    (figure.points || []).forEach(function (point) {
      if (point.hidden) return;      // 隐藏顶点不给选：名字都看不见，选它没意义
      var spot = solidProject([point.x, point.y, point.z], center, radius, box.w, box.h);
      var at = viewBoxToClient(svg, spot.x, spot.y);
      var dx = event.clientX - at.x;
      var dy = event.clientY - at.y;
      var distance = dx * dx + dy * dy;
      // 名字画在点的右边一点点，按名字也算按在点上（不然很难点中）
      var labelDx = event.clientX - (at.x + 9);
      var labelDy = event.clientY - (at.y - 9);
      distance = Math.min(distance, labelDx * labelDx + labelDy * labelDy);
      // 动点滑到定点上（两个点投影到同一处）时，优先拿**能拖的那个**：
      // 老师这时候按下去要拖的是动点；选成定点就等于把定点搬走，
      // 而定点常常是圆的定义点，圆会跟着变（真机反馈过）。
      if (distance < bestDistance) { bestDistance = distance; best = point; }
      else if (best && distance <= bestDistance * 1.2
               && constraintOfPoint(point.name) && !constraintOfPoint(best.name)) {
        bestDistance = distance;
        best = point;
      }
    });
    // 26 像素：空间里点密集，范围太小很难选中（老师反馈"可能选不中"）
    return (best && bestDistance < 30 * 30) ? best : null;
  }

  function solidApply(wantExact) {
    if (!solidState.shapes.length) {
      onFigure({ kind: 'solid', title: '', note: '', points: [], edges: [], faces: [],
                 bbox: [0, 0, 0, 1, 1, 1], source: [], measures: {} }, true, true);
      solidHint('清空了，重新写几个空间点吧');
      return;
    }
    var body = { kind: 'solid', shapes: solidState.shapes.slice(),
                 title: (figureState.current && figureState.current.title) || '' };
    // 记下"这次提交的是什么"：服务端把同样的 source 送回来时，onFigure 才知道
    // 那是我们自己引起的重画，不该重置工具 / 约束 / 选中。
    solidState.sentSource = body.shapes.join("\n");
    // 拖动过程中只要小数：坐标一直在变，算分数根号又慢又没意义；
    // 松手那一下再要一次精确值。
    if (wantExact === false) body.exact = false;
    solidState.wantExact = wantExact !== false;
    // 拖动中的请求和松手时的请求是并发的，回来的顺序不保证：
    // 晚到的"只有小数"会把精确值盖掉（老师反馈的"拖完不回到分数根号"就是这个）。
    // 发个序号，只认最新那一次的响应。
    solidState.applySeq = (solidState.applySeq || 0) + 1;
    var seq = solidState.applySeq;
    api('/api/figure', { method: 'POST', body: body }).then(function (res) {
      if (seq !== solidState.applySeq) return;      // 有更新的请求了，这次的结果作废
      solidState.gotExact = wantExact !== false;
      if (res.figure) onFigure(res.figure, true, true);
      // 该精确的时候拿到的是近似值（拖动那次晚到了、或者被别的覆盖了），
      // 再要一次。这样不管请求回来的顺序怎样，最后一定收敛到分数根号。
      if (solidState.wantExact && !solidState.gotExact) solidApply(true);
    }).catch(function (error) {
      if (seq !== solidState.applySeq) return;
      setFigureStatus('这一步画不出来：' + error.message, 'warn');
    });
  }

  function solidAddPoint() {
    var rawX = (document.getElementById('solidX').value || '').trim();
    var rawY = (document.getElementById('solidY').value || '').trim();
    var rawZ = (document.getElementById('solidZ').value || '').trim();
    // 三个都要填：空字符串 Number('') 是 0，会把点悄悄放到原点（老师反馈过）
    if (!rawX || !rawY || !rawZ) {
      setFigureStatus('x、y、z 三个都要填（不填会被当成 0，点就跑到原点了）', 'warn');
      return;
    }
    var x = Number(rawX);
    var y = Number(rawY);
    var z = Number(rawZ);
    if (!isFinite(x) || !isFinite(y) || !isFinite(z)) {
      setFigureStatus('x、y、z 都要填数字', 'warn');
      return;
    }
    var figure = figureState.current || { points: [] };
    var name = nextPointName(figure, []);
    solidState.shapes.push(name + '(' + x + ', ' + y + ', ' + z + ')');
    ['solidX', 'solidY', 'solidZ'].forEach(function (id) {
      document.getElementById(id).value = '';
    });
    solidHint('加了点 ' + name + '(' + x + ', ' + y + ', ' + z + ')');
    solidApply();
  }

  /* 这个点在不在选中的线 / 面上（决定能不能被拖）。

     用**冻结**下来的那条线/那个面判断：拖端点时线本身不动，所以端点也能拖。 */
  function pointOnConstraint(point, constraint, figure) {
    // 曲面上的动点：这个动点是"挂在"那张曲面上的，拖它就是拖它自己。
    // 以前这里只认 line/plane，circle/sphere/cylinder/cone 一律返回 false，
    // 结果按在动点上永远进不了拖动状态（老师反馈"完全拖不动"）。
    if (constraint.type !== 'line' && constraint.type !== 'face') {
      // 这两种写法都得认：动点表里存的是 name，工具约束里存的是 point。
      // 只认 point 的话，动点表那条永远是 undefined，动点就拖不动了。
      return point.name === (constraint.point || constraint.name);
    }
    var p = [point.x, point.y, point.z];
    var span = Math.max(
      Math.hypot(figure.bbox[3] - figure.bbox[0], figure.bbox[4] - figure.bbox[1],
                 figure.bbox[5] - figure.bbox[2]), 1);
    // 容差跟曲面那套一致（2e-3）：拖动中坐标只保留两位小数，
    // 吸到线上之后的点会带最多 0.005 的偏差，按 1e-4 判它就"又不在线上了"，
    // 下一次按下去拖不动（老师反馈的"沿线拖很难拖"）。
    var tolerance = span * 2e-3;
    if (constraint.type === 'line') {
      var line = constraint.line;
      if (!line) return false;
      var d = [line[1][0] - line[0][0], line[1][1] - line[0][1], line[1][2] - line[0][2]];
      var length = Math.hypot(d[0], d[1], d[2]);
      if (length < 1e-9) return false;
      var o = [p[0] - line[0][0], p[1] - line[0][1], p[2] - line[0][2]];
      var n = [o[1] * d[2] - o[2] * d[1], o[2] * d[0] - o[0] * d[2],
               o[0] * d[1] - o[1] * d[0]];
      return Math.hypot(n[0], n[1], n[2]) / length <= tolerance;
    }
    var plane = constraint.plane;
    if (!plane) return false;
    var e1 = [plane[1][0] - plane[0][0], plane[1][1] - plane[0][1], plane[1][2] - plane[0][2]];
    var e2 = [plane[2][0] - plane[0][0], plane[2][1] - plane[0][1], plane[2][2] - plane[0][2]];
    var normal = [e1[1] * e2[2] - e1[2] * e2[1], e1[2] * e2[0] - e1[0] * e2[2],
                  e1[0] * e2[1] - e1[1] * e2[0]];
    var norm = Math.hypot(normal[0], normal[1], normal[2]);
    if (norm < 1e-9) return false;
    var offset = [p[0] - plane[0][0], p[1] - plane[0][1], p[2] - plane[0][2]];
    return Math.abs(offset[0] * normal[0] + offset[1] * normal[1]
                    + offset[2] * normal[2]) / norm <= tolerance;
  }

  /* 把 shapes 里那个点定义改到新坐标 */
  /* 立体视图里"1 个世界单位 ≈ 多少屏幕像素"。
     直接量：把原点和 (1,0,0) 各投一次影，量屏幕上差多少像素。
     沿哪根轴量差别不大（正交投影各轴尺度接近），够这个"顿一下"用了。 */
  function solidPixelsPerUnit(svg) {
    if (!svg || !svg.__solidBox || !svg.__solidCenter) return 0;
    var box = svg.__solidBox;
    var a = solidProject([0, 0, 0], svg.__solidCenter, svg.__solidRadius, box.w, box.h);
    var b = solidProject([1, 0, 0], svg.__solidCenter, svg.__solidRadius, box.w, box.h);
    var ca = viewBoxToClient(svg, a.x, a.y);
    var cb = viewBoxToClient(svg, b.x, b.y);
    var dx = cb.x - ca.x;
    var dy = cb.y - ca.y;
    var distance = Math.sqrt(dx * dx + dy * dy);
    return distance > 0 ? distance : 0;
  }

  function solidMovePoint(name, x, y, z) {
    var pattern = new RegExp('^' + escapeRegExp(name) + '\\s*\\(');
    for (var index = 0; index < solidState.shapes.length; index++) {
      if (pattern.test(solidState.shapes[index].trim())) {
        solidState.shapes[index] = name + '(' + x + ', ' + y + ', ' + z + ')';
        return true;
      }
    }
    // 曲面上的动点不是用 A(x,y,z) 定义的，而是 oncircle/onsurface/onsphere。
    // 以前这里直接放弃 -> 动点拖不动。改成把那条 on* 行就地改写成普通点：
    // 位置是 surfaceHit 在曲面上解出来的，所以它照样贴着曲面走。
    var onPattern = new RegExp('^\\s*(oncircle|onsurface|onsphere)\\s+'
                               + escapeRegExp(name) + '\\s');
    for (var other = 0; other < solidState.shapes.length; other++) {
      if (onPattern.test(solidState.shapes[other])) {
        solidState.shapes[other] = name + '(' + x + ', ' + y + ', ' + z + ')';
        return true;
      }
    }
    return false;
  }

  /* 拖动中被约束的点：把鼠标位置投影到线 / 面上。

     线：把冻结的两个端点投到屏幕，再把鼠标投到这条线段上；
     面：解射线与平面的交点（正交投影下射线好解）。 */
  /* 按下 / 触摸一个点：这次到底能不能拖？鼠标和触屏共用这一个判断。
     返回 true 表示已经进入拖动状态。

     和平面那边一个手感：**不要求点先落在线上**。老师选了「沿线拖 / 在面拖」再拖一个
     不在线上的点，第一次移动就把它吸到这条线 / 这个面上，并且顺手绑上去
     （跟「圆上动点」同一张表），之后拖它就一直沿着这条线 / 这个面滑。
     只有"这条线 / 这个面自己的定义点"不给拖 —— 拖它等于把线本身搬走。 */
  function solidBeginDrag(point, figure) {
    /* 「拖棱」：按住那两个端点里的任意一个，**两个一起沿着轨道滑**（刚体平移、长度不变）。
       这条判断放在最前面：它和"轨道自己的定义点不给拖"是两回事。 */
    var pair = solidState.slidePair || [];
    if (pair.length === 2 && pair.indexOf(point.name) >= 0) {
      var constraint0 = solidState.constraint;
      if (constraint0 && constraint0.type === 'line') {
        var pairPoints = [];
        (figure.points || []).forEach(function (item) {
          if (pair.indexOf(item.name) >= 0) { pairPoints.push(item); }
        });
        if (pairPoints.length === 2) {
          solidState.dragEdge = pairPoints;
          solidState.edgeFrom = null;
          solidState.dragPoint = null;
          solidState.dragging = true;
          solidState.dragMoved = 0;
          solidState.dragLastX = null;
          solidState.dragLastY = null;
          solidHint('拖着 ' + pair.join(' 和 ') + ' 走：两个端点一起沿着 '
                    + (constraint0.names || []).join('') + ' 滑，长度不变');
          return true;
        }
      }
    }
    var constraint = constraintOfPoint(point.name) || solidState.constraint;
    if (!constraint) return false;
    if (constraint.type !== 'line' && constraint.type !== 'face') {
      // 圆上 / 侧面上 / 球面上的动点：这条约束是**属于某一个点**的。
      // 按在别的点上绝不能拿它来拖 —— 否则"动点滑到定点上"的时候会把那个**定点**
      // 拖走（它有可能是圆的定义点，圆就跟着一起变，老师反馈的"圆不断变化"就是这个）。
      // 注意：对上了要**继续往下走**去起拖，不能直接 return true —— 那等于"说了能拖
      // 却没进入拖动状态"，动点就再也拖不动了（真踩过）。
      if (point.name !== (constraint.point || constraint.name)) return false;
    } else if ((constraint.names || []).indexOf(point.name) >= 0) {
      // 按的是轨道**自己的定义点**：拖它会把轨道本身搬走，所以这里不拖点。
      // 但**不能就这么算了** —— 调用方会接着试"按棱"，贴着线段端点按下去
      // 就能拖动整条棱（老师要的就是这个）。所以这里不再弹那句让人摸不着头脑的提示。
      return false;
    }
    solidState.dragPoint = point.name;
    solidState.dragging = true;
    solidState.dragMoved = 0;
    solidState.dragLastX = null;
    solidState.dragLastY = null;
    solidState.dragBind = pointOnConstraint(point, constraint, figure) ? null : constraint;
    return true;
  }

  /* 按在一条**棱**上（不是端点）：整条棱沿着当前约束滑 ——
     这就是老师要的"沿线拖动线段"，在圆上就是"定长弦"。
     返回 true 表示已经进入拖动状态。 */
  function solidBeginEdgeDrag(svg, event, figure) {
    var constraint = solidState.constraint;
    if (!constraint) {
      // 没选轨道时按棱 = 转视角（这是对的），但老师会以为"这里没有沿线拖线段"。
      // 第一次碰到就点一句怎么用 —— 只提示一次，不刷屏。
      if (!solidState.edgeHinted) {
        solidState.edgeHinted = true;
        solidHint('想整条棱沿线滑：先点「沿线拖」，再点那条直线上的两个点当轨道，然后按住棱拖');
      }
      return false;
    }
    var edge = solidEdgeUnder(svg, event, figure);
    if (!edge) return false;
    if ((constraint.names || []).indexOf(edge[0].name) >= 0
        || (constraint.names || []).indexOf(edge[1].name) >= 0) {
      setFigureStatus('这条棱是轨道自己的定义边，拖它会把轨道一起搬走', 'warn');
      return false;
    }
    solidState.dragEdge = edge;
    solidState.edgeFrom = null;                       // 第一帧只记起点，不跳
    solidState.dragPoint = null;
    solidState.dragging = true;
    solidState.dragMoved = 0;
    solidState.dragLastX = null;
    solidState.dragLastY = null;
    solidHint('拖着这条棱走：它整体沿着轨道滑，长度不变');
    return true;
  }

  /* 把这个点绑到这条线 / 这个面上：以后不用再点工具，拖它就沿着它滑（跟圆上动点一样）。 */
  function bindToConstraint(name, constraint) {
    if (!constraint || (constraint.type !== 'line' && constraint.type !== 'face')) return;
    solidState.dynamicPoints = (solidState.dynamicPoints || []).filter(function (item) {
      return item.name !== name;
    });
    solidState.dynamicPoints.push({ name: name, point: name, type: constraint.type,
                                    names: (constraint.names || []).slice(),
                                    line: constraint.line, plane: constraint.plane });
    solidHint(name + ' 已经能沿着这条' +
      (constraint.type === 'line' ? '线段' : '平面') + '滑了（右上「自由拖」可以解开）');
  }

  function solidDragMove(event) {
    var figure = figureState.current;
    if (!figure || figure.kind !== 'solid') return;
    // 拖整条棱时没有"被拖的那一个点"，但有 dragEdge
    if (!solidState.dragPoint && !solidState.dragEdge) return;
    // 先看这个点是不是"挂在曲面上的动点"，再看当前工具的临时约束
    var constraint = (solidState.dragPoint ? constraintOfPoint(solidState.dragPoint) : null)
                     || solidState.constraint;
    if (!constraint) return;
    var svg = currentFigureSvg();
    if (!svg || !svg.__solidBox) return;
    var at = {};
    (figure.points || []).forEach(function (point) { at[point.name] = point; });
    var moving = at[solidState.dragPoint];
    // 拖整条棱时 dragPoint 是空的：这时候不需要"被拖的那一个点"，两个端点由 dragEdge 带。
    // 以前这里无条件 `if (!moving) return;`，于是棱的拖动**一帧都走不到**下面那段
    // （拖是起来了、提示也出来了，就是不动 —— 老师报的"拖不了棱"就是这个）。
    var edgeDrag = !!(solidState.dragEdge && solidState.dragEdge.length === 2);
    if (!moving && !edgeDrag) return;
    var spot = null;
    if (constraint.type === 'line') {
      var pa = { x: constraint.line[0][0], y: constraint.line[0][1], z: constraint.line[0][2] };
      var pb = { x: constraint.line[1][0], y: constraint.line[1][1], z: constraint.line[1][2] };
      var a2 = solidProject([pa.x, pa.y, pa.z], svg.__solidCenter, svg.__solidRadius,
                            svg.__solidBox.w, svg.__solidBox.h);
      var b2 = solidProject([pb.x, pb.y, pb.z], svg.__solidCenter, svg.__solidRadius,
                            svg.__solidBox.w, svg.__solidBox.h);
      var clientA = viewBoxToClient(svg, a2.x, a2.y);
      var clientB = viewBoxToClient(svg, b2.x, b2.y);
      var dx = clientB.x - clientA.x;
      var dy = clientB.y - clientA.y;
      var lengthSquared = dx * dx + dy * dy;
      if (lengthSquared < 1e-9) return;
      var ratio = Math.max(0, Math.min(1,
        ((event.clientX - clientA.x) * dx + (event.clientY - clientA.y) * dy) / lengthSquared));
      spot = { x: pa.x + (pb.x - pa.x) * ratio,
               y: pa.y + (pb.y - pa.y) * ratio,
               z: pa.z + (pb.z - pa.z) * ratio };
    } else if (constraint.type === 'circle' || constraint.type === 'sphere'
               || constraint.type === 'cylinder' || constraint.type === 'cone') {
      var found = surfaceHit(svg, figure, constraint, event);
      if (!found) return;
      spot = { x: found[0], y: found[1], z: found[2] };
    } else {
      var plane = constraint.plane;
      if (!plane) return;
      var e1 = [plane[1][0] - plane[0][0], plane[1][1] - plane[0][1], plane[1][2] - plane[0][2]];
      var e2 = [plane[2][0] - plane[0][0], plane[2][1] - plane[0][1], plane[2][2] - plane[0][2]];
      var normal = [e1[1] * e2[2] - e1[2] * e2[1], e1[2] * e2[0] - e1[0] * e2[2],
                    e1[0] * e2[1] - e1[1] * e2[0]];
      var p0 = solidRayAt(svg, event.clientX, event.clientY, 0);
      var p1 = solidRayAt(svg, event.clientX, event.clientY, 1);
      var f0 = (p0[0] - plane[0][0]) * normal[0] + (p0[1] - plane[0][1]) * normal[1]
             + (p0[2] - plane[0][2]) * normal[2];
      var f1 = (p1[0] - plane[0][0]) * normal[0] + (p1[1] - plane[0][1]) * normal[1]
             + (p1[2] - plane[0][2]) * normal[2];
      if (Math.abs(f1 - f0) < 1e-12) return;
      var solved = solidRayAt(svg, event.clientX, event.clientY, -f0 / (f1 - f0));
      spot = { x: solved[0], y: solved[1], z: solved[2] };
    }
    if (!spot) return;
    // 记一下"到底动了没有"：松手之后紧跟的那个 click 事件要按这个判断，
    // 是拖动就不能再当成"点选"（否则拖完点被选/取消选，看着像"一点就取消"）。
    if (solidState.dragLastX !== null) {
      solidState.dragMoved += Math.abs(event.clientX - solidState.dragLastX)
                            + Math.abs(event.clientY - solidState.dragLastY);
    }
    solidState.dragLastX = event.clientX;
    solidState.dragLastY = event.clientY;
    // 拖的是**整条棱**：两个端点一起走同样的量（见 solidEdgeShift），所以棱长不变 ——
    // 在圆上就是老师要的"定长弦"（两个端点同加一个角度）。
    if (solidState.dragEdge && solidState.dragEdge.length === 2) {      // 第一帧只记"按下时落在这条线 / 这个面上的哪一点"，Δ 从 0 开始 ——
      // 不然一按下去整条棱会跳一下（跟鼠标位移无关的那一截）。
      if (!solidState.edgeFrom) { solidState.edgeFrom = spot; return; }
      var shifted = solidEdgeShift(solidState.dragEdge, constraint, spot, solidState.edgeFrom);
      if (!shifted) return;
      var moved = true;
      shifted.forEach(function (item) {
        if (!solidMovePoint(item.name, item.x, item.y, item.z)) moved = false;
      });
      if (!moved) return;
      // 乐观更新：先把本地这张图上的端点挪过去重画一遍，棱立刻跟着手走；
      // 服务端那份回来的权威值会覆盖它（solidApply 每 70ms 发一次）。
      shifted.forEach(function (item) {
        (figure.points || []).forEach(function (point) {
          if (point.name !== item.name) return;
          point.x = item.x;
          point.y = item.y;
          point.z = item.z;
        });
      });
      refreshSolid();
      if (solidState.dragTimer) return;
      solidState.dragTimer = setTimeout(function () {
        solidState.dragTimer = null;
        solidApply(false);
      }, 70);
      return;
    }
    // 空间里也要在整数坐标"顿一下"，跟平面保持一致（原来这里只四舍五入到两位小数）
    var perUnit = solidPixelsPerUnit(svg);
    if (solidState.snapDrag !== solidState.dragPoint) {
      solidState.snapDrag = solidState.dragPoint;
      solidState.snapX = null;
      solidState.snapY = null;
      solidState.snapZ = null;
    }
    var sx = detentAxis(solidState.snapX, spot.x, perUnit);
    var sy = detentAxis(solidState.snapY, spot.y, perUnit);
    var sz = detentAxis(solidState.snapZ, spot.z, perUnit);
    solidState.snapX = sx;
    solidState.snapY = sy;
    solidState.snapZ = sz;
    var rounded = [Math.round(sx * 100) / 100, Math.round(sy * 100) / 100,
                   Math.round(sz * 100) / 100];
    if (!solidMovePoint(solidState.dragPoint, rounded[0], rounded[1], rounded[2])) return;
    if (solidState.dragBind) {
      // 真的落在这条线 / 这个面上了 -> 绑住它，松手以后照样沿着它滑
      bindToConstraint(solidState.dragPoint, solidState.dragBind);
      solidState.dragBind = null;
    }
    if (solidState.dragTimer) return;
    solidState.dragTimer = setTimeout(function () {
      solidState.dragTimer = null;
      solidApply(false);          // 拖动中只给小数
    }, 70);
  }

  /* 一条棱整体沿约束滑动：两个端点**用同一个 Δ**，所以棱长一点不变。

       · 直线约束：Δ = 沿线的比例增量（端点在 AB 上的投影参数）
       · 平面约束：Δ = 面内平移量（两个端点减同一个向量）
       · 圆 / 球 / 柱 / 锥上的弦：Δ = 绕轴的角度增量 —— 这才是"定长弦"：
         两个端点绕同一根轴转同一个角度，半径不变、夹角不变，弦长当然不变。
     spot 是鼠标现在落在约束上的位置，from 是按下时那一个，两者算法一致（solidDragMove 里算）。 */
  function solidEdgeShift(edge, constraint, spot, from) {
    if (!edge || edge.length !== 2 || !spot || !from || !constraint) return null;
    var index;
    function along(base, direction, lengthSquared, point) {
      return ((point.x - base[0]) * direction[0] + (point.y - base[1]) * direction[1]
              + (point.z - base[2]) * direction[2]) / lengthSquared;
    }
    var out = [];
    if (constraint.type === 'line') {
      var line = constraint.line;
      if (!line) return null;
      var d = [line[1][0] - line[0][0], line[1][1] - line[0][1], line[1][2] - line[0][2]];
      var lengthSquared = d[0] * d[0] + d[1] * d[1] + d[2] * d[2];
      if (!(lengthSquared > 0)) return null;
      var delta = along(line[0], d, lengthSquared, spot)
                - along(line[0], d, lengthSquared, from);
      for (index = 0; index < 2; index++) {
        var ratio = along(line[0], d, lengthSquared, edge[index]) + delta;
        out.push({ name: edge[index].name, x: line[0][0] + d[0] * ratio,
                   y: line[0][1] + d[1] * ratio, z: line[0][2] + d[2] * ratio });
      }
      return out;
    }
    if (constraint.type === 'face') {
      var shift = [spot.x - from.x, spot.y - from.y, spot.z - from.z];
      for (index = 0; index < 2; index++) {
        out.push({ name: edge[index].name, x: edge[index].x + shift[0],
                   y: edge[index].y + shift[1], z: edge[index].z + shift[2] });
      }
      return out;
    }
    // 圆 / 球 / 柱 / 锥：绕轴转一个角度
    var axis = solidAxisOf(constraint);
    if (!axis) return null;
    var unit = axis.unit;
    function cross(a, b) {
      return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
    }
    function dot(a, b) { return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]; }
    function radial(point) {
      var v = [point.x - axis.center[0], point.y - axis.center[1], point.z - axis.center[2]];
      var up = dot(v, unit);
      return [v[0] - up * unit[0], v[1] - up * unit[1], v[2] - up * unit[2]];
    }
    var v0 = radial(from);
    var v1 = radial(spot);
    if (Math.hypot(v0[0], v0[1], v0[2]) < 1e-9
        || Math.hypot(v1[0], v1[1], v1[2]) < 1e-9) return null;
    var turn = Math.atan2(dot(cross(v0, v1), unit), dot(v0, v1));
    if (!isFinite(turn)) return null;
    var cos = Math.cos(turn);
    var sin = Math.sin(turn);
    for (index = 0; index < 2; index++) {
      var point = edge[index];
      var v = [point.x - axis.center[0], point.y - axis.center[1], point.z - axis.center[2]];
      var keep = dot(v, unit);
      var side = [v[0] - keep * unit[0], v[1] - keep * unit[1], v[2] - keep * unit[2]];
      var turned = cross(unit, side);
      out.push({
        name: point.name,
        x: axis.center[0] + keep * unit[0] + side[0] * cos + turned[0] * sin,
        y: axis.center[1] + keep * unit[1] + side[1] * cos + turned[1] * sin,
        z: axis.center[2] + keep * unit[2] + side[2] * cos + turned[2] * sin
      });
    }
    return out;
  }

  /* 约束对应的旋转轴（圆心 + 轴向），从当前这张图里找。找不到就返回 null（宁可不转）。 */
  function solidAxisOf(constraint) {
    var figure = figureState.current;
    if (!figure) return null;
    var axis = null;
    (figure.curves || []).forEach(function (curve) {
      if (curve.name === constraint.target && curve.center && curve.axis) axis = curve;
    });
    if (axis) {
      var norm = Math.hypot(axis.axis[0], axis.axis[1], axis.axis[2]) || 1;
      return { center: axis.center,
               unit: [axis.axis[0] / norm, axis.axis[1] / norm, axis.axis[2] / norm] };
    }
    var center = null;
    (figure.surfaces || []).forEach(function (surface) {
      if (surface.kind === 'sphere' && surface.at === constraint.target) center = surface.center;
    });
    if (!center) return null;
    // 球：绕哪根轴转都行，取 z 轴（弦长只跟夹角有关，跟轴无关）
    return { center: center, unit: [0, 0, 1] };
  }

  /* 鼠标底下是不是一条棱 —— **按棱的中点离鼠标最近**来挑（老师定的就近原则）。

     不用"点到棱的垂距"：两条棱挨在一起、或者按在共用的顶点附近时，垂距差不多，
     挑中的常常不是老师想拖的那条。候选范围：按在棱身上够近，或者按在中点附近够近
     （后者是为了抓短棱时好按），然后按中点距离排序取最近的那条。 */
  function solidEdgeUnder(svg, event, figure) {
    var at = {};
    (figure.points || []).forEach(function (point) { at[point.name] = point; });
    var best = null;
    var bestMid = Infinity;
    var bodyLimit = 14 * 14;
    var midLimit = 26 * 26;
    var box = svg.__solidBox;
    (figure.edges || []).forEach(function (edge) {
      var a = at[edge[0]];
      var b = at[edge[1]];
      if (!a || !b) return;
      var pa = solidProject([a.x, a.y, a.z], svg.__solidCenter, svg.__solidRadius, box.w, box.h);
      var pb = solidProject([b.x, b.y, b.z], svg.__solidCenter, svg.__solidRadius, box.w, box.h);
      var ca = viewBoxToClient(svg, pa.x, pa.y);
      var cb = viewBoxToClient(svg, pb.x, pb.y);
      var dx = cb.x - ca.x;
      var dy = cb.y - ca.y;
      var lengthSquared = dx * dx + dy * dy;
      if (lengthSquared < 1e-6) return;
      var ratio = Math.max(0, Math.min(1,
        ((event.clientX - ca.x) * dx + (event.clientY - ca.y) * dy) / lengthSquared));
      var ox = event.clientX - (ca.x + dx * ratio);
      var oy = event.clientY - (ca.y + dy * ratio);
      var bodyDistance = ox * ox + oy * oy;
      // 中点：投影之后两端的正中
      var mx = (ca.x + cb.x) / 2;
      var my = (ca.y + cb.y) / 2;
      var midDistance = (event.clientX - mx) * (event.clientX - mx)
                      + (event.clientY - my) * (event.clientY - my);
      if (bodyDistance > bodyLimit && midDistance > midLimit) return;
      if (midDistance < bestMid) {
        bestMid = midDistance;
        best = [{ name: a.name, x: a.x, y: a.y, z: a.z },
                { name: b.name, x: b.x, y: b.y, z: b.z }];
      }
    });
    return best;
  }

  /* 射线与曲面求交：把 P(u) = P0 + u·D 代进曲面方程，解一元二次。

     圆/球/柱/锥都是二次曲面，所以一个公式够用：
       球：   |P - c|² = R²
       圆柱： |P - c|² - ((P-c)·n)² = r²     （到轴的距离等于半径）
       圆锥： ((P-A)·n)² = cos²α·|P-A|²      （与轴的夹角等于半顶角）
       圆：   先交圆所在平面，再投到圆上
     用**靠近相机**的那个交点，所以拖到哪儿点就贴到哪儿的正面。 */
  function surfaceHit(svg, figure, constraint, event) {
    var at = {};
    (figure.points || []).forEach(function (item) { at[item.name] = item; });
    var p0 = solidRayAt(svg, event.clientX, event.clientY, 0);
    var p1 = solidRayAt(svg, event.clientX, event.clientY, 1);
    var d = [p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2]];
    var curve = null;
    (figure.curves || []).forEach(function (item) {
      if (item.name === constraint.target) curve = item;
    });
    if (constraint.type === 'circle' || constraint.type === 'cylinder'
        || constraint.type === 'cone') {
      if (!curve) return null;
    }
    var center = null;
    var normal = [0, 0, 1];
    var radius = 0;
    if (constraint.type === 'sphere') {
      (figure.surfaces || []).forEach(function (item) {
        if (item.kind === 'sphere' && item.at === constraint.target) {
          center = item.center;
          radius = item.radius;
        }
      });
      if (!center) return null;
    } else {
      center = curve.center;
      normal = curve.axis || [0, 0, 1];
      radius = curve.radius;
    }
    if (constraint.type === 'circle') {
      // 圆：射线交它所在的平面，再把交点投回圆上
      var denom = d[0] * normal[0] + d[1] * normal[1] + d[2] * normal[2];
      if (Math.abs(denom) < 1e-9) return null;
      var f0 = (p0[0] - center[0]) * normal[0] + (p0[1] - center[1]) * normal[1]
             + (p0[2] - center[2]) * normal[2];
      var u = -f0 / denom;
      var hit = [p0[0] + d[0] * u, p0[1] + d[1] * u, p0[2] + d[2] * u];
      var out = [hit[0] - center[0], hit[1] - center[1], hit[2] - center[2]];
      var size = Math.hypot(out[0], out[1], out[2]) || 1;
      return [center[0] + out[0] / size * radius, center[1] + out[1] / size * radius,
              center[2] + out[2] / size * radius];
    }
    // 剩下三种：解一元二次
    var base = center;
    var axis = normal;
    var cos2 = 1;
    if (constraint.type === 'cone') {
      var apex = null;
      (figure.surfaces || []).forEach(function (item) {
        if (item.kind === 'cone' && item.circles.indexOf(constraint.target) >= 0) {
          apex = item.apexAt;
          var height = item.height || 1;
          cos2 = height * height / (height * height + item.radius * item.radius);
        }
      });
      if (!apex) return null;
      base = apex;
    }
    var a2 = p0[0] - base[0];
    var a3 = p0[1] - base[1];
    var a4 = p0[2] - base[2];
    var n2 = axis[0] * axis[0] + axis[1] * axis[1] + axis[2] * axis[2];
    var function_quadratic = function (start, direction) {
      var qa, qb, qc;
      if (constraint.type === 'sphere') {
        qa = direction[0] * direction[0] + direction[1] * direction[1] + direction[2] * direction[2];
        qb = 2 * (start[0] * direction[0] + start[1] * direction[1] + start[2] * direction[2]);
        qc = start[0] * start[0] + start[1] * start[1] + start[2] * start[2] - radius * radius;
      } else if (constraint.type === 'cylinder') {
        var along = (start[0] * axis[0] + start[1] * axis[1] + start[2] * axis[2])
                  / (n2 || 1);
        var alongD = (direction[0] * axis[0] + direction[1] * axis[1] + direction[2] * axis[2])
                   / (n2 || 1);
        var sx = start[0] - along * axis[0];
        var sy = start[1] - along * axis[1];
        var sz = start[2] - along * axis[2];
        var dx2 = direction[0] - alongD * axis[0];
        var dy2 = direction[1] - alongD * axis[1];
        var dz2 = direction[2] - alongD * axis[2];
        qa = dx2 * dx2 + dy2 * dy2 + dz2 * dz2;
        qb = 2 * (sx * dx2 + sy * dy2 + sz * dz2);
        qc = sx * sx + sy * sy + sz * sz - radius * radius;
      } else {                                   // cone
        var dotA = start[0] * axis[0] + start[1] * axis[1] + start[2] * axis[2];
        var dotD = direction[0] * axis[0] + direction[1] * axis[1] + direction[2] * axis[2];
        var lenA = start[0] * start[0] + start[1] * start[1] + start[2] * start[2];
        var cross2 = lenA * n2 - dotA * dotA;
        var crossD = (start[0] * direction[0] + start[1] * direction[1]
                      + start[2] * direction[2]) * n2 - dotA * dotD;
        var lenD = direction[0] * direction[0] + direction[1] * direction[1]
                 + direction[2] * direction[2];
        var crossE = lenD * n2 - dotD * dotD;
        qa = cos2 * crossE - (1 - cos2) * dotD * dotD;
        qb = 2 * (cos2 * crossD - (1 - cos2) * dotA * dotD);
        qc = cos2 * cross2 - (1 - cos2) * dotA * dotA;
      }
      return [qa, qb, qc];
    };
    var start = [p0[0] - base[0], p0[1] - base[1], p0[2] - base[2]];
    var quad = function_quadratic(start, d);
    var disc = quad[1] * quad[1] - 4 * quad[0] * quad[2];
    if (disc < 0 || Math.abs(quad[0]) < 1e-12) return null;
    var root = Math.sqrt(disc);
    var u1 = (-quad[1] - root) / (2 * quad[0]);
    var u2 = (-quad[1] + root) / (2 * quad[0]);
    var pick = Math.abs(u1) < Math.abs(u2) ? u1 : u2;
    var solution = [p0[0] + d[0] * pick, p0[1] + d[1] * pick, p0[2] + d[2] * pick];
    if (constraint.type === 'cylinder') {
      // 侧面只在两个圆之间：超出就夹回来
      var low = null;
      var high = null;
      (figure.curves || []).forEach(function (item) {
        if (item.name === constraint.target || (item.ring && low === null)) {
          if (low === null) low = item.center[2];
        }
      });
      var ends = [];
      (figure.surfaces || []).forEach(function (item) {
        if (item.kind === 'cylinder' && item.circles.indexOf(constraint.target) >= 0) {
          item.circles.forEach(function (label) {
            (figure.curves || []).forEach(function (curve2) {
              if (curve2.name === label) ends.push(curve2.center[2]);
            });
          });
        }
      });
      if (ends.length === 2) {
        var bottom = Math.min(ends[0], ends[1]);
        var top = Math.max(ends[0], ends[1]);
        solution[2] = Math.max(bottom, Math.min(top, solution[2]));
      }
    }
    return solution;
  }

  function solidDragEnd() {
    if (!solidState.dragging) return;
    solidState.dragging = false;
    solidState.dragPoint = null;
    solidState.dragEdge = null;
    solidState.edgeFrom = null;
    solidState.dragBind = null;
    solidState.dragLastX = null;
    solidState.dragLastY = null;
    // 动过 4 像素以上才算拖动：紧接着的 click 事件不当"点选"处理
    solidState.afterDrag = solidState.dragMoved > 4;
    if (solidState.dragTimer) { clearTimeout(solidState.dragTimer); solidState.dragTimer = null; }
    solidApply(true);             // 松手了，把精确值算回来
  }

  /* 删点：把那行定义删掉，用到它的棱、面也一起删（不然服务端会报"没定义"） */
  function solidDeletePoint(name) {
    var kept = [];
    var removed = false;
    solidState.shapes.forEach(function (line) {
      if (new RegExp('^' + escapeRegExp(name) + '\\s*\\(').test(line.trim())) {
        removed = true;
        return;
      }
      var words = line.replace(/[(),]/g, ' ').split(/\s+/);
      if (words.indexOf(name) > 0) { removed = true; return; }
      kept.push(line);
    });
    if (!removed) { setFigureStatus('没找到这个点', 'warn'); return; }
    solidState.shapes = kept;
    solidState.selected = [];
    solidState.constraint = null;
    // 绑在它身上的约束（圆上/侧面上/线上动点）一起清掉：点都没了，留着下次会指到一个不存在的点
    solidState.dynamicPoints = (solidState.dynamicPoints || []).filter(function (item) {
      return item.name !== name;
    });
    solidHighlight();
    solidHint('删掉了 ' + name);
    solidApply();
  }

  /* 点在圆的投影上吗：把圆的顶点环投到屏幕，量点到折线的距离。

     圆是一圈隐藏顶点拼的，直接量到折线最近距离就够了（老师点的是"边"）。 */
  function solidCurveUnder(svg, event, tolerance) {
    var figure = figureState.current;
    if (!figure || !figure.curves) return null;
    var at = {};
    (figure.points || []).forEach(function (point) { at[point.name] = point; });
    var limit = (tolerance || 12) * (tolerance || 12);
    var best = null;
    var bestDistance = Infinity;
    figure.curves.forEach(function (curve) {
      var ring = curve.ring || [];
      for (var index = 0; index < ring.length; index++) {
        var a = at[ring[index]];
        var b = at[ring[(index + 1) % ring.length]];
        if (!a || !b) continue;
        var pa = solidProject([a.x, a.y, a.z], svg.__solidCenter, svg.__solidRadius,
                              svg.__solidBox.w, svg.__solidBox.h);
        var pb = solidProject([b.x, b.y, b.z], svg.__solidCenter, svg.__solidRadius,
                              svg.__solidBox.w, svg.__solidBox.h);
        var ca = viewBoxToClient(svg, pa.x, pa.y);
        var cb = viewBoxToClient(svg, pb.x, pb.y);
        var dx = cb.x - ca.x;
        var dy = cb.y - ca.y;
        var lengthSquared = dx * dx + dy * dy;
        var ratio = lengthSquared < 1e-9 ? 0 : Math.max(0, Math.min(1,
          ((event.clientX - ca.x) * dx + (event.clientY - ca.y) * dy) / lengthSquared));
        var spotX = ca.x + dx * ratio;
        var spotY = ca.y + dy * ratio;
        var distance = (event.clientX - spotX) * (event.clientX - spotX)
                     + (event.clientY - spotY) * (event.clientY - spotY);
        if (distance < bestDistance) { bestDistance = distance; best = curve; }
      }
    });
    return (best && bestDistance < limit) ? best : null;
  }

  /* 按当前选中的圆/点，推出是哪个曲面。

     一个圆 + 一个不在圆心的点 = 圆锥侧面；两个圆 = 圆柱（或圆台）侧面。 */
  function inferSurface() {
    var figure = figureState.current;
    if (!figure || !figure.surfaces) return null;
    var picked = solidState.pickedCurves;
    var names = {};
    picked.forEach(function (name) { names[name] = true; });
    if (solidState.pickedSurface) return solidState.pickedSurface;
    // 两个圆 -> 圆柱
    var cylinders = figure.surfaces.filter(function (item) {
      return item.kind === 'cylinder' && item.circles.length === 2
        && names[item.circles[0]] && names[item.circles[1]];
    });
    if (cylinders.length) return cylinders[0];
    // 一个圆 + 一个选中的顶点 -> 圆锥
    var cones = figure.surfaces.filter(function (item) {
      return item.kind === 'cone' && names[item.circles[0]]
        && solidState.selected.indexOf(item.at) < 0
        && figure.points.some(function (point) {
             return point.name === item.at
               && solidState.selected.indexOf(point.name) >= 0; });
    });
    if (cones.length) return cones[0];
    return null;
  }

  /* 这个点是不是"挂在某张曲面上的动点"。

     是的话返回它的约束 —— 拖动要用。**不看 solidState.constraint**：
     那是"当前工具的临时约束"，点一下别的工具（比如「面面角」）就没了，
     动点也就跟着拖不动了（老师反馈过"只会转动视角"）。 */
  function constraintOfPoint(name) {
    var found = null;
    (solidState.dynamicPoints || []).forEach(function (item) {
      if (item.name === name) found = item;
    });
    return found;
  }

  /* 这个点在不在这个圆上。

     圆是 (圆心 C, 半径 r, 轴 n)：把 C->P 拆成沿轴的一段和垂直于轴的一段，
     沿轴那段≈0 且垂直那段≈r 才算在圆上。容差按整张图的尺度取，别太死。 */
  function pointOnCurve(point, curve, figure) {
    var box = figure.bbox || [0, 0, 0, 1, 1, 1];
    var span = Math.max(Math.hypot(box[3] - box[0], box[4] - box[1], box[5] - box[2]), 1);
    var tolerance = span * 2e-3;
    var axis = curve.axis || [0, 0, 1];
    var norm = Math.hypot(axis[0], axis[1], axis[2]) || 1;
    var unit = [axis[0] / norm, axis[1] / norm, axis[2] / norm];
    var v = [point.x - curve.center[0], point.y - curve.center[1],
             point.z - curve.center[2]];
    var along = v[0] * unit[0] + v[1] * unit[1] + v[2] * unit[2];
    var side = [v[0] - along * unit[0], v[1] - along * unit[1], v[2] - along * unit[2]];
    var radial = Math.hypot(side[0], side[1], side[2]);
    return {ok: Math.abs(along) <= tolerance
                && Math.abs(radial - curve.radius) <= tolerance,
            offPlane: Math.abs(along), offRadius: Math.abs(radial - curve.radius),
            tolerance: tolerance};
  }

  /* 点在不在这个约束对应的曲面上。

     圆 / 球 / 圆柱侧面 / 圆锥侧面各判一次，返回 {ok, why}。
     老师手输坐标要落在曲面上，越界就拒绝并说清超了多少。 */
  function pointOnSurface(point, binding, figure) {
    var box = figure.bbox || [0, 0, 0, 1, 1, 1];
    var span = Math.max(Math.hypot(box[3] - box[0], box[4] - box[1], box[5] - box[2]), 1);
    var tolerance = span * 2e-3;
    var p = [point.x, point.y, point.z];
    if (binding.type === 'circle' || binding.type === 'cylinder'
        || binding.type === 'cone') {
      var curve = null;
      (figure.curves || []).forEach(function (item) {
        if (item.name === binding.target) curve = item;
      });
      if (!curve) return {ok: false, why: '找不到那个圆了'};
      if (binding.type === 'circle') {
        var check = pointOnCurve(point, curve, figure);
        return check.ok ? {ok: true} : {ok: false,
          why: '离圆面 ' + round4(check.offPlane) + '、离圆周 ' + round4(check.offRadius)
               + '（允许 ' + round4(check.tolerance) + '）'};
      }
      // 圆柱 / 圆锥侧面：先看半径对不对，再看高度在不在范围内
      var axis = curve.axis || [0, 0, 1];
      var norm = Math.hypot(axis[0], axis[1], axis[2]) || 1;
      var unit = [axis[0] / norm, axis[1] / norm, axis[2] / norm];
      var v = [p[0] - curve.center[0], p[1] - curve.center[1], p[2] - curve.center[2]];
      var along = v[0] * unit[0] + v[1] * unit[1] + v[2] * unit[2];
      var side = [v[0] - along * unit[0], v[1] - along * unit[1], v[2] - along * unit[2]];
      var radial = Math.hypot(side[0], side[1], side[2]);
      var surface = null;
      (figure.surfaces || []).forEach(function (item) {
        if (item.kind === binding.type && (item.circles || []).indexOf(binding.target) >= 0) {
          surface = item;
        }
      });
      var height = surface ? (surface.height || 0) : 0;
      if (binding.type === 'cone') {
        // 圆锥：半径随高度线性收缩
        var ratio = height > 0 ? Math.max(0, Math.min(1, along / height)) : 0;
        var want = curve.radius * (1 - ratio);
        if (Math.abs(radial - want) > tolerance || along < -tolerance
            || along > height + tolerance) {
          return {ok: false, why: '不在圆锥侧面上（这个高度的半径应该是 '
                        + round4(want) + '，现在是 ' + round4(radial) + '）'};
        }
        return {ok: true};
      }
      if (Math.abs(radial - curve.radius) > tolerance) {
        return {ok: false, why: '不在圆柱侧面上（半径应该是 ' + round4(curve.radius)
                      + '，现在是 ' + round4(radial) + '）'};
      }
      if (along < -tolerance || along > height + tolerance) {
        return {ok: false, why: '超出圆柱高度了（应该在 0 到 ' + round4(height) + ' 之间）'};
      }
      return {ok: true};
    }
    if (binding.type === 'sphere') {
      var center = null;
      var radius = 0;
      (figure.surfaces || []).forEach(function (item) {
        if (item.kind === 'sphere' && item.at === binding.target) {
          center = item.center;
          radius = item.radius;
        }
      });
      if (!center) return {ok: false, why: '找不到那个球了'};
      var distance = Math.hypot(p[0] - center[0], p[1] - center[1], p[2] - center[2]);
      if (Math.abs(distance - radius) > tolerance) {
        return {ok: false, why: '不在球面上（球心到它的距离应该是 ' + round4(radius)
                      + '，现在是 ' + round4(distance) + '）'};
      }
      return {ok: true};
    }
    return {ok: true};
  }

  /* 老师手输坐标：改了之后要还在曲面上，否则拒绝。 */
  function applyPointEdit(name, x, y, z) {
    var figure = figureState.current;
    if (!figure || figure.kind !== 'solid') return false;
    if (!isFinite(x) || !isFinite(y) || !isFinite(z)) {
      setFigureStatus('三个坐标都要是数字', 'warn');
      return false;
    }
    var binding = constraintOfPoint(name);
    if (binding) {
      var check = pointOnSurface({x: x, y: y, z: z}, binding, figure);
      if (!check.ok) {
        setFigureStatus(name + ' ' + check.why + '，所以没改', 'warn');
        return false;
      }
    }
    if (!solidMovePoint(name, x, y, z)) {
      setFigureStatus('改不了 ' + name + ' 的坐标', 'warn');
      return false;
    }
    solidState.selected = [name];
    solidHint(name + ' 已移到 (' + round4(x) + ', ' + round4(y) + ', ' + round4(z) + ')');
    setFigureStatus(name + ' 的坐标改好了', 'done');
    solidApply();
    return true;
  }

  /* 把选中的那个点设为"选中这个圆上的动点"。

     老师要的交互：点圆的边 -> 点圆上一个点 -> 按这里。点不在圆上就明确拒绝。 */
  function bindPointToCircle() {
    var figure = figureState.current;
    if (!figure || figure.kind !== 'solid') {
      setFigureStatus('先在立体图上画一个圆', 'warn');
      return;
    }
    var label = solidState.pickedCurves.length
      ? solidState.pickedCurves[solidState.pickedCurves.length - 1] : null;
    if (!label) {
      setFigureStatus('先点一下圆的边，选中那个圆', 'warn');
      return;
    }
    var curve = null;
    (figure.curves || []).forEach(function (item) {
      if (item.name === label) curve = item;
    });
    if (!curve) { setFigureStatus('找不到那个圆了，重新选一下', 'warn'); return; }
    if (solidState.selected.length !== 1) {
      setFigureStatus('再点一下圆上的一个点（只选一个点）', 'warn');
      return;
    }
    var name = solidState.selected[0];
    var point = null;
    (figure.points || []).forEach(function (item) {
      if (item.name === name) point = item;
    });
    if (!point) { setFigureStatus('找不到那个点了', 'warn'); return; }
    var check = pointOnCurve(point, curve, figure);
    if (!check.ok) {
      setFigureStatus(name + ' 不在 ' + label + ' 上（离圆面 '
        + round4(check.offPlane) + '、离圆周 ' + round4(check.offRadius)
        + '，允许 ' + round4(check.tolerance) + '）', 'warn');
      return;
    }
    solidState.dynamicPoints.push({name: name, point: name, type: 'circle',
                                   target: label});
    solidState.constraint = {type: 'circle', target: label, point: name};
    refreshSolid();
    solidHint(name + ' 已经是 ' + label + ' 上的动点了，拖着它在圆上滑动');
    setFigureStatus(name + ' 已经是圆上动点，可以直接拖了', 'done');
  }

  /* 在选中的圆 / 曲面上放一个动点 */
  function addDynamicPoint(where) {
    var figure = figureState.current;
    if (!figure || figure.kind !== 'solid') {
      setFigureStatus('先在立体图上画一个圆或曲面', 'warn');
      return;
    }
    var name = nextPointName(figure);
    var line = '';
    var constraint = null;
    if (where === 'circle') {
      var curve = solidState.pickedCurves.length
        ? solidState.pickedCurves[solidState.pickedCurves.length - 1] : null;
      if (!curve) { setFigureStatus('先点一下圆的边，选中它', 'warn'); return; }
      line = 'oncircle ' + name + ' ' + curve + ' 0';
      constraint = { type: 'circle', target: curve, point: name };
    } else if (where === 'surface') {
      var surface = inferSurface();
      if (!surface) {
        setFigureStatus('先选一个圆；圆锥再点一下它的顶点，圆柱再选第二个圆', 'warn');
        return;
      }
      line = 'onsurface ' + name + ' ' + surface.circles[0] + ' 0 0.5';
      constraint = { type: surface.kind, target: surface.circles[0], point: name };
      solidState.pickedSurface = surface;
    } else {
      if (!solidState.pickedSurface || solidState.pickedSurface.kind !== 'sphere') {
        setFigureStatus('先选中球心，再按「整个球面」', 'warn');
        return;
      }
      line = 'onsphere ' + name + ' ' + solidState.pickedSurface.at + ' 0 30';
      constraint = { type: 'sphere', target: solidState.pickedSurface.at, point: name };
    }
    solidState.shapes.push(line);
    solidState.selected = [name];
    solidState.constraint = constraint;
    solidState.dynamicPoints.push({ name: name, point: name,
                                    type: constraint.type,
                                    target: constraint.target });
    solidHighlight();
    solidHint('加好了动点 ' + name + '，拖着它就能在'
      + (where === 'circle' ? '圆上' : where === 'surface' ? '侧面上' : '球面上') + '滑动');
    solidApply();
  }

  /* 截面圆：把球面上选中的几个点定成一个圆。

     交给服务端算：过一个平面和球面的交线（平面到球心的距离决定半径），
     和圆的处理完全一样 —— 加完照样能选中、能加圆上动点。 */
  function addSectionCircle() {
    var figure = figureState.current;
    var sphere = solidState.pickedSurface;
    if (!figure || figure.kind !== 'solid' || !sphere || sphere.kind !== 'sphere') {
      setFigureStatus('先选中球心，再按「整个球面」', 'warn');
      return;
    }
    if (solidState.selected.length < 3) {
      setFigureStatus('在球面上点三个点（球面动点加的点），再按「截面圆」', 'warn');
      return;
    }
    solidState.shapes.push('spheresection ' + sphere.at + ' '
      + solidState.selected.slice(0, 3).join(' '));
    solidState.selected = [];
    solidHint('截面圆加好了，右栏能看到它；它也能被选中、能加圆上动点');
    solidApply();
  }

  /* 一键取消所有选中的点：连线段/平面的约束一起解除 */
  function clearSolidSelection() {
    if (!solidState.selected.length && !solidState.constraint && !solidState.armed) {
      setFigureStatus('本来就没有选中的东西', 'warn');
      return;
    }
    solidState.selected = [];
    solidState.constraint = null;
    solidState.armed = null;
    solidState.edgeMode = false;
    solidState.pickedCurves = [];
    solidState.pickedSurface = null;
    solidState.dragging = false;
    solidState.dragPoint = null;
    solidHighlight();
    refreshSolid();
    solidHint('已取消所有选择');
  }

  /* 清空：把画出来的空间对象整个清掉。工具条上的「清空」和右栏那个按钮都走这里。 */
  function clearSolidAll() {
    if (!solidState.shapes.length) { setFigureStatus('本来就是空的', 'warn'); return; }
    solidState.shapes = [];
    solidState.selected = [];
    solidState.constraint = null;
    solidState.dynamicPoints = [];
    solidHighlight();
    solidApply();
  }

  /* 点一下图上的东西：先看点，再看圆的边。
     鼠标点击和触屏"轻点"都走这里 —— 触屏上浏览器补的那个 click 经常被滑动判定的
     preventDefault 吃掉，光等它会让选点"时灵时不灵"。 */
  function solidPickAt(svg, figure, event) {
    // 上一次拖动留下的标记。**只有"这一下真的在拖点"时才看 dragMoved** ——
    // dragMoved 是上一次拖动留下来的数，谁都没清过；拿它单独判断的话，拖过一回之后
    // 之后每一次点击都会被当成"刚拖过"丢掉，老师那边就是"点屏幕选不中点了"。
    var wasDragging = solidState.dragging;
    var moved = solidState.afterDrag || (wasDragging && solidState.dragMoved > 4);
    solidState.afterDrag = false;
    if (solidState.dragging) {
      solidState.dragging = false;
      solidState.dragPoint = null;
    }
    if (moved) return;
    var point = solidPointUnder(svg, event);
    if (!point) {
      /* 「拖棱」架着的时候：**点一条棱**当轨道（那条棱的方向就是轨道）。
         这个工具只管棱，和「沿线拖点」分开 —— 老师要求"沿线拖只能拖点"。 */
      if (solidState.armed && solidState.armed.kind === 'edgealong') {
        var trackEdge = solidEdgeUnder(svg, event, figure);
        if (trackEdge && trackEdge.length === 2) {
          solidState.constraint = {
            type: 'line', names: [trackEdge[0].name, trackEdge[1].name],
            line: [[trackEdge[0].x, trackEdge[0].y, trackEdge[0].z],
                   [trackEdge[1].x, trackEdge[1].y, trackEdge[1].z]],
            plane: null
          };
          solidState.edgeMode = true;
          solidState.armed = null;
          solidState.selected = [];
          solidHighlight();
          refreshSolid();
          solidHint('轨道就是 ' + trackEdge[0].name + trackEdge[1].name
                    + '：按住别的棱拖动，整条棱沿着它滑（长度不变）');
          setFigureStatus('轨道选好了：按住一条棱拖动（右上「自由拖」可以解开）', 'done');
          return;
        }
        setFigureStatus('点一条棱当轨道（棱上任意位置，别点端点）', 'warn');
        return;
      }
      // 正在用"点多几个点再按确定"的工具时，点在圆的边上不算数：
      // 那会把已经选好的点清掉（老师会以为"选不了第二个点"）
      if (solidState.armed) {
        setFigureStatus('现在是在选点：点这几个点，然后按「确定」', 'warn');
        return;
      }
      // 没点到点，试试是不是点在圆的边上：圆也要能像点一样被选中
      var curve = solidCurveUnder(svg, event);
      if (curve) {
        // 别用中文标识符：这个文件的编码来回转过几道，
        // 中文变量名会变成运行时错误（真踩过：圆一次也选不中）
        var at = solidState.pickedCurves.indexOf(curve.name);
        if (at >= 0) solidState.pickedCurves.splice(at, 1);
        else solidState.pickedCurves.push(curve.name);
        solidState.selected = [];
        refreshSolid();
        solidHint(solidState.pickedCurves.length
          ? '已选中 ' + solidState.pickedCurves.join('、')
            + '，可以按「圆上动点」；圆锥再点一下顶点，圆柱再选第二个圆'
          : '取消选中圆了');
        return;
      }
      solidState.selected = [];
      solidHint();
      return;
    }
    if (solidState.tool === 'delete') { solidDeletePoint(point.name); return; }
    // 多点工具（连线 / 画面 / 画圆 / 画球 / 三种角）正在选点的时候，点一个就**加一个**。
    //   以前这里会被"之前点过圆的边"接管成"只留这一个点"：老师看到的就是
    //   "无论如何都只能选中一个点，选不了下一个"（那个状态还看不见、清不掉）。
    //   只有"选中了圆、要指定那一个点"（绑定圆上动点）时才替换。
    if (!solidState.armed && solidState.pickedCurves.length) {
      solidState.selected = [point.name];
    } else {
      var index = solidState.selected.indexOf(point.name);
      if (index >= 0) { solidState.selected.splice(index, 1); } else {
        solidState.selected.push(point.name);
      }
    }
    solidHint();
    refreshSolid();
    setFigureStatus('已选 ' + (solidState.selected.join('、') || '（空）'), 'done');
    /* 只要两个点的工具（连线 / 沿线拖）：点够两个就**直接建出来**，不必再按「确定」。
       老师的心智就是"点两个点连一条线"：多这一步，他看到的就是"点了两个点没反应、
       创建不了棱"（他报的正是这个）。画面 / 画圆 / 画球 / 三种角那种要点三个以上、
       或者要多选几个点的，仍然按「确定」收尾。 */
    /* 每一步只要两个点的工具（连线 / 画球 / 沿线拖点 / 拖棱）点够两个就**自动进下一步**，
       不必再按「确定」—— 老师的心智就是"点两个点"，多这一步他看到的就是"点了没反应"。
       画面 / 画圆 / 三种角那种要点三个以上、或者想多选几个点的，仍然按「确定」收尾。
       （拖棱是两步各两点：第 1 步点完自动进第 2 步，第 2 步点完直接定下轨道和要滑的两点。） */
    var autoSpec = solidState.armed ? SOLID_ARMS[solidState.armed.kind] : null;
    if (autoSpec) {
      var autoNeed = autoSpec.steps[solidState.armed.stage];
      if (autoNeed <= 2 && solidState.selected.length === autoNeed) {
        confirmConstraint();
      }
    }
  }

  function setupSolidTools(svg, figure) {
    svg.addEventListener('click', function (event) {
      // 触屏轻点是自己处理的（见 setupSolidGestures 的 touchend），
      // 浏览器随后补的那个 click 要丢掉，不然一下会选中又被取消
      if (Date.now() - (solidState.touchPickedAt || 0) < 500) return;
      solidPickAt(svg, figure, event);
    });
    svg.addEventListener('mousedown', function (event) {
      var point = solidPointUnder(svg, event);
      if (!point) return;
      // 拖动时别把坐标轴上的数字、点名一起选成蓝色文字
      event.preventDefault();
      // 正在用"点多几个点再按确定"的工具（连线 / 画面 / 画圆 / 画球 / 三种角）时，
      //   按在点上是在**选点**，不是拖它。以前这里无条件开始拖：老师点第二个点，
      //   看到的是"点被拖走了"，于是"根本没法点两个点创建棱，只连线还是当点拖动"
      //   （实际上点还有可能被拖出 4 像素以上，连选中的那一下都被丢掉）。
      //   选了轨道（沿线拖 / 在面拖）不算这种情况：那时候拖点就是要它沿着轨道滑。
      if (solidState.armed) return;
      solidBeginDrag(point, figure);
    });
  }

  /* 立体的右栏 = 平面那块「代数区」的同款：一行一个对象，行里能改坐标、能删。

     重新写过是因为老师反馈"右栏信息冗余重复、还清不掉"：原来四张卡片里
     「怎么看」跟底下的状态条是同一句话，角那张卡片又把名字写了两遍；
     除了工具条上那个「清空」，右栏自己没有"清掉 / 删掉一项"的地方。
     现在：头一行数量 + 清空 / 取消选择，下面 点 / 棱 / 面 / 圆 / 球 一行一个，
     点那一行直接改三个坐标（回车生效），行尾「删除」。 */
  function renderSolidInfo(box, figure) {
    // 每次重画都从空开始。以前这里只往里 append：拖动中每 70ms 重画一次就多糊一层，
    //   右栏于是堆着一大摞"上一张图的点 / 棱 / 面 / 角"，按「清空」也清不掉
    //   （老师反馈的"信息冗余重复"和"清空后还剩一大堆不存在的对象"都是这个）。
    box.innerHTML = '';
    // 曲面逼近出来的隐藏顶点不进这张表：一个圆 36 个 p1、p2… 谁也没法看
    var points = (figure.points || []).filter(function (item) { return !item.hidden; });
    var edges = figure.edges || [];
    var faces = figure.faces || [];
    var curves = figure.curves || [];
    var surfaces = figure.surfaces || [];
    var sources = figure.source || [];
    var total = points.length + edges.length + faces.length + curves.length + surfaces.length;

    var head = el('div', 'plane-side-head');
    head.appendChild(el('span', null, '空间对象'));
    head.appendChild(el('span', null, total ? total + ' 个' : '空'));
    box.appendChild(head);

    var bar = el('div', 'solid-side-bar');
    var clear = el('button', 'plane-mini', '清空');
    clear.title = '把画出来的空间对象全删掉（跟工具条上的「清空」一样）';
    clear.onclick = clearSolidAll;
    bar.appendChild(clear);
    var drop = el('button', 'plane-mini', '取消选择');
    drop.title = '把选中的点 / 圆 / 曲面都放掉';
    drop.onclick = clearSolidSelection;
    bar.appendChild(drop);
    box.appendChild(bar);

    // 角**置顶**：老师点「线线角 / 线面角 / 面面角」就是为了看这个数，
    // 它以前排在点、棱、面、圆、球的下面，得往下翻才看得到（老师反馈"很不方便"）。
    // 现在紧跟在"空间对象 / 清空"那一行下面，对象列表排在它后面。
    var angles = figure.angles || [];
    if (angles.length) {
      var angleCard = el('div', 'info-card');
      angleCard.appendChild(el('h4', null, '角'));
      angles.forEach(function (item) {
        var title = ({ lineangle: '线线角', lineplane: '线面角',
                       planeangle: '面面角' })[item.type] || '角';
        var names = (item.names || []).join('');
        angleCard.appendChild(infoRow(title + ' ' + names,
          item.degrees == null ? '算不出来'
            : (item.nice != null ? item.nice + '°' : round4(item.degrees) + '°')));
        if (item.error) {
          angleCard.appendChild(el('div', 'info-hint', item.error));
        } else if (item.approximate) {
          angleCard.appendChild(el('div', 'info-hint',
            'sin = ' + item.sin + '　cos = ' + item.cos + '（拖动中先用小数）'));
        } else if (item.sin != null) {
          // 线面角是按**正弦**定义的，把定义那一条排在前面
          var pairs = item.by === 'sin'
            ? ['sin = ' + item.sin, 'cos = ' + item.cos]
            : ['cos = ' + item.cos, 'sin = ' + item.sin];
          angleCard.appendChild(el('div', 'info-hint', pairs.join('　')));
        } else {
          angleCard.appendChild(el('div', 'info-hint',
            '（这几个点的坐标不是简单有理数，精确值算不出来，只给了度数）'));
        }
      });
      box.appendChild(angleCard);
    }

    if (!total) {
      box.appendChild(el('div', 'plane-empty',
        '左边「更多设置」按坐标加点；或者直接在面板下面那行写 A(0,0,0)。'));
      return;
    }

    var at = {};
    points.forEach(function (item) { at[item.name] = item; });

    points.forEach(function (item) {
      // 坐标可以直接改：回车或离开输入框就生效。
      // 动点改完必须还在它那张曲面上，否则拒绝（applyPointEdit 里判）。
      var row = el('div', 'plane-row' + (solidState.selected.indexOf(item.name) >= 0 ? ' on' : ''));
      var main = el('div', 'plane-row-main');
      var title = el('div', 'plane-row-id');
      title.appendChild(document.createTextNode(item.name));
      var binding = constraintOfPoint(item.name);
      title.appendChild(el('span', 'plane-row-tag',
        binding ? ({ circle: '圆上动点', sphere: '球面动点', line: '线上滑动',
                     face: '面上滑动' })[binding.type] || '动点' : '点'));
      main.appendChild(title);
      // 定义那一行照抄老师写的那句（动点就是 oncircle / onsurface，别处看不到）
      var definition = '';
      for (var index = 0; index < sources.length; index++) {
        if (new RegExp('^\\s*' + escapeRegExp(item.name) + '\\s*[=(]').test(sources[index])) {
          definition = sources[index];
        }
      }
      main.appendChild(el('div', 'plane-row-def',
        definition || (item.name + '(' + round4(item.x) + ', ' + round4(item.y)
                       + ', ' + round4(item.z) + ')')));
      var group = el('div', 'solid-coord');
      [['x', item.x], ['y', item.y], ['z', item.z]].forEach(function (pair) {
        var input = document.createElement('input');
        input.type = 'text';
        input.className = 'plane-edit-input';
        input.value = round4(pair[1]);
        input.title = item.name + ' 的 ' + pair[0] + ' 坐标（改完回车）';
        var commit = function () {
          var boxes = group.querySelectorAll('input');
          var values = [];
          for (var i = 0; i < boxes.length; i++) { values.push(Number(boxes[i].value)); }
          if (!applyPointEdit(item.name, values[0], values[1], values[2])) {
            // 被拒绝：把输入框恢复成原来的值，别留一个假的数字在上面
            boxes[0].value = round4(item.x);
            boxes[1].value = round4(item.y);
            boxes[2].value = round4(item.z);
          }
        };
        input.onkeydown = function (event) {
          if (event.key === 'Enter') { event.preventDefault(); commit(); }
        };
        input.onblur = commit;
        group.appendChild(input);
      });
      main.appendChild(group);
      row.appendChild(main);
      var acts = el('div', 'plane-row-acts');
      var remove = el('button', 'plane-mini', '删除');
      remove.title = '删掉 ' + item.name + '；用到它的棱和面会一起删掉';
      remove.onclick = function () { solidDeletePoint(item.name); };
      acts.appendChild(remove);
      row.appendChild(acts);
      row.onclick = function (event) {
        if (event.target && event.target.tagName === 'INPUT') return;   // 改坐标别当选中
        var where = solidState.selected.indexOf(item.name);
        if (where >= 0) solidState.selected.splice(where, 1);
        else solidState.selected = [item.name];
        solidHighlight();
        refreshSolid();
        solidHint(solidState.selected.length ? '选中了 ' + solidState.selected.join('、')
                                            : '取消选中了');
      };
      box.appendChild(row);
    });

    // 棱 / 面 / 圆 / 球：它们是上面那些点算出来的，删点就跟着没了，所以只列出来
    edges.forEach(function (edge) {
      var a = at[edge[0]];
      var b = at[edge[1]];
      var row = el('div', 'plane-row');
      var main = el('div', 'plane-row-main');
      var title = el('div', 'plane-row-id');
      title.appendChild(document.createTextNode(edge.join('')));
      title.appendChild(el('span', 'plane-row-tag', '棱'));
      main.appendChild(title);
      if (a && b) {
        main.appendChild(el('div', 'plane-row-val',
          '|' + edge.join('') + '| = ' + round4(Math.hypot(a.x - b.x, a.y - b.y, a.z - b.z))));
      }
      row.appendChild(main);
      box.appendChild(row);
    });
    faces.forEach(function (face) {
      var row = el('div', 'plane-row');
      var main = el('div', 'plane-row-main');
      var title = el('div', 'plane-row-id');
      title.appendChild(document.createTextNode(face.join('')));
      title.appendChild(el('span', 'plane-row-tag', '面'));
      main.appendChild(title);
      row.appendChild(main);
      box.appendChild(row);
    });
    curves.forEach(function (curve) {
      var row = el('div', 'plane-row');
      var main = el('div', 'plane-row-main');
      var title = el('div', 'plane-row-id');
      title.appendChild(document.createTextNode(curve.name || '圆'));
      title.appendChild(el('span', 'plane-row-tag', '圆'));
      main.appendChild(title);
      if (curve.radius != null) {
        main.appendChild(el('div', 'plane-row-val', '半径 ' + round4(curve.radius)));
      }
      row.appendChild(main);
      box.appendChild(row);
    });
    surfaces.forEach(function (surface) {
      var label = ({ sphere: '球', cylinder: '圆柱', cone: '圆锥' })[surface.kind] || '曲面';
      var row = el('div', 'plane-row');
      var main = el('div', 'plane-row-main');
      var title = el('div', 'plane-row-id');
      title.appendChild(document.createTextNode(
        surface.at || (surface.circles || []).join('') || label));
      title.appendChild(el('span', 'plane-row-tag', label));
      main.appendChild(title);
      var size = [];
      if (surface.radius != null) size.push('半径 ' + round4(surface.radius));
      if (surface.height != null) size.push('高 ' + round4(surface.height));
      if (size.length) main.appendChild(el('div', 'plane-row-val', size.join('　')));
      row.appendChild(main);
      box.appendChild(row);
    });

    var measures = figure.measures || {};
    if (measures.area != null) {
      var measureCard = el('div', 'info-card');
      measureCard.appendChild(el('h4', null, '表面积 / 体积'));
      measureCard.appendChild(infoRow('表面积', measures.area));
      measureCard.appendChild(infoRow('体积',
        measures.volume != null ? measures.volume : '（不闭合，不算）'));
      if (measures.volume == null) {
        measureCard.appendChild(el('div', 'info-hint',
          '这些面没围成一个封闭的立体，体积没有意义。'));
      }
      box.appendChild(measureCard);
    }
    // 这里原来还有一张「怎么看」的卡片（拖动转视角 / 滚轮缩放），
    // 跟面板底部那行状态提示是同一句话 —— 老师嫌右栏啰嗦，删掉了。
  }

  function infoRow(name, value) {
    var row = el('div', 'info-row');
    row.appendChild(el('span', null, name));
    row.appendChild(el('span', null, String(value)));
    return row;
  }

  /* 作图对话框：只剩立体，所以没有 kind 可切，只把输入框聚焦过去 */
  function openFigureDialog() {
    $('#figureNoteBox').style.display = 'none';
    var dialog = $('#figureDialog');
    if (!dialog.open) dialog.showModal();
    setTimeout(function () {
      var target = $('#figureShapes');
      if (target) target.focus();
    }, 30);
  }

  /* 立体图形直接画：一行一条。
     旧面板的「表达式（关于 x）」入口已经停用（函数 / 方程归平面面板），
     这里**必须给一句中文说明并指路**，不能让老师敲完表达式什么都没发生。 */
  function drawFromDialog() {
    var expression = ($('#figureExpression').value || '').trim();
    if (expression && !($('#figureShapes').value || '').trim()) {
      figureError('这块面板只剩「立体图形」了：函数、方程、平面几何都搬到了'
                  + '「几何画板（平面面板）」——点顶栏的「作图」，'
                  + '把「' + expression + '」写在下面的方程输入栏里就行。');
      return;
    }
    var lines = ($('#figureShapes').value || '').split('\n')
      .map(function (line) { return line.trim(); })
      .filter(function (line) { return line.length > 0; });
    if (!lines.length) {
      figureError('请至少写一个空间点，例如 A(0,0,0)；要画函数或方程请去顶栏「作图」的几何画板');
      return;
    }
    api('/api/figure', { method: 'POST',
                         body: { kind: 'solid', shapes: lines,
                                 title: ($('#figureTitleInput').value || '').trim() } })
      .then(function (res) {
        closeDialog('figureDialog');
        onFigure(res.figure);
      }).catch(function (error) { figureError(error.message); });
  }

  function figureError(text) {
    var box = $('#figureNoteBox');
    box.style.display = '';
    box.className = 'notice error';
    box.textContent = text;
  }

  /* 课堂资料：板书 / 课堂时间点 / 截图 存在哪，一键打开文件夹 */
  function openMaterials() {
    api('/api/classroom/materials').then(function (res) {
      var list = $('#matList');
      list.innerHTML = '';
      $('#matWorkspace').textContent = '工作区：' + (res.workspace || '');
      (res.folders || []).forEach(function (folder) {
        var row = el('div', 'mat-row');
        row.appendChild(el('span', 'mat-name', folder.label));
        row.appendChild(el('span', 'mat-path', folder.path));
        row.appendChild(el('span', 'mat-count',
          folder.count ? (folder.count + ' 个文件') : '还没有'));
        var open = el('button', 'btn', '打开文件夹');
        open.disabled = !folder.exists;
        if (!folder.exists) open.title = '这个文件夹还没有内容';
        open.onclick = function () {
          api('/api/open', { method: 'POST', body: { path: folder.path } })
            .catch(function (error) { toast(error.message, 'error'); });
        };
        row.appendChild(open);
        list.appendChild(row);
      });
      $('#materialsDialog').showModal();
    }).catch(function (error) { toast(error.message, 'error'); });
  }

  function onTimer(event) {
    if (event.action === 'stop') {
      stopTimer();
      return;
    }
    var total = Number(event.seconds || 0);
    if (total <= 0) return;
    stopTimer();
    state.timer = { endAt: Date.now() + total * 1000, label: event.label || '课堂计时',
                    total: total };
    var pill = $('#timerPill');
    $('#timerLabel').textContent = state.timer.label;
    pill.classList.remove('hidden', 'done');
    renderTimer();
    /* 原来 250ms 就改一次 DOM，整页跟着重排；改成 1 秒，而且内容没变就不写 */
    state.timerHandle = setInterval(renderTimer, 1000);
  }

  /* AI 头像：DeepSeek 同款蓝鲸鱼（内联 SVG，Win7 没有 emoji 字体） */
  /* 老师指定的头像图（内联 data URI，不额外发请求）。加载失败就退回下面那只鲸鱼，
     绝不让头像变成一个破图标。 */
  var AVATAR_DATA = 'data:image/webp;base64,UklGRkAKAABXRUJQVlA4IDQKAADQQQCdASoaARoBPp1OoEulpKMhp7VYkLATiWNu/HyY/eWZTfyvZ0cB8r/cP3S6VTpHx3zFR6e3zzb7S/9l6vvMP/Tv9ZuxB5m/289aP0nf5X0bOpV9Gvy4/aL/b70pNVs8w9nGP/tJ7N/YzLZiFcnr8JYrjy3g4S4tph2NsEjsqhkICiCR2VQyEBRBI7KoZCAogkdlUMgFY80OPQkamHY1WJ2ulP9AhrW8chctf/r0y2urx2ZqIN/tknzJOYd+VMTPAXhbXWB5sNM+F6EeTorutxE/gK1J6bjEQGyowU5bTFa6jdPd7YgPMEuNlyzZTgKFRQI48RGx1CRrCvZS1lCjDC+ZFgcuC0ukpUVxCAYnfQ/7Q1q9byyVNDBhryCQYnogdK4vANcy4todLZry98SxzHuMO1ePc5ucjKsT/prcbQ2jfYk75wQebhMHYdPbiPaPc9LGfDBetsHoF2X2YzwNXmANqq1NQtMpv18AXVTJ3d1u+Q5gun8kGR49fIbwHAAHgUMbclCjsiqXHyZFk9qJsbVNAoowciGAVGiegZzqtaKCN5R+IjdxWvJaWFNAqCEBWd9dBpYZS0gwaMu4kkoDUwqzFx3bwJ9PQnlKOwTKuIbS0gG1NmHY2wT3WsKnsifU6gxFiQNrKPma+V9gH8ze+C6mVEls/y5VDIQFEEjsqhkICiCR2VQyEBRBI7KoZCAogkdlUHgAAP7+9egAAAAAEWLbYC9gJnb7B4tsnUpYfRvOtJ00I4Ac5OWmWLtIZzmyrkzbGFH47fMieHYGtr3YBxn/RBCCfKLOOcjpTljnFhadFUtV6/Pd/BNeV0gBybNrlsgG0U7JA+h83D5oM+JYnmLBYrkHDheZgSyFcOosfjzyeuKHa1lZ0xWszyeNsjQNFechQPf/RS++28gLUWsSxmUABGrP+9ZyFBTXAUUbu8TiU+bKGT4PO0kmqciD+zl2tRHGe6So0pys0Qo1QG74EUPuuRz+B/nDXFzdmJHFctF3b1Z+7PBQPwu8DrPouLuB/pMgO4mSY9Fg1hf1uU7/EnvJ77QHdJNUIhanuQ1ITaYaPe9xXBPEqwAQzTqjM6zhQiNcgpExKQVkPEUspFPsyMA5OLYu78Jr8i50KD1f1k/BISd89+hCXeksHmkmPtXLfKtZC+MLWmxBZ/WziogrzucOFbZUVuW7PS3gu0YtalniIy9zFn/SGP4xXfFnidhvkcAWSpizA/gw16HSD6vcp2z2ay8v6KxRbO674NpememTE6OJtAFDHzFSO22zst2CwlfZLgCVsHEM0+f73wOzvuyEq9MUehdtICWgXtC6xjcSBRMWFLUZ3KgtJct65SIR1CQKZrNuMqMU9QSvZ8JUuytLkQ+TiM5uNeQCyJgUgxXMBmDjbMWVwCHBKVeWTKSJ72XNg80Yq94FLirqKAK+ZkoQE1ueaIPACiRnJYM0Gv/KZTE9xkCHiyvDpbs0wtuMXksx5Jbbwatq/ldDDB+mpKkKPRDvdmK2/WhUK3T+tysofokvUb+zYzknVSUrr/mXlnIYD2hhk9/ZAcaGFDr2RcTqPOiJECT9Bj3ON5SFUgxXkAI0Vvr2mA69LMVJLlJhaOp94JFIR9oSO36sUNofbSUW/fGdrdp9szk0uEvjyZ7EAuJXuM3Y/RFB9bivsrq5HoJqjq4FkHaW/LiM1xg5yk3fDAuzBeoqR235Yegnz5HtzfLradzwBTw3VUDzHl1pdZBa+GUfM1unOX03oSveHRCpOKvxF5F2QBIsDeWstmfobSK+Qe+BAccVN5fH2ZABjwytPLnKY8F/hit1IYVM+ifr9TJP67BjHRh4VI06O2JpgGpVomiifeq7ogUpYdP5J6PkABakFRFPRm4wUCHz42Wlap+w7vVJROsrKleBNmy+CeKd8fKjmXsVzziXZJPf9SE/m76QEmh0hvY1sfEZPIrXH1XfcVXjLJgdtHzH2SI2VRSGG2TEUME+x8g0DWU6/3SBblCLa8TjLgg58hNZ1R0PpVT3l8PRzQ/Ce/LnJIibhwWdD98gPxTItzsGXqNZB2xFNgbMhEKQ6xKReyKmianJfs+Ie7rmk7bxG8nMlRrrut6okFO3L/M3TUaWuuNEzgzzF9K8FjuTfqhrgPCCF0OeaTik53zuChV+Z4LwBWe+pUQUoj/T2SrnXH7wA2uKLpCJERvpC2s2aBL1lJqLUskQFx4y6Mr5bNfn8vQ0q27y01xz9DHTqURwA/pkMcoxgTXL4/bvHedmbfIUNgoi+bYgZ+Ed/KnaFIk3vgC+iMx30u+9jiuqPh6ksU/RgOyy24SCBOwPXpyKLxxcsA8cE40cPz4F8AN5jN32d/YcB/IkP+tbUsl9htt2/D5z3ZP75u6A4U9smgzpWdIhJ7rJ/hwXs25eRK8XcttWPyvDw7v4mvXvkMr7K83DRdcUdoc6+50q8bx//Y5aRec4XyaSFQML6FlZkM6K8IFuIZiwTeQfBHjbQPSO6dDb8G4NYssMazlsXuhXM2eTuaUyAmLRHMErUlp4z3cwJK+ZdvlJbc5+1seoGULci6l3L8FX/1M43onEQYjM0AkxWsqg20CdfPy9NSmeDGmG29MEljyj589bHSzgCr6IC7xj3kxor6hABKXezAk6NuFZ9r3b9KQ73s0V8dR7mxHms1F+I09k9ViFLqCFEvhOiTSIwEg1PlaYf98t7fo/m+LVk8QBex0pfE5S1Lhc0F6b8Fba5PrrKKyPPg9hgWj/SyS0yJk/v2yj3UOuSfSnSGBvaQh8RZFZ9crZFAu0/NF7HzQq6pdKNRg+IiAHeaCfA9vdsRL+gTQ0Q9IzQ7J5112GkyYlkHXENt9yH4raBcxVVD9HHJWn5DEGJi511AsqvxqmOxFdvjSN7mkNjjmlwAlBmDZdbGLKWHB9OUff24/Ov+tpxpNTdwCa5nm/sSZFeVKXmxOt+POjpYoFbTTq7Vo0EDAwoM6F/yzzk1ilAtz+77x3RzDbmMIY4B1ytwJfpWcQfXQSH4wyRABYVmHBz9tIwe0bWmlFN3bf9qTZ5zqWwyNzO9UFi1Zy7nGRPncdr1A70sQK86Hhhri2idt5SgtlmV5anVnNOckDKVZ5u/CiAWkJrE0lx4t36x9PBVARoZUkI0Z0xsC1GWGo1fndTN4rrZO0eYF7lqV9pXLyX1sXvlaz7la97V7gxWpIIf/wjzJ3WFry/ihhX1/Y/bW4clCMAOZDXqnv68GwZH4ftzFvlu7ZjzJxfcywIdUQtw6iop+mza4zqTCwn6MdOJ9q9+YLH699aVDK7DdyJ6sZO/ABjBuSCPVpc+Ay1lnBx56nR6Tj0Or0ayXhawRVBsMyvDfNFKlfo74/UH6dny5rwRTN7CTq8Xe5ap8fJiSmm3UftjQO8zIbeSoqeK3MpHUTFzg8hMx6O8uLybodzVzqGs8VwaCLVpkJZRmmANk6eGp5OXY+bIdva+QFW73DgjdeWAAAAAAAAAAAAAAAAA==';

  function avatarWhale() {
    var box = el('div', 'avatar');
    var img = document.createElement('img');
    img.className = 'avatar-img';
    img.alt = '';
    img.src = AVATAR_DATA;
    img.onerror = function () {
      // 图加载不出来（老浏览器不认 WebP 之类）：换成原来那只内联 SVG 鲸鱼，
      // 绝不把头像留成一个破图标。
      var legacy = avatarLegacy();
      box.className = legacy.className;
      box.innerHTML = legacy.innerHTML;
    };
    box.appendChild(img);
    return box;
  }

  function avatarLegacy() {
    var box = el('div', 'avatar');
    box.innerHTML = '<svg viewBox="0 0 32 32" width="20" height="20" aria-hidden="true">'
      + '<path d="M4 19c0-5 4.5-9 10-9 3.2 0 5.6 1.2 7.2 3.1 1.6-.4 2.6-1.4 3-2.6.4 1.9-.3 3.6-1.7 '
      + '4.7.5.8.8 1.7.8 2.7 0 1.1-.4 2-1.1 2.7 1.3.2 2.4-.1 3.3-.9-.3 1.9-1.8 3.2-3.9 3.2-1.6 '
      + '0-2.9-.7-3.7-1.8-1.2.5-2.5.8-3.9.8-5.5 0-10-3.7-10-8.6z"'
      + ' fill="#4d6bfe"/>'
      + '<circle cx="12" cy="17" r="1.5" fill="#fff"/>'
      + '</svg>';
    return box;
  }

  function renderTimer() {
    if (!state.timer) return;
    var left = Math.max(0, Math.round((state.timer.endAt - Date.now()) / 1000));
    var text = (left >= 60)
      ? (Math.floor(left / 60) + ':' + ('0' + (left % 60)).slice(-2))
      : (left + ' 秒');
    $('#timerValue').textContent = text;
    $('#boardTimer').textContent = text;
    $('#boardTimer').classList.toggle('hidden', $('#board').classList.contains('hidden'));
    if (left <= 0) {
      clearInterval(state.timerHandle);
      state.timerHandle = null;
      state.timer = null;
      $('#timerValue').textContent = '时间到';
      $('#timerPill').classList.add('done');
      $('#boardTimer').textContent = '时间到';
      beep();
      toast('时间到：' + ($('#timerLabel').textContent || ''), 'warn');
    }
  }

  function stopTimer() {
    stopRing();                       // 停计时立刻静音
    if (state.timerHandle) clearInterval(state.timerHandle);
    state.timerHandle = null;
    state.timer = null;
    $('#timerPill').classList.add('hidden');
    $('#timerPill').classList.remove('done');
    $('#boardTimer').classList.add('hidden');
  }

  /* 倒计时结束的提示音；浏览器不允许时静默跳过 */
  /* 倒计时结束的**短**音效：两声"叮"，不到半秒。关界面/停计时立刻静音。 */
  var ringContext = null;

  function stopRing() {
    if (ringContext) {
      try { ringContext.close(); } catch (error) { /* 已经关了 */ }
      ringContext = null;
    }
  }

  function beep() {
    try {
      var AudioCtx = window.AudioContext || window.webkitAudioContext;
      if (!AudioCtx) return;
      stopRing();
      var context = new AudioCtx();
      ringContext = context;
      [0, 0.18].forEach(function (offset) {
        var osc = context.createOscillator();
        var gain = context.createGain();
        osc.type = 'sine';
        osc.frequency.value = 1320;
        osc.connect(gain);
        gain.connect(context.destination);
        gain.gain.setValueAtTime(0.0001, context.currentTime + offset);
        gain.gain.exponentialRampToValueAtTime(0.22, context.currentTime + offset + 0.01);
        gain.gain.exponentialRampToValueAtTime(0.0001, context.currentTime + offset + 0.12);
        osc.start(context.currentTime + offset);
        osc.stop(context.currentTime + offset + 0.14);
      });
      // 响完自己收掉，别留一个开着的音频通道
      setTimeout(function () {
        if (ringContext === context) stopRing();
      }, 600);
    } catch (error) { /* 静默 */ }
  }

  /* ------------------------------------------------- 悬浮工具栏（可选） */
  /* 服务端记录「网页还开着」，悬浮窗靠它决定要不要把整个程序收掉（老师关了网页就一起退）。
     同一次心跳顺手取回工具栏排队的命令，在这里执行 —— 执行的都是网页本来就有的功能，
     工具栏上没有第二套实现。 */
  function startFloatWatch() {
    var tick = function () {
      api('/api/float/ping', {
        method: 'POST',
        body: { sessionId: (state.session && state.session.id) || '' }
      }).then(function (res) {
        (res.commands || []).forEach(runFloatCommand);
        if (res.uiStamp && state.uiStamp && res.uiStamp !== state.uiStamp) noticeUiUpdate();
      }).catch(function () { /* 服务暂时不可用：页面上已经有连接提示了，下一拍再试 */ });
    };
    tick();
    state.floatHandle = setInterval(tick, FLOAT_PING_MS);
    /* 360 / Chrome 会把后台标签页的定时器冻住（甚至降到 1 分钟一次）。
       老师切回来时要**立刻**补一次心跳，别等服务端那边超时、也别让工具栏以为网页没了。
       这三个事件覆盖：切回本标签页、窗口重新获得焦点、从后退缓存里恢复。 */
    ['visibilitychange', 'focus', 'pageshow'].forEach(function (name) {
      window.addEventListener(name, function () {
        if (name !== 'visibilitychange' || !document.hidden) tick();
      });
    });
  }

  /* 服务端的前端文件改过了（`server.ui_stamp()`），而本页是**早就打开**的旧 JS。
     不自动刷新——老师可能正在打字，一刷新就没了；把标题栏的版本号变成明显的"点我刷新"。 */
  function noticeUiUpdate() {
    var badge = $('#versionBadge');
    if (!badge || badge.getAttribute('data-stale')) return;
    badge.setAttribute('data-stale', '1');
    badge.textContent = '界面已更新 · 点我刷新';
    badge.className = (badge.className || '') + ' version-stale';
    badge.title = '本机的前端文件已经改过，刷新一下才是最新界面';
    badge.style.cursor = 'pointer';
    badge.onclick = function () { window.location.reload(); };
  }

  function runFloatCommand(command) {
    if (!command) return;
    switch (command.action) {
      case 'board':                                    // 抽学号 / 大屏
        if (command.event) onBoard(command.event);
        return;
      case 'ask_timer':                                // 「计时」按钮：弹出时长选择
        openTimerDialog();
        return;
      case 'physics':                                  // 「物理」按钮：打开沙盒面板
        if (window.W7Sandbox && window.W7Sandbox.openPanel) { window.W7Sandbox.openPanel(); return; }
        if (window.W7Physics) { window.W7Physics.open(); return; }
        if (window.W7Practice) { window.W7Practice.open(); return; }
        toast('界面脚本没加载成功，按 Ctrl+F5 强制刷新一下页面', 'error');
        return;
      case 'phet':                                     // 「PhET」按钮：打开 PhET 互动仿真面板
        if (window.W7Phet) { window.W7Phet.open(); return; }
        toast('界面脚本没加载成功，按 Ctrl+F5 强制刷新一下页面', 'error');
        return;
      case 'bio':                                      // 「生物」按钮：打开生物过程动画（面板里还有 PhET 生物仿真的入口）
        if (window.W7Bio && window.W7Bio.openPanel) { window.W7Bio.openPanel(); return; }
        if (window.W7Phet) { window.W7Phet.open('生物'); return; }
        toast('界面脚本没加载成功，按 Ctrl+F5 强制刷新一下页面', 'error');
        return;
      case 'chem':                                     // 「化学」按钮：打开化学实验台
        var chemButton = document.getElementById('btnChem');
        if (chemButton) { chemButton.click(); return; }
        toast('界面脚本没加载成功，按 Ctrl+F5 强制刷新一下页面', 'error');
        return;
      case 'figure':                                   // 「数学函数 / 几何画板」：打开作图面板
        // 平面已经替换了函数/方程/平面几何：这两颗按钮直接开平面面板
        if (window.W7Plane && window.W7Plane.open) { window.W7Plane.open(); return; }
        openFigurePanel(command.kind);
        return;
      case 'calc':                                     // 「计算器 / 换算」：打开计算器面板
        openCalc(command.tab);
        return;
      case 'translate':                                // 「翻译查词」：打开翻译 / 词典面板
        openExtraPanel('translate');
        return;
      case 'memo':                                     // 「备忘录」：打开备忘录面板
        openExtraPanel('memo');
        return;
      case 'calendar':                                 // 「日历」：打开日历面板（节假日 / 调休 / 农历）
        openExtraPanel('calendar');
        return;
      case 'attach_image':                             // 「截图给AI」：放进待发送的图片
        addImagePath(command.path);
        toast('截图已加入待发送的图片', 'ok');
        return;
      case 'toast':
        toast(command.message || '', command.level || 'ok');
        return;
      default:
        return;
    }
  }

  /* 翻译查词 / 备忘录 / 日历这三个面板都在 extras.js 里（app.js 已经很大了）。
     它们要调的服务端接口由服务端那一半提供，前端只负责把面板叫出来、把状态同步过去。
     脚本没加载出来时**明确说清楚**，不能让老师点了按钮什么都没发生。 */
  function openExtraPanel(name, payload) {
    if (!window.W7Extras) {
      toast('界面脚本 extras.js 没加载成功，按 Ctrl+F5 强制刷新一下页面', 'error');
      return;
    }
    if (name === 'translate') W7Extras.openTranslate(payload);
    else if (name === 'memo') W7Extras.openMemo(payload);
    else if (name === 'calendar') W7Extras.openCalendar(payload);
  }

  function onTurn(event) {
    var ctx = state.current;
    var message = event.message || {};
    var calls = message.tool_calls || [];
    if (ctx) {
      // 一轮结束：这一段思考和这一段正文都收尾。后面（工具跑完、下一轮开始）
      // 产生的思考和正文会**另起节点**排在下面——这就是"每轮思考各占一行"的关键。
      streamBreak(ctx);
      if (message.usage) {
        ctx.usage.style.display = '';
        var line = usageLine({ usage: message.usage, cost: message.cost });
        ctx.usage.innerHTML = '';
        Array.prototype.slice.call(line.children).forEach(function (child) { ctx.usage.appendChild(child); });
      }
    }
    var usage = message.usage || {};
    state.run.prompt = usage.prompt_tokens;
    state.run.completion = usage.completion_tokens;
    state.run.hit = usage.cache_hit_tokens;
    state.run.miss = usage.cache_miss_tokens;
    state.run.reasoning = usage.reasoning_tokens || state.run.reasoning;
    state.run.cost = (state.run.cost || 0) + (message.cost || 0);
    if (event.totals) {
      if (!state.session.usage) state.session.usage = {};
      state.session.usage = event.totals;
    }
    renderInspector();
  }

  function finishFoldQuiet() {
    var ctx = state.current;
    if (!ctx) return;
    if (ctx.textBlock) ctx.textBlock.classList.remove('caret');
    if (ctx.thinkStep) thinkSettle(ctx.thinkStep);
  }

  function onNotice(event) {
    var ctx = state.current;
    if (ctx) {
      var node = el('div', 'notice' + (event.level === 'error' ? ' error' : ''), event.message);
      streamAppend(ctx, node);
    }
    if (event.level === 'warn') toast(event.message, 'warn');
  }

  function onError(event) {
    var ctx = state.current;
    if (ctx) {
      finishFoldQuiet();
      ctx.bubble.classList.add('error-state');
    }
    var wrap = el('div', 'msg error');
    wrap.appendChild(avatarWhale());
    var bubble = el('div', 'bubble');
    bubble.appendChild(el('div', 'notice error', event.message));
    // 这一轮什么都没写出来，没有“继续生成”可点，给一个直接重试的入口
    var retry = el('button', 'btn small', '重新生成');
    retry.onclick = function () { regenerate(); };
    bubble.appendChild(retry);
    wrap.appendChild(bubble);
    if (!ctx) $('#thread').appendChild(wrap);
    else ctx.bubble.appendChild(bubble.firstChild);
    toast(event.message, 'error');
  }

  function removeContinueRows() {
    var rows = $('#thread').querySelectorAll('.continue-row');
    for (var index = rows.length - 1; index >= 0; index--) {
      if (rows[index].parentNode) rows[index].parentNode.removeChild(rows[index]);
    }
  }

  /* 这次运行没写完（被熔断、轮次上限、长度上限或手动停止打断）时给一个一键继续 */
  function continueRow() {
    var row = el('div', 'continue-row');
    var button = el('button', 'btn continue-btn');
    button.innerHTML = '<span class="ico-slot" data-icon="play"></span>继续生成';
    button.title = '接着上一条没写完的回答继续（不用自己打字）';
    button.onclick = function () {
      removeContinueRows();
      send('继续');
    };
    if (window.W7Icons) W7Icons.hydrate(button);
    row.appendChild(button);
    return row;
  }

  function showContinue() {
    var ctx = state.current;
    if (!ctx || ctx.continueRow) return;
    ctx.continueRow = continueRow();
    ctx.bubble.appendChild(ctx.continueRow);
    scrollToBottom(true);
  }

  function onDone(event) {
    if (event.session && state.session) {
      state.session.title = event.session.title || state.session.title;
      if (event.usage) state.session.usage = event.usage;
    }
    if (event && event.canContinue) showContinue();
    renderInspector();
  }

  /* ------------------------------------------------------------- 状态切换 */
  function setStreaming(active) {
    state.streaming = active;
    if (active) removeContinueRows();        // 新一轮开始，上一轮遗留的“继续”按钮收掉
    else finishFoldQuiet();                  // 停止/出错时也要把折叠栏收尾
    // 这一轮结束了但作图面板还在等：说清楚"它没画"，别让老师一直盯着转圈
    if (!active && figureWaiting) {
      figureWaiting = false;
      setFigureStatus('AI 这一轮没有画图，看看对话里它说了什么', 'warn');
    }
    // 计算器里"让 AI 解方程/因式分解"也在等同一轮：把回答捞出来显示到面板上。
    // 必须赶在下面把 state.current 清空之前取。
    if (!active && aiCapture) finishAiCapture();
    $('#btnSend').classList.toggle('hidden', active);
    $('#btnStop').classList.toggle('hidden', !active);
    $('#input').disabled = active;
    setStatus(active ? '生成中' : '就绪', active ? 'busy' : '');
    if (!active) {
      state.current = null;
      state.controller = null;
    }
    renderInspector();
  }

  function setStatus(text, kind) {
    var pill = $('#statusPill');
    pill.textContent = text;
    pill.className = 'pill ' + (kind || '');
  }

  /* 停止：**点了就立刻停**，不等工具调用跑完（老师："不管是不是长任务长命令都要能点了停止
     就立即打断，不要让它等一会等工具调用完"）。

     做法就三步：① 发一次**强制停止**（服务端立刻把这次运行从登记表里摘掉，下一条消息马上能发，
     那个线程即使还在收尾，它的写盘也已经作废）；② 直接 abort 掉前端的流，不等 stopped 事件；
     ③ 界面立刻放开。

     以前是"先普通停止 -> 等 9 秒 -> 再强制 -> 再等 20 秒放弃并 abort"，那两个定时器**会跨到
     下一次运行里**去 abort 新的流 —— 表现就是老师报的"连点几下停止，下一次对话刚说两句就被停"。
     定时器删掉，这类 bug 从结构上没有了。 */
  function stop() {
    if (!state.streaming) return;
    var runId = state.runId;
    setStatus('正在停止…', 'busy');
    api('/api/stop', { method: 'POST', body: { runId: runId, force: true } })
      .catch(function () { });
    if (state.controller) {
      try { state.controller.abort(); } catch (error) { /* 已经断了就算了 */ }
    }
    unlockAfterStop();
    toast('已停止', 'warn');
  }

  /* 兼容老调用点：定时器已经不需要了，留着空函数免得别处调用报错。 */
  function stopWatchdog() {
  }

  function clearStopTimers() {
    if (state.stopForce) { clearTimeout(state.stopForce); state.stopForce = null; }
    if (state.stopGiveUp) { clearTimeout(state.stopGiveUp); state.stopGiveUp = null; }
  }

  /* 服务端说停下来了（或流断了）：把界面恢复成"可以继续输入" */
  function unlockAfterStop() {
    clearStopTimers();
    if (!state.streaming) return;
    setStreaming(false);
    setStatus('已停止', '');
    renderSessions();
  }

  function onStopped() {
    unlockAfterStop();
  }

  function nowText() {
    var date = new Date();
    var pad = function (value) { return value < 10 ? '0' + value : String(value); };
    return date.getFullYear() + '-' + pad(date.getMonth() + 1) + '-' + pad(date.getDate()) + ' ' +
           pad(date.getHours()) + ':' + pad(date.getMinutes()) + ':' + pad(date.getSeconds());
  }

  /* ------------------------------------------------------------- 上下文操作 */
  function addFileContext(path) {
    if (!state.session) return;
    api('/api/context', { method: 'POST', body: { id: state.session.id, kind: 'file', source: path } })
      .then(function () {
        toast('已加入上下文：' + shortPath(path), 'ok');
        return openSession(state.session.id);
      }).catch(function (error) { toast(error.message, 'error'); });
  }

  function addUrlContext(url) {
    if (!state.session) return;
    toast('正在读取页面…');
    api('/api/context', { method: 'POST', body: { id: state.session.id, kind: 'url', source: url } })
      .then(function () {
        toast('已加入上下文', 'ok');
        return openSession(state.session.id);
      }).catch(function (error) { toast(error.message, 'error'); });
  }

  function uploadFiles(files) {
    if (!state.session || !files || !files.length) return;
    Array.prototype.slice.call(files).forEach(function (file) {
      if (/^image\//.test(file.type || '')) {
        uploadImageFile(file);
        return;
      }
      var reader = new FileReader();
      reader.onload = function () {
        var dataUrl = String(reader.result || '');
        var base64 = dataUrl.slice(dataUrl.indexOf(',') + 1);
        api('/api/context', {
          method: 'POST',
          body: { id: state.session.id, kind: 'file', name: file.name, data: base64 }
        }).then(function () {
          toast('已加入上下文：' + file.name, 'ok');
          return openSession(state.session.id);
        }).catch(function (error) { toast(error.message, 'error'); });
      };
      reader.readAsDataURL(file);
    });
  }

  /* ---------------------------------------------------------- 识图：待发送图片 */
  function uploadImageFile(file) {
    if (!file) return;
    if (!/^image\//.test(file.type || '')) {
      toast('只支持图片文件（png/jpg/gif/webp/bmp）', 'warn');
      return;
    }
    if (file.size > 8 * 1024 * 1024) {
      toast('图片超过 8MB，请先压缩或裁剪后再发', 'error');
      return;
    }
    var reader = new FileReader();
    reader.onload = function () {
      var dataUrl = String(reader.result || '');
      var base64 = dataUrl.slice(dataUrl.indexOf(',') + 1);
      api('/api/upload_image', { method: 'POST', body: { name: file.name || '截图.png', data: base64 } })
        .then(function (res) {
          state.images.push({ path: res.path, name: res.name, mime: res.mime,
                              bytes: res.bytes, width: res.width, height: res.height });
          renderImageBar();
          updateTokenHint();
          toast('已添加图片：' + res.name, 'ok');
        }).catch(function (error) { toast(error.message, 'error'); });
    };
    reader.readAsDataURL(file);
  }

  function addImagePath(path) {
    path = String(path || '').trim().replace(/^"|"$/g, '');
    if (!path) return;
    if (!/\.(png|jpe?g|gif|webp|bmp)$/i.test(path)) {
      toast('不是支持的图片格式（png/jpg/gif/webp/bmp）', 'warn');
      return;
    }
    api('/api/image_info?path=' + encodeURIComponent(path)).then(function (res) {
      state.images.push({ path: res.path, name: res.name, mime: res.mime,
                          bytes: res.bytes, width: res.width, height: res.height });
      renderImageBar();
      updateTokenHint();
    }).catch(function (error) { toast('无法读取图片：' + error.message, 'error'); });
  }

  function renderImageBar() {
    var bar = $('#imageBar');
    bar.innerHTML = '';
    if (!state.images.length) {
      bar.classList.add('hidden');
      return;
    }
    bar.classList.remove('hidden');
    state.images.forEach(function (item, index) {
      var chip = el('div', 'image-chip');
      var img = document.createElement('img');
      img.src = imageUrl(item.path);
      img.alt = item.name || '';
      chip.appendChild(img);
      var meta = el('div', 'i-meta');
      meta.appendChild(el('div', 'i-name', item.name || shortPath(item.path)));
      meta.appendChild(el('div', 'i-size', item.width
        ? (item.width + '×' + item.height + ' · ' + fmtBytes(item.bytes))
        : fmtBytes(item.bytes)));
      chip.appendChild(meta);
      var remove = iconButton(null, 'close', 12, '移除这张图片');
      remove.onclick = function () {
        state.images.splice(index, 1);
        renderImageBar();
        updateTokenHint();
      };
      chip.appendChild(remove);
      bar.appendChild(chip);
    });
  }

  /* ------------------------------------------------------------- 设置 */
  /* 与服务端 normalize_endpoint 等价的预览（服务端仍是权威实现） */
  function effectiveEndpoint(value) {
    var url = String(value || '').trim().replace(/\/+$/, '');
    if (!url) return state.config.endpoint || '';
    if (/\/chat\/completions$/i.test(url)) return url;
    if (/\/(v1|beta|api)$/i.test(url)) return url + '/chat/completions';
    return url + '/chat/completions';
  }

  function updateEndpointHint() {
    var box = $('#endpointEffective');
    var raw = $('#setEndpoint').value.trim();
    var effective = effectiveEndpoint(raw);
    if (!effective) {
      box.style.display = 'none';
      return;
    }
    box.style.display = '';
    box.textContent = '实际请求地址：' + effective +
      (raw && effective !== raw ? '（会自动补全 /chat/completions，不用手写）' : '');
  }

  function openSettings() {
    var config = state.config;
    $('#setModel').value = config.model || '';
    $('#setEndpoint').value = config.baseUrlRaw || '';
    $('#setTemp').value = config.temperature == null ? '' : config.temperature;
    $('#setMaxTokens').value = config.maxTokens || 0;
    // 注意：这里的兜底数字必须跟 core.WEBUI_DEFAULTS 一致。以前写的是 8 和 900，
    // 结果老师一打开设置再保存，就把"8 轮 / 900 秒"这两个旧默认值焊死进配置里了。
    $('#setRounds').value = config.maxToolRounds || 1024;
    fillEffortSelect($('#setEffort'), config.reasoningEffort || '', '');
    $('#setEffortHint').textContent = effortHint(config.reasoningEffort || '');
    $('#setWorkspace').value = config.workspace || '';
    $('#setOutside').checked = !!config.allowOutsideWorkspace;
    // 开机自启：以注册表里的实际状态为准（配置只表示"希望开着"）
    var autostart = config.autostart || {};
    $('#setAutostart').checked = autostart.available ? !!autostart.on : false;
    $('#setAutostart').disabled = autostart.available === false;
    $('#setAutostartNote').textContent = autostart.note ||
      '关掉之后开机不会自己起来，随时可以在这里再打开。';
    $('#setTools').checked = !!config.toolsEnabled;
    $('#setDesktop').checked = config.allowDesktopControl !== false;
    // 熔断默认关闭：只有明确存过 true 才勾上
    $('#setLoopBreaker').checked = config.loopBreaker === true;
    $('#setDesktopConfirm').checked = config.confirmDesktopInput === true;
    $('#setMessageConfirm').checked = config.confirmMessages === true;
    $('#setMaxImages').value = config.maxImages == null ? 256 : config.maxImages;
    $('#setCommandConfirm').checked = config.confirmHighRiskCommands === true;
    $('#setSelfRestart').checked = config.allowSelfRestart !== false;
    $('#setSystem').value = config.systemPrompt || '';
    var price = config.price || {};
    $('#setPriceHit').value = price.cache_hit;
    $('#setPriceMiss').value = price.cache_miss;
    $('#setPriceOut').value = price.output;
    $('#setHost').value = config.host || '127.0.0.1';
    $('#setPort').value = config.port || 8770;
    $('#setToken').value = '';
    $('#setLoopLimit').value = config.loopRepeatLimit || 3;
    $('#setMaxSeconds').value = config.maxRunSeconds == null ? 0 : config.maxRunSeconds;
    $('#setMaxSubagents').value = config.maxSubagents == null ? 4 : config.maxSubagents;
    $('#setSubagentRounds').value = config.subagentMaxRounds == null ? 256 : config.subagentMaxRounds;
    $('#setAutoCompact').checked = config.autoCompact !== false;
    $('#setCompactRatio').value = config.compactThresholdRatio || 0.8;
    var search = config.search || {};
    $('#setSearchEngine').value = search.engine || 'auto';
    $('#setSearchEndpoint').value = search.endpoint || '';
    $('#settingsNote').style.display = 'none';
    $('#apiKeyResult').style.display = 'none';
    // API Key 只显示掩码，输入框留空
    $('#setApiKey').value = '';
    $('#apiKeyState').textContent = config.hasApiKey
      ? ('当前密钥：' + config.apiKeyMasked + (config.apiKeyFromProfile ? '（来自模型配置块）' : '（来自 config\\agent.json）'))
      : '当前没有配置密钥，填写后保存即可';
    var header = config.authHeader || 'Authorization';
    var known = ['Authorization', 'api-key', 'x-api-key'];
    $('#setAuthHeader').value = known.indexOf(header) >= 0 ? header : '__custom__';
    $('#setAuthHeaderCustom').classList.toggle('hidden', known.indexOf(header) >= 0);
    $('#setAuthHeaderCustom').value = known.indexOf(header) >= 0 ? '' : header;
    $('#setThinking').value = config.thinking || '';
    fillNotifyFields(config.notify || {});
    // 悬浮工具栏的配置存在 config\float.json 里，单独取一次
    api('/api/float/config').then(function (res) {
      var floatConfig = res.config || {};
      $('#setQqDir').value = floatConfig.qq_download_dir || '';
      $('#setFloatWhiteboard').value = floatConfig.whiteboard || '';
      $('#setFloatDisplay').value = floatConfig.display || '';
      $('#setFloatQq').value = floatConfig.qq || '';
    }).catch(function () { /* 读不到就留空，不影响其它设置 */ });
    if (config.endpointOverridden) {
      var note = $('#endpointEffective');
      note.style.display = '';
      note.textContent = '注意：接口地址被 config\\webui.json 里的 endpoint 覆盖了，改 agent.json 的 base_url 不会生效。';
    }
    updateEndpointHint();
    $('#settingsDialog').showModal();
  }

  /* 认证方式：Bearer 之外还要支持企业网关的 api-key / 自定义头 */
  function currentAuthHeader() {
    var value = $('#setAuthHeader').value;
    if (value === '__custom__') return ($('#setAuthHeaderCustom').value || '').trim();
    return value;
  }

  /* 通知配置：密钥/授权码只显示"已保存"，输入框一律留空（留空=不改） */
  function fillNotifyFields(notify) {
    $('#setSmtpHost').value = notify.smtpHost || '';
    $('#setSmtpPort').value = notify.smtpPort || '';
    $('#setSmtpSsl').value = notify.smtpSsl === false ? '0' : '1';
    $('#setSmtpUser').value = notify.smtpUser || '';
    $('#setSmtpPassword').value = '';
    $('#setSmtpPassword').placeholder = notify.hasSmtpPassword ? '已保存（留空 = 不修改）' : '16 位授权码';
    $('#setMailTo').value = notify.mailTo || '';
    $('#setWebhookKind').value = notify.webhookKind || 'wecom';
    $('#setWebhookUrl').value = '';
    $('#setWebhookUrl').placeholder = notify.hasWebhookUrl
      ? ('已保存：' + notify.webhookUrlMasked + '（留空 = 不修改）')
      : '留空 = 不修改；填了才能推送';
    var status = [];
    status.push('邮件：' + (notify.mailStatus || '未配置'));
    status.push('群推送：' + (notify.webhookStatus || '未配置'));
    $('#notifyStatus').textContent = status.join('　|　');
  }

  /* 只把老师真的填了的字段发出去：留空表示"不改"，
     否则每次保存设置都会把手工填在 config\webui.json 里的密钥清掉
     （服务端是深合并，字段不出现就保持原值）。 */
  function collectNotify() {
    var patch = {
      smtp_host: $('#setSmtpHost').value.trim(),
      smtp_port: Number($('#setSmtpPort').value) || 465,
      smtp_ssl: $('#setSmtpSsl').value !== '0',
      smtp_user: $('#setSmtpUser').value.trim(),
      mail_to: $('#setMailTo').value.trim(),
      webhook_kind: $('#setWebhookKind').value || 'wecom'
    };
    var password = $('#setSmtpPassword').value;
    if (password) patch.smtp_password = password;
    var url = $('#setWebhookUrl').value.trim();
    if (url) patch.webhook_url = url;
    return patch;
  }

  function testConnection() {
    var box = $('#apiKeyResult');
    box.style.display = '';
    box.className = 'notice';
    box.textContent = '正在测试…（会真的调用一次接口的 /models）';
    var button = $('#apiKeyTest');
    button.disabled = true;
    // 先把输入框里的新 Key 存下来再测，避免"测的是旧 Key"
    var patch = { settings: {} };
    var typed = $('#setApiKey').value.trim();
    if (typed) patch.settings.api_key = typed;
    patch.settings.auth_header = currentAuthHeader();
    patch.settings.endpoint = $('#setEndpoint').value.trim();
    api('/api/settings', { method: 'POST', body: patch }).then(function (res) {
      state.config = res.config;
      $('#setApiKey').value = '';
      $('#apiKeyState').textContent = state.config.hasApiKey
        ? ('当前密钥：' + state.config.apiKeyMasked)
        : '当前没有配置密钥';
      return api('/api/agent/test', { method: 'POST', body: {} });
    }).then(function (res) {
      box.className = 'notice' + (res.ok ? '' : ' error');
      var lines = [];
      lines.push(res.ok ? ('连接正常：' + res.message) : ('连接失败：' + (res.error || '未知错误')));
      if (res.hint) lines.push(res.hint);
      lines.push('实际请求：' + res.endpoint);
      lines.push('发送的认证头：' + res.authHeader);
      box.textContent = lines.join('\n');
      if (res.ok) refreshModels(true);
    }).catch(function (error) {
      box.className = 'notice error';
      box.textContent = '测试失败：' + error.message;
    }).then(function () { button.disabled = false; });
  }

  /* 开机自启动诊断：把服务端查到的每一条事实原样列出来，再给一个一键修复。
     老师的机器上"到底有没有生效"以前完全看不到（只在后台日志里），所以这里逐条讲清楚：
     程序在哪、路径有没有空格、注册表里写的什么、启动文件夹有没有快捷方式、是不是被禁用了。 */
  function autostartLevelLabel(level) {
    if (level === 'ok') return '正常';
    if (level === 'warn') return '注意';
    if (level === 'bad') return '有问题';
    return '说明';
  }

  function autostartLevelClass(level) {
    if (level === 'ok') return 'badge ok';
    if (level === 'warn') return 'badge warn';
    if (level === 'bad') return 'badge err';
    return 'badge';
  }

  function renderAutostartReport(report) {
    var box = $('#autostartDiagReport');
    box.style.display = '';
    box.className = 'notice' + (report && report.ok ? '' : ' error');
    while (box.firstChild) box.removeChild(box.firstChild);
    if (!report || !report.items) {
      box.textContent = '没拿到诊断结果，请稍后再点一次。';
      $('#autostartFixBtn').style.display = 'none';
      return;
    }
    var head = el('div', null, report.summary || '');
    head.style.fontWeight = '600';
    head.style.marginBottom = '6px';
    box.appendChild(head);
    for (var i = 0; i < report.items.length; i++) {
      var item = report.items[i];
      var row = el('div', null);
      row.style.margin = '3px 0';
      var badge = el('span', autostartLevelClass(item.level), autostartLevelLabel(item.level));
      badge.style.marginRight = '6px';
      row.appendChild(badge);
      var title = el('span', null, item.title + '：');
      title.style.fontWeight = '600';
      row.appendChild(title);
      row.appendChild(document.createTextNode(item.detail));
      box.appendChild(row);
    }
    // 只有真的还有问题（或者写法是旧的）才把「一键修复」亮出来
    $('#autostartFixBtn').style.display = report.fixable ? '' : 'none';
  }

  function diagnoseAutostart() {
    var box = $('#autostartDiagReport');
    box.style.display = '';
    box.className = 'notice';
    box.textContent = '正在检查这台机器的开机自启…';
    $('#autostartFixBtn').style.display = 'none';
    var button = $('#autostartDiagBtn');
    button.disabled = true;
    api('/api/autostart').then(function (res) {
      renderAutostartReport(res.report);
    }).catch(function (error) {
      box.className = 'notice error';
      box.textContent = '诊断失败：' + error.message;
    }).then(function () { button.disabled = false; });
  }

  function repairAutostart() {
    var box = $('#autostartDiagReport');
    box.className = 'notice';
    box.textContent = '正在修复：把注册表和启动文件夹都写好…';
    var button = $('#autostartFixBtn');
    button.disabled = true;
    api('/api/autostart/repair', { method: 'POST', body: {} }).then(function (res) {
      renderAutostartReport(res.report);
      toast(res.ok ? ('已修复：' + res.message) : ('没修好：' + res.message), res.ok ? '' : 'error');
    }).catch(function (error) {
      box.className = 'notice error';
      box.textContent = '修复失败：' + error.message;
    }).then(function () { button.disabled = false; });
  }

  function saveSettings() {
    var settings = {
      model: $('#setModel').value.trim(),
      endpoint: $('#setEndpoint').value.trim(),
      temperature: $('#setTemp').value === '' ? null : Number($('#setTemp').value),
      max_tokens: Number($('#setMaxTokens').value || 0),
      max_tool_rounds: Number($('#setRounds').value || 1024),
      reasoning_effort: $('#setEffort').value || '',
      thinking: $('#setThinking').value || '',
      auth_header: currentAuthHeader(),
      workspace_root: $('#setWorkspace').value.trim(),
      allow_outside_workspace: $('#setOutside').checked,
      autostart: $('#setAutostart').checked,
      tools_enabled: $('#setTools').checked,
      allow_desktop_control: $('#setDesktop').checked,
      loop_breaker: $('#setLoopBreaker').checked,
      confirm_desktop_input: $('#setDesktopConfirm').checked,
      confirm_messages: $('#setMessageConfirm').checked,
      max_images: Number($('#setMaxImages').value || 256),
      confirm_high_risk_commands: $('#setCommandConfirm').checked,
      allow_self_restart: $('#setSelfRestart').checked,
      notify: collectNotify(),
      system_prompt: $('#setSystem').value,
      host: $('#setHost').value.trim() || '127.0.0.1',
      port: Number($('#setPort').value || 8770),
      loop_repeat_limit: Number($('#setLoopLimit').value || 3),
      max_run_seconds: Number($('#setMaxSeconds').value || 0),
      max_subagents: Number($('#setMaxSubagents').value || 4),
      subagent_max_rounds: Number($('#setSubagentRounds').value || 256),
      auto_compact: $('#setAutoCompact').checked,
      compact_threshold_ratio: Number($('#setCompactRatio').value || 0.8),
      search: {
        engine: $('#setSearchEngine').value,
        endpoint: $('#setSearchEndpoint').value.trim(),
        max_results: (state.config.search && state.config.search.maxResults) || 5,
        timeout: 20
      },
      price: {
        currency: (state.config.price && state.config.price.currency) || 'USD',
        unit: 1000000,
        cache_hit: Number($('#setPriceHit').value || 0),
        cache_miss: Number($('#setPriceMiss').value || 0),
        output: Number($('#setPriceOut').value || 0)
      }
    };
    if ($('#setToken').value) settings.access_token = $('#setToken').value;
    // API Key 和访问令牌一样：只在新填了的时候发出去，留空表示"不改"
    var key = $('#setApiKey').value.trim();
    if (key) settings.api_key = key;
    // 悬浮工具栏那几项存在 config\float.json 里，跟主设置一起保存（失败要说清楚是哪一半失败）
    var floatConfig = {
      qq_download_dir: $('#setQqDir').value.trim(),
      whiteboard: $('#setFloatWhiteboard').value.trim(),
      display: $('#setFloatDisplay').value.trim(),
      qq: $('#setFloatQq').value.trim()
    };
    var floatSave = api('/api/float/config', { method: 'POST', body: { config: floatConfig } })
      .then(function () { return ''; })
      .catch(function (error) { return error.message; });
    api('/api/settings', { method: 'POST', body: { settings: settings } }).then(function (res) {
      state.config = res.config;
      renderHeader();
      renderInspector();
      return floatSave;
    }).then(function (floatError) {
      if (floatError) {
        // 主设置已经存下去了，这里只报告工具栏那部分没存上，别让老师以为全都失败了
        toast('设置已保存，但悬浮工具栏的配置没存上：' + floatError, 'error');
        return;
      }
      $('#settingsDialog').close();
      toast('设置已保存' + (settings.host !== '127.0.0.1' ? '，监听地址变更需重启服务' : ''), 'ok');
    }).catch(function (error) { toast(error.message, 'error'); });
  }

  function refreshModels(force) {
    api('/api/models' + (force ? '?refresh=1' : '')).then(function (res) {
      var list = $('#modelOptions');
      list.innerHTML = '';
      (res.models || []).forEach(function (id) {
        var option = el('option');
        option.value = id;
        list.appendChild(option);
      });
      $('#modelsHint').textContent = (res.models && res.models.length)
        ? ('接口可用模型：' + res.models.join(' / '))
        : (res.error || '未获取到模型列表');
    }).catch(function (error) {
      $('#modelsHint').textContent = error.message;
    });
  }

  function openSnapshots() {
    api('/api/state').then(function (data) {
      var box = $('#snapshotList');
      box.innerHTML = '';
      (data.snapshots || []).forEach(function (item) {
        var row = el('div', 'snapshot-row');
        row.appendChild(el('span', 's-name', item.file));
        row.appendChild(el('span', 'muted', fmtNumber(item.size) + 'B'));
        row.appendChild(el('span', 'muted', item.time));
        var button = el('button', 'btn', '回滚');
        button.onclick = function () {
          if (!window.confirm('用该快照覆盖原文件？')) return;
          api('/api/snapshots/restore', { method: 'POST', body: { file: item.file } })
            .then(function (res) { toast('已回滚：' + res.path, 'ok'); })
            .catch(function (error) { toast(error.message, 'error'); });
        };
        row.appendChild(button);
        box.appendChild(row);
      });
      if (!box.children.length) box.appendChild(el('div', 'muted', '还没有快照'));
      $('#snapshotDialog').showModal();
    }).catch(function (error) { toast(error.message, 'error'); });
  }

  /* ------------------------------------------------------------- 事件绑定 */
  function bindEvents() {
    $('#btnSend').onclick = function () { send(); };
    $('#btnStop').onclick = stop;
    $('#newSession').onclick = function () { if (!state.streaming) newSession(); };
    $('#btnSettings').onclick = openSettings;
    $('#btnSettingsTop').onclick = openSettings;
    $('#setEndpoint').addEventListener('input', updateEndpointHint);
    $('#btnSnapshots').onclick = openSnapshots;
    $('#btnExport').onclick = function () {
      if (!state.session) return;
      window.open('/api/session/export?id=' + encodeURIComponent(state.session.id), '_blank');
    };
    $('#btnClear').onclick = function () {
      if (!state.session || state.streaming) return;
      if (!window.confirm('清空当前会话的消息记录？')) return;
      api('/api/session/clear', { method: 'POST', body: { id: state.session.id } })
        .then(function (res) { state.session = res.session; renderAll(); })
        .catch(function (error) { toast(error.message, 'error'); });
    };
    $('#btnTheme').onclick = function () {
      var current = document.documentElement.getAttribute('data-theme');
      var next = current === 'dark' ? 'light' : 'dark';
      document.documentElement.setAttribute('data-theme', next);
      localStorage.setItem('w7a.theme', next);
      api('/api/settings', { method: 'POST', body: { settings: { theme: next } } }).catch(function () {});
    };
    $('#btnInspector').onclick = function () { $('#inspector').classList.toggle('open'); };
    $('#btnTouch').onclick = function () {
      var enabled = !document.body.classList.contains('touch');
      applyTouch(enabled);
      toast(enabled ? '已开启触屏模式：字号与按钮放大，操作按钮常显' : '已关闭触屏模式', 'ok');
    };
    $('#sidebarToggle').onclick = function () { $('#sidebar').classList.toggle('open'); };
    $('#sessionSearch').oninput = renderSessions;
    $('#btnAttach').onclick = function () {
      W7Panels.openPicker({
        title: '选择要加入上下文的文件',
        mode: 'file',
        filter: 'all',
        start: state.config.desktop || state.config.workspace,
        filters: [{ key: 'all', label: '全部' }, { key: 'doc', label: '文档' }]
      }).then(function (result) {
        if (!result) return;
        result.paths.forEach(function (path) { addFileContext(path); });
      });
    };
    $('#btnImage').onclick = function () {
      W7Panels.openPicker({
        title: '选择图片（截图可直接 Ctrl+V 粘贴）',
        mode: 'file',
        filter: 'image',
        start: state.config.desktop || state.config.workspace,
        filters: [{ key: 'image', label: '只看图片' }, { key: 'all', label: '全部文件' }]
      }).then(function (result) {
        if (!result) return;
        result.paths.forEach(function (path) { addImagePath(path); });
      });
    };
    // 工作区标题右侧的「文件夹+」= 新建工作区（建文件夹 + 登记 + 切过去）
    $('#workspacePicker').onclick = function (event) {
      event.stopPropagation();
      openCreateWorkspace();
    };
    $('#wsCreateGo').onclick = createWorkspace;
    $('#wsAddExisting').onclick = function (event) {
      event.stopPropagation();
      addExistingWorkspace();
    };
    $('#wsPickParent').onclick = function () {
      W7Panels.openPicker({
        title: '新工作区建在哪个文件夹下',
        mode: 'dir',
        start: $('#wsParent').value || state.config.workspace,
        filters: []
      }).then(function (result) {
        if (!result || !result.paths.length) return;
        $('#wsParent').value = result.paths[0];
        renderWorkspacePreview();
      });
    };
    $('#wsName').oninput = renderWorkspacePreview;
    $('#wsName').onkeydown = function (event) {
      if (event.key === 'Enter') createWorkspace(event);
    };
    // 筛选：只看本工作区 / 显示全部工作区（侧栏按工作区分组）
    $('#btnScope').onclick = function () {
      state.scopeAll = !state.scopeAll;
      localStorage.setItem('w7a.scopeAll', state.scopeAll ? '1' : '0');
      renderSessions();
      toast(state.scopeAll ? '已显示全部工作区的对话' : '只看本工作区的对话', 'ok');
    };
    $('#sidebarFold').onclick = function () { $('#sidebar').classList.remove('open'); };
    document.addEventListener('click', closeWorkspaceMenus);
    $('#btnSearch').onclick = function () {
      var input = $('#sessionSearch');
      var hidden = input.classList.toggle('hidden');
      if (!hidden) input.focus();
      else { input.value = ''; renderSessions(); }
    };
    $('#scrollBottom').onclick = function () { scrollToBottom(true); };
    $('#messages').addEventListener('scroll', updateScrollButton);
    $('#boardClose').onclick = closeBoard;
    $('#board').addEventListener('click', function (event) {
      if (event.target.id === 'board') closeBoard();
    });
    $('#boardReset').onclick = function () { boardPick('reset'); };
    $('#boardNext').onclick = function () { boardPick('next'); };
    $('#timerStart').onclick = function () {
      closeDialog('timerDialog');
      startTimerFromDialog();
    };
    /* 顶栏「作图」= 几何画板（平面面板）。
       **谁后跑谁说了算**：app.js 的 bindEvents 挂在 DOMContentLoaded 上，比
       plane.js 的 boot() 晚（plane.js 在 readyState=loading 时直接注册，先执行），
       所以这里要是无条件写 openFigurePanel，就会**盖掉** plane.js 装的 openPanel——
       老师点「作图」跳出来的是立体面板，而平方面板永远打不开（无头探针抓到的真 bug）。
       这里的写法对两种加载顺序都成立：plane.js 在就用它，不在（脚本没加载出来）
       就退回旧面板，绝不出现"点了没反应"。 */
    $('#btnFigure').onclick = function () {
      if (window.W7Plane && window.W7Plane.open) { window.W7Plane.open(); return; }
      openFigurePanel();
    };
    /* 平方面板右上角那个「立体」：老师要的入口（平面面板只管平面，立体仍在旧面板）。
       绑定放 app.js 而不是 plane.js，是因为**关掉平方面板 + 叫出立体面板**这两件事
       都在 app.js 这边（plane.js 只认平面自己的状态，不该反过来动立体）。 */
    $('#planeSolid').onclick = function () {
      if (window.W7Plane && window.W7Plane.close) window.W7Plane.close();
      openFigurePanel();
    };
    $('#btnCalc').onclick = function () { openCalc('math'); };
    $('#calcTabMath').onclick = function () { setCalcTab('math'); };
    $('#calcTabUnits').onclick = function () { setCalcTab('units'); };
    $('#calcTabSolve').onclick = function () { setCalcTab('solve'); };
    $('#solveKindEquation').onclick = function () { setSolveKind('equation'); };
    $('#solveKindFactor').onclick = function () { setSolveKind('factor'); };
    $('#solveGo').onclick = function () { solveWithAi(solveKind); };
    $('#solveInput').onkeydown = function (event) {
      if (event.key === 'Enter') { event.preventDefault(); solveWithAi(solveKind); }
    };
    $('#calcModeRad').onclick = function () { setCalcMode(false); };
    $('#calcModeDeg').onclick = function () { setCalcMode(true); };
    $('#unitSwap').onclick = function () {
      var from = $('#unitFrom').value;
      $('#unitFrom').value = $('#unitTo').value;
      $('#unitTo').value = from;
      unitState.from = $('#unitFrom').value;
      unitState.to = $('#unitTo').value;
      doConvert();
    };
    $('#unitSearch').onkeydown = function (event) {
      if (event.key === 'Enter') { event.preventDefault(); searchUnits(); }
    };
    $('#unitFrom').onchange = function () { unitState.from = this.value; doConvert(); };
    $('#unitTo').onchange = function () { unitState.to = this.value; doConvert(); };
    $('#unitValue').oninput = doConvert;
    $('#calcInput').onkeydown = function (event) {
      if (event.key === 'Enter') { event.preventDefault(); calcEvaluate(); }
    };
    $('#unitSearch').oninput = function () {
      // 边打边搜，但要防抖：一体机上打字慢，每敲一下就发一次请求太费
      if (unitSearchTimer) clearTimeout(unitSearchTimer);
      unitSearchTimer = setTimeout(searchUnits, 220);
    };
    $('#unitSearchGo').onclick = searchUnits;
    $('#btnMaterials').onclick = openMaterials;
    $('#figureMore').onclick = openFigureDialog;
    // 平面（几何画板）那一排的切换已经删掉：旧面板只剩立体一种 kind，
    // 函数 / 方程 / 几何的 chip 和切线工具都不在 index.html 里了。
    // 空间几何：按坐标加点 / 连线 / 画面 / 约束拖动 / 删除 / 撤销 / 清空
    var solidToolButtons = document.querySelectorAll('.solid-tool[data-solid]');
    for (var solidIndex = 0; solidIndex < solidToolButtons.length; solidIndex++) {
      (function (button) {
        button.onclick = function () {
          var key = button.getAttribute('data-solid');
          if (key === 'line') { setSolidTool('line'); return; }
          if (key === 'face') { setSolidTool('face'); return; }
          setSolidTool(key);
        };
      })(solidToolButtons[solidIndex]);
    }
    $('#solidAdd').onclick = solidAddPoint;
    $('#solidAlongLine').onclick = function () { armSolid('alignline'); };
    var alongEdgeButton = document.getElementById('solidAlongEdge');
    if (alongEdgeButton) {
      alongEdgeButton.onclick = function () { armSolid('edgealong'); };
    }
    $('#solidOnFace').onclick = function () { armSolid('alignface'); };
    $('#solidLine').onclick = function () { armSolid('segment'); };
    $('#solidFace').onclick = function () { armSolid('face'); };
    $('#solidCircle3').onclick = function () { armSolid('circle3'); };
    $('#solidSphere').onclick = function () { armSolid('sphereoa'); };
    $('#solidAngleLL').onclick = function () { armSolid('aa'); };
    $('#solidAngleLP').onclick = function () { armSolid('ap'); };
    $('#solidAnglePP').onclick = function () { armSolid('pp'); };
    $('#solidConfirm').onclick = confirmConstraint;
    $('#solidUnselect').onclick = clearSolidSelection;
    $('#solidOnCircle').onclick = function () { addDynamicPoint('circle'); };
    $('#solidBindCircle').onclick = bindPointToCircle;
    $('#solidOnSurface').onclick = function () { addDynamicPoint('surface'); };
    $('#solidOnSphere').onclick = function () { addDynamicPoint('sphere'); };
    $('#solidSection').onclick = addSectionCircle;
    $('#solidPickSphere').onclick = function () {
      // 选中的那个点正好是某个球的球心 -> 选中整个球面
      var figure = figureState.current;
      var found = null;
      (figure && figure.surfaces || []).forEach(function (item) {
        if (item.kind === 'sphere' && solidState.selected.indexOf(item.at) >= 0) {
          found = item;
        }
      });
      if (!found) {
        setFigureStatus('先点一下球的圆心（球心），再按「整个球面」', 'warn');
        return;
      }
      solidState.pickedSurface = found;
      refreshSolid();
      solidHint('已选中整个球面（球心 ' + found.at + '），可以按「球面动点」');
    };
    $('#solidFree').onclick = function () {
      solidState.constraint = null;
      // 已经绑上去的点（圆上 / 侧面上动点、沿线 / 在面滑的点）也要解开：
      // 选了谁就解谁，一个都没选就全解 —— 按钮说的是"点又可以自由拖动"。
      var targets = solidState.selected.slice();
      solidState.dynamicPoints = (solidState.dynamicPoints || []).filter(function (item) {
        return targets.length ? targets.indexOf(item.name) < 0 : false;
      });
      solidHighlight();
      refreshSolid();
      solidHint(targets.length
        ? ('解开 ' + targets.join('、') + ' 的约束了，点又可以自由拖动')
        : '解除约束了，点又可以自由拖动');
    };
    $('#solidUndo').onclick = function () {
      if (!solidState.shapes.length) { setFigureStatus('没有可撤销的了', 'warn'); return; }
      var dropped = solidState.shapes.pop();
      solidState.selected = [];
      solidHint('撤销了 ' + dropped);
      solidApply();
    };
    $('#solidClear').onclick = clearSolidAll;
    window.addEventListener('mousemove', solidDragMove);
    window.addEventListener('mouseup', solidDragEnd);
    $('#panelDraw').onclick = drawFromPanel;
    $('#panelAskAI').onclick = askAIFromPanel;
    $('#panelInput').onkeydown = function (event) {
      if (event.key === 'Enter') { event.preventDefault(); drawFromPanel(); }
    };
    $('#figureDraw').onclick = drawFromDialog;
    // 旧面板的表达式输入：回车也走同一条"拒绝并指路"的路，跟点「画出来」一致
    $('#figureExpression').onkeydown = function (event) {
      if (event.key === 'Enter') { event.preventDefault(); drawFromDialog(); }
    };
    $('#figureClose').onclick = closeFigure;
    $('#figurePanel').addEventListener('click', function (event) {
      if (event.target.id === 'figurePanel') closeFigure();
    });
    $('#timerPill').onclick = stopTimer;
    $('#btnSkills').onclick = function () {
      W7Panels.openSkills({
        enabledSkills: function () { return sessionSkillNames(); },
        toggleSkill: function (name, on) {
          if (!state.session) return Promise.reject(new Error('尚未选择会话'));
          return toggleSessionSkill(name, on);
        },
        onChanged: function () { reloadSkills(); }
      });
    };
    $('#btnUrl').onclick = function () {
      var url = window.prompt('输入页面地址：', 'http://');
      if (url && url !== 'http://') addUrlContext(url.trim());
    };
    $('#btnRun').onclick = function () {
      var command = window.prompt('直接执行一条本机命令（高风险命令会被拦截）：', 'dir');
      if (!command) return;
      api('/api/command', { method: 'POST', body: { command: command } }).then(function (res) {
        var node = toolNode({ id: 'local', function: { name: 'run_command', arguments: JSON.stringify({ command: command }) } },
          { tool_call_id: 'local', status: 'ok', content: res.output, meta: res.meta }, false);
        expandToolRow(node);        // 手动跑的这条命令：直接展开输出，省得再点一下
        $('#thread').appendChild(node);
        scrollToBottom(true);
      }).catch(function (error) { toast(error.message, 'error'); });
    };

    $('#settingsSave').onclick = saveSettings;
    $('#apiKeyTest').onclick = testConnection;
    $('#autostartDiagBtn').onclick = diagnoseAutostart;
    $('#autostartFixBtn').onclick = repairAutostart;
    $('#apiKeyClear').onclick = function () {
      if (!window.confirm('清除已保存的 API Key？（config\\agent.json 里的 api_key 会被置空）')) return;
      api('/api/settings', { method: 'POST', body: { settings: { clear_api_key: true } } })
        .then(function (res) {
          state.config = res.config;
          $('#apiKeyState').textContent = '已清除，当前没有配置密钥';
          $('#apiKeyResult').style.display = '';
          $('#apiKeyResult').className = 'notice';
          $('#apiKeyResult').textContent = '已清除 API Key。';
          renderBanner();
        }).catch(function (error) { toast(error.message, 'error'); });
    };
    $('#setAuthHeader').onchange = function () {
      $('#setAuthHeaderCustom').classList.toggle('hidden', $('#setAuthHeader').value !== '__custom__');
    };
    $('#settingsTest').onclick = function () {
      api('/api/models?refresh=1').then(function (res) {
        note('连接成功：' + ((res.models || []).join(' / ') || '接口未返回模型列表'), 'ok');
      }).catch(function (error) { note('连接失败：' + error.message, 'error'); });
    };
    $('#settingsModels').onclick = function () {
      refreshModels(true);
      note('已请求模型列表，见“模型”输入框下方的提示。', 'ok');
    };

    function note(text, kind) {
      var box = $('#settingsNote');
      box.style.display = '';
      box.className = 'notice' + (kind === 'error' ? ' error' : '');
      box.textContent = text;
    }

    $('#confirmAllow').onclick = function () { answerConfirm(true); };
    $('#confirmDeny').onclick = function () { answerConfirm(false); };
    $('#confirmDialog').addEventListener('cancel', function (event) {
      event.preventDefault();
      answerConfirm(false);
    });

    $('#modelSelect').onchange = function () {
      api('/api/session/model', { method: 'POST', body: { id: state.session.id, model: currentModelSelection() } })
        .then(function (res) {
          state.session = res.session;
          var names = (state.config.profiles || []);
          var name = currentModelSelection();
          state.config.model = (state.config.models[name] && state.config.models[name].model) || name || state.config.model;
          renderInspector();
          toast('已切换模型：' + state.config.model, 'ok');
        }).catch(function (error) { toast(error.message, 'error'); });
    };

    $('#effortSelect').onchange = function () {
      var level = currentEffort();
      api('/api/session/effort', { method: 'POST', body: { id: state.session.id, effort: level } })
        .then(function (res) {
          state.session = res.session;
          $('#effortSelect').title = '推理等级：越高想得越久、越周密，也越慢越贵。\n' + effortHint(level);
          renderInspector();
          toast('推理等级：' + effortLabel(level), 'ok');
        }).catch(function (error) { toast(error.message, 'error'); });
    };

    $('#setEffort').onchange = function () {
      $('#setEffortHint').textContent = effortHint($('#setEffort').value || '');
    };

    document.addEventListener('click', function (event) {
      var button = event.target.closest ? event.target.closest('[data-copy]') : null;
      if (!button) return;
      var block = button.closest('.code-block');
      var code = block ? block.querySelector('code') : null;
      if (!code) return;
      copyText(code.textContent).then(function () {
        button.textContent = '已复制';
        setTimeout(function () { button.textContent = '复制'; }, 1500);
      }).catch(function () { toast('复制失败', 'error'); });
    });

    $$('[data-close]').forEach(function (button) {
      button.onclick = function () {
        var dialog = document.getElementById(button.getAttribute('data-close'));
        if (dialog) dialog.close();
      };
    });

    var input = $('#input');
    input.addEventListener('keydown', function (event) {
      if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        send();
      }
    });
    /* 每敲一个字就量一次 scrollHeight + 设高度 = 强制重排，打字就卡。按帧合并：一帧最多一次 */
    input.addEventListener('input', function () {
      if (input.growPending) return;
      input.growPending = true;
      var run = function () { input.growPending = false; updateTokenHint(); };
      if (window.requestAnimationFrame) window.requestAnimationFrame(run);
      else setTimeout(run, 16);
    });
    // 截图后直接 Ctrl+V 粘贴：一体机上最常用的识图入口
    input.addEventListener('paste', function (event) {
      var items = (event.clipboardData && event.clipboardData.items) || [];
      var handled = false;
      for (var index = 0; index < items.length; index++) {
        if (items[index].type && items[index].type.indexOf('image') === 0) {
          var file = items[index].getAsFile();
          if (file) {
            uploadImageFile(file);
            handled = true;
          }
        }
      }
      if (handled) event.preventDefault();
    });

    document.addEventListener('keydown', function (event) {
      // 有对话框开着时 Esc 归对话框（浏览器自己会关它），别顺手把大屏/作图面板也关了
      if (document.querySelector('dialog[open]')) return;
      if (event.key === 'Escape' && !$('#figurePanel').classList.contains('hidden')) {
        closeFigure();
        return;
      }
      if (event.key === 'Escape' && !$('#board').classList.contains('hidden')) {
        closeBoard();
        return;
      }
      if (event.key === 'Escape' && state.streaming) stop();
    });

    var box = $('#composerBox');
    ['dragenter', 'dragover'].forEach(function (name) {
      box.addEventListener(name, function (event) {
        event.preventDefault();
        box.classList.add('dragover');
      });
    });
    ['dragleave', 'drop'].forEach(function (name) {
      box.addEventListener(name, function (event) {
        event.preventDefault();
        box.classList.remove('dragover');
      });
    });
    box.addEventListener('drop', function (event) {
      if (event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files.length) {
        uploadFiles(event.dataTransfer.files);
      }
    });

    window.addEventListener('beforeunload', function () {
      if (state.streaming) stop();
      stopRing();          // 刷新 / 关标签页时立刻静音，别让"叮"跟着走进下一个页面
      // 跟服务端打个招呼：悬浮窗据此在几秒内把整程序收掉，老师不用干等超时。
      // 按 F5 也没关系——新页面 2 秒内就会重新报活，服务端把倒计时收回去。
      try {
        fetch('/api/float/bye', {
          method: 'POST', keepalive: true,
          headers: AUTH_TOKEN ? { 'Content-Type': 'application/json', 'X-Auth-Token': AUTH_TOKEN }
                              : { 'Content-Type': 'application/json' },
          body: '{}'
        });
      } catch (error) { /* 关页面时说不上话就算了，服务端 10 秒后也会发现 */ }
    });
  }

  document.addEventListener('DOMContentLoaded', boot);
})();
