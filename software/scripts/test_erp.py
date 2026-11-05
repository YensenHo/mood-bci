#!/usr/bin/env python3
"""ERP 合成数据自检（headless，不依赖 GUI）。

构造：fs=250、4 通道、总长 60s；50 个事件（每秒一个，t=1s..50s），每个事件在
300ms 处叠加一个全通道同形状的 +10µV 正偏转（高斯 bump），再叠加较大随机噪声
（单试次噪声 σ=30µV > 信号 10µV，淹没单试次）。

断言：
  [1] compute_erp 叠加平均后在 ~300ms 处恢复明显正峰：峰幅 > 5µV、峰潜伏期在
      250~350ms 内 —— 证明「多次叠加平均压噪声、恢复锁时信号」。
  [2] 单试次（任取一个 epoch）基线噪声幅度显著大于平均 ERP 的基线噪声
      （体现信号平均的价值，理论比值 ≈ √N）。
  [3] 越界事件（放在数据开头/结尾导致 epoch 越界）被正确跳过：不报错，且结果与
      只用有效事件完全一致；全部越界时 compute_erp 抛 ValueError 且信息清晰。

运行（隔离环境）：
    env -i HOME=$HOME PATH=/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin PYTHONPATH= \\
        .venv/bin/python scripts/test_erp.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from eegscope.erp import compute_erp

FS = 250.0
N_CH = 4
TOTAL_SEC = 60.0
N_EVENTS = 50          # 有效事件数
AMP = 10.0             # ERP 峰幅（µV）
LATENCY = 0.300        # 峰潜伏期（s，刺激后）
WIDTH = 0.040          # 高斯 bump 宽度（s）
NOISE_STD = 30.0       # 单试次噪声 σ（µV），> AMP
TMIN, TMAX = -0.2, 0.8


def _add_bump(sig, t_axis, et, amp=AMP, latency=LATENCY, width=WIDTH):
    """在事件时刻 et 后 `latency` 秒处叠加一个正偏转高斯 bump（全通道同形状）。"""
    bump = amp * np.exp(-0.5 * ((t_axis - (et + latency)) / width) ** 2)
    for ch in range(sig.shape[0]):
        sig[ch] += bump


def build_data():
    rng = np.random.default_rng(2024)
    n = int(round(TOTAL_SEC * FS))
    t = np.arange(n) / FS
    sig = np.zeros((N_CH, n), dtype=np.float64)
    # 大随机噪声（单试次噪声 > 信号）
    sig += NOISE_STD * rng.standard_normal((N_CH, n))
    # 50 个事件：t = 1s, 2s, ..., 50s
    event_times = []
    for k in range(1, N_EVENTS + 1):
        et = float(k)  # 每秒一个
        event_times.append(et)
        _add_bump(sig, t, et)
    return sig, event_times


def _baseline_std(x):
    """信号在 [TMIN, 0) 基线段的标准差（µV）。"""
    n_base = int(round((0.0 - TMIN) * FS))
    return float(np.std(x[:n_base]))


def main() -> int:
    sig, event_times = build_data()

    # [1] 正常事件 → 平均 ERP 恢复 300ms 正峰
    times, erp = compute_erp(sig, FS, event_times, tmin=TMIN, tmax=TMAX)
    assert times.shape == (int(round((TMAX - TMIN) * FS)),), f"times 长度错误 {times.shape}"
    assert erp.shape == (N_CH, times.shape[0]), f"erp 形状错误 {erp.shape}"

    # 刺激后 [0, TMAX] 内找 |erp| 最大点（通道平均）
    post = times >= 0.0
    erp_avg = erp.mean(axis=0)
    k = int(np.argmax(np.abs(erp_avg[post])))
    peak_lat_ms = float(times[post][k] * 1000.0)
    peak_amp = float(erp_avg[post][k])

    print(f"[test_erp] fs={FS:g}Hz, 通道={N_CH}, 有效事件={N_EVENTS}, "
          f"epoch=[{TMIN:g},{TMAX:g}]s")
    print(f"[test_erp] [1] 平均 ERP 峰: 潜伏期 {peak_lat_ms:.1f} ms, "
          f"峰幅 {peak_amp:+.3f} µV（期望 ~300ms / ~+{AMP:.0f}µV）")
    assert peak_amp > 5.0, f"平均 ERP 应有明显正峰（>5µV），实际 {peak_amp:.3f}µV"
    assert 250.0 <= peak_lat_ms <= 350.0, f"峰潜伏期应在 250~350ms，实际 {peak_lat_ms:.1f}ms"

    # [2] 单试次噪声 vs 平均 ERP 基线噪声
    # 任取一个单试次 epoch（第一个事件）算其基线噪声
    n_epoch = int(round((TMAX - TMIN) * FS))
    ev_sample = int(round(event_times[0] * FS))
    start = int(round(ev_sample + TMIN * FS))
    single_epoch = sig[0, start:start + n_epoch]  # 通道 0
    single_noise = _baseline_std(single_epoch)
    avg_noise = float(np.std(erp.mean(axis=0)[: int(round((0.0 - TMIN) * FS))]))
    ratio = single_noise / max(avg_noise, 1e-12)

    print(f"[test_erp] [2] 单试次基线噪声 σ = {single_noise:.2f} µV  vs  "
          f"平均 ERP 基线噪声 σ = {avg_noise:.3f} µV  （比值 {ratio:.1f}×，"
          f"理论 ≈√{N_EVENTS}≈{np.sqrt(N_EVENTS):.1f}×）")
    assert single_noise > 3.0 * avg_noise, \
        f"单试次噪声应显著大于平均 ERP 基线噪声，实际比值 {ratio:.1f}×"

    # [3] 越界事件被跳过：加边界事件后结果与只用有效事件一致，且不报错
    boundary_events = event_times + [0.05, TOTAL_SEC - 0.05]  # epoch 会越界
    _, erp_b = compute_erp(sig, FS, boundary_events, tmin=TMIN, tmax=TMAX)
    assert np.allclose(erp, erp_b, atol=1e-9), "越界事件应被跳过且不影响平均"
    print(f"[test_erp] [3] 越界事件(±边界)被正确跳过，平均 ERP 与纯有效事件一致")

    # 全部越界 → 抛 ValueError 且信息清晰
    try:
        compute_erp(sig, FS, [0.05, TOTAL_SEC - 0.05], tmin=TMIN, tmax=TMAX)
        raise AssertionError("全部越界时应抛 ValueError")
    except ValueError as e:
        msg = str(e)
        assert "事件" in msg, f"错误信息应提及事件，实际: {msg!r}"
        print(f"[test_erp] [4] 全部越界 → ValueError（信息清晰）: {msg[:80]}...")

    print("\n✅ test_erp 全部通过（叠加平均恢复锁时信号 + 越界跳过）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
