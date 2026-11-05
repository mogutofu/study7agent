# -*- coding: utf-8 -*-
"""物理平面引擎（M6）：把一道题里的整条过程**一次算完**，交给界面播放。

为什么在服务端算：高中物理的过程都是分段的闭式解（每段加速度恒定 / 匀速 / 匀加速），
服务端一次算出分段结果和轨迹采样点，界面只负责画——这样"拖时间轴、定格、按段跳"
都不用前端自己积分，也不会因为前端算错而对不上答案。

三个模型（老师定的）：
  * `belt`    传送带：水平/倾斜、共速判定、相对位移与摩擦生热
  * `efield`  带电粒子在匀强电场中的类平抛：偏转量、出射角、屏上落点、会不会撞板
  * `circuit` 直流电路动态分析：滑动变阻器滑动时各表读数、功率极值与曲线

约定：单位一律 SI（m、s、kg、N、V、A、Ω、C），角度输入用**度**。
返回结构（三个模型一致）：
    {"ok": True, "title": …, "scene": {primitives: [...]},
     "phases": [...], "frames": [...], "cards": [...], "values": {...}, "charts": [...]}
界面不认识物理，只按 `scene` 画元件、按 `frames` 动物块、把 `cards` 当"过程卡"列出。
"""

import math

import schematics

G = 9.8
MAX_PHASES = 8                # 分段求解的最多段数（再多就是模型没配好，别死循环）
FRAME_COUNT = 61              # 轨迹采样点数（含首末）


class PhysicsError(ValueError):
    """参数不合法（负长度、0 质量之类）——直接说清楚，别画出一张假图。"""


def _num(params, key, default, low, high, unit=""):
    """取一个数并做范围检查：坏参数必须报错，不能拿默认值糊过去。"""
    raw = (params or {}).get(key, default)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise PhysicsError("%s 要是数字" % key)
    if value != value or value in (float("inf"), float("-inf")):
        raise PhysicsError("%s 不是有效数字" % key)
    if value < low or value > high:
        raise PhysicsError("%s 要在 %s ~ %s%s 之间" % (key, low, high, unit))
    return value


def models():
    """三个模型各自的参数表单（界面照这个生成输入框，别在前端再抄一份默认值）。"""
    return [
        {"key": "belt", "name": "传送带（水平/倾斜）",
         "params": [
             {"key": "length", "label": "传送带长度", "unit": "m", "default": 4.0, "low": 0.1, "high": 100},
             {"key": "angle", "label": "倾角", "unit": "°", "default": 0, "low": -40, "high": 40},
             {"key": "mu", "label": "动摩擦因数", "unit": "", "default": 0.2, "low": 0, "high": 2},
             {"key": "mass", "label": "物块质量", "unit": "kg", "default": 1.0, "low": 0.01, "high": 1000},
             {"key": "belt_speed", "label": "带速", "unit": "m/s", "default": 2.0, "low": 0, "high": 100},
             {"key": "belt_dir", "label": "带速方向（up=沿斜面向上/向右，down=向下/向左）",
              "default": "up", "choices": ["up", "down"]},
             {"key": "start", "label": "物块放在哪端（bottom=底端/左端，top=顶端/右端）",
              "default": "bottom", "choices": ["bottom", "top"]},
             {"key": "speed", "label": "物块初速度（沿正方向，可负）", "unit": "m/s",
              "default": 0.0, "low": -100, "high": 100},
         ]},
        {"key": "efield", "name": "带电粒子的类平抛（匀强电场）",
         "params": [
             {"key": "voltage", "label": "偏转电压 U", "unit": "V", "default": 200, "low": 0, "high": 100000},
             {"key": "gap", "label": "板间距 d", "unit": "m", "default": 0.02, "low": 0.0001, "high": 10},
             {"key": "plate", "label": "板长 L", "unit": "m", "default": 0.1, "low": 0.0001, "high": 100},
             {"key": "v0", "label": "入射速度 v0", "unit": "m/s", "default": 20000000.0,
              "low": 1, "high": 1e9},
             {"key": "particle", "label": "粒子",
              "default": "electron", "choices": ["electron", "proton", "alpha"]},
             {"key": "screen", "label": "板右端到屏的距离 D", "unit": "m", "default": 0.2,
              "low": 0, "high": 100},
         ]},
        {"key": "circuit", "name": "直流电路动态分析（滑动变阻器）",
         "params": [
             {"key": "emf", "label": "电源电动势 E", "unit": "V", "default": 12.0, "low": 0.1, "high": 1000},
             {"key": "r", "label": "内阻 r", "unit": "Ω", "default": 1.0, "low": 0, "high": 1000},
             {"key": "r1", "label": "定值电阻 R1", "unit": "Ω", "default": 2.0, "low": 0, "high": 10000},
             {"key": "r2max", "label": "滑动变阻器最大阻值", "unit": "Ω", "default": 10.0,
              "low": 0.01, "high": 10000},
             {"key": "steps", "label": "滑片取几个位置", "unit": "", "default": 41, "low": 3, "high": 201},
         ]},
    ]


