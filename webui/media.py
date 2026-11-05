# -*- coding: utf-8 -*-
"""音视频与课件体检：找出"视频放不了"的真实原因。

老师的场景：课件（pptx）在别的电脑上好好的，拷到教室一体机上视频放不出来。原因通常是这几类：

  1. **视频根本没跟着文件走**：pptx 里是"链接"（外链）而不是内嵌，原视频在原来那台电脑上；
  2. **编码不兼容**：视频是 H.265(HEVC)/AV1/VP9/ProRes，或者 WMV/老 AVI(DIVX/XVID)，
     新老 PowerPoint 与 Win7 自带播放器都放不了（PPT 最稳的是 MP4 + H.264 + AAC）；
  3. **文件损坏**：pptx 本身是个 zip，媒体部件缺失/CRC 错误，PowerPoint 直接报"无法播放媒体"；
  4. **路径/字体问题**：外链路径带盘符、字体缺失导致排版崩（本模块也会顺带报告）。

只使用 Python 3.8 标准库：容器解析是自己写的（ISO-BMFF / RIFF-AVI / ASF-WMV / EBML-MKV），
不做转码——转码需要 ffmpeg，本模块会检测系统里有没有 ffmpeg 并给出可执行的建议命令。
"""

import os
import re
import shutil
import struct
import subprocess
import threading
import zipfile

# 单个媒体部件超过这个大小就不做内容检查（也不能整个读进内存）：
# 教室机器只有 2-4G 内存，课件里塞一个 1G 的视频会把机器拖死。
MAX_MEDIA_BYTES = 512 * 1024 * 1024

# PPT / Win7 上"能不能放"的判定表：结论 + 建议
VIDEO_CODECS = {
    b"avc1": ("H.264", True, ""),
    b"avc3": ("H.264", True, ""),
    b"hev1": ("H.265/HEVC", False, "HEVC 需要额外解码器，Win7 自带播放器和多数老版 PowerPoint 放不了"),
    b"hvc1": ("H.265/HEVC", False, "HEVC 需要额外解码器，Win7 自带播放器和多数老版 PowerPoint 放不了"),
    b"av01": ("AV1", False, "AV1 在 Win7 上基本没有可用的解码器"),
    b"vp09": ("VP9", False, "VP9 在 PowerPoint 里支持很差，建议转 H.264"),
    b"mp4v": ("MPEG-4 第 2 部分", False, "老编码，PowerPoint 支持不稳定，建议转 H.264"),
    b"apch": ("ProRes", False, "ProRes 是剪辑用格式，PowerPoint 放不了"),
    b"jpeg": ("Motion JPEG", False, "动态 JPEG 在 PowerPoint 里通常放不了"),
}
AUDIO_CODECS = {
    b"mp4a": ("AAC", True, ""),
    b"ac-3": ("AC-3", False, "AC-3 需要额外解码器，建议换成 AAC"),
    b"ec-3": ("E-AC-3", False, "E-AC-3 需要额外解码器，建议换成 AAC"),
    b"alac": ("Apple Lossless", False, "建议换成 AAC"),
    b"Opus": ("Opus", False, "Opus 在 PowerPoint 里支持很差，建议换成 AAC"),
    b"twos": ("PCM", True, ""),
    b"sowt": ("PCM", True, ""),
}
# AVI/WMV 的四字符编码
AVI_CODECS = {
    "H264": ("H.264", True, ""), "X264": ("H.264", True, ""), "AVC1": ("H.264", True, ""),
    "DIVX": ("DivX (MPEG-4)", False, "DivX 需要单独装解码器，教室机器通常没有"),
    "DX50": ("DivX 5", False, "DivX 需要单独装解码器，教室机器通常没有"),
    "XVID": ("Xvid", False, "Xvid 需要单独装解码器，教室机器通常没有"),
    "MP42": ("MS MPEG-4 v2", False, "老编码，建议转 H.264"),
    "MP43": ("MS MPEG-4 v3", False, "老编码，建议转 H.264"),
    "WMV3": ("WMV 9", False, "WMV 依赖系统解码器，拷到别的机器常常放不了"),
    "WMV2": ("WMV 8", False, "WMV 依赖系统解码器，拷到别的机器常常放不了"),
    "MJPG": ("Motion JPEG", False, "建议转 H.264"),
    "IV50": ("Indeo 5", False, "Indeo 早就停止支持了，必须转码"),
    "CVID": ("Cinepak", False, "Cinepak 早已淘汰，必须转码"),
}
MEDIA_EXTS = {
    ".mp4": "video", ".m4v": "video", ".mov": "video", ".m4a": "audio", ".m4b": "audio",
    ".avi": "video", ".wmv": "video", ".asf": "video", ".mkv": "video", ".webm": "video",
    ".mp3": "audio", ".wav": "audio", ".flac": "audio", ".ogg": "audio", ".aac": "audio",
    ".wma": "audio", ".3gp": "video", ".mpg": "video", ".mpeg": "video", ".rmvb": "video",
}


