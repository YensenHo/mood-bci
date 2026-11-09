"""跨被试同步 ISC（inter-subject correlation）—— 神经电影学主指标。

神经电影学（Hasson 2004/2008）的核心：N 个观众看同一部片，算他们同一导联
脑电时间序列的 Pearson 相关。同步高 = 片子这一刻"抓住"了所有人的大脑
（抓人点/共鸣点）；同步低 = 掉点。它测的是"群体共鸣/投入度"，不是个体情绪。

用法（监控端）：
    isc = IscAnalyzer(fs=250, n_channels=8, window_seconds=5.0)
    for participant, chunk in 收到各参与者数据块:
        isc.add(participant, chunk)
    per_ch, summary, n_pairs = isc.compute()   # per_ch=(n_ch,), summary=跨通道均值

对齐假设：所有参与者用同一硬件（同 fs）、同一时间点开始播放/采集，故按"最新
样本对齐"（各自缓冲的末尾对齐）。若参与者起播时间差异大，需引入片段时间码做
对齐（后续版本）。
"""
from __future__ import annotations

from itertools import combinations

import numpy as np


class IscAnalyzer:
    def __init__(self, fs: float, n_channels: int,
                 window_seconds: float = 5.0, channel_names: list | None = None):
        self.fs = float(fs)
        self.n_channels = int(n_channels)
        self.window = max(1, int(self.fs * window_seconds))
        self.channel_names = [str(c) for c in (channel_names or [])] or \
            [f"ch{i + 1}" for i in range(self.n_channels)]
        self._bufs: dict[str, np.ndarray] = {}

    @property
    def participants(self) -> list:
        return list(self._bufs.keys())

    def add(self, participant: str, chunk) -> None:
        """追加一块 (n_ch, n) µV 数据到该参与者的滚动缓冲（超窗裁最旧）。"""
        chunk = np.asarray(chunk, dtype=np.float64)
        if chunk.ndim == 1:
            chunk = chunk[None, :]
        if chunk.shape[0] != self.n_channels or chunk.shape[1] == 0:
            return
        buf = self._bufs.get(participant)
        if buf is None or buf.shape[1] == 0:
            buf = chunk.copy()
        else:
            buf = np.concatenate([buf, chunk], axis=1)
        if buf.shape[1] > self.window:
            buf = buf[:, -self.window:]
        self._bufs[participant] = buf

    def compute(self):
        """算每个通道的成对平均 ISC。

        返回 (isc_per_ch, summary, n_pairs)：
          isc_per_ch: (n_ch,) 每个通道的成对平均 Pearson 相关（-1~1）
          summary: 跨通道平均 ISC（单个"同步度"数字）
          n_pairs: 参与的两两配对数
        少于 2 个参与者或数据不足 0.5s 时返回 (None, None, 0)。
        """
        parts = list(self._bufs.keys())
        if len(parts) < 2:
            return None, None, 0
        min_len = min(self._bufs[p].shape[1] for p in parts)
        if min_len < self.fs * 0.5:
            return None, None, 0
        # 按"最新样本"对齐（末尾对齐），因为各参与者都在实时看同一进度
        signals = [self._bufs[p][:, -min_len:] for p in parts]
        pairs = list(combinations(range(len(parts)), 2))

        isc_ch = np.zeros(self.n_channels)
        for c in range(self.n_channels):
            rs = []
            for a, b in pairs:
                x = signals[a][c] - signals[a][c].mean()
                y = signals[b][c] - signals[b][c].mean()
                denom = np.sqrt(float((x * x).sum()) * float((y * y).sum()))
                rs.append(0.0 if denom < 1e-12 else float((x * y).sum() / denom))
            isc_ch[c] = float(np.mean(rs))
        summary = float(np.mean(isc_ch))
        return isc_ch, summary, len(pairs)

    def clear(self) -> None:
        self._bufs.clear()
