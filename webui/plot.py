# -*- coding: utf-8 -*-
"""作图引擎：把"函数表达式 / 几何图形描述"变成网页上能直接画出来的坐标数据。

为什么坐标在这里算、而不是让模型直接给点：
  - 让模型吐几十上百个采样点，又贵又容易算错；让它给**表达式**最省也最准；
  - 表达式在本地求值，画出来的曲线一定是对的（图画错了在课堂上很难看）。

三个入口共用这一份实现：
  - 模型工具 ``show_figure``（AI 读懂需求后直接画在网页的作图面板里）
  - 网页作图面板 ``POST /api/figure``（老师自己敲表达式，立刻出图）
  - 两者的返回结构完全一样。

坐标一律是"数学坐标"，怎么排版交给网页；这里只管算得对。
**只使用 Python 3.8 标准库，表达式用自己写的解析器求值（绝不 eval）。**
"""

import math
import re
from fractions import Fraction

MAX_EXPRESSION = 200
MAX_SAMPLES = 1200
DEFAULT_SAMPLES = 480
DEFAULT_X_MIN = -10.0
DEFAULT_X_MAX = 10.0
MAX_COORD = 1e6
MAX_SHAPES = 200

# --------------------------------------------------------------------------
# 表达式：词法 + 递归下降求值
# --------------------------------------------------------------------------

_TOKEN = re.compile(r"""
    \s*(?:
          (?P<number>\d+\.\d*|\.\d+|\d+)(?:[eE](?P<exp>[-+]?\d+))?
        | (?P<name>[A-Za-z_][A-Za-z_0-9]*)
        | (?P<op>[-+*/^(),])
        | (?P<bad>\S)
    )
""", re.X)


def _safe(function):
    """把数学函数的异常（定义域外、除零、溢出）统一变成 nan：曲线在那里断开就行。"""
    def call(*args):
        try:
            value = function(*args)
        except (ValueError, ZeroDivisionError, OverflowError, TypeError):
            return float("nan")
        try:
            return float(value)
        except (TypeError, ValueError):
            return float("nan")
    return call


def _divide(left, right):
    return float("nan") if right == 0 else left / right


def _power(base, exponent):
    try:
        return float(base ** exponent)
    except (ValueError, ZeroDivisionError, OverflowError, TypeError):
        return float("nan")


UNARY = {
    "sin": _safe(math.sin), "cos": _safe(math.cos), "tan": _safe(math.tan),
    "asin": _safe(math.asin), "acos": _safe(math.acos), "atan": _safe(math.atan),
    "sinh": _safe(math.sinh), "cosh": _safe(math.cosh), "tanh": _safe(math.tanh),
    "sqrt": _safe(math.sqrt), "abs": _safe(abs), "exp": _safe(math.exp),
    "ln": _safe(math.log), "log": _safe(math.log10), "log10": _safe(math.log10),
    "log2": _safe(math.log2), "floor": _safe(math.floor), "ceil": _safe(math.ceil),
    "round": _safe(round), "sign": _safe(lambda v: (v > 0) - (v < 0)),
}
BINARY = {
    "min": _safe(min), "max": _safe(max), "pow": _power,
    "log": _safe(lambda a, b: math.log(a, b)), "atan2": _safe(math.atan2),
}
CONSTANTS = {"pi": math.pi, "e": math.e, "tau": 2 * math.pi}


class ExpressionError(ValueError):
    """表达式看不懂时抛出，消息直接给老师/模型看。"""


def _tokenize(text):
    tokens = []
    position = 0
    while position < len(text):
        match = _TOKEN.match(text, position)
        if not match:
            break
        if match.group("bad"):
            raise ExpressionError("表达式里有看不懂的字符：%s" % match.group("bad"))
        position = match.end()
        if match.group("number"):
            value = float(match.group("number") + ("e" + match.group("exp")
                                                  if match.group("exp") else ""))
            tokens.append(("number", value))
        elif match.group("name"):
            tokens.append(("name", match.group("name").lower()))
        else:
            tokens.append(("op", match.group("op")))
    if position != len(text):
        raise ExpressionError("表达式里有看不懂的内容：%s" % text[position:position + 20])
    if not tokens:
        raise ExpressionError("表达式是空的")
    return tokens


class _Parser(object):
    """递归下降，**返回语法树**（元组），不再是闭包。

    为什么改成 AST：老师要看到"一阶导数的表达式"，那就必须能对式子做符号运算，
    闭包是算不出来的。树的形状很小，就六种：

        ("num", 值)              ("x",)                ("const", 名字)
        ("neg", 子)              ("bin", 运算符, 左, 右)  ("call", 函数, 子)
        ("call2", 函数, 左, 右)

    ``compile_expression`` 的对外契约没变（还是返回吃 x 吐 y 的函数），
    只是中间多了一层树。
    """

    def __init__(self, tokens):
        self.tokens = tokens
        self.pos = 0

    def peek(self):
        return self.tokens[self.pos] if self.pos < len(self.tokens) else (None, None)

    def take(self):
        token = self.peek()
        self.pos += 1
        return token

    def expect_op(self, symbol):
        kind, value = self.take()
        if kind != "op" or value != symbol:
            raise ExpressionError("这里应该有一个 %s" % symbol)

    def parse(self):
        node = self.expression()
        if self.pos != len(self.tokens):
            raise ExpressionError("表达式后面多出来一段，看不懂")
        return node

    def expression(self):
        node = self.term()
        while True:
            kind, value = self.peek()
            if kind == "op" and value in "+-":
                self.take()
                node = ("bin", value, node, self.term())
            else:
                return node

    def term(self):
        node = self.unary()
        while True:
            kind, value = self.peek()
            if kind == "op" and value in "*/":
                self.take()
                node = ("bin", value, node, self.unary())
            elif kind in ("number", "name") or (kind == "op" and value == "("):
                # 隐式乘法：2x、3sin(x)、x(x+1) 都当乘法（老师和模型都爱这么写）
                node = ("bin", "*", node, self.unary())
            else:
                return node

    def unary(self):
        kind, value = self.peek()
        if kind == "op" and value in "+-":
            self.take()
            inner = self.unary()
            return inner if value == "+" else ("neg", inner)
        return self.power()

    def power(self):
        """幂的底数只吃 primary，指数才允许带符号。

        这样 -x^2 是 -(x^2)（数学书上的写法），而 2^-1 也照样能写。
        """
        node = self.primary()
        kind, value = self.peek()
        if kind == "op" and value == "^":
            self.take()
            return ("bin", "^", node, self.unary())      # 右结合：2^3^2 = 2^(3^2)
        return node

    def primary(self):
        kind, value = self.take()
        if kind == "number":
            return ("num", float(value))
        if kind == "op" and value == "(":
            node = self.expression()
            self.expect_op(")")
            return node
        if kind == "name":
            if value == "x":
                return ("x",)
            if value == "y":
                return ("y",)
            if value.lower() in THETA_NAMES:
                # θ / theta / t / u：参数的第一个槽位（编译时和 x 共用）
                return ("t",)
            if value in CONSTANTS:
                return ("const", value)
            if value in UNARY:
                self.expect_op("(")
                inner = self.expression()
                self.expect_op(")")
                return ("call", value, inner)
            if value in BINARY:
                self.expect_op("(")
                first = self.expression()
                self.expect_op(",")
                second = self.expression()
                self.expect_op(")")
                return ("call2", value, first, second)
            raise ExpressionError("不认识的函数或名字：%s（可用：%s）"
                                  % (value, "、".join(sorted(list(UNARY) + list(BINARY)
                                                             + list(CONSTANTS)))))
        raise ExpressionError("表达式在这里断了，少了一项")


def _combine(operator, left, right):
    """把两个闭包按运算符拼成一个。left/right 已经是闭包，不是节点。

    闭包统一是 ``f(x, y=0)`` 两个参数：函数图像只用 x，方程（隐函数）要用到 y。
    y 给默认值，所以 ``compile_expression`` 返回的东西照样能当 f(x) 用。
    """
    if operator == "+":
        return lambda x, y=0.0: left(x, y) + right(x, y)
    if operator == "-":
        return lambda x, y=0.0: left(x, y) - right(x, y)
    if operator == "*":
        return lambda x, y=0.0: left(x, y) * right(x, y)
    if operator == "/":
        return lambda x, y=0.0: _divide(left(x, y), right(x, y))
    return lambda x, y=0.0: _power(left(x, y), right(x, y))


def _compile(node):
    """把语法树编成闭包 ``f(x, y=0)``。函数图像只用 x；方程（隐函数）两边都要用。"""
    kind = node[0]
    if kind == "num":
        value = node[1]
        return lambda x, y=0.0, value=value: value
    if kind == "x":
        return lambda x, y=0.0: x
    if kind == "y":
        return lambda x, y=0.0: y
    if kind == "t":
        # 参数曲线的参数：和 x 共用第一个槽位，所以 f(t) 直接就能算
        return lambda x, y=0.0: x
    if kind == "const":
        value = CONSTANTS[node[1]]
        return lambda x, y=0.0, value=value: value
    if kind == "neg":
        inner = _compile(node[1])
        return lambda x, y=0.0, inner=inner: -inner(x, y)
    if kind == "bin":
        return _combine(node[1], _compile(node[2]), _compile(node[3]))
    if kind == "call":
        function = UNARY[node[1]]
        inner = _compile(node[2])
        return lambda x, y=0.0, function=function, inner=inner: function(inner(x, y))
    function = BINARY[node[1]]
    left, right = _compile(node[2]), _compile(node[3])
    return lambda x, y=0.0, function=function, left=left, right=right: \
        function(left(x, y), right(x, y))


# --------------------------------------------------------------------------
# 符号求导：老师要的是"一阶导数的表达式"，不是一堆数
# --------------------------------------------------------------------------
class _NoDerivative(Exception):
    """这个函数没有初等导数（取整、符号这些）。"""


def _diff(node):
    kind = node[0]
    if kind == "num" or kind == "const":
        return ("num", 0.0)
    if kind == "x":
        return ("num", 1.0)
    if kind == "neg":
        return _neg(_diff(node[1]))
    if kind == "bin":
        operator, left, right = node[1], node[2], node[3]
        if operator == "+":
            return _add(_diff(left), _diff(right))
        if operator == "-":
            return _sub(_diff(left), _diff(right))
        if operator == "*":
            return _add(_mul(_diff(left), right), _mul(left, _diff(right)))
        if operator == "/":
            # (u'v - uv') / v²
            top = _sub(_mul(_diff(left), right), _mul(left, _diff(right)))
            return _div(top, _pow(right, ("num", 2.0)))
        if operator == "^":
            return _diff_power(left, right)
        raise _NoDerivative("不认识的运算符 %s" % operator)
    if kind == "call":
        return _diff_call(node[1], node[2])
    raise _NoDerivative("这个式子的导数算不出来")


