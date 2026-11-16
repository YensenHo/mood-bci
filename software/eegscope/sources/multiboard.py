"""MultiBoardSource —— 多块 OpenBCI 板合并成一个数据源（4 × 16ch = 64ch）。

背景：RT-BCI 每块 16ch 板是独立的一个 USB 串口（自带 STM32），4 块板 = 4 个串口，
不是插上就自动 64 通道。USB 集线器只做物理集中，数据合并必须在这里做：
本类把多块 OpenBCISource 包起来，对外表现为一个 64 通道数据源。

拼接顺序：read() 时按板顺序拼接 —— 第 1 块板 = 通道 0~15，第 2 块 = 16~31，
第 3 块 = 32~47，第 4 块 = 48~63。channel_names 必须按同样的顺序给，
即「你物理上怎么把 64 个电极接到 4 块板的 64 个通道上，channels 列表就怎么写」。

诚实边界：各板是独立 STM32、独立采样时钟，无硬件同步信号，
跨板时间对齐靠「等样本数」对齐；长期漂移（ppm 级晶振误差）是硬件限制。
不同板的实际偏移与漂移尚未实测，不能据此认定任何时间尺度的分析已对齐。
"""
from __future__ import annotations

import numpy as np
import time

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

        # 每板独立输出缓冲（不静默丢弃未消费样本）：
        # 各板独立时钟 + 掉线时样本数不等，read() 把「比最慢板多出的样本」缓冲起来，
        # 下次 read 优先消费，避免被截断丢弃。缓冲超上限时丢最旧 + 记溢出（显式标记）。
        self._buffers = [np.zeros((self._n_ch_per_board, 0), dtype=np.float64)
                         for _ in self._boards]
        self._buffer_maxlen = int(round(self.sample_rate * 2.0))   # 上限 2 秒样本量
        self._buffer_overflow = [0] * len(self._boards)            # 每板缓冲溢出计数

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
    def full_scale_uv(self) -> float:
        return self._boards[0].full_scale_uv

    @property
    def alpha_level(self) -> float:
        return 0.0

    def set_alpha_level(self, level: float) -> None:
        pass

    def start(self) -> None:
        started = []
        try:
            for b in self._boards:
                b.start()
                started.append(b)
        except Exception:
            for b in reversed(started):
                b.stop()
            raise
        # 板卡依次握手期间，较早启动的板会产生旧样本。
        self.discard_pre_boundary_samples()

    def discard_pre_boundary_samples(self) -> dict:
        """丢弃边界之前已收到的积压，仅在采集启动/新录制开始前调用。

        这是接收端粗对齐，不是采样时钟同步；串口/设备内部可能仍有旧数据。
        每板快照后到达的数据保留，避免无限清队列。
        """
        started = time.monotonic()
        buffered = [int(buf.shape[1]) for buf in self._buffers]
        self._buffers = [np.zeros((self._n_ch_per_board, 0), dtype=np.float64)
                         for _ in self._boards]
        queued = [b._queue.qsize() for b in self._boards]
        removed = [b.read_available(n).shape[1] for b, n in zip(self._boards, queued)]
        self._last_buffered = [0] * len(self._boards)
        return {"method": "discard_received_pre_boundary",
                "discarded_buffered_samples": buffered,
                "discarded_queued_samples": removed,
                "boundary_duration_seconds": time.monotonic() - started,
                "hardware_synchronized": False,
                "note": "接收端近似边界；设备/串口残留、传输延迟和独立时钟仍可能造成偏移或漂移。"}

    def stop(self) -> None:
        for b in self._boards:
            b.stop()

    def read(self, n_samples: int) -> np.ndarray:
        n = int(n_samples)
        if n <= 0:
            return np.zeros((self._n_ch, 0), dtype=np.float64)
        # 逐板：先消费上一轮缓冲，再读新样本，拼成「尽量 n 样本」的一段
        chunks: list[np.ndarray] = []
        for i, b in enumerate(self._boards):
            fresh = b.read(n)
            buf = self._buffers[i]
            combined = (np.concatenate([buf, fresh], axis=1)
                        if buf.shape[1] else fresh)
            # 缓冲溢出：超上限时丢最旧样本 + 记溢出（不静默）
            if combined.shape[1] > self._buffer_maxlen:
                dropped = combined.shape[1] - self._buffer_maxlen
                self._buffer_overflow[i] += dropped
                combined = combined[:, -self._buffer_maxlen:]
            chunks.append(combined)
        self._last_board_gaps = [int(b.last_read_gap()) for b in self._boards]

        min_len = min((c.shape[1] for c in chunks), default=0)
        if min_len == 0:
            # 全部掉线：已读到的样本全部存回缓冲（不丢），返回零填充兜底
            for i, c in enumerate(chunks):
                self._buffers[i] = c
            self._last_buffered = [int(c.shape[1]) for c in chunks]
            return np.empty((self._n_ch, 0), dtype=np.float64)

        # 每板只消费 min_len，多余部分存回缓冲（不静默丢弃未消费样本）
        out_parts = []
        self._last_buffered = []
        for i, c in enumerate(chunks):
            out_parts.append(c[:, :min_len])
            self._buffers[i] = c[:, min_len:]
            self._last_buffered.append(int(c.shape[1] - min_len))
        return np.concatenate(out_parts, axis=0)

    def read_available(self, max_samples: int) -> np.ndarray:
        """拉取后台队列中已接收的数据，缓冲每板多余样本，不等待慢板。"""
        limit = max(0, int(max_samples))
        self._last_arrivals = []
        for i, board in enumerate(self._boards):
            # 慢板未跟上时，限制中间缓冲增长；其余样本仍留在后台队列。
            room = max(0, max(limit, self._buffer_maxlen) - self._buffers[i].shape[1])
            fresh = board.read_available(min(limit, room))
            self._last_arrivals.append(fresh.shape[1])
            if fresh.shape[1]:
                self._buffers[i] = np.concatenate((self._buffers[i], fresh), axis=1)
        self._last_board_gaps = [0] * len(self._boards)
        count = min([limit] + [b.shape[1] for b in self._buffers])
        if count == 0:
            self._last_buffered = [b.shape[1] for b in self._buffers]
            return np.empty((self._n_ch, 0), dtype=np.float64)
        out = np.concatenate([b[:, :count] for b in self._buffers], axis=0)
        self._buffers = [b[:, count:] for b in self._buffers]
        self._last_buffered = [b.shape[1] for b in self._buffers]
        return out

    def read_gap_info(self) -> dict:
        """返回上次 read() 的缺口/缓冲/溢出信息（供录制层标记，不静默丢样本）。

        - per_board_gap      每板本次请求 n 与实际读到的不足部分（掉线/欠采）；
        - per_board_buffered 每板比最慢板多出、暂存待下次消费的样本数（未丢弃）；
        - per_board_overflow 每板缓冲超上限被丢最旧的累计样本数（显式标记溢出）。
        """
        return {
            "per_board_gap": list(getattr(self, "_last_board_gaps", [0] * len(self._boards))),
            "per_board_buffered": list(getattr(self, "_last_buffered", [0] * len(self._boards))),
            "per_board_overflow": list(self._buffer_overflow),
            "per_board_queued": [b._queue.qsize() for b in self._boards],
            "per_board_new_samples": list(getattr(self, "_last_arrivals", [])),
        }

    def board_ports(self):
        """返回各板串口（调试/日志用）。"""
        return list(self._ports)

    def board_stats(self) -> list:
        """返回各板的帧级同步统计（顺序 = ports 顺序，即物理板序）。

        用于时间同步地基：每块板自己的帧编号 + 接收时间 + 估计丢帧数。
        注意：各板独立 STM32/独立采样时钟，接收时间是「电脑收到」而非「设备采样」。
        """
        return [b.frame_stats() for b in self._boards]

    def drain_frame_logs(self) -> list:
        """drain 各板的逐帧记录（每板 list of (byte1 帧编号, 接收单调时间)）。"""
        return [b.drain_frame_log() for b in self._boards]
