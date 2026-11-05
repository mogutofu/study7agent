# -*- coding: utf-8 -*-
"""外部应用的查找与启动：悬浮工具栏上的「白板 / 展台 / QQ」按钮走这里。

教室一体机上这些软件装在哪完全看学校（希沃的安装目录、QQ 的版本号都不一样），
所以不写死路径，按三级顺序找：

  1. ``config\\float.json`` 里老师自己填的路径 —— 填了就用它，不再猜；
  2. 常见安装位置（支持 ``%ProgramFiles%`` 这类环境变量和一个 ``*`` 通配）；
  3. 开始菜单 / 桌面的快捷方式名字里的关键词 —— 希沃白板5 装完一定会在开始菜单留下入口。

启动一律走 ``os.startfile()``：``.exe`` 和 ``.lnk`` 都能开，而且不经过 cmd，
省掉一整层引号转义（也避开 §8 坑 3 那类批处理编码问题）。

注意：**PPT 不在这里**。「切到PPT」是把已经运行的 WPS 演示 / PowerPoint 切到前台，
属于"切窗口"而不是"打开程序"，由悬浮工具栏就地用 AppActivate 完成（要快，不走网络）。

只使用 Python 3.8 标准库。
"""

import glob
import json
import os

# 悬浮工具栏能开的外部应用。exes 里的 %VAR% 由 os.path.expandvars 展开，
# 允许一个 * 通配（WPS 的 "WPS Office\11.1.0.xxxxx\office6\wpp.exe" 版本号一直在变）。
APPS = {
    "whiteboard": {
        "label": "希沃白板",
        "exes": [
            r"%ProgramFiles%\Seewo\EasiNote5\EasiNote5.exe",
            r"%ProgramFiles(x86)%\Seewo\EasiNote5\EasiNote5.exe",
            r"%ProgramFiles%\Seewo\EasiNote5\EasiNote.exe",
            r"%ProgramFiles(x86)%\Seewo\EasiNote5\EasiNote.exe",
            r"%ProgramFiles%\Seewo\EasiNote\EasiNote.exe",
            r"%ProgramFiles(x86)%\Seewo\EasiNote\EasiNote.exe",
            r"%ProgramFiles%\Seewo\EasiNote5\*EasiNote*.exe",
            r"%ProgramFiles(x86)%\Seewo\EasiNote5\*EasiNote*.exe",
        ],
        "lnk": ["希沃白板"],
    },
    "display": {
        "label": "希沃展台",
        "exes": [
            r"%ProgramFiles%\Seewo\SeewoDisplay\SeewoDisplay.exe",
            r"%ProgramFiles(x86)%\Seewo\SeewoDisplay\SeewoDisplay.exe",
            r"%ProgramFiles%\Seewo\SeewoVideo\SeewoVideo.exe",
            r"%ProgramFiles(x86)%\Seewo\SeewoVideo\SeewoVideo.exe",
        ],
        # 只认展会自己那几个词：写一个裸 "Seewo" 会把希沃白板也匹配进来
        "lnk": ["希沃展台", "视频展台", "展台"],
    },
    "qq": {
        "label": "QQ",
        "exes": [
            # QQNT（新版，9.x）：%ProgramFiles%\Tencent\QQNT\QQ.exe
            r"%ProgramFiles%\Tencent\QQNT\QQ.exe",
            r"%ProgramFiles(x86)%\Tencent\QQNT\QQ.exe",
            # 经典 QQ
            r"%ProgramFiles(x86)%\Tencent\QQ\Bin\QQScLauncher.exe",
            r"%ProgramFiles%\Tencent\QQ\Bin\QQScLauncher.exe",
            r"%ProgramFiles(x86)%\Tencent\QQ\Bin\QQ.exe",
            r"%ProgramFiles%\Tencent\QQ\Bin\QQ.exe",
            r"%ProgramFiles%\Tencent\QQNT\*QQ*.exe",
            # 腾讯 TIM：学校里很常见，它就是"那个 QQ"，学生/老师看到的图标是同一套
            r"%ProgramFiles(x86)%\Tencent\TIM\Bin\TIM.exe",
            r"%ProgramFiles%\Tencent\TIM\Bin\TIM.exe",
            r"%ProgramFiles(x86)%\Tencent\TIM\Bin\QQScLauncher.exe",
            r"%ProgramFiles%\Tencent\TIM\Bin\QQScLauncher.exe",
        ],
        "lnk": ["腾讯QQ", "QQ", "腾讯TIM", "TIM"],
    },
}

CONFIG_NAME = os.path.join("config", "float.json")
# 「切到PPT」在没找到正在运行的演示程序时，去这些地方找最新的课件
PRESENTATION_EXTS = (".pptx", ".ppt", ".ppsx", ".pps", ".pptm")
PPT_SKIP_DIRS = {"Cache", "Code Cache", "GPUCache", "blob_storage", "Crashpad",
                 "__pycache__", "node_modules", ".git", "Partitions"}

