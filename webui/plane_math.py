import json
# -*- coding: utf-8 -*-
"""「平面」模块的数值 / 符号计算层（P2）。

职责边界（不越层）：
  - **不持有状态**：对象、依赖图、撤销栈都在 :mod:`plane` 里；
  - 只做"把表达式 / 几何算成数或点列"这一件事，返回的都是纯数据（能直接 JSON 化）；
  - 表达式**一律**走 ``plot.py`` 的手写词法 + 递归下降解析器（``plot._tokenize`` /
    ``plot._Parser`` / ``plot._compile`` / ``plot._diff`` / ``plot.render``），
    **绝不 eval**，也**不重写第二个解析器**；
  - 度量（距离 / 面积 / 斜率 / 角度）走 ``calc.py`` 的 ``Exact``，和计算器面板给一样的精确值。

命名参数（滑动条）：解析器只认 ``x`` / ``y`` / ``t`` 三个自由变量，其它名字会报"不认识的函数
或名字"。所以这里的做法是**在词法层把参数替换成数字字面量**（``_bind``），替换值一律加括号，
避免 ``a^2`` 在 ``a=-2`` 时被解析成 ``-(2^2)``。替换过程中 expression 仍然由 plot 的解析器解析。

采样上限（教室机是 Win7，绝不能卡；下面每个数字都是**硬上限**，所有循环都有界）：

===================  ==========================================================
``FUNCTION_SAMPLES``   480   显函数采样点数（与 ``plot.DEFAULT_SAMPLES`` 一致）
``PARAMETRIC_SAMPLES`` 600   参数曲线；plot 内部用 900，平面面板要同时画多条，降到 600
``POLAR_SAMPLES``      600   极坐标曲线，同上
``IMPLICIT_GRID``      120   隐式曲线网格 120×120 = 14400 次求值（plot 方程面板用 160）
``MAX_IMPLICIT_GRID``  200   调用方最多能把网格调到 200×200 = 40000 次求值
``INEQUALITY_GRID``    96    不等式区域 96×96 = 9216 次求值（粗掩码，教室里够用）
``MAX_POINTS``         4000  一次返回的点数硬上限，超了截断并置 ``truncated``
``MAX_REGION_RUNS``    4000  区域最多给 4000 个矩形（行内游程合并），超了截断
===================  ==========================================================

返回值统一形状：``{"ok": bool, "error": {code, message, path, hint} | None, ...}``，
另外按家族带自己的字段（采样带 ``kind/segments/regions/bbox/points/truncated``，
度量带 ``exact/decimal/value/approximate``）。**任何**函数都不会把异常抛给调用方。
"""

import functools
import math
import re

import calc
import plot

# --------------------------------------------------------------------------
# 上限与默认值
# --------------------------------------------------------------------------

FUNCTION_SAMPLES = 480
PARAMETRIC_SAMPLES = 600
POLAR_SAMPLES = 600
IMPLICIT_GRID = 120
MAX_IMPLICIT_GRID = 200
INEQUALITY_GRID = 96
MAX_POINTS = 4000
MAX_REGION_RUNS = 4000
MIN_SAMPLES = 8
MAX_SAMPLES = 4000

# 与 plane.DEFAULT_VIEW 保持一致（plane_math 不能 import plane，会成环）
DEFAULT_BOUNDS = {"xmin": -10.0, "ymin": -6.0, "xmax": 10.0, "ymax": 6.0}

# 解析器里已经有含义的名字，绝不能被参数替换掉
_RESERVED = (set(plot.CONSTANTS) | set(plot.UNARY) | set(plot.BINARY)
             | {"x", "y", "t", "u", "theta", "θ", "r"})

_CURVE_DEFAULTS = {"kind": "", "segments": [], "regions": [], "bbox": None,
                   "points": 0, "truncated": False, "samples": 0}
_NUMBER_DEFAULTS = {"exact": "", "decimal": "", "value": None, "approximate": False}

TYPES_WITH_CURVES = ("function", "implicit", "parametric", "polar", "inequality")


# --------------------------------------------------------------------------
# 返回值拼装 + 兜底
# --------------------------------------------------------------------------

