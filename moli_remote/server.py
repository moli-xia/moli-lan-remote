"""魔力局域网远程助手 被控端服务:HTTP 静态页 + WebSocket 视频流/控制通道。

协议要点:
- 二进制帧 = 9 字节头(struct ">HHHHB": 宽、高、光标x、光标y、flags) + JPEG 数据
- JSON 消息用于认证、设置、剪贴板、ping/pong 等
- 验证码认证;新验证码连接会接管控制权,旧连接被踢下线
"""

import asyncio
import hmac
import json
import platform
import secrets
import socket
import struct
import threading
import time
from pathlib import Path

from aiohttp import WSMsgType, web

from . import __version__
from .capture import ScreenCapture
from .discovery import DiscoveryResponder, lan_ips
from .input_control import RemoteInput

STATIC_DIR = Path(__file__).parent / "static"
FRAME_HEADER = struct.Struct(">HHHHB")

PRESETS = {
    "smooth": {"quality": 45, "scale": 0.5, "fps": 15},
    # hd 在 4K 等超大屏上自动用 0.75x 缩放,保证低延迟(超清档才走全分辨率)
    "hd": {"quality": 62, "scale": "auto", "fps": 24},
    "best": {"quality": 82, "scale": 1.0, "fps": 30},
}


class Settings:
    def __init__(self, quality=62, scale="auto", fps=24, monitor=1, preset="hd"):
        self.quality = quality
        self.scale = scale
        self.fps = fps
        self.monitor = monitor
        self.preset = preset

    def to_dict(self):
        return {
            "quality": self.quality, "scale": self.scale,
            "fps": self.fps, "monitor": self.monitor, "preset": self.preset,
        }


class Client:
    def __init__(self, ws, ip):
        self.ws = ws
        self.ip = ip
        self.name = "?"
        self.control = False
        self.token = None
        self.queue = asyncio.Queue(maxsize=1)  # 只保留最新帧,避免慢链路积压
        self.send_lock = asyncio.Lock()
        self.task = None
        self.drops = 0        # 拥塞信号:队列满丢帧数(采集线程每秒读取)
        self.slow_sends = 0   # 拥塞信号:单帧发送耗时超阈值次数
        self._sx = 0.0  # 滚轮小数累积
        self._sy = 0.0


