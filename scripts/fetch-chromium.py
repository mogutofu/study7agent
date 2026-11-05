# -*- coding: utf-8 -*-
"""内置浏览器内核（Chromium 109）拉取脚本。

为什么锁死 109：Chromium 110 起不再支持 Windows 7/8/8.1，109 是最后一个能在
教室一体机上跑的版本（见 docs/embedded-browser.md）。要换版本先改这里的
CHROMIUM_MAJOR 和候选表，并同步那份文档。

四个候选通道按顺序试，第一个成功的就用：

  1. ungoogled-chromium 109.0.5414.120（官方公布 SHA-256）
  2. 同上的国内 GitHub 代理（哈希锁死，代理改不了内容）
  3. Google 官方快照桶（Win_x64，没有公布哈希，第一次拉完我们自己锁）
  4. npmmirror 的 chromium-browser-snapshots 镜像（国内直连友好）

用法：
    .\\python\\python.exe scripts\\fetch-chromium.py --probe        只探测通道
    .\\python\\python.exe scripts\\fetch-chromium.py               下载 + 校验 + 解包
    .\\python\\python.exe scripts\\fetch-chromium.py --trim        再裁剪（去无用文件）
    .\\python\\python.exe scripts\\fetch-chromium.py --check       打印内核版本
    .\\python\\python.exe scripts\\fetch-chromium.py --archive X.zip   用人工下好的包

哈希对不上就整包丢弃并报错，不会静默接受一个被换过的内核。
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fetch_common as net  # noqa: E402

CHROMIUM_MAJOR = "109"
UNGOOGLED_TAG = "109.0.5414.120-1.1"
UNGOOGLED_FILE = "ungoogled-chromium_%s_windows_x64.zip" % UNGOOGLED_TAG
# 官方公布的 SHA-256（ungoogled-chromium-binaries 发布页），走任何代理都靠它对账
UNGOOGLED_SHA256 = "655b53273ef0b35fa2db5d224af8039131e254296e51a8618f26088ae5e57cba"
# 官方快照桶里确实存在的几个 109 修订号（2022-11-10/11 生成，桶内实测）
SNAPSHOT_REVS = ("1070094", "1070054", "1070032", "1070007")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENDOR_DIR = os.path.join(BASE_DIR, "vendor")
ARCHIVE = os.path.join(VENDOR_DIR, "chromium-%s-win64.zip" % CHROMIUM_MAJOR)
TARGET_DIR = os.path.join(VENDOR_DIR, "chromium-%s" % CHROMIUM_MAJOR)
LOCK_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor-lock.json")

# 裁剪时明确扔掉的：都是启动不需要的旁支（更新器、代理快捷方式、崩溃上报、加密媒体）
DROP_NAMES = {
    "chrome_proxy.exe", "elevation_service.exe", "notification_helper.exe",
    "widevinecdm.dll", "chrome_pwa_launcher.exe", "chrome_cleanup_tool.exe",
    "updater.exe", "setup.exe", "mini_installer.exe",
    "first_party_sets.db", "first_party_sets.db-journal",
}
KEEP_LOCALES = {"zh-cn.pak", "en-us.pak"}
# 目录级清理：一体机上那些"注入型"软件（各种管家/加速器）会在浏览器进程里挂 DLL
# 并在内核目录下写 log\。实测开发机上跑一次 --check 就长出来一个 log\ 目录，
# 里面是 chrome.KSoftLaunch64.dll.log 这种。这种东西绝不能跟着包发出去。
DROP_DIRS = {"log"}


def github_url():
    return ("https://github.com/ungoogled-software/ungoogled-chromium-windows/"
            "releases/download/%s/%s" % (UNGOOGLED_TAG, UNGOOGLED_FILE))


def candidates():
    """(说明, URL, 官方哈希或空) 按优先级排。"""
    rows = [("ungoogled 官方发行包", github_url(), UNGOOGLED_SHA256)]
    for proxy in ("https://ghproxy.net/", "https://ghfast.top/"):
        rows.append(("ungoogled 经 %s" % proxy, proxy + github_url(), UNGOOGLED_SHA256))
    for rev in SNAPSHOT_REVS:
        rows.append(("Google 官方快照 r%s" % rev,
                     "https://storage.googleapis.com/chromium-browser-snapshots/"
                     "Win_x64/%s/chrome-win.zip" % rev, ""))
    for rev in SNAPSHOT_REVS[:2]:
        rows.append(("npmmirror 镜像 r%s" % rev,
                     "https://registry.npmmirror.com/-/binary/chromium-browser-snapshots/"
                     "Win_x64/%s/chrome-win.zip" % rev, ""))
    return rows


def read_lock():
    if not os.path.isfile(LOCK_FILE):
        return {}
    try:
        with open(LOCK_FILE, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return {}


def write_lock(url, digest):
    data = read_lock()
    data["chromium"] = {"version": CHROMIUM_MAJOR, "url": url, "sha256": digest}
    with open(LOCK_FILE, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


def do_probe():
    rows = []
    for label, url, _official in candidates():
        result = net.probe(url)
        result["label"] = label
        rows.append(result)
    net.report(rows, "Chromium %s 通道探测（只取前 64 KB）" % CHROMIUM_MAJOR)
    ok = [row for row in rows if row["ok"]]
    print("\n可用通道 %d/%d" % (len(ok), len(rows)))
    if ok:
        print("首选：%s" % ok[0]["url"])
    return 0 if ok else 1


def pick(archive):
    if archive:
        return "人工指定的包", archive, ""
    rows = candidates()
    lock = read_lock().get("chromium", {})
    if lock.get("sha256") and lock.get("url"):
        for label, url, official in rows:
            if url == lock["url"]:
                return label, url, official or lock["sha256"]
    for label, url, official in rows:
        result = net.probe(url, want=1024)
        print("  试 %s ... %s %s" % (label, "OK" if result["ok"] else "不通", result["error"]))
        if result["ok"]:
            return label, url, official
    return "", "", ""


def download(label, url, official, parallel=1):
    if os.path.isfile(ARCHIVE):
        print("已有 %s，跳过下载（要重下先删掉它）" % ARCHIVE)
        return True
    if not os.path.isdir(VENDOR_DIR):
        os.makedirs(VENDOR_DIR)
    print("从 %s 取内核：%s" % (label, url))
    print("（约 100-200 MB，断线重跑本命令会接着下）")
    if parallel > 1:
        ok, message = net.fetch_parallel(url, ARCHIVE, connections=parallel)
    else:
        ok, message = net.fetch_to_file(url, ARCHIVE, timeout=120)
    print("  %s" % message)
    if not ok:
        return False
    digest = net.sha256_file(ARCHIVE)
    print("  sha256 %s" % digest)
    if official and digest != official:
        print("哈希对不上！官方是 %s，下到的是 %s，整包丢弃。" % (official, digest))
        os.remove(ARCHIVE)
        return False
    if not official:
        lock = read_lock().get("chromium", {})
        if lock.get("sha256") and lock["sha256"] != digest:
            print("哈希和上次锁定的不一致（锁定 %s），整包丢弃。" % lock["sha256"])
            os.remove(ARCHIVE)
            return False
        write_lock(url, digest)
        print("  已把哈希写进 scripts\\vendor-lock.json，以后每次都要对上")
    return True


def extract():
    if not os.path.isfile(ARCHIVE):
        print("没有 %s，先跑一次不带参数的下载" % ARCHIVE)
        return False
    if os.path.isdir(TARGET_DIR):
        shutil.rmtree(TARGET_DIR)
    print("解包到 %s" % TARGET_DIR)
    with zipfile.ZipFile(ARCHIVE) as archive:
        archive.extractall(TARGET_DIR)
    exe = find_exe()
    if not exe:
        print("解包后没找到 chrome.exe，包结构可能变了，先人工看一眼 %s" % TARGET_DIR)
        return False
    print("  内核可执行文件：%s" % os.path.relpath(exe, BASE_DIR))
    return True


def find_exe():
    for root, _dirs, files in os.walk(TARGET_DIR):
        for name in files:
            if name.lower() in ("chrome.exe", "chromium.exe"):
                return os.path.join(root, name)
    return None


def tree_size():
    total = 0
    for root, _dirs, files in os.walk(TARGET_DIR):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def do_trim():
    before = tree_size()
    removed = 0
    folders = 0
    for root, dirs, files in os.walk(TARGET_DIR, topdown=True):
        for name in list(dirs):
            if name.lower() in DROP_DIRS:
                shutil.rmtree(os.path.join(root, name), ignore_errors=True)
                dirs.remove(name)
                folders += 1
        in_locales = os.path.basename(root).lower() == "locales"
        for name in files:
            lower = name.lower()
            drop = lower in DROP_NAMES or lower.endswith(".pdb")
            if in_locales and lower.endswith(".pak") and lower not in KEEP_LOCALES:
                drop = True
            if drop:
                path = os.path.join(root, name)
                try:
                    os.remove(path)
                    removed += 1
                except OSError:
                    pass
    after = tree_size()
    print("裁剪：删了 %d 个文件 + %d 个目录，%s 减到 %s" % (
        removed, folders, net.human(before), net.human(after)))
    return True


def file_version(path):
    """读 PE 文件里的版本资源。

    不能用 `chrome.exe --version`：Windows 上 chrome.exe 是 GUI 子系统程序，
    版本号不往 stdout 打，直接调用会一直挂着不退出（实测 30 秒超时）。
    GetFileVersionInfo 是系统 API，ctypes 直接调，不用装任何东西。
    """
    import ctypes
    from ctypes import wintypes

    size = ctypes.windll.version.GetFileVersionInfoSizeW(path, None)
    if not size:
        return ""
    buffer = ctypes.create_string_buffer(size)
    if not ctypes.windll.version.GetFileVersionInfoW(path, 0, size, buffer):
        return ""
    pointer = ctypes.c_void_p()
    length = wintypes.UINT()
    if not ctypes.windll.version.VerQueryValueW(buffer, "\\", ctypes.byref(pointer), ctypes.byref(length)):
        return ""
    if not pointer.value or length.value < 52:
        return ""
    data = ctypes.cast(pointer, ctypes.POINTER(ctypes.c_uint32 * 13)).contents
    # 结构体：前面 2 个 DWORD 是签名与结构版本，接着 4 个 WORD 才是文件版本
    parts = ctypes.cast(ctypes.byref(data, 8), ctypes.POINTER(ctypes.c_uint16 * 4)).contents
    return "%d.%d.%d.%d" % (parts[1], parts[0], parts[3], parts[2])


def do_check():
    exe = find_exe()
    if not exe:
        print("还没解包，先跑不带参数的命令")
        return 1
    version = file_version(exe)
    if not version:
        print("读不到版本资源：%s" % os.path.relpath(exe, BASE_DIR))
        return 1
    print("内核版本：%s   (%s)" % (version, os.path.relpath(exe, BASE_DIR)))
    if not version.startswith(CHROMIUM_MAJOR + "."):
        print("版本不是 %s 一系，Win7 上可能起不来，先人工确认。" % CHROMIUM_MAJOR)
        return 1
    return 0


def main():
    parser = argparse.ArgumentParser(description="拉取内置 Chromium %s 内核" % CHROMIUM_MAJOR)
    parser.add_argument("--probe", action="store_true", help="只探测通道，不下载")
    parser.add_argument("--trim", action="store_true", help="裁剪解包后的目录")
    parser.add_argument("--check", action="store_true", help="打印内核版本")
    parser.add_argument("--archive", default="", help="用人工下好的 zip（离线机器兜底）")
    parser.add_argument("--parallel", type=int, default=1,
                        help="分段并行下载的段数，默认 1；单连接被 CDN 限到几 KB/s 时用 4-8")
    args = parser.parse_args()

    if args.probe:
        return do_probe()
    if args.check:
        return do_check()
    if args.trim:
        return 0 if do_trim() else 1
    if os.path.isdir(TARGET_DIR) and find_exe():
        print("已经有解包的 %s（要重来先删掉这个目录）" % TARGET_DIR)
        return 0
    label, url, official = pick(args.archive)
    if not url:
        print("所有通道都不通。人工兜底：把 zip 放到 vendor\\ 下再跑")
        print("  .\\python\\python.exe scripts\\fetch-chromium.py --archive vendor\\your.zip")
        return 1
    if not download(label, url, official, args.parallel):
        return 1
    if not extract():
        return 1
    print("下一步：--check 看版本，--trim 裁剪")
    return 0


if __name__ == "__main__":
    sys.exit(main())
