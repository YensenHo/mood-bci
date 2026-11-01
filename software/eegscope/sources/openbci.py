"""OpenBCISource —— OpenBCI 兼容 ADS1299 采集板的串口数据源。

背景：用户买的「16 通道 ADS1299 采集板」是 OpenBCI 兼容板（商品描述里明确写了
「OpenBCI 兼容采集板」「配合 OpenBCI GUI」），走 OpenBCI Cyton/Daisy 串口协议。
本驱动直接解析 OpenBCI 的 33 字节数据帧，不依赖 OpenBCI 官方 GUI。

协议（OpenBCI Cyton，8ch @ 250 SPS）：
  每帧 33 字节：
    [0]     0xA0 帧头
    [1]     样本计数（0-255 回绕）
    [2..25] 8 通道 × 3 字节 = 24 字节，24bit 有符号大端（二进制补码）
    [26..31] 6 字节辅助（加速度计，EEG 采集时忽略）
    [32]     0xC0~0xC6 帧尾（0xC0 = 无辅助数据）

  缩放：µV = raw × (4.5e6 / (gain × 2^23))，gain 默认 24 → 0.02235 µV/count。

16 通道（Cyton + Daisy）：
  两个 8ch 板级联，样本计数最低位 bit0 区分主/副板：
    bit0=0 → 主板（通道 1-8）；bit0=1 → 副板（通道 9-16）。
  每通道有效采样率 = 基频一半（250 → 125 Hz）。

⚠️ 诚实边界：这是「OpenBCI 兼容」克隆板，具体波特率/帧格式/通道数/缩放可能与
原版有出入。板子到手第一步：用 scripts/capture_serial.py 抓几秒原始字节，确认
波特率 + 帧头 0xA0 + 通道数 + 缩放，再定 8ch 还是 Daisy、gain 多少。本驱动的
波特率/gain/通道数/scale 均可配，抓到真实字节后按实际填 config.yaml。
"""
from __future__ import annotations

import queue
import threading
import time

import numpy as np

from .base import DataSource

START_BYTE = 0xA0
FRAME_LEN = 33
CH_PER_BOARD = 8
# ADS1299 满量程 ±4.5V（OpenBCI 内部基准），24bit 有符号 → LSB = 4.5e6/(gain·2^23) µV
VREF_UV = 4.5e6


def parse_cyton_frame(frame: bytes, gain: float = 24.0,
                      scale_uv: float | None = None) -> np.ndarray:
    """解析一个 33 字节 Cyton 帧 → 8 通道 µV（float64，shape (8,)）。

    scale_uv 显式给定时覆盖 gain（单位 µV/count）。
    """
    if len(frame) < FRAME_LEN:
        raise ValueError(f"帧过短: {len(frame)} < {FRAME_LEN}")
    if scale_uv is None:
        scale_uv = VREF_UV / (gain * (2 ** 23))
    ch = np.zeros(CH_PER_BOARD, dtype=np.float64)
    for i in range(CH_PER_BOARD):
        b = frame[2 + 3 * i: 5 + 3 * i]
        v = (b[0] << 16) | (b[1] << 8) | b[2]
        if v & 0x800000:
            v -= 0x1000000  # 24bit 符号扩展
        ch[i] = v * scale_uv
    return ch


class FrameScanner:
    """字节流 → 完整 33 字节帧（带失步重同步）。

    帧头 0xA0 之后若帧尾高半字节不是 0xC（假帧头/数据里撞见 0xA0），
    丢 1 字节重扫，自动恢复对齐。
    """

    def __init__(self, start_byte: int = START_BYTE, frame_len: int = FRAME_LEN):
        self._start = start_byte
        self._len = frame_len
        self._buf = bytearray()

    def _is_valid(self, frame: bytes) -> bool:
        # 帧尾高半字节应为 0xC（0xC0~0xC6/0xCF）
        return (frame[self._len - 1] & 0xF0) == 0xC0

    def feed(self, data: bytes):
        self._buf.extend(data)
        while len(self._buf) >= self._len:
            idx = self._buf.find(self._start)
            if idx < 0:
                # 无帧头：保留尾部（可能是半个帧头），等更多字节
                self._buf = self._buf[-(self._len - 1):]
                break
            if idx > 0:
                del self._buf[:idx]
                continue
            frame = bytes(self._buf[:self._len])
            if self._is_valid(frame):
                del self._buf[:self._len]
                yield frame
            else:
                del self._buf[:1]  # 假帧头，丢 1 字节重扫


