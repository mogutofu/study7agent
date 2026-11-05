# -*- coding: utf-8 -*-
"""物理练习模式（M7）：四类题型的出题、判分与错题本记录。

四类题型（都从已有的引擎出题，不另造一套答案）：
  * `reading` 仪器读数——`instruments.py` 出仪器状态，判分按高考估读口径；
  * `calc`    模型计算题——`physics.py` 的三个模型，取一个关键量当答案；
  * `fill`    过程填空——同一个模型，一次填 2~3 个空（共速时刻、相对滑动、生热…）；
  * `graph`   图像读数——给一条曲线的点，读某个横坐标处的值 / 求斜率。

为什么"从引擎出题"：老师要的是"答案和模型算出来的一模一样"。题目里带 `figure`（仪器图）
或 `chart`（曲线点），界面只负责画；判分把每个空的答案、误差限、该写几位都带着，
所以前端不做物理，只做录入和展示。
"""

import math
import random

import instruments
import sim2d
import physics


class PracticeError(ValueError):
    """题型/模型不认识，或者作答内容不对——直接说清楚。"""


#: 每种题型能问的量（label / 从 values 里取哪个键 / 单位 / 误差限）
CALC_BLANKS = {
    "belt": [
        ("time_to_belt", "物块与带共速的时刻", "s", 0.02),
        ("place_at_belt", "共速时物块的位置", "m", 0.02),
        ("time_total", "物块到达另一端的总时间", "s", 0.02),
        ("heat", "全程摩擦生热", "J", 0.05),
        ("slip_distance", "相对滑动路程", "m", 0.02),
    ],
    "efield": [
        ("offset", "粒子离开电场时的偏移量", "m", 0.0002),
        ("offset_end", "打到屏上的位置（离中线）", "m", 0.0005),
        ("angle", "出射时的偏转角（度）", "°", 0.1),
        ("time_plate", "在板间飞行的时间", "s", 1e-11),
    ],
    "circuit": [
        ("power_max", "R2 上能达到的最大功率", "W", 0.02),
        ("power_max_at", "功率最大时的 R2 阻值", "Ω", 0.05),
        ("current_max", "R2=0 时的电流", "A", 0.01),
        ("voltage_max", "R2 最大时的电压表读数", "V", 0.02),
    ],
}

TYPES = [
    {"key": "reading", "name": "仪器读数", "note": "游标卡尺 / 螺旋测微器 / 电表 / 刻度尺"},
    {"key": "calc", "name": "模型计算题", "note": "传送带、类平抛、电路动态分析各取一个量"},
    {"key": "fill", "name": "过程填空", "note": "同一个模型一次填 2~3 个空"},
    {"key": "graph", "name": "图像读数", "note": "从 v–t / 轨迹图上读数"},
    {"key": "example", "name": "例子题（沙盒/电路）", "note": "从现成例子里出题，答案由引擎算"},
]


def types():
    return [dict(item) for item in TYPES]


def _number(value, digits=4):
    return round(float(value) + 0.0, digits)


def new_question(qtype, seed=None, kind=None, model=None):
    """出一道题。seed 相同 -> 同一道题（自检和重做都用得上）。"""
    qtype = str(qtype or "reading")
    rng = random.Random(seed)
    if qtype == "reading":
        return _reading_question(kind or _pick(kinds(), rng), rng)
    if qtype in ("calc", "fill"):
        return _model_question(model or _pick(["belt", "efield", "circuit"], rng), rng,
                               qtype == "fill")
    if qtype == "graph":
        return _graph_question(rng)
    if qtype == "example":
        # 界面上切题型时 kind 可能还留着上一个题型的东西（比如读数题的仪器名），
        # 不是例子 key 就当没给，随机挑一道——不能让老师看到"没有这道例子题"
        keys = [item["key"] for item in EXAMPLE_QUESTIONS]
        chosen = str(kind or "").strip()
        return _example_question(chosen if chosen in keys else _pick(keys, rng), rng)
    raise PracticeError("没有这种题型：%s（可选：reading / calc / fill / graph / example）"
                        % qtype)


def kinds():
    return ["caliper20", "micrometer", "ammeter06", "voltmeter15", "ruler"]


def _pick(items, rng):
    return items[rng.randrange(0, len(items))]


# ---------------------------------------------------------------- 例子题（M8）

