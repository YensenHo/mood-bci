"""离线时间同步分析：从 sync.json 估算「可估计」的量，并明确「不可识别」的量。

诚实边界（对应评估报告）：
  - 可估计（相对量）：眼动固定偏移（截距）、眼动/脑电时钟漂移（斜率）、丢帧率、无效段比例。
  - 不可识别（需共同事件 / 共同硬件触发）：绝对端到端延迟、设备真实采样时刻、硬件同步精度。
    在没有「屏幕发光 + 脑电/眼动共同可见的事件」之前，**无法**从接收时间唯一反推出
    真实采样偏移或绝对延迟——本模块只报相对量，绝对量显式标 None。
"""
from __future__ import annotations

import json
import os

import numpy as np


def load_sync(sync_path: str) -> dict:
    with open(sync_path, encoding="utf-8") as f:
        return json.load(f)


def _fit_linear(x: np.ndarray, y: np.ndarray):
    """最小二乘线性拟合，返回 (slope, intercept)。样本 <2 返回 (None, None)。"""
    if x.size < 2:
        return None, None
    slope, intercept = np.polyfit(x, y, 1)
    return float(slope), float(intercept)


def analyze_sync(sync: dict) -> dict:
    """分析 sync.json，返回报告 dict。可估计的给数值，不可识别的标 None + 原因。"""
    timeline = sync.get("timeline", [])
    frame_logs = sync.get("frame_logs", [])
    gap_log = sync.get("gap_log", [])

    # ---- 眼动：固定偏移（截距）+ 时钟漂移（斜率） ----
    eye = [e for e in timeline if e.get("kind") == "eye_sample" and e["data"].get("valid")]
    eye_ts, eye_recv, eye_valid_all = [], [], 0
    for e in timeline:
        if e.get("kind") != "eye_sample":
            continue
        eye_valid_all += 1
        d = e["data"]
        if d.get("valid") and d.get("device_ts") is not None:
            eye_ts.append(d["device_ts"])
            eye_recv.append(e["t_recv"])
    eye_ts = np.array(eye_ts, dtype=float)
    eye_recv = np.array(eye_recv, dtype=float)
    eye_drift, eye_offset = _fit_linear(eye_recv, eye_ts)
    # 漂移 = slope - 1（device_ts 相对 host 时间多走的比例）
    eye_drift_frac = (eye_drift - 1.0) if eye_drift is not None else None

    # 眼动无效段比例
    eye_invalid = sum(1 for e in timeline if e.get("kind") == "eye_sample" and not e["data"].get("valid"))
    eye_n = eye_invalid + len(eye_ts)
    eye_invalid_ratio = (eye_invalid / eye_n) if eye_n else None

    # ---- 脑电：丢帧（帧编号跳变）+ 跨板漂移 ----
    eeg_dropped_total = 0
    eeg_dup_unknown = 0
    eeg_oob = 0
    per_board = []
    for bi, log in enumerate(frame_logs):
        if not log:
            continue
        counters = np.array([c for c, _ in log], dtype=int)
        times = np.array([t for _, t in log], dtype=float)
        # 帧编号跳变（mod 256）累加丢帧
        dropped = 0
        dup = 0
        oob = 0
        last = None
        for c in counters:
            if last is not None:
                diff = (int(c) - int(last)) % 256
                if diff == 0:
                    dup += 1        # 重复 或 整圈丢失，无法区分
                elif diff < 128:
                    dropped += diff - 1
                else:
                    oob += 1        # 乱序/重启
            last = c
        # 该板帧率的斜率（帧/秒）：用首末 (counter, time) 粗估
        if len(counters) >= 2 and times[-1] > times[0]:
            span = (int(counters[-1]) - int(counters[0])) % 256
            # 帧编号回绕会低估 span，这里只做粗估，标记为「近似」
            rate_approx = span / (times[-1] - times[0])
        else:
            rate_approx = None
        per_board.append({"board": bi, "n_frames": len(log),
                          "dropped_est": dropped, "dup_or_unknown": dup,
                          "out_of_order": oob, "rate_approx_hz": rate_approx})
        eeg_dropped_total += dropped
        eeg_dup_unknown += dup
        eeg_oob += oob

    # ---- 刺激事件：计划时间 vs 记录时间 ----
    stim = [e for e in timeline if e.get("kind") == "stimulus"]
    stim_lag = []
    for e in stim:
        planned = e["data"].get("planned_s")
        if planned is not None:
            stim_lag.append(e["t_recv"] - planned)

    report = {
        # 可估计（相对量）
        "eye": {
            "n_valid": int(len(eye_ts)),
            "n_invalid": int(eye_invalid),
            "invalid_ratio": eye_invalid_ratio,
            "clock_drift_ppm": (eye_drift_frac * 1e6 if eye_drift_frac is not None else None),
            "fixed_offset_s": eye_offset,
            "note": "offset/drift 是「相对接收时间」的估计；绝对采样延迟不可识别。",
        },
        "eeg": {
            "n_boards": len(per_board),
            "dropped_total_est": int(eeg_dropped_total),
            "dup_or_unknown_total": int(eeg_dup_unknown),
            "out_of_order_total": int(eeg_oob),
            "per_board": per_board,
            "note": "丢帧是帧编号跳变的估计；整圈丢失(256帧)与重复帧无法区分，已单列。",
        },
        "stimulus": {
            "n_events": len(stim),
            "schedule_lag_s": (float(np.mean(stim_lag)) if stim_lag else None),
            "screen_onset_known": all(not e["data"].get("screen_onset_known", True) for e in stim) if stim else None,
        },
        "gap_events": len(gap_log),
        # 不可识别（需共同事件 / 共同硬件触发）
        "unidentifiable": {
            "absolute_end_to_end_latency_s": None,
            "true_sampling_offset_s": None,
            "hardware_sync_precision_s": None,
            "reason": ("接收时间只能给相对偏移/漂移；绝对延迟、真实采样时刻、硬件同步精度"
                       "需屏幕发光 + 脑电/眼动共同可见的事件实测。"),
        },
    }
    return report


def analyze_sync_file(sync_path: str) -> dict:
    return analyze_sync(load_sync(sync_path))
