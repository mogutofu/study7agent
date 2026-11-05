# -*- coding: utf-8 -*-
"""Study7Agent Win7 兼容性快速门禁（开发期替代反复开 Win7 虚拟机）。

用法（一律用随包解释器，纯标准库）：
    .\\python\\python.exe scripts\\win7-gate.py                  # 扫全项目，出中文报告
    .\\python\\python.exe scripts\\win7-gate.py --only syntax     # 只跑某一项
    .\\python\\python.exe scripts\\win7-gate.py --json            # 机器可读
    .\\python\\python.exe scripts\\win7-gate.py --quiet           # 只出失败项
    .\\python\\python.exe scripts\\win7-gate.py --fix-hints       # 追加逐条修复指引
    .\\python\\python.exe scripts\\win7-gate.py --path tmp-x      # 只扫某个目录（自测用）

为什么要有它：目标机是 Windows 7 SP1 x64 + 360 极速内核（≈Chrome 109 之前）+ PowerShell 2.0
+ .NET 3.5 + 随包 Python 3.8.10，而前端没有构建步骤（webui\\ui\\*.js 是浏览器直接加载的原生
JS）。也就是说：写错一个 `?.` 不是“编译不过”，而是老师机器上整个面板白屏。真机验收一次十几分钟，
所以把“能静态判定”的那部分做成秒级门禁，见 docs\\plane-architecture.md 第 7 节。

六项检查（--only 用编号或英文名）：
  1 syntax     前端语法门禁（Chrome 109 / 360 内核，无构建步骤）
  2 api        禁用 API 扫描（浏览器 / CSS / Win8+ Win32·WinRT）
  3 fonts      字体门禁（Win10/11 专有字体）
  4 python     Python 标准库门禁（随包 3.8 只许标准库 + 本项目模块）
  5 hardrules  项目硬约束（.ps1 BOM / .cmd ASCII+CRLF / 禁用字符 / eval·exec）
  6 fallback   特性回退门禁（能力探测 + 非空白兜底）

退出码：0 = 全部通过（可以有警告）；1 = 有错误；2 = 门禁自身用不了（参数/根目录问题）。

已知局限（报告末尾也会打印，不装作全覆盖）：
  * JS 的词法剥离是自写的：模板字符串与正则字面量按启发式识别（`/` 前面是标识符/右括号时按除号），
    极端写法（例如 `a++ / re/.test(x)`）可能判错；结论当高置信度线索，不当证明。
  * Python 的 import 用 tokenize 取，动态导入（importlib.import_module / __import__）扫不到。
  * CSS 的 flex gap 只查“同一条规则里同时有 display:flex 和 gap”，跨规则继承的 flex 容器查不到。
  * 类字段是近似判定：只看 class 体第一层的 `名字 =` 或 `名字;`。
"""

import argparse
import bisect
import contextlib
import io
import json
import os
import re
import sys
import sysconfig
import time
import tokenize

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SELF = os.path.abspath(__file__)

# 扫描时直接跳过的目录名（只看相对扫描根本身的名字，扫描根本身永远不跳）。
# python/ 是随包解释器（几万个标准库文件，不是本项目代码）；
# study7agent-src/ 是同步出去的镜像，扫它等于把同一份代码报两遍；
# browser-profile/ 是启动器给 360/Edge 用的**运行时用户目录**（Edge 自己写进去的
# 组件 JS 里有可选链和 Segoe UI Variable），它不是本项目源码，扫它只会淹没真问题。
SKIP_DIRS = {
    "python", "study7agent-src", "dist", "logs", "workspace", "config",
    "__pycache__", ".git", ".svn", ".vscode", ".idea", "node_modules",
    "browser-profile",
    # 随包第三方前端库（SmilesDrawer / JSME）：别人编好的产物，改不了也不该改。
    # 它们确实用了新语法（?? / ** / async）和 eval（GWT 的延迟绑定），所以**只能**跑在
    # 随包内核 Chromium 109 上；用老师的老 360 内核打开时，面板里的结构式/画板会降级
    # （chem.js 里做了能力探测，见 docs/third-party-licenses.md 与 roadmap 的 M5）。
    "vendor",
}

JS_EXT = (".js",)
CSS_EXT = (".css", ".html")
PY_EXT = (".py",)
PS1_EXT = (".ps1",)
CMD_EXT = (".cmd", ".bat")
FONT_EXT = (".js", ".css", ".html", ".py", ".ps1", ".cmd", ".bat", ".cs")


# ---------------------------------------------------------------------------
# 通用：文件收集 / 读取 / 行号
# ---------------------------------------------------------------------------


class LineIndex(object):
    """把字符偏移换算成 行:列（都从 1 起）。只在真的要报错时才建，省时间。"""

    def __init__(self, text):
        self.starts = [0]
        pos = text.find("\n")
        while pos >= 0:
            self.starts.append(pos + 1)
            pos = text.find("\n", pos + 1)

    def line_col(self, offset):
        if offset < 0:
            offset = 0
        idx = bisect.bisect_right(self.starts, offset) - 1
        return idx + 1, offset - self.starts[idx] + 1


class Source(object):
    """一个待扫描的文本文件：原始字节 + 解码后的文本（BOM 容错）+ 相对路径。"""

    __slots__ = ("path", "rel", "raw", "text", "encoding", "error", "lines", "imports",
                 "py_code", "py_comments", "py_note")

    def __init__(self, path, rel, raw, text, encoding, error):
        self.path = path
        self.rel = rel
        self.raw = raw
        self.text = text or ""
        self.encoding = encoding
        self.error = error
        self.lines = None
        self.imports = None
        self.py_code = None
        self.py_comments = None
        self.py_note = None

    def line_col(self, offset):
        if self.lines is None:
            self.lines = LineIndex(self.text)
        return self.lines.line_col(offset)


