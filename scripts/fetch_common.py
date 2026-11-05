# -*- coding: utf-8 -*-
"""取数脚本的公共件：HTTP 探测、断点续传、哈希、体积显示。

只给 scripts\\fetch-chromium.py 和 scripts\\fetch-chem-data.py 用，
用随包 Python 3.8 的标准库，不许 pip。

两个脚本都要在开发机上联网跑（教室机器完全离线），所以这里只关心
「通道通不通、能不能续、内容对不对」，不关心速度以外的体验。
"""

import hashlib
import os
import shutil
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

# 有些站点（Wikidata、PubChem）对没有 UA 的请求直接 403，UA 必须带。
DEFAULT_UA = ("Mozilla/5.0 (Windows NT 6.1; Win64; x64) Study7Agent-build/1.1 "
              "(offline classroom package; contact: local build script)")
PROBE_BYTES = 65536
DEFAULT_TIMEOUT = 25


def safe(text):
    """把网络来的文字压成纯 ASCII，避免控制台按 GBK 编码时直接抛异常。"""
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    return (text or "").encode("ascii", "replace").decode("ascii")


def human(size):
    value = float(size or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return ("%.1f %s" % (value, unit)) if unit != "B" else ("%d B" % value)
        value /= 1024.0
    return "%.1f GB" % value


def request(url, headers=None, timeout=DEFAULT_TIMEOUT, method="GET"):
    merged = {"User-Agent": DEFAULT_UA, "Accept": "*/*"}
    if headers:
        merged.update(headers)
    return urllib.request.Request(url, headers=merged, method=method)


def probe(url, timeout=DEFAULT_TIMEOUT, want=PROBE_BYTES):
    """只取开头一小段，确认通道能通、能拿到多少字节、要多久。

    返回 dict：ok / status / total / got / seconds / head / error。
    服务端不支持 Range 时会老老实实把整个包下下来，所以这里读够 want 就断开。
    """
    result = {"url": url, "ok": False, "status": 0, "total": 0, "got": 0,
              "seconds": 0.0, "head": "", "error": ""}
    started = time.time()
    try:
        with urllib.request.urlopen(
                request(url, {"Range": "bytes=0-%d" % (want - 1)}), timeout=timeout) as response:
            result["status"] = response.status
            result["total"] = total_of(response)
            chunk = response.read(want)
            result["got"] = len(chunk)
            result["head"] = safe(chunk[:96])
            result["ok"] = bool(chunk)
    except urllib.error.HTTPError as exc:
        result["status"] = exc.code
        result["error"] = "HTTP %s" % exc.code
    except Exception as exc:  # 网络类异常五花八门，探测阶段一律收成一行
        result["error"] = "%s: %s" % (type(exc).__name__, safe(str(exc))[:120])
    result["seconds"] = time.time() - started
    return result


def total_of(response):
    """从 Content-Range 里问出总大小；没有就看 Content-Length。

    实测 GitHub 发行包对 Range 请求只回 Content-Range 不回 Content-Length，
    早先版本的探针因此把总大小显示成问号（也就没法估算要下多久）。
    """
    content_range = response.headers.get("Content-Range") or ""
    if "/" in content_range:
        tail = content_range.rsplit("/", 1)[1].strip()
        if tail.isdigit():
            return int(tail)
    length = response.headers.get("Content-Length")
    return int(length) if length and length.isdigit() else 0


def size_of(url, timeout=DEFAULT_TIMEOUT):
    """只问大小，不下载正文。"""
    try:
        with urllib.request.urlopen(request(url, {"Range": "bytes=0-0"}), timeout=timeout) as response:
            return total_of(response)
    except Exception:
        return 0


def drain(url, path, timeout=DEFAULT_TIMEOUT, limit=4 * 1024 * 1024):
    """把小文件整份取回来（SPARQL 结果、HTML 表、JSON），超过 limit 就放弃。"""
    try:
        with urllib.request.urlopen(request(url), timeout=timeout) as response:
            data = response.read(limit + 1)
            if len(data) > limit:
                return None, "超过 %s 上限" % human(limit)
    except urllib.error.HTTPError as exc:
        return None, "HTTP %s" % exc.code
    except Exception as exc:
        return None, "%s: %s" % (type(exc).__name__, safe(str(exc))[:120])
    if path:
        folder = os.path.dirname(path)
        if folder and not os.path.isdir(folder):
            os.makedirs(folder)
        with open(path, "wb") as handle:
            handle.write(data)
    return data, ""


def fetch_to_file(url, path, timeout=DEFAULT_TIMEOUT):
    """断点续传下载。返回 (ok, message)。

    .part 临时文件是故意的：教室网/公司网经常半路断，重跑一次能接着下，
    不用把两百兆重来一遍。
    """
    part = path + ".part"
    done = os.path.getsize(part) if os.path.isfile(part) else 0
    headers = {"Accept": "*/*"}
    if done:
        headers["Range"] = "bytes=%d-" % done
    try:
        with urllib.request.urlopen(request(url, headers, timeout=timeout), timeout=timeout) as response:
            if done and response.status != 206:
                done = 0  # 服务端不认续传，从头来
            mode = "ab" if done else "wb"
            with open(part, mode) as handle:
                while True:
                    chunk = response.read(256 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
    except urllib.error.HTTPError as exc:
        return False, "HTTP %s" % exc.code
    except Exception as exc:
        return False, "%s: %s" % (type(exc).__name__, safe(str(exc))[:140])
    os.replace(part, path)
    return True, "已下载 %s" % human(os.path.getsize(path))


def fetch_parallel(url, path, connections=4, timeout=DEFAULT_TIMEOUT):
    """多连接分段下载，每段各自续传。返回 (ok, message)。

    加这个是因为实测单连接会被 CDN 限到几 KB/s（GitHub 发行包直连，12 MB 磨了
    十几分钟），而分段并行每段各走一条连接。课堂/学校网络上重建包时同样会遇到，
    所以留成显式开关（--parallel），默认还是单连接那条简单路径。

    安全性：每段写完都要长度对得上，最终整包再过一遍 SHA-256，所以并行只影响
    快慢，不影响"拿到的是不是官方那个包"。
    """
    total = size_of(url, timeout=timeout)
    if total <= 0:
        return fetch_to_file(url, path, timeout=timeout)
    span = total // connections + 1
    parts = []
    for index in range(connections):
        start = index * span
        if start >= total:
            break
        parts.append((index, start, min(start + span, total) - 1, "%s.part%d" % (path, index)))
    errors = []
    baseline = sum(os.path.getsize(item[3]) for item in parts if os.path.isfile(item[3]))

    def worker(item):
        index, start, end, part = item
        want = end - start + 1
        have = os.path.getsize(part) if os.path.isfile(part) else 0
        if have >= want:
            return
        try:
            with urllib.request.urlopen(
                    request(url, {"Range": "bytes=%d-%d" % (start + have, end)}, timeout=timeout),
                    timeout=timeout) as response:
                with open(part, "ab") as handle:
                    while True:
                        block = response.read(256 * 1024)
                        if not block:
                            break
                        handle.write(block)
        except Exception as exc:
            errors.append("段%d %s" % (index, safe(str(exc))[:70]))

    threads = [threading.Thread(target=worker, args=(item,), daemon=True) for item in parts]
    started = time.time()
    for thread in threads:
        thread.start()
    while any(thread.is_alive() for thread in threads):
        time.sleep(5)
        got = sum(os.path.getsize(item[3]) for item in parts if os.path.isfile(item[3]))
        rate = (got - baseline) / max(0.001, time.time() - started)
        sys.stdout.write("  已下 %s / %s，%.0f KB/s\n" % (human(got), human(total), rate / 1024.0))
        sys.stdout.flush()
    for thread in threads:
        thread.join()
    if errors:
        return False, "；".join(errors[:3]) + "（重跑本命令会接着下）"
    for index, start, end, part in parts:
        have = os.path.getsize(part) if os.path.isfile(part) else 0
        if have != end - start + 1:
            return False, "段%d 只下到 %s / %s（重跑本命令会接着下）" % (
                index, human(have), human(end - start + 1))
    merged = path + ".part"
    with open(merged, "wb") as out:
        for _index, _start, _end, part in parts:
            with open(part, "rb") as source:
                shutil.copyfileobj(source, out, 1024 * 1024)
    for _index, _start, _end, part in parts:
        os.remove(part)
    os.replace(merged, path)
    return True, "已下载 %s（%d 段并行）" % (human(os.path.getsize(path)), len(parts))


def sha256_file(path, block=1024 * 1024):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(block)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def report(rows, title):
    """把探测结果打成一张能贴进文档的表。

    标题、地址这些是自己写的中文，直接打；只有网络来的片段（head / 报错）
    过一遍 safe()，免得控制台按 GBK 编码时被一个怪字节整崩。
    """
    lines = ["", title]
    for row in rows:
        state = "OK " if row["ok"] else "FAIL"
        if row["ok"]:
            extra = "%s / %s in %.1fs" % (human(row["got"]), human(row["total"]) if row["total"] else "?",
                                          row["seconds"])
        else:
            extra = safe(row["error"]) or "无数据"
        lines.append("  [%s] %s" % (state, extra))
        lines.append("        %s" % row["url"])
        if row["head"]:
            lines.append("        head: %s" % safe(row["head"])[:72])
    text = "\n".join(lines)
    try:
        sys.stdout.write(text + "\n")
    except UnicodeEncodeError:
        sys.stdout.write(safe(text) + "\n")
    sys.stdout.flush()
    return text


def quote(url):
    """SPARQL 查询串要塞进 URL，这里统一按 RFC 3986 编码。"""
    return urllib.parse.quote(url, safe="")
