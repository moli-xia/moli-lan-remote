# 魔力局域网远程助手(Moli LAN Remote Assistant)

参照 [UU 远程] 的交互方式编写的**局域网**远程桌面工具:

- **被控端**:Windows 桌面应用(系统托盘常驻,深色界面),一键共享本机屏幕
- **主控端**:Windows 桌面应用(视频窗口 + 完整键鼠控制 + 深色界面),也可用浏览器/手机直连(零安装)
- **单一安装包**:双 exe 共享运行库,Inno Setup 中文安装向导,自动放行防火墙、可选开机自启

> ⚠ **免责声明**:本软件仅供可信局域网内**经授权**的远程协助与学习研究使用,严禁用于未授权控制、监控他人设备。完整声明见 [DISCLAIMER.md](DISCLAIMER.md),安装向导中亦需确认同意。

![平台](https://img.shields.io/badge/平台-Windows%2010%2F11-blue) ![场景](https://img.shields.io/badge/场景-可信局域网-informational) ![版本](https://img.shields.io/badge/版本-1.0.0-green) ![协议](https://img.shields.io/badge/协议-MIT-yellow)

## 功能特性

- 🔍 **设备发现**:UDP 定向广播 + TCP 网段兜底双通道扫描(多网卡/AP 隔离环境依然可靠),也可手动输地址
- 🔢 **验证码直连**:6 位验证码认证,错误 5 次自动封禁 1 分钟;断线自动重连,Token 免重复输码
- ⚡ **低延迟管线**:libjpeg-turbo 零拷贝直编 + 4:2:0 子采样;**拥塞感知动态码率**(Wi-Fi 带宽不足自动降档保流畅);发送缓冲受限防积压
- 🖥 **多显示器**:切换"全部屏幕"或单块显示器
- 🎨 **画质切换**:流畅(0.5x) / 高清(自适应) / 超清(全分辨率)三档
- 🖱 **完整控制**:鼠标移动/点击/拖拽/滚轮、键盘(含组合键)、本地光标即时预览
- 🈶 **中文透传**:主控端"文字"输入条,支持中文输入法;手机浏览器连接时同样可用
- 📋 **剪贴板互通**:双向同步文本
- 📱 **Web 端保留**:被控端同时提供网页客户端,手机/平板浏览器打开地址即可控制
- 🪟 **被控端托盘**:关闭窗口即最小化到托盘,服务持续运行;可随时重新生成验证码

## 快速开始

### 安装(推荐)

从 [Releases](https://github.com/moli-xia/moli-lan-remote/releases) 下载 `MoliLanRemote-Setup-1.0.0.exe`:

1. 阅读并同意**免责声明**(安装向导第一步)
2. 勾选 **防火墙放行**(强烈推荐)、**桌面快捷方式**、**开机自启被控端**(可选)
3. 安装后开始菜单/桌面出现两个程序:**魔力被控端**、**魔力主控端**

### 被控端(被控制的那台电脑)

启动 **魔力被控端**(托盘图标,主窗口显示):

```
连接验证码   3 8 4 9 2 0
访问地址     http://192.168.1.5:8765
状态         运行中 · 等待主控端连接…
```

关闭窗口只是最小化到托盘;右键托盘图标 → 显示主窗口 / 重新生成验证码 / 退出。

### 主控端(控制别人的电脑)

启动 **魔力主控端**:

1. 设备列表自动扫描出局域网内的设备,双击(或手动输入 `IP:端口`)
2. 输入被控端显示的 6 位验证码 → 连接
3. 视频窗口出现后即可键鼠控制;工具栏提供画质/显示器切换、文字输入、剪贴板、全屏(Esc 退出),右下角实时显示分辨率/帧率/延迟

手机/平板也可以直接用浏览器打开 `http://被控IP:8765`,体验同一套功能。

### 从源码运行

```bash
pip install -r requirements.txt   # 含 PySide6-Essentials / pyturbojpeg
# 可选:安装 libjpeg-turbo 运行库(无则自动回退 PIL 编码)
#   winget install -e --id libjpeg-turbo.libjpeg-turbo.VC
python -m moli_remote host        # 被控端桌面应用
python -m moli_remote client      # 主控端桌面应用
python -m moli_remote             # 被控端控制台版(无需 GUI)
python -m moli_remote viewer      # 浏览器扫描器(控制台)
```

## 命令行参数(控制台版 / host 附加参数)

| 参数 | 说明 | 默认 |
| --- | --- | --- |
| `--port` | HTTP/WS 端口 | 8765 |
| `--name` | 设备名称(发现列表里显示) | 主机名 |
| `--code` | 固定 6 位验证码 | 每次启动随机 |
| `--monitor` | 初始显示器:0=全部屏幕,1..n=单屏 | 1 |
| `--fps` | 帧率上限(5-30) | 24 |
| `--quality` | JPEG 质量 30-95 | 62 |
| `--no-discovery` | 关闭 UDP 发现应答 | 开启 |

## 发现机制与故障排查

设备发现使用**双通道**,专门解决局域网"查不到/连不上":

1. **UDP 定向广播**(快):向本机每个网段的广播地址发送探测(多网卡环境不遗漏);被控端应答时附带"对主控端可达的源 IP",避免返回 WSL/Hyper-V 虚拟网卡地址
2. **TCP 网段扫描**(可靠):对本机各网段并发探测 `:8765/api/info`——即使 UDP 被防火墙或路由器 AP 隔离拦截,只要 TCP 可达就能发现并连接

**连不上时按序检查**:

| 症状 | 原因与处理 |
| --- | --- |
| 列表为空 | ①被控端没运行(注意托盘) ②跨网段 → 手动输入 `IP:8765` 连接 |
| 查到但连不上 | **被控端防火墙未放行**(最常见)→ 被控端窗口红色警告点「立即修复」(UAC 确认一次) |
| 验证码错误 | 每次启动随机生成,以被控端窗口显示为准;错 5 次锁 1 分钟(可"重新生成验证码"重置) |
| 画面卡顿/反复重连 | Wi-Fi 带宽不足 → v1.0.0 已自动降档处理;仍差请用 5GHz Wi-Fi 或网线 |
| 断开后扫描不到 | 被控端是否被从托盘退出了?重新启动被控端 |

## 低延迟设计

- **编码**:libjpeg-turbo 直接从 mss 的 BGRA 缓冲区零拷贝编码(跳过像素转换),4:2:0 子采样;DLL 随安装包分发,缺失时自动回退 PIL
- **抓屏**:缩放档在 Windows 上用 StretchBlt 按目标分辨率直接抓屏;4K 屏"高清"档实测本机选择 0.75x 或全尺寸
- **无线(Wi-Fi)专项**:
  - **拥塞感知动态码率**:拥塞时自动降质量(62→32)与分辨率(1.0x→0.75x→0.5x),空闲 3 秒逐级恢复
  - **发送缓冲限制 256KB**:防内核缓冲无限扩张导致心跳饿死(断连死循环的根源)
  - **TCP_NODELAY**:键鼠小包立即发出
  - **本地光标即时预览**:鼠标输入瞬间本地绘制预测光标,远程光标追上后收敛
- 实测(4K 屏):全尺寸约 12fps/33ms;模拟 5Mbps 慢链路 40 秒持续拥塞不断连

## 打包构建(exe 安装包)

```bash
packaging\build.bat
```

流程:清理残留进程 → 生成图标 → PyInstaller 打包 `MoliHost.exe` + `MoliViewer.exe`(共享 `_internal`,含 turbojpeg.dll)→ Inno Setup 生成 `packaging/dist/MoliLanRemote-Setup-1.0.0.exe`(含免责声明许可页)。

## 开源协议与免责

- 代码基于 [MIT License](LICENSE) 开源
- 使用本软件须同时遵守 [DISCLAIMER.md](DISCLAIMER.md) —— **仅限合法、经授权的远程协助用途**,作者不对滥用行为承担责任

## 项目结构

```
moli-lan-remote/
├── moli_remote/
│   ├── __main__.py      # CLI:host / client / server / viewer
│   ├── server.py        # aiohttp 服务:WS 流、认证、控制权、拥塞自适应
│   ├── client.py        # 主控端 WS worker(线程 + Qt 信号桥)
│   ├── capture.py       # StretchBlt/mss 抓屏 + turbojpeg 编码 + 光标
│   ├── input_control.py # pynput 键鼠注入 + 剪贴板
│   ├── discovery.py     # UDP 定向广播 + TCP 网段兜底发现
│   ├── viewer.py        # 浏览器扫描助手(控制台)
│   ├── static/          # 网页客户端(手机可用)
│   └── qtui/            # Qt 桌面应用
│       ├── host_app.py  #   被控端:托盘 + 状态窗口 + 防火墙修复
│       ├── viewer_app.py#   主控端:发现/连接/视频/键鼠
│       ├── firewall.py  #   防火墙检测/一键修复
│       └── common.py    #   深色主题/图标/配置/日志/单实例
├── packaging/           # PyInstaller spec + Inno Setup 脚本 + 免责声明许可页
├── tools/               # 图标生成 / e2e 自测 / 性能基准
├── DISCLAIMER.md        # 免责声明
├── LICENSE              # MIT
├── 启动被控端.bat / 启动主控端.bat
└── requirements.txt / pyproject.toml
```
