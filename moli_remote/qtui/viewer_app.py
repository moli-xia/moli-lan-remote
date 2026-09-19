"""魔力局域网远程助手 主控端桌面应用:设备发现 / 验证码连接 / 视频窗口 / 键鼠控制。"""

import sys
import threading
import time

from PySide6.QtCore import QObject, Qt, QRectF, QTimer, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QPushButton,
    QSizePolicy, QStackedWidget, QVBoxLayout, QWidget,
)

from ..client import ClientWorker
from ..discovery import scan
from .common import (
    app_icon, apply_dark_theme, cursor_pixmap, enable_dark_titlebar,
    ensure_single_instance, load_json, make_logger, save_json, settings_dir,
)

LOGFILE = settings_dir() / "viewer.log"

STYLE = """
QMainWindow, QWidget { background:#0e1526; color:#e8edf6; font-size:14px; }
QLabel#title { font-size:20px; font-weight:700; }
QLabel#dim { color:#93a1b8; font-size:12px; }
QLabel#err { color:#f87171; font-size:12px; }
QLabel#status { color:#8fd3a0; font-size:12px; font-family:Consolas,monospace; }
QLabel#status[watch="1"] { color:#f0c674; }
QLineEdit, QComboBox {
    background:#101a30; border:1px solid #2a3a5f; border-radius:8px; padding:7px 10px; color:#e8edf6;
}
QLineEdit:focus, QComboBox:focus { border-color:#3b82f6; }
QComboBox::drop-down { border:none; width:22px; }
QComboBox::down-arrow {
    image:none; border-left:4px solid transparent; border-right:4px solid transparent;
    border-top:5px solid #93a1b8; margin-right:8px;
}
QComboBox QAbstractItemView {
    background:#101a30; color:#e8edf6; border:1px solid #2a3a5f;
    selection-background-color:#1d3557; selection-color:#ffffff; outline:none;
}
QListWidget {
    background:#101a30; border:1px solid #22304e; border-radius:10px; padding:6px; font-size:14px;
}
QListWidget::item { padding:9px 8px; border-radius:6px; }
QListWidget::item:selected { background:#1d3557; }
QPushButton { padding:8px 16px; border-radius:8px; background:#1b2946; border:1px solid #2a3a5f; }
QPushButton:hover { background:#24365c; }
QPushButton#primary { background:#2563eb; border:none; font-weight:600; }
QPushButton#primary:hover { background:#3b82f6; }
QPushButton#danger { color:#fca5a5; }
QCheckBox { spacing:6px; }
QCheckBox::indicator { width:16px; height:16px; }
QToolTip { background:#131e36; color:#e8edf6; border:1px solid #2a3a5f; padding:4px 8px; }
QScrollBar:vertical { background:transparent; width:10px; margin:0; }
QScrollBar::handle:vertical { background:#2a3a5f; border-radius:5px; min-height:24px; }
QScrollBar::handle:vertical:hover { background:#3b4f7a; }
QScrollBar::add-line, QScrollBar::sub-line { height:0; }
QScrollBar::add-page, QScrollBar::sub-page { background:transparent; }
QFrame#toolbar { background:#131e36; border:1px solid #22304e; border-radius:10px; }
"""


class ScanThread(threading.Thread):
    class Sig(QObject):
        done = Signal(list)

    def __init__(self):
        super().__init__(daemon=True)
        self.sig = self.Sig()

    def run(self):
        try:
            devs = scan(2.2)  # UDP 广播 + TCP 网段兜底
        except Exception:
            devs = []
        self.sig.done.emit(devs)