def make_console_safe():
    """中文 Windows 上输出不会因“某个字符编不出来”中断（与 webui/core.py 同一套做法）。

    * 编不出来的字符降级成 ?，绝不中断；
    * 输出被重定向（不是控制台）且没设 PYTHONIOENCODING 时改用 UTF-8 写，
      日志拿到别的机器上不会乱码；控制台本身仍用系统编码（PEP 528 已处理）。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            continue
        try:
            if not stream.isatty() and not os.environ.get("PYTHONIOENCODING"):
                stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError, OSError):
            continue


def decode_bytes(raw):
    """按 BOM 容错解码。返回 (text, encoding, error)。

    项目规矩是源码全 UTF-8；这里对 UTF-16 只做“能读出来”的容错，
    编码本身对不对由硬约束检查（第 5 项）负责报错。
    """
    if raw[:3] == b"\xef\xbb\xbf":
        try:
            return raw[3:].decode("utf-8"), "utf-8-sig", None
        except UnicodeDecodeError as exc:
            return None, "utf-8-sig", "UTF-8 BOM 之后不是合法 UTF-8：%s" % exc
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        enc = "utf-16-le" if raw[:2] == b"\xff\xfe" else "utf-16-be"
        try:
            return raw.decode(enc), enc, None
        except UnicodeDecodeError as exc:
            return None, enc, "UTF-16 解码失败：%s" % exc
    try:
        return raw.decode("utf-8"), "utf-8", None
    except UnicodeDecodeError as exc:
        try:
            return raw.decode("gbk"), "gbk", None
        except UnicodeDecodeError:
            return None, "unknown", "既不是 UTF-8 也不是 GBK：%s" % exc


def rel_of(path):
    try:
        return os.path.relpath(path, ROOT)
    except ValueError:                      # 跨盘符
        return path


def collect_files(roots):
    """按扫描根收集文件。返回 (files, skipped_dirs, missing_roots)。"""
    files = []
    skipped = set()
    missing = []
    for root in roots:
        if not os.path.isdir(root):
            missing.append(root)
            continue
        for base, dirs, names in os.walk(root):
            keep = []
            for name in dirs:
                if name.lower() in SKIP_DIRS or name.startswith("tmp-"):
                    skipped.add(name)
                else:
                    keep.append(name)
            dirs[:] = keep
            for name in names:
                if name.startswith("tmp-"):
                    # 项目约定：tmp-* 是临时草稿（AGENTS.md 要求收工时删掉），不参与门禁
                    skipped.add("tmp-* 临时文件")
                    continue
                path = os.path.join(base, name)
                if os.path.abspath(path) == SELF:
                    skipped.add("win7-gate.py（门禁自身）")
                    continue
                files.append(path)
    files.sort()
    return files, skipped, missing


# ---------------------------------------------------------------------------
# Finding / Report
# ---------------------------------------------------------------------------


class Finding(object):
    __slots__ = ("check", "level", "rel", "line", "col", "rule", "message")

    def __init__(self, check, level, rel, line, col, rule, message):
        self.check = check
        self.level = level                # error / warn
        self.rel = rel
        self.line = line
        self.col = col
        self.rule = rule
        self.message = message

    def where(self):
        if self.line:
            return "%s:%d:%d" % (self.rel, self.line, self.col)
        return self.rel

    def as_dict(self):
        return {"file": self.rel, "line": self.line, "col": self.col,
                "rule": self.rule, "level": self.level, "message": self.message}


class Report(object):
    """一份报告里的所有发现 + 门禁自身的局限说明。"""

    def __init__(self):
        self.findings = []
        self.notes = []

    def error(self, check, src, offset, rule, message):
        self._add(check, "error", src, offset, rule, message)

    def warn(self, check, src, offset, rule, message):
        self._add(check, "warn", src, offset, rule, message)

    def note(self, text):
        if text not in self.notes:
            self.notes.append(text)

    def _add(self, check, level, src, offset, rule, message):
        if src is None:
            rel, line, col = "（全局）", 0, 0
        elif offset is None:
            rel, line, col = src.rel, 0, 0
        else:
            rel = src.rel
            line, col = src.line_col(offset)
        self.findings.append(Finding(check, level, rel, line, col, rule, message))

    def for_check(self, key):
        return [item for item in self.findings if item.check == key]

    def count(self, key=None, level="error"):
        return len([item for item in self.findings
                    if item.level == level and (key is None or item.check == key)])


# ---------------------------------------------------------------------------
# JS 词法剥离：注释 / 字符串 / 模板字符串 / 正则字面量
# ---------------------------------------------------------------------------

# 这些字符或关键字之后出现的 `/` 更可能是正则字面量而不是除号（启发式，见文件头局限说明）。
# `+` `-` 故意没放进来：`i++ / 2` 那种写法比 `a - /re/` 常见得多。
REGEX_OK_BEFORE = set("(,=:[!&|?{};>")
REGEX_OK_WORDS = set((
    "return", "typeof", "instanceof", "in", "of", "new", "delete", "void",
    "case", "do", "else", "throw", "yield", "await",
))
JS_SPECIAL = re.compile(r"[/'\"`]")
JS_WORD_TAIL = re.compile(r"[\w$]+$")


class JsStrip(object):
    """把 JS 源码里“不是代码”的部分换成空格，偏移与行号一一对应。

    code        ：剥干净后的源码（与原文字节偏移等长）
    regex_spans ：[(start, end, flags)] 正则字面量，用来查 `d` 标志
    """

    def __init__(self, text, keep_strings=False):
        self.text = text
        self.keep_strings = keep_strings        # 只查字体名时用：保留字符串，只抹注释
        self.out = [" "] * len(text)
        spans = []
        self._scan(0, len(text), False, spans)
        self.code = "".join(self.out)
        self.regex_spans = spans

    def _copy(self, start, end):
        if end > start:
            self.out[start:end] = self.text[start:end]

    def _scan(self, i, end, in_template, spans):
        text = self.text
        prev = ""
        word = ""
        depth = 0
        while i < end:
            found = JS_SPECIAL.search(text, i, end)
            if found is None:
                self._copy(i, end)
                break
            stop = found.start()
            self._copy(i, stop)
            tail = text[i:stop].rstrip()
            if tail:                                  # 从刚拷进去的代码恢复 prev / word
                prev = tail[-1]
                tail_word = JS_WORD_TAIL.search(tail)
                word = tail_word.group(0) if tail_word else ""
            i = stop
            ch = text[i]

            if ch == "/" and text[i:i + 2] == "//":
                nl = text.find("\n", i, end)
                i = end if nl < 0 else nl
                continue
            if ch == "/" and text[i:i + 2] == "/*":
                close = text.find("*/", i + 2, end)
                i = end if close < 0 else close + 2
                continue
            if ch == "'" or ch == '"':
                j = i + 1
                while j < end:
                    if text[j] == "\\":
                        j += 2
                        continue
                    if text[j] == ch:
                        j += 1
                        break
                    if text[j] == "\n":               # 未闭合的字符串：按行尾收场
                        break
                    j += 1
                if self.keep_strings:
                    self._copy(i, j)
                i = j
                prev, word = ch, ""
                continue
            if ch == "`":
                piece = i
                i += 1
                while i < end:
                    if text[i] == "\\":
                        i += 2
                        continue
                    if text[i] == "`":
                        i += 1
                        break
                    if text[i:i + 2] == "${":
                        if self.keep_strings:
                            self._copy(piece, i)
                        i = self._scan(i + 2, end, True, spans)
                        piece = i
                        continue
                    i += 1
                if self.keep_strings:
                    self._copy(piece, i)
                prev, word = "`", ""
                continue
            if ch == "/" and (prev in REGEX_OK_BEFORE or word in REGEX_OK_WORDS):
                j = i + 1
                in_class = False
                while j < end:
                    c = text[j]
                    if c == "\\":
                        j += 2
                        continue
                    if c == "\n":
                        break
                    if c == "[":
                        in_class = True
                    elif c == "]":
                        in_class = False
                    elif c == "/" and not in_class:
                        j += 1
                        break
                    j += 1
                flags = ""
                while j < end and text[j].isalpha():
                    flags += text[j]
                    j += 1
                spans.append((i, j, flags))
                if self.keep_strings:
                    self._copy(i, j)
                i = j
                prev, word = "/", ""
                continue
            if in_template and ch == "}":
                if depth == 0:
                    return i + 1
                depth -= 1
            elif in_template and ch == "{":
                depth += 1
            self._copy(i, i + 1)
            word = ch if (ch.isalnum() or ch in "_$") else ""
            prev = ch
            i += 1
        return i


# ---------------------------------------------------------------------------
# Python：注释/字符串剥离（tokenize，精确）+ import 提取
# ---------------------------------------------------------------------------


def _blank_spans(text, spans):
    """把 spans 里的字符抹成空格（换行保留），一次线性扫描，别按字符循环。"""
    out = []
    pos = 0
    for begin, end in sorted(spans):
        if begin < pos:
            continue
        out.append(text[pos:begin])
        out.append(re.sub(r"[^\n]", " ", text[begin:end]))
        pos = end
    out.append(text[pos:])
    return "".join(out)


def py_analyze(src):
    """返回 (code, imports, comment_free, note)，每个文件算一次并缓存在 Source 上。

    code         注释与字符串都被抹成空白（长度不变，行号对得上）
    imports      [(顶层模块名, 行号)]；相对导入（from . import x）不列
    comment_free 只抹注释、保留字符串（用来查 WinRT 命名空间这类写在字符串里的名字）
    """
    text = src.text
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError, ValueError) as exc:
        note = ("%s 无法 tokenize（%s），本文件的 import 与 eval/exec 检查退化为朴素剥离，"
                "结果可能不准" % (src.rel, exc.__class__.__name__))
        code = re.sub(r"('''[\s\S]*?'''|\"\"\"[\s\S]*?\"\"\"|'[^'\n]*'|\"[^\"\n]*\")",
                      lambda m: " " * len(m.group(0)), text)
        code = re.sub(r"#[^\n]*", lambda m: " " * len(m.group(0)), code)
        return code, [], code, note

    starts = LineIndex(text).starts

    def line_start(row):
        return starts[row - 1] if row - 1 < len(starts) else len(text)

    string_spans = []
    comment_spans = []
    for tok in tokens:
        begin = line_start(tok.start[0]) + tok.start[1]
        end = line_start(tok.end[0]) + tok.end[1]
        if tok.type == tokenize.STRING:
            string_spans.append((begin, end))
        elif tok.type == tokenize.COMMENT:
            comment_spans.append((begin, end))

    imports = []
    state = None                          # None / "from" / "after-from" / "import"
    dots = 0
    alias = False
    for tok in tokens:
        kind = tok.type
        if kind in (tokenize.COMMENT, tokenize.STRING):
            continue
        if kind in (tokenize.NEWLINE, tokenize.ENDMARKER, tokenize.NL,
                    tokenize.INDENT, tokenize.DEDENT):
            state, dots, alias = None, 0, False
            continue
        if state == "from":
            if kind == tokenize.OP and tok.string == ".":
                dots += 1
                continue
            if kind == tokenize.NAME:
                if dots == 0:
                    imports.append((tok.string, tok.start[0]))
                state = "after-from"      # 后面还有 .server / import 之类，得吃干净
                continue
            state = None
            continue
        if state == "after-from":
            if kind == tokenize.NAME and tok.string == "import":
                state = None
                continue
            if kind == tokenize.NAME or (kind == tokenize.OP and tok.string == "."):
                continue
            state = None
            continue
        if state == "import":
            if alias:
                alias = False
                if kind == tokenize.OP and tok.string == ",":
                    continue
                state = None
                continue
            if kind == tokenize.NAME and tok.string == "as":
                alias = True
                continue
            if kind == tokenize.NAME:
                imports.append((tok.string, tok.start[0]))
                continue
            if kind == tokenize.OP and tok.string == ",":
                continue
            state = None
            continue
        if kind == tokenize.NAME and tok.string == "from":
            state, dots = "from", 0
            continue
        if kind == tokenize.NAME and tok.string == "import":
            state, alias = "import", False
            continue
    return (_blank_spans(text, string_spans + comment_spans), imports,
            _blank_spans(text, comment_spans), None)


def py_parts(src):
    """缓存版 py_analyze：同一文件在多项检查里只解析一次。"""
    if src.py_code is None:
        code, imports, comments, note = py_analyze(src)
        src.py_code, src.imports, src.py_comments, src.py_note = code, imports, comments, note
    return src.py_code, src.imports, src.py_comments, src.py_note


# ---------------------------------------------------------------------------
# 第 1 项：前端语法门禁
# ---------------------------------------------------------------------------

JS_SYNTAX_RULES = (
    ("js-optional-chain", re.compile(r"\?\.(?!\d)"), "可选链 ?."),
    ("js-nullish", re.compile(r"\?\?"), "空值合并 ?? / ??="),
    ("js-logical-assign", re.compile(r"(\|\||&&)="), "逻辑赋值 ||= / &&="),
    ("js-exponent", re.compile(r"\*\*"), "幂运算符 **"),
    ("js-private-field", re.compile(r"#[\w$]"), "类私有字段 #x"),
    ("js-array-at", re.compile(r"\.at\s*\("), "Array/String 的 at( )"),
    ("js-await", re.compile(r"\bawait\b"), "await（含顶层 await）"),
    ("js-async", re.compile(r"\basync\b"), "async 函数"),
    ("js-structured-clone", re.compile(r"\bstructuredClone\s*\("), "structuredClone( )"),
    ("js-object-hasown", re.compile(r"\bObject\s*\.\s*hasOwn\s*\("), "Object.hasOwn( )"),
    ("js-replace-all", re.compile(r"\.replaceAll\s*\("), "String.replaceAll( )"),
    ("js-dynamic-import", re.compile(r"\bimport\s*\("), "动态 import( )"),
    ("js-module-export", re.compile(r"\bexport\b"), "ESM export"),
)

JS_CLASS_HEAD = re.compile(r"\bclass\b[^;{}]*\{")
JS_CLASS_FIELD = re.compile(r"[A-Za-z_$][\w$]*\s*(?:=|;)")


def find_class_fields(code):
    """近似找出 class 体第一层的字段声明（`x = 1;` / `x;`）。

    只看 class 体（花括号深度 1）里、前面最近的非空白字符是 `{` 或 `;` 的
    `名字 =` / `名字;`；`名字(...)` 是方法，不算。
    """
    hits = []
    for head in JS_CLASS_HEAD.finditer(code):
        depth = 1
        i = head.end()
        while i < len(code) and depth:
            ch = code[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            i += 1
        body_end = i
        depth = 1
        j = head.end()
        while j < body_end:
            ch = code[j]
            if ch == "{":
                depth += 1
                j += 1
                continue
            if ch == "}":
                depth -= 1
                j += 1
                continue
            if ch in " \t\n;":
                j += 1
                continue
            if depth == 1:
                candidate = JS_CLASS_FIELD.match(code, j)
                if candidate:
                    before = j - 1
                    while before >= head.end() and code[before] in " \t\n":
                        before -= 1
                    if before < head.end() or code[before] in "{;":
                        hits.append(j)
                        j = candidate.end()
                        continue
            j += 1
    return hits


def check_syntax(ctx, rep):
    """第 1 项：webui/ui/*.js 必须是不经构建、Chrome 109 / 360 内核直接能跑的语法。"""
    check = "syntax"
    for src in ctx.sources(JS_EXT):
        strip = JsStrip(src.text)
        code = strip.code
        for rule, pattern, label in JS_SYNTAX_RULES:
            for match in pattern.finditer(code):
                rep.error(check, src, match.start(), rule,
                          "出现%s：Win7 上的 360 旧内核会直接抛 SyntaxError，"
                          "整个脚本文件一行都不执行（面板白屏）" % label)
        for start, _end, flags in strip.regex_spans:
            if "d" in flags:
                rep.error(check, src, start, "js-regex-d-flag",
                          "正则用了 d（indices）标志：旧内核不认识这个标志，写成正则的地方会抛错")
        for offset in find_class_fields(code):
            rep.error(check, src, offset, "js-class-field",
                      "出现类字段（class 体第一层的 `名字 = 值`）：旧内核不支持，脚本直接在类体上报语法错")
    if ctx.by_ext.get(".js"):
        rep.note("JS 语法检查建立在自写词法剥离之上（注释 / 字符串 / 模板字符串 / 正则字面量已排除）；"
                 "正则字面量按启发式识别，极端写法可能判错。")


# ---------------------------------------------------------------------------
# 第 2 项：禁用 API 扫描
# ---------------------------------------------------------------------------

# 允许出现，但必须有无它也能跑的兜底路径 → 警告
JS_SOFT_API = (
    ("navigator-gpu", re.compile(r"\bnavigator\s*\.\s*gpu\b"), "navigator.gpu（WebGPU）"),
    ("webgpu", re.compile(r"\bWebGPU\b"), "WebGPU"),
    ("gpu-buffer", re.compile(r"\bGPUBuffer\b"), "GPUBuffer"),
    ("offscreen-canvas", re.compile(r"\bOffscreenCanvas\b"), "OffscreenCanvas"),
    ("create-image-bitmap", re.compile(r"\bcreateImageBitmap\s*\("),
     "createImageBitmap( )（架构文档第 7 节点名）"),
)

# Win7 的 360 内核上不存在，出现即错误
JS_HARD_API = (
    ("show-open-file-picker", re.compile(r"\bshowOpenFilePicker\s*\("),
     "showOpenFilePicker( )（File System Access API）"),
    ("show-save-file-picker", re.compile(r"\bshowSaveFilePicker\s*\("),
     "showSaveFilePicker( )（File System Access API）"),
    ("scheduler-post-task", re.compile(r"\bscheduler\s*\.\s*postTask\s*\("),
     "scheduler.postTask( )（Prioritized Task Scheduling）"),
    ("navigator-share", re.compile(r"\bnavigator\s*\.\s*share\s*\("), "navigator.share( )"),
)

CSS_HARD = (
    ("css-has", re.compile(r":has\s*\("), "CSS :has( )"),
    ("css-color-mix", re.compile(r"color-mix\s*\("), "CSS color-mix( )"),
    ("css-container", re.compile(r"@container\b"), "CSS @container"),
    ("css-aspect-ratio", re.compile(r"(?<![\w-])aspect-ratio\s*:"), "CSS aspect-ratio:"),
)

CSS_DISPLAY_FLEX = re.compile(r"display\s*:\s*(?:inline-)?flex\b")
CSS_GAP = re.compile(r"(?<![\w-])(?:gap|row-gap|column-gap)\s*:")

# Win8+ 才有的 Win32 / WinRT 调用
WIN8_PLUS_DLLS = ("dwmapi", "shcore", "combase")
WIN8_PLUS_SYMBOLS = (
    "SetProcessDpiAwarenessContext", "SetThreadDpiAwarenessContext", "GetDpiForWindow",
    "GetDpiForSystem", "GetSystemMetricsForDpi", "AdjustWindowRectExForDpi",
    "EnableNonClientDpiScaling", "RoInitialize", "RoGetActivationFactory",
)
WIN8_WINRT_NAMESPACES = ("Windows.UI.", "Windows.Foundation")
WIN8_SYMBOL_RE = re.compile("|".join(re.escape(item) for item in WIN8_PLUS_SYMBOLS))
WINRT_RE = re.compile("|".join(re.escape(item) for item in WIN8_WINRT_NAMESPACES))
WINDLL_RE = re.compile(r"ctypes\s*\.\s*windll\s*\.\s*([\w.]+)")


def css_blocks(text):
    """极简 CSS 规则切分：(选择器, 声明体, 体起始偏移)。

    手写线性扫描，不用正则：`([^{}]+)\\{([^{}]*)\\}` 在真实样式表上会退化成 O(n^2)
    （实测 style.css 一项就要 4.9 秒），而这里只是找成对的 `{` `}`。
    """
    pos = 0
    while True:
        open_at = text.find("{", pos)
        if open_at < 0:
            return
        close_at = text.find("}", open_at)
        if close_at < 0:
            return
        yield text[pos:open_at], text[open_at + 1:close_at], open_at + 1
        pos = close_at + 1


def check_api(ctx, rep):
    """第 2 项：浏览器 / CSS / Win8+ Win32·WinRT 的禁用 API。"""
    check = "api"
    for src in ctx.sources(JS_EXT):
        code = JsStrip(src.text).code
        for rule, pattern, label in JS_HARD_API:
            for match in pattern.finditer(code):
                rep.error(check, src, match.start(), rule,
                          "使用了%s：Win7 的 360 内核没有这个 API，"
                          "调用处会抛 TypeError，功能整条断掉" % label)
        for rule, pattern, label in JS_SOFT_API:
            for match in pattern.finditer(code):
                rep.warn(check, src, match.start(), rule,
                         "出现了%s：允许用，但同一条路径必须有「没有它也能跑」的兜底"
                         "（探测失败就走 Canvas2D / SVG 分支），否则老机器上是白屏" % label)

    for src in ctx.sources(CSS_EXT):
        # 抹注释必须等长（_blank），不能直接删：删了以后 match 的偏移就不再对应原文行号
        text = HTML_COMMENT_RE.sub(_blank, src.text)
        text = BLOCK_COMMENT_RE.sub(_blank, text)
        for rule, pattern, label in CSS_HARD:
            for match in pattern.finditer(text):
                rep.error(check, src, match.start(), rule,
                          "使用了%s：Win7 的 360 旧内核不支持，"
                          "命中的样式会整条失效（不是降级，是当作无效声明丢掉）" % label)
        for selector, body, offset in css_blocks(text):
            if CSS_DISPLAY_FLEX.search(body):
                gap = CSS_GAP.search(body)
                if gap:
                    rep.warn(check, src, offset + gap.start(), "css-flex-gap",
                             "flex 容器里用了 gap（选择器 %s）：老内核的 flexbox 不认 gap，"
                             "间距会全塌成 0（不报错，只是难看），所以只算警告" % selector.strip()[:60])

    for src in ctx.sources(PY_EXT):
        code, _imports, comments, _note = py_parts(src)
        for match in WINDLL_RE.finditer(code):
            dll = match.group(1).split(".")[0].lower()
            if dll in WIN8_PLUS_DLLS:
                rep.error(check, src, match.start(), "py-win8-dll",
                          "调用了 %s.dll：这个库 Windows 7 上没有（Win8+ 才引入），"
                          "ctypes.windll 取属性就抛 AttributeError" % dll)
        for match in WIN8_SYMBOL_RE.finditer(code):
            rep.error(check, src, match.start(), "py-win8-api",
                      "调用了 %s：Win8+ 才有的 API，Win7 上必定失败；"
                      "Win7 上等效的是 SetProcessDPIAware" % match.group(0))
        for match in WINRT_RE.finditer(comments):
            rep.error(check, src, match.start(), "py-winrt",
                      "出现 WinRT 命名空间 %s：WinRT 是 Win8+ 才有的，Win7 上不存在"
                      % match.group(0))
    rep.note("Win8+ API 覆盖架构文档点名的 DLL/函数名（另加 shcore / combase / WinRT 命名空间）；"
             "动态拼名字（getattr(dll, 变量)）扫不到。")


# ---------------------------------------------------------------------------
# 第 3 项：字体门禁
# ---------------------------------------------------------------------------

BANNED_FONTS = ("Segoe UI Variable", "Segoe Fluent Icons", "Segoe UI Emoji")
BANNED_FONT_RE = re.compile("|".join(re.escape(item) for item in BANNED_FONTS), re.I)
BLOCK_COMMENT_RE = re.compile(r"/\*[\s\S]*?\*/")
HTML_COMMENT_RE = re.compile(r"<!--[\s\S]*?-->")


