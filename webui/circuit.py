# -*- coding: utf-8 -*-
"""电路的求解与作图（M7 补做）：老师自己摆元件、连线，这边算电流电压。

数据模型（就是"自己绘制"的那个电路）：
    components: [{id, type, ...值, at:[列,行], dir:'h'|'v'}]     元件摆在网格上
    wires:      [["b1.1", "r1.2"], ...]                        导线连的是**接线柱**
    -> `nets()` 用并查集把接线柱并成节点，`solve()` 就按节点做直流求解。

为什么这样设计：老师画电路时脑子里想的是"把这两头连起来"，不是"定义节点"。所以只存元件和
导线，节点是算出来的——连线画错了删一条导线就行，不用维护节点表。
"""

import math

#: 元件类型：两端的接线柱名、默认值（界面照这个出调色板）
TYPES = {
    "battery": {"name": "电源", "value": "emf", "default": 12.0, "extra": ("r", 1.0),
                "terminals": ("+", "-"), "symbol": "电源（长线正极）"},
    "resistor": {"name": "定值电阻", "value": "r", "default": 5.0, "extra": None,
                 "terminals": ("1", "2"), "symbol": "矩形"},
    #: 滑动变阻器**四个接线柱都要**：A、B 是电阻丝两端，C、D 都接在滑片上（同一个点）。
    #: 用哪两个接线柱决定了接入电阻：A-C 是左半段、B-C 是右半段、A-B 是整根电阻丝（滑片不起作用）、
    #: C-D 等于把滑片两头接一起（接错了，等于短路）。
    "rheostat": {"name": "滑动变阻器", "value": "rmax", "default": 10.0,
                 "extra": ("ratio", 1.0), "terminals": ("A", "B", "C", "D"),
                 "symbol": "矩形＋滑片箭头（A/B/C/D 四个接线柱）"},
    "lamp": {"name": "小灯泡", "value": "r", "default": 10.0, "extra": None,
             "terminals": ("1", "2"), "symbol": "圆圈＋叉"},
    "ammeter": {"name": "电流表", "value": None, "default": None, "extra": None,
                "terminals": ("+", "-"), "symbol": "圆圈 A"},
    "voltmeter": {"name": "电压表", "value": None, "default": None, "extra": None,
                  "terminals": ("+", "-"), "symbol": "圆圈 V"},
    "switch": {"name": "开关", "value": "closed", "default": True, "extra": None,
               "terminals": ("1", "2"), "symbol": "刀闸"},
    "switch2": {"name": "双掷开关", "value": "to", "default": "A", "extra": None,
                "terminals": ("K", "A", "B"), "symbol": "单刀双掷（刀接 A 或 B）"},
    "capacitor": {"name": "电容", "value": "c", "default": 1e-6, "extra": None,
                  "terminals": ("+", "-"),
                  "symbol": "两条平行线（直流稳定后相当于断路）"},
    "resistance_box": {"name": "电阻箱", "value": "r", "default": 100.0, "extra": None,
                       "terminals": ("1", "2"), "symbol": "矩形＋斜箭头（旋钮读数）"},
    "diode": {"name": "二极管", "value": None, "default": None, "extra": None,
              "terminals": ("+", "-"), "symbol": "三角形＋竖线（+ 是正极）"},
}

SMALL = 1e-6          # 理想电流表/闭合开关/导线的电阻（避免除零，也让矩阵不奇异）
OPEN = 1e9            # 断开的开关


class CircuitError(ValueError):
    """电路有问题（没电源、短路、悬空…）——给老师看得懂的说法，别给一个假读数。"""


def normalize(components, wires):
    """整理并做基本检查（元件类型、接线柱、重复 id）。"""
    seen = set()
    items = []
    for index, raw in enumerate(components or []):
        if not isinstance(raw, dict):
            raise CircuitError("元件要写成对象")
        kind = str(raw.get("type") or "")
        if kind not in TYPES:
            raise CircuitError("不认识的元件：%s" % kind)
        ident = str(raw.get("id") or ("%s%d" % (kind[0], index + 1)))
        if ident in seen:
            raise CircuitError("元件 id 重复：%s" % ident)
        seen.add(ident)
        label = str(raw.get("name") or ident).strip()[:12] or ident
        item = {"id": ident, "name": label, "type": kind,
                "at": [int((raw.get("at") or [0, 0])[0]), int((raw.get("at") or [0, 0])[1])],
                "dir": "v" if str(raw.get("dir")) == "v" else "h"}
        for key in ("emf", "r", "rmax", "ratio", "closed", "value", "c", "to", "name"):
            if key in raw:
                item[key] = raw[key]
        item["_"] = None
        items.append(item)
    labels = {}
    for item in items:                                  # 名字不能撞车：图上要靠它认人
        if item["name"] in labels:
            raise CircuitError("名字 %s 用了两次（%s 和 %s），换一个"
                               % (item["name"], labels[item["name"]], item["id"]))
        labels[item["name"]] = item["id"]
    links = []
    for pair in wires or []:
        via = []
        if isinstance(pair, dict):                     # 手动连的线：带折点
            first, second = str(pair.get("from") or ""), str(pair.get("to") or "")
            for point in pair.get("via") or []:
                if not isinstance(point, (list, tuple)) or len(point) < 2:
                    raise CircuitError("折点要写成 [列, 行]")
                via.append([int(point[0]), int(point[1])])
        elif isinstance(pair, (list, tuple)) and len(pair) == 2:
            first, second = str(pair[0]), str(pair[1])
        else:
            raise CircuitError("一根导线要写两个接线柱，例如 [\"b1.+\", \"r1.1\"]，"
                               "或写成 {from, to, via} 自己走线")
        for terminal in (first, second):
            owner = terminal_of(items, terminal)
            name = terminal.partition(".")[2]
            if not owner or name not in TYPES[owner["type"]]["terminals"]:
                raise CircuitError("没有这个接线柱：%s（%s 的接线柱是 %s）"
                                   % (terminal, owner["id"] if owner else "?",
                                      "/".join(TYPES[owner["type"]]["terminals"]) if owner else "?"))
        links.append({"from": first, "to": second, "via": via})
    if not items:
        raise CircuitError("先摆几个元件")
    return items, links


def terminal_of(items, name):
    """按 "r1.1" 这种名字找元件（找不到给 None）。"""
    head, _sep, _tail = str(name).partition(".")
    for item in items:
        if item["id"] == head:
            return item
    return None


def component_value(item):
    """这个元件在电路里等效的电阻（电压源用诺顿等效时另外处理）。"""
    kind = item["type"]
    if kind in ("resistor", "lamp"):
        return max(SMALL, float(item.get("r", TYPES[kind]["default"])))
    if kind == "rheostat":
        rmax = max(SMALL, float(item.get("rmax", 10.0)))
        ratio = max(0.0, min(1.0, float(item.get("ratio", 1.0))))
        return max(SMALL, rmax * ratio)
    if kind == "ammeter":
        # 理想电流表 0 Ω；填了内阻就按填的算（伏安法内接/外接的差别就出在这儿）
        return max(SMALL, abs(float(item.get("r") or 0.0)))
    if kind == "switch":
        return SMALL if item.get("closed", True) else OPEN
    if kind == "resistance_box":
        return max(SMALL, float(item.get("r", 100.0)))
    if kind == "capacitor":
        return OPEN                      # 直流稳定后电容相当于断路
    if kind == "diode":
        return SMALL if item.get("_diode_on", True) else OPEN
    if kind == "battery":
        return max(SMALL, float(item.get("r", 1.0)))
    # 电压表：默认理想（不走电流）；填了内阻就按内阻算，它会分掉一点电流
    return max(SMALL, abs(float(item.get("r") or 0.0))) or OPEN