def _diff_power(base, exponent):
    if _is_number(exponent):
        power = exponent[1]
        # n·u^(n-1)·u'
        return _mul(_mul(("num", power), _pow(base, ("num", power - 1.0))), _diff(base))
    # e^u 的导数就是 e^u·u'（不用写 ln(e)，它是 1）
    if base[0] == "const" and base[1] == "e":
        return _mul(("bin", "^", base, exponent), _diff(exponent))
    if _is_number(base) and base[1] > 0:
        # a^u 的导数：a^u · ln(a) · u'
        return _mul(_mul(("bin", "^", base, exponent), ("call", "ln", base)), _diff(exponent))
    # 一般情形 u^v：u^v · (v'·ln(u) + v·u'/u)
    inner = _add(_mul(_diff(exponent), ("call", "ln", base)),
                 _div(_mul(exponent, _diff(base)), base))
    return _mul(("bin", "^", base, exponent), inner)


_DIFF_TABLE = {
    "sin": lambda u, d: _mul(("call", "cos", u), d),
    "cos": lambda u, d: _neg(_mul(("call", "sin", u), d)),
    "tan": lambda u, d: _div(d, _pow(("call", "cos", u), ("num", 2.0))),
    "asin": lambda u, d: _div(d, ("call", "sqrt", _sub(("num", 1.0), _pow(u, ("num", 2.0))))),
    "acos": lambda u, d: _neg(_div(d, ("call", "sqrt",
                                       _sub(("num", 1.0), _pow(u, ("num", 2.0)))))),
    "atan": lambda u, d: _div(d, _add(("num", 1.0), _pow(u, ("num", 2.0)))),
    "sinh": lambda u, d: _mul(("call", "cosh", u), d),
    "cosh": lambda u, d: _mul(("call", "sinh", u), d),
    "tanh": lambda u, d: _div(d, _pow(("call", "cosh", u), ("num", 2.0))),
    "exp": lambda u, d: _mul(("call", "exp", u), d),
    "ln": lambda u, d: _div(d, u),
    "log": lambda u, d: _div(d, _mul(u, ("num", math.log(10)))),
    "log10": lambda u, d: _div(d, _mul(u, ("num", math.log(10)))),
    "log2": lambda u, d: _div(d, _mul(u, ("num", math.log(2)))),
    "sqrt": lambda u, d: _div(d, _mul(("num", 2.0), ("call", "sqrt", u))),
    "abs": lambda u, d: _mul(("call", "sign", u), d),
    # floor / ceil / round / sign 故意不放进表里：它们处处是跳跃的台阶，
    # 给个 "y' = 0" 会让老师以为那是条水平线（真给过，是误导）。
    # 不放进来的话 _diff_call 会抛 _NoDerivative，界面如实说"算不出来"。
}


def _diff_call(name, argument):
    builder = _DIFF_TABLE.get(name)
    if builder is None:
        raise _NoDerivative("%s 的导数算不出来" % name)
    return builder(argument, _diff(argument))


# -- 化简 + 拼回字符串（不求最简，只求跟老师手写的样子接近）------------------
def _is_number(node, value=None):
    if node[0] != "num":
        return False
    return value is None or abs(node[1] - value) < 1e-12


def _add(left, right):
    if _is_number(left, 0.0):
        return right
    if _is_number(right, 0.0):
        return left
    if _is_number(left) and _is_number(right):
        return ("num", left[1] + right[1])
    if right[0] == "neg":
        return _sub(left, right[1])
    return ("bin", "+", left, right)


def _sub(left, right):
    if _is_number(right, 0.0):
        return left
    if _is_number(left, 0.0):
        return _neg(right)
    if _is_number(left) and _is_number(right):
        return ("num", left[1] - right[1])
    return ("bin", "-", left, right)


def _mul(left, right):
    if _is_number(left, 0.0) or _is_number(right, 0.0):
        return ("num", 0.0)
    if _is_number(left, 1.0):
        return right
    if _is_number(right, 1.0):
        return left
    if _is_number(left, -1.0):
        return _neg(right)
    if _is_number(right, -1.0):
        return _neg(left)
    if _is_number(left) and _is_number(right):
        return ("num", left[1] * right[1])
    # 数字 × (数字 × …)：把两个系数乘起来。
    # 不做这一步的话 3·(2x) 会渲染成 "32x"——**数学上是错的**（真踩过）
    if _is_number(left) and right[0] == "bin" and right[1] == "*" and _is_number(right[2]):
        return _mul(("num", left[1] * right[2][1]), right[3])
    if _is_number(right) and left[0] == "bin" and left[1] == "*" and _is_number(left[2]):
        return _mul(("num", right[1] * left[2][1]), left[3])
    return ("bin", "*", left, right)


def _div(left, right):
    if _is_number(left, 0.0):
        return ("num", 0.0)
    if _is_number(right, 1.0):
        return left
    if left == right and not _is_number(left, 0.0):
        return ("num", 1.0)          # x / x = 1（结构一样就直接约掉）
    if _is_number(left) and _is_number(right) and right[1] != 0:
        return ("num", left[1] / right[1])
    return ("bin", "/", left, right)


def _pow(base, exponent):
    if _is_number(exponent, 1.0):
        return base
    if _is_number(exponent, 0.0):
        return ("num", 1.0)
    if _is_number(base, 1.0):
        return ("num", 1.0)
    if _is_number(base) and _is_number(exponent):
        try:
            value = base[1] ** exponent[1]
        except (OverflowError, ValueError):
            return ("bin", "^", base, exponent)
        if isinstance(value, float) and abs(value) < 1e15:
            return ("num", value)
    return ("bin", "^", base, exponent)


def _neg(node):
    if _is_number(node):
        return ("num", -node[1])
    if node[0] == "neg":
        return node[1]
    # -(2·sin(2x)) 写成 -2sin(2x)，别写成 -(sin(2x) * 2)
    if node[0] == "bin" and node[1] == "*":
        if _is_number(node[2]):
            return _mul(("num", -node[2][1]), node[3])
        if _is_number(node[3]):
            return _mul(("num", -node[3][1]), node[2])
    return ("neg", node)


_PRECEDENCE = {"+": 1, "-": 1, "*": 2, "/": 2, "^": 4}


def render(node):
    """把树写成老师看得懂的式子（尽量贴着他们手写的习惯）。"""
    kind = node[0]
    if kind == "num":
        return _render_number(node[1])
    if kind == "x":
        return "x"
    if kind == "const":
        return node[1]
    if kind == "neg":
        inner = render(node[1])
        return "-" + ("(%s)" % inner if node[1][0] == "bin" else inner)
    if kind == "call":
        return "%s(%s)" % (node[1], render(node[2]))
    if kind == "call2":
        return "%s(%s, %s)" % (node[1], render(node[2]), render(node[3]))
    operator, left, right = node[1], node[2], node[3]
    text = _render_binary(operator, left, right)
    return text


def _render_binary(operator, left, right):
    left_text = render(left)
    right_text = render(right)
    if operator in ("+", "-"):
        if operator == "-" and right[0] == "neg":
            return "%s + %s" % (left_text, render(right[1]))
        return "%s %s %s" % (left_text, operator, _wrap(right, right_text, 2))
    if operator == "*":
        # 数字和式子挨着写：2x、3sin(x)（老师就这么写，而且我们的解析器也认）
        if _is_number(left) and not _is_number(right):
            coefficient = _render_number(left[1])
            right_wrapped = _wrap(right, right_text, 2)
            # ★ 右边以数字开头时绝对不能挨着写："3" + "2x" 会变成 "32x"（真踩过，
            #   数学上是错的）。这种情况老实加乘号。
            if right_wrapped[:1].isdigit():
                return "%s * %s" % (coefficient, right_wrapped)
            if right[0] == "neg":
                return "-%s%s" % (coefficient, _wrap(right[1], render(right[1]), 2))
            if coefficient == "1":
                return right_wrapped
            if coefficient == "-1":
                return "-" + right_wrapped
            return "%s%s" % (coefficient, right_wrapped)
        return "%s * %s" % (_wrap(left, left_text, 2), _wrap(right, right_text, 2))
    if operator == "/":
        return "%s / %s" % (_wrap(left, left_text, 2), _wrap(right, right_text, 3))
    if operator == "^":
        return "%s^%s" % (_wrap(left, left_text, 5), _wrap(right, right_text, 4))
    return "%s %s %s" % (left_text, operator, right_text)


def _wrap(node, text, needed):
    if node[0] == "bin" and _PRECEDENCE.get(node[1], 9) < needed:
        return "(%s)" % text
    if node[0] == "neg":
        return "(%s)" % text
    return text


def _render_number(value):
    if abs(value - round(value)) < 1e-12 and abs(value) < 1e15:
        return str(int(round(value)))
    text = ("%.6f" % value).rstrip("0").rstrip(".")
    return text or "0"


def derivative_expression(text):
    """返回 (一阶导数的字符串, 错误消息)。算不出来时第二个是给老师看的原因。"""
    node = _parse_node(text)
    try:
        return render(_diff(node)), ""
    except _NoDerivative as error:
        return "", str(error)
    except (ValueError, OverflowError) as error:
        return "", "这个式子的导数算不出来（%s）" % error


# --------------------------------------------------------------------------
# 函数图像：采样 + 断点切分
# --------------------------------------------------------------------------


def _parse_node(text):
    """归一化 + 解析成语法树。"""
    text = str(text or "").strip()
    if not text:
        raise ExpressionError("表达式是空的")
    if len(text) > MAX_EXPRESSION:
        raise ExpressionError("表达式太长（上限 %d 个字符）" % MAX_EXPRESSION)
    # 老师习惯写 "y = sin(x)"，把左边那个 y= 去掉
    text = re.sub(r"^\s*y\s*=", "", text, flags=re.I)
    # 常见的中文写法先归一化
    text = (text.replace("（", "(").replace("）", ")").replace("，", ",")
                .replace("π", "pi").replace("×", "*").replace("÷", "/")
                .replace("−", "-").replace("^", "^")
                # 极坐标的 θ：老师在纸上写 θ，键盘上打 theta。词法只认 ASCII 字母，
                # 所以这里先换成 theta（参数名统一在编译时归到 t）
                .replace("θ", "theta").replace("Θ", "theta"))
    return _Parser(_tokenize(text)).parse()


def compile_expression(text):
    """把表达式编译成 ``f(x)``。看不懂就抛 ExpressionError（消息能直接给老师看）。"""
    return _compile(_parse_node(text))


# --------------------------------------------------------------------------
def _robust_range(values):
    """取 2%–98% 分位当纵轴范围：tan(x) 那种 ±1e15 的尖峰不该把整张图压成一条线。"""
    ordered = sorted(values)
    if not ordered:
        return -1.0, 1.0
    low = ordered[int(len(ordered) * 0.02)]
    high = ordered[int(len(ordered) * 0.98)]
    if not (high > low):
        low, high = ordered[0], ordered[-1]
    if not (high > low):
        low, high = low - 1.0, high + 1.0
    pad = (high - low) * 0.12
    return low - pad, high + pad


