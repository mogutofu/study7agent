"""桌面控制（Windows）：截图、界面元素、鼠标键盘、窗口 / 进程 / 注册表。

这一版是按 Windows-MCP 的形状重写的（老师选的方案 A）：**工具的名字、参数、返回
都对齐 Windows-MCP**，而执行层换成纯 ctypes 直连 Win32 的三块底座：

  - `webui\\winapi.py`    窗口 / 屏幕 / 鼠标键盘 / 剪贴板 / 进程 / 注册表（ctypes）
  - `webui\\winimage.py`  抓屏画布 + 标注 + PNG 编码（纯标准库，不依赖 Pillow）
  - `webui\\uia.py`       UI 自动化：常驻 PowerShell 里跑 UIA（见 `uia_worker.ps1`）

**为什么要有 Snapshot 的编号（label）**：截图会把能点、能输入的元素框出来，并在
中心写上红色编号；文本里也按同一个编号列出来。之后 `Click(label=3)`、
`Type(label=5, text="你好")`、`Scroll(label=2, direction="down")` 就能指到那个元素。
编号从 **0** 开始（和 Windows-MCP 一致）。界面变了以后编号会失效，重新 Snapshot 一次。

三条硬规矩（旧版就有的约定，测试都钉着）：

  1. **测试/自检期间不许真动键鼠**：`W7A_NO_INPUT=1` 时 `input_blocked()` 为真，
     所有鼠标键盘动作直接返回 `NO_INPUT_MESSAGE`，绝不留副作用。
  2. **老师点「停止」要立刻停**：`set_cancel(信号)` 收两种信号 —— `core.py` 注入的
     `threading.Event`，或测试里的无参回调（见 `uia.as_cancel`）；UIA 常驻进程
     和这里的每个等待循环都轮询它；被叫停就杀进程 / 退出，返回「已被停止」。
  3. 每个公开函数返回 `(结果, 错误文案)`，成功时错误文案是空串 —— 和项目其它模块一致。
"""

import json
import os
import sys
import tempfile
import time

try:
    import winapi
    import winimage
    import uia
    _BASE_ERROR = ""
except Exception as _exc:  # 非 Windows 上导入也不该炸，让 available() 去解释原因
    winapi = None
    winimage = None
    uia = None
    _BASE_ERROR = "%s: %s" % (type(_exc).__name__, _exc)

TIMEOUT = 30.0
MAX_IMAGE = (1920, 1080)     # 截图的像素上限（Windows-MCP 同款预算）
MAX_ELEMENTS = 400           # 一次遍历最多收多少个元素
WAIT_MAX = 120.0

# 旧的 media_key 工具并进了 Shortcut：这些名字现在直接当快捷键按
MEDIA_KEYS = {"play_pause": 0xB3, "next": 0xB0, "prev": 0xB1, "stop": 0xB2,
              "volume_up": 0xAF, "volume_down": 0xAE, "mute": 0xAD}

# 上一次 Snapshot 的结果（label → 元素）。Click/Type/Scroll 靠它把编号变成坐标。
_STATE = {"stamp": 0.0, "window": "", "elements": [], "path": ""}
_CANCEL = None
_AVAILABLE = None
_PROCESS_NAMES = {}


def input_blocked():
    """是否禁止一切"模拟输入"（鼠标、键盘、多媒体键）。

    设环境变量 `W7A_NO_INPUT=1` 就全禁。**自动化测试必须打开它**：
    开发与自检期间不允许真的去动使用者的鼠标和键盘（这是使用者的明确要求，
    工程上也对——测试不该有副作用）。被禁止时工具会明确报错，而不是静默不动。
    """
    return str(os.environ.get("W7A_NO_INPUT") or "").strip() not in ("", "0", "false", "False")


NO_INPUT_MESSAGE = ("当前禁止模拟键鼠操作（环境变量 W7A_NO_INPUT=1，测试/自检期间默认开启）。"
                    "需要真的操作键鼠时，去掉该环境变量后重启服务。")


def _as_cancel(callback):
    """把「停止」信号统一成无参判定函数（`threading.Event` 或无参回调都行）。

    归一化逻辑在 `uia.as_cancel`（那里写了为什么必须两种都收）；`uia` 没加载起来时
    这里再兜一次，避免底座坏了连「停止」都注册不上。
    """
    if uia is not None and hasattr(uia, "as_cancel"):
        return uia.as_cancel(callback)
    if callback is None or callable(callback):
        return callback
    is_set = getattr(callback, "is_set", None)
    return is_set if callable(is_set) else callback


def set_cancel(callback):
    """注册「停止」信号（`core.py` 每轮开始前注入 `self.cancel`，那是个 `threading.Event`；
    停止按钮按下去就是 `event.set()`）。UIA 常驻进程也一起收到。"""
    global _CANCEL
    _CANCEL = _as_cancel(callback)
    if uia is not None:
        try:
            uia.set_cancel(_CANCEL)
        except Exception:
            pass


def _cancelled():
    callback = _CANCEL
    if callback is None:
        return False
    try:
        return bool(callback())
    except Exception:
        return False


def available(refresh=False):
    """桌面控制能不能用（返回 (是/否, 说明)）。

    第一次调用会把 UIA 常驻进程拉起来（编译一次 C#，几秒），之后就一直是热的。
    """
    global _AVAILABLE
    if _AVAILABLE is not None and not refresh:
        return _AVAILABLE
    if _BASE_ERROR:
        _AVAILABLE = (False, "桌面底座加载失败：%s" % _BASE_ERROR)
        return _AVAILABLE
    if sys.platform != "win32":
        _AVAILABLE = (False, "桌面控制只在 Windows 上用（当前系统是 %s）。" % sys.platform)
        return _AVAILABLE
    try:
        winapi.make_dpi_aware()
        info = uia.worker().ping(timeout=120.0)
    except uia.UiaError as exc:
        _AVAILABLE = (False, "UIA 工作进程起不来：%s" % exc)
        return _AVAILABLE
    except Exception as exc:
        _AVAILABLE = (False, "桌面底座自检失败：%s: %s" % (type(exc).__name__, exc))
        return _AVAILABLE
    _AVAILABLE = (True, "Windows UIA 可用（dotnet=%s，uia=%s，pid=%s）"
                  % (info.get("dotnet", "?"), info.get("uia", "?"), info.get("pid", "?")))
    return _AVAILABLE


def _guard(need_input=True):
    """公共前置检查：底座可用 / 没被停止 / （要动键鼠时）没被禁。返回错误文案。"""
    ok, message = available()
    if not ok:
        return message
    if _cancelled():
        return "已被停止"
    if need_input and input_blocked():
        return NO_INPUT_MESSAGE
    return ""


def _input_guard():
    return _guard(need_input=True)


def status():
    """桌面控制的状态（给「状态」页和自检看）。"""
    ok, message = available()
    info = {"available": ok, "message": message, "input_blocked": input_blocked(),
            "elements": len(_STATE["elements"]), "snapshot_window": _STATE["window"]}
    if winapi is not None and sys.platform == "win32":
        try:
            info["dpi"] = winapi.DPI_MODE
            info["screens"] = len(winapi.displays())
            info["virtual"] = list(winapi.virtual_screen())
            info["admin"] = winapi.is_admin()
            foreground = winapi.foreground_window() or {}
            info["foreground"] = foreground.get("title", "")
        except Exception as exc:
            info["error"] = str(exc)
    if uia is not None:
        info["worker"] = uia.status()
    return info


# ------------------------------------------------------------------ 屏幕与截图

def _int(text, fallback=0):
    try:
        return int(text)
    except (TypeError, ValueError):
        return fallback


def _image_path(path):
    """统一成绝对路径 + `.png` 后缀，顺手建目录。

    我们只有纯标准库的 PNG 编码器（没有 JPEG），所以不管调用方写什么后缀，
    内容都是 PNG、后缀也统一成 .png —— 免得出现「.jpg 其实是 PNG」这种坑。
    """
    text = str(path or "").strip()
    if not text:
        folder = os.path.join(tempfile.gettempdir(), "w7a-shots")
        text = os.path.join(folder, "screen-%s.png" % time.strftime("%Y%m%d-%H%M%S"))
    text = os.path.abspath(os.path.expanduser(text))
    if not text.lower().endswith(".png"):
        text = os.path.splitext(text)[0] + ".png"
    folder = os.path.dirname(text)
    if folder and not os.path.isdir(folder):
        os.makedirs(folder)
    return text


