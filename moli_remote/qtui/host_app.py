"""魔力局域网远程助手 被控端桌面应用:系统托盘常驻 + 状态窗口。

后台线程运行 aiohttp 服务(与控制台版完全同一核心),
窗口/托盘负责展示验证码、连接状态,支持重新生成验证码、复制地址。
"""

import asyncio
import socket
import sys
import threading

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QTextOption
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QMainWindow, QMenu, QMessageBox,
    QPushButton, QSystemTrayIcon, QTextEdit, QVBoxLayout, QWidget,
)

from ..discovery import lan_ips
from ..server import MoliRemoteServer
from . import firewall
from .common import (
    app_icon, apply_dark_theme, enable_dark_titlebar, ensure_single_instance,
    load_json, make_logger, save_json, settings_dir,
)

LOGFILE = settings_dir() / "host.log"

STYLE = """
QMainWindow, QWidget { background:#0e1526; color:#e8edf6; font-size:14px; }
QLabel#title { font-size:19px; font-weight:700; }
QLabel#code { font-size:44px; font-weight:800; letter-spacing:10px; color:#5ea0ff; }
QLabel#dim { color:#93a1b8; font-size:12px; }
QLabel#status { padding:8px 12px; border-radius:8px; background:#16213a; font-size:13px; }
QLabel#status[state="ok"] { color:#8fd3a0; }
QLabel#status[state="busy"] { color:#f0c674; }
QLabel#status[state="err"] { color:#f87171; }
QLabel#err { color:#f87171; font-size:12px; }
QFrame#card { background:#131e36; border:1px solid #22304e; border-radius:12px; }
QPushButton { padding:8px 14px; border-radius:8px; background:#1b2946; border:1px solid #2a3a5f; }
QPushButton:hover { background:#24365c; }
QTextEdit { background:#0a101f; border:1px solid #22304e; border-radius:8px; color:#9fb0c9;
            font-family:Consolas,monospace; font-size:11px; }
QToolTip { background:#131e36; color:#e8edf6; border:1px solid #2a3a5f; padding:4px 8px; }
QScrollBar:vertical { background:transparent; width:10px; margin:0; }
QScrollBar::handle:vertical { background:#2a3a5f; border-radius:5px; min-height:24px; }
QScrollBar::handle:vertical:hover { background:#3b4f7a; }
QScrollBar::add-line, QScrollBar::sub-line { height:0; }
QScrollBar::add-page, QScrollBar::sub-page { background:transparent; }
QScrollBar:horizontal { background:transparent; height:10px; margin:0; }
QScrollBar::handle:horizontal { background:#2a3a5f; border-radius:5px; min-width:24px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width:0; }
"""


