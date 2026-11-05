# -*- coding: utf-8 -*-
"""平面几何求解层：**交点**、点在对象上、最近点（垂足）、距离、零点。

为什么要单独一层：
  - ``plane.py`` 是对象模型与依赖图，它不该知道"直线和圆怎么解"；
  - ``plane_math.py`` 是采样层（把表达式变成点），它不该知道"求交"；
  - 老师点名的三件事是**交点、绘制、联动**，交点最容易出错（平行、相切、重合、
    采样缝上的重复点），所以单独一个纯数学模块，好测、好换。

这个模块**没有状态、不读文件、不联网**，纯函数，任何输入都不会抛异常。

公开 API（全部"全函数"：非法输入返回结构化中文错误，绝不抛出去）
--------------------------------------------------------------------------
    intersect(kind_a, data_a, kind_b, data_b, **options) -> dict
        两个对象求交。kind 取 KIND_NAMES 里的一个。
    intersect_objects(obj_a, obj_b, objects=None, **options) -> dict
        直接吃架构文档里的 PlaneObject（内部先 geometry_of 再 intersect），
        ``plane.py`` 只用调这一个函数。
    geometry_of(obj, objects=None, samples=None) -> dict
        把 PlaneObject 的 definition/value 归一化成 (kind, data)。
    point_on(kind, data, point, tol=1e-9) -> dict
        "点在对象上"（动点约束要用）。
    closest_point_on(kind, data, point, **options) -> dict
        最近点（垂足、投影、点到曲线的最近点）。
    distance(kind, data, point, **options) -> dict
        点到对象的距离。
    roots_on_interval(f, x_min, x_max, samples=400, **options) -> dict
        区间内的零点（符号变号扫描 + 二分细化）。
    nearest_root_interval(f, x, x_min, x_max, samples=400, **options) -> dict
        离 x 最近的那个零点，以及夹住它的区间。

options（都可省）：``tol``（默认 1e-9）、``merge_tol``（去重容差，默认按采样步长推）、
``samples``（隐式曲线采样网格，默认 96）、``window``（[xmin,xmax,ymin,ymax]）、
``refine``（默认 True）、``max_pairs``（线段对上限，封顶 MAX_PAIR_TESTS）。

返回形状（所有函数都是这个信封，按需取键；**先看 ok**）
--------------------------------------------------------------------------
    {
      "ok": bool,          # 只表示"输入合法、算完了"。数学上"没有交点"也是 ok=True
      "status": "none" | "one" | "two" | "many" | "infinite" | "error",
      "points": [[x, y], ...],   # 按 x 再按 y 升序，已去重
      "count": int,
      "exact": [[str, str], ...] | None,  # 精确形式（"√2/2"）；全都能精确才有
      "tangent": bool,     # 相切（重根 / 内切 / 外切）
      "overlap": bool,     # 有重合段 → 无穷多交点
      "truncated": bool,   # 触到上限，结果是部分的，warnings 里有说明
      "message": str,      # 中文，可以直接念给老师听
      "errors": [{"code", "message", "path", "hint"}],   # 与架构文档 §5.2 一致
      "warnings": [str],
    }

    point_on / closest_point_on / distance 额外带 "on" / "point" / "parameter" / "value"；
    roots_on_interval 额外带 "roots"；nearest_root_interval 额外带 "root" / "interval"。
    出错时这些键都在，只是值为 None（**先看 ok**）。

kind 与 data（冻结；``geometry_of`` 会生成同样形状）
--------------------------------------------------------------------------
    "point"    {"x": f, "y": f}                      或 [x, y]
    "line"     {"a": [x, y], "b": [x, y]}            两点定直线（也收 start/end）
    "segment"  同上                                   a、b 是两端
    "ray"      同上                                   a 是端点，指向 b
    "circle"   {"center": [x, y], "radius": f}
    "conic"    {"a","b","c","d","e","f"}             通用 Ax²+Bxy+Cy²+Dx+Ey+F=0
               或 {"kind":"ellipse", "center":[h,k], "rx":a, "ry":b}
               或 {"kind":"ellipse", "foci":[[..],[..]], "a":A}
               或 {"kind":"hyperbola", "center":[h,k], "rx":a, "ry":b}
               或 {"kind":"parabola", "vertex":[h,k], "p":p, "axis":"y"|"x"}
    "polyline" {"points": [[x, y], ...], "closed": bool, "curve": {...}}
               "curve" 可选，告诉求解层这条折线是哪条真曲线采样来的，交点会被细化：
               {"type":"function", "expr":"x^2", "tmin":-10, "tmax":10,
                "count":401, "runs":[[i0,i1],...]}
               {"type":"parametric", "x":"cos(t)", "y":"sin(t)", ...}
               {"type":"polar", "expr":"1+cos(theta)", ...}
    "implicit" {"expr":"x^2+y^2-1", "xmin","xmax","ymin","ymax","samples"} 或 {"f": callable}

精度与代价（说清楚，别让调用方猜）
--------------------------------------------------------------------------
  * 直线×直线、直线×圆、圆×圆、直线×圆锥曲线：有**精确路径**。输入坐标能当有理数
    （整数 / Fraction / 能还原成"漂亮分数"的浮点）时用 ``Fraction`` + ``calc.Exact``
    精确求解，``points`` 是精确值转的浮点，``exact`` 里带 "√2/2" 这种写法。
    退化（平行、重合、相切、同心、内含）用精确比较判定，不靠 eps 猜。
  * 折线×折线：x 排序扫描 + 活跃集，只测 x 区间重叠的线段对。
    复杂度 O(N log N + K)，K 是 x 区间重叠的线段对数（密采样曲线近似线性）；
    上限 ``MAX_PAIR_TESTS``（12 万对），到顶就停下并置 ``truncated``。
  * 折线带 ``curve`` 描述时，交点沿参数刻度二分细化到 ~1e-15；
    **裸折线（没有 curve）只能给到弦的精度**（O(h²)，h 是采样步长，2000 点约 1e-4）——
    这不是偷懒：一条折线在数学上就是折线，硬说 1e-9 才是撒谎。
    想让"函数图像与直线求交"精确，就让 geometry_of 生成折线（它带 curve 描述）。
  * 隐式曲线 / 圆锥曲线：先用 marching squares 采样（复用 ``plot`` 的实现），
    折线求交，再沿解析对象（直线参数 t / 圆的角度 θ）或曲线自身参数二分细化；
    隐式×隐式用 2×2 牛顿迭代。
  * 去重：解析情形用 ``tol``；折线情形用 max(tol, 采样步长的 1/4)——
    网格缝上相邻两段会各报一个几乎相同的点，这是"明明一个交点却出来两个"的真凶。
    重合段会被归并成极少数端点，不会吐几十个点。

只依赖：标准库 + 本项目的 ``plot``（复用它的解析器与 marching squares）和
``calc``（复用它的 ``Exact``）。两个都拿不到时降级：精确路径关掉、隐式采样报错。
"""

import functools
import math
import sys
import json
from fractions import Fraction

try:
    import calc as _calc
except ImportError:                    # 精确值模块不在就退化成浮点，功能不消失
    _calc = None

try:
    import plot as _plot
except ImportError:                    # 采样兜底要用它的 marching squares
    _plot = None


KIND_NAMES = ("point", "line", "segment", "ray", "circle", "conic",
              "polyline", "implicit")

MAX_COORD = 1e9              # 和 plot.MAX_COORD 一个量级：再大就该怀疑写错了，不是算不出来
MAX_POLYLINE_POINTS = 20000  # 单条折线点数上限（再密就该在采样层降采样）
MAX_PIECES = 64              # 一条曲线最多几条分支
MAX_PAIR_TESTS = 120000      # 线段对测试上限：教室机器上宁可少交点也不能卡住
# 自动区间的曲线只在自己采样到的那段里找交点；找不够时把窗口按这些倍数张开重试
# （只用在这一问上，不写回对象）。老师把直线转过去、交点跑到采样区间外面时靠它救回来。
WIDEN_FACTORS = (4.0, 16.0, 64.0)
MAX_RESULT_POINTS = 400      # 返回点数上限（病态曲线能交上千次，别把前端撑爆）
MAX_REFINE = 400             # 最多细化多少个交点（每个交点几十次求值，必须封顶）
MAX_FIELD_GRID = 140         # 隐式曲线采样网格上限（140² = 19600 个格子）
DEFAULT_FIELD_GRID = 96
DEFAULT_CURVE_SAMPLES = 401  # geometry_of 给函数曲线采多少点（和 plot 的 480 同量级）
MAX_CURVE_SAMPLES = 2001
DEFAULT_TOL = 1e-9
TOL_ANGLE = 1e-12            # 方向夹角的相对容差（判"平行"用）
TOL_TANGENT = 1e-12          # 判别式的相对容差（判"相切"用，见 _quadratic_roots）
BISECT_STEPS = 48            # 2^-48 ≈ 3.6e-15，够到双精度底
NEWTON_STEPS = 30
EXPAND_STEPS = 4             # 细化时向两边扩张找变号区间的最多次数
DEFAULT_WINDOW = (-10.0, 10.0, -10.0, 10.0)

_UNBOUNDED = None


# ---------------------------------------------------------------------------
# 0. 错误形状与"全函数"包装
# ---------------------------------------------------------------------------

class _BadInput(Exception):
    """输入不合法。只在模块内部抛，公开函数一律翻成结构化返回值。"""

    def __init__(self, code, message, path="", hint=""):
        Exception.__init__(self, message)
        self.error = {"code": code, "message": message, "path": path, "hint": hint}


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


def _bad(code, message, path="", hint=""):
    raise _BadInput(code, message, path, hint)


def _error_result(error):
    """出错时的完整信封。键是各函数键的并集，调用方**先看 ok** 就不会踩坑。"""
    return {
        "ok": False, "status": "error",
        "points": [], "count": 0, "exact": None,
        "tangent": False, "overlap": False, "truncated": False,
        "message": error["message"], "errors": [error], "warnings": [],
        "roots": [], "interval": None, "root": None, "value": None,
        "on": None, "distance": None, "point": None, "parameter": None,
    }


def _ok_result(status, points, message, exact=None, tangent=False, overlap=False,
               truncated=False, warnings=(), extra=None):
    result = {
        "ok": True, "status": status, "points": points, "count": len(points),
        "exact": exact, "tangent": bool(tangent), "overlap": bool(overlap),
        "truncated": bool(truncated), "message": message, "errors": [],
        "warnings": list(warnings),
        "roots": [], "interval": None, "root": None, "value": None,
        "on": None, "distance": None, "point": None, "parameter": None,
    }
    if extra:
        result.update(_as_mapping(extra))
    return result


