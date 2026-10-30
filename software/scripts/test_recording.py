#!/usr/bin/env python3
"""Headless 冒烟：合成源模拟录制 → 走「录制的保存逻辑」→ analyze_session 分析。

链路：SyntheticSource → SignalChain → SessionRecorder（与 GUI 同一套保存逻辑）
      → 落盘 recordings/<timestamp>/ → analyze_session.py 子进程 →
      断言 report.md 存在且 FAA / 频带功率都是有限数值、exit 0。

不弹 GUI。运行（隔离环境）：
    env -i HOME=$HOME PATH=/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin PYTHONPATH= \
        .venv/bin/python scripts/test_recording.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from eegscope.config import load_config
from eegscope.dsp import SignalChain
from eegscope.recorder import SessionRecorder, config_snapshot
from eegscope.sources import make_source

SOFTWARE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(SOFTWARE, "config.yaml")
ANALYZE = os.path.join(SOFTWARE, "scripts", "analyze_session.py")


def _clean_env():
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    return env


def main() -> int:
    cfg = load_config(CONFIG)
    fs = cfg.sample_rate
    source = make_source(cfg)
    source.start()
    n_ch = source.n_channels

    tmp = tempfile.mkdtemp(prefix="eegscope_rec_")
    out_dir = None
    try:
        # 用与 GUI 完全相同的 SessionRecorder（同一套保存逻辑）
        rec = SessionRecorder(
            fs, source.channel_names,
            source_type=type(source).__name__,
            config_snapshot=config_snapshot(cfg),
            recordings_dir=tmp,
        )
        rec.start()

        dsp = SignalChain(fs, cfg.dsp.bandpass_hz, cfg.dsp.notch_hz, cfg.dsp.notch_q)
        block = int(0.2 * fs)
        total_blocks = int(round(8.0 * fs / block))
        for bi in range(total_blocks):
            chunk = source.read(block)
            filtered = dsp.process(chunk)
            rec.append(chunk, filtered)
            if bi == int(round(2.0 * fs / block)):  # ~2s 处打一个 marker
                rec.add_marker("测试事件")

        out_dir = rec.stop()
        assert out_dir is not None, "录制应成功落盘"
        assert os.path.exists(os.path.join(out_dir, "session.npz")), "缺 session.npz"
        assert os.path.exists(os.path.join(out_dir, "meta.json")), "缺 meta.json"

        z = np.load(os.path.join(out_dir, "session.npz"))
        assert z["raw"].shape == (n_ch, int(round(8.0 * fs))), \
            f"raw 形状错误: {z['raw'].shape}"
        assert z["filtered"].shape == z["raw"].shape, "raw/filtered 形状不一致"
        assert np.isfinite(z["raw"]).all() and np.isfinite(z["filtered"]).all(), \
            "录制数据含 NaN/Inf"
        print(f"[test_recording] 录制 OK: {z['raw'].shape}, fs={float(z['fs']):g}, "
              f"目录={out_dir}")

        # 跑 analyze_session.py（子进程，验证 headless exit 0）
        r = subprocess.run(
            [sys.executable, ANALYZE, out_dir],
            env=_clean_env(), capture_output=True, text=True)
        print(r.stdout, end="")
        if r.returncode != 0:
            print(r.stderr, file=sys.stderr)
        assert r.returncode == 0, f"analyze_session.py 应 exit 0，实际 {r.returncode}"

        assert os.path.exists(os.path.join(out_dir, "report.md")), "缺 report.md"
        report = open(os.path.join(out_dir, "report.md"), encoding="utf-8").read()
        assert "分频带功率均值" in report and "FAA" in report

        tr = np.load(os.path.join(out_dir, "trajectories.npz"))
        faa = tr["faa"]
        bp = tr["band_power"]
        band_names = [str(b) for b in tr["band_names"]]
        assert np.isfinite(faa).any(), "16ch 含 F3/F4，FAA 至少应有有限值"
        assert np.isfinite(bp).all(), f"频带功率应全为有限值，实际存在 NaN/Inf"
        print(f"[test_recording] analyze OK: FAA 有限值 {int(np.isfinite(faa).sum())}/"
              f"{len(faa)}, 频带 {band_names} 全有限")

        print("\n✅ 录制→离线分析冒烟测试通过")
        return 0
    finally:
        source.stop()
        if out_dir:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
