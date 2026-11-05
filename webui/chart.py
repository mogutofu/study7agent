# -*- coding: utf-8 -*-
"""图像（M8）：把"一个量随另一个量怎么变"画成 SVG。

老师要的用法：每个元件/物块有自己的名字（R1、A1、物块），坐标轴随便填——x 填 `R1`、
y 填 `PR1` 就是"R1 的电阻"对"R1 的功率"；旁边再配一张说明表讲清每个记号是什么。

和已有的 `plot.py`（数学函数图／几何，坐标轴**必须等比**，不然圆画成椭圆）不同，这里的
x、y 是两个不同的物理量（比如 R 和 P），**必须各自独立定标**，所以另写一个小的：
  * `evaluate(表达式, lookup)`：把 `PR1`、`Ek物块`、`P(R1)`、`U1*I1` 算成数。
    自己写词法/语法分析（**不用 eval**——轴上的字是界面上填进来的，不能当代码跑）。
  * `chart(points, ...)`：两轴各自自动定标（刻度取 1/2/2.5/5 × 10^n），画网格、折线、
    当前点；轴标题就是老师填的原话，不做文字旋转（Win7 上少踩一个坑）。
  * `series_of(...)`：按采样表逐点算 (x, y)，顺手把算不出来的点跳过并记下原因。
"""

import math

#: 记号里允许出现的字符：字母、数字、下划线、汉字（"物块"这种名字要能写）
_LETTERS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")


class ChartError(Exception):
    """表达式看不懂 / 数算不出来时抛这个，界面上直接显示这句话。"""


def _is_name_char(char):
    return char in _LETTERS or u"\u4e00" <= char <= u"\u9fff"


def _tokenize(text):
    tokens = []
    index = 0
    length = len(str(text))
    text = str(text)
    while index < length:
        char = text[index]
        if char in " \t":
            index += 1
            continue
        if char.isdigit() or (char == "." and index + 1 < length and text[index + 1].isdigit()):
            start = index
            while index < length and (text[index].isdigit() or text[index] == "."):
                index += 1
            tokens.append(("number", text[start:index]))
            continue
        if _is_name_char(char):
            start = index
            while index < length and _is_name_char(text[index]):
                index += 1
            tokens.append(("name", text[start:index]))
            continue
        if char in "+-*/()":
            tokens.append((char, char))
            index += 1
            continue
        raise ChartError("算式里有看不懂的符号：%s" % char)
    if not tokens:
        raise ChartError("算式是空的")
    return tokens


def evaluate(expression, lookup):
    """把表达式算成数。`lookup(记号)` 负责把 `PR1` / `物块` 变成数（不认识就抛 ChartError）。

    支持 `+ - * /`、括号、一元负号、数字，以及两种写法：`Ek物块` 和 `Ek(物块)`。
    """
    tokens = _tokenize(expression)
    position = [0]

    def peek():
        return tokens[position[0]] if position[0] < len(tokens) else (None, None)

    def take():
        token = peek()
        position[0] += 1
        return token

    def primary():
        kind, text = take()
        if kind == "number":
            return float(text)
        if kind == "-":
            return -primary()
        if kind == "+":
            return primary()
        if kind == "(":
            value = sum_rule()
            if take()[0] != ")":
                raise ChartError("括号没配对上")
            return value
        if kind == "name":
            if peek()[0] == "(":                      # Ek(物块) 这种写法
                take()
                inner_kind, inner_text = take()
                if inner_kind != "name":
                    raise ChartError("括号里要写元件 / 物块的名字")
                if take()[0] != ")":
                    raise ChartError("括号没配对上")
                return lookup(text + inner_text)
            return lookup(text)
        raise ChartError("算式到这儿就断了：%s" % (text if text else "（结尾）"))

    def product_rule():
        value = primary()
        while True:
            kind, _text = peek()
            if kind == "*":
                take()
                value *= primary()
            elif kind == "/":
                take()
                divisor = primary()
                if abs(divisor) < 1e-300:
                    raise ChartError("除数算出来是 0")
                value /= divisor
            else:
                return value

    def sum_rule():
        value = product_rule()
        while True:
            kind, _text = peek()
            if kind == "+":
                take()
                value += product_rule()
            elif kind == "-":
                take()
                value -= product_rule()
            else:
                return value

    result = sum_rule()
    if position[0] != len(tokens):
        raise ChartError("算式后面还有没用到的东西：%s" % tokens[position[0]][1])
    if result != result or result in (float("inf"), float("-inf")):
        raise ChartError("这个算式算出来不是个数")
    return result


