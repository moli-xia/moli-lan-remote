# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包:MoliHost.exe + MoliViewer.exe 共享同一 _internal 目录。"""

import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent
sys.path.insert(0, str(ROOT))

host_entry = ROOT / "packaging" / "host_entry.py"
viewer_entry = ROOT / "packaging" / "viewer_entry.py"
icons = ROOT / "packaging" / "icons"

# turbojpeg 为 ctypes 加载,需手动带上其 DLL 与数据文件
try:
    from PyInstaller.utils.hooks import collect_all
    tj_datas, tj_bins, tj_hidden = collect_all("turbojpeg")
except Exception:
    tj_datas, tj_bins, tj_hidden = [], [], []
tj_dll = Path(r"C:\libjpeg-turbo64\bin\turbojpeg.dll")
if tj_dll.exists():
    tj_bins.append((str(tj_dll), "."))

host = Analysis(
    [str(host_entry)],
    pathex=[str(ROOT)],
    name="MoliHost",
    icon=str(icons / "host.ico"),
    console=False,
    exclude_binaries=True,
    noarchive=False,
    datas=[(str(ROOT / "moli_remote" / "static"), "moli_remote/static")] + tj_datas,
    binaries=tj_bins,
    hiddenimports=tj_hidden,
)
viewer = Analysis(
    [str(viewer_entry)],
    pathex=[str(ROOT)],
    name="MoliViewer",
    icon=str(icons / "viewer.ico"),
    console=False,
    exclude_binaries=True,
    noarchive=False,
)

pyz_host = PYZ(host.pure)
pyz_viewer = PYZ(viewer.pure)

exe_host = EXE(
    pyz_host,
    host.scripts,
    [],
    exclude_binaries=True,
    name="MoliHost",
    icon=str(icons / "host.ico"),
    console=False,
)
exe_viewer = EXE(
    pyz_viewer,
    viewer.scripts,
    [],
    exclude_binaries=True,
    name="MoliViewer",
    icon=str(icons / "viewer.ico"),
    console=False,
)

coll = COLLECT(
    exe_host,
    exe_viewer,
    host.binaries, host.zipfiles, host.datas,
    viewer.binaries, viewer.zipfiles, viewer.datas,
    name="MoliLanRemote",
    upx=False,
)
