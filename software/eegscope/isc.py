"""ISC（跨被试同步 inter-subject correlation）骨架。

ISC 是项目主指标（HARNESS「📊 指标」）：N 个观众看同一刺激时，同一导联的
脑电随时间变化应高度同步；同步峰 = 抓人点，谷 = 掉点。

本模块只做相关性计算，**不做分类、不需要被试内 baseline 校准**（ISC 本身是
相对的），天然绕开 HARNESS P1（情绪不做跨被试裸预测）。

接口约定（留给以后接多被试对齐数据）：
  - `signals`：list[np.ndarray (n_ch, n)]，N 个被试、同长度、同通道，
    **已由上层对齐到同一时间轴 / 同一刺激起点**（对齐本身不在本模块做）。
  - 返回 `(t_axis, isc_curve)`：t_axis 为窗口中心时间（秒），isc_curve 为
    对应窗口的 ISC 值。
"""
from __future__ import annotations

import numpy as np


def _pairwise_channel_corr(xi: np.ndarray, xj: np.ndarray) -> np.ndarray:
    """两个被试同一时间窗 (n_ch, n_win) 的逐导联皮尔逊相关，返回 (n_ch,)。

    手动实现 Pearson r（等价 np.corrcoef 的对角外元素），并对常数信号（方差 0）
    返回 0，避免除零 / NaN。
    """
    xi = xi - xi.mean(axis=1, keepdims=True)
    xj = xj - xj.mean(axis=1, keepdims=True)
    num = np.sum(xi * xj, axis=1)
    den = np.sqrt(np.sum(xi * xi, axis=1) * np.sum(xj * xj, axis=1))
    r = np.zeros(xi.shape[0], dtype=np.float64)
    mask = den > 0
    r[mask] = num[mask] / den[mask]
    return np.clip(r, -1.0, 1.0)


def compute_isc(signals, fs, window_sec=5.0, step_sec=1.0):
    """计算时间分辨的跨被试同步 ISC。

    参数
    ----
    signals : list[np.ndarray]
        N 个被试的信号，每个 shape (n_ch, n)，同长度、同通道、已对齐到同一
        时间轴 / 同一刺激。
    fs : float
        采样率（Hz）。
    window_sec : float
        滑动窗长（秒），默认 5.0。
    step_sec : float
        滑动步长（秒），默认 1.0。

    返回
    ----
    (t_axis, isc_curve) : (np.ndarray, np.ndarray)
        t_axis 为各窗中心时间（秒），isc_curve 为对应 ISC 值。
    """
    signals = [np.asarray(s, dtype=np.float64) for s in signals]
    if not signals:
        raise ValueError("signals 不能为空（至少 1 个被试）")

    n_ch, n = signals[0].shape
    for s in signals:
        if s.ndim != 2:
            raise ValueError("每个被试信号必须是 (n_ch, n) 二维数组")
        if s.shape != (n_ch, n):
            raise ValueError("所有被试信号必须同 shape（同导联、同长度）")

    fs = float(fs)
    window = int(round(window_sec * fs))
    step = int(round(step_sec * fs))
    if window < 2:
        raise ValueError(f"窗口太小（{window} 样本），需 ≥ 2 样本才能算相关")
    if step < 1:
        raise ValueError("步长必须 ≥ 1 样本")
    if window > n:
        raise ValueError(f"窗长（{window} 样本）超过数据长度（{n} 样本）")

    # 所有被试对（无向，去重）
    n_subj = len(signals)
    pairs = [(i, j) for i in range(n_subj) for j in range(i + 1, n_subj)]

    t_axis = []
    isc_curve = []
    for start in range(0, n - window + 1, step):
        end = start + window
        vals = []
        for i, j in pairs:
            # 同导联两两相关（每导联单独算，再平均）
            r_per_ch = _pairwise_channel_corr(
                signals[i][:, start:end], signals[j][:, start:end])
            vals.extend(float(v) for v in r_per_ch)
        isc_curve.append(float(np.mean(vals)))
        t_axis.append((start + window / 2.0) / fs)

    return np.asarray(t_axis, dtype=np.float64), np.asarray(isc_curve, dtype=np.float64)