class OpenBCISource(DataSource):
    """OpenBCI 兼容串口数据源（8ch 或 16ch Daisy）。

    read() 由后台线程持续解析串口帧填充队列；read() 阻塞到拿到 ≥1 样本
    （掉线兜底返回 1 个零样本，避免下游空数组报错）。
    """

    def __init__(self, port, baudrate=115200, n_channels=16, gain=24.0,
                 scale_uv=None, channel_names=None, timeout=2.0,
                 gain_code=None):
        if n_channels not in (8, 16):
            raise ValueError(f"OpenBCI 仅支持 8 或 16 通道，收到 {n_channels}")
        self._port = port
        self._baudrate = int(baudrate)
        self._n_ch = int(n_channels)
        self._daisy = (self._n_ch == 16)
        self._gain = float(gain)
        self._scale = float(scale_uv) if scale_uv else None
        self._timeout = float(timeout)
        # gain_code：RT-BCI 板的「x 通道 ... X」增益指令参数（0=1x,1=2x,2=4x,3=6x,4=8x,5=12x,6=24x）。
        # None = 不发送增益指令（保持板子上电默认，通常 24x）。
        self._gain_code = None if gain_code is None else int(gain_code)
        if self._gain_code is not None:
            # 发送了增益指令后，缩放用的增益要跟着变，否则 µV 值错 24 倍
            _gmap = {0: 1.0, 1: 2.0, 2: 4.0, 3: 6.0, 4: 8.0, 5: 12.0, 6: 24.0}
            self._gain = _gmap.get(self._gain_code, 24.0)
        self._fs = 250.0 / (2.0 if self._daisy else 1.0)

        names = list(channel_names) if channel_names else None
        self._names = (names if names and len(names) == self._n_ch
                       else [f"ch{i + 1}" for i in range(self._n_ch)])

        self._ser = None
        self._thread = None
        self._stop_event = threading.Event()
        self._queue = queue.Queue()

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
    def is_synthetic(self) -> bool:
        return False

    @property
    def alpha_level(self) -> float:
        return 0.0  # 真实硬件无 alpha 旋钮概念（合成源专用）

    def set_alpha_level(self, level: float) -> None:
        pass  # 真实硬件不可调，no-op

    def start(self) -> None:
        import serial
        if self._ser is None:
            self._ser = serial.Serial(self._port, self._baudrate, timeout=0.05)
        self._stop_event.clear()
        self._handshake()
        self._thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._thread.start()

    def _handshake(self) -> None:
        """RT-BCI 板握手：发 0xF0 → 等 "$$$" → （可选）设增益 → 发 'b' 开始采集。

        失败不致命：reader 线程仍会尝试读帧（若板子已自行开始流式）。
        """
        ser = self._ser
        if ser is None:
            return
        try:
            ser.reset_input_buffer()
            ser.write(b"\xF0")  # 触发「Success: System is Up」握手
            buf = b""
            deadline = time.time() + 2.0
            while b"$$$" not in buf and time.time() < deadline:
                d = ser.read(200)
                if d:
                    buf += d
            # 可选：设置增益（打满 Railed 时把 gain_code 设为 0 = 1x）
            # 固件 BCI_parse 用紧凑格式：x{通道}{power_down}{增益}{input}{bias}{srb2}{srb1}X
            if self._gain_code is not None:
                for ch in range(1, 9):
                    ser.write(f"x{ch}0{self._gain_code}0000X".encode())
                    time.sleep(0.005)
                if self._daisy:
                    # 16 通道：9~16 用 Q W E R T Y U I 表示
                    for nm in "QWERTYUI":
                        ser.write(f"x{nm}0{self._gain_code}0000X".encode())
                        time.sleep(0.005)
            ser.write(b"b")  # 开始采集
        except Exception:
            pass

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        if self._ser is not None:
            try:
                self._ser.close()
            except Exception:
                pass
            self._ser = None

    def read(self, n_samples: int) -> np.ndarray:
        n = int(n_samples)
        if n <= 0:
            return np.zeros((self._n_ch, 0), dtype=np.float64)
        out = []
        deadline = time.time() + max(1.0, 4.0 * n / self._fs)
        while len(out) < n and time.time() < deadline:
            try:
                out.append(self._queue.get(timeout=0.05))
            except queue.Empty:
                continue
        if not out:
            return np.zeros((self._n_ch, 1), dtype=np.float64)  # 掉线兜底
        return np.stack(out, axis=1).astype(np.float64)

    # ---- 后台读串口线程 ----
    def _reader_loop(self) -> None:
        scanner = FrameScanner()
        pending_main = None  # Daisy：暂存主板 8ch，等副板 8ch 拼成 16ch
        while not self._stop_event.is_set():
            try:
                ser = self._ser
                if ser is None:
                    break
                raw = ser.read(ser.in_waiting or 1)
                if not raw:
                    continue
            except Exception:
                break
            for frame in scanner.feed(raw):
                ch = parse_cyton_frame(frame, self._gain, self._scale)
                if not self._daisy:
                    self._queue.put(ch)
                else:
                    board = frame[1] & 1
                    if board == 0:
                        pending_main = ch
                    elif pending_main is not None:
                        self._queue.put(np.concatenate([pending_main, ch]))
                        pending_main = None


def list_ports():
    """列出可用串口（供 capture_serial / 调试）。返回 list[str]。"""
    import serial.tools.list_ports
    return [p.device for p in serial.tools.list_ports.comports()]