def _find_extrema(slope_of, x_min, x_max, samples):
    """找区间里的极值点：看导数的符号变化，再用二分法逼近。

    老师拖动动点的时候会在极值点"顿一下"，靠的就是这份坐标。
    """
    if slope_of is None:
        return []
    points = []
    step = (x_max - x_min) / (samples - 1.0)
    previous_x, previous_slope = None, None
    for index in range(samples):
        x = x_min + step * index
        try:
            slope = float(slope_of(x))
        except Exception:                                  # noqa: BLE001
            slope = float("nan")
        if not math.isfinite(slope):
            previous_x, previous_slope = None, None
            continue
        if previous_slope is not None and previous_slope != 0 and slope != 0:
            if (previous_slope > 0) != (slope > 0):
                # 在 [previous_x, x] 里二分找导数的零点
                low, high = previous_x, x
                low_sign = previous_slope > 0
                for _step in range(60):
                    middle = (low + high) / 2.0
                    try:
                        middle_slope = float(slope_of(middle))
                    except Exception:                      # noqa: BLE001
                        break
                    if not math.isfinite(middle_slope) or middle_slope == 0:
                        break
                    if (middle_slope > 0) == low_sign:
                        low = middle
                    else:
                        high = middle
                # 导数由负变正 = 极小值；由正变负 = 极大值。
                # 这里 low_sign 记的是"左边那段导数是正的"，所以正的才是极大。
                points.append(((low + high) / 2.0, "max" if low_sign else "min"))
        previous_x, previous_slope = x, slope
    return points


def build_function(args):
    expression_text = str(args.get("expression") or "").strip()
    node = _parse_node(expression_text)
    expression = _compile(node)

    # 一阶导数的表达式：老师要看到的是"y' = 2x - 2"这种，不是一堆数
    derivative_text, derivative_error, slope_of = "", "", None
    try:
        derivative_node = _diff(node)
        derivative_text = render(derivative_node)
        slope_of = _compile(derivative_node)
    except _NoDerivative:
        derivative_error = "这个函数没有初等导数（取整、符号这类函数算不出来）"
    except (ValueError, OverflowError, ZeroDivisionError):
        derivative_error = "这个函数的一阶导数算不出来"
    try:
        x_min = float(args.get("x_min", DEFAULT_X_MIN))
        x_max = float(args.get("x_max", DEFAULT_X_MAX))
    except (TypeError, ValueError):
        raise ExpressionError("x_min / x_max 要是数字")
    for name, value in (("x_min", x_min), ("x_max", x_max)):
        if not math.isfinite(value) or abs(value) > MAX_COORD:
            raise ExpressionError("%s 超出可画范围" % name)
    if x_max <= x_min:
        x_min, x_max = min(x_min, x_max), max(x_min, x_max)
    if x_max - x_min < 1e-6:
        raise ExpressionError("画图范围太小了（x_max 要比 x_min 大）")

    try:
        samples = int(args.get("samples") or DEFAULT_SAMPLES)
    except (TypeError, ValueError):
        samples = DEFAULT_SAMPLES
    samples = max(24, min(MAX_SAMPLES, samples))

    values = []
    for index in range(samples):
        x = x_min + (x_max - x_min) * index / (samples - 1.0)
        try:
            y = float(expression(x))
        except Exception:                                  # noqa: BLE001 - 任何异常都当成"这里没值"
            y = float("nan")
        if not math.isfinite(y) or abs(y) > MAX_COORD:
            y = float("nan")
        slope = None
        if slope_of is not None:
            try:
                slope = float(slope_of(x))
            except Exception:                              # noqa: BLE001
                slope = None
            if slope is not None and (not math.isfinite(slope) or abs(slope) > MAX_COORD):
                slope = None
        values.append((x, y, slope))

    y_min, y_max = _robust_range([y for _x, y, _s in values if math.isfinite(y)])
    span = y_max - y_min
    segments, current = [], []
    for x, y, slope in values:
        if not math.isfinite(y) or y < y_min - span * 4 or y > y_max + span * 4:
            if len(current) > 1:
                segments.append(current)
            current = []
            continue
        if current and abs(y - current[-1][1]) > span * 1.5:
            # tan / 1/x 这类竖直渐近线：不要连成一条竖线
            segments.append(current)
            current = []
        # 第三个分量是这一点的一阶导数值：界面靠它在动点处画切线、显示导数值
        current.append([round(x, 4), round(y, 4),
                        None if slope is None else round(slope, 4)])
    if len(current) > 1:
        segments.append(current)

    if not segments:
        raise ExpressionError("这个表达式画不出东西（可能定义域是空的，或者范围选得不对）")

    extrema = []
    for x, kind in _find_extrema(slope_of, x_min, x_max, samples):
        try:
            y = float(expression(x))
        except Exception:                                  # noqa: BLE001
            continue
        if math.isfinite(y) and y_min - span <= y <= y_max + span:
            extrema.append({"x": round(x, 4), "y": round(y, 4), "kind": kind})

    return {
        "kind": "function",
        "title": str(args.get("title") or "").strip(),
        "note": str(args.get("note") or "").strip(),
        "label": "y = %s" % (expression_text or str(args.get("expression") or "").strip()),
        "expression": expression_text,
        "derivative": derivative_text,
        "derivativeError": derivative_error,
        "extrema": extrema,
        "x_min": round(x_min, 4), "x_max": round(x_max, 4),
        "y_min": round(y_min, 4), "y_max": round(y_max, 4),
        "segments": segments,
        "bbox": _curve_bbox(segments),
    }


# --------------------------------------------------------------------------
# 几何图形：一行一条的小语法
# --------------------------------------------------------------------------
_POINT_CALL = re.compile(r"""^\s*([A-Za-z][A-Za-z0-9_']*)\s*[=＝]?\s*
                             [(（]\s*(-?\d+(?:\.\d+)?)\s*[,，]\s*(-?\d+(?:\.\d+)?)\s*[)）]\s*$""",
                         re.X)
_POINT_PLAIN = re.compile(r"^\s*([A-Za-z][A-Za-z0-9_']*)\s*[=＝]?\s+"
                          r"(-?\d+(?:\.\d+)?)\s*[,，]?\s+(-?\d+(?:\.\d+)?)\s*$")
_NUMBER = re.compile(r"^-?\d+(?:\.\d+)?$")

# 曲面用多少段多边形逼近：36 段肉眼已经是圆了，再细只是拖慢渲染
ROUND_SEGMENTS = 36

SEGMENT_WORDS = ("seg", "segment", "线段")
LINE_WORDS = ("line", "直线")
RAY_WORDS = ("ray", "射线")
CIRCLE_WORDS = ("circle", "圆", "⊙")
POLY_WORDS = ("poly", "polygon", "多边形", "三角形")
ANGLE_WORDS = ("angle", "角")
FACE_WORDS = ("face", "面", "平面")
PRISM_WORDS = ("prism", "柱", "棱柱")
PYRAMID_WORDS = ("pyramid", "锥", "棱锥")
BOX_WORDS = ("box", "cube", "长方体", "正方体")
CYLINDER_WORDS = ("cylinder", "圆柱", "圆柱体")
CONE_WORDS = ("cone", "圆锥", "圆锥体")
SPHERE_WORDS = ("sphere", "ball", "球", "球体")
CIRCLE3_WORDS = ("circle3", "过三点圆", "三点圆")
ONCIRCLE_WORDS = ("oncircle", "圆上动点", "圆上")
ONSURFACE_WORDS = ("onsurface", "侧面动点", "侧面上")
ONSPHERE_WORDS = ("onsphere", "球面动点", "球面上")
SECTION_WORDS = ("spheresection", "球截面", "截面圆")
LINEANGLE_WORDS = ("lineangle", "线线角")
LINEPLANE_WORDS = ("lineplane", "线面角")
PLANEANGLE_WORDS = ("planeangle", "面面角", "二面角")

SYNTAX_HELP = ('每条一个：A(0,0) 定义点；seg A B 线段；line A B 直线；ray A B 射线；'
               'circle O 3 圆心半径；circle O A 过点作圆；poly A B C 多边形；angle A B C 角')


def _parse_point(line):
    match = _POINT_CALL.match(line) or _POINT_PLAIN.match(line)
    if not match:
        return None
    return match.group(1), float(match.group(2)), float(match.group(3))


# 特殊角的精确 sin/cos（查表比从坐标反推可靠）
NICE_ANGLES = {
    0: ("0", "1"), 30: ("1/2", "√3/2"), 45: ("√2/2", "√2/2"), 60: ("√3/2", "1/2"),
    90: ("1", "0"), 120: ("√3/2", "-1/2"), 135: ("√2/2", "-√2/2"),
    150: ("1/2", "-√3/2"), 180: ("0", "-1"),
}


def _nice_fraction(value):
    """把坐标还原成"简单有理数"；还原不出来就返回 None。

    必须做这一步：老师写的 C(0.5, 0.8660254) 是个浮点，直接丢给 Exact
    会算出一个天文数字（分子分母都是十几位），比不给还糟。
    """
    try:
        fraction = Fraction(value).limit_denominator(1000)
    except (ValueError, OverflowError):
        return None
    if abs(float(fraction) - value) > 1e-9:
        return None
    return fraction


def _angle_info(points, shape):
    """算一个角的度数，以及 sin / cos 的**精确值**（分数或根号）。

    老师在黑板上写的是 sin∠ABC = 3/5 这种，不是 0.6。所以这里用 calc 里的 Exact
    算：设 u = A - B、v = C - B，则

        cos = (u·v) / √(|u|²·|v|²)        sin = |u×v| / √(|u|²·|v|²)

    两条退路，按可靠性排序：
      1. 坐标都是简单有理数（整点最典型）→ 从坐标精确算，能出 3/5、√2/2 这种；
      2. 坐标里有浮点，但角度正好是特殊角 → 查表给标准值（√3/2 这种）；
      3. 都不行 → 只给度数，如实标 exact=False。
    """
    a = points.get(shape.get("from"))
    vertex = points.get(shape.get("at"))
    c = points.get(shape.get("to"))
    if not (a and vertex and c):
        return {}
    ux, uy = a[0] - vertex[0], a[1] - vertex[1]
    vx, vy = c[0] - vertex[0], c[1] - vertex[1]
    norm_u = ux * ux + uy * uy
    norm_v = vx * vx + vy * vy
    if norm_u < 1e-12 or norm_v < 1e-12:
        return {}
    dot = ux * vx + uy * vy
    cross = ux * vy - uy * vx
    length = math.sqrt(norm_u) * math.sqrt(norm_v)
    degrees = math.degrees(math.acos(max(-1.0, min(1.0, dot / length))))
    info = {"degrees": round(degrees, 3)}

    nice = None
    for candidate in sorted(NICE_ANGLES):
        if abs(degrees - candidate) < 0.02:
            nice = candidate
            break
    if nice is not None:
        info["nice"] = nice

    exact_done = False
    fractions = [_nice_fraction(value) for value in (ux, uy, vx, vy)]
    if all(item is not None for item in fractions):
        try:
            from calc import Exact
            fu_x, fu_y, fv_x, fv_y = fractions
            f_norm_u = fu_x * fu_x + fu_y * fu_y
            f_norm_v = fv_x * fv_x + fv_y * fv_y
            f_dot = fu_x * fv_x + fu_y * fv_y
            f_cross = fu_x * fv_y - fu_y * fv_x
            denominator = Exact.rational(f_norm_u * f_norm_v).sqrt()
            info["cos"] = (Exact.rational(f_dot) / denominator).format()
            info["sin"] = (Exact.rational(abs(f_cross)) / denominator).format()
            exact_done = True
        except Exception:                                  # noqa: BLE001
            exact_done = False
    if not exact_done and nice is not None:
        info["sin"], info["cos"] = NICE_ANGLES[nice]
        exact_done = True
    info["exact"] = exact_done
    return info


