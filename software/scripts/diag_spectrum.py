#!/usr/bin/env python3
"""诊断：抓 ~15 秒原始数据，打印频谱峰值 + 分频带功率 + 25/50Hz 专项，定位噪声来源。"""
import numpy as np
from scipy.signal import welch, find_peaks
import time, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from eegscope.sources.openbci import OpenBCISource
from eegscope.sources.ports import detect_stm32_device_names


def main():
    ports = detect_stm32_device_names()
    if len(ports) != 4:
        print(f"⚠️ 检测到 {len(ports)} 个串口，预期 4 个：{ports}")
        return 1
    print(f"抓到 4 板，读 15 秒原始数据…（静坐即可）")
    srcs = [OpenBCISource(p, n_channels=16) for p in ports]
    for s in srcs:
        s.start()
    time.sleep(1.5)

    fs = 125
    N = 1875  # 15s
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
    print(f"数据: {data.shape} (通道×样本)")

    # 逐通道 raw PSD（未滤波，看真实频谱）
    freqs, psd = welch(data, fs, nperseg=512)
    psd_avg = psd.mean(axis=0)

    peaks, _ = find_peaks(psd_avg, height=psd_avg.max() * 0.05, distance=5)
    top = sorted(peaks, key=lambda p: psd_avg[p], reverse=True)[:12]
    print("\n=== 平均频谱 TOP 峰值（原始、未滤波）===")
    for p in top:
        print(f"  {freqs[p]:6.2f} Hz : {psd_avg[p]:10.1f} µV²/Hz")

    bands = [("δ 0.5-4", 0.5, 4), ("θ 4-8", 4, 8), ("α 8-13", 8, 13),
             ("β 13-30", 13, 30), ("γ 30-45", 30, 45)]
    print("\n=== 64 通道平均分频带功率 (µV²) ===")
    for nm, lo, hi in bands:
        m = (freqs >= lo) & (freqs < hi)
        print(f"  {nm:>10}: {psd[:, m].sum(axis=1).mean():10.1f}")

    print("\n=== 专项频率检查（µV²/Hz，看有没有尖峰）===")
    # 注意：fs=125 → Nyquist=62.5Hz，超过 62.5Hz 的频率（如 100Hz 谐波）采样不到，不查
    for f0, label in [(25, "25Hz 板载振荡"), (50, "50Hz 工频"),
                      (12.5, "12.5Hz(50/4)"), (16.67, "16.7Hz(50/3)")]:
        m = (freqs >= f0 - 0.6) & (freqs <= f0 + 0.6)
        if m.sum() == 0:
            continue
        print(f"  {label:>18} @ {f0:g}Hz: {psd_avg[m].mean():10.1f} µV²/Hz")

    # 单通道看：找 alpha 最强通道的 PSD 峰值
    al = psd[:, (freqs >= 8) & (freqs < 13)].sum(axis=1)
    best = int(np.argmax(al))
    print(f"\n=== α 最强通道 = 通道{best + 1}（全局索引），其 TOP 峰 ===")
    pk, _ = find_peaks(psd[best], height=psd[best].max() * 0.05, distance=5)
    tp = sorted(pk, key=lambda p: psd[best][p], reverse=True)[:8]
    for p in tp:
        print(f"  {freqs[p]:6.2f} Hz : {psd[best][p]:10.1f} µV²/Hz")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
