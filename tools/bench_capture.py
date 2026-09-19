"""屏幕采集/编码性能基准:连续采集编码 N 帧,输出耗时与帧大小。"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from moli_remote.capture import ScreenCapture  # noqa: E402

N = 12


def bench(scale):
    c = ScreenCapture(1)
    for _ in range(2):
        c.grab(62, scale)
    times, sizes = [], []
    for _ in range(N):
        c.invalidate()  # 模拟画面持续变化(最坏情况)
        t0 = time.perf_counter()
        f = c.grab(62, scale)
        times.append((time.perf_counter() - t0) * 1000)
        sizes.append(len(f.jpeg))
    avg = sum(times) / N
    print(f"scale={scale}: 平均 {avg:6.1f} ms/帧  最大 {max(times):6.1f} ms  "
          f"帧大小 {sum(sizes)/N/1024:5.0f} KB  → 理论 {1000/avg:5.1f} fps")


if __name__ == "__main__":
    print("(4K 屏幕,质量 62,连续变化画面)")
    bench(1.0)
    bench(0.5)
