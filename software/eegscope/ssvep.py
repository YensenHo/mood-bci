"""SSVEP 解码 —— 高小榕（Xiaorong Gao）团队三大公开算法：CCA / FBCCA / TRCA。

论文锚点（高小榕老师为共同作者，IEEE/JNE 公开算法）：
  - CCA   典型相关分析
      G. Bin, X. Gao, et al., "An online multi-channel SSVEP-based brain-computer
      interface using a canonical correlation analysis method", J. Neural Eng., 2009.
  - FBCCA 滤波组典型相关分析
      X. Chen, Y. Wang, S. Gao, T.-P. Jung, X. Gao, "Filter bank canonical
      correlation analysis for implementing a high-speed SSVEP-based BCI",
      J. Neural Eng., 2015.
  - TRCA  任务相关成分分析
      M. Nakanishi, Y. Wang, X. Chen, Y.-T. Wang, X. Gao, T.-P. Jung,
      "Enhancing Detection of SSVEPs for a High-Speed Brain Speller Using
      Task-Related Component Analysis", IEEE Trans. Biomed. Eng., 2017.

接口约定（与 eegscope 现有分析模块 isc.py/erp.py 一致）：
  - 输入 EEG 为 (n_ch, n) 的 float 数组，单位 µV，任意通道数（16ch / 64ch 均可）。
  - CCA / FBCCA 无训练，可直接对实时数据做频率识别。
  - TRCA 需先「校准」：每个刺激频率收集若干次 trial，训练空间滤波器 + 模板，
    再用模型识别。

诚实边界：
  - SSVEP 信号主要在枕区（O1/O2/Oz 等），本模块用「全部通道」做 CCA 空间滤波
    （CCA 会自动学出最优通道组合），不硬编码通道名；若想只用枕区，上层自行切片。
  - SSVEP 需要受试者注视闪烁目标，无法在自然观影场景下同时做情绪测量（ISC）。
"""
from __future__ import annotations

import numpy as np
from scipy.linalg import eigh, qr, svd
from scipy.signal import butter, filtfilt


# --------------------------------------------------------------------------
# 参考信号
# --------------------------------------------------------------------------
def ssvep_reference(freqs, fs, n_samples, n_harmonics=3):
    """生成各刺激频率的正弦/余弦参考信号。

    参数
    ----
    freqs : 刺激频率列表（Hz），如 [8.0, 9.0, 10.0, ...]
    fs : 采样率（Hz）
    n_samples : 数据窗样本数
    n_harmonics : 谐波次数（标准取 3）

    返回
    ----
    np.ndarray，shape (n_freqs, 2*n_harmonics, n_samples)：
        第 i 个频率的参考 = [sin(1f), cos(1f), sin(2f), cos(2f), ..., sin(Nh·f), cos(Nh·f)]
    """
    freqs = np.asarray(freqs, dtype=np.float64)
    t = np.arange(n_samples, dtype=np.float64) / float(fs)
    refs = []
    for f in freqs:
        comps = []
        for h in range(1, int(n_harmonics) + 1):
            phase = 2.0 * np.pi * h * float(f) * t
            comps.append(np.sin(phase))
            comps.append(np.cos(phase))
        refs.append(np.stack(comps, axis=0))
    return np.stack(refs, axis=0)  # (n_freqs, 2*Nh, n_samples)


def _cca_corr(X, Y):
    """两矩阵的最大典型相关系数（标准 SVD 实现）。

    X : (n_ch, n)，Y : (n_ref, n)。先各自去均值，QR 正交化后对交叉积做 SVD，
    最大奇异值即最大典型相关系数 ρ。
    """
    X = np.asarray(X, dtype=np.float64)
    Y = np.asarray(Y, dtype=np.float64)
    X = X - X.mean(axis=1, keepdims=True)
    Y = Y - Y.mean(axis=1, keepdims=True)
    Qx, _ = qr(X.T, mode="economic")
    Qy, _ = qr(Y.T, mode="economic")
    # 防秩亏：截断到较小维
    _, s, _ = svd(Qx.T @ Qy, full_matrices=False)
    return float(s[0]) if s.size else 0.0


# --------------------------------------------------------------------------
# CCA（无训练）
# --------------------------------------------------------------------------
def cca_classify(data, fs, freqs, n_harmonics=3):
    """无训练 CCA：对每个频率的参考信号求典型相关，取相关系数最大的频率为目标。

    返回 (corrs, target_idx)：
        corrs —— 各频率的相关系数（长度 = len(freqs)）
        target_idx —— 识别出的频率下标
    """
    data = np.asarray(data, dtype=np.float64)
    n_samples = data.shape[1]
    refs = ssvep_reference(freqs, fs, n_samples, n_harmonics)
    corrs = np.array([_cca_corr(data, refs[i]) for i in range(len(freqs))])
    return corrs, int(np.argmax(corrs))


