"""PyInstaller 入口:被控端。"""
import sys

from moli_remote.qtui.host_app import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