# 进程级缓存：开始菜单扫描一次就够，网络共享盘上的开始菜单目录可能很慢
_CACHE = {"lnks": None, "resolved": {}}


def reset_cache():
    """测试用：清掉进程内缓存。"""
    _CACHE["lnks"] = None
    _CACHE["resolved"] = {}


def load_config(base_dir):
    """读 config\\float.json（可选）。坏文件按"没配"处理，不能让工具栏点不动。"""
    path = os.path.join(base_dir or "", CONFIG_NAME)
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def app_overrides(base_dir):
    """config\\float.json 里 "apps" 那一段：应用名 -> 路径。"""
    apps = load_config(base_dir).get("apps")
    if not isinstance(apps, dict):
        return {}
    return dict((key, value.strip()) for key, value in apps.items()
                if isinstance(value, str) and value.strip())


# 网页设置里能改的那几项（其余键原样保留，老师手写的注释式配置不会被抹掉）
EDITABLE_KEYS = ("qq_download_dir", "whiteboard", "display", "qq")


def config_view(base_dir):
    """给网页设置界面看的当前值（应用路径是 "apps" 里的，拍平到同一层）。"""
    payload = load_config(base_dir)
    view = {"qq_download_dir": str(payload.get("qq_download_dir") or "")}
    apps = app_overrides(base_dir)
    for key in ("whiteboard", "display", "qq"):
        view[key] = apps.get(key, "")
    return view


def save_config(base_dir, patch):
    """把网页上改的几项写回 config\\float.json（合并写，不认识的键保留）。

    返回 (是否成功, 错误消息)。
    """
    if not isinstance(patch, dict):
        return False, "配置内容不对"
    payload = load_config(base_dir)
    apps = payload.get("apps")
    if not isinstance(apps, dict):
        apps = {}
    for key in ("whiteboard", "display", "qq"):
        if key not in patch:
            continue
        value = str(patch.get(key) or "").strip()
        if value:
            apps[key] = value
        else:
            apps.pop(key, None)
    if "qq_download_dir" in patch:
        value = str(patch.get("qq_download_dir") or "").strip().strip('"')
        if value and not os.path.isdir(os.path.expandvars(value)):
            return False, "这个文件夹不存在：%s" % value
        payload["qq_download_dir"] = value
    if apps:
        payload["apps"] = apps
    else:
        payload.pop("apps", None)
    path = os.path.join(base_dir or "", CONFIG_NAME)
    try:
        directory = os.path.dirname(path)
        if directory and not os.path.isdir(directory):
            os.makedirs(directory)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    except OSError as exc:
        return False, "写不进 config\\float.json：%s" % exc
    reset_cache()
    return True, ""


def _start_menu_dirs():
    dirs = [
        os.path.join(os.environ.get("ProgramData") or r"C:\ProgramData",
                     "Microsoft", "Windows", "Start Menu", "Programs"),
        os.path.join(os.environ.get("APPDATA") or "", "Microsoft", "Windows",
                     "Start Menu", "Programs"),
        os.path.join(os.environ.get("USERPROFILE") or "", "Desktop"),
        os.path.join(os.environ.get("PUBLIC") or r"C:\Users\Public", "Desktop"),
    ]
    return [item for item in dirs if item and os.path.isdir(item)]


def start_menu_links():
    """开始菜单 + 桌面上的快捷方式：[(文件名（不含扩展名）, 完整路径)]。"""
    if _CACHE["lnks"] is not None:
        return _CACHE["lnks"]
    found = []
    for root in _start_menu_dirs():
        for base, dirs, files in os.walk(root):
            dirs[:] = [name for name in dirs if not name.startswith(".")]
            for name in files:
                if name.lower().endswith(".lnk"):
                    found.append((name[:-4], os.path.join(base, name)))
    # 一个快捷方式都没扫到（开始菜单还没建好/被安全软件挡住）就先别缓存，下次再扫
    if found:
        _CACHE["lnks"] = found
    return found


def _from_exes(patterns):
    for pattern in patterns:
        expanded = os.path.expandvars(pattern)
        if not expanded or "%" in expanded:
            continue
        if "*" in expanded or "?" in expanded:
            for hit in sorted(glob.glob(expanded)):
                if os.path.isfile(hit):
                    return hit
            continue
        if os.path.isfile(expanded):
            return expanded
    return ""


def _from_links(keywords):
    links = start_menu_links()
    for keyword in keywords:
        needle = keyword.lower()
        for name, path in links:
            if name.lower() == needle:
                return path
    for keyword in keywords:
        needle = keyword.lower()
        for name, path in links:
            if needle in name.lower():
                return path
    return ""


def resolve(key, base_dir="", use_cache=True):
    """返回这个应用的可执行文件/快捷方式路径；找不到返回空字符串。"""
    if key not in APPS:
        return ""
    # config\float.json 每次都读：老师改完存盘，下一次点按钮就生效，不用重启服务
    configured = (app_overrides(base_dir).get(key) or "").strip()
    if configured:
        candidate = os.path.expandvars(configured.strip('"'))
        if os.path.exists(candidate):
            return candidate
    if use_cache and key in _CACHE["resolved"]:
        return _CACHE["resolved"][key]
    target = _from_exes(APPS[key]["exes"]) or _from_links(APPS[key]["lnk"])
    # 只缓存"找到了"的结果：老师完全可能开着服务去装希沃白板，
    # 把一次没找到缓存一辈子的话，他点多少次都还是"没找到"，只能重启服务。
    if use_cache and target:
        _CACHE["resolved"][key] = target
    return target


