"""会话录制：内存缓冲 raw / filtered，停止时落盘 session.npz + meta.json。

设计目标：
  - 与 GUI 解耦（`MainWindow` 和 headless 冒烟测试共用同一套保存逻辑，
    保证「录制 → 落盘 → 离线分析」链路对合成数据和真实硬件完全一致）。
  - 录制阶段纯内存缓冲（list of float32 ndarray），不做磁盘增量写，
    因此长会话内存占用 = 2 × n_ch × n × 4 字节（raw + filtered 各一份），
    长会话需注意（详见 README「会话录制」一节）。

落盘格式（`recordings/<YYYYmmdd_HHMMSS>/`）：
  - `session.npz`：`raw`(n_ch, n) float32、`filtered`(n_ch, n) float32、
    `fs`(float32 标量)、`channel_names`(字符串数组)，np.savez_compressed 压缩。
  - `meta.json`：采样率、通道名、数据源类型、时长秒、录制开始时间、
    config 快照（可 JSON 化关键字段）、markers 列表。
"""
from __future__ import annotations

import json
import os
from datetime import datetime

import numpy as np


def config_snapshot(cfg) -> dict:
    """把 AppConfig 的关键字段转成可 JSON 化的 dict（只取离线分析需要的部分）。"""
    snap = {
        "sample_rate": float(getattr(cfg, "sample_rate", 0.0)),
        "channels": list(getattr(cfg, "channels", [])),
    }
    # 以下字段在部分数据源/配置缺失时优雅降级，不影响录制。
    try:
        snap["source"] = {"type": getattr(cfg.source, "type", "")}
    except AttributeError:
        snap["source"] = {"type": ""}
    try:
        snap["dsp"] = {
            "bandpass_hz": list(cfg.dsp.bandpass_hz),
            "notch_hz": [float(x) for x in cfg.dsp.notch_hz],
            "notch_q": float(cfg.dsp.notch_q),
        }
    except AttributeError:
        snap["dsp"] = {}
    try:
        snap["analysis"] = {
            "window_seconds": float(cfg.analysis.window_seconds),
            "bands": {k: list(v) for k, v in cfg.analysis.bands.items()},
            "faa_channels": list(cfg.analysis.faa_channels),
        }
    except AttributeError:
        snap["analysis"] = {}
    return snap


class SessionRecorder:
    """内存缓冲的会话录制器。

    用法：
        rec = SessionRecorder(fs, channel_names, source_type, config_snapshot(cfg),
                              recordings_dir)
        rec.start()
        ... 每个数据块调用 rec.append(raw_chunk, filtered_chunk) ...
        rec.add_marker("刺激开始")
        out_dir = rec.stop()          # 停止并落盘，返回保存目录（无数据返回 None）
    """

    def __init__(self, sample_rate, channel_names, source_type="unknown",
                 config_snapshot=None, recordings_dir="recordings"):
        self.fs = float(sample_rate)
        self.channel_names = [str(c) for c in channel_names]
        self.source_type = str(source_type)
        self.config_snapshot = dict(config_snapshot or {})
        self.recordings_dir = str(recordings_dir)
        self.reset()

    # ---- 状态 ----
    def reset(self) -> None:
        self._raw: list = []
        self._filtered: list = []
        self._n_samples = 0
        self._started_at: datetime | None = None
        self._recording = False
        self.markers: list = []

    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def elapsed_seconds(self) -> float:
        return self._n_samples / self.fs if self.fs > 0 else 0.0

    # ---- 录制生命周期 ----
    def start(self) -> None:
        self.reset()
        self._started_at = datetime.now()
        self._recording = True

    def append(self, raw_chunk, filtered_chunk) -> None:
        """缓冲一块数据。raw_chunk / filtered_chunk 均为 (n_ch, n) µV。"""
        if not self._recording:
            return
        raw_chunk = np.asarray(raw_chunk)
        filtered_chunk = np.asarray(filtered_chunk)
        if raw_chunk.ndim == 1:
            raw_chunk = raw_chunk[None, :]
        if filtered_chunk.ndim == 1:
            filtered_chunk = filtered_chunk[None, :]
        if raw_chunk.shape[1] == 0:
            return
        # 存 float32 副本，落盘时不再做一次大拷贝的类型转换
        self._raw.append(np.ascontiguousarray(raw_chunk, dtype=np.float32))
        self._filtered.append(np.ascontiguousarray(filtered_chunk, dtype=np.float32))
        self._n_samples += raw_chunk.shape[1]

    def add_marker(self, label) -> dict | None:
        """在 meta 追加一个 marker（时间 = 当前录制相对秒）。录制外调用返回 None。"""
        if not self._recording:
            return None
        m = {"time_s": round(self.elapsed_seconds, 6), "label": str(label)}
        self.markers.append(m)
        return m

    def stop(self):
        """停止录制并落盘。返回保存目录；无数据或未在录制时返回 None。"""
        if not self._recording:
            return None
        self._recording = False
        if self._n_samples == 0:
            return None
        return self.save(self._default_dir())

    # ---- 落盘 ----
    def _default_dir(self) -> str:
        stamp = (self._started_at or datetime.now()).strftime("%Y%m%d_%H%M%S")
        return os.path.join(self.recordings_dir, stamp)

    def save(self, out_dir: str) -> str:
        """把已缓冲数据写到 out_dir（session.npz + meta.json），返回 out_dir。"""
        os.makedirs(out_dir, exist_ok=True)
        raw = np.concatenate(self._raw, axis=1).astype(np.float32, copy=False)
        filtered = np.concatenate(self._filtered, axis=1).astype(np.float32, copy=False)

        np.savez_compressed(
            os.path.join(out_dir, "session.npz"),
            raw=raw,
            filtered=filtered,
            fs=np.float32(self.fs),
            channel_names=np.array(self.channel_names),
        )

        meta = {
            "sample_rate": self.fs,
            "channel_names": self.channel_names,
            "source_type": self.source_type,
            "duration_seconds": round(self.elapsed_seconds, 6),
            "n_samples": int(self._n_samples),
            "recorded_at": (self._started_at or datetime.now()).isoformat(),
            "config": self.config_snapshot,
            "markers": self.markers,
        }
        with open(os.path.join(out_dir, "meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        return out_dir
