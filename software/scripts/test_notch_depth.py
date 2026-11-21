#!/usr/bin/env python3
"""对比：单次陷波 vs 级联多次陷波，对 50Hz / 25Hz / 各频带的抑制效果。

抓 ~20 秒真机数据，对同一段数据分别施加：
  A) 现状：带通 0.5-45 + 单次 50Hz 陷波 + 单次 25Hz 陷波
  B) 加强：带通 0.5-45 + 3 次 50Hz 陷波 + 3 次 25Hz 陷波
打印两方案下 50Hz 峰、25Hz 峰、α/β/δ 频带功率，看加强版能压多少。
"""
import numpy as np
from scipy.signal import butter, iirnotch, filtfilt, welch
import time, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from eegscope.sources.openbci import OpenBCISource
from eegscope.sources.ports import detect_stm32_device_names


def bp(x, fs, lo=0.5, hi=45.0, order=4):
    b, a = butter(order, [lo / (fs / 2), hi / (fs / 2)], "bandpass")
    return filtfilt(b, a, x)


def notch(x, fs, f0, q=30.0):
    b, a = iirnotch(f0 / (fs / 2), q)
    return filtfilt(b, a, x)


def band_power(freqs, psd_avg, lo, hi):
    m = np.logical_and(freqs >= lo, freqs < hi)
    return psd_avg[m].sum() if m.sum() else 0.0


def main():
    ports = detect_stm32_device_names()
    if len(ports) != 4:
        print(f"⚠️ 检测到 {len(ports)} 个串口，预期 4 个")
        return 1
    print("抓 20 秒原始数据…")
    srcs = [OpenBCISource(p, n_channels=16) for p in ports]
    for s in srcs:
        s.start()
    time.sleep(1.5)

    fs = 125
    N = 2500
    chunks = [[] for _ in srcs]
    got = 0
    while got < N:
        for bi in range(len(srcs)):
            d = srcs[bi].read(60)
            if d.shape[1] > 0:
                chunks[bi].append(d)
        got += 60
        time.sleep(0.005)
    data = np.concatenate(
        [np.concatenate(chunks[bi], axis=1)[:, :N] for bi in range(len(srcs))], axis=0
    )
    for s in srcs:
        try:
            s.stop()
        except Exception:
            pass
    print(f"数据: {data.shape}")

    def clean_a(x):
        y = bp(x, fs)
        y = notch(y, fs, 25.0)
        return notch(y, fs, 50.0)

    def clean_b(x):
        y = bp(x, fs)
        for _ in range(3):
            y = notch(y, fs, 25.0)
        for _ in range(3):
            y = notch(y, fs, 50.0)
        return y

    # 用 64 通道均值信号对比（通道数多，滤波耗时可控）
    mean_sig = data.mean(axis=0)
    fa = clean_a(mean_sig)
    fb = clean_b(mean_sig)

    def report(tag, y):
        freqs, psd = welch(y, fs, nperseg=512)
        psd_avg = psd if psd.ndim == 1 else psd.mean(axis=0)
        p50 = band_power(freqs, psd_avg, 49.4, 50.6)
        p25 = band_power(freqs, psd_avg, 24.4, 25.6)
        d = band_power(freqs, psd_avg, 0.5, 4)
        a = band_power(freqs, psd_avg, 8, 13)
        b = band_power(freqs, psd_avg, 13, 30)
        print(f"\n[{tag}]")
        print(f"  50Hz 峰(49.4-50.6): {p50:12.1f} µV²")
        print(f"  25Hz 峰(24.4-25.6): {p25:12.1f} µV²")
        print(f"  δ 0.5-4:            {d:12.1f} µV²")
        print(f"  α 8-13:             {a:12.1f} µV²")
        print(f"  β 13-30:            {b:12.1f} µV²")
        return p50, p25

    p50a, p25a = report("A 现状(单次陷波)", fa)
    p50b, p25b = report("B 加强(级联3次)", fb)
    print("\n=== 抑制倍数 ===")
    print(f"  50Hz: {p50a / max(p50b, 1e-9):.1f}x")
    print(f"  25Hz: {p25a / max(p25b, 1e-9):.1f}x")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
