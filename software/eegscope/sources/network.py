"""NetworkSource —— 监控端数据源：从 StreamServer 收参与者实时上传的脑电数据。

接入方式与串口源完全一致（继承 DataSource，实现 start/stop/read），
因此整套 DSP / 分析 / 可视化 / 录制管线无需改动，直接显示远程数据。

通道数 / 通道名 / 采样率以监控端 config 为准（开发者在 config_remote.yaml 里
填的和参与者同一套硬件配置）。参与者 hello 里的 fs/channels/participant 作为
元数据留存（记录是谁、以及未来多参与者异构建模用），不覆盖 config 的解码口径。

start() 在本地起 StreamServer 监听 host:port，等参与者连进来推数据；
read(n) 从服务器队列取数据块拼成 (n_ch, n)。
"""
from __future__ import annotations

import time

import numpy as np

from .base import DataSource
from ..remote import StreamServer


class NetworkSource(DataSource):
    """接收参与者实时上传的脑电（WebSocket 服务器端）。"""

    def __init__(self, host: str = "0.0.0.0", port: int = 8765,
                 channel_names: list | None = None, sample_rate: float = 250.0,
                 timeout: float = 2.0):
        self._host = host
        self._port = int(port)
        self._timeout = float(timeout)
        self._names = [str(c) for c in (channel_names or [])]
        self._fs = float(sample_rate)
        n_ch = len(self._names)
        self._server = StreamServer(host=self._host, port=self._port,
                                    n_channels=n_ch or None)

    # ---- DataSource 接口 ----
    @property
    def sample_rate(self) -> float:
        return self._fs

    @property
    def n_channels(self) -> int:
        return len(self._names)

    @property
    def channel_names(self):
        return list(self._names) if self._names else \
            [f"ch{i + 1}" for i in range(self.n_channels)]

    @property
    def is_synthetic(self) -> bool:
        return False

    @property
    def full_scale_uv(self) -> float:
        return 187500.0  # 与真实板一致（gain 24x）

    @property
    def participant(self) -> str:
        """最近连上来的参与者名字（没连上时为空串）。"""
        m = self._server.meta
        return str((m or {}).get("participant", "")) if m else ""

    @property
    def participants(self) -> list:
        """已连接的参与者名单（ISC / 多人用）。"""
        return self._server.participants

    def start(self) -> None:
        self._server.start()

    def stop(self) -> None:
        self._server.stop()

    def read(self, n_samples: int) -> np.ndarray:
        """拼 n_samples 个样本；无数据时阻塞到 deadline，兜底返回 1 个零样本。"""
        n = int(n_samples)
        nc = max(self.n_channels, 1)
        if n <= 0:
            return np.zeros((nc, 0), dtype=np.float64)

        out: list = []
        deadline = time.time() + max(1.0, 4.0 * n / max(self._fs, 1.0))
        while sum(c.shape[1] for c in out) < n and time.time() < deadline:
            chunk = self._server.get(timeout=0.05)
            if chunk is None:
                continue
            out.append(chunk)

        if not out:
            return np.zeros((nc, 1), dtype=np.float64)
        data = np.concatenate(out, axis=1)
        return data[:, :n]

    def read_participant(self, participant: str, n_samples: int) -> np.ndarray:
        """读某个参与者的 n_samples 个样本（ISC 用）；无数据时兜底 1 个零样本。"""
        n = int(n_samples)
        nc = max(self.n_channels, 1)
        if n <= 0:
            return np.zeros((nc, 0), dtype=np.float64)
        out: list = []
        deadline = time.time() + max(1.0, 4.0 * n / max(self._fs, 1.0))
        while sum(c.shape[1] for c in out) < n and time.time() < deadline:
            chunk = self._server.get(participant=participant, timeout=0.05)
            if chunk is None:
                continue
            out.append(chunk)
        if not out:
            return np.zeros((nc, 1), dtype=np.float64)
        data = np.concatenate(out, axis=1)
        return data[:, :n]
