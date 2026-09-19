"""被控端屏幕采集与 JPEG 编码。

采集线程独占一个 mss 实例;画面无变化时跳过编码,空闲桌面几乎不耗 CPU。
帧内附带光标位置(仅 Windows),由主控端绘制虚拟指针。

低延迟管线:
- 缩放模式(scale<1):Windows 下用 StretchBlt 直接按目标分辨率抓屏(源数据量降至
  1/scale^2),再用 libjpeg-turbo 从 BGRA 缓冲区零拷贝编码;
- 全尺寸模式:mss 抓屏 + turbojpeg 直编(跳过 PIL 像素转换);
- turbojpeg 不可用时自动回退 PIL(同样使用 4:2:0 子采样)。
"""

import ctypes
import io
import os
import sys
import threading
import time
from ctypes import wintypes

import mss
from PIL import Image

try:
    import numpy as np
    from turbojpeg import TurboJPEG, TJPF_BGRX, TJPF_RGB

    def _find_turbojpeg():
        cands = [os.environ.get("MOLI_TURBOJPEG", "")]
        if getattr(sys, "frozen", False):
            base = os.path.dirname(sys.executable)
            cands += [
                os.path.join(base, "_internal", "turbojpeg.dll"),
                os.path.join(base, "turbojpeg.dll"),
            ]
        cands += [
            r"C:\libjpeg-turbo64\bin\turbojpeg.dll",
            r"C:\Program Files\libjpeg-turbo64\bin\turbojpeg.dll",
            r"C:\libjpeg-turbo\bin\turbojpeg.dll",
        ]
        lib = ctypes.util.find_library("turbojpeg")
        if lib:
            cands.append(lib)
        for c in cands:
            if c and os.path.isfile(c):
                return c
        return None

    _TJ = TurboJPEG(_find_turbojpeg())
    _HAS_TJ = True
except Exception:
    _HAS_TJ = False

_IS_WINDOWS = sys.platform == "win32"
_JPEG_SUBSAMPLING = 2  # 4:2:0 — 体积更小、编码更快,桌面内容视觉损失可忽略

_SRCCOPY = 0x00CC0020
_HALFTONE = 4


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
        ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", ctypes.c_long), ("biYPelsPerMeter", ctypes.c_long),
        ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD),
    ]


def _win_stretch_grab(mon, virt, dw, dh):
    """用 StretchBlt 直接抓取缩放后的屏幕(物理像素坐标,进程需 DPI-aware)。"""
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    src_x = int(mon["left"] - virt["left"])
    src_y = int(mon["top"] - virt["top"])
    sw, sh = int(mon["width"]), int(mon["height"])
    hdc = user32.GetDC(0)
    mem_dc = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, dw, dh)
    try:
        gdi32.SelectObject(mem_dc, bmp)
        gdi32.SetStretchBltMode(mem_dc, _HALFTONE)
        ok = gdi32.StretchBlt(mem_dc, 0, 0, dw, dh, hdc, src_x, src_y, sw, sh, _SRCCOPY)
        if not ok:
            raise RuntimeError("StretchBlt 失败")
        buf = ctypes.create_string_buffer(dw * dh * 4)
        bih = _BITMAPINFOHEADER()
        bih.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        bih.biWidth = dw
        bih.biHeight = -dh  # 负值 = 自上而下,BGRA 顺序
        bih.biPlanes = 1
        bih.biBitCount = 32
        bih.biCompression = 0  # BI_RGB
        gdi32.GetDIBits(mem_dc, bmp, 0, dh, buf, ctypes.byref(bih), 0)
        return buf.raw
    finally:
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(0, hdc)


class Frame:
    __slots__ = ("jpeg", "width", "height", "cur_x", "cur_y", "cursor_visible")

    def __init__(self, jpeg, width, height, cur_x, cur_y, cursor_visible):
        self.jpeg = jpeg
        self.width = width
        self.height = height
        self.cur_x = cur_x
        self.cur_y = cur_y
        self.cursor_visible = cursor_visible


def _encode_bgra(raw, w, h, quality):
    """BGRA 原始缓冲 → JPEG(优先 turbojpeg 零拷贝直编)。"""
    if _HAS_TJ:
        arr = np.frombuffer(raw, dtype=np.uint8).reshape((h, w, 4))
        return _TJ.encode(arr, quality=int(quality),
                          pixel_format=TJPF_BGRX,
                          jpeg_subsample=_JPEG_SUBSAMPLING)
    img = Image.frombytes("RGB", (w, h), raw, "raw", "BGRX")
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=int(quality), subsampling=_JPEG_SUBSAMPLING)
    return buf.getvalue()


