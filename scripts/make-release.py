# -*- coding: utf-8 -*-
"""一条命令打出发布用的两样东西，都丢进 dist\\。

    .\\python\\python.exe scripts\\make-release.py
    .\\python\\python.exe scripts\\make-release.py --api-key sk-xxx     （临时换一把）
    .\\python\\python.exe scripts\\make-release.py 1.1.0-alpha1         （指定版本）

产物（版本号默认取 webui\\core.py 的 VERSION）：

    dist\\study7agent-<版本>.zip            对外分发：里面是同一个单文件 exe + 使用说明.txt
    dist\\study7agent-<版本>-deepseek.zip   同一个包，另外多一份预填好的 config\\agent.json
    dist\\study7agent-<版本>.exe            同一个 exe，本机试跑用（不用发出去）

**为什么发 zip 而不是裸 exe**：IM / 网盘 / 邮件对 .exe 基本都拦，zip 才发得出去；
而且 zip 里能顺手放一份中文「使用说明.txt」。

**为什么带密钥那台不是把密钥编进 exe**：密钥进二进制就没法审计、没法一键更换。
两个 zip 里的 **exe 字节完全相同**，差别只在有没有那份 config\\agent.json。
所以校验也硬气：

  * 干净 zip（和里面的 exe）里不许出现 `sk-`（exe 按字节扫，zip 逐成员扫）
  * 带密钥那个 zip 里，`sk-` 只允许出现在 config/agent.json 这一个成员里

exe 由 scripts\\make-exe.py 打（"发哪些文件"复用 make-package.py 的规则表），
本脚本只负责"两样都打"、"密钥从哪来"、"打完全身扫一遍"。

密钥来源，按优先级：
  1. 命令行 --api-key
  2. 环境变量 W7A_RELEASE_KEY
  3. config\\release-key.txt 的第一行非注释内容

只有 Python 3.8 标准库。
"""

import importlib.util
import io
import json
import os
import re
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, os.pardir))
KEY_FILE = os.path.join("config", "release-key.txt")
ENV_KEY = "W7A_RELEASE_KEY"
CONFIG_MEMBER = "config/agent.json"     # zip 成员名一律用正斜杠（zip 规范；Windows 上 os.path.join 会给反斜杠）
README_MEMBER = "使用说明.txt"
# 和 AGENTS.md 里那两条校验命令同一个正则：sk- 后面至少 16 位
SECRET = re.compile(rb"sk-[A-Za-z0-9]{16,}")
SCAN_BLOCK = 4 * 1024 * 1024
SCAN_OVERLAP = 64          # 防着一个密钥正好跨在两块之间


def load_script(name, filename):
    """把 scripts\\ 下的脚本当模块加载（文件名带横线，不能直接 import）。"""
    path = os.path.join(HERE, filename)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def read_key(root=ROOT):
    """读 config\\release-key.txt 的第一行非注释内容；读不到返回空串。"""
    try:
        with io.open(os.path.join(root, KEY_FILE), encoding="utf-8-sig") as handle:
            for line in handle:
                line = line.strip()
                if line and not line.startswith("#"):
                    return line
    except (OSError, ValueError):
        return ""
    return ""


def parse_args(argv):
    version, key = "", ""
    rest = list(argv[1:])
    index = 0
    while index < len(rest):
        item = rest[index]
        if item in ("--api-key", "--key") and index + 1 < len(rest):
            key = rest[index + 1].strip()
            index += 2
            continue
        if not version:
            version = item
        index += 1
    return version, key


def exe_argv(version=""):
    """喂给 make-exe.main() 的参数。**绝不接受密钥参数**——干净件就是这么来的。"""
    argv = ["make-exe.py"]
    if version:
        argv += ["--version", version]
    return argv


def artifact_names(version):
    """返回 (exe 文件名, 干净 zip 名, 带密钥 zip 名)。

    对外分发的是**两个 zip**：IM/网盘对 .exe 会拦，zip 里放的是同一个单文件 exe。
    """
    base = "study7agent-%s" % version
    return base + ".exe", base + ".zip", base + "-deepseek.zip"


