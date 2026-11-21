"""EEG 伪迹检测（眨眼 / 肌电 / 运动 / 电极跳变），实时可用。

只依赖 numpy + scipy.signal（复用 analysis.welch_psd）。在**滤波后信号**
（带通 0.5–45Hz + 50/25Hz 陷波）上检测，输出逐通道伪迹类型 + 全局判定，
供情绪指标门控、面板提示与离线报告共用，避免三处各写一份。

检测项（阈值均为启发式默认，应在干净真机数据上标定）：
- 振幅：任一有效通道 |x| 超过 ``amp_thresh_uv``。额区(Fp/AF/F)超阈 → 眨眼；
  其余通道超阈 → 运动/大干扰。
- 尖峰/电极跳变：相邻样本差分 |Δx| 超过 ``slope_thresh_uv``（µV/样本）。
- 肌电：beta+gamma(20–45Hz) 功率占「theta+alpha+beta+gamma」比例 > ``emg_frac``
  （排除 delta，避免 1/f 大 delta 稀释比例导致漏检）。
- 运动/低频漂移：delta(0.5–4Hz) 功率占比 > ``motion_frac``（默认 0.85，高阈值
  避免把正常 1/f 静息谱误判为运动）。

诚实边界：本模块只「检测 + 标记」，不改写信号（不做 ICA/插值）。伪迹剔除在
下游 = 门控（暂停/标记指标），不是伪造干净脑电。
"""
from __future__ import annotations

import numpy as np

from .analysis import welch_psd


def _match_indices(channel_names, prefixes):
    up = [str(nm).upper() for nm in channel_names]
    return [i for i, nm in enumerate(up) if nm.startswith(prefixes)]


def detect_artifacts(sig, fs, channel_names=None, valid=None, *,
                     amp_thresh_uv=100.0, slope_thresh_uv=150.0,
                     emg_band=(20.0, 45.0), emg_frac=0.6, emg_min_power_uv2=200.0,
                     motion_band=(0.5, 4.0), motion_frac=0.85):
    """检测一个分析窗 (n_ch, n) 的伪迹。

    参数
    ----
    sig : (n_ch, n) 或 (n,) 已滤波 EEG 数据（µV）。
    fs : 采样率 Hz。
    channel_names : 通道名列表（长度 = n_ch）。
    valid : 每通道有效性布尔掩码（无效通道不参与检测）。

    返回
    ----
    dict：
      per_channel : list[str]  每通道伪迹类型（空串=干净，多类用逗号分隔）。
      n_artifact_channels : int  有伪迹的通道数。
      blink / emg / motion / pop : bool  各类伪迹是否检出。
      clean : bool  窗口整体是否干净（无任何伪迹）。
      reasons : list[str]  人类可读原因。
    """
    sig = np.asarray(sig, dtype=np.float64)
    if sig.ndim == 1:
        sig = sig[None, :]
    n_ch, n = sig.shape
    if channel_names is None:
        channel_names = [f"ch{i + 1}" for i in range(n_ch)]
    if valid is None:
        valid = np.ones(n_ch, dtype=bool)
    else:
        valid = np.asarray(valid, dtype=bool)

    frontal = _match_indices(channel_names, ("FP", "AF", "F"))

    per_channel = [[] for _ in range(n_ch)]

    # 1) 振幅（眨眼 / 运动 / 大干扰）
    amp = np.max(np.abs(sig), axis=1) if n else np.zeros(n_ch)
    amp_bad = (amp > amp_thresh_uv) & valid

    # 2) 尖峰 / 电极跳变（逐样本差分）
    slope = np.max(np.abs(np.diff(sig, axis=1)), axis=1) if n > 1 else np.zeros(n_ch)
    slope_bad = (slope > slope_thresh_uv) & valid

    # 3) 频带功率（肌电=高频占比；运动=低频占比）
    emg_hit = np.zeros(n_ch, dtype=bool)
    motion_hit = np.zeros(n_ch, dtype=bool)
    if n >= fs * 0.5:
        freqs, psd = welch_psd(sig, fs)
        m_emg = (freqs >= emg_band[0]) & (freqs < emg_band[1])
        m_mot = (freqs >= motion_band[0]) & (freqs < motion_band[1])
        m_low = (freqs >= 4.0) & (freqs < 45.0)   # theta+alpha+beta+gamma（排除 delta）
        for i in range(n_ch):
            if not valid[i]:
                continue
            p = psd[i]
            total = p.sum()
            if total <= 0:
                continue
            # 肌电：高频占「非 delta」功率比例 > emg_frac，且高频绝对功率超下限
            # （避免「低频很弱、但高频绝对值也不高」的假阳性，如合成源的高 beta）
            low_pow = p[m_low].sum()
            emg_pow = p[m_emg].sum()
            if low_pow > 0 and emg_pow > emg_min_power_uv2:
                if emg_pow / low_pow > emg_frac:
                    emg_hit[i] = True
            # 运动：delta 占全频带比例
            if p[m_mot].sum() / total > motion_frac:
                motion_hit[i] = True

    # 全局大振幅(多通道同时超阈)=运动；仅额区少数通道超阈=眨眼
    n_amp_bad = int(amp_bad.sum())
    global_amp = n_amp_bad >= max(2, int(round(0.3 * n_ch)))
    for i in range(n_ch):
        if not valid[i]:
            continue
        if amp_bad[i]:
            if global_amp:
                per_channel[i].append("motion")
            else:
                per_channel[i].append("blink" if i in frontal else "motion")
        if slope_bad[i]:
            per_channel[i].append("pop")
        if emg_hit[i]:
            per_channel[i].append("emg")
        if motion_hit[i]:
            per_channel[i].append("motion")

    blink = any("blink" in pc for pc in per_channel)
    emg = any("emg" in pc for pc in per_channel)
    motion = any("motion" in pc for pc in per_channel)
    pop = any("pop" in pc for pc in per_channel)

    reasons = []
    if blink:
        reasons.append("检测到眨眼/额区大振幅伪迹")
    if emg:
        reasons.append("检测到肌电（高频功率占比过高）")
    if motion:
        reasons.append("检测到运动/低频漂移伪迹")
    if pop:
        reasons.append("检测到尖峰/电极跳变")

    return {
        "per_channel": [",".join(pc) for pc in per_channel],
        "n_artifact_channels": int(sum(bool(pc) for pc in per_channel)),
        "blink": blink, "emg": emg, "motion": motion, "pop": pop,
        "clean": not (blink or emg or motion or pop),
        "reasons": reasons,
    }
