"""离线时间同步分析：从 sync.json 估算「可估计」的量，并明确「不可识别」的量。

诚实边界（对应评估报告）：
  - 可估计（相对量，仅当单位已知 + 单一时钟分段）：眼动/脑电「表观」时钟漂移（斜率）、
    设备时间戳与电脑接收时间的拟合截距、丢帧率、无效段比例。
  - 不可识别（需共同事件 / 共同硬件触发）：绝对端到端延迟、设备真实采样时刻、硬件同步精度。
    拟合截距包含**未知时钟原点 + 传输延迟**，斜率受**缓冲与时变延迟**影响，两者都**不是**
    真实采样偏移或已验证的时钟漂移；本模块只报「表观」量，并显式标不可识别。

单位约定：设备时间戳必须携带 device_ts_unit（"s"/"ms"/"us"/"ns"），否则不换算、不输出漂移。
"""
from __future__ import annotations

import json

import numpy as np

_UNIT_SCALE = {"s": 1.0, "ms": 1e-3, "us": 1e-6, "ns": 1e-9,
               "second": 1.0, "seconds": 1.0,
               "millisecond": 1e-3, "milliseconds": 1e-3}


def load_sync(sync_path: str) -> dict:
    with open(sync_path, encoding="utf-8") as f:
        return json.load(f)


def _fit_linear(x: np.ndarray, y: np.ndarray):
    """最小二乘线性拟合，返回 (slope, intercept)。样本 <2 返回 (None, None)。"""
    if x.size < 2:
        return None, None
    slope, intercept = np.polyfit(x, y, 1)
    return float(slope), float(intercept)


def _to_seconds(value: float, unit) -> float | None:
    """按单位把设备时间戳换算成秒；单位未知/不认识返回 None。"""
    if unit is None or value is None:
        return None
    scale = _UNIT_SCALE.get(str(unit).lower())
    if scale is None:
        return None
    return float(value) * scale


def _eye_drift(eye_ts_s: np.ndarray, eye_recv: np.ndarray, reliable: bool):
    """眼动「表观」漂移（ppm）与截距（s）。reliable=False 时不输出数值。"""
    if not reliable or eye_ts_s.size < 2:
        return None, None
    slope, intercept = _fit_linear(eye_recv, eye_ts_s)
    if slope is None:
        return None, None
    # 表观漂移 = slope - 1（设备时间戳相对电脑时间多走的比例）；截距含未知原点+传输延迟。
    return (slope - 1.0) * 1e6, intercept


