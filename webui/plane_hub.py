# -*- coding: utf-8 -*-
"""「平面」（Plane）的**单例中心**：HTTP 路由与 Agent 工具共用同一块平面。

为什么要这个文件：`webui/plane.py` 的对象模型是个普通类，谁 `Plane()` 谁就有一块
自己的图。服务端路由和 Agent 工具要是各建一个，老师看到的和 AI 改的就成了两张图
（工具建的点在网页上永远不出现）。所以两边都只认 `plane_hub.get()`。

为什么把 `import plane` 写在函数里（**延迟导入**）：

1. `plane.py` 是并行开发的，可能还没落地、或者只写了一半有语法错。放在函数里，
   导入 `plane_hub` 本身永远不会失败——服务端其余部分照常工作；
2. `tools.py` / `server.py` 在模块顶部 import 它，也不会把 `plane.py` 的导入问题
   扩散成"整个网页打不开"。

线程安全：后台下载、子代理、HTTP 线程可能同时调 `get()`，用一把模块级锁，
保证"只构造一个"。锁里只做构造，不做别的（`Plane()` 不会再回调本模块，不会自锁）。

另外这里放着**命令层的一小块**：`run_command()` 把老师习惯的 GeoGebra 风格命令
（`Midpoint(A,B)` 这种）解析成 `plane.create_many` 认的操作序列。放在本模块的理由：
它是纯解析、不碰状态，而且"中点"那条等价构造（`{on: 线段, parameter: 0.5}`，见
docs/plane-architecture.md §4「关于中点」）是**平面模块自己的约定**，跟工具层没关系。
真正的动作语义（哪个 action 干什么）在 `webui/tools.py` 里，本模块不认识 action。
"""

import builtins
import json
import re
import threading

_LOCK = threading.Lock()
_INSTANCE = {"plane": None}


def get():
    """拿共享的那块平面；**第一次调用时才构造**。拿不到会抛异常（同 ``import plane``）。"""
    with _LOCK:
        plane = _INSTANCE["plane"]
        if plane is not None:
            return plane
        import plane as plane_module          # noqa: E402 - 故意延迟到调用时
        made = plane_module.Plane()
        _INSTANCE["plane"] = made
        return made


def peek():
    """已经建好的那块平面；还没建就返回 None（**不会**触发构造）。

    给"缓存了实例的调用方"比对用：`plane_hub.set()` 换了实例之后，
    缓存里的旧实例必须作废，否则网页画的还是旧图。
    """
    with _LOCK:
        return _INSTANCE["plane"]


def set(plane):
    """换掉共享实例（测试用 / 导入整块平面时用）。"""
    with _LOCK:
        _INSTANCE["plane"] = plane


def reset():
    """丢掉实例，下次 ``get()`` 重新建一块（测试隔离用）。"""
    with _LOCK:
        _INSTANCE["plane"] = None


def fresh_state(state):
    """按一份 PlaneState（JSON 可序列化的字典）新建一块平面并返回。

    给"导入整块平面"用：`Plane.loads` 只认 JSON 文本，所以这里 dumps 一次再 loads
    （走的是同一条被自检覆盖的路），而不是把内部结构拼出来。
    """
    import plane as plane_module          # 同 get()：延迟导入，import 本模块永远不失败
    return plane_module.Plane.loads(json.dumps(state, ensure_ascii=False))


# --------------------------------------------------------------------------
# 命令层：GeoGebra 风格命令 -> create_many 的操作序列
# --------------------------------------------------------------------------

# 冻结支持的命令名（大小写不敏感）。认不出来的命令要**报出这张表**，
# 让模型知道下一步该换哪个写法，而不是收到一句"看不懂"。
COMMANDS = ("Point", "Segment", "Line", "Circle", "Midpoint", "Intersect",
            "Function", "Polygon", "Angle", "Distance", "Slider")

# 自动编号用的前缀，和 plane._ID_PREFIX 同一套口味（点用 A、B、C…，圆用 c1…）。
_ID_PREFIX = {
    "segment": "seg", "ray": "ray", "line": "lin", "vector": "vec", "circle": "c",
    "polygon": "poly", "angle": "ang", "distance": "dist", "length": "len",
    "function": "f", "slider": "sl",
}
_NUMBER = re.compile(r"^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$")
# 对象 id 是"标识符"，命令行里出现的裸名字都按 id 解释
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_']*$")


