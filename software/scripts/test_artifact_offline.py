#!/usr/bin/env python3
"""端到端验证：合成录制（注入眨眼）→ analyze_session 离线检测伪迹 + 门控 FAA。"""
import json
import os
import subprocess
import sys
import tempfile

import numpy as np
from scipy.signal import butter, filtfilt

SOFT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SOFT)

FS = 250
N = 2500  # 10 秒
NAMES = ["Fp1", "Fp2", "F3", "F4", "F7", "F8", "Fz", "Cz",
         "C3", "C4", "T7", "T8", "Pz", "O1", "O2", "Oz"]


def _band_noise(n, fs, lo=0.5, hi=45.0, seed=0):
    r = np.random.default_rng(seed)
    x = r.standard_normal(n)
    b, a = butter(4, [lo / (fs / 2), hi / (fs / 2)], "bandpass")
    return filtfilt(b, a, x)


def main():
    t = np.arange(N) / FS
    raw = np.zeros((len(NAMES), N))
    for i in range(len(NAMES)):
        raw[i] = 12.0 * _band_noise(N, FS, seed=i) + 20.0 * np.sin(2 * np.pi * 10.0 * t)
    raw += 5.0 * np.sin(2 * np.pi * 50.0 * t) + 500.0  # 模拟原始 50Hz + DC
    b0, bl = int(3.0 * FS), int(0.25 * FS)
    for ch in (0, 1):  # Fp1/Fp2 眨眼
        raw[ch, b0:b0 + bl] += 200.0 * np.hanning(bl)

    d = tempfile.mkdtemp(prefix="hermes-verify-sess-")
    np.savez(os.path.join(d, "session.npz"), raw=raw, fs=np.array(FS),
             channel_names=np.array(NAMES))
    meta = {
        "sample_rate": FS, "source_type": "OpenBCISource",
        "config": {
            "dsp": {"bandpass_hz": [0.5, 45.0], "notch_hz": [50.0, 25.0, 25.0, 25.0],
                    "notch_q": 30.0},
            "analysis": {"bands": {"delta": [0.5, 4], "theta": [4, 8], "alpha": [8, 13],
                                   "beta": [13, 30], "gamma": [30, 45]},
                         "faa_channels": ["F3", "F4"]},
        },
    }
    with open(os.path.join(d, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f)

    r = subprocess.run(
        [sys.executable, os.path.join(SOFT, "scripts", "analyze_session.py"), d],
        capture_output=True, text=True, cwd=SOFT,
        env={"HOME": os.environ["HOME"], "PATH": "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin",
             "PYTHONPATH": ""},
    )
    assert r.returncode == 0, r.stderr

    z = np.load(os.path.join(d, "trajectories.npz"))
    art = z["artifact_flags"]
    assert art.any(), "伪迹应被检出（眨眼窗）"
    assert art.sum() < len(art), "不应所有窗都判伪迹"

    report = open(os.path.join(d, "report.md"), encoding="utf-8").read()
    assert "伪迹" in report, "报告应含伪迹汇总"

    print(f"[OK] 离线伪迹检测: {int(art.sum())}/{len(art)} 窗有伪迹（眨眼约在第 3s 窗）")
    print(f"[OK] report.md 含伪迹汇总行")
    # 清理
    for fn in os.listdir(d):
        os.remove(os.path.join(d, fn))
    os.rmdir(d)
    print("已清理临时会话目录")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
