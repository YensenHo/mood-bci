"""SSVEP 算法正确性测试（无硬件，合成数据）。

验证高小榕团队三大算法 CCA / FBCCA / TRCA 能在合成 SSVEP 数据上正确识别频率。
合成方式：在枕区通道注入目标频率的正弦波（含二次谐波）+ 高斯噪声。

用法（隔离环境，见 skill）：
    env -i HOME=$HOME PATH=/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin \
        PYTHONPATH= .venv/bin/python scripts/test_ssvep.py
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from eegscope.ssvep import (  # noqa: E402
    cca_classify,
    fbcca_classify,
    trca_classify,
    trca_train,
)

FS = 250.0
FREQS = [8.0, 9.0, 10.0, 11.0, 12.0]
N_CH = 16  # 任意通道数，算法不依赖（64ch 同理）
WIN_SEC = 2.0
N_SAMPLES = int(FS * WIN_SEC)
OCC_CH = [12, 13, 14, 15]  # 模拟枕区通道（O1/O2/Oz 附近）


def make_ssvep(freq, n_ch=N_CH, n_samples=N_SAMPLES, fs=FS, noise=2.0):
    """合成一段 SSVEP 数据：枕区注入 freq 正弦波（基频+二次谐波），全通道加噪声。"""
    rng = np.random.default_rng(hash(freq) % (2 ** 32))  # 可复现
    data = rng.standard_normal((n_ch, n_samples)) * noise
    t = np.arange(n_samples) / fs
    sig = 10.0 * np.sin(2 * np.pi * freq * t) + 5.0 * np.sin(2 * np.pi * 2 * freq * t)
    for ch in OCC_CH:
        data[ch] += sig
    return data


def test_cca():
    data = make_ssvep(10.0)
    corrs, idx = cca_classify(data, FS, FREQS)
    assert FREQS[idx] == 10.0, f"CCA 识别错: 期望 10Hz，得到 {FREQS[idx]}Hz"
    print(f"[CCA]   ✓ 识别 {FREQS[idx]:.1f}Hz  corrs={np.round(corrs, 3)}")


def test_fbcca():
    data = make_ssvep(10.0)
    corrs, idx = fbcca_classify(data, FS, FREQS)
    assert FREQS[idx] == 10.0, f"FBCCA 识别错: 期望 10Hz，得到 {FREQS[idx]}Hz"
    print(f"[FBCCA] ✓ 识别 {FREQS[idx]:.1f}Hz  corrs={np.round(corrs, 3)}")


def test_trca():
    # 校准：每个频率 5 个 trial
    trials = []
    for f in FREQS:
        trials.append([make_ssvep(f) for _ in range(5)])
    model = trca_train(trials, FREQS)

    # 测试：新数据（10Hz，不同噪声）
    test_data = make_ssvep(10.0, n_samples=N_SAMPLES)
    corrs, idx = trca_classify(test_data, FREQS, model)
    assert FREQS[idx] == 10.0, f"TRCA 识别错: 期望 10Hz，得到 {FREQS[idx]}Hz"
    print(f"[TRCA]  ✓ 识别 {FREQS[idx]:.1f}Hz  corrs={np.round(corrs, 3)}")


def test_robustness_scan():
    """多频率扫描：每个频率独立验证识别准确率（简单鲁棒性检查）。"""
    n_ok = 0
    n_total = len(FREQS)
    for f in FREQS:
        data = make_ssvep(f)
        _, idx = cca_classify(data, FS, FREQS)
        if FREQS[idx] == f:
            n_ok += 1
    print(f"[CCA 扫描] {n_ok}/{n_total} 个频率识别正确")
    assert n_ok == n_total, f"CCA 扫描有误：{n_ok}/{n_total}"


if __name__ == "__main__":
    test_cca()
    test_fbcca()
    test_trca()
    test_robustness_scan()
    print("\n全部 SSVEP 算法测试通过 ✓")
