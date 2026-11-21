#!/usr/bin/env python3
"""合成数据测试：verify detect_artifacts 对眨眼/肌电/运动/跳变/干净的判定。"""
import numpy as np
from scipy.signal import butter, filtfilt
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from eegscope.artifacts import detect_artifacts

FS = 250
N = 2000  # 8 秒
NAMES = ["Fp1", "Fp2", "F3", "F4", "F7", "F8", "Fz", "Cz",
         "C3", "C4", "T7", "T8", "Pz", "O1", "O2", "Oz"]
NCH = len(NAMES)


def _band_noise(n, fs, lo=0.5, hi=45.0, seed=0):
    rng = np.random.default_rng(seed)
    x = rng.standard_normal(n)
    b, a = butter(4, [lo / (fs / 2), hi / (fs / 2)], "bandpass")
    return filtfilt(b, a, x)


def clean_sig(seed=0):
    t = np.arange(N) / FS
    sig = np.zeros((NCH, N))
    for i in range(NCH):
        sig[i] = 12.0 * _band_noise(N, FS, seed=seed + i) + 20.0 * np.sin(2 * np.pi * 10.0 * t)
    return sig, t


def main():
    # 1) 干净 → 无伪迹
    sig, t = clean_sig()
    r = detect_artifacts(sig, FS, NAMES)
    assert r["clean"], f"干净信号误判: {r['reasons']}"
    print(f"[OK] 干净: clean=True, reasons={r['reasons']}")

    # 2) 眨眼：额区 Fp1/Fp2 大振幅
    sig, t = clean_sig()
    b0, bl = int(1.0 * FS), int(0.25 * FS)
    for ch in (0, 1):  # Fp1, Fp2
        sig[ch, b0:b0 + bl] += 200.0 * np.hanning(bl)
    r = detect_artifacts(sig, FS, NAMES)
    assert r["blink"], f"眨眼未检出: {r['reasons']}"
    print(f"[OK] 眨眼: blink={r['blink']}, reasons={r['reasons']}")

    # 3) 肌电：颞区 T7/T8 高频(40Hz)低幅
    sig, t = clean_sig()
    for ch in (10, 11):  # T7, T8
        sig[ch] += 60.0 * np.sin(2 * np.pi * 40.0 * t)
    r = detect_artifacts(sig, FS, NAMES)
    assert r["emg"], f"肌电未检出: {r['reasons']}"
    print(f"[OK] 肌电: emg={r['emg']}, reasons={r['reasons']}")

    # 4) 运动：全通道大振幅低频(1.5Hz)
    sig, t = clean_sig()
    sig += 300.0 * np.sin(2 * np.pi * 1.5 * t)
    r = detect_artifacts(sig, FS, NAMES)
    assert r["motion"], f"运动未检出: {r['reasons']}"
    print(f"[OK] 运动: motion={r['motion']}, reasons={r['reasons']}")

    # 5) 电极跳变：单样本尖峰
    sig, t = clean_sig()
    sig[5, int(1.0 * FS)] += 300.0
    r = detect_artifacts(sig, FS, NAMES)
    assert r["pop"], f"跳变未检出: {r['reasons']}"
    print(f"[OK] 跳变: pop={r['pop']}, reasons={r['reasons']}")

    print("\n全部通过 ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
