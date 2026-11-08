#!/usr/bin/env python3
"""emotion_live.py —— 真机情绪指标一键测试：五频带功率 + α 地形特征。

读 20 秒真机数据 → 滤波(去直流+带通0.5-45+50Hz陷波) → welch 算五频带功率
→ 报告平均频带功率 + α/β 比(放松度) + α 最强通道(枕区特征)。

用法：.venv/bin/python scripts/emotion_live.py
"""
import serial.tools.list_ports
import numpy as np
from scipy.signal import butter, filtfilt, iirnotch, welch
import time, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from eegscope.sources.openbci import OpenBCISource


def bp(x, fs, lo=0.5, hi=45.0, order=4):
    b, a = butter(order, [lo / (fs / 2), hi / (fs / 2)], "bandpass")
    return filtfilt(b, a, x)


def notch(x, fs, f0=50.0, q=30.0):
    b, a = iirnotch(f0 / (fs / 2), q)
    return filtfilt(b, a, x)


def main():
    ports = [p.device for p in serial.tools.list_ports.comports()
             if p.vid == 0x0483 or "STM32" in (p.description or "").upper()]
    if len(ports) != 4:
        print(f"⚠️ 检测到 {len(ports)} 个串口，预期 4 个")
        return 1
    print("读 20 秒数据…（请静坐、别动、别眨眼太频繁）")
    srcs = [OpenBCISource(p, n_channels=16) for p in ports]
    for s in srcs:
        s.start()
    time.sleep(1.5)

    fs = 125
    N = 2500
    # 先做 1 秒快速检查，识别哪些板在流（跳过坏板）
    alive = []
    for bi, s in enumerate(srcs):
        t0 = time.time(); n = 0
        while time.time() - t0 < 1.0:
            d = s.read(20)
            if d.shape[1] > 0:
                n += d.shape[1]
        if n > 30:
            alive.append(bi)
    if not alive:
        print("⚠️ 没有板在流数据")
        return 1
    print(f"在流板: {[bi + 1 for bi in alive]} (共 {len(alive)} 块 / {len(alive) * 16} 通道)")

    # 只从在流的板读 ~20 秒
    chunks = [[] for _ in srcs]
    got = 0
    while got < N:
        for bi in alive:
            d = srcs[bi].read(60)
            if d.shape[1] > 0:
                chunks[bi].append(d)
        got += 60
        time.sleep(0.005)
    data = np.concatenate([np.concatenate(chunks[bi], axis=1)[:, :N] for bi in alive], axis=0)
    for s in srcs:
        try:
            s.stop()
        except Exception:
            pass
    print(f"数据: {data.shape}")

    def _clean(x):
        y = bp(x - x.mean(), fs)
        y = notch(y, fs, 25.0)   # 25Hz 硬件振荡陷波
        return notch(y, fs, 50.0)  # 50Hz 工频陷波

    filt = np.array([_clean(x) for x in data])

    bands = [("δ 0.5-4", 0.5, 4), ("θ 4-8", 4, 8), ("α 8-13", 8, 13),
             ("β 13-30", 13, 30), ("γ 30-45", 30, 45)]
    freqs, psd = welch(filt, fs, nperseg=256)
    pow_all = {}
    for nm, lo, hi in bands:
        m = (freqs >= lo) & (freqs < hi)
        pow_all[nm] = psd[:, m].sum(axis=1)

    print("\n=== 64 通道平均频带功率 (µV²) ===")
    avg = {nm: float(np.mean(pow_all[nm])) for nm, _, _ in bands}
    for nm, _, _ in bands:
        print(f"  {nm:>10}: {avg[nm]:10.1f}")
    a = avg["α 8-13"]
    b = avg["β 13-30"]
    t = avg["θ 4-8"]
    print(f"\n  α/β 比 = {a / b:.2f}  (越高越放松)")
    print(f"  α/θ 比 = {a / t:.2f}")

    al = pow_all["α 8-13"]
    order = np.argsort(al)[::-1][:8]
    print("\n=== α 最强的 8 个通道（应是枕区）===")
    for i in order:
        print(f"  通道{i + 1:2d}: α={al[i]:8.1f} µV²")

    print("\n⚠️ FAA(额叶偏侧化) 需 F3/F4 精确通道名，串口未识板，先看整体频带。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
