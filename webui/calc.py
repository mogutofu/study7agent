# -*- coding: utf-8 -*-
"""计算器 + 单位换算的引擎。

三件事：
  1. **精确算术**：``Exact`` 表示"若干个 coef * sqrt(rad) 之和"，
     所以 1/3、√2、2√3/3、(1+√5)/2 这些都能精确算、精确显示；
  2. **表达式求值**：自己写的小解析器（不用 eval），支持 + - * / ^ % ! 括号和常用函数；
  3. **单位换算**：表在 ``units.py``（215 个单位 / 29 个物理量），带搜索。

几何那边的线线角 / 线面角 / 二面角也用这里的 ``Exact`` 化成分数根号，
所以这个模块不依赖界面，可以单独测。
"""
import math
import re
from fractions import Fraction

import units as units_module

MAX_TERMS = 64                     # 精确值最多几项，防止爆炸
MAX_TEXT = 200


# --------------------------------------------------------------------------
# 精确值：sum(coef * sqrt(rad))，rad 是无平方因子的非负整数（1 表示有理项）
# --------------------------------------------------------------------------
# 试除法最多跑到 sqrt(这个数)：再大就不分解了
# （动点拖动会带来 2.92 这种坐标，还原成分数后分子×分母可以大到几亿，
#   完整试除要跑上千万次 —— 请求永远不返回，表现成"角一动就消失"）
SQUAREFREE_LIMIT = 10 ** 8


def _squarefree(value):
    """把 sqrt(value) 拆成 k * sqrt(m)，m 无平方因子。返回 (k, m)。

    太大的数直接放弃分解（返回 1, value）。宁可不化简，也**绝不能卡住**：
    化简失败只是显示得难看，卡住是整个界面死在那一帧。
    """
    value = int(value)
    if value <= 0:
        return 1, 1
    if value > SQUAREFREE_LIMIT:
        return 1, value
    k = 1
    m = value
    factor = 2
    while factor * factor <= m:
        while m % (factor * factor) == 0:
            m //= factor * factor
            k *= factor
        factor += 1 if factor == 2 else 2
    return k, m


