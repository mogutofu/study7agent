# -*- coding: utf-8 -*-
"""常驻的 UI Automation 工作进程（Python 侧）。

老的桌面层每调一次工具就新起一个 powershell.exe：进程 + Add-Type 编译先花掉约 0.5 秒，
之后每个元素的每个属性又是一次跨进程 COM 调用。实测本机：新起进程 544 ms，
常驻进程一次往返 0.13 ms。所以这里只起**一个** powershell.exe（跑 uia_worker.ps1，
里面那份 C# 只编译一次），之后每条命令就是「写一行、读一行」。

协议（行分隔符 \\n，字段分隔符 \\x1f，行间分隔符 \\x1e，都在 uia_worker.ps1 顶部有说明）：
    请求：<seq> \\x1f <命令> \\x1f <参数>...
    应答：<seq> \\x1f ok  \\x1f <meta> \\x1e <rows>   /   <seq> \\x1f err \\x1f <消息>

两条硬性教训（都来自实测）：
  * 工作进程里任何未捕获异常都会让整个常驻进程死掉，下一次写管道直接
    OSError(22)；所以 worker 每条命令都 try/catch 并一定回一行，Python 这边
    也要能识别「进程没了」并自动重启。
  * UIA 遍历一旦卡在某个提供程序上就没法从外部中断，只能杀进程；所以超时和
    「停止」按钮都走「杀掉 + 下次重启」这条路。
"""

import atexit
import os
import subprocess
import sys
import threading
import time

try:
    from Queue import Queue, Empty  # Python 2 兼容（本项目实际跑 3.8，保险起见）
except ImportError:
    from queue import Queue, Empty  # type: ignore

CREATE_NO_WINDOW = 0x08000000
DEFAULT_TIMEOUT = 60.0
START_TIMEOUT = 120.0

US = "\x1f"
RS = "\x1e"

# 树行的字段顺序（和 uia_worker.ps1 里 Describe() 的 Row(...) 一一对应）
TREE_FIELDS = ("depth", "path", "kind", "ctrl", "name", "aid", "x", "y", "width", "height",
               "enabled", "offscreen", "focusable", "focused", "password", "value",
               "range_min", "range_max", "toggle", "expand",
               "scroll_v", "scroll_h", "scroll_v_pct", "scroll_h_pct",
               "hwnd", "pid", "class_name", "shortcut", "window")
# 查找行的字段顺序
FIND_FIELDS = ("depth", "path", "ctrl", "name", "aid", "x", "y", "width", "height",
               "enabled", "offscreen", "focusable", "field", "hit", "hwnd", "window")


class UiaError(Exception):
    """UIA 层的问题（起不来、编译失败、命令报错）。"""


class UiaCancelled(Exception):
    """被「停止」信号叫停。"""


def as_cancel(callback):
    """把「停止」信号统一成一个无参判定函数：返回 True 表示该停。

    两种都要收，因为两种都真的在用：

      - `threading.Event` —— `webui\\core.py` 每一轮开始前注入的就是它
        （`self.cancel = cancel`，`server.py` 里 `cancel = threading.Event()`；
        `tools.cancelled()` 也是 `cancel.is_set()`），随后
        `desktop_module.set_cancel(self.cancel)` 一路传到这儿；
      - 无参回调（`lambda: True`）—— 测试与自检里这么传。

    这条曾经踩过坑：只当回调调用，Event 就被当函数调，抛
    `TypeError: 'Event' object is not callable` —— 崩在 `Worker.start()` 的轮询里，
    被 `desktop.available()` 兜住并**缓存**成「桌面底座自检失败」，于是整族桌面工具
    全挂（老师 2026-10-07 报的就是这个）。所以这里做一次归一化，两处都不再各自假设。
    """
    if callback is None:
        return None
    if callable(callback):
        return callback
    is_set = getattr(callback, "is_set", None)
    if callable(is_set):
        return is_set
    return callback   # 不认识的东西原样留着：调用方本来就吞异常，不会因此崩


