# -*- coding: utf-8 -*-
"""生成「Win7 教学助手」的应用图标：agent-icon.png（512x512）与 agent.ico（7 个尺寸）。

设计：圆角方形底板（靛蓝 -> 紫 的斜向渐变 + 顶部高光 + 底部内阴影 + 内侧描边），
      中间是白色对话气泡（带尾巴），气泡里用品牌渐变色的粉笔线条（波纹 + 直线 + 光标）
      表达「教学 + 智能」。符号全部是几何图形，不使用任何字体，因此不依赖系统字符。

实现：只用 Python 标准库（zlib / struct / math），不需要 Pillow、numpy 或任何联网资源。
      - 每个尺寸按自己的细节层级（LOD）重画，而不是把大图缩小：
        16 只留一条对齐像素网格的粗线，24/32 两行文字，48/64 两行 + 更长的首行，
        96 以上才加粉笔波纹、光标竖条和气泡投影；
      - 先按 4 倍尺寸做解析式抗锯齿光栅化，再盒式降采样（超采样自己做，不依赖系统缩放）；
      - PNG 由 zlib 手写编码，ICO 由 struct 手写拼装：
        16~128 用未压缩的 32bpp DIB（Windows 7 兼容性最好），256 用 PNG 压缩条目。

用法（在项目根目录）：
    python\\python.exe scripts\\make-icon.py
        按内置设计重新生成 agent-icon.png（512x512）与 agent.ico（7 个尺寸）。

    python\\python.exe scripts\\make-icon.py 自备图.png [输出.ico]
        把自备 PNG 当作图稿，缩放出同名 .ico（默认与源图同目录同名）。
        传入的就是本脚本产出的 agent-icon.png 时，会按内置设计逐尺寸重画，结果与上面一致。

输出是可复现的：同一份脚本、同一份 Python，任意机器上跑出来的字节完全一致
（图稿没有随机数、没有时间戳，PNG 也用固定参数压缩）。
"""

import math
import os
import struct
import sys
import zlib

SIZES = (16, 24, 32, 48, 64, 128, 256)
PNG_SIZE = 512          # agent-icon.png 的边长（界面 / README 用）
SS = 4                  # 超采样倍数：按 4 倍尺寸光栅化后盒式降采样
DIB_MAX = 128           # <= 该尺寸的 ICO 条目用 32bpp DIB，更大的用 PNG 条目

# ---------------------------------------------------------------- 基础工具

def _clamp(v, lo, hi):
    return lo if v < lo else (hi if v > hi else v)