class Exact(object):
    """精确值。内部就是 {无平方因子: 系数}，例如 1/2 + √2 是 {1: 1/2, 2: 1}。"""

    __slots__ = ("terms",)

    def __init__(self, terms=None):
        self.terms = {}
        for rad, coef in (terms or {}).items():
            coef = Fraction(coef)
            if coef:
                self.terms[int(rad)] = coef

    # -- 构造 --------------------------------------------------------------
    @classmethod
    def rational(cls, value):
        return cls({1: Fraction(value)})

    @classmethod
    def radical(cls, rad, coef=1):
        """coef * sqrt(rad)，自动把 sqrt 里的平方因子提出来。"""
        k, m = _squarefree(rad)
        return cls({m: Fraction(coef) * k})

    @classmethod
    def from_float(cls, value):
        """浮点数转精确值：先试"漂亮的分数"，再试"整数倍根号"，都不行返回 None。"""
        if not math.isfinite(value):
            return None
        ratio = Fraction(value).limit_denominator(10000)
        if abs(float(ratio) - value) <= 1e-9 * max(1.0, abs(value)):
            return cls({1: ratio})
        for rad in range(2, 400):
            squared = value / math.sqrt(rad)
            ratio = Fraction(squared).limit_denominator(2000)
            if ratio and abs(float(ratio) * math.sqrt(rad) - value) <= 1e-9 * max(1.0, abs(value)):
                k, m = _squarefree(rad)
                return cls({m: ratio * k})
        return None

    # -- 运算 --------------------------------------------------------------
    def is_rational(self):
        return all(rad == 1 for rad in self.terms)

    def rational_value(self):
        return self.terms.get(1, Fraction(0))

    def __add__(self, other):
        return self._combine(other, 1)

    def __sub__(self, other):
        return self._combine(other, -1)

    def _combine(self, other, sign):
        other = as_exact(other)
        terms = dict(self.terms)
        for rad, coef in other.terms.items():
            terms[rad] = terms.get(rad, Fraction(0)) + sign * coef
        return Exact(terms)

    def __neg__(self):
        return Exact(dict((rad, -coef) for rad, coef in self.terms.items()))

    def __mul__(self, other):
        other = as_exact(other)
        if len(self.terms) * len(other.terms) > MAX_TERMS:
            raise ValueError("精确值太复杂了")
        terms = {}
        for rad_a, coef_a in self.terms.items():
            for rad_b, coef_b in other.terms.items():
                k, m = _squarefree(rad_a * rad_b)
                terms[m] = terms.get(m, Fraction(0)) + coef_a * coef_b * k
        return Exact(terms)

    def __truediv__(self, other):
        other = as_exact(other)
        if not other.terms:
            raise ZeroDivisionError("除以 0 了")
        if len(other.terms) == 1:
            rad, coef = list(other.terms.items())[0]
            if rad == 1:
                return self * Exact({1: 1 / coef})
            # a / (b√r) = a√r / (b r)
            return (self * Exact({rad: coef})) * Exact({1: Fraction(1, 1) / (coef * coef * rad)})
        # 两项分母：乘共轭把它有理化
        conjugate = Exact(dict((rad, coef if rad == 1 else -coef)
                               for rad, coef in other.terms.items()))
        denominator = other * conjugate
        if not denominator.is_rational() or not denominator.rational_value():
            raise ValueError("这个分母化不了简")
        return (self * conjugate) * Exact({1: 1 / denominator.rational_value()})

    def __pow__(self, exponent):
        exponent = int(exponent)
        if exponent < 0:
            return Exact.rational(1) / (self ** (-exponent))
        result = Exact.rational(1)
        base = self
        while exponent:
            if exponent & 1:
                result = result * base
            exponent >>= 1
            if exponent:
                base = base * base
        return result

    def sqrt(self):
        if self.is_rational():
            value = self.rational_value()
            if value < 0:
                raise ValueError("负数不能开平方（实数范围内）")
            if value == 0:
                # √0 = 0。不特判的话会走到 _squarefree(0) = (1, 1)，被当成 1
                # （线面角 90° 的 cos 就是这么错成 1 的）
                return Exact.rational(0)
            k, m = _squarefree(value.numerator)
            k2, m2 = _squarefree(value.denominator)
            # sqrt(n/d) = sqrt(n*d)/d
            k, m = _squarefree(value.numerator * value.denominator)
            return Exact.radical(m, Fraction(k, value.denominator))
        raise ValueError("这个数的平方根化不出来")

    def abs(self):
        return self if self.to_float() >= 0 else -self

    def to_float(self):
        total = 0.0
        for rad, coef in self.terms.items():
            total += float(coef) * math.sqrt(rad)
        return total

    def __eq__(self, other):
        try:
            return self.terms == as_exact(other).terms
        except (TypeError, ValueError):
            return NotImplemented

    def __repr__(self):
        return "Exact(%r)" % (format_exact(self),)

    # -- 显示 --------------------------------------------------------------
    def format(self):
        return format_exact(self)


def as_exact(value):
    if isinstance(value, Exact):
        return value
    if isinstance(value, (int, Fraction)):
        return Exact.rational(value)
    if isinstance(value, float):
        found = Exact.from_float(value)
        if found is None:
            raise ValueError("这个小数化不成分数或根号")
        return found
    raise ValueError("不能当精确值用")


def format_exact(value):
    """把精确值写成老师看得懂的样子：3/4、√2、2√3/3、(1+√5)/2。"""
    value = as_exact(value)
    if not value.terms:
        return "0"
    pieces = []
    for rad in sorted(value.terms):
        coef = value.terms[rad]
        if rad == 1:
            pieces.append(_format_rational(coef))
            continue
        root = "√%d" % rad
        if coef == 1:
            pieces.append(root)
        elif coef == -1:
            pieces.append("-" + root)
        elif coef.denominator == 1:
            pieces.append("%d%s" % (coef.numerator, root))
        elif coef.numerator == 1:
            # √2/2 而不是 1√2/2
            pieces.append("%s/%d" % (root, coef.denominator))
        elif coef.numerator == -1:
            pieces.append("-%s/%d" % (root, coef.denominator))
        else:
            pieces.append("%d%s/%d" % (coef.numerator, root, coef.denominator))
    text = ""
    for piece in pieces:
        if not text:
            text = piece
        elif piece.startswith("-"):
            text += " - " + piece[1:]
        else:
            text += " + " + piece
    return text


