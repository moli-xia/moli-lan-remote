"""生成打包用 .ico 图标(host/viewer 两种配色)。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from PySide6.QtWidgets import QApplication  # noqa: E402

from moli_remote.qtui.common import make_logo_pixmap  # noqa: E402


def main():
    app = QApplication([])
    out_dir = Path(__file__).parent.parent / "packaging" / "icons"
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, accent, accent2 in (
        ("host", "#3b82f6", "#22d3ee"),
        ("viewer", "#22c55e", "#4ade80"),
    ):
        pm = make_logo_pixmap(256, accent, accent2)
        pm.save(str(out_dir / f"{name}.ico"), "ICO")
        pm.save(str(out_dir / f"{name}.png"), "PNG")
        print("written", out_dir / f"{name}.ico")


if __name__ == "__main__":
    main()