def _screen_box(region=None, display=None, monitor=0):
    """region / display / monitor 三个参数统一成一个屏幕框。

    返回 `((left, top, right, bottom), 显示器清单, 错误)`。
    region 既接受 [left, top, right, bottom]，也接受 [x, y, 宽, 高]。
    """
    if winapi is None:
        return None, [], "桌面底座没加载。"
    try:
        screens = winapi.displays()
        virtual = winapi.virtual_screen()
    except Exception as exc:
        return None, [], "枚举显示器失败：%s" % exc
    full = (int(virtual[0]), int(virtual[1]), int(virtual[0] + virtual[2]), int(virtual[1] + virtual[3]))
    if region:
        values = region
        if isinstance(region, str):
            try:
                values = json.loads(region)
            except ValueError:
                values = [part for part in region.replace("，", ",").split(",") if part.strip()]
        try:
            numbers = [int(float(item)) for item in (values or [])]
        except (TypeError, ValueError):
            return None, screens, "region 要形如 [left, top, right, bottom] 或 [x, y, 宽, 高]。"
        if len(numbers) != 4:
            return None, screens, "region 要 4 个数字。"
        left, top, third, fourth = numbers
        if third > left and fourth > top:
            return (left, top, third, fourth), screens, ""
        if third > 0 and fourth > 0:
            return (left, top, left + third, top + fourth), screens, ""
        return None, screens, "region 不合法：%r" % (region,)
    want = display if display not in (None, "") else monitor
    try:
        index = int(want or 0)
    except (TypeError, ValueError):
        return None, screens, "display 要给屏幕序号（1 开始），0 表示所有屏拼起来。"
    if index <= 0:
        return full, screens, ""
    if index > len(screens):
        return None, screens, "这台机器上只有 %d 块屏幕。" % len(screens)
    rect = screens[index - 1]["rect"]
    return (int(rect[0]), int(rect[1]), int(rect[2]), int(rect[3])), screens, ""


def _annotate(canvas, elements, box):
    """把可操作元素框出来、编号写在中心（编号 = label = 截图上的红字）。"""
    span_x = max(1.0, float(box[2] - box[0]))
    span_y = max(1.0, float(box[3] - box[1]))
    factor_x = canvas.width / span_x
    factor_y = canvas.height / span_y
    for element in elements:
        try:
            left = int((element["x"] - box[0]) * factor_x)
            top = int((element["y"] - box[1]) * factor_y)
            right = int((element["x"] + element["width"] - box[0]) * factor_x)
            bottom = int((element["y"] + element["height"] - box[1]) * factor_y)
            if right <= left or bottom <= top:
                continue
            canvas.draw_rect((left, top, right, bottom), (255, 40, 40), 1)
            label = str(element["label"])
            text_x = int((element["cx"] - box[0]) * factor_x) - 3 * len(label)
            text_y = int((element["cy"] - box[1]) * factor_y) - 4
            canvas.draw_text(text_x, text_y, label, (255, 255, 255), (200, 30, 30))
        except Exception:
            continue


def capture(path, monitor=0, image_format="png", scale=1.0, region=None, display=None,
            cursor=False, annotate=False, elements=None):
    """截一张图存下来，返回 info 字典。

    monitor=0 表示所有屏拼起来；1/2/… 表示第几块屏，并把该屏在虚拟桌面里的原点
    （`origin_x`/`origin_y`，多屏时左屏可能是负数）一起带回去，方便把图上的位置
    换算回屏幕坐标。`region` 优先于 `monitor`/`display`。
    """
    error = _guard(need_input=False)
    if error:
        return None, error
    if not str(path or "").strip():
        return None, "截图要给一个保存路径。"
    box, screens, error = _screen_box(region=region, display=display, monitor=monitor)
    if error:
        return None, error
    try:
        target = _image_path(path)
    except OSError as exc:
        return None, "建不了保存目录：%s" % exc
    started = time.time()
    try:
        canvas = winimage.Canvas.grab(region=box, scale=float(scale or 1.0), max_size=MAX_IMAGE)
    except Exception as exc:
        return None, "抓屏失败：%s" % exc
    if cursor:
        try:
            position = winapi.cursor_position()
            canvas.draw_cursor(position[0] - box[0], position[1] - box[1])
        except Exception:
            pass
    if annotate and elements:
        _annotate(canvas, elements, box)
    try:
        canvas.save_png(target)
    except (OSError, IOError) as exc:
        return None, "写图片失败：%s" % exc
    info = {"path": target, "width": canvas.width, "height": canvas.height,
            "bytes": os.path.getsize(target), "monitors": len(screens),
            "origin_x": box[0], "origin_y": box[1],
            "monitor": int(monitor or 0) or 1, "format": "png",
            "seconds": round(time.time() - started, 3), "scale": float(scale or 1.0)}
    return info, ""


def capture_window(path, window, scale=1.0):
    """只截某一个窗口（比全屏截图更适合看细节）。"""
    error = _guard(need_input=False)
    if error:
        return None, error
    try:
        hwnd, reason = winapi.resolve_window(window)
    except Exception as exc:
        return None, "找窗口失败：%s" % exc
    if hwnd is None:
        return None, reason or "没有给出窗口。"
    item = winapi.window_info(hwnd)
    title = item["title"] or str(window)
    if item["minimized"]:
        return None, "窗口「%s」是最小化的，截不到内容（先用 App(switch) 把它恢复）。" % title
    rect = item["rect"]
    if rect[2] <= rect[0] or rect[3] <= rect[1]:
        return None, "窗口「%s」的尺寸是 0，截不了。" % title
    info, error = capture(path, region=[int(rect[0]), int(rect[1]), int(rect[2]), int(rect[3])],
                          scale=scale)
    if error:
        return None, error
    info["window"] = item["title"]
    info["handle"] = item["handle"]
    info["text"] = "窗口「%s」截图: %s（%dx%d）" % (title, info["path"], info["width"], info["height"])
    return info, ""


def screenshot(save_path="", display=None, region=None, window="", scale=1.0, cursor=False):
    """只截图、不遍历界面元素：比 Snapshot 快很多（100 毫秒以内）。"""
    if window:
        return capture_window(save_path or _image_path(""), window, scale=scale)
    info, error = capture(save_path or _image_path(""), display=display, region=region,
                          scale=scale, cursor=cursor)
    if error:
        return None, error
    info["text"] = "截图: %s（%dx%d，%d 字节，origin=(%d,%d)）" % (
        info["path"], info["width"], info["height"], info["bytes"], info["origin_x"], info["origin_y"])
    return info, ""


# ------------------------------------------------------------------ 界面元素

_ACTION_MAP = {"edit": "fill", "textbox": "fill", "checkbox": "toggle", "combobox": "select",
               "radiobutton": "select", "slider": "slide", "spinner": "slide",
               "document": "scroll"}

_CTRL_NAMES = {"Button": "按钮", "Edit": "编辑框", "TextBox": "编辑框", "CheckBox": "复选框",
               "RadioButton": "单选", "ComboBox": "下拉框", "Slider": "滑杆", "Spinner": "微调",
               "Document": "文档", "ListItem": "列表项", "MenuItem": "菜单项", "Hyperlink": "链接",
               "SplitButton": "分裂按钮", "TabItem": "选项卡", "TreeItem": "树项",
               "DataItem": "数据项", "HeaderItem": "表头", "ScrollBar": "滚动条",
               "Text": "文本", "Image": "图片", "StatusBar": "状态栏", "Group": "分组",
               "Pane": "面板", "Custom": "自定义控件", "MenuBar": "菜单栏", "Tab": "选项卡组",
               "ToolBar": "工具栏", "Window": "窗口"}


def _action_for(ctrl):
    """控件类型 → 建议动作（和 Windows-MCP 的映射一致）。"""
    return _ACTION_MAP.get((ctrl or "").strip().casefold(), "click")


