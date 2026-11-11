"""SyntheticSource —— 合成真实感脑电数据源（无需硬件）。

每个通道包含：
  - 8–13 Hz alpha 振荡（幅度随通道不同，枕区 O1/O2/Oz 最强，× alpha_level）
  - 1/f 粉噪声（IIR 成形，带状态 lfilter，跨块连续）
  - 偶发眨眼伪迹（低频慢波，主要出现在额叶 F 通道）
  - 50 Hz 工频 + 150 Hz 谐波
  - 缓漂移（极低频正弦）

数据按真实采样率分块生成（read(n) 逐块出数），内部用全局样本计数保证相位连续，
不会一次性生成完。`set_alpha_level(0~1)` 用于模拟睁眼/闭眼（闭眼 alpha 变强）。
"""
from __future__ import annotations

import numpy as np
from scipy.signal import lfilter

from .base import DataSource

# 1/f（粉噪声）成形滤波器系数（把白噪声滤波成 ~1/f 频谱）
_PINK_B = np.array([0.049922035, -0.095993537, 0.050612699, -0.004408786])
_PINK_A = np.array([1.0, -2.494956002, 2.017265875, -0.522189400])

# 10-20 对称镜像通道对：左右对称的 alpha 节律应共享频率/幅度，
# 这样合成数据在无情绪内容时 FAA 接近 0（诚实的"中性"基线）。
_MIRROR_PAIRS = [
    ("Fp1", "Fp2"), ("F3", "F4"), ("F7", "F8"),
    ("C3", "C4"), ("T7", "T8"), ("P3", "P4"),
    ("P7", "P8"), ("O1", "O2"),
]


def _channel_kind(name: str) -> str:
    """按 10-20 命名推断通道所在脑区，用于合成信号的空间特征。"""
    n = name.upper()
    if n.startswith("O"):
        return "occipital"
    if n.startswith("P"):
        return "parietal"
    if n.startswith("F"):
        return "frontal"
    if n.startswith("T"):
        return "temporal"
    if n.startswith("C"):
        return "central"
    return "other"


