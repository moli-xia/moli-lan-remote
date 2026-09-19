"""Qt 桌面应用共用工具:配置持久化、程序化图标、日志。"""

import json
import sys
import time
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSharedMemory, Qt
from PySide6.QtGui import (
    QColor, QIcon, QLinearGradient, QPainter, QPainterPath, QPalette, QPen, QPixmap,
)


def settings_dir() -> Path:
    if sys.platform == "win32":
        base = Path(__import__("os").environ.get("APPDATA", Path.home()))
    else:
        base = Path.home() / ".config"
    d = base / "MoliLanRemote"
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_json(name, default=None):
    p = settings_dir() / name
    try:
        return {**(default or {}), **json.loads(p.read_text("utf-8"))}
    except Exception:
        return dict(default or {})


def save_json(name, data):
    p = settings_dir() / name
    try:
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    except Exception:
        pass


def make_logo_pixmap(size=256, accent="#3b82f6", accent2="#22d3ee") -> QPixmap:
    """程序化绘制 Logo:圆角屏幕 + 底座 + 渐变圆点,无需外部图标资源。"""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    s = size / 256.0
    # 屏幕
    grad = QLinearGradient(24 * s, 24 * s, 232 * s, 200 * s)
    grad.setColorAt(0, QColor(accent))
    grad.setColorAt(1, QColor(accent2))
    path = QPainterPath()
    path.addRoundedRect(QRectF(28 * s, 40 * s, 200 * s, 132 * s), 20 * s, 20 * s)
    p.fillPath(path, grad)
    # 高光
    hl = QPainterPath()
    hl.addRoundedRect(QRectF(44 * s, 56 * s, 110 * s, 20 * s), 10 * s, 10 * s)
    p.fillPath(hl, QColor(255, 255, 255, 70))
    # 底座
    stand = QPainterPath()
    stand.addRoundedRect(QRectF(108 * s, 176 * s, 40 * s, 22 * s), 6 * s, 6 * s)
    p.fillPath(stand, QColor(accent))
    base = QPainterPath()
    base.addRoundedRect(QRectF(76 * s, 196 * s, 104 * s, 14 * s), 7 * s, 7 * s)
    p.fillPath(base, QColor(accent))
    # 连接圆点
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#ffffff"))
    p.drawEllipse(QPointF(196 * s, 106 * s), 18 * s, 18 * s)
    p.setBrush(QColor(34, 197, 94))
    p.drawEllipse(QPointF(196 * s, 106 * s), 12 * s, 12 * s)
    p.end()
    return pm


def app_icon(accent="#3b82f6", accent2="#22d3ee") -> QIcon:
    icon = QIcon()
    for s in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(make_logo_pixmap(s, accent, accent2))
    return icon


def cursor_pixmap(size=22) -> QPixmap:
    """白色箭头鼠标指针(带描边),用于视频区虚拟光标。"""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    s = size / 22.0
    path = QPainterPath()
    path.moveTo(3 * s, 1.5 * s)
    path.lineTo(17 * s, 10.5 * s)
    path.lineTo(10.4 * s, 11.6 * s)
    path.lineTo(13.6 * s, 18.4 * s)
    path.lineTo(10.8 * s, 19.6 * s)
    path.lineTo(7.6 * s, 12.6 * s)
    path.lineTo(3 * s, 15.8 * s)
    path.closeSubpath()
    p.setPen(QPen(QColor(20, 27, 45), 1.5 * s))
    p.setBrush(QColor(255, 255, 255, 235))
    p.drawPath(path)
    p.end()
    return pm


def make_logger(logfile: Path):
    """返回 log_fn(str):同时写文件与 stdout,带时间戳。"""
    def log(msg):
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"
        try:
            logfile.parent.mkdir(parents=True, exist_ok=True)
            with open(logfile, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass
        try:
            print(line, flush=True)
        except Exception:
            pass
    return log


# ---------------------------------------------------------------- 主题

def apply_dark_theme(app):
    """Fusion 风格 + 深色调色板:保证下拉框/滚动条/提示等原生部件一致暗色。"""
    app.setStyle("Fusion")
    p = QPalette()
    p.setColor(QPalette.Window, QColor("#0e1526"))
    p.setColor(QPalette.WindowText, QColor("#e8edf6"))
    p.setColor(QPalette.Base, QColor("#101a30"))
    p.setColor(QPalette.AlternateBase, QColor("#131e36"))
    p.setColor(QPalette.Text, QColor("#e8edf6"))
    p.setColor(QPalette.Button, QColor("#1b2946"))
    p.setColor(QPalette.ButtonText, QColor("#e8edf6"))
    p.setColor(QPalette.Highlight, QColor("#3b82f6"))
    p.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    p.setColor(QPalette.ToolTipBase, QColor("#131e36"))
    p.setColor(QPalette.ToolTipText, QColor("#e8edf6"))
    p.setColor(QPalette.PlaceholderText, QColor("#64748b"))
    p.setColor(QPalette.Link, QColor("#60a5fa"))
    for role in (QPalette.Text, QPalette.ButtonText, QPalette.WindowText):
        p.setColor(QPalette.Disabled, role, QColor("#55637a"))
    app.setPalette(p)


def enable_dark_titlebar(widget):
    """Windows 深色标题栏(DWM 沉浸式暗色模式),失败静默忽略。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        hwnd = int(widget.winId())
        value = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 20, ctypes.byref(value), ctypes.sizeof(value))  # DWMWA_USE_IMMERSIVE_DARK_MODE
    except Exception:
        pass


# ------------------------------------------------------------- 单实例

def ensure_single_instance(key: str):
    """返回 QSharedMemory 表示本进程持有单实例锁;已有实例时返回 None。"""
    shm = QSharedMemory(key)
    if shm.attach():
        return None
    if not shm.create(1):
        return None
    return shm
