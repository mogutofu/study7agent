# -*- coding: utf-8 -*-
"""化学引擎：**录规则，推反应**（不录反应本身）。

为什么这么设计：搜遍 GitHub 也找不到中学口径、可分发、可校验的"化学反应库"
（能拿到的都是 ML 用的专利反应集），而背一张反应表既背不全也背不准。
所以数据只录两样：**物种**（webui\\data\\chem-species.json）和**规则**
（webui\\data\\chem-rules.json）——溶解性、金属活动性、常见盐拆离子。
反应现场推：配平用原子守恒矩阵高斯消元，能不能发生按规则判。

硬红线（写在代码里，也写在 docs\\chem-engine.md）：
  * 规则表里查不到的，一律返回"资料未收录"，**绝不编造**方程式或现象
  * 推出来的反应在界面上要标"理论预测"，和教材事实分开显示

只使用 Python 3.8.10 标准库。
"""

import io
import json
import math
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
# 化学数据放 chemdata\，**不能**放 webui\data\：后者是老师的会话/上传目录，
# 打包时按目录名整个排除（EXCLUDE_DIRS 里有 "data"），放那儿等于不随包发布。
DATA_DIR = os.path.join(HERE, "chemdata")
RULES_PATH = os.path.join(DATA_DIR, "chem-rules.json")
SPECIES_PATH = os.path.join(DATA_DIR, "chem-species.json")
THERMO_PATH = os.path.join(DATA_DIR, "chem-thermo.json")
STRUCTURES_PATH = os.path.join(DATA_DIR, "chem-structures.json")
COMMON_PATH = os.path.join(DATA_DIR, "chem-common.json")
T_STANDARD = 298.15

UNKNOWN = "资料未收录"

ELEMENT = re.compile(r"([A-Z][a-z]?)(\d*)")
TOKEN = re.compile(r"([A-Z][a-z]?|\d+|[()\[\]])")


# --------------------------------------------------------------------------
# 化学式
# --------------------------------------------------------------------------

def parse_formula(text):
    """把化学式拆成 {元素: 个数}；带括号和嵌套都行：Ca(OH)2、Fe2(SO4)3、Al2(SO4)3。

    认不出来就抛 ValueError（调用方按"资料未收录"处理，不猜）。
    """
    if not text or not text.strip():
        raise ValueError("空化学式")
    source = text.strip()
    # 常见写法归一：下标字符、空格、括号后省略的 1
    for bad, good in (("\u2082", "2"), ("\u2083", "3"), ("\u00b7", ".")):
        source = source.replace(bad, good)
    if "·" in source:
        raise ValueError("含水合物（结晶水）的第一版不支持：%s" % text)
    stack = [{}]
    index = 0
    while index < len(source):
        char = source[index]
        if char == "(" or char == "[":
            stack.append({})
            index += 1
            continue
        if char == ")" or char == "]":
            if len(stack) < 2:
                raise ValueError("括号不配对：%s" % text)
            group = stack.pop()
            index += 1
            digits = ""
            while index < len(source) and source[index].isdigit():
                digits += source[index]
                index += 1
            times = int(digits) if digits else 1
            for element, count in group.items():
                stack[-1][element] = stack[-1].get(element, 0) + count * times
            continue
        found = ELEMENT.match(source, index)
        if not found or not found.group(1):
            raise ValueError("看不懂的字符 %r（%s）" % (char, text))
        element = found.group(1)
        count = int(found.group(2)) if found.group(2) else 1
        stack[-1][element] = stack[-1].get(element, 0) + count
        index = found.end()
    if len(stack) != 1:
        raise ValueError("括号不配对：%s" % text)
    if not stack[0]:
        raise ValueError("没解析出任何元素：%s" % text)
    return stack[0]


def formula_text(counts):
    """把 {元素: 个数} 还原成习惯写法（CHON 之外按习惯排序：金属、非金属、氢）。"""
    if not counts:
        return ""
    order = ["K", "Na", "Ca", "Mg", "Al", "Zn", "Fe", "Cu", "Ag", "Ba", "NH4", "H",
             "C", "Si", "N", "P", "S", "Cl", "O"]
    names = list(counts.keys())
    names.sort(key=lambda item: (order.index(item) if item in order else len(order), item))
    out = []
    for name in names:
        count = counts[name]
        out.append(name if count == 1 else "%s%d" % (name, count))
    return "".join(out)


# --------------------------------------------------------------------------
# 配平：原子守恒矩阵 + 高斯消元（分数运算，答案就是最小整数比）
# --------------------------------------------------------------------------

def _fraction(value):
    from fractions import Fraction
    return Fraction(value)