def simulate(key, params=None):
    """按模型名求解；不认识的模型直接报错（界面按 error 显示，不要空白）。"""
    builders = {"belt": belt, "efield": efield, "circuit": circuit}
    builder = builders.get(str(key or "").strip())
    if builder is None:
        raise PhysicsError("没有这个模型：%s（可选：%s）" % (key, "、".join(sorted(builders))))
    result = builder(params or {})
    result["ok"] = True
    result["key"] = str(key)
    return result


# ---------------------------------------------------------------- 传送带

def belt(params):
    """传送带：分段闭式解 + 共速判定 + 相对位移与摩擦生热。

    正方向 = 沿斜面向上（水平带就是向右）；物块从 x=0 出发，带另一端在 x=L。
    每段加速度都是常数，所以每段都能解析积分；相对滑动量 = 带走的距离 - 物块走的距离，
    摩擦生热 Q = μmg·cosθ·Σ|Δs_rel| —— 这正是学生最容易错的地方，所以单独给出来。
    """
    length = _num(params, "length", 4.0, 0.1, 100, " m")
    angle = math.radians(_num(params, "angle", 0, -40, 40, "°"))
    mu = _num(params, "mu", 0.2, 0, 2)
    mass = _num(params, "mass", 1.0, 0.01, 1000, " kg")
    speed_belt = _num(params, "belt_speed", 2.0, 0, 100, " m/s")
    if str(params.get("belt_dir") or "up") == "down":
        speed_belt = -speed_belt
    speed = _num(params, "speed", 0.0, -100, 100, " m/s")
    at_top = str(params.get("start") or "bottom") == "top"

    gs = G * math.sin(angle)                 # 重力沿斜面向下的分量
    gc = G * math.cos(angle)
    phases = []
    time = 0.0
    place = length if at_top else 0.0        # 从顶端放就从 x=L 开始
    velocity = speed
    slip_total = 0.0
    common = None                      # 第一次共速的时刻与位置

    for _ in range(MAX_PHASES):
        relative = speed_belt - velocity
        if abs(relative) < 1e-9:
            # 已经相对静止：能不能一起走，看静摩擦够不够（μmg·cosθ ≥ mg·sinθ，水平带恒成立）
            if abs(gs) <= mu * gc + 1e-12:
                if abs(velocity) < 1e-9:
                    break                                   # 带也停着，物块就停在这儿
                end = length if velocity > 0 else 0.0
                span = (end - place) / velocity
                phases.append(_belt_phase("共速后随带匀速", time, time + span, 0.0,
                                          velocity, velocity, place, end, 0.0))
                time, place = time + span, end
                break
            # 静摩擦不够：物块相对带向下滑，摩擦沿斜面向上
            accel = -gs + mu * gc
        else:
            accel = -gs + (1.0 if relative > 0 else -1.0) * mu * gc

        # 这一段可能的结束原因：追上带速、走到端点、或者滑出去
        stops = []
        if abs(accel) > 1e-12 and relative * accel > 0:
            cross = relative / accel               # v(t) = v + a·t 追上带速的时刻
            if cross > 1e-9:
                stops.append(cross)
        for target in (length, 0.0):
            for root in _roots(0.5 * accel, velocity, place - target):
                if root > 1e-9:
                    stops.append(root)
        if not stops:
            # 走到这儿说明物块停不住也出不去（比如放在底端、μ<tanθ，还没动就往下滑）
            if phases:
                break
            phases.append(_belt_phase("物块在起点就待不住，直接滑出带面", 0.0, 0.0, accel,
                                      velocity, velocity, place, place, 0.0))
            break
        span = min(stops)
        next_velocity = velocity + accel * span
        next_place = place + velocity * span + 0.5 * accel * span * span
        if next_place > length - 1e-9:
            next_place = length
        if next_place < 1e-9:
            next_place = 0.0
        # 相对位移：这一段带比物块多走的距离（带也可能"少走"，所以取绝对值计入发热）
        slip = speed_belt * span - (next_place - place)
        slip_total += abs(slip)
        note = _belt_note(angle, mu, relative, accel)
        phases.append(_belt_phase(note, time, time + span, accel, velocity, next_velocity,
                                  place, next_place, slip))
        time, place, velocity = time + span, next_place, next_velocity
        if abs(velocity - speed_belt) < 1e-6 and common is None:
            common = (time, place)
            velocity = speed_belt
        if place >= length - 1e-9 or place <= 1e-9:
            break

    heat = mu * mass * G * math.cos(angle) * slip_total
    reached = "top" if place >= length - 1e-9 else ("bottom" if place <= 1e-9 else "still")
    cards = [_belt_card(phase, mass, mu, angle) for phase in phases]
    values = {
        "time_total": _round(time), "distance": _round(place), "speed_end": _round(velocity),
        "reached": reached, "slip_distance": _round(slip_total), "heat": _round(heat),
        "time_to_belt": _round(common[0]) if common else None,
        "place_at_belt": _round(common[1]) if common else None,
        "block_kinetic": _round(0.5 * mass * velocity * velocity),
    }
    frames = _belt_frames(phases, time, speed_belt, length)
    charts = [
        {"name": "速度 v 随时间变化", "x": "t / s", "y": "v / (m/s)",
         "points": [[frame["t"], frame["v"]] for frame in frames]},
        {"name": "位移 x 随时间变化", "x": "t / s", "y": "x / m",
         "points": [[frame["t"], frame["x"]] for frame in frames]},
    ]
    scene = _belt_scene(length, angle, mu, mass, speed_belt, speed, place)
    return {"title": "传送带", "scene": scene, "phases": phases, "frames": frames,
            "cards": cards, "values": values, "charts": charts, "params": params,
            "figure": schematics.belt_diagram(params)}


