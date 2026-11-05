"""ERP 事件相关电位：锁时叠加平均（time-locked averaging）。

原理：刺激诱发的锁时脑电信号（如 P300、N400）单试次信噪比极低，往往淹没在
自发脑电 / 噪声里；把同一类刺激触发的多个试次按事件时间对齐后**平均**，噪声
（相位随机）以 1/√N 衰减，锁时信号（相位恒定）保持不变，从而恢复出 ERP 波形。

本模块只做叠加平均这一件事，**不做分类、不做溯源、不做单被试情绪结论**。事件
时间由上层从 `meta.json` 的 markers（`time_s` 字段）提供；接真实刺激标记时只需
保证标记时间与刺激 onset 锁时对齐即可（这是留给以后接真实刺激设备的接口，见
`analyze_erp.py` 与 README）。
"""
from __future__ import annotations

import numpy as np


def compute_erp(data, fs, event_times, tmin=-0.2, tmax=0.8):
    """对每个事件截取 [tmin, tmax] 的 epoch 做基线校正后叠加平均，返回平均 ERP。

    参数
    ----
    data : array-like
        (n_ch, n) 的滤波后 µV 数据（也可传 1D，会被视作单通道）。
    fs : float
        采样率（Hz）。
    event_times : array-like
        事件时刻列表（秒，相对数据起点）。每个事件对应一次刺激 onset。
    tmin, tmax : float
        epoch 时间窗（秒）。默认 [-0.2, 0.8]，即刺激前 200ms 到刺激后 800ms；
        [tmin, 0] 用作基线（减去该段均值实现基线校正）。

    返回
    ----
    (times, erp)
      times : np.ndarray (n_epoch,)  从 tmin 到 tmax（不含右端点）的采样时刻（秒）。
      erp   : np.ndarray (n_ch, n_epoch)  各通道平均 ERP（µV）。

    越界处理：事件太靠近数据两端导致 epoch 越界的，跳过该事件不报错；
    所有事件都越界（或事件列表为空）时抛 `ValueError` 并给清晰提示。
    """
    data = np.asarray(data, dtype=np.float64)
    if data.ndim == 1:
        data = data[None, :]
    if data.ndim != 2:
        raise ValueError(f"data 必须是 (n_ch, n) 或 (n,)，实际 ndim={data.ndim}")
    n_ch, n = data.shape

    fs = float(fs)
    if fs <= 0:
        raise ValueError(f"采样率非法: {fs}")
    if tmax <= tmin:
        raise ValueError(f"epoch 窗非法: tmin={tmin} >= tmax={tmax}")

    n_epoch = int(round((tmax - tmin) * fs))
    if n_epoch < 2:
        raise ValueError(f"epoch 窗太短（{n_epoch} 样本）")
    times = tmin + np.arange(n_epoch) / fs

    # 基线段 [tmin, 0) 的样本数
    n_base = int(round((0.0 - tmin) * fs))
    n_base = max(0, min(n_base, n_epoch))

    acc = np.zeros((n_ch, n_epoch), dtype=np.float64)
    n_used = 0
    for et in event_times:
        et = float(et)
        start = int(round(et * fs + tmin * fs))
        end = start + n_epoch
        if start < 0 or end > n:
            continue  # epoch 越界，跳过该事件
        epoch = data[:, start:end]
        if n_base > 0:
            epoch = epoch - epoch[:, :n_base].mean(axis=1, keepdims=True)
        acc += epoch
        n_used += 1

    if n_used == 0:
        raise ValueError(
            f"没有可用事件：数据总长 {n / fs:.3f}s（{n} 样本），"
            f"共 {len(event_times)} 个事件，epoch 窗 [{tmin}, {tmax}]s "
            f"全部越界或事件列表为空，无法计算 ERP。请确认标记时间在数据范围内、"
            f"且与数据两端至少留出 {max(-tmin, 0):.1f}s / {max(tmax, 0):.1f}s 余量。")

    return times, acc / n_used
