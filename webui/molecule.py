# -*- coding: utf-8 -*-
"""分子结构：SMILES（中学能遇到的那个子集）-> 原子/键图 -> 分子式、官能团、2D/3D 坐标。

为什么要自己写：随包已有的 20 万条结构都是 SMILES，而"结构式 / 官能团 / 球棍模型"三样
都要同一张**原子-键图**。用第三方库（SmilesDrawer）试过：它的调用姿势和文档不一致、
画 canvas 还是异步的，而且产物带 `??`/`**`（老内核直接语法错误）。自己写一次三处都能用，
也好保证只用标准库、只写 ES5 能跑的东西。

支持的子集（覆盖教材里的有机物足够）：
  * 元素：C B N O P S F Cl Br I（大小写按 SMILES 规矩：大写=脂肪族，小写=芳香族）
  * 键：默认单键、`=` 双键、`#` 三键、`-` 显式单键
  * 结构：分支 `()`、成环（数字，可重复表示环闭合）、`[]` 里的电荷与氢（如 [NH4+]、[O-]）
  * 隐式氢：按标准价态补（C4 N3 O2 卤素1 S2 P3），芳香碳按 3 价 + 1 氢
不认识的一律抛 ValueError，由调用方说"这个式子我还读不了"，绝不猜。

只使用 Python 3.8.10 标准库。
"""

import math
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
MECHANISMS_PATH = os.path.join(HERE, "chemdata", "chem-mechanisms.json")

#: 标准价态（用来补隐式氢）
VALENCE = {"C": 4, "N": 3, "O": 2, "S": 2, "P": 3, "F": 1, "Cl": 1, "Br": 1, "I": 1,
           "B": 3, "Si": 4}
AROMATIC_ELEMENTS = {"c": "C", "n": "N", "o": "O", "s": "S", "p": "P", "b": "B"}
#: 只认这些元素：遇到 XY 里的 X、或者别的什么字母，要**报读不了**，不能当成原子收下
KNOWN_ELEMENTS = ("C", "B", "N", "O", "P", "S", "F", "Cl", "Br", "I", "Si", "H")


class MoleculeError(ValueError):
    """读不懂的 SMILES（调用方要说"资料未收录/这个式子读不了"，不许猜）。"""


class Atom(object):
    __slots__ = ("index", "element", "aromatic", "charge", "explicit_h", "hydrogens")

    def __init__(self, index, element, aromatic=False, charge=0, explicit_h=None):
        self.index = index
        self.element = element
        self.aromatic = aromatic
        self.charge = charge
        self.explicit_h = explicit_h          # [] 里写明的氢数；None 表示按价态补
        self.hydrogens = 0

    def __repr__(self):
        return "<Atom %d %s%s>" % (self.index, self.element, "*" if self.aromatic else "")


class Molecule(object):
    def __init__(self):
        self.atoms = []
        self.bonds = []                        # [(i, j, order)]，order=1/2/3；芳香键按 1.5 记在 aromatic
        self.aromatic_bonds = set()

    # -- 组装 ---------------------------------------------------------------
    def add_atom(self, element, aromatic=False, charge=0, explicit_h=None):
        atom = Atom(len(self.atoms), element, aromatic, charge, explicit_h)
        self.atoms.append(atom)
        return atom

    def add_bond(self, first, second, order=1, aromatic=False):
        self.bonds.append((first, second, order))
        if aromatic:
            self.aromatic_bonds.add((min(first, second), max(first, second)))

    def neighbours(self, index):
        out = []
        for left, right, order in self.bonds:
            if left == index:
                out.append((right, order))
            elif right == index:
                out.append((left, order))
        return out

    def bond_order_sum(self, index):
        total = 0.0
        for other, order in self.neighbours(index):
            if (min(index, other), max(index, other)) in self.aromatic_bonds:
                total += 1.5
            else:
                total += order
        return total

    # -- 隐式氢 -------------------------------------------------------------
    def fill_hydrogens(self):
        for atom in self.atoms:
            if atom.explicit_h is not None:
                atom.hydrogens = atom.explicit_h
                continue
            valence = VALENCE.get(atom.element)
            if valence is None:
                atom.hydrogens = 0
                continue
            used = self.bond_order_sum(atom.index)
            if atom.aromatic:
                # 芳香碳：环内 1.5+1.5 已经占满 3，剩 1 个氢；带取代基时按实际算
                used = round(used)
            atom.hydrogens = max(0, int(round(valence - used)))
            if atom.charge:                    # 带电原子（[O-]/[NH4+]）按电荷修正
                atom.hydrogens = max(0, atom.hydrogens - abs(atom.charge) * (1 if atom.charge < 0 else 0))
        return self

    # -- 分子式 -------------------------------------------------------------
    def formula(self):
        """Hill 记法：C 在前、H 第二、其余按字母序（教材/检索都是这个口径）。"""
        counts = {}
        for atom in self.atoms:
            counts[atom.element] = counts.get(atom.element, 0) + 1
            if atom.hydrogens:
                counts["H"] = counts.get("H", 0) + atom.hydrogens
        order = []
        if "C" in counts:
            order.append("C")
            if "H" in counts:
                order.append("H")
        for element in sorted(counts):
            if element not in order:
                order.append(element)
        out = []
        for element in order:
            number = counts[element]
            out.append(element if number == 1 else "%s%d" % (element, number))
        return "".join(out)

    def counts(self):
        out = {}
        for atom in self.atoms:
            out[atom.element] = out.get(atom.element, 0) + 1
        out["H"] = out.get("H", 0) + sum(atom.hydrogens for atom in self.atoms)
        return out


# --- 解析 ------------------------------------------------------------------

BOND_SYMBOLS = {"-": 1, "=": 2, "#": 3, ":": 1, "/": 1, "\\": 1}


