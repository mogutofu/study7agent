# -*- coding: utf-8 -*-
"""「平面」（Plane）对象模型：状态 + 依赖图 + 求值 + 门面（P1）。

对应 ``docs/plane-architecture.md`` §3 / §4 / §5.2（接口冻结件）。这里只做三件事：

1. **状态**：``PlaneObject`` / ``PlaneState`` 的形状严格按 §3，键名一个不改；
   ``json`` 存读（``dumps`` / ``loads``），**不用 pickle**。
2. **依赖图**：创建时从 ``definition`` 自动抽 ``parents``（§4 类型表的引用字段，
   另外加上"表达式里引用了哪个对象"这一条，滑动条联动靠它）；**拓扑求值**（父对象先算）；
   **成环拒绝**（结构化错误）；**级联删除**并把连带删掉的 id 列在 ``removed`` 里。
3. **门面**：``create`` / ``create_many`` / ``update`` / ``delete`` / ``get`` /
   ``list_objects`` / ``query_state`` / ``snapshot`` / ``restore`` / ``undo`` / ``redo``。
   返回值一律是 §5.2 冻结的结构：成功带 ``stateVersion/created/updated/removed/
   warnings/errors/explanation/handle``，失败带 ``errors: [{code, message, path, hint}]``。

**求值只做"传播"，不做"整张图重算"**：改一个对象只重算它自己和它的后代（拓扑序），
所以"改 A 的坐标，线段、中点、圆跟着变"是依赖传播的结果，前端拿到 state 就能直接画。

求交的数学不在这里：``plane_math.intersection`` 是唯一调用点，它优先用
``webui/plane_geom.py``（另一个 agent 的求解器），拿不到才退回极简实现并在
``warnings`` 里说明。

只在标准库 + 本项目模块上跑（``plane_math`` 用到 ``plot`` / ``calc``），Python 3.8。
"""

import copy
import json
import math
import os
import re
import time

import plane_math

# --------------------------------------------------------------------------
# 上限与默认值
# --------------------------------------------------------------------------

MAX_OBJECTS = 2000           # 一块平面上最多多少对象（防止模型批量刷爆内存）
MAX_BATCH = 200              # create_many 一次最多几个操作
MAX_UNDO = 100               # 撤销栈深度（超了丢最老的）
QUERY_LIMIT = 50             # query_state 默认最多返回几个对象
MAX_QUERY_LIMIT = 500
MAX_WARNINGS = 6             # 一次返回最多几条 warning（多了截断，别刷屏）
SEQ_LIMIT = 10 ** 9
ROUND_SEGMENTS = 36          # 弧 / 扇形给前端折线的段数（与 plot.ROUND_SEGMENTS 一致）

DEFAULT_VIEW = {"xmin": -10.0, "ymin": -6.0, "xmax": 10.0, "ymax": 6.0,
                "grid": True, "axes": True, "polar": False}
# 自动区间（函数没写 xmin/xmax）时，把"网页端看得见的横轴范围"往两边各多采这么多屏。
# 老师要的是"无限"：屏幕外也要尽量画出来，拖出去、放大之后线还在，不会贴着边断掉。
AUTO_WINDOW_MARGIN = 2.0
DEFAULT_STYLE = {"color": "#4d6bfe", "size": 4, "visible": True, "layer": 0,
                 "lineStyle": "solid", "fill": None, "label": True}
OBJECT_KEYS = ("id", "type", "definition", "parents", "children", "value", "style",
               "meta", "seq")
SOURCE_KINDS = ("agent", "user", "import")
LINE_STYLES = ("solid", "dashed", "dotted")
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_']*$")

# 自动编号用的前缀（躲开解析器里有含义的名字：x / y / t / u / e / pi / sin…）
_ID_PREFIX = {
    "segment": "seg", "ray": "ray", "line": "lin", "vector": "vec",
    "perpendicular": "perp", "parallel": "para", "perpendicular_bisector": "pbis",
    "angle_bisector": "abis", "tangent": "tang", "circle": "c", "arc": "arc",
    # 三角形的三个心（重心 / 内心 / 垂心）：id 用 cen / inc / ort 开头，一眼能认出来
    "centroid": "cen", "incenter": "inc", "orthocenter": "ort",
    "sector": "sect", "polygon": "poly", "regular_polygon": "reg", "angle": "ang",
    "distance": "dist", "length": "len", "area": "area", "slope": "slope",
    "radius": "rad", "function": "f", "implicit": "eq", "parametric": "par",
    "polar": "pol", "inequality": "ineq", "conic": "conic", "slider": "sl",
    "number": "num", "text": "txt", "boolean": "bool", "locus": "locus",
    "trace": "trace", "measure": "m",
}

# 求值要等后续阶段的类型（P3/P4）：对象照样登记，但这次不静默——创建时给 warning
NOT_COMPUTED_YET = ("conic", "locus")

# 轨迹最多留多少个点。Win7 教室里一次拖拽可能上千个点，不设上限会越拖越卡；
# 到顶后丢掉最老的（"接着走"的感觉比"越走越慢"重要）。
TRACE_MAX_POINTS = 1500

# 紧凑模式（query_state / list_objects 默认）里，曲线采样点超过这么多就不回给模型。
# 一个显函数默认 2400 个采样点 ≈ 4 万字符，一次就能把上下文吃光。
COMPACT_VALUE_POINTS = 64

# 表达式里引用别的对象（滑动条联动）的字段
_EXPRESSION_FIELDS = {
    "function": ("expr",), "implicit": ("expr",), "parametric": ("x", "y"),
    "polar": ("expr",), "inequality": ("expr", "exprs"),
}


# --------------------------------------------------------------------------
# 类型表（冻结件 §4）：每个类型要哪些字段、哪些字段是"引用别的对象"
# --------------------------------------------------------------------------

def _as_mapping(value):
    """把 dict / JSON 字符串 / 键值对数组统一成 dict，其它当空。

    下游是 x.update(...)：传字符串进来会抛
    dictionary update sequence element #0 has length 1; 2 is required。
    """
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    if isinstance(value, (list, tuple)):
        try:
            return dict(value)
        except Exception:
            return {}
    return {}


def _spec(ref=None, any_of=None, numbers=(), texts=(), hint="", not_yet=False):
    return {"ref": dict(ref or {}), "any_of": tuple(any_of or ()),
            "numbers": tuple(numbers), "texts": tuple(texts),
            "hint": hint, "not_yet": not_yet}


_TYPE_SPEC = {
    "point": _spec(ref={"on": "id", "intersect": "ids"},
                   any_of=(("x", "y"), ("on",), ("intersect",)),
                   numbers=("x", "y", "parameter", "index"),
                   hint="point 要写 {x, y}、{on: 父对象, parameter: 0..1} "
                        "或 {intersect: [id1, id2], index: 0}；"
                        "两个交点时可以用 {intersect: [...], near: {x, y}} 取离 (x, y) 近的那个"),
    "segment": _spec(ref={"start": "id", "end": "id"}, any_of=(("start", "end"),),
                     hint="segment 要写 {start: 点, end: 点}"),
    "ray": _spec(ref={"start": "id", "end": "id"}, any_of=(("start", "end"),),
                 hint="ray 要写 {start: 点, end: 点}"),
    "line": _spec(ref={"start": "id", "end": "id"}, any_of=(("start", "end"),),
                  hint="line 要写 {start: 点, end: 点}"),
    "vector": _spec(ref={"start": "id", "end": "id"}, any_of=(("start", "end"),),
                    hint="vector 要写 {start: 点, end: 点}"),
    "perpendicular": _spec(ref={"through": "id", "to": "id"},
                           any_of=(("through", "to"),),
                           hint="perpendicular 要写 {through: 点, to: 线}"),
    "parallel": _spec(ref={"through": "id", "to": "id"},
                      any_of=(("through", "to"),),
                      hint="parallel 要写 {through: 点, to: 线}"),
    "perpendicular_bisector": _spec(ref={"a": "id", "b": "id"},
                                    any_of=(("a", "b"),),
                                    hint="perpendicular_bisector 要写 {a: 点, b: 点}"),
    "angle_bisector": _spec(ref={"a": "id", "b": "id", "c": "id"},
                            any_of=(("a", "b", "c"),),
                            hint="angle_bisector 要写 {a, b, c}，顶点在 b"),
    # 三角形的三个心：都是「点」，但要多算一步（重心/内心/垂心）。
    # 老师讲三角形四心时要在图上直接点出来，不能让模型自己算坐标再画个普通点 ——
    # 普通点不会跟着顶点动，学生一拖就散了。
    "centroid": _spec(ref={"a": "id", "b": "id", "c": "id"},
                      any_of=(("a", "b", "c"),),
                      hint="centroid 要写 {a, b, c}，求三角形 ABC 的重心"),
    "incenter": _spec(ref={"a": "id", "b": "id", "c": "id"},
                      any_of=(("a", "b", "c"),),
                      hint="incenter 要写 {a, b, c}，求三角形 ABC 的内心"),
    "orthocenter": _spec(ref={"a": "id", "b": "id", "c": "id"},
                         any_of=(("a", "b", "c"),),
                         hint="orthocenter 要写 {a, b, c}，求三角形 ABC 的垂心"),
    "tangent": _spec(ref={"to": "id", "at": "id"}, any_of=(("to", "at"),),
                     hint="tangent 要写 {to: 圆, at: 切点}"),
    "circle": _spec(ref={"center": "id", "through": "id_or_ids"},
                    any_of=(("center", "radius"), ("center", "through"), ("through",)),
                    numbers=("radius",),
                    hint="circle 三种构造：{center, radius}、{center, through}、"
                         "{through: [a, b, c]}"),
    "arc": _spec(ref={"center": "id", "from": "id", "to": "id"},
                 any_of=(("center", "from", "to"),),
                 hint="arc 要写 {center: 圆心, from: 起点, to: 终点}"),
    "sector": _spec(ref={"center": "id", "from": "id", "to": "id"},
                    any_of=(("center", "from", "to"),),
                    hint="sector 要写 {center: 圆心, from: 起点, to: 终点}"),
    "polygon": _spec(ref={"points": "ids"}, any_of=(("points",),),
                     hint="polygon 要写 {points: [点, 点, 点…]}（至少 3 个）"),
    "regular_polygon": _spec(ref={"a": "id", "b": "id"}, any_of=(("a", "b"),),
                             numbers=("n",),
                             hint="regular_polygon 要写 {a: 点, b: 点, n: 边数}"),
    "angle": _spec(ref={"a": "id", "b": "id", "c": "id"}, any_of=(("a", "b", "c"),),
                   hint="angle 要写 {a, b, c}，∠ABC 的顶点在 b"),
    "distance": _spec(ref={"of": "id_or_ids"}, any_of=(("of",),),
                      hint="distance 要写 {of: [点, 点]} 或 {of: 线段}"),
    "length": _spec(ref={"of": "id_or_ids"}, any_of=(("of",),),
                    hint="length 要写 {of: 线段/多边形}"),
    "area": _spec(ref={"of": "id_or_ids"}, any_of=(("of",),),
                  hint="area 要写 {of: 多边形/圆}"),
    "slope": _spec(ref={"of": "id_or_ids"}, any_of=(("of",),),
                   hint="slope 要写 {of: [点, 点]} 或 {of: 直线}"),
    "radius": _spec(ref={"of": "id_or_ids"}, any_of=(("of",),),
                    hint="radius 要写 {of: 圆}"),
    "function": _spec(texts=("expr",), any_of=(("expr",),), numbers=("xmin", "xmax", "samples"),
                      hint="function 要写 {expr: \"x^2+sin(x)\", var: \"x\"}；"
                           "xmin/xmax 可以不写 —— 不写就是自动区间，跟着网页上的视野走"
                           "（还会往两边各多采 %.0f 屏，拖出去、放大都还有线）"
                           % AUTO_WINDOW_MARGIN),
    "implicit": _spec(texts=("expr",), any_of=(("expr",),), numbers=("grid",),
                      hint="implicit 要写 {expr: \"x^2+y^2-1\"}（也可以写 x^2+y^2=1）"),
    "parametric": _spec(texts=("x", "y"), any_of=(("x", "y"),), numbers=("tmin", "tmax"),
                        hint="parametric 要写 {x: \"cos(t)\", y: \"sin(t)\", tmin, tmax}"),
    "polar": _spec(texts=("expr",), any_of=(("expr",),), numbers=("tmin", "tmax"),
                   hint="polar 要写 {expr: \"1+cos(theta)\", tmin, tmax}"),
    "inequality": _spec(texts=("expr",), any_of=(("expr",), ("exprs",)),
                        numbers=("grid",),
                        hint="inequality 要写 {expr: \"y > x^2\"} 或 "
                             "{exprs: [\"x^2+y^2<=1\", \"y>x^2\"], op: \"and\"}"),
    "conic": _spec(texts=("kind",), any_of=(("kind",),), not_yet=True,
                   hint="conic 要写 {kind: \"ellipse\"|\"hyperbola\"|\"parabola\", …}"),
    "slider": _spec(any_of=(("value",),), numbers=("value", "min", "max", "step"),
                    hint="slider 要写 {value, min, max, step}"),
    "number": _spec(any_of=(("value",),), numbers=("value",),
                    hint="number 要写 {value: 数字}"),
    "text": _spec(texts=("text",), any_of=(("text",),),
                  hint="text 要写 {text: \"…\"}"),
    "boolean": _spec(any_of=(("value",),),
                     hint="boolean 要写 {value: true|false}"),
    "locus": _spec(ref={"point": "id", "driver": "id"}, any_of=(("point", "driver"),),
                   numbers=("samples",), not_yet=True,
                   hint="locus 要写 {point: 动点, driver: 驱动对象, samples}"),
    "trace": _spec(ref={"of": "id"}, any_of=(("of",),),
                   hint="trace 要写 {of: 被追踪的对象}"),
    "measure": _spec(ref={"args": "ids"}, any_of=(("kind", "args"),),
                     hint="measure 要写 {kind: \"distance\"|\"angle\"|…, args: [对象…]}"),
}