def _belt_phase(note, t0, t1, accel, v0, v1, x0, x1, slip):
    return {"note": note, "t0": _round(t0), "t1": _round(t1), "accel": _round(accel),
            "v0": _round(v0), "v1": _round(v1), "x0": _round(x0), "x1": _round(x1),
            "slip": _round(slip)}


def _belt_note(angle, mu, relative, accel):
    """这一段的物理描述：摩擦朝哪、物块是在加速还是减速（过程卡要讲的就是这句）。"""
    if angle == 0:
        return "滑动摩擦向前，物块加速" if relative > 0 else "滑动摩擦向后，物块减速"
    where = "沿斜面向上" if relative > 0 else "沿斜面向下"
    return "相对带%s滑，摩擦力%s，%s" % ("向上" if relative > 0 else "向下", where,
                                        "加速" if accel > 0 else "减速")


def _belt_card(phase, mass, mu, angle):
    friction = mu * mass * G * math.cos(angle)
    return {
        "title": phase["note"],
        "law": "牛顿第二定律（沿斜面方向）",
        "forces": "重力分量 mg·sinθ=%s N，摩擦力 μmg·cosθ=%s N" % (_round(mass * G * math.sin(angle)),
                                                                  _round(friction)),
        "equations": "a = (%s) / m = %s m/s²；Δv = aΔt" % (_round(phase["accel"] * mass),
                                                          phase["accel"]),
        "result": "%s s → %s s：v %s → %s m/s，位移 %s → %s m%s" % (
            phase["t0"], phase["t1"], phase["v0"], phase["v1"], phase["x0"], phase["x1"],
            "，相对滑动 %s m" % phase["slip"] if abs(phase["slip"]) > 1e-9 else ""),
    }


