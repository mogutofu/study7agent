# -*- coding: utf-8 -*-
"""下载：图片 / 文件 / 视频。

为什么单独一个模块：下载跟"读网页"不是一回事——它要落盘、要限量、要能取消、
要能后台跑（一个 300MB 的视频不能让整个对话卡在那里等着）。
这里只做最要紧的几件事，全部用标准库：

  - **有界**：边下边计数，超过上限立刻断开（借用 DSH web-fetch-http 的做法：
    Content-Length 声明超限就直接拒，流式增长超限才截断，并且**分开报告**是哪种）；
  - **落盘安全**：先写 .part 再原子改名（`os.replace`）。Win7 上改名经常撞
    EACCES/EBUSY/EPERM（杀毒软件、资源管理器正开着），所以带一张重试表
    （对照 DSH atomic-write：20ms 起、上限 200ms、最多 8 次）；
  - **能取消**：cancel 置位时立刻停（老师点了停止，不该还在偷偷下 300MB）；
  - **诚实**：拿到的是网页而不是图片/视频时说清楚，不让模型以为下到了东西。
"""

import hashlib
import os
import json
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

# 默认上限：单个文件 200MB；调用方可以调小，但不能超过硬顶 2048MB
DEFAULT_MAX_BYTES = 200 * 1024 * 1024
HARD_MAX_BYTES = 2048 * 1024 * 1024
# 一次读多少字节：太大占内存，太小慢
CHUNK_SIZE = 64 * 1024
# URL 长度上限（对照 DSH：2048）
MAX_URL_LENGTH = 2048
DEFAULT_TIMEOUT = 60
# Windows 上"临时改名失败但要重试"的错误码（对照 DSH atomic-write 的常量表）
WINDOWS_TRANSIENT_ERRS = (13, 32, 36)     # EACCES / EBUSY(32) / EPERM
ATOMIC_RETRY_DELAYS = (0.02, 0.04, 0.08, 0.12, 0.16, 0.2, 0.2, 0.2)

USER_AGENT = ("Mozilla/5.0 (Windows NT 6.1; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36")