class UiaTimeout(UiaError):
    """命令超时（进程已杀掉，下次调用会自动重启）。"""


def powershell_path():
    """找到 powershell.exe。先看 PATH，再看系统目录（Win7 一定在 System32 下）。"""
    for candidate in ("powershell.exe", "powershell"):
        found = _which(candidate)
        if found:
            return found
    root = os.environ.get("SystemRoot") or r"C:\Windows"
    full = os.path.join(root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    if os.path.isfile(full):
        return full
    sysnative = os.path.join(root, "Sysnative", "WindowsPowerShell", "v1.0", "powershell.exe")
    if os.path.isfile(sysnative):
        return sysnative
    return "powershell.exe"


def _which(name):
    paths = (os.environ.get("PATH") or "").split(os.pathsep)
    for folder in paths:
        if not folder:
            continue
        candidate = os.path.join(folder, name)
        if os.path.isfile(candidate):
            return candidate
    return None


def _to_int(text, fallback=0):
    try:
        return int(str(text).strip())
    except (TypeError, ValueError):
        return fallback


def _to_float(text, fallback=0.0):
    try:
        return float(str(text).strip())
    except (TypeError, ValueError):
        return fallback


def parse_rows(rows_text, fields):
    """把 \\x1e 分隔的行、\\x1f 分隔的字段变成一串字典。"""
    out = []
    if not rows_text:
        return out
    for line in rows_text.split(RS):
        if not line:
            continue
        values = line.split(US)
        row = {}
        for index, name in enumerate(fields):
            row[name] = values[index] if index < len(values) else ""
        for key in ("depth", "x", "y", "width", "height", "scroll_v_pct", "scroll_h_pct", "hwnd", "pid"):
            if key in row:
                row[key] = _to_int(row[key], 0)
        for key in ("enabled", "offscreen", "focusable", "focused", "password", "scroll_v", "scroll_h"):
            if key in row:
                row[key] = str(row[key]).strip() in ("1", "true", "True")
        out.append(row)
    return out


class UiaWorker(object):
    """一个常驻的 UIA 工作进程。线程安全（内部一把锁串行化请求）。"""

    def __init__(self, script=None, timeout=DEFAULT_TIMEOUT, start_timeout=START_TIMEOUT):
        folder = os.path.dirname(os.path.abspath(__file__))
        self.script = script or os.path.join(folder, "uia_worker.ps1")
        self.timeout = timeout
        self.start_timeout = start_timeout
        self._lock = threading.RLock()
        self._proc = None
        self._queue = Queue()
        self._reader = None
        self._errors = []
        self._seq = 0
        self._cancel = None
        self._starts = 0
        self._failures = 0
        self._pid = None

    # ------------------------------------------------------------ 生命周期

    def set_cancel(self, callback):
        """传无参函数**或 `threading.Event`**；表示「停」就杀掉工作进程（下次自动重启）。"""
        self._cancel = as_cancel(callback)

    def alive(self):
        return self._proc is not None and self._proc.poll() is None

    def status(self):
        return {"running": self.alive(), "pid": self._pid, "starts": self._starts,
                "failures": self._failures, "errors": list(self._errors[-5:]),
                "script": self.script}

    def _spawn(self):
        if not os.path.isfile(self.script):
            raise UiaError("找不到工作进程脚本：%s" % self.script)
        command = [powershell_path(), "-NoProfile", "-NonInteractive", "-STA",
                   "-ExecutionPolicy", "Bypass", "-File", self.script]
        flags = CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, cwd=os.path.dirname(self.script),
                                    creationflags=flags)
        except OSError as error:
            raise UiaError("启动 UIA 工作进程失败：%s" % error)
        self._proc = proc
        self._starts += 1
        self._reader = threading.Thread(target=self._read_stdout, args=(proc,))
        self._reader.daemon = True
        self._reader.start()
        err_thread = threading.Thread(target=self._read_stderr, args=(proc,))
        err_thread.daemon = True
        err_thread.start()

    def _read_stdout(self, proc):
        try:
            while True:
                line = proc.stdout.readline()
                if not line:
                    break
                text = line.decode("utf-8", "replace").rstrip("\r\n")
                if text:
                    self._queue.put((proc, text))
        except Exception:
            pass
        finally:
            self._queue.put((proc, None))  # 进程没了（带上是谁，避免旧进程的消息干扰新进程）

    def _read_stderr(self, proc):
        try:
            for line in iter(proc.stderr.readline, b""):
                text = line.decode("utf-8", "replace").rstrip("\r\n")
                if text:
                    self._errors.append(text)
                    del self._errors[:-40]
        except Exception:
            pass

    def _kill(self):
        proc = self._proc
        self._proc = None
        self._pid = None
        if proc is not None:
            try:
                if proc.poll() is None:
                    proc.kill()
            except Exception:
                pass
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                try:
                    if stream:
                        stream.close()
                except Exception:
                    pass

    def start(self):
        """起进程并等到 READY。已经在跑就直接返回。"""
        with self._lock:
            if self.alive():
                return True
            self._spawn()
            deadline = time.time() + self.start_timeout
            while time.time() < deadline:
                if self._cancel is not None and self._cancel():
                    self._kill()
                    raise UiaCancelled("已取消")
                try:
                    item = self._queue.get(timeout=0.2)
                except Empty:
                    if self._proc is not None and self._proc.poll() is not None:
                        message = "；".join(self._errors[-3:]) or "进程已退出"
                        self._kill()
                        raise UiaError("UIA 工作进程启动失败：%s" % message)
                    continue
                who, line = item if isinstance(item, tuple) else (self._proc, item)
                if who is not self._proc and who is not None:
                    continue  # 上一个进程的残留消息
                if line is None:
                    message = "；".join(self._errors[-3:]) or "进程已退出"
                    self._kill()
                    raise UiaError("UIA 工作进程启动失败：%s" % message)
                parts = line.split(US)
                if parts and parts[0] == "READY":
                    self._pid = _to_int(parts[2].split("=")[-1]) if len(parts) > 2 else None
                    self._errors = []
                    return True
                if parts and parts[0] == "FATAL":
                    message = parts[1] if len(parts) > 1 else line
                    self._kill()
                    raise UiaError("UIA 工作进程编译失败：%s" % message)
            self._kill()
            raise UiaError("UIA 工作进程 %s 秒内没有就绪" % self.start_timeout)

    def stop(self):
        with self._lock:
            proc = self._proc
            if proc is not None and proc.poll() is None:
                try:
                    proc.stdin.write(b"0" + US.encode("utf-8") + b"quit\n")
                    proc.stdin.flush()
                except Exception:
                    pass
                deadline = time.time() + 2.0
                while time.time() < deadline and proc.poll() is None:
                    time.sleep(0.05)
            self._kill()

    def restart(self):
        self.stop()
        return self.start()

    # ------------------------------------------------------------ 请求

    def request(self, command, *args, **kwargs):
        """发一条命令，返回 (meta 字典, 行字典列表)。失败抛 UiaError/UiaTimeout/UiaCancelled。"""
        timeout = kwargs.pop("timeout", None) or self.timeout
        fields = kwargs.pop("fields", None)
        if kwargs:
            raise TypeError("多余参数：%s" % sorted(kwargs))
        with self._lock:
            self.start()
            self._seq += 1
            seq = str(self._seq)
            parts = [seq, command]
            for arg in args:
                text = "" if arg is None else str(arg)
                text = text.replace("\r", " ").replace("\n", " ")
                text = text.replace(US, " ").replace(RS, " ")
                parts.append(text)
            line = US.join(parts) + "\n"
            proc = self._proc
            if proc is None or proc.poll() is not None:
                self._failures += 1
                self._kill()
                raise UiaError("UIA 工作进程已退出")
            try:
                proc.stdin.write(line.encode("utf-8"))
                proc.stdin.flush()
            except (OSError, IOError, ValueError) as error:
                self._failures += 1
                self._kill()
                raise UiaError("写工作进程失败（已重启，请重试）：%s" % error)

            deadline = time.time() + timeout
            while True:
                if self._cancel is not None:
                    try:
                        if self._cancel():
                            self._kill()
                            raise UiaCancelled("已取消")
                    except UiaCancelled:
                        raise
                    except Exception:
                        pass
                left = deadline - time.time()
                if left <= 0:
                    self._failures += 1
                    self._kill()
                    raise UiaTimeout("UIA 命令 %s 超过 %.0f 秒没回（进程已杀掉，下次会重启）"
                                     % (command, timeout))
                try:
                    item = self._queue.get(timeout=min(0.1, max(0.01, left)))
                except Empty:
                    if self._proc is None or self._proc.poll() is not None:
                        self._failures += 1
                        self._kill()
                        message = "；".join(self._errors[-3:]) or "进程已退出"
                        raise UiaError("UIA 工作进程在处理 %s 时退出：%s" % (command, message))
                    continue
                who, answer = item if isinstance(item, tuple) else (self._proc, item)
                if who is not self._proc and who is not None:
                    continue  # 上一个进程的残留消息
                if answer is None:
                    self._failures += 1
                    self._kill()
                    message = "；".join(self._errors[-3:]) or "进程已退出"
                    raise UiaError("UIA 工作进程在处理 %s 时退出：%s" % (command, message))
                head = answer.split(US, 2)
                if len(head) < 2 or head[0] != seq:
                    continue  # 不是这条命令的应答（理论上不会发生）
                if head[1] == "err":
                    raise UiaError("UIA %s 失败：%s" % (command, head[2] if len(head) > 2 else "?"))
                payload = head[2] if len(head) > 2 else ""
                return self._split_payload(payload)

    @staticmethod
    def _split_payload(payload):
        """返回 (meta 字典, 正文串)。正文原样交给业务函数，由它们再按 RS 切。"""
        meta = {}
        rows = ""
        if payload:
            if RS in payload:
                meta_text, body = payload.split(RS, 1)
            else:
                meta_text, body = payload, ""
            for pair in meta_text.split(US):
                if not pair:
                    continue
                if "=" in pair:
                    key, value = pair.split("=", 1)
                    meta[key.strip()] = value
                else:
                    meta[pair.strip()] = ""
            rows = body
        return meta, rows

    # ------------------------------------------------------------ 业务命令

    def ping(self, timeout=30.0):
        meta, _rows = self.request("ping", timeout=timeout)
        return meta

    def tree(self, window="desktop", max_elements=400, max_depth=14, scroll=True,
             name_limit=120, timeout=120.0):
        meta, body = self.request("tree", window, max_elements, max_depth,
                                  1 if scroll else 0, name_limit, timeout=timeout, fields=TREE_FIELDS)
        return meta, parse_rows(body, TREE_FIELDS)

    def find(self, needle, exact=False, window="desktop", max_hits=40, max_scan=4000, timeout=120.0):
        meta, body = self.request("find", needle, 1 if exact else 0, window, max_hits, max_scan,
                                  timeout=timeout, fields=FIND_FIELDS)
        return meta, parse_rows(body, FIND_FIELDS)

    def read(self, window="desktop", max_chars=20000, max_scan=4000, name_limit=200,
             doc_text=False, timeout=40.0):
        """返回 (meta, 行列表)。doc_text=True 才去读 Document 的全文：Chromium 的
        TextPattern 第一次访问要建整棵无障碍树，实测同一个窗口超过 10 秒，所以默认关。"""
        meta, body = self.request("read", window, max_chars, max_scan, name_limit,
                                  1 if doc_text else 0, timeout=timeout)
        lines = [line for line in body.split(RS) if line] if body else []
        return meta, lines

    def resolve(self, hwnd, path, timeout=30.0):
        meta, _rows = self.request("resolve", hwnd, path, timeout=timeout)
        return meta

    def act(self, hwnd, path, action, text="", amount="", timeout=60.0):
        meta, _rows = self.request("act", hwnd, path, action, text, amount, timeout=timeout)
        return meta