def _belt_scene(length, angle, mu, mass, speed_belt, speed, end):
    """平面图元：带（线）、两端转轮（圆）、物块（矩形）与箭头（速度/带速方向）。"""
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    primitives = [
        {"kind": "line", "from": [0.0, 0.0], "to": [length * cos_a, length * sin_a],
         "role": "belt"},
        {"kind": "circle", "center": [0.0, 0.0], "radius": 0.08, "role": "pulley"},
        {"kind": "circle", "center": [length * cos_a, length * sin_a], "radius": 0.08,
         "role": "pulley"},
    ]
    if angle != 0:
        primitives.append({"kind": "line", "from": [0.0, 0.0],
                           "to": [length * cos_a, 0.0], "role": "ground", "dashed": True})
        primitives.append({"kind": "label", "at": [length * cos_a * 0.5, -0.18],
                           "text": "θ=%s°" % _round(math.degrees(angle))})
    primitives.append({"kind": "arrow", "from": [length * cos_a * 0.5, sin_a * length * 0.5],
                       "to": [length * cos_a * 0.5 + 0.5 * math.copysign(1, speed_belt or 1) * cos_a,
                              sin_a * length * 0.5 + 0.5 * math.copysign(1, speed_belt or 1) * sin_a],
                       "role": "belt_speed"})
    primitives.append({"kind": "label", "at": [length * cos_a * 0.5, sin_a * length * 0.5 + 0.3],
                       "text": "带速 %s m/s" % abs(_round(speed_belt))})
    primitives.append({"kind": "block", "at": [end * cos_a, end * sin_a], "size": [0.3, 0.2],
                       "role": "block", "angle": math.degrees(angle)})
    if speed:
        primitives.append({"kind": "arrow", "from": [0.0, 0.35],
                           "to": [0.6 * math.copysign(1, speed), 0.35], "role": "v0"})
        primitives.append({"kind": "label", "at": [0.3, 0.5], "text": "v0=%s m/s" % _round(speed)})
    return {"unit": "m", "primitives": primitives,
            "note": "带长 %s m，μ=%s，m=%s kg，带速 %s m/s" % (length, mu, mass,
                                                              _round(speed_belt))}


def _belt_frames(phases, time_total, speed_belt, length, count=FRAME_COUNT):
    """按时间采样：每一帧给物块位置/速度/加速度和带上一个标记点（看相对滑动用）。"""
    frames = []
    if not phases or time_total <= 0:
        return frames
    for index in range(count):
        moment = time_total * index / float(count - 1)
        place, velocity, accel = _belt_state(phases, moment)
        marker = (length * 0.5 + speed_belt * moment) % length if speed_belt else length * 0.5
        frames.append({"t": _round(moment), "x": _round(place), "v": _round(velocity),
                       "a": _round(accel), "belt_mark": _round(marker)})
    return frames


def _belt_state(phases, moment):
    for phase in phases:
        if phase["t0"] - 1e-9 <= moment <= phase["t1"] + 1e-9:
            span = moment - phase["t0"]
            place = phase["x0"] + phase["v0"] * span + 0.5 * phase["accel"] * span * span
            return place, phase["v0"] + phase["accel"] * span, phase["accel"]
    last = phases[-1]
    return last["x1"], last["v1"], last["accel"]


# ---------------------------------------------------------------- 类平抛

#: 常见粒子（高中题里就这三种）：电量、质量
PARTICLES = {
    "electron": {"name": "电子", "charge": 1.6e-19, "mass": 9.1e-31},
    "proton": {"name": "质子", "charge": 1.6e-19, "mass": 1.67e-27},
    "alpha": {"name": "α粒子（氦核）", "charge": 3.2e-19, "mass": 6.64e-27},
}


