"""生成 README 用应用截图(离屏渲染,不含任何真实桌面内容)。

合成一张"演示桌面"作为远程画面素材,再离屏渲染被控端窗口与主控端两个页面。
输出:docs/screenshots/{host,viewer-connect,viewer-session}.png
"""

import io
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image, ImageDraw, ImageFont  # noqa: E402
from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

OUT = ROOT / "docs" / "screenshots"


def font(size, bold=False):
    name = "msyhbd.ttc" if bold else "msyh.ttc"
    try:
        return ImageFont.truetype(f"C:\\Windows\\Fonts\\{name}", size)
    except Exception:
        try:
            return ImageFont.truetype("C:\\Windows\\Fonts\\msyh.ttc", size)
        except Exception:
            return ImageFont.load_default()


def rounded(d, box, r, fill, outline=None, width=1):
    d.rounded_rectangle(box, radius=r, fill=fill, outline=outline, width=width)


def render_demo_desktop(w=1920, h=1080):
    """合成一张 Windows 风格演示桌面(纯绘制,无真实内容)。"""
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    # ---- 壁纸:深蓝渐变 + 柔和光斑 ----
    top, bottom = (8, 16, 46), (28, 62, 128)
    for y in range(h):
        t = y / h
        d.line([(0, y), (w, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(top, bottom)))
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    for cx, cy, r, alpha in ((w * 0.28, h * 0.42, 420, 70), (w * 0.75, h * 0.65, 520, 46),
                             (w * 0.62, h * 0.2, 260, 38)):
        od.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(120, 180, 255, alpha))
    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    d = ImageDraw.Draw(img)

    # ---- 桌面图标(两列) ----
    f_label = font(17)
    icons = [("此电脑", "#4f8cff"), ("回收站", "#9aa7b8"), ("文档", "#f0b429"),
             ("图片", "#e0567a"), ("音乐", "#8a63d2"), ("下载", "#2fbf8f")]
    for i, (label, color) in enumerate(icons):
        col, row = divmod(i, 3)
        x = 60 + col * 130
        y = 60 + row * 130
        rounded(d, (x, y, x + 68, y + 68), 14, color)
        d.ellipse((x + 22, y + 20, x + 46, y + 44), fill=(255, 255, 255, 230))
        tb = d.textbbox((0, 0), label, font=f_label)
        d.text((x + 34 - (tb[2] - tb[0]) / 2, y + 76), label, fill=(235, 240, 248), font=f_label)

    # ---- 打开的窗口:记事本风格 ----
    wx0, wy0, wx1, wy1 = int(w * 0.26), int(h * 0.12), int(w * 0.74), int(h * 0.82)
    d.rectangle((wx0 + 10, wy0 + 12, wx1 + 10, wy1 + 12), fill=(0, 0, 0))  # 简单投影
    rounded(d, (wx0, wy0, wx1, wy1), 10, (245, 246, 250), outline=(150, 158, 170), width=2)
    rounded(d, (wx0, wy0, wx1, wy0 + 44), 10, (235, 238, 245))
    d.rectangle((wx0, wy0 + 22, wx1, wy0 + 44), fill=(235, 238, 245))
    f_title = font(19, bold=True)
    d.text((wx0 + 18, wy0 + 10), "记事本 — 欢迎使用魔力局域网远程助手", fill=(40, 48, 62), font=f_title)
    for i, (cx, cy, color) in enumerate(((wx1 - 92, wy0 + 22, (225, 228, 235)),
                                         (wx1 - 56, wy0 + 22, (225, 228, 235)),
                                         (wx1 - 24, wy0 + 22, (232, 90, 90)))):
        d.ellipse((cx - 10, cy - 10, cx + 10, cy + 10), fill=color)
    f_body = font(21)
    lines = [
        "这是一台演示用的远程电脑画面。",
        "",
        "你可以在这里完成:",
        "  · 文档编辑、代码编写、系统设置",
        "  · 运行命令行工具、管理文件",
        "  · 播放演示视频、浏览网页",
        "",
        "左键拖动选择文字,右键呼出菜单,",
        "滚轮翻页 — 全部操作即时生效。",
        "试试下方的画质切换与文字输入功能!",
    ]
    y = wy0 + 74
    for line in lines:
        d.text((wx0 + 28, y), line, fill=(55, 63, 78), font=f_body)
        y += 40
    # 状态栏
    d.rectangle((wx0 + 2, wy1 - 34, wx1 - 2, wy1 - 2), fill=(228, 232, 240))
    d.text((wx0 + 20, wy1 - 30), "第 1 行,第 1 列", fill=(110, 118, 132), font=font(16))
    d.text((wx1 - 190, wy1 - 30), "UTF-8    100%", fill=(110, 118, 132), font=font(16))

    # ---- 任务栏 ----
    tb_h = 56
    d.rectangle((0, h - tb_h, w, h), fill=(22, 28, 40))
    d.line([(0, h - tb_h), (w, h - tb_h)], fill=(70, 82, 100))
    # 开始按钮(四色方块)
    sx, sy = w // 2 - 140, h - tb_h // 2
    for dx, dy, c in ((-9, -9, "#58a6ff"), (1, -9, "#3fb950"), (-9, 1, "#f0883e"), (1, 1, "#d2a8ff")):
        d.rectangle((sx + dx, sy + dy, sx + dx + 8, sy + dy + 8), fill=c)
    for i, c in enumerate(("#4f8cff", "#2fbf8f", "#e0567a", "#f0b429", "#8a63d2")):
        cx = w // 2 - 70 + i * 62
        rounded(d, (cx, sy - 16, cx + 34, sy + 18), 8, c)
    d.text((w - 250, sy - 20), "10:24", fill=(230, 235, 242), font=font(19, bold=True))
    d.text((w - 250, sy + 4), "2026/9/19", fill=(170, 178, 192), font=font(15))
    # 输入法/音量/网络图标
    d.text((w - 150, sy - 12), "中", fill=(200, 208, 222), font=font(20))
    d.polygon((w - 110, sy + 10, w - 96, sy - 12, w - 82, sy + 10), fill=(200, 208, 222))
    d.rectangle((w - 68, sy - 4, w - 60, sy + 10), fill=(200, 208, 222))
    d.polygon((w - 60, sy + 10, w - 52, sy - 10, w - 44, sy + 10), fill=(200, 208, 222))
    return img


