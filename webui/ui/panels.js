/*!
 * panels.js —— 面向老师/学生的图形化面板
 *   1) 文件选择器：点选盘符/桌面/文档/工作区，不用手打路径（浏览器拿不到真实路径，所以由本地服务列目录）
 *   2) 技能面板：勾选启用、查看编辑、新建与删除 skills
 *   3) 工作区面板：设置工作区、打开文件夹、查看最近生成的文件
 *
 * 依赖：index.html 里的 #pickerDialog / #skillsDialog / #workspaceDialog，以及 window.W7Api
 */
(function (global) {
  'use strict';

  function api() {
    if (!global.W7Api) throw new Error('W7Api 未就绪');
    return global.W7Api;
  }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function icon(name, size) {
    return global.W7Icons ? W7Icons.markup(name, size || 14) : '';
  }

  function iconButton(className, name, size, title) {
    var button = el('button', className);
    button.innerHTML = icon(name, size);
    if (title) button.title = title;
    return button;
  }

  function fmtSize(bytes) {
    var value = Number(bytes || 0);
    if (value >= 1048576) return (value / 1048576).toFixed(1) + 'MB';
    if (value >= 1024) return Math.round(value / 1024) + 'KB';
    return value + 'B';
  }

  function toast(message, kind) {
    if (global.W7Toast) global.W7Toast(message, kind);
  }

  /* 图片地址统一从 app.js 拿：局域网模式下它会给地址带上访问令牌 */
  function imageUrl(path) {
    if (global.W7Api && global.W7Api.imageUrl) return global.W7Api.imageUrl(path);
    return '/api/image?path=' + encodeURIComponent(path);
  }

  function closeDialog(id) {
    var dialog = document.getElementById(id);
    if (dialog && dialog.open) dialog.close();
  }

  /* ------------------------------------------------------------------ 文件选择器 */
  var pickerState = null;
  var PAGE_SIZE = 300;          // 一次渲染多少行：学校机器上几千项的目录不能一次性铺出来

  function rememberDir(path) {
    try {
      if (path) localStorage.setItem('w7a.lastDir', path);
    } catch (error) { /* 隐私模式下忽略 */ }
  }

  function lastDir() {
    try {
      return localStorage.getItem('w7a.lastDir') || '';
    } catch (error) {
      return '';
    }
  }

  function openPicker(options) {
    options = options || {};
    var dialog = document.getElementById('pickerDialog');
    var resolved = false;

    // 万一还有上一次没结束的选择（正常流程不会），先把它结束掉，别留下永远不 resolve 的 Promise
    if (pickerState && pickerState.resolve) pickerState.resolve(null);

    return new Promise(function (resolve) {
      pickerState = {
        options: options,
        mode: options.mode || 'file',
        filter: options.filter || 'all',
        cwd: options.start || lastDir() || '',
        parent: '',
        crumbs: [],
        entries: [],
        total: 0,
        hidden: 0,
        truncated: false,
        selected: [],
        cursor: -1,
        shown: [],
        page: PAGE_SIZE,
        thumbCount: 0,
        seq: 0,
        resolve: function (value) {
          if (resolved) return;
          resolved = true;
          pickerState = null;
          closeDialog('pickerDialog');
          resolve(value);
        }
      };

      document.getElementById('pickerTitle').textContent = options.title || '选择文件';
      document.getElementById('pickerHint').textContent = pickerState.mode === 'dir'
        ? '单击选中文件夹后点「用这个文件夹」；双击文件夹进入'
        : '单击选中，双击打开；按住 Ctrl 可以选多个';
      document.getElementById('pickerPick').textContent =
        pickerState.mode === 'dir' ? '用这个文件夹' : '确定';
      document.getElementById('pickerSearch').value = '';
      document.getElementById('pickerChosen').textContent = '';
      var filters = document.getElementById('pickerFilters');
      filters.innerHTML = '';
      (options.filters || []).forEach(function (item) {
        var chip = el('button', 'chip' + (item.key === pickerState.filter ? ' active' : ''), item.label);
        chip.onclick = function () {
          if (!pickerState) return;
          pickerState.filter = item.key;
          Array.prototype.slice.call(filters.children).forEach(function (node) {
            node.classList.toggle('active', node === chip);
          });
          load(pickerState.cwd);
        };
        filters.appendChild(chip);
      });
      filters.style.display = (options.filters || []).length ? '' : 'none';
      document.getElementById('pickerUpload').style.display =
        options.upload === false ? 'none' : '';
      document.getElementById('pickerUploadInput').accept =
        options.filter === 'image' ? 'image/*' : '';
      if (!dialog.open) dialog.showModal();
      load(pickerState.cwd);
    });
  }

  function load(path) {
    var state = pickerState;
    if (!state) return;
    state.seq += 1;
    var seq = state.seq;
    var query = '/api/browse?filter=' + encodeURIComponent(state.filter) +
      (path ? '&path=' + encodeURIComponent(path) : '');
    setListMessage('正在读取…');
    api().get(query).then(function (data) {
      if (!pickerState || pickerState !== state || seq !== state.seq) return;   // 过期响应丢掉
      state.cwd = data.path || '';
      state.parent = data.parent || '';
      state.crumbs = data.crumbs || [];
      state.entries = data.entries || [];
      state.total = data.total == null ? state.entries.length : data.total;
      state.hidden = data.filtered || 0;
      state.truncated = !!data.truncated;
      state.page = PAGE_SIZE;
      state.cursor = -1;
      state.thumbCount = 0;
      rememberDir(state.cwd);
      renderShortcuts(data);
      renderCrumbs();
      renderList();
    }).catch(function (error) {
      if (!pickerState || pickerState !== state || seq !== state.seq) return;
      setListError(error.message, path);
    });
  }

  function setListMessage(text) {
    var box = document.getElementById('pickerList');
    box.innerHTML = '';
    box.appendChild(el('div', 'muted', text));
  }

  function setListError(message, path) {
    var box = document.getElementById('pickerList');
    box.innerHTML = '';
    var row = el('div', 'muted', '读取失败：' + message);
    var retry = el('button', 'btn small', '重试');
    retry.onclick = function () { load(path || (pickerState && pickerState.cwd) || ''); };
    row.appendChild(document.createTextNode(' '));
    row.appendChild(retry);
    box.appendChild(row);
    var back = el('button', 'btn small', '回到常用位置');
    back.onclick = function () { load(''); };
    row.appendChild(document.createTextNode(' '));
    row.appendChild(back);
  }

  /* 面包屑：C: > 用户 > 老师 > 桌面 > 课件，每一段都能点 */
  function renderCrumbs() {
    var state = pickerState;
    var box = document.getElementById('pickerCrumbs');
    if (!state || !box) return;
    box.innerHTML = '';
    var parts = state.crumbs;
    if (!parts || !parts.length) {
      if (state.cwd) {
        box.appendChild(el('span', 'crumb current', state.cwd));
      } else {
        box.appendChild(el('span', 'crumb muted', '常用位置'));
      }
      return;
    }
    parts.forEach(function (item, index) {
      if (index) box.appendChild(el('span', 'crumb-sep', '›'));
      var crumb = el('button', 'crumb' + (index === parts.length - 1 ? ' current' : ''), item.name);
      crumb.title = item.path;
      crumb.onclick = function () { load(item.path); };
      box.appendChild(crumb);
    });
  }

  function renderShortcuts(data) {
    var box = document.getElementById('pickerShortcuts');
    box.innerHTML = '';
    var current = (pickerState && pickerState.cwd) || '';
    (data.shortcuts || []).forEach(function (item) {
      var chip = el('button', 'chip' + (samePath(item.path, current) ? ' active' : ''), item.name);
      chip.title = item.path;
      chip.onclick = function () { load(item.path); };
      box.appendChild(chip);
    });
    var sep = el('span', 'shortcut-sep');
    box.appendChild(sep);
    (data.drives || []).forEach(function (drive) {
      var chip = el('button', 'chip' + (samePath(drive, current) ? ' active' : ''), drive);
      chip.title = '打开 ' + drive;
      chip.onclick = function () { load(drive); };
      box.appendChild(chip);
    });
  }

  function samePath(left, right) {
    var clean = function (value) {
      return String(value || '').replace(/\\/g, '/').replace(/\/+$/, '').toLowerCase();
    };
    return !!left && clean(left) === clean(right);
  }

  function renderList() {
    var state = pickerState;
    if (!state) return;
    var box = document.getElementById('pickerList');
    var keyword = (document.getElementById('pickerSearch').value || '').trim().toLowerCase();
    box.innerHTML = '';

    var up = el('div', 'picker-row dir' + (state.parent ? '' : ' disabled'));
    var upIcon = el('span', 'p-ico');
    upIcon.innerHTML = icon('up', 15);
    up.appendChild(upIcon);
    up.appendChild(el('span', 'p-name', '上一级'));
    up.appendChild(el('span', 'p-meta', state.parent || '已经是顶层'));
    if (state.parent) up.onclick = function () { load(state.parent); };
    box.appendChild(up);

    var shown = state.entries.filter(function (item) {
      return !keyword || item.name.toLowerCase().indexOf(keyword) >= 0;
    });
    state.shown = shown;
    if (state.cursor >= shown.length) state.cursor = shown.length - 1;

    if (!shown.length) {
      var why;
      if (keyword) {
        why = '「' + keyword + '」在这个文件夹里没有匹配项（共 ' + state.entries.length + ' 项）';
      } else if (state.hidden > 0) {
        why = '这个文件夹里没有符合当前筛选的文件（另有 ' + state.hidden + ' 项被筛掉了，' +
              '点上面的「全部」能看到）';
      } else {
        why = '这个文件夹是空的';
      }
      box.appendChild(el('div', 'muted', why));
    } else {
      shown.slice(0, state.page).forEach(function (item) {
        box.appendChild(listRow(state, item));
      });      if (shown.length > state.page) {
        var more = el('button', 'btn picker-more',
                      '显示更多（还有 ' + (shown.length - state.page) + ' 项）');
        more.onclick = function () {
          state.page += PAGE_SIZE * 2;
          renderList();
        };
        box.appendChild(more);
      }
    }
    if (state.truncated) {
      box.appendChild(el('div', 'muted', '这个文件夹项目太多，只列出了前 2000 项；' +
        '可以用上面的搜索框筛选，或直接进入下一级文件夹。'));
    }
    markCursor();
  }

  function listRow(state, item) {
    var row = el('div', 'picker-row' + (item.dir ? ' dir' : '') +
      (state.selected.indexOf(item.path) >= 0 ? ' selected' : ''));
    row.setAttribute('data-path', item.path);
    var rowIcon = el('span', 'p-ico');
    rowIcon.innerHTML = icon(item.dir ? 'folder' : iconForExt(item.ext), 15);
    row.appendChild(rowIcon);
    row.appendChild(el('span', 'p-name', item.name));
    if (state.filter === 'image' && !item.dir && item.thumb && state.thumbCount < 60) {
      state.thumbCount += 1;
      var thumb = document.createElement('img');
      thumb.className = 'p-thumb';
      thumb.loading = 'lazy';
      thumb.alt = '';
      thumb.src = imageUrl(item.path);
      row.appendChild(thumb);
    }
    row.appendChild(el('span', 'p-meta', item.dir ? '文件夹'
      : fmtSize(item.size) + '  ' + item.mtime));
    row.onclick = function (event) {
      state.cursor = state.shown.indexOf(item);
      if (event && (event.ctrlKey || event.metaKey)) toggleSelect(item);
      else select(item);
      markCursor();
    };
    row.ondblclick = function () { openItem(item); };
    return row;
  }

  /* 双击（或回车）一个条目：文件夹就进去，文件在选择模式下直接确定 */
  function openItem(item) {
    var state = pickerState;
    if (!state || !item) return;
    if (item.dir) {
      load(item.path);
      return;
    }
    if (state.mode === 'dir') {
      toast('这里要选的是文件夹，双击文件夹可以进入', 'warn');
      return;
    }
    select(item);
    confirmPick();
  }

  function toggleSelect(item) {
    var state = pickerState;
    if (!state) return;
    if (state.mode === 'dir' && !item.dir) return;
    var index = state.selected.indexOf(item.path);
    if (index >= 0) state.selected.splice(index, 1);
    else if (item.dir && state.mode === 'file') return;   // 选文件时文件夹不参与多选
    else state.selected.push(item.path);
    paintSelection();
  }

  function select(item) {
    var state = pickerState;
    if (!state) return;
    if (state.mode === 'dir' && !item.dir) {
      toast('这里要选的是文件夹，双击可以进入这个文件夹', 'warn');
      return;
    }
    if (state.mode === 'file' && item.dir) {
      // 选文件的场景里，点文件夹只当“想进去”，不能把文件夹当成选中的文件
      document.getElementById('pickerChosen').textContent = '文件夹：双击进入 ' + item.name;
      document.getElementById('pickerChosen').className = 'picker-chosen';
      return;
    }
    state.selected = [item.path];
    paintSelection();
  }

  function paintSelection() {
    var state = pickerState;
    if (!state) return;
    var rows = document.getElementById('pickerList').querySelectorAll('.picker-row[data-path]');
    for (var index = 0; index < rows.length; index++) {
      rows[index].classList.toggle('selected',
        state.selected.indexOf(rows[index].getAttribute('data-path')) >= 0);
    }
    var chosen = document.getElementById('pickerChosen');
    if (!state.selected.length) {
      chosen.textContent = '';
      chosen.className = 'picker-chosen';
      return;
    }
    chosen.className = 'picker-chosen on';
    if (state.selected.length === 1) {
      chosen.textContent = '已选：' + baseName(state.selected[0]);
      chosen.title = state.selected[0];
    } else {
      chosen.textContent = '已选 ' + state.selected.length + ' 个';
      chosen.title = state.selected.join('\n');
    }
  }

  function markCursor() {
    var state = pickerState;
    if (!state) return;
    var box = document.getElementById('pickerList');
    var rows = box.querySelectorAll('.picker-row[data-path]');
    for (var index = 0; index < rows.length; index++) {
      var on = index === state.cursor;
      rows[index].classList.toggle('cursor', on);
      if (!on) continue;
      // 手动滚动，不用 scrollIntoView(选项)：老内核不认参数，会把整页滚跑
      var top = rows[index].offsetTop;
      var bottom = top + rows[index].offsetHeight;
      if (top < box.scrollTop) box.scrollTop = top - 4;
      else if (bottom > box.scrollTop + box.clientHeight) {
        box.scrollTop = bottom - box.clientHeight + 4;
      }
    }
  }

  function moveCursor(step) {
    var state = pickerState;
    if (!state || !state.shown.length) return;
    var next = state.cursor + step;
    if (next < 0) next = 0;
    if (next >= state.shown.length) next = state.shown.length - 1;
    if (next >= state.page) {                    // 光标跑到还没渲染的部分就先补渲染
      state.page = next + PAGE_SIZE;
      renderList();
    }
    state.cursor = next;
    markCursor();
  }

  function activateCursor() {
    var state = pickerState;
    if (!state) return;
    if (state.cursor < 0) {
      confirmPick();
      return;
    }
    openItem(state.shown[state.cursor]);
  }

  function iconForExt(ext) {
    if (['.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp'].indexOf(ext) >= 0) return 'image';
    if (ext === '.pptx') return 'slides';
    if (['.xlsx', '.csv'].indexOf(ext) >= 0) return 'chart';
    if (['.docx', '.pdf', '.txt', '.md'].indexOf(ext) >= 0) return 'file';
    return 'file';
  }

  function confirmPick() {
    var state = pickerState;
    if (!state) return;
    var paths = state.selected.slice();
    if (!paths.length && state.mode === 'dir' && state.cwd) {
      paths = [state.cwd];
      toast('已选择当前文件夹：' + baseName(state.cwd), 'ok');
    }
    if (!paths.length) {
      toast(state.mode === 'dir' ? '请先选中一个文件夹（双击可以进入）' : '请先选中一个文件', 'warn');
      return;
    }
    state.resolve({ paths: paths, names: paths.map(baseName), dir: state.mode === 'dir' });
  }

  function baseName(path) {
    var parts = String(path).split(/[\\/]/);
    return parts[parts.length - 1] || path;
  }

  function uploadThroughPicker(files) {
    var state = pickerState;
    if (!state || !files || !files.length) return;
    var file = files[0];
    var limit = 32 * 1024 * 1024;
    if (file.size > limit) {
      toast('文件超过 32MB，请换一个小一些的文件', 'error');
      return;
    }
    var reader = new FileReader();
    reader.onload = function () {
      var dataUrl = String(reader.result || '');
      api().post('/api/upload', {
        name: file.name || '上传文件',
        data: dataUrl.slice(dataUrl.indexOf(',') + 1),
        expect: (state.options.filter === 'image') ? 'image' : ''
      }).then(function (res) {
        state.resolve({ paths: [res.path], names: [res.name], uploaded: true });
      }).catch(function (error) { toast(error.message, 'error'); });
    };
    reader.onerror = function () { toast('读取这个文件失败，请换一个文件', 'error'); };
    reader.readAsDataURL(file);
  }

  function bindPicker() {
    document.getElementById('pickerUp').onclick = function () {
      var state = pickerState;
      if (state && state.parent) load(state.parent);
      else if (state) load('');
    };
    document.getElementById('pickerRefresh').onclick = function () {
      if (pickerState) load(pickerState.cwd);
    };
    document.getElementById('pickerPick').onclick = confirmPick;
    document.getElementById('pickerCancel').onclick = function () {
      if (pickerState) pickerState.resolve(null);
    };
    document.getElementById('pickerSearch').oninput = function () {
      if (!pickerState) return;
      pickerState.page = PAGE_SIZE;         // 换关键字要重新分页
      renderList();
    };
    document.getElementById('pickerUploadInput').onchange = function (event) {
      uploadThroughPicker(event.target.files);
      event.target.value = '';
    };
    document.getElementById('pickerUpload').onclick = function () {
      document.getElementById('pickerUploadInput').click();
    };
    // 键盘操作：上下选、回车进入/确定、退格回上一层
    document.getElementById('pickerDialog').addEventListener('keydown', function (event) {
      if (!pickerState) return;
      var typing = event.target && event.target.id === 'pickerSearch';
      if (event.key === 'ArrowDown') {
        event.preventDefault();
        moveCursor(1);
      } else if (event.key === 'ArrowUp') {
        event.preventDefault();
        moveCursor(-1);
      } else if (event.key === 'Enter') {
        event.preventDefault();
        if (typing && pickerState.cursor < 0) {
          pickerState.cursor = 0;          // 在搜索框里按回车：先高亮第一条，再按一次才打开
          markCursor();
        } else {
          activateCursor();
        }
      } else if (event.key === 'Backspace' && !typing) {
        event.preventDefault();
        if (pickerState.parent) load(pickerState.parent);
      } else if (event.key === 'Home' && !typing) {
        event.preventDefault();
        pickerState.cursor = 0;
        markCursor();
      }
    });
    document.getElementById('pickerDialog').addEventListener('cancel', function (event) {
      event.preventDefault();
      if (pickerState) pickerState.resolve(null);
    });
  }

  /* ------------------------------------------------------------------ 技能面板 */
  var skillsDeps = null;

  function openSkills(deps) {
    skillsDeps = deps || {};
    var dialog = document.getElementById('skillsDialog');
    if (!dialog.open) dialog.showModal();
    refreshSkills();
  }

  function refreshSkills() {
    var list = document.getElementById('skillsList');
    list.innerHTML = '<div class="muted">正在读取 skills…</div>';
    api().get('/api/skills').then(function (data) {
      var enabled = (skillsDeps && skillsDeps.enabledSkills()) || [];
      list.innerHTML = '';
      (data.skills || []).forEach(function (skill) {
        list.appendChild(skillRow(skill, enabled));
      });
      if (!data.skills || !data.skills.length) {
        list.appendChild(el('div', 'muted', '还没有技能，可以在下面新建一个。'));
      }
    }).catch(function (error) {
      list.innerHTML = '';
      list.appendChild(el('div', 'muted', error.message));
    });
  }

  function skillRow(skill, enabled) {
    var row = el('div', 'skill-row' + (enabled.indexOf(skill.name) >= 0 ? ' active' : ''));
    if (skill.always) {
      // 硬性技能（SKILL.md 里写了 always: true）每次都会带上，关不掉，
      // 所以不给复选框，只挂一个「常驻」牌子，免得老师以为勾掉就不生效了
      var badge = el('span', 'skill-always', '常驻');
      badge.title = '这条技能每次对话都会带上，关不掉';
      row.appendChild(badge);
      row.appendChild(el('span', 'skill-always-name', skill.name));
    } else {
      var label = el('label', 'switch');
      var box = document.createElement('input');
      box.type = 'checkbox';
      box.checked = enabled.indexOf(skill.name) >= 0;
      box.onchange = function () { toggleSkill(skill.name, box.checked); };
      label.appendChild(box);
      label.appendChild(el('span', null, skill.name));
      row.appendChild(label);
    }
    row.appendChild(el('span', 's-desc', skill.description || '（无说明）'));

    var view = iconButton('btn icon', 'search', 13, '查看内容');
    view.onclick = function () {
      document.getElementById('skillName').value = skill.name;
      document.getElementById('skillDesc').value = skill.description || '';
      document.getElementById('skillContent').value = skill.content || '';
    };
    var edit = iconButton('btn icon', 'pencil', 13, '编辑');
    edit.onclick = function () {
      view.onclick();
      document.getElementById('skillContent').focus();
    };
    var remove = iconButton('btn icon', 'trash', 13, '删除');
    remove.onclick = function () {
      if (!global.confirm('删除技能“' + skill.name + '”？文件会从 skills 目录移除。')) return;
      api().post('/api/skills/delete', { name: skill.name }).then(function () {
        toast('已删除技能：' + skill.name, 'ok');
        refreshSkills();
        if (skillsDeps && skillsDeps.onChanged) skillsDeps.onChanged();
      }).catch(function (error) { toast(error.message, 'error'); });
    };
    if (skill.editable === false) {
      remove.style.display = 'none';
    }
    var actions = el('div', 'skill-actions');
    actions.appendChild(view);
    actions.appendChild(edit);
    actions.appendChild(remove);
    row.appendChild(actions);
    return row;
  }

  function toggleSkill(name, on) {
    if (!skillsDeps) return;
    skillsDeps.toggleSkill(name, on).then(function () {
      refreshSkills();
    }).catch(function (error) { toast(error.message, 'error'); });
  }

  function bindSkills() {
    document.getElementById('skillNew').onclick = function () {
      document.getElementById('skillName').value = '';
      document.getElementById('skillDesc').value = '';
      document.getElementById('skillContent').value = '';
      document.getElementById('skillName').focus();
    };
    document.getElementById('skillSave').onclick = function () {
      var name = document.getElementById('skillName').value.trim();
      if (!name) {
        toast('请先填写技能名称', 'warn');
        document.getElementById('skillName').focus();
        return;
      }
      api().post('/api/skills/save', {
        name: name,
        description: document.getElementById('skillDesc').value.trim(),
        content: document.getElementById('skillContent').value
      }).then(function () {
        toast('技能已保存：' + name, 'ok');
        refreshSkills();
        if (skillsDeps && skillsDeps.onChanged) skillsDeps.onChanged();
      }).catch(function (error) { toast(error.message, 'error'); });
    };
  }

  function init() {
    if (!document.getElementById('pickerDialog')) return;
    bindPicker();
    bindSkills();
  }

  global.W7Panels = {
    init: init,
    openPicker: openPicker,
    openSkills: openSkills,
    refreshSkills: refreshSkills,
    baseName: baseName
  };
})(window);