def _build_elements(rows, from_find=False, box=None):
    """把 UIA 的行变成带编号（label）的元素表。编号从 0 开始。

    顺序照 Windows-MCP：先交互元素（kind="i"），再可滚动元素（kind="s"）。
    离屏的、尺寸为 0 的（点不到）直接跳过，免得占号。
    """
    if from_find:
        groups = [("i", list(rows))]
    else:
        groups = [("i", [row for row in rows if row.get("kind") == "i"]),
                  ("s", [row for row in rows if row.get("kind") == "s"])]
    elements = []
    for kind, group in groups:
        for row in group:
            try:
                left = int(row.get("x") or 0)
                top = int(row.get("y") or 0)
                width = int(row.get("width") or 0)
                height = int(row.get("height") or 0)
            except (TypeError, ValueError):
                continue
            if width <= 0 or height <= 0:
                continue
            if str(row.get("offscreen") or "").strip().lower() in ("1", "true", "yes"):
                continue
            center_x = left + width // 2
            center_y = top + height // 2
            if box is not None:
                if not (box[0] <= center_x < box[2] and box[1] <= center_y < box[3]):
                    continue
            elements.append({
                "label": len(elements), "kind": kind,
                "name": row.get("name") or "", "ctrl": row.get("ctrl") or "",
                "aid": row.get("aid") or "", "path": row.get("path") or "",
                "hwnd": _int(row.get("hwnd"), 0), "pid": _int(row.get("pid"), 0),
                "window": row.get("window") or "",
                "x": left, "y": top, "width": width, "height": height,
                "cx": center_x, "cy": center_y,
                "enabled": str(row.get("enabled") or "1").strip().lower() not in ("0", "false", "no"),
                "value": row.get("value") or "", "shortcut": row.get("shortcut") or "",
                "toggle": row.get("toggle") or "",
                "action": _action_for(row.get("ctrl") or ""),
            })
    return elements


def _element_line(element):
    parts = ["[%d] (%d,%d) %s" % (element["label"], element["cx"], element["cy"],
                                  _CTRL_NAMES.get(element["ctrl"], element["ctrl"] or "控件"))]
    if element["name"]:
        parts.append('"%s"' % element["name"])
    parts.append("[%s]" % element["action"])
    if element["value"]:
        parts.append('值="%s"' % element["value"])
    if element["toggle"]:
        parts.append("勾选=%s" % element["toggle"])
    if element["shortcut"]:
        parts.append("快捷键=%s" % element["shortcut"])
    if not element["enabled"]:
        parts.append("(禁用)")
    return " ".join(parts)


def _element_by_label(label):
    """编号 → 上一次 Snapshot 里的元素；返回 (元素, 错误)。"""
    if not _STATE["elements"]:
        return None, "还没有看过屏幕：先调用 Snapshot 截一张图，再用它给的编号（label）。"
    try:
        index = int(label)
    except (TypeError, ValueError):
        return None, "label 要是整数（截图上的编号，从 0 开始）。"
    if index < 0 or index >= len(_STATE["elements"]):
        return None, ("编号 %d 不在上一次快照里（一共 %d 个，编号 0~%d）。重新 Snapshot 一次吧。"
                      % (index, len(_STATE["elements"]), len(_STATE["elements"]) - 1))
    return _STATE["elements"][index], ""


def forget():
    """忘掉上一次 Snapshot 的编号（界面变了很多、想重新编号时用）。"""
    _STATE.update({"stamp": 0.0, "window": "", "elements": [], "path": ""})
    return True


def _same_name(live, snapshot):
    """界面变了没有：拿活元素的 name 和快照里的比（快照的名字可能被截断）。"""
    left = (live or "").strip().casefold()
    right = (snapshot or "").strip().casefold()
    if not right:
        return True
    if not left:
        return False
    return left == right or left.startswith(right) or right.startswith(left)


def _parse_loc(loc):
    """把 loc 变成 (x, y)：接受 [x, y] / "x,y" / {"x":1,"y":2} / '[1, 2]'。"""
    value = loc
    if isinstance(value, str):
        text = value.strip()
        try:
            value = json.loads(text)
        except ValueError:
            parts = [part for part in text.replace("，", ",").split(",") if part.strip()]
            if len(parts) == 2:
                value = parts
            else:
                return None, "loc 要形如 [x, y]（也可以是 \"x,y\" 或 {\"x\":1,\"y\":2}）：%r" % (loc,)
    if isinstance(value, dict):
        try:
            return (int(float(value["x"])), int(float(value["y"]))), ""
        except (KeyError, TypeError, ValueError):
            return None, "loc 字典要有 x 和 y。"
    if isinstance(value, (list, tuple)) and len(value) == 2:
        try:
            return (int(float(value[0])), int(float(value[1]))), ""
        except (TypeError, ValueError):
            return None, "loc 的两个值要能当数字用：%r" % (loc,)
    return None, "loc 要形如 [x, y]：%r" % (loc,)


def _point(loc=None, label=None):
    """loc / label → 屏幕坐标，返回 (x, y, 说明, 错误)。

    label 的坐标会**按当前界面重新算一遍**（UIA 路径解析 + 名字比对）：算得出来就用
    新坐标（界面滚动过、移动过也对），算不出来或界面已经变了就退回快照里的坐标，
    并在说明里讲清楚 —— 不静默点错地方。
    """
    if label is None and loc is None:
        return 0, 0, "", "要给出 loc（屏幕坐标 [x, y]）或 label（上一次 Snapshot 的编号）之一。"
    if label is not None:
        element, error = _element_by_label(label)
        if error:
            return 0, 0, "", error
        x, y = element["cx"], element["cy"]
        note = ""
        if element["hwnd"] and element["path"] and element["kind"] in ("i", "s"):
            meta = None
            try:
                meta = uia.resolve(element["hwnd"], element["path"])
            except uia.UiaError:
                meta = None
            if meta and str(meta.get("ok")) == "1":
                live_name = meta.get("name") or ""
                if _same_name(live_name, element["name"]):
                    x, y = _int(meta.get("cx"), x), _int(meta.get("cy"), y)
                    note = "坐标按当前界面重新算过"
                else:
                    note = ("界面已经变了（那个位置现在是「%s」），用的还是上次快照的坐标"
                            % (live_name or "别的控件"))
            else:
                note = "那个元素已经找不到了，用的是上次快照的坐标"
        return int(x), int(y), note, ""
    point, error = _parse_loc(loc)
    if error:
        return 0, 0, "", error
    return point[0], point[1], "", ""


def _foreground_title():
    try:
        info = winapi.foreground_window() or {}
    except Exception:
        return ""
    return info.get("title") or ""


def snapshot(use_vision=True, use_annotation=True, use_ui_tree=True, window="desktop",
             find="", read_text=False, max_elements=MAX_ELEMENTS, max_depth=14,
             region=None, display=None, save_path="", scale=1.0, max_chars=4000):
    """看一眼屏幕：截一张图 + 列出能操作的元素（带编号）。

    返回的 info 里有：`text`（给模型看的文字）、`elements`（带编号的元素表）、
    `path`（截图路径）、`counts`（各类元素个数）、`truncated`（元素是否被截断）。
    把 `find` 填上就只列名字里含这个关键字的元素（不用整屏翻）。
    """
    error = _guard(need_input=False)
    if error:
        return None, error
    started = time.time()
    box, screens, error = _screen_box(region=region, display=display)
    if error:
        return None, error
    rows = []
    meta = {}
    truncated = False
    if use_ui_tree:
        try:
            if find:
                meta, rows = uia.find(str(find), window=window or "desktop", max_hits=40, timeout=60.0)
            else:
                meta, rows = uia.snapshot(window=window or "desktop",
                                          max_elements=max(1, _int(max_elements, MAX_ELEMENTS)),
                                          max_depth=max(1, _int(max_depth, 14)), timeout=90.0)
        except uia.UiaCancelled:
            return None, "已被停止"
        except uia.UiaError as exc:
            return None, "读取界面元素失败：%s" % exc
        truncated = str(meta.get("truncated") or "").strip() in ("1", "true")
    counts = {}
    for row in rows:
        kind = row.get("kind") or "?"
        counts[kind] = counts.get(kind, 0) + 1
    full = _screen_box()[0]
    elements = _build_elements(rows, from_find=bool(find), box=None if box == full else box)

    canvas = None
    path = ""
    if use_vision:
        try:
            canvas = winimage.Canvas.grab(region=box, scale=float(scale or 1.0), max_size=MAX_IMAGE)
        except Exception as exc:
            return None, "抓屏失败：%s" % exc
        if use_annotation and elements:
            _annotate(canvas, elements, box)
        path = _image_path(save_path)
        try:
            canvas.save_png(path)
        except (OSError, IOError) as exc:
            return None, "写图片失败：%s" % exc

    text_lines = _snapshot_text(box, screens, elements, counts, truncated, path, canvas,
                                window=window, find=find, use_ui_tree=use_ui_tree)
    if read_text and use_ui_tree:
        try:
            _text_meta, lines = uia.read(window=window or "desktop", max_chars=_int(max_chars, 4000),
                                         doc_text=False, timeout=40.0)
        except uia.UiaError as exc:
            lines = ["（读窗口文字失败：%s）" % exc]
        text_lines.append("窗口文字（最多 %d 字）:" % _int(max_chars, 4000))
        text_lines.extend("  " + line for line in lines[:80])

    info = {"path": path, "width": canvas.width if canvas else 0,
            "height": canvas.height if canvas else 0,
            "text": "\n".join(text_lines), "elements": elements, "counts": counts,
            "truncated": truncated, "window": window or "desktop", "find": find or "",
            "elapsed": round(time.time() - started, 3),
            "origin": [box[0], box[1]], "screens": len(screens),
            "displays": screens, "raw_rows": len(rows)}
    _STATE.update({"stamp": time.time(), "window": window or "desktop",
                   "elements": elements, "path": path})
    return info, ""


