"""时间同步基础：单调时钟 + 追加式事件时间线（不可覆盖）。

目的（为「屏幕眼动 + 64 通道脑电」的时间对齐打地基）：

1. **单调时钟**：`time.monotonic()`，不受系统时钟跳变/NTP 校时影响，适合做
   同一台电脑内「眼动、脑电帧、刺激事件」三者的相对时间基准。

2. **追加式事件日志**：只 append、不覆盖，原始记录不可变。

3. **明确区分两种时间**（本项目的诚实边界）：
   - **设备采样时间**（如脑电帧的样本/帧编号、眼动仪自带时间戳）= 设备侧时钟；
   - **电脑接收时间** = 本机单调时钟，是「电脑什么时候收到」，**不是精确采样时刻**。
     两者之间的差（传输延迟/抖动）不可消除，只能记录、不能冒充精确采样时间。

记录三类事件，都用同一个单调时钟：
   - ``eeg_frame``  ：脑电帧（板号 + 样本/帧编号 + 接收单调时间）
   - ``eye_sample`` ：眼动样本（设备时间戳 + 时钟源 + 坐标 + 有效性 + 接收单调时间）
   - ``stimulus``   ：刺激事件（计划时间 + 软件记录时间；屏幕实际发光时间标 unknown）
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from typing import Any, Optional


def monotonic_now() -> float:
    """本机单调时钟（秒），同一台电脑内做相对时间基准。"""
    return time.monotonic()


@dataclass
class TimelineEvent:
    """一条追加式时间线事件。kind + 相对时间 + 任意字段。"""
    kind: str                          # eeg_frame / eye_sample / stimulus / ...
    t_recv: float                      # 电脑接收单调时间（秒，相对 timeline 起点）
    data: dict = field(default_factory=dict)   # 事件专属字段

    def to_dict(self) -> dict:
        return {"kind": self.kind, "t_recv": round(self.t_recv, 9),
                "data": self.data}


class SyncTimeline:
    """追加式事件时间线（不可覆盖）。三类数据源统一在这条时间线上对齐。

    用法：
        tl = SyncTimeline()
        t0 = tl.start()                # 记录起点，返回起点单调时间
        tl.record_eeg_frame(board, sample_counter, recv_monotonic)
        tl.record_eye_sample(device_ts, clock_source, x, y, valid, recv_monotonic)
        tl.record_stimulus(event_id, planned_s, kind)
        events = tl.events()           # 按追加顺序返回
    """

    def __init__(self) -> None:
        self._t0: Optional[float] = None
        self._events: list[TimelineEvent] = []
        self._eeg_dropped_total: int = 0      # 脑电累计估计丢帧数

    # ---- 生命周期 ----
    def start(self) -> float:
        self._t0 = monotonic_now()
        return self._t0

    def _rel(self, recv_monotonic: Optional[float]) -> float:
        """把「电脑接收单调时间」转成相对 timeline 起点的秒数。"""
        if self._t0 is None:
            self.start()
        t0: float = self._t0  # type: ignore[assignment]
        if recv_monotonic is None:
            return monotonic_now() - t0
        return recv_monotonic - t0

    # ---- 三类事件 ----
    def record_eeg_frame(self, board: int, sample_counter: Optional[int],
                         recv_monotonic: Optional[float] = None, *,
                         dropped_since_prev: int = 0) -> None:
        """记一条脑电帧。sample_counter=设备侧样本/帧编号；recv_monotonic=接收时刻（None=用当前时刻）。"""
        self._events.append(TimelineEvent(
            kind="eeg_frame",
            t_recv=self._rel(recv_monotonic),
            data={"board": int(board),
                  "sample_counter": None if sample_counter is None else int(sample_counter),
                  "dropped_since_prev": int(dropped_since_prev)},
        ))

    def record_eye_sample(self, device_ts: Optional[float], clock_source: str,
                          x: Optional[float], y: Optional[float], valid: bool,
                          recv_monotonic: Optional[float] = None) -> None:
        """记一条眼动样本。device_ts=眼动仪自带时间戳；clock_source=其时钟来源。

        valid=False 表示该段眼动无效（眨眼/丢失/超范围）。x/y 为归一化或像素坐标，
        单位在 meta 里另行声明，这里只存原始值。
        """
        self._events.append(TimelineEvent(
            kind="eye_sample",
            t_recv=self._rel(recv_monotonic),
            data={"device_ts": None if device_ts is None else round(float(device_ts), 9),
                  "clock_source": str(clock_source),
                  "x": None if x is None else float(x),
                  "y": None if y is None else float(y),
                  "valid": bool(valid)},
        ))

    def record_stimulus(self, event_id: str, planned_s: Optional[float],
                        kind: str = "onset", *, screen_onset_known: bool = False) -> None:
        """记一条刺激事件。

        planned_s = 计划出现时间（相对 timeline 起点，若由调度器给出）；
        t_recv（本函数记下的）= 软件实际记录时间；
        screen_onset_known=False 表示屏幕实际发光时间未知（无外部光电测量）。
        """
        self._events.append(TimelineEvent(
            kind="stimulus",
            t_recv=self._rel(None),
            data={"event_id": str(event_id),
                  "planned_s": None if planned_s is None else round(float(planned_s), 9),
                  "stim_kind": str(kind),
                  "screen_onset_known": bool(screen_onset_known)},
        ))

    def record_event(self, kind: str, **data: Any) -> None:
        """通用事件（marker、丢包、重连等）。"""
        self._events.append(TimelineEvent(kind=kind, t_recv=self._rel(None), data=data))

    # ---- 读取 ----
    def events(self) -> list[dict]:
        """返回按追加顺序的事件列表（浅拷贝，原始记录不被修改）。"""
        return [e.to_dict() for e in self._events]

    def summary(self) -> dict:
        """统计摘要：各类事件计数 + 时间跨度。"""
        kinds: dict[str, int] = {}
        ts = [e.t_recv for e in self._events]
        for e in self._events:
            kinds[e.kind] = kinds.get(e.kind, 0) + 1
        return {
            "n_events": len(self._events),
            "by_kind": kinds,
            "t_start": round(ts[0], 6) if ts else None,
            "t_end": round(ts[-1], 6) if ts else None,
            "eeg_dropped_total": int(self._eeg_dropped_total),
        }


@dataclass
class StimulusSpec:
    """一次刺激的规格：计划时间 + 类型。计划时间相对 timeline 起点。"""
    event_id: str
    planned_s: float
    kind: str = "onset"


class StimulusScheduler:
    """刺激调度器：给定计划时间表，在单调时钟到达计划时刻时记录事件。

    计划时间 vs 记录时间的差 = 软件调度抖动；屏幕实际发光时间另标 unknown。
    """

    def __init__(self, timeline: SyncTimeline) -> None:
        self._tl = timeline
        self._queue: list[StimulusSpec] = []

    def schedule(self, event_id: str, planned_s: float, kind: str = "onset") -> None:
        self._queue.append(StimulusSpec(event_id, planned_s, kind))

    def tick(self) -> list[str]:
        """在采集循环里周期调用：把已到计划时刻的事件记进时间线，返回本次触发的 id 列表。"""
        now = self._tl._rel(None) if self._tl._t0 is not None else 0.0
        fired: list[str] = []
        remaining: list[StimulusSpec] = []
        for spec in self._queue:
            if spec.planned_s <= now:
                self._tl.record_stimulus(spec.event_id, spec.planned_s, spec.kind)
                fired.append(spec.event_id)
            else:
                remaining.append(spec)
        self._queue = remaining
        return fired