def parse_smiles(text):
    """SMILES -> Molecule（只认上面写的那个子集，读不了就抛 MoleculeError）。"""
    source = (text or "").strip()
    if not source:
        raise MoleculeError("空的 SMILES")
    molecule = Molecule()
    stack = []
    current = None
    pending_order = None
    rings = {}
    index = 0
    while index < len(source):
        char = source[index]
        if char in BOND_SYMBOLS:
            pending_order = BOND_SYMBOLS[char]
            index += 1
            continue
        if char == "(":
            if current is None:
                raise MoleculeError("分支前面没有原子：%s" % source)
            stack.append(current)
            index += 1
            continue
        if char == ")":
            if not stack:
                raise MoleculeError("括号不配对：%s" % source)
            current = stack.pop()
            index += 1
            continue
        if char.isdigit():
            number = char
            if number in rings:
                other, order = rings.pop(number)
                molecule.add_bond(other, current, order or pending_order or 1,
                                  aromatic=_both_aromatic(molecule, other, current))
            else:
                rings[number] = (current, pending_order)
            pending_order = None
            index += 1
            continue
        if char == "[":
            end = source.find("]", index)
            if end < 0:
                raise MoleculeError("方括号不配对：%s" % source)
            atom = _bracket_atom(source[index + 1:end])
            molecule.add_atom(atom["element"], atom["aromatic"], atom["charge"],
                              atom["hydrogens"])
            atom_index = len(molecule.atoms) - 1
            if current is not None:
                _bond(molecule, current, atom_index, pending_order)
            current = atom_index
            pending_order = None
            index = end + 1
            continue
        if char == "%":                        # %10 这种两位环号
            number = source[index + 1:index + 3]
            if not number.isdigit():
                raise MoleculeError("百分号后面要跟两位数字：%s" % source)
            if number in rings:
                other, order = rings.pop(number)
                molecule.add_bond(other, current, order or pending_order or 1,
                                  aromatic=_both_aromatic(molecule, other, current))
            else:
                rings[number] = (current, pending_order)
            pending_order = None
            index += 3
            continue
        element = _read_element(source, index)
        if element is None:
            raise MoleculeError("不认识第 %d 个字符：%s" % (index + 1, char))
        aromatic = element[0].islower()
        molecule.add_atom(AROMATIC_ELEMENTS.get(element[0], element) if aromatic else element,
                          aromatic=aromatic)
        atom_index = len(molecule.atoms) - 1
        if current is not None:
            _bond(molecule, current, atom_index, pending_order)
        current = atom_index
        pending_order = None
        index += len(element)
    if stack:
        raise MoleculeError("括号不配对：%s" % source)
    if rings:
        raise MoleculeError("有环没闭合：%s" % source)
    return molecule.fill_hydrogens()


def _both_aromatic(molecule, first, second):
    return (molecule.atoms[first].aromatic and molecule.atoms[second].aromatic)


def _bond(molecule, first, second, order):
    if order is None:
        order = 1
    aromatic = _both_aromatic(molecule, first, second) and order == 1
    if aromatic:
        order = 1
    molecule.add_bond(first, second, order, aromatic=aromatic)


def _read_element(source, index):
    """从 index 开始读一个元素符号（Cl/Br/Si 两个字母优先）；不认识的返回 None。"""
    two = source[index:index + 2]
    if two in ("Cl", "Br", "Si"):
        return two
    if two.capitalize() in ("Cl", "Br", "Si"):
        return two.capitalize()
    one = source[index]
    # 只认 ASCII 字母：`str.isalpha()` 对汉字也是 True，老师往输入框里写「乙醇」时
    # 会被当成两个"元素"画成两个灰球（分子式还显示成"乙醇"）——宁可不认识，也不能瞎猜。
    if not ("A" <= one <= "Z" or "a" <= one <= "z"):
        return None
    if one.isalpha():
        if one.upper() not in ("C", "B", "N", "O", "P", "S", "F", "I", "H") and one.isupper():
            return None
        if one.lower() not in AROMATIC_ELEMENTS and one.islower():
            return None
        return one
    return None


def _bracket_atom(inner):
    """[] 里的原子：[NH4+]、[O-]、[13CH4] 这种（同位素先只看元素）。"""
    text = inner.strip()
    while text and text[0].isdigit():          # 丢掉同位素质量数
        text = text[1:]
    charge = 0
    if "+" in text:
        tail = text.split("+", 1)[1].strip()
        charge = int(tail) if tail.isdigit() else 1
    elif "-" in text:
        tail = text.split("-", 1)[1].strip()
        charge = -(int(tail) if tail.isdigit() else 1)
    symbol = ""
    for char in text:
        if "A" <= char <= "Z" or "a" <= char <= "z":   # 只认 ASCII 字母（汉字不算元素）
            symbol += char
        else:
            break
    if not symbol:
        raise MoleculeError("方括号里没有元素：%s" % inner)
    # 元素符号最多两个字母、且第二个必须小写（Cl/Br/Si），别把 NH4 的 H 也算进元素
    if len(symbol) > 1 and symbol[1].islower():
        element = symbol[:2]
        rest = text[2:]
    else:
        element = symbol[0]
        rest = text[1:]
    aromatic = element[0].islower()
    if aromatic:
        element = AROMATIC_ELEMENTS.get(element[0], element[0].upper())
    hydrogens = 0
    if "H" in rest:
        tail = rest.split("H", 1)[1]
        digits = ""
        while digits.__len__() < len(tail) and tail[len(digits)].isdigit():
            digits += tail[len(digits)]
        hydrogens = int(digits) if digits else 1
    return {"element": element, "aromatic": aromatic, "charge": charge,
            "hydrogens": hydrogens}


# --- 官能团 ----------------------------------------------------------------

#: 官能团识别：都在**原子-键图**上判，不做字符串瞎猜（认错官能团比不认更糟）
GROUP_LABELS = {
    "carboxyl": "羧基 —COOH",
    "ester": "酯基 —COO—",
    "aldehyde": "醛基 —CHO",
    "ketone": "羰基（酮）",
    "hydroxyl": "羟基 —OH",
    "ether": "醚键 —O—",
    "amino": "氨基 —NH2",
    "nitro": "硝基 —NO2",
    "amide": "酰胺键 —CONH—",
    "phenyl": "苯环",
    "double_bond": "碳碳双键 C=C",
    "triple_bond": "碳碳三键 C≡C",
    "halogen": "卤素原子",
    "ester_ring": "酯环",
}