def _snapshot_text(box, screens, elements, counts, truncated, path, canvas,
                   window="", find="", use_ui_tree=True):
    """拼给模型看的文字（紧凑一点：只列能操作的，坐标是虚拟桌面物理像素）。"""
    lines = []
    try:
        width, height = winapi.screen_size()
        virtual = winapi.virtual_screen()
        lines.append("屏幕 %dx%d，虚拟桌面 (%d,%d)-(%d,%d)，%d 块显示器，DPI %s"
                     % (width, height, virtual[0], virtual[1], virtual[0] + virtual[2],
                        virtual[1] + virtual[3], len(screens), winapi.DPI_MODE))
    except Exception as exc:
        lines.append("（屏幕信息读不到：%s）" % exc)
    if box[2] - box[0] != 0 and canvas is not None:
        lines.append("这次看的是区域 (%d,%d)-(%d,%d)" % (box[0], box[1], box[2], box[3]))
    try:
        cursor = winapi.cursor_position()
        lines.append("光标 (%d,%d)" % cursor)
    except Exception:
        pass
    foreground = _foreground_title()
    if foreground:
        lines.append("前台窗口「%s」" % foreground)
    try:
        windows = winapi.list_windows(visible_only=True, titled_only=True)
    except Exception:
        windows = []
    if windows:
        lines.append("窗口清单（z 序，前台在前）:")
        for index, item in enumerate(windows[:12], 1):
            mark = " [前台]" if item.get("foreground") else ""
            lines.append("  %d. 「%s」handle=%s pid=%s %dx%d%s"
                         % (index, (item.get("title") or "")[:48], item.get("handle"),
                            item.get("pid"), item.get("width", 0), item.get("height", 0), mark))
        if len(windows) > 12:
            lines.append("  …还有 %d 个窗口" % (len(windows) - 12))
    if find:
        lines.append("按关键字「%s」找到 %d 个能操作的元素（编号 = label）:" % (find, len(elements)))
    elif use_ui_tree:
        lines.append("可操作元素 %d 个（编号 = 截图上的红字 = label）:" % len(elements))
    for element in elements:
        lines.append("  " + _element_line(element))
    if use_ui_tree and not elements:
        lines.append("  没有找到能操作的元素（界面可能没在前台，或者需要加 window= 指定窗口）。")
    if truncated:
        lines.append("（元素太多被截断了：加 window= 或 find= 缩小范围，或者调大 max_elements。）")
    if path:
        lines.append("截图: %s" % path)
        lines.append("（图上的位置 = 虚拟桌面物理像素；编号从 0 开始。）")
    lines.append("用法: Click(label=编号) / Type(label=编号, text=\"…\") / "
                 "Scroll(label=编号, direction=\"down\")；图上没有的坐标可以直接给 loc=[x, y]。")
    return lines


# ------------------------------------------------------------------ 鼠标键盘

def click(loc=None, label=None, button="left", clicks=1):
    """点一下（`label` 用 Snapshot 的编号，`loc` 用屏幕坐标；clicks=0 只把鼠标移过去）。"""
    error = _input_guard()
    if error:
        return None, error
    x, y, note, error = _point(loc, label)
    if error:
        return None, error
    try:
        count = int(clicks)
    except (TypeError, ValueError):
        return None, "clicks 要是整数（0 表示只把鼠标移过去）。"
    if count > 3:
        count = 3
    try:
        winapi.click(x, y, button=button, clicks=count)
    except winapi.WinApiError as exc:
        return None, "点击失败：%s" % exc
    target = ""
    if label is not None:
        element, _err = _element_by_label(label)
        if element:
            target = element["name"]
    info = {"action": "hover" if count <= 0 else "click", "x": x, "y": y,
            "button": button, "clicks": max(0, count), "target": target}
    if note:
        info["note"] = note
    return info, ""


def type_text(text, loc=None, label=None, clear=False, caret_position="idle", press_enter=False):
    """把文字打进当前焦点（给了 loc/label 就先点一下那个元素）。

    `clear=True` 先 Ctrl+A 再退格（清空原内容）；`caret_position` 是 start/idle/end
    （先把光标挪到开头/不动/末尾）；`press_enter=True` 打完回车（发消息常用）。
    """
    error = _input_guard()
    if error:
        return None, error
    caret = str(caret_position or "idle").strip().lower()
    if caret not in ("start", "idle", "end"):
        return None, 'caret_position 只能是 "start" / "idle" / "end"。'
    data = "" if text is None else str(text)
    if not data and not clear:
        return None, "要输入的文本是空的。"
    x = y = None
    note = ""
    target = ""
    if loc is not None or label is not None:
        x, y, note, error = _point(loc, label)
        if error:
            return None, error
        if label is not None:
            element, _err = _element_by_label(label)
            if element:
                target = element["name"]
    sent = 0
    try:
        if x is not None:
            winapi.click(x, y)
            time.sleep(0.05)
        if caret == "start":
            winapi.press_keys("home")
        elif caret == "end":
            winapi.press_keys("end")
        if clear:
            winapi.press_keys("ctrl+a")
            time.sleep(0.05)
            winapi.press_keys("backspace")
            time.sleep(0.05)
        sent = winapi.type_text(data)
        if press_enter:
            time.sleep(0.05)
            winapi.press_keys("enter")
    except winapi.WinApiError as exc:
        return None, "输入失败：%s" % exc
    info = {"action": "type", "chars": len(data), "cleared": bool(clear),
            "pressed_enter": bool(press_enter), "caret": caret, "keys_sent": sent, "target": target}
    if x is not None:
        info["x"] = x
        info["y"] = y
    if note:
        info["note"] = note
    return info, ""


def scroll(loc=None, label=None, type="vertical", direction="down", wheel_times=1):
    """滚轮（type=vertical/horizontal，direction=up/down/left/right）。

    没给 loc/label 就在**光标当前位置**滚（一般是当前焦点窗口里）。
    """
    error = _input_guard()
    if error:
        return None, error
    kind = str(type or "vertical").strip().lower()
    if kind in ("v", "vert", "vertical"):
        horizontal = False
    elif kind in ("h", "horz", "horizontal"):
        horizontal = True
    else:
        return None, 'type 只能是 "vertical" 或 "horizontal"。'
    note = ""
    target = ""
    if loc is None and label is None:
        try:
            x, y = winapi.cursor_position()
        except winapi.WinApiError as exc:
            return None, "读光标位置失败：%s" % exc
        note = "没有指定位置，用的是光标当前位置"
    else:
        x, y, note, error = _point(loc, label)
        if error:
            return None, error
        if label is not None:
            element, _err = _element_by_label(label)
            if element:
                target = element["name"]
    try:
        times = int(wheel_times or 1)
    except (TypeError, ValueError):
        return None, "wheel_times 要是整数。"
    try:
        winapi.scroll(x, y, direction=str(direction or "down").strip().lower(),
                      wheel_times=times, horizontal=horizontal)
    except winapi.WinApiError as exc:
        return None, "滚动失败：%s" % exc
    info = {"action": "scroll", "type": kind, "direction": direction, "times": times,
            "x": x, "y": y, "target": target}
    if note:
        info["note"] = note
    return info, ""


