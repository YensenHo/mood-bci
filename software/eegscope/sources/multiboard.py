"""MultiBoardSource —— 多块 OpenBCI 板合并成一个数据源（4 × 16ch = 64ch）。

背景：RT-BCI 每块 16ch 板是独立的一个 USB 串口（自带 STM32），4 块板 = 4 个串口，
不是插上就自动 64 通道。USB 集线器只做物理集中，数据合并必须在这里做：
本类把多块 OpenBCISource 包起来，对外表现为一个 64 通道数据源。

拼接顺序：read() 时按板顺序拼接 —— 第 1 块板 = 通道 0~15，第 2 块 = 16~31，
第 3 块 = 32~47，第 4 块 = 48~63。channel_names 必须按同样的顺序给，
即「你物理上怎么把 64 个电极接到 4 块板的 64 个通道上，channels 列表就怎么写」。

诚实边界：各板是独立 STM32、独立采样时钟，无硬件同步信号，
跨板时间对齐靠「等样本数」对齐；长期漂移（ppm 级晶振误差）是硬件限制。
对 ISC / 情绪这种秒级时间尺度的分析影响很小，对 ERP 这种毫秒级锁时分析需注意。
"""
from __future__ import annotations

import numpy as np

from .base import DataSource
from .openbci import OpenBCISource


class MultiBoardSource(DataSource):
    def __init__(self, ports, baudrate=115200, n_channels_per_board=16,
                 gain=24.0, scale_uv=None, channel_names=None, gain_code=None):
        ports = list(ports)
        if not ports:
            raise ValueError("MultiBoardSource 至少需要 1 个串口")
        self._ports = ports
        self._n_ch_per_board = int(n_channels_per_board)
        self._boards = [
            OpenBCISource(port=p, baudrate=baudrate,
                          n_channels=self._n_ch_per_board,
                          gain=gain, scale_uv=scale_uv, gain_code=gain_code)
            for p in self._ports
        ]
        self._n_ch = self._n_ch_per_board * len(self._boards)

        names = list(channel_names) if channel_names else None
        self._names = (names if names and len(names) == self._n_ch
                       else [f"ch{i + 1}" for i in range(self._n_ch)])

    # ---- DataSource 接口 ----
    @property
    def sample_rate(self) -> float:
        return self._boards[0].sample_rate

    @property
    def n_channels(self) -> int:
        return self._n_ch

    @property
    def channel_names(self):
        return list(self._names)

    @property
    def is_synthetic(self) -> bool:
        return False

    @property
    def alpha_level(self) -> float:
        return 0.0

    def set_alpha_level(self, level: float) -> None:
        pass

    def start(self) -> None:
        for b in self._boards:
            b.start()

    def stop(self) -> None:
        for b in self._boards:
            b.stop()

    def read(self, n_samples: int) -> np.ndarray:
        n = int(n_samples)
        if n <= 0:
            return np.zeros((self._n_ch, 0), dtype=np.float64)
        # 逐板读，按最小样本数对齐（各板独立时钟，漂移/掉线时样本数可能不等）
        chunks = [b.read(n) for b in self._boards]
        min_len = min(c.shape[1] for c in chunks)
        if min_len == 0:
            return np.zeros((self._n_ch, 1), dtype=np.float64)  # 全部掉线兜底
        return np.concatenate([c[:, :min_len] for c in chunks], axis=0)

    def board_ports(self):
        """返回各板串口（调试/日志用）。"""
        return list(self._ports)