_WORKER = None
_WORKER_LOCK = threading.Lock()


def worker():
    """进程内单例（工作进程很贵：编译一次 C#，之后一直复用）。"""
    global _WORKER
    with _WORKER_LOCK:
        if _WORKER is None:
            _WORKER = UiaWorker()
            atexit.register(_shutdown_at_exit)
        return _WORKER


def set_cancel(callback):
    worker().set_cancel(callback)


def shutdown():
    global _WORKER
    with _WORKER_LOCK:
        current = _WORKER
        _WORKER = None
    if current is not None:
        current.stop()


def _shutdown_at_exit():
    try:
        shutdown()
    except Exception:
        pass


def status():
    if _WORKER is None:
        return {"running": False, "pid": None, "starts": 0, "failures": 0,
                "errors": [], "script": None}
    return _WORKER.status()


def snapshot(window="desktop", max_elements=400, max_depth=14, scroll=True, timeout=120.0):
    """遍历界面元素，返回 (meta, 行列表)。"""
    return worker().tree(window=window, max_elements=max_elements, max_depth=max_depth,
                         scroll=scroll, timeout=timeout)


def find(needle, exact=False, window="desktop", max_hits=40, timeout=120.0):
    return worker().find(needle, exact=exact, window=window, max_hits=max_hits, timeout=timeout)