class SyntheticSource(DataSource):
    # 各脑区 alpha 相对幅度（枕区最强）
    _ALPHA_BY_KIND = {
        "occipital": 1.0, "parietal": 0.7, "central": 0.6,
        "frontal": 0.5, "temporal": 0.4, "other": 0.6,
    }

    def __init__(self, sample_rate=250.0, channel_names=None, seed=42,
                 alpha_level=0.25, blink_rate=0.25, alpha_uv=30.0,
                 noise_uv=8.0, mains_uv=12.0, harmonic_uv=4.0, drift_uv=20.0):
        self._fs = float(sample_rate)
        self._names = list(channel_names) if channel_names else [f"ch{i + 1}" for i in range(16)]
        self._n_ch = len(self._names)
        self._rng = np.random.default_rng(int(seed))

        self.alpha_level = float(alpha_level)   # 0=睁眼/弱，1=闭眼/强
        self.blink_rate = float(blink_rate)
        self.alpha_uv = float(alpha_uv)
        self.noise_uv = float(noise_uv)
        self.mains_uv = float(mains_uv)
        self.harmonic_uv = float(harmonic_uv)
        self.drift_uv = float(drift_uv)

        # 每通道静态参数（跨块一致）
        self._alpha_freq = self._rng.uniform(8.0, 13.0, self._n_ch)   # Hz
        self._alpha_phase = self._rng.uniform(0.0, 2 * np.pi, self._n_ch)
        self._alpha_amp = np.array([self._alpha_amp_for(n) for n in self._names])
        self._blink_weight = np.array([self._blink_weight_for(n) for n in self._names])
        self._drift_freq = self._rng.uniform(0.08, 0.3, self._n_ch)   # Hz（缓漂移）
        self._drift_phase = self._rng.uniform(0.0, 2 * np.pi, self._n_ch)

        # 对称镜像对共享 alpha 频率/幅度（让中性合成数据的 FAA ≈ 0）
        name_to_idx = {n.upper(): i for i, n in enumerate(self._names)}
        for a, b in _MIRROR_PAIRS:
            ia, ib = name_to_idx.get(a), name_to_idx.get(b)
            if ia is not None and ib is not None:
                self._alpha_freq[ib] = self._alpha_freq[ia]
                self._alpha_amp[ib] = self._alpha_amp[ia]

        # 流式状态
        self._t = 0  # 已生成样本总数（保证跨块相位连续）
        self._pink_zi = np.zeros((self._n_ch, _PINK_A.size - 1))
        self._next_blink = int(self._rng.exponential(1.0 / max(self.blink_rate, 1e-6)) * self._fs)
        self._started = False

    # ---- 通道空间特征 ----
    def _alpha_amp_for(self, name: str) -> float:
        base = self._ALPHA_BY_KIND.get(_channel_kind(name), 0.6)
        return base * self._rng.uniform(0.8, 1.2)

    def _blink_weight_for(self, name: str) -> float:
        return 1.0 if _channel_kind(name) == "frontal" else 0.08

    # ---- DataSource 接口 ----
    @property
    def sample_rate(self) -> float:
        return self._fs

    @property
    def n_channels(self) -> int:
        return self._n_ch

    @property
    def channel_names(self):
        return list(self._names)

    @property
    def is_synthetic(self):
        return True

    def start(self) -> None:
        self._started = True

    def stop(self) -> None:
        self._started = False

    def set_alpha_level(self, level: float) -> None:
        """模拟睁眼/闭眼：0=睁眼（alpha 弱），1=闭眼（alpha 强）。"""
        self.alpha_level = float(np.clip(level, 0.0, 1.0))

    def read(self, n_samples: int) -> np.ndarray:
        n = int(n_samples)
        if n <= 0:
            return np.zeros((self._n_ch, 0), dtype=np.float64)

        t = np.arange(self._t, self._t + n, dtype=np.float64)
        out = np.zeros((self._n_ch, n), dtype=np.float64)

        # 1) alpha 振荡（枕区最强；幅度 × alpha_level）
        out += (self.alpha_uv * self._alpha_amp[:, None] * self.alpha_level *
                np.sin(2 * np.pi * self._alpha_freq[:, None] * t[None, :] / self._fs
                       + self._alpha_phase[:, None]))

        # 2) 1/f 粉噪声（带状态 lfilter，跨块连续）
        white = self._rng.standard_normal((self._n_ch, n))
        pink, self._pink_zi = lfilter(_PINK_B, _PINK_A, white, axis=1, zi=self._pink_zi)
        out += self.noise_uv * pink

        # 3) 50 Hz 工频 + 150 Hz 谐波
        out += self.mains_uv * np.sin(2 * np.pi * 50.0 * t / self._fs)
        out += self.harmonic_uv * np.sin(2 * np.pi * 150.0 * t / self._fs + 0.7)

        # 4) 缓漂移（极低频正弦）
        out += self.drift_uv * np.sin(2 * np.pi * self._drift_freq[:, None] * t[None, :]
                                      + self._drift_phase[:, None])

        # 5) 眨眼伪迹（低频慢波，额叶 F 通道最强）
        while self._next_blink < self._t + n:
            bt = self._next_blink
            if bt >= self._t:
                self._add_blink(out, t, bt)
            self._next_blink += (int(self._rng.exponential(1.0 / max(self.blink_rate, 1e-6))
                                     * self._fs) + int(0.5 * self._fs))

        self._t += n
        return out

    def _add_blink(self, out: np.ndarray, t: np.ndarray, blink_sample: int) -> None:
        """叠加一个高斯慢波（负向偏转），宽度 ~0.12s，额叶强。"""
        sigma = 0.12 * self._fs
        amp = self._rng.uniform(80.0, 200.0)
        dt = t - blink_sample
        wave = amp * np.exp(-0.5 * (dt / sigma) ** 2)
        for ch in range(self._n_ch):
            w = self._blink_weight[ch]
            if w > 1e-3:
                out[ch] += -w * wave
