#!/usr/bin/env python3
"""同步分析 4 个独立复现 bug 的失败测试（先红后绿）。

1. 帧率回绕：[(i%256, i/250) for i in range(500)] 真实速率 250 帧/s，
   旧代码用 (last-first)%256 得 ~121.7。
2. 屏幕发光状态：screen_onset_known=False 不能报 True。
3. 眼动时钟单位：device_ts 毫秒 0/1000/2000 vs 电脑秒 0/1/2 同速，不能报巨量漂移。
4. 表述：截距含未知时钟原点+传输延迟，不能叫「真实采样偏移/已验证漂移/硬件精度」。

run: .venv/bin/python scripts/test_sync_analysis.py  （exit 0 = 全过）
"""
from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from eegscope.sync_analysis import analyze_sync

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


def _eye_timeline(device_ts, t_recv, unit=None):
    return [{"kind": "eye_sample", "t_recv": tr,
             "data": {"device_ts": dt, "device_ts_unit": unit,
                      "clock_source": "sim", "x": 0.5, "y": 0.5, "valid": True,
                      "segment_id": 0}}
            for dt, tr in zip(device_ts, t_recv)]


# ---- 1. 帧率回绕 ----
def test_frame_rate_wraparound():
    print("[1] 帧率回绕")
    log = [[(i % 256, i / 250.0) for i in range(500)]]  # 250 帧/s，回绕一次
    sync = {"timeline": [], "frame_logs": log, "gap_log": []}
    rep = analyze_sync(sync)
    board0 = rep["eeg"]["per_board"][0]
    rate = board0.get("rate_hz", board0.get("rate_approx_hz"))
    _ck(rate is not None and abs(rate - 250.0) < 5.0,
        f"250 帧/s 回绕后仍≈250（实测 {rate}，旧代码≈121.7）")


# ---- 2. 屏幕发光状态 ----
def test_screen_onset():
    print("[2] 屏幕发光状态")
    def stim(flags):
        return {"timeline": [{"kind": "stimulus", "t_recv": 1.0,
                              "data": {"planned_s": 0.5, "screen_onset_known": f}}
                             for f in flags], "frame_logs": [], "gap_log": []}
    _ck(analyze_sync(stim([True]))["stimulus"]["screen_onset_known"] is True, "全已知→True")
    _ck(analyze_sync(stim([False]))["stimulus"]["screen_onset_known"] is False, "全未知→False（不反转）")
    _ck(analyze_sync(stim([True, False]))["stimulus"]["screen_onset_known"] is False, "混合→False")
    _ck(analyze_sync(stim([]))["stimulus"]["screen_onset_known"] is None, "无事件→None")


# ---- 3. 眼动时钟单位 ----
def test_eye_clock_unit():
    print("[3] 眼动时钟单位")
    # device_ts 毫秒 0/1000/2000 vs 电脑秒 0/1/2 → 同速，漂移应≈0
    sync_ms = {"timeline": _eye_timeline([0, 1000, 2000], [0.0, 1.0, 2.0], unit="ms"),
               "frame_logs": [], "gap_log": []}
    rep_ms = analyze_sync(sync_ms)
    drift = rep_ms["eye"].get("apparent_drift_ppm", rep_ms["eye"].get("clock_drift_ppm"))
    _ck(drift is not None and abs(drift) < 1000,
        f"毫秒单位同速→漂移≈0（实测 {drift}，旧代码≈999000000ppm）")
    # 单位未知 → 不输出貌似可信的漂移
    sync_unk = {"timeline": _eye_timeline([0, 1000, 2000], [0.0, 1.0, 2.0], unit=None),
                "frame_logs": [], "gap_log": []}
    rep_unk = analyze_sync(sync_unk)
    drift_unk = rep_unk["eye"].get("apparent_drift_ppm", rep_unk["eye"].get("clock_drift_ppm"))
    _ck(drift_unk is None,
        "单位未知→漂移标 None（不输出貌似可信数值）")


# ---- 4. 表述：不把截距/斜率叫成真实采样偏移/已验证漂移 ----
def test_wording():
    print("[4] 表述")
    sync = {"timeline": _eye_timeline([0.0, 1.0, 2.0], [0.0, 1.0, 2.0], unit="s"),
            "frame_logs": [], "gap_log": []}
    rep = analyze_sync(sync)
    eye = rep["eye"]
    _ck("device_vs_host_intercept_s" in eye and "apparent_drift_ppm" in eye,
        "字段名用 device_vs_host_intercept_s / apparent_drift_ppm")
    _ck("fixed_offset_s" not in eye and "clock_drift_ppm" not in eye,
        "不再用 fixed_offset_s / clock_drift_ppm 旧名")
    _ck("true_sampling_offset" not in str(eye) and "硬件精度" not in str(eye.get("note", "")),
        "不宣称真实采样偏移/硬件精度")
    _ck(rep["unidentifiable"]["true_sampling_offset_s"] is None,
        "真实采样偏移仍标不可识别")


def main() -> int:
    test_frame_rate_wraparound()
    test_screen_onset()
    test_eye_clock_unit()
    test_wording()
    print(f"\n=== {_PASS} passed, {_FAIL} failed ===")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
