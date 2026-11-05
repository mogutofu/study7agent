# -*- coding: utf-8 -*-
"""打包一个干净的发布 zip：只带运行需要的文件，排除本机状态与密钥。

跑法： .\\python\\python.exe scripts\\make-package.py [版本]
      .\\python\\python.exe scripts\\make-package.py [版本] --api-key sk-xxx
产物： dist\\study7agent-<版本>.zip（zip 内是单层目录 study7agent-<版本>\\）

默认**不含任何密钥**，适合对外分发。
--api-key 是显式选择：会按 config\\agent.example.json 造一份带密钥的 config\\agent.json
放进包里（产物默认叫 <包名>-deepseek.zip，不会覆盖干净包），本机的 config\\agent.json 不受影响。
带密钥的包等于把额度交出去，**不要放到群里、网盘、公共共享盘**。
"""
import hashlib
import io
import json
import os
import re
import sys
import time
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, os.pardir))

# 随包发布的目录与文件（相对包根）
INCLUDE_FILES = ["agent.exe", "agent.ico", "agent-icon.png", "start-webui.cmd", "README.md",
                 # 一键启动器：老师双击它就完事（无窗口起服务 + 应用窗口 + 托盘图标）。
                 # 它的源码和编译脚本一起进包，方便在别的机器上重新编译（docs\build.md）。
                 "Study7Agent.exe",
                 os.path.join("launcher", "Study7Agent.cs"),
                 os.path.join("launcher", "Payload.cs"),
                 os.path.join("scripts", "build-launcher.ps1"),
                 # 单文件打包（tar.gz payload 追加到 exe 尾部）：随包带走，
                 # 换台机器也能自己重新打一个单文件版
                 os.path.join("scripts", "make-exe.py"),
                 os.path.join("config", "agent.example.json"),
                 os.path.join("scripts", "make-icon.py"),
                 # Win7 快速兼容性门禁：开发期替代"反复开虚拟机"，任务书点名要交付的东西。
                 # 随包带走，谁拿到都能自己跑一遍（几秒钟），不用装任何东西。
                 os.path.join("scripts", "win7-gate.py"),
                 # 悬浮工具栏：server.py 启动浏览器时会把它拉起来，缺了就没这个功能
                 os.path.join("scripts", "study7agent-float.ps1"),
                 # 打包脚本自己也带上：随包发布的 docs\build.md 会引用它，
                  # 而且包内自检（webui\test_webui.py）里有专门测它的用例——
                  # 不带就会让"解压后跑自检"这一步直接报 FileNotFoundError。
                 os.path.join("scripts", "make-package.py"),
                 # 同上：test_webui.py 里有专门测发布流程的用例，包内自检要读得到它。
                 # 这个脚本里**没有**密钥（密钥在 config\release-key.txt，而 config\ 不进包）。
                 os.path.join("scripts", "make-release.py"),
                 # 下面这几个也是**包内自检要 import 的**：解压后第一次跑自检就是因为缺它们
                 # 直接报 FileNotFoundError（客机 2026-09-29 栽过：870 项里 5 个 error，
                 # 全是 scripts\fetch_common.py 找不到）。凡是 test_webui.py 会去读的脚本，
                 # 都必须列在这儿 —— 增量同步那条路会补 scripts\*.py，全新解压这条路不会。
                 os.path.join("scripts", "fetch_common.py"),
                 os.path.join("scripts", "fetch-chromium.py"),
                 os.path.join("scripts", "fetch-chem-data.py")]
INCLUDE_DIRS = ["webui", "skills", "docs", "python"]
# 明确排除：本机状态、密钥、缓存、源码镜像、临时文件
EXCLUDE_DIRS = {"__pycache__", "data", ".webui-uploads", "logs", "study7agent-src",
                ".git", ".vscode", "node_modules",
                "browser-profile",   # Study7Agent.exe 开的那个浏览器窗口自己的配置目录（运行时产生）
                "kernel-profile"}    # 内置内核（Chromium 109）的配置目录，同上
