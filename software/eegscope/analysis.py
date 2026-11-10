"""情绪指标：FAA 额叶偏侧化 + 分频带功率（Welch PSD）。

- FAA = ln(P_F4) − ln(P_F3)，作为效价辅助指标（项目约定：正=偏正性情绪，
  负=偏负性）。仅当 F3、F4 两通道同时存在时才计算，缺失时优雅降级
  （faa=None、faa_ok=False、note="需 F3/F4"，不抛异常）。
- 分频带功率 delta/theta/alpha/beta/gamma，作为唤醒度/投入度的辅助显示。
"""
from __future__ import annotations

import numpy as np
from scipy.signal import welch

DEFAULT_BANDS = {
    "delta": (0.5, 4.0),
    "theta": (4.0, 8.0),
    "alpha": (8.0, 13.0),
    "beta": (13.0, 30.0),
    "gamma": (30.0, 45.0),
}


def _trapz(y, x=None, axis=-1):
    """跨 numpy 版本兼容的梯形积分（numpy 2.x 移除了 np.trapz）。"""
    if hasattr(np, "trapezoid"):
        return np.trapezoid(y, x=x, axis=axis)
    return np.trapz(y, x=x, axis=axis)


def welch_psd(sig, fs, nperseg=None):
    """Welch 功率谱估计。sig 为 (n_ch, n) 或 (n,)，返回 (freqs, psd)。"""
    sig = np.asarray(sig, dtype=np.float64)
    if sig.ndim == 1:
        sig = sig[None, :]
    n = sig.shape[1]
    if nperseg is None:
        nperseg = int(min(fs, n))
    nperseg = int(max(16, min(nperseg, n)))
    noverlap = nperseg // 2
    freqs, psd = welch(sig, fs=float(fs), nperseg=nperseg,
                       noverlap=noverlap, axis=1)
    return freqs, psd


def band_power_map(data, fs, band_lo, band_hi):
    """逐通道频带功率：对每通道用 `welch_psd` 求 PSD，再对 [band_lo, band_hi)
    频带做梯形积分，返回每通道频带功率 `np.ndarray (n_ch,)`（µV²）。

    复用 `welch_psd`（不重写 PSD 逻辑），与 `EmotionAnalyzer._integrate` 同一
    积分口径，供脑地形图面板等需要「空间分布」的场景使用。
    """
    data = np.asarray(data, dtype=np.float64)
    if data.ndim == 1:
        data = data[None, :]
    n_ch, n = data.shape
    if n < 2:
        return np.zeros(n_ch, dtype=np.float64)
    freqs, psd = welch_psd(data, fs)
    m = (freqs >= band_lo) & (freqs < band_hi)
    if not m.any():
        return np.zeros(n_ch, dtype=np.float64)
    return _trapz(psd[:, m], freqs[m], axis=1)



