# -*- coding: utf-8 -*-
"""把整个程序树打进一个 exe（Study7Agent.exe + 尾部 tar.gz payload）。

产物结构（与 launcher\\Payload.cs 的读取逻辑一一对应）：

    [Study7Agent.exe][payload.tar.gz][戳][4 字节戳长度]["W7APAYL1"][8 字节 payload 长度]

为什么是 gzip + tar 而不是 zip：Win7 自带的 .NET 3.5 只有 GZipStream/DeflateStream，
没有 zip 容器读取器；tar 的头格式简单到能在 C# 里几十行读完，zip 的中央目录不行。
（也考虑过 LZMA/7z，decoder 得另带 —— 不行。）

"发什么文件"复用 scripts\\make-package.py 的规则，"内核裁剪"复用
scripts\\fetch-chromium.py 的名单：一个规则只有一个出处，不在这里重抄一遍。

用法：
    .\\python\\python.exe scripts\\make-exe.py                 正式打（含内置内核，几分钟）
    .\\python\\python.exe scripts\\make-exe.py --no-chromium   先验流程（不带内核，快）
    .\\python\\python.exe scripts\\make-exe.py --payload build\\payload-<版本>.tar.gz
                                                               只改了外壳时复用上次的 payload（秒级）
    .\\python\\python.exe scripts\\make-exe.py --no-payload    打完把 payload 删掉

payload 默认**留在 build\\ 下**：改外壳（launcher\\*.cs）时用 --payload 复用，秒级出包；
不带 --no-payload 就不会删。真踩过：以前默认删，结果发布流程跑完 payload 就没了，
下次改一行 C# 又要等三分钟。
"""

import calendar
import gzip
import hashlib
import importlib.util
import io
import json
import os
import re
import struct
import subprocess
import sys
import tarfile
import time
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, os.pardir))
VENDOR = os.path.join(ROOT, "vendor")
CHROMIUM_ZIP = os.path.join(VENDOR, "chromium-109-win64.zip")
CHROMIUM_DIR = "chromium-109"          # payload 里的目录名（启动器的 FindKernel 认这个）
MAGIC = b"W7APAYL1"
STUB = os.path.join(ROOT, "Study7Agent.exe")
GZIP_LEVEL = 6                          # 9 太慢，6 的体积差别在 1% 以内


def load(name, filename):
    path = os.path.join(HERE, filename)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def safe_console():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
            if not stream.isatty() and not os.environ.get("PYTHONIOENCODING"):
                stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError, OSError):
            continue


def version_of(package_module, override=""):
    if override:
        return override
    with io.open(os.path.join(ROOT, "webui", "core.py"), "r", encoding="utf-8") as handle:
        found = re.search(r'^VERSION = "([^"]+)"', handle.read(), re.M)
    return found.group(1) if found else "0.0.0"


def human(size):
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return "%.1f %s" % (value, unit)
        value /= 1024.0
    return "%.1f GB" % value


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def show(path):
    """尽量给相对路径；目标在别的盘符上时（自检用临时目录）relpath 会直接抛异常。"""
    try:
        return os.path.relpath(path, ROOT)
    except ValueError:
        return path


def payload_members(package_module, chromium_module, no_chromium, log):
    """产出 (归档名, 绝对路径) 列表；内核直接从 zip 里挑，不用解包后的目录。"""
    members = []
    skip = {"study7agent.exe"}          # 它就是外壳本身，塞进 payload 毫无意义
    for name in package_module.INCLUDE_FILES:
        if os.path.basename(name).lower() in skip:
            continue
        path = os.path.join(ROOT, name)
        if os.path.isfile(path):
            members.append((name.replace("\\", "/"), path))
        else:
            log("警告：缺少 %s" % name)
    for name in package_module.INCLUDE_DIRS:
        folder = os.path.join(ROOT, name)
        if not os.path.isdir(folder):
            log("警告：缺少目录 %s" % name)
            continue
        for path in package_module.rel_files(folder):
            members.append((os.path.relpath(path, ROOT).replace("\\", "/"), path))
    if not no_chromium:
        if not os.path.isfile(CHROMIUM_ZIP):
            log("警告：没有 %s，这次不带内置内核（先跑 scripts\\fetch-chromium.py）" % CHROMIUM_ZIP)
        else:
            with zipfile.ZipFile(CHROMIUM_ZIP) as archive:
                names = chromium_members(archive, chromium_module)
                members.append(("__chromium__", names))
    return members