def _total(function):
    """保证公开函数永不抛异常：漏网的异常变成结构化错误（带异常类型，方便报 bug）。"""
    @functools.wraps(function)
    def wrapper(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except _BadInput as bad:
            return _error_result(bad.error)
        except Exception as error:                   # noqa: BLE001  兜底，绝不能炸到界面
            return _error_result({
                "code": "internal_error",
                "message": "几何计算内部出错（%s：%s）" % (type(error).__name__, error),
                "path": "",
                "hint": "把这一组图形和这句话一起报给开发者",
            })
    return wrapper


# ---------------------------------------------------------------------------
# 1. 数值与点的基础工具（非法输入全在这里拦，后面就可以放心算）
# ---------------------------------------------------------------------------

def _number(value, path, what="数字"):
    if value is None or isinstance(value, bool):
        _bad("bad_number", "%s 不是数字：%r" % (what, value), path,
             "给它一个普通数字，例如 1.5")
    try:
        result = float(value)
    except (TypeError, ValueError):
        _bad("bad_number", "%s 不是数字：%r" % (what, value), path,
             "给它一个普通数字，例如 1.5")
    if not math.isfinite(result):
        _bad("not_finite", "%s 不是有限数（%r）" % (what, value), path,
             "NaN / Inf 不能参与几何计算，先检查这个对象的定义式")
    if abs(result) > MAX_COORD:
        _bad("coordinate_too_large", "%s 超出可算范围（|值| ≤ %g）：%r"
             % (what, MAX_COORD, value), path, "坐标或半径太大了，检查单位或写法")
    return result


def _point_value(value, path, what="点"):
    """收 [x, y] 或 {x, y}，统一成 [x, y]。"""
    if isinstance(value, dict):
        if "x" not in value or "y" not in value:
            _bad("bad_point", "%s 要有 x 和 y：%r" % (what, value), path,
                 "写成 [1, 2] 或 {\"x\": 1, \"y\": 2}")
        return [_number(value["x"], path + ".x", what + "的 x"),
                _number(value["y"], path + ".y", what + "的 y")]
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return [_number(value[0], path + "[0]", what + "的 x"),
                _number(value[1], path + "[1]", what + "的 y")]
    _bad("bad_point", "%s 要是 [x, y]：%r" % (what, value), path,
         "写成 [1, 2] 或 {\"x\": 1, \"y\": 2}")


def _segment_points(value, path, what="折线"):
    if not isinstance(value, (list, tuple)):
        _bad("bad_point_list", "%s 要是一串点：%r" % (what, value), path,
             "写成 [[0,0],[1,1],...]")
    if len(value) < 2:
        _bad("too_few_points", "%s 至少要 2 个点，现在只有 %d 个" % (what, len(value)),
             path, "补上第二个点")
    if len(value) > MAX_POLYLINE_POINTS:
        _bad("polyline_too_long", "%s 的点太多了（%d 个，上限 %d）"
             % (what, len(value), MAX_POLYLINE_POINTS), path,
             "先在采样层降采样，几何层不做抽稀")
    return [_point_value(item, "%s[%d]" % (path, index), "第 %d 个点" % (index + 1))
            for index, item in enumerate(value)]


def _cross(ox, oy, ax, ay, bx, by):
    """(A-O) × (B-O) 的二维叉积。"""
    return (ax - ox) * (by - oy) - (ay - oy) * (bx - ox)


def _magnitude(*values):
    """一组数的量级，用来把容差变成"相对"的（1e12 处的 1e-9 是荒唐的）。"""
    biggest = 1.0
    for value in values:
        value = abs(value)
        if value > biggest:
            biggest = value
    return biggest


def _as_fraction(value):
    """能当精确有理数用就返回 Fraction，否则 None（退回浮点路径）。

    只做**一次** limit_denominator：判据比 ``calc.Exact.from_float`` 的有理分支更严
    （相对 1e-12 而不是 1e-9），因为 1e-9 会把 0.999999999 也"化简"成 1 ——
    那样一条离圆 1e-9 的直线会被判成相切，老师问"为什么只有一个交点"就没法解释了。
    另外不去试 400 个根号：折线上几千个坐标每个都要转一遍，那里不能慢。
    """
    if _calc is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return Fraction(value)
    if isinstance(value, Fraction):
        return value
    if not isinstance(value, float):
        try:
            value = float(value)
        except (TypeError, ValueError):
            return None
    if not math.isfinite(value):
        return None
    try:
        ratio = Fraction(value).limit_denominator(10000)
    except (ValueError, OverflowError):
        return None
    if abs(float(ratio) - value) <= 1e-12 * max(1.0, abs(value)):
        return ratio
    return None


def _all_rational(values):
    numbers = [_as_fraction(value) for value in values]
    if any(value is None for value in numbers):
        return None
    return numbers


def _exact_number(fraction):
    return _calc.Exact.rational(fraction)


def _exact_text(fraction):
    return _calc.format_exact(_exact_number(fraction))


def _value_of(function, *args):
    """求值并吃掉异常/NaN（定义域外、除零都算"这里没有值"）。"""
    try:
        value = float(function(*args))
    except Exception:                                # noqa: BLE001
        return None
    return value if math.isfinite(value) else None


def _bisect(function, low, high, low_value):
    """二分。只要求变号，收敛到区间宽度 ~2^-48（够双精度了）。"""
    for _step in range(BISECT_STEPS):
        middle = 0.5 * (low + high)
        if middle == low or middle == high:          # 浮点已经贴到极限
            break
        value = function(middle)
        if value is None or not math.isfinite(value):
            break
        if (value < 0) == (low_value < 0):
            low, low_value = middle, value
        else:
            high = middle
    return 0.5 * (low + high)


def _quadratic_roots(a, b, c):
    """a t² + b t + c = 0 的浮点解。返回 (是否重根, [根...])。

    重根判据用的是**判别式相对浮点噪声**（TOL_TANGENT），不是调用方的几何容差：
    "相切"是数学结论，不该随 tol 变。判别式稍微正一点点就是**两个**交点——
    哪怕它们只差 1e-4，那也是两个点（老师会数出来的），
    反过来硬算两个差 1e-9 的根才是造假。
    """
    if a == 0:
        if b == 0:
            return (False, [])
        return (False, [-c / b])
    disc = b * b - 4.0 * a * c
    scale = _magnitude(b * b, 4.0 * a * c)
    if disc < -TOL_TANGENT * scale:
        return (False, [])
    if disc <= TOL_TANGENT * scale:
        return (True, [-b / (2.0 * a)])
    root = math.sqrt(disc)
    # 数值稳定写法：避免 -b ± √D 里两个相近的数相减丢有效位
    q = -0.5 * (b + math.copysign(root, b if b else 1.0))
    if q == 0:
        return (False, [(-b + root) / (2.0 * a), (-b - root) / (2.0 * a)])
    return (False, [q / a, c / q])


def _quadratic_roots_exact(a, b, c):
    """Fraction 系数 → (是否重根, [Exact...])。没有实根返回空表。"""
    if a == 0:
        if b == 0:
            return (False, [])
        return (False, [_exact_number(-c / b)])
    disc = b * b - 4 * a * c
    if disc < 0:
        return (False, [])
    if disc == 0:
        return (True, [_exact_number(-b / (2 * a))])
    root = _calc.Exact.rational(disc).sqrt()
    two_a = _exact_number(2 * a)
    return (False, [(-_exact_number(b) + root) / two_a,
                    (-_exact_number(b) - root) / two_a])


def _exact_pair(first, second):
    return [first.format(), second.format()]


def _dedupe(points, tol):
    """按网格分桶去重：tol 内的点只留排序后第一个。

    为什么用网格而不是"和上一个比"：排序后 x 相近的点确实挨着，但 y 的抖动会让
    "只比上一个"漏掉一部分；分桶 + 查 3×3 邻格是严格正确的，而且是 O(n)。
    为什么不留平均值：解析点和粗采样点混在一起时，取平均反而把准的那个拉偏了。
    """
    if tol <= 0:
        return list(points)
    kept = []
    cells = {}
    for point in points:
        cell_x = int(math.floor(point[0] / tol))
        cell_y = int(math.floor(point[1] / tol))
        found = False
        for offset_x in (-1, 0, 1):
            for offset_y in (-1, 0, 1):
                for other in cells.get((cell_x + offset_x, cell_y + offset_y), ()):
                    if abs(other[0] - point[0]) <= tol \
                            and abs(other[1] - point[1]) <= tol:
                        found = True
                        break
                if found:
                    break
            if found:
                break
        if found:
            continue
        kept.append(point)
        cells.setdefault((cell_x, cell_y), []).append(point)
    return kept


# ---------------------------------------------------------------------------
# 2. 归一化：把调用方给的 data 变成内部统一形状
# ---------------------------------------------------------------------------

def _kind_of(value, path):
    if not isinstance(value, str) or value not in KIND_NAMES:
        _bad("unknown_kind", "不认识的图形类型：%r" % (value,), path,
             "可用：" + "、".join(KIND_NAMES))
    return value


def _line_like(data, kind, path):
    """直线 / 线段 / 射线 都收两点。返回 {"a": [...], "b": [...]}。"""
    if not isinstance(data, dict):
        if isinstance(data, (list, tuple)) and len(data) == 2:
            data = {"a": data[0], "b": data[1]}
        else:
            _bad("bad_data", "%s 的数据要写成 {a: [...], b: [...]}：%r" % (kind, data),
                 path, "例如 {\"a\": [0, 0], \"b\": [1, 1]}")
    first = data.get("a", data.get("start", data.get("p1")))
    second = data.get("b", data.get("end", data.get("p2")))
    if first is None or second is None:
        _bad("missing_field", "%s 缺少端点（要有 a 和 b）" % kind, path,
             "补上两个端点，例如 {\"a\": [0, 0], \"b\": [1, 1]}")
    point_a = _point_value(first, path + ".a", "%s 的起点" % kind)
    point_b = _point_value(second, path + ".b", "%s 的终点" % kind)
    if point_a[0] == point_b[0] and point_a[1] == point_b[1]:
        if kind == "segment":
            _bad("degenerate_segment", "线段的两端是同一个点，它其实是一个点",
                 path, "把两个端点分开；或者改用 point 类型")
        if kind == "ray":
            _bad("degenerate_ray", "射线的端点和方向点重合，方向定不下来", path,
                 "再给一个和端点不同的点")
        _bad("degenerate_line", "这两个点重合，定不出直线", path, "再给一个不同的点")
    return {"a": point_a, "b": point_b}


def _circle(data, path):
    if not isinstance(data, dict):
        _bad("bad_data", "圆的数据要写成 {center: [...], radius: r}：%r" % (data,),
             path, "例如 {\"center\": [0, 0], \"radius\": 3}")
    center = data.get("center", data.get("centre"))
    if center is None:
        _bad("missing_field", "圆缺少圆心 center", path + ".center",
             "补上圆心，例如 {\"center\": [0, 0], \"radius\": 3}")
    radius = data.get("radius", data.get("r"))
    if radius is None:
        _bad("missing_field", "圆缺少半径 radius", path + ".radius",
             "补上半径；只知道圆上一点就先把它算成半径再传进来")
    center = _point_value(center, path + ".center", "圆心")
    radius = _number(radius, path + ".radius", "半径")
    if radius < 0:
        _bad("negative_radius", "半径不能是负数：%r" % (radius,), path + ".radius",
             "半径是长度，取绝对值或改定义")
    if radius == 0:
        _bad("zero_radius", "半径是 0 的圆其实是一个点，求交没有意义", path + ".radius",
             "改用 point 类型，或者把半径改成正数")
    return {"center": center, "radius": radius}


def _polyline(data, path):
    if isinstance(data, (list, tuple)):
        data = {"points": data}
    if not isinstance(data, dict):
        _bad("bad_data", "折线的数据要写成 {points: [[x, y], ...]}：%r" % (data,),
             path, "例如 {\"points\": [[0, 0], [1, 1]]}")
    points = data.get("points")
    if points is None:
        _bad("missing_field", "折线缺少 points", path + ".points",
             "补上一串点，例如 [[0, 0], [1, 1]]")
    result = {"points": _segment_points(points, path + ".points"),
              "closed": bool(data.get("closed"))}
    if isinstance(data.get("curve"), dict):
        result["curve"] = data["curve"]
    return result


def _conic_coefficients(data, path):
    """圆锥曲线统一成 Ax²+Bxy+Cy²+Dx+Ey+F=0 的系数。"""
    if not isinstance(data, dict):
        _bad("bad_data", "圆锥曲线的数据要是字典：%r" % (data,), path,
             "例如 {\"kind\": \"ellipse\", \"center\": [0, 0], \"rx\": 2, \"ry\": 1}")
    if all(key in data for key in "abcdef"):
        values = [_number(data[key], "%s.%s" % (path, key), "系数 " + key)
                  for key in "abcdef"]
        if values[0] == values[1] == values[2] == 0:
            _bad("degenerate_conic", "A、B、C 全是 0，这不是圆锥曲线", path,
                 "二次项至少要有一个非零")
        return values
    kind = str(data.get("kind") or "").lower()
    if kind in ("ellipse", "hyperbola"):
        center = _point_value(data.get("center"), path + ".center", "中心")
        rx = _number(data.get("rx", data.get("a")), path + ".rx", "半轴 rx")
        ry = _number(data.get("ry", data.get("b")), path + ".ry", "半轴 ry")
        if kind == "ellipse" and data.get("foci") and "rx" not in data \
                and "a" not in data:
            # 焦点 + 长半轴：b² = a² - c²
            first = _point_value(data["foci"][0], path + ".foci[0]", "焦点")
            second = _point_value(data["foci"][1], path + ".foci[1]", "焦点")
            center = [(first[0] + second[0]) / 2.0, (first[1] + second[1]) / 2.0]
            half_c = math.hypot(second[0] - first[0], second[1] - first[1]) / 2.0
            rx = _number(data.get("a"), path + ".a", "长半轴 a")
            if rx <= half_c:
                _bad("bad_conic", "长半轴 a 必须大于焦距 c（a=%g, c=%g）" % (rx, half_c),
                     path + ".a", "椭圆的 a 一定要比 c 大")
            ry = math.sqrt(rx * rx - half_c * half_c)
        if rx <= 0 or ry <= 0:
            _bad("bad_conic", "半轴要大于 0（rx=%g, ry=%g）" % (rx, ry), path + ".rx",
                 "改成正数")
        h, k = center
        # 椭圆：b²(x-h)² + a²(y-k)² = a²b²；双曲线：b²(x-h)² - a²(y-k)² = a²b²
        sign = 1.0 if kind == "ellipse" else -1.0
        return [ry * ry, 0.0, sign * rx * rx,
                -2.0 * ry * ry * h, -2.0 * sign * rx * rx * k,
                sign * rx * rx * k * k + ry * ry * h * h - rx * rx * ry * ry]
    if kind == "parabola":
        vertex = _point_value(data.get("vertex", data.get("center")),
                              path + ".vertex", "顶点")
        p = _number(data.get("p"), path + ".p", "焦准距 p")
        if p == 0:
            _bad("bad_conic", "焦准距 p 不能是 0", path + ".p", "改成非零的数")
        h, k = vertex
        if str(data.get("axis") or "y").lower().startswith("x"):
            # (y-k)² = 2p(x-h)
            return [0.0, 0.0, 1.0, -2.0 * p, -2.0 * k, k * k + 2.0 * p * h]
        # (x-h)² = 2p(y-k)
        return [1.0, 0.0, 0.0, -2.0 * h, -2.0 * p, h * h + 2.0 * p * k]
    _bad("unknown_conic", "不认识的圆锥曲线类型：%r" % (data.get("kind"),),
         path + ".kind", "支持 ellipse / hyperbola / parabola，或者直接给 a~f 六个系数")


def _window_from(data, path):
    """取景范围：曲线在哪儿找交点。给不出就用默认的 [-10,10]²。"""
    if all(key in data for key in ("xmin", "xmax", "ymin", "ymax")):
        keys = ("xmin", "xmax", "ymin", "ymax")
    elif all(key in data for key in ("x_min", "x_max", "y_min", "y_max")):
        keys = ("x_min", "x_max", "y_min", "y_max")
    else:
        return list(DEFAULT_WINDOW)
    values = [_number(data[key], "%s.%s" % (path, key), key) for key in keys]
    if values[1] <= values[0] or values[3] <= values[2]:
        _bad("bad_window", "取景范围要是 xmin<xmax 且 ymin<ymax：%r" % (values,), path,
             "例如 [-10, 10, -10, 10]")
    return values


def _sampled_bounds(value):
    """从对象**已经算好的** ``value`` 里量出折线范围：``[xmin, xmax, ymin, ymax]``。

    为什么需要它：自动区间的曲线（没写 xmin/xmax）在屏幕上画到视野外好几屏，
    求交却按默认的 ``[-10,10]²`` 找 —— 于是 x = ±17 这种交点会"找不到"。
    用模型自己那份采样当窗口，求交的范围就和老师看到的那条线严格一致。
    """
    if not isinstance(value, dict):
        return None
    groups = value.get("segments")
    if not isinstance(groups, list):
        single = value.get("points")
        groups = [single] if isinstance(single, list) else []
    low_x = high_x = low_y = high_y = None
    for group in groups:
        if not isinstance(group, (list, tuple)):
            continue
        for item in group:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                x, y = item[0], item[1]
            elif isinstance(item, dict):
                x, y = item.get("x"), item.get("y")
            else:
                continue
            x, y = _numeric(x), _numeric(y)
            if x is None or y is None:
                continue
            low_x = x if low_x is None else min(low_x, x)
            high_x = x if high_x is None else max(high_x, x)
            low_y = y if low_y is None else min(low_y, y)
            high_y = y if high_y is None else max(high_y, y)
    if low_x is None or high_x is None or high_x <= low_x:
        return None
    if low_y is None or high_y is None or high_y <= low_y:
        low_y, high_y = DEFAULT_WINDOW[2], DEFAULT_WINDOW[3]
    return [low_x, high_x, low_y, high_y]


def _implicit(data, path):
    """隐式曲线：拿一个 F(x,y)。优先用现成的 f，否则用 plot 的解析器编译表达式。"""
    if not isinstance(data, dict):
        _bad("bad_data", "隐式曲线要写成 {expr: \"x^2+y^2-1\"}：%r" % (data,), path,
             "例如 {\"expr\": \"x^2+y^2-1\"}")
    function = data.get("f")
    if callable(function):
        text = str(data.get("expr") or "")
    else:
        text = str(data.get("expr") or "").strip()
        if not text:
            _bad("missing_field", "隐式曲线缺少 expr", path + ".expr",
                 "给一个式子，例如 x^2+y^2-1（等于 0）")
        if _plot is None:
            _bad("no_parser", "没有解析器（plot 模块不可用），算不了隐式曲线", path,
                 "检查部署包里 webui/plot.py 在不在")
        if "=" in text:
            # x^2+y^2=4 这种写法：搬成 左边-右边 = 0
            left, right = text.split("=", 1)
            text = "(%s)-(%s)" % (left, right)
        function = _compile(text, path + ".expr", "隐式曲线")
    return {"expr": str(data.get("expr") or ""), "f": function,
            "window": _window_from(data, path),
            "samples": int(data.get("samples") or DEFAULT_FIELD_GRID)}


def _normalize(kind, data, path):
    if kind == "point":
        point = _point_value(data, path, "点")
        return {"x": point[0], "y": point[1]}
    if kind in ("line", "segment", "ray"):
        return _line_like(data, kind, path)
    if kind == "circle":
        return _circle(data, path)
    if kind == "conic":
        return {"coeff": _conic_coefficients(data, path)}
    if kind == "polyline":
        return _polyline(data, path)
    return _implicit(data, path)


# ---------------------------------------------------------------------------
# 3. 直线型 × 直线型（直线/线段/射线共用；精确与浮点走同一个内核）
# ---------------------------------------------------------------------------

def _range_of(kind):
    """对象在自己的参数刻度上的范围。射线从 a 出发指向 b，所以是 t ≥ 0。"""
    if kind == "segment":
        return (0, 1)
    if kind == "ray":
        return (0, _UNBOUNDED)
    return (_UNBOUNDED, _UNBOUNDED)


def _map_range(kind, t_start, t_end):
    """把另一个对象的范围映射到本对象的参数刻度上（两边共线时才有意义）。"""
    if kind == "segment":
        return (min(t_start, t_end), max(t_start, t_end))
    if kind == "ray":
        return (t_start, _UNBOUNDED) if t_end >= t_start else (_UNBOUNDED, t_start)
    return (_UNBOUNDED, _UNBOUNDED)


def _max_low(first, second):
    if first is _UNBOUNDED:
        return second
    if second is _UNBOUNDED:
        return first
    return first if first > second else second


def _min_high(first, second):
    if first is _UNBOUNDED:
        return second
    if second is _UNBOUNDED:
        return first
    return first if first < second else second


def _in_range(kind, t, slack):
    if kind == "line":
        return True
    if kind == "segment":
        return -slack <= t <= 1 + slack
    return t >= -slack


def _line_pair_raw(ax, ay, bx, by, cx, cy, dx, dy, kind_a, kind_b,
                   tol_angle, tol_length):
    """直线型求交的**唯一**内核。返回：

        ("none",)
        ("point", t)               交点在 A 上的参数（坐标 = a + t·(b-a)）
        ("overlap", t_lo, t_hi)    共线重叠区间（None 表示无穷那一头）

    为什么一个函数同时管精确和浮点：传 Fraction + tol=0 就是精确算术
    （"平行"= 叉积严格为 0），传 float + 容差就是浮点近似。两条路走同一份代码，
    不会出现"精确路径改好了、浮点路径还是老 bug"。
    """
    rx = bx - ax
    ry = by - ay
    sx = dx - cx
    sy = dy - cy
    denom = rx * sy - ry * sx
    if tol_angle > 0:
        parallel = abs(denom) <= tol_angle * math.hypot(rx, ry) * math.hypot(sx, sy)
    else:
        parallel = (denom == 0)
    if parallel:
        # 共线吗？点到直线的距离 = |(C-A)×r| / |r|
        side = (cx - ax) * ry - (cy - ay) * rx
        if tol_length > 0:
            collinear = abs(side) <= tol_length * math.hypot(rx, ry)
        else:
            collinear = (side == 0)
        if not collinear:
            return ("none",)
        # 共线：把 B 的两个端点投影到 A 的参数刻度，化成两个区间求交
        square = rx * rx + ry * ry
        t_start = ((cx - ax) * rx + (cy - ay) * ry) / square
        t_end = ((dx - ax) * rx + (dy - ay) * ry) / square
        low = _max_low(_range_of(kind_a)[0], _map_range(kind_b, t_start, t_end)[0])
        high = _min_high(_range_of(kind_a)[1], _map_range(kind_b, t_start, t_end)[1])
        slack = 0 if tol_length == 0 else tol_length / math.sqrt(square)
        if low is not _UNBOUNDED and high is not _UNBOUNDED:
            if low > high + slack:
                return ("none",)
            if high - low <= 2 * slack:
                return ("point", (low + high) / 2)     # 只在一点碰上
        return ("overlap", low, high)
    t = ((cx - ax) * sy - (cy - ay) * sx) / denom
    u = ((cx - ax) * ry - (cy - ay) * rx) / denom
    if tol_length > 0:
        slack_a = tol_length / math.hypot(rx, ry)
        slack_b = tol_length / math.hypot(sx, sy)
    else:
        slack_a = slack_b = 0
    if not _in_range(kind_a, t, slack_a) or not _in_range(kind_b, u, slack_b):
        return ("none",)
    return ("point", t)


def _line_pair(a, kind_a, b, kind_b, tol, exact=True):
    """两个直线型对象求交 → (points, exact, tangent, overlap)。

    ``exact=False`` 时强制走浮点：折线上几千段每段都去试精确转换毫无意义
    （而且这里的结果只能精确到弦，声称"精确"反而误导）。
    """
    values = (a["a"][0], a["a"][1], a["b"][0], a["b"][1],
              b["a"][0], b["a"][1], b["b"][0], b["b"][1])
    fractions = _all_rational(values) if (exact and _calc is not None) else None
    if fractions is not None:
        ax, ay, bx, by, cx, cy, dx, dy = fractions
        raw = _line_pair_raw(ax, ay, bx, by, cx, cy, dx, dy, kind_a, kind_b, 0, 0)
        if raw[0] == "none":
            return [], None, False, False
        if raw[0] == "point":
            first = ax + raw[1] * (bx - ax)
            second = ay + raw[1] * (by - ay)
            return ([[float(first), float(second)]],
                    [_exact_pair(_exact_number(first), _exact_number(second))],
                    False, False)
        points = []
        exact_out = []
        for t in (raw[1], raw[2]):
            if t is _UNBOUNDED:
                continue
            first = ax + t * (bx - ax)
            second = ay + t * (by - ay)
            points.append([float(first), float(second)])
            exact_out.append(_exact_pair(_exact_number(first), _exact_number(second)))
        return points, (exact_out or None), False, True
    ax, ay, bx, by, cx, cy, dx, dy = values
    tol_length = tol * _magnitude(*values)
    raw = _line_pair_raw(ax, ay, bx, by, cx, cy, dx, dy, kind_a, kind_b,
                         TOL_ANGLE, tol_length)
    if raw[0] == "none":
        return [], None, False, False
    if raw[0] == "point":
        return ([[ax + raw[1] * (bx - ax), ay + raw[1] * (by - ay)]],
                None, False, False)
    points = []
    for t in (raw[1], raw[2]):
        if t is _UNBOUNDED:
            continue
        points.append([ax + t * (bx - ax), ay + t * (by - ay)])
    return points, None, False, True


def _overlap_endpoints(a, kind_a, b, kind_b, tol):
    """共线重叠段的两个端点（用于把"重合"如实报出来）。"""
    values = (a["a"][0], a["a"][1], a["b"][0], a["b"][1],
              b["a"][0], b["a"][1], b["b"][0], b["b"][1])
    tol_length = tol * _magnitude(*values)
    raw = _line_pair_raw(values[0], values[1], values[2], values[3],
                         values[4], values[5], values[6], values[7],
                         kind_a, kind_b, TOL_ANGLE, tol_length)
    if raw[0] != "overlap":
        return []
    ax, ay, bx, by = values[0], values[1], values[2], values[3]
    rx, ry = bx - ax, by - ay
    points = []
    for t in (raw[1], raw[2]):
        if t is _UNBOUNDED:
            continue
        points.append([ax + t * rx, ay + t * ry])
    return points


# ---------------------------------------------------------------------------
# 4. 直线型 × 圆（精确）/ 圆 × 圆（精确）/ 直线型 × 圆锥曲线（精确）
# ---------------------------------------------------------------------------

def _line_circle(line, kind, circle, tol, exact=True):
    """直线型 × 圆。代入参数式得二次方程，判别式为 0 就是相切。"""
    ax, ay = line["a"]
    bx, by = line["b"]
    cx, cy = circle["center"]
    radius = circle["radius"]
    rx, ry = bx - ax, by - ay
    if exact and _calc is not None:
        numbers = _all_rational((ax, ay, rx, ry, cx, cy, radius))
        if numbers is not None:
            fx, fy, frx, fry, fcx, fcy, fradius = numbers
            fa = frx * frx + fry * fry
            fb = 2 * (frx * (fx - fcx) + fry * (fy - fcy))
            fc = (fx - fcx) ** 2 + (fy - fcy) ** 2 - fradius * fradius
            tangent, roots = _quadratic_roots_exact(fa, fb, fc)
            points = []
            exact_out = []
            for t in roots:
                # 注意：calc.Exact 没有定义比较运算符，范围判断要先用浮点值
                if not _in_range(kind, t.to_float(), 0):
                    continue
                px = _exact_number(fx) + t * _exact_number(frx)
                py = _exact_number(fy) + t * _exact_number(fry)
                points.append([px.to_float(), py.to_float()])
                exact_out.append(_exact_pair(px, py))
            return points, (exact_out or None), bool(tangent and len(points) == 1), False
    a = rx * rx + ry * ry
    b = 2.0 * (rx * (ax - cx) + ry * (ay - cy))
    c = (ax - cx) ** 2 + (ay - cy) ** 2 - radius * radius
    tangent, roots = _quadratic_roots(a, b, c)
    slack = 0.0 if a <= 0 else tol * _magnitude(ax, ay, bx, by) / math.sqrt(a)
    points = []
    for t in roots:
        if not _in_range(kind, t, slack):
            continue
        points.append([ax + t * rx, ay + t * ry])
    return points, None, bool(tangent and len(points) == 1), False


def _circle_circle(first, second, tol):
    """圆 × 圆。先比 d² 与 (r1±r2)² 判位置关系，再用标准闭式解。"""
    c1x, c1y = first["center"]
    c2x, c2y = second["center"]
    r1 = first["radius"]
    r2 = second["radius"]
    if _calc is not None:
        numbers = _all_rational((c1x, c1y, c2x, c2y, r1, r2))
        if numbers is not None:
            fx1, fy1, fx2, fy2, fr1, fr2 = numbers
            fdx, fdy = fx2 - fx1, fy2 - fy1
            fd2 = fdx * fdx + fdy * fdy
            if fd2 == 0:
                # 同心：半径相同就是同一个圆（无穷多交点），否则永不相交
                return [], None, False, (fr1 == fr2)
            if fd2 > (fr1 + fr2) ** 2 or fd2 < (fr1 - fr2) ** 2:
                return [], None, False, False
            tangent = (fd2 == (fr1 + fr2) ** 2 or fd2 == (fr1 - fr2) ** 2)
            # P = C1 + (H/d²)(C2-C1) ± (√(r1²d²-H²)/d²)·perp(C2-C1)，全是精确运算
            height = (fd2 + fr1 * fr1 - fr2 * fr2) / 2
            chord2 = fr1 * fr1 * fd2 - height * height
            if chord2 < 0:
                return [], None, False, False
            base = _exact_number(height) / _exact_number(fd2)
            if chord2 == 0:
                px = _exact_number(fx1) + base * _exact_number(fdx)
                py = _exact_number(fy1) + base * _exact_number(fdy)
                return ([[px.to_float(), py.to_float()]], [_exact_pair(px, py)],
                        True, False)
            chord = _calc.Exact.rational(chord2).sqrt() / _exact_number(fd2)
            points = []
            exact_out = []
            for sign in (1, -1):
                # 注意：calc.Exact 没有 __rmul__，写 sign * chord 会 TypeError，
                # 所以先把符号并进 Exact 再乘
                offset = chord if sign > 0 else -chord
                px = _exact_number(fx1) + base * _exact_number(fdx) \
                    - offset * _exact_number(fdy)
                py = _exact_number(fy1) + base * _exact_number(fdy) \
                    + offset * _exact_number(fdx)
                points.append([px.to_float(), py.to_float()])
                exact_out.append(_exact_pair(px, py))
            return points, exact_out, tangent, False
    dx, dy = c2x - c1x, c2y - c1y
    d2 = dx * dx + dy * dy
    if d2 == 0:
        return [], None, False, (r1 == r2)
    distance = math.sqrt(d2)
    scale = tol * _magnitude(c1x, c1y, c2x, c2y, r1, r2)
    if distance > r1 + r2 + scale or distance < abs(r1 - r2) - scale:
        return [], None, False, False
    tangent = (abs(distance - (r1 + r2)) <= scale
               or abs(distance - abs(r1 - r2)) <= scale)
    height = (d2 + r1 * r1 - r2 * r2) / (2.0 * d2)
    chord2 = r1 * r1 / d2 - height * height
    if chord2 <= 0:
        return ([[c1x + height * dx, c1y + height * dy]], None, True, False)
    chord = math.sqrt(chord2)
    return ([[c1x + height * dx - chord * dy, c1y + height * dy + chord * dx],
             [c1x + height * dx + chord * dy, c1y + height * dy - chord * dx]],
            None, tangent, False)


def _line_conic(line, kind, conic, tol, exact=True):
    """直线型 × 圆锥曲线：代入得二次方程，精确路径同直线×圆。"""
    ax, ay = line["a"]
    bx, by = line["b"]
    ca, cb, cc, cd, ce, cf = conic["coeff"]
    rx, ry = bx - ax, by - ay
    if exact and _calc is not None:
        numbers = _all_rational((ax, ay, rx, ry) + tuple(conic["coeff"]))
        if numbers is not None:
            fx, fy, frx, fry = numbers[0], numbers[1], numbers[2], numbers[3]
            fa, fb, fc, fd, fe, ff = numbers[4:]
            alpha = fa * frx * frx + fb * frx * fry + fc * fry * fry
            beta = (2 * fa * fx * frx + fb * (fx * fry + fy * frx)
                    + 2 * fc * fy * fry + fd * frx + fe * fry)
            gamma = (fa * fx * fx + fb * fx * fy + fc * fy * fy
                     + fd * fx + fe * fy + ff)
            tangent, roots = _quadratic_roots_exact(alpha, beta, gamma)
            points = []
            exact_out = []
            for t in roots:
                if not _in_range(kind, t.to_float(), 0):    # Exact 不能直接比大小
                    continue
                px = _exact_number(fx) + t * _exact_number(frx)
                py = _exact_number(fy) + t * _exact_number(fry)
                points.append([px.to_float(), py.to_float()])
                exact_out.append(_exact_pair(px, py))
            return points, (exact_out or None), bool(tangent and len(points) == 1), False
    alpha = ca * rx * rx + cb * rx * ry + cc * ry * ry
    beta = (2.0 * ca * ax * rx + cb * (ax * ry + ay * rx)
            + 2.0 * cc * ay * ry + cd * rx + ce * ry)
    gamma = (ca * ax * ax + cb * ax * ay + cc * ay * ay
             + cd * ax + ce * ay + cf)
    tangent, roots = _quadratic_roots(alpha, beta, gamma)
    length = math.hypot(rx, ry)
    slack = 0.0 if length == 0 else tol * _magnitude(ax, ay, bx, by) / length
    points = []
    for t in roots:
        if not _in_range(kind, t, slack):
            continue
        points.append([ax + t * rx, ay + t * ry])
    return points, None, bool(tangent and len(points) == 1), False


# ---------------------------------------------------------------------------
# 5. 采样：把曲线变成折线（带参数刻度，细化时要用）
# ---------------------------------------------------------------------------

class _Piece(object):
    """一条采样折线 + 它的参数刻度。

    ``params`` 与 ``points`` 一一对应：有了它，交点就能用二分推回**真曲线**，
    而不是停在弦上（弦的误差是 O(h²)，2000 点也才 1e-4 量级）。
    ``closed`` 为真时最后一点回到第一点也算一条边 —— 多边形的边一条都不能漏
    （三角形漏掉一条边，"边与直线求交"就少一个交点，老师一眼就看出来）。
    """

    __slots__ = ("points", "params", "point_of", "field", "curve", "closed")

    def __init__(self, points, params=None, point_of=None, field=None, curve=None,
                 closed=False):
        self.points = points
        self.params = params
        self.point_of = point_of      # t -> (x, y)
        self.field = field            # (x, y) -> F
        self.curve = curve            # 生成这条折线的描述（JSON 安全），可为 None
        self.closed = closed


def _piece_segments(piece):
    """折线的所有线段 (index, x1, y1, x2, y2)；闭合折线补上收尾那条边。

    收尾那条边的 ``index`` 是 ``len(points) - 1``（一个"虚拟段"），
    参数区间由 ``_hit_bracket`` 还原（跨过头尾）。
    """
    points = piece.points
    last = len(points) - 1
    for index in range(last):
        yield (index, points[index][0], points[index][1],
               points[index + 1][0], points[index + 1][1])
    if piece.closed and last >= 2:
        yield (last, points[last][0], points[last][1], points[0][0], points[0][1])


def _hit_bracket(piece, index):
    """某一段折线对应的参数区间（闭合折线的收尾段要跨过头尾）。"""
    params = piece.params
    step = params[1] - params[0] if len(params) > 1 else 1.0
    if index < len(params) - 1:
        return params[index], params[index + 1]
    return params[-1], params[0] + step


def _compile(text, path, what):
    if _plot is None:
        _bad("no_parser", "没有解析器（plot 模块不可用），算不了%s" % what, path,
             "检查部署包里 webui/plot.py 在不在")
    try:
        return _plot.compile_expression(text)
    except Exception as error:                       # noqa: BLE001
        _bad("bad_expression", "%s 看不懂：%s（%s）" % (what, text, error), path,
             "检查括号和写法")


def _numeric(value):
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or abs(value) > MAX_COORD:
        return None
    return value


def _curve_pieces(curve, path, samples=None):
    """按 ``curve`` 描述采样出折线。返回 _Piece 列表（点数不足 2 的段丢掉）。"""
    kind = str(curve.get("type") or "").lower()
    count = int(samples or curve.get("count") or DEFAULT_CURVE_SAMPLES)
    count = max(2, min(MAX_CURVE_SAMPLES, count))
    if kind == "function":
        text = str(curve.get("expr") or "").strip()
        if not text:
            _bad("missing_field", "函数曲线缺少 expr", path + ".expr",
                 "例如 {\"type\": \"function\", \"expr\": \"x^2\"}")
        function = _compile(text, path + ".expr", "函数")
        t_min = _number(curve.get("tmin", DEFAULT_WINDOW[0]), path + ".tmin", "起止")
        t_max = _number(curve.get("tmax", DEFAULT_WINDOW[1]), path + ".tmax", "起止")

        def point_of(t, function=function):
            return (t, function(t))
    elif kind == "parametric":
        x_of = _compile(str(curve.get("x") or ""), path + ".x", "参数方程 x(t)")
        y_of = _compile(str(curve.get("y") or ""), path + ".y", "参数方程 y(t)")
        t_min = _number(curve.get("tmin", 0.0), path + ".tmin", "起止")
        t_max = _number(curve.get("tmax", 2.0 * math.pi), path + ".tmax", "起止")

        def point_of(t, x_of=x_of, y_of=y_of):
            return (x_of(t), y_of(t))
    elif kind == "polar":
        text = str(curve.get("expr") or "").strip()
        if not text:
            _bad("missing_field", "极坐标曲线缺少 expr", path + ".expr",
                 "例如 {\"type\": \"polar\", \"expr\": \"1+cos(theta)\"}")
        radius_of = _compile(text, path + ".expr", "极坐标方程")
        t_min = _number(curve.get("tmin", 0.0), path + ".tmin", "起止")
        t_max = _number(curve.get("tmax", 2.0 * math.pi), path + ".tmax", "起止")

        def point_of(t, radius_of=radius_of):
            radius = radius_of(t)
            return (radius * math.cos(t), radius * math.sin(t))
    else:
        _bad("unknown_curve", "不认识的曲线描述：%r" % (curve.get("type"),),
             path + ".type", "支持 function / parametric / polar")
    if t_max == t_min:
        _bad("bad_window", "采样区间是空的（tmin = tmax）", path + ".tmin",
             "让两端不一样")
    spans = []
    for index in range(count):
        t = t_min + (t_max - t_min) * index / (count - 1.0)
        try:
            x, y = point_of(t)
        except Exception:                            # noqa: BLE001  定义域外就断开
            spans.append((t, None))
            continue
        x = _numeric(x)
        y = _numeric(y)
        spans.append((t, None if x is None or y is None else (x, y)))
    pieces = []
    params = []
    points = []
    for t, value in spans:
        if value is None:
            if len(points) > 1:
                pieces.append(_Piece(points, params, point_of, None, curve))
            points, params = [], []
            continue
        points.append([value[0], value[1]])
        params.append(t)
    if len(points) > 1:
        pieces.append(_Piece(points, params, point_of, None, curve))
    return pieces


def _marching_squares():
    """复用 plot 的等值线实现（同一个算法不要在两个文件里各写一遍）。"""
    marching = getattr(_plot, "_marching_squares", None) if _plot is not None else None
    chain = getattr(_plot, "_chain_segments", None) if _plot is not None else None
    if marching is None or chain is None:
        _bad("no_sampler", "没有采样器（plot 里没有 marching squares）", "",
             "检查部署包里 webui/plot.py 是不是被改过")
    return marching, chain


def _plane_math_lines(raw):
    """把 plane_math 的采样结果认成一串折线；认不出来返回 None（交给兜底）。"""
    if isinstance(raw, dict):
        if raw.get("ok") is False:
            return None
        for key in ("segments", "lines", "points"):
            if isinstance(raw.get(key), (list, tuple)):
                raw = raw[key]
                break
        else:
            return None
    if not isinstance(raw, (list, tuple)) or not raw:
        return None
    pieces = []
    for line in raw:
        if isinstance(line, dict):
            line = line.get("points")
        if not isinstance(line, (list, tuple)) or len(line) < 2:
            return None
        points = []
        for item in line:
            if not isinstance(item, (list, tuple)) or len(item) != 2:
                return None
            x, y = _numeric(item[0]), _numeric(item[1])
            if x is None or y is None:
                return None
            points.append([x, y])
        pieces.append(points)
        if len(pieces) >= MAX_PIECES:
            break
    return pieces or None


def _plane_math_pieces(expr, field, window, count):
    """问 ``plane_math`` 要采样（架构文档里它才是采样层）。拿不到就返回空表。

    两种签名都试：本项目 plane_math 的 ``(expr, bounds=, grid=)``，以及更一般的
    ``(expr, window=, samples=, field=)``；记不清对方写成哪种形式，
    认不出来就退回 plot 的 marching squares，绝不让求交整条路废掉。
    用 sys.modules 查是为了能测：自检里塞个假模块就能验证这条委派真的通。
    """
    module = sys.modules.get("plane_math")
    if module is None or not expr:
        return []
    sampler = getattr(module, "sample_implicit", None)
    if not callable(sampler):
        return []
    bounds = (window[0], window[1], window[2], window[3])
    for kwargs in ({"bounds": bounds, "grid": count},
                   {"window": bounds, "samples": count, "field": field}):
        try:
            raw = sampler(expr, **kwargs)
        except Exception:                            # noqa: BLE001  对方签名不稳就算了
            continue
        lines = _plane_math_lines(raw)
        if lines:
            # 带上 curve 描述：这**不是**精确顶点，而是采样折线，
            # 别让 _scan_pieces_against 把它当"本来就有理"的数据去声称精确
            marker = {"type": "implicit", "expr": expr}
            return [_Piece(points, None, None, field, marker) for points in lines]
    return []


def _field_pieces(field, window, samples, curve=None):
    """隐式曲线 F(x,y)=0 采样成折线（marching squares）。"""
    x_min, x_max, y_min, y_max = window
    count = max(8, min(MAX_FIELD_GRID, int(samples or DEFAULT_FIELD_GRID)))
    expr = None
    if isinstance(curve, dict) and str(curve.get("type")) == "implicit":
        expr = str(curve.get("expr") or "")
    pieces = _plane_math_pieces(expr, field, window, count)
    if pieces:
        return pieces
    marching, chain = _marching_squares()
    xs = [x_min + (x_max - x_min) * index / (count - 1.0) for index in range(count)]
    ys = [y_min + (y_max - y_min) * index / (count - 1.0) for index in range(count)]
    grid = [[_numeric(field(x, y)) for x in xs] for y in ys]
    segments = marching(grid, xs, ys)
    if not segments:
        return []
    step = max(x_max - x_min, y_max - y_min) / (count - 1.0)
    result = []
    for line in chain(segments, max(1e-9, step * 1e-3)):
        if len(line) < 2:
            continue
        result.append(_Piece([[float(point[0]), float(point[1])] for point in line],
                             None, None, field, curve))
        if len(result) >= MAX_PIECES:
            break
    return result


def _conic_field(coefficients):
    ca, cb, cc, cd, ce, cf = coefficients

    def field(x, y, ca=ca, cb=cb, cc=cc, cd=cd, ce=ce, cf=cf):
        return ca * x * x + cb * x * y + cc * y * y + cd * x + ce * y + cf

    return field


def _conic_window(coefficients):
    """圆锥曲线默认取景：椭圆按自己的外接矩形，其余用默认窗口。

    让老师在默认视图里就能看到交点，而不是"画出来了但交点在屏幕外"。
    """
    ca, cb, cc, cd, ce, cf = coefficients
    if cb == 0 and ca > 0 and cc > 0:
        h = -cd / (2.0 * ca)
        k = -ce / (2.0 * cc)
        value = ca * h * h + cc * k * k - cf
        if value > 0:
            rx = math.sqrt(value / ca)
            ry = math.sqrt(value / cc)
            return [h - 3.0 * rx, h + 3.0 * rx, k - 3.0 * ry, k + 3.0 * ry]
    return list(DEFAULT_WINDOW)


def _window_of_data(kind, data, options):
    if options.get("window"):
        return options["window"]
    if kind == "implicit":
        return data["window"]
    if kind == "conic":
        return _conic_window(data["coeff"])
    return list(DEFAULT_WINDOW)


def _piece_point_of(pieces):
    for piece in pieces:
        if piece.point_of is not None:
            return piece.point_of
    return None


def _params_of_curve(curve, points):
    """从 curve 描述 + 点数还原参数刻度；对不上就返回 None（那就不细化）。"""
    count = int(curve.get("count") or 0)
    t_min = curve.get("tmin")
    t_max = curve.get("tmax")
    if count < 2 or t_min is None or t_max is None:
        return None
    try:
        t_min = float(t_min)
        t_max = float(t_max)
    except (TypeError, ValueError):
        return None
    step = (t_max - t_min) / (count - 1.0)
    runs = curve.get("runs")
    if isinstance(runs, (list, tuple)) and runs:
        params = []
        for item in runs:
            if not isinstance(item, (list, tuple)) or len(item) != 2:
                return None
            first, last = int(item[0]), int(item[1])
            if last < first:
                return None
            for index in range(first, last + 1):
                params.append(t_min + step * index)
        return params if len(params) == len(points) else None
    if count == len(points):
        return [t_min + step * index for index in range(count)]
    return None


def _polyline_pieces(data, path):
    """折线对象的内部表示。带 ``curve`` 描述时重建参数刻度（细化要用）。"""
    points = data["points"]
    curve = data.get("curve")
    closed = bool(data.get("closed"))
    if isinstance(curve, dict):
        point_of = _piece_point_of(_curve_pieces(curve, path + ".curve"))
        return [_Piece(points, _params_of_curve(curve, points), point_of, None, curve,
                       closed)]
    return [_Piece(points, None, None, None, None, closed)]


def _pieces_of(kind, data, options, path="data"):
    """把"要采样的" kind 变成 _Piece 列表（解析对象不走这里）。"""
    if kind == "polyline":
        return _polyline_pieces(data, path)
    if kind == "conic":
        return _field_pieces(_conic_field(data["coeff"]),
                             _window_of_data("conic", data, options),
                             options["samples"],
                             {"type": "conic", "coeff": list(data["coeff"])})
    if kind == "implicit":
        return _field_pieces(data["f"], _window_of_data("implicit", data, options),
                             data.get("samples") or options["samples"],
                             {"type": "implicit", "expr": data.get("expr")})
    _bad("internal", "内部错误：%s 不该走到采样这一步" % kind, path, "报给开发者")


# ---------------------------------------------------------------------------
# 6. 折线 × 折线：x 排序扫描（不做 Bentley-Ottmann，够用而且好读）
# ---------------------------------------------------------------------------

class _Hit(object):
    """一个候选交点。``piece``/``index`` 指向产生它的那一段折线，细化要用。"""

    __slots__ = ("x", "y", "exact_x", "exact_y", "piece", "index", "side")

    def __init__(self, x, y, piece=None, index=None, side=0):
        self.x = x
        self.y = y
        self.exact_x = None
        self.exact_y = None
        self.piece = piece
        self.index = index
        self.side = side


def _segments_of(pieces):
    segments = []
    for piece in pieces:
        for index, x1, y1, x2, y2 in _piece_segments(piece):
            if x1 == x2 and y1 == y2:
                continue                             # 采样缝上的零长段：内部静默跳过
            segments.append((x1, y1, x2, y2, piece, index))
    return segments


def _coincident(first, other, tol):
    """两条线段（x1, y1, x2, y2）是不是"同一条线段"。

    判据：四个端点到对方**直线**的距离都 ≤ tol。
    只在调用方已经确认它们 x/y 包围盒都重叠的前提下用 —— 那时"同一条无限直线"
    就等于"同一条线段"（两个区间在两条轴上的投影都相交，必然相交）。
    为什么需要它：老师很可能把同一条曲线选中两次求交（或者比较"同一条线画两遍"），
    没有这一步就会返回几千个采样级的假交点；有了它，答案是"重合，端点在这两个"。
    容差取 tol（默认 1e-9），比采样步长小几个数量级，所以**不会**把近平行的相切
    误判成重合。
    """
    for first_pair, second_pair in ((first, other), (other, first)):
        px, py, qx, qy = first_pair[0], first_pair[1], first_pair[2], first_pair[3]
        rx, ry, sx, sy = second_pair[0], second_pair[1], second_pair[2], second_pair[3]
        length = math.hypot(sx - rx, sy - ry)
        if length == 0:
            continue
        if abs(_cross(rx, ry, sx, sy, px, py)) > tol * length:
            return False
        if abs(_cross(rx, ry, sx, sy, qx, qy)) > tol * length:
            return False
    return True


def _seg_seg(seg, other, tol):
    """线段 × 线段。返回 None / ("p", x, y) / ("o", x1, y1, x2, y2)。"""
    raw = _line_pair_raw(seg[0], seg[1], seg[2], seg[3],
                         other[0], other[1], other[2], other[3],
                         "segment", "segment", TOL_ANGLE, tol)
    if raw[0] == "none":
        return None
    if raw[0] == "point":
        t = raw[1]
        return ("p", seg[0] + t * (seg[2] - seg[0]), seg[1] + t * (seg[3] - seg[1]))
    values = []
    for t in (raw[1], raw[2]):
        if t is _UNBOUNDED:
            values.extend([None, None])
        else:
            values.extend([seg[0] + t * (seg[2] - seg[0]),
                           seg[1] + t * (seg[3] - seg[1])])
    return ("o", values[0], values[1], values[2], values[3])


def _sweep_hits(pieces_a, pieces_b, options):
    """按 x 排序扫描求交。返回 (hits, truncated)。

    复杂度 O(N log N + K)：N 是线段总数，K 是 **x 区间重叠**的线段对数。
    密采样曲线（每段很短）时 K ≈ O(N)，2000×2000 也就几千次测试；
    最坏情况用 MAX_PAIR_TESTS 封顶，到顶就停并置 truncated（绝不给教室机器埋雷）。
    """
    segments_a = _segments_of(pieces_a)
    segments_b = _segments_of(pieces_b)
    if not segments_a or not segments_b:
        return [], False
    events = []
    for index, seg in enumerate(segments_a):
        low, high = (seg[0], seg[2]) if seg[0] <= seg[2] else (seg[2], seg[0])
        events.append((low, 0, 0, index))
        events.append((high, 1, 0, index))
    for index, seg in enumerate(segments_b):
        low, high = (seg[0], seg[2]) if seg[0] <= seg[2] else (seg[2], seg[0])
        events.append((low, 0, 1, index))
        events.append((high, 1, 1, index))
    events.sort()                                    # 同一 x 上"进入"排在"离开"前面，
    active = ({}, {})                                # 这样端点相碰也算得到
    hits = []
    tested = 0
    truncated = False
    cap = options["max_pairs"]
    for _x, leaving, side, index in events:
        if leaving:
            active[side].pop(index, None)
            continue
        seg = segments_a[index] if side == 0 else segments_b[index]
        seg_low = seg[1] if seg[1] <= seg[3] else seg[3]
        seg_high = seg[3] if seg[1] <= seg[3] else seg[1]
        other_side = 1 - side
        others = segments_b if side == 0 else segments_a
        for other_index in list(active[other_side]):
            other = others[other_index]
            other_low = other[1] if other[1] <= other[3] else other[3]
            other_high = other[3] if other[1] <= other[3] else other[1]
            if seg_high < other_low or other_high < seg_low:
                continue                             # y 包围盒不重叠，省掉一次真求交
            tested += 1
            if tested > cap:
                truncated = True
                break
            if _coincident(seg[:4], other[:4], options["tol_length"]):
                hits.append(_Overlap((seg[0], seg[1], seg[2], seg[3])))
                continue
            hit = _seg_seg(seg, other, options["tol_length"])
            if hit is None:
                continue
            if hit[0] == "p":
                hits.append(_Hit(hit[1], hit[2], seg[4], seg[5], side))
            else:
                hits.append(_Overlap(hit[1:]))
        if truncated:
            break
        active[side][index] = True
    return hits, truncated


class _Overlap(object):
    """重合段的端点（某一头可能是 None = 无穷远）。"""

    __slots__ = ("values",)

    def __init__(self, values):
        self.values = values


def _merge_overlaps(overlaps, tol):
    """把重合段归并成极少数区间。返回 (端点列表, 归并后的区间列表)。

    两条曲线沿一段重合时，相邻线段对会给出很多首尾相叠的小区间（还有一堆
    "只在一个点碰上"的假交点）；原样全吐出去就是"一个重叠边返回 50 个点"。
    归并成极大区间后，一个重叠边只剩 2 个端点（或 1 个 / 0 个，看是不是无穷那头），
    再把落在区间里的那些点丢掉 —— 它们本来就是这段重合的一部分。
    """
    groups = []
    for values in overlaps:
        endpoints = []
        if values[0] is not None:
            endpoints.append((values[0], values[1]))
        if values[2] is not None:
            endpoints.append((values[2], values[3]))
        if not endpoints:
            continue
        merged = False
        for group in groups:
            if any(_near_point(group, point, tol) for point in endpoints):
                group.extend(endpoints)
                merged = True
                break
        if not merged:
            groups.append(list(endpoints))
    points = []
    spans = []
    for group in groups:
        low = group[0]
        high = group[0]
        for point in group:
            if point[0] < low[0]:
                low = point
            if point[0] > high[0]:
                high = point
        edge = [list(low)]
        if abs(high[0] - low[0]) > tol or abs(high[1] - low[1]) > tol:
            edge.append(list(high))
        points.extend(edge)
        spans.append(edge)
    return points, spans


def _on_any_span(point, spans, tol):
    """点是不是落在某段重合区间上（落上就说明它是重合的一部分，不必单独返回）。"""
    for edge in spans:
        if len(edge) < 2:
            continue                                 # 无穷那一头，判断不了就别筛
        distance, _t = _distance_to_line(point, {"a": edge[0], "b": edge[1]}, "segment")
        if distance <= tol:
            return True
    return False


def _near_point(points, point, tol):
    for other in points:
        if abs(other[0] - point[0]) <= tol and abs(other[1] - point[1]) <= tol:
            return True
    return False


# ---------------------------------------------------------------------------
# 7. 细化：把弦上的交点推回真曲线
# ---------------------------------------------------------------------------

def _minimum_abs(residual, low, high):
    """三分法找 |residual| 的最小值点（切点两端同号，二分用不上）。迭代次数封顶。"""
    left, right = low, high
    for _step in range(90):
        first = left + (right - left) / 3.0
        second = right - (right - left) / 3.0
        first_value = _value_of(residual, first)
        second_value = _value_of(residual, second)
        if first_value is None or second_value is None:
            return None
        if abs(first_value) <= abs(second_value):
            right = second
        else:
            left = first
        if right - left <= 1e-16 * max(1.0, abs(left)):
            break
    return 0.5 * (left + right)


def _zero_on_bracket(point_at, residual, low, high, anchor, move_limit):
    """在参数区间 [low, high] 上解 residual(t) = 0，返回细化后的 (x, y)。

    变号 → 二分（1e-15 级）；两端同号 → 三分法找 |residual| 的最小值，
    只有"真的贴上去了"（相对两端小 1e-6 以上）才认，这对应**相切**。
    没有这一步，相切处采样出来的两个假交点会各自留在原地（差 0.04 的两个点），
    老师看到的"圆和椭圆有两个交点"就是错的。
    细化结果离粗交点太远就丢弃：宁可保留采样值，也不能跳到另一条分支上。
    """
    root = None
    low_value = _value_of(residual, low)
    high_value = _value_of(residual, high)
    if low_value is not None and high_value is not None:
        if low_value != 0.0 and high_value != 0.0 \
                and (low_value < 0) != (high_value < 0):
            root = _bisect(lambda t: _value_of(residual, t), low, high, low_value)
        else:
            candidate = _minimum_abs(residual, low, high)
            if candidate is not None:
                value = _value_of(residual, candidate)
                scale = max(abs(low_value), abs(high_value))
                if value == 0.0 or (value is not None and scale > 0
                                    and abs(value) <= 1e-6 * scale):
                    root = candidate
    if root is None:
        return None
    try:
        x, y = point_at(root)
    except Exception:                                # noqa: BLE001
        return None
    if not (math.isfinite(x) and math.isfinite(y)):
        return None
    if anchor is not None and move_limit is not None \
            and math.hypot(x - anchor[0], y - anchor[1]) > move_limit:
        return None
    return (x, y)


def _signed_for(kind, data):
    """给定解析对象（直线型 / 圆），造一个"点在上面 = 0"的带符号函数。"""
    if kind == "circle":
        cx, cy = data["center"]
        radius = data["radius"]

        def signed(x, y, cx=cx, cy=cy, radius=radius):
            return (x - cx) ** 2 + (y - cy) ** 2 - radius * radius
        return signed
    ax, ay = data["a"]
    rx, ry = data["b"][0] - ax, data["b"][1] - ay

    def signed(x, y, ax=ax, ay=ay, rx=rx, ry=ry):
        return (x - ax) * ry - (y - ay) * rx
    return signed


def _parametrization(kind, data):
    """解析对象的参数化：(point_at, t_of, 长度→参数的换算系数)。"""
    if kind == "circle":
        cx, cy = data["center"]
        radius = data["radius"]

        def point_at(t, cx=cx, cy=cy, radius=radius):
            return (cx + radius * math.cos(t), cy + radius * math.sin(t))

        def t_of(point, cx=cx, cy=cy):
            return math.atan2(point[1] - cy, point[0] - cx)
        return point_at, t_of, 1.0 / radius
    ax, ay = data["a"]
    rx, ry = data["b"][0] - ax, data["b"][1] - ay
    square = rx * rx + ry * ry
    length = math.sqrt(square)

    def point_at(t, ax=ax, ay=ay, rx=rx, ry=ry):
        return (ax + t * rx, ay + t * ry)

    def t_of(point, ax=ax, ay=ay, rx=rx, ry=ry, square=square):
        return ((point[0] - ax) * rx + (point[1] - ay) * ry) / square
    return point_at, t_of, 1.0 / length


def _refine_on_curve(field, point_of, low, high, anchor, move_limit):
    """场曲线 × 参数曲线：沿参数区间解 F(P(t)) = 0。"""
    def residual(t, field=field, point_of=point_of):
        try:
            x, y = point_of(t)
        except Exception:                            # noqa: BLE001
            return None
        return field(x, y)
    return _zero_on_bracket(point_of, residual, low, high, anchor, move_limit)


def _refine_field_analytic(field, kind, data, anchor, grid_step, options):
    """场曲线 × 解析对象：在解析对象的参数上扩张找变号区间再二分。

    粗交点来自网格，F 在它附近不一定马上变号（可能只是擦过一个格子）；
    向两边扩张几次找到真正的变号点再二分，精度就从网格级（1e-1）变成 1e-12。
    """
    point_at, t_of, scale = _parametrization(kind, data)
    try:
        t_star = t_of(anchor)
    except Exception:                                # noqa: BLE001
        return None

    def residual(t, field=field, point_at=point_at):
        try:
            x, y = point_at(t)
        except Exception:                            # noqa: BLE001
            return None
        return field(x, y)
    span = max(grid_step * scale, 1e-12)
    for round_index in range(EXPAND_STEPS + 1):
        found = _zero_on_bracket(point_at, residual, t_star - span, t_star + span,
                                 anchor, 4.0 * span * (2 ** round_index) / scale
                                 + grid_step)
        if found is not None:
            return found
        span *= 2.0
    return None


def _refine_explicit(function_a, function_b, low, high, anchor):
    """两条显函数图像求交：解 f(x) - g(x) = 0。x 就是参数，直接二分。"""
    def residual(x, function_a=function_a, function_b=function_b):
        first = _value_of(function_a, x)
        second = _value_of(function_b, x)
        if first is None or second is None:
            return None
        return first - second

    def point_at(x, function_a=function_a):
        value = _value_of(function_a, x)
        return (x, value) if value is not None else None
    if _value_of(residual, low) is None or _value_of(residual, high) is None:
        return None
    return _zero_on_bracket(point_at, residual, low, high, anchor,
                            4.0 * abs(high - low) + 1e-9)


def _newton_pair(first, second, x, y, window):
    """两个隐式曲线求交的 2×2 牛顿迭代（数值雅可比，步长封顶）。"""
    x_min, x_max, y_min, y_max = window
    step = max(1e-7, (x_max - x_min) * 1e-7)
    limit = 0.5 * max(x_max - x_min, y_max - y_min)
    for _round in range(NEWTON_STEPS):
        first_value = _value_of(first, x, y)
        second_value = _value_of(second, x, y)
        if first_value is None or second_value is None:
            return None
        if abs(first_value) <= 1e-14 and abs(second_value) <= 1e-14:
            return (x, y)
        fx = _value_of(first, x + step, y)
        fy = _value_of(first, x, y + step)
        gx = _value_of(second, x + step, y)
        gy = _value_of(second, x, y + step)
        if None in (fx, fy, gx, gy):
            return None
        fx = (fx - first_value) / step
        fy = (fy - first_value) / step
        gx = (gx - second_value) / step
        gy = (gy - second_value) / step
        determinant = fx * gy - fy * gx
        if determinant == 0:
            return None
        delta_x = (-first_value * gy + fy * second_value) / determinant
        delta_y = (-fx * second_value + first_value * gx) / determinant
        length = math.hypot(delta_x, delta_y)
        if length > limit:
            delta_x *= limit / length
            delta_y *= limit / length
        x += delta_x
        y += delta_y
        if not (math.isfinite(x) and math.isfinite(y)):
            return None
        if abs(delta_x) <= 1e-15 and abs(delta_y) <= 1e-15:
            return (x, y)
    return (x, y) if abs(x) < MAX_COORD and abs(y) < MAX_COORD else None


# ---------------------------------------------------------------------------
# 8. 分派：把各种组合接到上面几块
# ---------------------------------------------------------------------------

_RANK = {"point": 0, "line": 1, "segment": 1, "ray": 1, "circle": 2,
         "conic": 3, "polyline": 4, "implicit": 5}
_LINE_LIKE = ("line", "segment", "ray")
_ANALYTIC = ("line", "segment", "ray", "circle")
_SAMPLED = ("conic", "polyline", "implicit")


def _options(raw):
    options = {"tol": DEFAULT_TOL, "merge_tol": None, "samples": DEFAULT_FIELD_GRID,
               "window": None, "refine": True, "max_pairs": MAX_PAIR_TESTS,
               "tol_length": DEFAULT_TOL}
    if not raw:
        return options
    if raw.get("tol") is not None:
        options["tol"] = _number(raw["tol"], "options.tol", "容差 tol")
    if raw.get("merge_tol") is not None:
        options["merge_tol"] = _number(raw["merge_tol"], "options.merge_tol", "去重容差")
    if raw.get("samples") is not None:
        options["samples"] = int(max(8, min(MAX_FIELD_GRID,
                                            _number(raw["samples"], "options.samples",
                                                    "采样数"))))
    if raw.get("window") is not None:
        values = raw["window"]
        if not isinstance(values, (list, tuple)) or len(values) != 4:
            _bad("bad_window", "options.window 要是 [xmin, xmax, ymin, ymax]",
                 "options.window", "例如 [-10, 10, -10, 10]")
        options["window"] = [_number(value, "options.window[%d]" % index, "取景")
                             for index, value in enumerate(values)]
        if options["window"][1] <= options["window"][0] \
                or options["window"][3] <= options["window"][2]:
            _bad("bad_window", "取景范围要是 xmin<xmax 且 ymin<ymax", "options.window",
                 "例如 [-10, 10, -10, 10]")
    if "refine" in raw:
        options["refine"] = bool(raw["refine"])
    if raw.get("max_pairs") is not None:
        options["max_pairs"] = int(max(1, min(MAX_PAIR_TESTS,
                                              _number(raw["max_pairs"], "options.max_pairs",
                                                      "上限"))))
    options["tol_length"] = options["tol"]
    return options


def _count_status(points, overlap):
    if overlap:
        return "infinite"
    if not points:
        return "none"
    if len(points) == 1:
        return "one"
    if len(points) == 2:
        return "two"
    return "many"


def _status_message(status, count, tangent, overlap, reason=""):
    if status == "infinite":
        base = "有无穷多交点（两个图形有重合的部分）"
    elif status == "none":
        base = "没有交点"
    elif status == "one":
        base = "只有 1 个交点" + ("（相切）" if tangent else "")
    elif status == "two":
        base = "有 2 个交点"
    else:
        base = "有 %d 个交点" % count
    return base + ("：" + reason if reason else "")


def _finalize(points, exact, tangent, overlap, truncated, warnings, options,
              reason="", extra=None):
    """排序 → 去重 → 定状态。

    精确形式和坐标**绑在一起**走排序（所以这里用 [x, y, exact] 三元组而不是
    两个平行列表）：平行列表排序后就对不上号了，"√2/2 配到了负的那个点"是真踩过的坑。
    """
    merge = options.get("merge_tol")
    if merge is None:
        merge = options.get("sampled_tol")
    if merge is None:
        merge = options["tol"]
    rows = [[point[0], point[1],
             exact[index] if (exact and index < len(exact)) else None]
            for index, point in enumerate(points)]
    rows.sort(key=lambda row: (row[0], row[1]))
    dropped = 0
    if len(rows) > MAX_RESULT_POINTS:
        dropped = len(rows) - MAX_RESULT_POINTS
        rows = rows[:MAX_RESULT_POINTS]
        truncated = True
    kept = _dedupe(rows, merge)
    exact_out = None
    if kept and all(row[2] is not None for row in kept):
        exact_out = [row[2] for row in kept]
    points_out = [[row[0], row[1]] for row in kept]
    status = _count_status(points_out, overlap)
    warnings = list(warnings)
    if truncated:
        warnings.append("交点太多或曲线太密，结果是部分的（线段对上限 %d、交点数上限 %d）"
                        % (options["max_pairs"], MAX_RESULT_POINTS))
    if dropped:
        warnings.append("还有 %d 个交点没有返回" % dropped)
    return _ok_result(status, points_out,
                      _status_message(status, len(points_out), tangent, overlap, reason),
                      exact_out, tangent, overlap, truncated, warnings, extra)


def _sampled_tolerance(pieces):
    """采样折线的去重容差：采样步长的 1/4。**只算采样出来的折线。**

    为什么不能对所有折线都用：多边形的边可以很长（比如 1.5），拿边长的 1/4 当合并半径
    会把同一条边与圆的两个真交点、甚至相邻两条边各自的交点合并掉
    （自检里就是这么少了一半交点的）。网格缝上的重复点只可能出现在**采样曲线**上
    （函数图像、隐式曲线、参数曲线），多边形顶点是精确数据，不会产生近邻重复。
    """
    biggest = 0.0
    for piece in pieces:
        if piece.curve is None and piece.field is None:
            continue                                 # 精确折线不参与
        points = piece.points
        for index in range(len(points) - 1):
            step = math.hypot(points[index + 1][0] - points[index][0],
                              points[index + 1][1] - points[index][1])
            if step > biggest:
                biggest = step
    return 0.25 * biggest if biggest > 0 else None


def _intersect_point(point, kind, data, options):
    """点 × 对象：就是"点在不在上面"；在上面就把这个点当交点返回。"""
    check = point_on(kind, data, [point["x"], point["y"]], options["tol"])
    if not check["ok"]:
        error = check["errors"][0]
        _bad(error["code"], error["message"], error["path"], error["hint"])
    points = [[point["x"], point["y"]]] if check["on"] else []
    result = _finalize(points, None, False, False, False, [], options)
    result["on"] = bool(check["on"])
    return result


def _scan_pieces_against(pieces, other_kind, other_data, options):
    """折线（或采样曲线）的每一段 × 解析对象。返回 (hits, overlaps, truncated)。"""
    hits = []
    overlaps = []
    truncated = False
    tests = 0
    circle = (other_kind == "circle")
    rational_other = _all_rational((other_data["center"][0], other_data["center"][1],
                                    other_data["radius"])) if circle else \
        _all_rational((other_data["a"][0], other_data["a"][1],
                       other_data["b"][0], other_data["b"][1]))
    for piece in pieces:
        rational_piece = piece.curve is None and _all_rational(
            [value for point in piece.points for value in point]) is not None
        # 只在"整条折线本来就是精确数据"时才走精确路径：采样出来的点声称精确
        # 等于把弦的误差说成真值，那是骗人
        exact = rational_piece and rational_other is not None and options["refine"]
        for index, x1, y1, x2, y2 in _piece_segments(piece):
            segment = {"a": [x1, y1], "b": [x2, y2]}
            tests += 1
            if tests > options["max_pairs"]:
                truncated = True
                break
            if circle:
                found, found_exact, _tangent, overlap = _line_circle(
                    segment, "segment", other_data, options["tol"], exact)
            else:
                found, found_exact, _tangent, overlap = _line_pair(
                    segment, "segment", other_data, other_kind, options["tol"], exact)
            if overlap:
                endpoints = _overlap_endpoints(segment, "segment", other_data,
                                               other_kind, options["tol"])
                if len(endpoints) < 2:
                    endpoints = [segment["a"], segment["b"]]
                overlaps.append((endpoints[0][0], endpoints[0][1],
                                 endpoints[1][0], endpoints[1][1]))
                continue
            for position, point in enumerate(found):
                hit = _Hit(point[0], point[1], piece, index)
                if found_exact:
                    hit.exact_x, hit.exact_y = found_exact[position]
                hits.append(hit)
        if truncated:
            break
    return hits, overlaps, truncated


def _finish_pieces_against(hits, overlaps, other_kind, other_data, options,
                           truncated, reason=""):
    """细化 + 去重 + 排序，出最终信封。"""
    warnings = []
    signed = _signed_for(other_kind, other_data)
    points = []
    exact = []
    budget = MAX_REFINE
    for hit in hits:
        x, y = hit.x, hit.y
        piece = hit.piece
        if options["refine"] and budget > 0 and piece.point_of is not None \
                and piece.params is not None and hit.index is not None:
            budget -= 1
            low, high = _hit_bracket(piece, hit.index)
            if piece.field is not None:
                # 场曲线（隐式/圆锥）采样出来的折线：沿自己的参数解 F(P(t)) = 0
                root = _refine_on_curve(piece.field, piece.point_of, low, high,
                                        (x, y), 4.0 * abs(high - low) + 1e-9)
            else:
                # 参数曲线 × 解析对象：解"点在解析对象上"的带符号函数
                root = _zero_on_bracket(
                    piece.point_of,
                    lambda t, signed=signed, point_of=piece.point_of: signed(*point_of(t)),
                    low, high, (x, y), 4.0 * abs(high - low) + 1e-9)
            if root is not None:
                x, y = root
        points.append([x, y])
        exact.append([hit.exact_x, hit.exact_y] if hit.exact_x is not None else None)
    if budget <= 0:
        warnings.append("交点太多，只细化了前 %d 个（其余保留采样精度）" % MAX_REFINE)
    sampled = options.get("merge_tol") or options.get("sampled_tol") or options["tol"]
    overlap_points, spans = _merge_overlaps(overlaps, sampled)
    if spans:
        points = [point for point in points if not _on_any_span(point, spans, sampled)]
        for point in overlap_points:
            points.append(point)
            exact.append(None)                       # 重合段没有"那个"精确交点
        warnings.append("两个图形有重合的边，重合部分只给了端点")
    return _finalize(points, exact, False, bool(spans), truncated, warnings, options,
                     reason or ("有重合段" if spans else ""))


def _intersect_pieces_analytic(pieces, other_kind, other_data, options, reason=""):
    hits, overlaps, truncated = _scan_pieces_against(pieces, other_kind, other_data,
                                                     options)
    options["sampled_tol"] = _sampled_tolerance(pieces)
    return _finish_pieces_against(hits, overlaps, other_kind, other_data, options,
                                  truncated, reason)


def _intersect_sampled(kind_a, data_a, kind_b, data_b, options, path_a, path_b):
    """两边都要采样的情形：折线引擎 + （有场/有描述时）细化。"""
    pieces_a = _pieces_of(kind_a, data_a, options, path_a)
    pieces_b = _pieces_of(kind_b, data_b, options, path_b)
    warnings = []
    if not pieces_a or not pieces_b:
        if not pieces_a:
            warnings.append("第一个图形在取景范围里没有采到点")
        if not pieces_b:
            warnings.append("第二个图形在取景范围里没有采到点")
        return _finalize([], None, False, False, False, warnings, options)
    options["sampled_tol"] = _sampled_tolerance(pieces_a + pieces_b)
    hits, truncated = _sweep_hits(pieces_a, pieces_b, options)
    field_a = _field_of(pieces_a)
    field_b = _field_of(pieces_b)
    explicit_a = _explicit_of(pieces_a)
    explicit_b = _explicit_of(pieces_b)
    window = options.get("window") or _default_window(kind_a, data_a, kind_b, data_b)
    points = []
    budget = MAX_REFINE
    for hit in hits:
        if isinstance(hit, _Overlap):
            continue
        x, y = hit.x, hit.y
        piece = hit.piece
        if options["refine"] and budget > 0 and piece is not None:
            budget -= 1
            other_field = field_b if hit.side == 0 else field_a
            other_explicit = explicit_b if hit.side == 0 else explicit_a
            this_explicit = explicit_a if hit.side == 0 else explicit_b
            root = None
            if piece.point_of is not None and piece.params is not None \
                    and hit.index is not None:
                low, high = _hit_bracket(piece, hit.index)
                if piece.field is not None:
                    root = _refine_on_curve(piece.field, piece.point_of, low, high,
                                            (x, y), 4.0 * abs(high - low) + 1e-9)
                elif other_field is not None:
                    root = _refine_on_curve(other_field, piece.point_of, low, high,
                                            (x, y), 4.0 * abs(high - low) + 1e-9)
                elif other_explicit is not None and this_explicit is not None:
                    root = _refine_explicit(this_explicit, other_explicit, low, high,
                                            (x, y))
            elif piece.field is not None and field_a is not None and field_b is not None:
                root = _newton_pair(field_a, field_b, x, y, window)
            if root is not None:
                x, y = root
        points.append([x, y])
    if not options["refine"]:
        warnings.append("按调用方要求没有细化，交点是采样精度")
    sampled = options["sampled_tol"] or options["tol"]
    overlap_points, spans = _merge_overlaps([hit.values for hit in hits
                                             if isinstance(hit, _Overlap)], sampled)
    if spans:
        points = [point for point in points if not _on_any_span(point, spans, sampled)]
        points.extend(overlap_points)
        warnings.append("两个图形有重合的部分，只给了端点")
    return _finalize(points, None, False, bool(spans), truncated, warnings,
                     options, "有重合段" if spans else "")


def _field_of(pieces):
    for piece in pieces:
        if piece.field is not None:
            return piece.field
    return None


def _explicit_of(pieces):
    """这条折线是不是"y = f(x)"形式的显函数采样？是就返回 f。"""
    for piece in pieces:
        curve = piece.curve
        if not isinstance(curve, dict) or piece.point_of is None:
            return None
        if str(curve.get("type") or "").lower() != "function":
            return None

        def value_of(x, point_of=piece.point_of):
            try:
                return float(point_of(x)[1])
            except Exception:                        # noqa: BLE001
                return None
        return value_of
    return None


def _default_window(kind_a, data_a, kind_b, data_b):
    for kind, data in ((kind_a, data_a), (kind_b, data_b)):
        if kind == "implicit":
            return data["window"]
        if kind == "conic":
            return _conic_window(data["coeff"])
    return list(DEFAULT_WINDOW)


@_total
def intersect(kind_a, data_a, kind_b, data_b, **options):
    """两个几何对象求交。见模块开头的 API 说明。"""
    kind_a = _kind_of(kind_a, "kind_a")
    kind_b = _kind_of(kind_b, "kind_b")
    options = _options(options)
    data_a = _normalize(kind_a, data_a, "data_a")
    data_b = _normalize(kind_b, data_b, "data_b")
    if _RANK[kind_a] > _RANK[kind_b]:
        kind_a, data_a, kind_b, data_b = kind_b, data_b, kind_a, data_a
    if kind_a == "point":
        return _intersect_point(data_a, kind_b, data_b, options)
    if kind_a in _LINE_LIKE and kind_b in _LINE_LIKE:
        points, exact, tangent, overlap = _line_pair(data_a, kind_a, data_b, kind_b,
                                                     options["tol"])
        reason = ""
        if overlap:
            reason = "两条直线重合" if kind_a == kind_b == "line" else "有重合的部分"
        elif not points:
            reason = "两条直线平行" if kind_a == kind_b == "line" else "平行，永远碰不上"
        return _finalize(points, exact, tangent, overlap, False, [], options, reason)
    if kind_a in _LINE_LIKE and kind_b == "circle":
        points, exact, tangent, overlap = _line_circle(data_a, kind_a, data_b,
                                                       options["tol"])
        return _finalize(points, exact, tangent, overlap, False, [], options)
    if kind_a in _LINE_LIKE and kind_b == "conic":
        points, exact, tangent, overlap = _line_conic(data_a, kind_a, data_b,
                                                      options["tol"])
        return _finalize(points, exact, tangent, overlap, False, [], options)
    if kind_a == "circle" and kind_b == "circle":
        points, exact, tangent, overlap = _circle_circle(data_a, data_b, options["tol"])
        reason = ""
        if overlap:
            reason = "两个圆完全重合"
        elif not points:
            if data_a["center"] == data_b["center"]:
                reason = "两个圆同心，半径不同"
            else:
                reason = "两个圆相离，或者一个整个在另一个里面"
        return _finalize(points, exact, tangent, overlap, False, [], options, reason)
    if kind_a == "polyline" and kind_b in _LINE_LIKE + ("circle",):
        return _intersect_pieces_analytic(_pieces_of(kind_a, data_a, options, "data_a"),
                                          kind_b, data_b, options)
    if kind_a in _LINE_LIKE + ("circle",) and kind_b == "polyline":
        return _intersect_pieces_analytic(_pieces_of(kind_b, data_b, options, "data_b"),
                                          kind_a, data_a, options)
    if kind_a in _LINE_LIKE + ("circle",) and kind_b in ("conic", "implicit"):
        # 场曲线（隐式 / 圆锥）× 直线或圆：采样场曲线，沿解析对象的参数二分细化
        pieces = _pieces_of(kind_b, data_b, options, "data_b")
        window = _window_of_data(kind_b, data_b, options)
        grid_step = max(window[1] - window[0], window[3] - window[2]) \
            / float(options["samples"])
        field = _field_of(pieces)
        hits, overlaps, truncated = _scan_pieces_against(pieces, kind_a, data_a, options)
        warnings = []
        if field is not None and options["refine"]:
            budget = MAX_REFINE
            for hit in hits:
                if budget <= 0:
                    warnings.append("交点太多，只细化了前 %d 个" % MAX_REFINE)
                    break
                budget -= 1
                root = _refine_field_analytic(field, kind_a, data_a, (hit.x, hit.y),
                                              grid_step, options)
                if root is not None:
                    hit.x, hit.y = root
        options["sampled_tol"] = _sampled_tolerance(pieces)
        return _finish_with_refined(hits, overlaps, options, truncated, warnings)
    return _intersect_sampled(kind_a, data_a, kind_b, data_b, options, "data_a", "data_b")


def _finish_with_refined(hits, overlaps, options, truncated, warnings):
    """已在外层细化过的命中：只做去重排序（别重复细化）。"""
    points = [[hit.x, hit.y] for hit in hits]
    sampled = options.get("sampled_tol") or options["tol"]
    overlap_points, spans = _merge_overlaps(overlaps, sampled)
    if spans:
        points = [point for point in points if not _on_any_span(point, spans, sampled)]
        points.extend(overlap_points)
        warnings = list(warnings) + ["两个图形有重合的边，重合部分只给了端点"]
    return _finalize(points, None, False, bool(spans), truncated, list(warnings),
                     options, "有重合段" if spans else "")


# ---------------------------------------------------------------------------
# 9. geometry_of：架构文档的对象 → (kind, data)
# ---------------------------------------------------------------------------

def _objects_table(objects):
    if objects is None:
        return {}
    if isinstance(objects, dict):
        return objects
    table = {}
    if isinstance(objects, (list, tuple)):
        for item in objects:
            if isinstance(item, dict) and item.get("id"):
                table[item["id"]] = item
    return table


def _lookup(objects, name, path):
    item = objects.get(name) if objects else None
    if item is None:
        _bad("missing_parent", "找不到它引用的对象：%s" % (name,), path,
             "先创建这个对象，或者在 objects 里把它一起传进来")
    return item


def _geometry_result(kind, data, points=()):
    points = list(points)
    result = _ok_result("many" if len(points) > 2 else ("one" if points else "none"),
                        points, "")
    result["kind"] = kind
    result["data"] = data
    return result


def _resolve_point(objects, value, path):
    """definition 里的"点"可能直接是坐标，也可能是另一个对象的 id。"""
    if isinstance(value, (list, tuple)) and len(value) == 2 \
            and not isinstance(value[0], str):
        return _point_value(value, path, "点")
    if isinstance(value, dict) and "x" in value and "y" in value:
        return _point_value(value, path, "点")
    if isinstance(value, str):
        found = geometry_of(_lookup(objects, value, path), objects)
        if not found["ok"]:
            error = found["errors"][0]
            _bad(error["code"], "父对象 %s 算不出来：%s" % (value, error["message"]),
                 path, error["hint"])
        if found["kind"] != "point":
            _bad("not_a_point", "对象 %s 不是点（是 %s）" % (value, found["kind"]), path,
                 "换一个点对象")
        return [found["data"]["x"], found["data"]["y"]]
    _bad("bad_point", "这里要一个点或者点的 id：%r" % (value,), path,
         "写成 [1, 2]、{\"x\":1,\"y\":2} 或者 \"A\"")


def _value_point(value, path):
    if isinstance(value, dict) and "x" in value and "y" in value:
        return _point_value(value, path, "点")
    return None


_CONSTRUCTED_LINES = ("perpendicular", "parallel", "perpendicular_bisector",
                      "angle_bisector", "tangent")
# 三角形的三个心：value 里是 {x, y}，几何上就是一个点（重心 / 内心 / 垂心）
_CONSTRUCTED_POINTS = {"centroid": "重心", "incenter": "内心", "orthocenter": "垂心"}
_CONSTRUCTED_LABELS = {"perpendicular": "垂线", "parallel": "平行线",
                       "perpendicular_bisector": "中垂线", "angle_bisector": "角平分线",
                       "tangent": "切线"}


def _constructed_line(value, path, label):
    """构造线（垂线/平行线/中垂线/角平分线/切线）→ 两点式直线。

    plane.py 已经把这些对象的 ``value`` 填成和 ``line`` 同形状
    （``{point, direction, a, b, c}``），所以这里**不重新推导几何**，
    只把它搬成几何层认的两点式；没有 direction 就退回直线方程 Ax+By+C=0。
    """
    if not isinstance(value, dict) or not value:
        _bad("missing_value", "%s 还没有算出来（value 是空的）" % label, path,
             "由 plane.py 先算出这条直线的 point/direction 再求交")
    point = value.get("point")
    direction = value.get("direction")
    if isinstance(point, dict) and "x" in point and "y" in point:
        point = [point["x"], point["y"]]
    if isinstance(direction, dict):
        # plane.py 的 _line_value 填的是 {x, y}；顺手也收 {dx, dy} 这种写法
        if "x" in direction and "y" in direction:
            direction = [direction["x"], direction["y"]]
        elif "dx" in direction and "dy" in direction:
            direction = [direction["dx"], direction["dy"]]
    if point is not None and direction is not None:
        start = _point_value(point, path + ".point", "%s 上的一点" % label)
        step = _point_value(direction, path + ".direction", "%s 的方向向量" % label)
        if step[0] == 0 and step[1] == 0:
            _bad("degenerate_line", "%s 的方向是 (0,0)，定不出直线" % label,
                 path + ".direction", "检查定义：这条线的方向不该退化成零向量")
        return {"a": start, "b": [start[0] + step[0], start[1] + step[1]]}
    if all(isinstance(value.get(key), (int, float)) and not isinstance(value.get(key), bool)
           for key in ("a", "b", "c")):
        ca = _number(value["a"], path + ".a", "直线方程 A")
        cb = _number(value["b"], path + ".b", "直线方程 B")
        cc = _number(value["c"], path + ".c", "直线方程 C")
        if ca == 0 and cb == 0:
            _bad("degenerate_line", "%s 的直线方程里 A、B 都是 0" % label, path, "检查定义")
        # 取系数大的那一维消元，避免除以接近 0 的数
        if abs(ca) >= abs(cb):
            start = [-cc / ca, 0.0]
        else:
            start = [0.0, -cc / cb]
        return {"a": start, "b": [start[0] - cb, start[1] + ca]}   # 方向 (-B, A)
    _bad("missing_value", "%s 的 value 里既没有 point/direction，也没有 a/b/c" % label,
         path, "由 plane.py 把这条直线的方向或方程填进 value")


@_total
def geometry_of(obj, objects=None, samples=None, widen=1.0):
    """把 PlaneObject（或它的 definition）归一化成 (kind, data)。

    优先用 ``value``（那是模型算出来的当前值），没有再回过头从 ``definition``
    里按 id 解析父对象 —— 这样拖动中的点、算好的交点都不用重算一遍。

    ``widen``：只对"自动区间"的曲线（没写窗口的显函数 / 隐式曲线）起作用 ——
    把它的采样窗口按倍数张开。求交找不到东西时会用它重试（见 ``intersect_objects``）。
    """
    if not isinstance(obj, dict):
        _bad("bad_object", "对象要是字典：%r" % (obj,), "",
             "传 PlaneObject 或者它的 definition")
    definition = obj.get("definition") if isinstance(obj.get("definition"), dict) else obj
    kind = definition.get("type") or obj.get("type")
    if not kind:
        _bad("missing_field", "对象没有 type", "type", "补上 type，例如 \"circle\"")
    kind = str(kind)
    value = obj.get("value") if isinstance(obj.get("value"), dict) else {}
    table = _objects_table(objects)

    if kind == "point":
        point = _value_point(value, "value")
        if point is None and "x" in definition and "y" in definition:
            point = _point_value([definition["x"], definition["y"]], "definition", "点")
        if point is not None:
            return _geometry_result("point", {"x": point[0], "y": point[1]}, [point])
        if isinstance(definition.get("intersect"), (list, tuple)):
            # 交点对象 {intersect: [id1, id2], index: 0}：直接算出来
            pair = list(definition["intersect"])
            if len(pair) != 2:
                _bad("bad_definition", "交点的 intersect 要正好两个对象 id",
                     "definition.intersect", "例如 {\"intersect\": [\"c\", \"l\"]}")
            first = geometry_of(_lookup(table, pair[0], "definition.intersect[0]"), table)
            second = geometry_of(_lookup(table, pair[1], "definition.intersect[1]"), table)
            if not first["ok"]:
                error = first["errors"][0]
                _bad(error["code"], "交点依赖的对象算不出来：%s" % error["message"],
                     "definition.intersect[0]", error["hint"])
            if not second["ok"]:
                error = second["errors"][0]
                _bad(error["code"], "交点依赖的对象算不出来：%s" % error["message"],
                     "definition.intersect[1]", error["hint"])
            found = intersect(first["kind"], first["data"], second["kind"], second["data"],
                              **({"samples": int(samples)} if samples else {}))
            if not found["ok"]:
                error = found["errors"][0]
                _bad(error["code"], "两个对象求交失败：%s" % error["message"],
                     "definition.intersect", error["hint"])
            index = int(definition.get("index") or 0)
            if index < 0 or index >= len(found["points"]):
                _bad("no_such_intersection",
                     "这两个对象现在只有 %d 个交点，取不到第 %d 个"
                     % (len(found["points"]), index + 1), "definition.index",
                     "改小 index，或者检查两个对象是不是还相交")
            point = found["points"][index]
            return _geometry_result("point", {"x": point[0], "y": point[1]}, [point])
        if "on" in definition:
            _bad("dynamic_point", "对象上的动点要先把 parameter 定下来才有坐标",
                 "definition.on", "由 plane_dyn 先算出坐标再传进来")
        _bad("missing_field", "点缺少坐标或构造方式", "definition",
             "写成 {x, y}，或者 {on, parameter}，或者 {intersect: [..], index}")

    if kind in _CONSTRUCTED_POINTS:
        # 三角形的重心 / 内心 / 垂心：plane.py 已经把坐标算进 value，
        # 这里只把它搬成几何层认的点，**不重新推导几何**。
        point = _value_point(value, "value")
        if point is not None:
            return _geometry_result("point", {"x": point[0], "y": point[1]}, [point])
        _bad("missing_value", "%s 还没有算出来（value 是空的）"
             % _CONSTRUCTED_POINTS.get(kind, kind), "value",
             "由 plane.py 先把这个点的坐标算出来再求交")

    if kind in ("segment", "ray", "line", "vector"):
        # 向量按**有向线段**处理：求交时只有它那一段有位置意义
        first = definition.get("start", definition.get("a"))
        second = definition.get("end", definition.get("b"))
        if first is None or second is None:
            _bad("missing_field", "%s 缺少 start / end" % kind, "definition",
                 "补上两个端点 id 或坐标")
        point_a = _resolve_point(table, first, "definition.start")
        point_b = _resolve_point(table, second, "definition.end")
        real_kind = "segment" if kind == "vector" else kind
        return _geometry_result(real_kind, _line_like({"a": point_a, "b": point_b},
                                                      real_kind, "definition"),
                                [point_a, point_b])

    if kind == "circle":
        through = definition.get("through")
        center = definition.get("center", value.get("center"))
        if center is None and isinstance(through, (list, tuple)) and len(through) == 3:
            # 三点定圆：先算外心（这时还没有圆心可用）
            points = [_resolve_point(table, item, "definition.through[%d]" % index)
                      for index, item in enumerate(through)]
            center, radius = _circumcircle(points)
            return _geometry_result("circle",
                                    _circle({"center": center, "radius": radius},
                                            "definition"), [center])
        center = _resolve_point(table, center, "definition.center")
        radius = definition.get("radius", value.get("radius"))
        if radius is None and through is not None:
            if isinstance(through, (list, tuple)):
                _bad("bad_definition", "三点定圆要正好三个点", "definition.through",
                     "给三个点 id")
            edge = _resolve_point(table, through, "definition.through")
            radius = math.hypot(edge[0] - center[0], edge[1] - center[1])
        if radius is None:
            _bad("missing_field", "圆缺少半径 radius", "definition.radius",
                 "补上半径，或者用 through / 三点构造")
        return _geometry_result("circle", _circle({"center": center, "radius": radius},
                                                  "definition"), [center])

    if kind == "polygon":
        names = definition.get("points")
        if not isinstance(names, (list, tuple)) or len(names) < 3:
            _bad("bad_definition", "多边形至少要三个点", "definition.points",
                 "给三个以上的点 id")
        points = [_resolve_point(table, name, "definition.points[%d]" % index)
                  for index, name in enumerate(names)]
        return _geometry_result("polyline", {"points": points, "closed": True}, points)

    if kind in ("arc", "sector"):
        center = _resolve_point(table, definition.get("center"), "definition.center")
        first = _resolve_point(table, definition.get("from"), "definition.from")
        second = _resolve_point(table, definition.get("to"), "definition.to")
        radius = math.hypot(first[0] - center[0], first[1] - center[1])
        start = math.atan2(first[1] - center[1], first[0] - center[0])
        end = math.atan2(second[1] - center[1], second[0] - center[0])
        while end <= start:
            end += 2.0 * math.pi
        count = max(8, min(MAX_CURVE_SAMPLES, int(samples or DEFAULT_CURVE_SAMPLES)))
        points = [[center[0] + radius * math.cos(start + (end - start) * index
                                                 / (count - 1.0)),
                   center[1] + radius * math.sin(start + (end - start) * index
                                                 / (count - 1.0))]
                  for index in range(count)]
        if kind == "sector":
            points = [list(center)] + points + [list(center)]
        return _geometry_result("polyline",
                                {"points": points, "closed": kind == "sector"}, points)

    if kind == "function":
        text = str(definition.get("expr") or "").strip()
        if not text:
            _bad("missing_field", "函数缺少 expr", "definition.expr", "例如 \"x^2\"")
        low = definition.get("xmin", definition.get("x_min"))
        high = definition.get("xmax", definition.get("x_max"))
        auto = low is None or high is None
        if auto:
            # 自动区间（老师敲的方程默认就是这种）：按模型刚采出来的那段折线求交，
            # 不然画到 ±50 的曲线只有 ±10 能求交，远处的交点会"找不到"。
            sampled = _sampled_bounds(value)
            if sampled:
                low, high = sampled[0], sampled[1]
                if widen != 1.0:
                    middle = (low + high) / 2.0
                    half = (high - low) / 2.0 * widen
                    low, high = middle - half, middle + half
        low = _number(low if low is not None else DEFAULT_WINDOW[0],
                      "definition.xmin", "起点")
        high = _number(high if high is not None else DEFAULT_WINDOW[1],
                       "definition.xmax", "终点")
        # 窗口宽了，采样点数跟着涨：不然一段折线横跨老远，交点会漏在缝里。
        # 但**封顶 1001**：求交只需要"先粗后细"——先有变号区间，再靠二分细化，
        # 密度再高只是白烧时间（2000 段的两条曲线互交一次要 80ms，老师会看到"正在算"）。
        base = max(1e-9, DEFAULT_WINDOW[1] - DEFAULT_WINDOW[0])
        wanted = int(samples or definition.get("samples")
                     or round(DEFAULT_CURVE_SAMPLES * abs(high - low) / base))
        count = max(2, min(MAX_CURVE_SAMPLES, min(1001, wanted)))
        curve = {"type": "function", "expr": text,
                 "tmin": low, "tmax": high, "count": count, "auto": bool(auto)}
        return _curve_object(curve, "definition.expr", "函数 y = %s" % text)

    if kind in ("parametric", "polar"):
        count = max(2, min(MAX_CURVE_SAMPLES,
                           int(samples or definition.get("samples")
                               or DEFAULT_CURVE_SAMPLES)))
        if kind == "parametric":
            curve = {"type": "parametric", "x": str(definition.get("x") or ""),
                     "y": str(definition.get("y") or ""), "count": count}
            label = "参数曲线"
        else:
            curve = {"type": "polar", "expr": str(definition.get("expr") or ""),
                     "count": count}
            label = "极坐标曲线 r = %s" % curve["expr"]
        curve["tmin"] = _number(definition.get("tmin", 0.0), "definition.tmin", "起点")
        curve["tmax"] = _number(definition.get("tmax", 2.0 * math.pi),
                                "definition.tmax", "终点")
        return _curve_object(curve, "definition", label)

    if kind == "implicit":
        text = str(definition.get("expr") or "").strip()
        if not text:
            _bad("missing_field", "隐式曲线缺少 expr", "definition.expr",
                 "例如 \"x^2+y^2-1\"")
        built = _implicit({"expr": text}, "definition")
        pinned = any(key in definition for key in ("xmin", "xmax", "ymin", "ymax",
                                                   "x_min", "x_max", "y_min", "y_max"))
        if pinned:
            window = _window_from(definition, "definition")
        else:
            # 和显函数同理：按模型已经采出来的那段找交点（隐式曲线是按 view 采的），
            # 不然画在 (100, 0) 那个圆求交时会去 [-10,10]² 里白找一遍
            window = _sampled_bounds(value) or list(DEFAULT_WINDOW)
            if widen != 1.0:
                middle_x = (window[0] + window[1]) / 2.0
                middle_y = (window[2] + window[3]) / 2.0
                half_x = (window[1] - window[0]) / 2.0 * widen
                half_y = (window[3] - window[2]) / 2.0 * widen
                window = [middle_x - half_x, middle_x + half_x,
                          middle_y - half_y, middle_y + half_y]
        return _geometry_result("implicit",
                                {"expr": text, "f": built["f"],
                                 "window": window, "auto": not pinned,
                                 "samples": int(definition.get("samples")
                                                or DEFAULT_FIELD_GRID)})

    if kind == "conic":
        return _geometry_result("conic",
                                {"coeff": _conic_coefficients(definition, "definition")})

    if kind in _CONSTRUCTED_LINES:
        # 垂线 / 平行线 / 中垂线 / 角平分线 / 切线：几何由 plane.py 算好放进 value，
        # 形状和 line 完全一样（point + direction，或直线方程 a/b/c），这里只做搬运。
        # "圆上动点 → 到定直线的垂线 → 与定直线求交"（垂足）就是靠这一类接上的。
        data = _constructed_line(value, "value", _CONSTRUCTED_LABELS[kind])
        return _geometry_result("line", _line_like(data, "line", "value"),
                                [data["a"], data["b"]])

    if kind == "inequality":
        _bad("not_a_curve", "不等式表示的是区域，不是曲线，没法求交点", "definition.expr",
             "先用它的边界（方程）建一个对象再求交")
    _bad("unsupported_type", "求交还不支持这种对象：%s" % kind, "type",
         "支持 point / line / segment / ray / vector / circle / polygon / arc / sector / "
         "function / implicit / parametric / polar / conic / perpendicular / parallel / "
         "perpendicular_bisector / angle_bisector / tangent")


def _curve_object(curve, path, label):
    """函数/参数/极坐标对象 → 折线 + curve 描述（带 runs，细化靠它）。"""
    pieces = _curve_pieces(curve, path)
    points = []
    runs = []
    count = curve["count"]
    t_min, t_max = curve["tmin"], curve["tmax"]
    step = (t_max - t_min) / (count - 1.0)
    for piece in pieces:
        first = int(round((piece.params[0] - t_min) / step))
        runs.append([first, first + len(piece.points) - 1])
        points.extend(piece.points)
    if not points:
        _bad("empty_curve", "%s 在给定范围里一个点都采不到" % label, path,
             "检查表达式和范围")
    resolved = dict(curve)
    resolved["runs"] = runs
    return _geometry_result("polyline",
                            {"points": points, "closed": False, "curve": resolved},
                            points)


def _circumcircle(points):
    """三点外接圆 → (圆心, 半径)。三点共线就没有外接圆，老实报错。"""
    (ax, ay), (bx, by), (cx, cy) = points
    divisor = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if divisor == 0:
        _bad("collinear_points", "这三个点在一条直线上，定不出圆",
             "definition.through", "换三个不共线的点")
    square_a = ax * ax + ay * ay
    square_b = bx * bx + by * by
    square_c = cx * cx + cy * cy
    center_x = (square_a * (by - cy) + square_b * (cy - ay) + square_c * (ay - by)) \
        / divisor
    center_y = (square_a * (cx - bx) + square_b * (ax - cx) + square_c * (bx - ax)) \
        / divisor
    return [center_x, center_y], math.hypot(ax - center_x, ay - center_y)


@_total
def _auto_window(data):
    """这份几何的窗口是不是"跟着采样走"的（找不够时可以张开重试）。

    显函数的标记在 ``data["curve"]["auto"]`` 里（曲线描述那一层），隐式曲线在顶层。
    """
    if not isinstance(data, dict):
        return False
    if data.get("auto"):
        return True
    curve = data.get("curve")
    return bool(isinstance(curve, dict) and curve.get("auto"))


def intersect_objects(object_a, object_b, objects=None, **options):
    """直接对数模对象求交：``plane.py`` 只用调这一个。

    先各自 ``geometry_of``，再 ``intersect``；任一步算不出来就返回结构化错误
    （code/message/path/hint），绝不返回半个结果。

    ``need``（可选）＝要第几个交点。要的那个没找到、而其中一边是"自动区间"的曲线时，
    会把窗口按 ``WIDEN_FACTORS`` 张开重试 —— 老师把直线转过去之后，交点会跑到曲线
    采样到的区间外面，那不是"没有交点"，是"还没找到那儿"。
    """
    try:
        need = max(1, int(options.pop("need", 1) or 1))
    except (TypeError, ValueError):
        need = 1
    first = geometry_of(object_a, objects)
    if not first["ok"]:
        return first
    second = geometry_of(object_b, objects)
    if not second["ok"]:
        return second
    found = intersect(first["kind"], first["data"], second["kind"], second["data"], **options)
    if len(found.get("points") or []) >= need:
        return found
    if not (_auto_window(first["data"]) or _auto_window(second["data"])):
        return found
    widened = found
    for factor in WIDEN_FACTORS:
        first = geometry_of(object_a, objects, widen=factor)
        second = geometry_of(object_b, objects, widen=factor)
        if not (first["ok"] and second["ok"]):
            break
        attempt = intersect(first["kind"], first["data"], second["kind"], second["data"],
                            **options)
        if len(attempt.get("points") or []) >= need:
            attempt["widened"] = factor
            return attempt
        if len(attempt.get("points") or []) > len(widened.get("points") or []):
            widened = attempt
    return widened


# ---------------------------------------------------------------------------
# 10. 点谓词 / 最近点 / 距离
# ---------------------------------------------------------------------------

def _field_of_data(kind, data):
    if kind == "conic":
        return _conic_field(data["coeff"])
    if kind == "implicit":
        return data["f"]
    return None


def _distance_to_line(point, data, kind):
    ax, ay = data["a"]
    bx, by = data["b"]
    rx, ry = bx - ax, by - ay
    square = rx * rx + ry * ry
    if square == 0:
        return math.hypot(point[0] - ax, point[1] - ay), 0.0
    t = ((point[0] - ax) * rx + (point[1] - ay) * ry) / square
    if kind == "segment":
        t = min(1.0, max(0.0, t))
    elif kind == "ray":
        t = max(0.0, t)
    foot = [ax + t * rx, ay + t * ry]
    return math.hypot(point[0] - foot[0], point[1] - foot[1]), t


def _distance_to_polyline(point, points):
    best = None
    best_t = 0.0
    for index in range(len(points) - 1):
        distance, t = _distance_to_line(point, {"a": points[index],
                                                "b": points[index + 1]}, "segment")
        if best is None or distance < best:
            best = distance
            best_t = index + t
    if best is None:
        best = math.hypot(point[0] - points[0][0], point[1] - points[0][1])
    return best, best_t


def _closest_on_polyline(point, points):
    """折线上离 point 最近的那个点（垂足落在段内）。"""
    best = None
    for index in range(len(points) - 1):
        segment = {"a": points[index], "b": points[index + 1]}
        distance, t = _distance_to_line(point, segment, "segment")
        if best is None or distance < best[0]:
            best = (distance, segment, t, index)
    if best is None:
        return list(points[0]), 0.0
    distance, segment, t, index = best
    foot = [segment["a"][0] + t * (segment["b"][0] - segment["a"][0]),
            segment["a"][1] + t * (segment["b"][1] - segment["a"][1])]
    return foot, index + t


def _project_to_field(field, point, window, samples):
    """隐式曲线的最近点：先粗采样找最近的那一段，再做"切向 + 拉回曲线"迭代。

    为什么不是简单的牛顿求根：把点搬到曲线上**不等于**搬到最近的曲线上，
    沿曲线还会漂。每轮先沿切线把垂直分量消掉、再拉回曲线，才是垂足。
    解析解要解拉格朗日方程组（高次），教室场景不值得；这里到 1e-12 量级够用，
    而且步数封顶，永远不会卡。
    """
    pieces = _field_pieces(field, window, samples)
    if not pieces:
        return None
    best = None
    for piece in pieces:
        distance, _parameter = _distance_to_polyline(point, piece.points)
        if best is None or distance < best[0]:
            best = (distance, piece)
    foot, _parameter = _closest_on_polyline(point, best[1].points)
    x, y = foot
    step = max(1e-7, (window[1] - window[0]) * 1e-7)
    for _round in range(NEWTON_STEPS + 20):
        value = _value_of(field, x, y)
        right = _value_of(field, x + step, y)
        left = _value_of(field, x - step, y)
        up = _value_of(field, x, y + step)
        down = _value_of(field, x, y - step)
        if None in (value, right, left, up, down):
            break
        gradient_x = (right - left) / (2.0 * step)
        gradient_y = (up - down) / (2.0 * step)
        square = gradient_x * gradient_x + gradient_y * gradient_y
        if square <= 0:
            break
        # 1) 沿切线 (-gy, gx) 走一步，把 (P-Q)·T 消掉（垂足条件）
        tangent_x, tangent_y = -gradient_y, gradient_x
        shift = ((point[0] - x) * tangent_x + (point[1] - y) * tangent_y) / square
        x += shift * tangent_x
        y += shift * tangent_y
        if not (math.isfinite(x) and math.isfinite(y)):
            return None
        # 2) 拉回曲线（切线是直线，走完要重新落回曲线上）
        value = _value_of(field, x, y)
        if value is None:
            break
        x -= value * gradient_x / square
        y -= value * gradient_y / square
        if abs(shift) <= 1e-15 and abs(value) <= 1e-15:
            break
        if not (math.isfinite(x) and math.isfinite(y)):
            return None
    return [x, y]


@_total
def closest_point_on(kind, data, point, **options):
    """点到对象的最近点（垂足 / 投影）。"""
    kind = _kind_of(kind, "kind")
    options = _options(options)
    data = _normalize(kind, data, "data")
    point = _point_value(point, "point", "点")
    if kind == "point":
        foot = [data["x"], data["y"]]
        return _ok_result("one", [foot], "最近点就是它自己",
                          extra={"point": foot, "value": 0.0, "distance": 0.0,
                                 "parameter": 0.0})
    if kind in _LINE_LIKE:
        distance, t = _distance_to_line(point, data, kind)
        ax, ay = data["a"]
        foot = [ax + t * (data["b"][0] - ax), ay + t * (data["b"][1] - ay)]
        return _ok_result("one", [foot], "垂足",
                          extra={"point": foot, "value": distance, "distance": distance,
                                 "parameter": t})
    if kind == "circle":
        cx, cy = data["center"]
        radius = data["radius"]
        dx, dy = point[0] - cx, point[1] - cy
        length = math.hypot(dx, dy)
        if length == 0:
            foot = [cx + radius, cy]              # 圆心：最近点不唯一，给个确定的
        else:
            foot = [cx + radius * dx / length, cy + radius * dy / length]
        distance = math.hypot(point[0] - foot[0], point[1] - foot[1])
        return _ok_result("one", [foot], "圆上的最近点",
                          extra={"point": foot, "value": distance, "distance": distance,
                                 "parameter": math.atan2(dy, dx)})
    if kind == "polyline":
        foot, parameter = _closest_on_polyline(point, data["points"])
        distance = math.hypot(point[0] - foot[0], point[1] - foot[1])
        return _ok_result("one", [foot], "折线上的最近点",
                          extra={"point": foot, "value": distance, "distance": distance,
                                 "parameter": parameter})
    field = _field_of_data(kind, data)
    window = options.get("window") or _window_of_data(kind, data, options)
    foot = _project_to_field(field, point, window, options["samples"])
    if foot is None:
        _bad("no_solution", "在这块取景范围里找不到曲线上的最近点", "options.window",
             "把取景范围调大，或者检查曲线表达式")
    distance = math.hypot(point[0] - foot[0], point[1] - foot[1])
    return _ok_result("one", [foot], "曲线上的最近点（数值投影）",
                      extra={"point": foot, "value": distance, "distance": distance,
                             "parameter": None})


@_total
def distance(kind, data, point, **options):
    """点到对象的距离（就是 ``closest_point_on`` 距离那个分量）。"""
    found = closest_point_on(kind, data, point, **options)
    if not found["ok"]:
        return found
    value = found["distance"]
    return _ok_result("one", [], "距离 %.10g" % value,
                      extra={"value": value, "distance": value,
                             "point": found["point"], "parameter": found["parameter"]})


@_total
def point_on(kind, data, point, tol=DEFAULT_TOL):
    """"点在不在这上面"。动点约束要用，所以容差可调。"""
    kind = _kind_of(kind, "kind")
    tol = _number(tol, "tol", "容差")
    data = _normalize(kind, data, "data")
    point = _point_value(point, "point", "点")
    if kind == "point":
        value = math.hypot(point[0] - data["x"], point[1] - data["y"])
    elif kind in _LINE_LIKE:
        value, _t = _distance_to_line(point, data, kind)
    elif kind == "circle":
        value = abs(math.hypot(point[0] - data["center"][0],
                               point[1] - data["center"][1]) - data["radius"])
    elif kind == "polyline":
        value, _t = _distance_to_polyline(point, data["points"])
    else:
        field = _field_of_data(kind, data)
        window = _window_of_data(kind, data, {})
        foot = _project_to_field(field, point, window, DEFAULT_FIELD_GRID)
        if foot is None:
            _bad("no_solution", "在这块取景范围里找不到曲线，判断不了点是否在线上",
                 "options.window", "把取景范围调大")
        value = math.hypot(point[0] - foot[0], point[1] - foot[1])
    on = value <= tol
    message = ("点在这个对象上（距离 %.3g）" % value if on
               else "点不在这上面（距离 %.3g，容差 %.3g）" % (value, tol))
    return _ok_result("one" if on else "none", [], message,
                      extra={"on": on, "value": value, "distance": value})


# ---------------------------------------------------------------------------
# 11. 零点（根）
# ---------------------------------------------------------------------------

def _compile_scalar(f, path):
    if callable(f):
        return f
    if isinstance(f, str):
        compiled = _compile(f, path, "表达式")
        return lambda x, compiled=compiled: compiled(x)
    _bad("bad_function", "要一个表达式字符串或者一个函数：%r" % (f,), path,
         "例如 \"x^2-2\" 或者 lambda x: x*x-2")


@_total
def roots_on_interval(f, x_min, x_max, samples=400, **options):
    """区间内的零点：变号扫描 + 二分细化。``roots`` 里是横坐标。"""
    function = _compile_scalar(f, "f")
    x_min = _number(x_min, "x_min", "左端")
    x_max = _number(x_max, "x_max", "右端")
    if x_max < x_min:
        x_min, x_max = x_max, x_min
    if x_max == x_min:
        _bad("bad_window", "区间是空的（x_min = x_max）", "x_min", "让两端不一样")
    _options(options)                                 # 顺手校验一下调用方给的选项
    count = int(max(2, min(4096, _number(samples, "samples", "采样数"))))
    step = (x_max - x_min) / (count - 1.0)
    roots = []
    previous_x = x_min
    previous_value = _value_of(function, previous_x)
    if previous_value == 0.0:
        roots.append(previous_x)
    for index in range(1, count):
        current_x = x_min + step * index
        current_value = _value_of(function, current_x)
        if current_value is None or previous_value is None:
            previous_x, previous_value = current_x, current_value
            continue
        if current_value == 0.0:
            roots.append(current_x)
        elif previous_value != 0.0 and (previous_value < 0) != (current_value < 0):
            roots.append(_bisect(lambda t: _value_of(function, t),
                                 previous_x, current_x, previous_value))
        previous_x, previous_value = current_x, current_value
    roots = sorted(roots)
    return _ok_result("many" if len(roots) > 2 else ("one" if roots else "none"),
                      [[root, 0.0] for root in roots],
                      "找到 %d 个零点" % len(roots) if roots else "这个区间里没有零点",
                      extra={"roots": roots})


@_total
def nearest_root_interval(f, x, x_min, x_max, samples=400, **options):
    """离 x 最近的那个零点，以及夹住它的区间（"根在哪两个数之间"）。"""
    found = roots_on_interval(f, x_min, x_max, samples, **options)
    if not found["ok"]:
        return found
    x = _number(x, "x", "参考位置")
    roots = found["roots"]
    if not roots:
        return _ok_result("none", [], "这个区间里没有零点",
                          extra={"root": None, "interval": None})
    nearest = roots[0]
    for root in roots:
        if abs(root - x) < abs(nearest - x):
            nearest = root
    low = _number(x_min, "x_min", "左端")
    high = _number(x_max, "x_max", "右端")
    span = abs(high - low) / max(2.0, float(samples) - 1.0)
    return _ok_result("one", [[nearest, 0.0]],
                      "最近的零点是 %.10g，落在 [%.10g, %.10g] 里"
                      % (nearest, nearest - span, nearest + span),
                      extra={"root": nearest, "interval": [nearest - span,
                                                           nearest + span],
                             "roots": roots})


__all__ = ["intersect", "intersect_objects", "geometry_of", "point_on",
           "closest_point_on", "distance", "roots_on_interval",
           "nearest_root_interval", "KIND_NAMES", "MAX_COORD", "MAX_PAIR_TESTS",
           "MAX_FIELD_GRID", "DEFAULT_TOL"]
