"""标准 EDF 文件导出（纯 Python，零第三方依赖）。

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
import math


def write_edf(path: str, data, fs: float, channel_names: list, started_at=None) -> bool:
    """写普通 EDF；记录长度按样本数选择，避免在末尾虚构补零样本。"""
    try:
        data = np.asarray(data, dtype=np.float64)
        if data.ndim == 1:
            data = data[None, :]
        n_ch, n = data.shape
        if n_ch == 0 or n == 0 or len(channel_names) != n_ch or not np.isfinite(data).all():
            return False
        fs = float(fs)
        if fs <= 0 or not fs.is_integer():
            return False
        nr = math.gcd(n, int(fs))
        duration = f"{nr / fs:.6f}".rstrip("0").rstrip(".")
        if len(duration) > 8 or abs(float(duration) - nr / fs) > 1e-9:
            return False
        n_records = n // nr

        # 每通道物理量程（µV），留 1% 余量，避免边界值溢出
        pmin = data.min(axis=1)
        pmax = data.max(axis=1)
        span = pmax - pmin
        # EDF 数值字段最多 8 字符。高直流且平线的通道若只留 0.01 µV，
        # 两个边界会四舍五入成同一数值，导致整份 EDF 导出失败。
        margin = np.maximum(0.01 * np.maximum(span, 1.0),
                            1e-5 * np.maximum(np.abs(pmin), np.abs(pmax)))
        pmin = pmin - margin
        pmax = pmax + margin

        dig_min, dig_max = -32768, 32767
        header_bytes = 256 + 256 * n_ch

        parts: list = []
        parts.append("0".ljust(8))                      # 版本
        parts.append("".ljust(80))                      # 被试标识
        parts.append("eegscope".ljust(80))              # 记录标识
        parts.append(started_at.strftime("%d.%m.%y") if started_at else "01.01.70")
        parts.append(started_at.strftime("%H.%M.%S") if started_at else "00.00.00")
        parts.append(str(header_bytes).ljust(8))        # 头字节数
        parts.append("".ljust(44))                      # 普通 EDF，非 EDF+
        parts.append(str(n_records).ljust(8))           # 记录数
        parts.append(duration.ljust(8))                  # 每条记录时长（秒）
        parts.append(str(n_ch).ljust(4))                # 通道数

        def number(value):
            for precision in range(6, 0, -1):
                text = f"{value:.{precision}g}"
                if len(text) <= 8:
                    return text
            raise ValueError("EDF 物理量程无法表示")

        pmin_text = [number(v) for v in pmin]
        pmax_text = [number(v) for v in pmax]
        pmin = np.array([float(v) for v in pmin_text])
        pmax = np.array([float(v) for v in pmax_text])
        if np.any(pmin >= pmax) or np.any(pmin > data.min(axis=1)) or np.any(pmax < data.max(axis=1)):
            return False
        labels = [str(v).encode("ascii", "replace").decode()[:16] for v in channel_names]
        fields = [(labels, 16), ([""] * n_ch, 80), (["uV"] * n_ch, 8),
                  (pmin_text, 8), (pmax_text, 8), ([str(dig_min)] * n_ch, 8),
                  ([str(dig_max)] * n_ch, 8), ([""] * n_ch, 80),
                  ([str(nr)] * n_ch, 8), ([""] * n_ch, 32)]
        for values, width in fields:
            parts.extend(value.ljust(width) for value in values)

        header = "".join(parts).encode("ascii", errors="replace")
        if len(header) != header_bytes:
            return False

        with open(path, "wb") as f:
            f.write(header)
            for r in range(n_records):
                for i in range(n_ch):
                    seg = data[i, r * nr:(r + 1) * nr]
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