class HostWindow(QMainWindow):
    def __init__(self, overrides):
        super().__init__()
        self.overrides = overrides
        self.setWindowTitle("魔力局域网远程助手 · 被控端")
        self.setWindowIcon(app_icon("#3b82f6", "#22d3ee"))
        self.setFixedSize(440, 500)
        self.setStyleSheet(STYLE)
        self.server = None
        self.thread = None
        self._server_error = None
        self._close_to_tray = True

        self.log = make_logger(LOGFILE)
        self._build_ui()
        self._build_tray()
        self._start_server()
        self._fw_state = None  # None=检测中, "ok", "warn", "off"
        threading.Thread(target=self._check_firewall, daemon=True).start()
        self._tick()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(1000)

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(22, 18, 22, 14)
        root.setSpacing(10)

        title = QLabel("魔力局域网远程助手 · 被控端")
        title.setObjectName("title")
        root.addWidget(title)
        sub = QLabel("本机屏幕将通过局域网共享,主控端无需安装软件")
        sub.setObjectName("dim")
        root.addWidget(sub)

        self.status = QLabel("正在启动服务…")
        self.status.setObjectName("status")
        self.status.setAlignment(Qt.AlignCenter)
        root.addWidget(self.status)

        # 防火墙警告行(检测到未放行时显示)
        fw_row = QHBoxLayout()
        self.fw_label = QLabel()
        self.fw_label.setObjectName("err")
        self.fw_label.setWordWrap(True)
        fw_row.addWidget(self.fw_label, 1)
        self.fw_btn = QPushButton("立即修复")
        self.fw_btn.clicked.connect(self._fix_firewall)
        fw_row.addWidget(self.fw_btn)
        self.fw_row_widget = QWidget()
        self.fw_row_widget.setLayout(fw_row)
        self.fw_row_widget.hide()
        root.addWidget(self.fw_row_widget)

        card = QFrame()
        card.setObjectName("card")
        card_l = QVBoxLayout(card)
        card_l.setContentsMargins(14, 10, 14, 12)
        card_l.addWidget(self._dim("连接验证码(在主控端输入)"))
        self.code_label = QLabel("------")
        self.code_label.setObjectName("code")
        self.code_label.setAlignment(Qt.AlignCenter)
        card_l.addWidget(self.code_label)
        self.addr_label = QLabel()
        self.addr_label.setObjectName("dim")
        self.addr_label.setWordWrap(True)
        self.addr_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        card_l.addWidget(self.addr_label)
        root.addWidget(card)

        btns = QHBoxLayout()
        b1 = QPushButton("重新生成验证码")
        b1.setToolTip("立即换一个新的 6 位验证码")
        b1.clicked.connect(self._regen)
        b2 = QPushButton("复制访问地址")
        b2.setToolTip("复制第一个局域网访问地址到剪贴板")
        b2.clicked.connect(self._copy_addr)
        btns.addWidget(b1)
        btns.addWidget(b2)
        root.addLayout(btns)

        root.addWidget(self._dim("运行日志(%s):" % LOGFILE))
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFixedHeight(110)
        self.log_view.setWordWrapMode(QTextOption.NoWrap)
        root.addWidget(self.log_view)

        note = QLabel("⚠ 仅供可信局域网内经授权的远程协助使用,严禁未授权控制他人设备;"
                      "使用前请阅读免责声明(DISCLAIMER.md)。")
        note.setObjectName("dim")
        note.setWordWrap(True)
        root.addWidget(note)

    def _dim(self, text):
        lab = QLabel(text)
        lab.setObjectName("dim")
        return lab

    def _build_tray(self):
        self.tray = QSystemTrayIcon(app_icon("#3b82f6", "#22d3ee"), self)
        menu = QMenu()
        act_show = QAction("显示主窗口", self)
        act_show.triggered.connect(self.show_up)
        menu.addAction(act_show)
        act_regen = QAction("重新生成验证码", self)
        act_regen.triggered.connect(self._regen)
        menu.addAction(act_regen)
        menu.addSeparator()
        act_quit = QAction("退出魔力局域网远程助手", self)
        act_quit.triggered.connect(self._quit)
        menu.addAction(act_quit)
        self.tray.setContextMenu(menu)
        self.tray.setToolTip("魔力局域网远程助手(被控端)")
        self.tray.activated.connect(
            lambda r: self.show_up() if r == QSystemTrayIcon.Trigger else None)
        self.tray.show()

    def show_up(self):
        self._close_to_tray = True
        self.showNormal()
        self.activateWindow()

    # ------------------------------------------------------------- 防火墙
    def _check_firewall(self):
        """后台线程检测;结果由 UI 定时器渲染(跨线程只写变量)。"""
        try:
            enabled, has_rule = firewall.check_status()
        except Exception:
            self._fw_state = None
            return
        if enabled and not has_rule:
            # 先静默尝试修复(管理员启动的程序可直接成功)
            if firewall.add_rule_quiet():
                self.log("已自动添加防火墙放行规则。")
                has_rule = True
        if not enabled:
            self._fw_state = "off"
        elif has_rule:
            self._fw_state = "ok"
        else:
            self._fw_state = "warn"

    def _render_firewall(self):
        st = self._fw_state
        if st == "ok":
            self.fw_row_widget.hide()
        elif st == "off":
            self.fw_row_widget.hide()  # 防火墙整体关闭,不拦截
        elif st == "warn":
            self.fw_label.setText("⚠ Windows 防火墙未放行魔力局域网远程助手,局域网设备将无法连接本机")
            self.fw_row_widget.show()
        else:
            self.fw_row_widget.hide()

    def _fix_firewall(self):
        self.fw_btn.setEnabled(False)
        self.fw_label.setText("正在添加防火墙规则…")
        if firewall.add_rule_quiet():
            self.log("已添加防火墙放行规则。")
            self._fw_state = "ok"
            self._render_firewall()
        elif firewall.add_rule_elevated():
            self.log("已请求管理员权限添加防火墙规则,请在系统弹窗中确认。")
            self.fw_label.setText("请在系统弹窗(UAC)中确认添加规则,完成后自动刷新…")
            QTimer.singleShot(5000, self._refirewall_after_uac)
        else:
            self.fw_label.setText("自动修复失败:请以管理员身份运行本程序,或手动放行程序")
        self.fw_btn.setEnabled(True)

    def _refirewall_after_uac(self):
        self._check_firewall()
        self._render_firewall()
        if self._fw_state == "ok":
            self.log("防火墙规则已生效。")

    # -------------------------------------------------------------- server
    def _start_server(self):
        cfg = load_json("host.json", {
            "port": 8765, "name": None, "code": "", "fps": 24, "quality": 70,
        })
        cfg.update({k: v for k, v in self.overrides.items() if v})
        save_json("host.json", cfg)

        def ui_log(msg):
            self.log(msg)
            self.log_view.append(msg)
            if self.log_view.document().blockCount() > 300:
                self.log_view.clear()

        self.server = MoliRemoteServer(
            port=int(cfg["port"]),
            code=cfg.get("code") or None,
            name=cfg.get("name") or socket.gethostname(),
            quality=int(cfg.get("quality", 62)),
            fps=int(cfg.get("fps", 24)),
            monitor=int(cfg.get("monitor", 1)),
            discovery=True,
            log_fn=ui_log,
            show_banner=False,
        )

        def _run_server():
            try:
                asyncio.run(self.server.run())
            except Exception as e:
                self._server_error = str(e)
                self.log(f"服务异常退出: {e}")

        self.thread = threading.Thread(target=_run_server, daemon=True)
        self.thread.start()

    def _set_status(self, text, state):
        self.status.setText(text)
        self.status.setProperty("state", state)
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)

    def _tick(self):
        if not self.server:
            return
        self._render_firewall()
        if self._server_error:
            self._set_status(f"服务启动失败:{self._server_error}", "err")
            return
        if not self.thread.is_alive():
            self._set_status("服务已停止", "err")
            return
        self.code_label.setText(self.server.code)
        ips = sorted(lan_ips()) or ["127.0.0.1"]
        self.addr_label.setText("访问地址  " + "   ".join(
            f"http://{ip}:{self.server.port}" for ip in ips))
        if self.server.controller is not None:
            c = self.server.controller
            self._set_status(
                f"控制中:{c.name}({c.ip})  ·  观看 {max(0, len(self.server.clients) - 1)} 人",
                "ok")
        else:
            self._set_status("运行中 · 等待主控端连接…", "busy")

    def _regen(self):
        if self.server:
            self.server.regenerate_code()

    def _copy_addr(self):
        ips = sorted(lan_ips()) or ["127.0.0.1"]
        QApplication.clipboard().setText(f"http://{ips[0]}:{self.server.port}")
        self.tray.showMessage("魔力局域网远程助手", "访问地址已复制到剪贴板",
                              QSystemTrayIcon.Information, 1500)

    # -------------------------------------------------------------- 退出
    def closeEvent(self, ev):
        if self._close_to_tray and self.tray.isVisible():
            ev.ignore()
            self.hide()
            self.tray.showMessage(
                "魔力局域网远程助手", "已最小化到托盘,被控服务继续运行。右键托盘图标可退出。",
                QSystemTrayIcon.Information, 2000)
            return
        ev.accept()

    def _quit(self):
        self._close_to_tray = False
        self.tray.hide()
        if self.server:
            self.server.request_stop()
            self.thread.join(timeout=3)
        QApplication.quit()


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    import argparse
    p = argparse.ArgumentParser(prog="moli-remote-host", description="魔力局域网远程助手 被控端桌面应用")
    p.add_argument("--port", type=int, default=None)
    p.add_argument("--name", default=None)
    p.add_argument("--code", default=None, help="固定 6 位验证码")
    p.add_argument("--monitor", type=int, default=None)
    p.add_argument("--fps", type=int, default=None)
    p.add_argument("--quality", type=int, default=None)
    args = p.parse_args(argv)
    overrides = {k: v for k, v in vars(args).items() if v is not None and k != "no_discovery"}

    app = QApplication(["moli_remote-host"])
    app.setApplicationName("魔力局域网远程助手 被控端")
    app.setQuitOnLastWindowClosed(False)
    apply_dark_theme(app)
    shm = ensure_single_instance("MoliHost-Singleton-v1")
    if shm is None:
        QMessageBox.warning(None, "魔力局域网远程助手 被控端",
                            "被控端已在运行。\n请查看系统托盘区域的魔豆图标。")
        return 0
    win = HostWindow(overrides)
    win.show()
    enable_dark_titlebar(win)
    return app.exec()