def _format_rational(value):
    value = Fraction(value)
    if value.denominator == 1:
        return str(value.numerator)
    return "%d/%d" % (value.numerator, value.denominator)


def decimal_text(value, digits=10):
    """小数显示：去掉多余的 0，整数就不带小数点。"""
    if isinstance(value, Exact):
        value = value.to_float()
    text = ("%." + str(digits) + "f") % value
    text = text.rstrip("0").rstrip(".")
    return text or "0"


# --------------------------------------------------------------------------
# 表达式求值（自己解析，不用 eval）
# --------------------------------------------------------------------------
_NUMBER = re.compile(r"\d+(?:\.\d+)?(?:[eE][+-]?\d+)?")
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

FUNCTIONS = {
    "sqrt": lambda v: v.sqrt(),
    "abs": lambda v: v.abs(),
    "sin": None, "cos": None, "tan": None,
    "asin": None, "acos": None, "atan": None,
    "ln": None, "log": None, "log10": None, "exp": None,
}
EXACT_TRIG_HELP = "特殊角（π/12 的整数倍）给精确值"

# sin 在 π/12 的整数倍上的精确值，k = 弧度 / (π/12)，只列 0..6，其余靠对称性推
_SIN_TABLE = {
    0: Exact.rational(0),
    1: (Exact.radical(6) - Exact.radical(2)) * Exact.rational(Fraction(1, 4)),   # 15°
    2: Exact.rational(Fraction(1, 2)),                                           # 30°
    3: Exact.radical(2, Fraction(1, 2)),                                         # 45°
    4: Exact.radical(3, Fraction(1, 2)),                                         # 60°
    5: (Exact.radical(6) + Exact.radical(2)) * Exact.rational(Fraction(1, 4)),   # 75°
    6: Exact.rational(1),                                                        # 90°
}


def _snap_pi(value):
    """把浮点弧度值认成 π 的简单有理数倍。

    `sin(pi/6)` 里的 pi 是浮点数，不认这一步就永远走不到精确路径——
    老师看到的是 0.5 而不是 1/2。
    """
    if not math.isfinite(value):
        return None
    if value == 0:
        return Fraction(0)
    ratio = Fraction(value / math.pi).limit_denominator(24)
    if abs(float(ratio) * math.pi - value) <= 1e-9 * max(1.0, abs(value)):
        return ratio
    return None


def sin_exact(ratio):
    """ratio 是"π 的多少倍"，返回精确的 sin 值；不是 π/12 的整数倍就返回 None。"""
    if ratio is None:
        return None
    step = Fraction(ratio) * 12
    if step.denominator != 1:
        return None
    k = int(step) % 24
    if k > 12:
        return -sin_exact(Fraction(k - 12, 12))
    if k in _SIN_TABLE:
        return _SIN_TABLE[k]
    if 6 < k < 12:
        return sin_exact(Fraction(12 - k, 12))
    return None


def cos_exact(ratio):
    if ratio is None:
        return None
    return sin_exact(Fraction(1, 2) - Fraction(ratio))


def tan_exact(ratio):
    sine, cosine = sin_exact(ratio), cos_exact(ratio)
    if sine is None or cosine is None or not cosine.terms:
        return None
    try:
        return sine / cosine
    except (ValueError, ZeroDivisionError):
        return None


