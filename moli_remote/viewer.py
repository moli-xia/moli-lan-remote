"""主控端助手:扫描局域网内的 魔力局域网远程助手 被控端,选中后用浏览器打开。"""

import sys
import webbrowser

from .discovery import scan


def run_viewer(timeout=1.5, open_index=None):
    print("正在扫描局域网内的 魔力局域网远程助手 设备…")
    devs = scan(timeout)
    if not devs:
        print("未发现设备。请确认:")
        print("  1. 被控端已运行(moli_remote server)")
        print("  2. 两台设备在同一局域网")
        print("  3. 被控端防火墙已放行 UDP 47520")
        return 1

    print(f"\n发现 {len(devs)} 台设备:\n")
    for i, d in enumerate(devs, 1):
        tag = "  ← 本机" if d.get("self") else ""
        print(f"  [{i}] {d.get('name', '?')}  {d.get('os', '')}  "
              f"http://{d['ip']}:{d['port']}{tag}")

    idx = open_index
    while True:
        if idx is None:
            if not sys.stdin.isatty():
                break
            try:
                raw = input("\n输入序号打开浏览器(r=重新扫描, q=退出): ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                break
            if raw in ("q", "exit"):
                break
            if raw == "r":
                return run_viewer(timeout, None)
            if raw.isdigit() and 1 <= int(raw) <= len(devs):
                idx = int(raw)
            else:
                print("无效输入。")
                continue
        d = devs[idx - 1]
        url = f"http://{d['ip']}:{d['port']}"
        print(f"正在打开 {d.get('name', url)} → {url}")
        webbrowser.open(url)
        break
    return 0