def functional_groups(molecule):
    """图上的官能团（返回中文名列表，按固定顺序去重）。

    判据都在**键和图**上：羧基 = 碳上既有 C=O 又有 C—O—H；酯基 = 碳上既有 C=O 又有 C—O—C；
    醛基 = C=O 的碳还带氢；酮 = C=O 的碳两侧都是碳；醚 = 氧两边都是碳、且都不是羰基碳。
    """
    found = []

    def add(key):
        if key in GROUP_LABELS and GROUP_LABELS[key] not in found:
            found.append(GROUP_LABELS[key])

    def is_carbonyl_carbon(index):
        return any(molecule.atoms[other].element == "O" and order == 2
                   for other, order in molecule.neighbours(index))

    for atom in molecule.atoms:
        if atom.element != "C":
            continue
        double_o = [other for other, order in molecule.neighbours(atom.index)
                    if molecule.atoms[other].element == "O" and order == 2]
        single_o = [other for other, order in molecule.neighbours(atom.index)
                    if molecule.atoms[other].element == "O" and order == 1]
        single_n = [other for other, order in molecule.neighbours(atom.index)
                    if molecule.atoms[other].element == "N" and order == 1]
        if not double_o:
            continue
        if single_o:
            # 单键氧还连着第二个碳 -> 酯；只连着氢 -> 羧基
            add("ester") if len(molecule.neighbours(single_o[0])) == 2 else add("carboxyl")
        elif single_n and atom.hydrogens == 0:
            add("amide")                      # C(=O)—N：酰胺，不是酮也不是氨基
        elif atom.hydrogens >= 1:
            add("aldehyde")
        else:
            add("ketone")
    for atom in molecule.atoms:
        if atom.element != "O":
            continue
        carbons = [other for other, order in molecule.neighbours(atom.index)
                   if molecule.atoms[other].element == "C"]
        doubles = [order for _other, order in molecule.neighbours(atom.index) if order == 2]
        if doubles:
            continue                          # 羰基上的氧不单独算羟基/醚
        if len(carbons) == 1 and not is_carbonyl_carbon(carbons[0]):
            add("hydroxyl")
        elif len(carbons) == 2 and not any(is_carbonyl_carbon(index) for index in carbons):
            add("ether")
    for atom in molecule.atoms:
        if atom.element != "N":
            continue
        oxygens = [(other, order) for other, order in molecule.neighbours(atom.index)
                   if molecule.atoms[other].element == "O"]
        doubles = [other for other, order in oxygens if order == 2]
        if len(oxygens) >= 2 and doubles:
            add("nitro")
        elif any(molecule.atoms[other].element == "C" and order == 1
                 and is_carbonyl_carbon(other)
                 for other, order in molecule.neighbours(atom.index)):
            add("amide")
        else:
            add("amino")
    for atom in molecule.atoms:
        if atom.element in ("F", "Cl", "Br", "I"):
            add("halogen")
    for first, second, order in molecule.bonds:
        left, right = molecule.atoms[first], molecule.atoms[second]
        if left.element != "C" or right.element != "C":
            continue
        if order == 2:
            add("double_bond")
        elif order == 3:
            add("triple_bond")
    if _has_benzene(molecule):
        add("phenyl")
    return found


def _has_benzene(molecule):
    """六元芳香环（苯环）：六个芳香碳首尾相接。"""
    aromatic_carbons = [atom.index for atom in molecule.atoms
                        if atom.aromatic and atom.element == "C"]
    if len(aromatic_carbons) < 6:
        return False
    ring = set()
    for first, second, _order in molecule.bonds:
        if first in aromatic_carbons and second in aromatic_carbons:
            ring.add((min(first, second), max(first, second)))
    if len(ring) < 6:
        return False
    degrees = {}
    for first, second in ring:
        degrees[first] = degrees.get(first, 0) + 1
        degrees[second] = degrees.get(second, 0) + 1
    return sum(1 for count in degrees.values() if count == 2) >= 6


def mechanism_list():
    """反应动画清单（界面下拉用）。"""
    with open(MECHANISMS_PATH, encoding="utf-8") as handle:
        table = json.load(handle)
    return [{"key": item.get("key"), "label": item.get("label")}
            for item in table.get("list") or []]


def reaction_animation(rules_path=None, key=None):
    """分子层面的反应动画数据：反应物一侧、产物一侧，各自带坐标与"断键/成键"标记。

    断键/成键是按**元素对**标的（数据在 chem-mechanisms.json）：不做原子映射（那要专门的
    反应原子映射算法），但课本上要讲的机理要点——"这个 C—Br 断了、新的 C—O 成了"——够用。
    """
    with open(rules_path or MECHANISMS_PATH, encoding="utf-8") as handle:
        table = json.load(handle)
    rule = None
    for item in table.get("list") or []:
        if item.get("key") == key:
            rule = item
            break
    if rule is None:
        return None
    sides = []
    for names, markers, kind in ((rule.get("reactants"), rule.get("break"), "break"),
                                 (rule.get("products"), rule.get("form"), "form")):
        panels = []
        for smiles in names or []:
            molecule = parse_smiles(smiles)
            panels.append(_panel(molecule, markers or [], kind))
        sides.append(panels)
    return {"key": rule.get("key"), "label": rule.get("label"), "note": rule.get("note"),
            "break": rule.get("break") or [], "form": rule.get("form") or [],
            "break_color": BREAK_COLOR, "form_color": FORM_COLOR,
            "reactants": sides[0], "products": sides[1]}


