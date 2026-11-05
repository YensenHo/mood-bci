#!/usr/bin/env python3
"""Headless 测试：OpenBCI 帧解析器 + FrameScanner（无需真实硬件）。

用构造的 33 字节帧验证：µV 缩放、24bit 符号扩展、帧头扫描、失步重同步、
OpenBCISource 构造校验（8/16 通道采样率、非法通道数报错）。

运行（隔离环境）：
    env -i HOME=$HOME PATH=/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin PYTHONPATH= \
        .venv/bin/python scripts/test_openbci_parser.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from eegscope.sources.openbci import (FRAME_LEN, START_BYTE, FrameScanner,
                                      OpenBCISource, parse_cyton_frame)


def make_frame(channel_vals, counter=0, end=0xC0):
    """channel_vals: 长度 8 的 24bit 有符号整数列表。"""
    f = bytearray(FRAME_LEN)
    f[0] = START_BYTE
    f[1] = counter & 0xFF
    for i, v in enumerate(channel_vals):
        v &= 0xFFFFFF
        f[2 + 3 * i] = (v >> 16) & 0xFF
        f[3 + 3 * i] = (v >> 8) & 0xFF
        f[4 + 3 * i] = v & 0xFF
    f[FRAME_LEN - 1] = end & 0xFF
    return bytes(f)


def main() -> int:
    scale24 = 4.5e6 / (24 * 2 ** 23)  # 增益 24 → 缩放 µV/count

    # [1] 缩放与正值
    ch = parse_cyton_frame(make_frame([1, 0, 0, 0, 0, 0, 0, 0]), gain=24.0)
    assert np.isclose(ch[0], scale24), f"1 count 应= {scale24} µV, 得 {ch[0]}"
    assert all(ch[i] == 0 for i in range(1, 8)), "其余通道应为 0"
    print(f"[1] 缩放 OK: 1 count = {ch[0]:.6f} µV (期望 {scale24:.6f})")

    # [2] 符号扩展（负值）
    ch = parse_cyton_frame(make_frame([-1, 0, 0, 0, 0, 0, 0, 0]), gain=24.0)
    assert np.isclose(ch[0], -scale24), f"-1 count 应= {-scale24}, 得 {ch[0]}"
    print(f"[2] 符号扩展 OK: -1 count = {ch[0]:.6f} µV")

    # [3] 大值（256 counts）
    ch = parse_cyton_frame(make_frame([256, 0, 0, 0, 0, 0, 0, 0]), gain=24.0)
    assert np.isclose(ch[0], 256 * scale24), f"256 counts 应= {256*scale24}, 得 {ch[0]}"
    print(f"[3] 大值 OK: 256 counts = {ch[0]:.4f} µV")

    # [4] 增益缩放（gain=12 → 缩放翻倍）
    ch = parse_cyton_frame(make_frame([1] + [0] * 7), gain=12.0)
    assert np.isclose(ch[0], 2 * scale24), "gain=12 缩放应翻倍"
    print(f"[4] 增益缩放 OK: gain=12 → {ch[0]:.6f} µV (=2×)")

    # [5] 显式 scale_uv 覆盖
    ch = parse_cyton_frame(make_frame([100] + [0] * 7), gain=24.0, scale_uv=0.1)
    assert np.isclose(ch[0], 10.0), "scale_uv=0.1, 100 counts 应=10 µV"
    print(f"[5] 显式 scale 覆盖 OK: {ch[0]:.1f} µV")

    # [6] FrameScanner：噪声 + 两帧，正确提取，样本计数保留
    sc = FrameScanner()
    frames = list(sc.feed(b"\x00\xAA\xFF" + make_frame([1] + [0] * 7, counter=5) +
                          b"\x13" + make_frame([2] + [0] * 7, counter=6)))
    assert len(frames) == 2, f"应提取 2 帧, 得 {len(frames)}"
    assert frames[0][1] == 5 and frames[1][1] == 6, "样本计数应保留"
    print(f"[6] FrameScanner OK: 噪声后提取 {len(frames)} 帧, 计数 {frames[0][1]}/{frames[1][1]}")

    # [7] FrameScanner：坏帧尾（假帧头）→ 丢弃并重同步，保留后面的好帧
    bad = bytearray(make_frame([1] + [0] * 7))
    bad[FRAME_LEN - 1] = 0x00  # 破坏帧尾
    good = make_frame([3] + [0] * 7, counter=9)
    frames2 = list(FrameScanner().feed(bytes(bad) + good))
    assert len(frames2) == 1, f"坏帧应被丢弃只留 1 好帧, 得 {len(frames2)}"
    assert frames2[0][1] == 9
    print(f"[7] 失步重同步 OK: 坏帧丢弃, 好帧 {len(frames2)} 帧")

    # [8] OpenBCISource 构造校验
    s8 = OpenBCISource(port="/dev/null", n_channels=8)
    assert s8.n_channels == 8 and s8.sample_rate == 250.0
    s16 = OpenBCISource(port="/dev/null", n_channels=16)
    assert s16.n_channels == 16 and s16.sample_rate == 125.0
    try:
        OpenBCISource(port="/dev/null", n_channels=12)
        raise AssertionError("12 通道应报错")
    except ValueError:
        pass
    print("[8] OpenBCISource 构造 OK: 8ch→250Hz, 16ch→125Hz, 12ch 报错")

    print("\n✅ OpenBCI 解析器测试全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
