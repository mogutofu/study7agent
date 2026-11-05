# -*- coding: utf-8 -*-
"""读数模块（M7）：五种仪器的读数与判分，按高考的估读口径。

为什么要单独一个模块：读数题的分不是"数值对上"就算，**估读到哪一位**才算（螺旋测微器必须
估读到 0.001 mm、游标卡尺不估读、0.5 V 分度估读到 0.1 V……）。这些规则散在老师脑子里，
这里做成一张表：每种仪器给 `division`（最小分度）、`step`（读数允许的最小步长）、`digits`
（该写几位小数），判分、出题、画图都用这一份，不各写一套。

五种：游标卡尺（10/20/50 分度）、螺旋测微器、电流表（0.6/3 A）、电压表（3/15 V）、刻度尺。
"""

import math
import random


class ReadError(ValueError):
    """仪器名/参数不认识——直接说清楚，别给出一个假的读数。"""


#: 每种仪器的读数口径（division=最小分度，step=读数的最小步长，digits=该写的小数位）
#: tolerance=判分时允许的误差。老师的话是"本来就容易读不准"，所以按**最小分度的一格左右**给
#: （屏幕上仪器图是缩放过的，游标对线、表盘估读都容易差一点），不是卡到分度值的一半。
INSTRUMENTS = {
    "caliper10": {"name": "游标卡尺（10 分度）", "unit": "mm", "division": 0.1,
                  "step": 0.1, "digits": 1, "estimate": False, "tolerance": 0.1,
                  "note": "10 分度：精度 0.1 mm，末位就是精度位，不估读"},
    "caliper20": {"name": "游标卡尺（20 分度）", "unit": "mm", "division": 0.05,
                  "step": 0.05, "digits": 2, "estimate": False, "tolerance": 0.05,
                  "note": "20 分度：精度 0.05 mm，不估读（写成 0.05 的整数倍）"},
    "caliper50": {"name": "游标卡尺（50 分度）", "unit": "mm", "division": 0.02,
                  "step": 0.02, "digits": 2, "estimate": False, "tolerance": 0.03,
                  "note": "50 分度：精度 0.02 mm，不估读"},
    "micrometer": {"name": "螺旋测微器", "unit": "mm", "division": 0.01,
                   "step": 0.001, "digits": 3, "estimate": True, "tolerance": 0.002,
                   "note": "精度 0.01 mm，必须估读到 0.001 mm"},
    "ammeter06": {"name": "电流表（0.6 A 量程）", "unit": "A", "division": 0.02,
                  "step": 0.01, "digits": 2, "estimate": True, "tolerance": 0.02,
                  "note": "最小分度 0.02 A，估读到 0.01 A"},
    "ammeter3": {"name": "电流表（3 A 量程）", "unit": "A", "division": 0.1,
                 "step": 0.01, "digits": 2, "estimate": True, "tolerance": 0.03,
                 "note": "最小分度 0.1 A，估读到 0.01 A"},
    "voltmeter3": {"name": "电压表（3 V 量程）", "unit": "V", "division": 0.1,
                   "step": 0.01, "digits": 2, "estimate": True, "tolerance": 0.03,
                   "note": "最小分度 0.1 V，估读到 0.01 V"},
    "voltmeter15": {"name": "电压表（15 V 量程）", "unit": "V", "division": 0.5,
                    "step": 0.1, "digits": 1, "estimate": True, "tolerance": 0.1,
                    "note": "最小分度 0.5 V，估读到 0.1 V"},
    "ruler": {"name": "刻度尺", "unit": "mm", "division": 1.0, "step": 0.1, "tolerance": 0.2,
              "digits": 1, "estimate": True,
              "note": "最小分度 1 mm，估读到 0.1 mm"},
}

#: 出题时给"真值"用的档位（每个仪器一组常见量程内取值）
RANGES = {
    "caliper10": (0.0, 120.0), "caliper20": (0.0, 120.0), "caliper50": (0.0, 120.0),
    "micrometer": (0.0, 8.0), "ammeter06": (0.0, 0.6), "ammeter3": (0.0, 3.0),
    "voltmeter3": (0.0, 3.0), "voltmeter15": (0.0, 15.0), "ruler": (0.0, 200.0),
}


