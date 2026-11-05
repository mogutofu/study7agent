# -*- coding: utf-8 -*-
"""化学数据取数脚本（构建期联网、运行期离线）。

设计口径（见 docs/chem-engine.md）：不录"反应"，录"物种 + 规则"，
反应由 webui\\chem.py 现场推。所以这里只负责把开放数据源里的**物种**拉下来。

四个数据源（ATcT 与 ChEBI 只探通道，作为补漏备选）：
  1. Wikidata            CC0          中文名/别名/分子式/SMILES/InChI/CAS
  2. PubChem             free-to-use  计算类字段（分子式/SMILES/分子量）
  3. NIST-JANAF (SRD 13) 美国政府作品  标准生成焓/吉布斯自由能/熵
  4. NIST WebBook       免费          物种热化学（JANAF 没收录的补这里）
  5. ATcT (阿贡实验室)   美国政府作品  高精度热化学值（本次探测 403，备选）
  6. ChEBI               CC BY 4.0    化合物本体（补中文名与别名）

用法：
    .\\python\\python.exe scripts\\fetch-chem-data.py --probe    只探测通道
    .\\python\\python.exe scripts\\fetch-chem-data.py --sample   各拉一份样本存到 vendor\\chem-probe\\

真正常规导出（物种表 5000 条）在 M3 做，这里先把通道打通并把样本留下，
免得后面才发现某条通道在教室网络上根本走不通。
"""

import argparse
import importlib.util
import io
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fetch_common as net  # noqa: E402

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROBE_DIR = os.path.join(BASE_DIR, "vendor", "chem-probe")

# 探针只查三个最常见的物种，够验证通道和字段名
WIKIDATA_BASE = "https://query.wikidata.org/sparql?format=json&query="
WIKIDATA_QUERY = """
SELECT ?item ?itemLabel ?formula ?smiles ?cas WHERE {
  VALUES ?item { wd:Q283 wd:Q408 wd:Q8063 }
  OPTIONAL { ?item wdt:P274 ?formula }
  OPTIONAL { ?item wdt:P233 ?smiles }
  OPTIONAL { ?item wdt:P231 ?cas }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "zh-hans,zh,en". }
}
LIMIT 12
"""

SOURCES = [
    {"label": "Wikidata SPARQL（CC0）", "url": WIKIDATA_BASE + net.quote(WIKIDATA_QUERY),
     "file": "wikidata.json", "sample": True},
    {"label": "PubChem PUG-REST（free-to-use）",
     "url": "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/water/property/"
            "MolecularFormula,CanonicalSMILES,MolecularWeight/JSON",
     "file": "pubchem.json", "sample": True},
    {"label": "NIST-JANAF（SRD 13，免费）", "url": "https://janaf.nist.gov/tables/H-064.html",
     "file": "janaf-water.html", "sample": True},
    {"label": "NIST WebBook（免费，物种热化学）",
     "url": "https://webbook.nist.gov/cgi/cbook.cgi?ID=C7732185&Units=SI",
     "file": "webbook-water.html", "sample": True},
    {"label": "ATcT（阿贡实验室，可选）", "url": "https://atct.anl.gov/",
     "file": "atct.html", "sample": False},
    {"label": "ChEBI 本体（CC BY 4.0，258 MB，只探通道不取样）",
     "url": "https://ftp.ebi.ac.uk/pub/databases/chebi/ontology/chebi.obo",
     "file": "chebi.obo", "sample": False},
]

# 物种表：挑"有 CAS 号"的那批（这批数据最规矩），带中文标签
WIKIDATA_SPECIES_QUERY = """
SELECT ?item ?itemLabel ?formula ?smiles ?cas WHERE {
  ?item wdt:P231 ?cas .
  OPTIONAL { ?item wdt:P274 ?formula }
  OPTIONAL { ?item wdt:P233 ?smiles }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "zh-hans,zh,en". }
}
LIMIT %d OFFSET %d
"""
WIKIDATA_PAGE = 300