def read(window="desktop", max_chars=20000, doc_text=False, timeout=40.0):
    return worker().read(window=window, max_chars=max_chars, doc_text=doc_text, timeout=timeout)


def resolve(hwnd, path, timeout=30.0):
    return worker().resolve(hwnd, path, timeout=timeout)


def act(hwnd, path, action, text="", amount="", timeout=60.0):
    return worker().act(hwnd, path, action, text=text, amount=amount, timeout=timeout)


if __name__ == "__main__":  # 手工自检：python webui\\uia.py
    for _stream in (sys.stdout, sys.stderr):  # 窗口标题里可能有 GBK 打不出的字符
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    print("powershell:", powershell_path())
    print("脚本:", worker().script)
    started = time.time()
    print("就绪用了 %.1f 秒" % (time.time() - started))
    print("ping:", worker().ping())

    for round_index in (1, 2, 3):
        begin = time.time()
        meta, rows = snapshot(max_elements=400)
        elapsed = (time.time() - begin) * 1000
        print("第 %d 次遍历：%s 行，%.0f ms（%s）"
              % (round_index, len(rows), elapsed, meta))
    kinds = {}
    for row in rows:
        kinds[row["kind"]] = kinds.get(row["kind"], 0) + 1
    print("分类:", kinds)

    # 路径一致性：按记录下来的路径回到同一个元素，名字必须一样
    checked = 0
    mismatched = 0
    for row in rows:
        if row["kind"] != "i" or not row["hwnd"]:
            continue
        info = resolve(row["hwnd"], row["path"])
        checked += 1
        if info.get("ok") != "1":
            mismatched += 1
            continue
        if info.get("name", "").strip() != row["name"].strip():
            mismatched += 1
            if mismatched <= 3:
                print("对不上：path=%s 记录=%r 现在=%r" % (row["path"], row["name"], info.get("name")))
        if checked >= 20:
            break
    print("路径一致性：查了 %d 个，对不上 %d 个" % (checked, mismatched))

    begin = time.time()
    meta, hits = find("确定")
    print("查找「确定」：%s 命中，%.0f ms" % (len(hits), (time.time() - begin) * 1000))
    begin = time.time()
    meta, lines = read(max_chars=4000)
    print("读文字：%d 行 / %s 字，%.0f ms" % (len(lines), meta.get("chars"), (time.time() - begin) * 1000))
    if lines:
        print("开头几行:", [line[:40] for line in lines[:5]])
    print("状态:", status())
    shutdown()