def open_path(path):
    """用系统默认方式打开一个文件/文件夹/快捷方式。"""
    try:
        os.startfile(path)                                    # noqa: S606 - 本地可信路径
    except Exception as exc:                                  # noqa: BLE001
        return False, "打不开 %s：%s" % (os.path.basename(path), exc)
    return True, path


def open_app(key, base_dir=""):
    """打开一个外部应用，返回 (是否成功, 给老师看的一句话)。"""
    spec = APPS.get(key)
    if spec is None:
        return False, "工具栏不认识这个应用：%s" % key
    target = resolve(key, base_dir)
    if not target:
        return False, ("这台电脑上没找到「%s」。装好了还是找不到的话，"
                       "把它的 exe 路径填进 config\\float.json，例如："
                       '{"apps": {"%s": "D:\\\\软件\\\\xxx.exe"}}' % (spec["label"], key))
    ok, detail = open_path(target)
    if not ok:
        return False, detail
    return True, "已打开%s" % spec["label"]


# --------------------------------------------------------------------------
# 「切到PPT」的后半截：任务栏里没有演示程序时，打开最近收到的那份课件
# --------------------------------------------------------------------------
def _qq_receive_dirs():
    """QQ 收到文件可能放在哪。

    QQNT 的下载路径存在它自己的数据库里，外面读不到，所以这里是**尽力而为**的猜测：
    经典 QQ 的 FileRecv、QQNT 的 nt_file、以及 Documents\\Tencent Files 本身。
    猜不中也没关系——config\\float.json 里的 qq_download_dir 优先于这里。
    """
    home = os.path.expanduser("~")
    found = []
    tencent = os.path.join(home, "Documents", "Tencent Files")
    if os.path.isdir(tencent):
        try:
            names = os.listdir(tencent)
        except OSError:
            names = []
        for name in names:
            base = os.path.join(tencent, name)
            if not os.path.isdir(base):
                continue
            for sub in (("FileRecv",), ("nt_qq", "nt_file"), ("nt_qq", "nt_file", "recv")):
                candidate = os.path.join(base, *sub)
                if os.path.isdir(candidate):
                    found.append(candidate)
        found.append(tencent)
    found.append(os.path.join(home, "Downloads"))
    return found


def configured_ppt_dir(base_dir=""):
    """老师在 config\\float.json 里指定的课件目录（没填或路径不存在就返回空）。"""
    configured = (load_config(base_dir).get("qq_download_dir") or "").strip()
    if not configured:
        return ""
    expanded = os.path.expandvars(configured.strip('"'))
    return expanded if os.path.isdir(expanded) else ""


def presentation_dirs(base_dir=""):
    """该去哪些目录找课件。

    **填了 qq_download_dir 就只看那一个目录**：老师既然明确说了"QQ 的文件在这"，
    再跑去 Downloads 里翻一个不相干的旧课件打开，比找不到更糟。
    """
    configured = configured_ppt_dir(base_dir)
    if configured:
        return [configured]
    return [path for path in _qq_receive_dirs() if os.path.isdir(path)]


def newest_presentation(base_dir="", max_depth=4):
    """最近改动过的 .ppt/.pptx（只在上面那些目录里找，最多往下 4 层）。"""
    best, best_time = "", -1.0
    for root in presentation_dirs(base_dir):
        root = root.rstrip("\\/")
        for base, dirs, files in os.walk(root):
            if base.count(os.sep) - root.count(os.sep) >= max_depth:
                dirs[:] = []
            else:
                dirs[:] = [name for name in dirs
                           if name not in PPT_SKIP_DIRS and not name.startswith(".")]
            for name in files:
                if name.startswith("~$"):            # PowerPoint 开着时的临时文件
                    continue
                if os.path.splitext(name)[1].lower() not in PRESENTATION_EXTS:
                    continue
                full = os.path.join(base, name)
                try:
                    stamp = os.path.getmtime(full)
                except OSError:
                    continue
                if stamp > best_time:
                    best, best_time = full, stamp
    return best


def open_latest_presentation(base_dir=""):
    """用系统默认应用（老师机器上通常是 WPS 演示）打开最近收到的那份课件。"""
    target = newest_presentation(base_dir)
    if not target:
        return False, ("没找到课件。QQ 把收到的文件放在哪，可以在 config\\float.json 里加一行 "
                       '"qq_download_dir": "D:\\\\QQ文件"；或者先把课件打开再点「切到PPT」。')
    ok, detail = open_path(target)
    if not ok:
        return False, detail
    return True, "正在用默认应用打开 %s" % os.path.basename(target)
