#!/usr/bin/env python3
"""端到端同步测试：走「正常录制入口」（MainWindow 录制按钮路径），
而非手动调用各对象。验证：开始录制 → 采集 tick → 停止录制 后，
sync.json 真的写入了眼动样本、每板帧统计、逐帧记录、缺口信息。

另含离线分析合成测试：注入已知偏移/漂移/丢帧 → analyze_sync 测回，
并确认「绝对延迟/真实采样偏移/硬件精度」标为不可识别(None)。

run: .venv/bin/python scripts/test_sync_e2e.py   （exit 0 = 全过）
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

_PASS = 0
_FAIL = 0


def _ck(cond: bool, name: str) -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  PASS {name}")
    else:
        _FAIL += 1
        print(f"  FAIL {name}")


# ---- 1. 端到端：MainWindow 正常录制入口 ----
def test_e2e_recording_entry() -> None:
    print("[1] 端到端：MainWindow 录制入口")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    from eegscope import app as appmod
    from eegscope.config import load_config
    from eegscope.sources.multiboard import MultiBoardSource
    from eegscope.sources.eye import SimulatedEyeTracker
    from eegscope.sync import StimulusScheduler

    # 用临时录制目录，避免污染 software/recordings
    tmp_rec = tempfile.mkdtemp(prefix="e2e_rec_")
    old_dir = appmod._RECORDINGS_DIR
    appmod._RECORDINGS_DIR = tmp_rec

    cfg = load_config("config64.yaml")   # 64 通道
    # 4 块假板（不 start 串口，手动喂帧填充 frame_stats + frame_log）
    mb = MultiBoardSource(ports=["COM1", "COM2", "COM3", "COM4"],
                          n_channels_per_board=16, channel_names=cfg.channels)
    eye = SimulatedEyeTracker(sample_rate=120.0, offset_s=0.25, drift_ppm=0.0,
                              loss_rate=0.0, invalid_rate=0.0, seed=3)

    win = appmod.MainWindow(cfg, mb, eye_tracker=eye)
    win._start_recording()
    for bi, b in enumerate(mb._boards):
        for c in range(200):
            b._update_frame_stats(c, time.monotonic() + bi * 1e-6)
    _ck(win.recorder is not None and win.recorder.is_recording, "开始录制后 recorder 就绪")
    assert win.recorder is not None
    _ck(win.recorder.timeline is win.timeline, "SyncTimeline 已传入 recorder")

    # 刺激调度器：挂在录制的时间线上，计划时间相对录制起点
    sched = StimulusScheduler(win.timeline)
    sched.schedule("stim_onset_1", planned_s=0.05)
    sched.schedule("stim_onset_2", planned_s=0.12)

    # 手动驱动 _tick 几次（绕过 QTimer，确定性）
    for _ in range(10):
        for board in mb._boards:
            for _sample in range(7):
                board._queue.put(np.ones(16))
        sched.tick()      # 记录已到计划时刻的刺激事件（软件记录时间）
        win._tick()
        time.sleep(0.02)   # 让眼动模拟器按真实时间产样本
        app.processEvents()

    win._stop_recording()
    appmod._RECORDINGS_DIR = old_dir

    # 找到刚落盘的目录
    subdirs = [d for d in os.listdir(tmp_rec) if os.path.isdir(os.path.join(tmp_rec, d))]
    _ck(len(subdirs) >= 1, "录制落盘成功")
    out = os.path.join(tmp_rec, subdirs[0])
    sync_path = os.path.join(out, "sync.json")
    _ck(os.path.exists(sync_path), "sync.json 存在（正常入口写入）")

    with open(sync_path, encoding="utf-8") as f:
        sync = json.load(f)
    kinds = [e["kind"] for e in sync["timeline"]]
    _ck("eye_sample" in kinds, "时间线含眼动样本（正常入口写入）")
    _ck(len(sync["board_stats_snapshots"]) > 0, "每板帧统计快照已记录（正常入口写入）")
    _ck(len(sync.get("frame_logs", [])) == 4, f"逐帧记录 4 板（实测 {len(sync.get('frame_logs', []))}）")
    _ck(len(sync.get("gap_log", [])) > 0, "缺口信息已记录（假板 read 欠采 → 显式标记）")
    _ck("clock_note" in sync and "start_monotonic" in sync, "时钟说明 + 起点已记录")
    _ck(eye._started is False, "停止录制后眼动仪已停")

    # 刺激事件：计划时间/软件记录时间/屏幕发光时间三分离
    stim_evts = [e for e in sync["timeline"] if e["kind"] == "stimulus"]
    _ck(len(stim_evts) >= 2, f"刺激事件已记录（实测 {len(stim_evts)}）")
    if stim_evts:
        s0 = stim_evts[0]["data"]
        _ck("planned_s" in s0 and "screen_onset_s" in s0 and "screen_onset_known" in s0,
            "刺激三时间字段分离（planned_s / t_recv / screen_onset_s）")
        _ck(s0["screen_onset_known"] is False and s0["screen_onset_s"] is None,
            "屏幕发光时间未知（无光电测量，不冒充已显示）")

    # 重新读取会话，检查数据完整性
    meta_path = os.path.join(out, "meta.json")
    npz_path = os.path.join(out, "session.npz")
    _ck(os.path.exists(meta_path) and os.path.exists(npz_path), "meta.json + session.npz 已落盘")
    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)
    npz = np.load(npz_path)
    _ck(meta.get("n_samples", 0) > 0, f"meta n_samples>0（实测 {meta.get('n_samples')}）")
    _ck(npz["raw"].shape[1] == meta["n_samples"],
        f"session.npz raw 样本数 = meta（实测 {npz['raw'].shape[1]} vs {meta['n_samples']}）")
    _ck(npz["raw"].shape[0] == 64, f"64 通道（实测 {npz['raw'].shape[0]}）")


# ---- 2. 离线分析：测回注入的偏移/漂移 + 不可识别量标 None ----
def test_offline_analysis() -> None:
    print("[2] 离线分析 analyze_sync")
    from eegscope.sync_analysis import analyze_sync

    # 构造一条已知偏移/漂移的时间线（device_ts ≈ (1+drift)·t_recv + offset）
    OFFSET, DRIFT_PPM = 0.500, 50000.0
    sync = {
        "timeline": [{"kind": "eye_sample", "t_recv": i * 0.005,
                      "data": {"device_ts": i * 0.005 * (1 + DRIFT_PPM * 1e-6) + OFFSET,
                               "device_ts_unit": "s", "segment_id": 0,
                               "clock_source": "sim", "x": 0.5, "y": 0.5, "valid": True}}
                     for i in range(500)],
        "frame_logs": [],
        "gap_log": [],
    }
    rep = analyze_sync(sync)
    drift = rep["eye"]["apparent_drift_ppm"]
    offset = rep["eye"]["device_vs_host_intercept_s"]
    _ck(drift is not None and abs(drift - DRIFT_PPM) < 2000,
        f"测回表观漂移 ≈ {DRIFT_PPM}ppm（实测 {drift:.0f}ppm）")
    _ck(offset is not None and abs(offset - OFFSET) < 0.02,
        f"测回截距 ≈ {OFFSET}s（实测 {offset:.3f}s）")
    # 不可识别量必须显式标 None + 原因
    un = rep["unidentifiable"]
    _ck(un["absolute_end_to_end_latency_s"] is None
        and un["true_sampling_offset_s"] is None
        and un["hardware_sync_precision_s"] is None
        and "reason" in un,
        "绝对延迟/真实采样偏移/硬件精度 标为不可识别(None)+原因")


def main() -> int:
    test_e2e_recording_entry()
    test_offline_analysis()
    print(f"\n=== {_PASS} passed, {_FAIL} failed ===")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
