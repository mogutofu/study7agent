# -*- coding: utf-8 -*-
"""电路图（M7 补做）：按初中/高中的电路符号画，不是"几个矩形加文字"。

老师的话："电路得有电路符号"。所以这里画的是真符号：
  * 电源：长线（正极）＋ 短线（负极），旁边标 ε、r；
  * 定值电阻：矩形；
  * 滑动变阻器：矩形 + 滑片箭头（箭头指到哪个位置就是滑动到哪）；
  * 电流表/电压表：圆圈里写 A / V，电压表用两根线并接在被测元件两端；
  * 导线：正交折线；开关：两个接点 + 一根斜着的刀闸。

坐标是"电路图视角"，和物理场景的 x/y 无关——`physics.py` 的电路模型和练习模式都用它出图。
"""

#: 画布与主回路的位置（都在这里调，别散在函数里）
CANVAS = (620, 300)
LEFT, RIGHT, TOP, BOTTOM = 70, 550, 70, 220


def _wire(x1, y1, x2, y2):
    return '<line x1="%s" y1="%s" x2="%s" y2="%s" stroke="#3a4652" stroke-width="2"/>' % (
        x1, y1, x2, y2)


def _label(x, y, text, size=12, anchor="middle", color="#3a4652"):
    return ('<text x="%s" y="%s" font-size="%d" fill="%s" text-anchor="%s">%s</text>'
            % (x, y, size, color, anchor, text))


def battery(x, y, emf=None, inner=None):
    """电源符号：长线 + 短线（长的是正极），左边标 ε、内阻 r。"""
    parts = [
        '<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#3a4652" stroke-width="2.5"/>'
        % (x - 18, y - 8, x + 18, y - 8),
        '<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#3a4652" stroke-width="5"/>'
        % (x - 10, y + 8, x + 10, y + 8),
        _label(x + 26, y - 12, "+", 14, "middle"),
    ]
    text = "ε" + (("=%s V" % emf) if emf is not None else "")
    if inner is not None:
        text += "，r=%s Ω" % inner
    parts.append(_label(x - 26, y, text, 12, "end"))
    return "".join(parts)


def resistor(x, y, width=54, height=20, name="", value=None):
    """定值电阻：矩形（中学通用画法）。"""
    parts = ['<rect x="%d" y="%d" width="%d" height="%d" fill="#fff" stroke="#3a4652" '
             'stroke-width="2"/>' % (x - width / 2, y - height / 2, width, height)]
    if name or value is not None:
        parts.append(_label(x, y - height / 2 - 8,
                            name + (("=%s Ω" % value) if value is not None else "")))
    return "".join(parts)


def rheostat(x, y, width=76, height=20, name="R2", value=None, ratio=0.0):
    """滑动变阻器：矩形 + 滑片箭头（`ratio` 是滑片位置 0~1）。"""
    knob = x - width / 2 + width * ratio          # 0% 在最左、100% 在最右
    parts = [resistor(x, y, width, height, name, value),
             '<line x1="%.1f" y1="%d" x2="%.1f" y2="%.1f" stroke="#c0392b" stroke-width="2"/>'
             % (knob, y - 26, knob, y - height / 2),
             '<line x1="%.1f" y1="%d" x2="%d" y2="%d" stroke="#c0392b" stroke-width="2"/>'
             % (knob, y - 26, x + width / 2 + 10, y - 26),
             '<polygon points="%.1f,%.1f %.1f,%.1f %.1f,%.1f" fill="#c0392b"/>'
             % (knob, y - height / 2 + 2, knob - 6, y - height / 2 - 10,
                knob + 6, y - height / 2 - 10)]
    return "".join(parts)


def meter(x, y, letter, name="", radius=18):
    """电表：圆圈里写 A / V（电流表/电压表）。"""
    return ('<circle cx="%d" cy="%d" r="%d" fill="#fff" stroke="#3a4652" stroke-width="2"/>'
            '<text x="%d" y="%d" font-size="15" fill="#3a4652" text-anchor="middle" '
            'font-weight="600">%s</text>%s'
            % (x, y, radius, x, y + 5, letter,
               _label(x, y + radius + 16, name) if name else ""))