def efield(params):
    """带电粒子垂直进入匀强电场：水平匀速 + 竖直匀加速 = 类平抛。

    先判"会不会打在板上"：y = ½at² 到 d/2 就撞板，撞了就没有出射点，屏上落点也就无从谈起——
    这类题学生最容易漏条件，所以引擎先判。
    """
    voltage = _num(params, "voltage", 200, 0, 100000, " V")
    gap = _num(params, "gap", 0.02, 0.0001, 10, " m")
    plate = _num(params, "plate", 0.1, 0.0001, 100, " m")
    v0 = _num(params, "v0", 2e7, 1, 1e9, " m/s")
    screen = _num(params, "screen", 0.2, 0, 100, " m")
    kind = str(params.get("particle") or "electron")
    if kind not in PARTICLES:
        raise PhysicsError("没有这种粒子：%s" % kind)
    charge = PARTICLES[kind]["charge"]
    mass = PARTICLES[kind]["mass"]
    field = voltage / gap                      # 场强 E = U/d
    force = charge * field
    accel = force / mass
    time_plate = plate / v0
    offset = 0.5 * accel * time_plate ** 2
    half = gap / 2.0
    hit = offset > half + 1e-15
    if hit:                                     # 撞板：解 ½at² = d/2
        time_field = math.sqrt(2 * half / accel) if accel > 0 else 0.0
        offset = half
    else:
        time_field = time_plate
    speed_y = accel * time_field
    angle = math.degrees(math.atan2(speed_y, v0))
    total_offset = offset + (speed_y / v0) * screen if not hit else offset
    cards = [
        {"title": "受力", "law": "匀强电场 F = qE = qU/d",
         "forces": "E = U/d = %s V/m，F = %s N（方向与电场平行）" % (_sci(field), _sci(force)),
         "equations": "a = F/m = %s m/s²" % _sci(accel),
         "result": "粒子质量 %s kg、电量 %s C" % (_sci(mass), _sci(charge))},
        {"title": "运动分解", "law": "类平抛：水平匀速 + 竖直匀加速",
         "forces": "垂直于场的方向不受力，沿场方向恒力",
         "equations": "x = v0·t，y = ½at²",
         "result": "在板间飞行 %s s，竖直位移 %s m，出射竖直速度 %s m/s" % (
             _sci(time_field), _sci(offset), _sci(speed_y))},
    ]
    if hit:
        cards.append({"title": "结论", "law": "打在板上", "forces": "",
                      "equations": "½·a·t² = d/2",
                      "result": "偏移 %s m 已经超过板间距的一半 %s m，粒子打在极板上" % (
                          _sci(offset), _sci(half))})
    else:
        cards.append({"title": "结论", "law": "飞出电场后匀速直线",
                      "equations": "tanθ = v_y/v0，Y = y + tanθ·D",
                      "result": "偏转角 %s°，屏上偏移 %s m（动能增加 %s J）" % (
                          _round(angle), _sci(total_offset), _sci(0.5 * mass * speed_y ** 2))})
        cards[-1]["forces"] = "出板后不受电场力"
    frames = _efield_frames(accel, v0, time_field, screen, hit)
    values = {
        "field": _sci(field), "accel": _sci(accel), "time_plate": _sci(time_plate),
        "offset": _sci(offset), "offset_end": _sci(total_offset), "speed_y": _sci(speed_y),
        "angle": _round(angle), "hit": hit,
        "energy_gain": _sci(0.5 * mass * speed_y ** 2),
        "speed_out": _sci(math.hypot(v0, speed_y)),
    }
    scene = _efield_scene(gap, plate, screen, offset, total_offset, hit, accel, v0, time_field)
    charts = [
        {"name": "竖直偏移 y 随水平位移 x 变化（轨迹）", "x": "x / m", "y": "y / m",
         "points": [[frame["x"], frame["y"]] for frame in frames]},
        {"name": "竖直分速度 v_y 随时间变化", "x": "t / s", "y": "v_y / (m/s)",
         "points": [[frame["t"], frame["vy"]] for frame in frames]},
    ]
    return {"title": "带电粒子的类平抛", "scene": scene, "phases": [
        {"note": "板间：水平匀速 + 竖直匀加速", "t0": 0.0, "t1": _sci(time_field),
         "accel": _sci(accel), "x0": 0.0, "x1": _sci(v0 * time_field),
         "v0": _sci(v0), "v1": _sci(math.hypot(v0, speed_y))},
    ], "frames": frames, "cards": cards, "values": values, "charts": charts,
        "params": params, "figure": schematics.efield_diagram(params)}