def nets(items, links):
    """并查集把接线柱并成节点；返回 (节点表, 每个接线柱属于哪个节点)。"""
    parent = {}

    def find(name):
        parent.setdefault(name, name)
        while parent[name] != name:
            parent[name] = parent[parent[name]]
            name = parent[name]
        return name

    def union(one, two):
        root_a, root_b = find(one), find(two)
        if root_a != root_b:
            parent[root_a] = root_b

    for item in items:
        for terminal in TYPES[item["type"]]["terminals"]:
            find("%s.%s" % (item["id"], terminal))
    for item in items:
        if item["type"] == "rheostat":       # C、D 都在滑片上，本来就是同一个点
            union("%s.C" % item["id"], "%s.D" % item["id"])
    for link in links:
        union(link["from"], link["to"])
    groups = {}
    for name in list(parent):
        groups.setdefault(find(name), []).append(name)
    order = sorted(groups)
    index = dict((root, position) for position, root in enumerate(order))
    return {"groups": [sorted(groups[root]) for root in order],
            "of": dict((name, index[find(name)]) for name in parent),
            "count": len(order)}


def solve(components, wires):
    """直流求解：节点电压、各支路电流、电表读数、功率。

    做法：电源按诺顿等效（E、r -> 电流源 E/r 并联 r），其余元件就是电导；然后写节点方程
    `G·V = I` 用高斯消元解（标准库，不引 numpy）。理想电压表不走电流，只记录两端电压。
    """
    items, links = normalize(components, wires)
    net = nets(items, links)
    nodes = net["count"]
    if nodes < 2:
        raise CircuitError("所有元件都连在一个点上，构不成回路")

    batteries = [item for item in items if item["type"] == "battery"]
    if not batteries:
        raise CircuitError("电路里没有电源")

    ground = 0                                   # 以 0 号节点为参考地
    size = nodes - 1
    def add(a, b, conductance):
        if a != ground:
            matrix[index_of[a]][index_of[a]] += conductance
            if b != ground:
                matrix[index_of[a]][index_of[b]] -= conductance
        if b != ground:
            matrix[index_of[b]][index_of[b]] += conductance
            if a != ground:
                matrix[index_of[b]][index_of[a]] -= conductance

    def inject(node, current):
        if node != ground:
            matrix[index_of[node]][size] += current

    def node_of(terminal):
        return net["of"][terminal]

    index_of = {}                                # 节点 -> 方程里的行号（去掉参考地）
    position = 0
    for node in range(nodes):
        if node == ground:
            continue
        index_of[node] = position
        position += 1

    matrix = None                                # 由 build() 反复重建（二极管要迭代）

    def build():
        """把元件填进节点方程。二极管通断、双掷开关刀位每次重建时按当前状态取。"""
        nonlocal matrix
        matrix = [[0.0] * (size + 1) for _ in range(size)]
        # 每个节点加一条极小的对地电导（gmin）：变阻器有 4 个接线柱，没接的柱子本来就悬空，
        # 没有 gmin 矩阵会奇异、方程解不出来
        for node in range(nodes):
            if node != ground:
                add(node, ground, 1e-9)
        for item in items:
            kind = item["type"]
            terminals = ITEM_TERMINALS(item)
            if kind == "voltmeter":
                # 理想电压表断路（只测电压）；填了内阻就当一个大电阻接进去
                inner = abs(float(item.get("r") or 0.0))
                if inner > 0:
                    add(node_of(terminals[0]), node_of(terminals[1]), 1.0 / max(SMALL, inner))
                continue
            if kind == "capacitor":
                add(node_of(terminals[0]), node_of(terminals[1]), 1.0 / OPEN)
                continue                             # 直流稳定后相当于断路
            if kind == "diode":
                add(node_of(terminals[0]), node_of(terminals[1]),
                    1.0 / (SMALL if item.get("_diode_on", True) else OPEN))
                continue
            if kind == "switch2":
                # 刀接在哪个触点上：K 与 A 或 B 通，另一路断开
                common = node_of("%s.K" % item["id"])
                chosen = str(item.get("to") or "A").upper()
                other = "B" if chosen == "A" else "A"
                add(common, node_of("%s.%s" % (item["id"], chosen)), 1.0 / SMALL)
                add(common, node_of("%s.%s" % (item["id"], other)), 1.0 / OPEN)
                continue
            if kind == "rheostat":
                # 内部就是两段电阻：A—滑片—B；C、D 都在滑片上（先并成一个节点）
                node_a = node_of("%s.A" % item["id"])
                node_b = node_of("%s.B" % item["id"])
                node_c = node_of("%s.C" % item["id"])
                node_d = node_of("%s.D" % item["id"])
                rmax = max(SMALL, float(item.get("rmax", 10.0)))
                ratio = max(0.0, min(1.0, float(item.get("ratio", 1.0))))
                add(node_c, node_d, 1.0 / SMALL)               # C、D 本来就是同一个点
                add(node_a, node_c, 1.0 / max(SMALL, rmax * ratio))
                add(node_c, node_b, 1.0 / max(SMALL, rmax * (1.0 - ratio)))
                continue
            if kind == "battery":
                emf = float(item.get("emf", 12.0))
                inner = max(SMALL, float(item.get("r", 1.0)))
                positive, negative = node_of(terminals[0]), node_of(terminals[1])
                add(positive, negative, 1.0 / inner)               # 内阻
                inject(positive, emf / inner)                      # 诺顿电流源
                inject(negative, -emf / inner)
                continue
            add(node_of(terminals[0]), node_of(terminals[1]), 1.0 / component_value(item))

    def node_voltages():
        values = gauss(matrix, size)
        out = [0.0] * nodes
        for node, slot in index_of.items():
            out[node] = values[slot]
        return out

    build()
    node_voltage = node_voltages()

    # 二极管的通断会影响别的二极管，迭代到自洽（最多 10 轮；正偏导通、反偏截止）。
    # 两端几乎为 0 V（这一路本来就没电流）时保持上一轮状态，不然悬空节点会在两态之间来回跳。
    diodes = [item for item in items if item["type"] == "diode"]
    scale = max([abs(float(x.get("emf", 0.0))) for x in batteries] + [1.0])
    epsilon = 1e-9 * scale
    for _round in range(10):
        changed = False
        for diode in diodes:
            drop = (node_voltage[node_of("%s.+" % diode["id"])]
                    - node_voltage[node_of("%s.-" % diode["id"])])
            if abs(drop) <= epsilon:
                continue
            if (drop > 0) != bool(diode.get("_diode_on", True)):
                diode["_diode_on"] = drop > 0
                changed = True
        if not changed:
            break
        build()
        node_voltage = node_voltages()

    readings = []
    for item in items:
        terminals = ITEM_TERMINALS(item)
        first, second = node_of(terminals[0]), node_of(terminals[1])
        drop = node_voltage[first] - node_voltage[second]
        entry = {"id": item["id"], "label": item.get("name") or item["id"],
                 "type": item["type"], "name": TYPES[item["type"]]["name"],
                 "voltage": round(drop, 6)}
        if item["type"] == "ammeter":
            current = drop / component_value(item)      # 表有内阻就按内阻算
            entry["current"] = round(current, 6)
            entry["reading"] = "%s A" % _show(current, 3)
        elif item["type"] == "voltmeter":
            entry["reading"] = "%s V" % _show(drop, 3)
        elif item["type"] == "rheostat":
            rmax = max(SMALL, float(item.get("rmax", 10.0)))
            ratio = max(0.0, min(1.0, float(item.get("ratio", 1.0))))
            used = _rheostat_usage(item, net, node_voltage, links)
            entry["resistance"] = round(used["value"], 4)
            entry["usage"] = used["text"]
            entry["reading"] = used["text"]
            for note in used["notes"]:
                entry.setdefault("notes", []).append(note)
            entry["current"] = round(used["current"], 6)
            entry["power"] = (None if used["value"] == float("inf")
                              else round(used["current"] ** 2 * used["value"], 6))
            entry["slider"] = ratio
        elif item["type"] == "capacitor":
            # 老师要求"要显示存储的电荷量"：Q = C·U（直流稳定后 I=0，U 就是两端电压）
            capacitance = max(0.0, float(item.get("c", TYPES["capacitor"]["default"])))
            charge = capacitance * drop
            entry["reading"] = ("直流稳定：断路（U=%s V），Q=CU=%s C"
                                % (_show(drop, 3), _quantity(charge)))
            entry["current"] = 0.0
            entry["capacitance"] = capacitance
            entry["charge"] = round(charge, 15)
        elif item["type"] == "diode":
            on = bool(item.get("_diode_on", True))
            if abs(drop) <= 1e-9:
                entry["reading"] = "两端 0 V：这一路本来就没有电流，通断都不影响"
                entry["current"] = 0.0
            elif on:
                entry["reading"] = "正向导通（两端几乎 0 V，电流 %s A）" % _show(drop / SMALL, 3)
                entry["current"] = round(drop / SMALL, 6)
            else:
                entry["reading"] = "反向截止（%s V，没有电流）" % _show(drop, 3)
                entry["current"] = 0.0
        elif item["type"] == "switch2":
            entry["reading"] = "刀接在 %s 触点" % str(item.get("to") or "A").upper()
        elif item["type"] == "resistance_box":
            value = max(SMALL, float(item.get("r", 100.0)))
            entry["reading"] = "R箱=%s Ω、I=%s A" % (_show(value, 3), _show(drop / value, 3))
            entry["power"] = round(abs(drop * drop / value), 6)
        elif item["type"] == "battery":
            inner = max(SMALL, float(item.get("r", 1.0)))
            current = (float(item.get("emf", 12.0)) - drop) / inner
            entry["current"] = round(current, 6)
            entry["power"] = round(abs(current * drop), 6)
            entry["reading"] = "I=%s A" % _show(current, 3)
        else:
            resistance = component_value(item)
            current = drop / resistance
            entry["current"] = round(current, 6)
            entry["power"] = round(abs(current * drop), 6)
            entry["reading"] = "U=%s V、I=%s A" % (_show(drop, 3), _show(current, 3))
        readings.append(entry)

    total_current = None
    for item in readings:
        if item["type"] == "battery":
            total_current = item["current"]
    return {"ok": True, "nodes": nodes, "node_voltage": [round(value, 6) for value in node_voltage],
            "readings": readings, "current": total_current,
            "warnings": _warnings(items, net, readings)}


