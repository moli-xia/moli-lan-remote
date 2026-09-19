"""PyInstaller 入口:主控端。"""
import sys

from moli_remote.qtui.viewer_app import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