def _blank(match):
    return re.sub(r"[^\n]", " ", match.group(0))


def font_text(src):
    """字体检查用的文本：抹掉注释，**保留字符串**。

    注释里写“别用 Segoe UI Emoji”是正常的说明文字（icons.js 开头就是这样），
    不该被当成违规；但字符串里的字体名（font-family / Font = "..."）必须抓。
    """
    low = src.rel.lower()
    if low.endswith(".py"):
        return py_parts(src)[2]                       # 只抹注释那份
    if low.endswith(".js"):
        return JsStrip(src.text, keep_strings=True).code
    if low.endswith(".css"):
        return BLOCK_COMMENT_RE.sub(_blank, src.text)
    if low.endswith(".html"):
        return BLOCK_COMMENT_RE.sub(_blank, HTML_COMMENT_RE.sub(_blank, src.text))
    return src.text


def check_fonts(ctx, rep):
    """第 3 项：Win10/11 专有字体不许出现（图标一律内联 SVG）。"""
    check = "fonts"
    for src in ctx.sources(FONT_EXT):
        for match in BANNED_FONT_RE.finditer(font_text(src)):
            rep.error(check, src, match.start(), "banned-font",
                      "使用了 Win10/11 专有字体 %s：Win7 上没有这个字体，"
                      "文字会退化成方框（图标请用内联 SVG，别靠字体图标）" % match.group(0))
    rep.note("字体检查扫这些后缀：%s，并已排除注释（注释里说明“别用某字体”不算违规）；"
             "Segoe UI / Microsoft YaHei 是 Win7 自带的，允许。" % "、".join(FONT_EXT))


