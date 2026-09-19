"""Windows 防火墙辅助:检测并添加 魔力局域网远程助手 入站放行规则。

局域网连不上的最常见原因是 Windows 防火墙拦截了入站 TCP/UDP。
这里提供三层能力:
- firewall_rule_exists()/firewall_enabled():状态检测(供 UI 展示);
- add_rule_quiet():以当前进程权限直接添加(需管理员,通常失败);
- add_rule_elevated():通过 UAC 提升运行 netsh 添加(会弹系统确认框)。
"""

import ctypes
import os
import subprocess
import sys

RULE_NAME = "魔力局域网远程助手"
_NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW


def exe_path():
    """需要放行的程序:冻结环境为主程序 exe;源码运行为 python.exe 本身。"""
    return os.path.abspath(sys.executable)


def _netsh(args, timeout=8):
    try:
        r = subprocess.run(["netsh", *args], capture_output=True, text=True,
                           timeout=timeout, creationflags=_NO_WINDOW)
        return r.returncode == 0, r.stdout or ""
    except Exception:
        return False, ""


def firewall_rule_exists():
    ok, out = _netsh(["advfirewall", "firewall", "show", "rule",
                      f"name={RULE_NAME}"])
    if not ok:
        return False
    return ("启用" in out) or ("Enable" in out and "Yes" in out) or \
           ("Enabled:" in out and "Yes" in out)


def firewall_enabled():
    """任一配置文件开启即视为防火墙生效(保守)。"""
    ok, out = _netsh(["advfirewall", "show", "allprofiles", "state"])
    if not ok:
        return True
    states = []
    for line in out.splitlines():
        key, _, val = line.partition(":")
        if key.strip().lower() in ("state", "状态"):
            states.append(val.strip().upper())
    if not states:
        return True
    return any(s in ("ON", "开", "启用", "ENABLE") for s in states)


def add_rule_quiet():
    """以当前权限直接添加规则(删除旧的避免路径过期)。返回是否成功。"""
    _netsh(["advfirewall", "firewall", "delete", "rule", f"name={RULE_NAME}"])
    ok, _ = _netsh(["advfirewall", "firewall", "add", "rule",
                    f"name={RULE_NAME}", "dir=in", "action=allow",
                    f"program={exe_path()}", "enable=yes", "profile=any"])
    return ok


def add_rule_elevated():
    """通过 UAC 提升运行 netsh 添加规则(弹系统确认框,异步执行)。"""
    params = (
        f'advfirewall firewall delete rule name="{RULE_NAME}" & '
        f'advfirewall firewall add rule name="{RULE_NAME}" dir=in '
        f'action=allow program="{exe_path()}" enable=yes profile=any'
    )
    try:
        rc = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", "netsh", params, None, 0)
        return rc > 32
    except Exception:
        return False


def check_status():
    """综合状态:返回 (防火墙开启, 规则存在)。"""
    return firewall_enabled(), firewall_rule_exists()