def _panel(molecule, markers, kind):
    """一侧里一个分子的画法：原子（**3D 坐标**）、键（含是否被标记）。

    用 3D 排版（和球棍模型同一套坐标），前端按球棍模型那套投影画——这样动画也能拖着转，
    而且不会像 2D 排版那样几个分子挤成一团。氢也画出来（小球），
    "脱去两个氢""C—H 断裂"才看得见。
    """
    expanded, positions = with_hydrogens(molecule, layout3d(molecule))
    xs = [point[0] for point in positions.values()] or [0.0]
    ys = [point[1] for point in positions.values()] or [0.0]
    zs = [point[2] for point in positions.values()] or [0.0]
    span = max(max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs)) or 1.0
    scale = 90.0 / (span + 1.0)
    cx = (max(xs) + min(xs)) / 2.0
    cy = (max(ys) + min(ys)) / 2.0
    cz = (max(zs) + min(zs)) / 2.0
    atoms = []
    for atom in expanded.atoms:
        x, y, z = positions[atom.index]
        atoms.append({"element": atom.element, "label": atom.element,
                      "x": round((x - cx) * scale, 2),
                      "y": round((y - cy) * scale, 2),
                      "z": round((z - cz) * scale, 2),
                      "color": BALL_COLORS.get(atom.element, "#3a4652"),
                      "r": 7 if atom.element == "H" else 12})
    pairs = set()
    for first, second in markers or []:
        pairs.add((str(first).upper(), str(second).upper()))
    bonds = []
    for first, second, order in expanded.bonds:
        left, right = expanded.atoms[first], expanded.atoms[second]
        marked = (left.element.upper(), right.element.upper()) in pairs or \
                 (right.element.upper(), left.element.upper()) in pairs
        bonds.append({"a": first, "b": second, "order": order, "marked": marked,
                      "kind": kind if marked else ""})
    return {"formula": molecule.formula(), "atoms": atoms, "bonds": bonds,
            "width": round((max(xs) - min(xs)) * scale + 40, 1),
            "height": round((max(ys) - min(ys)) * scale + 40, 1),
            "marked": sum(1 for bond in bonds if bond["marked"])}


def describe(smiles):
    """一句话摘要：分子式 + 官能团（给界面/工具直接用）。"""
    molecule = parse_smiles(smiles)
    return {"formula": molecule.formula(), "groups": functional_groups(molecule),
            "atoms": len(molecule.atoms), "bonds": len(molecule.bonds)}


# --- 环与邻接 --------------------------------------------------------------

def rings(molecule):
    """找出环（每个环给一条原子路径）。做法：BFS 生成树，非树边各自闭出一个环。

    中学要画的环基本就是苯环/环己烷这类单环，这个朴素做法足够，也不会乱猜。
    """
    seen = set()
    tree = set()
    order = []
    for start in range(len(molecule.atoms)):
        if start in seen:
            continue
        queue = [start]
        seen.add(start)
        while queue:
            current = queue.pop(0)
            order.append(current)
            for other, _order in molecule.neighbours(current):
                if other in seen:
                    continue
                seen.add(other)
                tree.add((min(current, other), max(current, other)))
                queue.append(other)
    out = []
    for first, second, _order in molecule.bonds:
        key = (min(first, second), max(first, second))
        if key in tree or key in [item for item in _rings_done(out)]:
            continue
        path = _path_between(molecule, first, second, tree)
        if len(path) >= 3:
            out.append(path)
    return out


def _rings_done(found):
    """已经收进结果的边（避免同一个环被重复算）。"""
    edges = []
    for path in found:
        for index in range(len(path)):
            left, right = path[index], path[(index + 1) % len(path)]
            edges.append((min(left, right), max(left, right)))
    return edges


def _path_between(molecule, first, second, tree):
    """树上 first 到 second 的路径（含两端）。"""
    parent = {first: None}
    queue = [first]
    while queue:
        current = queue.pop(0)
        if current == second:
            break
        for other, _order in molecule.neighbours(current):
            if other in parent or (min(current, other), max(current, other)) not in tree:
                continue
            parent[other] = current
            queue.append(other)
    if second not in parent:
        return []
    path = []
    cursor = second
    while cursor is not None:
        path.append(cursor)
        cursor = parent[cursor]
    return path


def adjacency(molecule):
    out = {}
    for index in range(len(molecule.atoms)):
        out[index] = []
    for first, second, order in molecule.bonds:
        out[first].append((second, order))
        out[second].append((first, order))
    return out


# --- 2D 排版 ---------------------------------------------------------------

def layout2d(molecule):
    """给每个原子一个 2D 坐标（单位长度 = 一根键）。

    链状部分画成锯齿（键角 120°，就是教材里碳链的样子）；环画成正多边形
    （苯环就是正六边形）。返回 {原子序号: (x, y)}。
    """
    positions = {}
    ring_list = rings(molecule)
    for path in ring_list:
        if any(index in positions for index in path):
            continue
        sides = len(path)
        radius = 1.0 / (2.0 * math.sin(math.pi / sides)) if sides > 2 else 1.0
        for order_index, atom_index in enumerate(path):
            angle = 2 * math.pi * order_index / sides + math.pi / 2
            positions[atom_index] = (radius * math.cos(angle), radius * math.sin(angle))
    links = adjacency(molecule)
    # 剩下的按 BFS 摆：第一个没摆的原子放原点，子原子沿着"背离父原子"的方向 ±30°
    for start in range(len(molecule.atoms)):
        if start in positions:
            continue
        positions[start] = (0.0, 0.0)
        queue = [start]
        while queue:
            current = queue.pop(0)
            kids = [index for index, _order in links[current] if index not in positions]
            if not kids:
                continue
            base = _outward_angle(molecule, positions, current)
            for slot, child in enumerate(kids):
                offset = math.radians(30 if slot % 2 == 0 else -30)
                if len(kids) > 2:
                    offset = math.radians(120 * (slot - (len(kids) - 1) / 2.0))
                angle = base + offset
                x, y = positions[current]
                positions[child] = (x + math.cos(angle), y + math.sin(angle))
                queue.append(child)
    return positions


