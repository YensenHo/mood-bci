"""DataSource 抽象基类 —— 真实 ADS1299 串口驱动的接入点。"""
from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np


class DataSource(ABC):
    """所有数据源（合成 / 真实硬件）的公共接口。

    真实 ADS1299 驱动只需继承本类，实现 `start` / `stop` / `read` 以及
    `sample_rate`、`n_channels` 两个属性，即可接入整套可视化 + 分析管线。

    约定：
      - `read()` 返回单位为 µV 的 float 数组，shape = (n_channels, n_samples)。
      - 按真实采样率出数据，阻塞式等待（无数据时可阻塞）。
    """

    @property
    @abstractmethod
    def sample_rate(self) -> float:
        """采样率（Hz）。"""

    @property
    @abstractmethod
    def n_channels(self) -> int:
        """通道数。"""

    @property
    def channel_names(self):
        """默认返回 ch1..chN；有名字的数据源覆盖此属性。"""
        return [f"ch{i + 1}" for i in range(self.n_channels)]

    @property
    def is_synthetic(self) -> bool:
        """是否合成数据源（决定 GUI 是否显示 alpha 旋钮/模拟闭眼控件）。"""
        return False

    @property
    def full_scale_uv(self) -> float:
        """满量程（µV），用于信号质量/饱和判定的参考。合成源默认 ±200µV，真实板覆盖。"""
        return 200.0

    @abstractmethod
    def start(self) -> None:
        """打开设备 / 初始化数据流。"""

    @abstractmethod
    def stop(self) -> None:
        """关闭设备 / 释放资源。"""

    @abstractmethod
    def read(self, n_samples: int) -> np.ndarray:
        """读取 n_samples 个样本，返回 (n_channels, n_samples) 的 float 数组（µV）。"""

    # 上下文管理器支持
    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()
        return False