TYPE_NAMES = tuple(sorted(_TYPE_SPEC))


# --------------------------------------------------------------------------
# 结构化错误 / 返回值拼装（冻结件 §5.2）
# --------------------------------------------------------------------------

def error(code, message, path="", hint=""):
    """结构化错误：四个键永远都在（没内容就是空串）。"""
    return {"code": str(code), "message": str(message), "path": str(path),
            "hint": str(hint)}


def _clip(warnings):
    """warning 去重 + 截断（别把一整块平面的问题都塞进模型上下文）。"""
    out = []
    for item in warnings or []:
        text = str(item)
        if text and text not in out:
            out.append(text)
    if len(out) > MAX_WARNINGS:
        extra = len(out) - MAX_WARNINGS + 1
        out = out[:MAX_WARNINGS - 1] + ["…还有 %d 条提示" % extra]
    return out


def make_object(id, type, definition=None, style=None, meta=None, seq=0, value=None,
                parents=None, source="agent", warnings=None):
    """造一个 ``PlaneObject``（§3 的九个键全都补齐）。

    ``children`` 交给 :class:`Plane` 维护，调用方**不要**自己写；``value`` 同理是算出来的。
    """
    notes = warnings if isinstance(warnings, list) else []
    return {
        "id": str(id),
        "type": str(type),
        "definition": copy.deepcopy(definition) if isinstance(definition, dict) else {},
        "parents": [str(item) for item in (parents or [])],
        "children": [],
        "value": copy.deepcopy(value) if isinstance(value, dict) else {},
        "style": _clean_style(style, notes),
        "meta": _clean_meta(meta, notes, source),
        "seq": int(seq) if isinstance(seq, int) and not isinstance(seq, bool) else 0,
    }


def _clean_style(style, warnings):
    """样式校验（Agent 给的 JSON 也是不可信输入）：认不出来的键留着，坏的键退回默认值。"""
    out = copy.deepcopy(DEFAULT_STYLE)
    if not isinstance(style, dict):
        if style is not None:
            warnings.append("style 要是字典，已经用默认样式")
        return out
    for key, value in style.items():
        if key == "color":
            out["color"] = value if isinstance(value, str) and value.strip() else out["color"]
        elif key == "size":
            out["size"] = value if _is_number(value) and value >= 0 else out["size"]
        elif key == "visible":
            out["visible"] = bool(value)
        elif key == "layer":
            out["layer"] = int(value) if _is_number(value) else out["layer"]
        elif key == "lineStyle":
            out["lineStyle"] = value if value in LINE_STYLES else out["lineStyle"]
        elif key == "fill":
            out["fill"] = value if isinstance(value, str) else None
        elif key == "label":
            out["label"] = bool(value)
        else:
            out[key] = value                      # 未知键原样留着，向前兼容
    return out


def _clean_meta(meta, warnings, source="agent"):
    out = {"source": source if source in SOURCE_KINDS else "agent",
           "at": time.strftime("%Y-%m-%d %H:%M"), "frozen": False}
    if not isinstance(meta, dict):
        if meta is not None:
            warnings.append("meta 要是字典，已经用默认值")
        return out
    for key, value in meta.items():
        if key == "source":
            if value in SOURCE_KINDS:
                out["source"] = value
            elif value is not None:
                warnings.append("meta.source 只能是 %s，已经记成 agent"
                                % "/".join(SOURCE_KINDS))
        elif key == "frozen":
            out["frozen"] = bool(value)
        elif key == "at":
            out["at"] = value if isinstance(value, str) else out["at"]
        else:
            out[key] = value
    return out


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) \
        and math.isfinite(value)


def _has_range(source, low_key, high_key):
    """source 里那一对数是不是"能用的区间"（都是数字，而且上界大于下界）。"""
    if not isinstance(source, dict):
        return False
    low, high = source.get(low_key), source.get(high_key)
    return _is_number(low) and _is_number(high) and float(high) > float(low)


def _ref_ids(value, kind):
    """把引用字段读成 id 列表；形状不对返回 None。"""
    if kind == "id":
        return [value] if isinstance(value, str) and value.strip() else None
    if kind == "ids":
        items = value if isinstance(value, (list, tuple)) else None
        if not items:
            return None
        out = []
        for item in items:
            if not isinstance(item, str) or not item.strip():
                return None
            out.append(item)
        return out
    if isinstance(value, str) and value.strip():
        return [value]
    items = value if isinstance(value, (list, tuple)) else None
    if not items:
        return None
    out = []
    for item in items:
        if not isinstance(item, str) or not item.strip():
            return None
        out.append(item)
    return out


def _extra_checks(kind, definition, warnings):
    """类型自己的额外校验（数量、范围、枚举）。"""
    errors = []
    if kind == "point":
        if "intersect" in definition and isinstance(definition["intersect"], (list, tuple)) \
                and len(definition["intersect"]) != 2:
            errors.append(error("bad_value", "intersect 要正好两个对象",
                                "definition.intersect", "例如 {intersect: [\"l1\", \"l2\"]}"))
        if _is_number(definition.get("index")) and definition["index"] < 0:
            errors.append(error("bad_value", "index 不能是负数", "definition.index",
                                "从 0 开始数"))
    elif kind == "circle":
        if "radius" in definition and definition["radius"] <= 0:
            errors.append(error("bad_value", "半径要大于 0", "definition.radius",
                                "换一个正数半径，或者改用到圆上一点的构造"))
        if isinstance(definition.get("through"), (list, tuple)) \
                and len(definition["through"]) != 3:
            errors.append(error("bad_value", "三点定圆要正好三个点",
                                "definition.through", "例如 {through: [\"A\", \"B\", \"C\"]}"))
    elif kind == "polygon":
        points = definition.get("points")
        if isinstance(points, (list, tuple)) and len(points) < 3:
            errors.append(error("bad_value", "多边形至少要三个点", "definition.points",
                                "补够三个顶点"))
    elif kind == "regular_polygon":
        sides = definition.get("n", 5)
        if not isinstance(sides, int) or isinstance(sides, bool) or not 3 <= sides <= 100:
            errors.append(error("bad_value", "正多边形的边数要在 3..100 之间",
                                "definition.n", "例如 n = 5"))
    elif kind == "slider":
        low, high = definition.get("min"), definition.get("max")
        if _is_number(low) and _is_number(high) and high <= low:
            errors.append(error("bad_value", "滑动条的 min 要小于 max", "definition.max",
                                "把范围写成 min < max"))
        if _is_number(definition.get("step")) and definition["step"] <= 0:
            errors.append(error("bad_value", "滑动条的 step 要大于 0", "definition.step",
                                "常用 0.1 或 1"))
        value = definition.get("value")
        if _is_number(value) and _is_number(low) and _is_number(high) \
                and not low <= value <= high:
            errors.append(error("bad_value", "滑动条的 value 要落在 [min, max] 里",
                                "definition.value", "调整 value，或者把 min/max 放宽"))
    elif kind == "boolean":
        if not isinstance(definition.get("value"), bool):
            errors.append(error("bad_value", "boolean 的 value 只能是 true/false",
                                "definition.value", "写 true 或 false"))
    elif kind == "inequality":
        exprs = definition.get("exprs")
        if exprs is not None:
            if not isinstance(exprs, (list, tuple)) or not exprs \
                    or any(not isinstance(item, str) or not item.strip() for item in exprs):
                errors.append(error("bad_value", "exprs 要是一串非空的不等式",
                                    "definition.exprs", "例如 [\"y > x^2\"]"))
            if str(definition.get("op", "and")).lower() not in ("and", "or"):
                errors.append(error("bad_value", "op 只能是 and 或 or", "definition.op",
                                    "多个区域取交集用 and，取并集用 or"))
    return errors


def _expression_parents(kind, definition, known):
    """表达式里引用到的对象（滑动条）也算父对象：联动才传播得起来。"""
    names = []
    for field in _EXPRESSION_FIELDS.get(kind, ()):
        value = definition.get(field)
        texts = value if isinstance(value, (list, tuple)) else [value]
        for text in texts:
            if isinstance(text, str):
                names.extend(plane_math.expression_names(text))
    out = []
    for name in names:
        if name in known and name not in out:
            out.append(name)
    return out


def extract_parents(kind, definition, known):
    """抽 ``parents``：先按类型表里的引用字段（按字段声明顺序），再按表达式里出现的对象名。"""
    spec = _TYPE_SPEC.get(kind) or {}
    refs = []
    for field, ref_kind in spec.get("ref", {}).items():
        if field not in definition:
            continue
        ids = _ref_ids(definition[field], ref_kind)
        for one in ids or ():
            if one not in refs:
                refs.append(one)
    for one in _expression_parents(kind, definition, known):
        if one not in refs:
            refs.append(one)
    return refs


def validate_definition(kind, definition, known, warnings):
    """校验 ``definition``。返回 ``(parents, errors)``。``known`` 是"可以引用的 id 集合"。"""
    spec = _TYPE_SPEC.get(kind)
    if spec is None:
        return [], [error("unknown_type", "不认识的类型：%s" % (kind or "（空）"), "type",
                          "可用类型：" + "、".join(TYPE_NAMES))]
    if not isinstance(definition, dict):
        return [], [error("bad_definition", "definition 要是一个字典", "definition",
                          "例如 {\"x\": 0, \"y\": 0}")]
    errors = []
    any_of = spec["any_of"]
    if any_of and not any(_group_present(definition, group) for group in any_of):
        group = any_of[0]
        errors.append(error("missing_field",
                            "%s 缺少 %s" % (kind, "、".join(group)),
                            "definition." + group[0], spec["hint"]))
    refs = []
    for field, ref_kind in spec["ref"].items():
        if field not in definition:
            continue
        ids = _ref_ids(definition[field], ref_kind)
        if ids is None:
            errors.append(error("bad_reference",
                                "%s 的 %s 要写对象 id%s"
                                % (kind, field, "数组" if ref_kind == "ids" else ""),
                                "definition." + field, spec["hint"]))
            continue
        for one in ids:
            if one not in known:
                errors.append(error("unknown_parent", "引用的对象不存在：%s" % one,
                                    "definition." + field,
                                    "先创建 %s，或者用 create_many 一次把这一组建出来" % one))
        refs.extend(ids)
    for one in _expression_parents(kind, definition, known):
        if one not in refs:
            refs.append(one)
    for field in spec["numbers"]:
        if field in definition and not _is_number(definition[field]):
            errors.append(error("bad_value", "%s 的 %s 要是数字" % (kind, field),
                                "definition." + field, spec["hint"]))
    for field in spec["texts"]:
        if field in definition and (not isinstance(definition[field], str)
                                    or not definition[field].strip()):
            errors.append(error("bad_value", "%s 的 %s 要是非空字符串" % (kind, field),
                                "definition." + field, spec["hint"]))
    errors.extend(_extra_checks(kind, definition, warnings))
    if spec["not_yet"]:
        warnings.append("%s 的对象已经登记，但求值要等后续阶段（P3/P4）" % kind)
    return refs, errors