def layout2d3(molecule):
    """2D 排版但补成三维元组（x, y, 0）——补氢的通用函数是按三维写的。"""
    return {index: (value[0], value[1], 0.0)
            for index, value in layout2d(molecule).items()}


def _outward_angle(molecule, positions, index):
    """父原子"往外"的方向：没有任何已摆邻居就向右。"""
    neighbours = [other for other, _order in molecule.neighbours(index) if other in positions]
    if not neighbours:
        return 0.0
    x0, y0 = positions[index]
    vx = vy = 0.0
    for other in neighbours:
        x1, y1 = positions[other]
        length = math.hypot(x0 - x1, y0 - y1) or 1.0
        vx += (x0 - x1) / length
        vy += (y0 - y1) / length
    if abs(vx) < 1e-9 and abs(vy) < 1e-9:
        return 0.0
    return math.atan2(vy, vx)


# --- 2D 结构式（SVG） ------------------------------------------------------

ELEMENT_COLORS = {"O": "#c0392b", "N": "#2f6fd0", "S": "#c8860d", "P": "#c8860d",
                  "F": "#1f9e6e", "Cl": "#1f9e6e", "Br": "#a5442a", "I": "#6b3fa0",
                  "C": "#1b1f24", "H": "#1b1f24"}
#: 球棍模型里小球的上色：碳深、氢浅（教材里的球棍模型就是白氢），比文字标签用的颜色浅一档。
#: 氢别再用近乎纯白——白底上根本看不出来，老师会说"没有氢"；现在配 CSS 里的深色描边
#: （`.chem-struct .mol-ball`），白球也一样清楚。
BALL_COLORS = {"H": "#dbe3ee", "C": "#3a4652", "O": "#d94f4f", "N": "#4a7fd4",
               "S": "#d8a03a", "P": "#d8a03a", "F": "#3fb98a", "Cl": "#3fb98a",
               "Br": "#b45b3e", "I": "#8a5fc0"}
#: 官能团高亮配色（按类型分色，一眼能分清"这是羧基、那是羟基"）
GROUP_COLORS = {
    "carboxyl": "#4d6bfe", "ester": "#12a594", "aldehyde": "#e08a1e",
    "ketone": "#c0392b", "hydroxyl": "#e0518f", "ether": "#c8860d",
    "amino": "#2f6fd0", "nitro": "#a5442a", "amide": "#7d5ba6",
    "phenyl": "#6b3fa0", "double_bond": "#2f9e8f", "triple_bond": "#0f7f9e",
    "halogen": "#1f9e6e",
}
#: 断键/成键的动画配色
BREAK_COLOR = "#c0392b"
FORM_COLOR = "#12a45f"


def _alpha(color, ratio):
    """把 #rrggbb 按比例调成 rgba（高亮块用半透明，别盖住结构式）。"""
    match = re.match(r"^#([0-9a-fA-F]{6})$", color or "")
    if not match:
        return color or "rgba(77,107,254,.14)"
    value = int(match.group(1), 16)
    return "rgba(%d,%d,%d,%.2f)" % ((value >> 16) & 255, (value >> 8) & 255, value & 255, ratio)


def detect_groups(molecule):
    """官能团 + **每个官能团涉及哪些原子**（界面要高亮它们）。

    返回 [{"key", "label", "atoms": [序号…]}]；`functional_groups()` 只是它的名字版。
    """
    links = adjacency(molecule)
    out = []

    def add(key, atoms):
        for item in out:
            if item["key"] == key:
                item["atoms"] = sorted(set(item["atoms"]) | set(atoms))
                return
        out.append({"key": key, "label": GROUP_LABELS.get(key, key), "atoms": sorted(set(atoms)),
                    "color": GROUP_COLORS.get(key, "#4d6bfe")})

    def carbonyl_carbon(index):
        return any(molecule.atoms[other].element == "O" and order == 2
                   for other, order in links[index])

    for atom in molecule.atoms:
        if atom.element != "C":
            continue
        doubles = [other for other, order in links[atom.index]
                   if molecule.atoms[other].element == "O" and order == 2]
        singles = [other for other, order in links[atom.index]
                   if molecule.atoms[other].element == "O" and order == 1]
        nitrogens = [other for other, order in links[atom.index]
                     if molecule.atoms[other].element == "N" and order == 1]
        if doubles:
            if singles:
                if len(links[singles[0]]) == 2:
                    add("ester", [atom.index, doubles[0], singles[0]])
                else:
                    add("carboxyl", [atom.index, doubles[0], singles[0]])
            elif nitrogens and atom.hydrogens == 0:
                add("amide", [atom.index, doubles[0], nitrogens[0]])
            elif atom.hydrogens >= 1:
                add("aldehyde", [atom.index, doubles[0]])
            else:
                add("ketone", [atom.index, doubles[0]])
    for atom in molecule.atoms:
        if atom.element != "O":
            continue
        if [order for _other, order in links[atom.index] if order == 2]:
            continue
        carbons = [other for other, _order in links[atom.index]
                   if molecule.atoms[other].element == "C"]
        if len(carbons) == 1 and not carbonyl_carbon(carbons[0]):
            add("hydroxyl", [atom.index, carbons[0]])
        elif len(carbons) == 2 and not any(carbonyl_carbon(index) for index in carbons):
            add("ether", [atom.index] + carbons)
    for atom in molecule.atoms:
        if atom.element != "N":
            continue
        oxygens = [(other, order) for other, order in links[atom.index]
                   if molecule.atoms[other].element == "O"]
        if len(oxygens) >= 2 and [1 for _other, order in oxygens if order == 2]:
            add("nitro", [atom.index] + [other for other, _order in oxygens])
        elif any(molecule.atoms[other].element == "C" and order == 1
                 and carbonyl_carbon(other) for other, order in links[atom.index]):
            add("amide", [atom.index] + [other for other, order in links[atom.index]
                                         if molecule.atoms[other].element == "C"])
        else:
            add("amino", [atom.index])
    for first, second, order in molecule.bonds:
        left, right = molecule.atoms[first], molecule.atoms[second]
        if left.element == "C" and right.element == "C":
            if order == 2:
                add("double_bond", [first, second])
            elif order == 3:
                add("triple_bond", [first, second])
        if molecule.atoms[first].element in ("F", "Cl", "Br", "I"):
            add("halogen", [first])
        if molecule.atoms[second].element in ("F", "Cl", "Br", "I"):
            add("halogen", [second])
    if _has_benzene(molecule):
        aromatic = [atom.index for atom in molecule.atoms
                    if atom.aromatic and atom.element == "C"]
        add("phenyl", aromatic)
    return out