def kinds():
    """仪器清单（界面按这个生成下拉框）。"""
    return [{"key": key, "name": item["name"], "unit": item["unit"],
             "digits": item["digits"], "estimate": item["estimate"], "note": item["note"]}
            for key, item in sorted(INSTRUMENTS.items())]


def spec(kind):
    if kind not in INSTRUMENTS:
        raise ReadError("没有这种仪器：%s" % kind)
    return INSTRUMENTS[kind]


def format_reading(kind, value):
    """按该仪器该写的小数位格式化（0.42 A、5.820 mm 这种），判分和显示都用它。"""
    item = spec(kind)
    return "%.*f" % (item["digits"], value + 0.0)


# ---------------------------------------------------------------- 读数（从仪器状态算真值）

def caliper_reading(main_mm, vernier_divisions, vernier_index):
    """游标卡尺：读数 = 主尺读数 + 游标对齐格数 × 精度。

    主尺读数是"游标 0 刻线左边最近的主尺刻度"，所以这里要求给的是**整毫米**主尺读数；
    精度 = 1 mm / 分度数（10→0.1、20→0.05、50→0.02），不估读。
    """
    if vernier_divisions not in (10, 20, 50):
        raise ReadError("游标卡尺只有 10 / 20 / 50 分度")
    if not 0 <= vernier_index <= vernier_divisions:
        raise ReadError("游标对齐格数要在 0 ~ %d 之间" % vernier_divisions)
    precision = 1.0 / vernier_divisions
    value = float(main_mm) + vernier_index * precision
    kind = "caliper%d" % vernier_divisions
    return {"kind": kind, "value": round(value, 4), "text": format_reading(kind, value),
            "precision": precision, "main": float(main_mm), "index": vernier_index}


def micrometer_reading(sleeve_mm, thimble_div, thimble_step=0.01):
    """螺旋测微器：固定刻度 + 可动刻度 × 0.01 mm；可动刻度要**估读**到 0.001 mm。

    `thimble_div` 可以是小数（例如 32.4 格），真值就是 sleeve + 32.4×0.01 = 5.824 mm，
    正确写法是 5.824 mm（最后一位是估读位）。
    """
    if not 0 <= thimble_div < 50:
        raise ReadError("可动刻度在 0 ~ 50 格之间")
    value = float(sleeve_mm) + float(thimble_div) * thimble_step
    return {"kind": "micrometer", "value": round(value, 4),
            "text": format_reading("micrometer", value), "sleeve": float(sleeve_mm),
            "thimble": float(thimble_div)}


def meter_reading(kind, value):
    """电流表/电压表：给指针示值，按量程的最小分度算正确读数。

    指针落在两小格之间时要估读：0.02 A 分度估读到 0.01 A、0.5 V 分度估读到 0.1 V，
    所以先把真值**对齐到 step**（真实实验中指针就是落在最小读数步长上的）。
    """
    item = spec(kind)
    if kind not in ("ammeter06", "ammeter3", "voltmeter3", "voltmeter15"):
        raise ReadError("%s 不是电表" % kind)
    if not 0 <= value <= RANGES[kind][1] + 1e-9:
        raise ReadError("示值超出量程")
    step = item["step"]
    aligned = round(value / step) * step
    text = format_reading(kind, aligned)
    return {"kind": kind, "value": round(aligned, 6), "text": text,
            "divisions": round(aligned / item["division"], 4)}


def ruler_reading(value_mm):
    """刻度尺：最小分度 1 mm，估读到 0.1 mm。"""
    aligned = round(float(value_mm) / 0.1) * 0.1
    return {"kind": "ruler", "value": round(aligned, 4),
            "text": format_reading("ruler", aligned)}


def reading(kind, params):
    """按仪器名分派：params 就是上面几个函数要的参数。"""
    if kind.startswith("caliper"):
        return caliper_reading(params.get("main", 0), int(kind[7:]),
                               int(params.get("index", 0)))
    if kind == "micrometer":
        return micrometer_reading(params.get("sleeve", 0), params.get("thimble", 0))
    if kind == "ruler":
        return ruler_reading(params.get("value", 0))
    return meter_reading(kind, params.get("value", 0))


# ---------------------------------------------------------------- 判分（按高考口径）