def _rheostat_usage(item, net, node_voltage, links=None):
    """变阻器的接线柱用在哪两段上——**接错了就不正常工作**（这正是老师要看出来的）。

    内部就是两段电阻：A—滑片（rmax×滑片位置）和 滑片—B（rmax×(1−滑片位置)），
    C、D 都在滑片上。所以看每一段有没有电流就知道老师用的是哪两个柱子：
      * 只有左段有电流：用了 A 和滑片 → 接入 rmax×位置
      * 只有右段有电流：用了 B 和滑片 → 接入 rmax×(1−位置)
      * 两段都有电流：用了 A 和 B（整根电阻丝，滑片不起作用）→ 接入 rmax
      * C、D 被接在一起：把滑片两头短接，等于一根导线
    """
    rmax = max(SMALL, float(item.get("rmax", 10.0)))
    ratio = max(0.0, min(1.0, float(item.get("ratio", 1.0))))
    r_left = max(SMALL, rmax * ratio)
    r_right = max(SMALL, rmax * (1.0 - ratio))
    node_a = net["of"]["%s.A" % item["id"]]
    node_b = net["of"]["%s.B" % item["id"]]
    node_c = net["of"]["%s.C" % item["id"]]
    node_d = net["of"]["%s.D" % item["id"]]
    v_a, v_b = node_voltage[node_a], node_voltage[node_b]
    v_slider = node_voltage[node_c]
    i_left = (v_a - v_slider) / r_left
    i_right = (v_slider - v_b) / r_right
    touched = {"A": False, "B": False, "C": False, "D": False}
    for link in (links or []):
        for name in ("A", "B", "C", "D"):
            if link["from"] == "%s.%s" % (item["id"], name) or \
                    link["to"] == "%s.%s" % (item["id"], name):
                touched[name] = True
    # C、D 是同一个点；两边都接线 = 把电路两边通过滑片直接短接（典型接错）
    slider_short = touched["C"] and touched["D"]
    left_on = abs(i_left) > 1e-6
    right_on = abs(i_right) > 1e-6
    if not left_on and not right_on:
        # **这一路没有电流**（开关断开、或者压根没通电）时按"接了哪几个接线柱"判：
        # "接入多少电阻"是变阻器自己的性质，跟有没有电流没关系。
        # 原来这里一律算成 inf，图像就整张画不出来（老师反馈过"只会显示算出来不是个数"）。
        slider_wired = touched["C"] or touched["D"]
        if touched["A"] and touched["B"]:
            left_on = right_on = True
        elif touched["A"] and slider_wired:
            left_on = True
        elif touched["B"] and slider_wired:
            right_on = True
    notes = []
    # 分压接法：A、B 接电源（整根电阻丝都在电路里），滑片 C/D 接负载。
    # 这时候两段都有电流是**正常的**（滑片把电阻丝分成两段，负载跟其中一段并联），
    # 不能像"限流接错"那样报错。
    divider = bool((touched["C"] or touched["D"]) and touched["A"] and touched["B"])
    if slider_short and abs(i_left) > 1e-6 and abs(i_right) > 1e-6:
        notes.append("C、D 接在一起了：滑片两头被短接，等于一根导线（接错）")
    if left_on and right_on and not slider_short and not divider:
        notes.append("用了 A、B 两端：整根电阻丝都接进去了，滑片不起作用（接错）")
    if slider_short:
        value = 0.0
        text = "C、D 短接：接入 0 Ω（等于导线）"
    elif left_on and right_on and divider:
        # 分压：负载接在滑片和某一端之间，看到的是"这一段与（另一段+外电路）并联"，
        # 写不成一个数，就按跟负载并联的那一段报，并说清是分压接法
        value = r_left
        text = "分压接法：A—B 接电源、滑片输出（左段 %.3f Ω、右段 %.3f Ω）" % (r_left, r_right)
    elif left_on and right_on:
        value = rmax
        text = "A—B 整根电阻丝 %.3f Ω" % rmax
    elif left_on:
        value = r_left
        text = "A—滑片 左半段 %.3f Ω" % r_left
    elif right_on:
        value = r_right
        text = "B—滑片 右半段 %.3f Ω" % r_right
    elif not (touched["A"] or touched["B"] or touched["C"] or touched["D"]):
        value = float("inf")
        text = "变阻器没接进电路（一个接线柱都没接线）"
    else:
        value = float("inf")
        text = "这一路没有电流（接错线了：电流没过电阻丝）"
    if value == float("inf"):
        used_current = 0.0
    elif divider:
        used_current = abs(i_left) + abs(i_right)      # 分压：两段都过电流
    elif slider_short:
        used_current = abs(i_left) + abs(i_right)          # 短接那段等于导线
    elif left_on:
        used_current = abs(i_left)                          # A—滑片（含 A—B 那种）
    else:
        used_current = abs(i_right)
    return {"value": value, "text": text, "notes": notes,
            "left": r_left, "right": r_right, "current": used_current,
            "current_left": round(i_left, 6), "current_right": round(i_right, 6)}