#: 每道例子题：拿沙盒/电路里的现成例子跑一遍引擎，把**引擎算出来的量**当答案。
#: `ask` 里的数字都是引擎返回的，不是写死的——换个例子参数答案就跟着变。
EXAMPLE_QUESTIONS = [
    {"key": "belt_time", "name": "传送带：多快能同速",
     "example": "belt", "unit": "s",
     "prompt": "把物块从静止放到传送带上（皮带 2 m/s、μ=0.25）：物块经过多长时间才和皮带同速？",
     "digits": 2, "tolerance": 0.06},
    {"key": "incline_speed", "name": "斜面：某时刻的速度",
     "example": "incline", "unit": "m/s",
     "prompt": "物块从斜面顶端由静止下滑（μ=0.2、g=9.8 m/s²）：1.7 s 末它的速度大小是多少？",
     "digits": 2, "tolerance": 0.1},
    {"key": "projectile_range", "name": "平抛：落地时的水平位移",
     "example": "projectile", "unit": "m",
     "prompt": "小球从 2.6 m 高处水平抛出（vx=2 m/s、g=9.8 m/s²）：落到地面（y=0）时的水平位移多大？",
     "digits": 2, "tolerance": 0.12},
    {"key": "collision_after", "name": "碰撞：碰后乙的速度",
     "example": "collision", "unit": "m/s",
     "prompt": "1 kg 的甲以 2 m/s 撞上静止的 2 kg 乙（完全弹性）：碰后乙的速度多大？",
     "digits": 2, "tolerance": 0.12},
]


def _belt_time(scene):
    """物块速度第一次达到皮带速度的时刻（引擎的帧里找）。"""
    belt = 2.0
    for frame in sim2d.run(scene)["frames"]:
        if frame["bodies"][0][2] >= belt - 0.01:
            return frame["t"]
    raise PracticeError("这个例子没跑出结果")


def _speed(frame, index=0):
    """速度**大小**（不是 vx 分量——斜面/斜着走的题上这两个差很多）。"""
    slot = frame["bodies"][index]
    return math.hypot(slot[2], slot[3])


def _final_speed(scene):
    frames = sim2d.run(scene)["frames"]
    return _speed(frames[-1])


def _range(scene):
    """落到地面（y=0）那一刻的水平位移：在跨越 y=0 的两帧之间线性插值。"""
    frames = sim2d.run(scene)["frames"]
    start_x = frames[0]["bodies"][0][0]
    previous = frames[0]
    for frame in frames:
        y = frame["bodies"][0][1]
        if y <= 0:
            before = previous["bodies"][0][1]
            span = (before - y) or 1.0
            ratio = (before - 0.0) / span
            x = previous["bodies"][0][0] + ratio * (frame["bodies"][0][0] - previous["bodies"][0][0])
            return x - start_x
        previous = frame
    raise PracticeError("这个例子里小球还没落到地面")


def _second_body(scene):
    """碰后**紧接着**那一帧的速度：不能用最后一帧（那时候乙早就滑走了）。
    碰撞时刻从引擎的事件里拿（kind='bump'）。"""
    result = sim2d.run(scene)
    moments = [item["t"] for item in (result.get("events") or []) if item.get("kind") == "bump"]
    if not moments:
        raise PracticeError("这个例子里没有发生碰撞")
    moment = moments[0]
    # 只看**还在轨道上**的帧：滑出轨道之后是自由落体，速度会一直涨，不是碰后的速度
    after = [_speed(frame, 1) for frame in result["frames"]
             if frame["t"] >= moment and frame["bodies"][1][4] == 1]
    if not after:
        raise PracticeError("碰撞之后没有帧了")
    return max(after)


EXAMPLE_SOLVERS = {"belt_time": _belt_time, "incline_speed": _final_speed,
                   "projectile_range": _range, "collision_after": _second_body}


def _example_question(key, rng):
    item = None
    for row in EXAMPLE_QUESTIONS:
        if row["key"] == key:
            item = row
    if item is None:
        raise PracticeError("没有这道例子题：%s" % key)
    scene = sim2d.example(item["example"])["scene"]
    value = EXAMPLE_SOLVERS[item["key"]](scene)
    return {
        "type": "example", "kind": item["key"], "title": "例子题：%s" % item["name"],
        "prompt": item["prompt"],
        "figure": sim2d.scene_figure(scene),      # 左边出张场景图（老师："好歹出个图意思意思"）
        "chart": None,
        "blanks": [{"key": "a", "label": "答案", "unit": item["unit"],
                    "answer": _number(value, 3), "text": _show(value),
                    "tolerance": item["tolerance"], "digits": item["digits"]}],
        "hint": "提示：可以在物理沙盒里把「%s」这个例子装出来跑一遍，图像就能看到。"
                % item["example"],
        "params": {"example": item["example"]}, "seed": None,
    }


# ---------------------------------------------------------------- 四种题型

def _reading_question(kind, rng):
    question = instruments.drill(kind, seed=rng.randrange(1 << 30))
    item = instruments.spec(kind)
    return {
        "type": "reading", "kind": kind, "title": "读数：%s" % item["name"],
        "prompt": "读出下面仪器的示数（按高考要求估读，单位 %s）。" % item["unit"],
        "figure": question["figure"], "chart": None,
        "blanks": [{"key": "a", "label": "读数", "unit": item["unit"],
                    "answer": question["answer"], "text": question["text"],
                    "tolerance": item["step"] / 2, "digits": item["digits"],
                    "instrument": kind}],
        "hint": item["note"], "params": question["params"], "seed": None,
    }


