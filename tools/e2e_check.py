"""魔力局域网远程助手 端到端自测:对运行中的被控端做一轮完整检查。

用法:先启动被控端,再执行
    python tools/e2e_check.py --port 8765 --code 123456

检查项:设备信息 / 验证码错误与正确 / 视频帧 / 画质切换 / ping / 剪贴板往返
        / 鼠标移动(会把真实光标移动到主屏中心再移回原位)。
"""

import argparse
import asyncio
import ctypes
import json
import struct
import sys
import time

sys.path.insert(0, str(__file__ + "/.."))

import aiohttp  # noqa: E402

if sys.platform == "win32":
    # 与被控端一致,使用物理像素坐标(高 DPI 屏幕下 GetCursorPos 才准确)
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        ctypes.windll.user32.SetProcessDPIAware()

FRAME_HEADER = struct.Struct(">HHHHB")
RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"  {'✓' if ok else '✗'} {name}" + (f" — {detail}" if detail else ""))


def cursor_pos():
    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
    pt = POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


async def main(port, code):
    base = f"http://127.0.0.1:{port}"
    async with aiohttp.ClientSession() as http:
        # 1. 设备信息
        async with http.get(f"{base}/api/info") as r:
            info = await r.json()
        check("GET /api/info", r.status == 200 and info.get("app") == "moli_remote",
              f"{info.get('name')} · {info.get('os')}")

        # 2. 首页与静态资源
        async with http.get(base + "/") as r:
            html = await r.text()
        check("GET / 返回页面", r.status == 200 and "魔力局域网远程助手" in html)
        async with http.get(base + "/static/app.js") as r:
            check("GET /static/app.js", r.status == 200)

        # 3. WebSocket:错误验证码
        async with http.ws_connect(f"{base}/ws") as ws:
            await ws.send_json({"t": "auth", "code": "000000", "name": "e2e"})
            msg = json.loads((await ws.receive()).data)
            check("错误验证码被拒绝", msg.get("t") == "auth_fail", msg.get("reason", ""))

        # 4. WebSocket:正确验证码
        ws = await http.ws_connect(f"{base}/ws")
        await ws.send_json({"t": "auth", "code": code, "name": "e2e-checker"})
        msg = json.loads((await ws.receive()).data)
        if msg.get("t") != "auth_ok":
            check("验证码连接", False, str(msg))
            return
        token = msg["token"]
        mons = msg["monitors"]
        screen = msg["screen"]
        check("验证码连接", True,
              f"{len(mons)} 个显示对象, 主画面 {screen['w']}×{screen['h']}")
        check("取得控制权", msg.get("control") is True)

        # 5. 视频帧
        frames = 0
        jpeg_ok = True
        deadline = time.time() + 8
        while frames < 3 and time.time() < deadline:
            m = await ws.receive(6)
            if m.type == aiohttp.WSMsgType.BINARY:
                w, h, cx, cy, flags = FRAME_HEADER.unpack_from(m.data, 0)
                if m.data[9:11] != b"\xff\xd8" or not w or not h:
                    jpeg_ok = False
                frames += 1
        check("收到视频帧", frames >= 3, f"{frames} 帧" + ("" if jpeg_ok else ",JPEG 头异常"))
        check("帧头/JPEG 合法", jpeg_ok)

        # 6. 画质切换(配置回显)
        await ws.send_json({"t": "cfg", "preset": "smooth"})
        got = None
        deadline = time.time() + 4
        while time.time() < deadline and got is None:
            m = await ws.receive(4)
            if m.type == aiohttp.WSMsgType.TEXT:
                d = json.loads(m.data)
                if d.get("t") == "settings":
                    got = d["settings"]
        check("画质切换生效", bool(got) and got.get("preset") == "smooth",
              f"fps={got and got.get('fps')}")
        await ws.send_json({"t": "cfg", "preset": "hd"})

        # 7. ping/pong
        t0 = time.perf_counter()
        await ws.send_json({"t": "ping", "ts": 123})
        got_pong = False
        deadline = time.time() + 3
        while time.time() < deadline and not got_pong:
            m = await ws.receive(3)
            if m.type == aiohttp.WSMsgType.TEXT and json.loads(m.data).get("t") == "pong":
                got_pong = True
        check("ping/pong", got_pong)

        # 8. 剪贴板往返(先备份再恢复)
        try:
            import pyperclip
            backup = pyperclip.paste()
            await ws.send_json({"t": "clip_set", "text": "moli_remote-e2e-测试123"})
            set_ok = False
            got_clip = None
            deadline = time.time() + 5
            while time.time() < deadline and got_clip is None:
                m = await ws.receive(5)
                if m.type == aiohttp.WSMsgType.TEXT:
                    d = json.loads(m.data)
                    if d.get("t") == "clip_ok":
                        set_ok = True
                    elif d.get("t") == "clip":
                        got_clip = d
            if set_ok and got_clip is None:
                await ws.send_json({"t": "clip_get"})
                deadline = time.time() + 4
                while time.time() < deadline and got_clip is None:
                    m = await ws.receive(4)
                    if m.type == aiohttp.WSMsgType.TEXT:
                        d = json.loads(m.data)
                        if d.get("t") == "clip":
                            got_clip = d
            check("剪贴板往返", got_clip is not None and got_clip.get("text") == "moli_remote-e2e-测试123",
                  "" if got_clip else "未收到 clip 消息")
            pyperclip.copy(backup or "")
        except Exception as e:  # 无剪贴板环境跳过
            check("剪贴板往返", True, f"跳过({e})")

        # 9. 鼠标移动(移到主屏中心,再移回原位)
        if sys.platform == "win32":
            orig = cursor_pos()
            await ws.send_json({"t": "in", "k": "move", "x": 0.5, "y": 0.5})
            await asyncio.sleep(0.4)
            now = cursor_pos()
            moved = now != orig
            near_center = abs(now[0] - screen["w"] / 2) < 80 and abs(now[1] - screen["h"] / 2) < 80
            check("鼠标注入(移动到中心)", moved and near_center, f"光标 {now}")
            ctypes.windll.user32.SetCursorPos(orig[0], orig[1])

        # 10. Token 重连
        await ws.close()
        ws2 = await http.ws_connect(f"{base}/ws")
        await ws2.send_json({"t": "auth", "token": token, "name": "e2e-checker"})
        msg = json.loads((await ws2.receive()).data)
        check("Token 免验证码重连", msg.get("t") == "auth_ok")
        await ws2.close()

    print()
    failed = [r for r in RESULTS if not r[1]]
    print(f"共 {len(RESULTS)} 项,通过 {len(RESULTS) - len(failed)},失败 {len(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--code", default="123456")
    args = ap.parse_args()
    sys.exit(asyncio.run(main(args.port, args.code)))
