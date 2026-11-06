#!/usr/bin/env python3
"""Headless 测试：多板（4 × 16ch = 64ch）数据源 —— 工厂接线 + read 对齐。

覆盖：
  [1] config64.yaml 解析 → make_source → MultiBoardSource：64 通道、正确通道名、F3/F4/O* 在位。
  [2] read() 对齐：各板返回不同样本数时，按最小样本数对齐拼接（不 crash、顺序正确）。
  [3] 单板回归：config.yaml（单 port）仍走 OpenBCISource，16 通道。

运行（隔离环境）：
    env -i HOME=$HOME PATH=/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin PYTHONPATH= \\
        .venv/bin/python scripts/test_multiboard.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from eegscope.config import load_config
from eegscope.sources import make_source
from eegscope.sources.multiboard import MultiBoardSource
from eegscope.sources.openbci import OpenBCISource

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)


def test_config64_builds_64ch():
    cfg = load_config(os.path.join(_ROOT, "config64.yaml"))
    assert len(cfg.channels) == 64, f"config64 应为 64 通道，实为 {len(cfg.channels)}"
    assert len(cfg.source.openbci.ports) == 4, "config64 应配 4 个串口"

    src = make_source(cfg)
    assert isinstance(src, MultiBoardSource), f"应为 MultiBoardSource，实为 {type(src).__name__}"
    assert src.n_channels == 64, f"应为 64 通道，实为 {src.n_channels}"
    assert src.sample_rate == 125.0, f"16ch Daisy 每通道应 125Hz，实为 {src.sample_rate}"
    assert src.channel_names == cfg.channels, "通道名应与 config 顺序一致"

    names = src.channel_names
    # 关键通道在位：FAA 需 F3/F4，枕区 alpha 需 O*
    for want in ("F3", "F4", "O1", "Oz", "O2", "T7", "T8", "CB1"):
        assert want in names, f"缺关键通道 {want}"
    # 无重复（信号通道名唯一；NC 占位=空通道，允许出现多次）
    signal_names = [n for n in names if n != "NC"]
    assert len(set(signal_names)) == len(signal_names), "信号通道名不应有重复"
    assert names.count("NC") == 2, f"应有 2 个空通道占位 NC，实为 {names.count('NC')}"


def test_read_aligns_to_min_length():
    src = MultiBoardSource(ports=["/a", "/b", "/c", "/d"], n_channels_per_board=16)
    # 伪造各板 read：板 i 返回 (16, 10-i) 个样本，全填 (i+1) 以验证拼接顺序
    for i, b in enumerate(src._boards):
        b.read = lambda n, i=i: np.full((16, max(1, n - i)), float(i + 1))
    out = src.read(10)
    # 板3 只给 7 个 → 全体对齐到 7
    assert out.shape == (64, 7), f"应 (64,7)，实为 {out.shape}"
    # 拼接顺序：板0=ch0-15 全1，板1=ch16-31 全2，板2=ch32-47 全3，板3=ch48-63 全4
    assert out[0, 0] == 1.0 and out[15, 0] == 1.0
    assert out[16, 0] == 2.0 and out[31, 0] == 2.0
    assert out[32, 0] == 3.0 and out[47, 0] == 3.0
    assert out[48, 0] == 4.0 and out[63, 0] == 4.0


def test_read_all_dead_fallback():
    src = MultiBoardSource(ports=["/a", "/b", "/c", "/d"], n_channels_per_board=16)
    for b in src._boards:
        b.read = lambda n: np.zeros((16, 0))  # 全部掉线 → 0 样本
    out = src.read(10)
    assert out.shape == (64, 1), f"全掉线兜底应 (64,1)，实为 {out.shape}"


def test_single_board_regression():
    cfg = load_config(os.path.join(_ROOT, "config.yaml"))
    src = make_source(cfg)
    assert isinstance(src, OpenBCISource), "单板(单 port)应仍走 OpenBCISource"
    assert src.n_channels == 16, f"config.yaml 应 16 通道，实为 {src.n_channels}"


def test_channel_2d_covers_64ch():
    # 地形图坐标表必须覆盖 config64 的全部 64 个位置，否则面板会静默跳过缺失电极
    from eegscope.viz import CHANNEL_2D, topo_interpolate
    cfg = load_config(os.path.join(_ROOT, "config64.yaml"))
    missing = [nm for nm in cfg.channels if nm.upper() not in
               {k.upper() for k in CHANNEL_2D}]
    assert not missing, f"CHANNEL_2D 缺这些位置: {missing}"

    # 64 通道插值应正常出图（≥3 个可定位通道，不 crash）
    vals = np.linspace(1.0, 64.0, 64)
    grid, extent = topo_interpolate(cfg.channels, vals)
    assert grid.shape == (64, 64), f"插值网格应 64×64，实为 {grid.shape}"
    assert len(extent) == 4
    # 圆外应为 NaN（mask），圆内应有有效值
    assert np.isnan(grid).any(), "头外应 masked 为 NaN"
    assert np.isfinite(grid).any(), "头内应有有效插值"


if __name__ == "__main__":
    test_config64_builds_64ch()
    test_read_aligns_to_min_length()
    test_read_all_dead_fallback()
    test_single_board_regression()
    test_channel_2d_covers_64ch()
    print("ALL PASS: multiboard (4×16ch=64ch)")