def chromium_members(archive, chromium_module):
    """按裁剪名单从官方 zip 里挑文件，去掉顶层那一层目录。"""
    keep = []
    for info in archive.infolist():
        if info.is_dir():
            continue
        parts = info.filename.replace("\\", "/").split("/")
        if len(parts) < 2:
            continue
        relative = "/".join(parts[1:])
        lowered = [item.lower() for item in parts[1:-1]]
        base = parts[-1].lower()
        if any(item in chromium_module.DROP_DIRS for item in lowered):
            continue
        if base in chromium_module.DROP_NAMES or base.endswith(".pdb"):
            continue
        if lowered and lowered[-1] == "locales" and base.endswith(".pak") \
                and base not in chromium_module.KEEP_LOCALES:
            continue
        keep.append((relative, info))
    return keep


def build_payload(members, target, chromium_module, log):
    """写 tar.gz，再算 sha256。返回 (sha256, 文件数, 原始字节数)。

    **payload 可重现**：成员按名字排序，tar 里的时间戳统一取 zip 里的值，gzip 头的
    mtime 钉成 0。所以戳（版本-哈希前 12 位）就是"这棵程序树"的指纹，两次打包一样就
    说明发出去的东西没变。

    注意整体 exe **不是**字节可重现的：外壳由 csc 3.5 编译，它会在 PE 头里写编译时间戳
    （.NET 3.5 的编译器没有 /deterministic）。要比对发布物就用戳 + 记录下来的 exe 哈希。
    """
    files = 0
    total_bytes = 0
    started = time.time()
    members = [(name, value) for name, value in members if name != "__chromium__"] + \
              [(name, value) for name, value in members if name == "__chromium__"]
    members.sort(key=lambda item: item[0])
    archive = zipfile.ZipFile(CHROMIUM_ZIP) if any(item[0] == "__chromium__" for item in members) else None
    try:
        # gzip 头里默认会写当前时间，那样 payload 每次都不同、戳也就没意义了。mtime=0 钉死。
        with open(target, "wb") as sink:
            with gzip.GzipFile(fileobj=sink, mode="wb", compresslevel=GZIP_LEVEL, mtime=0) as gz:
                with tarfile.open(fileobj=gz, mode="w|", format=tarfile.GNU_FORMAT) as tar:
                    for name, value in members:
                        if name == "__chromium__":
                            for relative, info in sorted(value, key=lambda item: item[0]):
                                source = archive.open(info)
                                try:
                                    # 多一层 "chrome"：官方包里那层目录名又长又带版本号
                                    # （ungoogled-chromium_109.0.5414.120-1.1_windows，50 字符），
                                    # Win7 没有长路径支持，解到用户目录下很容易顶到 260 上限。
                                    entry = tarfile.TarInfo(CHROMIUM_DIR + "/chrome/" + relative)
                                    entry.size = info.file_size
                                    entry.mtime = calendar.timegm(info.date_time)
                                    tar.addfile(entry, source)
                                finally:
                                    source.close()
                                files += 1
                                total_bytes += info.file_size
                            log("  内置内核：%d 个文件" % len(value))
                            continue
                        tar.add(value, arcname=name, recursive=False)
                        files += 1
                        total_bytes += os.path.getsize(value)
    finally:
        if archive is not None:
            archive.close()
    digest = hashlib.sha256()
    with open(target, "rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    log("  打进 %d 个文件，原始 %s，压缩后 %s，用时 %.0f 秒"
        % (files, human(total_bytes), human(os.path.getsize(target)), time.time() - started))
    return digest.hexdigest(), files, total_bytes