def ITEM_TERMINALS(item):
    """这个元件有哪些接线柱（滑动变阻器有 4 个：A、B 是电阻丝两端，C、D 在滑片上）。"""
    return ["%s.%s" % (item["id"], name) for name in TYPES[item["type"]]["terminals"]]


def _warnings(items, net, readings):
    """把"看着能算其实不对"的情况挑出来说清楚（短路、没回路、电表反接）。"""
    notes = []
    for item, source in zip(readings, items):
        if item["type"] == "ammeter" and abs(item.get("current") or 0) > 50:
            notes.append("电流表 %s 的电流大得离谱，八成是短路了（检查有没有跳过用电器）" % item["id"])
        if item["type"] == "diode" and not source.get("_diode_on", True):
            notes.append("二极管 %s 是反接的（正极接在低电位那头），截止、没有电流——"
                         "要让它导通就把 + 和 - 两头对调" % item["id"])
        if item["type"] == "battery":
            emf = abs(float(source.get("emf", 12.0)))
            drop = abs(item.get("voltage") or 0.0)
            # 端电压远低于电动势 = 外电路几乎被短路（内阻上把电压都吃掉了）
            if emf > 1e-9 and drop < 0.2 * emf and abs(item.get("current") or 0) > 1e-3:
                notes.append("电源 %s 的端电压只有 %s V（电动势 %s V）：外电路几乎是短路，"
                             "检查有没有用导线把电源两头直接连起来"
                             % (item["id"], _show(drop, 3), _show(emf, 3)))
    if len(net["groups"]) == 2 and len(items) <= 1:
        notes.append("电路里只有一个元件，构不成回路")
    return notes


def _show(value, digits):
    text = ("%%.%df" % digits) % (float(value) + 0.0)
    if text.endswith("0" * digits) and "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _quantity(value):
    """电荷量这种可能很小的数写得好看点：0.000012 C -> 1.2×10^-5 C（不用上标字符，Win7 字体不一定有）。"""
    def tidy(text):
        return text.rstrip("0").rstrip(".") if "." in text else text

    value = float(value)
    if value == 0:
        return "0"
    magnitude = abs(value)
    if 0.001 <= magnitude < 1000:
        return tidy(_show(value, 4))
    exponent = 0
    scaled = magnitude
    while scaled >= 10:
        scaled /= 10.0
        exponent += 1
    while scaled < 1:
        scaled *= 10.0
        exponent -= 1
    return "%s×10^%d" % (tidy(_show(scaled if value > 0 else -scaled, 3)), exponent)


def gauss(matrix, size):
    """高斯消元（带部分选主元）；矩阵是 size×(size+1)。"""
    for column in range(size):
        pivot = max(range(column, size), key=lambda row: abs(matrix[row][column]))
        if abs(matrix[pivot][column]) < 1e-12:
            raise CircuitError("电路解不出来：有节点悬空或者结构不对（检查连线）")
        if pivot != column:
            matrix[column], matrix[pivot] = matrix[pivot], matrix[column]
        lead = matrix[column][column]
        for slot in range(column, size + 1):
            matrix[column][slot] /= lead
        for row in range(size):
            if row == column:
                continue
            factor = matrix[row][column]
            if factor == 0:
                continue
            for slot in range(column, size + 1):
                matrix[row][slot] -= factor * matrix[column][slot]
    return [matrix[row][size] for row in range(size)]


# ---------------------------------------------------------------- 作图（网格 + 电路符号）

CELL = 90
PAD = 60


def render(components, wires, readings=None, columns=7, rows=5):
    """按网格把元件和导线画成 SVG（接线柱带 data-terminal，界面靠它做点击连线）。"""
    items, links = normalize(components, wires)
    by_id = dict((item["id"], item) for item in items)
    reading_of = dict((row["id"], row) for row in (readings or []))
    width = PAD * 2 + CELL * (columns - 1)
    height = PAD * 2 + CELL * (rows - 1)
    parts = ['<svg class="circuit-svg" xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
             'viewBox="0 0 %d %d" data-circuit="1">' % (width, height, width, height)]
    parts.append('<rect width="%d" height="%d" fill="#fff"/>' % (width, height))

    # 先画导线：两端都按元件的接线柱坐标画正交折线
    for index, link in enumerate(links):
        a = _terminal_point(by_id, link["from"])
        b = _terminal_point(by_id, link["to"])
        if not a or not b:
            continue
        # 连线就是**两个点之间的一条直线段**（跟平面几何的线段一个逻辑），
        # 不拐弯、不横平竖直
        chain = [a] + [[PAD + point[0] * CELL, PAD + point[1] * CELL]
                       for point in link["via"]] + [b]
        points = " ".join("%d,%d" % (point[0], point[1]) for point in chain)
        parts.append('<polyline points="%s" fill="none" stroke="#3a4652" stroke-width="2" '
                     'data-wire="%d"/>' % (points, index))
        parts.append('<polyline points="%s" fill="none" stroke="rgba(0,0,0,0)" '
                     'stroke-width="14" data-wire="%d"/>' % (points, index))

    for item in items:
        parts.append(_draw_item(item, reading_of.get(item["id"])))
    parts.append("</svg>")
    return "".join(parts)


def _label(item):
    """画在图上/提示里的名字：老师改过就用改的，没改就是 id。"""
    return item.get("name") or item["id"]