class VideoWidget(QWidget):
    """视频渲染区:黑底等比缩放 + 光标,键盘鼠标事件回调给主窗口。

    光标低延迟策略:输入时立即绘制"本地预测光标"(不等远程帧回显),
    远程帧携带的真实光标追上后自动收敛;800ms 无输入则完全交给远程光标。
    """

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._img = None
        self._cursor = (0, 0, False)
        self._local_cursor = None   # (nx, ny) 本地预测位置
        self._local_at = 0.0
        self._reconnecting = False
        self._rect = QRectF()
        self._cursor_pm = cursor_pixmap()

    def set_frame(self, img):
        self._img = img
        self.update()

    def set_reconnecting(self, on):
        if self._reconnecting != on:
            self._reconnecting = on
            self.update()

    def set_cursor(self, info):
        """远程帧光标;若与本地预测接近则收敛(清除预测,交给远程)。"""
        self._cursor = info
        img = self._img
        if self._local_cursor is not None and img is not None and not img.isNull():
            rx, ry, vis = info
            w, h = img.width(), img.height()
            lx, ly = self._local_cursor
            if vis and w and h and \
                    abs(rx / w - lx) < 0.01 and abs(ry / h - ly) < 0.01:
                self._local_cursor = None
        self.update()

    def set_local_cursor(self, nx, ny):
        """本地即时预测光标(鼠标输入的瞬间反馈)。"""
        self._local_cursor = (nx, ny)
        self._local_at = time.monotonic()
        self.update()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.fillRect(self.rect(), Qt.black)
        img = self._img
        if img is None or img.isNull():
            p.setPen(Qt.gray)
            p.drawText(self.rect(), Qt.AlignCenter, "等待画面…")
            return
        w, h = img.width(), img.height()
        cw, ch = self.width(), self.height()
        scale = min(cw / w, ch / h)
        dw, dh = w * scale, h * scale
        x, y = (cw - dw) / 2, (ch - dh) / 2
        self._rect = QRectF(x, y, dw, dh)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.drawImage(self._rect, img)
        if self._reconnecting:
            band = QRectF(0, ch / 2 - 26, cw, 52)
            p.fillRect(band, QColor(10, 15, 26, 200))
            p.setPen(QColor("#f0c674"))
            f = p.font()
            f.setPointSize(13)
            p.setFont(f)
            p.drawText(band, Qt.AlignCenter, "连接断开,正在自动重连…(画面已暂停)")
        if self._rect.width() <= 0:
            p.end()
            return
        # 本地预测光标优先(输入即时反馈);超时/收敛后交给远程光标
        lc = self._local_cursor
        if lc is not None and time.monotonic() - self._local_at > 0.8:
            self._local_cursor = None
            lc = None
        if lc is not None:
            px, py = x + lc[0] * dw, y + lc[1] * dh
            p.setOpacity(0.9)
            p.drawPixmap(int(px), int(py), self._cursor_pm)
            p.setOpacity(1.0)
        else:
            cx, cy, visible = self._cursor
            if visible:
                px = x + cx * scale
                py = y + cy * scale
                p.drawPixmap(int(px), int(py), self._cursor_pm)
        p.end()

    # ---------------------------------------------------------- 输入事件
    def _norm(self, pos):
        r = self._rect
        if r.width() <= 0:
            return None
        nx = (pos.x() - r.x()) / r.width()
        ny = (pos.y() - r.y()) / r.height()
        return (min(1.0, max(0.0, nx)), min(1.0, max(0.0, ny)))

    def mouseMoveEvent(self, ev):
        self.win.on_video_mouse(ev, "move")

    def mousePressEvent(self, ev):
        self.setFocus()
        self.grabMouse()
        self.win.on_video_mouse(ev, "down")

    def mouseReleaseEvent(self, ev):
        if self.mouseGrabber() is self:
            self.releaseMouse()
        self.win.on_video_mouse(ev, "up")

    def wheelEvent(self, ev):
        self.win.on_video_wheel(ev)

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key_Escape and self.window().isFullScreen():
            self.window().toggle_fullscreen()
            return
        self.win.on_video_key(ev, True)

    def keyReleaseEvent(self, ev):
        self.win.on_video_key(ev, False)


