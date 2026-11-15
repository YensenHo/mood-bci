"""眼动仪数据源适配层（不含具体设备协议）。

设计原则（对应「先通过模拟器验证、不假定具体设备协议」）：

- 只定义一个**通用适配接口**（设备时间戳 + 时钟来源 + 归一化坐标 + 有效性），
  未来接入具体眼动仪时，写一个 ``XXXEyeTracker`` 继承 ``EyeTrackerSource``，
  把设备私有协议转成这个通用接口即可，采集/录制/同步管线零改动。

- 提供一个 ``SimulatedEyeTracker`` 模拟器，可注入：
  固定时间偏移、时钟漂移、丢帧、无效段（眨眼/超范围），
  用来在没有真眼动仪的情况下，先验证整条「记录 + 对齐 + 校验」链路。

坐标约定：``x/y`` 为**屏幕归一化坐标 [0,1]**（左上角原点），
设备自带时间戳 ``device_ts`` 的单位与 ``clock_source`` 由设备决定，
本层只做原始透传，不做任何假设。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
import time as _time

import numpy as np


@dataclass
class EyeSample:
    """一个眼动样本。device_ts 为设备自带时间戳（原始，不透传单位），
    clock_source 标识其时钟来源（如 "device_1000Hz" / "host_receive"）。
    valid=False 表示该样本不可用（眨眼/丢失/超范围）。"""
    device_ts: float | None
    clock_source: str
    x: float | None
    y: float | None
    valid: bool


class EyeTrackerSource(ABC):
    """眼动仪适配器抽象基类。真实设备子类实现 start/stop/read。"""

    @property
    @abstractmethod
    def sample_rate(self) -> float:
        """眼动采样率（Hz）。"""

    @abstractmethod
    def start(self) -> None:
        """打开/初始化眼动仪。"""

    @abstractmethod
    def stop(self) -> None:
        """关闭眼动仪。"""

    @abstractmethod
    def read(self) -> list[EyeSample]:
        """读取自上次调用以来累积的眼动样本（可能为空）。"""

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()
        return False


class SimulatedEyeTracker(EyeTrackerSource):
    """模拟眼动仪：平滑注视轨迹 + 可注入的偏移/漂移/丢帧/无效段。

    用于在无真眼动仪时端到端验证同步记录与校验逻辑。

    参数：
      sample_rate    采样率 Hz
      offset_s       固定时间偏移（模拟眼动仪与电脑时钟的固定差）
      drift_ppm      时钟漂移（ppm；>0 = 眼动仪比电脑快）
      loss_rate      丢帧概率（0~1）
      invalid_rate   无效段（眨眼）概率（0~1）
      seed           随机种子（可复现）
    """

    def __init__(self, sample_rate: float = 120.0, *,
                 offset_s: float = 0.0, drift_ppm: float = 0.0,
                 loss_rate: float = 0.0, invalid_rate: float = 0.0,
                 seed: int = 0) -> None:
        self._fs = float(sample_rate)
        self._offset_s = float(offset_s)
        self._drift = float(drift_ppm) * 1e-6     # ppm → 比例
        self._loss = float(loss_rate)
        self._invalid = float(invalid_rate)
        self._rng = np.random.default_rng(int(seed))

        self._started = False
        self._n = 0              # 已生成样本数
        self._t0 = None          # start 时的 host 单调时间

    @property
    def sample_rate(self) -> float:
        return self._fs

    def start(self) -> None:
        self._started = True
        self._n = 0
        self._t0 = _time.monotonic()

    def stop(self) -> None:
        self._started = False

    def read(self) -> list[EyeSample]:
        """返回自上次 read 以来「设备时钟已到期」的样本。

        设备时钟比电脑快/慢 (1+drift)：host 走过 H 秒 → 设备走过 H*(1+drift) 秒，
        应产出 H*(1+drift)*fs 个样本。device_ts 按设备时钟走（每样本 +1/fs）+ 固定偏移。
        """
        if not self._started or self._t0 is None:
            return []
        host_elapsed = _time.monotonic() - self._t0
        device_elapsed = host_elapsed * (1.0 + self._drift)
        target = int(device_elapsed * self._fs)
        out: list[EyeSample] = []
        while self._n < target:
            t_device = self._n / self._fs          # 设备采样时刻（设备时钟，秒）
            device_ts = t_device + self._offset_s  # 设备时间戳 = 设备时钟 + 固定偏移
            self._n += 1
            if self._rng.random() < self._loss:
                continue                           # 丢帧：样本数已计，但不产出
            valid = self._rng.random() >= self._invalid
            x = 0.5 + 0.25 * np.sin(2 * np.pi * 0.2 * t_device)
            y = 0.5 + 0.25 * np.sin(2 * np.pi * 0.13 * t_device + 1.0)
            out.append(EyeSample(
                device_ts=device_ts,
                clock_source="simulated_device_Hz",
                x=None if not valid else float(x),
                y=None if not valid else float(y),
                valid=valid,
            ))
        return out