def _terminal_point(by_id, name):
    head, _sep, tail = str(name).partition(".")
    item = by_id.get(head)
    if item is None:
        return None
    x = PAD + item["at"][0] * CELL
    y = PAD + item["at"][1] * CELL
    if item["type"] == "rheostat":
        ratio = max(0.0, min(1.0, float(item.get("ratio", 1.0))))
        knob = x - 30 + 60 * ratio
        # A、B 是电阻丝两端的接线柱（画在矩形下角），C、D 是滑片上的两个柱子（跟着滑片走）
        # 四个柱子都是**固定**的：下排 A/B 在电阻丝两端下角，上排 C/D 在滑杆两端
        # （左上角、右上角）。跟着动的是红色滑片，不是接线柱。
        return {"A": [x - 40, y + 16], "B": [x + 40, y + 16],
                "C": [x - 40, y - 28], "D": [x + 40, y - 28]}.get(tail, [x, y])
    if item["type"] == "switch2":
        # 单刀双掷：K 是刀（公共端，画在左边），A、B 是两个触点（右边上/下）
        return {"K": [x - 30, y], "A": [x + 30, y - 14], "B": [x + 30, y + 14]}.get(tail, [x, y])
    sign = 1 if tail in ("2", "-") else -1        # 第一个接线柱在元件左边/上边
    if item["dir"] == "h":
        return [x + sign * 30, y]
    return [x, y + sign * 30]


def _draw_item(item, reading=None):
    label = _label(item)
    x = PAD + item["at"][0] * CELL
    y = PAD + item["at"][1] * CELL
    kind = item["type"]
    parts = ['<g data-component="%s">' % item["id"]]
    if kind == "battery":
        parts.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#3a4652" stroke-width="2.5"/>'
                     % (x + 8, y - 12, x + 8, y + 12))
        parts.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#3a4652" stroke-width="5"/>'
                     % (x - 8, y - 7, x - 8, y + 7))
        parts.append(_text(x, y - 22, "ε=%s V" % _show(item.get("emf", 12), 3)))
        parts.append(_text(x, y + 30, "r=%s Ω" % _show(item.get("r", 1), 3)))
        parts.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#3a4652" stroke-width="2"/>'
                     % (x - 30, y, x - 8, y))
        parts.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#3a4652" stroke-width="2"/>'
                     % (x + 8, y, x + 30, y))
    elif kind in ("resistor", "rheostat"):
        parts.append('<rect x="%d" y="%d" width="60" height="22" rx="2" fill="#fff" '
                     'stroke="#3a4652" stroke-width="2"/>' % (x - 30, y - 11))
        parts.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#3a4652" stroke-width="2"/>'
                     % (x - 30, y, x - 30, y))
        if kind == "resistor":
            parts.append(_text(x, y - 18, "%s=%s Ω" % (label, _show(item.get("r", 5), 3))))
        else:
            ratio = max(0.0, min(1.0, float(item.get("ratio", 1.0))))
            # 课本画法：一个矩形 + 一根**竖直箭头**指向它（箭头在哪儿就是滑片在哪儿）。
            # 不画杆子、不画光晕——老师要"简洁明了、拖着就动"。
            knob = x - 30 + 60 * ratio
            parts.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#c0392b" '
                         'stroke-width="2" data-knob="%s"/>'
                         % (knob, y - 30, knob, y - 13, item["id"]))
            parts.append('<polygon points="%d,%d %d,%d %d,%d" fill="#c0392b" data-knob="%s"/>'
                         % (knob, y - 9, knob - 6, y - 21, knob + 6, y - 21, item["id"]))
            # 好抓：透明粗线做热区（看得见的还是那一根细箭头）
            parts.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="rgba(0,0,0,0)" '
                         'stroke-width="18" data-knob="%s"/>'
                         % (knob, y - 22, knob, y - 6, item["id"]))
            # 本体：滑杆（上排两个柱子装在它上面）+ 从滑杆下来的滑片箭头
            parts.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#3a4652" '
                         'stroke-width="2"/>' % (x - 42, y - 28, x + 42, y - 28))
            parts.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#c0392b" '
                         'stroke-width="2.4" data-knob="%s"/>'
                         % (knob, y - 28, knob, y - 12, item["id"]))
            parts.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#3a4652" '
                         'stroke-width="1.4"/>' % (x - 40, y + 16, x - 30, y + 11))
            parts.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#3a4652" '
                         'stroke-width="1.4"/>' % (x + 40, y + 16, x + 30, y + 11))
            # 上排两个柱子就在滑杆两端，不用额外接线；只在柱子上标 C、D
            parts.append(_text(x - 40, y - 34, "C", 11, "middle", "#2b4acb"))
            parts.append(_text(x + 40, y - 34, "D", 11, "middle", "#2b4acb"))
            parts.append(_text(x - 40, y + 30, "A", 11, "middle", "#2b4acb"))
            parts.append(_text(x + 40, y + 30, "B", 11, "middle", "#2b4acb"))
            parts.append(_text(x, y - 48, "%s：%s Ω　滑片 %d%%（下排 A/B 电阻丝两端，上排 C/D 滑片）"
                               % (label, _show(float(item.get("rmax", 10)) * ratio, 3),
                                  round(ratio * 100))))
    elif kind == "lamp":
        parts.append('<circle cx="%d" cy="%d" r="16" fill="#fff" stroke="#3a4652" '
                     'stroke-width="2"/>' % (x, y))
        parts.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#3a4652"/>'
                     % (x - 11, y - 11, x + 11, y + 11))
        parts.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#3a4652"/>'
                     % (x + 11, y - 11, x - 11, y + 11))
    elif kind in ("ammeter", "voltmeter"):
        letter = "A" if kind == "ammeter" else "V"
        parts.append('<circle cx="%d" cy="%d" r="18" fill="#fff" stroke="#3a4652" '
                     'stroke-width="2"/>' % (x, y))
        parts.append('<text x="%d" y="%d" font-size="16" font-weight="600" fill="#3a4652" '
                     'text-anchor="middle">%s</text>' % (x, y + 6, letter))
    elif kind == "capacitor":
        parts.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#3a4652" '
                     'stroke-width="3"/>' % (x - 5, y - 16, x - 5, y + 16))
        parts.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#3a4652" '
                     'stroke-width="3"/>' % (x + 5, y - 16, x + 5, y + 16))
        parts.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#3a4652" '
                     'stroke-width="2"/>' % (x - 30, y, x - 5, y))
        parts.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#3a4652" '
                     'stroke-width="2"/>' % (x + 5, y, x + 30, y))
        parts.append(_text(x, y - 26, "C=%s F" % _show(item.get("c", 1e-6), 6)))
    elif kind == "diode":
        parts.append('<polygon points="%d,%d %d,%d %d,%d" fill="#fff" stroke="#3a4652" '
                     'stroke-width="2"/>' % (x - 12, y - 14, x - 12, y + 14, x + 6, y))
        parts.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#3a4652" '
                     'stroke-width="3"/>' % (x + 6, y - 14, x + 6, y + 14))
        parts.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#3a4652" '
                     'stroke-width="2"/>' % (x - 30, y, x - 12, y))
        parts.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#3a4652" '
                     'stroke-width="2"/>' % (x + 6, y, x + 30, y))
        parts.append(_text(x, y - 24, "D"))
    elif kind == "switch2":
        chosen = str(item.get("to") or "A").upper()
        parts.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#3a4652" '
                     'stroke-width="2"/>' % (x - 30, y, x - 22, y))
        parts.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#3a4652" '
                     'stroke-width="2"/>' % (x + 22, y - 14, x + 30, y - 14))
        parts.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#3a4652" '
                     'stroke-width="2"/>' % (x + 22, y + 14, x + 30, y + 14))
        for point in ((x - 22, y), (x + 22, y - 14), (x + 22, y + 14)):
            parts.append('<circle cx="%d" cy="%d" r="3.5" fill="#3a4652"/>' % point)
        tip = (x + 22, y - 14) if chosen == "A" else (x + 22, y + 14)
        parts.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#c0392b" '
                     'stroke-width="2.5"/>' % (x - 22, y, tip[0], tip[1]))
        parts.append(_text(x, y - 26, "K"))
    elif kind == "resistance_box":
        parts.append('<rect x="%d" y="%d" width="60" height="22" rx="2" fill="#fff" '
                     'stroke="#3a4652" stroke-width="2"/>' % (x - 30, y - 11))
        parts.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#c0392b" '
                     'stroke-width="2"/>' % (x - 26, y + 22, x + 26, y - 22))
        parts.append('<polygon points="%d,%d %d,%d %d,%d" fill="#c0392b"/>'
                     % (x + 26, y - 22, x + 14, y - 22, x + 26, y - 10))
        parts.append(_text(x, y - 22, "%s=%s Ω（电阻箱）"
                           % (label, _show(item.get("r", 100), 3))))
    elif kind == "switch":
        closed = item.get("closed", True)
        parts.append('<circle cx="%d" cy="%d" r="3.5" fill="#3a4652"/>' % (x - 18, y))
        parts.append('<circle cx="%d" cy="%d" r="3.5" fill="#3a4652"/>' % (x + 18, y))
        if closed:
            parts.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#3a4652" '
                         'stroke-width="2"/>' % (x - 18, y, x + 18, y))
        else:
            parts.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#3a4652" '
                         'stroke-width="2"/>' % (x - 18, y, x + 10, y - 18))
        parts.append(_text(x, y + 26, "断开" if not closed else "闭合"))
    if reading and reading.get("reading"):
        color = "#c0392b" if reading["type"] in ("ammeter", "voltmeter") else "#7b8794"
        parts.append('<text x="%d" y="%d" font-size="12" fill="%s" text-anchor="middle">%s'
                     '</text>' % (x, y + 44, color, reading["reading"]))
    for terminal in ITEM_TERMINALS(item):
        point = _terminal_point({item["id"]: item}, terminal)
        parts.append('<circle cx="%d" cy="%d" r="5" fill="#eef2f7" stroke="#4d6bfe" '
                     'data-terminal="%s"/>' % (point[0], point[1], terminal))
    parts.append("</g>")
    return "".join(parts)