def _model_question(model, rng, many):
    spec = _model_params(model, rng)
    result = physics.simulate(model, spec["params"])
    values = result["values"]
    names = [item for item in CALC_BLANKS[model] if values.get(item[0]) is not None]
    chosen = names[:3] if many else [names[rng.randrange(0, len(names))]]
    blanks = []
    for index, (key, label, unit, tolerance) in enumerate(chosen):
        blanks.append({"key": "abcdef"[index], "label": label, "unit": unit,
                       "answer": _number(values[key], 6), "tolerance": tolerance,
                       "digits": 3, "instrument": None})
    model_names = {"belt": "传送带", "efield": "带电粒子的类平抛", "circuit": "直流电路动态分析"}
    return {
        "type": "fill" if many else "calc", "kind": model,
        "title": "计算：%s" % model_names[model],
        "prompt": spec["prompt"], "figure": result.get("figure"),
        "chart": (result["charts"] or [None])[0],
        "blanks": blanks, "hint": spec["hint"], "params": spec["params"], "seed": None,
    }


def _model_params(model, rng):
    """给模型配一组"像课本例题"的参数，并写下题干（参数必须出现在题干里）。"""
    if model == "belt":
        length = rng.choice([4.0, 5.0, 6.0])
        speed_belt = rng.choice([2.0, 3.0, 4.0])
        mu = rng.choice([0.1, 0.2, 0.3])
        params = {"length": length, "angle": 0, "mu": mu, "mass": 1.0,
                  "belt_speed": speed_belt, "belt_dir": "up", "start": "bottom", "speed": 0}
        return {"params": params, "hint": "先判共速（μ 与带速），共速前滑动摩擦向前",
                "prompt": "水平传送带长 %.0f m，以 %.0f m/s 匀速运动；把质量 1 kg 的物块轻放在左端，"
                          "物块与带之间的动摩擦因数 μ=%.2f。求（g 取 9.8 m/s²）。"
                          % (length, speed_belt, mu)}
    if model == "efield":
        voltage = rng.choice([100, 200, 300])
        params = {"voltage": voltage, "gap": 0.02, "plate": 0.1, "v0": 2e7,
                  "particle": "electron", "screen": 0.2}
        return {"params": params, "hint": "水平匀速、竖直匀加速；先看会不会打在板上",
                "prompt": "电子以 2.0×10⁷ m/s 沿两板中线射入，板长 0.1 m、板间距 0.02 m，"
                          "偏转电压 %d V，板右端到屏 0.2 m。求（电子质量 9.1×10⁻³¹ kg、"
                          "电量 1.6×10⁻¹⁹ C）。" % voltage}
    params = {"emf": 12.0, "r": 1.0, "r1": 2.0, "r2max": 10.0, "steps": 41}
    return {"params": params, "hint": "P2 = ε²R2/(r+R1+R2)²，配方后 R2 = r + R1 时最大",
            "prompt": "电源电动势 12 V、内阻 1 Ω，与定值电阻 2 Ω、滑动变阻器（0~10 Ω）串联，"
                      "电压表并接在滑动变阻器两端。求。"}


def _graph_question(rng):
    """图像读数：给一条 v–t 曲线，读某个时刻的速度（或求加速度）。"""
    speed0 = rng.choice([0.0, 1.0, 2.0])
    acceleration = rng.choice([1.0, 1.5, 2.0, -1.0])
    duration = rng.choice([2.0, 3.0, 4.0])
    points = []
    step = 0
    while step <= 40:
        moment = duration * step / 40.0
        value = speed0 + acceleration * moment
        if value < 0:
            break
        points.append([round(moment, 3), round(value, 3)])
        step += 1
    ask_speed = rng.random() < 0.5
    if ask_speed:
        index = rng.randrange(6, len(points) - 2)
        moment = points[index][0]
        answer = points[index][1]
        blank = {"key": "a", "label": "t = %.1f s 时的速度" % moment, "unit": "m/s",
                 "answer": answer, "tolerance": 0.05, "digits": 2, "instrument": None}
        hint = "在横轴上找到该时刻，往上看曲线，再往左读纵轴"
    else:
        blank = {"key": "a", "label": "图线的斜率（加速度）", "unit": "m/s²",
                 "answer": round(acceleration, 3), "tolerance": 0.08, "digits": 2,
                 "instrument": None}
        hint = "v–t 图的斜率就是加速度（取直线上两点算 Δv/Δt）"
    return {
        "type": "graph", "kind": "vt",
        "title": "图像读数：v–t 图",
        "prompt": "下图是某物体运动的 v–t 图像，请读数。",
        "figure": None,
        "chart": {"name": "速度随时间变化", "x": "t / s", "y": "v / (m/s)", "points": points},
        "blanks": [blank], "hint": hint, "params": {}, "seed": None,
    }


