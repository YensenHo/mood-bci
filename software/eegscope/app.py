"""Qt 主窗口 + 数据流编排：source → dsp → analysis → viz。"""
from __future__ import annotations

import os
import sys
import time

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QApplication, QComboBox, QHBoxLayout, QLabel,
                               QMainWindow, QPushButton, QSlider, QSplitter,
                               QVBoxLayout, QWidget)

from . import _runtime
from .analysis import (BaselineCalibrator, EmotionAnalyzer, band_power_map,
                       detect_25hz_concentration, welch_psd)
from .dsp import SignalChain
from .recorder import SessionRecorder, config_snapshot
from .remote import StreamUploader
from .sync import SyncTimeline
from .viz import (EmotionPanel, QualityPanel, SpectrogramPanel, SpectrumPanel,
                  TopographyPanel, WaveformPanel, compute_channel_quality, setup_theme)

# 录制输出根目录：dev = software/recordings/；frozen = exe 目录/recordings/
_RECORDINGS_DIR = _runtime.data_dir("recordings")


class MainWindow(QMainWindow):
    def __init__(self, cfg, source, eye_tracker=None):
        super().__init__()
        self.cfg = cfg
        self.source = source
        # 眼动仪（可选）：真实设备或模拟器；无则 None，眼动样本不记录。
        self.eye_tracker = eye_tracker

        self.dsp = SignalChain(cfg.sample_rate, cfg.dsp.bandpass_hz,
                               cfg.dsp.notch_hz, cfg.dsp.notch_q)
        self.analyzer = EmotionAnalyzer(cfg.sample_rate, cfg.channels,
                                        cfg.analysis.bands, cfg.analysis.faa_channels)

        src_label = "合成数据" if getattr(source, "is_synthetic", False) else "真实硬件"
        self.setWindowTitle(
            f"eegscope — 实时脑电可视化 + 情绪分析（{src_label} · {source.n_channels} 通道）")
        self._build_ui()
        self.info_label.setText(f"{src_label} · {source.n_channels} 通道已就绪")

        # 分析/PSD 刷新周期（每 N 次 tick 算一次，约 500ms）
        self._analysis_every = max(1, int(0.5 * 1000 / cfg.viz.refresh_ms))
        self._tick_count = 0
        # 指标显示平滑（EMA），降低低 alpha 时 FAA/效价/唤醒度的噪声抖动
        self._ema_state = {}
        self._last_valid_channels = None

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(cfg.viz.refresh_ms)
        self._frac = 0.0
        self._last_tick = time.monotonic()
        # 原始数据缓冲（未滤波）：电极接触检测要看「打满/平线」，滤波会把直流去掉
        self._raw_hist = np.zeros((source.n_channels, 0), dtype=np.float64)

        # 会话录制状态（懒初始化，按下「录制」才建 SessionRecorder）
        self.recorder: SessionRecorder | None = None
        # 时间同步时间线（录制时随 SessionRecorder 一起落盘 sync.json）
        self.timeline = SyncTimeline()
        # 远程上传状态（懒初始化，按下「上传」才建 StreamUploader）
        self.uploader: StreamUploader | None = None
        # 基线校准状态（被试内 baseline，HARNESS P1）
        self.calibrator = BaselineCalibrator()
        self._baseline_mode = False

    # ---- UI ----
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(6, 6, 6, 6)

        root.addLayout(self._build_controls())

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # 左列：波形 + 信号质量（质量标签对齐波形通道）
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(4)
        self.waveform = WaveformPanel(
            self.source.n_channels, self.source.channel_names,
            self.cfg.viz.window_seconds, self.cfg.sample_rate,
            self.cfg.viz.channel_spacing_uv)
        self.quality = QualityPanel(self.source.channel_names, self.cfg.sample_rate,
                                    full_scale_uv=self.source.full_scale_uv)
        left_layout.addWidget(self.waveform, stretch=1)
        left_layout.addWidget(self.quality, stretch=0)
        splitter.addWidget(left)

        # 右列：频谱 + 情绪 + 脑地形图 + 时频图
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        self.spectrum = SpectrumPanel(self.cfg.sample_rate, self.cfg.analysis.bands)
        self.emotion = EmotionPanel(self.cfg.analysis.bands)
        self.topography = TopographyPanel(self.source.channel_names,
                                          self.cfg.analysis.bands)
        self.spectrogram = SpectrogramPanel(self.source.channel_names,
                                            self.cfg.sample_rate,
                                            window_seconds=10.0)
        right_layout.addWidget(self.spectrum, stretch=2)
        right_layout.addWidget(self.emotion, stretch=2)
        right_layout.addWidget(self.topography, stretch=3)
        right_layout.addWidget(self.spectrogram, stretch=3)
        splitter.addWidget(right)

        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([750, 420])
        root.addWidget(splitter, stretch=1)

    def _build_controls(self):
        bar = QHBoxLayout()
        bar.setSpacing(10)

        self.start_btn = QPushButton("暂停")
        self.start_btn.setCheckable(True)
        self.start_btn.toggled.connect(self._on_start_toggled)
        bar.addWidget(self.start_btn)

        self.alpha_label = QLabel("alpha 幅度（睁眼↔闭眼）")
        bar.addWidget(self.alpha_label)
        self.alpha_slider = QSlider(Qt.Orientation.Horizontal)
        self.alpha_slider.setRange(0, 100)
        self.alpha_slider.setValue(int(self.source.alpha_level * 100))
        self.alpha_slider.valueChanged.connect(self._on_alpha_slider)
        self.alpha_slider.setFixedWidth(160)
        bar.addWidget(self.alpha_slider)

        self.eyes_btn = QPushButton("模拟闭眼")
        self.eyes_btn.setCheckable(True)
        self.eyes_btn.toggled.connect(self._on_eyes_toggled)
        bar.addWidget(self.eyes_btn)

        # 真实硬件没有 alpha 旋钮/模拟闭眼概念，隐藏相关控件
        if not getattr(self.source, "is_synthetic", False):
            self.alpha_label.hide()
            self.alpha_slider.hide()
            self.eyes_btn.hide()

        # 会话录制 / 标记（合成与真实硬件都可用）
        self.record_btn = QPushButton("录制")
        self.record_btn.setCheckable(True)
        self.record_btn.toggled.connect(self._on_record_toggled)
        bar.addWidget(self.record_btn)

        self.marker_combo = QComboBox()
        self.marker_combo.setEditable(True)
        self.marker_combo.addItems(["刺激开始", "刺激结束", "事件", "自定义"])
        self.marker_combo.setCurrentText("刺激开始")
        self.marker_combo.setFixedWidth(120)
        bar.addWidget(self.marker_combo)

        self.mark_btn = QPushButton("标记")
        self.mark_btn.clicked.connect(self._on_mark_clicked)
        bar.addWidget(self.mark_btn)

        # 远程上传（参与者端：把数据实时发到我的电脑）
        self.upload_btn = QPushButton("📤 上传")
        self.upload_btn.setCheckable(True)
        self.upload_btn.toggled.connect(self._on_upload_toggled)
        bar.addWidget(self.upload_btn)

        # 基线校准（被试内 baseline：记录中性期指标，之后 z-score）
        self.baseline_btn = QPushButton("基线校准")
        self.baseline_btn.setCheckable(True)
        self.baseline_btn.toggled.connect(self._on_baseline_toggled)
        bar.addWidget(self.baseline_btn)

        bar.addWidget(QLabel("PSD 通道"))
        self.ch_selector = QComboBox()
        self.ch_selector.addItems(self.source.channel_names)
        self.ch_selector.setCurrentIndex(0)
        bar.addWidget(self.ch_selector)

        self.info_label = QLabel("")
        self.info_label.setStyleSheet("color: #999;")
        bar.addWidget(self.info_label)
        if hasattr(self.source, "discard_pre_boundary_samples"):
            sync_notice = QLabel("多板未硬件同步")
            sync_notice.setStyleSheet("color: #e69f00;")
            bar.addWidget(sync_notice)
        bar.addStretch(1)
        return bar

    # ---- 控制回调 ----
    def _on_start_toggled(self, checked):
        if checked and self.recorder is not None:
            self.start_btn.blockSignals(True)
            self.start_btn.setChecked(False)
            self.start_btn.blockSignals(False)
            self.info_label.setText("录制期间不能暂停采集，请先停止录制")
            return
        if checked:
            self.timer.stop()
            self.start_btn.setText("继续")
        else:
            self._last_tick = time.monotonic()
            self.timer.start(self.cfg.viz.refresh_ms)
            self.start_btn.setText("暂停")

    def _on_alpha_slider(self, value):
        self.source.set_alpha_level(value / 100.0)
        # 手动拖动旋钮时，同步取消“模拟闭眼”按钮状态
        if self.eyes_btn.isChecked() and value < 90:
            self.eyes_btn.blockSignals(True)
            self.eyes_btn.setChecked(False)
            self.eyes_btn.blockSignals(False)

    def _on_eyes_toggled(self, checked):
        if checked:
            self.source.set_alpha_level(1.0)
            self.alpha_slider.blockSignals(True)
            self.alpha_slider.setValue(100)
            self.alpha_slider.blockSignals(False)
            self.eyes_btn.setText("模拟睁眼")
        else:
            self.source.set_alpha_level(0.2)
            self.alpha_slider.blockSignals(True)
            self.alpha_slider.setValue(20)
            self.alpha_slider.blockSignals(False)
            self.eyes_btn.setText("模拟闭眼")

    # ---- 会话录制 / 标记 ----
    def _on_record_toggled(self, checked):
        if checked:
            self._start_recording()
        else:
            self._stop_recording()

    def _start_recording(self):
        if not self.timer.isActive():
            self.start_btn.setChecked(False)
            self._last_tick = time.monotonic()
            self.timer.start(self.cfg.viz.refresh_ms)
            self.start_btn.setText("暂停")
        # 新建一条时间线（每次录制独立起点），并（可选）启动眼动仪
        self.timeline = SyncTimeline()
        self.timeline.start()
        if self.eye_tracker is not None:
            try:
                self.eye_tracker.start()
            except Exception as e:
                self.info_label.setText(f"眼动仪启动失败：{e}")
        self.recorder = SessionRecorder(
            self.cfg.sample_rate,
            self.source.channel_names,
            source_type=type(self.source).__name__,
            config_snapshot=config_snapshot(self.cfg),
            recordings_dir=_RECORDINGS_DIR,
            timeline=self.timeline,
        )
        # 在眼动设备等可能耗时的启动步骤之后，再清理录制前积压。
        alignment = None
        if hasattr(self.source, "discard_pre_boundary_samples"):
            alignment = self.source.discard_pre_boundary_samples()
        if hasattr(self.source, "drain_frame_logs"):
            self.source.drain_frame_logs()
        elif hasattr(self.source, "drain_frame_log"):
            self.source.drain_frame_log()
        self.recorder.start()
        self.recorder.alignment = alignment
        self.record_btn.setText("停止录制")
        self.info_label.setText("录制中…")

    def _stop_recording(self):
        if self.recorder is None:
            return
        # 先消费已接收的积压，避免界面卡顿后停止录制时丢失最后几秒。
        if hasattr(self.source, "read_available"):
            limit = max(1, int(self.cfg.sample_rate * 5))
            for _ in range(30):
                if self._tick() < limit:
                    break
        self._record_sync()
        # 先停眼动仪（若在跑）
        if self.eye_tracker is not None:
            try:
                self.eye_tracker.stop()
            except Exception:
                pass
        out = self.recorder.stop()
        export_status = self.recorder.export_status
        self.recorder = None
        # 复位按钮（blockSignals 防止再次触发 toggled 递归）
        self.record_btn.blockSignals(True)
        self.record_btn.setChecked(False)
        self.record_btn.blockSignals(False)
        self.record_btn.setText("录制")
        if out:
            failed = [name for name, ok in (export_status or {}).items() if not ok]
            if failed:
                self.info_label.setText(f"NPZ 已保存；导出失败: {', '.join(failed)}")
            else:
                self.info_label.setText(f"已保存: {os.path.basename(out)}")

    def _on_upload_toggled(self, checked):
        if checked:
            self._start_upload()
        else:
            self._stop_upload()

    def _start_upload(self):
        r = self.cfg.remote
        self.uploader = StreamUploader(r.upload_url, participant=r.participant)
        self.uploader.set_meta(self.source.sample_rate, self.source.channel_names)
        self.uploader.start()
        self.upload_btn.setText("📤 上传中…")
        self.info_label.setText(f"上传到 {r.upload_url}（参与者：{r.participant}）")

    def _stop_upload(self):
        if self.uploader is not None:
            self.uploader.close()
            self.uploader = None
        self.upload_btn.setText("📤 上传")
        self.info_label.setText("已停止上传")

    def _on_baseline_toggled(self, checked):
        """基线校准：按下开始采集中性期指标，再按结束并算 mean/std。"""
        # 校准切换会改变指标尺度（raw ↔ z-score），清掉旧 EMA，避免新旧值混叠
        self._ema_state.clear()
        if checked:
            self.calibrator.reset()
            self._baseline_mode = True
            self.baseline_btn.setText("基线采集中…")
            self.info_label.setText("基线采集中：让被试看中性片/静坐，完成后再次点击结束")
        else:
            self._baseline_mode = False
            self.baseline_btn.setText("基线校准")
            if self.calibrator.finish():
                self.info_label.setText(
                    f"基线校准完成（{self.calibrator.n_samples} 样本），指标已转 z-score")
            else:
                self.info_label.setText("基线样本不足（<3 帧或全为零），未校准")

    def _on_mark_clicked(self):
        if self.recorder is None or not self.recorder.is_recording:
            self.info_label.setText("未在录制，无法标记")
            return
        label = self.marker_combo.currentText().strip() or "事件"
        m = self.recorder.add_marker(label)
        if m is not None:
            self.info_label.setText(f"已标记 @ {m['time_s']:.1f}s: {label}")

    # ---- 数据流 ----
    def _record_sync(self) -> None:
        """录制中周期调用：把 source 的每板帧统计/逐帧/缺口 + 眼动样本写进时间线。"""
        rec = self.recorder
        if rec is None or not rec.is_recording:
            return
        src = self.source
        # 1) 每板帧统计（board_stats 多板 / frame_stats 单板）
        if hasattr(src, "board_stats"):
            stats = src.board_stats()
        elif hasattr(src, "frame_stats"):
            stats = [src.frame_stats()]
        else:
            stats = None
        if stats is not None:
            rec.record_board_stats(stats)
        # 2) 逐帧记录（有界 drain）
        if hasattr(src, "drain_frame_logs"):
            logs = src.drain_frame_logs()
        elif hasattr(src, "drain_frame_log"):
            logs = [src.drain_frame_log()]
        else:
            logs = []
        if logs:
            rec.record_frame_logs(logs)
        # 3) 缺口/截断（掉线/欠采/跨板长度不等，显式标记，不静默丢）
        if hasattr(src, "read_gap_info"):
            rec.record_gap_info(src.read_gap_info())
        elif hasattr(src, "last_read_gap"):
            rec.record_gap_info({"gap_samples": int(src.last_read_gap())})
        # 4) 眼动样本
        if self.eye_tracker is not None:
            for s in self.eye_tracker.read():
                self.timeline.record_eye_sample(
                    device_ts=s.device_ts, clock_source=s.clock_source,
                    x=s.x, y=s.y, valid=s.valid, recv_monotonic=None,
                    device_ts_unit=s.device_ts_unit, segment_id=s.segment_id,
                    coordinate_system=s.coordinate_system, confidence=s.confidence,
                    frame_number=s.frame_number, calibrated=s.calibrated)

    def _tick(self):
        now = time.monotonic()
        elapsed = max(0.0, now - self._last_tick)
        self._last_tick = now
        if hasattr(self.source, "read_available"):
            # 采集由板卡后台读取；界面延迟后拉取积压，不把 tick 当采样时钟。
            chunk = self.source.read_available(max(1, int(self.cfg.sample_rate * 5)))
        else:
            # 合成/其他数据源按经过的实际时间产生样本，而非假定每次 tick 准时。
            self._frac += self.cfg.sample_rate * elapsed
            n = int(self._frac)
            self._frac -= n
            if n <= 0:
                return
            chunk = self.source.read(n)
        if chunk.shape[1] == 0:
            self._record_sync()
            return 0
        # 远程上传：把原始 µV 块镜像发出去（非阻塞，内部队列 + 后台线程）
        if self.uploader is not None and self.uploader.is_connected:
            self.uploader.send(chunk)
        filtered = self.dsp.process(chunk)
        self.waveform.push(filtered)
        # 累积原始数据（供电极接触检测），长度与波形窗一致
        self._raw_hist = np.concatenate([self._raw_hist, chunk], axis=1)
        raw_max = int(self.cfg.viz.window_seconds * self.cfg.sample_rate)
        if self._raw_hist.shape[1] > raw_max:
            self._raw_hist = self._raw_hist[:, -raw_max:]

        # 录制中：缓冲原始 µV 与滤波后数据 + 记录时间同步元数据
        if self.recorder is not None and self.recorder.is_recording:
            self.recorder.append(chunk, filtered)
            self._record_sync()
            self.record_btn.setText(f"停止录制（墙钟 {self.recorder.monotonic_seconds():.0f}s / 数据 {self.recorder.elapsed_seconds:.0f}s）")
            wall = self.recorder.monotonic_seconds()
            if wall > 5 and abs(wall - self.recorder.elapsed_seconds) > 2:
                self.info_label.setText("警告：墙钟时长与样本时长相差超过 2 秒，请检查采集及队列")

        self._tick_count += 1
        if self._tick_count % self._analysis_every == 0:
            self._run_analysis()
        return chunk.shape[1]

    def _run_analysis(self):
        hist = self.waveform.hist
        n_win = int(self.cfg.analysis.window_seconds * self.cfg.sample_rate)
        if hist.shape[1] < n_win:
            return  # 等窗口攒满一个 analysis 窗再算，避免短窗导致的噪声指标
        data = hist[:, -n_win:]
        if self._raw_hist.shape[1] < n_win:
            return
        raw_win = self._raw_hist[:, -n_win:]
        quality = compute_channel_quality(raw_win, self.cfg.sample_rate,
                                          full_scale_uv=self.source.full_scale_uv)
        valid = [r["status"] != "bad" and nm.strip().upper() not in ("NC", "", "REF", "GND")
                 for r, nm in zip(quality, self.source.channel_names)]
        self.quality.update_quality(raw_win, quality)
        mask = tuple(valid)
        if self._last_valid_channels is not None and mask != self._last_valid_channels:
            self._ema_state.clear()
            # 校准时的通道集合变化会改变平均功率，旧基线不可继续使用。
            self.calibrator.reset()
        self._last_valid_channels = mask
        metrics = self.analyzer.analyze(data, valid_channels=valid)
        interference = detect_25hz_concentration(raw_win, self.cfg.sample_rate, valid)
        if self.recorder is not None and self.recorder.is_recording:
            reasons = [(["预留或参考通道"] if nm.strip().upper() in ("NC", "", "REF", "GND")
                        else r["reasons"]) for nm, r in zip(self.source.channel_names, quality)]
            self.recorder.record_channel_quality(valid, reasons, interference)

        # 基线校准（P1 被试内 baseline）：采集中累加样本；已校准则 z-score
        if self._baseline_mode and not interference["warning"]:
            self.calibrator.add_sample(metrics)
        if self.calibrator.calibrated:
            metrics = self.calibrator.zscore(metrics)
            metrics["_calibrated"] = True
        # 软件构造的效价/唤醒度仅在各自具备被试内基线时显示。
        if not self.calibrator.has_calibration("valence"):
            metrics["valence"] = None
        if not self.calibrator.has_calibration("arousal"):
            metrics["arousal"] = None
        if interference["warning"]:
            metrics["valence"] = None
            metrics["arousal"] = None

        # 显示层 EMA 平滑（不改变分析器输出的原始指标）
        disp = dict(metrics)
        disp["faa"] = self._ema("faa", metrics["faa"])
        disp["valence"] = self._ema("valence", metrics["valence"])
        disp["arousal"] = self._ema("arousal", metrics["arousal"])
        self.emotion.update_metrics(disp)

        ch_idx = max(0, min(self.ch_selector.currentIndex(), data.shape[0] - 1))
        if valid[ch_idx]:
            freqs, psd = welch_psd(data[ch_idx], self.cfg.sample_rate)
            self.spectrum.update_psd(freqs, psd)
        else:
            self.spectrum.update_psd(np.array([]), np.array([]))

        # 脑地形图：对每个频带算「每通道频带功率」，推给面板（面板按当前选中频带渲染）
        band_powers = {}
        for bname, (blo, bhi) in self.cfg.analysis.bands.items():
            power = band_power_map(data, self.cfg.sample_rate, blo, bhi)
            power[~np.asarray(valid)] = np.nan
            band_powers[bname] = power
        self.topography.update_band_powers(band_powers)

        # 时频图：取面板下拉选中的通道，喂最近 window_seconds 秒（约 10s）滤波数据
        sp_idx = self.spectrogram.selected_channel_index()
        sp_win = int(self.spectrogram.window_sec * self.cfg.sample_rate)
        sp_data = hist[sp_idx, -min(hist.shape[1], sp_win):]
        if valid[sp_idx]:
            self.spectrogram.update_spectrogram(sp_data)
        else:
            self.spectrogram.img.clear()

        if metrics.get("faa_ok"):
            if self.calibrator.calibrated:
                self.info_label.setText(f"FAA z {disp['faa']:+.2f}（已校准）")
            else:
                ao = metrics.get("alpha_occipital")
                if ao is not None:
                    self.info_label.setText(f"FAA {disp['faa']:+.3f}  "
                                            f"alpha(枕区) {ao:.1f} µV²")
                else:
                    self.info_label.setText(f"FAA {disp['faa']:+.3f}")
        else:
            self.info_label.setText(metrics.get("note", ""))
        if interference["warning"]:
            self.info_label.setText("警告：多数有效通道 25 Hz 成分异常集中，暂停情绪显示和基线采集")

    def _ema(self, key, value, alpha=0.3):
        if value is None:
            self._ema_state.pop(key, None)
            return None
        prev = self._ema_state.get(key)
        if prev is None:
            self._ema_state[key] = value
            return value
        self._ema_state[key] = alpha * value + (1 - alpha) * prev
        return self._ema_state[key]

    def closeEvent(self, event):
        self.timer.stop()
        # 若正在录制，先落盘再退出
        if self.recorder is not None and self.recorder.is_recording:
            self._stop_recording()
        # 若正在上传，断开连接
        if self.uploader is not None:
            try:
                self.uploader.close()
            finally:
                self.uploader = None
        self.source.stop()
        super().closeEvent(event)


def run_app(cfg, source, eye_tracker=None):
    """创建 QApplication、启动窗口并进入事件循环。eye_tracker 可选（真实设备或模拟器）。

    眼动仪在「开始录制」时才启动（见 MainWindow._start_recording），这里只透传。
    """
    app = QApplication.instance() or QApplication(sys.argv)
    setup_theme()
    source.start()
    win = MainWindow(cfg, source, eye_tracker=eye_tracker)
    win.resize(1280, 800)
    win.show()
    sys.exit(app.exec())