def _group_present(definition, group):
    for field in group:
        if field not in definition:
            return False
        value = definition[field]
        if value is None or value == "" or value == []:
            return False
    return True


# --------------------------------------------------------------------------
# Plane
# --------------------------------------------------------------------------

class Plane(object):
    """一块平面。所有门面方法返回 §5.2 冻结结构。"""

    def __init__(self, workspace=None, state=None):
        self.workspace = workspace or os.getcwd()
        # window 是"网页端现在看得见的横轴范围"，只用来决定自动区间的曲线采多远，
        # 不算状态版本、不进撤销栈（见 set_window / _curve_bounds）。
        self.state = {"version": 0, "window": None, "view": dict(DEFAULT_VIEW), "objects": {},
                      "order": [], "undo": [], "redo": []}
        self._seq = 0
        self._versions = []          # 版本号对内容快照（受 MAX_UNDO 限制），restore 用
        if state is not None:
            self._adopt(state)

    # -- 读 ----------------------------------------------------------------
    def view(self):
        return copy.deepcopy(self.state["view"])

    def get(self, id=None):
        """按 id 拿一个对象的**拷贝**；没有就返回 None。"""
        obj = self.state["objects"].get(str(id or ""))
        return copy.deepcopy(obj) if obj is not None else None

    def list_objects(self, type=None, visible=None, tag=None):
        """按创建顺序列出对象（每个都带算好的 ``value``）。

        前端拿到这个列表就能直接画：点的 ``{x, y}``、圆的 ``{center:{x,y}, radius}``、
        线段的两个端点、函数/隐式曲线的 ``value.segments``（``[[x, y], …]``，
        直接喂 SVG ``<polyline>``）。
        """
        wanted = str(type).strip() if type else None
        out = []
        for oid in self.state["order"]:
            obj = self.state["objects"].get(oid)
            if obj is None:
                continue
            if wanted and obj["type"] != wanted:
                continue
            if visible is not None and bool(obj["style"].get("visible", True)) != bool(visible):
                continue
            if tag is not None and not _has_tag(obj, tag):
                continue
            out.append(copy.deepcopy(obj))
        return out

    def counts(self):
        out = {}
        for oid in self.state["order"]:
            obj = self.state["objects"].get(oid)
            if obj is None:
                continue
            out[obj["type"]] = out.get(obj["type"], 0) + 1
        return out

    def query_state(self, type=None, ids=None, tag=None, visible=None, source=None,
                    text=None, limit=None, compact=True):
        """查状态。**默认紧凑模式**：只回"计数 + 命中的对象"，不回整张平面。

        ``truncated`` 表示还有没返回的对象（要么被 ``limit`` 截了，要么不匹配过滤）。
        """
        try:
            size = int(limit) if limit is not None else QUERY_LIMIT
        except (TypeError, ValueError):
            size = QUERY_LIMIT
        size = max(1, min(MAX_QUERY_LIMIT, size))
        wanted_ids = None
        if ids is not None:
            wanted_ids = set(str(item) for item in ids) if isinstance(ids, (list, tuple)) \
                else {str(ids)}
        wanted_type = str(type).strip() if type else None
        wanted_text = str(text).strip().lower() if text else None

        rows = []
        matched = 0
        for oid in self.state["order"]:
            obj = self.state["objects"].get(oid)
            if obj is None:
                continue
            if wanted_type and obj["type"] != wanted_type:
                continue
            if wanted_ids is not None and oid not in wanted_ids:
                continue
            if tag is not None and not _has_tag(obj, tag):
                continue
            if visible is not None and bool(obj["style"].get("visible", True)) != bool(visible):
                continue
            if source is not None and obj["meta"].get("source") != source:
                continue
            if wanted_text and not _matches_text(obj, wanted_text):
                continue
            matched += 1
            if len(rows) < size:
                rows.append(copy.deepcopy(obj) if not compact else _compact(obj))
        total = len(self.state["objects"])
        return {"ok": True, "stateVersion": self.state["version"],
                "version": self.state["version"], "view": self.view(),
                "counts": self.counts(), "total": total, "matched": matched,
                "returned": len(rows), "truncated": matched > len(rows),
                "limit": size, "compact": bool(compact), "objects": rows,
                "warnings": [], "errors": [], "handle": "",
                "explanation": "共 %d 个对象，命中 %d 个，返回 %d 个"
                               % (total, matched, len(rows))}

    def variables(self):
        """命名参数的当前值（键是对象 id）：滑动条、数值对象、度量。

        表达式采样时把它当变量表传进 ``plane_math``，滑动条就真的联动了。
        """
        out = {}
        for oid in self.state["order"]:
            obj = self.state["objects"].get(oid)
            if obj is None:
                continue
            kind = obj["type"]
            if kind in ("slider", "number"):
                value = obj["definition"].get("value")
            elif kind in ("distance", "length", "area", "slope", "radius", "measure"):
                value = (obj.get("value") or {}).get("value")
            else:
                continue
            if _is_number(value):
                out[oid] = float(value)
        return out

    def descendants(self, id=None):
        """所有依赖它的后代 id（按创建顺序）。"""
        oid = str(id or "")
        if oid not in self.state["objects"]:
            return []
        seen = set()
        stack = list(self.state["objects"][oid]["children"])
        while stack:
            current = stack.pop()
            if current in seen or current not in self.state["objects"]:
                continue
            seen.add(current)
            stack.extend(self.state["objects"][current]["children"])
        return [item for item in self.state["order"] if item in seen]

    def topological_order(self):
        """拓扑序（父对象在前）。有环就返回 ``ok=False`` 和环上的 id，绝不硬转。"""
        objects = self.state["objects"]
        pending = {}
        for oid in self.state["order"]:
            obj = objects.get(oid)
            if obj is None:
                continue
            pending[oid] = [parent for parent in obj["parents"] if parent in objects]
        order = []
        ready = [oid for oid in self.state["order"]
                 if oid in pending and not pending[oid]]
        while ready:
            current = ready.pop(0)
            order.append(current)
            pending.pop(current, None)
            for oid in list(pending):
                if current in pending[oid]:
                    pending[oid].remove(current)
                    if not pending[oid]:
                        ready.append(oid)
        if pending:
            return {"ok": False, "order": order, "cycle": sorted(pending),
                    "error": error("cycle", "依赖图上有环：%s" % "、".join(sorted(pending)),
                                   "parents", "把成环的引用去掉一个")}
        return {"ok": True, "order": order, "cycle": [], "error": None}

    # -- 写 ----------------------------------------------------------------
    def create(self, type=None, id=None, definition=None, style=None, meta=None):
        """建一个对象。等价于 ``create_many([{op: "create", …}])``。"""
        return self.create_many([{"op": "create", "type": type, "id": id,
                                  "definition": definition, "style": style,
                                  "meta": meta}])

    def create_many(self, ops=None):
        """批量操作（冻结件 §5.1 的 ``plane_batch``）：``[{op, type, id, definition, style}]``。

        - ``op``：``create`` / ``update`` / ``delete``（``add`` / ``create_object`` 等别名也认）；
        - 整批**原子**：有一条不过就一条都不落盘，返回结构化 ``errors``；
        - 同批里可以"先写子对象、后写父对象"：内部按依赖拓扑排序后再建；
        - 整批只加 **1** 次 ``version``，撤销也是一步（老师按一次撤销退回整批）。
        """
        if not isinstance(ops, (list, tuple)) or not ops:
            return self._fail([error("missing_field", "没有要执行的操作", "operations",
                                     "传 [{op: \"create\", type: \"point\", …}, …]")])
        if len(ops) > MAX_BATCH:
            return self._fail([error("too_many", "一次最多 %d 个操作" % MAX_BATCH,
                                     "operations", "分批提交")])
        before = self._mark()
        warnings = []
        errors = []
        known = set(self.state["objects"])
        creates = []
        others = []
        for index, raw in enumerate(ops):
            where = "operations[%d]" % index
            if not isinstance(raw, dict):
                errors.append(error("bad_definition", "第 %d 个操作不是字典" % (index + 1),
                                    where, "每个操作写成 {op, type, id, definition}"))
                continue
            action = str(raw.get("op") or raw.get("action") or "create").strip().lower()
            if action in ("create", "add", "create_object", "create_objects"):
                action = "create"
            elif action in ("update", "update_object", "set"):
                action = "update"
            elif action in ("delete", "remove", "delete_object"):
                action = "delete"
            else:
                errors.append(error("unknown_op", "不认识的操作：%s" % action, where + ".op",
                                    "可用：create / update / delete"))
                continue
            if action == "create":
                kind = raw.get("type")
                if not isinstance(kind, str) or not kind.strip():
                    errors.append(error("missing_field", "create 缺少 type", where + ".type",
                                        "type 见架构文档 §4 的类型表"))
                    continue
                kind = kind.strip()
                oid = raw.get("id")
                if oid is None:
                    oid = self._free_id(kind, known)
                elif not isinstance(oid, str) or not oid.strip():
                    errors.append(error("bad_value", "id 要是非空字符串", where + ".id",
                                        "id 也是标签，例如 \"A\""))
                    continue
                oid = oid.strip()
                if oid in known:
                    errors.append(error("duplicate_id", "id 已经存在：%s" % oid,
                                        where + ".id", "换一个 id，或者改用 update"))
                    continue
                if len(self.state["objects"]) + len(creates) >= MAX_OBJECTS:
                    errors.append(error("too_many", "一块平面最多 %d 个对象" % MAX_OBJECTS,
                                        where, "先删掉一些不用的对象"))
                    continue
                known.add(oid)
                creates.append({"op": "create", "index": index, "type": kind, "id": oid,
                                "definition": raw.get("definition"),
                                "style": raw.get("style"), "meta": raw.get("meta")})
            else:
                oid = raw.get("id")
                if not isinstance(oid, str) or not oid.strip():
                    errors.append(error("missing_field", "%s 缺少 id" % action, where + ".id",
                                        "说明改哪一个对象"))
                    continue
                oid = oid.strip()
                if oid not in known:
                    errors.append(error("not_found", "没有这个对象：%s" % oid, where + ".id",
                                        "先用 list_objects 看看有哪些对象"))
                    continue
                if action == "update":
                    fields = raw.get("fields")
                    if not isinstance(fields, dict):
                        fields = {key: raw[key] for key in ("definition", "style", "meta")
                                  if key in raw}
                    if not fields:
                        errors.append(error("missing_field", "update 没有要改的内容",
                                            where, "给 definition / style / meta"))
                        continue
                else:
                    fields = None
                others.append({"op": action, "index": index, "id": oid, "fields": fields})

        if errors:
            return self._fail(errors, warnings)

        # 校验 definition（引用可以是同批里"待建"的对象）
        for item in creates:
            definition = item["definition"]
            if definition is None:
                definition = {}
                item["definition"] = definition
            parents, found = validate_definition(item["type"], definition, known, warnings)
            item["parents"] = [parent for parent in parents if parent != item["id"]]
            item["self_ref"] = item["id"] in parents
            if item["self_ref"]:
                found.append(error("cycle", "对象 %s 引用了自己" % item["id"],
                                   "definition", "去掉对自己的引用"))
            errors.extend(found)

        # 同批内按依赖排序：先写子对象再写父对象也能建起来
        ordered, cycle = _order_creates(creates)
        if cycle:
            errors.append(error("cycle", "这一批对象之间成环了：%s" % "、".join(cycle),
                                "operations", "去掉其中一个引用"))
        if errors:
            return self._fail(errors, warnings)

        created = []
        for item in ordered:
            self._insert(item["type"], item["id"], item["definition"], item["style"],
                         item["meta"], item["parents"], warnings)
            created.append(item["id"])
        updated = []
        removed = []
        removed_set = set()
        # 先把这一批新建对象的 children 建起来：同批里「建了子对象再删父对象」时，
        # 删除要顺着依赖把刚建好的子对象一起级联删掉，不能留下悬空引用。
        self._reindex()
        for item in others:
            if item["op"] == "update":
                outcome = self._apply_update(item["id"], item["fields"], warnings)
                if outcome is None:
                    self._apply_snapshot(before["state"])          # 原子回滚
                    if item["id"] not in self.state["objects"]:
                        return self._fail([error("not_found",
                                                 "对象 %s 已经被这一批里前面的删除连带删掉了"
                                                 % item["id"], "operations",
                                                 "别在同一批里既删父对象、又改它的后代")],
                                          warnings)
                    return self._fail([error("bad_value",
                                             "对象 %s 不能这样改（被冻结，或定义不合法）"
                                             % item["id"], "operations",
                                             "先解冻，或拆开一条条提交看看是哪条")],
                                      warnings)
            elif item["id"] not in self.state["objects"] and item["id"] in removed_set:
                continue          # 已经被同一批前面的级联删掉了：这一条算删成功，不报错
            else:
                outcome = self._apply_delete(item["id"], warnings)
            if outcome is None:
                self._apply_snapshot(before["state"])          # 理论到不了：原子回滚
                return self._fail([error("internal_error", "批量执行失败，已经整批回滚",
                                         "operations", "拆开一条条提交看看是哪条")],
                                  warnings)
            if item["op"] == "update":
                updated.append(item["id"])
            else:
                removed.extend(outcome)
                removed_set.update(outcome)
                self._reindex()
        self._reindex()
        touched = [oid for oid in created + updated if oid in self.state["objects"]]
        warnings.extend(self._evaluate(touched))
        if not created and not updated and not removed:
            return self._fail([error("missing_field", "这批操作什么都没做", "operations",
                                     "检查 op 和 id")], warnings)
        return self._commit(before, created=created, updated=updated, removed=removed,
                            warnings=warnings, explanation=_batch_explanation(
                                created, updated, removed))

    def update(self, id=None, **fields):
        """改一个对象：``definition`` / ``style`` / ``meta``（都是**浅合并**）。

        ``id`` / ``type`` / ``parents`` / ``children`` / ``value`` / ``seq`` 是只读的，
        改它们会拿到 ``immutable``；冻住的对象（``meta.frozen``）要先解冻。
        """
        before = self._mark()
        oid = str(id or "")
        obj = self.state["objects"].get(oid)
        if obj is None:
            return self._fail([error("not_found", "没有这个对象：%s" % (oid or "（空）"),
                                     "id", "先用 list_objects 看看有哪些对象")])
        if obj["meta"].get("frozen") and (set(fields) - {"meta"}):
            return self._fail([error("immutable", "对象 %s 被冻结了，不能改" % oid,
                                     "meta.frozen",
                                     "先 update(\"%s\", meta={\"frozen\": false}) 再改" % oid)])
        for key, value in fields.items():
            if key not in ("definition", "style", "meta"):
                return self._fail([error("immutable", "%s 是只读字段，不能改" % key, key,
                                         "只能改 definition / style / meta")])
            if not isinstance(value, dict):
                return self._fail([error("bad_value", "%s 要是一个字典" % key, key,
                                         "例如 definition={\"x\": 1}")])
        warnings = []
        outcome = self._apply_update(oid, fields, warnings)
        if outcome is None:
            return self._fail([error("bad_definition", "改完之后对象不合法，已经放弃",
                                     "definition", "检查必填字段")], warnings)
        self._reindex()
        warnings.extend(self._evaluate([oid]))
        return self._commit(before, updated=[oid], warnings=warnings,
                            explanation="已更新 %s" % oid)

    def delete(self, id=None):
        """删对象，**级联删掉依赖它的后代**，被连带删掉的 id 全在 ``removed`` 里（不静默）。"""
        before = self._mark()
        oid = str(id or "")
        if oid not in self.state["objects"]:
            return self._fail([error("not_found", "没有这个对象：%s" % (oid or "（空）"),
                                     "id", "先用 list_objects 看看有哪些对象")])
        warnings = []
        removed = self._apply_delete(oid, warnings)
        if removed is None:
            return self._fail([error("immutable", "有对象被冻结了，整次删除都没做", "meta.frozen",
                                     "先解冻，或者先删掉没冻的那个")], warnings)
        self._reindex()
        extra = len(removed) - 1
        explanation = "已删除 %s" % oid
        if extra > 0:
            explanation += "，并连带删除 %d 个依赖它的对象：%s" % (extra, "、".join(removed[1:]))
        return self._commit(before, removed=removed, warnings=warnings,
                            explanation=explanation)

    def _curve_bounds(self, two_d=False):
        """曲线采样用的范围：可见范围往两边各多给 ``AUTO_WINDOW_MARGIN`` 屏。

        屏幕上只看得见一小块，但曲线要"无限"：屏幕外也尽量多画一段，拖出去、放大
        之后线还在。网页端没报过可见范围时就用服务端自己的 view（默认 ±10）。
        采样点数按跨度等比放大 —— 范围大了点还是那么少，线就一节一节的。

        ``two_d=True`` 给隐式曲线 / 不等式区域用：它们是**二维**采样框，光把 x 拉长
        会又扁又稀（半径 2 的圆从 100 多点掉到 85 点）。所以这一路要网页端把
        x、y **两个方向**都报上来才启用；没报过就照旧用服务端 view 那一块。
        """
        view = dict(self.state["view"])
        window = self.state.get("window")
        if not isinstance(window, dict):
            window = {}
        if two_d and not (_has_range(window, "xmin", "xmax") and _has_range(window, "ymin", "ymax")):
            return view
        if _has_range(window, "xmin", "xmax"):
            low, high = float(window["xmin"]), float(window["xmax"])
        else:
            low, high = view["xmin"], view["xmax"]
        margin = (high - low) * AUTO_WINDOW_MARGIN
        view["xmin"], view["xmax"] = low - margin, high + margin
        if _has_range(window, "ymin", "ymax"):
            ylow, yhigh = float(window["ymin"]), float(window["ymax"])
            ymargin = (yhigh - ylow) * AUTO_WINDOW_MARGIN
            view["ymin"], view["ymax"] = ylow - ymargin, yhigh + ymargin
        base = DEFAULT_VIEW["xmax"] - DEFAULT_VIEW["xmin"]
        span = view["xmax"] - view["xmin"]
        if span > 0 and base > 0:
            view["samples"] = int(round(plane_math.FUNCTION_SAMPLES * span / base))
            # 二维那一路的格子也要跟着放大，不然网格被拉稀、线一节一节的。
            # 上限由 sample_implicit 自己夹（MAX_IMPLICIT_GRID），花费有底。
            view["grid"] = int(round(plane_math.IMPLICIT_GRID * span / base))
        return view

    def _keep_gliders_on_view_change(self):
        """换可见范围时，把挂在**自动区间**函数上的动点按当前 x 重算参数。

        自动区间的函数是按可见范围采样的，参数 t 的含义跟着视野走：视野一变，同一个 t
        就落到别的 x 上 —— 老师看到的是"缩放之后线段长度变了"（两个端点各自跑了一段，
        长度自然就变了；拖动中还可能突然移位）。这些点的 x 是老师拖出来的，视野变了不该动，
        所以按 x 反推新参数。钉了区间的函数、圆、弧、直线上的动点跟视野无关，不用碰。

        这是个不动点：x 本来就是按参数算出来的，重算一遍参数得到的是同一个值，
        所以视野没变时调用它没有任何副作用。
        """
        bounds = self._curve_bounds()
        low = bounds.get("xmin")
        high = bounds.get("xmax")
        if not (_is_number(low) and _is_number(high)) or float(high) <= float(low):
            return
        low = float(low)
        high = float(high)
        for obj in self.state["objects"].values():
            if obj["type"] != "point":
                continue
            definition = obj["definition"]
            host = self.state["objects"].get(definition.get("on"))
            if host is None or host["type"] != "function":
                continue
            host_definition = host["definition"]
            if _is_number(host_definition.get("xmin")) and _is_number(host_definition.get("xmax")):
                continue
            x = (obj.get("value") or {}).get("x")
            if not _is_number(x):
                continue
            parameter = (float(x) - low) / (high - low)
            definition["parameter"] = min(1.0, max(0.0, parameter))

    def set_window(self, xmin=None, xmax=None, ymin=None, ymax=None):
        """网页端把"现在看得见的范围"告诉服务端：自动区间的曲线按它重采样。

        只改"画多少"：**不加版本、不进撤销栈** —— 拖一下视野不该变成老师按一次
        "撤销"就退回去的东西。返回形状和 apply 一致，前端一条路处理。
        ymin/ymax 可以不报（老前端只报 x），那种情况下隐式曲线照旧按服务端 view 采。
        """
        if not (_is_number(xmin) and _is_number(xmax)) or not (float(xmax) > float(xmin)):
            return self._fail([error("bad_value", "窗口要写 xmin < xmax 的两个数", "window",
                                     "例如 {\"xmin\": -10, \"xmax\": 10}")])
        window = {"xmin": float(xmin), "xmax": float(xmax)}
        if ymin is not None or ymax is not None:
            if not (_is_number(ymin) and _is_number(ymax)) or not (float(ymax) > float(ymin)):
                return self._fail([error("bad_value", "窗口的 ymin / ymax 要么都不写，"
                                                    "要么写 ymin < ymax 的两个数", "window",
                                         "例如 {\"ymin\": -6, \"ymax\": 6}")])
            window["ymin"] = float(ymin)
            window["ymax"] = float(ymax)
        self.state["window"] = window
        self._keep_gliders_on_view_change()
        warnings = self._evaluate(None)
        return self._ok(updated=sorted(self.state["objects"]), warnings=warnings,
                        explanation="已按可见范围重新采样曲线")

    def set_view(self, **fields):
        """改视图（``zoom`` / ``pan`` / ``reset_view`` 都走这里）。隐式曲线跟着重采样。"""
        before = self._mark()
        view = dict(self.state["view"])
        for key, value in fields.items():
            if key not in DEFAULT_VIEW:
                return self._fail([error("bad_value", "视图里没有 %s" % key, "view." + key,
                                         "可用：" + "、".join(sorted(DEFAULT_VIEW)))])
            if key in ("grid", "axes", "polar"):
                view[key] = bool(value)
            else:
                if not _is_number(value):
                    return self._fail([error("bad_value", "view.%s 要是数字" % key,
                                             "view." + key, "例如 xmin = -10")])
                view[key] = float(value)
        for low, high in (("xmin", "xmax"), ("ymin", "ymax")):
            if view[high] <= view[low]:
                return self._fail([error("bad_value", "%s 要大于 %s" % (high, low),
                                         "view." + high, "别把范围写反了")])
        self.state["view"] = view
        warnings = self._evaluate(None)
        return self._commit(before, warnings=warnings, explanation="已调整视图")

    # -- 求值 --------------------------------------------------------------
    def evaluate(self):
        """整张图按拓扑序重算一遍（``loads`` / ``restore`` 之后用）。"""
        before = self._mark()
        warnings = self._evaluate(None)
        return self._commit(before, updated=list(self.state["order"]), warnings=warnings,
                            explanation="已按依赖图重算全部对象的取值")

    def _evaluate(self, ids):
        """只重算 ``ids`` 和它们的后代，父对象先算 —— 这就是"联动"。"""
        objects = self.state["objects"]
        if ids is None:
            affected = set(objects)
        else:
            affected = set()
            for oid in ids:
                if oid in objects:
                    affected.add(oid)
                    affected.update(self.descendants(oid))
        order = self.topological_order()
        warnings = []
        if not order["ok"]:
            warnings.append("依赖图上有环，只重算了能排出来的部分")
        for oid in order["order"]:
            if oid not in affected:
                continue
            obj = objects.get(oid)
            if obj is None:
                continue
            # ★ 轨迹是**累积**出来的：每轮重算都会覆盖 value，累积的点会被抹掉，所以跳过它，
            #   在下面统一追加（见 _accumulate_traces）。
            if obj["type"] == "trace":
                continue
            value = self._compute_value(obj)
            obj["value"] = value if isinstance(value, dict) else {}
            warning = _value_warning(oid, obj["value"])
            if warning:
                warnings.append(warning)
        warnings.extend(self._accumulate_traces(affected))
        return warnings

    def _accumulate_traces(self, affected):
        """追迹：把被追踪对象**当前**的位置追加进轨迹点表。

        老师拖一次动点这里就多一个点，拖一圈屏幕上就留下一条曲线 —— 这就是"运动轨迹"。
        结果同时写进 ``points`` 和 ``segments``：前端 drawLocus 读的是 segments
        （和函数曲线同一个形状，所以能复用同一段画线代码）。

        只有被追踪对象**这一轮真的重算过**才追加：否则拖 A 的时候会把 B 的旧位置
        重复记一遍，轨迹会糊成一团看不出形状。
        """
        objects = self.state["objects"]
        warnings = []
        for oid in self.state["order"]:
            obj = objects.get(oid)
            if obj is None or obj["type"] != "trace":
                continue
            target = (obj.get("definition") or {}).get("of")
            target_obj = objects.get(target) if target else None
            if target_obj is None:
                continue
            if target not in affected:
                continue
            spot = target_obj.get("value")
            if not isinstance(spot, dict):
                continue
            x, y = spot.get("x"), spot.get("y")
            if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
                continue
            value = obj.get("value") if isinstance(obj.get("value"), dict) else {}
            points = value.get("points")
            if not isinstance(points, list):
                points = []
            x, y = float(x), float(y)
            # 位置没变就不重复记（同一个点重算十次也只算一个）
            if not points or abs(points[-1][0] - x) > 1e-9 or abs(points[-1][1] - y) > 1e-9:
                points.append([x, y])
                if len(points) > TRACE_MAX_POINTS:
                    del points[:len(points) - TRACE_MAX_POINTS]
            obj["value"] = {"kind": "trace", "points": points, "segments": points,
                            "count": len(points),
                            "truncated": len(points) >= TRACE_MAX_POINTS}
        return warnings

    def _compute_value(self, obj):
        kind = obj["type"]
        if kind in ("centroid", "incenter", "orthocenter"):
            return self._value_triangle_center(obj)
        if kind == "point":
            return self._value_point(obj)
        if kind in ("segment", "ray", "line", "vector"):
            return self._value_path(obj)
        if kind in ("perpendicular", "parallel", "perpendicular_bisector",
                    "angle_bisector", "tangent"):
            return self._value_constructed_line(obj)
        if kind == "circle":
            return self._value_circle(obj)
        if kind in ("arc", "sector"):
            return self._value_arc(obj)
        if kind == "polygon":
            return self._value_polygon(obj)
        if kind == "regular_polygon":
            return self._value_regular_polygon(obj)
        if kind == "angle":
            return self._value_angle(obj)
        if kind in ("distance", "length", "area", "slope", "radius", "measure"):
            return self._value_measure(obj)
        if kind in plane_math.TYPES_WITH_CURVES:
            return self._value_curve(obj)
        if kind in ("slider", "number"):
            return {"value": float(obj["definition"]["value"])}
        if kind == "boolean":
            return {"value": bool(obj["definition"]["value"])}
        if kind == "text":
            return {"text": obj["definition"]["text"]}
        return {}

    def _point_of(self, oid):
        """父点的坐标（父对象已经算过了，拓扑序保证的）。"""
        obj = self.state["objects"].get(oid)
        if obj is None:
            return None
        value = obj.get("value") or {}
        return plane_math.as_pair(value)

    def _value_triangle_center(self, obj):
        """三角形的重心 / 内心 / 垂心（都是「点」）。

        三个顶点由 definition 的 a / b / c 给出（顺序无所谓，求心只看这三个点）。
        三点共线时内心、垂心都没有意义 —— 必须回结构化错误，不能悄悄给一个假点，
        不然老师会看到「内心跑到三角形的一条边上」。重心不受影响：共线的三点
        照样有重心（就在那条线上），这是课本定义。
        """
        kind = obj["type"]
        definition = obj["definition"]
        spots = [self._point_of(definition.get(field)) for field in ("a", "b", "c")]
        if any(spot is None for spot in spots):
            return {"error": error("no_value", "%s 要看三个顶点" % kind,
                                   "definition.a", "先让三个顶点求值成功")}
        (ax, ay), (bx, by), (cx, cy) = spots[0], spots[1], spots[2]
        if kind == "centroid":
            if max(abs(ax - bx), abs(ay - by), abs(ax - cx), abs(ay - cy),
                   abs(bx - cx), abs(by - cy)) < 1e-12:
                return {"error": error("degenerate", "三个顶点重合，重心定不下来",
                                       "definition.a", "把三个顶点分开")}
            return {"x": (ax + bx + cx) / 3.0, "y": (ay + by + cy) / 3.0}
        # 有向面积的两倍：三点共线时为 0
        area2 = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
        label = "内心" if kind == "incenter" else "垂心"
        if abs(area2) < 1e-12:
            return {"error": error("degenerate", "三点共线，%s定不下来" % label,
                                   "definition.a", "三个顶点不能排成一条直线")}
        if kind == "incenter":
            # 内心 = 按**对边**长度加权平均：(a·A + b·B + c·C) / (a+b+c)
            side_a = math.hypot(cx - bx, cy - by)
            side_b = math.hypot(ax - cx, ay - cy)
            side_c = math.hypot(bx - ax, by - ay)
            total = side_a + side_b + side_c
            return {"x": (side_a * ax + side_b * bx + side_c * cx) / total,
                    "y": (side_a * ay + side_b * by + side_c * cy) / total}
        # 垂心用 H = A + B + C − 2O 算（O 是外心）：比解两条高线的交点稳 ——
        # 高线垂直于某条边时斜率不存在，解方程会碰上除零，这个式子不会。
        circle = _circumcircle(spots)
        if circle is None:
            return {"error": error("degenerate", "三点共线，垂心定不下来",
                                   "definition.a", "三个顶点不能排成一条直线")}
        ox, oy = circle[0]
        return {"x": ax + bx + cx - 2.0 * ox, "y": ay + by + cy - 2.0 * oy}

    def _value_point(self, obj):
        definition = obj["definition"]
        if "intersect" in definition:
            objects = self.state["objects"]
            first = objects.get(definition["intersect"][0])
            second = objects.get(definition["intersect"][1])
            index = definition.get("index", 0)
            outcome = plane_math.intersection(first, second, index, objects)
            # 「就近取交点」只在**建的时候**算一次，然后把选中的 index 钉进定义里。
            # 不能每次求值都按 near 重挑：直线一转，离那个旧点击位置最近的分支就换了，
            # 老师看到的是"交点突然跳回顶点 / 不见了"（抛物线顶点那条经典性质就是这样）。
            if isinstance(definition.get("near"), dict) and "index" not in definition:
                chosen = _nearest_index(definition["near"], outcome.get("points"), index)
                if chosen != index:
                    index = chosen
                    outcome = plane_math.intersection(first, second, index, objects)
                if outcome.get("points"):
                    definition["index"] = index       # 归一化：之后一直按这个分支走
            out = {"intersect": list(definition["intersect"]), "index": index,
                   "source": outcome.get("source", ""), "points": outcome.get("points", [])}
            if outcome.get("reason"):
                out["reason"] = outcome["reason"]
            if outcome.get("ok"):
                point = plane_math.as_pair(outcome.get("point"))
                if point is not None:
                    out["x"], out["y"] = point[0], point[1]
                    return out
            out["error"] = outcome.get("error") or error("no_intersection", "算不出交点",
                                                        "definition.intersect")
            return out
        if "on" in definition:
            parent = self.state["objects"].get(definition["on"])
            t = definition.get("parameter", 0.5)
            outcome = plane_math.point_on_object(parent, t, self.variables())
            out = {"on": definition["on"], "parameter": t}
            if outcome.get("ok"):
                point = plane_math.as_pair(outcome.get("point"))
                if point is not None:
                    out["x"], out["y"] = point[0], point[1]
                    return out
            out["error"] = outcome.get("error") or error("no_value", "动点算不出来",
                                                         "definition.on")
            return out
        x, y = definition["x"], definition["y"]
        if not (_is_number(x) and _is_number(y)):
            return {"error": error("bad_value", "点的坐标不是有限的数", "definition.x")}
        return {"x": float(x), "y": float(y)}

    def _value_path(self, obj):
        definition = obj["definition"]
        start = self._point_of(definition["start"])
        end = self._point_of(definition["end"])
        if start is None or end is None:
            return {"error": error("no_value", "端点还没有坐标", "definition.start",
                                   "先让父对象求值成功")}
        out = {"start": {"x": start[0], "y": start[1]},
               "end": {"x": end[0], "y": end[1]}}
        if obj["type"] in ("line", "ray", "vector"):
            direction = [end[0] - start[0], end[1] - start[1]]
            if abs(direction[0]) < 1e-12 and abs(direction[1]) < 1e-12:
                return {"error": error("degenerate", "两端的点重合了", "definition.end",
                                       "换一个不重合的点")}
            out.update(_line_value(start, direction))
        elif obj["type"] == "segment":
            direction = [end[0] - start[0], end[1] - start[1]]
            if abs(direction[0]) < 1e-12 and abs(direction[1]) < 1e-12:
                return {"error": error("degenerate", "线段的两个端点重合了",
                                       "definition.end", "换一个不重合的点")}
            out.update(_line_value(start, direction))
        measured = plane_math.exact_distance(start, end)
        if measured.get("ok"):
            out["length"] = measured["value"]
            out["lengthExact"] = measured["exact"] or measured["decimal"]
        return out

    def _value_constructed_line(self, obj):
        kind = obj["type"]
        definition = obj["definition"]
        # 切线且父对象是**曲线**：用导数当斜率。必须排在下面圆的判断之前 ——
        # 函数曲线没有 centre，走圆那条只会得到"切线要看圆和切点"，
        # 而老师点的是曲线上的动点（他报的"切线丢了"就是这个）。
        if kind == "tangent":
            target = self.state["objects"].get(definition.get("to"))
            if target is not None and target.get("type") in plane_math.TYPES_WITH_CURVES:
                spot = self._point_of(definition.get("at"))
                if spot is None:
                    return {"error": error("no_value", "切线要知道切在哪一点", "definition.at",
                                           "先点一个曲线上的动点")}
                if target.get("type") == "function":
                    # 显函数还是老路：符号求导，算不出来就报错（不拿 0 当斜率）
                    expr = (target.get("definition") or {}).get("expr")
                    slope = _tangent_slope(expr, spot[0])
                    if slope is None:
                        return {"error": error(
                            "no_value",
                            "这条曲线在这一点算不出切线：只支持 y = f(x) 这种函数的导数",
                            "definition.to",
                            "把曲线写成 y = ... 的形式（例如 x^2/3 或 sin(x)），"
                            "或者换一条曲线")}
                    return _line_value(spot, [1.0, slope])
                # 方程 / 参数 / 极坐标曲线：**切向量**而不是斜率。
                # F_y = 0 的地方切线是竖直的，斜率不存在 —— 用斜率当接口必定算出
                # inf/NaN，或者画出一条假的水平线（见 plane_math.tangent_direction）。
                aimed = plane_math.tangent_direction(target, spot, self.variables())
                if not aimed.get("ok"):
                    return {"error": aimed.get("error") or
                            error("no_value", "曲线在这一点算不出切线方向", "definition.to",
                                  "换一个切点试试")}
                return _line_value(spot, aimed["direction"])
        if kind in ("perpendicular", "parallel"):
            foot = self._point_of(definition["through"])
            target = self.state["objects"].get(definition["to"])
            line = plane_math.line_of((target or {}).get("value") or {})
            if foot is None or line is None:
                return {"error": error("no_value", "%s 还算不出来（要看穿过的点和目标直线）"
                                                 % kind, "definition.to",
                                       "先让父对象求值成功")}
            direction = line["direction"]
            if kind == "perpendicular":
                direction = [-direction[1], direction[0]]
            return _line_value(foot, direction)
        if kind == "perpendicular_bisector":
            first = self._point_of(definition["a"])
            second = self._point_of(definition["b"])
            if first is None or second is None:
                return {"error": error("no_value", "中垂线要看两个点", "definition.a")}
            middle = [(first[0] + second[0]) / 2.0, (first[1] + second[1]) / 2.0]
            direction = [-(second[1] - first[1]), second[0] - first[0]]
            if abs(direction[0]) < 1e-12 and abs(direction[1]) < 1e-12:
                return {"error": error("degenerate", "两个点重合，中垂线定不下来",
                                       "definition.b", "换一个不重合的点")}
            return _line_value(middle, direction)
        if kind == "angle_bisector":
            first = self._point_of(definition["a"])
            vertex = self._point_of(definition["b"])
            third = self._point_of(definition["c"])
            if first is None or vertex is None or third is None:
                return {"error": error("no_value", "角平分线要看三个点", "definition.a")}
            ux, uy = first[0] - vertex[0], first[1] - vertex[1]
            vx, vy = third[0] - vertex[0], third[1] - vertex[1]
            norm_u, norm_v = math.hypot(ux, uy), math.hypot(vx, vy)
            if norm_u < 1e-12 or norm_v < 1e-12:
                return {"error": error("degenerate", "角的两条边有一条退化了",
                                       "definition.b", "检查三个点的坐标")}
            direction = [ux / norm_u + vx / norm_v, uy / norm_u + vy / norm_v]
            if abs(direction[0]) < 1e-12 and abs(direction[1]) < 1e-12:
                direction = [-uy / norm_u, ux / norm_u]      # 平角：取垂直方向
            return _line_value(vertex, direction)
        circle_obj = self.state["objects"].get(definition["to"])
        circle = plane_math.circle_of((circle_obj or {}).get("value") or {})
        at = self._point_of(definition["at"])
        if circle is None or at is None:
            return {"error": error("no_value", "切线要看圆和切点", "definition.at",
                                   "先让父对象求值成功")}
        radius = [at[0] - circle["center"][0], at[1] - circle["center"][1]]
        if abs(radius[0]) < 1e-12 and abs(radius[1]) < 1e-12:
            return {"error": error("degenerate", "切点就是圆心，切线定不下来",
                                   "definition.at", "把切点挪开")}
        return _line_value(at, [-radius[1], radius[0]])

    def _value_circle(self, obj):
        definition = obj["definition"]
        center = None
        radius = None
        if "center" in definition:
            center = self._point_of(definition["center"])
            if center is None:
                return {"error": error("no_value", "圆心还没有坐标", "definition.center",
                                       "先让父对象求值成功")}
        if "radius" in definition:
            radius = float(definition["radius"])
        elif "through" in definition:
            through = definition["through"]
            if isinstance(through, str):
                other = self._point_of(through)
                if other is None:
                    return {"error": error("no_value", "圆上的点还没有坐标",
                                           "definition.through", "先让父对象求值成功")}
                measured = plane_math.exact_distance(center, other)
                if not measured.get("ok"):
                    return {"error": measured["error"]}
                radius = measured["value"]
            else:
                points = [self._point_of(item) for item in through]
                if any(point is None for point in points):
                    return {"error": error("no_value", "三点定圆要先有三个点",
                                           "definition.through", "先让父对象求值成功")}
                outcome = _circumcircle(points)
                if outcome is None:
                    return {"error": error("degenerate", "三点共线，画不出外接圆",
                                           "definition.through", "换三个不共线的点")}
                center, radius = outcome
        if center is None or radius is None:
            return {"error": error("missing_field", "圆缺圆心或半径", "definition.center")}
        measured = plane_math.number_result(radius)
        out = {"center": {"x": center[0], "y": center[1]}, "radius": radius}
        if measured.get("ok"):
            out["radiusExact"] = measured["exact"] or ""
            out["radiusDecimal"] = measured["decimal"]
        return out

    def _value_arc(self, obj):
        definition = obj["definition"]
        center = self._point_of(definition["center"])
        first = self._point_of(definition["from"])
        second = self._point_of(definition["to"])
        if center is None or first is None or second is None:
            return {"error": error("no_value", "弧要看圆心和两个端点", "definition.center")}
        radius = math.hypot(first[0] - center[0], first[1] - center[1])
        if radius <= 0:
            return {"error": error("degenerate", "半径是 0，画不出弧", "definition.from")}
        start = math.atan2(first[1] - center[1], first[0] - center[0])
        end = math.atan2(second[1] - center[1], second[0] - center[0])
        if end <= start:
            end += 2 * math.pi
        out = {"center": {"x": center[0], "y": center[1]}, "radius": radius,
               "startAngle": start, "endAngle": end,
               "from": {"x": first[0], "y": first[1]},
               "to": {"x": second[0], "y": second[1]}}
        if obj["type"] == "sector":
            out["area"] = round(0.5 * radius * radius * (end - start), 6)
        path = []
        for index in range(ROUND_SEGMENTS + 1):
            angle = start + (end - start) * index / float(ROUND_SEGMENTS)
            path.append([round(center[0] + radius * math.cos(angle), 4),
                         round(center[1] + radius * math.sin(angle), 4)])
        out["path"] = path
        return out

    def _value_polygon(self, obj):
        points = [self._point_of(oid) for oid in obj["definition"]["points"]]
        if any(point is None for point in points):
            return {"error": error("no_value", "还有顶点没算出来", "definition.points",
                                   "先让父对象求值成功")}
        out = {"points": [{"x": point[0], "y": point[1]} for point in points]}
        measured = plane_math.exact_area(points)
        if measured.get("ok"):
            out["area"] = measured["value"]
            out["areaExact"] = measured["exact"] or measured["decimal"]
        perimeter = 0.0
        for index in range(len(points)):
            perimeter += math.hypot(points[index][0] - points[(index + 1) % len(points)][0],
                                    points[index][1] - points[(index + 1) % len(points)][1])
        out["perimeter"] = perimeter
        return out

    def _value_regular_polygon(self, obj):
        definition = obj["definition"]
        first = self._point_of(definition["a"])
        second = self._point_of(definition["b"])
        if first is None or second is None:
            return {"error": error("no_value", "正多边形要看两个点", "definition.a")}
        sides = int(definition.get("n", 5))
        dx, dy = second[0] - first[0], second[1] - first[1]
        if abs(dx) < 1e-12 and abs(dy) < 1e-12:
            return {"error": error("degenerate", "两个点重合了", "definition.b")}
        points = []
        for index in range(sides):
            angle = 2 * math.pi * index / float(sides)
            cos_a, sin_a = math.cos(angle), math.sin(angle)
            points.append([first[0] + dx * cos_a - dy * sin_a,
                           first[1] + dx * sin_a + dy * cos_a])
        out = {"points": [{"x": point[0], "y": point[1]} for point in points],
               "n": sides}
        measured = plane_math.exact_area(points)
        if measured.get("ok"):
            out["area"] = measured["value"]
            out["areaExact"] = measured["exact"] or measured["decimal"]
        return out

    def _value_angle(self, obj):
        definition = obj["definition"]
        first = self._point_of(definition["a"])
        vertex = self._point_of(definition["b"])
        third = self._point_of(definition["c"])
        if first is None or vertex is None or third is None:
            return {"error": error("no_value", "角要看三个点", "definition.a",
                                   "先让父对象求值成功")}
        measured = plane_math.exact_angle(first, vertex, third)
        if not measured.get("ok"):
            return {"error": measured["error"]}
        return {"degrees": measured["value"], "radians": measured["radians"],
                "sin": measured.get("sin", ""), "cos": measured.get("cos", ""),
                "sinValue": measured.get("sinValue"), "cosValue": measured.get("cosValue"),
                "exact": measured.get("exact", ""), "decimal": measured["decimal"],
                "approximate": measured.get("approximate", True)}

    def _value_measure(self, obj):
        kind = obj["type"]
        definition = obj["definition"]
        if kind == "measure":
            kind = str(definition.get("kind") or "").lower()
            args = [self.state["objects"].get(oid) for oid in definition.get("args", [])]
            points = [self._point_of(oid) for oid in definition.get("args", [])]
            return self._measure_value(kind, args, points, definition.get("args", []))
        targets = definition["of"]
        targets = [targets] if isinstance(targets, str) else list(targets)
        args = [self.state["objects"].get(oid) for oid in targets]
        points = [self._point_of(oid) for oid in targets]
        return self._measure_value(kind, args, points, targets)

    def _measure_value(self, kind, args, points, ids):
        """度量：复用 plane_math（和计算器面板同一套 Exact，值是一样的）。"""
        known = [point for point in points if point is not None]
        if kind == "distance":
            if len(known) >= 2:
                return _measure_out("distance", ids, plane_math.exact_distance(known[0], known[1]))
            return _measure_out(kind, ids, _measure_failure(
                "missing_field", "距离要两个点或一条线段", "definition.of",
                "写成 {of: [点, 点]} 或 {of: 线段}"))
        if kind == "length":
            if len(known) >= 2:
                return _measure_out("length", ids, plane_math.exact_distance(known[0], known[1]))
            total = 0.0
            for target in args:
                value = (target or {}).get("value") or {}
                if _is_number(value.get("length")):
                    total += value["length"]
                elif _is_number(value.get("perimeter")):
                    total += value["perimeter"]
            return _measure_out("length", ids, plane_math.number_result(total))
        if kind == "area":
            target = args[0] if args else None
            value = (target or {}).get("value") or {}
            if _is_number(value.get("area")):
                return _measure_out("area", ids, plane_math.number_result(value["area"]))
            if len(known) >= 3:
                return _measure_out("area", ids, plane_math.exact_area(known))
            return _measure_out("area", ids, _measure_failure(
                "missing_field", "面积要一个多边形/圆或至少三个点", "definition.of"))
        if kind in ("angle", "lineangle"):
            # 两条线所成的角（取锐角）：和空间那边的线线角同一套算法 ——
            # calc.Exact 出"分数 + 根号"，特殊角查表，算不出精确值时只给小数值。
            lines = [arg for arg in args if arg is not None
                     and plane_math.line_of((arg.get("value") or {})) is not None]
            if len(lines) >= 2:
                measured = plane_math.exact_line_angle(lines[0].get("value") or {},
                                                       lines[1].get("value") or {})
            elif len(known) >= 3:
                measured = plane_math.exact_angle(known[0], known[1], known[2])
            else:
                return _measure_out(kind, ids, _measure_failure(
                    "missing_field", "角要两条线（或者三个点）", "definition.args",
                    "写成 {kind: \"angle\", args: [线1, 线2]} 或 [点A, 顶点, 点C]"))
            out = _measure_out(kind, ids, measured)
            if measured.get("ok"):
                out.update({"degrees": measured["value"], "radians": measured.get("radians"),
                            "sin": measured.get("sin", ""), "cos": measured.get("cos", ""),
                            "sinValue": measured.get("sinValue"),
                            "cosValue": measured.get("cosValue"),
                            "approximate": measured.get("approximate", True)})
            return out
        if kind == "slope":
            if len(known) >= 2:
                return _measure_out("slope", ids, plane_math.exact_slope(known[0], known[1]))
            target = args[0] if args else None
            value = (target or {}).get("value") or {}
            direction = plane_math.as_pair(value.get("direction"))
            if direction is not None and direction[0] != 0:
                return _measure_out("slope", ids, plane_math.number_result(
                    direction[1] / direction[0]))
            return _measure_out("slope", ids, _measure_failure(
                "no_slope", "这条线给不出斜率（竖直或没算出来）", "definition.of"))
        if kind == "radius":
            target = args[0] if args else None
            value = (target or {}).get("value") or {}
            if _is_number(value.get("radius")):
                return _measure_out("radius", ids, plane_math.number_result(value["radius"]))
            if len(known) >= 2:
                return _measure_out("radius", ids, plane_math.exact_distance(known[0], known[1]))
            return _measure_out("radius", ids, _measure_failure(
                "missing_field", "半径要一个圆", "definition.of"))
        return _measure_out(kind, ids, _measure_failure(
            "unknown_measure", "不认识的度量：%s" % (kind or "（空）"), "definition.of"))

    def _value_curve(self, obj):
        """曲线对象：采样点直接放进 ``value``，前端拿到 state 就能画。"""
        # 显函数是**一维**采样，"自动区间 + 多采几屏"最省事；
        # 隐式曲线 / 不等式区域是二维采样框，走 two_d 那一路（要 x、y 都报上来才用，
        # 见 _curve_bounds）：只有服务端 view 时保持原样，免得把框拉大之后线变稀。
        bounds = self._curve_bounds() if obj["type"] == "function" \
            else self._curve_bounds(two_d=True)
        outcome = plane_math.sample_object(obj, bounds, self.variables())
        kind = outcome.get("kind") or obj["type"]
        if not outcome.get("ok"):
            return {"kind": kind, "segments": [], "regions": [],
                    "error": outcome.get("error") or error("empty_curve", "采样失败",
                                                           "definition.expr")}
        value = {"kind": kind, "segments": outcome["segments"],
                 "regions": outcome["regions"], "bbox": outcome["bbox"],
                 "points": outcome["points"], "truncated": outcome["truncated"],
                 "samples": outcome["samples"]}
        for key in ("cell", "cols", "rows", "area"):
            if key in outcome:
                value[key] = outcome[key]
        return value

    # -- 快照 / 撤销 --------------------------------------------------------
    def snapshot(self):
        """当前状态的深拷贝（可 JSON 化）：``version`` / ``view`` / ``objects`` / ``order``。"""
        return {"version": self.state["version"], "view": copy.deepcopy(self.state["view"]),
                "objects": copy.deepcopy(self.state["objects"]),
                "order": list(self.state["order"])}

    def dumps(self, indent=None):
        """状态转 JSON 文本（**不用 pickle**）。撤销栈不进 JSON（内部用，不对外暴露）。"""
        return json.dumps(self.snapshot(), ensure_ascii=False, indent=indent)

    @classmethod
    def loads(cls, text, workspace=None):
        """JSON 文本转 :class:`Plane`。**loads 之后会按依赖图重算一遍 value**。"""
        try:
            state = json.loads(text)
        except ValueError as failure:
            raise ValueError("平面状态不是合法 JSON：%s" % failure)
        return cls(workspace=workspace, state=state)

    def history(self):
        """还能回到哪些 stateVersion（升序）。``restore`` 只认这张表里的版本。"""
        versions = set([self.state["version"]])
        versions.update(entry["version"] for entry in self._versions)
        return sorted(versions)

    def restore(self, target):
        """回到任意 ``stateVersion``（也可以直接给 ``snapshot()`` 的结果）。

        ``version`` 是**只增不减**的：回滚后当前 ``stateVersion`` 是新号，内容才是旧的，
        所以前端凭版本号判断新旧永远成立。回去这一步本身也能再被 ``undo`` 撤销。
        """
        before = self._mark()
        snapshot = None
        if isinstance(target, dict) and isinstance(target.get("objects"), dict):
            snapshot = target
        else:
            try:
                version = int(target)
            except (TypeError, ValueError):
                return self._fail([error("bad_value", "restore 要一个 stateVersion 或 snapshot",
                                         "version", "先 snapshot 拿到版本号")])
            if version == self.state["version"]:
                snapshot = self.snapshot()
            else:
                for entry in reversed(self._versions):
                    if entry["version"] == version:
                        snapshot = entry["state"]
                        break
            if snapshot is None:
                return self._fail([error("not_found", "回不到版本 %d" % version, "version",
                                         "只能回到 %s（撤销栈最多记 %d 步）"
                                         % (self.history(), MAX_UNDO))])
        try:
            self._apply_snapshot(snapshot)
        except ValueError as failure:
            return self._fail([error("bad_definition", "这份快照不能用：%s" % failure,
                                     "version", "换一个版本")])
        self._reindex()
        warnings = self._evaluate(None)
        return self._commit(before, updated=list(self.state["order"]), warnings=warnings,
                            explanation="已回到版本 %s 的内容" % snapshot.get("version"))

    def undo(self):
        """撤销上一步（含批量操作，整批一起退；撤销本身不清空重做栈）。"""
        before = self._mark()
        if not self.state["undo"]:
            return self._fail([error("not_found", "没有可以撤销的操作", "undo",
                                     "已经是最早的状态了")])
        entry = self.state["undo"].pop()
        self.state["redo"].append(self._record(self.state["version"], self._undo_snapshot()))
        self._apply_snapshot(entry["state"])
        self._reindex()
        warnings = self._evaluate(None)
        return self._commit(before, updated=list(self.state["order"]), warnings=warnings,
                            keep_history=True,
                            explanation="已撤销，回到版本 %d 的内容" % entry["version"])

    def redo(self):
        """重做（新的修改会清空重做栈）。"""
        before = self._mark()
        if not self.state["redo"]:
            return self._fail([error("not_found", "没有可以重做的操作", "redo",
                                     "重做栈是空的")])
        entry = self.state["redo"].pop()
        self.state["undo"].append(self._record(self.state["version"], self._undo_snapshot()))
        self._apply_snapshot(entry["state"])
        self._reindex()
        warnings = self._evaluate(None)
        return self._commit(before, updated=list(self.state["order"]), warnings=warnings,
                            keep_history=True, explanation="已重做")

    # -- 内部：状态维护 -----------------------------------------------------
    def _mark(self):
        return {"version": self.state["version"], "state": self._undo_snapshot()}

    def _undo_snapshot(self):
        """撤销栈里的状态：只留"输入"（definition/style/meta），不留算出来的 ``value``。

        撤销之后反正要按依赖图重算，而曲线采样值很占地方（一条曲线几百个点，
        100 层撤销栈会变成几十兆）。重算出来的 value 和原来一模一样。
        """
        objects = {}
        for oid, obj in self.state["objects"].items():
            light = dict(obj)
            light["definition"] = copy.deepcopy(obj["definition"])
            light["style"] = dict(obj["style"])
            light["meta"] = dict(obj["meta"])
            light["parents"] = list(obj["parents"])
            light["children"] = list(obj["children"])
            light["value"] = {}
            objects[oid] = light
        return {"version": self.state["version"], "view": copy.deepcopy(self.state["view"]),
                "objects": objects, "order": list(self.state["order"])}

    def _record(self, version, snapshot):
        """记一条"版本号对内容快照"，``restore`` 靠它回滚任意版本（只受 MAX_UNDO 限制）。

        撤销/重做这两个栈和它是**同一批字典对象**（引用共享，不额外占内存）。
        """
        entry = {"version": version, "state": snapshot}
        self._versions.append(entry)
        if len(self._versions) > MAX_UNDO:
            del self._versions[0]
        return entry

    def _commit(self, before, created=None, updated=None, removed=None, warnings=None,
                explanation="", handle=None, keep_history=False):
        """提交一次变更：``version + 1``，并把"变更前的内容"记进历史。

        ``keep_history=True`` 是 ``undo`` / ``redo`` 用的：栈由它们自己维护，
        这里只推版本号，**不清空重做栈**（否则撤销一次就再也重做不回来了）。
        """
        version = before["version"] + 1
        self.state["version"] = version
        entry = self._record(before["version"], before["state"])
        if not keep_history:
            self.state["undo"].append(entry)
            if len(self.state["undo"]) > MAX_UNDO:
                del self.state["undo"][0]
            self.state["redo"] = []                  # 新的修改清空重做栈
        return self._ok(created, updated, removed, warnings, explanation, handle)

    def _ok(self, created=None, updated=None, removed=None, warnings=None,
            explanation="", handle=None):
        version = self.state["version"]
        return {"ok": True, "stateVersion": version,
                "created": list(created or []), "updated": list(updated or []),
                "removed": list(removed or []), "warnings": _clip(warnings),
                "errors": [], "explanation": explanation,
                "handle": handle or ("undo:%d" % (version - 1))}

    def _fail(self, errors, warnings=None, explanation=""):
        """失败结构：比冻结件多了 ``created/updated/removed/handle`` 三个空键（超集）。"""
        if isinstance(errors, dict):
            errors = [errors]
        return {"ok": False, "stateVersion": self.state["version"], "created": [],
                "updated": [], "removed": [], "warnings": _clip(warnings),
                "errors": list(errors or []), "explanation": explanation, "handle": ""}

    def _apply_snapshot(self, snapshot):
        """换内容（view / objects / order），不动撤销栈。"""
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get("objects"), dict):
            raise ValueError("快照里没有 objects")
        objects = {}
        for oid, raw in snapshot["objects"].items():
            if not isinstance(raw, dict) or not isinstance(raw.get("type"), str):
                raise ValueError("对象 %s 的形状不对" % oid)
            objects[str(oid)] = _normalize_object(str(oid), raw)
        order = []
        for oid in snapshot.get("order") or []:
            if oid in objects and oid not in order:
                order.append(oid)
        for oid in sorted(objects, key=lambda item: (objects[item]["seq"], item)):
            if oid not in order:
                order.append(oid)
        view = dict(DEFAULT_VIEW)
        raw_view = snapshot.get("view")
        if isinstance(raw_view, dict):
            for key in DEFAULT_VIEW:
                if key in raw_view:
                    view[key] = raw_view[key]
        self.state["objects"] = objects
        self.state["order"] = order
        self.state["view"] = view
        self._seq = max([obj["seq"] for obj in objects.values()] or [0])
        for obj in objects.values():                 # parents 以 definition 为准（自愈）
            known = set(objects)
            parents = [parent for parent in extract_parents(obj["type"], obj["definition"],
                                                            known) if parent != obj["id"]]
            if parents != obj["parents"]:
                obj["parents"] = parents

    def _adopt(self, state):
        """``__init__`` / ``loads`` 用：换内容 + 重置撤销栈 + 重算取值。"""
        if not isinstance(state, dict):
            raise ValueError("平面状态要是一个字典")
        self._apply_snapshot(state)
        try:
            self.state["version"] = max(0, int(state.get("version", 0)))
        except (TypeError, ValueError):
            self.state["version"] = 0
        self.state["undo"] = []
        self.state["redo"] = []
        self._versions = []
        self._reindex()
        if self.topological_order()["ok"]:
            self._evaluate(None)

    def _insert(self, kind, oid, definition, style, meta, parents, warnings=None):
        self._seq = min(SEQ_LIMIT, self._seq + 1)
        source = "agent"
        if isinstance(meta, dict) and meta.get("source") in SOURCE_KINDS:
            source = meta["source"]
        obj = make_object(oid, kind, definition, style, meta, self._seq,
                          parents=parents, source=source, warnings=warnings)
        self.state["objects"][oid] = obj
        self.state["order"].append(oid)
        if kind in ("slider", "number") and not _IDENTIFIER.match(oid):
            (warnings if isinstance(warnings, list) else []).append(
                "%s 的 id「%s」不能写在表达式里（只认 ASCII 字母、数字、下划线），"
                "滑动条联动会失效" % (kind, oid))
        return obj

    def _apply_update(self, oid, fields, warnings):
        """改一个对象的内容（不提交版本、不重算）。失败返回 None。

        冻结的对象只允许改 ``meta``（也就只有"解冻"这一步走得通）。
        """
        obj = self.state["objects"].get(oid)
        if obj is None:
            return None
        if obj["meta"].get("frozen") and (set(fields) - {"meta"}):
            return None
        if any(key not in ("definition", "style", "meta") for key in fields):
            return None
        definition = dict(obj["definition"])
        for key, value in (fields.get("definition") or {}).items():
            # null = 把这个字段删掉（前端把"起点/终点"清空就是让函数回到自动区间）
            if value is None:
                definition.pop(key, None)
            else:
                definition[key] = value
        known = set(self.state["objects"])
        parents, errors = validate_definition(obj["type"], definition, known, warnings)
        if errors:
            warnings.append(errors[0]["message"])
            return None
        if oid in parents:
            warnings.append("对象 %s 不能引用自己" % oid)
            return None
        for parent in parents:
            if oid in self._ancestors(parent):
                warnings.append("这样会形成环：%s 依赖 %s" % (oid, parent))
                return None
        style = dict(obj["style"])
        style.update(_as_mapping(fields.get("style")))
        meta = dict(obj["meta"])
        meta.update(_as_mapping(fields.get("meta")))
        obj["definition"] = definition
        obj["style"] = _clean_style(style, warnings)
        obj["meta"] = _clean_meta(meta, warnings, obj["meta"].get("source", "agent"))
        obj["parents"] = [parent for parent in parents if parent != oid]
        return obj

    def _dependents(self, oid):
        """所有直接/间接依赖 ``oid`` 的对象（按创建顺序）。

        故意用 ``parents`` 反查，而不用 ``children`` 缓存：同一批里"先建子对象、
        再删父对象"时，那个刚建好的子对象的 ``children`` 还没重建过，
        靠缓存就会漏掉它，删完父对象留下悬空引用。
        """
        objects = self.state["objects"]
        children = {}
        for item, obj in objects.items():
            for parent in obj["parents"]:
                children.setdefault(parent, []).append(item)
        seen = set()
        stack = list(children.get(oid, ()))
        while stack:
            current = stack.pop()
            if current == oid or current in seen or current not in objects:
                continue
            seen.add(current)
            stack.extend(children.get(current, ()))
        return [item for item in self.state["order"] if item in seen]

    def _apply_delete(self, oid, warnings):
        """删掉对象 + 它的后代，返回被删的 id 列表（父对象在前）。失败返回 None。"""
        if oid not in self.state["objects"]:
            return None
        removed = [oid] + self._dependents(oid)
        for item in removed:
            if (self.state["objects"].get(item) or {}).get("meta", {}).get("frozen"):
                warnings.append("对象 %s 被冻结了，不能删" % item)
                return None
        for item in removed:
            self.state["objects"].pop(item, None)
        self.state["order"] = [item for item in self.state["order"] if item not in removed]
        return removed

    def _reindex(self):
        """重算 ``children``（由 Plane 维护，调用方不写）与 ``order``。"""
        objects = self.state["objects"]
        for obj in objects.values():
            obj["children"] = []
        for oid in list(self.state["order"]):
            obj = objects.get(oid)
            if obj is None:
                self.state["order"].remove(oid)
                continue
            for parent in obj["parents"]:
                parent_obj = objects.get(parent)
                if parent_obj is not None and parent != oid:
                    parent_obj["children"].append(oid)

    def _ancestors(self, oid):
        objects = self.state["objects"]
        seen = set()
        start = objects.get(oid)
        stack = list(start["parents"]) if start else []
        while stack:
            current = stack.pop()
            if current in seen or current not in objects:
                continue
            seen.add(current)
            stack.extend(objects[current]["parents"])
        return seen

    def _free_id(self, kind, known):
        """自动编号：点用 A、B、…；其它按类型前缀编号。"""
        objects = self.state["objects"]
        if kind == "point":
            letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
            for round_index in range(0, 100):
                for letter in letters:
                    candidate = letter if round_index == 0 \
                        else "%s%d" % (letter, round_index)
                    if candidate not in objects and candidate not in known:
                        return candidate
        prefix = _ID_PREFIX.get(kind) or (kind[:3] if kind else "obj")
        for index in range(1, MAX_OBJECTS + 2):
            candidate = "%s%d" % (prefix, index)
            if candidate not in objects and candidate not in known:
                return candidate
        return "%s%d" % (prefix, len(objects) + 1)