def pil_to_qimage(img: Image.Image) -> QImage:
    data = img.tobytes("raw", "RGB")
    qimg = QImage(data, img.width, img.height, img.width * 3, QImage.Format_RGB888)
    return qimg.copy()  # 复制以确保数据归属 Qt


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    demo = render_demo_desktop(1920, 1080)

    # ---------- 1) 被控端窗口 ----------
    import moli_remote.qtui.host_app as H
    H.lan_ips = lambda: ["192.168.1.23"]  # 演示地址,不暴露真实网络
    win = H.HostWindow({"port": 8765, "code": "384920", "name": "演示客厅电脑"})
    win.log_view.clear()
    for line in ("[10:23:41] 服务已启动,等待主控端连接…(JPEG 编码: libjpeg-turbo)",
                 "[10:23:58] TCP_NODELAY=1, SO_SNDBUF=256KB(小包直发+限制发送缓冲)",
                 "[10:24:02] 魔力主控端(192.168.1.35) 已连接并取得控制权"):
        win.log_view.append(line)
    win._set_status("控制中:魔力主控端(192.168.1.35) · 观看 0 人", "ok")
    app.processEvents()
    win.grab().save(str(OUT / "host.png"))
    win._close_to_tray = False
    win._quit()
    print("host.png")

    # ---------- 2) 主控端 · 连接页 ----------
    from moli_remote.qtui.viewer_app import MainWindow
    vw = MainWindow()
    vw._scan_gen += 1  # 使真实扫描结果过期,注入演示设备
    vw.on_scanned([
        {"name": "演示客厅电脑", "os": "Windows 11", "ip": "192.168.1.23", "port": 8765},
        {"name": "演示书房主机", "os": "Windows 11", "ip": "192.168.1.35", "port": 8765},
    ])
    app.processEvents()
    vw.grab().save(str(OUT / "viewer-connect.png"))
    print("viewer-connect.png")

    # ---------- 3) 主控端 · 远程会话页 ----------
    vw.current = ("192.168.1.23", 8765)
    vw._had_session = True
    vw.on_event({"t": "auth_ok", "control": True, "token": "demo",
                 "monitors": [{"i": 1, "w": 1920, "h": 1080}],
                 "screen": {"w": 1920, "h": 1080},
                 "settings": {"preset": "hd", "monitor": 1}})
    vw.stack.setCurrentIndex(1)  # 正常流程由 start_worker 切页,演示时手动切换
    vw.video.set_frame(pil_to_qimage(demo))
    vw.on_status({"fps": 24, "rtt": 8, "w": 1920, "h": 1080,
                  "cursor": (1240, 630, True)})
    vw.video.setFocus()
    app.processEvents()
    vw.grab().save(str(OUT / "viewer-session.png"))
    vw.close()
    print("viewer-session.png")


if __name__ == "__main__":
    main()
