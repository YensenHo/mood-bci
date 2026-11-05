#!/usr/bin/env python3
"""合成数据验证 compute_isc：前段高同步、后段各自独立噪声，ISC 高/低应可区分。

构造：2 个被试、8 通道、fs=250、总长 30s。
  - 前 15s：两被试共享同一强相关信号（10Hz 正弦 + 每通道/每被试不同小噪声）。
  - 后 15s：两被试各自独立高斯噪声。
断言：前段 ISC 均值明显高于后段（前段 > 0.5、后段 < 0.3、且差 > 0.5）。

headless，不依赖 GUI。运行（隔离环境）：
    env -i HOME=$HOME PATH=/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin PYTHONPATH= \
        .venv/bin/python scripts/test_isc.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from eegscope.isc import compute_isc


def main() -> int:
    fs = 250.0
    n_ch = 8
    total = int(30 * fs)
    half = total // 2  # 15s 处切分
    rng = np.random.default_rng(7)

    t = np.arange(total) / fs
    # 共享信号：10Hz 正弦为主 + 少量 20Hz 谐波
    shared = (np.sin(2 * np.pi * 10.0 * t)
              + 0.4 * np.sin(2 * np.pi * 20.0 * t))

    s1 = np.zeros((n_ch, total), dtype=np.float64)
    s2 = np.zeros((n_ch, total), dtype=np.float64)

    # 前 15s：两被试共享信号 + 各自小噪声
    for ch in range(n_ch):
        n1 = rng.standard_normal(half) * 0.15
        n2 = rng.standard_normal(half) * 0.15
        s1[ch, :half] = shared[:half] + n1
        s2[ch, :half] = shared[:half] + n2

    # 后 15s：各自独立噪声
    s1[:, half:] = rng.standard_normal((n_ch, total - half))
    s2[:, half:] = rng.standard_normal((n_ch, total - half))

    t_axis, isc = compute_isc([s1, s2], fs, window_sec=5.0, step_sec=1.0)

    # 用窗口中心时间切分前/后段（窗口可能跨边界，但前段均值仍应远高于后段）
    front = isc[t_axis < 15.0]
    back = isc[t_axis >= 15.0]
    assert len(front) > 0 and len(back) > 0, "前/后段都应有窗口"

    front_mean = float(np.mean(front))
    back_mean = float(np.mean(back))

    print(f"[test_isc] fs={fs} Hz, 通道={n_ch}, 总长=30s, 窗=5s, 步=1s")
    print(f"[test_isc] 窗口数: 前段={len(front)} 后段={len(back)}")
    print(f"[test_isc] 前段(共享信号) ISC 均值 = {front_mean:.4f}")
    print(f"[test_isc] 后段(独立噪声) ISC 均值 = {back_mean:.4f}")
    print(f"[test_isc] 前-后差 = {front_mean - back_mean:.4f}")

    assert front_mean > 0.5, f"前段 ISC 应 > 0.5，实际 {front_mean:.4f}"
    assert back_mean < 0.3, f"后段 ISC 应 < 0.3，实际 {back_mean:.4f}"
    assert front_mean - back_mean > 0.5, \
        f"前段应显著高于后段（差应 > 0.5），实际差 {front_mean - back_mean:.4f}"

    # 额外校验：返回类型 / 长度一致
    assert t_axis.shape == isc.shape, "t_axis 与 isc_curve 长度应一致"
    assert np.isfinite(isc).all(), "ISC 曲线不应含 NaN/Inf"
    assert -1.0 <= isc.min() and isc.max() <= 1.0, "ISC 应在 [-1, 1]"

    print("\n✅ test_isc 全部通过（合成数据 ISC 高/低可区分）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