def _tokenize(text):
    tokens = []
    index = 0
    while index < len(text):
        char = text[index]
        if char.isspace():
            index += 1
            continue
        if char.isdigit() or (char == "." and index + 1 < len(text) and text[index + 1].isdigit()):
            match = _NUMBER.match(text, index)
            tokens.append(("number", match.group(0)))
            index = match.end()
            continue
        if char.isalpha() or char == "_":
            match = _NAME.match(text, index)
            word = match.group(0)
            tokens.append(("name", word))
            index = match.end()
            continue
        if text.startswith("**", index):
            tokens.append(("op", "^"))
            index += 2
            continue
        if char in "+-*/^%!()√×÷":
            tokens.append(("op", {"×": "*", "÷": "/", "√": "sqrt"}.get(char, char)))
            index += 1
            continue
        raise ValueError("看不懂这个符号：%s" % char)
    return tokens


class _Calc(object):
    """递归下降求值。尽量用 Exact 算，算不动再退到浮点（并标记 approximate）。"""

    def __init__(self, tokens, exact=True, degrees=False):
        self.tokens = tokens
        self.pos = 0
        self.exact = exact
        self.degrees = degrees
        self.approximate = False

    def peek(self):
        return self.tokens[self.pos] if self.pos < len(self.tokens) else (None, None)

    def take(self):
        token = self.peek()
        self.pos += 1
        return token

    def expect(self, symbol):
        kind, value = self.take()
        if kind != "op" or value != symbol:
            raise ValueError("这里应该有一个 %s" % symbol)

    def parse(self):
        value = self.expression()
        if self.pos != len(self.tokens):
            raise ValueError("表达式后面多出来一段")
        return value

    def expression(self):
        value = self.term()
        while True:
            kind, symbol = self.peek()
            if kind == "op" and symbol in "+-":
                self.take()
                value = self._add(value, self.term(), symbol)
            else:
                return value

    def term(self):
        value = self.unary()
        while True:
            kind, symbol = self.peek()
            if kind == "op" and symbol in "*/%":
                self.take()
                value = self._mul(value, self.unary(), symbol)
            elif kind in ("number", "name") or (kind == "op" and symbol in "(√"):
                value = self._mul(value, self.unary(), "*")     # 隐式乘法 2π
            else:
                return value

    def unary(self):
        kind, symbol = self.peek()
        if kind == "op" and symbol in "+-":
            self.take()
            inner = self.unary()
            return inner if symbol == "+" else self._neg(inner)
        return self.power()

    def power(self):
        value = self.postfix()
        kind, symbol = self.peek()
        if kind == "op" and symbol == "^":
            self.take()
            exponent = self.unary()
            return self._pow(value, exponent)
        return value

    def postfix(self):
        value = self.primary()
        while True:
            kind, symbol = self.peek()
            if kind == "op" and symbol == "!":
                self.take()
                value = self._factorial(value)
            else:
                return value

    def primary(self):
        kind, value = self.take()
        if kind == "number":
            if "." in value or "e" in value or "E" in value:
                return float(value)
            return Fraction(int(value))
        if kind == "op" and value == "(":
            inner = self.expression()
            self.expect(")")
            return inner
        if kind == "name":
            lowered = value.lower()
            if lowered in ("pi", "π"):
                return math.pi
            if lowered == "e":
                return math.e
            if lowered in ("ans",):
                return self.ans if hasattr(self, "ans") else Fraction(0)
            if self.peek() == ("op", "(") and lowered in FUNCTIONS:
                self.take()
                argument = self.expression()
                self.expect(")")
                return self._call(lowered, argument)
            if lowered == "sqrt" and self.peek()[0] == "op":
                inner = self.unary()
                return self._call("sqrt", inner)
            raise ValueError("不认识的函数或名字：%s" % value)
        raise ValueError("表达式在这里断了，少了一项")

    # -- 各种运算：能精确就精确，不能就转浮点 ------------------------------
    def _to_float(self, value):
        if isinstance(value, Exact):
            return value.to_float()
        return float(value)

    def _exact_or_none(self, value):
        if isinstance(value, float):
            try:
                return as_exact(value)
            except ValueError:
                return None
        if isinstance(value, Fraction):
            return Exact.rational(value)
        return value if isinstance(value, Exact) else None

    def _add(self, left, right, symbol):
        a, b = self._exact_or_none(left), self._exact_or_none(right)
        if a is not None and b is not None:
            try:
                return a + b if symbol == "+" else a - b
            except ValueError:
                pass
        self.approximate = True
        result = self._to_float(left) + (self._to_float(right) if symbol == "+"
                                         else -self._to_float(right))
        return result

    def _mul(self, left, right, symbol):
        a, b = self._exact_or_none(left), self._exact_or_none(right)
        if symbol == "%":
            self.approximate = True
            return math.fmod(self._to_float(left), self._to_float(right))
        if a is not None and b is not None:
            try:
                return a * b if symbol == "*" else a / b
            except (ValueError, ZeroDivisionError) as error:
                if isinstance(error, ZeroDivisionError):
                    raise
        self.approximate = True
        return (self._to_float(left) * self._to_float(right) if symbol == "*"
                else self._to_float(left) / self._to_float(right))

    def _neg(self, value):
        exact = self._exact_or_none(value)
        return -exact if exact is not None else -self._to_float(value)

    def _pow(self, base, exponent):
        if isinstance(exponent, Fraction) and exponent.denominator == 1:
            power = int(exponent)
            exact = self._exact_or_none(base)
            if exact is not None and abs(power) <= 64:
                try:
                    return exact ** power
                except ValueError:
                    pass
            self.approximate = True
            return self._to_float(base) ** power
        self.approximate = True
        return self._to_float(base) ** self._to_float(exponent)

    def _factorial(self, value):
        number = self._to_float(value)
        if number < 0 or number != int(number) or number > 170:
            raise ValueError("阶乘只支持 0 到 170 的整数")
        return Fraction(math.factorial(int(number)))

    def _call(self, name, argument):
        if name == "sqrt":
            exact = self._exact_or_none(argument)
            if exact is not None:
                try:
                    return exact.sqrt()
                except ValueError:
                    pass
            self.approximate = True
            return math.sqrt(self._to_float(argument))
        if name == "abs":
            exact = self._exact_or_none(argument)
            return exact.abs() if exact is not None else abs(self._to_float(argument))
        if name in ("sin", "cos", "tan"):
            return self._trig(name, argument)
        self.approximate = True
        value = self._to_float(argument)
        table = {"asin": math.asin, "acos": math.acos, "atan": math.atan,
                 "ln": math.log, "log": math.log, "log10": math.log10, "exp": math.exp}
        try:
            return table[name](value)
        except ValueError as error:
            raise ValueError("这个函数在这个数上没有定义（%s）" % error)

    def _trig(self, name, argument):
        """特殊角给精确值，其余走浮点。

        角度默认按**弧度**；self.degrees 为真时按**度**（界面上那个 角度/弧度 开关）。
        """
        exact = self._exact_or_none(argument)
        ratio = None
        if exact is not None and exact.is_rational():
            raw = float(exact.rational_value())
            ratio = (Fraction(exact.rational_value()) / 180 if self.degrees
                     else _snap_pi(raw))
            if self.degrees and Fraction(exact.rational_value()).denominator == 1:
                ratio = Fraction(int(exact.rational_value()), 180)
        else:
            raw = self._to_float(argument)
            ratio = (Fraction(raw).limit_denominator(3600) / 180 if self.degrees
                     else _snap_pi(raw))
            snapped = Fraction(raw).limit_denominator(3600)
            if self.degrees and abs(float(snapped) - raw) > 1e-9:
                ratio = None
        table = {"sin": sin_exact, "cos": cos_exact, "tan": tan_exact}
        found = table[name](ratio)
        if found is not None:
            return found
        self.approximate = True
        value = math.radians(self._to_float(argument)) if self.degrees else self._to_float(argument)
        return {"sin": math.sin, "cos": math.cos, "tan": math.tan}[name](value)