# 中学化学最常见的那些物质，按 CAS 号点名（CAS 比 QID 可靠：我写错一个号，Wikidata 就
# 不会返回它，一眼能看出来；而 QID 猜错了会静默拿到别的物质——探针阶段 Q408=澳大利亚
# 就是这么踩的）。这批是"种子"，覆盖教材里的主角，后面再按需扩。
SEED_CAS = (
    "7732-18-5",    # 水
    "7782-44-7",    # 氧气
    "1333-74-0",    # 氢气
    "124-38-9",     # 二氧化碳
    "7647-14-5",    # 氯化钠
    "7664-93-9",    # 硫酸
    "1310-73-2",    # 氢氧化钠
    "7647-01-0",    # 盐酸（氯化氢）
    "471-34-1",     # 碳酸钙
    "7758-98-7",    # 硫酸铜
    "1309-37-1",    # 氧化铁
    "7722-64-7",    # 高锰酸钾
    "64-17-5",      # 乙醇
    "74-82-8",      # 甲烷
    "74-85-1",      # 乙烯
    "74-86-2",      # 乙炔
    "71-43-2",      # 苯
    "64-19-7",      # 乙酸
    "50-99-7",      # 葡萄糖
    "497-19-8",     # 碳酸钠
    "144-55-8",     # 碳酸氢钠
    "7664-41-7",    # 氨
    "7697-37-2",    # 硝酸
    "7440-50-8",    # 铜
    "7439-89-6",    # 铁
    "7440-66-6",    # 锌
    "7440-23-5",    # 钠
    "7440-09-7",    # 钾
    "7440-70-2",    # 钙
    "7440-21-3",    # 硅
    "7723-14-0",    # 磷
    "7704-34-9",    # 硫
    "7782-50-5",    # 氯气
    "7727-37-9",    # 氮气（基准态，算 ΔrG° 要用它的 S°）
    "7782-42-5",    # 石墨（碳的基准态）
    "10043-52-4",   # 氯化钙
    "12125-02-9",   # 氯化铵
    "13106-47-3",   # 碳酸氢铵
    "1332-58-7",    # 高岭土（陶瓷）
    # ---- 第二批：中学教材里常出现的酸/碱/盐/氧化物/单质/有机物 ----
    "7722-84-1",    # 过氧化氢
    "1305-78-8",    # 氧化钙（生石灰）
    "1305-62-0",    # 氢氧化钙（熟石灰）
    "1310-58-3",    # 氢氧化钾
    "7447-40-7",    # 氯化钾
    "7757-79-1",    # 硝酸钾
    "7761-88-8",    # 硝酸银
    "7783-90-6",    # 氯化银
    "7720-78-7",    # 硫酸亚铁
    "1309-33-7",    # 氢氧化铁
    "20427-59-2",   # 氢氧化铜
    "1317-38-0",    # 氧化铜
    "1309-48-4",    # 氧化镁
    "7786-30-3",    # 氯化镁
    "7757-82-6",    # 硫酸钠
    "7758-94-3",    # 氯化亚铁
    "7733-02-0",    # 硫酸锌
    "7664-38-2",    # 磷酸
    "584-08-7",     # 碳酸钾
    "7631-99-4",    # 硝酸钠
    "10361-37-2",   # 氯化钡
    "10043-01-3",   # 硫酸铝
    "21645-51-2",   # 氢氧化铝
    "1344-28-1",    # 氧化铝
    "7631-86-9",    # 二氧化硅
    "7446-09-5",    # 二氧化硫
    "7446-11-9",    # 三氧化硫
    "630-08-0",     # 一氧化碳
    "10102-43-9",   # 一氧化氮
    "10102-44-0",   # 二氧化氮
    "7783-06-4",    # 硫化氢
    "3811-04-9",    # 氯酸钾
    "17194-00-2",   # 氢氧化钡
    "7487-88-9",    # 硫酸镁
    "7727-43-7",    # 硫酸钡
    "7758-99-8",    # 硫酸铜晶体（五水）
    "50-00-0",      # 甲醛
    "75-07-0",      # 乙醛
    "67-56-1",      # 甲醇
    "74-98-6",      # 丙烷
    "106-97-8",     # 丁烷
    "108-88-3",     # 甲苯
    "108-95-2",     # 苯酚
    "141-78-6",     # 乙酸乙酯
    "57-50-1",      # 蔗糖
    "9005-25-8",    # 淀粉
    "56-40-6",      # 甘氨酸
    "57-13-6",      # 尿素
    "127-09-3",     # 乙酸钠
    "7429-90-5",    # 铝
    "7439-95-4",    # 镁
    "7440-59-7",    # 氦
    "7440-37-1",    # 氩
    "7553-56-2",    # 碘
    "7726-95-6",    # 溴
    "7440-22-4",    # 银
    "7440-39-3",    # 钡
    "7439-97-6",    # 汞
)