def _text(x, y, text, size=12, color="#3a4652", anchor="middle"):
    return ('<text x="%d" y="%d" font-size="%d" fill="%s" text-anchor="%s">%s</text>'
            % (x, y, size, color, anchor, text))


#: 图像里能用的"量"：前缀 -> (中文含义, 单位)
QUANTITY_MEANINGS = {
    "E": ("电动势", "V"), "r": ("内阻", "Ω"),
    "R": ("电阻（变阻器是接入的那一段）", "Ω"),
    "Rmax": ("最大阻值", "Ω"), "k": ("滑片位置（0~1）", ""),
    "U": ("电压", "V"), "I": ("电流", "A"), "P": ("功率", "W"),
    "C": ("电容", "F"), "Q": ("电荷量 Q=CU", "C"),
}

#: 直接写元件的名字时按哪个量（老师说的"x 填 R1 就是 R1 的电阻"）
PRIMARY_QUANTITY = {"battery": "E", "capacitor": "C", "ammeter": "I", "voltmeter": "U",
                    "diode": "U"}
DEFAULT_PRIMARY = "R"

_ELECTRIC_TYPES = ("battery", "resistor", "rheostat", "lamp", "ammeter", "voltmeter",
                   "capacitor", "resistance_box", "diode")


#: 多曲线时的配色（第一条永远是主曲线那个蓝）
SERIES_COLORS = ("#c0392b", "#1e8449", "#8e44ad", "#d68910", "#2874a6")


def _split_series(expression):
    """y 轴写多条曲线用分号隔开：`Ek(甲); Ek(乙)`（中文分号也认）。"""
    text = str(expression or "")
    parts = [part.strip() for part in text.replace("；", ";").split(";")]
    return [part for part in parts if part] or [text.strip()]


def quantity_help():
    """图像旁边的说明表开头那句话（老师："说明书里只需要写像是 Ek() 动能 这样即可"）。"""
    return ("轴上写函数，括号里填元件的名字：R(R1) 是 R1 的电阻、P(R1) 是 R1 的功率。"
            "直接写元件的名字（R1）= 它的主量。几个量能组成算式，比如 U(R1)*I(R1)。")


def function_table():
    """说明表：**只列有哪些函数**，不填元件名、不填当前值。"""
    rows = []
    for prefix in ("E", "r", "R", "Rmax", "k", "U", "I", "P", "C", "Q"):
        meaning, unit = QUANTITY_MEANINGS.get(prefix, (prefix, ""))
        rows.append((prefix + "()", "%s%s" % (meaning, ("／" + unit) if unit else "")))
    return rows


def quantities(items, result):
    """一次求解结果 -> {记号表, 含义, 说明表}。

    记号 = **量 + 元件名字**（`PR1`、`U1`、`Qc1`）；另外把元件的名字本身也放进表里，
    等于它的主量（电阻的 R、电源的 E、电容的 C、表的读数），这样"轴填 R1"就能用。
    """
    rows = dict((row["id"], row) for row in (result.get("readings") or []))
    table, names, legend = {}, {}, []
    for item in items:
        name = item.get("name") or item["id"]
        kind = item["type"]
        row = rows.get(item["id"]) or {}
        values = []
        if kind == "battery":
            values.append(("E", float(item.get("emf", 12.0))))
            values.append(("r", max(SMALL, float(item.get("r", 1.0)))))
        if kind in ("resistor", "lamp", "resistance_box"):
            values.append(("R", max(SMALL, float(item.get("r", 0.0)))))
        if kind == "rheostat":
            rmax = max(SMALL, float(item.get("rmax", 10.0)))
            ratio = max(0.0, min(1.0, float(item.get("ratio", 1.0))))
            used = row.get("resistance", rmax * ratio)
            if used is not None and used != float("inf"):
                values.append(("R", float(used)))
            values.append(("Rmax", rmax))
            values.append(("k", ratio))
        if kind == "capacitor":
            values.append(("C", max(0.0, float(item.get("c", 1e-6)))))
            values.append(("Q", float(row.get("charge", 0.0))))
        if kind in _ELECTRIC_TYPES:
            values.append(("U", float(row.get("voltage", 0.0))))
            if row.get("current") is not None:
                values.append(("I", float(row["current"])))
            power = row.get("power")
            if power is None and kind == "rheostat" and row.get("resistance") == float("inf"):
                power = None                     # 电阻都没算出来，功率更不该乱给
            elif power is None and kind in ("resistor", "lamp", "resistance_box"):
                power = abs(float(row.get("voltage", 0.0)) * float(row.get("current") or 0.0))
            if power is not None:
                values.append(("P", abs(float(power))))
        for prefix, value in values:
            symbol = prefix + name
            table[symbol] = float(value)
            names[symbol] = prefix
            meaning, unit = QUANTITY_MEANINGS.get(prefix, (prefix, ""))
            legend.append(("%s(%s)" % (prefix, name),          # 说明表里就写函数形式
                           "%s 的%s%s" % (name, meaning, ("／" + unit) if unit else ""),
                           float(value)))
        primary = (PRIMARY_QUANTITY.get(kind) or DEFAULT_PRIMARY) + name
        if primary in table:
            table[name] = table[primary]
            names[name] = names[primary]
    return {"table": table, "names": names, "legend": legend}