# --------------------------------------------------------------------------
# FBCCA（无训练）
# --------------------------------------------------------------------------
def _filterbank(data, fs, n_subbands, freqs):
    """FBCCA 滤波器组：把数据分解成 n_subbands 个子带。

    第 k 个子带覆盖第 k 次谐波的频率区间 [k·f_min, min(k·f_max, fs/2-1)]，
    用 4 阶 Butterworth 带通 + 零相位 filtfilt。
    """
    f_min, f_max = float(min(freqs)), float(max(freqs))
    nyq = fs / 2.0
    subbands = []
    for k in range(1, int(n_subbands) + 1):
        lo = k * f_min
        hi = min(k * f_max, nyq - 1.0)
        if hi <= lo:
            continue
        b, a = butter(4, [lo / nyq, hi / nyq], btype="bandpass")
        subbands.append(filtfilt(b, a, data, axis=1))
    return subbands


def fbcca_classify(data, fs, freqs, n_harmonics=3, n_subbands=5):
    """无训练 FBCCA：滤波器组分解 + 各子带 CCA + 平方加权融合。

    权重 w_k = k^(-1.25) + 0.25（Chen et al. 2015 经验权重），
    融合方式 ρ = Σ_k w_k · ρ_k²。

    返回 (corrs, target_idx)，corrs 为融合后各频率的得分。
    """
    data = np.asarray(data, dtype=np.float64)
    n_samples = data.shape[1]
    subbands = _filterbank(data, fs, n_subbands, freqs)
    n_band = len(subbands)
    n_freqs = len(freqs)
    rho = np.zeros((n_band, n_freqs), dtype=np.float64)
    for k, sub in enumerate(subbands):
        refs = ssvep_reference(freqs, fs, n_samples, n_harmonics)
        for i in range(n_freqs):
            rho[k, i] = _cca_corr(sub, refs[i])
    weights = np.array([(k + 1) ** (-1.25) + 0.25 for k in range(n_band)])
    corrs = np.sum(weights[:, None] * (rho ** 2), axis=0)
    return corrs, int(np.argmax(corrs))


# --------------------------------------------------------------------------
# TRCA（需训练）
# --------------------------------------------------------------------------
def trca_train(trials, freqs):
    """训练 TRCA 模型。

    参数
    ----
    trials : list，长度 = len(freqs)。trials[i] 是第 i 个刺激频率的 trial 列表，
        每个 trial 为 (n_ch, n) 的 EEG（同长度、同通道）。每个频率至少 2 个 trial。
    freqs : 刺激频率列表

    返回
    ----
    model : dict {freq: {"w": 空间滤波器 (n_ch,), "template": 模板 (n,)}}
    """
    model = {}
    for fi, f in enumerate(freqs):
        X = np.stack([np.asarray(tr, dtype=np.float64) for tr in trials[fi]], axis=0)
        n_trial, n_ch, n_samples = X.shape
        if n_trial < 2:
            raise ValueError(f"频率 {f} 至少需要 2 个 trial 才能训 TRCA（当前 {n_trial}）")
        # 逐 trial 去均值
        X = X - X.mean(axis=2, keepdims=True)

        # 跨 trial 协方差 S = Σ_{i≠j} X_i X_j^T；trial 内协方差 Q = Σ_i X_i X_i^T
        S = np.zeros((n_ch, n_ch), dtype=np.float64)
        Q = np.zeros((n_ch, n_ch), dtype=np.float64)
        for i in range(n_trial):
            Q += X[i] @ X[i].T
            for j in range(n_trial):
                if i != j:
                    S += X[i] @ X[j].T

        # 广义特征值 S w = λ Q w，取最大 λ 的特征向量
        Q = Q + 1e-8 * np.eye(n_ch)
        eigvals, eigvecs = eigh(S, Q)
        w = eigvecs[:, -1]

        # 模板 = 各 trial 经空间滤波后的平均
        template = np.mean([w @ X[i] for i in range(n_trial)], axis=0)
        model[float(f)] = {"w": w, "template": template}
    return model


def trca_classify(data, freqs, model):
    """用训练好的 TRCA 模型识别测试数据。

    返回 (corrs, target_idx)，corrs 为各频率的模板皮尔逊相关。
    """
    data = np.asarray(data, dtype=np.float64)
    data = data - data.mean(axis=1, keepdims=True)
    corrs = []
    for f in freqs:
        w = model[float(f)]["w"]
        template = model[float(f)]["template"]
        y = w @ data  # (n,)
        # 皮尔逊相关，防常数信号除零
        denom = np.std(y) * np.std(template)
        corr = float(np.corrcoef(y, template)[0, 1]) if denom > 0 else 0.0
        corrs.append(corr)
    corrs = np.nan_to_num(np.asarray(corrs), nan=0.0)
    return corrs, int(np.argmax(corrs))