# 按 CAS 点名取数：一条请求就够，不会像"全表扫 + 标签服务"那样超时
WIKIDATA_SEED_QUERY = """
SELECT ?item ?itemLabel ?formula ?smiles ?cas WHERE {
  VALUES ?cas { %s }
  ?item wdt:P231 ?cas .
  OPTIONAL { ?item wdt:P274 ?formula }
  OPTIONAL { ?item wdt:P233 ?smiles }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "zh-hans,zh,en". }
}
"""


def probe_with_retry(url, tries=3, gap=6.0):
    """Wikidata 的公共端点会限流（429），隔几秒重试一次比直接放弃靠谱。"""
    result = net.probe(url)
    for _ in range(tries - 1):
        if result["ok"] or "429" not in (result["error"] or ""):
            break
        print("  （被限流，%d 秒后重试）" % int(gap))
        time.sleep(gap)
        result = net.probe(url)
    return result


def do_probe(save):
    rows = []
    for source in SOURCES:
        result = probe_with_retry(source["url"])
        result["label"] = source["label"]
        rows.append(result)
    net.report(rows, "化学数据通道探测")
    print("")
    for row in rows:
        print("  %-40s %s" % (row.get("label", ""), "通" if row["ok"] else "不通"))
    if save:
        if not os.path.isdir(PROBE_DIR):
            os.makedirs(PROBE_DIR)
        for row, source in zip(rows, SOURCES):
            if not row["ok"] or not source["sample"]:
                continue
            time.sleep(3.0)  # 同一个端点连着打会被限流，慢一点
            data, error = net.drain(source["url"], os.path.join(PROBE_DIR, source["file"]))
            if data is None:
                print("  %s 取样失败：%s" % (source["label"], error))
        print("\n样本目录：%s" % os.path.relpath(PROBE_DIR, BASE_DIR))
    return 0 if any(row["ok"] for row in rows) else 1


SUBSCRIPT = {"\u2080": "0", "\u2081": "1", "\u2082": "2", "\u2083": "3", "\u2084": "4",
             "\u2085": "5", "\u2086": "6", "\u2087": "7", "\u2088": "8", "\u2089": "9",
             "\u00b2": "2", "\u00b3": "3", "\u207a": "+", "\u207b": "-"}


def ascii_formula(text):
    """把 Wikidata 那种带 Unicode 下标的分子式（H₂O）转成 ASCII（H2O）。

    引擎按 ASCII 数原子，带下标就一个都认不出来——实测 `水` 返回的就是 H₂O。
    """
    out = []
    for char in text or "":
        out.append(SUBSCRIPT.get(char, char))
    return "".join(out).replace(" ", "").strip()


def species_from_bindings(bindings):
    """把 SPARQL 结果整理成物种表；同一个 QID 的多条记录合并成一条。"""
    rows = {}
    order = []
    for item in bindings:
        qid = (item.get("item") or {}).get("value", "").rsplit("/", 1)[-1]
        if not qid:
            continue
        if qid not in rows:
            rows[qid] = {"id": qid, "name": "", "formula": "", "smiles": "", "cas": ""}
            order.append(qid)
        row = rows[qid]
        label = (item.get("itemLabel") or {}).get("value", "")
        lang = (item.get("itemLabel") or {}).get("xml:lang", "")
        if label and (not row["name"] or lang.startswith("zh")):
            row["name"] = label
        for key, field in (("formula", "formula"), ("smiles", "smiles"), ("cas", "cas")):
            value = (item.get(key) or {}).get("value", "")
            if value and not row[field]:
                row[field] = value
    out = []
    for qid in order:
        row = rows[qid]
        row["formula"] = ascii_formula(row["formula"])
        if row["formula"] or row["smiles"]:
            out.append(row)
    return out


def seed_query(cas_list):
    """把 CAS 列表拼成 VALUES 子句（分批发，一条 SPARQL 里塞太多也会被拒）。"""
    values = " ".join('"%s"' % item for item in cas_list)
    return WIKIDATA_SEED_QUERY % values