def media_ext(path):
    return os.path.splitext(str(path or ""))[1].lower()


def is_media_path(path):
    return media_ext(path) in MEDIA_EXTS


def _read(path, size, offset=0):
    with open(path, "rb") as handle:
        handle.seek(offset)
        return handle.read(size)


def inspect_media(path):
    """识别容器/编码/时长/分辨率，并给出"Win7 + PowerPoint 能不能放"的结论。"""
    result = {
        "path": path, "kind": MEDIA_EXTS.get(media_ext(path), ""),
        "container": "", "video": "", "audio": "", "duration": 0.0,
        "width": 0, "height": 0, "bytes": 0, "playable": False,
        "problems": [], "advice": [], "ok": True,
    }
    try:
        result["bytes"] = os.path.getsize(path)
    except OSError as exc:
        result["ok"] = False
        result["problems"].append("读不到文件：%s" % exc)
        return result
    if result["bytes"] == 0:
        result["ok"] = False
        result["problems"].append("文件是 0 字节（多半是拷贝时中断了）")
        return result
    head = _read(path, 16)
    try:
        if head[4:8] == b"ftyp":
            _parse_isobmff(path, result)
        elif head[:4] == b"RIFF" and head[8:12] == b"AVI ":
            _parse_avi(path, result)
        elif head[:4] == b"RIFF" and head[8:12] == b"WAVE":
            _parse_wave(path, result)
        elif head[:16].startswith(b"\x30\x26\xb2\x75"):
            _parse_asf(path, result)
        elif head[:4] == b"\x1a\x45\xdf\xa3":
            _parse_mkv(path, result)
        elif head[:3] == b"ID3" or head[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
            _parse_mp3(path, result)
        elif head[:4] == b"fLaC":
            result["container"] = "FLAC"
            result["audio"] = "FLAC"
            result["problems"].append("FLAC 是音频，PowerPoint 插不进去（需要转成 MP3/WAV）")
        elif head[:8] == b"\x89PNG\r\n\x1a\n" or head[:3] == b"\xff\xd8\xff":
            result["ok"] = False
            result["problems"].append("这是图片，不是音视频")
        else:
            result["ok"] = False
            result["problems"].append("认不出这个容器格式（文件可能已损坏，或不是常见的 mp4/avi/wmv/mkv）")
    except (OSError, struct.error, ValueError) as exc:
        result["ok"] = False
        result["problems"].append("解析失败：%s（文件可能被截断或损坏）" % exc)
    _verdict(path, result)
    return result


def _verdict(path, result):
    if not result["ok"]:
        return
    playable = True
    video = result.get("video") or ""
    audio = result.get("audio") or ""
    if video and not result.get("judged"):
        # 容器解析器已经判断过编码（avi/wmv/mkv 分支会自己给结论），避免重复报同一句
        for codec, (name, fine, why) in VIDEO_CODECS.items():
            if video == name:
                if not fine:
                    playable = False
                    result["problems"].append("视频编码 %s：%s" % (name, why))
                break
        else:
            playable = False
            result["problems"].append("视频编码 %s 不在常见兼容列表里，PowerPoint 可能放不了" % video)
    if audio:
        for codec, (name, fine, why) in AUDIO_CODECS.items():
            if audio == name:
                if not fine:
                    result["problems"].append("音频编码 %s：%s" % (name, why))
                break
    if not video and result["kind"] == "video":
        result["problems"].append("这个文件里没有视频轨（可能只有声音，或者视频轨已损坏）")
        playable = False
    if result["kind"] == "audio" and not audio:
        result["problems"].append("没找到音频轨")
        playable = False
    result["playable"] = playable and not result["problems"]
    if result["problems"]:
        ffmpeg = which_ffmpeg()
        if ffmpeg:
            result["advice"].append('本机有 ffmpeg，可以直接转码：%s -i "%s" -c:v libx264 -profile:v high '
                                    '-pix_fmt yuv420p -c:a aac -b:a 128k "输出.mp4"' % (ffmpeg, path))
        else:
            result["advice"].append("本机没有 ffmpeg。最省事的办法：用「格式工厂 / QQ影音 / 剪映」把视频转成 "
                                    "MP4(H.264)+AAC 再插回课件；或用 PowerPoint 的"
                                    "「文件→信息→压缩媒体→演示文稿质量」重新压缩一次。")
        result["advice"].append("PPT 官方推荐：MP4 容器 + H.264 视频 + AAC 音频，分辨率 1080p 以内。")


def which_ffmpeg():
    """系统里有 ffmpeg 就返回路径（只做检测，不随包分发）。"""
    for name in ("ffmpeg", "ffmpeg.exe"):
        for folder in (os.environ.get("PATH") or "").split(os.pathsep):
            candidate = os.path.join(folder, name)
            if os.path.isfile(candidate):
                return candidate
    return ""


def _boxes(path, start, end, depth=0, want=6):
    """遍历 ISO-BMFF 的 box，遇到想要的类型就回调（这里用简单递归收集）。"""
    found = []
    size_total = os.path.getsize(path)
    offset = start
    while offset + 8 <= min(end, size_total) and depth < want:
        header = _read(path, 8, offset)
        if len(header) < 8:
            break
        size = struct.unpack(">I", header[:4])[0]
        kind = header[4:8]
        body = offset + 8
        if size == 1:
            size = struct.unpack(">Q", _read(path, 8, offset + 8))[0]
            body = offset + 16
        elif size == 0:
            size = size_total - offset
        if size < 8:
            break
        found.append((kind, body, offset + size))
        offset += size
    return found


def _find_box(path, start, end, names, depth=0):
    for kind, body, stop in _boxes(path, start, end, depth):
        if kind in names:
            return kind, body, stop
        for container in (b"moov", b"trak", b"mdia", b"minf", b"stbl", b"udta", b"edts"):
            if kind == container and depth < 6:
                inner = _find_box(path, body, stop, names, depth + 1)
                if inner:
                    return inner
    return None


def _parse_isobmff(path, result):
    result["container"] = "MP4/MOV (ISO-BMFF)"
    end = os.path.getsize(path)
    moov = _find_box(path, 0, end, (b"moov",))
    if not moov:
        result["ok"] = False
        result["problems"].append("文件缺少 moov 索引（视频文件的「目录」）——说明这个文件不完整，"
                                  "通常是拷贝/下载/导出中途断了；请重新拷一份原视频")
        return
    mvhd = _find_box(path, moov[1], moov[2], (b"mvhd",))
    if mvhd:
        body = mvhd[1]
        version = _read(path, 1, body)[0]
        if version == 1:
            data = _read(path, 28, body)
            if len(data) >= 28:
                timescale = struct.unpack(">I", data[12:16])[0] or 1
                result["duration"] = struct.unpack(">Q", data[20:28])[0] / float(timescale)
        else:
            data = _read(path, 20, body)
            if len(data) >= 20:
                timescale = struct.unpack(">I", data[12:16])[0] or 1
                result["duration"] = struct.unpack(">I", data[16:20])[0] / float(timescale)
    tkhd = _find_box(path, moov[1], moov[2], (b"tkhd",))
    if tkhd:
        body = tkhd[1]
        version = _read(path, 1, body)[0]
        if version == 1:
            data = _read(path, 32, body)
            if len(data) >= 32:
                result["width"] = struct.unpack(">I", data[24:28])[0] >> 16
                result["height"] = struct.unpack(">I", data[28:32])[0] >> 16
        else:
            data = _read(path, 20, body)
            if len(data) >= 20:
                result["width"] = struct.unpack(">I", data[12:16])[0] >> 16
                result["height"] = struct.unpack(">I", data[16:20])[0] >> 16
    # trak 在 moov 里面，必须进 moov 找（只遍历顶层 box 是找不到视频轨的）
    seen_video = False
    seen_audio = False
    for kind, body, stop in _boxes(path, moov[1], moov[2]):
        if kind != b"trak":
            continue
        stsd = _find_box(path, body, stop, (b"stsd",))
        hdlr = _find_box(path, body, stop, (b"hdlr",))
        handler = ""
        if hdlr:
            handler = _read(path, 12, hdlr[1] + 8).decode("latin-1", "replace")
        if not stsd:
            continue
        entry = _read(path, 16, stsd[1] + 8)     # stsd: version+flags(4) + count(4) + entry 头(8)
        codec = entry[4:8] if len(entry) >= 8 else b""
        if "vide" in handler and not seen_video:
            seen_video = True
            name, fine, why = VIDEO_CODECS.get(codec, (codec.decode("latin-1", "replace"), False, ""))
            result["video"] = name
            result["judged"] = True
            if not fine and why:
                result["problems"].append("视频编码 %s：%s" % (name, why))
        elif "soun" in handler and not seen_audio:
            seen_audio = True
            name, fine, why = AUDIO_CODECS.get(codec, (codec.decode("latin-1", "replace"), False, ""))
            result["audio"] = name
            if not fine and why:
                result["problems"].append("音频编码 %s：%s" % (name, why))


def _parse_avi(path, result):
    result["container"] = "AVI (RIFF)"
    size = os.path.getsize(path)
    header = _read(path, min(size, 4096))
    match = re.search(rb"avih", header)
    if match:
        data = header[match.start() + 8: match.start() + 8 + 40]
        if len(data) >= 40:
            microseconds = struct.unpack("<I", data[0:4])[0]
            total = struct.unpack("<I", data[16:20])[0]
            result["duration"] = microseconds * total / 1000000.0
            if microseconds:
                result["width"] = struct.unpack("<I", data[32:36])[0]
                result["height"] = struct.unpack("<I", data[36:40])[0]
    for fourcc, (name, fine, why) in AVI_CODECS.items():
        if fourcc.encode("ascii") in header:
            result["video"] = name
            result["judged"] = True
            if not fine:
                result["problems"].append("视频编码 %s：%s" % (name, why))
            break
    if not result["video"]:
        result["problems"].append("认不出 AVI 里的视频编码（老 AVI 常见），这类文件在教室电脑上最容易放不出来")


def _parse_wave(path, result):
    result["container"] = "WAV (RIFF)"
    result["audio"] = "PCM" if b"fmt " in _read(path, 4096) else "未知"


def _parse_asf(path, result):
    result["container"] = "ASF/WMV"
    result["video"] = "WMV (ASF)"
    result["problems"].append("WMV/ASF 依赖系统解码器：拷到没装解码包的机器上常常放不出来，"
                              "建议转成 MP4(H.264)")


def _parse_mkv(path, result):
    result["container"] = "Matroska (MKV/WebM)"
    head = _read(path, 65536)
    codecs = set(re.findall(rb"(V_MPEG4/ISO/AVC|V_MPEGH/ISO/HEVC|V_VP8|V_VP9|V_AV1|"
                            rb"A_AAC|A_OPUS|A_AC3|A_MPEG/L3)", head))
    for codec in sorted(codecs):
        text = codec.decode("latin-1")
        if text.startswith("V_"):
            name = {"V_MPEG4/ISO/AVC": "H.264", "V_MPEGH/ISO/HEVC": "H.265/HEVC",
                    "V_VP8": "VP8", "V_VP9": "VP9", "V_AV1": "AV1"}.get(text, text)
            result["video"] = name
            if name != "H.264":
                result["problems"].append("视频编码 %s：MKV 里的这种编码 PowerPoint 基本放不了" % name)
        else:
            name = {"A_AAC": "AAC", "A_OPUS": "Opus", "A_AC3": "AC-3",
                    "A_MPEG/L3": "MP3"}.get(text, text)
            result["audio"] = name
    if not codecs:
        result["problems"].append("MKV 的编码信息没读到（文件可能损坏）；另外 PowerPoint 对 MKV 支持很差，"
                                  "建议转成 MP4")
    result["problems"].append("PowerPoint 更推荐 MP4：MKV 常常插进去也播不了")


def _parse_mp3(path, result):
    result["container"] = "MP3"
    result["audio"] = "MP3"
    size = os.path.getsize(path)
    result["duration"] = round(size / 16000.0, 1)      # 128kbps 粗估


# --------------------------------------------------------------------------
# 课件（pptx）体检
# --------------------------------------------------------------------------

MEDIA_MIME = {
    ".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime",
    ".avi": "video/avi", ".wmv": "video/x-ms-wmv", ".mkv": "video/x-matroska",
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4", ".wma": "audio/x-ms-wma",
}


def diagnose_pptx(path):
    """体检一个 pptx：zip 是否完整、每页引用了哪些媒体、媒体是内嵌还是外链、编码能不能放。"""
    report = {
        "path": path, "ok": True, "slides": 0, "media": [], "problems": [],
        "external": [], "advice": [], "fonts": [], "size": 0,
    }
    try:
        report["size"] = os.path.getsize(path)
    except OSError as exc:
        report["ok"] = False
        report["problems"].append("读不到文件：%s" % exc)
        return report
    if not zipfile.is_zipfile(path):
        report["ok"] = False
        report["problems"].append("这不是一个有效的 pptx（zip 结构都不对，文件已经损坏）。"
                                  "可以试试用 PowerPoint 打开后「另存为」修复，或找回备份/快照。")
        return report
    try:
        with zipfile.ZipFile(path) as archive:
            bad = archive.testzip()
            if bad:
                report["problems"].append("压缩包内 %s 这一部分已损坏（CRC 校验不通过）" % bad)
            names = archive.namelist()
            slides = sorted(name for name in names
                            if re.match(r"ppt/slides/slide\d+\.xml$", name))
            report["slides"] = len(slides)
            if not slides:
                report["problems"].append("里面没有任何幻灯片（文件可能是空的或结构异常）")
            media_names = [name for name in names if name.startswith("ppt/media/")]
            report["media"] = _media_summary(archive, media_names)
            report["fonts"] = sorted({_font_name(archive, name) for name in names
                                      if name.startswith("ppt/fonts/")} - {""})
            for index, slide in enumerate(slides, start=1):
                _check_slide_media(archive, index, slide, report)
            # 每页的 .rels 已由 _check_slide_media 带页码处理过，这里只扫剩下的，
            # 否则同一个外链会被记两次（一次有页码，一次页码未知）。
            rels = [name for name in names if name.endswith(".rels")
                    and not name.startswith("ppt/slides/_rels/")]
            for rel in rels:
                _check_external_links(archive, rel, report)
    except (zipfile.BadZipFile, OSError, KeyError) as exc:
        report["ok"] = False
        report["problems"].append("读取课件失败：%s" % exc)
        return report
    _pptx_advice(report)
    return report


def _media_summary(archive, media_names):
    out = []
    for name in sorted(media_names):
        ext = os.path.splitext(name)[1].lower()
        if ext not in MEDIA_EXTS:
            continue
        try:
            size = archive.getinfo(name).file_size
        except (KeyError, zipfile.BadZipFile):
            size = 0
        entry = {"part": name, "ext": ext, "bytes": size, "video": "", "audio": "",
                 "duration": 0.0, "playable": True, "problems": []}
        if size > MAX_MEDIA_BYTES:
            # 教室机器只有 2-4G 内存：包里塞一个 1G 的视频不能整个读进内存，
            # 也不要偷偷写到临时目录，直接如实说明"太大了，没查内容"。
            entry["problems"].append(
                "这个媒体部件有 %.0f MB，超过检查上限（%d MB），只看了大小没看编码；"
                "换台内存大点的机器，或用「提取媒体」单独取出来检查"
                % (size / 1048576.0, MAX_MEDIA_BYTES // 1048576))
            out.append(entry)
            continue
        _inspect_part(archive, name, ext, entry)
        out.append(entry)
    return out


def _inspect_part(archive, name, ext, entry):
    """把部件流式写到临时文件再查（不整个读进内存），查完删掉。"""
    tmp_dir = os.path.join(os.environ.get("TEMP") or "/tmp", "w7a-media-probe")
    probe = ""
    try:
        if not os.path.isdir(tmp_dir):
            os.makedirs(tmp_dir)
        probe = os.path.join(tmp_dir, "probe-%d%s" % (threading.get_ident(), ext))
        written = 0
        with archive.open(name) as source:
            with open(probe, "wb") as handle:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > MAX_MEDIA_BYTES:
                        break
                    handle.write(chunk)
        entry["bytes"] = written or entry.get("bytes") or 0
        info = inspect_media(probe)
        entry["video"] = info.get("video") or ""
        entry["audio"] = info.get("audio") or ""
        entry["duration"] = info.get("duration") or 0.0
        entry["container"] = info.get("container") or ""
        entry["playable"] = bool(info.get("playable"))
        entry["problems"] = list(entry.get("problems") or []) + list(info.get("problems") or [])
        entry["width"] = info.get("width") or 0
        entry["height"] = info.get("height") or 0
    except (KeyError, zipfile.BadZipFile):
        entry["playable"] = False
        entry["problems"].append("这一部分读不出来（压缩包内已损坏）")
    except OSError as exc:
        entry["playable"] = False
        entry["problems"].append("无法解析这个媒体：%s" % exc)
    finally:
        if probe and os.path.exists(probe):
            try:
                os.remove(probe)
            except OSError:
                pass


def _font_name(archive, name):
    try:
        # 嵌入字体可能几十 MB，只要开头 400 字节，别整个读出来
        with archive.open(name) as handle:
            head = handle.read(400)
    except (KeyError, zipfile.BadZipFile, OSError):
        return ""
    match = re.search(rb"name=\"([^\"]+)\"", head)
    return match.group(1).decode("utf-8", "replace") if match else ""


def _check_slide_media(archive, index, slide_name, report):
    """看这一页引用了哪些媒体、引用是否真的存在。"""
    try:
        xml = archive.read(slide_name).decode("utf-8", "replace")
        rels_name = "ppt/slides/_rels/%s.rels" % os.path.basename(slide_name)
        rels = archive.read(rels_name).decode("utf-8", "replace") if rels_name in archive.namelist() else ""
    except (KeyError, zipfile.BadZipFile):
        report["problems"].append("第 %d 页读不出来（文件可能损坏）" % index)
        return
    referenced = re.findall(r'r:(?:link|embed)="(rId\d+)"', xml)
    if not referenced:
        return
    # 属性顺序不固定（Id 可能在 Target 后面），所以逐个 Relationship 元素解析，别指望正则分组
    targets = {}
    for element in re.findall(r"<Relationship\b[^>]*/?>", rels):
        rid = re.search(r'Id="([^"]+)"', element)
        target = re.search(r'Target="([^"]+)"', element)
        if rid:
            targets[rid.group(1)] = target.group(1) if target else ""
    for rid in sorted(set(referenced), key=lambda value: int(value[3:])):
        target = targets.get(rid, "")
        if not target:
            report["problems"].append("第 %d 页引用了媒体 %s，但关系文件里找不到它（链接断了）"
                                      % (index, rid))
            continue
        if target.startswith(("http:", "https:", "file:")) or re.match(r"^[A-Za-z]:", target):
            report["external"].append({"slide": index, "target": target})
            report["problems"].append("第 %d 页的视频/音频是**外链**：%s —— 换个文件夹或换台电脑就播不了，"
                                      "必须重新插入（PowerPoint 里默认是嵌入，外链多见于从别处拷来的课件）"
                                      % (index, target))
            continue
        part = os.path.normpath(os.path.join("ppt/slides", target)).replace("\\", "/")
        if part not in archive.namelist():
            report["problems"].append("第 %d 页引用的媒体部件缺失：%s（课件被「压缩媒体」处理过或拷贝不完整时常见）"
                                      % (index, part))


def _check_external_links(archive, rel_name, report):
    try:
        rels = archive.read(rel_name).decode("utf-8", "replace")
    except (KeyError, zipfile.BadZipFile):
        return
    for target, mode in re.findall(r'Target="([^"]+)"(?:\s+TargetMode="([^"]+)")?', rels):
        if mode == "External" and is_media_path(target) \
                and not any(item["target"] == target for item in report["external"]):
            report["external"].append({"slide": 0, "target": target})


def _pptx_advice(report):
    if report["external"]:
        report["advice"].append("外链的媒体要重新插入一次（PowerPoint：插入→视频→此设备），"
                                "插入后 PowerPoint 会把它嵌进文件里。")
    if any(not item["playable"] for item in report["media"]):
        report["advice"].append("不兼容的媒体可以用「提取媒体」取出来，转成 MP4(H.264)+AAC 后再用"
                                "「替换媒体」放回去（本工具支持 replace_media）。")
    if report["fonts"]:
        report["advice"].append("课件里嵌入了字体：%s。换机器若缺字体，排版会跑版，"
                                "可以改成常见字体（微软雅黑/宋体）。" % "、".join(report["fonts"]))
    if not report["advice"] and not report["problems"]:
        report["advice"].append("没发现明显问题：媒体都已内嵌，编码也在兼容列表里。")


def extract_media(path, out_dir):
    """把 pptx 里的媒体部件导出来，方便老师试播/送修/替换。"""
    written = []
    # 导出目录可能还不存在（老师随手写了个新目录名），先建出来
    if out_dir and not os.path.isdir(out_dir):
        os.makedirs(out_dir, exist_ok=True)
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            if not name.startswith("ppt/media/"):
                continue
            ext = os.path.splitext(name)[1].lower()
            if ext not in MEDIA_EXTS:
                continue
            target = os.path.join(out_dir, os.path.basename(name))
            index = 1
            while os.path.exists(target):
                target = os.path.join(out_dir, "%s-%d%s" % (os.path.splitext(os.path.basename(name))[0],
                                                            index, ext))
                index += 1
            # 流式导出：课件里的视频可能上 G，不能整个读进内存
            with archive.open(name) as source:
                with open(target, "wb") as handle:
                    shutil.copyfileobj(source, handle, 1024 * 1024)
            written.append(target)
    return written


def replace_media(path, part_name, new_file):
    """把 pptx 里指定的媒体部件换成新的文件（保持文件名与位置不变，PPT 不需要改引用）。

    返回 (是否替换, 说明)。part_name 可以只给文件名（如 media1.mp4）。
    """
    part = part_name
    if not part.startswith("ppt/media/"):
        part = "ppt/media/" + os.path.basename(part)
    if not os.path.isfile(new_file):
        return False, "找不到新文件：%s" % new_file
    with open(new_file, "rb") as handle:
        payload = handle.read()
    tmp = path + ".tmp-media"
    replaced = False
    try:
        with zipfile.ZipFile(path) as source:
            names = source.namelist()
            if part not in names:
                return False, "课件里没有这个媒体部件：%s（可用 diagnose_pptx 看正确名字）" % part
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as target:
                for name in names:
                    data = payload if name == part else source.read(name)
                    target.writestr(name, data)
        os.replace(tmp, path)
        replaced = True
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    if replaced:
        return True, "已把 %s 换成 %s（%.1f MB）" % (part, os.path.basename(new_file),
                                                 len(payload) / 1048576.0)
    return False, "替换失败"


def media_report_text(info):
    """把 inspect_media 的结果整理成给模型/老师看的短文。"""
    lines = ["文件：%s" % info["path"]]
    lines.append("容器：%s ｜ 视频：%s ｜ 音频：%s" % (info["container"] or "未知",
                                                    info["video"] or "无",
                                                    info["audio"] or "无"))
    if info["duration"]:
        lines.append("时长：约 %.1f 秒 ｜ 分辨率：%s" % (
            info["duration"], ("%dx%d" % (info["width"], info["height"])) if info["width"] else "未知"))
    lines.append("大小：%.1f MB ｜ Win7+PowerPoint 能否播放：%s"
                 % (info["bytes"] / 1048576.0, "可以" if info["playable"] else "很可能放不了"))
    if info["problems"]:
        lines.append("")
        lines.append("发现的问题：")
        for item in info["problems"]:
            lines.append("- " + item)
    if info["advice"]:
        lines.append("")
        lines.append("建议：")
        for item in info["advice"]:
            lines.append("- " + item)
    return "\n".join(lines)


def pptx_report_text(report):
    lines = ["课件：%s（%.1f MB，%d 页，内嵌媒体 %d 个）"
             % (report["path"], report["size"] / 1048576.0, report["slides"], len(report["media"]))]
    if report["media"]:
        lines.append("")
        lines.append("内嵌媒体：")
        for item in report["media"]:
            mark = "可播放" if item["playable"] else "有问题"
            lines.append("- %s（%.1f MB，%s，%s %s）%s"
                         % (item["part"], item["bytes"] / 1048576.0, item.get("container") or "未知",
                            item.get("video") or "无视频", item.get("audio") or "",
                            "→ " + "；".join(item["problems"]) if item["problems"] else "→ " + mark))
    if report["external"]:
        lines.append("")
        lines.append("外链媒体（换电脑就播不了）：")
        for item in report["external"]:
            lines.append("- 第 %s 页：%s" % (item["slide"] or "?", item["target"]))
    if report["problems"]:
        lines.append("")
        lines.append("发现的问题：")
        for item in report["problems"]:
            lines.append("- " + item)
    if report["advice"]:
        lines.append("")
        lines.append("建议：")
        for item in report["advice"]:
            lines.append("- " + item)
    return "\n".join(lines)
