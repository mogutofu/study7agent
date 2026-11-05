# -*- coding: utf-8 -*-
"""Study7Agent WebUI 服务端。

一个只依赖 Python 3.8 标准库的本地 Web 服务：
  - 托管 webui/ui 下的单页应用（无 CDN、无 npm 构建，离线内网可用）
  - 提供会话、配置、工具、快照等 REST 接口
  - /api/chat 用 SSE 流式回传：思考过程、正文、工具调用、用量与缓存命中
  - 高风险命令通过 /api/confirm 等界面确认后再执行

启动：
    python\\python.exe webui\\server.py
"""

import argparse
import base64
import calendar
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote

import core
import apps as apps_module
import desktop as desktop_module
import calc as calc_module
import chart as chart_module
import chem as chem_module
import circuit as circuit_module
import classroom as classroom_module
import instruments as instruments_module
import molecule as molecule_module
import physics as physics_module
import practice as practice_module
import sim2d as sim2d_module
import biology as biology_module
import plot as plot_module
import units as units_module
import tools as tools_module
import memo as memo_module
import lunar as lunar_module
import holiday as holiday_module
import plane_hub

HERE = os.path.dirname(os.path.abspath(__file__))
UI_DIR = os.path.join(HERE, "ui")


def log_line(message):
    """往启动日志里写一行。

    老师的 exe 没有控制台，只能靠 `logs\exe.log` 判断"到底有没有走到这一步"。
    服务端是被启动器用管道接着跑的：启动器把 stdout/stderr 原样追加进那个日志文件，
    所以这里写 stderr 就等于写日志（前面加 [配置] 前缀，和启动器的行区分开）。
    """
    try:
        sys.stderr.write("[配置] %s\n" % message)
        sys.stderr.flush()
    except (IOError, ValueError):
        pass



def ui_stamp(directory=None):
    """界面文件的指纹（`webui\\ui` 下所有文件的"最新改动时间 + 总字节数"）。

    为什么需要：老师的页面是**长期开着**的标签页，改了 `.js/.css` 不刷新就一直跑旧代码，
    于是反复出现"你说改好了、我这儿没变"（见 roadmap 1.1.0 的 M5 段）。网页拿这个指纹
    在心跳里对一下，不一样就在标题栏提示"点我刷新"——不自动刷新，免得把正在打的字弄丢。
    """
    newest = 0
    total = 0
    try:
        for name in sorted(os.listdir(directory or UI_DIR)):
            path = os.path.join(directory or UI_DIR, name)
            if os.path.isfile(path):
                newest = max(newest, int(os.path.getmtime(path)))
                total += os.path.getsize(path)
    except OSError:
        return ""
    return "%d-%d" % (newest, total)


CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
}
CONFIRM_TIMEOUT = 900
# SSE 心跳间隔：长工具调用期间定期写一行注释保活（社区经验是 ≤30 秒，这里取 15 秒）
SSE_HEARTBEAT_SECONDS = 15.0
# 已经点了"停止"的运行，最多再挡这么久；超过就把登记摘掉，让老师能继续提问。
# 取 20 秒：正常的取消（读模型流、轮次之间、长命令/PowerShell）都在 1 秒内生效，
# 这里只是"万一卡在不听话的子进程里"的兜底，不能再让老师干等。
# 老师点了停止之后，这次运行最多再挡住同一个会话这么久。
# 原来是 20 秒：子进程一时收不掉的话，老师想马上改口重问，会被自己的停止按钮
# 锁在门外 20 秒——体感就是"停了半天没反应"。5 秒足够正常收尾。
STOP_GRACE_SECONDS = 5.0

# 网页每 2 秒给服务端发一次心跳（/api/float/ping）；超过这么久没动静就当作"网页关了"，
# 悬浮工具栏据此把整个程序收掉。取 10 秒：刷新页面、切标签页都不会误判。
# ★ 这个值**不再是"多久没心跳就算网页关了"**：360 浏览器会冻结后台标签页的定时器，
#   老师切去改个作业再回来，心跳就断过十几秒。以前一断就把整个程序杀掉（只留一个空网页），
#   是老师反馈最严重的一条。现在它只用来给工具栏显示"网页最近活过吗"，
#   真正决定"网页关了"的只有网页自己发来的 /api/float/bye（关标签页时 beforeunload 里发）。
FLOAT_PAGE_TIMEOUT = 120.0
# 网页关掉时它自己会喊一声（beforeunload），把倒计时缩短到这么久，老师不用干等
FLOAT_BYE_GRACE = 4.0
# 网页最多积压多少条待执行命令：网页一直没来（比如浏览器崩了）也不让它无限涨
FLOAT_QUEUE_LIMIT = 32
# 课堂时间点清单（工作区里）：记录的时间 + 当屏截图，课后按时间线回看
MARKER_FILE = "课堂时间点.json"
# 课堂资料：工具栏和网页上那几个"存下来的东西"分别落在工作区的哪个子目录
CLASSROOM_FOLDERS = (("board", "板书", "板书"), ("marker", "课堂时间点", "课堂时间点"),
                     ("shot", "截图", "截图"))

# 文件选择器过滤规则
BROWSE_FILTERS = {
    "image": tuple(core.IMAGE_MIME.keys()),
    "doc": (".docx", ".xlsx", ".pptx", ".pdf", ".txt", ".md", ".csv", ".json", ".log"),
    "media": (".mp4", ".mp3", ".wav", ".avi", ".mkv", ".mov"),
}

# 不允许通过界面直接运行的文件类型（避免误点可执行文件）
BLOCKED_OPEN = (".exe", ".bat", ".cmd", ".com", ".scr", ".msi", ".ps1", ".vbs", ".vbe",
                ".js", ".jse", ".wsf", ".wsh", ".lnk", ".reg", ".dll", ".sys", ".cpl")


# ---------------------------------------------------------------- 平面（plane）
# 「平面」的对象模型在 webui\plane.py 里（并行开发，可能还没落地）。这里**延迟导入**：
# 文件不存在、或者只写了一半有语法错，都只影响 /api/plane/* 这几条路由（回一句中文说明），
# 服务端其余部分照常工作——不能因为一个还没写完的模块让老师整站打不开。
_PLANE_LOCK = threading.Lock()
_PLANE_CACHE = {"target": None}

# 同一个能力可能有几种命名，按顺序试（接口冻结的是**数据形状**，不是 Python 方法名）。
# 注意顺序：state 必须优先 snapshot()——它返回的才是冻结的 PlaneState
# （version / view / objects / order）；query_state() 是"精简查询"，objects 是个列表，
# 形状不一样，前端画不了，所以这里**故意不列它**。
_PLANE_METHODS = {
    "state": ("snapshot", "get_state", "to_dict", "state"),
    "apply": ("create_many", "apply", "apply_ops", "applyOps", "run_ops"),
    "undo": ("undo",),
    "redo": ("redo",),
    "svg": ("export_svg", "to_svg", "render_svg", "svg"),
    "window": ("set_window", "set_sampling_window", "window"),
}


def _plane_target():
    """拿到真正干活的对象：共享的那块 Plane（`plane_hub` 保证和 Agent 工具是同一个实例）。

    **必须走 plane_hub**：以前这里自己 `Plane()`，于是网页上老师拖出来的图和 AI 用
    `plane` 工具建的图是**两张图**（工具建的点在网页上永远不出现）。plane_hub 负责
    "只构造一个"，这里只按 `_PLANE_METHODS` 的几种命名挑出能用的方法。
    """
    with _PLANE_LOCK:
        cached = _PLANE_CACHE["target"]
    # 缓存要能失效：`plane import` 会整块换掉共享实例，拿着旧实例的缓存去调就是
    # "网页画的还是旧图"。所以先跟 plane_hub 现在的那一个比一比。
    if cached is not None and cached is plane_hub.peek():
        return cached
    try:
        candidate = plane_hub.get()
    except Exception as exc:  # noqa: BLE001 - 文件还没写 / 写了一半，都走这里
        # 文案保持原样：前端和老师看到的都是这句「平面模块尚未就绪：…」
        raise RuntimeError("平面模块 webui\\plane.py 还不能用：%s" % exc)
    target = candidate
    if not any(callable(getattr(candidate, method, None))
               for method in _PLANE_METHODS["state"]):
        # 退化路径：实例上一个方法都对不上时，试模块级函数（老实现可能直接放在模块上）
        import plane as plane_module
        target = plane_module
    with _PLANE_LOCK:
        _PLANE_CACHE["target"] = target
    return target


def _plane_invoke(kind, *args, **kwargs):
    """按名字顺序试出一个能用的方法，返回 (结果, 问题说明)。只缓存成功的导入。"""
    try:
        target = _plane_target()
    except RuntimeError as exc:
        return None, str(exc)
    for name in _PLANE_METHODS[kind]:
        method = getattr(target, name, None)
        if callable(method):
            return method(*args, **kwargs), ""
    return None, "平面模块没有提供 %s 接口（找过：%s）" % (kind, "、".join(_PLANE_METHODS[kind]))


class PlaneBus(object):
    """平面的多窗口同步：一个窗口改了图，别的开着的窗口跟着刷新。

    只广播"有新版本了"这一条通知，不搬运状态本身——一次拖动一秒能改几十次，
    每来一次就把整份状态复制给每个窗口太浪费；收到通知的窗口自己去 /api/plane/state 取。
    """

    MAX_SUBSCRIBERS = 8

    def __init__(self):
        self.lock = threading.Lock()
        self.subs = []

    def subscribe(self, write):
        with self.lock:
            if len(self.subs) >= self.MAX_SUBSCRIBERS:
                return False
            self.subs.append(write)
            return True

    def unsubscribe(self, write):
        with self.lock:
            if write in self.subs:
                self.subs.remove(write)

    def publish(self, payload):
        with self.lock:
            subs = list(self.subs)
        for write in subs:
            try:
                alive = write(payload)
            except Exception:  # noqa: BLE001 - 一个窗口写坏了不能拖累别的
                alive = False
            if not alive:
                self.unsubscribe(write)


def _is_drive_root(path):
    """'C:' 或 'C:\\' 这种盘符根：它没有上一级。"""
    text = str(path or "")
    return len(text) == 2 and text[1] == ":"


def _crumbs(path):
    """把路径拆成可点的层级：C:\\ > 用户 > 老师 > 桌面 > 课件（UNC 保持 \\\\server\\share 开头）。"""
    text = str(path or "").rstrip("\\/")
    if not text:
        return []
    if text.startswith("\\\\"):                      # \\server\share\dir
        parts = [part for part in text.split("\\") if part]
        if len(parts) < 2:
            return []
        head = "\\\\" + parts[0] + "\\" + parts[1]
        rest = parts[2:]
        crumbs = [{"name": head, "path": head}]
        current = head
        for name in rest:
            current = current + "\\" + name
            crumbs.append({"name": name, "path": current})
        return crumbs
    drive, tail = os.path.splitdrive(text)
    crumbs = []
    current = drive + "\\"
    crumbs.append({"name": current, "path": current})
    for name in [part for part in tail.split("\\") if part]:
        current = os.path.join(current, name)
        crumbs.append({"name": name, "path": current})
    return crumbs


def _shortcuts():
    """给老师的常用位置：桌面、文档、下载、图片、工作区。"""
    home = os.path.expanduser("~")
    candidates = [
        ("桌面", os.path.join(home, "Desktop")),
        ("文档", os.path.join(home, "Documents")),
        ("下载", os.path.join(home, "Downloads")),
        ("图片", os.path.join(home, "Pictures")),
    ]
    out = []
    for name, path in candidates:
        if os.path.isdir(path):
            out.append({"name": name, "path": path})
    return out


def _drives():
    out = []
    for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
        root = letter + ":\\"
        if os.path.isdir(root):
            out.append(root)
    return out


