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
import time
from datetime import datetime

import numpy as np

from .edfio import write_csv, write_edf
from . import __version__


def config_snapshot(cfg) -> dict:
    """把 AppConfig 的关键字段转成可 JSON 化的 dict（只取离线分析需要的部分）。"""
    snap = {
        "sample_rate": float(getattr(cfg, "sample_rate", 0.0)),
        "channels": list(getattr(cfg, "channels", [])),
    }
    # 以下字段在部分数据源/配置缺失时优雅降级，不影响录制。
    try:
        snap["source"] = {"type": getattr(cfg.source, "type", "")}
        hw = getattr(cfg.source, "openbci", None)
        if hw is not None:
            snap["source"]["openbci"] = {k: getattr(hw, k, None) for k in
                                           ("port", "ports", "n_channels", "gain", "gain_code", "scale_uv")}
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
                 config_snapshot=None, recordings_dir="recordings",
                 timeline=None):
        self.fs = float(sample_rate)
        self.channel_names = [str(c) for c in channel_names]
        self.source_type = str(source_type)
        self.config_snapshot = dict(config_snapshot or {})
        self.recordings_dir = str(recordings_dir)
        # 时间同步时间线（可选）：追加式记录 eeg_frame / eye_sample / stimulus 事件。
        # 由上层传入同一个 SyncTimeline 实例，录制结束时随会话一起落盘。
        self.timeline = timeline
        self._board_stats_log: list = []   # 每块板帧统计的周期快照
        self.reset()

    # ---- 状态 ----
    def reset(self) -> None:
        self._raw: list = []
        self._filtered: list = []
        self._n_samples = 0
        self._started_at: datetime | None = None
        self._start_monotonic: float | None = None   # 录制起点的电脑单调时钟（time.monotonic）
        self._stop_monotonic: float | None = None
        self._recording = False
        self.markers: list = []
        self.alignment = None
        self.acquisition = None
        self._board_stats_log: list = []
        # 逐帧记录（每板一个 list of (byte1 帧编号, 接收单调时间)），周期 drain 累加。
        self._frame_logs: list = []        # list[list[tuple[int,float]]]，长度=板数
        self._gap_log: list = []           # 每次 read 的缺口/截断信息
        self._quality_log: list = []
        self.export_status = None

    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def elapsed_seconds(self) -> float:
        """按样本数/采样率推算的时长（秒）。这是「样本时间」，不是电脑接收时间。"""
        return self._n_samples / self.fs if self.fs > 0 else 0.0

    def monotonic_seconds(self) -> float:
        """录制起点至今的电脑单调时钟秒数（time.monotonic 差值）。"""
        if self._start_monotonic is None:
            return 0.0
        return (self._stop_monotonic or time.monotonic()) - self._start_monotonic

    # ---- 录制生命周期 ----
    def start(self) -> None:
        self.reset()
        self._started_at = datetime.now()
        self._start_monotonic = time.monotonic()
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
        m = {"time_s": round(self.elapsed_seconds, 6),
             "t_monotonic": round(self.monotonic_seconds(), 6), "label": str(label)}
        self.markers.append(m)
        return m

    def record_board_stats(self, board_stats: list) -> None:
        """周期快照每块板的帧统计（丢帧/接收时间），供时间同步层事后分析。

        board_stats = MultiBoardSource.board_stats() 返回的 list（每板一个 dict）。
        录制外调用忽略。快照只追加、不覆盖原始帧统计。

        ⚠️ 时间字段区分两种时钟：
          - t_monotonic = 电脑 time.monotonic() 相对录制起点的秒数（接收时间基准）；
          - t_samples   = 样本数/采样率（样本时间，样本连续时的近似时长）。
        两者都会记录，不把「样本时间」冒充「电脑接收时间」。
        """
        if not self._recording:
            return
        snap = {"t_monotonic": round(self.monotonic_seconds(), 6),
                "t_samples": round(self.elapsed_seconds, 6),
                "boards": [dict(s) for s in (board_stats or [])]}
        self._board_stats_log.append(snap)

    def record_frame_logs(self, frame_logs: list) -> None:
        """周期 drain 每块板的逐帧记录（(byte1 帧编号, 接收单调时间)），累加保存。

        frame_logs = [板0 的 list, 板1 的 list, ...]，每项 = (int 帧编号, float 接收单调时间)。
        首次调用时按板数初始化；后续调用 append 到对应板。
        """
        if not self._recording:
            return
        logs = list(frame_logs or [])
        if not self._frame_logs:
            self._frame_logs = [[] for _ in logs]
        for i, items in enumerate(logs):
            if i < len(self._frame_logs):
                self._frame_logs[i].extend(items)

    def record_gap_info(self, gap_info: dict) -> None:
        """记录一次 read 的缺口/截断信息（掉线/欠采/跨板长度不等的显式标记）。"""
        if not self._recording:
            return
        self._gap_log.append({
            "t_monotonic": round(self.monotonic_seconds(), 6),
            "gap": dict(gap_info or {}),
        })

    def record_channel_quality(self, valid, reasons, interference=None) -> None:
        if self._recording:
            self._quality_log.append({"t_monotonic": round(self.monotonic_seconds(), 6),
                                      "t_samples": round(self.elapsed_seconds, 6),
                                      "valid": [bool(x) for x in valid],
                                      "reasons": reasons, "interference": interference})

    def stop(self):
        """停止录制并落盘。返回保存目录；无数据或未在录制时返回 None。"""
        if not self._recording:
            return None
        self._recording = False
        self._stop_monotonic = time.monotonic()
        if self._n_samples == 0:
            return None
        return self.save(self._default_dir())

    # ---- 落盘 ----
    def _default_dir(self) -> str:
        stamp = (self._started_at or datetime.now()).strftime("%Y%m%d_%H%M%S")
        path = os.path.join(self.recordings_dir, stamp)
        suffix = 1
        while os.path.exists(path):
            path = os.path.join(self.recordings_dir, f"{stamp}_{suffix}")
            suffix += 1
        return path

    def save(self, out_dir: str) -> str:
        """把已缓冲数据写到 out_dir（session.npz + meta.json），返回 out_dir。"""
        os.makedirs(out_dir, exist_ok=True)
        if any(os.path.exists(os.path.join(out_dir, name)) for name in
               ("session.npz", "meta.json", "sync.json", "session_raw.edf", "session_filtered.edf", "session_raw.csv")):
            raise FileExistsError("录制目录已存在数据，拒绝覆盖")
        raw = np.concatenate(self._raw, axis=1).astype(np.float32, copy=False)
        filtered = np.concatenate(self._filtered, axis=1).astype(np.float32, copy=False)

        np.savez_compressed(
            os.path.join(out_dir, "session.npz"),
            raw=raw,
            filtered=filtered,
            fs=np.float32(self.fs),
            channel_names=np.array(self.channel_names),
        )

        # 导出标准格式（主流脑电软件 EEGLAB/MNE 可直接读）：
        #   - session_raw.edf / session_filtered.edf：EDF+ 标准格式
        #   - session_raw.csv：OpenBCI 同款 CSV（Excel/Python 可读）
        exports = {
            "raw_edf": write_edf(os.path.join(out_dir, "session_raw.edf"), raw, self.fs, self.channel_names, self._started_at),
            "filtered_edf": write_edf(os.path.join(out_dir, "session_filtered.edf"), filtered, self.fs, self.channel_names, self._started_at),
            "raw_csv": write_csv(os.path.join(out_dir, "session_raw.csv"), raw, self.fs, self.channel_names),
        }
        self.export_status = exports

        meta = {
            "software_version": __version__,
            "sample_rate": self.fs,
            "channel_names": self.channel_names,
            "source_type": self.source_type,
            "duration_seconds": round(self.elapsed_seconds, 6),
            "wall_duration_seconds": round(self.monotonic_seconds(), 6),
            "duration_difference_seconds": round(self.monotonic_seconds() - self.elapsed_seconds, 6),
            "export_status": exports,
            "n_samples": int(self._n_samples),
            "recorded_at": (self._started_at or datetime.now()).isoformat(),
            "config": self.config_snapshot,
            "markers": self.markers,
            "alignment": self.alignment,
            "acquisition": self.acquisition,
        }
        with open(os.path.join(out_dir, "meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

        # 时间同步元数据（若接了 SyncTimeline）：追加式事件时间线 + 每板帧统计 + 逐帧 + 缺口。
        # 原始记录不可覆盖（只读追加），区分「设备采样时间」与「电脑接收时间」。
        if self.timeline is not None:
            sync = {
                "timeline": self.timeline.events(),
                "timeline_summary": self.timeline.summary(),
                "board_stats_snapshots": self._board_stats_log,
                # 逐帧记录：每板 list of [byte1 帧编号, 接收单调时间]
                "frame_logs": [[[int(c), round(float(t), 9)] for c, t in log]
                               for log in self._frame_logs],
                "gap_log": self._gap_log,
                "channel_quality": self._quality_log,
                "channel_quality_note": "分析窗口内按原始数据筛查平线、量程饱和、非有限值及疑似零值/重复值平台异常；可疑或明显异常通道均暂不纳入情绪特征计算。NC/REF/GND 不参与。未标异常不代表接触或信号质量已验证，数据异常不等于电极接触故障。",
                "start_monotonic": self._start_monotonic,
                "clock_note": (
                    "时钟说明：\n"
                    "  - t_monotonic / frame_logs 的时间 = 电脑 time.monotonic() 相对录制起点的秒数，"
                    "是接收时间（一次串口 read 的多帧共享同一批接收时间，是到达时间上界），不是设备精确采样时间；\n"
                    "  - t_samples = 样本数/采样率（样本连续时的近似时长），不是电脑接收时间；\n"
                    "  - device_ts = 眼动仪自带时间戳（时钟源见 clock_source 字段）；\n"
                    "  - sample_counter / byte1 帧编号 = 设备侧帧计数（0~255 回绕，语义见评估报告）。"),
            }
            with open(os.path.join(out_dir, "sync.json"), "w", encoding="utf-8") as f:
                json.dump(sync, f, ensure_ascii=False, indent=2)
        return out_dir