def run_query(query, log, tries=4):
    """跑一条 SPARQL，带 429/503/超时 重试。返回 bindings 列表（失败返回 None）。"""
    url = WIKIDATA_BASE + net.quote(query)
    waits = [20, 60, 120, 180]
    for attempt in range(tries):
        if attempt:
            log("  重试第 %d 次（等 %d 秒）" % (attempt, waits[min(attempt - 1, len(waits) - 1)]))
            time.sleep(waits[min(attempt - 1, len(waits) - 1)])
        data, error = net.drain(url, None)
        if data is None:
            log("  失败：%s" % error)
            continue
        try:
            payload = json.loads(data.decode("utf-8"))
        except ValueError as exc:
            log("  返回的不是 JSON：%s" % exc)
            continue
        return payload.get("results", {}).get("bindings", [])
    return None


def load_chem():
    """把 webui\\chem.py 当模块加载：规范式、繁简归一这些化学知识只有那一处出处。"""
    path = os.path.join(BASE_DIR, "webui", "chem.py")
    spec = importlib.util.spec_from_file_location("chem_rules", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["chem_rules"] = module
    spec.loader.exec_module(module)
    return module


def apply_transforms(species):
    """给物种表补上：教材写法的规范式 + 简体名。

    Wikidata 的 formula 串是它自己的元素排序（氢氧化钠给的是 HNaO、氯化铵是 ClH4N），
    界面上要显示化学式就不能直接用；中文标签也可能回退到繁体（碳酸鈣）。
    """
    chem = load_chem()
    rules = chem.load_rules()
    out = []
    for row in species:
        item = dict(row)
        item["formula_canonical"] = chem.canonical_formula(item.get("formula", ""))
        item["name"] = chem.simplify_chinese(rules, item.get("name", ""))
        out.append(item)
    return out


def write_species(out_path, species, source, log):
    species = apply_transforms(species)
    folder = os.path.dirname(out_path)
    if folder and not os.path.isdir(folder):
        os.makedirs(folder)
    payload = {
        "_note": "构建期导出（运行期不联网）。要更新就重跑 "
                 "scripts\\fetch-chem-data.py --seed / --export / --reformat",
        "source": source,
        "formula_note": "formula 是原始值（Wikidata 的元素排序）；"
                        "**显示用 formula_canonical**（教材写法：NaOH、NH4Cl）；name 已做繁简归一",
        "count": len(species),
        "species": species,
    }
    with io.open(out_path, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True))
        handle.write("\n")
    log("已写出 %s（%d 个物种，%s）"
        % (os.path.relpath(out_path, BASE_DIR), len(species), net.human(os.path.getsize(out_path))))
    return len(species)