def balance(left, right):
    """配平 left -> right（都是化学式字符串）。返回 (左系数, 右系数) 或 None。

    做法：每个元素一行的齐次线性方程组 A·x = 0，把最后一个未知量定为 1 解出来，
    再乘最小公倍数变成整数，最后除以最大公约数。解不唯一（或只有全零解）就返回 None。
    """
    from fractions import Fraction
    try:
        left_maps = [parse_formula(item) for item in left]
        right_maps = [parse_formula(item) for item in right]
    except ValueError:
        return None
    if not left_maps or not right_maps:
        return None
    elements = sorted(set([key for item in left_maps + right_maps for key in item]))
    # 右边移到左边，符号取负
    columns = []
    for item in left_maps:
        columns.append([Fraction(item.get(element, 0)) for element in elements])
    for item in right_maps:
        columns.append([Fraction(-item.get(element, 0)) for element in elements])
    rows = len(elements)
    cols = len(columns)
    if cols < 2:
        return None
    # 把最后一列当已知量（取 1），解前 cols-1 个未知量
    matrix = [[columns[c][r] for c in range(cols - 1)] + [-columns[cols - 1][r]]
              for r in range(rows)]
    # 行阶梯化简
    pivot_row = 0
    where = []
    for col in range(cols - 1):
        pick = None
        for row in range(pivot_row, rows):
            if matrix[row][col] != 0:
                pick = row
                break
        if pick is None:
            continue
        matrix[pivot_row], matrix[pick] = matrix[pick], matrix[pivot_row]
        divisor = matrix[pivot_row][col]
        matrix[pivot_row] = [value / divisor for value in matrix[pivot_row]]
        for row in range(rows):
            if row != pivot_row and matrix[row][col] != 0:
                factor = matrix[row][col]
                matrix[row] = [a - factor * b for a, b in zip(matrix[row], matrix[pivot_row])]
        where.append((pivot_row, col))
        pivot_row += 1
        if pivot_row == rows:
            break
    # 无解（某行左边全 0、右边非 0）就配不出来
    for row in range(pivot_row, rows):
        if all(value == 0 for value in matrix[row][:cols - 1]) and matrix[row][cols - 1] != 0:
            return None
    if len(where) < cols - 1:
        return None                      # 自由度 > 0：配平不唯一（元素没配全）
    solution = [Fraction(0)] * cols
    solution[cols - 1] = Fraction(1)
    for row, col in where:
        solution[col] = matrix[row][cols - 1]
    if any(value <= 0 for value in solution):
        return None                      # 出现负系数：反应物/生成物写反了
    # 变成最小整数比
    from math import gcd
    denominator = 1
    for value in solution:
        denominator = denominator * value.denominator // gcd(denominator, value.denominator)
    numbers = [int(value * denominator) for value in solution]
    common = 0
    for value in numbers:
        common = gcd(common, value)
    if common > 1:
        numbers = [value // common for value in numbers]
    return numbers[:len(left)], numbers[len(left):]


def balance_text(text):
    """配平一条写成人话的方程式：'Fe + O2 -> Fe3O4'（-> 和 = 都认，全角箭头也认）。

    返回 (方程式字符串, 现象提示) 或 (None, 原因)。
    """
    if not text or not text.strip():
        return None, "空方程式"
    raw = text.strip()
    for arrow in ("\u2192", "->", "=>", "=", "\uff1d"):
        if arrow in raw:
            left_text, right_text = raw.split(arrow, 1)
            break
    else:
        return None, "没看到 -> 或 =，不知道哪边是生成物"
    left = [item.strip() for item in re.split(r"[+＋]", left_text) if item.strip()]
    right = [item.strip() for item in re.split(r"[+＋]", right_text) if item.strip()]
    numbers = balance(left, right)
    if numbers is None:
        return None, "配不平（元素没配全，或者反应物/生成物写反了）"
    parts = []
    for formula, count in list(zip(left, numbers[0])):
        parts.append(formula if count == 1 else "%d%s" % (count, formula))
    rendered = " + ".join(parts) + " = "
    parts = []
    for formula, count in list(zip(right, numbers[1])):
        parts.append(formula if count == 1 else "%d%s" % (count, formula))
    return rendered + " + ".join(parts), ""


# --------------------------------------------------------------------------
# 规则：溶解性 / 活动性 / 复分解
# --------------------------------------------------------------------------

METAL_ORDER = ["NH4", "Li", "K", "Rb", "Cs", "Be", "Mg", "Ca", "Sr", "Ba",
               "Na", "Al", "Zn", "Fe", "Mn", "Cr", "Co", "Ni", "Ti", "V", "Mo", "W",
               "Sn", "Pb", "Cu", "Ag", "Hg", "Pt", "Au", "Sb", "Bi"]
# 非金属的书写顺序：碳在最前（碳酸盐、有机物都是它领头），氧在最后
NON_METAL_ORDER = ["C", "B", "Si", "As", "N", "P", "S", "Cl", "F", "Br", "I", "Se", "O"]


def canonical_formula(text):
    """把化学式整理成**教材写法**：NaOH（不是 Wikidata 给的 HNaO）、NH4Cl（不是 ClH4N）。

    做法：先解析出各元素个数，再按"金属/铵根 -> 氢 -> 碳 -> 其它非金属 -> 氧"重排，
    铵根（1 个 N + 4 个 H）单独成组。Wikidata 的 formula 串有它自己的排序，不能直接当
    显示用的化学式（实测氢氧化钠给的是 HNaO、氯化铵给的是 ClH4N）。
    解析不了就把原文原样返回（不猜）。
    """
    if not text:
        return ""
    try:
        counts = parse_formula(text)
    except ValueError:
        return text
    # 铵根成组
    if counts.get("N") == 1 and counts.get("H") == 4 and len(counts) > 1:
        counts = dict(counts)
        counts.pop("N")
        counts.pop("H")
        counts["NH4"] = 1
    elif counts.get("N") == 2 and counts.get("H") == 8 and len(counts) > 2:
        counts = dict(counts)
        counts["N"] = 0
        counts["H"] = 0
        counts["NH4"] = 2
        counts = dict((key, value) for key, value in counts.items() if value)
    if not counts:
        return text
    names = list(counts.keys())
    metals = [item for item in names if item in METAL_ORDER]
    others = [item for item in names if item not in METAL_ORDER]
    metals.sort(key=METAL_ORDER.index)
    has_metal = bool(metals)
    hydroxide = 0
    if has_metal:
        # 有金属时，若 H 与 O 一样多就成组写成氢氧根：NaOH / Ca(OH)2 / Fe(OH)3。
        # 摊平成 NaHO、CaH2O2 不是教材写法（NaHCO3 这种 H≠O 的不会误判）。
        h_count = counts.get("H", 0)
        o_count = counts.get("O", 0)
        if h_count and h_count == o_count:
            hydroxide = h_count
            others = [item for item in others if item not in ("H", "O")]
        middle = [item for item in others if item == "H"]
        rest = [item for item in others if item != "H"]
    else:
        # 无金属：有机物按 Hill 式（碳、氢在前），水/酸这类把氢放前面
        middle = [item for item in others if item in ("C", "H")]
        middle.sort(key=lambda item: (0 if item == "C" else 1))
        rest = [item for item in others if item not in ("C", "H")]
    rest.sort(key=lambda item: (NON_METAL_ORDER.index(item) if item in NON_METAL_ORDER
                                else len(NON_METAL_ORDER), item))
    ordered = metals + middle + rest
    out = []
    for name in ordered:
        count = counts[name]
        out.append(name if count == 1 else "%s%d" % (name, count))
    if hydroxide == 1:
        out.append("OH")
    elif hydroxide > 1:
        out.append("(OH)%d" % hydroxide)
    return "".join(out)


def simplify_chinese(rules, text):
    """繁体 -> 简体（化学范围）：Wikidata 的中文标签会回退到繁体，课堂上不能出现「碳酸鈣」。

    按**最长匹配**替换：表里既有单字（鈣->钙）也有词（氧氣->氧气），
    只按单字查的话多字条目永远不会命中。
    """
    if not text:
        return text
    table = rules.get("zh_traditional") or {}
    keys = sorted([key for key in table if not key.startswith("_")], key=len, reverse=True)
    out = text
    for key in keys:
        if key in out:
            out = out.replace(key, table[key])
    return out


def load_structures(path=STRUCTURES_PATH):
    """读批量结构表（ChEBI，紧凑格式：fields + rows）；没有文件就返回空表。"""
    try:
        with io.open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {"count": 0, "fields": [], "rows": []}


def structure_row(structures, row):
    """把一行数组变成 dict（界面/工具层用起来方便）。"""
    fields = structures.get("fields") or []
    return dict(zip(fields, row))


def load_common(path=COMMON_PATH):
    """读"常见化工品"中文名小表（chem-common.json）。

    ChEBI 大表只有英文名，中文名（甘油、偶氮二异丁腈……）一个都查不到，
    所以另立一张手工维护的小表，查结构时先查它。读不到就当空表（不影响大表查询）。
    """
    try:
        with io.open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {"rows": []}
    if not isinstance(data, dict) or not isinstance(data.get("rows"), list):
        return {"rows": []}
    return data


def find_common(common, query, limit=20):
    """在小表里找：名字/别名精确 > 名字前缀 > 名字或别名子串；分子式也认（精确）。

    rows 是 dict（和大表的数组格式不同，这里转成同一套字段名交出去）。
    """
    text = (query or "").strip()
    if not text:
        return []
    lowered = text.lower()
    scored = []
    for row in common.get("rows") or []:
        if not isinstance(row, dict) or not row.get("smiles"):
            continue
        names = [row.get("name") or ""] + list(row.get("aliases") or [])
        lowered_names = [name.lower() for name in names if name]
        rank = None
        if any(name == lowered for name in lowered_names):
            rank = 0
        elif any(name.startswith(lowered) for name in lowered_names):
            rank = 1
        elif any(lowered in name for name in lowered_names):
            rank = 2
        elif (row.get("formula") or "").lower() == lowered:
            rank = 3
        if rank is None:
            continue
        scored.append((rank, row))
    scored.sort(key=lambda item: (item[0], item[1].get("name") or ""))
    return [{"chebi": "", "name": row.get("name") or "", "formula": row.get("formula") or "",
             "smiles": row.get("smiles") or "", "aliases": list(row.get("aliases") or []),
             "local": True} for _rank, row in scored[:limit]]


def find_structures(structures, query, limit=20, common=None):
    """按名称（不区分大小写，子串）或分子式（精确）找结构。

    先查**中文名小表**（chem-common.json）：甘油、偶氮二异丁腈这类只在这里有，
    命中就直接返回，不再去扫 20 万行大表（中文名在大表里必然一个都匹配不上，白扫）。
    大表（ChEBI，英文名）里：先给"分子式完全匹配"的结果，再给名称匹配——拿分子式找时要的是
    那一个，不该被一堆名字里带数字的挤掉。
    """
    text = (query or "").strip()
    if not text:
        return []
    local = find_common(common if common is not None else load_common(), text, limit)
    if local:
        return local
    fields = structures.get("fields") or []
    if not fields:
        return []
    where = dict((name, index) for index, name in enumerate(fields))
    lowered = text.lower()
    exact = []
    named = []
    for row in structures.get("rows") or []:
        formula = row[where.get("formula", 2)] if len(row) > 2 else ""
        name = row[where.get("name", 1)] if len(row) > 1 else ""
        if formula and formula.lower() == lowered:
            exact.append(row)
        elif name and lowered in name.lower():
            named.append(row)
        if len(exact) + len(named) >= limit * 4:
            break
    out = exact + named
    return [structure_row(structures, row) for row in out[:limit]]


#: 课本里的俗名 -> 物种表里的正名（输入框里写"醋酸""酒精"是常事）。
#: 只收高中课本上真出现过的有机俗名，别贪多——这张表只是"查不到时再试一次"。
NAME_ALIASES = {
    "醋酸": "乙酸", "冰醋酸": "乙酸", "酒精": "乙醇", "木精": "甲醇",
    "甘油": "丙三醇", "电石气": "乙炔", "蚁酸": "甲酸", "草酸": "乙二酸",
    "石炭酸": "苯酚", "蚁醛": "甲醛", "福尔马林": "甲醛", "氯仿": "三氯甲烷",
}


def smiles_for(text, species=None, structures=None):
    """把"老师写的名字"换成 SMILES：输入框里写「乙醇」是最自然的写法。

    老师真这么干过——往 SMILES 框里填「乙醇」，旧解析器把两个汉字当成两个元素，
    画出两个灰球，分子式还显示成"乙醇"。现在解析器会拒绝，这里负责把名字查出来。
    顺序：物种表（中文名/分子式，带 SMILES）-> 名字换成俗名的正名再查 ->
    结构表（ChEBI，英文名/分子式）。返回 (smiles, 名字)；查不到给 (None, "")。
    """
    want = (text or "").strip()
    if not want:
        return None, ""
    species = species if species is not None else load_species()
    found = []
    for query in [want, NAME_ALIASES.get(want, want)]:
        for row in species_search(species, query, limit=10):
            smiles = (row.get("smiles") or "").strip()
            if smiles:
                exact = (row.get("name") or "").strip() == query
                found.append((0 if exact else 1, smiles, row.get("name") or want))
        if found:
            break
    if not found:                     # 大表几 MB，只有小表没命中才去读
        for row in find_structures(structures if structures is not None else load_structures(),
                                   want, limit=5):
            smiles = (row.get("smiles") or "").strip()
            if smiles:
                exact = (row.get("name") or "").strip().lower() == want.lower()
                found.append((0 if exact else 2, smiles, row.get("name") or want))
    if not found:
        return None, ""
    found.sort(key=lambda item: item[0])
    return found[0][1], found[0][2]


def formula_from_smiles(smiles):
    """从 SMILES 数出分子式（只处理第一版够用的子集）。

    认得：元素、括号分支、环闭合数字、双/三键、芳香小写（c/n/o/s）、[Fe+3] 这种方括号原子。
    认不出（比如 复杂的立体标记）就返回 ""——宁可没有，也不猜错原子数。
    """
    if not smiles:
        return ""
    text = smiles.strip()
    counts = {}
    index = 0
    while index < len(text):
        char = text[index]
        if char in "()[]-+=#$@/\\%0123456789.":
            # 方括号里的内容单独处理：[Fe+3] / [OH-] / [O-2]
            if char == "[":
                end = text.find("]", index)
                if end < 0:
                    return ""
                inner = text[index + 1:end]
                found = ELEMENT.match(inner, 0)
                if not found or not found.group(1):
                    return ""
                element = found.group(1)
                counts[element] = counts.get(element, 0) + 1
                index = end + 1
                continue
            index += 1
            continue
        if char.islower():
            # 芳香原子：c/n/o/s -> C/N/O/S
            element = char.upper()
            counts[element] = counts.get(element, 0) + 1
            index += 1
            continue
        found = ELEMENT.match(text, index)
        if not found or not found.group(1):
            return ""
        element = found.group(1)
        counts[element] = counts.get(element, 0) + 1
        index = found.end()
    if not counts:
        return ""
    # SMILES 里没写出来的氢补不了（要隐式氢规则），所以只给"重原子骨架"，明确标注
    return "".join("%s%s" % (name, counts[name] if counts[name] > 1 else "")
                   for name in sorted(counts))


def load_species(path=SPECIES_PATH):
    """读物种表；没有文件就返回空表（界面按"资料未收录"处理，不是崩）。"""
    try:
        with io.open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {"count": 0, "species": []}


def load_rules(path=RULES_PATH):
    with io.open(path, encoding="utf-8") as handle:
        return json.load(handle)


def load_thermo(path=THERMO_PATH):
    """读热化学表（298.15 K 的 ΔfH° 与 S°）；没有文件就返回空表。"""
    try:
        with io.open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {"count": 0, "rows": []}


def _is_element_formula(formula):
    """单一元素的化学式（O2、Fe、C…）：标准态单质的 ΔfH° 按定义是 0。

    WebBook 里这类物质的 ΔfH° 行**根本不列**（因为它就是 0），所以不能当成"缺数据"。
    """
    try:
        return len(parse_formula(formula)) == 1
    except ValueError:
        return False


def thermo_of(thermo, rules, formula, phase=""):
    """查一个物种的 (ΔfH°, S°, 相态)。

    相态选择：规则表里点名是气体的优先气相，其余优先凝聚相；缺哪个就用另一个。
    单质按定义 ΔfH°=0（S° 仍查表）。表里没有这个物种就返回 None。
    """
    rows = [row for row in (thermo.get("rows") or [])
            if (row.get("formula") or "") == formula]
    if phase:
        rows = [row for row in rows if row.get("phase") == phase] or rows
    if not rows:
        if _is_element_formula(formula):
            return {"dHf": 0.0, "S": None, "phase": "", "by_definition": True}
        return None
    gases = ((rules.get("gas_species") or {}).get("list")) or []
    liquids = ((rules.get("liquid_species") or {}).get("list")) or []
    if formula in gases:
        want = ["gas", "liquid", "solid"]
    elif formula in liquids:
        want = ["liquid", "solid", "gas"]
    else:
        want = ["solid", "liquid", "gas"]
    picked = None
    for phase_name in want:
        for row in rows:
            if row.get("phase") == phase_name:
                picked = row
                break
        if picked is not None:
            break
    if picked is None:
        picked = rows[0]
    d_hf = picked.get("dHf")
    by_definition = False
    if d_hf is None and _is_element_formula(formula):
        # WebBook 不给单质的 ΔfH°（按定义是 0），这里补上并标出来源
        d_hf = 0.0
        by_definition = True
    return {"dHf": d_hf, "S": picked.get("S"), "phase": picked.get("phase", ""),
            "by_definition": by_definition}


def reaction_energy(thermo, rules, reactants, products, temperature=T_STANDARD):
    """算一条反应的 ΔrH°、ΔrS°、ΔrG° 与能否自发（默认 298.15 K）。

    reactants / products 都是 [(化学式, 系数)]（配平后的）。
    **这只是标准态热力学判据**：ΔrG°<0 说明在这个状态下有自发趋势，
    实际能不能看到反应还要看动力学（活化能）、浓度、温度——返回值里写清楚了。
    任何一个物种查不到热力学数据，就返回"资料未收录"，不猜数值。
    """
    missing = []
    d_h = 0.0
    d_s = 0.0
    used = []
    for formula, count in list(reactants) + list(products):
        sign = -1.0 if (formula, count) in reactants else 1.0
        info = thermo_of(thermo, rules, formula)
        if info is None or info.get("dHf") is None or info.get("S") is None:
            missing.append(formula)
            continue
        d_h += sign * count * info["dHf"]
        d_s += sign * count * info["S"]
        used.append("%s(%s)" % (formula, info["phase"] or "基准态"))
    if missing:
        return {"ok": False, "reason": UNKNOWN,
                "detail": "这些物质没有热力学数据：%s" % "、".join(sorted(set(missing)))}
    d_g = d_h - temperature * d_s / 1000.0          # kJ/mol
    return {"ok": True, "dH": round(d_h, 2), "dS": round(d_s, 2), "dG": round(d_g, 2),
            "spontaneous": d_g < 0, "phases": used, "temperature": temperature,
            "note": "标准态判据（各物质按其相态）；实际能否观察到反应还要看"
                    "动力学与实际浓度、温度。"}


def solubility(rules, cation, anion):
    """这个离子组合的溶解性：'溶' / '不溶' / '微溶'；表里没有就返回 UNKNOWN。"""
    table = (rules.get("solubility") or {}).get(anion)
    if not isinstance(table, dict):
        return UNKNOWN
    if cation in table:
        return table[cation]
    return table.get("_default", UNKNOWN)


def is_precipitate(rules, cation, anion):
    """会不会出沉淀：只有明确'不溶'才算（'微溶'不下结论——教材里也是分情况的）。"""
    return solubility(rules, cation, anion) == "不溶"


def activity_index(rules, metal):
    order = ((rules.get("activity") or {}).get("order")) or []
    return order.index(metal) if metal in order else -1


def can_replace_hydrogen(rules, metal):
    """金属能不能置换出酸里的氢：排在 H 前面才行。"""
    index = activity_index(rules, metal)
    return 0 <= index < activity_index(rules, "H")


def split_salt(rules, formula):
    """化学式 -> (阳离子, 阴离子)。

    先查表（表里的写法是教材口径，最权威）；表里没有就**按电荷守恒反推**：
    拿所有 (阳离子, 阴离子) 组合算一遍化学式，和它一模一样的那一对就是答案
    （FeSO4 -> Fe2+SO4；Fe2(SO4)3 -> Fe3+SO4，不用猜铁是几价）。
    反推不出来就返回 None——宁可说"资料未收录"，不硬猜。
    """
    text = (formula or "").replace(" ", "")
    salts = rules.get("salts") or {}
    pair = salts.get(text)
    if pair and len(pair) == 2:
        return pair[0], pair[1]
    return derive_salt(rules, text)


#: 反推时只认金属与铵根：氢放在里面的话，水会被当成"H + OH 的盐"，
#: 于是"水 + 碳酸钠"会被推成"生成 CO2 和 NaOH"（真会发生的事，所以把 H 排除）。
DERIVE_CATIONS = ("NH4", "Na", "K", "Ca", "Ba", "Mg", "Al", "Zn", "Fe2", "Fe3",
                  "Cu", "Ag", "Pb")


def derive_salt(rules, formula):
    """表里没有的盐：用"电荷守恒"反推出离子对（不查表也能算）。"""
    if not formula:
        return None
    charges = rules.get("ion_charge") or {}
    try:
        wanted = canonical_formula(formula)
    except ValueError:
        return None
    hits = []
    for cation in DERIVE_CATIONS:
        if not isinstance(charges.get(cation), int):
            continue
        for anion, charge in charges.items():
            if not isinstance(charge, int) or charge >= 0:
                continue
            built = combine_ions(rules, cation, anion)
            if not built:
                continue
            try:
                if canonical_formula(built) == wanted:
                    hits.append((cation, anion))
            except ValueError:
                continue
    if len(hits) == 1:
        return hits[0]
    return None


def ion_name(rules, symbol):
    return (rules.get("ion_names") or {}).get(symbol, symbol)


def combine_ions(rules, cation, anion):
    """按电荷十字交叉写出化合物化学式：Ca + OH -> Ca(OH)2、Al + SO4 -> Al2(SO4)3。

    多原子离子（两个字符以上）下标大于 1 时加括号，这是教材写法。
    有机酸根（CH3COO）教材里写在前面：CH3COONa 而不是 NaCH3COO——数据里点名，代码不写死。
    """
    charges = rules.get("ion_charge") or {}
    if cation not in charges or anion not in charges:
        return None
    positive = charges[cation]
    negative = charges[anion]
    if positive <= 0 or negative >= 0:
        return None
    from math import gcd
    left = abs(negative)
    right = positive
    common = gcd(left, right)
    left, right = left // common, right // common
    # 注意：ion 的 key 可能带电荷后缀（Fe2/Fe3），写进化学式要换成真正的元素符号
    symbol = (rules.get("ion_symbol") or {}).get(cation, cation)
    head = symbol if left == 1 else _with_count(symbol, left)
    tail = anion if right == 1 else _with_count(anion, right)
    if anion in ((rules.get("anion_first") or {}).get("list") or []):
        return tail + head
    return head + tail


def _with_count(ion, count):
    """离子在化学式里带下标时的写法：只有**真的多元素**才加括号。

    OH/SO4/NH4 要写成 (OH)2、(NH4)2SO4；Cl 虽然也是两个字符，但它是一个元素，
    只能写 ZnCl2——按字符数判断会写出 Zn(Cl)2 这种错式子。
    """
    polyatomic = len(ion) > 1
    try:
        polyatomic = len(parse_formula(ion)) > 1
    except ValueError:
        pass
    if polyatomic:
        return "(%s)%d" % (ion, count)
    return "%s%d" % (ion, count)


def _water_and_gas(rules, cation, anion):
    """这一对离子混合后会不会跑出气体：H+CO3 -> CO2、H+HCO3 -> CO2、NH4+OH -> NH3（都带水）。

    规则从数据来（rules.acid_gas），不写死在代码里——加一个酸式盐只要加一行表。
    """
    table = ((rules.get("acid_gas") or {}).get(cation + "|" + anion)) or {}
    products = table.get("products")
    if products:
        return list(products), table.get("phenomenon") or ""
    return None, ""


def _metal_salt(rules, metal, anion):
    """金属单质 + 阴离子 -> 盐的化学式（铁按亚铁算，见规则表 metal_ion）。"""
    ion = (rules.get("metal_ion") or {}).get(metal)
    if not ion:
        return None
    return combine_ions(rules, ion, anion)


def predict_replacement(rules, metal, other):
    """置换反应：金属 + 酸，或金属 + 盐溶液。返回 dict（形状同复分解那个）。

    判据是金属活动性顺序：
      * 金属 + 酸：金属排在 H 前面才反应，生成盐 + H2
      * 金属 + 盐溶液：金属比盐里的金属活泼才反应，生成新盐 + 被置换出来的金属
    规则表里没有的（不认识的金属、不认识的盐）一律"资料未收录"。
    """
    if activity_index(rules, metal) < 0:
        return {"ok": False, "reason": UNKNOWN, "detail": "活动性顺序表里没有 %s" % metal}
    target = split_salt(rules, other)
    if not target:
        return {"ok": False, "reason": UNKNOWN,
                "detail": "规则表里没有 %s 的拆离子方式" % other}
    cation, anion = target
    if cation == metal:
        return {"ok": False, "reason": "同一种金属，谈不上置换", "detail": ""}
    if cation == "H":
        if not can_replace_hydrogen(rules, metal):
            return {"ok": False,
                    "reason": "%s 排在氢后面，不能置换出酸里的氢" % metal, "detail": ""}
        salt = _metal_salt(rules, metal, anion)
        if not salt:
            return {"ok": False, "reason": UNKNOWN,
                    "detail": "写不出 %s 与 %s 组成的盐" % (metal, ion_name(rules, anion))}
        numbers = balance([metal, other], [salt, "H2"])
        if numbers is None:
            return {"ok": False, "reason": "产物配不平，先当资料未收录", "detail": salt}
        def render(items, counts):
            return " + ".join(formula if count == 1 else "%d%s" % (count, formula)
                              for formula, count in zip(items, counts))
        return {"ok": True, "predicted": True,
                "equation": "%s = %s" % (render([metal, other], numbers[0]),
                                         render([salt, "H2"], numbers[1])),
                "phenomenon": "金属表面有气泡冒出（H2）",
                "why": "%s 比氢活泼，把酸里的氢置换了出来" % metal,
                "visual": visual_hints(rules, [salt, "H2"], [other])}
    # 金属 + 盐溶液
    if activity_index(rules, metal) >= activity_index(rules, cation):
        return {"ok": False,
                "reason": "%s 不如 %s 活泼，置换不出来"
                          % (metal, ion_name(rules, cation)), "detail": ""}
    salt = _metal_salt(rules, metal, anion)
    if not salt:
        return {"ok": False, "reason": UNKNOWN,
                "detail": "写不出 %s 与 %s 组成的盐" % (metal, ion_name(rules, anion))}
    numbers = balance([metal, other], [salt, cation])
    if numbers is None:
        return {"ok": False, "reason": "产物配不平，先当资料未收录", "detail": salt}

    def render_pair(items, counts):
        return " + ".join(formula if count == 1 else "%d%s" % (count, formula)
                          for formula, count in zip(items, counts))

    phenomenon = ["%s 表面析出 %s" % (metal, cation)]
    if cation == "Cu":
        phenomenon.append("蓝色溶液逐渐变浅")
    return {"ok": True, "predicted": True,
            "equation": "%s = %s" % (render_pair([metal, other], numbers[0]),
                                     render_pair([salt, cation], numbers[1])),
            "phenomenon": "；".join(phenomenon),
            "why": "%s 比 %s 活泼，把它从盐溶液里置换了出来"
                   % (metal, ion_name(rules, cation)),
            "visual": visual_hints(rules, [salt, cation], [other])}


COEFFICIENT = re.compile(r"^\s*(\d+)\s*(.+?)\s*$")


def parse_equation(text):
    """把 'CH4 + 2O2 = CO2 + 2H2O' 拆成 ([(式, 系数)], [(式, 系数)])。

    认 -> 和 =；系数写在式子前面（1 可以省）。
    """
    if not text:
        raise ValueError("空方程式")
    raw = text.strip()
    for arrow in ("\u2192", "->", "=>", "=", "\uff1d"):
        if arrow in raw:
            left_text, right_text = raw.split(arrow, 1)
            break
    else:
        raise ValueError("没看到 -> 或 =")

    def side(part):
        items = []
        for term in re.split(r"[+\uff0b]", part):
            term = term.strip()
            if not term:
                continue
            found = COEFFICIENT.match(term)
            if found:
                items.append((found.group(2), int(found.group(1))))
            else:
                items.append((term, 1))
        return items

    left, right = side(left_text), side(right_text)
    if not left or not right:
        raise ValueError("方程式两边都要有物质")
    return left, right


def species_search(table, query, limit=10):
    """在物种表里按中文名/分子式/规范式找（分子式精确优先，名字按子串）。"""
    text = (query or "").strip().lower()
    if not text:
        return []
    exact = []
    named = []
    for row in table.get("species") or []:
        formula = (row.get("formula") or "").lower()
        canonical = (row.get("formula_canonical") or "").lower()
        name = (row.get("name") or "")
        if text in (formula, canonical):
            exact.append(row)
        elif text in name.lower():
            named.append(row)
    return (exact + named)[:limit]


def lab_mix(rules, contents, reagent):
    """实验台：往一个容器里加一种试剂，按引擎推结果。

    contents 是容器里已有的物质（化学式列表）。逐个和已有的配：复分解、置换（两个方向），
    一条都不成时**区分**两种情形——"确实不反应"是结论，"资料未收录"是不敢下结论。
    """
    reagent = (reagent or "").strip()
    if not reagent:
        return {"ok": False, "reason": "没说要加什么"}
    contents = [item for item in (contents or []) if item]
    if reagent in contents:
        return {"ok": False, "reason": "容器里已经有这种物质了"}
    unknown = False
    definite_negative = ""
    for existing in contents:
        attempts = [predict_double_displacement(rules, existing, reagent)]
        # 置换只在"确实是一种金属"时才试：拿一个不是金属的东西去试，只会白白得到"资料未收录"，
        # 把"明确不反应"的结论盖掉（氯化钠+硝酸钾就是这么被带偏的）。
        if activity_index(rules, existing) >= 0:
            attempts.append(predict_replacement(rules, existing, reagent))
        if activity_index(rules, reagent) >= 0:
            attempts.append(predict_replacement(rules, reagent, existing))
        for result in attempts:
            if result.get("ok"):
                result["with"] = existing
                return result
            reason = result.get("reason") or ""
            if reason == UNKNOWN:
                unknown = True
            elif reason:
                definite_negative = reason
    redox = predict_redox(rules, contents + [reagent], 298)
    if redox is not None and redox.get("ok"):
        redox["with"] = reagent
        return redox
    if redox is not None and redox.get("reason"):
        return redox          # 有这一组、只是条件不够：如实说"要加热/要浓硫酸"
    if definite_negative:
        return {"ok": False, "reason": "混合后没有明显变化（按随包规则表判，不是「查不到」）",
                "detail": definite_negative}
    if unknown:
        return {"ok": False, "reason": UNKNOWN,
                "detail": "容器里有规则表没收录的物质，不敢下结论"}
    return {"ok": False, "reason": "混合后没有明显变化（按随包规则表判，不是「查不到」）"}


# 氧化还原/燃烧表：条件 -> 需要的温度（和 conditions 表保持一致）
REDOX_TEMPERATURE = {"ignite": 873, "heat": 673, "high": 1273, "acid_heat": 443,
                     "catalyst": 298, "cu_heat": 573, "none": 298}


def redox_entries(rules):
    return ((rules.get("redox") or {}).get("list")) or []


def ignite_entries(rules):
    """「点了火就发生」的全部反应：氧化还原那一族 + 有机/特例表里条件就是点燃的。

    有机燃烧（甲烷、乙醇、苯……）写在 organic_reactions 里，条件同样是点燃。以前「点燃」
    只看 redox 表，于是 agent 点不着甲烷，而界面（sim 走 organic_reactions）却着得起来——
    同一个操作两条路结果不一样。这里合成一张表，两边都从它查。
    """
    rows = list(redox_entries(rules))
    seen = set(row.get("key") for row in rows)
    for table in ("organic_reactions", "special_reactions"):
        for row in _list_of(rules, table):
            if (row.get("condition") or "none") != "ignite" or not row.get("reactants"):
                continue
            if row.get("key") in seen:
                continue
            seen.add(row.get("key"))
            rows.append(row)
    return rows


def _safe_visual(rules, products, reactants):
    """视觉提示是锦上添花：表里没收录的物质不该把整条反应带崩（踩过）。"""
    try:
        return visual_hints(rules, products, reactants)
    except Exception:
        return {}


def contents_keys(contents):
    """把 contents 规整成「瓶子 key / 式子」的字符串列表。

    界面传的是 [{"key": "NaCl", "mol": 0.01}]，agent / 脚本传的是 ["NaCl"]，两条路都要认。
    踩过的大坑：以前这些地方直接 str(item)，dict 出来是 "{'key': 'NaCl', ...}"，
    永远比不中表里的 "NaCl" —— 于是焰色反应、氧化还原表、受热分解在界面上全部静默失效，
    而 agent 那条路（传字符串）却是好的，所以一直没被发现。
    """
    keys = []
    for item in contents or []:
        if isinstance(item, dict):
            key = str(item.get("key") or "").strip()
        else:
            key = str(item or "").strip()
        if key:
            keys.append(key)
    return keys


def formula_counts_of(rules, key):
    """contents 里的 key（式子，或「浓硫酸」这种别名）拆成 {元素: 个数}；认不出来返回 {}。

    不猜：解析不了（中文名、带结晶水的写法）就当没有，绝不按子串去蒙一个元素出来。
    """
    text = (rules.get("aliases") or {}).get(key) or key
    try:
        return parse_formula(text)
    except ValueError:
        return {}


def flame_color_of(rules, contents):
    """焰色反应：火焰什么颜色**只由金属元素决定**（钠黄、钾浅紫……）。

    所以按元素查表，而不是一个个列盐名 —— 任何盐、碱、氧化物、金属单质都自动覆盖，
    新加试剂不用回来补数据。表里的顺序就是优先级（同时含钾和钠时取钾）。
    认不出式子、或物质里没有表上的金属（比如铁、铝）就返回空串：铁没有焰色，不能编。
    """
    table = (rules.get("flame_color") or {}).get("element") or {}
    if not table:
        return ""
    for key in contents_keys(contents):
        counts = formula_counts_of(rules, key)
        if not counts:
            continue
        for element in table:                     # 数据里的顺序 = 优先级
            if counts.get(element):
                return table[element]
    return ""


def burn_color_of(rules, contents):
    """可燃物燃烧时火焰的颜色（甲烷淡蓝、苯明亮带浓烟、镁耀眼白光……）。

    这是**化学变化的一部分**，跟"焰色反应是物理变化"要分开说，所以另立一张按物质查的表。
    """
    table = (rules.get("flame_color") or {}).get("burn") or {}
    for key in contents_keys(contents):
        if key in table:
            return table[key]
    return ""


def predict_redox(rules, contents, temperature=298):
    """按"反应物集合"查氧化还原/燃烧表（与顺序无关），温度不够就说缺什么条件。

    返回 None 表示表里没有这一组；返回 ok=False 表示有这一组但条件不够。
    """
    items = contents_keys(contents)
    if not items:
        return None
    for row in ignite_entries(rules):
        reactants = row.get("reactants") or []
        if sorted(reactants) != sorted(items):
            continue
        condition = row.get("condition") or "none"
        need = REDOX_TEMPERATURE.get(condition, 298)
        if temperature < need:
            table = (rules.get("conditions") or {})
            if isinstance(table.get("list"), dict):
                table = table["list"]
            label = ((table.get(condition) or {}).get("label")) or {
                "ignite": "点燃", "heat": "加热", "high": "高温",
                "acid_heat": "浓硫酸、加热", "catalyst": "催化剂",
                "none": "常温"}.get(condition, condition)
            return {"ok": False, "reason": "这一组能反应，但要「%s」（约 %d K）" % (label, need),
                    "detail": "现在只有 %d K" % int(temperature)}
        products = row.get("products") or []
        fixed = row.get("balanced")
        if fixed:
            colors = {}
            for formula in products:
                color = (rules.get("gas_color") or {}).get(formula)
                if color:
                    colors[formula] = color
            return {"ok": True, "predicted": True, "equation": fixed,
                    "phenomenon": row.get("phenomenon") or "",
                    "intensity": row.get("intensity") or "normal",
                    "gas_colors": colors,
                    "why": "氧化还原反应（" + condition + "）",
                    "visual": _safe_visual(rules, products, reactants)}
        numbers = balance(reactants, products)
        if numbers is None:
            return {"ok": False, "reason": "表里有这条反应但配不平，先当资料未收录",
                    "detail": row.get("key")}
        left = " + ".join("%s%s" % ("" if n == 1 else n, f) for f, n in zip(reactants, numbers[0]))
        right = " + ".join("%s%s" % ("" if n == 1 else n, f) for f, n in zip(products, numbers[1]))
        colors = {}
        for formula in products:
            color = (rules.get("gas_color") or {}).get(formula)
            if color:
                colors[formula] = color
        return {"ok": True, "predicted": True, "equation": left + " = " + right,
                "phenomenon": row.get("phenomenon") or "",
                "intensity": row.get("intensity") or "normal",
                "gas_colors": colors,
                "why": "氧化还原反应（" + condition + "）",
                "visual": _safe_visual(rules, products, reactants)}
    return None


def lab_ignite(rules, contents):
    """实验台/燃烧台：点燃容器里的东西（可燃物 + 氧气/氯气等）。"""
    result = predict_redox(rules, contents, 873)
    flame = flame_color_of(rules, contents)
    burn = burn_color_of(rules, contents)
    if result is None:
        if flame:
            return {"ok": True, "predicted": True,
                    "equation": "（焰色反应是物理变化，没有化学方程式）",
                    "phenomenon": "焰色反应：火焰呈%s —— 这是金属元素的物理性质，不是化学反应" % flame,
                    "flame_color": flame, "intensity": "normal",
                    "gas_colors": {}, "why": "焰色反应"}
        return {"ok": False, "reason": "表里没有这一组可燃物（换一组试试，或先用混合看看）"}
    if flame:
        result["flame_color"] = flame
        result["phenomenon"] = (result.get("phenomenon") or "") + "；焰色：火焰呈%s" % flame
    elif burn:
        result["flame_color"] = burn
        result["phenomenon"] = (result.get("phenomenon") or "") + "；火焰呈%s" % burn
    return result


def lab_heat_redox(rules, contents):
    """加热时先看氧化还原表：443 K（浓硫酸+碳/铜）-> 673 -> 873（点燃级）-> 1273。

    实验台只有一个「加热」按钮，是按温度阶梯走的，所以这里逐档试：
    点一次到不了点燃温度就继续点，够热了燃烧/爆炸就会发生。
    """
    last = None
    for temperature in (443, 673, 873, 1273):
        result = predict_redox(rules, contents, temperature)
        if result is None:
            return None
        if result.get("ok"):
            return result
        last = result
    return last


def lab_heat(rules, contents):
    """实验台：加热容器，查受热分解表（有催化剂的按催化剂那条走）。"""
    table = ((rules.get("decompose") or {}).get("rules")) or []
    contents = [item for item in (contents or []) if item]
    keys = contents_keys(contents)               # 表里比的是式子，不是 {"key": ...} 这种字典
    redox = lab_heat_redox(rules, contents)      # 浓硫酸 + 碳/铜 这类两个反应物的
    if redox:
        # 金属按焰色反应上色，别的可燃物按燃烧的火焰颜色上色
        redox["flame_color"] = (flame_color_of(rules, contents)
                                or burn_color_of(rules, contents)) or None
        if not redox["flame_color"]:
            redox.pop("flame_color")
        return redox
    # （焰色兜底放到最后，见本函数末尾）
    for item in table:
        reactant = item.get("reactant")
        if reactant not in keys:
            continue
        condition = item.get("condition")
        if condition == "none":
            continue
        note = ""
        if condition == "catalyst":
            catalyst = item.get("catalyst")
            if catalyst and catalyst not in keys:
                continue
            note = "（%s 作催化剂）" % catalyst
        products = item.get("products") or []
        numbers = balance([reactant], products)
        if numbers is None:
            return {"ok": False, "reason": "表里有这条分解反应但配不平，先当资料未收录",
                    "detail": reactant}
        text = " + ".join("%s%s" % ("" if n == 1 else n, f) for f, n in zip([reactant], numbers[0]))
        text += " = " + " + ".join("%s%s" % ("" if n == 1 else n, f)
                                   for f, n in zip(products, numbers[1]))
        return {"ok": True, "predicted": True, "equation": text,
                "phenomenon": (item.get("phenomenon") or "") + note,
                "why": "受热分解" if condition != "catalyst" else "在催化剂作用下分解",
                "visual": visual_hints(rules, products, [reactant])}
    flame_only = flame_color_of(rules, contents)
    if flame_only and len(keys) == 1:
        # 表里没有分解反应、又是单一种金属盐：那就是在做焰色反应
        return {"ok": True, "predicted": True,
                "equation": "（焰色反应是物理变化，没有化学方程式）",
                "phenomenon": "焰色反应：火焰呈%s —— 金属元素的物理性质" % flame_only,
                "flame_color": flame_only, "intensity": "normal",
                "gas_colors": {}, "why": "焰色反应"}
    return {"ok": False, "reason": "加热后没有明显变化（随包表里没有这条分解反应）"}


#: 需要"垫着加热"的器皿（锥形瓶/烧杯）；试管可以直接夹在铁架台上
HEATED_NEEDS_STAND = ("flask", "beaker")
#: 收集气体的装置
COLLECTORS = ("bottle", "trough")
#: 这套几何真的判得出来的规则；不在里面的（比如"导管伸入集气瓶不宜过长"，界面没有管长
#: 这个概念）只能当检查要点提醒，不列进"没问题"里——判过且做到了的规则不该再冒出来。
JUDGEABLE = ("heated_tube_downward", "flask_needs_stand_or_lamp", "collect_connected")


def lab_check(rules, apparatus, links):
    """实验台的「检查装置」：**只判几何**——竖着还是斜着、有没有铁架台、有没有接导管。

    化学判在 lab_mix / lab_heat 里，这里不重复一遍。规则表里按这套几何判不了的条目
    （比如"导管伸入集气瓶不宜过长"，界面没有管长这个概念）放进 tips 当检查要点，
    不硬报"有问题"——宁可少判一条，也不能给老师一个假结论。

    apparatus: [{"kind", "contents", "heated", "tilt"}]，links: [[i, j]] 是 apparatus 的下标对。
    """
    items = [item for item in (apparatus or []) if isinstance(item, dict)]
    pairs = []
    for link in links or []:
        if not isinstance(link, (list, tuple)) or len(link) != 2:
            continue
        try:
            left, right = int(link[0]), int(link[1])
        except (TypeError, ValueError):
            continue
        if 0 <= left < len(items) and 0 <= right < len(items) and left != right:
            pairs.append((left, right))

    kinds = [str(item.get("kind") or "") for item in items]
    has_stand = "stand" in kinds
    keys = set()
    for index, item in enumerate(items):
        heated = bool(item.get("heated"))
        contents = [value for value in (item.get("contents") or []) if value]
        if kinds[index] == "tube" and heated and contents and not item.get("tilt"):
            keys.add("heated_tube_downward")
        if kinds[index] in HEATED_NEEDS_STAND and heated and not has_stand:
            keys.add("flask_needs_stand_or_lamp")
    # 台上摆了集气瓶/水槽（也就是打算收集气体），却没拿导管接上去
    if any(kind in COLLECTORS for kind in kinds) and \
            any(item.get("contents") for item in items):
        linked = set()
        for left, right in pairs:
            linked.add(left)
            linked.add(right)
        if not any(kinds[index] in COLLECTORS for index in linked):
            keys.add("collect_connected")

    problems, tips = [], []
    for rule in ((rules.get("setup_rules") or {}).get("rules")) or []:
        text = rule.get("text") or ""
        key = rule.get("key") or ""
        if key in keys:
            problems.append({"key": key, "text": text})
        elif text and key not in JUDGEABLE:
            tips.append(text)
    return {"ok": True, "problems": problems, "tips": tips,
            "apparatus": len(items), "links": len(pairs)}


#: 反应速率档 -> 基本时间常数（秒）。**只决定界面上动画的快慢**，不是真的动力学常数。
RATE_BASE = {"instant": 0.6, "fast": 1.4, "normal": 2.6, "slow": 6.0}
#: 条件不够时"差多少"按 Arrhenius 折算用的活化能量级（kJ/mol）；只用于教学模拟。
DEFAULT_EA = 60.0
R_GAS = 8.314e-3
#: 默认溶液体积（L）：一份试剂的量级，用来把 mol 折成浓度算平衡。
DEFAULT_VOLUME = 0.05
#: "溶液里自带的水"按多少 mol 算（约 1 mL 水，相对于一份试剂的量级是过量的）
SOLVENT_WATER = 0.05


def _list_of(rules, key, field="list"):
    return ((rules.get(key) or {}).get(field)) or []


def reagent_of(rules, key):
    """试剂架上的这一瓶（没有就返回空字典）。"""
    for entry in _list_of(rules, "reagents"):
        if entry.get("key") == key:
            return entry
    return {}


def species_formula(rules, key):
    """式子 -> 查热化学用的分子式：简式（C2H5OH）先看试剂表里写的 formula，再按规范式。"""
    entry = reagent_of(rules, key)
    if entry.get("formula"):
        return entry["formula"]
    try:
        return canonical_formula(key)
    except ValueError:
        return ""


def _is_pure_phase(rules, formula):
    """纯凝聚相（金属单质、氧化物、不溶物、固体试剂）：活度按 1，不进反应商。"""
    entry = reagent_of(rules, formula)
    if entry.get("kind") == "solid":
        return True
    if activity_index(rules, formula) >= 0:
        return True
    if formula in (rules.get("oxides") or {}):
        return True
    return _is_insoluble_solid(rules, formula)


def _escapes(rules, formula, sealed=False):
    """气体逸出 / 纯凝聚相：不进反应商。它们一出现就把平衡往右推——
    这正是教材里"生成气体或沉淀就反应完全"的来历。

    **密闭恒容容器里气体不逸出**（sealed=True）：气体留在釜里，按分压进反应商，
    可逆反应（N2+3H2 -> 2NH3、2NO2 -> N2O4 这类）才会真的停在平衡上。
    """
    if is_gas(rules, formula):
        return not sealed
    return _is_pure_phase(rules, formula)


def condition_of(rules, key):
    return ((rules.get("reaction_conditions") or {}).get(key)) or {}


def condition_state(rules, condition, amounts, temperature, needs=None):
    """这个反应条件满足没有：(满足?, 缺什么, 条件温度)。

    needs 是这条反应额外点名要的东西（比如催化剂二氧化锰）：容器里没有就报"缺什么"，
    而不是让老师看到"什么都没发生"却不知道为什么。
    """
    info = condition_of(rules, condition)
    need_t = float(info.get("temperature") or T_STANDARD)
    required = needs or info.get("needs")
    if required and amounts.get(required, 0) <= 1e-9:
        return False, "需要有 %s" % required, need_t
    if temperature + 0.5 < need_t:
        return False, "需要「%s」（现在约 %d K）" % (info.get("label") or condition, int(temperature)), need_t
    return True, "", need_t


def rate_tau(rate_class, temperature, need_temperature):
    """时间常数 τ（秒）：条件刚好够就是这一档的基本值，温度越低越慢（Arrhenius 折算）。

    下限压到基本值的 1/4：高温下真实速率快得离谱，但界面上的动画还得看得见——
    这是**演示口径**，不是动力学常数（真实 τ 由 rate_class 与温度共同决定，代码里写明了）。
    """
    base = RATE_BASE.get(rate_class, RATE_BASE["normal"])
    exponent = DEFAULT_EA / R_GAS * (1.0 / max(temperature, 1.0) -
                                     1.0 / max(need_temperature, 1.0))
    return max(base * 0.25, base * math.exp(max(min(exponent, 60.0), -60.0)))


def equilibrium_extent(rules, k_value, left, right, amounts, volume, sealed=False):
    """按 Q(x) = K 求反应进度 x（mol）。left/right 是 [(式子, 系数)]。

    开放容器：Q 只算**溶在溶液里**的物种（浓度 = mol / 体积），气体逸出、沉淀、金属/氧化物
    按活度 1 处理、不进 Q——所以"生成气体或沉淀就反应完全"，和教材结论一致。
    **密闭恒容容器（sealed=True）**：气体留在里面，按 c = n/V 进 Q，可逆反应才停在平衡上。
    """
    limits = [amounts.get(f, 0.0) / c for f, c in left if c > 0]
    x_max = min(limits) if limits else 0.0
    if x_max <= 1e-12:
        return 0.0
    if not [1 for f, _ in left + right if not _escapes(rules, f, sealed)]:
        return x_max                    # 两边都是气体（开放）/沉淀/纯固体：直接反应到底
    if k_value is None:
        return x_max

    def quotient(x):
        value = 1.0
        for formula, count in right:
            if _escapes(rules, formula, sealed):
                continue
            value *= ((amounts.get(formula, 0.0) + count * x) / volume) ** count
        for formula, count in left:
            if _escapes(rules, formula, sealed):
                continue
            remain = amounts.get(formula, 0.0) - count * x
            if remain <= 1e-15:
                return float("inf")
            value /= (remain / volume) ** count
        return value

    if quotient(0.0) >= k_value:
        return 0.0
    if quotient(x_max) <= k_value:
        return x_max
    low, high = 0.0, x_max
    for _ in range(60):
        mid = (low + high) / 2.0
        if quotient(mid) < k_value:
            low = mid
        else:
            high = mid
    return (low + high) / 2.0


def _candidates(rules, amounts, temperature):
    """按容器里现有的东西列出可能发生的反应（顺序 = 先算谁）。

    有机反应、特例反应（铝/硅/氯那几条）按表里点名的反应物与条件；
    无机物走复分解 / 置换 / 氧化物+酸 / 受热分解。
    """
    present = [key for key, mol in amounts.items() if mol > 1e-9]
    out = []
    for table in ("organic_reactions", "special_reactions"):
        for rule in _list_of(rules, table):
            need = rule.get("reactants") or []
            if need and all(amounts.get(item, 0.0) > 1e-9 for item in need):
                out.append({"kind": "organic" if table == "organic_reactions" else "special",
                            "rule": rule, "rate": "normal",
                            "reactants": list(need), "products": list(rule.get("products") or []),
                            "condition": rule.get("condition") or "none",
                            "reversible": bool(rule.get("reversible")),
                            "phenomenon": rule.get("phenomenon") or "",
                            "literal": rule.get("literal") or ""})
    # 氧化还原/燃烧那一族（C、S、P、Fe、Mg、Na、H₂、Cu + O₂/Cl₂，以及浓硫酸一族）：
    # 以前 sim 这条路**根本不查这张表** —— 所以"镁条放氧气里加热"在实验台什么都没发生，
    # 而 agent 用「点燃」却是好的。渲染借 predict_redox（表里写死的 balanced 字段它认），
    # 不在候选阶段重复实现一遍配平。
    for row in redox_entries(rules):
        need = row.get("reactants") or []
        if not need or not all(amounts.get(item, 0.0) > 1e-9 for item in need):
            continue
        built = predict_redox(rules, need, 1273)
        if not built or not built.get("ok"):
            continue
        out.append({"kind": "redox", "result": built, "rate": "fast",
                    "condition": row.get("condition") or "none", "reversible": False,
                    "phenomenon": row.get("phenomenon") or built.get("phenomenon") or ""})
    for index, first in enumerate(present):
        for second in present[index + 1:]:
            result = predict_double_displacement(rules, first, second)
            if result.get("ok"):
                out.append({"kind": "double", "result": result, "rate": "instant",
                            "condition": "none", "reversible": False})
            oxide = (rules.get("oxides") or {}).get(first)
            if oxide and second in (((rules.get("acids") or {}).get("strong")) or []) + \
                    (((rules.get("acids") or {}).get("weak")) or []):
                out.append({"kind": "oxide", "oxide": first, "acid": second, "rate": "fast",
                            "condition": "none", "reversible": False})
            oxide = (rules.get("oxides") or {}).get(second)
            if oxide and first in (((rules.get("acids") or {}).get("strong")) or []) + \
                    (((rules.get("acids") or {}).get("weak")) or []):
                out.append({"kind": "oxide", "oxide": second, "acid": first, "rate": "fast",
                            "condition": "none", "reversible": False})
            if activity_index(rules, first) >= 0:
                result = predict_replacement(rules, first, second)
                if result.get("ok"):
                    out.append({"kind": "replace", "result": result, "rate": "fast",
                                "condition": "none", "reversible": False})
            if activity_index(rules, second) >= 0:
                result = predict_replacement(rules, second, first)
                if result.get("ok"):
                    out.append({"kind": "replace", "result": result, "rate": "fast",
                                "condition": "none", "reversible": False})
    for key in present:
        decomposed = decompose_rule(rules, key, amounts)
        if decomposed:
            out.append(decomposed)
    return out


def decompose_rule(rules, key, amounts):
    """受热分解表里这一条。

    催化剂不在容器里也要**照样报出来**（条件不满足，缺什么写清楚）——不然老师看到的是
    "什么都没发生"，而真实原因是"忘了加二氧化锰"。
    """
    for item in _list_of(rules, "decompose", "rules"):
        if item.get("reactant") != key:
            continue
        catalyst = item.get("catalyst")
        return {"kind": "decompose", "rate": "slow",
                "condition": item.get("condition") or "heat",
                "needs": catalyst or None,
                "reactants": [key], "products": list(item.get("products") or []),
                "catalyst": catalyst or "", "reversible": False,
                "phenomenon": item.get("phenomenon") or ""}
    return None


def oxide_products(rules, oxide, acid):
    """氧化物 + 酸 -> 盐 + 水（离子模型算不出来这条，单独认）。"""
    metal = (rules.get("oxides") or {}).get(oxide)
    acid_pair = split_salt(rules, acid)
    if not metal or not acid_pair:
        return None
    anion = acid_pair[1]
    salt = combine_ions(rules, metal, anion)
    if not salt:
        return None
    numbers = balance([oxide, acid], [salt, "H2O"])
    if numbers is None:
        return None
    return {"left": [(oxide, numbers[0][0]), (acid, numbers[0][1])],
            "right": [(salt, numbers[1][0]), ("H2O", numbers[1][1])],
            "equation": "%s%s + %s%s = %s%s + %sH2O" % (
                "" if numbers[0][0] == 1 else numbers[0][0], oxide,
                "" if numbers[0][1] == 1 else numbers[0][1], acid,
                "" if numbers[1][0] == 1 else numbers[1][0], salt,
                "" if numbers[1][1] == 1 else numbers[1][1])}


def _stoichiometry(rules, candidate):
    """把一条候选反应换成 [(式子, 系数)] 与方程式文本。"""
    kind = candidate["kind"]
    if kind in ("organic", "decompose", "special"):
        left = list(candidate["reactants"])
        right = list(candidate["products"])
        if candidate.get("literal"):
            return None, None, candidate["literal"]
        numbers = balance(left, right)
        if numbers is None:
            return None, None, ""
        return (list(zip(left, numbers[0])), list(zip(right, numbers[1])),
                _equation_text(list(zip(left, numbers[0])), list(zip(right, numbers[1]))))
    if kind == "oxide":
        built = oxide_products(rules, candidate["oxide"], candidate["acid"])
        if not built:
            return None, None, ""
        return built["left"], built["right"], built["equation"]
    result = candidate.get("result") or {}
    try:
        left, right = parse_equation(result.get("equation") or "")
    except ValueError:
        return None, None, ""
    return left, right, result.get("equation") or ""


def _equation_text(left, right):
    def render(items):
        return " + ".join(f if n == 1 else "%d%s" % (n, f) for f, n in items)
    return "%s = %s" % (render(left), render(right))


def _reaction_thermo(thermo, rules, left, right, temperature):
    """按分子式查热化学，算出这个温度下的 ΔrG° 与 K（查不到就返回 None）。"""
    if not thermo or not thermo.get("rows"):
        return None
    mapped_left = [(species_formula(rules, f) or f, n) for f, n in left]
    mapped_right = [(species_formula(rules, f) or f, n) for f, n in right]
    if any(not item[0] for item in mapped_left + mapped_right):
        return None
    energy = reaction_energy(thermo, rules, mapped_left, mapped_right, temperature)
    if not energy.get("ok"):
        return None
    d_g = energy["dG"]
    try:
        k_value = math.exp(-d_g * 1000.0 / (8.314 * temperature))
    except OverflowError:
        k_value = float("inf")
    return {"dG": d_g, "dH": energy["dH"], "dS": energy["dS"], "K": k_value}


def species_appearance(rules, key):
    """这个物种在界面上该画成什么（界面不自己懂化学，只按这个画）。

    kind: liquid（溶在溶液里）/ solid（纯固体）/ precipitate（沉淀）/ gas（气体逸出）
    """
    if is_gas(rules, key) or reagent_of(rules, key).get("kind") == "gas":
        return {"kind": "gas", "color": ""}
    if _is_insoluble_solid(rules, key):
        return {"kind": "precipitate", "color": solid_color(rules, key) or "#f2f4f7"}
    if _is_pure_phase(rules, key):
        return {"kind": "solid", "color": solid_color(rules, key) or "#c9ced6"}
    return {"kind": "liquid", "color": solution_color(rules, key)}


def lab_simulate(rules, thermo, contents, temperature=None, seconds=None, volume=None,
                 sealed=False, pressure=None):
    """实验台的核心：**把"容器里有什么"算成"接下来会怎样"**。

    加进去就开始算，不需要点"反应"：
      * 条件满足的反应立刻开始，按 ΔrG° 算出的 K 停在**反应限度**上（不是点一下反应一下）；
      * 条件不够的（要加热、要浓硫酸、要催化剂）明确说缺什么，并且给出"几乎不进行"的结论；
      * 全程给一条随时间变化的轨迹（mol 数），界面照着播动画——所以现象是"反应进行中"的样子。

    contents: [{"key": "CaCO3", "mol": 0.01}, ...]（也可以直接给式子列表，按默认一份算）。
    temperature: K（常温 298.15，酒精灯加热 673，高温 1273）。
    """
    temperature = float(temperature or T_STANDARD)
    volume = float(volume or DEFAULT_VOLUME)
    seconds = float(seconds or 6.0)
    sealed = bool(sealed)
    amounts = {}
    order = []
    for item in contents or []:
        if isinstance(item, dict):
            key = str(item.get("key") or "")
            mol = float(item.get("mol") or 0.0)
        else:
            key, mol = str(item or ""), 0.0
        if not key:
            continue
        if mol <= 0:
            mol = float((rules.get("amounts") or {}).get(
                reagent_of(rules, key).get("kind") or "", 0.0)) or \
                float((rules.get("amounts") or {}).get("_default") or 0.02)
        if key not in amounts:
            order.append(key)
            amounts[key] = 0.0
        amounts[key] += mol
    if not amounts:
        return {"ok": True, "empty": True, "temperature": temperature,
                "conditions": [], "reactions": [], "steps": [], "final": [],
                "visual": {}, "phenomenon": "", "limit_note": ""}

    _ESCAPE_CACHE = None
    # 同一物质的两种写法（浓硫酸 = H2SO4）：算条件、算反应时按右边那个式子认，
    # 但界面上的瓶子还是"浓硫酸"那把（老师要看到自己选的东西）。
    for alias, real in (rules.get("aliases") or {}).items():
        if alias == "_note" or alias not in amounts or real in amounts:
            continue
        amounts[real] = amounts[alias]
        order.append(real)
    # 溶液里自带的水：铝/锌 + 强碱这类反应要水参加，但老师不会专门去点"蒸馏水"。
    # 只要容器里有溶液（液体试剂），就按"水大量过量"给一份溶剂水（不进 contents/终态显示）。
    if amounts.get("H2O", 0.0) <= 1e-9 and any(
            reagent_of(rules, key).get("kind") == "liquid" for key in list(amounts)):
        amounts["H2O"] = SOLVENT_WATER
        solvent = True
    else:
        solvent = False
    # 恒容容器里"设定压强"：按 pV=nRT 反算该有多少 mol 气体（保持老师给的配料比），
    # 这就是教材里"充入气体使压强达到 p0"那句话。体积不变时改变压强的另一条路是
    # 充惰性气体——那条路**不影响平衡**（各组分浓度没变），模型里自然也这样。
    charged = None
    if sealed and pressure:
        want = float(pressure)                       # kPa
        inert = set((rules.get("inert_gases") or {}).get("list") or [])
        # 只把**参与反应的气体**归一到目标压强：惰性气体是额外叠加上去的。
        # 这样两个考点才分得开——"充气到 p0" 改的是反应气的量（平衡移动），
        # "恒容再充惰气" 只抬高总压（各组分浓度不变，平衡不移动）。
        gas_keys = [key for key in amounts
                    if is_gas(rules, key) and key not in inert and amounts[key] > 1e-9]
        have = sum(amounts[key] for key in gas_keys)
        if gas_keys and have > 1e-9:
            target = want * volume / (8.314 * temperature)      # mol
            scale = target / have
            for key in gas_keys:
                amounts[key] *= scale
            inert_mol = sum(amounts[key] for key in amounts
                            if key in inert and amounts[key] > 1e-9)
            charged = {"kPa": want, "mol": round(target, 6), "scale": round(scale, 4),
                       "inert_mol": round(inert_mol, 6)}
    initial = dict(amounts)
    reactions = []
    for candidate in _candidates(rules, amounts, temperature):
        left, right, equation = _stoichiometry(rules, candidate)
        if not left or not right:
            if candidate.get("literal"):
                reactions.append({"kind": candidate["kind"], "equation": candidate["literal"],
                                  "met": True, "extent": 0.0, "tau": 0.0,
                                  "phenomenon": candidate.get("phenomenon") or "",
                                  "literal": True, "limit_mol": 0.0,
                                  "note": "这条只给结论（没有配平式）"})
            continue
        if not any(amounts.get(f, 0.0) > 1e-12 for f, _ in left):
            continue
        met, missing, need_t = condition_state(rules, candidate.get("condition") or "none",
                                               amounts, temperature,
                                               needs=candidate.get("needs"))
        tau = rate_tau(candidate.get("rate") or "normal", temperature, need_t)
        energy = _reaction_thermo(thermo, rules, left, right, temperature)
        k_value = energy["K"] if energy else None
        if k_value is not None and (k_value > 1e6 or k_value < 1e-9):
            k_value = None if k_value > 1e6 else k_value
        limits = [amounts.get(f, 0.0) / c for f, c in left if c > 0]
        limit_mol = min(limits) if limits else 0.0
        extent = equilibrium_extent(rules, k_value, left, right, amounts, volume, sealed)
        if not met:
            extent = 0.0           # 条件不够：组成不动，只报"缺什么"
        if extent <= 1e-9 and met:
            continue
        for formula, count in left:
            amounts[formula] = max(0.0, amounts.get(formula, 0.0) - count * extent)
        for formula, count in right:
            amounts[formula] = amounts.get(formula, 0.0) + count * extent
            if formula not in order:
                order.append(formula)
        reactions.append({
            "kind": candidate["kind"], "equation": equation, "met": met,
            "missing": missing, "temperature_needed": need_t,
            "rate": candidate.get("rate") or "normal", "tau": round(tau, 3),
            "reversible": bool(candidate.get("reversible")),
            "extent": extent, "limit_mol": limit_mol, "k": k_value,
            "dG": energy["dG"] if energy else None,
            "phenomenon": candidate.get("phenomenon") or
                          ((candidate.get("result") or {}).get("phenomenon") or ""),
            "why": (candidate.get("result") or {}).get("why") or "",
        })
    final = {key: mol for key, mol in amounts.items() if mol > 1e-12}

    # 火焰颜色（>400 K 就是点了火）：
    #   * 有金属 → 焰色反应（物理变化），没别的反应时单列一条给界面照着上色；
    #   * 可燃物（甲烷/乙醇/苯/硫/镁……）→ 燃烧那一条反应本身带上火焰颜色。
    lit = temperature > 400
    flame = flame_color_of(rules, contents) if lit else ""
    burn = burn_color_of(rules, contents) if lit else ""
    live_now = [item for item in reactions if item.get("met")]
    if burn and live_now:
        for row in live_now:
            if not row.get("flame_color"):
                row["flame_color"] = burn
    elif flame and not live_now:
        reactions.append({
            "kind": "flame", "equation": "（焰色反应是物理变化，没有化学方程式）",
            "met": True, "extent": 0.0, "tau": 0.0, "k": None, "limit_mol": 0.0,
            "phenomenon": "焰色反应：火焰呈%s —— 这是金属元素的物理性质，不是化学反应" % flame,
            "flame_color": flame, "intensity": "normal", "gas_colors": {},
            "why": "焰色反应"})

    live = [item for item in reactions if item.get("met") and item.get("extent", 0) > 1e-12]
    slowest = max([item.get("tau") or 0.0 for item in live] or [0.0])
    steps = []
    if slowest and slowest < seconds * 30:
        samples = 20
        for index in range(samples + 1):
            moment = seconds * index / float(samples)
            done = 1.0 - math.exp(-moment / slowest)
            snapshot = {}
            for key in order:
                start = initial.get(key, 0.0)
                end = final.get(key, 0.0)
                value = start + (end - start) * done
                if value > 1e-9:
                    snapshot[key] = round(value, 6)
            steps.append({"t": round(moment, 3), "amounts": snapshot})

    formed = []
    for item in live:
        for token in (item.get("equation") or "").split("=")[-1].split("+"):
            token = re.sub(r"^\d+", "", token.strip())
            if token and token not in formed:
                formed.append(token)
    visual = visual_hints(rules, formed, list(initial)) if formed else {"gas": [], "solids": [],
                                                                       "precipitate": None}
    # 颜色按"反应前后还剩什么溶在里面"算：溴水被加成掉就该褪色，不是照着产物瞎编一个色
    before = solution_color_of(rules, initial)
    after = solution_color_of(rules, final)
    visual["solution_before"] = before["color"]
    visual["solution_after"] = after["color"]
    visual["solution_alpha_before"] = before["alpha"]
    visual["solution_alpha_after"] = after["alpha"]
    # 分层：不溶于水的有机层单独一条（界面画成色带，不然"乙酸乙酯浮在上层"根本看不出来）
    visual["layers"] = solution_layers(rules, final)
    if visual["layers"]:
        # 已经画成"一层"的不要又在杯底画一堆（固体油脂、硬脂酸这些）
        layered = set(item["key"] for item in visual["layers"])
        visual["solids"] = [row for row in visual.get("solids") or []
                            if row.get("formula") not in layered]
    phenomena = []
    if visual["layers"]:
        described = "、".join(
            "%s%s" % ("上层" if item["where"] == "up" else "下层", item["key"])
            for item in visual["layers"])
        phenomena.append("液体分层（%s）" % described)
    for item in live:
        text = item.get("phenomenon") or ""
        if text and text not in phenomena:
            phenomena.append(text)
    frozen = [item for item in reactions if not item.get("met")]
    limit_notes = []
    for item in live:
        if item.get("k") is None:
            limit_notes.append("%s：没有热化学数据，按教材结论算反应完全" % item["equation"])
        else:
            limit_notes.append("%s：K≈%s，转化率约 %d%%（反应停在限度上）"
                               % (item["equation"], _format_k(item["k"]),
                                  round(100.0 * item["extent"] / max(1e-12, item["limit_mol"]))))
    conditions = ["常温" if temperature <= T_STANDARD + 1 else "加热到约 %d K" % int(temperature)]
    if sealed:
        conditions.append("恒容密闭（%.1f L）：气体不逸出，按 pV=nRT 算压强" % volume)
    if live:
        conditions.append("反应 %d 个，从加入药品起就开始进行" % len(live))
    if frozen:
        conditions.append("%d 个反应条件不够，暂时几乎不进行" % len(frozen))
    # 稀有气体：要**明说**它不反应，不能只是"什么都没发生"
    inert = (rules.get("inert_gases") or {}).get("list") or []
    inert_present = [key for key in amounts if key in inert and amounts[key] > 1e-9]
    if inert_present:
        conditions.append("惰性气体（%s）不参与反应" % "、".join(inert_present))
    pressure_out = None
    if sealed:
        gas_mol = sum(mol for key, mol in final.items() if is_gas(rules, key))
        if charged:
            conditions.append("充气到 %.4g kPa 起算（%.4g mol 气体）"
                              % (charged["kPa"], charged["mol"]))
        if gas_mol > 0:
            # pV = nRT：p(kPa) = n(mol) * 8.314 * T(K) / V(L)
            pressure_out = {"kPa": round(gas_mol * 8.314 * temperature / volume, 1),
                            "mol": round(gas_mol, 6), "volume": volume,
                            "start_kPa": charged["kPa"] if charged else None}
            conditions.append("反应后釜内气体共 %.4f mol，约 %.4g kPa"
                              % (gas_mol, pressure_out["kPa"]))
    indicator = _indicator_state(rules, amounts)
    appearance = {}
    for key in list(initial) + list(final):
        if key not in appearance:
            appearance[key] = species_appearance(rules, key)
    return {"ok": True, "empty": False, "temperature": temperature, "volume": volume,
            "conditions": conditions, "seconds": seconds,
            "reactions": reactions, "steps": steps, "appearance": appearance,
            "initial": [{"key": key, "mol": round(value, 6)} for key, value in initial.items()
                        if not (solvent and key == "H2O")],
            "final": [{"key": key, "mol": round(value, 6)} for key, value in final.items()
                      if not (solvent and key == "H2O")],
            "visual": visual, "phenomenon": "；".join(phenomena),
            "indicator": indicator,
            "sealed": sealed, "pressure": pressure_out, "charged": charged,
            "t95": round(3.0 * slowest, 2) if slowest else 0.0,
            "limit_note": "；".join(limit_notes),
            "frozen": [{"equation": item.get("equation") or "",
                        "missing": item.get("missing") or "",
                        "phenomenon": item.get("phenomenon") or ""} for item in frozen]}


def solution_nature(rules, amounts):
    """溶液的酸碱性：还剩酸就酸性、还剩碱就碱性，强碱弱酸盐/强酸弱碱盐按教材口径。

    数据在 rules.nature_hint 里（Na2CO3 显碱性、NH4Cl 显酸性这类），代码里不写死。
    """
    acids = set(_list_of(rules, "acids", "strong")) | set(_list_of(rules, "acids", "weak"))
    bases = {key for key, pair in (rules.get("salts") or {}).items()
             if isinstance(pair, list) and len(pair) == 2 and pair[1] == "OH"}
    bases.add("NH3H2O")
    hint = rules.get("nature_hint") or {}
    for key, mol in (amounts or {}).items():
        if mol <= 1e-9:
            continue
        if key in acids:
            return "acid"
        if key in bases:
            return "base"
    for key, mol in (amounts or {}).items():
        if mol > 1e-9 and hint.get(key):
            return hint[key]
    return "neutral"


def _indicator_state(rules, amounts):
    """指示剂的颜色（滴了才返回）：界面直接按这个颜色给溶液上色。"""
    table = rules.get("indicators") or {}
    out = []
    for key in table:
        if key == "_note" or amounts.get(key, 0.0) <= 1e-9:
            continue
        color = (table.get(key) or {}).get(solution_nature(rules, amounts))
        if color:
            out.append({"key": key, "nature": solution_nature(rules, amounts), "color": color})
    return out


def _format_k(value):
    if value is None:
        return "?"
    if value >= 1e6:
        return "很大（反应完全）"
    if value <= 1e-6:
        return "极小（基本不反应）"
    return "%.2g" % value


def lab_presets(rules):
    """预设实验清单（老师一键看到该摆什么、怎么做）。"""
    return ((rules.get("lab_presets") or {}).get("list")) or []


def lab_reagents(rules):
    """实验台试剂架的**全套**清单：手写的那批 + 按"阳离子 × 阴离子"生成的那一批。

    老师要的是"高中常见的盐成套都在"，手打一百多条既打不全也容易错，所以成套那部分
    由 `rules.reagent_grid` 生成：阳离子 × 阴离子 -> 化学式（`combine_ions`）-> 中文名
    （阳离子名 + 阴离子名）-> 该是固体还是溶液（按溶解性判）。
    `skip` 里是**不该摆上架子**的组合（Al2(CO3)3 双水解、Fe2S3 遇水分解、NH4OH 不存在…）。

    每一种都带 `kind`（solid/liquid/gas）与 `tag`（货架分组），界面按这个分组摆。
    """
    out = []
    seen = set()
    for entry in _list_of(rules, "reagents"):
        key = entry.get("key")
        if not key or key in seen:
            continue
        seen.add(key)
        item = dict(entry)
        item["tag"] = item.get("tag") or "其他"
        out.append(item)
    grid = rules.get("reagent_grid") or {}
    skip = set(grid.get("skip") or [])
    cation_names = grid.get("cation_names") or {}
    anion_names = grid.get("anion_names") or {}
    always_solid = set(grid.get("always_solid") or [])
    for cation in grid.get("cations") or []:
        for anion in grid.get("anions") or []:
            if cation + "|" + anion in skip:
                continue
            formula = combine_ions(rules, cation, anion)
            if not formula or formula in seen:
                continue
            seen.add(formula)
            solubility_value = solubility(rules, cation, anion)
            if formula in always_solid or solubility_value == "不溶":
                kind = "solid"
                tag = "固体试剂"
            elif solubility_value == UNKNOWN:
                continue
            else:
                kind = "liquid"
                tag = "盐溶液"
            label = "%s%s" % (anion_names.get(anion, anion), cation_names.get(cation, cation))
            out.append({"key": formula, "label": label, "kind": kind, "tag": tag,
                        "generated": "%s|%s" % (cation, anion)})
    for entry in grid.get("extra") or []:
        if entry.get("key") and entry["key"] not in seen:
            seen.add(entry["key"])
            out.append(dict(entry))
    return out


def lab_catalog(rules):
    """实验台的器材与试剂清单（试剂带上颜色，界面给药瓶标出来）。"""
    reagents = []
    for item in lab_reagents(rules):
        entry = dict(item)
        key = entry.get("key") or ""
        entry["color"] = (solution_color(rules, key) or solid_color(rules, key) or "")
        reagents.append(entry)
    return {"apparatus": ((rules.get("apparatus") or {}).get("list")) or [],
            "reagents": reagents,
            "presets": lab_presets(rules),
            "setup_rules": ((rules.get("setup_rules") or {}).get("rules")) or []}


def solution_color(rules, formula):
    """溶液颜色（教材口径）；表里没有就返回空串，界面按无色处理——不猜。"""
    return ((rules.get("solution_color") or {}).get(formula)) or ""


def solid_color(rules, formula):
    """固体/沉淀颜色；表里没有返回空串。"""
    return ((rules.get("solid_color") or {}).get(formula)) or ""


def is_gas(rules, formula):
    """常温常压下是气体（界面据此冒气泡）。"""
    return formula in (((rules.get("gas_species") or {}).get("list")) or [])


def visual_hints(rules, products, solution=None):
    """把一条反应的产物翻译成**界面能直接画的东西**。

    界面不该自己懂化学：它要的就是"冒不冒气泡、有没有沉淀、什么颜色、溶液从什么色变成什么色"。
    这些判断都在引擎这边（溶解度、颜色表都在数据里），界面只负责画。

    products: 产物化学式列表（配平后的）；solution: 反应前容器里已有的物质（算颜色变化用）。
    """
    gases = []
    precipitate = None
    solids = []
    colors = []
    for formula in products:
        if is_gas(rules, formula):
            gases.append(formula)
            continue
        # 只有"不溶物 / 纯凝聚相（金属、氧化物、固体试剂）"才画成固体；
        # 溶在水里的盐（NaCl、CH3COONa…）哪怕颜色表里有固体的白，也不能画成沉淀。
        if _is_insoluble_solid(rules, formula):
            if precipitate is None:
                precipitate = {"formula": formula,
                               "color": solid_color(rules, formula) or "#f2f4f7"}
            continue
        if _is_pure_phase(rules, formula):
            solids.append({"formula": formula,
                           "color": solid_color(rules, formula) or "#c9ced6"})
            continue
        color = solution_color(rules, formula)
        if color:
            colors.append(color)
    before = ""
    for formula in (solution or []):
        color = solution_color(rules, formula)
        if color:
            before = color
            break
    after = colors[0] if colors else ("" if precipitate else before)
    return {"gas": gases, "precipitate": precipitate, "solids": solids,
            "solution_before": before, "solution_after": after}


def solution_layers(rules, amounts):
    """液体分成哪几层：不溶于水的有机层单独一层，其余算水层。

    返回 [{"key", "where": "up"/"down", "color", "mol", "solid"}]（从上到下按 up -> 水层 -> down 排），
    界面照着画色带。**不分层就返回空列表**（界面按一层画）。
    哪一层在上/在下按密度来，数据在 rules.organic_layer 里（苯 0.88 浮、四氯化碳 1.59 沉）。

    常温下是**固体**的不溶有机物（油脂、硬脂酸、苯酚）也算一层：它们浮在水面或沉在水底，
    画成"一层"比画成杯底一小堆更像真的；`solid` 标出来，界面画得实一点。
    """
    table = rules.get("organic_layer") or {}
    colors = rules.get("layer_color") or {}
    layers = []
    aqueous = 0.0
    for key, mol in (amounts or {}).items():
        if mol <= 1e-9:
            continue
        where = table.get(key)
        if where in ("up", "down"):
            layers.append({"key": key, "where": where,
                           "color": colors.get(key) or "#f0e2bd",
                           "mol": round(mol, 6),
                           "solid": bool(_escapes(rules, key))})
            continue
        if _escapes(rules, key):
            continue
        aqueous += mol
    if not layers:
        return []
    for item in layers:
        item["aqueous_mol"] = round(aqueous, 6)
    layers.sort(key=lambda item: 0 if item["where"] == "up" else 1)
    return layers


def solution_color_of(rules, amounts):
    """当前液体看起来是什么颜色、有多浓（0~1）。

    颜色按还溶在里面的有色物质算（溴水被加成掉就该褪色）；浓度按还剩多少 mol 估，
    所以"蓝色变浅"也画得出来——同一个色、更淡。
    **会分层的有机层不算进水层的颜色**（它自己一层，见 solution_layers）。
    """
    best = {"color": "", "alpha": 0.0}
    layer_table = rules.get("organic_layer") or {}
    for key, mol in (amounts or {}).items():
        if mol <= 1e-9 or _escapes(rules, key) or layer_table.get(key):
            continue
        color = solution_color(rules, key)
        if not color:
            continue
        alpha = min(1.0, mol / 0.05)
        if alpha > best["alpha"]:
            best = {"color": color, "alpha": round(alpha, 3)}
    return best


# 产物化学式 -> (阳离子, 阴离子)：只认"阳离子打头"的教材写法（NaCl、CaCO3、Cu(OH)2…），
# 阴离子按**长到短**匹配，所以 CO3/SO4/NO3 不会被当成"结尾多一个数字"砍掉。
CATIONS_IN_FORMULA = ("NH4", "Na", "K", "Ca", "Ba", "Mg", "Al", "Zn", "Fe", "Cu", "Ag", "Pb")
ANIONS_IN_FORMULA = ("CH3COO", "HCO3", "ClO3", "MnO4", "SiO3", "NO3", "SO4", "SO3", "CO3",
                     "OH", "Cl", "Br", "I", "S", "F")


def _split_for_solubility(formula):
    text = (formula or "").strip()
    for cation in CATIONS_IN_FORMULA:
        if not text.startswith(cation):
            continue
        rest = text[len(cation):].replace("(", "").replace(")", "")
        rest = re.sub(r"^\d+", "", rest)
        for anion in ANIONS_IN_FORMULA:
            if rest.startswith(anion):
                return cation, anion
        return cation, rest
    return "", text


def _is_insoluble_solid(rules, formula):
    """这个产物是不是"不溶的固体"（界面要画沉淀）。"""
    pair = _split_for_solubility(formula)
    if not pair[0]:
        return False
    return is_precipitate(rules, pair[0], pair[1])


def predict_double_displacement(rules, first, second):
    """两种物质（盐/碱/酸）的溶液混合能不能发生复分解。

    返回 dict：ok / equation / phenomenon / why / reason / detail。
    判据就是教材那三条：**生成沉淀、生成气体、生成水**。三条都不沾就不反应——
    这是结论，不是"查不到"。规则表里没有的，一律说"资料未收录"，绝不编造。
    """
    left = split_salt(rules, first)
    right = split_salt(rules, second)
    if not left or not right:
        return {"ok": False, "reason": UNKNOWN,
                "detail": "规则表里没有这两种物质的拆离子方式（%s / %s）" % (first, second)}
    cation_a, anion_a = left
    cation_b, anion_b = right
    if cation_a == cation_b or anion_a == anion_b:
        return {"ok": False, "reason": "两种物质含相同的离子，混合不发生复分解", "detail": ""}

    pairs = [(cation_a, anion_b), (cation_b, anion_a)]
    products = []
    precipitates = []
    gases = []
    made_water = False
    unknown = []
    for cation, anion in pairs:
        if (cation, anion) == ("H", "OH"):
            products.append("H2O")
            made_water = True
            continue
        gas_products, gas_text = _water_and_gas(rules, cation, anion)
        if gas_products:
            products.extend(gas_products)
            gases.append(gas_text)
            made_water = True
            continue
        solubility_value = solubility(rules, cation, anion)
        if solubility_value == UNKNOWN:
            unknown.append((cation, anion))
            continue
        formula = combine_ions(rules, cation, anion)
        if not formula:
            unknown.append((cation, anion))
            continue
        products.append(formula)
        if solubility_value == "不溶":
            precipitates.append((cation, anion, formula))

    if unknown and not (precipitates or gases or made_water):
        return {"ok": False, "reason": UNKNOWN,
                "detail": "有组合的溶解性/电荷表里没有：%s"
                          % "、".join("%s%s" % (ion_name(rules, item[0]), ion_name(rules, item[1]))
                                      for item in unknown)}
    if not (precipitates or gases or made_water):
        return {"ok": False,
                "reason": "互换离子后既没有沉淀、也没有气体、也没有水，不复分解",
                "detail": "（%s、%s 都溶）"
                          % ("".join(ion_name(rules, item) for item in pairs[0]),
                             "".join(ion_name(rules, item) for item in pairs[1]))}
    if unknown:
        return {"ok": False, "reason": UNKNOWN,
                "detail": "有组合的表里没有，不敢下结论：%s"
                          % "、".join("%s%s" % (ion_name(rules, item[0]), ion_name(rules, item[1]))
                                      for item in unknown)}

    numbers = balance([first, second], products)
    if numbers is None:
        return {"ok": False, "reason": "推出来的产物配不平，先当资料未收录",
                "detail": "产物：%s" % "、".join(products)}

    def render(items, counts):
        out = []
        for formula, count in zip(items, counts):
            out.append(formula if count == 1 else "%d%s" % (count, formula))
        return out

    left_text = " + ".join(render([first, second], numbers[0]))
    right_text = " + ".join(render(products, numbers[1]))
    why = []
    if precipitates:
        why.append("生成了难溶的 %s" % precipitates[0][2])
    if gases:
        why.append("生成了气体")
    if made_water and not (precipitates or gases):
        why.append("生成了水（中和反应）")
    phenomenon = []
    if precipitates:
        color = (rules.get("precipitate_color") or {}).get(
            precipitates[0][0] + precipitates[0][1], "白色")
        phenomenon.append("%s沉淀" % color)
    phenomenon.extend(gases)
    if made_water and not (precipitates or gases):
        phenomenon.append("放热，看不到明显现象")
    return {"ok": True, "predicted": True, "equation": "%s = %s" % (left_text, right_text),
            "phenomenon": "、".join(phenomenon), "why": "；".join(why),
            "visual": visual_hints(rules, products, [first, second])}
