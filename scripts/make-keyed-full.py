# -*- coding: utf-8 -*-
"""带密钥的**完整目录版**包：目录树（skills/webui/python/docs/scripts）+ 内置内核
+ config/agent.json（密钥）+ config/float.json（QQ 下载目录）。

为什么要有它：单文件 exe 版里带内核，但老师要"解压出来能看到 skills 等全部文件"的完整版；
而目录版包里原本**没有内核**，启动器找不到就会退回默认浏览器（360）——所以这里把展开好的
vendor\\chromium-109\\<版本目录> 平铺进包里的 chromium-109\\，FindKernel 就能找到。

用法：
    .\\python\\python.exe scripts\\make-keyed-full.py
产物：dist\\study7agent-<版本>-deepseek.zip（覆盖同名文件）
注意：会临时借用 dist\\study7agent-<版本>.zip（目录版）的构建过程，跑完会恢复原来的单文件版 zip。
"""
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "dist")
PY = os.path.join(ROOT, "python", "python.exe")
sys.path.insert(0, os.path.join(ROOT, "webui"))
import core  # noqa: E402

VERSION = core.VERSION


def load(name, filename):
    path = os.path.join(ROOT, "scripts", filename)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    packer = load("packer", "make-package.py")
    kernel_root = None
    vendor = os.path.join(ROOT, "vendor", "chromium-109")
    if os.path.isdir(vendor):
        for name in sorted(os.listdir(vendor)):
            candidate = os.path.join(vendor, name)
            if os.path.isdir(candidate) and os.path.exists(os.path.join(candidate, "chrome.exe")):
                kernel_root = candidate
                break
        if kernel_root is None and os.path.exists(os.path.join(vendor, "chrome.exe")):
            kernel_root = vendor

    single_zip = os.path.join(DIST, "study7agent-%s.zip" % VERSION)
    backup = os.path.join(DIST, "study7agent-%s-single.zip" % VERSION)
    keyed = os.path.join(DIST, "study7agent-%s-deepseek.zip" % VERSION)
    if os.path.exists(single_zip):
        shutil.move(single_zip, backup)
    subprocess.call([PY, os.path.join("scripts", "make-package.py")], cwd=ROOT)

    key = ""
    key_file = os.path.join(ROOT, "config", "release-key.txt")
    if os.path.exists(key_file):
        for line in io.open(key_file, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#"):
                key = line
                break
    config_text = packer.prefilled_config(key)
    float_text = json.dumps({"qq_download_dir": "D:\\下载",
                             "note": "QQ 收到文件的默认下载目录（老师这台指定）"},
                            ensure_ascii=False, indent=2) + "\n"

    skip_dirs = {"log", "crashpad", "__pycache__"}
    skip_names = {"chrome.7z", "chrome.pdb"}
    count = 0
    with zipfile.ZipFile(single_zip) as src:
        root = src.namelist()[0].split("/")[0]
        with zipfile.ZipFile(keyed, "w", zipfile.ZIP_DEFLATED) as out:
            for item in src.infolist():
                out.writestr(item, src.read(item.filename))
            out.writestr(root + "/config/agent.json", config_text)
            out.writestr(root + "/config/float.json", float_text)
            if kernel_root:
                for base, dirs, files in os.walk(kernel_root):
                    dirs[:] = [d for d in dirs if d.lower() not in skip_dirs]
                    for name in files:
                        if name.lower() in skip_names or name.lower().endswith(".pdb"):
                            continue
                        full = os.path.join(base, name)
                        rel = os.path.relpath(full, kernel_root).replace("\\", "/")
                        with open(full, "rb") as handle:
                            out.writestr(root + "/chromium-109/" + rel, handle.read())
                        count += 1

    with zipfile.ZipFile(keyed) as z:
        members = z.namelist()
        secret = re.compile(rb"sk-[A-Za-z0-9]{16,}")
        hits = [n for n in members if not n.endswith("/") and secret.search(z.read(n))]
        print("包：%s（%.1f MB，条目 %d）" % (os.path.basename(keyed),
                                            os.path.getsize(keyed) / 1048576.0, len(members)))
        print("内核文件 %d 个，chrome.exe 在：%s"
              % (count, any(n.endswith("chromium-109/chrome.exe") for n in members)))
        print("含密钥成员：%s" % hits)
        if hits != [members[0].split("/")[0] + "/config/agent.json"]:
            raise SystemExit("密钥位置不对，别发出去")

    os.remove(single_zip)
    if os.path.exists(backup):
        shutil.move(backup, single_zip)
    print("单文件版发布 zip 已恢复：%s" % os.path.basename(single_zip))


if __name__ == "__main__":
    main()