class MoliRemoteServer:
    def __init__(self, host="0.0.0.0", port=8765, code=None, name=None,
                 quality=62, scale="auto", fps=24, monitor=1, discovery=True,
                 log_fn=None, show_banner=True):
        self.host = host
        self.port = port
        self.code = (code or f"{secrets.randbelow(1000000):06d}").strip().zfill(6)
        self.name = name or socket.gethostname()
        self.device_id = secrets.token_hex(4)
        self.discovery_enabled = discovery
        self.log_fn = log_fn or (lambda msg: print(
            f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True))
        self.show_banner = show_banner

        self.settings = Settings(quality, scale, fps, monitor)
        self.capture = ScreenCapture(monitor)
        self.input = RemoteInput()
        self.os_name = f"{platform.system()} {platform.release()}"

        self.clients = set()          # 已认证 Client
        self.controller = None        # 当前握有控制权的 Client
        self.tokens = {}              # token -> {name, control}
        self.fail_bans = {}           # ip -> [失败次数, 解封时间]
        self.loop = None
        self._stop = threading.Event()
        self._stop_async = None
        self._responder = None
        self._last_grab = 0.0
        self._congestion = 0        # 0=空闲 .. 6=严重(网络自适应)
        self._congest_since = 0.0
        self._calm_since = 0.0
        self._net_stat_t = 0.0

    # ------------------------------------------------------------- lifecycle
    def regenerate_code(self):
        """更换验证码(不影响已连接会话的 Token)。"""
        self.code = f"{secrets.randbelow(1000000):06d}"
        self.fail_bans.clear()
        self.log_fn(f"验证码已更换为 {self.code}")
        return self.code

    def request_stop(self):
        """线程安全地请求服务停止(供 GUI 调用)。"""
        self._stop.set()
        if self.loop and self._stop_async:
            self.loop.call_soon_threadsafe(self._stop_async.set)

    # ------------------------------------------------------------------ web
    async def run(self):
        self.loop = asyncio.get_running_loop()
        self._stop_async = asyncio.Event()
        app = web.Application()
        app.router.add_get("/", self._index)
        app.router.add_get("/favicon.ico", self._favicon)
        app.router.add_get("/api/info", self._api_info)
        app.router.add_get("/ws", self._ws_handler)
        app.router.add_static("/static/", str(STATIC_DIR))

        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        site = web.TCPSite(runner, self.host, self.port)
        await site.start()

        threading.Thread(target=self._capture_loop, daemon=True).start()
        if self.discovery_enabled:
            self._responder = DiscoveryResponder(self.name, self.port, self.device_id)
            self._responder.start()
            if self._responder.failed:
                self.log_fn("发现服务启动失败(UDP 47520 被占用),已跳过。")

        if self.show_banner:
            self._print_banner()
        from .capture import _HAS_TJ
        self.log_fn(f"服务已启动,等待主控端连接…(JPEG 编码: {'libjpeg-turbo' if _HAS_TJ else 'PIL 回退'})")
        try:
            await self._stop_async.wait()
        finally:
            if self._responder:
                self._responder.stop()
            await runner.cleanup()
            self.log_fn("服务已停止。")

    async def _index(self, request):
        return web.FileResponse(STATIC_DIR / "index.html")

    async def _favicon(self, request):
        return web.Response(status=204)

    async def _api_info(self, request):
        return web.json_response({
            "app": "moli_remote", "name": self.name, "os": self.os_name,
            "version": __version__, "port": self.port,
            "id": self.device_id, "input": self.input.ok,
        })

    # -------------------------------------------------------------- capture
    def _effective_scale(self):
        """解析 scale:"auto" 时按显示器宽度自适应(4K 用 0.75x 保延迟)。

        0.75x 具体是否启用由 capture 在本机实测决定(软件渲染环境自动回退 1.0)。
        """
        s = self.settings
        if s.scale != "auto":
            return s.scale
        mons = self.capture.monitors_full()
        idx = min(max(s.monitor, 0), len(mons) - 1)
        if mons[idx]["width"] < 3000:
            return 1.0
        return self.capture.auto_scale_for(0.75)

    def _update_congestion(self):
        """每秒聚合各客户端拥塞信号,维护拥塞等级(0=空闲..6=严重)。

        SO_SNDBUF 受限后,带宽不足会让 send_bytes 真实阻塞(slow_sends)
        并触发队列丢帧(drops),信号可靠;增速封顶 +2/秒、恢复 3 秒/级,
        避免码率在目标附近剧烈振荡。
        """
        now = time.perf_counter()
        drops = 0
        slow = 0
        for c in list(self.clients):
            drops += c.drops
            slow += c.slow_sends
            c.drops = 0
            c.slow_sends = 0
        if drops or slow:
            if self._congestion == 0:
                self._congest_since = now
            self._congestion = min(6, self._congestion + 1 + min(drops, 1))
            self._calm_since = now
        elif self._congestion:
            if not self._calm_since:
                self._calm_since = now
            if now - self._calm_since > 3.0:  # 空闲 3 秒才降一级,避免振荡
                self._congestion = max(0, self._congestion - 1)
                self._calm_since = now
        return self._congestion

    def _adapt_params(self):
        """按拥塞等级返回 (quality, scale):网络差时自动降码率保流畅。

        等级 ≥2:分辨率降到 0.75x;≥4:降到 0.5x;质量随等级线性下调。
        用户画质设置是"目标上限",网络恢复后自动升回。
        """
        base_scale = self._effective_scale()
        cong = self._congestion
        if cong >= 4:
            scale = max(0.45, base_scale * 0.5)
        elif cong >= 2:
            scale = max(0.55, base_scale * 0.75)
        else:
            scale = base_scale
        factor = max(0.4, 1.0 - 0.11 * cong)
        quality = int(max(32, round(self.settings.quality * factor)))
        return quality, scale

    def _capture_loop(self):
        while not self._stop.is_set():
            if not self.clients:
                self._stop.wait(0.05)
                continue
            fps = max(5, min(30, self.settings.fps))
            interval = 1.0 / fps
            now = time.perf_counter()
            if now - self._net_stat_t > 1.0:
                self._net_stat_t = now
                prev = self._congestion
                cong = self._update_congestion()
                if cong != prev and (cong in (0, 2, 4, 6) or prev in (0, 2, 4)):
                    q, s = self._adapt_params()
                    self.log_fn(f"网络自适应:拥塞等级 {prev}→{cong},"
                                f"当前 质量{q}/缩放{s:.2f}")
            if now - self._last_grab < interval * 0.9:
                self._stop.wait(0.004)
                continue
            self._last_grab = now
            try:
                quality, scale = self._adapt_params()
                frame = self.capture.grab(quality, scale)
            except Exception as e:
                self._log(f"采集异常: {e}")
                self._stop.wait(0.5)
                continue
            if frame is None:
                continue
            pkt = FRAME_HEADER.pack(
                frame.width, frame.height, frame.cur_x, frame.cur_y,
                1 if frame.cursor_visible else 0,
            ) + frame.jpeg
            for client in list(self.clients):
                self.loop.call_soon_threadsafe(self._offer, client, pkt)

    @staticmethod
    def _offer(client, pkt):
        if client.ws.closed:
            return
        try:
            client.queue.put_nowait(pkt)
        except asyncio.QueueFull:
            client.drops += 1  # 拥塞信号:发送跟不上采集
            try:
                client.queue.get_nowait()
                client.queue.put_nowait(pkt)
            except Exception:
                pass

    # ------------------------------------------------------------ websocket
    async def _ws_handler(self, request):
        # 关闭 Nagle + 限制发送缓冲:
        # - Nagle 关闭后键鼠/控制小包立即发出;
        # - SO_SNDBUF 限制可防止 Windows 内核发送缓冲自动扩张到十几 MB,
        #   慢速 Wi-Fi 上视频数据无限堆积会把心跳回应挤到 25 秒超时,
        #   造成"断开-重连"死循环(用户侧表现为画面卡死、状态栏反复重连)。
        try:
            tr = request.transport
            if tr is not None:
                sock = tr.get_extra_info("socket")
                if sock is not None:
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 256 * 1024)
                    if not getattr(self, "_nodelay_logged", False):
                        self._nodelay_logged = True
                        on = sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY)
                        snd = sock.getsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF) // 1024
                        self.log_fn(f"TCP_NODELAY={on}, SO_SNDBUF={snd}KB(小包直发+限制发送缓冲)")
        except Exception:
            pass
        ws = web.WebSocketResponse(heartbeat=25, max_msg_size=1 << 20)
        await ws.prepare(request)
        client = Client(ws, request.remote)
        authed = False
        try:
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                try:
                    data = json.loads(msg.data)
                except Exception:
                    continue
                t = data.get("t")
                if not authed:
                    if t != "auth":
                        continue
                    ok, why, expired = self._authenticate(client, data)
                    if ok:
                        authed = True
                        await self._on_authed(client, data)
                    else:
                        await self._send_json(client, {"t": "auth_fail", "reason": why, "expired": expired})
                        if not expired and why.startswith("失败次数过多"):
                            await ws.close()
                    continue
                if t == "ping":
                    await self._send_json(client, {"t": "pong", "ts": data.get("ts", 0)})
                elif t == "cfg":
                    await self._apply_cfg(client, data)
                elif t == "take":
                    await self._take_control(client)
                elif t == "clip_get":
                    await self._send_json(client, {"t": "clip", "text": self.input.clipboard_get()})
                elif client.control and t == "in":
                    await self._dispatch_input(client, data)
                elif client.control and t == "key":
                    self.input.key_event(data.get("code"), data.get("key"),
                                         data.get("kc"), bool(data.get("down")))
                elif client.control and t == "text":
                    self.input.type_text(str(data.get("text", "")), int(data.get("bs", 0) or 0))
                elif client.control and t == "clip_set":
                    if self.input.clipboard_set(str(data.get("text", ""))):
                        await self._send_json(client, {"t": "clip_ok"})
        finally:
            self._remove(client)
        return ws

    def _authenticate(self, client, data):
        now = time.time()
        fails, until = self.fail_bans.get(client.ip, [0, 0])
        if now < until:
            return False, "失败次数过多,请 1 分钟后再试", False
        token = data.get("token")
        if token and token in self.tokens:
            info = self.tokens[token]
            client.token = token
            client.name = info["name"]
            client.control = info["control"] and (self.controller is None or self.controller is client)
            if client.control:
                self.controller = client
            return True, "", False
        code = str(data.get("code") or "")
        if hmac.compare_digest(code, self.code):
            client.name = str(data.get("name") or "未知设备")[:32]
            client.token = secrets.token_urlsafe(24)
            client.control = True
            if len(self.tokens) > 64:
                self.tokens.pop(next(iter(self.tokens)))
            self.tokens[client.token] = {"name": client.name, "control": True}
            self.fail_bans.pop(client.ip, None)
            return True, "", False
        self.fail_bans[client.ip] = [fails + 1, now + 60] if fails + 1 >= 5 else [fails + 1, 0]
        left = 5 - min(fails + 1, 5)
        return False, f"验证码错误(剩余 {left} 次机会)" if left else "失败次数过多,请 1 分钟后再试", False

    async def _on_authed(self, client, data):
        # 新验证码连接接管控制权;被接管方收到 kicked 后断开
        prev = self.controller
        if client.control and prev is not None and prev is not client:
            self._log(f"{client.name}({client.ip}) 接管了控制权,踢掉 {prev.name}({prev.ip})")
            await self._send_json(prev, {"t": "kicked", "by": client.name})
            try:
                await prev.ws.close()
            except Exception:
                pass
            self._remove(prev)

        self.clients.add(client)
        if client.control:
            self.controller = client
        client.task = asyncio.create_task(self._sender(client))

        mons = self.capture.monitors()
        mon = mons[min(self.settings.monitor, len(mons) - 1)] if mons else {"w": 0, "h": 0}
        await self._send_json(client, {
            "t": "auth_ok",
            "token": client.token,
            "control": client.control,
            "device": {"name": self.name, "os": self.os_name, "version": __version__},
            "monitors": mons,
            "screen": {"w": mon["w"], "h": mon["h"]},
            "settings": self.settings.to_dict(),
        })
        await self._broadcast_control_state()
        self._log(f"{client.name}({client.ip}) 已连接"
                  f"{'并取得控制权' if client.control else '(观看模式)'}")

    async def _sender(self, client):
        try:
            while not client.ws.closed:
                pkt = await client.queue.get()
                t0 = time.perf_counter()
                async with client.send_lock:
                    await client.ws.send_bytes(pkt)
                cost = (time.perf_counter() - t0) * 1000
                if cost > 60:  # 单帧发送超过 60ms 视为带宽不足
                    client.slow_sends += 1
        except Exception:
            pass

    async def _send_json(self, client, obj):
        try:
            async with client.send_lock:
                await client.ws.send_str(json.dumps(obj, ensure_ascii=False))
        except Exception:
            pass

    def _remove(self, client):
        self.clients.discard(client)
        if self.controller is client:
            self.controller = None
            if self.loop and self.clients:
                self.loop.call_soon_threadsafe(
                    asyncio.create_task, self._broadcast_control_state())
        if client.task:
            client.task.cancel()

    async def _apply_cfg(self, client, data):
        s = self.settings
        preset = data.get("preset")
        if preset in PRESETS:
            s.preset = preset
            for k, v in PRESETS[preset].items():
                setattr(s, k, v)
        if "quality" in data:
            s.quality = min(95, max(30, int(data["quality"])))
            s.preset = "custom"
        if "scale" in data:
            v = data["scale"]
            s.scale = "auto" if v == "auto" else min(1.0, max(0.3, float(v)))
            s.preset = "custom"
        if "fps" in data:
            s.fps = min(30, max(5, int(data["fps"])))
        if "monitor" in data:
            mons = self.capture.monitors()
            s.monitor = min(len(mons) - 1, max(0, int(data["monitor"])))
            self.capture.set_monitor(s.monitor)
        if s.preset != "custom" or "quality" in data or "scale" in data:
            self.capture.invalidate()
        for c in list(self.clients):
            await self._send_json(c, {"t": "settings", "settings": s.to_dict()})

    async def _take_control(self, client):
        if self.controller is None or self.controller is client or self.controller.ws.closed:
            self.controller = client
            client.control = True
            if client.token in self.tokens:
                self.tokens[client.token]["control"] = True
            self._log(f"{client.name}({client.ip}) 取得控制权")
        await self._broadcast_control_state()

    async def _broadcast_control_state(self):
        for c in list(self.clients):
            await self._send_json(c, {
                "t": "control_state",
                "held": self.controller is not None,
                "you": self.controller is c,
                "by": self.controller.name if self.controller else None,
            })

    async def _dispatch_input(self, client, data):
        k = data.get("k")
        try:
            if k == "move":
                self._to_abs(data["x"], data["y"])
            elif k == "down" or k == "up":
                self._to_abs(data.get("x"), data.get("y"))
                self.input.mouse_button(data.get("btn", "left"), k == "down")
            elif k == "wheel":
                self._to_abs(data.get("x"), data.get("y"))
                client._sx += float(data.get("dx", 0))
                client._sy += float(data.get("dy", 0))
                dx = int(client._sx)  # 截断取整,余量留到下一次
                dy = int(client._sy)
                client._sx -= dx
                client._sy -= dy
                if dx or dy:
                    self.input.mouse_scroll(dx, dy)
        except Exception:
            pass

    def _to_abs(self, nx, ny):
        """归一化坐标 → 当前显示器物理像素坐标。"""
        if nx is None or ny is None:
            return
        mons = self.capture.monitors_full()
        idx = min(self.settings.monitor, len(mons) - 1)
        mon = mons[idx]
        x = mon["left"] + float(nx) * mon["width"]
        y = mon["top"] + float(ny) * mon["height"]
        self.input.mouse_move(
            min(mon["left"] + mon["width"] - 1, max(mon["left"], x)),
            min(mon["top"] + mon["height"] - 1, max(mon["top"], y)),
        )

    # ---------------------------------------------------------------- misc
    def _log(self, msg):
        self.log_fn(msg)  # 时间戳由 log_fn 统一添加

    def _print_banner(self):
        ips = sorted(lan_ips()) or ["127.0.0.1"]
        C, D, B = "\033[96m", "\033[2m", "\033[0m"
        print()
        print(f"{C}  ━━━ 魔力局域网远程助手 v{__version__} ━━━{B}")
        print(f"  设备名称 : {self.name}")
        print(f"  操作系统 : {self.os_name}")
        for ip in ips:
            print(f"  访问地址 : http://{ip}:{self.port}{D}  (主控端浏览器/手机打开){B}")
        print(f"{C}  验证码   : {self.code}{B}   ← 在主控端输入此验证码")
        discovery_ok = self.discovery_enabled and self._responder is not None \
            and not self._responder.failed
        print(f"  发现服务 : {'UDP 47520 已启动' if discovery_ok else '未启用'}")
        print(f"  输入控制 : {'可用' if self.input.ok else '不可用(仅观看)'}")
        print(f"{D}  Ctrl+C 退出。首次运行请允许防火墙放行(TCP {self.port} / UDP 47520)。{B}")
        print(flush=True)


async def run_server(**kwargs):
    server = MoliRemoteServer(**kwargs)
    await server.run()
