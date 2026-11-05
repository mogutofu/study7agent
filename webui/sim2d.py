# -*- coding: utf-8 -*-
"""平面动力学引擎（M6.5）：老师/学生自己画轨道、方场，物块像高考大题那样动起来。

和 `physics.py`（三个固定模型的分段闭式解）的分工：
  * `physics.py` 是"填空题"——模型固定，只改参数；
  * 这个模块是"画板"——场景由用户画：多段轨道（每段自己的动摩擦因数）、竖直圆环、
    匀强电场/磁场区（磁场垂直屏幕，正负表示出/入纸面），物块能设质量与电荷量。
    轨道上的运动用约束动力学（法向力由曲率与外力共同决定，N<0 就离开轨道），
    离开轨道后按重力 + 电场力 + 洛伦兹力做自由运动，再落回轨道就继续滑。

坐标系：x 向右、y 向上，单位米；重力沿 -y。磁场 B 是"垂直屏幕"的分量：
B>0 表示指向纸面外（用 ⊙ 画），B<0 表示指向纸面里（用 ⊗ 画）。

**这是数值积分**（自由段 RK4，轨道段沿弧长积分），所以自检拿"课本有解析解"的题去对：
斜面下滑速度、水平面滑行距离、竖直圆环的临界速度 √(gR)、磁场里的圆周半径 mv/(qB)。
"""

import json
import math

G = 9.8
WORLD_W = 6.0                 # 沙盒世界的宽（m）：跟界面画布一致（6 m x 3 m）
WORLD_H = 3.0                 # 世界的高（m）
MAX_STEPS = 400000            # 硬上限：别让坏场景把 CPU 占死
TOL = 0.02                    # 判定"贴在某条轨道上"的距离容差（米）
LAND_GAP = 0.01               # 离开轨道后至少走这么远才允许"落回"（不然刚滑出尽头又被吸回来）


def _approaching(hit, position, velocity):
    """物块是不是**朝着**轨道去（不是擦着走）——不然同一条轨道会反复判成"落回"。"""
    at, normal = hit[1], hit[3]
    toward = [at[0] - position[0], at[1] - position[1]]
    if velocity[0] * toward[0] + velocity[1] * toward[1] > 0:
        return True
    return velocity[0] * normal[0] + velocity[1] * normal[1] < 0


class SceneError(ValueError):
    """场景不合法（点不够、半径是 0、参数不是数）——直接说清楚，别算出一张假图。"""


# ---------------------------------------------------------------- 场景校验

def _pair(value, name):
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        raise SceneError("%s 要写成 [x, y]" % name)
    try:
        return [float(value[0]), float(value[1])]
    except (TypeError, ValueError):
        raise SceneError("%s 要是数字" % name)


def _positive(value, name, allow_zero=False):
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise SceneError("%s 要是数字" % name)
    if number < 0 or (number == 0 and not allow_zero):
        raise SceneError("%s 要大于 0" % name)
    return number


GRAVITY_DIRS = {"down": [0.0, -1.0], "up": [0.0, 1.0], "left": [-1.0, 0.0], "right": [1.0, 0.0]}


def _gravity(scene):
    """重力：可以写一个数（默认竖直向下 -y）、写 [gx, gy]、或写 {value, dir}。

    方向要能改（老师要的）：斜面题一般竖直向下，但也有人要"斜面上的等效重力"，
    所以这里统一成矢量，下游只认矢量。
    """
    raw = scene.get("g", G)
    if isinstance(raw, (list, tuple)):
        pair = _pair(raw, "重力加速度")
        return [pair[0], pair[1]]
    if isinstance(raw, dict):
        value = float(raw.get("value", G) or 0.0)
        direction = GRAVITY_DIRS.get(str(raw.get("dir") or "down"))
        if direction is None:
            raise SceneError("重力方向只能是 down / up / left / right")
        return [direction[0] * value, direction[1] * value]
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise SceneError("重力加速度要是数字、[gx, gy] 或 {value, dir}")
    return [0.0, -value]


def normalize(scene):
    """把界面递来的场景整成引擎内部用的形状，坏数据在这里就报错。"""
    scene = scene or {}
    tracks = []
    for index, raw in enumerate(scene.get("tracks") or []):
        kind = str(raw.get("kind") or "line")
        if kind == "line":
            points = [_pair(item, "轨道 %d 的点" % (index + 1)) for item in raw.get("points") or []]
            if len(points) < 2:
                raise SceneError("轨道 %d 至少要两个点" % (index + 1))
            mu_raw = raw.get("mu", 0.0)
            if isinstance(mu_raw, (list, tuple)):
                mu = [max(0.0, float(item)) for item in mu_raw]
                while len(mu) < len(points) - 1:
                    mu.append(mu[-1] if mu else 0.0)
            else:
                mu = [max(0.0, float(mu_raw))] * (len(points) - 1)
            tracks.append({"id": str(raw.get("id") or "t%d" % (index + 1)), "kind": "line",
                           "points": points, "mu": mu[:len(points) - 1],
                           "belt": bool(raw.get("belt")),
                           "v_belt": float(raw.get("v_belt") or 0.0)})
        elif kind == "circle":
            radius = _positive(raw.get("radius"), "圆轨道半径")
            center = _pair(raw.get("center"), "圆心")
            mu = max(0.0, float(raw.get("mu", 0.0)))
            tracks.append({"id": str(raw.get("id") or "c%d" % (index + 1)), "kind": "circle",
                           "center": center, "radius": radius, "mu": mu,
                           "belt": bool(raw.get("belt")),
                           "v_belt": float(raw.get("v_belt") or 0.0),
                           "inner": raw.get("inner", True)})
        else:
            raise SceneError("不认识的轨道类型：%s" % kind)
    # 板块 / 轮子 / 弹簧 / 连接体也算"场景里有东西"（轮子纯滚动这种例子可以没有物块）
    linked_gear = any(scene.get(key) for key in ("plates", "wheels", "springs", "links"))
    if not tracks and not (scene.get("fields") or []) and not linked_gear and \
            not any(_pair(item.get("v") or [0, 0], "初速度") != [0.0, 0.0]
                    for item in (scene.get("bodies") or [scene.get("block") or {}])):
        raise SceneError("场景里什么都没有：画一条轨道、摆一个场区，或者给物块一个初速度")

    fields = []
    for index, raw in enumerate(scene.get("fields") or []):
        rect = raw.get("rect")
        if not isinstance(rect, (list, tuple)) or len(rect) < 4:
            raise SceneError("场区 %d 要写成 [x, y, 宽, 高]" % (index + 1))
        box = [float(rect[0]), float(rect[1]), _positive(rect[2], "场区宽度"),
               _positive(rect[3], "场区高度")]
        item = {"id": str(raw.get("id") or "f%d" % (index + 1)), "rect": box}
        if raw.get("E") is not None:
            item["E"] = _pair(raw.get("E"), "电场 E")
        if raw.get("B") is not None:
            item["B"] = float(raw.get("B"))
        if "E" not in item and "B" not in item:
            raise SceneError("场区 %d 既没有电场也没有磁场" % (index + 1))
        fields.append(item)

    raw_bodies = scene.get("bodies")
    if raw_bodies is None:
        # 只写了板/轮子/弹簧/连接体的场景本来就可以没有物块（轮子纯滚动、板块对滑…）
        raw_bodies = [] if linked_gear else [scene.get("block") or {}]
    if not raw_bodies and not linked_gear:
        raise SceneError("至少要有一个物块")
    bodies = []
    for index, item in enumerate(raw_bodies):
        item = item or {}
        bodies.append({
            "id": str(item.get("id") or ("p%d" % (index + 1))),
            "mass": _positive(item.get("mass", 1.0), "物块 %d 的质量" % (index + 1)),
            "charge": float(item.get("charge", 0.0) or 0.0),
            "at": _pair(item.get("at", [0.0, 0.0]), "物块 %d 的位置" % (index + 1)),
            "v": _pair(item.get("v", [0.0, 0.0]), "物块 %d 的初速度" % (index + 1)),
            "radius": max(0.01, float(item.get("radius", 0.06) or 0.06)),
            "name": str(item.get("name") or ("物块 %d" % (index + 1))),
        })
    world = {"tracks": tracks, "fields": fields, "bodies": bodies,
             "block": (bodies[0] if bodies else {}),
             "width": WORLD_W, "height": WORLD_H,
             "restitution": min(1.0, max(0.0, float(scene.get("restitution", 1.0) or 0.0))),
             "gravity": _gravity(scene),
             "duration": float(scene.get("duration", 10.0)),
             "dt": float(scene.get("dt", 0.0005)) or 0.0005}
    # 板块 / 弹簧 / 轮子 / 连接体这些新键：有就走扩展引擎，没有就还是老路子（老数值一个都不变）。
    world["extended"] = bool(_attach_linked(scene, world))
    return world


# ---------------------------------------------------------------- 几何

def _closest_on_track(track, point):
    """点到轨道最近的点：返回 (距离, 位置, 切向, 法向, 曲率半径带符号, 段号, 是否落在段内部)。

    直线段的曲率半径是无穷大；圆轨道内表面法向指向圆心（曲率半径取正），
    外表面法向背离圆心（取负）——`N = m v²/R - F·n` 这一条把"圆环内侧"和"小山丘"统一了。
    "落在段内部"用来判"能不能落回轨道"：末端的垂足不算（不然物块从轨道尽头飞出去，
    会被判成又落回同一条轨道，来回弹）。
    """
    if track["kind"] == "line":
        best = None
        points = track["points"]
        for index in range(len(points) - 1):
            start, end = points[index], points[index + 1]
            dx, dy = end[0] - start[0], end[1] - start[1]
            length = math.hypot(dx, dy)
            if length < 1e-12:
                continue
            t = ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / (length * length)
            raw = t
            t = max(0.0, min(1.0, t))
            at = [start[0] + dx * t, start[1] + dy * t]
            distance = math.hypot(point[0] - at[0], point[1] - at[1])
            if best is None or distance < best[0]:
                tangent = [dx / length, dy / length]
                normal = [-tangent[1], tangent[0]]
                inside = 1e-9 < raw < 1 - 1e-9
                best = (distance, at, tangent, normal, float("inf"), index, inside)
        return best
    center, radius = track["center"], track["radius"]
    dx, dy = point[0] - center[0], point[1] - center[1]
    distance_from_center = math.hypot(dx, dy) or 1e-9
    outward = [dx / distance_from_center, dy / distance_from_center]
    inner = track.get("inner", True)
    at = [center[0] + outward[0] * radius, center[1] + outward[1] * radius]
    normal = [-outward[0], -outward[1]] if inner else [outward[0], outward[1]]
    tangent = [-normal[1], normal[0]]
    radius_signed = radius if inner else -radius
    return (abs(distance_from_center - radius), at, tangent, normal, radius_signed, 0, True)


def _nearest_track(scene, point):
    best = None
    for track in scene["tracks"]:
        hit = _closest_on_track(track, point)
        if hit and (best is None or hit[0] < best[0][0]):
            best = (hit, track)
    return best


def _point_at(track, position):
    """按弧长参数取轨道上的点（直线段用累计长度，圆用角度）。"""
    if track["kind"] == "circle":
        angle = position
        center, radius = track["center"], track["radius"]
        return [center[0] + radius * math.cos(angle), center[1] + radius * math.sin(angle)]
    points = track["points"]
    left = max(0.0, position)
    for index in range(len(points) - 1):
        start, end = points[index], points[index + 1]
        length = math.hypot(end[0] - start[0], end[1] - start[1])
        if left <= length or index == len(points) - 2:
            ratio = left / length if length else 0.0
            return [start[0] + (end[0] - start[0]) * ratio,
                    start[1] + (end[1] - start[1]) * ratio]
        left -= length
    return list(points[-1])


def _arc_position(track, point):
    """"时间 -> 位置"反过来：给一个点，算它在轨道上的弧长参数。"""
    if track["kind"] == "circle":
        center = track["center"]
        return math.atan2(point[1] - center[1], point[0] - center[0])
    points = track["points"]
    total = 0.0
    best = (float("inf"), 0.0)
    for index in range(len(points) - 1):
        start, end = points[index], points[index + 1]
        dx, dy = end[0] - start[0], end[1] - start[1]
        length = math.hypot(dx, dy)
        if length < 1e-12:
            continue
        t = ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / (length * length)
        t = max(0.0, min(1.0, t))
        at = [start[0] + dx * t, start[1] + dy * t]
        distance = math.hypot(point[0] - at[0], point[1] - at[1])
        if distance < best[0]:
            best = (distance, total + t * length)
        total += length
    return best[1]


def _track_span(track):
    if track["kind"] == "circle":
        return 2 * math.pi * track["radius"]
    points = track["points"]
    return sum(math.hypot(points[i + 1][0] - points[i][0], points[i + 1][1] - points[i][1])
               for i in range(len(points) - 1))