def switch(x, y, closed=True):
    """开关：两个接点 + 刀闸。"""
    parts = ['<circle cx="%d" cy="%d" r="3" fill="#3a4652"/>' % (x - 16, y),
             '<circle cx="%d" cy="%d" r="3" fill="#3a4652"/>' % (x + 16, y)]
    if closed:
        parts.append(_wire(x - 16, y, x + 16, y))
    else:
        parts.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#3a4652" stroke-width="2"/>'
                     % (x - 16, y, x + 10, y - 16))
    return "".join(parts)


def circuit_diagram(params, readings=None):
    """画一张串联电路图：电源(ε,r) + R1 + 滑动变阻器 R2，电流表串在回路里、电压表并接 R2。

    `params` 用 `physics.py` 电路模型的那几个键；`readings` 传 {"current":…, "voltage":…}
    时会在表盘旁写上读数（讲题时好用）。
    """
    emf = params.get("emf")
    inner = params.get("r")
    r1 = params.get("r1")
    r2max = params.get("r2max")
    ratio = 0.0
    if readings and readings.get("r2") is not None and r2max:
        ratio = max(0.0, min(1.0, float(readings["r2"]) / float(r2max)))
    width, height = CANVAS
    parts = ['<svg class="circuit-svg" xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
             'viewBox="0 0 %d %d">' % (width, height, width, height)]

    # 左竖线：电源；上横线：R1 与滑动变阻器；右竖线；下横线：电流表与开关
    parts.append(_wire(LEFT, TOP, LEFT, BOTTOM))
    parts.append(_wire(LEFT + 30, TOP, RIGHT, TOP))
    parts.append(_wire(LEFT, TOP, LEFT + 30, TOP))
    parts.append(_wire(RIGHT, TOP, RIGHT, BOTTOM))
    parts.append(_wire(LEFT, BOTTOM, RIGHT, BOTTOM))
    parts.append(battery(LEFT, (TOP + BOTTOM) / 2, emf, inner))

    # R1（上边靠左）、滑动变阻器（上边靠右）
    parts.append(resistor(180, TOP, 56, 20, "R1", r1))
    parts.append(rheostat(390, TOP, 84, 20, "R2", r2max, ratio))

    # 电压表并接在 R2 两端；电流表与开关串在下边
    parts.append(_wire(348, TOP, 348, 150))
    parts.append(_wire(432, TOP, 432, 150))
    parts.append(_wire(348, 150, 372, 150))
    parts.append(_wire(408, 150, 432, 150))
    parts.append(meter(390, 150, "V", "电压表"))
    parts.append(meter(310, BOTTOM, "A", "电流表"))
    parts.append(switch(150, BOTTOM, True))

    if readings:
        if readings.get("current") is not None:
            parts.append(_label(310, BOTTOM - 30, "%s A" % readings["current"], 12, "middle",
                                "#c0392b"))
        if readings.get("voltage") is not None:
            parts.append(_label(390, 118, "%s V" % readings["voltage"], 12, "middle", "#c0392b"))
        if readings.get("r2") is not None:
            parts.append(_label(390, TOP - 34, "滑片 R2=%s Ω" % readings["r2"], 12, "middle",
                                "#c0392b"))
    parts.append(_label(width / 2, height - 18,
                        "串联电路：电流表测干路电流，电压表并接在滑动变阻器两端"))
    parts.append("</svg>")
    return "".join(parts)