def evaluate(text, exact=True, degrees=False):
    """算一个表达式。返回 {ok, exact, decimal, approximate, error}。"""
    text = str(text or "").strip()
    if not text:
        return {"ok": False, "error": "还没输入算式"}
    if len(text) > MAX_TEXT:
        return {"ok": False, "error": "算式太长了"}
    try:
        tokens = _tokenize(text)
        calculator = _Calc(tokens, exact=exact, degrees=degrees)
        value = calculator.parse()
    except ZeroDivisionError:
        return {"ok": False, "error": "除以 0 了"}
    except ValueError as error:
        message = str(error)
        if "math domain error" in message:
            message = "这个数不在函数的定义域里（比如给负数开平方、给 0 取对数）"
        return {"ok": False, "error": message or "算不出来"}
    except (ArithmeticError, OverflowError):
        return {"ok": False, "error": "算不出来（数值太大或超出范围）"}
    result = {"ok": True, "approximate": False, "exact": "", "decimal": ""}
    if isinstance(value, Exact):
        result["exact"] = format_exact(value)
        result["decimal"] = decimal_text(value)
        result["value"] = value.to_float()
    else:
        number = float(value)
        if not math.isfinite(number):
            return {"ok": False, "error": "结果不是有限的数"}
        result["decimal"] = decimal_text(number)
        result["value"] = number
        found = Exact.from_float(number)
        if found is not None and not calculator.approximate:
            result["exact"] = format_exact(found)
        else:
            result["approximate"] = True
    if result["exact"] and result["exact"] == result["decimal"]:
        result["exact"] = ""
    return result