def _belt_speed(track):
    """这条轨道是不是传送带；是的话返回皮带速度（不是就返回 0）。"""
    try:
        return float(track.get("v_belt") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _mu_at(track, segment):
    if track["kind"] == "circle":
        return track["mu"]
    mu = track["mu"]
    return mu[min(max(0, segment), len(mu) - 1)] if mu else 0.0


# ---------------------------------------------------------------- 场

def _field_at(scene, point):
    """这一点处的电场与磁场（多个场区重叠就相加）。"""
    e = [0.0, 0.0]
    b = 0.0
    for field in scene["fields"]:
        x, y, width, height = field["rect"]
        if not (x <= point[0] <= x + width and y <= point[1] <= y + height):
            continue
        if field.get("E"):
            e[0] += field["E"][0]
            e[1] += field["E"][1]
        if field.get("B"):
            b += field["B"]
    return e, b


# ---------------------------------------------------------------- 求解

def run(scene):
    """跑一遍场景：返回轨迹帧、事件、能量账与过程卡。

    支持**多个物块**（要能出碰撞与动量题）：每个物块各自判轨道接触，两两查碰撞，
    碰撞用"法向冲量 + 恢复系数 e"，一对物块之间的冲量是内力 -> 动量严格守恒，
    动能按 (1-e²) 损失，这笔损失单列成 `collision_loss`。
    """
    world = normalize(scene)
    if world.get("extended"):
        return _run_linked(world)          # 板块 / 弹簧 / 轮子 / 连接体：走联立求解的扩展引擎
    bodies = world["bodies"]
    gravity = world["gravity"]
    dt = max(1e-5, min(0.01, world["dt"]))
    duration = max(0.05, min(120.0, world["duration"]))
    sample_every = max(1, int(round(0.005 / dt)))

    events = []
    frames = []
    states = []
    energy0 = 0.0
    momentum0 = [0.0, 0.0]
    for body in bodies:
        position = list(body["at"])
        velocity = list(body["v"])
        hit = _nearest_track(world, position)
        track, arc = None, 0.0
        if hit and hit[0][0] <= TOL:
            track = hit[1]
            arc = _arc_position(track, position)
            events.append(_event(0.0, "on", "%s 放在轨道上开始运动" % body["name"],
                                 position, velocity))
        states.append({"body": body, "position": position, "velocity": velocity, "track": track,
                       "arc": arc, "airborne": 0.0, "stopped": False,
                       "stop_reported": False})
        energy0 += _energy(body, position, velocity, gravity)
        momentum0[0] += body["mass"] * velocity[0]
        momentum0[1] += body["mass"] * velocity[1]

    want_forces = bool(scene.get("forces"))
    heat = 0.0
    belt_work = 0.0                 # 摩擦生热
    work_electric = 0.0        # 电场力做功
    landing = 0.0              # 落回轨道时法向速度损失掉的动能
    collision_loss = 0.0       # 碰撞里损失的动能
    collision_count = 0
    time = 0.0
    steps = 0
    while time < duration and steps < MAX_STEPS:
        steps += 1
        for state in states:
            if state["stopped"]:
                continue
            body, position, velocity = state["body"], state["position"], state["velocity"]
            if state["track"] is not None:
                result = _track_step(world, body, state["track"], state["arc"], position,
                                     velocity, dt)
                state["track"], state["arc"] = result["track"], result["arc"]
                state["position"], state["velocity"] = result["position"], result["velocity"]
                state["forces"] = result.get("forces")
                heat += result["heat"]
                belt_work += result.get("belt_work", 0.0)
                work_electric += result["work"]
                if result["left"]:
                    state["airborne"] = 0.0
                    events.append(_event(time, "leave", "%s：%s" % (body["name"], result["note"]),
                                         state["position"], state["velocity"]))
                if result["stop"] and not state["stop_reported"]:
                    # 只是"停住"，**不能把这个物块冻住**：别的物块撞上来它还得分得动
                    state["stop_reported"] = True
                    events.append(_event(time, "stop", "%s 停在轨道上（摩擦把动能吃完了）"
                                         % body["name"], state["position"], state["velocity"]))
            else:
                electric, magnetic = _field_at(world, position)
                state["position"], state["velocity"], state["forces"] = _free_step(
                    world, body, position, velocity, electric, magnetic, dt)
                position, velocity = state["position"], state["velocity"]
                work_electric += body["charge"] * (electric[0] * velocity[0] +
                                                   electric[1] * velocity[1]) * dt
                state["airborne"] += math.hypot(velocity[0], velocity[1]) * dt
                hit = _nearest_track(world, position)
                if hit and hit[0][6] and hit[0][0] <= TOL and state["airborne"] > LAND_GAP \
                        and _approaching(hit[0], position, velocity):
                    state["track"] = hit[1]
                    state["arc"] = _arc_position(hit[1], position)
                    tangent = hit[0][2]
                    speed = velocity[0] * tangent[0] + velocity[1] * tangent[1]
                    landing += 0.5 * body["mass"] * max(
                        0.0, velocity[0] ** 2 + velocity[1] ** 2 - speed * speed)
                    state["velocity"] = [tangent[0] * speed, tangent[1] * speed]
                    events.append(_event(time, "land", "%s 落回轨道（法向速度损失，只保留沿轨道分量）"
                                         % body["name"], position, state["velocity"]))
        for hit in _collisions(world, states):
            collision_count += 1
            collision_loss += hit["loss"]
            states[hit["i"]]["velocity"] = hit["va"]
            states[hit["j"]]["velocity"] = hit["vb"]
            events.append(_event(time, "bump", hit["text"], hit["at"], hit["va"]))
        time += dt
        if steps % sample_every == 0 or steps == 1:
            frame = {
                "t": round(time, 4),
                "x": round(states[0]["position"][0], 4), "y": round(states[0]["position"][1], 4),
                "vx": round(states[0]["velocity"][0], 4), "vy": round(states[0]["velocity"][1], 4),
                "speed": round(math.hypot(states[0]["velocity"][0], states[0]["velocity"][1]), 4),
                "on": bool(states[0]["track"]),
                "bodies": [[round(item["position"][0], 4), round(item["position"][1], 4),
                            round(item["velocity"][0], 4), round(item["velocity"][1], 4),
                            1 if item["track"] is not None else 0] for item in states]}
            if want_forces:
                frame["forces"] = [_force_row(item) for item in states]
            frames.append(frame)
        if all(item["track"] is None and not (-1e6 < item["position"][1] < 1e6) for item in states):
            events.append(_event(time, "gone", "物块跑出场景了", states[0]["position"],
                                 states[0]["velocity"]))
            break

    energy1 = 0.0
    momentum1 = [0.0, 0.0]
    for state in states:
        energy1 += _energy(state["body"], state["position"], state["velocity"], gravity)
        momentum1[0] += state["body"]["mass"] * state["velocity"][0]
        momentum1[1] += state["body"]["mass"] * state["velocity"][1]
    speed = math.hypot(states[0]["velocity"][0], states[0]["velocity"][1])
    values = {
        "time": round(time, 3), "x": round(states[0]["position"][0], 4),
        "y": round(states[0]["position"][1], 4), "speed": round(speed, 4),
        "on_track": bool(states[0]["track"]),
        "heat": round(heat, 4), "work_electric": round(work_electric, 4),
        "belt_work": round(belt_work, 4),
        "landing_loss": round(landing, 4), "collision_loss": round(collision_loss, 4),
        "collision_count": collision_count,
        "kinetic": round(sum(0.5 * item["body"]["mass"] *
                             (item["velocity"][0] ** 2 + item["velocity"][1] ** 2)
                             for item in states), 4),
        "potential": round(sum(-item["body"]["mass"] *
                               (gravity[0] * item["position"][0] + gravity[1] * item["position"][1])
                               for item in states), 4),
        "energy_start": round(energy0, 4), "energy_end": round(energy1, 4),
        "energy_gap": round(energy1 - energy0 + heat + landing + collision_loss -
                            work_electric - belt_work, 4),
        "momentum_start": [round(momentum0[0], 4), round(momentum0[1], 4)],
        "momentum_end": [round(momentum1[0], 4), round(momentum1[1], 4)],
        "bodies_end": [[round(item["position"][0], 4), round(item["position"][1], 4),
                        round(item["velocity"][0], 4), round(item["velocity"][1], 4)]
                       for item in states],
        "steps": steps,
    }
    return {"ok": True, "kind": "sim2d", "scene": world, "frames": frames, "events": events,
            "values": values, "cards": _cards(values, events),
            "force_keys": list(FORCE_KEYS) if want_forces else []}


def _energy(body, position, velocity, gravity):
    return (0.5 * body["mass"] * (velocity[0] ** 2 + velocity[1] ** 2) -
            body["mass"] * (gravity[0] * position[0] + gravity[1] * position[1]))


def _collisions(world, states):
    """两两查碰撞（圆盘近似）：法向冲量按恢复系数算。

    物块之间的冲量是**内力**，所以一对物块的动量碰前碰后严格相等——过程卡里就给这个对照，
    学生一眼能看出"动量守恒、动能不守恒"。重叠了还要把它们推开，否则下一帧会反复判定。
    """
    hits = []
    restitution = world["restitution"]
    for i in range(len(states)):
        for j in range(i + 1, len(states)):
            first, second = states[i], states[j]
            pa, pb = first["position"], second["position"]
            radius = first["body"]["radius"] + second["body"]["radius"]
            dx, dy = pb[0] - pa[0], pb[1] - pa[1]
            distance = math.hypot(dx, dy)
            if distance > radius or distance < 1e-12:
                continue
            nx, ny = dx / distance, dy / distance
            va, vb = list(first["velocity"]), list(second["velocity"])
            ma, mb = first["body"]["mass"], second["body"]["mass"]
            vrel = (vb[0] - va[0]) * nx + (vb[1] - va[1]) * ny
            overlap = radius - distance
            if vrel < -1e-9:                    # 真的在接近才算碰撞（vrel≈0 只是挨着，别每帧都记一次）
                impulse = -(1 + restitution) * vrel / (1.0 / ma + 1.0 / mb)
                before = [ma * va[0] + mb * vb[0], ma * va[1] + mb * vb[1]]
                ke_before = 0.5 * ma * (va[0] ** 2 + va[1] ** 2) + \
                    0.5 * mb * (vb[0] ** 2 + vb[1] ** 2)
                va = [va[0] - impulse / ma * nx, va[1] - impulse / ma * ny]
                vb = [vb[0] + impulse / mb * nx, vb[1] + impulse / mb * ny]
                ke_after = 0.5 * ma * (va[0] ** 2 + va[1] ** 2) + \
                    0.5 * mb * (vb[0] ** 2 + vb[1] ** 2)
                after = [ma * va[0] + mb * vb[0], ma * va[1] + mb * vb[1]]
                hits.append({
                    "i": i, "j": j, "va": va, "vb": vb,
                    "at": [(pa[0] + pb[0]) / 2, (pa[1] + pb[1]) / 2],
                    "loss": max(0.0, ke_before - ke_after),
                    "text": "%s 和 %s 碰撞：碰前动量 (%s, %s) -> 碰后 (%s, %s)，动能损失 %s J" % (
                        first["body"]["name"], second["body"]["name"],
                        round(before[0], 3), round(before[1], 3), round(after[0], 3),
                        round(after[1], 3), round(max(0.0, ke_before - ke_after), 4)),
                })
                pa[0] -= nx * overlap / 2
                pa[1] -= ny * overlap / 2
                pb[0] += nx * overlap / 2
                pb[1] += ny * overlap / 2
            else:                                          # 已经在分开：只把重叠推开
                pa[0] -= nx * overlap / 2
                pa[1] -= ny * overlap / 2
                pb[0] += nx * overlap / 2
                pb[1] += ny * overlap / 2
    return hits


def _track_step(world, body, track, arc, position, velocity, dt):
    """轨道上的一步：把运动拆成"沿轨道"与"法向约束"两半。

    N = m v²/R - F_外力·n（R<0 表示外表面），N<0 说明轨道推不住物块 -> 离开轨道。
    摩擦按 μN 且方向与相对运动相反；v 很小时先看静摩擦够不够，不够才滑动。
    注意：`arc` 对直线是**弧长**、对圆是**角度**，所以位移要除以半径换算。
    """
    hit = _closest_on_track(track, position)
    _distance, _at, tangent, normal, radius_signed, segment, _inside = hit
    mass, charge = body["mass"], body["charge"]
    electric, _magnetic = _field_at(world, position)
    applied = [charge * electric[0] + mass * world["gravity"][0],
               charge * electric[1] + mass * world["gravity"][1]]
    speed = velocity[0] * tangent[0] + velocity[1] * tangent[1]
    normal_force = 0.0
    if math.isinf(radius_signed):
        normal_force = -(applied[0] * normal[0] + applied[1] * normal[1])
    else:
        normal_force = (mass * speed * speed / radius_signed -
                        (applied[0] * normal[0] + applied[1] * normal[1]))
    if normal_force < -1e-9:
        return {"track": None, "arc": arc, "position": position, "velocity": velocity,
                "heat": 0.0, "work": 0.0, "left": True, "stop": False,
                "note": "法向力算成负的：轨道推不住物块，离开轨道（比如圆环里速度不够）"}

    mu = _mu_at(track, segment)
    drive = applied[0] * tangent[0] + applied[1] * tangent[1]
    friction_limit = mu * max(0.0, normal_force)
    belt = _belt_speed(track)                    # 传送带：皮带自己的速度（0 = 普通轨道）
    relative = speed - belt
    stopped = False
    carried = False                              # 被静摩擦带着跟皮带一起走
    direction = 0.0                              # 摩擦力方向（静摩擦那一支不用它）
    if abs(drive) <= friction_limit and \
            abs(relative) <= max(1e-6, friction_limit * dt / mass):
        # 静摩擦够用：一步之内就跟着皮带（或停住），别来回抖（否则速度会在 ±μg·dt 之间翻符号）
        speed = belt
        stopped = abs(drive) < 1e-9 and abs(belt) < 1e-9
        carried = not stopped
        acceleration = 0.0
    else:
        # 摩擦力方向看**相对皮带**的速度：物块比皮带快就被拖慢，比皮带慢就被带着走
        direction = 1.0 if relative > 0 else (-1.0 if relative < 0
                                              else (1.0 if drive > 0 else -1.0))
        acceleration = (drive - direction * friction_limit) / mass
    moved = speed * dt + 0.5 * acceleration * dt * dt
    new_speed = speed + acceleration * dt
    # 生热看的是**相对皮带的位移**：被皮带带着走的那部分不算摩擦生热
    heat = friction_limit * abs(moved - belt * dt)
    # 传送带是要"喂能量"的：电机做的功 = 物块从摩擦得到的功 + 生热。
    # 普通轨道（belt=0）这两项正好抵消，恒等于 0，所以不用分情况。
    belt_work = 0.0
    if friction_limit > 1e-12:
        if carried:
            belt_work = -drive * moved          # 跟着皮带走：静摩擦平衡外力，电机替它出这份功
        elif not stopped:
            belt_work = (-direction * friction_limit) * moved + heat
    work = charge * (electric[0] * tangent[0] + electric[1] * tangent[1]) * moved

    if track["kind"] == "line":
        span = _track_span(track)
        new_arc = arc + moved
        if new_arc < 0 or new_arc > span:            # 滑到轨道尽头：变成自由运动
            return {"track": None, "arc": arc, "position": position,
                    "velocity": [tangent[0] * new_speed, tangent[1] * new_speed],
                    "heat": heat, "work": work, "left": True, "stop": False,
                    "note": "滑到轨道尽头，之后按自由运动算"}
    else:
        # 圆上的弧长 -> 角度要**加负号**：p = R(cosφ, sinφ) 时 dp/dφ = -R·切向，
        # 所以沿切向走 moved 对应 Δφ = -moved/R（符号搞反会凭空生出能量，踩过）
        new_arc = (arc - moved / track["radius"]) % (2 * math.pi)
    position = _point_at(track, new_arc)
    velocity = [tangent[0] * new_speed, tangent[1] * new_speed]
    return {"track": track, "arc": new_arc, "position": position, "velocity": velocity,
            "heat": heat, "work": work, "belt_work": belt_work,
            "forces": _track_forces(mass, charge, world, electric, tangent, normal,
                                    normal_force, drive, friction_limit, direction,
                                    stopped, carried, acceleration),
            "left": False, "stop": stopped, "note": ""}


def _track_forces(mass, charge, world, electric, tangent, normal, normal_force,
                  drive, friction_limit, direction, stopped, carried, acceleration):
    """轨道上那几个力（矢量，N）：重力、电场力、法向力、摩擦力。

    轨道上洛伦兹力不做功、也不改速率，引擎里本来就不管它，这里也不画（保持一套口径）。
    法向力沿 n̂、摩擦力沿切向：静摩擦（停住 / 被皮带带着走）正好平衡切向驱动力，
    动摩擦就是 -direction*μN。
    """
    gravity = [mass * world["gravity"][0], mass * world["gravity"][1]]
    electric_force = [charge * electric[0], charge * electric[1]]
    normal_vec = [normal_force * normal[0], normal_force * normal[1]]
    if stopped or carried:
        tangential = -drive                      # 静摩擦：把切向驱动力抵掉
    elif friction_limit > 1e-12:
        tangential = -direction * friction_limit
    else:
        tangential = 0.0
    friction = [tangential * tangent[0], tangential * tangent[1]]
    return {"gravity": gravity, "electric": electric_force, "magnetic": [0.0, 0.0],
            "normal": normal_vec, "friction": friction,
            "net": [gravity[0] + electric_force[0] + normal_vec[0] + friction[0],
                    gravity[1] + electric_force[1] + normal_vec[1] + friction[1]]}


def _free_forces(mass, charge, world, electric, magnetic, velocity):
    """自由段那几个力：重力、电场力、洛伦兹力（合力就是它们的和）。"""
    gravity = [mass * world["gravity"][0], mass * world["gravity"][1]]
    electric_force = [charge * electric[0], charge * electric[1]]
    magnetic_force = [charge * magnetic * velocity[1], -charge * magnetic * velocity[0]]
    return {"gravity": gravity, "electric": electric_force, "magnetic": magnetic_force,
            "normal": [0.0, 0.0], "friction": [0.0, 0.0],
            "net": [gravity[0] + electric_force[0] + magnetic_force[0],
                    gravity[1] + electric_force[1] + magnetic_force[1]]}


def _free_step(world, body, position, velocity, electric, magnetic, dt):
    """自由段：重力 + 电场力 + 洛伦兹力（B 垂直屏幕），RK4 走一步。"""
    mass, charge = body["mass"], body["charge"]

    def accel(state):
        vx, vy = state[2], state[3]
        ax = (charge * electric[0] + mass * world["gravity"][0] +
              charge * magnetic * vy) / mass
        ay = (charge * electric[1] + mass * world["gravity"][1] -
              charge * magnetic * vx) / mass
        return [vx, vy, ax, ay]

    state = [position[0], position[1], velocity[0], velocity[1]]
    k1 = accel(state)
    k2 = accel([state[i] + dt / 2 * k1[i] for i in range(4)])
    k3 = accel([state[i] + dt / 2 * k2[i] for i in range(4)])
    k4 = accel([state[i] + dt * k3[i] for i in range(4)])
    for index in range(4):
        state[index] += dt / 6 * (k1[index] + 2 * k2[index] + 2 * k3[index] + k4[index])
    new_velocity = [state[2], state[3]]
    return ([state[0], state[1]], new_velocity,
            _free_forces(body["mass"], body["charge"], world, electric, magnetic,
                         [0.5 * (velocity[0] + new_velocity[0]),
                          0.5 * (velocity[1] + new_velocity[1])]))


#: 每一帧每个物块给前端的受力行：顺序固定，前端照着画箭头
FORCE_KEYS = ("gravity", "electric", "magnetic", "normal", "friction", "net")


def _force_row(state):
    """一个物块这一帧的受力：按 FORCE_KEYS 的顺序给 [Fx, Fy]（N）。"""
    forces = state.get("forces") or {}
    row = []
    for key in FORCE_KEYS:
        pair = forces.get(key) or [0.0, 0.0]
        row.append([round(pair[0], 4), round(pair[1], 4)])
    return row


def _event(time, kind, text, position, velocity):
    return {"t": round(time, 4), "kind": kind, "text": text,
            "x": round(position[0], 4), "y": round(position[1], 4),
            "speed": round(math.hypot(velocity[0], velocity[1]), 4)}


def _cards(values, events):
    cards = [{
        "title": "能量账", "law": "能量守恒 / 功能关系",
        "forces": "摩擦生热 %s J，落回损失 %s J，碰撞损失 %s J（%s 次），电场力做功 %s J%s" % (
            values["heat"], values["landing_loss"], values["collision_loss"],
            values["collision_count"], values["work_electric"],
            ("，皮带做功 %s J" % values["belt_work"])
            if abs(values.get("belt_work") or 0.0) > 1e-9 else ""),
        "equations": "E初 + W电 + W皮带 - Q - E落回 - E碰撞 = E末",
        "result": "初 %s J -> 末 %s J（动能 %s J、势能 %s J），对账误差 %s J" % (
            values["energy_start"], values["energy_end"], values["kinetic"],
            values["potential"], values["energy_gap"]),
    }]
    for item in events:
        cards.append({"title": item["text"], "law": "事件 @ t=%s s" % item["t"],
                      "forces": "", "equations": "",
                      "result": "位置 (%s, %s)，速度 %s m/s" % (item["x"], item["y"],
                                                              item["speed"])})
    return cards


# ---------------------------------------------------------------- 示例场景

#: 图像里能用的"量"：前缀 -> (中文含义, 单位)。名字就是物块的名字（"物块 2"、"小车"…）
BODY_MEANINGS = {
    "x": ("横坐标", "m"), "y": ("纵坐标", "m"),
    "v": ("速率", "m/s"), "vx": ("横向速度", "m/s"), "vy": ("竖直速度", "m/s"),
    "a": ("加速度大小", "m/s²"), "ax": ("横向加速度", "m/s²"), "ay": ("竖直加速度", "m/s²"),
    "m": ("质量", "kg"), "p": ("动量大小", "kg·m/s"),
    "Ek": ("动能", "J"), "Ep": ("重力势能", "J"), "E": ("机械能（Ek+Ep）", "J"),
    "E弹": ("弹簧弹性势能", "J"), "F弹": ("弹簧弹力", "N"),
    "长": ("弹簧长度", "m"), "转角": ("轮子转角", "度"), "角速度": ("轮子角速度", "rad/s"),
}
TIME_MEANINGS = {"t": ("时间", "s")}
#: 不挂在某个实体身上的全局量：记号就是它自己。Q = 摩擦生热（所有接触面加起来）
GLOBAL_MEANINGS = {"Q": ("摩擦生热", "J")}

_PREFIX_ORDER = ("Ek", "Ep", "vy", "vx", "ax", "ay", "E", "p", "v", "a", "x", "y", "m",
                 "E弹", "F弹", "长", "转角", "角速度")


def quantity_help():
    """说明表开头的话：记号怎么写。"""
    return ("轴上写函数，括号里填物块的名字：Ek(物块1) 是物块1的动能、a(小车) 是小车的加速度、"
            "x小车 是小车的位移、v小车 是小车的速度；板、轮子、弹簧也有名字，例如 E弹(弹簧1)。"
            "t 是时间、Q 是摩擦生热，这两个不用括号，直接写。"
            "直接写物块的名字（物块1）= 它的主量（动能）。"
            "注意名字里的空格要省掉：物块 1 就写 Ek(物块1)。")


def function_table():
    """说明表：**只列有哪些函数**，不填名字、不填当前值。"""
    rows = []
    meaning, unit = TIME_MEANINGS["t"]
    rows.append(("t", "%s%s" % (meaning, ("／" + unit) if unit else "")))
    meaning, unit = GLOBAL_MEANINGS["Q"]
    rows.append(("Q", "%s%s" % (meaning, ("／" + unit) if unit else "")))
    for prefix in _PREFIX_ORDER:
        meaning, unit = BODY_MEANINGS.get(prefix, (prefix, ""))
        rows.append((prefix + "()", "%s%s" % (meaning, ("／" + unit) if unit else "")))
    return rows


def symbol_name(name):
    """记号里不能有空格（表达式按空格断词），所以名字里的空格去掉：
    "物块 1" 在式子里写 `Ek物块1`（说明表里照样显示"物块 1 的动能"）。"""
    return "".join(str(name or "").split())


def frame_table(world, frame, previous, dt, jumpy=False, previous_frame=None):
    """一帧 -> 记号表（图表用）。加速度用相邻两帧的速度差算（帧间隔就是 dt）。

    板、轮子、弹簧和摩擦生热也在同一张表里：板用和物块一样的记号（x小车、v小车、a小车…），
    轮子是 x/y/v/转角/角速度，弹簧是 E弹/F弹/长，整场的摩擦生热是 Q。板/轮的加速度要看
    previous_frame（上一整帧），光有物块的速度槽算不出来。

    `jumpy` 的那一帧（碰撞、落回轨道…）速度是**突变**的，用速度差算出来的"加速度"会
    冲到几百 m/s²、把整张图的纵轴拉爆（老师要的是斜面/摩擦那种几 m/s² 的加速度）。
    这种帧直接不给加速度，画图时跳过这一点，并在图下说明跳了几个点。"""
    table = {"t": float(frame.get("t") or 0.0)}
    gravity = world.get("gravity") or [0.0, 0.0]
    bodies = world.get("bodies") or []
    for index, slot in enumerate(frame.get("bodies") or []):
        body = bodies[index] if index < len(bodies) else {"mass": 1.0}
        name = symbol_name(body.get("name") or ("物块 %d" % (index + 1)))
        mass = float(body.get("mass", 1.0) or 1.0)
        x, y, vx, vy = float(slot[0]), float(slot[1]), float(slot[2]), float(slot[3])
        speed = math.hypot(vx, vy)
        ax = ay = 0.0
        if previous is not None and index < len(previous):
            was = previous[index]
            step = dt if dt > 1e-9 else 1.0
            ax = (vx - float(was[2])) / step
            ay = (vy - float(was[3])) / step
        kinetic = 0.5 * mass * speed * speed
        potential = -mass * (gravity[0] * x + gravity[1] * y)
        values = {"x": x, "y": y, "vx": vx, "vy": vy, "v": speed,
                  "ax": ax, "ay": ay, "a": math.hypot(ax, ay), "m": mass,
                  "p": mass * speed, "Ek": kinetic, "Ep": potential,
                  "E": kinetic + potential}
        if jumpy:
            for key in ("ax", "ay", "a"):
                values[key] = None
        for prefix, value in values.items():
            table[prefix + name] = None if value is None else float(value)

    # 板：跟物块一样一套记号（x小车、v小车、a小车…），加速度用上一整帧的速度差算
    plates = world.get("plates") or []
    for index, slot in enumerate(frame.get("plates") or []):
        plate = plates[index] if index < len(plates) else {"mass": 1.0}
        name = symbol_name(plate.get("name") or ("板 %d" % (index + 1)))
        mass = float(plate.get("mass", 1.0) or 1.0)
        x, y, vx, vy = float(slot[0]), float(slot[1]), float(slot[2]), float(slot[3])
        speed = math.hypot(vx, vy)
        ax = ay = 0.0
        if previous_frame is not None:
            was = previous_frame.get("plates") or []
            if index < len(was):
                step = dt if dt > 1e-9 else 1.0
                ax = (vx - float(was[index][2])) / step
                ay = (vy - float(was[index][3])) / step
        kinetic = 0.5 * mass * speed * speed
        potential = -mass * (gravity[0] * x + gravity[1] * y)
        values = {"x": x, "y": y, "vx": vx, "vy": vy, "v": speed,
                  "ax": ax, "ay": ay, "a": math.hypot(ax, ay), "m": mass,
                  "p": mass * speed, "Ek": kinetic, "Ep": potential,
                  "E": kinetic + potential}
        if jumpy:
            for key in ("ax", "ay", "a"):
                values[key] = None
        for prefix, value in values.items():
            table[prefix + name] = None if value is None else float(value)

    # 轮子：x/y/v（速率）和转角；角速度由相邻两帧的转角差算
    wheels = world.get("wheels") or []
    for index, slot in enumerate(frame.get("wheels") or []):
        wheel = wheels[index] if index < len(wheels) else {}
        name = symbol_name(wheel.get("name") or ("轮子 %d" % (index + 1)))
        x, y, angle, vx = float(slot[0]), float(slot[1]), float(slot[2]), float(slot[3])
        table["x" + name] = x
        table["y" + name] = y
        table["vx" + name] = vx
        table["v" + name] = abs(vx)
        table["转角" + name] = angle
        if previous_frame is not None and dt > 1e-9:
            was = previous_frame.get("wheels") or []
            if index < len(was):
                table["角速度" + name] = (angle - float(was[index][2])) / dt

    # 弹簧：E弹（弹性势能）、F弹（弹力）、长（当前长度，比原长多出来的才算变形）
    springs = world.get("springs") or []
    for index, slot in enumerate(frame.get("springs") or []):
        spring = springs[index] if index < len(springs) else {}
        name = symbol_name(spring.get("name") or ("弹簧 %d" % (index + 1)))
        k = float(spring.get("k", 50.0) or 0.0)
        rest = float(spring.get("rest", 0.0) or 0.0)
        stretch = float(slot[0]) - rest
        table["E弹" + name] = 0.5 * k * stretch * stretch
        table["F弹" + name] = float(slot[1])
        table["长" + name] = float(slot[0])

    # 整场累计的摩擦生热：Q
    if frame.get("heat") is not None:
        table["Q"] = float(frame.get("heat") or 0.0)
    return table


def plot_chart(scene, x_expression, y_expression, cursor=None, x_range=None, y_range=None):
    """算一遍场景，把每一帧的记号表按 x/y 表达式画成图。

    动画播放时前端只挪那个红点（用返回的 meta 自己算像素），不用每帧重新请求。
    """
    import chart as chart_module
    result = run(scene)
    frames = result["frames"]
    world = result["scene"]
    if len(frames) < 2:
        raise SceneError("这次运动只有 %d 帧，画不出图像（检查一下时长和物块初速度）"
                         % len(frames))
    dt = float(frames[1].get("t") or 0.0) - float(frames[0].get("t") or 0.0)
    # 出过事件（落回轨道、碰撞、滑出轨道…）的那一两帧：速度是突变的，加速度跳过
    moments = [float(item.get("t") or 0.0) for item in (result.get("events") or [])]
    window = max(dt * 1.5, 1e-9)

    def jumpy(moment):
        for hit in moments:
            if abs(moment - hit) <= window or abs(moment - dt - hit) <= window:
                return True
        return False

    tables = []
    previous = None
    previous_frame = None
    for frame in frames:
        tables.append(frame_table(world, frame, previous, dt,
                                  jumpy=jumpy(float(frame.get("t") or 0.0)),
                                  previous_frame=previous_frame))
        previous = frame.get("bodies")
        previous_frame = frame
    names = {}
    # 物块、板、轮子、弹簧的名字都能写进记号（normalize 里已经保证这几类名字不重）
    body_names = [symbol_name(item.get("name") or "")
                  for kind in ("bodies", "plates", "wheels", "springs")
                  for item in (world.get(kind) or [])]
    for name in body_names:
        for prefix in _PREFIX_ORDER:
            if prefix + name in tables[0]:
                names[prefix + name] = prefix
    # "直接写名字 = 它的主量"这一项**每一帧都要有**（只加到最后一帧的话，曲线就只有一个点）
    for table in tables:
        for name in body_names:
            for prefix in _PREFIX_ORDER:
                if prefix + name in table:
                    table.setdefault(name, table[prefix + name])
                    names.setdefault(name, prefix)
                    break
    for key in TIME_MEANINGS:
        if key in tables[0]:
            names[key] = key
    for key in GLOBAL_MEANINGS:
        if key in tables[0]:
            names[key] = key

    def describe_one(text):
        flat = text.replace("(", "").replace(")", "")
        for key in names:
            if key.lower() in (text.lower(), flat.lower()):
                meaning, unit = (TIME_MEANINGS.get(names[key])
                                 or GLOBAL_MEANINGS.get(names[key])
                                 or BODY_MEANINGS.get(names[key], (names[key], "")))
                return "%s（%s%s）" % (text, meaning, ("／" + unit) if unit else "")
        return text

    def describe(expression):
        """记号写成「记号（中文含义／单位）」；用分号分开的多条曲线各写各的，图例才看得懂。"""
        text = str(expression or "").strip()
        if ";" in text or "；" in text:
            return "; ".join(describe_one(part.strip())
                             for part in text.replace("；", ";").split(";") if part.strip())
        return describe_one(text)

    x_expression = str(x_expression or "").strip() or "t"
    y_expression = str(y_expression or "").strip()
    if not y_expression:
        first = (world.get("bodies") or [{}])[0].get("name") or "物块 1"
        y_expression = "Ek" + symbol_name(first)
    import circuit as circuit_module          # 借它的分号拆法（多条曲线）
    texts = circuit_module._split_series(y_expression)
    try:
        points, skipped = chart_module.series_of(tables, x_expression, texts[0],
                                                 lambda table: table)
        extra = []
        for order, text in enumerate(texts[1:]):
            group, _ignored = chart_module.series_of(tables, x_expression, text,
                                                     lambda table: table)
            extra.append((group, circuit_module.SERIES_COLORS[
                order % len(circuit_module.SERIES_COLORS)], describe(text)))
    except chart_module.ChartError as exc:
        raise SceneError(str(exc))
    shown = None
    if cursor is not None:
        try:
            shown = max(0, min(len(points) - 1, int(cursor)))
        except (TypeError, ValueError):
            shown = None
    figure = chart_module.chart(points, describe(x_expression), describe(y_expression),
                                cursor=shown, note="%d 帧" % len(points),
                                x_range=x_range, y_range=y_range, extra=extra,
                                main_label=describe(texts[0]))
    return {"ok": True, "figure": figure,
            "points": [[round(point[0], 6), round(point[1], 6)] for point in points],
            "cursor": shown,
            "meta": chart_module.meta_of(points, x_range=x_range, y_range=y_range,
                                         extra=[group[0] for group in extra]),
            "legend": chart_module.legend_rows(function_table()), "help": quantity_help(),
            "x": x_expression, "y": y_expression,
            "x_label": describe(x_expression), "y_label": describe(y_expression),
            "skipped": skipped or ""}


#: 现成例子（M8）：一键装进沙盒的典型题目，每个都写清"看什么"。
#: 沙盒的世界是 **6 m x 3 m**（x 0~6、y 0~3），所以坐标都压在这个范围里。
#: 重力的键是 `g`（数字 = 竖直向下，也可以写 [gx, gy] 或 {value, dir}）。
EXAMPLES = [
    {"key": "circular", "chart": {"x": "x(粒子)", "y": "y(粒子)"},
     "name": "匀强磁场里的圆周运动",
     "note": "带电粒子垂直进入匀强磁场：洛伦兹力提供向心力，轨迹是圆（这里 r = mv/qB = 0.70 m）。"
             "x 轴填 x(粒子)、y 轴填 y(粒子)，图上就是一个圆。",
     "scene": {"tracks": [], "fields": [{"id": "磁场", "rect": [0.4, 0.4, 5.2, 2.3],
                                        "B": -5.7}],
               "bodies": [{"name": "粒子", "mass": 1.0, "charge": 0.5, "radius": 0.05,
                           "at": [2.2, 0.9], "v": [2.0, 0.0]}],
               "g": 0, "duration": 2.2, "restitution": 1.0}},
    {"key": "boundary", "chart": {"x": "x(粒子)", "y": "y(粒子)"},
     "name": "有界磁场的临界",
     "note": "磁场只有一小块：速度大就穿出去、速度小就转回来（临界 v = qBd/2m 约 1.7 m/s）。"
             "把粒子的 vx 改成 1.2 看它转弯回来。",
     "scene": {"tracks": [], "fields": [{"id": "有界磁场", "rect": [2.4, 0.3, 1.2, 2.4],
                                        "B": -5.7}],
               "bodies": [{"name": "粒子", "mass": 1.0, "charge": 0.5, "radius": 0.05,
                           "at": [0.9, 1.5], "v": [2.6, 0.0]}],
               "g": 0, "duration": 1.8, "restitution": 1.0}},
    {"key": "selector", "chart": {"x": "x(粒子)", "y": "y(粒子)"},
     "name": "速度选择器",
     "note": "电场和磁场垂直：只有速度 v = E/B（这里 2 m/s）的粒子能直着穿过去，"
             "别的会被偏转——把 vx 改成 1.5 或 3 试试。",
     "scene": {"tracks": [], "fields": [{"id": "场区", "rect": [0.3, 0.9, 5.4, 1.2],
                                        "B": -0.5, "E": [0.0, -1.0]}],
               "bodies": [{"name": "粒子", "mass": 1.0, "charge": 0.5, "radius": 0.05,
                           "at": [0.5, 1.5], "v": [2.0, 0.0]}],
               "g": 0, "duration": 2.6, "restitution": 1.0}},
    {"key": "capacitor", "chart": {"x": "x(粒子)", "y": "y(粒子)"},
     "name": "平行板电容器的类平抛",
     "note": "带电粒子沿中线飞进偏转电场：x 方向匀速、y 方向匀加速（类平抛）。"
             "y-x 图是一条抛物线；把场强 E 改大，偏得更多。",
     "scene": {"tracks": [], "fields": [{"id": "偏转电场", "rect": [1.6, 1.0, 2.4, 1.0],
                                        "E": [0.0, -2.0]}],
               "bodies": [{"name": "粒子", "mass": 1.0, "charge": 0.25, "radius": 0.05,
                           "at": [0.4, 1.5], "v": [2.5, 0.0]}],
               "g": 0, "duration": 2.2, "restitution": 1.0}},
    {"key": "belt", "chart": {"x": "t", "y": "v(物块)"},
     "name": "传送带",
     "note": "皮带 2 m/s、物块从静止放上去：先被摩擦力加速，达到同速后一起匀速走。"
             "看 v-t 图和能量账里的「皮带做功」。",
     "scene": {"tracks": [{"id": "传送带", "kind": "line", "points": [[0.4, 1.2], [5.7, 1.2]],
                           "mu": 0.25, "belt": True, "v_belt": 2.0}],
               "fields": [],
               "bodies": [{"name": "物块", "mass": 1.0, "charge": 0.0,
                           "at": [0.6, 1.2], "v": [0.0, 0.0]}],
               "g": 9.8, "duration": 3.0, "restitution": 1.0}},
    {"key": "incline", "chart": {"x": "t", "y": "v(物块)"},
     "name": "斜面上的摩擦",
     "note": "斜面 μ=0.2：加速度 a = g(sinθ - μcosθ)。v-t 图是一条直线（匀加速）。",
     "scene": {"tracks": [{"id": "斜面", "kind": "line", "points": [[0.6, 2.4], [4.6, 0.6]],
                           "mu": 0.2}],
               "fields": [],
               "bodies": [{"name": "物块", "mass": 1.0, "charge": 0.0,
                           "at": [0.6, 2.4], "v": [0.0, 0.0]}],
               "g": 9.8, "duration": 1.7, "restitution": 1.0}},
    {"key": "projectile", "chart": {"x": "x(小球)", "y": "y(小球)"},
     "name": "平抛运动",
     "note": "从高处水平抛出：水平匀速、竖直自由落体。y-x 图是抛物线、vy-t 图是直线。",
     "scene": {"tracks": [], "fields": [],
               "bodies": [{"name": "小球", "mass": 0.5, "charge": 0.0, "radius": 0.06,
                           "at": [0.8, 2.6], "v": [2.0, 0.0]}],
               "g": 9.8, "duration": 0.75, "restitution": 0.0}},
    {"key": "collision", "chart": {"x": "t", "y": "Ek(甲)"},
     "name": "两物块的碰撞",
     "note": "恢复系数 e=1 是完全弹性碰撞：动量守恒、动能也守恒；改成 0.3 看动能损失和生热。",
     "scene": {"tracks": [{"id": "水平面", "kind": "line", "points": [[0.4, 1.2], [5.7, 1.2]],
                           "mu": 0.0}],
               "fields": [],
               "bodies": [{"name": "甲", "mass": 1.0, "charge": 0.0, "at": [1.0, 1.2],
                           "v": [2.0, 0.0]},
                          {"name": "乙", "mass": 2.0, "charge": 0.0, "at": [4.0, 1.2],
                           "v": [0.0, 0.0]}],
               "g": 9.8, "duration": 3.0, "restitution": 1.0}},
    {"key": "loop", "chart": {"x": "t", "y": "y(小车)"},
     "name": "竖直圆环过最高点",
     "note": "圆环内侧：速度不够会在最高点之前离开轨道（这里要从底部 5.9 m/s 以上才过得去）。"
             "现在给的是 4 m/s：看着它离开轨道；改成 6.5 就能转一整圈。",
     "scene": {"tracks": [{"id": "圆环", "kind": "circle", "center": [3.0, 1.5], "radius": 0.7,
                           "mu": 0.0, "inner": True}],
               "fields": [],
               "bodies": [{"name": "小车", "mass": 0.5, "charge": 0.0,
                           "at": [3.0, 0.8], "v": [4.0, 0.0]}],
               "g": 9.8, "duration": 2.5, "restitution": 1.0}},
    {"key": "combined", "chart": {"x": "x(粒子)", "y": "y(粒子)"},
     "name": "先加速再偏转（组合场）",
     "note": "电场先把粒子加速，再进磁场偏转：x-y 图里是一段直线加一段圆弧。",
     "scene": {"tracks": [], "fields": [{"id": "加速电场", "rect": [0.3, 1.1, 1.2, 0.8],
                                        "E": [6.0, 0.0]},
                                       {"id": "偏转磁场", "rect": [2.1, 0.4, 3.4, 2.6],
                                        "B": -9.0}],
               "bodies": [{"name": "粒子", "mass": 1.0, "charge": 0.5, "radius": 0.05,
                           "at": [0.5, 1.5], "v": [0.0, 0.0]}],
               "g": 0, "duration": 2.4, "restitution": 1.0}},
    # ---- 板块 / 弯道 / 单摆 / 弹簧 / 轮子 / 连接体（新引擎的六道题） ----
    {"key": "exam", "chart": {"x": "t", "y": "v(摆球); v(物块)"},
     "name": "小车上的弯道（单摆撞物块）",
     "note": "摆球从水平位置放开，到最低点正好 6 m/s；撞上小车上的物块（等质量完全弹性）后"
             "物块 3 m/s、摆球 -3 m/s。物块在小车上先滑过 1 m 粗糙段（μ=0.1，生热正好 1 J），"
             "再冲上 1/4 圆弧，离开弧顶时速度 1.677 m/s（水平 0.75、竖直 1.5），之后平抛。"
             "看 v-t 图里的几级台阶：摆球 -6 → +3、物块加速到 2.64、离开弧顶后 vy 从 1.5 掉到 0。",
     "scene": {"tracks": [{"id": "粗糙段", "kind": "line",
                           "points": [[-1.105, 0.10], [-0.105, 0.10]],
                           "mu": 0.1, "body": "car"},
                          {"id": "弯道", "kind": "circle",
                           "center": [-0.105, 0.225], "radius": 0.125,
                           "inner": True, "arc": [270.0, 360.0], "mu": 0.0, "body": "car"}],
               "plates": [{"id": "car", "name": "小车", "mass": 3.0, "size": [2.4, 0.2],
                           "at": [3.0, 0.1], "support": "ground",
                           "lock_rotation": True, "mu_ground": 0.0}],
               "bodies": [{"id": "blk", "name": "物块", "mass": 1.0, "radius": 0.05,
                           "at": [1.895, 0.20], "v": [0.0, 0.0]},
                          {"id": "ball", "name": "摆球", "mass": 1.0 / 3.0, "radius": 0.05,
                           "at": [0.0, 2.0], "v": [0.0, 0.0]}],
               "anchors": [{"id": "O", "at": [1.8, 2.0]}],
               "links": [{"id": "绳", "name": "绳", "kind": "rope", "a": "ball",
                          "anchor": "O", "length": 1.8}],
               "g": 10.0, "duration": 2.0, "restitution": 1.0}},
    {"key": "plateslip", "chart": {"x": "t", "y": "v小车; v物块"},
     "name": "板块相对滑动（动量守恒与生热）",
     "note": "光滑地面上的小车（2 kg）上面放一个物块（1 kg），物块以 2 m/s 滑上粗糙面（μ=0.2）："
             "物块减速、小车加速，最后一起以 2/3 m/s 匀速走。相对滑动 0.667 m、生热 1.33 J，"
             "动量 2 kg·m/s 一直不变。看两条 v-t 曲线一降一升，最后并到同一条水平线。",
     "scene": {"tracks": [{"id": "粗糙面", "kind": "line",
                           "points": [[-0.8, 0.10], [0.5, 0.10]], "mu": 0.2, "body": "car"}],
               "plates": [{"id": "car", "name": "小车", "mass": 2.0, "size": [1.6, 0.2],
                           "at": [3.0, 0.1], "support": "ground",
                           "lock_rotation": True, "mu_ground": 0.0}],
               "bodies": [{"id": "blk", "name": "物块", "mass": 1.0, "radius": 0.05,
                           "at": [2.23, 0.20], "v": [2.0, 0.0]}],
               "g": 10.0, "duration": 1.0, "restitution": 1.0}},
    {"key": "spring", "chart": {"x": "t", "y": "E弹(弹簧); Ek(甲)"},
     "name": "弹簧连接的两个物块",
     "note": "光滑水平面上 2 kg 和 1 kg 的两个物块用弹簧连着：整体质心一直以 1/3 m/s 匀速走"
             "（动量守恒），动能在两块之间传递、弹簧势能和动能此消彼长，加起来不变。"
             "看两条曲线完全反相、质心速度是一条水平线。",
     "scene": {"tracks": [{"id": "光滑面", "kind": "line",
                           "points": [[1.2, 0.20], [4.8, 0.20]], "mu": 0.0}],
               "bodies": [{"id": "A", "name": "甲", "mass": 2.0, "radius": 0.05,
                           "at": [2.0, 0.20], "v": [0.5, 0.0]},
                          {"id": "B", "name": "乙", "mass": 1.0, "radius": 0.05,
                           "at": [2.6, 0.20], "v": [0.0, 0.0]}],
               "springs": [{"id": "s1", "name": "弹簧", "a": "A", "b": "B",
                            "k": 50.0, "rest": 0.6}],
               "g": 10.0, "duration": 0.75, "restitution": 1.0}},
    {"key": "wheel", "chart": {"x": "t", "y": "x轮子1; v轮子1"},
     "name": "轮子的纯滚动",
     "note": "轮子在地面上纯滚动（不打滑）：v = ωr 一直成立、v 恒为 1 m/s——静摩擦不做功。"
             "x-t 图是一条斜直线（x = 1.0 t），v-t 图是水平线。",
     "scene": {"tracks": [],
               "wheels": [{"id": "wh1", "name": "轮子1", "radius": 0.12, "mass": 1.0,
                           "at": [1.2, 0.12], "v": [1.0, 0.0], "roll": True, "mu": 0.5}],
               "g": 10.0, "duration": 0.8, "restitution": 1.0}},
    {"key": "atwood", "chart": {"x": "t", "y": "vy甲; vy乙"},
     "name": "轻绳跨过定滑轮（连接体）",
     "note": "1 kg 和 2 kg 两个物块用轻绳跨过定滑轮：1 kg 的向上、2 kg 的向下，"
             "整体加速度 a = (m2-m1)g/(m1+m2) = 3.33 m/s²、绳中张力 13.3 N。"
             "看两条 v-t 都是斜直线、斜率一样（一条正一条负）。",
     "scene": {"tracks": [],
               "bodies": [{"id": "A", "name": "甲", "mass": 1.0, "radius": 0.05,
                           "at": [3.0, 2.4], "v": [0.0, 0.0]},
                          {"id": "B", "name": "乙", "mass": 2.0, "radius": 0.05,
                           "at": [3.0, 1.6], "v": [0.0, 0.0]}],
               "anchors": [{"id": "P", "at": [3.0, 2.9]}],
               "links": [{"id": "绳", "name": "绳", "kind": "rope", "a": "A", "b": "B",
                          "anchor": "P", "length": 1.8}],
               "g": 10.0, "duration": 0.3, "restitution": 1.0}},
    {"key": "pendulum", "chart": {"x": "t", "y": "v(摆球); v(物块)"},
     "name": "单摆撞物块（简化版）",
     "note": "老师那道题的简化版：去掉小车和弯道，只留单摆和物块。摆球（1/3 kg）从水平位置"
             "放开，到最低点 6 m/s，和 1 kg 的物块完全弹性碰撞——碰后物块 3 m/s、摆球 -3 m/s。"
             "看 v-t 图上两条线在碰撞那一瞬间同时跳变。",
     "scene": {"tracks": [{"id": "光滑面", "kind": "line",
                           "points": [[1.6, 0.20], [5.2, 0.20]], "mu": 0.0}],
               "bodies": [{"id": "ball", "name": "摆球", "mass": 1.0 / 3.0, "radius": 0.05,
                           "at": [0.2, 2.0], "v": [0.0, 0.0]},
                          {"id": "blk", "name": "物块", "mass": 1.0, "radius": 0.05,
                           "at": [2.2, 0.20], "v": [0.0, 0.0]}],
               "anchors": [{"id": "O", "at": [2.0, 2.0]}],
               "links": [{"id": "绳", "name": "绳", "kind": "rope", "a": "ball",
                          "anchor": "O", "length": 1.8}],
               "g": 10.0, "duration": 0.9, "restitution": 1.0}},
]



def scene_figure(scene, width=560, height=300):
    """把场景画成一张配图（例子题要"左边出个图"，不能让人凭空想）。

    画：轨道（折线/圆）、场区（虚线框 + 磁场/电场标注）、物块（方块 + 质量/电荷 + **初速度箭头**）。
    坐标跟沙盒一致（6 m x 3 m，y 向上），比例按 width/height 各自缩放。
    """
    world = normalize(scene)
    pad = 26.0
    span_x = max(1e-6, float(world.get("width", WORLD_W)))
    span_y = max(1e-6, float(world.get("height", WORLD_H)))

    def at_x(value):
        return pad + float(value) / span_x * (width - pad * 2)

    def at_y(value):
        return height - pad - float(value) / span_y * (height - pad * 2)

    parts = ['<svg class="scene-svg" xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
             'viewBox="0 0 %d %d" data-scene="1">' % (width, height, width, height)]
    parts.append('<rect width="%d" height="%d" fill="#fff"/>' % (width, height))
    # 刻度网格（0.5 m 一格，淡）+ 边框
    step = 0.5
    value = 0.0
    while value <= span_x + 1e-9:
        parts.append('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="#eef2f7"/>'
                     % (at_x(value), at_y(0), at_x(value), at_y(span_y)))
        value += step
    value = 0.0
    while value <= span_y + 1e-9:
        parts.append('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="#eef2f7"/>'
                     % (at_x(0), at_y(value), at_x(span_x), at_y(value)))
        value += step
    parts.append('<rect x="%g" y="%g" width="%g" height="%g" fill="none" stroke="#dde4ec"/>'
                 % (at_x(0), at_y(span_y), at_x(span_x) - at_x(0), at_y(0) - at_y(span_y)))
    parts.append('<text x="%g" y="%g" font-size="11" fill="#7b8794">x / m</text>'
                 % (at_x(span_x) - 34, at_y(0) + 14))
    parts.append('<text x="%g" y="%g" font-size="11" fill="#7b8794">y / m</text>'
                 % (at_x(0) - 20, at_y(span_y) + 10))

    # 场区
    for field in world["fields"]:
        rect = field["rect"]
        label = ("磁场 B=%s T" % _show(float(field.get("B") or 0.0), 3)
                 if field.get("B") is not None else
                 "电场 E=%s N/C" % _show(math.hypot(*(field.get("E") or [0, 0])), 3))
        parts.append('<rect x="%g" y="%g" width="%g" height="%g" fill="rgba(120,90,200,0.06)" '
                     'stroke="#7c3aed" stroke-dasharray="5 3"/>'
                     % (at_x(rect[0]), at_y(rect[1] + rect[3]),
                        at_x(rect[0] + rect[2]) - at_x(rect[0]),
                        at_y(rect[1]) - at_y(rect[1] + rect[3])))
        parts.append('<text x="%g" y="%g" font-size="11" fill="#6d28d9">%s</text>'
                     % (at_x(rect[0]) + 4, at_y(rect[1] + rect[3]) + 13, _escape(label)))

    # 轨道（挂在板上的轨道要跟着板走：points/center 是板的局部坐标，画图前先换成世界坐标）
    def pose_of(track):
        index = track.get("plate")
        plates = world.get("plates") or []
        if index is None or not (0 <= index < len(plates)):
            return None
        plate = plates[index]
        angle = float(plate.get("angle") or 0.0)
        return (float(plate["at"][0]), float(plate["at"][1]),
                math.cos(angle), math.sin(angle))

    def world_point(pose, x, y):
        if pose is None:
            return (float(x), float(y))
        return (pose[0] + pose[2] * x - pose[3] * y,
                pose[1] + pose[3] * x + pose[2] * y)

    for track in world["tracks"]:
        pose = pose_of(track)
        span = track.get("range")
        if track["kind"] == "circle":
            radius = track["radius"]
            center = world_point(pose, track["center"][0], track["center"][1])
            if span:
                samples = []
                for step in range(33):
                    angle = span[0] + (span[1] - span[0]) * step / 32.0
                    samples.append("%g,%g" % (at_x(center[0] + radius * math.cos(angle)),
                                              at_y(center[1] + radius * math.sin(angle))))
                parts.append('<polyline points="%s" fill="none" stroke="#3a4652" '
                             'stroke-width="2.4"/>' % " ".join(samples))
                parts.append('<text x="%g" y="%g" font-size="11" fill="#3a4652">'
                             '圆弧 R=%s m</text>'
                             % (at_x(center[0]) - 12, at_y(center[1]) + 4, _show(radius, 3)))
            else:
                parts.append('<circle cx="%g" cy="%g" r="%g" fill="none" stroke="#3a4652" '
                             'stroke-width="2.4"/>'
                             % (at_x(center[0]), at_y(center[1]),
                                radius / span_x * (width - pad * 2)))
                parts.append('<text x="%g" y="%g" font-size="11" fill="#3a4652">圆环 R=%s m</text>'
                             % (at_x(center[0] - radius), at_y(center[1]) - 6, _show(radius, 3)))
        else:
            spots = [world_point(pose, point[0], point[1]) for point in track["points"]]
            points = " ".join("%g,%g" % (at_x(spot[0]), at_y(spot[1])) for spot in spots)
            belt = float(track.get("v_belt") or 0.0)
            color = "#1d4ed8" if track.get("belt") else "#3a4652"
            parts.append('<polyline points="%s" fill="none" stroke="%s" stroke-width="3"/>'
                         % (points, color))
            if track.get("belt"):
                middle = spots[len(spots) // 2]
                parts.append('<text x="%g" y="%g" font-size="11" fill="#1d4ed8">'
                             '传送带 %s m/s</text>'
                             % (at_x(middle[0]) - 24, at_y(middle[1]) - 8, _show(belt, 3)))
            mu = track.get("mu")
            if isinstance(mu, (list, tuple)) and mu:
                mu = mu[0]
            first = spots[0]
            if isinstance(mu, (int, float)):
                parts.append('<text x="%g" y="%g" font-size="11" fill="#7b8794">'
                             'μ=%s</text>' % (at_x(first[0]) + 6, at_y(first[1]) + 15,
                                               _show(mu, 3)))
    # 物块（方块 + 质量/电荷 + 初速度箭头）
    for body in world["bodies"]:
        size = max(10.0, 0.12 / span_x * (width - pad * 2))
        center_x, center_y = at_x(body["at"][0]), at_y(body["at"][1])
        parts.append('<rect x="%g" y="%g" width="%g" height="%g" fill="#3a4652"/>'
                     % (center_x - size / 2, center_y - size / 2, size, size))
        text = "%s %s kg" % (body["name"], _show(body["mass"], 3))
        if abs(body["charge"]) > 1e-9:
            text += "，q=%s C" % _show(body["charge"], 3)
        parts.append('<text x="%g" y="%g" font-size="12" fill="#3a4652">%s</text>'
                     % (center_x + size / 2 + 4, center_y + 4, _escape(text)))
        vx, vy = body["v"][0], body["v"][1]
        if math.hypot(vx, vy) > 1e-9:
            scale = 46.0 / max(abs(vx), abs(vy), 1e-9)
            head = [center_x + vx * scale, center_y - vy * scale]
            parts.append('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="#c0392b" '
                         'stroke-width="2"/>' % (center_x, center_y, head[0], head[1]))
            angle = math.atan2(head[1] - center_y, head[0] - center_x)
            for side in (-1, 1):
                spot = [head[0] - 9 * math.cos(angle + side * 0.42),
                        head[1] - 9 * math.sin(angle + side * 0.42)]
                parts.append('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="#c0392b" '
                             'stroke-width="2"/>' % (head[0], head[1], spot[0], spot[1]))
            parts.append('<text x="%g" y="%g" font-size="11" fill="#c0392b">v0=%s m/s</text>'
                         % (head[0] + 4, head[1] - 4,
                            _show(math.hypot(vx, vy), 3)))
    # 板：矩形（按位姿转），标注名字和质量
    for plate in world.get("plates") or []:
        box_w, box_h = plate["size"]
        angle = float(plate.get("angle") or 0.0)
        cos_a, sin_a = math.cos(angle), math.sin(angle)
        corners = []
        for dx, dy in ((-box_w / 2, -box_h / 2), (box_w / 2, -box_h / 2),
                       (box_w / 2, box_h / 2), (-box_w / 2, box_h / 2)):
            wx = plate["at"][0] + cos_a * dx - sin_a * dy
            wy = plate["at"][1] + sin_a * dx + cos_a * dy
            corners.append("%g,%g" % (at_x(wx), at_y(wy)))
        parts.append('<polygon points="%s" fill="rgba(58,70,82,0.18)" stroke="#3a4652" '
                     'stroke-width="2"/>' % " ".join(corners))
        parts.append('<text x="%g" y="%g" font-size="12" fill="#3a4652">%s %s kg</text>'
                     % (at_x(plate["at"][0]) - 12, at_y(plate["at"][1]) + 4,
                        _escape(plate.get("name") or "板"), _show(plate.get("mass", 1.0), 3)))

    # 轮子：圆 + 一根辐条表示转角
    for wheel in world.get("wheels") or []:
        center = (float(wheel["at"][0]), float(wheel["at"][1]))
        radius = float(wheel.get("radius") or 0.12)
        parts.append('<circle cx="%g" cy="%g" r="%g" fill="none" stroke="#3a4652" '
                     'stroke-width="2"/>'
                     % (at_x(center[0]), at_y(center[1]),
                        radius / span_x * (width - pad * 2)))
        spoke = float(wheel.get("angle") or 0.0)
        parts.append('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="#3a4652" '
                     'stroke-width="1.6"/>'
                     % (at_x(center[0]), at_y(center[1]),
                        at_x(center[0] + radius * math.cos(spoke)),
                        at_y(center[1] + radius * math.sin(spoke))))
        parts.append('<text x="%g" y="%g" font-size="11" fill="#3a4652">%s R=%s m</text>'
                     % (at_x(center[0]) + 4, at_y(center[1]) - 8,
                        _escape(wheel.get("name") or "轮子"), _show(radius, 3)))

    def point_of(raw):
        """a / b 的引用换成世界坐标：认 id（物块、板、轮子、悬挂点），也认直接写的 [x, y]。"""
        if isinstance(raw, (list, tuple)) and len(raw) >= 2:
            return (float(raw[0]), float(raw[1]))
        key = str(raw or "")
        for kind in ("bodies", "plates", "wheels", "anchors"):
            for item in world.get(kind) or []:
                if str(item.get("id")) == key and item.get("at"):
                    return (float(item["at"][0]), float(item["at"][1]))
        return None

    def draw_spring(head, tail):
        """弹簧画成一条折线（几个来回）从 head 到 tail（世界坐标）。"""
        x1, y1 = at_x(head[0]), at_y(head[1])
        x2, y2 = at_x(tail[0]), at_y(tail[1])
        length = math.hypot(x2 - x1, y2 - y1)
        if length < 1e-9:
            return
        ux, uy = (x2 - x1) / length, (y2 - y1) / length
        px, py = -uy, ux
        spots = [(x1, y1)]
        turns = 6
        for step in range(1, turns * 2):
            factor = step / float(turns * 2)
            swing = 5.0 if step % 2 else -5.0
            spots.append((x1 + ux * length * factor + px * swing,
                          y1 + uy * length * factor + py * swing))
        spots.append((x2, y2))
        parts.append('<polyline points="%s" fill="none" stroke="#b45309" stroke-width="1.8"/>'
                     % " ".join("%g,%g" % spot for spot in spots))

    for spring in world.get("springs") or []:
        head, tail = point_of(spring.get("a")), point_of(spring.get("b"))
        if head is None or tail is None:
            continue
        draw_spring(head, tail)
        middle = ((head[0] + tail[0]) / 2.0, (head[1] + tail[1]) / 2.0)
        parts.append('<text x="%g" y="%g" font-size="11" fill="#b45309">%s k=%s N/m</text>'
                     % (at_x(middle[0]) - 10, at_y(middle[1]) - 9,
                        _escape(spring.get("name") or "弹簧"),
                        _show(spring.get("k", 50.0), 3)))

    for link in world.get("links") or []:
        head = point_of(link.get("a"))
        tail = point_of(link.get("b"))
        if tail is None:
            tail = point_of(link.get("anchor"))
        if head is None or tail is None:
            continue
        dash = "6 4" if link.get("kind") == "rod" else ""
        parts.append('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="#0f766e" '
                     'stroke-width="1.8" stroke-dasharray="%s"/>'
                     % (at_x(head[0]), at_y(head[1]), at_x(tail[0]), at_y(tail[1]), dash))
        text = "绳" if link.get("kind") != "rod" else "杆"
        length = float(link.get("length") or 0.0)
        parts.append('<text x="%g" y="%g" font-size="11" fill="#0f766e">%s %s m</text>'
                     % (at_x((head[0] + tail[0]) / 2.0) - 8,
                        at_y((head[1] + tail[1]) / 2.0) + 14,
                        text, _show(length, 3)))

    for anchor in world.get("anchors") or []:
        spot = (float(anchor["at"][0]), float(anchor["at"][1]))
        parts.append('<rect x="%g" y="%g" width="7" height="7" fill="#0f766e"/>'
                     % (at_x(spot[0]) - 3.5, at_y(spot[1]) - 3.5))
        parts.append('<text x="%g" y="%g" font-size="11" fill="#0f766e">悬挂点</text>'
                     % (at_x(spot[0]) + 8, at_y(spot[1]) + 4))

    parts.append("</svg>")
    return "".join(parts)


def _escape(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _show(value, digits=3):
    """配图上的数字：去掉没用的尾零（2.0 -> 2、0.250 -> 0.25）。"""
    text = ("%%.%df" % digits) % (float(value) + 0.0)
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def examples():
    """例子表：只给名字和说明（场景等老师点了哪条再取，省得一次传一大坨）。"""
    return [{"key": item["key"], "name": item["name"], "note": item["note"],
             "chart": item.get("chart") or {}}
            for item in EXAMPLES]


def example(key):
    for item in EXAMPLES:
        if item["key"] == str(key):
            return {"key": item["key"], "name": item["name"], "note": item["note"],
                    "chart": item.get("chart") or {},
                    "scene": json.loads(json.dumps(item["scene"]))}
    raise SceneError("没有这个例子：%s" % key)


def demo():

    """一个"像高考大题"的默认场景：斜面 -> 水平面 -> 竖直圆环，还有一块磁场区。

    放在这里是为了：① 面板一打开就有东西可看；② 自检拿它当回归样例（点、μ、场的增删都不炸）。
    """
    return {
        "tracks": [
            {"id": "斜面", "kind": "line", "points": [[0.0, 1.8], [1.6, 0.6]], "mu": 0.10},
            {"id": "水平面", "kind": "line", "points": [[1.6, 0.6], [4.2, 0.6]], "mu": 0.15},
            {"id": "圆环", "kind": "circle", "center": [4.2, 1.2], "radius": 0.6, "mu": 0.0,
             "inner": True},
        ],
        "fields": [{"id": "磁场区", "rect": [1.6, 0.4, 1.8, 1.8], "B": 0.6}],
        "block": {"mass": 0.5, "charge": 0.02, "at": [0.0, 1.8], "v": [0.0, 0.0]},
        "duration": 6.0, "dt": 0.0005,
    }

# ---------------------------------------------------------------- 连接体引擎（板块 / 挂轨道 / 弹簧 / 轮子 / 单摆）
#
# 这一块给"板块 + 挂轨道 + 单摆 + 弹簧 + 轮子"这类多体场景用。旧路径（单物块沿轨道或自由运动）
# 一个字都没动：normalize() 里只有出现新键才把 extended 标成 True，run() 见到 extended 才走这里。
# 数值上走"半步踢 -> 解约束 -> 漂移 -> 位置投影 -> 半步踢 -> 解约束"（KDK），约束用 Gauss-Seidel 迭代，
# 所以板、板上骑手、摆球、弹簧是**联立**解的，不是拿上一步的旧坐标显式耦合（那样会漂）。
#
# 坐标口径（跟界面约定，改之前先看 docs/sandbox-linked-bodies.md）：
#   * 板上轨道的 points / center 是**板的局部坐标**，每步跟着板的位姿换到世界坐标；
#   * 骑手物块的中心就在轨道线上（半径只用于物块互撞和画图），所以圆弧上物块抬升量就是轨道形状本身；
#   * 圆轨道的 arc 存成 range=[起角, 终角]（弧度），s=(角度-起角)*半径，方向为角度增大方向；
#   * 地面是 y=0：板 support="ground" 时竖直方向被地面托住（不做竖直动力学），轮子同样只在地面上滚。

LINK_SWITCH_TOL = 0.02      # 换轨 / 上轨时允许的世界坐标距离
LINK_RIDER_PASSES = 3       # 每半步里骑手法向约束的迭代遍数
LINK_JOINT_PASSES = 4       # 每半步里绳 / 杆 / 滑轮约束的迭代遍数
LINK_ESCAPE_Y = -0.02       # 物块掉到这条线以下就算跑出场景
LINK_SUBSTEPS = 4         # 每个采样步内部再分几小步积分（轨道曲率带来的离散误差跟子步长成正比）
LINK_SLOP = 1e-9


def _static_point(at):
    """把世界坐标点包成"零逆质量"的实体，好跟物块一起进约束求解。"""
    return {"kind": "point", "name": "", "x": float(at[0]), "y": float(at[1]),
            "vx": 0.0, "vy": 0.0, "m": 0.0, "invm": 0.0, "wx": 0.0, "wy": 0.0,
            "iinv": 0.0, "angle": 0.0, "omega": 0.0, "inertia": 0.0}


def _speed_at(ent, rx, ry):
    """实体上 (rx, ry) 这个材料点的世界速度。"""
    omega = ent["omega"]
    return ent["vx"] - omega * ry, ent["vy"] + omega * rx


def _inv_mass(ent, rx, ry, nx, ny):
    """在 (rx, ry) 处沿 n 方向推一下的等效质量倒数。"""
    cross = rx * ny - ry * nx
    return ent["wx"] * nx * nx + ent["wy"] * ny * ny + ent["iinv"] * cross * cross


def _impulse(ent, rx, ry, jx, jy):
    """在 (rx, ry) 处给实体一个冲量 (jx, jy)：改线速度，也改角速度。"""
    ent["vx"] += ent["wx"] * jx
    ent["vy"] += ent["wy"] * jy
    ent["omega"] += ent["iinv"] * (rx * jy - ry * jx)


def _offset(ent, off):
    """实体中心到挂点的世界偏移（跟着实体转角一起转）。"""
    if not off:
        return 0.0, 0.0
    c, s = math.cos(ent["angle"]), math.sin(ent["angle"])
    return c * off[0] - s * off[1], s * off[0] + c * off[1]


def _attach_at(ent, off):
    """挂点的世界坐标。"""
    rx, ry = _offset(ent, off)
    return ent["x"] + rx, ent["y"] + ry


def _local_to_world(host, x, y):
    """轨道局部坐标 -> 世界坐标（host 为空表示铺在世界里的静态轨道）。"""
    if host is None:
        return x, y
    c, s = math.cos(host["angle"]), math.sin(host["angle"])
    return host["x"] + c * x - s * y, host["y"] + s * x + c * y


def _local_dir_to_world(host, dx, dy):
    if host is None:
        return dx, dy
    c, s = math.cos(host["angle"]), math.sin(host["angle"])
    return c * dx - s * dy, s * dx + c * dy


def _world_to_local(host, x, y):
    if host is None:
        return x, y
    c, s = math.cos(host["angle"]), math.sin(host["angle"])
    dx, dy = x - host["x"], y - host["y"]
    return c * dx + s * dy, -s * dx + c * dy


def _track_geom(track):
    """轨道的局部几何。线轨在扩展场景里按首尾单段处理，圆轨带角度范围。"""
    if track["kind"] == "circle":
        center = track["center"]
        radius = track["radius"]
        span = track.get("range")
        if span:
            a0, a1 = float(span[0]), float(span[1])
        else:
            a0, a1 = 0.0, 2.0 * math.pi
        return {"kind": "circle", "center": [center[0], center[1]], "radius": radius,
                "a0": a0, "a1": a1, "length": radius * (a1 - a0),
                "inner": bool(track.get("inner", True)),
                "mu": max(0.0, float(track.get("mu", 0.0) or 0.0)),
                "plate": track.get("plate")}
    points = track["points"]
    p0 = points[0]
    p1 = points[-1]
    ex, ey = p1[0] - p0[0], p1[1] - p0[1]
    length = math.hypot(ex, ey)
    if length < 1e-12:
        raise SceneError("轨道「%s」的两个点重合了，没法当轨道用" % track["id"])
    return {"kind": "line", "p0": [p0[0], p0[1]], "t": [ex / length, ey / length],
            "length": length, "mu": max(0.0, float(track["mu"][0] if track["mu"] else 0.0)),
            "plate": track.get("plate")}


def _geom_project(geom, qx, qy, s_prev):
    """把局部点投到轨道上：返回 (弧长 s, 局部点, 局部切向)。s 不夹到 [0, 长度]，越界交给调用者判断。"""
    if geom["kind"] == "line":
        p0, tangent = geom["p0"], geom["t"]
        s = (qx - p0[0]) * tangent[0] + (qy - p0[1]) * tangent[1]
        return s, [p0[0] + tangent[0] * s, p0[1] + tangent[1] * s], [tangent[0], tangent[1]]
    center, radius = geom["center"], geom["radius"]
    angle = math.atan2(qy - center[1], qx - center[0])
    full = (geom["a1"] - geom["a0"]) >= 2.0 * math.pi - 1e-9
    if s_prev is None:
        if not full:
            angle = geom["a0"] + ((angle - geom["a0"]) % (2.0 * math.pi))
    else:
        base = geom["a0"] + s_prev / radius
        angle += 2.0 * math.pi * round((base - angle) / (2.0 * math.pi))
    s = (angle - geom["a0"]) * radius
    return (s, [center[0] + radius * math.cos(angle), center[1] + radius * math.sin(angle)],
            [-math.sin(angle), math.cos(angle)])


def _link_ref(value, label, tables):
    """把 a / b 的引用解析成 {"kind": ..., "index"/"at": ...}。"""
    if isinstance(value, dict):
        if value.get("at") is not None:
            return {"kind": "point", "at": _pair(value.get("at"), label)}
        if value.get("id") is not None:
            value = value.get("id")
        else:
            raise SceneError("%s 要写成 id 字符串或者 [x, y]" % label)
    if isinstance(value, (list, tuple)):
        return {"kind": "point", "at": _pair(value, label)}
    key = str(value or "")
    for kind in ("body", "plate", "wheel", "anchor"):
        table = tables.get(kind) or {}
        if key in table:
            return {"kind": kind, "index": table[key]}
    raise SceneError("%s 里的「%s」找不到对应的物块、板、轮子或悬挂点（要先给它起个 id）"
                     % (label, key))


def _attach_linked(scene, world):
    """解析板块 / 轮子 / 弹簧 / 连接 / 悬挂点并挂到 world 上；一条新键都没有就返回 None。"""
    raw_tracks = scene.get("tracks") or []
    raw_plates = scene.get("plates") or []
    raw_wheels = scene.get("wheels") or []
    raw_springs = scene.get("springs") or []
    raw_links = scene.get("links") or []
    raw_anchors = scene.get("anchors") or []
    # 轨道挂板（body）或者只画一段圆弧（arc）都得走扩展引擎：旧路径只会把圆轨当整圆跑
    hosted = any(isinstance(item, dict) and (item.get("body") or item.get("arc") is not None)
                 for item in raw_tracks)
    if not (raw_plates or raw_wheels or raw_springs or raw_links or raw_anchors or hosted):
        return None

    tables = {"body": {}, "plate": {}, "wheel": {}, "anchor": {}}
    for index, body in enumerate(world["bodies"]):
        tables["body"][body["id"]] = index

    anchors = []
    for index, raw in enumerate(raw_anchors):
        raw = raw or {}
        item = {"id": str(raw.get("id") or "a%d" % (index + 1)),
                "at": _pair(raw.get("at"), "悬挂点 %d 的位置" % (index + 1))}
        if item["id"] in tables["anchor"] or item["id"] in tables["plate"] or                 item["id"] in tables["wheel"] or item["id"] in tables["body"]:
            raise SceneError("id「%s」重名了：板、轮子、物块、悬挂点要各叫各的" % item["id"])
        tables["anchor"][item["id"]] = len(anchors)
        anchors.append(item)

    plates = []
    for index, raw in enumerate(raw_plates):
        raw = raw or {}
        name = str(raw.get("name") or ("板 %d" % (index + 1)))
        size = raw.get("size")
        if size is None:
            size = [1.6, 0.16]
        size = _pair(size, "板 %d 的尺寸" % (index + 1))
        if size[0] <= 0.0 or size[1] <= 0.0:
            raise SceneError("板「%s」的尺寸要是正数" % name)
        support = str(raw.get("support") or "none")
        if support not in ("ground", "none"):
            raise SceneError("板「%s」的 support 只能是 ground 或者 none" % name)
        item = {"id": str(raw.get("id") or "pl%d" % (index + 1)), "name": name,
                "mass": _positive(raw.get("mass", 1.0), "板 %d 的质量" % (index + 1)),
                "size": size, "at": _pair(raw.get("at", [0.0, 0.0]), "板 %d 的位置" % (index + 1)),
                "v": _pair(raw.get("v") or [0.0, 0.0], "板 %d 的初速度" % (index + 1)),
                "angle": math.radians(float(raw.get("angle", 0.0) or 0.0)),
                "support": support,
                "lock_rotation": bool(raw.get("lock_rotation", True)),
                "fixed": bool(raw.get("fixed", False)),
                "mu_ground": max(0.0, float(raw.get("mu_ground", 0.0) or 0.0))}
        if item["id"] in tables["plate"] or item["id"] in tables["body"] or                 item["id"] in tables["wheel"] or item["id"] in tables["anchor"]:
            raise SceneError("id「%s」重名了：板、轮子、物块、悬挂点要各叫各的" % item["id"])
        tables["plate"][item["id"]] = len(plates)
        plates.append(item)

    wheels = []
    for index, raw in enumerate(raw_wheels):
        raw = raw or {}
        name = str(raw.get("name") or ("轮子 %d" % (index + 1)))
        item = {"id": str(raw.get("id") or "wh%d" % (index + 1)), "name": name,
                "radius": _positive(raw.get("radius", 0.12), "轮子 %d 的半径" % (index + 1)),
                "mass": _positive(raw.get("mass", 1.0), "轮子 %d 的质量" % (index + 1)),
                "at": _pair(raw.get("at", [0.0, 0.12]), "轮子 %d 的位置" % (index + 1)),
                "v": _pair(raw.get("v") or [0.0, 0.0], "轮子 %d 的初速度" % (index + 1)),
                "roll": bool(raw.get("roll", True)),
                "mu": max(0.0, float(raw.get("mu", 0.0) or 0.0)),
                "plate": str(raw.get("plate") or "") or None}
        if item["id"] in tables["wheel"] or item["id"] in tables["body"] or                 item["id"] in tables["plate"] or item["id"] in tables["anchor"]:
            raise SceneError("id「%s」重名了：板、轮子、物块、悬挂点要各叫各的" % item["id"])
        tables["wheel"][item["id"]] = len(wheels)
        wheels.append(item)

    springs = []
    for index, raw in enumerate(raw_springs):
        raw = raw or {}
        label = "弹簧 %d" % (index + 1)
        item = {"id": str(raw.get("id") or "s%d" % (index + 1)),
                "name": str(raw.get("name") or ("弹簧 %d" % (index + 1))),
                "a": _link_ref(raw.get("a"), label + "的 a 端", tables),
                "b": _link_ref(raw.get("b"), label + "的 b 端", tables),
                "k": _positive(raw.get("k", 50.0), label + "的劲度系数"),
                "rest": _positive(raw.get("rest", 0.5), label + "的原长", allow_zero=True),
                "damping": max(0.0, float(raw.get("damping", 0.0) or 0.0)),
                "at_a": _pair(raw.get("at_a"), label + " a 端的偏移") if raw.get("at_a") else None,
                "at_b": _pair(raw.get("at_b"), label + " b 端的偏移") if raw.get("at_b") else None}
        springs.append(item)

    links = []
    for index, raw in enumerate(raw_links):
        raw = raw or {}
        label = "连接 %d" % (index + 1)
        kind = str(raw.get("kind") or "rope")
        if kind not in ("rope", "rod"):
            raise SceneError("连接 %d 的 kind 只能是 rope（绳）或者 rod（杆）" % (index + 1))
        b_raw = raw.get("b")
        pulley = None
        if b_raw is None and raw.get("anchor") is None:
            raise SceneError("连接 %d 没有 b 端，就得给一个 anchor（悬挂点）" % (index + 1))
        if b_raw is not None and raw.get("anchor") is not None:
            pulley = _link_ref(raw.get("anchor"), label + "的定滑轮", tables)
        item = {"id": str(raw.get("id") or "ln%d" % (index + 1)),
                "name": str(raw.get("name") or ("连接 %d" % (index + 1))),
                "kind": kind,
                "a": _link_ref(raw.get("a"), label + "的 a 端", tables),
                "b": _link_ref(b_raw, label + "的 b 端", tables) if b_raw is not None else None,
                "anchor": _link_ref(raw.get("anchor"), label + "的悬挂点", tables)
                          if (b_raw is None and raw.get("anchor") is not None) else None,
                "pulley": pulley,
                "length": float(raw.get("length") or 0.0),
                "at_a": _pair(raw.get("at_a"), label + " a 端的偏移") if raw.get("at_a") else None,
                "at_b": _pair(raw.get("at_b"), label + " b 端的偏移") if raw.get("at_b") else None}
        if item["length"] < 0.0:
            raise SceneError("连接「%s」的长度不能是负数" % item["name"])
        links.append(item)

    # 名字不许撞：图像上的记号是"前缀 + 名字"，重名了老师根本分不清哪条线。
    names = {}
    for kind, items in (("物块", world["bodies"]), ("板", plates), ("轮子", wheels),
                        ("弹簧", springs)):
        for item in items:
            key = item["name"]
            if key in names:
                raise SceneError("名字「%s」重了：%s和%s都叫这个名字，图像上分不清" %
                                 (key, names[key], kind))
            names[key] = kind

    # 轨道挂板：points / center 是板的局部坐标
    for index, track in enumerate(world["tracks"]):
        raw = raw_tracks[index] if index < len(raw_tracks) else {}
        raw = raw or {}
        track["plate"] = None
        if raw.get("body"):
            key = str(raw.get("body"))
            if key not in tables["plate"]:
                raise SceneError("轨道「%s」的 body 要指向一块板（plates 里的 id），现在写的是「%s」"
                                 % (track["id"], key))
            track["plate"] = tables["plate"][key]
        span = raw.get("arc")
        if span is not None:
            if track["kind"] != "circle":
                raise SceneError("轨道「%s」是直线，不能给 arc（arc 只给圆轨道用）" % track["id"])
            span = _pair(span, "轨道「%s」的 arc" % track["id"])
            if span[1] < span[0]:
                raise SceneError("轨道「%s」的 arc 要写成 [起角, 终角] 且终角不小于起角"
                                 % track["id"])
            if span[1] - span[0] > 360.0 + 1e-9:
                raise SceneError("轨道「%s」的 arc 跨度不能超过 360 度" % track["id"])
            track["range"] = [math.radians(span[0]), math.radians(span[1])]
        else:
            track["range"] = None

    # 轮子挂板：plate 字段是板的 id，这里换成板的下标（整数量快，也省得每步查表）
    for item in wheels:
        if not item["plate"]:
            item["plate"] = None
            continue
        if item["plate"] not in tables["plate"]:
            raise SceneError("轮子「%s」的 plate 要指向一块板（plates 里的 id），现在写的是「%s」"
                             % (item["name"], item["plate"]))
        item["plate"] = tables["plate"][item["plate"]]

    world["anchors"] = anchors
    world["plates"] = plates
    world["wheels"] = wheels
    world["springs"] = springs
    world["links"] = links
    world["extended"] = True
    return world

def _entity_list(world):
    """把物块 / 板 / 轮子变成可积分的实体：各方向逆质量、转动惯量一次算好。"""
    bodies = []
    for index, body in enumerate(world["bodies"]):
        invm = 1.0 / body["mass"]
        bodies.append({"kind": "body", "slot": index, "name": body["name"],
                       "x": body["at"][0], "y": body["at"][1],
                       "vx": body["v"][0], "vy": body["v"][1],
                       "m": body["mass"], "invm": invm, "wx": invm, "wy": invm,
                       "iinv": 0.0, "inertia": 0.0, "angle": 0.0, "omega": 0.0,
                       "radius": body["radius"], "charge": body["charge"],
                       "track": None, "s": 0.0, "escaped": False})
    plates = []
    for index, plate in enumerate(world.get("plates") or []):
        mass = plate["mass"]
        invm = 0.0 if plate["fixed"] else 1.0 / mass
        inertia = mass * (plate["size"][0] ** 2 + plate["size"][1] ** 2) / 12.0
        iinv = 0.0
        if (not plate["fixed"]) and (not plate["lock_rotation"]):
            iinv = 1.0 / inertia
        # support="ground"：竖直方向被地面托住，只有水平自由度
        wy = 0.0 if (plate["fixed"] or plate["support"] == "ground") else invm
        plates.append({"kind": "plate", "slot": index, "name": plate["name"],
                       "x": plate["at"][0], "y": plate["at"][1],
                       "vx": plate["v"][0],
                       "vy": 0.0 if (plate["fixed"] or plate["support"] == "ground")
                             else plate["v"][1],
                       "m": mass, "invm": invm, "wx": invm, "wy": wy,
                       "iinv": iinv, "inertia": inertia if iinv > 0.0 else 0.0,
                       "angle": plate["angle"], "omega": 0.0, "size": plate["size"],
                       "mu_ground": plate["mu_ground"], "fixed": plate["fixed"],
                       "support": plate["support"], "escaped": False,
                       "kinematic": False})
    wheels = []
    for index, wheel in enumerate(world.get("wheels") or []):
        mass = wheel["mass"]
        radius = wheel["radius"]
        inertia = 0.5 * mass * radius * radius
        iinv = (1.0 / inertia) if wheel["roll"] else 0.0
        omega = (-wheel["v"][0] / radius) if wheel["roll"] else 0.0
        host = wheel["plate"]
        kinematic = host is not None
        off = None
        if kinematic:
            off = _world_to_local(plates[host], wheel["at"][0], wheel["at"][1])
        wheels.append({"kind": "wheel", "slot": index, "name": wheel["name"],
                       "x": wheel["at"][0], "y": wheel["at"][1],
                       "vx": wheel["v"][0], "vy": wheel["v"][1],
                       "m": mass, "invm": 0.0 if kinematic else 1.0 / mass,
                       "wx": 0.0 if kinematic else 1.0 / mass, "wy": 0.0,
                       "iinv": 0.0 if kinematic else iinv,
                       "inertia": (inertia if iinv > 0.0 else 0.0) if not kinematic else 0.0,
                       "angle": 0.0, "omega": omega, "R": radius,
                       "roll": wheel["roll"], "plate": host, "off": off,
                       "kinematic": kinematic, "escaped": False})
    return bodies, plates, wheels


def _ref_entity(ref, bodies, plates, wheels, anchors):
    """引用 -> 实体（悬挂点 / 世界坐标点包成零逆质量的静态实体）。"""
    kind = ref.get("kind")
    if kind == "body":
        return bodies[ref["index"]]
    if kind == "plate":
        return plates[ref["index"]]
    if kind == "wheel":
        return wheels[ref["index"]]
    if kind == "anchor":
        return _static_point(anchors[ref["index"]]["at"])
    return _static_point(ref["at"])


def _spring_state(spring):
    """量一下弹簧当前的长度、弹力、弹性势能（采样和能量账都要用）。"""
    ax, ay = _attach_at(spring["ea"], spring["ra"])
    bx, by = _attach_at(spring["eb"], spring["rb"])
    length = math.hypot(bx - ax, by - ay)
    spring["length"] = length
    spring["force"] = spring["k"] * (length - spring["rest"])
    spring["energy"] = 0.5 * spring["k"] * (length - spring["rest"]) ** 2
    return length


def _spring_step(spring, half):
    """弹簧力（含阻尼）当成冲量加到两端，返回这半步的阻尼耗散。"""
    ax, ay = _attach_at(spring["ea"], spring["ra"])
    bx, by = _attach_at(spring["eb"], spring["rb"])
    dx, dy = bx - ax, by - ay
    distance = math.hypot(dx, dy)
    if distance < LINK_SLOP:
        return 0.0
    nx, ny = dx / distance, dy / distance
    ra, rb = spring["ra"], spring["rb"]
    vax, vay = _speed_at(spring["ea"], ra[0], ra[1])
    vbx, vby = _speed_at(spring["eb"], rb[0], rb[1])
    vrel = (vbx - vax) * nx + (vby - vay) * ny
    force = spring["k"] * (distance - spring["rest"]) + spring["damping"] * vrel
    _impulse(spring["ea"], ra[0], ra[1], force * half * nx, force * half * ny)
    _impulse(spring["eb"], rb[0], rb[1], -force * half * nx, -force * half * ny)
    if spring["damping"] <= 0.0:
        return 0.0
    return spring["damping"] * vrel * vrel * half


def _rider_contact(ent, geoms, hosts):
    """骑手跟轨道的接触信息（几何、法向、接触点）；不在轨道上返回 None。"""
    index = ent["track"]
    if index is None:
        return None
    geom = geoms[index]
    host = hosts[index]
    qx, qy = _world_to_local(host, ent["x"], ent["y"])
    s, point, tangent = _geom_project(geom, qx, qy, ent.get("s"))
    px, py = _local_to_world(host, point[0], point[1])
    tx, ty = _local_dir_to_world(host, tangent[0], tangent[1])
    scale = math.hypot(tx, ty)
    if scale > LINK_SLOP:
        tx, ty = tx / scale, ty / scale
    if geom["kind"] == "circle" and not geom["inner"]:
        nx, ny = ty, -tx
    else:
        nx, ny = -ty, tx
    if host is None:
        rx, ry = 0.0, 0.0
    else:
        rx, ry = px - host["x"], py - host["y"]
    return {"geom": geom, "host": host, "s": s, "point": [px, py],
            "tangent": (tx, ty), "normal": (nx, ny), "rx": rx, "ry": ry}


def _solve_rider(ent, geoms, hosts):
    """骑手法向约束：物块跟轨道接触点的法向相对速度清成 0，返回冲量大小。"""
    contact = _rider_contact(ent, geoms, hosts)
    if contact is None:
        return 0.0
    host = contact["host"]
    nx, ny = contact["normal"]
    rx, ry = contact["rx"], contact["ry"]
    vx, vy = _speed_at(ent, 0.0, 0.0)
    if host is None:
        wx, wy = 0.0, 0.0
    else:
        wx, wy = _speed_at(host, rx, ry)
    vrel = (vx - wx) * nx + (vy - wy) * ny
    k = _inv_mass(ent, 0.0, 0.0, nx, ny)
    if host is not None:
        k += _inv_mass(host, rx, ry, nx, ny)
    if k <= LINK_SLOP:
        return 0.0
    j = -vrel / k
    _impulse(ent, 0.0, 0.0, j * nx, j * ny)
    if host is not None:
        _impulse(host, rx, ry, -j * nx, -j * ny)
    return abs(j)


def _solve_friction(ent, geoms, hosts, normal_impulse):
    """骑手切向摩擦：|j|<=μN 就按静摩擦把相对速度拉平，否则按 μN 滑动。返回生热。"""
    if normal_impulse <= 0.0:
        return 0.0
    contact = _rider_contact(ent, geoms, hosts)
    if contact is None:
        return 0.0
    geom = contact["geom"]
    mu = geom["mu"]
    if mu <= 0.0:
        return 0.0
    host = contact["host"]
    tx, ty = contact["tangent"]
    rx, ry = contact["rx"], contact["ry"]
    vx, vy = _speed_at(ent, 0.0, 0.0)
    if host is None:
        wx, wy = 0.0, 0.0
    else:
        wx, wy = _speed_at(host, rx, ry)
    vrel = (vx - wx) * tx + (vy - wy) * ty
    k = _inv_mass(ent, 0.0, 0.0, tx, ty)
    if host is not None:
        k += _inv_mass(host, rx, ry, tx, ty)
    if k <= LINK_SLOP:
        return 0.0
    j = -vrel / k
    cap = mu * normal_impulse
    if abs(j) > cap:
        j = cap if j > 0.0 else -cap
    _impulse(ent, 0.0, 0.0, j * tx, j * ty)
    if host is not None:
        _impulse(host, rx, ry, -j * tx, -j * ty)
    # 生热按"这一小段里物块相对轨道表面滑过的距离"算：Q = μN·|相对速度|·dt
    return abs(j) * abs(vrel)


def _solve_roll(ent):
    """轮子纯滚动：接触点速度 vx + ωR = 0（静摩擦不做功，所以不产生热）。"""
    radius = ent["R"]
    rx, ry = 0.0, -radius
    vx, _vy = _speed_at(ent, rx, ry)
    k = _inv_mass(ent, rx, ry, 1.0, 0.0)
    if k <= LINK_SLOP:
        return
    j = -vx / k
    _impulse(ent, rx, ry, j, 0.0)


def _solve_ground_friction(ent, half):
    """板跟地面之间的摩擦（法向力近似取板重），返回生热。"""
    if ent["fixed"] or ent["mu_ground"] <= 0.0:
        return 0.0
    # 法向力近似取板重 M*g（板竖直方向被地面托住，板上骑手带来的那点增量忽略不计）
    cap = ent["mu_ground"] * ent["m"] * abs(10.0) * half
    if ent["wx"] <= LINK_SLOP:
        return 0.0
    j = -ent["vx"] / ent["wx"]
    if abs(j) > cap:
        j = cap if j > 0.0 else -cap
    _impulse(ent, 0.0, 0.0, j, 0.0)
    return abs(j) * abs(ent["vx"])


def _solve_link(link):
    """绳 / 杆 / 过定滑轮的绳：速度级约束（绳只能拉，杆两边都顶）。"""
    first, second = link["ea"], link["eb"]
    ra, rb = link["ra"], link["rb"]
    if link["pulley"] is not None:
        point = link["pulley"]
        ax, ay = _attach_at(first, ra)
        bx, by = _attach_at(second, rb)
        nax, nay = ax - point[0], ay - point[1]
        na = math.hypot(nax, nay)
        nbx, nby = bx - point[0], by - point[1]
        nb = math.hypot(nbx, nby)
        if na < LINK_SLOP or nb < LINK_SLOP:
            return
        nax, nay, nbx, nby = nax / na, nay / na, nbx / nb, nby / nb
        vax, vay = _speed_at(first, ra[0], ra[1])
        vbx, vby = _speed_at(second, rb[0], rb[1])
        rate = nax * vax + nay * vay + nbx * vbx + nby * vby
        k = _inv_mass(first, ra[0], ra[1], nax, nay) +             _inv_mass(second, rb[0], rb[1], nbx, nby)
        if k <= LINK_SLOP:
            return
        j = rate / k
        if link["kind"] == "rope" and j <= 0.0:
            return
        _impulse(first, ra[0], ra[1], -j * nax, -j * nay)
        _impulse(second, rb[0], rb[1], -j * nbx, -j * nby)
        return
    ax, ay = _attach_at(first, ra)
    bx, by = _attach_at(second, rb)
    dx, dy = bx - ax, by - ay
    distance = math.hypot(dx, dy)
    if distance < LINK_SLOP:
        return
    nx, ny = dx / distance, dy / distance
    vax, vay = _speed_at(first, ra[0], ra[1])
    vbx, vby = _speed_at(second, rb[0], rb[1])
    vrel = (vbx - vax) * nx + (vby - vay) * ny
    k = _inv_mass(first, ra[0], ra[1], nx, ny) + _inv_mass(second, rb[0], rb[1], nx, ny)
    if k <= LINK_SLOP:
        return
    j = vrel / k
    if link["kind"] == "rope" and j <= 0.0:
        return                                    # 绳松了：没有张力，谁也不拉
    _impulse(first, ra[0], ra[1], j * nx, j * ny)
    _impulse(second, rb[0], rb[1], -j * nx, -j * ny)


def _project_link(link):
    """位置投影：绳 / 杆不许超过长度（按逆质量分配修正量，重的一边少动）。"""
    first, second = link["ea"], link["eb"]
    ra, rb = link["ra"], link["rb"]
    length = link["length"]
    if length <= LINK_SLOP:
        return
    ax, ay = _attach_at(first, ra)
    bx, by = _attach_at(second, rb)
    if link["pulley"] is not None:
        point = link["pulley"]
        nax, nay = ax - point[0], ay - point[1]
        na = math.hypot(nax, nay)
        nbx, nby = bx - point[0], by - point[1]
        nb = math.hypot(nbx, nby)
        if na < LINK_SLOP or nb < LINK_SLOP:
            return
        excess = na + nb - length
        if excess <= LINK_SLOP:
            return
        ka = _inv_mass(first, ra[0], ra[1], nax / na, nay / na)
        kb = _inv_mass(second, rb[0], rb[1], nbx / nb, nby / nb)
        total = ka + kb
        if total <= LINK_SLOP:
            return
        first["x"] -= nax / na * excess * ka / total
        first["y"] -= nay / na * excess * ka / total
        second["x"] -= nbx / nb * excess * kb / total
        second["y"] -= nby / nb * excess * kb / total
        return
    dx, dy = bx - ax, by - ay
    distance = math.hypot(dx, dy)
    excess = distance - length
    if excess <= LINK_SLOP:
        return
    nx, ny = dx / distance, dy / distance
    ka = _inv_mass(first, ra[0], ra[1], nx, ny)
    kb = _inv_mass(second, rb[0], rb[1], nx, ny)
    total = ka + kb
    if total <= LINK_SLOP:
        return
    first["x"] += nx * excess * ka / total
    first["y"] += ny * excess * ka / total
    second["x"] -= nx * excess * kb / total
    second["y"] -= ny * excess * kb / total


def _find_track(geoms, hosts, x, y, tol):
    """找离 (x, y) 最近、且在容差内的轨道：返回 (轨道号, 弧长) 或 None。"""
    best = None
    for index, geom in enumerate(geoms):
        host = hosts[index]
        qx, qy = _world_to_local(host, x, y)
        s, point, _tangent = _geom_project(geom, qx, qy, None)
        if s < -LINK_SLOP or s > geom["length"] + LINK_SLOP:
            continue
        px, py = _local_to_world(host, point[0], point[1])
        distance = math.hypot(px - x, py - y)
        if distance > tol:
            continue
        if best is None or distance < best[0]:
            best = (distance, index, min(geom["length"], max(0.0, s)))
    if best is None:
        return None
    return best[1], best[2]


def _switch_track(ent, current, geoms, hosts):
    """骑手跑出当前轨道的端点：接得上就换到相邻轨道，接不上就离开轨道（转自由运动）。"""
    best = None
    for index, geom in enumerate(geoms):
        if index == current:
            continue
        host = hosts[index]
        qx, qy = _world_to_local(host, ent["x"], ent["y"])
        s, point, _tangent = _geom_project(geom, qx, qy, None)
        if s < -LINK_SLOP or s > geom["length"] + LINK_SLOP:
            continue
        px, py = _local_to_world(host, point[0], point[1])
        distance = math.hypot(px - ent["x"], py - ent["y"])
        if distance > LINK_SWITCH_TOL:
            continue
        if best is None or distance < best[0]:
            best = (distance, index, min(geom["length"], max(0.0, s)), px, py)
    if best is None:
        ent["track"] = None
        ent["s"] = 0.0
        return False
    ent["track"] = best[1]
    ent["s"] = best[2]
    ent["x"], ent["y"] = best[3], best[4]
    return True


def _project_rider(ent, geoms, hosts):
    """把骑手投影回轨道线；跑出端点就换轨，换不上就放开（老师的题里物块从弧顶飞出去靠这个）。"""
    index = ent["track"]
    if index is None:
        return
    geom = geoms[index]
    host = hosts[index]
    qx, qy = _world_to_local(host, ent["x"], ent["y"])
    s, point, _tangent = _geom_project(geom, qx, qy, ent.get("s"))
    if s < -LINK_SLOP or s > geom["length"] + LINK_SLOP:
        _switch_track(ent, index, geoms, hosts)
        return
    s = min(geom["length"], max(0.0, s))
    ent["s"] = s
    px, py = _local_to_world(host, point[0], point[1])
    ent["x"], ent["y"] = px, py


def _linked_collisions(ents, restitution):
    """两两查碰撞（圆盘近似）：跟旧路径同一套公式与文案，只是作用在实体上。"""
    hits = []
    for i in range(len(ents)):
        for j in range(i + 1, len(ents)):
            first, second = ents[i], ents[j]
            if first["escaped"] or second["escaped"]:
                continue
            radius = first.get("radius", first.get("R", 0.06)) +                 second.get("radius", second.get("R", 0.06))
            dx, dy = second["x"] - first["x"], second["y"] - first["y"]
            distance = math.hypot(dx, dy)
            if distance > radius or distance < 1e-12:
                continue
            nx, ny = dx / distance, dy / distance
            ma, mb = first["m"], second["m"]
            va = [first["vx"], first["vy"]]
            vb = [second["vx"], second["vy"]]
            vrel = (vb[0] - va[0]) * nx + (vb[1] - va[1]) * ny
            overlap = radius - distance
            if vrel < -1e-9:                       # 真的在接近才算法碰撞
                impulse = -(1.0 + restitution) * vrel / (first["invm"] + second["invm"])
                before = [ma * va[0] + mb * vb[0], ma * va[1] + mb * vb[1]]
                ke_before = 0.5 * ma * (va[0] ** 2 + va[1] ** 2) +                     0.5 * mb * (vb[0] ** 2 + vb[1] ** 2)
                _impulse(first, 0.0, 0.0, -impulse * nx, -impulse * ny)
                _impulse(second, 0.0, 0.0, impulse * nx, impulse * ny)
                va = [first["vx"], first["vy"]]
                vb = [second["vx"], second["vy"]]
                ke_after = 0.5 * ma * (va[0] ** 2 + va[1] ** 2) +                     0.5 * mb * (vb[0] ** 2 + vb[1] ** 2)
                after = [ma * va[0] + mb * vb[0], ma * va[1] + mb * vb[1]]
                hits.append({
                    "i": i, "j": j, "va": va, "vb": vb,
                    "at": [(first["x"] + second["x"]) / 2, (first["y"] + second["y"]) / 2],
                    "loss": max(0.0, ke_before - ke_after),
                    "text": "%s 和 %s 碰撞：碰前动量 (%s, %s) -> 碰后 (%s, %s)，动能损失 %s J" % (
                        first["name"], second["name"],
                        round(before[0], 3), round(before[1], 3), round(after[0], 3),
                        round(after[1], 3), round(max(0.0, ke_before - ke_after), 4)),
                })
            first["x"] -= nx * overlap / 2
            first["y"] -= ny * overlap / 2
            second["x"] += nx * overlap / 2
            second["y"] += ny * overlap / 2
    return hits


def _entity_kinetic(ent):
    return 0.5 * ent["m"] * (ent["vx"] ** 2 + ent["vy"] ** 2) +         0.5 * ent["inertia"] * ent["omega"] ** 2


def _entity_potential(ent, gravity):
    return -ent["m"] * (gravity[0] * ent["x"] + gravity[1] * ent["y"])

def _link_setup(world, bodies, plates, wheels):
    """解析弹簧 / 连接的引用，补齐默认长度（长度写回 raw，界面上要显示）。"""
    anchors = world.get("anchors") or []
    springs = []
    for raw in world.get("springs") or []:
        first = _ref_entity(raw["a"], bodies, plates, wheels, anchors)
        second = _ref_entity(raw["b"], bodies, plates, wheels, anchors)
        item = {"name": raw["name"], "ea": first, "eb": second,
                "ra": raw["at_a"] or [0.0, 0.0], "rb": raw["at_b"] or [0.0, 0.0],
                "k": raw["k"], "rest": raw["rest"], "damping": raw["damping"],
                "length": 0.0, "force": 0.0, "energy": 0.0}
        _spring_state(item)
        springs.append(item)
    links = []
    for raw in world.get("links") or []:
        first = _ref_entity(raw["a"], bodies, plates, wheels, anchors)
        pulley = None
        if raw["b"] is not None:
            second = _ref_entity(raw["b"], bodies, plates, wheels, anchors)
            if raw.get("pulley") is not None:
                point = _ref_entity(raw["pulley"], bodies, plates, wheels, anchors)
                pulley = [point["x"], point["y"]]
        else:
            second = _ref_entity(raw["anchor"], bodies, plates, wheels, anchors)
        item = {"name": raw["name"], "kind": raw["kind"], "ea": first, "eb": second,
                "ra": raw["at_a"] or [0.0, 0.0], "rb": raw["at_b"] or [0.0, 0.0],
                "pulley": pulley, "length": raw["length"]}
        ax, ay = _attach_at(first, item["ra"])
        bx, by = _attach_at(second, item["rb"])
        if pulley is not None:
            geometric = math.hypot(ax - pulley[0], ay - pulley[1]) + \
                math.hypot(bx - pulley[0], by - pulley[1])
        else:
            geometric = math.hypot(bx - ax, by - ay)
        if item["length"] <= 0.0:
            item["length"] = geometric
        if item["length"] <= LINK_SLOP:
            raise SceneError("连接「%s」的长度是 0：给个 length，或者让两端隔开一点" % item["name"])
        raw["length"] = item["length"]
        links.append(item)
    return springs, links


def _project_wheel(ent):
    """轮子只在地面上滚：中心高度就是半径（竖直方向没有自由度）。"""
    ent["y"] = ent["R"]
    ent["vy"] = 0.0


def _follow_plate(ent, plates, dt):
    """挂在一块板上的轮子：位置跟着板的位姿走（纯画法，不改板的动力学）。"""
    plate = plates[ent["plate"]]
    rx, ry = _offset(plate, ent["off"])
    ent["x"], ent["y"] = plate["x"] + rx, plate["y"] + ry
    vx, vy = _speed_at(plate, rx, ry)
    ent["vx"], ent["vy"] = vx, vy
    if ent["roll"]:
        ent["omega"] = -vx / ent["R"]
    ent["angle"] += ent["omega"] * dt


def _linked_energy(bodies, plates, wheels, springs, gravity):
    total = 0.0
    for group in (bodies, plates, wheels):
        for ent in group:
            total += _entity_kinetic(ent) + _entity_potential(ent, gravity)
    for spring in springs:
        total += spring["energy"]
    return total


def _linked_momentum(bodies, plates, wheels):
    total = [0.0, 0.0]
    for group in (bodies, plates, wheels):
        for ent in group:
            total[0] += ent["m"] * ent["vx"]
            total[1] += ent["m"] * ent["vy"]
    return total


def _linked_frame(bodies, plates, wheels, springs, heat, time):
    """一帧：旧键一个不少，新键按协议第 1 节加 plates / wheels / springs / heat。"""
    # 只有轮子、没有物块的场景（轮子纯滚动那条例子）也得出帧：主量先给 0，别让界面拿不到帧
    primary = bodies[0] if bodies else {"x": 0.0, "y": 0.0, "vx": 0.0, "vy": 0.0, "track": None}
    frame = {
        "t": round(time, 4),
        "x": round(primary["x"], 4), "y": round(primary["y"], 4),
        "vx": round(primary["vx"], 4), "vy": round(primary["vy"], 4),
        "speed": round(math.hypot(primary["vx"], primary["vy"]), 4),
        "on": primary["track"] is not None,
        "bodies": [[round(ent["x"], 4), round(ent["y"], 4), round(ent["vx"], 4),
                    round(ent["vy"], 4), 1 if ent["track"] is not None else 0]
                   for ent in bodies],
        "plates": [[round(ent["x"], 4), round(ent["y"], 4), round(ent["vx"], 4),
                    round(ent["vy"], 4), round(math.degrees(ent["angle"]), 4)]
                   for ent in plates],
        "wheels": [[round(ent["x"], 4), round(ent["y"], 4),
                    round(math.degrees(ent["angle"]), 4), round(ent["vx"], 4)]
                   for ent in wheels],
        "springs": [[round(_spring_state(ent), 4), round(ent["force"], 4)] for ent in springs],
        "heat": round(heat, 4),
    }
    return frame


def _run_linked(world):
    """板块 / 挂轨道 / 弹簧 / 轮子 / 连接体的主循环。

    每一步：半步踢（重力、电场、弹簧） -> 解约束（骑手法向+摩擦、绳/杆/滑轮、轮子滚动、板地摩擦）
    -> 漂移 -> 位置投影（骑手贴轨道、换轨/离轨、连接长度、挂板轮子） -> 半步踢 -> 解约束 -> 碰撞。
    板、骑手、摆球是联立解的：摩擦的反作用力当步就回到板上，不是拿上一步的旧坐标显式耦合。
    """
    gravity = world["gravity"]
    gx, gy = gravity
    dt = max(1e-5, min(0.01, float(world["dt"])))
    duration = max(0.05, min(120.0, float(world["duration"])))
    sample_every = max(1, int(round(0.005 / dt)))
    restitution = world["restitution"]
    sub_dt = dt / LINK_SUBSTEPS
    sub_half = 0.5 * sub_dt

    geoms = [_track_geom(track) for track in world["tracks"]]
    bodies, plates, wheels = _entity_list(world)
    hosts = [plates[geom["plate"]] if geom["plate"] is not None else None
             for geom in geoms]

    for ent in bodies:
        hit = _find_track(geoms, hosts, ent["x"], ent["y"],
                          max(LINK_SWITCH_TOL, ent["radius"]))
        if hit is not None:
            ent["track"], ent["s"] = hit[0], hit[1]
    for ent in bodies:
        if ent["track"] is not None:
            _project_rider(ent, geoms, hosts)

    springs, links = _link_setup(world, bodies, plates, wheels)
    energy0 = _linked_energy(bodies, plates, wheels, springs, gravity)
    momentum0 = _linked_momentum(bodies, plates, wheels)

    heat = 0.0
    spring_loss = 0.0
    collision_loss = 0.0
    landing = 0.0
    work_electric = 0.0
    collision_count = 0
    events = []
    frames = []
    time = 0.0
    steps = 0

    for ent in bodies:
        if ent["track"] is not None:
            events.append(_event(0.0, "on", "%s 骑在轨道上开始运动" % ent["name"],
                                 [ent["x"], ent["y"]], [ent["vx"], ent["vy"]]))
    for link in links:
        if link["pulley"] is not None:
            events.append(_event(0.0, "hang", "%s 绕过定滑轮，绳长 %s m" %
                                 (link["name"], round(link["length"], 3)),
                                 link["pulley"], [0.0, 0.0]))
        else:
            events.append(_event(0.0, "hang", "%s 挂住了：%s m，%s" %
                                 (link["name"], round(link["length"], 3),
                                  "杆（刚性距离）" if link["kind"] == "rod" else "绳"),
                                 [link["ea"]["x"], link["ea"]["y"]],
                                 [link["ea"]["vx"], link["ea"]["vy"]]))
    for spring in springs:
        events.append(_event(0.0, "spring", "弹簧 %s：原长 %s m，劲度系数 %s N/m" %
                             (spring["name"], round(spring["rest"], 3),
                              round(spring["k"], 3)),
                             [spring["ea"]["x"], spring["ea"]["y"]], [0.0, 0.0]))
    for ent in wheels:
        if ent["roll"]:
            events.append(_event(0.0, "roll", "轮子 %s 在地面上纯滚动（v = ωR）" % ent["name"],
                                 [ent["x"], ent["y"]], [ent["vx"], 0.0]))

    def kick(half_step):
        nonlocal work_electric, spring_loss
        for ent in bodies:
            if ent["escaped"]:
                continue
            _impulse(ent, 0.0, 0.0, ent["m"] * gx * half_step, ent["m"] * gy * half_step)
            if ent["charge"]:
                electric, magnetic = _field_at(world, [ent["x"], ent["y"]])
                force_x = electric[0] + magnetic * ent["vy"]
                force_y = electric[1] - magnetic * ent["vx"]
                _impulse(ent, 0.0, 0.0, ent["charge"] * force_x * half_step,
                         ent["charge"] * force_y * half_step)
                work_electric += ent["charge"] * (electric[0] * ent["vx"] +
                                                  electric[1] * ent["vy"]) * half_step
        for ent in plates:
            if ent["fixed"] or ent["escaped"]:
                continue
            _impulse(ent, 0.0, 0.0, ent["m"] * gx * half_step, ent["m"] * gy * half_step)
        for ent in wheels:
            if ent["kinematic"] or ent["escaped"]:
                continue
            _impulse(ent, 0.0, 0.0, ent["m"] * gx * half_step, ent["m"] * gy * half_step)
        for spring in springs:
            spring_loss += _spring_step(spring, half_step)

    def solve(half_step):
        nonlocal heat
        normal_impulse = {}
        for _round in range(LINK_RIDER_PASSES):
            for ent in bodies:
                if ent["escaped"] or ent["track"] is None:
                    continue
                magnitude = _solve_rider(ent, geoms, hosts)
                normal_impulse[ent["slot"]] = normal_impulse.get(ent["slot"], 0.0) + magnitude
        for ent in bodies:
            if ent["escaped"] or ent["track"] is None:
                continue
            heat += _solve_friction(ent, geoms, hosts, normal_impulse.get(ent["slot"], 0.0))
        for _round in range(LINK_JOINT_PASSES):
            for link in links:
                _solve_link(link)
        for ent in wheels:
            if ent["kinematic"] or ent["escaped"]:
                continue
            _solve_roll(ent)
        for ent in plates:
            heat += _solve_ground_friction(ent, half_step)

    def drift(dt_step):
        for group in (bodies, plates, wheels):
            for ent in group:
                if ent["escaped"] or ent.get("kinematic") or ent.get("fixed"):
                    continue
                ent["x"] += ent["vx"] * dt_step
                ent["y"] += ent["vy"] * dt_step
                ent["angle"] += ent["omega"] * dt_step

    def project():
        nonlocal events
        for ent in bodies:
            if ent["escaped"]:
                continue
            if ent["track"] is not None:
                _project_rider(ent, geoms, hosts)
            if ent["track"] is None and ent["y"] < LINK_ESCAPE_Y:
                ent["escaped"] = True
                events.append(_event(time, "gone", "%s 跑出场景了" % ent["name"],
                                     [ent["x"], ent["y"]], [ent["vx"], ent["vy"]]))
        for ent in wheels:
            if ent["escaped"] or ent["kinematic"]:
                continue
            _project_wheel(ent)
            if ent["y"] < LINK_ESCAPE_Y:
                ent["escaped"] = True
                events.append(_event(time, "gone", "轮子 %s 跑出场景了" % ent["name"],
                                     [ent["x"], ent["y"]], [ent["vx"], ent["vy"]]))
        for ent in plates:
            if ent["escaped"] or ent["fixed"] or ent["support"] == "ground":
                continue
            if ent["y"] < LINK_ESCAPE_Y:
                ent["escaped"] = True
                events.append(_event(time, "gone", "板 %s 掉出场景了" % ent["name"],
                                     [ent["x"], ent["y"]], [ent["vx"], ent["vy"]]))
        for link in links:
            _project_link(link)
        for ent in wheels:
            if ent["kinematic"] and not ent["escaped"]:
                _follow_plate(ent, plates, sub_dt)

    def collide():
        nonlocal collision_loss, collision_count
        hits = _linked_collisions(bodies + wheels, restitution)
        for hit in hits:
            collision_count += 1
            collision_loss += hit["loss"]
            events.append(_event(time, "bump", hit["text"], hit["at"], hit["va"]))

    while steps < MAX_STEPS and time < duration - 1e-12:
        steps += 1
        for _sub in range(LINK_SUBSTEPS):
            kick(sub_half)
            solve(sub_half)
            drift(sub_dt)
            project()
            kick(sub_half)
            solve(sub_half)
            collide()
        time += dt
        if steps % sample_every == 0 or steps == 1:
            frames.append(_linked_frame(bodies, plates, wheels, springs, heat, time))
        if bodies and all(ent["escaped"] for ent in bodies):
            break
    if not frames:
        frames.append(_linked_frame(bodies, plates, wheels, springs, heat, time))

    kinetic = 0.0
    potential = 0.0
    spring_energy = 0.0
    for group in (bodies, plates, wheels):
        for ent in group:
            kinetic += _entity_kinetic(ent)
            potential += _entity_potential(ent, gravity)
    for spring in springs:
        _spring_state(spring)
        spring_energy += spring["energy"]
    energy1 = kinetic + potential + spring_energy
    momentum1 = _linked_momentum(bodies, plates, wheels)
    primary = bodies[0] if bodies else {"x": 0.0, "y": 0.0, "vx": 0.0, "vy": 0.0, "track": None}
    values = {
        "time": round(time, 3), "x": round(primary["x"], 4), "y": round(primary["y"], 4),
        "speed": round(math.hypot(primary["vx"], primary["vy"]), 4),
        "on_track": primary["track"] is not None,
        "heat": round(heat, 4), "work_electric": round(work_electric, 4),
        "belt_work": 0.0,
        "landing_loss": round(landing, 4), "collision_loss": round(collision_loss, 4),
        "collision_count": collision_count,
        "kinetic": round(kinetic, 4), "potential": round(potential, 4),
        "energy_start": round(energy0, 4), "energy_end": round(energy1, 4),
        "energy_gap": round(energy1 - energy0 + heat + landing + collision_loss +
                            spring_loss - work_electric, 4),
        "momentum_start": [round(momentum0[0], 4), round(momentum0[1], 4)],
        "momentum_end": [round(momentum1[0], 4), round(momentum1[1], 4)],
        "bodies_end": [[round(ent["x"], 4), round(ent["y"], 4), round(ent["vx"], 4),
                        round(ent["vy"], 4), 1 if ent["track"] is not None else 0]
                       for ent in bodies],
        "plates_end": [[round(ent["x"], 4), round(ent["y"], 4), round(ent["vx"], 4),
                        round(ent["vy"], 4)] for ent in plates],
        "wheels_end": [[round(ent["x"], 4), round(ent["y"], 4),
                        round(math.degrees(ent["angle"]), 4), round(ent["vx"], 4),
                        round(ent["omega"], 4)] for ent in wheels],
        "spring_energy": round(spring_energy, 4),
        "spring_loss": round(spring_loss, 4),
        "steps": steps,
    }
    return {"ok": True, "kind": "sim2d", "scene": world, "frames": frames,
            "events": events, "values": values, "cards": _cards(values, events),
            "force_keys": []}