# ---------------------------------------------------------------------------
# 第 4 项：Python 标准库门禁
# ---------------------------------------------------------------------------


def stdlib_names():
    """3.8 上没有 sys.stdlib_module_names，所以自己扫出来。

    做法：builtin_module_names ∪ sysconfig stdlib 目录下的顶层 .py / 包
    ∪ 解释器 DLLs 目录下的扩展模块（_socket、_ssl 这些不在 Lib 里）。
    选它的理由：随包解释器就是发布出去的那个解释器，扫出来的集合与真实运行时严格一致，
    不会像硬编码清单那样随 Python 版本或平台腐烂；代价只是启动时列两个目录（毫秒级）。
    """
    names = set(sys.builtin_module_names)
    names.update({"__future__", "os.path", "ntpath", "posixpath"})
    folders = [sysconfig.get_paths().get("stdlib"),
               os.path.join(os.path.dirname(sys.executable), "DLLs")]
    for folder in folders:
        if not folder:
            continue
        try:
            entries = os.listdir(folder)
        except OSError:
            continue
        for entry in entries:
            low = entry.lower()
            if low.endswith(".py"):
                names.add(entry[:-3])
            elif low.endswith((".pyd", ".so", ".dll")):
                names.add(entry.split(".")[0])
            elif os.path.isfile(os.path.join(folder, entry, "__init__.py")):
                names.add(entry)
    return names