def build_geometry(args):
    raw = args.get("shapes")
    if isinstance(raw, str):
        raw = [item for item in re.split(r"[\n;；]", raw) if item.strip()]
    if not isinstance(raw, list) or not raw:
        raise ExpressionError("shapes 不能为空。" + SYNTAX_HELP)
    if len(raw) > MAX_SHAPES:
        raise ExpressionError("图形太多了（上限 %d 条）" % MAX_SHAPES)

    points = {}
    order = []
    shapes = []
    for item in raw:
        line = str(item or "").strip()
        if not line:
            continue
        parsed = _parse_point(line)
        if parsed:
            name, x, y = parsed
            if abs(x) > MAX_COORD or abs(y) > MAX_COORD:
                raise ExpressionError("点 %s 的坐标超出可画范围" % name)
            if name not in points:
                order.append(name)
            points[name] = (x, y)
            continue
        parts = line.replace(",", " ").replace("，", " ").split()
        if not parts:
            continue
        word = parts[0].lower()
        rest = parts[1:]
        try:
            if word in SEGMENT_WORDS + LINE_WORDS + RAY_WORDS:
                if len(rest) != 2:
                    raise ExpressionError("「%s」需要两个点：%s" % (line, SYNTAX_HELP))
                kind = ("segment" if word in SEGMENT_WORDS
                        else "line" if word in LINE_WORDS else "ray")
                shapes.append({"type": kind, "a": rest[0], "b": rest[1]})
            elif word in CIRCLE_WORDS:
                if len(rest) != 2:
                    raise ExpressionError("「%s」需要圆心和半径（或圆上一点）：%s"
                                          % (line, SYNTAX_HELP))
                center, second = rest[0], rest[1]
                if _NUMBER.match(second):
                    shapes.append({"type": "circle", "center": center,
                                   "radius": float(second)})
                else:
                    shapes.append({"type": "circle", "center": center, "through": second})
            elif word in POLY_WORDS:
                if len(rest) < 3:
                    raise ExpressionError("多边形至少要三个点：%s" % SYNTAX_HELP)
                shapes.append({"type": "poly", "points": rest})
            elif word in ANGLE_WORDS:
                if len(rest) != 3:
                    raise ExpressionError("角要写三个点（顶点在中间）：%s" % SYNTAX_HELP)
                shapes.append({"type": "angle", "from": rest[0], "at": rest[1],
                               "to": rest[2]})
            else:
                raise ExpressionError("看不懂这一条：%s。%s" % (line, SYNTAX_HELP))
        except ExpressionError:
            raise

    if not points:
        raise ExpressionError("至少要有一个点。" + SYNTAX_HELP)

    def need(name, where):
        if name not in points:
            raise ExpressionError("%s 用到了没定义的点 %s（已知：%s）"
                                  % (where, name, "、".join(order)))

    for shape in shapes:
        for key in ("a", "b"):
            if key in shape:
                need(shape[key], shape["type"])
        if shape["type"] == "circle":
            need(shape["center"], "circle")
            if shape.get("through"):
                need(shape["through"], "circle")
        if shape["type"] == "poly":
            for name in shape["points"]:
                need(name, "poly")
        if shape["type"] == "angle":
            for key in ("from", "at", "to"):
                need(shape[key], "angle")

    xs = [value[0] for value in points.values()]
    ys = [value[1] for value in points.values()]
    for shape in shapes:
        if shape["type"] == "circle":
            center = points[shape["center"]]
            radius = shape.get("radius")
            if radius is None:
                other = points[shape["through"]]
                radius = math.hypot(other[0] - center[0], other[1] - center[1])
                shape["radius"] = round(radius, 6)
            if radius <= 0:
                raise ExpressionError("圆的半径要大于 0")
            xs += [center[0] - radius, center[0] + radius]
            ys += [center[1] - radius, center[1] + radius]

    # 角：把度数和 sin/cos 的精确值算出来（老师要的是 3/5 这种，不是 0.6）
    for shape in shapes:
        if shape["type"] == "angle":
            info = _angle_info(points, shape)
            if info:
                shape.update(info)

    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)
    pad = max(x_max - x_min, y_max - y_min, 1.0) * 0.15
    return {
        "kind": "geometry",
        "title": str(args.get("title") or "").strip(),
        "note": str(args.get("note") or "").strip(),
        "x_min": round(x_min - pad, 4), "x_max": round(x_max + pad, 4),
        "y_min": round(y_min - pad, 4), "y_max": round(y_max + pad, 4),
        "points": [{"name": name, "x": round(points[name][0], 4),
                    "y": round(points[name][1], 4)} for name in order],
        "shapes": shapes,
        # 原始文本原样回显：工具栏点出来的图要能拿着它继续改（来回转换会丢信息）
        "source": [str(item).strip() for item in raw if str(item or "").strip()],
        "bbox": [round(x_min, 4), round(y_min, 4), round(x_max, 4), round(y_max, 4)],
    }


# --------------------------------------------------------------------------
# 空间几何（立体图形）：点、棱、面，以及棱柱 / 棱锥 / 长方体
#
# 面一律要求**共面**：老师画"面 ABCD"时如果四个点不共面，画出来是扭曲的，
# 与其将错就错不如直接说清楚（老师明确要求过"不共面就拒绝"）。
# --------------------------------------------------------------------------
_POINT3 = re.compile(r"^\s*([A-Za-z][A-Za-z0-9_']*)\s*[=＝]?\s*[(（]\s*(.+?)\s*[)）]\s*$")
SOLID_HELP = ("写法：A(0,0,0) 定义空间点；seg A B 连棱；face A B C 画面（要求共面）；"
              "prism A B C 3 三棱柱（底面 + 高）；pyramid A B C 3 三棱锥；"
              "box A B C D 2 长方体；\n"
              "cylinder O 3 5 圆柱；cone O 3 5 圆锥；sphere O 3 球；\n"
              "线线角 A B C D；线面角 A B C D E；面面角 A B C D E F；\n"
              "oncircle M ⊙O 30 圆上动点；onsurface M ⊙O 45 .5 侧面动点；\n"
              "onsphere M O 30 60 球面动点；spheresection O A B C 球截面。")
COPLANAR_TOLERANCE = 1e-6


def _parse_point3(line):
    """A(1, 2, 3) / A(1 2 3) / A(1，2，3) —— 空间点，三个坐标。"""
    match = _POINT3.match(line)
    if not match:
        return None
    inside = match.group(2).replace("，", ",").replace(" ", ",")
    parts = [piece for piece in inside.split(",") if piece.strip()]
    if len(parts) != 3:
        raise ExpressionError("空间点要写三个坐标，例如 A(1, 2, 3)：%s" % line)
    try:
        return match.group(1), tuple(float(piece) for piece in parts)
    except ValueError:
        raise ExpressionError("点的坐标要是数字：%s" % line)


def _normal(a, b, c):
    ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
    return (uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx)


def _face_normal(coords):
    """从多边形顶点求一个法向量；退化了就返回 None。"""
    for index in range(1, len(coords) - 1):
        candidate = _normal(coords[0], coords[index], coords[index + 1])
        length = math.sqrt(sum(value * value for value in candidate))
        if length > 1e-9:
            return tuple(value / length for value in candidate)
    return None


def _is_coplanar(coords):
    """这些点共不共面。少于 4 个点总是共面的。"""
    if len(coords) < 4:
        return True
    origin = coords[0]
    normal = None
    for index in range(1, len(coords) - 1):
        candidate = _normal(origin, coords[index], coords[index + 1])
        if any(abs(value) > 1e-12 for value in candidate):
            normal = candidate
            break
    if normal is None:
        return True                      # 全都共线，谈不上"不共面"
    length = math.sqrt(sum(value * value for value in normal)) or 1.0
    for point in coords:
        offset = (point[0] - origin[0], point[1] - origin[1], point[2] - origin[2])
        distance = abs(sum(offset[i] * normal[i] for i in range(3))) / length
        if distance > COPLANAR_TOLERANCE:
            return False
    return True


def _triangle_area(a, b, c):
    normal = _normal(a, b, c)
    return math.sqrt(sum(value * value for value in normal)) / 2.0


def _face_area(coords):
    """空间多边形的面积：拆成三角形算（顶点按顺序给出，都是凸的或简单多边形）。"""
    if len(coords) < 3:
        return 0.0
    total = 0.0
    for index in range(1, len(coords) - 1):
        total += _triangle_area(coords[0], coords[index], coords[index + 1])
    return total


def _polyhedron_volume(faces, points):
    """闭合多面体的体积：各个面绕原点张成的有向四面体体积之和。

    没闭合的话这个和是没有几何意义的，所以先由调用方检查闭合性。
    """
    total = 0.0
    for face in faces:
        coords = [points[name] for name in face]
        for index in range(1, len(coords) - 1):
            a, b, c = coords[0], coords[index], coords[index + 1]
            total += (a[0] * (b[1] * c[2] - b[2] * c[1])
                      - a[1] * (b[0] * c[2] - b[2] * c[0])
                      + a[2] * (b[0] * c[1] - b[1] * c[0])) / 6.0
    return abs(total)


def _is_closed(faces):
    """每条棱是否正好被两个面共用——闭合多面体的判据。"""
    count = {}
    for face in faces:
        for index in range(len(face)):
            nxt = (index + 1) % len(face)
            key = tuple(sorted((face[index], face[nxt])))
            count[key] = count.get(key, 0) + 1
    return bool(count) and all(value == 2 for value in count.values())


def solid_measures(points, faces):
    """表面积、体积（不闭合就没有体积）。"""
    measures = {}
    areas = {}
    total_area = 0.0
    for face in faces:
        coords = [points[name] for name in face]
        area = _face_area(coords)
        areas["".join(face)] = round(area, 4)
        total_area += area
    if faces:
        measures["area"] = round(total_area, 4)
        measures["faces"] = areas
    if faces and _is_closed(faces):
        measures["volume"] = round(_polyhedron_volume(faces, points), 4)
    return measures


# --------------------------------------------------------------------------
# 空间里的三种角。都按定义算，再交给 Exact 化简成分数/根号。
# --------------------------------------------------------------------------
def _vec(a, b):
    return (b[0] - a[0], b[1] - a[1], b[2] - a[2])


