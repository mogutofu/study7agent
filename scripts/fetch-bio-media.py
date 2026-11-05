# -*- coding: utf-8 -*-
"""给生物模块补插图：优先 Wikimedia Commons 的**动画 GIF**，没有就退一步找静态插图。

判断"是不是真动画"：GIF 里每个帧都带一个图形控制扩展（0x21 0xF9 0x04），数一下就知道。
下载结果写进 webui/ui/vendor/bio/manifest.json + README.txt（署名与来源，CC BY-SA 要求）。

用法：
    .\\python\\python.exe scripts\\fetch-bio-media.py                # 只补缺的
    .\\python\\python.exe scripts\\fetch-bio-media.py --only key1,key2
    .\\python\\python.exe scripts\\fetch-bio-media.py --force        # 已有的也重下
"""

import argparse
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "webui", "ui", "vendor", "bio")
API = "https://commons.wikimedia.org/w/api.php"
UA = "Study7Agent/1.1 (classroom teaching aid; local teacher use)"

MAX_GIF = 6 * 1024 * 1024
MAX_IMG = 2 * 1024 * 1024

# 过程 key -> (中文名, 搜索词)。先按顺序找动画 GIF，都没有再找静态插图。
WANTED = [
    ("mitosis", "有丝分裂", ["mitosis animation", "mitosis cell division"]),
    ("meiosis", "减数分裂", ["meiosis animation", "meiosis cell division"]),
    ("mitosis_curve", "有丝分裂数目变化", ["chromosome segregation", "mitosis stages"]),
    ("meiosis_curve", "减数分裂数目变化", ["meiosis stages", "meiosis diagram"]),
    ("dna_structure", "DNA 双螺旋", ["DNA double helix", "DNA structure"]),
    ("dna_replication", "DNA 复制", ["DNA replication animation", "DNA replication"]),
    ("transcription", "转录", ["transcription animation", "RNA polymerase"]),
    ("translation", "翻译", ["translation ribosome", "protein biosynthesis"]),
    ("gene_mutation", "基因突变", ["point mutation", "gene mutation diagram"]),
    ("chromosome_variation", "染色体结构变异", ["chromosomal mutation", "chromosome deletion"]),
    ("monohybrid", "一对相对性状杂交", ["punnett square", "monohybrid cross"]),
    ("dihybrid", "两对相对性状杂交", ["dihybrid cross", "punnett square dihybrid"]),
    ("segregation_law", "分离定律", ["law of segregation", "meiosis segregation"]),
    ("independent_assortment", "自由组合定律", ["independent assortment", "random assortment"]),
    ("pedigree", "遗传系谱图", ["pedigree chart", "genetic pedigree"]),
    ("phage_experiment", "噬菌体侵染", ["bacteriophage", "phage infection"]),
    ("natural_selection_freq", "自然选择", ["natural selection", "peppered moth"]),
    ("dehydration", "脱水缩合", ["peptide bond formation", "condensation reaction amino acid"]),
    ("protein_structure", "蛋白质结构", ["protein structure levels", "protein folding"]),
    ("membrane_model", "细胞膜", ["cell membrane", "phospholipid bilayer"]),
    ("secretory_path", "分泌蛋白运输", ["protein secretion pathway", "endoplasmic reticulum golgi"]),
    ("osmosis", "渗透作用", ["osmosis animation", "osmosis"]),
    ("plasmolysis", "质壁分离", ["plasmolysis", "turgor pressure"]),
    ("active_transport", "主动运输", ["active transport", "sodium potassium pump"]),
    ("enzyme_barrier", "酶降低活化能", ["activation energy enzyme", "enzyme catalysis"]),
    ("enzyme_conditions", "酶的温度与pH", ["enzyme activity temperature", "enzyme kinetics"]),
    ("atp_adp", "ATP 与 ADP", ["ATP synthase", "ATP cycle"]),
    ("aerobic_respiration", "有氧呼吸", ["cellular respiration", "Krebs cycle"]),
    ("light_reaction", "光反应", ["photosynthesis light reaction", "photosystem"]),
    ("calvin_cycle", "暗反应", ["Calvin cycle", "carbon fixation"]),
    ("cell_differentiation", "细胞分化", ["cell differentiation", "stem cell differentiation"]),
    ("internal_environment", "内环境", ["blood plasma tissue fluid", "interstitial fluid"]),
    ("resting_potential", "静息电位", ["resting potential", "membrane potential"]),
    ("action_potential", "动作电位", ["action potential", "nerve impulse"]),
    ("potential_curve", "电位变化曲线", ["action potential graph", "membrane potential graph"]),
    ("nerve_conduction", "兴奋传导", ["nerve conduction", "axon potential"]),
    ("synapse", "突触传递", ["synapse", "synaptic transmission"]),
    ("blood_sugar", "血糖平衡", ["blood glucose regulation", "insulin glucagon"]),
    ("thyroid_axis", "甲状腺激素调节", ["thyroid hormone regulation", "HPT axis"]),
    ("humoral_immunity", "体液免疫", ["antibody immune response", "B cell antibody"]),
    ("cellular_immunity", "细胞免疫", ["cytotoxic T cell", "T cell immune"]),
    ("auxin", "生长素两重性", ["auxin", "plant hormone auxin"]),
    ("population_growth", "种群增长", ["population growth curve", "bacterial growth curve"]),
    ("species_relation", "种间关系", ["predator prey", "symbiosis"]),
    ("energy_flow", "能量流动", ["food chain", "energy pyramid"]),
    ("carbon_cycle", "碳循环", ["carbon cycle", "carbon cycle diagram"]),
    ("tissue_culture", "植物组织培养", ["plant tissue culture", "callus culture"]),
    ("protoplast_fusion", "植物体细胞杂交", ["protoplast fusion", "somatic hybridization"]),
    ("monoclonal_antibody", "单克隆抗体", ["monoclonal antibody", "hybridoma"]),
    ("nuclear_transfer", "核移植", ["nuclear transfer", "cloning somatic cell"]),
    ("restriction_enzyme", "限制酶", ["restriction enzyme", "restriction digest"]),
    ("pcr", "PCR", ["polymerase chain reaction", "PCR"]),
    ("expression_vector", "基因表达载体", ["plasmid vector", "gene cloning vector"]),
    ("gene_transfer", "目的基因导入", ["gene transfer", "Agrobacterium transformation"]),
    # 不在 54 个过程里、但课本常配的图（有就存着备用）
    ("muscle", "肌肉收缩", ["muscle contraction", "sarcomere"]),
    ("heart", "心脏与血液循环", ["heart pumping", "blood circulation"]),
    ("stomata", "气孔", ["stomata", "guard cell"]),
]