# --------------------------------------------------------------------------
# 模块级小工具
# --------------------------------------------------------------------------

def _normalize_object(oid, raw):
    out = make_object(oid, raw.get("type"), raw.get("definition"), raw.get("style"),
                      raw.get("meta"), raw.get("seq", 0), raw.get("value"))
    parents = raw.get("parents")
    if isinstance(parents, (list, tuple)):
        out["parents"] = [str(item) for item in parents if isinstance(item, str)]
    return out


def _order_creates(creates):
    """同批 create 的拓扑排序：先写子对象、后写父对象也能建起来。有环就报环上的 id。"""
    by_id = {}
    for item in creates:
        by_id[item["id"]] = item
    pending = {}
    for item in creates:
        pending[item["id"]] = [parent for parent in item["parents"] if parent in by_id]
    ordered = []
    ready = [item["id"] for item in creates if not pending[item["id"]]]
    while ready:
        current = ready.pop(0)
        ordered.append(by_id[current])
        pending.pop(current, None)
        for oid in list(pending):
            if current in pending[oid]:
                pending[oid].remove(current)
                if not pending[oid]:
                    ready.append(oid)
    if pending:
        return ordered, sorted(pending)
    return ordered, []


def _circumcircle(points):
    """三点外接圆（圆心 + 半径）。共线返回 None。"""
    (ax, ay), (bx, by), (cx, cy) = points[0], points[1], points[2]
    determinant = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(determinant) < 1e-12:
        return None
    norm_a = ax * ax + ay * ay
    norm_b = bx * bx + by * by
    norm_c = cx * cx + cy * cy
    ux = (norm_a * (by - cy) + norm_b * (cy - ay) + norm_c * (ay - by)) / determinant
    uy = (norm_a * (cx - bx) + norm_b * (ax - cx) + norm_c * (bx - ax)) / determinant
    return [ux, uy], math.hypot(ax - ux, ay - uy)