def move(loc=None, label=None, drag=False, from_loc=None, duration=None):
    """移动鼠标；`drag=True` 就从 from_loc（不给就用光标当前位置）拖到目标。"""
    error = _input_guard()
    if error:
        return None, error
    x, y, note, error = _point(loc, label)
    if error:
        return None, error
    seconds = None
    if duration not in (None, ""):
        try:
            seconds = max(0.0, min(10.0, float(duration)))
        except (TypeError, ValueError):
            return None, "duration 要是秒数（0~10）。"
    try:
        if drag:
            if from_loc is None:
                start = winapi.cursor_position()
            else:
                start, error = _parse_loc(from_loc)
                if error:
                    return None, error
            winapi.drag(start[0], start[1], x, y, duration=seconds)
            info = {"action": "drag", "from": [start[0], start[1]], "x": x, "y": y}
        else:
            winapi.move_mouse(x, y, duration=seconds)
            info = {"action": "move", "x": x, "y": y}
    except winapi.WinApiError as exc:
        return None, "移动鼠标失败：%s" % exc
    if note:
        info["note"] = note
    return info, ""


def shortcut(shortcut):
    """按快捷键："ctrl+c" / "alt+tab" / "win+r" / "f5" / "enter"，
    多媒体键直接写名字（play_pause、next、prev、stop、volume_up、volume_down、mute）。"""
    error = _input_guard()
    if error:
        return None, error
    combo = str(shortcut or "").strip()
    if not combo:
        return None, "没有给出按键。"
    try:
        winapi.press_keys(combo)
    except winapi.WinApiError as exc:
        return None, ("按键失败：%s（可用：ctrl+c 这种组合、f1~f12、enter/esc/tab/空格/方向键，"
                      "多媒体键 play_pause / next / prev / stop / volume_up / volume_down / mute）"
                      % exc)
    return {"action": "shortcut", "keys": combo}, ""


def wait(duration=1.0):
    """等一会儿（秒，最多 120）。等待期间响应「停止」。"""
    try:
        seconds = float(duration)
    except (TypeError, ValueError):
        return None, "duration 要是秒数。"
    seconds = max(0.0, min(WAIT_MAX, seconds))
    deadline = time.time() + seconds
    while True:
        if _cancelled():
            return None, "已被停止"
        left = deadline - time.time()
        if left <= 0:
            break
        time.sleep(min(0.1, left))
    return {"action": "wait", "seconds": round(seconds, 3)}, ""


def wait_for(condition, text=None, window_name=None, timeout=10.0, interval=0.25):
    """等一件事发生。

    condition 四种：
      - "window"  等 window_name（或 text）说的窗口出现
      - "gone"    等它消失
      - "text"    等某个窗口里出现 text 这段文字（默认看前台窗口）
      - "stable"  等界面连着两次采样一样（加载完了）
    等到返回 matched=True；超时返回 matched=False 并给一句错误说明。
    """
    error = _guard(need_input=False)
    if error:
        return None, error
    kind = str(condition or "").strip().lower()
    if kind not in ("window", "gone", "text", "stable"):
        return None, ('condition 只能是 "window"（等窗口出现）/ "gone"（等窗口消失）/ '
                      '"text"（等出现文字）/ "stable"（等内容不再变）。')
    try:
        limit = max(0.5, min(WAIT_MAX, float(timeout)))
        step = max(0.1, min(5.0, float(interval)))
    except (TypeError, ValueError):
        return None, "timeout / interval 要是秒数。"
    started = time.time()
    deadline = started + limit
    matched = False
    detail = ""
    previous = None
    while True:
        if _cancelled():
            return None, "已被停止"
        if kind in ("window", "gone"):
            keyword = str(window_name or text or "").strip()
            if not keyword:
                return None, 'condition="%s" 时要给 window_name（窗口标题里的一段）。' % kind
            try:
                hits = winapi.find_windows(keyword)
            except Exception as exc:
                return None, "找窗口失败：%s" % exc
            matched = bool(hits) if kind == "window" else not hits
            detail = ("找到 %d 个窗口" % len(hits)) if hits else "窗口不在"
        elif kind == "text":
            needle = str(text or "")
            if not needle:
                return None, 'condition="text" 时要给 text（要找的文字）。'
            scope = str(window_name or "").strip() or _foreground_title()
            if not scope:
                detail = "现在没有前台窗口"
            else:
                try:
                    _meta, lines = uia.read(window=scope, max_chars=4000, doc_text=False, timeout=20.0)
                    body = "\n".join(lines)
                    matched = needle in body
                    detail = "文字出现了" if matched else "还没出现"
                except uia.UiaError as exc:
                    detail = "读窗口文字失败：%s" % exc
        else:
            scope = str(window_name or "").strip() or _foreground_title()
            try:
                meta, rows = uia.snapshot(window=scope or "desktop", max_elements=200,
                                          max_depth=8, timeout=20.0)
                now = "%s|%d|%s" % (scope, len(rows), meta.get("truncated"))
            except uia.UiaError as exc:
                now = "err:%s" % exc
            if previous is not None and now == previous:
                matched = True
                detail = "界面连着两次一样，算稳定了"
            else:
                detail = "界面还在变"
            previous = now
        if matched or time.time() >= deadline:
            break
        time.sleep(min(step, max(0.0, deadline - time.time())))
    info = {"action": "wait_for", "condition": kind, "matched": matched,
            "seconds": round(time.time() - started, 2), "detail": detail}
    if not matched:
        return info, "等了 %.1f 秒也没等到（%s：%s）。" % (info["seconds"], kind, detail)
    return info, ""


# ------------------------------------------------------------------ 窗口与程序

def _process_name(pid):
    global _PROCESS_NAMES
    if not _PROCESS_NAMES:
        try:
            for item in winapi.list_processes(sort_by="pid", limit=0):
                _PROCESS_NAMES[int(item["pid"])] = item["name"]
        except Exception:
            _PROCESS_NAMES = {}
    return _PROCESS_NAMES.get(int(pid), "")


def list_windows(limit=0):
    """可见、有标题的顶层窗口（按 z 序，前台在前），返回 (列表, 错误)。"""
    error = _guard(need_input=False)
    if error:
        return [], error
    try:
        items = winapi.list_windows(visible_only=True, titled_only=True)
    except Exception as exc:
        return [], "枚举窗口失败：%s" % exc
    result = []
    for item in items:
        result.append({"handle": item["handle"], "title": item["title"],
                       "process": _process_name(item["pid"]), "pid": item["pid"],
                       "rect": list(item["rect"]), "x": item["rect"][0], "y": item["rect"][1],
                       "width": item["width"], "height": item["height"],
                       "minimized": item["minimized"], "maximized": item["maximized"],
                       "foreground": item["foreground"]})
    if limit:
        result = result[:int(limit)]
    return result, ""


def _resolve(hwnd_or_name):
    """窗口标识 → hwnd；空/桌面/全屏就取前台窗口。返回 (hwnd, 错误)。"""
    text = str(hwnd_or_name or "").strip()
    if not text or text.lower() in ("desktop", "桌面", "全屏", "foreground"):
        foreground = winapi.foreground_window()
        handle = foreground.get("handle") if foreground else None
        if not handle:
            return None, "现在没有前台窗口。"
        return handle, ""
    if text.lower().startswith("handle:"):
        text = text.split(":", 1)[1].strip()
    if text.isdigit():
        handle = int(text)
        if winapi.window_info(handle)["handle"]:
            return handle, ""
    return winapi.resolve_window(hwnd_or_name)