def project_modules(files):
    """本项目自己的模块名（webui/scripts 里的 .py 与包），作为 import 白名单。"""
    names = set()
    for path in files:
        if path.lower().endswith(".py"):
            names.add(os.path.splitext(os.path.basename(path))[0])
        if os.path.basename(path) == "__init__.py":
            names.add(os.path.basename(os.path.dirname(path)))
    return names


def check_python(ctx, rep):
    """第 4 项：webui\\*.py 与 scripts\\*.py 只许标准库 + 本项目模块。"""
    check = "python"
    allowed = stdlib_names()
    local = project_modules(ctx.files)
    extra = set(ctx.allow_modules)
    rep.note("标准库名单来自 sys.builtin_module_names + 随包解释器的 Lib/ 与 DLLs/ 目录"
             "（3.8 没有 sys.stdlib_module_names）；项目内部模块自动白名单 %d 个，"
             "另可用 --allow-module 追加。" % len(local))
    for src in ctx.sources(PY_EXT):
        _code, imports, _comments, note = py_parts(src)
        if note:
            rep.note(note)
        for module, lineno in imports:
            if module in allowed or module in local or module in extra:
                continue
            rep.error(check, src, _offset_of_line(src.text, lineno), "third-party-import",
                      "导入了非标准库模块 '%s'：本项目只允许 Python 3.8 标准库 + "
                      "本项目自己的模块（不许 pip、不许第三方包）" % module)


def _offset_of_line(text, lineno):
    pos = 0
    for _ in range(max(lineno - 1, 0)):
        nxt = text.find("\n", pos)
        if nxt < 0:
            return len(text)
        pos = nxt + 1
    return pos


# ---------------------------------------------------------------------------
# 第 5 项：项目硬约束
# ---------------------------------------------------------------------------


def blocked_glyph_ranges():
    """从 webui/test_webui.py 导入 TestUIFiles.BLOCKED_GLYPH_RANGES（唯一来源，不抄一份）。"""
    webui = os.path.join(ROOT, "webui")
    if webui not in sys.path:
        sys.path.insert(0, webui)
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
            import test_webui                     # noqa: E402
        return tuple(test_webui.TestUIFiles.BLOCKED_GLYPH_RANGES), None
    except Exception as exc:                      # noqa: BLE001 - 这里就是要兜住一切
        return None, "%s: %s" % (exc.__class__.__name__, exc)


def glyph_scope(rel):
    """与 test_webui.py 对齐的范围：webui/ui/*.js 与 webui/*.py。

    用“路径里有 ui 目录” / “路径里有 webui 目录”判断，这样 --path 指到别处也能用。
    """
    parts = [piece.lower() for piece in re.split(r"[\\/]+", rel)]
    if parts and parts[-1].endswith(".js") and "ui" in parts[:-1]:
        return True
    if parts and parts[-1].endswith(".py") and "webui" in parts[:-1]:
        return True
    return False