class App(object):
    """进程级共享状态：配置、会话、工具、运行中的请求。"""

    def __init__(self, base_dir, data_dir=None, exe_path=None):
        self.base_dir = base_dir
        # 本程序 exe 的完整路径（启动器传进来）。单文件发布版的文件名带版本号，
        # 开机自启必须指向它，不能死找 Study7Agent.exe。
        self.exe_path = exe_path or ""
        self.settings = core.Settings(base_dir, exe_path=self.exe_path)
        configured = data_dir or self.settings.webui.get("data_dir") or ""
        self.data_dir = os.path.abspath(configured) if configured else os.path.join(HERE, "data")
        if not os.path.isdir(self.data_dir):
            os.makedirs(self.data_dir)
        # 上传文件放在工作区内，模型才能用 read_file 继续读取（沙箱规则同样适用）；
        # 目录在首次上传时才创建，避免污染用户工作区
        self.upload_dir = os.path.join(self.settings.workspace_root(), ".webui-uploads")
        # 工作区在启动时就准备好，模型与文件选择器都能直接用
        try:
            root = self.settings.workspace_root()
            if not os.path.isdir(root):
                os.makedirs(root)
        except OSError:
            pass
        self.store = core.SessionStore(self.data_dir)
        self.runs = {}
        self.runs_lock = threading.Lock()
        self.model_cache = {"at": 0, "ids": []}
        # 悬浮工具栏（scripts\study7agent-float.ps1）与网页之间的信箱：
        # 工具栏发命令 → 这里排队 → 网页下一次心跳时取走并执行。
        # 网页反向用心跳报活，工具栏靠它判断"网页关了没有"。
        # 网页说过"再见"没有。**只有它作数**：360 会冻结后台标签页的定时器，
        # 拿"多久没心跳"当关闭依据会把老师正在用的程序杀掉（真实事故）。
        self.float_page_gone = False
        self.float_lock = threading.Lock()
        self.float_page_at = 0.0
        self.float_page_ever = False
        self.float_session = ""
        self.float_queue = []
        # 「平面」的变更通知（多窗口同步）：见 PlaneBus
        self.plane_bus = PlaneBus()

    # -- 运行管理 ---------------------------------------------------------
    def new_run(self, run_id, cancel, session_id=""):
        with self.runs_lock:
            self.runs[run_id] = {"cancel": cancel, "pending": {}, "lock": threading.Lock(),
                                 "session": session_id or "", "started": time.time(),
                                 "stopping_at": 0.0}
        return self.runs[run_id]

    def stop_run(self, run_id, force=False):
        """"老师点停止"：置位取消事件；force=True 时直接把这次运行从登记表里摘掉。

        为什么要 force：取消是"协作式"的——正在跑的长工具只能等它自己看到标志。
        如果它卡在一个不听话的子进程里，老师不该被永远挡在"对话进行中"外面。
        force 之后旧线程即使还在跑，它的写盘也被 AgentRun 挡住了（见 core._save）。
        """
        run = self.get_run(run_id)
        if not run:
            return False, "运行已结束"
        run["cancel"].set()
        if not run.get("stopping_at"):
            run["stopping_at"] = time.time()
        with run["lock"]:
            for waiter in run["pending"].values():
                waiter["approved"] = False
                waiter["event"].set()
        if force:
            self.end_run(run_id)
        return True, ""

    def session_busy(self, session_id, grace=0.0):
        """同一个会话是否已经有运行在跑：两个标签页同时提问会互相覆盖历史（后写的赢）。

        grace>0 时：如果那次运行早就被停止、却因为卡在某个工具里迟迟没退出，
        超过 grace 秒就不再挡住新的提问（老师不该被自己的"停止"按钮锁在外面）。
        """
        if not session_id:
            return False
        with self.runs_lock:
            for entry in self.runs.values():
                if entry.get("session") != session_id:
                    continue
                stopping_at = entry.get("stopping_at") or 0.0
                if grace and stopping_at and time.time() - stopping_at > grace:
                    continue
                return True
        return False

    def stale_run_ids(self, session_id, grace=0.0):
        """被停止太久、已经不该再挡路的运行 id（调用方负责 end_run）。"""
        if not session_id or not grace:
            return []
        stale = []
        with self.runs_lock:
            for run_id, entry in self.runs.items():
                if entry.get("session") != session_id:
                    continue
                stopping_at = entry.get("stopping_at") or 0.0
                if stopping_at and time.time() - stopping_at > grace:
                    stale.append(run_id)
        return stale

    def get_run(self, run_id):
        with self.runs_lock:
            return self.runs.get(run_id)

    def end_run(self, run_id):
        with self.runs_lock:
            entry = self.runs.pop(run_id, None)
        if entry:
            for waiter in entry["pending"].values():
                waiter["approved"] = False
                waiter["event"].set()

    def resolve_confirmation(self, run_id, call_id, approved):
        run = self.get_run(run_id)
        if not run:
            return False, "运行已结束"
        with run["lock"]:
            waiter = run["pending"].get(call_id)
        if not waiter:
            return False, "没有等待确认的操作"
        waiter["approved"] = bool(approved)
        waiter["event"].set()
        return True, ""

    # -- 便捷构造 ---------------------------------------------------------
    def make_client(self, model=None):
        resolved = self.settings.resolve_model(model)
        return core.client_for(
            resolved,
            timeout=int(self.settings.agent.get("timeout_seconds") or 300),
            ca_file=self.settings.agent.get("ca_file") or "",
            insecure=bool(self.settings.agent.get("insecure_skip_verify")),
        ), resolved

    def make_tools(self):
        return tools_module.Tools(self.settings, self.data_dir)

    def live_models(self, force=False):
        if not force and self.model_cache["ids"] and time.time() - self.model_cache["at"] < 300:
            return self.model_cache["ids"], ""
        try:
            client, _resolved = self.make_client()
            ids = client.list_models()
            self.model_cache = {"at": time.time(), "ids": ids}
            return ids, ""
        except core.ChatError as exc:
            return self.model_cache["ids"], exc.message

    # -- 悬浮工具栏的信箱 -------------------------------------------------
    def float_ping(self, session_id=""):
        """网页报活：记下时间和它当前打开的会话，顺手取走排队中的命令。"""
        with self.float_lock:
            self.float_page_at = time.time()
            self.float_page_ever = True
            self.float_page_gone = False          # 心跳回来了：撤销上一次"再见"
            if session_id:
                self.float_session = session_id
            commands, self.float_queue = self.float_queue, []
        return commands

    def float_enqueue(self, command):
        """给网页排一条命令（它下一次心跳时执行）。网页不来也不能让队列无限涨。"""
        with self.float_lock:
            self.float_queue.append(command)
            if len(self.float_queue) > FLOAT_QUEUE_LIMIT:
                del self.float_queue[:-FLOAT_QUEUE_LIMIT]

    def float_state(self):
        with self.float_lock:
            seen = self.float_page_at
            ever = self.float_page_ever
            session = self.float_session
        age = (time.time() - seen) if seen else -1.0
        with self.float_lock:
            gone = self.float_page_gone
        # page = "网页还在"：见过它、而且它没说再见。**不看超时**——
        # 超时只用来给工具栏显示"最近一次心跳是多久以前"。
        return {"page": bool(ever) and not gone,
                "age": int(age), "ever": ever, "session": session}

    def float_bye(self):
        """网页要关了（beforeunload 里主动发）：这才是"网页关了"的唯一依据。

        老师按 F5 也没关系——新页面 2 秒内就会重新报活，ping 会把 gone 清掉。
        """
        with self.float_lock:
            if self.float_page_ever:
                self.float_page_gone = True
                self.float_page_at = time.time()
        return True

    def request_exit(self, delay=1.0):
        """老师点了悬浮窗的「退出」：稍等一下让 HTTP 回应写完，然后结束进程。"""
        threading.Thread(target=_exit_soon, args=(delay,), daemon=True).start()