def window_action(window, action, x=None, y=None, width=None, height=None):
    """对一个窗口做事：focus（切到前台）/ close / minimize / maximize / restore /
    hide / show / move / resize / info。"""
    error = _guard(need_input=False)
    if error:
        return None, error
    try:
        hwnd, reason = _resolve(window)
    except Exception as exc:
        return None, "找窗口失败：%s" % exc
    if hwnd is None:
        return None, reason or "没有找到窗口。"
    kind = str(action or "").strip().lower()
    try:
        if kind in ("focus", "front", "activate", "switch"):
            ok, message = winapi.bring_to_front(hwnd)
            if not ok:
                return None, message
        elif kind == "info":
            return winapi.window_info(hwnd), ""
        elif kind == "close":
            if not winapi.close_window(hwnd):
                return None, "关窗口的请求没发出去。"
        elif kind in ("normal", "minimize", "minimized", "maximize", "maximized",
                      "restore", "hide", "show"):
            ok, message = winapi.show_window(hwnd, kind)
            if not ok:
                return None, message
        elif kind in ("move", "resize"):
            current = winapi.window_rect(hwnd)
            left = current[0] if x is None else int(x)
            top = current[1] if y is None else int(y)
            wide = (current[2] - current[0]) if width is None else int(width)
            high = (current[3] - current[1]) if height is None else int(height)
            ok, message = winapi.move_window(hwnd, left, top, wide, high)
            if not ok:
                return None, message
        else:
            return None, "不认识的窗口动作：%s" % action
    except winapi.WinApiError as exc:
        return None, "窗口操作失败：%s" % exc
    item = winapi.window_info(hwnd)
    return {"action": kind, "window": item["title"], "handle": item["handle"],
            "pid": item["pid"], "rect": list(item["rect"]),
            "foreground": item["foreground"]}, ""


def _wait_window_for_pid(pid, timeout=6.0):
    """等某个 pid 的窗口出现（启动程序之后用）。"""
    deadline = time.time() + max(0.0, float(timeout))
    while True:
        if _cancelled():
            return None
        try:
            for item in winapi.list_windows(visible_only=True, titled_only=True):
                if item["pid"] == pid:
                    return item
        except Exception:
            pass
        if time.time() >= deadline:
            return None
        time.sleep(0.25)


def _find_app(name):
    """在开始菜单里找程序：精确 → 前缀 → 含关键字。返回 (lnk 路径, 最接近的几个名字)。"""
    keyword = str(name or "").strip().casefold()
    if not keyword:
        return "", []
    try:
        apps = winapi.start_menu_apps()
    except Exception:
        return "", []
    exact = [key for key in apps if key.casefold() == keyword]
    starts = [key for key in apps if key.casefold().startswith(keyword) and key not in exact]
    loose = [key for key in apps
             if keyword in key.casefold() and key not in exact and key not in starts]
    order = exact + starts + loose
    if not order:
        return "", []
    return apps[order[0]], order[:8]


def app(mode="launch", name=None, window_loc=None, window_size=None, executable=None,
        args=None, cwd=None):
    """程序与窗口的总入口。

    mode:
      - "launch"             按名字启动开始菜单里的程序（name）
      - "launch_executable"  起一个 exe（executable，可带 args / cwd）
      - "switch"             把窗口切到前台（name 是标题里的一段）
      - "resize" / "move"    配合 window_loc=[x,y]、window_size=[宽,高]
      - "close" / "minimize" / "maximize" / "restore" / "hide" / "show"
    """
    error = _guard(need_input=False)
    if error:
        return None, error
    action = str(mode or "launch").strip().lower()
    if action in ("launch", "open", "start"):
        keyword = str(name or executable or "").strip()
        if not keyword:
            return None, "启动程序要给出 name（开始菜单里的名字）或 executable（exe 全路径）。"
        link, near = _find_app(keyword)
        if not link:
            try:
                pid = winapi.shell_execute(keyword)
            except winapi.WinApiError as exc:
                hint = ("；开始菜单里最接近的是：" + "、".join(near)) if near else ""
                return None, "没找到叫「%s」的程序：%s%s" % (keyword, exc, hint)
            window = _wait_window_for_pid(pid, 6.0) if pid else None
            if window is None:
                hits = winapi.find_windows(keyword)
                window = hits[0] if hits else None
            info = {"mode": "launch", "launched": keyword, "pid": pid or 0,
                    "window": (window or {}).get("title", ""),
                    "handle": (window or {}).get("handle", 0)}
            if window is None:
                info["note"] = "程序好像起来了，但 6 秒内没看到窗口；可以再 Snapshot 看一眼。"
            return info, ""
        try:
            pid = winapi.shell_execute(link)
        except winapi.WinApiError as exc:
            return None, "启动 %s 失败：%s" % (link, exc)
        window = _wait_window_for_pid(pid, 6.0) if pid else None
        if window is None:
            hits = winapi.find_windows(keyword)
            window = hits[0] if hits else None
        info = {"mode": "launch", "launched": os.path.basename(link), "path": link,
                "pid": pid or 0, "window": (window or {}).get("title", ""),
                "handle": (window or {}).get("handle", 0)}
        if window is None:
            info["note"] = "程序好像起来了，但 6 秒内没看到窗口；可以再 Snapshot 看一眼。"
        return info, ""
    if action in ("launch_executable", "exec", "run"):
        target = str(executable or name or "").strip()
        if not target:
            return None, "launch_executable 要给出 executable（exe 全路径）。"
        try:
            pid, _note = winapi.launch_executable(target, args=args, cwd=cwd)
        except (winapi.WinApiError, OSError) as exc:
            return None, "启动失败：%s" % exc
        window = _wait_window_for_pid(pid, 6.0)
        info = {"mode": "launch_executable", "launched": target, "pid": pid,
                "window": (window or {}).get("title", ""),
                "handle": (window or {}).get("handle", 0)}
        if window is None:
            info["note"] = "进程起来了，但 6 秒内没看到窗口；可以再 Snapshot 看一眼。"
        return info, ""
    sub = {"switch": "focus", "focus": "focus", "front": "focus", "activate": "focus",
           "resize": "resize", "move": "move", "close": "close", "minimize": "minimize",
           "maximize": "maximize", "restore": "restore", "hide": "hide", "show": "show"}.get(action)
    if sub is None:
        return None, ('不认识的 mode：%s（可用 launch / launch_executable / switch / resize / '
                      'move / close / minimize / maximize / restore / hide / show）。' % mode)
    left = top = wide = high = None
    if window_loc:
        point, error = _parse_loc(window_loc)
        if error:
            return None, "window_loc 要是 [x, y]：%s" % error
        left, top = point
    if window_size:
        if isinstance(window_size, str):
            try:
                window_size = json.loads(window_size)
            except ValueError:
                window_size = [part for part in window_size.replace("，", ",").split(",") if part.strip()]
        try:
            wide, high = int(float(window_size[0])), int(float(window_size[1]))
        except (TypeError, ValueError, IndexError):
            return None, "window_size 要是 [宽, 高]。"
    info, error = window_action(name or "", sub, x=left, y=top, width=wide, height=high)
    if error:
        return None, error
    info = dict(info)
    info["mode"] = action
    return info, ""


# ------------------------------------------------------------------ 系统

def clipboard(mode="get", text=None):
    """读/写剪贴板：mode="get" 读，"set" 写（要给 text）。

    读是只读的（任何时候都行）；**写会覆盖老师自己复制的东西**，所以跟键鼠一样
    受 `W7A_NO_INPUT=1` / `input_blocked()` 限制（自检与客户端里默认禁掉输入时，
    写剪贴板也必须被拦住）。
    """
    kind = str(mode or "get").strip().lower()
    error = _input_guard() if kind in ("set", "write", "copy") else _guard(need_input=False)
    if error:
        return None, error
    if kind in ("get", "read"):
        try:
            value = winapi.clipboard_get_text()
        except winapi.WinApiError as exc:
            return None, "读剪贴板失败：%s" % exc
        value = value or ""
        return {"mode": "get", "chars": len(value), "text": value}, ""
    if kind in ("set", "write", "copy"):
        if text is None:
            return None, "写剪贴板要给出 text。"
        try:
            winapi.clipboard_set_text(str(text))
        except winapi.WinApiError as exc:
            return None, "写剪贴板失败：%s" % exc
        return {"mode": "set", "chars": len(str(text))}, ""
    return None, 'mode 只能是 "get" 或 "set"。'