def _error(code, message, path="command", hint=""):
    return {"code": code, "message": message, "path": path, "hint": hint}


def _supported():
    return "、".join(COMMANDS)


def _free_id(plane, kind, reserved):
    """下一个没被占用的 id（点走 A、B、C…，别的按类型前缀编号）。

    ``reserved`` 是**本次命令里已经写进 ops、但还没落盘**的 id：不算上它，
    一条命令里连写两次 `Circle(A, B)` 会拿到同一个 id，整批就变成 duplicate_id。
    """
    taken = builtins.set(reserved)
    objects = getattr(plane, "state", {}).get("objects") or {}
    taken.update(objects)
    if kind == "point":
        letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        for round_index in range(100):
            for letter in letters:
                candidate = letter if round_index == 0 else "%s%d" % (letter, round_index)
                if candidate not in taken:
                    return candidate
    prefix = _ID_PREFIX.get(kind) or kind[:3]
    for index in range(1, 4000):
        candidate = "%s%d" % (prefix, index)
        if candidate not in taken:
            return candidate
    return "%s%d" % (prefix, len(taken) + 1)


def _split_args(text):
    """按逗号切参数，但引号里的逗号不算（`Function("x^2,1", 0, 3)` 这种）。"""
    parts = []
    current = []
    quote = ""
    for char in text:
        if quote:
            if char == quote:
                quote = ""
            current.append(char)
            continue
        if char in "\"'":
            quote = char
            current.append(char)
            continue
        if char == ",":
            parts.append("".join(current))
            current = []
            continue
        current.append(char)
    parts.append("".join(current))
    return [part.strip() for part in parts if part.strip()]


def _split_commands(text):
    """按换行 / `;` 切命令，并去掉 `#` 与 `//` 后面的注释。引号里的分隔符不算。"""
    lines = []
    current = []
    quote = ""
    for char in str(text or ""):
        if quote:
            if char == quote:
                quote = ""
            current.append(char)
            continue
        if char in "\"'":
            quote = char
            current.append(char)
            continue
        if char in "\n\r;":
            lines.append("".join(current))
            current = []
            continue
        current.append(char)
    lines.append("".join(current))
    cleaned = []
    for line in lines:
        for marker in ("#", "//"):
            position = line.find(marker)
            if position >= 0:
                line = line[:position]
        line = line.strip()
        if line:
            cleaned.append(line)
    return cleaned


def _number(text):
    text = str(text or "").strip()
    if not _NUMBER.match(text):
        return None
    try:
        return float(text)
    except ValueError:                       # 理论到不了，保险
        return None


