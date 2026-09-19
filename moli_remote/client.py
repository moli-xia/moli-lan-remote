"""主控端 WebSocket 客户端 worker(独立线程运行 aiohttp,经 Qt 信号桥接 UI)。

- 视频帧在工作线程解码为 QImage,通过信号(队列连接)送到 UI 线程
- 连接级故障自动用 Token 重连;认证级失败(验证码错/会话过期/被接管)不重试
"""

import asyncio
import json
import struct
import threading
import time

import aiohttp
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QImage

FRAME_HEADER = struct.Struct(">HHHHB")


class ClientSignals(QObject):
    frame = Signal(object)    # QImage
    event = Signal(dict)      # 服务端 JSON 消息(auth_ok/settings/control_state/clip/...)
    status = Signal(dict)     # {"fps","rtt","w","h"}
    state = Signal(str, str)  # connecting/authed/retrying/ended/failed + detail


class ClientWorker(threading.Thread):
    def __init__(self, host, port, code=None, token=None, name="魔力主控端"):
        super().__init__(daemon=True)
        self.sig = ClientSignals()
        self.host = host
        self.port = port
        self.name = name
        self._auth = {"code": code} if code else {"token": token}
        self._token = token
        self._loop = None
        self._ws = None
        self._stop_evt = None
        self._ever_authed = False
        self._frames = 0
        self._rtt = 0
        self._last_w = 0
        self._last_h = 0
        self._last_status = 0.0
        self._ping_ts = 0.0
        self._last_ping = 0.0

    # ------------------------------------------------------- 线程安全接口
    def send_json(self, obj):
        if self._loop and self._ws is not None:
            try:
                self._loop.call_soon_threadsafe(self._send_now, obj)
            except RuntimeError:
                pass  # 事件循环已关闭

    def _send_now(self, obj):
        if self._ws is not None and not self._ws.closed:
            try:
                asyncio.ensure_future(self._ws.send_str(json.dumps(obj, ensure_ascii=False)))
            except Exception:
                pass

    def stop(self):
        loop = self._loop
        if loop is None:
            return

        def _do_stop():
            self._stop_evt.set()
            ws = self._ws
            if ws is not None and not ws.closed:
                asyncio.ensure_future(ws.close())

        try:
            loop.call_soon_threadsafe(_do_stop)
        except RuntimeError:
            pass  # 事件循环已随线程结束关闭;socket 由线程清理

    # ------------------------------------------------------------- 主循环
    def run(self):
        asyncio.run(self._main())

    async def _main(self):
        self._loop = asyncio.get_running_loop()
        self._stop_evt = asyncio.Event()
        tries = 0
        try:
            while not self._stop_evt.is_set():
                self._safe_state("connecting", "")
                try:
                    how = await asyncio.wait_for(self._session(), timeout=15)
                except asyncio.TimeoutError:
                    how = "net_error:连接超时"
                except Exception as e:
                    how = "net_error:" + str(e)
                self._ws = None
                if self._stop_evt.is_set():
                    break
                if how in ("auth_fail", "kicked"):
                    self._safe_state("ended", how)
                    return
                if how == "closed" and self._token:
                    self._auth = {"token": self._token}
                tries += 1
                # 首连快速失败(多为地址错/防火墙);已连过则多轮重连抗抖动
                if tries > (8 if self._ever_authed else 3):
                    self._safe_state("failed", how)
                    return
                self._safe_state("retrying", str(tries))
                try:
                    await asyncio.wait_for(self._stop_evt.wait(),
                                           timeout=min(3.0, 0.6 * tries))
                except asyncio.TimeoutError:
                    pass
            self._safe_state("stopped", "")
        finally:
            self._ws = None
            self._loop = None  # 线程退出后阻止对已关闭循环的调用

    def _safe_state(self, s, detail):
        try:
            self.sig.state.emit(s, detail)
        except RuntimeError:
            pass  # 应用退出后 Qt 对象已销毁

    async def _session(self):
        """一次完整的 WS 会话,返回结束原因。整体上限 15 秒(含认证)。

        aiohttp 默认已启用 TCP_NODELAY,无需显式设置。
        """
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=3, sock_read=None)
        async with aiohttp.ClientSession(timeout=timeout) as http:
            async with http.ws_connect(
                f"ws://{self.host}:{self.port}/ws", max_msg_size=1 << 20, heartbeat=25
            ) as ws:
                self._ws = ws
                self._frames = 0
                await ws.send_str(json.dumps(
                    {"t": "auth", "name": self.name, **self._auth},
                    ensure_ascii=False))
                async for msg in ws:
                    now = time.monotonic()
                    if now - self._last_ping > 2:
                        self._last_ping = now
                        self._ping_ts = time.perf_counter()
                        await ws.send_str(json.dumps({"t": "ping", "ts": 0}))
                    if msg.type == aiohttp.WSMsgType.BINARY:
                        self._on_frame(msg.data)
                    elif msg.type == aiohttp.WSMsgType.TEXT:
                        how = self._on_text(msg.data)
                        if how:
                            return how
                return "closed"

    def _on_text(self, raw):
        try:
            d = json.loads(raw)
        except Exception:
            return None
        t = d.get("t")
        if t == "auth_ok":
            self._token = d.get("token") or self._token
            self._ever_authed = True
            self._emit_event(d)
            self._safe_state("authed", "")
        elif t == "auth_fail":
            self._emit_event(d)
            return "auth_fail"
        elif t == "pong":
            self._rtt = int((time.perf_counter() - self._ping_ts) * 1000)
        elif t == "kicked":
            self._emit_event(d)
            return "kicked"
        else:
            self._emit_event(d)
        return None

    def _emit_event(self, d):
        try:
            self.sig.event.emit(d)
        except RuntimeError:
            pass

    def _on_frame(self, data):
        if len(data) <= 9:
            return
        w, h, cx, cy, flags = FRAME_HEADER.unpack_from(data, 0)
        img = QImage.fromData(bytes(data[9:]))
        if img.isNull():
            return
        self._frames += 1
        now = time.monotonic()
        if now - self._last_status > 1.0:
            self._last_status = now
            self.sig.status.emit({
                "fps": self._frames, "rtt": self._rtt, "w": w, "h": h,
                "cursor": (cx, cy, bool(flags & 1)),
            })
            self._frames = 0
        self.sig.frame.emit(img)
