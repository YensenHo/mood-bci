#!/usr/bin/env python3
"""时间同步基础自测：用模拟数据验证「记录 + 事后校验」整条链路。

无硬件、无眼动仪：SimulatedEyeTracker（可注入偏移/漂移/丢帧/无效段）+
手动帧编号 + 刺激事件，验证：

1. SyncTimeline 追加式记录三类事件、时间单调不减。
2. OpenBCISource 帧编号丢帧检测（字节流里故意挖掉帧）。
3. SimulatedEyeTracker 的固定偏移 / 时钟漂移 / 丢帧 / 无效段。
4. SessionRecorder 把时间线 + 每板帧统计落成 sync.json。
5. 事后校验能「测回」注入的偏移与漂移（可复现的同步验证）。

run: .venv/bin/python scripts/test_sync.py   （exit 0 = 全过）
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from eegscope.sync import SyncTimeline, StimulusScheduler
from eegscope.sources.eye import SimulatedEyeTracker
from eegscope.sources.openbci import OpenBCISource, parse_cyton_frame
from eegscope.sources.multiboard import MultiBoardSource
from eegscope.recorder import SessionRecorder

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


# ---- 1. Timeline 基础 ----
def test_timeline_basics() -> None:
    print("[1] SyncTimeline 基础")
    tl = SyncTimeline()
    tl.start()
    tl.record_eeg_frame(board=1, sample_counter=0, recv_monotonic=None)
    tl.record_eye_sample(device_ts=1.0, clock_source="sim", x=0.5, y=0.5,
                         valid=True, recv_monotonic=None)
    tl.record_stimulus("s0", planned_s=2.0)
    evs = tl.events()
    _ck(len(evs) == 3, "三类事件各记一条")
    _ck([e["kind"] for e in evs] == ["eeg_frame", "eye_sample", "stimulus"],
        "事件顺序正确")
    ts = [e["t_recv"] for e in evs]
    _ck(all(ts[i] <= ts[i + 1] for i in range(len(ts) - 1)), "t_recv 单调不减")
    s = tl.summary()
    _ck(s["n_events"] == 3 and s["by_kind"]["stimulus"] == 1, "summary 计数正确")


# ---- 2. 帧编号丢帧检测 + 语义分类 ----
def test_frame_drop_detection() -> None:
    print("[2] 帧编号丢帧检测 + 语义分类")
    src = OpenBCISource(port="COM_TEST", n_channels=8)  # 不 start，直接测统计
    # 连续帧 0..9，但 5、6 被挖掉 → 应检测到 2 帧丢
    for c in [0, 1, 2, 3, 4, 7, 8, 9]:
        src._update_frame_stats(c, float(c))
    st = src.frame_stats()
    _ck(st["n_frames"] == 8, "帧计数正确")
    _ck(st["n_dropped_est"] == 2, "挖掉 2 帧 → 丢帧=2")
    # 回绕：254,255,0,1 不应误判丢帧
    src2 = OpenBCISource(port="COM_TEST2", n_channels=8)
    for c in [254, 255, 0, 1]:
        src2._update_frame_stats(c, 0.0)
    _ck(src2.frame_stats()["n_dropped_est"] == 0, "帧编号回绕不误判丢帧")
    # 重复帧（diff=0）与整圈丢失无法区分 → dup_or_unknown，不误判丢帧
    src3 = OpenBCISource(port="C3", n_channels=8)
    for c in [0, 1, 1, 2]:
        src3._update_frame_stats(c, 0.0)
    _ck(src3.frame_stats()["n_dup_or_unknown"] == 1 and src3.frame_stats()["n_dropped_est"] == 0,
        "重复帧→dup_or_unknown（不误判丢帧）")
    # 乱序/重启（diff>=128 回退太多）→ out_of_order，不计入丢帧
    src4 = OpenBCISource(port="C4", n_channels=8)
    for c in [0, 1, 0]:   # 1→0 回退 255
        src4._update_frame_stats(c, 0.0)
    _ck(src4.frame_stats()["n_out_of_order"] == 1 and src4.frame_stats()["n_dropped_est"] == 0,
        "乱序/重启→out_of_order（不计入丢帧）")
    # 16ch 有线 Daisy：byte1 每帧 +1（0,1,2,3...），不误判
    src5 = OpenBCISource(port="C5", n_channels=16)
    for c in range(40):
        src5._update_frame_stats(c, 0.0)
    _ck(src5.frame_stats()["n_dropped_est"] == 0 and src5.frame_stats()["n_frames"] == 40,
        "16ch Daisy byte1 每帧+1 不误判")


# ---- 3. 眼动模拟器：偏移/漂移/丢帧/无效 ----
def test_simulated_eye() -> None:
    print("[3] SimulatedEyeTracker")
    import time as _t
    # 偏移 0.5s、无漂移、无丢帧、无无效段
    et = SimulatedEyeTracker(sample_rate=100.0, offset_s=0.5, drift_ppm=0.0,
                             loss_rate=0.0, invalid_rate=0.0, seed=42)
    et.start()
    _t.sleep(0.5)
    samples = et.read()
    et.stop()
    _ck(len(samples) >= 45, f"0.5s@100Hz → ~50 样本（实测 {len(samples)}）")
    _ck(abs(samples[0].device_ts - 0.5) < 1e-6,
        f"固定偏移 0.5s 可测（首样本 device_ts={samples[0].device_ts:.4f}）")
    dts = [s.device_ts for s in samples]
    _ck(all(dts[i] < dts[i + 1] for i in range(len(dts) - 1)), "device_ts 单调递增")

    # 漂移 50000ppm(5%)：设备时钟更快 → 同样 0.5s 产更多样本
    et2 = SimulatedEyeTracker(sample_rate=100.0, offset_s=0.0, drift_ppm=50000.0,
                              loss_rate=0.0, invalid_rate=0.0, seed=1)
    et2.start()
    _t.sleep(0.5)
    s2 = et2.read()
    et2.stop()
    _ck(len(s2) > 50, f"5%漂移 → 产更多样本（实测 {len(s2)} > 50）")

    # 丢帧 + 无效段
    et3 = SimulatedEyeTracker(sample_rate=200.0, loss_rate=0.5, invalid_rate=0.5, seed=7)
    et3.start()
    _t.sleep(0.5)
    got3 = et3.read()
    et3.stop()
    n_valid3 = sum(1 for s in got3 if s.valid)
    _ck(0 < len(got3) < 100, f"丢帧生效（{len(got3)} < ~100）")
    _ck(0 < n_valid3 < len(got3), f"无效段生效（{n_valid3}/{len(got3)} valid）")


# ---- 4. 录制落盘 sync.json ----
def test_recorder_sync_json() -> None:
    print("[4] SessionRecorder 落 sync.json")
    tl = SyncTimeline()
    tl.start()
    rec = SessionRecorder(250.0, ["F3", "F4"], source_type="synthetic",
                          timeline=tl,
                          recordings_dir=tempfile.mkdtemp(prefix="sync_test_"))
    rec.start()
    # 录几块合成数据
    rng = np.random.default_rng(0)
    for _ in range(5):
        rec.append(rng.standard_normal((2, 100)) * 20, rng.standard_normal((2, 100)) * 20)
        rec.record_board_stats([{"n_frames": 100, "n_dropped_est": 0,
                                 "last_counter": 99, "first_recv_s": 0.0, "last_recv_s": 0.4}])
        tl.record_eeg_frame(board=1, sample_counter=99, recv_monotonic=None)
        tl.record_eye_sample(device_ts=0.4, clock_source="sim", x=0.5, y=0.5,
                             valid=True, recv_monotonic=None)
    out = rec.stop()
    _ck(out is not None, "录制落盘成功")
    assert out is not None
    with open(os.path.join(out, "sync.json"), encoding="utf-8") as f:
        sync = json.load(f)
    _ck("timeline" in sync and "board_stats_snapshots" in sync, "sync.json 结构完整")
    _ck(len(sync["timeline"]) == 10, "时间线事件数=10（5 eeg + 5 eye）")
    _ck(len(sync["board_stats_snapshots"]) == 5, "每板帧统计快照=5")
    _ck("clock_note" in sync, "clock_note 说明接收时间≠采样时间")


# ---- 5. 可复现的同步校验：注入偏移/漂移，事后测回 ----
def test_sync_verification_recovery() -> None:
    print("[5] 同步校验：测回注入的偏移/漂移/丢帧率")
    import time as _t
    # (a) 偏移 + 漂移（无丢帧，polyfit 干净）
    OFFSET, DRIFT_PPM = 0.500, 50000.0     # 5% 漂移，明显可测
    et = SimulatedEyeTracker(sample_rate=200.0, offset_s=OFFSET, drift_ppm=DRIFT_PPM,
                             loss_rate=0.0, invalid_rate=0.0, seed=1)
    et.start()
    t0 = _t.monotonic()
    rows = []          # (recv_rel, device_ts)
    dur = 1.0
    while (_t.monotonic() - t0) < dur:
        for s in et.read():
            if s.valid and s.device_ts is not None:
                rows.append((_t.monotonic() - t0, s.device_ts))
        _t.sleep(0.01)
    et.stop()

    _ck(len(rows) > 0, "收到有效眼动样本")
    rx = np.array([r[0] for r in rows], dtype=float)
    dy = np.array([r[1] for r in rows], dtype=float)
    slope, intercept = np.polyfit(rx, dy, 1)
    # device_ts ≈ (1+drift)·host + offset → 斜率≈1+drift，截距≈offset
    _ck(abs(slope - (1.0 + DRIFT_PPM * 1e-6)) < 0.01,
        f"测回时钟漂移 ≈ {DRIFT_PPM}ppm（实测斜率 {slope:.4f}）")
    _ck(abs(intercept - OFFSET) < 0.05,
        f"测回固定偏移 ≈ {OFFSET}s（实测截距 {intercept:.3f}s）")

    # (b) 丢帧率（无漂移，30% 丢帧）
    et2 = SimulatedEyeTracker(sample_rate=200.0, loss_rate=0.3,
                              invalid_rate=0.0, seed=2)
    et2.start()
    _t.sleep(0.5)
    got = [s for s in et2.read() if s.valid]
    et2.stop()
    nominal = int(0.5 * 200)   # 100
    _ck(0 < len(got) < nominal * 0.85, f"30%丢帧可检测（收到 {len(got)} < 名义 {nominal}）")


# ---- 6. 跨板截断 + 掉线缺口（点3：不静默丢未消费样本） ----
def test_multiboard_truncation() -> None:
    print("[6] MultiBoard 跨板截断 + 掉线缺口标记")
    mb = MultiBoardSource(ports=["A", "B"], n_channels_per_board=8,
                          channel_names=[f"c{i}" for i in range(16)])
    # 板A 塞 5 个样本、板B 塞 3 个（模拟两板采样长度不等）
    for _ in range(5):
        mb._boards[0]._queue.put(np.zeros(8))
    for _ in range(3):
        mb._boards[1]._queue.put(np.ones(8))
    out = mb.read(10)
    info = mb.read_gap_info()
    _ck(out.shape == (16, 3), f"截断到最短长度 3（实测 {out.shape}）")
    _ck(info["per_board_truncated"] == [2, 0],
        f"板A截断 2 样本被显式记录（实测 {info['per_board_truncated']}），不静默丢弃")


def main() -> int:
    test_timeline_basics()
    test_frame_drop_detection()
    test_simulated_eye()
    test_recorder_sync_json()
    test_sync_verification_recovery()
    test_multiboard_truncation()
    print(f"\n=== {_PASS} passed, {_FAIL} failed ===")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