def _dot(u, v):
    return u[0] * v[0] + u[1] * v[1] + u[2] * v[2]


def _cross(u, v):
    return (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])


def _norm(u):
    return math.sqrt(_dot(u, u))


def _exact_ratio(numerator, denominator_squared):
    """把 分子 / √分母 化成精确形式（3/5、√2/2 这种）。算不出就返回 None。"""
    from calc import Exact
    try:
        return (Exact.rational(numerator) / Exact.rational(denominator_squared).sqrt()).format()
    except Exception:                                      # noqa: BLE001
        return None


def _exact_of(parts):
    """把一串整数（坐标）换算成 Exact 能用的分数；有浮点就返回 None。"""
    fractions = []
    for value in parts:
        fraction = _nice_fraction(value)
        if fraction is None:
            return None
        fractions.append(fraction)
    return fractions


def _sub3(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot3(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross3(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _perpendicular(axis):
    """给一个轴，随便找一个和它垂直的单位向量（用来建圆的平面基）。"""
    length = math.sqrt(_dot3(axis, axis)) or 1.0
    unit = tuple(value / length for value in axis)
    seed = (0.0, 0.0, 1.0) if abs(unit[2]) < 0.9 else (1.0, 0.0, 0.0)
    side = _cross3(unit, seed)
    size = math.sqrt(_dot3(side, side)) or 1.0
    return tuple(value / size for value in side)


def _solid_angle_info(points, shape, want_exact=True):
    """算度数，并尽量给出精确的 sin / cos。

    三种角的定义不一样，别混：
      线线角：cosθ = |u·v| / (|u||v|)，取锐角
      线面角：sinθ = |u·n| / (|u||n|)   —— 线面角是用**正弦**定义的，cos 由它推
      面面角：cosθ = |n₁·n₂| / (|n₁||n₂|)，取两个平面的锐夹角

    精确值只在**坐标都是简单有理数**时才算（整点最典型）。坐标里带浮点
    （比如正四面体的 0.866…）就退回查表；表里也没有就只给度数，别硬凑。
    """
    from fractions import Fraction

    kind = shape["type"]
    names = shape["names"]
    coords = [points[name] for name in names]
    if any(item is None for item in coords):
        return {"error": "有点找不到"}

    def degenerate(reason):
        """退化了就如实说，别抛异常 —— 拖动时会经过这些位置。"""
        return {"error": reason}

    def as_fraction(value):
        fraction = _nice_fraction(value)
        return fraction

    exact_ready = all(as_fraction(item) is not None
                      for point in coords for item in point)

    def build(vectors_builder):
        """返回 (定义用的分子, 平方后的分母, 另外一种三角函数的分子)。"""
        return vectors_builder()

    # ---- 数值部分（永远都算得出）----
    if kind == "lineangle":
        u = _vec(coords[0], coords[1])
        v = _vec(coords[2], coords[3])
        numerator = abs(_dot(u, v))
        other = _norm(_cross(u, v))
        squared = _dot(u, u) * _dot(v, v)
        by = "cos"
    elif kind == "lineplane":
        u = _vec(coords[0], coords[1])
        normal = _cross(_vec(coords[2], coords[3]), _vec(coords[2], coords[4]))
        if _norm(normal) < 1e-12:
            return degenerate("平面 %s 的三个点共线，定不出平面" % "".join(names[2:]))
        if _norm(u) < 1e-12:
            return degenerate("这条线退化成点了")
        numerator = abs(_dot(u, normal))            # 这个是 sin 的分子
        other = _norm(_cross(u, normal))
        squared = _dot(u, u) * _dot(normal, normal)
        by = "sin"
    else:
        # 第二个面从哪开始：按分组来（面可以整圈选，不是固定三个点）
        first_size = len(shape.get("groups", [[], []])[0]) or 3
        n1 = _cross(_vec(coords[0], coords[1]), _vec(coords[0], coords[2]))
        n2 = _cross(_vec(coords[first_size], coords[first_size + 1]),
                    _vec(coords[first_size], coords[first_size + 2]))
        if _norm(n1) < 1e-12 or _norm(n2) < 1e-12:
            return degenerate("有平面的三个点共线，定不出平面")
        numerator = abs(_dot(n1, n2))
        other = _norm(_cross(n1, n2))
        squared = _dot(n1, n1) * _dot(n2, n2)
        by = "cos"

    if squared < 1e-18:
        return degenerate("图形退化了，算不出角")
    ratio = max(-1.0, min(1.0, numerator / math.sqrt(squared)))
    degrees = math.degrees(math.asin(ratio) if by == "sin" else math.acos(ratio))
    info = {"degrees": round(degrees, 3), "by": by}
    for nice in (0, 30, 45, 60, 90):
        if abs(degrees - nice) < 0.02:
            info["nice"] = nice
            break

    # ---- 精确部分（拖动中不做：坐标一直在变，分数根号既慢又没意义）----
    if not want_exact:
        info["sin"] = round(math.sqrt(max(0.0, 1.0 - ratio * ratio)), 4) \
            if by == "cos" else round(ratio, 4)
        info["cos"] = round(ratio, 4) if by == "cos" \
            else round(math.sqrt(max(0.0, 1.0 - ratio * ratio)), 4)
        info["exact"] = False
        info["approximate"] = True
        return info

    exact = None
    # 分母一大，Exact 的化简就会变得又慢又没意义（老师要的是 3/5 这种漂亮值）。
    # 数太大就干脆不算精确值，退回度数。
    if exact_ready and squared > 1e10:
        exact_ready = False
    if exact_ready:
        try:
            from calc import Exact
            fractions = [[Fraction(item) for item in point] for point in coords]

            def frac_vec(a, b):
                return [b[i] - a[i] for i in range(3)]

            def frac_dot(a, b):
                return sum(a[i] * b[i] for i in range(3))

            def frac_cross(a, b):
                return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2],
                        a[0] * b[1] - a[1] * b[0]]

            if kind == "lineangle":
                u2 = frac_vec(fractions[0], fractions[1])
                v2 = frac_vec(fractions[2], fractions[3])
                first = abs(frac_dot(u2, v2))
                second_squared = frac_dot(u2, u2) * frac_dot(v2, v2)
                second_numerator = abs(frac_dot(frac_cross(u2, v2), frac_cross(u2, v2)))
            elif kind == "lineplane":
                u2 = frac_vec(fractions[0], fractions[1])
                n2 = frac_cross(frac_vec(fractions[2], fractions[3]),
                                frac_vec(fractions[2], fractions[4]))
                first = abs(frac_dot(u2, n2))
                second_squared = frac_dot(u2, u2) * frac_dot(n2, n2)
                second_numerator = None
            else:
                n1 = frac_cross(frac_vec(fractions[0], fractions[1]),
                                frac_vec(fractions[0], fractions[2]))
                n2b = frac_cross(frac_vec(fractions[first_size], fractions[first_size + 1]),
                                 frac_vec(fractions[first_size], fractions[first_size + 2]))
                first = abs(frac_dot(n1, n2b))
                second_squared = frac_dot(n1, n1) * frac_dot(n2b, n2b)
                second_numerator = abs(frac_dot(frac_cross(n1, n2b), frac_cross(n1, n2b)))

            first_exact = Exact.rational(first) / Exact.rational(second_squared).sqrt()
            if kind == "lineplane":
                # cos 由 sin 推：cos = √(1 - sin²)
                cosine = (Exact.rational(1) - first_exact * first_exact).sqrt()
                exact = (first_exact.format(), cosine.format())
            else:
                second_exact = (Exact.rational(second_numerator).sqrt()
                                / Exact.rational(second_squared).sqrt())
                exact = (first_exact.format(), second_exact.format())
        except Exception:                                  # noqa: BLE001
            exact = None

    if exact is not None:
        if by == "cos":
            info["cos"], info["sin"] = exact
        else:
            info["sin"], info["cos"] = exact
        info["exact"] = True
    elif info.get("nice") in NICE_ANGLES:
        info["sin"], info["cos"] = NICE_ANGLES[info["nice"]]
        info["exact"] = True
    else:
        info["exact"] = False
    return info


def build_solid(args):
    raw = args.get("shapes")
    if isinstance(raw, str):
        raw = [item for item in re.split(r"[\n;；]", raw) if item.strip()]
    if not isinstance(raw, list) or not raw:
        raise ExpressionError("没有要画的东西。" + SOLID_HELP)
    if len(raw) > MAX_SHAPES:
        raise ExpressionError("图形太多了（上限 %d 条）" % MAX_SHAPES)

    points = {}
    order = []
    edges = []
    faces = []
    angles = []
    curves = []          # 可命名的圆（曲面体的上下底、圆锥的底）
    surfaces = []        # 可加动点的曲面：圆柱侧面 / 圆锥侧面 / 球面

    hidden_points = set()

    def add_point(name, coords, hidden=False):
        if name not in points:
            order.append(name)
        points[name] = coords
        if hidden:
            # 曲面逼近出来的顶点：画面上不画点、不写名字，
            # 否则一个圆 36 个字母，图就糊了
            hidden_points.add(name)

    def add_circle(center_name, radius, axis, names):
        """登记一个圆：有名字、有圆心、有半径，前端才选得中。"""
        label = "⊙" + center_name + ("" if not curves else str(len(curves) + 1))
        while any(item["name"] == label for item in curves):
            label += "'"
        curves.append({"name": label, "kind": "circle", "at": center_name,
                       "center": list(points[center_name]), "radius": radius,
                       "axis": list(axis), "ring": names})
        return label

    def find_circle(label):
        for item in curves:
            if item["name"] == label:
                return item
        return None

    def find_surface(circle_labels):
        """按圆找它所属的曲面（圆柱侧面 / 圆锥侧面）。"""
        for item in surfaces:
            if item["kind"] in ("cylinder", "cone") and \
                    set(item["circles"]) & set(circle_labels):
                return item
        return None

    def find_sphere(center_name):
        for item in surfaces:
            if item["kind"] == "sphere" and item["at"] == center_name:
                return item
        return None

    def _circle_ring(center_point, radius_value, normal_unit):
        """在一个平面上摆一圈隐藏顶点，并把边连起来，返回名字列表。"""
        base = _perpendicular(normal_unit)
        other = _cross3(normal_unit, base)
        names = []
        for index in range(ROUND_SEGMENTS):
            angle = 2 * math.pi * index / ROUND_SEGMENTS
            candidate = free_name("p")
            add_point(candidate, tuple(
                center_point[axis] + radius_value * (math.cos(angle) * base[axis]
                                                     + math.sin(angle) * other[axis])
                for axis in range(3)), hidden=True)
            names.append(candidate)
        for index in range(ROUND_SEGMENTS):
            edges.append([names[index], names[(index + 1) % ROUND_SEGMENTS]])
        return names

    def need(name, what):
        if name not in points:
            raise ExpressionError("%s里面用到了没定义的点 %s，先写 %s(x,y,z)" % (what, name, name))
        return points[name]

    def free_name(base):
        candidate = base + "1"
        suffix = 1
        while candidate in points:
            suffix += 1
            candidate = base + str(suffix)
        return candidate

    for item in raw:
        line = str(item or "").strip()
        if not line:
            continue
        parsed = _parse_point3(line)
        if parsed:
            name, coords = parsed
            if any(abs(value) > MAX_COORD for value in coords):
                raise ExpressionError("点 %s 的坐标超出可画范围" % name)
            add_point(name, coords)
            continue
        parts = line.replace("，", " ").replace(",", " ").split()
        if not parts:
            continue
        word = parts[0].lower()
        rest = parts[1:]
        if word in SEGMENT_WORDS:
            if len(rest) != 2:
                raise ExpressionError("「%s」需要两个点：%s" % (line, SOLID_HELP))
            need(rest[0], "棱")
            need(rest[1], "棱")
            edges.append([rest[0], rest[1]])
        elif word in FACE_WORDS:
            if len(rest) < 3:
                raise ExpressionError("面至少要三个点：%s" % SOLID_HELP)
            coords = [need(name, "面") for name in rest]
            if not _is_coplanar(coords):
                raise ExpressionError("这几个点不共面，画不出一个平面：%s。"
                                      "要画立体的话请把每个面分开写" % " ".join(rest))
            faces.append(rest)
        elif word in CYLINDER_WORDS or word in CONE_WORDS or word in SPHERE_WORDS:
            is_sphere = word in SPHERE_WORDS
            is_cone = word in CONE_WORDS
            want = "球" if is_sphere else ("圆锥" if is_cone else "圆柱")
            if is_sphere:
                if len(rest) != 2:
                    raise ExpressionError("「%s」要写成「球心 半径」：%s" % (line, SOLID_HELP))
                try:
                    radius = float(rest[1])
                except ValueError:
                    # 不是数字就是点名：球心到那个点的距离当半径
                    edge = need(rest[1], "球面上的点")
                    radius = math.sqrt(sum(
                        (edge[index] - points[rest[0]][index]) ** 2 for index in range(3)))
                    if radius < 1e-9:
                        raise ExpressionError("那个点就是球心，定不出球")
                height = 0.0
            else:
                if len(rest) != 3:
                    raise ExpressionError("「%s」要写成「底面圆心 半径 高」：%s"
                                          % (line, SOLID_HELP))
                try:
                    radius = float(rest[1])
                    height = float(rest[2])
                except ValueError:
                    raise ExpressionError("「%s」的半径和高都要写数字" % line)
                if height <= 0:
                    raise ExpressionError("%s的高要大于 0" % want)
            if radius <= 0:
                raise ExpressionError("%s的半径要大于 0" % want)
            center = need(rest[0], "球心" if is_sphere else "底面圆心")

            def ring(z_offset, radius_value, normal_axis=None):
                """生成一圈顶点（hidden），返回名字列表。"""
                names = []
                for index in range(ROUND_SEGMENTS):
                    angle = 2 * math.pi * index / ROUND_SEGMENTS
                    candidate = free_name("p")
                    if normal_axis == "x":
                        coords = (center[0],
                                  center[1] + radius_value * math.cos(angle),
                                  center[2] + radius_value * math.sin(angle))
                    elif normal_axis == "y":
                        coords = (center[0] + radius_value * math.cos(angle),
                                  center[1],
                                  center[2] + radius_value * math.sin(angle))
                    else:
                        coords = (center[0] + radius_value * math.cos(angle),
                                  center[1] + radius_value * math.sin(angle),
                                  center[2] + z_offset)
                    add_point(candidate, coords, hidden=True)
                    names.append(candidate)
                # 这里不要再连边：调用处已经 connect 了，
                # 两边都做等于每圈加两遍棱（圆柱曾经有 108 条棱）
                return names

            def connect(names):
                for index in range(ROUND_SEGMENTS):
                    edges.append([names[index], names[(index + 1) % ROUND_SEGMENTS]])

            if is_sphere:
                # 球：赤道 + 两条经线大圆，课本上就是这么画的
                for axis in (None, "x", "y"):
                    names = ring(0.0, radius, axis)
                    connect(names)
                    # 球画的是线框：那几个大圆**不是**面，
                    # 当成面会让表面积算出一个错数（36π 变成 84 那种）
                    add_circle(rest[0], radius, (0, 0, 1) if axis is None
                               else ((1, 0, 0) if axis == "x" else (0, 1, 0)), names)
                surfaces.append({"kind": "sphere", "at": rest[0],
                                 "center": list(center), "radius": radius,
                                 "circles": [item["name"] for item in curves[-3:]]})
                continue

            bottom = ring(0.0, radius)
            connect(bottom)
            faces.append(bottom)
            add_circle(rest[0], radius, (0, 0, 1), bottom)
            if is_cone:
                apex = free_name("p")
                add_point(apex, (center[0], center[1], center[2] + height), hidden=True)
                # 不画 36 条母线：看着像笼子。课本上的圆锥就是底圆 + 半透明侧面
                for index in range(ROUND_SEGMENTS):
                    faces.append([bottom[index], bottom[(index + 1) % ROUND_SEGMENTS], apex])
                # 母线同理：36 条母线画出来也像笼子
                edges[:] = [pair for pair in edges
                            if apex not in pair]
                surfaces.append({"kind": "cone", "at": rest[0], "apex": apex,
                                 "apexAt": [center[0], center[1], center[2] + height],
                                 "radius": radius, "height": height,
                                 "circles": [curves[-1]["name"]]})
            else:
                top = ring(height, radius)
                connect(top)          # 上底那圈
                faces.append(top)
                # 上底圆**必须有自己的圆心**：以前两个圆共用底面中心 O，
                # 于是"在上圆加动点"会掉到下圆上（老师反馈过）
                top_center = free_name("O")
                add_point(top_center, (center[0], center[1], center[2] + height),
                          hidden=True)
                add_circle(top_center, radius, (0, 0, 1), top)
                for index in range(ROUND_SEGMENTS):
                    faces.append([bottom[index], bottom[(index + 1) % ROUND_SEGMENTS],
                                  top[(index + 1) % ROUND_SEGMENTS], top[index]])
                # 侧面那 36 条竖棱不画：课本上的圆柱是上下两个椭圆 + 半透明侧面，
                # 画上竖棱就成了笼子。只剔"连接上下两圈"的棱，圆上的棱一条不动。
                low = set(bottom)
                high = set(top)
                edges[:] = [pair for pair in edges
                            if not ((pair[0] in low and pair[1] in high)
                                    or (pair[1] in low and pair[0] in high))]
                surfaces.append({"kind": "cylinder", "at": rest[0],
                                 "radius": radius, "height": height,
                                 "circles": [item["name"] for item in curves[-2:]]})
        elif word in CIRCLE3_WORDS:
            if len(rest) != 3:
                raise ExpressionError("「%s」要写成「过三点的圆 A B C」：%s"
                                      % (line, SOLID_HELP))
            trio = [need(name, "圆") for name in rest]
            u = _sub3(trio[1], trio[0])
            v = _sub3(trio[2], trio[0])
            normal = _cross3(u, v)
            area2 = _dot3(normal, normal)
            if area2 < 1e-12:
                raise ExpressionError("这三个点在一条直线上，画不出圆")
            # 外接圆心：A + ((|u|²·v - |v|²·u) × n) / (2|n|²)
            # 外接圆心：w = ((|u|²·v - |v|²·u) × n) / (2|n|²)
            # 符号写反得到的是"对面那个点"，半径一样，所以光看半径发现不了
            diff = tuple(v[index] * _dot3(u, u) - u[index] * _dot3(v, v)
                         for index in range(3))
            center = tuple(trio[0][index] + _cross3(diff, normal)[index] / (2.0 * area2)
                           for index in range(3))
            radius = math.sqrt(sum((center[index] - trio[0][index]) ** 2
                                   for index in range(3)))
            unit = tuple(value / math.sqrt(area2) for value in normal)
            ring_names = _circle_ring(center, radius, unit)
            # 圆心也要登记成点（隐藏），add_circle 会去查它
            label = free_name("c")
            add_point(label, center, hidden=True)
            add_circle(label, radius, unit, ring_names)
        elif word in ONCIRCLE_WORDS:
            if len(rest) != 3:
                raise ExpressionError("「%s」要写成「点 圆 角度」：%s" % (line, SOLID_HELP))
            target = find_circle(rest[1])
            if target is None:
                raise ExpressionError("没有叫 %s 的圆：%s" % (rest[1], SOLID_HELP))
            angle = math.radians(float(rest[2]))
            axis = target["axis"]
            base = _perpendicular(axis)
            other = _cross3(axis, base)
            center = target["center"]
            radius = target["radius"]
            add_point(rest[0], tuple(
                center[index] + radius * (math.cos(angle) * base[index]
                                          + math.sin(angle) * other[index])
                for index in range(3)))
        elif word in ONSURFACE_WORDS:
            if len(rest) != 4:
                raise ExpressionError("「%s」要写成「点 圆 角度 比例」：%s" % (line, SOLID_HELP))
            target = find_surface([rest[1]])
            if target is None:
                raise ExpressionError("没有以 %s 为底的圆柱/圆锥：%s" % (rest[1], SOLID_HELP))
            angle = math.radians(float(rest[2]))
            ratio = float(rest[3])
            if not 0.0 <= ratio <= 1.0:
                raise ExpressionError("侧面上的比例要在 0 到 1 之间")
            base_circle = find_circle(target["circles"][0])
            center = base_circle["center"]
            radius = base_circle["radius"]
            base = _perpendicular((0, 0, 1))
            other = _cross3((0, 0, 1), base)
            if target["kind"] == "cone":
                apex = target["apexAt"]
                spot = [center[index] + (apex[index] - center[index]) * ratio
                        for index in range(3)]
                span = radius * (1.0 - ratio)
            else:
                spot = [center[0], center[1], center[2] + target["height"] * ratio]
                span = radius
            add_point(rest[0], tuple(
                spot[index] + span * (math.cos(angle) * base[index]
                                      + math.sin(angle) * other[index])
                for index in range(3)))
        elif word in ONSPHERE_WORDS:
            if len(rest) != 4:
                raise ExpressionError("「%s」要写成「点 球心 经度 纬度」：%s" % (line, SOLID_HELP))
            target = find_sphere(rest[1])
            if target is None:
                raise ExpressionError("没有以 %s 为球心的球：%s" % (rest[1], SOLID_HELP))
            lon = math.radians(float(rest[2]))
            lat = math.radians(float(rest[3]))
            center = target["center"]
            radius = target["radius"]
            add_point(rest[0], (
                center[0] + radius * math.cos(lat) * math.cos(lon),
                center[1] + radius * math.cos(lat) * math.sin(lon),
                center[2] + radius * math.sin(lat)))
        elif word in SECTION_WORDS:
            if len(rest) != 4:
                raise ExpressionError("「%s」要写成「球心 面上三点」：%s" % (line, SOLID_HELP))
            target = find_sphere(rest[0])
            if target is None:
                raise ExpressionError("没有以 %s 为球心的球：%s" % (rest[0], SOLID_HELP))
            trio = [need(name, "截面") for name in rest[1:]]
            normal = _cross3(_sub3(trio[1], trio[0]), _sub3(trio[2], trio[0]))
            length = math.sqrt(_dot3(normal, normal))
            if length < 1e-9:
                raise ExpressionError("这三个点在一条直线上，定不出截面")
            normal = tuple(value / length for value in normal)
            center = target["center"]
            offset = _dot3(normal, _sub3(trio[0], center))
            squared = target["radius"] ** 2 - offset ** 2
            if squared <= 1e-12:
                raise ExpressionError("这个平面跟球不相交（差得有点远）")
            circle_center = tuple(center[index] + normal[index] * offset
                                  for index in range(3))
            radius = math.sqrt(squared)
            name = free_name("c")
            add_point(name, circle_center, hidden=True)
            ring_names = []
            base = _perpendicular(normal)
            other = _cross3(normal, base)
            for index in range(ROUND_SEGMENTS):
                angle = 2 * math.pi * index / ROUND_SEGMENTS
                candidate = free_name("p")
                add_point(candidate, tuple(
                    circle_center[axis] + radius * (math.cos(angle) * base[axis]
                                                    + math.sin(angle) * other[axis])
                    for axis in range(3)), hidden=True)
                ring_names.append(candidate)
            for index in range(ROUND_SEGMENTS):
                edges.append([ring_names[index], ring_names[(index + 1) % ROUND_SEGMENTS]])
            label = add_circle(name, radius, normal, ring_names)
            surfaces.append({"kind": "section", "at": rest[0], "circle": label,
                             "radius": radius, "center": list(circle_center)})
        elif word in PRISM_WORDS or word in BOX_WORDS:
            if len(rest) < 4:
                raise ExpressionError("「%s」要写成「底面各点 + 高」：%s" % (line, SOLID_HELP))
            try:
                height = float(rest[-1])
            except ValueError:
                raise ExpressionError("「%s」最后要写高度（数字）" % line)
            base = rest[:-1]
            if word in BOX_WORDS and len(base) != 4:
                raise ExpressionError("长方体底面要四个点：%s" % SOLID_HELP)
            base_coords = [need(name, "底面") for name in base]
            if not _is_coplanar(base_coords):
                raise ExpressionError("底面这几个点不共面：%s" % " ".join(base))
            normal = _face_normal(base_coords)
            if normal is None:
                raise ExpressionError("底面这些点共线了，立不起来")
            top_names = []
            for index, name in enumerate(base):
                candidate = free_name(name)
                coords = base_coords[index]
                add_point(candidate, (coords[0] + normal[0] * height,
                                      coords[1] + normal[1] * height,
                                      coords[2] + normal[2] * height))
                top_names.append(candidate)
                edges.append([name, candidate])
            faces.append(list(base))
            faces.append(top_names)
            for index in range(len(base)):
                nxt = (index + 1) % len(base)
                faces.append([base[index], base[nxt], top_names[nxt], top_names[index]])
        elif word in PYRAMID_WORDS:
            if len(rest) < 4:
                raise ExpressionError("「%s」要写成「底面各点 + 高」：%s" % (line, SOLID_HELP))
            try:
                height = float(rest[-1])
            except ValueError:
                raise ExpressionError("「%s」最后要写高度（数字）" % line)
            base = rest[:-1]
            base_coords = [need(name, "底面") for name in base]
            if not _is_coplanar(base_coords):
                raise ExpressionError("底面这几个点不共面：%s" % " ".join(base))
            normal = _face_normal(base_coords)
            if normal is None:
                raise ExpressionError("底面这些点共线了，立不起来")
            center = [sum(coords[i] for coords in base_coords) / len(base_coords)
                      for i in range(3)]
            apex = free_name("S")
            add_point(apex, tuple(center[i] + normal[i] * height for i in range(3)))
            faces.append(list(base))
            for index in range(len(base)):
                nxt = (index + 1) % len(base)
                faces.append([base[index], base[nxt], apex])
                edges.append([base[index], apex])
        elif word in LINEANGLE_WORDS + LINEPLANE_WORDS + PLANEANGLE_WORDS:
            kind = ("lineangle" if word in LINEANGLE_WORDS
                    else "lineplane" if word in LINEPLANE_WORDS else "planeangle")
            if "|" in rest:
                # 显式分组：面可以整圈选（几个点都行），所以得知道从哪儿断开
                cut = rest.index("|")
                groups = [rest[:cut], rest[cut + 1:]]
                if len(groups[0]) < 2 or len(groups[1]) < 2:
                    raise ExpressionError("「|」两边都要有点：%s" % line)
            else:
                need_count = (4 if kind == "lineangle"
                              else 5 if kind == "lineplane" else 6)
                if len(rest) != need_count:
                    raise ExpressionError("「%s」要写 %d 个点（或用 | 分成两组）：%s"
                                          % (line, need_count, SOLID_HELP))
                split = 2 if kind == "lineangle" else 3
                groups = [rest[:split], rest[split:]]
            flat = [name for group in groups for name in group]
            for name in flat:
                need(name, "角")
            first_is_face = kind == "planeangle" or (kind == "lineangle" and False)
            if kind == "planeangle" and (len(groups[0]) < 3 or len(groups[1]) < 3):
                raise ExpressionError("面面角每个面至少三个点：%s" % line)
            if kind == "lineplane" and (len(groups[0]) < 2 or len(groups[1]) < 3):
                raise ExpressionError("线面角要一条直线（2 个点）加一个平面（至少 3 个点）：%s"
                                      % line)
            if kind == "lineangle" and (len(groups[0]) < 2 or len(groups[1]) < 2):
                raise ExpressionError("线线角每条线两个点：%s" % line)
            angles.append({"type": kind, "names": flat, "groups": groups})
            del first_is_face
        else:
            raise ExpressionError("看不懂这一条：%s。%s" % (line, SOLID_HELP))

    if not points:
        raise ExpressionError("没有定义任何点。" + SOLID_HELP)

    # 面里的棱也要画出来：老师看到的"棱"就是这些
    known = set()
    for edge in edges:
        known.add((edge[0], edge[1]))
        known.add((edge[1], edge[0]))
    for face in faces:
        for index in range(len(face)):
            nxt = (index + 1) % len(face)
            pair = (face[index], face[nxt])
            if pair not in known:
                known.add(pair)
                known.add((pair[1], pair[0]))
                edges.append([pair[0], pair[1]])

    want_exact = args.get("exact", True) is not False
    for shape in angles:
        try:
            info = _solid_angle_info(points, shape, want_exact)
        except Exception as error:                          # noqa: BLE001
            # 兜底：一个角算不出来，不能让整张图都画不出来
            info = {"error": "这个角暂时算不出来（%s）" % error}
        if info:
            shape.update(info)

    coords = [points[name] for name in order]
    xs = [point[0] for point in coords]
    ys = [point[1] for point in coords]
    zs = [point[2] for point in coords]
    return {
        "kind": "solid",
        "title": str(args.get("title") or "").strip(),
        "note": str(args.get("note") or "").strip(),
        "points": [{"name": name, "x": round(points[name][0], 4),
                    "y": round(points[name][1], 4),
                    "z": round(points[name][2], 4),
                    "hidden": 1 if name in hidden_points else 0} for name in order],
        "edges": edges,
        "faces": faces,
        "angles": angles,
        "curves": curves,
        "surfaces": surfaces,
        "bbox": [min(xs), min(ys), min(zs), max(xs), max(ys), max(zs)],
        "source": [str(item).strip() for item in raw if str(item or "").strip()],
        "measures": solid_measures(points, faces),
    }


def build(args):
    """统一入口：kind=function 画函数图，kind=equation 画方程，kind=geometry 画几何图形。

    返回 (payload, 错误消息)；成功时错误消息是空串。
    """
    args = args or {}
    kind = str(args.get("kind") or "function").strip().lower()
    try:
        if kind in ("geometry", "geo", "几何", "几何画板"):
            return build_geometry(args), ""
        if kind in ("solid", "space", "立体", "空间", "空间几何"):
            return build_solid(args), ""
        if kind in ("equation", "方程"):
            text = str(args.get("expression") or "").strip()
            if _looks_parametric(text):
                return build_parametric(args), ""
            if _looks_polar(text):
                return build_polar(args), ""
            return build_equation(args), ""
        return build_function(args), ""
    except ExpressionError as exc:
        return None, str(exc)
    except (ValueError, TypeError, ZeroDivisionError, OverflowError) as exc:
        return None, "画不出来：%s" % exc


# --------------------------------------------------------------------------
# 方程（隐函数曲线）：把 F(x,y)=0 画出来
# --------------------------------------------------------------------------
EQUATION_GRID = 160          # 网格密度：160×160 在这个尺寸下看不出折线
MAX_CONTOUR_SEGMENTS = 6000
PARAMETRIC_SAMPLES = 900     # 参数曲线和极坐标曲线的采样点数
POLAR_SAMPLES = 900


def _split_equation(text):
    """把 "x^2 + y^2 = 4" 拆成左右两边。"""
    text = str(text or "").strip()
    for symbol in ("==", "="):
        if symbol in text:
            left, right = text.split(symbol, 1)
            if left.strip() and right.strip():
                return left.strip(), right.strip()
    raise ExpressionError("方程要写成「左边 = 右边」，例如 x^2 + y^2 = 4")


# 参数方程：x = ... , y = ...（逗号/分号/换行隔开都认）
_PARAMETRIC = re.compile(r"(?is)^\s*x\s*=\s*(.+?)\s*[,，;；\n]\s*y\s*=\s*(.+?)\s*$")
# 极坐标：r = ...
_POLAR = re.compile(r"(?is)^\s*r\s*=\s*(.+)$")
# θ 和 t 都当参数用（老师在纸上写 θ，在键盘上打 theta 或 t）
THETA_NAMES = ("theta", "θ", "t", "u")


def _looks_parametric(text):
    return _PARAMETRIC.match(text or "") is not None


def _looks_polar(text):
    if _looks_parametric(text):
        return False
    if not _POLAR.match(text or ""):
        return False
    # "r = ..." 才算极坐标；"r^2 = ..." 这种先不认，老老实实当隐函数
    return text.strip().lower().startswith("r")


def _sample_curve(point_of, t_min, t_max, count):
    """按参数采样出一条（或几条）折线；断了的地方拆开。"""
    segments = []
    current = []
    previous = None
    for index in range(count):
        t = t_min + (t_max - t_min) * index / (count - 1.0)
        try:
            x, y = point_of(t)
            x = float(x)
            y = float(y)
        except Exception:                                  # noqa: BLE001
            x = y = None
        if x is None or not math.isfinite(x) or not math.isfinite(y) \
                or abs(x) > MAX_COORD or abs(y) > MAX_COORD:
            if len(current) > 1:
                segments.append(current)
            current = []
            previous = None
            continue
        if previous is not None:
            jump = max(abs(x - previous[0]), abs(y - previous[1]))
            if jump > (t_max - t_min) * 0.5 + 1.0:
                # 参数跳了一大截（tan 那种），别连成一条直线
                if len(current) > 1:
                    segments.append(current)
                current = []
        current.append([round(x, 4), round(y, 4)])
        previous = (x, y)
    if len(current) > 1:
        segments.append(current)
    # 四舍五入之后再清一次重复点：两个原始点可能舍成同一个点
    return [_round_line(line) for line in segments if len(_round_line(line)) > 1]


def build_parametric(args):
    """参数方程：x = cos(t), y = sin(t)。t 是参数。"""
    text = str(args.get("expression") or "").strip()
    match = _PARAMETRIC.match(text)
    if not match:
        raise ExpressionError("参数方程要写成「x = …, y = …」，例如 x = cos(t), y = sin(t)")
    # t 用第一个参数位（跟 x 同一格）：这样参数式里写 t 或者写 x 都能算
    x_of = _compile(_normalize_parameter(_parse_node(match.group(1))))
    y_of = _compile(_normalize_parameter(_parse_node(match.group(2))))

    x_min, x_max, y_min, y_max = _equation_range(args)
    t_min, t_max = _parameter_range(args, x_min, x_max)
    segments = _sample_curve(lambda t: (x_of(t), y_of(t)), t_min, t_max, PARAMETRIC_SAMPLES)
    if not segments:
        raise ExpressionError("这条参数曲线在给定范围里画不出来（检查 t 的范围）")
    return _curve_payload("parametric", text, args, x_min, x_max, y_min, y_max, segments)


def build_polar(args):
    """极坐标方程：r = 1 + cos(θ)。θ 从 0 到 2π。"""
    text = str(args.get("expression") or "").strip()
    match = _POLAR.match(text)
    if not match:
        raise ExpressionError("极坐标方程要写成「r = …」，例如 r = 1 + cos(theta)")
    r_of = _compile(_normalize_parameter(_parse_node(match.group(1))))

    x_min, x_max, y_min, y_max = _equation_range(args)
    t_min, t_max = _parameter_range(args, 0.0, 2 * math.pi, polar=True)
    segments = _sample_curve(lambda t: (r_of(t) * math.cos(t), r_of(t) * math.sin(t)),
                             t_min, t_max, POLAR_SAMPLES)
    if not segments:
        raise ExpressionError("这条极坐标曲线在给定范围里画不出来")
    return _curve_payload("polar", text, args, x_min, x_max, y_min, y_max, segments)


def _normalize_parameter(node):
    """把 theta / θ / u 都换成 t（它们在解析树里是不同的名字，编译时统一走第一个参数位）。"""
    if not isinstance(node, tuple):
        return node
    if node[0] == "theta":
        return ("t",)
    return tuple(_normalize_parameter(item) if isinstance(item, tuple) else item
                 for item in node)


def _parameter_range(args, default_min, default_max, polar=False):
    try:
        low = float(args.get("t_min", default_min))
        high = float(args.get("t_max", default_max))
    except (TypeError, ValueError):
        low, high = default_min, default_max
    if not math.isfinite(low) or not math.isfinite(high) or high <= low:
        low, high = default_min, default_max
    return low, high


def _curve_payload(kind, text, args, x_min, x_max, y_min, y_max, segments):
    return {
        "kind": kind,
        "title": str(args.get("title") or "").strip(),
        "note": str(args.get("note") or "").strip(),
        "label": text,
        "expression": text,
        "x_min": round(x_min, 4), "x_max": round(x_max, 4),
        "y_min": round(y_min, 4), "y_max": round(y_max, 4),
        "segments": segments,
        "bbox": _curve_bbox(segments),
        "derivative": "",
        "derivativeError": "",
        "extrema": [],
    }


def _point_key(point, tolerance):
    return (int(round(point[0] / tolerance)), int(round(point[1] / tolerance)))


def _marching_squares(field, xs, ys):
    """把 F(x,y)=0 的等值线抠出来（marching squares）。

    每个格子里看四个角的正负号，在变号的边上线性插值出交点。
    """
    rows = len(ys)
    columns = len(xs)
    segments = []
    for row in range(rows - 1):
        for column in range(columns - 1):
            x0, x1 = xs[column], xs[column + 1]
            y0, y1 = ys[row], ys[row + 1]
            v00 = field[row][column]
            v10 = field[row][column + 1]
            v11 = field[row + 1][column + 1]
            v01 = field[row + 1][column]
            if v00 is None or v10 is None or v11 is None or v01 is None:
                continue
            corners = ((v00, x0, y0), (v10, x1, y0), (v11, x1, y1), (v01, x0, y1))
            crossings = []
            for first, second in ((0, 1), (1, 2), (2, 3), (3, 0)):
                value_a, ax, ay = corners[first]
                value_b, bx, by = corners[second]
                if (value_a < 0) == (value_b < 0):
                    continue
                span = value_b - value_a
                ratio = 0.5 if abs(span) < 1e-15 else (0.0 - value_a) / span
                crossings.append((ax + (bx - ax) * ratio, ay + (by - ay) * ratio))
            if len(crossings) == 2:
                segments.append((crossings[0], crossings[1]))
            elif len(crossings) == 4:
                center = (v00 + v10 + v01 + v11) / 4.0
                if center >= 0:
                    segments.append((crossings[0], crossings[1]))
                    segments.append((crossings[2], crossings[3]))
                else:
                    segments.append((crossings[0], crossings[3]))
                    segments.append((crossings[1], crossings[2]))
            if len(segments) >= MAX_CONTOUR_SEGMENTS:
                return segments
    return segments


def _chain_segments(segments, tolerance=1e-5):
    """把首尾相接的线段串成折线。串起来前端才能"沿着曲线拖点"，也才不会画成一段段断的。

    ⚠ 接尾巴的时候必须拿**尾巴**去比对。之前这里图省事一直用 line[0]（头）比对，
    结果把错误的端点接了进来：折线里出现重复点、本该连成一条的曲线碎成几十段
    （老师反馈"方程永远是断开的"，就是这个）。
    """
    buckets = {}
    for index, (start, end) in enumerate(segments):
        buckets.setdefault(_point_key(start, tolerance), []).append(index)
        buckets.setdefault(_point_key(end, tolerance), []).append(index)
    used = [False] * len(segments)
    lines = []
    for index in range(len(segments)):
        if used[index]:
            continue
        used[index] = True
        line = [segments[index][0], segments[index][1]]
        for at_head in (False, True):
            while True:
                tip_key = _point_key(line[0] if at_head else line[-1], tolerance)
                found = None
                for candidate in buckets.get(tip_key, ()):
                    if not used[candidate]:
                        found = candidate
                        break
                if found is None:
                    break
                used[found] = True
                a, b = segments[found]
                if _point_key(a, tolerance) == tip_key:
                    joined = b
                elif _point_key(b, tolerance) == tip_key:
                    joined = a
                else:                                      # 桶是按端点建的，走不到这里
                    joined = a
                if at_head:
                    line.insert(0, joined)
                else:
                    line.append(joined)
        lines.append(line)
    return lines


def _curve_bbox(segments):
    """曲线实际占的范围。前端拿它来决定一开始的取景（老师要求"别画得太小"）。"""
    xs = [point[0] for line in segments for point in line]
    ys = [point[1] for line in segments for point in line]
    if not xs:
        return None
    return [min(xs), min(ys), max(xs), max(ys)]


def _drop_repeats(line):
    """去掉挨在一起的重复点：零长度线段画出来是断口，也让路径变长。

    ⚠ 必须在**四舍五入之后**再去重：两个不同的原始点舍到 3 位小数可能变成同一个点，
    先去掉再舍入等于白做（真踩过）。
    """
    out = []
    for point in line:
        if not out or point != out[-1]:
            out.append(point)
    return out


def _round_line(line):
    return _drop_repeats([[round(point[0], 3), round(point[1], 3)] for point in line])


def _equation_range(args):
    try:
        x_min = float(args.get("x_min", DEFAULT_X_MIN))
        x_max = float(args.get("x_max", DEFAULT_X_MAX))
        y_min = float(args.get("y_min", DEFAULT_X_MIN))
        y_max = float(args.get("y_max", DEFAULT_X_MAX))
    except (TypeError, ValueError):
        raise ExpressionError("横轴/纵轴范围要是数字")
    for name, value in (("x_min", x_min), ("x_max", x_max),
                        ("y_min", y_min), ("y_max", y_max)):
        if not math.isfinite(value) or abs(value) > MAX_COORD:
            raise ExpressionError("%s 超出可画范围" % name)
    if x_max <= x_min:
        x_min, x_max = min(x_min, x_max), max(x_min, x_max)
    if y_max <= y_min:
        y_min, y_max = min(y_min, y_max), max(y_min, y_max)
    if x_max - x_min < 1e-6 or y_max - y_min < 1e-6:
        raise ExpressionError("画图范围太小了")
    return x_min, x_max, y_min, y_max


def build_equation(args):
    """画方程（隐函数）曲线，例如 x^2 + y^2 = 4、x^2/4 - y^2/9 = 1。"""
    text = str(args.get("expression") or "").strip()
    left, right = _split_equation(text)
    difference = _compile(("bin", "-", _parse_node(left), _parse_node(right)))

    x_min, x_max, y_min, y_max = _equation_range(args)
    count = EQUATION_GRID
    xs = [x_min + (x_max - x_min) * index / (count - 1.0) for index in range(count)]
    ys = [y_min + (y_max - y_min) * index / (count - 1.0) for index in range(count)]
    field = []
    for y in ys:
        row = []
        for x in xs:
            try:
                value = float(difference(x, y))
            except Exception:                              # noqa: BLE001
                value = None
            else:
                if not math.isfinite(value) or abs(value) > MAX_COORD:
                    value = None
            row.append(value)
        field.append(row)

    raw = _marching_squares(field, xs, ys)
    if not raw:
        raise ExpressionError("这个方程在这块范围里画不出曲线"
                              "（检查一下写法，或者把范围调大一点）")
    segments = []
    for line in _chain_segments(raw):
        line = _round_line(line)
        if len(line) < 2:
            continue
        segments.append(line)
    if not segments:
        raise ExpressionError("这个方程在这块范围里画不出曲线")

    return {
        "kind": "equation",
        "title": str(args.get("title") or "").strip(),
        "note": str(args.get("note") or "").strip(),
        "label": text,
        "expression": text,
        "x_min": round(x_min, 4), "x_max": round(x_max, 4),
        "y_min": round(y_min, 4), "y_max": round(y_max, 4),
        "segments": segments,
        "bbox": _curve_bbox(segments),
        # 方程不给导数和积分：隐函数的一阶导是 -F_x/F_y，老师那个场景用不上，
        # 多两块卡片反而要多解释
        "derivative": "",
        "derivativeError": "",
        "extrema": [],
    }