class MainWindow(QMainWindow):
    def __init__(self, auto=None):
        super().__init__()
        self.setWindowTitle("魔力局域网远程助手 · 主控端")
        self.setWindowIcon(app_icon("#22c55e", "#4ade80"))
        self.resize(1100, 700)
        self.setStyleSheet(STYLE)

        self.cfg = load_json("viewer.json", {"tokens": {}})
        self.worker = None
        self.control_enabled = True
        self.control = False
        self.held = False
        self._last_move = 0.0
        self.auto = auto  # {"ip","port","code","quit"} 测试用
        self._auto_frames = 0

        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)
        self.stack.addWidget(self._build_connect_page())
        self.stack.addWidget(self._build_session_page())

        self.scan_timer = None
        self.start_scan()

    # ------------------------------------------------------------- 连接页
    def _build_connect_page(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(60, 40, 60, 30)
        v.setSpacing(12)

        t = QLabel("魔力局域网远程助手 · 主控端")
        t.setObjectName("title")
        v.addWidget(t)
        v.addWidget(self._dim("选择局域网内的设备,或直接输入地址连接(被控端控制台显示验证码)"))

        self.device_list = QListWidget()
        self.device_list.itemDoubleClicked.connect(lambda *_: self.connect_selected())
        v.addWidget(self.device_list, 2)

        row = QHBoxLayout()
        self.btn_refresh = QPushButton("刷新设备列表")
        self.btn_refresh.clicked.connect(self.start_scan)
        row.addWidget(self.btn_refresh)
        row.addStretch(1)
        v.addLayout(row)

        v.addWidget(self._dim("手动连接(地址 形如 192.168.1.5:8765):"))
        row2 = QHBoxLayout()
        self.addr_edit = QLineEdit()
        self.addr_edit.setPlaceholderText("192.168.1.5:8765")
        self.addr_edit.setText(self.cfg.get("last_addr", ""))
        row2.addWidget(self.addr_edit, 3)
        self.code_edit = QLineEdit()
        self.code_edit.setPlaceholderText("6 位验证码")
        row2.addWidget(self.code_edit, 2)
        v.addLayout(row2)

        btn_row = QHBoxLayout()
        self.btn_connect = QPushButton("连  接")
        self.btn_connect.setObjectName("primary")
        self.btn_connect.clicked.connect(self.connect_manual)
        btn_row.addWidget(self.btn_connect)
        btn_row.addStretch(1)
        v.addLayout(btn_row)

        self.connect_msg = QLabel(" ")
        self.connect_msg.setObjectName("dim")
        v.addWidget(self.connect_msg)
        tip = QLabel("提示:列表为空或连不上时,请确认被控端已运行且其防火墙已放行"
                     "(被控端窗口可一键修复);跨网段设备请手动输入 IP 连接")
        tip.setObjectName("dim")
        tip.setWordWrap(True)
        v.addWidget(tip)
        v.addStretch(1)
        return page

    def _build_session_page(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        bar = QFrame()
        bar.setObjectName("toolbar")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(12, 8, 12, 8)
        bl.setSpacing(8)
        self.brand = QLabel("魔力远程助手")
        self.brand.setStyleSheet("font-weight:700;")
        bl.addWidget(self.brand)

        self.chk_control = QCheckBox("控制模式")
        self.chk_control.setChecked(True)
        self.chk_control.stateChanged.connect(self.on_control_toggle)
        bl.addWidget(self.chk_control)

        bl.addWidget(QLabel("画质"))
        self.quality = QComboBox()
        self.quality.addItems(["流畅", "高清", "超清"])
        self.quality.setCurrentIndex(1)
        self.quality.currentIndexChanged.connect(
            lambda i: self.send_cfg({"preset": ["smooth", "hd", "best"][i]}))
        bl.addWidget(self.quality)

        bl.addWidget(QLabel("屏幕"))
        self.monitors = QComboBox()
        self.monitors.currentIndexChanged.connect(
            lambda i: self.send_cfg({"monitor": i}) if self.worker else None)
        bl.addWidget(self.monitors)

        self.btn_text = QPushButton("文字")
        self.btn_text.setToolTip("打开文字输入条,支持中文输入法,回车发送到远程")
        self.btn_text.clicked.connect(self.toggle_textbar)
        bl.addWidget(self.btn_text)
        self.btn_clip_get = QPushButton("取剪贴板")
        self.btn_clip_get.setToolTip("读取远程剪贴板并复制到本机")
        self.btn_clip_get.clicked.connect(lambda: self.send_json({"t": "clip_get"}))
        bl.addWidget(self.btn_clip_get)
        self.btn_clip_set = QPushButton("贴到远程")
        self.btn_clip_set.setToolTip("把本机剪贴板内容写入远程剪贴板")
        self.btn_clip_set.clicked.connect(self.paste_clipboard)
        bl.addWidget(self.btn_clip_set)
        self.btn_full = QPushButton("全屏")
        self.btn_full.setToolTip("全屏显示远程画面(按 Esc 退出)")
        self.btn_full.clicked.connect(self.toggle_fullscreen)
        bl.addWidget(self.btn_full)
        self.btn_leave = QPushButton("断开")
        self.btn_leave.setToolTip("断开连接,返回设备列表")
        self.btn_leave.setObjectName("danger")
        self.btn_leave.clicked.connect(self.disconnect)
        bl.addWidget(self.btn_leave)

        v.addWidget(bar)

        self.video = VideoWidget(self)
        v.addWidget(self.video, 1)

        textbar = QFrame()
        textbar.setObjectName("toolbar")
        tl = QHBoxLayout(textbar)
        tl.setContentsMargins(10, 8, 10, 8)
        self.text_edit = QLineEdit()
        self.text_edit.setPlaceholderText("输入文字(支持中文输入法),回车发送到远程电脑")
        self.text_edit.returnPressed.connect(self.send_text)
        tl.addWidget(self.text_edit, 1)
        btn_close_text = QPushButton("关闭")
        btn_close_text.clicked.connect(self.toggle_textbar)
        tl.addWidget(btn_close_text)
        self.textbar = textbar
        self.textbar.hide()
        v.addWidget(textbar)

        foot = QHBoxLayout()
        foot.setContentsMargins(10, 4, 10, 6)
        self.status = QLabel(" ")
        self.status.setObjectName("status")
        foot.addWidget(self.status)
        foot.addStretch(1)
        v.addLayout(foot)
        return page

    def _dim(self, text):
        lab = QLabel(text)
        lab.setObjectName("dim")
        return lab

    def set_msg(self, text, error=False):
        """连接页提示文字(错误时红色)。"""
        self.connect_msg.setObjectName("err" if error else "dim")
        self.connect_msg.style().unpolish(self.connect_msg)
        self.connect_msg.style().polish(self.connect_msg)
        self.connect_msg.setText(text)

    # ----------------------------------------------------------- 设备发现
    def start_scan(self):
        self._scan_gen = getattr(self, "_scan_gen", 0) + 1
        gen = self._scan_gen
        self.btn_refresh.setEnabled(False)
        self.device_list.clear()
        QListWidgetItem("正在扫描局域网…", self.device_list)
        self._scan = ScanThread()
        self._scan.sig.done.connect(
            lambda devs, g=gen: self.on_scanned(devs) if g == self._scan_gen else None)
        self._scan.start()
        # 兜底:扫描线程意外未回时恢复界面,避免按钮永久禁用
        QTimer.singleShot(9000, lambda: self._scan_timeout(gen))

    def _scan_timeout(self, gen):
        if gen == self._scan_gen and not self.btn_refresh.isEnabled():
            self.device_list.clear()
            QListWidgetItem("扫描超时 — 请点击\"刷新设备列表\"重试,"
                            "或手动输入 IP:端口 连接", self.device_list)
            self.btn_refresh.setEnabled(True)

    def on_scanned(self, devs):
        self.device_list.clear()
        log = make_logger(LOGFILE)
        if not devs:
            log("扫描完成:未发现设备(UDP+TCP 均无应答)")
            QListWidgetItem("未发现设备 — 请确认:①被控端正在运行(托盘有图标) "
                            "②两台电脑同一网段 ③被控端防火墙已放行。"
                            "也可手动输入 IP:端口 连接", self.device_list)
            self.btn_refresh.setEnabled(True)
            return
        log("扫描完成:" + "; ".join(
            f"{d.get('name')}@{d['ip']}({d.get('via')})" for d in devs))
        for d in devs:
            tag = "  ← 本机" if d.get("self") else ""
            item = QListWidgetItem(f"{d.get('name','?')}   {d.get('os','')}   "
                                   f"{d['ip']}:{d['port']}{tag}")
            item.setData(Qt.UserRole, (d["ip"], int(d["port"])))
            self.device_list.addItem(item)
        self.btn_refresh.setEnabled(True)

    def connect_selected(self):
        item = self.device_list.currentItem()
        if not item:
            return
        data = item.data(Qt.UserRole)
        if not data:
            return
        ip, port = data
        self.code_edit.setFocus()
        self.begin_connect(ip, port, ask_code=True)

    def connect_manual(self):
        raw = self.addr_edit.text().strip()
        if ":" not in raw:
            raw += ":8765"
        host, _, port = raw.rpartition(":")
        if not host or not port.isdigit():
            self.set_msg("地址格式:192.168.1.5:8765", error=True)
            return
        self.begin_connect(host, int(port), ask_code=False)

    # --------------------------------------------------------------- 连接
    def begin_connect(self, host, port, ask_code=False):
        token = self.cfg.get("tokens", {}).get(f"{host}:{port}")
        code = self.code_edit.text().strip() or None
        if not token and not code:
            self.set_msg("请输入 6 位验证码", error=True)
            return
        if code and len(code) != 6:
            self.set_msg("验证码为 6 位数字", error=True)
            return
        self.set_msg(" ")
        self.btn_connect.setEnabled(False)
        # 有验证码时优先用验证码(可主动接管),否则用保存的 Token 快速重连
        self.start_worker(host, port, code=code, token=(None if code else token))

    def start_worker(self, host, port, code=None, token=None):
        self.stop_worker()
        self.current = (host, port)
        self._had_session = False
        self.video.set_reconnecting(False)
        self.worker = ClientWorker(host, port, code=code, token=token)
        w = self.worker
        w.sig.frame.connect(self.on_frame)
        w.sig.event.connect(self.on_event)
        w.sig.status.connect(self.on_status)
        w.sig.state.connect(self.on_state)
        w.start()
        self.stack.setCurrentIndex(1)
        self.status.setText(f"正在连接 {host}:{port} …")

    def stop_worker(self):
        if self.worker:
            try:
                self.worker.stop()
            except Exception:
                pass  # 任何停止失败都不阻塞 UI 切换
            self.worker = None

    def disconnect(self):
        self.stop_worker()
        self.stack.setCurrentIndex(0)
        self.btn_connect.setEnabled(True)
        # 稍等 ws 完全关闭后再扫描,避免与断开握手产生竞态
        QTimer.singleShot(300, self.start_scan)

    # ------------------------------------------------------------ 事件槽
    def on_frame(self, img):
        self._auto_frames += 1
        self.video.set_frame(img)

    def on_status(self, st):
        self.video.set_cursor(st.get("cursor", (0, 0, False)))
        watch = not (self.control and self.control_enabled)
        self.status.setProperty("watch", "1" if watch else "0")
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
        self.status.setText(
            f"{st['w']}×{st['h']} · {st['fps']} fps · {st['rtt']} ms"
            + ("  · 观看模式" if watch else ""))

    def on_event(self, d):
        t = d.get("t")
        if t == "auth_ok":
            host, port = self.current
            self.cfg.setdefault("tokens", {})[f"{host}:{port}"] = d.get("token")
            self.cfg["last_addr"] = f"{host}:{port}"
            save_json("viewer.json", self.cfg)
            self.control = d.get("control", False)
            self.chk_control.setChecked(self.control)
            self.chk_control.setEnabled(True)
            mons = d.get("monitors", [])
            self.monitors.blockSignals(True)
            self.monitors.clear()
            for m in mons:
                label = f"全部屏幕 ({m['w']}×{m['h']})" if m["i"] == 0 else f"显示器 {m['i']} ({m['w']}×{m['h']})"
                self.monitors.addItem(label, m["i"])
            self.monitors.setCurrentIndex(max(0, self.monitors.findData(d.get("settings", {}).get("monitor", 1))))
            self.monitors.blockSignals(False)
            self.video.setFocus()
            if self.auto:
                QTimer.singleShot(int(self.auto.get("quit", 6)) * 1000, self._auto_finish)
        elif t == "settings":
            s = d.get("settings", {})
            preset = s.get("preset", "hd")
            if preset != "custom":
                self.quality.blockSignals(True)
                self.quality.setCurrentIndex({"smooth": 0, "hd": 1, "best": 2}.get(preset, 1))
                self.quality.blockSignals(False)
            i = self.monitors.findData(s.get("monitor"))
            if i is not None and i >= 0:
                self.monitors.blockSignals(True)
                self.monitors.setCurrentIndex(i)
                self.monitors.blockSignals(False)
        elif t == "control_state":
            self.control = bool(d.get("you"))
            self.held = bool(d.get("held"))
            self.chk_control.blockSignals(True)
            self.chk_control.setChecked(self.control)
            self.chk_control.setEnabled(self.control or not self.held)
            self.chk_control.blockSignals(False)
        elif t == "auth_fail":
            host, port = self.current
            self.cfg.get("tokens", {}).pop(f"{host}:{port}", None)
            save_json("viewer.json", self.cfg)
            self.stop_worker()
            self.stack.setCurrentIndex(0)
            self.btn_connect.setEnabled(True)
            self.set_msg(d.get("reason") or "验证码错误", error=True)
        elif t == "kicked":
            self.stop_worker()
            self.stack.setCurrentIndex(0)
            self.btn_connect.setEnabled(True)
            self.set_msg(f"设备已被「{d.get('by','其他设备')}」接管")
            self.start_scan()
        elif t == "clip":
            QApplication.clipboard().setText(d.get("text", ""))
            self.status.setText(f"已读取远程剪贴板({len(d.get('text',''))} 字符)并复制到本机")
        elif t == "clip_ok":
            self.status.setText("已写入远程剪贴板,可在远程电脑直接 Ctrl+V")

    def on_state(self, s, detail):
        host, port = getattr(self, "current", ("?", 0))
        if s == "connecting":
            self.video.set_reconnecting(getattr(self, "_had_session", False))
            self.status.setText(f"正在连接 {host}:{port} …")
        elif s == "authed":
            self._had_session = True
            self.video.set_reconnecting(False)
            self.btn_connect.setEnabled(True)
            self.status.setText("已连接")
        elif s == "retrying":
            self.video.set_reconnecting(True)
            n = detail or "1"
            self.status.setText(
                f"连接 {host}:{port} 断开,第 {n} 次自动重连… "
                f"(画面已暂停;若反复出现请检查 Wi-Fi 信号/带宽)")
        elif s == "ended":
            if detail == "auth_fail" or detail == "kicked":
                pass  # 具体消息由 event 处理
        elif s == "failed":
            self.stop_worker()
            self.stack.setCurrentIndex(0)
            self.btn_connect.setEnabled(True)
            self.set_msg(
                f"无法连接 {host}:{port} — 请确认:①被控端已运行 ②IP/端口正确 "
                f"③被控端防火墙已放行(被控端窗口有「立即修复」按钮)", error=True)
            self.start_scan()

    # ------------------------------------------------------------ 输入转发
    def send_json(self, obj):
        if self.worker:
            self.worker.send_json(obj)

    def send_cfg(self, obj):
        if self.worker:
            self.worker.send_json({"t": "cfg", **obj})

    def on_control_toggle(self, state):
        self.control_enabled = self.chk_control.isChecked()
        if self.control_enabled and not self.control:
            self.send_json({"t": "take"})

    def on_video_mouse(self, ev, kind):
        if not (self.control and self.control_enabled):
            return
        pos = self.video._norm(ev.position())
        if pos is None:
            return
        nx, ny = pos
        if kind == "move":
            now = time.monotonic()
            self.video.set_local_cursor(nx, ny)  # 本地即时反馈
            if now - self._last_move < 0.03:
                return
            self._last_move = now
            self.send_json({"t": "in", "k": "move", "x": nx, "y": ny})
        else:
            btn = {Qt.MouseButton.LeftButton: "left", Qt.MouseButton.RightButton: "right",
                   Qt.MouseButton.MiddleButton: "middle"}.get(ev.button())
            if btn:
                self.send_json({"t": "in", "k": kind, "btn": btn, "x": nx, "y": ny})

    def on_video_wheel(self, ev):
        if not (self.control and self.control_enabled):
            return
        delta = ev.angleDelta()
        self.send_json({"t": "in", "k": "wheel",
                        "dx": -delta.x() / 120.0, "dy": -delta.y() / 120.0})

    def on_video_key(self, ev, down):
        if not (self.control and self.control_enabled):
            return
        fw = QApplication.focusWidget()
        if fw is not None and fw is not self.video:
            cls = fw.metaObject().className()
            if cls in ("QLineEdit", "QTextEdit", "QPlainTextEdit", "QComboBox"):
                return
        self.send_json({
            "t": "key", "code": "", "key": ev.text(),
            "kc": int(ev.nativeVirtualKey()) & 0xFF, "down": down,
        })
        ev.accept()

    # ------------------------------------------------------------- 功能项
    def toggle_textbar(self):
        if self.textbar.isVisible():
            self.textbar.hide()
            self.video.setFocus()
        else:
            self.textbar.show()
            self.text_edit.setFocus()

    def send_text(self):
        text = self.text_edit.text()
        if text:
            self.send_json({"t": "text", "text": text, "bs": 0})
            self.text_edit.clear()
            self.text_edit.setFocus()

    def paste_clipboard(self):
        text = QApplication.clipboard().text()
        if not text:
            self.status.setText("本机剪贴板为空")
            return
        self.send_json({"t": "clip_set", "text": text})

    def toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()
        enable_dark_titlebar(self)

    def _auto_finish(self):
        log = make_logger(LOGFILE)
        log(f"[auto] connected frames={self._auto_frames} "
            f"status={self.status.text()} control={self.control}")
        self.close()
        QApplication.quit()

    def closeEvent(self, ev):
        self.stop_worker()
        ev.accept()


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    import argparse
    p = argparse.ArgumentParser(prog="moli-remote-viewer", description="魔力局域网远程助手 主控端桌面应用")
    p.add_argument("--auto", default=None, metavar="IP:PORT", help="(测试)自动连接")
    p.add_argument("--code", default=None, help="(测试)验证码")
    p.add_argument("--auto-quit", type=int, default=6, help="(测试)连接后 N 秒退出")
    args = p.parse_args(argv)

    app = QApplication(["moli_remote-viewer"])
    app.setApplicationName("魔力局域网远程助手 主控端")
    apply_dark_theme(app)
    shm = ensure_single_instance("MoliViewer-Singleton-v1")
    if shm is None:
        QMessageBox.warning(None, "魔力局域网远程助手 主控端", "主控端已在运行。")
        return 0
    win = MainWindow()
    if args.auto:
        host, _, port = args.auto.rpartition(":")
        win.auto = {"ip": host, "port": int(port or 8765), "code": args.code, "quit": args.auto_quit}
        win.addr_edit.setText(args.auto)
        if args.code:
            win.code_edit.setText(args.code)
        QTimer.singleShot(500, lambda: win.begin_connect(host, int(port or 8765)))
    win.show()
    enable_dark_titlebar(win)
    return app.exec()