def _step(low, high, count=6):
    """刻度步长：1/2/2.5/5 × 10^n 里最接近"整段分成 count 格"的那个。"""
    raw = max(1e-12, (float(high) - float(low)) / max(2.0, float(count)))
    magnitude = 10.0 ** math.floor(math.log10(raw))
    return min((1.0, 2.0, 2.5, 5.0, 10.0), key=lambda m: abs(m * magnitude - raw)) * magnitude


def _ticks(low, high, step):
    ticks = []
    value = math.ceil(round(float(low) / step, 9)) * step
    guard = 0
    while value <= float(high) + step * 1e-6 and guard < 400:
        ticks.append(round(value, 10))
        value += step
        guard += 1
    return ticks


def nice_range(low, high, count=6):
    """自动定标：步长取 1/2/2.5/5 × 10^n 里最接近的，返回 (起, 止, [刻度...])。"""
    low, high = float(low), float(high)
    if not high > low:                                # 所有点一样大（比如 P 一直是 0）：
        span = abs(low) * 0.1                         # 给一个像样的窗口，别出现 1e-09 这种刻度
        if span < 0.5:
            span = 0.5
        low, high = low - span, high + span
    step = _step(low, high, count)
    start = math.floor(low / step) * step
    end = math.ceil(high / step) * step
    ticks = _ticks(start, end, step)
    if len(ticks) < 2:
        ticks = [low, high]
    return ticks[0], ticks[-1], ticks


def _fixed_range(low, high, count=6):
    """老师手填的范围：**范围一点都不改**（只把刻度取整到里面）。"""
    low, high = float(low), float(high)
    if not high > low:
        raise ChartError("坐标范围要写成 小<大（比如 0 到 12）")
    step = _step(low, high, count)
    ticks = _ticks(low, high, step)
    if len(ticks) < 2:
        ticks = [low, high]
    return low, high, ticks


def trim(value):
    """刻度数字写短点：1.0 -> 1、0.30000000000000004 -> 0.3。"""
    number = float(value)
    if number == int(number) and abs(number) < 1e15:
        return str(int(number))
    return ("%.6g" % number)


def series_of(samples, x_expression, y_expression, lookup_of):
    """逐点算 (x, y)。`lookup_of(样本)` 给出这一点的记号表；算不出来的点跳过。"""
    points = []
    skipped = None
    for index, sample in enumerate(samples or []):
        table = lookup_of(sample)
        try:
            x = evaluate(x_expression, lambda name, t=table: _get(t, name))
            y = evaluate(y_expression, lambda name, t=table: _get(t, name))
        except ChartError as exc:
            skipped = str(exc)
            continue
        points.append([x, y, index])
    if not points:
        raise ChartError(skipped or "还没有数据可画")
    return points, skipped


def _get(table, name):
    if name in table:
        return _value_of(table[name], name)
    lowered = name.lower()
    for key, value in table.items():
        if key.lower() == lowered:
            return _value_of(value, name)
    raise ChartError("不认识这个记号：%s" % name)


def _value_of(value, name):
    """None 表示"这一帧这个量没法算"（比如落地/碰撞那一帧速度是突变的，加速度没有意义）。"""
    if value is None:
        raise ChartError("这一点的 %s 算不出来（跳过）" % name)
    return value


