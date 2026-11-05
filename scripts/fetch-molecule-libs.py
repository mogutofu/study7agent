# -*- coding: utf-8 -*-
"""构建期取第三方 JS 库（运行期不联网）。

为什么要单独一个脚本：项目不许 pip、不许构建步骤，前端只能"把别人编好的单文件拿进来"。
所以要**记清 URL 与 SHA-256**，重跑可校验、可复现（和 fetch-chromium.py / fetch-chem-data.py 一个套路）。

取到哪：`webui\\ui\\vendor\\`——必须放在 `webui\\` 下面，打包规则（INCLUDE_DIRS）只带
webui / skills / docs / python，放 `vendor\\`（构建缓存目录）就进不了包、老师那边一开就白屏。

跑法：
    .\\python\\python.exe scripts\\fetch-molecule-libs.py            # 缺什么取什么
    .\\python\\python.exe scripts\\fetch-molecule-libs.py --check    # 只校验已有的文件
"""

import argparse
import hashlib
import io
import json
import os
import sys
import urllib.request

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(BASE_DIR, "webui", "ui", "vendor")

#: 第三方库：URL / 文件名 / 许可证 / 用途。SHA-256 由脚本自己第一次下载时算出来填进去。
#:
#: 为什么**没有** SmilesDrawer（原来打算用它画 2D 结构式）：取回来实测发现三件事，
#: 不如自己写（2D/3D/官能团都要同一张原子-键图）：
#:   1) 它的 draw() 签名和文档不一致：2.1.7 是 draw(data, target, theme, weights, infoOnly)，
#:      照文档传第 4 个参数会内部空指针（determineDimensions of null）；
#:   2) 画到 canvas 是**异步**的（内部走 SVG -> Image），画完立刻取像素是空的——
#:      自检和截图都抓不住，老师那边也可能闪一下才出现；
#:   3) 产物用了 `??` 与 `**`，与"Win7 老内核也能跑我们的代码"这条冲突（门禁本来会拦）。
LIBS = ()


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(url):
    request = urllib.request.Request(url, headers={"User-Agent": "Study7Agent-build"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


#: 整包取（npm 上的目录结构要原样保留：GWT 的加载器按相对路径找 .cache.js）
PACKAGES = (
    {
        "name": "jsme-editor",
        "version": "2024.4.29",
        "dir": "jsme",
        "license": "BSD-3-Clause",
        "home": "https://jsmol.sourceforge.net/ 与 https://pypi.org/project/jsme-editor/ 同源的 JSME 分子编辑器",
        "use": "让学生/老师自己画分子（化学面板的「自己画」），画完导出 SMILES",
    },
)


def package_files(package):
    """问 jsDelivr 要这个包的文件清单（构建期一次，运行期不联网）。"""
    url = "https://data.jsdelivr.com/v1/packages/npm/%s@%s?structure=flat" % (
        package["name"], package["version"])
    payload = json.loads(download(url).decode("utf-8"))
    return [item for item in payload.get("files") or [] if item.get("name")]


def fetch_package(package, check_only=False):
    target = os.path.join(OUT_DIR, package["dir"])
    print("  包 %s@%s -> %s" % (package["name"], package["version"],
                                os.path.relpath(target, BASE_DIR)))
    try:
        files = package_files(package)
    except Exception as exc:                      # noqa: BLE001
        print("    取文件清单失败：%s" % exc)
        return False
    manifest = {}
    manifest_path = os.path.join(target, "manifest.json")
    if os.path.isfile(manifest_path):
        try:
            with io.open(manifest_path, encoding="utf-8") as handle:
                manifest = json.load(handle)
        except (OSError, ValueError):
            manifest = {}
    ok = True
    done = 0
    for item in files:
        name = item["name"].lstrip("/")
        if name.endswith(".map"):
            continue                              # source map 用不上，别白占体积
        path = os.path.join(target, name.replace("/", os.sep))
        recorded = (manifest.get("files") or {}).get(name)
        if recorded and os.path.isfile(path) and sha256_of(path) == recorded:
            done += 1
            continue
        if check_only:
            print("    缺/不匹配：%s" % name)
            ok = False
            continue
        url = "https://cdn.jsdelivr.net/npm/%s@%s/%s" % (package["name"],
                                                         package["version"], name)
        try:
            data = download(url)
        except Exception as exc:                  # noqa: BLE001
            print("    %s 取不到：%s" % (name, exc))
            ok = False
            continue
        folder = os.path.dirname(path)
        if folder and not os.path.isdir(folder):
            os.makedirs(folder)
        with open(path, "wb") as handle:
            handle.write(data)
        manifest.setdefault("files", {})[name] = sha256_of(path)
        done += 1
    if not check_only and manifest:
        manifest.update({"name": package["name"], "version": package["version"],
                         "license": package["license"], "home": package["home"],
                         "note": "由 scripts\\fetch-molecule-libs.py 取；改动这里没用，重跑脚本会覆盖"})
        with io.open(manifest_path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(manifest, ensure_ascii=False, indent=1, sort_keys=True))
            handle.write("\n")
    print("    %d/%d 个文件就绪" % (done, len([f for f in files if not f["name"].endswith(".map")])))
    return ok


def fetch_one(item, check_only=False):
    path = os.path.join(OUT_DIR, item["file"])
    if os.path.isfile(path):
        digest = sha256_of(path)
        if not item["sha256"]:
            print("  已有 %s（sha256=%s，把它填进脚本）" % (item["file"], digest))
            return True
        if digest == item["sha256"]:
            print("  %s 校验通过（%d 字节）" % (item["file"], os.path.getsize(path)))
            return True
        print("  %s 的 SHA-256 对不上（本地 %s）" % (item["file"], digest))
        if check_only:
            return False
        os.remove(path)
    if check_only:
        print("  缺 %s" % item["file"])
        return False
    for url in item["urls"]:
        try:
            print("  取 %s" % url)
            data = download(url)
        except Exception as exc:                      # noqa: BLE001 - 构建期把原因打出来就行
            print("    失败：%s" % exc)
            continue
        if not data:
            print("    空文件，跳过")
            continue
        if not os.path.isdir(OUT_DIR):
            os.makedirs(OUT_DIR)
        with open(path, "wb") as handle:
            handle.write(data)
        digest = sha256_of(path)
        print("  写好 %s（%d 字节，sha256=%s）" % (item["file"], len(data), digest))
        if item["sha256"] and digest != item["sha256"]:
            print("    ！和脚本里记的 SHA-256 不一致，先别用：%s" % item["sha256"])
            return False
        return True
    return False


def package_file_count(package):
    """已取下来的文件个数——README 里那句"多少个文件"要跟 manifest 对得上。"""
    path = os.path.join(OUT_DIR, package["dir"], "manifest.json")
    try:
        with io.open(path, encoding="utf-8") as handle:
            return len(json.load(handle).get("files") or {})
    except (OSError, ValueError):
        return 0


def write_notice():
    """把"这些东西是谁的、什么许可证"写一份放在旁边——随包发出去要能交代清楚。"""
    path = os.path.join(OUT_DIR, "README.txt")
    lines = ["这个目录里是随包发布的第三方前端库（不要手改，重新取用：",
             "  .\\python\\python.exe scripts\\fetch-molecule-libs.py", "",
             "运行期不联网，全部离线随包；来源、许可证与用途：", ""]
    for item in LIBS:
        lines.append("* %s（%s）" % (item["name"], item["license"]))
        lines.append("    用途：%s" % item["use"])
        lines.append("    来源：%s" % item["home"])
        lines.append("    文件：%s" % item["file"])
        lines.append("")
    for package in PACKAGES:
        lines.append("* %s@%s（%s）" % (package["name"], package["version"], package["license"]))
        lines.append("    用途：%s" % package["use"])
        lines.append("    来源：https://www.npmjs.com/package/%s" % package["name"])
        lines.append("    目录：%s\\（%d 个文件，逐个记在 manifest.json 里）"
                     % (package["dir"], package_file_count(package)))
        lines.append("")
    lines.append("详见 docs\\third-party-licenses.md。")
    if not os.path.isdir(OUT_DIR):
        os.makedirs(OUT_DIR)
    with io.open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines) + "\n")
    print("  已写 %s" % os.path.relpath(path, BASE_DIR))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="只校验，不下载")
    args = parser.parse_args()
    print("第三方库 -> %s" % os.path.relpath(OUT_DIR, BASE_DIR))
    ok = True
    for item in LIBS:
        ok = fetch_one(item, args.check) and ok
    for package in PACKAGES:
        ok = fetch_package(package, args.check) and ok
    if not args.check:
        write_notice()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