def display_inventory():
    """显示器清单（几块屏、各自的位置/分辨率/工作区、DPI、光标在哪）。"""
    error = _guard(need_input=False)
    if error:
        return None, error
    try:
        screens = winapi.displays()
        virtual = winapi.virtual_screen()
        primary = winapi.screen_size()
        foreground = winapi.foreground_window() or {}
    except Exception as exc:
        return None, "读显示器信息失败：%s" % exc
    info = {"dpi": winapi.DPI_MODE, "count": len(screens), "primary": list(primary),
            "virtual": {"x": virtual[0], "y": virtual[1], "width": virtual[2], "height": virtual[3]},
            "screens": [{"index": item["index"] + 1, "device": item["device"],
                         "primary": item["primary"], "rect": list(item["rect"]),
                         "work_rect": list(item["work_rect"]),
                         "width": item["width"], "height": item["height"]} for item in screens],
            "foreground": {"title": foreground.get("title", ""), "handle": foreground.get("handle", 0)},
            "admin": winapi.is_admin(), "input_blocked": input_blocked()}
    lines = ["%d 块显示器，DPI %s，主屏 %dx%d，虚拟桌面 (%d,%d) %dx%d"
             % (info["count"], info["dpi"], primary[0], primary[1],
                virtual[0], virtual[1], virtual[2], virtual[3])]
    for item in info["screens"]:
        lines.append("  屏 %d：%s%s 位置 (%d,%d) %dx%d，可用区 (%d,%d) %dx%d"
                     % (item["index"], item["device"], "（主屏）" if item["primary"] else "",
                        item["rect"][0], item["rect"][1], item["width"], item["height"],
                        item["work_rect"][0], item["work_rect"][1],
                        item["work_rect"][2] - item["work_rect"][0],
                        item["work_rect"][3] - item["work_rect"][1]))
    try:
        lines.append("光标 (%d,%d)" % winapi.cursor_position())
    except Exception:
        pass
    if info["foreground"]["title"]:
        lines.append("前台窗口「%s」" % info["foreground"]["title"])
    info["text"] = "\n".join(lines)
    return info, ""


def process(mode="list", name=None, pid=None, sort_by="memory", limit=20, force=False):
    """进程：mode="list" 按内存/pid/名字排前列出，mode="kill" 按 pid 或名字结束进程。"""
    error = _guard(need_input=False)
    if error:
        return None, error
    kind = str(mode or "list").strip().lower()
    if kind in ("list", "info"):
        try:
            items = winapi.list_processes(sort_by=str(sort_by or "memory").strip().lower(),
                                          limit=_int(limit, 0))
        except winapi.WinApiError as exc:
            return None, "读进程列表失败：%s" % exc
        keyword = str(name or "").strip().casefold()
        if keyword:
            items = [item for item in items if keyword in (item["name"] or "").casefold()]
        return {"mode": "list", "count": len(items), "processes": items}, ""
    if kind in ("kill", "stop"):
        targets = []
        if pid not in (None, ""):
            try:
                targets = [int(pid)]
            except (TypeError, ValueError):
                return None, "pid 要是整数。"
        elif name:
            keyword = str(name).strip().casefold()
            try:
                items = winapi.list_processes(sort_by="name", limit=0)
            except winapi.WinApiError as exc:
                return None, "读进程列表失败：%s" % exc
            targets = [item["pid"] for item in items if keyword in (item["name"] or "").casefold()]
            if not targets:
                return None, "没有找到名字里带「%s」的进程。" % name
        else:
            return None, "结束进程要给出 pid 或 name。"
        killed = []
        for target in targets:
            ok, message = winapi.kill_process(target, force=bool(force))
            killed.append({"pid": target, "ok": bool(ok), "error": "" if ok else message})
        failed = [item for item in killed if not item["ok"]]
        if failed and len(failed) == len(killed):
            return None, "没能结束进程：%s" % "；".join(item["error"] for item in failed if item["error"])
        return {"mode": "kill", "killed": killed}, ""
    return None, 'mode 只能是 "list" 或 "kill"。'


def _jsonable(value):
    if isinstance(value, bytes):
        return "0x" + value.hex()
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    return value


def registry(mode="get", path="", name=None, value=None, value_type=None):
    """注册表：mode="get" 读一个值，"list" 列全部值，"set" 写，"delete" 删。

    路径要以 HKEY_CURRENT_USER / HKCU / HKEY_LOCAL_MACHINE / HKLM 开头。
    """
    error = _guard(need_input=False)
    if error:
        return None, error
    path = str(path or "").strip()
    if not path:
        return None, "要给出注册表路径（例：HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run）。"
    kind = str(mode or "get").strip().lower()
    try:
        if kind == "get":
            result = winapi.registry_get(path, name)
            return {"mode": "get", "path": path, "name": name or "(默认)",
                    "value": _jsonable(result)}, ""
        if kind in ("list", "values"):
            result = winapi.registry_list(path)
            return {"mode": "list", "path": path, "count": len(result),
                    "values": {key: _jsonable(item) for key, item in result.items()}}, ""
        if kind in ("set", "write"):
            if value is None:
                return None, "写注册表要给出 value。"
            winapi.registry_set(path, name, value, value_type=str(value_type or "string").strip().lower())
            return {"mode": "set", "path": path, "name": name or "(默认)",
                    "value": _jsonable(value)}, ""
        if kind in ("delete", "del", "remove"):
            winapi.registry_delete(path, name)
            return {"mode": "delete", "path": path, "name": name or "(默认)"}, ""
    except winapi.WinApiError as exc:
        return None, str(exc)
    return None, 'mode 只能是 "get" / "set" / "delete" / "list"。'


# ------------------------------------------------------------------ 组合动作

def _collect_points(locs=None, labels=None):
    """[坐标列表] 或 [编号列表] → [(x, y, 名字)]；返回 (列表, 错误)。"""
    targets = []
    if labels:
        for item in labels:
            label = item[0] if isinstance(item, (list, tuple)) else item
            element, error = _element_by_label(label)
            if error:
                return [], error
            targets.append((element["cx"], element["cy"], element["name"]))
    if locs:
        for item in locs:
            point, error = _parse_loc(item)
            if error:
                return [], error
            targets.append((point[0], point[1], ""))
    if not targets:
        return [], "要给出 locs（坐标列表）或 labels（编号列表）。"
    return targets, ""


def multi_select(locs=None, labels=None, press_ctrl=True):
    """按住 Ctrl 依次点多个元素（列表里多选）。"""
    error = _input_guard()
    if error:
        return None, error
    points, error = _collect_points(locs, labels)
    if error:
        return None, error
    items = []
    try:
        if press_ctrl:
            winapi.key_down("ctrl")
        try:
            for x, y, name in points:
                if _cancelled():
                    return None, "已被停止"
                winapi.click(x, y)
                items.append({"x": x, "y": y, "target": name})
                time.sleep(0.08)
        finally:
            if press_ctrl:
                winapi.key_up("ctrl")
    except winapi.WinApiError as exc:
        return None, "多选失败：%s" % exc
    return {"action": "multi_select", "count": len(items), "ctrl": bool(press_ctrl),
            "items": items}, ""


def multi_edit(locs=None, labels=None):
    """一次点好几个地方各输入一段字：locs=[[x, y, 文本], …] 或 labels=[[编号, 文本], …]。"""
    error = _input_guard()
    if error:
        return None, error
    plan = []
    if labels:
        for item in labels:
            if not isinstance(item, (list, tuple)) or len(item) < 2:
                return None, "labels 每项要形如 [编号, 文本]。"
            plan.append(("label", item[0], item[1]))
    if locs:
        for item in locs:
            if not isinstance(item, (list, tuple)) or len(item) < 3:
                return None, "locs 每项要形如 [x, y, 文本]。"
            plan.append(("loc", (item[0], item[1]), item[2]))
    if not plan:
        return None, "要给出 locs（[[x,y,文本], …]）或 labels（[[编号,文本], …]）。"
    done = []
    for kind, where, text in plan:
        if _cancelled():
            return None, "已被停止"
        if kind == "loc":
            x, y, note, error = _point(loc=where)
        else:
            x, y, note, error = _point(label=where)
        if error:
            return None, error
        try:
            winapi.click(x, y)
            time.sleep(0.05)
            winapi.type_text(str(text))
        except winapi.WinApiError as exc:
            return None, "第 %d 项失败：%s" % (len(done) + 1, exc)
        item = {"x": x, "y": y, "text": str(text)}
        if note:
            item["note"] = note
        done.append(item)
        time.sleep(0.05)
    return {"action": "multi_edit", "count": len(done), "items": done}, ""