def check_hardrules(ctx, rep):
    """第 5 项：.ps1 的 BOM、.cmd 的 ASCII+CRLF、禁用字符、eval/exec。"""
    check = "hardrules"

    for src in ctx.sources(PS1_EXT):
        if src.raw[:3] != b"\xef\xbb\xbf":
            head = " ".join("%02X" % byte for byte in src.raw[:3]) or "文件是空的"
            rep.error(check, src, 0, "ps1-bom",
                      "不是 UTF-8 带 BOM（头三字节是 %s）：Windows PowerShell 会按本地代码页"
                      "（GBK）读，中文一乱码就可能蹦出引号/花括号，报一堆莫名其妙的语法错误"
                      % head)
        elif src.error:
            rep.error(check, src, 0, "ps1-encoding", src.error)

    for src in ctx.sources(CMD_EXT):
        for index, byte in enumerate(src.raw):
            if byte > 0x7F:
                rep.error(check, src, index, "cmd-ascii",
                          "第 %d 字节是 0x%02X，不是纯 ASCII：中文 Win7 的 cmd.exe 按本地代码页"
                          "解析批处理，非 ASCII 会乱码甚至把命令截断"
                          % (index, byte))
                break
        bad_lf = _lone_lf_lines(src.raw)
        if bad_lf:
            rep.error(check, src, _offset_of_line(src.text, bad_lf[0]), "cmd-crlf",
                      "第 %d 行是 LF 换行（全文 LF 行共 %d 行）：.cmd 必须 CRLF，"
                      "cmd.exe 会把孤立的 LF 当普通字符" % (bad_lf[0], len(bad_lf)))

    if ctx.glyph_ranges is None:
        rep.error(check, None, None, "glyph-ranges-unavailable",
                  "拿不到 webui/test_webui.py 的 BLOCKED_GLYPH_RANGES，禁用字符这一条没法判：%s"
                  % ctx.glyph_error)
    else:
        # 区间来自 test_webui.py，编译成一条正则按 C 速度扫，别在 Python 里逐字符比。
        glyph_re = re.compile("[%s]" % "".join(
            "%s-%s" % (re.escape(chr(low)), re.escape(chr(high)))
            for low, high in ctx.glyph_ranges))
        for src in ctx.sources(JS_EXT + PY_EXT):
            if not glyph_scope(src.rel):
                continue
            # ★ 口径（已定）：**界面文件算错误，.py 算警告**。
            #   .py 里这些 → ✓ ▸ ★ 基本都在注释和文档字符串里，到不了老师的屏幕；
            #   真走到输出时 core.make_console_safe 会兜住（它存在的意义就是这个）。
            #   为了注释里的箭头改几百行源码，收益为零、风险是碰坏中文。
            #   界面文件（webui/ui/*）保持错误级 —— 那是老师真会看到的方框。
            is_ui = src.rel.lower().endswith(".js")
            if is_ui:
                why = "Win7 上没有对应字体，界面里显示成空白或方框"
            else:
                why = ("Win7 上没有对应字形：日志/控制台里是方框，输出重定向到文件时"
                       "按 GBK 编码还会直接抛 UnicodeEncodeError（core.make_console_safe 就是为此）；"
                       "在注释/文档字符串里不影响运行，所以只警告")
            for match in glyph_re.finditer(src.text):
                message = ("出现 U+%04X：%s；图标请用内联 SVG，文字用中文或 ASCII"
                           % (ord(match.group(0)), why))
                if is_ui:
                    rep.error(check, src, match.start(), "blocked-glyph", message)
                else:
                    rep.warn(check, src, match.start(), "blocked-glyph-py", message)

    # eval 前面只排除单词字符（好抓到 window.eval( )）；exec 前面还要排除 `.`，
    # 否则 /re/.exec(s) 这种正则方法会被误报。
    eval_re = re.compile(r"(?<![\w])eval\s*\(")
    exec_re = re.compile(r"(?<![\w.])exec\s*\(")
    for src in ctx.sources(PY_EXT):
        code, _imports, _comments, _note = py_parts(src)
        for pattern, rule, label in ((eval_re, "py-eval", "eval"), (exec_re, "py-exec", "exec")):
            for match in pattern.finditer(code):
                rep.error(check, src, match.start(), rule,
                          "出现 %s 调用：项目硬禁（plot.py 的表达式求值就是为此手写的解析器）"
                          % label)
    for src in ctx.sources(JS_EXT):
        code = JsStrip(src.text).code
        for pattern, rule, label in ((eval_re, "js-eval", "eval"), (exec_re, "js-exec", "exec")):
            for match in pattern.finditer(code):
                rep.error(check, src, match.start(), rule,
                          "出现 %s 调用：项目硬禁（正则对象的 .exec() 不算，这里指独立调用）"
                          % label)


def _lone_lf_lines(raw):
    """返回“只有 LF 没有 CR”的行号（1 起）。"""
    lines = []
    number = 1
    index = 0
    length = len(raw)
    while index < length:
        if raw[index] == 0x0A:
            if index == 0 or raw[index - 1] != 0x0D:
                lines.append(number)
            number += 1
        index += 1
    return lines


# ---------------------------------------------------------------------------
# 第 6 项：特性回退门禁
# ---------------------------------------------------------------------------

PROBE_RES = (
    re.compile(r"getContext\s*\("),
    re.compile(r"createElement\s*\(\s*['\"]canvas['\"]\s*\)"),
    re.compile(r"WebGLRenderingContext"),
    re.compile(r"supportsWebGL|hasWebGL|canUseWebGL|webglOK", re.I),
)
FALLBACK_RES = (
    re.compile(r"try\s*\{"),
    re.compile(r"catch\s*\("),
    re.compile(r"兜底|回退|降级|退化"),
    re.compile(r"fallback", re.I),
    re.compile(r"if\s*\(\s*!\s*(?:gl|ctx|webgl|canvas)\b", re.I),
)
FALLBACK_TARGETS = (os.path.join("webui", "ui", "app.js"),
                    os.path.join("webui", "ui", "plane.js"))


def _first_match(patterns, text):
    best = None
    for pattern in patterns:
        match = pattern.search(text)
        if match and (best is None or match.start() < best):
            best = match.start()
    return best


def check_fallback(ctx, rep):
    """第 6 项：前端主渲染入口必须有“能力探测 + 回退”。"""
    check = "fallback"
    for rel in FALLBACK_TARGETS:
        path = None
        for root in ctx.roots:
            candidate = os.path.join(root, rel)
            if os.path.isfile(candidate):
                path = candidate
                break
        name = os.path.basename(rel)
        if path is None:
            if name == "app.js" and ctx.roots == [ROOT]:
                rep.error(check, None, None, "missing-entry",
                          "%s 找不到了，前端主渲染入口没了吗？" % rel)
            elif name == "plane.js":
                rep.note("%s 还不存在（plane 渲染层还没落地，架构文档 P5 阶段），"
                         "本项对它暂时跳过。" % rel.replace("\\", "/"))
            continue
        src = ctx.source_of(path)
        if src is None:
            continue
        code = JsStrip(src.text).code
        probe = _first_match(PROBE_RES, code)
        fallback = _first_match(FALLBACK_RES, code)
        if probe is None:
            rep.warn(check, src, 0, "no-capability-probe",
                     "%s 里看不到任何能力探测（例如 "
                     "document.createElement('canvas').getContext('webgl')）："
                     "整个面板现在是硬依赖，一旦老内核缺某个能力就是白屏，建议现在补上"
                     "「探测 -> 换分支」的写法" % rel.replace("\\", "/"))
        elif fallback is None:
            rep.warn(check, src, probe, "no-fallback-branch",
                     "%s 有探测但没有回退痕迹（找不到 catch / 兜底 / 降级分支）："
                     "探测完必须真的走另一条路，不能只探测一下" % rel.replace("\\", "/"))
        else:
            rep.note("%s：能力探测在第 %d 行，回退痕迹在第 %d 行。"
                     % (rel.replace("\\", "/"), src.line_col(probe)[0], src.line_col(fallback)[0]))
    rep.note("回退门禁是「关键词 + 结构」的启发式：能证明有痕迹，不能证明回退分支真的能用——"
             "后者要靠无头浏览器断言和真机验收。")


