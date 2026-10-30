#!/usr/bin/env python3
"""Headless 冒烟测试：SyntheticSource → DSP → Analysis（不弹 GUI）。

运行：见 README「冒烟验证」一节（务必用隔离环境跑）。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from eegscope.analysis import EmotionAnalyzer, welch_psd
from eegscope.config import load_config
from eegscope.dsp import SignalChain
from eegscope.sources import SyntheticSource, make_source

CONFIG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "config.yaml")


def read_seconds(source, seconds, fs, block_sec=0.2):
    block = int(block_sec * fs)
    chunks = []
    remaining = int(seconds * fs)
    while remaining > 0:
        n = min(block, remaining)
        chunks.append(source.read(n))
        remaining -= n
    return np.concatenate(chunks, axis=1)


def main() -> int:
    cfg = load_config(CONFIG)
    fs = cfg.sample_rate
    block = int(0.2 * fs)

    # [1] 数据源
    source = make_source(cfg)
    source.start()
    n_ch = source.n_channels
    print(f"[1] 数据源: {type(source).__name__} | fs={fs} Hz | 通道数={n_ch}")
    print(f"    通道名: {source.channel_names}")

    # [2] 5 秒数据（分块读取，模拟实时流）
    raw = read_seconds(source, 5.0, fs)
    assert raw.shape == (n_ch, int(5 * fs)), f"形状错误: {raw.shape}"
    assert np.isfinite(raw).all(), "raw 含 NaN/Inf"
    print(f"[2] 分块读取 5s 数据 OK | shape={raw.shape} | 无 NaN/Inf")

    # [3] DSP 信号链
    dsp = SignalChain(fs, cfg.dsp.bandpass_hz, cfg.dsp.notch_hz, cfg.dsp.notch_q)
    filtered = dsp.process(raw)
    assert filtered.shape == raw.shape
    assert np.isfinite(filtered).all(), "filtered 含 NaN/Inf"
    # 50Hz 陷波自检：滤后 50Hz 功率应远低于滤前
    freqs_raw, psd_raw = welch_psd(raw.mean(axis=0), fs)
    freqs_f, psd_f = welch_psd(filtered.mean(axis=0), fs)
    i50 = int(np.argmin(np.abs(freqs_f - 50.0)))
    print(f"[3] DSP OK | shape={filtered.shape} | 无 NaN")
    print(f"    50Hz 功率: 滤前 {psd_raw[0, i50]:.2e} -> 滤后 {psd_f[0, i50]:.2e} µV²/Hz")

    # [4] 情绪指标
    analyzer = EmotionAnalyzer(fs, source.channel_names, cfg.analysis.bands,
                               cfg.analysis.faa_channels)
    metrics = analyzer.analyze(filtered[:, -int(2 * fs):])
    print(f"[4] 情绪指标: FAA={metrics['faa']}  faa_ok={metrics['faa_ok']}")
    print(f"    分频带功率(µV²): " + ", ".join(
        f"{k}={v:.3f}" for k, v in metrics["bands"].items()))
    assert metrics["faa_ok"] is True, "16ch 含 F3/F4，FAA 应可计算"
    assert metrics["faa"] is not None
    assert set(cfg.analysis.bands.keys()) == set(metrics["bands"].keys())
    assert all(np.isfinite(v) for v in metrics["bands"].values() if v is not None)

    # [5] 链路自检：闭眼 alpha 上升
    source.set_alpha_level(1.0)
    raw_c = read_seconds(source, 5.0, fs)
    filt_c = dsp.process(raw_c)
    m_closed = analyzer.analyze(filt_c[:, -int(2 * fs):])

    source.set_alpha_level(0.15)
    raw_o = read_seconds(source, 5.0, fs)
    filt_o = dsp.process(raw_o)
    m_open = analyzer.analyze(filt_o[:, -int(2 * fs):])

    print(f"[5] 链路自检: 闭眼 alpha={m_closed['bands']['alpha']:.3f} vs "
          f"睁眼 alpha={m_open['bands']['alpha']:.3f} µV²")
    assert m_closed["bands"]["alpha"] > m_open["bands"]["alpha"], \
        "闭眼 alpha 应上升（链路自检失败）"

    # [6] F3/F4 缺失降级（3 通道：2 EEG + 1 参考）
    source3 = SyntheticSource(sample_rate=fs, channel_names=["F3", "C3", "REF"], seed=1)
    source3.start()
    raw3 = read_seconds(source3, 5.0, fs)
    dsp3 = SignalChain(fs, cfg.dsp.bandpass_hz, cfg.dsp.notch_hz, cfg.dsp.notch_q)
    filt3 = dsp3.process(raw3)
    a3 = EmotionAnalyzer(fs, ["F3", "C3", "REF"], cfg.analysis.bands,
                         cfg.analysis.faa_channels)
    m3 = a3.analyze(filt3[:, -int(2 * fs):])
    print(f"[6] F3/F4 缺失降级: faa_ok={m3['faa_ok']}  faa={m3['faa']}  note={m3['note']!r}")
    assert m3["faa_ok"] is False and m3["faa"] is None, "缺 F4 时应优雅降级"
    assert m3["note"] == "需 F3/F4"

    source.stop()
    source3.stop()
    print("\n✅ 冒烟测试全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
