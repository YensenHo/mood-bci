"""信号链 DSP：带通 0.5–45Hz + 50Hz 陷波 + 25Hz 陷波 + 去直流/去趋势（在线、带状态）。

在线滤波用带状态的 sosfilt / lfilter，状态随块传递；不逐短块去趋势，
避免分块边界产生与输入信号无关的频率。带通的高通部分抑制直流。

25Hz 陷波针对观察到的窄带成分，来源尚未确定；同时会削弱邻近真实频率，
不能用来证明干扰已经消失或恢复真实脑电。
"""
from __future__ import annotations

import numpy as np
from scipy.signal import (butter, iirnotch, lfilter,
                          sosfilt, sosfilt_zi)


class SignalChain:
    """带通 + 陷波 + 去趋势的在线信号链（有状态，可跨块连续调用）。"""

    def __init__(self, sample_rate, bandpass_hz=(0.5, 45.0),
                 notch_hz=(50.0, 25.0), notch_q=30.0):
        self.fs = float(sample_rate)
        self.bandpass_hz = tuple(bandpass_hz)
        if isinstance(notch_hz, (list, tuple)):
            self.notch_hz = tuple(float(f) for f in notch_hz)
        else:
            self.notch_hz = (float(notch_hz),)

        # 带通（sos 形式，数值稳定）
        lo, hi = self.bandpass_hz
        self.sos_bp = butter(4, [lo, hi], btype="bandpass", fs=self.fs, output="sos")

        # 多个陷波（50Hz 工频 + 25Hz 硬件振荡等）
        self.notches = []
        for f0 in self.notch_hz:
            b, a = iirnotch(f0, notch_q, fs=self.fs)
            self.notches.append((b, a))

        self._n_ch = None
        self._zi_bp = None
        self._zi_notch = None

    def _ensure_state(self, x: np.ndarray) -> None:
        n_ch = x.shape[0]
        if self._n_ch == n_ch and self._zi_bp is not None:
            return
        self._n_ch = n_ch
        ns = self.sos_bp.shape[0]
        z = sosfilt_zi(self.sos_bp)                       # (ns, 2)
        self._zi_bp = z[:, None, :] * x[None, :, 0, None]
        self._zi_notch = []
        for b, a in self.notches:
            self._zi_notch.append(np.zeros((n_ch, 2)))

    def reset(self) -> None:
        self._n_ch = None
        self._zi_bp = None
        self._zi_notch = None

    def process(self, x) -> np.ndarray:
        """处理一块数据 x (n_ch, n) 或 (n,)，返回同形状的已滤波数据（µV）。"""
        x = np.asarray(x, dtype=np.float64)
        if x.ndim == 1:
            x = x[None, :]
        n_ch, n = x.shape
        if n == 0:
            return x.copy()
        self._ensure_state(x)

        # 2) 带通 0.5–45 Hz（带状态，避免分块边界跳变）
        y, self._zi_bp = sosfilt(self.sos_bp, x, axis=1, zi=self._zi_bp)

        # 3) 逐个陷波（50Hz、25Hz…带状态）
        for bi, (b, a) in enumerate(self.notches):
            y, self._zi_notch[bi] = lfilter(b, a, y, axis=1,
                                            zi=self._zi_notch[bi])
        return y
