#!/usr/bin/env python3
"""离线分析 CLI：对一个录制目录做时间分辨的 FAA / 分频带功率 / 效价 / 唤醒度。

用法：
    scripts/analyze_session.py <recording_dir> [--window-sec 2.0] [--step-sec 1.0]

`recording_dir` 需含 `session.npz`（raw/filtered/fs/channel_names）与 `meta.json`
（可选，提供 markers / config 快照 / 时长等元信息）。

流程：
  1. 加载 session.npz。若存在 raw，用 SignalChain（带通 0.5-45 + 50Hz 陷波 +
     detrend）对 raw **重新滤波**（保证离线分析自包含、与线上口径一致）；
     若 raw 不存在则退用已保存的 filtered。
  2. 用滑动窗（默认窗长 2s、步长 1s）对整段数据逐窗调用 EmotionAnalyzer.analyze，
     得到时间分辨的 FAA、分频带功率（delta/theta/alpha/beta/gamma）、
     valence / arousal（复用 analysis.py 的 Welch，不重复实现）。
  3. 产出（写回 recording_dir）：
       - trajectories.npz：各指标时间序列 + 时间轴。
       - report.md：真实数字汇总报告 + 单被试辅助指标的诚实说明。

完全 headless，不依赖 GUI。运行（隔离环境）：
    env -i HOME=$HOME PATH=/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin PYTHONPATH= \
        .venv/bin/python scripts/analyze_session.py <recording_dir>
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from eegscope.analysis import DEFAULT_BANDS, EmotionAnalyzer
from eegscope.dsp import SignalChain


def _load_meta(rec_dir: str) -> dict:
    p = os.path.join(rec_dir, "meta.json")
    if os.path.exists(p):
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _load_session(rec_dir: str):
    """返回 (data_dict, meta)。data_dict 含 raw/filtered/fs/channel_names。"""
    npz_path = os.path.join(rec_dir, "session.npz")
    if not os.path.exists(npz_path):
        raise FileNotFoundError(f"未找到 session.npz: {npz_path}")
    z = np.load(npz_path)
    data = {k: z[k] for k in z.files}
    meta = _load_meta(rec_dir)
    return data, meta


def _finite_stats(x: np.ndarray):
    """对一维数组取有限值后算 (mean, std, count)。全 NaN 时返回 (None, None, 0)。"""
    x = np.asarray(x, dtype=np.float64)
    m = np.isfinite(x)
    if not m.any():
        return None, None, 0
    v = x[m]
    return float(np.mean(v)), float(np.std(v)), int(m.sum())


def _fmt(v, nd=4):
    return "NaN" if v is None else f"{v:.{nd}f}"


def run(rec_dir: str, window_sec: float, step_sec: float) -> int:
    data, meta = _load_session(rec_dir)

    fs = float(data.get("fs", meta.get("sample_rate", 250.0)))
    if isinstance(fs, np.ndarray):
        fs = float(fs.reshape(-1)[0])
    channel_names = [str(c) for c in data.get("channel_names", [])]
    if len(channel_names) == 0:
        channel_names = [str(c) for c in meta.get("channel_names", [])]

    raw = data.get("raw")
    filtered = data.get("filtered")

    # 关键口径：离线用 SignalChain 对 raw 重新滤波，与线上口径一致；
    # raw 缺失时退用已保存的 filtered。
    cfg = meta.get("config") or {}
    dsp_cfg = cfg.get("dsp") or {}
    bandpass = tuple(dsp_cfg.get("bandpass_hz", [0.5, 45.0]))
    notch_hz = float(dsp_cfg.get("notch_hz", 50.0))
    notch_q = float(dsp_cfg.get("notch_q", 30.0))

    if raw is not None and raw.size > 0:
        sig = np.asarray(raw, dtype=np.float64)
        chain = SignalChain(fs, bandpass, notch_hz, notch_q)
        sig = chain.process(sig)
        used = "raw(重新滤波)"
    elif filtered is not None and filtered.size > 0:
        sig = np.asarray(filtered, dtype=np.float64)
        used = "filtered(退用)"
    else:
        raise ValueError("session.npz 既无 raw 也无 filtered")

    if sig.ndim == 1:
        sig = sig[None, :]
    n_ch, n = sig.shape
    if len(channel_names) != n_ch:
        # 通道名缺失/不一致时退化为 ch1..chN（不影响频带功率，仅影响 FAA 命名匹配）
        channel_names = [f"ch{i + 1}" for i in range(n_ch)]

    a_cfg = cfg.get("analysis") or {}
    bands = {k: list(v) for k, v in (a_cfg.get("bands") or DEFAULT_BANDS).items()}
    faa_channels = tuple(a_cfg.get("faa_channels", ["F3", "F4"]))

    analyzer = EmotionAnalyzer(fs, channel_names, bands, faa_channels)

    # 滑动窗
    win = int(round(window_sec * fs))
    step = int(round(step_sec * fs))
    if win > n:
        win = n
    if win < 1 or step < 1:
        raise ValueError("窗长/步长非法")
    starts = list(range(0, n - win + 1, step))
    if not starts:
        starts = [0]

    t_axis = []
    faa = []
    faa_ok = []
    valence = []
    arousal = []
    band_power = {b: [] for b in bands}

    for s0 in starts:
        win_data = sig[:, s0:s0 + win]
        m = analyzer.analyze(win_data)
        center = (s0 + win / 2.0) / fs
        t_axis.append(center)
        faa.append(m["faa"] if m["faa_ok"] and m["faa"] is not None else np.nan)
        faa_ok.append(bool(m["faa_ok"]))
        valence.append(m["valence"] if m["valence"] is not None else np.nan)
        arousal.append(m["arousal"] if m["arousal"] is not None else np.nan)
        for b in bands:
            v = m["bands"].get(b)
            band_power[b].append(v if v is not None else np.nan)

    t_axis = np.asarray(t_axis, dtype=np.float64)
    faa = np.asarray(faa, dtype=np.float64)
    faa_ok = np.asarray(faa_ok, dtype=bool)
    valence = np.asarray(valence, dtype=np.float64)
    arousal = np.asarray(arousal, dtype=np.float64)
    band_names = list(bands.keys())
    bp = np.column_stack([band_power[b] for b in band_names]).astype(np.float64)

    # 落盘 trajectories.npz
    np.savez_compressed(
        os.path.join(rec_dir, "trajectories.npz"),
        t_axis=t_axis,
        faa=faa,
        faa_ok=faa_ok,
        valence=valence,
        arousal=arousal,
        band_names=np.array(band_names),
        band_power=bp,
    )

    # 汇总真实数字
    faa_mean, faa_std, faa_n = _finite_stats(faa)
    band_means = {b: float(np.nanmean(band_power[b])) for b in band_names}
    alpha = np.asarray(band_power["alpha"] if "alpha" in band_power else [np.nan])
    alpha_peak_t = float(t_axis[int(np.nanargmax(alpha))]) if np.isfinite(alpha).any() else None

    markers = meta.get("markers") or []
    duration = float(meta.get("duration_seconds", n / fs))

    lines = []
    lines.append("# 会话离线分析报告")
    lines.append("")
    lines.append(f"- **录制目录**：`{os.path.abspath(rec_dir)}`")
    lines.append(f"- **总时长**：{duration:.3f} s（{n} 样本）")
    lines.append(f"- **通道数**：{n_ch}")
    lines.append(f"- **采样率**：{fs:g} Hz")
    lines.append(f"- **数据来源**：{used}（通道名：{', '.join(channel_names)}）")
    lines.append(f"- **marker 数量**：{len(markers)}")
    if markers:
        lines.append("")
        lines.append("### Markers")
        for mk in markers:
            lines.append(f"- @ {mk.get('time_s', 0):.3f}s：{mk.get('label', '')}")
    lines.append("")
    lines.append(f"### 时间分辨指标（滑动窗：窗长 {window_sec:g}s，步长 {step_sec:g}s，共 {len(starts)} 窗）")
    lines.append("")
    lines.append(f"- **FAA**（ln P_F4 − ln P_F3）：均值 **{_fmt(faa_mean)}**，"
                 f"标准差 **{_fmt(faa_std)}**（有限值 {faa_n}/{len(faa)} 窗）")
    if faa_n < len(faa):
        lines.append("  - ⚠️ 部分窗 FAA 为 NaN（F3/F4 缺失或 alpha 功率为 0），已按缺失处理。")
    lines.append("")
    lines.append("### 分频带功率均值（µV²）")
    lines.append("")
    lines.append("| 频带 | 均值 (µV²) |")
    lines.append("|---|---|")
    for b in band_names:
        lines.append(f"| {b} | {_fmt(band_means[b])} |")
    lines.append("")
    lines.append(f"- **alpha 峰值位置**：{_fmt(alpha_peak_t, 3) if alpha_peak_t is not None else 'NaN'} s")
    lines.append("")
    lines.append("> **诚实说明**：以上 FAA / 分频带功率 / 效价 / 唤醒度均为**单被试辅助指标**，")
    lines.append("> 不是本项目主指标。项目主指标是跨被试同步 ISC（`eegscope/isc.py`），")
    lines.append("> 需要多个被试在同一刺激时间轴上的对齐数据，本报告不涉及。单被试指标")
    lines.append("> 仅供链路自检（睁眼/闭眼 alpha 差异）与探索性参考，不用于情绪结论。")

    with open(os.path.join(rec_dir, "report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    # 控制台摘要
    print(f"[analyze] 目录: {rec_dir}")
    print(f"[analyze] 时长={duration:.2f}s, 通道={n_ch}, fs={fs:g}Hz, 数据={used}")
    print(f"[analyze] 窗口: {window_sec:g}s/步{step_sec:g}s → {len(starts)} 窗")
    print(f"[analyze] markers={len(markers)}")
    print(f"[analyze] FAA 均值={_fmt(faa_mean)} ± {_fmt(faa_std)}（有限 {faa_n}/{len(faa)}）")
    print(f"[analyze] 频带功率均值(µV²): "
          + ", ".join(f"{b}={_fmt(band_means[b])}" for b in band_names))
    print(f"[analyze] alpha 峰值 @ {_fmt(alpha_peak_t, 3)} s")
    print(f"[analyze] 已写 trajectories.npz + report.md")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="对录制目录做时间分辨离线分析")
    ap.add_argument("recording_dir", help="录制目录（含 session.npz + meta.json）")
    ap.add_argument("--window-sec", type=float, default=2.0, help="滑动窗长（秒，默认 2.0）")
    ap.add_argument("--step-sec", type=float, default=1.0, help="滑动步长（秒，默认 1.0）")
    args = ap.parse_args(argv)
    return run(args.recording_dir, args.window_sec, args.step_sec)


if __name__ == "__main__":
    raise SystemExit(main())
