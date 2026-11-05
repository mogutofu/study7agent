/*!
 * md.js —— 极简 Markdown 渲染 + 轻量语法高亮
 * 目标环境：Windows 7 上的 Chrome 109 / Firefox 115（离线内网，无 CDN、无构建）
 * 说明：只实现对话场景需要的子集，流式输出时对未闭合的代码围栏也能容错。
 */
(function (global) {
  'use strict';

  var KEYWORDS = {
    python: 'and|as|assert|async|await|break|class|continue|def|del|elif|else|except|finally|for|from|global|if|import|in|is|lambda|None|nonlocal|not|or|pass|raise|return|True|False|try|while|with|yield|self',
    javascript: 'await|async|break|case|catch|class|const|continue|default|delete|do|else|export|extends|finally|for|from|function|if|import|in|instanceof|let|new|of|return|static|super|switch|this|throw|try|typeof|var|void|while|yield|null|true|false|undefined',
    json: 'true|false|null',
    go: 'break|case|chan|const|continue|default|defer|else|fallthrough|for|func|go|goto|if|import|interface|map|package|range|return|select|struct|switch|type|var|nil|true|false',
    java: 'abstract|boolean|break|byte|case|catch|char|class|const|continue|default|do|double|else|enum|extends|final|finally|float|for|if|implements|import|instanceof|int|interface|long|native|new|package|private|protected|public|return|short|static|strictfp|super|switch|synchronized|this|throw|throws|transient|try|void|volatile|while|null|true|false',
    sql: 'SELECT|FROM|WHERE|INSERT|INTO|VALUES|UPDATE|SET|DELETE|CREATE|TABLE|ALTER|DROP|INDEX|JOIN|LEFT|RIGHT|INNER|OUTER|ON|GROUP|BY|ORDER|HAVING|LIMIT|OFFSET|AS|AND|OR|NOT|NULL|DISTINCT|COUNT|SUM|AVG|MAX|MIN|PRIMARY|KEY|FOREIGN|REFERENCES|DEFAULT|UNIQUE',
    yaml: 'true|false|null|yes|no',
    css: ''
  };

  var COMMENT_STYLE = {
    python: { line: '#', hashOnly: true },
    yaml: { line: '#', hashOnly: true },
    bash: { line: '#', hashOnly: true },
    bat: { line: 'rem|::', hashOnly: true },
    sql: { line: '--', hashOnly: true },
    javascript: { line: '//', block: true },
    typescript: { line: '//', block: true },
    go: { line: '//', block: true },
    java: { line: '//', block: true },
    json: { line: null }
  };

  var ALIASES = {
    js: 'javascript', jsx: 'javascript', ts: 'typescript', tsx: 'typescript',
    py: 'python', py3: 'python', golang: 'go', sh: 'bash', shell: 'bash',
    cmd: 'bat', bat: 'bat', ps1: 'powershell', powershell: 'powershell',
    yml: 'yaml', md: 'markdown', htm: 'html', xml: 'html', text: 'text',
    console: 'text', log: 'text', csv: 'text'
  };

  function escapeHtml(text) {
    return String(text == null ? '' : text)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function normalizeLang(lang) {
    var key = String(lang || '').trim().toLowerCase();
    if (!key) return 'text';
    return ALIASES[key] || key;
  }

  function highlight(code, lang) {
    var language = normalizeLang(lang);
    if (language === 'text' || language === 'markdown') return escapeHtml(code);
    var keywords = KEYWORDS[language];
    var comment = COMMENT_STYLE[language] || { line: '//', block: true };
    var parts = [];

    if (comment.line) {
      var linePattern = comment.hashOnly
        ? (language === 'bat' ? '^[ \\t]*(?:rem\\b.*|::.*)$' : '#[^\\n]*')
        : escapeRegExp(comment.line) + '[^\\n]*';
      parts.push(linePattern);
    }
    if (comment.block) parts.push('/\\*[\\s\\S]*?\\*/');
    parts.push('"(?:\\\\.|[^"\\\\\\n])*"');
    parts.push("'(?:\\\\.|[^'\\\\\\n])*'");
    parts.push('`(?:\\\\.|[^`\\\\])*`');
    parts.push('\\b\\d+(?:\\.\\d+)?\\b');
    if (keywords) parts.push('\\b(?:' + keywords + ')\\b');

    var pattern = new RegExp(parts.join('|'), comment.hashOnly && language === 'bat' ? 'gim' : 'gm');
    var store = [];
    var marked = String(code).replace(pattern, function (match, offset) {
      var cls = 'tok-str';
      if (/^(\/\/|\/\*|#|--|rem\b|::)/i.test(match) && !/^["'`]/.test(match)) cls = 'tok-com';
      else if (/^\d/.test(match)) cls = 'tok-num';
      else if (/^["'`]/.test(match)) cls = 'tok-str';
      else cls = 'tok-kw';
      store.push('<span class="' + cls + '">' + escapeHtml(match) + '</span>');
      return '\u0000' + (store.length - 1) + '\u0000';
    });

    return escapeHtml(marked).replace(/\u0000(\d+)\u0000/g, function (_all, index) {
      return store[Number(index)];
    });
  }

  function escapeRegExp(text) {
    return String(text).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  }

  function inline(text) {
    var out = escapeHtml(text);
    out = out.replace(/`([^`]+)`/g, function (_all, code) {
      return '<code class="inline">' + code + '</code>';
    });
    out = out.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    out = out.replace(/(^|[\s(（])\*([^*\n]+)\*/g, '$1<em>$2</em>');
    out = out.replace(/~~([^~]+)~~/g, '<del>$1</del>');
    out = out.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
    out = out.replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g,
      '$1<a href="$2" target="_blank" rel="noopener noreferrer">$2</a>');
    out = out.replace(/`([^`]+)`/g, '<code class="inline">$1</code>');
    return out;
  }

  function codeBlock(lang, body, closed) {
    var label = normalizeLang(lang);
    return '<div class="code-block' + (closed ? '' : ' streaming') + '">' +
      '<div class="code-head"><span class="code-lang">' + escapeHtml(label) + '</span>' +
      '<button type="button" class="code-copy" data-copy>复制</button></div>' +
      '<pre><code>' + highlight(body, label) + '</code></pre></div>';
  }

  function renderTable(lines) {
    var rows = lines.map(function (line) {
      return line.replace(/^\s*\|/, '').replace(/\|\s*$/, '').split('|').map(function (cell) {
        return cell.trim();
      });
    });
    if (rows.length > 1 && rows[1].every(function (cell) { return /^:?-{1,}:?$/.test(cell); })) {
      rows.splice(1, 1);
    }
    var html = '<div class="table-wrap"><table>';
    html += '<thead><tr>' + rows[0].map(function (cell) {
      return '<th>' + inline(cell) + '</th>';
    }).join('') + '</tr></thead>';
    if (rows.length > 1) {
      html += '<tbody>' + rows.slice(1).map(function (row) {
        return '<tr>' + row.map(function (cell) {
          return '<td>' + inline(cell) + '</td>';
        }).join('') + '</tr>';
      }).join('') + '</tbody>';
    }
    return html + '</table></div>';
  }

  function render(markdown) {
    var text = String(markdown == null ? '' : markdown).replace(/\r\n?/g, '\n');
    var lines = text.split('\n');
    var html = [];
    var index = 0;
    var listStack = [];

    function closeLists(depth) {
      while (listStack.length > (depth || 0)) {
        html.push(listStack.pop() === 'ol' ? '</ol>' : '</ul>');
      }
    }

    while (index < lines.length) {
      var line = lines[index];

      if (/^\s*```/.test(line)) {
        var lang = line.replace(/^\s*```/, '').trim();
        var body = [];
        index += 1;
        var closed = false;
        while (index < lines.length) {
          if (/^\s*```\s*$/.test(lines[index])) { closed = true; index += 1; break; }
          body.push(lines[index]);
          index += 1;
        }
        closeLists(0);
        html.push(codeBlock(lang, body.join('\n'), closed));
        continue;
      }

      if (/^\s*$/.test(line)) {
        closeLists(0);
        index += 1;
        continue;
      }

      if (/^\s*\|.*\|\s*$/.test(line)) {
        var tableLines = [];
        while (index < lines.length && /^\s*\|.*\|\s*$/.test(lines[index])) {
          tableLines.push(lines[index]);
          index += 1;
        }
        closeLists(0);
        html.push(renderTable(tableLines));
        continue;
      }

      var heading = /^(#{1,6})\s+(.*)$/.exec(line);
      if (heading) {
        closeLists(0);
        var level = heading[1].length;
        html.push('<h' + level + '>' + inline(heading[2].trim()) + '</h' + level + '>');
        index += 1;
        continue;
      }

      if (/^\s*([-*_])\s*\1\s*\1[\s\1]*$/.test(line)) {
        closeLists(0);
        html.push('<hr>');
        index += 1;
        continue;
      }

      if (/^\s*>/.test(line)) {
        var quote = [];
        while (index < lines.length && /^\s*>/.test(lines[index])) {
          quote.push(lines[index].replace(/^\s*>\s?/, ''));
          index += 1;
        }
        closeLists(0);
        html.push('<blockquote>' + render(quote.join('\n')) + '</blockquote>');
        continue;
      }

      var item = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/.exec(line);
      if (item) {
        var depth = Math.floor(item[1].replace(/\t/g, '  ').length / 2) + 1;
        var ordered = /\d/.test(item[2]);
        closeLists(depth - 1);
        var wanted = ordered ? 'ol' : 'ul';
        if (listStack.length < depth) {
          listStack.push(wanted);
          html.push(ordered ? '<ol>' : '<ul>');
        } else if (listStack[listStack.length - 1] !== wanted) {
          html.push(listStack.pop() === 'ol' ? '</ol>' : '</ul>');
          listStack.push(wanted);
          html.push(ordered ? '<ol>' : '<ul>');
        }
        html.push('<li>' + inline(item[3]) + '</li>');
        index += 1;
        continue;
      }

      var paragraph = [line];
      index += 1;
      while (index < lines.length && !/^\s*$/.test(lines[index]) &&
             !/^\s*(```|>|#{1,6}\s|\||([-*+]|\d+[.)])\s)/.test(lines[index])) {
        paragraph.push(lines[index]);
        index += 1;
      }
      closeLists(0);
      html.push('<p>' + inline(paragraph.join('\n')).replace(/\n/g, '<br>') + '</p>');
    }
    closeLists(0);
    return html.join('\n');
  }

  /* 把 markdown 纯文本降级为可读纯文本（用于标题、预览等场景） */
  function plain(markdown) {
    return String(markdown == null ? '' : markdown)
      .replace(/```[\s\S]*?```/g, ' ')
      .replace(/`([^`]*)`/g, '$1')
      .replace(/[*_>#~]/g, '')
      .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
      .replace(/\s+/g, ' ')
      .trim();
  }

  global.MD = { render: render, highlight: highlight, escapeHtml: escapeHtml, plain: plain, inline: inline };
})(window);