def layout(points, width=660, height=380, count=6, x_range=None, y_range=None,
           extra=None):
    """算出画图用的几何：定标后的范围 + 画布上绘图区的位置。

    给两处用：`chart()` 画图，`meta_of()` 把映射关系回给前端——前端靠它**不用重新请求**
    就能把"当前点"那个红点挪到正确位置（动画每帧都请求一次就太费了）。
    """
    every = list(points) + [point for group in (extra or []) for point in group]
    xs = [float(point[0]) for point in every]
    ys = [float(point[1]) for point in every]
    if x_range and len(x_range) == 2 and x_range[0] is not None:
        x_min, x_max, x_ticks = _fixed_range(x_range[0], x_range[1], count)
    else:
        x_min, x_max, x_ticks = nice_range(min(xs), max(xs), count)
    if y_range and len(y_range) == 2 and y_range[0] is not None:
        y_min, y_max, y_ticks = _fixed_range(y_range[0], y_range[1], count)
    else:
        y_min, y_max, y_ticks = nice_range(min(ys), max(ys), count)
    if not x_max > x_min:
        x_max = x_min + 1
    if not y_max > y_min:
        y_max = y_min + 1
    left, right, top, bottom = 86.0, 24.0, 40.0, 56.0
    return {"x_min": x_min, "x_max": x_max, "y_min": y_min, "y_max": y_max,
            "x_ticks": x_ticks, "y_ticks": y_ticks,
            "left": left, "top": top,
            "plot_w": width - left - right, "plot_h": height - top - bottom,
            "width": width, "height": height}


def meta_of(points, width=660, height=380, x_range=None, y_range=None, extra=None):
    """数据坐标 -> 像素坐标的映射（前端挪红点用）。"""
    box = layout(points, width, height, x_range=x_range, y_range=y_range, extra=extra)
    return {"left": box["left"], "top": box["top"], "w": box["plot_w"], "h": box["plot_h"],
            "xmin": box["x_min"], "xmax": box["x_max"],
            "ymin": box["y_min"], "ymax": box["y_max"],
            "width": box["width"], "height": box["height"]}