def build_stub(log):
    """编外壳。复用 scripts\\build-launcher.ps1，编译参数只有一个出处。"""
    script = os.path.join(HERE, "build-launcher.ps1")
    powershell = os.path.join(os.environ.get("SystemRoot") or r"C:\Windows",
                              "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    log("编译外壳：%s" % script)
    result = subprocess.call([powershell, "-NoProfile", "-ExecutionPolicy", "Bypass",
                              "-File", script])
    if result != 0 or not os.path.isfile(STUB):
        raise SystemExit("外壳编译失败（退出码 %s）" % result)
    return STUB


def assemble(stub, payload, stamp, target, log):
    """把 [exe][payload][戳][戳长度][魔数][payload 长度] 拼出来。"""
    with open(target, "wb") as out:
        with open(stub, "rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
        with open(payload, "rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
        blob = stamp.encode("utf-8")
        out.write(blob)
        # 尾部 20 字节的顺序要跟两个读取方（launcher\Payload.cs 和下面 verify）一致：
        #   [payload 长度 8][魔数 8][戳长度 4]，戳紧挨在它们前面
        out.write(struct.pack("<q", os.path.getsize(payload)))
        out.write(MAGIC)
        out.write(struct.pack("<i", len(blob)))
    log("已写出 %s（%s）" % (show(target), human(os.path.getsize(target))))


def verify(target, stamp, payload_size, payload_digest, log):
    """按启动器的读法把尾部再解一遍——拼错了这里就会露馅。"""
    total = os.path.getsize(target)
    with open(target, "rb") as handle:
        handle.seek(total - 20)
        tail = handle.read(20)
        length = struct.unpack("<q", tail[0:8])[0]
        magic = tail[8:16]
        stamp_length = struct.unpack("<i", tail[16:20])[0]
        if magic != MAGIC:
            raise SystemExit("校验失败：尾部魔数不对（%r）" % magic)
        if length != payload_size:
            raise SystemExit("校验失败：payload 长度对不上（%d / %d）" % (length, payload_size))
        offset = total - 20 - stamp_length - length
        handle.seek(offset)
        digest = hashlib.sha256()
        left = length
        while left > 0:
            chunk = handle.read(min(1024 * 1024, left))
            if not chunk:
                break
            digest.update(chunk)
            left -= len(chunk)
        if digest.hexdigest() != payload_digest:
            raise SystemExit("校验失败：payload 哈希对不上")
        handle.seek(total - 20 - stamp_length)
        found = handle.read(stamp_length).decode("utf-8")
        if found != stamp:
            raise SystemExit("校验失败：戳对不上（%s / %s）" % (found, stamp))
        handle.seek(0, os.SEEK_SET)
        if handle.read(2) != b"MZ":
            raise SystemExit("校验失败：前面不是一个 PE 文件")
    log("校验通过：戳=%s，payload=%s，整体=%s" % (stamp, human(payload_size), human(total)))


def main(argv):
    safe_console()
    package_module = load("make_package_rules", "make-package.py")
    chromium_module = load("fetch_chromium_rules", "fetch-chromium.py")

    version = ""
    no_chromium = False
    drop_payload = False
    out_name = ""
    payload_file = ""
    rest = list(argv[1:])
    index = 0
    while index < len(rest):
        item = rest[index]
        if item == "--no-chromium":
            no_chromium = True
        elif item == "--no-payload":
            drop_payload = True
        elif item == "--payload" and index + 1 < len(rest):
            # 只改了外壳的时候复用上次的 tar.gz：打一次 payload 要几分钟，改外壳要秒级
            payload_file = rest[index + 1]
            index += 1
        elif item == "--out" and index + 1 < len(rest):
            out_name = rest[index + 1]
            index += 1
        elif item == "--version" and index + 1 < len(rest):
            version = rest[index + 1]
            index += 1
        index += 1

    def log(text):
        sys.stdout.write(text + "\n")
        sys.stdout.flush()

    version = version_of(package_module, version)
    dist = os.path.join(ROOT, "dist")
    if not os.path.isdir(dist):
        os.makedirs(dist)
    work = os.path.join(ROOT, "build")
    if not os.path.isdir(work):
        os.makedirs(work)
    payload_path = os.path.join(work, "payload-%s.tar.gz" % version)
    files = 0

    log("打包 %s（%s内置内核）" % (version, "不带" if no_chromium else ""))
    if payload_file:
        if not os.path.isfile(payload_file):
            raise SystemExit("没有这个 payload：%s" % payload_file)
        payload_path = payload_file
        digest = sha256_file(payload_path)
        files = None            # 复用时不重新统计文件数（读一遍 tar.gz 要几十秒，不值）
        log("  复用已有 payload：%s（%s）" % (show(payload_path), human(os.path.getsize(payload_path))))
    else:
        members = payload_members(package_module, chromium_module, no_chromium, log)
        digest, files, raw = build_payload(members, payload_path, chromium_module, log)
    stamp = "%s-%s" % (version, digest[:12])
    log("戳：%s" % stamp)

    stub = build_stub(log)
    if not out_name:
        out_name = "study7agent-%s.exe" % version
    target = os.path.join(dist, out_name)
    assemble(stub, payload_path, stamp, target, log)
    verify(target, stamp, os.path.getsize(payload_path), digest, log)
    if drop_payload and not payload_file:
        # 显式 --payload 指定的那份永远不删：人家就是留着复用的（这个坑真踩过）
        os.remove(payload_path)
    else:
        log("payload 留在 %s（改外壳时可以 --payload 复用它）" % show(payload_path))

    whole = hashlib.sha256()
    with open(target, "rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            whole.update(chunk)
    log("")
    log("产物：%s" % os.path.relpath(target, ROOT))
    log("大小：%s    文件数：%s" % (human(os.path.getsize(target)),
                                    "-" if files is None else str(files)))
    log("sha256：%s" % whole.hexdigest())
    log("")
    log("下一步：双击它。程序树会解到「程序目录 / %ProgramData%\\Study7Agent」里")
    log("        （**不要**解到用户目录下：实测那儿内核的 GPU 进程会连崩，见 docs\\embedded-browser.md §6）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