def readme_text():
    """zip 里的「使用说明.txt」。

    存 UTF-8 **带 BOM**：Win7 的记事本按 ANSI 读不带 BOM 的 UTF-8，中文会全是乱码。
    """
    lines = [
        "Study7Agent 使用说明",
        "",
        "1. 双击本文件夹里的 study7agent-*.exe 就能用（不用装 Python、不用装浏览器）。",
        "   第一次启动要解包运行环境（约二三十秒，慢的机器一两分钟），会有进度条，不要重复双击。",
        "2. 第一次启动会自动在桌面建一个快捷方式，以后从桌面图标进就行。",
        "3. 界面用的是程序自带的浏览器内核（Chromium 109）：不依赖你机器上的 Chrome/Edge/360 浏览器，",
        "   也不会影响你自己正开着的浏览器。",
        "4. 退出：右下角托盘图标（Study7Agent）→ 退出（连服务一起关）。",
        "5. 想固定到任务栏：把桌面（或开始菜单里）的「Study7Agent」图标拖到任务栏就行。",
        "   别固定界面窗口本身 —— 那个窗口归浏览器内核管，图标会是内核的。",
        "6. 开机自启默认已打开，可在界面「设置」里关掉。",
        "7. 出问题看日志：exe 旁边的 logs\\exe.log；托盘菜单里有「打开数据目录」和「查看日志」。",
        "8. 配置与数据都在 exe 旁边：config\\（设置、密钥）、data\\（会话记录）。",
    ]
    return "\ufeff" + "\r\n".join(lines) + "\r\n"


def clean_entries(exe_path):
    """干净 zip 里装什么：同一个 exe + 一份使用说明。"""
    return [(os.path.basename(exe_path), ("file", exe_path)),
            (README_MEMBER, ("text", readme_text()))]


def keyed_entries(exe_path, config_text):
    """带密钥那个 zip = 干净 zip 的全部内容 + 一份预填的 config\\agent.json。

    返回 [(归档名, 来源)]，来源是 ("file", 路径) 或 ("text", 文本)。
    单独拆出来是为了能测：谁往这个包里多塞一个带密钥的文件，测试就会响。
    """
    float_text = json.dumps({"qq_download_dir": "D:\\下载",
                            "note": "QQ 收到文件的默认下载目录（老师这台指定）"},
                           ensure_ascii=False, indent=2) + "\n"
    return clean_entries(exe_path) + [(CONFIG_MEMBER, ("text", config_text)),
                                     ("config/float.json", ("text", float_text))]


def normalize_member(name):
    """zip 成员名统一成正斜杠再比：老包里有反斜杠写法，别当成两个文件。"""
    return name.replace("\\", "/")


def scan_bytes(chunks):
    """在字节流里找 sk- 串（跨块也能找到）。返回命中的原文列表。"""
    hits = []
    tail = b""
    for chunk in chunks:
        window = tail + chunk
        for found in SECRET.findall(window):
            hits.append(found.decode("ascii", "replace"))
        tail = window[-SCAN_OVERLAP:]
    return hits


def scan_file(path):
    """扫一个普通文件（exe 也能扫）。"""
    with open(path, "rb") as handle:
        return scan_bytes(iter(lambda: handle.read(SCAN_BLOCK), b""))


def scan_zip(path):
    """扫一个 zip：返回 {成员名: [命中的密钥]}，只列有命中的成员。"""
    found = {}
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            if name.endswith("/"):
                continue
            hits = []
            with archive.open(name) as handle:
                hits = scan_bytes(iter(lambda: handle.read(SCAN_BLOCK), b""))
            if hits:
                found[name] = hits
    return found


def write_zip(target, entries):
    """写一个发布 zip。

    exe 那一条用 ZIP_STORED：它自己已经是压缩过的（137 MB），再 deflate 一遍
    要花一分钟、省不下 1%，纯浪费；文本成员照常压缩。
    """
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, source in entries:
            kind, value = source
            if kind == "file":
                archive.write(value, name, zipfile.ZIP_STORED)
            else:
                archive.writestr(name, value)