# --------------------------------------------------------------------------
# 单位换算 + 搜索
# --------------------------------------------------------------------------
def measures():
    out = []
    for key in sorted(units_module.MEASURES, key=lambda k: units_module.MEASURES[k]["cn"]):
        info = units_module.MEASURES[key]
        out.append({"key": key, "cn": info["cn"], "base": info["base"],
                    "count": len(info["units"])})
    return out


def unit_list(measure):
    info = units_module.MEASURES.get(measure)
    if info is None:
        return []
    rows = []
    for code in sorted(info["units"], key=lambda c: (not info["units"][c]["hot"], c)):
        unit = info["units"][code]
        rows.append({"code": code, "cn": unit["cn"], "hot": unit["hot"],
                     "base": code == info["base"]})
    return rows


def convert(measure, value, src, dst):
    info = units_module.MEASURES.get(measure)
    if info is None:
        raise ValueError("没有这个物理量：%s" % measure)
    table = info["units"]
    if src not in table:
        raise ValueError("没有这个单位：%s" % src)
    if dst not in table:
        raise ValueError("没有这个单位：%s" % dst)
    a, b = table[src], table[dst]
    return ((value * a["f"] + a["o"]) - b["o"]) / b["f"]


def _score(query, measure_cn, code, cn):
    """搜索打分：完全等于 > 前缀 > 包含；中文和代号都算。"""
    lowered = query.lower()
    best = 0
    for token, weight in ((code.lower(), 10), (cn, 8), (measure_cn, 2)):
        if not token:
            continue
        if token == lowered:
            best = max(best, 100 + weight)
        elif token.startswith(lowered):
            best = max(best, 60 + weight)
        elif lowered in token:
            best = max(best, 30 + weight)
    return best


def search_units(query, limit=40):
    """按中文名 / 代号 / 别名搜索，返回 (物理量, 单位) 列表。

    别名是 ``"物理量/单位"`` 的形式——**必须绑物理量**，
    否则搜"亩"会把"微秒"也带出来（两个代号都是 mu）。
    """
    query = str(query or "").strip()
    if not query:
        return []
    alias = units_module.ALIASES.get(query)
    scored = []
    for measure, info in units_module.MEASURES.items():
        for code, unit in info["units"].items():
            score = _score(query, info["cn"], code, unit["cn"])
            if alias and alias == "%s/%s" % (measure, code):
                score = max(score, 99)
            if score:
                scored.append((score, unit["hot"], info["cn"], measure, code, unit["cn"]))
    scored.sort(key=lambda row: (-row[0], not row[1], row[2], row[4]))
    out = []
    for score, hot, measure_cn, measure, code, cn in scored[:limit]:
        out.append({"measure": measure, "measureCn": measure_cn, "code": code,
                    "cn": cn, "hot": hot, "score": score})
    return out