def _unquote(text):
    text = str(text or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    return text


def _name(text, what, errors):
    """参数必须是一个对象名（id）。不是就记一条结构化错误。"""
    text = str(text or "").strip()
    if _IDENT.match(text):
        return text
    errors.append(_error("bad_command_arg", "%s 要写对象名，拿到的是「%s」" % (what, text),
                         "command", "例如 Segment(A, B)：A、B 是先建好的点名"))
    return ""


def _one_command(plane, line, ops, reserved, errors):
    """解析一条命令，把操作追加进 ``ops``。返回 True 表示解析成功。"""
    match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*\((.*)\)\s*$", line)
    if not match:
        errors.append(_error("bad_command", "看不懂这条命令：%s" % line, "command",
                             "写成 名字(参数, …)，例如 Midpoint(A, B)；可用命令：" + _supported()))
        return False
    name, body = match.group(1), match.group(2)
    canonical = ""
    for item in COMMANDS:
        if item.lower() == name.lower():
            canonical = item
            break
    if not canonical:
        errors.append(_error("unknown_command", "不支持的命令：%s" % name, "command",
                             "当前只支持：" + _supported()))
        return False
    raw = _split_args(body)
    handler = _COMMAND_HANDLERS[canonical]
    return handler(plane, raw, line, ops, reserved, errors)


def _cmd_point(plane, raw, line, ops, reserved, errors):
    if len(raw) != 2:
        errors.append(_error("bad_command", "Point 要正好两个坐标：%s" % line, "command",
                             "例如 Point(3, 4)；对象上的动点请用 create_object 直接写 "
                             "{on: 对象, parameter: 0..1}"))
        return False
    x, y = _number(raw[0]), _number(raw[1])
    if x is None or y is None:
        errors.append(_error("bad_command_arg", "Point 的两个参数都要是数：%s" % line,
                             "command", "例如 Point(0, 0) 或 Point(-1.5, 2)"))
        return False
    oid = _free_id(plane, "point", reserved)
    reserved.add(oid)
    ops.append({"op": "create", "type": "point", "id": oid,
                "definition": {"x": x, "y": y}})
    return True


def _cmd_two_points(plane, raw, line, ops, reserved, errors, kind, command):
    if len(raw) != 2:
        errors.append(_error("bad_command", "%s 要正好两个点：%s" % (command, line), "command",
                             "例如 %s(A, B)，A、B 是已经定义过的点" % command))
        return False
    start = _name(raw[0], "%s 的第一个点" % command, errors)
    end = _name(raw[1], "%s 的第二个点" % command, errors)
    if not start or not end:
        return False
    oid = _free_id(plane, kind, reserved)
    reserved.add(oid)
    ops.append({"op": "create", "type": kind, "id": oid,
                "definition": {"start": start, "end": end}})
    return True


def _cmd_segment(plane, raw, line, ops, reserved, errors):
    return _cmd_two_points(plane, raw, line, ops, reserved, errors, "segment", "Segment")


def _cmd_line(plane, raw, line, ops, reserved, errors):
    return _cmd_two_points(plane, raw, line, ops, reserved, errors, "line", "Line")


def _cmd_circle(plane, raw, line, ops, reserved, errors):
    """`Circle(A, B)` = 圆心 A、过点 B（和 GeoGebra 一致）。

    不给 `Circle(A, 3)` 的写法：那个 3 到底是半径还是 id 得靠猜，而猜错在课堂上
    就是画错图。要半径就直接用 create_object 写 `{center: "A", radius: 3}`。
    """
    if len(raw) != 2:
        errors.append(_error("bad_command", "Circle 要正好两个参数：%s" % line, "command",
                             "常用 Circle(A, B)：圆心 A、圆上一点 B；要半径请用 create_object "
                             "写 {center: \"A\", radius: 3}"))
        return False
    center = _name(raw[0], "Circle 的圆心", errors)
    through = _name(raw[1], "Circle 的圆上一点", errors)
    if not center or not through:
        return False
    oid = _free_id(plane, "circle", reserved)
    reserved.add(oid)
    ops.append({"op": "create", "type": "circle", "id": oid,
                "definition": {"center": center, "through": through}})
    return True


def _cmd_midpoint(plane, raw, line, ops, reserved, errors):
    """`Midpoint(A, B)` **等价展开**成文档 §4 的构造，不是新类型。

    类型表里故意没有 `midpoint`（见 docs/plane-architecture.md「关于中点」）：
    中点就是"线段上参数 0.5 的动点"，父对象是那条线段，线段动它跟着动。
    所以这里先建线段，再建 `{on: 线段, parameter: 0.5}`。
    """
    if len(raw) != 2:
        errors.append(_error("bad_command", "Midpoint 要正好两个点：%s" % line, "command",
                             "例如 Midpoint(A, B)"))
        return False
    first = _name(raw[0], "Midpoint 的第一个点", errors)
    second = _name(raw[1], "Midpoint 的第二个点", errors)
    if not first or not second:
        return False
    segment = _free_id(plane, "segment", reserved)
    reserved.add(segment)
    ops.append({"op": "create", "type": "segment", "id": segment,
                "definition": {"start": first, "end": second}})
    middle = _free_id(plane, "point", reserved)
    reserved.add(middle)
    ops.append({"op": "create", "type": "point", "id": middle,
                "definition": {"on": segment, "parameter": 0.5}})
    return True


def _cmd_intersect(plane, raw, line, ops, reserved, errors):
    """`Intersect(a, b)` 建第 1 个交点；`Intersect(a, b, k)` 要第 k 个（从 1 数）。

    `index` 的顺序按文档 §4「交点 index 的顺序」冻结：先按 x、再按 y 升序。
    交点不止一个时要第 2、3 个就再写一条 `Intersect(a, b, 2)`；不自动把每个交点都建出来，
    是因为个数得先算一遍才知道，而"命令 == 一批对象"这条更值钱（一次撤销退一条命令）。
    """
    if len(raw) not in (2, 3):
        errors.append(_error("bad_command", "Intersect 要两个或三个参数：%s" % line,
                             "command", "Intersect(a, b) 求第 1 个交点；Intersect(a, b, 2) 求第 2 个"))
        return False
    first = _name(raw[0], "Intersect 的第一个对象", errors)
    second = _name(raw[1], "Intersect 的第二个对象", errors)
    if not first or not second:
        return False
    index = 1
    if len(raw) == 3:
        given = _number(raw[2])
        if given is None:
            errors.append(_error("bad_command_arg", "Intersect 的第三个参数要是个序号：%s" % line,
                                 "command", "例如 Intersect(k1, k2, 1) 表示第 1 个交点（从 1 数）"))
            return False
        index = max(1, int(given))
    oid = _free_id(plane, "point", reserved)
    reserved.add(oid)
    ops.append({"op": "create", "type": "point", "id": oid,
                "definition": {"intersect": [first, second], "index": index - 1}})
    return True


def _cmd_function(plane, raw, line, ops, reserved, errors):
    """`Function("x^2")`，也可以 `Function("sin(x)", -3, 3)` 给定义域。"""
    if not raw:
        errors.append(_error("bad_command", "Function 要一个表达式：%s" % line, "command",
                             "例如 Function(\"x^2 - 2x - 3\")，要限定义域再加两个数"))
        return False
    expression = _unquote(raw[0])
    if not expression.strip():
        errors.append(_error("bad_command_arg", "Function 的表达式是空的：%s" % line, "command",
                             "例如 Function(\"sin(x)\")"))
        return False
    definition = {"expr": expression, "var": "x"}
    if len(raw) >= 3:
        low, high = _number(raw[1]), _number(raw[2])
        if low is None or high is None:
            errors.append(_error("bad_command_arg", "Function 的定义域要是两个数：%s" % line,
                                 "command", "例如 Function(\"x^2\", -3, 3)"))
            return False
        definition["xmin"], definition["xmax"] = low, high
    elif len(raw) == 2:
        # `Function("f(x)=x^2,1,0")` 这种带逗号的表达式会被切成多段：拼回去再试一次
        merged = _unquote(",".join(raw))
        if merged.strip():
            definition["expr"] = merged
        else:
            errors.append(_error("bad_command_arg", "Function 的定义域不完整：%s" % line,
                                 "command", "要么只给表达式，要么给表达式 + 上下界两个数"))
            return False
    oid = _free_id(plane, "function", reserved)
    reserved.add(oid)
    ops.append({"op": "create", "type": "function", "id": oid, "definition": definition})
    return True


def _cmd_polygon(plane, raw, line, ops, reserved, errors):
    if len(raw) < 3:
        errors.append(_error("bad_command", "Polygon 至少要三个点：%s" % line, "command",
                             "例如 Polygon(A, B, C)"))
        return False
    points = [_name(item, "Polygon 的顶点", errors) for item in raw]
    if any(not item for item in points):
        return False
    oid = _free_id(plane, "polygon", reserved)
    reserved.add(oid)
    ops.append({"op": "create", "type": "polygon", "id": oid,
                "definition": {"points": points}})
    return True


def _cmd_angle(plane, raw, line, ops, reserved, errors):
    if len(raw) < 3:
        errors.append(_error("bad_command", "Angle 要三个点：%s" % line, "command",
                             "例如 Angle(A, B, C)：顶点写在中间"))
        return False
    first = _name(raw[0], "Angle 的第一个点", errors)
    vertex = _name(raw[1], "Angle 的顶点", errors)
    third = _name(raw[2], "Angle 的第三个点", errors)
    if not first or not vertex or not third:
        return False
    oid = _free_id(plane, "angle", reserved)
    reserved.add(oid)
    ops.append({"op": "create", "type": "angle", "id": oid,
                "definition": {"a": first, "b": vertex, "c": third}})
    return True


def _cmd_distance(plane, raw, line, ops, reserved, errors):
    """`Distance(A, B)`：先把 AB 连出来，再量它的长度。

    直接量两个孤立的点是不行的——冻结的类型表里 `distance` 要 `{of: 线段}`。
    顺手连一条线段还有个好处：图上真能看见 AB，老师不会觉得"量了个寂寞"。
    """
    if len(raw) != 2:
        errors.append(_error("bad_command", "Distance 要正好两个点：%s" % line, "command",
                             "例如 Distance(A, B)；量一个已有的线段请用 create_object 写 "
                             "{type: \"distance\", definition: {of: \"线段id\"}}"))
        return False
    first = _name(raw[0], "Distance 的第一个点", errors)
    second = _name(raw[1], "Distance 的第二个点", errors)
    if not first or not second:
        return False
    segment = _free_id(plane, "segment", reserved)
    reserved.add(segment)
    ops.append({"op": "create", "type": "segment", "id": segment,
                "definition": {"start": first, "end": second}})
    oid = _free_id(plane, "distance", reserved)
    reserved.add(oid)
    ops.append({"op": "create", "type": "distance", "id": oid,
                "definition": {"of": segment}})
    return True


def _cmd_slider(plane, raw, line, ops, reserved, errors):
    """`Slider(1, -5, 5)` = 当前值 1、范围 -5..5，步长自动取范围的 1/100。"""
    if len(raw) < 3:
        errors.append(_error("bad_command", "Slider 至少要值、下界、上界：%s" % line, "command",
                             "例如 Slider(1, -5, 5) 或 Slider(1, -5, 5, 0.1)"))
        return False
    value, low, high = _number(raw[0]), _number(raw[1]), _number(raw[2])
    step = _number(raw[3]) if len(raw) > 3 else None
    if value is None or low is None or high is None or (len(raw) > 3 and step is None):
        errors.append(_error("bad_command_arg", "Slider 的参数都要是数：%s" % line, "command",
                             "例如 Slider(1, -5, 5, 0.1)"))
        return False
    if step is None or step <= 0:
        step = round((high - low) / 100.0, 6) or 0.1
    definition = {"value": value, "min": low, "max": high, "step": step}
    if len(raw) > 4:
        oid = _name(raw[4], "Slider 的名字", errors)
        if not oid:
            return False
    else:
        oid = _free_id(plane, "slider", reserved)
    reserved.add(oid)
    ops.append({"op": "create", "type": "slider", "id": oid, "definition": definition})
    return True


_COMMAND_HANDLERS = {
    "Point": _cmd_point, "Segment": _cmd_segment, "Line": _cmd_line,
    "Circle": _cmd_circle, "Midpoint": _cmd_midpoint, "Intersect": _cmd_intersect,
    "Function": _cmd_function, "Polygon": _cmd_polygon, "Angle": _cmd_angle,
    "Distance": _cmd_distance, "Slider": _cmd_slider,
}


def run_command(plane, text):
    """把命令文本解析成操作序列。返回 ``(ops, errors)``；有 errors 时 ops 不要用。

    纯解析：本函数**不改平面状态**，调用方拿 ops 去走 ``plane.create_many``
    （整批一个撤销步，老师按一次撤销就退回整条命令）。
    """
    ops = []
    errors = []
    # 注意：本模块的 `set()` 是"换掉共享实例"，把内建 set 遮住了 —— 这里必须写全名，
    # 否则 `set()` 会变成"少一个参数"的 TypeError（踩过一次，注释留证）。
    reserved = builtins.set()        # 本次命令里已经占用（还没落盘）的 id
    lines = _split_commands(text)
    if not lines:
        errors.append(_error("missing_field", "没有要执行的命令", "command",
                             "例如 Midpoint(A, B)；可用命令：" + _supported()))
        return ops, errors
    for line in lines:
        if not _one_command(plane, line, ops, reserved, errors):
            return ops, errors
    return ops, []