def render_svg(molecule, width=320, height=240, highlight=True, show_hydrogens=True):
    """把分子画成 2D 结构式（SVG 字符串）。

    **氢也画出来**（小字 H + 短键）：默认打开。老师明确要求"显示上氢"，因为
    "乙醇有 6 个氢""羧基上的氢"这些话，只有把氢写出来才对得上。
    想要教材那种光秃秃的骨架式，把 show_hydrogens 传 False 即可。
    """
    # 官能团要在**原来那张图**上认：补完氢以后羧基的氧多了一个 H 邻居，
    # 会被当成酯基（踩过：乙酸被标成"酯基 —COO—"）
    groups = detect_groups(molecule)
    if show_hydrogens:
        expanded, positions = with_hydrogens(molecule, layout2d3(molecule))
    else:
        expanded, positions = molecule, layout2d3(molecule)
    molecule = expanded
    if not positions:
        return ""
    xs = [point[0] for point in positions.values()]
    ys = [point[1] for point in positions.values()]
    span_x = max(xs) - min(xs) or 1.0
    span_y = max(ys) - min(ys) or 1.0
    scale = min((width - 60) / (span_x + 1.2), (height - 60) / (span_y + 1.2))
    offset_x = (width - span_x * scale) / 2.0 - min(xs) * scale
    offset_y = (height - span_y * scale) / 2.0 + max(ys) * scale      # y 轴翻转

    def screen(index):
        point = positions[index]
        return (offset_x + point[0] * scale, offset_y - point[1] * scale)

    parts = ['<svg class="mol-svg" viewBox="0 0 %d %d" width="%d" height="%d" '
             'xmlns="http://www.w3.org/2000/svg">' % (width, height, width, height)]
    rings_found = rings(molecule)
    double_bonds = {}
    for first, second, order in molecule.bonds:
        key = (min(first, second), max(first, second))
        double_bonds[key] = order
    if highlight:
        for group in groups:
            points = [screen(index) for index in group["atoms"] if index in positions]
            if not points:
                continue
            left = min(point[0] for point in points) - 18
            top = min(point[1] for point in points) - 18
            box_w = max(point[0] for point in points) + 18 - left
            box_h = max(point[1] for point in points) + 18 - top
            parts.append('<rect class="mol-hl" x="%.1f" y="%.1f" width="%.1f" height="%.1f" '
                         'rx="10" fill="%s" stroke="%s"><title>%s</title></rect>'
                         % (left, top, box_w, box_h, _alpha(group["color"], 0.14),
                            _alpha(group["color"], 0.45), group["label"]))
    for path in rings_found:
        if len(path) == 6 and all(molecule.atoms[index].aromatic for index in path):
            center_x = sum(screen(index)[0] for index in path) / 6.0
            center_y = sum(screen(index)[1] for index in path) / 6.0
            radius = min(math.hypot(screen(index)[0] - center_x, screen(index)[1] - center_y)
                         for index in path) * 0.62
            parts.append('<circle class="mol-aromatic" cx="%.1f" cy="%.1f" r="%.1f"/>'
                         % (center_x, center_y, radius))
    for first, second, order in molecule.bonds:
        x1, y1 = screen(first)
        x2, y2 = screen(second)
        key = (min(first, second), max(first, second))
        if key in molecule.aromatic_bonds:
            parts.append('<line class="mol-bond" x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f"/>'
                         % (x1, y1, x2, y2))
            continue
        parts.append('<line class="mol-bond" x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f"/>'
                     % (x1, y1, x2, y2))
        if order >= 2:
            # 双键/三键：平行的短线（教材画法）
            dx, dy = x2 - x1, y2 - y1
            length = math.hypot(dx, dy) or 1.0
            nx, ny = -dy / length * 3.2, dx / length * 3.2
            for shift in range(1, order):
                factor = shift
                parts.append('<line class="mol-bond" x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f"/>'
                             % (x1 + nx * factor * 0.8, y1 + ny * factor * 0.8,
                                x2 + nx * factor * 0.8, y2 + ny * factor * 0.8))
    for atom in molecule.atoms:
        x, y = screen(atom.index)
        if atom.element == "H":
            # 氢画成小字 H（老师要求"显示上氢"）
            parts.append('<text class="mol-atom mol-hydrogen" x="%.1f" y="%.1f" '
                         'text-anchor="middle" dominant-baseline="middle">H</text>'
                         % (x, y))
            continue
        if atom.element == "C" and not atom.aromatic:
            continue
        label = atom.element
        color = ELEMENT_COLORS.get(atom.element, "#1b1f24")
        parts.append('<text class="mol-atom" x="%.1f" y="%.1f" fill="%s" '
                     'text-anchor="middle" dominant-baseline="middle">%s</text>'
                     % (x, y, color, label))
    parts.append('</svg>')
    return "".join(parts)


# --- 3D 球棍模型 -----------------------------------------------------------

#: VSEPR：按"成键数 + 孤对"定构型（中学只用得到直线/平面三角/四面体）
#:
#: 四面体的四个方向分**两套手性**（负号个数为偶 / 为奇）：同一个中心的四个键必须同属一套，
#: 任意两个方向的夹角才是 109.5°。混用两套会算出 70.5°（踩过：乙醇的 C-C-O 画成 70.5°）。
TETRA_EVEN = ((1, 1, 1), (1, -1, -1), (-1, 1, -1), (-1, -1, 1))
TETRA_ODD = ((1, 1, -1), (1, -1, 1), (-1, 1, 1), (-1, -1, -1))
TRIGONAL = ((1, 0, 0), (-0.5, math.sqrt(3) / 2, 0), (-0.5, -math.sqrt(3) / 2, 0))
BOND_LENGTH = {"C": 1.5, "O": 1.4, "N": 1.45, "S": 1.6, "P": 1.6,
               "F": 1.35, "Cl": 1.6, "Br": 1.7, "I": 1.8, "H": 1.1}
