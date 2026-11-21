#!/usr/bin/env python3
"""真机伪迹诊断：抓 N 秒 → DSP(带通+级联陷波) → detect_artifacts + 分频带功率。

用法：.venv/bin/python scripts/diag_artifacts.py [秒数，默认 10]

输出：伪迹判定(clean/blink/emg/motion/pop + reasons) + 五频带功率 + 50/25Hz 抑制。
通道名用 config64.yaml（注意：USB 枚举顺序 ≠ 物理板序，额区/颞区前缀判定是近似，
识板前 blink/motion 标签可能有偏差，但「是否检出伪迹」不受影响）。
"""
import sys
import time
import os

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eegscope.analysis import welch_psd
from eegscope.artifacts import detect_artifacts
from eegscope.config import load_config
from eegscope.dsp import SignalChain
from eegscope.sources.openbci import OpenBCISource
from eegscope.sources.ports import detect_stm32_device_names


def main():
    dur = float(sys.argv[1]) if len(sys.argv) > 1 else 10.0
    ports = detect_stm32_device_names()
    if len(ports) < 1:
        print(f"⚠️ 没扫到任何板")
        return 1
    if len(ports) != 4:
        print(f"⚠️ 预期 4 板，只扫到 {len(ports)} 个，继续用 {len(ports)} 板测：{ports}")

    cfg = load_config("config64.yaml")
    fs = 125.0
    N = int(dur * fs)

    print(f"抓 {dur:g} 秒…（4 板，静坐/按指令动作）")
    srcs = [OpenBCISource(p, n_channels=16) for p in ports]
    for s in srcs:
        s.start()
    time.sleep(1.5)

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

    # DSP（带通 0.5-45 + 级联 50/25Hz 陷波，与线上口径一致）
    chain = SignalChain(fs, cfg.dsp.bandpass_hz, cfg.dsp.notch_hz, cfg.dsp.notch_q)
    filt = chain.process(data)

    names = [str(c) for c in cfg.channels]
    if len(names) != filt.shape[0]:
        names = [f"ch{i + 1}" for i in range(filt.shape[0])]

    r = detect_artifacts(filt, fs, names)
    print(f"\n=== 伪迹判定 ===")
    print(f"  clean={r['clean']}  blink={r['blink']}  emg={r['emg']}  motion={r['motion']}  pop={r['pop']}")
    print(f"  有伪迹通道 {r['n_artifact_channels']}/{filt.shape[0]}")
    if r["reasons"]:
        print("  原因：" + "、".join(r["reasons"]))
    else:
        print("  原因：（无，判定干净）")

    freqs, psd = welch_psd(filt, fs)
    psd_avg = psd.mean(axis=0)
    print(f"\n=== 分频带功率均值 (µV²) ===")
    for lo, hi, nm in [(0.5, 4, "δ"), (4, 8, "θ"), (8, 13, "α"),
                       (13, 30, "β"), (30, 45, "γ")]:
        m = np.logical_and(freqs >= lo, freqs < hi)
        print(f"  {nm} {lo:g}-{hi:g}: {psd[:, m].sum(axis=1).mean():10.1f}")
    # 50/25Hz 专项
    for f0, nm in [(25, "25Hz"), (50, "50Hz")]:
        m = np.logical_and(freqs >= f0 - 0.6, freqs <= f0 + 0.6)
        if m.sum():
            print(f"  {nm} 残余: {psd_avg[m].mean():10.1f} µV²/Hz")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