def _efield_frames(accel, v0, time_field, screen, hit, count=FRAME_COUNT):
    """轨迹采样：板间抛物线；没撞板就再加上出板到屏的一段直线。"""
    total = time_field + (screen / v0 if not hit else 0.0)
    frames = []
    for index in range(count):
        moment = total * index / float(count - 1)
        if moment <= time_field:
            x = v0 * moment
            y = 0.5 * accel * moment * moment
            vy = accel * moment
        else:
            x = v0 * moment
            y = 0.5 * accel * time_field ** 2 + accel * time_field * (moment - time_field)
            vy = accel * time_field
        frames.append({"t": _sci(moment), "x": _sci(x), "y": _sci(y), "vy": _sci(vy),
                       "vx": _sci(v0)})
    return frames


def _efield_scene(gap, plate, screen, offset, total_offset, hit, accel, v0, time_field):
    half = gap / 2.0
    primitives = [
        {"kind": "line", "from": [0.0, half], "to": [plate, half], "role": "plate"},
        {"kind": "line", "from": [0.0, -half], "to": [plate, -half], "role": "plate"},
        {"kind": "line", "from": [plate + screen, -half * 4], "to": [plate + screen, half * 4],
         "role": "screen"},
    ]
    if not hit:
        primitives.append({"kind": "arrow", "from": [plate + screen, total_offset],
                           "to": [plate + screen, 0.0], "role": "measured"})
    primitives.append({"kind": "label", "at": [plate * 0.5, half * 1.4],
                       "text": "U=%s V，d=%s m" % (_round(gap), _round(gap))})
    primitives.append({"kind": "label", "at": [plate + screen, -half * 4],
                       "text": "屏"})
    return {"unit": "m",
            "primitives": primitives,
            "note": "板长 %s m，出板偏移 %s m%s" % (_sci(plate), _sci(offset),
                                                   "，打在板上" if hit else "")}


# ---------------------------------------------------------------- 直流电路动态分析

def circuit(params):
    """滑动变阻器 R2 从 0 滑到最大值：每个位置的电流、电压、功率都算出来。

    结构固定为"电源 ε(r) + R1 + 滑动变阻器 R2 串联，电压表并接 R2"，这是高考动态分析的标准图。
    功率极值也由表给出：P2 = ε²R2/(r+R1+R2)² 在 R2 = r + R1 处最大（R2 是外电路的一部分，
    只有 R2 上功率取极值时才是这个条件——这点必须写清楚，不然学生套错）。
    """
    emf = _num(params, "emf", 12.0, 0.1, 1000, " V")
    inner = _num(params, "r", 1.0, 0, 1000, " Ω")
    r1 = _num(params, "r1", 2.0, 0, 10000, " Ω")
    r2max = _num(params, "r2max", 10.0, 0.01, 10000, " Ω")
    steps = int(_num(params, "steps", 41, 3, 201))

    rows = []
    for index in range(steps):
        r2 = r2max * index / float(steps - 1)
        current = emf / (inner + r1 + r2)
        rows.append({"r2": _round(r2), "current": _round(current),
                     "voltage": _round(current * r2), "terminal": _round(emf - current * inner),
                     "power": _round(current * current * r2),
                     "power_total": _round(current * current * (inner + r1 + r2))})
    best = max(rows, key=lambda row: row["power"])
    optimum = inner + r1                       # P2 取极大的位置：R2 = r + R1
    if inner + r1 > 1e-12 and optimum <= r2max:
        peak_at = _round(optimum)
        peak = _round(emf * emf / (4 * (inner + r1)))
        inside = True
    elif inner + r1 <= 1e-12:
        peak_at, peak, inside = rows[0]["r2"], rows[0]["power"], False
    else:
        peak_at, peak, inside = best["r2"], best["power"], False
    cards = [
        {"title": "电路结构", "law": "闭合电路欧姆定律",
         "forces": "电源 ε=%s V、内阻 r=%s Ω，与 R1=%s Ω、滑动变阻器 R2 串联" % (emf, inner, r1),
         "equations": "I = ε / (r + R1 + R2)　U2 = I·R2　U端 = ε - I·r",
         "result": "R2 从 0 滑到 %s Ω 时：I 由 %s A 变到 %s A，U2 由 %s V 变到 %s V" % (
             r2max, rows[0]["current"], rows[-1]["current"], rows[0]["voltage"],
             rows[-1]["voltage"])},
        {"title": "功率极值", "law": "P2 = I²R2 = ε²R2/(r+R1+R2)²",
         "forces": "分母是 R2 的二次式，配方后 R2 = r + R1 时取极大",
         "equations": "R2 = r + R1 = %s Ω，此时 P2 = ε²/[4(r+R1)]" % _round(inner + r1),
         "result": ("滑片在 %s Ω 处 R2 上功率最大 %s W" % (peak_at, peak)) if inside else
                   ("r + R1 = %s Ω 超出可调范围（0~%s Ω），全程单调，"
                    "%s Ω 处功率最大 %s W" % (_round(inner + r1), r2max, peak_at, peak))},
    ]
    frames = [{"t": index / float(max(1, steps - 1)), "r2": row["r2"], "current": row["current"],
               "voltage": row["voltage"], "power": row["power"], "terminal": row["terminal"]}
              for index, row in enumerate(rows)]
    charts = [
        {"name": "电流 I 随 R2 变化", "x": "R2 / Ω", "y": "I / A",
         "points": [[row["r2"], row["current"]] for row in rows]},
        {"name": "电压表读数 U2 随 R2 变化", "x": "R2 / Ω", "y": "U2 / V",
         "points": [[row["r2"], row["voltage"]] for row in rows]},
        {"name": "R2 上功率随 R2 变化", "x": "R2 / Ω", "y": "P2 / W",
         "points": [[row["r2"], row["power"]] for row in rows]},
    ]
    values = {"current_min": rows[-1]["current"], "current_max": rows[0]["current"],
              "voltage_min": rows[0]["voltage"], "voltage_max": rows[-1]["voltage"],
              "power_max": peak, "power_max_at": peak_at,
              "inside_range": inside, "rows": rows}
    scene = _circuit_scene(emf, inner, r1, r2max, rows[0])
    return {"title": "直流电路动态分析", "scene": scene,
            "phases": [{"note": "滑片从最左滑到最右，各表读数随之变化",
                        "t0": 0.0, "t1": 1.0, "accel": 0.0, "x0": 0.0, "x1": 1.0,
                        "v0": rows[0]["current"], "v1": rows[-1]["current"]}],
            "frames": frames, "cards": cards, "values": values, "charts": charts,
            "params": params,
            "figure": schematics.circuit_diagram(params, {
                "current": values["current_max"] if values["current_max"] is not None else None,
                "voltage": values["voltage_max"], "r2": values["power_max_at"]})}