# ---------------------------------------------------------------- 判分

def grade(question, answers):
    """按空判分。answers 是 {key: 文本}；每个空给"对不对 + 为什么"。

    仪器读数沿用 `instruments.judge`（估读位数也算分），模型题按误差限比数值。
    """
    if not isinstance(question, dict) or not question.get("blanks"):
        raise PracticeError("没有题目可判")
    answers = answers or {}
    items = []
    right = 0
    for blank in question["blanks"]:
        raw = answers.get(blank["key"], answers.get(blank["key"], ""))
        text = _clean(raw)
        if blank.get("instrument"):
            result = instruments.judge(blank["instrument"], blank["answer"], text)
            ok, reason = result["ok"], result["reason"]
        elif not text:
            ok, reason = False, "这个空还空着"
        else:
            value = instruments.as_number(text)
            if value is None:
                ok, reason = False, "要填数字"
            else:
                ok = abs(value - blank["answer"]) <= blank["tolerance"] + 1e-9
                reason = ("对了（%s %s，允许 ±%s）"
                          % (_show(blank["answer"]), blank["unit"], blank["tolerance"])
                          if ok else
                          "数值不对：正确结果约 %s %s（允许 ±%s）"
                          % (_show(blank["answer"]), blank["unit"], blank["tolerance"]))
        if ok:
            right += 1
        items.append({"key": blank["key"], "label": blank["label"], "answer": blank["answer"],
                      "text": blank.get("text") or _show(blank["answer"]),
                      "unit": blank["unit"], "ok": ok, "reason": reason,
                      "yours": text})
    total = len(question["blanks"])
    return {"ok": right == total, "right": right, "total": total,
            "score": round(100.0 * right / total, 1) if total else 0.0,
            "items": items, "hint": question.get("hint") or ""}


def _clean(value):
    return str(value if value is not None else "").strip()


def _show(value):
    text = ("%.6f" % float(value)).rstrip("0").rstrip(".")
    return text or "0"


def mistake_text(question, result):
    """把一道错题写成错题本能用的那种一条记录。"""
    wrong = [item for item in result["items"] if not item["ok"]]
    if not wrong:
        return None
    detail = "；".join("%s：你填 %s，应为 %s %s（%s）"
                       % (item["label"], item["yours"] or "（空）", item["text"], item["unit"],
                          item["reason"]) for item in wrong)
    return {"subject": "物理", "topic": _topic(question),
            "question": question.get("prompt") or question.get("title") or "物理题",
            "answer": "；".join("%s = %s %s" % (item["label"], item["text"], item["unit"])
                               for item in result["items"]),
            "reason": detail, "source": "练习模式"}


def _topic(question):
    mapping = {"reading": "仪器读数", "calc": "物理计算", "fill": "物理过程", "graph": "图像读数"}
    base = mapping.get(question.get("type"), "物理")
    names = {"caliper10": "游标卡尺", "caliper20": "游标卡尺", "caliper50": "游标卡尺",
             "micrometer": "螺旋测微器", "ammeter06": "电流表", "ammeter3": "电流表",
             "voltmeter3": "电压表", "voltmeter15": "电压表", "ruler": "刻度尺",
             "belt": "传送带", "efield": "类平抛", "circuit": "电路动态分析", "vt": "v–t 图像"}
    extra = names.get(question.get("kind"))
    return "%s·%s" % (base, extra) if extra else base


def grade_and_record(question, answers, tools=None):
    """判分；错了就顺手记进错题本（tools 给了才记）。"""
    result = grade(question, answers)
    result["recorded"] = False
    if tools is not None and not result["ok"]:
        import classroom   # 放在函数里：practice 不依赖 classroom 也能单独测

        entry = mistake_text(question, result)
        if entry:
            try:
                classroom.mistake_book(tools, {"action": "add", "items": [entry]})
                result["recorded"] = True
            except Exception as exc:                       # noqa: BLE001 - 记不上不能影响判分
                result["record_error"] = str(exc)
    return result


def accuracy_stats(records):
    """简单正确率统计（老师问"练得怎么样"时用）。"""
    if not records:
        return {"count": 0, "right": 0, "accuracy": 0.0}
    right = sum(1 for item in records if item.get("ok"))
    return {"count": len(records), "right": right,
            "accuracy": round(100.0 * right / len(records), 1)}


assert math.isfinite(sum(item[3] for item in CALC_BLANKS["belt"]))