def _clean_strokes(strokes):
    """笔画数据 → [[(x, y), …], …]。"""
    if isinstance(strokes, dict):
        strokes = strokes.get("strokes") or strokes.get("points") or []
    if not isinstance(strokes, (list, tuple)) or not strokes:
        raise ValueError("strokes 要是「一笔一笔」的列表，例如 [[[100,100],[200,200]]]。")
    cleaned = []
    for stroke in strokes:
        if isinstance(stroke, dict):
            stroke = stroke.get("points") or []
        if not isinstance(stroke, (list, tuple)) or not stroke:
            continue
        points = []
        for point in stroke:
            if isinstance(point, dict):
                point = [point.get("x"), point.get("y")]
            if not isinstance(point, (list, tuple)) or len(point) < 2:
                continue
            try:
                points.append((int(float(point[0])), int(float(point[1]))))
            except (TypeError, ValueError):
                continue
        if points:
            cleaned.append(points)
    if not cleaned:
        raise ValueError("strokes 里没有一个能用的点。")
    return cleaned


def draw_path(strokes, duration=0.45, button="left"):
    """按「一笔一笔」的点连笔画图（AI 画图会用到；项目特有的工具）。

    strokes = [[[x,y],[x,y],…], [[x,y],…]]。**每一笔落笔之前先移回本笔起点**：
    否则笔会从上一笔的末尾直接连到下一笔的开头，画出多余的线（老师提过的老问题）。
    """
    error = _input_guard()
    if error:
        return None, error
    try:
        stroke_list = _clean_strokes(strokes)
    except ValueError as exc:
        return None, str(exc)
    try:
        seconds = max(0.05, min(5.0, float(duration or 0.45)))
    except (TypeError, ValueError):
        return None, "duration 要是秒数。"
    step = max(2.0, seconds * 1000.0 / 25.0) / 1000.0
    started = time.time()
    points = 0
    try:
        for index, stroke in enumerate(stroke_list):
            if _cancelled():
                return None, "已被停止"
            start = stroke[0]
            if index:
                winapi.move_mouse(start[0], start[1])
                time.sleep(0.03)
            winapi.mouse_down(button, start[0], start[1])
            time.sleep(0.02)
            for point in stroke:
                if _cancelled():
                    winapi.mouse_up(button)
                    return None, "已被停止"
                winapi.move_mouse(point[0], point[1])
                time.sleep(step)
                points += 1
            winapi.mouse_up(button)
            time.sleep(0.02)
    except winapi.WinApiError as exc:
        try:
            winapi.mouse_up(button)
        except Exception:
            pass
        return None, "画图失败：%s" % exc
    return {"action": "draw_path", "strokes": len(stroke_list), "points": points,
            "seconds": round(time.time() - started, 2), "button": button}, ""


def send_qq_message(text, contact="", send_shortcut="enter", capture_path=""):
    """用 QQ 客户端发一条消息（模拟操作），发完截一张图，让老师/模型自己确认有没有发出去。

    诚实说明：这不是 QQ 官方接口，靠的是「切到 QQ 窗口 → 输入 → 回车」。
    所以它要求：QQ 已经登录并开着；目标聊天窗口已经打开（或者能给 contact 关键字去搜）。
    QQ 里如果设置了「按 Ctrl+Enter 发送」，就把 send_shortcut 传成 ctrl+enter。
    """
    if not text or not str(text).strip():
        return None, "要发的消息是空的"
    ok, message = available()
    if not ok:
        return None, message
    error = _input_guard()
    if error:
        return None, error
    if send_shortcut not in ("enter", "ctrl+enter"):
        return None, "send_shortcut 只能是 enter 或 ctrl+enter"
    windows, error = list_windows(limit=60)
    if error:
        return None, error
    target = ""
    for item in windows:
        process_name = (item.get("process") or "").lower()
        if process_name in ("qq.exe", "qq", "tim.exe", "tim") or "QQ" in (item.get("title") or ""):
            target = item["title"] or item["process"]
            break
    if not target:
        return None, ("没有找到 QQ 窗口：请先打开 QQ 并登录；如果用的是 TIM，也可以先用 "
                      "App(switch) 把聊天窗口切到前台，再用 Type + Shortcut 发消息。")
    _info, error = window_action(target, "focus")
    if error:
        return None, error
    steps = ["已切到窗口「%s」" % target]
    searched = False
    try:
        if contact:
            # 在 QQ 里 Ctrl+F 是「查找」，能直接搜联系人/群；搜不到也不致命，后面截图能看出来
            winapi.press_keys("ctrl+f")
            time.sleep(0.3)
            winapi.type_text(str(contact))
            time.sleep(0.3)
            winapi.press_keys("enter")
            time.sleep(0.4)
            searched = True
            steps.append("已尝试搜索联系人「%s」" % contact)
        winapi.type_text(str(text))
        time.sleep(0.05)
        if send_shortcut == "ctrl+enter":
            winapi.press_keys("ctrl+enter")
        else:
            winapi.press_keys("enter")
    except winapi.WinApiError as exc:
        return None, "发消息时出错：%s" % exc
    steps.append("已输入并发送 %d 个字" % len(str(text)))
    try:
        shot, shot_error = capture(capture_path or _image_path(""), scale=0.75)
    except OSError as exc:
        shot, shot_error = None, str(exc)
    result = {"window": target, "contact": str(contact or ""), "chars": len(str(text)),
              "steps": steps, "searched": searched, "send_shortcut": send_shortcut}
    if shot:
        result["screenshot"] = shot["path"]
        steps.append("已截图：%s" % shot["path"])
    else:
        steps.append("截图失败：%s" % shot_error)
    return result, ""


# ------------------------------------------------------------------ 自检

if __name__ == "__main__":  # 手工自检：python webui\desktop.py（绝不动真键鼠）
    for _stream in (sys.stdout, sys.stderr):  # GBK 控制台打不出 ✓ 这类字符，先换成 UTF-8
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    os.environ["W7A_NO_INPUT"] = "1"
    print("available:", available(refresh=True))
    print("status:", json.dumps(status(), ensure_ascii=False, indent=2, default=str))
    inventory, inventory_error = display_inventory()
    print("显示器:", inventory_error or inventory["text"])
    windows, windows_error = list_windows(limit=8)
    print("窗口:", windows_error or "")
    for item in windows:
        print("   %s「%s」pid=%s %s" % (item["handle"], item["title"][:36], item["pid"], item["process"]))
    begin = time.time()
    shot_info, shot_error = snapshot(use_vision=True, use_annotation=True, read_text=False)
    print("Snapshot 用了 %.2f 秒，错误=%r" % (time.time() - begin, shot_error))
    if shot_info:
        print(shot_info["text"])
        print("图片:", shot_info["path"], shot_info["width"], "x", shot_info["height"],
              "元素", len(shot_info["elements"]), "计数", shot_info["counts"])
        shots_folder = os.path.join(tempfile.gettempdir(), "w7a-shots")
        if shot_info["path"].startswith(shots_folder):
            os.remove(shot_info["path"])  # 自检不留垃圾
    quick, quick_error = screenshot(_image_path(""), scale=0.5)
    print("Screenshot:", quick_error or ("%s %dx%d %d 字节" % (quick["path"], quick["width"],
                                                               quick["height"], quick["bytes"])))
    if quick:
        shots_folder = os.path.join(tempfile.gettempdir(), "w7a-shots")
        if quick["path"].startswith(shots_folder):
            os.remove(quick["path"])
    clip, clip_error = clipboard("get")
    print("剪贴板:", clip_error or ("%d 个字" % clip["chars"]))
    listing, listing_error = process("list", limit=3)
    print("进程（内存前 3）:", listing_error or [(item["pid"], item["name"]) for item in listing["processes"]])
    values, values_error = registry("list", "HKCU\\Environment")
    print("注册表 HKCU\\Environment:", values_error or sorted((values or {}).get("values", {}).keys()))
    print("禁键鼠时的 click:", click(label=0))
    print("没给位置时的 click:", click())