class EmotionAnalyzer:
    def __init__(self, sample_rate, channel_names, bands=None,
                 faa_channels=("F3", "F4")):
        self.fs = float(sample_rate)
        self.channel_names = [str(c) for c in channel_names]
        self.bands = bands or dict(DEFAULT_BANDS)
        self.faa_channels = tuple(str(c) for c in faa_channels)

    # ---- 内部工具 ----
    def _index(self, name: str):
        """按名字（忽略大小写）找通道索引，找不到返回 None。"""
        target = name.upper()
        for i, nm in enumerate(self.channel_names):
            if nm.upper() == target:
                return i
        return None

    def _central_indices(self):
        """中央区 C3/C4/Cz/C1/C2/C5/C6（投入度用；排除 CB 小脑、CP 中央顶）。"""
        return [i for i, nm in enumerate(self.channel_names)
                if nm.upper().startswith("C")
                and not nm.upper().startswith(("CB", "CP"))]

    def _parieto_occipital_indices(self):
        """枕顶区 P*/PO*/O*（枕顶区低频功率用）。"""
        return [i for i, nm in enumerate(self.channel_names)
                if nm.upper().startswith(("P", "O"))]

    def _empty_result(self):
        return {
            "faa": None, "faa_ok": False,
            "theta_asymmetry": None,
            "bands": {b: None for b in self.bands},
            "alpha_occipital": None, "beta_alpha_ratio": None,
            "engagement": None, "parieto_occipital_lf": None,
            "valence": None, "arousal": None,
            "note": "",
        }

    @staticmethod
    def _integrate(psd, freqs, lo, hi):
        """把 (n_ch, nfreq) 的 PSD 在 [lo, hi) 频带内积分，返回每通道功率 (n_ch,)。"""
        m = (freqs >= lo) & (freqs < hi)
        if not m.any():
            return np.zeros(psd.shape[0])
        return _trapz(psd[:, m], freqs[m], axis=1)

    # ---- 主入口 ----
    def analyze(self, sig):
        """sig: (n_ch, n) 已滤波 EEG 数据（µV），返回指标 dict。"""
        result = self._empty_result()
        sig = np.asarray(sig, dtype=np.float64)
        if sig.ndim == 1:
            sig = sig[None, :]
        n_ch, n = sig.shape
        if n < self.fs * 0.5:  # 至少 0.5 秒数据才能稳定估计 PSD
            result["note"] = "数据不足"
            return result

        freqs, psd = welch_psd(sig, self.fs)

        # 分频带功率（每通道积分后跨通道平均）
        band_per_ch = {}
        for name, (lo, hi) in self.bands.items():
            per_ch = self._integrate(psd, freqs, lo, hi)
            result["bands"][name] = float(np.mean(per_ch))
            band_per_ch[name] = per_ch
        alpha_per_ch = band_per_ch.get("alpha")
        theta_per_ch = band_per_ch.get("theta")

        # FAA（需要 F3、F4 同时存在）
        i_f3 = self._index(self.faa_channels[0])
        i_f4 = self._index(self.faa_channels[1])
        if i_f3 is not None and i_f4 is not None and alpha_per_ch is not None:
            p_f3 = alpha_per_ch[i_f3]
            p_f4 = alpha_per_ch[i_f4]
            if p_f3 > 0 and p_f4 > 0:
                result["faa"] = float(np.log(p_f4) - np.log(p_f3))
                result["faa_ok"] = True
            else:
                result["note"] = "alpha 功率为 0，无法计算 FAA"
        else:
            result["note"] = "需 F3/F4"

        # 额叶 theta 不对称（效价备选：部分研究用 theta 比 alpha 更敏感，尤其焦虑/回避）
        if i_f3 is not None and i_f4 is not None and theta_per_ch is not None:
            t_f3 = theta_per_ch[i_f3]
            t_f4 = theta_per_ch[i_f4]
            if t_f3 > 0 and t_f4 > 0:
                result["theta_asymmetry"] = float(np.log(t_f4) - np.log(t_f3))

        # 唤醒度/投入度辅助指标
        beta = result["bands"].get("beta") or 0.0
        alpha = result["bands"].get("alpha") or 0.0
        result["beta_alpha_ratio"] = float(beta / (alpha + 1e-12))

        # 投入度 = 中央区 beta/alpha（HARNESS 指标表：中央区，不是全通道平均）
        central = self._central_indices()
        if central and "beta" in band_per_ch and "alpha" in band_per_ch:
            b_c = float(np.mean(band_per_ch["beta"][central]))
            a_c = float(np.mean(band_per_ch["alpha"][central]))
            if a_c > 0:
                result["engagement"] = float(b_c / a_c)

        # 枕顶区低频功率（delta+theta，唤醒度相关，HARNESS 效价/唤醒度指标之一）
        po = self._parieto_occipital_indices()
        if po:
            lf = 0.0
            for bname in ("delta", "theta"):
                if bname in band_per_ch:
                    lf += float(np.mean(band_per_ch[bname][po]))
            result["parieto_occipital_lf"] = float(lf)

        # 枕区 alpha（通道名以 O 开头）
        occ_idx = [i for i, nm in enumerate(self.channel_names) if nm.upper().startswith("O")]
        if occ_idx and alpha_per_ch is not None:
            result["alpha_occipital"] = float(np.mean(alpha_per_ch[occ_idx]))

        # 映射到 [-1, 1] 的效价/唤醒度（供二维指示显示）
        if result["faa"] is not None:
            result["valence"] = float(np.tanh(result["faa"]))
        result["arousal"] = float(np.tanh(result["beta_alpha_ratio"] - 1.0))

        return result


class BaselineCalibrator:
    """被试内 baseline 校准（HARNESS P1 强制）。

    记录中性刺激期（闭眼静坐 / 中性片）的指标均值与方差，之后对辅助指标做
    z-score：``z = (x − μ_baseline) / σ_baseline``。

    只校准辅助指标（FAA、θ不对称、投入度、枕顶低频、唤醒度等）；ISC 本身是
    相对的（两两相关），不需要 calibration。
    """

    CALIBRATABLE = (
        "faa", "theta_asymmetry", "engagement", "parieto_occipital_lf",
        "beta_alpha_ratio", "alpha_occipital", "valence", "arousal",
    )

    def __init__(self):
        self._samples = {k: [] for k in self.CALIBRATABLE}
        self._stats = {}          # k -> (mean, std)
        self.calibrated = False

    @property
    def n_samples(self):
        return len(self._samples.get("faa", []))

    def add_sample(self, metrics):
        """累积一帧指标（基线采集阶段每帧调用）。"""
        for k in self.CALIBRATABLE:
            v = metrics.get(k)
            if v is not None and np.isfinite(v):
                self._samples[k].append(float(v))

    def finish(self):
        """用已累积样本算各指标 mean/std。样本 <3 或 std=0 的指标不校准。"""
        self._stats = {}
        for k, vals in self._samples.items():
            if len(vals) >= 3:
                arr = np.asarray(vals, dtype=np.float64)
                sd = float(arr.std())
                if sd > 0:
                    self._stats[k] = (float(arr.mean()), sd)
        self.calibrated = bool(self._stats)
        return self.calibrated

    def reset(self):
        self._samples = {k: [] for k in self.CALIBRATABLE}
        self._stats = {}
        self.calibrated = False

    def zscore(self, metrics):
        """返回 z-score 后的指标副本（未校准/样本不足的指标保留原值）。"""
        out = dict(metrics)
        for k, (mu, sd) in self._stats.items():
            v = metrics.get(k)
            if v is not None and np.isfinite(v):
                out[k] = float((v - mu) / sd)
        return out