def _tangent_slope(expr, x):
    """函数曲线在 x 处的斜率；算不出来返回 None。

    ``plane_math.derivative`` 返回的是渲染好的导数**文本**（{ok, text}），
    再喂给 ``plane_math.evaluate`` 代入 x。任何一步不成立都返回 None ——
    调用方必须给出结构化错误，**绝不能把"算不出来"当成斜率 0**（那会画出一条假的水平切线）。
    """
    if not expr:
        return None
    try:
        derived = plane_math.derivative(expr)
        if not isinstance(derived, dict) or not derived.get("ok"):
            return None
        value = plane_math.evaluate(derived.get("text"), {"x": x})
    except Exception:
        return None
    if isinstance(value, dict):
        value = value.get("value", value.get("result"))
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _nearest_index(near, points, fallback):
    """候选交点里离 ``near`` 最近的那个序号。

    挑不出来（没给位置、候选不足两个、坐标读不出来）就返回 ``fallback`` ——
    宁可按原来的序号，也不猜。
    """
    spot = plane_math.as_pair(near)
    if spot is None or not isinstance(points, list) or len(points) < 2:
        return fallback
    best = fallback
    best_gap = None
    for position, item in enumerate(points):
        pair = plane_math.as_pair(item)
        if pair is None:
            continue
        gap = (pair[0] - spot[0]) ** 2 + (pair[1] - spot[1]) ** 2
        if best_gap is None or gap < best_gap:
            best_gap, best = gap, position
    return best