# ---------------------------------------------------------------------------
# 检查注册表 / 修复指引
# ---------------------------------------------------------------------------

CHECK_ORDER = (
    ("syntax", "1", "前端语法门禁（Chrome 109 / 360 内核，无构建步骤）", check_syntax),
    ("api", "2", "禁用 API 扫描（浏览器 / CSS / Win8+ Win32·WinRT）", check_api),
    ("fonts", "3", "字体门禁（Win10/11 专有字体）", check_fonts),
    ("python", "4", "Python 标准库门禁（随包 3.8 只许标准库 + 本项目模块）", check_python),
    ("hardrules", "5", "项目硬约束（.ps1 BOM / .cmd ASCII+CRLF / 禁用字符 / eval·exec）",
     check_hardrules),
    ("fallback", "6", "特性回退门禁（能力探测 + 非空白兜底）", check_fallback),
)

RULE_FIX = {
    "js-optional-chain": "`a?.b` 写成 `(a && a.b)`；`a?.[i]` 写成 `(a && a[i])`；"
                         "`a?.(x)` 写成 `(a && a(x))`。",
    "js-nullish": "`a ?? b` 写成 `(a !== null && a !== undefined) ? a : b`；`??=` 拆成普通 if。",
    "js-logical-assign": "`a ||= b` 写成 `a = a || b`；`a &&= b` 写成 `a = a && b`。",
    "js-exponent": "`a ** b` 写成 `Math.pow(a, b)`。",
    "js-private-field": "`#x` 改成普通属性，或者放进闭包变量。",
    "js-class-field": "类字段收进构造函数：`function F(){ this.x = 1; }`，别用 class 体赋值。",
    "js-array-at": "`arr.at(-1)` 写成 `arr[arr.length - 1]`。",
    "js-await": "去掉 await，改成 `.then(...)` 回调（本项目前端刻意不用 async/await）。",
    "js-async": "函数去掉 async，改成返回 Promise 或走回调。",
    "js-structured-clone": "用 `JSON.parse(JSON.stringify(x))` 或手写深拷贝。",
    "js-object-hasown": "用 `Object.prototype.hasOwnProperty.call(obj, key)`。",
    "js-replace-all": "用 `split(x).join(y)`，或者带 g 标志的 replace。",
    "js-dynamic-import": "别动态 import：按 <script> 顺序加载，函数挂到 window 上。",
    "js-module-export": "去掉 export：本项目不是 ES 模块，直接定义全局函数。",
    "js-regex-d-flag": "去掉正则的 d 标志；需要匹配位置就自己用 indexOf 算。",
    "show-open-file-picker": "用 <input type=file> 取文件（老内核只有这条路）。",
    "show-save-file-picker": "用 <a download> + Blob URL 触发下载，或让服务端写盘。",
    "scheduler-post-task": "用 `Promise.resolve().then(...)` 或 `setTimeout(..., 0)`。",
    "navigator-share": "去掉分享 API：复制到剪贴板，或者给一个能手动复制的文本框。",
    "css-has": "改成显式的 class（需要时用 JS 加类名）：`:has()` 在老内核上整条规则失效。",
    "css-color-mix": "颜色提前算好写死，或者用 rgba() 叠一层半透明。",
    "css-container": "改成 @media 媒体查询，或者在 JS 里量宽度后加类名。",
    "css-aspect-ratio": "用 padding-top 百分比的老写法（`padding-bottom: 56.25%` 之类）。",
    "css-flex-gap": "flex 容器里的 gap 用 margin 替代，例如 `> * + * { margin-left: 8px }`。",
    "py-win8-dll": "换成 Win7 上就有的 DLL（user32 / kernel32 / gdi32），并用 try/except 兜住。",
    "py-win8-api": "改用 Windows 7 就存在的 API（例如 user32.SetProcessDPIAware）。",
    "py-winrt": "去掉 WinRT：Win7 上没有这套东西，改用 Win32 + WinForms（PowerShell 2.0）。",
    "banned-font": "字体栈里去掉它；图标一律内联 SVG，不要依赖字体图标。",
    "third-party-import": "用标准库改写；确属随包自带就加白名单 --allow-module 名字。"
                          "绝不允许 pip 装包。",
    "ps1-bom": "用能写 BOM 的工具另存为“UTF-8 带 BOM”；禁止用 PowerShell 的 Set-Content 改源码。",
    "ps1-encoding": "确认 BOM 之后是合法 UTF-8；已经读坏的要从原始字节重写，不要靠编辑器猜。",
    "cmd-ascii": "把 .cmd 里的中文挪走（提示语交给 python 打印），脚本正文只留 ASCII。",
    "cmd-crlf": "整个 .cmd 的换行改成 CRLF（只写 \\n，cmd.exe 会把它当普通字符）。",
    "blocked-glyph": "删掉该字符：图标用 webui/ui/icons.js 的内联 SVG，文字用中文或 ASCII。",
    "py-eval": "去掉 eval：需要表达式求值就用 plot.py 的手写解析器。",
    "py-exec": "去掉 exec：改成函数调用或查表分发。",
    "js-eval": "去掉 eval：改成显式分支或查表分发。",
    "js-exec": "去掉独立调用的 exec：正则对象的 `.exec()` 不算，独立 exec 一律不要。",
    "no-capability-probe": "在入口处加一次能力探测（canvas.getContext('webgl') / 2d）并存成变量，"
                           "后面按它选渲染路径。",
    "no-fallback-branch": "探测为假时走真正能看的第二条路（Canvas2D / SVG 直接画），"
                          "并在界面上给中文提示，别留白屏。",
    "missing-entry": "确认文件没被改名/删掉；门禁的 FALLBACK_TARGETS 也要同步改。",
    "glyph-ranges-unavailable": "确认 webui/test_webui.py 还在、还能被导入"
                                 "（TestUIFiles.BLOCKED_GLYPH_RANGES 是唯一来源）。",
    "gate-crash": "这是门禁自己的 bug：把上面那行异常连同命令一起报给维护者。",
}


# ---------------------------------------------------------------------------
# 上下文 / 主流程
# ---------------------------------------------------------------------------


class Context(object):
    def __init__(self, roots, files, allow_modules):
        self.roots = roots
        self.files = files
        self.allow_modules = allow_modules
        self.by_ext = {}
        self.broken = []
        self.glyph_ranges = None
        self.glyph_error = None
        self._cache = {}
        for path in files:
            self.by_ext.setdefault(os.path.splitext(path)[1].lower(), []).append(path)

    def source_of(self, path):
        if path in self._cache:
            return self._cache[path]
        try:
            with open(path, "rb") as handle:
                raw = handle.read()
        except OSError as exc:
            self.broken.append("%s 读不了：%s" % (rel_of(path), exc))
            self._cache[path] = None
            return None
        text, encoding, error = decode_bytes(raw)
        src = Source(path, rel_of(path), raw, text, encoding, error)
        if error:
            self.broken.append("%s：%s" % (src.rel, error))
        self._cache[path] = src
        return src

    def sources(self, exts):
        out = []
        for ext in exts:
            for path in self.by_ext.get(ext, ()):
                src = self.source_of(path)
                if src is not None:
                    out.append(src)
        return out