QUALITY = {"image/gif": MAX_GIF, "image/png": MAX_IMG, "image/jpeg": MAX_IMG}
MIN_WIDTH = 160


def api(params, tries=3):
    query = urllib.parse.urlencode(params)
    last = None
    for attempt in range(tries):
        request = urllib.request.Request(API + "?" + query, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(request, timeout=45) as answer:
                return json.loads(answer.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            last = error
            if error.code == 429:
                time.sleep(15 * (attempt + 1))
                continue
            raise
        except Exception as error:                       # 网络抖动也重试
            last = error
            time.sleep(5)
    raise last


def search(term, mime):
    time.sleep(4.0)                    # 问太密会被 Commons 按 IP 限流（吃过一次 429）
    try:
        data = api({"action": "query", "format": "json", "generator": "search",
                    "gsrsearch": "filemime:%s %s" % (mime, term), "gsrnamespace": "6",
                    "gsrlimit": "8", "prop": "imageinfo",
                    "iiprop": "url|size|mime|extmetadata"})
    except Exception as error:
        sys.stderr.write("搜索失败（%s / %s）：%s\n" % (term, mime, error))
        return []
    pages = (data.get("query") or {}).get("pages") or {}
    rows = []
    for page in pages.values():
        info = (page.get("imageinfo") or [{}])[0]
        rows.append({"title": page.get("title") or "", "index": page.get("index") or 99,
                     "url": info.get("url"), "mime": info.get("mime"),
                     "size": info.get("size") or 0, "width": info.get("width") or 0,
                     "meta": info.get("extmetadata") or {}})
    rows.sort(key=lambda row: row["index"])
    return rows


def pick(rows, animated_only):
    good = []
    for row in rows:
        limit = QUALITY.get(row["mime"])
        if not limit or not row["url"]:
            continue
        if row["size"] > limit or row["width"] < MIN_WIDTH:
            continue
        good.append(row)
    if animated_only:
        good = [row for row in good if row["mime"] == "image/gif"]
    if not good:
        return None
    good.sort(key=lambda row: (0 if re.search(r"anim|cycle|division|transport",
                                              row["title"], re.I) else 1,
                               abs(row["size"] - 350 * 1024)))
    return good[0]


def frames(blob):
    """GIF 帧数（每个帧一个图形控制扩展）。"""
    return blob.count(b"\x21\xf9\x04")


def plain(value):
    text = re.sub(r"<[^>]+>", "", str(value or ""))
    return re.sub(r"\s+", " ", text).strip()


def download(url, path):
    request = urllib.request.Request(url.split("?")[0], headers={"User-Agent": UA})
    with urllib.request.urlopen(request, timeout=180) as answer:
        blob = answer.read()
    with io.open(path, "wb") as handle:
        handle.write(blob)
    return blob


def write_readme(manifest):
    lines = ["生物模块的插图来源与授权", "=" * 30, "",
             "全部来自 Wikimedia Commons（动画 GIF 优先，没有动画的用静态插图），随包内置、离线可用。",
             "授权以各文件自己的为准（多为公有领域 / CC BY / CC BY-SA），下表逐条列出作者与来源。",
             "使用时请保留本文件；CC BY-SA 的文件若再分发，需同样以 CC BY-SA 提供。", ""]
    for key in sorted(manifest):
        info = manifest[key]
        tag = "动画" if info.get("animated") else "静态"
        lines.append("- %s（%s，%s）：%s" % (info["name"], info["file"], tag, info["title"]))
        lines.append("    作者：%s" % info["author"])
        lines.append("    授权：%s" % info["license"])
        lines.append("    来源：%s" % info["source"])
    lines.append("")
    lines.append("共 %d 个插图，其中动画 %d 个。没有插图的过程用程序画的仿真场景。"
                 % (len(manifest), sum(1 for v in manifest.values() if v.get("animated"))))
    with io.open(os.path.join(OUT, "README.txt"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))


def main():
    parser = argparse.ArgumentParser(description="下载生物模块的插图")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--only", default="")
    args = parser.parse_args()
    if not os.path.isdir(OUT):
        os.makedirs(OUT)
    manifest_path = os.path.join(OUT, "manifest.json")
    manifest = {}
    if os.path.isfile(manifest_path):
        with io.open(manifest_path, encoding="utf-8") as handle:
            manifest = json.load(handle)

    only = [item.strip() for item in args.only.split(",") if item.strip()]
    made, kept, missed = [], [], []
    for key, name, terms in WANTED:
        if only and key not in only:
            continue
        if key in manifest and not args.force:
            kept.append(key)
            continue
        row = None
        for term in terms:                                 # 先找动画 GIF
            row = pick(search(term, "image/gif"), animated_only=True)
            if row:
                break
        if not row:                                        # 再退一步找静态插图
            for term in terms:
                row = pick(search(term, "image/png"), animated_only=False)
                if row:
                    break
        if not row:
            missed.append("%s（%s）" % (key, name))
            continue
        suffix = {"image/gif": ".gif", "image/png": ".png", "image/jpeg": ".jpg"}[row["mime"]]
        path = os.path.join(OUT, key + suffix)
        try:
            blob = download(row["url"], path)
        except Exception as error:
            missed.append("%s（%s：%s）" % (key, name, error))
            continue
        animated = row["mime"] == "image/gif" and frames(blob) > 1
        if row["mime"] == "image/gif" and not animated:
            os.remove(path)                                # 单帧 GIF 不如 PNG，丢掉
            missed.append("%s（%s：只找到单帧）" % (key, name))
            continue
        meta = row["meta"]
        manifest[key] = {
            "file": key + suffix, "name": name, "bytes": len(blob), "animated": animated,
            "title": plain(row["title"]),
            "author": plain((meta.get("Artist") or {}).get("value")) or "见来源页面",
            "license": plain((meta.get("LicenseShortName") or {}).get("value")) or "见来源页面",
            "source": (meta.get("DescriptionUrl") or {}).get("value") or row["url"],
        }
        made.append("%s %s -> %s（%.2f MB）" % (key, name, suffix, len(blob) / 1048576.0))

    with io.open(manifest_path, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(manifest, ensure_ascii=False, indent=1, sort_keys=True))
    write_readme(manifest)
    print("新下：%d 个（已有 %d 个）" % (len(made), len(kept)))
    for row in made:
        print("  + " + row)
    if missed:
        print("还没找到（这些用程序画的场景）：%d 个" % len(missed))
        for row in missed:
            print("  - " + row)
    print("清单：%s" % manifest_path)


if __name__ == "__main__":
    main()