def chart(points, x_label, y_label, cursor=None, note="", width=660, height=380,
          x_range=None, y_range=None, extra=None, main_label=None):
    """画图：points 是 [(x, y, 序号), ...]（按采样顺序连起来），cursor 是当前点的序号。

    两轴**各自**自动定标；点少于 2 个也画（画成一个点）。轴标题横着放，不旋转文字。
    """
    if not points:
        raise ChartError("还没有数据可画：先把滑片拖一拖，或者点「扫一遍」/「算一算」")
    groups = [list(points)] + [list(group[0]) for group in (extra or [])]
    box = layout(points, width, height, x_range=x_range, y_range=y_range,
                 extra=[group[0] for group in (extra or [])])
    x_min, x_max = box["x_min"], box["x_max"]
    y_min, y_max = box["y_min"], box["y_max"]
    x_ticks, y_ticks = box["x_ticks"], box["y_ticks"]
    left, top = box["left"], box["top"]
    plot_w, plot_h = box["plot_w"], box["plot_h"]

    def at_x(value):
        return left + (float(value) - x_min) / (x_max - x_min) * plot_w

    def at_y(value):
        return top + plot_h - (float(value) - y_min) / (y_max - y_min) * plot_h

    parts = ['<svg class="chart-svg" xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
             'viewBox="0 0 %d %d" data-chart="1">' % (width, height, width, height)]
    parts.append('<rect width="%d" height="%d" fill="#fff"/>' % (width, height))
    parts.append('<rect x="%g" y="%g" width="%g" height="%g" fill="#fbfcfe" stroke="#dde4ec"/>'
                 % (left, top, plot_w, plot_h))
    for tick in x_ticks:
        spot = at_x(tick)
        parts.append('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="#eef2f7"/>'
                     % (spot, top, spot, top + plot_h))
        parts.append('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="#9aa7b4"/>'
                     % (spot, top + plot_h, spot, top + plot_h + 4))
        parts.append('<text x="%g" y="%g" font-size="11" fill="#5b6773" text-anchor="middle">%s'
                     '</text>' % (spot, top + plot_h + 17, _escape(trim(tick))))
    for tick in y_ticks:
        spot = at_y(tick)
        parts.append('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="#eef2f7"/>'
                     % (left, spot, left + plot_w, spot))
        parts.append('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="#9aa7b4"/>'
                     % (left - 4, spot, left, spot))
        parts.append('<text x="%g" y="%g" font-size="11" fill="#5b6773" text-anchor="end">%s'
                     '</text>' % (left - 7, spot + 4, _escape(trim(tick))))
    if y_min < 0 < y_max:                             # 零线画深一点，一眼看出正负
        parts.append('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="#c8d2dc"/>'
                     % (left, at_y(0), left + plot_w, at_y(0)))
    if x_min < 0 < x_max:
        parts.append('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="#c8d2dc"/>'
                     % (at_x(0), top, at_x(0), top + plot_h))

    # 第一条是主曲线（有红点跟随），后面的用别的颜色；两条以上时右上角给个小图例
    for order, group in enumerate(groups):
        line = " ".join("%g,%g" % (at_x(point[0]), at_y(point[1])) for point in group)
        if len(group) <= 1:
            continue
        if order == 0:
            color, width_px = "#2f6fd0", 2
        else:
            color, width_px = (extra[order - 1][1], 1.8)
        parts.append('<polyline points="%s" fill="none" stroke="%s" stroke-width="%g"/>'
                     % (line, color, width_px))
    if len(groups) > 1:
        rows = [(main_label or y_label, "#2f6fd0")]
        for group in (extra or []):
            rows.append((group[2], group[1]))
        for order, row in enumerate(rows):
            spot = top + 14 + order * 15
            parts.append('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="%s" stroke-width="3"/>'
                         % (left + plot_w - 168, spot, left + plot_w - 150, spot, row[1]))
            parts.append('<text x="%g" y="%g" font-size="11" fill="#3a4652">%s</text>'
                         % (left + plot_w - 144, spot + 4, _escape(row[0])))
    inside = [point for point in points
              if x_min - 1e-9 <= float(point[0]) <= x_max + 1e-9
              and y_min - 1e-9 <= float(point[1]) <= y_max + 1e-9]
    if len(inside) <= 90:                             # 采样点不多时把点也点出来
        for point in inside:
            parts.append('<circle cx="%g" cy="%g" r="2" fill="#2f6fd0"/>'
                         % (at_x(point[0]), at_y(point[1])))
    if cursor is not None:
        hit = [point for point in points if int(point[2]) == int(cursor)]
        if hit:
            cx, cy = at_x(hit[0][0]), at_y(hit[0][1])
            parts.append('<circle cx="%g" cy="%g" r="5" fill="#e0518f"/>' % (cx, cy))
            label = "(%s, %s)" % (trim(hit[0][0]), trim(hit[0][1]))
            anchor = "start" if cx < left + plot_w * 0.6 else "end"
            parts.append('<text x="%g" y="%g" font-size="11" fill="#b03070" text-anchor="%s">%s'
                         '</text>' % (cx + (9 if anchor == "start" else -9), cy - 8, anchor,
                                      _escape(label)))

    parts.append('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="#3a4652" stroke-width="1.5"/>'
                 % (left, top + plot_h, left + plot_w, top + plot_h))
    parts.append('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="#3a4652" stroke-width="1.5"/>'
                 % (left, top, left, top + plot_h))
    parts.append('<text x="%g" y="%g" font-size="12" fill="#2b3a46">x 轴：%s</text>'
                 % (left, box["height"] - 14, _escape(x_label)))
    parts.append('<text x="%g" y="%g" font-size="12" fill="#2b3a46">y 轴：%s</text>'
                 % (left, 22, _escape(y_label)))
    if note:
        parts.append('<text x="%g" y="%g" font-size="11" fill="#7b8794" text-anchor="end">%s'
                     '</text>' % (left + plot_w, 22, _escape(note)))
    parts.append("</svg>")
    return "".join(parts)


def _escape(text):
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def legend_rows(entries):
    """说明表：entries 是 [(记号, 含义, 当前值)]，值缺了留空。"""
    rows = []
    for entry in entries:
        symbol, meaning = entry[0], entry[1]
        value = entry[2] if len(entry) > 2 else None
        rows.append({"symbol": symbol, "meaning": meaning,
                     "value": "" if value is None else trim(value)})
    return rows