def _smoothstep(e0, e1, x):
    t = _clamp((x - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _rrect_sdf(px, py, cx, cy, hw, hh, r):
    """圆角矩形的有符号距离（<0 在内部），单位与入参一致。"""
    qx = abs(px - cx) - (hw - r)
    qy = abs(py - cy) - (hh - r)
    ox = qx if qx > 0.0 else 0.0
    oy = qy if qy > 0.0 else 0.0
    m = qx if qx > qy else qy
    if m > 0.0:
        m = 0.0
    return math.sqrt(ox * ox + oy * oy) + m - r


# ------------------------------------------------- 覆盖率光栅器（解析抗锯齿）
# cov 是 SS 倍分辨率的 bytearray，值为 0..255 的覆盖率。
# 平台区（内部）用切片直接填 255，边缘带用精确 SDF 求覆盖，重叠图形按 max 合并。

def cov_clear(cov):
    cov[:] = b"\x00" * len(cov)


def raster_round_rect(cov, canvas_w, canvas_h, cx, cy, hw, hh, r):
    """把圆角矩形的覆盖率并入 cov（平台区直接填满，边缘带用精确 SDF）。坐标单位为最终像素。"""
    r = min(r, hw, hh)
    sw = canvas_w * SS
    sh = canvas_h * SS
    row = sw
    CX = cx * SS
    CY = cy * SS
    HW = hw * SS
    HH = hh * SS
    R = r * SS
    fill = b"\xff" * (sw + 2)

    y0 = max(0, int(math.floor(CY - HH - 0.5)))
    y1 = min(sh - 1, int(math.ceil(CY + HH + 0.5)))
    x0 = max(0, int(math.floor(CX - HW - 1.5)))
    x1 = min(sw - 1, int(math.ceil(CX + HW + 1.5)))
    corner0 = HH - R

    for Y in range(y0, y1 + 1):
        ay = abs(Y + 0.5 - CY)
        if ay > HH + 0.5:
            continue
        if ay <= corner0:
            dxm = HW
        else:
            k = ay - corner0
            if k >= R + 0.5:
                continue
            dxm = HW - R + math.sqrt(max(0.0, R * R - k * k))
        lo = CX - dxm
        hi = CX + dxm
        base = Y * row
        if ay <= HH - 1.0 and dxm > 1.0:
            # 平台：距离边界 >= 1px，覆盖率必然饱和
            pl = int(math.ceil(lo + 1.0))
            pr = int(math.floor(hi - 1.0))
            if pl < x0:
                pl = x0
            if pr > x1:
                pr = x1
            if pr >= pl:
                cov[base + pl:base + pr + 1] = fill[:pr - pl + 1]
            bl = max(x0, pl - 2)
            br = min(x1, pr + 2)
        else:
            bl = x0
            br = x1
        for X in range(bl, br + 1):
            d = _rrect_sdf(X + 0.5, Y + 0.5, CX, CY, HW, HH, R)
            c = (0.5 - d) * 255.0
            if c <= 0.0:
                continue
            if c >= 255.0:
                c = 255.0
            i = base + X
            if c > cov[i]:
                cov[i] = int(c)


def raster_capsule_chain(cov, canvas_w, canvas_h, pts, halves):
    """一串半径递增/递减的胶囊首尾相接，用来画「有粗细变化」的笔画。"""
    for i in range(len(pts) - 1):
        half = (halves[i] + halves[i + 1]) * 0.5
        raster_capsule(cov, canvas_w, canvas_h, pts[i][0], pts[i][1],
                       pts[i + 1][0], pts[i + 1][1], half)


def raster_capsule(cov, canvas_w, canvas_h, x0, y0, x1, y1, half):
    """线段胶囊（圆头），用于画笔画。"""
    row = canvas_w * SS
    sh = canvas_h * SS
    sw = row
    X0 = x0 * SS
    Y0 = y0 * SS
    X1 = x1 * SS
    Y1 = y1 * SS
    R = half * SS
    vx = X1 - X0
    vy = Y1 - Y0
    vv = vx * vx + vy * vy
    if vv <= 1e-9:
        vx = vy = 0.0
    ax = max(0, int(math.floor(min(X0, X1) - R - 1.5)))
    bx = min(sw - 1, int(math.ceil(max(X0, X1) + R + 1.5)))
    ay = max(0, int(math.floor(min(Y0, Y1) - R - 1.5)))
    by = min(sh - 1, int(math.ceil(max(Y0, Y1) + R + 1.5)))
    for Y in range(ay, by + 1):
        py = Y + 0.5
        base = Y * row
        for X in range(ax, bx + 1):
            px = X + 0.5
            wx = px - X0
            wy = py - Y0
            if vv > 1e-9:
                t = (wx * vx + wy * vy) / vv
                if t < 0.0:
                    t = 0.0
                elif t > 1.0:
                    t = 1.0
                dx = wx - vx * t
                dy = wy - vy * t
            else:
                dx = wx
                dy = wy
            d = math.sqrt(dx * dx + dy * dy) - R
            c = (0.5 - d) * 255.0
            if c <= 0.0:
                continue
            if c >= 255.0:
                c = 255.0
            i = base + X
            if c > cov[i]:
                cov[i] = int(c)


def cov_downsample(cov, w, h):
    """SS 倍覆盖率 -> 最终分辨率覆盖率（盒式平均 = 超采样降采样）。"""
    out = bytearray(w * h)
    row = w * SS
    area = SS * SS
    for y in range(h):
        o = y * w
        r0 = y * SS * row
        for x in range(w):
            c = x * SS
            total = 0
            for j in range(SS):
                b = r0 + j * row + c
                total += sum(cov[b:b + SS])
            out[o + x] = total // area
    return out


def blur_cov(cov, w, h, radius):
    """两次盒式模糊近似高斯，用于气泡的柔和投影。"""
    if radius < 1:
        return cov
    tmp = bytearray(w * h)
    win = radius * 2 + 1
    for y in range(h):
        base = y * w
        acc = 0
        for x in range(-radius, radius + 1):
            acc += cov[base + _clamp(x, 0, w - 1)]
        for x in range(w):
            tmp[base + x] = acc // win
            acc += cov[base + _clamp(x + radius + 1, 0, w - 1)] - cov[base + _clamp(x - radius, 0, w - 1)]
    out = bytearray(w * h)
    for x in range(w):
        acc = 0
        for y in range(-radius, radius + 1):
            acc += tmp[_clamp(y, 0, h - 1) * w + x]
        for y in range(h):
            out[y * w + x] = acc // win
            acc += tmp[_clamp(y + radius + 1, 0, h - 1) * w + x] - tmp[_clamp(y - radius, 0, h - 1) * w + x]
    return out


# ------------------------------------------------------------ 画布与合成
# 画布是预乘 alpha 的 RGBA8，最后再还原成直通 alpha 输出。

def composite(dst, cov, w, h, color, alpha=1.0):
    """把覆盖率 cov 用 color 合成到画布 dst 上（source-over）。

    color: (r, g, b) 常量，或 f(x, y) -> (r, g, b) / (r, g, b, a)。
    """
    const = not callable(color)
    cr = cg = cb = 0
    if const:
        cr, cg, cb = color
    for y in range(h):
        base = y * w
        for x in range(w):
            c = cov[base + x]
            if c == 0:
                continue
            ca = c * alpha / 255.0
            i = (base + x) * 4
            if const:
                sr = cr * ca
                sg = cg * ca
                sb = cb * ca
            else:
                col = color(x, y)
                a = ca if len(col) == 3 else ca * col[3]
                sr = col[0] * a
                sg = col[1] * a
                sb = col[2] * a
                ca = a
            inv = 1.0 - ca
            dst[i] = int(sr + dst[i] * inv + 0.5)
            dst[i + 1] = int(sg + dst[i + 1] * inv + 0.5)
            dst[i + 2] = int(sb + dst[i + 2] * inv + 0.5)
            dst[i + 3] = int(ca * 255.0 + dst[i + 3] * inv + 0.5)


def unpremultiply(px):
    """预乘 RGBA8 -> 直通 RGBA8。"""
    out = bytearray(len(px))
    for i in range(0, len(px), 4):
        a = px[i + 3]
        if a == 0:
            continue
        out[i + 3] = a
        out[i] = min(255, (px[i] * 255 + a // 2) // a)
        out[i + 1] = min(255, (px[i + 1] * 255 + a // 2) // a)
        out[i + 2] = min(255, (px[i + 2] * 255 + a // 2) // a)
    return out


# --------------------------------------------------------------- 图标设计

# 底板配色：整条 DeepSeek 蓝（浅蓝高光 -> 主色 #4d6bfe -> 右下压深）
# 跟 webui\ui\style.css 里的 --accent / --accent-strong 是同一套颜色，
# 改这里的时候记得两边一起改，别让图标和界面又对不上。
GRAD_STOPS = (
    (0.00, (0x6E, 0x8B, 0xFF)),
    (0.48, (0x4D, 0x6B, 0xFE)),
    (1.00, (0x2F, 0x46, 0xE0)),
)
INK_SCALE = 0.90            # 气泡里的笔画：用底板渐变色压暗一点，白底上对比更好


def _grad_rgb(t):
    t = _clamp(t, 0.0, 1.0)
    lo = GRAD_STOPS[0]
    hi = GRAD_STOPS[-1]
    for i in range(len(GRAD_STOPS) - 1):
        a = GRAD_STOPS[i]
        b = GRAD_STOPS[i + 1]
        if t <= b[0] or i == len(GRAD_STOPS) - 2:
            lo, hi = a, b
            break
    span = hi[0] - lo[0]
    k = 0.0 if span <= 0.0 else (t - lo[0]) / span
    return (lo[1][0] + (hi[1][0] - lo[1][0]) * k,
            lo[1][1] + (hi[1][1] - lo[1][1]) * k,
            lo[1][2] + (hi[1][2] - lo[1][2]) * k)


class Design(object):
    """一个尺寸下的全部几何参数（单位：最终像素）。细节随尺寸简化。"""

    def __init__(self, size):
        s = float(size)
        self.size = size
        # 细节层级：大尺寸画全套，小尺寸只留主体轮廓
        self.lod = 2 if size >= 96 else (1 if size >= 40 else 0)

        # 底板
        m = s * (0.012 + 0.028 * _clamp((s - 16.0) / 240.0, 0.0, 1.0))
        self.plate = s - 2.0 * m
        self.cx = s * 0.5
        self.cy = s * 0.5
        self.plate_r = (0.200 if self.lod == 0 else 0.222) * self.plate

        # 渐变方向：左上 -> 右下的对角线
        ux, uy = 0.66, 0.75
        norm = math.hypot(ux, uy)
        self.gx = ux / norm
        self.gy = uy / norm
        self.glen = 2.0 * (self.plate * 0.5 * abs(self.gx) + self.plate * 0.5 * abs(self.gy))

        # 气泡（白色主体 + 左下尾巴）
        self.bw = self.plate * (0.615 if self.lod >= 2 else (0.660 if self.lod == 1 else 0.630))
        self.bh = self.bw * 0.735
        self.bcx = self.cx
        self.bcy = self.cy - 0.055 * self.plate
        self.br = (0.30 if self.lod >= 2 else (0.28 if self.lod == 1 else 0.26)) * self.bh
        self.bx0 = self.bcx - self.bw * 0.5
        self.by0 = self.bcy - self.bh * 0.5
        self.bx1 = self.bcx + self.bw * 0.5
        self.by1 = self.bcy + self.bh * 0.5

        # 尾巴：二次贝塞尔 + 逐渐收细，比圆柱形更像真的气泡尾巴
        # 小尺寸上必须又短又粗，否则会变成一根挂着的线头
        if self.lod >= 2:
            k0, k1, tx, ty = 0.115, 0.045, 0.115, 0.330
        elif self.lod == 1:
            k0, k1, tx, ty = 0.170, 0.080, 0.125, 0.310
        else:
            k0, k1, tx, ty = 0.260, 0.160, 0.150, 0.290
        self.tail_p0 = (self.bx0 + 0.310 * self.bw, self.by1 - 0.170 * self.bh)
        self.tail_p1 = (self.bx0 + 0.255 * self.bw, self.by1 + 0.150 * self.bh)
        self.tail_p2 = (self.bx0 + tx * self.bw, self.by1 + ty * self.bh)
        self.tail_t0 = k0 * self.bh
        self.tail_t1 = k1 * self.bh
        self.tail_steps = 16 if self.lod >= 2 else (10 if self.lod == 1 else 7)

        # 气泡里的粉笔线条：大尺寸画波纹 + 直线 + 光标，小尺寸只留主体轮廓
        self.lines = 2 if size >= 24 else 1
        thick = self.bh * (0.105 if self.lod >= 2 else 0.125)
        if self.lod == 0:
            thick = self.bh * (0.16 if size >= 32 else (0.20 if size >= 24 else 0.28))
        self.ink_half = thick * 0.5
        self.lx0 = self.bx0 + self.bw * 0.165
        self.lx1 = self.bx0 + self.bw * 0.835
        self.ly1 = self.by0 + self.bh * 0.335
        self.ly2 = self.by0 + self.bh * 0.665
        self.wave = self.bh * (0.115 if self.lod >= 2 else 0.0)
        self.cur_x = self.bx0 + self.bw * 0.685      # 光标竖条的位置
        self.line2_end = self.bx0 + self.bw * 0.575  # 第二行文字的收尾（右边留出光标）
        self.snap_ink()

    def snap_ink(self):
        """小尺寸把笔画对齐到像素网格。

        16~32 像素上一条 1.5px 的线如果跨在两行之间，会摊成两条灰边、
        看起来又虚又脏；对齐后正好落在整行上，颜色实、边缘利落。
        """
        if self.lod > 0:
            return
        thick = max(1.0, float(round(self.ink_half * 2.0)))
        self.ink_half = thick * 0.5
        if self.lines == 1:
            mid = round(self.by0 + self.bh * 0.5 - thick * 0.5) + thick * 0.5
            self.ly1 = mid
            self.ly2 = mid
        else:
            self.ly1 = round(self.ly1 - thick * 0.5) + thick * 0.5
            self.ly2 = round(self.ly2 - thick * 0.5) + thick * 0.5

    # ---- 渐变色（按像素位置）
    def grad_at(self, x, y):
        t = ((x + 0.5 - self.cx) * self.gx + (y + 0.5 - self.cy) * self.gy) / self.glen + 0.5
        return _grad_rgb(t)

    # ---- 底板颜色（含高光 / 内阴影 / 内侧描边）
    def plate_at(self, x, y, sdf):
        base = _grad_rgb(((x + 0.5 - self.cx) * self.gx + (y + 0.5 - self.cy) * self.gy) / self.glen + 0.5)
        ny = (y + 0.5 - self.cy) / (self.plate * 0.5)
        ny = _clamp(ny, -1.0, 1.0)
        # 垂直压暗，底部略深，制造体积感
        shade = 1.0 - 0.075 * _smoothstep(-0.2, 1.0, ny)
        r = base[0] * shade
        g = base[1] * shade
        b = base[2] * shade
        if self.lod >= 1:
            # 左上方柔光高光
            hx = (x + 0.5 - (self.cx - self.plate * 0.26)) / (self.plate * 0.92)
            hy = (y + 0.5 - (self.cy - self.plate * 0.60)) / (self.plate * 0.92)
            hd = math.sqrt(hx * hx + hy * hy)
            if hd < 1.0:
                k = (1.0 - hd) ** 1.7 * 0.20
                r += (255.0 - r) * k
                g += (255.0 - g) * k
                b += (255.0 - b) * k
            # 底部内侧阴影
            w = 0.24 * _clamp((ny - 0.10) / 0.90, 0.0, 1.0)
            if w > 0.0:
                band = _clamp(1.0 + sdf / (self.plate * 0.085), 0.0, 1.0)
                k = w * band
                r += (28.0 - r) * k
                g += (18.0 - g) * k
                b += (72.0 - b) * k
            # 内侧描边（上亮下暗），让边缘更利落
            rw = max(1.0, self.size * 0.0055)
            if sdf > -rw:
                band = _clamp(1.0 + sdf / rw, 0.0, 1.0)
                k = 0.34 * band * (0.18 + 0.82 * _clamp((1.0 - ny) * 0.5, 0.0, 1.0))
                r += (255.0 - r) * k
                g += (255.0 - g) * k
                b += (255.0 - b) * k
        return r, g, b


def _tail_stroke(d, dy):
    """气泡尾巴：二次贝塞尔采样成「由粗到细」的胶囊链，得到圆润的锥形尖角。"""
    p0, p1, p2 = d.tail_p0, d.tail_p1, d.tail_p2
    pts = []
    halves = []
    for i in range(d.tail_steps + 1):
        u = i / float(d.tail_steps)
        v = 1.0 - u
        pts.append((v * v * p0[0] + 2.0 * v * u * p1[0] + u * u * p2[0],
                    v * v * p0[1] + 2.0 * v * u * p1[1] + u * u * p2[1] + dy))
        halves.append((d.tail_t0 + (d.tail_t1 - d.tail_t0) * u) * 0.5)
    return pts, halves


def raster_bubble(cov, size, d, dy=0.0):
    """白色气泡本体 + 尾巴（并集）。"""
    raster_round_rect(cov, size, size, d.bcx, d.bcy + dy, d.bw * 0.5, d.bh * 0.5, d.br)
    pts, halves = _tail_stroke(d, dy)
    raster_capsule_chain(cov, size, size, pts, halves)


def draw_icon(size):
    """画出一张 size x size 的图标，返回直通 alpha 的 RGBA bytes。"""
    d = Design(size)
    canvas = bytearray(size * size * 4)
    cov = bytearray((size * SS) * (size * SS))

    # 1) 底板
    cov_clear(cov)
    raster_round_rect(cov, size, size, d.cx, d.cy, d.plate * 0.5, d.plate * 0.5, d.plate_r)
    cov1 = cov_downsample(cov, size, size)
    hw = d.plate * 0.5

    def plate_color(x, y):
        return d.plate_at(x, y, _rrect_sdf(x + 0.5, y + 0.5, d.cx, d.cy, hw, hw, d.plate_r))

    composite(canvas, cov1, size, size, plate_color)

    # 2) 气泡的柔和投影（只在大尺寸出现，小尺寸上是噪点）
    if d.lod >= 2:
        radius = max(1, int(round(size * 0.007)))
        cov_clear(cov)
        raster_bubble(cov, size, d, max(1.0, size * 0.012))
        shadow = blur_cov(cov_downsample(cov, size, size), size, size, radius)
        composite(canvas, shadow, size, size, (20.0, 38.0, 120.0), 0.32)

    # 3) 气泡 + 尾巴（白色，底部带一点点冷灰，避免死白）
    cov_clear(cov)
    raster_bubble(cov, size, d)
    cov1 = cov_downsample(cov, size, size)

    def bubble_color(x, y):
        k = _clamp((y + 0.5 - d.by0) / d.bh, 0.0, 1.0)
        k = k * k * 0.35 + k * 0.65
        return (255.0 - 22.0 * k, 255.0 - 20.0 * k, 255.0 - 12.0 * k)

    composite(canvas, cov1, size, size, bubble_color)

    # 4) 气泡里的粉笔笔画：波纹（大尺寸才有）+ 直线 + 光标
    cov_clear(cov)
    if d.lines == 1:
        raster_capsule(cov, size, size, d.lx0, d.ly1, d.lx1, d.ly1, d.ink_half)
    else:
        if d.wave > 0.0:
            # 粉笔味：两端略收细，中段稍粗
            steps = 16
            pts = []
            halves = []
            for i in range(steps + 1):
                t = i / float(steps)
                pts.append((d.lx0 + (d.lx1 - d.lx0) * t,
                            d.ly1 + math.sin(t * math.pi * 2.0) * d.wave))
                halves.append(d.ink_half * (0.80 + 0.20 * math.sin(t * math.pi)))
            raster_capsule_chain(cov, size, size, pts, halves)
        else:
            raster_capsule(cov, size, size, d.lx0, d.ly1, d.lx1, d.ly1, d.ink_half)
        line2_end = d.line2_end if d.lod >= 2 else d.lx0 + (d.lx1 - d.lx0) * 0.45
        raster_capsule(cov, size, size, d.lx0, d.ly2, line2_end, d.ly2, d.ink_half)
        if d.lod >= 2:
            raster_round_rect(cov, size, size, d.cur_x, d.ly2, d.ink_half, d.bh * 0.165, d.ink_half)
    cov1 = cov_downsample(cov, size, size)

    def ink_color(x, y):
        c = d.grad_at(x, y)
        return (c[0] * INK_SCALE, c[1] * INK_SCALE, c[2] * INK_SCALE)

    composite(canvas, cov1, size, size, ink_color)
    return unpremultiply(canvas)


# ------------------------------------------------------------ PNG 编解码

def encode_png(rgba, w, h):
    raw = bytearray()
    stride = w * 4
    for y in range(h):
        raw.append(0)                       # filter: None
        raw += rgba[y * stride:(y + 1) * stride]
    comp = zlib.compress(bytes(raw), 9)

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", comp) + chunk(b"IEND", b""))


def decode_png(data):
    """读入 PNG，返回 (w, h, 直通 RGBA bytearray)。只支持非隔行扫描的常见格式。"""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("不是 PNG 文件")
    pos = 8
    idat = []
    plte = None
    trns = None
    w = h = depth = ctype = interlace = None
    while pos + 8 <= len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        tag = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if tag == b"IHDR":
            w, h, depth, ctype, _comp, _filt, interlace = struct.unpack(">IIBBBBB", body)
        elif tag == b"PLTE":
            plte = body
        elif tag == b"tRNS":
            trns = body
        elif tag == b"IDAT":
            idat.append(body)
        elif tag == b"IEND":
            break
    if w is None:
        raise ValueError("PNG 缺少 IHDR")
    if interlace:
        raise ValueError("不支持隔行扫描（interlaced）的 PNG，请另存为非隔行格式")
    if ctype not in (0, 2, 3, 4, 6):
        raise ValueError("不支持的 PNG 颜色类型：%d" % ctype)
    if depth not in (1, 2, 4, 8, 16):
        raise ValueError("不支持的 PNG 位深：%d" % depth)
    if ctype == 3 and plte is None:
        raise ValueError("调色板 PNG 缺少 PLTE")

    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ctype]
    bpp = max(1, channels * depth // 8)     # 滤波用的字节步长
    stride = (w * channels * depth + 7) // 8
    raw = zlib.decompress(b"".join(idat))
    if len(raw) < (stride + 1) * h:
        raise ValueError("PNG 数据不完整")

    prev = bytearray(stride)
    out = bytearray(w * h * 4)
    maxval = (1 << depth) - 1
    for y in range(h):
        start = y * (stride + 1)
        ft = raw[start]
        line = bytearray(raw[start + 1:start + 1 + stride])
        if ft == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif ft == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ft == 3:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 0xFF
        elif ft == 4:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                b = prev[i]
                c = prev[i - bpp] if i >= bpp else 0
                p = a + b - c
                pa = abs(p - a)
                pb = abs(p - b)
                pc = abs(p - c)
                pred = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pred) & 0xFF
        elif ft != 0:
            raise ValueError("未知的 PNG 滤波类型：%d" % ft)
        prev = line

        samples = _png_row_samples(line, w, channels, depth, maxval)
        o = y * w * 4
        for x in range(w):
            if ctype == 3:
                idx = samples[x]
                r = plte[idx * 3]
                g = plte[idx * 3 + 1]
                b = plte[idx * 3 + 2]
                a = trns[idx] if (trns is not None and idx < len(trns)) else 255
            elif ctype == 0:
                v = samples[x * channels]
                r = g = b = v
                a = 255
            elif ctype == 4:
                v = samples[x * 2]
                r = g = b = v
                a = samples[x * 2 + 1]
            elif ctype == 2:
                r, g, b = samples[x * 3], samples[x * 3 + 1], samples[x * 3 + 2]
                a = 255
            else:
                r, g, b, a = samples[x * 4], samples[x * 4 + 1], samples[x * 4 + 2], samples[x * 4 + 3]
            o2 = o + x * 4
            out[o2] = r
            out[o2 + 1] = g
            out[o2 + 2] = b
            out[o2 + 3] = a
    return w, h, out


def _png_row_samples(line, w, channels, depth, maxval):
    """把一行原始数据展开成 0..255 的采样序列（每像素 channels 个）。"""
    if depth == 8:
        return line
    if depth == 16:
        return line[0::2]
    per = channels * depth
    vals = bytearray((w * channels))
    mask = maxval
    for x in range(w):
        byte = line[(x * per) >> 3]
        shift = 8 - per - ((x * per) & 7)
        v = (byte >> shift) & mask
        if depth == 1 or depth == 2:
            v = v * 255 // maxval
        elif depth == 4:
            v = v * 17
        vals[x] = v
    return vals


def resize_rgba(src, sw, sh, dw, dh):
    """面积平均重采样（预乘后平均，避免透明边缘发黑）。"""
    out = bytearray(dw * dh * 4)
    xr = []
    for dx in range(dw):
        x0 = dx * sw / float(dw)
        x1 = (dx + 1) * sw / float(dw)
        lo = int(math.floor(x0))
        hi = min(sw, int(math.ceil(x1)))
        xr.append((lo, hi, x0, x1))
    for dy in range(dh):
        y0 = dy * sh / float(dh)
        y1 = (dy + 1) * sh / float(dh)
        ly = int(math.floor(y0))
        hy = min(sh, int(math.ceil(y1)))
        for dx in range(dw):
            lo, hi, x0, x1 = xr[dx]
            rs = gs = bs = as_ = 0.0
            wsum = 0.0
            for sy in range(ly, hy):
                wy = min(y1, sy + 1) - max(y0, sy)
                if wy <= 0.0:
                    continue
                base = sy * sw
                for sx in range(lo, hi):
                    wx = min(x1, sx + 1) - max(x0, sx)
                    if wx <= 0.0:
                        continue
                    wgt = wx * wy
                    i = (base + sx) * 4
                    a = src[i + 3] / 255.0
                    rs += src[i] * a * wgt
                    gs += src[i + 1] * a * wgt
                    bs += src[i + 2] * a * wgt
                    as_ += a * wgt
                    wsum += wgt
            o = (dy * dw + dx) * 4
            if wsum <= 0.0 or as_ <= 0.0:
                continue
            out[o] = min(255, int(rs / as_ + 0.5))
            out[o + 1] = min(255, int(gs / as_ + 0.5))
            out[o + 2] = min(255, int(bs / as_ + 0.5))
            out[o + 3] = min(255, int(as_ / wsum * 255.0 + 0.5))
    return out


# ------------------------------------------------------------ ICO 拼装

def ico_dib_entry(rgba, size):
    """未压缩 32bpp DIB（BITMAPINFOHEADER + 自下而上的 BGRA + AND 掩码）。"""
    hdr = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0,
                      size * size * 4, 0, 0, 0, 0)
    rows = []
    stride = size * 4
    for y in range(size - 1, -1, -1):
        src = rgba[y * stride:(y + 1) * stride]
        row = bytearray(stride)
        row[0::4] = src[2::4]               # B
        row[1::4] = src[1::4]               # G
        row[2::4] = src[0::4]               # R
        row[3::4] = src[3::4]               # A
        rows.append(bytes(row))
    mask_stride = ((size + 31) // 32) * 4
    mask = bytearray()
    for y in range(size - 1, -1, -1):
        mrow = bytearray(mask_stride)
        base = y * stride
        for x in range(size):
            if rgba[base + x * 4 + 3] < 128:
                mrow[x >> 3] |= 0x80 >> (x & 7)
        mask += mrow
    return hdr + b"".join(rows) + bytes(mask)


def build_ico(images, target):
    """images: [(size, rgba)]，<=128 用 DIB，>128 用 PNG 压缩条目。"""
    payload = []
    for size, rgba in images:
        if size <= DIB_MAX:
            payload.append((size, ico_dib_entry(rgba, size)))
        else:
            payload.append((size, encode_png(rgba, size, size)))
    header = struct.pack("<HHH", 0, 1, len(payload))
    offset = len(header) + 16 * len(payload)
    entries = []
    for size, data in payload:
        dim = 0 if size >= 256 else size
        entries.append(struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset))
        offset += len(data)
    blob = header + b"".join(entries) + b"".join(data for _s, data in payload)
    with open(target, "wb") as handle:
        handle.write(blob)
    return len(blob)


def verify_ico(path, expect):
    """读回 ICO 自检：文件头、目录项数量、每个宽高字节与数据格式。"""
    with open(path, "rb") as handle:
        data = handle.read()
    if data[:4] != b"\x00\x00\x01\x00":
        raise AssertionError("ICO 文件头不合法")
    count = struct.unpack("<H", data[4:6])[0]
    if count != len(expect):
        raise AssertionError("ICO 条目数 %d != %d" % (count, len(expect)))
    seen = []
    for i in range(count):
        w, h, _c, _r, planes, bpp, size, off = struct.unpack("<BBBBHHII", data[6 + 16 * i:22 + 16 * i])
        dim = w or 256
        if dim != expect[i] or (h or 256) != expect[i]:
            raise AssertionError("第 %d 个条目尺寸 %d 与预期 %d 不符" % (i, dim, expect[i]))
        if planes != 1 or bpp != 32:
            raise AssertionError("第 %d 个条目位深异常" % i)
        body = data[off:off + size]
        kind = "png" if body[:8] == b"\x89PNG\r\n\x1a\n" else "dib"
        if dim <= DIB_MAX and kind != "dib":
            raise AssertionError("%d 尺寸应为 DIB" % dim)
        if dim > DIB_MAX and kind != "png":
            raise AssertionError("%d 尺寸应为 PNG" % dim)
        if kind == "dib":
            (hsize,) = struct.unpack("<I", body[:4])
            if hsize != 40 or len(body) != 40 + dim * dim * 4 + ((dim + 31) // 32) * 4 * dim:
                raise AssertionError("%d 尺寸 DIB 结构不合法" % dim)
        seen.append("%d(%s)" % (dim, kind))
    return seen


# --------------------------------------------------------------- 命令行

def render_size(size, source):
    """source 为 None 时按内置设计画，否则把自备图稿缩放到该尺寸。"""
    if source is None:
        return draw_icon(size)
    w, h, px = source
    if w == size and h == size:
        return bytearray(px)
    return resize_rgba(px, w, h, size, size)


def render_images(source):
    return [(size, render_size(size, source)) for size in SIZES]


def main(argv):
    paths = []
    for arg in argv:
        if arg in ("-h", "--help"):
            print(__doc__)
            return 0
        if arg.startswith("-"):
            raise SystemExit("未知参数：%s\n\n%s" % (arg, __doc__))
        paths.append(arg)
    if len(paths) > 2:
        raise SystemExit(__doc__)

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    source = None
    write_png = False
    if paths:
        # 自备图稿：make-icon.py 自备.png [输出.ico]
        src_path = os.path.abspath(paths[0])
        ico_path = os.path.abspath(paths[1]) if len(paths) >= 2 else os.path.splitext(src_path)[0] + ".ico"
        if not os.path.isfile(src_path):
            raise SystemExit("找不到源图：%s" % src_path)
        with open(src_path, "rb") as handle:
            w, h, px = decode_png(handle.read())
        if (w, h) == (PNG_SIZE, PNG_SIZE) and bytes(px) == bytes(draw_icon(PNG_SIZE)):
            print("源图就是本脚本产出的内置图稿，按内置设计逐尺寸重画。")
        else:
            source = (w, h, px)
            print("使用自备图稿：%s（%dx%d）" % (src_path, w, h))
    else:
        # 内置设计：直接写根目录的两个文件
        png_path = os.path.join(root, "agent-icon.png")
        ico_path = os.path.join(root, "agent.ico")
        write_png = True

    images = render_images(source)
    if write_png:
        blob = encode_png(draw_icon(PNG_SIZE), PNG_SIZE, PNG_SIZE)
        with open(png_path, "wb") as handle:
            handle.write(blob)
        print("%s  %dx%d  %d 字节" % (png_path, PNG_SIZE, PNG_SIZE, len(blob)))
    size_bytes = build_ico(images, ico_path)
    seen = verify_ico(ico_path, list(SIZES))
    print("%s  %d 个尺寸  %d 字节" % (ico_path, len(images), size_bytes))
    print("尺寸：" + " ".join(seen))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