def run(roots, allow_modules, only):
    files, skipped, missing = collect_files(roots)
    ctx = Context(roots, files, allow_modules)
    ctx.glyph_ranges, ctx.glyph_error = blocked_glyph_ranges()
    rep = Report()
    results = []
    for key, number, title, func in CHECK_ORDER:
        if only and key not in only:
            continue
        try:
            func(ctx, rep)
        except Exception as exc:                      # noqa: BLE001 - 门禁自己崩了也要说清楚
            rep.findings.append(Finding(key, "error", "scripts/win7-gate.py", 0, 0, "gate-crash",
                                        "这一项自己崩了：%s: %s"
                                        % (exc.__class__.__name__, exc)))
        results.append((key, number, title))
    return ctx, rep, results, skipped, missing


def render_text(ctx, rep, results, skipped, missing, args, duration):
    out = []
    errors = rep.count(None, "error")
    warnings = rep.count(None, "warn")
    out.append("=" * 78)
    out.append(" Study7Agent Win7 兼容性快速门禁（开发期替代 Win7 虚拟机）")
    out.append(" 扫描根：%s" % "、".join(ctx.roots))
    kinds = []
    for ext, label in ((".js", "JS"), (".css", "CSS"), (".html", "HTML"), (".py", "PY"),
                       (".ps1", "PS1"), (".cmd", "CMD"), (".bat", "BAT"), (".cs", "CS")):
        count = len(ctx.by_ext.get(ext, ()))
        if count:
            kinds.append("%s %d" % (label, count))
    out.append(" 扫描文件：%d 个（%s）" % (len(ctx.files), "，".join(kinds) or "无"))
    if skipped:
        out.append(" 跳过：%s" % "、".join(sorted(skipped)))
    out.append(" 耗时：%.2f 秒" % duration)
    out.append("=" * 78)

    for key, number, title in results:
        items = rep.for_check(key)
        errs = [item for item in items if item.level == "error"]
        warns = [item for item in items if item.level == "warn"]
        if args.quiet and not errs:
            continue
        if errs:
            status = "失败 FAIL"
        elif warns:
            status = "通过 PASS（%d 条警告）" % len(warns)
        else:
            status = "通过 PASS"
        out.append("")
        out.append("[%s/6] %s …… %s" % (number, title, status))
        shown = 0
        for item in errs + ([] if args.quiet else warns):
            if shown >= args.max_items:
                break
            out.append("    [%s] %s  %s  (%s)"
                       % ("错误" if item.level == "error" else "警告",
                          item.where(), item.message, item.rule))
            fix = RULE_FIX.get(item.rule)
            if fix:
                out.append("           修复：%s" % fix)
            shown += 1
        hidden = len(errs) + (0 if args.quiet else len(warns)) - shown
        if hidden > 0:
            out.append("    …… 还有 %d 条没逐条打印（--max-items 可调，--json 里是全量）" % hidden)
        if not errs and not warns:
            out.append("    没有发现问题")

    if missing:
        out.append("")
        out.append(" 注意：这些扫描根不存在，已忽略：%s" % "、".join(missing))
    if ctx.broken:
        out.append("")
        out.append(" 以下文件读不进来，只跳过了内容检查（编码问题由第 5 项报错）：")
        for line in ctx.broken[:10]:
            out.append("    - %s" % line)

    if args.fix_hints:
        out.append("")
        out.append("-" * 78)
        out.append(" 修复指引（按规则汇总，只列本次真的命中的）")
        seen = []
        for item in rep.findings:
            if item.rule not in seen and item.rule in RULE_FIX:
                seen.append(item.rule)
        for rule in seen:
            out.append("  * [%s] %s" % (rule, RULE_FIX[rule]))

    out.append("")
    out.append("-" * 78)
    out.append(" 汇总：错误 %d 个，警告 %d 个 —— 结果：%s（退出码 %d）"
               % (errors, warnings, "失败 FAIL" if errors else "通过 PASS", 1 if errors else 0))
    out.append(" 本次覆盖：%s" % " / ".join("%s.%s" % (number, key) for key, number, _t in results))
    if rep.notes and not args.quiet:
        out.append(" 门禁自身的局限（不当成全覆盖）：")
        for note in rep.notes:
            out.append("    - %s" % note)
    out.append("=" * 78)
    return "\n".join(out)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Study7Agent Win7 兼容性快速门禁（纯标准库，随包 Python 3.8 运行）")
    parser.add_argument("--path", action="append", default=None, metavar="DIR",
                        help="只扫这个目录（可重复；默认扫仓库根，自动跳过 python/ 与 "
                             "study7agent-src/）")
    parser.add_argument("--only", action="append", default=None, metavar="CHECK",
                        help="只跑这一项：syntax / api / fonts / python / hardrules / fallback，"
                             "也可以写 1..6（可重复、可用逗号分隔）")
    parser.add_argument("--json", action="store_true", help="输出机器可读的 JSON")
    parser.add_argument("--quiet", action="store_true", help="只输出失败项")
    parser.add_argument("--fix-hints", action="store_true", help="追加按规则汇总的修复指引")
    parser.add_argument("--max-items", type=int, default=25, metavar="N",
                        help="每项最多逐条打印多少条（默认 25；--json 里始终是全量）")
    parser.add_argument("--allow-module", action="append", default=[], metavar="NAME",
                        help="import 白名单（可重复）；本项目自己的模块已自动加白")
    args = parser.parse_args(argv)

    try:
        make_console_safe()
    except Exception:                                 # noqa: BLE001
        pass

    keys = [key for key, _n, _t, _f in CHECK_ORDER]
    alias = {number: key for key, number, _t, _f in CHECK_ORDER}
    only = None
    if args.only:
        only = set()
        for item in args.only:
            for piece in re.split(r"[,\s]+", item.strip()):
                if not piece:
                    continue
                if piece in keys:
                    only.add(piece)
                elif piece in alias:
                    only.add(alias[piece])
                else:
                    sys.stderr.write("未知的检查项：%s（可选：%s，或 1..6）\n"
                                     % (piece, " / ".join(keys)))
                    return 2

    roots = [os.path.abspath(item) for item in args.path] if args.path else [ROOT]
    started = time.time()
    ctx, rep, results, skipped, missing = run(roots, args.allow_module, only)
    duration = time.time() - started

    errors = rep.count(None, "error")
    warnings = rep.count(None, "warn")
    if args.json:
        payload = {
            "ok": errors == 0,
            "root": ROOT,
            "roots": roots,
            "duration_ms": int(duration * 1000),
            "scanned": {"total": len(ctx.files),
                        "by_ext": {ext: len(paths) for ext, paths in sorted(ctx.by_ext.items())}},
            "skipped": sorted(skipped),
            "missing_roots": missing,
            "summary": {"errors": errors, "warnings": warnings,
                        "exit_code": 1 if errors else 0},
            "checks": [{"no": number, "key": key, "name": title,
                        "status": ("fail" if rep.count(key, "error")
                                   else ("warn" if rep.for_check(key) else "pass")),
                        "errors": rep.count(key, "error"),
                        "warnings": rep.count(key, "warn"),
                        "findings": [item.as_dict() for item in rep.for_check(key)]}
                       for key, number, title in results],
            "notes": rep.notes,
            "broken_files": ctx.broken,
        }
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    else:
        sys.stdout.write(render_text(ctx, rep, results, skipped, missing, args, duration) + "\n")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