def _line_value(point, direction):
    """直线形状的取值：点 + 方向 + 一般式 a·x + b·y + c = 0（求交直接用）。"""
    a, b = direction[1], -direction[0]
    return {"point": {"x": point[0], "y": point[1]},
            "direction": {"x": direction[0], "y": direction[1]},
            "a": a, "b": b, "c": -(a * point[0] + b * point[1])}


def _measure_failure(code, message, path="definition.of", hint=""):
    """度量算不出来时的结果（形状和 ``plane_math.number_result`` 一致）。"""
    return {"ok": False, "error": error(code, message, path, hint)}


def _measure_out(kind, ids, measured):
    out = {"kind": kind, "of": list(ids)}
    if measured.get("ok"):
        out.update({"value": measured["value"], "exact": measured.get("exact", ""),
                    "decimal": measured.get("decimal", ""),
                    "approximate": measured.get("approximate", False)})
        for key in ("sin", "cos", "degrees", "radians"):
            if key in measured:
                out[key] = measured[key]
    else:
        out["error"] = measured.get("error") or error("bad_value", "度量算不出来")
    return out


def _value_warning(oid, value):
    """取值里带着"没算出来 / 用了兜底"时给一条提示（不静默）。"""
    if not isinstance(value, dict):
        return ""
    if value.get("source") == "fallback":
        return "交点 %s 用的是兜底求交实现：%s" % (oid, value.get("reason") or "plane_geom 不可用")
    problem = value.get("error")
    if isinstance(problem, dict):
        return "%s 求值失败：%s" % (oid, problem.get("message", ""))
    return ""