def _as_mapping(value):
    """把可能是 dict / JSON 字符串 / 键值对数组的参数统一成 dict，其它一律当空。

    下游都是 `x.update(...)`：如果传进来的是字符串，dict.update 会抛
    dictionary update sequence element #0 has length 1; 2 is required —— 这就是 AI 工具调用
    报那个错的原因，所以在这里统一兜住。
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


def _error(code, message, path="", hint="", defaults=None):
    out = dict(defaults or {})
    out["ok"] = False
    out["error"] = {"code": code, "message": message, "path": path, "hint": hint}
    return out


def _curve_error(code, message, path="", hint=""):
    return _error(code, message, path, hint, _CURVE_DEFAULTS)


def _number_error(code, message, path="", hint=""):
    return _error(code, message, path, hint, _NUMBER_DEFAULTS)


def _guard(defaults=None):
    """兜底装饰器：任何意外（含 RecursionError）都变成错误值，不抛给调用方。"""
    def decorate(function):
        @functools.wraps(function)
        def wrapper(*args, **kwargs):
            try:
                return function(*args, **kwargs)
            except Exception as error:                      # noqa: BLE001
                return _error("internal_error",
                              "%s 算不出来（%s）" % (function.__name__, error),
                              "", "把表达式写简单一点再试", defaults)
        return wrapper
    return decorate


def _curve_ok(kind, segments, regions=None, samples=0, extra=None):
    segments, points, truncated = _trim(segments)
    out = dict(_CURVE_DEFAULTS)
    out.update({"ok": True, "error": None, "kind": kind, "segments": segments,
                "regions": list(regions or []), "points": points,
                "truncated": truncated, "samples": samples,
                "bbox": plot._curve_bbox(segments) if segments else None})
    if extra:
        out.update(_as_mapping(extra))
    return out


def _trim(segments):
    """点数硬上限：超了就截断（并让调用方知道 truncated）。"""
    total = sum(len(line) for line in segments)
    if total <= MAX_POINTS:
        return segments, total, False
    kept, used = [], 0
    for line in segments:
        room = MAX_POINTS - used
        if room <= 1:
            break
        kept.append(line if len(line) <= room else line[:room])
        used += len(kept[-1])
    return kept, used, True


def _clamp_samples(count, default):
    try:
        number = int(count)
    except (TypeError, ValueError):
        return default
    return max(MIN_SAMPLES, min(MAX_SAMPLES, number))


def _finite(value, default):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


# --------------------------------------------------------------------------
# 命名参数（滑动条）绑定 —— 词法层替换，替换值带括号
# --------------------------------------------------------------------------

def _literal(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return "(%r)" % number                # repr 保证是合法数字字面量；括号保证优先级


def _bind(text, variables):
    """把命名参数替换成数字字面量。返回 ``(新文本, 用到的参数名, 错误或 None)``。

    用 plot 自己的 ``_TOKEN`` 扫词，所以"哪个位置是名字"和解析器看到的一致；
    看不懂的字符原样留着，交给 plot 的解析器去报错（错误消息更准确）。
    """
    if not variables:
        return text, [], None
    pieces, used, cursor, position = [], [], 0, 0
    while position < len(text):
        match = plot._TOKEN.match(text, position)
        if not match or match.group("bad"):
            return text, [], None
        name = match.group("name")
        if name and name.lower() in variables and name.lower() not in _RESERVED:
            literal = _literal(variables[name.lower()])
            if literal is None:
                return text, [], _error("bad_value",
                                        "参数 %s 的值不是有限的数" % name,
                                        "variables.%s" % name,
                                        "滑动条的值要是数字")
            pieces.append(text[cursor:match.start("name")])
            pieces.append(literal)
            cursor = match.end("name")
            used.append(name.lower())
        position = match.end()
    pieces.append(text[cursor:])
    return "".join(pieces), used, None


def expression_names(text):
    """表达式里出现过的自由名字（去掉函数名、常量、x/y/t 这些参数槽）。

    依赖图靠它把"表达式里引用了哪个滑动条"算成一条边 —— 滑动条一动，曲线跟着重采样。
    词法用的是 plot 自己的 ``_TOKEN``；遇到看不懂的字符就放弃（返回已经认出来的部分）。
    """
    out = []
    text = str(text or "")
    position = 0
    while position < len(text):
        match = plot._TOKEN.match(text, position)
        if not match or match.group("bad"):
            break
        name = match.group("name")
        if name:
            lowered = name.lower()
            if lowered not in _RESERVED and lowered not in out:
                out.append(lowered)
        position = match.end()
    return out


def _compile(text, variables=None):
    """返回 ``(f(x, y=0) 闭包, 用到的参数名)``；看不懂就抛 ``plot.ExpressionError``。"""
    bound, used, error = _bind(str(text or ""), variables or {})
    if error is not None:
        raise plot.ExpressionError(error["message"])
    if len(bound) > plot.MAX_EXPRESSION:
        raise plot.ExpressionError("表达式太长（上限 %d 个字符）" % plot.MAX_EXPRESSION)
    return plot._compile(plot._parse_node(bound)), used


def _range_of(low, high, fallback_low, fallback_high):
    """一维范围：复用 plot 的范围校验（有限性 / 顺序 / 跨度 / MAX_COORD）。"""
    x_min, x_max, _y_min, _y_max = plot._equation_range({
        "x_min": fallback_low if low is None else low,
        "x_max": fallback_high if high is None else high,
        "y_min": fallback_low, "y_max": fallback_high})
    return x_min, x_max


def _glider_range(definition, value):
    """动点挂在**函数曲线**上时，参数 t 对应的 x 区间。

    钉了 xmin/xmax 就用它；**自动区间**必须用"服务端真正采样的那个区间" ——
    采样是按可见范围采的（见 ``plane._curve_bounds``），这里再假设一个 ±10 就会和画出来的
    曲线错位：同一个参数落到别的 x 上，缩放之后老师看到的是"线段长度变了"，拖动中则是
    "突然移位、对不上手指"（网页端 hostRange 取的就是采样点的范围，两边必须一致）。
    区间直接取采样点自己的首尾 x，和网页端一个做法。
    """
    low = definition.get("xmin")
    high = definition.get("xmax")
    if isinstance(low, (int, float)) and isinstance(high, (int, float)) and high > low:
        return float(low), float(high)
    xs = []
    segments = value.get("segments") if isinstance(value, dict) else None
    for group in segments or []:
        for point in group or []:
            pair = as_pair(point)
            if pair is not None:
                xs.append(pair[0])
    if xs:
        return min(xs), max(xs)
    return _range_of(low, high, plot.DEFAULT_X_MIN, plot.DEFAULT_X_MAX)


def _resolve_bounds(bounds):
    """把 ``{"xmin","ymin","xmax","ymax"}`` 校验成 ``(x_min, x_max, y_min, y_max)``。"""
    bounds = bounds if isinstance(bounds, dict) else {}
    args = {}
    for key, fallback in (("xmin", "x_min"), ("xmax", "x_max"),
                          ("ymin", "y_min"), ("ymax", "y_max")):
        value = bounds.get(key, DEFAULT_BOUNDS[key])
        args[fallback] = value
    return plot._equation_range(args)


# --------------------------------------------------------------------------
# 求值与符号求导
# --------------------------------------------------------------------------

@_guard()
def evaluate(expr, variables=None):
    """在一个点上求值。``variables`` 里的 ``x``/``y``/``t`` 进变量槽，其余当命名参数。

    ``{"ok": True, "value": 9.0, "finite": True, "error": None}``；
    表达式看不懂就给 ``ok=False`` + 结构化错误；值不是有限数（定义域外、溢出）就给
    ``ok=True, value=None, finite=False``（"这里没有定义"，不是错误）。
    """
    variables = variables if isinstance(variables, dict) else {}
    text = str(expr or "").strip()
    if not text:
        return _error("missing_field", "表达式是空的", "expr", "给一个含 x 的表达式，例如 x^2+1")
    try:
        function, _used = _compile(text, variables)
    except plot.ExpressionError as error:
        return _error("bad_expression", str(error), "expr",
                      "检查括号、函数名；命名参数要先定义成滑动条再传进来")
    slot_x = _finite(variables.get("x", variables.get("t", 0.0)), 0.0)
    slot_y = _finite(variables.get("y", 0.0), 0.0)
    value = float(function(slot_x, slot_y))
    if not math.isfinite(value):
        return {"ok": True, "value": None, "finite": False, "error": None}
    return {"ok": True, "value": value, "finite": True, "error": None}


@_guard()
def derivative(expr, variables=None):
    """符号求导（复用 ``plot._diff`` + ``plot.render``）。

    命名参数会在求导**之前**代入，所以 ``a*x^2`` 对 x 的导数是 ``2ax`` 的数值化写法。
    """
    text = str(expr or "").strip()
    if not text:
        return _error("missing_field", "表达式是空的", "expr", "给一个含 x 的表达式")
    try:
        bound, _used, error = _bind(text, variables or {})
        if error is not None:
            return _error(error["error"]["code"], error["error"]["message"],
                          "expr", error["error"]["hint"])
        node = plot._parse_node(bound)
    except plot.ExpressionError as error:
        return _error("bad_expression", str(error), "expr", "检查括号和函数名")
    try:
        return {"ok": True, "text": plot.render(plot._diff(node)), "error": None}
    except plot._NoDerivative as error:
        return _error("no_derivative", "这个式子的导数算不出来（%s）" % error, "expr",
                      "初等函数以外（取整、符号函数）没有导数")


# --------------------------------------------------------------------------
# 曲线采样
# --------------------------------------------------------------------------

@_guard(_CURVE_DEFAULTS)
def sample_function(expr, xmin=None, xmax=None, samples=None, variables=None):
    """显函数 y = f(x) 采样成折线（``segments`` 是 ``[[x, y], ...]`` 的列表）。"""
    text = str(expr or "").strip()
    if not text:
        return _curve_error("missing_field", "表达式是空的", "definition.expr",
                            "给一个含 x 的表达式，例如 x^2+1")
    try:
        function, _used = _compile(text, variables)
        x_min, x_max = _range_of(xmin, xmax, plot.DEFAULT_X_MIN, plot.DEFAULT_X_MAX)
    except plot.ExpressionError as error:
        return _curve_error("bad_expression", str(error), "definition.expr",
                            "检查表达式和 x 的范围")
    count = _clamp_samples(samples, FUNCTION_SAMPLES)
    segments = plot._sample_curve(lambda t: (t, function(t)), x_min, x_max, count)
    if not segments:
        return _curve_error("empty_curve", "这个函数在给定范围里画不出曲线",
                            "definition.expr", "检查定义域，或者把 x 的范围调大一点")
    return _curve_ok("function", segments, samples=count)


@_guard(_CURVE_DEFAULTS)
def sample_parametric(x_expr, y_expr, tmin=None, tmax=None, samples=None, variables=None):
    """参数曲线 x = x(t), y = y(t)。``definition`` 里就叫 ``x`` / ``y``（冻结的字段名）。"""
    if not str(x_expr or "").strip() or not str(y_expr or "").strip():
        return _curve_error("missing_field", "参数方程要同时给 x(t) 和 y(t)",
                            "definition.x", "例如 x = cos(t)、y = sin(t)")
    try:
        x_of, _used = _compile(x_expr, variables)
        y_of, _used2 = _compile(y_expr, variables)
        t_min, t_max = _range_of(tmin, tmax, 0.0, 2 * math.pi)
    except plot.ExpressionError as error:
        return _curve_error("bad_expression", str(error), "definition.x",
                            "检查 x(t)、y(t) 和 t 的范围")
    count = _clamp_samples(samples, PARAMETRIC_SAMPLES)
    segments = plot._sample_curve(lambda t: (x_of(t), y_of(t)), t_min, t_max, count)
    if not segments:
        return _curve_error("empty_curve", "这条参数曲线在给定范围里画不出来",
                            "definition.tmin", "检查 t 的范围")
    return _curve_ok("parametric", segments, samples=count)


@_guard(_CURVE_DEFAULTS)
def sample_polar(expr, tmin=None, tmax=None, samples=None, variables=None):
    """极坐标曲线 r = f(θ)。``expr`` 里写 ``1+cos(theta)`` 或 ``1+cos(θ)`` 都认。"""
    text = str(expr or "").strip()
    if not text:
        return _curve_error("missing_field", "极坐标曲线要写 r = …", "definition.expr",
                            "例如 1+cos(theta)")
    text = re.sub(r"(?is)^\s*r\s*=", "", text).strip()
    try:
        r_of, _used = _compile(text, variables)
        t_min, t_max = _range_of(tmin, tmax, 0.0, 2 * math.pi)
    except plot.ExpressionError as error:
        return _curve_error("bad_expression", str(error), "definition.expr",
                            "检查 r(θ) 和 θ 的范围")
    count = _clamp_samples(samples, POLAR_SAMPLES)
    segments = plot._sample_curve(
        lambda t: (r_of(t) * math.cos(t), r_of(t) * math.sin(t)), t_min, t_max, count)
    if not segments:
        return _curve_error("empty_curve", "这条极坐标曲线在给定范围里画不出来",
                            "definition.expr", "检查 r(θ) 的写法")
    return _curve_ok("polar", segments, samples=count)


def _implicit_field(text, variables):
    """隐式曲线的 F(x, y)：``x^2+y^2=1`` 和 ``x^2+y^2-1`` 都统一成 F=0。"""
    expression = str(text or "").strip()
    if not expression:
        raise plot.ExpressionError("表达式是空的")
    try:
        left, right = plot._split_equation(expression)
    except plot.ExpressionError:
        difference = expression
    else:
        difference = "(%s) - (%s)" % (left, right)
    function, _used = _compile(difference, variables)
    return function


@_guard(_CURVE_DEFAULTS)
def sample_implicit(expr, bounds=None, grid=None, variables=None):
    """隐式曲线 F(x, y) = 0：在网格上做 marching squares，再串成折线。

    网格复用 ``plot`` 的 ``_marching_squares`` / ``_chain_segments`` / ``_round_line``。
    """
    try:
        field_of = _implicit_field(expr, variables)
        x_min, x_max, y_min, y_max = _resolve_bounds(bounds)
    except plot.ExpressionError as error:
        return _curve_error("bad_expression", str(error), "definition.expr",
                            "方程写成「左边 = 右边」，例如 x^2 + y^2 = 1")
    except (TypeError, ValueError) as error:
        return _curve_error("bad_value", "画图范围不对（%s）" % error, "view",
                            "范围要是有限的数字")
    try:
        count = int(grid) if grid is not None else IMPLICIT_GRID
    except (TypeError, ValueError):
        count = IMPLICIT_GRID
    count = max(plot.EQUATION_GRID // 8, min(MAX_IMPLICIT_GRID, count))

    xs = [x_min + (x_max - x_min) * index / (count - 1.0) for index in range(count)]
    ys = [y_min + (y_max - y_min) * index / (count - 1.0) for index in range(count)]
    field = []
    for y in ys:
        row = []
        for x in xs:
            value = float(field_of(x, y))
            if not math.isfinite(value) or abs(value) > plot.MAX_COORD:
                value = None
            row.append(value)
        field.append(row)

    raw = plot._marching_squares(field, xs, ys)
    if not raw:
        return _curve_error("empty_curve", "这个方程在这块范围里画不出曲线",
                            "definition.expr", "检查写法，或者把范围调大一点")
    segments = []
    for line in plot._chain_segments(raw):
        line = plot._round_line(line)
        if len(line) > 1:
            segments.append(line)
    if not segments:
        return _curve_error("empty_curve", "这个方程在这块范围里画不出曲线",
                            "definition.expr", "换一个范围试试")
    return _curve_ok("implicit", segments, samples=count)


# --------------------------------------------------------------------------
# 隐式曲线上的动点 / 切线（F(x,y)=0 这一类：方程、圆锥曲线）
# --------------------------------------------------------------------------
#
# 参数 t 一律是**弧长占比**（0..1），不是网格位置：
#   * 「就近动点」= 鼠标位置投影到最近的那一段采样折线上（不是最近的网格点），
#     所以取参必须按折线本身的距离算，按网格行列算出来的点会偏半格；
#   * 隐式曲线的采样框跟着视野走（见 plane._curve_bounds），同一个位置在视野
#     放大前后落在不同的网格上，弧长占比是**跟视野无关**的那个量；
#   * "竖直线段上照抄另一个坐标"那种写法不算投影：斜着的曲线会算出明显偏远的点。

# 拉回曲线的迭代次数上限（几步就到机器精度，封顶是为了"绝不卡"）
_CURVE_PULL_STEPS = 12


def _sampled_scale(segments, fallback=1.0):
    """采样折线的大致跨度：数值求导 / 梯度差分步长要跟着它走。"""
    lines = [line for line in (segments or []) if len(line) >= 2]
    if not lines:
        return fallback
    xs = [point[0] for line in lines for point in line]
    ys = [point[1] for line in lines for point in line]
    if not xs or not ys:
        return fallback
    span = max(max(xs) - min(xs), max(ys) - min(ys))
    return span if math.isfinite(span) and span > 0 else fallback


def _nearest_on_segments(point, segments):
    """折线上离 ``point`` 最近的点：按**线段**投影（不是最近的采样点）。

    多条折线（隐式曲线可能是好几支）一起比，返回
    ``(距离, 投影点, 弧长占比, 折线序号, 段内位置)``；折线为空返回 None。
    弧长占比 = 走到投影点为止的长度 / 这一支的总长 —— 和网页端同一个口径。
    """
    best = None
    for branch, line in enumerate(segments or []):
        if not line or len(line) < 2:
            continue
        lengths = [0.0]
        total = 0.0
        for index in range(len(line) - 1):
            total += math.hypot(line[index + 1][0] - line[index][0],
                                line[index + 1][1] - line[index][1])
            lengths.append(total)
        if not (total > 0):
            continue
        for index in range(len(line) - 1):
            ax, ay = line[index]
            bx, by = line[index + 1]
            dx = bx - ax
            dy = by - ay
            span = dx * dx + dy * dy
            ratio = 0.0 if span <= 0 else \
                ((point[0] - ax) * dx + (point[1] - ay) * dy) / span
            ratio = min(1.0, max(0.0, ratio))
            px = ax + dx * ratio
            py = ay + dy * ratio
            distance = math.hypot(point[0] - px, point[1] - py)
            if best is None or distance < best[0]:
                along = lengths[index] + math.hypot(px - ax, py - ay)
                best = (distance, [px, py], along / total, branch, index)
    return best


def _segments_at(segments, branch, ratio, u):
    """折线 ``branch`` 上弧长占比 ``u`` 处的坐标；``ratio`` 是退回用的段内位置。"""
    line = segments[branch]
    lengths = [0.0]
    total = 0.0
    for index in range(len(line) - 1):
        total += math.hypot(line[index + 1][0] - line[index][0],
                            line[index + 1][1] - line[index][1])
        lengths.append(total)
    if not (total > 0):
        return list(line[0])
    want = u * total
    for index in range(len(line) - 1):
        if want <= lengths[index + 1] or index == len(line) - 2:
            span = lengths[index + 1] - lengths[index]
            place = ratio if span <= 0 else (want - lengths[index]) / span
            place = min(1.0, max(0.0, place))
            return [line[index][0] + (line[index + 1][0] - line[index][0]) * place,
                    line[index][1] + (line[index + 1][1] - line[index][1]) * place]
    return list(line[-1])


def _pull_to_curve(function, x, y, scale, steps=_CURVE_PULL_STEPS):
    """把点拉回 F=0 上：一步牛顿，走不动就原样返回。

    marching squares 的折线本身在曲线外（弦相对弧永远是偏的）：单位圆 45° 处
    折线点算出来的 |F| 约 3e-3。老师在讲台上要的是"这个点真的落在曲线上"，
    所以投影完再拉一次 —— 实测 12 条曲线上最差 |F| = 2e-15（数据精度的极限）。

    两个真踩过的坑（都写到测试里了）：
      * 差分步长按 scale×1e-5 取。中心差分的截断误差是 O(step²·F''')，步长取
        1e-6 时在 x^2+y^4=16 的顶部反而只剩 1e-5 的梯度精度，拉回来的点差 1e-5；
      * 方向必须"梯度指哪边就往回走"：F 为正时取负、F 为负时顺着走。
        弄反了 F 会一路跑掉（-0.024 → -0.047 → -0.094 …）。
    梯度太小（奇异点 / 局部平坦）或算出 nan 时**不动**，绝不拿 0 当梯度去算。
    """
    step = max(1e-9, scale * 1e-5)
    limit = max(1e-9, scale * 0.05)          # 一步最多挪采样跨度的 5%
    for _round in range(max(1, steps)):
        value = function(x, y)
        if value is None or not math.isfinite(value):
            return None
        gradient_x = (function(x + step, y) - function(x - step, y)) / (2.0 * step)
        gradient_y = (function(x, y + step) - function(x, y - step)) / (2.0 * step)
        if not (math.isfinite(gradient_x) and math.isfinite(gradient_y)):
            return None
        size = math.hypot(gradient_x, gradient_y)
        if not (size > 0):
            return [x, y]
        # 沿梯度的**正方向**走 |F| / |∇F|：梯度指向 F 增大的那一侧，
        # 所以 F>0 要往回走（取负）、F<0 才顺着走。方向弄反的话 F 会一路跑掉
        # （实测从 -0.024 变 -0.047、-0.094… 越修越远）。
        # 也正因为方向由 F 的正负定，长度必须先用 abs() 再交给 min() 封顶 ——
        # 负的 value 直接进 min() 会把封顶出来的长度也带成负数。
        along = min(abs(value) / size, limit)
        if value > 0:
            along = -along
        x += along * gradient_x / size
        y += along * gradient_y / size
        if not (math.isfinite(x) and math.isfinite(y)):
            return None
    return [x, y]


def _implicit_function(obj, variables=None):
    """隐式对象的 F(x, y)：复用 ``_implicit_field``（``F = G`` 两边相减，F=0 口径）。"""
    definition = obj.get("definition") if isinstance(obj.get("definition"), dict) else {}
    return _implicit_field(definition.get("expr"), variables)


def _implicit_samples(obj):
    """隐式对象**已经画出来的**那一份采样折线（``value.segments``）。

    为什么不用 ``sample_object`` 重新采一遍：那样得到的是"按服务端 view 采的"另一条
    折线，和老师屏幕上那条不是同一条 —— 点会落在看得见的曲线旁边。这里就用同一份。
    """
    value = obj.get("value") if isinstance(obj.get("value"), dict) else {}
    segments = []
    for group in value.get("segments") or []:
        line = [pair for pair in (as_pair(point) for point in group or []) if pair is not None]
        if len(line) >= 2:
            segments.append(line)
    return segments


@_guard()
def curve_glider(obj, parameter=0.5, variables=None):
    """曲线上的动点（隐式 / 参数 / 极坐标）：参数 = 采样折线上的**弧长占比**。

    返回 ``{"ok", "point", "parameter", "segments", "error"}``；``segments`` 是这次
    用的那份采样折线（调用方要用同一份做投影时省得再取一次）。取不到点就返回
    ``ok=False`` + 结构化错误 —— 调用方必须报出来，不许静默什么都不做。
    """
    kind = str(obj.get("type") or "")
    segments = _implicit_samples(obj)
    if not segments:
        return {"ok": False, "point": None, "parameter": None, "segments": [],
                "error": {"code": "no_value",
                          "message": "%s 还没有采样点，动点挂不上去" % (kind or "曲线"),
                          "path": "definition.on",
                          "hint": "先把曲线画出来（它得先有一条看得见的折线）"}}
    u = min(1.0, max(0.0, _finite(parameter, 0.5)))
    # 隐式曲线的采样折线可能有好几支（例如 x*y=1 的两条双曲线）：参数落在**第一支**上。
    # 跨支拼接会算出一个两段之间根本不存在的点，所以这里只在一支里走。
    point = _segments_at(segments, 0, 0.5, u)
    if kind == "implicit":
        try:
            function = _implicit_function(obj, variables)
        except plot.ExpressionError as error:
            return {"ok": False, "point": None, "parameter": u, "segments": segments,
                    "error": {"code": "bad_expression", "message": str(error),
                              "path": "definition.expr", "hint": "方程写成「左边 = 右边」"}}
        pulled = _pull_to_curve(function, point[0], point[1], _sampled_scale(segments))
        if pulled is not None:
            point = pulled
    return {"ok": True, "point": point, "parameter": u, "segments": segments, "error": None}


@_guard()
def curve_projection(obj, point, variables=None):
    """鼠标位置在曲线上的**就近点**：投影到最近的那一段采样折线上。

    返回 ``{"ok", "point", "parameter", "distance", "error"}``；
    ``parameter`` 就是弧长占比，直接可以当动点的参数存起来（两端口径一致）。
    """
    kind = str(obj.get("type") or "")
    spot = as_pair(point)
    if spot is None:
        return {"ok": False, "point": None, "parameter": None, "distance": None,
                "error": {"code": "bad_value", "message": "要投影的不是一个点",
                          "path": "point", "hint": "传 {x, y} 或者 [x, y]"}}
    segments = _implicit_samples(obj)
    if not segments:
        return {"ok": False, "point": None, "parameter": None, "distance": None,
                "error": {"code": "no_value",
                          "message": "%s 还没有采样点，算不出就近点" % (kind or "曲线"),
                          "path": "definition.on", "hint": "先把曲线画出来"}}
    best = _nearest_on_segments(spot, segments)
    if best is None:
        return {"ok": False, "point": None, "parameter": None, "distance": None,
                "error": {"code": "no_value", "message": "曲线退化成了一个点，算不出就近点",
                          "path": "definition.on", "hint": "换一条曲线"}}
    distance, foot, u, _branch, _ratio = best
    if kind == "implicit":
        try:
            function = _implicit_function(obj, variables)
        except plot.ExpressionError as error:
            return {"ok": False, "point": None, "parameter": u, "distance": distance,
                    "error": {"code": "bad_expression", "message": str(error),
                              "path": "definition.expr", "hint": "方程写成「左边 = 右边」"}}
        pulled = _pull_to_curve(function, foot[0], foot[1], _sampled_scale(segments))
        if pulled is not None:
            foot = pulled
            distance = math.hypot(spot[0] - foot[0], spot[1] - foot[1])
    return {"ok": True, "point": foot, "parameter": u, "distance": distance, "error": None}


def _tangent_of(function, parameter, t_min, t_max):
    """参数曲线在 t 处的切向量（中心差分，自然参数化）。

    自然参数化下 |P'(t)| = 1，所以差分步长直接给一个很小的绝对值就够稳。
    """
    span = t_max - t_min
    if not (span > 0):
        return None
    step = span * 1e-6
    first = function(max(t_min, parameter - step))
    second = function(min(t_max, parameter + step))
    if first is None or second is None:
        return None
    if not all(math.isfinite(value) for value in (first[0], first[1], second[0], second[1])):
        return None
    dx = second[0] - first[0]
    dy = second[1] - first[1]
    if math.hypot(dx, dy) <= 1e-15:
        return None
    return [dx, dy]


@_guard()
def tangent_direction(obj, point, variables=None):
    """曲线在一点处的切线**方向向量**；给不出来就返回 ``ok=False``。

    为什么返回方向而不是斜率：``F_y = 0`` 的地方切线是**竖直**的，斜率不存在，
    用斜率当接口必定算出 inf/NaN 或者一条假水平线。方向向量对竖直切线照样成立。
    """
    kind = str(obj.get("type") or "")
    definition = obj.get("definition") if isinstance(obj.get("definition"), dict) else {}
    value = obj.get("value") if isinstance(obj.get("value"), dict) else {}
    spot = as_pair(point)
    if spot is None:
        return {"ok": False, "direction": None,
                "error": {"code": "bad_value", "message": "切线要知道切在哪一点",
                          "path": "definition.at", "hint": "先点一个曲线上的动点"}}
    if kind == "implicit":
        try:
            function = _implicit_function(obj, variables)
        except plot.ExpressionError as error:
            return {"ok": False, "direction": None,
                    "error": {"code": "bad_expression", "message": str(error),
                              "path": "definition.expr", "hint": "方程写成「左边 = 右边」"}}
        scale = _sampled_scale(_implicit_samples(obj)) or max(1.0, abs(spot[0]) + abs(spot[1]))
        # 差分步长和 _pull_to_curve 用同一个系数：两边取不同的步长会让"点"和"切线"
        # 在导数很大的地方（x^2+y^4=16 的顶部）差出可见的角度。
        step = max(1e-9, scale * 1e-5)
        gradient_x = (function(spot[0] + step, spot[1]) - function(spot[0] - step, spot[1])) \
            / (2.0 * step)
        gradient_y = (function(spot[0], spot[1] + step) - function(spot[0], spot[1] - step)) \
            / (2.0 * step)
        if not (math.isfinite(gradient_x) and math.isfinite(gradient_y)):
            return {"ok": False, "direction": None,
                    "error": {"code": "not_finite", "message": "这一点附近算不出梯度",
                              "path": "definition.to", "hint": "换一个切点试试"}}
        # 梯度 (Fx, Fy) 是法向，切线取它的垂向 (-Fy, Fx)。
        # Fy = 0（例如 x^2+y^2=16 在 (4,0) 处）→ 方向 (-0, Fx) = 竖直，**不是 NaN**。
        if math.hypot(gradient_x, gradient_y) <= 1e-15:
            return {"ok": False, "direction": None,
                    "error": {"code": "degenerate",
                              "message": "这一点的梯度是 0（曲线在这里不平滑），切线定不下来",
                              "path": "definition.to", "hint": "换一个切点试试"}}
        return {"ok": True, "direction": [-gradient_y, gradient_x], "error": None}
    if kind in ("parametric", "polar", "function"):
        if kind == "function":
            # 显函数：斜率就是导数。故意不走数值差分 —— 这条老路一直是符号求导，
            # 换成数值会把"算不出来时报错"退化成"画一条假的切线"。
            slope = None
            try:
                derived = derivative(definition.get("expr"), variables)
            except plot.ExpressionError:
                derived = {"ok": False}
            if isinstance(derived, dict) and derived.get("ok"):
                result = evaluate(derived.get("text"), {"x": spot[0]})
                if result.get("ok") and isinstance(result.get("value"), (int, float)):
                    slope = float(result["value"])
            if slope is None or not math.isfinite(slope):
                return {"ok": False, "direction": None,
                        "error": {"code": "no_value", "message": "这条曲线在这一点算不出切线",
                                  "path": "definition.to", "hint": "换一个切点，或者换一条曲线"}}
            return {"ok": True, "direction": [1.0, slope], "error": None}
        low, high = tangent_range(definition)
        if kind == "parametric":
            try:
                function_x, _used = _compile(definition.get("x"), variables)
                function_y, _used = _compile(definition.get("y"), variables)
            except plot.ExpressionError as error:
                return {"ok": False, "direction": None,
                        "error": {"code": "bad_expression", "message": str(error),
                                  "path": "definition.x", "hint": "参数曲线要写 x(t) 和 y(t)"}}
            direction = _tangent_of(
                lambda t: (float(function_x(t, 0.0)), float(function_y(t, 0.0))),
                spot_parameter(spot, value, low, high), low, high)
        else:
            try:
                radius_of, _used = _compile(definition.get("expr"), variables)
            except plot.ExpressionError as error:
                return {"ok": False, "direction": None,
                        "error": {"code": "bad_expression", "message": str(error),
                                  "path": "definition.expr", "hint": "极坐标要写 r(θ)"}}

            def polar(t):
                radius = float(radius_of(t, 0.0))
                return (radius * math.cos(t), radius * math.sin(t))

            direction = _tangent_of(polar, spot_parameter(spot, value, low, high), low, high)
        if direction is None:
            return {"ok": False, "direction": None,
                    "error": {"code": "no_value", "message": "这条曲线在这一点算不出切线",
                              "path": "definition.to", "hint": "换一个切点，或者换一条曲线"}}
        return {"ok": True, "direction": direction, "error": None}
    return {"ok": False, "direction": None,
            "error": {"code": "unsupported_type",
                      "message": "切线还不支持 %s 这类对象" % (kind or "（空类型）"),
                      "path": "definition.to",
                      "hint": "支持圆 / 函数 / 隐式曲线 / 参数曲线 / 极坐标曲线"}}


def tangent_range(definition):
    """参数曲线 / 极坐标曲线的参数区间（和采样时用的那一份一致）。"""
    low = definition.get("tmin")
    high = definition.get("tmax")
    if isinstance(low, (int, float)) and isinstance(high, (int, float)) and high > low:
        return float(low), float(high)
    return 0.0, 2 * math.pi


def spot_parameter(spot, value, low, high):
    """按切点反推参数曲线的参数 t。

    参数曲线没有"解析求 t"的通用办法，服务端也只存了采样折线；拿**最近那段折线上的
    弧长占比** u 映射回 [tmin, tmax] 就够（差分本来就在采样点附近做），
    而且投影不出来时退回区间左端 —— 绝不会因为解不出来就画到别处去。
    """
    segments = []
    for group in (value.get("segments") or []):
        line = [pair for pair in (as_pair(item) for item in group or []) if pair is not None]
        if len(line) >= 2:
            segments.append(line)
    best = _nearest_on_segments(spot, segments)
    if best is None:
        return low
    _distance, _foot, u, _branch, _ratio = best
    return low + (high - low) * u


# 不等式：<= / >= / == 要排在 < / > 前面，正则的择一顺序保证不会切错
_COMPARE = re.compile(r"(<=|>=|==|=|<|>)")


def _comparison(text, variables):
    """把 ``y > x^2`` 变成 ``(判断函数, 用到的参数名)``。"""
    parts = _COMPARE.split(str(text or "").strip(), 1)
    if len(parts) != 3 or not parts[0].strip() or not parts[2].strip():
        raise plot.ExpressionError("不等式要写成「左边 < 右边」，例如 y > x^2")
    left, operator, right = parts
    function, used = _compile("(%s) - (%s)" % (left, right), variables)
    return _test_of(function, operator), used


def _test_of(function, operator):
    if operator in ("<", "<="):
        return lambda x, y: function(x, y) <= 0            # nan 比较恒为 False，正好排除
    if operator in (">", ">="):
        return lambda x, y: function(x, y) >= 0
    return lambda x, y: abs(function(x, y)) <= 1e-9         # 等号：退化区域，给极薄的一层


@_guard(_CURVE_DEFAULTS)
def sample_inequality(expr=None, exprs=None, op="and", bounds=None, grid=None,
                      variables=None):
    """不等式区域：**粗网格掩码**（不是精确边界多边形）。

    每个格心判一次真假，再把每行里连续的格合并成游程矩形
    ``[x0, y0, x1, y1]``，SVG 直接画 ``<rect>`` 就行。选掩码而不是精确边界是因为
    老师看到的就是一块填色，精确描边要再跑一趟等值线，教室里没有收益。
    """
    texts = []
    if expr:
        texts.append(expr)
    if isinstance(exprs, (list, tuple)):
        texts.extend(exprs)
    texts = [str(text).strip() for text in texts if str(text or "").strip()]
    if not texts:
        return _curve_error("missing_field", "不等式区域要写 expr 或 exprs",
                            "definition.expr", "例如 y > x^2")
    combine_and = str(op or "and").lower() != "or"
    try:
        tests = []
        for text in texts:
            test, _used = _comparison(text, variables)
            tests.append(test)
        x_min, x_max, y_min, y_max = _resolve_bounds(bounds)
    except plot.ExpressionError as error:
        return _curve_error("bad_expression", str(error), "definition.expr",
                            "写成 y > x^2 或 x^2+y^2 <= 1")
    except (TypeError, ValueError) as error:
        return _curve_error("bad_value", "画图范围不对（%s）" % error, "view",
                            "范围要是有限的数字")
    try:
        count = int(grid) if grid is not None else INEQUALITY_GRID
    except (TypeError, ValueError):
        count = INEQUALITY_GRID
    count = max(8, min(MAX_IMPLICIT_GRID, count))

    cell = (x_max - x_min) / float(count)
    step_y = (y_max - y_min) / float(count)
    regions = []
    inside_cells = 0
    truncated = False
    for row in range(count):
        y0 = y_min + step_y * row
        y_center = y0 + step_y / 2.0
        run_start = None
        for column in range(count + 1):
            inside = False
            if column < count:
                x_center = x_min + cell * column + cell / 2.0
                if combine_and:
                    inside = all(test(x_center, y_center) for test in tests)
                else:
                    inside = any(test(x_center, y_center) for test in tests)
            if inside:
                inside_cells += 1
                if run_start is None:
                    run_start = column
                continue
            if run_start is not None:
                if len(regions) >= MAX_REGION_RUNS:
                    truncated = True
                else:
                    regions.append([round(x_min + cell * run_start, 6),
                                    round(y0, 6),
                                    round(x_min + cell * column, 6),
                                    round(y0 + step_y, 6)])
                run_start = None
    if not regions:
        return _curve_error("empty_region", "这块范围里没有满足条件的点",
                            "definition.expr", "把范围调大，或者检查不等号方向")
    return _curve_ok("inequality", [], regions=regions, samples=count,
                     extra={"cell": round(cell, 6), "cols": count, "rows": count,
                            "area": round(inside_cells * cell * step_y, 6)})


def sample_object(obj, view=None, variables=None):
    """按对象类型选采样方式（P5 渲染层的入口）。

    ``view`` 是平面的 ``view`` 字典，``variables`` 是命名参数（滑动条）的当前值，
    两者都由 :meth:`plane.Plane` 提供：``sample_object(obj, plane.view(), plane.variables())``。
    """
    if not isinstance(obj, dict):
        return _curve_error("bad_value", "要采样的不是平面对象", "object", "传 PlaneObject 字典")
    kind = str(obj.get("type") or "")
    definition = obj.get("definition") if isinstance(obj.get("definition"), dict) else {}
    bounds = dict(DEFAULT_BOUNDS)
    if isinstance(view, dict):
        for key in bounds:
            if key in view:
                bounds[key] = view[key]
        # samples 不在 DEFAULT_BOUNDS 里，但上层可以用它给"自动区间"的建议采样点数
        # （范围一次采好几屏，点少了线就一节一节的）
        if "samples" in view:
            bounds["samples"] = view["samples"]
    if kind == "function":
        # samples 缺省时，只有"自动区间"（没写 xmin/xmax）才用 bounds 里的建议点数：
        # 那种情况一次采好几屏，点少了线会一节一节的；钉死区间的曲线照旧 480 点。
        auto = definition.get("xmin") is None or definition.get("xmax") is None
        samples = definition.get("samples")
        if samples is None and auto:
            samples = bounds.get("samples")
        return sample_function(definition.get("expr"),
                               definition.get("xmin", bounds["xmin"]),
                               definition.get("xmax", bounds["xmax"]),
                               samples, variables)
    if kind == "implicit":
        # 对象自己钉了 grid 就用它的；没钉才用上层按可见范围建议的格子密度
        # （视野放大后格子不跟着放大，线会一节一节的）
        grid = definition.get("grid")
        if grid is None:
            grid = bounds.get("grid")
        return sample_implicit(definition.get("expr"), bounds, grid, variables)
    if kind == "parametric":
        return sample_parametric(definition.get("x"), definition.get("y"),
                                 definition.get("tmin"), definition.get("tmax"),
                                 definition.get("samples"), variables)
    if kind == "polar":
        return sample_polar(definition.get("expr"),
                            definition.get("tmin"), definition.get("tmax"),
                            definition.get("samples"), variables)
    if kind == "inequality":
        region_grid = definition.get("grid")
        if region_grid is None:
            region_grid = bounds.get("grid")
        return sample_inequality(definition.get("expr"), definition.get("exprs"),
                                 definition.get("op", "and"), bounds,
                                 region_grid, variables)
    return _curve_error("unsupported_type",
                        "%s 不是曲线类对象，采样只支持 %s"
                        % (kind or "（空类型）", "、".join(TYPES_WITH_CURVES)),
                        "type", "用 plane_math.sample_object 只处理曲线对象")


# --------------------------------------------------------------------------
# 动点：在父对象的路径上取参数
# --------------------------------------------------------------------------

def _point_ok(point):
    """动点坐标**原样**返回，不在这里四舍五入。

    JSON 只需要"有限数"；而这些坐标是下游（切线方向、求交、沿曲线取参）的输入。
    曾经按 6 位小数截过一次，结果在 x^2+y^4=16 的顶部（导数 ~30）动点落到曲线外
    |F| ≈ 1e-5：老师要的是"这个点真的在曲线上"，这点精度不能省。
    """
    return {"ok": True, "point": [float(point[0]), float(point[1])], "error": None}


def _point_error(code, message, path="", hint=""):
    return {"ok": False, "point": None,
            "error": {"code": code, "message": message, "path": path, "hint": hint}}


def as_pair(value):
    """把坐标统一成 ``[x, y]``。

    对象 ``value`` 里坐标是 ``{"x": .., "y": ..}``（前端直接能用），内部算的时候
    一律先过这里，两种形状都认。
    """
    if isinstance(value, dict):
        x, y = value.get("x"), value.get("y")
    elif isinstance(value, (list, tuple)) and len(value) >= 2:
        x, y = value[0], value[1]
    else:
        return None
    if isinstance(x, bool) or isinstance(y, bool):
        return None
    if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
        return None
    if not (math.isfinite(x) and math.isfinite(y)):
        return None
    return [float(x), float(y)]


def _is_point(value):
    return as_pair(value) is not None


@_guard()
def point_on_object(obj, parameter=0.5, variables=None):
    """动点：在父对象路径上取参数 ``t``（0..1）处的坐标。

    支持 segment / ray / line / vector / circle / arc / sector / polygon / function，
    以及"点 + 方向"的垂线 / 平行线 / 中垂线 / 角平分线 / 切线。
    """
    if not isinstance(obj, dict):
        return _point_error("bad_value", "动点的父对象不是平面对象", "definition.on")
    kind = str(obj.get("type") or "")
    value = obj.get("value") if isinstance(obj.get("value"), dict) else {}
    t = _finite(parameter, 0.5)
    if kind in ("segment", "ray", "line", "vector"):
        start, end = as_pair(value.get("start")), as_pair(value.get("end"))
        if start is None or end is None:
            return _point_error("no_value", "%s 还没有端点坐标" % kind, "definition.on",
                                "先让父对象求值成功")
        return _point_ok([start[0] + (end[0] - start[0]) * t,
                          start[1] + (end[1] - start[1]) * t])
    if kind in ("perpendicular", "parallel", "perpendicular_bisector",
                "angle_bisector", "tangent"):
        # 垂线 / 平行线 / 中垂线 / 角平分线 / 切线：值里是"点 + 方向"、没有端点，
        # 参数 t 就是沿方向走 t 个单位（t=0 落在那个点上）——和网页端取参的方式一致。
        line = line_of(value)
        if line is None:
            return _point_error("no_value", "%s 还没有确定下来" % kind, "definition.on",
                                "先让父对象求值成功")
        point, direction = line["point"], line["direction"]
        return _point_ok([point[0] + direction[0] * t, point[1] + direction[1] * t])
    if kind == "circle":
        center, radius = as_pair(value.get("center")), value.get("radius")
        if center is None or not isinstance(radius, (int, float)):
            return _point_error("no_value", "圆还没有圆心或半径", "definition.on",
                                "先让父对象求值成功")
        angle = 2 * math.pi * t
        return _point_ok([center[0] + radius * math.cos(angle),
                          center[1] + radius * math.sin(angle)])
    if kind in ("arc", "sector"):
        start, end = value.get("startAngle"), value.get("endAngle")
        center, radius = as_pair(value.get("center")), value.get("radius")
        if center is None or not isinstance(radius, (int, float)) \
                or not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
            return _point_error("no_value", "%s 还没有角度或半径" % kind, "definition.on",
                                "先让父对象求值成功")
        angle = start + (end - start) * t
        return _point_ok([center[0] + radius * math.cos(angle),
                          center[1] + radius * math.sin(angle)])
    if kind == "polygon":
        raw = value.get("points")
        points = [as_pair(point) for point in raw] if isinstance(raw, list) else []
        points = [point for point in points if point is not None]
        if len(points) < 2:
            return _point_error("no_value", "多边形还没有顶点", "definition.on",
                                "先让父对象求值成功")
        lengths = []
        total = 0.0
        for index in range(len(points)):
            first = points[index]
            second = points[(index + 1) % len(points)]
            length = math.hypot(second[0] - first[0], second[1] - first[1])
            lengths.append(length)
            total += length
        if total <= 0:
            return _point_error("no_value", "多边形退化了（所有顶点重合）", "definition.on")
        target = (t % 1.0) * total
        walked = 0.0
        for index, length in enumerate(lengths):
            if walked + length >= target or index == len(lengths) - 1:
                ratio = 0.0 if length <= 0 else (target - walked) / length
                first = points[index]
                second = points[(index + 1) % len(points)]
                return _point_ok([first[0] + (second[0] - first[0]) * ratio,
                                  first[1] + (second[1] - first[1]) * ratio])
            walked += length
    if kind == "function":
        definition = obj.get("definition") if isinstance(obj.get("definition"), dict) else {}
        try:
            function, _used = _compile(definition.get("expr"), variables)
            x_min, x_max = _glider_range(definition, value)
        except plot.ExpressionError as error:
            return _point_error("bad_expression", str(error), "definition.on")
        x = x_min + (x_max - x_min) * t
        return _point_ok([x, function(x)])
    if kind in ("implicit", "parametric", "polar"):
        # 方程 / 参数 / 极坐标：参数 = 采样折线上的**弧长占比**（curve_glider 里）
        outcome = curve_glider(obj, t, variables)
        if not outcome.get("ok"):
            return _point_error((outcome.get("error") or {}).get("code") or "no_value",
                                (outcome.get("error") or {}).get("message") or "动点算不出来",
                                "definition.on",
                                (outcome.get("error") or {}).get("hint") or "")
        return _point_ok(outcome["point"])
    return _point_error("unsupported_type",
                        "动点还不支持挂在 %s 上" % (kind or "（空类型）"), "definition.on",
                        "支持线段/直线/射线/圆/弧/扇形/多边形/函数/方程曲线/参数曲线/极坐标曲线")


# --------------------------------------------------------------------------
# 度量：复用 calc.Exact，和计算器面板给一样的精确值
# --------------------------------------------------------------------------

def number_result(value):
    """把 Exact 或 float 包成和计算器面板一致的结果形状。"""
    if isinstance(value, calc.Exact):
        return {"ok": True, "exact": calc.format_exact(value),
                "decimal": calc.decimal_text(value), "value": value.to_float(),
                "approximate": False, "error": None}
    try:
        number = float(value)
    except (TypeError, ValueError):
        return _number_error("bad_value", "算不出数值")
    if not math.isfinite(number):
        return _number_error("not_finite", "结果不是有限的数")
    found = calc.Exact.from_float(number)
    decimal = calc.decimal_text(number)
    exact = calc.format_exact(found) if found is not None else ""
    if exact == decimal:
        exact = ""
    return {"ok": True, "exact": exact, "decimal": decimal, "value": number,
            "approximate": found is None, "error": None}


def _fractions(points):
    """坐标能还原成"简单有理数"就还原（整点最典型），还原不出来返回 None。

    复用 ``plot._nice_fraction``：老师写的 0.8660254 直接丢给 Exact 会算出一个
    天文数字，比不给还糟。
    """
    out = []
    for point in points:
        pair = as_pair(point)
        if pair is None:
            return None
        exact = [plot._nice_fraction(pair[0]), plot._nice_fraction(pair[1])]
        if exact[0] is None or exact[1] is None:
            return None
        out.append(exact)
    return out


@_guard(_NUMBER_DEFAULTS)
def exact_distance(first, second):
    """两点距离。``first`` / ``second`` 是 ``{"x","y"}`` 或 ``[x, y]``。"""
    first, second = as_pair(first), as_pair(second)
    if first is None or second is None:
        return _number_error("bad_value", "距离要两个点", "args", "传 {x, y} 或 [x, y]")
    dx = second[0] - first[0]
    dy = second[1] - first[1]
    pair = _fractions([first, second])
    if pair is not None:
        (ax, ay), (bx, by) = pair
        squared = (bx - ax) * (bx - ax) + (by - ay) * (by - ay)
        return number_result(calc.Exact.rational(squared).sqrt())
    return number_result(math.hypot(dx, dy))


@_guard(_NUMBER_DEFAULTS)
def exact_slope(first, second):
    """两点连线的斜率（竖直线给结构化错误，不是 inf）。"""
    first, second = as_pair(first), as_pair(second)
    if first is None or second is None:
        return _number_error("bad_value", "斜率要两个点", "args", "传 {x, y} 或 [x, y]")
    pair = _fractions([first, second])
    if pair is not None:
        (ax, ay), (bx, by) = pair
        if bx == ax:
            return _number_error("no_slope", "竖直线没有斜率", "args", "改用倾斜角表示")
        return number_result(calc.Exact.rational((by - ay) / (bx - ax)))
    dx = second[0] - first[0]
    dy = second[1] - first[1]
    if dx == 0:
        return _number_error("no_slope", "竖直线没有斜率", "args", "改用倾斜角表示")
    return number_result(dy / dx)


@_guard(_NUMBER_DEFAULTS)
def exact_area(points):
    """多边形面积（鞋带公式）。点按顺序给，逆时针/顺时针都行，面积取绝对值。"""
    if not isinstance(points, (list, tuple)) or len(points) < 3:
        return _number_error("bad_value", "面积至少要三个点", "args", "传顶点数组")
    pairs = [as_pair(point) for point in points]
    if any(pair is None for pair in pairs):
        return _number_error("bad_value", "顶点坐标不对", "args", "传 {x, y} 或 [x, y]")
    pair = _fractions(pairs)
    if pair is not None:
        total = 0
        for index in range(len(pair)):
            x1, y1 = pair[index]
            x2, y2 = pair[(index + 1) % len(pair)]
            total += x1 * y2 - x2 * y1
        return number_result(calc.Exact.rational(abs(total) / 2))
    total = 0.0
    for index in range(len(pairs)):
        x1, y1 = pairs[index]
        x2, y2 = pairs[(index + 1) % len(pairs)]
        total += x1 * y2 - x2 * y1
    return number_result(abs(total) / 2.0)


@_guard(_NUMBER_DEFAULTS)
def exact_angle(first, vertex, third):
    """∠ABC（顶点在 ``vertex``）：给度数，另外给 sin / cos 的精确值。"""
    first, vertex, third = as_pair(first), as_pair(vertex), as_pair(third)
    if first is None or vertex is None or third is None:
        return _number_error("bad_value", "角要三个点", "args", "传 {x, y} 或 [x, y]")
    ux = first[0] - vertex[0]
    uy = first[1] - vertex[1]
    vx = third[0] - vertex[0]
    vy = third[1] - vertex[1]
    norm_u = ux * ux + uy * uy
    norm_v = vx * vx + vy * vy
    if norm_u < 1e-12 or norm_v < 1e-12:
        return _number_error("degenerate", "角的两条边有一条退化了", "args", "检查点的坐标")
    dot = ux * vx + uy * vy
    cosine = dot / math.sqrt(norm_u * norm_v)
    cosine = max(-1.0, min(1.0, cosine))
    degrees = math.degrees(math.acos(cosine))
    sine = abs(ux * vy - uy * vx) / math.sqrt(norm_u * norm_v)
    out = {"ok": True, "value": degrees, "decimal": calc.decimal_text(degrees, 6),
           "exact": "", "approximate": True, "sin": "", "cos": "",
           # 小数值一直给：前端拖动中显示它，静止时显示 exact/sin/cos 的精确形式
           "sinValue": round(sine, 6), "cosValue": round(cosine, 6),
           "radians": round(math.radians(degrees), 12), "error": None}
    pair = _fractions([first, vertex, third])
    if pair is not None:
        (ax, ay), (bx, by), (cx, cy) = pair
        ux_e, uy_e = ax - bx, ay - by
        vx_e, vy_e = cx - bx, cy - by
        squared = (ux_e * ux_e + uy_e * uy_e) * (vx_e * vx_e + vy_e * vy_e)
        try:
            denominator = calc.Exact.rational(squared).sqrt()
            cosine_text = calc.format_exact(
                calc.Exact.rational(ux_e * vx_e + uy_e * vy_e) / denominator)
            sine_text = calc.format_exact(
                calc.Exact.rational(abs(ux_e * vy_e - uy_e * vx_e)) / denominator)
            # 太长的精确值等于没给：拖出来的坐标（1.37 这种）分母一大就会写出
            # 「191√52610/52610」，又长又没用 —— 这种时候只给小数就好。
            if len(sine_text) <= 12 and len(cosine_text) <= 12:
                out["cos"], out["sin"] = cosine_text, sine_text
                out["approximate"] = False
        except (ValueError, ZeroDivisionError):
            out["sin"] = out["cos"] = ""
    if out["exact"] == out["decimal"]:
        out["exact"] = ""
    return out


@_guard(_NUMBER_DEFAULTS)
def exact_line_angle(first, second):
    """两条直线（线段/射线/向量/垂线/切线都算）所成的角：取**锐角**，给度数 + sin/cos 精确值。

    和空间那边 ``plot._solid_angle_info`` 的线线角是同一套算法：
    ``cosθ = |u·v| / (|u||v|)``、``sinθ = |u×v| / (|u||v|)``，所以结果恒在 0..90 度。

    精确值只在**方向向量是简单有理数**时才算（整点连出来的线最典型）：坐标是拖出来的
    浮点时不算 —— 那样会得到分子分母十几位的天文学分数，比不给还糟。算不出精确值时，
    0/30/45/60/90 这几个特殊角查表给漂亮答案（和空间那边一个规矩）。
    """
    line_a, line_b = line_of(first), line_of(second)
    if line_a is None or line_b is None:
        return _number_error("bad_value", "两条线所成的角要两条直线或线段",
                             "definition.args", "传 {start,end} 或 {point,direction} 形状的对象")
    ux, uy = line_a["direction"]
    vx, vy = line_b["direction"]
    norm_u = ux * ux + uy * uy
    norm_v = vx * vx + vy * vy
    if norm_u < 1e-12 or norm_v < 1e-12:
        return _number_error("degenerate", "有一条线退化成点了", "definition.args",
                             "换两条正常的线")
    scale = math.sqrt(norm_u * norm_v)
    cosine = max(-1.0, min(1.0, abs(ux * vx + uy * vy) / scale))
    sine = abs(ux * vy - uy * vx) / scale
    degrees = math.degrees(math.acos(cosine))
    out = {"ok": True, "value": degrees, "decimal": calc.decimal_text(degrees, 6),
           "exact": "", "approximate": True, "sin": "", "cos": "",
           "sinValue": round(sine, 6), "cosValue": round(cosine, 6),
           "radians": round(math.radians(degrees), 12), "error": None}
    pair = _fractions([[ux, uy], [vx, vy]])
    if pair is not None:
        (ux_e, uy_e), (vx_e, vy_e) = pair
        squared = (ux_e * ux_e + uy_e * uy_e) * (vx_e * vx_e + vy_e * vy_e)
        # 分母太大就别算了：Exact 化简会又慢又没意义（老师要的是 3/5 这种漂亮值）
        if squared <= 10 ** 10:
            try:
                denominator = calc.Exact.rational(squared).sqrt()
                cosine_text = calc.format_exact(
                    calc.Exact.rational(abs(ux_e * vx_e + uy_e * vy_e)) / denominator)
                sine_text = calc.format_exact(
                    calc.Exact.rational(abs(ux_e * vy_e - uy_e * vx_e)) / denominator)
                # 同上：精确值太长就不给（拖出来的坐标最容易碰上），只留小数
                if len(sine_text) <= 12 and len(cosine_text) <= 12:
                    out["sin"], out["cos"] = sine_text, cosine_text
                    out["approximate"] = False
            except (ValueError, ZeroDivisionError):
                out["sin"] = out["cos"] = ""
    if out["approximate"]:
        for nice in (0, 30, 45, 60, 90):
            if abs(degrees - nice) < 0.02:
                out["sin"], out["cos"] = plot.NICE_ANGLES[nice]
                out["exact"] = "%d°" % nice
                out["approximate"] = False
                break
    return out


@_guard(_NUMBER_DEFAULTS)
def exact_radius(center, radius):
    """半径：``radius`` 是数字就直接精确化，是点就跟圆心算距离。"""
    if _is_point(radius):
        if not _is_point(center):
            return _number_error("bad_value", "半径要圆心或一个数字", "args")
        return exact_distance(center, radius)
    return number_result(radius)


def measure(kind, args, variables=None):
    """通用度量入口（``measure`` 类型的对象走这里）。

    ``kind``：distance / length / area / slope / angle / radius。
    """
    kind = str(kind or "").strip().lower()
    args = args if isinstance(args, (list, tuple)) else []
    if kind in ("distance", "length"):
        if len(args) >= 2:
            return exact_distance(args[0], args[1])
        return _number_error("missing_field", "距离要两个点", "definition.args",
                             "传 [[x,y],[x,y]]")
    if kind == "area":
        return exact_area(args[0] if len(args) == 1 and isinstance(args[0], list)
                          and args and isinstance(args[0][0], (list, tuple)) else args)
    if kind == "slope":
        if len(args) >= 2:
            return exact_slope(args[0], args[1])
        return _number_error("missing_field", "斜率要两个点", "definition.args")
    if kind == "angle":
        if len(args) >= 3:
            return exact_angle(args[0], args[1], args[2])
        return _number_error("missing_field", "角要三个点", "definition.args")
    if kind in ("lineangle", "angle_lines"):
        if len(args) >= 2:
            return exact_line_angle(args[0], args[1])
        return _number_error("missing_field", "两条线所成的角要两条线", "definition.args")
    if kind == "radius":
        if len(args) >= 2:
            return exact_radius(args[0], args[1])
        return _number_error("missing_field", "半径要圆心和半径（或圆上一点）", "definition.args")
    return _number_error("unknown_measure", "不认识的度量：%s" % (kind or "（空）"),
                         "definition.kind",
                         "可用：" + "、".join(("distance", "length", "area", "slope",
                                              "angle", "lineangle", "radius")))


# --------------------------------------------------------------------------
# 交点：调用点优先交给 plane_geom，拿不到才退回极简实现
# --------------------------------------------------------------------------
# 分工（老师拍板）：求交的数学归 webui/plane_geom.py（另一个 agent 在做），
# 平面这边只负责"依赖图 + 求值"这一半 —— 把两个被交对象的 value 传过去，拿回点来。
# 这里**不 import plane_geom 到模块顶层**：那份文件现在可能还不存在，也可能正在被改，
# 缺了它平面照样要能用（否则整块平面功能跟着一起挂）。

FALLBACK_SHAPES = ("line", "circle")


def _geometry_module():
    """拿到 ``plane_geom`` 模块；没有 / 有语法错都返回 None，绝不炸。"""
    try:
        import plane_geom
    except Exception:                                       # noqa: BLE001
        return None
    return plane_geom


def line_of(value):
    """从对象 ``value`` 里取出直线：``{"point", "direction", "a", "b", "c"}``。

    两种写法都认：``{point, direction}``（垂线/平行线/中垂线/切线/角平分线）和
    ``{start, end}``（线段/射线/直线/向量）。不是直线就返回 None。
    """
    if not isinstance(value, dict):
        return None
    point = as_pair(value.get("point"))
    direction = as_pair(value.get("direction"))
    if point is None or direction is None:
        start, end = as_pair(value.get("start")), as_pair(value.get("end"))
        if start is None or end is None:
            return None
        point, direction = start, [end[0] - start[0], end[1] - start[1]]
    if abs(direction[0]) < 1e-12 and abs(direction[1]) < 1e-12:
        return None
    a, b = direction[1], -direction[0]
    return {"point": point, "direction": direction, "a": a, "b": b,
            "c": -(a * point[0] + b * point[1])}


def circle_of(value):
    """从对象 ``value`` 里取出圆：``{"center": [x, y], "radius": r}``。"""
    if not isinstance(value, dict):
        return None
    center, radius = as_pair(value.get("center")), value.get("radius")
    if center is None or not isinstance(radius, (int, float)) or isinstance(radius, bool):
        return None
    if not math.isfinite(radius) or radius <= 0:
        return None
    return {"center": center, "radius": float(radius)}


def _shape_of(obj):
    if not isinstance(obj, dict):
        return None, None
    value = obj.get("value") if isinstance(obj.get("value"), dict) else {}
    if str(obj.get("type") or "") == "circle":
        circle = circle_of(value)
        if circle is not None:
            return "circle", circle
    line = line_of(value)
    if line is not None:
        return "line", line
    circle = circle_of(value)
    if circle is not None:
        return "circle", circle
    return None, None


def _line_line(first, second):
    """行列式解二元一次方程组；平行/重合返回 (None, 原因)。"""
    determinant = first["a"] * second["b"] - second["a"] * first["b"]
    scale = math.hypot(first["a"], first["b"]) * math.hypot(second["a"], second["b"])
    if scale <= 0 or abs(determinant) <= 1e-9 * scale:
        return None, "两个对象平行或重合，没有唯一交点"
    x = (first["b"] * second["c"] - second["b"] * first["c"]) / determinant
    y = (first["c"] * second["a"] - second["c"] * first["a"]) / determinant
    return [[x, y]], ""


def _line_circle(line, circle):
    """直线与圆：把参数式代进圆方程，解一元二次。"""
    px, py = line["point"]
    dx, dy = line["direction"]
    cx, cy = circle["center"]
    radius = circle["radius"]
    qa = dx * dx + dy * dy
    qb = 2.0 * (dx * (px - cx) + dy * (py - cy))
    qc = (px - cx) ** 2 + (py - cy) ** 2 - radius * radius
    if qa <= 0:
        return None, "这条直线的方向退化了"
    discriminant = qb * qb - 4 * qa * qc
    if discriminant < 0:
        return None, "直线和圆没有交点"
    if discriminant == 0:
        roots = [-qb / (2 * qa)]
    else:
        root = math.sqrt(discriminant)
        roots = [(-qb - root) / (2 * qa), (-qb + root) / (2 * qa)]
    roots.sort()
    return [[px + dx * t, py + dy * t] for t in roots], ""


def fallback_intersection(first, second):
    """极简兜底求交：**只有直线×直线、直线×圆**（两圆交给 plane_geom）。

    这是"plane_geom 不可用"时的保命实现，不是正式几何求解器：
    把线段/射线也当成整条直线看（不做端点裁剪）。
    """
    kind_a, shape_a = _shape_of(first)
    kind_b, shape_b = _shape_of(second)
    if kind_a is None or kind_b is None:
        return None, "这两个对象现在算不出交点（需要直线/线段/圆）"
    if kind_a == "line" and kind_b == "line":
        return _line_line(shape_a, shape_b)
    if kind_a == "line" and kind_b == "circle":
        return _line_circle(shape_a, shape_b)
    if kind_a == "circle" and kind_b == "line":
        return _line_circle(shape_b, shape_a)
    return None, "兜底实现不支持两圆求交（plane_geom 可用时会自动用它）"


def _tolerated_reason(raw):
    """plane_geom 明确说"这种对象我还不支持"时返回原因，否则 None。

    这类回答说明**它答不了**（不是"两个对象真的不相交"），所以调用方应该换一种签名再问、
    最后退回兜底。真正几何意义上的回答（平行、相离、切线…）不在这一档。
    """
    if not isinstance(raw, dict) or raw.get("ok") is not False:
        return None
    errors = raw.get("errors")
    code = ""
    message = raw.get("message") or ""
    if isinstance(errors, list) and errors and isinstance(errors[0], dict):
        code = str(errors[0].get("code") or "")
        message = errors[0].get("message") or message
    if code in ("internal_error", "unsupported_type", "bad_object"):
        return "%s（%s）" % (message or "它答不了这一对对象", code)
    return None


def _as_intersection(raw, index):
    """把 plane_geom 的返回值翻译成本模块的形状；认不出来返回 None。

    plane_geom 的信封（见它自己的 ``_ok_result`` / ``_error_result``）是
    ``{"ok": True, "points": [[x, y], …], "count", "exact", "tangent", "overlap",
    "truncated", "warnings"}``；失败是 ``{"ok": False, "errors": [{code, message,
    path, hint}]}``。**签名对不上时它自己会把 TypeError 包成 ``internal_error``**，
    那种情况返回 None，让调用方换一种签名再问，最后走兜底。
    """
    if isinstance(raw, dict):
        if raw.get("ok") is False:
            item = {}
            errors = raw.get("errors")
            if isinstance(errors, list) and errors and isinstance(errors[0], dict):
                item = errors[0]
            if item.get("code") == "internal_error":
                return None                 # 多半是签名不匹配，不是"真的不相交"
            message = raw.get("message") or item.get("message") or "算不出交点"
            return {"ok": False, "point": None, "points": [], "source": "plane_geom",
                    "error": {"code": item.get("code") or "no_intersection",
                              "message": str(message),
                              "path": item.get("path") or "definition.intersect",
                              "hint": item.get("hint") or "检查两个对象是否真的相交"}}
        points = raw.get("points")
        if isinstance(points, (list, tuple)):
            pairs = [as_pair(point) for point in points]
            pairs = [pair for pair in pairs if pair is not None]
            return _pick(pairs, index, "plane_geom", raw)
        pair = as_pair(raw.get("point") if "point" in raw else raw)
        if pair is not None:
            return _pick([pair], index, "plane_geom", raw)
        return None
    if isinstance(raw, (list, tuple)) and raw:
        pairs = [as_pair(point) for point in raw]
        pairs = [pair for pair in pairs if pair is not None]
        if pairs:
            return _pick(pairs, index, "plane_geom")
        return None
    pair = as_pair(raw)
    if pair is not None:
        return _pick([pair], index, "plane_geom")
    return None


def _pick(points, index, source, envelope=None):
    if index >= len(points):
        # ★ 两个图形**重合**（无穷多交点）时 plane_geom 只给有限端点、甚至一个点都不给。
        #   这时如果说"取不到第 N 个交点"，老师会以为工具坏了 —— 其实是重合。
        #   信封里的 status/overlap/message 就是为了区分这种情况。
        status = ""
        overlap = False
        detail = ""
        if isinstance(envelope, dict):
            status = str(envelope.get("status") or "")
            overlap = bool(envelope.get("overlap"))
            detail = str(envelope.get("message") or "")
        if status == "infinite" or overlap:
            return {"ok": False, "point": None, "points": points, "source": source,
                    "error": {"code": "coincident",
                              "message": "两个图形重合，有无穷多个交点"
                                         + ("（%s）" % detail if detail else ""),
                              "path": "definition.intersect",
                              "hint": "要单个交点就先让两个图形错开；"
                                      "想确认是否重合，看 error.code == \"coincident\""}}
        # 求解器给的说明比"取不到第 N 个"有用得多（例如"没有交点：两条直线平行"），
        # 有就带上；没有才退回这句通用话。
        tail = ("（%s）" % detail) if detail else ""
        return {"ok": False, "point": None, "points": points, "source": source,
                "error": {"code": "no_intersection",
                          "message": "只找到 %d 个交点，取不到第 %d 个%s"
                                     % (len(points), index + 1, tail),
                          "path": "definition.index", "hint": "把 index 改小一点"}}
    return {"ok": True, "point": points[index], "points": points, "source": source,
            "error": None}


def _ask_plane_geom(module, first, second, index, objects=None):
    """按几种可能的签名问 plane_geom。返回 ``(结果或 None, 答不了的原因)``。"""
    attempts = []
    if isinstance(objects, dict):
        # need：要第 index+1 个交点。plane_geom 会在"找不够"时把自动区间的窗口张开重试。
        try:
            need = max(1, int(index) + 1)
        except (TypeError, ValueError):
            need = 1
        attempts.append(("intersect_objects", (first, second, objects), {"need": need}))
    attempts.extend([
        ("intersect_objects", (first, second), {}),
        ("intersect", (first, second), {"index": index}),
        ("intersections", (first, second), {}),
    ])
    note = ""
    for name, args, kwargs in attempts:
        function = getattr(module, name, None)
        if not callable(function):
            continue
        try:
            raw = function(*args, **kwargs)
        except Exception:                                   # noqa: BLE001
            continue
        reason = _tolerated_reason(raw)
        if reason is not None:
            note = reason
            continue
        converted = _as_intersection(raw, index)
        if converted is not None:
            return converted, ""
    return None, note


@_guard()
def intersection(first, second, index=0, objects=None):
    """交点求解的**唯一调用点**：``plane.py`` 只调这个。

    顺序：先 ``plane_geom.intersect_objects``（另一个 agent 的求解器，接口见下），
    拿不到再走兜底极简实现。返回 ``{"ok", "point": [x, y], "points": [[x, y], ...],
    "source", "error"}``，``source`` 是 ``"plane_geom"`` 或 ``"fallback"`` —— 用了兜底时
    调用方要在 ``warnings`` 里说明，别让老师以为求交是完整的。

    ``objects`` 是整张对象表（``{id: PlaneObject}``），plane_geom 解析引用要用；
    兜底实现不看它。``plane_geom`` 侧只要满足下面任一条就能被接上（都接不上才兜底）::

        plane_geom.intersect_objects(object_a, object_b, objects=None) -> {"ok", "points"}
        plane_geom.intersect(first, second, index=0)                   -> {"ok", "point"}
        plane_geom.intersections(first, second)                        -> {"points": [...]}
    """
    try:
        wanted = int(index)
    except (TypeError, ValueError):
        wanted = 0
    if wanted < 0:
        wanted = 0
    module = _geometry_module()
    note = ""
    if module is not None:
        asked, note = _ask_plane_geom(module, first, second, wanted, objects)
        if asked is not None:
            return asked
    points, reason = fallback_intersection(first, second)
    if points is None:
        return {"ok": False, "point": None, "points": [], "source": "fallback",
                "reason": note,
                "error": {"code": "no_intersection", "message": reason,
                          "path": "definition.intersect",
                          "hint": "检查两个父对象是否真的相交；两圆求交要等 plane_geom"}}
    result = _pick(points, wanted, "fallback")
    result["reason"] = note or "plane_geom 不可用"
    return result
