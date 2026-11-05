"""SSVEP 离线识别 —— 对 eegscope 录制的 session.npz 做高小榕算法频率识别。

用法（隔离环境）：
    env -i HOME=$HOME PATH=/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin PYTHONPATH= \\
        .venv/bin/python scripts/analyze_ssvep.py <session.npz> \\
        --freqs 8.0,9.0,10.0,11.0,12.0 [--method cca|fbcca|trca] \\
        [--window 2.0] [--step 0.5] [--occ-only]

说明：
  - 对 session 的 filtered 数据做滑动窗口频率识别，输出每个窗口识别到的频率 + 置信度，
    以及整段数据里各频率被识别的占比（可视化哪段数据对应哪个刺激频率）。
  - TRCA 需要「校准数据」：用 --calib <session.npz> 指定一段已知频率的录制来训练。
  - SSVEP 信号主要在枕区，--occ-only 时只用通道名以 O 开头的通道（O1/O2/Oz 等）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eegscope.ssvep import cca_classify, fbcca_classify, trca_classify, trca_train  # noqa: E402


def load_session(path: str):
    d = np.load(path)
    return d["filtered"], float(d["fs"]), list(d["channel_names"])


def occ_indices(channel_names):
    return [i for i, nm in enumerate(channel_names) if str(nm).upper().startswith("O")]


def run_sweep(data, fs, freqs, method, window, step, occ_only, channel_names):
    """滑动窗口识别，返回 (times, targets, scores)。"""
    n_ch, n = data.shape
    win = int(round(window * fs))
    stp = int(round(step * fs))
    if occ_only:
        idx = occ_indices(channel_names)
        if not idx:
            print("警告：没有以 O 开头的通道，退回全通道", file=sys.stderr)
        else:
            data = data[idx, :]
    times, targets, scores = [], [], []
    for start in range(0, n - win + 1, stp):
        seg = data[:, start:start + win]
        if method == "trca":
            raise ValueError("TRCA 需 --calib 校准数据，见 --help")
        elif method == "fbcca":
            corrs, idx2 = fbcca_classify(seg, fs, freqs)
        else:
            corrs, idx2 = cca_classify(seg, fs, freqs)
        times.append((start + win / 2.0) / fs)
        targets.append(freqs[idx2])
        scores.append(float(corrs[idx2]))
    return (np.asarray(times), np.asarray(targets), np.asarray(scores))


def main():
    ap = argparse.ArgumentParser(description="SSVEP 离线识别（高小榕 CCA/FBCCA/TRCA）")
    ap.add_argument("session", help="eegscope 录制的 session.npz 路径")
    ap.add_argument("--freqs", required=True, help="候选刺激频率（Hz），逗号分隔")
    ap.add_argument("--method", default="cca", choices=["cca", "fbcca", "trca"])
    ap.add_argument("--window", type=float, default=2.0, help="识别窗长（秒）")
    ap.add_argument("--step", type=float, default=0.5, help="滑动步长（秒）")
    ap.add_argument("--occ-only", action="store_true", help="只用枕区（O 开头）通道")
    ap.add_argument("--calib", default=None, help="TRCA 校准 session.npz（已知频率录制）")
    ap.add_argument("--calib-freqs", default=None, help="校准数据的频率（逗号分隔，与 --calib 配合）")
    ap.add_argument("--out", default=None, help="结果 JSON 输出路径")
    args = ap.parse_args()

    freqs = [float(x) for x in args.freqs.split(",") if x.strip()]
    data, fs, channel_names = load_session(args.session)
    print(f"[analyze_ssvep] 数据 {data.shape[0]}ch × {data.shape[1]} 样本 @ {fs}Hz，"
          f"方法={args.method}，候选频率={freqs}")

    if args.method == "trca":
        if not args.calib:
            raise SystemExit("TRCA 需要 --calib <校准 session.npz> 和 --calib-freqs")
        calib_data, calib_fs, calib_ch = load_session(args.calib)
        calib_freqs = [float(x) for x in args.calib_freqs.split(",") if x.strip()]
        # 校准：把校准 session 按窗口切成每个频率的多个 trial
        trials = []
        for f in calib_freqs:
            # 简单做法：把校准数据按窗口切块，每块当作该频率的一个 trial
            # （真实使用需按 marker 对齐，这里提供 demo 路径）
            win = int(round(args.window * calib_fs))
            tr = [calib_data[:, i:i + win] for i in range(0, calib_data.shape[1] - win + 1, win)]
            trials.append(tr)
        model = trca_train(trials, calib_freqs)
        # TRCA 识别走逐窗
        win = int(round(args.window * fs))
        stp = int(round(args.step * fs))
        times, targets, scores = [], [], []
        for start in range(0, data.shape[1] - win + 1, stp):
            seg = data[:, start:start + win]
            corrs, idx2 = trca_classify(seg, calib_freqs, model)
            times.append((start + win / 2.0) / fs)
            targets.append(calib_freqs[idx2])
            scores.append(float(corrs[idx2]))
        times = np.asarray(times); targets = np.asarray(targets); scores = np.asarray(scores)
    else:
        times, targets, scores = run_sweep(
            data, fs, freqs, args.method, args.window, args.step, args.occ_only, channel_names)

    # 统计各频率占比
    freq_counts = {f: int((targets == f).sum()) for f in freqs}
    dominant = max(freq_counts, key=freq_counts.get)
    result = {
        "method": args.method,
        "freqs": freqs,
        "n_windows": int(len(times)),
        "dominant_freq": float(dominant),
        "freq_counts": freq_counts,
        "times": times.tolist(),
        "targets": targets.tolist(),
        "scores": scores.tolist(),
    }
    print(f"[analyze_ssvep] 共 {len(times)} 个窗口，主导频率 {dominant}Hz，"
          f"占比 {freq_counts[dominant]}/{len(times)}")
    print(f"[analyze_ssvep] 各频率识别次数: {freq_counts}")

    if args.out:
        Path(args.out).write_text(json.dumps(result, indent=2, ensure_ascii=False))
        print(f"[analyze_ssvep] 结果已写 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