def judge(kind, expected, answer):
    """判一个读数：数值要在半个最小读数步长内，**有效位数也要对**。

    返回 {"ok":bool, "reason":str, "digits":int}；reason 是给学生的说法（少估读一位、
    多写了一位、数量级不对……），老师照着讲就行。
    """
    item = spec(kind)
    text = str(answer or "").strip().replace("mm", "").replace("A", "").replace("V", "")
    text = text.replace("厘米", "").replace("cm", "").strip()
    if not text:
        return {"ok": False, "reason": "还没填读数", "digits": item["digits"]}
    try:
        value = float(text)
    except ValueError:
        return {"ok": False, "reason": "读数要写成数字", "digits": item["digits"]}
    places = len(text.split(".")[1]) if "." in text else 0
    if places > item["digits"]:
        return {"ok": False, "digits": item["digits"],
                "reason": "写多了：这种仪器读到小数点后 %d 位就够" % item["digits"]}
    if places < item["digits"] and item["estimate"]:
        return {"ok": False, "digits": item["digits"],
                "reason": "少估读一位：要写到小数点后 %d 位（估读位不能省）" % item["digits"]}
    if abs(value - expected) > item.get("tolerance", item["step"] / 2) + 1e-9:
        return {"ok": False, "digits": item["digits"],
                "reason": "数值不对：正确读数应在 %s 附近（允许 ±%s）"
                          % (format_reading(kind, expected),
                             item.get("tolerance", item["step"] / 2))}
    return {"ok": True, "reason": "对了（%s %s）" % (format_reading(kind, expected),
                                                  item["unit"]),
            "digits": item["digits"]}


# ---------------------------------------------------------------- 出题

def drill(kind, seed=None, params=None):
    """出一道读数题：不给参数就按仪器随机造一个**真实可读**的状态。

    随机时只在"最小读数步长"上取值：真值本身就是仪器能显示的样子，
    这样标准答案不会出现 0.4237 这种实验里读不出来的数。
    """
    item = spec(kind)
    rng = random.Random(seed)
    low, high = RANGES[kind]
    params = dict(params or {})
    if kind.startswith("caliper"):
        divisions = int(kind[7:])
        if "index" not in params:
            params["index"] = rng.randrange(0, divisions + 1)
        if "main" not in params:
            params["main"] = rng.randrange(0, 8) * 10 + rng.randrange(0, 10)
        if divisions == 10:
            params["main"] = min(params["main"], 120)
    elif kind == "micrometer":
        if "sleeve" not in params:
            params["sleeve"] = round(rng.uniform(0, 7) * 2) / 2.0     # 0.5 mm 一格
        if "thimble" not in params:
            params["thimble"] = rng.randrange(0, 500) / 10.0          # 估读到 0.1 格
    elif kind == "ruler":
        if "value" not in params:
            params["value"] = round(rng.uniform(low, high), 1)
    else:
        step = item["step"]
        if "value" not in params:
            params["value"] = round(rng.randint(int(low / step), int(high / step)) * step, 6)
    truth = reading(kind, params)
    return {"kind": kind, "name": item["name"], "unit": item["unit"],
            "params": params, "answer": truth["value"], "text": truth["text"],
            "digits": item["digits"], "note": item["note"],
            "figure": figure(kind, params)}


# ---------------------------------------------------------------- 画图（SVG）

def _ticks(count, length, step_px, x0, y0, vertical=True, long_every=5, label_from=0):
    """画一排刻度线（电表表盘、主尺、刻度尺都用它）。"""
    parts = []
    for index in range(count + 1):
        long = index % long_every == 0
        size = length if long else length * 0.6
        position = index * step_px
        if vertical:                                   # 刻度线朝下
            parts.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#3a4652"/>'
                         % (x0 + position, y0, x0 + position, y0 + size))
            if long and index >= label_from:
                parts.append('<text x="%.1f" y="%.1f" font-size="10" fill="#3a4652" '
                             'text-anchor="middle">%d</text>' % (x0 + position, y0 + size + 11, index))
        else:                                          # 刻度线朝右
            parts.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#3a4652"/>'
                         % (x0, y0 + position, x0 + size, y0 + position))
            if long and index >= label_from:
                parts.append('<text x="%.1f" y="%.1f" font-size="10" fill="#3a4652" '
                             'text-anchor="end">%d</text>' % (x0 - 3, y0 + position + 3, index))
    return parts