#: 标准键角对应的 cos 值：四面体 -1/3、平面三角 -1/2、直线 -1
TETRA_COS = -1.0 / 3.0
COS_TOLERANCE = 0.18


def _normalize(vector):
    length = math.sqrt(sum(value * value for value in vector)) or 1.0
    return tuple(value / length for value in vector)


def _ring_orders(molecule, path):
    """环上每条边的键级（环己烷全是 1，苯环上会有 2 或芳香键）。"""
    out = []
    sides = len(path)
    for index in range(sides):
        first, second = path[index], path[(index + 1) % sides]
        order = 1
        for other, value in molecule.neighbours(first):
            if other == second:
                order = max(order, value)
        if molecule.atoms[first].aromatic and molecule.atoms[second].aromatic:
            order = max(order, 2)
        out.append(order)
    return out


def ring_pucker(molecule, path):
    """环上每个原子要偏离环平面多少（Å）——**饱和环必须折起来**。

    以前所有环都画成正多边形（z 全 0）：苯环这样是对的（本来就平），
    但环己烷就成了纯平面，老师一眼就看出来不对（"环己烷都画成纯平面了"）。
    规则：
      * 芳香环、小环（3、4 元）保持平面；
      * 环上有双键时，**双键两端的碳必须跟取代基共平面**（不能翘出去），它们留在平面里；
      * 其余原子折起来：六元环按**椅式**（±0.25 Å 交替）、五元环按信封式（一个翘 0.5 Å）、
        更大的环也交替折（真实构型更复杂，这里只保证"不是平的"这件事不错）。
    """
    sides = len(path)
    orders = _ring_orders(molecule, path)
    if sides <= 4 or all(molecule.atoms[index].aromatic for index in path):
        return [0.0] * sides
    flat = set()
    for index, order in enumerate(orders):
        if order > 1:                     # 这条边是双键：两端都是 sp2，要留在平面里
            flat.add(index)
            flat.add((index + 1) % sides)
    free = [index for index in range(sides) if index not in flat]
    pucker = [0.0] * sides
    if not free:
        return pucker
    if sides == 5:
        pucker[free[0]] = 0.5             # 信封式
        return pucker
    amplitude = 0.25 if sides == 6 else 0.2
    for position, index in enumerate(free):
        pucker[index] = amplitude if position % 2 == 0 else -amplitude
    return pucker


def layout3d(molecule):
    """按 VSEPR 给每个原子一个 3D 坐标（Å）：链是四面体，双键两端共平面，三键直线，苯环平面。

    这是**教学用的近似构型**（标准键长键角），不是量子化学优化——界面里也这么写。
    """
    positions = {}
    links = adjacency(molecule)
    ring_list = rings(molecule)
    for path in ring_list:
        if any(index in positions for index in path):
            continue
        sides = len(path)
        radius = BOND_LENGTH.get("C", 1.5) / (2.0 * math.sin(math.pi / sides))
        pucker = ring_pucker(molecule, path)
        for order_index, atom_index in enumerate(path):
            angle = 2 * math.pi * order_index / sides
            positions[atom_index] = (radius * math.cos(angle), radius * math.sin(angle),
                                     pucker[order_index])
    for start in range(len(molecule.atoms)):
        if start in positions:
            continue
        positions[start] = (0.0, 0.0, 0.0)
        queue = [start]
        while queue:
            current = queue.pop(0)
            kids = [index for index, _order in links[current] if index not in positions]
            if not kids:
                continue
            slots = _free_directions(molecule, positions, current, len(kids))
            for child, direction in zip(kids, slots):
                length = BOND_LENGTH.get(molecule.atoms[child].element, 1.5)
                x, y, z = positions[current]
                positions[child] = (x + direction[0] * length,
                                    y + direction[1] * length,
                                    z + direction[2] * length)
                queue.append(child)
    return positions


def _free_directions(molecule, positions, index, count):
    """这个原子还能往哪些方向接（扣掉已经占用的方向），按 VSEPR 给一组标准方向。

    关键：新键与**已有的键**之间要成标准键角（四面体 109.5°、平面三角 120°、直线 180°），
    所以不是"随便找个没被占的方向"，而是按 cos 值挑（四面体要挑 -1/3 那一批）。
    """
    atom = molecule.atoms[index]
    orders = [order for _other, order in molecule.neighbours(index)]
    # 构型只看**这个原子自己的成键**：有三键就是直线、有双键或芳香就是平面三角，
    # 其余按四面体（用"邻居数 + 待接数"去判会判错：羧基碳明明有双键却被当成 sp3，踩过）
    if max(orders or [1]) == 3:
        template = [(1, 0, 0), (-1, 0, 0)]                     # 三键：直线
        want_cos = -1.0
    elif max(orders or [1]) == 2 or atom.aromatic:
        template = list(TRIGONAL)                              # 双键/芳环：平面三角
        want_cos = -0.5
    elif atom.element in ("O", "N"):
        template = list(TETRA_EVEN) + list(TETRA_ODD)          # 水/醇/胺：弯曲或三角锥
        want_cos = TETRA_COS
    else:
        template = list(TETRA_EVEN) + list(TETRA_ODD)          # 饱和碳：四面体
        want_cos = TETRA_COS
    taken = []
    x0, y0, z0 = positions[index]
    for other, _order in molecule.neighbours(index):
        if other not in positions:
            continue
        x1, y1, z1 = positions[other]
        taken.append(_normalize((x1 - x0, y1 - y0, z1 - z0)))
    free = []
    for direction in template:
        unit = _normalize(direction)
        if any(abs(sum(unit[axis] * used[axis] for axis in range(3)) - want_cos)
               > COS_TOLERANCE for used in taken):
            continue
        if any(abs(sum(unit[axis] * used[axis] for axis in range(3))) > 0.999
               for used in taken):
            continue
        if any(abs(sum(unit[axis] * got[axis] for axis in range(3))) > 0.999
               for got in free):
            continue
        free.append(unit)
        if len(free) >= count:
            break
    while len(free) < count:                                   # 兜底：随便补一个不撞的方向
        free.append(_normalize((0.3, 0.5, 0.8)))
    return free[:count]