def export_seed(log, out_path):
    """按 CAS 种子表取数：一条一条查，缺哪些号一眼能看出来。

    **可累积重跑**：输出文件里已有的 CAS 会跳过，所以被限流打断之后，再跑一次
    只补缺的那几个（WDQS 的 429 很常见，不能每次都从头打一遍）。
    """
    out_path = out_path or os.path.join(BASE_DIR, "webui", "chemdata", "chem-species.json")
    collected = {}
    if os.path.isfile(out_path):
        try:
            with io.open(out_path, encoding="utf-8") as handle:
                for row in json.load(handle).get("species") or []:
                    if row.get("cas"):
                        collected[row["cas"]] = row
            log("已有 %d 个物种，只补缺的" % len(collected))
        except (ValueError, OSError) as exc:
            log("旧文件读不了（从头来）：%s" % exc)
    todo = [item for item in SEED_CAS if item not in collected]
    if not todo:
        log("种子表已经齐了，不用再取")
        return write_species(out_path, [collected[key] for key in sorted(collected)],
                             "https://query.wikidata.org/ (CC0)，按 CAS 种子表点名", log)
    # 小批 + 长间隔：WDQS 对连续请求限得很死（一批 20 条时第二批就 429），
    # 每次请求之间留够时间，比"大批 + 硬重试"成功率高得多。
    batch = 8
    for start in range(0, len(todo), batch):
        chunk = todo[start:start + batch]
        bindings = run_query(seed_query(chunk), log)
        if bindings is None:
            log("第 %d 批失败，先留下已经拿到的" % (start // batch + 1))
            break
        rows = species_from_bindings(bindings)
        for row in rows:
            if row.get("cas"):
                collected[row["cas"]] = row
        log("第 %d 批：问到 %d 条（累计 %d）" % (start // batch + 1, len(rows), len(collected)))
        # 每批都落一次盘：429 打断也不会白干
        write_species(out_path, [collected[key] for key in sorted(collected)],
                      "https://query.wikidata.org/ (CC0)，按 CAS 种子表点名", log)
        if start + batch < len(todo):
            time.sleep(20.0)
    missing = [item for item in SEED_CAS if item not in collected]
    if missing:
        log("这些 CAS 号还没拿到（再跑一次 --seed 会只补它们）：%s" % ", ".join(missing))
    species = [collected[key] for key in sorted(collected)]
    return write_species(out_path, species, "https://query.wikidata.org/ (CC0)，按 CAS 种子表点名", log)


def export_wikidata(pages, log, out_path):
    """从 Wikidata 拉一批"有 CAS 号"的化合物（那批数据最规矩），写成物种表。

    分页 + 间隔：WDQS 对连续请求会 429，脚本里已经带了重试，这里再加 2 秒间隔。
    构建期联网、运行期离线——这个文件打完就随包走，教室里不再联网。
    """
    out_path = out_path or os.path.join(BASE_DIR, "webui", "chemdata", "chem-species.json")
    collected = {}
    waits = [15, 30, 45, 60]
    for page in range(pages):
        query = WIKIDATA_SPECIES_QUERY % (WIKIDATA_PAGE, page * WIKIDATA_PAGE)
        url = WIKIDATA_BASE + net.quote(query)
        data = None
        for attempt, wait in enumerate([0] + waits):
            if wait:
                log("  被限流，等 %d 秒再试（第 %d 次）" % (wait, attempt))
                time.sleep(wait)
            data, error = net.drain(url, None)
            if data is not None:
                break
            if "429" not in error and "503" not in error:
                log("第 %d 页失败：%s" % (page + 1, error))
                break
        if data is None:
            break
        payload = json.loads(data.decode("utf-8"))
        rows = species_from_bindings(payload.get("results", {}).get("bindings", []))
        for row in rows:
            collected[row["id"]] = row
        log("第 %d 页：%d 条（累计 %d）" % (page + 1, len(rows), len(collected)))
        time.sleep(3.0)
    species = [collected[key] for key in sorted(collected)]
    return write_species(out_path, species, "https://query.wikidata.org/ (CC0)，按 CAS 分页全表扫", log)


WEBBOOK_PAGE = "https://webbook.nist.gov/cgi/cbook.cgi?ID=C%s&Units=SI&Mask=%d"
WEBBOOK_MASKS = ((1, "gas"), (2, "condensed"))

def webbook_numbers(html):
    """从 WebBook 的 Mask 页里取出 {"gas"/"condensed": {"dHf":…, "S":…}}。

    页面表格每行形如（注意 Δ、f、H° 是**分开的单元格**，不能按"第一格就是标签"取）：
        ΔfH°gas   -241.826 ± 0.040   kJ/mol   Review   Cox, Wagman, et al., 1984   CODATA…
        S°gas,1 bar   188.835 ± 0.010   J/mol*K   Review   …
    所以把整行的文字拼起来再按"单位 + 前面那个数"抓。每格相态取第一条命中（CODATA 值在最前）。
    """
    out = {}
    for row in re.findall(r"<TR[^>]*>(.*?)</TR>", html, re.I | re.S):
        pieces = []
        for cell in re.findall(r"<T[DH][^>]*>(.*?)</T[DH]>", row, re.I | re.S):
            text = re.sub(r"<[^>]+>", " ", cell)
            text = (text.replace("&deg;", "\u00b0").replace("&#916;", "\u0394")
                        .replace("&plusmn;", "\u00b1").replace("&nbsp;", " "))
            pieces.append(text)
        line = " ".join(" ".join(pieces).split())
        if not line:
            continue
        lowered = line.lower()
        if "gas" in lowered:
            phase = "gas"
        elif "liquid" in lowered:
            phase = "liquid"
        elif "solid" in lowered or "crystalline" in lowered:
            phase = "solid"
        else:
            continue
        squeezed = line.replace(" ", "")
        # 判据必须收到"**行首**就是 ΔfH°/S°"：甲烷那种页面里，燃烧焓行（ΔcH°）的备注写着
        # "Corresponding ΔfHº gas = -74.48 kJ/mol"，宽松匹配会把它整行当成 ΔfH° 行，
        # 再取第一个数字就抓到了燃烧焓 -890.16（真踩过）。
        #
        # 数字后面**可以有小数点但没小数位**：WebBook 在不确定度较大的时候就写成
        # "-234. ± 2."（乙醇的 ΔfH°gas 就是这样），只认 "\d+\.\d+" 会整条漏掉——
        # 这就是"乙醇没有 ΔfH°、于是酯化反应算不出 K"的根因，所以这里允许 "\d+\."。
        if "kJ/mol" in line and (squeezed.startswith("\u0394fH") or squeezed.startswith("\u0394Hf")):
            found = re.search(r"(-?\d+(?:\.\d*)?)\s*(?:\u00b1\s*[\d.]+)?\s*kJ/mol", line)
            # 第一条命中就锁定：WebBook 把 CODATA 评价值排在前面，后面是逐年文献值
            # （甲烷那页有 5 条 ΔfH° 行，取最后一条会拿到 1932 年的老数据）
            if found and "dHf" not in out.setdefault(phase, {}):
                out[phase]["dHf"] = float(found.group(1))
        if "J/mol*K" in line and squeezed.startswith("S\u00b0"):
            found = re.search(r"(-?\d+(?:\.\d*)?)\s*(?:\u00b1\s*[\d.]+)?\s*J/mol\*K", line)
            if found and "S" not in out.setdefault(phase, {}):
                out[phase]["S"] = float(found.group(1))
    return out


def export_thermo(log, out_path):
    """给物种表里的每个物种补热化学数据（298.15 K 的 ΔfH° 与 S°）。

    数据源 NIST WebBook（免费公开），按 **CAS 号**取——URL 是确定性的，不用猜编号。
    相态分开存：水的液态 ΔfH°=-285.83、气态=-241.83，差 44 kJ/mol，混用就错了。

    ΔfG° 这里**不存**：它要由 ΔfH° 和 S°（含单质基准态）现算，见 chem.py 的 reaction_energy()。
    """
    out_path = out_path or os.path.join(BASE_DIR, "webui", "chemdata", "chem-thermo.json")
    species = []
    species_path = os.path.join(BASE_DIR, "webui", "chemdata", "chem-species.json")
    if os.path.isfile(species_path):
        with io.open(species_path, encoding="utf-8") as handle:
            species = json.load(handle).get("species") or []
    if not species:
        log("先跑 --seed 拿到物种表，再做热化学")
        return 0
    # 旧表先读进来：某个物种这次没取到值（页面改了、网络抖了）就用旧值兜底，
    # 一次重跑不该把上一轮已经拿到的数据弄丢。
    previous = {}
    if os.path.isfile(out_path):
        try:
            with io.open(out_path, encoding="utf-8") as handle:
                for row in json.load(handle).get("rows") or []:
                    previous[(row.get("cas"), row.get("phase"))] = row
        except (OSError, ValueError) as exc:
            log("旧的热化学表读不了（就按新取的来）：%s" % exc)
    log("要补热化学的物种：%d 个" % len(species))
    rows = []
    for item in species:
        cas = (item.get("cas") or "").replace("-", "")
        if not cas:
            continue
        merged = {}
        for mask, _label in WEBBOOK_MASKS:
            url = WEBBOOK_PAGE % (cas, mask)
            data, error = net.drain(url, None, timeout=60)
            if data is None:
                log("  %s mask=%d 取不到：%s" % (item.get("name", cas), mask, error))
                continue
            found = webbook_numbers(data.decode("utf-8", "replace"))
            for phase, values in found.items():
                merged.setdefault(phase, {}).update(values)
            time.sleep(0.8)
        if not merged:
            log("  %-12s 没有热化学数据（WebBook 里没收录）" % item.get("name", cas))
            continue
        for phase, values in sorted(merged.items()):
            old = previous.get((item.get("cas", ""), phase)) or {}
            d_hf = values.get("dHf")
            entropy = values.get("S")
            if d_hf is None and old.get("dHf") is not None:
                d_hf = old.get("dHf")
                log("  %s %s 这次没取到 ΔfH°，沿用上一轮的 %s" % (item.get("name", ""), phase, d_hf))
            if entropy is None and old.get("S") is not None:
                entropy = old.get("S")
                log("  %s %s 这次没取到 S°，沿用上一轮的 %s" % (item.get("name", ""), phase, entropy))
            rows.append({"cas": item.get("cas", ""), "name": item.get("name", ""),
                         "formula": item.get("formula_canonical") or item.get("formula", ""),
                         "phase": phase, "dHf": d_hf, "S": entropy})
            log("  %-12s %-10s ΔfH°=%s kJ/mol  S°=%s J/(mol·K)"
                % (item.get("name", ""), phase, d_hf, entropy))
    folder = os.path.dirname(out_path)
    if folder and not os.path.isdir(folder):
        os.makedirs(folder)
    payload = {
        "_note": "298.15 K 标准热力学数据，构建期从 NIST WebBook 导出（按 CAS 取）；运行期不联网。"
                 "要更新就重跑 scripts\\fetch-chem-data.py --thermo。"
                 "ΔfG° 不在这里，由 chem.py 用 ΔfH° 与 S°（含单质基准态）现算。",
        "source": "https://webbook.nist.gov/chemistry/ (NIST Chemistry WebBook, SRD 69)",
        "units": {"dHf": "kJ/mol", "S": "J/(mol*K)"},
        "count": len(rows),
        "rows": rows,
    }
    with io.open(out_path, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True))
        handle.write("\n")
    log("已写出 %s（%d 条，%s）" % (os.path.relpath(out_path, BASE_DIR), len(rows),
                                    net.human(os.path.getsize(out_path))))
    return len(rows)


CHEBI_OBO_URL = "https://ftp.ebi.ac.uk/pub/databases/chebi/ontology/chebi.obo"
CHEBI_CACHE = os.path.join(BASE_DIR, "vendor", "chebi.obo")


def obo_quoted(line):
    """从 `property_value: formula "C2H6O" xsd:string` 里取引号里那段。"""
    first = line.find('"')
    if first < 0:
        return ""
    second = line.find('"', first + 1)
    return line[first + 1:second if second > 0 else len(line)].strip()


def obo_property(line):
    """取 `property_value: <名字> "值" ...` 里的 (名字, 值)。

    名字可能是带命名空间的：ChEBI 255 用的是 `chemrof:generalized_empirical_formula`
    和 `chemrof:smiles_string`，老版本才是裸的 `formula` / `smiles`。
    所以下面按**后缀**判断，不写死前缀（写死了就会一条都收不到——真踩过）。
    """
    body = line[len("property_value:"):].strip()
    parts = body.split(None, 1)
    if not parts:
        return "", ""
    return parts[0].strip().lower(), obo_quoted(body)


def chebi_parse(path, log):
    """流式解析 ChEBI 的 OBO：取有分子式或 SMILES 的条目，跳过 obsolete。

    258 MB 的文本不能整个读进内存（教室开发机也就 4G），所以按行流式读。
    """
    rows = []
    current = None
    skipped_obsolete = 0

    def flush(item):
        if not item:
            return
        if item.get("obsolete"):
            return
        if not item.get("formula") and not item.get("smiles"):
            return
        if not item.get("name"):
            return
        rows.append([item.get("chebi", ""), item["name"],
                     item.get("formula", ""), item.get("smiles", "")])

    with io.open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.rstrip("\r\n")
            if line.startswith("["):
                flush(current)
                current = {} if line.strip() == "[Term]" else None
                continue
            if current is None:
                continue
            if line.startswith("id: "):
                current["chebi"] = line[4:].strip()
            elif line.startswith("name: "):
                current["name"] = line[6:].strip()
            elif line.startswith("is_obsolete: true"):
                current["obsolete"] = True
                skipped_obsolete += 1
            elif line.startswith("property_value:"):
                key, value = obo_property(line)
                if key.endswith("formula") and not current.get("formula"):
                    current["formula"] = value
                elif "smiles" in key and not current.get("smiles"):
                    current["smiles"] = value
    flush(current)
    log("  解析出 %d 条（跳过 obsolete %d 条）" % (len(rows), skipped_obsolete))
    return rows


def export_structures(log, out_path):
    """ChEBI 批量结构 -> webui\\chemdata\\chem-structures.json（给有机化学与球棍模型用）。

    存成"字段名 + 数组行"的紧凑格式：十几万条按对象写会大一倍。
    """
    out_path = out_path or os.path.join(BASE_DIR, "webui", "chemdata", "chem-structures.json")
    folder = os.path.dirname(CHEBI_CACHE)
    if folder and not os.path.isdir(folder):
        os.makedirs(folder)
    if not os.path.isfile(CHEBI_CACHE) or os.path.getsize(CHEBI_CACHE) < 1000:
        log("下载 ChEBI 本体（约 258 MB，只下一次，缓存在 vendor\\chebi.obo）……")
        ok, message = net.fetch_to_file(CHEBI_OBO_URL, CHEBI_CACHE, timeout=180)
        log("  %s" % message)
        if not ok:
            return 0
    else:
        log("用缓存 %s（%s）" % (os.path.relpath(CHEBI_CACHE, BASE_DIR),
                                 net.human(os.path.getsize(CHEBI_CACHE))))
    rows = chebi_parse(CHEBI_CACHE, log)
    if not rows:
        log("一条都没解析出来，OBO 格式可能变了")
        return 0
    payload = {
        "_note": "ChEBI 化合物结构（构建期导出，运行期离线）。fields 与 rows 一一对应；"
                 "只收有分子式或 SMILES 的条目。要更新就重跑 scripts\\fetch-chem-data.py --structures",
        "source": "https://www.ebi.ac.uk/chebi/ (ChEBI，CC BY 4.0)",
        "fields": ["chebi", "name", "formula", "smiles"],
        "count": len(rows),
        "rows": rows,
    }
    folder = os.path.dirname(out_path)
    if folder and not os.path.isdir(folder):
        os.makedirs(folder)
    with io.open(out_path, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        handle.write("\n")
    log("已写出 %s（%d 条，%s）" % (os.path.relpath(out_path, BASE_DIR), len(rows),
                                    net.human(os.path.getsize(out_path))))
    return len(rows)


def reformat(out_path, log):
    """离线重排：把已有的物种表过一遍规范式/繁简，**不联网**。

    改的是"怎么显示"（教材写法、简体），不是数据来源，所以不需要重新拉一遍。
    """
    out_path = out_path or os.path.join(BASE_DIR, "webui", "chemdata", "chem-species.json")
    if not os.path.isfile(out_path):
        log("还没有 %s，先跑 --seed" % out_path)
        return 0
    with io.open(out_path, encoding="utf-8") as handle:
        payload = json.load(handle)
    rows = payload.get("species") or []
    log("读入 %d 条，重排中（不联网）" % len(rows))
    return write_species(out_path, rows, payload.get("source", "本地重排"), log)


def main(argv):
    parser = argparse.ArgumentParser(description="化学数据取数（构建期联网）")
    parser.add_argument("--probe", action="store_true", help="只探测通道")
    parser.add_argument("--sample", action="store_true", help="探测并把样本存下来")
    parser.add_argument("--export", action="store_true", help="导出物种表（默认 webui\\chemdata\\chem-species.json）")
    parser.add_argument("--seed", action="store_true",
                        help="按 CAS 种子表导出（快、稳；推荐先跑这个）")
    parser.add_argument("--reformat", action="store_true",
                        help="离线重排已有的物种表（补规范式、繁简归一，不联网）")
    parser.add_argument("--thermo", action="store_true",
                        help="给物种表补热化学（NIST-JANAF 的 ΔfH°/ΔfG°/S°/Cp，298.15 K）")
    parser.add_argument("--structures", action="store_true",
                        help="拉 ChEBI 本体（258 MB）解析出批量结构，写 webui\\chemdata\\chem-structures.json")
    parser.add_argument("--pages", type=int, default=3, help="导出几页（每页 %d 条）" % WIKIDATA_PAGE)
    parser.add_argument("--out", default="", help="导出到指定路径")
    args = parser.parse_args()

    def log(text):
        sys.stdout.write(text + "\n")
        sys.stdout.flush()

    if args.probe or args.sample:
        return do_probe(args.sample)
    if args.reformat:
        count = reformat(args.out, log)
        return 0 if count > 0 else 1
    if args.structures:
        count = export_structures(log, args.out)
        return 0 if count > 0 else 1
    if args.thermo:
        count = export_thermo(log, args.out)
        return 0 if count > 0 else 1
    if args.seed:
        count = export_seed(log, args.out)
        return 0 if count > 0 else 1
    if args.export:
        count = export_wikidata(max(1, args.pages), log, args.out)
        return 0 if count > 0 else 1
    parser.print_help()
    print("\n用法：--probe 探通道 / --sample 存样本 / --seed 按 CAS 种子表出物种表 / "
          "--export 全表扫 / --reformat 离线重排")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