def analyze_sync(sync: dict) -> dict:
    """分析 sync.json，返回报告 dict。可估计的给数值，不可识别的标 None + 原因。"""
    timeline = sync.get("timeline", [])
    frame_logs = sync.get("frame_logs", [])
    gap_log = sync.get("gap_log", [])

    # ---- 眼动：表观漂移 + 截距（仅单位已知 + 单一时钟分段时可靠）----
    eye_ts_s_list: list[float] = []
    eye_recv_list: list[float] = []
    eye_valid = 0
    eye_invalid = 0
    units: set = set()
    segments: set = set()
    for e in timeline:
        if e.get("kind") != "eye_sample":
            continue
        d = e.get("data", {})
        if not d.get("valid"):
            eye_invalid += 1
            continue
        eye_valid += 1
        ts_s = _to_seconds(d.get("device_ts"), d.get("device_ts_unit"))
        if ts_s is None:
            continue  # 单位未知，不参与拟合
        eye_ts_s_list.append(ts_s)
        eye_recv_list.append(float(e["t_recv"]))
        units.add(str(d.get("device_ts_unit")))
        segments.add(int(d.get("segment_id", 0)))
    eye_ts_s = np.array(eye_ts_s_list, dtype=float)
    eye_recv = np.array(eye_recv_list, dtype=float)

    unit_known = len(units) == 1 and units.issubset(set(_UNIT_SCALE))
    single_segment = len(segments) <= 1
    drift_reliable = bool(unit_known and single_segment and eye_ts_s.size >= 2)
    drift_ppm, intercept_s = _eye_drift(eye_ts_s, eye_recv, drift_reliable)

    eye_invalid_ratio = (eye_invalid / (eye_valid + eye_invalid)) if (eye_valid + eye_invalid) else None

    # ---- 脑电：丢帧（帧编号跳变）+ 帧率（正确处理回绕）----
    eeg_dropped_total = 0
    eeg_dup_unknown = 0
    eeg_oob = 0
    per_board = []
    for bi, log in enumerate(frame_logs):
        if not log:
            continue
        counters = [int(c) for c, _ in log]
        times = [float(t) for _, t in log]
        dropped = 0
        dup = 0
        oob = 0
        forward = 0           # 正向累计帧步数（正确处理回绕：回绕时 diff 仍 =1）
        has_ambiguity = False
        last = None
        for c in counters:
            if last is not None:
                diff = (c - last) % 256
                if diff == 0:
                    dup += 1                  # 重复 或 整圈丢失，无法区分
                    has_ambiguity = True
                elif diff < 128:
                    forward += diff
                    if diff > 1:
                        dropped += diff - 1
                else:
                    oob += 1                  # 乱序/重启，无法消歧
                    has_ambiguity = True
            last = c
        # 帧率 = 正向步数 / 时间跨度；遇到无法消歧(乱序/重复/整圈丢失)标记 unreliable
        rate = None
        if len(times) >= 2 and times[-1] > times[0]:
            rate = forward / (times[-1] - times[0])
        per_board.append({
            "board": bi, "n_frames": len(log),
            "dropped_est": dropped, "dup_or_unknown": dup, "out_of_order": oob,
            "rate_hz": (round(rate, 4) if rate is not None else None),
            "rate_reliable": bool(not has_ambiguity and rate is not None),
        })
        eeg_dropped_total += dropped
        eeg_dup_unknown += dup
        eeg_oob += oob

    # ---- 刺激事件：计划时间 vs 记录时间 vs 屏幕发光 ----
    stim = [e for e in timeline if e.get("kind") == "stimulus"]
    stim_lag = []
    n_known = 0
    n_unknown = 0
    for e in stim:
        d = e.get("data", {})
        planned = d.get("planned_s")
        if planned is not None:
            stim_lag.append(float(e["t_recv"]) - float(planned))
        if d.get("screen_onset_known"):
            n_known += 1
        else:
            n_unknown += 1
    # 屏幕发光：只要有一个事件未知就整体标未知（不能把部分已知冒充全部已知）
    screen_onset_known = (True if stim and n_unknown == 0 else (False if stim else None))

    report = {
        "eye": {
            "n_valid": int(eye_valid),
            "n_invalid": int(eye_invalid),
            "invalid_ratio": eye_invalid_ratio,
            "apparent_drift_ppm": (round(drift_ppm, 3) if drift_ppm is not None else None),
            "device_vs_host_intercept_s": (round(intercept_s, 6) if intercept_s is not None else None),
            "drift_reliable": drift_reliable,
            "unit_known": unit_known,
            "single_clock_segment": single_segment,
            "note": ("表观漂移/截距仅当单位已知且单一时钟分段时输出；截距含未知时钟原点与传输延迟，"
                     "斜率受缓冲与时变延迟影响，均不是真实采样偏移或已验证漂移。"),
        },
        "eeg": {
            "n_boards": len(per_board),
            "dropped_total_est": int(eeg_dropped_total),
            "dup_or_unknown_total": int(eeg_dup_unknown),
            "out_of_order_total": int(eeg_oob),
            "per_board": per_board,
            "note": "丢帧是帧编号跳变的估计；整圈丢失(256帧)与重复帧无法区分，已单列；"
                    "乱序/重启使帧率不可靠(rate_reliable=False)。",
        },
        "stimulus": {
            "n_events": len(stim),
            "n_screen_onset_known": int(n_known),
            "n_screen_onset_unknown": int(n_unknown),
            "schedule_lag_s": (float(np.mean(stim_lag)) if stim_lag else None),
            "screen_onset_known": screen_onset_known,
            "note": "screen_onset_known 表示屏幕实际发光时间是否已知（需外部光电测量）；无测量时为 False。",
        },
        "gap_events": len(gap_log),
        "unidentifiable": {
            "absolute_end_to_end_latency_s": None,
            "true_sampling_offset_s": None,
            "hardware_sync_precision_s": None,
            "reason": ("接收时间只能给相对偏移/表观漂移；绝对延迟、真实采样时刻、硬件同步精度"
                       "需屏幕发光 + 脑电/眼动共同可见的事件实测，无法从接收时间唯一反推。"),
        },
    }
    return report


def analyze_sync_file(sync_path: str) -> dict:
    return analyze_sync(load_sync(sync_path))