def main(argv):
    exe_module = load_script("make_exe_release", "make-exe.py")
    packer = load_script("make_package_rules", "make-package.py")

    version_arg, key = parse_args(argv)
    if not key:
        key = (os.environ.get(ENV_KEY) or "").strip()
    if not key:
        key = read_key(ROOT)
    if not key:
        sys.stderr.write(
            "没有发布用的密钥。三选一：\n"
            "  1) 把密钥写进 %s（第一行，一行就够）\n"
            "  2) 传 --api-key sk-xxx\n"
            "  3) 设环境变量 %s\n" % (KEY_FILE, ENV_KEY))
        return 1
    if not key.startswith("sk-") or len(key) < 20:
        sys.stderr.write("这不像一把 DeepSeek 密钥：%s…（应当是 sk- 开头、不含空格）\n"
                         % key[:8])
        return 1

    print("=" * 72)
    print("1/2 单文件 exe（对外分发，里面不含任何密钥）")
    print("=" * 72)
    code = exe_module.main(exe_argv(version_arg))
    if code:
        sys.stderr.write("exe 没打出来，先修它，带密钥的那个也不打了。\n")
        return code

    version = version_arg or _version_from_core()
    exe_name, clean_name, keyed_name = artifact_names(version)
    exe_path = os.path.join(ROOT, "dist", exe_name)
    if not os.path.isfile(exe_path):
        sys.stderr.write("没找到刚打出来的 %s\n" % exe_path)
        return 1

    # 干净件必须干净：二进制按字节扫，一个 sk- 都不许有
    hits = scan_file(exe_path)
    if hits:
        sys.stderr.write("干净 exe 里扫到了密钥（%s）！停下，先查它从哪来的。\n"
                         % ", ".join(sorted(set(hits))[:3]))
        return 1
    print("已校验：%s 里没有任何 sk- 串（按字节扫的）" % exe_name)

    print("")
    print("=" * 72)
    print("2/3 干净 zip（对外分发：exe + 使用说明.txt）")
    print("=" * 72)
    clean_path = os.path.join(ROOT, "dist", clean_name)
    write_zip(clean_path, clean_entries(exe_path))
    found = scan_zip(clean_path)
    if found:
        sys.stderr.write("干净 zip 里有文件带密钥：%s\n" % ", ".join(sorted(found)))
        return 1
    print("已校验：%s 里没有任何 sk- 串（逐成员扫的）" % clean_name)

    print("")
    print("=" * 72)
    print("3/3 带密钥的 zip（同一份 exe + 预填 config\\agent.json；只给指定机器）")
    print("=" * 72)
    target = os.path.join(ROOT, "dist", keyed_name)
    write_zip(target, keyed_entries(exe_path, packer.prefilled_config(key)))
    found = scan_zip(target)
    others = sorted(name for name in found if normalize_member(name) != CONFIG_MEMBER)
    if others:
        sys.stderr.write("带密钥的包里除了 %s 还有别的文件带密钥：%s\n"
                         % (CONFIG_MEMBER, ", ".join(others)))
        return 1
    if CONFIG_MEMBER not in found:
        sys.stderr.write("带密钥的包里没有找到 %s 里的密钥，打包逻辑不对。\n" % CONFIG_MEMBER)
        return 1
    print("已校验：%s 里的密钥只出现在 %s 这一处" % (keyed_name, CONFIG_MEMBER))

    print("")
    print("dist\\ 里现在有：")
    print("  %-38s 对外分发用这个（解压后双击里面的 exe）" % clean_name)
    print("  %-38s 带密钥，只给指定的那台机器" % keyed_name)
    print("  %-38s 同一个 exe，方便本机直接试跑（不用发出去）" % exe_name)
    print("")
    print("老师那边：解压 zip → 双击 exe（第一次会自动建桌面快捷方式，以后从桌面图标进）。")
    return 0


def _version_from_core():
    try:
        with io.open(os.path.join(ROOT, "webui", "core.py"), encoding="utf-8") as handle:
            found = re.search(r'^VERSION = "([^"]+)"', handle.read(), re.M)
        return found.group(1) if found else "?"
    except OSError:
        return "?"


if __name__ == "__main__":
    sys.exit(main(sys.argv))