def describe(expression, names):
    """轴标题：认得出来就写成"P(R1)（R1 的功率／W）"，认不出来就照抄。"""
    text = str(expression or "").strip()
    flat = text.replace("(", "").replace(")", "")          # P(R1) 和 PR1 都认
    for key in names:
        if key.lower() in (text.lower(), flat.lower()):
            prefix = names[key]
            meaning, unit = QUANTITY_MEANINGS.get(prefix, (prefix, ""))
            return "%s（%s%s）" % (text, meaning, ("／" + unit) if unit else "")
    return text


def plot_chart(components, wires, x_expression, y_expression, ratios=None, steps=41,
               current_ratio=None, note="", x_range=None, y_range=None):
    """画 x-y 图：把滑动变阻器扫一遍（或者按老师拖出来的那些位置），每个位置解一次。

    返回图、采样点、以及"当前点在哪儿"——前端靠 `meta` 自己挪红点，不用每帧重新请求。
    """
    import chart as chart_module
    items, _links = normalize(components, wires)
    rheostats = [item for item in items if item["type"] == "rheostat"]
    boxes = [item for item in items if item["type"] == "resistance_box"]
    if not rheostats and not boxes:
        raise CircuitError("电路里没有能连续变的元件：图像要有一个变化的量才画得出来"
                           "（放一个滑动变阻器拖滑片，或者放一个电阻箱——会从它的 1/10 扫到 5 倍）")
    positions = []
    for value in (ratios or []):
        try:
            positions.append(max(0.0, min(1.0, float(value))))
        except (TypeError, ValueError):
            continue
    if len(positions) < 2:
        positions = [index / float(max(1, int(steps) - 1)) for index in range(int(steps))]
    # 只有电阻箱时：按对数扫它的阻值（1/10 倍 -> 5 倍），U-R 图才是条直线
    box_values = []
    if not rheostats and boxes:
        base = max(0.1, float(boxes[0].get("r", 10.0) or 10.0))
        count = max(2, len(positions))
        box_values = [base * (10 ** (-1.0 + 1.7 * index / float(count - 1)))
                      for index in range(count)]
    tables = []
    for order, position in enumerate(positions):
        copy = [dict(item) for item in (components or [])]
        for row in copy:
            if row.get("type") == "rheostat":
                row["ratio"] = position
            elif row.get("type") == "resistance_box" and box_values:
                row["r"] = box_values[order]
        result = solve(copy, wires)
        tables.append(quantities(normalize(copy, wires)[0], result))

    x_expression = str(x_expression or "").strip()
    y_expression = str(y_expression or "").strip()
    if not x_expression or not y_expression:
        raise CircuitError("x 轴和 y 轴都要填一个量，比如 x 填 R滑1、y 填 Ib1")
    # y 轴可以写多条曲线：`Ek(甲); Ek(乙)`
    extra_expressions = [part.strip() for part in _split_series(y_expression)][1:]
    try:
        points, skipped = chart_module.series_of(tables, x_expression,
                                                 _split_series(y_expression)[0],
                                                 lambda entry: entry["table"])
        extra = []
        for order, text in enumerate(extra_expressions):
            group, _ignored = chart_module.series_of(tables, x_expression, text,
                                                     lambda entry: entry["table"])
            extra.append((group, SERIES_COLORS[order % len(SERIES_COLORS)],
                          describe(text, tables[-1]["names"])))
    except chart_module.ChartError as exc:
        raise CircuitError("图像画不出来（%s）。检查变阻器的接线柱：A、B 是电阻丝两端、"
                           "C、D 在滑片上，至少要接一个电阻丝端（A 或 B）和一个滑片端"
                           "（C 或 D）；轴上的记号也要是表里列的函数，比如 R(rh1)、P(rh1)。"
                           % exc)

    # 整张图都没有电流（开关断开？）：功率全是 0，得说一句，不然老师以为程序坏了
    idle = all(abs(float(entry["table"].get("I" + str(item.get("name") or item["id"])) or 0.0))
               < 1e-9
               for entry in tables for item in items if item["type"] == "battery")
    cursor = None
    if current_ratio is not None and not box_values:
        try:
            target = max(0.0, min(1.0, float(current_ratio)))
            cursor = min(range(len(positions)), key=lambda i: abs(positions[i] - target))
        except (TypeError, ValueError):
            cursor = None
    idle_note = "　电路里没有电流（开关断开了？），功率都是 0" if idle else ""
    figure = chart_module.chart(points, describe(x_expression, tables[-1]["names"]),
                                describe(y_expression, tables[-1]["names"]),
                                cursor=cursor,
                                note=(note or ("%d 个采样点" % len(points))) + idle_note,
                                x_range=x_range, y_range=y_range, extra=extra,
                                main_label=describe(_split_series(y_expression)[0],
                                                    tables[-1]["names"]))
    return {"ok": True, "figure": figure, "points": [[round(p[0], 6), round(p[1], 6)]
                                                     for p in points],
            "cursor": cursor,
            "meta": chart_module.meta_of(points, x_range=x_range, y_range=y_range,
                                         extra=[group[0] for group in extra]),
            "legend": chart_module.legend_rows(function_table()),
            "help": quantity_help(), "positions": [round(value, 4) for value in positions],
            "x": x_expression, "y": y_expression,
            "x_label": describe(x_expression, tables[-1]["names"]),
            "y_label": describe(y_expression, tables[-1]["names"]),
            "skipped": skipped or ""}