def _has_tag(obj, tag):
    current = (obj.get("meta") or {}).get("tag")
    wanted = tag if isinstance(tag, (list, tuple, set)) else [tag]
    if isinstance(current, (list, tuple, set)):
        return any(item in current for item in wanted)
    return current in wanted


def _matches_text(obj, needle):
    if needle in str(obj.get("id", "")).lower() or needle in str(obj.get("type", "")).lower():
        return True
    for value in (obj.get("definition") or {}).values():
        texts = value if isinstance(value, (list, tuple)) else [value]
        for text in texts:
            if isinstance(text, str) and needle in text.lower():
                return True
    return False


def compact_value(value):
    """紧凑模式的 ``value``：曲线/函数的采样点数组不回给模型（一个函数就 2000+ 个点）。

    只留「认得出这是什么、大概在哪儿、有没有被截断」，外加每段最前面两个点当样子。
    ``segments`` 变成"段数"、``pointCount`` 是总点数——前端画布拿的是完整 state，
    所以这里裁掉不影响作图；真要完整采样点用 ``get_object`` 或 ``compact=false``。
    """
    if not isinstance(value, dict):
        return value
    segments = value.get("segments")
    if not isinstance(segments, (list, tuple)) or not segments:
        return value
    if not isinstance(segments[0], (list, tuple)):
        return value
    total = sum(len(seg) for seg in segments if isinstance(seg, (list, tuple)))
    if total <= COMPACT_VALUE_POINTS:
        return value
    out = {key: item for key, item in value.items() if key != "segments"}
    if isinstance(out.get("points"), (list, tuple)):
        out["points"] = total          # trace / locus 的 value.points 就是 segments 本身
    out["segments"] = len(segments)
    out["pointCount"] = total
    out["sample"] = [list(seg[:2]) for seg in segments
                     if isinstance(seg, (list, tuple))][:3]
    out["note"] = ("采样点已省略（共 %d 个）：要看完整曲线用 get_object（或 compact=false），"
                   "要作图直接看画布" % total)
    return out


def _compact(obj):
    """紧凑模式：只留"要画 / 要认"的字段；曲线采样点另做裁剪（见 ``compact_value``）。"""
    return {"id": obj["id"], "type": obj["type"], "definition": obj["definition"],
            "value": compact_value(obj["value"]),
            "visible": bool(obj["style"].get("visible", True)),
            "tag": (obj.get("meta") or {}).get("tag"), "frozen":
                bool((obj.get("meta") or {}).get("frozen"))}


def _batch_explanation(created, updated, removed):
    parts = []
    if created:
        parts.append("新建 %d 个对象：%s" % (len(created), "、".join(created)))
    if updated:
        parts.append("更新 %d 个：%s" % (len(updated), "、".join(updated)))
    if removed:
        parts.append("删除 %d 个：%s" % (len(removed), "、".join(removed)))
    return "；".join(parts) if parts else "没有变化"