# 常见类型 → 扩展名（服务端不给文件名时靠它猜）
CONTENT_TYPES = {
    "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png", "image/gif": ".gif",
    "image/webp": ".webp", "image/bmp": ".bmp", "image/svg+xml": ".svg",
    "video/mp4": ".mp4", "video/webm": ".webm", "video/x-msvideo": ".avi",
    "video/quicktime": ".mov", "video/x-matroska": ".mkv", "video/mpeg": ".mpg",
    "audio/mpeg": ".mp3", "audio/mp4": ".m4a", "audio/wav": ".wav", "audio/x-wav": ".wav",
    "audio/ogg": ".ogg", "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/zip": ".zip", "application/x-msdownload": ".exe",
    "text/plain": ".txt", "text/csv": ".csv", "application/json": ".json",
}
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
VIDEO_EXTS = (".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".mpg", ".mpeg", ".ts")
AUDIO_EXTS = (".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".wma")
DOC_EXTS = (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".md", ".csv")


class DownloadError(Exception):
    pass


def _as_mapping(value):
    """把可能是 dict / JSON 字符串 / 键值对数组的参数统一成 dict，其它一律当空。

    下游都是 `x.update(...)`：如果传进来的是字符串，dict.update 会抛
    dictionary update sequence element #0 has length 1; 2 is required —— 这就是 AI 工具调用
    报那个错的原因，所以在这里统一兜住。
    """
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    if isinstance(value, (list, tuple)):
        try:
            return dict(value)
        except Exception:
            return {}
    return {}


def human_size(size):
    size = float(size or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return ("%.0f %s" % (size, unit)) if unit in ("B", "KB") else ("%.1f %s" % (size, unit))
        size /= 1024.0
    return "%.1f GB" % size


def kind_of(path, content_type=""):
    """粗分类：图片 / 视频 / 音频 / 文档 / 其它。给模型和界面判断"下到了什么"。"""
    ext = os.path.splitext(str(path or ""))[1].lower()
    ctype = (content_type or "").split(";")[0].strip().lower()
    if ext in IMAGE_EXTS or ctype.startswith("image/"):
        return "image"
    if ext in VIDEO_EXTS or ctype.startswith("video/"):
        return "video"
    if ext in AUDIO_EXTS or ctype.startswith("audio/"):
        return "audio"
    if ext in DOC_EXTS or ctype in ("application/pdf",) or "officedocument" in ctype:
        return "doc"
    return "other"


def filename_from(url, content_disposition="", content_type="", fallback="下载文件"):
    """决定存成什么名字：优先服务端给的文件名，其次 URL 里的，最后按类型编一个。"""
    name = ""
    if content_disposition:
        # filename*=UTF-8''%E8%AF%BE%E4%BB%B6.pdf  或 filename="课件.pdf"
        match = re.search(r"filename\*\s*=\s*([^;]+)", content_disposition, re.I)
        if match:
            raw = match.group(1).strip().strip('"')
            if "''" in raw:
                raw = raw.split("''", 1)[1]
            try:
                name = urllib.parse.unquote(raw)
            except Exception:
                name = raw
        if not name:
            match = re.search(r'filename\s*=\s*"([^"]+)"', content_disposition, re.I) or \
                re.search(r"filename\s*=\s*([^;]+)", content_disposition, re.I)
            if match:
                name = match.group(1).strip().strip('"')
    if not name:
        path = urllib.parse.urlparse(url).path
        name = urllib.parse.unquote(os.path.basename(path))
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", name).strip(" .")
    if not name:
        name = fallback
    if not os.path.splitext(name)[1]:
        ext = CONTENT_TYPES.get((content_type or "").split(";")[0].strip().lower(), "")
        name += ext
    if len(name) > 120:
        stem, ext = os.path.splitext(name)
        name = stem[:110] + ext
    return name


def atomic_replace(source, target):
    """把 .part 改名成正式文件；Win7 上撞到"文件被占用"会重试几次。"""
    last = None
    for delay in (0.0,) + ATOMIC_RETRY_DELAYS:
        if delay:
            time.sleep(delay)
        try:
            os.replace(source, target)
            return ""
        except OSError as exc:
            last = exc
            if getattr(exc, "errno", None) not in WINDOWS_TRANSIENT_ERRS and os.name == "nt":
                # 不是"临时占用"类的错误（比如目标目录不存在），没必要重试
                break
    return "改名失败：%s" % (last or "未知错误")


def _ssl_context():
    try:
        return ssl.create_default_context()
    except Exception:
        return None


def download(url, path, max_bytes=DEFAULT_MAX_BYTES, timeout=DEFAULT_TIMEOUT, cancel=None,
             referer="", insecure=False, extra_headers=None):
    """把一个 URL 下到 path。返回 (info, error)。

    info: {path, bytes, kind, content_type, sha256, truncated, resumable, seconds, url, note}
    """
    url = str(url or "").strip()
    if not url:
        return None, "没给下载地址"
    if len(url) > MAX_URL_LENGTH:
        return None, "这个网址太长了（%d 字符，超过 %d）" % (len(url), MAX_URL_LENGTH)
    if not re.match(r"^https?://", url, re.I):
        return None, "下载地址必须以 http:// 或 https:// 开头：%s" % url
    try:
        max_bytes = int(max_bytes or DEFAULT_MAX_BYTES)
    except (TypeError, ValueError):
        max_bytes = DEFAULT_MAX_BYTES
    max_bytes = max(64 * 1024, min(HARD_MAX_BYTES, max_bytes))

    headers = {"User-Agent": USER_AGENT,
               "Accept": "*/*",
               "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}
    if referer:
        headers["Referer"] = referer
    headers.update(_as_mapping(extra_headers))
    request = urllib.request.Request(url, headers=headers)
    context = ssl.create_default_context() if url.lower().startswith("https") else None
    if insecure and context is not None:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE

    part_path = path + ".part"
    directory = os.path.dirname(path)
    if directory and not os.path.isdir(directory):
        try:
            os.makedirs(directory)
        except OSError as exc:
            return None, "保存目录建不出来：%s" % exc

    started = time.time()
    digest = hashlib.sha256()
    got = 0
    declared = 0
    content_type = ""
    disposition = ""
    truncated = False
    error = ""
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            content_type = response.headers.get("Content-Type") or ""
            disposition = response.headers.get("Content-Disposition") or ""
            try:
                declared = int(response.headers.get("Content-Length") or 0)
            except (TypeError, ValueError):
                declared = 0
            # 声明就超限：直接拒绝，别浪费时间下（对照 DSH：WEB_FETCH_TOO_LARGE）
            if declared and declared > max_bytes:
                return None, ("这个文件有 %s，超过本次允许的 %s（可以调大 max_mb，最多 %s）"
                              % (human_size(declared), human_size(max_bytes),
                                 human_size(HARD_MAX_BYTES)))
            with open(part_path, "wb") as handle:
                while True:
                    if cancel is not None and cancel.is_set():
                        error = "已被停止"
                        break
                    chunk = response.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    got += len(chunk)
                    if got > max_bytes:
                        # 流式下超限：留下已经下到的部分（正好 max_bytes），并标记"没下完"。
                        # 注意：即使这一块整块都超出（keep<=0），got 也要归到 max_bytes，
                        # 否则报出去的字节数会比上限还大（实测踩过）。
                        keep = max_bytes - (got - len(chunk))
                        if keep > 0:
                            handle.write(chunk[:keep])
                            digest.update(chunk[:keep])
                        got = max_bytes
                        truncated = True
                        break
                    handle.write(chunk)
                    digest.update(chunk)
    except urllib.error.HTTPError as exc:
        _cleanup(part_path)
        hint = {403: "服务器拒绝访问（可能有防盗链，试试带 referer）",
                404: "这个地址不存在（404）",
                401: "需要登录才能下", 429: "被限流了，过一会儿再试"}.get(exc.code, "")
        return None, "下载失败：HTTP %s %s%s" % (exc.code, exc.reason, ("，" + hint) if hint else "")
    except urllib.error.URLError as exc:
        _cleanup(part_path)
        return None, "下载失败：连不上（%s）。内网机器可能需要代理。" % exc.reason
    except (OSError, ValueError) as exc:
        _cleanup(part_path)
        return None, "下载失败：%s" % exc

    if error:
        _cleanup(part_path)
        return None, "下载中断：%s（已经下的部分已删掉，避免留下半截文件）" % error

    seconds = round(time.time() - started, 2)
    if got == 0:
        _cleanup(part_path)
        return None, "下载到的内容是空的（0 字节），这个地址可能不对"

    note = ""
    if truncated:
        note = ("只下了前 %s（超过本次上限 %s，已截断）。要完整的就调大 max_mb。"
                % (human_size(got), human_size(max_bytes)))
    final_name = filename_from(url, disposition, content_type)
    replace_error = atomic_replace(part_path, path)
    if replace_error:
        _cleanup(part_path)
        return None, "文件下完了但保存失败：%s（多半是杀毒软件或资源管理器正占用这个文件，稍后再试）" % replace_error

    return {"path": path, "name": final_name, "bytes": got, "declared": declared,
            "kind": kind_of(path, content_type), "content_type": content_type,
            "sha256": digest.hexdigest(), "truncated": truncated, "seconds": seconds,
            "url": url, "note": note,
            "html_warning": ("这个地址返回的是网页（text/html），不是图片/视频/文件。"
                             "如果是要下页面里的素材，先用 read_url 看看页面里有什么，"
                             "或者把图片/视频的真实地址给我。"
                             if content_type.lower().startswith("text/html") else "")}, ""


def _cleanup(path):
    try:
        if os.path.isfile(path):
            os.remove(path)
    except OSError:
        pass
