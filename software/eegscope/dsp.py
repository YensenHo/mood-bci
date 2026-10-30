"""信号链 DSP：带通 0.5–45Hz + 50Hz 陷波 + 去直流/去趋势（在线、带状态）。

在线滤波用带状态的 sosfilt / lfilter（zi 初值随块传递），避免分块处理时
滤波器边界跳变；去趋势用逐通道线性 detrend（同时去除直流偏置）。
"""
from __future__ import annotations

import numpy as np
from scipy.signal import (butter, detrend, iirnotch, lfilter, lfilter_zi,
                          sosfilt, sosfilt_zi)


class SignalChain:
    """带通 + 陷波 + 去趋势的在线信号链（有状态，可跨块连续调用）。"""

    def __init__(self, sample_rate, bandpass_hz=(0.5, 45.0),
                 notch_hz=50.0, notch_q=30.0):
        self.fs = float(sample_rate)
        self.bandpass_hz = tuple(bandpass_hz)
        self.notch_hz = float(notch_hz)

        # 带通（sos 形式，数值稳定）
        lo, hi = self.bandpass_hz
        self.sos_bp = butter(4, [lo, hi], btype="bandpass", fs=self.fs, output="sos")

        # 50 Hz 陷波
        self.b_notch, self.a_notch = iirnotch(self.notch_hz, notch_q, fs=self.fs)

        self._n_ch = None
        self._zi_bp = None
        self._zi_notch = None

    def _ensure_state(self, n_ch: int) -> None:
        if self._n_ch == n_ch and self._zi_bp is not None:
            return
        self._n_ch = n_ch
        ns = self.sos_bp.shape[0]
        z = sosfilt_zi(self.sos_bp)                       # (ns, 2)
        self._zi_bp = np.repeat(z[:, None, :], n_ch, axis=1)  # (ns, n_ch, 2)
        z2 = lfilter_zi(self.b_notch, self.a_notch)       # (2,)
        self._zi_notch = np.tile(z2, (n_ch, 1))           # (n_ch, 2)

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
        self._ensure_state(n_ch)

        # 1) 去直流 + 去趋势（逐通道线性 detrend）
        x = detrend(x, axis=1, type="linear")

        # 2) 带通 0.5–45 Hz（带状态，避免分块边界跳变）
        y, self._zi_bp = sosfilt(self.sos_bp, x, axis=1, zi=self._zi_bp)

        # 3) 50 Hz 陷波（带状态）
        y, self._zi_notch = lfilter(self.b_notch, self.a_notch, y, axis=1,
                                    zi=self._zi_notch)
        return y
