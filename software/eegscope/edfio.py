"""标准 EDF+ 文件导出（纯 Python，零第三方依赖）。

EDF（European Data Format）是脑电/生理时间序列的通用交换格式，EEGLAB、
MNE-Python、FieldTrip 等几乎所有脑电软件都能直接读。相比本项目自用的
session.npz（numpy 私有格式），EDF 让数据能被主流工具直接打开、交换、分析。

EDF 结构（纯 ASCII 头 + int16 小端数据）：
  - 头记录 256 字节（全局） + 每通道 256 字节
  - 数据记录：按记录顺序，每通道 nr 个 int16 样本
本实现用「1 秒一条记录」的惯例，与主流设备导出一致。
"""
from __future__ import annotations

import numpy as np


def write_edf(path: str, data, fs: float, channel_names: list) -> bool:
    """把 (n_ch, n) 的 µV 数据写成 EDF+ 文件。成功返回 True，失败返回 False。"""
    try:
        data = np.asarray(data, dtype=np.float64)
        if data.ndim == 1:
            data = data[None, :]
        n_ch, n = data.shape
        if n_ch == 0 or n == 0:
            return False
        fs = float(fs)
        nr = max(1, int(round(fs)))          # 每记录样本数（1 秒）
        n_records = int(np.ceil(n / nr))

        # 每通道物理量程（µV），留 1% 余量，避免边界值溢出
        pmin = data.min(axis=1)
        pmax = data.max(axis=1)
        span = pmax - pmin
        span = np.where(span < 1e-6, 1.0, span)
        pmin = pmin - 0.01 * span
        pmax = pmax + 0.01 * span

        dig_min, dig_max = -32768, 32767
        header_bytes = 256 + 256 * n_ch

        parts: list = []
        parts.append("0".ljust(8))                      # 版本
        parts.append("".ljust(80))                      # 被试标识
        parts.append("eegscope".ljust(80))              # 记录标识
        parts.append("01.01.70".ljust(8))               # 开始日期（占位）
        parts.append("00.00.00".ljust(8))               # 开始时间（占位）
        parts.append(str(header_bytes).ljust(8))        # 头字节数
        parts.append("EDF+C".ljust(44))                 # 保留（标 EDF+）
        parts.append(str(n_records).ljust(8))           # 记录数
        parts.append("1".ljust(8))                      # 每条记录时长（秒）
        parts.append(str(n_ch).ljust(4))                # 通道数

        for i in range(n_ch):
            label = str(channel_names[i]).encode("ascii", "replace").decode()[:16]
            parts.append(label.ljust(16))
            parts.append("".ljust(80))                  # 传感器类型
            parts.append("uV".ljust(8))                 # 物理单位
            parts.append(f"{pmin[i]:.6g}".ljust(8))     # 物理最小
            parts.append(f"{pmax[i]:.6g}".ljust(8))     # 物理最大
            parts.append(str(dig_min).ljust(8))         # 数字最小
            parts.append(str(dig_max).ljust(8))         # 数字最大
            parts.append("".ljust(80))                  # 预滤波
            parts.append(str(nr).ljust(8))              # 每记录样本数
            parts.append("".ljust(32))                  # 保留

        header = "".join(parts).encode("ascii", errors="replace")
        if len(header) != header_bytes:
            return False

        with open(path, "wb") as f:
            f.write(header)
            for r in range(n_records):
                for i in range(n_ch):
                    seg = data[i, r * nr:(r + 1) * nr]
                    if seg.shape[0] < nr:
                        seg = np.pad(seg, (0, nr - seg.shape[0]))
                    scaled = ((seg - pmin[i]) / (pmax[i] - pmin[i])
                              * (dig_max - dig_min) + dig_min)
                    scaled = np.clip(np.rint(scaled), dig_min, dig_max).astype(np.int16)
                    f.write(scaled.tobytes())
        return True
    except Exception:
        return False


def write_csv(path: str, data, fs: float, channel_names: list) -> bool:
    """把 (n_ch, n) 的 µV 数据写成 CSV（OpenBCI 同款，Excel/Python 都能读）。"""
    try:
        data = np.asarray(data, dtype=np.float64)
        if data.ndim == 1:
            data = data[None, :]
        n_ch, n = data.shape
        if n_ch == 0 or n == 0:
            return False
        header = ",".join([str(c) for c in channel_names])
        with open(path, "w", encoding="utf-8") as f:
            f.write(header + "\n")
            # 按列写：每行 n_ch 个值（转置后逐行写，避免整块格式化的内存峰值）
            for row in data.T:
                f.write(",".join(f"{v:.4f}" for v in row) + "\n")
        return True
    except Exception:
        return False
