#!/usr/bin/env python3
"""ERP 离线分析 CLI：对录制目录做事件相关电位（锁时叠加平均）。

用法：
    scripts/analyze_erp.py <recording_dir> [--tmin -0.2] [--tmax 0.8]

`recording_dir` 需含 `session.npz`（raw/filtered/fs/channel_names）与 `meta.json`
（含 `markers` 列表，每个元素 `{"time_s":.., "label":..}`，由「标记」按钮写入）。

流程：
  1. 加载 session.npz，用 SignalChain（带通 0.5-45 + 50Hz 陷波 + detrend）对 raw
     **重新滤波**（与 analyze_session.py 同一口径，保证离线自包含、与线上一致）；
     raw 缺失则退用已保存的 filtered。
  2. 从 meta.json 的 markers 取 `time_s` 作为事件时刻，调 `eegscope.erp.compute_erp`
     做 [tmin, tmax] epoch + 基线校正（减 [tmin,0] 均值）+ 叠加平均。
  3. 产出（写回 recording_dir）：
       - erp.npz：times + erp + channel_names + n_events（+ 总事件数）。
       - erp_report.md：每通道 ERP 峰值潜伏期(ms)/峰幅(µV)、事件数、基线窗，
         以及「单试次信噪比低、需多次叠加平均」的诚实说明。

完全 headless，不依赖 GUI。运行（隔离环境）：
    env -i HOME=$HOME PATH=/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin PYTHONPATH= \\
        .venv/bin/python scripts/analyze_erp.py <recording_dir>
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from eegscope.dsp import SignalChain
from eegscope.erp import compute_erp


def _load_meta(rec_dir: str) -> dict:
    p = os.path.join(rec_dir, "meta.json")
    if os.path.exists(p):
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _load_session(rec_dir: str):
    npz_path = os.path.join(rec_dir, "session.npz")
    if not os.path.exists(npz_path):
        raise FileNotFoundError(f"未找到 session.npz: {npz_path}")
    z = np.load(npz_path)
    data = {k: z[k] for k in z.files}
    meta = _load_meta(rec_dir)
    return data, meta


def _fmt(v, nd=3):
    return "NaN" if v is None else f"{v:.{nd}f}"


def run(rec_dir: str, tmin: float, tmax: float) -> int:
    data, meta = _load_session(rec_dir)

    fs = float(data.get("fs", meta.get("sample_rate", 250.0)))
    if isinstance(fs, np.ndarray):
        fs = float(fs.reshape(-1)[0])
    channel_names = [str(c) for c in data.get("channel_names", [])]
    if len(channel_names) == 0:
        channel_names = [str(c) for c in meta.get("channel_names", [])]

    raw = data.get("raw")
    filtered = data.get("filtered")

    # 关键口径：离线用 SignalChain 对 raw 重新滤波，与线上/analyze_session 一致。
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
        channel_names = [f"ch{i + 1}" for i in range(n_ch)]

    markers = meta.get("markers") or []
    event_times = [float(m.get("time_s", 0.0)) for m in markers
                   if isinstance(m, dict) and m.get("time_s") is not None]

    # 计算 ERP（越界事件在 compute_erp 内部跳过；全部越界则抛 ValueError 清晰报错）
    times, erp = compute_erp(sig, fs, event_times, tmin=tmin, tmax=tmax)

    # 有效事件数 = 非越界事件（与 compute_erp 同一判定口径）
    n_epoch = int(round((tmax - tmin) * fs))
    valid_events = 0
    for et in event_times:
        start = int(round(et * fs + tmin * fs))
        if 0 <= start and start + n_epoch <= n:
            valid_events += 1
    n_events = valid_events

    # 落盘 erp.npz
    np.savez_compressed(
        os.path.join(rec_dir, "erp.npz"),
        times=times,
        erp=erp,
        channel_names=np.array(channel_names),
        n_events=np.int64(n_events),
        n_markers=np.int64(len(event_times)),
    )

    # 每通道峰值：取刺激后 [0, tmax] 内 |erp| 最大的点
    post = times >= 0.0
    peak_lat_ms = []
    peak_amp = []
    for i in range(n_ch):
        if not post.any():
            peak_lat_ms.append(None)
            peak_amp.append(None)
            continue
        seg = erp[i][post]
        k = int(np.argmax(np.abs(seg)))
        peak_amp.append(float(seg[k]))
        peak_lat_ms.append(float(times[post][k] * 1000.0))

    # 写 erp_report.md
    lines = []
    lines.append("# ERP 事件相关电位报告")
    lines.append("")
    lines.append(f"- **录制目录**：`{os.path.abspath(rec_dir)}`")
    lines.append(f"- **数据来源**：{used}（通道数 {n_ch}，采样率 {fs:g} Hz）")
    lines.append(f"- **事件数**：{n_events} 个有效事件 / 共 {len(event_times)} 个标记"
                 + ("（越界事件已跳过）" if n_events != len(event_times) else ""))
    lines.append(f"- **epoch 窗**：[{tmin:g}, {tmax:g}] s")
    lines.append(f"- **基线窗**：[{tmin:g}, 0] s（每试次减去该段均值做基线校正）")
    lines.append("")
    lines.append("## 每通道 ERP 峰值（刺激后 0~tmax 内 |幅值| 最大点）")
    lines.append("")
    lines.append("| 通道 | 峰值潜伏期 (ms) | 峰幅 (µV) |")
    lines.append("|---|---|---|")
    for i, nm in enumerate(channel_names):
        lines.append(f"| {nm} | {_fmt(peak_lat_ms[i])} | {_fmt(peak_amp[i])} |")
    lines.append("")
    lines.append("> **诚实说明**：单试次 ERP 信噪比极低（诱发信号常远小于自发脑电 / "
                 "噪声），")
    lines.append("> 必须对**多次锁时叠加平均**才能恢复；事件数越少，上述峰值越不可靠。")
    lines.append("> 本报告只做叠加平均这一件事，不据此做任何情绪 / 认知结论；")
    lines.append("> ERP 峰值潜伏期与幅值仅作链路自检与后续接真实刺激标记时的参考口径。")

    with open(os.path.join(rec_dir, "erp_report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    # 控制台摘要
    print(f"[erp] 目录: {rec_dir}")
    print(f"[erp] 数据={used}, 通道={n_ch}, fs={fs:g}Hz")
    print(f"[erp] 有效事件={n_events}/{len(event_times)}, epoch=[{tmin:g},{tmax:g}]s, "
          f"基线=[{tmin:g},0]s")
    for i, nm in enumerate(channel_names):
        print(f"[erp]   {nm:>4}: 峰潜伏期 {_fmt(peak_lat_ms[i])} ms, "
              f"峰幅 {_fmt(peak_amp[i])} µV")
    print(f"[erp] 已写 erp.npz + erp_report.md")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="对录制目录做 ERP 锁时叠加平均")
    ap.add_argument("recording_dir", help="录制目录（含 session.npz + meta.json）")
    ap.add_argument("--tmin", type=float, default=-0.2, help="epoch 起点（秒，默认 -0.2）")
    ap.add_argument("--tmax", type=float, default=0.8, help="epoch 终点（秒，默认 0.8）")
    args = ap.parse_args(argv)
    return run(args.recording_dir, args.tmin, args.tmax)


if __name__ == "__main__":
    raise SystemExit(main())