EXCLUDE_FILES = {"agent.json",          # 本机配置（里面有老师的 API Key），绝不进包
                 "webui.json",          # 本机设置（工作区、令牌）
                 "desktop.ini",         # 本机生成的文件夹图标配置
                 "checksums.txt"}       # 重新生成


def make_console_safe():
    """中文 Windows（GBK）上把输出重定向到文件时，个别字符编不出来会中断脚本。

    这个脚本要保持独立（不 import webui 里的模块），所以这里放一份精简实现。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
            if not stream.isatty() and not os.environ.get("PYTHONIOENCODING"):
                stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError, OSError):
            continue


def parse_args(argv):
    """返回 (版本, api_key, 输出文件名)；兼容以前只传一个版本的用法。"""
    version = ""
    api_key = ""
    out_name = ""
    rest = list(argv[1:])
    index = 0
    while index < len(rest):
        item = rest[index]
        if item in ("--api-key", "--key") and index + 1 < len(rest):
            api_key = rest[index + 1].strip()
            index += 2
            continue
        if item == "--out" and index + 1 < len(rest):
            out_name = rest[index + 1].strip()
            index += 2
            continue
        if not version:
            version = item
        index += 1
    return version, api_key, out_name


def prefilled_config(api_key):
    """按 config\\agent.example.json 造一份带密钥的配置（只写进包里，不动本机文件）。"""
    path = os.path.join(ROOT, "config", "agent.example.json")
    with io.open(path, "r", encoding="utf-8") as handle:
        config = json.load(handle)
    config["api_key"] = api_key
    for profile in (config.get("models") or {}).values():
        # 模型档案里空着的密钥也一起填上，这样老师在界面上切 flash/pro 都不会掉线
        if isinstance(profile, dict) and not profile.get("api_key"):
            profile["api_key"] = api_key
    return json.dumps(config, ensure_ascii=False, indent=2) + "\n"


def rel_files(root):
    out = []
    for base, dirs, files in os.walk(root):
        dirs[:] = [item for item in dirs if item not in EXCLUDE_DIRS]
        for name in files:
            if name in EXCLUDE_FILES or name.startswith("tmp-") or name.endswith(".pyc"):
                continue
            if name.endswith(".zip") or name.endswith(".log"):
                continue
            out.append(os.path.join(base, name))
    return out


def main(argv):
    make_console_safe()
    version_arg, api_key, out_name = parse_args(argv)
    version = "0.5.2"       # 兜底值；正常情况下从 webui\core.py 的 VERSION 读
    with io.open(os.path.join(ROOT, "webui", "core.py"), "r", encoding="utf-8") as handle:
        found = re.search(r'^VERSION = "([^"]+)"', handle.read(), re.M)
    if found:
        version = found.group(1)
    if version_arg:
        version = version_arg
    package = "study7agent-%s" % version
    dist = os.path.join(ROOT, "dist")
    if not os.path.isdir(dist):
        os.makedirs(dist)
    if not out_name:
        out_name = package + ("-deepseek.zip" if api_key else ".zip")
    elif not out_name.endswith(".zip"):
        out_name += ".zip"
    target = os.path.join(dist, out_name)

    collected = []
    for name in INCLUDE_FILES:
        path = os.path.join(ROOT, name)
        if os.path.isfile(path):
            collected.append(path)
        else:
            sys.stderr.write("警告：缺少 %s\n" % name)
    for name in INCLUDE_DIRS:
        path = os.path.join(ROOT, name)
        if os.path.isdir(path):
            collected.extend(rel_files(path))
        else:
            sys.stderr.write("警告：缺少目录 %s\n" % name)

    # 预填密钥：包内自己生成一份 config/agent.json（不复制本机那份，避免带出别的本机设置）
    extra_files = []
    if api_key:
        extra_files.append((os.path.join("config", "agent.json"), prefilled_config(api_key)))

    # 空目录（workspace 是运行时用的，包一个空壳让老师一眼看到产出位置）
    empty_dirs = ["workspace"]
    allowed_secret = "%s/config/agent.json" % package

    checksums = []
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(collected):
            relative = os.path.relpath(path, ROOT).replace(os.sep, "/")
            with open(path, "rb") as handle:
                data = handle.read()
            archive.writestr("%s/%s" % (package, relative), data)
            digest = hashlib.sha256(data).hexdigest()
            checksums.append("%s  %s" % (digest, relative))
        for relative, text in extra_files:
            data = text.encode("utf-8")
            archive.writestr("%s/%s" % (package, relative.replace(os.sep, "/")), data)
            checksums.append("%s  %s" % (hashlib.sha256(data).hexdigest(),
                                         relative.replace(os.sep, "/")))
        for name in empty_dirs:
            archive.writestr("%s/%s/" % (package, name), "")
        header = ("# study7agent %s 文件校验（sha256）\n"
                  "# 只覆盖随包发布的文件；python\\ 运行时目录与运行时生成的数据不在此列。\n"
                  % version)
        checksum_text = header + "\n".join(checksums) + "\n"
        archive.writestr("%s/checksums.txt" % package, checksum_text)

    # 根目录也放一份同样的校验文件，免得两处不一致（旧版本只写了一行 agent.exe）。
    # 预填密钥的包不动这份：它记录的是"标准发布包"的清单，混着两份会互相矛盾。
    if not api_key:
        with io.open(os.path.join(ROOT, "checksums.txt"), "w", encoding="utf-8",
                     newline="\n") as handle:
            handle.write(checksum_text)

    size = os.path.getsize(target)
    with zipfile.ZipFile(target) as archive:
        names = archive.namelist()
    print("已打包：%s" % target)
    print("  版本    ：%s" % version)
    print("  文件数  ：%d（zip 内条目 %d）" % (len(collected) + len(extra_files), len(names)))
    print("  大小    ：%.1f MB" % (size / 1048576.0))
    print("  根目录  ：%s/" % package)

    # 自检：包内不能出现密钥、会话数据或本机配置
    #（--api-key 模式下，只有那一份"故意预填"的 config/agent.json 例外）
    forbidden = []
    secret_pattern = re.compile(rb"sk-[A-Za-z0-9]{16,}")
    with zipfile.ZipFile(target) as archive:
        for name in names:
            if "/data/" in name or name.endswith("/webui.json"):
                forbidden.append(name)
            if name.endswith("/agent.json") and name != allowed_secret:
                forbidden.append(name)
            if name.endswith((".py", ".js", ".json", ".md", ".cmd", ".txt")):
                if secret_pattern.search(archive.read(name)) and name != allowed_secret:
                    forbidden.append(name + "（疑似含密钥）")
    if forbidden:
        print("  [!] 包里有不该出现的东西：")
        for item in forbidden:
            print("      " + item)
        return 1
    if api_key:
        # 复核：包里那份配置确实只带这一把密钥，而且密钥写对了
        with zipfile.ZipFile(target) as archive:
            payload = json.loads(archive.read(allowed_secret).decode("utf-8"))
        if payload.get("api_key") != api_key:
            print("  [!] 预填的密钥和给出的不一致")
            return 1
        print("  自检    ：没有会话数据、没有本机配置；预填密钥 1 处（%s…%s）"
              % (api_key[:6], api_key[-4:]))
        print("  校验文件：%s/checksums.txt（在 zip 里；仓库根目录那份留给标准包）" % package)
        print("  打包时间：%s" % time.strftime("%Y-%m-%d %H:%M:%S"))
        print("")
        print("  [!] 这个包里带着你的真实 API Key（config\\agent.json）：")
        print("      谁拿到这个 zip，谁就能用你的额度。别发到群里/网盘/公共共享盘；")
        print("      一旦外泄，去 platform.deepseek.com 把它删掉重新生成一把。")
        print("      要对外分发的干净包，直接跑 scripts\\make-package.py（不带 --api-key）。")
        return 0
    print("  自检    ：没有密钥、没有会话数据、没有本机配置 ✓")
    print("  校验文件：%s/checksums.txt" % package)
    print("  打包时间：%s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
