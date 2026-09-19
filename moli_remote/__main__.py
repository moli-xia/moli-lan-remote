"""魔力局域网远程助手 命令行入口。

    python -m moli_remote            # 被控端控制台版(默认)
    python -m moli_remote host       # 被控端桌面应用(托盘)
    python -m moli_remote client     # 主控端桌面应用
    python -m moli_remote viewer     # 主控端:扫描局域网设备并打开浏览器
"""

import argparse
import asyncio
import os
import sys


def _build_parser():
    p = argparse.ArgumentParser(
        prog="moli-remote", description="魔力局域网远程助手 · 局域网远程桌面(参照 UU 远程交互)")
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("server", help="被控端:共享本机屏幕(控制台版,默认)")
    s.add_argument("--port", type=int, default=8765, help="HTTP/WS 端口(默认 8765)")
    s.add_argument("--name", default=None, help="设备名称(默认取主机名)")
    s.add_argument("--code", default=None, help="固定 6 位验证码(默认每次启动随机)")
    s.add_argument("--monitor", type=int, default=1,
                   help="初始显示器:0=全部屏幕,1..n=单块屏幕(默认 1)")
    s.add_argument("--fps", type=int, default=24, help="帧率上限(默认 24)")
    s.add_argument("--quality", type=int, default=70, help="JPEG 质量 30-95(默认 70)")
    s.add_argument("--no-discovery", action="store_true", help="关闭 UDP 设备发现")

    h = sub.add_parser("host", help="被控端桌面应用(系统托盘常驻)")
    h.add_argument("--port", type=int, default=None)
    h.add_argument("--name", default=None)
    h.add_argument("--code", default=None)
    h.add_argument("--monitor", type=int, default=None)
    h.add_argument("--fps", type=int, default=None)
    h.add_argument("--quality", type=int, default=None)

    c = sub.add_parser("client", help="主控端桌面应用")
    c.add_argument("--auto", default=None, metavar="IP:PORT", help="(测试)启动后自动连接")
    c.add_argument("--code", default=None, help="(测试)自动连接使用的验证码")
    c.add_argument("--auto-quit", type=int, default=6, help="(测试)连接后 N 秒自动退出")

    v = sub.add_parser("viewer", help="主控端:扫描局域网内的设备(浏览器)")
    v.add_argument("--timeout", type=float, default=1.5, help="扫描等待秒数(默认 1.5)")
    v.add_argument("--open", type=int, default=None, metavar="N",
                   help="扫描后直接打开第 N 台设备")
    return p


def main(argv=None):
    if os.name == "nt":
        os.system("")  # 让 Windows 控制台支持 ANSI 颜色
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] not in ("server", "host", "client", "viewer", "-h", "--help"):
        argv = ["server"] + argv
    args = _build_parser().parse_args(argv)

    if args.cmd == "viewer":
        from .viewer import run_viewer
        return run_viewer(args.timeout, args.open)

    if args.cmd == "host":
        from .qtui.host_app import main as host_main
        cargs = [f"--{k}={v}" for k, v in vars(args).items()
                 if v is not None and k not in ("cmd", "no_discovery")]
        return host_main(cargs)

    if args.cmd == "client":
        from .qtui.viewer_app import main as client_main
        cargs = []
        if args.auto:
            cargs += ["--auto", args.auto, "--auto-quit", str(args.auto_quit)]
        if args.code:
            cargs += ["--code", args.code]
        return client_main(cargs)

    from .server import run_server
    try:
        asyncio.run(run_server(
            port=args.port, code=args.code, name=args.name,
            quality=args.quality, fps=args.fps, monitor=args.monitor,
            discovery=not args.no_discovery,
        ))
    except KeyboardInterrupt:
        print("\n已退出,再见。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
