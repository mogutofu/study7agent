# -*- coding: utf-8 -*-
"""批量补插图（快版）。

慢的教训：一次 500 个文件还带 extmetadata（整包元数据）-> 响应几 MB，卡在网络上。
现在分三步，都很轻：
  1) 分类里只拉 url|size|mime（几十 KB），一次 500 个；
  2) 本地按关键词挑出要的文件；
  3) 并行下载（upload.wikimedia.org 直链），授权信息只对选中的那几十个文件一次性批量取。
"""
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "webui", "ui", "vendor", "bio")
API = "https://commons.wikimedia.org/w/api.php"
UA = "Study7Agent/1.1 (classroom teaching aid; local teacher use)"

sys.path.insert(0, HERE)
import importlib.util  # noqa: E402

spec = importlib.util.spec_from_file_location("fbio", os.path.join(HERE, "fetch-bio-media.py"))
fbio = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fbio)

CATEGORIES = [
    "Category:Animations of molecular biology", "Category:Animations of cell biology",
    "Category:Cell cycle", "Category:Mitosis", "Category:Meiosis",
    "Category:DNA replication", "Category:Transcription (genetics)",
    "Category:Translation (biology)", "Category:Photosynthesis",
    "Category:Cellular respiration", "Category:Enzymes", "Category:Immunology",
    "Category:Neurophysiology", "Category:Plant physiology", "Category:Genetics",
    "Category:Biotechnology", "Category:Polymerase chain reaction",
    "Category:Ecology diagrams", "Category:Population ecology",
    "Category:Cell diagrams", "Category:Molecular biology diagrams",
]


def call(params, tries=2):
    query = urllib.parse.urlencode(params)
    for attempt in range(tries):
        request = urllib.request.Request(API + "?" + query, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(request, timeout=60) as answer:
                return json.loads(answer.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            if error.code == 429:
                time.sleep(8 * (attempt + 1))
                continue
            raise
        except Exception:
            time.sleep(3)
    return {}


def harvest():
    # 池子落盘缓存：限流让每次只能拉回几个分类，缓存起来才能一轮轮攒（只增不减）
    path = os.path.join(OUT, "pool.json")
    pool = {}
    if os.path.isfile(path):
        with io.open(path, encoding="utf-8") as handle:
            pool = json.load(handle)
    for title in CATEGORIES:
        data = call({"action": "query", "format": "json", "generator": "categorymembers",
                     "gcmtitle": title, "gcmtype": "file", "gcmlimit": "500",
                     "prop": "imageinfo", "iiprop": "url|size|mime"})
        for page in ((data.get("query") or {}).get("pages") or {}).values():
            info = (page.get("imageinfo") or [{}])[0]
            mime = info.get("mime")
            if mime not in ("image/gif", "image/png", "image/jpeg"):
                continue
            if info.get("size", 0) > fbio.QUALITY.get(mime, 0):
                continue
            if (info.get("width") or 0) < fbio.MIN_WIDTH:
                continue
            pool[page.get("title")] = {"title": page.get("title"), "url": info.get("url"),
                                       "mime": mime, "size": info.get("size") or 0}
        print("  %-46s 池子里 %d" % (title[9:], len(pool)))
        time.sleep(0.4)
    with io.open(path, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(pool, ensure_ascii=False))
    return pool


def credit(titles):
    """一次性取授权信息（每批 40 个标题）。"""
    info = {}
    titles = list(titles)
    for start in range(0, len(titles), 40):
        batch = titles[start:start + 40]
        data = call({"action": "query", "format": "json", "titles": "|".join(batch),
                     "prop": "imageinfo", "iiprop": "extmetadata|url"})
        for page in ((data.get("query") or {}).get("pages") or {}).values():
            meta = ((page.get("imageinfo") or [{}])[0].get("extmetadata") or {})
            info[page.get("title")] = meta
    return info


def words(text):
    return set(re.findall(r"[a-z]{4,}", str(text).lower()))


def main():
    manifest_path = os.path.join(OUT, "manifest.json")
    manifest = {}
    if os.path.isfile(manifest_path):
        with io.open(manifest_path, encoding="utf-8") as handle:
            manifest = json.load(handle)
    todo = [(key, name, terms) for key, name, terms in fbio.WANTED if key not in manifest]
    print("还缺 %d 个，开始拉分类" % len(todo))
    pool = harvest()
    print("候选池 %d 个文件" % len(pool))

    chosen = {}
    missed = []
    for key, name, terms in todo:
        want = set()
        for term in terms:
            want |= words(term)
        best, best_score = None, 0
        for row in pool.values():
            hit = len(want & words(row["title"]))
            if hit < 1:                                  # 放宽：一个主题词命中就收
                continue
            score = hit + (2 if row["mime"] == "image/gif" else 0)
            if score > best_score:
                best, best_score = row, score
        if best:
            chosen[key] = (name, best)
        else:
            missed.append("%s（%s）" % (key, name))

    metas = credit([row["title"] for _name, row in chosen.values()]) if chosen else {}
    print("挑中 %d 个，开始并行下载" % len(chosen))

    def grab(item):
        key, (name, row) = item
        suffix = {"image/gif": ".gif", "image/png": ".png", "image/jpeg": ".jpg"}[row["mime"]]
        path = os.path.join(OUT, key + suffix)
        try:
            blob = fbio.download(row["url"], path)
        except Exception as error:
            return key, name, None, str(error)
        animated = row["mime"] == "image/gif" and fbio.frames(blob) > 1
        if row["mime"] == "image/gif" and not animated:
            os.remove(path)
            return key, name, None, "单帧"
        meta = metas.get(row["title"], {})
        manifest[key] = {
            "file": key + suffix, "name": name, "bytes": len(blob), "animated": animated,
            "title": fbio.plain(row["title"]),
            "author": fbio.plain((meta.get("Artist") or {}).get("value")) or "见来源页面",
            "license": fbio.plain((meta.get("LicenseShortName") or {}).get("value")) or "见来源页面",
            "source": (meta.get("DescriptionUrl") or {}).get("value") or row["url"],
        }
        return key, name, suffix + ("（动画）" if animated else ""), None

    made = []
    with ThreadPoolExecutor(max_workers=6) as pool_exec:
        for key, name, info, error in pool_exec.map(grab, list(chosen.items())):
            if error:
                missed.append("%s（%s：%s）" % (key, name, error))
            else:
                made.append("%s %s -> %s" % (key, name, info))

    with io.open(manifest_path, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(manifest, ensure_ascii=False, indent=1, sort_keys=True))
    fbio.write_readme(manifest)
    print("新下 %d 个，清单共 %d 个" % (len(made), len(manifest)))
    for row in sorted(made):
        print("  + " + row)
    print("还没找到 %d 个：" % len(missed))
    for row in sorted(missed):
        print("  - " + row)


if __name__ == "__main__":
    main()