class Handler(BaseHTTPRequestHandler):
    server_version = "study7agent-webui/" + core.VERSION
    protocol_version = "HTTP/1.0"

    # -- 基础工具 ---------------------------------------------------------
    @property
    def app(self):
        return self.server.app

    def log_message(self, fmt, *args):
        if getattr(self.server, "verbose", False):
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, message, status=400):
        self._json({"ok": False, "error": message}, status)

    def _text(self, payload, status=200):
        """悬浮工具栏专用：PowerShell 2.0 没有 ConvertFrom-Json，读不了 JSON。

        沿用 desktop.py 已经用了很久的 ``OK|...`` / ``ERR|...`` 单行约定。
        """
        body = payload.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError:
            raise ValueError("请求体不是合法 JSON")

    def _authorized(self):
        # 必须看"实际绑定的地址"，不能看配置文件里的 host：
        # `start-webui.cmd --host 0.0.0.0` 会覆盖配置，只看配置就会以为"还是本机"，
        # 于是监听在局域网上却谁都不用带令牌——同网段任何人都能执行本机命令。
        token = self.app.settings.webui.get("access_token") or ""
        host = getattr(self.server, "bind_host", "") or \
            self.app.settings.webui.get("host") or "127.0.0.1"
        loopback = host in ("127.0.0.1", "localhost", "::1")
        if not loopback:
            # 监听局域网而没设令牌 = 全开。宁可拒绝服务，也不能默默放行。
            if not token:
                self._error("服务正在监听局域网但没有设置访问令牌，已拒绝请求。"
                            "请在 config\\webui.json 里设置 access_token 后重启。", 401)
                return False
            provided = self.headers.get("X-Auth-Token") or ""
            if not provided:
                query = parse_qs(urlparse(self.path).query)
                provided = (query.get("token") or [""])[0]
            if provided != token:
                self._error("访问令牌无效", 401)
                return False
        origin = self.headers.get("Origin")
        if origin:
            origin_host = urlparse(origin).hostname or ""
            if origin_host not in ("127.0.0.1", "localhost", "::1"):
                self._error("拒绝来自 %s 的跨站请求" % origin_host, 403)
                return False
        return True

    # -- 路由 -------------------------------------------------------------
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path.startswith("/api/"):
            if not self._authorized():
                return
            try:
                self._api_get(path, parse_qs(parsed.query))
            except Exception as exc:  # noqa: BLE001 - 兜底，避免线程静默退出
                self._error("服务端异常：%s" % exc, 500)
            return
        self._serve_static(path)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if not path.startswith("/api/"):
            self._error("未知接口", 404)
            return
        if not self._authorized():
            return
        try:
            self._api_post(path)
        except ValueError as exc:
            self._error(str(exc), 400)
        except Exception as exc:  # noqa: BLE001
            self._error("服务端异常：%s" % exc, 500)

    # -- 静态资源 ---------------------------------------------------------
    def _serve_static(self, path):
        if path in ("/", ""):
            path = "/index.html"
        relative = unquote(path).lstrip("/")
        target = os.path.normpath(os.path.join(UI_DIR, relative))
        if not target.startswith(UI_DIR) or not os.path.isfile(target):
            self._error("未找到 %s" % path, 404)
            return
        extension = os.path.splitext(target)[1].lower()
        with open(target, "rb") as handle:
            body = handle.read()
        self.send_response(200)
        self.send_header("Content-Type", CONTENT_TYPES.get(extension, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    # -- GET 接口 ---------------------------------------------------------
    def _api_get(self, path, query):
        app = self.app
        if path == "/api/state":
            session = None
            session_id = (query.get("session") or [""])[0]
            if session_id:
                session = app.store.load_or_none(session_id)
            self._json({
                "ok": True,
                "uiStamp": ui_stamp(),
                "config": app.settings.public_config(),
                "skills": [skill.to_dict() for skill in
                           core.load_skills(app.base_dir,
                                            skip=app.settings.deleted_skills())],
                "sessions": app.store.list_summaries(),
                "session": session,
                "snapshots": app.make_tools().snapshots(30),
            })
            return
        if path == "/api/session":
            session_id = (query.get("id") or [""])[0]
            session = app.store.load_or_none(session_id)
            if session is None:
                self._error("会话不存在", 404)
                return
            self._json({"ok": True, "session": session})
            return
        if path == "/api/session/export":
            session_id = (query.get("id") or [""])[0]
            session = app.store.load_or_none(session_id)
            if session is None:
                self._error("会话不存在", 404)
                return
            markdown = core.session_to_markdown(session)
            self.send_response(200)
            self.send_header("Content-Type", "text/markdown; charset=utf-8")
            self.send_header("Content-Disposition",
                             "attachment; filename=\"session-%s.md\"" % session_id)
            body = markdown.encode("utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/models":
            ids, error = app.live_models(force=(query.get("refresh") or [""])[0] == "1")
            self._json({"ok": not error, "models": ids, "error": error,
                        "endpoint": app.settings.resolve_model()["endpoint"]})
            return
        if path == "/api/balance":
            try:
                client, _resolved = app.make_client()
                self._json({"ok": True, "balance": client.balance()})
            except core.ChatError as exc:
                self._json({"ok": False, "error": exc.message})
            return
        if path == "/api/file":
            self._file_preview((query.get("path") or [""])[0])
            return
        if path == "/api/image":
            self._image_serve((query.get("path") or [""])[0])
            return
        if path == "/api/image_info":
            self._image_info((query.get("path") or [""])[0])
            return
        if path == "/api/browse":
            self._browse(query)
            return
        if path == "/api/skills":
            self._json({"ok": True, "skills": self._skill_list()})
            return
        if path == "/api/float/config":
            # 悬浮工具栏的配置存在 config\float.json，网页设置里能改（GET 读、POST 写）
            self._json({"ok": True, "config": apps_module.config_view(app.base_dir)})
            return
        if path == "/api/classroom/materials":
            # 板书 / 课堂时间点 / 截图 三个目录 + 各自有多少文件：网页上那个「课堂资料」
            # 按钮靠它把目录列出来，点一下就用 /api/open 在资源管理器里打开。
            root = app.settings.workspace_root()
            folders = []
            for key, label, folder in CLASSROOM_FOLDERS:
                path = os.path.join(root, folder)
                try:
                    count = len([name for name in os.listdir(path)
                                 if not name.startswith(".")]) if os.path.isdir(path) else 0
                except OSError:
                    count = 0
                folders.append({"key": key, "label": label, "path": path,
                                "count": count, "exists": os.path.isdir(path)})
            self._json({"ok": True, "workspace": root, "folders": folders,
                        "markers": os.path.join(root, MARKER_FILE),
                        "hasMarkers": os.path.isfile(os.path.join(root, MARKER_FILE))})
            return
        if path == "/api/float/state":
            state = app.float_state()
            self._text("OK|page=%d|age=%d|ever=%d"
                       % (1 if state["page"] else 0, state["age"], 1 if state["ever"] else 0))
            return
        if path == "/api/memo":
            # 备忘录存在 config\memo.json（跨会话），前端拿到整份就能直接重画
            self._json({"ok": True, "items": memo_module.load(app.base_dir)})
            return
        if path == "/api/calendar":
            self._calendar(query)
            return
        if path == "/api/plane/state":
            state = self._plane_state_or_error()
            if state is not None:
                self._json({"ok": True, "state": state})
            return
        if path == "/api/plane/export":
            self._plane_export(query)
            return
        if path == "/api/plane/events":
            self._plane_events()
            return
        if path == "/api/autostart":
            # 开机自启体检（只读：不改注册表、不写启动文件夹）
            self._json({"ok": True, "report": core.autostart_diagnose(
                app.base_dir, exe_path=app.exe_path)})
            return
        self._error("未知接口 %s" % path, 404)

    # -- 文件选择器（老师不需要手打路径）----------------------------------
    def _browse(self, query):
        app = self.app
        workspace = app.settings.workspace_root()
        raw = (query.get("path") or [""])[0].strip()
        kind = (query.get("filter") or ["all"])[0]

        if not raw:
            self._json({"ok": True, "path": "", "parent": "", "entries": [], "total": 0,
                        "filtered": 0, "crumbs": [],
                        "drives": _drives(),
                        "shortcuts": _shortcuts() + [{"name": "工作区", "path": workspace}],
                        "workspace": workspace})
            return

        target = os.path.abspath(os.path.expandvars(raw))
        if not os.path.isdir(target):
            self._error("目录不存在或无法访问：%s" % target, 400)
            return
        allowed = BROWSE_FILTERS.get(kind)
        entries = []
        total = 0
        try:
            names = sorted(os.listdir(target), key=lambda value: value.lower())
        except OSError as exc:
            self._error("无法读取目录：%s" % exc, 400)
            return
        for name in names:
            if name.startswith("."):
                continue
            full = os.path.join(target, name)
            try:
                is_dir = os.path.isdir(full)
                stat = os.stat(full)
            except OSError:
                continue
            total += 1
            extension = os.path.splitext(name)[1].lower()
            if not is_dir and allowed and extension not in allowed:
                continue
            item = {
                "name": name,
                "path": full,
                "dir": is_dir,
                "size": 0 if is_dir else stat.st_size,
                "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(stat.st_mtime)),
                "ext": extension,
            }
            if not is_dir and kind == "image" and stat.st_size <= 262144:
                # 只给小图缩略图：整张原图塞进列表，几十张照片就能把学校机器的内存吃满。
                # 真正的地址由前端用带令牌的 imageUrl() 拼（局域网模式下 <img> 也要带令牌）
                item["thumb"] = True
            entries.append(item)
        entries.sort(key=lambda item: (not item["dir"], item["name"].lower()))
        truncated = len(entries) > 2000
        # 上一级：注意 Windows 上 dirname("C:") 得到的是 "C:"（表示“C 盘的当前目录”），
        # 直接用它会在盘符根上跳到莫名其妙的目录，所以盘符根一律没有上一级
        parent = os.path.dirname(target.rstrip("\\/")) or ""
        if _is_drive_root(parent) or os.path.normcase(parent) == os.path.normcase(target):
            parent = ""
        self._json({"ok": True, "path": target, "parent": parent,
                    "entries": entries[:2000], "truncated": truncated,
                    "total": total, "filtered": max(0, total - len(entries)),
                    "crumbs": _crumbs(target),
                    "drives": _drives(),
                    "shortcuts": _shortcuts() + [{"name": "工作区", "path": workspace}],
                    "workspace": workspace})

    # -- 技能管理 ---------------------------------------------------------
    def _skill_list(self):
        out = []
        for skill in core.load_skills(self.app.base_dir,
                                      skip=self.app.settings.deleted_skills()):
            try:
                with open(skill.path, "r", encoding="utf-8-sig", errors="replace") as handle:
                    content = handle.read()
            except OSError:
                content = ""
            out.append({"name": skill.name, "description": skill.description,
                        "path": skill.path, "content": content,
                        "always": bool(skill.always),
                        "editable": os.path.normcase(skill.path).startswith(
                            os.path.normcase(os.path.join(self.app.base_dir, "skills")))})
        return out

    def _image_info(self, raw_path):
        """校验图片路径并回传尺寸，供界面显示缩略图信息。"""
        try:
            handler = self.app.make_tools()
            target = handler.resolve(raw_path, must_exist=True)
        except (tools_module.ToolError, OSError) as exc:
            self._error("无法读取图片：%s" % exc, 400)
            return
        mime = core.image_mime(target)
        if not mime:
            self._error("不是支持的图片格式（png/jpg/jpeg/gif/webp/bmp）")
            return
        size = os.path.getsize(target)
        if size > core.MAX_IMAGE_BYTES:
            self._error("图片过大（%.1f MB），请压缩到 %d MB 以内"
                        % (size / 1048576.0, core.MAX_IMAGE_BYTES // 1048576))
            return
        try:
            with open(target, "rb") as handle:
                head = handle.read(262144)
        except OSError as exc:
            self._error("无法读取图片：%s" % exc)
            return
        width, height = core.image_size(head)
        self._json({"ok": True, "path": target, "name": os.path.basename(target),
                    "mime": mime, "bytes": size, "width": width, "height": height})

    def _image_serve(self, raw_path):
        """把本机图片发给浏览器做缩略图（仍然受工作区沙箱约束）。"""
        try:
            handler = self.app.make_tools()
            target = handler.resolve(raw_path, must_exist=True)
        except (tools_module.ToolError, OSError) as exc:
            self._error("无法读取图片：%s" % exc, 400)
            return
        mime = core.image_mime(target)
        if not mime:
            self._error("不是支持的图片格式", 400)
            return
        try:
            if os.path.getsize(target) > core.MAX_IMAGE_BYTES:
                self._error("图片过大", 400)
                return
            with open(target, "rb") as handle:
                body = handle.read()
        except OSError as exc:
            self._error("无法读取图片：%s" % exc, 400)
            return
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _reject_if_busy(self, session_id, message=""):
        """正在生成中就别让界面改会话。

        AgentRun 手里攥着一份会话副本，运行结束时整份写回；这期间改的东西会被它冲掉，
        老师看到的就是"刚加进去的文件不翼而飞"。宁可明确让他等一会儿，也不能悄悄丢数据。
        """
        if self.app.session_busy(session_id, grace=STOP_GRACE_SECONDS):
            self._error(message or "这个对话正在生成中，等它结束再改（现在改会被覆盖）。", 409)
            return True
        return False

    def _file_preview(self, raw_path):
        try:
            handler = self.app.make_tools()
            target = handler.resolve(raw_path, must_exist=True)
            with open(target, "rb") as handle:
                data = handle.read(200000)
            text, encoding = tools_module.decode_bytes(data)
            self._json({"ok": True, "path": target, "text": text, "encoding": encoding,
                        "bytes": os.path.getsize(target)})
        except (tools_module.ToolError, OSError) as exc:
            self._error("无法读取：%s" % exc, 400)

    # -- POST 接口 --------------------------------------------------------
    def _api_post(self, path):
        app = self.app
        if path == "/api/chat":
            self._chat()
            return
        body = self._read_json()
        if path == "/api/stop":
            ok, error = app.stop_run(body.get("runId") or "", force=bool(body.get("force")))
            self._json({"ok": ok, "error": error or None, "forced": bool(body.get("force"))})
            return
        if path == "/api/confirm":
            ok, error = app.resolve_confirmation(body.get("runId") or "", body.get("id") or "",
                                                 body.get("approved"))
            self._json({"ok": ok, "error": error})
            return
        if path == "/api/session/new":
            session = app.store.create(title=body.get("title") or "新会话",
                                       model=body.get("model") or "",
                                       workspace=core.canonical_path(body.get("workspace"))
                                       or app.settings.workspace_root(),
                                       effort=body.get("effort")
                                       or app.settings.webui.get("reasoning_effort") or "")
            self._json({"ok": True, "session": session})
            return
        if path == "/api/session/rename":
            session_id = body.get("id") or ""
            session = app.store.load_or_none(session_id)
            if session is None:
                self._error("会话不存在", 404)
                return
            session["title"] = (body.get("title") or "").strip() or session.get("title")
            self._json({"ok": True, "session": app.store.save(session)})
            return
        if path == "/api/session/delete":
            ok = app.store.delete(body.get("id") or "")
            self._json({"ok": ok})
            return
        if path == "/api/session/model":
            session = app.store.load_or_none(body.get("id") or "")
            if session is None:
                self._error("会话不存在", 404)
                return
            session["model"] = (body.get("model") or "").strip()
            self._json({"ok": True, "session": app.store.save(session)})
            return
        if path == "/api/session/effort":
            session = app.store.load_or_none(body.get("id") or "")
            if session is None:
                self._error("会话不存在", 404)
                return
            level = (body.get("effort") or "").strip().lower()
            if level and level not in core.REASONING_EFFORTS:
                self._error("不支持的推理等级：%s" % level)
                return
            session["effort"] = level
            self._json({"ok": True, "session": app.store.save(session)})
            return
        if path == "/api/session/clear":
            session = app.store.load_or_none(body.get("id") or "")
            if session is None:
                self._error("会话不存在", 404)
                return
            if self._reject_if_busy(session["id"]):
                return
            session["messages"] = []
            session["usage"] = core.empty_usage()
            self._json({"ok": True, "session": app.store.save(session)})
            return
        if path == "/api/session/rewind":
            session = app.store.load_or_none(body.get("id") or "")
            if session is None:
                self._error("会话不存在", 404)
                return
            if self._reject_if_busy(session["id"]):
                return
            messages = session.get("messages") or []
            index = None
            for position in range(len(messages) - 1, -1, -1):
                if messages[position].get("role") == "user":
                    index = position
                    break
            if index is None:
                self._error("没有可重发的提问")
                return
            text = messages[index].get("content") or ""
            session["messages"] = messages[:index]
            app.store.save(session)
            self._json({"ok": True, "session": session, "text": text})
            return
        if path == "/api/context":
            self._context(body)
            return
        if path == "/api/upload_image":
            self._upload_image(body)
            return
        if path == "/api/upload":
            self._upload(body)
            return
        if path == "/api/open":
            self._open_path(body)
            return
        if path == "/api/workspace":
            self._set_workspace(body)
            return
        if path == "/api/workspace/create":
            self._create_workspace(body)
            return
        if path == "/api/workspace/add":
            self._add_workspace(body)
            return
        if path == "/api/workspace/rename":
            self._rename_workspace(body)
            return
        if path == "/api/workspace/remove":
            self._remove_workspace(body)
            return
        if path == "/api/workspace/open":
            self._open_workspace()
            return
        if path == "/api/skills/save":
            self._skill_save(body)
            return
        if path == "/api/skills/delete":
            self._skill_delete(body)
            return
        if path == "/api/session/skills":
            self._session_skills(body)
            return
        if path == "/api/context/remove":
            session = app.store.load_or_none(body.get("id") or "")
            if session is None:
                self._error("会话不存在", 404)
                return
            try:
                index = int(body.get("index"))
            except (TypeError, ValueError):
                self._error("索引无效")
                return
            context = session.get("context") or []
            if 0 <= index < len(context):
                context.pop(index)
            session["context"] = context
            self._json({"ok": True, "session": app.store.save(session)})
            return
        if path == "/api/settings":
            patch = body.get("settings") or {}
            if not isinstance(patch, dict):
                self._error("settings 必须是一个对象")
                return
            allowed = {}
            for key in ("model", "endpoint", "temperature", "max_tokens", "max_tool_rounds",
                        "tools_enabled", "system_prompt", "trim_messages", "context_max_items",
                        "theme", "workspace_root", "allow_outside_workspace", "price",
                        "access_token", "host", "port",
                        "loop_repeat_limit", "max_run_seconds", "max_round_reasoning_chars",
                        "max_subagents", "subagent_max_rounds", "search", "reasoning_effort",
                        "sse_write_timeout", "thinking", "auth_header",
                        "allow_desktop_control", "confirm_desktop_input", "confirm_messages",
                        "auto_compact", "compact_threshold_ratio",
                        "max_images", "confirm_high_risk_commands", "allow_self_restart",
                        "notify", "autostart"):
                if key in patch:
                    allowed[key] = patch[key]
            app.settings.save_webui(allowed)
            # 开机自启：配置只管"老师希望开着"，真正生效的是注册表，所以这里同步写一次
            if "autostart" in allowed:
                ok, message, on = core.set_autostart(
                    app.base_dir, bool(allowed["autostart"]), exe_path=app.exe_path)
                if not ok:
                    log_line("开机自启设置失败：%s" % message)
                app.settings.save_webui({"autostart": on})
            # 模型 API Key 等字段写回 config/agent.json（agent.exe 也读这个文件）；
            # 无密钥不覆盖：界面留空表示"不改"，避免误清空
            agent_patch = {}
            api_key = str(patch.get("api_key") or "").strip()
            if api_key and api_key != "__KEEP__":
                agent_patch["api_key"] = api_key
            if "clear_api_key" in patch and patch["clear_api_key"]:
                agent_patch["api_key"] = ""
            if "base_url" in patch and str(patch.get("base_url") or "").strip():
                agent_patch["base_url"] = str(patch["base_url"]).strip()
            if agent_patch:
                app.settings.save_agent(agent_patch)
            app.settings.reload()
            self._json({"ok": True, "config": app.settings.public_config()})
            return
        if path == "/api/autostart/repair":
            # 设置里的「一键修复」：注册表 + 启动文件夹都写好，清掉 msconfig 的禁用记录
            ok, message = core.repair_autostart(app.base_dir, exe_path=app.exe_path)
            log_line("开机自启一键修复（%s）：%s" % ("成功" if ok else "失败", message))
            self._json({"ok": ok, "message": message,
                        "report": core.autostart_diagnose(
                            app.base_dir, exe_path=app.exe_path)})
            return
        if path == "/api/agent/test":
            self._test_connection()
            return
        if path == "/api/command":
            self._command(body)
            return
        if path == "/api/snapshots/restore":
            self._restore(body)
            return
        if path == "/api/classroom/pick":
            self._classroom_pick(body)
            return
        if path == "/api/figure":
            self._figure(body)
            return
        if path == "/api/calc":
            self._calc(body)
            return
        if path == "/api/chem":
            self._chem(body)
            return
        if path == "/api/units":
            self._units(body)
            return
        if path == "/api/circuit":
            self._circuit(body)
            return
        if path == "/api/practice":
            self._practice(body)
            return
        if path == "/api/instruments":
            self._instruments(body)
            return
        if path == "/api/physics":
            self._physics(body)
            return
        if path == "/api/bio":
            self._bio(body)
            return
        if path == "/api/float/ping":
            commands = app.float_ping(body.get("sessionId") or "")
            self._json({"ok": True, "commands": commands, "uiStamp": ui_stamp()})
            return
        if path == "/api/float/bye":
            app.float_bye()
            self._json({"ok": True})
            return
        if path == "/api/float/config":
            # 悬浮工具栏的配置（存在 config\float.json 里）：网页设置里改 QQ 下载目录 / 应用路径
            self._float_config(body)
            return
        if path == "/api/float/command":
            self._float_command(body)
            return
        if path == "/api/float/quit":
            app.request_exit(body.get("delay") or 1.0)
            self._json({"ok": True})
            return
        if path == "/api/memo":
            ok, error, items = memo_module.apply(app.base_dir, body)
            # 不管成功失败都回整份清单：前端直接重画，不用自己维护状态
            payload = {"ok": ok, "items": items}
            if not ok:
                payload["error"] = error
            self._json(payload)
            return
        if path == "/api/translate":
            self._translate(body)
            return
        if path == "/api/plane/window":
            self._plane_window(body)
            return
        if path == "/api/plane/apply":
            self._plane_change("apply", body)
            return
        if path == "/api/plane/undo":
            self._plane_change("undo")
            return
        if path == "/api/plane/redo":
            self._plane_change("redo")
            return
        self._error("未知接口 %s" % path, 404)

    # -- 工作区 -----------------------------------------------------------
    def _set_workspace(self, body):
        app = self.app
        raw = (body.get("path") or "").strip().strip('"')
        if not raw:
            self._error("请选择工作区目录")
            return
        target = core.canonical_path(raw)
        try:
            if not os.path.isdir(target):
                os.makedirs(target)
        except OSError as exc:
            self._error("无法创建工作区：%s" % exc)
            return
        app.settings.save_webui({"workspace_root": target})
        app.settings.remember_workspace(target)          # 侧栏按工作区分组时要记住最近用过的
        app.settings.reload()
        self._json({"ok": True, "workspace": app.settings.workspace_root(),
                    "config": app.settings.public_config()})

    def _create_workspace(self, body):
        """新建工作区：在指定位置下真的建一个文件夹，登记进列表，并切过去。"""
        app = self.app
        try:
            target = app.settings.create_workspace(body.get("name"), body.get("parent") or "")
        except (ValueError, OSError) as exc:
            self._error("无法新建工作区：%s" % exc)
            return
        app.settings.save_webui({"workspace_root": target})
        app.settings.remember_workspace(target, (body.get("name") or "").strip())
        app.settings.reload()
        self._json({"ok": True, "workspace": app.settings.workspace_root(),
                    "config": app.settings.public_config()})

    def _add_workspace(self, body):
        """把一个已有的文件夹加进工作区列表（不新建文件夹）。"""
        app = self.app
        target = core.canonical_path(body.get("path"))
        if not target:
            self._error("请选择文件夹")
            return
        try:
            if not os.path.isdir(target):
                os.makedirs(target)
        except OSError as exc:
            self._error("无法打开该文件夹：%s" % exc)
            return
        app.settings.remember_workspace(target, (body.get("title") or "").strip())
        app.settings.reload()
        self._json({"ok": True, "workspace": target, "config": app.settings.public_config()})

    def _rename_workspace(self, body):
        app = self.app
        try:
            app.settings.rename_workspace(body.get("path"), body.get("title"))
        except ValueError as exc:
            self._error(str(exc))
            return
        app.settings.reload()
        self._json({"ok": True, "config": app.settings.public_config()})

    def _remove_workspace(self, body):
        """从列表里移除：只删登记，不删文件夹、不删会话。"""
        app = self.app
        app.settings.forget_workspace(body.get("path"))
        app.settings.reload()
        self._json({"ok": True, "config": app.settings.public_config()})

    def _open_workspace(self):
        workspace = self.app.settings.workspace_root()
        try:
            if not os.path.isdir(workspace):
                os.makedirs(workspace)
        except OSError as exc:
            self._error("工作区不存在且无法创建：%s" % exc)
            return
        try:
            if os.name == "nt":
                os.startfile(workspace)  # noqa: S606 - 打开资源管理器
            else:
                subprocess.Popen(["xdg-open", workspace])
        except Exception as exc:  # noqa: BLE001
            self._error("无法打开文件夹：%s" % exc)
            return
        self._json({"ok": True, "workspace": workspace})

    # -- 技能管理 ---------------------------------------------------------
    def _skill_save(self, body):
        app = self.app
        name = (body.get("name") or "").strip()
        description = (body.get("description") or "").strip()
        content = body.get("content") or ""
        if not name or len(name) > 32 or re.search(r'[\\/:*?"<>|]', name):
            self._error("技能名称不合法（1-32 个字符，不能包含 \\ / : * ? \" < > |）")
            return
        if not content.strip():
            self._error("技能内容不能为空")
            return
        directory = os.path.join(app.base_dir, "skills", name)
        try:
            if not os.path.isdir(directory):
                os.makedirs(directory)
            target = os.path.join(directory, "SKILL.md")
            body_text = "---\nname: %s\ndescription: %s\n---\n\n%s\n" % (
                name, description or name, content.strip())
            with open(target, "w", encoding="utf-8") as handle:
                handle.write(body_text)
        except OSError as exc:
            self._error("保存技能失败：%s" % exc)
            return
        # 老师自己又建了同名技能：把"删过"的记录抹掉，不然新技能会被自己挡住
        app.settings.unmark_skill_deleted(name)
        self._json({"ok": True, "path": target, "skills": self._skill_list()})

    def _skill_delete(self, body):
        app = self.app
        name = (body.get("name") or "").strip()
        root = os.path.join(app.base_dir, "skills")
        target = os.path.join(root, name)
        if not name or not os.path.normcase(os.path.dirname(target)).startswith(
                os.path.normcase(root)) or not os.path.isdir(target):
            self._error("技能不存在：%s" % name)
            return
        try:
            shutil.rmtree(target)
        except OSError as exc:
            self._error("删除技能失败：%s" % exc)
            return
        # 记在 config\webui.json 里：技能装在程序目录，升级会整个换掉，
        # 不记住的话老师删掉的技能下次升级又回来了（老师专门提过这件事）。
        app.settings.mark_skill_deleted(name)
        self._json({"ok": True, "skills": self._skill_list()})

    def _session_skills(self, body):
        app = self.app
        session = app.store.load_or_none(body.get("id") or "")
        if session is None:
            self._error("会话不存在", 404)
            return
        if self._reject_if_busy(session["id"]):
            return
        known = {skill.name for skill in core.load_skills(app.base_dir,
                                                          skip=app.settings.deleted_skills())}
        wanted = [str(name) for name in (body.get("skills") or []) if str(name) in known]
        session["skills"] = wanted
        self._json({"ok": True, "session": app.store.save(session)})

    # -- 图片上传（识图：截图、相机照片）----------------------------------
    def _save_upload(self, name, data):
        """把上传内容写进工作区，返回保存后的路径。"""
        app = self.app
        if not os.path.isdir(app.upload_dir):
            os.makedirs(app.upload_dir)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        target = os.path.join(app.upload_dir, "%s-%s" % (stamp, name))
        index = 1
        while os.path.exists(target):
            target = os.path.join(app.upload_dir, "%s-%d-%s" % (stamp, index, name))
            index += 1
        with open(target, "wb") as handle:
            handle.write(data)
        return target

    def _upload(self, body):
        """通用上传：文件选择器里的「从电脑选择文件」走这里（浏览器只能给文件名，拿不到路径）。"""
        name = os.path.basename(body.get("name") or "上传文件")
        expect = (body.get("expect") or "").strip()
        try:
            data = base64.b64decode(body.get("data") or "", validate=False)
        except (ValueError, TypeError) as exc:
            self._error("文件数据无法解码：%s" % exc)
            return
        if not data:
            self._error("缺少文件内容")
            return
        limit = core.MAX_IMAGE_BYTES if expect == "image" else 32 * 1024 * 1024
        if len(data) > limit:
            self._error("文件过大（%.1f MB），上限 %d MB"
                        % (len(data) / 1048576.0, limit // 1048576))
            return
        if expect == "image":
            if os.path.splitext(name)[1].lower() not in core.IMAGE_MIME:
                self._error("只支持 png/jpg/jpeg/gif/webp/bmp 图片")
                return
            width, height = core.image_size(data)
        else:
            extension = os.path.splitext(name)[1].lower()
            if extension in BLOCKED_OPEN:
                self._error("出于安全考虑，不允许上传该类型的可执行文件")
                return
            width = height = 0
        try:
            target = self._save_upload(name, data)
        except OSError as exc:
            self._error("保存失败：%s" % exc)
            return
        self._json({"ok": True, "path": target, "name": os.path.basename(target),
                    "mime": core.image_mime(target), "bytes": len(data),
                    "width": width, "height": height})

    def _open_path(self, body):
        """用系统默认程序打开生成的文件（一体机上点一下就能看课件）。"""
        raw = (body.get("path") or "").strip()
        if not raw:
            self._error("缺少文件路径")
            return
        target = os.path.abspath(os.path.expandvars(raw))
        if not os.path.exists(target):
            self._error("文件不存在：%s" % target)
            return
        # Windows 解析路径时会丢掉结尾的空格和点（"cmd.exe " 和 "cmd.exe" 是同一个文件），
        # 所以判扩展名之前要先按同样的规则归一化，否则黑名单形同虚设。
        # 注意这里只是判断用，真正打开的还是原路径。
        extension = os.path.splitext(target.rstrip(" ."))[1].lower()
        if os.path.isfile(target) and extension in BLOCKED_OPEN:
            self._error("出于安全考虑，不允许直接运行 %s 文件" % extension)
            return
        try:
            if os.name == "nt":
                os.startfile(target)  # noqa: S606
            else:
                subprocess.Popen(["xdg-open", target])
        except Exception as exc:  # noqa: BLE001
            self._error("无法打开：%s" % exc)
            return
        self._json({"ok": True, "path": target})

    def _upload_image(self, body):
        app = self.app
        name = os.path.basename(body.get("name") or "图片.png")
        payload = body.get("data") or ""
        if not payload:
            self._error("缺少图片数据")
            return
        extension = os.path.splitext(name)[1].lower()
        if extension not in core.IMAGE_MIME:
            self._error("只支持 png/jpg/jpeg/gif/webp/bmp 图片")
            return
        try:
            data = base64.b64decode(payload, validate=False)
        except (ValueError, TypeError) as exc:
            self._error("图片数据无法解码：%s" % exc)
            return
        if len(data) > core.MAX_IMAGE_BYTES:
            self._error("图片过大（%.1f MB），请压缩到 %d MB 以内"
                        % (len(data) / 1048576.0, core.MAX_IMAGE_BYTES // 1048576))
            return
        width, height = core.image_size(data)
        if not os.path.isdir(app.upload_dir):
            os.makedirs(app.upload_dir)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        target = os.path.join(app.upload_dir, "%s-%s" % (stamp, name))
        index = 1
        while os.path.exists(target):
            target = os.path.join(app.upload_dir,
                                  "%s-%d-%s" % (stamp, index, name))
            index += 1
        try:
            with open(target, "wb") as handle:
                handle.write(data)
        except OSError as exc:
            self._error("保存图片失败：%s" % exc)
            return
        self._json({"ok": True, "path": target, "name": os.path.basename(target),
                    "mime": core.image_mime(target), "bytes": len(data),
                    "width": width, "height": height})

    # -- 上下文（附件 / 内网页面）-----------------------------------------
    def _context(self, body):
        app = self.app
        session = app.store.load_or_none(body.get("id") or "")
        if session is None:
            self._error("会话不存在", 404)
            return
        if self._reject_if_busy(session["id"]):
            return
        kind = body.get("kind") or "file"
        source = (body.get("source") or "").strip()
        handler = app.make_tools()
        try:
            if body.get("data"):
                name = os.path.basename(body.get("name") or "upload.txt")
                stamp = time.strftime("%Y%m%d-%H%M%S")
                if not os.path.isdir(app.upload_dir):
                    os.makedirs(app.upload_dir)
                target = os.path.join(app.upload_dir, "%s-%s" % (stamp, name))
                with open(target, "wb") as handle:
                    handle.write(base64.b64decode(body["data"]))
                source = target
                kind = "file"
            if not source:
                self._error("缺少文件路径或 URL")
                return
            if kind == "url":
                text, _meta = handler.run("read_url", {"url": source})
            else:
                text, _meta = handler.run("read_file", {"path": source, "max_lines": 4000})
        except (tools_module.ToolError, OSError, ValueError) as exc:
            self._error("加载失败：%s" % exc, 400)
            return
        limit = int(app.settings.webui.get("context_max_items") or 12)
        items = session.get("context") or []
        items.append({"kind": kind, "source": source, "content": text,
                      "created": time.strftime("%Y-%m-%d %H:%M:%S")})
        session["context"] = items[-limit:] if limit > 0 else items
        app.store.save(session)
        self._json({"ok": True, "context": _context_view(session),
                    "session": {"id": session["id"], "title": session.get("title")}})

    # -- 手动执行命令（界面上的“运行命令”入口）-----------------------------
    def _command(self, body):
        app = self.app
        command = (body.get("command") or "").strip()
        handler = app.make_tools()
        verdict = handler.authorize("run_command", {"command": command})
        if not verdict.get("allow"):
            self._json({"ok": False, "status": verdict.get("status") or "denied",
                        "output": verdict.get("output") or "命令被拒绝"})
            return
        output, meta = handler.run("run_command", {"command": command})
        self._json({"ok": True, "output": output, "meta": meta})

    # -- 快照回滚 ---------------------------------------------------------
    def _restore(self, body):
        handler = self.app.make_tools()
        try:
            source, target = handler.restore(body.get("file") or "")
            destination = handler.resolve(target, for_write=True)
            with open(source, "rb") as src, open(destination, "wb") as dst:
                dst.write(src.read())
        except (tools_module.ToolError, OSError) as exc:
            self._error("回滚失败：%s" % exc, 400)
            return
        self._json({"ok": True, "path": destination, "from": source})

    # -- 课堂抽学号（大屏上的「完全重置 / 抽下一个」按钮）--------------------
    def _classroom_pick(self, body):
        session = self.app.store.load_or_none(body.get("id") or "")
        if session is None:
            self._error("会话不存在", 404)
            return
        if self._reject_if_busy(session["id"], "正在生成中，等这一轮结束再抽。"):
            return
        reset_only = str(body.get("action") or "") == "reset"
        ok, message, events = self._run_pick(session, reset_only=reset_only)
        if not ok:
            self._error(message)
            return
        self._json({"ok": True, "text": message, "events": events})

    def _run_pick(self, session, reset_only=False):
        """在指定会话上抽一次学号，返回 (成功, 一句话, 界面事件列表)。"""
        app = self.app
        # 生成中的会话不能抽：AgentRun 手里攥着一份会话副本，两边各自 store.save 会互相覆盖，
        # 刚抽出来的学号会被那一轮结束时写回的旧副本冲掉。宁可让老师等两秒。
        if app.session_busy(session["id"], grace=STOP_GRACE_SECONDS):
            return False, "正在生成中，等这一轮结束再抽。", []
        handler = app.make_tools()
        events = []
        handler.session = session
        handler.emit = events.append
        args = {"count": 1}
        if reset_only:
            args["reset_only"] = True
        try:
            text, _meta = handler.run("pick_students", args)
        except tools_module.ToolError as exc:
            return False, str(exc), []
        app.store.save(session)
        return True, text.split("\n")[0], events

    # -- 作图面板（只剩立体）----------------------------------------------
    #
    # 老师要求"把平面模块用来替换以前的函数、方程、平面几何模块"：函数 / 方程 / 平面几何
    # 只能走平面（/api/plane/apply），这条老接口**必须拒绝并指路**，
    # 否则浏览器端就成了绕过收敛的后门（AI 那边 show_figure 已经这么拒绝了，两边要一致）。
    def _figure(self, body):
        """网页作图面板的接口。**只剩立体**（kind=solid），和模型工具 show_figure 对齐。"""
        kind = str((body or {}).get("kind") or "").strip().lower()
        if kind != "solid":
            self._json({
                "ok": False,
                "errors": [{
                    "code": "wrong_tool",
                    "message": "作图接口只剩立体了（kind=solid），不再画平面上的"
                               "函数 / 方程 / 几何（收到的是 %s）" % (kind or "（空）"),
                    "path": "kind",
                    "hint": "平面改用 POST /api/plane/apply：函数 plot_function、"
                            "方程 plot_implicit，圆 / 直线 / 交点 / 动点用 create_object；"
                            "网页上点顶栏「作图」打开的就是几何画板（平面面板）"}],
                "explanation": "平面作图已经统一到平面面板（几何画板），/api/figure 只在画立体时用。",
            }, 400)
            return
        figure, error = plot_module.build(body)
        if error:
            self._error(error)
            return
        self._json({"ok": True, "figure": figure})

    # -- 计算器 / 单位换算（引擎在 calc.py，界面在「计算器」面板）--------------
    def _chem(self, body):
        """化学面板：配平 / 复分解 / 置换 / 反应热 / 查物种 / 查结构，一个接口六种动作。

        和 agent 那边的 chem 工具走**同一个引擎**（webui\\chem.py），不在两边各写一遍逻辑。
        """
        action = str(body.get("action") or "balance")
        try:
            rules = chem_module.load_rules()
        except Exception as exc:                      # noqa: BLE001 - 数据坏了要说人话
            self._error("化学规则表读不了：%s" % exc)
            return
        if action == "balance":
            text, error = chem_module.balance_text(str(body.get("equation") or ""))
            if text is None:
                self._error(error)
                return
            self._json({"ok": True, "equation": text})
            return
        if action == "predict":
            self._json(_with_error(chem_module.predict_double_displacement(
                rules, str(body.get("a") or "").strip(), str(body.get("b") or "").strip())))
            return
        if action == "replace":
            self._json(_with_error(chem_module.predict_replacement(
                rules, str(body.get("metal") or "").strip(),
                str(body.get("other") or "").strip())))
            return
        if action == "thermo":
            try:
                reactants, products = chem_module.parse_equation(str(body.get("equation") or ""))
            except ValueError as exc:
                self._error("读不懂方程式：%s" % exc)
                return
            result = chem_module.reaction_energy(
                chem_module.load_thermo(), rules, reactants, products)
            result["equation"] = " + ".join("%s%s" % ("" if n == 1 else n, f)
                                            for f, n in reactants) + " = " + \
                                 " + ".join("%s%s" % ("" if n == 1 else n, f)
                                            for f, n in products)
            self._json(result)
            return
        if action == "lab":
            op = str(body.get("op") or "catalog")
            if op == "catalog":
                payload = {"ok": True}
                payload.update(chem_module.lab_catalog(rules))
                self._json(payload)
                return
            if op == "mix":
                self._json(_with_error(chem_module.lab_mix(
                    rules, body.get("contents") or [], str(body.get("reagent") or ""))))
                return
            if op == "ignite":
                return self._json(chem_module.lab_ignite(rules, body.get("contents") or []))
            if op == "heat":
                self._json(_with_error(chem_module.lab_heat(rules, body.get("contents") or [])))
                return
            if op == "check":
                self._json(chem_module.lab_check(
                    rules, body.get("apparatus") or [], body.get("links") or []))
                return
            if op == "sim":
                # 实验台的核心：加进去就开始算（条件、限度、随时间的轨迹都在引擎里）
                # sealed：恒容密闭反应釜（气体不逸出，按 pV=nRT 算压强）
                self._json(chem_module.lab_simulate(
                    rules, chem_module.load_thermo(), body.get("contents") or [],
                    temperature=body.get("temperature"),
                    seconds=body.get("seconds"),
                    volume=body.get("volume"),
                    sealed=bool(body.get("sealed")),
                    pressure=body.get("pressure")))
                return
            self._error("不认识的 lab op：%s" % op)
            return
        if action == "species":
            rows = chem_module.species_search(chem_module.load_species(),
                                              str(body.get("query") or ""))
            self._json({"ok": True, "count": len(rows), "species": rows})
            return
        if action == "structure":
            rows = chem_module.find_structures(chem_module.load_structures(),
                                               str(body.get("query") or ""),
                                               int(body.get("limit") or 20))
            self._json({"ok": True, "count": len(rows), "structures": rows})
            return
        if action in ("structure2d", "ball3d", "mechanism", "mechanisms"):
            # 有机物结构：SMILES -> 原子/键图 -> 结构式 SVG / 球棍模型坐标 / 断键成键动画
            if action == "mechanisms":
                self._json({"ok": True, "list": molecule_module.mechanism_list()})
                return
            if action == "mechanism":
                payload = molecule_module.reaction_animation(key=str(body.get("key") or ""))
                if not payload:
                    self._json({"ok": False, "error": "没有这条反应的动画"})
                    return
                payload["ok"] = True
                self._json(payload)
                return
            text = str(body.get("smiles") or "")
            resolved = None
            try:
                molecule = molecule_module.parse_smiles(text)
            except molecule_module.MoleculeError as exc:
                # 写的不是 SMILES 时按"名字/分子式"再查一次（老师会直接写「乙醇」）。
                # 查不到才报错——报错里要告诉他两种写法都行，别让他猜。
                found, label = chem_module.smiles_for(text)
                if not found:
                    self._json({"ok": False,
                                "error": "这个式子我还读不了：%s（也可以直接写名称或分子式，"
                                         "例如「乙醇」或 C2H6O，我按名称去查）" % exc})
                    return
                molecule = molecule_module.parse_smiles(found)
                resolved = {"from": text, "smiles": found, "name": label}
            text = resolved["smiles"] if resolved else text
            groups = molecule_module.detect_groups(molecule)
            if action == "structure2d":
                payload = {"ok": True, "formula": molecule.formula(),
                           "svg": molecule_module.render_svg(molecule),
                           "groups": [item["label"] for item in groups],
                           "atoms": len(molecule.atoms), "bonds": len(molecule.bonds)}
                if resolved:
                    payload["resolved"] = resolved
                self._json(payload)
                return
            payload = molecule_module.ball_stick(text)
            payload["ok"] = True
            if resolved:
                payload["resolved"] = resolved
            self._json(payload)
            return
        self._error("不认识的 action：%s" % action)

    def _calc(self, body):
        """算一个算式。exact=精确值（分数/根号），decimal=小数。

        degrees=True 时三角函数按"度"解释（界面上那个 角度/弧度 开关）。
        """
        expression = str(body.get("expression") or "")
        result = calc_module.evaluate(expression,
                                      exact=body.get("exact") is not False,
                                      degrees=bool(body.get("degrees")))
        if not result.get("ok"):
            self._error(result.get("error") or "算不出来")
            return
        self._json({"ok": True, "result": result})

    @staticmethod
    def _range_of(body, axis):
        """图像的手填坐标范围：xmin/xmax（y 同理），两个都给了才算。"""
        try:
            low = body.get(axis + "min")
            high = body.get(axis + "max")
            if low is None or high is None:
                return None
            return [float(low), float(high)]
        except (TypeError, ValueError):
            return None

    def _circuit(self, body):
        """电路编辑器：列元件类型 / 算一遍（读数一起给）/ 出图 / 要示例电路。"""
        action = str(body.get("action") or "solve")
        if action == "types":
            self._json({"ok": True, "types": [
                {"key": key, "name": item["name"], "symbol": item["symbol"],
                 "terminals": list(item["terminals"]),
                 "value": item["value"], "default": item["default"]}
                for key, item in sorted(circuit_module.TYPES.items())]})
            return
        if action == "demo":
            self._json({"ok": True, "circuit": circuit_module.demo()})
            return
        if action == "examples":
            self._json({"ok": True, "examples": circuit_module.examples()})
            return
        if action == "example":
            try:
                self._json({"ok": True, "example": circuit_module.example(body.get("key"))})
            except circuit_module.CircuitError as exc:
                self._error(str(exc))
            return
        if action == "draw":
            # 只画不算是必须的：电路画到一半（还有悬空元件）时算不出来，
            # 但画布不能空掉，不然连"再点哪个接线柱"都做不到
            try:
                self._json({"ok": True, "figure": circuit_module.render(
                    body.get("components") or [], body.get("wires") or [])})
            except circuit_module.CircuitError as exc:
                self._error(str(exc))
            return
        if action == "chart":
            # 图像（M8）：x/y 轴各填一个记号，把滑动变阻器扫一遍算出来画成图
            try:
                payload = circuit_module.plot_chart(
                    body.get("components") or [], body.get("wires") or [],
                    body.get("x"), body.get("y"),
                    ratios=body.get("ratios"), steps=int(body.get("steps") or 41),
                    current_ratio=body.get("ratio"),
                    x_range=self._range_of(body, "x"),
                    y_range=self._range_of(body, "y"))
            except (circuit_module.CircuitError, chart_module.ChartError) as exc:
                self._error(str(exc))
                return
            self._json(payload)
            return
        components = body.get("components") or []
        wires = body.get("wires") or []
        try:
            result = circuit_module.solve(components, wires)
            figure = circuit_module.render(components, wires, result["readings"])
        except circuit_module.CircuitError as exc:
            self._error(str(exc))
            return
        result["figure"] = figure
        self._json(result)

    def _practice(self, body):
        """物理练习模式（M7）：四种题型出题 / 判分（错了自动进错题本）/ 看错题本。"""
        action = str(body.get("action") or "types")
        if action == "types":
            self._json({"ok": True, "types": practice_module.types(),
                        "instruments": instruments_module.kinds()})
            return
        if action in ("new", "question"):
            try:
                question = practice_module.new_question(str(body.get("qtype") or "reading"),
                                                        body.get("seed"), body.get("kind"),
                                                        body.get("model"))
            except practice_module.PracticeError as exc:
                self._error(str(exc))
                return
            self._json({"ok": True, "question": question})
            return
        if action == "grade":
            try:
                result = practice_module.grade_and_record(
                    body.get("question") or {}, body.get("answers") or {},
                    self.app.make_tools() if body.get("record", True) else None)
            except practice_module.PracticeError as exc:
                self._error(str(exc))
                return
            self._json(result)
            return
        if action in ("mistakes", "book"):
            tools = self.app.make_tools()
            _path, payload = classroom_module.load_mistakes(tools, {})
            text, _meta = classroom_module.mistake_book(
                tools, {"action": "list", "topic": str(body.get("topic") or ""),
                        "limit": int(body.get("limit") or 20)})
            items = payload.get("items") or []
            limit = int(body.get("limit") or 20)
            self._json({"ok": True, "text": text, "total": len(items),
                        "mistakes": {"items": items[-limit:], "total": len(items)}})
            return
        self._error("不认识的 action：%s" % action)

    def _instruments(self, body):
        """读数模块（M7）：列仪器 / 出一道读数题 / 判一个读数。

        判分按高考估读口径（见 instruments.py 的表），错的要说清"少估读一位"还是"数值不对"。
        """
        action = str(body.get("action") or "kinds")
        if action == "kinds":
            self._json({"ok": True, "instruments": instruments_module.kinds()})
            return
        if action in ("drill", "question"):
            try:
                self._json(instruments_module.drill(str(body.get("kind") or ""),
                                                    body.get("seed"),
                                                    body.get("params") or None))
            except instruments_module.ReadError as exc:
                self._error(str(exc))
            return
        if action == "judge":
            try:
                result = instruments_module.score(str(body.get("kind") or ""),
                                                  float(body.get("expected")),
                                                  body.get("answer"))
            except instruments_module.ReadError as exc:
                self._error(str(exc))
                return
            except (TypeError, ValueError):
                self._error("expected 要是数字")
                return
            self._json(result)
            return
        if action == "figure":
            try:
                self._json({"ok": True, "figure": instruments_module.figure(
                    str(body.get("kind") or ""), body.get("params") or {})})
            except instruments_module.ReadError as exc:
                self._error(str(exc))
            return
        self._error("不认识的 action：%s" % action)

    def _bio(self, body):
        """生物过程动画：一份过程表（54 个课本过程）+ 通用分步图，详细数据见 webui/biology.py。"""
        action = str(body.get("action") or "list").strip()
        if action == "list":
            self._json({"ok": True, "chapters": biology_module.chapters(),
                        "processes": biology_module.processes(body.get("chapter"))})
            return
        if action == "detail":
            try:
                row = biology_module.process(body.get("key"))
            except ValueError as exc:
                self._error(str(exc))
                return
            figures = [biology_module.figure(row["key"], index)
                       for index in range(len(row["steps"]))]
            self._json({"ok": True, "process": row, "figures": figures})
            return
        self._error("不认识的 action：%s" % action)

    def _physics(self, body):
        """物理平面引擎（M6）：列模型 / 求解一个模型。

        求解是**一次算完整条过程**（分段闭式解 + 轨迹采样 + 过程卡），界面只负责画和播。
        """
        action = str(body.get("action") or "sim")
        if action == "models":
            self._json({"ok": True, "models": physics_module.models()})
            return
        if action == "scene_demo":
            self._json({"ok": True, "scene": sim2d_module.demo()})
            return
        if action == "examples":
            self._json({"ok": True, "examples": sim2d_module.examples()})
            return
        if action == "example":
            try:
                self._json({"ok": True, "example": sim2d_module.example(body.get("key"))})
            except sim2d_module.SceneError as exc:
                self._error(str(exc))
            return
        if action == "run":
            try:
                self._json(sim2d_module.run(body.get("scene") or {}))
            except sim2d_module.SceneError as exc:
                self._error(str(exc))
            return
        if action == "chart":
            # 图像（M8）：按 x/y 表达式把每一帧画成图（动画播放时前端只挪红点）
            try:
                self._json(sim2d_module.plot_chart(body.get("scene") or {},
                                                   body.get("x"), body.get("y"),
                                                   cursor=body.get("index"),
                                                   x_range=self._range_of(body, "x"),
                                                   y_range=self._range_of(body, "y")))
            except (sim2d_module.SceneError, chart_module.ChartError) as exc:
                self._error(str(exc))
            return
        if action not in ("sim", "simulate"):
            self._error("不认识的 action：%s" % action)
            return
        try:
            payload = physics_module.simulate(body.get("model") or body.get("key") or "",
                                              body.get("params") or {})
        except physics_module.PhysicsError as exc:
            self._error(str(exc))
            return
        self._json(payload)

    def _units(self, body):
        """单位换算面板：列物理量 / 列单位 / 搜索 / 换算，四种动作一个接口。"""
        action = str(body.get("action") or "measures")
        if action == "measures":
            self._json({"ok": True, "measures": calc_module.measures()})
            return
        if action == "units":
            measure = str(body.get("measure") or "")
            if measure not in units_module.MEASURES:
                self._error("没有这个物理量")
                return
            self._json({"ok": True, "units": calc_module.unit_list(measure),
                        "base": units_module.MEASURES[measure]["base"],
                        "cn": units_module.MEASURES[measure]["cn"]})
            return
        if action == "search":
            self._json({"ok": True,
                        "hits": calc_module.search_units(str(body.get("query") or ""))})
            return
        if action == "convert":
            try:
                value = float(body.get("value"))
            except (TypeError, ValueError):
                self._error("要换算的数值填得不对")
                return
            try:
                result = calc_module.convert(str(body.get("measure") or ""), value,
                                             str(body.get("from") or ""),
                                             str(body.get("to") or ""))
            except (ValueError, ZeroDivisionError) as error:
                self._error(str(error))
                return
            self._json({"ok": True, "value": result,
                        "exact": calc_module.decimal_text(result, 10),
                        "from": body.get("from"), "to": body.get("to")})
            return
        self._error("不认识的动作：%s" % action)

    # -- 悬浮工具栏的配置（存在 config\float.json，网页设置里能改 QQ 下载目录）----
    def _float_config(self, body):
        patch = body.get("config")
        if not isinstance(patch, dict):
            self._error("config 必须是一个对象")
            return
        ok, message = apps_module.save_config(self.app.base_dir, {
            key: patch.get(key) for key in apps_module.EDITABLE_KEYS if key in patch})
        if not ok:
            self._error(message)
            return
        self._json({"ok": True, "config": apps_module.config_view(self.app.base_dir)})

    # -- 悬浮工具栏 ---------------------------------------------------------
    def _float_capture(self, folder_name, prefix):
        """截当前屏幕，存进工作区的某个子目录。返回 (路径, 尺寸信息, 错误)。"""
        app = self.app
        if not app.settings.webui.get("allow_desktop_control", True):
            return "", None, "桌面操作已在设置里关掉了（设置 → 电脑操作），截不了屏。"
        folder = os.path.join(app.settings.workspace_root(), folder_name)
        try:
            if not os.path.isdir(folder):
                os.makedirs(folder)
        except OSError as exc:
            return "", None, "建不了目录 %s：%s" % (folder, exc)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        path = os.path.join(folder, "%s-%s.png" % (prefix, stamp))
        index = 1
        while os.path.exists(path):
            path = os.path.join(folder, "%s-%s-%d.png" % (prefix, stamp, index))
            index += 1
        info, error = desktop_module.capture(path)
        if error:
            return "", None, error
        return path, info, ""

    def _float_mark(self, note=""):
        """课堂时间点：截一张图 + 往工作区的清单里追一条记录。"""
        app = self.app
        path, _info, error = self._float_capture("课堂时间点", "时间点")
        if error:
            return False, error, 0
        target = os.path.join(app.settings.workspace_root(), MARKER_FILE)
        payload = {"items": []}
        if os.path.isfile(target):
            try:
                with open(target, "r", encoding="utf-8-sig") as handle:
                    loaded = json.load(handle)
                if isinstance(loaded, dict) and isinstance(loaded.get("items"), list):
                    payload = loaded
            except (OSError, ValueError):
                payload = {"items": []}          # 清单坏了也不能挡住记录时间点
        record = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "image": path,
                  "note": str(note or "")[:200], "session": app.float_state()["session"]}
        payload["items"].append(record)
        try:
            with open(target, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
        except OSError as exc:
            return False, "截图存好了（%s），但时间点清单写不进去：%s" % (path, exc), 0
        return True, "已记下课堂时间点 %s（第 %d 个）" % (record["time"],
                                                          len(payload["items"])), 1

    def _float_command(self, body):
        """悬浮工具栏的按钮 → 服务端动作。

        返回 ``OK|给老师看的话|focus=0/1``：focus=1 表示结果在网页上，悬浮窗顺手把网页
        切到前台。截图 / 板书 / 时间点特意是 focus=0 —— 抢焦点会把网页截进图里。
        """
        app = self.app
        action = str(body.get("action") or "").strip()
        ok, message, focus = True, "", 0

        if action == "app":
            ok, message = apps_module.open_app(str(body.get("app") or "").strip(), app.base_dir)
            if ok:
                app.float_enqueue({"action": "toast", "message": message})
        elif action == "pick":
            session = app.store.load_or_none(app.float_state()["session"])
            if session is None:
                ok, message = False, "还没打开对话：先在网页里新建一个会话。"
            else:
                ok, message, events = self._run_pick(session, reset_only=False)
                if ok:
                    for event in events:
                        app.float_enqueue({"action": "board", "event": event})
                    focus = 1
        elif action == "timer":
            app.float_enqueue({"action": "ask_timer"})
            message, focus = "选择计时时长", 1
        elif action == "shot":
            path, _info, error = self._float_capture("截图", "截图")
            if error:
                ok, message = False, error
            else:
                # 只把路径交给网页，图片的尺寸/类型由网页问 /api/image_info —— 那边本来就有
                app.float_enqueue({"action": "attach_image", "path": path})
                message = "截图已放进待发送的图片"
        elif action == "board_save":
            path, _info, error = self._float_capture("板书", "板书")
            if error:
                ok, message = False, error
            else:
                message = "已保存板书：%s" % os.path.basename(path)
                app.float_enqueue({"action": "toast", "message": message})
        elif action == "mark":
            ok, message, focus = self._float_mark(body.get("note") or "")
            if ok:
                app.float_enqueue({"action": "toast", "message": message})
        elif action == "open_ppt":
            # 「切到PPT」的兜底：任务栏里没有演示程序，就用默认应用打开最近收到的那份课件
            ok, message = apps_module.open_latest_presentation(app.base_dir)
        elif action == "physics":
            # 「物理」按钮：打开物理沙盒面板（轨道/场/物块/受力矢量/图像都在那边）
            app.float_enqueue({"action": "physics"})
            message, focus = "打开物理沙盒", 1
        elif action == "phet":
            # 「PhET」按钮：打开 PhET 面板（物理/化学/生物的互动仿真，随包内置）
            app.float_enqueue({"action": "phet"})
            message, focus = "打开 PhET 模拟", 1
        elif action == "bio":
            # 「生物」按钮：打开生物过程动画面板（50 多个课本过程的分步动画，见 webui/biology.py）
            app.float_enqueue({"action": "bio"})
            message, focus = "打开生物过程动画", 1
        elif action == "chem":
            # 「化学」按钮：打开化学实验台
            app.float_enqueue({"action": "chem"})
            message, focus = "打开化学实验台", 1
        elif action == "figure":
            # 数学函数 / 几何画板：打开网页上的作图面板，让老师直接敲表达式，或者接着让 AI 画
            kind = str(body.get("kind") or "function").strip().lower()
            kind = "geometry" if kind in ("geometry", "geo") else "function"
            app.float_enqueue({"action": "figure", "kind": kind})
            message, focus = "打开作图面板", 1
        elif action == "calc":
            # 计算器 / 单位换算：打开网页上的面板（一体机是触摸屏，那边有大键盘）
            tab = "units" if str(body.get("tab") or "").strip().lower() == "units" else "math"
            app.float_enqueue({"action": "calc", "tab": tab})
            message, focus = "打开计算器", 1
        elif action == "translate":
            # 翻译查词：网页上有输入框和结果区，这里只负责把它叫出来
            app.float_enqueue({"action": "translate"})
            message, focus = "打开翻译查词", 1
        elif action == "memo":
            # 备忘录：笔记存在 config\memo.json，网页上那个面板负责增删改
            app.float_enqueue({"action": "memo"})
            message, focus = "打开备忘录", 1
        elif action == "calendar":
            # 日历：农历 / 节气 / 节假日都在 /api/calendar，网页面板负责画
            app.float_enqueue({"action": "calendar"})
            message, focus = "打开日历", 1
        elif action == "show":
            focus = 1
        else:
            ok, message = False, "悬浮工具栏不认识这个动作：%s" % action

        if not ok and not message:
            message = "操作没有成功"
        # 这一行是 PowerShell 按 | 切开读的，消息里不能再出现 | 或换行
        safe = message.replace("|", "／").replace("\r", " ").replace("\n", " ").strip()
        self._text("%s|%s|focus=%d" % ("OK" if ok else "ERR", safe, focus))

    # -- 日历 -------------------------------------------------------------
    def _calendar(self, query):
        """一个月的日历：农历 + 节假日 / 调休。

        农历从 webui\\lunar.py 的压缩表来（不联网，永远有）；放假安排从
        timor.tech 来（可能拿不到），拿不到就报 source=none/cache，页面照常画。
        """
        app = self.app
        today = time.localtime()
        try:
            year = int((query.get("year") or [""])[0] or today.tm_year)
            month = int((query.get("month") or [""])[0] or today.tm_mon)
        except ValueError:
            self._error("year / month 必须是数字")
            return
        if not (lunar_module.MIN_YEAR <= year <= lunar_module.MAX_YEAR):
            self._error("农历只支持 %d-%d 年" % (lunar_module.MIN_YEAR, lunar_module.MAX_YEAR))
            return
        if not 1 <= month <= 12:
            self._error("月份要在 1-12 之间")
            return

        holidays = holiday_module.view(app.base_dir, year)
        # 手里这份不是刚联网拿的（缓存过期 / 内置离线表）：先照常画，同时让 holiday
        # 在后台刷一次。页面收到 pending 会自己再问一次 —— 老师不用再点任何东西，
        # 也就不会出现"日历一直转圈 / 下载不下来"。
        pending = False
        if holidays.get("stale"):
            pending = holiday_module.refresh_soon(app.base_dir, year)
        days_in_month = calendar.monthrange(year, month)[1]
        today_text = time.strftime("%Y-%m-%d")
        days = []
        for day in range(1, days_in_month + 1):
            key = "%04d-%02d-%02d" % (year, month, day)
            entry = holidays["days"].get(key) or {}
            weekday = calendar.weekday(year, month, day) + 1     # 1=周一 ... 7=周日
            is_weekend = weekday >= 6
            # 接口里有的按接口（法定假日 / 调休上班）；接口没提的就是普通周末
            if entry:
                is_holiday = bool(entry.get("holiday"))
                is_workday = not is_holiday
            else:
                is_holiday = is_weekend
                is_workday = False
            days.append({
                "date": key,
                "day": day,
                "weekday": weekday,
                "lunar": lunar_module.lunar_text((year, month, day)),
                "term": "",                      # 24 节气没做，见 webui\lunar.py 顶部说明
                "holiday": str(entry.get("name") or ""),
                "isHoliday": is_holiday,
                "isWorkday": is_workday,
                "isToday": key == today_text,
            })
        self._json({"ok": True, "year": year, "month": month,
                    "source": holidays["source"], "fetchedAt": holidays["fetchedAt"],
                    "pending": pending,
                    "days": days})

    # -- 翻译查词 ---------------------------------------------------------
    def _translate(self, body):
        """翻译 / 查词：单独一次模型请求，不带工具、不写进对话历史。

        走的是 core 里已有的"一次性调用"写法（见 AgentRun._summarize）：
        直接拿 client.stream 发一个最小 payload，收到完整正文再回给前端。
        没配模型 / 连不上 / 模型报错：一律 ok:false + 中文原因，前端只看这个字段。
        """
        app = self.app
        source = str(body.get("text") or "").strip()
        mode = str(body.get("mode") or "translate").strip().lower()
        if mode not in ("translate", "word"):
            mode = "translate"
        if not source:
            self._json({"ok": False, "error": "没有要翻译的内容", "mode": mode})
            return
        source = source[:2000]                   # 查词/翻译不需要整篇课文
        resolved = app.settings.resolve_model(None)
        if not resolved["model"]:
            self._json({"ok": False, "error": "尚未配置模型，请先在设置中填写模型名称",
                        "mode": mode})
            return
        if not resolved["endpoint"]:
            self._json({"ok": False, "error": "尚未配置接口地址 base_url", "mode": mode})
            return
        if mode == "word":
            system = ("你是英语词典。用中文解释用户给的词：先给音标和词性，"
                      "再列常用义项，最后给一两个例句。简洁，不要寒暄。")
            instruction = "解释这个词：%s"
        else:
            system = ("你是中英互译助手。给自然、地道的译文；原文里的生词另外列出，"
                      "每个词给音标和词性。不要寒暄，不要解释你的做法。")
            instruction = "翻译下面这段，并按上面的要求列出其中的生词：\n%s"
        payload = {
            "model": resolved["model"],
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": instruction % source}],
            "stream": False,
        }
        try:
            client = core.client_for(
                resolved,
                timeout=int(app.settings.webui.get("translate_timeout") or 30),
                ca_file=app.settings.agent.get("ca_file") or "",
                insecure=bool(app.settings.agent.get("insecure_skip_verify")))
            result = client.stream(payload, lambda kind, text: None)
        except core.ChatError as exc:
            self._json({"ok": False, "error": "调用模型失败：%s" % exc.message,
                        "mode": mode})
            return
        except Exception as exc:                 # noqa: BLE001 - 网络/解析什么都可能出
            self._json({"ok": False, "error": "调用模型失败：%s" % exc, "mode": mode})
            return
        text = (result.get("content") or "").strip()
        if not text:
            self._json({"ok": False, "error": "模型没有返回内容", "mode": mode})
            return
        self._json({"ok": True, "text": text, "mode": mode})

    # -- 平面（对象模型 + 依赖图）-----------------------------------------
    def _plane_state_or_error(self):
        """取一次平面状态；取不到就回一句中文说明并返回 None。"""
        state, problem = _plane_invoke("state")
        if problem:
            self._error("平面模块尚未就绪：%s" % problem, 503)
            return None
        if not isinstance(state, dict):
            self._error("平面模块没有返回状态（拿到的是 %s）" % type(state).__name__, 500)
            return None
        return state

    def _plane_window(self, body):
        """网页端把"现在看得见的范围"报上来：自动区间的曲线按它重采样。

        只影响"画多少"，不加版本、不进撤销栈 —— 拖一下视野不该变成一次可撤销的操作。
        **故意不广播**给别的窗口：window 是全局的，两个窗口各自报自己的视野会互相
        触发重采样、来回打架；各窗口只在自己的视野变了的时候同步。
        ymin/ymax 是可选的（隐式曲线 / 不等式区域要两个方向都知道才跟着视野走）。
        """
        fields = {}
        for key in ("xmin", "xmax"):
            value = (body or {}).get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                self._error("窗口要写 %s（数字），例如 {\"xmin\": -10, \"xmax\": 10}" % key)
                return
            fields[key] = float(value)
        for key in ("ymin", "ymax"):
            value = (body or {}).get(key)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                self._error("窗口的 %s 要写数字，例如 {\"ymin\": -6, \"ymax\": 6}" % key)
                return
            fields[key] = float(value)
        result, problem = _plane_invoke("window", **fields)
        if problem:
            self._error("平面模块尚未就绪：%s" % problem, 503)
            return
        if not isinstance(result, dict):
            self._error("平面模块没有返回结果（window 拿到的是 %s）" % type(result).__name__, 500)
            return
        self._json(result)

    def _plane_change(self, kind, body=None):
        """apply / undo / redo 走同一条路：调一次、原样回冻结形状、广播给别的窗口。"""
        if kind == "apply":
            ops = (body or {}).get("ops")
            if isinstance(ops, dict):
                ops = [ops]
            if not isinstance(ops, list) or not ops:
                self._error("ops 必须是非空数组，例如 "
                            "[{\"op\":\"create\",\"type\":\"point\",\"id\":\"A\","
                            "\"definition\":{\"x\":0,\"y\":0}}]")
                return
            result, problem = _plane_invoke("apply", ops)
        else:
            result, problem = _plane_invoke(kind)
        if problem:
            self._error("平面模块尚未就绪：%s" % problem, 503)
            return
        if not isinstance(result, dict):
            self._error("平面模块没有返回结果（%s 拿到的是 %s）" % (kind, type(result).__name__), 500)
            return
        self._json(result)
        if result.get("ok"):
            self.app.plane_bus.publish({"t": "plane", "version": result.get("stateVersion")})

    def _plane_export(self, query):
        """导出：json 由服务端给；svg 让平面模块自己画（它不会就明说）。"""
        fmt = ((query.get("format") or ["json"])[0] or "json").strip().lower()
        if fmt == "json":
            state = self._plane_state_or_error()
            if state is not None:
                self._json({"ok": True, "format": "json", "state": state})
            return
        if fmt == "svg":
            text, problem = _plane_invoke("svg")
            if not problem and isinstance(text, str) and text.lstrip().startswith("<"):
                body = text.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "image/svg+xml; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return
            self._error("平面模块还不会导出 SVG%s。网页端工具栏上的「导出 SVG」是从画布直接"
                        "序列化的，不依赖这条接口。"
                        % ("（%s）" % problem if problem else ""), 501)
            return
        if fmt == "png":
            self._error("服务端不生成 PNG（随包只有标准库，不做位图编码）。网页端工具栏的"
                        "「导出 PNG」是先把画布序列化成 SVG、再画到 canvas 导出的，用那个。", 501)
            return
        self._error("不支持的导出格式 %s（只有 svg / json；png 在网页端做）" % fmt)

    def _plane_events(self):
        """平面的变更长连接（SSE）：一个窗口改图，别的窗口跟着刷新。

        只推"有新版本了"（见 PlaneBus），状态由各窗口自己去 /api/plane/state 取。
        """
        app = self.app
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
        except Exception:  # noqa: BLE001 - 网页在这个瞬间关了
            return
        self.close_connection = True
        try:
            self.connection.settimeout(float(app.settings.webui.get("sse_write_timeout") or 30))
        except (OSError, TypeError, ValueError):
            pass
        lock = threading.Lock()
        alive = {"value": True}

        def write(payload=None):
            # payload=None 是心跳注释（连上以后长时间没变更也不能被中间设备掐断）
            line = b": keepalive\n\n" if payload is None else (
                "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n").encode("utf-8")
            with lock:
                if not alive["value"]:
                    return False
                try:
                    self.wfile.write(line)
                    self.wfile.flush()
                    return True
                except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, OSError):
                    alive["value"] = False
                    return False

        state, problem = _plane_invoke("state")
        version = state.get("version") if isinstance(state, dict) else None
        # 第一条先报当前版本：网页据此马上判断要不要拉一次状态，不用等第一次改动
        if not write({"t": "plane", "version": version, "hello": True,
                      "ready": not problem, "error": problem or None}):
            return
        if not app.plane_bus.subscribe(write):
            write({"t": "plane", "error": "同时打开的平面窗口太多了，关掉几个再刷新本页。"})
            return
        try:
            while alive["value"]:
                time.sleep(SSE_HEARTBEAT_SECONDS)
                if alive["value"] and not write():
                    break
        finally:
            app.plane_bus.unsubscribe(write)

    # -- SSE 对话 ---------------------------------------------------------
    def _chat(self):
        app = self.app
        body = self._read_json()
        text = (body.get("text") or "").strip()
        if not text:
            self._error("消息不能为空")
            return
        session_id = body.get("sessionId") or ""
        session = app.store.load_or_none(session_id) if session_id else None
        if session is None:
            session = app.store.create(title="新会话",
                                       model=body.get("model") or "",
                                       workspace=app.settings.workspace_root())
        if not session.get("workspace"):
            session["workspace"] = app.settings.workspace_root()
        if body.get("model"):
            session["model"] = body["model"]
        if body.get("effort") is not None:
            level = str(body.get("effort") or "").strip().lower()
            if level and level not in core.REASONING_EFFORTS:
                self._error("不支持的推理等级：%s" % level)
                return
            session["effort"] = level
        images = []
        for entry in body.get("images") or []:
            if isinstance(entry, dict):
                path = entry.get("path") or ""
                if path:
                    images.append({"path": path,
                                   "name": entry.get("name") or os.path.basename(path),
                                   "mime": entry.get("mime") or core.image_mime(path)})
            elif entry:
                images.append({"path": str(entry), "name": os.path.basename(str(entry)),
                               "mime": core.image_mime(str(entry))})
        model_info = app.settings.resolve_model(session.get("model") or None)
        if not model_info["model"]:
            self._error("尚未配置模型，请先在设置中填写模型名称")
            return
        if not model_info["endpoint"]:
            self._error("尚未配置接口地址 base_url")
            return

        # 同一个会话同时只能有一个运行：两个窗口一起提问会让后写的一方覆盖掉对方的对话记录。
        # 但"上次那次已经被停止、只是还卡在某个工具里"不该把老师锁在门外——
        # 超过 STOP_GRACE 秒就直接把它踢出登记表，让新的提问继续。
        if app.session_busy(session["id"], grace=STOP_GRACE_SECONDS):
            self._error("这个对话正在生成中，请等它结束，或另开一个对话。", 409)
            return
        for stale_id in app.stale_run_ids(session["id"], grace=STOP_GRACE_SECONDS):
            app.end_run(stale_id)

        run_id = "run_" + base64.urlsafe_b64encode(os.urandom(9)).decode("ascii").strip("=")
        cancel = threading.Event()
        run = app.new_run(run_id, cancel, session_id=session["id"])
        client = core.client_for(
            model_info,
            timeout=int(app.settings.agent.get("timeout_seconds") or 300),
            ca_file=app.settings.agent.get("ca_file") or "",
            insecure=bool(app.settings.agent.get("insecure_skip_verify")),
        )
        handler = app.make_tools()
        disconnected = {"value": False}

        # new_run 已经登记了：从这里到下面 try/finally 之间还有一段会抛异常的操作
        # （写响应头。老师在这个瞬间关页面就会 BrokenPipeError）。不在这里收尾的话，
        # 登记表里会留下一个 stopping_at=0 的僵尸运行，这个对话会一直被判"正在生成中"。
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
        except Exception:
            app.end_run(run_id)
            raise
        self.close_connection = True
        # 浏览器休眠/合盖后客户端不再读，write 会一直阻塞并占着 emit_lock；
        # 给这条连接设个超时，卡住时会抛 OSError，下面的 emit 就会把这次运行取消掉
        try:
            self.connection.settimeout(float(app.settings.webui.get("sse_write_timeout") or 30))
        except (OSError, TypeError, ValueError):
            pass

        # 子代理是并行跑的，多个线程会同时往同一条 SSE 流写数据，必须串行化
        emit_lock = threading.Lock()
        # 心跳：长时间跑工具（拖鼠标、命令、等接口）时 SSE 一直不写东西，
        # 浏览器/代理会以为连接死了直接掐断（社区已确认：claude-code #25979/#70017、
        # opencode #31281）。定期写一行 SSE 注释（`: keepalive`）保温，界面会忽略它。
        last_write = {"ts": time.time()}
        heartbeat = {"stop": False}

        def write_raw(payload):
            with emit_lock:
                try:
                    self.wfile.write(payload)
                    self.wfile.flush()
                    last_write["ts"] = time.time()
                    return True
                except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, OSError):
                    disconnected["value"] = True
                    cancel.set()
                    return False

        def beat():
            while not heartbeat["stop"]:
                time.sleep(SSE_HEARTBEAT_SECONDS)
                if heartbeat["stop"] or disconnected["value"]:
                    return
                if time.time() - last_write["ts"] < SSE_HEARTBEAT_SECONDS * 0.8:
                    continue
                if not write_raw(b": keepalive\n\n"):
                    return

        def emit(event):
            if disconnected["value"]:
                return
            write_raw(("data: " + json.dumps(event, ensure_ascii=False) + "\n\n").encode("utf-8"))

        def confirmer(call_id, name, args):
            waiter = {"event": threading.Event(), "approved": False}
            with run["lock"]:
                run["pending"][call_id] = waiter
            try:
                deadline = time.time() + CONFIRM_TIMEOUT
                while not waiter["event"].is_set():
                    if cancel.is_set() or disconnected["value"]:
                        return False
                    if time.time() > deadline:
                        emit({"t": "notice", "level": "warn", "message": "确认超时，已自动拒绝。"})
                        return False
                    waiter["event"].wait(0.5)
                return bool(waiter["approved"])
            finally:
                with run["lock"]:
                    run["pending"].pop(call_id, None)

        effort = core.resolve_effort(app.settings, session)
        emit({"t": "start", "runId": run_id, "sessionId": session["id"],
              "model": model_info["model"], "endpoint": model_info["endpoint"],
              "profile": model_info["profile"], "title": session.get("title"),
              "effort": effort, "images": len(images)})
        beat_thread = threading.Thread(target=beat, daemon=True)
        beat_thread.start()
        try:
            runner = core.AgentRun(app.settings, session, client, handler,
                                   emit, confirmer, cancel, store=app.store)
            runner.subagent_runner = core.make_subagent_runner(
                app.settings, client, handler, emit, cancel,
                model=session.get("model") or "", effort=effort)
            runner.run(text, images=images)
        except Exception as exc:  # noqa: BLE001
            emit({"t": "error", "message": "运行失败：%s" % exc})
        finally:
            heartbeat["stop"] = True
            app.end_run(run_id)
            emit({"t": "close"})


    # -- 连接自检 ---------------------------------------------------------
    def _test_connection(self):
        """真的打一次接口（GET /models），把网关的原始报错原样告诉老师。

        401 = Key 不对；402 = 余额不足；429 = 限流；404 = 地址写错。
        这样"填了 Key 还是不生效"就有据可查，不用猜。
        """
        app = self.app
        model = app.settings.resolve_model()
        if not model["model"]:
            self._error("还没填模型名称")
            return
        if not model["endpoint"]:
            self._error("还没填接口地址 base_url")
            return
        key = model["api_key"] or ""
        header = model.get("auth_header") or core.DEFAULT_AUTH_HEADER
        sent = ("%s: %s" % (header, "Bearer " + core.mask_secret(key)) if header.lower() == "authorization"
                else "%s: %s" % (header, core.mask_secret(key))) if key else "（没有发送密钥）"
        models_url = model["endpoint"].replace("/chat/completions", "/models")
        client = core.client_for(
            model,
            timeout=int(app.settings.agent.get("timeout_seconds") or 300),
            ca_file=app.settings.agent.get("ca_file") or "",
            insecure=bool(app.settings.agent.get("insecure_skip_verify")))
        try:
            ids = client.list_models()
            if not ids:
                # 能连上但一个模型都没有：多半地址不对。不能报"连接正常"——
                # list_models 会吞掉 404 回去试 /v1/models，两条都 404 时只是返回空列表。
                self._json({"ok": False, "status": 0, "models": [], "endpoint": models_url,
                            "authHeader": sent, "keyMasked": core.mask_secret(key),
                            "error": "接口有响应，但没有返回任何模型。",
                            "hint": "多半是接口地址不对（/models 和 /v1/models 都返回 404/405）。"
                                    "DeepSeek 官方地址是 https://api.deepseek.com，带 /v1 或不带都可以，"
                                    "服务端会自动补全；如果这是单位自建网关，确认它是否提供 /models —— "
                                    "有的网关没有这个接口，但聊天照样能用，那就直接发一条消息试试。"}, 200)
                return
            self._json({"ok": True, "models": ids, "endpoint": models_url,
                        "authHeader": sent, "keyMasked": core.mask_secret(key),
                        "message": "连接正常，接口返回 %d 个模型。" % len(ids)})
        except core.ChatError as exc:
            hint = ""
            code = getattr(exc, "status", None)
            if code == 401:
                hint = ("网关返回 401：密钥无效或没带上。请在 DeepSeek 平台重新生成 API Key"
                        "（platform.deepseek.com/api_keys），粘贴时注意不要带空格或换行；"
                        "如果这是单位自建网关，确认它要的请求头是 %s（有的网关要 api-key 或 x-api-key，"
                        "可以在「认证方式」里改）。" % header)
            elif code == 402:
                hint = "网关返回 402：账户余额不足，请充值后再试。"
            elif code == 429:
                hint = "网关返回 429：请求太频繁，等一会儿再试或降低并发。"
            elif code in (404, 405):
                hint = ("网关返回 %s：接口地址不对。DeepSeek 官方地址是 https://api.deepseek.com，"
                        "带 /v1 或不带都可以，服务端会自动补全。" % code)
            elif code == 400:
                hint = "网关返回 400：请求格式被拒绝，检查接口地址是否是 OpenAI 兼容的 chat/completions。"
            self._json({"ok": False, "status": code, "error": exc.message, "hint": hint,
                        "endpoint": models_url, "authHeader": sent,
                        "keyMasked": core.mask_secret(key)}, 200)
        except Exception as exc:      # noqa: BLE001 - 网络异常也要如实报
            self._json({"ok": False, "error": "连接失败：%s" % exc, "hint": "检查网络/代理/证书设置。",
                        "endpoint": models_url, "authHeader": sent,
                        "keyMasked": core.mask_secret(key)}, 200)

    def _context_view(self, session):
        return _context_view(session)


def _context_view(session):
    items = []
    for index, item in enumerate(session.get("context") or []):
        content = item.get("content") or ""
        items.append({
            "index": index,
            "kind": item.get("kind"),
            "source": item.get("source"),
            "chars": len(content),
            "preview": content[:200],
        })
    return items


def _already_running(host, port, timeout=2):
    """端口被占用时确认一下是不是“我们自己已经在跑”（老师双击了两次是常事）。"""
    target = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    try:
        with urllib.request.urlopen("http://%s:%d/api/state" % (target, port),
                                    timeout=timeout) as response:
            body = response.read(200000).decode("utf-8", "replace")   # 读完，别让服务端写一半就断
    except Exception:      # noqa: BLE001 - 只是探测，失败就按“不是我们”处理
        return False
    return '"sessions"' in body and '"config"' in body


def float_toolbar_command(base_dir, url, powershell=None):
    """拼出拉起悬浮工具栏的命令行；脚本不在就返回 None。

    单独拆出来是为了能测：PowerShell 的 `-File` 绑定器**不接受空的参数值**
    （`-Token ""` 会直接报 "Missing an argument for parameter 'Token'" 然后什么都不做），
    所以本机模式（没有令牌）时这个参数必须**整个省掉**，不能传空串。
    """
    script = os.path.join(base_dir, "scripts", "study7agent-float.ps1")
    if not os.path.isfile(script):
        return None
    if powershell is None:
        powershell = os.path.join(os.environ.get("SystemRoot") or r"C:\Windows",
                                  "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
        if not os.path.isfile(powershell):
            powershell = "powershell.exe"
    parsed = urlparse(url)
    command = [powershell, "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass",
               "-File", script,
               "-Api", "%s://%s" % (parsed.scheme, parsed.netloc),
               "-Url", url]
    token = (parse_qs(parsed.query).get("token") or [""])[0]
    if token:
        command += ["-Token", token]
    # 把 exe 路径也告诉工具栏：它那个「显示界面」在窗口被关掉之后要靠这个 exe 把界面重新开出来
    # （没传的话它会去猜、猜不到就只能让老师自己双击图标 —— 绝不退回去开默认浏览器）。
    exe = os.path.join(base_dir, "Study7Agent.exe")
    if os.path.isfile(exe):
        command += ["-Exe", exe]
    return command


def start_float_toolbar(base_dir, url, disabled=False):
    """拉起悬浮工具栏（scripts\\study7agent-float.ps1）。

    它和网页是一对：点它把网页切到前台，网页一关它就把 WebUI 服务和它自己一起收掉。
    非 Windows、脚本缺失、或者老师传了 --no-float 时静默跳过——它只是锦上添花，
    绝不能因为它起不来就让整个服务起不来。
    """
    if disabled or os.name != "nt":
        return False
    command = float_toolbar_command(base_dir, url)
    if command is None:
        return False
    try:
        # CREATE_NO_WINDOW：不要多弹一个黑窗口出来，工具栏自己就是全部界面
        subprocess.Popen(command, creationflags=0x08000000)
    except OSError as exc:
        sys.stderr.write("悬浮工具栏没能启动（不影响使用）：%s\n" % exc)
        return False
    return True


def start_frontend(base_dir, url, no_open=False, no_float=False, open_browser=True):
    """起完服务之后的"前端"两件事，返回 (开了系统浏览器, 起了悬浮工具栏)。

    这两件事**互不相干**：内嵌内核版永远带 --no-open（界面由启动器用内置内核打开），
    而悬浮工具栏是老师上课要用的，不能跟着一起消失——关它只有 --no-float 一个开关。
    以前这两句写在一个 if 里，内嵌内核版一上线工具栏就会静默没了。
    """
    opened = False
    if open_browser and not no_open:
        threading.Thread(target=lambda: (time.sleep(0.4), webbrowser.open(url))).start()
        opened = True
    return opened, start_float_toolbar(base_dir, url, no_float)


def _exit_soon(delay):
    try:
        time.sleep(max(0.0, min(10.0, float(delay))))
    except (TypeError, ValueError):
        time.sleep(1.0)
    _hard_exit()


def _hard_exit():
    """立刻结束进程。

    走 os._exit 而不是让 serve_forever 自己收尾：SSE 长连接和后台线程会拖住进程，
    而这个进程的父窗口（start-webui.cmd 的控制台）正等着它退出好一起关掉。
    """
    try:
        sys.stdout.flush()
    except Exception:                                   # noqa: BLE001 - 关不掉也要退
        pass
    os._exit(0)


def _with_error(result):
    """给 "ok: false" 的化学结果补一个 error 字段。

    前端/agent 拿到的失败原因要统一能读（配不平、不反应、资料未收录都算**答案**，
    但调用方总得有个字段可以显示），不然界面上只会看到一句"请求失败：HTTP 200"。
    """
    if isinstance(result, dict) and result.get("ok") is False and not result.get("error"):
        result = dict(result)
        result["error"] = result.get("reason") or "算不出来"
    return result


class QuietServer(ThreadingHTTPServer):
    """浏览器关页面/休眠会直接断开连接，这类噪声不要刷满老师的控制台窗口。"""

    daemon_threads = True
    # Windows 上 SO_REUSEADDR 允许两个进程绑同一个端口（Linux 不允许），
    # 双击两次启动器就会出现两个服务各写一份会话文件，所以 Windows 上必须关掉
    allow_reuse_address = os.name != "nt"
    allow_reuse_port = False

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError,
                            TimeoutError, socket.timeout)):
            return
        if isinstance(exc, OSError) and getattr(exc, "winerror", None) in (10053, 10054, 10038):
            return
        ThreadingHTTPServer.handle_error(self, request, client_address)


def build_server(base_dir, host, port, verbose=False, data_dir=None, exe_path=None):
    app = App(base_dir, data_dir=data_dir, exe_path=exe_path)
    # 开机自启对齐一次：配置里说开着、注册表里却没有（换了 exe 位置、被优化软件清掉）就补上。
    # 只补"该开没开"，老师关掉之后不会再偷偷开回来。
    try:
        note = core.ensure_autostart(app.settings, exe_path=app.exe_path)
        if note:
            print("[配置] " + note)
    except Exception as exc:                          # noqa: BLE001 - 设不上也不能挡启动
        print("[配置] 开机自启没设上：%s" % exc)
    server = QuietServer((host, port), Handler)
    server.app = app
    # 记下真正绑定的地址：_authorized 要靠它判断"这是不是只对本机开放"
    server.bind_host = host
    server.verbose = verbose
    return server


def main(argv=None):
    core.make_console_safe()          # 中文 Win7 上重定向输出不因个别字符中断
    parser = argparse.ArgumentParser(description="Study7Agent WebUI")
    parser.add_argument("--base-dir", default=None, help="agent 根目录（含 config 与 agent.exe）")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--data-dir", default=None,
                        help="会话与运行数据的目录（默认 webui\\data；内嵌内核版由启动器指到程序目录外，升级不丢）")
    parser.add_argument("--no-open", action="store_true", help="启动后不自动打开浏览器")
    parser.add_argument("--no-float", action="store_true",
                        help="不启动悬浮工具栏（scripts\\study7agent-float.ps1，仅 Windows）")
    parser.add_argument("--verbose", action="store_true", help="打印访问日志")
    parser.add_argument("--exe-path", default=None,
                        help="本程序 exe 的完整路径（启动器传；单文件版的开机自启要指向它）")
    args = parser.parse_args(argv)

    base_dir = args.base_dir or os.path.dirname(HERE)
    if not os.path.isdir(os.path.join(base_dir, "config")):
        sys.stderr.write("警告：%s 下没有 config 目录，请用 --base-dir 指定 agent 根目录。\n" % base_dir)

    settings = core.Settings(base_dir)
    host = args.host or settings.webui.get("host") or "127.0.0.1"
    port = args.port or int(settings.webui.get("port") or 8770)

    # 先看是不是已经有一个自己在跑了（双击两次启动器是常事）：
    # Windows 上允许重复绑定同一端口，不先查就会起出第二个服务，两个进程各写一份会话
    if _already_running(host, port):
        url = "http://%s:%d/" % ("127.0.0.1" if host in ("0.0.0.0", "::") else host, port)
        print("Study7Agent 已经在本机 %s 上运行，直接打开这个地址即可：%s" % (port, url))
        if not args.no_open and settings.webui.get("open_browser", True):
            webbrowser.open(url)
        # 第二次双击启动器是常事：服务不用再起，但工具栏可能被关掉了，补一个
        start_float_toolbar(base_dir, url, args.no_float)
        return 0

    try:
        server = build_server(base_dir, host, port, verbose=args.verbose,
                              data_dir=args.data_dir, exe_path=args.exe_path)
    except OSError as exc:
        sys.stderr.write("无法监听 %s:%s —— %s\n" % (host, port, exc))
        sys.stderr.write("请关闭占用该端口的程序，或用 --port 指定其它端口。\n")
        return 1

    actual_port = server.server_address[1]
    token = settings.webui.get("access_token") or ""
    loopback = host in ("127.0.0.1", "localhost", "::1")
    if not loopback and not token:
        # 监听局域网却没有令牌 = 同网段任何人都能执行本机命令；
        # 自动生成一个并落盘，命令行里打印带令牌的地址（浏览器会记住它）。
        token = secrets.token_urlsafe(16)
        settings.save_webui({"access_token": token})
        settings.reload()
        print("  已自动生成访问令牌（局域网访问需要）")
    url = "http://127.0.0.1:%d/" % actual_port if loopback else "http://%s:%d/?token=%s" % (host, actual_port, token)

    model = settings.resolve_model()
    print("Study7Agent WebUI %s" % core.VERSION)
    print("  根目录 : %s" % base_dir)
    print("  模型   : %s" % (model["model"] or "(未配置)"))
    print("  接口   : %s" % (model["endpoint"] or "(未配置)"))
    print("  工作区 : %s" % settings.workspace_root())
    print("  地址   : %s" % url)
    print("  停止   : Ctrl+C")
    if not loopback and not token:
        print("  警告   : 正在监听非本机地址且未设置访问令牌，任何能访问该端口的人都能执行本机命令！")
    sys.stdout.flush()

    if settings.webui.get("open_browser", True) and not args.no_open:
        start_frontend(base_dir, url, no_open=False, no_float=args.no_float, open_browser=True)
    else:
        # 不开系统浏览器不等于不要工具栏：两个开关各管各的
        start_float_toolbar(base_dir, url, args.no_float)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
