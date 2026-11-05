# -*- coding: utf-8 -*-
"""屏幕图像的小工具（纯标准库）：BGRA 原始像素 → 裁剪 / 缩放 / 标注 → PNG。

老的实现是「PowerShell 里用 System.Drawing 存 PNG」，每次调用要起一个进程；
现在抓屏走 webui\\winapi.py 的 GDI（1920×1080 约 40 ms），PNG 编码用标准库
zlib + struct 自己写（一张 1920×1080 约几十毫秒），标注框和编号也直接在像素缓冲上画。

颜色一律用 (r, g, b) 或 (r, g, b, a)，a 是 0-255 的不透明度。
"""

import binascii
import os
import struct
import zlib

from winapi import WinApiError, capture_raw

# 5×7 点阵数字与几个符号（每行 5 位，低位在左，共 7 行）
FONT = {
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
    "3": ("11111", "00010", "00100", "00010", "00001", "10001", "01110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "11110", "00001", "00001", "10001", "01110"),
    "6": ("00110", "01000", "10000", "11110", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00010", "01100"),
    "-": ("00000", "00000", "00000", "11111", "00000", "00000", "00000"),
    ".": ("00000", "00000", "00000", "00000", "00000", "01100", "01100"),
    ":": ("00000", "01100", "01100", "00000", "01100", "01100", "00000"),
    "+": ("00000", "00100", "00100", "11111", "00100", "00100", "00000"),
    ",": ("00000", "00000", "00000", "00000", "01100", "00100", "01000"),
    "[": ("01110", "01000", "01000", "01000", "01000", "01000", "01110"),
    "]": ("01110", "00010", "00010", "00010", "00010", "00010", "01110"),
    " ": ("00000", "00000", "00000", "00000", "00000", "00000", "00000"),
}
for _index, _letter in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ"):
    # 只给标注用得到的几个字母留形状，其余画成方框，够用了
    FONT.setdefault(_letter, ("11110", "10001", "10001", "11110", "10001", "10001", "11110"))


class Canvas(object):
    """一块 BGRA 像素缓冲（大小 = width × height × 4）。"""

    def __init__(self, width, height, pixels):
        self.width = int(width)
        self.height = int(height)
        self.pixels = bytes(pixels)
        expected = self.width * self.height * 4
        if len(self.pixels) != expected:
            raise WinApiError("像素长度不对：%d，应为 %d" % (len(self.pixels), expected))

    # ------------------------------------------------------------ 构造

    @classmethod
    def grab(cls, region=None, monitor=None, scale=1.0, max_size=(1920, 1080), displays=None):
        """抓屏。monitor 是显示器下标（0 基），给了就用它的区域；否则用 region/整屏。"""
        if monitor is not None:
            monitors = displays if displays is not None else __import__("winapi").displays()
            index = int(monitor)
            if index < 0 or index >= len(monitors):
                raise WinApiError("显示器下标超范围：%s（本机有 %d 个）" % (monitor, len(monitors)))
            region = monitors[index]["rect"]
        width, height, raw, _box = capture_raw(region=region, scale=scale, max_size=max_size)
        return cls(width, height, raw)

    def region_box(self, region=None, monitor=None):
        """和 grab 用同一套规则，返回这块画面对应的屏幕区域（用来把图坐标换算回屏幕坐标）。"""
        import winapi

        if monitor is not None:
            monitors = winapi.displays()
            index = int(monitor)
            if index < 0 or index >= len(monitors):
                raise WinApiError("显示器下标超范围：%s" % monitor)
            return monitors[index]["rect"]
        virtual = winapi.virtual_screen()
        if region is None:
            # 注意：virtual_screen() 给的是 (x, y, 宽, 高)，这里统一换算成 (左, 上, 右, 下)，
            # 和下面 region 分支的返回格式保持一致（否则多屏时换算回屏幕坐标会错）。
            return (virtual[0], virtual[1], virtual[0] + virtual[2], virtual[1] + virtual[3])
        left, top, right, bottom = [int(value) for value in region]
        if right <= left or bottom <= top:
            raise WinApiError("区域不合法：%r" % (region,))
        return (left, top, right, bottom)

    # ------------------------------------------------------------ 基础操作

    def crop(self, box):
        """按**本画布像素**裁剪，返回新画布。"""
        left, top, right, bottom = [int(value) for value in box]
        left = max(0, min(self.width, left))
        right = max(0, min(self.width, right))
        top = max(0, min(self.height, top))
        bottom = max(0, min(self.height, bottom))
        if right <= left or bottom <= top:
            raise WinApiError("裁剪区域不合法：%r" % (box,))
        stride = self.width * 4
        chunk = bytearray()
        for row in range(top, bottom):
            start = row * stride + left * 4
            chunk += self.pixels[start:start + (right - left) * 4]
        return Canvas(right - left, bottom - top, bytes(chunk))

    def resized(self, scale):
        """缩放（最近邻，够标注图用）。"""
        factor = max(0.05, min(8.0, float(scale)))
        target_width = max(1, int(round(self.width * factor)))
        target_height = max(1, int(round(self.height * factor)))
        if target_width == self.width and target_height == self.height:
            return self
        stride = self.width * 4
        out = bytearray(target_width * target_height * 4)
        xs = [min(self.width - 1, int(x * self.width / target_width)) * 4 for x in range(target_width)]
        for y in range(target_height):
            source = min(self.height - 1, int(y * self.height / target_height)) * stride
            target = y * target_width * 4
            row = bytearray(target_width * 4)
            for index, offset in enumerate(xs):
                row[index * 4:index * 4 + 4] = self.pixels[source + offset:source + offset + 4]
            out[target:target + target_width * 4] = row
        return Canvas(target_width, target_height, bytes(out))

    # ------------------------------------------------------------ 画图

    def _blend(self, x, y, color):
        if x < 0 or y < 0 or x >= self.width or y >= self.height:
            return
        red, green, blue = color[0], color[1], color[2]
        alpha = color[3] if len(color) > 3 else 255
        offset = (y * self.width + x) * 4
        buffer = self._mutable()
        if alpha >= 255:
            buffer[offset] = blue
            buffer[offset + 1] = green
            buffer[offset + 2] = red
            buffer[offset + 3] = 255
        elif alpha > 0:
            ratio = alpha / 255.0
            buffer[offset] = int(buffer[offset] * (1 - ratio) + blue * ratio)
            buffer[offset + 1] = int(buffer[offset + 1] * (1 - ratio) + green * ratio)
            buffer[offset + 2] = int(buffer[offset + 2] * (1 - ratio) + red * ratio)
            buffer[offset + 3] = 255

    def _mutable(self):
        if not isinstance(self.pixels, bytearray):
            self.pixels = bytearray(self.pixels)
        return self.pixels

    def draw_rect(self, box, color=(255, 0, 0), thickness=1):
        left, top, right, bottom = [int(value) for value in box]
        _ = self._mutable()
        for offset in range(max(1, int(thickness))):
            for x in range(left + offset, right - offset):
                self._blend(x, top + offset, color)
                self._blend(x, bottom - 1 - offset, color)
            for y in range(top + offset, bottom - offset):
                self._blend(left + offset, y, color)
                self._blend(right - 1 - offset, y, color)
        return self

    def fill_rect(self, box, color=(255, 255, 0, 90), radius=6):
        """给元素填一层半透明底色（比实心框更容易看清底下的字）。"""
        left, top, right, bottom = [int(value) for value in box]
        corners = ((left + radius, top + radius), (right - radius, top + radius),
                   (left + radius, bottom - radius), (right - radius, bottom - radius))
        for y in range(top, bottom):
            for x in range(left, right):
                if radius:
                    inside = False
                    for corner_x, corner_y in corners:
                        if (x < left + radius or x >= right - radius) and \
                                (y < top + radius or y >= bottom - radius):
                            dx = x - corner_x
                            dy = y - corner_y
                            if dx * dx + dy * dy > radius * radius:
                                inside = True
                                break
                    if inside:
                        continue
                self._blend(x, y, color)
        return self

    def draw_text(self, x, y, text, color=(255, 255, 255), background=(200, 30, 30), size=1):
        """画点阵文字（数字/大写字母/几个符号）。返回画完的右下角 x 坐标。"""
        size = max(1, int(size))
        cursor = int(x)
        if background:
            width = len(str(text)) * 6 * size
            for row in range(y - size, y + 8 * size):
                for column in range(cursor - size, cursor + width):
                    self._blend(column, row, background)
        for char in str(text):
            rows = FONT.get(char.upper())
            if rows is None:
                cursor += 6 * size
                continue
            for row_index, row in enumerate(rows):
                for column_index, bit in enumerate(row):
                    if bit != "1":
                        continue
                    for dy in range(size):
                        for dx in range(size):
                            self._blend(cursor + column_index * size + dx,
                                        y + row_index * size + dy, color)
            cursor += 6 * size
        return cursor

    def draw_grid(self, columns, rows, color=(255, 0, 0, 110)):
        for index in range(1, max(1, int(columns))):
            x = int(self.width * index / float(columns))
            for y in range(self.height):
                self._blend(x, y, color)
        for index in range(1, max(1, int(rows))):
            y = int(self.height * index / float(rows))
            for x in range(self.width):
                self._blend(x, y, color)
        return self

    def draw_cursor(self, x, y, radius=12, color=(255, 40, 40)):
        """把光标位置圈出来（图是缩放过的，这里传的是**画布**坐标）。"""
        center_x, center_y = int(x), int(y)
        for angle_step in range(0, 360, 4):
            radians = angle_step * 3.14159265 / 180.0
            for offset in range(-1, 2):
                px = int(center_x + (radius + offset) * __import__("math").cos(radians))
                py = int(center_y + (radius + offset) * __import__("math").sin(radians))
                self._blend(px, py, color)
        return self

    # ------------------------------------------------------------ 输出

    def png_bytes(self):
        """编码成 PNG（RGBA，标准库 zlib）。"""
        width, height = self.width, self.height
        stride = width * 4
        rgba = bytearray(stride * height)
        pixels = self.pixels
        rgba[0::4] = pixels[2::4]
        rgba[1::4] = pixels[1::4]
        rgba[2::4] = pixels[0::4]
        if len(pixels[3::4]) == width * height:
            rgba[3::4] = b"\xff" * (width * height)
        raw = bytearray()
        for row in range(height):
            raw.append(0)
            start = row * stride
            raw += rgba[start:start + stride]
        compressor = zlib.compressobj(6)
        body = compressor.compress(bytes(raw)) + compressor.flush()
        header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
        return (b"\x89PNG\r\n\x1a\n"
                + _chunk(b"IHDR", header)
                + _chunk(b"IDAT", body)
                + _chunk(b"IEND", b""))

    def save_png(self, path):
        path = os.path.abspath(path)
        folder = os.path.dirname(path)
        if folder and not os.path.isdir(folder):
            os.makedirs(folder)
        with open(path, "wb") as handle:
            handle.write(self.png_bytes())
        return path


def _chunk(kind, payload):
    return (struct.pack(">I", len(payload)) + kind + payload
            + struct.pack(">I", binascii.crc32(kind + payload) & 0xFFFFFFFF))


if __name__ == "__main__":  # 手工自检：python webui\winimage.py
    import sys
    import time

    for _stream in (sys.stdout, sys.stderr):  # 显示器名里可能有 GBK 打不出的字符
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    from winapi import displays

    start = time.time()
    canvas = Canvas.grab(scale=1.0)
    grabbed = time.time()
    canvas.draw_rect((100, 100, 400, 300), (255, 0, 0), 2)
    canvas.draw_text(110, 110, "12", size=2)
    canvas.draw_grid(8, 6)
    canvas.draw_cursor(300, 500)
    drawn = time.time()
    data = canvas.png_bytes()
    encoded = time.time()
    target = os.path.join(os.environ.get("TEMP") or ".", "w7a-canvas-probe.png")
    canvas.save_png(target)
    half = canvas.resized(0.5)
    print("抓屏 %dx%d %.0f ms；标注 %.0f ms；PNG %d 字节 %.0f ms；半尺寸 %dx%d"
          % (canvas.width, canvas.height, (grabbed - start) * 1000, (drawn - grabbed) * 1000,
             len(data), (encoded - drawn) * 1000, half.width, half.height))
    print("显示器:", displays())
    print("写了:", target)