def belt_diagram(params):
    """传送带的示意图：斜面（或水平）、带、物块、带速箭头。"""
    length = float(params.get("length", 4.0))
    angle = float(params.get("angle", 0.0))
    speed = float(params.get("belt_speed", 2.0))
    direction = str(params.get("belt_dir") or "up")
    width, height = 620, 260
    import math
    radians = math.radians(angle)
    x0, y0 = 60, 200
    x1 = x0 + length * 40 * math.cos(radians)
    y1 = y0 - length * 40 * math.sin(radians)
    parts = ['<svg class="belt-svg" xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
             'viewBox="0 0 %d %d">' % (width, height, width, height)]
    parts.append('<line x1="%d" y1="%d" x2="%.1f" y2="%.1f" stroke="#4d6bfe" stroke-width="8" '
                 'stroke-linecap="round"/>' % (x0, y0, x1, y1))
    parts.append('<circle cx="%d" cy="%d" r="10" fill="#e8eefc" stroke="#4d6bfe"/>' % (x0, y0))
    parts.append('<circle cx="%.1f" cy="%.1f" r="10" fill="#e8eefc" stroke="#4d6bfe"/>' % (x1, y1))
    arrow = 46 if direction == "up" else -46
    parts.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#12a594" '
                 'stroke-width="2"/>' % (x1 / 2 + x0 / 2, y1 / 2 + y0 / 2 + 18,
                                         x1 / 2 + x0 / 2 + arrow, y1 / 2 + y0 / 2 + 18))
    parts.append(_label(x1 / 2 + x0 / 2, y1 / 2 + y0 / 2 + 40,
                        "带速 %s m/s" % speed, 12, "middle", "#12a594"))
    parts.append('<rect x="%d" y="%d" width="26" height="26" rx="4" fill="#3a4652"/>'
                 % (x0 - 12, y0 - 40))
    parts.append(_label(x0, y0 - 52, "物块 m=1 kg", 12, "middle"))
    parts.append(_label(x1 + 6, y1 - 14, "L=%s m" % length, 12, "start"))
    if angle:
        parts.append(_wire(x0, y0, x1, y0))
        parts.append(_label((x0 + x1) / 2, y0 + 18, "θ=%s°" % angle))
    parts.append(_label(width / 2, height - 12, "物块与带之间的动摩擦因数 μ 见题干"))
    parts.append("</svg>")
    return "".join(parts)


def efield_diagram(params):
    """类平抛示意图：两块极板 + 电子入射 + 屏。"""
    width, height = 620, 260
    plate = float(params.get("plate", 0.1))
    gap = float(params.get("gap", 0.02))
    voltage = params.get("voltage")
    screen = float(params.get("screen", 0.2))
    x0, x1 = 150, 150 + max(120, plate * 900)
    screen_x = min(width - 40, x1 + max(60, screen * 260))
    mid = height / 2
    half = max(24, gap * 900)
    parts = ['<svg class="efield-svg" xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
             'viewBox="0 0 %d %d">' % (width, height, width, height)]
    parts.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="#3a4652" stroke-width="5"/>'
                 % (x0, mid - half, x1, mid - half))
    parts.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="#3a4652" stroke-width="5"/>'
                 % (x0, mid + half, x1, mid + half))
    parts.append(_label((x0 + x1) / 2, mid - half - 10, "偏转电压 %s V" % voltage))
    parts.append(_wire(x0 - 60, mid, x0, mid))
    parts.append('<polygon points="%d,%d %d,%d %d,%d" fill="#c0392b"/>'
                 % (x0, mid, x0 - 10, mid - 5, x0 - 10, mid + 5))
    parts.append(_label(x0 - 66, mid - 8, "电子 v₀", 12, "end"))
    parts.append(_wire(screen_x, mid - half * 2.4, screen_x, mid + half * 2.4))
    parts.append(_label(screen_x, mid + half * 2.4 + 16, "屏"))
    parts.append('<path d="M%d %d Q%d %d %d %d" fill="none" stroke="#c0392b" stroke-width="2"/>'
                 % (x0, mid, (x0 + x1) / 2, mid + half * 0.5, x1, mid + half * 0.6))
    return "".join(parts + ["</svg>"])