#: 每种仪器的画布尺寸（够大才看得清分度：老师要能照着图读数）
FIGURES = {"caliper": (980, 300), "micrometer": (980, 300), "meter": (760, 420),
           "ruler": (980, 200)}


def _scale(kind):
    if kind.startswith("caliper"):
        return 7.0                       # 每毫米 7 px（量程 120 mm 也放得下）
    return 1.0


def _svg(width, height):
    return ('<svg class="inst-svg" xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
            'viewBox="0 0 %d %d">' % (width, height, width, height))


def _text(x, y, text, size=13, anchor="middle", color="#3a4652"):
    return ('<text x="%.1f" y="%.1f" font-size="%d" fill="%s" text-anchor="%s">%s</text>'
            % (x, y, size, color, anchor, text))


def _caliper_figure(kind, truth):
    """游标卡尺：主尺（毫米刻度 + 厘米数字）＋ 游标（分度数字），游标 0 线对准读数位置。

    只画仪器、**不写答案分解式**（"主尺＋第几格×精度"那句会直接把答案说穿，
    放在判分解释里讲）。
    """
    divisions = int(kind[7:])
    precision = 1.0 / divisions
    width, height = FIGURES["caliper"]
    scale = _scale(kind)
    value = truth["value"]
    left, main_y = 60, 130
    parts = [_svg(width, height)]
    parts.append('<rect x="30" y="60" width="920" height="120" rx="8" fill="#f8fafc" '
                 'stroke="#c3ccd8"/>')
    # 主尺：每毫米一线，5 mm 中线，10 mm 长线带厘米数字
    for mm in range(0, 121):
        x = left + mm * scale
        if mm % 10 == 0:
            length, stroke = 26, 2.0
        elif mm % 5 == 0:
            length, stroke = 18, 1.4
        else:
            length, stroke = 11, 1.0
        parts.append('<line x1="%.1f" y1="%d" x2="%.1f" y2="%d" stroke="#3a4652" '
                     'stroke-width="%.1f"/>' % (x, main_y, x, main_y + length, stroke))
        if mm % 10 == 0:
            parts.append(_text(x, main_y - 8, str(mm // 10), 14))
    parts.append(_text(left - 34, main_y + 8, "cm", 13, "end"))
    parts.append(_text(width / 2, 92, "主尺（每小格 1 mm）", 12, "middle", "#7b8794"))
    # 游标：0 线在读数处，分度间距 = (n-1)/n mm
    spacing = (divisions - 1.0) / divisions
    zero_x = left + value * scale
    plate_y = main_y + 30      # 游标就贴在主尺下面（离太远看着不像一把卡尺）
    parts.append('<rect x="%.1f" y="%d" width="%.1f" height="46" rx="4" fill="#dce7ff" '
                 'stroke="#4d6bfe"/>' % (zero_x - 6, plate_y, (divisions - 1) * scale + 12))
    for step in range(0, divisions + 1):
        x = zero_x + step * spacing * scale
        length = 26 if step % 5 == 0 else 16
        parts.append('<line x1="%.1f" y1="%d" x2="%.1f" y2="%d" stroke="#2b4acb" '
                     'stroke-width="%.1f"/>'
                     % (x, plate_y, x, plate_y + length, 1.8 if step % 5 == 0 else 1.0))
        if step % 5 == 0 or divisions <= 10:
            parts.append(_text(x, plate_y + 48, str(step), 11, "middle", "#2b4acb"))
    parts.append(_text(zero_x - 6, plate_y - 8, "游标 0", 12, "middle", "#c0392b"))
    parts.append(_text(width / 2, height - 12,
                       "游标卡尺：主尺读整毫米，再看游标第几格与主尺对齐（%d 分度，精度 %.2f mm，"
                       "不估读）" % (divisions, precision), 12, "middle", "#7b8794"))
    parts.append("</svg>")
    return "".join(parts)


def _micrometer_figure(truth):
    """螺旋测微器：固定套管（0.5 mm 一格、毫米数字、轴向基准线）＋ 可动刻度鼓轮。

    鼓轮画成"展开图"，但**必须让基准线正好压在读数那一格上**：
    鼓轮绕中心对称展开（基准线上下各 25 格），读数是"基准线对着可动刻度第几格"，
    这样图上量出来的和答案完全一致（之前鼓轮是死的 0~50、基准线压在哪儿全凭运气，
    看着就"错位"）。
    """
    width, height = FIGURES["micrometer"]
    sleeve = truth["sleeve"]
    thimble = truth["thimble"]
    axis_y = 170
    parts = [_svg(width, height)]
    parts.append('<rect x="20" y="30" width="940" height="250" rx="10" fill="#f8fafc" '
                 'stroke="#c3ccd8"/>')
    # 测砧 + 测微螺杆
    parts.append('<rect x="50" y="%d" width="26" height="76" rx="6" fill="#dfe6ef" '
                 'stroke="#3a4652" stroke-width="2"/>' % (axis_y - 38))
    parts.append(_text(63, axis_y + 62, "测砧", 12))
    parts.append('<rect x="76" y="%d" width="150" height="24" fill="#eef2f7" '
                 'stroke="#3a4652"/>' % (axis_y - 12))
    # 固定套管：轴向基准线 + 0.5 mm 刻度（下方整毫米带数字、上方短线）
    # 套管只画到"最后一条露出来的刻度"为止，再往右被鼓轮盖住（这就是"螺旋盖住尺子"）
    visible = math.floor((sleeve + thimble * 0.01 + 1e-9) * 2) / 2.0
    sleeve_x = 226
    sleeve_w = int(visible * 52) + 18
    parts.append('<rect x="%d" y="%d" width="%d" height="76" rx="6" fill="#fff" '
                 'stroke="#3a4652" stroke-width="2"/>' % (sleeve_x, axis_y - 38, sleeve_w))
    parts.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#3a4652" stroke-width="2"/>'
                 % (sleeve_x, axis_y, sleeve_x + sleeve_w, axis_y))
    half_step = 26
    index = 0
    while index * half_step <= sleeve_w and index * 0.5 <= visible + 1e-9:
        x = sleeve_x + index * half_step
        if index % 2 == 0:
            parts.append('<line x1="%.1f" y1="%d" x2="%.1f" y2="%d" stroke="#3a4652" '
                         'stroke-width="1.6"/>' % (x, axis_y, x, axis_y + 24))
            parts.append(_text(x, axis_y + 42, str(index // 2), 13))
        else:
            parts.append('<line x1="%.1f" y1="%d" x2="%.1f" y2="%d" stroke="#3a4652" '
                         'stroke-width="1.2"/>' % (x, axis_y - 22, x, axis_y))
        index += 1
    parts.append(_text(sleeve_x + sleeve_w / 2, axis_y - 52,
                       "固定套管：下方整毫米带数字，上方短线是 0.5 mm", 12, "middle", "#7b8794"))
    # 可动刻度：绕基准线上下各 25 格展开，读数那格**正好落在基准线上**
    drum_x, drum_w = sleeve_x + sleeve_w - 6, 96    # 鼓轮压在套管右端（盖住看不见的刻度）
    step_px = 4.2
    span = 25
    drum_top = axis_y - span * step_px
    drum_h = 2 * span * step_px
    parts.append('<rect x="%d" y="%.1f" width="%d" height="%.1f" rx="8" fill="#dce7ff" '
                 'stroke="#2b4acb" stroke-width="2"/>' % (drum_x, drum_top, drum_w, drum_h))
    # 刻度线按**小数偏移**画：第 n 格画在 y = 基准线 + (n - 读数)×每格像素，
    # 这样基准线正好落在"读数那一格"的位置上（不四舍五入 -> 图上的读数和答案一致）
    base = int(thimble)
    first = base - span
    for value in range(first, base + span + 1):
        shown = value % 50
        y = axis_y - (value - thimble) * step_px     # 往上数字变大（之前上下颠倒了）
        long = shown % 5 == 0
        parts.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="#2b4acb" '
                     'stroke-width="%.1f"/>'
                     % (drum_x, y, drum_x + (34 if long else 18), y, 1.8 if long else 0.9))
        if long:
            parts.append(_text(drum_x + 52, y + 4, str(shown), 12, "start", "#2b4acb"))
    # 基准线：从套管一直指到鼓轮**中间**（读数那一格）
    parts.append('<line x1="%.1f" y1="%d" x2="%d" y2="%d" stroke="#c0392b" stroke-width="2.5"/>'
                 % (sleeve_x + sleeve_w - 10, axis_y, drum_x - 30, axis_y))
    parts.append('<polygon points="%d,%d %d,%d %d,%d" fill="#c0392b"/>'
                 % (drum_x - 14, axis_y, drum_x - 30, axis_y - 7, drum_x - 30, axis_y + 7))
    parts.append(_text(drum_x + drum_w / 2, drum_top - 12,
                       "可动刻度（每格 0.01 mm，估读到 0.001）", 12, "middle", "#2b4acb"))
    parts.append(_text(drum_x - 34, axis_y - 14, "基准线", 12, "end", "#c0392b"))
    # 棘轮
    ratchet_x = drum_x + drum_w + 20
    parts.append('<rect x="%d" y="%d" width="56" height="120" rx="8" fill="#dfe6ef" '
                 'stroke="#3a4652" stroke-width="2"/>' % (ratchet_x, axis_y - 60))
    for flutes in range(0, 6):
        parts.append('<line x1="%.1f" y1="%d" x2="%.1f" y2="%d" stroke="#7b8794"/>'
                     % (ratchet_x + 6 + flutes * 9, axis_y - 52,
                        ratchet_x + 6 + flutes * 9, axis_y + 52))
    parts.append(_text(ratchet_x + 28, axis_y + 76, "棘轮", 12))
    parts.append(_text(width / 2, height - 14,
                       "螺旋测微器：先读固定套管（整毫米 + 0.5 mm 短线），"
                       "再看基准线对着可动刻度第几格（×0.01 mm，最后一位估读）",
                       12, "middle", "#7b8794"))
    parts.append("</svg>")
    return "".join(parts)


def _show_number(value):
    text = ("%.3f" % (float(value) + 0.0)).rstrip("0").rstrip(".")
    return text or "0"


def _meter_figure(kind, truth):
    """电表：半圆表盘 + 主分度数字 + 小分度 + 指针。

    位置全部按极坐标算（x = cx + R·cosθ、y = cy - R·sinθ）：之前用 SVG 的 rotate()
    转刻度和数字，y 轴向下会把整块表盘连指针一起**镜像**（零点指到正下方、数字全歪），
    看着就是"示数错位"。现在 0 在最左端、满量程在最右端，数字一律正着写。
    """
    item = spec(kind)
    top = RANGES[kind][1]
    width, height = FIGURES["meter"]
    center = (width / 2.0, 350.0)
    radius = 250.0
    parts = [_svg(width, height)]
    parts.append('<path d="M%.1f %.1f A%.0f %.0f 0 0 1 %.1f %.1f" fill="#fff" '
                 'stroke="#3a4652" stroke-width="2"/>'
                 % (center[0] - radius, center[1], radius, radius, center[0] + radius,
                    center[1]))
    total_minor = 60
    for index in range(0, total_minor + 1):
        fraction = index / float(total_minor)
        angle = math.radians(180.0 - 180.0 * fraction)      # 左端 180°，右端 0°
        major = index % 10 == 0
        outer = radius
        inner = radius - (28 if major else 16)
        parts.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#3a4652" '
                     'stroke-width="%.1f"/>'
                     % (center[0] + outer * math.cos(angle), center[1] - outer * math.sin(angle),
                        center[0] + inner * math.cos(angle), center[1] - inner * math.sin(angle),
                        1.7 if major else 0.9))
        if major:
            label_r = radius - 54
            parts.append(_text(center[0] + label_r * math.cos(angle),
                               center[1] - label_r * math.sin(angle) + 5,
                               _show_number(top * fraction), 14))
    value = min(top, max(0.0, float(truth["value"])))
    needle = math.radians(180.0 - 180.0 * (value / top))
    parts.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#c0392b" '
                 'stroke-width="3"/>'
                 % (center[0], center[1], center[0] + (radius - 12) * math.cos(needle),
                    center[1] - (radius - 12) * math.sin(needle)))
    parts.append('<circle cx="%.1f" cy="%.1f" r="10" fill="#3a4652"/>' % center)
    parts.append(_text(center[0], center[1] + 46, item["name"], 15))
    parts.append(_text(center[0], center[1] + 72,
                       "量程 0~%s %s，最小分度 %s %s" % (_show_number(top), item["unit"],
                                                          _show_number(item["division"]),
                                                          item["unit"]), 12, "middle", "#7b8794"))
    parts.append("</svg>")
    return "".join(parts)


def _ruler_figure(truth):
    """刻度尺：毫米刻度 + 厘米数字 + 被测物。"""
    width, height = FIGURES["ruler"]
    parts = [_svg(width, height)]
    parts.append('<rect x="30" y="40" width="920" height="120" rx="6" fill="#f8fafc" '
                 'stroke="#c3ccd8"/>')
    left, base_y = 60, 70
    for mm in range(0, 141):
        x = left + mm * 6
        if mm % 10 == 0:
            length, stroke = 26, 1.8
        elif mm % 5 == 0:
            length, stroke = 16, 1.2
        else:
            length, stroke = 9, 0.9
        parts.append('<line x1="%.1f" y1="%d" x2="%.1f" y2="%d" stroke="#3a4652" '
                     'stroke-width="%.1f"/>' % (x, base_y, x, base_y + length, stroke))
        if mm % 10 == 0:
            parts.append(_text(x, base_y - 8, str(mm // 10), 14))
    object_width = truth["value"] * 6
    parts.append('<rect x="%d" y="%d" width="%.1f" height="22" rx="3" fill="#dce7ff" '
                 'stroke="#4d6bfe" stroke-width="2"/>' % (left, base_y + 46, object_width))
    parts.append(_text(left + object_width / 2, base_y + 86, "被测物体", 12, "middle", "#2b4acb"))
    parts.append(_text(width / 2, height - 14,
                       "刻度尺：最小分度 1 mm（每 10 mm 一个数字），要估读到 0.1 mm",
                       12, "middle", "#7b8794"))
    parts.append("</svg>")
    return "".join(parts)


def figure(kind, params):
    """把仪器状态画成 SVG（老师照图读数；图上**不写答案分解式**）。"""
    spec(kind)
    truth = reading(kind, params)
    if kind.startswith("caliper"):
        return _caliper_figure(kind, truth)
    if kind == "micrometer":
        return _micrometer_figure(truth)
    if kind == "ruler":
        return _ruler_figure(truth)
    return _meter_figure(kind, truth)


def score(kind, expected, answer):
    """判分 + 给一句讲解（界面直接用）。"""
    result = judge(kind, expected, answer)
    item = spec(kind)
    result["hint"] = item["note"]
    result["expected_text"] = format_reading(kind, expected)
    return result


def round_value(kind, value):
    """把任意真值对齐到该仪器能读出的步长（出题/画图前先对齐）。"""
    item = spec(kind)
    step = item["step"]
    return round(round(float(value) / step) * step, 6)


def check_reading(kind, value):
    """给一个读数判断能不能被这台仪器读出来（小于半格就认为读不出来）。"""
    item = spec(kind)
    if value < -1e-9 or value > RANGES[kind][1] + 1e-9:
        return False
    return abs(round(value / item["step"]) * item["step"] - value) < 1e-9


def uncertainty(kind):
    """一次读数允许的误差范围（判分窗口）——测微器是 ±0.002 mm 这种。"""
    item = spec(kind)
    return item.get("tolerance", item["step"] / 2.0)


def as_number(text):
    """把'5.820 mm'这种文本还原成数（老师可能带单位粘进来）。"""
    cleaned = "".join(char for char in str(text) if char.isdigit() or char in ".-")
    try:
        return float(cleaned)
    except ValueError:
        return None


def nice_angle(kind, value):
    """电表指针角度（画图用）：量程内线性映射到 -180°~0°。"""
    top = RANGES[kind][1]
    return -180 + 180 * max(0.0, min(1.0, float(value) / top))


def nearest_division(kind, value):
    """指针落在第几小格（老师讲题时会说'指在第 21 小格'）。"""
    return float(value) / spec(kind)["division"]


assert math.isfinite(uncertainty("micrometer"))
