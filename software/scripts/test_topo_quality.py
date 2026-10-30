#!/usr/bin/env python3
"""Headless 测试：脑地形图频带功率 + 信号质量 + 插值（不弹 GUI）。

覆盖：
  [1] band_power_map 算 alpha 频带 → 枕区 O1/Oz/O2 功率显著高于额区 F3/F4
      （合成源枕区 alpha 最强，证伪积分/插值实现错误）。
  [2] 注入全零（平线）+ 超大幅值（饱和）通道 → 质量检测标「坏」，其余不误标。
  [3] topo_interpolate：网格形状正确、圆外 NaN/mask、朝向正确、无 crash。

运行（隔离环境）：
    env -i HOME=$HOME PATH=/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin PYTHONPATH= \
        .venv/bin/python scripts/test_topo_quality.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from eegscope.analysis import band_power_map
from eegscope.config import load_config
from eegscope.dsp import SignalChain
from eegscope.sources import SyntheticSource
from eegscope.viz import CHANNEL_2D, compute_channel_quality, topo_interpolate

SOFTWARE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(SOFTWARE, "config.yaml")


def read_seconds(source, seconds, fs, block_sec=0.2):
    block = int(block_sec * fs)
    chunks, remaining = [], int(seconds * fs)
    while remaining > 0:
        n = min(block, remaining)
        chunks.append(source.read(n))
        remaining -= n
    return np.concatenate(chunks, axis=1)


def test_band_power_map():
    cfg = load_config(CONFIG)
    fs = cfg.sample_rate
    # 闭眼（alpha 强），让枕/额对比更稳
    src = SyntheticSource(sample_rate=fs, channel_names=cfg.channels,
                          seed=42, alpha_level=1.0)
    src.start()
    try:
        raw = read_seconds(src, 5.0, fs)
        dsp = SignalChain(fs, cfg.dsp.bandpass_hz, cfg.dsp.notch_hz, cfg.dsp.notch_q)
        filt = dsp.process(raw)
        window = filt[:, -int(2 * fs):]  # 与 app 分析窗一致（2s）

        alpha = band_power_map(window, fs, 8.0, 13.0)  # (n_ch,)
        assert alpha.shape == (len(cfg.channels),), f"形状错误 {alpha.shape}"

        idx = {nm.upper(): i for i, nm in enumerate(cfg.channels)}
        occ = float(np.mean([alpha[idx["O1"]], alpha[idx["OZ"]], alpha[idx["O2"]]]))
        frn = float(np.mean([alpha[idx["F3"]], alpha[idx["F4"]]]))
        print(f"[1] band_power_map alpha (µV²): 枕区均值={occ:.3f}  额区均值={frn:.3f}  "
              f"比值={occ / max(frn, 1e-12):.2f}×")
        for nm in ("O1", "OZ", "O2", "F3", "F4"):
            print(f"      {nm} = {alpha[idx[nm]]:.3f} µV²")
        assert occ > frn * 1.5, \
            f"枕区 alpha 应显著高于额区（实测 {occ:.3f} vs {frn:.3f}）"
        assert np.isfinite(alpha).all(), "alpha 功率应全为有限值"
        print("      ✅ 枕区 alpha 显著高于额区")
    finally:
        src.stop()


def test_quality():
    cfg = load_config(CONFIG)
    fs = cfg.sample_rate
    src = SyntheticSource(sample_rate=fs, channel_names=cfg.channels,
                          seed=7, alpha_level=0.5)
    src.start()
    try:
        raw = read_seconds(src, 5.0, fs)
        dsp = SignalChain(fs, cfg.dsp.bandpass_hz, cfg.dsp.notch_hz, cfg.dsp.notch_q)
        filt = dsp.process(raw)
        window = filt[:, -int(2 * fs):].copy()
        n = window.shape[1]

        # 注入两个坏通道：ch0 全零（平线）、ch1 超大幅值（饱和）
        rng = np.random.default_rng(123)
        window[0, :] = 0.0
        window[1, :] = np.clip(300.0 * rng.standard_normal(n), -200.0, 200.0)

        results = compute_channel_quality(window, fs)
        assert len(results) == len(cfg.channels)

        flat_name = cfg.channels[0]
        sat_name = cfg.channels[1]
        status = {cfg.channels[i]: results[i]["status"] for i in range(len(results))}

        print(f"[2] 信号质量: 注入 {flat_name}=平线, {sat_name}=饱和")
        for i, r in enumerate(results):
            nm = cfg.channels[i]
            mark = "  <-- 注入" if i in (0, 1) else ""
            print(f"      {nm:>4}: {r['status']:<4} rms={r['rms']:6.1f}µV "
                  f"std={r['std']:6.2f}µV 50Hz={r['mains']:.2e} "
                  f"flat={int(r['flat'])} sat={int(r['saturated'])}{mark}")
        assert status[flat_name] == "bad", f"{flat_name} 平线应判 bad，实际 {status[flat_name]}"
        assert status[sat_name] == "bad", f"{sat_name} 饱和应判 bad，实际 {status[sat_name]}"
        for i in range(2, len(results)):
            nm = cfg.channels[i]
            assert results[i]["status"] != "bad", \
                f"正常通道 {nm} 不应被误标为 bad（{results[i]['status']}: {results[i]['reasons']}）"
        print("      ✅ 平线/饱和判 bad，其余正常通道未误标")
    finally:
        src.stop()


def test_interpolate():
    cfg = load_config(CONFIG)
    names = cfg.channels

    # 已知通道值：枕区 O1/Oz/O2=1.0，其余=0
    vals = np.zeros(len(names))
    for nm, v in [("O1", 1.0), ("Oz", 1.0), ("O2", 1.0)]:
        i = [k for k, c in enumerate(names) if c.upper() == nm.upper()][0]
        vals[i] = v

    grid, extent = topo_interpolate(names, vals, grid_n=64)
    assert grid.shape == (64, 64), f"网格形状错误 {grid.shape}"
    x0, x1, y0, y1 = extent
    assert x0 < x1 and y0 < y1, f"extent 非法 {extent}"

    # 圆外应为 NaN：四角距圆心 sqrt(1.15²+1.15²)≈1.63 > 1
    assert np.isnan(grid[0, 0]) and np.isnan(grid[0, -1])
    assert np.isnan(grid[-1, 0]) and np.isnan(grid[-1, -1])
    # 圆心应在圆内且被插值（非 NaN）
    c = grid.shape[0] // 2
    assert not np.isnan(grid[c, c]), "圆心应为有效插值（非 NaN）"

    def grid_value(x, y):
        """按数据坐标 (x,y) 查询插值网格值（grid 第 0 行=y1 额顶，末行=y0 枕）。"""
        col = int(round((x - x0) / (x1 - x0) * (grid.shape[1] - 1)))
        row = int(round((y1 - y) / (y1 - y0) * (grid.shape[0] - 1)))
        return grid[row, col]

    # 朝向正确：枕区(y<0)应高于额区(y>0)。取电极间采样点验证插值而非简单回传。
    occ = float(grid_value(0.0, -0.70))   # 介于 Oz(1.0) 与 Pz(0) 之间
    frn = float(grid_value(0.0, 0.55))    # 靠近 Fz(0)
    print(f"[3] topo_interpolate: shape={grid.shape} extent=({x0:.2f},{x1:.2f},"
          f"{y0:.2f},{y1:.2f})")
    print(f"      枕区采样点(0,-0.70)={occ:.3f}  额区采样点(0,+0.55)={frn:.3f} "
          f"(枕区应更高)")
    assert occ > frn, "枕区插值应高于额区（朝向错误）"

    # 无坐标通道应跳过、不报错
    names_extra = names + ["REF", "M1"]
    grid2, _ = topo_interpolate(names_extra, list(vals) + [0.5, 0.5], grid_n=32)
    assert grid2.shape == (32, 32)
    assert np.isnan(grid2[0, 0]), "额外无坐标通道不影响圆外 mask"

    print("      ✅ 网格形状/圆外 mask/朝向/跳过无坐标通道 全部 OK")


def main() -> int:
    print("=== test_topo_quality (headless) ===")
    test_band_power_map()
    test_quality()
    test_interpolate()
    print("\n✅ test_topo_quality 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