def with_hydrogens(molecule, layout):
    """把隐式氢变成**真的氢原子**（球棍模型和反应动画里要看得见它们）。

    为什么只在 3D/动画里展开：2D 结构式按教材习惯是不画 C—H 的骨架式，但
    "甲烷是一个碳带四个氢、乙醇氧化脱掉两个氢"这种话，只有把氢画出来才讲得清楚。
    """
    expanded = Molecule()
    for atom in molecule.atoms:
        expanded.add_atom(atom.element, atom.aromatic, atom.charge, 0)
    for first, second, order in molecule.bonds:
        expanded.add_bond(first, second, order,
                          aromatic=(min(first, second), max(first, second)) in molecule.aromatic_bonds)
    positions = dict(layout)
    heavy = len(molecule.atoms)
    for atom in molecule.atoms:
        for slot in range(atom.hydrogens):
            index = len(expanded.atoms)
            expanded.add_atom("H", False, 0, 0)
            expanded.add_bond(atom.index, index, 1)
            positions[index] = _hydrogen_spot(molecule, positions, atom.index, slot)
    return expanded, positions


def _hydrogen_spot(molecule, positions, index, slot):
    """给一个氢找个位置：按几何**算**出来，不靠模板挑。

    以前是从四面体/平面三角的固定方向里挑一个"没被占的"——苯环碳上那个"径向往外"的方向
    模板里根本没有，挑不到就退回一个乱方向，氢被塞进环里、压在碳球后面，看着就像"没有氢"。
    现在：先求"背离所有邻居"的轴（= -Σ单位方向），再把 k 个氢按标准键角摆在这根轴的锥面上。

    锥面半角按价层电子对来：
      * 1 个氢、且只有 1 个重原子邻居（O—H、N—H）：≈70.5°（水/醇/氨的键角就是这么来的）
      * 2 个氢（如 —CH2—）：54.7°，两个氢方位相差 180°
      * 3 个氢（如 —CH3）：70.5°，方位相差 120°
      * 其余（芳香碳、双键碳上的那个氢）：就在轴上（0°），也就是径向往外
    """
    origin = positions[index]
    taken = []
    for other, _order in molecule.neighbours(index):
        if other not in positions:
            continue
        point = positions[other]
        taken.append(_normalize(tuple(point[axis] - origin[axis] for axis in range(3))))
    axis = [0.0, 0.0, 0.0]
    for used in taken:
        for component in range(3):
            axis[component] -= used[component]
    if sum(value * value for value in axis) < 1e-6:      # 邻居正好对称（少见）：随便挑一根
        axis = [0.0, 0.0, 1.0]
    axis = _normalize(tuple(axis))
    # 和轴垂直的两个方向，用来摆同一锥面上的多个氢
    helper = (0.0, 0.0, 1.0)
    if abs(sum(axis[component] * helper[component] for component in range(3))) > 0.9:
        helper = (1.0, 0.0, 0.0)
    side = _normalize((axis[1] * helper[2] - axis[2] * helper[1],
                       axis[2] * helper[0] - axis[0] * helper[2],
                       axis[0] * helper[1] - axis[1] * helper[0]))
    other_side = _normalize((axis[1] * side[2] - axis[2] * side[1],
                             axis[2] * side[0] - axis[0] * side[2],
                             axis[0] * side[1] - axis[1] * side[0]))
    total = max(1, molecule.atoms[index].hydrogens)
    if total == 4:
        # 甲烷这种"一个重原子带四个氢"：直接取标准的正四面体方向（锥面摆不出来正四面体）
        unit = _normalize(TETRA_EVEN[slot])
        return tuple(origin[component] + unit[component] * 1.0 for component in range(3))
    if total == 2:
        half = math.radians(54.7)
        angle = math.pi * slot
    elif total == 3 or len(taken) <= 1:
        half = math.radians(70.5)
        angle = 2 * math.pi * slot / total
    else:
        half = 0.0
        angle = 0.0
    unit = tuple(axis[component] * math.cos(half) +
                 (side[component] * math.cos(angle) + other_side[component] * math.sin(angle))
                 * math.sin(half) for component in range(3))
    return tuple(origin[component] + unit[component] * 1.0 for component in range(3))


def ball_stick(smiles):
    """球棍模型的数据（界面自己投影、自己旋转）：原子、键、坐标、颜色。

    **氢也画出来**（小球），不然甲烷就成了孤零零一个球，"正四面体"根本看不出来。
    """
    molecule = parse_smiles(smiles)
    expanded, positions = with_hydrogens(molecule, layout3d(molecule))
    atoms = []
    for atom in expanded.atoms:
        x, y, z = positions[atom.index]
        atoms.append({"element": atom.element, "x": round(x, 4), "y": round(y, 4),
                      "z": round(z, 4), "hydrogens": atom.hydrogens,
                      "color": BALL_COLORS.get(atom.element, "#3a4652"),
                      "radius": 0.34 if atom.element == "H" else 0.42})
    bonds = []
    for first, second, order in expanded.bonds:
        bonds.append({"a": first, "b": second, "order": order})
    return {"formula": molecule.formula(), "atoms": atoms, "bonds": bonds,
            "groups": detect_groups(molecule),
            "note": "按标准键长键角的近似构型（VSEPR），氢原子用小球表示；不是量子化学优化结果"}