def _circuit_scene(emf, inner, r1, r2max, first):
    primitives = [
        {"kind": "label", "at": [0.0, 1.0], "text": "ε=%s V, r=%s Ω" % (emf, inner)},
        {"kind": "label", "at": [1.5, 1.0], "text": "R1=%s Ω" % r1},
        {"kind": "label", "at": [3.0, 1.0], "text": "R2=0~%s Ω" % r2max},
        {"kind": "label", "at": [3.0, 0.2], "text": "电压表并接 R2"},
        {"kind": "line", "from": [0.0, 0.0], "to": [4.0, 0.0], "role": "wire"},
        {"kind": "line", "from": [0.0, 0.0], "to": [0.0, 1.0], "role": "wire"},
        {"kind": "line", "from": [0.0, 1.0], "to": [4.0, 1.0], "role": "wire"},
        {"kind": "line", "from": [4.0, 1.0], "to": [4.0, 0.0], "role": "wire"},
    ]
    return {"unit": "m", "primitives": primitives,
            "note": "R2=0 时 I=%s A，电压表 %s V" % (first["current"], first["voltage"])}


# ---------------------------------------------------------------- 小工具

def _roots(a, b, c):
    """a·t² + b·t + c = 0 的实根（按 t 升序）；a≈0 时退化成一次方程。"""
    if abs(a) < 1e-12:
        if abs(b) < 1e-12:
            return []
        return [-c / b]
    delta = b * b - 4 * a * c
    if delta < 0:
        return []
    root = math.sqrt(delta)
    return sorted([(-b - root) / (2 * a), (-b + root) / (2 * a)])


def _round(value):
    return round(value + 0.0, 4)


def _sci(value):
    """科学计数法字符串（微观量用普通小数会长到没法看）。"""
    if value == 0:
        return "0"
    if 1e-3 <= abs(value) < 1e5:
        return "%s" % _round(value)
    return "%.4g" % value