class ScreenCapture:
    def __init__(self, monitor=1):
        self._lock = threading.Lock()
        self._monitor = monitor
        self._sct = None
        self._last_raw = None
        self._last_size = (0, 0)
        self._invalid = True
        self._auto_cache = {}

    def set_monitor(self, index):
        with self._lock:
            if self._monitor != index:
                self._monitor = index
                self._invalid = True

    def invalidate(self):
        """画质/缩放等参数变化后强制重新编码一帧。"""
        with self._lock:
            self._invalid = True

    def monitors_full(self):
        """原始显示器列表(含 left/top),下标 0 为全部屏幕组成的虚拟屏。"""
        with self._lock:
            if self._sct is None:
                self._sct = mss.mss()
                self._invalid = True
            return [dict(m) for m in self._sct.monitors]

    def monitors(self):
        """给主控端看的精简列表:[{i, w, h}, ...]。"""
        return [
            {"i": i, "w": int(m["width"]), "h": int(m["height"])}
            for i, m in enumerate(self.monitors_full())
        ]

    def auto_scale_for(self, target_scale):
        """一次性实测本机:缩放抓屏与全尺寸抓屏哪个更快,返回更快的 scale。

        StretchBlt 在真实 GPU 上远快于全尺寸 BitBlt;在软件渲染环境(如部分虚拟机)
        则可能相反,因此实测后缓存结论。
        """
        key = (self._monitor, target_scale)
        if key in self._auto_cache:
            return self._auto_cache[key]
        with self._lock:
            if self._sct is None:
                self._sct = mss.mss()
                self._invalid = True
            mons = self._sct.monitors
            idx = min(max(self._monitor, 0), len(mons) - 1)
            mon = mons[idx]
            w0, h0 = int(mon["width"]), int(mon["height"])
            if not (_IS_WINDOWS and w0 >= 3000):
                self._auto_cache[key] = 1.0
                return 1.0
            w = max(1, round(w0 * target_scale))
            h = max(1, round(h0 * target_scale))

            def sample(fn, n=2):
                best = float("inf")
                for _ in range(n):
                    t0 = time.perf_counter()
                    fn()
                    best = min(best, time.perf_counter() - t0)
                return best

            try:
                self._sct.grab(mon)  # 预热
                t_full = sample(lambda: self._sct.grab(mon))
                t_scale = sample(lambda: _win_stretch_grab(mon, mons[0], w, h))
            except Exception:
                t_scale = float("inf")
            choice = target_scale if t_scale < t_full else 1.0
            self._auto_cache[key] = choice
            return choice

    def _cursor_rel(self, mon):
        if not _IS_WINDOWS:
            return 0, 0, False
        pt = _POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        x = pt.x - mon["left"]
        y = pt.y - mon["top"]
        if 0 <= x < mon["width"] and 0 <= y < mon["height"]:
            return x, y, True
        return 0, 0, False

    def _changed(self, raw, w, h):
        if not self._invalid and self._last_size == (w, h) and raw == self._last_raw:
            return False
        self._last_raw = raw
        self._last_size = (w, h)
        self._invalid = False
        return True

    def grab(self, quality, scale):
        """采集并编码一帧;画面无变化时返回 None。scale 为 0~1 的目标比例。"""
        with self._lock:
            if self._sct is None:
                self._sct = mss.mss()
                self._invalid = True
            mons = self._sct.monitors
            idx = min(max(self._monitor, 0), len(mons) - 1)
            mon = mons[idx]
            w0, h0 = int(mon["width"]), int(mon["height"])

            if _IS_WINDOWS and scale and scale < 1.0:
                w = max(1, round(w0 * scale))
                h = max(1, round(h0 * scale))
                raw = _win_stretch_grab(mon, mons[0], w, h)
                if not self._changed(raw, w, h):
                    return None
                jpeg = _encode_bgra(raw, w, h, quality)
            else:
                shot = self._sct.grab(mon)
                raw = shot.bgra
                if not self._changed(raw, w0, h0):
                    return None
                if scale and scale < 1.0:  # 非 Windows 回退:抓全屏后 PIL 缩放
                    img = Image.frombytes("RGB", shot.size, raw, "raw", "BGRX")
                    w = max(1, round(w0 * scale))
                    h = max(1, round(h0 * scale))
                    img = img.resize((w, h), Image.BILINEAR)
                    if _HAS_TJ:
                        jpeg = _TJ.encode(np.asarray(img), quality=int(quality),
                                          pixel_format=TJPF_RGB,
                                          jpeg_subsample=_JPEG_SUBSAMPLING)
                    else:
                        buf = io.BytesIO()
                        img.save(buf, "JPEG", quality=int(quality),
                                 subsampling=_JPEG_SUBSAMPLING)
                        jpeg = buf.getvalue()
                else:
                    w, h = w0, h0
                    jpeg = _encode_bgra(raw, w, h, quality)

            cx, cy, cvis = self._cursor_rel(mon)
            kx = min(65535, round(cx * w / w0))
            ky = min(65535, round(cy * h / h0))
            return Frame(jpeg, w, h, kx, ky, cvis)
