"""局域网设备发现:UDP 定向广播 + TCP 网段兜底扫描。

可靠性设计(修复局域网发现不了/连不上的问题):
- UDP 探测面向**每个本机网段**的定向广播地址(a.b.c.255)分别发送,
  多网卡环境下(如同时存在 WSL/Hyper-V 虚拟网卡)不再依赖单一默认接口;
- 被控端应答时附带"对本对端的路由源 IP"(UDP connect 选路,不发包),
  主控端优先用它连接,避免拿到虚拟网卡等错误地址;
- TCP 兜底:对本机各网段并发探测 :port/api/info,只要 TCP 可达即可发现,
  即使 UDP 广播被防火墙/网络策略(AP 隔离、组播过滤)拦截也能工作。
"""

import asyncio
import ipaddress
import json
import platform
import socket
import threading
import time

from . import __version__

PROBE = b"MOLI-REMOTE-DISCOVER-v1"
DISCOVERY_PORT = 47520


def default_route_ip():
    """默认路由的源 IP(UDP connect 仅选路,不实际发包)。"""
    for target in (("223.5.5.5", 53), ("8.8.8.8", 53)):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                s.connect(target)
                ip = s.getsockname()[0]
                if not ip.startswith("127."):
                    return ip
            finally:
                s.close()
        except OSError:
            continue
    return None


def route_ip_to(peer):
    """对指定对端的路由源 IP(用于应答时告知主控端正确的连接地址)。"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(peer)
            return s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        return None


def lan_ips():
    """本机 IPv4 列表:默认路由源优先,过滤回环/链路本地,虚拟网段靠后。"""
    primary = default_route_ip()
    ips = {primary} if primary else set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    ips = {ip for ip in ips if not ip.startswith(("127.", "169.254."))}

    def rank(ip):
        if ip == primary:
            return (0, ip)
        if ip.startswith("172."):  # WSL/Hyper-V 虚拟交换机常见网段
            return (2, ip)
        return (1, ip)

    return sorted(ips, key=rank)


def _local_subnets():
    """本机各 IPv4 所在 /24 网段(大多数局域网环境;去重)。"""
    nets, seen = [], set()
    for ip in lan_ips():
        net = ipaddress.ip_network(f"{ip}/24", strict=False)
        if net not in seen:
            seen.add(net)
            nets.append(net)
    return nets


class DiscoveryResponder(threading.Thread):
    """被控端:应答 UDP 探测,回包附带对主控端的正确源 IP。"""

    def __init__(self, name, http_port, device_id):
        super().__init__(daemon=True)
        self.info = {
            "app": "moli_remote", "v": __version__, "id": device_id,
            "name": name, "port": int(http_port),
            "os": f"{platform.system()} {platform.release()}",
        }
        self.stop_event = threading.Event()
        self.failed = False
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.sock.bind(("", DISCOVERY_PORT))
            self.sock.settimeout(0.5)
        except OSError:
            self.failed = True

    def _payload_for(self, addr):
        info = dict(self.info)
        src = route_ip_to(addr)
        if src:
            info["src"] = src
        return json.dumps(info, ensure_ascii=False).encode("utf-8")

    def run(self):
        if self.failed:
            return
        s = self.sock
        while not self.stop_event.is_set():
            try:
                data, addr = s.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break
            if data.strip() == PROBE:
                try:
                    s.sendto(self._payload_for(addr), addr)
                except OSError:
                    pass
        try:
            s.close()
        except Exception:
            pass

    def stop(self):
        self.stop_event.set()


# ---------------------------------------------------------------- 扫描端

def udp_scan(timeout=1.0):
    """多网卡定向广播探测,返回 {key: info}(info 含 ip/via)。"""
    results = {}
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.settimeout(0.2)
    except OSError:
        return results
    targets = {
        ("255.255.255.255", DISCOVERY_PORT),
        ("127.0.0.1", DISCOVERY_PORT),
    }
    for ip in lan_ips():
        targets.add((f"{ip.rsplit('.', 1)[0]}.255", DISCOVERY_PORT))
    for t in targets:
        try:
            s.sendto(PROBE, t)
        except OSError:
            pass
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            data, addr = s.recvfrom(2048)
        except socket.timeout:
            continue
        except OSError:
            break
        try:
            info = json.loads(data.decode("utf-8"))
        except Exception:
            continue
        if info.get("app") != "moli_remote":
            continue
        ip = info.get("src") or addr[0]  # 优先对主控端可达的源地址
        results[f"{ip}:{info.get('port')}"] = {**info, "ip": ip, "via": "udp"}
    try:
        s.close()
    except Exception:
        pass
    return results


async def _tcp_probe(http, sem, ip, port, results, probe_timeout):
    async with sem:
        try:
            async with http.get(
                f"http://{ip}:{port}/api/info", timeout=probe_timeout,
            ) as r:
                if r.status != 200:
                    return
                info = await r.json(content_type=None)
        except Exception:
            return
        if info.get("app") != "moli_remote":
            return
        try:
            port_ = int(info.get("port", port))
        except (TypeError, ValueError):
            port_ = port
        results[f"{ip}:{port_}"] = {**info, "ip": ip, "port": port_, "via": "tcp"}


def tcp_scan(port=8765, timeout=3.5):
    """对本机各网段并发 HTTP 探测(纯 TCP,UDP 被拦时的兜底发现)。"""
    import aiohttp

    results = {}
    probe_timeout = aiohttp.ClientTimeout(sock_connect=0.5, sock_read=0.8, total=1.6)

    async def _run():
        targets = {"127.0.0.1"}
        for net in _local_subnets():
            targets.update(str(h) for h in net.hosts())
        conn = aiohttp.TCPConnector(limit=0, force_close=True)
        async with aiohttp.ClientSession(connector=conn) as http:
            sem = asyncio.Semaphore(64)  # 保守并发,避免端口/句柄压力
            await asyncio.wait_for(
                asyncio.gather(*(
                    _tcp_probe(http, sem, ip, port, results, probe_timeout)
                    for ip in targets)),
                timeout=timeout)

    try:
        asyncio.run(_run())
    except Exception:
        pass  # 超时/部分失败:返回已收集到的
    return results


def _dev_rank(r, primary):
    ip = r["ip"]
    return (0 if ip == primary else 1,          # 同一设备优先默认路由源地址
            0 if r.get("via") == "udp" else 1,
            ip)


def scan(timeout=2.2, port=8765):
    """混合发现:UDP 广播(快)+ TCP 网段扫描(可靠),按设备 ID 去重。"""
    udp = udp_scan(timeout=min(1.0, timeout))
    tcp = tcp_scan(port=port, timeout=max(1.5, timeout))
    merged = dict(tcp)
    merged.update(udp)
    primary = default_route_ip()
    local = set(lan_ips()) | {"127.0.0.1"}
    by_id = {}
    for r in merged.values():
        key = r.get("id") or f"{r['ip']}:{r.get('port')}"
        cand = by_id.get(key)
        if cand is None or _dev_rank(r, primary) < _dev_rank(cand, primary):
            by_id[key] = r
    devs = []
    for r in by_id.values():
        r["self"] = r["ip"] in local
        devs.append(r)
    devs.sort(key=lambda d: (not d["self"], str(d.get("name", ""))))
    return devs