#: 现成例子（M8）：一键装进电路画板的典型实验。`chart` 是装载后建议的坐标轴
#: （老师要看的曲线：U-I、I-R、P-R 这些）。
EXAMPLES = [
    {"key": "emf", "name": "测电源的电动势和内阻",
     "note": "电流表测干路电流、电压表测路端电压，电阻箱改外电阻。"
             "把 x 轴填 R(箱1)、y 轴填 U(电源)：画出来是一条往下走的直线，"
             "纵轴截距就是电动势 E、斜率绝对值是内阻 r。",
     "chart": {"x": "R(箱1)", "y": "U(电源)"},
     "components": [
         {"id": "b1", "type": "battery", "at": [1, 3], "emf": 3.0, "r": 0.5, "name": "电源"},
         {"id": "s1", "type": "switch", "at": [2, 3], "closed": True, "name": "开关"},
         {"id": "a1", "type": "ammeter", "at": [3, 3], "name": "电流表"},
         {"id": "rb1", "type": "resistance_box", "at": [5, 3], "r": 10.0, "name": "箱1"},
         {"id": "v1", "type": "voltmeter", "at": [1, 1], "name": "电压表"}],
     "wires": [["b1.+", "s1.1"], ["s1.2", "a1.+"], ["a1.-", "rb1.1"], ["rb1.2", "b1.-"],
               ["v1.+", "b1.+"], ["v1.-", "b1.-"]]},
    {"key": "lamp", "name": "小灯泡的伏安特性（分压接法）",
     "note": "滑动变阻器分压：能让灯泡两端电压从 0 开始连续变。"
             "x 轴填 U(灯1)、y 轴填 I(灯1)，一边拖滑片一边看曲线（不是直线，灯泡电阻随温度变）。",
     "chart": {"x": "U(灯1)", "y": "I(灯1)"},
     "components": [
         {"id": "b1", "type": "battery", "at": [0, 3], "emf": 6.0, "r": 0.5, "name": "电源"},
         {"id": "s1", "type": "switch", "at": [1, 3], "closed": True, "name": "开关"},
         {"id": "rh1", "type": "rheostat", "at": [5, 2], "rmax": 10.0, "ratio": 0.5,
          "name": "滑1"},
         {"id": "l1", "type": "lamp", "at": [3, 0], "r": 10.0, "name": "灯1"},
         {"id": "a1", "type": "ammeter", "at": [5, 0], "name": "电流表"},
         {"id": "v1", "type": "voltmeter", "at": [1, 0], "name": "电压表"}],
     "wires": [["b1.+", "s1.1"], ["s1.2", "rh1.A"], ["rh1.B", "b1.-"],
               ["rh1.C", "l1.1"], ["l1.2", "a1.+"], ["a1.-", "b1.-"],
               ["v1.+", "rh1.C"], ["v1.-", "b1.-"]]},
    {"key": "ammeter_outer", "name": "伏安法测电阻（电流表外接）",
     "note": "电流表接在电压表**外面**：电压表的分流让测出来的电阻偏小。"
             "x 轴填 U(表1)（电压表读数）、y 轴填 I(电流表)（电流表读数）：直线斜率就是测出来的电阻。",
     "chart": {"x": "U(表1)", "y": "I(电流表)"},
     "components": [
         {"id": "b1", "type": "battery", "at": [0, 3], "emf": 6.0, "r": 0.5, "name": "电源"},
         {"id": "s1", "type": "switch", "at": [1, 3], "closed": True, "name": "开关"},
         {"id": "rh1", "type": "rheostat", "at": [5, 2], "rmax": 20.0, "ratio": 0.4,
          "name": "滑1"},
         {"id": "r1", "type": "resistor", "at": [3, 1], "r": 5.0, "name": "待测电阻"},
         {"id": "a1", "type": "ammeter", "at": [4, 3], "r": 0.5, "name": "电流表"},
         {"id": "v1", "type": "voltmeter", "at": [2, 0], "r": 3000.0, "name": "表1"}],
     "wires": [["b1.+", "s1.1"], ["s1.2", "rh1.A"], ["rh1.B", "b1.-"],
               ["rh1.C", "a1.+"], ["a1.-", "r1.1"], ["r1.2", "b1.-"],
               ["v1.+", "r1.1"], ["v1.-", "r1.2"]]},
    {"key": "ammeter_inner", "name": "伏安法测电阻（电流表内接）",
     "note": "电流表跟待测电阻**串在一起**再并电压表：电流表也分掉一点电压，测出来的电阻偏大。"
             "跟「电流表外接」那个例子对一下，就知道为什么小电阻用外接、大电阻用内接。",
     "chart": {"x": "U(表1)", "y": "I(电流表)"},
     "components": [
         {"id": "b1", "type": "battery", "at": [0, 3], "emf": 6.0, "r": 0.5, "name": "电源"},
         {"id": "s1", "type": "switch", "at": [1, 3], "closed": True, "name": "开关"},
         {"id": "rh1", "type": "rheostat", "at": [5, 2], "rmax": 20.0, "ratio": 0.4,
          "name": "滑1"},
         {"id": "r1", "type": "resistor", "at": [3, 1], "r": 5.0, "name": "待测电阻"},
         {"id": "a1", "type": "ammeter", "at": [3, 3], "r": 0.5, "name": "电流表"},
         {"id": "v1", "type": "voltmeter", "at": [1, 0], "r": 3000.0, "name": "表1"}],
     "wires": [["b1.+", "s1.1"], ["s1.2", "rh1.A"], ["rh1.B", "b1.-"],
               ["rh1.C", "a1.+"], ["a1.-", "r1.1"], ["r1.2", "b1.-"],
               ["v1.+", "a1.+"], ["v1.-", "r1.2"]]},
    {"key": "limiter", "name": "滑动变阻器：限流接法",
     "note": "变阻器**串**在电路里（用 A、C 两个接线柱）：电压只能从某个值往上调，调不到 0。"
             "跟分压接法比一比，考试里选哪种接法就看这个。",
     "chart": {"x": "R(滑1)", "y": "I(电源)"},
     "components": [
         {"id": "b1", "type": "battery", "at": [0, 3], "emf": 6.0, "r": 0.5, "name": "电源"},
         {"id": "s1", "type": "switch", "at": [1, 3], "closed": True, "name": "开关"},
         {"id": "r1", "type": "resistor", "at": [3, 2], "r": 10.0, "name": "电阻"},
         {"id": "rh1", "type": "rheostat", "at": [5, 2], "rmax": 20.0, "ratio": 0.5,
          "name": "滑1"},
         {"id": "v1", "type": "voltmeter", "at": [3, 0], "name": "电压表"}],
     "wires": [["b1.+", "s1.1"], ["s1.2", "r1.1"], ["r1.2", "rh1.A"], ["rh1.C", "b1.-"],
               ["v1.+", "r1.1"], ["v1.-", "r1.2"]]},
]


def examples():
    """例子表：名字、说明、建议坐标轴（场景等点了哪条再取）。"""
    return [{"key": item["key"], "name": item["name"], "note": item["note"],
             "chart": item.get("chart") or {}}
            for item in EXAMPLES]


def example(key):
    for item in EXAMPLES:
        if item["key"] == str(key):
            return {"key": item["key"], "name": item["name"], "note": item["note"],
                    "chart": item.get("chart") or {},
                    "circuit": {"components": [dict(row) for row in item["components"]],
                                "wires": [list(pair) for pair in item["wires"]]}}
    raise CircuitError("没有这个例子：%s" % key)


def demo():
    """一个默认电路：电源 -> 开关 -> 电流表 -> R1 -> 滑动变阻器 -> 回电源，电压表并接变阻器。"""
    return {
        "components": [
            {"id": "b1", "type": "battery", "emf": 12.0, "r": 1.0, "at": [0, 2], "dir": "h"},
            {"id": "s1", "type": "switch", "closed": True, "at": [1, 0]},
            {"id": "a1", "type": "ammeter", "at": [3, 0]},
            {"id": "r1", "type": "resistor", "r": 2.0, "at": [5, 0]},
            {"id": "rh1", "type": "rheostat", "rmax": 10.0, "ratio": 1.0, "at": [5, 2]},
            {"id": "v1", "type": "voltmeter", "at": [6, 2], "dir": "v"},
        ],
        "wires": [["b1.+", "s1.1"], ["s1.2", "a1.+"], ["a1.-", "r1.1"], ["r1.2", "rh1.A"],
                  ["rh1.C", "b1.-"], ["v1.+", "rh1.A"], ["v1.-", "rh1.C"]],
    }


assert math.isfinite(SMALL)
